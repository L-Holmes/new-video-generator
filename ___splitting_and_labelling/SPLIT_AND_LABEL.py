"""
SPLIT_AND_LABEL.py
==================
Turn a raw narration *script* into a per-line **shot list** — a map from each
phrase-line of the script to a shot (media type + search term) the downstream
renderer can act on.

        script-spices.txt   ->   spices-script_to_search_term.json

======================= THE DECISION FLOW (read this first) ===================

Every line from the splitter walks DOWN this ladder and stops at the first
tier that decides.  Tiers 0-1 are DETERMINISTIC; only tier 3 rolls dice.

    line (text + rule ids + meta from the splitter)
      |
      v
    TIER 0  HARD GATES         what is even possible right now?
      |                        (no image on screen yet -> the whole
      |                         edit_previous family is off the menu)
      v
    TIER 1  CONFIDENT LOCKS    first matching lock wins, in this order:
      |                          1. list run            -> grid
      |                          2. money amount        -> object_generate
      |                          3. place named         -> map (obscure)
      |                                                    stock (famous)
      |                          4. named person/thing  -> wikipedia (obscure)
      |                                                    stock (famous)
      |                          5. quoted speech       -> decorate_previous
      |                          6. SFX beat (rule 60)  -> decorate_previous
      |                          7. nothing picture-able-> hold_previous
      |                        no lock matched? fall through:
      v
    TIER 2  CONTEXT NUDGES     multiply/floor the score-sheet:
      |                          opener vs continuation, maybe-a-name,
      |                          maybe-a-place  (nothing is decided here)
      v
    TIER 3  WEIGHTED SAMPLE    stock-dominant prior + per-rule affinities
      |                        from RULE_MEDIA_WEIGHTS, seeded sample
      v
    EMIT    ShotSpec + search term + legacy renderer row
            (every row records WHICH tier decided and WHY)

WHERE TO CHANGE WHAT  (the future-dev cheat sheet)
--------------------------------------------------
    add/rename a media type........ SPLIT_AND_LABEL_CONFIG.SHOT_TEMPLATES
                                    (+ TEMPLATE_TO_LEGACY, + weights column)
    add a deterministic rule....... write a lock_*() below, insert into
                                    TIER1_LOCKS at the right priority
    add a soft bias................ write a nudge_*() below, append to
                                    TIER2_NUDGES
    change how random the end is... CHOICE_MODE / CHOICE_TEMP
    change per-splitter-rule taste. RULE_MEDIA_WEIGHTS.py (regenerate via
                                    gen_rule_media_weights.py)
    teach it who's famous.......... ENTITY_FAME_CACHE.json (see fame section)
    change search-term wording..... synthesize_search_term()

PIPELINE (three stages, each independently cached)
--------------------------------------------------
    1. SPLIT    sentence_splitter.split_text_into_sections_with_meta()
                -> [(line_text, rule_ids, meta)]   meta = spaCy-grounded facts
                   (entities, opener flag, keywords, list membership...) so
                   NOTHING below re-detects language features with regexes.
    2. DECIDE   walk each line down the tier ladder -> Decision
                (template, tier, why-trail, score-sheet if sampled)
    3. EMIT     search term + position + legacy `search_type` string for the
                renderer, PLUS the new fields (template / shot / tier / why)

Run it directly to self-test against `script-spices.txt`:

        python SPLIT_AND_LABEL.py
"""
from __future__ import annotations

import contextlib
import io
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- the splitter: the with-meta API is the contract this file relies on ----
from sentence_splitter import split_text_into_sections_with_meta

# --- OUR media vocabulary (separate from the renderer's CONFIG.py) -----------
from SPLIT_AND_LABEL_CONFIG import (
    SHOT_TEMPLATES, PREVIOUS_FAMILY, GRID_TEMPLATES, FRESH_MATERIAL_TEMPLATES,
    GRID_CELLS, to_legacy,
)

# --- the reviewable per-rule affinity table (tier 3 only) --------------------
from RULE_MEDIA_WEIGHTS import RULE_MEDIA_WEIGHTS


# =============================================================================
# TOGGLES  —  the knobs you are meant to flip
# =============================================================================

# When True, every file this script WRITES is prefixed with "TESTING_".
TESTING_SCRIPT_SEARCH_TERM_GENERATION = True

