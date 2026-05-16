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
from enum import Enum
from typing import TypedDict
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
from JOINT_IMAGE_CREATOR import composite as create_joint_scene, TRANSITION_RANDOM, TRANSITION_FADE

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
# JOINT SCENE INTEGRATION
# ===========================================================================
# These mirror constants in JOINT_IMAGE_CREATOR.compositor so we can compute
# timing without importing private helpers.
#
# TRANSITION_DURATION (0.6) + INTRO_TAIL_PADDING (0.05) — total length of the
# stage_NN_of_NN.mp4 "intro" file emitted by the compositor when the newest
# overlay animates in.
JOINT_INTRO_DURATION_SEC: float = 0.65

# If a scene is shorter than this, skip the transition entirely and just
# use the _loop.mp4 file for the whole scene. Below ~1s there isn't time
# for the animation to read clearly anyway.
JOINT_MIN_SCENE_DURATION_FOR_TRANSITION_SEC: float = 1.0

# Floor for the duration we pass to create_joint_scene. The compositor will
# emit a _loop.mp4 of at least this length per stage; the stitcher trims
# each stage's loop down to its actual per-stage need.
JOINT_BASE_DURATION_FALLBACK_SEC: float = 3.0



# ===========================================================================
# SEARCH TERM TYPES
# ===========================================================================

class MediaType(Enum):
    STOCK = "stock"
    JOINT = "joint"


class MediaVariant(Enum):
    DEFAULT = "default"
    THREE_ROW = "3 row"


class SearchTermData(TypedDict):
    search_term: str
    search_type: MediaType
    variant: MediaVariant
    position: str


# ===========================================================================
# JOINT SCENE LAYOUTS
# ===========================================================================
# TODO - consider moving this to the JOINT_IMAGE_CREATOR file

JOINT_LAYOUT_POSITIONS = {
    MediaVariant.THREE_ROW: [
        [25, 50],
        [50, 50],
        [75, 50],
    ],
}

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

def get_script_text_to_stock_footage_search(scene_lines: list[str]) -> dict[str, SearchTermData]:
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

# ---------------------------------------------------------------------------
# Joint scene timing helpers
# ---------------------------------------------------------------------------

def _load_scene_timings() -> dict[str, float]:
    """Return {script_text → runtime_seconds} from the audio-sync output."""
    p = Path(SYNCHRONIZED_SCRIPT_OUTPUT_FILE)
    print(f"\n[joint:timings] loading scene timings from {p}")
    if not p.exists():
        print(f"[joint:timings] FATAL: timings file missing: {p}")
        print( "[joint:timings]   (did run_audio_script_synchronizer run?)")
        sys.exit(1)
    timings = {k: float(v) for k, v in json.loads(p.read_text()).items()}
    print(f"[joint:timings] loaded {len(timings)} timing entries")
    return timings


def _compute_joint_stage_timing(
    script_text: str,
    scene_timings: dict[str, float],
) -> dict:
    """
    Decide how to split a single joint stage's runtime between the intro
    (transition) file and the loop (resting) file.

    Returns::
        {
          "script_text":    str,
          "total_duration": float,   # full scene runtime from audio sync
          "use_transition": bool,    # False for very short scenes
          "intro_duration": float,   # 0 if no transition else ~0.65
          "loop_duration":  float,   # remainder; can be 0
        }
    """
    if script_text not in scene_timings:
        available = "\n".join(f"    - {k}" for k in scene_timings)
        print(f"[joint:timings] FATAL: no timing for joint stage:")
        print(f"   '{script_text}'")
        print(f"   Available timings:\n{available}")
        sys.exit(1)

    total = scene_timings[script_text]
    use_transition = total >= JOINT_MIN_SCENE_DURATION_FOR_TRANSITION_SEC
    intro = JOINT_INTRO_DURATION_SEC if use_transition else 0.0
    loop  = max(0.0, total - intro)

    print(f"[joint:timings] '{script_text[:70]}'")
    print(f"[joint:timings]   total={total:.3f}s  use_transition={use_transition}")
    print(f"[joint:timings]   intro={intro:.3f}s  loop={loop:.3f}s")

    return {
        "script_text":    script_text,
        "total_duration": total,
        "use_transition": use_transition,
        "intro_duration": intro,
        "loop_duration":  loop,
    }


