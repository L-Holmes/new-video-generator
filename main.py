from __future__ import annotations

import argparse
import gc
import hashlib
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
from pathlib import Path
from typing import Callable, TypedDict

import nltk
import ollama
import requests
import spacy
from PIL import Image
from rake_nltk import Rake

import COLOUR_GRADE_ETC

# ===========================================================================
# IMPORTS - LOCAL
# ===========================================================================
from AUDIO_SCRIPT_SYNCHRONIZER import run as run_audio_script_synchronizer
from GET_FROM_WIKIPEDIA import get_from_wikipedia
from GET_MAP import get_map_image
from JOINT_IMAGE_CREATOR import TRANSITION_FADE, TRANSITION_RANDOM
from JOINT_IMAGE_CREATOR import composite as create_joint_scene
from PIXELLATE import pixellate_image
from SCRIPT_AUDIO_CUTDOWN_AND_PROCESS import run as run_audio_cutdown
from STITCH_TOGETHER import stitch_together_video
from STOCK_FOOTAGE_REVIEW import run_media_review
from WORDS_ON_SCREEN import WordRenderConfig, render_scene_to_video

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

_CACHE_DIR = f"{_NAME}-CACHE" if _NAME else "CACHE"
_OUTPUT_DIR = f"{_NAME}-OUTPUT" if _NAME else "OUTPUT"
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

# --- Cinematic colour grading (unified "shot on film at golden hour" look) ---
# Master switch: give STOCK footage one cohesive film grade so the whole video
# reads as a single graded collection. Applied late (just before Ken Burns) to
# the CHOSEN footage only — see apply_colour_grading_to_final_data + the
# "CINEMATIC COLOUR GRADING" section lower down for the full machinery.
#
# Preview / pick a look first:   uv run COLOUR_GRADE_ETC.py
TOGGLE_STOCK_COLOUR_GRADING_ETC: bool = True
# When True, grade EVERY scene (stickman / ai_edit / read-out / maps included),
# not just real-world stock. Ignored unless TOGGLE_STOCK_COLOUR_GRADING_ETC.
APPLY_COLOUR_GRADING_TO_ALL: bool = False
# COLOUR_GRADE_ETC now has ONE unified cinematic look (no variations); this just
# selects it. Run `uv run COLOUR_GRADE_ETC.py` to preview before/after stills.
STOCK_COLOUR_GRADE_PRESET: str = COLOUR_GRADE_ETC.DEFAULT_PRESET

PEXELS_API_KEY: str = "PewOP3u4JK8nTBe0kkazrBgXPSwfeh0tWS1kE9y4eS26TzTEG0wmuGK8"

STOCK_FOOTAGE_CACHE_DIR = Path(f"{_CACHE_DIR}/stock_footage/")
HISTORY_FILE = STOCK_FOOTAGE_CACHE_DIR / "history.json"

OUTPUT_FILE = f"{_OUTPUT_DIR}/output.mp4"
TEMP_DIR = Path("tmp_stitch/")

# --- Narration audio -------------------------------------------------
# The ORIGINAL recording (what you record / TTS-generate).
RAW_SCRIPT_AUDIO_FILE = f"{_SCRIPT_STEM}.wav"  # e.g. script-stickman.wav

# Tightened narration produced by SCRIPT_AUDIO_CUTDOWN_AND_PROCESS.run(),
# written under the cache dir, e.g.
#   stickman-CACHE/AUDIO/script-stickman.processed.wav   (--name stickman)
#   CACHE/AUDIO/script.processed.wav                     (no --name)
PROCESSED_AUDIO_DIR = f"{_CACHE_DIR}/AUDIO"

# SCRIPT_AUDIO_FILE now points at the PROCESSED file, so every existing use
# (synchroniser + final stitch) automatically runs on the tightened audio.
SCRIPT_AUDIO_FILE = f"{PROCESSED_AUDIO_DIR}/{_SCRIPT_STEM}.processed.wav"

# Whisper model the cutdown uses (keep in step with the synchroniser's).
AUDIO_CUTDOWN_WHISPER_MODEL = "small.en"
# Re-run the cutdown even if the processed WAV already exists — flip to True
# after you change the EASY KNOBS in SCRIPT_AUDIO_CUTDOWN_AND_PROCESS.py,
# otherwise the cached processed file is reused.
FORCE_AUDIO_CUTDOWN = False


SYNCHRONIZED_SCRIPT_OUTPUT_FILE = f"{_CACHE_DIR}/script_timings_seconds.json"
AUDIO_START_DELAY_SECONDS = 0.5

STOCK_FOOTAGE_TO_DOWNLOADED_MEDIA_FILE = f"{_CACHE_DIR}/stock_footage/history.json"
REVIEW_STOCK_FOOTAGE_OUTPUT_FILE = (
    f"{_CACHE_DIR}/stock_footage/review_accepting_footage.json"
)

FINAL_SCRIPT_AND_CLIPS = f"{_CACHE_DIR}/final_script_to_clips.json"

# Per-scene fetched candidates (videos + images). The review GUI consumes
# this. FINAL_SCRIPT_AND_CLIPS is what the stitcher consumes and is
# written only AFTER the user has finished picking.
CANDIDATES_CACHE_FILE = f"{_CACHE_DIR}/footage_candidates.json"

LINE_INDEX_TO_SEARCH_TERM_FILE = (
    f"{_NAME}_script_to_search_term.json" if _NAME else "script_to_search_term.json"
)
TIMESTAMPS_ABSOLUTE_FILE = (
    f"{_CACHE_DIR}/{_NAME}_timestamps_absolute.json"
    if _NAME
    else f"{_CACHE_DIR}/timestamps_absolute.json"
)

# Optional per-word timings produced by AUDIO_SCRIPT_SYNCHRONIZER (Whisper
# word-level). If present, READ_OUT scenes use these for exact sync;
# otherwise they fall back to syllable-based estimation.
WORD_TIMINGS_FILE = (
    f"{_CACHE_DIR}/{_NAME}_word_timings.json"
    if _NAME
    else f"{_CACHE_DIR}/word_timings.json"
)

# ===========================================================================
# Create all required dirs on startup
# ===========================================================================

Path(_CACHE_DIR).mkdir(parents=True, exist_ok=True)
Path(_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
Path(PROCESSED_AUDIO_DIR).mkdir(parents=True, exist_ok=True)  # ← add

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
# MAP INTEGRATION
# ===========================================================================
# "map" scenes are rendered locally by GET_MAP.py: the scene's search_term is
# treated as a PLACE NAME (country / region / city), geocoded via OpenStreetMap,
# and drawn as a clean highlighted map. Like read-out/joint scenes they need NO
# external candidates and NO review — generate_map_scenes produces them from the
# script + timings and the generic merge step folds them into final_data. The
# rendered PNG is baked to a STATIC MP4 so the Ken Burns pass leaves the composed
# map untouched (whole-world highlight / pin stays exactly as drawn).

# Set False to disable map rendering entirely.
MAP_ENABLE: bool = True

# Where rendered map stills + MP4s are written (cache-scoped).
MAP_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/map_scenes")

# Where GET_MAP caches geocoding results (cache-scoped). The world-borders
# GeoJSON itself is cached once, globally, next to GET_MAP.py and shared.
MAP_GEOCODE_CACHE_DIR: str = f"{_CACHE_DIR}/maps"


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
    STOCK = "stock"  # Pexels videos+images, picked via review GUI
    WIKIPEDIA = "wikipedia"  # Wikipedia images, picked via review GUI
    JOINT_3_ROW = "joint_3_row"  # 3-image collage composited locally
    READ_OUT = "read_out"  # Kinetic typography (script text on screen)
    MAP = "map"  # Highlighted map of a country/region/place (rendered locally)
    STICKMAN = "stickman"  # AI-generated stickman; 2 variants → review GUI
    AI_EDIT = "ai_edit"  # Edit the preceding AI image; N variants -> 2nd review
    STICKMAN_EXPLAIN_STOCK = (
        "stickman_explain_stock"  # chosen Pexels clip composited onto a board base
    )
    STICKMAN_EXPLAIN_WIKIPEDIA = "stickman_explain_wikipedia"  # chosen Wikipedia image composited onto a board base
    STICKMAN_TEXT_OVERLAY = (
        "stickman_text_overlay"  # caption (search_term) on the PREVIOUS scene's image
    )
    STICKMAN_JOINT_3_ROW = (
        "stickman_joint_3_row"  # like JOINT_3_ROW but tiles are AI stickman images
    )
    MANUAL_STOCK_ADD_TO_PREVIOUS = "manual_stock_add_to_previous"  # place this scene's chosen still onto the PREVIOUS scene's image (manual click/size)
    ZOOM_PREV_IMG = "zoom_prev_img"  # derive this scene's image by cropping/zooming into the PREVIOUS scene's image
    STATIC_OF_PREVIOUS = "static_of_previous"  # reuse prev image, OR freeze prev video's last *played* frame


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
    MediaType.MANUAL_STOCK_ADD_TO_PREVIOUS,
}

# Which MediaTypes are handled by the joint compositor. Add new joint
# layouts here AND to JOINT_LAYOUT_POSITIONS below.
JOINT_TYPES: set[MediaType] = {MediaType.JOINT_3_ROW, MediaType.STICKMAN_JOINT_3_ROW}

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

# Stickman-joint scenes reuse the AI stickman generator to produce the TILES
# that feed the joint compositor (instead of Pexels stills). The compositor
# only ever consumes the first image per scene, so we generate 1 variant.
STICKMAN_JOINT_TYPES: set[MediaType] = {MediaType.STICKMAN_JOINT_3_ROW}
STICKMAN_JOINT_NUM_VARIANTS: int = 1
STICKMAN_JOINT_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/stickman_joint_scenes")


# AI edit scenes are generated AFTER stage-1 review (they need the chosen
# preceding image), then reviewed in a SECOND stage with its own state file.
AI_EDIT_NUM_VARIANTS: int = 1
AI_EDIT_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/ai_edit_scenes")

# How many preceding AI images (stickman / ai_edit) to pass to the generator
# as ADDITIONAL context, on top of the base image being edited. 0 = disable.
# There may be fewer than this available (or none) — best effort.
AI_EDIT_CONTEXT_NUM_IMAGES: int = 3

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


# Text-overlay scenes: a Fireship-style caption (the scene's search_term)
# composited onto the PREVIOUS scene's chosen image. Synthesised AFTER review
# + explainer (so any prior scene type resolves) and BEFORE Ken Burns. Not
# fetched, not AI-generated, not reviewed.
STICKMAN_TEXT_OVERLAY_TYPES: set[MediaType] = {MediaType.STICKMAN_TEXT_OVERLAY}
STICKMAN_TEXT_OVERLAY_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/text_overlay_scenes")
STICKMAN_TEXT_OVERLAY_RENDER_SAFETY_PAD_SEC: float = 0.08

# How many preceding stickman images to pass as ADDITIONAL context (on top of
# the 3 style refs) for character/style continuity → up to 6 images total.
# 0 = original behaviour. Context is drawn only from preceding STICKMAN scenes
# in the same batch (script order); it can't see stock/wiki/joint/ai_edit images.
# NOTE: uses each scene's generation-time variant-0 output, not the reviewed
# pick (which doesn't exist yet — all stickman gen runs before review).
STICKMAN_CONTEXT_NUM_IMAGES: int = 3


