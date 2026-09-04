"""
sentence_splitter.py
====================
VERSION: v18  (comparison / exception / pivot / agent / SFX reveal rules)

Split prose into short, scannable phrase-lines for visual presentation —
captions, kinetic typography, slide-decks, animation cues, YouTube videos.

DESIGN PHILOSOPHY
-----------------
Mimic how a writer would line-break their own prose for emphasis:

    • LIST items each get their own line.
    • REVEALS (proper nouns, numbers, dramatic content words before "...")
      get their own line.
    • HARD PUNCTUATION ends a line — full stop, exclaim, question, semi, colon.
    • COMMAS act as soft break-points: between clauses (verb,verb)
      AND between list items (noun,noun).
    • SHORT CONNECTORS ("and", "the", "where") cling to whichever neighbour
      contains their grammatical HEAD (not just blindly forward or backward),
      and NEVER cross a sentence boundary.
    • IDIOMATIC UNITS — phrasal verbs, possessives, "what if", named entities,
      hyphenated compounds, currency-amount tokens, "used to <verb>" — stay intact.
    • PP-INTERNAL ENTITIES like "in Egypt" / "on Earth" / "of India" are NOT
      treated as reveals — those are qualifiers, not the dramatic noun.
    • MULTI-TOKEN entities ("the Tethys Sea", "the Atacama Desert") DO get
      a reveal line, with the split placed BEFORE the determiner that begins
      the noun chunk so the chunk reads cleanly.
    • DRAMATIC ELLIPSES ("whale fossils…") get a reveal line for the immediately
      preceding noun phrase.

The rules are deliberately structural (POS / DEP / NER / sentence position),
not lexical, so they generalise across vocabulary.  The only hard-coded
lexical sets are punctuation marks and a handful of frozen idioms ("what if").

USAGE
-----
    >>> from sentence_splitter import split_text_into_sections
    >>> split_text_into_sections("The fast cat sat on the comfortable mat")
    [Chunk(text='The fast cat sat', ids=[]), Chunk(text='on the comfortable mat', ids=[])]

    v18.1: downstream consumers that need per-line facts (entities, opener
    flag, keywords, list membership...) should use the richer API instead:

    >>> from sentence_splitter import split_text_into_sections_with_meta
    >>> split_text_into_sections_with_meta("In Egypt, whales turned to stone")
    [ChunkWithMeta(text='In Egypt,', ids=[7], meta={'opener': True,
        'ents': [{'text': 'Egypt', 'label': 'GPE'}], ...}), ...]

DATA SHAPE
----------
The pipeline no longer passes bare lists of sentence strings around.  Instead
it passes an *ordered map* — a plain list (so order is preserved and duplicate
texts are allowed) whose entries each map a phrase-line of text to a list of
integer ids.  See the ``Chunk`` / ``ChunkMap`` definitions and ``merge_chunks``
below.  The id-list is a placeholder and is **always empty for now**.
"""
from __future__ import annotations

import re
from typing import Dict, List, NamedTuple, Set, Optional, Tuple

import spacy
from spacy.tokens import Doc, Span, Token

# =============================================================================
# SHARED TEXT LOGIC  —  every word list, regex, threshold and text-check that
# this file and 2-auto-tagging both rely on now lives in ONE place:
#     ___splitting_and_labelling/shared_text_logic.py
# Nothing lexical is defined in this file any more.  The names below are
# imported verbatim, so the rules read exactly as they always did.
# =============================================================================
# This file lives in 0-sentence-splitter/ and shared_text_logic.py one level
# up, so PATHS.py (which knows where every stage folder is) goes on first.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import PATHS  # noqa: F401,E402  — every stage folder on sys.path

from shared_text_logic import (  # noqa: E402
    # --- rule ids (SECTION 1) ---
    RULE_DESCRIPTIONS, describe_rule, SPLIT_RULE_IDS, LIST_RUN_RULE_IDS,
    # --- punctuation & symbols (2.1) ---
    HARD_PUNCT, DASH_PUNCT, LONG_DASH_PUNCT, OPEN_QUOTES, CLOSE_QUOTES,
    ANY_QUOTE, APOSTROPHES, OPEN_BRACKETS, CLOSE_BRACKETS, CURRENCY_SYMS,
    MARKDOWN_EMPHASIS_CHARS,
    # --- spaCy tag / dep / entity sets (2.2) ---
    WH_TAGS, RELATIVE_PRONOUN_TAGS, REVEAL_ENTS, LOCATION_ENTS, NUMERIC_ENTS,
    NUMERIC_NO_REVEAL, NUMERIC_QUALIFIER_ENTS, VERB_MOD_DEPS, AUX_LIKE_DEPS,
    PARTICLE_DEPS, LIGHTWEIGHT_POS,
    # --- function words & closed classes (2.3) ---
    PROMISCUOUS_PREPS, DISCOURSE_INIT, ADV_INTRODUCERS, TRANSITION_ADVERBS,
    COMPARATIVE_MARKERS, NEGATION_TOKENS, APPROXIMATOR_WORDS,
    ANAPHORIC_SUBJECT_PRONOUNS, NOUN_LIST_BLOCKED_PREPS,
    NOUN_LIST_SPLITTABLE_PREPS,
    # --- verb meaning families (2.4) ---
    COPULA_BE_LEMMAS, COPULA_SENSORY_LEMMAS, COPULA_BECOMING_LEMMAS,
    COPULA_STAYING_LEMMAS, COPULA_JUDGMENT_LEMMAS, STRONG_COPULA_LEMMAS,
    ALL_COPULA_LEMMAS, COPULAR_LEMMAS, COPULAR_FORMS,
    POSSESSION_CORE_LEMMAS, POSSESSION_CONTAIN_LEMMAS,
    POSSESSION_FEATURE_LEMMAS, POSSESSION_NEGATIVE_LEMMAS,
    POSSESSION_HIDDEN_LEMMAS, ALL_POSSESSION_LEMMAS,
    CREATION_PRODUCE_LEMMAS, CREATION_BUILD_LEMMAS, CREATION_CREATE_LEMMAS,
    CREATION_CRAFT_LEMMAS, CREATION_DESIGN_LEMMAS, CREATION_CAUSE_LEMMAS,
    CREATION_ENABLE_LEMMAS, ALL_CREATION_LEMMAS,
    PERCEPTION_SEE_LEMMAS, PERCEPTION_FIND_LEMMAS, PERCEPTION_REALIZE_LEMMAS,
    PERCEPTION_THINK_LEMMAS, PERCEPTION_KNOW_LEMMAS, PERCEPTION_REVEAL_LEMMAS,
    PERCEPTION_SAY_LEMMAS, PERCEPTION_SENSE_LEMMAS, ALL_PERCEPTION_LEMMAS,
    EQUATION_EQUAL_LEMMAS, EQUATION_MEAN_LEMMAS, EQUATION_REFER_LEMMAS,
    ALL_EQUATION_LEMMAS, EQUATION_PHRASAL_PARTICLES,
    COMPARISON_RESEMBLE_LEMMAS,
    RESULT_THAT_INTENSIFIERS, RESULT_THAN_INTENSIFIERS,
    RESULT_TO_INTENSIFIERS, ALL_RESULT_INTENSIFIERS,
    # --- weak / non-visual vocabulary (2.5) ---
    WEAK_VERB_LEMMAS, WEAK_VERB_FORMS, WEAK_ADJ_LEMMAS,
    # --- measurement words (2.6) ---
    MEASURE_NOUNS,
    # --- spatial prepositions (2.7) ---
    SPATIAL_LOCATIVE_PREPS, SPATIAL_DIRECTIONAL_PREPS, SPATIAL_TEMPORAL_PREPS,
    ALL_SPATIAL_PREPS,
    # --- fixed multi-word phrases (2.8) ---
    FROZEN_BIGRAMS, IDIOM_PHRASES, DISCOURSE_PIVOT_PHRASES,
    EXCEPTION_SINGLE_MARKERS, EXCEPTION_BIGRAMS,
    # --- sound effects (2.9) ---
    SFX_WORDS,
    # --- topic & era lexicons (2.10) ---
    GENERIC_TOPIC_NOUNS, ERA_STYLE_NOUNS,
    # --- regexes (2.13) ---
    PARAGRAPH_BREAK_RX, WHITESPACE_RUN_RX, PUNCT_ONLY_RX,
    # --- thresholds (2.14) ---
    MIN_LEAD_FOR_CLAUSE_SPLIT, MIN_LEAD_FOR_BUT_OR, MIN_LEAD_FOR_AND_CLAUSE,
    MIN_LEAD_FOR_ENTITY, LONG_PREP_SUBTREE_MIN, RUNON_SENT_MIN_TOKENS,
    RUNON_WINDOW, LONG_LEAD_TO_ROOT, SHORT_TAIL_TO_PUNCT, SHORT_SUBORD_CLAUSE,
    SHORT_SENT_NO_SPLIT, LONG_SUBORD_OPENER_TOKENS, LONG_COMMA_LEAD_CONTENT,
    LONG_COMMA_TAIL_CONTENT, INFINITIVE_SPLIT_SENT_MIN,
    INFINITIVE_SPLIT_LEAD_MIN, INFINITIVE_SPLIT_TAIL_MIN, OF_REVEAL_SENT_MIN,
    OF_REVEAL_LEAD_MIN, PROGRESSIVE_SENT_MIN, PROGRESSIVE_LEAD_MIN,
    COPULA_REVEAL_SENT_MIN, COPULA_REVEAL_CHUNK_MIN, PP_PRON_PART_SENT_MIN,
    PP_PRON_PART_LEAD_MIN, AUX_PP_REVEAL_LEAD_MIN, CHAINED_PART_SENT_MIN,
    DOBJ_DISQUAL_SENT_MIN, MIN_LEAD_FOR_DESCRIPTOR,
    RESULT_INTENSIFIER_LOOKBACK, SPATIAL_PREP_SUBTREE_MIN_NOUNS,
    SPATIAL_PREP_SENT_MIN_TOKENS, SPATIAL_PREP_LEAD_MIN, COMPARISON_SENT_MIN,
    COMPARISON_LEAD_MIN, EXCEPTION_SENT_MIN, EXCEPTION_LEAD_MIN,
    DISCOURSE_PIVOT_MIN_TAIL, AGENT_REVEAL_SENT_MIN, AGENT_REVEAL_LEAD_MIN,
    LIST_ITEM_MAX_TOKENS, LIST_MIN_TAGGED,
    # --- the shared spaCy model (SECTION 0) ---
    get_nlp_required,
    # --- named text checks (SECTION 4) ---
    is_ellipsis_text, is_only_punctuation,
    # --- named token checks (SECTION 5) ---
    verb_is_used_as_equation, verb_is_used_as_copula,
    verb_has_substantial_object, verb_has_substantial_complement,
    is_ordinal_token, is_inside_compound_named_entity,
    is_inside_hyphen_compound, is_inside_frozen_bigram,
    is_big_punctuation_split_point, is_inside_runon_sentence,
    noun_chunk_containing, tokens_to_next_punctuation, count_content_tokens,
    has_visualisable_content, find_idiom_spans,
)

# --- the old private names, kept as thin aliases so every rule below reads
# --- exactly as it did before the extraction (see shared_text_logic.py).
_nlp = get_nlp_required
_SPLIT_RULE_IDS = SPLIT_RULE_IDS
_LIST_RULE_IDS = LIST_RUN_RULE_IDS
_LIST_ITEM_MAX_TOKENS = LIST_ITEM_MAX_TOKENS
_LIST_MIN_TAGGED = LIST_MIN_TAGGED
_GENERIC_TOPIC_NOUNS = GENERIC_TOPIC_NOUNS
_ANAPHORIC_SUBJECT_PRONOUNS = ANAPHORIC_SUBJECT_PRONOUNS
_PARA_BREAK_RE = PARAGRAPH_BREAK_RX
_WS_RUN_RE = WHITESPACE_RUN_RX
PUNCT_ONLY_RE = PUNCT_ONLY_RX
BLOCKED_PREPS = NOUN_LIST_BLOCKED_PREPS
SPLITTABLE_PREPS = NOUN_LIST_SPLITTABLE_PREPS
APPROX_ADV = APPROXIMATOR_WORDS
APPROX_LEMMAS = APPROXIMATOR_WORDS
APOS = APOSTROPHES
EMPH_CHARS = MARKDOWN_EMPHASIS_CHARS
REL_TAGS = RELATIVE_PRONOUN_TAGS
_is_equation_use = verb_is_used_as_equation
_is_copular_use = verb_is_used_as_copula
_is_substantial_dobj = verb_has_substantial_object
_has_substantial_complement = verb_has_substantial_complement
_is_ordinal = is_ordinal_token
_matches_ellipsis = is_ellipsis_text
_in_compound_ne = is_inside_compound_named_entity
_in_hyphen_compound = is_inside_hyphen_compound
_is_frozen_bigram_split = is_inside_frozen_bigram
_is_big_punct_split = is_big_punctuation_split_point
_is_in_runon = is_inside_runon_sentence
_chunk_containing = noun_chunk_containing
_tokens_to_next_punct = tokens_to_next_punctuation
_content_count = count_content_tokens
_has_visualisable_content = has_visualisable_content
_idiom_spans = find_idiom_spans



# === VERSION marker — change me when shipping a new revision ===========
# The user can check this at runtime: `from sentence_splitter import VERSION`.
# If it doesn't match the version you expect, they're running stale code.
VERSION = "v18.5-2026-07-03"


 # === SINGLE-RUN DEBUG FLAG ================================================
# Set to True to always print stage-by-stage output when
# split_text_into_sections() is called.  Can also be enabled per-call
# via split_text_into_sections(text, debug=True).
SINGLE_RUN_DEBUG = False


# =============================================================================
# CHUNK — the unit that flows through the post-processing pipeline
# =============================================================================
# The pipeline used to pass plain lists of sentence strings around, e.g.
#     ["the big dog", "jumped over", "the fox"]
# It now passes an *ordered map* instead — a list (so order is preserved and
# duplicate texts are allowed) whose entries each map a phrase-line of text to
# a list of integer ids:
#     [Chunk("the big dog", []), Chunk("jumped over", []), Chunk("the fox", [])]
#
# Because ``Chunk`` is a NamedTuple it ALSO behaves like a plain (key, value)
# pair — you can unpack it (``text, ids = chunk``) or index it
# (``chunk[0]`` / ``chunk[1]``) — so the structure reads exactly as "an array,
# each item mapping a string to an int list".
#
# The ``ids`` list is a placeholder for per-line metadata and is ALWAYS EMPTY
# for now.  The invariants the rest of the code maintains:
#   • SPLITTING prose into more lines creates additional Chunks, each with its
#     own fresh (empty) ids list — never a shared/aliased list.
#   • MERGING two Chunks into one concatenates their ids lists — and that
#     concatenation happens in exactly one place: ``merge_chunks`` below.
class Chunk(NamedTuple):
    text: str          # the visible phrase-line (the map "key")
    ids: List[int]     # integer ids for this line (the map "value"); empty for now


# Type alias for the ordered map itself: a list of (text, ids) entries.
ChunkMap = List[Chunk]


def merge_chunks(a: Chunk, b: Chunk, sep: str = " ",
                 rule: Optional[int] = None) -> Chunk:
    """Merge two chunk-map entries into a single entry.

    • The texts are joined with *sep* (a single space by default; callers pass
      ``sep=""`` when gluing bare punctuation onto the previous line) and the
      result is stripped of surrounding whitespace.
    • The id-lists are combined LEFT-half-first, then RIGHT-half, and the
      *rule* id of the merge itself (if given) is inserted at the FRONT:

          merged.ids  =  [rule]  +  a.ids  +  b.ids

      This follows the project-wide convention that the most-recently-applied
      rule sits at the front of the list.  Pass `rule` as the merging-rule id
      (1000+) of the glue operation being performed; omit it (None) to just
      combine the two halves without recording a merge.

    This is the single choke-point through which all id-list merging flows, so
    any future smarter logic (dedup, re-mapping, …) only has to change here.
    """
    merged_ids = ([rule] if rule is not None else []) + list(a.ids) + list(b.ids)
    return Chunk((a.text + sep + b.text).strip(), merged_ids)


# RULE_DESCRIPTIONS + describe_rule() — the id → plain-English table.
# → moved to shared_text_logic.py, SECTION 1.1 (imported at the top of this file).


# _SPLIT_RULE_IDS — which rule stamps which id (aliased to SPLIT_RULE_IDS).
# → moved to shared_text_logic.py, SECTION 1.2 (imported at the top of this file).


# CONFIG — punctuation sets, spaCy tag/entity/dep sets, function words, measure nouns, frozen bigrams and every lead-in / clause-length threshold.
# → moved to shared_text_logic.py, SECTION 2.1-2.3 + 2.6 + 2.14 (imported at the top of this file).


# the spaCy model loader — ONE model is now shared with the tagger (_nlp = get_nlp_required).
# → moved to shared_text_logic.py, SECTION 0 (imported at the top of this file).


# =============================================================================
# UTILITY HELPERS
# =============================================================================

# verb-sense checks: is this verb an equation use / does it have a substantial object or complement?
# → moved to shared_text_logic.py, SECTION 5 (imported at the top of this file).

def _prev_split(splits: Set[int], i: int) -> int:
    """Largest split index strictly less than *i*."""
    return max((s for s in splits if s < i), default=0)

def _next_split(splits: Set[int], i: int, doc_len: int) -> int:
    """Smallest split index strictly greater than *i*."""
    return min((s for s in splits if s > i), default=doc_len)


# token & span checks: compound entities, hyphen compounds, ordinals, ellipses, big-punctuation splits, run-ons, frozen bigrams, noun chunks, distance to punctuation, content counts.
# → moved to shared_text_logic.py, SECTION 5 (imported at the top of this file).


# --- debug helpers ---------------------------------------------------------

def _splits_to_chunks_list(doc: Doc, splits: Set[int]) -> ChunkMap:
    """Build an ordered chunk-map from a splits set (for debug display).

    Each chunk derived straight from the splits is "new", so it gets its own
    fresh (empty) ids list."""
    idx = sorted(splits)
    chunks: ChunkMap = []
    for i in range(len(idx) - 1):
        text = doc[idx[i]:idx[i + 1]].text.strip()
        if text:
            chunks.append(Chunk(text, []))
    return chunks


def _format_chunks_debug(chunks: ChunkMap) -> str:
    """Format an ordered chunk-map with '|||' separators for debug display.

    Only the text half of each entry is shown — the ids are always empty for
    now, so printing them would just be noise."""
    if not chunks:
        return "[]"
    return '["' + '" ||| "'.join(c.text for c in chunks) + '"]'


def _debug_print_stage(name: str, was_applied: bool,
                       doc_or_chunks) -> None:
    """Print one line of debug output for a pipeline stage.

    *doc_or_chunks* is either a tuple ``(doc, splits)`` (for rule stages)
    or a ``ChunkMap`` of (text, ids) entries (for post-processing stages).
    """
    status = "TRUE" if was_applied else "FALSE"
    bang = " !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" if was_applied else ""
    if isinstance(doc_or_chunks, tuple):
        doc, splits = doc_or_chunks
        chunks = _splits_to_chunks_list(doc, splits)
    else:
        chunks = doc_or_chunks
    print(f"==> {name} ({status}){bang}")
    print(f"    {_format_chunks_debug(chunks)}")

# the verb MEANING FAMILIES (equation / result / spatial / perception / creation / possession / copula), the v18 lexicons (comparison, exception, discourse pivot, SFX) and the weak (non-visual) verb + adjective vocabulary.
# → moved to shared_text_logic.py, SECTION 2.4 + 2.5 (imported at the top of this file).


# has_visualisable_content() — is there anything in this span you could put on screen?
# → moved to shared_text_logic.py, SECTION 5 (imported at the top of this file).


# =============================================================================
# RULE FUNCTIONS  —  each returns a *set of token indices* where a split is
# desired.  "Split at i" means: the chunk boundary lies BEFORE doc[i].
# =============================================================================

