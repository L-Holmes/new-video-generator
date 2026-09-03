"""
_visualisables_extractor.py — find every VISUALISABLE in ONE line segment.

e.g.
    entry = create_visualisables_entry("The tractor and the cat, Molly, went down the lane.")

    entry.line      --> "The tractor and the cat, Molly, went down the lane."
    entry.template  --> "The [1] and the [2], [3], went down the [4]."
    entry.as_map()  --> {"The [1] and the [2], [3], went down the [4].": {
                            "1": {"visualisable": "tractor", "variant": None,
                                  "action": None, "location": None, ...},
                            "2": {"visualisable": "cat",     ...},
                            "3": {"visualisable": "Molly",   ...},
                            "4": {"visualisable": "lane",    ...}}}

WHAT A VISUALISABLE IS
    Defined in ../VISUALISABLES_NLP_DEFINITION.txt. This file implements it:
        PART 1 (things that ARE)      --> SECTION 5, the harvesters
        PART 2 (things that are NOT)  --> SECTION 6, the filters
        PART 3 (abstract nouns)       --> SECTION 6, drop_abstract()
        PART 4 (rules of thumb)       --> SECTION 7, the length fallback

NO LINGUISTICS ARE INVENTED HERE. Every judgement is delegated:
    spaCy                    POS / dependencies / NER / noun_chunks
    shared_text_logic.py     the shared word lists  (weak verbs, idioms,
                             SFX words, measure nouns, ...)
    Brysbaert (2014)         concreteness 1..5, via kb().kb_concreteness()
    WordNet                  is it a place? a change-of-state verb?
    abstract_term_resolver   coreference:  "it" --> "the tractor"

TWO PASSES — YOU NEED BOTH
    create_visualisables_entry(segment)         per SEGMENT
        --> what is IN this segment           (visualisable)
    resolve_visualisable_details(entries, doc)  across segments
        --> variant / action / location, and the pronouns

    The second cannot be folded into the first: a thing's location is
    usually named in a DIFFERENT segment ("In Egypt," ... "there's a
    valley"), and its variant depends on everything that happened earlier.

    _visualisables_pipeline.py wires both together — call that, not this.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# The shared word lists and the knowledge base live one directory up.
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

# Safe to import eagerly: its spaCy load is lazy, its body is just word lists.
import shared_text_logic as STL          # noqa: E402


# =============================================================================
# SECTION 0 — WIRING TO THE TOOLS WE REUSE
#
# Nothing here decides anything, and it all loads LAZILY: importing this
# module must stay cheap, because myownstuff.py imports it long before it
# has any text to work on.
# =============================================================================

_KB = None                                # VISUAL_RECOMMENDER: None=untried, False=absent
_IDIOM_MEMO: tuple = (None, None, None)   # (id(doc), doc, spans) — see _idiom_spans()


def kb():
    """VISUAL_RECOMMENDER — concreteness + the WordNet lookups — or None.

    Lazy: it reads ~740 KB of recommender_data.json at import time.
    None means those checks are OFF and we degrade to spaCy alone.
    """
    global _KB
    if _KB is None:
        try:
            import VISUAL_RECOMMENDER as _vr
            _vr.kb_concreteness("test")          # force the data file to load
            _KB = _vr
        except Exception as exc:
            _KB = False
            print(f"[visualisables] note: VISUAL_RECOMMENDER unavailable "
                  f"({exc}) — abstract nouns will NOT be filtered.")
    return _KB or None


def nlp():
    """The one shared spaCy model. Required — no parse, no harvesters."""
    return STL.get_nlp_required()


# =============================================================================
# SECTION 1 — THE DIALS
# =============================================================================

# Brysbaert concreteness runs 1.0 (abstract) .. 5.0 (concrete). Keep >= 3.0.
#   e.g. KEEP  tractor 5.00   jar 4.90   field 4.26
#        DROP  history 2.96   monopoly 2.69   resource 2.55
# (Measured: every cut in 2.7..4.2 is perfect on the spec's lists. 3.0 is the
#  one that costs nothing on the real scripts — 3.5 starts eating "district"
#  3.29 and "decade" 3.19, both filmable.)
MIN_CONCRETENESS = 3.0

# ...and it may ONLY judge nouns. The scale is calibrated on nouns, and using
# it on anything else deletes visualisables the spec explicitly keeps.
#   e.g. ancient 2.04   brilliant 2.07   whoosh 2.64   (all real pictures)
CONCRETENESS_APPLIES_TO = {"thing"}          # i.e. KIND_THING only

# A setting found ONLY by its preposition must still be somewhere you could
# stand in.  e.g. IS   shelf 4.96   lamppost 4.66   lane 4.66   field 4.26
#                 NOT  weight 3.94  accident 3.26   spice trade 3.08
# Without this, "crashed into the lamppost BY ACCIDENT" makes the location
# "accident". Unrated words still pass (silence means keep).
MIN_SETTING_CONCRETENESS = 4.0

# The mercy rule: a line with NO picture in it may still hold the screen if
# it is this many real words long.  e.g. "what had to be done"
LENGTH_FALLBACK_MIN_TOKENS = 4

# Longer than this is a sentence, not something you would type into a stock
# search box.  e.g. "a jar of nutmeg that sat on the shelf" --> "jar of nutmeg"
MAX_PHRASE_TOKENS = 6


# The kinds of visualisable — one per PART 1 bullet. Plain strings, so the
# emitted json stays readable.
KIND_NAME      = "name"       # PROPN / named entity        "Molly", "Rome"
KIND_THING     = "thing"      # concrete noun phrase        "jar of nutmeg"
KIND_NUMBER    = "number"     # NUM / MONEY / PERCENT       "900 ships"
KIND_DATE      = "date"       # DATE / TIME                 "the 1600s"
KIND_QUALITY   = "quality"    # strong ADJ                  "rusted"
KIND_ACTION    = "action"     # strong VERB                 "sailed"
KIND_SOUND     = "sound"      # SFX_WORDS                   "boom"
KIND_FALLBACK  = "fallback"   # the mercy rule — no real picture
KIND_REFERENCE = "reference"  # a pronoun standing in for an earlier slot
                              #                             "They" --> tractor + cat
KIND_DEICTIC   = "deictic"    # "I" / "we" / "you"          "the narrator"
                              # Also a pronoun, but one that never points at
                              # anything IN the script — and there is no stock
                              # clip of "the narrator", so it is not a thing to
                              # fetch footage for.

# A "Concrete Visualisable" (myownstuff.py's definition): a thing, not an
# action or a quality. These are the ones that can own a shot on their own.
CONCRETE_KINDS = {KIND_NAME, KIND_THING, KIND_NUMBER, KIND_DATE}


# =============================================================================
# SECTION 2 — THE DATA STRUCTURES
# =============================================================================


@dataclass
class Visualisable:
    """ONE thing we could put on screen, plus everything we know about it.

    The four fields the TODO asks for:
        visualisable  the thing, trimmed to a search box query   "jar of nutmeg"
        variant       extra description of how it looks NOW —
                      "yellow paint splat, broken window", "really big"
        action        what it is doing in this line              "flew away"
        location      the setting it is standing in              "the lane"

    variant / action / location are None until the DOCUMENT pass runs
    (SECTION 10). None means "not worked out yet", not "there isn't one".

    The rest is provenance, so a wrong answer can be traced back.
    """
    visualisable: str
    surface: str                        # the exact substring of the line
    kind: str                           # one of the KIND_* constants
    detector: str                       # which harvester found it
    char_span: tuple[int, int]          # (start, end) into the LINE
    concreteness: float | None = None   # Brysbaert rating, when it has one
    is_setting: bool = False            # a place to stand in, not a prop
    confidence: float = 1.0             # 0..1, detector agreement

    # ---- filled by the document pass ---------------------------------------
    variant: str | None = None
    action: str | None = None
    location: str | None = None

    # ---- what the document pass needs to fill them -------------------------
    # identity      one label for this thing across the WHOLE script, so
    #               "The tractor" (line 1) and "it" (line 3) share a history
    # token_span    doc-relative token span (char_span is line-relative)
    # setting_score 1.0 = a place by entity/WordNet, 0.5 = only the
    #               preposition says so, 0.0 = not a place
    identity: str | None = None
    token_span: tuple[int, int] | None = None
    setting_score: float = 0.0

    # owner         the WHOLE this thing is a part of, when a possessive
    #               pronoun said so:  "Its windscreen" --> owner = "tractor".
    #               Only ever filled from the abstract-terms map, and only so
    #               that a part's damage can describe its whole (step 5).
    owner: str | None = None

    def is_concrete(self) -> bool:
        """A thing, not an action or a quality."""
        return self.kind in CONCRETE_KINDS

    def as_dict(self) -> dict:
        """The json row: the answer first, then just enough workings.

        e.g. {"visualisable": "tractor", "variant": "yellow paint",
              "action": "crashed", "location": "lampost", "kind": "thing",
              "identity": "tractor", "is_setting": False, "confidence": 0.9}

        The full record (surface / detector / char_span / concreteness /
        token_span / setting_score) stays on this object, for anything that
        holds the Visualisable rather than the map.
        """
        return {
            "visualisable": self.visualisable,
            "variant": self.variant,
            "action": self.action,
            "location": self.location,
            "kind": self.kind,
            "identity": self.identity,
            "is_setting": self.is_setting,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class VisualisablesEntry:
    """The result for ONE split line: the line with its visualisables punched
    out into numbered slots, plus what fills each slot.

    e.g. line          "The tractor and the cat went down the lane."
         template      "The [1] and the [2] went down the [3]."
         visualisables {1: Visualisable(tractor), 2: cat, 3: lane}

    Slots are numbered 1..n LEFT TO RIGHT, so "[2]" always means the same
    thing to every later stage.
    """
    line: str
    template: str
    visualisables: dict[int, Visualisable] = field(default_factory=dict)

    dropped: list = field(default_factory=list)         # candidates the filters killed

    def as_map(self) -> dict:
        """The output shape the TODO asks for, for this one line:

            {template: {"1": {...}, "2": {...}}}

        Slot numbers are STRING keys — integer keys do not survive json.
        """
        return {self.template: {str(n): v.as_dict()
                                for n, v in sorted(self.visualisables.items())}}

    def concrete_only(self) -> dict[int, Visualisable]:
        """Just the slots that are things — what the renderer fetches footage
        for. e.g. drops "sailed" and "rusted", keeps "900 ships"."""
        return {n: v for n, v in self.visualisables.items() if v.is_concrete()}


@dataclass
class Candidate:
    """Internal working record: a span a harvester put forward, before the
    filters have had their say. Token indices point into ctx.doc.

    Every filter is list[Candidate] -> list[Candidate], and sets dropped_by
    rather than deleting, so "why is my line empty" is answerable.
    """
    start: int                          # token index into ctx.doc, inclusive
    end: int                            # token index into ctx.doc, exclusive
    kind: str
    detector: str
    confidence: float = 1.0
    concreteness: float | None = None
    is_setting: bool = False
    setting_score: float = 0.0
    dropped_by: str | None = None       # e.g. "abstract(2.69)", "weak-verb"


@dataclass
class Context:
    """The parsed world around the target line.

    WHY: spaCy parses a fragment badly — "went down the lane" on its own has
    no subject and often the wrong POS. So we parse the segment INSIDE its
    neighbours and then slice out the tokens that belong to it.

        doc          the spaCy Doc over the whole window
        line         the target line segment, verbatim
        line_chars   (start, end) of the segment inside the window text
        line_tokens  (start, end) token indices of the segment inside doc
        blocked      idiom / discourse spans, kept for the length fallback
    """
    doc: object
    line: str
    line_chars: tuple[int, int]
    line_tokens: tuple[int, int]
    blocked: list = field(default_factory=list)
    keep_pronouns: bool = False       # keep "it"/"they" for the document pass

    def target_tokens(self):
        """The tokens of the TARGET LINE only. Every harvester iterates this,
        never doc — so nothing can be harvested out of the surrounding
        context. That is what makes "parse wide, extract narrow" safe."""
        lo, hi = self.line_tokens
        return list(self.doc[lo:hi])

    def to_line_chars(self, tok_start: int, tok_end: int) -> tuple[int, int]:
        """Token span (doc-relative) --> character span (line-relative), which
        is what the template needs.  e.g. (14, 15) --> (23, 27)"""
        lo, hi = self.line_tokens
        a, b = max(int(tok_start), lo), min(int(tok_end), hi)
        if a >= b:
            return (0, 0)
        span = self.doc[a:b]
        base = self.line_chars[0]
        return (max(span.start_char - base, 0),
                min(span.end_char - base, len(self.line)))


# =============================================================================
# SECTION 3 — SMALL SHARED HELPERS
# =============================================================================


def _clip_to_line(ctx: "Context", start: int, end: int) -> tuple[int, int] | None:
    """Intersect an entity span with the target line, or None if they miss.

    An entity can straddle a segment boundary — whatever cut the script did
    not protect entity spans: "the Circus | Maximus." Requiring containment
    threw those away and left the second segment with nothing, so we keep
    the showable half.
    """
    lo, hi = ctx.line_tokens
    a, b = max(int(start), lo), min(int(end), hi)
    return (a, b) if a < b else None


def _idiom_spans(doc):
    """STL.find_idiom_spans(doc), memoised for the current doc.

    It scans the WHOLE doc for all 27 idioms and we call it once per LINE —
    i.e. quadratic in script length, and ~70% of the warm per-line cost
    before this. The answer only depends on the doc, so compute it once.
    """
    global _IDIOM_MEMO
    key, held, spans = _IDIOM_MEMO
    if key == id(doc) and held is doc:
        return spans
    spans = STL.find_idiom_spans(doc)
    _IDIOM_MEMO = (id(doc), doc, spans)     # hold the doc too, so the id can't be reused
    return spans


def _find_token_span(ctx: "Context", phrase: str) -> tuple[int, int] | None:
    """First token span INSIDE THE LINE whose text is `phrase`, or None.

    Needed because a few of shared_text_logic's extractors hand back strings
    ("Vasco da Gama") while everything here works in token indices.
    """
    lo, hi = ctx.line_tokens
    want = " ".join(phrase.lower().split())
    if not want:
        return None
    for i in range(lo, hi):
        for j in range(i + 1, min(hi, i + 12) + 1):    # no name run is longer
            if " ".join(ctx.doc[i:j].text.lower().split()) == want:
                return (i, j)
    return None


def _is_frozen_pair(doc, start: int, end: int) -> bool:
    """Does this span contain a pair that must never be cut?
    e.g. "bread and butter", "kind of"  -> True (one picture, not two)"""
    lowers = tuple(t.lower_ for t in doc[start:end])
    return any(lowers[i:i + 2] in STL.FROZEN_BIGRAMS
               for i in range(max(0, len(lowers) - 1)))


def _wordnet_verdict(word: str) -> str | None:
    """"physical" / "abstract" / None — the backstop for words Brysbaert
    never rated. Which child of entity.n.01 does the word's FIRST noun sense
    hang under?

    NOTE abstraction.n.06 — .n.01 is a different, low-level synset and scores
    0/10. First sense only: widening to 2-3 senses measures worse.
    None (WordNet can't say, or isn't installed) means KEEP.
    """
    knowledge = kb()
    if knowledge is None:
        return None
    physical = knowledge._wn_has_hypernym(
        word, frozenset({"physical_entity.n.01"}), 1)
    abstract = knowledge._wn_has_hypernym(
        word, frozenset({"abstraction.n.06"}), 1)
    if abstract and not physical:
        return "abstract"
    if physical and not abstract:
        return "physical"
    return None


# =============================================================================
# SECTION 4 — THE PER-SEGMENT PIPELINE   (read this first)
#
# Eleven steps, one call each, top to bottom, no branching. Every other
# section below is one of these steps written out.
# =============================================================================


def create_visualisables_entry(
    line_segment: str,
    next_line_segment: str | None = None,
    all_preceeding_text: str | None = None,
    doc=None,
    line_token_span: tuple[int, int] | None = None,
    keep_pronouns: bool = False,
) -> VisualisablesEntry:
    """Find every visualisable in ONE line segment.

    @input line_segment = the section of a line we are labelling.
        e.g. "In Egypt,"
    @input next_line_segment = the segment AFTER it. Parsing context only —
        nothing is ever harvested out of it.
        e.g. "there's a valley filled with"
    @input all_preceeding_text = everything before it, or None for the very
        first segment. Parsing context, and the pool the pronouns resolve
        into.  e.g. "In Egypt, there's a valley filled with"
    @input doc, line_token_span = THE FAST PATH. If the text this segment
        sits in has already been parsed, hand the Doc over with the
        segment's token span and nothing is re-parsed or re-located.
        e.g. doc=<the whole script, parsed>, line_token_span=(0, 3)
    @input keep_pronouns = keep "it"/"they"/"she" as KIND_REFERENCE slots
        instead of dropping them, so resolve_visualisable_details() can turn
        each into what it points at. On its own a pronoun is not a picture,
        which is why this is off by default.

    @output VisualisablesEntry — the template segment and its numbered slots.
        e.g. template      "The [1] and the [2], [3], went down the [4]."
             visualisables {1: tractor, 2: cat, 3: Molly, 4: lane}

    THE STEPS
        0  parse the segment in context, so spaCy sees whole sentences
        1  mark the spans we may never emit  (idioms, discourse furniture)
        2  harvest every candidate — one detector per PART 1 bullet
        3  drop the PART 2 non-visualisables (pronouns, weak words, ...)
        4  drop the PART 3 abstract nouns    (Brysbaert / WordNet)
        5  trim each survivor to its searchable core  ("a jar of" -> "jar")
        6  split "X and Y" into two candidates
        7  resolve overlaps — one picture per piece of text
        8  order left to right, which fixes the slot numbers [1] [2] [3]
        9  mercy rule: nothing survived, but it is 4+ real words
       10  classify settings, then build the template + the map
    """
    # 0) PARSE
    ctx = build_context(line_segment,
                        next_line_segment,
                        all_preceeding_text,
                        doc,
                        line_token_span)
    ctx.keep_pronouns = keep_pronouns

    # 1) BLOCK — spans made of concrete words that are not pictures.
    blocked = find_blocked_spans(ctx)

    # 2) HARVEST — deliberately over-generous; the filters make it right.
    candidates = []
    candidates += find_name_candidates(ctx)          # PROPN + named entities
    candidates += find_number_candidates(ctx)        # NUM + measures + money
    candidates += find_date_candidates(ctx)          # DATE / TIME
    candidates += find_noun_phrase_candidates(ctx)   # noun_chunks — the bulk
    candidates += find_quality_candidates(ctx)       # strong adjectives
    candidates += find_action_candidates(ctx)        # strong verbs
    candidates += find_sound_candidates(ctx)         # SFX words
    harvest = list(candidates)                       # a handle, so we can show what died

    # 3) FILTER — PART 2. Nothing that only POINTS at a picture.
    candidates = drop_blocked(candidates, blocked)
    candidates = drop_non_visualisable(candidates, ctx)

    # 4) FILTER — PART 3. Nothing you cannot point a camera at.
    candidates = drop_abstract(candidates, ctx)

    # 5) TIDY — cut each down to what you would type into a search box.
    candidates = trim_to_searchable_core(candidates, ctx)

    # 6) SPLIT — "dust and rock" is two pictures, not one.
    candidates = split_coordinated_candidates(candidates, ctx)

    # 7) DEDUPE — the harvesters overlap by design; pick one per span.
    candidates = resolve_overlaps(candidates, ctx)

    # 8) ORDER — left to right.
    candidates = order_candidates(candidates)

    # 9) FALLBACK — the mercy rule, so a real segment is never left empty.
    candidates = apply_length_fallback(candidates, ctx)

    # 10) BUILD — mark settings, punch out the template, number the slots.
    candidates = classify_settings(candidates, ctx)
    entry = build_entry(ctx, candidates)
    entry.dropped = [c for c in harvest if c.dropped_by]
    return entry


# =============================================================================
# STEP 0 — THE CONTEXT
# =============================================================================


def build_context(line_segment: str,
                  following: str | None,
                  preceding: str | None,
                  doc=None,
                  line_token_span: tuple[int, int] | None = None) -> Context:
    """Parse the segment inside its neighbours, and locate it in the result.

    FAST PATH — the caller hands us a doc it has already parsed, plus this
    segment's token span in it. Nothing to concatenate, nothing to locate,
    nothing to align. Prefer it: en_core_web_sm runs at a flat 0.10 ms/token,
    so parsing a growing prefix once per segment is quadratic and pointless.

    FALLBACK — build the window  preceding + segment + following  BY
    CONCATENATION, keeping the offsets as we go. Never str.find() the segment
    back afterwards: a segment can legitimately occur twice ("It was.").
    """
    # ---- fast path -------------------------------------------------------
    if doc is not None and line_token_span is not None:
        lo, hi = int(line_token_span[0]), int(line_token_span[1])
        span = doc[lo:hi]
        # ctx.line is the SPAN's text, not line_segment: the two should be
        # equal, and if they ever differ the doc is what the offsets and the
        # template are computed against.
        return Context(doc=doc, line=span.text,
                       line_chars=(span.start_char, span.end_char),
                       line_tokens=(lo, hi))

    # ---- fallback --------------------------------------------------------
    line = " ".join(line_segment.split())
    before = " ".join((preceding or "").split())
    after = " ".join((following or "").split())

    parts, start = [], 0
    if before:
        parts.append(before)
        start = len(before) + 1
    parts.append(line)
    if after:
        parts.append(after)
    window = " ".join(parts)
    end = start + len(line)

    parsed = doc if doc is not None else nlp()(window)
    # "expand" because spaCy tokenisation may not align to our offsets.
    span = parsed.char_span(start, end, alignment_mode="expand")
    if span is None:
        span = parsed[:]
    return Context(doc=parsed, line=line, line_chars=(start, end),
                   line_tokens=(span.start, span.end))


# =============================================================================
# STEP 2 — SPANS WE MAY NEVER EMIT
# =============================================================================


def find_blocked_spans(ctx: Context) -> list[tuple[int, int]]:
    """Token spans made of concrete words that are NOT pictures (PART 2).

    Found BEFORE harvesting, because their words individually look perfect:

        idioms         "out of the blue", "the rest is history"
                       — filming the blue would be wrong
        discourse      "here's the thing", "plot twist"  — script furniture
        frozen bigrams "want to", "kind of"  — pairs that never come apart

    All three lists come from shared_text_logic.
    """
    doc = ctx.doc
    lo, hi = ctx.line_tokens
    lowers = [t.lower_ for t in doc]
    spans: list[tuple[int, int]] = []

    for a, b in _idiom_spans(doc):
        if a < hi and b > lo:
            spans.append((a, b))

    def scan(phrases):
        for phrase in phrases:
            n = len(phrase)
            for i in range(lo, max(lo, hi - n + 1)):
                if tuple(lowers[i:i + n]) == tuple(phrase):
                    spans.append((i, i + n))

    scan(STL.DISCOURSE_PIVOT_PHRASES)     # "here's the thing", "plot twist"
    scan(STL.FROZEN_BIGRAMS)              # "want to", "kind of"

    spans = sorted(set(spans))
    ctx.blocked = spans                   # apply_length_fallback() reads this
    return spans


# =============================================================================
# SECTION 5 — STEP 3: THE HARVESTERS
#
# One function per bullet of spec PART 1. Every one of them:
#   * iterates ctx.target_tokens() only,
#   * returns list[Candidate] (possibly empty),
#   * is allowed to overlap the others — step 8 sorts that out,
#   * never filters. Being generous here is the point.
# =============================================================================


def find_name_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullet 1 — capitalised and named things. KIND_NAME.
    e.g. "Molly", "Rome", "Vasco da Gama", "the Banda Islands"

    The strongest kind: you can go and fetch the actual photograph of it.
    Two sources, both already written —
        spaCy entities in NAME_ENT_LABELS | PLACE_ENT_LABELS
        STL.extract_name_runs(), the shared capital-run walker, which
        already globs "Vasco da Gama" together and already refuses to start
        on a CAP_STOPWORD ("The", "But", "January").
    """
    doc, out = ctx.doc, []
    lo, hi = ctx.line_tokens

    # PLACE_ENT_LABELS is unioned in because NAME_ENT_LABELS omits LOC, which
    # PART 1 explicitly lists. Without it "the Banda Islands" is only a noun
    # chunk and loses its determiner.
    name_labels = STL.NAME_ENT_LABELS | STL.PLACE_ENT_LABELS
    for ent in doc.ents:
        if ent.label_ not in name_labels:
            continue
        span = _clip_to_line(ctx, ent.start, ent.end)
        if span is None:
            continue
        whole = (ent.start >= lo and ent.end <= hi)
        out.append(Candidate(span[0], span[1], KIND_NAME,
                             "name:ent" if whole else "name:ent-clipped",
                             confidence=1.0 if whole else 0.8))

    # Capital runs spaCy's NER missed.
    for run in STL.extract_name_runs(ctx.line):
        span = _find_token_span(ctx, run)
        if span and not any(c.start <= span[0] and span[1] <= c.end for c in out):
            out.append(Candidate(span[0], span[1], KIND_NAME,
                                 "name:run", confidence=0.9))
    return out


