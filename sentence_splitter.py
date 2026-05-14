"""
sentence_splitter.py
====================
VERSION: v17  (post-merge unvisualisable rewrite, aggressive iteration)

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
    ['The fast cat sat', 'on the comfortable mat']
"""
from __future__ import annotations

import re
from typing import List, Set, Optional, Tuple

import spacy
from spacy.tokens import Doc, Span, Token


# === VERSION marker — change me when shipping a new revision ===========
# The user can check this at runtime: `from sentence_splitter import VERSION`.
# If it doesn't match the version you expect, they're running stale code.
VERSION = "v17-2026-05-12"


 # === SINGLE-RUN DEBUG FLAG ================================================
# Set to True to always print stage-by-stage output when
# split_text_into_sections() is called.  Can also be enabled per-call
# via split_text_into_sections(text, debug=True).
SINGLE_RUN_DEBUG = False


# =============================================================================
# CONFIG  —  punctuation / structural sets
# (lexical content lives in spaCy's POS/DEP/NER tags, not here, so the rules
#  generalise across vocabulary)
# =============================================================================

# Sentence-final punctuation — always closes a line.
HARD_PUNCT       = {".", "!", "?", ";", ":"}

# Dashes — em (—), en (–), figure (—), double-hyphen (--), single hyphen (-),
# minus (−).  In-word hyphens are filtered out via whitespace check.
DASH_PUNCT       = {"—", "–", "--", "-", "−"}

# "Long" dashes — em / en / double-hyphen.  These ALWAYS earn a split before
# AND after themselves when whitespace-flanked.  Single hyphen "-" is more
# delicate (could be in-word) and handled separately in rule_dashes.
LONG_DASH_PUNCT  = {"—", "–", "--"}

# Quotation marks — straight, smart, French «», German „".
OPEN_QUOTES      = {'"', "\u201C", "\u2018", "\u00AB", "\u2039", "\u201E", "\u201A", "`"}
CLOSE_QUOTES     = {'"', "\u201D", "\u2019", "\u00BB", "\u203A"}
ANY_QUOTE        = OPEN_QUOTES | CLOSE_QUOTES

# Brackets.
OPEN_BRACKETS    = {"(", "[", "{"}
CLOSE_BRACKETS   = {")", "]", "}"}

# Currency symbols — split BEFORE these when followed by a digit.
CURRENCY_SYMS    = {"$", "£", "€", "¥", "₹", "₽", "¢"}

# Penn-Treebank tags for wh-words.  Replaces any hard-coded
# "where/that/who/..." list — generalises to who / whom / whose / what /
# which / where / when / why / how with no string matching.
#   WDT  wh-determiner    : that, which, what
#   WP   wh-pronoun       : who, whom
#   WP$  poss. wh-pronoun : whose
#   WRB  wh-adverb        : where, when, why, how
WH_TAGS          = {"WDT", "WP", "WP$", "WRB"}

# Named-entity types worth introducing on their own line ("the reveal").
REVEAL_ENTS      = {
    "PERSON", "ORG", "GPE", "LOC", "FAC", "NORP",
    "EVENT", "WORK_OF_ART", "PRODUCT", "LAW", "LANGUAGE",
    "DATE", "TIME", "MONEY", "QUANTITY", "PERCENT",
}

# Multi-token entity types that earn a "post-entity" split (RULE 30).
LOCATION_ENTS    = {"GPE", "LOC", "FAC", "ORG", "PERSON", "EVENT", "WORK_OF_ART"}

# Numeric/measure entities — atomic, never cut internally.
NUMERIC_ENTS     = {"CARDINAL", "ORDINAL", "QUANTITY", "MONEY",
                    "DATE", "TIME", "PERCENT"}

# Numeric ent labels we DON'T want to "reveal" when single-token (these are
# usually part of measure phrases like "thousands of kilometers").
NUMERIC_NO_REVEAL = {"CARDINAL", "QUANTITY", "PERCENT", "ORDINAL"}

# Numeric/measurement entity types that, when preceded by an ADP in a shorter
# sentence, are qualifier PPs ("in the 19th century", "for 40 years", "by
# 1946") rather than reveals.  Used by RULE 18 condition (ii').
NUMERIC_QUALIFIER_ENTS = {"DATE", "TIME", "MONEY", "QUANTITY",
                          "PERCENT", "CARDINAL", "ORDINAL"}

# A verb whose dep is one of these is *not heading a top-level clause*,
# so we do NOT use it as a clause boundary.  Includes:
#   amod          : "the running man"
#   acl, acl:relcl: "the man (who is) running for office"
#   advcl         : "running fast, he tripped" / "while the fire crackled"
#   relcl         : UD relative-clause label
#   ccomp         : clausal complement — "she said [he left]"
#   xcomp         : open clausal complement — "tries [to leave]", "left [stranded]"
#   oprd          : object predicate — "called him [crazy]"
#   csubj         : clausal subject — "[that he came] surprised her"
# Adding ccomp/xcomp/oprd keeps verb chains together inside subordinate
# clauses (fixes "feels like it's remembering being underwater" etc.).
VERB_MOD_DEPS    = {"amod", "acl", "acl:relcl", "advcl", "relcl",
                    "ccomp", "xcomp", "oprd", "csubj"}

# Aux / negation deps — never split between aux/neg and the main verb.
#   "doesn't find", "had been running", "is going", "won't say"
AUX_LIKE_DEPS    = {"aux", "auxpass", "neg"}

