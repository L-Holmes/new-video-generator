import os
import sys
import re
import argparse
import subprocess
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
import random
import requests
import nltk
from rake_nltk import Rake
import time
import json

# Silently ensure NLTK has the required data for RAKE
nltk.download('punkt', quiet=True)
nltk.download('punkt_tab', quiet=True)

# ==========================================
# CONFIGURATION & RESEARCH-BACKED VARIABLES
# ==========================================

WORDS_PER_MINUTE = 155               # Standard narration pace

OPTIMAL_CLIP_LENGTH_SEC = 4.8        # Ideal average pacing
MAX_CLIP_LENGTH_SEC = 8.0            # Avoid dragging
MIN_CLIP_LENGTH_SEC = 1.8            # Avoid flashing too fast

TARGET_RESOLUTION = (1920, 1080)     # HD output, youtube
KEN_BURNS_ZOOM = 0.06                # Subtle cinematic zoom padding
FPS = 24

# Energy Thresholds & Modifiers
ENERGY_THRESHOLD_VERY_HIGH = 0.04    # Triggers shorter clip lengths (fast cuts)
ENERGY_THRESHOLD_LOW = 0.01          # Triggers longer clip lengths (slow scenes)
CLIP_SPEED_MODIFIER_FAST = 0.85      # 15% faster cuts for high energy
CLIP_SPEED_MODIFIER_SLOW = 1.15      # 15% slower cuts for low energy

DEBUG = False
HISTORY_FILE = Path("download_history.json")

# ==========================================
# PRIVATE FUNCTIONS
# ==========================================

def _load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return {}
    return {}

def _save_history(history: dict):
    HISTORY_FILE.write_text(json.dumps(history, indent=2))

def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^a-zA-Z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _extract_keywords(text: str, needed_count: int) -> list:
    rake = Rake(min_length=1, max_length=4)
    rake.extract_keywords_from_text(text)
    ranked = rake.get_ranked_phrases()
    if len(ranked) < needed_count:
        ranked.extend([w for w in _clean_text(text).split() if len(w) > 3])
    return list(dict.fromkeys(ranked))[:needed_count]

def _pad_image(src: Path, dest: Path) -> None:
    padded_w = int(TARGET_RESOLUTION[0] * (1 + KEN_BURNS_ZOOM))
    padded_h = int(TARGET_RESOLUTION[1] * (1 + KEN_BURNS_ZOOM))
    with Image.open(src) as img:
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        ratio = img.width / img.height
        target_ratio = padded_w / padded_h
        if ratio > target_ratio:
            new_h = padded_h
            new_w = int(new_h * ratio)
        else:
            new_w = padded_w
            new_h = int(new_w / ratio)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = (new_w - padded_w) // 2
        top  = (new_h - padded_h) // 2
        img.crop((left, top, left + padded_w, top + padded_h)).save(dest, "JPEG", quality=92)

def _clamp_duration(secs: float) -> float:
    return max(MIN_CLIP_LENGTH_SEC, min(MAX_CLIP_LENGTH_SEC, secs))

# ==========================================
# MAIN PIPELINE STEPS
# ==========================================

def verify_environment():
    if not Path("script.txt").exists():
        print("[error] script.txt not found.")
        sys.exit(1)
    if not shutil.which("ffmpeg"):
        print("[error] ffmpeg not installed.")
        sys.exit(1)

    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    placeholders = ["NOT_SET_YET", "YOUR_KEY_HERE", "PLACEHOLDER"]
    if not api_key or any(p in api_key.upper() for p in placeholders):
        print("[error] PEXELS_API_KEY is missing or invalid.")
        sys.exit(1)

    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": "test", "per_page": 1},
            timeout=5
        )
        if resp.status_code == 401:
            print("[error] PEXELS_API_KEY Unauthorized.")
            sys.exit(1)
    except Exception as e:
        print(f"[error] API Connection failed: {e}")
        sys.exit(1)


def read_script() -> str:
    return Path("script.txt").read_text(encoding="utf-8").strip()