def find_number_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullets 4 and 6 — numbers, quantities, money. KIND_NUMBER.
    e.g. "900 ships", "15 metres", "£4,000", "seventeen million"

    A figure is visualisable because it goes on screen as a counter or a bar.

    THE ONE RULE THAT MATTERS: a number and its measure word are ONE
    candidate and must never be split, or the noun-phrase harvester emits
    "metres" on its own, which is a picture of nothing.
    """
    doc, out = ctx.doc, []
    lo, hi = ctx.line_tokens
    numeric = STL.NUMERIC_ENTS - {"DATE", "TIME"}      # dates are their own kind

    for ent in doc.ents:
        if ent.label_ not in numeric:
            continue
        span = _clip_to_line(ctx, ent.start, ent.end)
        if span is None:
            continue
        whole = (ent.start >= lo and ent.end <= hi)
        out.append(Candidate(span[0], span[1], KIND_NUMBER,
                             "number:ent" if whole else "number:ent-clipped",
                             confidence=1.0 if whole else 0.8))

    for tok in ctx.target_tokens():
        if tok.pos_ != "NUM" and not tok.like_num:
            continue
        start, end = tok.i, tok.i + 1
        # glue the measure word on:  "900 ships", "15 metres"
        head = tok.head
        if (end <= head.i < hi
                and tok.dep_ in {"nummod", "quantmod", "compound"}
                and (head.lower_ in STL.MEASURE_NOUNS
                     or head.pos_ in {"NOUN", "PROPN"})):
            end = head.i + 1
        # ...and any number words in front:  "3 thousand years"
        while (start - 1 >= lo
               and doc[start - 1].dep_ in {"quantmod", "compound"}
               and (doc[start - 1].pos_ == "NUM" or doc[start - 1].like_num)):
            start -= 1
        out.append(Candidate(start, end, KIND_NUMBER, "number:tok",
                             confidence=0.9))
    return out


def find_date_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullet 5 — dates, years, times. KIND_DATE.
    e.g. "1946", "the 1600s", "the 19th century", "August 1945", "midnight"

    The whole expression is the unit, not the bare digits — it goes on screen
    as a timeline marker travelling to that year. Kept separate from
    find_number_candidates() because a date is a timeline and a number is a
    chart, even though spaCy's NER hands them over together.
    """
    out = []
    lo, hi = ctx.line_tokens
    for ent in ctx.doc.ents:
        if ent.label_ not in {"DATE", "TIME"}:
            continue
        span = _clip_to_line(ctx, ent.start, ent.end)
        if span is None:
            continue
        whole = (ent.start >= lo and ent.end <= hi)
        out.append(Candidate(span[0], span[1], KIND_DATE,
                             "date:ent" if whole else "date:ent-clipped",
                             confidence=1.0 if whole else 0.8))
    return out