# Phrasal-verb particle dep — keep the particle joined to its verb.
#   "sped past", "laid down", "set up", "look around"
PARTICLE_DEPS    = {"prt", "compound:prt"}

# POS classes that are too "lightweight" to stand alone as a chunk —
# fragments containing only these will be merged into a neighbour.
LIGHTWEIGHT_POS  = {"CCONJ", "SCONJ", "DET", "ADP", "PART", "PRON", "AUX",
                    "ADV", "INTJ"}

# Prepositions that almost never want a split AFTER them (bind tightly to NP).
PROMISCUOUS_PREPS = {"of"}

# Common measurement / time / quantifier words that mustn't split from a
# preceding number — "15 meters", "40 million", "3 thousand years".
MEASURE_NOUNS    = {
    "meter", "meters", "metre", "metres",
    "foot", "feet", "yard", "yards", "mile", "miles",
    "kilometer", "kilometers", "kilometre", "kilometres",
    "inch", "inches", "centimeter", "centimeters", "centimetre", "centimetres",
    "millimeter", "millimeters", "millimetre", "millimetres",
    "pound", "pounds", "kilogram", "kilograms", "kilo", "kilos",
    "ton", "tons", "tonne", "tonnes", "ounce", "ounces", "gram", "grams",
    "thousand", "million", "billion", "trillion", "hundred", "dozen",
    "second", "seconds", "minute", "minutes", "hour", "hours",
    "day", "days", "week", "weeks", "month", "months",
    "year", "years", "decade", "decades", "century", "centuries",
    "millennium", "millennia",
    "degree", "degrees", "percent", "percentage",
}

# Frozen multi-word idioms we never split inside (kept tiny — POS/DEP do the
# rest).  Bigrams: when token i.lower_ == first and (i+1).lower_ == second,
# no split is allowed between them.
FROZEN_BIGRAMS   = {
    ("what", "if"),       # hypothetical opener
    ("as", "if"),         # similarity
    ("even", "if"),
    ("as", "though"),
    ("kind", "of"),       # hedges
    ("sort", "of"),
    ("type", "of"),
    ("a", "lot"),
    ("at", "least"),
    ("at", "most"),
    ("at", "all"),
    ("of", "course"),
    ("used", "to"),       # "used to be" — keep verb glued to "to"
    ("able", "to"),
    ("going", "to"),
    ("have", "to"),
    ("had", "to"),
    ("got", "to"),
    ("want", "to"),
    ("wants", "to"),
    ("wanted", "to"),
    ("need", "to"),
    ("needs", "to"),
    ("needed", "to"),
    ("try", "to"),
    ("tried", "to"),
}

# Sentence-initial discourse markers that cling to the rest of their sentence
# even when followed by a comma ("Anyway, here is..." → no split after "Anyway,").
DISCOURSE_INIT   = {
    "anyway", "well", "so", "now", "yeah", "yep", "okay", "ok", "right",
    "honestly", "actually", "basically", "essentially", "literally",
    "obviously", "clearly", "frankly", "interestingly", "ironically",
    "fortunately", "unfortunately", "naturally",
    "hmm", "huh", "oh", "ah",
}

# Adverbs that introduce a reveal entity ("specifically Kerala", "namely Smith").
# When one of these immediately precedes a reveal entity, allow the reveal
# even with only 1 token of lead-in.
ADV_INTRODUCERS  = {
    "specifically", "especially", "namely", "particularly", "notably",
    "essentially", "primarily", "mainly", "chiefly", "principally",
    "exactly", "precisely", "literally",
}

# Adverbs that signal a new visual shot/scene transition.
TRANSITION_ADVERBS = {
    "then", "later", "suddenly", "eventually", "finally",
    "afterwards", "subsequently", "next", "soon", "now",
}

# Tunables ----------------------------------------------------------------
MIN_LEAD_FOR_CLAUSE_SPLIT = 3       # tokens before wh/SCONJ to enable split
MIN_LEAD_FOR_BUT_OR       = 3       # tokens before "but"/"or" coord
MIN_LEAD_FOR_AND_CLAUSE   = 5       # tokens before clause-and ("X and he Y")
MIN_LEAD_FOR_ENTITY       = 2       # tokens before entity to count as a "reveal"
LONG_PREP_SUBTREE_MIN     = 5       # ADP subtree size before we split after it
                                    # (was 7 — missed "in a very physical way" type splits)
RUNON_SENT_MIN_TOKENS     = 30      # sentence length needed to enable runon-suppress
RUNON_WINDOW              = 18      # tokens either side checked for punctuation
LONG_LEAD_TO_ROOT         = 12      # force split BEFORE ROOT after this long a lead
SHORT_TAIL_TO_PUNCT       = 3       # don't split verb if remainder to next punct ≤ this
SHORT_SUBORD_CLAUSE       = 2       # don't split before SCONJ if its clause is ≤ this many tokens
                                    # (was 3 — over-suppressed "anyway, here is a sentence /
                                    #  that has punctuatoin" because "that has punctuatoin" has
                                    #  exactly 3 tokens to next punct.)
SHORT_SENT_NO_SPLIT       = 4       # sentences with ≤ this many *non-punct* tokens are
                                    # never split internally.  Per-rule guards inside
                                    # rule_verb_clause / rule_long_preps / rule_clause_starters
                                    # do most of the work now (≤ 8-9 token threshold there);
                                    # this just catches the genuinely tiny ones.
LONG_SUBORD_OPENER_TOKENS = 6       # tokens needed inside an SCONJ/wh-led opener
                                    # before we split after its closing comma (RULE 26)