# ===========================================================================
# MANUAL STOCK PLACEMENT (overlay one stock still onto the previous image)
# ===========================================================================
# The scene's OWN still is fetched + picked in normal stage-1 review (image-
# only — see load_stock_footage). Then a SEPARATE stage (after all picks are
# made) lets the user click where on the PREVIOUS scene's image to drop it.
# Output is a static MP4 so the Ken Burns pass leaves the placement untouched.
MANUAL_STOCK_ADD_TYPES: set[MediaType] = {MediaType.MANUAL_STOCK_ADD_TO_PREVIOUS}
ZOOM_PREV_TYPES: set[MediaType] = {MediaType.ZOOM_PREV_IMG}
STATIC_OF_PREVIOUS_TYPES: set[MediaType] = {MediaType.STATIC_OF_PREVIOUS}
MANUAL_STOCK_PLACEMENT_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/manual_stock_placement")
MANUAL_STOCK_PLACEMENT_RENDER_SAFETY_PAD_SEC: float = 0.08

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
    MediaType.STICKMAN_JOINT_3_ROW: [
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
SFX_VOLUME: float = 0.3
MUSIC_VOLUME: float = 0.01  # ducked under narration

# Per-type auto-injected SFX for joint scenes. Played at "loop_start"
# (right after the transition animation finishes) on every joint stage.
# User can still override per-scene by setting `"sfx"` in the JSON.
JOINT_TYPE_SFX_MAP: dict[MediaType, dict] = {
    MediaType.JOINT_3_ROW: {
        "path": "se-pop.mp3",
        "timing": "loop_start",
    },
    MediaType.STICKMAN_JOINT_3_ROW: {
        "path": "se-pop.mp3",
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

# Wikimedia (upload.wikimedia.org) rate-limits hard and returns HTTP 429 when
# hit with many parallel connections. Cap concurrent WIKI downloads well below
# DOWNLOAD_WORKERS and retry 429/503 with backoff. Pexels keeps full
# concurrency — this throttles Wikimedia only.
WIKI_DOWNLOAD_CONCURRENCY: int = 2
WIKI_DOWNLOAD_MAX_RETRIES: int = 4
WIKI_DOWNLOAD_BASE_BACKOFF_SEC: float = 1.5
_wiki_download_semaphore = threading.Semaphore(WIKI_DOWNLOAD_CONCURRENCY)

# Wikimedia's UA policy is enforced with a hard 403 on upload.wikimedia.org for
# generic / thin User-Agents. Use a DEDICATED session with the descriptive UA
# set at the SESSION level (mirrors GET_FROM_WIKIPEDIA.py, whose requests work),
# rather than passing a per-request header through the shared Pexels session.
WIKI_DOWNLOAD_USER_AGENT: str = (
    "VideoGenerationPipeline/1.0 "
    "(personal research project; contact: logosa1960@gmail.com)"
)

_wiki_session = requests.Session()
_wiki_session.headers.update({"User-Agent": WIKI_DOWNLOAD_USER_AGENT})
_wiki_adapter = requests.adapters.HTTPAdapter(
    pool_connections=WIKI_DOWNLOAD_CONCURRENCY,
    pool_maxsize=WIKI_DOWNLOAD_CONCURRENCY,
    max_retries=0,  # we handle retries/backoff ourselves
)
_wiki_session.mount("https://", _wiki_adapter)
_wiki_session.mount("http://", _wiki_adapter)


class ProgressTracker:
    """Thread-safe text progress indicator."""

    def __init__(self, total: int, label: str = "PROGRESS", bar_width: int = 30):
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

        msg = (
            f"{self.label} [{self.done:>4}/{self.total}]  "
            f"TIME REMAINING {bar} {eta}      "
        )
        sys.stdout.write("\r" + msg)
        sys.stdout.flush()


# =================================================
# some ai edit stuff
# =================================================


def _edit_candidates_cache_file(scene_index: int) -> str:
    return f"{_CACHE_DIR}/edit_candidates_{scene_index:03d}.json"


def _edit_review_state_file(scene_index: int) -> str:
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return str(
        STOCK_FOOTAGE_CACHE_DIR / f"review_accepting_edits_{scene_index:03d}.json"
    )


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
        print(
            f"⚠️  [cache] {file_path} exists but couldn't be parsed "
            f"({exc}) — regenerating."
        )
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


def get_script_text_to_stock_footage_search(
    scene_lines: list[str],
) -> dict[str, SearchTermData]:
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


def _get_video_metadata(
    search_term: str, max_results: int = 10, page: int = 1
) -> list[tuple[str, float]]:
    """Hit Pexels Videos API, return (url, duration) pairs — NO downloading yet."""
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={
            "query": search_term,
            "per_page": max_results,
            "orientation": "landscape",
            "page": page,
        },
        timeout=8,
    )
    if resp.status_code != 200:
        print(f"  [video meta] API error {resp.status_code} for '{search_term}'")
        return []

    results = []
    for video in resp.json().get("videos", []):
        files = sorted(
            video.get("video_files", []), key=lambda f: f.get("width", 0), reverse=True
        )
        if files:
            results.append((files[0]["link"], float(video.get("duration", 0))))

    print(f"  [video meta] '{search_term}' p{page} → {len(results)} results")
    return results


def _get_image_metadata(
    search_term: str, max_results: int = 5, page: int = 1
) -> list[str]:
    """Hit Pexels Images API, return URLs only — NO downloading."""
    try:
        resp = _http_session.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={
                "query": search_term,
                "per_page": max_results,
                "orientation": "landscape",
                "page": page,
            },
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
    """
    Thread-safe Wikipedia image downloader.

    Wikimedia 429s under parallel load, so this:
      - limits concurrent Wikimedia connections via a dedicated semaphore
        (independent of the 12-worker Pexels pool),
      - retries 429/503 with exponential backoff, honouring Retry-After,
      - sends Wikimedia's required descriptive User-Agent.
    """
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

    with _wiki_download_semaphore:  # throttle Wikimedia concurrency
        for attempt in range(1, WIKI_DOWNLOAD_MAX_RETRIES + 1):
            resp = None
            try:
                resp = _wiki_session.get(url, stream=True, timeout=30)
            except Exception as exc:
                print(f"  [wiki download] conn error (attempt {attempt}): {exc}")

            # Success.
            if resp is not None and resp.status_code == 200:
                try:
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            f.write(chunk)
                except Exception as exc:
                    print(f"  [wiki download] write failed: {exc}")
                    return None
                break

            status = resp.status_code if resp is not None else "conn-error"

            # Non-retryable HTTP error → give up immediately.
            if resp is not None and resp.status_code not in (429, 503):
                print(f"  [wiki download] FAILED {status} for {url}")
                return None

            # Out of attempts.
            if attempt == WIKI_DOWNLOAD_MAX_RETRIES:
                print(
                    f"  [wiki download] FAILED {status} after "
                    f"{WIKI_DOWNLOAD_MAX_RETRIES} attempts for {url}"
                )
                return None

            # Honour Retry-After if present, else exponential backoff + jitter.
            wait = WIKI_DOWNLOAD_BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            if resp is not None:
                ra = resp.headers.get("Retry-After")
                if ra:
                    try:
                        wait = max(wait, float(ra))
                    except ValueError:
                        pass
            wait += random.uniform(0, 0.5)
            print(
                f"  [wiki download] {status} — retry "
                f"{attempt}/{WIKI_DOWNLOAD_MAX_RETRIES} in {wait:.1f}s"
            )
            time.sleep(wait)

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
        print("[timings]   (did run_audio_script_synchronizer run?)")
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
    loop = max(0.0, total - intro)

    print(f"[joint:timings] '{script_text[:70]}'")
    print(f"[joint:timings]   total={total:.3f}s  use_transition={use_transition}")
    print(f"[joint:timings]   intro={intro:.3f}s  loop={loop:.3f}s")

    return {
        "script_text": script_text,
        "total_duration": total,
        "use_transition": use_transition,
        "intro_duration": intro,
        "loop_duration": loop,
    }


def _stage_file_paths(
    group_output_folder: Path,
    stage_index: int,
    num_stages: int,
) -> tuple[Path, Path]:
    """Return (intro_path, loop_path) for one stage of a joint group."""
    intro = group_output_folder / f"stage_{stage_index + 1:02d}_of_{num_stages:02d}.mp4"
    loop = (
        group_output_folder
        / f"stage_{stage_index + 1:02d}_of_{num_stages:02d}_loop.mp4"
    )
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
        group_output_folder,
        stage_index,
        num_stages,
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
            print(
                f"[joint:footage] FATAL: transition stage missing loop file: {loop_path}"
            )
            sys.exit(1)

        entries.append({str(intro_path): round(timing["intro_duration"], 3)})
        print(
            f"[joint:footage]   → intro entry: {intro_path.name}  "
            f"trim={timing['intro_duration']:.3f}s"
        )

        if timing["loop_duration"] > 0.01:
            entries.append({str(loop_path): round(timing["loop_duration"], 3)})
            print(
                f"[joint:footage]   → loop  entry: {loop_path.name}  "
                f"trim={timing['loop_duration']:.3f}s"
            )
        else:
            print(f"[joint:footage]   (loop omitted — duration <= 0.01s)")
    else:
        use_path = loop_path if loop_path.exists() else intro_path
        entries.append({str(use_path): round(timing["total_duration"], 3)})
        print(
            f"[joint:footage]   → static entry: {use_path.name}  "
            f"trim={timing['total_duration']:.3f}s  (no transition: scene too short)"
        )

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
    print(
        f"[merge:{source_label}] merging {len(generated_footage_map)} entry(ies) "
        f"into final_data"
    )
    print(
        f"[merge:{source_label}] final_data currently has {len(final_data)} entry(ies)"
    )
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
            print(
                f"[merge:{source_label}] REPLACED '{script_text[:60]}...'  "
                f"(was {old_count} entry(ies), now {len(entries)})"
            )
            for e in entries:
                for path, trim in e.items():
                    print(f"[merge:{source_label}]     {Path(path).name}  trim={trim}s")
        else:
            final_data.append({"script_text": script_text, "footage": entries})
            appended += 1
            print(
                f"[merge:{source_label}] APPENDED '{script_text[:60]}...'  "
                f"({len(entries)} entry(ies))"
            )
            for e in entries:
                for path, trim in e.items():
                    print(f"[merge:{source_label}]     {Path(path).name}  trim={trim}s")

    print(
        f"\n[merge:{source_label}] done — replaced={replaced}, appended={appended}, "
        f"final_data size now {len(final_data)}"
    )
    return final_data


def _add_local_paths_to_history(generated_footage_map: dict[str, list[dict]]) -> None:
    """
    The stitcher's history.json maps {url → local_path}. For locally-generated
    files (joint scenes, read-out scenes, future types) we add identity
    entries (path → path) so the same lookup mechanism resolves them with no
    stitcher changes.
    """
    history = _load_history()
    print(f"\n[history] augmenting history.json (currently {len(history)} entries)")

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
    print(
        f"[history] done — added={added}, already_present={skipped}, "
        f"history now has {len(history)} entries"
    )


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
    print(
        f"\n[fetch] Phase A: gathering metadata for {len(scene_items)} scene(s) "
        f"in parallel..."
    )
    if skipped_by_type:
        skipped_summary = ", ".join(f"{n} {t}" for t, n in skipped_by_type.items())
        print(f"[fetch]   (skipped {skipped_summary} — produced by local generators)")

    def fetch_meta_for_scene(idx_and_scene):
        idx, (script_text, scene_data) = idx_and_scene
        search_term = scene_data["search_term"]
        search_type = scene_data["search_type"]
        num_clips, max_runtime = _get_num_stock_images(script_text)

        # Explainer scenes feature ONE chosen clip; manual-placement scenes use
        # ONE chosen still. Both want a single pick spanning the full scene.
        if (
            search_type in STICKMAN_EXPLAIN_TYPES
            or search_type in MANUAL_STOCK_ADD_TYPES
        ):
            max_runtime = num_clips * max_runtime  # == full scene runtime
            num_clips = 1

        print(f"\n[fetch:meta] scene[{idx}] '{script_text[:50]}...'")
        print(f"[fetch:meta]   search='{search_term}', type={search_type.value}")

        # ── WIKIPEDIA path ───────────────────────────────────────────
        if search_type in WIKIPEDIA_TYPES:
            print(f"[fetch:meta]   → using WIKIPEDIA source")
            wiki_urls = get_from_wikipedia(search_term, max_images=5)
            print(f"[fetch:meta]   wikipedia returned {len(wiki_urls)} URL(s)")
            return (idx, script_text, num_clips, max_runtime, [], [], wiki_urls)

        # ── PEXELS path ──────────────────────────────────────────────
        print(f"[fetch:meta]   → using PEXELS source")

        video_meta: list[tuple[str, float]] = []
        # manual_stock_add_to_previous places a STILL, so never fetch clips for it.
        fetch_videos = search_type not in MANUAL_STOCK_ADD_TYPES
        seen: set[str] = set()
        if fetch_videos:
            for page in range(1, 4):
                if len(video_meta) >= 2:
                    break
                for url, dur in _get_video_metadata(
                    search_term, max_results=10, page=page
                ):
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

        return (idx, script_text, num_clips, max_runtime, video_meta, image_urls, [])

    out: list[dict] = [None] * len(scene_items)  # type: ignore[list-item]
    all_tasks: list[tuple] = []
    # task = (scene_idx, kind, url, trim_seconds)
    # kind is one of: "videos", "images", "wiki_images"

    if not scene_items:
        print(f"[fetch] no eligible scenes — returning empty candidates list")
        return []

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        for result in ex.map(fetch_meta_for_scene, enumerate(scene_items)):
            (
                idx,
                script_text,
                num_clips,
                max_runtime,
                video_meta,
                pexels_img_urls,
                wiki_img_urls,
            ) = result

            out[idx] = {
                "script_text": script_text,
                "candidates": {"videos": [], "images": []},
                "num_clips_needed": num_clips,
                "max_runtime_per_clip_seconds": max_runtime,
            }

            for url, dur in video_meta:
                trim = min(dur, max_runtime)
                all_tasks.append((idx, "videos", url, round(trim, 2)))

            for url in pexels_img_urls:
                all_tasks.append((idx, "images", url, round(float(max_runtime), 2)))

            for url in wiki_img_urls:
                all_tasks.append(
                    (idx, "wiki_images", url, round(float(max_runtime), 2))
                )

    print(f"[fetch] Phase A done — {len(all_tasks)} files queued.")

    if not all_tasks:
        return out

    # ── Phase B: parallel download with progress bar ──────────────────
    print(
        f"[fetch] Phase B: downloading {len(all_tasks)} files "
        f"with {DOWNLOAD_WORKERS} workers..."
    )
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
        txt: data
        for txt, data in script_to_search_term.items()
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

    print(
        f"[stickman] generating {STICKMAN_NUM_VARIANTS} variant(s) per scene "
        f"→ {STICKMAN_OUTPUT_DIR}"
    )
    generated = generate_stickman_images(
        prompts_file=STICKMAN_PROMPTS_FILE,
        out_dir=STICKMAN_OUTPUT_DIR,
        num_variants=STICKMAN_NUM_VARIANTS,
        context_num_images=STICKMAN_CONTEXT_NUM_IMAGES,
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
            print(
                f"[stickman] WARNING: no images for '{script_text[:60]}' — "
                f"the review GUI will have no options for this scene"
            )
            continue

        image_paths = [p for p in image_paths if Path(p).exists()]
        if not image_paths:
            print(
                f"[stickman] WARNING: generated paths missing on disk for "
                f"'{script_text[:60]}' — skipping"
            )
            continue

        if script_text not in scene_timings:
            print(f"[stickman] FATAL: no timing for '{script_text[:60]}'")
            sys.exit(1)
        duration = round(float(scene_timings[script_text]), 3)

        # One stickman image fills the whole scene; offer every variant as a
        # choice. num_clips_needed = 1 — the reviewer picks a single image.
        image_candidates = [{p: duration} for p in image_paths]

        bundles.append(
            {
                "script_text": script_text,
                "candidates": {"videos": [], "images": image_candidates},
                "num_clips_needed": 1,
                "max_runtime_per_clip_seconds": duration,
            }
        )
        print(
            f"[stickman]   '{script_text[:50]}' → {len(image_candidates)} "
            f"option(s), {duration:.2f}s each"
        )

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


def generate_stickman_joint_candidates(
    script_to_search_term: dict[str, SearchTermData],
) -> list[dict]:
    """
    For every scene whose search_type is in STICKMAN_JOINT_TYPES (currently just
    MediaType.STICKMAN_JOINT_3_ROW), generate ONE AI stickman image — reusing
    the SAME generator + prompt engineering as MediaType.STICKMAN — and return
    candidate bundles in the SAME shape load_stock_footage() returns.

    These bundles are appended to candidates_data and consumed by the joint
    compositor (generate_joint_scenes) EXACTLY like the Pexels-image bundles are
    for JOINT_3_ROW. The ONLY difference between joint_3_row and
    stickman_joint_3_row is the source of the tile images: Pexels vs AI.

    generate_stickman_images() filters its prompts file by an EXACT
    search_type == process_type match, so we just pass the REAL search-term file
    with process_type = "stickman_joint_3_row". No temp file / relabelling
    needed, and it can't collide with the ordinary "stickman" pass.

    Returns [] (and does no work) if there are no stickman-joint scenes.
    """
    joint_scenes = {
        txt: data
        for txt, data in script_to_search_term.items()
        if data["search_type"] in STICKMAN_JOINT_TYPES
    }

    print("\n" + "=" * 70)
    print(f"[stickman-joint] {len(joint_scenes)} stickman-joint scene(s) found")
    print("=" * 70)

    if not joint_scenes:
        print("[stickman-joint] nothing to generate — skipping")
        return []

    # Lazy import — keeps the fal / dotenv dependency out of runs that don't
    # use any AI-generated scenes.
    from ai_generate_stickman_images import generate_stickman_images

    STICKMAN_JOINT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate once per stickman-joint type (the file filter matches ONE
    # type-string per call). Currently a single type, but looping keeps the
    # set honest if more get added later.
    print(
        f"[stickman-joint] generating {STICKMAN_JOINT_NUM_VARIANTS} variant(s) "
        f"per scene → {STICKMAN_JOINT_OUTPUT_DIR}"
    )
    generated: dict[str, list[str]] = {}
    for jt in STICKMAN_JOINT_TYPES:
        part = generate_stickman_images(
            prompts_file=STICKMAN_PROMPTS_FILE,  # the real search-term file
            out_dir=STICKMAN_JOINT_OUTPUT_DIR,
            num_variants=STICKMAN_JOINT_NUM_VARIANTS,
            process_type=jt.value,  # e.g. "stickman_joint_3_row"
        )
        generated.update(part)
    # generated: { script_text: [path, ...] }
    print(f"[stickman-joint] generator returned images for {len(generated)} scene(s)")

    scene_timings = _load_scene_timings()

    bundles: list[dict] = []
    history = _load_history()
    added = 0

    for txt in joint_scenes:
        image_paths = generated.get(txt)
        if not image_paths:  # stripped-key fallback (mirrors stickman/joint paths)
            for k, v in generated.items():
                if k.strip() == txt.strip():
                    image_paths = v
                    break

        image_paths = [p for p in (image_paths or []) if Path(p).exists()]
        if not image_paths:
            print(
                f"[stickman-joint] WARNING: no image generated for "
                f"'{txt[:60]}' — the joint compositor will fail for this scene"
            )
            continue

        if txt not in scene_timings:
            print(f"[stickman-joint] FATAL: no timing for '{txt[:60]}'")
            sys.exit(1)
        duration = round(float(scene_timings[txt]), 3)

        # One image fills this scene's tile; the compositor uses the first
        # candidate, so a single variant is all that's needed.
        image_candidates = [{p: duration} for p in image_paths]

        bundles.append(
            {
                "script_text": txt,
                "candidates": {"videos": [], "images": image_candidates},
                "num_clips_needed": 1,
                "max_runtime_per_clip_seconds": duration,
            }
        )
        print(
            f"[stickman-joint]   '{txt[:50]}' → {len(image_candidates)} "
            f"image(s), {duration:.2f}s each"
        )

        # Identity entries so history.get(image_url) in the joint compositor
        # resolves these PNGs straight to disk (no download attempted).
        for p in image_paths:
            if p not in history:
                history[p] = p
                added += 1

    _save_history(history)
    print(f"[stickman-joint] added {added} identity entry(ies) to history.json")
    print(f"[stickman-joint] DONE — {len(bundles)} candidate bundle(s)")
    return bundles


def _regenerate_stickman_joint_scene(
    script_text: str,
    script_to_search_term: dict[str, SearchTermData],
) -> list[dict] | None:
    """
    Re-run the stickman generator for ONE stickman_joint tile → fresh image
    candidates for the review GUI's 'try again' (R). Same generator + prompt
    engineering as stickman, just the joint type/dir/variant count.
    """
    from ai_generate_stickman_images import _scene_stem, generate_stickman_images

    stem = _scene_stem(script_text)
    for v in range(STICKMAN_JOINT_NUM_VARIANTS):
        for fname in (f"{stem}_{v}.png", f"{stem}_{v}.placeholder.png"):
            try:
                (STICKMAN_JOINT_OUTPUT_DIR / fname).unlink(missing_ok=True)
            except Exception:
                pass

    st = script_to_search_term.get(script_text, {}).get("search_type")
    process_type = st.value if hasattr(st, "value") else "stickman_joint_3_row"

    generated = generate_stickman_images(
        prompts_file=STICKMAN_PROMPTS_FILE,
        out_dir=STICKMAN_JOINT_OUTPUT_DIR,
        num_variants=STICKMAN_JOINT_NUM_VARIANTS,
        process_type=process_type,
    )

    paths = generated.get(script_text)
    if not paths:
        for k, v in generated.items():
            if k.strip() == script_text.strip():
                paths = v
                break
    paths = [p for p in (paths or []) if Path(p).exists()]
    if not paths:
        print(f"[regen] stickman_joint produced nothing for '{script_text[:60]}'")
        return None

    scene_timings = _load_scene_timings()
    duration = round(float(scene_timings[script_text]), 3)

    history = _load_history()
    for p in paths:
        history.setdefault(p, p)
    _save_history(history)

    entries = _maybe_pixellate_entries(
        [{p: duration} for p in paths],
        script_to_search_term.get(script_text, {}).get("search_type"),
    )
    print(f"[regen] stickman_joint '{script_text[:50]}' → {len(entries)} new option(s)")
    return entries


def _regenerate_stickman_scene(
    script_text: str,
    script_to_search_term: dict[str, SearchTermData],
) -> list[dict] | None:
    """
    Re-run the stickman generator for ONE scene → fresh image candidates
    ([{path: trim}, ...]) for the review GUI's 'try again' (R).

    Deletes this scene's existing variant + placeholder PNGs so the generator
    actually re-renders it (it skips files that already exist); every OTHER
    stickman scene keeps its cached image.
    """
    from ai_generate_stickman_images import _scene_stem, generate_stickman_images

    stem = _scene_stem(script_text)
    for v in range(STICKMAN_NUM_VARIANTS):
        for fname in (f"{stem}_{v}.png", f"{stem}_{v}.placeholder.png"):
            try:
                (STICKMAN_OUTPUT_DIR / fname).unlink(missing_ok=True)
            except Exception:
                pass

    generated = generate_stickman_images(
        prompts_file=STICKMAN_PROMPTS_FILE,
        out_dir=STICKMAN_OUTPUT_DIR,
        num_variants=STICKMAN_NUM_VARIANTS,
        context_num_images=STICKMAN_CONTEXT_NUM_IMAGES,
    )

    paths = generated.get(script_text)
    if not paths:  # stripped-key fallback
        for k, v in generated.items():
            if k.strip() == script_text.strip():
                paths = v
                break
    paths = [p for p in (paths or []) if Path(p).exists()]
    if not paths:
        print(f"[regen] stickman produced nothing for '{script_text[:60]}'")
        return None

    scene_timings = _load_scene_timings()
    duration = round(float(scene_timings[script_text]), 3)

    history = _load_history()  # identity entries so lookups resolve
    for p in paths:
        history.setdefault(p, p)
    _save_history(history)

    entries = _maybe_pixellate_entries(
        [{p: duration} for p in paths],
        script_to_search_term.get(script_text, {}).get("search_type"),
    )
    print(f"[regen] stickman '{script_text[:50]}' → {len(entries)} new option(s)")
    return entries


def _regenerate_ai_edit_scene(
    edit_text: str,
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
    cand_cache: str,
) -> list[dict] | None:
    """
    Re-run the ai_edit generator for ONE edit scene → fresh image candidates
    for the review GUI's 'try again' (R).

    Deletes this edit's existing variant + placeholder PNGs (the generator
    skips existing files), rebuilds the candidates from the CURRENT final_data
    (same base/context the user is reviewing), and refreshes the per-edit
    candidate cache so a later resume uses the new images.
    """
    from ai_generate_stickman_images import _scene_stem

    stem = _scene_stem(edit_text)
    for v in range(AI_EDIT_NUM_VARIANTS):
        for fname in (f"{stem}_{v}.png", f"{stem}_{v}.placeholder.png"):
            try:
                (AI_EDIT_OUTPUT_DIR / fname).unlink(missing_ok=True)
            except Exception:
                pass

    bundles = build_ai_edit_candidates_for_target(
        script_to_search_term=script_to_search_term,
        final_data=final_data,
        target_text=edit_text,
    )
    if not bundles:
        print(f"[regen] ai_edit produced nothing for '{edit_text[:60]}'")
        return None

    save_to_cache(bundles, cand_cache)  # keep resume in sync
    images = bundles[0].get("candidates", {}).get("images") or None
    if images:
        print(f"[regen] ai_edit '{edit_text[:50]}' → {len(images)} new option(s)")
    return images


def build_ai_edit_candidates_for_target(
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
    target_text: str,
) -> list[dict]:
    """
    Generate ai_edit image(s) for a SINGLE target ai_edit scene, returned in
    the load_stock_footage() shape so the same review GUI consumes them.

    CRITICAL: `final_data` must already hold the user's picks for every scene
    that PRECEDES `target_text` in script order — including earlier ai_edits.
    That's what makes chains work: edit N is built from the image the user
    actually CHOSE for the preceding AI scene (which may be edit N-1).

    Only the target is flagged is_edit=True, so generate_ai_edits produces
    exactly one scene's images; every other scene is offered purely as a
    potential walk-back base (is_ai_base + chosen_image).
    """
    from ai_edit import generate_ai_edits

    # Resolve each decided scene's CHOSEN image (first footage entry) to disk.
    chosen_by_text: dict[str, str | None] = {}
    for entry in final_data:
        footage = entry.get("footage") or []
        key = next(iter(footage[0]), None) if footage else None
        chosen_by_text[entry["script_text"]] = (
            _resolve_to_local_path(key) if key else None
        )

    # Ordered descriptors (dict order == script order). Collect preceding AI
    # images for optional context as we walk.
    ordered_scenes: list[dict] = []
    preceding_ai_images: list[str] = []  # resolved local paths, script order
    reached_target = False

    for text, data in script_to_search_term.items():
        st = data["search_type"]
        is_target = text == target_text
        chosen_local = chosen_by_text.get(text)

        ordered_scenes.append(
            {
                "script_text": text,
                "is_edit": is_target,  # ONLY the target
                "is_ai_base": st in AI_BASE_TYPES,
                "instruction": data["search_term"],
                "chosen_image": None if is_target else chosen_local,
            }
        )

        if is_target:
            reached_target = True
        elif not reached_target and st in AI_BASE_TYPES and chosen_local:
            preceding_ai_images.append(chosen_local)

    if not preceding_ai_images:
        print(
            f"[ai_edit] WARNING: no preceding stickman/ai_edit scene before "
            f"'{target_text[:60]}' — there's no base image to edit. Put a "
            f"stickman (or an earlier ai_edit) ahead of it in the script."
        )

    # Optional extra context: the N most recent preceding AI images, EXCLUDING
    # the immediate base (preceding_ai_images[-1]) which is already the edit
    # source. Flip to include the base by dropping the [:-1] slice.
    context_images: list[str] = []
    if AI_EDIT_CONTEXT_NUM_IMAGES > 0 and len(preceding_ai_images) > 1:
        context_images = preceding_ai_images[:-1][-AI_EDIT_CONTEXT_NUM_IMAGES:]

    for sc in ordered_scenes:
        if sc["is_edit"]:
            sc["context_images"] = context_images  # consumed by generate_ai_edits

    print(f"\n[ai_edit] building target '{target_text[:60]}'")
    base_name = Path(preceding_ai_images[-1]).name if preceding_ai_images else "NONE"
    print(
        f"[ai_edit]   preceding AI images: {len(preceding_ai_images)} (base = {base_name})"
    )
    print(f"[ai_edit]   context images passed: {len(context_images)}")
    for ci in context_images:
        print(f"[ai_edit]     context ← {Path(ci).name}")

    generated = generate_ai_edits(
        ordered_scenes,
        out_dir=AI_EDIT_OUTPUT_DIR,
        num_variants=AI_EDIT_NUM_VARIANTS,
    )  # { edit_text: [path, ...] }

    paths = [p for p in generated.get(target_text, []) if Path(p).exists()]
    if not paths:
        print(f"[ai_edit] WARNING: no images generated for '{target_text[:60]}'")
        return []

    scene_timings = _load_scene_timings()
    if target_text not in scene_timings:
        print(f"[ai_edit] FATAL: no timing for '{target_text[:60]}'")
        sys.exit(1)
    dur = round(float(scene_timings[target_text]), 3)

    history = _load_history()  # identity entries so lookups resolve
    for p in paths:
        history.setdefault(p, p)
    _save_history(history)

    # Pixellate the fresh fal output BEFORE review, so you review / hand-edit
    # the pixellated edit. Its BASE was the CHOSEN pixellated image of the
    # preceding AI scene (resolved from final_data above), so the whole chain
    # stays in the pixel look and reflects any manual fixes you painted in.
    pixel_images = _maybe_pixellate_entries(
        [{p: dur} for p in paths], MediaType.AI_EDIT
    )

    print(f"[ai_edit]   → {len(paths)} candidate image(s), {dur:.2f}s each")
    return [
        {
            "script_text": target_text,
            "candidates": {"videos": [], "images": pixel_images},
            "num_clips_needed": 1,
            "max_runtime_per_clip_seconds": dur,
        }
    ]


def run_ai_edit_stage(
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
) -> list[dict]:
    """
    Generate + review every ai_edit scene ONE AT A TIME, in script order.

    An ai_edit edits the image chosen for the nearest preceding AI scene, which
    may itself be an earlier ai_edit — so edit N can't be generated until the
    user has PICKED edit N-1. We therefore loop:

        for each ai_edit (script order):
            build candidates from the CURRENT final_data (has all prior picks)
            review it (blocking GUI, just this one scene)
            merge the pick back into final_data

    Handles a single ai_edit, scattered ai_edits, and arbitrarily long runs of
    consecutive ai_edits identically. Per-edit candidate + review-state files
    are scoped by script index, so a re-run resumes and reuses prior work.
    Delete those files (or the cache dir) to force regeneration.
    """
    edit_texts = [
        txt
        for txt, data in script_to_search_term.items()
        if data["search_type"] == MediaType.AI_EDIT
    ]

    print("\n" + "=" * 70)
    print(f"[ai_edit stage] {len(edit_texts)} ai_edit scene(s) to process")
    print("=" * 70)

    if not edit_texts:
        print("[ai_edit stage] no ai_edit scenes — skipping")
        return final_data

    by_script = {e["script_text"]: i for i, e in enumerate(final_data)}
    script_index = {txt: i for i, txt in enumerate(script_to_search_term)}

    for n, edit_text in enumerate(edit_texts, start=1):
        idx = script_index[edit_text]
        cand_cache = _edit_candidates_cache_file(idx)
        state_file = _edit_review_state_file(idx)

        print("\n" + "-" * 70)
        print(
            f"[ai_edit stage] ({n}/{len(edit_texts)}) scene #{idx}: '{edit_text[:60]}'"
        )
        print("-" * 70)

        # Build (or load) THIS edit's candidates from the up-to-date final_data.
        bundles = load_from_cache(cand_cache)
        if bundles:
            print(f"[ai_edit stage]   loaded {len(bundles)} cached bundle(s)")
        else:
            bundles = build_ai_edit_candidates_for_target(
                script_to_search_term=script_to_search_term,
                final_data=final_data,
                target_text=edit_text,
            )
            save_to_cache(bundles, cand_cache)

        if not bundles:
            print(f"[ai_edit stage]   WARNING: nothing generated — skipping")
            continue

        # Review THIS edit (blocking; returns after the user picks).
        print(f"[ai_edit stage]   launching review GUI for this edit...")

        def _regen_edit(
            script_text: str, _t=edit_text, _cc=cand_cache
        ) -> list[dict] | None:
            if script_text != _t:
                return None
            return _regenerate_ai_edit_scene(
                edit_text=_t,
                script_to_search_term=script_to_search_term,
                final_data=final_data,
                cand_cache=_cc,
            )

        edit_final, has_manual = run_media_review(
            candidates_data=bundles,
            history_file=str(HISTORY_FILE),
            review_state_file=state_file,
            cache_dir=_CACHE_DIR,
            regenerate_fn=_regen_edit,
            regenerable_texts={edit_text},
        )

        if has_manual:
            print(
                f"\n[ai_edit stage] Exiting for manual fixes (scene #{idx}). "
                f"Re-run to resume from here."
            )
            sys.exit(0)

        # Merge the pick so the NEXT edit can build on it.
        for e in edit_final:
            if e["script_text"] in by_script:
                final_data[by_script[e["script_text"]]]["footage"] = e["footage"]
            else:
                final_data.append(e)
                by_script[e["script_text"]] = len(final_data) - 1
            for item in e["footage"]:
                for path, trim in item.items():
                    print(
                        f"[ai_edit stage]   ✓ picked {Path(path).name} (trim {trim}s)"
                    )

        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)  # checkpoint each pick

    print("\n" + "=" * 70)
    print(f"[ai_edit stage] DONE — processed {len(edit_texts)} ai_edit scene(s)")
    print("=" * 70)
    return final_data


# ===========================================================================
# GENERATOR: JOINT SCENES
# ===========================================================================


def generate_joint_scenes(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],
    final_data: list[dict] | None = None,
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
    print(
        f"[joint scenes] script_to_search_term has {len(script_to_search_term)} entries"
    )
    print(f"[joint scenes] candidates_data has {len(candidates_data)} entries")
    print(f"[joint scenes] joint types registered: {[t.value for t in JOINT_TYPES]}")
    print("=" * 70)

    scene_timings = _load_scene_timings()

    candidates_by_text: dict[str, dict] = {c["script_text"]: c for c in candidates_data}
    candidates_by_stripped: dict[str, dict] = {
        c["script_text"].strip(): c for c in candidates_data
    }
    print(
        f"[joint scenes] candidates lookup built with {len(candidates_by_text)} entries"
    )

    # Map script_text -> the image the user CHOSE in review (url or local path).
    # The compositor uses this instead of candidate[0], so edited/regenerated
    # tiles flow through correctly.
    chosen_by_text: dict[str, str | None] = {}
    for entry in final_data or []:
        footage = entry.get("footage") or []
        key = next(iter(footage[0]), None) if footage else None
        chosen_by_text[entry["script_text"]] = key

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
        print(
            f"[joint scenes]   sorted[{i}]: pos={data['position']}, "
            f"type={data['search_type'].value}, script='{txt[:60]}...'"
        )

    # 2) Group consecutive joints by (same search_type + contiguous position).
    grouped_joint_scenes: list[list[tuple[str, SearchTermData]]] = []
    current_group: list[tuple[str, SearchTermData]] = []
    previous_scene_data = None

    for script_text, scene_data in joint_scenes:
        if not previous_scene_data:
            current_group.append((script_text, scene_data))
            previous_scene_data = scene_data
            continue

        same_type = scene_data["search_type"] == previous_scene_data["search_type"]
        next_position = (
            int(scene_data["position"]) == int(previous_scene_data["position"]) + 1
        )

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
        print(
            f"[joint scenes]   group {gi}: type={joint_type.value}, "
            f"positions={positions}, size={len(grp)}"
        )

    # 3) Generate each group + collect footage entries.
    script_text_to_footage_entries: dict[str, list[dict]] = {}

    for group_index, group in enumerate(grouped_joint_scenes):
        joint_type = group[0][1]["search_type"]
        print(
            f"\n[joint scenes] processing group {group_index}: "
            f"type={joint_type.value}, size={len(group)}"
        )

        # Look up layout for this joint type.
        layout_positions = JOINT_LAYOUT_POSITIONS.get(joint_type)
        if not layout_positions:
            print(
                f"[joint scenes] FATAL: no layout registered for "
                f"{joint_type.value} in JOINT_LAYOUT_POSITIONS"
            )
            sys.exit(1)

        # Per-joint-type rendering config. Add a `case` here when adding a
        # new joint layout (and an entry in JOINT_LAYOUT_POSITIONS).
        match joint_type:
            case MediaType.JOINT_3_ROW:
                box_percentage = 50
                transition = TRANSITION_RANDOM
                background_path = "_BACKGROUNDS/bg_crumpled_card.mp4"
                base_duration = JOINT_BASE_DURATION_FALLBACK_SEC
                remove_bg = True

            case MediaType.STICKMAN_JOINT_3_ROW:
                # Identical to JOINT_3_ROW; the ONLY difference is the tiles come
                # from the AI stickman generator instead of Pexels. The stickman
                # images are line art on forced-white backgrounds, so remove_bg
                # cuts the figure out onto the crumpled card. Flip to False if you
                # ever want the white kept.
                box_percentage = 50
                transition = TRANSITION_RANDOM
                background_path = "_BACKGROUNDS/bg_crumpled_card.mp4"
                base_duration = JOINT_BASE_DURATION_FALLBACK_SEC
                remove_bg = True

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
                print(
                    f"[joint scenes] FATAL: item_index {item_index} >= layout length {len(layout_positions)}"
                )
                sys.exit(1)

            matching_candidate = candidates_by_text.get(script_text)
            if not matching_candidate:
                matching_candidate = candidates_by_stripped.get(script_text.strip())

            if not matching_candidate:
                print(
                    f"[joint scenes] FATAL: no matching candidate for: '{script_text}'"
                )
                print(f"  HINT: delete {CANDIDATES_CACHE_FILE} and re-run to refresh.")
                sys.exit(1)

            image_candidates = matching_candidate.get("candidates", {}).get(
                "images", []
            )
            if not image_candidates:
                print(f"[joint scenes] FATAL: no image candidates for: '{script_text}'")
                sys.exit(1)

            # Prefer the reviewed pick; fall back to candidate[0] only if this
            # scene was never reviewed (shouldn't happen now joint scenes are
            # in stage-1 review).
            image_url = chosen_by_text.get(script_text) or ""
            if not image_url:
                first_image = image_candidates[0]
                image_url = next(iter(first_image), "")
                print(
                    f"[joint scenes]   no review pick for '{script_text[:50]}' "
                    f"— falling back to candidate[0]"
                )
            else:
                print(
                    f"[joint scenes]   using reviewed pick for "
                    f"'{script_text[:50]}': "
                    f"{Path(image_url).name if '/' in image_url else image_url}"
                )
            if not image_url:
                print(f"[joint scenes] FATAL: no image_url for '{script_text}'")
                sys.exit(1)

            # Resolve the candidate to an on-disk file. Pexels joint_3_row
            # candidates are URLs (download if not cached); stickman_joint
            # candidates are ALREADY-LOCAL AI tiles — never try to download a
            # local path (that's what produced the "No scheme supplied" error).
            local_path = _resolve_to_local_path(image_url)
            if not local_path and image_url.startswith(("http://", "https://")):
                local_path = _download_image(image_url)
            if not local_path:
                print(
                    f"[joint scenes] FATAL: could not resolve image to disk: {image_url}"
                )
                if not image_url.startswith(("http://", "https://")):
                    print(
                        f"  It's a LOCAL file that's gone — most likely the review-GUI"
                    )
                    print(
                        f"  cleanup deleted it because a STALE review decision (from when"
                    )
                    print(
                        f"  this scene had a different search_type) pointed elsewhere."
                    )
                    print(
                        f"  Delete {CANDIDATES_CACHE_FILE} and re-run to regenerate it."
                    )
                else:
                    print(
                        f"  HINT: delete {CANDIDATES_CACHE_FILE} and re-run to refresh."
                    )
                sys.exit(1)

            items.append(
                {
                    "path": local_path,
                    "position": layout_positions[item_index],
                    "scale-fit-box-percentage": box_percentage,
                    "transition": transition,
                    "removeBG": remove_bg,
                }
            )

        if not items:
            print(
                f"[joint scenes] FATAL: no items to composite for group {group_index}"
            )
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
    print(
        f"[joint scenes] DONE — produced footage entries for "
        f"{len(script_text_to_footage_entries)} stage(s)"
    )
    print("=" * 70)
    return script_text_to_footage_entries


# ===========================================================================
# GENERATOR: READ-OUT (KINETIC TYPOGRAPHY) SCENES
# ===========================================================================


def generate_read_out_scenes(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],  # unused — registry signature uniformity
    final_data: list[dict] | None = None,  # unused — registry signature uniformity
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
        (txt, data)
        for txt, data in script_to_search_term.items()
        if data["search_type"] == MediaType.READ_OUT
    ]

    if not read_outs:
        print("[read-out scenes] no read-out scenes — returning empty map")
        return {}

    print(f"[read-out scenes] found {len(read_outs)} read-out scene(s)")

    # Inputs we need from the audio-sync stage.
    scene_timings = _load_scene_timings()  # text → duration
    line_starts = load_json(TIMESTAMPS_ABSOLUTE_FILE)  # text → abs start

    # Optional precise per-word timings (Whisper word-level).
    precise: dict | None = None
    if Path(WORD_TIMINGS_FILE).exists():
        try:
            precise = json.loads(Path(WORD_TIMINGS_FILE).read_text())
            n_covered = sum(1 for txt, _ in read_outs if precise.get(txt))
            print(
                f"[read-out scenes] loaded precise word timings "
                f"({n_covered}/{len(read_outs)} read-out lines covered)"
            )
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
            print(
                f"[read-out scenes] WARNING: scene has zero/negative duration "
                f"({duration}s) — skipping '{script_text[:60]}'"
            )
            continue

        line_start = float(line_starts.get(script_text, 0.0))
        per_line_words = (precise or {}).get(script_text)

        # Build a safe filename. Strip non-alphanumerics, prefix with idx.
        safe_stem = (
            re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:50] or "scene"
        )
        output_path = str(output_dir / f"read_out_{idx:03d}_{safe_stem}.mp4")

        # Render slightly longer than the scene runtime so the stitcher's
        # trim never falls short due to libx264 keyframe alignment. The
        # extra footage (last word stationary) is invisible after trim.
        render_duration = duration + READ_OUT_RENDER_SAFETY_PAD_SEC

        print(
            f"\n[read-out scenes] [{idx + 1}/{len(read_outs)}] "
            f"'{script_text[:60]}{'...' if len(script_text) > 60 else ''}'"
        )
        print(f"[read-out scenes]   scene duration   = {duration:.3f}s")
        print(
            f"[read-out scenes]   render duration  = {render_duration:.3f}s "
            f"(+{READ_OUT_RENDER_SAFETY_PAD_SEC:.3f}s safety pad)"
        )
        print(f"[read-out scenes]   line_start (abs) = {line_start:.3f}s")
        print(
            f"[read-out scenes]   precise words    = "
            f"{'yes (' + str(len(per_line_words)) + ' words)' if per_line_words else 'no'}"
        )
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


def generate_map_scenes(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],  # unused — registry signature uniformity
    final_data: list[dict] | None = None,  # unused — registry signature uniformity
) -> dict[str, list[dict]]:
    """
    Render a highlighted map for every scene flagged MediaType.MAP and return a
    stitcher-ready map:

        { script_text: [ {local_mp4_path: trim_seconds} ], ... }

    The scene's `search_term` is treated as a PLACE NAME. GET_MAP geocodes it
    and decides what to draw:
      - a country   -> the whole world, with that country highlighted
      - a region    -> the parent country, with the region/state highlighted
      - a city/town -> the parent country, with a pin dropped on the place

    Each map renders to a PNG, then bakes into a STATIC MP4 (exactly like the
    text-overlay / manual-placement scenes) so the Ken Burns pass skips it and
    the composed map is never cropped. One entry per scene, trimmed to runtime.

    Like read-out scenes, map scenes need NO external candidates and NO review,
    so this generator is registered in LOCAL_FOOTAGE_GENERATORS and its output
    is appended into final_data by the generic merge step.
    """
    print("\n" + "=" * 70)
    print("[map scenes] STARTING generate_map_scenes")
    print("=" * 70)

    if not MAP_ENABLE:
        print("[map scenes] MAP_ENABLE is False — skipping")
        return {}

    map_scenes = [
        (txt, data)
        for txt, data in script_to_search_term.items()
        if data["search_type"] == MediaType.MAP
    ]
    if not map_scenes:
        print("[map scenes] no map scenes — returning empty map")
        return {}

    print(f"[map scenes] found {len(map_scenes)} map scene(s)")

    scene_timings = _load_scene_timings()

    output_dir = MAP_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[map scenes] output dir: {output_dir}")

    footage_map: dict[str, list[dict]] = {}

    for idx, (script_text, scene_data) in enumerate(map_scenes):
        place = scene_data["search_term"]

        if script_text not in scene_timings:
            print(f"[map scenes] FATAL: no timing for '{script_text[:80]}'")
            sys.exit(1)

        duration = float(scene_timings[script_text])
        if duration <= 0:
            print(
                f"[map scenes] WARNING: zero/negative duration "
                f"({duration}s) — skipping '{script_text[:60]}'"
            )
            continue

        safe_stem = (
            re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:50] or "scene"
        )
        png_path = str(output_dir / f"map_{idx:03d}_{safe_stem}.png")
        mp4_path = str(output_dir / f"map_{idx:03d}_{safe_stem}.mp4")

        print(
            f"\n[map scenes] [{idx + 1}/{len(map_scenes)}] "
            f"'{script_text[:60]}{'...' if len(script_text) > 60 else ''}'"
        )
        print(f"[map scenes]   place          = '{place}'")
        print(f"[map scenes]   scene duration = {duration:.3f}s")
        print(f"[map scenes]   -> {png_path}")

        rendered = get_map_image(place, png_path, cache_dir=MAP_GEOCODE_CACHE_DIR)
        if not rendered:
            print(
                f"[map scenes] FATAL: could not render a map for '{place}' "
                f"(scene '{script_text[:60]}')"
            )
            sys.exit(1)

        # Bake the still into a static MP4 so the Ken Burns pass skips it and
        # the composed map (whole-world highlight / pin) is never cropped.
        try:
            _render_image_to_static_mp4(rendered, duration, mp4_path)
        except Exception as exc:
            print(
                f"[map scenes] FATAL: static MP4 render failed for "
                f"'{script_text[:50]}': {exc}"
            )
            sys.exit(1)

        footage_map[script_text] = [{mp4_path: round(duration, 3)}]
        print(f"[map scenes]   ✓ done — stitcher trim = {round(duration, 3)}s")

    print("\n" + "=" * 70)
    print(f"[map scenes] DONE — produced {len(footage_map)} map scene(s)")
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
        (txt, data)
        for txt, data in script_to_search_term.items()
        if data["search_type"] in STICKMAN_EXPLAIN_TYPES
    ]
    if not explain_scenes:
        print("[explain scenes] no explainer scenes — returning empty map")
        return {}

    print(f"[explain scenes] found {len(explain_scenes)} explainer scene(s)")

    # Lazy import keeps the PIL/ffmpeg-only dep out of runs that don't use it.
    from MAKE_EXPLAINER_IMAGE import make_explainer

    scene_timings = _load_scene_timings()

    # Map script_text -> the LOCAL PATH of the clip the user CHOSE in review.
    # IMPORTANT: unlike the joint compositor (which resolves/downloads URLs
    # itself further down), make_explainer needs an on-disk file — so we MUST
    # resolve the key (URL → local via history.json) right here.
    chosen_by_text: dict[str, str | None] = {}
    for entry in final_data or []:
        footage = entry.get("footage") or []
        key = next(iter(footage[0]), None) if footage else None
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
            print(
                f"[explain scenes] FATAL: no chosen footage in final_data for "
                f"'{script_text[:70]}' — was it picked in review?"
            )
            sys.exit(1)

        if script_text not in scene_timings:
            print(f"[explain scenes] FATAL: no timing for '{script_text[:70]}'")
            sys.exit(1)

        duration = float(scene_timings[script_text])
        if duration <= 0:
            print(
                f"[explain scenes] WARNING: zero/negative duration "
                f"({duration}s) — skipping '{script_text[:60]}'"
            )
            continue

        render_duration = duration + STICKMAN_EXPLAIN_RENDER_SAFETY_PAD_SEC

        safe_stem = (
            re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:50] or "scene"
        )
        output_path = str(out_dir / f"explain_{idx:03d}_{safe_stem}.mp4")

        print(
            f"\n[explain scenes] [{idx + 1}/{len(explain_scenes)}] "
            f"'{script_text[:60]}{'...' if len(script_text) > 60 else ''}'"
        )
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