def find_noun_phrase_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullets 2 and 3 — ordinary concrete nouns, as WHOLE PHRASES.
    KIND_THING. This is the bulk of the work.

    THE UNIT IS THE NOUN PHRASE, NOT THE NOUN:
        "a jar of nutmeg"     not "jar"
        "the old wooden ship" not "ship"

    So we start from spaCy's noun_chunks, with one repair: it cuts before a
    preposition, so "a jar of nutmeg" arrives as ["a jar", "nutmeg"]. Re-join
    across STL.PROMISCUOUS_PREPS ({"of"} — the one attachment that is
    reliably nominal) when the parse actually says jar <- of <- nutmeg.

    (Measured: sm and trf give byte-identical noun_chunks on all seven worked
     examples, and trf costs 9x and lost Molly/PERSON — so stay on sm.)
    """
    doc, out = ctx.doc, []
    lo, hi = ctx.line_tokens
    chunks = [nc for nc in doc.noun_chunks if lo <= nc.start and nc.end <= hi]

    skip: set[int] = set()
    for i, nc in enumerate(chunks):
        if i in skip:
            continue
        start, end, root = nc.start, nc.end, nc.root
        for j in range(i + 1, len(chunks)):
            nxt = chunks[j]
            between = doc[end:nxt.start]
            if (len(between) == 1
                    and between[0].lower_ in STL.PROMISCUOUS_PREPS
                    and between[0].head.i == root.i
                    and nxt.root.head.i == between[0].i):
                end = nxt.end
                skip.add(j)
            else:
                break
        if doc[start:end].root.pos_ not in {"NOUN", "PROPN", "PRON"}:
            continue
        out.append(Candidate(start, end, KIND_THING, "np:chunk",
                             confidence=0.9))
    return out


def find_quality_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullet 7 — strong descriptive adjectives. KIND_QUALITY.
    e.g. "red", "rusted", "ancient", "microscopic" — they change what the
    picture LOOKS like.

    Test is ADJ, and not in STL.WEAK_ADJ_LEMMAS (which is where "many",
    "several", "possible" get thrown out).

    An adjective INSIDE a noun chunk we already harvested ("the OLD wooden
    ship") must not become its own slot — emit it anyway, resolve_overlaps()
    absorbs it. It only stands alone as a predicate ("the sky turned red").
    """
    out = []
    for tok in ctx.target_tokens():
        if tok.pos_ != "ADJ":
            continue
        if tok.lemma_.lower() in STL.WEAK_ADJ_LEMMAS:
            continue
        out.append(Candidate(tok.i, tok.i + 1, KIND_QUALITY,
                             "quality:adj", confidence=0.6))
    return out


