from __future__ import annotations
import hashlib

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import nltk
import ollama
import requests
from PIL import Image
from rake_nltk import Rake
import spacy

# ===========================================================================
# IMPORTS - LOCAL
# ===========================================================================

from AUDIO_SCRIPT_SYNCHRONIZER import run as run_audio_script_synchronizer
from STOCK_FOOTAGE_REVIEW import run_media_review
from STITCH_TOGETHER import stitch_together_video

print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
print("running main")
print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

# ===========================================================================
# GLOBAL FLAGS
# ===========================================================================

DEBUG: bool = True
# Whether to print verbose debug lines throughout the pipeline.
# Set to False for clean production runs.
# e.g. True  →  "[DEBUG] Step 1: breaking into scenes…"

# ===========================================================================
# NAME-BASED PATH CONFIGURATION
# Usage:  python main.py --name myproject
#         → reads script-myproject.txt, caches to CACHE-myproject/, outputs to myproject-OUTPUT/
# No --name → original behaviour: script.txt, CACHE/, OUTPUT/
# ===========================================================================

_arg_parser = argparse.ArgumentParser(add_help=False)
_arg_parser.add_argument("--name", default="")
_known_args, _ = _arg_parser.parse_known_args()
_NAME = _known_args.name.strip()

_CACHE_DIR   = f"CACHE-{_NAME}"  if _NAME else "CACHE"
_OUTPUT_DIR  = f"{_NAME}-OUTPUT" if _NAME else "OUTPUT"
_SCRIPT_STEM = f"script-{_NAME}" if _NAME else "script"

# ===========================================================================
# GLOBAL FILE / DIRECTORY PATHS
# ===========================================================================

SCRIPT_FILE: str = f"{_SCRIPT_STEM}.txt"

FINAL_VIDEO_FILE: str = f"{_CACHE_DIR}/output_video_final.mp4"

VALID_TAGS: list[str] = ["image", "stock_vid", "diagram", "ai_gen", "custom"]

MAX_CLIP_SECONDS: float = 8.0

WORDS_PER_MINUTE = 155
OPTIMAL_CLIP_LENGTH_SEC = 4.8
MAX_CLIP_LENGTH_SEC = 8.0
MIN_CLIP_LENGTH_SEC = 1.8

APPLY_KEN_BURNS_AFFECT = False

PEXELS_API_KEY: str = "PewOP3u4JK8nTBe0kkazrBgXPSwfeh0tWS1kE9y4eS26TzTEG0wmuGK8"

STOCK_FOOTAGE_CACHE_DIR = Path(f"{_CACHE_DIR}/stock_footage/")
HISTORY_FILE = STOCK_FOOTAGE_CACHE_DIR / "history.json"

OUTPUT_FILE = f"{_OUTPUT_DIR}/output.mp4"
TEMP_DIR    = Path("tmp_stitch/")

SCRIPT_AUDIO_FILE = f"{_SCRIPT_STEM}.wav"
SYNCHRONIZED_SCRIPT_OUTPUT_FILE = f"{_CACHE_DIR}/script_timings_seconds.json"
AUDIO_START_DELAY_SECONDS = 0.5

STOCK_FOOTAGE_TO_DOWNLOADED_MEDIA_FILE = f"{_CACHE_DIR}/stock_footage/history.json"
REVIEW_STOCK_FOOTAGE_OUTPUT_FILE       = f"{_CACHE_DIR}/stock_footage/review_accepting_footage.json"

FINAL_SCRIPT_AND_CLIPS = f"{_CACHE_DIR}/final_script_to_clips.json"

# NEW: cache of the per-scene fetched candidates (2 videos + 3 images each).
# This is what the review GUI consumes; FINAL_SCRIPT_AND_CLIPS is what stitch_together
# consumes and is only written AFTER the user has finished picking.
CANDIDATES_CACHE_FILE = f"{_CACHE_DIR}/footage_candidates.json"