# --- thresholds for NEW rules (31-38) ---------------------------------------
LONG_COMMA_LEAD_CONTENT   = 5       # min content tokens in lead before generic-long-comma split (RULE 31)
LONG_COMMA_TAIL_CONTENT   = 3       # min content tokens in tail
INFINITIVE_SPLIT_SENT_MIN = 12      # RULE 32: min sent ntok to allow `to + VERB` split
INFINITIVE_SPLIT_LEAD_MIN = 2       # Was 6. FROZEN_BIGRAMS protects "want to", etc. = 6
INFINITIVE_SPLIT_TAIL_MIN = 4
OF_REVEAL_SENT_MIN        = 12      # RULE 33: terminal-of reveal
OF_REVEAL_LEAD_MIN        = 5
PROGRESSIVE_SENT_MIN      = 10      # RULE 34: split before VBG in `be + Ving`
PROGRESSIVE_LEAD_MIN      = 3
COPULA_REVEAL_SENT_MIN    = 7       # RULE 35: copula-attribute reveal
COPULA_REVEAL_CHUNK_MIN   = 2       # RULE 35: min noun-chunk length to count as reveal
PP_PRON_PART_SENT_MIN     = 9       # RULE 36: PP-with-PRON-participle reveal
PP_PRON_PART_LEAD_MIN     = 2
AUX_PP_REVEAL_LEAD_MIN    = 3       # RULE 37: terminal `'s about X` reveal
CHAINED_PART_SENT_MIN     = 11      # RULE 38: chained-participle reveal
DOBJ_DISQUAL_SENT_MIN     = 8       # min sent length to allow dobj-disqualifier in RULE 12

# Comparative markers — when a verb's dobj subtree contains one, the dobj is
# a "reveal NP" worth splitting before instead of gluing.
COMPARATIVE_MARKERS = {"than", "more", "less", "fewer"}


# =============================================================================
# spaCy MODEL — load once, reuse across calls.
# =============================================================================
_NLP = None
def _nlp() -> "spacy.language.Language":
    global _NLP
    if _NLP is None:
        _NLP = spacy.load("en_core_web_sm")
    return _NLP


# =============================================================================
# UTILITY HELPERS
# =============================================================================

def _prev_split(splits: Set[int], i: int) -> int:
    """Largest split index strictly less than *i*."""
    return max((s for s in splits if s < i), default=0)

def _next_split(splits: Set[int], i: int, doc_len: int) -> int:
    """Smallest split index strictly greater than *i*."""
    return min((s for s in splits if s > i), default=doc_len)


def _in_compound_ne(doc: Doc, i: int) -> bool:
    """
    True if cutting at *i* would split a multi-token named entity
    (e.g. between "John" and "Ford", or "New" and "York City").
    """
    if i <= 0 or i >= len(doc):
        return False
    left, right = doc[i - 1], doc[i]
    return (left.ent_iob_ in {"B", "I"}
            and right.ent_iob_ == "I"
            and left.ent_type_ == right.ent_type_)


def _in_hyphen_compound(doc: Doc, i: int) -> bool:
    """True if *i* would cut a hyphenated compound: 'self-driving', 'follow-up'."""
    if i <= 0 or i >= len(doc):
        return False
    if doc[i - 1].text == "-" and not doc[i - 1].whitespace_:
        return True
    if doc[i].text == "-" and not doc[i - 1].whitespace_:
        return True
    return False


def _is_ordinal(tok: Token) -> bool:
    """Detect ordinals (first, 3rd, ...) without a hard-coded word list."""
    if tok.ent_type_ == "ORDINAL":
        return True
    return bool(re.match(r"^\d+(st|nd|rd|th)$", tok.lower_))


def _matches_ellipsis(text: str) -> bool:
    """Detect ..., ...., or … (single-character ellipsis)."""
    return bool(re.fullmatch(r"\.{2,}|\u2026+", text))


def _is_big_punct_split(doc: Doc, i: int) -> bool:
    """True if the split at *i* is "punctuation-driven" — either the token
    immediately to its LEFT or to its RIGHT is "big" (non-comma) punctuation
    that should always force a line-break:

        • hard-punct      .  !  ?  ;  :
        • ellipsis        ...  ….
        • dash            —   –   --   -
        • quote           "  '  “  ”  ‘  ’  «  »  ‹  ›  „  ‚  `
        • bracket         (  )  [  ]  {  }

    These splits are NEVER wiped by any anti-rule.  Big punctuation marks
    a deliberate visual break by the writer — even short sentences should
    honour it.  (Commas are deliberately NOT included: they're soft
    break-points and the existing comma rules decide when to split.)
    """
    if i <= 0 or i > len(doc):
        return False
    BIG = HARD_PUNCT | DASH_PUNCT | ANY_QUOTE | OPEN_BRACKETS | CLOSE_BRACKETS
    # left side
    if i > 0:
        lt = doc[i - 1]
        if lt.text in BIG or _matches_ellipsis(lt.text):
            return True
    # right side
    if i < len(doc):
        rt = doc[i]
        if rt.text in BIG or _matches_ellipsis(rt.text):
            return True
    return False


def _is_in_runon(doc: Doc, i: int) -> bool:
    """
    True if token *i* lives in a long sentence (≥ RUNON_SENT_MIN_TOKENS)
    AND no hard punctuation appears within RUNON_WINDOW tokens either side.
    Used to suppress fine-grained verb splits inside list-heavy passages
    (e.g. the lighthouse paragraph).  CRUCIAL: returns False for short
    sentences so ordinary S-V-O sentences ("the cat sat on the mat") still
    split after the verb.
    """
    sent = doc[i].sent
    if len(sent) < RUNON_SENT_MIN_TOKENS:
        return False
    lo = max(0, i - RUNON_WINDOW)
    hi = min(len(doc), i + RUNON_WINDOW)
    return not any(t.text in HARD_PUNCT for t in doc[lo:hi])