def find_action_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullet 8 — strong, filmable verbs. KIND_ACTION.
    e.g. "jumped", "sailed", "exploded", "sank"

    Test: VERB, and not in WEAK_VERB_LEMMAS / WEAK_VERB_FORMS — that pair
    removes be/have/do/get/make/know/seem, the verbs whose picture always
    lives in the noun next to them.

    The particle comes too: "flew AWAY", "dried UP" — it is what makes the
    action filmable, and "flew" and "flew away" are different shots.

    These are NOT concrete visualisables. They are harvested so the document
    pass can attach them to the thing doing them, as `action`.
    """
    out = []
    lo, hi = ctx.line_tokens
    for tok in ctx.target_tokens():
        if tok.pos_ != "VERB":
            continue
        if (tok.lemma_.lower() in STL.WEAK_VERB_LEMMAS
                or tok.text.lower() in STL.WEAK_VERB_FORMS):
            continue
        end = tok.i + 1
        for child in tok.children:
            if child.dep_ in STL.PARTICLE_DEPS and lo <= child.i < hi:
                end = max(end, child.i + 1)
        out.append(Candidate(tok.i, end, KIND_ACTION, "action:verb",
                             confidence=0.7))
    return out


def find_sound_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullet 9 — bare sound-effect words. KIND_SOUND.
    e.g. "and then BOOM the roof came down" — boom earns its own beat.

    BARE only. "the sonic boom shattered windows" is a KIND_THING and belongs
    to the noun-phrase harvester. spaCy's POS is the discriminator.
    """
    out = []
    for tok in ctx.target_tokens():
        if tok.lower_ not in STL.SFX_WORDS:
            continue
        if not (tok.pos_ in {"INTJ", "X"} or tok.dep_ in {"intj", "discourse"}):
            continue
        out.append(Candidate(tok.i, tok.i + 1, KIND_SOUND, "sound:sfx",
                             confidence=0.8))
    return out


# =============================================================================
# SECTION 6 — STEPS 4 AND 5: THE FILTERS
#
# Spec PART 2 (not visualisables) and PART 3 (the abstract-noun grey area).
# Every filter is list[Candidate] -> list[Candidate], and sets `dropped_by`
# rather than deleting, so entry.dropped can show its working.
# =============================================================================


def drop_blocked(candidates: list[Candidate],
                 blocked: list[tuple[int, int]]) -> list[Candidate]:
    """Remove anything OVERLAPPING a span from find_blocked_spans().
    Overlap, not containment: an idiom's words must not leak out either way
    round.  e.g. "out of the blue" -> "the blue" is not a candidate."""
    if not blocked:
        return candidates
    kept = []
    for c in candidates:
        hit = next(((a, b) for a, b in blocked
                    if c.start < b and c.end > a), None)
        if hit is None:
            kept.append(c)
        else:
            c.dropped_by = f"blocked{list(hit)}"
    return kept


def drop_non_visualisable(candidates: list[Candidate],
                          ctx: Context) -> list[Candidate]:
    """Spec PART 2 — the things that only POINT at a picture.

        pronouns             "it", "they", "this"  (unless keep_pronouns)
        weak verbs           STL.WEAK_VERB_LEMMAS / _FORMS
        weak adjectives      STL.WEAK_ADJ_LEMMAS
        generic topic nouns  STL.GENERIC_TOPIC_NOUNS — "thing, way, time,
                             part, place, people, world, story, fact". Real
                             nouns that turn up in every script about
                             anything, so they never earn a picture.
        function words       nothing left but DET/ADP/PART

    None of these lists is written here — they are shared_text_logic's, so
    nothing downstream can disagree about what a weak verb is.
    """
    doc, kept = ctx.doc, []
    for c in candidates:
        root = doc[c.start:c.end].root
        lemma = root.lemma_.lower()
        why = None
        if root.pos_ == "PRON":
            # With keep_pronouns it survives as a KIND_REFERENCE slot for the
            # document pass to resolve into the thing it points at.
            if ctx.keep_pronouns and root.lower_ not in {"what", "which",
                                                         "that", "who"}:
                c.kind = KIND_REFERENCE
                kept.append(c)
                continue
            why = "pronoun"
        elif root.pos_ == "VERB" and (lemma in STL.WEAK_VERB_LEMMAS
                                      or root.text.lower() in STL.WEAK_VERB_FORMS):
            why = "weak-verb"
        elif root.pos_ == "ADJ" and lemma in STL.WEAK_ADJ_LEMMAS:
            why = "weak-adj"
        elif (c.kind != KIND_NAME
                and root.pos_ in {"NOUN", "PROPN"}
                and lemma in STL.GENERIC_TOPIC_NOUNS):
            # only when the generic noun is the phrase's HEAD:
            #   "the main thing"        -> goes
            #   "a way out of the mine" -> stays (it is about the mine)
            why = "generic-noun"
        elif all(t.pos_ in STL.LIGHTWEIGHT_POS or t.is_punct
                 for t in doc[c.start:c.end]):
            why = "function-words"
        if why:
            c.dropped_by = why
        else:
            kept.append(c)
    return kept


def drop_abstract(candidates: list[Candidate],
                  ctx: Context) -> list[Candidate]:
    """Spec PART 3 — the known gap, and the reason this file exists.

    "monopoly", "inflation", "betrayal", "freedom", "tension" are all tagged
    NOUN, so every check above waves them through, and none of them is
    something you can point a camera at.

    We do not write a word list for this. Three steps, in order:

      1. BRYSBAERT (2014) concreteness — 37k words, human-rated 1..5.
         Score >= MIN_CONCRETENESS keeps it, below drops it.
         e.g. "The company held a monopoly on the spice trade."
              company 4.21 KEEP   spice trade 3.08 KEEP   monopoly 2.69 DROP

      2. NOT RATED? Ask WordNet (_wordnet_verdict). ~87% right, and it barely
         ever fires — Brysbaert covers 85% of real noun heads, and the ones
         it misses are proper nouns and years, which never reach this filter.

      3. STILL NOTHING? KEEP IT. A wrong drop blanks the line; a wrong keep
         costs one mediocre stock clip that manual tagging fixes.

    Scored on the phrase's HEAD lemma — Brysbaert rates single words.
    e.g. "a wall of water" is scored on "wall".

    ONLY KIND_THING reaches this filter (CONCRETENESS_APPLIES_TO). Names,
    numbers and dates are concrete by construction; adjectives, verbs and SFX
    would be wrongly killed by a noun-calibrated scale.

    NOTE the abstract term is DROPPED, not flagged. The spec's idea of
    resolving it into a concrete stand-in ("inflation" -> a shrinking pile of
    banknotes) has nothing to work from later. Revisit here if that gets
    built: keep the candidate and add a flag.
    """
    knowledge, doc, kept = kb(), ctx.doc, []
    for c in candidates:
        if c.kind not in CONCRETENESS_APPLIES_TO:
            kept.append(c)
            continue
        head = doc[c.start:c.end].root.lemma_.lower()

        # 1. Brysbaert, the published answer.
        score = knowledge.kb_concreteness(head) if knowledge else None
        c.concreteness = score
        if score is not None:
            if score >= MIN_CONCRETENESS:
                kept.append(c)
            else:
                c.dropped_by = f"abstract({score:.2f})"
            continue

        # 2. Not rated — ask WordNet.
        if _wordnet_verdict(head) == "abstract":
            c.dropped_by = "abstract(wordnet)"
            continue

        # 3. Silence means KEEP.
        kept.append(c)
    return kept


# =============================================================================
# SECTION 7 — STEPS 6 TO 10: TIDY, SPLIT, DEDUPE, ORDER, FALLBACK
# =============================================================================