# Tier-3 collapse: "sample" (seeded per line -> varied but reproducible) or
# "argmax" (always the top score -> deterministic but monotonous).
CHOICE_MODE = "sample"
GLOBAL_SEED = 1337
CHOICE_TEMP = 3.0     # sampling sharpness: higher = peakier (~argmax)

# Swallow the splitter's noisy stage-by-stage stdout.
QUIET_SPLITTER = True

# Static config columns (per spec: don't worry about sfx/music yet).
SFX_DEFAULT = "none"
SFX_TIMING_DEFAULT = "loop_start"
MUSIC_DEFAULT = "none"
MUSIC_TRIM_SECONDS_DEFAULT = 0
MUSIC_FADE_OUT_DEFAULT = 0


# =============================================================================
# ENTITY FAME  —  "Himalayas -> stock footage, Alaric the Goth -> wikipedia"
# =============================================================================
# Three-valued on purpose: locks only fire on CONFIDENT answers.
#
#     "famous"   -> everyone knows what it looks like -> stock, term = name
#     "obscure"  -> nobody does -> wikipedia (things) / map (places)
#     "unknown"  -> we can't tell -> NO lock; tier 2 nudges instead
#
# Source of truth is ENTITY_FAME_CACHE.json next to this file:
#     { "<lowercased name>": "famous" | "obscure" }
# Populate it however you like — the intended way is a one-off script that
# hits the Wikipedia API (sitelink count / monthly pageviews) per entity and
# thresholds the result.  Until the cache exists, the small built-in list
# below catches the obvious famous cases and everything else is "unknown"
# (which safely degrades a lock into a nudge — never a wrong hard choice).

ENTITY_FAME_CACHE_PATH = Path(__file__).resolve().parent / "ENTITY_FAME_CACHE.json"

_BUILTIN_FAMOUS = {
    "sahara", "himalayas", "everest", "amazon", "nile", "alps", "antarctica",
    "grand canyon", "great barrier reef", "niagara falls", "mount fuji",
    "new york", "new york city", "manhattan", "london", "paris", "tokyo",
    "rome", "venice", "dubai", "hong kong", "los angeles", "san francisco",
    "egypt", "china", "india", "japan", "russia", "brazil", "australia",
    "america", "europe", "africa", "canada", "mexico", "france", "germany",
    "italy", "spain", "greece", "earth", "moon", "sun", "mars",
    "einstein", "napoleon", "cleopatra", "shakespeare", "leonardo da vinci",
    "titanic", "eiffel tower", "statue of liberty", "great wall of china",
    "pyramids", "colosseum",
}

_fame_cache: Optional[Dict[str, str]] = None


def entity_fame(name: str) -> str:
    """Return 'famous' / 'obscure' / 'unknown' for an entity name."""
    global _fame_cache
    if _fame_cache is None:
        if ENTITY_FAME_CACHE_PATH.exists():
            _fame_cache = {k.lower(): v for k, v in
                           json.loads(ENTITY_FAME_CACHE_PATH.read_text(
                               encoding="utf-8")).items()}
        else:
            _fame_cache = {}
    low = name.lower().strip()
    if low in _fame_cache:
        return _fame_cache[low]
    if low in _BUILTIN_FAMOUS:
        return "famous"
    return "unknown"


# =============================================================================
# THE DECISION OBJECT  —  what one line resolves to
# =============================================================================

@dataclass
class Decision:
    template: str                       # key into SHOT_TEMPLATES
    tier: str                           # "tier1:lock_place" / "tier3:sampled"
    why: List[str]                      # human-readable audit trail
    term_override: Optional[str] = None  # a lock may dictate the search term
    scoresheet: Optional[Dict[str, float]] = None  # tier-3 decisions only


@dataclass
class ScriptState:
    """What the engine knows about the timeline so far (mutated by EMIT)."""
    has_visual: bool = False    # has ANY fresh image reached the screen yet?
    prev_term: str = ""         # standing search term of the last fresh shot
    grid_pos: int = 0           # 1-based position inside a running grid


# =============================================================================
# TIER 0 — HARD GATES  (what is even possible right now)
# =============================================================================

def allowed_templates(state: ScriptState) -> set:
    """The menu tier 1 and tier 3 are allowed to choose from."""
    allowed = set(SHOT_TEMPLATES)
    if not state.has_visual:
        # Can't act on a previous image before any fresh visual exists.
        allowed -= PREVIOUS_FAMILY
    return allowed


