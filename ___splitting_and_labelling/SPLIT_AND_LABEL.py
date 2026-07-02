"""
SPLIT_AND_LABEL.py
==================
Turn a raw narration *script* into a per-line **shot list** — a map from each
phrase-line of the script to a concrete media type (effect) plus a search term
the downstream renderer can act on.

        script-spices.txt   ->   spices-script_to_search_term.json

PIPELINE (three stages, each independently cached)
--------------------------------------------------
    1. SPLIT    raw script text ->  sentence_splitter.split_text_into_sections()
                                    -> ordered list of (line_text, rule_ids)
    2. WEIGHTS  for every line, turn its rule_ids into an affinity score for
                every media type (via RULE_MEDIA_WEIGHTS), then apply the BIG
                GENERAL RULES below. The score-sheet is stored WITH the rule ids
                and their descriptions, so the number -> meaning -> media-type
                linking is fully reviewable.
    3. LABEL    sample one media type per line from those scores, then emit the
                final config row (search_term / search_type / position / sfx...)

Run it directly to self-test against `script-spices.txt`:

        uv run SPLIT_AND_LABEL.py      # dependencies come from your pyproject/uv
        python SPLIT_AND_LABEL.py

================================ BIG GENERAL RULES ============================
(These are applied on TOP of the per-rule affinities in RULE_MEDIA_WEIGHTS.py.
 They are the "sensible defaults" — written down here so they're reviewable.)

  R1. CONTINUATION vs OPENER (sentence position).
      A line that CONTINUES a sentence (no . ! ? ; : before it) is far more
      likely to be one of the "previous-image" media types
      (static_of_previous / zoom_prev_img / decorate_previous /
      manual_stock_add_to_previous) — it's elaborating on what's already on
      screen, not fetching something new. A line that OPENS a sentence gets
      those types strongly suppressed and leans fresh (stock, etc).

  R2. NAMED / CAPITALISED THINGS -> wikipedia.
      A line the splitter tagged as a named entity (rules 18/48/50) OR that
      carries a proper-noun-ish capitalised word / ALL-CAPS token is much more
      likely to be `wikipedia` (and a bit more `object_generate`).

  R3. MOST THINGS ARE STOCK.
      `stock` has a high baseline everywhere; the specialist types only win
      when a rule (or a big rule) makes a confident case.

  R4. PLACES -> map.
      A line tagged as a location/spatial split (rules 30/46) OR with a
      capitalised word after a locative preposition ("in Oregon", "in
      modern-day Indonesia") is much more likely to be `map`.

  (Add more here as you find them, and mirror the change in adjust_for_context.)
==============================================================================
"""
from __future__ import annotations

import contextlib
import io
import json
import random
import re
from pathlib import Path
from typing import Dict, List, Tuple

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- the sentence splitter (same dir / on PYTHONPATH); we only need the entry
#     point. `Chunk` is a (text, ids) NamedTuple that also unpacks as a tuple. --
from sentence_splitter import split_text_into_sections

# --- authoritative media-type vocabulary (NOT hardcoded here) ----------------
#     MediaType lives in ___visuals/CONFIG.py. Its .value strings are exactly
#     the search_type enums we emit. Prefer the normal package import; fall back
#     to loading CONFIG.py directly by path if ___visuals isn't on sys.path.
try:
    from ___visuals.CONFIG import MediaType
except Exception:  # pragma: no cover - environment-dependent
    import importlib.util as _ilu

    def _load_media_type():
        here = Path(__file__).resolve().parent
        for cand in (here / "___visuals" / "CONFIG.py",
                     here.parent / "___visuals" / "CONFIG.py"):
            if cand.exists():
                spec = _ilu.spec_from_file_location("_visuals_config", cand)
                mod = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod.MediaType
        raise ImportError(
            "Could not import MediaType. Ensure ___visuals/CONFIG.py is "
            "importable (on PYTHONPATH) or sits next to SPLIT_AND_LABEL.py."
        )

    MediaType = _load_media_type()

# --- the reviewable per-rule affinity table (see RULE_MEDIA_WEIGHTS.py) -------
from RULE_MEDIA_WEIGHTS import RULE_MEDIA_WEIGHTS


# =============================================================================
# TOGGLES  —  the knobs you are meant to flip
# =============================================================================

# When True, every file this script WRITES is prefixed with "TESTING_" — both
# the final output and the cache files.
TESTING_SCRIPT_SEARCH_TERM_GENERATION = True

# The "AI stuff" master switch. When False (default) every media type whose
# name contains "ai" or "stickman" is removed from the choosable set entirely.
# Flip to True to bring the whole vocabulary back into play.
AI_ENABLED = False

