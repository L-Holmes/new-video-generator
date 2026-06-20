"""
sentence_splitter.py
====================
VERSION: v18  (ordered-map chunk representation)

Split prose into short, scannable phrase-lines for visual presentation —
captions, kinetic typography, slide-decks, animation cues, YouTube videos.

CHANGES FROM v17:
  • Chunks are no longer List[str].  They are now an ordered map (a list of
    single-key dicts) mapping each chunk's text to an integer list:
        [{"the big dog": []}, {"jumped over": []}, {"the fox": []}]
    The int list is empty for now, but the plumbing is in place.  When two
    chunks merge, their int lists are concatenated via _merge_chunks().
  • Helper functions _chunk(), _chunk_text(), _chunk_ints(), _merge_chunks()
    centralise all creation / extraction / merging of these items.
"""
from __future__ import annotations

import re
from typing import List, Set, Optional, Tuple, Dict

import spacy
from spacy.tokens import Doc, Span, Token


# === VERSION marker ====================================================
VERSION = "v18-2026-05-13"

SINGLE_RUN_DEBUG = False


# =============================================================================
# ORDERED-MAP CHUNK TYPE
#
# A ChunkMap is a list of single-key dicts.  Each dict maps a chunk's text
# (the "sentence") to an integer list.  The list is empty for now, but the
# plumbing is in place so that downstream code can attach metadata to each
# chunk and have it survive splits (which create fresh empty lists) and
# merges (which concatenate the lists).
# =============================================================================
ChunkMap = List[Dict[str, List[int]]]


def _chunk(text: str, ints: Optional[List[int]] = None) -> Dict[str, List[int]]:
    """Build a single-key chunk dict: {text: ints} (ints defaults to [])."""
    return {text: list(ints) if ints else []}


def _chunk_text(c: Dict[str, List[int]]) -> str:
    """Extract the text key from a chunk dict."""
    return next(iter(c.keys()))


def _chunk_ints(c: Dict[str, List[int]]) -> List[int]:
    """Extract the int list from a chunk dict."""
    return next(iter(c.values()))


def _merge_chunks(a: Dict[str, List[int]],
                  b: Dict[str, List[int]],
                  sep: str = " ") -> Dict[str, List[int]]:
    """Merge two chunk dicts.

    Concatenates the text (with *sep* between the two halves when both are
    non-empty) and CONCATENATES the int lists.  This is the only function
    that combines two chunk items; every merge in the pipeline goes through
    here so int-list merging is consistent.
    """
    ta, tb = _chunk_text(a), _chunk_text(b)
    if sep and ta and tb:
        merged_text = (ta + sep + tb).strip()
    else:
        merged_text = (ta + tb).strip()
    return {merged_text: _chunk_ints(a) + _chunk_ints(b)}


# =============================================================================
# CONFIG  (unchanged from v17)
# =============================================================================
HARD_PUNCT       = {".", "!", "?", ";", ":"}
DASH_PUNCT       = {"—", "–", "--", "-", "−"}
LONG_DASH_PUNCT  = {"—", "–", "--"}
OPEN_QUOTES      = {'"', "\u201C", "\u2018", "\u00AB", "\u2039", "\u201E", "\u201A", "`"}
CLOSE_QUOTES     = {'"', "\u201D", "\u2019", "\u00BB", "\u203A"}
ANY_QUOTE        = OPEN_QUOTES | CLOSE_QUOTES
OPEN_BRACKETS    = {"(", "[", "{"}
CLOSE_BRACKETS   = {")", "]", "}"}
CURRENCY_SYMS    = {"$", "£", "€", "¥", "₹", "₽", "¢"}
WH_TAGS          = {"WDT", "WP", "WP$", "WRB"}
REVEAL_ENTS      = {
    "PERSON", "ORG", "GPE", "LOC", "FAC", "NORP",
    "EVENT", "WORK_OF_ART", "PRODUCT", "LAW", "LANGUAGE",
    "DATE", "TIME", "MONEY", "QUANTITY", "PERCENT",
}
LOCATION_ENTS    = {"GPE", "LOC", "FAC", "ORG", "PERSON", "EVENT", "WORK_OF_ART"}
NUMERIC_ENTS     = {"CARDINAL", "ORDINAL", "QUANTITY", "MONEY",
                    "DATE", "TIME", "PERCENT"}
NUMERIC_NO_REVEAL = {"CARDINAL", "QUANTITY", "PERCENT", "ORDINAL"}
NUMERIC_QUALIFIER_ENTS = {"DATE", "TIME", "MONEY", "QUANTITY",
                          "PERCENT", "CARDINAL", "ORDINAL"}
VERB_MOD_DEPS    = {"amod", "acl", "acl:relcl", "advcl", "relcl",
                    "ccomp", "xcomp", "oprd", "csubj"}
AUX_LIKE_DEPS    = {"aux", "auxpass", "neg"}
PARTICLE_DEPS    = {"prt", "compound:prt"}
LIGHTWEIGHT_POS  = {"CCONJ", "SCONJ", "DET", "ADP", "PART", "PRON", "AUX",
                    "ADV", "INTJ"}
PROMISCUOUS_PREPS = {"of"}
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
FROZEN_BIGRAMS   = {
    ("what", "if"), ("as", "if"), ("even", "if"), ("as", "though"),
    ("kind", "of"), ("sort", "of"), ("type", "of"), ("a", "lot"),
    ("at", "least"), ("at", "most"), ("at", "all"), ("of", "course"),
    ("used", "to"), ("able", "to"), ("going", "to"), ("have", "to"),
    ("had", "to"), ("got", "to"), ("want", "to"), ("wants", "to"),
    ("wanted", "to"), ("need", "to"), ("needs", "to"), ("needed", "to"),
    ("try", "to"), ("tried", "to"),
}
DISCOURSE_INIT   = {
    "anyway", "well", "so", "now", "yeah", "yep", "okay", "ok", "right",
    "honestly", "actually", "basically", "essentially", "literally",
    "obviously", "clearly", "frankly", "interestingly", "ironically",
    "fortunately", "unfortunately", "naturally",
    "hmm", "huh", "oh", "ah",
}
ADV_INTRODUCERS  = {
    "specifically", "especially", "namely", "particularly", "notably",
    "essentially", "primarily", "mainly", "chiefly", "principally",
    "exactly", "precisely", "literally",
}
TRANSITION_ADVERBS = {
    "then", "later", "suddenly", "eventually", "finally",
    "afterwards", "subsequently", "next", "soon", "now",
}
MIN_LEAD_FOR_CLAUSE_SPLIT = 3
MIN_LEAD_FOR_BUT_OR       = 3
MIN_LEAD_FOR_AND_CLAUSE   = 5
MIN_LEAD_FOR_ENTITY       = 2
LONG_PREP_SUBTREE_MIN     = 5
RUNON_SENT_MIN_TOKENS     = 30
RUNON_WINDOW              = 18
LONG_LEAD_TO_ROOT         = 12
SHORT_TAIL_TO_PUNCT       = 3
SHORT_SUBORD_CLAUSE       = 2
SHORT_SENT_NO_SPLIT       = 4
LONG_SUBORD_OPENER_TOKENS = 6
LONG_COMMA_LEAD_CONTENT   = 5
LONG_COMMA_TAIL_CONTENT   = 3
INFINITIVE_SPLIT_SENT_MIN = 12
INFINITIVE_SPLIT_LEAD_MIN = 2
INFINITIVE_SPLIT_TAIL_MIN = 4
OF_REVEAL_SENT_MIN        = 12
OF_REVEAL_LEAD_MIN        = 5
PROGRESSIVE_SENT_MIN      = 10
PROGRESSIVE_LEAD_MIN      = 3
COPULA_REVEAL_SENT_MIN    = 7
COPULA_REVEAL_CHUNK_MIN   = 2
PP_PRON_PART_SENT_MIN     = 9
PP_PRON_PART_LEAD_MIN     = 2
AUX_PP_REVEAL_LEAD_MIN    = 3
CHAINED_PART_SENT_MIN     = 11
DOBJ_DISQUAL_SENT_MIN     = 8
COMPARATIVE_MARKERS = {"than", "more", "less", "fewer"}


# =============================================================================
# spaCy MODEL
# =============================================================================
_NLP = None
def _nlp() -> "spacy.language.Language":
    global _NLP
    if _NLP is None:
        _NLP = spacy.load("en_core_web_sm")
    return _NLP


# =============================================================================
# UTILITY HELPERS  (unchanged from v17 unless noted)
# =============================================================================
def _is_equation_use(verb_tok: Token) -> bool:
    lemma = verb_tok.lemma_.lower()
    if lemma not in EQUATION_PHRASAL_PARTICLES:
        return True
    required_parts = EQUATION_PHRASAL_PARTICLES[lemma]
    for child in verb_tok.children:
        if child.lower_ in required_parts and child.pos_ in {"ADP", "PART"}:
            return True
    doc = verb_tok.doc
    for j in range(verb_tok.i + 1, min(verb_tok.i + 4, len(doc))):
        if doc[j].lower_ in required_parts:
            return True
        if doc[j].text in HARD_PUNCT:
            break
    return False

def _has_substantial_complement(verb_tok: Token) -> Tuple[bool, Optional[Token]]:
    dobj = next((c for c in verb_tok.children if c.dep_ in {"dobj", "obj"}), None)
    if dobj is not None:
        subtree = list(dobj.subtree)
        if len(subtree) >= 3:
            return (True, dobj)
        n_adj = sum(1 for x in subtree if x.pos_ == "ADJ")
        n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
        has_relcl = any(c.dep_ in {"acl", "acl:relcl", "relcl"}
                        for c in dobj.children)
        has_comp = any(x.lower_ in COMPARATIVE_MARKERS for x in subtree)
        if n_adj >= 1 or n_nouns >= 2 or has_relcl or has_comp:
            return (True, dobj)
    ccomp = next((c for c in verb_tok.children if c.dep_ == "ccomp"), None)
    if ccomp is not None:
        subtree_len = len(list(ccomp.subtree))
        if subtree_len >= 3:
            return (True, ccomp)
    xcomp = next((c for c in verb_tok.children
                  if c.dep_ == "xcomp" and c.pos_ == "VERB"), None)
    if xcomp is not None:
        return (True, xcomp)
    return (False, None)

def _is_substantial_dobj(verb_tok: Token) -> bool:
    dobj = next((c for c in verb_tok.children if c.dep_ in {"dobj", "obj"}), None)
    if dobj is None:
        return False
    subtree = list(dobj.subtree)
    if len(subtree) >= 3:
        return True
    n_adj = sum(1 for x in subtree if x.pos_ == "ADJ")
    n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
    has_relcl = any(c.dep_ in {"acl", "acl:relcl", "relcl"}
                    for c in dobj.children)
    has_comp = any(x.lower_ in COMPARATIVE_MARKERS for x in subtree)
    return n_adj >= 1 or n_nouns >= 2 or has_relcl or has_comp

def _prev_split(splits: Set[int], i: int) -> int:
    return max((s for s in splits if s < i), default=0)

def _next_split(splits: Set[int], i: int, doc_len: int) -> int:
    return min((s for s in splits if s > i), default=doc_len)

def _in_compound_ne(doc: Doc, i: int) -> bool:
    if i <= 0 or i >= len(doc):
        return False
    left, right = doc[i - 1], doc[i]
    return (left.ent_iob_ in {"B", "I"}
            and right.ent_iob_ == "I"
            and left.ent_type_ == right.ent_type_)