def _stage_file_paths(
    group_output_folder: Path,
    stage_index: int,
    num_stages: int,
) -> tuple[Path, Path]:
    """Return (intro_path, loop_path) for one stage of a joint group."""
    intro = group_output_folder / f"stage_{stage_index + 1:02d}_of_{num_stages:02d}.mp4"
    loop  = group_output_folder / f"stage_{stage_index + 1:02d}_of_{num_stages:02d}_loop.mp4"
    return intro, loop


def _build_footage_entries_for_stage(
    group_output_folder: Path,
    stage_index: int,
    num_stages: int,
    timing: dict,
) -> list[dict]:
    """
    Build the {path: trim_secs} entries the stitcher plays back-to-back for
    a single joint stage.

    Layout when use_transition is True:
        [intro_file: intro_duration, loop_file: loop_duration]

    Layout when use_transition is False (very short scene):
        [loop_file: total_duration]
    """
    intro_path, loop_path = _stage_file_paths(
        group_output_folder, stage_index, num_stages,
    )

    print(f"\n[joint:footage] stage {stage_index + 1}/{num_stages}")
    print(f"[joint:footage]   intro: {intro_path}  exists={intro_path.exists()}")
    print(f"[joint:footage]   loop:  {loop_path}   exists={loop_path.exists()}")
    print(f"[joint:footage]   timing: {timing}")

    if not intro_path.exists():
        print(f"[joint:footage] FATAL: expected intro file missing: {intro_path}")
        sys.exit(1)

    entries: list[dict] = []

    if timing["use_transition"]:
        if not loop_path.exists():
            print(f"[joint:footage] FATAL: transition stage missing loop file: {loop_path}")
            sys.exit(1)

        entries.append({str(intro_path): round(timing["intro_duration"], 3)})
        print(f"[joint:footage]   → intro entry: {intro_path.name}  "
              f"trim={timing['intro_duration']:.3f}s")

        if timing["loop_duration"] > 0.01:
            entries.append({str(loop_path): round(timing["loop_duration"], 3)})
            print(f"[joint:footage]   → loop  entry: {loop_path.name}  "
                  f"trim={timing['loop_duration']:.3f}s")
        else:
            print(f"[joint:footage]   (loop omitted — duration <= 0.01s)")
    else:
        # Short scene: skip the transition, use the resting composition
        # (loop file) for the full scene duration. The loop file always
        # exists because the newest overlay is always TRANSITION_RANDOM
        # in composite_flag mode — but guard anyway.
        use_path = loop_path if loop_path.exists() else intro_path
        entries.append({str(use_path): round(timing["total_duration"], 3)})
        print(f"[joint:footage]   → static entry: {use_path.name}  "
              f"trim={timing['total_duration']:.3f}s  (no transition: scene too short)")

    print(f"[joint:footage]   total entries for this stage: {len(entries)}")
    return entries


# ---------------------------------------------------------------------------
# Joint scene → final_data integration
# ---------------------------------------------------------------------------