def generate_text_overlay_scenes(
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
) -> dict[str, list[dict]]:
    """
    For every MediaType.STICKMAN_TEXT_OVERLAY scene, composite a tilted
    Fireship-style caption (the scene's search_term) onto the PREVIOUS scene's
    chosen image, returning a stitcher-ready map:

        { script_text: [ {local_mp4_path: trim_seconds} ], ... }

    "Previous image" = the nearest preceding scene that is NOT itself a
    text-overlay and whose final footage resolves to an image (or a video,
    whose first frame is used). Output is a STATIC MP4 so the Ken Burns pass
    skips it and the tilted caption is never cropped/zoomed.

    NOT in LOCAL_FOOTAGE_GENERATORS: it needs the post-review picks
    (final_data), not the raw candidates.
    """
    print("\n" + "=" * 70)
    print("[text-overlay] STARTING generate_text_overlay_scenes")
    print("=" * 70)

    overlay_scenes = [
        (txt, data)
        for txt, data in script_to_search_term.items()
        if data["search_type"] in STICKMAN_TEXT_OVERLAY_TYPES
    ]
    if not overlay_scenes:
        print("[text-overlay] no text-overlay scenes — returning empty map")
        return {}

    print(f"[text-overlay] found {len(overlay_scenes)} text-overlay scene(s)")

    from MAKE_TEXT_OVERLAY import make_text_overlay  # lazy import

    scene_timings = _load_scene_timings()
    ordered_texts = list(script_to_search_term.keys())
    final_by_text = {e["script_text"]: e for e in final_data}

    def _resolve_base_for(idx: int) -> str | None:
        """Nearest preceding non-overlay scene's resolved local image/video."""
        for j in range(idx - 1, -1, -1):
            prev_text = ordered_texts[j]
            if (
                script_to_search_term[prev_text]["search_type"]
                in STICKMAN_TEXT_OVERLAY_TYPES
            ):
                continue  # skip other captions
            footage = (final_by_text.get(prev_text) or {}).get("footage") or []
            if not footage:
                continue
            key = next(iter(footage[0]), None)  # url or local path
            local = _resolve_to_local_path(key) if key else None
            if local:
                print(
                    f"[text-overlay]   base for '{ordered_texts[idx][:45]}' "
                    f"← '{prev_text[:45]}' ({Path(local).name})"
                )
                return local
        print(
            f"[text-overlay]   WARNING: no prior image for "
            f"'{ordered_texts[idx][:45]}' — using a plain background"
        )
        return None

    out_dir = STICKMAN_TEXT_OVERLAY_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    footage_map: dict[str, list[dict]] = {}
    for txt, data in overlay_scenes:
        idx = ordered_texts.index(txt)
        base_local = _resolve_base_for(idx)

        if txt not in scene_timings:
            print(f"[text-overlay] FATAL: no timing for '{txt[:60]}'")
            sys.exit(1)
        duration = float(scene_timings[txt])
        if duration <= 0:
            print(
                f"[text-overlay] WARNING: zero/negative duration — skipping "
                f"'{txt[:60]}'"
            )
            continue

        render_duration = duration + STICKMAN_TEXT_OVERLAY_RENDER_SAFETY_PAD_SEC
        safe_stem = re.sub(r"[^a-zA-Z0-9]+", "_", txt).strip("_")[:50] or "scene"
        output_path = str(out_dir / f"text_overlay_{idx:03d}_{safe_stem}.mp4")

        try:
            make_text_overlay(
                base_image_path=base_local or "",
                text=data["search_term"],  # the caption text
                output_path=output_path,
                duration=render_duration,
                seed=txt,  # deterministic position/tilt per scene
            )
        except Exception as exc:
            print(f"[text-overlay] FATAL: render failed for '{txt[:50]}': {exc}")
            sys.exit(1)

        footage_map[txt] = [{output_path: round(duration, 3)}]
        print(
            f"[text-overlay]   ✓ '{txt[:50]}' → {Path(output_path).name} "
            f"(trim {round(duration, 3)}s)"
        )

    print("\n" + "=" * 70)
    print(f"[text-overlay] DONE — produced {len(footage_map)} scene(s)")
    print("=" * 70)
    return footage_map


