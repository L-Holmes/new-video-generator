import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!1
# THIS IS IMPORTANT
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!1
USE_VAAPI: bool = False
# Toggle to True to use AMD's hardware H.264 encoder (much faster, but
# produces ~20-40% larger files than libx264 at similar visual quality).
# Test on one job first, compare file sizes, decide.

VAAPI_DEVICE: str = "/dev/dri/renderD128"
# Render node for VAAPI. /dev/dri/renderD128 is the standard first GPU on
# Debian. Check `ls /dev/dri/` if you have multiple GPUs.

VAAPI_QP: int = 20 # increase into 22 if file size too big...
# Quantization parameter for VAAPI. Lower = better quality + bigger files.
# 20 lands roughly at libx264 CRF 18 visual quality. Try 22 for smaller
# files if 20 is bigger than you want.

# --- CONFIGURATION ---
TARGET_WIDTH  = 1920
TARGET_HEIGHT = 1080
TARGET_FPS    = 30

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"}


# -----------------------------------------------------------------------------------------
# -----------------------------------------------------------------------------------------

# TODO in future- extract this out into its own python file (since its used by this file and main.py... 

import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor

ENCODE_WORKERS: int = 2 if USE_VAAPI else 8
# Parallel ffmpeg encodes. 3 is a sweet spot on an 8-core Ryzen — each
# libx264 encode is already multi-threaded, so going higher mostly just
# makes them slower individually. Drop to 1 if your fan spins up too much.


class ProgressTracker:
    """Thread-safe text progress indicator with ETA.

    Usage:
        tracker = ProgressTracker(total=100, label="STITCHING ")
        tracker.tick()    # call from any thread, once per unit of work
        tracker.finish()  # final newline
    """

    def __init__(self, total: int, label: str = "PROGRESS",
                 bar_width: int = 30):
        self.total = max(1, total)
        self.label = label
        self.bar_width = bar_width
        self.done = 0
        self.start = time.time()
        self._lock = threading.Lock()
        self._render()

    def tick(self, n: int = 1) -> None:
        with self._lock:
            self.done += n
            self._render()

    def finish(self) -> None:
        with self._lock:
            if self.done < self.total:
                self.done = self.total
            self._render()
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _render(self) -> None:
        elapsed = time.time() - self.start
        frac = self.done / self.total

        if self.done >= 2 and elapsed > 0.2:
            rate = self.done / elapsed
            remaining_s = max(0.0, (self.total - self.done) / rate) if rate else 0
            mins, secs = divmod(int(remaining_s), 60)
            eta = f"{mins}m {secs}s"
        else:
            eta = "calculating..."

        filled = int(self.bar_width * frac)
        bar = ">" * filled + "-" * (self.bar_width - filled)

        msg = (f"{self.label} [{self.done:>4}/{self.total}]  "
               f"TIME REMAINING {bar} {eta}      ")
        sys.stdout.write("\r" + msg)
        sys.stdout.flush()


# -----------------------------------------------------------------------------------------
# -----------------------------------------------------------------------------------------


def _is_image(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS

def _load_json(path: str):
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))

def _run(cmd, *, quiet: bool = False, **kw):
    if not quiet:
        print("  $", " ".join(str(c) for c in cmd))
    if quiet:
        # Drop ffmpeg's own stderr chatter — progress bar handles status
        kw.setdefault("stdout", subprocess.DEVNULL)
        kw.setdefault("stderr", subprocess.DEVNULL)
    return subprocess.run(cmd, check=True, **kw)

def _audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())

_VF_BASE = (
    f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
    f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1"
)


def _generate_clip(src: str, frames: int, out: str, quiet: bool = False) -> None:
    """Encodes a single clip/image to a .ts file with exact frame count."""
    is_img = _is_image(src)
    cmd = ["ffmpeg", "-y"]

    # VAAPI needs the device declared BEFORE inputs
    if USE_VAAPI:
        cmd += ["-vaapi_device", VAAPI_DEVICE]

    if is_img:
        cmd += ["-loop", "1", "-framerate", str(TARGET_FPS), "-i", src]
    else:
        cmd += ["-i", src]

    if USE_VAAPI:
        # Software scale+pad, then upload NV12 frames to the GPU for encoding.
        # Doing the scale on the GPU (scale_vaapi) is fiddlier with our pad
        # step, so we keep scale+pad in software — they're cheap and the
        # encode is the slow part.
        vf = (
            f"{_VF_BASE},"
            f"fps={TARGET_FPS},"
            f"tpad=stop_mode=clone:stop_duration=5,"
            f"format=nv12,hwupload"
        )
        cmd += [
            "-vf", vf,
            "-frames:v", str(frames),
            "-c:v", "h264_vaapi",
            "-qp", str(VAAPI_QP),
            "-video_track_timescale", "90000",
            "-an", out,
        ]
    else:
        cmd += [
            "-vf", f"{_VF_BASE},fps={TARGET_FPS},tpad=stop_mode=clone:stop_duration=5",
            "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-video_track_timescale", "90000",
            "-an", out,
        ]

    _run(cmd, quiet=quiet)