# stage-3 collapse: "sample" (seeded per line -> varied but reproducible) or
# "argmax" (always the top score -> deterministic but monotonous).
CHOICE_MODE = "sample"
GLOBAL_SEED = 1337
# Sampling sharpness. Scores are raised to this power before sampling, so the
# top pick wins MOST of the time (delivering "mostly stock") while strong
# runners-up still surface occasionally. 1.0 = flat/proportional; higher =
# peakier; very high ~ argmax.
CHOICE_TEMP = 3.0

# Swallow the splitter's noisy stage-by-stage stdout.
QUIET_SPLITTER = True

# Static config columns — constant for now (per spec: don't worry about sfx/music).
SFX_DEFAULT = "none"
SFX_TIMING_DEFAULT = "loop_start"
MUSIC_DEFAULT = "none"
MUSIC_TRIM_SECONDS_DEFAULT = 0
MUSIC_FADE_OUT_DEFAULT = 0


# =============================================================================
# MEDIA-TYPE VOCABULARY (derived from MediaType)
# =============================================================================
ALL_EFFECTS: List[str] = [m.value for m in MediaType]

# The "previous-image" family — these act on the prior line's image and inherit
# its search term. (Their AI cousins reference the previous scene too, but are
# gated by AI_ENABLED.)
PREVIOUS_EFFECTS = {
    "manual_stock_add_to_previous",
    "zoom_prev_img",
    "static_of_previous",
    "decorate_previous",
    "stickman_text_overlay",   # caption on the PREVIOUS scene's image
}

# "position" only advances across a run of joint / multi-cell effects.
JOINT_EFFECTS = {"joint_3_row", "stickman_joint_3_row"}
JOINT_CELLS = 3


def _is_ai_effect(name: str) -> bool:
    low = name.lower()
    return "ai" in low or "stickman" in low


def enabled_effects() -> List[str]:
    """Media types a line may resolve to, honouring the AI gate. Order follows
    MediaType."""
    if AI_ENABLED:
        return list(ALL_EFFECTS)
    return [e for e in ALL_EFFECTS if not _is_ai_effect(e)]


# =============================================================================
# THE PROBABILITY MODEL
# =============================================================================
# A line's score-sheet is built like this:
#
#   1. Start from BASE_PRIOR (stock-dominant — big general rule R3).
#   2. For each rule id the splitter stamped on the line, take the element-wise
#      MAX with that rule's affinities from RULE_MEDIA_WEIGHTS. (MAX, so a rule
#      can only RAISE a media type's affinity — several rules don't over-add,
#      and stock keeps its floor.)
#   3. Apply the BIG GENERAL RULES (adjust_for_context): opener/continuation,
#      named-entity -> wikipedia, place -> map.
#   4. Clamp to [SCORE_FLOOR, SCORE_CEIL].
#
# The scores are INDEPENDENT affinities (they do not sum to 1). Stage 3 is the
# only place they're collapsed into a single pick (normalise + sample).

SCORE_FLOOR = 0.005
SCORE_CEIL = 0.99

# Stock-dominant global prior (non-AI types). AI types (only present when
# AI_ENABLED) get a small flat prior and no per-rule boosts — "ignore for now".
_NONAI_BASE = {
    "stock": 0.50, "object_generate": 0.12, "read_out": 0.08, "wikipedia": 0.06,
    "map": 0.04, "joint_3_row": 0.03, "manual_stock_add_to_previous": 0.02,
    "zoom_prev_img": 0.02, "static_of_previous": 0.03, "decorate_previous": 0.02,
}
_AI_BASE_FLAT = 0.05


def base_prior() -> Dict[str, float]:
    prior = {}
    for eff in enabled_effects():
        prior[eff] = _NONAI_BASE.get(eff, _AI_BASE_FLAT)
    return prior


# ---- context detectors (make the big rules easy to identify) ----------------
# A capitalised word that is NOT sentence-initial -> proper-noun-ish.
_MIDCAP_RE = re.compile(r"(?<!^)(?<![.!?:;]\s)\b[A-Z][a-z]{2,}")
# An ALL-CAPS token (SHOUTED / acronym).
_ALLCAPS_RE = re.compile(r"\b[A-Z]{3,}\b")
# A capitalised word right after a locative preposition -> a place.
_LOCATIVE_RE = re.compile(
    r"\b(?:in|on|at|to|from|into|across|near|through|over|around|within|by|"
    r"toward|towards|onto)\s+(?:the\s+|modern[-\s]day\s+)?[A-Z][a-z]{2,}"
)
_DIGIT_RE = re.compile(r"[0-9]")

