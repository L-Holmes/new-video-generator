"""
SEARCH_TERM_SYNTHESIS.py  —  MECHANICAL FALLBACK search terms
============================================================================
Terms good enough to never be nonsense (always a concrete noun, referents
resolved, no spoiler machinery beyond per-line words).  The INTENDED quality
path is an LLM pass over the finished JSON using SEARCH_TERM_LLM_PROMPT.md —
it sees the whole script and can protect reveals and craft scene prompts in
ways per-line mechanics cannot.
============================================================================
One entry point, `synthesize()`, dispatched PER TEMPLATE/MATERIAL.  Each
material has a different consumer with different tastes, so each gets its
own explicit contract:

    stock          2-4 words, >=1 concrete NOUN mandatory, order:
                   [adjectives] noun(s) [action] — never verb-only, never
                   empty; anaphora resolved first; "historical" qualifier
                   appended when the script is in a historical era and the
                   line is about people/vessels/conflict
    wikipedia/map  the EXACT entity string, leading determiner stripped,
                   nothing appended (the consumer is a title lookup)
    ai stickman    a fuller scene prompt (up to ~7 words): subject + action
                   + props, anaphora resolved
    ai_edit        an imperative DELTA prompt relative to the standing
                   image: "add another <noun>" / "add <new nouns>"
    composite      "add <new concrete nouns> to previous"
    caption        the quoted span verbatim if the line carries a quote,
                   else the punch keywords — UPPERCASE, numbers kept
    typography     the line itself, verbatim (kinetic typography IS text)
    grid cell      one concrete noun + at most one modifier
    hold/zoom      inherit the standing term

ANAPHORA RESOLUTION (the "this wrinkled seed" problem)
------------------------------------------------------
The splitter marks demonstrative NPs and bare-pronoun subjects; the engine
maintains `subjects` — a recency list of concrete nouns already on screen /
in play — and the script-level `topic` ("nutmeg").  Resolution here:

    bare pronoun subject ("It was worth...")   -> most recent subject
    demonstrative NP  ("this wrinkled seed")   -> line's own words PLUS the
                                                  referent qualifier: the
                                                  topic, or the most recent
                                                  subject that isn't the
                                                  head noun itself
                                                  -> "wrinkled nutmeg seed"
    no nouns at all on the line                -> most recent subject, else
                                                  the script topic

HONESTY CLAUSE: this is lemma matching + recency, not real coreference.
With two equally recent candidate referents it can pick the wrong one.  It
converts "relies on invisible context" into "usually right, always
concrete" — which is the achievable target without a coref model.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from SPLIT_AND_LABEL_CONFIG import SHOT_TEMPLATES, Material

# ALL word lists live in the splitter (the linguistic-lexicon home) —
# this module defines none of its own.
from sentence_splitter import ERA_STYLE_NOUNS

_MAX_STOCK_WORDS = 4
_MAX_AI_WORDS = 7


# =============================================================================
# ANAPHORA RESOLUTION
# =============================================================================

def resolve_base_subject(meta: dict, subjects: List[str]) -> str:
    """The concrete thing this line is ABOUT, resolved through recency."""
    head = meta.get("head_noun", "")
    topic = meta.get("script_topic", "")
    if meta.get("pronoun_subject") and not head:
        return subjects[0] if subjects else topic
    if not meta.get("nouns"):
        return subjects[0] if subjects else topic
    return head


def _referent_qualifier(meta: dict, subjects: List[str]) -> str:
    """For a demonstrative NP: what earlier thing does it point back to?"""
    head = meta.get("head_noun", "")
    topic = meta.get("script_topic", "")
    if topic and topic != head:
        return topic
    for s in subjects:
        if s and s != head:
            return s
    return ""


# =============================================================================
# PER-MATERIAL BUILDERS
# =============================================================================

def _strip_determiner(name: str) -> str:
    for det in ("the ", "The ", "a ", "A ", "an ", "An "):
        if name.startswith(det):
            return name[len(det):]
    return name


def _quoted_span(text: str) -> str:
    for open_q, close_q in (('"', '"'), ("\u201c", "\u201d"), ("'", "'")):
        if open_q in text:
            after = text.split(open_q, 1)[1]
            if close_q in after:
                inner = after.split(close_q, 1)[0].strip()
                if inner:
                    return inner
    return ""


def _era_is_historical(meta: dict, subjects_era: str) -> bool:
    return subjects_era == "historical"


def _build_stock_term(text, meta, subjects, era) -> str:
    keywords: List[str] = list(meta.get("keywords", []))
    nouns = list(meta.get("nouns", []))
    words: List[str] = []

    if meta.get("demonstrative") and nouns:
        qualifier = _referent_qualifier(meta, subjects)
        # line's own modifiers + the resolved referent + the head noun:
        # "this little wrinkled seed" -> "wrinkled nutmeg seed"
        modifiers = [w for w in keywords if w not in nouns][:1]
        words = modifiers + ([qualifier] if qualifier else []) + \
            [meta.get("head_noun") or nouns[0]]
    elif not nouns:
        base = resolve_base_subject(meta, subjects)
        modifiers = [w for w in keywords][:2]
        words = ([base] if base else []) + modifiers
    else:
        words = keywords[:]

    # dedupe, cap, and guarantee a noun is present
    seen, out = set(), []
    for w in words:
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    out = out[:_MAX_STOCK_WORDS]
    if not set(out) & set(nouns) and not meta.get("demonstrative"):
        base = resolve_base_subject(meta, subjects)
        if base and base not in out:
            out = [base] + out[:_MAX_STOCK_WORDS - 1]

    if era == "historical" and (
            set(nouns) & ERA_STYLE_NOUNS
            or any(e["label"] in {"PERSON", "NORP", "EVENT", "ORG"}
                   for e in meta.get("ents", []))):
        out.append("historical")

    return " ".join(out) if out else (meta.get("script_topic") or text.lower())


def _build_ai_scene_term(text, meta, subjects, era) -> str:
    """Stickman prompts can afford a fuller scene description."""
    keywords = list(meta.get("keywords", []))
    nouns = meta.get("nouns", [])
    words = keywords[:_MAX_AI_WORDS]
    if not nouns:
        base = resolve_base_subject(meta, subjects)
        if base:
            words = [base] + words[:_MAX_AI_WORDS - 1]
    elif meta.get("demonstrative"):
        qualifier = _referent_qualifier(meta, subjects)
        if qualifier and qualifier not in words:
            words = [qualifier] + words[:_MAX_AI_WORDS - 1]
    return " ".join(dict.fromkeys(w for w in words if w)) or \
        (meta.get("script_topic") or text.lower())


def _build_ai_edit_term(text, meta, subjects) -> str:
    """Imperative delta prompt relative to the standing AI image."""
    nouns = meta.get("nouns", [])
    new_nouns = [n for n in nouns if n not in subjects]
    head = meta.get("head_noun", "")
    if head and head in subjects and meta.get("has_number"):
        return f"add another {head}"        # "two dollars" -> another coin
    if new_nouns:
        return "add " + " ".join(new_nouns[:3])
    kw = " ".join(meta.get("keywords", [])[:3])
    return f"change to {kw}" if kw else "emphasise the subject"


def _build_composite_term(text, meta, subjects) -> str:
    nouns = meta.get("nouns", [])
    new_nouns = [n for n in nouns if n not in subjects] or nouns
    if new_nouns:
        return "add " + " ".join(new_nouns[:3]) + " to previous"
    base = resolve_base_subject(meta, subjects)
    return f"add {base or 'detail'} to previous"


def _build_caption_term(text, meta) -> str:
    quoted = _quoted_span(text) if meta.get("in_quote") else ""
    if quoted:
        return quoted.upper()
    kw = " ".join(meta.get("keywords", [])[:4])
    return (kw or text).upper()


def _build_grid_cell_term(text, meta, subjects) -> str:
    nouns = meta.get("nouns", [])
    keywords = meta.get("keywords", [])
    if nouns:
        modifiers = [w for w in keywords if w not in nouns][:1]
        return " ".join(modifiers + [nouns[0]])
    base = resolve_base_subject(meta, subjects)
    return base or " ".join(keywords[:2]) or text.lower()


def _entity_term(meta: dict, place_first: bool) -> str:
    place = next((e["text"] for e in meta.get("ents", [])
                  if e["label"] in {"GPE", "LOC"}), "")
    thing = next((e["text"] for e in meta.get("ents", [])
                  if e["label"] in {"PERSON", "ORG", "FAC", "EVENT",
                                    "WORK_OF_ART", "NORP"}), "")
    ent = (place or thing) if place_first else (thing or place)
    return _strip_determiner(ent)


# =============================================================================
# THE ENTRY POINT
# =============================================================================

def synthesize(text: str, meta: dict, template: str,
               subjects: List[str], prev_term: str,
               era: str = "", term_override: Optional[str] = None) -> str:
    """Return the search term / prompt / caption for one decided line."""
    spec = SHOT_TEMPLATES[template]

    if template == "new__typography":
        return text                               # typography IS the line
    if template in {"editprev__caption", "editprev__draw"}:
        return _build_caption_term(text, meta)
    if term_override:
        if spec.material in {Material.WIKIPEDIA, Material.MAP}:
            return _strip_determiner(term_override)
        return term_override
    if template == "editprev__ai_edit":
        return _build_ai_edit_term(text, meta, subjects)
    if template == "editprev__add_stock":
        return _build_composite_term(text, meta, subjects)
    if template in {"editprev__hold", "editprev__zoom"}:
        return prev_term or _build_stock_term(text, meta, subjects, era)
    if spec.material is Material.WIKIPEDIA:
        return _entity_term(meta, place_first=False) or \
            _build_stock_term(text, meta, subjects, era)
    if spec.material is Material.MAP:
        return _entity_term(meta, place_first=True) or \
            _build_stock_term(text, meta, subjects, era)
    if spec.layout.kind.value == "grid":
        return _build_grid_cell_term(text, meta, subjects)
    if spec.material is Material.AI_STOCK:
        return _build_ai_scene_term(text, meta, subjects, era)
    # stock / stock_image (object_generate) / boards
    return _build_stock_term(text, meta, subjects, era)