def _is_copular_use(verb_tok: Token) -> bool:
    for child in verb_tok.children:
        if child.dep_ in {"acomp", "oprd", "attr"}:
            return True
        if child.dep_ == "xcomp" and child.pos_ in {"ADJ", "VERB", "AUX"}:
            return True
        if child.dep_ == "ccomp" and (child.tag_ in {"VBN", "VBG"}
                                       or child.pos_ == "ADJ"):
            return True
    return False

def _in_hyphen_compound(doc: Doc, i: int) -> bool:
    if i <= 0 or i >= len(doc):
        return False
    if doc[i - 1].text == "-" and not doc[i - 1].whitespace_:
        return True
    if doc[i].text == "-" and not doc[i - 1].whitespace_:
        return True
    return False

def _is_ordinal(tok: Token) -> bool:
    if tok.ent_type_ == "ORDINAL":
        return True
    return bool(re.match(r"^\d+(st|nd|rd|th)$", tok.lower_))

def _matches_ellipsis(text: str) -> bool:
    return bool(re.fullmatch(r"\.{2,}|\u2026+", text))

def _is_big_punct_split(doc: Doc, i: int) -> bool:
    if i <= 0 or i > len(doc):
        return False
    BIG = HARD_PUNCT | DASH_PUNCT | ANY_QUOTE | OPEN_BRACKETS | CLOSE_BRACKETS
    if i > 0:
        lt = doc[i - 1]
        if lt.text in BIG or _matches_ellipsis(lt.text):
            return True
    if i < len(doc):
        rt = doc[i]
        if rt.text in BIG or _matches_ellipsis(rt.text):
            return True
    return False

def _is_in_runon(doc: Doc, i: int) -> bool:
    sent = doc[i].sent
    if len(sent) < RUNON_SENT_MIN_TOKENS:
        return False
    lo = max(0, i - RUNON_WINDOW)
    hi = min(len(doc), i + RUNON_WINDOW)
    return not any(t.text in HARD_PUNCT for t in doc[lo:hi])

def _is_frozen_bigram_split(doc: Doc, i: int) -> bool:
    if i <= 0 or i >= len(doc):
        return False
    pair = (doc[i - 1].lower_, doc[i].lower_)
    return pair in FROZEN_BIGRAMS

def _chunk_containing(doc: Doc, i: int) -> Optional[Span]:
    for nc in doc.noun_chunks:
        if nc.start <= i < nc.end:
            return nc
    return None

def _tokens_to_next_punct(doc: Doc, i: int) -> int:
    j = i
    while j < len(doc) and doc[j].text not in HARD_PUNCT and doc[j].text != ",":
        j += 1
    return j - i

def _content_count(doc: Doc, lo: int, hi: int) -> int:
    return sum(1 for x in doc[lo:hi]
               if x.pos_ in {"NOUN", "PROPN", "VERB", "ADJ", "ADV", "NUM"})


# --- debug helpers ---------------------------------------------------------

def _splits_to_chunks_list(doc: Doc, splits: Set[int]) -> ChunkMap:
    """Build a ChunkMap (list of single-key dicts) from a splits set."""
    idx = sorted(splits)
    chunks: ChunkMap = []
    for i in range(len(idx) - 1):
        text = doc[idx[i]:idx[i + 1]].text.strip()
        if text:
            chunks.append(_chunk(text))
    return chunks


def _format_chunks_debug(chunks) -> str:
    """Format chunks for debug display with '|||' separators.

    Accepts a ChunkMap (list of single-key dicts) or a plain List[str]
    (used for the "Original:" line where we don't yet have a ChunkMap).
    """
    if not chunks:
        return "[]"
    parts = []
    for c in chunks:
        if isinstance(c, dict):
            text = _chunk_text(c)
            ints = _chunk_ints(c)
            if ints:
                parts.append(f'"{text}": {ints}')
            else:
                parts.append(f'"{text}": []')
        else:
            parts.append(f'"{c}"')
    return "[" + " ||| ".join(parts) + "]"


def _debug_print_stage(name: str, was_applied: bool,
                       doc_or_chunks) -> None:
    status = "TRUE" if was_applied else "FALSE"
    bang = " !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" if was_applied else ""
    if isinstance(doc_or_chunks, tuple):
        doc, splits = doc_or_chunks
        chunks = _splits_to_chunks_list(doc, splits)
    else:
        chunks = doc_or_chunks
    print(f"==> {name} ({status}){bang}")
    print(f"    {_format_chunks_debug(chunks)}")


# =============================================================================
# EQUATION / DEFINITION LEMMA SETS  (unchanged)
# =============================================================================
EQUATION_EQUAL_LEMMAS = {
    "equal", "represent", "signify", "symbolize", "symbolise",
    "denote", "stand",
}
EQUATION_MEAN_LEMMAS = {
    "mean", "imply", "indicate", "suggest", "translate",
}
EQUATION_REFER_LEMMAS = {
    "refer", "amount", "boil", "come",
}
ALL_EQUATION_LEMMAS = (EQUATION_EQUAL_LEMMAS
                       | EQUATION_MEAN_LEMMAS
                       | EQUATION_REFER_LEMMAS)
EQUATION_PHRASAL_PARTICLES = {
    "stand": {"for"},
    "refer": {"to"},
    "amount": {"to"},
    "boil": {"to"},
    "come": {"to"},
    "translate": {"to", "into"},
}

# =============================================================================
# RESULT-CLAUSE INTENSIFIERS  (unchanged)
# =============================================================================
RESULT_THAT_INTENSIFIERS = {"so", "such"}
RESULT_THAN_INTENSIFIERS = {"more", "less", "fewer"}
RESULT_TO_INTENSIFIERS = {"too", "enough"}
ALL_RESULT_INTENSIFIERS = (RESULT_THAT_INTENSIFIERS
                            | RESULT_THAN_INTENSIFIERS
                            | RESULT_TO_INTENSIFIERS)
RESULT_INTENSIFIER_LOOKBACK = 6

# =============================================================================
# SPATIAL PREPOSITION SETS  (unchanged)
# =============================================================================
SPATIAL_LOCATIVE_PREPS = {
    "in", "on", "at", "under", "beneath", "below", "above", "over",
    "behind", "between", "among", "amongst", "amid", "amidst",
    "around", "near", "beside", "inside", "outside", "within",
    "throughout", "against", "atop", "upon", "underneath",
}
SPATIAL_DIRECTIONAL_PREPS = {
    "into", "onto", "through", "across", "along", "past",
    "toward", "towards", "beyond", "off", "via",
}
SPATIAL_TEMPORAL_PREPS = {
    "during", "since", "until", "till", "before", "after", "while",
}
ALL_SPATIAL_PREPS = (SPATIAL_LOCATIVE_PREPS
                     | SPATIAL_DIRECTIONAL_PREPS
                     | SPATIAL_TEMPORAL_PREPS)
SPATIAL_PREP_SUBTREE_MIN_NOUNS = 2
SPATIAL_PREP_SENT_MIN_TOKENS   = 8
SPATIAL_PREP_LEAD_MIN          = 3

# =============================================================================
# PERCEPTION / COGNITION LEMMA SETS  (unchanged)
# =============================================================================
PERCEPTION_SEE_LEMMAS = {
    "see", "spot", "notice", "observe", "witness", "glimpse",
    "perceive", "detect",
}
PERCEPTION_FIND_LEMMAS = {
    "find", "discover", "uncover", "unearth", "encounter",
}
PERCEPTION_REALIZE_LEMMAS = {
    "realize", "realise", "recognize", "recognise",
    "understand", "grasp", "comprehend",
}
PERCEPTION_THINK_LEMMAS = {
    "think", "believe", "suspect", "assume", "suppose", "reckon",
    "imagine", "guess",
}
PERCEPTION_KNOW_LEMMAS = {
    "know", "mean", "signify", "imply", "indicate", "suggest",
}
PERCEPTION_REVEAL_LEMMAS = {
    "reveal", "show", "demonstrate", "expose", "disclose",
}
PERCEPTION_SAY_LEMMAS = {
    "say", "claim", "argue", "declare", "announce", "report",
    "state", "mention", "admit", "confess",
}
ALL_PERCEPTION_LEMMAS = (PERCEPTION_SEE_LEMMAS
                         | PERCEPTION_FIND_LEMMAS
                         | PERCEPTION_REALIZE_LEMMAS
                         | PERCEPTION_THINK_LEMMAS
                         | PERCEPTION_KNOW_LEMMAS
                         | PERCEPTION_REVEAL_LEMMAS
                         | PERCEPTION_SAY_LEMMAS)

# =============================================================================
# CREATION LEMMA SETS  (unchanged)
# =============================================================================
CREATION_PRODUCE_LEMMAS = {"produce", "manufacture", "generate", "fabricate", "yield"}
CREATION_BUILD_LEMMAS = {"build", "construct", "assemble", "erect", "raise"}
CREATION_CREATE_LEMMAS = {"create", "invent", "conceive", "establish", "found", "launch", "introduce"}
CREATION_CRAFT_LEMMAS = {"craft", "shape", "sculpt", "mold", "mould", "forge"}
CREATION_DESIGN_LEMMAS = {"design", "develop", "devise", "engineer", "pioneer", "architect"}
CREATION_CAUSE_LEMMAS = {"cause", "trigger", "spark", "prompt", "drive"}
CREATION_ENABLE_LEMMAS = {"enable", "allow", "permit", "let"}
ALL_CREATION_LEMMAS = (CREATION_PRODUCE_LEMMAS
                       | CREATION_BUILD_LEMMAS
                       | CREATION_CREATE_LEMMAS
                       | CREATION_CRAFT_LEMMAS
                       | CREATION_DESIGN_LEMMAS
                       | CREATION_CAUSE_LEMMAS
                       | CREATION_ENABLE_LEMMAS)

# =============================================================================
# POSSESSION LEMMA SETS  (unchanged)
# =============================================================================
POSSESSION_CORE_LEMMAS = {"have", "own", "possess"}
POSSESSION_CONTAIN_LEMMAS = {"contain", "include", "comprise", "encompass"}
POSSESSION_FEATURE_LEMMAS = {"feature", "boast", "offer", "provide", "present"}
POSSESSION_NEGATIVE_LEMMAS = {"lack", "miss", "need", "require"}
POSSESSION_HIDDEN_LEMMAS = {"harbor", "harbour", "house"}
ALL_POSSESSION_LEMMAS = (POSSESSION_CORE_LEMMAS
                         | POSSESSION_CONTAIN_LEMMAS
                         | POSSESSION_FEATURE_LEMMAS
                         | POSSESSION_NEGATIVE_LEMMAS
                         | POSSESSION_HIDDEN_LEMMAS)

# =============================================================================
# COPULA LEMMA SETS  (unchanged)
# =============================================================================
COPULA_BE_LEMMAS = {"be"}
COPULA_SENSORY_LEMMAS = {"look", "sound", "feel", "taste", "smell", "seem", "appear"}
COPULA_BECOMING_LEMMAS = {"become", "get", "grow", "turn", "go", "come", "fall", "run"}
COPULA_STAYING_LEMMAS = {"remain", "stay", "keep", "continue"}
COPULA_JUDGMENT_LEMMAS = {"prove"}
STRONG_COPULA_LEMMAS = (COPULA_SENSORY_LEMMAS
                        | COPULA_BECOMING_LEMMAS
                        | COPULA_STAYING_LEMMAS
                        | COPULA_JUDGMENT_LEMMAS)