def trim_to_searchable_core(candidates: list[Candidate],
                            ctx: Context) -> list[Candidate]:
    """Cut each candidate down to what you would type into a search box.
    e.g. "a jar of nutmeg sat on the shelf" --> "jar of nutmeg"
         "several rusted anchors"           --> "rusted anchors"

    From the front: determiners and weak/quantifier adjectives.
    From the back:  punctuation and dangling prepositions.
    Strong adjectives, compounds and nummods STAY — they are what the picture
    looks like.  Still too long? Keep the head plus its amod/compound/nummod
    children, which is what abstract_term_resolver.mention_name() does too.
    """
    doc, out = ctx.doc, []
    for c in candidates:
        if c.kind in {KIND_NAME, KIND_NUMBER, KIND_DATE, KIND_SOUND}:
            out.append(c)        # already exactly the thing you would search
            continue
        start, end = c.start, c.end
        while start < end and (
                doc[start].pos_ in {"DET", "PUNCT", "PART", "CCONJ"}
                or (doc[start].pos_ == "ADJ"
                    and doc[start].lemma_.lower() in STL.WEAK_ADJ_LEMMAS)):
            start += 1
        while end > start and (doc[end - 1].is_punct
                               or doc[end - 1].pos_ in {"ADP", "PART", "CCONJ"}):
            end -= 1
        if start >= end:
            c.dropped_by = "trimmed-to-nothing"
            continue
        if end - start > MAX_PHRASE_TOKENS:
            root = doc[start:end].root
            keep = {root.i} | {ch.i for ch in root.children
                               if ch.dep_ in {"amod", "compound", "nummod"}
                               and start <= ch.i < end}
            start, end = min(keep), max(keep) + 1
        c.start, c.end = start, end
        out.append(c)
    return out


def split_coordinated_candidates(candidates: list[Candidate],
                                 ctx: Context) -> list[Candidate]:
    """Two visualisables joined by "and"/"or" are TWO visualisables.
    e.g. "dust and rock"            --> dust | rock
         "the yellow and black guy" --> yellow | black guy

    Do it on the DEPENDENCY TREE (a `conj` child of the head), never on the
    string: "bread and butter" and "cat and mouse game" are ONE picture each,
    and STL.FROZEN_BIGRAMS is what says so.

    spaCy usually gives two noun_chunks already, so this mostly catches the
    cases inside one chunk — coordinated adjectives and compounds.
    """
    doc, out = ctx.doc, []
    for c in candidates:
        root = doc[c.start:c.end].root
        conjs = sorted((ch for ch in root.children
                        if ch.dep_ == "conj" and c.start <= ch.i < c.end),
                       key=lambda t: t.i)
        if not conjs or _is_frozen_pair(doc, c.start, c.end):
            out.append(c)
            continue
        pieces, cut = [], c.start
        for ch in conjs:
            edge = min(t.i for t in ch.subtree if t.i >= c.start)
            if cut < edge:
                pieces.append((cut, edge))
            cut = edge
        pieces.append((cut, c.end))
        for a, b in pieces:
            while a < b and doc[a].pos_ in {"CCONJ", "DET", "PUNCT"}:
                a += 1
            while b > a and doc[b - 1].is_punct:
                b -= 1
            if a < b:
                out.append(Candidate(a, b, c.kind, c.detector + "+conj",
                                     confidence=c.confidence * 0.95))
    return out


def resolve_overlaps(candidates: list[Candidate],
                     ctx: Context) -> list[Candidate]:
    """Seven harvesters ran over the same tokens. Pick ONE per span.

    Longest span first, so "900 ships" beats "900" and "the old wooden ship"
    absorbs "old" and "wooden" (one slot, not three). Precedence only breaks
    ties, which is where "the 1600s" comes out a date rather than a thing:

        name (5) > number / date (4) > thing (3) > action / sound (2)
                 > quality (1) > fallback (0)

    Losers keep the winner in `dropped_by`, so you can see what absorbed what.
    """
    precedence = {KIND_NAME: 5, KIND_NUMBER: 4, KIND_DATE: 4, KIND_THING: 3,
                  KIND_ACTION: 2, KIND_SOUND: 2, KIND_QUALITY: 1,
                  KIND_FALLBACK: 0}
    ranked = sorted(candidates,
                    key=lambda c: (-(c.end - c.start),
                                   -precedence.get(c.kind, 0),
                                   -c.confidence,
                                   c.start))
    kept: list[Candidate] = []
    for c in ranked:
        clash = next((k for k in kept
                      if c.start < k.end and c.end > k.start), None)
        if clash is None:
            kept.append(c)
        else:
            c.dropped_by = f"absorbed-by:{clash.detector}"
    return kept


