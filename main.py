from __future__ import annotations
import hashlib

import argparse
import json
import math
import os
import gc
import random
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, TypedDict
from pathlib import Path

import nltk
import ollama
import requests
from PIL import Image
from rake_nltk import Rake
import spacy
from GET_FROM_WIKIPEDIA import get_from_wikipedia

# ===========================================================================
# IMPORTS - LOCAL
# ===========================================================================

from AUDIO_SCRIPT_SYNCHRONIZER import run as run_audio_script_synchronizer
from STOCK_FOOTAGE_REVIEW import run_media_review
from STITCH_TOGETHER import stitch_together_video
from JOINT_IMAGE_CREATOR import composite as create_joint_scene, TRANSITION_RANDOM, TRANSITION_FADE
from WORDS_ON_SCREEN import render_scene_to_video, WordRenderConfig

print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
print("running main")
print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

# ===========================================================================
# GLOBAL FLAGS
# ===========================================================================

DEBUG: bool = True
# Whether to print verbose debug lines throughout the pipeline.
# Set to False for clean production runs.

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

_CACHE_DIR   = f"{_NAME}-CACHE"  if _NAME else "CACHE"
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

# Per-scene fetched candidates (videos + images). The review GUI consumes
# this. FINAL_SCRIPT_AND_CLIPS is what the stitcher consumes and is
# written only AFTER the user has finished picking.
CANDIDATES_CACHE_FILE = f"{_CACHE_DIR}/footage_candidates.json"

LINE_INDEX_TO_SEARCH_TERM_FILE = f"{_NAME}_script_to_search_term.json" if _NAME else "script_to_search_term.json"
TIMESTAMPS_ABSOLUTE_FILE = f"{_CACHE_DIR}/{_NAME}_timestamps_absolute.json" if _NAME else f"{_CACHE_DIR}/timestamps_absolute.json"

# Optional per-word timings produced by AUDIO_SCRIPT_SYNCHRONIZER (Whisper
# word-level). If present, READ_OUT scenes use these for exact sync;
# otherwise they fall back to syllable-based estimation.
WORD_TIMINGS_FILE = f"{_CACHE_DIR}/{_NAME}_word_timings.json" if _NAME else f"{_CACHE_DIR}/word_timings.json"

# ===========================================================================
# Create all required dirs on startup
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
# READ-OUT (KINETIC TYPOGRAPHY) INTEGRATION
# ===========================================================================
# Extra seconds added to the rendered MP4 beyond the scene's actual runtime,
# so that when the stitcher trims to `line_duration` it never falls short
# due to libx264 encoder rounding. The extra footage shows the last word
# stationary, which is invisible after trim.
READ_OUT_RENDER_SAFETY_PAD_SEC: float = 0.08

# Set False to disable the kinetic typography renderer entirely.
READ_OUT_ENABLE: bool = True


# ===========================================================================
# SEARCH TERM TYPES   (FLAT SCHEMA — every "(type, variant)" is its own enum)
# ===========================================================================
#
# To add a new media type:
#   1. Add the enum value below.
#   2. (optional) Add it to NEEDS_EXTERNAL_CANDIDATES if the type fetches
#      images/videos from an external source (Pexels, Wikipedia, …) as
#      raw material.
#   3. (optional) Add it to JOINT_TYPES if it's a new joint-compositor
#      layout, and add its layout to JOINT_LAYOUT_POSITIONS.
#   4. (optional) Write a generator function returning
#      dict[script_text, list[{path: trim_seconds}]] and register it in
#      LOCAL_FOOTAGE_GENERATORS at the bottom of this file.
# ===========================================================================

class MediaType(Enum):
    STOCK       = "stock"          # Pexels videos+images, picked via review GUI
    WIKIPEDIA   = "wikipedia"      # Wikipedia images, picked via review GUI
    JOINT_3_ROW = "joint_3_row"    # 3-image collage composited locally
    READ_OUT    = "read_out"       # Kinetic typography (script text on screen)
    STICKMAN    = "stickman"       # AI-generated stickman; 2 variants → review GUI
    AI_EDIT     = "ai_edit"        # Edit the preceding AI image; N variants -> 2nd review
    STICKMAN_EXPLAIN_STOCK     = "stickman_explain_stock"      # chosen Pexels clip composited onto a board base
    STICKMAN_EXPLAIN_WIKIPEDIA = "stickman_explain_wikipedia"  # chosen Wikipedia image composited onto a board base


class SearchTermData(TypedDict):
    search_term: str
    search_type: MediaType
    position: str


# Which MediaTypes need external candidates (Pexels / Wikipedia / …) fetched
# before generation runs. Anything not in this set is produced purely from
# script text + timing data by its registered generator.
NEEDS_EXTERNAL_CANDIDATES: set[MediaType] = {
    MediaType.STOCK,
    MediaType.WIKIPEDIA,
    MediaType.JOINT_3_ROW,
    MediaType.STICKMAN_EXPLAIN_STOCK,
    MediaType.STICKMAN_EXPLAIN_WIKIPEDIA,
}

# Which MediaTypes are handled by the joint compositor. Add new joint
# layouts here AND to JOINT_LAYOUT_POSITIONS below.
JOINT_TYPES: set[MediaType] = {MediaType.JOINT_3_ROW}

# Which MediaTypes pull their candidates from Wikipedia instead of Pexels.
# Everything else in NEEDS_EXTERNAL_CANDIDATES goes via Pexels.
WIKIPEDIA_TYPES: set[MediaType] = {
    MediaType.WIKIPEDIA,
    MediaType.STICKMAN_EXPLAIN_WIKIPEDIA,
}


# ===========================================================================
# STICKMAN (AI-GENERATED) INTEGRATION
# ===========================================================================
# Stickman scenes are AI-generated locally (via fal) BEFORE the review GUI, so
# they slot into the candidate flow exactly like Pexels/Wikipedia stills: we
# generate STICKMAN_NUM_VARIANTS images per scene (same prompt) and let the
# same review GUI pick one. After selection they flow through the normal image
# pipeline (incl. Ken Burns) and into the stitcher with no special handling.

# How many candidate images to generate per stickman scene — i.e. the "2
# options" the reviewer chooses between (vs ~5 for Pexels).
STICKMAN_NUM_VARIANTS: int = 1
# STICKMAN_NUM_VARIANTS: int = 2 # TODO change back to 2 once finished testing...

# The generator reads the same search-term JSON the rest of the pipeline uses;
# it just filters rows whose search_type == "stickman".
STICKMAN_PROMPTS_FILE: str = LINE_INDEX_TO_SEARCH_TERM_FILE

# Where generated PNGs are written (cache-scoped, like joint/read-out output).
STICKMAN_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/stickman_scenes")


# AI edit scenes are generated AFTER stage-1 review (they need the chosen
# preceding image), then reviewed in a SECOND stage with its own state file.
AI_EDIT_NUM_VARIANTS: int   = 1
AI_EDIT_OUTPUT_DIR:   Path  = Path(f"{_CACHE_DIR}/ai_edit_scenes")
EDIT_CANDIDATES_CACHE_FILE  = f"{_CACHE_DIR}/edit_candidates.json"
REVIEW_EDITS_OUTPUT_FILE    = f"{_CACHE_DIR}/stock_footage/review_accepting_edits.json"

# Which MediaTypes count as a valid base for an ai_edit (walk-back eligibility).
AI_BASE_TYPES: set[MediaType] = {MediaType.STICKMAN, MediaType.AI_EDIT}


# Explainer scenes: a chosen stock/wiki clip composited onto an Einstein board
# base AFTER review. Stock-explain pulls from Pexels; wiki-explain from
# Wikipedia (already wired via NEEDS_EXTERNAL_CANDIDATES + WIKIPEDIA_TYPES above).
STICKMAN_EXPLAIN_TYPES: set[MediaType] = {
    MediaType.STICKMAN_EXPLAIN_STOCK,
    MediaType.STICKMAN_EXPLAIN_WIKIPEDIA,
}

STICKMAN_EXPLAIN_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/stickman_explain_scenes")
STICKMAN_EXPLAIN_RENDER_SAFETY_PAD_SEC: float = 0.08

# ===========================================================================
# JOINT SCENE LAYOUTS
# ===========================================================================
# TODO - consider moving this to the JOINT_IMAGE_CREATOR file

JOINT_LAYOUT_POSITIONS: dict[MediaType, list[list[int]]] = {
    MediaType.JOINT_3_ROW: [
        [25, 50],
        [50, 50],
        [75, 50],
    ],
}


# ===========================================================================
# SOUND EFFECTS / MUSIC
# ===========================================================================

SOUND_EFFECTS_DIR = Path("_SOUND_EFFECTS")
AUDIO_EVENTS_FILE = f"{_CACHE_DIR}/audio_events.json"

# Hardcoded volumes — applied to all SFX and music respectively.
SFX_VOLUME:   float = 0.3
MUSIC_VOLUME: float = 0.01   # ducked under narration

# Per-type auto-injected SFX for joint scenes. Played at "loop_start"
# (right after the transition animation finishes) on every joint stage.
# User can still override per-scene by setting `"sfx"` in the JSON.
JOINT_TYPE_SFX_MAP: dict[MediaType, dict] = {
    MediaType.JOINT_3_ROW: {
        "path":   "se-pop.mp3",
        "timing": "loop_start",
    },
}


# ===========================================================================
# DOWNLOAD CONCURRENCY + PROGRESS TRACKING
# ===========================================================================
import threading

DOWNLOAD_WORKERS: int = 12
# Parallel HTTP workers. 12 is a good default for residential broadband.
# Bump to 16-20 if you're on fast fibre; drop to 4-6 if Pexels starts
# rate-limiting (you'd see 429 errors).

# Shared HTTP session — reuses TCP/TLS connections across requests.
_http_session = requests.Session()
_http_adapter = requests.adapters.HTTPAdapter(
    pool_connections=DOWNLOAD_WORKERS,
    pool_maxsize=DOWNLOAD_WORKERS,
    max_retries=2,
)
_http_session.mount("https://", _http_adapter)
_http_session.mount("http://", _http_adapter)

