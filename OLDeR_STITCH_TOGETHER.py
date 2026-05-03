"""
STITCH_TOGETHER.py
==================
Frame-precise concatenation of clips into the final video, with the script
audio laid on top.

About the drift you were seeing
-------------------------------
The previous version used `-t <duration_seconds>` for each clip.  ffmpeg
rounds duration-based trims to the nearest output frame, so each clip is
±1 frame off its target.  At 30fps that's up to ±33ms per clip — and
across 50 clips that drifts visibly out of sync with the audio.

This version does it differently:

  cumulative_frames[i] = round(cumulative_seconds[i] * fps)
  clip_frames[i]       = cumulative_frames[i] - cumulative_frames[i-1]

Every clip ends *exactly* on its target cumulative frame.  Because each
clip's start is anchored to the previous clip's frame-aligned end, drift
is mathematically impossible — the worst-case error for the entire video
is ±1 frame, not ±N frames.

Each clip is then rendered with `-frames:v <count>` (which writes exactly
that many output frames, full stop) instead of `-t <seconds>` (which is a
"close enough" duration).

A `tpad=stop_mode=clone` filter is also added as a safety net — if a
source video happens to be shorter than the requested frame count, its
last frame is held instead of the clip ending early.  In normal operation
the pad is unused.

Audio is muxed at the end with `-shortest`.  Since the durations come
from the synchronizer (which derived them from the audio itself), the
silent video and the audio are the same length and stay in lockstep.

Dependencies: ffmpeg + ffprobe on PATH.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

TARGET_WIDTH  = 1920
TARGET_HEIGHT = 1080
TARGET_FPS    = 30

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"}


def _is_image(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def _load_json(path: str):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kw)


def _audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


# Common video filter — same scaling/padding/SAR for every clip
_VF_BASE = (
    f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
    f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1"
)


def _image_to_clip(src: str, frames: int, out: str) -> None:
    _run([
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(TARGET_FPS),
        "-i", src,
        "-vf", f"{_VF_BASE},fps={TARGET_FPS}",
        "-frames:v", str(frames),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-an",
        out,
    ])


def _video_to_clip(src: str, frames: int, out: str) -> None:
    # tpad clone-pad as a safety net for shorter-than-expected sources.
    _run([
        "ffmpeg", "-y",
        "-i", src,
        "-vf", f"{_VF_BASE},fps={TARGET_FPS},tpad=stop_mode=clone:stop_duration=10",
        "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-an",
        out,
    ])


def _concat(clip_paths: list[str], out: str) -> None:
    list_file = out + ".list.txt"
    with open(list_file, "w") as f:
        for p in clip_paths:
            esc = Path(p).resolve().as_posix().replace("'", r"'\''")
            f.write(f"file '{esc}'\n")
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_file, "-c", "copy", out,
    ])
    os.remove(list_file)


def _add_audio(video: str, audio: str, out: str) -> None:
    _run([
        "ffmpeg", "-y",
        "-i", video, "-i", audio,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        out,
    ])


def stitch_together_video(
    final_script_and_clips: str = "CACHE/final_script_to_clips.json",
    history_file:           str = "CACHE/stock_footage/history.json",
    script_audio_file:      str = "script.wav",
    output_file:            str = "OUTPUT/output_with_audio.mp4",
) -> None:
    clips_data = _load_json(final_script_and_clips)
    if clips_data is None:
        raise FileNotFoundError(f"Could not load: {final_script_and_clips}")
    history = _load_json(history_file)
    if history is None:
        raise FileNotFoundError(f"Could not load: {history_file}")
    if not Path(script_audio_file).exists():
        raise FileNotFoundError(f"Audio not found: {script_audio_file}")

    # ── Build flat segment list with frame-precise frame counts ─────────────
    segments: list[tuple[str, int, float]] = []   # (path, frames, target_seconds_for_log)
    cum_t = 0.0
    cum_f = 0

    for entry in clips_data:
        for footage_item in entry.get("footage", []):
            for url, duration in footage_item.items():
                local = history.get(url)
                if not local:
                    raise KeyError(f"URL not in history.json: {url}")
                if not Path(local).exists():
                    raise FileNotFoundError(f"Media missing: {local}")

                cum_t   += float(duration)
                target_f = round(cum_t * TARGET_FPS)
                clip_f   = target_f - cum_f
                if clip_f <= 0:
                    raise ValueError(
                        f"Segment {len(segments)} got {clip_f} frames "
                        f"(duration={duration!r}). A duration of 0s isn't valid."
                    )
                segments.append((local, clip_f, float(duration)))
                cum_f = target_f

    audio_dur = _audio_duration(script_audio_file)
    target_video_secs = cum_f / TARGET_FPS
    diff_frames = round((audio_dur - target_video_secs) * TARGET_FPS)
    print(f"\nFrame-precise plan:")
    print(f"  {len(segments)} clips → {cum_f} frames = {target_video_secs:.3f}s")
    print(f"  sum of input durations:  {cum_t:.3f}s")
    print(f"  audio length:            {audio_dur:.3f}s "
          f"(diff: {audio_dur - target_video_secs:+.3f}s = {diff_frames:+d} frames)")
    if abs(diff_frames) > 5:
        print(f"  ⚠️  durations and audio length differ by {diff_frames} frames — "
              f"check that the synchronizer ran on the same WAV.")

    # ── Encode each clip with exact frame count ─────────────────────────────
    tmp = tempfile.mkdtemp(prefix="stitch_")
    clip_files: list[str] = []
    try:
        for i, (src, frames, dur) in enumerate(segments):
            out_clip = os.path.join(tmp, f"clip_{i:04d}.mp4")
            kind = "IMAGE" if _is_image(src) else "VIDEO"
            print(f"\n[{i+1}/{len(segments)}] {kind} target {dur:.2f}s "
                  f"→ {frames}f ({frames/TARGET_FPS:.3f}s)  |  {Path(src).name}")
            if _is_image(src):
                _image_to_clip(src, frames, out_clip)
            else:
                _video_to_clip(src, frames, out_clip)
            clip_files.append(out_clip)

        # ── Concat ──────────────────────────────────────────────────────────
        print(f"\nConcatenating {len(clip_files)} clip(s)…")
        silent = os.path.join(tmp, "silent_concat.mp4")
        _concat(clip_files, silent)

        # ── Mux audio ───────────────────────────────────────────────────────
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        print(f"\nAdding audio from {script_audio_file}…")
        _add_audio(silent, script_audio_file, output_file)

        # ── Verify ──────────────────────────────────────────────────────────
        out_dur = _audio_duration(output_file)
        print(f"\n✓ Output: {output_file}")
        print(f"  output duration  = {out_dur:.3f}s")
        print(f"  audio duration   = {audio_dur:.3f}s")
        print(f"  difference       = {out_dur - audio_dur:+.3f}s  "
              f"(within {round(abs(out_dur-audio_dur)*TARGET_FPS)} frame(s))")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nDone.")


if __name__ == "__main__":
    stitch_together_video()