def _is_frozen_bigram_split(doc: Doc, i: int) -> bool:
    """True if cutting at *i* would split a frozen bigram like 'what if'."""
    if i <= 0 or i >= len(doc):
        return False
    pair = (doc[i - 1].lower_, doc[i].lower_)
    return pair in FROZEN_BIGRAMS


def _chunk_containing(doc: Doc, i: int) -> Optional[Span]:
    """Return the noun_chunk containing token index *i*, or None."""
    for nc in doc.noun_chunks:
        if nc.start <= i < nc.end:
            return nc
    return None


def _tokens_to_next_punct(doc: Doc, i: int) -> int:
    """Return the number of tokens from *i* (inclusive) up to the next
    HARD_PUNCT or comma (or end of doc)."""
    j = i
    while j < len(doc) and doc[j].text not in HARD_PUNCT and doc[j].text != ",":
        j += 1
    return j - i


def _content_count(doc: Doc, lo: int, hi: int) -> int:
    """Count NOUN/PROPN/VERB/ADJ/ADV/NUM tokens in doc[lo:hi].  Used by
    the long-clause / infinitive / progressive / `of`-reveal rules to
    measure how much "real content" is on each side of a candidate split."""
    return sum(1 for x in doc[lo:hi]
               if x.pos_ in {"NOUN", "PROPN", "VERB", "ADJ", "ADV", "NUM"})


# --- debug helpers ---------------------------------------------------------

def _splits_to_chunks_list(doc: Doc, splits: Set[int]) -> List[str]:
    """Build list of chunk texts from a splits set (for debug display)."""
    idx = sorted(splits)
    chunks: List[str] = []
    for i in range(len(idx) - 1):
        text = doc[idx[i]:idx[i + 1]].text.strip()
        if text:
            chunks.append(text)
    return chunks


def _format_chunks_debug(chunks: List[str]) -> str:
    """Format a chunk list with '|||' separators for debug display."""
    if not chunks:
        return "[]"
    return '["' + '" ||| "'.join(chunks) + '"]'


