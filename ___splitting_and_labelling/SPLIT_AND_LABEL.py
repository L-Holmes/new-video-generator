"""
SPLIT_AND_LABEL.py
==================
Turn a raw narration *script* into a per-line **shot list** — a map from each
phrase-line of the script to a shot (media type; search terms are added by the
ADD_SEARCH_TEXT.py review loop afterwards) the downstream
renderer can act on.

        script-spices.txt   ->   spices-script_to_search_term.json

======================= THE DECISION FLOW (read this first) ===================

Every line from the splitter walks DOWN this ladder and stops at the first
tier that decides.  Tiers 0-1 are DETERMINISTIC; only tier 3 rolls dice.

    line (text + rule ids + meta from the splitter)
      |
      v
    TIER 0  HARD GATES         what is even possible right now?
      |                        • no image on screen yet -> the whole
      |                          edit_previous family is off the menu
      |                        • AI_ENABLED off -> AI templates off the menu
      |                        • editprev__ai_edit only if the PREVIOUS frame
      |                          was AI material (it edits an AI image)
      |                        • requirement templates need their ingredient:
      |                          wikipedia needs a named thing, map needs a
      |                          place, grids need a list run — without it
      |                          they are NOT ON THE MENU (not just unlikely)
      v
    TIER 1  CONFIDENT LOCKS    first matching lock wins, in this order:
      |                          1. cold open, nothing to picture
      |                                                 -> new__typography
      |                          2. list run            -> grid (stickman
      |                                                    grid when AI on)
      |                          3. money amount        -> new__object
      |                          4. place named         -> map (obscure)
      |                                                    stock (famous)
      |                          5. named person/thing  -> wikipedia (obscure)
      |                                                    stock (famous)
      |                          6. quoted speech       -> editprev__caption
      |                          7. SFX beat (rule 60)  -> editprev__caption
      |                          8. nothing picture-able-> editprev__hold
      |                        no lock matched? fall through:
      v
    TIER 2  CONTEXT NUDGES     multiply/floor the score-sheet:
      |                          opener vs continuation, maybe-a-name,
      |                          maybe-a-place  (nothing is decided here)
      v
    TIER 3  WEIGHTED SAMPLE    stock-dominant prior + per-rule affinities
      |                        from RULE_MEDIA_WEIGHTS, seeded sample.
      |                        Grids are LOCK-ONLY: never sampleable here.
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
    write search terms............. NOT HERE.  This file decides SHOTS only;
                                    search_term is emitted empty and filled
                                    by ADD_SEARCH_TEXT.py (LLM + rule lists —
                                    see prompts/ and MASTER_README.md)

PIPELINE
--------
    1. SPLIT    sentence_splitter.split_text_into_sections_with_meta()
                -> [(line_text, rule_ids, meta)]   meta = spaCy-grounded facts
                   (entities, opener flag, keywords, list membership...) so
                   NOTHING below re-detects language features with regexes.
                   The ONLY cached stage (spaCy is the expensive part).
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
from sentence_splitter import (split_text_into_sections_with_meta,
                               RULE_DESCRIPTIONS)

# --- OUR media vocabulary (separate from the renderer's CONFIG.py) -----------
from SPLIT_AND_LABEL_CONFIG import (
    SHOT_TEMPLATES, PREVIOUS_FAMILY, GRID_TEMPLATES, FRESH_MATERIAL_TEMPLATES,
    AI_TEMPLATES, TEMPLATE_REQUIREMENTS, LOCK_ONLY_TEMPLATES,
    PRIOR_OPENER, PRIOR_CONT, CONT_FRESH_DAMP, Strategy,
    GRID_CELLS, Material, to_legacy,
)

# --- the reviewable per-rule affinity table (tier 3 only) --------------------
from RULE_MEDIA_WEIGHTS import RULE_MEDIA_WEIGHTS


# =============================================================================
# TOGGLES  —  the knobs you are meant to flip
# =============================================================================

# When True, every file this script WRITES is prefixed with "TESTING_".
TESTING_SCRIPT_SEARCH_TERM_GENERATION = True

# The "AI stuff" master switch.  When False (default) every template that
# needs AI (derived from its AXES in the config — material/base — never from
# its name) is removed from the menu at tier 0.
AI_ENABLED = False

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
# THE ONLY SOURCE OF TRUTH is ENTITY_FAME_CACHE.json next to this file:
#     { "<lowercased name>": "famous" | "obscure" }
# Populate it with populate_entity_fame.py (hits the Wikipedia/Wikidata APIs
# and thresholds sitelink counts / pageviews — run it on a machine with
# network access; it merges, never overwrites).
#
# There is deliberately NO built-in list of famous names in this codebase.
# Production code must carry zero world-knowledge answer keys: an earlier
# version had a hardcoded famous-places set, which made the fixture test
# circular (it "passed" because the answer was baked in).  Every name the
# cache doesn't cover is "unknown", which safely degrades a hard lock into
# a soft tier-2 nudge — never a wrong deterministic choice.

ENTITY_FAME_CACHE_PATH = Path(__file__).resolve().parent / "ENTITY_FAME_CACHE.json"

_fame_cache: Optional[Dict[str, str]] = None


def entity_fame(name: str) -> str:
    """Return 'famous' / 'obscure' / 'unknown' for an entity name.
    Cache-driven only — see the policy note above."""
    global _fame_cache
    if _fame_cache is None:
        if ENTITY_FAME_CACHE_PATH.exists():
            _fame_cache = {k.lower(): v for k, v in
                           json.loads(ENTITY_FAME_CACHE_PATH.read_text(
                               encoding="utf-8")).items()}
        else:
            _fame_cache = {}
    return _fame_cache.get(name.lower().strip(), "unknown")


# =============================================================================
# THE DECISION OBJECT  —  what one line resolves to
# =============================================================================

@dataclass
class Decision:
    template: str                       # key into SHOT_TEMPLATES
    tier: str                           # "tier1:lock_place" / "tier3:sampled"
    why: List[str]                      # human-readable audit trail

    scoresheet: Optional[Dict[str, float]] = None  # tier-3 decisions only


@dataclass
class ScriptState:
    """What the engine knows about the timeline so far (mutated by EMIT)."""
    has_visual: bool = False    # has ANY fresh image reached the screen yet?
    prev_material: str = ""     # Material.value of the last fresh shot
    grid_pos: int = 0           # 1-based position inside a running grid
    # SUBJECTS IN PLAY: recency-ordered concrete nouns/entities the script
    # has put on screen or in the narration ("nutmeg", "banda islands"...).
    # Term synthesis resolves "this wrinkled seed" / "it" against this.
    subjects: List[str] = field(default_factory=list)



# =============================================================================
# TIER 0 — HARD GATES  (what is even possible right now)
# =============================================================================

_PLACE_LABELS = {"GPE", "LOC"}
_NAMED_THING_LABELS = {"PERSON", "ORG", "FAC", "EVENT", "WORK_OF_ART", "NORP"}


def _meets_requirement(requirement: str, meta: dict) -> bool:
    if requirement == "named_thing_entity":
        return _first_ent(meta, _NAMED_THING_LABELS) is not None
    if requirement == "place_entity":
        return _first_ent(meta, _PLACE_LABELS) is not None
    if requirement == "list":
        return bool(meta.get("list"))
    raise ValueError(f"unknown template requirement {requirement!r}")


def allowed_templates(state: ScriptState, meta: dict) -> set:
    """The menu tier 1 and tier 3 are allowed to choose from.
    Everything removed here is IMPOSSIBLE for this line, not just unlikely."""
    allowed = set(SHOT_TEMPLATES)
    if not AI_ENABLED:
        allowed -= AI_TEMPLATES
    if not state.has_visual:
        # Can't act on a previous image before any fresh visual exists.
        allowed -= PREVIOUS_FAMILY
    if state.prev_material != Material.AI_STOCK.value:
        # ai_edit edits the PRECEDING AI IMAGE — meaningless otherwise.
        allowed.discard("editprev__ai_edit")
    # Requirement templates need their ingredient on THIS line
    # (wikipedia -> named thing, map -> place, grid -> list run).
    for template, requirement in TEMPLATE_REQUIREMENTS.items():
        if template in allowed and not _meets_requirement(requirement, meta):
            allowed.discard(template)
    return allowed


# =============================================================================
# TIER 1 — CONFIDENT LOCKS
# =============================================================================
# Each lock inspects one line and either returns (template, reason) or
# None.  Locks run IN THE ORDER of TIER1_LOCKS; the first match
# wins.  A lock whose template isn't in `allowed` is skipped (falls through).
#
# Design rule: a lock must only fire on evidence strong enough that you'd be
# happy for it to be 100% deterministic.  If you're tuning probabilities,
# you're writing a tier-2 nudge, not a lock.

_SFX_RULE_ID = 60


def _first_ent(meta: dict, labels: set) -> Optional[str]:
    for e in meta.get("ents", []):
        if e["label"] in labels:
            return e["text"]
    return None


def lock_cold_open(text, meta, ids, allowed, state):
    """Nothing on screen yet AND nothing picture-able on the line: kinetic
    typography on a blank background (legacy read_out) — never a random
    stock fetch for an abstract opener."""
    if not state.has_visual and meta.get("has_visualisable") is False:
        return ("new__typography",
                "cold open with nothing picture-able -> script text on blank")
    return None


def lock_list_grid(text, meta, ids, allowed, state):
    """A detected list run stays coherent: every item -> the same grid.
    Stickman tiles when AI is on, stock tiles otherwise."""
    if meta.get("list"):
        li = meta["list"]
        template = ("editgroup__ai"
                    if AI_ENABLED and "editgroup__ai" in allowed
                    else "editgroup__stock")
        return (template,
                f"list run (group {li['group']}, item {li['index'] + 1}"
                f"/{li['size']}) -> one grid cell per item")
    return None


def lock_money(text, meta, ids, allowed, state):
    """A money amount is a graphic beat -> the object editor."""
    if meta.get("has_money"):
        return ("new__object",
                "money amount on the line -> new__object")
    return None


def lock_place(text, meta, ids, allowed, state):
    """A named place: famous -> stock footage of it; obscure -> map.
    Fame comes ONLY from the data cache; unknown fame never locks."""
    place = _first_ent(meta, _PLACE_LABELS)
    if not place:
        return None
    fame = entity_fame(place)
    if fame == "famous":
        return ("new__stock",
                f"famous place '{place}' -> stock footage of it")
    if fame == "obscure":
        return ("new__map", f"obscure place '{place}' -> highlighted map")
    return None    # unknown fame -> tier 2 nudges handle it


def lock_named_thing(text, meta, ids, allowed, state):
    """A named person/org/artwork: famous -> stock; obscure -> wikipedia."""
    name = _first_ent(meta, _NAMED_THING_LABELS)
    if not name:
        return None
    fame = entity_fame(name)
    if fame == "famous":
        return ("new__stock",
                f"famous name '{name}' -> stock footage of them/it")
    if fame == "obscure":
        return ("new__wikipedia",
                f"obscure name '{name}' -> wikipedia image")
    return None


def lock_quote(text, meta, ids, allowed, state):
    """Quoted speech goes on screen as text over the standing image."""
    if meta.get("in_quote"):
        return ("editprev__caption",
                "quoted speech -> caption over the previous image")
    return None


def lock_sfx(text, meta, ids, allowed, state):
    """A splitter SFX beat (rule 60) is a punch, not a new fetch."""
    if _SFX_RULE_ID in ids:
        return ("editprev__caption",
                "SFX beat (rule 60) -> big word over the previous image")
    return None


def lock_unvisualisable(text, meta, ids, allowed, state):
    """Nothing picture-able on the line -> hold what's on screen."""
    if meta.get("has_visualisable") is False:
        return ("editprev__hold",
                "no picture-able content -> hold the previous image")
    return None


