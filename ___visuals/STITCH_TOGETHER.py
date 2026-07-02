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

_DUR_CACHE: dict[str, float] = {}

def _media_duration_cached(path: str) -> float:
    """ffprobe duration with a process-lifetime cache.

    Lets a music fade land on the file's real end even when the user gave no
    trim, without re-probing the same file for every scene that references it.
    """
    if path in _DUR_CACHE:
        return _DUR_CACHE[path]
    try:
        d = _audio_duration(path)
    except Exception:
        d = 0.0
    _DUR_CACHE[path] = d
    return d


def _resolve_audio_events(
    audio_events_map: dict,
    clips_data: list[dict],
    abs_ts: dict,
    total_duration: float,
    sfx_volume: float,
    music_volume: float,
) -> list[dict]:
    """
    Convert the per-scene events into a flat list with absolute start times.

    Volumes are injected from constants here (not from the events map),
    based on the event's "type" field.

    Returns events like:
        {"path": str, "start_seconds": float, "duration": float|None,
         "volume": float, "fade_out": float, "_debug": str}
    """
    print("\n" + "=" * 70)
    print("[audio resolve] RESOLVING audio events to absolute timings")
    print(f"[audio resolve] scenes with events: {len(audio_events_map)}")
    print(f"[audio resolve] total video duration: {total_duration:.3f}s")
    print(f"[audio resolve] sfx_volume={sfx_volume}, music_volume={music_volume}")
    print("=" * 70)

    sorted_anchors = sorted(abs_ts.items(), key=lambda x: float(x[1]))
    print(f"[audio resolve] {len(sorted_anchors)} timestamp anchors loaded")

    text_to_start = {txt: float(t) for txt, t in sorted_anchors}

    # For computing where loop_start lands: scene_start + first_clip_duration
    text_to_first_clip_dur = {}
    text_to_num_clips = {}
    for entry in clips_data:
        footage = entry.get("footage", [])
        text_to_num_clips[entry["script_text"]] = len(footage)
        if footage:
            first_trim = list(footage[0].values())[0]
            text_to_first_clip_dur[entry["script_text"]] = float(first_trim)

    resolved: list[dict] = []

    for script_text, events in audio_events_map.items():
        print(f"\n[audio resolve] scene: '{script_text[:60]}...'")

        if script_text not in text_to_start:
            print(f"[audio resolve]   ⚠️  no timestamp for this scene — SKIPPING all its events")
            continue

        scene_start = text_to_start[script_text]
        num_clips   = text_to_num_clips.get(script_text, 1)
        first_dur   = text_to_first_clip_dur.get(script_text, 0.0)
        print(f"[audio resolve]   scene_start={scene_start:.3f}s, "
              f"num_clips={num_clips}, first_clip_dur={first_dur:.3f}s")

        for ev in events:
            timing = ev.get("timing", "scene_start")
            path   = ev["path"]
            ev_type = ev.get("type", "sfx")

            if timing == "scene_start":
                start = scene_start
                print(f"[audio resolve]   '{timing}' resolved: {start:.3f}s")
            elif timing == "loop_start":
                if num_clips > 1:
                    start = scene_start + first_dur
                    print(f"[audio resolve]   '{timing}' resolved past intro: "
                          f"{scene_start:.3f} + {first_dur:.3f} = {start:.3f}s")
                else:
                    start = scene_start
                    print(f"[audio resolve]   '{timing}' fallback (no intro): {start:.3f}s")
            else:
                print(f"[audio resolve]   ⚠️  unknown timing '{timing}' — using scene_start")
                start = scene_start

            if not Path(path).exists():
                print(f"[audio resolve]   ❌ MISSING audio file: {path} — SKIPPING")
                continue

            # Volume comes from the hardcoded constants based on event type
            volume = music_volume if ev_type == "music" else sfx_volume

            # ── Resolve trim + fade timing ─────────────────────────────────
            # The out-fade must land on the END of the AUDIBLE source. We bound
            # the source to the time actually left in the video (and to the
            # user's trim, if any), then probe the file so the fade starts in
            # the right place even when no trim was given. This is what makes
            # `music_fade_out` work with `music_trim_seconds: 0` — previously
            # duration=None skipped the fade entirely.
            raw_trim  = ev.get("duration")                 # user trim, or None
            fade_secs = float(ev.get("fade_out", 0.0) or 0.0)
            available = max(0.0, total_duration - start)   # video time left after start

            trim_len   = raw_trim if (raw_trim and raw_trim > 0) else None
            fade_start = None
            if fade_secs > 0 and available > 0.05:
                src_dur  = _media_duration_cached(path)
                real_len = min(src_dur, available) if src_dur > 0 else available
                if raw_trim and raw_trim > 0:
                    real_len = min(real_len, raw_trim)
                trim_len = real_len                        # bound so the fade ends at the true end
                if real_len > fade_secs + 0.05:
                    fade_start = real_len - fade_secs
                else:
                    fade_start = 0.0                       # clip shorter than fade → fade all of it
                    fade_secs  = real_len

            resolved_ev = {
                "path":          path,
                "start_seconds": start,
                "duration":      raw_trim,                 # kept for back-compat / debug
                "trim_len":      trim_len,                 # bounded playable length (atrim)
                "fade_start":    fade_start,               # out-fade start in source time
                "volume":        volume,
                "fade_out":      fade_secs,
                "_debug":        ev.get("_debug", ""),
            }
            resolved.append(resolved_ev)
            print(f"[audio resolve]   ✓ [{ev_type:5}] {Path(path).name}  "
                  f"start={start:.3f}s  trim_len={resolved_ev['trim_len']}  "
                  f"fade_start={resolved_ev['fade_start']}  "
                  f"vol={volume}  fade={resolved_ev['fade_out']}s")

    print(f"\n[audio resolve] DONE — {len(resolved)} event(s) resolved")
    print("=" * 70)
    return resolved