def process_script(script_text: str) -> dict:
    """
    Splits the script into equal-word segments and assigns each segment
    its own duration based on word count and speaking speed.

    Returns a dict with:
        total_duration_sec  – estimated total narration time
        target_clip_sec     – average clip length used for planning
        segments            – list of dicts, one per image/clip:
                                {
                                  "text":        str,
                                  "duration_sec": float,  # clamped to [MIN, MAX]
                                  "keywords":    list[str]
                                }
        keywords            – flat keyword list (for backward compat / API fetching)
    """
    # === NEW ===
    # 



    # === OLD ===
    words = script_text.split()
    wpm = WORDS_PER_MINUTE
    total_duration = (len(words) / wpm) * 60

    # Determine target clip length from energy
    target_clip = OPTIMAL_CLIP_LENGTH_SEC
    target_clip = _clamp_duration(target_clip)

    # How many clips can we fit without squeezing any below MIN_CLIP_LENGTH_SEC?
    # Derive this from total duration — no artificial floor of 5.
    # Each clip should be at most target_clip seconds; we always need at least 1.
    n_clips = max(1, round(total_duration / target_clip))

    # If even ONE clip would be shorter than MIN, reduce n_clips until it fits.
    while n_clips > 1 and (total_duration / n_clips) < MIN_CLIP_LENGTH_SEC:
        n_clips -= 1

    if DEBUG:
        print(f"[debug] Words: {len(words)} | WPM: {wpm} | Total: {total_duration:.1f}s "
              f"| Target clip: {target_clip:.2f}s | n_clips: {n_clips}")

    # Slice the word list into n_clips segments
    segments = []
    words_per_seg = len(words) / n_clips
    for i in range(n_clips):
        start = int(i * words_per_seg)
        end   = int((i + 1) * words_per_seg) if i < n_clips - 1 else len(words)
        seg_words = words[start:end]
        seg_text  = " ".join(seg_words)

        # Duration = how long a narrator takes to read this segment.
        # Do NOT clamp here — trust the maths.  Clamping only happens if the
        # per-segment raw value is genuinely below/above the hardware bounds.
        raw_duration = (len(seg_words) / wpm) * 60
        seg_duration = _clamp_duration(raw_duration)

        # Pull 1-2 search keywords from this segment's text; fall back to full script
        seg_keywords = _extract_keywords(seg_text, 3)
        if not seg_keywords:
            seg_keywords = _extract_keywords(script_text, 3)

        segments.append({
            "text":         seg_text,
            "duration_sec": seg_duration,
            "keywords":     seg_keywords,
        })

    # Flat keyword list for the Pexels fetcher (one per segment)
    all_keywords = [seg["keywords"][0] if seg["keywords"] else "" for seg in segments]

    return {
        "total_duration_sec": total_duration,
        "duration_sec":       total_duration,   # backward-compat alias
        "target_clip_sec":    target_clip,
        "segments":           segments,
        "keywords":           all_keywords,
    }


def get_clips(processed_data: dict) -> list:
    """
    Downloads exactly one image per segment, using that segment's keywords.
    Returns a list of Paths in segment order.
    """
    output_dir = Path("output/images")
    output_dir.mkdir(parents=True, exist_ok=True)
    api_key = os.environ["PEXELS_API_KEY"]

    history = _load_history()
    now = time.time()
    history = {url: ts for url, ts in history.items() if now - ts < 86400}

    segments  = processed_data["segments"]
    download_queue = []   # list of (url, dest_path, segment_index)

    for seg_idx, seg in enumerate(segments):
        found = False
        for kw in seg["keywords"]:
            if found:
                break
            try:
                resp = requests.get(
                    "https://api.pexels.com/v1/search",
                    headers={"Authorization": api_key},
                    params={"query": kw, "per_page": 8, "orientation": "landscape"},
                    timeout=10
                )
            except Exception:
                continue
            if resp.status_code != 200:
                continue

            for photo in resp.json().get("photos", []):
                url = photo["src"]["large2x"]
                if url in history:
                    continue
                dest = output_dir / f"img_{seg_idx:04d}.jpg"
                download_queue.append((url, dest, seg_idx))
                history[url] = now
                found = True
                break

    _save_history(history)

    def _dl(url, dest, seg_idx):
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                dest.write_bytes(r.content)
                return (seg_idx, dest)
        except Exception:
            pass
        return None

    downloaded = {}   # seg_idx → Path
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_dl, u, d, i) for u, d, i in download_queue]
        for f in as_completed(futures):
            res = f.result()
            if res:
                seg_idx, path = res
                downloaded[seg_idx] = path

    # Return in segment order; skip any segments that had no image
    return [downloaded[i] for i in sorted(downloaded)]