# ORDER MATTERS.  Cold open runs first (it's the only lock valid before any
# visual exists); list coherence beats content locks; the weakest evidence
# (unvisualisable) goes last so stronger signals get first refusal.
TIER1_LOCKS = [
    lock_cold_open,
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
# None if it didn't apply).  Nothing here decides anything.  The BIG pacing
# behaviour (openers fetch fresh, continuations make small edits) lives in
# the config priors, NOT here — see PRIOR_OPENER / PRIOR_CONT.

_SAME_SUBJECT_ADD_MULT = 2.4  # continuation of the SAME subject -> layer on
_SAME_SUBJECT_ZOOM_MULT = 1.8
_SAME_SUBJECT_STOCK_MULT = 0.6
_NEW_NOUN_ADD_MULT = 2.0      # fresh noun mid-sentence -> add it onto prev
_NEW_NOUN_ADD_FLOOR = 0.30
_MAYBE_NAME_WIKI_FLOOR = 0.40  # unknown-fame name -> wikipedia competitive
_MAYBE_NAME_OG_MULT = 1.25
_MAYBE_PLACE_MAP_FLOOR = 0.50  # unknown-fame place -> map competitive
                               # ("modern-day Indonesia" must beat hold)


def nudge_same_subject_layering(sheet, text, meta, ids, state):
    """The line continues talking about something already on screen ->
    prefer ADDING TO the standing image over fetching a fresh one.  This is
    the mechanical version of 'growing nutmeg plants, then layer on top'."""
    if meta.get("opener"):
        return None
    same = (meta.get("pronoun_subject")
            or (meta.get("head_noun") and meta["head_noun"] in state.subjects))
    if not same:
        return None
    for eff, mult in (("editprev__add_stock", _SAME_SUBJECT_ADD_MULT),
                      ("editprev__ai_edit", _SAME_SUBJECT_ADD_MULT),
                      ("editprev__zoom", _SAME_SUBJECT_ZOOM_MULT)):
        if eff in sheet:
            sheet[eff] *= mult
    if "new__stock" in sheet:
        sheet["new__stock"] *= _SAME_SUBJECT_STOCK_MULT
    return "same subject still in play -> layer onto the previous image"


def nudge_new_noun_addition(sheet, text, meta, ids, state):
    """A NEW concrete noun arrives mid-sentence ("...on | the planet.") ->
    the natural beat is adding that thing onto the standing image, not
    captioning or refetching."""
    if meta.get("opener") or not meta.get("nouns"):
        return None
    head = meta.get("head_noun")
    if head and head not in state.subjects:
        for eff in ("editprev__add_stock", "editprev__ai_edit"):
            if eff in sheet:
                sheet[eff] = max(sheet[eff] * _NEW_NOUN_ADD_MULT,
                                 _NEW_NOUN_ADD_FLOOR)
        return "new concrete noun mid-sentence -> add it onto the previous"
    return None


def nudge_maybe_name(sheet, text, meta, ids, state):
    """A named thing whose fame we DON'T know: lean wikipedia, don't lock."""
    if _first_ent(meta, _NAMED_THING_LABELS):
        if "new__wikipedia" in sheet:
            sheet["new__wikipedia"] = max(sheet["new__wikipedia"],
                                          _MAYBE_NAME_WIKI_FLOOR)
        if "new__object" in sheet:
            sheet["new__object"] *= _MAYBE_NAME_OG_MULT
        return "unknown-fame name -> wikipedia leaned up"
    return None


def nudge_maybe_place(sheet, text, meta, ids, state):
    """A place whose fame we DON'T know: lean map hard, don't lock."""
    if _first_ent(meta, _PLACE_LABELS):
        if "new__map" in sheet:
            sheet["new__map"] = max(sheet["new__map"], _MAYBE_PLACE_MAP_FLOOR)
        return "unknown-fame place -> map floored up"
    return None


TIER2_NUDGES = [
    nudge_same_subject_layering,
    nudge_new_noun_addition,
    nudge_maybe_name,
    nudge_maybe_place,
]


# =============================================================================
# TIER 3 — WEIGHTED SAMPLE
# =============================================================================

SCORE_FLOOR = 0.005
SCORE_CEIL = 0.99


def build_scoresheet(text, meta, ids, allowed,
                     state) -> Tuple[Dict[str, float], List[str]]:
    """EXPECTED-RATIO prior (opener vs continuation, from the config) ->
    MAX with each splitter rule's affinities -> continuation damp on fresh
    templates -> tier-2 nudges.  LOCK_ONLY templates (the editgroups) are
    excluded: a list run locks them at tier 1; nothing samples them."""
    opener = bool(meta.get("opener"))
    prior = PRIOR_OPENER if opener else PRIOR_CONT
    sampleable = allowed - LOCK_ONLY_TEMPLATES
    sheet = {t: prior.get(t, 0.02) for t in SHOT_TEMPLATES if t in sampleable}
    for rid in ids:
        row = RULE_MEDIA_WEIGHTS.get(rid, {})
        for eff, val in row.items():
            if eff in sheet:
                sheet[eff] = max(sheet[eff], float(val))
    reasons = []
    if not opener:
        # keep loud rule weights from overturning the "mostly small edits
        # mid-sentence" mix (see the config's EXPECTED PACING RATIO note)
        for eff in list(sheet):
            if SHOT_TEMPLATES[eff].strategy is Strategy.NEW:
                sheet[eff] *= CONT_FRESH_DAMP
        reasons.append("tier2: continuation -> fresh-material damped "
                       "(mostly small edits mid-sentence)")
    for nudge in TIER2_NUDGES:
        r = nudge(sheet, text, meta, ids, state)
        if r:
            reasons.append(f"tier2: {r}")
    sheet = {e: round(min(max(v, SCORE_FLOOR), SCORE_CEIL), 4)
             for e, v in sheet.items()}
    return sheet, reasons

def sample_template(text: str, sheet: Dict[str, float]) -> str:
    effs = list(sheet.keys())
    if not effs:
        return "new__stock"
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
    allowed = allowed_templates(state, meta)
    if not state.has_visual:
        why.append("tier0: nothing on screen yet -> previous-family blocked")
    if not AI_ENABLED:
        why.append("tier0: AI disabled -> AI templates off the menu")

    # ---- TIER 1 — confident locks (first match wins) ------------------------
    for lock in TIER1_LOCKS:
        hit = lock(text, meta, ids, allowed, state)
        if hit is None:
            continue
        template, reason = hit
        if template not in allowed:
            why.append(f"tier1: {lock.__name__} wanted '{template}' "
                       f"but it's gated off -> falling through")
            continue
        why.append(f"tier1: {reason}")
        return Decision(template=template, tier=f"tier1:{lock.__name__}",
                        why=why)

    # ---- TIER 2 + 3 — nudged score-sheet, then sample -----------------------
    sheet, nudge_reasons = build_scoresheet(text, meta, ids, allowed, state)
    why.extend(nudge_reasons)
    template = sample_template(text, sheet)
    why.append(f"tier3: sampled '{template}' from the score-sheet")
    return Decision(template=template, tier="tier3:sampled",
                    why=why, scoresheet=sheet)


def _rule_desc(rid: int) -> str:
    # ONE master copy: sentence_splitter.RULE_DESCRIPTIONS.  The weights
    # file no longer mirrors descriptions (redundancy removed in v2).
    return RULE_DESCRIPTIONS.get(rid, f"<unknown rule {rid}>")


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
    # Cache name carries a schema version: whenever the split OUTPUT changes
    # the suffix bumps so stale caches can never be silently misread.
    # (v18.2 whitespace, v18.3 subject keys, v18.5 idioms + rule 61 -> META4)
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

def stage_decide(triples: List[Tuple[str, List[int], dict]]) -> List[dict]:
    """Returns a LIST aligned with the split lines (keyed-by-text dicts
    collide on duplicate lines).  Not cached: decisions are cheap and
    seeded-deterministic; only the spaCy split is worth caching."""
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
        })
        # keep the DECIDE-time state in sync with what EMIT will do, so
        # tier-0 gating sees the same world in both stages
        _advance_state_for(decision.template, state, meta=meta)
    print(f"[decide]  {len(rows)} decisions")
    return rows