def _build_audio_filter_chain(ev: dict, input_idx: int, output_label: str) -> str:
    """Build one filter_complex chain for a single audio event.

    Everything here is applied ONLY to this event's own input stream and is
    emitted to a private [output_label]. Narration ([1:a]) is never part of
    this chain, so trimming/fading an event can only ever touch that event —
    never the narration.

    `trim_len` and `fade_start` are precomputed in _resolve_audio_events (which
    knows the video length and can probe the source), so the out-fade lands on
    the source's true end even when the user gave no explicit trim.
    """
    parts = []

    # 1. Bound the source length (atrim). `trim_len` is the resolved playable
    #    length; fall back to the raw `duration` for any caller that didn't set it.
    trim_len = ev.get("trim_len", ev.get("duration"))
    if trim_len is not None and trim_len > 0:
        parts.append(f"atrim=0:{trim_len:.3f}")
        parts.append("asetpts=PTS-STARTPTS")

    # 2. Fade out the last `fade_out` secs of the (bounded) source. `fade_start`
    #    is in source-stream time (post-trim) and is computed where the
    #    durations are known. No fade_start ⇒ no fade (e.g. SFX, or fade off).
    fade_start = ev.get("fade_start")
    fade       = float(ev.get("fade_out", 0.0) or 0.0)
    if fade_start is not None and fade > 0:
        parts.append(f"afade=t=out:st={fade_start:.3f}:d={fade:.3f}:curve=log")

    # 3. Volume
    vol = ev.get("volume", 1.0)
    if abs(vol - 1.0) > 0.001:
        parts.append(f"volume={vol:.3f}")

    # 4. Delay (push into the timeline — must be last)
    delay_ms = max(0, int(round(ev["start_seconds"] * 1000)))
    if delay_ms > 0:
        parts.append(f"adelay={delay_ms}|{delay_ms}")

    filter_str = ",".join(parts) if parts else "anull"
    return f"[{input_idx}:a]{filter_str}[{output_label}]"


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
    history_file:           str = "history.json",
    script_audio_file:      str = "script.wav",
    output_file:            str = "spices-OUTPUT/output.mp4",
    audio_events_file:      str | None = None,   # ← NEW
    sfx_volume:             float = 1.0,         # ← NEW
    music_volume:           float = 0.3,         # ← NEW
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

        # Final Mux with Audio — possibly with SFX/music
        print("\nAdding audio and finalizing MP4...")
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)

        resolved_events = []
        if audio_events_file and Path(audio_events_file).exists():
            print(f"[mux] loading audio events from {audio_events_file}")
            audio_events_map = _load_json(audio_events_file) or {}
            resolved_events = _resolve_audio_events(
                audio_events_map=audio_events_map,
                clips_data=clips_data,
                abs_ts=abs_ts,
                total_duration=audio_len,
                sfx_volume=sfx_volume,
                music_volume=music_volume,
            )
        else:
            print(f"[mux] no audio events file — narration-only mux")

        if not resolved_events:
            print("[mux] using SIMPLE mux path (no SFX/music)")
            _run([
                "ffmpeg", "-y",
                "-i", silent_video,
                "-i", script_audio_file,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                output_file,
            ])
        else:
            print(f"[mux] using MIX path with {len(resolved_events)} audio event(s)")

            # Dedupe paths so the same file isn't loaded twice
            path_to_input_idx: dict[str, int] = {}
            unique_paths: list[str] = []
            for ev in resolved_events:
                if ev["path"] not in path_to_input_idx:
                    path_to_input_idx[ev["path"]] = 2 + len(unique_paths)
                    unique_paths.append(ev["path"])

            print(f"[mux] unique audio files to load: {len(unique_paths)}")
            for p, idx in path_to_input_idx.items():
                print(f"[mux]   input[{idx}] = {Path(p).name}")

            # Build the filter graph
            chains = []
            labels = []
            for i, ev in enumerate(resolved_events):
                idx = path_to_input_idx[ev["path"]]
                label = f"ev{i}"
                chain = _build_audio_filter_chain(ev, idx, label)
                chains.append(chain)
                labels.append(label)
                print(f"[mux]   chain[{i}]: {chain}")

            mix_inputs = "[1:a]" + "".join(f"[{l}]" for l in labels)
            n = 1 + len(labels)
            # mix_chain = (
                # f"{mix_inputs}amix=inputs={n}:duration=first:"
                # f"dropout_transition=0:normalize=0[aout]"
            # )
            mix_chain = (
                f"{mix_inputs}"
                f"amix=inputs={n}:duration=first:dropout_transition=0:normalize=0,"
                f"alimiter=limit=0.95[aout]"
            )


            chains.append(mix_chain)
            print(f"[mux]   mix:      {mix_chain}")

            filter_complex = ";".join(chains)
            print(f"\n[mux] full filter_complex ({len(filter_complex)} chars):")
            print(f"[mux]   {filter_complex}\n")

            cmd = [
                "ffmpeg", "-y",
                "-i", silent_video,
                "-i", script_audio_file,
            ]
            for p in unique_paths:
                cmd += ["-i", p]
            cmd += [
                "-filter_complex", filter_complex,
                "-map", "0:v:0",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                output_file,
            ]
            _run(cmd)

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