# =============================================================================
# TIER 1 — CONFIDENT LOCKS
# =============================================================================
# Each lock inspects one line and either returns (template, term_override,
# reason) or None.  Locks run IN THE ORDER of TIER1_LOCKS; the first match
# wins.  A lock whose template isn't in `allowed` is skipped (falls through).
#
# Design rule: a lock must only fire on evidence strong enough that you'd be
# happy for it to be 100% deterministic.  If you're tuning probabilities,
# you're writing a tier-2 nudge, not a lock.

_PLACE_LABELS = {"GPE", "LOC"}
_NAMED_THING_LABELS = {"PERSON", "ORG", "FAC", "EVENT", "WORK_OF_ART", "NORP"}
_SFX_RULE_ID = 60


def _first_ent(meta: dict, labels: set) -> Optional[str]:
    for e in meta.get("ents", []):
        if e["label"] in labels:
            return e["text"]
    return None


def lock_list_grid(text, meta, ids, allowed, state):
    """A detected list run stays coherent: every item -> the same grid."""
    if meta.get("list"):
        li = meta["list"]
        return ("grid_different", None,
                f"list run (group {li['group']}, item {li['index'] + 1}"
                f"/{li['size']}) -> one grid cell per item")
    return None


def lock_money(text, meta, ids, allowed, state):
    """A money amount is a graphic beat -> the object editor."""
    if meta.get("has_money"):
        return ("object_generate", None,
                "money amount on the line -> object_generate")
    return None


def lock_place(text, meta, ids, allowed, state):
    """A named place: famous -> stock footage of it; obscure -> map."""
    place = _first_ent(meta, _PLACE_LABELS)
    if not place:
        return None
    fame = entity_fame(place)
    if fame == "famous":
        return ("stock", place.lower(),
                f"famous place '{place}' -> stock footage of it")
    if fame == "obscure":
        return ("map", place, f"obscure place '{place}' -> highlighted map")
    return None    # unknown fame -> tier 2 nudges handle it


def lock_named_thing(text, meta, ids, allowed, state):
    """A named person/org/artwork: famous -> stock; obscure -> wikipedia."""
    name = _first_ent(meta, _NAMED_THING_LABELS)
    if not name:
        return None
    fame = entity_fame(name)
    if fame == "famous":
        return ("stock", name.lower(),
                f"famous name '{name}' -> stock footage of them/it")
    if fame == "obscure":
        return ("wikipedia", name,
                f"obscure name '{name}' -> wikipedia image")
    return None


def lock_quote(text, meta, ids, allowed, state):
    """Quoted speech goes on screen as text over the standing image."""
    if meta.get("in_quote"):
        return ("decorate_previous", None,
                "quoted speech -> text drawn over the previous image")
    return None


def lock_sfx(text, meta, ids, allowed, state):
    """A splitter SFX beat (rule 60) is a punch, not a new fetch."""
    if _SFX_RULE_ID in ids:
        return ("decorate_previous", None,
                "SFX beat (rule 60) -> big word over the previous image")
    return None


def lock_unvisualisable(text, meta, ids, allowed, state):
    """Nothing picture-able on the line -> hold what's on screen."""
    if meta.get("has_visualisable") is False:
        return ("hold_previous", None,
                "no picture-able content -> hold the previous image")
    return None


# ORDER MATTERS.  List coherence beats everything; the weakest evidence
# (unvisualisable) goes last so stronger signals get first refusal.
TIER1_LOCKS = [
    lock_list_grid,
    lock_money,
    lock_place,
    lock_named_thing,
    lock_quote,
    lock_sfx,
    lock_unvisualisable,
]


# =============================================================================
# TIER 2 — CONTEXT NUDGES  (soft biases on the tier-3 score-sheet)
# =============================================================================
# Each nudge mutates the score-sheet dict and returns a reason string (or
# None if it didn't apply).  Nothing here decides anything.

_CONT_PREV_FLOOR = {          # continuation lines favour the previous-family
    "hold_previous": 0.50,
    "zoom_previous": 0.42,
    "decorate_previous": 0.42,
    "composite_onto_previous": 0.42,
}
_CONT_STOCK_MULT = 0.5        # fresh stock less likely mid-sentence
_OPENER_PREV_MULT = 0.10      # previous-family suppressed at sentence start
_OPENER_STOCK_MULT = 1.15
_MAYBE_NAME_WIKI_MULT = 2.6   # unknown-fame name -> lean wikipedia
_MAYBE_NAME_OG_MULT = 1.25
_MAYBE_PLACE_MAP_MULT = 3.0   # unknown-fame place -> lean map