LINE_INDEX_TO_SEARCH_TERM_FILE = f"{_NAME}_script_to_search_term.json" if _NAME else "script_to_search_term.json"
TIMESTAMPS_ABSOLUTE_FILE = f"{_CACHE_DIR}/{_NAME}_timestamps_absolute.json" if _NAME else f"{_CACHE_DIR}/timestamps_absolute.json"
# ===========================================================================
# Create all required dirs and files on startup
# ===========================================================================

Path(_CACHE_DIR).mkdir(parents=True, exist_ok=True)
Path(_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ===========================================================================
# STEP 1  –  SCRIPTS
# ===========================================================================

def save_to_cache(data: list, file_path: str):
    """Saves the script and footage data to a JSON file."""
    try:
        Path(file_path).write_text(json.dumps(data, indent=4))
    except Exception as e:
        print(f"Error saving cache: {e}")

def load_from_cache(file_path: str) -> list | None:
    """Loads data from the JSON file. Returns None if file doesn't exist or is invalid."""
    p = Path(file_path)
    if not p.exists():
        print("ERROR! THE FILE DOESN'T EXIST: "+str(file_path))
        return None
    try:
        print("ERROR! THE FILE DOESN'T EXIST: "+str(file_path))
        return json.loads(p.read_text())
    except Exception:
        return None

from pathlib import Path
import json
import sys

def load_json(file_path: str) -> dict:
    """Loads JSON data from file into a dictionary. Exits if file doesn't exist or is invalid."""
    p = Path(file_path)
    
    if not p.exists():
        print(f"ERROR! THE FILE DOESN'T EXIST: {file_path}")
        sys.exit(1)
    
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        print(f"ERROR! INVALID JSON IN FILE: {file_path}")
        sys.exit(1)


# ---------------------------------------------------------------------------

def get_script_text_to_stock_footage_search(scene_lines: list[str]) -> dict[str, str]:
    """
    Returns
    -------
    dict[str, str]
        { original_narration_line: pexels_search_term }

        e.g.::

            {
                "the empire state building is really big.":
                    "Empire State Building",
                "built on a giant pedestal.":
                    "Empire State Building pedestal",   
                "they were created by the romans.":
                    "romans pedestal",                  
                "where they drunk chanoyu tea,":
                    "chanoyu tea",                      
            }
    """

    result: dict[str, str] = {}

    return result

# ===========================================================================
# STEP 2 GET IMAGES
# ===========================================================================


#  --- History helpers  (cache index: url → local file path) ---

def _load_history() -> dict:
    """
    Load the URL→local-path cache index from disk.

    Returns an empty dict if the file does not exist or is corrupt,
    so callers never need to handle a missing file.

    Returns
    -------
    dict
        e.g. {"https://images.pexels.com/.../photo.jpeg": "stock_footage/photo.jpg"}
    """
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_history(history: dict) -> None:
    """
    Persist the URL→local-path cache index to disk.

    Parameters
    ----------
    history : dict
        e.g. {"https://images.pexels.com/.../photo.jpeg": "stock_footage/photo.jpg"}
    """
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


# --- Scene timing helper ---

def _get_num_stock_images(input_script: str) -> tuple[int, float]:
    """
    Decide how many stock images a scene needs and how long it should run.

    Parameters
    ----------
    input_script : str = The raw narration text for a single scene.
                        e.g. "The Empire State Building is really big."

    Returns
    -------
    (num_images, runtime_seconds) : tuple[int, float]
        e.g. (1, 2.8)  for a short 7-word scene
        e.g. (2, 8.0)  for a 28-word scene

    """
    # --- Load timings file ---
    timings_path = Path(SYNCHRONIZED_SCRIPT_OUTPUT_FILE)
    timings: dict = json.loads(timings_path.read_text())

    if input_script not in timings:
        available = "\n".join(f"  - {k}" for k in timings.keys())
        raise KeyError(
            f"[_get_num_stock_images] Could not find timing for:\n"
            f"  '{input_script}'\n\n"
            f"Available keys:\n{available}"
        )
        exit()

    runtime_per_scene_seconds: float = float(timings[input_script])

    # If the runtime of that scene is longer than the maximum_runtime before viewer gets bored, determine how many scene switches we need
    num_images = max(1, math.ceil(runtime_per_scene_seconds / MAX_CLIP_SECONDS))

    # If the scene is long enough to warrant multiple clips, split evenly
    # --> it says 'max runtime' as if we can't find clips long enough, we may have multiple clips...
    max_runtime_per_clip_seconds = runtime_per_scene_seconds/num_images

    return num_images, max_runtime_per_clip_seconds


# ---------------------------------------------------------------------------
# Pexels fetch + local cache
# ---------------------------------------------------------------------------

def _fetch_stock_video(stock_footage_search_term: str, num_clips: int, page: int = 1) -> list[tuple[str, float]]:
    """Returns list of (url, duration_seconds)."""
    history = _load_history()

    resp = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={
            "query":       stock_footage_search_term,
            "per_page":    num_clips,
            "orientation": "landscape",
            "page":        page,
        },
    )
    if resp.status_code != 200:
        return []

    videos = resp.json().get("videos", [])
    if not videos:
        return []

    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, float]] = []
    for video in videos:
        files = sorted(video.get("video_files", []), key=lambda f: f.get("width", 0), reverse=True)
        if not files:
            continue
        url = files[0]["link"]
        duration = float(video.get("duration", 0))

        if url not in history:
            vid_resp = requests.get(url, stream=True)
            if vid_resp.status_code == 200:
                safe_name = f"pexels-video-{video['id']}.mp4"
                dest = STOCK_FOOTAGE_CACHE_DIR / safe_name
                with open(dest, "wb") as f:
                    for chunk in vid_resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                history[url] = str(dest)

        results.append((url, duration))

    _save_history(history)
    return results

