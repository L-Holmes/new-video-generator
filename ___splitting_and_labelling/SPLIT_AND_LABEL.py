"""
SPLIT_AND_LABEL.py  —  step 1 of 2: split a narration script into
visual-beat lines and write the empty shot list.

    uv run SPLIT_AND_LABEL.py            (runs the bundled sample scripts)

What it does (and ALL it does — there is no automatic media-type choosing):
  1. SPLIT   the script with sentence_splitter (spaCy) into phrase-lines,
             each tagged with the rule ids that cut it. Cached per script
             (the spaCy parse is the only expensive part).
  2. EMIT    [TESTING_]<prefix>-script_to_search_term.json — one row per
             line with media_type / search_term EMPTY. You fill them in
             step 2 with `uv run MANUAL_TAGGING.py` (point and click),
             optionally helped by the AI prompts in prompts/.

The output format is documented field-by-field in FORMAT.md.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

from sentence_splitter import split_text_into_sections_with_meta

# When True, every file this script WRITES is prefixed with "TESTING_".
TESTING_SCRIPT_SEARCH_TERM_GENERATION = True

# The splitter prints its own progress; keep it quiet inside the pipeline.
QUIET_SPLITTER = True

# legacy audio defaults (the renderer reads these as-is)
SFX_DEFAULT = "none"
SFX_TIMING_DEFAULT = "loop_start"
MUSIC_DEFAULT = "none"
MUSIC_TRIM_SECONDS_DEFAULT = 0
MUSIC_FADE_OUT_DEFAULT = 0


# =============================================================================
# paths + small helpers
# =============================================================================

def prefix_from_script_name(script_name: str) -> str:
    stem = Path(script_name).name
    stem = re.sub(r"\.[^.]+$", "", stem)
    stem = re.sub(r"^script[-_]", "", stem, flags=re.IGNORECASE)
    return stem or "script"


def _tag() -> str:
    return "TESTING_" if TESTING_SCRIPT_SEARCH_TERM_GENERATION else ""


def cache_dir_for(prefix: str) -> Path:
    # 'split-and-lable' spelling kept verbatim from the original path spec.
    return Path(f"{prefix}-CACHE") / "split-and-lable"


def split_cache_path(prefix: str) -> Path:
    # The name carries a schema version: if the split output ever changes,
    # bump the number so stale caches can never be silently misread.
    return cache_dir_for(prefix) / f"{_tag()}SPLITMETA4-{prefix}.json"


def output_path(prefix: str) -> Path:
    return Path(f"{_tag()}{prefix}-script_to_search_term.json")


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _resolve_script_path(script_name: str) -> Path:
    p = Path(script_name)
    if p.exists():
        return p
    alt = p.with_suffix(".txt")
    return alt if alt.exists() else p


# =============================================================================
# stage 1 — split (cached)
# =============================================================================

def _run_splitter(text: str) -> List[Tuple[str, List[int], dict]]:
    if QUIET_SPLITTER:
        with contextlib.redirect_stdout(io.StringIO()):
            chunks = split_text_into_sections_with_meta(text)
    else:
        chunks = split_text_into_sections_with_meta(text)
    return [(c.text.strip(), [int(i) for i in c.ids], dict(c.meta))
            for c in chunks if c.text.strip()]


def stage_split(prefix: str, script_path: Path
                ) -> List[Tuple[str, List[int], dict]]:
    cache = split_cache_path(prefix)
    if cache.exists():
        print(f"[split]   cache hit  -> {cache}")
        return [(t, [int(i) for i in ids], meta)
                for t, ids, meta in _load_json(cache)]
    if not script_path.exists():
        raise FileNotFoundError(
            f"Script not found: {script_path}. Provide a UTF-8 text transcript.")
    text = script_path.read_text(encoding="utf-8")
    print(f"[split]   running splitter on {script_path} ({len(text)} chars)...")
    triples = _run_splitter(text)
    _save_json(cache, [[t, ids, meta] for t, ids, meta in triples])
    print(f"[split]   {len(triples)} lines -> cached {cache}")
    return triples


# =============================================================================
# stage 2 — emit the empty shot list (see FORMAT.md)
# =============================================================================

def build_rows(triples: List[Tuple[str, List[int], dict]]) -> dict:
    out = {}
    for text, ids, _meta in triples:
        out[text] = {
            "search_term": "",       # you write this (MANUAL_TAGGING / AI)
            "search_type": "",       # derived from media_type + modifiers
            "media_type": "",        # you pick this (MANUAL_TAGGING)
            "modifiers": [],         # decorate / caption / group
            "group_id": None,        # lines sharing an id are one group
            "position": "1",
            "sfx": SFX_DEFAULT,
            "sfx_timing": SFX_TIMING_DEFAULT,
            "music": MUSIC_DEFAULT,
            "music_trim_seconds": MUSIC_TRIM_SECONDS_DEFAULT,
            "music_fade_out": MUSIC_FADE_OUT_DEFAULT,
            "rule_ids": ids,         # how the splitter cut this line
        }
    return out


def generate_script_to_search_term(script_name: str) -> Path:
    prefix = prefix_from_script_name(script_name)
    out_path = output_path(prefix)
    print(f"=== generate_script_to_search_term [prefix={prefix!r}] ===")
    if out_path.exists():
        print(f"[emit]    already exists -> {out_path} (delete it to re-emit)")
        return out_path
    triples = stage_split(prefix, _resolve_script_path(script_name))
    _save_json(out_path, build_rows(triples))
    print(f"[emit]    {len(triples)} rows -> {out_path}")
    print(f"[emit]    now run: uv run MANUAL_TAGGING.py")
    return out_path


# =============================================================================
# self-test — three edge-case sample scripts
# =============================================================================
_SAMPLE_SCRIPTS = {
    "script-spices.txt": (
        "If you open your kitchen cupboard right now, you probably have a "
        "jar of nutmeg. It costs about two dollars. But in the 1600s, this "
        "little wrinkled seed was the single most contested resource on the "
        "planet. It was worth more than its weight in gold. Nutmeg only grew "
        "in one place on Earth: the Banda Islands. A tiny, incredibly remote "
        "volcanic archipelago in modern-day Indonesia."
    ),
    "script-whales.txt": (
        "Here's the thing. In the middle of the Sahara sits a valley called "
        "Wadi Al-Hitan. Scientists digging there found ribs, vertebrae, "
        "teeth, and entire skulls. Whale skulls. The ground looked as if an "
        "ocean had simply dried up around them. Every fossil was intact "
        "except one. Locals swear the wind sounds like whale song at night. "
        "And then boom, a sandstorm buried the site for a decade. Recovering "
        "it cost about $2 million."
    ),
    "script-rome.txt": (
        "Rome was not built in a day.\n"
        "   But it burned in six.\n\n"
        "In 64 AD, a fire started in the merchant stalls near the Circus "
        "Maximus. Driven by wind, this hungry blaze devoured temples, "
        "villas, and entire districts. The city was rebuilt by a very "
        "unpopular emperor. Which brings us to Nero. He blamed a small "
        "religious sect, and the rest is history."
    ),
}


def _selftest() -> None:
    global TESTING_SCRIPT_SEARCH_TERM_GENERATION
    TESTING_SCRIPT_SEARCH_TERM_GENERATION = True
    for script_name, body in _SAMPLE_SCRIPTS.items():
        if not Path(script_name).exists():
            Path(script_name).write_text(body, encoding="utf-8")
            print(f"[selftest] wrote bundled sample -> {script_name}")
        out = generate_script_to_search_term(script_name)
        data = _load_json(out)
        print(f"\n----- {script_name}: {len(data)} lines -----")
        for i, (line, cfg) in enumerate(data.items()):
            if i >= 10:
                print(f"... ({len(data) - 10} more)")
                break
            print(f"  {line!r}   rules={cfg['rule_ids']}")
        print()


if __name__ == "__main__":
    _selftest()