def _extract_frame_at_timestamp(
    video_path: str, timestamp_sec: float, output_png: str
) -> str:
    """Grab a single frame from `video_path` at `timestamp_sec` (the moment the
    clip stops being shown) and write it to `output_png`. Used by
    static_of_previous to freeze the last *played* frame of the previous
    scene's video. Seeks just inside the cut so we never run past the clip."""
    import shlex

    vp = Path(video_path)
    if not vp.exists():
        raise RuntimeError(f"video does not exist: {video_path}")

    # Land just inside the played window. -ss AFTER -i = accurate (decoded) seek.
    ts = max(0.0, float(timestamp_sec) - 0.05)
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "warning",
        "-i",
        video_path,
        "-ss",
        f"{ts:.3f}",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        output_png,
    ]
    if DEBUG:
        print(f"[static-prev:ffmpeg]   {shlex.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stderr.strip():
        print(f"[static-prev:ffmpeg]   stderr:\n{result.stderr.rstrip()}")

    out = Path(output_png)
    if result.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        # Fallback: some short/odd clips fail an interior seek — grab the very
        # last frame from end-of-file instead.
        cmd_eof = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-loglevel",
            "warning",
            "-sseof",
            "-0.1",
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            output_png,
        ]
        if DEBUG:
            print(
                f"[static-prev:ffmpeg]   interior seek failed — trying EOF: "
                f"{shlex.join(cmd_eof)}"
            )
        result = subprocess.run(cmd_eof, capture_output=True, text=True)
        if result.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(
                f"frame extraction failed for {video_path} @ {ts:.3f}s "
                f"(returncode={result.returncode})"
            )
    return output_png