def nudge_opener_continuation(sheet, text, meta, ids):
    if meta.get("opener"):
        for eff in PREVIOUS_FAMILY:
            if eff in sheet:
                sheet[eff] *= _OPENER_PREV_MULT
        if "stock" in sheet:
            sheet["stock"] *= _OPENER_STOCK_MULT
        return "opener -> previous-family suppressed, fresh stock boosted"
    for eff, floor in _CONT_PREV_FLOOR.items():
        if eff in sheet:
            sheet[eff] = max(sheet[eff], floor)
    if "stock" in sheet:
        sheet["stock"] *= _CONT_STOCK_MULT
    return "continuation -> previous-family floored, fresh stock damped"


def nudge_maybe_name(sheet, text, meta, ids):
    """A named thing whose fame we DON'T know: lean wikipedia, don't lock."""
    if _first_ent(meta, _NAMED_THING_LABELS):
        if "wikipedia" in sheet:
            sheet["wikipedia"] *= _MAYBE_NAME_WIKI_MULT
        if "object_generate" in sheet:
            sheet["object_generate"] *= _MAYBE_NAME_OG_MULT
        return "unknown-fame name -> wikipedia leaned up"
    return None


def nudge_maybe_place(sheet, text, meta, ids):
    """A place whose fame we DON'T know: lean map, don't lock."""
    if _first_ent(meta, _PLACE_LABELS):
        if "map" in sheet:
            sheet["map"] *= _MAYBE_PLACE_MAP_MULT
        return "unknown-fame place -> map leaned up"
    return None


TIER2_NUDGES = [
    nudge_opener_continuation,
    nudge_maybe_name,
    nudge_maybe_place,
]


# =============================================================================
# TIER 3 — WEIGHTED SAMPLE
# =============================================================================

SCORE_FLOOR = 0.005
SCORE_CEIL = 0.99

# Stock-dominant global prior over the TEMPLATE names (big rule: MOST THINGS
# ARE STOCK).  Per-rule affinities can only RAISE a template above this.
BASE_PRIOR = {
    "stock": 0.50, "object_generate": 0.12, "wikipedia": 0.06, "map": 0.04,
    "grid_different": 0.03, "grid_same": 0.02,
    "composite_onto_previous": 0.02, "zoom_previous": 0.02,
    "hold_previous": 0.05, "decorate_previous": 0.03,
}


def build_scoresheet(text, meta, ids, allowed) -> Tuple[Dict[str, float], List[str]]:
    """Prior -> MAX with each splitter rule's affinities -> tier-2 nudges."""
    sheet = {t: BASE_PRIOR.get(t, 0.02) for t in SHOT_TEMPLATES if t in allowed}
    for rid in ids:
        row = RULE_MEDIA_WEIGHTS.get(rid, {}).get("media_type_probabilities", {})
        for eff, val in row.items():
            if eff in sheet:
                sheet[eff] = max(sheet[eff], float(val))
    reasons = []
    for nudge in TIER2_NUDGES:
        r = nudge(sheet, text, meta, ids)
        if r:
            reasons.append(f"tier2: {r}")
    sheet = {e: round(min(max(v, SCORE_FLOOR), SCORE_CEIL), 4)
             for e, v in sheet.items()}
    return sheet, reasons


def sample_template(text: str, sheet: Dict[str, float]) -> str:
    effs = list(sheet.keys())
    if not effs:
        return "stock"
    if CHOICE_MODE == "argmax":
        return max(effs, key=lambda e: sheet[e])
    weights = [max(sheet[e], 1e-6) ** CHOICE_TEMP for e in effs]
    total = sum(weights)
    rng = random.Random(f"{GLOBAL_SEED}:{text}")
    r = rng.random() * total
    acc = 0.0
    for eff, w in zip(effs, weights):
        acc += w
        if r <= acc:
            return eff
    return effs[-1]


# =============================================================================
# THE LADDER  —  one line in, one Decision out
# =============================================================================