def _distribute_trims(clip_durations: list[float], target: float) -> list[float]:
    """
    Spread `target` seconds across clips as evenly as possible.
    Short clips that can't fill their equal share are used fully,
    remainder is redistributed across the rest.

     e.g. if the max_runtime_per_clip_seconds is 8, and the first clip is 2, and the second is 11, then it will try and minimise the smallest clip length. So then the first would get 2 and the second would get 6 (it will be trimmed later).. but then if the first clip was 6 and the second clip was 6, they'd both be trimmed to 4 seconds..
    If the clip is longer or equal, then the trim would just be set to max_runtime_per_clip_seconds
    """
    trims = [0.0] * len(clip_durations)
    remaining = target
    sorted_indices = sorted(range(len(clip_durations)), key=lambda i: clip_durations[i])

    for rank, i in enumerate(sorted_indices):
        remaining_clips = len(sorted_indices) - rank
        equal_share = remaining / remaining_clips
        if clip_durations[i] <= equal_share:
            trims[i] = clip_durations[i]
            remaining -= clip_durations[i]
        else:
            for j in sorted_indices[rank:]:
                trims[j] = min(equal_share, clip_durations[j])
            break

    return trims

def _get_video_metadata(search_term: str, max_results: int = 10, page: int = 1) -> list[tuple[str, float]]:
    """
    Hit Pexels Videos API, return (url, duration) pairs — NO downloading yet.
    e.g. [("https://player.vimeo.com/...mp4", 12.0), ("https://...", 7.0)]
    """
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={"query": search_term, "per_page": max_results, "orientation": "landscape", "page": page},
        timeout=8,
    )
    if resp.status_code != 200:
        print(f"  [video meta] API error {resp.status_code} for '{search_term}'")
        return []

    results = []
    for video in resp.json().get("videos", []):
        files = sorted(video.get("video_files", []), key=lambda f: f.get("width", 0), reverse=True)
        if files:
            results.append((files[0]["link"], float(video.get("duration", 0))))

    print(f"  [video meta] '{search_term}' p{page} → {len(results)} results")
    return results