def _debug_print_stage(name: str, was_applied: bool,
                       doc_or_chunks) -> None:
    """Print one line of debug output for a pipeline stage.

    *doc_or_chunks* is either a tuple ``(doc, splits)`` (for rule stages)
    or a plain ``List[str]`` of chunk texts (for post-processing stages).
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


# Lemma set for "weak" verbs that shouldn't qualify a chunk as visualisable
# on their own — copulas and similar functional verbs.  A chunk containing
# ONLY weak verbs (no nouns, no adjectives, no concrete verbs) is essentially
# a connective phrase ("It's", "that has", "which were", "had been").
WEAK_VERB_LEMMAS = {
    "be", "have", "do", "get", "make", "go", "come",
    "seem", "appear", "become", "remain", "stay",
}

# All surface forms of the above weak verbs, including clitic contractions.
# Some spaCy parses tag clitics with non-canonical lemmas ("'re" instead of
# "be") so the lemma-based check alone misses them.  We also check the
# token's text.lower() against this set.
WEAK_VERB_FORMS = {
    # be
    "be", "am", "is", "are", "was", "were", "been", "being",
    "'s", "’s", "'re", "’re", "'m", "’m", "'ve", "’ve",
    # have
    "have", "has", "had", "having", "'d", "’d", "'ll", "’ll",
    # do
    "do", "does", "did", "done", "doing",
    # get
    "get", "gets", "got", "gotten", "getting",
    # make
    "make", "makes", "made", "making",
    # go
    "go", "goes", "went", "gone", "going",
    # come
    "come", "comes", "came", "coming",
    # seem / appear / become / remain / stay
    "seem", "seems", "seemed", "seeming",
    "appear", "appears", "appeared", "appearing",
    "become", "becomes", "became", "becoming",
    "remain", "remains", "remained", "remaining",
    "stay", "stays", "stayed", "staying",
}

# Lemma set for "weak" adjectives — quantifier-ish words that spaCy tags
# as ADJ but don't add visual content on their own.  A chunk like "many",
# "much", "few", "some" alone is not visualisable.  Real descriptive ADJs
# ("red", "tiny", "ancient", "brilliant") are visualisable.
WEAK_ADJ_LEMMAS = {
    "many", "much", "more", "less", "few", "fewer", "some", "any",
    "such", "other", "same", "different", "various", "several",
    "certain", "particular", "specific", "general",
    "own", "whole", "entire", "main", "only", "very", "too",
}


def _has_visualisable_content(doc: Doc, lo: int, hi: int) -> bool:
    """True if the span has at least one tangible/concrete content token.

    A token is "visualisable" when:
      • it's a NOUN or PROPN (you can see a thing/place/person), OR
      • it's a NUM (you can see a number), OR
      • it's an ADJ whose lemma isn't a quantifier-ish weak word
        ("many", "few", "some", "such", "other"...), OR
      • it's a VERB whose lemma isn't a copula/auxiliary-like ("is", "be",
        "have", "do", "get", "seem", "become"…) — these are connectives
        that don't carry independent visual content.

    Function words alone (DET / PRON / SCONJ / CCONJ / PART / ADP / AUX /
    interjections / ADV) do NOT make a chunk visualisable.

    Examples:
      "that many of"     → False (PRON + weak-ADJ + ADP)
      "It's"             → False (PRON + copula AUX)
      "which is the"     → False (PRON + copula + DET)
      "the patient dog"  → True  (NOUN)
      "ran fast"         → True  (concrete VERB)
      "was running"      → True  (running is VBG/VERB with lemma "run")
      "abstract."        → True  (real ADJ)
    """
    for t in doc[lo:hi]:
        if t.pos_ in {"NOUN", "PROPN", "NUM"}:
            return True
        if t.pos_ == "ADJ" and t.lemma_.lower() not in WEAK_ADJ_LEMMAS:
            return True
        if t.pos_ == "VERB":
            # Strict: verb counts as visualisable ONLY if its lemma AND its
            # text are both outside the weak-form sets.  Clitic contractions
            # like "'re", "'s", "'ve" sometimes have non-canonical lemmas in
            # spaCy, so the lemma-only check would miss them.
            lemma_weak = t.lemma_.lower() in WEAK_VERB_LEMMAS
            text_weak  = t.text.lower() in WEAK_VERB_FORMS
            if not (lemma_weak or text_weak):
                return True
    return False


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
# RULE 11 — COORDINATING "BUT" / "OR" WITH LONG LEAD-IN
# CCONJ "but" / "or" between clauses gets a split before it when the lead-up
# is substantial.  ("and" handled separately by RULE 21.)
# -----------------------------------------------------------------------------
def rule_but_or_coord(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "CCONJ" or t.lower_ not in {"but", "or"}:
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

    # Explicit prep sets for visual typography splitting
    BLOCKED_PREPS = {
        "of", "with", "for", "about", "as", "like", "than", "per", "via"
    }
    SPLITTABLE_PREPS = {
        # location
        "in", "at", "on", "under", "over", "above", "below", "beneath",
        "behind", "beside", "between", "among", "around", "near", "by",
        "inside", "outside", "within", "into", "onto", "through", "across",
        "along", "past", "toward", "towards", "off", "up", "down",
        "underneath", "against", "upon",
        # time
        "after", "before", "during", "since", "until", "till"
    }

    for a, b in zip(chunks[:-1], chunks[1:]):
        between = doc[a.end:b.start]

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
        prev = _prev_split(splits | out, i)
        if any(t.pos_ == "VERB" or t.text in HARD_PUNCT for t in doc[prev:i]):
            continue
        out.add(i)
    return out


# -----------------------------------------------------------------------------
# RULE 17 — LIST-CLOSING QUANTIFIERS
# Split BEFORE "all" / "both" / "each" / "every" when they immediately follow
# a noun — typical of summary-after-list constructions.
# -----------------------------------------------------------------------------
def rule_list_quantifiers(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.lower_ in {"all", "both", "each", "every"} and t.i > 0 \
                and doc[t.i - 1].pos_ in {"NOUN", "PROPN"}:
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
        # (ii') multi-token numeric/measure entity preceded by ADP
        # (e.g. "in the 19th century", "for 40 years", "by 1946") — these
        # are qualifiers, not reveals.  Only blocked in shorter sentences;
        # longer sentences have enough build-up to justify a reveal.
        sent_ntok_here = sum(1 for x in doc[split_at].sent if not x.is_punct)
        if (ent.label_ in NUMERIC_QUALIFIER_ENTS
                and prev_tok is not None and prev_tok.pos_ == "ADP"
                and sent_ntok_here < 12):
            # However: if the ADP is preceded by another ADP-PP chain (e.g.
            # "Built in Manhattan / in the 19th century"), the second PP is
            # the qualifier — still skip.  This is the default branch.
            continue
        # (v) appositive — preceded by a NOUN/PROPN ("the technician John Ford")
        if prev_tok is not None and prev_tok.pos_ in {"NOUN", "PROPN"}:
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
            print("1")
            continue
        # don't split mid-entity, UNLESS it's an approximator + number date combo
        if t.ent_iob_ == "I":
            prev_tok = doc[t.i - 1] # ensure prev_tok is defined before this check
            if not (prev_tok.ent_iob_ == "B" and prev_tok.pos_ in {"ADP", "ADV"}):
                print("1.5")
                continue
        # must be near sentence start (within first ~5 tokens)
        if t.i - t.sent.start > 5:
            print("2")
            continue
        # need at least one preceding token in this sentence
        if t.i == t.sent.start:
            print("3")
            continue
        prev_tok = doc[t.i - 1]
        # previous token should be ADP/ADV (the approximator)
        if prev_tok.pos_ not in {"ADP", "ADV"}:
            print("5")
            continue
        # sentence must be long enough — avoid splitting short sentences
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 12:
            print("6")
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
            print("7")
            continue
        # need at least 2 tokens of lead
        prev = _prev_split(splits | out, t.i)
        if t.i - prev < 2:
            print("k")
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
MIN_LEAD_FOR_DESCRIPTOR = 5

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
        # GUARD: only fires when sentence has substantial build-up
        # (≥ 8 content tokens).  Otherwise short copular sentences like
        # "The empire state building is really big." would over-split.
        #
        # Walk-back rule: step BACK over any ADV/ADJ chain UNTIL we hit
        # either (a) a token whose head is a VERB/AUX (clause boundary) or
        # (b) a token whose POS is not ADV/ADJ.  This lets us correctly
        # land the split BEFORE the FIRST element of the descriptor chain:
        #   #28  "visually confused very fast"  →  before "visually"
        #   #56  "are now brutally dry"         →  before "brutally"
        #   #41  "packed together unusually densely" → before "together"
        if last.pos_ in {"ADJ", "ADV"} and last.i > sent.start + 1:
            prev_tok = doc[last.i - 1]
            if prev_tok.pos_ == "ADV":
                if sent_ntok < 8:
                    continue
                start = prev_tok.i
                # Walk back through ADV/ADJ tokens whose head is in this
                # tail descriptor chain — stop when head is a finite VERB/AUX
                # that is NOT itself in the trailing descriptor span.
                while start > sent.start and doc[start - 1].pos_ in {"ADV", "ADJ"}:
                    candidate = doc[start - 1]
                    # never cross a punct boundary
                    if candidate.is_punct:
                        break
                    start -= 1
                prev_split = _prev_split(splits | out, start)
                if start - prev_split >= MIN_LEAD_FOR_DESCRIPTOR:
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
# -----------------------------------------------------------------------------
def rule_numeric_phrase_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for sent in doc.sents:
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
                # walk back to entity start
                k = t.i
                while k > sent.start and doc[k-1].ent_iob_ == "I" and doc[k-1].ent_type_ == t.ent_type_:
                    k -= 1
                split_at = k
                
            if split_at == 0 or split_at == sent.start:
                continue
                
            # what's directly before? If it's a NOUN/VERB/ADJ/ADP ending a meaningful
            # phrase, we split before the numeric phrase.
            prev_tok = doc[split_at - 1] if split_at > 0 else None
            if prev_tok is None:
                continue
            if prev_tok.pos_ not in {"NOUN", "VERB", "ADJ", "PART", "ADP"}:
                continue
            # require substantial lead-in
            prev = _prev_split(splits | out, split_at)
            if split_at - prev < 5:
                continue
            out.add(split_at)
            break  # one per sentence
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
        # (c) ADJ + "," + ADJ  — adjective series
        if prev.pos_ == "ADJ" and nxt.pos_ == "ADJ":
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
        # last must be ADJ or VBG/VBN-tagged verb (adj-like)
        is_adj_like = (last.pos_ == "ADJ" or last.tag_ in {"VBG", "VBN"})
        if not is_adj_like:
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
        first_adj_is_adj = (first_adj.pos_ == "ADJ" or first_adj.tag_ in {"VBG", "VBN"})
        if not first_adj_is_adj:
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
        out.add(cconj.i)
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
    out = set()
    for t in doc:
        if t.text != ",":
            continue
        if t.i + 1 >= len(doc):
            continue
        # TIGHTENED: only fire in sentences with ≥14 non-punct tokens.  This
        # is a "very long sentence" rule — for medium sentences, RULE 7,
        # RULE 8, and RULE 26 cover the necessary cases.
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 14:
            continue
        # lead from previous split
        prev = _prev_split(splits | out, t.i + 1)
        lead_content = _content_count(doc, prev, t.i + 1)
        if lead_content < LONG_COMMA_LEAD_CONTENT:
            continue
        # don't fire if there's already a hard punct in the lead — would
        # mean the lead "content" comes from a separate clause.
        if any(doc[k].text in HARD_PUNCT for k in range(prev, t.i)):
            continue
        # tail content tokens until next punct or sentence end
        sent_end = t.sent.end
        tail_end = t.i + 1
        while tail_end < sent_end and doc[tail_end].text not in HARD_PUNCT:
            tail_end += 1
        tail_content = _content_count(doc, t.i + 1, tail_end)
        if tail_content < LONG_COMMA_TAIL_CONTENT:
            continue
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
    out = set()
    for t in doc:
        if t.lower_ != "of" or t.pos_ != "ADP":
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < OF_REVEAL_SENT_MIN:
            continue
        # `of`-headed subtree must reach (close to) sentence end
        subtree = list(t.subtree)
        if not subtree:
            continue
        subtree_end = max(x.i for x in subtree)
        sent_end_i = max(x.i for x in t.sent if not x.is_punct)
        if subtree_end < sent_end_i - 2:   # subtree must close at the tail
            continue
        # require ≥2 nouns in subtree (so a "real" NP follows)
        n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
        if n_nouns < 2:
            continue
        # don't fire if subtree contains its own verb/aux
        if any(x.pos_ in {"VERB", "AUX"} for x in subtree):
            continue
        prev = _prev_split(splits | out, t.i + 1)
        lead = _content_count(doc, prev, t.i + 1)
        if lead < OF_REVEAL_LEAD_MIN:
            continue
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
    
    # Verbs that act as copulas (linking subject to visual predicate)
    COPULAR_LEMMAS = {
        "be", "feel", "look", "seem", "appear", "become", "remain", "stay",
        "sound", "taste", "smell", "prove", "turn"
    }
    COPULAR_FORMS = {
        "is", "are", "was", "were", "am", "be", "been", "being",
        "'s", "’s", "'re", "’re", "'m", "’m",
        "feels", "looks", "seems", "appears", "becomes", "remains", "stays",
        "felt", "looked", "seemed", "appeared", "became", "remained", "stayed"
    }
    
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
    out = set()
    for t in doc:
        if t.pos_ != "ADP":
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
        has_numeric = any(x.like_num or x.ent_type_ in {"DATE", "TIME", "CARDINAL", "QUANTITY", "PERCENT", "MONEY"} for x in pobj_subtree)
        if sent_ntok <= 8 and not (is_terminal_pp and has_numeric):
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


# =============================================================================
# ANTI-RULES  —  return indices to *remove* from the splits set.
# =============================================================================

# A — never split inside a multi-token named entity ("New York City").
def anti_rule_compound_ne(doc: Doc, splits: Set[int]) -> Set[int]:
    return {i for i in splits if _in_compound_ne(doc, i)}


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
    APOS = {"'", "\u2019", "\u2018", "`"}

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
            # Check if this is a long-sentence visual reveal split
            sent_ntok = sum(1 for x in left.sent if not x.is_punct)
            if sent_ntok >= INFINITIVE_SPLIT_SENT_MIN:
                prev = _prev_split(splits, left.i)
                lead_content = _content_count(doc, prev, left.i)
                # Check tail content
                sent_end = left.sent.end
                tail_end = left.i
                while tail_end < sent_end and doc[tail_end].text not in HARD_PUNCT:
                    tail_end += 1
                tail_content = _content_count(doc, left.i, tail_end)
                if lead_content >= INFINITIVE_SPLIT_LEAD_MIN and tail_content >= INFINITIVE_SPLIT_TAIL_MIN:
                    continue # Allow this split!
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
    EMPH_CHARS = {"*", "_"}

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
        # word count — cap at 6 (was 4) so longer connective phrases
        # like "but it's how it" also get re-merged.
        text = span.text.strip()
        if not text:
            continue
        if len(text.split()) > 6:
            continue
        # Use the strict visualisability check (NOUN/PROPN/NUM/ADJ/
        # concrete-VERB) — excludes copulas and AUX.
        if _has_visualisable_content(doc, lo, hi):
            continue
        # NEVER remove a split that is punctuation-driven on EITHER boundary.
        # The chunk in question may be light on content but if it's flanked
        # by big punctuation (ellipsis, dash, quote, bracket, hard-punct)
        # the writer marked an intentional break.  Fixes the bug where
        # "Which..." was being merged with "sounds familiar." because the
        # chunk had no content POS.
        if _is_big_punct_split(doc, lo) or _is_big_punct_split(doc, hi):
            continue
        # If the chunk ends in a HARD_PUNCT (sentence-final), it's a
        # deliberate sentence break — don't wipe.  This is also caught by
        # the protect-big-punct check above (HARD_PUNCT ∈ big punct), but
        # double-guard for clarity.
        last_nonspace = None
        for t in reversed(list(span)):
            if not t.is_space:
                last_nonspace = t
                break
        if last_nonspace is not None and last_nonspace.text in HARD_PUNCT:
            continue

        # Remove the LEFT boundary so this chunk glues backward into the
        # previous chunk as a cliffhanger, matching _post_merge_unvisualisable.
        # Exception: if lo is 0 (start of doc), remove hi instead.
        if lo > 0:
            bad.add(lo)
        elif hi < len(doc):
            bad.add(hi)
    return bad


# Y — never split immediately BEFORE an SCONJ mid-sentence.
# SCONJs like "because", "if", "while" should cling to the previous line
# as a cliffhanger. This anti-rule removes splits right before SCONJs,
# allowing rule_clause_starters to place the split AFTER the SCONJ instead.
def anti_rule_split_before_sconj(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i >= len(doc):
            continue
        tok = doc[i]
        if tok.pos_ == "SCONJ":
            # Don't remove if it's at the start of a sentence (after hard punct)
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


def _fuse_orphans(doc: Doc, chunks: List[str], chunk_spans: List[Tuple[int, int]],
                  protected: Optional[Set[int]] = None) -> Tuple[List[str], List[Tuple[int, int]]]:
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

    REL_TAGS  = {"WDT", "WP", "WP$", "WRB"}
    PUNCT_ONLY_RE = re.compile(r"^[^\w\s]+$")  # no letters/digits

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

    out: List[str]            = []
    out_spans: List[Tuple[int, int]] = []
    i = 0
    while i < len(chunks):
        cur_text = chunks[i]
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
                out[-1] = out[-1] + cur_text
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
                fused = (cur_text + " " + chunks[i + 1]).strip()
                out.append(fused)
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
                out[-1] = (out[-1] + " " + cur_text).strip()
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
                fused = (cur_text + " " + chunks[i + 1]).strip()
                out.append(fused)
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
            fused = (cur_text + " " + chunks[i + 1]).strip()
            out.append(fused)
            out_spans.append((cur_lo, nxt_hi))
            i += 2
            continue

        out.append(cur_text)
        out_spans.append((cur_lo, cur_hi))
        i += 1
    return out, out_spans


def _post_merge_unvisualisable(doc: Doc,
                               chunks: List[str],
                               chunk_spans: List[Tuple[int, int]],
                               protected: Set[int]) -> Tuple[List[str], List[Tuple[int, int]]]:
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
    """
    if len(chunks) <= 1:
        return chunks, chunk_spans

    for _iter in range(10):
        target_idx: Optional[int] = None

        for i, (lo, hi) in enumerate(chunk_spans):
            text = chunks[i].strip()
            if not text:
                continue
            if text[-1] in {".", "!", "?"}:
                continue
            if _has_visualisable_content(doc, lo, hi):
                continue
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
                new_chunks[i]     = doc[lo:j].text.strip()
                new_spans[i]      = (lo, j)
                new_chunks[i + 1] = doc[j:nxt_hi].text.strip()
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
            new_chunks[i - 1] = (chunks[i - 1] + " " + chunks[i]).strip()
            new_spans[i - 1]  = (prev_lo, hi)
            del new_chunks[i]
            del new_spans[i]
            chunks = new_chunks
            chunk_spans = new_spans
        elif can_fwd:
            nxt_lo, nxt_hi = chunk_spans[i + 1]
            new_chunks = list(chunks)
            new_spans  = list(chunk_spans)
            new_chunks[i] = (chunks[i] + " " + chunks[i + 1]).strip()
            new_spans[i]  = (lo, nxt_hi)
            del new_chunks[i + 1]
            del new_spans[i + 1]
            chunks = new_chunks
            chunk_spans = new_spans
        else:
            break

    return chunks, chunk_spans