def _render_image_to_static_mp4(
    image_path: str, duration: float, output_path: str
) -> str:
    """Bake a still into a silent, perfectly static H.264 MP4 of `duration`s
    (+ a tiny safety pad the stitcher trims). Output is forced to EVEN
    dimensions (libx264/yuv420p requires it — odd dims are what made the
    encoder fail) and verbosely logged so any failure is diagnosable."""
    import shlex

    from PIL import Image as _PILImage

    img_path = Path(image_path)
    if not img_path.exists():
        raise RuntimeError(f"input image does not exist: {image_path}")
    img_bytes = img_path.stat().st_size
    try:
        with _PILImage.open(image_path) as _im:
            iw, ih = _im.size
            imode = _im.mode
    except Exception as exc:
        raise RuntimeError(f"could not open input image {image_path}: {exc}")

    render_duration = duration + MANUAL_STOCK_PLACEMENT_RENDER_SAFETY_PAD_SEC
    even_w, even_h = (iw // 2) * 2, (ih // 2) * 2
    is_even = iw % 2 == 0 and ih % 2 == 0

    print(f"[manual-place:ffmpeg] input  = {image_path}")
    print(
        f"[manual-place:ffmpeg]   exists={img_path.exists()} size={img_bytes}B "
        f"dims={iw}x{ih} mode={imode} "
        f"({'even' if is_even else 'ODD -> scaling to even'})"
    )
    print(
        f"[manual-place:ffmpeg]   duration={duration:.3f}s "
        f"pad={MANUAL_STOCK_PLACEMENT_RENDER_SAFETY_PAD_SEC:.3f}s "
        f"render={render_duration:.3f}s fps={KEN_BURNS_FPS}"
    )
    print(f"[manual-place:ffmpeg]   target dims (even) = {even_w}x{even_h}")

    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "warning",
        "-loop",
        "1",
        "-framerate",
        str(KEN_BURNS_FPS),
        "-i",
        image_path,
        "-t",
        f"{render_duration:.3f}",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # <- the fix: even dims
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-r",
        str(KEN_BURNS_FPS),
        "-an",
        output_path,
    ]
    print(f"[manual-place:ffmpeg]   cmd: {shlex.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stderr.strip():
        print(f"[manual-place:ffmpeg]   stderr:\n{result.stderr.rstrip()}")
    print(f"[manual-place:ffmpeg]   returncode={result.returncode}")

    out_path = Path(output_path)
    out_size = out_path.stat().st_size if out_path.exists() else 0
    print(
        f"[manual-place:ffmpeg]   output = {output_path} "
        f"exists={out_path.exists()} size={out_size}B"
    )

    if result.returncode != 0 or out_size == 0:
        raise RuntimeError(
            f"static MP4 render failed for {image_path} "
            f"(returncode={result.returncode}, output_size={out_size}B). "
            f"See ffmpeg stderr above."
        )
    return output_path


def run_manual_image_stage(
    script_to_search_term: dict[str, "SearchTermData"],
    final_data: list[dict],
) -> list[dict]:
    """
    Single script-order pass over all "derive-from-previous" image scenes:
      • MANUAL_STOCK_ADD_TO_PREVIOUS — composite this scene's chosen still onto
        the PREVIOUS scene's image at a clicked position/size.
      • ZOOM_PREV_IMG — crop/zoom into the PREVIOUS scene's image.
      • STATIC_OF_PREVIOUS — reuse the PREVIOUS scene's image as-is, OR freeze
        the last *played* frame of the PREVIOUS scene's last video clip
        (timestamp from the JSON-derived trim). Non-interactive.

    All processed together, ONE AT A TIME, merging each result back into
    final_data before the next, so any mix chains correctly (zoom into a
    composite, place onto a zoom, freeze a video then zoom the frozen frame,
    ...). "Previous" = the nearest preceding scene that resolves to usable
    footage (videos → a frame). Runs after stage-1 review + the
    explainer/text-overlay generators and before Ken Burns (output is a static
    MP4 → KB skips it).

    Resume: placement_<idx>.json / crop_<idx>.json are reused without
    re-opening the GUI; delete one to redo that scene. STATIC_OF_PREVIOUS has
    no GUI, so it's just recomputed each run (cheap + deterministic).
    """
    manual_set = MANUAL_STOCK_ADD_TYPES | ZOOM_PREV_TYPES | STATIC_OF_PREVIOUS_TYPES
    ordered_texts = list(script_to_search_term.keys())
    manual_texts = [
        t
        for t in ordered_texts
        if script_to_search_term[t]["search_type"] in manual_set
    ]

    print("\n" + "=" * 70)
    print(f"[manual-img] {len(manual_texts)} derive-from-previous scene(s) to process")
    print("=" * 70)
    if not manual_texts:
        print("[manual-img] none — skipping")
        return final_data

    from MANUAL_STOCK_PLACEMENT import (
        CropBox,
        Placement,
        composite_overlays,
        crop_and_zoom,
        extract_frame,
        place_overlays_interactive,
        zoom_prev_interactive,
    )

    scene_timings = _load_scene_timings()
    by_script = {e["script_text"]: i for i, e in enumerate(final_data)}
    script_index = {txt: i for i, txt in enumerate(script_to_search_term)}

    out_dir = MANUAL_STOCK_PLACEMENT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    def _dims(p: str) -> str:
        try:
            from PIL import Image as _I

            with _I.open(p) as im:
                return f"{im.size[0]}x{im.size[1]}"
        except Exception:
            return "?x?"

    def _resolve_scene_still(text: str) -> str | None:
        entry = next((e for e in final_data if e["script_text"] == text), None)
        footage = (entry or {}).get("footage") or []
        key = next(iter(footage[0]), None) if footage else None
        if not key:
            return None
        local = _resolve_to_local_path(key)
        if not local:
            return None
        if _classify_footage_path(local) == "video":
            frame_png = (
                out_dir / f"frame_{hashlib.md5(local.encode()).hexdigest()[:12]}.png"
            )
            if not (frame_png.exists() and frame_png.stat().st_size > 1024):
                try:
                    extract_frame(local, str(frame_png))
                except Exception as exc:
                    print(
                        f"[manual-img] WARNING: frame extract failed for "
                        f"{Path(local).name}: {exc}"
                    )
                    return None
            return str(frame_png) if frame_png.exists() else None
        return local

    def _resolve_base(idx: int) -> tuple[str | None, str | None]:
        for j in range(idx - 1, -1, -1):
            still = _resolve_scene_still(ordered_texts[j])
            if still:
                return still, ordered_texts[j]
        return None, None

    def _resolve_static_source(idx: int) -> tuple[str | None, str | None]:
        """For STATIC_OF_PREVIOUS: walk back to the nearest scene with footage,
        then reuse its image as-is, or freeze the last *played* frame of its
        last video clip (timestamp = that clip's trim, from the JSON timings)."""
        for j in range(idx - 1, -1, -1):
            prev_text = ordered_texts[j]
            entry = next((e for e in final_data if e["script_text"] == prev_text), None)
            footage = (entry or {}).get("footage") or []
            if not footage:
                continue
            last_path, last_trim = next(
                iter(footage[-1].items())
            )  # LAST clip of prev scene
            local = _resolve_to_local_path(last_path)
            if not local:
                continue

            if _classify_footage_path(local) == "video":
                key = f"{local}|{round(float(last_trim), 3)}"
                freeze_png = (
                    out_dir
                    / f"static_src_{hashlib.md5(key.encode()).hexdigest()[:12]}.png"
                )
                if not (freeze_png.exists() and freeze_png.stat().st_size > 1024):
                    try:
                        _extract_frame_at_timestamp(
                            local, float(last_trim), str(freeze_png)
                        )
                    except Exception as exc:
                        print(
                            f"[manual-img] WARNING: freeze-frame failed for "
                            f"{Path(local).name}: {exc}"
                        )
                        continue
                print(
                    f"[manual-img]   static source ← '{prev_text[:45]}' "
                    f"(VIDEO {Path(local).name}, freeze @ {float(last_trim):.2f}s)"
                )
                return str(freeze_png), prev_text

            print(
                f"[manual-img]   static source ← '{prev_text[:45]}' "
                f"(IMAGE {Path(local).name}, reused as-is)"
            )
            return local, prev_text
        return None, None

    for n, text in enumerate(manual_texts, start=1):
        idx = script_index[text]
        stype = script_to_search_term[text]["search_type"]
        kind = (
            "static"
            if stype in STATIC_OF_PREVIOUS_TYPES
            else "zoom"
            if stype in ZOOM_PREV_TYPES
            else "place"
        )

        print("\n" + "-" * 70)
        print(
            f"[manual-img] ({n}/{len(manual_texts)}) [{kind}] scene #{idx}: '{text[:55]}'"
        )
        print("-" * 70)

        if text not in scene_timings:
            print(f"[manual-img] FATAL: no timing for '{text[:60]}'")
            sys.exit(1)
        duration = float(scene_timings[text])
        if duration <= 0:
            print(f"[manual-img] WARNING: zero duration — skipping '{text[:55]}'")
            continue

        safe_stem = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:50] or "scene"
        result_png = out_dir / f"manual_{kind}_{idx:03d}_{safe_stem}.png"
        output_mp4 = out_dir / f"manual_{kind}_{idx:03d}_{safe_stem}.mp4"
        print(f"[manual-img]   duration = {duration:.3f}s")

        if kind == "static":
            # Non-interactive: reuse prev image, or freeze prev video's last frame.
            src_still, _src_text = _resolve_static_source(idx)
            if not src_still:
                print(
                    f"[manual-img] FATAL: no preceding media to derive a still "
                    f"from for '{text[:60]}'. static_of_previous needs a normal "
                    f"image/video scene before it."
                )
                sys.exit(1)
            try:
                shutil.copyfile(src_still, str(result_png))
            except Exception as exc:
                print(
                    f"[manual-img] FATAL: couldn't stage still from "
                    f"{Path(src_still).name}: {exc}"
                )
                sys.exit(1)

        else:
            # place & zoom both use the PREVIOUS scene's image as the backdrop.
            base_still, base_text = _resolve_base(idx)
            if not base_still:
                print(
                    f"[manual-img] FATAL: no preceding image for '{text[:60]}'. "
                    f"This type needs a normal scene before it whose image is "
                    f"the backdrop."
                )
                sys.exit(1)
            print(
                f"[manual-img]   base <- '{base_text[:50]}' "
                f"({Path(base_still).name}, {_dims(base_still)})"
            )

            if kind == "place":
                overlay_still = _resolve_scene_still(text)
                if not overlay_still:
                    print(
                        f"[manual-img] FATAL: no chosen stock for '{text[:60]}'. Its "
                        f"overlay is picked in stage-1 review — did you select one? "
                        f"(Delete {CANDIDATES_CACHE_FILE} and re-run if stale.)"
                    )
                    sys.exit(1)
                print(
                    f"[manual-img]   overlay = {Path(overlay_still).name} "
                    f"({_dims(overlay_still)})"
                )

                state_file = out_dir / f"placement_{idx:03d}.json"
                placements = None
                if state_file.exists():
                    try:
                        d = json.loads(state_file.read_text())
                        remove_bg = bool(d.get("remove_bg", True))
                        # new format: {"remove_bg":.., "placements":[{...}, ...]}
                        # old format (single stamp): {"width_pct":.., "cx_frac":.., ...}
                        raw = d["placements"] if "placements" in d else [d]
                        placements = [
                            Placement(
                                int(r["width_pct"]),
                                float(r["cx_frac"]),
                                float(r["cy_frac"]),
                                remove_bg,
                            )
                            for r in raw
                        ]
                        print(
                            f"[manual-img]   resume: reusing {len(placements)} saved "
                            f"placement(s)"
                        )
                    except Exception as exc:
                        print(
                            f"[manual-img]   couldn't read {state_file.name} ({exc}); "
                            f"re-opening GUI"
                        )
                        placements = None
                if placements is None:
                    placements = place_overlays_interactive(
                        base_image_path=base_still,
                        overlay_image_path=overlay_still,
                        window_title=(
                            f"Place '{script_to_search_term[text]['search_term']}' "
                            f"(scene {n}/{len(manual_texts)})"
                        ),
                    )
                    if not placements:
                        print(
                            f"\n[manual-img] Exited without placing scene #{idx}. "
                            f"Re-run to resume."
                        )
                        sys.exit(0)
                    state_file.write_text(
                        json.dumps(
                            {
                                "remove_bg": placements[0].remove_bg,
                                "placements": [
                                    {
                                        "width_pct": p.width_pct,
                                        "cx_frac": p.cx_frac,
                                        "cy_frac": p.cy_frac,
                                    }
                                    for p in placements
                                ],
                            },
                            indent=2,
                        )
                    )
                print(f"[manual-img]   stamps = {len(placements)}")
                try:
                    composite_overlays(
                        base_still, overlay_still, placements, str(result_png)
                    )
                except Exception as exc:
                    print(f"[manual-img] FATAL: composite failed: {exc}")
                    sys.exit(1)

            else:  # zoom
                state_file = out_dir / f"crop_{idx:03d}.json"
                crop = None
                if state_file.exists():
                    try:
                        d = json.loads(state_file.read_text())
                        crop = CropBox(
                            int(d["width_pct"]),
                            float(d["cx_frac"]),
                            float(d["cy_frac"]),
                        )
                        print(f"[manual-img]   resume: reusing saved crop box")
                    except Exception as exc:
                        print(
                            f"[manual-img]   couldn't read {state_file.name} ({exc}); re-opening GUI"
                        )
                        crop = None
                if crop is None:
                    crop = zoom_prev_interactive(
                        base_image_path=base_still,
                        window_title=f"Zoom into previous image (scene {n}/{len(manual_texts)})",
                    )
                    if crop is None:
                        print(
                            f"\n[manual-img] Exited without zooming scene #{idx}. Re-run to resume."
                        )
                        sys.exit(0)
                    state_file.write_text(
                        json.dumps(
                            {
                                "width_pct": crop.width_pct,
                                "cx_frac": crop.cx_frac,
                                "cy_frac": crop.cy_frac,
                            },
                            indent=2,
                        )
                    )
                try:
                    crop_and_zoom(base_still, crop, str(result_png))
                except Exception as exc:
                    print(f"[manual-img] FATAL: crop/zoom failed: {exc}")
                    sys.exit(1)

        print(
            f"[manual-img]   result = {Path(result_png).name} ({_dims(str(result_png))})"
        )
        try:
            _render_image_to_static_mp4(str(result_png), duration, str(output_mp4))
        except Exception as exc:
            print(f"[manual-img] FATAL: MP4 render failed: {exc}")
            sys.exit(1)

        entries = [{str(output_mp4): round(duration, 3)}]
        if text in by_script:
            final_data[by_script[text]]["footage"] = entries
        else:
            final_data.append({"script_text": text, "footage": entries})
            by_script[text] = len(final_data) - 1
        _add_local_paths_to_history({text: entries})
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"[manual-img]   OK {Path(output_mp4).name} (trim {round(duration, 3)}s)")

    print("\n" + "=" * 70)
    print(f"[manual-img] DONE — processed {len(manual_texts)} scene(s)")
    print("=" * 70)
    return final_data


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