ALL_COPULA_LEMMAS = COPULA_BE_LEMMAS | STRONG_COPULA_LEMMAS
NEGATION_TOKENS = {"n't", "not", "never"}
WEAK_VERB_LEMMAS = {
    "be", "have", "do", "get", "make", "go", "come",
    "seem", "appear", "become", "remain", "stay",
}
WEAK_VERB_FORMS = {
    "be", "am", "is", "are", "was", "were", "been", "being",
    "'s", "’s", "'re", "’re", "'m", "’m", "'ve", "’ve",
    "have", "has", "had", "having", "'d", "’d", "'ll", "’ll",
    "do", "does", "did", "done", "doing",
    "get", "gets", "got", "gotten", "getting",
    "make", "makes", "made", "making",
    "go", "goes", "went", "gone", "going",
    "come", "comes", "came", "coming",
    "seem", "seems", "seemed", "seeming",
    "appear", "appears", "appeared", "appearing",
    "become", "becomes", "became", "becoming",
    "remain", "remains", "remained", "remaining",
    "stay", "stays", "stayed", "staying",
}
WEAK_ADJ_LEMMAS = {
    "many", "much", "more", "less", "few", "fewer", "some", "any",
    "such", "other", "same", "different", "various", "several",
    "certain", "particular", "specific", "general",
    "own", "whole", "entire", "main", "only", "very", "too",
}


def _has_visualisable_content(doc: Doc, lo: int, hi: int) -> bool:
    ntok = sum(1 for t in doc[lo:hi] if not t.is_punct and not t.is_space)
    if ntok >= 4:
        return True
    for t in doc[lo:hi]:
        if t.pos_ in {"NOUN", "PROPN", "NUM"}:
            return True
        if t.pos_ == "ADJ" and t.lemma_.lower() not in WEAK_ADJ_LEMMAS:
            return True
        if t.pos_ == "VERB":
            lemma_weak = t.lemma_.lower() in WEAK_VERB_LEMMAS
            text_weak  = t.text.lower() in WEAK_VERB_FORMS
            if not (lemma_weak or text_weak):
                return True
    return False