def _download_clip(url: str) -> str | None:
    """
    Download a single video to cache. Returns local path, or None on failure.
    e.g. "CACHE/stock_footage/pexels-video-12345.mp4"
    """
    history = _load_history()
    if url in history and Path(history[url]).exists():
        print(f"  [cache hit] {Path(history[url]).name}")
        return history[url]

    # Generate a unique name based on the URL hash
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    filename = f"pexels-{url_hash}.mp4"
    
    print(f"  [download] {filename} ...")
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STOCK_FOOTAGE_CACHE_DIR / filename

    vid_resp = requests.get(url, stream=True, timeout=30)
    if vid_resp.status_code != 200:
        print(f"  [download] FAILED {vid_resp.status_code}")
        return None

    with open(dest, "wb") as f:
        for chunk in vid_resp.iter_content(chunk_size=65536):  # 64kb chunks, faster
            f.write(chunk)

    history[url] = str(dest)
    _save_history(history)
    print(f"  [download] done → {dest.name}")
    return str(dest)


def _download_image(url: str) -> str | None:
    """
    Download a single image to cache. Returns local path, or None on failure.
    e.g. "CACHE/stock_footage/pexels-img-abc123.jpg"
    """
    history = _load_history()
    if url in history and Path(history[url]).exists():
        print(f"  [cache hit img] {Path(history[url]).name}")
        return history[url]

    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    # Try to preserve the extension from the URL
    ext = ".jpg"
    for cand_ext in (".jpg", ".jpeg", ".png", ".webp"):
        if cand_ext in url.lower().split("?")[0]:
            ext = cand_ext
            break
    filename = f"pexels-img-{url_hash}{ext}"

    print(f"  [download img] {filename} ...")
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STOCK_FOOTAGE_CACHE_DIR / filename

    try:
        img_resp = requests.get(url, stream=True, timeout=15)
    except Exception as exc:
        print(f"  [download img] FAILED {exc}")
        return None
    if img_resp.status_code != 200:
        print(f"  [download img] FAILED {img_resp.status_code}")
        return None

    with open(dest, "wb") as f:
        for chunk in img_resp.iter_content(chunk_size=65536):
            f.write(chunk)

    history[url] = str(dest)
    _save_history(history)
    print(f"  [download img] done → {dest.name}")
    return str(dest)