def order_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Sort left to right. THIS is what fixes the slot numbers: afterwards
    index 0 is [1], index 1 is [2], and template matches visualisables."""
    return sorted(candidates, key=lambda c: (c.start, c.end))


def apply_length_fallback(candidates: list[Candidate],
                          ctx: Context) -> list[Candidate]:
    """Spec PART 1, last bullet — the mercy rule.

    Nothing survived, but the segment is >= LENGTH_FALLBACK_MIN_TOKENS real
    words ("what had to be done")? Emit ONE low-confidence KIND_FALLBACK
    covering the whole segment. It is not a picture — it is a licence for the
    segment to stand on its own and hold whatever is already on screen.

    NO fallback when the segment is nothing but a blocked phrase — e.g. "and
    the rest is history", where the grammar glue around the idiom does not
    rescue it.
    """
    if candidates:
        return candidates
    lo, hi = ctx.line_tokens
    content = [t.i for t in ctx.doc[lo:hi] if not t.is_punct and not t.is_space]
    substantive = [i for i in content
                   if ctx.doc[i].pos_ not in STL.LIGHTWEIGHT_POS]
    if substantive and all(any(a <= i < b for a, b in ctx.blocked)
                           for i in substantive):
        return candidates
    # shared_text_logic's own test, so the two can't drift apart
    if not STL.has_visualisable_content(ctx.doc, lo, hi):
        return candidates
    if len(content) < LENGTH_FALLBACK_MIN_TOKENS:
        return candidates
    return [Candidate(lo, hi, KIND_FALLBACK, "fallback:length",
                      confidence=0.2)]


# =============================================================================
# SECTION 8 — STEP 11a: WHICH ONES ARE SETTINGS
# =============================================================================


def classify_settings(candidates: list[Candidate],
                      ctx: Context) -> list[Candidate]:
    """Mark which candidates are the PLACE the others are standing in.

    myownstuff.py's third definition: a "Setting" is a place that usually
    contains the visualisables — "field", "25th Avenue", "Planet Earth". The
    renderer needs it: a setting goes in the BACKGROUND, a thing on top.

    Two signals, scored separately (1.0 beats 0.5) rather than OR-ed:

      WHAT IT IS  (1.0)  kb_is_place() — 250 countries + capitals, then
                         WordNet geographical_area / district / urban_area —
                         or a GPE / LOC / FAC entity.   e.g. "Egypt"
      HOW IT IS USED (0.5)  it hangs off a spatial preposition, or off a
                         preposition whose verb is a verb of MOTION.
                         e.g. "went DOWN the lane"  ("down" is in neither
                         prep list, so the motion verb is what saves it)

    A 0.5 setting must also pass MIN_SETTING_CONCRETENESS, or "crashed into
    the lamppost BY ACCIDENT" makes "accident" a place.

    KNOWN OVER-FIRE: a metaphorical locative still scores 0.5 — "held a
    monopoly ON the spice trade". Attachment cannot separate that from "sat
    ON the shelf", so instead the document pass takes the STRONGEST signal in
    a sentence, and a real place always beats it.

    Does not remove anything — a setting is still a visualisable with a slot.
    """
    knowledge, doc = kb(), ctx.doc
    spatial_preps = STL.SPATIAL_LOCATIVE_PREPS | STL.SPATIAL_DIRECTIONAL_PREPS
    for c in candidates:
        if c.kind not in {KIND_THING, KIND_NAME}:
            continue
        span = doc[c.start:c.end]
        root = span.root
        # WHAT IT IS
        is_place = any(t.ent_type_ in STL.PLACE_ENT_LABELS for t in span)
        if not is_place and knowledge is not None:
            is_place = knowledge.kb_is_place(root.lemma_.lower())
        # HOW IT IS USED
        gov = root.head
        used_spatially = (gov.pos_ == "ADP" and gov.lower_ in spatial_preps)
        # the object of a preposition hanging off a verb of motion is where
        # that motion happened  ("go" -> travel.v.01)
        if not used_spatially and gov.pos_ == "ADP":
            governing = gov.head
            if governing.pos_ == "VERB" and _is_motion_verb(governing):
                used_spatially = True
        if used_spatially and not is_place:
            rating = (knowledge.kb_concreteness(root.lemma_.lower())
                      if knowledge else None)
            if rating is not None and rating < MIN_SETTING_CONCRETENESS:
                used_spatially = False
        c.setting_score = 1.0 if is_place else (0.5 if used_spatially else 0.0)
        c.is_setting = c.setting_score > 0.0
    return candidates


# =============================================================================
# SECTION 9 — STEP 11b: BUILDING THE ANSWER
# =============================================================================


def build_entry(ctx: Context,
                candidates: list[Candidate]) -> VisualisablesEntry:
    """Punch the candidates out of the line and number the holes.

    "The tractor and the cat, Molly, went down the lane."
        --> "The [1] and the [2], [3], went down the [4]."
        +   {1: tractor, 2: cat, 3: Molly, 4: lane}

    Substitute RIGHT TO LEFT, so a replacement cannot invalidate the offsets
    of the ones still to come.
    """
    ordered = order_candidates(candidates)
    vis = {n: _candidate_to_visualisable(c, ctx)
           for n, c in enumerate(ordered, 1)}

    template = ctx.line
    for n in sorted(vis, reverse=True):
        a, b = vis[n].char_span
        if a < b:
            template = template[:a] + f"[{n}]" + template[b:]

    return VisualisablesEntry(line=ctx.line, template=template,
                              visualisables=vis)


def _candidate_to_visualisable(cand: Candidate, ctx: Context) -> Visualisable:
    """One Candidate -> one Visualisable. Token span becomes a character
    span; variant / action / location stay None for the document pass."""
    a, b = ctx.to_line_chars(cand.start, cand.end)
    surface = ctx.line[a:b]
    return Visualisable(
        visualisable=" ".join(surface.split()).strip(" ,.;:!?"),
        surface=surface,
        kind=cand.kind,
        detector=cand.detector,
        char_span=(a, b),
        concreteness=cand.concreteness,
        is_setting=cand.is_setting,
        setting_score=cand.setting_score,
        confidence=cand.confidence,
        token_span=(cand.start, cand.end),
    )


# =============================================================================
# SECTION 10 — THE DOCUMENT PASS: variant / action / location
#
# Everything above answers "what is in THIS line". The other three fields
# cannot be answered there:
#   action    is in this line, but needs the parse, not the text
#   location  needs the setting, which is often in an EARLIER line
#             ("In Egypt," ... "there's a valley")
#   variant   needs the whole history of the thing — the tractor is only
#             "the one with yellow paint on it" because of a segment 3 back
# =============================================================================


# Which coreference to run. Measured on the tractor narrative, CPU:
#   "off"   recency guessing only   ~0 s    ~50-55% right
#   "fast"  lingmess alone           4.9 s  81.4 CoNLL-F1
#   "full"  all four models         51.0 s  83.6 F1
# "fast" is the default: the ensemble's extra ~2 F1 is not worth 10x the time.
COREF_DEFAULT = "fast"

# Verbs that CHANGE how the thing they act on LOOKS, so it needs a new
# variant afterwards. WordNet's own hypernyms — no verb list is written here.
#   e.g. break / shatter / melt / rust / collapse / burn / crack -> yes
#        rev / pour / crash / go / see / pass / like             -> no
#
# NOT change.v.01. That is the CAUSATIVE "cause to change, make different",
# and it is far too wide: it files "rev" and "fill" as changes, which is
# where variants like "revved" came from. change.v.02 is "UNDERGO a change",
# which is the one that means the picture is now different.
CHANGE_VERB_ROOTS = frozenset({"change.v.02", "destroy.v.02", "break.v.02"})

# Verbs of MOTION, so the place they moved to/through is a setting.
#   e.g. go / walk / sail / pour -> yes      sit / stand / contain -> no
MOTION_VERB_ROOTS = frozenset({"travel.v.01", "move.v.02", "move.v.03"})


def resolve_visualisable_details(entries: list[VisualisablesEntry],
                                 doc,
                                 coref: str = COREF_DEFAULT,
                                 abstract_terms: dict | None = None
                                 ) -> list[VisualisablesEntry]:
    """Fill variant / action / location across a WHOLE script.

    @input entries = every line's VisualisablesEntry, IN SCRIPT ORDER. Build
        them with keep_pronouns=True if you want "it"/"they" resolved.
    @input doc = the ONE parsed Doc the entries were built against.
    @input coref = "fast" | "full" | "off"  — see COREF_DEFAULT. IGNORED
        when abstract_terms is given: the answers are already in the map.
    @input abstract_terms = the whole script's pronouns, resolved ONCE up
        front by _abstract_term_resolver.resolve_all_abstract_terms(). When
        it is given, step 3 LOOKS THE ANSWER UP and runs no model at all.
        e.g. {(395, 397): {"surface": "it", "resolved": "valley",
                           "confidence": 0.26, "source": "models", ...}}
    @output the same entries, mutated in place.

    THE STEPS
        1  action    what each thing is doing, from the dependency parse
        2  identity  collapse mentions, so one thing has one history
        3  coref     turn "it"/"they" into the thing they point at
        4  location  the line's setting, carried forward
        5  variant   what each thing looks like by now, described
    """
    entries = fill_actions(entries, doc)
    entries = assign_identities(entries)
    entries = resolve_references(entries, doc, coref, abstract_terms)
    entries = fill_locations(entries, doc)
    entries = fill_variants(entries, doc)
    return entries


# -----------------------------------------------------------------------------
# step 1 — action
# -----------------------------------------------------------------------------

def fill_actions(entries: list[VisualisablesEntry], doc) -> list[VisualisablesEntry]:
    """`action` = the verb this thing is the subject or object of.
    e.g. "The tractor ploughed the field" --> tractor.action = "ploughed"
         "The bee flew away"              --> bee.action     = "flew away"

    A weak verb never becomes an action: the picture in "the cat WAS on the
    mat" is the mat, not the being. A KIND_ACTION slot is skipped — it does
    not HAVE an action, it IS one.
    """
    for entry in entries:
        for vis in entry.visualisables.values():
            if vis.token_span is None:
                continue
            if vis.kind in {KIND_ACTION, KIND_FALLBACK}:
                continue
            verb = _governing_verb(doc, *vis.token_span)
            if verb is None:
                continue
            if (verb.lemma_.lower() in STL.WEAK_VERB_LEMMAS
                    or verb.text.lower() in STL.WEAK_VERB_FORMS):
                continue
            vis.action = _verb_phrase(doc, verb)
    return entries


def _governing_verb(doc, start: int, end: int):
    """The verb this span hangs off, or None. At most three steps up the
    tree: a noun's head is usually its verb directly (nsubj/dobj) or one
    preposition away ("sailed FOR the islands"). Further than that and the
    verb is in another clause."""
    node = doc[start:end].root
    for _ in range(3):
        head = node.head
        # Compare .i, NEVER `is` — spaCy builds a fresh Token proxy on every
        # attribute access, so even `tok.head is tok` at the ROOT is False.
        if head.i == node.i:
            return None
        if head.pos_ in {"VERB", "AUX"}:
            return head if head.pos_ == "VERB" else None
        node = head
    return None


def _verb_phrase(doc, verb) -> str:
    """The verb plus its particle — "flew away", "dried up"."""
    end = verb.i + 1
    for child in verb.children:
        if child.dep_ in STL.PARTICLE_DEPS:
            end = max(end, child.i + 1)
    return doc[verb.i:end].text


# -----------------------------------------------------------------------------
# step 2 — identity
# -----------------------------------------------------------------------------

def assign_identities(entries: list[VisualisablesEntry]) -> list[VisualisablesEntry]:
    """Collapse the many ways a script names one thing into ONE label.
    e.g. "The tractor" (line 1), "the tractor" (line 5), "tractor" -> "tractor"

    Without this, step 5 gives each of them its own variant history and the
    yellow paint never sticks.

    Reuses abstract_term_resolver.build_canonical_map(), which already groups
    names by containment and elects the longest form. Falls back to the
    lowercased name if that module will not import.
    """
    named = [v for e in entries for v in e.visualisables.values()
             if v.kind in CONCRETE_KINDS]
    if not named:
        return entries
    resolver = _resolver()
    if resolver is None:
        for vis in named:
            vis.identity = vis.visualisable.lower()
        return entries
    mapping = resolver.build_canonical_map([v.visualisable for v in named])
    for vis in named:
        key = resolver.canonical_key(vis.visualisable)
        vis.identity = (mapping.get(key) or vis.visualisable).lower()
    return entries


_RESOLVER = None      # abstract_term_resolver: None=untried, False=absent


def _resolver():
    """abstract_term_resolver, or None. Lazy, because importing it pulls in
    torch and patches transformers — a caller that only wants per-line
    extraction should never pay that."""
    global _RESOLVER
    if _RESOLVER is None:
        try:
            import _abstract_term_resolver as _atr
            _RESOLVER = _atr
        except Exception as exc:
            _RESOLVER = False
            print(f"[visualisables] note: abstract_term_resolver unavailable "
                  f"({exc}) — identities fall back to the bare name and "
                  f"pronouns to recency.")
    return _RESOLVER or None


# -----------------------------------------------------------------------------
# step 3 — coreference
# -----------------------------------------------------------------------------

def resolve_references(entries: list[VisualisablesEntry], doc,
                       mode: str = COREF_DEFAULT,
                       abstract_terms: dict | None = None
                       ) -> list[VisualisablesEntry]:
    """Turn each KIND_REFERENCE slot into the thing it points at.
    e.g. "They passed a bee"  -->  [1] = "the tractor and the cat"
         "It then crashed"    -->  [1] = "the tractor"

    Ask abstract_term_resolver's models, highest-weighted first, for the
    cluster that IS this pronoun and then the best antecedent in it — its own
    find_cluster() / pick_antecedent() / mention_name(), not reimplementations.

    No model answers (or mode="off")? Fall back to RECENCY, stamped with that
    module's own FALLBACK_CONFIDENCE (0.10), because a recency guess is only
    ~50-55% right and downstream should know.

    Nothing at all? Keep the pronoun and set confidence 0 — the renderer's
    cue to hold whatever is already on screen, which is the right answer for
    an unresolvable "it" anyway.

    THE MAP PATH — PREFER IT. Hand over abstract_terms and every answer is
    already worked out: the caller resolved the WHOLE script once, with all
    four models voting, before any of this ran. Then this is a dict lookup.
        faster    one ~40 s pass per script, instead of ~5 s per segment
                  that happens to contain a pronoun
        better    a weighted vote of four models with a real confidence,
                  instead of the first model that answered at a flat 0.75
        more      the map also carries the deictics ("I" --> the narrator)
                  and the possessives ("Its" --> "the tractor's"), neither
                  of which the per-segment path can see

    The model path below stays for callers who never built a map. Only one
    of the two ever runs.
    """
    refs = [(i, v) for i, e in enumerate(entries)
            for v in e.visualisables.values() if v.kind == KIND_REFERENCE]

    if abstract_terms is not None:
        _references_from_map(entries, doc, refs, abstract_terms)
        _apply_possessives(entries, doc, abstract_terms)
        return entries

    if not refs:
        return entries

    clusters = _coref_clusters(doc, mode)
    resolver = _resolver()
    for entry_index, vis in refs:
        token = doc[vis.token_span[0]] if vis.token_span else None
        answer = None
        if token is not None and clusters and resolver is not None:
            answer = _model_referent(resolver, doc, token, clusters)
        if answer is not None:
            vis.visualisable, vis.confidence = answer, 0.75
            vis.detector += "+coref"
        else:
            guess = _recency_referent(entries, entry_index, token)
            if guess is None:
                vis.confidence = 0.0
                vis.detector += "+unresolved"
                continue
            vis.visualisable = guess
            vis.confidence = 0.10
            vis.detector += "+recency"
        vis.identity = vis.visualisable.lower()
    return entries


# Below this, an answer is a guess and we do NOT swap it in: the slot keeps
# its pronoun, which is the renderer's cue to hold the picture it already has.
# A wrong picture is worse than the last right one. This is
# abstract_term_resolver.CONFIDENCE_THRESHOLD — repeated here only as the
# fallback for when that module will not import.
ABSTRACT_CONFIDENCE_THRESHOLD = 0.25


def _abstract_threshold() -> float:
    """The resolver's own threshold, so the two files cannot disagree."""
    return getattr(_resolver(), "CONFIDENCE_THRESHOLD",
                   ABSTRACT_CONFIDENCE_THRESHOLD)