# Guards history.json mutations from worker threads.
_history_lock = threading.Lock()


class ProgressTracker:
    """Thread-safe text progress indicator."""

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
    """Loads data from a cache JSON file. Returns None if the file doesn't
    exist (a normal cache miss) or can't be parsed (worth flagging)."""
    p = Path(file_path)
    if not p.exists():
        # Not an error — we just haven't generated this cache yet.
        print(f"ℹ️  [cache miss] no file yet at {file_path} — will generate it.")
        return None
    try:
        return json.loads(p.read_text())
    except Exception as exc:
        print(f"⚠️  [cache] {file_path} exists but couldn't be parsed "
              f"({exc}) — regenerating.")
        return None


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
    """

    result: dict[str, str] = {}

    return result

# ===========================================================================
# STEP 2 GET IMAGES
# ===========================================================================


# --- History helpers (cache index: url → local file path) ---

def _load_history() -> dict:
    """Load the URL→local-path cache index from disk."""
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_history(history: dict) -> None:
    """Persist the URL→local-path cache index to disk."""
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


# --- Scene timing helper ---

def _get_num_stock_images(input_script: str) -> tuple[int, float]:
    """Decide how many stock images a scene needs and how long it should run."""
    timings_path = Path(SYNCHRONIZED_SCRIPT_OUTPUT_FILE)
    timings: dict = json.loads(timings_path.read_text())

    if input_script not in timings:
        available = "\n".join(f"  - {k}" for k in timings.keys())
        raise KeyError(
            f"[_get_num_stock_images] Could not find timing for:\n"
            f"  '{input_script}'\n\n"
            f"Available keys:\n{available}"
        )

    runtime_per_scene_seconds: float = float(timings[input_script])

    num_images = max(1, math.ceil(runtime_per_scene_seconds / MAX_CLIP_SECONDS))
    max_runtime_per_clip_seconds = runtime_per_scene_seconds / num_images

    return num_images, max_runtime_per_clip_seconds


# ---------------------------------------------------------------------------
# Pexels metadata + download helpers
# ---------------------------------------------------------------------------

def _get_video_metadata(search_term: str, max_results: int = 10, page: int = 1) -> list[tuple[str, float]]:
    """Hit Pexels Videos API, return (url, duration) pairs — NO downloading yet."""
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

def _get_image_metadata(search_term: str, max_results: int = 5,
                       page: int = 1) -> list[str]:
    """Hit Pexels Images API, return URLs only — NO downloading."""
    try:
        resp = _http_session.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": search_term, "per_page": max_results,
                    "orientation": "landscape", "page": page},
            timeout=8,
        )
    except Exception as exc:
        print(f"  [image meta] API error: {exc}")
        return []

    if resp.status_code != 200:
        print(f"  [image meta] API error {resp.status_code} for '{search_term}'")
        return []

    urls: list[str] = []
    for p in resp.json().get("photos", []) or []:
        url = (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large")
        if url:
            urls.append(url)
    return urls


def _download_clip(url: str) -> str | None:
    """Download a single video to cache. Returns local path, or None on failure."""
    history = _load_history()
    if url in history and Path(history[url]).exists():
        print(f"  [cache hit] {Path(history[url]).name}")
        return history[url]

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
        for chunk in vid_resp.iter_content(chunk_size=65536):
            f.write(chunk)

    history[url] = str(dest)
    _save_history(history)
    print(f"  [download] done → {dest.name}")
    return str(dest)


def _download_image(url: str) -> str | None:
    """Download a single image to cache. Returns local path, or None on failure."""
    history = _load_history()
    if url in history and Path(history[url]).exists():
        print(f"  [cache hit img] {Path(history[url]).name}")
        return history[url]

    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
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


def _download_clip_parallel(url: str) -> str | None:
    """Thread-safe, silent version of _download_clip using the shared session."""
    with _history_lock:
        history = _load_history()
        if url in history and Path(history[url]).exists():
            return history[url]

    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    filename = f"pexels-{url_hash}.mp4"
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STOCK_FOOTAGE_CACHE_DIR / filename

    try:
        vid_resp = _http_session.get(url, stream=True, timeout=30)
        if vid_resp.status_code != 200:
            return None
        with open(dest, "wb") as f:
            for chunk in vid_resp.iter_content(chunk_size=65536):
                f.write(chunk)
    except Exception:
        return None

    with _history_lock:
        history = _load_history()
        history[url] = str(dest)
        _save_history(history)

    return str(dest)


def _download_image_parallel(url: str) -> str | None:
    """Thread-safe, silent version of _download_image using the shared session."""
    with _history_lock:
        history = _load_history()
        if url in history and Path(history[url]).exists():
            return history[url]

    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    ext = ".jpg"
    for cand_ext in (".jpg", ".jpeg", ".png", ".webp"):
        if cand_ext in url.lower().split("?")[0]:
            ext = cand_ext
            break
    filename = f"pexels-img-{url_hash}{ext}"
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STOCK_FOOTAGE_CACHE_DIR / filename

    try:
        img_resp = _http_session.get(url, stream=True, timeout=15)
        if img_resp.status_code != 200:
            return None
        with open(dest, "wb") as f:
            for chunk in img_resp.iter_content(chunk_size=65536):
                f.write(chunk)
    except Exception:
        return None

    with _history_lock:
        history = _load_history()
        history[url] = str(dest)
        _save_history(history)

    return str(dest)


def _download_wikipedia_image_parallel(url: str) -> str | None:
    """Thread-safe Wikipedia image downloader."""
    with _history_lock:
        history = _load_history()
        if url in history and Path(history[url]).exists():
            return history[url]

    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

    ext = ".jpg"
    lower = url.lower()
    for cand_ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if lower.endswith(cand_ext):
            ext = cand_ext
            break

    filename = f"wiki-img-{url_hash}{ext}"
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STOCK_FOOTAGE_CACHE_DIR / filename

    try:
        resp = _http_session.get(
            url,
            stream=True,
            timeout=20,
            headers={"User-Agent": "VideoGenerationPipeline/1.0 (local)"},
        )
        if resp.status_code != 200:
            print(f"  [wiki download] FAILED {resp.status_code} for {url}")
            return None
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
    except Exception as exc:
        print(f"  [wiki download] FAILED: {exc}")
        return None

    with _history_lock:
        history = _load_history()
        history[url] = str(dest)
        _save_history(history)

    return str(dest)


# ---------------------------------------------------------------------------
# Joint scene timing helpers
# ---------------------------------------------------------------------------

def _load_scene_timings() -> dict[str, float]:
    """Return {script_text → runtime_seconds} from the audio-sync output."""
    p = Path(SYNCHRONIZED_SCRIPT_OUTPUT_FILE)
    print(f"\n[timings] loading scene timings from {p}")
    if not p.exists():
        print(f"[timings] FATAL: timings file missing: {p}")
        print( "[timings]   (did run_audio_script_synchronizer run?)")
        sys.exit(1)
    timings = {k: float(v) for k, v in json.loads(p.read_text()).items()}
    print(f"[timings] loaded {len(timings)} timing entries")
    return timings


def _compute_joint_stage_timing(
    script_text: str,
    scene_timings: dict[str, float],
) -> dict:
    """Decide how to split a single joint stage's runtime between intro and loop."""
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
        use_path = loop_path if loop_path.exists() else intro_path
        entries.append({str(use_path): round(timing["total_duration"], 3)})
        print(f"[joint:footage]   → static entry: {use_path.name}  "
              f"trim={timing['total_duration']:.3f}s  (no transition: scene too short)")

    print(f"[joint:footage]   total entries for this stage: {len(entries)}")
    return entries


# ===========================================================================
# GENERIC FOOTAGE-MERGE HELPERS
# ===========================================================================
# Used by every local-file generator (joint, read-out, future types). They
# don't care which generator produced the entries — they just integrate any
# script_text → footage map into the master final_data list and history.

def _merge_generated_footage_into_final_data(
    final_data: list[dict],
    generated_footage_map: dict[str, list[dict]],
    source_label: str = "generated",
) -> list[dict]:
    """
    Replace the `footage` list in `final_data` for any script_text that the
    generator produced output for. Entries not already in final_data are
    appended at the end.
    """
    print("\n" + "=" * 70)
    print(f"[merge:{source_label}] merging {len(generated_footage_map)} entry(ies) "
          f"into final_data")
    print(f"[merge:{source_label}] final_data currently has "
          f"{len(final_data)} entry(ies)")
    print("=" * 70)

    by_script = {entry["script_text"]: i for i, entry in enumerate(final_data)}

    replaced = 0
    appended = 0

    for script_text, entries in generated_footage_map.items():
        if script_text in by_script:
            idx = by_script[script_text]
            old_count = len(final_data[idx].get("footage", []))
            final_data[idx]["footage"] = entries
            replaced += 1
            print(f"[merge:{source_label}] REPLACED '{script_text[:60]}...'  "
                  f"(was {old_count} entry(ies), now {len(entries)})")
            for e in entries:
                for path, trim in e.items():
                    print(f"[merge:{source_label}]     {Path(path).name}  trim={trim}s")
        else:
            final_data.append({"script_text": script_text, "footage": entries})
            appended += 1
            print(f"[merge:{source_label}] APPENDED '{script_text[:60]}...'  "
                  f"({len(entries)} entry(ies))")
            for e in entries:
                for path, trim in e.items():
                    print(f"[merge:{source_label}]     {Path(path).name}  trim={trim}s")

    print(f"\n[merge:{source_label}] done — replaced={replaced}, appended={appended}, "
          f"final_data size now {len(final_data)}")
    return final_data