def _merge_throwaways(doc: Doc, raw: List[Tuple[int, int]],
                       protected: Optional[Set[int]] = None
                       ) -> Tuple[List[str], List[Tuple[int, int]]]:
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

    Returns (chunks, spans) where spans[k] is the (lo, hi) token range that
    produced chunks[k] — needed by _fuse_orphans for structural decisions.
    """
    if protected is None:
        protected = set()
    out: List[str] = []
    out_spans: List[Tuple[int, int]] = []
    fwd_buf = ""
    fwd_lo: Optional[int] = None      # token index where the forward buffer began

    for lo, hi in raw:
        text = doc[lo:hi].text.strip()
        if not text:
            continue

        if _is_throwaway_span(doc, lo, hi):
            # NEW: respect protected boundaries.
            left_protected  = lo in protected
            right_protected = hi in protected
            if left_protected and right_protected:
                # both sides inviolate — keep this chunk as-is even if tiny
                out.append((fwd_buf + text).strip())
                start_lo = fwd_lo if fwd_lo is not None else lo
                out_spans.append((start_lo, hi))
                fwd_buf = ""
                fwd_lo  = None
                continue

            direction = _throwaway_direction(doc, lo, hi)
            # don't glue across sentence boundaries — go forward instead
            if direction == "bwd" and out and out[-1] and out[-1][-1] in HARD_PUNCT:
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
                    and out[-1].rstrip() and out[-1].rstrip()[-1] in CLOSE_QUOTES):
                direction = "bwd"
            if direction == "bwd" and out:
                out[-1] = (out[-1] + " " + text).strip()
                out_spans[-1] = (out_spans[-1][0], hi)
            elif direction == "keep":
                out.append((fwd_buf + text).strip())
                start_lo = fwd_lo if fwd_lo is not None else lo
                out_spans.append((start_lo, hi))
                fwd_buf = ""
                fwd_lo  = None
            else:
                if fwd_lo is None:
                    fwd_lo = lo
                fwd_buf += text + " "
        else:
            out.append((fwd_buf + text).strip())
            start_lo = fwd_lo if fwd_lo is not None else lo
            out_spans.append((start_lo, hi))
            fwd_buf = ""
            fwd_lo  = None

    if fwd_buf:
        if out:
            out[-1] = (out[-1] + " " + fwd_buf).strip()
            out_spans[-1] = (out_spans[-1][0], len(doc))
        else:
            out.append(fwd_buf.strip())
            out_spans.append((fwd_lo if fwd_lo is not None else 0, len(doc)))

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
    ("rule_bare_noun_lists",         rule_bare_noun_lists,         True),
    ("rule_list_quantifiers",        rule_list_quantifiers,        False),
    ("rule_entity_reveal",           rule_entity_reveal,           True),
    ("rule_numeric_intro_reveal",    rule_numeric_intro_reveal,    True),  # NEW
    ("rule_post_entity_split",       rule_post_entity_split,       True),
    ("rule_currency_reveal",         rule_currency_reveal,         True),
    ("rule_imperative_start",        rule_imperative_start,        False),
    ("rule_and_or_clause",           rule_and_or_clause,           True),
    ("rule_terminal_descriptor",     rule_terminal_descriptor,     True),
    ("rule_terminal_adj_coord",      rule_terminal_adj_coord,      True),
    ("rule_adjective_reveal",        rule_adjective_reveal,        True),
    ("rule_numeric_phrase_reveal",   rule_numeric_phrase_reveal,   True),
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

def split_text_into_sections(text: str, debug: bool = False) -> List[str]:
    """
    Split *text* into a list of phrase-sized sections suitable for kinetic
    typography, captions, or YouTube-style on-screen text.

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
    }
    protected: Set[int] = set()

    # ---- show original text in debug mode -----------------------------------
    if is_debug:
        print("Original: ")
        print(f'    {_format_chunks_debug([text.strip()])}')
        print()

    # ---- positive rules (add splits) ----------------------------------------
    for name, fn, takes_splits in _POSITIVE_PIPELINE:
        prev = splits.copy()
        if takes_splits:
            new = fn(doc, splits)
        else:
            new = fn(doc)
        was_applied = bool(new - prev)
        splits |= new
        # track protected (big-punct) splits
        if name in PROTECTED_RULE_NAMES:
            protected |= new
        if is_debug:
            _debug_print_stage(name, was_applied, (doc, splits))

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
    merged, merged_spans = _merge_throwaways(doc, raw, protected)
    merged_clean = [c for c in merged if c]
    if is_debug:
        _debug_print_stage("merge_throwaways",
                           raw_text != merged_clean, merged_clean)

    # filter empties while keeping spans aligned
    pairs = [(c, s) for c, s in zip(merged, merged_spans) if c]
    if not pairs:
        return []
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

    return fused


# =============================================================================
# CLI / DEMO
# =============================================================================

def _run_test(text: str) -> None:
    print(f"\nBEFORE:\n{text}")
    print("\nAFTER:")
    print("\n".join(split_text_into_sections(text)))
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
    ]
    for t in cases:
        _run_test(t)