LOCAL_FOOTAGE_GENERATORS: dict[
    str,
    Callable[
        [dict[str, SearchTermData], list[dict]],
        dict[str, list[dict]],
    ],
] = {
    "joint": generate_joint_scenes,  # handles every type in JOINT_TYPES
    "read_out": generate_read_out_scenes,  # handles MediaType.READ_OUT
    "map": generate_map_scenes,  # handles MediaType.MAP
}


def run_all_local_generators(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],
    final_data: list[dict] | None = None,
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
            produced = generator(script_to_search_term, candidates_data, final_data)
        except Exception as exc:
            print(f"[generators] FATAL: {generator.__name__} raised: {exc}")
            raise

        for script_text in produced:
            if script_text in combined:
                print(
                    f"[generators] WARNING: '{script_text[:60]}' already produced by "
                    f"another generator — overwriting with {name}"
                )
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
    ZOOM_IN_CENTER = "zoom_in_center"
    ZOOM_OUT_CENTER = "zoom_out_center"
    PAN_LEFT_TO_RIGHT = "pan_left_to_right"
    PAN_RIGHT_TO_LEFT = "pan_right_to_left"
    TILT_BOTTOM_TO_TOP = "tilt_bottom_to_top"
    TILT_TOP_TO_BOTTOM = "tilt_top_to_bottom"
    ZOOM_IN_PAN_LR = "zoom_in_pan_lr"
    ZOOM_IN_PAN_RL = "zoom_in_pan_rl"
    ZOOM_OUT_PAN_LR = "zoom_out_pan_lr"
    ZOOM_OUT_PAN_RL = "zoom_out_pan_rl"


# Probabilities must sum to ~1.0. random.choices handles normalisation
# internally so small rounding is fine.
KEN_BURNS_EFFECT_PROBABILITIES: dict[KenBurnsEffect, float] = {
    KenBurnsEffect.ZOOM_IN_CENTER: 0.28,
    KenBurnsEffect.ZOOM_OUT_CENTER: 0.22,
    KenBurnsEffect.PAN_LEFT_TO_RIGHT: 0.14,
    KenBurnsEffect.PAN_RIGHT_TO_LEFT: 0.12,
    KenBurnsEffect.TILT_BOTTOM_TO_TOP: 0.06,
    KenBurnsEffect.TILT_TOP_TO_BOTTOM: 0.05,
    KenBurnsEffect.ZOOM_IN_PAN_LR: 0.04,
    KenBurnsEffect.ZOOM_IN_PAN_RL: 0.04,
    KenBurnsEffect.ZOOM_OUT_PAN_LR: 0.03,
    KenBurnsEffect.ZOOM_OUT_PAN_RL: 0.02,
}

# Rendering parameters — tweak these to taste.
KEN_BURNS_OUTPUT_RESOLUTION: tuple[int, int] = (1920, 1080)
KEN_BURNS_WORKING_RESOLUTION: tuple[int, int] = (4000, 2250)  # 1.78 aspect, oversampled
KEN_BURNS_ZOOM_DELTA: float = 0.05  # 5% of frame
KEN_BURNS_PAN_DELTA: float = 0.05  # 5% of working dim
KEN_BURNS_FPS: int = 30
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
    out_w, out_h = KEN_BURNS_OUTPUT_RESOLUTION
    over_w, over_h = KEN_BURNS_WORKING_RESOLUTION
    fps = KEN_BURNS_FPS
    z_delta = KEN_BURNS_ZOOM_DELTA  # 0.05
    pan_z = 1 + KEN_BURNS_PAN_DELTA  # 1.05 — baseline zoom
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
    cx, cy = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    max_x, max_y = "(iw-iw/zoom)", "(ih-ih/zoom)"

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
    prep = (
        f"scale={over_w}:{over_h}:force_original_aspect_ratio=increase,"
        f"crop={over_w}:{over_h},setsar=1"
    )

    # d=1 → each input frame produces exactly 1 output frame. Combined with
    # `-loop 1 -framerate fps -i image -t duration` at the CLI level, this
    # gives us a clean monotonic `on` from 0 to duration*fps.
    zp = f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={out_w}x{out_h}:fps={fps}"

    return f"{prep},{zp}"


