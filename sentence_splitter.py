"""
sentence_splitter.py
====================
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

# Numeric/measure entities — atomic, never cut internally.
NUMERIC_ENTS     = {"CARDINAL", "ORDINAL", "QUANTITY", "MONEY",
                    "DATE", "TIME", "PERCENT"}

# Numeric ent labels we DON'T want to "reveal" when single-token (these are
# usually part of measure phrases like "thousands of kilometers").
NUMERIC_NO_REVEAL = {"CARDINAL", "QUANTITY", "PERCENT", "ORDINAL"}

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
SHORT_SUBORD_CLAUSE       = 3       # don't split before SCONJ if its clause is ≤ this many tokens
                                    # (was 4 — caused "while the fire crackled" to be skipped because
                                    #  the clause is exactly 4 tokens)


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
    """Largest split index strictly less than *i*. (0 always present.)"""
    return max(s for s in splits if s < i)


def _next_split(splits: Set[int], i: int, doc_len: int) -> int:
    """Smallest split index strictly greater than *i*. (len(doc) always present.)"""
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
# Always split AFTER em / en / double-hyphen dashes.
# A single hyphen INSIDE a word ("self-driving") is filtered via the
# whitespace check.
# Examples:  "fold smaller — it changes the rules"  → split after "—"
#            "and here is another- does it..."      → split after "-"
#            "self-driving"                         → NOT split
# -----------------------------------------------------------------------------
def rule_dashes(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text not in DASH_PUNCT:
            continue
        if (t.text == "-"
                and t.i > 0
                and not t.whitespace_
                and not doc[t.i - 1].whitespace_):
            continue
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
        first_tok = sent[0]
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
        # (a') clausal-end via adverb/adjective whose HEAD is a verb in this
        # sentence — captures "land rose upward, climates changed" where
        # "upward" (ADV) closes the clause headed by "rose" (VERB).
        if prev.pos_ in {"ADV", "ADJ"} and prev.head.pos_ == "VERB" and prev.head.i < prev.i:
            out.add(t.i + 1)
            continue
        # (b) noun list: NOUN, + (NOUN/DET/ADJ/ADV/NUM) with no early verb
        if prev.pos_ in {"NOUN", "PROPN"} and nxt.pos_ in {"NOUN", "PROPN", "DET", "ADJ", "ADV", "NUM"}:
            window_end = min(t.i + 4, len(doc))
            has_early_verb = any(doc[k].pos_ == "VERB" for k in range(t.i + 1, window_end))
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
        prev = _prev_split(splits | out, t.i)
        if t.i - prev > MIN_LEAD_FOR_CLAUSE_SPLIT:
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
        if t.dep_ in VERB_MOD_DEPS:
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
        # short direct object stays with verb
        dobj = next((c for c in t.children if c.dep_ in {"dobj", "obj"}), None)
        if dobj and len(list(dobj.subtree)) <= 3 and (dobj.i - t.i) < 4:
            continue
        # short COMPLEMENT (xcomp/attr/acomp/ccomp) stays with verb.
        # Catches "Which sounds impossible because..." — "sounds" should not
        # split off from its short attr complement "impossible".
        comp = next((c for c in t.children
                     if c.dep_ in {"xcomp", "attr", "acomp", "ccomp"}), None)
        if comp and len(list(comp.subtree)) <= 3 and (comp.i - t.i) < 4:
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
# -----------------------------------------------------------------------------
def rule_long_preps(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "ADP" or t.lower_ in PROMISCUOUS_PREPS:
            continue
        if len(list(t.subtree)) >= LONG_PREP_SUBTREE_MIN:
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
#   "dust and rock"                                   → kept together
#   "endless dunes and unbearable heat"               → 2 lines (chunks > 1 tok)
# -----------------------------------------------------------------------------
def rule_noun_lists(doc: Doc) -> Set[int]:
    out = set()
    chunks = list(doc.noun_chunks)
    for a, b in zip(chunks[:-1], chunks[1:]):
        between = doc[a.end:b.start]
        # 1) verb between → not a list
        if any(x.pos_ == "VERB" for x in between):
            continue
        # 2) hard punctuation between → cross-sentence, not a list
        if any(x.text in HARD_PUNCT for x in between):
            continue
        # 2b) either side is a pronoun-only chunk → not a list, this is just
        #     "the reason it works..." being parsed as two chunks.
        if (len(a) == 1 and a[0].pos_ == "PRON") or (len(b) == 1 and b[0].pos_ == "PRON"):
            continue
        # 3) appositive
        if b.root.dep_ == "appos":
            continue
        # 4) shared dobj/obj head
        if a.root.head == b.root.head and a.root.dep_ in {"dobj", "obj"}:
            continue
        # 5) ordinal appositive: "X the second"
        if b.text.lower().startswith("the ") and len(b) >= 2 and _is_ordinal(b[1]):
            continue
        # 6) single short prep between (≤ 3 chars) — "the lift in the skyscraper"
        if len(between) == 1 and between[0].pos_ == "ADP" and len(between[0].text) <= 3:
            continue
        # 7) "X and Y" where BOTH chunks are single NOUN tokens → keep together
        #    (handles "dust and rock", "salt and pepper", "love and war")
        if (len(a) == 1 and len(b) == 1
                and len(between) == 1 and between[0].pos_ == "CCONJ"
                and a[0].pos_ in {"NOUN", "PROPN"} and b[0].pos_ in {"NOUN", "PROPN"}):
            continue
        # 8) frozen bigram immediately before b ("what if ...")
        if b.start >= 2 and (doc[b.start - 2].lower_, doc[b.start - 1].lower_) in FROZEN_BIGRAMS:
            continue
        # 9) don't slice a multi-token NE
        if _in_compound_ne(doc, b.start):
            continue
        out.add(b.start)
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
    out = set()
    for t in doc:
        if t.pos_ != "CCONJ" or t.lower_ not in {"and", "or"}:
            continue
        if t.i + 1 >= len(doc):
            continue
        nxt = doc[t.i + 1]
        looks_clausal = (nxt.pos_ == "PRON"
                         or (nxt.pos_ in {"AUX", "VERB"} and nxt.dep_ != "amod"))
        if not looks_clausal:
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev > MIN_LEAD_FOR_AND_CLAUSE:
            out.add(t.i)
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
            bad.add(i)
        if left.pos_ == "AUX" and right.pos_ in {"VERB", "AUX"}:
            bad.add(i)
    return bad


# C — never split inside a hyphenated compound ("self-driving", "hyper-arid").
def anti_rule_hyphen_compound(doc: Doc, splits: Set[int]) -> Set[int]:
    return {i for i in splits if _in_hyphen_compound(doc, i)}


# D — never split a possessive marker from its head noun ("Rome's downfall").
def anti_rule_possessive(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        if doc[i - 1].text in {"'s", "'", "\u2019s", "\u2019"}:
            bad.add(i)
        if doc[i].text in {"'s", "\u2019s"}:
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


# G — never split DET from its head noun/adj/num ("the cat", "the second").
def anti_rule_det_head(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        if doc[i - 1].pos_ == "DET" and doc[i].pos_ in {"NOUN", "PROPN", "ADJ", "NUM"}:
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
def anti_rule_to_infinitive(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.lower_ == "to" and left.dep_ == "aux" and right.pos_ == "VERB":
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
            bad.add(i)
    return bad


# R — never split a compound NOUN from its head NOUN.  Examples:
#   "skull fragments" — "skull" dep=compound, head="fragments"
#   "space agencies"  — "space" dep=compound, head="agencies"
#   "salt flats"      — "salt"  dep=compound, head="flats"
# This guards the common case where a modifier noun is split off as its own
# tiny line because some other rule fired between the two tokens.
def anti_rule_compound_noun(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if (left.pos_ in {"NOUN", "PROPN"}
                and right.pos_ in {"NOUN", "PROPN"}
                and left.dep_ == "compound"
                and left.head == right):
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
    """
    span = doc[lo:hi]
    text = span.text.strip()
    if not text:
        return True
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
    """
    span = doc[lo:hi]
    if len(span) == 0:
        return "bwd"
    # SPECIAL: single AUX tokens always introduce a predicate to the right
    if len(span) == 1 and span[0].pos_ == "AUX":
        return "fwd"
    root = span.root
    head = root.head
    if head is root or head.i == root.i:
        return "bwd"
    if head.i >= hi:
        return "fwd"
    if head.i < lo:
        return "bwd"
    return "bwd"


def _fuse_orphans(doc: Doc, chunks: List[str], raw: List[Tuple[int, int]]) -> List[str]:
    """
    Post-merge pass that fixes orphan single-content-word lines.

    Two patterns we fix:

      (1) Single-noun chunk followed by a chunk starting with a relative
          pronoun (that/which/where/who/why/how) — fuse them.
            ['regions', 'that are now brutally dry']
              → ['regions that are now brutally dry']

      (2) Single content-word chunk preceded by a chunk ending in a
          preposition / determiner / "to" — fuse forward into the previous.
            ['the busses took', '4 hours.']
              → ['the busses took 4 hours.']
            ['It costs', 'about two dollars.']
              → ['It costs about two dollars.']
          (The previous chunk's tail is a preposition or aux 'to' — there's
          nothing useful in splitting it off.)

      (3) Single-VERB chunk (gerund / participle) followed by a noun chunk —
          merge them.
            ['revealing', 'evidence of...']
              → ['revealing evidence of...']

    These all collapse pointless one-word lines that don't carry visual
    weight in kinetic typography.
    """
    if len(chunks) <= 1:
        return chunks

    # build per-chunk first/last token info from the raw spans
    # raw spans correspond to chunks pre-merge — we can't easily map after
    # merge, so we fall back to lightweight string heuristics.
    REL_STARTERS = {"that", "which", "where", "who", "whom", "whose",
                    "when", "why", "how"}
    GLUE_TAILS = {"to", "of", "in", "on", "at", "by", "for", "with", "from",
                  "into", "onto", "about", "as", "than", "the", "a", "an"}

    def _word_count(s: str) -> int:
        return len(re.findall(r"\b\w+\b", s))

    def _first_word(s: str) -> str:
        m = re.search(r"\b(\w+)\b", s.lower())
        return m.group(1) if m else ""

    def _last_word(s: str) -> str:
        m = re.findall(r"\b(\w+)\b", s.lower())
        return m[-1] if m else ""

    out: List[str] = []
    i = 0
    while i < len(chunks):
        cur = chunks[i]
        # try forward fusion: cur is single word + next starts with relative pronoun
        if (i + 1 < len(chunks)
                and _word_count(cur) == 1
                and _first_word(chunks[i + 1]) in REL_STARTERS
                and not cur.rstrip().endswith((".", "!", "?"))):
            out.append((cur + " " + chunks[i + 1]).strip())
            i += 2
            continue
        # try forward fusion: cur is short (≤2 words) and next is a NP — but
        # only if cur looks like a leading verb/participle ("revealing X").
        if (i + 1 < len(chunks)
                and _word_count(cur) == 1
                and not cur.rstrip().endswith((".", "!", "?", ",", ":", ";"))
                and out  # need a previous chunk to anchor
                and _last_word(out[-1]) in GLUE_TAILS):
            # the previous chunk ends with a glue word → glue this single word
            # backward into the previous chunk.
            out[-1] = (out[-1] + " " + cur).strip()
            i += 1
            continue
        out.append(cur)
        i += 1
    return out
    """
    Apply the smart head-aware merge.

    Refinement: NEVER glue a throwaway BACKWARD across a sentence boundary —
    if the previous chunk ends in HARD_PUNCT, force forward gluing instead.
    This prevents bugs like "Not near" being appended to a previous "a desert.".
    """
    out: List[str] = []
    fwd_buf = ""

    for lo, hi in raw:
        text = doc[lo:hi].text.strip()
        if not text:
            continue

        if _is_throwaway_span(doc, lo, hi):
            direction = _throwaway_direction(doc, lo, hi)
            # don't glue across sentence boundaries — go forward instead
            if direction == "bwd" and out and out[-1] and out[-1][-1] in HARD_PUNCT:
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
            else:
                fwd_buf += text + " "
        else:
            out.append((fwd_buf + text).strip())
            fwd_buf = ""

    if fwd_buf:
        if out:
            out[-1] = (out[-1] + " " + fwd_buf).strip()
        else:
            out.append(fwd_buf.strip())

    return out


# =============================================================================
# MAIN ENTRY-POINT
# =============================================================================

def split_text_into_sections(text: str) -> List[str]:
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
    """
    text = rule_strip_markdown(text)
    nlp_pipe = _nlp()
    doc = nlp_pipe(text)
    splits: Set[int] = {0, len(doc)}

    # ---- positive rules (add splits) ----------------------------------------
    splits |= rule_hard_punct(doc)
    splits |= rule_dashes(doc)
    splits |= rule_ellipsis(doc)
    splits |= rule_pre_ellipsis_reveal(doc, splits)
    splits |= rule_quotes(doc)
    splits |= rule_brackets(doc)
    splits |= rule_initial_adverbial_comma(doc)
    splits |= rule_comma_split(doc)
    splits |= rule_appositive_comma(doc)
    splits |= rule_clause_starters(doc, splits)
    splits |= rule_but_or_coord(doc, splits)
    splits |= rule_verb_clause(doc)
    splits |= rule_long_lead_in(doc, splits)
    splits |= rule_long_preps(doc)
    splits |= rule_noun_lists(doc)
    splits |= rule_bare_noun_lists(doc, splits)
    splits |= rule_list_quantifiers(doc)
    splits |= rule_entity_reveal(doc, splits)
    splits |= rule_currency_reveal(doc, splits)
    splits |= rule_imperative_start(doc)
    splits |= rule_and_or_clause(doc, splits)

    # ---- forbidden splits (remove) ------------------------------------------
    splits -= anti_rule_compound_ne(doc, splits)
    splits -= anti_rule_aux_main_verb(doc, splits)
    splits -= anti_rule_hyphen_compound(doc, splits)
    splits -= anti_rule_possessive(doc, splits)
    splits -= anti_rule_phrasal_particle(doc, splits)
    splits -= anti_rule_numeric_unit(doc, splits)
    splits -= anti_rule_det_head(doc, splits)
    splits -= anti_rule_inside_quote(doc, splits)
    splits -= anti_rule_inside_bracket(doc, splits)
    splits -= anti_rule_frozen_bigram(doc, splits)
    splits -= anti_rule_adj_noun(doc, splits)
    splits -= anti_rule_to_infinitive(doc, splits)
    splits -= anti_rule_numeric_range(doc, splits)
    splits -= anti_rule_no_split_before_comma(doc, splits)
    splits -= anti_rule_currency_glued(doc, splits)
    splits -= anti_rule_num_unit(doc, splits)
    splits -= anti_rule_neg_modifier(doc, splits)
    splits -= anti_rule_compound_noun(doc, splits)

    # always preserve sentinel splits
    splits |= {0, len(doc)}

    raw = _build_raw_chunks(doc, splits)
    merged = [s for s in _merge_throwaways(doc, raw) if s]
    fused = _fuse_orphans(doc, merged, raw)
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
    ]
    for t in cases:
        _run_test(t)