def decide_shot(text: str, meta: dict, ids: List[int],
                state: ScriptState) -> Decision:
    """Walk one line down tier 0 -> 1 -> 2 -> 3.  Read top to bottom; this
    function IS the flow chart."""
    why: List[str] = [f"rule {rid}: {_rule_desc(rid)}" for rid in ids]

    # ---- TIER 0 — hard gates ------------------------------------------------
    allowed = allowed_templates(state)
    if not state.has_visual:
        why.append("tier0: nothing on screen yet -> previous-family blocked")

    # ---- TIER 1 — confident locks (first match wins) ------------------------
    for lock in TIER1_LOCKS:
        hit = lock(text, meta, ids, allowed, state)
        if hit is None:
            continue
        template, term, reason = hit
        if template not in allowed:
            why.append(f"tier1: {lock.__name__} wanted '{template}' "
                       f"but it's gated off -> falling through")
            continue
        why.append(f"tier1: {reason}")
        return Decision(template=template, tier=f"tier1:{lock.__name__}",
                        why=why, term_override=term)

    # ---- TIER 2 + 3 — nudged score-sheet, then sample -----------------------
    sheet, nudge_reasons = build_scoresheet(text, meta, ids, allowed)
    why.extend(nudge_reasons)
    template = sample_template(text, sheet)
    why.append(f"tier3: sampled '{template}' from the score-sheet")
    return Decision(template=template, tier="tier3:sampled",
                    why=why, scoresheet=sheet)


def _rule_desc(rid: int) -> str:
    return RULE_MEDIA_WEIGHTS.get(rid, {}).get("description",
                                               f"<unknown rule {rid}>")


# =============================================================================
# SEARCH-TERM SYNTHESIS  (uses the splitter's spaCy keywords, not regexes)
# =============================================================================

def synthesize_search_term(text: str, meta: dict, decision: Decision,
                           state: ScriptState) -> str:
    if decision.term_override:
        return decision.term_override
    spec = SHOT_TEMPLATES[decision.template]
    keywords = " ".join(meta.get("keywords", [])[:6])
    if decision.template in PREVIOUS_FAMILY:
        if decision.template == "composite_onto_previous":
            return f"add {keywords or text.lower()} to previous"
        if decision.template == "decorate_previous":
            return (keywords or text.lower()).upper()   # on-screen text
        return state.prev_term or keywords or text.lower()
    # wikipedia / map want the entity itself, not a keyword soup
    if spec.material.value in {"wikipedia", "map"}:
        ent = (_first_ent(meta, _PLACE_LABELS)
               or _first_ent(meta, _NAMED_THING_LABELS))
        if ent:
            return ent
    return keywords or text.lower()


# =============================================================================
# SMALL UTILITIES  (unchanged from the previous version)
# =============================================================================

def prefix_from_script_name(script_name: str) -> str:
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
    # v18.1: name changed from SPLIT- to SPLITMETA- because the payload now
    # includes the meta dicts — old caches must not be misread.
    return cache_dir_for(prefix) / f"{_tag()}SPLITMETA-{prefix}.json"


def decide_cache_path(prefix: str) -> Path:
    return cache_dir_for(prefix) / f"{_tag()}DECIDE-{prefix}.json"


def review_sheet_path(prefix: str) -> Path:
    return cache_dir_for(prefix) / f"{_tag()}REVIEW-{prefix}.tsv"


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
# STAGE 1 — SPLIT  (with meta)
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
# STAGE 2 — DECIDE  (the ladder, per line; fully reviewable)
# =============================================================================

def stage_decide(prefix: str,
                 triples: List[Tuple[str, List[int], dict]]) -> List[dict]:
    """Returns a LIST aligned with the split lines (keyed-by-text dicts
    collide on duplicate lines; the old version had that bug)."""
    cache = decide_cache_path(prefix)
    if cache.exists():
        print(f"[decide]  cache hit  -> {cache}")
        return _load_json(cache)

    state = ScriptState()
    rows: List[dict] = []
    for text, ids, meta in triples:
        decision = decide_shot(text, meta, ids, state)
        rows.append({
            "text": text,
            "ids": ids,
            "meta": meta,
            "template": decision.template,
            "tier": decision.tier,
            "why": decision.why,
            "scoresheet": decision.scoresheet,
            "term_override": decision.term_override,
        })
        # keep the DECIDE-time state in sync with what EMIT will do, so
        # tier-0 gating sees the same world in both stages
        _advance_state_for(decision.template, state, term="(pending)")
    _save_json(cache, rows)
    print(f"[decide]  {len(rows)} decisions -> cached {cache}")
    return rows