def stitch_together_video(
    final_script_and_clips: str = "CACHE-spices/final_script_to_clips.json",
    absolute_timestamps:    str = "spices_timestamps_absolute.json",
    history_file:           str = "history.json", # Update path if needed
    script_audio_file:      str = "script.wav",
    output_file:            str = "spices-OUTPUT/output.mp4",
) -> None:
    # 1. Load Data
    clips_data = _load_json(final_script_and_clips)
    abs_ts = _load_json(absolute_timestamps)
    history = _load_json(history_file)
    audio_len = _audio_duration(script_audio_file)

    if not all([clips_data, abs_ts, history]):
        raise FileNotFoundError("Could not load required JSON files.")

    # 2. Plan the Frames
    # We use the absolute timestamps as the "Truth" for sentence boundaries
    segments_to_render = []
    
    # Sort absolute timestamps by time
    sorted_anchors = sorted(abs_ts.items(), key=lambda x: float(x[1]))
    
    total_expected_frames = round(audio_len * TARGET_FPS)
    current_frame_pos = 0

    print(f"Planning {len(clips_data)} script segments...")

    for i, (sentence_text, start_time) in enumerate(sorted_anchors):
        # Determine the "End Anchor" for this sentence
        if i < len(sorted_anchors) - 1:
            next_start_time = float(sorted_anchors[i+1][1])
        else:
            next_start_time = audio_len
        
        # Calculate exactly how many frames this sentence block must occupy
        target_end_frame = round(next_start_time * TARGET_FPS)
        sentence_frame_budget = target_end_frame - current_frame_pos

        # Find the matching footage items in the clips_data
        # We match based on the sentence text
        match = next((item for item in clips_data if item["script_text"] == sentence_text), None)
        
        if not match or not match.get("footage"):
            print(f"⚠️ No footage found for: {sentence_text[:30]}...")
            # Fill with black or skip (adjusting pos to maintain sync)
            current_frame_pos = target_end_frame
            continue

        footage_items = match["footage"]
        
        # If there are multiple clips for one sentence, distribute the budget
        # based on their relative durations in the JSON
        local_durations = [list(f.values())[0] for f in footage_items]
        sum_local_durs = sum(local_durations)
        
        sentence_running_frames = 0
        for j, f_item in enumerate(footage_items):
            url = list(f_item.keys())[0]
            rel_dur = list(f_item.values())[0]
            local_path = history.get(url)

            if not local_path:
                print(f"❌ Missing file in history: {url}")
                continue

            # Calculate frame count for this specific clip
            if j < len(footage_items) - 1:
                # Share of the budget based on relative duration
                clip_frames = round((rel_dur / sum_local_durs) * sentence_frame_budget)
            else:
                # Last clip gets whatever is left in the sentence budget
                clip_frames = sentence_frame_budget - sentence_running_frames
            
            if clip_frames > 0:
                segments_to_render.append((local_path, clip_frames))
                sentence_running_frames += clip_frames
        
        current_frame_pos += sentence_running_frames

    # 3. Execution
    tmp = tempfile.mkdtemp(prefix="stitch_")
    clip_files = []
    try:
        # Encode clips

        # Encode clips in parallel with a single progress bar
        print(f"\nEncoding {len(segments_to_render)} clips "
              f"({ENCODE_WORKERS} parallel workers)...")

        # Pre-allocate so results stay in order regardless of completion order
        clip_files = [None] * len(segments_to_render)
        tracker = ProgressTracker(
            total=len(segments_to_render), label="STITCHING ",
        )

        def encode_one(task):
            idx, (src, frames) = task
            out_path = os.path.join(tmp, f"segment_{idx:04d}.ts")
            _generate_clip(src, frames, out_path, quiet=True)
            tracker.tick()
            return idx, out_path

        with ThreadPoolExecutor(max_workers=ENCODE_WORKERS) as ex:
            for idx, out_path in ex.map(encode_one, enumerate(segments_to_render)):
                clip_files[idx] = out_path

        tracker.finish()





        # Concatenate .ts files
        print("\nConcatenating intermediate files...")
        concat_list = os.path.join(tmp, "clips.txt")
        with open(concat_list, "w") as f:
            for cf in clip_files:
                f.write(f"file '{Path(cf).resolve().as_posix()}'\n")
        
        silent_video = os.path.join(tmp, "silent_final.ts")
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list, "-c", "copy", silent_video])

        # Final Mux with Audio
        print("\nAdding audio and finalizing MP4...")
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        _run([
            "ffmpeg", "-y",
            "-i", silent_video,
            "-i", script_audio_file,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            output_file
        ])

        # 4. Final Verify
        actual_dur = _audio_duration(output_file)
        print(f"\n✅ DONE.")
        print(f"Expected Duration (Audio): {audio_len:.3f}s")
        print(f"Actual Video Duration:     {actual_dur:.3f}s")
        print(f"Final Drift: {actual_dur - audio_len:+.3f}s")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    stitch_together_video()