def _references_from_map(entries: list[VisualisablesEntry], doc, refs,
                         abstract_terms: dict) -> None:
    """Fill every KIND_REFERENCE slot from the pre-built map. No model runs.

    e.g. "[1] was once covered"   +  {(395, 397): {"resolved": "valley", ...}}
             -->  [1] = "valley", confidence 0.26, detector "+abstract:models"
    """
    threshold = _abstract_threshold()
    for _entry_index, vis in refs:
        token = doc[vis.token_span[0]] if vis.token_span else None
        row = _abstract_row(abstract_terms, token) if token is not None else None

        if row is None or not row["resolved"]:
            # Four models had a go and none of them could say. Keep the
            # pronoun; hold the picture.
            vis.confidence = 0.0
            vis.detector += "+abstract:none"
            continue

        if row["confidence"] < threshold:
            # A guess, usually the recency fallback at 0.10. Same answer:
            # keep the pronoun rather than put the wrong thing on screen.
            vis.confidence = row["confidence"]
            vis.detector += f"+abstract:{row['source']}-low"
            continue

        vis.visualisable = row["resolved"]
        vis.confidence = row["confidence"]
        vis.detector += "+abstract:" + row["source"]
        if row["source"] == "deictic":
            # "I" / "we" / "you" — the narrator or the viewer. A person, but
            # not one there is any footage of, so it stops being a thing to
            # film and becomes a note for whatever draws the shot.
            vis.kind = KIND_DEICTIC
        vis.identity = vis.visualisable.lower()


def _apply_possessives(entries: list[VisualisablesEntry], doc,
                       abstract_terms: dict) -> None:
    """ "Its windscreen broke"  -->  the slot reads "the tractor's windscreen".

    A possessive pronoun never gets a slot of its own — it sits INSIDE a noun
    phrase, as the head noun's determiner — so resolving one is not swapping
    a slot but RENAMING it.
        e.g. "Its windscreen"  -->  "the tractor's windscreen"
             "their sails"     -->  "the ships' sails"

    It also records the whole on the part (vis.owner), which is the missing
    half of the KNOWN MISS in _variant_descriptions(): once we know whose
    windscreen it is, fill_variants() can give the TRACTOR a broken one.
    """
    threshold = _abstract_threshold()
    for entry in entries:
        for vis in entry.visualisables.values():
            if vis.token_span is None or not vis.is_concrete():
                continue
            lo, hi = vis.token_span
            poss = next((t for t in doc[lo:hi] if t.tag_ == "PRP$"), None)
            if poss is None:
                continue
            row = _abstract_row(abstract_terms, poss)
            if (row is None or not row["resolved"]
                    or row["confidence"] < threshold):
                continue
            before, after = doc[lo:poss.i].text, doc[poss.i + 1:hi].text
            owner = row["resolved"]
            vis.visualisable = " ".join(
                part for part in (before, _possessive_form(owner), after) if part)
            vis.identity = vis.visualisable.lower()
            vis.owner = _owner_identity(entries, owner)
            vis.detector += "+abstract:poss"


def _abstract_row(abstract_terms: dict, token):
    """The map's row for this pronoun, or None if it has none.

    The map is keyed by (start_char, end_char) into the text it was built
    over, and join_segments() is what makes that the same text this doc was
    parsed from — so the exact hit is the normal case. The overlap scan is
    the safety net for the one place the two can disagree: the map may have
    been built off a different spaCy model, and "it's" is one token to one
    tokeniser and two to another.
    """
    start, end = token.idx, token.idx + len(token.text)
    row = abstract_terms.get((start, end))
    if row is not None:
        return row
    for (a, b), row in abstract_terms.items():
        if a < end and start < b:
            return row
    return None


def _possessive_form(name: str) -> str:
    """ "the tractor" --> "the tractor's",  "Locals" --> "Locals'".
    The resolver's own rule, so the two files cannot drift apart."""
    resolver = _resolver()
    if resolver is not None:
        return resolver.possessive_form(name)
    return name + ("'" if name.endswith("s") else "'s")


def _owner_identity(entries: list[VisualisablesEntry], name: str) -> str:
    """The resolver calls the whole "the tractor"; the rest of the script
    already calls it "tractor". Match them up, or the variant would land on
    an identity nothing else shares.

    Containment, which is how assign_identities() groups names anyway.
    """
    want = name.lower()
    for entry in entries:
        for vis in entry.visualisables.values():
            if not vis.identity:
                continue
            if vis.identity == want or vis.identity in want or want in vis.identity:
                return vis.identity
    return want


def _coref_clusters(doc, mode: str):
    """[(weight, clusters), ...] from the coreference models, strongest
    first. Empty when mode is "off" or nothing will run — the caller then
    uses recency, so this never raises."""
    if mode == "off":
        return []
    resolver = _resolver()
    if resolver is None:
        return []
    wanted = ([m for m in resolver.MODELS if m[0] == "lingmess"]
              if mode == "fast" else list(resolver.MODELS))
    out = []
    for label, kind, checkpoint, weight in wanted:
        try:
            out.append((weight, resolver.run_model(kind, checkpoint, doc.text)))
        except Exception as exc:
            print(f"[visualisables] coref model {label} skipped: "
                  f"{type(exc).__name__}: {exc}")
    return sorted(out, key=lambda pair: -pair[0])


def _model_referent(resolver, doc, token, weighted_clusters):
    """The best antecedent any model offers for this pronoun, or None."""
    start, end = token.idx, token.idx + len(token.text)
    for _weight, clusters in weighted_clusters:
        span, cluster = resolver.find_cluster(clusters, start, end)
        if cluster is None:
            continue
        ante = resolver.pick_antecedent(doc, cluster, span, start)
        # pick_antecedent only rejects PRONOUN mentions, so a cluster can hand
        # back a VERB and "the cat did not like it" resolves to "revved". A
        # thing on screen is a NOUN; anything else, try the next model.
        if ante is not None and ante.root.pos_ in {"NOUN", "PROPN", "NUM"}:
            return resolver.mention_name(doc, ante)
    return None


def _recency_referent(entries, entry_index: int, token):
    """The last concrete thing mentioned before this line.

    Number-aware: "they" takes EVERY concrete thing from the most recent line
    that had more than one ("The tractor and the cat ... They" -> both);
    "it" takes the single most recent thing.
    """
    plural = bool(token is not None and "Plur" in token.morph.get("Number"))
    for earlier in range(entry_index - 1, -1, -1):
        things = [v.visualisable for _, v in
                  sorted(entries[earlier].visualisables.items())
                  if v.kind in CONCRETE_KINDS]
        if not things:
            continue
        if plural:
            return " and ".join(things) if len(things) > 1 else things[-1]
        return things[-1]
    return None


# -----------------------------------------------------------------------------
# step 4 — location
# -----------------------------------------------------------------------------

def fill_locations(entries: list[VisualisablesEntry], doc) -> list[VisualisablesEntry]:
    """`location` = the setting this thing is standing in.

    Three rules, in order:
      1. A setting belongs to its whole SENTENCE, not just its own segment.
         "The tractor and the cat went down the lane" is cut into four
         segments with the setting in the LAST one, so a forward
         segment-by-segment carry would leave the tractor with no location.
      2. A sentence with no setting of its own inherits the last one seen.
         e.g. "In Egypt," ... then six lines about the valley — all still in
         Egypt.
      3. A setting is not in itself — the lane's own location stays whatever
         was current before it.

    Several settings in one sentence? Highest setting_score wins, so a real
    place (1.0) beats a merely prepositional one (0.5). Ties go LEFTMOST.
    """
    # ---- which sentence is each line in? --------------------------------
    sent_key, best_in_sent = {}, {}
    for index, entry in enumerate(entries):
        spans = [v.token_span for v in entry.visualisables.values()
                 if v.token_span]
        if not spans:
            sent_key[index] = None
            continue
        key = doc[min(a for a, _ in spans)].sent.start
        sent_key[index] = key
        here = [v for v in entry.visualisables.values() if v.setting_score > 0]
        if not here:
            continue
        best = max(here, key=lambda v: (v.setting_score,
                                        -(v.token_span or (0, 0))[0]))
        prev = best_in_sent.get(key)
        if prev is None or best.setting_score > prev.setting_score:
            best_in_sent[key] = best

    # ---- walk the script, carrying the last setting forward -------------
    current = None
    for index, entry in enumerate(entries):
        key = sent_key[index]
        chosen = best_in_sent.get(key) if key is not None else None
        if chosen is not None:
            current = chosen.visualisable
        for vis in entry.visualisables.values():
            if chosen is not None and vis is chosen:
                continue          # a setting is not in itself
            vis.location = current
    return entries