def _fetch_stock_footage(search_term: str, num_clips: int, max_runtime_per_clip_seconds: float) -> list[dict]:
    """
    [LEGACY — kept for backwards-compat / debugging.]
    The new pipeline uses _fetch_stock_footage_candidates() instead.

    Fetch enough clips to fill the WHOLE scene runtime (num_clips * max_runtime_per_clip_seconds),
    with each individual clip capped at max_runtime_per_clip_seconds.
    """
    total_needed = num_clips * max_runtime_per_clip_seconds   # the actual full scene duration

    # ── 1. Collect metadata until we cover the full scene ─────────────────
    # Each candidate's contribution is capped at max_runtime_per_clip_seconds,
    # so a single 60-second Pexels clip only counts for max_runtime_per_clip_seconds towards coverage.
    def _coverage(items: list[tuple[str, float]]) -> float:
        return sum(min(d, max_runtime_per_clip_seconds) for _, d in items)

    collected: list[tuple[str, float]] = []
    seen: set[str] = set()

    for page in range(1, 4):  # max 3 pages
        for url, dur in _get_video_metadata(search_term, max_results=10, page=page):
            if url not in seen and dur > 0:
                collected.append((url, dur))
                seen.add(url)
            if _coverage(collected) >= total_needed:
                break
        if _coverage(collected) >= total_needed or not collected:
            break

    # ── 2. Pick the minimum subset that covers total_needed ───────────────
    chosen: list[tuple[str, float]] = []
    covered = 0.0
    for url, dur in collected:
        chosen.append((url, dur))
        covered += min(dur, max_runtime_per_clip_seconds)
        if covered >= total_needed:
            break

    if not chosen:
        # ── Image fallback (unchanged behaviour) ──────────────────────────
        print(f"  [image fallback] no videos for '{search_term}'")
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": search_term, "per_page": num_clips, "orientation": "landscape"},
            timeout=8,
        )
        if resp.status_code != 200 or not resp.json().get("photos"):
            return []
        photos = resp.json()["photos"]
        history = _load_history()
        results = []
        for p in photos:
            url = p["src"]["large2x"]
            if url not in history:
                img_data = requests.get(url, timeout=10).content
                name = url.rstrip("/").split("/")[-1].split("?")[0]
                dest = STOCK_FOOTAGE_CACHE_DIR / (name if name.endswith(".jpg") else name + ".jpg")
                dest.write_bytes(img_data)
                history[url] = str(dest)
            results.append({url: total_needed / len(photos)})
        _save_history(history)
        return results

    # ── 3. Download chosen clips ──────────────────────────────────────────
    valid: list[tuple[str, float]] = []
    for url, dur in chosen:
        print("     ... downloading clip:", url)
        local = _download_clip(url)
        print("     ...[done]")
        if local:
            valid.append((url, dur))

    if not valid:
        return []

    # ── 4. Distribute trims so they sum to total_needed ───────────────────
    cap = lambda dur: min(dur, max_runtime_per_clip_seconds)
    trims: list[float] = [0.0] * len(valid)
    remaining = total_needed

    for i, (_, dur) in enumerate(valid):
        share = remaining / (len(valid) - i)
        trims[i] = min(cap(dur), share)
        remaining -= trims[i]

    if remaining > 0.01:
        for i, (_, dur) in enumerate(valid):
            headroom = cap(dur) - trims[i]
            if headroom > 0:
                extra = min(remaining, headroom)
                trims[i] += extra
                remaining -= extra
                if remaining <= 0.01:
                    break

    if remaining > 0.01:
        i_best = max(range(len(valid)), key=lambda i: valid[i][1] - trims[i])
        extra = min(remaining, valid[i_best][1] - trims[i_best])
        trims[i_best] += extra
        remaining -= extra
        if remaining > 0.01:
            print(f"  ⚠️  could only cover {total_needed - remaining:.2f}s of {total_needed:.2f}s "
                  f"for '{search_term}' — sources too short.")

    total_trim = sum(trims)
    print(f"  [footage] {len(valid)} clip(s), trims: {[round(t,2) for t in trims]}, "
          f"total {total_trim:.2f}s (needed {total_needed:.2f}s)")
    return [{url: trim} for (url, _), trim in zip(valid, trims)]


# ---------------------------------------------------------------------------
# NEW: multi-candidate fetch (2 videos + 3 images per scene)
# ---------------------------------------------------------------------------

def _fetch_video_candidates(search_term: str, max_runtime_per_clip_seconds: float,
                            num_videos: int = 2) -> list[dict]:
    """
    Fetch up to `num_videos` distinct candidate videos for a single scene.
    Downloads each to local cache. Returns:
        [{url: trim_secs}, ...]   (trim = min(real_duration, max_runtime_per_clip_seconds))
    """
    collected: list[tuple[str, float]] = []
    seen: set[str] = set()

    for page in range(1, 4):  # walk up to 3 pages of results
        if len(collected) >= num_videos:
            break
        for url, dur in _get_video_metadata(search_term, max_results=10, page=page):
            if url in seen or dur <= 0:
                continue
            seen.add(url)
            collected.append((url, dur))
            if len(collected) >= num_videos:
                break

    out: list[dict] = []
    for url, dur in collected[:num_videos]:
        local = _download_clip(url)
        if not local:
            continue
        trim = min(dur, max_runtime_per_clip_seconds)
        out.append({url: round(trim, 2)})

    print(f"  [video candidates] '{search_term}' → {len(out)} video(s)")
    return out


