"""
sentence_splitter.py
====================
Split prose into short, scannable phrase-lines for visual presentation —
captions, kinetic typography, slide-decks, animation cues, YouTube videos.

DESIGN PHILOSOPHY
-----------------
Mimic how a writer would line-break their own prose for emphasis:

    • LIST items each get their own line.
    • REVEALS (proper nouns, numbers, quoted phrases, dates) get their own line.
    • HARD PUNCTUATION ends a line — full stop, exclaim, question, semi, colon.
    • SHORT CONNECTORS ("and", "the", "where") cling to whichever neighbour
      contains their grammatical HEAD (not just blindly forward or backward).
    • IDIOMATIC UNITS — phrasal verbs, possessives, "what if", named entities,
      hyphenated compounds — stay intact.
    • RUN-ON sentences split at high-confidence boundaries (lists, wh-clauses);
      we suppress weaker verb-level splits inside very long noun-list streams.

The rules are deliberately structural (POS / DEP / NER / sentence position),
not lexical, so they generalise across any vocabulary.  The only hard-coded
lexical sets are punctuation marks and a handful of frozen idioms ("what if").

USAGE
-----
    >>> from sentence_splitter import split_text_into_sections
    >>> split_text_into_sections("The fast cat sat on the comfortable mat")
    ['The fast cat sat', 'on the comfortable mat']
"""
from __future__ import annotations

import re
from typing import List, Set

import spacy
from spacy.tokens import Doc, Span, Token


# =============================================================================
# CONFIG  —  punctuation / structural sets
# (lexical content lives in spaCy's POS/DEP/NER tags, not here, so the rules
#  generalise across vocabulary)
# =============================================================================

# Sentence-final punctuation — always closes a line.
HARD_PUNCT       = {".", "!", "?", ";", ":"}

# Dashes — em (—), en (–), figure (—), double-hyphen (--), single hyphen (-).
DASH_PUNCT       = {"—", "–", "--", "-", "−"}

# Quotation marks — straight, smart, French «», German „".
OPEN_QUOTES      = {'"', "\u201C", "\u2018", "\u00AB", "\u2039", "\u201E", "\u201A", "`"}
CLOSE_QUOTES     = {'"', "\u201D", "\u2019", "\u00BB", "\u203A"}
ANY_QUOTE        = OPEN_QUOTES | CLOSE_QUOTES

# Brackets.
OPEN_BRACKETS    = {"(", "[", "{"}
CLOSE_BRACKETS   = {")", "]", "}"}

# Currency symbols — split BEFORE these when followed by a digit.
CURRENCY_SYMS    = {"$", "£", "€", "¥", "₹", "₽"}

# Penn-Treebank tags for wh-words. Replaces a hard-coded "where/that/who/..."
# list — generalises to who / whom / whose / what / which / where / when /
# why / how with no string matching.
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

# A verb whose dep is one of these is *modifying a noun*, not heading a clause,
# so we do NOT use it as a clause boundary.
#   amod      : "the running man"
#   acl       : "the man running for office"
#   acl:relcl : "the man who is running"
#   advcl     : participle adverbial "running fast, he tripped"
VERB_MOD_DEPS    = {"amod", "acl", "acl:relcl", "advcl"}

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

# Frozen multi-word idioms we never split inside (kept tiny — POS/DEP do the
# rest).  These are bigrams: when token i.lower_ == first and token (i+1).lower_
# == second, no split is allowed between them.
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
    ("by", "the"),        # "by the way" (handled by trigram below as well)
}

# Sentence-initial discourse markers that cling to the rest of their sentence
# even when followed by a comma (e.g. "Anyway, here is..." → no split after).
DISCOURSE_INIT   = {
    "anyway", "well", "so", "now", "yeah", "yep", "okay", "ok", "right",
    "honestly", "actually", "basically", "essentially", "literally",
    "obviously", "clearly", "frankly", "interestingly", "ironically",
    "fortunately", "unfortunately", "naturally",
}


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


def _is_in_runon(doc: Doc, i: int, window: int = 18) -> bool:
    """
    True if there's no hard punctuation within *window* tokens either side of *i*.
    Used to dial DOWN the verb-level split rule inside long list-heavy passages.
    """
    lo = max(0, i - window)
    hi = min(len(doc), i + window)
    return not any(t.text in HARD_PUNCT for t in doc[lo:hi])