# rule ids that indicate a NAMED ENTITY, and a PLACE, respectively.
_ENTITY_RULE_IDS = {18, 48, 50}
_PLACE_RULE_IDS = {30, 46}


def _has_entity(text: str, ids: List[int]) -> bool:
    if any(i in _ENTITY_RULE_IDS for i in ids):
        return True
    return bool(_MIDCAP_RE.search(text) or _ALLCAPS_RE.search(text))


def _has_place(text: str, ids: List[int]) -> bool:
    if any(i in _PLACE_RULE_IDS for i in ids):
        return True
    return bool(_LOCATIVE_RE.search(text))


# ---- continuation nudges (R1) -----------------------------------------------
# Floors the previous-family gets on a continuation line, so within-sentence
# lines genuinely favour "act on the previous image" media types. Slightly
# different floors bias a featureless continuation toward static_of_previous.
_CONT_PREV_FLOOR = {
    "static_of_previous": 0.50,
    "zoom_prev_img": 0.42,
    "decorate_previous": 0.42,
    "manual_stock_add_to_previous": 0.42,
}
_CONT_STOCK_MULT = 0.5       # fresh stock less likely mid-sentence
_CONT_READOUT_MULT = 0.8
_OPENER_PREV_MULT = 0.10     # previous-family strongly suppressed at sentence start
_OPENER_STOCK_MULT = 1.15
_ENTITY_WIKI_MULT = 2.6      # R2
_ENTITY_OG_MULT = 1.25
_PLACE_MAP_MULT = 3.0        # R4


def adjust_for_context(dist: Dict[str, float], text: str, ids: List[int],
                       is_opener: bool) -> Dict[str, float]:
    """Apply the BIG GENERAL RULES to a combined score-sheet, in place-ish."""
    d = dict(dist)

    # R1 — opener vs continuation
    if is_opener:
        for eff in PREVIOUS_EFFECTS:
            if eff in d:
                d[eff] *= _OPENER_PREV_MULT
        if "stock" in d:
            d["stock"] *= _OPENER_STOCK_MULT
    else:
        for eff, floor in _CONT_PREV_FLOOR.items():
            if eff in d:
                d[eff] = max(d[eff], floor)
        if "stock" in d:
            d["stock"] *= _CONT_STOCK_MULT
        if "read_out" in d:
            d["read_out"] *= _CONT_READOUT_MULT

    # R2 — named / capitalised -> wikipedia
    if _has_entity(text, ids):
        if "wikipedia" in d:
            d["wikipedia"] *= _ENTITY_WIKI_MULT
        if "object_generate" in d:
            d["object_generate"] *= _ENTITY_OG_MULT

    # R4 — place -> map
    if _has_place(text, ids):
        if "map" in d:
            d["map"] *= _PLACE_MAP_MULT

    return d


def score_line(text: str, ids: List[int], is_opener: bool) -> Dict[str, float]:
    """Build the (independent) score-sheet for one line, over enabled types."""
    allowed = enabled_effects()
    dist = base_prior()

    # combine per-rule affinities by MAX (a rule can only raise a type)
    for rid in ids:
        rp = RULE_MEDIA_WEIGHTS.get(rid, {}).get("media_type_probabilities", {})
        for eff, val in rp.items():
            if eff in dist:
                dist[eff] = max(dist[eff], float(val))

    dist = adjust_for_context(dist, text, ids, is_opener)

    return {
        eff: round(min(max(dist.get(eff, SCORE_FLOOR), SCORE_FLOOR), SCORE_CEIL), 4)
        for eff in allowed
    }


def _why(ids: List[int]) -> List[str]:
    """Human-readable 'why this line was cut' — the number->meaning linking."""
    out = []
    for rid in ids:
        desc = RULE_MEDIA_WEIGHTS.get(rid, {}).get("description")
        out.append(f"{rid}: {desc}" if desc else f"{rid}: <unknown rule>")
    return out


# =============================================================================
# SMALL UTILITIES
# =============================================================================

def prefix_from_script_name(script_name: str) -> str:
    """script-spices.txt -> 'spices'. Robust to any extension and a leading
    'script-'/'script_'. script-spices.wav -> 'spices' too."""
    stem = Path(script_name).name
    stem = re.sub(r"\.[^.]+$", "", stem)
    stem = re.sub(r"^script[-_]", "", stem, flags=re.IGNORECASE)
    return stem or "script"


def _testing() -> bool:
    return bool(TESTING_SCRIPT_SEARCH_TERM_GENERATION)


def _tag() -> str:
    return "TESTING_" if _testing() else ""


