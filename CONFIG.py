"""
Central configuration + shared runtime state for the video pipeline.

Everything that used to live in the top "config block" of main.py now lives
here: name-based path config, global file/dir paths, feature flags, the
MediaType enum + type-classification sets, the joint-scene layout/SFX maps,
the shared HTTP sessions + threading locks, and the ProgressTracker.

main.py and every extracted helper module import what they need from here, so
this is the ONE place to flip knobs / change paths.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import TypedDict

import requests

import COLOUR_GRADE_ETC

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
# COLOUR_GRADE_STAGE module for the full machinery.
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

# Generic image-extension set, shared by footage classification helpers
# (CACHE_IO) and the Ken Burns / colour-grade stages.
IMAGE_EXTENSIONS: set[str] = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

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
#      LOCAL_FOOTAGE_GENERATORS (see SCENE_GENERATORS).
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
    DECORATE_PREVIOUS = "decorate_previous"  # interactive: draw text (and future tools) onto the PREVIOUS scene's image

 # near MANUAL_STOCK_ADD_TYPES / ZOOM_PREV_TYPES / STATIC_OF_PREVIOUS_TYPES:
DECORATE_PREVIOUS_TYPES: set[MediaType] = {MediaType.DECORATE_PREVIOUS}
DECORATE_PREVIOUS_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/decorate_previous")
DECORATE_PREVIOUS_RENDER_SAFETY_PAD_SEC: float = 0.08


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

DECORATE_PREVIOUS_TYPES: set[MediaType] = {MediaType.DECORATE_PREVIOUS}
DECORATE_PREVIOUS_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/decorate_previous")
DECORATE_PREVIOUS_RENDER_SAFETY_PAD_SEC: float = 0.08

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