# =============================================================================
# RULE FUNCTIONS  —  unchanged from v17 (they only manipulate split sets,
# NOT chunk lists, so the refactor doesn't touch them).
# =============================================================================
def rule_strip_markdown(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def rule_normalise_punct(text: str) -> str:
    text = text.replace("\u2026", "...")
    text = re.sub(r"(?<=[A-Za-z0-9])(\.{2,})(?=\s|$)",
                  lambda m: m.group(1), text)
    text = re.sub(r"(^|\s)(\.{2,})(?=[A-Za-z0-9])",
                  lambda m: m.group(1) + m.group(2) + " ", text)
    text = re.sub(r"(?<=[A-Za-z0-9])(\.{2,})(?=[A-Za-z0-9])",
                  lambda m: m.group(1), text)
    for dash in ("—", "–", "--"):
        text = re.sub(
            r"(?<=[A-Za-z0-9])(" + re.escape(dash) + r")(?=[A-Za-z0-9])",
            lambda m: m.group(1), text,
        )
    return text


def rule_hard_punct(doc: Doc) -> Set[int]:
    return {t.i + 1 for t in doc if t.text in HARD_PUNCT}


def rule_dashes(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text not in DASH_PUNCT:
            if (len(t.text) > 1
                    and t.text.endswith("-")
                    and t.text[-2].isalnum()
                    and t.whitespace_):
                out.add(t.i + 1)
            continue
        if (t.text == "-"
                and t.i > 0
                and not t.whitespace_
                and not doc[t.i - 1].whitespace_):
            continue
        out.add(t.i + 1)
    return out


def rule_ellipsis(doc: Doc) -> Set[int]:
    return {t.i + 1 for t in doc if _matches_ellipsis(t.text)}


def rule_pre_ellipsis_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if not _matches_ellipsis(t.text):
            continue
        head_i = None
        for j in range(t.i - 1, max(t.i - 5, -1), -1):
            if doc[j].pos_ in {"NOUN", "PROPN", "ADJ"}:
                head_i = j
                break
        if head_i is None:
            continue
        for k in range(head_i + 1, t.i):
            if doc[k].pos_ in {"ADV", "PART"} or doc[k].lower_ in {"not", "no", "n't", "never"}:
                head_i = None
                break
        if head_i is None:
            continue
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


def rule_quotes(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text in OPEN_QUOTES:
            out.add(t.i)
        if t.text in CLOSE_QUOTES:
            out.add(t.i + 1)
    return out


def rule_brackets(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text in OPEN_BRACKETS:
            out.add(t.i)
        if t.text in CLOSE_BRACKETS:
            out.add(t.i + 1)
    return out


def rule_initial_adverbial_comma(doc: Doc) -> Set[int]:
    out = set()
    for sent in doc.sents:
        if len(sent) == 0:
            continue
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
            break
    return out


def rule_comma_split(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text != ",":
            continue
        if t.i + 1 >= len(doc) or t.i == 0:
            continue
        prev = doc[t.i - 1]
        nxt  = doc[t.i + 1]
        if prev.pos_ == "VERB":
            out.add(t.i + 1)
            continue
        if prev.pos_ in {"ADV", "ADJ"} and prev.head.pos_ == "VERB" and prev.head.i < prev.i:
            out.add(t.i + 1)
            continue
        if prev.pos_ in {"NOUN", "PROPN"} and nxt.pos_ == "ADV":
            out.add(t.i + 1)
            continue
        if prev.pos_ in {"NOUN", "PROPN"} and nxt.pos_ in {"NOUN", "PROPN", "DET", "ADJ", "ADV", "NUM"}:
            window_end = min(t.i + 4, len(doc))
            has_early_verb = any(
                doc[k].pos_ == "VERB" and doc[k].tag_ not in {"VBG", "VBN"}
                for k in range(t.i + 1, window_end)
            )
            if not has_early_verb:
                out.add(t.i + 1)
    return out


def rule_appositive_comma(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text != "," or t.i + 1 >= len(doc):
            continue
        nxt = doc[t.i + 1]
        if nxt.tag_ in WH_TAGS:
            out.add(t.i + 1)
    return out


def rule_clause_starters(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        is_wh    = t.tag_ in WH_TAGS
        is_sconj = t.pos_ == "SCONJ"
        if not (is_wh or is_sconj):
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok <= 8:
            continue
        if _is_frozen_bigram_split(doc, t.i):
            continue
        if t.lower_ in {"like", "than", "as"} and t.i > 0:
            prev_tok = doc[t.i - 1]
            if prev_tok.pos_ in {"VERB", "AUX", "ADJ"}:
                continue
        if _tokens_to_next_punct(doc, t.i) <= SHORT_SUBORD_CLAUSE:
            continue
        split_right_before = (t.i in (splits | out))
        if split_right_before:
            if is_sconj:
                out.add(t.i + 1)
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev > MIN_LEAD_FOR_CLAUSE_SPLIT:
            if is_sconj:
                out.add(t.i + 1)
            else:
                out.add(t.i)
    return out


def rule_but_or_coord(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "CCONJ" or t.lower_ not in {"but", "or"}:
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev > MIN_LEAD_FOR_BUT_OR:
            out.add(t.i)
    return out


def rule_verb_clause(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "VERB":
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok <= 9:
            continue
        subj = next((c for c in t.children if c.dep_ in {"nsubj", "nsubjpass"}), None)
        if subj:
            is_compound_subj = any(x.pos_ == "CCONJ" for x in subj.subtree)
            if is_compound_subj:
                out.add(t.i)
                continue
        if t.dep_ in VERB_MOD_DEPS:
            is_long_subclause = (t.dep_ in {"relcl", "acl:relcl", "advcl"}
                                 and len(list(t.subtree)) >= 8)
            if not is_long_subclause:
                continue
        if t.i + 1 < len(doc) and doc[t.i + 1].dep_ in PARTICLE_DEPS:
            continue
        if t.i + 1 < len(doc) and doc[t.i + 1].text == ",":
            continue
        if t.i + 1 < len(doc) and doc[t.i + 1].text in HARD_PUNCT:
            continue
        if t.i + 1 < len(doc) and doc[t.i + 1].tag_ in {"WDT", "WP", "WP$"}:
            continue
        if t.i > 0 and doc[t.i - 1].lower_ == "to" and doc[t.i - 1].dep_ == "aux":
            continue
        if (t.i + 2 < len(doc)
                and doc[t.i + 1].lower_ == "to"
                and doc[t.i + 2].pos_ == "VERB"):
            continue
        dobj = next((c for c in t.children if c.dep_ in {"dobj", "obj"}), None)
        if dobj and len(list(dobj.subtree)) <= 5 and (dobj.i - t.i) < 6:
            subtree_toks = list(dobj.subtree)
            has_comparative = any(x.lower_ in COMPARATIVE_MARKERS
                                  for x in subtree_toks)
            n_adj   = sum(1 for x in subtree_toks if x.pos_ == "ADJ")
            n_nouns = sum(1 for x in subtree_toks if x.pos_ in {"NOUN", "PROPN"})
            visual_weight = n_adj + n_nouns
            is_reveal_np = (sent_ntok >= DOBJ_DISQUAL_SENT_MIN
                            and visual_weight >= 2)
            if not (has_comparative or is_reveal_np):
                continue
        comp = next((c for c in t.children
                     if c.dep_ in {"xcomp", "attr", "acomp", "ccomp"}), None)
        if comp and len(list(comp.subtree)) <= 5 and (comp.i - t.i) < 6:
            subtree_toks = list(comp.subtree)
            has_comparative = any(x.lower_ in COMPARATIVE_MARKERS
                                  for x in subtree_toks)
            n_adj   = sum(1 for x in subtree_toks if x.pos_ == "ADJ")
            n_nouns = sum(1 for x in subtree_toks if x.pos_ in {"NOUN", "PROPN"})
            visual_weight = n_adj + n_nouns
            is_reveal_np = (sent_ntok >= DOBJ_DISQUAL_SENT_MIN
                            and visual_weight >= 2)
            if not (has_comparative or is_reveal_np):
                continue
        has_subject = any(c.dep_ in {"nsubj", "nsubjpass", "csubj"} for c in t.children)
        if not has_subject:
            prep = next((c for c in t.children if c.dep_ in {"prep", "case"}), None)
            if prep and len(list(prep.subtree)) <= 5:
                continue
        if _tokens_to_next_punct(doc, t.i + 1) <= SHORT_TAIL_TO_PUNCT:
            continue
        if _is_in_runon(doc, t.i):
            prep = next((c for c in t.children if c.dep_ in {"prep", "case"}), None)
            if prep and len(list(prep.subtree)) <= 5:
                continue
        out.add(t.i + 1)
    return out


def rule_long_lead_in(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "VERB" or t.dep_ != "ROOT":
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev > LONG_LEAD_TO_ROOT:
            out.add(t.i)
    return out


def rule_long_preps(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "ADP" or t.lower_ in PROMISCUOUS_PREPS:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok <= 9:
            continue
        subtree = list(t.subtree)
        if len(subtree) < LONG_PREP_SUBTREE_MIN:
            continue
        if any(x.pos_ in {"VERB", "AUX"} for x in subtree):
            continue
        n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
        if n_nouns < 3:
            continue
        out.add(t.i + 1)
    return out


def rule_noun_lists(doc: Doc) -> Set[int]:
    out = set()
    chunks = list(doc.noun_chunks)
    BLOCKED_PREPS = {"of", "with", "for", "about", "as", "like", "than", "per", "via"}
    SPLITTABLE_PREPS = {
        "in", "at", "on", "under", "over", "above", "below", "beneath",
        "behind", "beside", "between", "among", "around", "near", "by",
        "inside", "outside", "within", "into", "onto", "through", "across",
        "along", "past", "toward", "towards", "off", "up", "down",
        "underneath", "against", "upon",
        "after", "before", "during", "since", "until", "till"
    }
    for a, b in zip(chunks[:-1], chunks[1:]):
        between = doc[a.end:b.start]
        sent_ntok = sum(1 for x in b.root.sent if not x.is_punct)
        if sent_ntok <= 9:
            continue
        if any(t.pos_ == "VERB" for t in between):
            continue
        if any(t.text in HARD_PUNCT for t in between):
            continue
        if len(b) == 1 and b[0].pos_ == "PRON":
            continue
        if b.root.dep_ == "appos":
            continue
        if b.text.lower().startswith("the ") and len(b) >= 2 and _is_ordinal(b[1]):
            continue
        if len(between) == 1 and between[0].pos_ == "ADP":
            prep = between[0].lower_
            if a.root.pos_ not in {"NOUN", "PROPN"}:
                if prep == "of":
                    continue
            else:
                is_qualifier = False
                head = b.root
                for _ in range(4):
                    if head == a.root:
                        is_qualifier = True
                        break
                    if head.head == head:
                        break
                    head = head.head
                if is_qualifier:
                    continue
                if prep in BLOCKED_PREPS or prep not in SPLITTABLE_PREPS:
                    continue
        if _in_compound_ne(doc, b.start):
            continue
        out.add(b.start)
        if len(between) == 1 and between[0].pos_ == "CCONJ":
            out.add(a.start)
    return out


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


def rule_entity_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    seen = set()
    for ent in doc.ents:
        if ent.label_ not in REVEAL_ENTS:
            continue
        ent_start = ent.start
        if ent_start == 0:
            continue
        if doc[ent_start].ent_iob_ == "I":
            continue
        if len(ent) == 1 and ent.label_ in NUMERIC_NO_REVEAL:
            continue
        if len(ent) == 1 and ent.label_ in {"DATE", "TIME", "MONEY"}:
            continue
        if len(ent) == 1 and doc[ent_start].dep_ == "compound":
            continue
        chunk = _chunk_containing(doc, ent_start)
        split_at = chunk.start if chunk is not None else ent_start
        if chunk is not None and split_at < ent_start:
            leading = doc[split_at]
            if leading.pos_ == "ADP":
                split_at = ent_start
        if split_at == 0 or split_at in seen:
            continue
        seen.add(split_at)
        if chunk is not None and len(ent) == 1 and len(chunk) > 1:
            continue
        prev_tok = doc[split_at - 1] if split_at > 0 else None
        if len(ent) == 1 and prev_tok is not None and prev_tok.pos_ == "ADP":
            continue
        sent_ntok_here = sum(1 for x in doc[split_at].sent if not x.is_punct)
        if prev_tok is not None and prev_tok.pos_ == "ADP":
            if ent.label_ in NUMERIC_QUALIFIER_ENTS and sent_ntok_here < 12:
                continue
            if sent_ntok_here <= 10:
                continue
        if prev_tok is not None and prev_tok.pos_ == "ADP" and sent_ntok_here <= 11:
            continue
        if prev_tok is not None and prev_tok.pos_ in {"NOUN", "PROPN"}:
            continue
        sent_ntok_here2 = sum(1 for x in doc[split_at].sent if not x.is_punct)
        if sent_ntok_here2 <= 10 and prev_tok is not None and prev_tok.pos_ == "ADP":
            continue
        if split_at >= 2:
            two_back = doc[split_at - 2]
            one_back = doc[split_at - 1]
            if (two_back.pos_ == "PROPN"
                    and one_back.lower_ == "the"
                    and doc[split_at].text[:1].isupper()
                    and doc[split_at].pos_ in {"NOUN", "PROPN", "ADJ"}):
                continue
        if prev_tok is not None and prev_tok.lower_ in ADV_INTRODUCERS:
            out.add(split_at)
            continue
        prev = _prev_split(splits | out, split_at)
        if split_at - prev < MIN_LEAD_FOR_ENTITY:
            continue
        out.add(split_at)
    return out


def rule_numeric_intro_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if not t.like_num:
            continue
        if t.ent_iob_ == "I":
            prev_tok = doc[t.i - 1]
            if not (prev_tok.ent_iob_ == "B" and prev_tok.pos_ in {"ADP", "ADV"}):
                continue
        if t.i - t.sent.start > 5:
            continue
        if t.i == t.sent.start:
            continue
        prev_tok = doc[t.i - 1]
        if prev_tok.pos_ not in {"ADP", "ADV"}:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 12:
            continue
        has_following_comma = False
        for k in range(t.i + 1, min(t.i + 7, len(doc))):
            if doc[k].text == ",":
                has_following_comma = True
                break
            if doc[k].text in HARD_PUNCT:
                break
        if not has_following_comma:
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev < 2:
            continue
        out.add(t.i)
    return out


def rule_currency_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.text in CURRENCY_SYMS and t.i + 1 < len(doc) and doc[t.i + 1].like_num:
            prev = _prev_split(splits | out, t.i)
            if t.i - prev > 1:
                out.add(t.i)
    return out


def rule_imperative_start(doc: Doc) -> Set[int]:
    out = set()
    for sent in doc.sents:
        if len(sent) <= 4 and sent[0].pos_ == "VERB" and sent[0].dep_ == "ROOT":
            out.add(sent.start)
    return out


def rule_and_or_clause(doc: Doc, splits: Set[int]) -> Set[int]:
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
        looks_clausal = (nxt.pos_ == "PRON"
                         or (nxt.pos_ in {"AUX", "VERB"} and nxt.dep_ != "amod"))
        if looks_clausal:
            out.add(t.i)
            continue
        sent = t.sent
        sent_ntok = sum(1 for x in sent if not x.is_punct)
        toks_after_in_sent = sum(1 for x in sent if x.i > t.i and not x.is_punct)
        if sent_ntok >= 12 and toks_after_in_sent <= 5:
            prev_chunk_ends_here = any(c.end == t.i for c in chunks)
            next_chunk_starts_here = any(c.start == t.i + 1 for c in chunks)
            if prev_chunk_ends_here and next_chunk_starts_here:
                if t.i > 0 and doc[t.i - 1].text == ",":
                    pass
                else:
                    out.add(t.i)
                    continue
        if nxt.pos_ in {"ADP", "ADV"} and sent_ntok >= 12:
            prev_chunk_start = prev
            if prev_chunk_start < len(doc) and prev_chunk_start < t.i:
                k = prev_chunk_start
                while k < t.i and doc[k].is_punct:
                    k += 1
                if k < t.i and doc[k].pos_ in {"ADP", "ADV"}:
                    out.add(t.i)
                    continue
    return out


MIN_LEAD_FOR_DESCRIPTOR = 5

def rule_terminal_descriptor(doc: Doc, splits: Set[int]) -> Set[int]:
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
        if last.pos_ in {"ADJ", "ADV"} and last.i > sent.start + 1:
            prev_tok = doc[last.i - 1]
            if prev_tok.pos_ == "ADV":
                if sent_ntok < 8:
                    continue
                start = prev_tok.i
                while start > sent.start and doc[start - 1].pos_ in {"ADV", "ADJ"}:
                    candidate = doc[start - 1]
                    if candidate.is_punct:
                        break
                    if candidate.head.i < start - 1 or candidate.head.i > last.i:
                        break
                    start -= 1
                prev_split = _prev_split(splits | out, start)
                lead_len = start - prev_split
                lead_has_copula = any(
                    doc[k].pos_ in {"VERB", "AUX"}
                    and doc[k].lemma_.lower() in ALL_COPULA_LEMMAS
                    for k in range(prev_split, start)
                )
                threshold = 2 if lead_has_copula else MIN_LEAD_FOR_DESCRIPTOR
                if lead_len >= threshold:
                    out.add(start)
                continue
        if last.pos_ == "ADJ" and last.dep_ in {"acomp", "attr"} \
                and last.head.pos_ in {"VERB", "AUX"} and last.head.i < last.i:
            head_far  = (last.i - last.head.i >= 3)
            long_verb = (sent_ntok >= 9 and last.head.pos_ == "VERB")
            if head_far or long_verb:
                prev_split = _prev_split(splits | out, last.i)
                if last.i - prev_split >= MIN_LEAD_FOR_DESCRIPTOR:
                    out.add(last.i)
            continue
        if last.pos_ in {"NOUN", "PROPN"} and last.i > sent.start:
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


def rule_adjective_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.tag_ not in {"WDT", "WP", "WRB"}:
            continue
        if t.i + 1 >= len(doc):
            continue
        nxt = doc[t.i + 1]
        if nxt.pos_ not in {"AUX", "VERB"}:
            continue
        j = t.i + 2
        while j < len(doc) and doc[j].pos_ == "ADV":
            j += 1
        if j >= len(doc) or doc[j].is_punct:
            continue
        if doc[j].pos_ not in {"ADJ", "ADV", "NOUN", "PROPN"}:
            continue
        sent_end = t.sent.end
        if sent_end - j > 5:
            continue
        if j == t.i + 2:
            continue
        prev_split = _prev_split(splits | out, j - 1)
        if (j - 1) - prev_split >= 5:
            out.add(j - 1)
    return out


def rule_numeric_phrase_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for sent in doc.sents:
        sent_ntok = sum(1 for x in sent if not x.is_punct)
        if sent_ntok < 10:
            continue
        for t in sent:
            if not t.like_num and t.ent_type_ not in {"CARDINAL", "QUANTITY", "DATE"}:
                continue
            if t.i == sent.start:
                continue
            tokens_after = sent.end - t.i - 1
            if tokens_after > 6:
                continue
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


def rule_numeric_approximator_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    DEBUG = True
    APPROX_ADV = {"nearly", "almost", "about", "roughly", "approximately",
                  "around", "over", "just", "only", "barely", "merely"}
    out = set()
    for t in doc:
        if t.lower_ not in APPROX_ADV:
            continue
        if t.pos_ not in {"ADV", "ADP"}:
            continue
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


def rule_comma_list_extension(doc: Doc) -> Set[int]:
    out = set()
    for t in doc:
        if t.text != ",":
            continue
        if t.i + 1 >= len(doc) or t.i == 0:
            continue
        prev = doc[t.i - 1]
        nxt  = doc[t.i + 1]
        if (prev.pos_ in {"NOUN", "PROPN", "PRON"}
                and nxt.pos_ == "VERB"
                and nxt.tag_ in {"VBG", "VBN"}):
            out.add(t.i + 1)
            continue
        if (prev.pos_ in {"NOUN", "PROPN", "ADJ", "ADV", "VERB"}
                and nxt.pos_ == "CCONJ"):
            out.add(t.i + 1)
            continue
        if prev.pos_ == "ADJ" and nxt.pos_ == "ADJ":
            is_chain = False
            k = t.i + 2
            if k < len(doc):
                if doc[k].text == "," and k + 1 < len(doc) and doc[k + 1].pos_ == "ADJ":
                    is_chain = True
                elif doc[k].lower_ == "and" and k + 1 < len(doc) and doc[k + 1].pos_ == "ADJ":
                    is_chain = True
            if is_chain:
                out.add(t.i + 1)
            continue
    return out


def rule_long_subord_comma(doc: Doc) -> Set[int]:
    out = set()
    for sent in doc.sents:
        if len(sent) < 8:
            continue
        first_idx = 0
        while first_idx < len(sent) and sent[first_idx].pos_ in {"CCONJ", "INTJ"}:
            first_idx += 1
        if first_idx >= len(sent):
            continue
        first_tok = sent[first_idx]
        if first_tok.pos_ != "SCONJ" and first_tok.tag_ not in WH_TAGS:
            continue
        for t in sent:
            if t.text != ",":
                continue
            tokens_before = sum(1 for x in sent
                                if x.i < t.i and not x.is_punct)
            if tokens_before < LONG_SUBORD_OPENER_TOKENS:
                continue
            has_verb = any(x.pos_ in {"VERB", "AUX"}
                           for x in sent if x.i < t.i)
            if not has_verb:
                continue
            if t.i + 1 < len(doc):
                out.add(t.i + 1)
            break
    return out


def rule_terminal_adj_coord(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for sent in doc.sents:
        sent_ntok = sum(1 for x in sent if not x.is_punct)
        if sent_ntok < 7:
            continue
        last = None
        for t in reversed(list(sent)):
            if t.is_punct or t.is_space:
                continue
            last = t
            break
        if last is None:
            continue
        is_descriptor_like = (last.pos_ in {"ADJ", "NOUN"}
                               or last.tag_ in {"VBG", "VBN"})
        if not is_descriptor_like:
            continue
        if last.i - 1 < sent.start:
            continue
        cconj = doc[last.i - 1]
        if cconj.pos_ != "CCONJ":
            continue
        if cconj.i - 1 < sent.start:
            continue
        first_adj = doc[cconj.i - 1]
        first_adj_is_descriptor = (first_adj.pos_ in {"ADJ", "NOUN"}
                                    or first_adj.tag_ in {"VBG", "VBN"})
        if not first_adj_is_descriptor:
            continue
        if first_adj.i - 1 < sent.start:
            continue
        adv = doc[first_adj.i - 1]
        if adv.pos_ != "ADV":
            continue
        adv_start = adv.i
        while adv_start > sent.start and doc[adv_start - 1].pos_ == "ADV":
            adv_start -= 1
        if adv_start - 1 < sent.start:
            continue
        verb = doc[adv_start - 1]
        if verb.pos_ not in {"VERB", "AUX"}:
            continue
        prev1 = _prev_split(splits | out, first_adj.i)
        if first_adj.i - prev1 >= 2:
            out.add(first_adj.i)
        out.add(cconj.i + 1)
    return out


def rule_pp_intro_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "ADP" or t.lower_ in PROMISCUOUS_PREPS:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 10:
            continue
        subtree = list(t.subtree)
        n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
        has_verb = any(x.pos_ in {"VERB", "AUX"} for x in subtree)
        if n_nouns < 3 or has_verb:
            continue
        prev = _prev_split(splits | out, t.i + 1)
        lead_tokens = doc[prev:t.i + 1]
        n_lead = sum(1 for x in lead_tokens if not x.is_punct)
        if n_lead < 3:
            continue
        if any(x.text in HARD_PUNCT for x in lead_tokens):
            continue
        if not any(x.pos_ in {"VERB", "AUX"} for x in lead_tokens):
            continue
        out.add(t.i + 1)
    return out


def rule_participle_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    chunks = list(doc.noun_chunks)
    for t in doc:
        if t.tag_ not in {"VBG", "VBN"}:
            continue
        if t.pos_ not in {"VERB", "AUX"}:
            continue
        if t.dep_ in {"amod", "compound"}:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 9:
            continue
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
        if any(x.text in HARD_PUNCT for x in doc[t.i:nxt_chunk.start]):
            continue
        if _in_compound_ne(doc, nxt_chunk.start):
            continue
        prev = _prev_split(splits | out, nxt_chunk.start)
        if nxt_chunk.start - prev < 2:
            continue
        out.add(nxt_chunk.start)
    return out


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
        if t.i > 0 and t.i + 1 < len(doc):
            prev_tok = doc[t.i - 1]
            next_tok = doc[t.i + 1]
            if prev_tok.pos_ == "ADJ" and next_tok.pos_ == "ADJ":
                has_shared_noun = False
                for k in range(next_tok.i + 1, min(next_tok.i + 5, len(doc))):
                    if doc[k].pos_ in {"NOUN", "PROPN"}:
                        has_shared_noun = True
                        break
                    if doc[k].text in HARD_PUNCT:
                        break
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
        sent_end = t.sent.end
        tail_end = t.i
        while tail_end < sent_end and doc[tail_end].text not in HARD_PUNCT:
            tail_end += 1
        tail_content = _content_count(doc, t.i, tail_end)
        if tail_content < INFINITIVE_SPLIT_TAIL_MIN:
            continue
        if t.i > 0:
            prev_tok = doc[t.i - 1]
            if (prev_tok.lower_, t.lower_) in FROZEN_BIGRAMS:
                continue
            if t.i + 1 < len(doc):
                pass
        out.add(t.i + 1)
    return out


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


def rule_progressive_split(doc: Doc, splits: Set[int]) -> Set[int]:
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


def rule_copula_attr_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
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
        n_adj = sum(1 for x in chunk if x.pos_ == "ADJ")
        n_nouns = sum(1 for x in chunk if x.pos_ in {"NOUN", "PROPN"})
        n_visual_verbs = sum(1 for x in chunk if x.tag_ in {"VBG", "VBN"})
        is_comparative = any(x.lower_ == "than" for x in chunk)
        visual_weight = n_adj + n_nouns + n_visual_verbs
        is_heavy = (len(chunk) >= 4) or visual_weight >= 2 or is_comparative
        if not is_heavy:
            continue
        head_verb = None
        head = chunk.root
        for _ in range(3):
            if head.pos_ in {"VERB", "AUX"}:
                head_verb = head
                break
            if head.head == head:
                break
            head = head.head
        if head_verb is None:
            continue
        is_copula = (head_verb.lemma_.lower() in COPULAR_LEMMAS or 
                     head_verb.text.lower() in COPULAR_FORMS)
        if not is_copula:
            continue
        if chunk.start <= head_verb.i:
            continue
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


def rule_pron_participle_pp_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
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
        if part.tag_ not in {"VBN", "VBG", "VBD"}:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < PP_PRON_PART_SENT_MIN:
            continue
        prev = _prev_split(splits | out, t.i + 1)
        lead = _content_count(doc, prev, t.i + 1)
        if lead < PP_PRON_PART_LEAD_MIN:
            continue
        out.add(t.i + 1)
    return out


def rule_terminal_pp_after_copula(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for sent in doc.sents:
        sent_ntok = sum(1 for x in sent if not x.is_punct)
        if sent_ntok < 9:
            continue
        last = None
        for t in reversed(list(sent)):
            if t.is_punct or t.is_space:
                continue
            last = t
            break
        if last is None or last.pos_ not in {"NOUN", "PROPN"}:
            continue
        head = last.head
        if head is last or head.pos_ != "ADP":
            continue
        adp = head
        if adp.i - 1 < sent.start:
            continue
        before_adp = doc[adp.i - 1]
        if before_adp.pos_ not in {"ADJ", "VERB"}:
            continue
        prev = _prev_split(splits | out, adp.i)
        lead_content = _content_count(doc, prev, adp.i)
        if lead_content < MIN_LEAD_FOR_DESCRIPTOR:
            continue
        out.add(adp.i)
    return out


def rule_phrasal_object_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    chunks = list(doc.noun_chunks)
    for t in doc:
        if t.pos_ != "VERB":
            continue
        prt = next((c for c in t.children if c.dep_ in PARTICLE_DEPS), None)
        if prt is None:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 7:
            continue
        nxt_chunk: Optional[Span] = None
        for nc in chunks:
            if nc.start > prt.i:
                nxt_chunk = nc
                break
        if nxt_chunk is None:
            continue
        if len(nxt_chunk) < 2:
            continue
        if nxt_chunk.start - prt.i > 2:
            continue
        if _in_compound_ne(doc, nxt_chunk.start):
            continue
        if any(doc[k].text in HARD_PUNCT for k in range(prt.i, nxt_chunk.start)):
            continue
        prev = _prev_split(splits | out, nxt_chunk.start)
        if nxt_chunk.start - prev < 2:
            continue
        out.add(nxt_chunk.start)
    return out


def rule_prep_object_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    DEBUG = True
    out = set()
    for t in doc:
        if t.pos_ != "ADP":
            continue
        j = t.i + 1
        while j < len(doc) and doc[j].pos_ == "DET":
            j += 1
        if j < len(doc) and doc[j].pos_ == "ADJ":
            is_intensified = (j > 0 and doc[j - 1].pos_ == "ADV"
                              and doc[j - 1].lower_ in
                              {"too", "so", "very", "really", "extremely"})
            if not is_intensified:
                if DEBUG:
                    print(f"  [prep-obj] SKIP at idx {t.i}: prep '{t.text}' "
                          f"followed by ADJ '{doc[j].text}'")
                continue
        head = t.head
        if head.dep_ in {"dobj", "obj"}:
            verb_head = head.head
            if (verb_head.pos_ in {"VERB", "AUX"}
                    and verb_head.lemma_.lower() in ALL_POSSESSION_LEMMAS
                    and verb_head.dep_ not in {"aux", "auxpass"}):
                continue
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
        pobj = next((c for c in t.children if c.dep_ in {"pobj", "obj"}), None)
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
        chunk = _chunk_containing(doc, pobj.i)
        if chunk is None:
            continue
        if len(chunk) < 2 and not is_terminal_pp:
            continue
        pobj_subtree = list(pobj.subtree)
        has_numeric = any(
            x.like_num
            or x.ent_type_ in {"DATE", "TIME", "CARDINAL",
                               "QUANTITY", "PERCENT", "MONEY"}
            for x in pobj_subtree
        )
        if pobj is not None and pobj.pos_ == "PRON":
            pobj_subtree = list(pobj.subtree)
            if len(pobj_subtree) == 1:
                if DEBUG:
                    print(f"  [prep-obj] SKIP at idx {t.i}: pobj is bare PRON '{pobj.text}'")
                continue
        if is_terminal_pp and has_numeric:
            if sent_ntok < 11:
                continue
        else:
            if sent_ntok < 12:
                continue
        if t.lower_ in PROMISCUOUS_PREPS:
            continue
        pobj_ent_type = doc[pobj.i].ent_type_
        if pobj_ent_type in {"GPE", "LOC", "FAC", "ORG", "PERSON"}:
            out.add(t.i + 1)
        elif is_terminal_pp:
            split_idx = chunk.start if chunk else pobj.i
            out.add(split_idx)
        else:
            if t.head.pos_ in {"NOUN", "PROPN"}:
                out.add(t.i + 1)
            else:
                out.add(t.i)
    return out


def rule_transition_adverb(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "ADV" or t.lower_ not in TRANSITION_ADVERBS:
            continue
        if t.i == 0:
            continue
        if t.dep_ == "amod":
            continue
        if t.i > 0 and doc[t.i - 1].pos_ == "AUX":
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok <= 8:
            continue
        prev = _prev_split(splits | out, t.i)
        if t.i - prev < MIN_LEAD_FOR_CLAUSE_SPLIT:
            continue
        out.add(t.i)
    return out


def rule_sconj_hang(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "SCONJ":
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        is_comparative_than = (t.lower_ == "than" and t.head.pos_ in {"ADJ", "ADV"})
        if sent_ntok <= 8 and not is_comparative_than:
            continue
        if t.i + 1 < len(doc) and doc[t.i + 1].pos_ == "ADP":
            continue
        out.add(t.i + 1)
    return out


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
        if t.i == t.sent.start:
            continue
        if t.i + 1 >= len(doc):
            continue
        nxt = doc[t.i + 1]
        if nxt.is_punct:
            continue
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
            out.add(post_complement_split + 1)
            continue
        is_be = (lemma == "be")
        if not is_be and not _is_copular_use(t):
            continue
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


def rule_possession_reveal_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        lemma = t.lemma_.lower()
        if lemma not in ALL_POSSESSION_LEMMAS:
            continue
        if t.pos_ not in {"VERB", "AUX"}:
            continue
        if t.dep_ in {"aux", "auxpass"}:
            continue
        if t.i + 1 < len(doc) and doc[t.i + 1].lower_ == "to":
            continue
        if not _is_substantial_dobj(t):
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 7:
            continue
        if t.i == t.sent.start:
            continue
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


def rule_creation_reveal_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        lemma = t.lemma_.lower()
        if lemma not in ALL_CREATION_LEMMAS:
            continue
        if t.pos_ != "VERB":
            continue
        if t.dep_ in {"aux", "auxpass"}:
            continue
        has_auxpass = any(c.dep_ == "auxpass" for c in t.children)
        if has_auxpass:
            continue
        if not _is_substantial_dobj(t):
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 8:
            continue
        if t.i == t.sent.start:
            continue
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
        has_auxpass = any(c.dep_ == "auxpass" for c in t.children)
        if has_auxpass:
            continue
        has_comp, comp_head = _has_substantial_complement(t)
        if not has_comp:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 8:
            continue
        if t.i == t.sent.start:
            continue
        neg_offset = 0
        k = t.i + 1
        while k < len(doc) and (doc[k].dep_ == "neg"
                                or doc[k].lower_ in NEGATION_TOKENS):
            neg_offset += 1
            k += 1
        default_pos = t.i + 1 + neg_offset
        split_pos = default_pos
        if comp_head is not None and comp_head.dep_ == "ccomp":
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


def rule_spatial_prep_reveal_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        if t.pos_ != "ADP":
            continue
        if t.lower_ not in ALL_SPATIAL_PREPS:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < SPATIAL_PREP_SENT_MIN_TOKENS:
            continue
        subtree = list(t.subtree)
        n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
        if n_nouns < SPATIAL_PREP_SUBTREE_MIN_NOUNS:
            continue
        if any(x.pos_ in {"VERB", "AUX"} for x in subtree):
            continue
        prev = _prev_split(splits | out, t.i + 1)
        lead = doc[prev:t.i + 1]
        lead_content = sum(1 for x in lead
                           if x.pos_ in {"NOUN", "PROPN", "VERB",
                                         "ADJ", "ADV", "NUM"})
        if lead_content < SPATIAL_PREP_LEAD_MIN:
            continue
        if not any(x.pos_ in {"VERB", "AUX"} for x in lead):
            continue
        if any(x.text in HARD_PUNCT for x in lead):
            continue
        out.add(t.i + 1)
    return out


def rule_result_clause_reveal_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for t in doc:
        lt = t.lower_
        if lt not in {"that", "than", "to"}:
            continue
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 9:
            continue
        intensifier_found: Optional[str] = None
        lookback_start = max(t.sent.start, t.i - RESULT_INTENSIFIER_LOOKBACK)
        for j in range(t.i - 1, lookback_start - 1, -1):
            jt = doc[j]
            if jt.text in HARD_PUNCT:
                break
            if jt.lower_ in ALL_RESULT_INTENSIFIERS:
                intensifier_found = jt.lower_
                break
            if lt == "than" and jt.tag_ in {"JJR", "RBR"}:
                intensifier_found = "<comparative>"
                break
        if intensifier_found is None:
            continue
        if lt == "that":
            if t.pos_ != "SCONJ":
                continue
            if intensifier_found not in RESULT_THAT_INTENSIFIERS:
                continue
        elif lt == "than":
            if t.pos_ not in {"ADP", "SCONJ"}:
                continue
            if (intensifier_found != "<comparative>"
                    and intensifier_found not in RESULT_THAN_INTENSIFIERS):
                continue
        elif lt == "to":
            if t.dep_ != "aux":
                continue
            if t.i + 1 >= len(doc) or doc[t.i + 1].pos_ != "VERB":
                continue
            if intensifier_found not in RESULT_TO_INTENSIFIERS:
                continue
        out.add(t.i + 1)
    return out


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
        if not _is_equation_use(t):
            continue
        has_comp, comp_head = _has_substantial_complement(t)
        if not has_comp:
            if lemma in EQUATION_PHRASAL_PARTICLES:
                required = EQUATION_PHRASAL_PARTICLES[lemma]
                for child in t.children:
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
        sent_ntok = sum(1 for x in t.sent if not x.is_punct)
        if sent_ntok < 6:
            continue
        if t.i == t.sent.start:
            continue
        split_pos = t.i + 1
        if lemma in EQUATION_PHRASAL_PARTICLES:
            required = EQUATION_PHRASAL_PARTICLES[lemma]
            k = t.i + 1
            while k < len(doc) and (doc[k].pos_ == "ADV"
                                     or (doc[k].pos_ in {"ADP", "PART"}
                                         and doc[k].lower_ in required)):
                k += 1
            split_pos = k
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


def rule_and_visualisables_split(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    DEBUG = False
    for t in doc:
        if t.pos_ != "CCONJ" or t.lower_ not in {"and", "or"}:
            continue
        if DEBUG:
            print(f"  [and-vis] examining '{t.text}' at idx {t.i}")
        if t.i == 0 or t.i + 1 >= len(doc):
            if DEBUG: print(f"    SKIP: at sentence boundary")
            continue
        left = doc[t.i - 1]
        left_visualisable = _has_visualisable_content(doc, t.i - 1, t.i)
        if DEBUG:
            print(f"    left='{left.text}' (pos={left.pos_}) visualisable={left_visualisable}")
        if not left_visualisable:
            continue
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
        prev = _prev_split(splits | out, t.i)
        lead_content = _content_count(doc, prev, t.i)
        if DEBUG:
            print(f"    lead=[{prev}:{t.i}] content_count={lead_content}")
        if lead_content < 1:
            if DEBUG: print(f"    SKIP: insufficient lead content")
            continue
        if any(doc[k].text in HARD_PUNCT for k in range(prev, t.i)):
            if DEBUG: print(f"    SKIP: hard punct in lead")
            continue
        if DEBUG:
            print(f"    -> ADD split at {t.i + 1} (after '{t.text}')")
        out.add(t.i + 1)
    return out


def rule_title_appositive_verb_split(doc: Doc, splits: Set[int]) -> Set[int]:
    DEBUG = False
    out = set()
    for i in range(len(doc) - 2):
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
        j = i + 3
        while j < len(doc) and doc[j].text[:1].isupper() \
                and doc[j].pos_ in {"NOUN", "PROPN", "ADJ"}:
            j += 1
        if j >= len(doc):
            continue
        if doc[j].pos_ != "VERB":
            if DEBUG:
                print(f"  [title-verb] SKIP at idx {j}: not VERB "
                      f"(found {doc[j].pos_} '{doc[j].text}')")
            continue
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


def rule_first_list_item_split(doc: Doc, splits: Set[int]) -> Set[int]:
    DEBUG = True
    out = set()
    for t in doc:
        if t.pos_ not in {"NOUN", "PROPN", "ADJ", "DET"}:
            continue
        chunk = _chunk_containing(doc, t.i)
        if chunk is None:
            continue
        head = chunk.root
        if head.i != t.i and t.i != chunk.start:
            continue
        n_conj_np = sum(1 for c in head.children
                        if c.dep_ == "conj" and c.pos_ in {"NOUN", "PROPN"})
        if n_conj_np < 1:
            continue
        chain_has_comma = False
        for c in head.children:
            if c.dep_ == "conj":
                for k in range(head.i + 1, c.i):
                    if doc[k].text == ",":
                        chain_has_comma = True
                        break
        if not chain_has_comma:
            continue
        split_at = chunk.start
        if split_at == 0:
            continue
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
    for t in doc:
        if t.lower_ != "not":
            continue
        if t.i == 0:
            continue
        prev_tok = doc[t.i - 1]
        if prev_tok.text not in (DASH_PUNCT | {","}):
            continue
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


def rule_terminal_specifier_reveal(doc: Doc, splits: Set[int]) -> Set[int]:
    out = set()
    for sent in doc.sents:
        sent_ntok = sum(1 for x in sent if not x.is_punct)
        if sent_ntok < 4:
            continue
        last = None
        for t in reversed(list(sent)):
            if t.is_punct or t.is_space:
                continue
            last = t
            break
        if last is None or last.i <= sent.start:
            continue
        if last.pos_ not in {"NOUN", "PROPN", "ADJ", "NUM"}:
            continue
        prev = doc[last.i - 1]
        if prev.pos_ not in {"NOUN", "PROPN"}:
            continue
        if prev.dep_ not in {"dobj", "obj"}:
            continue
        head_verb = prev.head
        if head_verb.pos_ != "VERB":
            continue
        if head_verb.i >= prev.i:
            continue
        if last.head != prev:
            continue
        prev_split = _prev_split(splits | out, last.i)
        if last.i - prev_split < 3:
            continue
        out.add(last.i)
    return out


# =============================================================================
# ANTI-RULES  —  unchanged (they manipulate split sets, not chunk lists).
# =============================================================================
def anti_rule_compound_ne(doc: Doc, splits: Set[int]) -> Set[int]:
    APPROX_LEMMAS = {"nearly", "almost", "about", "roughly", "approximately",
                     "around", "over", "just", "only", "barely", "merely"}
    bad = set()
    for i in splits:
        if not _in_compound_ne(doc, i):
            continue
        if i > 0 and doc[i - 1].lower_ in APPROX_LEMMAS \
                and doc[i - 1].pos_ in {"ADV", "ADP"}:
            continue
        bad.add(i)
    return bad


def anti_rule_aux_main_verb(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.dep_ in AUX_LIKE_DEPS and right.pos_ in {"VERB", "AUX"}:
            if left.lower_ == "to" and left.dep_ == "aux":
                continue
            bad.add(i)
        if left.pos_ == "AUX" and right.pos_ in {"VERB", "AUX"}:
            if (left.lemma_.lower() in ALL_COPULA_LEMMAS
                    and left.dep_ not in {"aux", "auxpass"}
                    and right.tag_ == "VBG"):
                continue
            bad.add(i)
    return bad


def anti_rule_hyphen_compound(doc: Doc, splits: Set[int]) -> Set[int]:
    return {i for i in splits if _in_hyphen_compound(doc, i)}


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
        if left.whitespace_ != "":
            continue
        if _has_apos(left.text) or _has_apos(right.text):
            bad.add(i)
    return bad


def anti_rule_phrasal_particle(doc: Doc, splits: Set[int]) -> Set[int]:
    return {i for i in splits if 0 < i < len(doc) and doc[i].dep_ in PARTICLE_DEPS}


def anti_rule_numeric_unit(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.ent_type_ in NUMERIC_ENTS and right.ent_iob_ == "I":
            bad.add(i)
        if left.like_num and right.pos_ == "NOUN" and right.dep_ in {"compound", "nummod", "nmod"}:
            bad.add(i)
    return bad


def anti_rule_det_head(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.pos_ == "DET" and right.pos_ in {"NOUN", "PROPN", "ADJ", "NUM"}:
            bad.add(i)
        if (left.pos_ == "NUM" and left.dep_ in {"nummod", "det"}
                and right.pos_ in {"NOUN", "PROPN", "ADJ"}):
            bad.add(i)
    return bad


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


def anti_rule_frozen_bigram(doc: Doc, splits: Set[int]) -> Set[int]:
    return {i for i in splits if _is_frozen_bigram_split(doc, i)}


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


def anti_rule_to_infinitive(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.lower_ == "to" and left.dep_ == "aux" and right.pos_ == "VERB":
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


def anti_rule_numeric_range(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.text == ":" and left.i > 0 and doc[left.i - 1].like_num and right.like_num:
            bad.add(i)
    return bad


def anti_rule_no_split_before_comma(doc: Doc, splits: Set[int]) -> Set[int]:
    return {i for i in splits if 0 < i < len(doc) and doc[i].text == ","}


def anti_rule_currency_glued(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if 0 < i < len(doc) and doc[i - 1].text in CURRENCY_SYMS and doc[i].like_num:
            bad.add(i)
    return bad


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
        if left.lower_ in MEASURE_NOUNS and right.lower_ in MEASURE_NOUNS:
            bad.add(i)
    return bad


def anti_rule_neg_modifier(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        if doc[i - 1].lower_ in {"not", "no", "never", "n't"}:
            if _is_big_punct_split(doc, i):
                continue
            bad.add(i)
    return bad


def anti_rule_compound_noun(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.pos_ in {"NOUN", "PROPN"} and right.pos_ in {"NOUN", "PROPN"}:
            bad.add(i)
    return bad


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
                for i in range(open_idx + 1, tok.i + 1):
                    bad.add(i)
                open_idx = None
    return bad


def anti_rule_short_sentence(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for sent in doc.sents:
        ntok = sum(1 for t in sent if not t.is_punct)
        if ntok > SHORT_SENT_NO_SPLIT:
            continue
        last_noun_i: Optional[int] = None
        for t in reversed(list(sent)):
            if t.is_punct or t.is_space:
                continue
            if t.pos_ in {"NOUN", "PROPN"}:
                last_noun_i = t.i
                break
            else:
                break
        copula_pp_reveal_ok = False
        if last_noun_i is not None:
            j = last_noun_i - 1
            while j > sent.start and doc[j].pos_ in {"DET", "ADJ"}:
                j -= 1
            if j > sent.start and doc[j].pos_ == "ADP":
                k = j - 1
                while k > sent.start and doc[k].pos_ == "ADV":
                    k -= 1
                if k >= sent.start and doc[k].pos_ in {"AUX", "VERB"}:
                    lead_content = sum(1 for x in sent
                                       if x.i < last_noun_i and not x.is_punct)
                    if lead_content >= AUX_PP_REVEAL_LEAD_MIN:
                        copula_pp_reveal_ok = True
        for i in splits:
            if not (sent.start < i < sent.end):
                continue
            if _is_big_punct_split(doc, i):
                continue
            if copula_pp_reveal_ok and last_noun_i is not None:
                if last_noun_i - 1 <= i <= last_noun_i:
                    continue
            bad.add(i)
    return bad


def anti_rule_verb_to_verb(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.pos_ in {"VERB", "AUX"} and right.pos_ in {"VERB", "AUX"}:
            if right.dep_ == "amod":
                continue
            if right.i + 1 < len(doc) and doc[right.i + 1].pos_ in {"NOUN", "PROPN"}:
                continue
            if (left.lemma_.lower() in ALL_COPULA_LEMMAS
                    and left.dep_ not in {"aux", "auxpass"}
                    and right.tag_ == "VBG"):
                continue
            bad.add(i)
    return bad


def anti_rule_verb_to_dem_pron(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        left, right = doc[i - 1], doc[i]
        if left.pos_ in {"VERB", "AUX"} and right.pos_ in {"PRON", "DET"} \
                and right.head == left:
            ALL_REVEAL_VERB_LEMMAS = (ALL_POSSESSION_LEMMAS
                                       | ALL_CREATION_LEMMAS
                                       | ALL_PERCEPTION_LEMMAS
                                       | ALL_EQUATION_LEMMAS)
            if (left.lemma_.lower() in ALL_REVEAL_VERB_LEMMAS
                    and left.dep_ not in {"aux", "auxpass"}
                    and _is_substantial_dobj(left)):
                continue
            bad.add(i)
        elif left.pos_ in {"VERB", "AUX"} and right.tag_ in {"DT", "WDT"}:
            if right.head == left:
                bad.add(i)
    return bad


def anti_rule_orphan_measure_tail(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i <= 0 or i >= len(doc):
            continue
        j = i
        while j < len(doc) and doc[j].text not in HARD_PUNCT:
            j += 1
        tail = doc[i:j]
        content = [x for x in tail if not x.is_punct and not x.is_space]
        if not content or len(content) > 2:
            continue
        if content[-1].lower_ not in MEASURE_NOUNS:
            continue
        if any(x.pos_ == "ADP" for x in content[:-1]):
            bad.add(i)
            continue
        if i > 0 and doc[i - 1].pos_ == "ADP":
            bad.add(i)
            continue
    return bad


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
        if _is_big_punct_split(doc, lo) or _is_big_punct_split(doc, hi):
            continue
        last_nonspace = None
        for t in reversed(list(span)):
            if not t.is_space:
                last_nonspace = t
                break
        if last_nonspace is not None and last_nonspace.text in HARD_PUNCT:
            continue
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


def anti_rule_split_before_sconj(doc: Doc, splits: Set[int]) -> Set[int]:
    bad = set()
    for i in splits:
        if i >= len(doc):
            continue
        tok = doc[i]
        if tok.pos_ != "SCONJ":
            continue
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
    span = doc[lo:hi]
    text = span.text.strip()
    if not text:
        return True
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
    span = doc[lo:hi]
    if len(span) == 0:
        return "bwd"
    if len(span) == 1 and span[0].pos_ == "AUX":
        return "fwd"
    if len(span) == 1 and span[0].pos_ == "SCONJ":
        return "bwd"
    if len(span) == 1 and span[0].pos_ == "ADP":
        return "bwd"
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


# === REFACTORED: _fuse_orphans now operates on ChunkMap ====================
def _fuse_orphans(doc: Doc,
                  chunks: ChunkMap,
                  chunk_spans: List[Tuple[int, int]],
                  protected: Optional[Set[int]] = None
                  ) -> Tuple[ChunkMap, List[Tuple[int, int]]]:
    """Post-merge pass that fixes orphan single-content-word lines.

    Now operates on a ChunkMap (list of {text: int_list} dicts).  When two
    chunks fuse, their int lists are concatenated via _merge_chunks().
    """
    if protected is None:
        protected = set()
    if len(chunks) <= 1:
        return chunks, chunk_spans

    REL_TAGS  = {"WDT", "WP", "WP$", "WRB"}
    PUNCT_ONLY_RE = re.compile(r"^[^\w\s]+$")

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

    out: ChunkMap = []
    out_spans: List[Tuple[int, int]] = []
    i = 0
    while i < len(chunks):
        cur_chunk = chunks[i]
        cur_text = _chunk_text(cur_chunk)
        cur_lo, cur_hi = chunk_spans[i]
        cur_content = _content_tokens(cur_lo, cur_hi)

        # pattern (5): pure-punctuation orphan glues backward
        if _all_punct(cur_lo, cur_hi) and cur_lo not in protected:
            if out:
                # No space when gluing pure punctuation.
                merged = _merge_chunks(out[-1], cur_chunk, sep="")
                out[-1] = merged
                out_spans[-1] = (out_spans[-1][0], cur_hi)
                i += 1
                continue
            i += 1
            continue

        # pattern (1): single-noun then relative pronoun
        if (i + 1 < len(chunks)
                and len(cur_content) == 1
                and cur_content[0].pos_ in {"NOUN", "PROPN"}
                and not cur_text.rstrip().endswith((".", "!", "?", ":", ";"))
                and cur_hi not in protected):
            nxt_lo, nxt_hi = chunk_spans[i + 1]
            first = _first_content_tok(nxt_lo, nxt_hi)
            if first is not None and first.tag_ in REL_TAGS:
                fused = _merge_chunks(cur_chunk, chunks[i + 1])
                out.append(fused)
                out_spans.append((cur_lo, nxt_hi))
                i += 2
                continue

        # pattern (2): orphaned-after-prep
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
                merged = _merge_chunks(out[-1], cur_chunk)
                out[-1] = merged
                out_spans[-1] = (prev_lo, cur_hi)
                i += 1
                continue

        # pattern (3): single-VERB chunk then its head's NP
        if (i + 1 < len(chunks)
                and len(cur_content) == 1
                and cur_content[0].pos_ == "VERB"
                and not cur_text.rstrip().endswith((".", "!", "?", ":", ";"))):
            nxt_lo, nxt_hi = chunk_spans[i + 1]
            verb = cur_content[0]
            if any(nxt_lo <= c.i < nxt_hi for c in verb.children) \
                    and cur_hi not in protected:
                fused = _merge_chunks(cur_chunk, chunks[i + 1])
                out.append(fused)
                out_spans.append((cur_lo, nxt_hi))
                i += 2
                continue

        # pattern (4): bare DET-only chunk glues forward
        if (i + 1 < len(chunks)
                and len(cur_content) == 1
                and cur_content[0].pos_ == "DET"
                and cur_hi not in protected):
            nxt_lo, nxt_hi = chunk_spans[i + 1]
            fused = _merge_chunks(cur_chunk, chunks[i + 1])
            out.append(fused)
            out_spans.append((cur_lo, nxt_hi))
            i += 2
            continue

        out.append(cur_chunk)
        out_spans.append((cur_lo, cur_hi))
        i += 1
    return out, out_spans


# === REFACTORED: _post_merge_unvisualisable now operates on ChunkMap =======
def _post_merge_unvisualisable(doc: Doc,
                               chunks: ChunkMap,
                               chunk_spans: List[Tuple[int, int]],
                               protected: Set[int]) -> Tuple[ChunkMap, List[Tuple[int, int]]]:
    """Final pass: re-glue any chunk with no visualisable content.

    Operates on a ChunkMap.  Merges via _merge_chunks() so int lists are
    concatenated when two chunks combine.
    """
    if len(chunks) <= 1:
        return chunks, chunk_spans

    DEBUG_PMV = True
    if DEBUG_PMV:
        print(f"  [post-merge-unvis] INPUT chunks:")
        for i, (c, (lo, hi)) in enumerate(zip(chunks, chunk_spans)):
            vis = _has_visualisable_content(doc, lo, hi)
            print(f"    [{i}] '{_chunk_text(c)}' ints={_chunk_ints(c)} "
                  f"span={lo}:{hi} visualisable={vis} "
                  f"lo_protected={lo in protected} hi_protected={hi in protected}")

    for _iter in range(10):
        target_idx: Optional[int] = None

        for i, (lo, hi) in enumerate(chunk_spans):
            text = _chunk_text(chunks[i]).strip()
            if not text:
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

        # STEP A: DET-steal (substantive orphans only)
        if is_substantive and i + 1 < len(chunk_spans):
            nxt_lo, nxt_hi = chunk_spans[i + 1]
            j = nxt_lo
            while j < nxt_hi and doc[j].pos_ == "DET":
                j += 1
            if j > nxt_lo and _has_visualisable_content(doc, j, nxt_hi):
                new_chunks = list(chunks)
                new_spans  = list(chunk_spans)
                new_chunks[i]     = _chunk(doc[lo:j].text.strip(),
                                           _chunk_ints(chunks[i]))
                new_spans[i]      = (lo, j)
                new_chunks[i + 1] = _chunk(doc[j:nxt_hi].text.strip(),
                                           _chunk_ints(chunks[i + 1]))
                new_spans[i + 1]  = (j, nxt_hi)
                chunks      = new_chunks
                chunk_spans = new_spans
                hi = j

        # STEP B: choose merge direction
        can_back = i > 0
        can_fwd  = i + 1 < len(chunk_spans)

        if can_back and lo in protected and not is_substantive:
            can_back = False
        if can_fwd and hi in protected:
            can_fwd = False

        if can_back:
            prev_lo, prev_hi = chunk_spans[i - 1]
            new_chunks = list(chunks)
            new_spans  = list(chunk_spans)
            new_chunks[i - 1] = _merge_chunks(chunks[i - 1], chunks[i])
            new_spans[i - 1]  = (prev_lo, hi)
            del new_chunks[i]
            del new_spans[i]
            chunks = new_chunks
            chunk_spans = new_spans
        elif can_fwd:
            nxt_lo, nxt_hi = chunk_spans[i + 1]
            new_chunks = list(chunks)
            new_spans  = list(chunk_spans)
            new_chunks[i] = _merge_chunks(chunks[i], chunks[i + 1])
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
            print(f"    [{i}] '{_chunk_text(c)}' ints={_chunk_ints(c)}")

    return chunks, chunk_spans


# === REFACTORED: _merge_throwaways now operates on ChunkMap ===============
def _merge_throwaways(doc: Doc,
                      raw: List[Tuple[int, int]],
                      protected: Optional[Set[int]] = None
                      ) -> Tuple[ChunkMap, List[Tuple[int, int]]]:
    """Apply the smart head-aware merge.

    Returns (chunks, spans) where chunks is a ChunkMap (list of single-key
    dicts {text: int_list}) and spans[k] is the (lo, hi) token range that
    produced chunks[k].  All merges go through _merge_chunks() so int lists
    are concatenated when two chunks combine.
    """
    DEBUG_MT = True
    if DEBUG_MT:
        print(f"  [merge-throwaways] INPUT raw chunks:")
        for i, (lo, hi) in enumerate(raw):
            text = doc[lo:hi].text.strip()
            is_tw = _is_throwaway_span(doc, lo, hi)
            print(f"    [{i}] '{text}' span={lo}:{hi} is_throwaway={is_tw} "
                  f"lo_protected={lo in protected} hi_protected={hi in protected}")

    if protected is None:
        protected = set()
    out: ChunkMap = []
    out_spans: List[Tuple[int, int]] = []
    # Forward buffer: holds the chunk being accumulated when throwaways
    # glue FORWARD.  Stored as a ChunkMap item so int lists accumulate too.
    fwd_chunk: Optional[Dict[str, List[int]]] = None
    fwd_lo: Optional[int] = None

    def _is_pure_punct(text: str) -> bool:
        return all(c in (HARD_PUNCT | ANY_QUOTE | DASH_PUNCT
                          | OPEN_BRACKETS | CLOSE_BRACKETS | {","})
                   for c in text)

    for lo, hi in raw:
        text = doc[lo:hi].text.strip()
        if not text:
            continue

        cur_chunk = _chunk(text)

        if _is_throwaway_span(doc, lo, hi):
            left_protected  = lo in protected
            right_protected = hi in protected
            if left_protected and right_protected:
                if _is_pure_punct(text) and out:
                    merged = _merge_chunks(out[-1], cur_chunk, sep="")
                    out[-1] = merged
                    out_spans[-1] = (out_spans[-1][0], hi)
                    continue
                # Prepend any pending fwd_chunk, then append standalone.
                if fwd_chunk is not None:
                    cur_chunk = _merge_chunks(fwd_chunk, cur_chunk)
                    start_lo = fwd_lo if fwd_lo is not None else lo
                else:
                    start_lo = lo
                out.append(cur_chunk)
                out_spans.append((start_lo, hi))
                fwd_chunk = None
                fwd_lo  = None
                continue

            direction = _throwaway_direction(doc, lo, hi)

            # don't glue across sentence boundaries — go forward instead
            if direction == "bwd" and out and _chunk_text(out[-1]) \
                    and _chunk_text(out[-1])[-1] in HARD_PUNCT:
                if not _is_pure_punct(text):
                    direction = "fwd"

            if direction == "fwd" and right_protected:
                direction = "bwd" if out else "keep"
            if direction == "bwd" and left_protected:
                direction = "fwd"
            # SPECIAL: lone dash after closing quote → bwd
            if (direction == "fwd" and out
                    and len(text) <= 2 and any(d in text for d in DASH_PUNCT)
                    and _chunk_text(out[-1]).rstrip()
                    and _chunk_text(out[-1]).rstrip()[-1] in CLOSE_QUOTES):
                direction = "bwd"

            if direction == "bwd" and out:
                merged = _merge_chunks(out[-1], cur_chunk)
                out[-1] = merged
                out_spans[-1] = (out_spans[-1][0], hi)
            elif direction == "keep":
                if fwd_chunk is not None:
                    cur_chunk = _merge_chunks(fwd_chunk, cur_chunk)
                    start_lo = fwd_lo if fwd_lo is not None else lo
                else:
                    start_lo = lo
                out.append(cur_chunk)
                out_spans.append((start_lo, hi))
                fwd_chunk = None
                fwd_lo  = None
            else:  # forward
                if fwd_chunk is None:
                    fwd_chunk = cur_chunk
                    fwd_lo = lo
                else:
                    fwd_chunk = _merge_chunks(fwd_chunk, cur_chunk)
        else:
            # Non-throwaway: prepend any pending fwd_chunk.
            if fwd_chunk is not None:
                cur_chunk = _merge_chunks(fwd_chunk, cur_chunk)
                start_lo = fwd_lo if fwd_lo is not None else lo
            else:
                start_lo = lo
            out.append(cur_chunk)
            out_spans.append((start_lo, hi))
            fwd_chunk = None
            fwd_lo  = None

    if fwd_chunk is not None:
        if out:
            merged = _merge_chunks(out[-1], fwd_chunk)
            out[-1] = merged
            out_spans[-1] = (out_spans[-1][0], len(doc))
        else:
            out.append(fwd_chunk)
            out_spans.append((fwd_lo if fwd_lo is not None else 0, len(doc)))

    if DEBUG_MT:
        print(f"  [merge-throwaways] OUTPUT chunks:")
        for i, c in enumerate(out):
            print(f"    [{i}] '{_chunk_text(c)}' ints={_chunk_ints(c)}")

    return out, out_spans


# =============================================================================
# PIPELINE DEFINITIONS  (unchanged)
# =============================================================================
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
    ("rule_first_list_item_split",   rule_first_list_item_split,   True),
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
    ("rule_sconj_hang",              rule_sconj_hang,              True),
    ("rule_copula_reveal_split",     rule_copula_reveal_split,     True),
    ("rule_possession_reveal_split", rule_possession_reveal_split, True),
    ("rule_creation_reveal_split",   rule_creation_reveal_split,   True),
    ("rule_perception_reveal_split", rule_perception_reveal_split, True),
    ("rule_spatial_prep_reveal_split", rule_spatial_prep_reveal_split, True),
    ("rule_result_clause_reveal_split", rule_result_clause_reveal_split, True),
    ("rule_equation_reveal_split",   rule_equation_reveal_split,   True),
    ("rule_and_visualisables_split", rule_and_visualisables_split, True),
    ("rule_title_appositive_verb_split", rule_title_appositive_verb_split, True),
    ("rule_terminal_specifier_reveal", rule_terminal_specifier_reveal, True),
]

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
# MAIN ENTRY-POINT  —  now returns a ChunkMap
# =============================================================================
def split_text_into_sections(text: str, debug: bool = False) -> ChunkMap:
    """Split *text* into a list of phrase-sized sections.

    Returns a ChunkMap — a list of single-key dicts, each mapping the
    chunk's text to an integer list (empty for now):
        [{"the big dog": []}, {"jumped over": []}, ...]
    """
    is_debug = debug or SINGLE_RUN_DEBUG

    text = rule_strip_markdown(text)
    text = rule_normalise_punct(text)
    nlp_pipe = _nlp()
    doc = nlp_pipe(text)
    splits: Set[int] = {0, len(doc)}

    PROTECTED_RULE_NAMES = {
        "rule_hard_punct", "rule_dashes", "rule_ellipsis",
        "rule_quotes", "rule_brackets",
        "rule_terminal_specifier_reveal",
    }
    protected: Set[int] = set()

    if is_debug:
        print("Original: ")
        print(f'    {_format_chunks_debug([text.strip()])}')
        print()

    # ---- positive rules ----
    for name, fn, takes_splits in _POSITIVE_PIPELINE:
        prev = splits.copy()
        if takes_splits:
            new = fn(doc, splits)
        else:
            new = fn(doc)
        was_applied = bool(new - prev)
        splits |= new
        if name in PROTECTED_RULE_NAMES:
            protected |= new
        if is_debug:
            _debug_print_stage(name, was_applied, (doc, splits))

    # ---- anti-rules ----
    if is_debug:
        print()
    for name, fn in _ANTI_PIPELINE:
        prev = splits.copy()
        bad = fn(doc, splits)
        was_applied = bool(bad & prev)
        splits -= bad
        if is_debug:
            _debug_print_stage(name, was_applied, (doc, splits))

    splits |= {0, len(doc)}
    splits |= protected

    if is_debug:
        print()
        print(f"  [protected split indices: {sorted(protected)}]")
        print()

    # ---- post-processing ----
    if is_debug:
        print("=== POST-PROCESSING ===")
        print()

    raw = _build_raw_chunks(doc, splits)

    # -- merge throwaways --
    raw_text = [doc[lo:hi].text.strip() for lo, hi in raw]
    raw_text = [t for t in raw_text if t]
    merged, merged_spans = _merge_throwaways(doc, raw, protected)
    merged_clean = [c for c in merged if _chunk_text(c)]
    if is_debug:
        _debug_print_stage("merge_throwaways",
                           raw_text != [_chunk_text(c) for c in merged_clean],
                           merged_clean)

    # filter empties while keeping spans aligned
    pairs = [(c, s) for c, s in zip(merged, merged_spans) if _chunk_text(c)]
    if not pairs:
        return []
    merged, merged_spans = [p[0] for p in pairs], [p[1] for p in pairs]

    # -- fuse orphans --
    prev_chunks = list(merged)
    fused, fused_spans = _fuse_orphans(doc, merged, merged_spans, protected)
    if is_debug:
        _debug_print_stage("fuse_orphans",
                           [_chunk_text(c) for c in prev_chunks]
                               != [_chunk_text(c) for c in fused],
                           fused)

    # -- post-merge unvisualisable --
    prev_chunks = list(fused)
    fused, fused_spans = _post_merge_unvisualisable(
        doc, fused, fused_spans, protected)
    if is_debug:
        _debug_print_stage("post_merge_unvisualisable",
                           [_chunk_text(c) for c in prev_chunks]
                               != [_chunk_text(c) for c in fused],
                           fused)

    return fused


# =============================================================================
# CLI / DEMO
# =============================================================================
def _run_test(text: str) -> None:
    print(f"\nBEFORE:\n{text}")
    print("\nAFTER:")
    result = split_text_into_sections(text)
    print("[")
    for item in result:
        for k, v in item.items():
            print(f'    "{k}": {v},')
    print("]")
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