def _ken_burns_cache_path(
    image_path: str, effect: KenBurnsEffect, duration: float
) -> Path:
    """Stable cache filename keyed on (image, effect, duration)."""
    key = f"{image_path}|{effect.value}|{round(duration, 3)}"
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    return KEN_BURNS_CACHE_DIR / f"kb-{h}.mp4"


def _render_ken_burns_clip(
    image_path: str, effect: KenBurnsEffect, duration: float
) -> str:
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
    filter_str = _build_ken_burns_filter(effect, duration)  # ← was render_duration

    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(KEN_BURNS_FPS),
        "-i",
        image_path,
        "-t",
        f"{render_duration:.3f}",
        "-vf",
        filter_str,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-r",
        str(KEN_BURNS_FPS),
        "-an",
        str(output_path),
    ]

    if DEBUG:
        print(
            f"  [ken-burns render] {Path(image_path).name} "
            f"effect={effect.value} dur={duration:.2f}s -> {output_path.name}"
        )

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
                    print(
                        f"[ken-burns]   (not a URL in history.json AND not a "
                        f"valid local path) — keeping original entry"
                    )
                    new_item[path] = trim
                    n_skipped_missing += 1
                    tracker.tick()
                    continue

                duration = float(trim)
                effect = _pick_ken_burns_effect(path)  # seed on original key
                try:
                    mp4_path = _render_ken_burns_clip(local_path, effect, duration)
                except Exception as exc:
                    print(
                        f"\n[ken-burns] ERROR rendering {local_path}: {exc} "
                        f"— keeping original entry"
                    )
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
    print(
        f"[ken-burns] DONE — rendered={n_rendered}, "
        f"skipped_missing={n_skipped_missing}, failed={n_failed}"
    )
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
# PIXELLATION (RETRO LOOK FOR AI-GENERATED IMAGES)
# ===========================================================================
# Runs AFTER both review stages and BEFORE the local generators + Ken Burns.
#   - WHY after ai_edit: an ai_edit edits the CHOSEN preceding AI image (and
#     edits chain). Pixellate before the edit and fal redraws on top of pixels.
#     So we wait until ALL editing is done, then pixellate the results.
#   - WHY before the generators: stickman_joint tiles must be pixellated BEFORE
#     generate_joint_scenes bakes them into the collage MP4. That generator
#     reads the chosen image straight from final_data, so replacing the path
#     here is all it takes — it then tiles the pixellated version.

PIXELLATE_AI_IMAGES: bool = True

# Which MediaTypes get pixellated — the "genuinely AI-generated still" types.
# NOTE on STICKMAN_JOINT_3_ROW: those tiles are line art on a forced-white
# background that the compositor keys out (removeBG=True). Pixellation averages
# colours, so near-white can drift slightly off-white and leave a faint fringe
# after keying. If that looks bad, just drop STICKMAN_JOINT_3_ROW from this set.
PIXELLATE_AI_TYPES: set[MediaType] = {
    MediaType.STICKMAN,
    MediaType.AI_EDIT,
    MediaType.STICKMAN_JOINT_3_ROW,
}

# Forwarded to pixellate_image. Smaller grid = chunkier pixels.
PIXELLATE_GRID_WIDTH: int = 400
PIXELLATE_GRID_HEIGHT: int = 200
PIXELLATE_TOLERANCE: int = 80

PIXELLATE_CACHE_DIR = Path(f"{_CACHE_DIR}/pixellated")


def _pixellate_cache_path(image_path: str) -> Path:
    """Stable cache filename keyed on (image, grid, tolerance)."""
    key = f"{image_path}|{PIXELLATE_GRID_WIDTH}x{PIXELLATE_GRID_HEIGHT}|{PIXELLATE_TOLERANCE}"
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    return PIXELLATE_CACHE_DIR / f"px-{h}.png"


def _maybe_pixellate_entries(
    image_entries: list[dict],
    search_type: MediaType | None,
) -> list[dict]:
    """
    Pixellate every image path in a list of {path: trim} candidate entries,
    returning NEW entries that point at the pixellated copies. Originals are
    left untouched on disk; identity history entries are registered so the
    review GUI + stitcher resolve the new PNGs.

    Key properties:
      - No-op when pixellation is disabled OR the scene's search_type isn't a
        pixellated type — callers can pass anything and let this decide.
      - Cached + idempotent: if the px copy already exists it is REUSED (not
        re-rendered), so a HAND-EDITED px file survives re-runs, and a path
        that's already a px copy is passed straight through (no double-pixel).
    """
    if not PIXELLATE_AI_IMAGES or search_type not in PIXELLATE_AI_TYPES:
        return image_entries
    if not image_entries:
        return image_entries

    PIXELLATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    history = _load_history()
    out: list[dict] = []
    changed = False

    for entry in image_entries:
        new_entry: dict = {}
        for path, trim in entry.items():
            if not _is_image_path(path):
                new_entry[path] = trim  # videos / other → leave
                continue

            local_path = _resolve_to_local_path(path)
            if not local_path:
                print(
                    f"[pixellate] WARNING: can't resolve to disk: {path} "
                    f"— keeping original"
                )
                new_entry[path] = trim
                continue

            # Already a pixellated copy? don't pixellate the pixels again.
            if str(PIXELLATE_CACHE_DIR) in local_path:
                new_entry[path] = trim
                continue

            out_path = _pixellate_cache_path(local_path)
            if out_path.exists() and out_path.stat().st_size > 1024:
                if DEBUG:
                    print(f"  [pixellate cache hit] {out_path.name}")
            else:
                try:
                    pixellate_image(
                        input_path=local_path,
                        output_path=str(out_path),
                        target_width=PIXELLATE_GRID_WIDTH,
                        target_height=PIXELLATE_GRID_HEIGHT,
                        tolerance=PIXELLATE_TOLERANCE,
                    )
                except Exception as exc:
                    print(
                        f"[pixellate] ERROR pixellating {local_path}: {exc} "
                        f"— keeping original"
                    )
                    new_entry[path] = trim
                    continue

            history.setdefault(str(out_path), str(out_path))
            new_entry[str(out_path)] = trim
            changed = True
        out.append(new_entry)

    if changed:
        _save_history(history)
    return out


def pixellate_candidate_bundles(
    bundles: list[dict],
    script_to_search_term: dict[str, SearchTermData],
) -> None:
    """
    Pixellate the AI-generated image candidates in `bundles` IN PLACE, BEFORE
    review — so the review GUI shows (and any manual fix paints onto) the
    pixellated versions. Bundles whose scene isn't a PIXELLATE_AI_TYPES type
    (stock / wiki / Pexels-joint) pass through untouched. Idempotent.
    """
    if not PIXELLATE_AI_IMAGES:
        return

    n = 0
    for bundle in bundles:
        st = script_to_search_term.get(bundle["script_text"], {}).get("search_type")
        if st not in PIXELLATE_AI_TYPES:
            continue
        imgs = (bundle.get("candidates") or {}).get("images") or []
        if not imgs:
            continue
        bundle["candidates"]["images"] = _maybe_pixellate_entries(imgs, st)
        n += 1

    if n:
        print(f"[pixellate] pre-review: pixellated AI candidates in {n} bundle(s)")


# ===========================================================================
# CINEMATIC COLOUR GRADING (unified "shot on film at golden hour" look)
# ===========================================================================
# Runs as a pass over final_data right BEFORE Ken Burns. Stills are graded once
# (one cheap ffmpeg image op) and then KB animates the already-graded still;
# stock videos + the stock/wiki explainer & joint composites + manual stock
# placements are graded in place. The whole look lives in COLOUR_GRADE_ETC.py
# (one ffmpeg filter chain, identical for images and videos) so every piece of
# stock ends up part of the same graded "collection".
#
# Scope:
#   TOGGLE_STOCK_COLOUR_GRADING_ETC=False  -> nothing is graded.
#   APPLY_COLOUR_GRADING_TO_ALL=True       -> EVERY scene is graded.
#   otherwise                              -> only "real-world stock" scenes,
#                                             i.e. COLOUR_GRADE_STOCK_TYPES.
#
# Graded output is cached by (source file, preset+algorithm fingerprint) so
# re-runs are instant and changing the preset/algorithm transparently re-grades.

# "Real-world stock" = every type that fetches external candidates (Pexels
# videos+images, Wikipedia stills, the stock/wiki explainer composites, joint
# collages built from stock, manual stock placement). Defined off
# NEEDS_EXTERNAL_CANDIDATES so any new stock type is covered automatically. AI
# stickman / ai_edit / read-out / maps / pure text overlays are intentionally
# excluded so the film look doesn't fight the illustrated/synthetic styling —
# flip APPLY_COLOUR_GRADING_TO_ALL to grade those too. To drop a specific stock
# type (e.g. JOINT_3_ROW), subtract it from this set.
COLOUR_GRADE_STOCK_TYPES: set[MediaType] = set(NEEDS_EXTERNAL_CANDIDATES)

COLOUR_GRADE_CACHE_DIR = Path(f"{_CACHE_DIR}/colour_graded")


def _colour_grade_cache_path(local_path: str, fingerprint: str, is_video: bool) -> Path:
    """Stable cache filename keyed on (source file, grade fingerprint)."""
    clean = local_path.split("?", 1)[0]
    ext = ".mp4" if is_video else (Path(clean).suffix.lower() or ".jpg")
    h = hashlib.md5(f"{local_path}|{fingerprint}".encode()).hexdigest()[:16]
    return COLOUR_GRADE_CACHE_DIR / f"cg-{fingerprint}-{h}{ext}"


def apply_colour_grading_to_final_data(
    final_data: list[dict],
    script_to_search_term: dict[str, SearchTermData],
) -> tuple[list[dict], dict[str, str]]:
    """
    Give the CHOSEN footage one unified cinematic film grade.

    Walks final_data and, for every grade-eligible scene, replaces each image /
    video footage entry with a graded copy (cached). Eligibility is decided per
    scene from its MediaType (COLOUR_GRADE_STOCK_TYPES / APPLY_COLOUR_GRADING_TO_ALL).

    Returns (final_data, path_remap) where path_remap is {old_key: graded_path}
    so the caller can register identity entries in history.json.
    """
    print("\n" + "=" * 70)
    print("[colour-grade] CINEMATIC GRADE over final_data")
    print(
        f"[colour-grade] enabled={TOGGLE_STOCK_COLOUR_GRADING_ETC} "
        f"all={APPLY_COLOUR_GRADING_TO_ALL} preset={STOCK_COLOUR_GRADE_PRESET!r}"
    )
    print("=" * 70)

    if not TOGGLE_STOCK_COLOUR_GRADING_ETC:
        print("[colour-grade] TOGGLE_STOCK_COLOUR_GRADING_ETC=False — skipping")
        return final_data, {}

    try:
        fingerprint = COLOUR_GRADE_ETC.preset_fingerprint(STOCK_COLOUR_GRADE_PRESET)
    except KeyError as exc:
        print(f"[colour-grade] {exc} — skipping (fix STOCK_COLOUR_GRADE_PRESET)")
        return final_data, {}

    def _eligible(script_text: str) -> bool:
        if APPLY_COLOUR_GRADING_TO_ALL:
            return True
        st = script_to_search_term.get(script_text, {}).get("search_type")
        return st in COLOUR_GRADE_STOCK_TYPES

    # Pre-scan so the progress bar has an accurate total.
    to_grade = 0
    for entry in final_data:
        if not _eligible(entry.get("script_text", "")):
            continue
        for footage_item in entry.get("footage", []):
            for path in footage_item:
                if _classify_footage_path(path) in ("image", "video"):
                    to_grade += 1

    if to_grade == 0:
        print("[colour-grade] no eligible footage in final_data — nothing to do")
        return final_data, {}

    print(
        f"[colour-grade] grading {to_grade} footage file(s)  "
        f"[stock types: {sorted(t.value for t in COLOUR_GRADE_STOCK_TYPES)}]"
    )
    COLOUR_GRADE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tracker = ProgressTracker(total=to_grade, label="COLOUR GRADE")
    path_remap: dict[str, str] = {}
    n_graded = n_cached = n_skipped = n_failed = 0

    for entry in final_data:
        eligible = _eligible(entry.get("script_text", ""))
        new_footage: list[dict] = []
        for footage_item in entry.get("footage", []):
            new_item: dict = {}
            for path, trim in footage_item.items():
                kind = _classify_footage_path(path)
                if not eligible or kind not in ("image", "video"):
                    new_item[path] = trim
                    continue

                local_path = _resolve_to_local_path(path)
                if not local_path:
                    print(
                        f"\n[colour-grade] WARNING: can't resolve to disk: {path} "
                        f"— keeping original"
                    )
                    new_item[path] = trim
                    n_skipped += 1
                    tracker.tick()
                    continue

                is_vid = kind == "video"
                out = _colour_grade_cache_path(local_path, fingerprint, is_vid)
                if out.exists() and out.stat().st_size > 1024:
                    n_cached += 1
                else:
                    try:
                        COLOUR_GRADE_ETC.grade_media(
                            local_path,
                            str(out),
                            preset=STOCK_COLOUR_GRADE_PRESET,
                        )
                        n_graded += 1
                    except Exception as exc:
                        print(
                            f"\n[colour-grade] ERROR grading {local_path}: {exc} "
                            f"— keeping original"
                        )
                        new_item[path] = trim
                        n_failed += 1
                        tracker.tick()
                        continue

                new_item[str(out)] = trim
                path_remap[path] = str(out)
                tracker.tick()
            new_footage.append(new_item)
        entry["footage"] = new_footage

    tracker.finish()
    print(
        f"[colour-grade] DONE — graded={n_graded}, cached={n_cached}, "
        f"skipped={n_skipped}, failed={n_failed}"
    )
    return final_data, path_remap