def _fetch_image_candidates(search_term: str, max_runtime_per_clip_seconds: float,
                            num_images: int = 3) -> list[dict]:
    """
    Fetch up to `num_images` distinct candidate images for a single scene.
    Downloads each to local cache. Returns:
        [{url: trim_secs}, ...]   (trim = max_runtime_per_clip_seconds, since images don't have a duration)
    """
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": search_term, "per_page": max(num_images, 5),
                    "orientation": "landscape"},
            timeout=8,
        )
    except Exception as exc:
        print(f"  [image candidates] API error: {exc}")
        return []

    if resp.status_code != 200:
        print(f"  [image candidates] API error {resp.status_code} for '{search_term}'")
        return []

    photos = resp.json().get("photos", []) or []
    out: list[dict] = []
    seen: set[str] = set()
    for p in photos:
        if len(out) >= num_images:
            break
        url = (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large")
        if not url or url in seen:
            continue
        seen.add(url)
        local = _download_image(url)
        if not local:
            continue
        out.append({url: round(float(max_runtime_per_clip_seconds), 2)})

    print(f"  [image candidates] '{search_term}' → {len(out)} image(s)")
    return out


def _fetch_stock_footage_candidates(search_term: str,
                                    max_runtime_per_clip_seconds: float) -> dict:
    """
    Fetch a CANDIDATE BUNDLE for a single scene: 2 videos + 3 images.
    The user picks from these via the review GUI.

    Returns
    -------
    dict
        {
            "videos": [{url: trim_secs}, ...],    # up to 2 entries
            "images": [{url: trim_secs}, ...],    # up to 3 entries
        }
    """
    videos = _fetch_video_candidates(search_term, max_runtime_per_clip_seconds, num_videos=2)
    images = _fetch_image_candidates(search_term, max_runtime_per_clip_seconds, num_images=3)
    return {"videos": videos, "images": images}


# ---------------------------------------------------------------------------

def load_stock_footage(all_scenes: dict) -> list[dict]:
    """
    Build the candidates list (2 videos + 3 images per scene) the review GUI
    will display.

    Returns
    -------
    list[dict]   e.g.::
        [
            {
              "script_text": "The Empire State Building is really big.",
              "candidates": {
                "videos": [{url: trim}, {url: trim}],
                "images": [{url: trim}, {url: trim}, {url: trim}],
              },
              "num_clips_needed": 1,
              "max_runtime_per_clip_seconds": 4.8,
            },
            ...
        ]
    """
    out: list[dict] = []
    for script_text, search_term in all_scenes.items():
        num_clips, max_runtime = _get_num_stock_images(script_text)
        print(f"\n[fetch] '{script_text}'")
        print(f"        search='{search_term}' clips={num_clips} max_runtime={max_runtime:.2f}s")
        candidates = _fetch_stock_footage_candidates(search_term, max_runtime)
        out.append({
            "script_text":                    script_text,
            "candidates":                     candidates,
            "num_clips_needed":               num_clips,
            "max_runtime_per_clip_seconds":   max_runtime,
        })
    return out


# ===================================

def additional_steps_save_for_later():
    # (ADDITIONAL STEPS TO SAVE FOR LATER)
    # Custom images
    # e.g. like you have a default background like that moving slightly crunched paper affects
    # - then layer over the elements (e.g. three wise men, or map with arrow / lines...)
    # - try and reuse lines / have set textures and appearance in a  config file... So same style for each vid... 
    #
    #
    #
    # x)
    # Manually select scenes to have Ken Burns effect
    # Only after all done and good 
    pass



# ===========================================================================
# MAIN  –  STEP 3 - STITCH TOGETHER
# ===========================================================================


# ===========================================================================
# MAIN  –  ORCHESTRATOR
# ===========================================================================

def verify_environment():
    # if not Path("script.txt").exists():
        # print("[error] script.txt not found.")
        # sys.exit(1)
    # if not shutil.which("ffmpeg"):
        # print("[error] ffmpeg not installed.")
        # sys.exit(1)
# 
    # api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    # placeholders = ["NOT_SET_YET", "YOUR_KEY_HERE", "PLACEHOLDER"]
    # if not api_key or any(p in api_key.upper() for p in placeholders):
        # print("[error] PEXELS_API_KEY is missing or invalid.")
        # sys.exit(1)
# 
    # try:
        # resp = requests.get(
            # "https://api.pexels.com/v1/search",
            # headers={"Authorization": api_key},
            # params={"query": "test", "per_page": 1},
            # timeout=5
        # )
        # if resp.status_code == 401:
            # print("[error] PEXELS_API_KEY Unauthorized.")
            # sys.exit(1)
    # except Exception as e:
        # print(f"[error] API Connection failed: {e}")
        # sys.exit(1)
    pass



def split_text_into_sections(section):
    lines = section.split("\n")
    cleaned = []

    for line in lines:
        line = line.strip()

        # skip empty lines
        if not line:
            continue

        # skip markdown headings
        if re.match(r"^#+\s", line):
            continue

        cleaned.append(line)

    return cleaned


def main() -> None:
    """
    Runs the full pipeline from raw script to finished video.
    Each stage is a clearly labelled block – treat this like a bash script.
    Comment out any stage to resume from a checkpoint.
    """

    # --- Stage -1 ----
    verify_environment()

    # 1)
    # - Manually break into scenes
    # - get the search terms for pexels

    print("====================================================================")
    print("Breaking into scenes...")
    scriptTextToPexelSearch: dict[str, str] = load_json(LINE_INDEX_TO_SEARCH_TERM_FILE)
    print("!!!!!!script text to pexel search:")
    print(scriptTextToPexelSearch)
    # e.g. scriptTextToPexelSearch = 
            # { "The empire state building is really big.": "empire state building",
                # ...
                # "the samurai of Japan ruled over the kingdom.": "samurai warriors japan", }

    # 1.5)
    # Generate the timestamps to match up the recorded audio to the script
    run_audio_script_synchronizer(SCRIPT_AUDIO_FILE, LINE_INDEX_TO_SEARCH_TERM_FILE,
                                  SYNCHRONIZED_SCRIPT_OUTPUT_FILE, TIMESTAMPS_ABSOLUTE_FILE,
                                  AUDIO_START_DELAY_SECONDS)



    # 2) fetch CANDIDATES (2 videos + 3 images per scene)
    print("====================================================================")
    print("Loading stock footage candidates...")

    candidates_data = load_from_cache(CANDIDATES_CACHE_FILE)
    if candidates_data:
        print(f"✅ Loaded {len(candidates_data)} candidate bundle(s) from cache.")
    else:
        print("🔍 Cache miss. Fetching candidates from Pexels...")
        candidates_data = load_stock_footage(scriptTextToPexelSearch)
        save_to_cache(candidates_data, CANDIDATES_CACHE_FILE)
        print(f"💾 Cached {len(candidates_data)} candidate bundle(s) to {CANDIDATES_CACHE_FILE}.")

    # ---- PRINT THE CANDIDATES MAP ----
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
    print("\n=== SCRIPT → CANDIDATE MEDIA ===")
    for entry in candidates_data:
        print(f"\nSCRIPT: {entry['script_text']}")
        print(f"  needs {entry.get('num_clips_needed', 1)} clip(s), "
              f"each ≤ {entry.get('max_runtime_per_clip_seconds', 0):.2f}s")
        cands = entry.get("candidates", {}) or {}
        print("  VIDEOS:")
        for item in cands.get("videos", []):
            for url, trim in item.items():
                print(f"    - {url}  (trim: {trim}s)")
        print("  IMAGES:")
        for item in cands.get("images", []):
            for url, trim in item.items():
                print(f"    - {url}  (trim: {trim}s)")
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

    additional_steps_save_for_later()

    # 2.5) review the candidates
    print("====================================================================")
    print("Launching media review GUI...")
    final_data, has_manual = run_media_review(
        candidates_data=candidates_data,
        history_file=str(HISTORY_FILE),
        review_state_file=REVIEW_STOCK_FOOTAGE_OUTPUT_FILE,
        cache_dir=_CACHE_DIR,
    )

    if has_manual:
        # Instructions have already been printed by run_media_review.
        # User must drop their files, edit the JSONs, then re-run.
        print("\n[main] Exiting so you can perform the manual fixes above.")
        print("       Re-run when done — already-decided scenes will be skipped.")
        sys.exit(0)

    # All scenes decided automatically — persist the legacy-format mapping
    # that stitch_together_video expects.
    save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
    print(f"💾 Final script→clips map written to {FINAL_SCRIPT_AND_CLIPS}.")

    # ---- PRINT THE FINAL CHOSEN MAP ----
    print("\n=== FINAL SCRIPT → CHOSEN MEDIA ===")
    for entry in final_data:
        print(f"\nSCRIPT: {entry['script_text']}")
        for item in entry["footage"]:
            for url, trim in item.items():
                print(f"  ✓ {url}  (trim: {trim}s)")
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

    # 3)
    # Stitch together into initial video
    # - maybe option to add the voice track? no probs not.
    print("====================================================================")
    stitch_together_video(FINAL_SCRIPT_AND_CLIPS, TIMESTAMPS_ABSOLUTE_FILE, HISTORY_FILE, SCRIPT_AUDIO_FILE, OUTPUT_FILE)

    # et.log(DEBUG, f"Pipeline complete.  Final video: '{final_video}'")


# ===========================================================================

if __name__ == "__main__":
    main()


# ================================================
# ==== OTHER THINGS MAYBE USEFUL DOWN THE LINE ===
# ================================================

def splitSceneIntoPowerpointSlideImages():
    # === SPLIT SCENE INTO MULTIPLE DIFERENT IMAGES ===
    twotest="But where exactly in the world did this tea originate"

    ai_request = f"Split this sentence into the different images that would make up this slide on my powerpoint. Identify the key nouns and visual elements. Just simple bullet point: \n{twotest}"

    response2 = ollama.chat(
        model="qwen2.5:7b",
        messages=[
            {"role": "user", "content": ai_request}
        ]
    )

    reply2 = response2["message"]["content"]
    print(reply2)

    # --------------
    # === (CLEANUP) SPLIT SCENE INTO MULTIPLE DIFERENT IMAGES ===

    ai_request2 = f"Strip out any ai fulff, explanations, headings or follow up questions: give me just a csv of identified key terms. nothing else: \n{reply2}"

    response3 = ollama.chat(
        model="qwen2.5:7b",
        messages=[
            {"role": "user", "content": ai_request2}
        ]
    )
    reply3 = response3["message"]["content"]
    print(reply3)



def determineIfStockVideo():
    # -------------
    # === DETERMINE IF STOCK FOOTAGE ===

    scenes_text = """
    The empire state building is really big.
    Built in Manhattan in the 19th century.
    Back in 1946,
    the technician John Ford the second
    created a new carburettor for
    the lift in the skyscraper
    where they drunk chanoyu tea,
    which would go on to revolutionize the entire world.
    But where exactly in the world did this tea originate?
    It was in the newly formed state of Okinawa.
    Back in the 1700s,
    the samurai of Japan ruled over the kingdom.
    They discovered Koshuta —
    a type of rare plant which only grows in the foothills of the Japanese Alps...
    """

    # turn each non-empty line into a scene
    scenes = [line.strip() for line in scenes_text.split("\n") if line.strip()]

    for scene in scenes:
        ai_request = f"""
    Would this scene be likely to have nice stock footage available on sites like Pexels, Pixabay or Storyblocks?

    Things like: dates, anything that is abstract without key nouns.. Obscure specific things like a specific type of something... or a named person who isn't that famous.. will not have stock footage (video) available.
    Only popular things, with obvious nouns will. 
    Or maybe like popular celebrities etc.

    Scene: {scene}

    Just output:
    yes
    or
    no

    Nothing else.
    """

    response4 = ollama.chat(
        model="qwen2.5:7b",
        messages=[
            {"role": "user", "content": ai_request}
        ]
    )

    reply4 = response4["message"]["content"].strip()

    print(scene)
    print(reply4)
    print("-----")
