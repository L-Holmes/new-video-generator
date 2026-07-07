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

# Allow `uv run ___visuals/<file>.py` from the repo root: when a package file
# is executed directly, python puts ___visuals/ (not the root) on sys.path,
# so `from ___visuals...` imports fail. This puts the root back. Paste the
# same 4 lines at the top of any package file you want to run directly.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import TypedDict
from dataclasses import dataclass

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
    NEW = "new"                      # puts brand-new material on screen
    EDIT_PREVIOUS = "edit_previous"  # acts on the image already on screen
    AI = "ai"                        # ai-generated look (red buttons)
    BOARD = "board"                  # sits on the stickman explain board


# ---------------------------------------------------------------------------
# BASE MEDIA TYPES — pick exactly one per line. The NAME is the value the
# renderer dispatches on; there is no separate legacy string.
# ---------------------------------------------------------------------------
MEDIA_TYPE_CATALOG: dict[str, dict] = {
    "stock": {
        "tags": [Tag.NEW], "color": "#2e6da4",
        "info": "footage or an image fetched from the stock library. the "
                "workhorse — most lines are this. stack group for a grid "
                "of them.",
        "example": "examples/stock.png",
    },
    "ai_stock": {
        "tags": [Tag.NEW, Tag.AI], "color": "#c0392b",
        "info": "an ai-generated picture in the channel's stickman style. "
                "the search term is a scene prompt, not a search. stack "
                "group for a grid of them.",
        "example": "examples/ai_stock.png",
    },
    "wikipedia": {
        "tags": [Tag.NEW], "color": "#148f77",
        "info": "the image from a wikipedia article. the search term must "
                "be the exact article name ('Banda Islands').",
        "example": "examples/wikipedia.png",
    },
    "map": {
        "tags": [Tag.NEW], "color": "#1e8449",
        "info": "a rendered map with the place highlighted. the search "
                "term is just the place name ('Indonesia').",
        "example": "examples/map.png",
    },
    "typography": {
        "tags": [Tag.NEW], "color": "#8d6e2f",
        "info": "the line's own words animate on a blank background. good "
                "cold-open when there is nothing to picture yet.",
        "example": "examples/typography.png",
    },
    "stock_on_board": {
        "tags": [Tag.NEW, Tag.AI, Tag.BOARD], "color": "#e74c3c",
        "info": "stock footage shown on the stickman's explain board.",
        "example": "examples/stock_on_board.png",
    },
    "wikipedia_on_board": {
        "tags": [Tag.NEW, Tag.AI, Tag.BOARD], "color": "#e74c3c",
        "info": "a wikipedia image shown on the stickman's explain board.",
        "example": "examples/wikipedia_on_board.png",
    },
    "hold_previous": {
        "tags": [Tag.EDIT_PREVIOUS], "color": "#5c6bc0",
        "info": "keep the previous image on screen, frozen. the default "
                "for quick mid-sentence beats — stack decorate on it to "
                "draw on it, stamp pictures into it, zoom, or cut objects "
                "out (everything manual lives in the ONE decorate editor).",
        "example": "examples/hold_previous.png",
    },
    "background": {
        "tags": [Tag.NEW], "color": "#4a4a55",
        "info": "nothing on top — the BACKGROUND VIDEO just plays for this "
                "line (VIDEO_BACKGROUND_MODE only; the search term is "
                "unused). stack decorate to draw straight onto the "
                "background footage, or caption for a tilted caption over "
                "it. outside background mode this renders NOTHING — don't "
                "use it there.",
        "example": "examples/background.png",
    },
    "ai_edit_previous": {
        "tags": [Tag.EDIT_PREVIOUS, Tag.AI], "color": "#a93226",
        "info": "ai edits the previous ai image in place. the search term "
                "is the change ('add a second coin').",
        "example": "examples/ai_edit_previous.png",
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
        "info": "this line is one cell of a group with its neighbours "
                "(rule of n: mark 3 lines in a row and they render as 3 "
                "cells side by side). a group OF stock, a group OF ai "
                "stock — the base type stays whatever you picked.",
        "example": "examples/group.png",
    },
}

# Which base types accept the group modifier (they have grid layouts).
GROUPABLE_TYPES: set[str] = {"stock", "ai_stock"}

# Which base types accept the collage modifier (multi-pick in review).
COLLAGEABLE_TYPES: set[str] = {"stock"}


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
    uses_wikipedia: bool = False             # ...and that fetch is Wikipedia, not Pexels
    image_only: bool = False                 # fetch a STILL only; one pick spans the scene
    # ── generators / stages ─────────────────────────────────────────────
    is_ai_base: bool = False                 # valid base image for an ai-edit walk-back
    is_ai_edit: bool = False                 # the ai-edit-previous stage handles it
    is_on_board: bool = False                # chosen clip composited onto a board base
    # ── derive-from-previous stages ─────────────────────────────────────
    acts_on_previous: bool = False           # derives its image from the previous scene
    is_hold_previous: bool = False           # freeze/reuse the previous image