def stitch_video(images: list, processed_data: dict):
    """
    Assembles the final video.  Each image is shown for exactly the duration
    of its corresponding script segment, with a Ken Burns zoom effect.
    """
    segments = processed_data["segments"]
    # Align: if fewer images than segments, use as many as we have
    pairs = list(zip(images, segments))

    video_path = Path("output/final_video.mp4")
    video_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir   = Path("temp_proc")
    temp_dir.mkdir(exist_ok=True)

    # ── Debug stats table ────────────────────────────────────────────────────
    if DEBUG:
        print("\n[debug] Per-clip breakdown:")
        print(f"  {'#':<4} {'File':<20} {'Duration':>10}   Keywords")
        print(f"  {'-'*4} {'-'*20} {'-'*10}   {'-'*30}")
        for i, (img_path, seg) in enumerate(pairs):
            kw_str = ", ".join(seg["keywords"][:3])
            print(f"  {i+1:<4} {img_path.name:<20} {seg['duration_sec']:>8.2f}s   {kw_str}")
        total_clip_secs = sum(seg["duration_sec"] for _, seg in pairs)
        est_secs        = processed_data["total_duration_sec"]
        print(f"\n  Total clip time : {total_clip_secs:.2f}s")
        print(f"  Script estimate : {est_secs:.2f}s")
        diff = abs(total_clip_secs - est_secs)
        print(f"  Difference      : {diff:.2f}s  ({diff/est_secs*100:.1f}%)\n")
    # ────────────────────────────────────────────────────────────────────────

    inputs  = []
    filters = []

    for i, (img_path, seg) in enumerate(pairs):
        secs   = seg["duration_sec"]
        padded = temp_dir / f"p_{i:04d}.jpg"
        _pad_image(img_path, padded)

        inputs.extend(["-loop", "1", "-t", f"{secs:.4f}", "-i", str(padded)])

        # Ken Burns: random start corner for variety
        px = random.choice(["iw/2-(iw/zoom/2)", "0", "iw-(iw/zoom)"])
        py = random.choice(["ih/2-(ih/zoom/2)", "0", "ih-(ih/zoom)"])
        n_frames = int(secs * FPS)
        filters.append(
            f"[{i}:v]zoompan=z='min(zoom+0.002,1.06)':x='{px}':y='{py}'"
            f":d={n_frames}:s=1920x1080:fps={FPS}[v{i}];"
        )

    concat = "".join(f"[v{i}]" for i in range(len(pairs)))
    filters.append(f"{concat}concat=n={len(pairs)}:v=1:a=0[outv]")

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + ["-filter_complex", "".join(filters),
           "-map", "[outv]",
           "-c:v", "libx264",
           "-preset", "ultrafast",
           "-pix_fmt", "yuv420p",
           str(video_path)]
    )

    try:
        _run_ffmpeg(cmd)
    except subprocess.CalledProcessError as e:
        print("[error] FFmpeg failed.")
        raise
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def _run_ffmpeg(cmd: list):
    """Run ffmpeg, streaming output line-by-line; show progress in DEBUG mode."""
    import re as _re

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    time_re = _re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
    output_lines = []

    for line in process.stdout:
        line = line.rstrip()
        output_lines.append(line)
        if DEBUG:
            print(line)
            m = time_re.search(line)
            if m:
                h, mn, s = m.groups()
                current = int(h) * 3600 + int(mn) * 60 + float(s)
                print(f"  → encoded {current:.1f}s so far")

    process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd, "\n".join(output_lines))

    class _Result:
        stdout = "\n".join(output_lines)
    return _Result()


# ==========================================
# ENTRY POINT
# ==========================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    DEBUG = args.debug

    verify_environment()
    script = read_script()
    data   = process_script(script)

    if DEBUG:
        print(f"[debug] Script words     : {len(script.split())}")
        print(f"[debug] Estimated time   : {data['total_duration_sec']:.1f}s")
        print(f"[debug] Target clip len  : {data['target_clip_sec']:.2f}s")
        print(f"[debug] Number of clips  : {len(data['segments'])}")

    imgs = get_clips(data)
    if not imgs:
        print("[error] No images downloaded.")
        sys.exit(1)

    stitch_video(imgs, data)
    print(f"\nDone: {Path('output/final_video.mp4').absolute()}")


    # === NEW ===

     #0)
    print("0) verifying params and env")
    verify_environement()
    print("        [tick] All ready to go")

    #1) 
    print("1) reading script...")
    script = read_script()         #this should only output whats happeneing if debug is set
    print("      [tick] script read")

    #2) 
    print("2) ")
    # this gets all the instructions for getting the pixel images... you know like determining key phrases...  
    # in fact, you what? use that rule of setting variables (do this at the top of main.py) for like optimal clip length etc. You know like research that opus clip has done on how long a clip should be befor eswitching? and use another constant thta represents words to time to read so that we can approximate that. Add any other variables from research done on optimal video arrangement. Add that into the equation that determines what videos / pictures to get...
    # It should aim for in and around that upper limit (e.g. lets say 8 seconds) or lower. 
    # Whilst also of course using sentiment analysis to determine the key words etc. 
    processed_script = process_script() # this will need private functoins, prepended with '__' to show they are private- probably the most complex step. So do step-by-step like a bash script so its easy to debug when debug flag is enabled.

    #3) Get clips / videos 

    #4) Stitch it all together 

    #5) Done.
