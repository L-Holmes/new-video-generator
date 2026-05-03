"""
stitch_clips.py
---------------
Reads final_script_to_clips.json and history.json, trims each clip/image
to the required duration, strips audio, then assembles a single output video
with the provided audio track laid over the top.

Dependencies: ffmpeg must be on PATH  (pip install nothing needed)
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Target resolution / frame-rate for YouTube (change if you prefer 1080p)
TARGET_WIDTH  = 1920
TARGET_HEIGHT = 1080
TARGET_FPS    = 30


def load_from_cache(file_path: str) -> list | dict | None:
    """Loads data from the JSON file. Returns None if file doesn't exist or is invalid."""
    p = Path(file_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess command, printing it first for transparency."""
    print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kwargs)


def image_to_clip(src: str, duration: float, out: str) -> None:
    """Convert a still image to a silent video clip of `duration` seconds."""
    run([
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", src,
        "-vf", f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={TARGET_FPS}",
        "-t", str(duration),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-an",
        out,
    ])


def video_to_clip(src: str, duration: float, out: str) -> None:
    """Trim a video to `duration` seconds, strip audio, normalise resolution."""
    run([
        "ffmpeg", "-y",
        "-i", src,
        "-t", str(duration),
        "-vf", f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={TARGET_FPS}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-an",
        out,
    ])


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"}


def is_image(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def concat_clips(clip_paths: list[str], out: str) -> None:
    """Concatenate a list of silent video files into one output file."""
    # Write a concat list file
    list_file = out + ".list.txt"
    with open(list_file, "w") as f:
        for p in clip_paths:
            # ffmpeg concat demuxer needs absolute or properly escaped paths
            escaped = Path(p).resolve().as_posix().replace("'", r"'\''")
            f.write(f"file '{escaped}'\n")

    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        out,
    ])
    os.remove(list_file)


def add_audio(video: str, audio: str, out: str) -> None:
    """Lay audio track over silent video. Audio is trimmed/padded to video length."""
    run([
        "ffmpeg", "-y",
        "-i", video,
        "-i", audio,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        # If audio is shorter than video the video just ends — YouTube is fine with that.
        # If audio is longer, trim it to video length:
        "-shortest",
        out,
    ])


def stitch_together_video(
    final_script_and_clips: str = "CACHE/final_script_to_clips.json",
    history_file: str           = "CACHE/stock_footage/history.json",
    script_audio_file: str      = "script.wav",
    output_file: str            = "OUTPUT/output_with_audio.mp4",
) -> None:
    # ── Load JSON data ────────────────────────────────────────────────────────
    clips_data = load_from_cache(final_script_and_clips)
    if clips_data is None:
        raise FileNotFoundError(f"Could not load clips JSON from: {final_script_and_clips}")

    history = load_from_cache(history_file)
    if history is None:
        raise FileNotFoundError(f"Could not load history JSON from: {history_file}")

    # ── Build ordered list of (local_path, duration) ─────────────────────────
    segments: list[tuple[str, float]] = []

    for entry in clips_data:
        for footage_item in entry.get("footage", []):
            for url, duration in footage_item.items():
                local_path = history.get(url)
                if not local_path:
                    raise KeyError(f"URL not found in history.json: {url}")
                if not Path(local_path).exists():
                    raise FileNotFoundError(f"Media file missing: {local_path}")
                segments.append((local_path, float(duration)))

    print(f"Found {len(segments)} segment(s) to process.\n")

    # ── Process each segment into a normalised silent clip ───────────────────
    tmp_dir = tempfile.mkdtemp(prefix="yt_stitch_")
    clip_files: list[str] = []

    try:
        for i, (src, dur) in enumerate(segments):
            out_clip = os.path.join(tmp_dir, f"clip_{i:04d}.mp4")
            print(f"[{i+1}/{len(segments)}] {'IMAGE' if is_image(src) else 'VIDEO'} "
                  f"→ {dur:.2f}s  |  {src}")
            if is_image(src):
                image_to_clip(src, dur, out_clip)
            else:
                video_to_clip(src, dur, out_clip)
            clip_files.append(out_clip)

        # ── Concatenate all clips ─────────────────────────────────────────────
        print(f"\nConcatenating {len(clip_files)} clip(s)…")
        silent_tmp = os.path.join(tmp_dir, "silent_concat.mp4")
        concat_clips(clip_files, silent_tmp)

        # ── Add audio ────────────────────────────────────────────────────────
        if not Path(script_audio_file).exists():
            raise FileNotFoundError(f"Audio file not found: {script_audio_file}")

        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        print(f"\nAdding audio from {script_audio_file}…")
        add_audio(silent_tmp, script_audio_file, output_file)
        print(f"\n✓ Output saved → {output_file}")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\nDone!")


if __name__ == "__main__":
    stitch_together_video()