MEDIA_PROPERTIES: dict[MediaType, MediaProperties] = {
    MediaType.STOCK: MediaProperties(needs_external_candidates=True),
    MediaType.AI_STOCK: MediaProperties(is_ai_base=True),
    MediaType.WIKIPEDIA: MediaProperties(needs_external_candidates=True, uses_wikipedia=True),
    MediaType.MAP: MediaProperties(),
    MediaType.TYPOGRAPHY: MediaProperties(),
    MediaType.STOCK_ON_BOARD: MediaProperties(needs_external_candidates=True, is_on_board=True),
    MediaType.WIKIPEDIA_ON_BOARD: MediaProperties(needs_external_candidates=True, uses_wikipedia=True, is_on_board=True),
    MediaType.HOLD_PREVIOUS: MediaProperties(acts_on_previous=True, is_hold_previous=True),
    MediaType.BACKGROUND: MediaProperties(),   # nothing fetched/generated/reviewed —
    #   the scene stays footage-less until VIDEO_BACKGROUND_STAGE fills it with
    #   the bare background segment (stage 2.655, VIDEO_BACKGROUND_MODE only).
    MediaType.AI_EDIT_PREVIOUS: MediaProperties(is_ai_base=True, is_ai_edit=True, acts_on_previous=True),
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
    media_type: MediaType    # enum after normalise_scene_row (name string on disk)
    modifiers: list[str]     # stackable extras: decorate / group
    group_id: int | None     # lines sharing an id render as ONE group (rule of n)
    position: str            # cell number within the group ("1".."n")


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
    unknown = [m for m in row["modifiers"] if m not in MODIFIERS]
    if unknown:
        raise ValueError(
            f"unknown modifier(s) {unknown} on scene '{script_text[:60]}' "
            f"(valid: {', '.join(MODIFIERS)})"
        )
    if "group" in row["modifiers"] and name not in GROUPABLE_TYPES:
        raise ValueError(
            f"'{name}' cannot take the group modifier on scene "
            f"'{script_text[:60]}' (groupable: {', '.join(sorted(GROUPABLE_TYPES))})"
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
    if row["stamp_source"] is not None \
            and row["stamp_source"] not in STAMP_SOURCE_TYPES:
        raise ValueError(
            f"unknown stamp_source {row['stamp_source']!r} on scene "
            f"'{script_text[:60]}' "
            f"(valid: {', '.join(STAMP_SOURCE_TYPES)}, or null)"
        )
    row["media_type"] = MediaType(name)


def scene_type(row: dict) -> "MediaType | None":
    """The row's MediaType (after normalise_scene_row). None-safe."""
    mt = row.get("media_type")
    return mt if isinstance(mt, MediaType) else None


def scene_is_grouped(row: dict) -> bool:
    return "group" in (row.get("modifiers") or [])


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
    groups: consecutive scenes sharing the SAME media_type AND the SAME
    non-null group_id are one group."""
    groups: list[list[tuple[str, "SearchTermData"]]] = []
    current: list[tuple[str, "SearchTermData"]] = []
    prev: "SearchTermData | None" = None
    for text, row in scenes:
        same = (
            prev is not None
            and row.get("media_type") == prev.get("media_type")
            and row.get("group_id") is not None
            and row.get("group_id") == prev.get("group_id")
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

# --- STAMP SOURCE (pre-loading the decorate editor's stamp tab) ------------
# A hold_previous + decorate scene doesn't use its search_term for its own
# footage (it reuses the previous image) — so when one HAS a term, the term
# describes what to STAMP ("jar of nutmeg"). row["stamp_source"] says where
# those pictures come from; DECORATE_STAGE fetches them (STAMP_FETCH) and
# passes them to the editor, whose stamp tab opens pre-loaded (and active).
# Valid values: the NEW-material types the fetcher supports, or null.
# The tagging tool offers these as its step 3 (only when hold_previous +
# decorate + a non-empty term), and search terms are OPTIONAL for the
# TERM_OPTIONAL_TYPES (a bare hold/background line needs none).
STAMP_SOURCE_TYPES: tuple[str, ...] = ("stock", "wikipedia")
TERM_OPTIONAL_TYPES: tuple[str, ...] = ("hold_previous", "background")
STAMPS_CACHE_DIR: Path = Path(f"{_CACHE_DIR}/stamp_fetch")
STAMP_FETCH_COUNT: int = 6           # pictures offered per stamp term

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
VIDEO_BG_CARD_SCALE: float = 0.80      # card box, fraction of the frame
VIDEO_BG_CARD_BORDER_PX: int = 10      # white polaroid border (0 = none)
VIDEO_BG_CARD_SHADOW: bool = True      # soft drop shadow under the card
# --- keying (transparent / white-backed overlays float directly) ---
VIDEO_BG_KEY_AUTO: bool = True         # detect + key per CLIP (off = all cards)
VIDEO_BG_KEY_WHITE_MIN: int = 235      # border pixel counts as white from here
VIDEO_BG_KEY_BORDER_FRAC: float = 0.60 # ≥ this share of white border → keyed
VIDEO_BG_KEY_SIMILARITY: float = 0.10  # ffmpeg colorkey similarity (video clips)
VIDEO_BG_KEY_BLEND: float = 0.08       # ffmpeg colorkey edge blend

# ===========================================================================
# COLLAGE (the `collage` modifier — several review picks on ONE line)
# ===========================================================================
# Auto mode scatters the picks with overlaps onto COLLAGE_BACKGROUND (an
# image path, a '#rrggbb' colour, or None for the default plain card);
# stamp-yourself mode loads the picks into the decorate editor as stamps.
COLLAGE_OUTPUT_DIR: Path = Path(f"{_CACHE_DIR}/collage_scenes")
COLLAGE_BACKGROUND: str | None = None
COLLAGE_NUM_PICKS: int = 3     # review slots (and images fetched) per collage row
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
        raise RuntimeError(f"groupable types missing a JOINT_LAYOUT_POSITIONS entry: {missing}")


_validate_joint_layouts()


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