def cache_dir_for(prefix: str) -> Path:
    # 'split-and-lable' spelling kept verbatim from the task's path spec.
    return Path(f"{prefix}-CACHE") / "split-and-lable"


def split_cache_path(prefix: str) -> Path:
    return cache_dir_for(prefix) / f"{_tag()}SPLIT-{prefix}.json"


def weights_cache_path(prefix: str) -> Path:
    return cache_dir_for(prefix) / f"{_tag()}WEIGHTS-{prefix}.json"


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
# STAGE 1 — SPLIT
# =============================================================================

def _run_splitter(text: str) -> List[Tuple[str, List[int]]]:
    if QUIET_SPLITTER:
        with contextlib.redirect_stdout(io.StringIO()):
            chunks = split_text_into_sections(text)
    else:
        chunks = split_text_into_sections(text)
    return [(str(t).strip(), [int(i) for i in ids]) for (t, ids) in chunks
            if str(t).strip()]


def stage_split(prefix: str, script_path: Path) -> List[Tuple[str, List[int]]]:
    cache = split_cache_path(prefix)
    if cache.exists():
        print(f"[split]   cache hit  -> {cache}")
        return [(t, [int(i) for i in ids]) for t, ids in _load_json(cache)]
    if not script_path.exists():
        raise FileNotFoundError(
            f"Script not found: {script_path}. Provide a UTF-8 text transcript."
        )
    text = script_path.read_text(encoding="utf-8")
    print(f"[split]   running splitter on {script_path} ({len(text)} chars)...")
    pairs = _run_splitter(text)
    _save_json(cache, [[t, ids] for t, ids in pairs])
    print(f"[split]   {len(pairs)} lines -> cached {cache}")
    return pairs


# =============================================================================
# STAGE 2 — WEIGHTS  (reviewable score-sheets: ids + why + probabilities)
# =============================================================================

def _is_opener(prev_text) -> bool:
    """True if a line starts a new sentence: first line, or the previous line
    ended on sentence-final punctuation."""
    if prev_text is None:
        return True
    return bool(re.search(r"[.!?;:]\s*$", prev_text))


def stage_weights(prefix: str,
                  split_pairs: List[Tuple[str, List[int]]]) -> Dict[str, dict]:
    cache = weights_cache_path(prefix)
    if cache.exists():
        print(f"[weights] cache hit  -> {cache}")
        return _load_json(cache)

    weights: Dict[str, dict] = {}
    prev_text = None
    for text, ids in split_pairs:
        opener = _is_opener(prev_text)
        weights[text] = {
            "ids": ids,
            "opener": opener,
            "why": _why(ids),
            "media_type_probabilities": score_line(text, ids, opener),
        }
        prev_text = text
    _save_json(cache, weights)
    print(f"[weights] {len(weights)} score-sheets -> cached {cache}")
    return weights


# =============================================================================
# STAGE 3 — LABEL
# =============================================================================

def choose_effect(text: str, scoresheet: Dict[str, float]) -> str:
    effs = list(scoresheet.keys())
    if not effs:
        return "read_out"
    if CHOICE_MODE == "argmax":
        return max(effs, key=lambda e: scoresheet[e])
    weights = [max(scoresheet[e], 1e-6) ** CHOICE_TEMP for e in effs]
    total = sum(weights)
    rng = random.Random(f"{GLOBAL_SEED}:{text}")
    r = rng.random() * total
    acc = 0.0
    for eff, w in zip(effs, weights):
        acc += w
        if r <= acc:
            return eff
    return effs[-1]


# --- baseline, dependency-free search-term synthesis -------------------------
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "so", "of", "to", "in",
    "on", "at", "by", "for", "with", "from", "into", "over", "under", "about",
    "as", "is", "are", "was", "were", "be", "been", "being", "it", "its", "this",
    "that", "these", "those", "he", "she", "they", "them", "we", "you", "your",
    "his", "her", "their", "our", "my", "me", "i", "do", "does", "did", "will",
    "would", "can", "could", "should", "may", "might", "must", "have", "has",
    "had", "not", "no", "yes", "up", "down", "out", "off", "just", "now", "very",
    "really", "more", "most", "than", "which", "who", "what", "when", "where",
    "why", "how", "there", "here", "all", "both", "each", "every", "some", "any",
}
_WORD_RE = re.compile(r"[A-Za-z0-9$£€¥₹₽¢%'-]+")


def _keywords(text: str, max_words: int = 6) -> str:
    toks = _WORD_RE.findall(text)
    kept = []
    for t in toks:
        low = t.lower().strip("'-")
        if not low:
            continue
        if low in _STOPWORDS and not _DIGIT_RE.search(low):
            continue
        kept.append(low)
    if not kept:
        kept = [t.lower() for t in toks[:max_words]]
    return " ".join(kept[:max_words])