def _add_local_paths_to_history(generated_footage_map: dict[str, list[dict]]) -> None:
    """
    The stitcher's history.json maps {url → local_path}. For locally-generated
    files (joint scenes, read-out scenes, future types) we add identity
    entries (path → path) so the same lookup mechanism resolves them with no
    stitcher changes.
    """
    history = _load_history()
    print(f"\n[history] augmenting history.json "
          f"(currently {len(history)} entries)")

    added = 0
    skipped = 0
    for entries in generated_footage_map.values():
        for entry in entries:
            for path in entry:
                if path in history:
                    skipped += 1
                    continue
                history[path] = path
                added += 1
                print(f"[history]   + identity entry for {Path(path).name}")

    _save_history(history)
    print(f"[history] done — added={added}, already_present={skipped}, "
          f"history now has {len(history)} entries")


# ===========================================================================
# EXTERNAL CANDIDATE FETCHING (Pexels + Wikipedia)
# ===========================================================================

def load_stock_footage(all_scenes: dict) -> list[dict]:
    """
    Two phases:
      A) gather metadata in parallel across scenes
      B) download every candidate file in parallel with one progress bar

    Only fetches candidates for scenes whose search_type is in
    NEEDS_EXTERNAL_CANDIDATES. Within that set, types in WIKIPEDIA_TYPES
    pull from Wikipedia; everything else goes via Pexels.

    Returns:
        [{"script_text", "candidates": {"videos": [...], "images": [...]},
          "num_clips_needed", "max_runtime_per_clip_seconds"}, ...]
    """
    eligible: dict = {}
    skipped_by_type: dict[str, int] = {}
    for k, v in all_scenes.items():
        st = v.get("search_type")
        if st in NEEDS_EXTERNAL_CANDIDATES:
            eligible[k] = v
        else:
            type_name = st.value if hasattr(st, "value") else str(st)
            skipped_by_type[type_name] = skipped_by_type.get(type_name, 0) + 1

    scene_items = list(eligible.items())
    print(f"\n[fetch] Phase A: gathering metadata for {len(scene_items)} scene(s) "
          f"in parallel...")
    if skipped_by_type:
        skipped_summary = ", ".join(f"{n} {t}" for t, n in skipped_by_type.items())
        print(f"[fetch]   (skipped {skipped_summary} — produced by local generators)")

    def fetch_meta_for_scene(idx_and_scene):
        idx, (script_text, scene_data) = idx_and_scene
        search_term = scene_data["search_term"]
        search_type = scene_data["search_type"]
        num_clips, max_runtime = _get_num_stock_images(script_text)

        # Explainer scenes feature ONE chosen clip on the board for the whole
        # scene, regardless of length — collapse the multi-clip split so the
        # review GUI asks for a single pick spanning the full scene.
        if search_type in STICKMAN_EXPLAIN_TYPES:
            max_runtime = num_clips * max_runtime   # == full scene runtime
            num_clips = 1

        print(f"\n[fetch:meta] scene[{idx}] '{script_text[:50]}...'")
        print(f"[fetch:meta]   search='{search_term}', type={search_type.value}")

        # ── WIKIPEDIA path ───────────────────────────────────────────
        if search_type in WIKIPEDIA_TYPES:
            print(f"[fetch:meta]   → using WIKIPEDIA source")
            wiki_urls = get_from_wikipedia(search_term, max_images=5)
            print(f"[fetch:meta]   wikipedia returned {len(wiki_urls)} URL(s)")
            return (idx, script_text, num_clips, max_runtime,
                    [], [], wiki_urls)

        # ── PEXELS path ──────────────────────────────────────────────
        print(f"[fetch:meta]   → using PEXELS source")

        video_meta: list[tuple[str, float]] = []
        seen: set[str] = set()
        for page in range(1, 4):
            if len(video_meta) >= 2:
                break
            for url, dur in _get_video_metadata(search_term, max_results=10, page=page):
                if url in seen or dur <= 0:
                    continue
                seen.add(url)
                video_meta.append((url, dur))
                if len(video_meta) >= 2:
                    break

        image_urls: list[str] = []
        seen_img: set[str] = set()
        for url in _get_image_metadata(search_term, max_results=5, page=1):
            if url in seen_img:
                continue
            seen_img.add(url)
            image_urls.append(url)
            if len(image_urls) >= 3:
                break

        return (idx, script_text, num_clips, max_runtime,
                video_meta, image_urls, [])

    out: list[dict] = [None] * len(scene_items)  # type: ignore[list-item]
    all_tasks: list[tuple] = []
    # task = (scene_idx, kind, url, trim_seconds)
    # kind is one of: "videos", "images", "wiki_images"

    if not scene_items:
        print(f"[fetch] no eligible scenes — returning empty candidates list")
        return []

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        for result in ex.map(fetch_meta_for_scene, enumerate(scene_items)):
            (idx, script_text, num_clips, max_runtime,
             video_meta, pexels_img_urls, wiki_img_urls) = result

            out[idx] = {
                "script_text":                  script_text,
                "candidates":                   {"videos": [], "images": []},
                "num_clips_needed":             num_clips,
                "max_runtime_per_clip_seconds": max_runtime,
            }

            for url, dur in video_meta:
                trim = min(dur, max_runtime)
                all_tasks.append((idx, "videos", url, round(trim, 2)))

            for url in pexels_img_urls:
                all_tasks.append((idx, "images", url, round(float(max_runtime), 2)))

            for url in wiki_img_urls:
                all_tasks.append((idx, "wiki_images", url,
                                  round(float(max_runtime), 2)))

    print(f"[fetch] Phase A done — {len(all_tasks)} files queued.")

    if not all_tasks:
        return out

    # ── Phase B: parallel download with progress bar ──────────────────
    print(f"[fetch] Phase B: downloading {len(all_tasks)} files "
          f"with {DOWNLOAD_WORKERS} workers...")
    tracker = ProgressTracker(total=len(all_tasks), label="DOWNLOADING")

    def download_one(task):
        scene_idx, kind, url, trim = task
        if kind == "videos":
            local = _download_clip_parallel(url)
        elif kind == "wiki_images":
            local = _download_wikipedia_image_parallel(url)
        else:
            local = _download_image_parallel(url)
        tracker.tick()
        return scene_idx, kind, url, trim, local

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        for scene_idx, kind, url, trim, local in ex.map(download_one, all_tasks):
            if local is None:
                continue
            bucket = "videos" if kind == "videos" else "images"
            out[scene_idx]["candidates"][bucket].append({url: trim})

    tracker.finish()
    print(f"[fetch] Phase B done.")
    return out




# ===========================================================================
# STICKMAN CANDIDATE GENERATION (AI-generated, reviewed like stock stills)
# ===========================================================================

def generate_stickman_candidates(
    script_to_search_term: dict[str, SearchTermData],
) -> list[dict]:
    """
    For every scene with search_type == MediaType.STICKMAN, generate
    STICKMAN_NUM_VARIANTS AI images (same prompt) and return candidate bundles
    in the SAME shape load_stock_footage() returns, so they can be appended to
    candidates_data and reviewed by the same GUI (which will show N options).

    Generated PNGs are keyed in the candidates dict by their local path, and an
    identity entry (path -> path) is added to history.json so the review GUI
    and the stitcher resolve them exactly like downloaded media.

    Returns [] (and does no work) if there are no stickman scenes.
    """
    stickman_scenes = {
        txt: data for txt, data in script_to_search_term.items()
        if data["search_type"] == MediaType.STICKMAN
    }

    print("\n" + "=" * 70)
    print(f"[stickman] {len(stickman_scenes)} stickman scene(s) found")
    print("=" * 70)

    if not stickman_scenes:
        print("[stickman] nothing to generate — skipping")
        return []

    # Lazy import keeps the fal / dotenv dependency out of pipeline runs that
    # don't use stickman scenes.
    from ai_generate_stickman_images import generate_stickman_images

    print(f"[stickman] generating {STICKMAN_NUM_VARIANTS} variant(s) per scene "
          f"→ {STICKMAN_OUTPUT_DIR}")
    generated = generate_stickman_images(
        prompts_file=STICKMAN_PROMPTS_FILE,
        out_dir=STICKMAN_OUTPUT_DIR,
        num_variants=STICKMAN_NUM_VARIANTS,
    )
    # generated: { script_text: [path, path, ...] }
    print(f"[stickman] generator returned images for {len(generated)} scene(s)")

    scene_timings = _load_scene_timings()

    bundles: list[dict] = []
    for script_text in stickman_scenes:
        image_paths = generated.get(script_text)
        if not image_paths:  # stripped-key fallback, mirroring the joint path
            for k, v in generated.items():
                if k.strip() == script_text.strip():
                    image_paths = v
                    break

        if not image_paths:
            print(f"[stickman] WARNING: no images for '{script_text[:60]}' — "
                  f"the review GUI will have no options for this scene")
            continue

        image_paths = [p for p in image_paths if Path(p).exists()]
        if not image_paths:
            print(f"[stickman] WARNING: generated paths missing on disk for "
                  f"'{script_text[:60]}' — skipping")
            continue

        if script_text not in scene_timings:
            print(f"[stickman] FATAL: no timing for '{script_text[:60]}'")
            sys.exit(1)
        duration = round(float(scene_timings[script_text]), 3)

        # One stickman image fills the whole scene; offer every variant as a
        # choice. num_clips_needed = 1 — the reviewer picks a single image.
        image_candidates = [{p: duration} for p in image_paths]

        bundles.append({
            "script_text":                  script_text,
            "candidates":                   {"videos": [], "images": image_candidates},
            "num_clips_needed":             1,
            "max_runtime_per_clip_seconds": duration,
        })
        print(f"[stickman]   '{script_text[:50]}' → {len(image_candidates)} "
              f"option(s), {duration:.2f}s each")

    # Register identity entries so the url→local lookup resolves these PNGs.
    history = _load_history()
    added = 0
    for bundle in bundles:
        for cand in bundle["candidates"]["images"]:
            for path in cand:
                if path not in history:
                    history[path] = path
                    added += 1
    _save_history(history)
    print(f"[stickman] added {added} identity entry(ies) to history.json")

    print(f"[stickman] DONE — {len(bundles)} candidate bundle(s)")
    return bundles