def _merge_joint_scenes_into_final_data(
    final_data: list[dict],
    joint_footage_map: dict[str, list[dict]],
) -> list[dict]:
    """
    Replace the `footage` list in `final_data` for any script_text that the
    joint generator produced output for. Joint stages that weren't already
    in final_data are appended at the end.
    """
    print("\n" + "=" * 70)
    print(f"[joint:merge] merging {len(joint_footage_map)} joint stage(s) into final_data")
    print(f"[joint:merge] final_data currently has {len(final_data)} entry(ies)")
    print("=" * 70)

    by_script = {entry["script_text"]: i for i, entry in enumerate(final_data)}

    replaced = 0
    appended = 0

    for script_text, entries in joint_footage_map.items():
        if script_text in by_script:
            idx = by_script[script_text]
            old_count = len(final_data[idx].get("footage", []))
            final_data[idx]["footage"] = entries
            replaced += 1
            print(f"[joint:merge] REPLACED '{script_text[:60]}...'  "
                  f"(was {old_count} entry(ies), now {len(entries)})")
            for e in entries:
                for path, trim in e.items():
                    print(f"[joint:merge]     {Path(path).name}  trim={trim}s")
        else:
            final_data.append({"script_text": script_text, "footage": entries})
            appended += 1
            print(f"[joint:merge] APPENDED '{script_text[:60]}...'  "
                  f"({len(entries)} entry(ies))")
            for e in entries:
                for path, trim in e.items():
                    print(f"[joint:merge]     {Path(path).name}  trim={trim}s")

    print(f"\n[joint:merge] done — replaced={replaced}, appended={appended}, "
          f"final_data size now {len(final_data)}")
    return final_data


def _add_joint_paths_to_history(joint_footage_map: dict[str, list[dict]]) -> None:
    """
    The stitcher's history.json maps {url → local_path}. For our newly
    generated joint files we add identity entries (path → path) so the
    same lookup mechanism resolves them with no stitcher changes.
    """
    history = _load_history()
    print(f"\n[joint:history] augmenting history.json "
          f"(currently {len(history)} entries)")

    added = 0
    skipped = 0
    for entries in joint_footage_map.values():
        for entry in entries:
            for path in entry:
                if path in history:
                    skipped += 1
                    continue
                history[path] = path
                added += 1
                print(f"[joint:history]   + identity entry for {Path(path).name}")

    _save_history(history)
    print(f"[joint:history] done — added={added}, already_present={skipped}, "
          f"history now has {len(history)} entries")


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
    for script_text, scene_data in all_scenes.items():
        search_term = scene_data["search_term"]

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


# ----------------------------