def _advance_state_for(template: str, state: ScriptState,
                       meta: Optional[dict] = None) -> None:
    """One place that updates the timeline state — used by DECIDE and EMIT.
    Grid position comes from the LIST META index when available (broken
    position runs are impossible by construction); the counter is only a
    fallback for grids without list meta (shouldn't happen: grids are
    lock-only and the lock requires list meta)."""
    if template in GRID_TEMPLATES:
        li = (meta or {}).get("list")
        if li:
            state.grid_pos = li["index"] % GRID_CELLS + 1
        else:
            state.grid_pos = state.grid_pos % GRID_CELLS + 1
    else:
        state.grid_pos = 0
    if template in FRESH_MATERIAL_TEMPLATES and template not in PREVIOUS_FAMILY:
        state.has_visual = True
        state.prev_material = SHOT_TEMPLATES[template].material.value
    if meta:
        # SUBJECTS IN PLAY: most recent first, deduped, capped.
        for new_subject in ([meta.get("head_noun", "")]
                            + [e["text"].lower() for e in meta.get("ents", [])]):
            if new_subject:
                if new_subject in state.subjects:
                    state.subjects.remove(new_subject)
                state.subjects.insert(0, new_subject)
        del state.subjects[5:]


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
                            why=row["why"])
        _advance_state_for(decision.template, state, meta=meta)
        position = str(state.grid_pos if state.grid_pos else 1)
        spec = SHOT_TEMPLATES[decision.template]
        shot_dict = spec.to_dict()
        li = meta.get("list")
        if li and decision.template in GRID_TEMPLATES:
            # RULE OF N: the group's real size, not a hardcoded 3.  (The
            # legacy renderer still draws 3-cell rows — position cycles for
            # its benefit; see TODO_LEGACY_SWITCHOVER.md.)
            shot_dict["layout"]["n"] = li["size"]

        out[text] = {
            # ---- legacy columns: the renderer reads exactly these ----------
            # search_term is INTENTIONALLY EMPTY: terms are written by the
            # ADD_SEARCH_TEXT.py review loop (LLM + your rules), never by
            # per-line mechanics.  See MASTER_README.md.
            "search_term": "",
            "search_type": to_legacy(decision.template, AI_ENABLED),
            "position": position,
            "sfx": SFX_DEFAULT,
            "sfx_timing": SFX_TIMING_DEFAULT,
            "music": MUSIC_DEFAULT,
            "music_trim_seconds": MUSIC_TRIM_SECONDS_DEFAULT,
            "music_fade_out": MUSIC_FADE_OUT_DEFAULT,
            # ---- new columns: additive, ignored by the current renderer ----
            "template": decision.template,
            "shot": shot_dict,
            "tier": decision.tier,
            "why": decision.why,
            # the splitter's tags — useful context for the search-text AI
            "rule_ids": row["ids"],
        }
    return out


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
    decisions = stage_decide(triples)
    final = build_final_map(decisions)
    _save_json(out_path, final)
    print(f"[emit]    {len(final)} rows -> wrote {out_path} "
          f"(search terms empty: fill them with ADD_SEARCH_TEXT.py)")
    return out_path