def build_ai_edit_candidates(
    script_to_search_term: dict[str, SearchTermData],
    stage1_final_data: list[dict],
) -> list[dict]:
    """
    After stage-1 review, generate ai_edit images and return candidate bundles
    in the SAME shape as load_stock_footage(), for a 2nd review stage.
    Returns [] if there are no ai_edit scenes.
    """
    has_edits = any(
        d["search_type"] == MediaType.AI_EDIT
        for d in script_to_search_term.values()
    )
    if not has_edits:
        print("[ai_edit] no ai_edit scenes - skipping stage 2")
        return []

    from ai_edit import generate_ai_edits

    # Stage-1 chosen image per scene (first footage entry's path/url -> local).
    chosen_by_text: dict[str, str | None] = {}
    for entry in stage1_final_data:
        footage = entry.get("footage") or []
        if not footage:
            chosen_by_text[entry["script_text"]] = None
            continue
        key = next(iter(footage[0]), None)  # url or local path
        local = _resolve_to_local_path(key) if key else None
        chosen_by_text[entry["script_text"]] = local

    # Ordered scene descriptors for the resolver (script order = dict order).
    ordered_scenes = []
    for text, data in script_to_search_term.items():
        st = data["search_type"]
        ordered_scenes.append({
            "script_text": text,
            "is_edit":     st == MediaType.AI_EDIT,
            "is_ai_base":  st in AI_BASE_TYPES,
            "instruction": data["search_term"],
            "chosen_image": chosen_by_text.get(text),
        })

    generated = generate_ai_edits(
        ordered_scenes, out_dir=AI_EDIT_OUTPUT_DIR,
        num_variants=AI_EDIT_NUM_VARIANTS,
    )  # { edit_text: [path, ...] }

    scene_timings = _load_scene_timings()
    bundles: list[dict] = []
    history = _load_history()
    for text, data in script_to_search_term.items():
        if data["search_type"] != MediaType.AI_EDIT:
            continue
        paths = [p for p in generated.get(text, []) if Path(p).exists()]
        if not paths:
            print(f"[ai_edit] WARNING: no images for '{text[:50]}'")
            continue
        if text not in scene_timings:
            print(f"[ai_edit] FATAL: no timing for '{text[:50]}'")
            sys.exit(1)
        dur = round(float(scene_timings[text]), 3)
        bundles.append({
            "script_text": text,
            "candidates": {"videos": [], "images": [{p: dur} for p in paths]},
            "num_clips_needed": 1,
            "max_runtime_per_clip_seconds": dur,
        })
        for p in paths:                 # identity entries so lookups resolve
            history.setdefault(p, p)
    _save_history(history)

    print(f"[ai_edit] built {len(bundles)} edit candidate bundle(s)")
    return bundles


# ===========================================================================
# GENERATOR: JOINT SCENES
# ===========================================================================

def generate_joint_scenes(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],
) -> dict[str, list[dict]]:
    """
    Build joint composite scenes for any scene whose search_type is in
    JOINT_TYPES, and return a stitcher-ready map of:

        { script_text: [ {local_path: trim_seconds}, ... ], ... }

    Each joint stage typically contributes TWO entries (intro + loop). Very
    short scenes get just one entry — the loop file alone.

    Adjacent scenes are grouped if they share the SAME joint search_type
    AND have contiguous positions. Each group becomes one composite render.
    """

    print("\n" + "=" * 70)
    print("[joint scenes] STARTING generate_joint_scenes")
    print(f"[joint scenes] script_to_search_term has {len(script_to_search_term)} entries")
    print(f"[joint scenes] candidates_data has {len(candidates_data)} entries")
    print(f"[joint scenes] joint types registered: "
          f"{[t.value for t in JOINT_TYPES]}")
    print("=" * 70)

    scene_timings = _load_scene_timings()

    candidates_by_text: dict[str, dict] = {c["script_text"]: c for c in candidates_data}
    candidates_by_stripped: dict[str, dict] = {c["script_text"].strip(): c for c in candidates_data}
    print(f"[joint scenes] candidates lookup built with {len(candidates_by_text)} entries")

    # 1) Locate all scenes whose search_type is a joint type.
    joint_scenes: list[tuple[str, SearchTermData]] = []
    for script_text, scene_data in script_to_search_term.items():
        if scene_data["search_type"] not in JOINT_TYPES:
            continue
        joint_scenes.append((script_text, scene_data))

    print(f"\n[joint scenes] found {len(joint_scenes)} joint scene(s)")
    if not joint_scenes:
        print("[joint scenes] no joint scenes — returning empty map")
        return {}

    joint_scenes.sort(key=lambda scene: int(scene[1]["position"]))
    for i, (txt, data) in enumerate(joint_scenes):
        print(f"[joint scenes]   sorted[{i}]: pos={data['position']}, "
              f"type={data['search_type'].value}, script='{txt[:60]}...'")

    # 2) Group consecutive joints by (same search_type + contiguous position).
    grouped_joint_scenes: list[list[tuple[str, SearchTermData]]] = []
    current_group: list[tuple[str, SearchTermData]] = []
    previous_scene_data = None

    for script_text, scene_data in joint_scenes:
        if not previous_scene_data:
            current_group.append((script_text, scene_data))
            previous_scene_data = scene_data
            continue

        same_type     = scene_data["search_type"] == previous_scene_data["search_type"]
        next_position = int(scene_data["position"]) == int(previous_scene_data["position"]) + 1

        if same_type and next_position:
            current_group.append((script_text, scene_data))
        else:
            grouped_joint_scenes.append(current_group)
            current_group = [(script_text, scene_data)]

        previous_scene_data = scene_data

    if current_group:
        grouped_joint_scenes.append(current_group)

    print(f"\n[joint scenes] formed {len(grouped_joint_scenes)} group(s)")
    for gi, grp in enumerate(grouped_joint_scenes):
        positions = [s[1]["position"] for s in grp]
        joint_type = grp[0][1]["search_type"]
        print(f"[joint scenes]   group {gi}: type={joint_type.value}, "
              f"positions={positions}, size={len(grp)}")

    # 3) Generate each group + collect footage entries.
    script_text_to_footage_entries: dict[str, list[dict]] = {}

    for group_index, group in enumerate(grouped_joint_scenes):
        joint_type = group[0][1]["search_type"]
        print(f"\n[joint scenes] processing group {group_index}: "
              f"type={joint_type.value}, size={len(group)}")

        # Look up layout for this joint type.
        layout_positions = JOINT_LAYOUT_POSITIONS.get(joint_type)
        if not layout_positions:
            print(f"[joint scenes] FATAL: no layout registered for "
                  f"{joint_type.value} in JOINT_LAYOUT_POSITIONS")
            sys.exit(1)

        # Per-joint-type rendering config. Add a `case` here when adding a
        # new joint layout (and an entry in JOINT_LAYOUT_POSITIONS).
        match joint_type:
            case MediaType.JOINT_3_ROW:
                box_percentage   = 28
                transition       = TRANSITION_RANDOM
                background_path  = "_BACKGROUNDS/bg_crumpled_card.mp4"
                base_duration    = JOINT_BASE_DURATION_FALLBACK_SEC
                remove_bg        = True

            case _:
                print(f"[joint scenes] FATAL: unsupported joint type: {joint_type}")
                sys.exit(1)

        stage_timings = [
            _compute_joint_stage_timing(script_text, scene_timings)
            for script_text, _ in group
        ]

        max_loop_duration = max(
            (t["loop_duration"] for t in stage_timings if t["use_transition"]),
            default=0.0,
        )
        max_static_duration = max(
            (t["total_duration"] for t in stage_timings if not t["use_transition"]),
            default=0.0,
        )
        composite_duration = max(max_loop_duration, max_static_duration, base_duration)
        print(f"[joint scenes:timing] composite_duration = {composite_duration:.3f}s")

        items = []
        for item_index, (script_text, _) in enumerate(group):
            if item_index >= len(layout_positions):
                print(f"[joint scenes] FATAL: item_index {item_index} >= layout length {len(layout_positions)}")
                sys.exit(1)

            matching_candidate = candidates_by_text.get(script_text)
            if not matching_candidate:
                matching_candidate = candidates_by_stripped.get(script_text.strip())

            if not matching_candidate:
                print(f"[joint scenes] FATAL: no matching candidate for: '{script_text}'")
                print(f"  HINT: delete {CANDIDATES_CACHE_FILE} and re-run to refresh.")
                sys.exit(1)

            image_candidates = matching_candidate.get("candidates", {}).get("images", [])
            if not image_candidates:
                print(f"[joint scenes] FATAL: no image candidates for: '{script_text}'")
                sys.exit(1)

            first_image = image_candidates[0]
            image_url = next(iter(first_image), "")
            if not image_url:
                print(f"[joint scenes] FATAL: no image_url extracted from candidate")
                sys.exit(1)

            history = _load_history()
            local_path = history.get(image_url)
            if not (local_path and Path(local_path).exists()):
                local_path = _download_image(image_url)
                if not local_path:
                    print(f"[joint scenes] FATAL: failed to download image: {image_url}")
                    sys.exit(1)

            items.append({
                "path":                       local_path,
                "position":                   layout_positions[item_index],
                "scale-fit-box-percentage":   box_percentage,
                "transition":                 transition,
                "removeBG":                   remove_bg,
            })

        if not items:
            print(f"[joint scenes] FATAL: no items to composite for group {group_index}")
            sys.exit(1)

        output_folder = Path(_CACHE_DIR) / "joint_scenes" / f"group_{group_index}"
        output_folder.mkdir(parents=True, exist_ok=True)

        create_joint_scene(
            items=items,
            output_folder=str(output_folder),
            composite_flag=True,
            background_path=background_path,
            duration=composite_duration,
        )
        print(f"[joint scenes] ✓ generated group {group_index}")

        num_stages = len(group)
        for stage_index, (script_text, _) in enumerate(group):
            timing = stage_timings[stage_index]
            entries = _build_footage_entries_for_stage(
                group_output_folder=output_folder,
                stage_index=stage_index,
                num_stages=num_stages,
                timing=timing,
            )
            script_text_to_footage_entries[script_text] = entries

    print("\n" + "=" * 70)
    print(f"[joint scenes] DONE — produced footage entries for "
          f"{len(script_text_to_footage_entries)} stage(s)")
    print("=" * 70)
    return script_text_to_footage_entries