def generate_joint_scenes( script_to_search_term: dict[str, SearchTermData], candidates_data: list[dict],) -> dict[str, list[dict]]:
    """
    Build joint composite scenes for any scene flagged MediaType.JOINT, and
    return a stitcher-ready map of:

        { script_text: [ {local_path: trim_seconds}, ... ], ... }

    Each joint stage typically contributes TWO entries:
      1. an intro file (~0.65s, plays the transition)
      2. a loop file (covers the rest of the scene)

    Very short scenes (< JOINT_MIN_SCENE_DURATION_FOR_TRANSITION_SEC) get
    just one entry — the loop file alone.
    """

    print("\n" + "=" * 70)
    print("[joint scenes] STARTING generate_joint_scenes")
    print(f"[joint scenes] script_to_search_term has {len(script_to_search_term)} entries")
    print(f"[joint scenes] candidates_data has {len(candidates_data)} entries")
    print("=" * 70)

    # Load timings up front so we can size each group's loops correctly.
    scene_timings = _load_scene_timings()

    # ── Build lookup dicts for reliable matching ──────────────────────
    candidates_by_text: dict[str, dict] = {c["script_text"]: c for c in candidates_data}
    candidates_by_stripped: dict[str, dict] = {c["script_text"].strip(): c for c in candidates_data}
    print(f"[joint scenes] candidates lookup built with {len(candidates_by_text)} entries")

    # 1) Locate all scenes flagged as 'joint'.
    joint_scenes: list[tuple[str, SearchTermData]] = []
    for script_text, scene_data in script_to_search_term.items():
        print(f"[joint scenes] checking scene: type={scene_data.get('search_type')}, "
              f"variant={scene_data.get('variant')}, pos={scene_data.get('position')}, "
              f"script='{script_text[:60]}...'")
        if scene_data["search_type"] != MediaType.JOINT:
            print(f"[joint scenes]   → SKIPPED (not JOINT)")
            continue
        joint_scenes.append((script_text, scene_data))
        print(f"[joint scenes]   → KEPT as joint scene")

    print(f"\n[joint scenes] found {len(joint_scenes)} joint scene(s) before sorting")
    if not joint_scenes:
        print("[joint scenes] no joint scenes — returning empty map")
        return {}

    joint_scenes.sort(key=lambda scene: int(scene[1]["position"]))
    for i, (txt, data) in enumerate(joint_scenes):
        print(f"[joint scenes]   sorted[{i}]: pos={data['position']}, script='{txt[:60]}...'")

    # 2) Group consecutive joints by (type, variant, contiguous position).
    grouped_joint_scenes: list[list[tuple[str, SearchTermData]]] = []
    current_group: list[tuple[str, SearchTermData]] = []
    previous_scene_data = None

    for script_text, scene_data in joint_scenes:
        if not previous_scene_data:
            current_group.append((script_text, scene_data))
            previous_scene_data = scene_data
            print(f"[joint scenes] grouping: started first group with pos={scene_data['position']}")
            continue

        same_type     = scene_data["search_type"] == previous_scene_data["search_type"]
        same_variant  = scene_data["variant"]     == previous_scene_data["variant"]
        next_position = int(scene_data["position"]) == int(previous_scene_data["position"]) + 1

        print(f"[joint scenes] grouping: pos={scene_data['position']} vs prev={previous_scene_data['position']}")
        print(f"[joint scenes]   same_type={same_type}, same_variant={same_variant}, next_position={next_position}")

        if same_type and same_variant and next_position:
            current_group.append((script_text, scene_data))
            print(f"[joint scenes]   → ADDED to current group (now size {len(current_group)})")
        else:
            grouped_joint_scenes.append(current_group)
            print(f"[joint scenes]   → BREAK: ending group of size {len(current_group)}, starting new")
            current_group = [(script_text, scene_data)]

        previous_scene_data = scene_data

    if current_group:
        grouped_joint_scenes.append(current_group)
        print(f"[joint scenes] appended final group of size {len(current_group)}")

    print(f"\n[joint scenes] formed {len(grouped_joint_scenes)} group(s):")
    for gi, grp in enumerate(grouped_joint_scenes):
        positions = [s[1]["position"] for s in grp]
        variant   = grp[0][1]["variant"]
        print(f"[joint scenes]   group {gi}: variant={variant}, positions={positions}, "
              f"size={len(grp)}")

    # 3) Generate each group + collect footage entries.
    script_text_to_footage_entries: dict[str, list[dict]] = {}

    for group_index, group in enumerate(grouped_joint_scenes):
        variant = group[0][1]["variant"]
        print(f"\n[joint scenes] processing group {group_index}: variant={variant}, size={len(group)}")

        match variant:
            case MediaVariant.THREE_ROW:
                print("[joint scenes]   → matched THREE_ROW layout")

                layout_positions = [
                    [25, 50],
                    [50, 50],
                    [75, 50],
                ]

                box_percentage   = 28              # was scale_percentage = 30
                transition       = TRANSITION_RANDOM
                background_path  = "_BACKGROUNDS/bg_crumpled_card.mp4"
                base_duration    = JOINT_BASE_DURATION_FALLBACK_SEC
                remove_bg        = True            # NEW

                print(f"[joint scenes]   layout_positions={layout_positions}")
                print(f"[joint scenes]   box_percentage={box_percentage}")
                print(f"[joint scenes]   transition={transition}")
                print(f"[joint scenes]   background_path={background_path}")
                print(f"[joint scenes]   base_duration={base_duration}s (fallback)")
                print(f"[joint scenes]   remove_bg={remove_bg}")

            case _:
                print(f"[joint scenes] FATAL: unsupported variant: {variant}")
                sys.exit(1)

        # ── Compute per-stage timing ─────────────────────────────────
        print(f"\n[joint scenes:timing] computing timing for {len(group)} stage(s)")
        stage_timings = [
            _compute_joint_stage_timing(script_text, scene_timings)
            for script_text, _ in group
        ]

        # We pass a single `duration` to create_joint_scene that controls
        # the LOOP file's length (and the intro is auto-trimmed to 0.65s
        # when there's an animation). We want the loop file to be at
        # LEAST as long as the longest per-stage requirement so the
        # stitcher can trim each stage down to its own need.
        max_loop_duration = max(
            (t["loop_duration"] for t in stage_timings if t["use_transition"]),
            default=0.0,
        )
        max_static_duration = max(
            (t["total_duration"] for t in stage_timings if not t["use_transition"]),
            default=0.0,
        )
        composite_duration = max(max_loop_duration, max_static_duration, base_duration)
        print(f"[joint scenes:timing] max_loop_duration  = {max_loop_duration:.3f}s")
        print(f"[joint scenes:timing] max_static_duration= {max_static_duration:.3f}s")
        print(f"[joint scenes:timing] base_duration      = {base_duration:.3f}s")
        print(f"[joint scenes:timing] → composite_duration = {composite_duration:.3f}s "
              f"(passed to create_joint_scene)")

        # ── Build the items list for the compositor ──────────────────
        items = []
        for item_index, (script_text, _) in enumerate(group):
            print(f"\n[joint scenes]   item {item_index}: script='{script_text[:80]}'")

            if item_index >= len(layout_positions):
                print(f"[joint scenes] FATAL: item_index {item_index} >= layout length {len(layout_positions)}")
                sys.exit(1)

            matching_candidate = candidates_by_text.get(script_text)
            if not matching_candidate:
                print(f"[joint scenes]     exact match failed, trying stripped match...")
                matching_candidate = candidates_by_stripped.get(script_text.strip())

            if not matching_candidate:
                print(f"[joint scenes] FATAL: no matching candidate for script_text:")
                print(f"            '{script_text}'")
                print(f"  Available candidates in cache:")
                for key in candidates_by_text:
                    print(f"    - '{key}'")
                print(f"\n  HINT: Your candidates cache ({CANDIDATES_CACHE_FILE}) may be stale.")
                print(f"        Delete it and re-run to refresh:")
                print(f"        rm {CANDIDATES_CACHE_FILE}")
                sys.exit(1)

            print(f"[joint scenes]     found matching candidate ✓")

            image_candidates = matching_candidate.get("candidates", {}).get("images", [])
            print(f"[joint scenes]     image_candidates count: {len(image_candidates)}")
            if not image_candidates:
                print(f"[joint scenes] FATAL: no image candidates for script: '{script_text}'")
                sys.exit(1)

            first_image = image_candidates[0]
            print(f"[joint scenes]     first_image dict: {first_image}")
            image_url = next(iter(first_image), "")
            if not image_url:
                print(f"[joint scenes] FATAL: no image_url extracted from candidate")
                sys.exit(1)

            print(f"[joint scenes]     extracted image_url: '{image_url[:80]}...'"
                  if len(image_url) > 80 else
                  f"[joint scenes]     extracted image_url: '{image_url}'")

            history = _load_history()
            local_path = history.get(image_url)
            if local_path and Path(local_path).exists():
                print(f"[joint scenes]     cache hit: {local_path}")
            else:
                if local_path:
                    print(f"[joint scenes]     history has path but file missing: {local_path}")
                else:
                    print(f"[joint scenes]     URL not in download history")
                print(f"[joint scenes]     downloading image on-the-fly...")
                local_path = _download_image(image_url)
                if not local_path:
                    print(f"[joint scenes] FATAL: failed to download image: {image_url}")
                    sys.exit(1)
                print(f"[joint scenes]     downloaded to: {local_path}")

            items.append({
                "path":                       local_path,
                "position":                   layout_positions[item_index],
                "scale-fit-box-percentage":   box_percentage,   # was scale-page-height-percentage
                "transition":                 transition,
                "removeBG":                   remove_bg,        # NEW
            })

            print(f"[joint scenes]     → ADDED item: path={local_path}, "
                  f"position={layout_positions[item_index]}, "
                  f"box={box_percentage}%, removeBG={remove_bg}")

        print(f"\n[joint scenes]   total items built for group {group_index}: {len(items)}")
        if not items:
            print(f"[joint scenes] FATAL: no items to composite for group {group_index}")
            sys.exit(1)

        output_folder = Path(_CACHE_DIR) / "joint_scenes" / f"group_{group_index}"
        output_folder.mkdir(parents=True, exist_ok=True)
        print(f"[joint scenes]   output_folder: {output_folder}")

        print(f"[joint scenes]   calling create_joint_scene with:")
        print(f"[joint scenes]     items count = {len(items)}")
        print(f"[joint scenes]     output_folder = {str(output_folder)}")
        print(f"[joint scenes]     composite_flag = True")
        print(f"[joint scenes]     background_path = {background_path}")
        print(f"[joint scenes]     duration = {composite_duration:.3f}s")

        create_joint_scene(
            items=items,
            output_folder=str(output_folder),
            composite_flag=True,
            background_path=background_path,
            duration=composite_duration,
        )
        print(f"[joint scenes] ✓ generated group {group_index}")

        # ── Build footage entries for each stage in this group ───────
        num_stages = len(group)
        print(f"\n[joint scenes]   building stitcher footage entries for "
              f"{num_stages} stage(s) in group {group_index}")
        for stage_index, (script_text, _) in enumerate(group):
            timing = stage_timings[stage_index]
            entries = _build_footage_entries_for_stage(
                group_output_folder=output_folder,
                stage_index=stage_index,
                num_stages=num_stages,
                timing=timing,
            )
            script_text_to_footage_entries[script_text] = entries

    # ── Summary ─────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"[joint scenes] DONE — produced footage entries for "
          f"{len(script_text_to_footage_entries)} stage(s):")
    for script_text, entries in script_text_to_footage_entries.items():
        print(f"  '{script_text[:60]}...':")
        for entry in entries:
            for path, trim in entry.items():
                print(f"    - {Path(path).name}  trim={trim}s")
    print("=" * 70)

    return script_text_to_footage_entries

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
    scriptTextToPexelSearch: dict[str, SearchTermData] = load_json(LINE_INDEX_TO_SEARCH_TERM_FILE)
    # convert types as needed.
    for key, value in scriptTextToPexelSearch.items():
        value["search_type"] = MediaType(value["search_type"])
        value["variant"] = MediaVariant(value["variant"])
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

    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
    additional_steps_save_for_later()

    # 2.6) Generate joint composite scenes (if any) and integrate them
    #      back into final_data so the stitcher uses the new local files
    #      instead of the original pexels picks for those stages.
    joint_footage_map = generate_joint_scenes(
        script_to_search_term=scriptTextToPexelSearch,
        candidates_data=candidates_data,
    )

    if joint_footage_map:
        print("\n[main] joint scenes produced — integrating into final_data")
        final_data = _merge_joint_scenes_into_final_data(final_data, joint_footage_map)
        _add_joint_paths_to_history(joint_footage_map)

        # Persist the updated mapping so the stitcher reads the new state.
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with joint scenes → {FINAL_SCRIPT_AND_CLIPS}")

        print("\n=== FINAL SCRIPT → MEDIA (POST-JOINT-MERGE) ===")
        for entry in final_data:
            print(f"\nSCRIPT: {entry['script_text']}")
            for item in entry["footage"]:
                for path_or_url, trim in item.items():
                    label = Path(path_or_url).name if "/" in path_or_url else path_or_url
                    print(f"  ✓ {label}  (trim: {trim}s)")
        print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
    else:
        print("\n[main] no joint scenes to merge; final_data unchanged")

    additional_steps_save_for_later()

    # 3) Stitch together into the initial video.
    print("====================================================================")
    print("Stitching final video...")
    stitch_together_video( FINAL_SCRIPT_AND_CLIPS, TIMESTAMPS_ABSOLUTE_FILE, HISTORY_FILE, SCRIPT_AUDIO_FILE, OUTPUT_FILE,)
    print("done")


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