def _is_frozen_bigram_split(doc: Doc, i: int) -> bool:
    """True if cutting at *i* would split a frozen bigram like 'what if'."""
    if i <= 0 or i >= len(doc):
        return False
    pair = (doc[i - 1].lower_, doc[i].lower_)
    return pair in FROZEN_BIGRAMS


def _sentence_position(tok: Token) -> int:
    """Position of *tok* within its sentence (0 = first token of sentence)."""
    return tok.i - tok.sent.start


# =============================================================================
# RULE FUNCTIONS  —  each returns a *set of token indices* where a split is
# desired.  "Split at i" means: the chunk boundary lies BEFORE doc[i].
# =============================================================================

# -----------------------------------------------------------------------------
# RULE 0 — strip markdown headings (preprocessing on raw text).
# Examples:  "# Title"  →  removed
#            "### Subsection"  →  removed
# -----------------------------------------------------------------------------
def rule_strip_markdown(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


# -----------------------------------------------------------------------------
# RULE 1 — HARD PUNCTUATION  (.  !  ?  ;  :)
# Always end a line at sentence-final punctuation.
# Examples:  "He left."        → "He left." +
#            "Wait! Stop!"     → "Wait!"  /  "Stop!"
#            "Look: a bird."   → "Look:"  /  "a bird."
# Note: comma is NOT a hard split — too fine-grained for kinetic-typography.
# -----------------------------------------------------------------------------
def rule_hard_punct(doc: Doc) -> Set[int]:
    return {t.i + 1 for t in doc if t.text in HARD_PUNCT}


# -----------------------------------------------------------------------------
# RULE 2 — DASHES  (—  –  --  -)
# Always split AFTER em / en / double-hyphen dashes.
# A single hyphen INSIDE a word (no surrounding spaces) is part of a compound
# and is skipped — see _in_hyphen_compound for the anti-rule.
# Examples:  "fold smaller — it changes the rules"  → split after "—"
#            "and here is another- does it..."      → split after "-"
#            "self-driving"                         → NOT split
# -----------------------------------------------------------------------------
def rule_dashes(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text not in DASH_PUNCT:
            continue
        # in-word hyphen ("self-driving") — skip
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
# RULE 4 — QUOTATION MARKS
# Quoted phrases always get their own line.
# Split BEFORE an opening quote and AFTER a closing quote.
# Examples:  He calls them "peppers" to please ...
#               → ["He calls them", '"peppers"', "to please ..."]
#            Saying "Alright," he ...
#               → ["Saying", '"Alright,"', "he ..."]
# Anti-rule H below prevents splits INSIDE the quoted span.
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
# RULE 5 — BRACKETS  (parentheses, square, curly)
# Same logic as quotes — parenthetical asides get their own line.
# Examples:  "the cost (about £1,000) is high"
#               → ["the cost", "(about £1,000)", "is high"]
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
# RULE 6 — COMMA AFTER SENTENCE-INITIAL ADVERBIAL
# When a sentence opens with a substantial adverbial phrase that ends in a
# comma, give the adverbial its own line.
#
#   "Back in 1946, the technician..."     → "Back in 1946," / "the technician..."
#   "Two thousand years ago, the Romans..." → "Two thousand years ago," / "the Romans..."
#   "By the late 1400s, European powers..." → "By the late 1400s," / "European powers..."
#
# We DO NOT split when the lead-up is just a single discourse word:
#   "Anyway, here is a sentence"  → kept together
#   "Now, that's better"          → kept together
#
# Conditions:
#   • comma falls within first ~6 tokens of the sentence
#   • lead-up has ≥ 3 tokens OR contains a NUM / DATE / PROPN
#   • lead-up's first token is NOT a known discourse marker
# -----------------------------------------------------------------------------
def rule_initial_adverbial_comma(doc: Doc) -> Set[int]:
    out = set()
    for sent in doc.sents:
        first_tok = sent[0]
        if first_tok.lower_ in DISCOURSE_INIT:
            continue
        for t in sent[: min(7, len(sent))]:
            if t.text != ",":
                continue
            lead = sent[: t.i - sent.start]
            has_substance = (len(lead) >= 3
                             or any(x.like_num
                                    or x.ent_type_ in {"DATE", "TIME", "GPE", "LOC", "PERSON"}
                                    or x.pos_ == "PROPN" for x in lead))
            if has_substance and t.i + 1 < len(doc):
                out.add(t.i + 1)
            break       # only the FIRST comma in the sentence
    return out


# -----------------------------------------------------------------------------
# RULE 7 — CLAUSE-STARTER WH-WORDS / SUBORDINATING CONJUNCTIONS
# Split BEFORE a wh-word (WDT/WP/WP$/WRB) or SCONJ when it begins a subordinate
# clause AND the lead-in is at least a few tokens.  Detection by tag/POS — no
# hard-coded list, so it catches that / which / whose / how / why / where /
# when / while / as / because / since / though / if / unless / although / ...
#
# Examples:
#   "the man WHO arrived"        → ["the man", "who arrived"]
#   "we left WHEN it rained"     → ["we left", "when it rained"]
#   "the bread WHILE the fire crackled"
#                                → ["...the bread", "while the fire crackled"]
#   "...the world, WHICH would..."  → ["...the world,", "which would..."]
#
# Frozen bigrams "what if", "as if", "even if", "as though" stay intact.
# -----------------------------------------------------------------------------
def rule_clause_starters(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        is_wh    = t.tag_ in WH_TAGS
        is_sconj = t.pos_ == "SCONJ"
        if not (is_wh or is_sconj):
            continue
        # frozen bigram check — don't break inside "what if", "as if", ...
        if _is_frozen_bigram_split(doc, t.i):
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev > 3:
            out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 8 — COORDINATING "BUT" / "OR" WITH LONG LEAD-IN
# CCONJ "but" / "or" between clauses gets a split before it when the lead-up
# is substantial.  We deliberately exclude "and" here (handled by RULE 11
# instead, which knows the difference between list-and and clause-and).
#
# Examples:
#   "I tried hard but it failed"  → ["I tried hard", "but it failed"]
#   "drive or take the train"     → kept together (lead-in too short)
# -----------------------------------------------------------------------------
def rule_but_or_coord(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "CCONJ" or t.lower_ not in {"but", "or"}:
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev > 3:
            out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 9 — VERB CLAUSE BOUNDARIES
# Split AFTER a verb whose clause has finished, BUT keep:
#   • short S-V-O patterns intact   ("kneaded the bread" — verb + tiny dobj)
#   • phrasal verbs intact          ("sped past", "laid down" — verb + prt)
#   • verb modifiers intact         ("the running man" — amod / acl)
#   • aux + main verb intact        (handled separately by anti-rule B)
#   • verbs in run-on contexts      (let list rules dominate, see RULE 14)
#
# Examples:
#   "The fast cat sat on the comfortable mat"
#       → ["The fast cat sat", "on the comfortable mat"]
#   "The baker kneaded the bread while the fire crackled"
#       → splits via RULE 7's "while", verb rule keeps "kneaded the bread"
# -----------------------------------------------------------------------------
def rule_verb_clause(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "VERB":
            continue
        # verb that's modifying a noun → not a clause boundary
        if t.dep_ in VERB_MOD_DEPS:
            continue
        # phrasal-verb particle next → keep together
        if t.i + 1 < len(doc) and doc[t.i + 1].dep_ in PARTICLE_DEPS:
            continue
        # "to <verb>" infinitives — t.dep_ might be xcomp; keep with "to"
        if t.i > 0 and doc[t.i - 1].lower_ == "to" and doc[t.i - 1].dep_ == "aux":
            continue
        # short direct object stays with verb
        dobj = next((c for c in t.children if c.dep_ in {"dobj", "obj"}), None)
        if dobj and len(list(dobj.subtree)) <= 3 and (dobj.i - t.i) < 4:
            continue
        # RUN-ON SUPPRESSION: in a long stretch with no punctuation, prefer
        # to let list rules (12, 13) do the splitting.  Keep the verb attached
        # to a short prep complement so we don't over-fragment.
        if _is_in_runon(doc, t.i):
            prep = next((c for c in t.children if c.dep_ in {"prep", "case"}), None)
            if prep and len(list(prep.subtree)) <= 5:
                continue
        # ROOT verbs handled by RULE 10's long-lead-in heuristic
        if t.dep_ == "ROOT":
            continue
        out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 10 — LONG LEAD-IN TO ROOT VERB
# Safety net for run-on sentences with no punctuation: if the preamble before
# the main verb has gone on for many tokens with no break, force a split
# BEFORE the ROOT verb.
# Threshold: 12 tokens.
# -----------------------------------------------------------------------------
def rule_long_lead_in(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "VERB" or t.dep_ != "ROOT":
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev > 12:
            out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 11 — LONG PREPOSITIONAL PHRASES
# Keep SHORT prep phrases intact ("in the box", "at home").  Long ones — more
# than ~4 tokens of reach before next punctuation — get a break after the prep.
# "of" is excluded because it almost always wants to bind to its head NP.
# Examples:
#   "in the box"                     → kept
#   "in the middle of nowhere with"  → splits after the long PP head
# -----------------------------------------------------------------------------
def rule_long_preps(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "ADP" or t.lower_ in PROMISCUOUS_PREPS:
            continue
        j = t.i + 1
        while j < len(doc) and doc[j].text not in HARD_PUNCT:
            j += 1
        if j - t.i > 4:
            out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 12 — NOUN-PHRASE LISTS  (the canonical list-detector)
# When consecutive noun chunks appear with no intervening verb, treat them as
# a list and split between them.
#
# Skips:
#   • appositives ("the city, Rome")
#   • two NPs sharing the same dobj/obj head
#   • ordinal appositive ("John Ford the second")
#   • single short prep between (in / on / at)  →  "the lift in the skyscraper"
#   • frozen bigram inside ("what if X and Y")
#   • cuts that would slice a multi-token NE
#
# Examples:
#   "the red car the blue truck the green bike sped"  →  splits between each
#   "John Ford the second"                            →  kept together
# -----------------------------------------------------------------------------
def rule_noun_lists(doc: Doc) -> Set[int]:
    out = set()
    chunks = list(doc.noun_chunks)
    for a, b in zip(chunks[:-1], chunks[1:]):
        between = doc[a.end:b.start]
        # 1) verb between → not a list
        if any(x.pos_ == "VERB" for x in between):
            continue
        # 2) appositive
        if b.root.dep_ == "appos":
            continue
        # 3) shared dobj/obj head
        if a.root.head == b.root.head and a.root.dep_ in {"dobj", "obj"}:
            continue
        # 4) ordinal appositive: "X the second"
        if b.text.lower().startswith("the ") and len(b) >= 2 and _is_ordinal(b[1]):
            continue
        # 5) single short prep between (≤3 chars) — "the lift in the skyscraper"
        if len(between) == 1 and between[0].pos_ == "ADP" and len(between[0].text) <= 3:
            continue
        # 6) frozen bigram immediately before b ("what if ...")
        if b.start >= 2 and (doc[b.start - 2].lower_, doc[b.start - 1].lower_) in FROZEN_BIGRAMS:
            continue
        # 7) don't slice a multi-token NE
        if _in_compound_ne(doc, b.start):
            continue
        out.add(b.start)
    return out


# -----------------------------------------------------------------------------
# RULE 13 — BARE  NOUN-DET-NOUN  LISTS
# Catches stretches that spaCy's noun_chunks may miss:
# split BEFORE a determiner if the previous token is a NOUN/PROPN AND no verb
# has appeared since the last split.
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
        # skip ordinal: "X the second"
        if i + 1 < len(doc) and _is_ordinal(doc[i + 1]):
            continue
        # don't slice a multi-token NE
        if _in_compound_ne(doc, i):
            continue
        prev = _prev_split(splits | out, i)
        if any(t.pos_ == "VERB" for t in doc[prev:i]):
            continue
        out.add(i)
    return out


# -----------------------------------------------------------------------------
# RULE 14 — LIST-CLOSING QUANTIFIERS
# Split BEFORE "all" / "both" / "each" / "every" when they immediately follow
# a noun — typical of summary-after-list constructions.
# Examples:  "...the dog all walked along..."  → split before "all"
#            "...the cars both stopped..."     → split before "both"
# -----------------------------------------------------------------------------
def rule_list_quantifiers(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.lower_ in {"all", "both", "each", "every"} and t.i > 0 \
                and doc[t.i - 1].pos_ in {"NOUN", "PROPN"}:
            out.add(t.i)
    return out


# -----------------------------------------------------------------------------
# RULE 15 — NAMED-ENTITY REVEAL
# Split BEFORE the start of a "reveal" entity (PERSON, GPE, ORG, DATE, MONEY,
# ...) when it's introduced after at least a couple of tokens of build-up.
#
# Examples:
#   "...native to Sri Lanka"
#       → ["...native to", "Sri Lanka"]
#   "...specifically Kerala"
#       → ["...specifically", "Kerala"]
#   "Christopher Columbus" / "Alaric the Goth"
#       → introduced on their own line in YouTube-script context
#
# Heuristic: split at entity start if ≥2 tokens have passed since the previous
# split AND the entity start is at a B- (begin) tag, not I-.
# -----------------------------------------------------------------------------
def rule_entity_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    seen = set()
    for ent in doc.ents:
        if ent.label_ not in REVEAL_ENTS:
            continue
        i = ent.start
        if i == 0 or i in seen:
            continue
        seen.add(i)
        # only "reveal" if there's enough lead-in
        prev = _prev_split(splits | out, i)
        if i - prev < 2:
            continue
        # must be a B-tag boundary (not mid-entity)
        if doc[i].ent_iob_ == "I":
            continue
        out.add(i)
    return out


# -----------------------------------------------------------------------------
# RULE 16 — CURRENCY-AMOUNT REVEAL
# Currency symbols followed by a number get their own dramatic intro.
# Examples:
#   "It costs $800,000"   → ["It costs", "$800,000"]
#   "Just £1,000"         → ["Just", "£1,000"]
# Anti-rule F protects the inside of "$800,000" from being cut.
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
# RULE 17 — APPOSITIVE COMMA
# When a comma precedes an appositive noun phrase that re-names or clarifies,
# split BEFORE the appositive — but only for non-trivial appositives (the
# YouTube examples show ", which would..." gets its own line).
#
# Examples:
#   "...chanoyu tea, which would go on..."  → ["...chanoyu tea,", "which would..."]
#   "...the city, Rome"                     → handled by RULE 7's WDT/SCONJ
# -----------------------------------------------------------------------------
def rule_appositive_comma(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text != ",":
            continue
        if t.i + 1 >= len(doc):
            continue
        nxt = doc[t.i + 1]
        # only split before relative-clause / wh appositives
        if nxt.tag_ in WH_TAGS:
            out.add(t.i + 1)
    return out


# -----------------------------------------------------------------------------
# RULE 18 — IMPERATIVE / SHORT COMMAND SENTENCES
# A sentence consisting of a verb at position 0 followed by ≤3 tokens often
# wants its own line (already comes for free via hard punct, but this rule
# also forces a break BEFORE such a sentence in case the previous one ran on).
# Examples:  "Bike. Fold. Train."
#            "Listen up."
# -----------------------------------------------------------------------------
def rule_imperative_start(doc: Doc) -> Set[int]:
    out = set()
    for sent in doc.sents:
        if len(sent) <= 4 and sent[0].pos_ == "VERB" and sent[0].dep_ == "ROOT":
            out.add(sent.start)
    return out


# -----------------------------------------------------------------------------
# RULE 19 — AND/OR BETWEEN INDEPENDENT CLAUSES
# CCONJ "and"/"or" splits BEFORE itself when followed by a clear new-clause
# starter (pronoun + verb, or "I/we/you/he/she/it/they" + AUX), and the
# lead-in is substantial.  Distinguishes list-and ("cat and dog") from
# clause-and ("he ran and she walked").
#
# Examples:
#   "He ran and she walked"     → ["He ran", "and she walked"]
#   "cat and dog"               → kept (next token is NOUN, not pronoun+verb)
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
        if t.i - prev > 5:
            out.add(t.i)
    return out


# =============================================================================
# ANTI-RULES  —  return indices to *remove* from the splits set.
# These keep idiomatic / multi-token units intact.
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

# C — never split inside a hyphenated compound ("self-driving").
def anti_rule_hyphen_compound(doc: Doc, splits: Set[int]) -> Set[int]:
    return {i for i in splits if _in_hyphen_compound(doc, i)}

# D — never split a possessive from its head noun ("Rome's downfall").
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
        # "<NUM> <NOUN>" pairs that aren't NER-tagged but read as units
        if left.like_num and right.pos_ == "NOUN" and right.dep_ in {"compound", "nummod", "nmod"}:
            bad.add(i)
    return bad

# G — never split DET (or PRON-determiner) from its head noun ("the cat").
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

# J — never split a frozen bigram ("what if", "as if", "kind of", ...).
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

# M — never split before a determiner if the previous chunk would be just one
# function-word (avoids one-word "and" / "or" / "but" lines mid-list when we
# can do better by attaching them to a neighbour during merge).
# (No-op here — the merge phase handles this.  Kept as a comment for clarity.)

# N — never split inside a numeric range ("1-2", "9:30").
# spaCy usually tokenizes these as a single token, so explicit handling is
# rarely required; included for completeness.
def anti_rule_numeric_range(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        # "9", ":", "30" → keep around the colon
        if left.text == ":" and left.i > 0 and doc[left.i - 1].like_num and right.like_num:
            bad.add(i)
    return bad


# =============================================================================
# CHUNK BUILDING & SMART MERGE LOGIC
# =============================================================================

def _build_raw_chunks(doc: Doc, splits: Set[int]) -> List[tuple[int, int]]:
    idx = sorted(splits)
    return [(idx[i], idx[i + 1]) for i in range(len(idx) - 1)]


def _is_throwaway_span(doc: Doc, lo: int, hi: int) -> bool:
    """
    A chunk is "throwaway" (will be glued to a neighbour) when:
      • it's very short (< 3 words), AND
      • it has no content word (NOUN / PROPN / NUM / VERB / ADJ), AND
      • it doesn't end with hard punctuation.
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
    If the head is to the right of the span, return 'fwd' (merge with next).
    If the head is to the left, return 'bwd' (merge with previous).
    Tie / no head → default 'bwd' so trailing connectives like "and" stick to
    the line they read against.

    Examples:
      "...the patient dog | all | walked along..."
          'all' has head 'walked' (right) → fwd → "the patient dog / all walked along..."
      "the curious child | and | the patient dog"
          'and' is conj of 'child' (left) → bwd → "the curious child and / the patient dog"
    """
    span = doc[lo:hi]
    if len(span) == 0:
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


def _merge_throwaways(doc: Doc, raw: List[tuple[int, int]]) -> List[str]:
    """Apply the smart head-aware merge."""
    out: List[str] = []
    fwd_buf = ""

    for lo, hi in raw:
        text = doc[lo:hi].text.strip()
        if not text:
            continue

        if _is_throwaway_span(doc, lo, hi):
            direction = _throwaway_direction(doc, lo, hi)
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
    typography, captions or YouTube-style on-screen text.

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
    splits |= rule_quotes(doc)
    splits |= rule_brackets(doc)
    splits |= rule_initial_adverbial_comma(doc)
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
    splits |= rule_appositive_comma(doc)
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

    # always preserve sentinel splits
    splits |= {0, len(doc)}

    raw = _build_raw_chunks(doc, splits)
    return [s for s in _merge_throwaways(doc, raw) if s]


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
        # Original suite
        "#first heading\nThe old lighthouse keeper the wandering sailor the curious child "
        "and the patient dog all walked along the endless shoreline where the crashing waves "
        "the drifting clouds the distant mountains and the whispering wind created a tapestry "
        "of motion and sound that inspired the painter the poet the musician and the dreamer "
        "who gathered their brushes their notebooks their instruments and their hopes as the "
        "gentle sun the rising tide the circling gulls and the rustling dunes surrounded them "
        "with a quiet reminder that the world the sea the sky and the land are always alive "
        "with stories waiting to be found",

        "The fast cat sat on the comfortable mat",
        "The baker kneaded the bread while the fire crackled",
        "The red car the blue truck the green bike sped past the house",
        "anyway, here is a sentence that has punctuatoin",
        "and here is another- does it handle everything all fine? but what if the other person "
        "and the dragon laid down together at the edge of the brook?",
        "The empire state building is really big. Built in Manhattan in the 19th century. "
        "Back in 1946, the technician John Ford the second created a new OpenAI carburettor "
        "for the lift in the skyscraper where they drunk chanoyu tea, which would go on to "
        "revolutionize the entire world.",

        # YouTube-style additions
        'He calls them "peppers" to make his investors happy.',
        "Yep... New York City was traded for nutmeg.",
        "It costs about $800,000 over a lifetime.",
        "Two thousand years ago, the Romans built the fastest delivery system on Earth.",
        "Bike. Fold. Train. Unfold. Bike again.",
    ]
    for t in cases:
        _run_test(t)