# ===========================================================================
# GENERATOR: READ-OUT (KINETIC TYPOGRAPHY) SCENES
# ===========================================================================

def generate_read_out_scenes(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],   # unused — kept for registry signature uniformity
) -> dict[str, list[dict]]:
    """
    Render a silent kinetic-typography MP4 for every scene flagged
    MediaType.READ_OUT, and return a stitcher-ready map of:

        { script_text: [ {local_path: trim_seconds} ], ... }

    Each read-out scene produces ONE entry — a single MP4 rendered slightly
    longer than the scene's runtime (for safe trimming) but reported to the
    stitcher with `trim_seconds = scene_runtime` so the cut lands cleanly.

    The rendered MP4 is silent — the stitcher overlays the global narration
    audio across all scenes.
    """
    print("\n" + "=" * 70)
    print("[read-out scenes] STARTING generate_read_out_scenes")
    print("=" * 70)

    if not READ_OUT_ENABLE:
        print("[read-out scenes] READ_OUT_ENABLE is False — skipping")
        return {}

    read_outs = [
        (txt, data) for txt, data in script_to_search_term.items()
        if data["search_type"] == MediaType.READ_OUT
    ]

    if not read_outs:
        print("[read-out scenes] no read-out scenes — returning empty map")
        return {}

    print(f"[read-out scenes] found {len(read_outs)} read-out scene(s)")

    # Inputs we need from the audio-sync stage.
    scene_timings = _load_scene_timings()                 # text → duration
    line_starts   = load_json(TIMESTAMPS_ABSOLUTE_FILE)   # text → abs start

    # Optional precise per-word timings (Whisper word-level).
    precise: dict | None = None
    if Path(WORD_TIMINGS_FILE).exists():
        try:
            precise = json.loads(Path(WORD_TIMINGS_FILE).read_text())
            n_covered = sum(1 for txt, _ in read_outs if precise.get(txt))
            print(f"[read-out scenes] loaded precise word timings "
                  f"({n_covered}/{len(read_outs)} read-out lines covered)")
        except Exception as exc:
            print(f"[read-out scenes] couldn't parse {WORD_TIMINGS_FILE}: {exc}")
            precise = None
    else:
        print(f"[read-out scenes] no {WORD_TIMINGS_FILE} — using syllable estimation")

    # One config shared by every read-out scene.
    cfg = WordRenderConfig()

    output_dir = Path(_CACHE_DIR) / "read_out_scenes"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[read-out scenes] output dir: {output_dir}")

    footage_map: dict[str, list[dict]] = {}

    for idx, (script_text, scene_data) in enumerate(read_outs):
        if script_text not in scene_timings:
            print(f"[read-out scenes] FATAL: no timing for '{script_text[:80]}'")
            sys.exit(1)

        duration = float(scene_timings[script_text])
        if duration <= 0:
            print(f"[read-out scenes] WARNING: scene has zero/negative duration "
                  f"({duration}s) — skipping '{script_text[:60]}'")
            continue

        line_start = float(line_starts.get(script_text, 0.0))
        per_line_words = (precise or {}).get(script_text)

        # Build a safe filename. Strip non-alphanumerics, prefix with idx.
        safe_stem = re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:50] or "scene"
        output_path = str(output_dir / f"read_out_{idx:03d}_{safe_stem}.mp4")

        # Render slightly longer than the scene runtime so the stitcher's
        # trim never falls short due to libx264 keyframe alignment. The
        # extra footage (last word stationary) is invisible after trim.
        render_duration = duration + READ_OUT_RENDER_SAFETY_PAD_SEC

        print(f"\n[read-out scenes] [{idx + 1}/{len(read_outs)}] "
              f"'{script_text[:60]}{'...' if len(script_text) > 60 else ''}'")
        print(f"[read-out scenes]   scene duration   = {duration:.3f}s")
        print(f"[read-out scenes]   render duration  = {render_duration:.3f}s "
              f"(+{READ_OUT_RENDER_SAFETY_PAD_SEC:.3f}s safety pad)")
        print(f"[read-out scenes]   line_start (abs) = {line_start:.3f}s")
        print(f"[read-out scenes]   precise words    = "
              f"{'yes (' + str(len(per_line_words)) + ' words)' if per_line_words else 'no'}")
        print(f"[read-out scenes]   → {output_path}")

        try:
            render_scene_to_video(
                script_text=script_text,
                line_duration=render_duration,
                output_path=output_path,
                precise_word_timings=per_line_words,
                line_start_absolute=line_start,
                config=cfg,
            )
        except Exception as exc:
            print(f"[read-out scenes] FATAL: render failed: {exc}")
            sys.exit(1)

        # Report the ORIGINAL scene duration as trim. The extra pad gets
        # discarded by the stitcher.
        footage_map[script_text] = [{output_path: round(duration, 3)}]
        print(f"[read-out scenes]   ✓ done — stitcher trim = {round(duration, 3)}s")

    print("\n" + "=" * 70)
    print(f"[read-out scenes] DONE — produced {len(footage_map)} read-out scene(s)")
    print("=" * 70)
    return footage_map


def generate_stickman_explain_scenes(
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
) -> dict[str, list[dict]]:
    """
    For every scene whose search_type is in STICKMAN_EXPLAIN_TYPES, composite
    the clip the user CHOSE in review (stored in final_data) onto a randomly
    selected Einstein board base, and return a stitcher-ready map:

        { script_text: [ {local_mp4_path: trim_seconds} ], ... }

    Always renders an MP4 (even when the chosen footage is a still) so the
    board stays static under the later Ken Burns pass — Ken Burns only touches
    image entries, and these are already video.

    NOT in LOCAL_FOOTAGE_GENERATORS because it needs the post-review picks
    (final_data), not the raw candidates the registry generators receive.
    """
    print("\n" + "=" * 70)
    print("[explain scenes] STARTING generate_stickman_explain_scenes")
    print("=" * 70)

    explain_scenes = [
        (txt, data) for txt, data in script_to_search_term.items()
        if data["search_type"] in STICKMAN_EXPLAIN_TYPES
    ]
    if not explain_scenes:
        print("[explain scenes] no explainer scenes — returning empty map")
        return {}

    print(f"[explain scenes] found {len(explain_scenes)} explainer scene(s)")

    # Lazy import keeps the PIL/ffmpeg-only dep out of runs that don't use it.
    from MAKE_EXPLAINER_IMAGE import make_explainer

    scene_timings = _load_scene_timings()

    # Map script_text -> local path of the clip chosen in stage-1 review.
    chosen_by_text: dict[str, str | None] = {}
    for entry in final_data:
        footage = entry.get("footage") or []
        if not footage:
            chosen_by_text[entry["script_text"]] = None
            continue
        key = next(iter(footage[0]), None)             # url or local path
        chosen_by_text[entry["script_text"]] = (
            _resolve_to_local_path(key) if key else None
        )

    out_dir = STICKMAN_EXPLAIN_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[explain scenes] output dir: {out_dir}")

    footage_map: dict[str, list[dict]] = {}

    for idx, (script_text, _) in enumerate(explain_scenes):
        chosen = chosen_by_text.get(script_text)
        if not chosen:
            print(f"[explain scenes] FATAL: no chosen footage in final_data for "
                  f"'{script_text[:70]}' — was it picked in review?")
            sys.exit(1)

        if script_text not in scene_timings:
            print(f"[explain scenes] FATAL: no timing for '{script_text[:70]}'")
            sys.exit(1)

        duration = float(scene_timings[script_text])
        if duration <= 0:
            print(f"[explain scenes] WARNING: zero/negative duration "
                  f"({duration}s) — skipping '{script_text[:60]}'")
            continue

        render_duration = duration + STICKMAN_EXPLAIN_RENDER_SAFETY_PAD_SEC

        safe_stem = re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:50] or "scene"
        output_path = str(out_dir / f"explain_{idx:03d}_{safe_stem}.mp4")

        print(f"\n[explain scenes] [{idx + 1}/{len(explain_scenes)}] "
              f"'{script_text[:60]}{'...' if len(script_text) > 60 else ''}'")
        print(f"[explain scenes]   base footage   = {chosen}")
        print(f"[explain scenes]   scene duration = {duration:.3f}s")
        print(f"[explain scenes]   render dur     = {render_duration:.3f}s")

        try:
            make_explainer(
                media_path=chosen,
                output_path=output_path,
                duration=render_duration,
            )
        except Exception as exc:
            print(f"[explain scenes] FATAL: explainer render failed: {exc}")
            sys.exit(1)

        # Report the ORIGINAL scene duration as trim; the safety pad is trimmed off.
        footage_map[script_text] = [{output_path: round(duration, 3)}]
        print(f"[explain scenes]   ✓ done — stitcher trim = {round(duration, 3)}s")

    print("\n" + "=" * 70)
    print(f"[explain scenes] DONE — produced {len(footage_map)} explainer scene(s)")
    print("=" * 70)
    return footage_map


# ===========================================================================
# GENERATOR REGISTRY
# ===========================================================================
# Every entry here is a "local file generator": given the script and (for
# types that use them) external candidates, it writes one or more MP4/image
# files to disk and returns a {script_text → [{path: trim_seconds}, ...]}
# map. The merge helpers above are completely generator-agnostic — they
# integrate any such map into final_data + history.json identically.
#
# Note: a single generator can handle multiple MediaTypes — generate_joint_scenes
# already does, dispatching internally based on which types are in JOINT_TYPES.

LOCAL_FOOTAGE_GENERATORS: dict[str, Callable[
    [dict[str, SearchTermData], list[dict]],
    dict[str, list[dict]],
]] = {
    "joint":    generate_joint_scenes,    # handles every type in JOINT_TYPES
    "read_out": generate_read_out_scenes, # handles MediaType.READ_OUT
}


