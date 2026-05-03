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

OLLAMA_MODEL = "qwen2.5:7b"
SCENE_MAP_CACHE_FILE = f"{_CACHE_DIR}/scene_map_cache.json"

OUTPUT_FILE = f"{_OUTPUT_DIR}/output.mp4"
TEMP_DIR    = Path("tmp_stitch/")

SCRIPT_AUDIO_FILE = f"{_SCRIPT_STEM}.wav"
SCRIPT_LINES_FILE = f"{_CACHE_DIR}/scene_map_cache.json"
SYNCHRONIZED_SCRIPT_OUTPUT_FILE = f"{_CACHE_DIR}/script_timings_seconds.json"
AUDIO_START_DELAY_SECONDS = 0.5

STOCK_FOOTAGE_TO_DOWNLOADED_MEDIA_FILE = f"{_CACHE_DIR}/stock_footage/history.json"
REVIEW_STOCK_FOOTAGE_OUTPUT_FILE       = f"{_CACHE_DIR}/stock_footage/review_accepting_footage.json"

FINAL_SCRIPT_AND_CLIPS = f"{_CACHE_DIR}/final_script_to_clips.json"

LINE_INDEX_TO_SEARCH_TERM_FILE = "line-index-to-search-term.json"
# ===========================================================================
# Create all required dirs and files on startup
# ===========================================================================