def _search_term(text: str, effect: str, prev_term: str) -> str:
    if effect in PREVIOUS_EFFECTS:
        base = prev_term or _keywords(text)
        if effect == "manual_stock_add_to_previous":
            return f"add {_keywords(text)} to previous"
        if effect == "stickman_text_overlay":
            return _keywords(text).upper()      # caption text
        return base
    return _keywords(text) or text.lower()


def build_final_map(split_pairs: List[Tuple[str, List[int]]],
                    weights: Dict[str, dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    prev_term = ""
    joint_pos = 0

    for text, ids in split_pairs:
        entry = weights.get(text) or {}
        scoresheet = entry.get("media_type_probabilities") \
            or score_line(text, ids, True)

        # can't act on a "previous" image before any fresh visual exists
        if not prev_term:
            filtered = {e: s for e, s in scoresheet.items()
                        if e not in PREVIOUS_EFFECTS}
            scoresheet = filtered or scoresheet

        effect = choose_effect(text, scoresheet)

        if effect in JOINT_EFFECTS:
            joint_pos = joint_pos % JOINT_CELLS + 1
        else:
            joint_pos = 0
        position = str(joint_pos if joint_pos else 1)

        term = _search_term(text, effect, prev_term)
        out[text] = {
            "search_term": term,
            "search_type": effect,
            "position": position,
            "sfx": SFX_DEFAULT,
            "sfx_timing": SFX_TIMING_DEFAULT,
            "music": MUSIC_DEFAULT,
            "music_trim_seconds": MUSIC_TRIM_SECONDS_DEFAULT,
            "music_fade_out": MUSIC_FADE_OUT_DEFAULT,
        }
        # only fresh visuals become the standing "previous" subject
        if effect not in PREVIOUS_EFFECTS and effect != "read_out":
            prev_term = term
    return out


# =============================================================================
# TOP-LEVEL ENTRY POINT
# =============================================================================

def generate_script_to_search_term(script_name: str) -> Path:
    """Run split -> weights -> label for one script. Every stage is cached; if a
    cache exists the stage is skipped. Returns the final output path (itself
    treated as the stage-3 cache -> rerun-idempotent)."""
    prefix = prefix_from_script_name(script_name)
    out_path = output_path(prefix)

    mode = "TESTING" if _testing() else "LIVE"
    print(f"=== generate_script_to_search_term "
          f"[prefix={prefix!r}, mode={mode}, AI_ENABLED={AI_ENABLED}] ===")

    if out_path.exists():
        print(f"[label]   cache hit  -> {out_path} (skipping all stages)")
        return out_path

    script_path = _resolve_script_path(script_name)
    split_pairs = stage_split(prefix, script_path)
    weights = stage_weights(prefix, split_pairs)
    final = build_final_map(split_pairs, weights)
    _save_json(out_path, final)
    print(f"[label]   {len(final)} rows -> wrote {out_path}")
    return out_path


# =============================================================================
# SELF-TEST  —  running the file directly is ALWAYS a TESTING run
# =============================================================================
_SAMPLE_SPICES_SCRIPT = (
    "If you open your kitchen cupboard right now, you probably have a jar of "
    "nutmeg. It costs about two dollars. But in the 1600s, this little wrinkled "
    "seed was the single most contested resource on the planet. It was worth "
    "more than its weight in gold. Nutmeg only grew in one place on Earth: the "
    "Banda Islands. A tiny, incredibly remote volcanic archipelago in "
    "modern-day Indonesia."
)


def _selftest() -> None:
    global TESTING_SCRIPT_SEARCH_TERM_GENERATION
    TESTING_SCRIPT_SEARCH_TERM_GENERATION = True   # HARD RULE: local == testing

    script_name = "script-spices.txt"
    if not Path(script_name).exists():
        Path(script_name).write_text(_SAMPLE_SPICES_SCRIPT, encoding="utf-8")
        print(f"[selftest] wrote bundled sample -> {script_name}")

    out = generate_script_to_search_term(script_name)

    print("\n----- OUTPUT PREVIEW -----")
    data = _load_json(out)
    for i, (line, cfg) in enumerate(data.items()):
        if i >= 14:
            print(f"... ({len(data) - 14} more rows)")
            break
        print(f"  {line!r}")
        print(f"      -> [{cfg['search_type']}] "
              f"pos={cfg['position']}  term={cfg['search_term']!r}")


if __name__ == "__main__":
    _selftest()