def run_all_local_generators(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],
) -> dict[str, list[dict]]:
    """
    Invoke every registered generator and merge their outputs into a single
    {script_text → footage_entries} map.

    Generators are run in registry order. If two generators produce entries
    for the same script_text (shouldn't happen with sensible config), the
    later one wins and a warning is printed.
    """
    print("\n" + "=" * 70)
    print(f"[generators] running {len(LOCAL_FOOTAGE_GENERATORS)} local generator(s)")
    print("=" * 70)

    combined: dict[str, list[dict]] = {}
    for name, generator in LOCAL_FOOTAGE_GENERATORS.items():
        print(f"\n[generators] → {name}: {generator.__name__}")
        try:
            produced = generator(script_to_search_term, candidates_data)
        except Exception as exc:
            print(f"[generators] FATAL: {generator.__name__} raised: {exc}")
            raise

        for script_text in produced:
            if script_text in combined:
                print(f"[generators] WARNING: '{script_text[:60]}' already produced by "
                      f"another generator — overwriting with {name}")
        combined.update(produced)
        print(f"[generators] ← {name} produced {len(produced)} entry(ies)")

    print(f"\n[generators] all generators done — {len(combined)} total entry(ies)")
    return combined

# ===========================================================================
# KEN BURNS EFFECT FOR STATIC IMAGES
# ===========================================================================
# Static images (Pexels / Wikipedia stills selected via the review GUI) are
# converted into short MP4s with a randomly-chosen but weighted Ken Burns
# style motion before being handed to the stitcher. Each (image, effect,
# duration) combo is cached as kb-<md5>.mp4 so re-runs skip re-encoding.

class KenBurnsEffect(Enum):
    ZOOM_IN_CENTER     = "zoom_in_center"
    ZOOM_OUT_CENTER    = "zoom_out_center"
    PAN_LEFT_TO_RIGHT  = "pan_left_to_right"
    PAN_RIGHT_TO_LEFT  = "pan_right_to_left"
    TILT_BOTTOM_TO_TOP = "tilt_bottom_to_top"
    TILT_TOP_TO_BOTTOM = "tilt_top_to_bottom"
    ZOOM_IN_PAN_LR     = "zoom_in_pan_lr"
    ZOOM_IN_PAN_RL     = "zoom_in_pan_rl"
    ZOOM_OUT_PAN_LR    = "zoom_out_pan_lr"
    ZOOM_OUT_PAN_RL    = "zoom_out_pan_rl"


# Probabilities must sum to ~1.0. random.choices handles normalisation
# internally so small rounding is fine.
KEN_BURNS_EFFECT_PROBABILITIES: dict[KenBurnsEffect, float] = {
    KenBurnsEffect.ZOOM_IN_CENTER:     0.28,
    KenBurnsEffect.ZOOM_OUT_CENTER:    0.22,
    KenBurnsEffect.PAN_LEFT_TO_RIGHT:  0.14,
    KenBurnsEffect.PAN_RIGHT_TO_LEFT:  0.12,
    KenBurnsEffect.TILT_BOTTOM_TO_TOP: 0.06,
    KenBurnsEffect.TILT_TOP_TO_BOTTOM: 0.05,
    KenBurnsEffect.ZOOM_IN_PAN_LR:     0.04,
    KenBurnsEffect.ZOOM_IN_PAN_RL:     0.04,
    KenBurnsEffect.ZOOM_OUT_PAN_LR:    0.03,
    KenBurnsEffect.ZOOM_OUT_PAN_RL:    0.02,
}

# Rendering parameters — tweak these to taste.
KEN_BURNS_OUTPUT_RESOLUTION:    tuple[int, int] = (1920, 1080)
KEN_BURNS_WORKING_RESOLUTION:   tuple[int, int] = (4000, 2250)  # 1.78 aspect, oversampled
KEN_BURNS_ZOOM_DELTA:           float = 0.05   # 5% of frame
KEN_BURNS_PAN_DELTA:            float = 0.05   # 5% of working dim
KEN_BURNS_FPS:                  int   = 30
KEN_BURNS_RENDER_SAFETY_PAD_SEC: float = 0.08  # same trick as read-out scenes

KEN_BURNS_CACHE_DIR = Path(f"{_CACHE_DIR}/ken_burns")

IMAGE_EXTENSIONS: set[str] = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _classify_footage_path(path: str) -> str:
    """Return 'image', 'video', or 'other' for a footage entry key."""
    # Strip URL query strings and fragments before reading the extension —
    # Pexels image URLs look like `....jpeg?auto=compress&...` which would
    # otherwise produce a suffix of `.jpeg?auto=compress&...`.
    clean = path.split("?", 1)[0].split("#", 1)[0]
    suffix = Path(clean).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
        return "video"
    return "other"


def _is_image_path(path: str) -> bool:
    return _classify_footage_path(path) == "image"


def _pick_ken_burns_effect(seed_string: str) -> KenBurnsEffect:
    """Deterministic per-image weighted random pick — same image, same effect."""
    rng = random.Random(seed_string)
    effects = list(KEN_BURNS_EFFECT_PROBABILITIES.keys())
    weights = list(KEN_BURNS_EFFECT_PROBABILITIES.values())
    return rng.choices(effects, weights=weights, k=1)[0]

def _build_ken_burns_filter(effect: KenBurnsEffect, duration: float) -> str:
    """
    Build the ffmpeg -vf chain for `effect` over `duration` seconds.

    Uses zoompan — the canonical Ken Burns filter. (The `crop` filter's
    w/h are not per-frame, so it can't do zoom; zoompan handles zoom+pan
    in one go for all 10 effects.)

    Pipeline:  cover-fit to oversampled canvas  ->  zoompan  ->  output
    Easing:    smoothstep on `on/(tf-1)` clamped to [0,1]  (safety pad
               frames at the end hold the final position stationary).
    """
    out_w, out_h   = KEN_BURNS_OUTPUT_RESOLUTION
    over_w, over_h = KEN_BURNS_WORKING_RESOLUTION
    fps            = KEN_BURNS_FPS
    z_delta        = KEN_BURNS_ZOOM_DELTA          # 0.05
    pan_z          = 1 + KEN_BURNS_PAN_DELTA       # 1.05 — baseline zoom
                                                   # for pan-only effects so
                                                   # there's room to move

    # Total animation frames — clamped to ≥2 so on/(tf-1) is safe.
    tf = max(2, int(round(duration * fps)))

    # smoothstep-eased progress p ∈ [0,1] across the scene's actual runtime.
    # `\,` escapes the comma so the filtergraph parser doesn't treat it as
    # a filter separator.
    p = f"min(on/{tf - 1}\\,1)"
    s = f"({p}*{p}*(3-2*{p}))"

    # Visible window in input space is (iw/zoom, ih/zoom).
    # x/y are the top-left corner of that window in input coords.
    cx, cy       = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    max_x, max_y = "(iw-iw/zoom)",   "(ih-ih/zoom)"

    match effect:
        case KenBurnsEffect.ZOOM_IN_CENTER:
            z, x, y = f"(1+{z_delta}*{s})", cx, cy
        case KenBurnsEffect.ZOOM_OUT_CENTER:
            z, x, y = f"(1+{z_delta}-{z_delta}*{s})", cx, cy
        case KenBurnsEffect.PAN_LEFT_TO_RIGHT:
            z, x, y = f"{pan_z}", f"{max_x}*{s}", cy
        case KenBurnsEffect.PAN_RIGHT_TO_LEFT:
            z, x, y = f"{pan_z}", f"{max_x}*(1-{s})", cy
        case KenBurnsEffect.TILT_BOTTOM_TO_TOP:
            z, x, y = f"{pan_z}", cx, f"{max_y}*(1-{s})"
        case KenBurnsEffect.TILT_TOP_TO_BOTTOM:
            z, x, y = f"{pan_z}", cx, f"{max_y}*{s}"
        case KenBurnsEffect.ZOOM_IN_PAN_LR:
            z = f"(1+{z_delta}*{s})"
            x, y = f"{max_x}*(0.3+0.4*{s})", cy
        case KenBurnsEffect.ZOOM_IN_PAN_RL:
            z = f"(1+{z_delta}*{s})"
            x, y = f"{max_x}*(0.7-0.4*{s})", cy
        case KenBurnsEffect.ZOOM_OUT_PAN_LR:
            z = f"(1+{z_delta}-{z_delta}*{s})"
            x, y = f"{max_x}*(0.3+0.4*{s})", cy
        case KenBurnsEffect.ZOOM_OUT_PAN_RL:
            z = f"(1+{z_delta}-{z_delta}*{s})"
            x, y = f"{max_x}*(0.7-0.4*{s})", cy
        case _:
            raise ValueError(f"Unknown Ken Burns effect: {effect}")

    # Cover-fit to oversampled canvas; gives zoompan plenty of pixels to
    # crop from when zoomed in. force_original_aspect_ratio=increase scales
    # up so both dims meet/exceed the target, then crop trims the excess.
    prep = (f"scale={over_w}:{over_h}:force_original_aspect_ratio=increase,"
            f"crop={over_w}:{over_h},setsar=1")

    # d=1 → each input frame produces exactly 1 output frame. Combined with
    # `-loop 1 -framerate fps -i image -t duration` at the CLI level, this
    # gives us a clean monotonic `on` from 0 to duration*fps.
    zp = (f"zoompan=z='{z}':x='{x}':y='{y}'"
          f":d=1:s={out_w}x{out_h}:fps={fps}")

    return f"{prep},{zp}"


def _ken_burns_cache_path(image_path: str, effect: KenBurnsEffect,
                          duration: float) -> Path:
    """Stable cache filename keyed on (image, effect, duration)."""
    key = f"{image_path}|{effect.value}|{round(duration, 3)}"
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    return KEN_BURNS_CACHE_DIR / f"kb-{h}.mp4"