# -----------------------------------------------------------------------------
# step 5 — variant
# -----------------------------------------------------------------------------

def fill_variants(entries: list[VisualisablesEntry], doc) -> list[VisualisablesEntry]:
    """`variant` = extra description of how this thing looks NOW.

    NOT a version number — the words you would ADD to the plain name when
    searching for it or drawing it:

        "yellow paint splat, broken window"
        "really big"
        "fast"

    None means the plain, unmodified thing (the base version).

    It ACCUMULATES per identity, in script order, because the tractor keeps
    its yellow paint for the rest of the video:

        "The tractor went down the lane."     -->  None
        "She poured yellow paint onto it."    -->  "yellow paint"
        "Its windscreen broke."               -->  "yellow paint, broken"

    Stamped with the state AS OF that segment, this segment's own changes
    included — the shot shows the result.
    """
    history: dict[str, list[str]] = {}
    for entry in entries:
        for _n, vis in sorted(entry.visualisables.items()):
            if not vis.identity or vis.kind in {KIND_ACTION, KIND_QUALITY,
                                                KIND_FALLBACK}:
                continue
            so_far = history.setdefault(vis.identity, [])
            for description in _variant_descriptions(doc, vis):
                if description not in so_far:
                    so_far.append(description)
                # ...and a PART's change describes its WHOLE: "Its windscreen
                # broke" leaves the TRACTOR with a broken windscreen. Only
                # possible once a possessive resolved to an owner, which is
                # the abstract-terms map's doing — the KNOWN MISS below.
                if vis.owner:
                    whole = history.setdefault(vis.owner, [])
                    phrase = f"{description} {_head_word(doc, vis)}"
                    if phrase not in whole:
                        whole.append(phrase)
            vis.variant = ", ".join(so_far) or None
    return entries


def _variant_descriptions(doc, vis) -> list[str]:
    """What this segment says about how the thing LOOKS. Three sources, all
    read off the parse — no word list is written here:

      a) A QUALITY PREDICATED OF IT.
         e.g. "the tractor was really loud"  --> "really loud"
              "the sky turned red"           --> "red"
         An adjective INSIDE the phrase is already part of `visualisable`
         ("yellow paint"), so it is not repeated here.

      b) IT IS THE PATIENT OF A CHANGE-OF-STATE VERB, written as the PAST
         PARTICIPLE, because a variant is a description and not an event.
         e.g. "Its windscreen broke"  --> "broken"
              "the ship sank"         --> "sunken"
         WordNet says which verbs those are (CHANGE_VERB_ROOTS). Only a
         DIRECT patient counts — object, passive subject, or the subject of
         an intransitive, which is where a narration script usually puts the
         thing that got damaged. The object of a preposition does NOT:
         "a valley filled with whale skeletons" must not describe the
         skeletons as "filled".

      c) SOMETHING WAS PUT ONTO IT (caused motion) — it acquires that thing.
         e.g. "she poured yellow paint onto the tractor" --> "yellow paint"
         The tractor is not the object of "poured" (the paint is), so (b)
         cannot see it. The verb must be one of MOTION, or locative
         inversion walks in: "In the Sahara SITS a valley" has the same
         shape, and the Sahara does not thereby acquire a valley.

    KNOWN MISSES, stated rather than hacked around:
      * "It then CRASHED INTO the lamppost" — WordNet files crash.v.01 under
        travel.v.01, and widening the sense window lets far too much else in.
      * "ITS windscreen broke" describes the tractor too, not just the
        windscreen. Part-to-whole needs reliable meronymy, which WordNet
        does not give — so the tractor's variant stops at "yellow paint".
        FIXED for the possessive case only, and only when an abstract-terms
        map is in play: "Its" resolves to the tractor, so the windscreen
        knows whose it is (vis.owner) and fill_variants() hands the tractor
        "broken windscreen". A part named any other way ("the windscreen
        broke") still needs the meronymy we have not got.
    """
    if vis.token_span is None:
        return []
    root = doc[vis.token_span[0]:vis.token_span[1]].root
    out: list[str] = []

    # (a) a quality predicated of it. Asks the HEAD directly rather than
    #     _governing_verb, so the copula counts too ("the tractor WAS loud").
    if root.dep_ in {"nsubj", "nsubjpass"}:
        for sibling in root.head.children:
            if (sibling.dep_ in {"acomp", "oprd"} and sibling.pos_ == "ADJ"
                    and sibling.lemma_.lower() not in STL.WEAK_ADJ_LEMMAS):
                out.append(_adjective_phrase(doc, sibling))

    verb = _governing_verb(doc, *vis.token_span)
    if verb is None:
        return out

    # (b) patient of a change-of-state verb
    has_object = any(ch.dep_ in {"dobj", "obj"} for ch in verb.children)
    is_patient = (root.dep_ in {"dobj", "obj", "nsubjpass"}
                  or (root.dep_ == "nsubj" and not has_object))
    if is_patient and _is_change_verb(verb):
        out.append(_past_participle(verb))

    # (c) caused motion: something was PUT onto it
    spatial = STL.SPATIAL_LOCATIVE_PREPS | STL.SPATIAL_DIRECTIONAL_PREPS
    if (root.dep_ == "pobj" and root.head.lower_ in spatial
            and root.head.head.i == verb.i and _is_motion_verb(verb)):
        for child in verb.children:
            if child.dep_ not in {"dobj", "obj"}:
                continue
            lo = min([child.i] + [g.i for g in child.children
                                  if g.dep_ in {"amod", "compound"}])
            out.append(doc[lo:child.i + 1].text)
    return out


def _head_word(doc, vis) -> str:
    """The one word a phrase is ABOUT.  e.g. "the tractor's windscreen"
    --> "windscreen". Used to describe the whole by its part."""
    return doc[vis.token_span[0]:vis.token_span[1]].root.text.lower()


def _adjective_phrase(doc, adj) -> str:
    """The adjective plus its adverbs — "really big", not "big"."""
    start = min([adj.i] + [c.i for c in adj.children if c.dep_ == "advmod"])
    return doc[start:adj.i + 1].text


_IRREGULAR_PARTICIPLES: dict | None = None


def _irregular_participles() -> dict:
    """{lemma: past participle} for the irregular verbs, built from WordNet's
    OWN exception list — so no verb forms are written out here.

    e.g. break -> broken   freeze -> frozen   tear -> torn
         melt  -> molten   sink   -> sunken   bend -> bent

    WordNet lists every irregular form of a lemma without saying which is
    which, so: skip the "-ing" ones, prefer a form ending in -n (the
    participle, for the strong verbs), else take the single form it offers.
    Empty when WordNet is not installed — then everything is regular.
    """
    global _IRREGULAR_PARTICIPLES
    if _IRREGULAR_PARTICIPLES is None:
        out: dict[str, str] = {}
        knowledge = kb()
        wordnet = knowledge._wn() if knowledge else None
        if wordnet:
            for form, lemmas in wordnet._exception_map.get("v", {}).items():
                if form.endswith("ing"):
                    continue
                for lemma in lemmas:
                    best = out.get(lemma)
                    if best is None or (form.endswith("n")
                                        and not best.endswith("n")):
                        out[lemma] = form
        _IRREGULAR_PARTICIPLES = out
    return _IRREGULAR_PARTICIPLES


def _past_participle(verb) -> str:
    """The verb as a DESCRIPTION rather than an event.
    e.g. "broke" --> "broken"   "sank" --> "sunken"   "rusted" --> "rusted"

    1. the word as written, when it already IS a past participle ("covered")
    2. WordNet's irregular form for the lemma  (_irregular_participles)
    3. otherwise regular -ed, with the three English spelling rules
       e.g. collapse -> collapsed   dry -> dried   rip -> ripped
    """
    if verb.tag_ == "VBN":
        return verb.text.lower()
    lemma = verb.lemma_.lower()
    irregular = _irregular_participles().get(lemma)
    if irregular:
        return irregular
    if lemma.endswith("e"):
        return lemma + "d"
    if len(lemma) > 2 and lemma.endswith("y") and lemma[-2] not in "aeiou":
        return lemma[:-1] + "ied"
    if (len(lemma) > 2 and lemma[-1] not in "aeiouwxy"
            and lemma[-2] in "aeiou" and lemma[-3] not in "aeiou"):
        return lemma + lemma[-1] + "ed"
    return lemma + "ed"


def _is_motion_verb(verb) -> bool:
    """WordNet: is this a verb of motion? False when WordNet is absent,
    which just means classify_settings() falls back to its prep lists."""
    knowledge = kb()
    if knowledge is None:
        return False
    return knowledge._wn_has_hypernym(verb.lemma_.lower(),
                                      MOTION_VERB_ROOTS, 1, pos="v")


def _is_change_verb(verb) -> bool:
    """WordNet: does this verb change how what it acts on LOOKS?

    TWO senses here, where everything else in this file takes one, because
    WordNet's FIRST sense of "break" is "interrupt" — a one-sense window
    loses "Its windscreen broke", which is the whole point. Measured at two:
    break / sink / melt / rust / collapse / burn / shatter / crack / freeze /
    bend all pass; rev / pour / crash / go / see / pass / like all fail.
    Three lets "see" in.
    """
    knowledge = kb()
    if knowledge is None:
        return False
    return knowledge._wn_has_hypernym(verb.lemma_.lower(),
                                      CHANGE_VERB_ROOTS, 2, pos="v")