def _add_colour_graded_paths_to_history(path_remap: dict[str, str]) -> None:
    """Identity entries so the stitcher's url→local lookup finds graded files."""
    if not path_remap:
        return
    history = _load_history()
    added = 0
    for new_path in path_remap.values():
        if new_path not in history:
            history[new_path] = new_path
            added += 1
    _save_history(history)
    print(f"[colour-grade] added {added} identity entry(ies) to history.json")


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
    print(
        f"[audio events] hardcoded SFX_VOLUME={SFX_VOLUME}, MUSIC_VOLUME={MUSIC_VOLUME}"
    )
    print("=" * 70)

    out: dict[str, list[dict]] = {}

    def _is_none(value) -> bool:
        return value in (None, "none", "None", "")

    for script_text, scene_data in script_to_search_term.items():
        events: list[dict] = []
        short = script_text[:60]
        print(
            f"\n[audio events] scene: '{short}{'...' if len(script_text) > 60 else ''}'"
        )

        search_type = scene_data.get("search_type")

        # ── SFX resolution ──────────────────────────────────────────
        user_sfx = scene_data.get("sfx", "none")

        if not _is_none(user_sfx):
            timing = scene_data.get("sfx_timing", "loop_start")
            sfx_path = str(SOUND_EFFECTS_DIR / user_sfx)
            events.append(
                {
                    "type": "sfx",
                    "path": sfx_path,
                    "timing": timing,
                    "_debug": f"user-defined sfx '{user_sfx}'",
                }
            )
            print(f"[audio events]   + SFX (user): {user_sfx} @ {timing}")
        else:
            if search_type in JOINT_TYPE_SFX_MAP:
                default = JOINT_TYPE_SFX_MAP[search_type]
                sfx_path = str(SOUND_EFFECTS_DIR / default["path"])
                events.append(
                    {
                        "type": "sfx",
                        "path": sfx_path,
                        "timing": default["timing"],
                        "_debug": f"auto-injected for type {search_type.value}",
                    }
                )
                print(
                    f"[audio events]   + SFX (auto for {search_type.value}): "
                    f"{default['path']} @ {default['timing']}"
                )
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
            events.append(
                {
                    "type": "music",
                    "path": music_path,
                    "timing": "scene_start",
                    "duration": trim,
                    "fade_out": fade,
                    "_debug": (
                        f"user-defined music '{user_music}' (trim={trim}, fade={fade}s)"
                    ),
                }
            )
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
    scriptTextToPexelSearch: dict[str, SearchTermData] = load_json(
        LINE_INDEX_TO_SEARCH_TERM_FILE
    )
    # Convert string search_type to MediaType enum. (Flat schema — no
    # variant field any more; the type encodes everything.)
    for key, value in scriptTextToPexelSearch.items():
        try:
            value["search_type"] = MediaType(value["search_type"])
        except ValueError:
            valid = ", ".join(t.value for t in MediaType)
            print(
                f"ERROR: unknown search_type {value['search_type']!r} "
                f"on scene '{key[:60]}'"
            )
            print(f"       valid values: {valid}")
            sys.exit(1)
    print("!!!!!!script text to pexel search:")
    print(scriptTextToPexelSearch)

    # 1.4) Tighten the narration BEFORE anything time-based runs. The cutdown
    #      removes dead-air + adds the sentence transitions, writing
    #      PROCESSED_AUDIO_DIR/<stem>.processed.wav. Because SCRIPT_AUDIO_FILE
    #      points there, the synchroniser (1.5) and the final stitch both run
    #      on the tightened audio, so the timings and the baked-in narration match.
    print("====================================================================")
    print("Tightening narration audio (silence cutdown + sentence transitions)...")
    if not Path(RAW_SCRIPT_AUDIO_FILE).exists():
        print(f"ERROR! raw narration not found: {RAW_SCRIPT_AUDIO_FILE}")
        sys.exit(1)
    processed_wav, _ = run_audio_cutdown(
        audio_file=RAW_SCRIPT_AUDIO_FILE,
        script_file=SCRIPT_FILE,
        output_dir=PROCESSED_AUDIO_DIR,
        whisper_model=AUDIO_CUTDOWN_WHISPER_MODEL,
        force=FORCE_AUDIO_CUTDOWN,
    )
    if os.path.normpath(str(processed_wav)) != os.path.normpath(SCRIPT_AUDIO_FILE):
        print(
            f"  ! cutdown wrote {processed_wav}, but the pipeline expects "
            f"{SCRIPT_AUDIO_FILE} — check PROCESSED_AUDIO_DIR / the stem."
        )
        sys.exit(1)
    print(f"  ✓ pipeline narration: {SCRIPT_AUDIO_FILE}")

    # 1.5) Audio synchronisation — produces line timings + (optionally) per-word timings
    run_audio_script_synchronizer(
        SCRIPT_AUDIO_FILE,
        LINE_INDEX_TO_SEARCH_TERM_FILE,
        SYNCHRONIZED_SCRIPT_OUTPUT_FILE,
        TIMESTAMPS_ABSOLUTE_FILE,
        AUDIO_START_DELAY_SECONDS,
    )

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
            print(
                f"[main] added {len(stickman_candidates)} stickman candidate "
                f"bundle(s) to the review set"
            )

        # AI stickman tiles for stickman_joint scenes — same downstream flow as
        # JOINT_3_ROW (these bundles feed the joint compositor) but the tiles
        # are AI renders, not Pexels stills.
        stickman_joint_candidates = generate_stickman_joint_candidates(
            scriptTextToPexelSearch
        )
        if stickman_joint_candidates:
            candidates_data.extend(stickman_joint_candidates)
            print(
                f"[main] added {len(stickman_joint_candidates)} stickman-joint "
                f"candidate bundle(s) to the review set"
            )

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
        print(
            f"💾 Cached {len(candidates_data)} candidate bundle(s) to {CANDIDATES_CACHE_FILE}."
        )

    # ── Pixellate AI candidates BEFORE review ────────────────────────────
    # In-memory, AFTER the cache load/fetch (so it applies on cache hits too)
    # and BEFORE non_edit_candidates is built. The candidate cache stays raw,
    # so this is cheap to re-apply and never bakes pixels into the cache. Only
    # stickman / stickman_joint are in candidates_data here; ai_edit gets
    # pixellated later, as each edit is generated (build_ai_edit_candidates…).
    pixellate_candidate_bundles(candidates_data, scriptTextToPexelSearch)

    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
    print("\n=== SCRIPT → CANDIDATE MEDIA ===")
    for entry in candidates_data:
        print(f"\nSCRIPT: {entry['script_text']}")
        print(
            f"  needs {entry.get('num_clips_needed', 1)} clip(s), "
            f"each ≤ {entry.get('max_runtime_per_clip_seconds', 0):.2f}s"
        )
        cands = entry.get("candidates", {}) or {}
        print("  VIDEOS:")
        for item in cands.get("videos", []):
            for url, trim in item.items():
                print(f"    - {url}  (trim: {trim}s)")
        print("  IMAGES:")
        for item in cands.get("images", []):
            for url, trim in item.items():
                print(f"    - {url}  (trim: {trim}s)")

    # 2.5) STAGE 1 review — everything EXCEPT (things that don't need reviewing...)
    print("====================================================================")
    print("Launching media review GUI (stage 1: stock / wiki / joint / stickman)...")
    # Exclude from the stage-1 review GUI:
    #   - ai_edit: reviewed later in stage 2 (needs the stage-1 picks first)
    #   - stickman_joint: the joint compositor always uses candidate [0] and
    #     IGNORES the review pick, so reviewing these is pointless — and the
    #     review's cleanup would delete the "unchosen" local AI tiles, which
    #     (unlike Pexels URLs) can't be re-downloaded. They re-enter final_data
    #     via the generator-merge step (2.6) afterwards.
    _excluded_from_review = {
        MediaType.AI_EDIT,
        MediaType.ZOOM_PREV_IMG,
        MediaType.STATIC_OF_PREVIOUS,
    }
    non_edit_candidates = [
        c
        for c in candidates_data
        if scriptTextToPexelSearch.get(c["script_text"], {}).get("search_type")
        not in _excluded_from_review
    ]

    _stickman_texts = {
        t
        for t, d in scriptTextToPexelSearch.items()
        if d.get("search_type") == MediaType.STICKMAN
    }
    _stickman_joint_texts = {
        t
        for t, d in scriptTextToPexelSearch.items()
        if d.get("search_type") in STICKMAN_JOINT_TYPES
    }
    _regenerable_stage1 = _stickman_texts | _stickman_joint_texts

    def _regen_stage1(script_text: str) -> list[dict] | None:
        st = scriptTextToPexelSearch.get(script_text, {}).get("search_type")
        if st == MediaType.STICKMAN:
            return _regenerate_stickman_scene(script_text, scriptTextToPexelSearch)
        if st in STICKMAN_JOINT_TYPES:
            return _regenerate_stickman_joint_scene(
                script_text, scriptTextToPexelSearch
            )
        return None

    final_data, has_manual = run_media_review(
        candidates_data=non_edit_candidates,
        history_file=str(HISTORY_FILE),
        review_state_file=REVIEW_STOCK_FOOTAGE_OUTPUT_FILE,
        cache_dir=_CACHE_DIR,
        regenerate_fn=_regen_stage1,
        regenerable_texts=_regenerable_stage1,
    )
    if has_manual:
        print("\n[main] Exiting so you can perform the manual fixes above.")
        sys.exit(0)

    # 2.55) ai_edit scenes — generated + reviewed ONE AT A TIME, in script
    #       order, so chains of consecutive ai_edits work to any depth (each
    #       edit waits for the previous scene's pick before it's generated).
    final_data = run_ai_edit_stage(
        script_to_search_term=scriptTextToPexelSearch,
        final_data=final_data,
    )

    print("\n=== FINAL SCRIPT → CHOSEN MEDIA ===")
    for entry in final_data:
        print(f"\nSCRIPT: {entry['script_text']}")
        for item in entry["footage"]:
            for url, trim in item.items():
                print(f"  ✓ {url}  (trim: {trim}s)")
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

    # 2.57)
    additional_steps_save_for_later()

    # 2.6) Run every registered local-file generator (joint, read-out, …)
    #      and merge their outputs back into final_data so the stitcher
    #      uses the new local files instead of any prior placeholders.
    generated_footage_map = run_all_local_generators(
        script_to_search_term=scriptTextToPexelSearch,
        candidates_data=candidates_data,
        final_data=final_data,
    )

    if generated_footage_map:
        print("\n[main] local footage produced — integrating into final_data")
        final_data = _merge_generated_footage_into_final_data(
            final_data,
            generated_footage_map,
            source_label="local-generators",
        )
        _add_local_paths_to_history(generated_footage_map)

        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with local footage → {FINAL_SCRIPT_AND_CLIPS}")

        print("\n=== FINAL SCRIPT → MEDIA (POST-GENERATOR-MERGE) ===")
        for entry in final_data:
            print(f"\nSCRIPT: {entry['script_text']}")
            for item in entry["footage"]:
                for path_or_url, trim in item.items():
                    label = (
                        Path(path_or_url).name if "/" in path_or_url else path_or_url
                    )
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
            final_data,
            explain_footage_map,
            source_label="stickman-explain",
        )
        _add_local_paths_to_history(explain_footage_map)
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(
            f"💾 Updated final_data with explainer footage → {FINAL_SCRIPT_AND_CLIPS}"
        )
    else:
        print("\n[main] no explainer scenes; final_data unchanged")

    # 2.63) Text-overlay scenes: caption (search_term) composited onto the
    #       PREVIOUS scene's chosen image. After explainer (so any prior scene
    #       type resolves) and before Ken Burns (static MP4 → KB skips it, so
    #       the tilted caption is never cropped).
    overlay_footage_map = generate_text_overlay_scenes(
        script_to_search_term=scriptTextToPexelSearch,
        final_data=final_data,
    )
    if overlay_footage_map:
        print("\n[main] text-overlay footage produced — integrating into final_data")
        final_data = _merge_generated_footage_into_final_data(
            final_data,
            overlay_footage_map,
            source_label="text-overlay",
        )
        _add_local_paths_to_history(overlay_footage_map)
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(
            f"💾 Updated final_data with text-overlay footage → {FINAL_SCRIPT_AND_CLIPS}"
        )
    else:
        print("\n[main] no text-overlay scenes; final_data unchanged")

    # 2.64) Manual stock placement: composite each MANUAL_STOCK_ADD_TO_PREVIOUS
    #       scene's chosen still onto the PREVIOUS scene's image at a clicked
    #       position/size. After text-overlay (so the base resolves to a
    #       finished image) and before Ken Burns (static MP4 → KB skips it).
    final_data = run_manual_image_stage(
        script_to_search_term=scriptTextToPexelSearch,
        final_data=final_data,
    )
    save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)

    # 2.648) Cinematic colour grade — give all STOCK footage one unified
    #        "shot on film at golden hour" look BEFORE Ken Burns, so stills are
    #        graded once (then KB animates the graded still) and stock videos /
    #        composites are graded in place. Toggle via
    #        TOGGLE_STOCK_COLOUR_GRADING_ETC / APPLY_COLOUR_GRADING_TO_ALL.
    final_data, colour_grade_remap = apply_colour_grading_to_final_data(
        final_data,
        scriptTextToPexelSearch,
    )
    if colour_grade_remap:
        _add_colour_graded_paths_to_history(colour_grade_remap)
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with graded footage → {FINAL_SCRIPT_AND_CLIPS}")

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
                    label = (
                        Path(path_or_url).name if "/" in path_or_url else path_or_url
                    )
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

    # Some upstream stages only write this conditionally; guarantee it exists
    # so the stitcher can always load the latest picks.
    save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
    print(f"💾 Final clip map → {FINAL_SCRIPT_AND_CLIPS}")

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

    # so that our terminal doesn't mess up! (so i can still see what I'm typing...)
    subprocess.run(["stty", "sane"])

    print("done")


# ===========================================================================

if __name__ == "__main__":
    main()


# ================================================
# ==== OTHER THINGS MAYBE USEFUL DOWN THE LINE ===
# ================================================


def splitSceneIntoPowerpointSlideImages():
    twotest = "But where exactly in the world did this tea originate"

    ai_request = f"Split this sentence into the different images that would make up this slide on my powerpoint. Identify the key nouns and visual elements. Just simple bullet point: \n{twotest}"

    response2 = ollama.chat(
        model="qwen2.5:7b", messages=[{"role": "user", "content": ai_request}]
    )

    reply2 = response2["message"]["content"]
    print(reply2)

    ai_request2 = f"Strip out any ai fulff, explanations, headings or follow up questions: give me just a csv of identified key terms. nothing else: \n{reply2}"

    response3 = ollama.chat(
        model="qwen2.5:7b", messages=[{"role": "user", "content": ai_request2}]
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
            model="qwen2.5:7b", messages=[{"role": "user", "content": ai_request}]
        )

        reply4 = response4["message"]["content"].strip()
        print(scene)
        print(reply4)
        print("-----")