Path(_CACHE_DIR).mkdir(parents=True, exist_ok=True)
Path(_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Empty JSON files that other modules try to open before anything is written
for _f in [SCENE_MAP_CACHE_FILE, SYNCHRONIZED_SCRIPT_OUTPUT_FILE,
           FINAL_SCRIPT_AND_CLIPS, STOCK_FOOTAGE_TO_DOWNLOADED_MEDIA_FILE,
           REVIEW_STOCK_FOOTAGE_OUTPUT_FILE]:
    _p = Path(_f)
    if not _p.exists():
        _p.write_text("{}")

# ===========================================================================
# STEP 0  –  READ IN SCRIPT
# ===========================================================================

def read_in_script() -> str:
    """
    'Open the script.txt file.'

    Reads SCRIPT_FILE from disk and returns its full contents as a string.
    Raises FileNotFoundError with a helpful message if the file is missing.
    """
    # et.log(DEBUG, f"Step 0: reading script from '{SCRIPT_FILE}'")
script_path: Path = Path(SCRIPT_FILE)
    # Resolved path object for the script file.
    # e.g. PosixPath("script.txt")

    if not script_path.exists():
        raise FileNotFoundError(
            f"Script file not found: '{SCRIPT_FILE}'. "
            "Create the file and add your script before running."
        )

    contents: str = script_path.read_text(encoding="utf-8")
    # Full raw text of the script, newlines and all.
    # e.g. "The white stork glided across the sky.$\nIt landed upon the farmhouse.$\n…"

    # et.log(DEBUG, f"  Read {len(contents)} characters from '{SCRIPT_FILE}'")
    return contents


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
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _load_scene_map_cache() -> dict | None:
    """
    Load cached scene map from disk.  Returns None on miss / error.

    File: scene_map_cache.json
        { "The Empire State Building is really big.": "empire state building", ... }
    """
    p = Path(SCENE_MAP_CACHE_FILE)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None

def _save_scene_map_cache(scene_map: dict) -> None:
    """Persist scene map so Step 1 AI calls are skipped on re-runs."""
    cache_path = Path("CACHE/scene_map_cache.json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)   # <-- auto-create folder
    cache_path.write_text(json.dumps(scene_map, indent=2))

def _split_on_headings(script: str) -> list[str]:
    """
    Split a markdown-style script into sections wherever a '#' heading appears.

    The heading line is kept at the top of its section so the AI has
    topical context, but it will not be treated as a scene line.

    Parameters
    ----------
    script : str
        Full script text, possibly multi-section.

    Returns
    -------
    list[str]
        One string per section.  Empty sections are dropped.

    Example
    -------
    >>> script = '''
    ... # intro
    ... The empire state building is really big.
    ... Built in Manhattan in the 19th century.
    ... # tea
    ... But where exactly did this tea originate?
    ... '''
    >>> _split_on_headings(script)
    [
        "# intro\\nThe empire state building is really big.\\nBuilt in Manhattan in the 19th century.",
        "# tea\\nBut where exactly did this tea originate?"
    ]
    """
    sections: list[str] = []
    current_lines: list[str] = []

    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#"):
            # Flush whatever we've accumulated so far as a completed section
            if current_lines:
                sections.append("\n".join(current_lines))
            # Start the new section with the heading for AI context
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append("\n".join(current_lines))

    return [s for s in sections if s.strip()]


def _get_ai_response(prompt: str) -> str:
    """
    Send a prompt to the local Ollama model and return its text reply.

    Parameters
    ----------
    prompt : str
        The full prompt to send.

    Returns
    -------
    str
        The model's response text.
    """
    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return response["message"]["content"]

# ---------------------------------------------------------------------------

_nlp = spacy.load("en_core_web_sm")

# Pronouns and vague words that signal we NEED context to resolve
_REFERENCE_WORDS = {
    "they", "them", "their", "it", "its", "this", "that", "these",
    "those", "he", "she", "we", "there", "here", "one",
}


def _line_needs_context_resolution(line: str) -> bool:
    """
    Return True if the line contains pronouns or vague references that
    require prior context to resolve — i.e. NLP alone will produce a
    useless or misleading search term.

    Parameters
    ----------
    line : str
        e.g. "they were created by the romans."    → True  ('they' = ?)
             "giant pedestal"                      → True  (pedestal of what?)
             "the empire state building is big."   → False (self-contained)

    Examples
    --------
    >>> _line_needs_context_resolution("they drove up outside.")
    True
    >>> _line_needs_context_resolution("The empire state building is big.")
    False
    """
    tokens = {tok.text.lower() for tok in _nlp(line)}
    return bool(tokens & _REFERENCE_WORDS)


def _resolve_references_with_ai(line: str, previous_lines: list[str]) -> str:
    """
    Ask AI to rewrite ONE line, replacing pronouns / vague references with
    their actual subjects — using previous lines as context.

    This is the simplest possible AI task: pure substitution, no creativity,
    no imagery generation, no search term generation. Just: replace 'they'
    with what 'they' actually refers to.

    Parameters
    ----------
    line : str
        The line with unresolved references.
        e.g. "they were created by the romans."

    previous_lines : list[str]
        The 1–3 lines immediately before this one in the script.
        e.g. ["the empire state building is really big.",
              "built on a giant pedestal."]

    Returns
    -------
    str
        Rewritten line with references resolved.
        e.g. "the pedestals were created by the romans."
        Falls back to original line if AI reply looks malformed.

    Examples
    --------
    Context: ["the empire state building is big.", "built on a giant pedestal."]
    Input:   "they were created by the romans."
    Output:  "the pedestals of the empire state building were created by the romans."
    """
    context_block = "\n".join(f"  {l}" for l in previous_lines)
    prompt = (
        "Rewrite the TARGET LINE by replacing any pronouns or vague references "
        "with the actual noun they refer to, using the CONTEXT LINES to identify it.\n"
        "Output ONLY the rewritten line. No explanation. No extra text.\n\n"
        f"CONTEXT LINES (for reference only — do not describe these):\n{context_block}\n\n"
        f"TARGET LINE: {line}\n\n"
        "REWRITTEN LINE:"
    )
    raw = _get_ai_response(prompt).strip()

    # Sanity check: if AI returns multiple lines or something huge, fall back
    first_line = raw.splitlines()[0].strip() if raw else ""
    if first_line and len(first_line) < len(line) * 3:
        return first_line
    return line     # fallback: use original, NLP will do its best


def _extract_nouns_spacy(line: str) -> list[str]:
    """
    Extract content words from a line using spaCy POS tags.
    No manual stop word list needed — function words (DET, ADP, AUX,
    CCONJ, PRON) are simply not NOUN/PROPN so they're never included.
    Unknown words like 'chanoyu' are handled via grammatical context.

    Priority: named entity spans → PROPN → NOUN

    Parameters
    ----------
    line : str
        A resolved (pronoun-free) narration line.
        e.g. "where they drunk chanoyu tea"

    Returns
    -------
    list[str]
        e.g. ["chanoyu", "tea"]
        Most specific/rare terms first (proper nouns before common nouns).
        Returns [] only if line is truly content-free (e.g. "Back in 1946,").

    Examples
    --------
    >>> _extract_nouns_spacy("the technician John Ford the second")
    ["John Ford", "technician"]

    >>> _extract_nouns_spacy("where they drunk chanoyu tea")
    ["chanoyu", "tea"]

    >>> _extract_nouns_spacy("Back in 1946,")
    []                  # no nouns — caller appends date/number as fallback
    """
    doc = _nlp(line)
    terms: list[str] = []
    seen: set[str] = set()

    # 1) Named entity spans first — kept whole ("Empire State Building", "John Ford")
    for ent in doc.ents:
        text = ent.text.strip()
        if text.lower() not in seen:
            terms.append(text)
            seen.update(t.lower() for t in text.split())

    # 2) Remaining PROPN then NOUN tokens not covered by an entity
    entity_token_ids = {tok.i for ent in doc.ents for tok in ent}
    proper, common = [], []
    for tok in doc:
        if tok.i in entity_token_ids:
            continue
        word = tok.text.strip(".,;:\"'").lower()
        if len(word) < 2 or word in seen:
            continue
        if tok.pos_ == "PROPN":
            proper.append(tok.text)
            seen.add(word)
        elif tok.pos_ == "NOUN":
            common.append(tok.text.lower())
            seen.add(word)

    terms.extend(proper)
    terms.extend(common)

    # 3) Fallback: if still empty, grab any NUM tokens (catches "1946", "19th")
    if not terms:
        for tok in doc:
            if tok.pos_ == "NUM":
                terms.append(tok.text)

    return terms[:4]    # cap at 4 — Pexels results degrade beyond that


def get_script_text_to_stock_footage_search(scene_lines: list[str]) -> dict[str, str]:
    """
    NLP-first, context-aware pipeline. No manual stop word lists.

    Per line:
      1. If line contains pronouns/vague refs → AI resolves them (trivial task)
      2. spaCy extracts nouns/entities from the resolved line
      3. Result is a clean, context-aware Pexels search term

    AI is only ever asked to do reference substitution — the simplest
    possible rewrite task — never imagery generation or search term invention.

    Parameters
    ----------
    section_text : str
        One section from _split_on_headings.

    Returns
    -------
    dict[str, str]
        { original_narration_line: pexels_search_term }

        e.g.::

            {
                "the empire state building is really big.":
                    "Empire State Building",
                "built on a giant pedestal.":
                    "Empire State Building pedestal",   ← context from prev line
                "they were created by the romans.":
                    "romans pedestal",                  ← 'they' resolved via AI
                "where they drunk chanoyu tea,":
                    "chanoyu tea",                      ← unknown word kept correctly
            }
    """

    result: dict[str, str] = {}
    processed_lines: list[str] = []    # resolved versions, used as rolling context

    for line in scene_lines:
        # Step 1 — resolve pronouns/references using last 3 processed lines
        if _line_needs_context_resolution(line):
            context_window = processed_lines[-3:] if len(processed_lines) >= 1 else []
            resolved = _resolve_references_with_ai(line, context_window)
            method = "ai+nlp"
        else:
            resolved = line
            method = "nlp"

        # Step 2 — extract nouns from the resolved line
        terms = _extract_nouns_spacy(resolved)

        if terms:
            search_term = " ".join(terms)
            result[line] = search_term
            print(f"  [{method}] {line[:45]!r}")
            print(f"         resolved → {resolved[:45]!r}")
            print(f"         search   → {search_term!r}")
        else:
            print(f"  [!!] no terms extracted for {line[:45]!r} — skipped")

        processed_lines.append(resolved)

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

def _fetch_stock_footage(search_term: str, num_clips: int, max_runtime_per_clip_seconds: float) -> list[dict]:
    """
    Fetch just enough clips to cover max_runtime_per_clip_seconds. Downloads only what's needed.

    Returns list of {url: trim_seconds} dicts, e.g.:
        [{"https://...mp4": 4.0}, {"https://...mp4": 3.5}]

    Strategy:
        1. Get metadata (fast — no downloading)
        2. Pick fewest clips that cover max_runtime_per_clip_seconds
        3. Download only those clips
        4. Distribute trims evenly across them
    """
    # ── 1. Collect metadata until we have enough duration ──────────────────
    collected: list[tuple[str, float]] = []   # (url, duration)
    seen: set[str] = set()

    for page in range(1, 4):  # max 3 pages
        for url, dur in _get_video_metadata(search_term, max_results=10, page=page):
            if url not in seen and dur > 0:
                collected.append((url, dur))
                seen.add(url)
            if sum(d for _, d in collected) >= max_runtime_per_clip_seconds:
                break
        if sum(d for _, d in collected) >= max_runtime_per_clip_seconds or not collected:
            break

    # ── 2. Pick only the clips we actually need ────────────────────────────
    chosen, total = [], 0.0
    for url, dur in collected:
        chosen.append((url, dur))
        total += dur
        if total >= max_runtime_per_clip_seconds:
            break

    if not chosen:
        # ── Image fallback ─────────────────────────────────────────────────
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
            results.append({url: max_runtime_per_clip_seconds / len(photos)})
        _save_history(history)
        return results

    # ── 3. Download only the chosen clips ─────────────────────────────────
    valid = []
    for url, dur in chosen: # Removed enumerate(chosen)
        print("     ... downloading clip:", url)
        local = _download_clip(url) # Removed video_id argument
        print("     ...[done]")
        if local:
            valid.append((url, dur))

    if not valid:
        return []

    # ── 4. Distribute trims ────────────────────────────────────────────────
    # Equal share per clip, capped by clip's actual duration
    # e.g. target=8, clips=[2s, 11s] → trims=[2.0, 6.0]
    # e.g. target=8, clips=[6s,  6s] → trims=[4.0, 4.0]
    per_clip = max_runtime_per_clip_seconds / len(valid)
    trims = [min(dur, per_clip) for _, dur in valid]

    # redistribute any leftover from short clips
    leftover = max_runtime_per_clip_seconds - sum(trims)
    if leftover > 0.01:
        for i, (_, dur) in enumerate(valid):
            if trims[i] < dur:
                extra = min(leftover, dur - trims[i])
                trims[i] += extra
                leftover -= extra

    print(f"  [footage] {len(valid)} clip(s), trims: {[round(t,2) for t in trims]}")
    return [{url: trim} for (url, _), trim in zip(valid, trims)]

# ---------------------------------------------------------------------------

def load_stock_footage(all_scenes: dict) -> list[dict]:
    """
    Returns an ORDERED LIST so stitch_together processes scenes in script order.

    Returns
    -------
    list[dict]  e.g.
        [
            {"script_text": "The Empire State Building is really big.",
             "footage":      [{"https://images.pexels.com/photos/36042878/...jpeg":5}, ...],
            {"script_text": "Back in 1946,",
             "footage":      [{"https://images.pexels.com/photos/11223344/...jpeg":3.4}, ...],
        ]
    """
    footage_list: list[dict] = []
    for script_text, stock_footage_search_term in all_scenes.items():
        num_images, max_runtime_per_clip_seconds = _get_num_stock_images(script_text)
        footage_to_runtime_seconds = _fetch_stock_footage(stock_footage_search_term, num_images, max_runtime_per_clip_seconds)

        #TODO - change below to have a runtime_seconds associated with each clip
        footage_list.append({
            "script_text":     script_text,
            "footage":         footage_to_runtime_seconds,
        })
    return footage_list


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

    # ── STAGE 0: Read the raw script from disk ──────────────────────────────
    # Produces:  in-memory string (no file written).
    # Manual step before this: write your script into SCRIPT_FILE,
    #   using '$' to mark where each scene ends.
    print("====================================================================")
    print("Reading in script...")
    script: str = read_in_script()
    # The full raw text of the user's script.

    


    # 1)
    # - Manually break into scenes
    # - get the search terms for pexels

    print("====================================================================")
    print("Breaking into scenes...")
    scriptTextToPexelSearch: dict[str, str] = {}
    # e.g. scriptTextToPexelSearch = 
            # { "The empire state building is really big.": "empire state building",
                # ...
                # "the samurai of Japan ruled over the kingdom.": "samurai warriors japan", }
    

    cached = _load_scene_map_cache()
    if cached:
        print(f"[cache] Loaded {len(cached)} scenes from {SCENE_MAP_CACHE_FILE}")
        scriptTextToPexelSearch = cached
    else:
        print("Splitting on headings...")
        sections = _split_on_headings(script)
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        for section in sections:
            print("... getting scenes")

            scene_lines = split_text_into_sections(section)

            section_scenes = get_script_text_to_stock_footage_search(scene_lines)
            scriptTextToPexelSearch.update(section_scenes)
        _save_scene_map_cache(scriptTextToPexelSearch)
        print(f"[cache] Saved {len(scriptTextToPexelSearch)} scenes to {SCENE_MAP_CACHE_FILE}")


    #1.5)
    # Generate the timestamps to match up the recorded audio to the script
    # TODO - add caching mechanism in the below functoin!
    run_audio_script_synchronizer (SCRIPT_AUDIO_FILE, SCRIPT_LINES_FILE, SYNCHRONIZED_SCRIPT_OUTPUT_FILE, AUDIO_START_DELAY_SECONDS)
    # ---------------------

    # 2) fetch images
    print("====================================================================")
    print("Loading stock footage...")

    # Try to load from cache first
    script_text_to_media_url_and_runtime = load_from_cache(FINAL_SCRIPT_AND_CLIPS)

    if script_text_to_media_url_and_runtime:
        print("✅ Loaded footage mapping from cache.")
    else:
        print("🔍 Cache miss. Fetching from Pexels...")
        # If cache is empty, run the original function
        script_text_to_media_url_and_runtime = load_stock_footage(scriptTextToPexelSearch)
        
        # Save the results for next time
        save_to_cache(script_text_to_media_url_and_runtime, FINAL_SCRIPT_AND_CLIPS)
        print("💾 Results cached to disk.")

    # ---- PRINT THE MAP BEFORE RETURNING ----
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
    print("\n=== SCRIPT → MEDIA MAP ===")

    for entry in script_text_to_media_url_and_runtime:
        print(f"\nSCRIPT: {entry['script_text']}")
        print("FOOTAGE:")

        for item in entry["footage"]:
            # each item is a dict with 1 key:value pair
            for url, score in item.items():
                print(f"  - {url}  (score: {score})")
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

    additional_steps_save_for_later()

    #2.5) 
    # review fetched footage
    # TODO does this account for how long the clip actually is? with the new system of max runtime seconds???
    run_media_review(
        script_text_to_media_url_and_runtime=script_text_to_media_url_and_runtime,
        stock_footage_map_path=STOCK_FOOTAGE_TO_DOWNLOADED_MEDIA_FILE,
        output_file=REVIEW_STOCK_FOOTAGE_OUTPUT_FILE,
    )


    # 3) 
    # Stitch together into initial video
    # - maybe option to add the voice track? no probs not. 
    print("====================================================================")
    stitch_together_video(FINAL_SCRIPT_AND_CLIPS, HISTORY_FILE, SCRIPT_AUDIO_FILE, OUTPUT_FILE)

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