# -----------------------------------------------------------------------------
# RULE 0 — strip markdown headings (preprocessing on raw text).
# Examples:  "# Title" → removed
#            "### Subsection" → removed
# -----------------------------------------------------------------------------
def rule_strip_markdown(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


# -----------------------------------------------------------------------------
# RULE 0.25 — NORMALISE WHITESPACE  (preprocessing)
# Scripts arrive hard-wrapped and sometimes indented.  If raw newlines reach
# spaCy they become WHITESPACE TOKENS inside the doc, which silently breaks
# the splitter three ways:
#   1. adjacency-based rules check doc[t.i+1] expecting the comma/noun/conj
#      to be NEXT — a '\n' token in between stops the rule firing (this is
#      how a clean 3-item list can come out mangled);
#   2. "non-punct token" counts include space tokens, inflating every
#      length threshold;
#   3. chunk text is emitted from the raw doc text, so '\n' and indentation
#      leak straight into the output JSON.
# Fix at the source: no whitespace run ever survives to the parser.
#
#   • a PARAGRAPH break (blank line) is a sentence break: if the text before
#     it doesn't already end in sentence punctuation, a full stop is added;
#   • every remaining whitespace run (newlines, tabs, multi-spaces) collapses
#     to a single space.
# -----------------------------------------------------------------------------
# the paragraph-break / whitespace-run regexes.
# → moved to shared_text_logic.py, SECTION 2.13 (imported at the top of this file).


def rule_normalise_whitespace(text: str) -> str:
    text = _PARA_BREAK_RE.sub(r"\1. ", text)   # unpunctuated paragraph ends
    return _WS_RUN_RE.sub(" ", text).strip()   # collapse all whitespace runs


# -----------------------------------------------------------------------------
# RULE 0.5 — NORMALISE PUNCTUATION  (preprocessing)
# Replaces "weird" Unicode punctuation variants with canonical forms AND
# ensures spaCy will tokenize them as separate tokens by adding whitespace
# around them when they're glued to alphanumeric characters.
#
# Why this matters:
#   • spaCy sometimes fuses an ellipsis with the preceding word into one
#     token ("Which..." → single token).  This kills the post-ellipsis
#     split because rule_ellipsis can't find a stand-alone ellipsis token.
#   • Stray "weird" quotes (single open `‘`, single close `’`) and dashes
#     can confuse the splitter.  We normalise them.
#
# Normalisations performed:
#   • U+2026 (horizontal ellipsis "…") → "..."
#   • Add a space after "..." or "…" when followed by an alphanumeric
#   • Add a space before "..." or "…" when preceded by an alphanumeric
#     and the dots are followed by whitespace or end-of-string (so we
#     don't tear "1.2.3").  Heuristic: only force a space when the run
#     of dots is ≥ 2 in length AND the surrounding context makes the
#     dot-run punctuation (not decimal point).
#   • Long-dash forcing: ensure em/en dashes have whitespace either side
#     when they sit between alphanumerics (so "yes—consistently" still
#     splits cleanly).  Single hyphen "-" is left alone to preserve
#     "self-driving" style compounds.
#
# All changes are conservative — we never change the SEMANTICS of the
# string, only the WHITESPACE around already-existing punctuation.
# -----------------------------------------------------------------------------
def rule_normalise_punct(text: str) -> str:
    # Canonicalise U+2026 ellipsis to three dots (so RULE 3 catches it
    # via the same regex path).  Keep already-existing "..." untouched.
    text = text.replace("\u2026", "...")
    # Ensure "..." has a space before it when glued to a preceding word.
    # Pattern: <alphanumeric>...<whitespace OR end> → "<alphanumeric>... "
    text = re.sub(r"(?<=[A-Za-z0-9])(\.{2,})(?=\s|$)",
                  lambda m: m.group(1), text)
    # Ensure "..." has a space after it when glued to a following word
    # AND the "..." starts at the beginning of the string OR after whitespace.
    # We CAN'T use a variable-width lookbehind here (Python's re module
    # rejects `(?<=\s|^)`), so we match the leading whitespace/start
    # explicitly as a capture group and re-insert it.
    text = re.sub(r"(^|\s)(\.{2,})(?=[A-Za-z0-9])",
                  lambda m: m.group(1) + m.group(2) + " ", text)
    # Mid-word "..." (alphanumeric on BOTH sides) → split on both sides
    # so "Which...sounds" becomes "Which ... sounds".  This handles the
    # case where spaCy fuses the ellipsis with adjacent text.
    text = re.sub(r"(?<=[A-Za-z0-9])(\.{2,})(?=[A-Za-z0-9])",
                  lambda m: + m.group(1), text)
    # Em-dash / en-dash / double-hyphen glued to alphanumerics on both
    # sides → ensure whitespace either side.
    for dash in ("—", "–", "--"):
        # alphanum + dash + alphanum  →  alphanum + " " + dash + " " + alphanum
        text = re.sub(
            r"(?<=[A-Za-z0-9])(" + re.escape(dash) + r")(?=[A-Za-z0-9])",
            lambda m: m.group(1), text,
        )
    # Smart-quote normalisation: replace curly singles ‘ ’ that are clearly
    # bracket-quotes (not apostrophes).  Heuristic: if a curly single
    # appears glued to whitespace on one side and an alphanumeric on the
    # other, AND it's the OUTER kind (open vs close), we don't touch
    # apostrophes embedded in words like "isn't".  Conservative: leave
    # curly singles inside words alone.  (Apostrophe detection is handled
    # by anti_rule_possessive downstream.)
    return text


# -----------------------------------------------------------------------------
# RULE 1 — HARD PUNCTUATION  (.  !  ?  ;  :)
# Always end a line at sentence-final punctuation.
# Examples:  "He left."        → "He left." +
#            "Wait! Stop!"     → "Wait!" / "Stop!"
#            "Look: a bird."   → "Look:" / "a bird."
# Note: comma is NOT a hard split (handled separately by RULE 7 / RULE 8).
# -----------------------------------------------------------------------------
def rule_hard_punct(doc: Doc) -> Set[int]:
    return {t.i + 1 for t in doc if t.text in HARD_PUNCT}


# -----------------------------------------------------------------------------
# RULE 2 — DASHES  (—  –  --  -)
# Split AFTER em/en/double-hyphen dashes and after spaCy-glued single hyphens
# like "another-" tokens.  Dashes attach to the PRECEDING text on their line.
#
# Examples:
#   "fold smaller — it changes the rules"
#       → split AFTER "—" → ['fold smaller —', 'it changes the rules']
#   "If the answer is yes — consistently — then it's brilliant."
#       → splits AFTER each em-dash, NOT before.  Preserves the natural
#         reading flow.
#   "and here is another- does it..."
#       → spaCy may emit "another-" as a single token; we detect a token
#         that ends in "-" with alphanumeric before AND a trailing space,
#         and split AFTER it.
#   "self-driving"
#       → NOT split (single hyphen with no whitespace either side).
# -----------------------------------------------------------------------------
def rule_dashes(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text not in DASH_PUNCT:
            # Detect spaCy-glued hyphen-suffix token: "another-" with
            # whitespace after.  Split AFTER such a token.
            if (len(t.text) > 1
                    and t.text.endswith("-")
                    and t.text[-2].isalnum()
                    and t.whitespace_):
                out.add(t.i + 1)
            continue
        # In-word single hyphen ("self-driving") — skip.
        if (t.text == "-"
                and t.i > 0
                and not t.whitespace_
                and not doc[t.i - 1].whitespace_):
            continue
        # Split AFTER the dash so dashes attach to the preceding chunk.
        out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 3 — ELLIPSES  (...  or  …)
# Ellipses are inherently dramatic — always split AFTER.
# Examples:  "Yep..."   → own line
#            "And..."   → own line
#            "Yet…"     → own line  (single-char ellipsis)
# -----------------------------------------------------------------------------
def rule_ellipsis(doc: Doc) -> Set[int]:
    return {t.i + 1 for t in doc if _matches_ellipsis(t.text)}


# -----------------------------------------------------------------------------
# RULE 4 — DRAMATIC PRE-ELLIPSIS REVEAL
# When an ellipsis follows a content phrase, give that phrase its own line.
#   "...best places on Earth to find whale fossils… is a desert."
#       → split BEFORE "whale fossils"
#   "...so flat… satellites use them..."
#       → split BEFORE "so flat"
# Strategy: find the closest content word (NOUN/ADJ/VERB) preceding the
# ellipsis, then walk back through any compound-NOUN or modifying-ADV chain
# to find the start of that small phrase.
# -----------------------------------------------------------------------------
def rule_pre_ellipsis_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if not _matches_ellipsis(t.text):
            continue
        # find content word in last few tokens
        # (skip VERBs — verbs at this position are usually predicates of the
        #  previous noun, not the dramatic-reveal target.)
        head_i = None
        for j in range(t.i - 1, max(t.i - 5, -1), -1):
            if doc[j].pos_ in {"NOUN", "PROPN", "ADJ"}:
                head_i = j
                break
        if head_i is None:
            continue
        # ABORT: if any ADV / PART / negation sits between the content word
        # and the ellipsis, this isn't a clean reveal — it's an interrupted
        # phrase like "stranded not physically…".  We don't fire here.
        for k in range(head_i + 1, t.i):
            if doc[k].pos_ in {"ADV", "PART"} or doc[k].lower_ in {"not", "no", "n't", "never"}:
                head_i = None
                break
        if head_i is None:
            continue
        # walk back through compound NOUNs and modifying ADVs to phrase start
        start = head_i
        while start > 0 and (
            doc[start - 1].pos_ == "ADV"
            or (doc[start - 1].pos_ in {"NOUN", "PROPN", "ADJ"}
                and doc[start - 1].dep_ in {"compound", "amod", "nmod"})
        ):
            start -= 1
        if start == 0:
            continue
        prev = _prev_split(splits | out, start)
        if start - prev < 2:
            continue
        out.add(start)
    return out


# -----------------------------------------------------------------------------
# RULE 5 — QUOTATION MARKS
# Quoted phrases always get their own line.
# Split BEFORE an opening quote, AFTER a closing quote.
# -----------------------------------------------------------------------------
def rule_quotes(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text in OPEN_QUOTES:
            out.add(t.i)              # split BEFORE opening quote
        if t.text in CLOSE_QUOTES:
            out.add(t.i + 1)          # split AFTER closing quote
    return out


# -----------------------------------------------------------------------------
# RULE 6 — BRACKETS  (parentheses, square, curly)
# Same logic as quotes — parenthetical asides get their own line.
# -----------------------------------------------------------------------------
def rule_brackets(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text in OPEN_BRACKETS:
            out.add(t.i)
        if t.text in CLOSE_BRACKETS:
            out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 7 — COMMA AFTER SENTENCE-INITIAL ADVERBIAL
# When a sentence opens with a substantial adverbial phrase that ends in a
# comma, give the adverbial its own line.
#   "Back in 1946, the technician..."        → "Back in 1946," / "the technician..."
#   "Two thousand years ago, the Romans..."  → "Two thousand years ago," / "the Romans..."
#   "In Egypt, there's a valley..."          → "In Egypt," / "there's a valley..."
#   "Then geology shifted, oceans..."        → "Then geology shifted," / "oceans..."
#
# Skipped when lead-up is just a single discourse word ("Anyway,", "Now,").
# -----------------------------------------------------------------------------
def rule_initial_adverbial_comma(doc: Doc) -> Set[int]:
    out = set()
    for sent in doc.sents:
        if len(sent) == 0:
            continue
        # CRITICAL: the first content token (skipping leading CCONJ "And"/"But"
        # /"So"/"Or") is what we check for discourse-marker status.  This
        # makes "And honestly, X" detect "honestly" as a discourse opener,
        # so we DON'T split after the comma.
        first_idx = 0
        while first_idx < len(sent) and sent[first_idx].pos_ in {"CCONJ", "INTJ"}:
            first_idx += 1
        if first_idx >= len(sent):
            continue
        first_tok = sent[first_idx]
        if first_tok.lower_ in DISCOURSE_INIT:
            continue
        for t in sent[: min(8, len(sent))]:
            if t.text != ",":
                continue
            lead = sent[: t.i - sent.start]
            has_substance = (len(lead) >= 2
                             or any(x.like_num
                                    or x.ent_type_ in {"DATE", "TIME", "GPE", "LOC", "PERSON"}
                                    or x.pos_ == "PROPN" for x in lead))
            # TIGHTENED: in shorter sentences (≤10 non-punct tokens), require
            # a clear "scene-setter" — the lead must contain a NUM, DATE/TIME/
            # GPE/LOC/PERSON entity, or PROPN.  This kills over-splitting of
            # short sentences like "And once that clicks, deserts start
            # getting weird very fast." while still firing on "Back in 1946,
            # the technician..." and "In Egypt, there's a valley...".
            sent_ntok = sum(1 for x in sent if not x.is_punct)
            if sent_ntok <= 10:
                strong_substance = any(
                    x.like_num
                    or x.ent_type_ in {"DATE", "TIME", "GPE", "LOC", "PERSON"}
                    or x.pos_ == "PROPN"
                    for x in lead
                )
                if not strong_substance:
                    break
            if has_substance and t.i + 1 < len(doc):
                out.add(t.i + 1)
            break       # only the FIRST comma in the sentence
    return out


# -----------------------------------------------------------------------------
# RULE 8 — COMMA SPLIT  (clausal coordination + comma-separated lists)
# Splits AFTER a comma in two distinct situations:
#
#   (a) CLAUSAL COORDINATION: comma preceded by a VERB.
#       "geology shifted, oceans retreated, land rose, climates changed."
#       → each comma starts a new line.
#
#   (b) NOUN LIST: comma preceded by a NOUN/PROPN, followed by NOUN/DET/ADJ/ADV/NUM,
#       with no verb in the next ~3 tokens.
#       "ribs, vertebrae, entire fossilized bodies..."
#       → comma after "ribs" and after "vertebrae" both split.
#
# Splits happen AFTER the comma so the comma stays attached to the previous word.
# -----------------------------------------------------------------------------
def rule_comma_split(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text != ",":
            continue
        if t.i + 1 >= len(doc) or t.i == 0:
            continue
        prev = doc[t.i - 1]
        nxt  = doc[t.i + 1]
        
        # (a) clausal: VERB,  + anything substantive → split
        if prev.pos_ == "VERB":
            out.add(t.i + 1)
            continue
            
        # (a') clausal-end via adverb/adjective whose HEAD is a verb in this sentence
        if prev.pos_ in {"ADV", "ADJ"} and prev.head.pos_ == "VERB" and prev.head.i < prev.i:
            out.add(t.i + 1)
            continue
            
        # (c) NOUN/PROPN + "," + ADV  — sequential/transition adverb.
        # "buried in sediment, | then eventually fossilized"
        # "drifted into areas, | later becoming..."
        # These adverbs introduce a new visual clause, so we always split.
        if prev.pos_ in {"NOUN", "PROPN"} and nxt.pos_ == "ADV":
            out.add(t.i + 1)
            continue
            
        # (b) noun list: NOUN, + (NOUN/DET/ADJ/ADV/NUM) with no early verb
        if prev.pos_ in {"NOUN", "PROPN"} and nxt.pos_ in {"NOUN", "PROPN", "DET", "ADJ", "ADV", "NUM"}:
            window_end = min(t.i + 4, len(doc))
            # Treat VBG/VBN as participles (not finite verbs) — they don't
            # block list flow.
            has_early_verb = any(
                doc[k].pos_ == "VERB" and doc[k].tag_ not in {"VBG", "VBN"}
                for k in range(t.i + 1, window_end)
            )
            if not has_early_verb:
                out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 9 — APPOSITIVE / RELATIVE COMMA
# When a comma precedes a wh-word starting a relative clause, split AFTER
# the comma so the relative clause begins a new line.
#   "...chanoyu tea, which would go on..."  → ["...chanoyu tea,", "which would..."]
# -----------------------------------------------------------------------------
def rule_appositive_comma(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text != "," or t.i + 1 >= len(doc):
            continue
        nxt = doc[t.i + 1]
        if nxt.tag_ in WH_TAGS:
            out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 10 — CLAUSE-STARTER WH-WORDS / SUBORDINATING CONJUNCTIONS
# Split BEFORE a wh-word (WDT/WP/WP$/WRB) or SCONJ when it begins a subordinate
# clause AND the lead-in is at least a few tokens AND its clause has substance.
# Generalises to who / whom / whose / what / which / where / when / why / how /
# while / as / because / since / though / if / unless / although / ...
#
# Frozen bigrams "what if", "as if" etc. stay intact via anti-rule J.
# Very short subordinate clauses (≤ SHORT_SUBORD_CLAUSE tokens to next punct)
# don't trigger a split — that would just orphan a tiny tail.
# -----------------------------------------------------------------------------
def rule_clause_starters(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        is_wh    = t.tag_ in WH_TAGS
        is_sconj = t.pos_ == "SCONJ"
        if not (is_wh or is_sconj):
            continue
        # CRITICAL: skip short sentences (≤8 non-punct tokens).
        # "But it's how it developed that I don't see mentioned" stays whole;
        # "I just don't know if the world is ready for it" stays whole;
        # "A tool is only useful if you actually use it" stays whole.
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok <= 8:
            continue
        if _is_frozen_bigram_split(doc, t.i):
            continue
        # Don't split before tightly-bound copular SCONJs ("like", "than", "as")
        # when the preceding token is a verb/aux/adj — these read as
        # "verb + complement" not as a new clause.
        # Examples: "looks like X", "feels like X", "more than X", "seems as X".
        if t.lower_ in {"like", "than", "as"} and t.i > 0:
            prev_tok = doc[t.i - 1]
            if prev_tok.pos_ in {"VERB", "AUX", "ADJ"}:
                continue
        # if the subordinate clause is very short, don't bother splitting
        if _tokens_to_next_punct(doc, t.i) <= SHORT_SUBORD_CLAUSE:
            continue
            
        # Check if there's a split immediately before this SCONJ.
        # If so, we want to move it AFTER the SCONJ so the SCONJ clings
        # backwards as a cliffhanger.
        split_right_before = (t.i in (splits | out))
        
        if split_right_before:
            if is_sconj:
                out.add(t.i + 1)
            # We rely on anti_rule_split_before_sconj to remove t.i
            continue
            
        prev = _prev_split(splits | out, t.i)
        if t.i - prev > MIN_LEAD_FOR_CLAUSE_SPLIT:
            if is_sconj:
                out.add(t.i + 1)
            else:
                out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 11 — COORDINATING "BUT" / "OR" / "SO" / "YET" WITH LONG LEAD-IN
# CCONJ "but" / "or" / "so" / "yet" between clauses gets a split before it
# when the lead-up is substantial.  ("and" handled separately by RULE 21.)
# v18: added "so" / "yet" — result and contrast pivots that were previously
# unhandled without a comma ("the dam broke so the valley flooded").  The
# POS gate keeps intensifier-"so" ("so flat" — ADV) and temporal-"yet"
# ("not yet" — ADV) out, and sentence-initial "So, ..." never has enough
# lead-in to fire.
# -----------------------------------------------------------------------------
def rule_but_or_coord(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "CCONJ" or t.lower_ not in {"but", "or", "so", "yet"}:
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev > MIN_LEAD_FOR_BUT_OR:
            out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 12 — VERB CLAUSE BOUNDARIES
# Split AFTER a verb whose clause has finished, BUT keep:
#   • short S-V-O patterns intact   ("kneaded the bread"  — verb + tiny dobj)
#   • short S-V-PP patterns intact  ("sat on a chair"     — verb + tiny prep)
#   • phrasal verbs intact          ("sped past", "laid down")
#   • verb modifiers intact         ("the running man"    — amod/acl/relcl/advcl)
#   • aux + main verb intact        (handled by anti-rule B)
#   • run-on suppression in long list-heavy passages
#   • verbs immediately before comma or hard punct (let other rules handle)
#   • verbs followed by short tail to next punct (e.g. "they do now.")
#   • verbs followed by infinitive ("used to be", "decided to leave")
#
# Examples:
#   "The fast cat sat on the comfortable mat"
#       → ["The fast cat sat", "on the comfortable mat"]
#       (sat is ROOT, prep complement fits, but sentence is short so runon
#        suppression DOESN'T fire — split happens.)
#   "The baker kneaded the bread while the fire crackled"
#       → "kneaded the bread" stays together (short dobj),
#         then RULE 10 splits before "while".
# -----------------------------------------------------------------------------
def rule_verb_clause(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "VERB":
            continue
            
        # CRITICAL: skip short sentences entirely.
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok <= 9:
            continue

        # NEW: Compound Subject Reveal
        # If the verb has a compound subject (contains "and"/"or"),
        # split BEFORE the verb to reveal the action separately.
        # E.g. "the person and the dragon | laid down together"
        # But for single subjects: "the dragon laid at the edge" (no split before verb)
        subj = next((c for c in t.children if c.dep_ in {"nsubj", "nsubjpass"}), None)
        if subj:
            is_compound_subj = any(x.pos_ == "CCONJ" for x in subj.subtree)
            if is_compound_subj:
                out.add(t.i)
                continue # We found our split, skip the rest of the checks for this verb

        if t.dep_ in VERB_MOD_DEPS:
            # EXCEPTION: a finite VERB heading a LONG relative clause /
            # advcl is a genuine clause boundary worth splitting after.
            # Fixes #49 ("...where the plateau abruptly collapses into
            # surrounding desert basins.") — the relcl subtree is ≥8 tokens
            # so we let it split.  Doesn't apply to amod/acl participles.
            is_long_subclause = (t.dep_ in {"relcl", "acl:relcl", "advcl"}
                                 and len(list(t.subtree)) >= 8)
            if not is_long_subclause:
                continue
        # phrasal-verb particle next → keep together
        if t.i + 1 < len(doc) and doc[t.i + 1].dep_ in PARTICLE_DEPS:
            continue
        # next token is a comma → let RULE 8 handle the comma boundary
        if t.i + 1 < len(doc) and doc[t.i + 1].text == ",":
            continue
        # next token is hard punct → don't add a redundant split
        if t.i + 1 < len(doc) and doc[t.i + 1].text in HARD_PUNCT:
            continue
        # next token is a wh-determiner ("makes that clearer", "knows what to do")
        # — keep the verb glued to its clausal complement object.
        if t.i + 1 < len(doc) and doc[t.i + 1].tag_ in {"WDT", "WP", "WP$"}:
            continue
        # "to <verb>" infinitive — keep verb attached to "to"
        if t.i > 0 and doc[t.i - 1].lower_ == "to" and doc[t.i - 1].dep_ == "aux":
            continue
        # verb immediately followed by "to <verb>" (infinitive complement)
        # e.g. "used to be", "decided to leave", "wants to go"
        if (t.i + 2 < len(doc)
                and doc[t.i + 1].lower_ == "to"
                and doc[t.i + 2].pos_ == "VERB"):
            continue
        # short direct object stays with verb — UNLESS the dobj subtree
        # contains a comparative marker ("than"/"more"/"less"/"fewer") OR
        # is a multi-modifier reveal NP (≥1 ADJ and ≥2 NOUN/PROPN tokens
        # in its subtree) in a sentence ≥ DOBJ_DISQUAL_SENT_MIN tokens.
        # Both signal "this is the visual reveal, please split before it".
        # Fixes #82 (calibration tools larger than cities), #123 (great
        # cycle paths), #87 (surfaces so level they become...).
        dobj = next((c for c in t.children if c.dep_ in {"dobj", "obj"}), None)
        if dobj and len(list(dobj.subtree)) <= 5 and (dobj.i - t.i) < 6:
            subtree_toks = list(dobj.subtree)
            has_comparative = any(x.lower_ in COMPARATIVE_MARKERS
                                  for x in subtree_toks)
            n_adj   = sum(1 for x in subtree_toks if x.pos_ == "ADJ")
            n_nouns = sum(1 for x in subtree_toks if x.pos_ in {"NOUN", "PROPN"})
            # Visual weight: clusters of ADJ/NOUN are heavy reveals, whether
            # it's "gigantic geometric surface" (2 ADJ + 1 NOUN) or 
            # "reference surfaces" (0 ADJ + 2 NOUN).
            visual_weight = n_adj + n_nouns
            is_reveal_np = (sent_ntok >= DOBJ_DISQUAL_SENT_MIN
                            and visual_weight >= 2)
            if not (has_comparative or is_reveal_np):
                continue
        # short COMPLEMENT (xcomp/attr/acomp/ccomp) stays with verb.
        # Catches "Which sounds impossible because..." — "sounds" should not
        # split off from its short attr complement "impossible".
        # Same disqualifier applies (comparative inside / multi-modifier reveal NP).
        comp = next((c for c in t.children
                     if c.dep_ in {"xcomp", "attr", "acomp", "ccomp"}), None)
        if comp and len(list(comp.subtree)) <= 5 and (comp.i - t.i) < 6:
            subtree_toks = list(comp.subtree)
            has_comparative = any(x.lower_ in COMPARATIVE_MARKERS
                                  for x in subtree_toks)
            n_adj   = sum(1 for x in subtree_toks if x.pos_ == "ADJ")
            n_nouns = sum(1 for x in subtree_toks if x.pos_ in {"NOUN", "PROPN"})
            # Visual weight: clusters of ADJ/NOUN are heavy reveals, whether
            # it's "gigantic geometric surface" (2 ADJ + 1 NOUN) or 
            # "reference surfaces" (0 ADJ + 2 NOUN).
            visual_weight = n_adj + n_nouns
            is_reveal_np = (sent_ntok >= DOBJ_DISQUAL_SENT_MIN
                            and visual_weight >= 2)
            if not (has_comparative or is_reveal_np):
                continue
        # IMPERATIVE / SUBJECTLESS verb + short PP stays together.
        # "Switch to a fresh steed" — verb has no nsubj, prep complement is
        # short, so we keep them together.  Doesn't apply to S-V-PP sentences
        # like "The fast cat sat on the comfortable mat" because "sat" has
        # an nsubj.
        has_subject = any(c.dep_ in {"nsubj", "nsubjpass", "csubj"} for c in t.children)
        if not has_subject:
            prep = next((c for c in t.children if c.dep_ in {"prep", "case"}), None)
            if prep and len(list(prep.subtree)) <= 5:
                continue
        # remainder until next punct is very short → orphan-tail check
        # ("the way they do now." — splitting after "do" would orphan "now.")
        if _tokens_to_next_punct(doc, t.i + 1) <= SHORT_TAIL_TO_PUNCT:
            continue
        # RUN-ON SUPPRESSION: in a long sentence with no nearby punctuation,
        # let list rules dominate.  Keep the verb attached to a short prep
        # complement so we don't over-fragment.
        if _is_in_runon(doc, t.i):
            prep = next((c for c in t.children if c.dep_ in {"prep", "case"}), None)
            if prep and len(list(prep.subtree)) <= 5:
                continue
        # otherwise split AFTER the verb (including ROOT verbs).
        out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 13 — LONG LEAD-IN TO ROOT VERB
# Safety net for run-on sentences with no punctuation: if the preamble before
# the main verb has gone on for many tokens with no break, force a split
# BEFORE the ROOT verb.
# -----------------------------------------------------------------------------
def rule_long_lead_in(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "VERB" or t.dep_ != "ROOT":
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev > LONG_LEAD_TO_ROOT:
            out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 14 — LONG PREPOSITIONAL PHRASES
# Only split AFTER a preposition when its dependency subtree is genuinely
# long (≥ LONG_PREP_SUBTREE_MIN tokens).  Short PPs like "in the box",
# "on the comfortable mat", "in the sand", "in Egypt" are kept intact.
# "of" is excluded entirely — it almost always wants to bind to its head NP.
#
# REFINEMENT: skip ADPs whose subtree contains a VERB or AUX — these are
# preposition-led relative clauses ("through regions that are now brutally
# dry") where splitting after the prep would amputate the descriptor tail.
# Letting RULE 22 (terminal descriptor) reveal the tail produces a far
# better break: "...scattered through regions that are now | brutally dry".
# -----------------------------------------------------------------------------
def rule_long_preps(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "ADP" or t.lower_ in PROMISCUOUS_PREPS:
            continue
        # CRITICAL: skip short sentences — never break a short sentence at
        # a preposition.  "Think about your most common journeys" stays whole.
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok <= 9:
            continue
        subtree = list(t.subtree)
        if len(subtree) < LONG_PREP_SUBTREE_MIN:
            continue
        # NEW: relative-clause guard — if subtree contains its own verb/aux
        # (e.g. "through regions that ARE now brutally dry"), don't break
        # after this prep.  Other rules will handle the descriptor reveal.
        if any(x.pos_ in {"VERB", "AUX"} for x in subtree):
            continue
        # TIGHTENED: require ≥3 nouns in subtree.  Short PPs with just one
        # noun and lots of modifiers (e.g. "under dust and rock", "during
        # one temporary version") aren't reveals — they're qualifiers.
        n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
        if n_nouns < 3:
            continue
        out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 15 — NOUN-PHRASE LISTS  (the canonical list-detector)
# When consecutive noun chunks appear with no intervening verb, treat them as
# a list and split between them.
#
# Skips:
#   • verb between → not a list
#   • hard-punct between → different sentences, not a list
#   • appositives ("the city, Rome")
#   • two NPs sharing the same dobj/obj head
#   • ordinal appositive ("John Ford the second")
#   • single short prep between (in / on / at)  →  "the lift in the skyscraper"
#   • "X and Y" where both X and Y are single NOUN tokens  → "dust and rock"
#   • frozen bigram inside ("what if X and Y")
#   • cuts that would slice a multi-token NE
#
# Examples:
#   "the red car the blue truck the green bike sped"  → splits between each
#   "John Ford the second"                            → kept together
#   "dust and rock"                                   → split
#   "endless dunes and unbearable heat"               → 2 lines (chunks > 1 tok)
# -----------------------------------------------------------------------------
def rule_noun_lists(doc: Doc) -> Set[int]:
    out = set()
    chunks = list(doc.noun_chunks)

    # BLOCKED_PREPS / SPLITTABLE_PREPS — which prepositions may be cut after in a list.
    # → moved to shared_text_logic.py, SECTION 2.3 (imported at the top of this file).

    for a, b in zip(chunks[:-1], chunks[1:]):
        between = doc[a.end:b.start]

        # NEW guard goes HERE — first thing in the loop:
        sent_ntok = sum(1 for x in b.root.sent if not x.is_punct)
        if sent_ntok <= 9:
            continue

        # 1) verb between → different clauses
        if any(t.pos_ == "VERB" for t in between):
            continue
        # 2) hard punctuation → let sentence rules handle
        if any(t.text in HARD_PUNCT for t in between):
            continue
        # 2b) pronoun fragments on the right side
        if len(b) == 1 and b[0].pos_ == "PRON":
            continue
        # 3) appositives
        if b.root.dep_ == "appos":
            continue
        # 5) ordinal: "Henry the Eighth"
        if b.text.lower().startswith("the ") and len(b) >= 2 and _is_ordinal(b[1]):
            continue

        # 6) prepositions – visual typography logic
        if len(between) == 1 and between[0].pos_ == "ADP":
            prep = between[0].lower_
            
            # If Chunk A is a lightweight opener (PRON), Chunk B is the
            # visual subject. We split to reveal it, unless it's strictly
            # "of" which binds too tightly. (e.g. "what about | the frog")
            if a.root.pos_ not in {"NOUN", "PROPN"}:
                if prep == "of":
                    continue
                # else: fall through and split
            else:
                # Chunk A is a substantial NP (NOUN/PROPN).
                # Check if Chunk B is a qualifier of Chunk A (same NP)
                # or a new clause/opener (different head).
                is_qualifier = False
                head = b.root
                for _ in range(4): # walk up max 4 levels in the tree
                    if head == a.root:
                        is_qualifier = True
                        break
                    if head.head == head: # reached the sentence root
                        break
                    head = head.head
                
                if is_qualifier:
                    continue # Keep "the lift in the skyscraper" together
                
                # If it's not structurally a qualifier, but prep is blocked 
                # or unknown, still don't split (safe fallback).
                if prep in BLOCKED_PREPS or prep not in SPLITTABLE_PREPS:
                    continue

        # 9) don't cut a named entity
        if _in_compound_ne(doc, b.start):
            continue

        out.add(b.start)
        # for coordinations, also mark the left side so it gets isolated.
        # The CCONJ ("and") will naturally cling backwards to the first item.
        # e.g. "...measuring" / "elevation and" / "distance from orbit."
        if len(between) == 1 and between[0].pos_ == "CCONJ":
            out.add(a.start)

    return out


# -----------------------------------------------------------------------------
# RULE 16 — BARE  NOUN-DET-NOUN  LISTS
# Catches stretches that spaCy's noun_chunks may miss:
# split BEFORE a determiner if the previous token is a NOUN/PROPN AND no verb
# has appeared since the last split AND no hard punct between.
#
# Example: "the wandering sailor the curious child the patient dog"
#       → split before each "the"
# -----------------------------------------------------------------------------
def rule_bare_noun_lists(doc: Doc, splits: Set[int]) -> Set[int]:
    DEBUG = False
    out = set()
    for i in range(1, len(doc) - 1):
        if doc[i].pos_ != "DET":
            continue
        if doc[i - 1].pos_ not in {"NOUN", "PROPN"}:
            continue
        if i + 1 < len(doc) and _is_ordinal(doc[i + 1]):
            continue
        if _in_compound_ne(doc, i):
            continue

        # NEW: title-appositive guard
        # Pattern: PROPN + "the" + Capitalised → "Alaric the Goth"
        if (doc[i - 1].pos_ == "PROPN"
                and doc[i].lower_ == "the"
                and i + 1 < len(doc)
                and doc[i + 1].text[:1].isupper()
                and doc[i + 1].pos_ in {"NOUN", "PROPN", "ADJ"}):
            if DEBUG:
                print(f"  [bare-list] SKIP at idx {i}: title appositive "
                      f"'{doc[i-1].text} {doc[i].text} {doc[i+1].text}'")
            continue

        prev = _prev_split(splits | out, i)
        if any(t.pos_ == "VERB" or t.text in HARD_PUNCT for t in doc[prev:i]):
            continue
        if DEBUG:
            print(f"  [bare-list] ADD split at idx {i}: "
                  f"'{doc[i-1].text} | {doc[i].text} {doc[i+1].text if i+1<len(doc) else ''}'")
        out.add(i)
    return out

# -----------------------------------------------------------------------------
# RULE 17 — LIST-CLOSING QUANTIFIERS
# Split BEFORE "all" / "both" / "each" / "every" when they immediately follow
# a noun — typical of summary-after-list constructions.
#
# TIGHTENED: requires sentence ≥ 10 non-punct tokens.  In shorter sentences,
# "every" / "all" after a noun is usually starting a qualifier NP, not
# closing a list.  Catches:
#   "She looks at the sky every evening."   (7 tokens)  → stays whole
#   "I see them all the time."              (6 tokens)  → stays whole
# -----------------------------------------------------------------------------
def rule_list_quantifiers(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.lower_ in {"all", "both", "each", "every"} and t.i > 0 \
                and doc[t.i - 1].pos_ in {"NOUN", "PROPN"}:
            sent_ntok = sum(1 for x in t.sent if not x.is_punct)
            if sent_ntok < 10:
                continue
            out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 18 — NAMED-ENTITY REVEAL
# Split BEFORE the noun chunk that contains a "reveal" entity (PERSON, GPE,
# ORG, DATE, MONEY, ...) when it's introduced after at least a couple of
# tokens of build-up.
#
# CRUCIAL refinements (catches all the common bad cases):
#   (i)   skip if the entity start is mid-entity (B/I tag)
#   (ii)  skip if the entity is single-token AND immediately preceded by ADP
#         ("in Egypt", "on Earth", "in Bolivia", "of India") — these are
#         qualifiers, not reveals
#   (iii) skip if the entity is single-token AND nested inside a larger
#         noun chunk ("the Sahara") — already-introduced topic, not a reveal
#   (iv)  skip if the entity is a compound modifier of the next noun
#         ("OpenAI carburettor", "Tesla driver") — adjective-like qualifier
#   (v)   skip appositives where the previous token is a NOUN/PROPN
#         ("the technician John Ford")
#   (vi)  skip single-token CARDINAL/QUANTITY/PERCENT/ORDINAL — usually
#         part of measurement phrases ("thousands of kilometers")
#   (vii) for multi-token entities, split BEFORE the noun chunk that contains
#         the entity (so "the Tethys Sea" splits at "the", not at "Tethys")
#
# Examples (all desired):
#   "...native to the Malabar Coast of India, specifically Kerala."
#       → split before "the Malabar Coast"  AND  before "Kerala"
#   "covered by the Tethys Sea"  → split before "the Tethys Sea"
#   "in Egypt"           → no split (PP-internal single-tok GPE)
#   "the Sahara"         → no split (single-tok GPE inside a larger NP)
#   "OpenAI carburettor" → no split (compound modifier)
# -----------------------------------------------------------------------------
def rule_entity_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    seen = set()
    for ent in doc.ents:
        if ent.label_ not in REVEAL_ENTS:
            continue
        ent_start = ent.start
        if ent_start == 0:
            continue
        # (i) must be at a B-tag boundary
        if doc[ent_start].ent_iob_ == "I":
            continue
        # (vi) skip plain-numeric single-tokens — they're usually measurements
        if len(ent) == 1 and ent.label_ in NUMERIC_NO_REVEAL:
            continue
        # (vi') NEW: skip single-token DATE / TIME / MONEY entities too.
        # A bare year ("1994"), time ("3pm"), or amount ("$100") at the
        # end of a sentence is almost always a qualifier, not a reveal.
        # Multi-token date reveals ("40 million years ago") still fire.
        if len(ent) == 1 and ent.label_ in {"DATE", "TIME", "MONEY"}:
            continue


        # (iv) compound modifier ("OpenAI carburettor")
        if len(ent) == 1 and doc[ent_start].dep_ == "compound":
            continue
        # find the noun chunk containing the entity (if any) — split before THAT
        chunk = _chunk_containing(doc, ent_start)
        split_at = chunk.start if chunk is not None else ent_start
        # If the chunk's leading token is a preposition like "around" / "over"
        # / "about" and the actual entity starts later, snap split_at to the
        # entity's true start.  Fixes "But around / 40 million years ago"
        # where the chunk may include "around".
        if chunk is not None and split_at < ent_start:
            leading = doc[split_at]
            if leading.pos_ == "ADP":
                split_at = ent_start
        if split_at == 0 or split_at in seen:
            continue
        seen.add(split_at)
        # (iii) single-token entity nested in a larger chunk ("the Sahara")
        if chunk is not None and len(ent) == 1 and len(chunk) > 1:
            continue
        # (ii) single-token entity preceded by ADP ("in Egypt")
        prev_tok = doc[split_at - 1] if split_at > 0 else None
        if len(ent) == 1 and prev_tok is not None and prev_tok.pos_ == "ADP":
            continue

        # (ii') Multi-token entity preceded by ADP and not in a long
        # sentence with strong build-up → qualifier PP, not reveal.
        # Covers DATE/TIME/MONEY/CARDINAL plus generic terminal entities
        # in short-to-medium sentences.
        sent_ntok_here = sum(1 for x in doc[split_at].sent if not x.is_punct)
        if prev_tok is not None and prev_tok.pos_ == "ADP":
            # Strict block: numeric/measure entities at any sentence length
            # under 12 tokens.
            if ent.label_ in NUMERIC_QUALIFIER_ENTS and sent_ntok_here < 12:
                continue
            # Broader block: ANY entity (including LOC/GPE/PERSON) in a
            # short sentence ≤ 10 tokens — these are terminal qualifiers,
            # not reveals.  Catches "to the left" if "the left" gets an
            # entity tag, "every evening", "since 1994", etc.
            if sent_ntok_here <= 10:
                continue

        # (ii'') Stronger ADP-precedence guard for short-to-medium sentences.
        # Any entity preceded by an ADP in a sentence ≤10 non-punct tokens
        # is a terminal qualifier PP, not a reveal.  Covers:
        #   "since 1994"     (DATE,    10 tokens)
        #   "for the worst case"  (entity-tagged "worst case", short sent)
        #   "to the left"    (LOC-ish entity, short sent)
        if prev_tok is not None and prev_tok.pos_ == "ADP" and sent_ntok_here <= 11:
            continue

        # (v) appositive — preceded by a NOUN/PROPN ("the technician John Ford")
        if prev_tok is not None and prev_tok.pos_ in {"NOUN", "PROPN"}:
            continue

        # (v') Terminal short qualifier PP — if the entity sits at the end
        # of a short-to-medium sentence (≤10 tokens) and is preceded by an
        # ADP whose head is a NOUN/VERB earlier in the sentence, treat as
        # qualifier not reveal.  Catches:
        #   "She looks at the sky every evening."   ("every evening" = DATE)
        #   "This dataset contains every transaction since 1994."
        #   "The plan lacks an exit strategy for the worst case."
        sent_ntok_here2 = sum(1 for x in doc[split_at].sent if not x.is_punct)
        if sent_ntok_here2 <= 10 and prev_tok is not None and prev_tok.pos_ == "ADP":
            continue

        # (vi'') Title-appositive guard: skip when the entity sits inside
        # a "PROPN + the + Capitalised" pattern — these are titles
        # attached to a name, not standalone reveals.
        # Catches: "Alaric the Goth", "Henry the Eighth", "Ivan the Terrible",
        # "William the Conqueror", "Catherine the Great".
        if split_at >= 2:
            two_back = doc[split_at - 2]
            one_back = doc[split_at - 1]
            if (two_back.pos_ == "PROPN"
                    and one_back.lower_ == "the"
                    and doc[split_at].text[:1].isupper()
                    and doc[split_at].pos_ in {"NOUN", "PROPN", "ADJ"}):
                continue

        # SPECIAL: if preceded by an introducer adverb ("specifically Kerala",
        # "namely Smith", "especially China"), always reveal — these adverbs
        # explicitly signal an upcoming named reveal.
        if prev_tok is not None and prev_tok.lower_ in ADV_INTRODUCERS:
            out.add(split_at)
            continue
        # (general) need at least MIN_LEAD_FOR_ENTITY tokens of build-up
        prev = _prev_split(splits | out, split_at)
        if split_at - prev < MIN_LEAD_FOR_ENTITY:
            continue
        out.add(split_at)
    return out


# -----------------------------------------------------------------------------
# RULE — NUMERIC INTRO REVEAL  (structural fallback for missed DATE entities)
# Splits BEFORE a numeric expression at the start of a sentence-initial
# adverbial when preceded by an ADP/ADV approximator and followed by a
# comma. Catches the "But around | 40 million years ago, ..." pattern that
# rule_entity_reveal misses when spaCy tags the span as CARDINAL instead
# of DATE/QUANTITY.
#
# Examples (fires):
#   "But around 40 million years ago, this region was..."
#       → "But around" | "40 million years ago, this region was..."
#   "Just over 100 species had been discovered, when the team..."
#       → "Just over" | "100 species had been discovered, when..."
#
# Examples (deliberately doesn't fire):
#   "But over 30 years have passed today."   — no adverbial comma
#   "Before 1946, scientists thought..."     — single-token lead < 2
#   "Around 5 dogs lay in the sun."          — sentence too short
#   "From the 19th century onwards, ..."     — prev_tok is DET, not ADP/ADV
# -----------------------------------------------------------------------------
def rule_numeric_intro_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if not t.like_num:
            continue
        # don't split mid-entity, UNLESS it's an approximator + number date combo
        if t.ent_iob_ == "I":
            prev_tok = doc[t.i - 1] # ensure prev_tok is defined before this check
            if not (prev_tok.ent_iob_ == "B" and prev_tok.pos_ in {"ADP", "ADV"}):
                continue
        # must be near sentence start (within first ~5 tokens)
        if t.i - t.sent.start > 5:
            continue
        # need at least one preceding token in this sentence
        if t.i == t.sent.start:
            continue
        prev_tok = doc[t.i - 1]
        # previous token should be ADP/ADV (the approximator)
        if prev_tok.pos_ not in {"ADP", "ADV"}:
            continue
        # sentence must be long enough — avoid splitting short sentences
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 12:
            continue
        # must be inside a sentence-initial adverbial: comma within next
        # ~6 tokens (stop searching at a hard-punct sentence boundary)
        has_following_comma = False
        for k in range(t.i + 1, min(t.i + 7, len(doc))):
            if doc[k].text == ",":
                has_following_comma = True
                break
            if doc[k].text in HARD_PUNCT:
                break
        if not has_following_comma:
            continue
        # need at least 2 tokens of lead
        prev = _prev_split(splits | out, t.i)
        if t.i - prev < 2:
            continue
        out.add(t.i)
    return out



# -----------------------------------------------------------------------------
# RULE 19 — CURRENCY-AMOUNT REVEAL
# Currency symbols followed by a number get their own dramatic intro.
# Examples:
#   "It costs $800,000"   → ["It costs", "$800,000"]
#   "Just £1,000"         → ["Just", "£1,000"]
# Anti-rule O ensures the inside of "$800,000" is never cut.
# -----------------------------------------------------------------------------
def rule_currency_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.text in CURRENCY_SYMS and t.i + 1 < len(doc) and doc[t.i + 1].like_num:
            prev = _prev_split(splits | out, t.i)
            if t.i - prev > 1:
                out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 20 — IMPERATIVE / SHORT COMMAND SENTENCES
# A sentence consisting of a verb at position 0 followed by ≤ 3 tokens often
# wants its own line.  Hard-punct already gives it that, but this rule also
# forces a break BEFORE such a sentence in case the previous chunk ran on.
# -----------------------------------------------------------------------------
def rule_imperative_start(doc: Doc) -> Set[int]:
    out = set()
    for sent in doc.sents:
        if len(sent) <= 4 and sent[0].pos_ == "VERB" and sent[0].dep_ == "ROOT":
            out.add(sent.start)
    return out


# -----------------------------------------------------------------------------
# RULE 21 — AND/OR BETWEEN INDEPENDENT CLAUSES
# CCONJ "and"/"or" splits BEFORE itself when followed by a clear new-clause
# starter (pronoun + verb, or "I/we/you/he/she/it/they" + AUX) and the
# lead-in is substantial.  Distinguishes list-and ("cat and dog") from
# clause-and ("he ran and she walked").
# -----------------------------------------------------------------------------
def rule_and_or_clause(doc: Doc, splits: Set[int]) -> Set[int]:
    """Split BEFORE a coordinating 'and'/'or' under any of these patterns:

    (1) Clausal coordination: 'and' followed by a clear new clause
        (PRON + verb, or AUX/VERB).  This is the original behaviour.

    (2) Parallel NPs at end-of-clause: 'and'/'or' followed by a noun chunk
        when the chunk before the conjunction was also a noun chunk and the
        conjunction sits within the last ~6 tokens of its sentence.
        Fixes #79 "ancient vertebrae or | skull fragments." and
        #18 "elevation | and distance from orbit.".

    (3) Parallel PPs / adverbials: 'and'/'or' followed by an ADP or ADV
        starting a new prepositional/adverbial phrase, and the previous
        chunk also started with an ADP/ADV.  Fixes #117
        "Quick detour at the next junction | and straight back home.".

    All variants gate on a substantial lead-in (≥ MIN_LEAD_FOR_AND_CLAUSE).
    All are structural — no hardcoded vocabulary.
    """
    out = set()
    chunks = list(doc.noun_chunks)
    for t in doc:
        if t.pos_ != "CCONJ" or t.lower_ not in {"and", "or"}:
            continue
        if t.i + 1 >= len(doc):
            continue
        nxt = doc[t.i + 1]
        prev = _prev_split(splits | out, t.i)
        if t.i - prev <= MIN_LEAD_FOR_AND_CLAUSE:
            continue

        # (1) classic clausal-and
        looks_clausal = (nxt.pos_ == "PRON"
                         or (nxt.pos_ in {"AUX", "VERB"} and nxt.dep_ != "amod"))
        if looks_clausal:
            out.add(t.i)
            continue

        # (2) parallel-NP near end of clause: previous chunk ends just
        # before this CCONJ, and next chunk begins right after.
        # Tightened: only fires in long sentences (≥12 content tokens) AND
        # the conjunction must be in the last 5 tokens.  Avoids over-firing
        # on short coordinations like "Quick detour at the next junction
        # and straight back home." or "the atmosphere is electric, and
        # everyone decides to move".
        sent = t.sent
        sent_ntok = sum(1 for x in sent if not x.is_punct)
        toks_after_in_sent = sum(1 for x in sent if x.i > t.i and not x.is_punct)
        if sent_ntok >= 12 and toks_after_in_sent <= 5:
            prev_chunk_ends_here = any(c.end == t.i for c in chunks)
            next_chunk_starts_here = any(c.start == t.i + 1 for c in chunks)
            if prev_chunk_ends_here and next_chunk_starts_here:
                # Don't fire across a comma — comma-separated NPs are
                # already a list, RULE 8 / RULE 25 handle them.
                if t.i > 0 and doc[t.i - 1].text == ",":
                    pass
                else:
                    out.add(t.i)
                    continue

        # (3) parallel-PP / parallel-adverbial: 'and' followed by an ADP/ADV
        # that begins a new PP, AND the immediate previous CHUNK started
        # with an ADP/ADV.  Walks back over the previous split to find what
        # that chunk opens with.  Tightened: requires ≥12 sentence tokens.
        if nxt.pos_ in {"ADP", "ADV"} and sent_ntok >= 12:
            # find start of previous chunk
            prev_chunk_start = prev
            if prev_chunk_start < len(doc) and prev_chunk_start < t.i:
                # skip past leading whitespace/punct
                k = prev_chunk_start
                while k < t.i and doc[k].is_punct:
                    k += 1
                if k < t.i and doc[k].pos_ in {"ADP", "ADV"}:
                    out.add(t.i)
                    continue
    return out


# -----------------------------------------------------------------------------
# RULE 22 — TERMINAL DESCRIPTOR REVEAL
# When a sentence ends with a "describe-the-noun" tail — typically a final
# adjective, adverbial intensifier, or short noun phrase that follows a
# copula or "are now" / "is just" / "look genuinely" — give that descriptor
# its own line.  This is the "reveal the visual quality" pattern that runs
# through the YouTube scripts:
#
#   "regions that are now | brutally dry."
#   "the fuel alone is just | crazy."
#   "stations there have gone years | without recording rainfall."
#   "Parts of the landscape look genuinely | Martian —"
#   "more consistent than surfaces | humans could realistically engineer..."
#
# Detection (all structural):
#   • a token T preceded by ADV ("now", "just", "genuinely", "really",
#     "completely") whose own dep is amod/acomp/attr/advmod and is in the
#     last few tokens of the clause
#   • OR a final ADJ whose head is a copular AUX/VERB earlier in the clause
#   • the lead-in must be substantial (≥ MIN_LEAD_FOR_DESCRIPTOR tokens)
# We split BEFORE the descriptor, so its descriptive impact lands solo.
#
# REFINEMENTS:
#   • Pattern A guard: skip if sentence has < 8 non-punct tokens.  Keeps
#     "The empire state building is really big." whole (5 content words).
#   • Pattern B loosened: also fires when sentence_ntok ≥ 9 AND the head is
#     a real VERB (not AUX) — catches "...looks | microscopic" where the
#     head-distance is only 2 but the sentence has plenty of build-up.
# -----------------------------------------------------------------------------
# MIN_LEAD_FOR_DESCRIPTOR.
# → moved to shared_text_logic.py, SECTION 2.14 (imported at the top of this file).

def rule_terminal_descriptor(doc: Doc, splits: Set[int]) -> Set[int]:
    """Split BEFORE a terminal descriptor that "reveals the visual quality".

    Now covers FIVE patterns (all structural — no hardcoded vocabulary):

    Pattern A — terminal ADJ/ADV with preceding intensifier ADV:
        "regions that are now | brutally dry"
        "the fuel alone is just | crazy"
        "Parts of the landscape look genuinely | Martian"

    Pattern B — terminal ADJ as acomp/attr of an earlier copular verb:
        "the fuel is | crazy"
        "the place feels | extraterrestrial"  (only with substantial gap)

    Pattern C — terminal NOUN preceded by "than" (comparative reveal):
        "calibration tools larger than | cities"
        "more consistent than | surfaces"

    Pattern D — final NP after "from / for / against" with a long lead-in
    (the prepositional reveal):
        "checking their measurements against | prehistoric deserts"
        "thousands of kilometers per hour through | space"

    Pattern E — final ADV/ADJ tail after "are / were / is / was" + something
    (the "are now X" pattern):
        "regions that are now | brutally dry"
        "places that are once | green"
    """
    out = set()
    for sent in doc.sents:
        last = None
        for t in reversed(list(sent)):
            if t.is_punct or t.is_space:
                continue
            last = t
            break
        if last is None:
            continue
        sent_ntok = sum(1 for x in sent if not x.is_punct)

        # Pattern A: final ADJ/ADV with preceding intensifier ADV.
        if last.pos_ in {"ADJ", "ADV"} and last.i > sent.start + 1:
            prev_tok = doc[last.i - 1]
            if prev_tok.pos_ == "ADV":
                if sent_ntok < 8:
                    continue
                start = prev_tok.i
                # NEW: only walk back through ADV/ADJ tokens that are
                # clearly DESCRIPTIVE modifiers (their head is the
                # terminal ADJ/ADV).  Stop walking back at adverbs whose
                # head is the predicate verb — those are temporal/sentence
                # adverbs ("now", "then", "always"), not descriptive
                # intensifiers ("brutally", "deeply", "scientifically").
                while start > sent.start and doc[start - 1].pos_ in {"ADV", "ADJ"}:
                    candidate = doc[start - 1]
                    if candidate.is_punct:
                        break
                    # is candidate's head the terminal descriptor itself
                    # (or another descriptor in the chain)?
                    if candidate.head.i < start - 1 or candidate.head.i > last.i:
                        break
                    start -= 1
                prev_split = _prev_split(splits | out, start)
                lead_len = start - prev_split
                # Standard threshold OR shorter threshold for copular
                # lead-in (verb/AUX + ADV chain before descriptor).
                # Allows "are now | brutally dry" to fire even when the
                # lead-in from previous split is small.
                lead_has_copula = any(
                    doc[k].pos_ in {"VERB", "AUX"}
                    and doc[k].lemma_.lower() in ALL_COPULA_LEMMAS
                    for k in range(prev_split, start)
                )
                threshold = 2 if lead_has_copula else MIN_LEAD_FOR_DESCRIPTOR
                if lead_len >= threshold:
                    out.add(start)
                continue

        # Pattern B: final ADJ tied to a copular AUX/VERB earlier in clause.
        # Fires when EITHER (a) the head is at least 3 tokens back, OR
        # (b) the sentence has ≥ 9 non-punct tokens AND the head is a real
        # VERB (not just an AUX) — catches "...crossing X looks | microscopic"
        # where the head distance is small but the sentence has build-up.
        if last.pos_ == "ADJ" and last.dep_ in {"acomp", "attr"} \
                and last.head.pos_ in {"VERB", "AUX"} and last.head.i < last.i:
            head_far  = (last.i - last.head.i >= 3)
            long_verb = (sent_ntok >= 9 and last.head.pos_ == "VERB")
            if head_far or long_verb:
                prev_split = _prev_split(splits | out, last.i)
                if last.i - prev_split >= MIN_LEAD_FOR_DESCRIPTOR:
                    out.add(last.i)
            continue

        # Pattern C: terminal NOUN preceded by "than" (comparative reveal)
        if last.pos_ in {"NOUN", "PROPN"} and last.i > sent.start:
            # walk back past trailing modifiers (compound nouns, det/adj)
            np_start = last.i
            while np_start > sent.start and doc[np_start - 1].pos_ in {"NOUN", "PROPN", "ADJ", "DET", "NUM"} \
                    and not doc[np_start - 1].is_punct:
                np_start -= 1
            if np_start > sent.start and doc[np_start - 1].lower_ == "than" \
                    and sent_ntok >= 10:
                prev_split = _prev_split(splits | out, np_start)
                if np_start - prev_split >= MIN_LEAD_FOR_DESCRIPTOR:
                    out.add(np_start)
                continue

    return out


# -----------------------------------------------------------------------------
# RULE 23 — ADJECTIVE-PHRASE REVEAL (mid-sentence "reveal a quality" splits)
# Splits BEFORE an adjective phrase that follows a relative pronoun + auxiliary
# pattern ("X that are / were / is / was Y").  The "Y" is the visual quality
# being revealed.
#
# Examples:
#   "regions that are now | brutally dry"
#   "places that are once | green"
#   "skeletons that vanished | millions of years ago"
#
# Detection: find pattern WDT/WP + AUX/VERB + (ADV) + ADJ/NUM/NOUN-phrase at
# clause end, with substantial lead-in before the WDT.
# -----------------------------------------------------------------------------
def rule_adjective_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        # find a relative pronoun
        if t.tag_ not in {"WDT", "WP", "WRB"}:
            continue
        # next must be aux/verb
        if t.i + 1 >= len(doc):
            continue
        nxt = doc[t.i + 1]
        if nxt.pos_ not in {"AUX", "VERB"}:
            continue
        # walk forward past aux/adv to find adj/noun reveal
        j = t.i + 2
        while j < len(doc) and doc[j].pos_ == "ADV":
            j += 1
        # j is candidate split point (before the adjective/intensifier)
        if j >= len(doc) or doc[j].is_punct:
            continue
        # need a real adj/noun terminus and not too far ahead
        if doc[j].pos_ not in {"ADJ", "ADV", "NOUN", "PROPN"}:
            continue
        # only fire if this is near sentence end (within 5 tokens)
        sent_end = t.sent.end
        if sent_end - j > 5:
            continue
        # split happens at j-1 (after the ADV chain) so "are now" stays
        # together and "brutally dry" reveals.  But if there are no ADVs
        # between aux and adj, we DON'T split (no reveal flavour).
        if j == t.i + 2:
            continue
        prev_split = _prev_split(splits | out, j - 1)
        # only reveal when there's been substantial build-up
        if (j - 1) - prev_split >= 5:
            out.add(j - 1)
    return out


# -----------------------------------------------------------------------------
# RULE 24 — TRAILING NUMERIC PHRASE REVEAL
# Split BEFORE a final numeric/quantity phrase that closes a clause:
#   "vanished | millions of years ago"
#   "underwater for | millions of years"
#   "scattered | 15 meters long"
# Detection: a clause-ending span of [NUM/QUANTITY] + NOUN preceded by a
# substantial lead-in ending in a verb/PP.
#
# TIGHTENED: requires sentence ≥ 10 non-punct tokens.  Without this guard,
# short sentences with terminal DATE/TIME entities ("She looks at the sky
# every evening.") over-split because spaCy tags "every evening" as DATE.
# -----------------------------------------------------------------------------
def rule_numeric_phrase_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for sent in doc.sents:
        sent_ntok = sum(1 for x in sent if not x.is_punct)
        if sent_ntok < 10:
            continue
        # walk forward to find a NUM at near-end
        for t in sent:
            if not t.like_num and t.ent_type_ not in {"CARDINAL", "QUANTITY", "DATE"}:
                continue
            if t.i == sent.start:
                continue
            # how far is this NUM from sentence end?
            tokens_after = sent.end - t.i - 1
            if tokens_after > 6:
                continue

            # Find the true start of this numeric phrase/chunk so we don't
            # split mid-entity (e.g. inside "the 19th century").
            split_at = t.i
            chunk = _chunk_containing(doc, t.i)
            if chunk is not None:
                split_at = chunk.start
            elif t.ent_iob_ in {"B", "I"}:
                k = t.i
                while k > sent.start and doc[k-1].ent_iob_ == "I" and doc[k-1].ent_type_ == t.ent_type_:
                    k -= 1
                split_at = k

            if split_at == 0 or split_at == sent.start:
                continue

            prev_tok = doc[split_at - 1] if split_at > 0 else None
            if prev_tok is None:
                continue
            if prev_tok.pos_ not in {"NOUN", "VERB", "ADJ", "PART", "ADP"}:
                continue
            prev = _prev_split(splits | out, split_at)
            if split_at - prev < 5:
                continue
            out.add(split_at)
            break
    return out


# Add new helper rule
def rule_numeric_approximator_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    """
    Splits BEFORE a numeric+noun phrase when preceded by an approximator
    ADV ("nearly", "almost", "about", "roughly", "approximately", "over",
    "around", "just", "only") + lead-in.

    Examples:
      "lingers for nearly | ten seconds"
      "took almost | a hundred years"
      "spans over | three continents"
    """
    DEBUG = False
    # APPROX_ADV → APPROXIMATOR_WORDS.
    # → moved to shared_text_logic.py, SECTION 2.3 (imported at the top of this file).
    out = set()
    for t in doc:
        if t.lower_ not in APPROX_ADV:
            continue
        if t.pos_ not in {"ADV", "ADP"}:
            continue
        # Next token must be a numeric (like_num or NUM pos) or DET+NUM
        j = t.i + 1
        if j >= len(doc):
            continue
        if not (doc[j].like_num or doc[j].pos_ == "NUM"
                or (doc[j].pos_ == "DET" and j + 1 < len(doc)
                    and (doc[j + 1].like_num or doc[j + 1].pos_ == "NUM"))):
            if DEBUG: print(f"  [approx-num] SKIP at idx {t.i}: next not numeric")
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 8:
            continue
        # Lead
        split_at = j
        if split_at == 0:
            continue
        prev = _prev_split(splits | out, split_at)
        lead = _content_count(doc, prev, split_at)
        if lead < 1:
            if DEBUG: print(f"  [approx-num] SKIP at idx {split_at}: lead too short")
            continue
        if DEBUG:
            print(f"  [approx-num] ADD split at idx {split_at}: "
                  f"'{t.text} {doc[j].text}...'")
        out.add(split_at)
    return out

# -----------------------------------------------------------------------------
# RULE 25 — COMMA LIST EXTENSION
# Extends RULE 8 to cover three additional comma patterns that the canonical
# clausal-or-noun split misses:
#
#   (a) NOUN/PRON + "," + VERB  (participle-list element)
#       "dodging parked cars, squeezed by traffic, soaked in the rain..."
#       → each comma starts a new participle phrase.  RULE 8 doesn't fire
#         here because the LEFT of the comma is a NOUN, but the RIGHT is a
#         VERB (a present/past participle), so the existing noun-list branch
#         (which requires a noun-y RHS) can't trigger.
#
#   (b) any content + "," + CCONJ  (list-final coordinator)
#       "trains, cycling, and hoping nothing goes wrong"
#       "the atmosphere is electric, and everyone decides to move"
#       → split AFTER the comma so the trailing CCONJ phrase reads alone.
#         RULE 8 also misses this because the RHS is CCONJ, not noun-y.
#
#   (c) ADJ + "," + ADJ  (adjective series)
#       "wide, flat, ancient surfaces"
#       → split between successive adjectives in a series.
#
# All three are structural (POS-only), no vocabulary.
# -----------------------------------------------------------------------------
def rule_comma_list_extension(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text != ",":
            continue
        if t.i + 1 >= len(doc) or t.i == 0:
            continue
        prev = doc[t.i - 1]
        nxt  = doc[t.i + 1]
        # (a) NOUN/PRON + "," + VERB  — participle-list element.
        # Restricted to VBG/VBN tagged verbs: TRUE participles ("dodging",
        # "squeezed", "soaked").  Finite VBD verbs ("became", "fossilized")
        # after a comma are clause continuations not list items, so we
        # don't split there.  Fixes #43 vs preserves #141.
        if (prev.pos_ in {"NOUN", "PROPN", "PRON"}
                and nxt.pos_ == "VERB"
                and nxt.tag_ in {"VBG", "VBN"}):
            out.add(t.i + 1)
            continue
        # (b) any content + "," + CCONJ  — list-final coord
        if (prev.pos_ in {"NOUN", "PROPN", "ADJ", "ADV", "VERB"}
                and nxt.pos_ == "CCONJ"):
            out.add(t.i + 1)
            continue
        # (c) ADJ + "," + ADJ  — adjective series.
        # Only fires for 3+ chained adjectives ("wide, flat, ancient
        # surfaces"), NOT for simple 2-ADJ pairs ("giant, terrifying
        # birds" or "sheer, unclimbable cliffs") which are tight
        # descriptor stacks belonging to the same noun.
        if prev.pos_ == "ADJ" and nxt.pos_ == "ADJ":
            # Look ahead: must be ADJ + comma + ADJ + (comma or "and") + ADJ
            # to qualify as a real series.  Otherwise it's a pair, keep
            # together.
            is_chain = False
            k = t.i + 2  # skip past current comma and next ADJ
            if k < len(doc):
                if doc[k].text == "," and k + 1 < len(doc) and doc[k + 1].pos_ == "ADJ":
                    is_chain = True
                elif doc[k].lower_ == "and" and k + 1 < len(doc) and doc[k + 1].pos_ == "ADJ":
                    is_chain = True
            if is_chain:
                out.add(t.i + 1)
            continue
    return out


# -----------------------------------------------------------------------------
# RULE 26 — LONG SUBORDINATE-CLAUSE OPENER WITH CLOSING COMMA
# When a sentence opens with an SCONJ or wh-word that leads a substantial
# subordinate clause (≥ LONG_SUBORD_OPENER_TOKENS content tokens, contains a
# verb), and that clause closes with a comma, split AFTER the closing comma.
#
# Examples:
#   "If you already know a landscape is almost perfectly level,"
#         + " | you can compare satellite readings against it..."
#   "Because the site contained huge numbers of skeletons,"
#         + " | scientists have debated..."
#   "When entire landscapes reflect cleanly for kilometers,"
#         + " | you're looking at a level of flatness..."
#
# RULE 7 already covers single-comma adverbial openers ("Back in 1946,").
# This rule covers the LONGER subordinate-clause variant where RULE 7
# under-shoots because it only inspects the first 8 tokens.  Skips leading
# CCONJ/INTJ ("And"/"But"/"So"/"Or") so "And if you already know X, Y" still
# triggers cleanly.
# -----------------------------------------------------------------------------
def rule_long_subord_comma(doc: Doc) -> Set[int]:
    out = set()
    for sent in doc.sents:
        if len(sent) < 8:
            continue
        # skip leading CCONJ/INTJ
        first_idx = 0
        while first_idx < len(sent) and sent[first_idx].pos_ in {"CCONJ", "INTJ"}:
            first_idx += 1
        if first_idx >= len(sent):
            continue
        first_tok = sent[first_idx]
        # only triggers on SCONJ or wh-word openers
        if first_tok.pos_ != "SCONJ" and first_tok.tag_ not in WH_TAGS:
            continue
        # find the FIRST comma in this sentence with enough lead and a verb
        for t in sent:
            if t.text != ",":
                continue
            tokens_before = sum(1 for x in sent
                                if x.i < t.i and not x.is_punct)
            if tokens_before < LONG_SUBORD_OPENER_TOKENS:
                # don't break — there may be a later, longer-led comma
                continue
            has_verb = any(x.pos_ in {"VERB", "AUX"}
                           for x in sent if x.i < t.i)
            if not has_verb:
                continue
            if t.i + 1 < len(doc):
                out.add(t.i + 1)
            break       # only the first qualifying comma
    return out


# -----------------------------------------------------------------------------
# RULE 27 — TERMINAL COORDINATED ADJECTIVE PAIR
# When a sentence ends with a coordinated adjective pair after a copular
# verb + intensifier ADV chain, split BEFORE the first ADJ AND BEFORE the
# CCONJ — so each adjective in the pair gets its own line.
#
# Pattern (sentence-final):
#   VERB/AUX  +  ADV+  +  ADJ  +  CCONJ  +  ADJ
#
# Example:
#   "these landscapes feel simultaneously calming and alien."
#         → ["these landscapes feel simultaneously",
#            "calming",
#            "and alien."]
#   "the design is genuinely powerful but flawed."
#         → ["the design is genuinely",
#            "powerful",
#            "but flawed."]
#
# We accept VBG/VBN-tagged verbs as adj-like (e.g. "stunning and gripping",
# "burnt and forgotten") because they often function as predicate adjectives.
# -----------------------------------------------------------------------------
def rule_terminal_adj_coord(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for sent in doc.sents:
        sent_ntok = sum(1 for x in sent if not x.is_punct)
        if sent_ntok < 7:
            continue
        # find last non-punct token
        last = None
        for t in reversed(list(sent)):
            if t.is_punct or t.is_space:
                continue
            last = t
            break
        if last is None:
            continue
        # Accept ADJ, participle-adjective (VBG/VBN), OR NOUN as the
        # terminal descriptor.  spaCy small model often misparses
        # adjectives as NOUN ("alien", "panic", "scary") — if it sits
        # in coordinator position after an ADJ/VBN/VBG, treat it as
        # the adjective it almost certainly is.
        is_descriptor_like = (last.pos_ in {"ADJ", "NOUN"}
                               or last.tag_ in {"VBG", "VBN"})
        if not is_descriptor_like:
            continue
        # walk back: expect CCONJ before last
        if last.i - 1 < sent.start:
            continue
        cconj = doc[last.i - 1]
        if cconj.pos_ != "CCONJ":
            continue
        # before CCONJ: ADJ or VBG/VBN verb
        if cconj.i - 1 < sent.start:
            continue
        first_adj = doc[cconj.i - 1]
        first_adj_is_descriptor = (first_adj.pos_ in {"ADJ", "NOUN"}
                                    or first_adj.tag_ in {"VBG", "VBN"})
        if not first_adj_is_descriptor:
            continue
        # before first_adj: at least one ADV
        if first_adj.i - 1 < sent.start:
            continue
        adv = doc[first_adj.i - 1]
        if adv.pos_ != "ADV":
            continue
        # walk back through any ADV chain
        adv_start = adv.i
        while adv_start > sent.start and doc[adv_start - 1].pos_ == "ADV":
            adv_start -= 1
        # before ADVs: VERB or AUX
        if adv_start - 1 < sent.start:
            continue
        verb = doc[adv_start - 1]
        if verb.pos_ not in {"VERB", "AUX"}:
            continue
        # split BEFORE first_adj and BEFORE cconj
        prev1 = _prev_split(splits | out, first_adj.i)
        if first_adj.i - prev1 >= 2:
            out.add(first_adj.i)
        out.add(cconj.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 28 — PP-INTRODUCTORY REVEAL
# Splits AFTER a preposition whose subtree contains ≥ 2 NOUNs and NO VERB
# or AUX — i.e. a "fat" prepositional phrase whose object is a non-trivial
# noun sequence rather than a relative clause.  Reads like an introduction
# to a visual reveal.
#
# Examples:
#   "Straight roads vanishing into | heat haze for absurd distances."
#   "Nature accidentally produced calibration tools larger than | cities."
#   "scientists need known reference surfaces to calibrate instruments
#       measuring elevation and distance from | orbit."
#
# Why exclude "of"?  "of" almost always wants to bind to its head NP
# ("hours of trains", "the shape of the spine") — splitting after it would
# orphan a tiny qualifier.
#
# Why exclude subtrees containing a VERB/AUX?  Those are relative-clause
# preps ("through regions that are now brutally dry") — RULE 22 (terminal
# descriptor) handles those better.
# -----------------------------------------------------------------------------
def rule_pp_intro_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "ADP" or t.lower_ in PROMISCUOUS_PREPS:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        # Require longer sentences (≥10 content tokens) to avoid over-
        # splitting medium sentences like #92 "...live during one temporary
        # version of the map." where the PP-tail is a qualifier.
        if sent_ntok < 10:
            continue
        subtree = list(t.subtree)
        n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
        has_verb = any(x.pos_ in {"VERB", "AUX"} for x in subtree)
        # Require ≥3 nouns in subtree — short qualifier PPs ("of the map")
        # don't qualify.  Real reveals are PPs with multiple nouns.
        if n_nouns < 3 or has_verb:
            continue
        # require ≥ 3 lead tokens with NO HARD_PUNCT in the lead
        prev = _prev_split(splits | out, t.i + 1)
        lead_tokens = doc[prev:t.i + 1]
        n_lead = sum(1 for x in lead_tokens if not x.is_punct)
        if n_lead < 3:
            continue
        if any(x.text in HARD_PUNCT for x in lead_tokens):
            continue
        # require the lead to contain a VERB
        if not any(x.pos_ in {"VERB", "AUX"} for x in lead_tokens):
            continue
        out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 29 — PARTICIPLE REVEAL
# After a present- or past-participle verb (VBG/VBN), split BEFORE the next
# multi-token noun chunk that the participle introduces.  This is the
# "reveal what's being acted on" pattern.
#
# Examples:
#   "People crossing | empty landscapes suddenly noticing..."
#   "Bodies drifted into | coastal areas..."
#   "Cycling sounds great until you're | dodging parked cars..."
#
# Skipped when:
#   • sentence is short (< 9 non-punct tokens)
#   • the next noun-chunk is single-token (probably part of the same idiom)
#   • the participle is more than 3 tokens away from the chunk
#   • there's a HARD_PUNCT between them
#   • it would slice a multi-token NE
# -----------------------------------------------------------------------------
def rule_participle_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    chunks = list(doc.noun_chunks)
    for t in doc:
        # restrict to tokens spaCy tags as gerund / past-participle and
        # which actually function as a verb (avoids amod adjectives).
        if t.tag_ not in {"VBG", "VBN"}:
            continue
        if t.pos_ not in {"VERB", "AUX"}:
            continue
        # NEW: also skip when the participle is functioning as an adjective
        # modifier (amod) — even if spaCy tagged it pos=VERB.  These are
        # not introducing a new visual reveal; they're modifying a noun.
        # Catches "a strange recurring dream", "a missing piece",
        # "the burning question", etc.
        if t.dep_ in {"amod", "compound"}:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 9:
            continue
        # find next noun chunk after the participle
        nxt_chunk: Optional[Span] = None
        for nc in chunks:
            if nc.start > t.i:
                nxt_chunk = nc
                break
        if nxt_chunk is None:
            continue
        if len(nxt_chunk) < 1:
            continue
        if nxt_chunk.start - t.i > 3:
            continue
        # don't cross a hard punct
        if any(x.text in HARD_PUNCT for x in doc[t.i:nxt_chunk.start]):
            continue
        if _in_compound_ne(doc, nxt_chunk.start):
            continue
        prev = _prev_split(splits | out, nxt_chunk.start)
        if nxt_chunk.start - prev < 2:
            continue
        out.add(nxt_chunk.start)
    return out


# -----------------------------------------------------------------------------
# RULE 30 — POST-ENTITY LOCATION SPLIT
# After a multi-token named entity, split BEFORE a following preposition.
# This separates the entity (the "where") from a qualifier prepositional
# phrase that often follows ("in Oregon", "in Egypt", "of India").
#
# Examples:
#   "A lone person walking across Alvord Desert | in Oregon..."
#   "Wadi Al-Hitan | in Egypt — literally..."
#   "The Atacama Desert | in Chile..."
#
# We require:
#   • the entity is multi-token (single-token entities are not "the reveal")
#   • the next token is a preposition (ADP)
#   • we're not slicing another compound NE
#   • the sentence has substantial length and there's enough lead-in
# -----------------------------------------------------------------------------
def rule_post_entity_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for ent in doc.ents:
        if ent.label_ not in LOCATION_ENTS:
            continue
        if len(ent) < 2:
            continue
        end = ent.end
        if end >= len(doc):
            continue
        nxt = doc[end]
        if nxt.pos_ != "ADP":
            continue
        if _in_compound_ne(doc, end):
            continue
        sent_ntok = sum(1 for x in nxt.sent if not x.is_punct)
        if sent_ntok < 8:
            continue
        prev = _prev_split(splits | out, end)
        if end - prev < 3:
            continue
        out.add(end)
    return out


# -----------------------------------------------------------------------------
# RULE 31 — GENERIC LONG-CLAUSE COMMA SPLIT
# Splits AFTER any comma where the lead from the previous split contains
# ≥ LONG_COMMA_LEAD_CONTENT content tokens AND the tail until the next
# punctuation contains ≥ LONG_COMMA_TAIL_CONTENT content tokens.
#
# This is the catch-all "many words before a comma" rule.  RULE 7 only
# inspects the first 8 tokens of a sentence; RULE 26 only fires for
# SCONJ/wh-led openers.  This catches everything else:
#   #64 "Alvord's fascinating because unlike Uyuni's bright reflective salt
#        surface, | it's this dry pale playa..."
#   #19 "If you already know a landscape is almost perfectly level, | you
#        can compare..." (also caught by RULE 26)
#
# Skipped when the comma is inside a quoted span or bracket (handled by
# anti-rules H/I downstream).
# -----------------------------------------------------------------------------
def rule_long_clause_comma(doc: Doc, splits: Set[int]) -> Set[int]:
    DEBUG = False
    out = set()
    for t in doc:
        if t.text != ",":
            continue
        if t.i + 1 >= len(doc):
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 14:
            continue

        # ADJ-pair guard — only suppress when the ADJ pair forms a tight
        # descriptor stack sharing a noun head ahead.  Patterns like
        # "too X, too Y" or "X, Y" describing an earlier subject DO split.
        if t.i > 0 and t.i + 1 < len(doc):
            prev_tok = doc[t.i - 1]
            next_tok = doc[t.i + 1]
            if prev_tok.pos_ == "ADJ" and next_tok.pos_ == "ADJ":
                # Check: does the NEXT adj have a NOUN head within ~4 tokens?
                # If yes → tight descriptor stack ("giant, terrifying birds")
                # If no → parallel ADJ phrases describing earlier subject
                #         ("too reflective, too chaotic")
                has_shared_noun = False
                for k in range(next_tok.i + 1, min(next_tok.i + 5, len(doc))):
                    if doc[k].pos_ in {"NOUN", "PROPN"}:
                        has_shared_noun = True
                        break
                    if doc[k].text in HARD_PUNCT:
                        break
                # Also check: is the prev ADJ preceded by an intensifier ADV
                # like "too"/"so"/"very"?  Intensified ADJ pairs are
                # parallel structures, not stacks.
                prev_has_intensifier = (prev_tok.i > 0
                    and doc[prev_tok.i - 1].pos_ == "ADV"
                    and doc[prev_tok.i - 1].lower_ in
                    {"too", "so", "very", "really", "extremely",
                     "incredibly", "totally", "completely"})
                if has_shared_noun and not prev_has_intensifier:
                    if DEBUG:
                        print(f"  [long-comma] SKIP at idx {t.i}: ADJ-pair "
                              f"'{prev_tok.text}, {next_tok.text}' (shared NP)")
                    continue
                if DEBUG:
                    print(f"  [long-comma] ADJ-pair {prev_tok.text}, "
                          f"{next_tok.text}: shared_noun={has_shared_noun}, "
                          f"intensifier={prev_has_intensifier} → ALLOW SPLIT")

        prev = _prev_split(splits | out, t.i + 1)
        lead_content = _content_count(doc, prev, t.i + 1)
        if lead_content < LONG_COMMA_LEAD_CONTENT:
            continue
        if any(doc[k].text in HARD_PUNCT for k in range(prev, t.i)):
            continue
        sent_end = t.sent.end
        tail_end = t.i + 1
        while tail_end < sent_end and doc[tail_end].text not in HARD_PUNCT:
            tail_end += 1
        tail_content = _content_count(doc, t.i + 1, tail_end)
        if tail_content < LONG_COMMA_TAIL_CONTENT:
            continue
        if DEBUG:
            print(f"  [long-comma] ADD split at idx {t.i + 1}: "
                  f"lead={lead_content}, tail={tail_content}")
        out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 32 — INFINITIVE SPLIT
# Splits BEFORE a `to + VERB` infinitive in a long sentence when the
# lead-in is substantial AND the tail is substantial.  Fixes #18
# "...known reference surfaces | to calibrate instruments measuring..."
# -----------------------------------------------------------------------------
def rule_infinitive_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.lower_ != "to":
            continue
        if t.dep_ != "aux":
            continue
        if t.i + 1 >= len(doc):
            continue
        if doc[t.i + 1].pos_ != "VERB":
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < INFINITIVE_SPLIT_SENT_MIN:
            continue
        prev = _prev_split(splits | out, t.i)
        lead_content = _content_count(doc, prev, t.i)
        if lead_content < INFINITIVE_SPLIT_LEAD_MIN:
            continue
        # tail
        sent_end = t.sent.end
        tail_end = t.i
        while tail_end < sent_end and doc[tail_end].text not in HARD_PUNCT:
            tail_end += 1
        tail_content = _content_count(doc, t.i, tail_end)
        if tail_content < INFINITIVE_SPLIT_TAIL_MIN:
            continue
        # skip if directly preceded by a verb/AUX that explicitly takes the
        # infinitive ("want to", "need to", "have to", "going to")
        if t.i > 0:
            prev_tok = doc[t.i - 1]
            if (prev_tok.lower_, t.lower_) in FROZEN_BIGRAMS:
                continue
            # NEXT pair check too
            if t.i + 1 < len(doc):
                # already handled by FROZEN_BIGRAMS — fine
                pass
        out.add(t.i + 1)  # Split AFTER "to" so it clings to the previous line
    return out


# -----------------------------------------------------------------------------
# RULE 33 — TERMINAL `OF` REVEAL
# Splits AFTER `of` ONLY when the `of`-headed NP is the LAST chunk in the
# sentence AND the lead before `of` is ≥ OF_REVEAL_LEAD_MIN content tokens.
# Fixes #69 "...trace the shape of | the spine through the desert surface."
#
# `of` is normally in PROMISCUOUS_PREPS (binds tightly).  This is the
# narrow carve-out for sentence-final reveals — short PPs like "of India"
# still won't fire because the lead threshold blocks them.
# -----------------------------------------------------------------------------
def rule_terminal_of_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    DEBUG = False
    out = set()
    for t in doc:
        if t.lower_ != "of" or t.pos_ != "ADP":
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < OF_REVEAL_SENT_MIN:
            if DEBUG: print(f"  [of-reveal] SKIP at idx {t.i}: sent too short ({sent_ntok})")
            continue
        subtree = list(t.subtree)
        if not subtree:
            continue
        subtree_end = max(x.i for x in subtree)
        sent_end_i = max(x.i for x in t.sent if not x.is_punct)

        # NEW: detect list pattern in the FORWARD scan from "of" instead
        # of relying on subtree.  Look forward from "of" within its
        # sentence for the pattern NOUN/PROPN + comma + ... + and/or.
        # If found, this is "of X, Y, and Z" — a list reveal.
        scan_end = min(t.i + 15, t.sent.end)
        n_commas_fwd = 0
        has_list_conj_fwd = False
        has_hard_punct = False
        for k in range(t.i + 1, scan_end):
            if doc[k].text in HARD_PUNCT:
                has_hard_punct = True
                break
            if doc[k].text == ",":
                n_commas_fwd += 1
            if doc[k].lower_ in {"and", "or"} and doc[k].pos_ == "CCONJ":
                has_list_conj_fwd = True
        is_forward_list = (n_commas_fwd >= 2) or (n_commas_fwd >= 1 and has_list_conj_fwd)

        if DEBUG:
            print(f"  [of-reveal] idx {t.i}: subtree_end={subtree_end}, "
                  f"sent_end={sent_end_i}, "
                  f"fwd_commas={n_commas_fwd}, fwd_and={has_list_conj_fwd}, "
                  f"is_list={is_forward_list}")

        # Subtree-end check (existing): UNLESS we detected a forward list,
        # in which case skip this check since spaCy may have attached the
        # list members elsewhere in the tree.
        if not is_forward_list:
            if subtree_end < sent_end_i - 2:
                if DEBUG: print(f"    SKIP: subtree doesn't reach sent end")
                continue

        n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
        if n_nouns < 2 and not is_forward_list:
            if DEBUG: print(f"    SKIP: only {n_nouns} nouns in subtree")
            continue
        if any(x.pos_ in {"VERB", "AUX"} for x in subtree):
            if DEBUG: print(f"    SKIP: subtree contains verb")
            continue

        prev = _prev_split(splits | out, t.i + 1)
        lead = _content_count(doc, prev, t.i + 1)

        # Use existing list-detection from subtree as secondary check
        n_commas = sum(1 for x in subtree if x.text == ",")
        has_list_conj = any(x.lower_ in {"and", "or"} and x.pos_ == "CCONJ"
                            for x in subtree)
        is_subtree_list = (n_commas >= 2) or (n_commas >= 1 and has_list_conj)
        is_list = is_forward_list or is_subtree_list

        threshold = 1 if is_list else OF_REVEAL_LEAD_MIN
        if DEBUG:
            print(f"    lead={lead}, threshold={threshold} (is_list={is_list})")
        if lead < threshold:
            if DEBUG: print(f"    SKIP: lead too short")
            continue

        if DEBUG:
            print(f"    ADD split at idx {t.i + 1} (after 'of')")
        out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 34 — PROGRESSIVE-FORM SPLIT (split BEFORE `be + Ving`)
# When an AUX (`'re`, `was`, `is`, `are`) is immediately followed by a VBG
# verb in a long sentence, split BEFORE the VBG so the action gets its own
# reveal line.
#
# Fixes #141: "Cycling sounds great until you're | dodging parked cars..."
# Also helps #1 (lighthouse): "the sun, the tide... | surrounded them..."
#
# Distinguish from passive `be + VBN` (e.g. "was covered") — that's a
# qualifier, not an action reveal.  We only fire on VBG (gerund/present
# participle in progressive form).
# -----------------------------------------------------------------------------
def rule_progressive_split(doc: Doc, splits: Set[int]) -> Set[int]:
    """Splits BEFORE a VBG in a `be + Ving` progressive construction.

    TIGHTENED in v2: requires sentence ≥12 tokens (was 10) AND lead ≥5
    content tokens (was 3).  Short progressives like "...keeps happening
    in environments..." or "I was looking..." stay whole — only long
    sentences with substantial build-up justify the action-reveal split.
    """
    out = set()
    for t in doc:
        if t.tag_ != "VBG":
            continue
        if t.i == 0:
            continue
        prev_tok = doc[t.i - 1]
        if prev_tok.pos_ != "AUX":
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 12:
            continue
        prev = _prev_split(splits | out, t.i)
        lead = _content_count(doc, prev, t.i)
        if lead < 5:
            continue
        out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 35 — COPULA-ATTRIBUTE REVEAL
# Splits BEFORE a multi-token attribute / acomp NP that follows a copula
# (AUX `be` or stative VERB), when the NP has ≥1 modifier and the sentence
# is long enough.
#
# Fixes #23 "What remains is | this gigantic geometric surface where..."
# and #79 "...that turn out to be | ancient vertebrae or skull fragments."
#
# Detection (all structural):
#   • copular AUX/VERB whose attr/acomp child is a NOUN/PROPN chunk
#   • chunk contains ≥1 ADJ or ≥2 NOUN tokens (multi-modifier reveal)
#   • chunk size ≥ COPULA_REVEAL_CHUNK_MIN
#   • lead-in ≥ MIN_LEAD_FOR_ENTITY tokens, no HARD_PUNCT in lead
# -----------------------------------------------------------------------------
def rule_copula_attr_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    
    # COPULAR_LEMMAS / COPULAR_FORMS — verbs that link a subject to a visual predicate.
    # → moved to shared_text_logic.py, SECTION 2.4 (imported at the top of this file).
    
    for chunk in doc.noun_chunks:
        # 1. Is the chunk heavy enough to be a visual reveal on its own?
        n_adj = sum(1 for x in chunk if x.pos_ == "ADJ")
        n_nouns = sum(1 for x in chunk if x.pos_ in {"NOUN", "PROPN"})
        # Count VBG/VBN as visual payload as well (e.g., "cheaper than driving")
        n_visual_verbs = sum(1 for x in chunk if x.tag_ in {"VBG", "VBN"})
        is_comparative = any(x.lower_ == "than" for x in chunk)
        
        visual_weight = n_adj + n_nouns + n_visual_verbs
        
        is_heavy = (len(chunk) >= 4) or visual_weight >= 2 or is_comparative
        if not is_heavy:
            continue
            
        # 2. Find the head verb of this chunk (walk up the tree max 3 steps)
        head_verb = None
        head = chunk.root
        for _ in range(3):
            if head.pos_ in {"VERB", "AUX"}:
                head_verb = head
                break
            if head.head == head: # reached root
                break
            head = head.head
            
        if head_verb is None:
            continue
            
        # 3. Is the head verb a copula/linking verb?
        is_copula = (head_verb.lemma_.lower() in COPULAR_LEMMAS or 
                     head_verb.text.lower() in COPULAR_FORMS)
        if not is_copula:
            continue
            
        # 4. Does the chunk come AFTER the verb? 
        if chunk.start <= head_verb.i:
            continue
            
        # 5. Is there enough lead-up, or is the visual payload heavy enough?
        split_at = chunk.start
        if split_at <= 0:
            continue
            
        prev = _prev_split(splits | out, split_at)
        
        lead_content = split_at - prev  

        if lead_content < 2 and visual_weight < 3:
            continue
            
        if any(doc[k].text in HARD_PUNCT for k in range(prev, split_at)):
            continue
            
        out.add(split_at)
        
    return out


# -----------------------------------------------------------------------------
# RULE 36 — PRONOUN-PARTICIPLE PP REVEAL
# Splits AFTER an ADP whose object is a PRONOUN immediately followed by a
# participle or past-tense verb acting as a participle (`me sat`, `him standing`,
# `her crossing`). The pronoun + verb phrase is the visual reveal.
#
# Fixes #113 "...full circle back to | me sat on the M6."
# -----------------------------------------------------------------------------
def rule_pron_participle_pp_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    """Splits AFTER an ADP whose object is `PRON + participle/verb (VBN/VBG/VBD)`.

    The PRON+verb phrase acts as a reduced relative clause that serves as
    the visual reveal. Splits AFTER the ADP so the phrase including
    the pronoun lands on its own line:

        "...full circle back to | me sat on the M6."
        "...waiting for | him standing there..."

    Now includes VBD (past tense) to catch colloquial/dialectal structures
    where a past tense verb functions as a participle ("me sat", "him stood").
    """
    out = set()
    for t in doc:
        if t.pos_ != "ADP":
            continue
        if t.i + 2 >= len(doc):
            continue
        pron = doc[t.i + 1]
        part = doc[t.i + 2]
        if pron.pos_ != "PRON":
            continue
        # Allow VBN, VBG, and VBD (colloquial "me sat", "him stood")
        if part.tag_ not in {"VBN", "VBG", "VBD"}:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < PP_PRON_PART_SENT_MIN:
            continue
        prev = _prev_split(splits | out, t.i + 1)
        lead = _content_count(doc, prev, t.i + 1)
        if lead < PP_PRON_PART_LEAD_MIN:
            continue
        # Split AFTER the ADP so the PRON+verb phrase starts a new reveal line
        out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 37 — TERMINAL PP-AFTER-COPULA REVEAL
# When a sentence ends with `[AUX/VERB] + [ADV*] + [ADJ] + ADP + NP`,
# split BEFORE the final ADP so the descriptor reveals on its own line.
#
# Fixes #87 "...surfaces so level they become | scientifically valuable
# from orbit." — wait, actually the user wants "scientifically valuable
# from orbit" together and the split BEFORE "scientifically".  But
# subsequent runs handled by Pattern A.  This rule handles the simpler
# case of a final PP after a copular ADJ:
#   "...is brilliant | from above."
# -----------------------------------------------------------------------------
def rule_terminal_pp_after_copula(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for sent in doc.sents:
        sent_ntok = sum(1 for x in sent if not x.is_punct)
        if sent_ntok < 9:
            continue
        # find last non-punct token
        last = None
        for t in reversed(list(sent)):
            if t.is_punct or t.is_space:
                continue
            last = t
            break
        if last is None or last.pos_ not in {"NOUN", "PROPN"}:
            continue
        # walk back to find ADP head of last
        head = last.head
        if head is last or head.pos_ != "ADP":
            continue
        adp = head
        # before ADP must be ADJ or VBN/VBG (the descriptor we DON'T split)
        if adp.i - 1 < sent.start:
            continue
        before_adp = doc[adp.i - 1]
        if before_adp.pos_ not in {"ADJ", "VERB"}:
            continue
        # before that, an AUX/VERB chain must exist (the copula)
        # we don't strictly require it for this terminal-PP variant; the
        # adj/ADP/NP combo is enough.  Just require a substantial lead.
        prev = _prev_split(splits | out, adp.i)
        lead_content = _content_count(doc, prev, adp.i)
        if lead_content < MIN_LEAD_FOR_DESCRIPTOR:
            continue
        out.add(adp.i)
    return out


# -----------------------------------------------------------------------------
# RULE 38 — PHRASAL-VERB OBJECT REVEAL
# When a sentence contains a phrasal verb (VERB + PRT) followed by a
# multi-token noun chunk in a long-enough sentence, split BEFORE the
# direct-object noun chunk.
#
# Fixes #4 "the green bike sped past | the house"
# -----------------------------------------------------------------------------
def rule_phrasal_object_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    chunks = list(doc.noun_chunks)
    for t in doc:
        if t.pos_ != "VERB":
            continue
        # look for a particle child
        prt = next((c for c in t.children if c.dep_ in PARTICLE_DEPS), None)
        if prt is None:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 7:
            continue
        # find next chunk strictly after the particle
        nxt_chunk: Optional[Span] = None
        for nc in chunks:
            if nc.start > prt.i:
                nxt_chunk = nc
                break
        if nxt_chunk is None:
            continue
        # require multi-token chunk and reasonable proximity
        if len(nxt_chunk) < 2:
            continue
        if nxt_chunk.start - prt.i > 2:
            continue
        # don't slice an entity
        if _in_compound_ne(doc, nxt_chunk.start):
            continue
        # don't cross hard punct
        if any(doc[k].text in HARD_PUNCT for k in range(prt.i, nxt_chunk.start)):
            continue
        prev = _prev_split(splits | out, nxt_chunk.start)
        if nxt_chunk.start - prev < 2:
            continue
        out.add(nxt_chunk.start)
    return out

# -----------------------------------------------------------------------------
# RULE 39 — PREPOSITIONAL OBJECT REVEAL
# -----------------------------------------------------------------------------
def rule_prep_object_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    DEBUG = False
    out = set()
    for t in doc:
        if t.pos_ != "ADP":
            continue

        # Don't split if next non-DET token is ADJ — UNLESS the ADJ is
        # intensified ("by too X"), which makes it a standalone phrase.
        j = t.i + 1
        while j < len(doc) and doc[j].pos_ == "DET":
            j += 1
        if j < len(doc) and doc[j].pos_ == "ADJ":
            # Skip the skip if the ADJ has an intensifier behind it
            is_intensified = (j > 0 and doc[j - 1].pos_ == "ADV"
                              and doc[j - 1].lower_ in
                              {"too", "so", "very", "really", "extremely"})
            if not is_intensified:
                if DEBUG:
                    print(f"  [prep-obj] SKIP at idx {t.i}: prep '{t.text}' "
                          f"followed by ADJ '{doc[j].text}'")
                continue

        # NEW: skip when this PP qualifies a dobj of a possession verb
        head = t.head
        if head.dep_ in {"dobj", "obj"}:
            verb_head = head.head
            if (verb_head.pos_ in {"VERB", "AUX"}
                    and verb_head.lemma_.lower() in ALL_POSSESSION_LEMMAS
                    and verb_head.dep_ not in {"aux", "auxpass"}):
                continue

            
        # COMPARATIVE REVEAL:
        # If the ADP is "than" and its head is an ADJ/ADV, split AFTER "than"
        if t.lower_ == "than" and t.head.pos_ in {"ADJ", "ADV"}:
            out.add(t.i + 1)
            continue
            
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
            
        is_visual_prep = False
        is_terminal_pp = False
        
        if t.head.pos_ in {"VERB", "AUX", "ADV"}:
            is_visual_prep = True
        elif t.head.pos_ == "ADJ":
            if t.head.head.pos_ in {"VERB", "AUX"} or t.head.dep_ in {"acomp", "attr"}:
                is_visual_prep = True
        
        # TERMINAL NOUN REVEAL LOGIC:
        pobj = next((c for c in t.children if c.dep_ in {"pobj", "obj"}), None)
        # NEW: skip when pobj is a bare PRON (single-token, no modifiers)
        if pobj is not None and pobj.pos_ == "PRON":
            pobj_subtree = list(pobj.subtree)
            if len(pobj_subtree) == 1:
                if DEBUG:
                    print(f"  [prep-obj] SKIP at idx {t.i}: pobj is bare PRON '{pobj.text}'")
                continue
        if pobj is not None:
            last_noun_i = max((tt.i for tt in t.sent if tt.pos_ in {"NOUN", "PROPN", "NUM"}), default=-1)
            if pobj.i == last_noun_i:
                is_terminal_pp = True
                is_visual_prep = True
                
        # NOUN-headed PPs with substantial visual objects
        if not is_visual_prep and t.head.pos_ in {"NOUN", "PROPN"}:
            if pobj is not None:
                pobj_subtree = list(pobj.subtree)
                n_nouns_pobj = sum(1 for x in pobj_subtree if x.pos_ in {"NOUN", "PROPN"})
                if n_nouns_pobj >= 2 and sent_ntok >= 12:
                    is_visual_prep = True
                    
        if not is_visual_prep:
            continue
            
        if pobj is None:
            continue
            
        # Is the object a multi-token noun chunk?
        chunk = _chunk_containing(doc, pobj.i)
        if chunk is None:
            continue
            
        # Allow single-token nouns ONLY if they are the terminal reveal
        if len(chunk) < 2 and not is_terminal_pp:
            continue
            
        # Sentence length check (short sentences don't need this split)
        # EXCEPTION: Allow terminal numeric/temporal reveals even in short sentences
        # e.g. "Built in Manhattan in the 19th century."
        pobj_subtree = list(pobj.subtree)
        has_numeric = any(
            x.like_num
            or x.ent_type_ in {"DATE", "TIME", "CARDINAL",
                               "QUANTITY", "PERCENT", "MONEY"}
            for x in pobj_subtree
        )

        # Don't fire when pobj is a bare PRON (single-token, no modifiers)
        # — pronouns aren't visual reveals.
        if pobj is not None and pobj.pos_ == "PRON":
            pobj_subtree = list(pobj.subtree)
            if len(pobj_subtree) == 1:
                if DEBUG:
                    print(f"  [prep-obj] SKIP at idx {t.i}: pobj is bare PRON '{pobj.text}'")
                continue

        # Sentence-length gating, tightened:
        #   • Numeric terminal: ≥11 tokens (was 9)
        #   • Default:          ≥12 tokens (was 11)
        # Keeps medium-short sentences from over-splitting on terminal
        # qualifier PPs like "from at least four species", "for the
        # worst case", "since 1994", "to the left".
        if is_terminal_pp and has_numeric:
            if sent_ntok < 11:
                continue
        else:
            if sent_ntok < 12:
                continue
            
        # Don't split if it's a blocked prep like "of"
        if t.lower_ in PROMISCUOUS_PREPS:
            continue


        # LOCATION ENTITY REVEAL:
        pobj_ent_type = doc[pobj.i].ent_type_
        if pobj_ent_type in {"GPE", "LOC", "FAC", "ORG", "PERSON"}:
            out.add(t.i + 1) # Split AFTER the ADP
        elif is_terminal_pp:
            # Split BEFORE the terminal noun CHUNK to avoid slicing mid-entity.
            # e.g. "in the 19th century" -> split before "the 19th century"
            split_idx = chunk.start if chunk else pobj.i
            out.add(split_idx)
        else:
            # For NOUN-headed PPs that introduce a substantial visual NP,
            # split AFTER the ADP so the ADP acts as a cliffhanger.
            if t.head.pos_ in {"NOUN", "PROPN"}:
                out.add(t.i + 1)
            else:
                out.add(t.i) # Split BEFORE the ADP
            
    return out

# -----------------------------------------------------------------------------
# RULE 40 — TRANSITION ADVERB REVEAL
# Splits BEFORE a transition adverb (then, later, suddenly, etc.) when it
# introduces a new visual clause/shot.
#
# Avoids breaking internal verb phrases ("will then leave") by requiring a
# substantial lead-in and skipping adverbs immediately following an AUX.
#
# Examples:
#   "drifted into areas then eventually fossilized" → "drifted into areas | then eventually fossilized"
#   "He will then leave"                           → NO SPLIT (short lead, follows AUX)
# -----------------------------------------------------------------------------
def rule_transition_adverb(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "ADV" or t.lower_ not in TRANSITION_ADVERBS:
            continue
        if t.i == 0:
            continue
            
        # Don't split if it's an adjective modifying a noun ("the then president")
        if t.dep_ == "amod":
            continue
            
        # Don't split right after an auxiliary verb ("will then", "was suddenly")
        if t.i > 0 and doc[t.i - 1].pos_ == "AUX":
            continue
            
        # Short sentences don't need this split
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok <= 8:
            continue
            
        # Require a substantial lead-in (so we don't split "He | then left")
        prev = _prev_split(splits | out, t.i)
        if t.i - prev < MIN_LEAD_FOR_CLAUSE_SPLIT:
            continue
            
        out.add(t.i)
    return out

# -----------------------------------------------------------------------------
# RULE 41 — SCONJ HANGING CONNECTOR
# -----------------------------------------------------------------------------
def rule_sconj_hang(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "SCONJ":
            continue
        # Skip short sentences, UNLESS it's a comparative "than"
        # which introduces a visual payload ("cheaper than driving").
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        is_comparative_than = (t.lower_ == "than" and t.head.pos_ in {"ADJ", "ADV"})
        if sent_ntok <= 8 and not is_comparative_than:
            continue
        # Skip if next token is ADP (e.g., "because of", "instead of")
        if t.i + 1 < len(doc) and doc[t.i + 1].pos_ == "ADP":
            continue
        # Add split AFTER the SCONJ
        out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 42 — COPULA REVEAL  (Family 1: "X is/looks/becomes Y" disclosure split)
#
# Splits at a copular/linking verb when the right side carries a substantive
# REVEAL.  Eight patterns total (split position depends on which fires):
#
#   (a)  is + DET + NOUN         → "is | the quiet just before sunrise"
#   (a') is + NOUN/PROPN         → "became | a doctor"        (non-be only)
#   (b)  is + WH + clause        → "is | how calm everyone seems"
#   (c)  is + PRON + VERB        → "is | we never had a plan"
#   (d)  is + VBG                → "is | remaining focused"
#   (e)  is + (ADV+) + ADJ       → "is really | big"          (be needs ADV)
#                                   "looks | microscopic"     (non-be doesn't)
#   (f)  is + JJR | + ADJ + than → "is | better than expected"  (be only)
#   (g)  is + SCONJ "that"       → "is | that we never planned"
#
# NEW — (h)  is + [complement] + "that"/WH + clause
#       Splits BEFORE the post-complement clause-introducer.  Triggered when
#       a copula is followed by an ADJ / NOUN / PROPN / VBN / VBG complement
#       and then a clause-introducing SCONJ "that" or WH-word.  Takes
#       PRIORITY over (a-g) — if (h) fires, the post-copula split is
#       suppressed so we don't fragment the complement.
#       Examples:
#         "It became obvious | that he was right"
#         "It became clear | what had to be done"
#         "She remained convinced | that the answer was wrong"
#         "It became known | that the boss had quit"  (VBN complement)
#         "The point is obvious | that no one is listening"  (be variant)
#
# NEGATION:
#   "isn't really big" → split AFTER "n't":
#     "The empire state building isn't | really big."
#
# Skipped when:
#   • verb has dep aux/auxpass — progressive, perfect, passive
#   • non-be copula in non-copular use (transitive / perceptive)
#   • bare 'be + ADJ' with no intensifier / comparative ("is simple")
#   • lead-in has zero content tokens
# -----------------------------------------------------------------------------
def rule_copula_reveal_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        lemma = t.lemma_.lower()
        if lemma not in ALL_COPULA_LEMMAS:
            continue
        if t.pos_ not in {"AUX", "VERB"}:
            continue
        if t.dep_ in {"aux", "auxpass"}:
            continue

        # Need at least 1 token of lead-in (even just a PRON subject)
        if t.i == t.sent.start:
            continue

        if t.i + 1 >= len(doc):
            continue
        nxt = doc[t.i + 1]
        if nxt.is_punct:
            continue

        # Walk past ADV chain + negation to the trigger token.
        k = t.i + 1
        adv_chain_len = 0
        neg_offset = 0
        while k < len(doc):
            tk = doc[k]
            if tk.pos_ == "ADV":
                adv_chain_len += 1
                k += 1
            elif tk.dep_ == "neg" or tk.lower_ in NEGATION_TOKENS:
                neg_offset += 1
                k += 1
            else:
                break
        if k >= len(doc) or doc[k].is_punct:
            continue
        trigger = doc[k]

        # --- PATTERN (h) PRIORITY CHECK ----------------------------------
        # Runs BEFORE _is_copular_use because the pattern itself is the
        # disambiguator: copula-lemma + complement + "that"/WH-clause is
        # overwhelmingly copular regardless of how spaCy parses the dep.
        # Catches "She remained convinced that..." even when spaCy parses
        # "convinced" with a non-standard dep label.
        post_complement_split = None
        complement_eligible = (trigger.pos_ in {"ADJ", "NOUN", "PROPN"}
                               or trigger.tag_ in {"VBN", "VBG"})
        if complement_eligible:
            j = k + 1
            while j < len(doc) and (
                doc[j].pos_ in {"ADJ", "ADV", "NOUN", "PROPN", "DET"}
                or doc[j].tag_ in {"VBN", "VBG"}
            ):
                j += 1
            if j < len(doc) and not doc[j].is_punct:
                tk = doc[j]
                is_that_clause = (tk.pos_ == "SCONJ" and tk.lower_ == "that")
                is_wh_clause = (tk.tag_ in WH_TAGS)
                if is_that_clause or is_wh_clause:
                    if _content_count(doc, t.i, j) >= 1:
                        post_complement_split = j

        if post_complement_split is not None:
            # Split AFTER "that" / WH-word so the connector clings backward
            # as a cliffhanger.  Consistent with rule_clause_starters' SCONJ
            # handling and your style guide.
            out.add(post_complement_split + 1)
            continue

        # For non-be copulas in patterns (a-g), require copular use.
        is_be = (lemma == "be")
        if not is_be and not _is_copular_use(t):
            continue

        # Patterns (a-g) require full content lead-in.
        prev = _prev_split(splits | out, t.i)
        lead_content = _content_count(doc, prev, t.i)
        if lead_content < 1:
            continue

        should_split = False

        if trigger.tag_ in WH_TAGS:
            should_split = True
        elif trigger.pos_ == "SCONJ" and trigger.lower_ == "that":
            should_split = True
        elif trigger.pos_ == "DET":
            for j in range(k + 1, min(k + 7, len(doc))):
                if doc[j].pos_ in {"NOUN", "PROPN"}:
                    should_split = True
                    break
                if doc[j].text in HARD_PUNCT:
                    break
        elif trigger.pos_ == "PRON":
            for j in range(k + 1, min(k + 5, len(doc))):
                if doc[j].pos_ in {"VERB", "AUX"}:
                    should_split = True
                    break
                if doc[j].text in HARD_PUNCT:
                    break
        elif trigger.tag_ in {"VBG", "VBN"}:
            # VBG = gerund-predicate ("is remaining focused")
            # VBN = past-participle predicate ("get confused", "remained convinced")
            should_split = True
        elif trigger.pos_ == "ADJ":
            if is_be:
                if adv_chain_len >= 1:
                    should_split = True
                else:
                    is_comp = (trigger.tag_ == "JJR")
                    if not is_comp:
                        for j in range(k + 1, min(k + 6, len(doc))):
                            if doc[j].lower_ == "than":
                                is_comp = True
                                break
                            if doc[j].text in HARD_PUNCT:
                                break
                    if is_comp:
                        should_split = True
            else:
                should_split = True
        elif trigger.pos_ in {"NOUN", "PROPN"} and not is_be:
            should_split = True

        if should_split:
            out.add(t.i + 1 + neg_offset)

    return out



# -----------------------------------------------------------------------------
# RULE 43 — POSSESSION REVEAL  (Family 2: "X has/owns/contains Y" split)
#
# Splits AFTER a possession / containment / featuring / lacking verb when
# the dobj carries a substantial reveal payload.  The dobj subtree IS the
# reveal — what's owned, contained, featured, lacked, harbored.
#
# Covered sub-families:
#   • CORE:        have, own, possess
#   • CONTAINMENT: contain, include, comprise, encompass
#   • FEATURING:   feature, boast, offer, provide, present
#   • NEGATIVE:    lack, miss, need, require
#   • HIDDEN:      harbor, harbour, house
#
# DISAMBIGUATION (NLP-driven):
#   • "have" filtered when dep == aux (perfect aspect: "has walked")
#   • "have/has/had to" infinitive filtered structurally
#   • Verb must have a substantial dobj (_is_substantial_dobj)
#   • Sentence must be ≥7 non-punct tokens — keeps tiny sentences whole
#     ("I have a cat." = 4 tokens, stays one line)
#
# NEGATION HANDLING:
#   Split lands AFTER negation:
#     "doesn't have | the kind of network..."
#     "lacks | a real exit strategy"  (no neg, but "lacks" itself is the reveal verb)
#
# Examples that FIRE:
#   "Let's assume you have | great cycle paths and missing trains..."
#   "She had | a strange recurring dream."
#   "We own | three houses near the coast."
#   "The cave harbors | thousands of bats."
#   "The plan lacks | a real exit strategy."
#   "Most cities are missing | the kind of dense bike network Amsterdam has."
#   "The new policy provides | small teams with faster shipping cycles."
#
# Examples that DON'T FIRE:
#   "I have walked for hours."          (have = perfect-aspect aux)
#   "She has to leave by noon."         ("has to" infinitive)
#   "I have it."                        (bare-pronoun dobj, not substantial)
#   "The room has a chair."             (short sentence, < 7 tokens)
# -----------------------------------------------------------------------------
def rule_possession_reveal_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        lemma = t.lemma_.lower()
        if lemma not in ALL_POSSESSION_LEMMAS:
            continue
        if t.pos_ not in {"VERB", "AUX"}:
            continue
        # Filter perfect-aspect / passive auxiliary uses
        if t.dep_ in {"aux", "auxpass"}:
            continue
        # Filter "have/has/had to" infinitive (FROZEN_BIGRAMS handles the
        # join elsewhere, but explicitly skip here too)
        if t.i + 1 < len(doc) and doc[t.i + 1].lower_ == "to":
            continue
        # Must have a substantive direct-object reveal payload
        if not _is_substantial_dobj(t):
            continue
        # Sentence must be long enough for the reveal to be visually useful
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 7:
            continue
        # Need at least 1 content token of lead-in
        # — same logic as pattern (h) in rule_copula_reveal_split.
        if t.i == t.sent.start:
            continue
        # Handle negation — split lands AFTER negation particles
        neg_offset = 0
        k = t.i + 1
        while k < len(doc) and (doc[k].dep_ == "neg"
                                or doc[k].lower_ in NEGATION_TOKENS):
            neg_offset += 1
            k += 1
        split_pos = t.i + 1 + neg_offset
        if split_pos >= len(doc):
            continue
        out.add(split_pos)
    return out


# -----------------------------------------------------------------------------
# RULE 44 — CREATION REVEAL  (Family 3: "X produced/built/created Y" split)
# 
# Splits AFTER a creation / production / transformation verb when its
# direct-object subtree carries a substantial reveal.  The dobj IS the
# reveal — what was produced, built, created, designed, caused.
# 
# Covered sub-families:
#   • PRODUCE: produce, manufacture, generate, fabricate, yield
#   • BUILD:   build, construct, assemble, erect, raise
#   • CREATE:  create, invent, conceive, establish, found, launch, introduce
#   • CRAFT:   craft, shape, sculpt, mold, forge
#   • DESIGN:  design, develop, devise, engineer, pioneer, architect
#   • CAUSE:   cause, trigger, spark, prompt, drive
#   • ENABLE:  enable, allow, permit, let
# 
# DISAMBIGUATION:
#   • Verb must be VERB pos (not auxiliary use)
#   • Verb must NOT be in passive context (auxpass on a child)
#   • Verb must have a substantial dobj (_is_substantial_dobj)
#   • Sentence must be ≥8 non-punct tokens
# 
# Examples that FIRE:
#   "Nature accidentally produced | calibration tools larger than cities."
#   "Edison created | the first practical lightbulb in 1879."
#   "The new policy enables | small teams to ship faster."
#   "The collapse triggered | a chain reaction across three continents."
#   "She designed | a deceptively simple interface."
#   "They built | a fence around the property."
#   "He invented | a device that detects pollution from satellites."
# 
# Examples that DON'T FIRE:
#   "He produced a list."           (single-token dobj, not substantial)
#   "The factory produces cars."    (5 tokens, < 8 sentence threshold)
#   "I built it."                   (PRON dobj, not substantial)
#   "She designed her own dress."   (short subtree without ADJ/multi-noun)
# -----------------------------------------------------------------------------
def rule_creation_reveal_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        lemma = t.lemma_.lower()
        if lemma not in ALL_CREATION_LEMMAS:
            continue
        if t.pos_ != "VERB":
            continue
        # Filter aux uses (defensive — creation verbs rarely act as aux)
        if t.dep_ in {"aux", "auxpass"}:
            continue
        # Filter passive constructions — in "was produced by X", the
        # passive subject is the thing produced, not a substantial dobj.
        # Detect by presence of auxpass among children OR dep == ROOT
        # with no dobj but a nsubjpass.
        has_auxpass = any(c.dep_ == "auxpass" for c in t.children)
        if has_auxpass:
            continue
        # Must have a substantive direct-object reveal payload
        if not _is_substantial_dobj(t):
            continue
        # Sentence must be long enough
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 8:
            continue
        # Need at least 1 token of lead-in (even just a PRON subject)
        if t.i == t.sent.start:
            continue
        # Handle negation — split lands AFTER negation particles
        neg_offset = 0
        k = t.i + 1
        while k < len(doc) and (doc[k].dep_ == "neg"
                                or doc[k].lower_ in NEGATION_TOKENS):
            neg_offset += 1
            k += 1
        split_pos = t.i + 1 + neg_offset
        if split_pos >= len(doc):
            continue
        out.add(split_pos)
    return out


# -----------------------------------------------------------------------------
# RULE 45 — PERCEPTION REVEAL  (Family 4: "X saw/found/knew Y" split)
#
# Splits AFTER a perception / cognition / discovery / saying verb when
# its complement carries a substantial reveal.  Accepts BOTH dobj-style
# complements ("noticed the strange shape") and ccomp/xcomp clausal
# complements ("noticed that he was late", "saw him leave").
#
# Covered sub-families:
#   • SEE:     see, spot, notice, observe, witness, glimpse, perceive, detect
#   • FIND:    find, discover, uncover, unearth, encounter
#   • REALIZE: realize/realise, recognize/recognise, understand, grasp, comprehend
#   • THINK:   think, believe, suspect, assume, suppose, reckon, imagine, guess
#   • KNOW:    know, mean, signify, imply, indicate, suggest
#   • REVEAL:  reveal, show, demonstrate, expose, disclose
#   • SAY:     say, claim, argue, declare, announce, report, state,
#              mention, admit, confess, swear, insist, warn, whisper, promise
#   • SENSE:   hear, overhear, watch, smell, taste, sense, feel  (v18 —
#              copular "feels cold" is filtered out automatically because
#              acomp is not a substantial dobj/ccomp complement)
#
# DISAMBIGUATION:
#   • Verb must be VERB pos (not auxiliary use)
#   • Verb must NOT be in passive context (auxpass child)
#   • Verb must have a substantial complement (_has_substantial_complement)
#   • Sentence must be ≥8 non-punct tokens
#
# NEGATION HANDLING:
#   Split lands AFTER any negation particle, matching Families 2/3.
#
# CLAUSAL COMPLEMENT HANDLING:
#   For ccomp complements introduced by "that"/WH (e.g. "noticed that...",
#   "saw what..."), the split lands AFTER the introducer to match
#   Pattern (h) in rule_copula_reveal_split — "that"/"what" clings
#   backward as a cliffhanger.
#
# Examples that FIRE:
#   "She noticed | the strange shape pressed into the snow."
#   "He realized that | the answer had been there all along."
#   "Scientists discovered | a previously unknown species deep in the cave."
#   "The report claims that | the entire dataset is corrupted."
#   "I suspect | something is wrong with the calibration."
#   "She admitted | she'd never actually read the book."
#   "They observed | thousands of stars across the southern sky."
#   "He demonstrated how | the technique could be used in surgery."
#
# Examples that DON'T FIRE:
#   "She saw it."                         (PRON dobj)
#   "I know."                             (no complement)
#   "He noticed the dog."                 (short sentence)
#   "Birds were spotted in the garden."   (passive)
#   "I think so."                         (so = ADV, no substantial complement)
# -----------------------------------------------------------------------------
def rule_perception_reveal_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        lemma = t.lemma_.lower()
        if lemma not in ALL_PERCEPTION_LEMMAS:
            continue
        if t.pos_ != "VERB":
            continue
        if t.dep_ in {"aux", "auxpass"}:
            continue
        # Filter passive constructions
        has_auxpass = any(c.dep_ == "auxpass" for c in t.children)
        if has_auxpass:
            continue
        # Must have a substantial complement (dobj OR ccomp OR xcomp)
        has_comp, comp_head = _has_substantial_complement(t)
        if not has_comp:
            continue
        # Sentence must be long enough
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 8:
            continue
        # Need at least 1 token of lead-in
        if t.i == t.sent.start:
            continue
        # Handle negation
        neg_offset = 0
        k = t.i + 1
        while k < len(doc) and (doc[k].dep_ == "neg"
                                or doc[k].lower_ in NEGATION_TOKENS):
            neg_offset += 1
            k += 1
        # Determine split position.  For ccomp introduced by "that"/WH,
        # split AFTER the introducer (cliffhanger pattern, matches
        # Pattern (h) in rule_copula_reveal_split).  Otherwise split
        # immediately after the verb (+ negation).
        default_pos = t.i + 1 + neg_offset
        split_pos = default_pos
        if comp_head is not None and comp_head.dep_ == "ccomp":
            # Walk from default_pos forward looking for "that" / WH-word
            # introducing the ccomp.  Allow up to 2 tokens of slack.
            for j in range(default_pos, min(default_pos + 3, len(doc))):
                tk = doc[j]
                if tk.pos_ == "SCONJ" and tk.lower_ == "that":
                    split_pos = j + 1
                    break
                if tk.tag_ in WH_TAGS:
                    split_pos = j + 1
                    break
        if split_pos >= len(doc):
            continue
        out.add(split_pos)
    return out

# -----------------------------------------------------------------------------
# RULE 46 — SPATIAL PREP REVEAL  (Family 5: "X happens through/into/across Y")
#
# Splits AFTER a spatial / directional / temporal preposition whose object
# subtree carries a substantive locative or trajectory reveal.  The "what
# happens where" pattern that runs through descriptive prose.
#
# Covered preposition classes:
#   • LOCATIVE:    in, on, at, under, over, above, behind, between, among,
#                  around, near, inside, outside, within, throughout, against,
#                  atop, upon, beneath, underneath
#   • DIRECTIONAL: into, onto, through, across, along, past, toward(s),
#                  beyond, off, via
#   • TEMPORAL:    during, since, until, till, before, after, while
#
# Stay-out set (handled by other rules):
#   of, with, for, about, as, like, than, per (PROMISCUOUS_PREPS / tight binders)
#
# DISAMBIGUATION (NLP-driven):
#   • Token must be POS=ADP (filters approximator uses of "around")
#   • Subtree must contain ≥ SPATIAL_PREP_SUBTREE_MIN_NOUNS nouns
#   • Subtree must NOT contain a finite verb/aux (filters preposition-led
#     relative clauses — those are handled by other rules)
#   • Sentence must be ≥ SPATIAL_PREP_SENT_MIN_TOKENS tokens
#   • Lead-in to prep must have ≥ SPATIAL_PREP_LEAD_MIN content tokens
#   • Lead-in must contain at least one VERB/AUX (so we don't split
#     mid-NP: "the cat in the box | on the table" — there's no VERB
#     between, so the second "on" doesn't qualify as a reveal)
#
# Overlap with existing rules:
#   This rule has overlap with rule_long_preps and rule_pp_intro_reveal.
#   Both of those use generic subtree-size heuristics; this rule uses
#   a curated whitelist + tighter structural gating, so it fires more
#   precisely on intended spatial reveals.  When all three rules agree
#   on a split position, the result is the same (deduplicated by set).
#
# Examples that FIRE:
#   "Straight roads vanishing into | heat haze for absurd distances."
#   "Marine fossils scattered through | regions of brutal aridity."
#   "She traced her finger along | the spine of the old manuscript."
#   "The bridge collapsed across | a ravine nearly four hundred feet deep."
#   "Migration routes shift over | thousands of square miles of tundra."
#   "Construction halted during | one of the worst storms in a century."
#
# Examples that DON'T FIRE:
#   "He looked at the sky."             (short sentence)
#   "She stood in the garden."          (short sentence, single-noun subtree)
#   "Books about world history."        (about is tight binder, excluded)
#   "Made of premium leather."          ("of" excluded)
#   "Scattered through regions that are now brutally dry."
#                                        (subtree contains verb 'are' — let
#                                         rule_terminal_descriptor reveal
#                                         "brutally dry" instead)
# -----------------------------------------------------------------------------
def rule_spatial_prep_reveal_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "ADP":
            continue
        if t.lower_ not in ALL_SPATIAL_PREPS:
            continue
        # Sentence-length gate
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < SPATIAL_PREP_SENT_MIN_TOKENS:
            continue
        # Subtree analysis: enough nouns, no finite verb/aux
        subtree = list(t.subtree)
        n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
        if n_nouns < SPATIAL_PREP_SUBTREE_MIN_NOUNS:
            continue
        if any(x.pos_ in {"VERB", "AUX"} for x in subtree):
            continue
        # Lead-in must have substantial content + at least one verb
        prev = _prev_split(splits | out, t.i + 1)
        lead = doc[prev:t.i + 1]
        lead_content = sum(1 for x in lead
                           if x.pos_ in {"NOUN", "PROPN", "VERB",
                                         "ADJ", "ADV", "NUM"})
        if lead_content < SPATIAL_PREP_LEAD_MIN:
            continue
        if not any(x.pos_ in {"VERB", "AUX"} for x in lead):
            continue
        # No HARD_PUNCT in lead
        if any(x.text in HARD_PUNCT for x in lead):
            continue
        # Split AFTER the preposition
        out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 47 — RESULT CLAUSE REVEAL  (Family 6: "so/such/more X that/than Y")
#
# Splits AFTER a result-clause connector ("that", "than", "to") when
# preceded by an intensifier ("so", "such", "more", "less", "fewer",
# "too", "enough") within RESULT_INTENSIFIER_LOOKBACK tokens.  The
# result clause downstream is the reveal payload.
#
# Patterns:
#   (a) "so X that Y"      → split AFTER "that"
#       "Some skeletons are so well preserved that | you can clearly trace..."
#   (b) "such X that Y"    → split AFTER "that"
#       "Such an obvious answer that | nobody thought to check it."
#   (c) "more/less/fewer X than Y" → split AFTER "than"
#       "The Antarctic is colder than | most people realize."
#       "Calibration tools larger than | cities."
#       (also fires on comparative JJR/RBR adjacency, no explicit intensifier)
#   (d) "too X to Y"       → split AFTER "to"
#       "The fog was too thick to | see your hand in front of you."
#   (e) "X enough to Y"    → split AFTER "to"
#       "He was tired enough to | sleep through the alarm."
#
# DISAMBIGUATION:
#   • "that" → SCONJ tag check filters relative-pronoun "that" (WDT)
#   • "than" → ADP/SCONJ check; intensifier or JJR adjacency required
#   • "to"   → must be aux to a VERB infinitive
#   • Intensifier must appear within RESULT_INTENSIFIER_LOOKBACK tokens
#     before the connector, in the SAME sentence (no crossing punctuation)
#   • Sentence must be ≥9 non-punct tokens
#
# Examples that FIRE:
#   "Some skeletons are so well preserved that | you can trace..."
#   "The fog was so thick that | you couldn't see your hand."
#   "Nature produced calibration tools larger than | cities."
#   "The Antarctic is colder than | most people realize."
#   "Such an obvious answer that | nobody thought to check it."
#   "He was tired enough to | sleep through three alarms."
#   "The river was too wide to | cross without a bridge."
#
# Examples that DON'T FIRE:
#   "The man that left was tall."           (no intensifier)
#   "I want to leave."                       (no intensifier)
#   "She said that it would rain."           (no intensifier — Family 4 ccomp)
#   "Better than nothing."                   (short sentence)
#   "Books that I love stay on the shelf."   (no intensifier, relative clause)
# -----------------------------------------------------------------------------
def rule_result_clause_reveal_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        lt = t.lower_
        if lt not in {"that", "than", "to"}:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 9:
            continue
        # Look back for an intensifier within the lookback window,
        # within the same sentence, and not across hard punct.
        intensifier_found: Optional[str] = None
        lookback_start = max(t.sent.start, t.i - RESULT_INTENSIFIER_LOOKBACK)
        for j in range(t.i - 1, lookback_start - 1, -1):
            jt = doc[j]
            if jt.text in HARD_PUNCT:
                break
            if jt.lower_ in ALL_RESULT_INTENSIFIERS:
                intensifier_found = jt.lower_
                break
            # Comparative adjacency: a JJR/RBR-tagged ADJ/ADV upstream
            # of "than" counts as an implicit intensifier
            # ("colder than", "faster than", "larger than").
            if lt == "than" and jt.tag_ in {"JJR", "RBR"}:
                intensifier_found = "<comparative>"
                break

        if intensifier_found is None:
            continue

        # Pattern-specific validation
        if lt == "that":
            # Must be SCONJ (filters WDT relative-pronoun "that")
            if t.pos_ != "SCONJ":
                continue
            # Only fires with "so"/"such" intensifier (not "more"/"too")
            if intensifier_found not in RESULT_THAT_INTENSIFIERS:
                continue

        elif lt == "than":
            # Must be ADP or SCONJ (functional comparison)
            if t.pos_ not in {"ADP", "SCONJ"}:
                continue
            # Only fires with comparative or "more/less/fewer" intensifier
            if (intensifier_found != "<comparative>"
                    and intensifier_found not in RESULT_THAN_INTENSIFIERS):
                continue

        elif lt == "to":
            # Must be aux to an infinitive VERB
            if t.dep_ != "aux":
                continue
            if t.i + 1 >= len(doc) or doc[t.i + 1].pos_ != "VERB":
                continue
            # Only fires with "too"/"enough" intensifier
            if intensifier_found not in RESULT_TO_INTENSIFIERS:
                continue

        # Split AFTER the connector — connector clings backward as
        # cliffhanger (consistent with Family 1 Pattern (h) and Family 4
        # ccomp handling).
        out.add(t.i + 1)

    return out


# -----------------------------------------------------------------------------
# RULE 48 — EQUATION REVEAL  (Family 7: "X means/equals/represents Y")
#
# Splits AFTER an equation / definition verb (plus any phrasal particle)
# when the complement carries a substantial reveal.  The complement
# defines, equates, or signifies the subject.
#
# Covered sub-families:
#   • EQUAL:  equal, represent, signify, symbolize/symbolise, denote,
#             stand (for)
#   • MEAN:   mean, imply, indicate, suggest, translate (to/into)
#   • REFER:  refer (to), amount (to), boil (down to), come (down to)
#
# DISAMBIGUATION:
#   • Verb must be VERB pos
#   • For phrasal verbs, required particle must be present
#     (_is_equation_use)
#   • Verb must have a substantial complement (dobj OR ccomp OR pobj
#     of the phrasal particle)
#   • Sentence must be ≥6 non-punct tokens (lower than Families 2-4
#     because definitional sentences are often short)
#
# SPLIT POSITION:
#   • Non-phrasal: split AFTER the verb (+ negation)
#   • Phrasal:     split AFTER the particle so it clings backward
#                  ("stands for | X", "boils down to | X")
#
# Examples that FIRE:
#   "Photosynthesis means | plants converting sunlight into energy."
#   "The acronym ROI stands for | return on investment."
#   "What this represents is | a complete shift in approach."
#   "The decision essentially boils down to | timing and budget."
#   "These symbols denote | the various phases of the experiment."
#   "The discovery signifies | a turning point in the field."
#   "An IOU amounts to | a written promise to pay."
#   "His silence implied | that he agreed with the plan."
#
# Examples that DON'T FIRE:
#   "I mean it."                            (PRON dobj)
#   "X equals Y."                           (short sentence)
#   "She stands tall."                      ("stand" without "for" particle)
#   "He refers a case to the lawyer."       (no "to" prep in equation sense —
#                                            actually does have "to", so might fire;
#                                            watch this one)
# -----------------------------------------------------------------------------
def rule_equation_reveal_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        lemma = t.lemma_.lower()
        if lemma not in ALL_EQUATION_LEMMAS:
            continue
        if t.pos_ != "VERB":
            continue
        if t.dep_ in {"aux", "auxpass"}:
            continue
        has_auxpass = any(c.dep_ == "auxpass" for c in t.children)
        if has_auxpass:
            continue
        # Phrasal-verb particle check
        if not _is_equation_use(t):
            continue
        # Must have a substantial complement (dobj, ccomp, or — for
        # phrasal verbs — pobj of the particle)
        has_comp, comp_head = _has_substantial_complement(t)
        if not has_comp:
            # For phrasal verbs, the "object" may be the pobj of the
            # phrasal particle.  Walk children to find prep + pobj.
            # In rule_equation_reveal_split — in the phrasal fallback block, replace:
            if lemma in EQUATION_PHRASAL_PARTICLES:
                required = EQUATION_PHRASAL_PARTICLES[lemma]
                for child in t.children:
                    # Direct child match
                    if (child.pos_ in {"ADP", "PART"}
                            and child.lower_ in required):
                        pobj = next((g for g in child.children
                                     if g.dep_ in {"pobj", "obj"}), None)
                        if pobj is not None:
                            subtree_len = len(list(pobj.subtree))
                            if subtree_len >= 2:
                                has_comp = True
                                comp_head = pobj
                                break
                    # NEW: grandchild match (e.g. "boil" → "down" → "to" → pobj)
                    for grand in child.children:
                        if (grand.pos_ in {"ADP", "PART"}
                                and grand.lower_ in required):
                            pobj = next((g for g in grand.children
                                         if g.dep_ in {"pobj", "obj"}), None)
                            if pobj is not None:
                                subtree_len = len(list(pobj.subtree))
                                if subtree_len >= 2:
                                    has_comp = True
                                    comp_head = pobj
                                    break
                    if has_comp:
                        break
            if not has_comp:
                continue
        # Sentence-length floor (lower than Families 2-4)
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 6:
            continue
        # Need a lead-in token
        if t.i == t.sent.start:
            continue
        # Determine split position
        # For phrasal verbs, walk forward through any particle/prep so
        # the particle clings backward with the verb.
        split_pos = t.i + 1
        if lemma in EQUATION_PHRASAL_PARTICLES:
            required = EQUATION_PHRASAL_PARTICLES[lemma]
            k = t.i + 1
            # Walk past ADV ("down" in "boils down to") and the particle itself
            while k < len(doc) and (doc[k].pos_ == "ADV"
                                     or (doc[k].pos_ in {"ADP", "PART"}
                                         and doc[k].lower_ in required)):
                k += 1
            split_pos = k
        # Handle negation (for non-phrasal verbs only — phrasal verbs
        # with negation are rare and odd)
        if lemma not in EQUATION_PHRASAL_PARTICLES:
            neg_offset = 0
            k = t.i + 1
            while k < len(doc) and (doc[k].dep_ == "neg"
                                    or doc[k].lower_ in NEGATION_TOKENS):
                neg_offset += 1
                k += 1
            split_pos = t.i + 1 + neg_offset
        if split_pos >= len(doc):
            continue
        out.add(split_pos)
    return out


# -----------------------------------------------------------------------------
# RULE 49 — AGGRESSIVE "AND" / "OR" BETWEEN VISUALISABLES
#
# Splits BEFORE "and"/"or" whenever:
#   • the token to its LEFT is a visualisable content token
#     (NOUN/PROPN/ADJ/VERB/NUM, excluding weak copulas)
#   • the token to its RIGHT is also a visualisable content token
#     (looking through optional DET/ADV)
#   • lead-in ≥ 2 content tokens
#
# This is the deliberately-aggressive variant of rule_and_or_clause.
# rule_and_or_clause has tight guards (≥12 tokens, ≥5 lead) that miss
# valid coordinations in medium sentences.  This rule fills the gap.
#
# Examples that FIRE:
#   "calming | and alien"            (ADJ + AND + ADJ)
#   "dust | and rock"                (NOUN + AND + NOUN)
#   "John | and Mary"                (PROPN + AND + PROPN)
#   "ran | and jumped"               (VERB + AND + VERB) — when lead allows
#   "scientifically valuable | and crucial"  (ADJ + AND + ADJ)
#
# Examples that DON'T FIRE:
#   "is and was"                     (weak copulas — skipped)
#   "I and he"                       (PRON, not visualisable)
#   "and the rest"                   (no lead)
# -----------------------------------------------------------------------------
def rule_and_visualisables_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    DEBUG = False  # flip to True for inspection
    for t in doc:
        if t.pos_ != "CCONJ" or t.lower_ not in {"and", "or"}:
            continue
        if DEBUG:
            print(f"  [and-vis] examining '{t.text}' at idx {t.i}")
        if t.i == 0 or t.i + 1 >= len(doc):
            if DEBUG: print(f"    SKIP: at sentence boundary")
            continue

        # Check LEFT neighbour is visualisable
        left = doc[t.i - 1]
        left_visualisable = _has_visualisable_content(doc, t.i - 1, t.i)
        if DEBUG:
            print(f"    left='{left.text}' (pos={left.pos_}) visualisable={left_visualisable}")
        if not left_visualisable:
            continue

        # Check RIGHT neighbour (through DET/ADV) is visualisable
        j = t.i + 1
        while j < len(doc) and doc[j].pos_ in {"DET", "ADV"}:
            j += 1
        if j >= len(doc):
            if DEBUG: print(f"    SKIP: walked off end")
            continue
        right_visualisable = _has_visualisable_content(doc, j, j + 1)
        if DEBUG:
            print(f"    right='{doc[j].text}' (pos={doc[j].pos_}) visualisable={right_visualisable}")
        if not right_visualisable:
            continue

        # Lead-in ≥ 1 content token (relaxed)
        prev = _prev_split(splits | out, t.i)
        lead_content = _content_count(doc, prev, t.i)
        if DEBUG:
            print(f"    lead=[{prev}:{t.i}] content_count={lead_content}")
        if lead_content < 1:
            if DEBUG: print(f"    SKIP: insufficient lead content")
            continue

        # No HARD_PUNCT in lead
        if any(doc[k].text in HARD_PUNCT for k in range(prev, t.i)):
            if DEBUG: print(f"    SKIP: hard punct in lead")
            continue

        if DEBUG:
            print(f"    -> ADD split at {t.i + 1} (after '{t.text}')")
        out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 50 — TITLE APPOSITIVE VERB REVEAL
# 
# Splits BEFORE the verb that follows a title-appositive subject like
# "Alaric the Goth", "Henry the Eighth", "Ivan the Terrible".  The
# character intro (name + title) becomes its own line, then the verb
# phrase reveals what they did.
# 
# Pattern: PROPN + "the" + Capitalised + <intervening modifiers?> + VERB
# 
# Sentence must be ≥10 non-punct tokens to avoid over-splitting
# short character mentions like "Henry the Eighth died."
# 
# Examples that FIRE:
#   "Alaric the Goth | laid siege to Rome..."
#   "Ivan the Terrible | crowned himself emperor."
#   "Henry the Eighth | dissolved every monastery..."
# 
# Examples that DON'T FIRE:
#   "Henry the Eighth died."         (short sentence)
#   "Alaric the Goth and his men..." (no verb immediately following)
# -----------------------------------------------------------------------------
def rule_title_appositive_verb_split(doc: Doc, splits: Set[int]) -> Set[int]:
    DEBUG = False
    out = set()
    for i in range(len(doc) - 2):
        # Detect PROPN + "the" + Capitalised
        if doc[i].pos_ != "PROPN":
            continue
        if doc[i + 1].lower_ != "the":
            continue
        if i + 2 >= len(doc):
            continue
        title_tok = doc[i + 2]
        if not title_tok.text[:1].isupper():
            continue
        if title_tok.pos_ not in {"NOUN", "PROPN", "ADJ"}:
            continue
        # Walk forward past any additional title tokens (continued Capitalised
        # words / NOUN/PROPN) to find the post-title VERB.
        j = i + 3
        while j < len(doc) and doc[j].text[:1].isupper() \
                and doc[j].pos_ in {"NOUN", "PROPN", "ADJ"}:
            j += 1
        # j is now the first non-title token; must be a VERB
        if j >= len(doc):
            continue
        if doc[j].pos_ != "VERB":
            if DEBUG:
                print(f"  [title-verb] SKIP at idx {j}: not VERB "
                      f"(found {doc[j].pos_} '{doc[j].text}')")
            continue
        # Sentence length floor
        sent_ntok = sum(1 for x in doc[j].sent if not x.is_punct)
        if sent_ntok < 10:
            if DEBUG:
                print(f"  [title-verb] SKIP at idx {j}: sent too short ({sent_ntok})")
            continue
        if DEBUG:
            print(f"  [title-verb] ADD split at idx {j} (before VERB '{doc[j].text}'): "
                  f"after title '{doc[i].text} {doc[i+1].text} ...'")
        out.add(j)
    return out



# -----------------------------------------------------------------------------
# RULE 51 — FIRST LIST ITEM REVEAL
#
# Splits BEFORE the first item of a multi-item list so each list item
# gets its own line (typography-style).
#
# Pattern: an NP whose `conj` children are themselves NPs in a
# comma-separated list, AND the NP follows a "list-introducer" token
# (preposition, dash, colon, comma, "but", "not").
#
# Examples that FIRE:
#   "cathedrals — | not the architecture, not the stained glass, but..."
#   "through | trial, error, and a few lucky accidents..."
#   "Every surface — | the pillars, the vaults, the carved stone saints"
#     (already split via dash, but reinforces the pattern)
#
# Detection:
#   1. Find a token T that heads an NP (NOUN/PROPN/ADJ, dep != det/amod)
#   2. T has ≥2 conj children that are also NOUN/PROPN heads
#   3. Walk back from T's NP start: previous content token is a
#      preposition, dash, colon, or sentence-initial position
#   4. Sentence length ≥10
# -----------------------------------------------------------------------------
def rule_first_list_item_split(doc: Doc, splits: Set[int]) -> Set[int]:
    DEBUG = False
    out = set()
    for t in doc:
        if t.pos_ not in {"NOUN", "PROPN", "ADJ", "DET"}:
            continue
        # Find the head of the noun chunk that contains this token
        chunk = _chunk_containing(doc, t.i)
        if chunk is None:
            continue
        head = chunk.root
        if head.i != t.i and t.i != chunk.start:
            continue  # only process at chunk start

        # Count conj siblings of the head that look like NP heads
        n_conj_np = sum(1 for c in head.children
                        if c.dep_ == "conj" and c.pos_ in {"NOUN", "PROPN"})
        if n_conj_np < 1:
            continue

        # Also require a comma somewhere in the conj chain — confirms list
        chain_has_comma = False
        for c in head.children:
            if c.dep_ == "conj":
                # check tokens between head and this conj
                for k in range(head.i + 1, c.i):
                    if doc[k].text == ",":
                        chain_has_comma = True
                        break
        if not chain_has_comma:
            continue

        split_at = chunk.start
        if split_at == 0:
            continue

        # Check left context — what's immediately before the chunk start?
        prev_tok = doc[split_at - 1]
        is_list_intro = (
            prev_tok.pos_ == "ADP"
            or prev_tok.text in DASH_PUNCT
            or prev_tok.text in {":", ","}
            or prev_tok.lower_ in {"but", "and", "or", "not"}
        )
        if not is_list_intro:
            if DEBUG:
                print(f"  [first-list] SKIP at idx {split_at}: prev "
                      f"'{prev_tok.text}' (pos={prev_tok.pos_}) not list intro")
            continue

        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 10:
            if DEBUG:
                print(f"  [first-list] SKIP at idx {split_at}: sent too short ({sent_ntok})")
            continue

        if DEBUG:
            print(f"  [first-list] ADD split at idx {split_at}: "
                  f"first item '{chunk.text}' (after '{prev_tok.text}'), "
                  f"n_conj={n_conj_np}")
        out.add(split_at)


    # NEW: "not X, not Y, but Z" pattern — repeated negation lists
    # that spaCy may not parse as conj.  Trigger when we find
    # consecutive "not <chunk>" patterns separated by commas.
    for t in doc:
        if t.lower_ != "not":
            continue
        if t.i == 0:
            continue
        prev_tok = doc[t.i - 1]
        # Need: previous token is DASH or COMMA (list-intro context)
        if prev_tok.text not in (DASH_PUNCT | {","}):
            continue
        # And: look ahead for another "not" or "but" within 8 tokens,
        # confirming this is a "not X, not Y, but Z" list
        found_continuation = False
        for k in range(t.i + 1, min(t.i + 12, len(doc))):
            if doc[k].text in HARD_PUNCT:
                break
            if doc[k].lower_ in {"not", "but"} and k > t.i + 1:
                found_continuation = True
                break
        if not found_continuation:
            continue
        if DEBUG:
            print(f"  [first-list] ADD 'not'-list split at idx {t.i}: "
                  f"'{prev_tok.text} | not ...'")
        out.add(t.i)

    return out


# -----------------------------------------------------------------------------
# RULE 52 — TERMINAL SPECIFIER REVEAL
#
# Splits BEFORE a sentence-terminal specifier/appositive that attaches to
# a preceding dobj NP head.  The "reveal what X is" pattern.
#
# Pattern: VERB + ... + DET? + NOUN_DOBJ_HEAD + SPECIFIER.
#
# Generic over all verbs (not restricted to emotion/perception families).
# The disambiguator is purely structural:
#   • last token is sentence-terminal content (NOUN/PROPN/ADJ/NUM)
#   • token immediately before it is a NOUN/PROPN
#   • that NOUN/PROPN is the dobj/obj of an earlier VERB in the sentence
#   • the specifier's syntactic head IS that NOUN/PROPN
#     (this filters out adverbials like "today" whose head is the verb,
#      not the dobj)
#
# Examples that FIRE:
#   "I hate the word several"     → "I hate the word | several"
#   "I love the song Yesterday"   → "I love the song | Yesterday"
#   "She read the book Dune"      → "She read the book | Dune"
#   "I prefer the color red"      → "I prefer the color | red"
#
# Examples that DON'T FIRE:
#   "I love red wine"             — prev is ADJ, not NOUN-dobj head
#   "I drove the red car"         — last IS the dobj head, no specifier
#   "I met John Smith"            — Smith is compound of John, not dobj
#   "I love the music industry"   — industry IS the dobj head
#   "I read the book today"       — today's head is read, not book
# -----------------------------------------------------------------------------
def rule_terminal_specifier_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for sent in doc.sents:
        sent_ntok = sum(1 for x in sent if not x.is_punct)
        if sent_ntok < 4:
            continue
        # find last content token in the sentence
        last = None
        for t in reversed(list(sent)):
            if t.is_punct or t.is_space:
                continue
            last = t
            break
        if last is None or last.i <= sent.start:
            continue
        # last must be a content word that could be a specifier
        if last.pos_ not in {"NOUN", "PROPN", "ADJ", "NUM"}:
            continue
        # the token immediately before it must be a NOUN/PROPN
        prev = doc[last.i - 1]
        if prev.pos_ not in {"NOUN", "PROPN"}:
            continue
        # prev must be the dobj of an earlier VERB
        if prev.dep_ not in {"dobj", "obj"}:
            continue
        head_verb = prev.head
        if head_verb.pos_ != "VERB":
            continue
        if head_verb.i >= prev.i:
            continue
        # specifier must attach structurally to the dobj head
        # (filters out adverbial modifiers like "today" whose head is the verb)
        if last.head != prev:
            continue
        # need substantial lead-in (verb + det + noun → at least 3 tokens)
        prev_split = _prev_split(splits | out, last.i)
        if last.i - prev_split < 3:
            continue
        out.add(last.i)
    return out


# -----------------------------------------------------------------------------
# RULE 56 — COMPARISON REVEAL  (v18)
#
# Splits at a comparison marker so the compared IMAGE gets its own line.
# This is a deliberate gap-filler: rule_clause_starters explicitly skips
# "like"/"as" after a verb/adj (they read as verb+complement, not a new
# clause), "like" is excluded from the spatial-prep whitelist, and
# rule_prep_object_reveal is gated at 12+ token sentences — so similes,
# the single most cut-worthy visual in narration, previously fell through.
#
# Three branches:
#   (a) resemblance VERBS ("resembles", "mimics", "mirrors") with a
#       substantial dobj → split AFTER the verb (+ any negation), matching
#       the Family 2/3/4 convention.
#   (b) "as if" / "as though" → split AFTER the frozen bigram (a split
#       before "as" would be wiped by anti_rule_split_before_sconj, and a
#       split inside the bigram by anti_rule_frozen_bigram — so the bigram
#       clings backward as a cliffhanger: 'it moved as if' | 'it was
#       breathing').
#   (c) "like" / "unlike" as ADP → split BEFORE the marker so the whole
#       simile is the line ('it looked' | 'like a graveyard of giants').
#       When "like" parses as SCONJ (clausal simile), split AFTER it
#       instead — same cliffhanger reasoning as (b).
#
# DISAMBIGUATION:
#   • approximator "like 500 people" (next non-DET token is numeric) → skip
#   • discourse filler "like, honestly" (dep intj/discourse) → skip
#   • bare-pronoun payload "like it" → skip
#   • sentence-initial "Like most deserts, ..." → skip (rule 7 owns the
#     comma boundary there)
#   • payload must contain a noun; single-token payloads allowed only when
#     terminal ("shattered like | glass." mirrors rule 39's terminal logic)
#
# Examples that FIRE:
#   "From above it looked | like a graveyard of giants."
#   "The whole valley resembles | a fossilised ocean floor."
#   "The ground moved as if | something underneath was breathing."
#   "A landscape unlike | anything else on Earth."
#
# Examples that DON'T FIRE:
#   "Looks like rain."                    (short sentence)
#   "There were like 500 people there."   (approximator use)
#   "I like the desert."                  (VERB 'like', not ADP/SCONJ)
#   "It felt like it."                    (bare-pronoun payload)
# -----------------------------------------------------------------------------
def rule_comparison_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)

        # ---- (a) resemblance verbs --------------------------------------
        if t.pos_ == "VERB" and t.lemma_.lower() in COMPARISON_RESEMBLE_LEMMAS:
            if t.dep_ in {"aux", "auxpass"}:
                continue
            if sent_ntok < COMPARISON_SENT_MIN:
                continue
            if t.i == t.sent.start:
                continue
            if not _is_substantial_dobj(t):
                continue
            # split lands after any negation, matching Families 2/3/4
            neg_offset = 0
            k = t.i + 1
            while k < len(doc) and (doc[k].dep_ == "neg"
                                    or doc[k].lower_ in NEGATION_TOKENS):
                neg_offset += 1
                k += 1
            if t.i + 1 + neg_offset < len(doc):
                out.add(t.i + 1 + neg_offset)
            continue

        # ---- (b) "as if" / "as though" ----------------------------------
        if (t.lower_ == "as" and t.i + 1 < len(doc)
                and doc[t.i + 1].lower_ in {"if", "though"}):
            if sent_ntok < COMPARISON_SENT_MIN:
                continue
            prev = _prev_split(splits | out, t.i)
            if t.i - prev < COMPARISON_LEAD_MIN:
                continue
            clause_start = t.i + 2
            nxt = _next_split(splits | out, clause_start, len(doc))
            if nxt - clause_start < 3:
                continue
            if not _has_visualisable_content(doc, clause_start, nxt):
                continue
            out.add(clause_start)
            continue

        # ---- (c) "like" / "unlike" --------------------------------------
        if t.lower_ not in {"like", "unlike"}:
            continue
        if t.pos_ not in {"ADP", "SCONJ"}:
            continue
        if t.dep_ in {"intj", "discourse"}:
            continue
        if sent_ntok < COMPARISON_SENT_MIN:
            continue
        if t.i == t.sent.start:
            continue
        # approximator use: "like 500 people"
        j = t.i + 1
        while j < len(doc) and doc[j].pos_ == "DET":
            j += 1
        if j < len(doc) and (doc[j].like_num or doc[j].pos_ == "NUM"):
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev < COMPARISON_LEAD_MIN:
            continue

        if t.pos_ == "SCONJ":
            # clausal simile — cliffhanger split AFTER the marker
            # (a split before an SCONJ would be wiped by
            #  anti_rule_split_before_sconj anyway)
            nxt = _next_split(splits | out, t.i + 1, len(doc))
            if nxt - (t.i + 1) < 3:
                continue
            if not _has_visualisable_content(doc, t.i + 1, nxt):
                continue
            out.add(t.i + 1)
            continue

        # ADP branch — the simile PP is the line
        pobj = next((c for c in t.children if c.dep_ in {"pobj", "obj"}), None)
        if pobj is None:
            continue
        subtree = list(pobj.subtree)
        if pobj.pos_ == "PRON" and len(subtree) == 1:
            continue                       # "like it" — not an image
        n_nouns = sum(1 for x in subtree
                      if x.pos_ in {"NOUN", "PROPN"})
        if n_nouns < 1:
            continue
        if len(subtree) < 2 and pobj.pos_ != "PROPN":
            # single common noun — allow only as a terminal reveal
            # ("shattered like | glass."), mirroring rule 39
            last_noun_i = max((x.i for x in t.sent
                               if x.pos_ in {"NOUN", "PROPN", "NUM"}),
                              default=-1)
            if pobj.i != last_noun_i:
                continue
        out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 57 — EXCEPTION REVEAL  (v18)
#
# Splits BEFORE an exception marker so the one-thing-left-out gets its own
# line — the classic twist beat ("every house burned | except one").
#
# Markers:
#   • single: except, excluding
#   • bigram: apart from, aside from, other than, save for
#   ("besides" is deliberately excluded — its discourse use dominates.)
#
# Existing coverage this fills a hole in: "except" isn't in the spatial-prep
# whitelist (RULE 46), and rule_prep_object_reveal needs 12+ token sentences
# — but exception twists live in SHORT sentences.
#
# DISAMBIGUATION:
#   • marker followed directly by punctuation → skip (discourse use)
#   • payload span must be picture-able (_has_visualisable_content)
#   • when the marker parses as SCONJ ("except that he lied"), the split
#     goes AFTER it (cliffhanger; a split before an SCONJ would be wiped
#     by anti_rule_split_before_sconj)
#
# Examples that FIRE:
#   "Everything burned | except one house."
#   "The valley is empty | apart from the bones."
#   "Nothing moves out here | other than the wind."
#
# Examples that DON'T FIRE:
#   "Except me."                          (no lead-in)
#   "Besides, nobody asked."              (marker not in set)
# -----------------------------------------------------------------------------
def rule_exception_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        marker_start: Optional[int] = None
        marker_end:   Optional[int] = None
        nxt_lower = doc[t.i + 1].lower_ if t.i + 1 < len(doc) else ""
        if (t.lower_, nxt_lower) in EXCEPTION_BIGRAMS:
            marker_start, marker_end = t.i, t.i + 2
        elif (t.lower_ in EXCEPTION_SINGLE_MARKERS
                and t.pos_ in {"ADP", "SCONJ", "VERB"}):
            marker_start, marker_end = t.i, t.i + 1
        if marker_start is None:
            continue
        if t.i == t.sent.start:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < EXCEPTION_SENT_MIN:
            continue
        prev = _prev_split(splits | out, marker_start)
        if marker_start - prev < EXCEPTION_LEAD_MIN:
            continue
        # discourse use ("besides," style) — marker straight into punctuation
        if marker_end >= len(doc) or doc[marker_end].is_punct:
            continue
        # payload must paint a picture
        nxt = _next_split(splits | out, marker_end, len(doc))
        if not _has_visualisable_content(doc, marker_end, nxt):
            continue
        if t.pos_ == "SCONJ":
            out.add(marker_end)      # "except that" clings back as cliffhanger
        else:
            out.add(marker_start)
    return out


# -----------------------------------------------------------------------------
# RULE 58 — DISCOURSE PIVOT / RETENTION HOOK  (v18)
#
# Gives fixed script-writing hooks their own beat: split BEFORE the hook
# and (when enough content follows) AFTER it, so lines like "here's the
# thing" or "but wait" sit alone as a visual pause / zoom / text-pop cue.
#
# This is the one deliberately LEXICAL rule in the positive pipeline —
# these phrases are retention idioms, not grammar, so POS/DEP can't find
# them.  The phrase list lives in DISCOURSE_PIVOT_PHRASES.
#
# MATCHING:
#   • lowercase token-sequence match, longest phrase first
#   • smart apostrophes normalised ("here’s" ≡ "here's")
#   • all phrase tokens must sit inside one sentence
#   • preceded by DET/ADJ/NUM/NOUN/PROPN → skip ("a fun fact about...",
#     "the plot twist was..." — noun uses, not hooks)
#   • ("get","this") additionally requires clause-final position
#     ("get this:" yes / "you get this feeling" no)
#   • an ADP "of" straight after the hook is pulled onto the hook line
#     ("as a result of" | "years of pressure")
#   • a comma straight after the hook clings back onto it
#
# Examples that FIRE:
#   "Here's the thing | the map was wrong."
#   "But wait | it gets worse | the second dam was already cracking."
#   "Which brings us to | the strangest part."
#
# Examples that DON'T FIRE:
#   "A fun fact about whales."            (preceded by DET — noun use)
#   "You get this feeling of dread."      (not clause-final)
# -----------------------------------------------------------------------------
def rule_discourse_pivot(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()

    def _low(tok: Token) -> str:
        return tok.lower_.replace("\u2019", "'")

    i = 0
    while i < len(doc):
        matched_len = 0
        for phrase in DISCOURSE_PIVOT_PHRASES:      # longest-first
            n = len(phrase)
            if i + n > len(doc):
                continue
            if tuple(_low(doc[i + k]) for k in range(n)) != phrase:
                continue
            # whole phrase inside one sentence
            if doc[i].sent != doc[i + n - 1].sent:
                continue
            # noun-use guard: "a fun fact", "the plot twist"
            if i > 0 and doc[i - 1].pos_ in {"DET", "ADJ", "NUM",
                                             "NOUN", "PROPN"}:
                continue
            # "get this" must be clause-final ("get this:" / "get this.")
            if phrase == ("get", "this"):
                after = i + n
                if after < len(doc) and not doc[after].is_punct:
                    continue
            matched_len = n
            break
        if not matched_len:
            i += 1
            continue

        # split BEFORE the hook
        if i > doc[i].sent.start:
            out.add(i)

        # split AFTER the hook
        j = i + matched_len
        if j < len(doc) and doc[j].lower_ == "of" and doc[j].pos_ == "ADP":
            j += 1                       # "as a result of" | payload
        if j < len(doc):
            sent_end = doc[i].sent.end
            tail = sum(1 for x in doc[j:sent_end] if not x.is_punct)
            if tail >= DISCOURSE_PIVOT_MIN_TAIL:
                if doc[j].text == ",":
                    out.add(j + 1)       # comma clings back onto the hook
                elif not doc[j].is_punct:
                    out.add(j)
        i += matched_len
    return out


# -----------------------------------------------------------------------------
# RULE 59 — PASSIVE AGENT REVEAL  (v18)
#
# Splits BEFORE the "by ..." agent phrase of a passive so the doer gets
# its own line — cut from the deed to whoever/whatever did it:
# "it was discovered | by a local farmer".
#
# Existing coverage this fills a hole in: "by" isn't in the spatial-prep
# whitelist (RULE 46), and rule_prep_object_reveal only reaches it in
# 12+ token sentences — agent reveals live in short punchy ones.
#
# DETECTION (structural):
#   • token "by" with POS=ADP, and EITHER dep=agent, OR its head verb has
#     an auxpass child ("was discovered by ..."), OR its head is a bare
#     VBN participle (reduced passive: "a valley carved by glaciers")
#   • pobj subtree must contain a NOUN/PROPN/NUM; bare pronouns skipped
#   • single-token pobj allowed only for PROPN ("painted by | Vermeer")
#   • DATE/TIME pobj skipped ("finished by Friday" is a deadline, not
#     an agent)
#
# Examples that FIRE:
#   "The skeletons were uncovered | by a passing camel herder."
#   "A canyon carved | by ten million years of floods."
#
# Examples that DON'T FIRE:
#   "It was seen by them."                (bare pronoun)
#   "The report is due by Friday."        (DATE — deadline use)
#   "She sat by the window."              (no passive context)
# -----------------------------------------------------------------------------
def rule_passive_agent_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.lower_ != "by" or t.pos_ != "ADP":
            continue
        head = t.head
        is_agent = (t.dep_ == "agent"
                    or any(c.dep_ == "auxpass" for c in head.children)
                    or head.tag_ == "VBN")
        if not is_agent:
            continue
        pobj = next((c for c in t.children if c.dep_ in {"pobj", "obj"}), None)
        if pobj is None:
            continue
        if pobj.ent_type_ in {"DATE", "TIME"}:
            continue                     # deadline use ("by Friday")
        subtree = list(pobj.subtree)
        if pobj.pos_ == "PRON" and len(subtree) == 1:
            continue                     # "by them"
        n_content = sum(1 for x in subtree
                        if x.pos_ in {"NOUN", "PROPN", "NUM"})
        if n_content < 1:
            continue
        if len(subtree) < 2 and pobj.pos_ != "PROPN":
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < AGENT_REVEAL_SENT_MIN:
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev < AGENT_REVEAL_LEAD_MIN:
            continue
        out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 60 — SFX / ONOMATOPOEIA BEAT  (v18)
#
# A bare sound word ("boom", "crash", "snap") gets its own line — an SFX
# sync point for the edit: 'and then' | 'boom' | 'the roof came down'.
#
# Because most SFX words double as nouns and verbs, detection requires the
# word to be used BARE:
#   • no det / poss / amod / compound / nummod child  (not "the crash",
#     "a loud bang")
#   • no dobj / nsubj / nsubjpass child                (not "snap a photo",
#     "cars crash")
#   • no phrasal particle child                        (not "pop up",
#     "snap out of it")
#   • not itself a compound modifier, and next token not a NOUN/PROPN
#     (not "crash site", "pop culture", "boom box")
#   • previous token not DET/ADJ/NUM/ADP/PART or possessive
#
# The one-word line this creates contains no canonical "content" and would
# normally be eaten by the throwaway-merging pass — so this rule is listed
# in PROTECTED_RULE_NAMES: its boundaries survive anti-rules AND merges,
# exactly like quotes and brackets do.
#
# A comma straight after the SFX word clings back onto it ("boom," ends
# the line) so no line ever starts with a bare comma.
#
# Examples that FIRE:
#   "And then | boom | the roof came down."
#   "One wrong step and | crack | the ice gives way."
#
# Examples that DON'T FIRE:
#   "The crash killed the market."        (det child — noun use)
#   "She snapped a photo."                (dobj — verb use)
#   "Pop culture moved on."               (compound — next token NOUN)
# -----------------------------------------------------------------------------
def rule_sfx_beat(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.lower_ not in SFX_WORDS:
            continue
        blocked_child_deps = ({"det", "poss", "amod", "compound", "nummod",
                               "dobj", "obj", "nsubj", "nsubjpass"}
                              | PARTICLE_DEPS)
        if any(c.dep_ in blocked_child_deps for c in t.children):
            continue
        if t.dep_ == "compound":
            continue
        nxt_i = t.i + 1
        if nxt_i < len(doc) and doc[nxt_i].pos_ in {"NOUN", "PROPN"}:
            continue
        if t.i > 0:
            prv = doc[t.i - 1]
            if prv.pos_ in {"DET", "ADJ", "NUM", "ADP", "PART"}:
                continue
            if prv.tag_ in {"PRP$", "POS"}:
                continue
        # split BEFORE the SFX word
        if t.i > 0:
            out.add(t.i)
        # split AFTER it (comma clings back)
        if nxt_i < len(doc):
            if doc[nxt_i].text == ",":
                out.add(nxt_i + 1)
            elif not doc[nxt_i].is_punct:
                out.add(nxt_i)
    return out


# -----------------------------------------------------------------------------
# RULE 61 — STRONG VERB INTRODUCING A LIST  (v18.5)
# "the blaze devoured | temples, | villas, | and entire districts."
# Rule 15 splits BETWEEN the list items but never separates the introducing
# VERB from item one — the verb clause stayed glued to the first item
# ("...devoured temples,"), hiding the verb beat and polluting grid cell 1.
# Detection is PART-OF-SPEECH based on purpose: ANY verb that isn't in
# WEAK_VERB_LEMMAS qualifies.  No whitelist of "action verbs" to fall out of
# date — 'devoured' works because it parses as a strong VERB, not because
# someone remembered to add it to a list.
#
# FIRE:      strong VERB, then (det/adj/num)* NOUN(s) ',' (det/adj/num)* NOUN
#            → split immediately after the verb
# DON'T FIRE: weak verb ("was", "included"); no comma noun-run following
# -----------------------------------------------------------------------------
def rule_verb_list_reveal(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "VERB" or t.lemma_.lower() in WEAK_VERB_LEMMAS:
            continue
        j = t.i + 1
        k, hops = j, 0
        while k < len(doc) and hops < 3 and doc[k].pos_ in {"DET", "ADJ", "NUM"}:
            k += 1
            hops += 1
        if k >= len(doc) or doc[k].pos_ not in {"NOUN", "PROPN"}:
            continue
        m = k                                   # absorb compound nouns
        while m + 1 < len(doc) and doc[m + 1].pos_ in {"NOUN", "PROPN"}:
            m += 1
        if m + 1 >= len(doc) or doc[m + 1].text != ",":
            continue
        n2, hops = m + 2, 0                     # a second item must follow
        while n2 < len(doc) and hops < 3 and doc[n2].pos_ in {"DET", "ADJ", "NUM"}:
            n2 += 1
            hops += 1
        if n2 < len(doc) and doc[n2].pos_ in {"NOUN", "PROPN"}:
            out.add(j)
    return out


# =============================================================================
# ANTI-RULES  —  return indices to *remove* from the splits set.
# =============================================================================

# A — never split inside a multi-token named entity ("New York City").
def anti_rule_compound_ne(doc: Doc, splits: Set[int]) -> Set[int]:
    """Wipe splits that would slice a multi-token named entity.

    EXCEPTION: approximator + number entities ("nearly ten", "almost a hundred",
    "about three thousand", "over forty") are tagged as single entities by
    spaCy, but the approximator + number boundary is a deliberate reveal
    target — splitting between them is the whole point of rule_numeric_approximator_reveal.
    """
    # APPROX_LEMMAS → APPROXIMATOR_WORDS (the same set the approximate-amount rule uses).
    # → moved to shared_text_logic.py, SECTION 2.3 (imported at the top of this file).
    bad = set()
    for i in splits:
        if not _in_compound_ne(doc, i):
            continue
        # Check: is the LEFT side of the split an approximator ADV/ADP?
        if i > 0 and doc[i - 1].lower_ in APPROX_LEMMAS \
                and doc[i - 1].pos_ in {"ADV", "ADP"}:
            # Preserve the split — this is a numeric-approximator reveal.
            continue
        bad.add(i)
    return bad


# B — never split between aux/neg and main verb ("doesn't find", "is going").
def anti_rule_aux_main_verb(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]

        if left.dep_ in AUX_LIKE_DEPS and right.pos_ in {"VERB", "AUX"}:
            # NEVER merge infinitives! "to calibrate" must stay split.
            if left.lower_ == "to" and left.dep_ == "aux":
                continue
            bad.add(i)





        if left.pos_ == "AUX" and right.pos_ in {"VERB", "AUX"}:
            # EXCEPTION: any copular (non-aux) lemma before a VBG is a
            # gerund-predicate reveal, not a progressive aspect.
            # e.g. "His goal is | remaining focused", "feels | calming"
            if (left.lemma_.lower() in ALL_COPULA_LEMMAS
                    and left.dep_ not in {"aux", "auxpass"}
                    and right.tag_ == "VBG"):
                continue
            bad.add(i)
    return bad




# C — never split inside a hyphenated compound ("self-driving", "hyper-arid").
def anti_rule_hyphen_compound(doc: Doc, splits: Set[int]) -> Set[int]:
    return {i for i in splits if _in_hyphen_compound(doc, i)}


# D — never split a contraction or possessive marker from its host word.
#     A token containing an apostrophe (straight ', curly ’, ‘, or backtick)
#     is glued ONLY when the left token has no trailing whitespace — i.e.
#     the two tokens were one orthographic word in the source.  This catches:
#       isn't → "is" + "n't"            (no whitespace between → glue)
#       Rome's → "Rome" + "'s"          (no whitespace between → glue)
#       they're → "they" + "'re"
#     But it does NOT block legitimate splits like:
#       'isn't | "drive or not."'       ("n't" + " " + opening-quote → KEEP)
#     because there IS whitespace between "n't" and the opening quote.
#
#     Detection is purely structural: scan each token's text for any
#     apostrophe character AND verify the tokens were unspaced in source.
def anti_rule_possessive(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    # APOS → APOSTROPHES.
    # → moved to shared_text_logic.py, SECTION 2.1 (imported at the top of this file).

    def _has_apos(s: str) -> bool:
        return any(c in APOS for c in s)

    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left = doc[i - 1]
        right = doc[i]
        # Only a contraction/possessive boundary if the tokens were ONE
        # orthographic word (no whitespace between left and right).
        if left.whitespace_ != "":
            continue
        if _has_apos(left.text) or _has_apos(right.text):
            bad.add(i)
    return bad


# E — never split before a phrasal-verb particle ("sped past", "looked up").
def anti_rule_phrasal_particle(doc: Doc, splits: Set[int]) -> Set[int]:
    return {i for i in splits if 0 < i < len(doc) and doc[i].dep_ in PARTICLE_DEPS}


# F — never split inside a numeric/measure unit ("1,110 pounds", "19th century").
def anti_rule_numeric_unit(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.ent_type_ in NUMERIC_ENTS and right.ent_iob_ == "I":
            bad.add(i)
        # "<NUM> <NOUN>" pairs with compound/nummod/nmod dep
        if left.like_num and right.pos_ == "NOUN" and right.dep_ in {"compound", "nummod", "nmod"}:
            bad.add(i)
    return bad


# G — never split DET (or NUM acting as determiner) from its head noun/adj/num.
#     Examples kept intact: "the cat", "a dog", "the second", "one link",
#     "two birds", "three thousand".
def anti_rule_det_head(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.pos_ == "DET" and right.pos_ in {"NOUN", "PROPN", "ADJ", "NUM"}:
            bad.add(i)
        # NUM functioning as a determiner ("one link", "two birds", "five years")
        if (left.pos_ == "NUM" and left.dep_ in {"nummod", "det"}
                and right.pos_ in {"NOUN", "PROPN", "ADJ"}):
            bad.add(i)
    return bad


# H — never split inside a quoted span (between matched quotes).
def anti_rule_inside_quote(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    open_idx = None
    for t in doc:
        if t.text in OPEN_QUOTES and open_idx is None:
            open_idx = t.i + 1
        elif t.text in CLOSE_QUOTES and open_idx is not None:
            for i in range(open_idx, t.i + 1):
                bad.add(i)
            open_idx = None
    return bad


# I — never split inside matched brackets.
def anti_rule_inside_bracket(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    open_idx = None
    for t in doc:
        if t.text in OPEN_BRACKETS and open_idx is None:
            open_idx = t.i + 1
        elif t.text in CLOSE_BRACKETS and open_idx is not None:
            for i in range(open_idx, t.i + 1):
                bad.add(i)
            open_idx = None
    return bad


# J — never split a frozen bigram ("what if", "as if", "used to", ...).
def anti_rule_frozen_bigram(doc: Doc, splits: Set[int]) -> Set[int]:
    return {i for i in splits if _is_frozen_bigram_split(doc, i)}


# K — never split between adjective and the noun it modifies.
def anti_rule_adj_noun(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.pos_ == "ADJ" and left.dep_ == "amod" \
                and right.pos_ in {"NOUN", "PROPN"} and left.head == right:
            bad.add(i)
    return bad


# L — never split between "to" and an infinitive ("to go", "to revolutionize").
# EXCEPTION: if the sentence is long enough and there's substantial
# lead-in and tail content, allow the split (this is the visual reveal
# pattern caught by rule_infinitive_split).
def anti_rule_to_infinitive(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.lower_ == "to" and left.dep_ == "aux" and right.pos_ == "VERB":
            # Existing: long-sentence visual reveal exception
            sent_ntok = sum(1 for x in left.sent if not x.is_punct)
            if sent_ntok >= INFINITIVE_SPLIT_SENT_MIN:
                prev = _prev_split(splits, left.i)
                lead_content = _content_count(doc, prev, left.i)
                sent_end = left.sent.end
                tail_end = left.i
                while tail_end < sent_end and doc[tail_end].text not in HARD_PUNCT:
                    tail_end += 1
                tail_content = _content_count(doc, left.i, tail_end)
                if lead_content >= INFINITIVE_SPLIT_LEAD_MIN and tail_content >= INFINITIVE_SPLIT_TAIL_MIN:
                    continue
            # NEW: result-clause "too X to" / "enough to" exception
            lookback_start = max(left.sent.start,
                                  left.i - RESULT_INTENSIFIER_LOOKBACK)
            has_too_enough = False
            for j in range(left.i - 1, lookback_start - 1, -1):
                jt = doc[j]
                if jt.text in HARD_PUNCT:
                    break
                if jt.lower_ in RESULT_TO_INTENSIFIERS:
                    has_too_enough = True
                    break
            if has_too_enough:
                continue

            # NEW: equation phrasal verb "translates to VERB" etc.
            # If the verb upstream within 3 tokens is an equation phrasal
            # verb whose required particle is "to", the "to + VERB" here
            # is part of the equation phrase, not a plain infinitive.
            equation_phrasal = False
            for j in range(max(left.sent.start, left.i - 3), left.i):
                jt = doc[j]
                if (jt.pos_ == "VERB"
                        and jt.lemma_.lower() in EQUATION_PHRASAL_PARTICLES
                        and "to" in EQUATION_PHRASAL_PARTICLES[jt.lemma_.lower()]):
                    equation_phrasal = True
                    break
            if equation_phrasal:
                continue

            bad.add(i)
    return bad


# M — never split inside a numeric range ("9:30", "1-2") that spaCy tokenized
#     into multiple tokens.
def anti_rule_numeric_range(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.text == ":" and left.i > 0 and doc[left.i - 1].like_num and right.like_num:
            bad.add(i)
    return bad


# N — never split immediately BEFORE a comma — punctuation must stay attached
#     to the preceding word.  Comma-splits are always handled AFTER the comma.
def anti_rule_no_split_before_comma(doc: Doc, splits: Set[int]) -> Set[int]:
    return {i for i in splits if 0 < i < len(doc) and doc[i].text == ","}


# O — never split between a currency symbol and the immediately following number.
#     Catches "$" + "800,000" even when spaCy's NER misses the MONEY label.
def anti_rule_currency_glued(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if 0 < i < len(doc) and doc[i - 1].text in CURRENCY_SYMS and doc[i].like_num:
            bad.add(i)
    return bad


# P — never split between a number and its measurement word ("15 meters",
#     "40 million", "3 thousand years", "1,110 pounds").
def anti_rule_num_unit(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.like_num and right.like_num:
            bad.add(i)
        if left.like_num and right.lower_ in MEASURE_NOUNS:
            bad.add(i)
        # also catches "thousand years" / "million dollars" pairs
        if left.lower_ in MEASURE_NOUNS and right.lower_ in MEASURE_NOUNS:
            bad.add(i)
    return bad


# Q — never split between PART/negation and what it modifies ("not near",
#     "no longer", "almost no").  Keeps tiny modifier glued to its head.
def anti_rule_neg_modifier(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        if doc[i - 1].lower_ in {"not", "no", "never", "n't"}:
            # NEVER wipe a punctuation-driven split.  "isn't | \"drive..."
            # / "didn't | ... | really happen" — the writer's punctuation
            # is intentional.  Fixes #142.
            if _is_big_punct_split(doc, i):
                continue
            bad.add(i)
    return bad


# R — never split between two adjacent NOUN/PROPN tokens.  This is the
#     pragmatic catch-all for compound nouns: spaCy doesn't always tag the
#     left as `compound`, so we glue ALL adjacent NOUN/PROPN pairs instead
#     of relying on the dep label.  Examples kept intact:
#       "skull fragments", "space agencies", "cost savings",
#       "kitchen cupboard", "Jacob Marley", "salt flats", "Jacob Marley",
#       "Banda Islands", "Christopher Columbus", "Cinnamon Sri Lanka".
def anti_rule_compound_noun(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.pos_ in {"NOUN", "PROPN"} and right.pos_ in {"NOUN", "PROPN"}:
            bad.add(i)
    return bad


# S — never split inside markdown emphasis: *word*, _word_, **word**, __word__.
#     spaCy may tokenise "*brilliant*" as ["*", "brilliant", "*"] or even
#     "**tethered goat**" as ["*", "*", "tethered", "goat", "*", "*"], so a
#     naive splitter can produce "* | brilliant | *,".  We detect ANY span
#     bounded by matching emphasis markers and forbid splits inside it.
#     Markers detected: * ** _ __  (and trailing punctuation after the closer).
def anti_rule_markdown_emphasis(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    # EMPH_CHARS → MARKDOWN_EMPHASIS_CHARS.
    # → moved to shared_text_logic.py, SECTION 2.1 (imported at the top of this file).

    def _is_emph(tok: Token) -> bool:
        s = tok.text
        return bool(s) and all(c in EMPH_CHARS for c in s)

    open_idx: Optional[int] = None
    for tok in doc:
        if _is_emph(tok):
            if open_idx is None:
                open_idx = tok.i
            else:
                # we have a matching closer — forbid all splits in [open_idx+1, tok.i+1]
                for i in range(open_idx + 1, tok.i + 1):
                    bad.add(i)
                open_idx = None
    return bad


# T — short sentences (≤ SHORT_SENT_NO_SPLIT non-punct tokens) read as a
#     single visual line; don't split inside them.  Catches:
#       "That island was Manhattan."          (4 words)
#       "It costs something much more valuable." (6 words)
#       "Driving was draining my soul."       (5 words)
#       "The busses took 4 hours."            (5 words)
#       "Things appeared lighter."            (3 words)
#     The guard only fires when the *entire* sentence has a content load
#     small enough to read at a glance.
#
#     EXCEPTION: a split that falls IMMEDIATELY AFTER an ellipsis ("...", "…")
#     is preserved even in a tiny sentence, because ellipses are inherently
#     dramatic line-breaks ("Yep... done.", "You're just... aware.").
def anti_rule_short_sentence(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for sent in doc.sents:
        # count non-punct tokens
        ntok = sum(1 for t in sent if not t.is_punct)
        if ntok > SHORT_SENT_NO_SPLIT:
            continue
        # Identify the last content noun in the sentence — if the sentence
        # ends with [AUX/VERB] + ADP + NOUN[.], a split BEFORE the final
        # noun is the "reveal what it's about" pattern and should NOT be
        # wiped.  Fixes #67 "...isn't about price- | it's about | transitions."
        last_noun_i: Optional[int] = None
        for t in reversed(list(sent)):
            if t.is_punct or t.is_space:
                continue
            if t.pos_ in {"NOUN", "PROPN"}:
                last_noun_i = t.i
                break
            else:
                break  # last content tok isn't a noun → pattern fails
        copula_pp_reveal_ok = False
        if last_noun_i is not None:
            # walk back from the noun looking for ADP + AUX/VERB pattern
            j = last_noun_i - 1
            # skip DET / ADJ between the noun and the ADP
            while j > sent.start and doc[j].pos_ in {"DET", "ADJ"}:
                j -= 1
            if j > sent.start and doc[j].pos_ == "ADP":
                k = j - 1
                # skip ADV between ADP and verb ("is really about X")
                while k > sent.start and doc[k].pos_ == "ADV":
                    k -= 1
                if k >= sent.start and doc[k].pos_ in {"AUX", "VERB"}:
                    # there must be ≥ AUX_PP_REVEAL_LEAD_MIN tokens of lead
                    # before the noun, otherwise it's just "it's about X"
                    # with no build-up.
                    lead_content = sum(1 for x in sent
                                       if x.i < last_noun_i and not x.is_punct)
                    if lead_content >= AUX_PP_REVEAL_LEAD_MIN:
                        copula_pp_reveal_ok = True
        for i in splits:
            if not (sent.start < i < sent.end):
                continue
            # PRESERVE: split adjacent to BIG (non-comma) punctuation —
            # ellipsis, dash, hard-punct, quote, bracket.  Big punctuation
            # always marks a deliberate visual break by the writer, so
            # splits around it are never wiped, even in short sentences.
            # Fixes #81 "Which... | sounds familiar.", #112 "But Together...
            # | mayyyybe not.", #178 "You're just... | aware.".
            if _is_big_punct_split(doc, i):
                continue
            # PRESERVE: split before last noun in `is + ADP + NOUN` pattern
            if copula_pp_reveal_ok and last_noun_i is not None:
                # the "before-noun" split is the noun's chunk-start; allow
                # ANY split that lands ≥ position (last_noun_i - 1) up to last_noun_i
                # (covers `before "transitions"` whether tokenizer offers
                #  DET at noun-1 or not).
                if last_noun_i - 1 <= i <= last_noun_i:
                    continue
            bad.add(i)
    return bad


# U — never split between two adjacent verb tokens.
#     spaCy may parse "start getting", "keeps happening", "comes from",
#     "stops feeling" with the second verb as xcomp/ccomp/advcl/conj/prep —
#     too many cases to enumerate positively, so this anti-rule catches them
#     all structurally.  Visual-typography reading prefers verb chains
#     to flow as one line.
def anti_rule_verb_to_verb(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.pos_ in {"VERB", "AUX"} and right.pos_ in {"VERB", "AUX"}:
            # EXCEPTION: if the right verb is an amod (participial adjective
            # like "known reference surfaces"), don't glue. It's a reveal NP!
            if right.dep_ == "amod":
                continue
            # EXCEPTION: if the right verb is followed by a NOUN, it's
            # likely a participial adjective ("missing people", "calibrated tools").
            if right.i + 1 < len(doc) and doc[right.i + 1].pos_ in {"NOUN", "PROPN"}:
                continue

            # EXCEPTION: any copular (non-aux) lemma before VBG — gerund-
            # predicate reveal.  Covers "remains | inspiring",
            # "feels | calming", "stays | running", etc.
            if (left.lemma_.lower() in ALL_COPULA_LEMMAS
                    and left.dep_ not in {"aux", "auxpass"}
                    and right.tag_ == "VBG"):
                continue
            bad.add(i)
    return bad


# V — never split between a verb and a following demonstrative determiner /
#     pronoun ("makes that clearer", "tells them everything", "knows what
#     you want").  These are clausal complements that read as one phrase.
def anti_rule_verb_to_dem_pron(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        # all PRON/DET dependents of the verb stay glued

        if left.pos_ in {"VERB", "AUX"} and right.pos_ in {"PRON", "DET"} \
                and right.head == left:
            # EXCEPTION: possession + creation + perception family verbs
            # with substantial dobj — the dobj-starting DET/PRON belongs
            # to the reveal NP, not the verb.
            ALL_REVEAL_VERB_LEMMAS = (ALL_POSSESSION_LEMMAS
                                       | ALL_CREATION_LEMMAS
                                       | ALL_PERCEPTION_LEMMAS
                                       | ALL_EQUATION_LEMMAS)
            if (left.lemma_.lower() in ALL_REVEAL_VERB_LEMMAS
                    and left.dep_ not in {"aux", "auxpass"}
                    and _is_substantial_dobj(left)):
                continue
            bad.add(i)
        # additional: explicit demonstrative tag DT/WDT after a verb, but
        # only if the DET actually modifies the verb (right.head == left).
        # Articles ("a", "the") starting a direct object (e.g. "created a new...")
        # have their head in the noun, NOT the verb, so we allow the split.
        elif left.pos_ in {"VERB", "AUX"} and right.tag_ in {"DT", "WDT"}:
            if right.head == left:
                bad.add(i)
    return bad


# W — never split if it would orphan a tiny measurement-tail like
#     "over time", "for years", "in seconds", "by minutes" — these read
#     poorly as a standalone visual chunk and almost always belong on the
#     previous line.
#
#     Detection: from split point i, walk forward to the next HARD_PUNCT.
#     If the intervening content tokens are ≤ 2 AND end with a token in
#     MEASURE_NOUNS AND a preposition appears in the tail, forbid the split.
#
#     Examples preserved (split forbidden):
#       "...poisoned large groups of marine animals repeatedly | over time."
#                                                          ^^^^^^^^^^^^
#       "...trying to do this | for years."
#                            ^^^^^^^^^^^
def anti_rule_orphan_measure_tail(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        # walk from i to next hard-punct (or end of doc)
        j = i
        while j < len(doc) and doc[j].text not in HARD_PUNCT:
            j += 1
        tail = doc[i:j]
        content = [x for x in tail if not x.is_punct and not x.is_space]
        if not content or len(content) > 2:
            continue
        # last content token must be a measure noun
        if content[-1].lower_ not in MEASURE_NOUNS:
            continue
        # CASE A: a preposition appears IN the tail before the measure noun.
        # (e.g. split at "X over time" → tail is ["over", "time"])
        if any(x.pos_ == "ADP" for x in content[:-1]):
            bad.add(i)
            continue
        # CASE B: the immediately preceding token (just before the split)
        # is an ADP.  (e.g. split at "X over | time." → tail is ["time."],
        # but the ADP "over" sits just to the left of the split.)  This
        # catches the common case where the split point falls BETWEEN the
        # prep and its measure-noun object.
        if i > 0 and doc[i - 1].pos_ == "ADP":
            bad.add(i)
            continue
    return bad


# X — never produce a chunk that lacks visualisable content.  A chunk is
#     "visualisable" when it has at least one NOUN / PROPN / NUM / ADJ /
#     concrete-VERB (excluding copulas: be/have/do/get/seem/become).
#     Connective fragments like "It's", "that many of", "and then the"
#     have no visualisable content — they fail this test.
#
#     This anti-rule walks splits in order, identifies non-visualisable
#     chunks, and removes the split that would isolate them so they
#     glue forward into the next chunk.  Length cap raised to 6 words
#     so phrases like "and that is just" / "but it's how it" get caught.
#
#     Fixes #81 "that many of", #175 "It's", #81-2 "That's", etc.
def anti_rule_content_starved(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    idx = sorted(splits)
    for k in range(len(idx) - 1):
        lo, hi = idx[k], idx[k + 1]
        span = doc[lo:hi]
        text = span.text.strip()
        if not text:
            continue
        if len(text.split()) > 6:
            continue
        if _has_visualisable_content(doc, lo, hi):
            continue
        # Big-punct protection on either boundary
        if _is_big_punct_split(doc, lo) or _is_big_punct_split(doc, hi):
            continue
        last_nonspace = None
        for t in reversed(list(span)):
            if not t.is_space:
                last_nonspace = t
                break
        if last_nonspace is not None and last_nonspace.text in HARD_PUNCT:
            continue

        # NEW: preserve split if the NEXT chunk is a substantial reveal
        # (≥3 content tokens).  Allows "She had | a strange recurring
        # dream..." to keep the boundary even though "She had" is weak.
        # The weak lead is justified by the strong reveal payload.
        if k + 1 < len(idx) - 0:
            next_lo = idx[k + 1]
            next_hi = idx[k + 2] if k + 2 < len(idx) else len(doc)
            if _content_count(doc, next_lo, next_hi) >= 3:
                continue

        if lo > 0:
            bad.add(lo)
        elif hi < len(doc):
            bad.add(hi)
    return bad

# Y — never split immediately BEFORE an SCONJ mid-sentence.
# SCONJs like "because", "if", "while" should cling to the previous line
# as a cliffhanger. This anti-rule removes splits right before SCONJs,
# allowing rule_clause_starters to place the split AFTER the SCONJ instead.
#
# EXCEPTION: pattern (h) of rule_copula_reveal_split intentionally splits
# BEFORE "that" / WH-clauses when preceded by a copular complement chain
# ("remained convinced that...", "became clear what..."). Detect that
# pattern and preserve the split.
def anti_rule_split_before_sconj(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i >= len(doc):
            continue
        tok = doc[i]
        if tok.pos_ != "SCONJ":
            continue
        # Don't remove if at start of sentence (after hard punct)
        if i > 0 and doc[i-1].text in HARD_PUNCT:
            continue
        bad.add(i)
    return bad

# =============================================================================
# CHUNK BUILDING & SMART MERGE LOGIC
# =============================================================================

def _build_raw_chunks(doc: Doc, splits: Set[int]) -> List[Tuple[int, int]]:
    idx = sorted(splits)
    return [(idx[i], idx[i + 1]) for i in range(len(idx) - 1)]


def _is_throwaway_span(doc: Doc, lo: int, hi: int) -> bool:
    """
    A chunk is "throwaway" (will be glued to a neighbour) when:
      • it's very short (< 3 words), AND
      • it has no content word (NOUN / PROPN / NUM / VERB / ADJ), AND
      • it doesn't end with hard punctuation or a comma.

    EXCEPTION: a single ADV/ADJ flanked by long dashes (em/en/double) is the
    parenthetical visual content — NEVER throwaway, even though it lacks a
    canonical content POS.  Fixes #191 "If the answer is yes — | consistently
    | — then it's brilliant." so `consistently` stands alone.
    """
    span = doc[lo:hi]
    text = span.text.strip()
    if not text:
        return True
    # EXCEPTION for parenthetical ADV/ADJ between long dashes:
    if (len(span) == 1 and span[0].pos_ in {"ADV", "ADJ"}
            and lo > 0 and hi < len(doc)
            and doc[lo - 1].text in LONG_DASH_PUNCT
            and doc[hi].text in LONG_DASH_PUNCT):
        return False
    if len(text.split()) >= 3:
        return False
    if text[-1] in HARD_PUNCT | {","}:
        return False
    return not any(t.pos_ in {"NOUN", "PROPN", "NUM", "VERB", "ADJ"} for t in span)


def _throwaway_direction(doc: Doc, lo: int, hi: int) -> str:
    """
    Decide which side a throwaway span should glue to.

    Strategy: glue to whichever side contains the span's grammatical HEAD.
    Examples:
      "...the patient dog | all | walked along..."
          'all' has head 'walked' (right) → fwd
      "the curious child | and | the patient dog"
          'and' is conj of 'child' (left) → bwd

    Special cases:
      • Single AUX/copula ("is", "was", "are"): always go FORWARD because it
        introduces a predicate — fixes "whale fossils… | is | a desert" being
        glued backward into "whale fossils… is".

      • Tiny ADV/ADJ span sitting between a preceding dash and a following
        opening-quote / dash: prefer BACKWARD so the introducer ("literally",
        "consistently") attaches to its dashed parenthetical's leading edge.
        Fixes "Wadi Al-Hitan in Egypt — | literally | "Valley of the
        Whales" — looks…" so that "literally" joins the previous chunk.
    """
    span = doc[lo:hi]
    if len(span) == 0:
        return "bwd"
    # SPECIAL: single AUX tokens always introduce a predicate to the right
    if len(span) == 1 and span[0].pos_ == "AUX":
        return "fwd"

    # SPECIAL: single SCONJ ('as', 'while', 'because', 'though', 'if', etc.)
    # acts as a cliffhanger, attaching to the end of the previous line.
    if len(span) == 1 and span[0].pos_ == "SCONJ":
        return "bwd"
    # SPECIAL: single ADP tokens act as cliffhangers, attaching to the
    # end of the previous line.  e.g. "across" / "in" / "from".
    # When a prep is isolated on its own line, it introduces the next
    # visual shot, so it belongs at the end of the previous line.
    if len(span) == 1 and span[0].pos_ == "ADP":
        return "bwd"

    # SPECIAL: ADV/ADJ between dash-on-left and quote/dash-on-right → bwd
    if (len(span) == 1 and span[0].pos_ in {"ADV", "ADJ"}
            and lo > 0 and hi < len(doc)
            and doc[lo - 1].text in DASH_PUNCT
            and (doc[hi].text in OPEN_QUOTES or doc[hi].text in DASH_PUNCT)):
        return "bwd"
    root = span.root
    head = root.head
    if head is root or head.i == root.i:
        return "bwd"
    if head.i >= hi:
        return "fwd"
    if head.i < lo:
        return "bwd"
    return "bwd"


def _fuse_orphans(doc: Doc, chunks: ChunkMap, chunk_spans: List[Tuple[int, int]],
                  protected: Optional[Set[int]] = None) -> Tuple[ChunkMap, List[Tuple[int, int]]]:
    """
    Post-merge pass that fixes orphan single-content-word lines.

    KEY DESIGN: this pass works structurally (POS / DEP) on the underlying
    spaCy tokens, NOT on hardcoded word lists.  That way it generalises to
    any vocabulary.

    NEW: never fuses across PROTECTED boundaries — those are big-punctuation
    splits (ellipsis, dash, colon, etc.) that must remain inviolate.  Any
    fusion candidate is rejected if the boundary between the two chunks
    being fused is in `protected`.

    The chunks list and the chunk_spans list are aligned: chunk_spans[k]
    gives the (lo, hi) token range that produced chunks[k] (post-merge).
    `chunks` is the ordered chunk-map; every fusion below goes through
    `merge_chunks`, so the two entries' id-lists are concatenated.

    Patterns we fuse:

      (1) Single-NOUN/PROPN chunk followed by a chunk whose first token is
          a relative pronoun (WDT/WP/WRB).  Fuses them into one line.
            ['regions', 'that are now brutally dry']
              → ['regions that are now brutally dry']

      (2) Single content-word chunk where the PREVIOUS chunk ends with an
          ADP (preposition) or PART (e.g. "to") whose grammatical head is
          INSIDE this chunk.  Fuses backward.
            ['It costs', 'about two dollars.']  (cost takes 'about ...' as obj)
              → ['It costs about two dollars.']

      (3) Single-VERB (gerund/participle) chunk followed by a NP chunk where
          the VERB's head is inside that NP.  Fuse forward.
            ['revealing', 'evidence of...']
              → ['revealing evidence of...']

      (4) Single bare-DET-only chunks ("the", "a", "an") merge forward.

      (5) Tiny chunks consisting entirely of orphaned punctuation (e.g. "...",
          "—", "..") merge backward into the previous chunk.  Fixes
          "If it's \"maybe\" / ... / it won't be." → "If it's \"maybe\"... / ..."
          EXCEPT when the trailing boundary is protected (would erase a
          big-punct split).
    """
    if protected is None:
        protected = set()
    if len(chunks) <= 1:
        return chunks, chunk_spans

    # REL_TAGS → RELATIVE_PRONOUN_TAGS (the wh-tag set) and PUNCT_ONLY_RE.
    # → moved to shared_text_logic.py, SECTION 2.2 + 2.13 (imported at the top of this file).

    def _content_tokens(lo: int, hi: int) -> List[Token]:
        return [t for t in doc[lo:hi] if not t.is_punct and not t.is_space]

    def _all_punct(lo: int, hi: int) -> bool:
        text = doc[lo:hi].text.strip()
        return bool(text) and bool(PUNCT_ONLY_RE.match(text))

    def _last_content_tok(lo: int, hi: int) -> Optional[Token]:
        toks = _content_tokens(lo, hi)
        return toks[-1] if toks else None

    def _first_content_tok(lo: int, hi: int) -> Optional[Token]:
        toks = _content_tokens(lo, hi)
        return toks[0] if toks else None

    out: ChunkMap            = []
    out_spans: List[Tuple[int, int]] = []
    i = 0
    while i < len(chunks):
        cur = chunks[i]
        cur_text = cur.text
        cur_lo, cur_hi = chunk_spans[i]
        cur_content = _content_tokens(cur_lo, cur_hi)

        # ----- pattern (5): pure-punctuation orphan glues backward -----
        # Always — even if the previous chunk ends in . ! ?  An ellipsis or
        # dash on its own line is never desirable; attach it to whatever came
        # before.  Fixes 'If it\'s "maybe" / ... / it won\'t be.' becoming
        # 'If it\'s "maybe"... / it won\'t be.'
        # NEW guard: don't fuse if `cur_lo` is a protected boundary (would
        # erase the inviolate split).
        if _all_punct(cur_lo, cur_hi) and cur_lo not in protected:
            if out:
                # if previous ends in sentence-final punct, append after that
                # punct (e.g. "Yep." + "..." → "Yep...")
                out[-1] = merge_chunks(out[-1], cur, sep="", rule=1002)
                out_spans[-1] = (out_spans[-1][0], cur_hi)
                i += 1
                continue
            # otherwise (orphan at start) skip the orphan
            i += 1
            continue

        # ----- pattern (1): single-noun then relative pronoun -----
        # Skip if the boundary between this chunk and the next is protected.
        if (i + 1 < len(chunks)
                and len(cur_content) == 1
                and cur_content[0].pos_ in {"NOUN", "PROPN"}
                and not cur_text.rstrip().endswith((".", "!", "?", ":", ";"))
                and cur_hi not in protected):
            nxt_lo, nxt_hi = chunk_spans[i + 1]
            first = _first_content_tok(nxt_lo, nxt_hi)
            if first is not None and first.tag_ in REL_TAGS:
                out.append(merge_chunks(cur, chunks[i + 1], sep=" ", rule=1003))
                out_spans.append((cur_lo, nxt_hi))
                i += 2
                continue

        # ----- pattern (2): orphaned-after-prep — previous chunk ends with
        #       an ADP/PART whose head lives INSIDE this chunk -----
        # Skip if the boundary at cur_lo is protected, OR if the next chunk
        # starts with a location entity (the deliberate location-reveal
        # from rule_prep_object_reveal: "Alvord Desert in | Oregon feels").
        if (out and len(cur_content) <= 2
                and not cur_text.rstrip().endswith((".", "!", "?", ":", ";"))
                and cur_lo not in protected):
            prev_lo, prev_hi = out_spans[-1]
            prev_last = _last_content_tok(prev_lo, prev_hi)
            first_cur = _first_content_tok(cur_lo, cur_hi)
            is_loc_reveal = (
                prev_last is not None
                and prev_last.pos_ == "ADP"
                and first_cur is not None
                and first_cur.ent_type_ in {"GPE", "LOC", "FAC"}
            )
            if (not is_loc_reveal
                    and prev_last is not None
                    and prev_last.pos_ in {"ADP", "PART"}
                    and any(prev_last.head.i == t.i or prev_last.head in t.subtree
                            for t in doc[cur_lo:cur_hi])):
                out[-1] = merge_chunks(out[-1], cur, sep=" ", rule=1004)
                out_spans[-1] = (prev_lo, cur_hi)
                i += 1
                continue

        # ----- pattern (3): single-VERB chunk then its head's NP -----
        if (i + 1 < len(chunks)
                and len(cur_content) == 1
                and cur_content[0].pos_ == "VERB"
                and not cur_text.rstrip().endswith((".", "!", "?", ":", ";"))):
            nxt_lo, nxt_hi = chunk_spans[i + 1]
            verb = cur_content[0]
            # if any child of the verb lives in the next chunk, fuse
            # Guard: don't fuse across a protected boundary
            if any(nxt_lo <= c.i < nxt_hi for c in verb.children) \
                    and cur_hi not in protected:
                out.append(merge_chunks(cur, chunks[i + 1], sep=" ", rule=1005))
                out_spans.append((cur_lo, nxt_hi))
                i += 2
                continue

        # ----- pattern (4): bare DET-only chunk glues forward -----
        # Guard: don't fuse across a protected boundary
        if (i + 1 < len(chunks)
                and len(cur_content) == 1
                and cur_content[0].pos_ == "DET"
                and cur_hi not in protected):
            nxt_lo, nxt_hi = chunk_spans[i + 1]
            out.append(merge_chunks(cur, chunks[i + 1], sep=" ", rule=1006))
            out_spans.append((cur_lo, nxt_hi))
            i += 2
            continue

        out.append(cur)
        out_spans.append((cur_lo, cur_hi))
        i += 1
    return out, out_spans


def _post_merge_unvisualisable(doc: Doc,
                               chunks: ChunkMap,
                               chunk_spans: List[Tuple[int, int]],
                               protected: Set[int]) -> Tuple[ChunkMap, List[Tuple[int, int]]]:
    """
    Final pass: any chunk that is NOT visualisable on its own gets re-glued
    to a neighbour. A "visualisable" chunk has at least one NOUN / PROPN /
    NUM / ADJ / concrete-VERB (see `_has_visualisable_content`).

    For SUBSTANTIVE discourse orphans (≥3 non-punct tokens) — phrases like
    "but what if", "and here is", "yeah but really" — two extra moves
    kick in, both designed to expose the downstream noun reveal:

      • STEAL: a leading DET ("the", "a", "this", "every") is taken from
        the next chunk and appended to the orphan, so the next chunk
        starts on its first content token.
            "but what if" | "the other person and"
              becomes
            "but what if the" | "other person and"

      • CROSS-PROTECTED BACKWARD MERGE: with no visualisable content of
        its own, the orphan can safely cross a protected sentence
        boundary to glue onto the end of the previous chunk.
            "X.  but what if the" → previous-line tail

    Short orphans (1-2 non-punct tokens like "It's", "and", "But") keep
    their existing behavior: no stealing, no protected-boundary crossing.

    Iterates up to 10 passes so chained orphans collapse.

    Gluing direction:
        • prefer BACKWARD (attach to the END of the previous chunk)
        • if there is no previous chunk OR the left boundary is protected
          (and the orphan isn't substantive), glue FORWARD
        • if BOTH boundaries are protected, leave alone

    `chunks` is the ordered chunk-map.  Every glue routes through
    `merge_chunks` (concatenating the two entries' id-lists).  The DET-steal
    is a boundary shift rather than a split or merge, so each of the two
    affected lines simply keeps its own ids while only its text is re-derived.
    """
    if len(chunks) <= 1:
        return chunks, chunk_spans

    DEBUG_PMV = False
    if DEBUG_PMV:
        print(f"  [post-merge-unvis] INPUT chunks:")
        for i, (c, (lo, hi)) in enumerate(zip(chunks, chunk_spans)):
            vis = _has_visualisable_content(doc, lo, hi)
            print(f"    [{i}] '{c.text}' span={lo}:{hi} visualisable={vis} "
                  f"lo_protected={lo in protected} hi_protected={hi in protected}")

    for _iter in range(10):
        target_idx: Optional[int] = None

        for i, (lo, hi) in enumerate(chunk_spans):
            text = chunks[i].text.strip()
            if not text:
                continue
            if _has_visualisable_content(doc, lo, hi):
                continue
            # Only skip terminal-punct chunks if they ARE visualisable.
            # A non-visualisable chunk ending in "." (e.g. "them.", "it.")
            # still needs to fuse backward — the period belongs after the
            # previous content, not on its own line.
            target_idx = i
            break

        if target_idx is None:
            break

        i = target_idx
        lo, hi = chunk_spans[i]

        content_toks = [t for t in doc[lo:hi]
                        if not t.is_punct and not t.is_space]
        is_substantive = len(content_toks) >= 3

        # --- STEP A: DET-steal (substantive orphans only) ----------------
        # Walk forward from the start of chunk i+1 over leading DET tokens
        # and append them to chunk i.  The split moves forward so the
        # next chunk starts on its first content token.
        if is_substantive and i + 1 < len(chunk_spans):
            nxt_lo, nxt_hi = chunk_spans[i + 1]
            j = nxt_lo
            while j < nxt_hi and doc[j].pos_ == "DET":
                j += 1
            # only apply if (a) we actually stole something AND
            # (b) what remains in the next chunk is still visualisable
            if j > nxt_lo and _has_visualisable_content(doc, j, nxt_hi):
                new_chunks = list(chunks)
                new_spans  = list(chunk_spans)
                # boundary shift (merge id 1007): a determiner moves from the
                # next line onto this one.  Following the "rule goes on the left
                # part" convention, the RECEIVER (this line, which keeps the
                # left boundary and gains the determiner) records 1007 at the
                # front of its ids; the donor line keeps its own ids unchanged.
                new_chunks[i]     = Chunk(doc[lo:j].text.strip(),
                                          [1007] + list(chunks[i].ids))
                new_spans[i]      = (lo, j)
                new_chunks[i + 1] = Chunk(doc[j:nxt_hi].text.strip(),
                                          chunks[i + 1].ids)
                new_spans[i + 1]  = (j, nxt_hi)
                chunks      = new_chunks
                chunk_spans = new_spans
                hi = j   # update local copy for merge logic below

        # --- STEP B: choose merge direction ------------------------------
        can_back = i > 0
        can_fwd  = i + 1 < len(chunk_spans)

        # Substantive orphans are allowed to cross protected boundaries
        # on the BACKWARD side: their content carries no reveal, so the
        # sentence-break visual is less valuable than a clean downstream
        # reveal.  Forward protected boundaries are still respected
        # (so we don't fuse INTO a fresh sentence's reveal).
        if can_back and lo in protected and not is_substantive:
            can_back = False
        if can_fwd and hi in protected:
            can_fwd = False

        if can_back:
            prev_lo, prev_hi = chunk_spans[i - 1]
            new_chunks = list(chunks)
            new_spans  = list(chunk_spans)
            # non-visualisable line folded backward into the previous → 1008
            new_chunks[i - 1] = merge_chunks(chunks[i - 1], chunks[i],
                                             sep=" ", rule=1008)
            new_spans[i - 1]  = (prev_lo, hi)
            del new_chunks[i]
            del new_spans[i]
            chunks = new_chunks
            chunk_spans = new_spans
        elif can_fwd:
            nxt_lo, nxt_hi = chunk_spans[i + 1]
            new_chunks = list(chunks)
            new_spans  = list(chunk_spans)
            # non-visualisable line folded forward into the next → 1009
            new_chunks[i] = merge_chunks(chunks[i], chunks[i + 1],
                                         sep=" ", rule=1009)
            new_spans[i]  = (lo, nxt_hi)
            del new_chunks[i + 1]
            del new_spans[i + 1]
            chunks = new_chunks
            chunk_spans = new_spans
        else:
            break

    if DEBUG_PMV:
        print(f"  [post-merge-unvis] OUTPUT chunks:")
        for i, c in enumerate(chunks):
            print(f"    [{i}] '{c.text}'")

    return chunks, chunk_spans


def _merge_throwaways(doc: Doc, raw: List[Tuple[int, int]],
                       protected: Optional[Set[int]] = None,
                       split_provenance: Optional[Dict[int, List[int]]] = None
                       ) -> Tuple[ChunkMap, List[Tuple[int, int]]]:
    """
    Apply the smart head-aware merge.

    Refinement: NEVER glue a throwaway BACKWARD across a sentence boundary —
    if the previous chunk ends in HARD_PUNCT, force forward gluing instead.
    This prevents bugs like "Not near" being appended to a previous "a desert.".

    NEW: never merge across a PROTECTED split (i.e. a big-punctuation boundary
    like ellipsis, dash, colon, etc).  If a throwaway chunk's left or right
    boundary is in `protected`, we honour it strictly:
      • right boundary protected → throwaway can't glue FORWARD (would erase
        the protected boundary).  It glues BACKWARD instead.
      • left boundary protected → throwaway can't glue BACKWARD.  It glues
        FORWARD instead.
      • BOTH boundaries protected → don't merge at all (keep as-is).

    This is what gives big-punctuation splits their "inviolate" property:
    "Which... | sounds familiar." cannot collapse into one chunk even if
    "Which..." passes the throwaway test.

    Returns (chunks, spans) where `chunks` is the ordered chunk-map (each entry
    a (text, ids) Chunk) and spans[k] is the (lo, hi) token range that produced
    chunks[k] — needed by _fuse_orphans for structural decisions.

    IDS: this is where a freshly-built chunk first gets its `ids` list.  A
    chunk's starting ids come from `split_provenance` keyed on its RIGHT edge
    (`hi`) — i.e. the splitting rule(s) that created that boundary (the chunk is
    the LEFT side of that split, which is the half that records the rule).  When
    fragments are glued together the merge routes through `merge_chunks` with
    the relevant merging-rule id:
      • throwaway glued backward            → 1000
      • throwaway(s) buffered then glued forward onto the next chunk → 1001
      • bare punctuation glued backward     → 1002
    """
    if protected is None:
        protected = set()
    if split_provenance is None:
        split_provenance = {}

    DEBUG_MT = False
    if DEBUG_MT:
        print(f"  [merge-throwaways] INPUT raw chunks:")
        for i, (lo, hi) in enumerate(raw):
            text = doc[lo:hi].text.strip()
            is_tw = _is_throwaway_span(doc, lo, hi)
            print(f"    [{i}] '{text}' span={lo}:{hi} is_throwaway={is_tw} "
                  f"lo_protected={lo in protected} hi_protected={hi in protected}")

    out: ChunkMap = []
    out_spans: List[Tuple[int, int]] = []
    fwd_buf = ""
    fwd_ids: List[int] = []            # ids of throwaways accumulated in fwd_buf
    fwd_lo: Optional[int] = None      # token index where the forward buffer began

    def _emit(text: str, base_ids: List[int]) -> Chunk:
        # Build a fresh chunk for a non-throwaway / kept span.  If a forward
        # buffer of throwaways is waiting, they attach onto the FRONT of this
        # chunk — a forward merge — recorded as rule 1001 (merge id at front,
        # then the buffered fragments' ids, then this chunk's own split ids).
        if fwd_buf:
            return Chunk((fwd_buf + text).strip(),
                         [1001] + list(fwd_ids) + list(base_ids))
        return Chunk(text.strip(), list(base_ids))

    for lo, hi in raw:
        text = doc[lo:hi].text.strip()
        if not text:
            continue

        # the splitting rule(s) that created this span's right edge
        here_ids = list(split_provenance.get(hi, []))

        if _is_throwaway_span(doc, lo, hi):
            # Is this throwaway made entirely of punctuation (a stray quote,
            # dash, ellipsis, comma)?  Such fragments are re-attached as
            # "stray punctuation" (merge id 1002) rather than as a content
            # leftover like 'and'/'the' (merge id 1000).
            is_pure_punct = all(c in (HARD_PUNCT | ANY_QUOTE | DASH_PUNCT
                                       | OPEN_BRACKETS | CLOSE_BRACKETS
                                       | {","})
                                for c in text)
            # NEW: respect protected boundaries.
            left_protected  = lo in protected
            right_protected = hi in protected
            if left_protected and right_protected:
                # Both sides inviolate — normally we keep the chunk
                # standalone.  EXCEPTION: pure-punctuation chunks
                # (closing quotes, trailing dashes, lone ellipses) glue
                # backward onto the previous chunk because they carry no
                # content and reading them alone makes no sense.
                # Fixes 'X."' / '"' becoming '"' on its own line.
                if is_pure_punct and out:
                    out[-1] = merge_chunks(out[-1], Chunk(text, here_ids),
                                           sep="", rule=1002)
                    out_spans[-1] = (out_spans[-1][0], hi)
                    continue
                out.append(_emit(text, here_ids))
                start_lo = fwd_lo if fwd_lo is not None else lo
                out_spans.append((start_lo, hi))
                fwd_buf = ""
                fwd_ids = []
                fwd_lo  = None
                continue

            direction = _throwaway_direction(doc, lo, hi)

            # don't glue across sentence boundaries — go forward instead
            if direction == "bwd" and out and out[-1].text and out[-1].text[-1] in HARD_PUNCT:
                # EXCEPTION: pure-punctuation orphans (closing quotes,
                # trailing punctuation) glue BACKWARD even if previous
                # chunk ends in HARD_PUNCT — they belong attached to
                # the sentence-final punctuation, not on their own line.
                # Fixes 'X.' / '"' becoming one chunk: 'X."'
                if not is_pure_punct:
                    direction = "fwd"



            # NEW: if the right boundary is protected, can't go forward
            if direction == "fwd" and right_protected:
                direction = "bwd" if out else "keep"
            # NEW: if the left boundary is protected, can't go backward
            if direction == "bwd" and left_protected:
                direction = "fwd"
            # SPECIAL: if span is a lone dash and previous chunk ends in a
            # closing quote ("…Whales" + "—"), glue the dash backward to the
            # quote (it closes a parenthetical containing the quote).
            if (direction == "fwd" and out
                    and len(text) <= 2 and any(d in text for d in DASH_PUNCT)
                    and out[-1].text.rstrip() and out[-1].text.rstrip()[-1] in CLOSE_QUOTES):
                direction = "bwd"
            if direction == "bwd" and out:
                # backward glue: stray punctuation → 1002, content bit → 1000
                bwd_rule = 1002 if is_pure_punct else 1000
                out[-1] = merge_chunks(out[-1], Chunk(text, here_ids),
                                       sep=" ", rule=bwd_rule)
                out_spans[-1] = (out_spans[-1][0], hi)
            elif direction == "keep":
                out.append(_emit(text, here_ids))
                start_lo = fwd_lo if fwd_lo is not None else lo
                out_spans.append((start_lo, hi))
                fwd_buf = ""
                fwd_ids = []
                fwd_lo  = None
            else:
                if fwd_lo is None:
                    fwd_lo = lo
                fwd_buf += text + " "
                # buffer this throwaway's own split ids (left half of the
                # eventual forward merge, applied when _emit runs)
                fwd_ids = fwd_ids + here_ids
        else:
            out.append(_emit(text, here_ids))
            start_lo = fwd_lo if fwd_lo is not None else lo
            out_spans.append((start_lo, hi))
            fwd_buf = ""
            fwd_ids = []
            fwd_lo  = None

    if fwd_buf:
        if out:
            # leftover forward buffer with nowhere ahead to attach → glue it
            # backward onto the last line (a backward merge → rule 1000)
            out[-1] = merge_chunks(out[-1], Chunk(fwd_buf.strip(), list(fwd_ids)),
                                   sep=" ", rule=1000)
            out_spans[-1] = (out_spans[-1][0], len(doc))
        else:
            out.append(Chunk(fwd_buf.strip(), list(fwd_ids)))
            out_spans.append((fwd_lo if fwd_lo is not None else 0, len(doc)))

    if DEBUG_MT:
        print(f"  [merge-throwaways] OUTPUT chunks:")
        for i, c in enumerate(out):
            print(f"    [{i}] '{c.text}'")

    return out, out_spans

# =============================================================================
# PIPELINE DEFINITIONS  —  used by split_text_into_sections for both normal
# execution and stage-by-stage debug output.
# =============================================================================

# Positive rules: (name, function, takes_splits_arg)
#   takes_splits_arg=True  → call as fn(doc, splits)
#   takes_splits_arg=False → call as fn(doc)
_POSITIVE_PIPELINE: List[tuple] = [
    ("rule_hard_punct",              rule_hard_punct,              False),
    ("rule_dashes",                  rule_dashes,                  False),
    ("rule_ellipsis",                rule_ellipsis,                False),
    ("rule_pre_ellipsis_reveal",     rule_pre_ellipsis_reveal,     True),
    ("rule_quotes",                  rule_quotes,                  False),
    ("rule_brackets",                rule_brackets,                False),
    ("rule_initial_adverbial_comma", rule_initial_adverbial_comma, False),
    ("rule_comma_split",             rule_comma_split,             False),
    ("rule_comma_list_extension",    rule_comma_list_extension,    False),
    ("rule_long_subord_comma",       rule_long_subord_comma,       False),
    ("rule_long_clause_comma",       rule_long_clause_comma,       True),
    ("rule_appositive_comma",        rule_appositive_comma,        False),
    ("rule_clause_starters",         rule_clause_starters,         True),
    ("rule_but_or_coord",            rule_but_or_coord,            True),
    ("rule_verb_clause",             rule_verb_clause,             False),
    ("rule_long_lead_in",            rule_long_lead_in,            True),
    ("rule_long_preps",              rule_long_preps,              False),
    ("rule_pp_intro_reveal",         rule_pp_intro_reveal,         True),
    ("rule_terminal_of_reveal",      rule_terminal_of_reveal,      True),
    ("rule_noun_lists",              rule_noun_lists,              False),
    ("rule_first_list_item_split",       rule_first_list_item_split,       True), 
    ("rule_bare_noun_lists",         rule_bare_noun_lists,         True),
    ("rule_list_quantifiers",        rule_list_quantifiers,        False),
    ("rule_entity_reveal",           rule_entity_reveal,           True),
    ("rule_numeric_intro_reveal",    rule_numeric_intro_reveal,    True),  
    ("rule_post_entity_split",       rule_post_entity_split,       True),
    ("rule_currency_reveal",         rule_currency_reveal,         True),
    ("rule_imperative_start",        rule_imperative_start,        False),
    ("rule_and_or_clause",           rule_and_or_clause,           True),
    ("rule_terminal_descriptor",     rule_terminal_descriptor,     True),
    ("rule_terminal_adj_coord",      rule_terminal_adj_coord,      True),
    ("rule_adjective_reveal",        rule_adjective_reveal,        True),
    ("rule_numeric_phrase_reveal",   rule_numeric_phrase_reveal,   True),
    ("rule_numeric_approximator_reveal", rule_numeric_approximator_reveal, True), 
    ("rule_participle_split",        rule_participle_split,        True),
    ("rule_progressive_split",       rule_progressive_split,       True),
    ("rule_copula_attr_reveal",      rule_copula_attr_reveal,      True),
    ("rule_pron_participle_pp_reveal", rule_pron_participle_pp_reveal, True),
    ("rule_terminal_pp_after_copula", rule_terminal_pp_after_copula, True),
    ("rule_phrasal_object_reveal",   rule_phrasal_object_reveal,   True),
    ("rule_infinitive_split",        rule_infinitive_split,        True),
    ("rule_prep_object_reveal",      rule_prep_object_reveal,      True),  
    ("rule_transition_adverb",       rule_transition_adverb,       True),
    ("rule_sconj_hang",             rule_sconj_hang,             True),
    ("rule_copula_reveal_split",       rule_copula_reveal_split,       True), 
    ("rule_possession_reveal_split",   rule_possession_reveal_split,   True),  
    ("rule_creation_reveal_split",     rule_creation_reveal_split,     True), 
    ("rule_perception_reveal_split",   rule_perception_reveal_split,   True), 
    ("rule_spatial_prep_reveal_split",   rule_spatial_prep_reveal_split,   True),
    ("rule_result_clause_reveal_split",  rule_result_clause_reveal_split,  True),
    ("rule_equation_reveal_split",       rule_equation_reveal_split,       True), 
    ("rule_and_visualisables_split",   rule_and_visualisables_split,   True), 
    ("rule_title_appositive_verb_split", rule_title_appositive_verb_split, True), 
    ("rule_terminal_specifier_reveal", rule_terminal_specifier_reveal, True), 
    # ---- v18 additions (RULES 56–60) ----------------------------------------
    ("rule_comparison_reveal",       rule_comparison_reveal,       True),
    ("rule_exception_reveal",        rule_exception_reveal,        True),
    ("rule_discourse_pivot",         rule_discourse_pivot,         True),
    ("rule_passive_agent_reveal",    rule_passive_agent_reveal,    True),
    ("rule_sfx_beat",                rule_sfx_beat,                False),
    ("rule_verb_list_reveal",        rule_verb_list_reveal,        False),
]

# Anti-rules: (name, function)
# All anti-rules are called as fn(doc, splits)
_ANTI_PIPELINE: List[tuple] = [
    ("anti_rule_compound_ne",        anti_rule_compound_ne),
    ("anti_rule_aux_main_verb",      anti_rule_aux_main_verb),
    ("anti_rule_hyphen_compound",    anti_rule_hyphen_compound),
    ("anti_rule_possessive",         anti_rule_possessive),
    ("anti_rule_phrasal_particle",   anti_rule_phrasal_particle),
    ("anti_rule_numeric_unit",       anti_rule_numeric_unit),
    ("anti_rule_det_head",           anti_rule_det_head),
    ("anti_rule_inside_quote",       anti_rule_inside_quote),
    ("anti_rule_inside_bracket",     anti_rule_inside_bracket),
    ("anti_rule_frozen_bigram",      anti_rule_frozen_bigram),
    ("anti_rule_adj_noun",           anti_rule_adj_noun),
    ("anti_rule_to_infinitive",      anti_rule_to_infinitive),
    ("anti_rule_numeric_range",      anti_rule_numeric_range),
    ("anti_rule_no_split_before_comma", anti_rule_no_split_before_comma),
    ("anti_rule_currency_glued",     anti_rule_currency_glued),
    ("anti_rule_num_unit",           anti_rule_num_unit),
    ("anti_rule_neg_modifier",       anti_rule_neg_modifier),
    ("anti_rule_compound_noun",      anti_rule_compound_noun),
    ("anti_rule_markdown_emphasis",  anti_rule_markdown_emphasis),
    ("anti_rule_short_sentence",     anti_rule_short_sentence),
    ("anti_rule_verb_to_verb",       anti_rule_verb_to_verb),
    ("anti_rule_verb_to_dem_pron",   anti_rule_verb_to_dem_pron),
    ("anti_rule_orphan_measure_tail", anti_rule_orphan_measure_tail),
    ("anti_rule_content_starved",    anti_rule_content_starved),
    ("anti_rule_split_before_sconj", anti_rule_split_before_sconj),
]


# =============================================================================
# MAIN ENTRY-POINT
# =============================================================================

def _split_core(text: str, debug: bool = False):
    """The full split pipeline.  Returns (doc, chunks, chunk_spans) so callers
    that need token-level information (the with-meta API below) can have it.
    Most callers want the thin public wrappers underneath instead.

    Splits *text* into phrase-sized sections suitable for kinetic typography,
    captions, or YouTube-style on-screen text.

    Pipeline:
       1) strip markdown headings
       2) parse with spaCy
       3) accumulate splits from each positive RULE
       4) remove forbidden splits via each ANTI-RULE
       5) build raw chunk spans
       6) merge throwaway fragments with head-aware direction
       7) post-merge: re-glue any chunk with no visualisable content

    Set debug=True (or set SINGLE_RUN_DEBUG=True at module level) to print
    the chunk shape after each pipeline stage.
    """
    is_debug = debug or SINGLE_RUN_DEBUG

    text = rule_strip_markdown(text)
    text = rule_normalise_whitespace(text)
    text = rule_normalise_punct(text)
    nlp_pipe = _nlp()
    doc = nlp_pipe(text)
    splits: Set[int] = {0, len(doc)}

    # Rules whose splits are "protected" (inviolate — never wiped by
    # anti-rules and never crossed by merge logic).  Tracked as a
    # by-product of the positive-pipeline so each rule is called only once.
    PROTECTED_RULE_NAMES = {
        "rule_hard_punct", "rule_dashes", "rule_ellipsis",
        "rule_quotes", "rule_brackets",
        "rule_terminal_specifier_reveal",
        # v18: a bare SFX line ("boom") has no canonical content POS and
        # would otherwise be eaten by the throwaway/unvisualisable merges.
        "rule_sfx_beat",
    }
    protected: Set[int] = set()

    # Split provenance: maps each split position → the list of splitting-rule
    # ids that created it, most-recent-first (so the newest rule is at the
    # front, matching the convention used everywhere else).  Only rules listed
    # in _SPLIT_RULE_IDS record anything — for now that's just rule_hard_punct
    # (id 1).  This drives the initial `ids` stamped onto each freshly-built
    # chunk in _merge_throwaways.
    split_provenance: Dict[int, List[int]] = {}

    # ---- show original text in debug mode -----------------------------------
    if is_debug:
        print("Original: ")
        print(f'    {_format_chunks_debug([Chunk(text.strip(), [])])}')
        print()

    # ---- positive rules (add splits) ----------------------------------------
    for name, fn, takes_splits in _POSITIVE_PIPELINE:
        prev = splits.copy()
        if takes_splits:
            new = fn(doc, splits)
        else:
            new = fn(doc)
        added = new - prev          # positions this rule introduced just now
        was_applied = bool(added)
        splits |= new
        # record provenance for the splitting rules that are wired up
        rid = _SPLIT_RULE_IDS.get(name)
        if rid is not None:
            for pos in added:
                # newest rule goes to the FRONT of the position's id-list
                split_provenance[pos] = [rid] + split_provenance.get(pos, [])
        # track protected (big-punct) splits
        if name in PROTECTED_RULE_NAMES:
            protected |= new
        if is_debug:
            _debug_print_stage(name, was_applied, (doc, splits))

    # ---- idioms are never cut open (rule 1010) -------------------------------
    for _ilo, _ihi in _idiom_spans(doc):
        splits -= set(range(_ilo + 1, _ihi)) - protected

    # ---- forbidden splits (remove) ------------------------------------------
    if is_debug:
        print()
    for name, fn in _ANTI_PIPELINE:
        prev = splits.copy()
        bad = fn(doc, splits)
        was_applied = bool(bad & prev)
        splits -= bad
        if is_debug:
            _debug_print_stage(name, was_applied, (doc, splits))

    # always preserve sentinel splits AND protected (big-punct) splits.
    splits |= {0, len(doc)}
    splits |= protected

    # Keep provenance only for positions that survived as real INTERIOR
    # boundaries (an anti-rule may have removed some; the sentinels 0 and
    # len(doc) are not real divisions and never carry a split-rule id).
    split_provenance = {
        pos: rules for pos, rules in split_provenance.items()
        if pos in splits and 0 < pos < len(doc)
    }

    if is_debug:
        print()
        print(f"  [protected split indices: {sorted(protected)}]")
        print()

    # ---- post-processing ----------------------------------------------------
    if is_debug:
        print("=== POST-PROCESSING ===")
        print()

    raw = _build_raw_chunks(doc, splits)

    # -- merge throwaways --
    raw_text = [doc[lo:hi].text.strip() for lo, hi in raw]
    raw_text = [t for t in raw_text if t]
    merged, merged_spans = _merge_throwaways(doc, raw, protected,
                                             split_provenance)
    # Chunk is a NamedTuple (always truthy), so filter on the .text field.
    merged_clean = [c for c in merged if c.text]
    if is_debug:
        _debug_print_stage("merge_throwaways",
                           raw_text != [c.text for c in merged_clean],
                           merged_clean)

    # filter empties while keeping spans aligned
    pairs = [(c, s) for c, s in zip(merged, merged_spans) if c.text]
    if not pairs:
        return doc, [], []
    merged, merged_spans = [p[0] for p in pairs], [p[1] for p in pairs]

    # -- fuse orphans --
    prev_chunks = list(merged)
    fused, fused_spans = _fuse_orphans(doc, merged, merged_spans, protected)
    if is_debug:
        _debug_print_stage("fuse_orphans",
                           prev_chunks != fused, fused)

    # -- post-merge unvisualisable --
    prev_chunks = list(fused)
    fused, fused_spans = _post_merge_unvisualisable(
        doc, fused, fused_spans, protected)
    if is_debug:
        _debug_print_stage("post_merge_unvisualisable",
                           prev_chunks != fused, fused)

    # Belt-and-braces: the whitespace normaliser means no space tokens should
    # exist, but no output line may EVER carry '\n' / tabs / double spaces —
    # sanitise once here, at the single exit point.
    fused = [Chunk(" ".join(c.text.split()), c.ids) for c in fused]
    return doc, fused, fused_spans


# =============================================================================
# PUBLIC API  —  two views of the same pipeline
# =============================================================================

def split_text_into_sections(text: str, debug: bool = False) -> ChunkMap:
    """Split *text* into phrase-lines.  Returns the classic ordered chunk-map:
    a list of ``Chunk(text, ids)`` entries (unpackable as ``text, ids = c``).
    This is the stable, backwards-compatible API — nothing about its return
    shape changed in v18.1."""
    _doc, chunks, _spans = _split_core(text, debug)
    return chunks


class ChunkWithMeta(NamedTuple):
    """A phrase-line plus everything downstream consumers keep re-deriving
    with regexes: the spaCy-grounded facts about the line.  Returned by
    split_text_into_sections_with_meta().  ``meta`` keys:

      opener            bool   — line starts a new sentence
      ents              list   — [{"text": ..., "label": ...}] spaCy entities
                                 overlapping this line (GPE/PERSON/MONEY/...)
      keywords          list   — content words in order (nouns, proper nouns,
                                 numbers, strong adjectives, strong verbs) —
                                 ready-made search-term material
      nouns             list   — noun/proper-noun lemmas only (subset of the
                                 above; a search term needs at least one)
      head_noun         str    — lemma of the line's main noun phrase head
                                 ("" when the line has no noun)
      demonstrative     bool   — an NP is anaphoric: "this/that/these/those X"
      pronoun_subject   bool   — the subject is a bare it/they/he/she
      script_topic      str    — doc-level: the most frequent concrete noun
                                 in the whole script ("nutmeg") — the
                                 last-resort referent for term synthesis
      has_visualisable  bool   — the splitter's own picture-ability test
      has_number        bool   — any numeric token
      has_money         bool   — MONEY entity or currency symbol
      in_quote          bool   — any quotation mark inside the line
      n_tokens          int    — non-punct token count
      span              [lo,hi]— token indices into the parsed doc (debugging)
      list              None | {"group": g, "index": i, "size": n} — set when
                                 this line is one item of a detected list run
    """
    text: str
    ids: List[int]
    meta: Dict[str, object]


def split_text_into_sections_with_meta(text: str,
                                       debug: bool = False
                                       ) -> List["ChunkWithMeta"]:
    """Like split_text_into_sections, but each line also carries a ``meta``
    dict of spaCy-grounded facts (see ChunkWithMeta).  This is the API the
    SPLIT_AND_LABEL pipeline consumes — it means the media-type decision code
    never has to re-detect entities/places/openers with regexes."""
    doc, chunks, spans = _split_core(text, debug)
    metas = _build_chunks_meta(doc, chunks, spans)
    return [ChunkWithMeta(c.text, list(c.ids), m)
            for c, m in zip(chunks, metas)]


# the list-run rule ids, their size thresholds, the generic topic nouns and the anaphoric pronouns.
# → moved to shared_text_logic.py, SECTION 1.3 + 2.10 + 2.14 (imported at the top of this file).

# IDIOM_PHRASES and find_idiom_spans().
# → moved to shared_text_logic.py, SECTION 2.8 + 5 (imported at the top of this file).


# ERA_STYLE_NOUNS.
# → moved to shared_text_logic.py, SECTION 2.10 (imported at the top of this file).


def _build_chunks_meta(doc: Doc, chunks: ChunkMap,
                       spans: List[Tuple[int, int]]) -> List[Dict[str, object]]:
    """Compute the per-line ``meta`` dicts from the parsed doc + final spans.

    Runs AFTER all merging, over the final chunk spans, so it never has to
    care about the split/merge machinery — it just reads facts off the doc.
    """
    # ---- doc-level topic: the most frequent concrete noun in the script ----
    # ("nutmeg" for a spice-trade script).  Stamped into every chunk's meta
    # so downstream term synthesis has a last-resort referent.
    topic_counts: Dict[str, int] = {}
    for t in doc:
        if t.pos_ in {"NOUN", "PROPN"} and not t.is_punct and not t.is_space:
            lem = t.lemma_.lower()
            if lem not in _GENERIC_TOPIC_NOUNS and len(lem) > 2:
                topic_counts[lem] = topic_counts.get(lem, 0) + 1
    script_topic = max(topic_counts, key=topic_counts.get) if topic_counts else ""

    # idiom token indices: excluded from keywords/nouns/visualisability
    idiom_idx: Set[int] = set()
    idiom_span_list = _idiom_spans(doc)
    for _lo, _hi in idiom_span_list:
        idiom_idx.update(range(_lo, _hi))

    metas: List[Dict[str, object]] = []
    for chunk_i, (lo, hi) in enumerate(spans):
        opener = (lo == 0
                  or bool(doc[lo].is_sent_start)
                  or doc[lo - 1].text in HARD_PUNCT)
        ents = [{"text": e.text, "label": e.label_}
                for e in doc.ents if e.start < hi and e.end > lo]
        keywords: List[str] = []
        nouns: List[str] = []
        head_noun = ""
        demonstrative = False
        pronoun_subject = False
        seen: Set[str] = set()
        for t in doc[lo:hi]:
            if t.is_punct or t.is_space:
                continue
            if t.i in idiom_idx:
                continue          # idiom words are not content (rule 1010)
            if (t.pos_ == "DET" and t.dep_ == "det"
                    and t.lower_ in {"this", "that", "these", "those"}):
                demonstrative = True
            if (t.pos_ == "PRON" and t.dep_ in {"nsubj", "nsubjpass"}
                    and t.lower_ in _ANAPHORIC_SUBJECT_PRONOUNS):
                pronoun_subject = True
            keep = False
            if t.pos_ in {"NOUN", "PROPN", "NUM"}:
                keep = True
                if t.pos_ in {"NOUN", "PROPN"}:
                    nouns.append(t.lemma_.lower())
                    # the first non-compound noun is the chunk's head noun
                    if not head_noun and t.dep_ != "compound":
                        head_noun = t.lemma_.lower()
            elif t.pos_ == "ADJ" and t.lemma_.lower() not in WEAK_ADJ_LEMMAS:
                keep = True
            elif (t.pos_ == "VERB"
                    and t.lemma_.lower() not in WEAK_VERB_LEMMAS
                    and t.text.lower() not in WEAK_VERB_FORMS):
                keep = True
            if keep:
                low = t.text.lower()
                if low not in seen:
                    seen.add(low)
                    keywords.append(low)
        # a chunk that fully contains an idiom is that saying: tag it 1010
        # and, if the idiom is all it has, it isn't picture-able at all
        chunk_has_idiom = any(lo <= _ilo and _ihi <= hi
                              for _ilo, _ihi in idiom_span_list)
        has_vis = _has_visualisable_content(doc, lo, hi)
        if chunk_has_idiom:
            if 1010 not in chunks[chunk_i].ids:
                chunks[chunk_i].ids.append(1010)
            if not nouns and not keywords:
                has_vis = False

        metas.append({
            "opener": opener,
            "ents": ents,
            "keywords": keywords,
            "nouns": nouns,
            "head_noun": head_noun,
            "demonstrative": demonstrative,
            "pronoun_subject": pronoun_subject,
            "script_topic": script_topic,
            "has_visualisable": has_vis,
            "has_number": any(t.like_num or t.pos_ == "NUM"
                              for t in doc[lo:hi]),
            "has_money": any(t.ent_type_ == "MONEY" or t.text in CURRENCY_SYMS
                             for t in doc[lo:hi]),
            "in_quote": any(t.text in ANY_QUOTE for t in doc[lo:hi]),
            "n_tokens": sum(1 for t in doc[lo:hi]
                            if not t.is_punct and not t.is_space),
            "span": [lo, hi],
            "list": None,
        })

    # ---- list grouping ------------------------------------------------------
    # A grid only makes sense as a YouTube visual for an OBVIOUS
    # comma-separated run of short, picture-able noun items ("scurvy, |
    # pirates, | and shipwrecks").  A chunk qualifies as a LIST ITEM only if
    # ALL of these hold:
    #     • its END boundary was created by a list rule (ids ∩ list rules)
    #     • it ends with a comma (the visible list signal)
    #     • it contains at least one noun (something to put in the cell)
    #     • it is short (≤ _LIST_ITEM_MAX_TOKENS non-punct tokens)
    # and a GROUP only forms from ≥ _LIST_MIN_TAGGED consecutive qualifying
    # items — i.e. at least THREE on-screen cells once the trailing item is
    # pulled in.  A single stray rule-15 boundary ("this wrinkled seed was" /
    # "the single most contested...") can no longer masquerade as a list.
    #
    # (A chunk's ids record the rule that created the boundary at its END,
    # so the FINAL list item carries the sentence-end id instead — the +1
    # extension below pulls it in, provided it also looks like an item.)
    def _qualifies(k: int) -> bool:
        return (bool(set(chunks[k].ids) & _LIST_RULE_IDS)
                and chunks[k].text.rstrip().endswith(",")
                and bool(metas[k]["nouns"])
                and metas[k]["n_tokens"] <= _LIST_ITEM_MAX_TOKENS)

    def _tail_qualifies(k: int) -> bool:      # the final, comma-less item
        return (bool(metas[k]["nouns"])
                and metas[k]["n_tokens"] <= _LIST_ITEM_MAX_TOKENS + 1)

    i, group_id, n = 0, -1, len(chunks)
    while i < n:
        if _qualifies(i):
            j = i
            while j < n and _qualifies(j):
                j += 1
            tagged = j - i
            if j < n and _tail_qualifies(j):
                j += 1                        # pull in the final list item
            if tagged >= _LIST_MIN_TAGGED:
                group_id += 1
                size = j - i
                for k in range(i, j):
                    metas[k]["list"] = {"group": group_id,
                                        "index": k - i, "size": size}
            i = j
        else:
            i += 1
    return metas


# =============================================================================
# CLI / DEMO
# =============================================================================

def _run_test(text: str) -> None:
    print(f"\nBEFORE:\n{text}")
    print("\nAFTER:")
    # split_text_into_sections now returns an ordered chunk-map; pull the text
    # half of each (text, ids) entry for display.
    print("\n".join(c.text for c in split_text_into_sections(text)))
    print("-" * 30)


if __name__ == "__main__":
    cases = [
        "The fast cat sat on the comfortable mat",
        "The baker kneaded the bread while the fire crackled",
        "The red car the blue truck the green bike sped past the house",
        "It costs $800,000 over a lifetime.",
        "Then geology shifted, oceans retreated, land rose upward, climates changed.",
        "ribs, vertebrae, entire fossilized bodies baking under one of the driest climates on Earth.",
        "Wadi Al-Hitan in Egypt — literally \"Valley of the Whales\" — looks almost cinematic from above.",
        "this entire region was covered by the Tethys Sea.",
        "Right now people imagine the Sahara as endless dunes and unbearable heat.",
        "Then time buried everything under dust and rock until the planet basically erased the evidence.",
        "Bike. Fold. Train. Unfold. Bike again.",
        "In Egypt, there's a valley filled with ancient whale skeletons sitting directly in the sand.",
        "Some places on Earth are so flat… satellites use them to check if they're broken.",
        # NEW CASES exercising RULE 25-30 + modifications
        "Cycling sounds great until you're dodging parked cars, squeezed by traffic, soaked in the rain.",
        "If you already know a landscape is almost perfectly level, you can compare satellite readings against it.",
        "Which is probably why these landscapes feel simultaneously calming and alien.",
        "Straight roads vanishing into heat haze for absurd distances.",
        "People crossing empty landscapes suddenly noticing shapes in rock.",
        "A lone person walking across Alvord Desert in Oregon feels almost emotionally vulnerable.",
        "The empire state building is really big.",
        "A single vehicle crossing Salar de Uyuni looks microscopic.",
        "And marine fossils scattered through regions that are now brutally dry.",
        "So now the choice isn't \"drive or not.\"",
        "But Together... mayyyybe not.",
        "One theory suggests toxic algal blooms poisoned large groups of marine animals repeatedly over time.",
        # NEW CASES exercising v18 RULES 56-60 + lexicon extensions
        "From above the whole valley looked like a graveyard of giants.",
        "The ground moved as if something underneath was breathing.",
        "Every single house on the street burned except one.",
        "The valley is completely empty apart from the bones.",
        "Here's the thing the map everyone trusted was wrong.",
        "Which brings us to the strangest part of the whole story.",
        "The skeletons were uncovered by a passing camel herder.",
        "A canyon carved by ten million years of flash floods.",
        "And then boom the entire roof came down around them.",
        "You can hear the ice cracking from a mile away.",
        "Locals swear the lights move on their own after midnight.",
        "The dam broke so the entire valley flooded within hours.",
    ]
    for t in cases:
        _run_test(t)