def _render_ken_burns_clip(image_path: str, effect: KenBurnsEffect,
                           duration: float) -> str:
    """
    Render a Ken Burns MP4 from `image_path`. Caches by (image, effect, duration).
    Returns the output path (str).
    """
    KEN_BURNS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _ken_burns_cache_path(image_path, effect, duration)

    if output_path.exists() and output_path.stat().st_size > 1024:
        if DEBUG:
            print(f"  [ken-burns cache hit] {output_path.name}")
        return str(output_path)

    render_duration = duration + KEN_BURNS_RENDER_SAFETY_PAD_SEC
    filter_str = _build_ken_burns_filter(effect, duration)   # ← was render_duration

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", str(KEN_BURNS_FPS),
        "-i", image_path,
        "-t", f"{render_duration:.3f}",
        "-vf", filter_str,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-r", str(KEN_BURNS_FPS),
        "-an",
        str(output_path),
    ]

    if DEBUG:
        print(f"  [ken-burns render] {Path(image_path).name} "
              f"effect={effect.value} dur={duration:.2f}s -> {output_path.name}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ken-burns] FATAL: ffmpeg failed for {image_path}")
        print(f"[ken-burns] filter: {filter_str}")
        print(f"[ken-burns] stderr (tail): {result.stderr[-800:]}")
        if output_path.exists():
            output_path.unlink()
        raise RuntimeError(f"Ken Burns render failed: {image_path}")

    return str(output_path)


def _resolve_to_local_path(path: str) -> str | None:
    """
    Resolve a footage entry key to an on-disk path.

    final_data entries can be keyed by:
      - a remote URL (Pexels/Wikipedia) — resolve via history.json
      - an already-local path (joint/read-out generators, or prior KB pass)

    Returns the local path if found on disk, else None.
    """
    if path.startswith(("http://", "https://")):
        history = _load_history()
        local = history.get(path)
        if local and Path(local).exists():
            return local
        return None

    return path if Path(path).exists() else None


def apply_ken_burns_to_final_data(
    final_data: list[dict],
) -> tuple[list[dict], dict[str, str]]:
    """
    Walk `final_data` and replace every static-image footage entry with a
    freshly-rendered Ken Burns MP4 of the same trim duration.

    Image entries may be keyed by URL (need history.json lookup) or by an
    already-local path. We handle both.

    Returns (final_data, path_remap) where path_remap is {old_key: new_mp4}
    so the caller can update history.json.
    """
    print("\n" + "=" * 70)
    print("[ken-burns] APPLYING Ken Burns to static images in final_data")
    print(f"[ken-burns] enabled={APPLY_KEN_BURNS_AFFECT}")
    print("=" * 70)

    if not APPLY_KEN_BURNS_AFFECT:
        print("[ken-burns] APPLY_KEN_BURNS_AFFECT=False — skipping")
        return final_data, {}

    # ── Diagnostic scan — categorise everything in final_data ─────────
    print(f"\n[ken-burns:scan] Scanning {len(final_data)} scene(s) in final_data...")
    video_exts = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
    n_total = n_img_url = n_img_local = n_videos = n_other = 0

    for entry in final_data:
        for footage_item in entry.get("footage", []):
            for path in footage_item:
                n_total += 1
                kind = _classify_footage_path(path)
                if kind == "image":
                    if path.startswith(("http://", "https://")):
                        n_img_url += 1
                    else:
                        n_img_local += 1
                elif kind == "video":
                    n_videos += 1
                else:
                    n_other += 1

    print(f"[ken-burns:scan]   total footage entries: {n_total}")
    print(f"[ken-burns:scan]   images (URL keys):     {n_img_url}")
    print(f"[ken-burns:scan]   images (local keys):   {n_img_local}")
    print(f"[ken-burns:scan]   videos:                {n_videos}")
    print(f"[ken-burns:scan]   other/unknown:         {n_other}")

    total_images = n_img_url + n_img_local
    if total_images == 0:
        print("[ken-burns] no static images in final_data — nothing to do")
        return final_data, {}

    print(f"\n[ken-burns] processing {total_images} static image(s)...")
    tracker = ProgressTracker(total=total_images, label="KEN BURNS")
    path_remap: dict[str, str] = {}
    n_rendered = n_skipped_missing = n_failed = 0

    for entry in final_data:
        new_footage: list[dict] = []
        for footage_item in entry.get("footage", []):
            new_item: dict = {}
            for path, trim in footage_item.items():
                if not _is_image_path(path):
                    new_item[path] = trim
                    continue

                local_path = _resolve_to_local_path(path)
                if not local_path:
                    print(f"\n[ken-burns] WARNING: can't resolve to disk: {path}")
                    print(f"[ken-burns]   (not a URL in history.json AND not a "
                          f"valid local path) — keeping original entry")
                    new_item[path] = trim
                    n_skipped_missing += 1
                    tracker.tick()
                    continue

                duration = float(trim)
                effect = _pick_ken_burns_effect(path)  # seed on original key
                try:
                    mp4_path = _render_ken_burns_clip(local_path, effect, duration)
                except Exception as exc:
                    print(f"\n[ken-burns] ERROR rendering {local_path}: {exc} "
                          f"— keeping original entry")
                    new_item[path] = trim
                    n_failed += 1
                    tracker.tick()
                    continue

                new_item[mp4_path] = trim
                path_remap[path] = mp4_path
                n_rendered += 1
                tracker.tick()
            new_footage.append(new_item)
        entry["footage"] = new_footage

    tracker.finish()
    print(f"[ken-burns] DONE — rendered={n_rendered}, "
          f"skipped_missing={n_skipped_missing}, failed={n_failed}")
    return final_data, path_remap


def _add_ken_burns_paths_to_history(path_remap: dict[str, str]) -> None:
    """Add identity entries so the stitcher's url→local lookup finds the new MP4s."""
    if not path_remap:
        return
    history = _load_history()
    added = 0
    for new_path in path_remap.values():
        if new_path not in history:
            history[new_path] = new_path
            added += 1
    _save_history(history)
    print(f"[ken-burns] added {added} identity entry(ies) to history.json")

# ===========================================================================
# AUDIO EVENTS
# ===========================================================================

def build_audio_events_map(
    script_to_search_term: dict[str, SearchTermData],
) -> dict[str, list[dict]]:
    """
    Resolve per-scene audio events from the JSON.

    Priority for SFX:
      1. Per-scene `sfx` field if not "none"
      2. Joint-type default from JOINT_TYPE_SFX_MAP (auto-injected for
         scenes whose search_type is in JOINT_TYPES)
      3. Nothing
    """
    print("\n" + "=" * 70)
    print("[audio events] BUILDING audio events map")
    print(f"[audio events] {len(script_to_search_term)} scene(s) to process")
    print(f"[audio events] hardcoded SFX_VOLUME={SFX_VOLUME}, "
          f"MUSIC_VOLUME={MUSIC_VOLUME}")
    print("=" * 70)

    out: dict[str, list[dict]] = {}

    def _is_none(value) -> bool:
        return value in (None, "none", "None", "")

    for script_text, scene_data in script_to_search_term.items():
        events: list[dict] = []
        short = script_text[:60]
        print(f"\n[audio events] scene: '{short}{'...' if len(script_text) > 60 else ''}'")

        search_type = scene_data.get("search_type")

        # ── SFX resolution ──────────────────────────────────────────
        user_sfx = scene_data.get("sfx", "none")

        if not _is_none(user_sfx):
            timing = scene_data.get("sfx_timing", "loop_start")
            sfx_path = str(SOUND_EFFECTS_DIR / user_sfx)
            events.append({
                "type":   "sfx",
                "path":   sfx_path,
                "timing": timing,
                "_debug": f"user-defined sfx '{user_sfx}'",
            })
            print(f"[audio events]   + SFX (user): {user_sfx} @ {timing}")
        else:
            if search_type in JOINT_TYPE_SFX_MAP:
                default = JOINT_TYPE_SFX_MAP[search_type]
                sfx_path = str(SOUND_EFFECTS_DIR / default["path"])
                events.append({
                    "type":   "sfx",
                    "path":   sfx_path,
                    "timing": default["timing"],
                    "_debug": f"auto-injected for type {search_type.value}",
                })
                print(f"[audio events]   + SFX (auto for {search_type.value}): "
                      f"{default['path']} @ {default['timing']}")
            else:
                print(f"[audio events]   (no SFX for this scene)")

        # ── Music resolution ────────────────────────────────────────
        user_music = scene_data.get("music", "none")

        if not _is_none(user_music):
            trim_raw = float(scene_data.get("music_trim_seconds", 0))
            trim = None if trim_raw == 0 else trim_raw

            fade_raw = float(scene_data.get("music_fade_out", 0))
            fade = fade_raw

            music_path = str(SOUND_EFFECTS_DIR / user_music)
            events.append({
                "type":     "music",
                "path":     music_path,
                "timing":   "scene_start",
                "duration": trim,
                "fade_out": fade,
                "_debug":   (f"user-defined music '{user_music}' "
                             f"(trim={trim}, fade={fade}s)"),
            })
            print(f"[audio events]   + MUSIC: {user_music} trim={trim} fade={fade}s")
        else:
            print(f"[audio events]   (no music for this scene)")

        if events:
            out[script_text] = events

    print("\n" + "=" * 70)
    print(f"[audio events] DONE — {len(out)} scene(s) have audio events")
    print("=" * 70)

    return out


# ===========================================================================
# MISC
# ===========================================================================

def additional_steps_save_for_later():
    # Custom images, Ken Burns effects, etc.
    pass


def verify_environment():
    pass


def split_text_into_sections(section):
    lines = section.split("\n")
    cleaned = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r"^#+\s", line):
            continue
        cleaned.append(line)

    return cleaned


# ===========================================================================
# MAIN  –  ORCHESTRATOR
# ===========================================================================

