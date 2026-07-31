"""
Central configuration + shared runtime state for the video pipeline.

Everything that used to live in the top "config block" of main.py now lives
here: name-based path config, global file/dir paths, feature flags, the
MediaType enum + type-classification sets, the joint-scene layout/SFX maps,
the shared HTTP sessions + threading locks, and the ProgressTracker.

main.py and every extracted helper module import what they need from here, so
this is the ONE place to flip knobs / change paths. Lives at the REPO ROOT
(not inside ___visuals/) so every other module — root or package — just
does `from CONFIG import ...` with no path bootstrap needed for this file
specifically. Package files under ___visuals/ still need their own "allow
`uv run ___visuals/<file>.py`" bootstrap to put the root back on sys.path;
that's what makes `from CONFIG import ...` resolve from inside the package.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TypedDict

import requests

from ___visuals import COLOUR_GRADE_ETC

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

# ===========================================================================
# MINIMUM DURATION FOR BRAND-NEW FOOTAGE
# ===========================================================================
# A scene that pops brand-new material on screen (stock / wikipedia / an AI
# image / a map — see MIN_DURATION_GATED_TYPES) for less than this just
# FLASHES and reads as a mistake. We don't have audio timings yet at tagging
# time, so we estimate a line's on-screen time from its WORD COUNT at
# NARRATION_WPM_ESTIMATE — deliberately a touch faster than average speech so
# we err toward flagging a line as too short rather than letting a flash
# through. Below the threshold the manual tagger refuses the new type and
# offers to fold the line into a neighbour or make it an edit-of-previous
# instead; the auto-tagger quietly prefers hold_previous + decorate for the
# same lines. Edit-previous / hold / background / typography are EXEMPT (they
# reuse what's already there, or — typography — ARE the words themselves).
MIN_NEW_FOOTAGE_SECONDS: float = 1.0
# Narrator pace used ONLY for the estimate above (not the render). ~150 wpm
# is average speech; 170 is a touch quicker, so a borderline line is flagged.
NARRATION_WPM_ESTIMATE: int = 170


def _word_count(text: str) -> int:
    return len(re.findall(r"[\w']+", text or ""))


def estimate_narration_seconds(text: str) -> float:
    """Rough on-screen time for a line from its word count at
    NARRATION_WPM_ESTIMATE. Used only by the tagging-time "too short for new
    footage" guard, never by the renderer (which uses real audio timings)."""
    n = _word_count(text)
    return (n / NARRATION_WPM_ESTIMATE * 60.0) if n else 0.0


def min_words_for_new_footage() -> int:
    """Fewest words a brand-new-footage scene needs so it clears
    MIN_NEW_FOOTAGE_SECONDS at NARRATION_WPM_ESTIMATE (always ≥ 1)."""
    return max(1, math.ceil(MIN_NEW_FOOTAGE_SECONDS * NARRATION_WPM_ESTIMATE / 60.0))


def words_needed_for_new_footage(text: str) -> int:
    """How many MORE words this line needs before brand-new footage on it
    would stand on its own (0 once it already clears the threshold)."""
    return max(0, min_words_for_new_footage() - _word_count(text))


def line_too_short_for_new_footage(text: str) -> bool:
    return words_needed_for_new_footage(text) > 0

# --- Cinematic colour grading (unified "shot on film at golden hour" look) ---
# Master switch: give STOCK footage one cohesive film grade so the whole video
# reads as a single graded collection. Applied late (just before Ken Burns) to
# the CHOSEN footage only — see apply_colour_grading_to_final_data + the
# COLOUR_GRADE_STAGE module for the full machinery.
#
# Preview / pick a look first:   uv run COLOUR_GRADE_ETC.py
TOGGLE_STOCK_COLOUR_GRADING_ETC: bool = False
# When True, grade EVERY scene (stickman / ai_edit / read-out / maps included),
# not just real-world stock. Ignored unless TOGGLE_STOCK_COLOUR_GRADING_ETC.
APPLY_COLOUR_GRADING_TO_ALL: bool = False
# When True, only VIDEO footage (mp4 etc.) gets colour graded — still images are
# left untouched entirely. Ignored unless TOGGLE_STOCK_COLOUR_GRADING_ETC.
APPLY_COLOUR_GRADING_TO_VIDEOS_ONLY: bool = True
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