# =============================================================================
# SELF-TEST  —  running the file directly is ALWAYS a TESTING run
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
    # Edge cases: 4-item list (rule of N), quotes, SFX beat, retention hook,
    # money, an obscure name, "as if" comparison, an exception reveal.
    "script-whales.txt": (
        "Here's the thing. In the middle of the Sahara sits a valley called "
        "Wadi Al-Hitan. Scientists digging there found ribs, vertebrae, "
        "teeth, and entire skulls. Whale skulls. The ground looked as if an "
        "ocean had simply dried up around them. Every fossil was intact "
        "except one. Locals swear the wind sounds like whale song at night. "
        "And then boom, a sandstorm buried the site for a decade. Recovering "
        "it cost about $2 million."
    ),
    # Edge cases: hard-wrapped text with indentation (the \n regression),
    # dates flipping era mode, demonstratives, a passive agent, a pivot.
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
    TESTING_SCRIPT_SEARCH_TERM_GENERATION = True   # HARD RULE: local == testing

    for script_name, body in _SAMPLE_SCRIPTS.items():
        if not Path(script_name).exists():
            Path(script_name).write_text(body, encoding="utf-8")
            print(f"[selftest] wrote bundled sample -> {script_name}")

        out = generate_script_to_search_term(script_name)

        print(f"\n----- OUTPUT PREVIEW: {script_name} -----")
        data = _load_json(out)
        for i, (line, cfg) in enumerate(data.items()):
            if i >= 12:
                print(f"... ({len(data) - 12} more rows)")
                break
            print(f"  {line!r}")
            print(f"      -> [{cfg['template']} -> legacy {cfg['search_type']}]"
                  f" {cfg['tier']}  pos={cfg['position']}  "
                  f"term={cfg['search_term']!r}")


if __name__ == "__main__":
    _selftest()