def main() -> None:
    """
    Runs the full pipeline from raw script to finished video.
    Each stage is a clearly labelled block – treat this like a bash script.
    Comment out any stage to resume from a checkpoint.
    """

    verify_environment()

    # 1) Break into scenes / load search-term map
    print("====================================================================")
    print("Breaking into scenes...")
    scriptTextToPexelSearch: dict[str, SearchTermData] = load_json(LINE_INDEX_TO_SEARCH_TERM_FILE)
    # Convert string search_type to MediaType enum. (Flat schema — no
    # variant field any more; the type encodes everything.)
    for key, value in scriptTextToPexelSearch.items():
        try:
            value["search_type"] = MediaType(value["search_type"])
        except ValueError:
            valid = ", ".join(t.value for t in MediaType)
            print(f"ERROR: unknown search_type {value['search_type']!r} "
                  f"on scene '{key[:60]}'")
            print(f"       valid values: {valid}")
            sys.exit(1)
    print("!!!!!!script text to pexel search:")
    print(scriptTextToPexelSearch)

    # 1.5) Audio synchronisation — produces line timings + (optionally) per-word timings
    run_audio_script_synchronizer(SCRIPT_AUDIO_FILE, LINE_INDEX_TO_SEARCH_TERM_FILE,
                                  SYNCHRONIZED_SCRIPT_OUTPUT_FILE, TIMESTAMPS_ABSOLUTE_FILE,
                                  AUDIO_START_DELAY_SECONDS)

    # 2) Fetch external candidates (Pexels + Wikipedia) — only for types in
    #    NEEDS_EXTERNAL_CANDIDATES. Other types are produced purely locally.
    print("====================================================================")
    print("Loading stock footage candidates...")

    candidates_data = load_from_cache(CANDIDATES_CACHE_FILE)
    if candidates_data:
        print(f"✅ Loaded {len(candidates_data)} candidate bundle(s) from cache.")
    else:
        print("🔍 Cache miss. Fetching candidates...")

        # External candidates (Pexels videos+images / Wikipedia stills).
        candidates_data = load_stock_footage(scriptTextToPexelSearch)

        # AI-generated stickman candidates (STICKMAN_NUM_VARIANTS images each),
        # reviewed by the SAME GUI alongside the stock candidates.
        stickman_candidates = generate_stickman_candidates(scriptTextToPexelSearch)
        if stickman_candidates:
            candidates_data.extend(stickman_candidates)
            print(f"[main] added {len(stickman_candidates)} stickman candidate "
                  f"bundle(s) to the review set")

        # Stickman bundles are appended, so re-sort the review list into SCRIPT
        # order. We use each scene's position in the search-term file (its dict
        # insertion order) rather than the `position` FIELD — that field isn't
        # reliably unique/correct, whereas the file is written top-to-bottom in
        # script order. Safe: the review GUI keys its state by script_text.
        _script_order = {txt: i for i, txt in enumerate(scriptTextToPexelSearch)}
        candidates_data.sort(
            key=lambda b: _script_order.get(b["script_text"], 1_000_000)
        )

        save_to_cache(candidates_data, CANDIDATES_CACHE_FILE)
        print(f"💾 Cached {len(candidates_data)} candidate bundle(s) to {CANDIDATES_CACHE_FILE}.")

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

    # 2.5) STAGE 1 review — everything EXCEPT ai_edit
    print("====================================================================")
    print("Launching media review GUI (stage 1: stock / wiki / joint / stickman)...")
    non_edit_candidates = [
        c for c in candidates_data
        if scriptTextToPexelSearch.get(c["script_text"], {}).get("search_type")
           != MediaType.AI_EDIT
    ]
    final_data, has_manual = run_media_review(
        candidates_data=non_edit_candidates,
        history_file=str(HISTORY_FILE),
        review_state_file=REVIEW_STOCK_FOOTAGE_OUTPUT_FILE,
        cache_dir=_CACHE_DIR,
    )
    if has_manual:
        print("\n[main] Exiting so you can perform the manual fixes above.")
        sys.exit(0)

    # 2.55) Generate ai_edit images from stage-1 picks, then STAGE 2 review
    edit_candidates = load_from_cache(EDIT_CANDIDATES_CACHE_FILE)
    if edit_candidates:
        print(f"✅ Loaded {len(edit_candidates)} edit candidate bundle(s) from cache.")
    else:
        edit_candidates = build_ai_edit_candidates(scriptTextToPexelSearch, final_data)
        save_to_cache(edit_candidates, EDIT_CANDIDATES_CACHE_FILE)

    if edit_candidates:
        print("============================================================")
        print("Launching media review GUI (stage 2: ai_edit)...")
        edit_final_data, has_manual2 = run_media_review(
            candidates_data=edit_candidates,
            history_file=str(HISTORY_FILE),
            review_state_file=REVIEW_EDITS_OUTPUT_FILE,
            cache_dir=_CACHE_DIR,
        )
        if has_manual2:
            print("\n[main] Exiting for manual fixes (stage 2).")
            sys.exit(0)
        # Merge stage-2 picks in. by_script keeps script order from stage 1.
        by_script = {e["script_text"]: i for i, e in enumerate(final_data)}
        for e in edit_final_data:
            if e["script_text"] in by_script:
                final_data[by_script[e["script_text"]]]["footage"] = e["footage"]
            else:
                final_data.append(e)

    save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
    print(f"💾 Final script→clips map written to {FINAL_SCRIPT_AND_CLIPS}.")

    print("\n=== FINAL SCRIPT → CHOSEN MEDIA ===")
    for entry in final_data:
        print(f"\nSCRIPT: {entry['script_text']}")
        for item in entry["footage"]:
            for url, trim in item.items():
                print(f"  ✓ {url}  (trim: {trim}s)")
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

    additional_steps_save_for_later()

    # 2.6) Run every registered local-file generator (joint, read-out, …)
    #      and merge their outputs back into final_data so the stitcher
    #      uses the new local files instead of any prior placeholders.
    generated_footage_map = run_all_local_generators(
        script_to_search_term=scriptTextToPexelSearch,
        candidates_data=candidates_data,
    )

    if generated_footage_map:
        print("\n[main] local footage produced — integrating into final_data")
        final_data = _merge_generated_footage_into_final_data(
            final_data, generated_footage_map, source_label="local-generators",
        )
        _add_local_paths_to_history(generated_footage_map)

        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with local footage → {FINAL_SCRIPT_AND_CLIPS}")

        print("\n=== FINAL SCRIPT → MEDIA (POST-GENERATOR-MERGE) ===")
        for entry in final_data:
            print(f"\nSCRIPT: {entry['script_text']}")
            for item in entry["footage"]:
                for path_or_url, trim in item.items():
                    label = Path(path_or_url).name if "/" in path_or_url else path_or_url
                    print(f"  ✓ {label}  (trim: {trim}s)")
        print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
    else:
        print("\n[main] no local generators produced anything; final_data unchanged")

    # 2.62) Stickman-explain scenes: composite each scene's CHOSEN stock/wiki
    #       clip onto a board base. Runs AFTER review (needs the picks) and
    #       BEFORE Ken Burns (outputs are MP4s, so KB skips them — and the raw
    #       still, if one was picked, is already replaced here so KB never
    #       animates the board).
    explain_footage_map = generate_stickman_explain_scenes(
        script_to_search_term=scriptTextToPexelSearch,
        final_data=final_data,
    )
    if explain_footage_map:
        print("\n[main] explainer footage produced — integrating into final_data")
        final_data = _merge_generated_footage_into_final_data(
            final_data, explain_footage_map, source_label="stickman-explain",
        )
        _add_local_paths_to_history(explain_footage_map)
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with explainer footage → {FINAL_SCRIPT_AND_CLIPS}")
    else:
        print("\n[main] no explainer scenes; final_data unchanged")



    # 2.65) Convert remaining static images in final_data to Ken Burns MP4s.
    #       Joint/read-out outputs are already MP4s by this point, so only
    #       Pexels/Wikipedia stills the review GUI selected get processed.
    final_data, ken_burns_remap = apply_ken_burns_to_final_data(final_data)
    if ken_burns_remap:
        _add_ken_burns_paths_to_history(ken_burns_remap)
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with Ken Burns MP4s → {FINAL_SCRIPT_AND_CLIPS}")

        print("\n=== FINAL SCRIPT → MEDIA (POST-KEN-BURNS) ===")
        for entry in final_data:
            print(f"\nSCRIPT: {entry['script_text']}")
            for item in entry["footage"]:
                for path_or_url, trim in item.items():
                    label = Path(path_or_url).name if "/" in path_or_url else path_or_url
                    print(f"  ✓ {label}  (trim: {trim}s)")
        print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

    # 2.7) Build audio events (SFX + music) and persist for the stitcher
    print("====================================================================")
    print("Building audio events map...")
    audio_events_map = build_audio_events_map(scriptTextToPexelSearch)
    Path(AUDIO_EVENTS_FILE).write_text(json.dumps(audio_events_map, indent=2))
    print(f"💾 Audio events written to {AUDIO_EVENTS_FILE}")

    additional_steps_save_for_later()

    # 3) Stitch together into the final video.
    print("====================================================================")
    print("Stitching final video...")
    gc.collect()
    stitch_together_video(
        FINAL_SCRIPT_AND_CLIPS,
        TIMESTAMPS_ABSOLUTE_FILE,
        HISTORY_FILE,
        SCRIPT_AUDIO_FILE,
        OUTPUT_FILE,
        AUDIO_EVENTS_FILE,
        SFX_VOLUME,
        MUSIC_VOLUME,
    )

    print("done")


# ===========================================================================

if __name__ == "__main__":
    main()


# ================================================
# ==== OTHER THINGS MAYBE USEFUL DOWN THE LINE ===
# ================================================

def splitSceneIntoPowerpointSlideImages():
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
    scenes_text = """
    The empire state building is really big.
    Built in Manhattan in the 19th century.
    """
    scenes = [line.strip() for line in scenes_text.split("\n") if line.strip()]

    for scene in scenes:
        ai_request = f"""
    Would this scene be likely to have nice stock footage available?
    Scene: {scene}
    Just output: yes or no
    """

        response4 = ollama.chat(
            model="qwen2.5:7b",
            messages=[{"role": "user", "content": ai_request}]
        )

        reply4 = response4["message"]["content"].strip()
        print(scene)
        print(reply4)
        print("-----")