def _advance_state_for(template: str, state: ScriptState, term: str) -> None:
    """One place that updates the timeline state — used by DECIDE and EMIT."""
    if template in GRID_TEMPLATES:
        state.grid_pos = state.grid_pos % GRID_CELLS + 1
    else:
        state.grid_pos = 0
    if template in FRESH_MATERIAL_TEMPLATES and template not in PREVIOUS_FAMILY:
        state.has_visual = True
        if term and term != "(pending)":
            state.prev_term = term


# =============================================================================
# STAGE 3 — EMIT  (search terms + legacy renderer rows + new fields)
# =============================================================================

def build_final_map(decisions: List[dict]) -> Dict[str, dict]:
    """Emit the renderer-facing map.  The legacy 8 columns are unchanged;
    the NEW columns (template / shot / tier / why) are purely additive.
    NOTE: keyed by line text for renderer compatibility — duplicate lines
    overwrite each other (pre-existing limitation, kept deliberately)."""
    out: Dict[str, dict] = {}
    state = ScriptState()

    for row in decisions:
        text, meta = row["text"], row["meta"]
        decision = Decision(template=row["template"], tier=row["tier"],
                            why=row["why"],
                            term_override=row.get("term_override"),
                            scoresheet=row.get("scoresheet"))
        term = synthesize_search_term(text, meta, decision, state)
        _advance_state_for(decision.template, state, term)
        position = str(state.grid_pos if state.grid_pos else 1)
        spec = SHOT_TEMPLATES[decision.template]

        out[text] = {
            # ---- legacy columns: the renderer reads exactly these ----------
            "search_term": term,
            "search_type": to_legacy(decision.template),
            "position": position,
            "sfx": SFX_DEFAULT,
            "sfx_timing": SFX_TIMING_DEFAULT,
            "music": MUSIC_DEFAULT,
            "music_trim_seconds": MUSIC_TRIM_SECONDS_DEFAULT,
            "music_fade_out": MUSIC_FADE_OUT_DEFAULT,
            # ---- new columns: additive, ignored by the current renderer ----
            "template": decision.template,
            "shot": spec.to_dict(),
            "tier": decision.tier,
            "why": decision.why,
        }
    return out


# =============================================================================
# REVIEW SHEET  —  the eyeball loop (Task 7)
# =============================================================================

def write_review_sheet(prefix: str, decisions: List[dict]) -> Path:
    """One TSV row per line: open in any spreadsheet, filter tier==tier3 to
    review only the lines where dice were rolled."""
    path = review_sheet_path(prefix)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("line\ttier\ttemplate\trule_ids\twhy\n")
        for row in decisions:
            why = " | ".join(row["why"])
            fh.write(f"{row['text']}\t{row['tier']}\t{row['template']}"
                     f"\t{','.join(map(str, row['ids']))}\t{why}\n")
    print(f"[review]  sheet -> {path}")
    return path


# =============================================================================
# TOP-LEVEL ENTRY POINT
# =============================================================================

def generate_script_to_search_term(script_name: str) -> Path:
    """Run split -> decide -> emit for one script.  Every stage is cached; if
    a cache exists the stage is skipped.  Returns the final output path."""
    prefix = prefix_from_script_name(script_name)
    out_path = output_path(prefix)

    mode = "TESTING" if _testing() else "LIVE"
    print(f"=== generate_script_to_search_term "
          f"[prefix={prefix!r}, mode={mode}] ===")

    if out_path.exists():
        print(f"[emit]    cache hit  -> {out_path} (skipping all stages)")
        return out_path

    script_path = _resolve_script_path(script_name)
    triples = stage_split(prefix, script_path)
    decisions = stage_decide(prefix, triples)
    final = build_final_map(decisions)
    _save_json(out_path, final)
    write_review_sheet(prefix, decisions)
    print(f"[emit]    {len(final)} rows -> wrote {out_path}")
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
        print(f"      -> [{cfg['template']} -> legacy {cfg['search_type']}] "
              f"{cfg['tier']}  pos={cfg['position']}  "
              f"term={cfg['search_term']!r}")


if __name__ == "__main__":
    _selftest()