def ensure_runtime_dirs() -> None:
    """Create the run's cache/output folders. Called by main.py at start —
    NOT at import time, so importing CONFIG (from the tagging tool, or when
    running any file directly) never creates directories in your cwd."""
    Path(_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    Path(_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    Path(PROCESSED_AUDIO_DIR).mkdir(parents=True, exist_ok=True)


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
# TYPOGRAPHY (KINETIC TYPOGRAPHY) INTEGRATION
# ===========================================================================
# Extra seconds added to the rendered MP4 beyond the scene's actual runtime,
# so that when the stitcher trims to `line_duration` it never falls short
# due to libx264 encoder rounding. The extra footage shows the last word
# stationary, which is invisible after trim.
TYPOGRAPHY_RENDER_SAFETY_PAD_SEC: float = 0.08

# Set False to disable the kinetic typography renderer entirely.
TYPOGRAPHY_ENABLE: bool = True


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
# BLANK / RANDOM-BACKGROUND SCENES
# ===========================================================================
# Two "empty canvas" media types, both rendered locally by
# generate_blank_scenes (no candidates, no review — like map / typography):
#   blank              — a flat colour fills the frame (white by default)
#   random_background  — one of the clips/stills in BACKGROUNDS_DIR fills it
# On their own they are a clean breath in the edit; stack `decorate` to draw
# or stamp onto them, or `caption` for a tilted caption on a bare backdrop.

# The colour a `blank` scene is filled with. Any ffmpeg colour (a name like
# "white", or "#rrggbb").
BLANK_SCENE_COLOUR: str = "white"

# Frame size the blank / background MP4s are rendered at. Matches the
# stitcher's target (STITCH_TOGETHER.TARGET_WIDTH x TARGET_HEIGHT), so the
# scene is never letterboxed or rescaled downstream.
BLANK_SCENE_RESOLUTION: tuple[int, int] = (1920, 1080)

# Where the rendered blank / random-background MP4s are written (cache-scoped).
BLANK_SCENE_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/blank_scenes")

# The folder `random_background` picks from. Stills and videos both work;
# a video is looped and centre-cropped to fill the frame, a still is baked
# into a static MP4. This is the same folder the joint compositor pulls its
# card backgrounds from (JOINT_IMAGE_CREATOR.BACKGROUNDS_DIR).
RANDOM_BACKGROUND_DIR: str = "_BACKGROUNDS"

# The pick is random per scene but DETERMINISTIC: it is seeded from the
# scene's script text, so re-running the pipeline never reshuffles which
# background a given line got. Bump this to reshuffle every scene at once.
RANDOM_BACKGROUND_SEED: int = 0


# ===========================================================================
# MATHS SCENES  —  manim-rendered animations of row["data"]
# ===========================================================================
# Every maths type renders TWO artefacts and the generator picks between them
# on the scene's runtime: a TRANSITION mp4 (the animation) and a FINAL still
# (its last frame). See AI_READ_THIS.txt — this is the house pattern for any
# animation whose length is fixed but whose scene length is not.
MATHS_SCENE_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/maths_scenes")

# Timeline: a marker sets off from the current year and travels back to
# row["data"]["year"], then the year pops up where it landed.
#
# These are TIGHT on purpose. Narration lines are short — in a typical script
# the median is around 1.3s and most are under 3s — so an animation of a
# couple of seconds is one that actually gets to play. Make the journey
# luxurious and every timeline silently falls back to its still.
TIMELINE_TRAVEL_SEC: float = 1.1  # the marker's journey
TIMELINE_LABEL_SEC: float = 0.4  # the year appearing once it lands
TIMELINE_SETTLE_SEC: float = 0.5  # a beat on the finished line
# The first two are ESSENTIAL: cut into them and you cut the journey or the
# reveal. The settle is a TRAILING beat, there to be trimmed — a scene that
# can fit travel+label plays the animation and simply loses some of the pause
# at the end. Only a scene too short for even travel+label gives up and shows
# the finished still for its whole length.
TIMELINE_FPS: int = 30
TIMELINE_RESOLUTION: tuple[int, int] = (1920, 1080)
TIMELINE_BACKGROUND: str = "#FFFFFF"
TIMELINE_INK: str = "#1B1B1B"  # axis, ticks, tick labels
TIMELINE_ACCENT: str = "#7D5BA6"  # the marker and the year that pops up
# How many tick marks the line carries between the two years (endpoints
# included). Odd numbers centre a tick, which reads better.
TIMELINE_TICKS: int = 7


def timeline_transition_seconds() -> float:
    """How long a timeline's animation runs, start to finish."""
    return TIMELINE_TRAVEL_SEC + TIMELINE_LABEL_SEC + TIMELINE_SETTLE_SEC


def timeline_min_playable_seconds() -> float:
    """The shortest the animation can be cut to and still SAY something: the
    journey plus the year landing. Everything after that is the settle beat,
    which the stitcher may trim away. A scene shorter than this gets the
    finished still instead of a clipped journey."""
    return TIMELINE_TRAVEL_SEC + TIMELINE_LABEL_SEC


# Charts (counter / progress_bar / bar_chart / pie_chart / line_graph): the
# same transition + hold pattern as the timeline, on the same tight budget —
# see the note above TIMELINE_TRAVEL_SEC. One shared trio of timings: every
# chart is BUILD (the number ticks / the bar fills / bars grow / slices sweep
# / the line draws), then a REVEAL beat (value labels pop), then a trailing
# settle there to be trimmed.
CHART_ANIM_SEC: float = 1.0  # the build
CHART_LABEL_SEC: float = 0.4  # the reveal beat once the build lands
CHART_SETTLE_SEC: float = 0.5  # a beat on the finished chart (trimmable)
CHART_BACKGROUND: str = "#FFFFFF"
CHART_INK: str = "#1B1B1B"  # values, category labels, titles
CHART_MUTED: str = "#898781"  # secondary ink: axis labels, small captions
CHART_BASELINE: str = "#C3C2B7"  # baseline / axis hairlines
CHART_TRACK: str = "#E1E0D9"  # the progress bar's empty track
CHART_ACCENT: str = "#7D5BA6"  # every single-measure mark: the counter's
#   digits, the progress fill, all bars, the trend line. One measure = one
#   colour — many-coloured bars would claim an identity difference the data
#   doesn't have.
# Categorical palette for the PIE only (slices DO carry identity). Validated
# on white in THIS order (dataviz six checks: worst adjacent CVD ΔE 16.3,
# normal-vision 19.6) — the order is the colour-blind-safety mechanism, so
# append, never shuffle. Two slots sit under 3:1 contrast on white; the
# required relief is built into the renderer (white slice gaps + ink direct
# labels on every slice).
CHART_PALETTE: tuple[str, ...] = (
    "#7D5BA6", "#EB6834", "#2A78D6", "#EDA100", "#E87BA4", "#008300",
)


def chart_transition_seconds() -> float:
    """How long a chart's animation runs, start to finish."""
    return CHART_ANIM_SEC + CHART_LABEL_SEC + CHART_SETTLE_SEC


def chart_min_playable_seconds() -> float:
    """The shortest a chart animation can be cut to and still say something:
    the build plus the reveal. Past that is settle; below it, the still."""
    return CHART_ANIM_SEC + CHART_LABEL_SEC


def chart_look() -> tuple:
    """Every shared input to a chart render that isn't the scene's own data —
    goes into every chart cache key, so a timing/colour change re-renders
    instead of serving the old video (see AI_READ_THIS.txt, point 1)."""
    return (CHART_ANIM_SEC, CHART_LABEL_SEC, CHART_SETTLE_SEC,
            TIMELINE_FPS, TIMELINE_RESOLUTION, CHART_BACKGROUND, CHART_INK,
            CHART_MUTED, CHART_BASELINE, CHART_TRACK, CHART_ACCENT,
            CHART_PALETTE)


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


# ===========================================================================
# MEDIA TYPES  —  built FROM the shared catalog. No legacy layer.
# ===========================================================================
# The catalog lives RIGHT HERE (one file, as requested) and is shared with
# the tagging tool: ___splitting_and_labelling/MEDIA_TYPES.py imports it
# from this module. The enum members ARE the catalog names:
#   MediaType.STOCK, MediaType.AI_STOCK, ..., MediaType.HOLD_PREVIOUS, ...
# Grouping is the `group` modifier (+ group_id), NOT a type. decorate is
# the ONE interactive editor (draw canvas + stamp/zoom/object tabs);
# caption is the AUTOMATIC tilted caption. The old combined types are gone.


class Tag(str, Enum):
    NEW = "new"  # puts brand-new material on screen
    EDIT_PREVIOUS = "edit_previous"  # acts on the image already on screen
    AI = "ai"  # ai-generated look (red buttons)
    BOARD = "board"  # sits on the stickman explain board
    MATHS = "maths"  # a rendered animation of DATA (timeline, chart, …)


# ---------------------------------------------------------------------------
# BASE MEDIA TYPES — pick exactly one per line. The NAME is the value the
# renderer dispatches on; there is no separate legacy string.
# ---------------------------------------------------------------------------
MEDIA_TYPE_CATALOG: dict[str, dict] = {
    "stock": {
        "tags": [Tag.NEW],
        "color": "#2e6da4",
        "info": "footage or an image fetched from the stock library. the "
        "workhorse — most lines are this. stack group for a grid "
        "of them.",
        "example": "examples/stock.png",
    },
    "ai_stock": {
        "tags": [Tag.NEW, Tag.AI],
        "color": "#c0392b",
        "info": "an ai-generated picture in the channel's stickman style. "
        "the search term is a scene prompt, not a search. stack "
        "group for a grid of them.",
        "example": "examples/ai_stock.png",
    },
    "wikipedia": {
        "tags": [Tag.NEW],
        "color": "#148f77",
        "info": "the image from a wikipedia article. the search term must "
        "be the exact article name ('Banda Islands').",
        "example": "examples/wikipedia.png",
    },
    "map": {
        "tags": [Tag.NEW],
        "color": "#1e8449",
        "info": "a rendered map with the place highlighted. the search "
        "term is just the place name ('Indonesia').",
        "example": "examples/map.png",
    },
    "typography": {
        "tags": [Tag.NEW],
        "color": "#8d6e2f",
        "info": "the line's own words animate on a blank background. good "
        "cold-open when there is nothing to picture yet.",
        "example": "examples/typography.png",
    },
    "blank": {
        "tags": [Tag.NEW],
        "color": "#9aa0a6",
        "info": "a plain white frame — nothing else. the search term is "
        "unused. a clean breath between busy scenes; stack decorate "
        "to draw / stamp onto the empty canvas, or caption for a "
        "tilted caption on white.",
        "example": "examples/blank.png",
    },
    "random_background": {
        "tags": [Tag.NEW],
        "color": "#6c7a89",
        "info": "a background picked at random from the _BACKGROUNDS/ folder "
        "(the same textured cards the joint compositor uses). the "
        "search term is unused. like blank, but with texture — stack "
        "decorate or caption on top. the pick is stable across re-runs.",
        "example": "examples/random_background.png",
    },
    "stock_on_board": {
        "tags": [Tag.NEW, Tag.AI, Tag.BOARD],
        "color": "#e74c3c",
        "info": "stock footage shown on the stickman's explain board.",
        "example": "examples/stock_on_board.png",
    },
    "wikipedia_on_board": {
        "tags": [Tag.NEW, Tag.AI, Tag.BOARD],
        "color": "#e74c3c",
        "info": "a wikipedia image shown on the stickman's explain board.",
        "example": "examples/wikipedia_on_board.png",
    },
    "hold_previous": {
        "tags": [Tag.EDIT_PREVIOUS],
        "color": "#5c6bc0",
        "info": "keep the previous image on screen, frozen. the default "
        "for quick mid-sentence beats — stack decorate on it to "
        "draw on it, stamp pictures into it, zoom, or cut objects "
        "out (everything manual lives in the ONE decorate editor). "
        "stack group instead to add this line's own picture as the "
        "next cell of the group opened above.",
        "example": "examples/hold_previous.png",
    },
    "background": {
        "tags": [Tag.NEW],
        "color": "#4a4a55",
        "info": "nothing on top — the BACKGROUND VIDEO just plays for this "
        "line (VIDEO_BACKGROUND_MODE only; the search term is "
        "unused). stack decorate to draw straight onto the "
        "background footage, or caption for a tilted caption over "
        "it. outside background mode this renders NOTHING — don't "
        "use it there.",
        "example": "examples/background.png",
    },
    "ai_edit_previous": {
        "tags": [Tag.EDIT_PREVIOUS, Tag.AI],
        "color": "#a93226",
        "info": "ai edits the previous ai image in place. the search term "
        "is the change ('add a second coin').",
        "example": "examples/ai_edit_previous.png",
    },
    "timeline": {
        "tags": [Tag.MATHS],
        "color": "#7d5ba6",
        "info": "a manim-animated timeline. a marker starts at the current "
        "year and travels back to the year you give it; when it "
        "lands, that year pops up on the line. the search term is "
        "unused — the year comes from the data field.",
        "example": "examples/timeline.png",
    },
    "counter": {
        "tags": [Tag.MATHS],
        "color": "#b03a68",
        "info": "a big number ticks up from 0 to the value you give it, "
        "with an optional prefix / suffix ('$', '%', ' million') and "
        "a caption underneath. for any single stat you narrate. the "
        "search term is unused — everything comes from the data form.",
        "example": "examples/counter.png",
    },
    "progress_bar": {
        "tags": [Tag.MATHS],
        "color": "#0e7490",
        "info": "a horizontal bar fills to the percentage you give it while "
        "the number ticks up above. ONE quantity out of a whole "
        "('73% of the ocean is unexplored') — for the parts of a "
        "whole, use pie chart. the search term is unused.",
        "example": "examples/progress_bar.png",
    },
    "bar_chart": {
        "tags": [Tag.MATHS],
        "color": "#c2571a",
        "info": "labelled bars grow up side by side, values popping on top "
        "— compare a few quantities. 2–6 'label: value' pairs in the "
        "data form, drawn in that order. the search term is unused.",
        "example": "examples/bar_chart.png",
    },
    "pie_chart": {
        "tags": [Tag.MATHS],
        "color": "#2f6f4f",
        "info": "a pie draws itself on, one slice per 'label: value' share, "
        "labels + computed percentages around it. the parts of one "
        "whole — the values can be anything, they don't have to sum "
        "to 100. the search term is unused.",
        "example": "examples/pie_chart.png",
    },
    "line_graph": {
        "tags": [Tag.MATHS],
        "color": "#4a6fa5",
        "info": "a trend line draws itself left to right across your "
        "'label: value' points, then the final value pops. change "
        "over time — the labels are the x axis (years, quarters), "
        "evenly spaced. the search term is unused.",
        "example": "examples/line_graph.png",
    },
}

# ---------------------------------------------------------------------------
# STACKABLE MODIFIERS — layer any of these on top of the base type.
# (you cannot stack first: there must be a base to put them on.)
# ---------------------------------------------------------------------------
MODIFIERS: dict[str, dict] = {
    "decorate": {
        "color": "#e6c15a",
        "info": "open the scene's image in the ONE decorate editor. it "
        "opens on the draw canvas (text boxes, arrows, highlights, "
        "circles, lines, rectangles) with tabs along the top for "
        "the other tools: stamp (place extra pictures), zoom "
        "(crop / push in), object (cut-out extraction editor).",
        "example": "examples/decorate.png",
    },
    "caption": {
        "color": "#e6c15a",
        "info": "AUTOMATIC: a big tilted caption is composited onto the "
        "scene's image for you — no editor, no clicking. the text "
        "is the search term (or a hand-added caption_text column). "
        "for hand-placed text use decorate's draw canvas instead.",
        "example": "examples/caption.png",
    },
    "collage": {
        "color": "#e6c15a",
        "info": "pick SEVERAL images for this one line in the review stage, "
        "then choose: auto collage (they're scattered with overlaps "
        "onto a plain background for you) or stamp it yourself (the "
        "picks load into the decorate editor as stamps). stock only.",
        "example": "examples/collage.png",
    },
    "group": {
        "color": "#e6c15a",
        "info": "this line is one cell of a group: the cells sit side by "
        "side, one appearing per line. OPEN a group with a real base "
        "(stock / ai stock) + group; CONTINUE it on the following "
        "lines with hold previous + group — you are holding the "
        "group picture on screen and landing one more cell on it. "
        "every cell brings its OWN picture, so every line in the "
        "group needs its own search term. 3 cells max (the layout's "
        "size); the whole group takes the base the first cell picked.",
        "example": "examples/group.png",
    },
}

# ---------------------------------------------------------------------------
# TAGGER TABS — how the media-type buttons are laid out in MANUAL_TAGGING.
# One tab per entry, one column per (tag, heading). A type appears in every
# column whose tag it carries; a type whose tags reach no column is
# unreachable, which _validate_media_type_tabs() refuses to let happen.
# Adding a maths type is a catalog entry with Tag.MATHS — no UI change.
# ---------------------------------------------------------------------------
MEDIA_TYPE_TABS: list[dict] = [
    {
        "name": "material",
        "label": "material",
        "columns": [
            (Tag.NEW, "NEW — brand-new material"),
            (Tag.EDIT_PREVIOUS, "EDIT PREVIOUS — act on what is on screen"),
        ],
    },
    {
        "name": "maths",
        "label": "maths",
        "columns": [(Tag.MATHS, "NEW — MATHS")],
    },
]


def _validate_media_type_tabs() -> None:
    reachable = {
        name
        for name, entry in MEDIA_TYPE_CATALOG.items()
        for tab in MEDIA_TYPE_TABS
        for tag, _ in tab["columns"]
        if tag in entry["tags"]
    }
    missing = set(MEDIA_TYPE_CATALOG) - reachable
    if missing:
        raise RuntimeError(
            f"these media types sit on no tagger tab (give them a tag that a "
            f"MEDIA_TYPE_TABS column selects): {sorted(missing)}"
        )


_validate_media_type_tabs()


# Which base types can OPEN a group (they have grid layouts). The group takes
# its base — where every cell's picture comes from — from the line that opens
# it.
GROUPABLE_TYPES: set[str] = {"stock", "ai_stock"}

# The base every CONTINUATION cell of a group carries. A group reads exactly
# as you tag it: the first line is `stock + group` (or `ai_stock + group`),
# and each line that joins it is `hold_previous + group` — the group picture
# stays on screen and this line's own tile lands on it. Continuation rows
# still carry their OWN search term, because every cell is its own picture.
#
# `hold_previous` is only the SPELLING. resolve_group_continuations() rewrites
# these rows to the group's real base right after load, because every stage
# downstream (candidate fetch, joint compositor, ai tile generator) dispatches
# on media_type and a cell must fetch its picture from the group's base.
GROUP_CONTINUATION_TYPE: str = "hold_previous"

# Which base types accept the collage modifier (multi-pick in review).
COLLAGEABLE_TYPES: set[str] = {"stock"}

# Base types that put BRAND-NEW material on screen, so a too-short line of
# one just FLASHES — these are gated by MIN_NEW_FOOTAGE_SECONDS (see the
# helpers above, the manual-tagger "too short" guard, and the auto-tagger's
# short-scene handling). Everything else is exempt: edit-of-previous / hold
# reuse the image already there, background shows the bare background,
# blank / random_background are an empty canvas rather than material, and
# typography IS the words (so a short one can never "flash new footage").
MIN_DURATION_GATED_TYPES: set[str] = {
    "stock", "ai_stock", "wikipedia", "map",
    "stock_on_board", "wikipedia_on_board",
}


# ===========================================================================
# THE `data` COLUMN  —  structured input for types the search term can't feed
# ===========================================================================
# Most types are driven by their search_term (a query, a place, a prompt). The
# MATHS family isn't: a timeline needs a YEAR, a pie chart will need labelled
# slices. Rather than bolt a column onto the json per type, every row carries
# ONE optional `data` object, and the type declares what belongs in it here.
#
# The tagger reads this table to BUILD its data form (one input per field, no
# per-type UI code), normalise_scene_row reads it to VALIDATE + coerce what
# comes back, and the generator reads the coerced values. Adding a pie chart
# is a catalog entry, a row here, and a generator — no schema change anywhere.
#
# `kind` drives both the coercion below and the input the tagger renders:
#   year    -> a whole year, sanity-bounded         (int)
#   int     -> any whole number                     (int)
#   number  -> any number                           (float)
#   percent -> a number 0..100                      (float)
#   text    -> a non-empty string                   (str)
#   series  -> "label: value, label: value" pairs   (canonical str)
#   shares  -> series, but values >= 0: parts of a whole, max 6
# series/shares stay a STRING through the json — the tagger's input and the
# file both hold the one-line spelling; renderers re-parse with parse_series.
@dataclass(frozen=True)
class DataField:
    name: str  # the key inside row["data"]
    label: str  # what the tagger's input is labelled
    kind: str  # see the table above
    help: str = ""  # shown under the input
    placeholder: str = ""
    required: bool = True


MEDIA_TYPE_DATA_FIELDS: dict[str, tuple[DataField, ...]] = {
    "timeline": (
        DataField(
            name="year",
            label="target year",
            kind="year",
            placeholder="1600",
            help="the year the marker travels back to. it sets off from the "
            "CURRENT year, which the renderer reads from the clock — so "
            "you only give it the destination.",
        ),
    ),
    "counter": (
        DataField(
            name="value",
            label="target value",
            kind="number",
            placeholder="1500000",
            help="the number the counter ticks up to, from 0. plain digits "
            "only — put units in the prefix / suffix.",
        ),
        DataField(
            name="prefix",
            label="prefix",
            kind="text",
            required=False,
            placeholder="$",
            help="sits just before the number ('$', '£').",
        ),
        DataField(
            name="suffix",
            label="suffix",
            kind="text",
            required=False,
            placeholder="%",
            help="sits just after the number ('%', ' million', ' km'). "
            "lead with a space for a word.",
        ),
        DataField(
            name="label",
            label="caption",
            kind="text",
            required=False,
            placeholder="ships lost at sea",
            help="a small caption under the number saying what it counts.",
        ),
    ),
    "progress_bar": (
        DataField(
            name="percent",
            label="percent full",
            kind="percent",
            placeholder="73",
            help="how far the bar fills, 0–100. the number ticks up "
            "alongside the fill.",
        ),
        DataField(
            name="label",
            label="caption",
            kind="text",
            required=False,
            placeholder="of the ocean is unexplored",
            help="a small caption under the bar.",
        ),
    ),
    "bar_chart": (
        DataField(
            name="bars",
            label="bars",
            kind="shares",
            placeholder="Rome: 900, Athens: 300, Sparta: 140",
            help="2–6 'label: value' pairs, comma-separated. bars grow in "
            "this order, values pop on top.",
        ),
        DataField(
            name="title",
            label="title",
            kind="text",
            required=False,
            placeholder="army size",
            help="a short title across the top of the chart.",
        ),
    ),
    "pie_chart": (
        DataField(
            name="slices",
            label="slices",
            kind="shares",
            placeholder="Portugal: 45, Spain: 30, others: 25",
            help="2–6 'label: value' shares of one whole. percentages are "
            "computed for you — the values needn't sum to 100.",
        ),
        DataField(
            name="title",
            label="title",
            kind="text",
            required=False,
            placeholder="spice trade, 1550",
            help="a short title across the top of the chart.",
        ),
    ),
    "line_graph": (
        DataField(
            name="points",
            label="points",
            kind="series",
            placeholder="1900: 12, 1950: 48, 2000: 95",
            help="2–8 'label: value' points, drawn left to right. the "
            "labels are the x axis (years, quarters — evenly spaced).",
        ),
        DataField(
            name="title",
            label="title",
            kind="text",
            required=False,
            placeholder="world population, billions",
            help="a short title across the top of the chart.",
        ),
    ),
}

# A year outside this range is a typo (a mistyped century, a stray digit),
# not a date anyone narrates.
DATA_YEAR_MIN, DATA_YEAR_MAX = -4000, 4000

# series/shares bounds: one pair is not a chart, and past these counts the
# labels stop fitting on a 1080p frame read at a glance. Shares cap lower —
# the pie palette has six validated slots.
SERIES_MIN_POINTS, SERIES_MAX_POINTS = 2, 8
SHARES_MIN_PARTS, SHARES_MAX_PARTS = 2, 6
SERIES_LABEL_MAX_CHARS = 24


def parse_series(raw: object) -> list[tuple[str, float]]:
    """The 'label: value, label: value' spelling -> [(label, value), ...].

    The ONE parser for series data: coercion validates through it at tag
    time, and the chart renderers re-parse the stored string through it at
    render time, so the two can never disagree. Splits entries on commas /
    newlines / semicolons; the LAST colon splits label from value, so a
    label may itself contain one ('Q1: 2020: 5' reads as label 'Q1: 2020').
    Raises ValueError, naming the entry, on anything malformed."""
    entries = [e.strip() for e in re.split(r"[,\n;]", str(raw)) if e.strip()]
    pairs: list[tuple[str, float]] = []
    for entry in entries:
        label, sep, value_text = entry.rpartition(":")
        label = label.strip()
        if not sep or not label:
            raise ValueError(
                f"{entry!r} is not 'label: value' (e.g. 'Rome: 900')"
            )
        if len(label) > SERIES_LABEL_MAX_CHARS:
            raise ValueError(
                f"label {label!r} is over {SERIES_LABEL_MAX_CHARS} chars — "
                f"long labels don't fit beside a chart mark, shorten it"
            )
        try:
            value = float(value_text)
        except ValueError:
            raise ValueError(
                f"{value_text.strip()!r} in {entry!r} is not a number"
            ) from None
        if not math.isfinite(value):
            raise ValueError(f"{entry!r} has a non-finite value")
        pairs.append((label, value))
    return pairs


def _plain_number(value: float) -> str:
    """A float as plain digits, never scientific ('%g' would spell 1500000 as
    '1.5e+06'): '1500000', '300.5'."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def format_series(pairs: list[tuple[str, float]]) -> str:
    """The canonical spelling parse_series reads back: 'a: 1, b: 2.5'."""
    return ", ".join(f"{label}: {_plain_number(value)}" for label, value in pairs)


def format_chart_value(value: float) -> str:
    """A value as printed ON a chart (bar tops, the line graph's reveal):
    grouped for readability — '1,500,000', '2.5'. Not for the canonical series
    string, where a comma would read as an entry separator."""
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def data_fields_for(media_type_name: str) -> tuple[DataField, ...]:
    """What belongs in row["data"] for this media type — () for most types."""
    return MEDIA_TYPE_DATA_FIELDS.get(media_type_name, ())


def _coerce_data_value(field: DataField, raw: object) -> object:
    """One `data` value, checked and converted to the type its kind promises."""
    if field.kind == "text":
        text = str(raw).strip()
        if not text:
            raise ValueError(f"'{field.label}' cannot be empty")
        return text
    if field.kind in ("series", "shares"):
        pairs = parse_series(raw)  # ValueError names the bad entry
        lo, hi = ((SHARES_MIN_PARTS, SHARES_MAX_PARTS)
                  if field.kind == "shares"
                  else (SERIES_MIN_POINTS, SERIES_MAX_POINTS))
        if not lo <= len(pairs) <= hi:
            raise ValueError(
                f"'{field.label}' needs {lo}–{hi} 'label: value' pairs, "
                f"got {len(pairs)}"
            )
        if field.kind == "shares":
            negative = [lb for lb, v in pairs if v < 0]
            if negative:
                raise ValueError(
                    f"'{field.label}' values are parts of a whole and can't "
                    f"be negative ({', '.join(negative)})"
                )
            if not any(v > 0 for _, v in pairs):
                raise ValueError(f"'{field.label}' values are all zero")
        return format_series(pairs)
    if field.kind == "percent":
        try:
            value = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"'{field.label}' must be a number, got {raw!r}")
        if not 0 <= value <= 100:
            raise ValueError(
                f"'{field.label}' = {value:g} is not between 0 and 100"
            )
        return value
    if field.kind == "number":
        try:
            return float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"'{field.label}' must be a number, got {raw!r}")
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"'{field.label}' must be a whole number, got {raw!r}")
    if field.kind == "year" and not (DATA_YEAR_MIN <= value <= DATA_YEAR_MAX):
        raise ValueError(
            f"'{field.label}' = {value} is outside {DATA_YEAR_MIN}.."
            f"{DATA_YEAR_MAX} — that looks like a typo, not a year"
        )
    return value


def coerce_scene_data(
    media_type_name: str,
    raw: object,
    script_text: str,
    *,
    require_all: bool = True,
) -> dict:
    """Validate row["data"] against its type's DataFields and return the coerced
    object. Types with no fields must carry no data. Unknown keys and
    unconvertible values always raise — the tagger wrote this file, so anything
    else here is a bug worth stopping for.

    `require_all=False` allows a required field to be MISSING (but still checks
    every value that IS there). The tagger saves with it off, because a form is
    incomplete while you are typing into it; the loader keeps it on, because by
    render time a missing year has nowhere left to come from."""
    fields = data_fields_for(media_type_name)
    if raw in (None, ""):
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"'data' must be an object on scene '{script_text[:60]}', "
            f"got {type(raw).__name__}"
        )
    if not fields:
        if raw:
            raise ValueError(
                f"'{media_type_name}' takes no data, but scene "
                f"'{script_text[:60]}' carries {sorted(raw)}"
            )
        return {}
    known = {f.name for f in fields}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(
            f"unknown data key(s) {unknown} for '{media_type_name}' on scene "
            f"'{script_text[:60]}' (valid: {', '.join(sorted(known))})"
        )
    out: dict = {}
    for field in fields:
        if field.name not in raw or raw[field.name] in (None, ""):
            if field.required and require_all:
                raise ValueError(
                    f"'{media_type_name}' needs data.{field.name} "
                    f"({field.label}) on scene '{script_text[:60]}' — retag "
                    f"the line and fill it in"
                )
            continue
        try:
            out[field.name] = _coerce_data_value(field, raw[field.name])
        except ValueError as exc:
            raise ValueError(f"{exc} (scene '{script_text[:60]}')") from None
    return out


def scene_data(row: dict) -> dict:
    """row["data"] — the structured input for types the search term can't feed
    (a timeline's year, a chart's slices). {} for every other type."""
    return row.get("data") or {}


MediaType = Enum(  # type: ignore[misc]
    "MediaType", {name.upper(): name for name in MEDIA_TYPE_CATALOG}
)
MediaType.__doc__ = "One member per MEDIA_TYPE_CATALOG entry; value == name."


# ===========================================================================
# PER-TYPE PROPERTY TABLE  —  SINGLE SOURCE OF TRUTH
# ===========================================================================
# One row of booleans per MediaType. Everything in the codebase reads these
# via media_props(x).<flag>. _validate_media_map() refuses to import if a
# type is unlisted, so a new type is one obvious place to fill in.
@dataclass(frozen=True)
class MediaProperties:
    # ── external raw material ───────────────────────────────────────────
    needs_external_candidates: bool = False  # fetch Pexels/Wikipedia before generation
    uses_wikipedia: bool = False  # ...and that fetch is Wikipedia, not Pexels
    image_only: bool = False  # fetch a STILL only; one pick spans the scene
    # ── generators / stages ─────────────────────────────────────────────
    is_ai_base: bool = False  # valid base image for an ai-edit walk-back
    is_ai_edit: bool = False  # the ai-edit-previous stage handles it
    is_on_board: bool = False  # chosen clip composited onto a board base
    # ── derive-from-previous stages ─────────────────────────────────────
    acts_on_previous: bool = False  # derives its image from the previous scene
    is_hold_previous: bool = False  # freeze/reuse the previous image


MEDIA_PROPERTIES: dict[MediaType, MediaProperties] = {
    MediaType.STOCK: MediaProperties(needs_external_candidates=True),
    MediaType.AI_STOCK: MediaProperties(is_ai_base=True),
    MediaType.WIKIPEDIA: MediaProperties(
        needs_external_candidates=True, uses_wikipedia=True
    ),
    MediaType.MAP: MediaProperties(),
    MediaType.TYPOGRAPHY: MediaProperties(),
    MediaType.BLANK: MediaProperties(),  # generate_blank_scenes fills the frame
    MediaType.RANDOM_BACKGROUND: MediaProperties(),  # ...with a colour / a backdrop
    MediaType.STOCK_ON_BOARD: MediaProperties(
        needs_external_candidates=True, is_on_board=True
    ),
    MediaType.WIKIPEDIA_ON_BOARD: MediaProperties(
        needs_external_candidates=True, uses_wikipedia=True, is_on_board=True
    ),
    MediaType.HOLD_PREVIOUS: MediaProperties(
        acts_on_previous=True, is_hold_previous=True
    ),
    MediaType.BACKGROUND: MediaProperties(),  # nothing fetched/generated/reviewed —
    #   the scene stays footage-less until VIDEO_BACKGROUND_STAGE fills it with
    #   the bare background segment (stage 2.655, VIDEO_BACKGROUND_MODE only).
    MediaType.AI_EDIT_PREVIOUS: MediaProperties(
        is_ai_base=True, is_ai_edit=True, acts_on_previous=True
    ),
    # The maths family: all-default rows — generate_maths_scenes renders each
    # from row["data"], nothing fetched or reviewed.
    MediaType.TIMELINE: MediaProperties(),
    MediaType.COUNTER: MediaProperties(),
    MediaType.PROGRESS_BAR: MediaProperties(),
    MediaType.BAR_CHART: MediaProperties(),
    MediaType.PIE_CHART: MediaProperties(),
    MediaType.LINE_GRAPH: MediaProperties(),
}


def _validate_media_map() -> None:
    """Refuse to import if the catalog, the enum, or the property table drift."""
    defined, expected = set(MEDIA_PROPERTIES), set(MediaType)
    if defined != expected:
        raise RuntimeError(
            "MEDIA_PROPERTIES is out of sync with MediaType!\n"
            f"  missing definitions for: {expected - defined}\n"
            f"  extra definitions for:   {defined - expected}"
        )
    bad_groupable = GROUPABLE_TYPES - {t.value for t in MediaType}
    if bad_groupable:
        raise RuntimeError(f"GROUPABLE_TYPES has unknown names: {bad_groupable}")
    bad_gated = MIN_DURATION_GATED_TYPES - {t.value for t in MediaType}
    if bad_gated:
        raise RuntimeError(f"MIN_DURATION_GATED_TYPES has unknown names: {bad_gated}")


_validate_media_map()  # runs on import

_DEFAULT_PROPS = MediaProperties()


def media_props(mt: "MediaType | None") -> MediaProperties:
    """Property row for a MediaType. Unknown/None → an all-False default row,
    so `media_props(x).some_flag` is always safe."""
    return MEDIA_PROPERTIES.get(mt, _DEFAULT_PROPS)


# ===========================================================================
# SCENE ROWS  —  loading + per-row helpers
# ===========================================================================
class SearchTermData(TypedDict, total=False):
    search_term: str
    media_type: MediaType  # enum after normalise_scene_row (name string on disk)
    modifiers: list[str]  # stackable extras: decorate / group
    group_id: int | None  # lines sharing an id render as ONE group (rule of n)
    position: str  # cell number within the group ("1".."n")


_DEAD_LEGACY_COLUMNS = ("search_type", "template", "shot", "tier", "why")


def normalise_scene_row(script_text: str, row: dict) -> None:
    """Bring ONE json row up to the schema the renderer runs on, IN PLACE:
    row["media_type"] becomes a MediaType enum; modifiers/group_id/position
    are guaranteed present and validated. Old flat files (search_type-only)
    are NOT accepted — run UPGRADE_OLD_JSON.py once to convert them."""
    for dead in _DEAD_LEGACY_COLUMNS:
        row.pop(dead, None)  # ignored if an upgraded file still carries them

    name = row.get("media_type")
    if isinstance(name, MediaType):
        name = name.value
    name = (name or "").strip()
    if not name:
        raise ValueError(
            f"scene '{script_text[:60]}' has no media_type. Old flat files "
            f"(search_type only) must be converted once with: "
            f"uv run UPGRADE_OLD_JSON.py <file>"
        )
    if name not in MEDIA_TYPE_CATALOG:
        raise ValueError(
            f"unknown media_type {name!r} on scene '{script_text[:60]}' "
            f"(valid: {', '.join(MEDIA_TYPE_CATALOG)})"
        )
    row.setdefault("modifiers", [])
    row.setdefault("group_id", None)
    row.setdefault("position", "1")
    # set for real by resolve_group_continuations() — it needs the neighbours,
    # which one row on its own cannot see.
    row.setdefault("group_continuation", False)
    unknown = [m for m in row["modifiers"] if m not in MODIFIERS]
    if unknown:
        raise ValueError(
            f"unknown modifier(s) {unknown} on scene '{script_text[:60]}' "
            f"(valid: {', '.join(MODIFIERS)})"
        )
    if "group" in row["modifiers"] and name not in GROUPABLE_TYPES \
            and name != GROUP_CONTINUATION_TYPE:
        raise ValueError(
            f"'{name}' cannot take the group modifier on scene "
            f"'{script_text[:60]}' (a group OPENS with "
            f"{' / '.join(sorted(GROUPABLE_TYPES))} + group and CONTINUES "
            f"with {GROUP_CONTINUATION_TYPE} + group)"
        )
    if "collage" in row["modifiers"] and name not in COLLAGEABLE_TYPES:
        raise ValueError(
            f"'{name}' cannot take the collage modifier on scene "
            f"'{script_text[:60]}' (collageable: {', '.join(sorted(COLLAGEABLE_TYPES))})"
        )
    if {"group", "collage"} <= set(row["modifiers"]):
        raise ValueError(
            f"group and collage cannot combine on scene '{script_text[:60]}' "
            f"(group = one cell per LINE across neighbours; collage = many "
            f"images on THIS line)"
        )
    row.setdefault("stamp_source", None)
    if row["stamp_source"] == "":
        row["stamp_source"] = None
    if (
        row["stamp_source"] is not None
        and row["stamp_source"] not in STAMP_SOURCE_TYPES
    ):
        raise ValueError(
            f"unknown stamp_source {row['stamp_source']!r} on scene "
            f"'{script_text[:60]}' "
            f"(valid: {', '.join(STAMP_SOURCE_TYPES)}, or null)"
        )
    row["stamp_decorate"] = bool(row.get("stamp_decorate", False))
    # structured input for the types a search term can't feed (see DataField)
    row["data"] = coerce_scene_data(name, row.get("data"), script_text)
    row["media_type"] = MediaType(name)


def scene_type(row: dict) -> "MediaType | None":
    """The row's MediaType (after normalise_scene_row). None-safe."""
    mt = row.get("media_type")
    return mt if isinstance(mt, MediaType) else None


def scene_is_grouped(row: dict) -> bool:
    return "group" in (row.get("modifiers") or [])


def scene_is_group_continuation(row: dict) -> bool:
    """This grouped row CONTINUES the group above it (it was tagged
    `hold_previous` + group) rather than opening one. Only meaningful after
    resolve_group_continuations(), which rewrites the row's media_type to the
    group's base and leaves this flag behind to remember what was written."""
    return bool(row.get("group_continuation"))


def _media_type_name(row: dict) -> str:
    """The row's media_type as a plain string, whether it is still the raw
    json string or has already been normalised to a MediaType."""
    name = row.get("media_type")
    if isinstance(name, MediaType):
        return name.value
    return (name or "").strip()


def resolve_group_continuations(scenes: dict[str, dict]) -> None:
    """Give every CONTINUATION cell of a group its group's real base, IN PLACE.

    The tagger writes a group the way it reads on screen: the first line picks
    the base and the modifier (`stock` + group), and each line that joins it is
    `hold_previous` + group. But nothing downstream knows about that spelling —
    the candidate fetch, the joint compositor and the ai tile generator all
    dispatch on media_type, and every cell fetches its OWN picture from the
    group's base. So the spelling is resolved once, here, right after load:
    each continuation row takes the opener's media_type and group_id, and
    remembers what it was with `group_continuation`.

    Works on raw rows (media_type is a string) and normalised ones (a
    MediaType) alike — the rewritten value copies the opener's form. Raises
    ValueError on a continuation with no group open above it, on a group opened
    by a base with no grid layout, and on a group longer than its layout."""
    opener: dict | None = None
    opener_text = ""
    run = 0
    for script_text, row in scenes.items():
        if not scene_is_grouped(row):
            opener, run = None, 0
            continue
        name = _media_type_name(row)
        if name != GROUP_CONTINUATION_TYPE:
            if name not in GROUPABLE_TYPES:
                raise ValueError(
                    f"'{name}' cannot open a group (scene "
                    f"'{script_text[:60]}'). Groupable bases: "
                    f"{', '.join(sorted(GROUPABLE_TYPES))}."
                )
            opener, opener_text, run = row, script_text, 1
            row["group_continuation"] = False
            continue
        if opener is None:
            raise ValueError(
                f"scene '{script_text[:60]}' is '{GROUP_CONTINUATION_TYPE}' + "
                f"group, but no group is open above it. A group OPENS with "
                f"{' / '.join(sorted(GROUPABLE_TYPES))} + group."
            )
        run += 1
        cells = JOINT_GROUP_CELLS[_media_type_name(opener)]
        if run > cells:
            raise ValueError(
                f"the group starting at '{opener_text[:60]}' has {run} cells, "
                f"but a group of {_media_type_name(opener)} draws {cells}. "
                f"Ungroup '{script_text[:60]}' or start a new group there."
            )
        row["media_type"] = opener["media_type"]
        row["group_id"] = opener.get("group_id")
        row["group_continuation"] = True


def scene_wants_decorate(row: dict) -> bool:
    return "decorate" in (row.get("modifiers") or [])


def scene_wants_collage(row: dict) -> bool:
    return "collage" in (row.get("modifiers") or [])


def scene_wants_caption(row: dict) -> bool:
    return "caption" in (row.get("modifiers") or [])


def scene_stamp_source(row: dict) -> "str | None":
    """Where the decorate editor's stamp tab should be pre-loaded from
    (row['stamp_source']), or None. Only meaningful together with a
    non-empty search_term — the term IS the stamp query."""
    src = (row or {}).get("stamp_source") or None
    if not src or not (row.get("search_term") or "").strip():
        return None
    return str(src)


def group_scene_rows(
    scenes: list[tuple[str, "SearchTermData"]],
) -> list[list[tuple[str, "SearchTermData"]]]:
    """Split an ORDERED (script-order) list of grouped scenes into render
    groups: a scene joins the one above it when it CONTINUES it (it was tagged
    `hold_previous` + group — see resolve_group_continuations), or when the two
    share the SAME media_type AND the SAME non-null group_id."""
    groups: list[list[tuple[str, "SearchTermData"]]] = []
    current: list[tuple[str, "SearchTermData"]] = []
    prev: "SearchTermData | None" = None
    for text, row in scenes:
        same = prev is not None and (
            scene_is_group_continuation(row)
            or (
                row.get("media_type") == prev.get("media_type")
                and row.get("group_id") is not None
                and row.get("group_id") == prev.get("group_id")
            )
        )
        if prev is not None and not same:
            groups.append(current)
            current = []
        current.append((text, row))
        prev = row
    if current:
        groups.append(current)
    return groups


# ===========================================================================
# DECORATE EDITOR (the `decorate` modifier)
# ===========================================================================
# ONE interactive editor, applied to the scene's OWN finished footage, with
# clickable tools: draw, text/caption, zoom/crop. It replaces the old
# decorate_previous, stickman_text_overlay and zoom_prev_img types. The
# stage lives in DECORATE_STAGE.py.
DECORATE_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/decorate_scenes")
DECORATE_RENDER_SAFETY_PAD_SEC: float = 0.08

# --- STAMP SOURCE (the editor's stamp tab, fed by the NORMAL review) -------
# A hold_previous + decorate scene doesn't use its search_term for its own
# footage (it reuses the previous image) — so when one HAS a term, the term
# describes what to STAMP ("jar of nutmeg"). row["stamp_source"] says which
# media type those candidates are fetched AS, and the scene then goes
# through the ORDINARY candidates fetch + stage-1 review like any stock /
# wikipedia / ai_stock scene (main() swaps its media_type to the source for
# those two stages — DECORATE_STAGE.swap/restore_stamp_rows_…): you CLICK
# the picture you want in the review, and that pick becomes the stamp
# waiting (pre-loaded + active) in the decorate editor's stamp tab.
# row["stamp_decorate"] additionally opens the pick in the decorator FIRST
# (cut it out / clean it up) before it's offered as a stamp.
# The tagging tool's step 3 sets both (only shown on a TERM_OPTIONAL_TYPES
# line + decorate + a non-empty term), and search terms are OPTIONAL for the
# TERM_OPTIONAL_TYPES — a bare hold / background / blank line needs none,
# because none of them fetch anything with it.
STAMP_SOURCE_TYPES: tuple[str, ...] = ("stock", "wikipedia", "ai_stock")
TERM_OPTIONAL_TYPES: tuple[str, ...] = (
    "hold_previous", "background", "blank", "random_background",
    # the maths family is driven by row["data"], not by a search term
    "timeline", "counter", "progress_bar", "bar_chart", "pie_chart",
    "line_graph",
)

# --- LIVE VIDEO DECORATE (decorations layered over PLAYING footage) --------
# When a stock VIDEO scene is followed by hold_previous scenes and ANY member
# of that run (the video scene itself, or any of the holds) carries the
# `decorate` modifier, the whole run becomes a CONTINUING CHAIN: the source
# keeps playing across the scene cuts (each scene starts where the previous
# one stopped — 2.3s + 1.2s + 1.0s reads as ONE 4.5s take) and each scene's
# decorations are rendered as TRANSPARENT LAYERS burned over the moving
# footage, ACCUMULATING down the chain. In the editor you decorate the exact
# frame on screen when your scene starts (earlier layers included); only the
# draw + stamp tools are offered (zoom/object change the picture's geometry,
# which can't sit over moving video). Hold runs with no decorate anywhere
# keep the old freeze behaviour, as does everything when this is False.
# Machinery: VIDEO_CHAINS.py; wired into STATIC_RENDER (segments) and
# DECORATE_STAGE (editor + burn).
DECORATE_VIDEO_LIVE: bool = True
# Extra tail on each continuing segment so the stitcher's frame budget never
# runs out of file (the stitcher trims — same idea as the other pads).
VIDEO_CHAIN_SEGMENT_PAD_SEC: float = 0.35

# ===========================================================================
# VIDEO BACKGROUND MODE  (the whole edit rides on YOUR footage)
# ===========================================================================
# OPTIONAL: instead of scenes replacing each other full-frame, ONE long
# background video (e.g. 30 minutes of you walking) plays continuously under
# the entire edit. It starts at VIDEO_BACKGROUND_START and is trimmed to the
# narration's length (a FATAL error if there isn't enough video left).
# Per scene, on top of the moving background:
#   - a scene that ends with footage becomes an overlay: photographic stuff
#     (stock video/images, wikipedia, ...) is scaled to VIDEO_BG_CARD_SCALE
#     of the frame and shown as a CARD (white border + soft shadow, matching
#     the collage look); anything with a TRANSPARENT or ~WHITE background
#     (stickman renders, maps, cut-outs) is auto-detected and KEYED so the
#     graphic floats directly over your footage;
#   - a `background` scene (or any line that ends with no footage) shows the
#     bare background — that's how "only when crucial" is expressed: leave
#     the line as background and nothing pops on;
#   - `background` + decorate opens the LIVE overlay editor ON the exact
#     background frame where that line starts, so you draw arrows/text
#     straight onto your own footage (same machinery as the decorate video
#     chains); + caption burns the tilted caption over it.
# Runs at stage 2.655 — after colour grade + Ken Burns (so cards carry their
# grade/motion), before the auto-overlay badges (so badges land on the full
# composited frame). Editing is unchanged: you review/decorate each scene's
# own graphic exactly as today and never need to see the background while
# doing it. NOTE: the background itself is NOT colour graded.
VIDEO_BACKGROUND_MODE: bool = False
# The long source video the edit rides on.
VIDEO_BACKGROUND_FILE: str = ""
# Where in that video the narration begins: seconds (float) or "mm:ss" /
# "hh:mm:ss". The video must run at least the narration's length past this.
VIDEO_BACKGROUND_START: float | str = 0.0
VIDEO_BG_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/video_background_scenes")
# --- card look (opaque/photographic overlays) ---
VIDEO_BG_CARD_SCALE: float = 0.80  # card box, fraction of the frame
VIDEO_BG_CARD_BORDER_PX: int = 10  # white polaroid border (0 = none)
VIDEO_BG_CARD_SHADOW: bool = True  # soft drop shadow under the card
# --- keying (transparent / white-backed overlays float directly) ---
VIDEO_BG_KEY_AUTO: bool = True  # detect + key per CLIP (off = all cards)
VIDEO_BG_KEY_WHITE_MIN: int = 235  # border pixel counts as white from here
VIDEO_BG_KEY_BORDER_FRAC: float = 0.60  # ≥ this share of white border → keyed
VIDEO_BG_KEY_SIMILARITY: float = 0.10  # ffmpeg colorkey similarity (video clips)
VIDEO_BG_KEY_BLEND: float = 0.08  # ffmpeg colorkey edge blend

# ===========================================================================
# COLLAGE (the `collage` modifier — several review picks on ONE line)
# ===========================================================================
# Auto mode scatters the picks with overlaps onto COLLAGE_BACKGROUND (an
# image path, a '#rrggbb' colour, or None for the default plain card);
# stamp-yourself mode loads the picks into the decorate editor as stamps.
COLLAGE_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/collage_scenes")
COLLAGE_BACKGROUND: str | None = None
COLLAGE_NUM_PICKS: int = 3  # review slots (and images fetched) per collage row
COLLAGE_RENDER_SAFETY_PAD_SEC: float = 0.08


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
# it filters rows by media_type == "ai_stock" (grouped or not — see its args).
STICKMAN_PROMPTS_FILE: str = LINE_INDEX_TO_SEARCH_TERM_FILE

# Where generated PNGs are written (cache-scoped, like joint/read-out output).
STICKMAN_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/stickman_scenes")

# Stickman-joint scenes reuse the AI stickman generator to produce the TILES
# that feed the joint compositor (instead of Pexels stills). The compositor
# only ever consumes the first image per scene, so we generate 1 variant.
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

STICKMAN_EXPLAIN_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/stickman_explain_scenes")
STICKMAN_EXPLAIN_RENDER_SAFETY_PAD_SEC: float = 0.08


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
MANUAL_STOCK_PLACEMENT_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/manual_stock_placement")
MANUAL_STOCK_PLACEMENT_RENDER_SAFETY_PAD_SEC: float = 0.08


# ===========================================================================
# JOINT SCENE LAYOUTS
# ===========================================================================
# TODO - consider moving this to the JOINT_IMAGE_CREATOR file

# Keyed by the BASE type of a group (the `group` modifier decides that a
# run of lines is a group; the base decides where the tiles come from).
JOINT_LAYOUT_POSITIONS: dict[MediaType, list[list[int]]] = {
    MediaType.STOCK: [
        [25, 50],
        [50, 50],
        [75, 50],
    ],
    MediaType.AI_STOCK: [
        [25, 50],
        [50, 50],
        [75, 50],
    ],
}


def _validate_joint_layouts() -> None:
    missing = {n for n in GROUPABLE_TYPES if MediaType(n) not in JOINT_LAYOUT_POSITIONS}
    if missing:
        raise RuntimeError(
            f"groupable types missing a JOINT_LAYOUT_POSITIONS entry: {missing}"
        )


_validate_joint_layouts()


# How many CELLS a group of each base type has — i.e. how many consecutive
# lines may share one group_id. This is the layout's own length, so "rule of
# n" is really "rule of however many tiles the layout draws" (3 today, for
# both bases). generate_joint_scenes hard-exits when a group has more members
# than its layout has positions, so the tagger reads this and refuses to build
# an over-long group in the first place — the failure moves from the middle of
# a render to the moment you click.
JOINT_GROUP_CELLS: dict[str, int] = {
    mt.value: len(positions) for mt, positions in JOINT_LAYOUT_POSITIONS.items()
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
    MediaType.STOCK: {
        "path": "se-pop.mp3",
        "timing": "loop_start",
    },
    MediaType.AI_STOCK: {
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
