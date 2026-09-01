"""
visualisables_extractor.py — find every VISUALISABLE in one split line.

    entry = create_visualisables_entry(
                "The tractor and the cat, Molly, went down the lane.",
                "They passed a bee.",       # rest of line + next sentence
                None)                       # everything before it

    entry.template  ->  "The [1] and the [2], Molly, went down the [3]."
    entry.as_map()  ->  {"The [1] and the [2], ... the [3].": {
                             "1": {"visualisable": "tractor", "variant": None,
                                   "action": None, "location": None},
                             "2": {"visualisable": "cat, Molly", ...},
                             "3": {"visualisable": "lane", ...}}}

WHAT A VISUALISABLE IS
    Defined in ../VISUALISABLES_NLP_DEFINITION.txt. That document is the
    spec; this file is its implementation. Every detector and every filter
    below names the PART/bullet of the spec it implements, so the two can
    be diffed by eye.

    PART 1 (things that ARE)   -> SECTION 6, the harvesters.
    PART 2 (things that ARE NOT) -> SECTION 7, the filters.
    PART 3 (the grey area: abstract nouns) -> SECTION 7, drop_abstract().
    PART 4 (rules of thumb)    -> the length fallback, SECTION 8.

NO NEW LINGUISTICS ARE INVENTED HERE
    Same house rule as abstract_term_resolver.py: every judgement is
    delegated to something already built, published and benchmarked. This
    file is plumbing between them.

      * spaCy (en_core_web_sm/trf) — POS, dependencies, NER, noun_chunks.
        Reached through shared_text_logic.get_nlp_required() so the whole
        process shares ONE loaded model.
      * shared_text_logic.py — the splitter's own word lists and checks
        (WEAK_VERB_LEMMAS, WEAK_ADJ_LEMMAS, GENERIC_TOPIC_NOUNS,
        IDIOM_PHRASES, SFX_WORDS, MEASURE_NOUNS, NAME_CONNECTORS,
        has_visualisable_content, find_idiom_spans, ...). Reusing these
        means the extractor and the splitter can never disagree about what
        a weak verb is.
      * Brysbaert, Warriner & Kuperman (2014) concreteness ratings —
        ~37k English words, human-rated 1 (abstract) .. 5 (concrete). The
        standard psycholinguistic answer to "can you photograph it".
        Already vendored in ../recommender_data.json (built by
        ../build_wordlists.py) and exposed as VISUAL_RECOMMENDER
        .kb_concreteness(). This is what finally closes PART 3.
      * WordNet (via NLTK), through the same VISUAL_RECOMMENDER kb_*
        layer — kb_is_person / kb_is_place / kb_is_time / kb_is_unit /
        kb_is_collective. Used to CLASSIFY a visualisable (is it a
        setting? a person?) and as the out-of-vocabulary fallback when
        Brysbaert has never seen the word.
      * abstract_term_resolver.py — the 4-model coreference ensemble.
        NOT used in this phase (we only fill "visualisable"); the hook is
        marked [PHASE 4] where it will go.

PHASES (see ../../TODO.md)
    phase 1  this file: the shape of the thing — functions + docstrings +
             an orchestration you can read top to bottom.        <- DONE
    phase 2  research the open questions marked  RESEARCH(phase-2)  and
             collected in the APPENDIX at the bottom.
    phase 3  fill in the bodies.                                 <- DONE
             All six self-test cases pass; run this file to see them.
    phase 4  variant / action / location — the other three fields.  <- DONE
             SECTION 12: a second, DOCUMENT-level pass. The per-line
             function cannot answer these, because a thing's location and
             its variant both depend on lines other than its own:

                 entries = [create_visualisables_entry(..., doc=doc,
                                line_token_span=span, keep_pronouns=True)
                            for each line]
                 entries = resolve_visualisable_details(entries, doc)

             Run this file to see both phases:
                 uv run visualisables_extractor.py              (phase 3)
                 uv run visualisables_extractor.py --phase4     (phase 4,
                                        recency; add --coref fast for the
                                        real thing, ~5 s of model time)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

# The rest of the pipeline lives one directory up.
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 0 — WIRING TO THE TOOLS WE ARE REUSING                    ### #
# ###                                                                     ### #
# ###   Nothing here decides anything. It only makes the existing         ### #
# ###   resources reachable, and does so LAZILY: importing this module    ### #
# ###   must stay cheap, because the caller (myownstuff.py) imports it    ### #
# ###   long before it has any text to work on.                           ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================

# shared_text_logic is safe to import eagerly: its spaCy load is lazy and
# its module body is just word lists.
import shared_text_logic as STL          # noqa: E402


_KB = None      # VISUAL_RECOMMENDER handle: None = untried, False = absent

# One-entry memo for STL.find_idiom_spans(). It scans the WHOLE doc for all
# 27 idioms, and we call it once per LINE over the same doc — which is
# O(lines x doc x idioms), i.e. quadratic in script length, and measured at
# ~70% of the warm per-line cost before this. The answer only depends on the
# doc, so compute it once. Keyed on id() but ALSO holding the doc, so the
# entry cannot be aliased onto a different doc that reuses the address.
_IDIOM_MEMO: tuple = (None, None, None)   # (id(doc), doc, spans)


def kb():
    """The knowledge-base layer of VISUAL_RECOMMENDER.py, or None.

    Imported lazily because that module reads recommender_data.json
    (~740 KB: the Brysbaert table, the country list) at import time, and a
    caller that only wants the dataclasses shouldn't pay for it.

    Returns the module, or None when it (or its data file) is missing — in
    which case every kb_-backed check must degrade to the spaCy-only
    behaviour, never crash. Same contract as STL.get_nlp().
    """
    global _KB
    if _KB is None:
        try:
            import VISUAL_RECOMMENDER as _vr
            _vr.kb_concreteness("test")          # force the data file to load
            _KB = _vr
        except Exception as exc:                 # pragma: no cover
            _KB = False
            print(f"[visualisables] note: VISUAL_RECOMMENDER unavailable "
                  f"({exc}) — concreteness + WordNet checks are OFF, so "
                  f"abstract nouns will NOT be filtered.")
    return _KB or None


def nlp():
    """The one shared spaCy model. Thin alias for STL.get_nlp_required().

    Required, not optional: without a parse there is no POS, no NER and no
    noun_chunks, and every harvester in SECTION 6 is dead. The splitter
    already refuses to run without it, so by the time we are called the
    model is loaded and warm.
    """
    return STL.get_nlp_required()


# -----------------------------------------------------------------------------
# THRESHOLDS  — all of them are guesses until phase 2 calibrates them.
# -----------------------------------------------------------------------------

# Brysbaert scale is 1.0 (abstract) .. 5.0 (concrete). 3.0 is the scale's own
# midpoint. The recommender's tests already assert jar >= 4.5 and
# melancholy <= 2.5, so the true boundary is somewhere in between.
#   SETTLED IN PHASE 2 (grid search, 0.1 steps, over the spec's own lists):
#     concrete nouns  min 4.26 (field)      abstract nouns  max 2.69 (monopoly)
#     -> EVERY threshold in 2.7 .. 4.2 scores a perfect 19/19 and 10/10.
#   Re-run over the three real scripts (55 noun-phrase heads) to break the
#   tie, i.e. what each threshold actually costs us in production text:
#     3.0 drops 2 heads   history 2.96 (already blocked as an idiom),
#                         resource 2.55 (genuinely abstract — correct)
#     3.5 drops 8 heads   and starts eating district 3.29, decade 3.19,
#                         place 3.48 — district in particular is filmable.
#   So 3.0 it is: it is inside the perfect band AND it is the value that
#   costs nothing on real text. Widen only with new counter-examples.
MIN_CONCRETENESS = 3.0

# Which kinds drop_abstract is even allowed to look at. PHASE 2 FOUND THIS
# THE HARD WAY: the concreteness scale is calibrated on NOUNS, and applying
# it to anything else is wrong. Measured, against the spec's own KEEP lists:
#     strong ADJECTIVES the spec says are visual: ancient 2.04, brilliant
#       2.07, enormous 2.90, tiny 3.11 — a 3.0 cut kills three of nine.
#     bare SFX words:   whoosh 2.64, thud 3.20 — kills whoosh.
#     verbs:            strong verbs bottom out at 3.57 but weak verbs
#       reach 3.68 ("hold"), so the two OVERLAP and no cut separates them.
# Nouns are the only class where the scale separates cleanly, so nouns are
# the only class it may judge. Everything else is settled by POS + the
# splitter's own weak-word lists, which already do it correctly.
CONCRETENESS_APPLIES_TO = {"thing"}          # i.e. KIND_THING only

# [PHASE 4] A setting found only by its PREPOSITION (the weak, 0.5 signal in
# classify_settings) must also be concrete enough to be somewhere you could
# stand. Without this, any prepositional object of a motion verb qualifies,
# and "crashed into the lamppost BY ACCIDENT" makes "accident" the location.
# Measured on the words that actually came up:
#     real places   lamppost 4.66  lane 4.66  shelf 4.96  valley 4.72
#                   field 4.26                       -> all >= 4.26
#     not places    weight 3.94  accident 3.26  spice trade 3.08
#                   monopoly 2.69                    -> all <= 3.94
# 4.0 sits in that gap. Note this gates ONLY the weak signal: a GPE/LOC
# entity or a WordNet place is a setting whatever Brysbaert thinks of it,
# and an unrated word still passes (silence means keep, as everywhere else).
MIN_SETTING_CONCRETENESS = 4.0

# PART 1, last bullet: the mercy rule. A span with no visualisable in it is
# still allowed to hold the screen if it is long enough to be a real line.
LENGTH_FALLBACK_MIN_TOKENS = 4

# A noun phrase longer than this is a sentence, not a picture — trim it.
#   RESEARCH(phase-2): is there a published figure for "searchable stock
#   footage query length"? Otherwise this stays a hand-set number.
MAX_PHRASE_TOKENS = 6


# The kinds of visualisable, one per PART 1 bullet. Kept as plain strings so
# the emitted json stays readable and diffable.
KIND_NAME     = "name"       # PROPN / named entity          "Molly", "Rome"
KIND_THING    = "thing"      # concrete common noun phrase   "jar of nutmeg"
KIND_NUMBER   = "number"     # NUM / CARDINAL / MONEY / %    "900 ships"
KIND_DATE     = "date"       # DATE / TIME                   "the 1600s"
KIND_QUALITY  = "quality"    # strong ADJ                    "rusted"
KIND_ACTION   = "action"     # strong VERB                   "sailed"
KIND_SOUND    = "sound"      # SFX_WORDS                     "boom"
KIND_FALLBACK = "fallback"   # the mercy rule — no real picture
KIND_REFERENCE = "reference" # [PHASE 4] a pronoun standing in for a slot
                             # somewhere earlier: "They" -> tractor + cat.
                             # Not concrete: the PICTURE is its referent's.

# myownstuff.py's "Concrete Visualisable": a thing, not an action or a
# quality. These are the kinds that can own a shot on their own.
CONCRETE_KINDS = {KIND_NAME, KIND_THING, KIND_NUMBER, KIND_DATE}


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 1 — THE DATA STRUCTURES                                   ### #
# ###                                                                     ### #
# ###   Two public ones (Visualisable, VisualisablesEntry) and one        ### #
# ###   internal working record (Candidate) that never escapes.           ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================


@dataclass
class Visualisable:
    """ONE thing we could put on screen, plus everything we know about it.

    Phase 3 fills `visualisable` and the provenance fields only. `variant`,
    `action` and `location` stay None — None means "not worked out yet",
    NOT "there isn't one". Phase 4 fills them.

    Fields, in the order the TODO asks for them:
      visualisable  the thing itself, trimmed to what you would type into a
                    stock-footage search box: "jar of nutmeg", not "a jar
                    of nutmeg that sat on the shelf".
      variant       which version of it is on screen right now — the
                    tractor BEFORE or AFTER the yellow paint.      [PHASE 4]
      action        what it is doing in this line ("ploughing").   [PHASE 4]
      location      the setting it is in ("the lane").             [PHASE 4]

    Provenance — kept so a wrong answer can be traced to the detector that
    produced it, and so the emitted json is self-explaining:
      surface       the exact substring of the line it came from.
      kind          one of the KIND_* constants.
      detector      the harvester function that found it.
      char_span     (start, end) into the ORIGINAL line, for the template.
      concreteness  the Brysbaert rating, when the word has one.
      is_setting    True when this is a place the other visualisables sit
                    in ("the lane", "the Banda Islands") rather than a
                    thing to film. Phase 4 reads this to fill `location`.
      confidence    0..1. Detector agreement, not a probability.
    """
    visualisable: str
    surface: str
    kind: str
    detector: str
    char_span: tuple[int, int]
    concreteness: float | None = None
    is_setting: bool = False
    confidence: float = 1.0

    # ---- the three fields phase 4 fills ------------------------------------
    variant: str | None = None
    action: str | None = None
    location: str | None = None

    # ---- what phase 4 needs to fill them -----------------------------------
    # identity     the canonical name of this THING across the whole script,
    #              so "the tractor" (line 1), "it" (line 3) and "the tractor"
    #              (line 5) are one thing with one running variant history.
    # token_span   the doc-relative token span. char_span is line-relative
    #              and is what the template uses; the document pass needs
    #              the doc coordinates to walk the parse.
    # setting_score how strongly this reads as a SETTING: 1.0 when it is a
    #              place by entity label or WordNet, 0.5 when only the
    #              preposition says so, 0.0 otherwise. See classify_settings.
    identity: str | None = None
    token_span: tuple[int, int] | None = None
    setting_score: float = 0.0

    def is_concrete(self) -> bool:
        """myownstuff.py's "Concrete Visualisable" test: a thing, not an
        action or a quality. `kind in CONCRETE_KINDS`."""
        return self.kind in CONCRETE_KINDS

    def as_dict(self) -> dict:
        """The json row for this visualisable — the four TODO fields first,
        then the provenance. Ordered so a human reading the emitted file
        sees the answer before the workings."""
        return {
            # the four fields the TODO asks for, first
            "visualisable": self.visualisable,
            "variant": self.variant,
            "action": self.action,
            "location": self.location,
            # then the workings
            "kind": self.kind,
            "identity": self.identity,
            "surface": self.surface,
            "detector": self.detector,
            "char_span": list(self.char_span),
            "concreteness": self.concreteness,
            "is_setting": self.is_setting,
            "setting_score": self.setting_score,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class VisualisablesEntry:
    """The result for ONE split line: the line with its visualisables
    punched out into numbered slots, plus what fills each slot.

      line          "The tractor and the cat, Molly, went down the lane."
      template      "The [1] and the [2], Molly, went down the [3]."
      visualisables {1: Visualisable(tractor), 2: ..., 3: ...}

    Slots are numbered 1..n, left to right, by position in the line — so
    the number is stable and a later stage can talk about "[2]" without
    ambiguity.
    """
    line: str
    template: str
    visualisables: dict[int, Visualisable] = field(default_factory=dict)

    # ---- what the splitter told us about this line, kept for later stages --
    rule_ids: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # why we did what we did
    # ---- debug: the harvested candidates the filters removed, each with the
    # filter that removed it. The useful half when calibrating a threshold.
    dropped: list = field(default_factory=list)

    def as_map(self) -> dict:
        """The exact output shape the TODO asks for:

            {template: {"1": {...}, "2": {...}}}

        Slot numbers become STRING keys because this ends up in json, where
        integer keys do not survive a round trip.
        """
        return {self.template: {str(n): v.as_dict()
                                for n, v in sorted(self.visualisables.items())}}

    def concrete_only(self) -> dict[int, Visualisable]:
        """Just the slots that are things, not actions/qualities — what the
        renderer actually needs to fetch footage for."""
        return {n: v for n, v in self.visualisables.items() if v.is_concrete()}


@dataclass
class Candidate:
    """Internal only. A span the harvesters put forward, before the filters
    have had their say. Token indices point into the CONTEXT doc (which
    covers the preceding text too), never into the line.

    Kept separate from Visualisable so the pipeline stages can be pure
    list-in / list-out — every filter in SECTION 7 and 8 is
    `list[Candidate] -> list[Candidate]` and nothing else.
    """
    start: int              # token index into ctx.doc, inclusive
    end: int                # token index into ctx.doc, exclusive
    kind: str
    detector: str
    confidence: float = 1.0
    concreteness: float | None = None
    is_setting: bool = False
    setting_score: float = 0.0         # 1.0 = a place; 0.5 = only the prep
    dropped_by: str | None = None      # set instead of deleting, for --debug


@dataclass
class Context:
    """The parsed world around the target line.

    WHY THE CONTEXT EXISTS AT ALL. spaCy parses a fragment badly: "went
    down the lane" on its own gets no subject and often the wrong POS. So
    we parse `preceding + line + following` as ONE document and then slice
    out the tokens belonging to the line. This is exactly what the splitter
    does in _build_chunks_meta() — it reads facts off a whole-document
    parse rather than re-parsing each chunk.

    The context earns its keep three more times:
      * NER — "the cat, Molly" only resolves to a PERSON with neighbours.
      * coreference — "They passed a bee" needs the sentence before to know
        who "they" are.                                          [PHASE 4]
      * repetition — a thing already on screen from an earlier line should
        not be re-fetched.                                       [PHASE 4]

      doc            the spaCy Doc over the whole window.
      line           the target line, verbatim.
      line_chars     (start, end) of the line inside the window text.
      line_tokens    (start, end) token indices of the line inside doc.
      hints          SplitterHints — see SECTION 4.
      blocked        token spans find_blocked_spans() found, stashed so
                     apply_length_fallback() can tell "this line is nothing
                     but an idiom" from "this line is merely dull".
    """
    doc: object
    line: str
    line_chars: tuple[int, int]
    line_tokens: tuple[int, int]
    hints: "SplitterHints"
    blocked: list = field(default_factory=list)
    keep_pronouns: bool = False       # [PHASE 4] see create_visualisables_entry

    def target_tokens(self):
        """The doc tokens that belong to the TARGET LINE, and only those.

        Every harvester iterates this, never doc — so a candidate can never
        be harvested out of the surrounding context. That invariant is what
        makes the "parse wide, extract narrow" trick safe.
        """
        lo, hi = self.line_tokens
        return list(self.doc[lo:hi])

    def to_line_chars(self, tok_start: int, tok_end: int) -> tuple[int, int]:
        """Token span (doc-relative) -> character span (line-relative), for
        the template. Returns offsets into `line`, so they are directly
        usable for slicing and for str.replace positions."""
        lo, hi = self.line_tokens
        a, b = max(int(tok_start), lo), min(int(tok_end), hi)
        if a >= b:
            return (0, 0)
        span = self.doc[a:b]
        base = self.line_chars[0]
        return (max(span.start_char - base, 0),
                min(span.end_char - base, len(self.line)))


@dataclass
class SplitterHints:
    """What sentence_splitter.py already worked out about this line.

    The splitter tags every line with the RULE IDS that cut it — i.e. WHY
    the line exists. shared_text_logic SECTION 1.3 groups them, and those
    groups are free, high-trust evidence about what the line is FOR:

      NONVISUAL_RULES {1008,1009,1010}  this line was merged back because
                        it had no picture in it, or it is a bare idiom.
                        -> expect zero or one visualisables; do not force.
      LIST_RULES        the line is one item of a list run ("scurvy," /
                        "pirates,") -> the item IS the visualisable.
      NAME_REVEAL_RULES the line exists to reveal a name -> that name is
                        the point of the shot; rank it first.
      MONEY/NUMBER/DATE_RULES  the line exists for its figure.
      SOUND_RULES {60}  the line is an SFX beat -> KIND_SOUND.
      RELATIVE_RULES    the line is a long in/on/at place phrase -> its
                        noun is a SETTING, not a prop.

    Used as CORROBORATION only, never as a detector: the rule ids say what
    the splitter thought the line was about, they do not name a span. If a
    hint and the harvesters disagree, the harvesters win and the
    disagreement is written to `notes`.

    All fields default to "no information", so passing rule_ids is
    genuinely optional — as the TODO says, "you may or may not want to use
    this".
    """
    rule_ids: list[int] = field(default_factory=list)
    is_nonvisual: bool = False
    is_list_item: bool = False
    is_name_reveal: bool = False
    is_figure: bool = False          # money / number / date
    is_sound: bool = False
    is_place_phrase: bool = False
    # straight from the splitter's meta dict, when the caller has it
    meta_keywords: list[str] = field(default_factory=list)
    meta_nouns: list[str] = field(default_factory=list)
    meta_ents: list[dict] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Three small shared helpers. Each is called from more than one step, and
# none of them decides anything a word list or a parse has not already said.
# -----------------------------------------------------------------------------

def _clip_to_line(ctx: "Context", start: int, end: int) -> tuple[int, int] | None:
    """Intersect an entity span with the target line, or None if they miss.

    An entity can straddle a line boundary, because the splitter cuts on
    its own rules and does not protect entity spans: "the Circus | Maximus."
    Requiring containment threw those away and left the second line with no
    visualisable at all. Clipping keeps the showable half and marks the
    detector "-clipped" so the provenance says what happened.

    The "parse wide, extract narrow" invariant still holds: the returned
    span is always inside the line.
    """
    lo, hi = ctx.line_tokens
    a, b = max(int(start), lo), min(int(end), hi)
    return (a, b) if a < b else None


def _idiom_spans(doc):
    """STL.find_idiom_spans(doc), memoised for the current doc — see
    _IDIOM_MEMO for why. Same answer, computed once per script instead of
    once per line."""
    global _IDIOM_MEMO
    key, held, spans = _IDIOM_MEMO
    if key == id(doc) and held is doc:
        return spans
    spans = STL.find_idiom_spans(doc)
    _IDIOM_MEMO = (id(doc), doc, spans)
    return spans


def _find_token_span(ctx: "Context", phrase: str) -> tuple[int, int] | None:
    """The first token span INSIDE THE TARGET LINE whose text is `phrase`.

    Needed because a few of shared_text_logic's extractors return strings
    (extract_name_runs) while everything here works in token indices.
    Whitespace-normalised on both sides so tokenisation differences do not
    matter. Bounded at 12 tokens: no name run is longer than that.
    """
    lo, hi = ctx.line_tokens
    want = " ".join(phrase.lower().split())
    if not want:
        return None
    for i in range(lo, hi):
        for j in range(i + 1, min(hi, i + 12) + 1):
            if " ".join(ctx.doc[i:j].text.lower().split()) == want:
                return (i, j)
    return None


def _is_frozen_pair(doc, start: int, end: int) -> bool:
    """Does this span contain a FROZEN_BIGRAM — a pair the splitter refuses
    to cut ("bread and butter", "kind of")? Used by the coordination split
    to tell "dust and rock" (two pictures) from a fixed phrase (one)."""
    lowers = tuple(t.lower_ for t in doc[start:end])
    return any(lowers[i:i + 2] in STL.FROZEN_BIGRAMS
               for i in range(max(0, len(lowers) - 1)))


def _wordnet_verdict(word: str) -> str | None:
    """"physical" / "abstract" / None — the out-of-vocabulary backstop for
    drop_abstract(), for words Brysbaert never rated.

    Which of entity.n.01's children the word's FIRST noun sense hangs
    under. Note abstraction.n.**06** — .n.01 is a different, low-level
    synset and scores 0/10 (phase 2 got this wrong once). First sense only:
    widening to 2-3 senses measured worse, the same precision-over-recall
    trade-off kb_is_unit / kb_is_time already document.

    None when WordNet cannot say, or is not installed — and the caller
    treats None as KEEP.
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
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 2 — THE ORCHESTRATION                                     ### #
# ###                                                                     ### #
# ###   READ THIS FIRST. Eleven steps, one call each, top to bottom,      ### #
# ###   no branching. Every other section in this file is one of these    ### #
# ###   steps written out. If a step is hard to explain in one line, it   ### #
# ###   is the wrong step.                                                ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================


def create_visualisables_entry(
    input_text: str,
    rest_of_line_plus_next_sentence: str | None = None,
    all_preceeding_text: str | None = None,
    rule_ids: Sequence[int] | None = None,
    splitter_meta: dict | None = None,
    doc=None,
    line_token_span: tuple[int, int] | None = None,
    keep_pronouns: bool = False,
) -> VisualisablesEntry:
    """Find every visualisable in ONE split line. The whole pipeline.

    @param input_text
        The target line — the thing we are labelling. One line as the
        sentence splitter cut it: "The tractor and the cat, Molly, went
        down the lane."
    @param rest_of_line_plus_next_sentence
        What comes AFTER it: the remainder of its sentence plus the next
        sentence, when there is one. Parsing context only — nothing is ever
        harvested out of it.
    @param all_preceeding_text
        Everything BEFORE it in the script, or None for the first line.
        Parsing context, and the antecedent pool coreference will need in
        phase 4. May be long; see RESEARCH(phase-2) on windowing it.
    @param rule_ids
        The splitter's rule ids for this line (Chunk.ids). Optional.
    @param splitter_meta
        The splitter's meta dict for this line (ChunkWithMeta.meta:
        keywords / nouns / ents / head_noun / has_visualisable / ...).
        Optional. When present it is used to CROSS-CHECK our own harvest,
        not to replace it.
    @param doc, line_token_span
        THE FAST PATH, and the one the pipeline should actually use. The
        splitter has already parsed the whole script and already knows the
        token span of this line (ChunkWithMeta.meta["span"]). Hand both
        over and nothing is re-parsed, re-joined or re-located — see
        build_context() for the timings and for why this is not an
        optimisation but a correctness win. Omit them and we fall back to
        concatenating the three strings and parsing that.

    @param keep_pronouns
        [PHASE 4] Keep "it"/"they"/"she" as KIND_REFERENCE slots instead of
        dropping them, so the document pass (SECTION 12) can resolve each
        to what it points at. On its own a pronoun is not a picture, which
        is why this is off by default; it is the document pass that turns
        one into the picture it refers to.

    @returns VisualisablesEntry — the template line and its numbered slots.

    THE STEPS
        0  parse the line inside its context, so spaCy sees whole sentences
        1  read the splitter's rule ids into hints
        2  mark the spans we may never emit (idioms, discourse furniture)
        3  harvest every candidate — one detector per PART 1 bullet
        4  drop the PART 2 non-visualisables (pronouns, weak words, ...)
        5  drop the PART 3 abstract nouns (Brysbaert / WordNet)
        6  trim each survivor to its searchable core ("a jar of" -> "jar")
        7  split "X and Y" into two candidates (spec RULE 49)
        8  resolve overlaps — one picture per piece of text
        9  order left to right and number the slots [1] [2] [3]
       10  mercy rule: nothing survived but the line is 4+ real words
       11  classify settings, then build the template + the map
    """
    # 0) PARSE — reuse the splitter's doc + span when we were given them;
    #    otherwise parse (preceding + line + following) and locate the line.
    ctx = build_context(input_text,
                        rest_of_line_plus_next_sentence,
                        all_preceeding_text,
                        rule_ids,
                        splitter_meta,
                        doc,
                        line_token_span)
    ctx.keep_pronouns = keep_pronouns

    # 1) HINTS — what the splitter already knows about this line.
    ctx.hints = read_splitter_hints(rule_ids, splitter_meta)

    # 2) BLOCK — spans that are made of concrete words but are not pictures.
    blocked = find_blocked_spans(ctx)

    # 3) HARVEST — everything PART 1 calls a visualisable. Deliberately
    #    over-generous: the filters below are what make it right.
    candidates = []
    candidates += find_name_candidates(ctx)          # PROPN + named entities
    candidates += find_number_candidates(ctx)        # NUM + measures + money
    candidates += find_date_candidates(ctx)          # DATE / TIME
    candidates += find_noun_phrase_candidates(ctx)   # noun_chunks — the bulk
    candidates += find_quality_candidates(ctx)       # strong adjectives
    candidates += find_action_candidates(ctx)        # strong verbs
    candidates += find_sound_candidates(ctx)         # SFX words
    harvest = list(candidates)     # a handle, so --debug can show what died

    # 4) FILTER — PART 2. Nothing that only points at a picture.
    candidates = drop_blocked(candidates, blocked)
    candidates = drop_non_visualisable(candidates, ctx)

    # 5) FILTER — PART 3. Nothing you cannot point a camera at.
    candidates = drop_abstract(candidates, ctx)

    # 6) TIDY — cut each one down to what you would type into a search box.
    candidates = trim_to_searchable_core(candidates, ctx)

    # 7) SPLIT — "dust and rock" is two pictures, not one.
    candidates = split_coordinated_candidates(candidates, ctx)

    # 8) DEDUPE — the harvesters overlap by design; pick one per span.
    candidates = resolve_overlaps(candidates, ctx)

    # 9) ORDER — left to right; this is what fixes the slot numbers.
    candidates = order_candidates(candidates)

    # 10) FALLBACK — the mercy rule, so a real line is never left empty.
    candidates = apply_length_fallback(candidates, ctx)

    # 11) BUILD — mark settings, punch out the template, number the slots.
    candidates = classify_settings(candidates, ctx)
    entry = build_entry(ctx, candidates)
    entry.dropped = [c for c in harvest if c.dropped_by]
    return entry


# =============================================================================
# =============================================================================
# ###   SECTION 3 — STEP 0: THE CONTEXT                                   ### #
# =============================================================================
# =============================================================================


def build_context(input_text: str,
                  following: str | None,
                  preceding: str | None,
                  rule_ids: Sequence[int] | None,
                  splitter_meta: dict | None,
                  doc=None,
                  line_token_span: tuple[int, int] | None = None) -> Context:
    """Parse the line inside its neighbours and locate it in the result.

    Joins  preceding + " " + line + " " + following  into one window,
    parses it once with the shared spaCy model, and records both the
    character span and the token span of the line inside it.

    Two things that will bite in phase 3 and are called out now:
      * Finding the line back in the window must not be a str.find() on the
        raw text — the line can legitimately occur twice ("It was."). Build
        the window BY CONCATENATION and keep the offsets as you go; never
        search for them afterwards.
      * spaCy tokenisation may not align to those character offsets.
        Convert with Doc.char_span(..., alignment_mode="expand"), the same
        call abstract_term_resolver.pick_antecedent() relies on.

    SETTLED IN PHASE 2 — DO NOT WINDOW, AND PREFERABLY DO NOT RE-PARSE.
    Measured with en_core_web_sm: 0.10 ms/token, flat from 90 to 5,760
    tokens, so a whole 5,760-token script parses in 0.56 s. Per-line
    re-parsing is the expensive thing here, not context size — 200 lines
    re-parsing their own preceding text is quadratic and pointless.

    So the fast path is to not parse at all: sentence_splitter.py ALREADY
    parses the whole script once, and hands every line
    `meta["span"] = [lo, hi]` — token indices into that very doc. Given the
    doc and that span there is nothing to locate, nothing to align and
    nothing to re-parse, which also deletes both hazards listed above.

    Hence the signature: `doc` and `line_token_span` are optional, and when
    the caller has them (it does — it is holding the splitter's output)
    they are used as-is. Concatenating and re-parsing is the fallback for
    a caller that only has strings.

    One wiring note for phase 3: split_text_into_sections_with_meta()
    currently returns the ChunkWithMeta list but NOT the doc, even though
    _split_core() already has it. Either add an accessor there, or have
    myownstuff.py parse the script once itself (0.56 s) and pass it down.
    """
    hints = SplitterHints()          # step 1 replaces this with the real one

    # ---- fast path: the caller handed us the splitter's own parse --------
    # Nothing to concatenate, nothing to locate, nothing to align.
    if doc is not None and line_token_span is not None:
        lo, hi = int(line_token_span[0]), int(line_token_span[1])
        span = doc[lo:hi]
        # ctx.line is the span's own text, not input_text: the two should be
        # equal, and if they ever differ the doc is the one the offsets and
        # the template are computed against.
        return Context(doc=doc, line=span.text,
                       line_chars=(span.start_char, span.end_char),
                       line_tokens=(lo, hi), hints=hints)

    # ---- fallback: build the window BY CONCATENATION --------------------
    # Offsets are accumulated as we go and never searched for afterwards,
    # so a line that occurs twice ("It was.") cannot be mislocated.
    line = " ".join(input_text.split())
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
    span = parsed.char_span(start, end, alignment_mode="expand")
    if span is None:                             # pragma: no cover
        span = parsed[:]
    return Context(doc=parsed, line=line, line_chars=(start, end),
                   line_tokens=(span.start, span.end), hints=hints)


# =============================================================================
# =============================================================================
# ###   SECTION 4 — STEP 1: THE SPLITTER'S HINTS                          ### #
# =============================================================================
# =============================================================================


def read_splitter_hints(rule_ids: Sequence[int] | None,
                        splitter_meta: dict | None) -> SplitterHints:
    """Turn the splitter's rule ids + meta into the flags we care about.

    Pure lookup against the SECTION 1.3 groups already defined in
    shared_text_logic — NONVISUAL_RULES, LIST_RULES, NAME_REVEAL_RULES,
    MONEY_RULES, NUMBER_RULES, DATE_RULES, SOUND_RULES, RELATIVE_RULES. No
    new rule ids are invented here; if a group is missing, add it there so
    the splitter and the extractor keep sharing one definition.

    Missing input is not an error: with no rule_ids every flag is False and
    the pipeline runs on the harvesters alone.
    """
    ids = [int(r) for r in (rule_ids or [])]
    meta = splitter_meta or {}
    seen = set(ids)
    figure_rules = STL.MONEY_RULES | STL.NUMBER_RULES | STL.DATE_RULES
    return SplitterHints(
        rule_ids=ids,
        is_nonvisual=bool(seen & STL.NONVISUAL_RULES),
        is_list_item=bool(seen & STL.LIST_RULES) or bool(meta.get("list")),
        is_name_reveal=bool(seen & STL.NAME_REVEAL_RULES),
        is_figure=bool(seen & figure_rules),
        is_sound=bool(seen & STL.SOUND_RULES),
        is_place_phrase=bool(seen & STL.RELATIVE_RULES),
        meta_keywords=list(meta.get("keywords") or []),
        meta_nouns=list(meta.get("nouns") or []),
        meta_ents=list(meta.get("ents") or []),
    )


# =============================================================================
# =============================================================================
# ###   SECTION 5 — STEP 2: SPANS WE MAY NEVER EMIT                       ### #
# =============================================================================
# =============================================================================


def find_blocked_spans(ctx: Context) -> list[tuple[int, int]]:
    """Token spans made of concrete words that are NOT pictures.

    Spec PART 2, the last three bullets. These have to be found BEFORE
    harvesting, because their words individually look perfect:

      idioms          "out of the blue", "against all odds" — filming the
                      blue would be wrong. STL.find_idiom_spans(doc) already
                      returns exactly these spans, and the splitter already
                      tags such a line 1010.
      discourse       "here's the thing", "which brings us to", "plot
                      twist" — script furniture. STL.DISCOURSE_PIVOT_PHRASES.
      frozen bigrams  STL.FROZEN_BIGRAMS — "want to", "kind of": pairs the
                      splitter refuses to cut, and we refuse to picture.

    PHASE 2 FOUND ONE, and it is worth taking eventually: the MAGPIE
    corpus (Haagsma, Bos & Nissim) — 56,622 sense-annotated instances over
    1,756 idiom TYPES, CC-licensed, one jsonl on GitHub
    (hslh/magpie-corpus). Pulling the distinct `idiom` field out is a
    ~10-line addition to build_wordlists.py, exactly like the concreteness
    table. Five of the spec's own eight example idioms are already in it
    ("the rest is history", "at the end of the day", "out of the blue",
    "easier said than done", "in a nutshell"); "against all odds", "long
    story short" and "little did they know" are not — so it EXTENDS
    IDIOM_PHRASES 65-fold, it does not replace it. Keep both.

    The other two candidates were checked and rejected: STREUSLE is a
    55k-word annotated CORPUS rather than a lexicon, and PARSEME covers
    verbal MWEs only.

    Priority: LOW. This is a blocklist whose failure mode is one mediocre
    stock clip, and DISCOURSE_PIVOT_PHRASES ("plot twist", "fun fact") is
    script furniture that no public idiom corpus will ever carry. Do it
    when touching build_wordlists.py for another reason.
    """
    doc = ctx.doc
    lo, hi = ctx.line_tokens
    lowers = [t.lower_ for t in doc]
    spans: list[tuple[int, int]] = []

    # idioms — the splitter's own matcher, so rule 1010 and this agree
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
    ctx.blocked = spans                   # apply_length_fallback reads this
    return spans


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 6 — STEP 3: THE HARVESTERS                                ### #
# ###                                                                     ### #
# ###   One function per bullet of spec PART 1, in the spec's own order.  ### #
# ###   Every one of them:                                                ### #
# ###     * iterates ctx.target_tokens() only,                            ### #
# ###     * returns list[Candidate] (possibly empty),                     ### #
# ###     * is allowed to overlap the others — step 8 sorts that out,     ### #
# ###     * never filters. Being generous here is the point; a candidate  ### #
# ###       that should not survive is SECTION 7's problem.               ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================


def find_name_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullet 1 — capitalised and named things. KIND_NAME.

    The strongest kind of visualisable: a named thing can always be shown,
    because you can go and fetch the actual photograph of it.

    Two sources, both already written:
      * spaCy entities whose label is in STL.NAME_ENT_LABELS (PERSON, ORG,
        GPE, LOC, FAC, NORP, EVENT, WORK_OF_ART, PRODUCT, LAW, LANGUAGE).
      * STL.extract_name_runs(text, doc) — the splitter's own capital-run
        walker. It already glues "Vasco da Gama" together across
        STL.NAME_CONNECTORS and already refuses to start a run on a
        STL.CAP_STOPWORDS word ("The", "But", "January"), which is the
        sentence-initial-capital trap the spec warns about.

    Confidence 1.0: this is the one detector that is essentially never
    wrong, and step 8 should let it win overlaps.
    """
    doc, out = ctx.doc, []
    lo, hi = ctx.line_tokens

    # NAME_ENT_LABELS is missing LOC, which the spec's PART 1 explicitly
    # lists among the labels to trust ("PERSON, ORG, GPE, LOC, FAC, ..."),
    # so union in PLACE_ENT_LABELS rather than writing a third list. Without
    # it "the Banda Islands" is only a noun chunk and loses its determiner.
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

    # Capital runs spaCy's NER missed. The splitter's own walker, so the
    # CAP_STOPWORDS / NAME_CONNECTORS rules stay shared with it.
    for run in STL.extract_name_runs(ctx.line):
        span = _find_token_span(ctx, run)
        if span and not any(c.start <= span[0] and span[1] <= c.end for c in out):
            out.append(Candidate(span[0], span[1], KIND_NAME,
                                 "name:run", confidence=0.9))
    return out


def find_number_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullets 4 and 6 — numbers, quantities, money. KIND_NUMBER.

    A figure is visualisable because it goes on screen as a counter, a bar,
    a pie or a timeline.

    Sources: tokens with pos_ == NUM or .like_num, entities in
    STL.NUMERIC_ENTS (CARDINAL, ORDINAL, QUANTITY, MONEY, PERCENT), and
    STL.extract_number_or_stat() for the written-out forms
    (number_parser: "seventeen million") and STL.price_parser for "£4,000".

    THE ONE RULE THAT MATTERS: a number and its measure word are ONE
    candidate and must never be split — "15 metres", "900 ships", "3
    thousand years". STL.MEASURE_NOUNS is that list; extend the span
    rightwards through it (and through the nummod/quantmod dependants
    spaCy attaches), or the noun-phrase harvester will emit "metres" on its
    own, which is not a picture of anything.
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
        # THE RULE THAT MATTERS: a number and its measure word are ONE
        # candidate. "900 ships", "15 metres", "3 thousand years".
        head = tok.head
        if (end <= head.i < hi
                and tok.dep_ in {"nummod", "quantmod", "compound"}
                and (head.lower_ in STL.MEASURE_NOUNS
                     or head.pos_ in {"NOUN", "PROPN"})):
            end = head.i + 1
        while (start - 1 >= lo
               and doc[start - 1].dep_ in {"quantmod", "compound"}
               and (doc[start - 1].pos_ == "NUM" or doc[start - 1].like_num)):
            start -= 1
        out.append(Candidate(start, end, KIND_NUMBER, "number:tok",
                             confidence=0.9))
    return out


def find_date_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullet 5 — dates, years, times. KIND_DATE.

    "1946", "the 19th century", "the 1600s", "August 1945", "midnight".
    Shown as a timeline marker travelling to that year, so the whole date
    expression is the unit, not the bare digits.

    Sources: DATE and TIME entities, STL.extract_year_or_date() and its
    dateparser booster. Kept separate from find_number_candidates because
    the two want different treatment downstream (a date is a timeline, a
    number is a chart) even though spaCy's NER hands them over together.
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
    """PART 1 bullets 2 and 3 — ordinary concrete nouns, as whole phrases.
    KIND_THING. This is the bulk of the work.

    THE UNIT IS THE NOUN PHRASE, NOT THE NOUN. The spec is explicit: "a jar
    of nutmeg" is the visualisable, not "jar"; "the old wooden ship", not
    "ship". So we start from doc.noun_chunks (a benchmarked spaCy
    component, not a regex) rather than from bare NOUN tokens.

    Three known shortfalls of spaCy noun_chunks, each with an existing
    remedy:
      1. It cuts before a preposition, so "a jar of nutmeg" arrives as two
         chunks. Re-join across STL.PROMISCUOUS_PREPS ({"of"}) when the
         second chunk is the first one's `pobj` dependant — that is the
         case the spec's own example asks for.
      2. It does not include compounds that the splitter treats as glued
         ("phone box", "crash site"). Those already come back inside the
         chunk via the `compound` dep, so keep compound children.
      3. It includes relative clauses' heads but not the clauses — which is
         what we want; do not follow acl/relcl.

    SETTLED IN PHASE 2: spaCy noun_chunks, on en_core_web_sm. Both models
    were run over the spec's seven worked examples and produced BYTE-
    IDENTICAL noun_chunks on all seven — trf buys nothing here, while
    costing 9x the parse time (0.88 vs 0.10 ms/token). It is not even
    uniformly better at NER: on "The tractor and the cat, Molly, went down
    the lane" sm tagged Molly/PERSON and trf tagged no entity at all
    (trf did win "900"/CARDINAL, which we get from POS=NUM anyway).
    So: stay on sm, which is also the model the splitter has already
    loaded — "one shared model" stays true. benepar was not benchmarked;
    it is not installed and nothing above suggests a need for it.

    The measurements also confirmed the three shortfalls listed above are
    real and are the only ones: "a jar of nutmeg" -> ['a jar', 'nutmeg']
    and "a wall of water" -> ['a wall', 'water'] in BOTH models, so the
    of-merge is genuinely required. Merging across "of" specifically is
    also the safe case in the parsing literature — of-attachment is
    treated as reliably nominal, unlike with/in/on, which is why
    STL.PROMISCUOUS_PREPS contains "of" and nothing else.

    One more thing the run confirmed, for step 7: "The yellow and black
    guy flew away" comes back as ONE chunk, so coordinated modifiers are
    not split for us and split_coordinated_candidates() has real work.
    """
    doc, out = ctx.doc, []
    lo, hi = ctx.line_tokens
    chunks = [nc for nc in doc.noun_chunks if lo <= nc.start and nc.end <= hi]

    skip: set[int] = set()
    for i, nc in enumerate(chunks):
        if i in skip:
            continue
        start, end, root = nc.start, nc.end, nc.root
        # "a jar OF nutmeg" arrives as two chunks; re-join across
        # PROMISCUOUS_PREPS ({"of"}) — the one attachment that is reliably
        # nominal — when the parse actually says jar <- of <- nutmeg.
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

    "red", "rusted", "ancient", "microscopic". They change what the picture
    LOOKS like, so they are visual.

    Test is already written and shared with the splitter: pos_ == "ADJ" and
    lemma not in STL.WEAK_ADJ_LEMMAS (which is where "many", "several",
    "possible" get thrown out).

    NOTE for step 8: an adjective inside a noun chunk we already harvested
    ("the OLD wooden ship") must NOT become its own slot — it is part of
    that picture. It only stands alone when it is a predicate ("the sky
    turned red"). Emit it either way here; resolve_overlaps() decides.
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

    "jumped", "sailed", "exploded", "collapsed", "sank". Actions you could
    actually point a camera at.

    Test, again shared with the splitter: pos_ == "VERB" and lemma not in
    STL.WEAK_VERB_LEMMAS and text not in STL.WEAK_VERB_FORMS. That pair of
    lists is what removes be/have/do/get/make/know/think/seem and the
    contractions — the verbs whose picture always lives in the noun next
    to them.

    The span should include the particle of a phrasal verb
    (STL.PARTICLE_DEPS: "flew AWAY", "dried UP"), because the particle is
    what makes the action filmable.

    Note these are NOT concrete visualisables (myownstuff.py's definition
    excludes actions). They are harvested because phase 4 will attach them
    to the thing performing them — an `action` on a Visualisable, not a
    shot of their own.
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
        # "flew AWAY", "dried UP" — the particle is what makes it filmable
        for child in tok.children:
            if child.dep_ in STL.PARTICLE_DEPS and lo <= child.i < hi:
                end = max(end, child.i + 1)
        out.append(Candidate(tok.i, end, KIND_ACTION, "action:verb",
                             confidence=0.7))
    return out


def find_sound_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullet 9 — bare sound-effect words. KIND_SOUND.

    "and then BOOM the roof came down" — boom earns its own on-screen beat.
    STL.SFX_WORDS is the list, and the splitter already cuts these lines
    with rule 60, so ctx.hints.is_sound corroborates.

    Only counts when the word is used BARE (as an INTJ or a standalone
    fragment), not when it is a real noun or verb in the clause — "the
    sonic boom shattered windows" is a KIND_THING, not an SFX beat. spaCy's
    POS on the token is the discriminator.
    """
    out = []
    for tok in ctx.target_tokens():
        if tok.lower_ not in STL.SFX_WORDS:
            continue
        # BARE only. "the sonic boom shattered windows" is a KIND_THING and
        # belongs to the noun-phrase harvester, not here.
        bare = tok.pos_ in {"INTJ", "X"} or tok.dep_ in {"intj", "discourse"}
        if not bare and not ctx.hints.is_sound:
            continue
        out.append(Candidate(tok.i, tok.i + 1, KIND_SOUND, "sound:sfx",
                             confidence=0.8 if bare else 0.5))
    return out


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 7 — STEPS 4 AND 5: THE FILTERS                            ### #
# ###                                                                     ### #
# ###   Spec PART 2 (things that are not visualisables) and PART 3 (the   ### #
# ###   abstract-noun grey area). Every filter is                         ### #
# ###   list[Candidate] -> list[Candidate] and sets `dropped_by` rather   ### #
# ###   than deleting, so --debug can show its working.                   ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================


def drop_blocked(candidates: list[Candidate],
                 blocked: list[tuple[int, int]]) -> list[Candidate]:
    """Remove anything overlapping a span from find_blocked_spans().

    Straight span arithmetic, no linguistics. Overlap, not containment: an
    idiom's words must not leak out as a candidate either way round.
    """
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

    In the spec's order:
      pronouns            pos_ == "PRON" — "it", "they", "this". They point
                          at a picture, they are not one. (Phase 4 will
                          RESOLVE these via abstract_term_resolver instead
                          of dropping them; for now they go.)
      weak verbs          STL.WEAK_VERB_LEMMAS / WEAK_VERB_FORMS — already
                          applied by find_action_candidates, re-checked
                          here because a noun chunk can contain a gerund.
      weak adjectives     STL.WEAK_ADJ_LEMMAS.
      generic topic nouns STL.GENERIC_TOPIC_NOUNS — "thing, way, time,
                          part, place, people, world, kind, story, fact".
                          Real nouns that turn up in every script about
                          anything, so they never earn a picture. Drop only
                          when the generic noun is the phrase's HEAD: "the
                          main thing" goes, "a way out of the mine" is
                          about the mine.
      function words      STL.LIGHTWEIGHT_POS — a candidate that survived
                          trimming down to nothing but DET/ADP/PART.

    None of these lists is written here. They are all imported, which is
    the point: the splitter refuses to leave such a line on its own, and we
    refuse to fetch footage for it, using the same data.
    """
    doc, kept = ctx.doc, []
    for c in candidates:
        root = doc[c.start:c.end].root
        lemma = root.lemma_.lower()
        why = None
        if root.pos_ == "PRON":
            # A pronoun points at a picture, it is not one — so by default
            # it goes. With keep_pronouns it survives as a KIND_REFERENCE
            # slot for the document pass to resolve into its referent.
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
            # only when the generic noun is the phrase's HEAD: "the main
            # thing" goes, "a way out of the mine" is about the mine.
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
    """Spec PART 3 — the known gap. THE function this whole file exists for.

    "monopoly", "inflation", "betrayal", "freedom", "justice", "tension"
    are all tagged NOUN by spaCy, so every check above waves them through,
    and none of them is something you can point a camera at.

    We do not write a word list for this. We use the published one:

      1. BRYSBAERT, WARRINER & KUPERMAN (2014) concreteness ratings.
         ~37,000 English words, each rated 1..5 by ~30 human raters
         (1 = purely abstract, 5 = experienced directly through the
         senses). It is THE standard psycholinguistic resource for exactly
         this question, and ../recommender_data.json already contains it —
         reach it with kb().kb_concreteness(lemma).
         Score >= MIN_CONCRETENESS -> keep. Below -> drop.

      2. NOT RATED? Ask WordNet, through the same kb() layer. The test is
         which top-level root the word's first noun sense hangs under:
         physical_entity.n.01 (keep) vs abstraction.n.06 (drop).
         kb()._wn_has_hypernym already implements that hypernym walk.

         NOTE THE ".n.06". entity.n.01 has exactly three children —
         abstraction.n.06, physical_entity.n.01, thing.n.08 — and
         "abstraction.n.01" is a DIFFERENT, low-level synset. Using it
         scores 0/10; using .n.06 scores 8/10. Phase 2 got this wrong once
         already, so it is written down here.

         Measured accuracy, first sense only: 12/13 concrete, 8/10
         abstract (~87%). Widening to 2-3 senses makes it WORSE (9/13
         concrete) — the same precision-over-recall finding the existing
         kb_is_unit / kb_is_time docstrings record. Known misses:
         skeleton -> abstract, and inflation/risk -> physical (WordNet
         files inflation under economic_process, which is a physical
         entity). Those two are exactly why this is the BACKSTOP and not
         the primary test.

         And it barely ever fires: Brysbaert covers 85% of the noun-phrase
         heads in the three real scripts, and every one of the 8 it missed
         was a proper noun or a year (Rome, Nero, Sahara, Indonesia,
         Hitan, Maximus, 1600, Islands) — all of which are KIND_NAME or
         KIND_DATE and skip this filter anyway. Budget accordingly: this
         branch is insurance, not a workhorse.

      3. STILL NOTHING, or no kb at all? KEEP IT. House rule, borrowed from
         the tagger in shared_text_logic SECTION 6, but pointed the other
         way: here a wrong drop is expensive (we lose a real shot and the
         line goes blank) while a wrong keep costs one mediocre stock clip
         that manual tagging can fix. So: silence means keep.

    Which token is scored: the phrase's HEAD lemma, not the whole phrase —
    Brysbaert rates single words. ("a wall of water" is scored on "wall".)

    ONLY KIND_THING REACHES THIS FILTER — see CONCRETENESS_APPLIES_TO for
    the measurements. Names, numbers and dates skip it because a proper
    noun is concrete by construction and Brysbaert has never heard of
    "Molly"; adjectives, verbs and SFX skip it because the scale demonstrably
    does not separate them ("ancient" 2.04, "whoosh" 2.64 would both die).

    PHASE 2 CLOSED THREE OF THE FOUR OPEN QUESTIONS HERE:
      * threshold -> 3.0. See MIN_CONCRETENESS.
      * imageability -> NO, stay on Brysbaert. The Glasgow Norms (Scott et
        al. 2019) really do rate both scales, and on the spec's lists
        imageability separates fine (+1.73 on a 1-7 scale vs Brysbaert's
        +1.57 on a 1-5 scale — the same margin once normalised). It loses
        on COVERAGE, which is what actually matters: of the 52 noun-phrase
        heads in the real scripts, Brysbaert rates 44 (85%) and Glasgow
        rates 25 (48%). Glasgow is 4.7k words against Brysbaert's 37k.
        The two also correlate at r=0.88, so a second table would buy
        almost no new information for a lot of missing words.
        (And imageability does NOT rescue the adjectives — "brilliant" is
        3.31/7 there, still mid-scale. Nothing does; that is why the fix
        was the POS exemption, not a different table.)
      * bigrams -> NO. Fetched the source: 2,896 bigram rows, and they are
        household objects ("baking soda", "beach ball", almost all rated
        exactly 5.00). Overlap with the compounds our own text produces:
        ZERO of the 16 compounds in the three real scripts, and zero of
        the spec's four ("phone box", "crash site", "spice trade", "stock
        footage"). Leave build_wordlists.py's `parts[1] != "0"` skip alone.

      * drop or flag? -> DROP OUTRIGHT (decided phase 2). An abstract
        noun is removed exactly like a PART 2 word: this function stays
        `list[Candidate] -> list[Candidate]`, there is no
        `needs_resolution` flag, and nothing downstream has to carry a
        slot it cannot film. "The company held a monopoly on the spice
        trade" yields [company, spice trade] and monopoly is simply gone.

        The trade-off, written down so it is a choice and not an
        oversight: the spec's PART 3 wants abstracts RESOLVED into
        concrete stand-ins ("inflation" -> a shrinking pile of
        banknotes), and dropping them here means no later stage can,
        because it never learns the term was there. If that resolution is
        ever built, this is the function to revisit — keep the candidate,
        add the flag, and let the new stage consume it. Until then,
        one filter with one behaviour.
    """
    knowledge, doc, kept = kb(), ctx.doc, []
    for c in candidates:
        # SEE CONCRETENESS_APPLIES_TO. The scale is calibrated on nouns and
        # judging anything else with it deletes "ancient" and "whoosh".
        if c.kind not in CONCRETENESS_APPLIES_TO:
            kept.append(c)
            continue
        head = doc[c.start:c.end].root.lemma_.lower()

        # 1. Brysbaert et al. (2014), the published answer.
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

        # 3. Silence means KEEP: a wrong drop blanks a line, a wrong keep
        #    costs one mediocre clip that manual tagging fixes.
        kept.append(c)
    return kept


# =============================================================================
# =============================================================================
# ###   SECTION 8 — STEPS 6 TO 10: TIDY, SPLIT, DEDUPE, ORDER, FALLBACK   ### #
# =============================================================================
# =============================================================================


def trim_to_searchable_core(candidates: list[Candidate],
                            ctx: Context) -> list[Candidate]:
    """Cut each candidate down to what you would type into a search box.

    The spec's own example: "a jar of nutmeg sat on the shelf" -> the
    visualisable is "jar of nutmeg". So, from the front:
      * strip determiners (pos_ == "DET": "a", "the", "this")
      * strip weak/quantifier adjectives (STL.WEAK_ADJ_LEMMAS: "several
        rusted anchors" -> "rusted anchors")
      * keep strong adjectives, compounds and nummods — they are what the
        picture LOOKS like
    and from the back:
      * drop trailing punctuation and dangling prepositions
      * drop a relative clause the chunker let in
      * if still longer than MAX_PHRASE_TOKENS, keep the head plus its
        adjectival/compound modifiers only

    abstract_term_resolver.mention_name() already does very nearly this
    job — entity first, then PERSON, then head + amod/compound/nummod
    children. Phase 3 should call it rather than re-derive it, and the two
    files should end up sharing one implementation.
    """
    doc, out = ctx.doc, []
    for c in candidates:
        if c.kind in {KIND_NAME, KIND_NUMBER, KIND_DATE, KIND_SOUND}:
            out.append(c)        # already exactly the thing you would search
            continue
        start, end = c.start, c.end
        # front: determiners and quantifier adjectives carry no picture
        while start < end and (
                doc[start].pos_ in {"DET", "PUNCT", "PART", "CCONJ"}
                or (doc[start].pos_ == "ADJ"
                    and doc[start].lemma_.lower() in STL.WEAK_ADJ_LEMMAS)):
            start += 1
        # back: trailing punctuation and a dangling preposition
        while end > start and (doc[end - 1].is_punct
                               or doc[end - 1].pos_ in {"ADP", "PART", "CCONJ"}):
            end -= 1
        if start >= end:
            c.dropped_by = "trimmed-to-nothing"
            continue
        # still a sentence rather than a picture? keep the head and the
        # modifiers that change how it LOOKS. Same shape as
        # abstract_term_resolver.mention_name().
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
    """Spec PART 1, second-to-last bullet (splitter RULE 49): two
    visualisables joined by "and"/"or" are TWO visualisables.

    "dust and rock" -> dust | rock.   "calming and alien" -> two.
    "The tractor and the cat went down the lane" -> tractor | cat | lane.

    spaCy usually gives two noun_chunks here already, so this mostly
    catches the cases inside one chunk — coordinated adjectives, and
    coordinated compounds ("the yellow and black guy").

    Do it on the DEPENDENCY tree (a `conj` child of the head, with the cc
    between them), never on the string: "bread and butter" and "cat and
    mouse game" are one picture each, and the parse is what tells them
    apart. STL.FROZEN_BIGRAMS covers the fixed pairs.
    """
    doc, out = ctx.doc, []
    for c in candidates:
        root = doc[c.start:c.end].root
        conjs = sorted((ch for ch in root.children
                        if ch.dep_ == "conj" and c.start <= ch.i < c.end),
                       key=lambda t: t.i)
        # the parse decides, never the string: "bread and butter" is one
        # picture and FROZEN_BIGRAMS is what says so.
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
    """Seven harvesters ran over the same tokens. Pick one per span.

    Precedence, strongest picture first:
      1. KIND_NAME     — a named thing beats the phrase containing it:
                         "the cat, Molly" keeps Molly.
      2. KIND_NUMBER / KIND_DATE  — "900 ships" beats "ships".
      3. KIND_THING    — the widest noun phrase beats a narrower one, and
                         absorbs any adjective inside it ("the old wooden
                         ship" is ONE slot, not three).
      4. KIND_ACTION / KIND_QUALITY — only where nothing above covers them.
      5. KIND_SOUND    — bare SFX only, so it rarely collides.

    Ties broken by (a) longer span, (b) higher confidence, (c) leftmost.
    Every loser keeps its span in `dropped_by` so --debug can show what was
    absorbed by what.
    """
    # Longest span first — "900 ships" must beat "900", "the old wooden
    # ship" must absorb "old" and "wooden". Precedence only breaks ties,
    # which is where "the 1600s" resolves to a date rather than a thing.
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
    """Sort left to right by start token. This is the step that fixes the
    slot numbers: after it, index 0 is [1], index 1 is [2], and the
    numbering in `template` matches the numbering in `visualisables`."""
    return sorted(candidates, key=lambda c: (c.start, c.end))


def apply_length_fallback(candidates: list[Candidate],
                          ctx: Context) -> list[Candidate]:
    """Spec PART 1, last bullet — the mercy rule.

    If NOTHING survived but the line is >= LENGTH_FALLBACK_MIN_TOKENS
    non-punctuation tokens ("what had to be done", "everything they had
    ever known"), emit ONE KIND_FALLBACK candidate covering the whole line,
    with low confidence.

    It is not a picture. It is a licence for the line to stand on its own
    and hold whatever image is already on screen — which is exactly how the
    splitter uses STL.has_visualisable_content(), the very function this
    reimplements the tail of. Call that function rather than recounting
    tokens, so the two can never drift apart.

    When the splitter's own hints say the line is non-visual (rule 1008 /
    1009 / 1010), return no fallback: the splitter already decided this
    scrap should be riding on its neighbour's image.
    """
    if candidates:
        return candidates
    # the splitter already parked this scrap on its neighbour's image
    if ctx.hints.is_nonvisual:
        return candidates
    lo, hi = ctx.line_tokens
    content = [t.i for t in ctx.doc[lo:hi] if not t.is_punct and not t.is_space]
    # A line that is ONLY an idiom is not picture-able (spec rule 1010).
    # Leading/trailing grammar glue does not rescue it — "and the rest is
    # history" is still just the saying — so LIGHTWEIGHT_POS tokens are not
    # counted when asking "is this line nothing but a blocked phrase?".
    substantive = [i for i in content
                   if ctx.doc[i].pos_ not in STL.LIGHTWEIGHT_POS]
    if substantive and all(any(a <= i < b for a, b in ctx.blocked)
                           for i in substantive):
        return candidates
    # the mercy rule itself — the splitter's own test, so the two can't drift
    if not STL.has_visualisable_content(ctx.doc, lo, hi):
        return candidates
    if len(content) < LENGTH_FALLBACK_MIN_TOKENS:
        return candidates
    return [Candidate(lo, hi, KIND_FALLBACK, "fallback:length",
                      confidence=0.2)]


# =============================================================================
# =============================================================================
# ###   SECTION 9 — STEP 11a: SETTINGS                                    ### #
# =============================================================================
# =============================================================================


def classify_settings(candidates: list[Candidate],
                      ctx: Context) -> list[Candidate]:
    """Mark which candidates are the PLACE the others are standing in.

    myownstuff.py's third definition: a "Setting" is a place that usually
    contains the visualisables — "field", "25th Avenue", "Planet Earth".
    The renderer needs the distinction: a setting goes in the BACKGROUND,
    a thing goes on top of it.

    Two independent signals, both already available:
      * WHAT IT IS — kb().kb_is_place(head) (250 countries + capitals from
        mledoze/countries, then WordNet's geographical_area / district /
        urban_area hypernyms), plus spaCy's GPE / LOC / FAC entity labels
        (STL.PLACE_ENT_LABELS).
      * HOW IT IS USED — the phrase hangs off a spatial preposition
        (STL.SPATIAL_LOCATIVE_PREPS / SPATIAL_DIRECTIONAL_PREPS): "went
        DOWN the lane", "sits UNDER the ice". This is what makes "the lane"
        a setting in the TODO's own example, and it is also what the
        splitter's RELATIVE_RULES already flag (ctx.hints.is_place_phrase).

    KNOWN OVER-FIRE, measured in phase 3: the "how it is used" half trusts
    the preposition, and a metaphorical locative fools it — "held a monopoly
    ON the spice trade" marks the spice trade a setting. Attachment does not
    separate the two cases ("on" attaches to the verb "held" there, exactly
    as it attaches to "sat" in "sat on the shelf"), so no cheap parse test
    fixes it. Left as-is deliberately: nothing consumes is_setting until
    phase 4, and a false setting costs nothing today. When phase 4 DOES
    consume it, require both halves to agree rather than either.

    Sets Candidate.is_setting. It does NOT remove the candidate — a setting
    is still a visualisable and still gets a slot.

    [PHASE 4] this is where `location` gets filled: every non-setting
    visualisable in the line inherits the line's setting, and a line with
    no setting of its own inherits the last one seen. That inheritance
    needs state across lines, so it belongs to myownstuff.py's loop rather
    than to this per-line function.
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
        # HOW IT IS USED — hanging off a spatial preposition
        gov = root.head
        used_spatially = (gov.pos_ == "ADP" and gov.lower_ in spatial_preps)
        # [PHASE 4] A third route to "used as a place", because the prep
        # lists alone were measurably not enough: "went DOWN the lane" is a
        # location, but "down" is in neither SPATIAL_LOCATIVE_PREPS nor
        # SPATIAL_DIRECTIONAL_PREPS, so the lane came back with no setting
        # at all and the whole first sentence got location=None.
        # Rather than start a fourth prep list, ask WordNet what the VERB
        # is: the object of any preposition hanging off a verb of motion
        # is where that motion happened. ("go" -> travel.v.01.)
        if not used_spatially and gov.pos_ == "ADP":
            governing = gov.head
            if governing.pos_ == "VERB" and _is_motion_verb(governing):
                used_spatially = True
        # A prep-only setting must also be concrete enough to BE a place —
        # see MIN_SETTING_CONCRETENESS. Unrated words pass.
        if used_spatially and not is_place:
            rating = (knowledge.kb_concreteness(root.lemma_.lower())
                      if knowledge else None)
            if rating is not None and rating < MIN_SETTING_CONCRETENESS:
                used_spatially = False
        # Score the two halves separately instead of OR-ing them away: the
        # document pass picks a sentence's setting by the STRONGEST signal,
        # so "held a monopoly on the spice trade" loses to a real place.
        c.setting_score = 1.0 if is_place else (0.5 if used_spatially else 0.0)
        c.is_setting = c.setting_score > 0.0
    return candidates


# =============================================================================
# =============================================================================
# ###   SECTION 10 — STEP 11b: BUILDING THE ANSWER                        ### #
# =============================================================================
# =============================================================================


def build_entry(ctx: Context,
                candidates: list[Candidate]) -> VisualisablesEntry:
    """Punch the candidates out of the line and number the holes.

    "The tractor and the cat, Molly, went down the lane."
      ->  "The [1] and the [2], Molly, went down the [3]."
      +   {1: tractor, 2: cat, 3: lane}

    Build the template RIGHT TO LEFT so each replacement cannot invalidate
    the character offsets of the ones still to come — the candidates arrive
    left-to-right from order_candidates(), so iterate them reversed while
    numbering them forwards.

    Also records, in `notes`, anything the splitter's hints and our harvest
    disagreed about (hints said name-reveal, we found no name) — cheap now,
    and it is the list to read first when the output looks wrong.
    """
    ordered = order_candidates(candidates)
    vis = {n: _candidate_to_visualisable(c, ctx)
           for n, c in enumerate(ordered, 1)}

    # Substitute RIGHT TO LEFT so each replacement cannot invalidate the
    # offsets of the ones still to come.
    template = ctx.line
    for n in sorted(vis, reverse=True):
        a, b = vis[n].char_span
        if a < b:
            template = template[:a] + f"[{n}]" + template[b:]

    # Where the splitter's hints and our harvest disagree. Cheap now, and
    # the first thing to read when the output looks wrong.
    h, notes = ctx.hints, []
    kinds = {c.kind for c in ordered}
    if h.is_name_reveal and KIND_NAME not in kinds:
        notes.append("splitter said name-reveal (18/50) but no name was found")
    if h.is_sound and KIND_SOUND not in kinds:
        notes.append("splitter said SFX (60) but no bare sound word was found")
    if h.is_figure and not (kinds & {KIND_NUMBER, KIND_DATE}):
        notes.append("splitter said figure (money/number/date) but none was found")
    if h.is_nonvisual and ordered:
        notes.append(f"splitter said non-visual "
                     f"({sorted(set(h.rule_ids) & STL.NONVISUAL_RULES)}) but "
                     f"{len(ordered)} visualisable(s) were found")

    return VisualisablesEntry(line=ctx.line, template=template,
                              visualisables=vis,
                              rule_ids=list(h.rule_ids), notes=notes)


def _candidate_to_visualisable(cand: Candidate,
                               ctx: Context) -> Visualisable:
    """One Candidate -> one Visualisable: convert token span to character
    span, copy the provenance across, and leave variant / action /
    location as None for phase 4."""
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
        # variant / action / location / identity stay None until the
        # document pass — see SECTION 12.
    )


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 12 — PHASE 4: THE DOCUMENT PASS                           ### #
# ###                                                                     ### #
# ###   Everything above answers "what is in THIS line". The other three  ### #
# ###   fields cannot be answered there:                                  ### #
# ###                                                                     ### #
# ###     action    is in this line, but needs the parse, not the text.   ### #
# ###     location  needs the setting, which is often in an EARLIER line  ### #
# ###               ("In Egypt," ... "there is a valley").                ### #
# ###     variant   needs the whole history of a thing: the tractor is    ### #
# ###               only "the one with yellow paint on it" because of a   ### #
# ###               sentence three lines back.                            ### #
# ###                                                                     ### #
# ###   So this section takes the WHOLE list of entries and one shared    ### #
# ###   doc, and fills them in five passes. Same house rule as the rest   ### #
# ###   of the file: spaCy's parse decides the grammar, WordNet decides   ### #
# ###   the verb classes, abstract_term_resolver decides the pronouns.    ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================


# Which coreference to run. Measured on the tractor narrative, CPU:
#   "off"   recency only        ~0 s    ~50-55% (abstract_term_resolver's
#                                       own figure for its fallback)
#   "fast"  lingmess alone       4.9 s  81.4 CoNLL-F1
#   "full"  all four models     51.0 s  83.6 F1 top model + ensemble vote
# "fast" is the default: it is a published, benchmarked model, it costs
# five seconds, and the ensemble's extra ~2 F1 is not worth 10x the time
# on a per-script batch job. All four checkpoints are cached locally.
COREF_DEFAULT = "fast"

# A verb that CHANGES the thing it acts on, so the thing needs a new
# variant afterwards. WordNet's own hypernyms, first sense only — the same
# precision-over-recall rule as everywhere else in this file. Measured:
# break/shatter/melt/collapse/rust/burn -> True; go/see/pour/crash -> False.
CHANGE_VERB_ROOTS = frozenset({"change.v.01", "change.v.02", "destroy.v.02"})

# A verb of MOTION, so the place it moved to/through is a setting. Used by
# classify_settings() (SECTION 9) as its third signal — see the comment
# there for why the preposition lists alone were not enough. Same WordNet
# lookup, same first-sense-only rule.
MOTION_VERB_ROOTS = frozenset({"travel.v.01", "move.v.02", "move.v.03"})


def resolve_visualisable_details(entries: list[VisualisablesEntry],
                                 doc,
                                 coref: str = COREF_DEFAULT
                                 ) -> list[VisualisablesEntry]:
    """Fill variant / action / location across a whole script. PHASE 4.

    @param entries  every line's VisualisablesEntry, IN SCRIPT ORDER, as
                    produced by create_visualisables_entry(). Build them
                    with keep_pronouns=True if you want "it"/"they"
                    resolved rather than dropped.
    @param doc      the one parsed spaCy Doc the entries were built against
                    (the same object passed as `doc=` per line).
    @param coref    "fast" | "full" | "off" — see COREF_DEFAULT.

    Mutates the entries in place and returns them.

    THE STEPS
        1  action    what each thing is doing, from the dependency parse
        2  identity  collapse mentions so one thing has one history
        3  coref     turn "it"/"they" into the thing they point at
        4  location  the line's setting, carried forward
        5  variant   the running history of what has happened to each thing
    """
    # 1) ACTION — purely within-line, but it needs the parse.
    entries = fill_actions(entries, doc)

    # 2) IDENTITY — "the tractor" and "tractor" are ONE thing, so they can
    #    share one variant history in step 5.
    entries = assign_identities(entries)

    # 3) COREF — "They passed a bee" only means something once "They" is
    #    the tractor and the cat.
    entries = resolve_references(entries, doc, coref)

    # 4) LOCATION — the setting of this line, or the last one seen.
    entries = fill_locations(entries, doc)

    # 5) VARIANT — what has happened to this thing so far in the script.
    entries = fill_variants(entries, doc)
    return entries


# -----------------------------------------------------------------------------
# step 1 — action
# -----------------------------------------------------------------------------

def fill_actions(entries: list[VisualisablesEntry], doc) -> list[VisualisablesEntry]:
    """`action` = the verb this thing is the subject or object of.

    "The tractor ploughed the field" -> tractor.action = "ploughed".
    "The bee flew away"              -> bee.action     = "flew away"
                                        (the particle comes too, because
                                        "flew" and "flew away" are different
                                        shots).

    Weak verbs never become an action: the picture in "the cat WAS on the
    mat" is the mat, not the being. That is STL.WEAK_VERB_LEMMAS again, the
    same list find_action_candidates() uses, so the two agree by
    construction.

    A KIND_ACTION slot is itself a verb and is skipped — it does not have
    an action, it IS one.
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
    """The verb this span hangs off, or None.

    Walks up the dependency tree at most three steps: a noun's head is
    usually its verb directly (nsubj/dobj) or one preposition away
    ("sailed FOR the islands"). Further than that and the verb is in
    another clause and is not what this thing is doing.
    """
    node = doc[start:end].root
    for _ in range(3):
        head = node.head
        # NB: compare .i, never `is`. spaCy builds a new Token proxy on
        # every attribute access, so `tok.head is tok` is False even at
        # the ROOT, and identity tests silently never fire.
        if head.i == node.i:
            return None
        if head.pos_ in {"VERB", "AUX"}:
            return head if head.pos_ == "VERB" else None
        node = head
    return None


def _verb_phrase(doc, verb) -> str:
    """The verb plus its particle — "flew away", "dried up". The particle
    is what makes the action a different picture (STL.PARTICLE_DEPS)."""
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

    "The tractor" (line 1), "the tractor" (line 5) and "tractor" all have
    to become the same key, or step 5 gives each of them its own variant
    history and the paint never sticks.

    Reuses abstract_term_resolver.build_canonical_map(), which already does
    exactly this for the coreference output — groups names by containment
    and elects the longest form of each group as the label. Sharing it
    means the extractor and the resolver can never disagree about whether
    two mentions are the same entity.

    Degrades to the lowercased name when that module will not import.
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


_RESOLVER = None      # abstract_term_resolver handle: None untried, False absent


def _resolver():
    """abstract_term_resolver, imported lazily, or None.

    Lazy because importing it pulls in torch and patches transformers at
    module level — a caller that only wants per-line extraction should
    never pay that.
    """
    global _RESOLVER
    if _RESOLVER is None:
        try:
            import abstract_term_resolver as _atr
            _RESOLVER = _atr
        except Exception as exc:                    # pragma: no cover
            _RESOLVER = False
            print(f"[visualisables] note: abstract_term_resolver "
                  f"unavailable ({exc}) — identities fall back to the bare "
                  f"name and pronouns to recency.")
    return _RESOLVER or None


# -----------------------------------------------------------------------------
# step 3 — coreference
# -----------------------------------------------------------------------------

def resolve_references(entries: list[VisualisablesEntry], doc,
                       mode: str = COREF_DEFAULT) -> list[VisualisablesEntry]:
    """Turn each KIND_REFERENCE slot into the thing it points at.

    "They passed a bee" is not a shot until "They" is "the tractor and the
    cat". The models do that: abstract_term_resolver's ensemble, whose four
    checkpoints are already cached locally (see COREF_DEFAULT for the
    speed/accuracy table).

    For each pronoun we ask the models, highest-weighted first, for the
    cluster that IS this pronoun, then for the best antecedent in it —
    both of those are that module's own find_cluster() / pick_antecedent(),
    not reimplementations. The answer is rendered with its mention_name(),
    which is also what trim_to_searchable_core() is modelled on, so a
    resolved pronoun and a directly-harvested noun phrase come out looking
    the same.

    When no model answers — or mode is "off" — we fall back to recency,
    flagged at that module's own FALLBACK_CONFIDENCE (0.10) because a
    recency guess is only ~50-55% right and downstream should know.

    A reference that cannot be resolved at all keeps its pronoun as the
    `visualisable` and a confidence of 0. It is then the renderer's cue to
    hold whatever is already on screen, which is the correct behaviour for
    an unresolvable "it" anyway.
    """
    refs = [(i, v) for i, e in enumerate(entries)
            for v in e.visualisables.values() if v.kind == KIND_REFERENCE]
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
            vis.confidence = 0.10        # A.FALLBACK_CONFIDENCE
            vis.detector += "+recency"
        vis.identity = vis.visualisable.lower()
    return entries


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
        except Exception as exc:                    # pragma: no cover
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
        # pick_antecedent only rejects PRONOUN mentions, so a cluster can
        # hand back a VERB span and "the cat did not like it" resolves to
        # "revved". A thing on screen is a noun; anything else is not an
        # answer, so fall through to the next model (then to recency).
        if ante is not None and ante.root.pos_ in {"NOUN", "PROPN", "NUM"}:
            return resolver.mention_name(doc, ante)
    return None


def _recency_referent(entries, entry_index: int, token):
    """The last concrete thing mentioned before this line — the fallback
    when no model clustered the pronoun.

    Number-aware: a plural pronoun ("they") takes every concrete thing from
    the most recent line that had more than one, which is what makes
    "The tractor and the cat ... They" come out as both. A singular one
    takes the single most recent thing.

    ~50-55% accurate on its own, which is why the caller stamps it with the
    low confidence rather than presenting it as an answer.
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

    Three rules, in this order:
      1. A setting belongs to its whole SENTENCE, not just to the line it
         appears in. The splitter cuts "The tractor and the cat went down
         the lane" into four lines with the setting in the last one, so a
         purely forward line-by-line carry would leave the tractor with no
         location at all. Sentences are read off the shared doc.
      2. A sentence with no setting of its own inherits the last one seen.
         A script says "In Egypt," once and then talks about the valley for
         six lines; all six are still in Egypt.
      3. A setting is not in itself — the lane's own location stays
         whatever was current before it.

    Which candidate IS the setting when a sentence offers several: the
    highest setting_score wins, so a real place (1.0 — entity label or
    WordNet) beats a merely prepositional one (0.5). That is what stops
    "held a monopoly ON the spice trade" from overriding a genuine
    location, and it is why classify_settings() scores the two halves
    separately instead of OR-ing them into a bool.
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
        # strongest signal wins; ties go to the LEFTMOST, because the first
        # locative in a clause is the one the sentence is actually about
        # ("crashed into the lamppost by accident").
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
    """`variant` = which version of this thing is on screen now.

    The tractor in line 1 and the tractor in line 8 are different pictures:
    by line 8 it has yellow paint on it and a broken windscreen. That is
    what the TODO's own sketch is asking for:

        [tractor][v2-with-yellow-paint-splat-and-broken-window][ploughing]

    So we walk the script in order, keep a running list of change events
    per identity, and stamp every mention with the state AS OF that line
    (this line's own changes included — the shot shows the result).

        first mention, nothing has happened  ->  "base"
        after one change                     ->  "v2: yellow paint"
        after two                            ->  "v2: yellow paint,
                                                  broke"        (v3)

    WHAT COUNTS AS A CHANGE — see _change_events(). Both signals are
    grounded, neither is a word list.
    """
    history: dict[str, list[str]] = {}
    for entry in entries:
        for _n, vis in sorted(entry.visualisables.items()):
            if not vis.identity or vis.kind in {KIND_ACTION, KIND_QUALITY,
                                                KIND_FALLBACK}:
                continue
            past = history.setdefault(vis.identity, [])
            for change in _change_events(doc, vis):
                if change not in past:
                    past.append(change)
            vis.variant = (f"v{len(past) + 1}: " + ", ".join(past)
                           if past else "base")
    return entries


def _change_events(doc, vis) -> list[str]:
    """What happened to this thing in this line that changes how it LOOKS.

    Two signals, both from things already built:

      a) IT IS THE PATIENT OF A CHANGE-OF-STATE VERB. "Its windscreen
         BROKE", "the ship SANK", "the ice MELTED". WordNet says which
         verbs those are (CHANGE_VERB_ROOTS, first sense only) — no verb
         list is written here.

      b) CAUSED MOTION ONTO IT. "she poured yellow PAINT onto the
         TRACTOR" — the tractor is not the object of "poured", the paint
         is, so (a) cannot see it. The construction is: our thing is the
         object of a spatial preposition hanging off a verb that also has
         a direct object. The thing then ACQUIRES that direct object, and
         "yellow paint" becomes the variant description.

    KNOWN MISSES, stated plainly rather than hacked around:
      * "It then CRASHED INTO the lamppost" — a crash plainly damages the
        tractor, but WordNet files crash.v.01 under travel.v.01, and
        widening the sense window to catch it lets far too much else in.
      * "ITS windscreen broke" changes the tractor, not just the
        windscreen. Propagating a part's change to its whole needs the
        possessive resolved to an identity AND a part-of relation; the
        first is available (step 3), the second is not reliable in
        WordNet's meronymy. Left for later.
    """
    if vis.token_span is None:
        return []
    span = doc[vis.token_span[0]:vis.token_span[1]]
    root = span.root
    verb = _governing_verb(doc, *vis.token_span)
    if verb is None:
        return []
    out: list[str] = []

    # (a) patient of a change-of-state verb.
    #     Includes the SUBJECT of an intransitive one: "the windscreen
    #     BROKE", "the ship SANK", "the ice MELTED" all put the thing that
    #     changes in subject position (the unaccusative alternation), and
    #     that is most of how a narration script describes damage.
    has_object = any(ch.dep_ in {"dobj", "obj"} for ch in verb.children)
    is_patient = (root.dep_ in {"dobj", "obj", "nsubjpass"}
                  or (root.dep_ == "nsubj" and not has_object)
                  or (root.dep_ == "pobj" and root.head.head.i == verb.i))
    if is_patient and _is_change_verb(verb):
        out.append(_verb_phrase(doc, verb))

    # (b) caused motion: something was put onto it
    spatial = STL.SPATIAL_LOCATIVE_PREPS | STL.SPATIAL_DIRECTIONAL_PREPS
    if (root.dep_ == "pobj" and root.head.lower_ in spatial
            and root.head.head.i == verb.i):
        for child in verb.children:
            if child.dep_ not in {"dobj", "obj"}:
                continue
            lo = min([child.i] + [g.i for g in child.children
                                  if g.dep_ in {"amod", "compound"}])
            out.append(doc[lo:child.i + 1].text)
    return out


def _is_motion_verb(verb) -> bool:
    """Does WordNet class this verb as motion? MOTION_VERB_ROOTS, first
    sense only, through the same kb() layer. False when WordNet is absent,
    which just means classify_settings() falls back to its prep lists."""
    knowledge = kb()
    if knowledge is None:
        return False
    return knowledge._wn_has_hypernym(verb.lemma_.lower(),
                                      MOTION_VERB_ROOTS, 1, pos="v")


def _is_change_verb(verb) -> bool:
    """Does WordNet class this verb as changing what it acts on?
    CHANGE_VERB_ROOTS, first sense only, through the same kb() layer the
    concreteness check uses. False when WordNet is not installed."""
    knowledge = kb()
    if knowledge is None:
        return False
    return knowledge._wn_has_hypernym(verb.lemma_.lower(),
                                      CHANGE_VERB_ROOTS, 1, pos="v")


# =============================================================================
# =============================================================================
# ###   SECTION 11 — DEBUG AND SELF-TEST                                  ### #
# =============================================================================
# =============================================================================


def debug_print_entry(entry: VisualisablesEntry,
                      candidates: list[Candidate] | None = None) -> None:
    """Print one entry the way abstract_term_resolver prints a resolution:
    the template, then each slot with the detector that found it, its
    concreteness score and its confidence, then — when `candidates` is
    passed — the ones that were DROPPED and by which filter. The dropped
    list is the useful half when calibrating MIN_CONCRETENESS in phase 2.
    """
    print(f"\n{'-' * 70}")
    print(f"line     : {entry.line}")
    print(f"template : {entry.template}")
    if entry.rule_ids:
        print(f"rule ids : {entry.rule_ids}")
    if not entry.visualisables:
        print("  (no visualisables)")
    for n, v in sorted(entry.visualisables.items()):
        conc = f"{v.concreteness:.2f}" if v.concreteness is not None else "   -"
        flag = "  <- SETTING" if v.is_setting else ""
        print(f"  [{n}] {v.visualisable:<26} {v.kind:<9} conc={conc} "
              f"conf={v.confidence:.2f}  {v.detector}{flag}")
        # phase-4 fields, only once the document pass has filled them
        extra = [f"{k}={val}" for k, val in
                 (("variant", v.variant), ("action", v.action),
                  ("location", v.location)) if val]
        if extra:
            print(f"      {'  '.join(extra)}")
    for note in entry.notes:
        print(f"  ! {note}")
    dropped = [c for c in (candidates or []) if c.dropped_by]
    if dropped:
        print("  dropped:")
        for c in sorted(dropped, key=lambda c: c.start):
            print(f"    - {c.detector:<16} {c.dropped_by}")


# The examples from the spec and the TODO, as a test fixture. Expected
# values are what a human says the answer is; phase 3 makes them pass.
_SELFTEST_CASES = [
    # (line, following, preceding, expected visualisables)
    # "would accept 'cat'" for slot 2 — so 'cat' it is. The remaining gap
    # is that we ALSO emit "Molly" as its own slot, though Molly IS the cat:
    # spaCy attaches that apposition to "tractor", not to "cat", so merging
    # on this parse would be worse than not merging. Apposition is phase 4.
    ("The tractor and the cat, Molly, went down the lane.",
     "They passed a bee.", None,
     ["tractor", "cat", "Molly", "lane"]),

    # "sat" is a VERB and is not in WEAK_VERB_LEMMAS, so spec PART 1 makes
    # it a visualisable. The phase-1 expectation here omitted it by mistake.
    ("a jar of nutmeg sat on the shelf",
     None, None,
     ["jar of nutmeg", "sat", "shelf"]),

    # PART 3: the whole point. "monopoly" must NOT come back.
    ("The company held a monopoly on the spice trade.",
     None, None,
     ["company", "spice trade"]),

    # PART 2: an idiom is not a picture, even though its words are.
    ("and the rest is history",
     None, None,
     []),

    # PART 1: number + measure stay glued; the date is its own slot.
    ("In the 1600s, 900 ships sailed for the Banda Islands.",
     None, None,
     ["the 1600s", "900 ships", "sailed", "the Banda Islands"]),

    # The mercy rule: no picture, but long enough to hold the screen.
    ("what had to be done",
     None, None,
     ["what had to be done"]),      # KIND_FALLBACK
]


# The TODO's own worked example, and the reason phase 4 exists: the tractor
# is a different picture by the end than it was at the start.
_PHASE4_NARRATIVE = (
    "The tractor and the cat, Molly, went down the lane. "
    "They passed a bee. "
    "It revved really loud. The cat did not like it. "
    "So she poured yellow paint onto the tractor. "
    "It swished and swerved after having the paint splatter its windscreen. "
    "It then crashed into the lampost by accident. "
    "The yellow and black guy flew away. "
    "Its windscreen broke."
)


def _selftest_phase4(coref: str = "off") -> None:
    """Run the whole two-stage pipeline over _PHASE4_NARRATIVE and print
    every slot with its variant / action / location.

    Defaults to coref="off" so the file stays quick to run; pass
    --coref fast to load the real model, which is what actually resolves
    "It then crashed into the lampost" back to the tractor.
    """
    import contextlib
    import io as _io
    from sentence_splitter import split_text_into_sections_with_meta

    with contextlib.redirect_stdout(_io.StringIO()):
        chunks = split_text_into_sections_with_meta(_PHASE4_NARRATIVE)
    doc = nlp()(_PHASE4_NARRATIVE)
    entries = [create_visualisables_entry(
                   "", None, None, rule_ids=chunk.ids,
                   splitter_meta=chunk.meta, doc=doc,
                   line_token_span=tuple(chunk.meta["span"]),
                   keep_pronouns=True)
               for chunk in chunks]
    entries = resolve_visualisable_details(entries, doc, coref=coref)

    print("=" * 74)
    print(f"phase 4 — the document pass   (coref={coref})")
    print("=" * 74)
    for entry in entries:
        print(f"\n{entry.template}")
        for n, vis in sorted(entry.visualisables.items()):
            print(f"   [{n}] {vis.visualisable:<22} {vis.kind:<9} "
                  f"variant={str(vis.variant):<24} "
                  f"action={str(vis.action):<10} location={vis.location}")

    # The point of the whole exercise: one thing, one running history.
    print("\n" + "-" * 74)
    print("variant history, per identity")
    print("-" * 74)
    seen: dict[str, str] = {}
    for entry in entries:
        for vis in entry.visualisables.values():
            if vis.identity and vis.variant and seen.get(vis.identity) != vis.variant:
                seen[vis.identity] = vis.variant
                print(f"  {vis.identity:<18} -> {vis.variant}")


def _selftest() -> None:
    """Run _SELFTEST_CASES and print each entry with debug_print_entry.

    Deliberately not asserting yet: phase 3 turns the expected lists into
    real assertions once the detectors exist, and phase 2's calibration
    will move some of them.
    """
    print("=" * 74)
    print("visualisables_extractor — self-test")
    print("=" * 74)
    for line, following, preceding, expected in _SELFTEST_CASES:
        entry = create_visualisables_entry(line, following, preceding)
        got = [v.visualisable for _, v in sorted(entry.visualisables.items())]
        debug_print_entry(entry, entry.dropped)
        print(f"  expected : {expected}")
        print(f"  got      : {got}")
        print(f"  -> {'MATCH' if got == expected else 'DIFFERS'}")
    print()


if __name__ == "__main__":
    _argv = sys.argv[1:]
    if "--phase4" in _argv:
        _mode = (_argv[_argv.index("--coref") + 1]
                 if "--coref" in _argv else "off")
        _selftest_phase4(_mode)
    else:
        _selftest()


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   APPENDIX — PHASE 2 RESULTS                                        ### #
# ###                                                                     ### #
# ###   Every question the phase-1 outline raised, and what measuring it  ### #
# ###   actually said. All eight are closed — seven by measurement, the   ### #
# ###   last by decision. The measurements are reproducible: every input  ### #
# ###   is on disk or one curl away (see the bottom of this appendix).    ### #
# ###                                                                     ### #
# ###   1. CONCRETENESS THRESHOLD ....................... 3.0   [CLOSED]  ### #
# ###      Every value in 2.7 .. 4.2 is perfect on the spec's lists       ### #
# ###      (concrete nouns bottom out at 4.26, abstract nouns top out at  ### #
# ###      2.69). 3.0 wins the tie-break on real text: it costs us only   ### #
# ###      "resource" (2.55) and "history" (2.96, already blocked as an   ### #
# ###      idiom), where 3.5 starts eating "district" and "decade".       ### #
# ###                                                                     ### #
# ###   2. IMAGEABILITY vs CONCRETENESS ................. NO    [CLOSED]  ### #
# ###      Glasgow Norms downloaded and tested head to head. Separation   ### #
# ###      is equivalent once you normalise the scales; coverage is not   ### #
# ###      close — 85% of real noun heads vs 48%. r = 0.88 between them,  ### #
# ###      so a second table buys almost nothing. Stay on Brysbaert.      ### #
# ###                                                                     ### #
# ###   3. WORDNET OOV BACKSTOP ............ first sense only   [CLOSED]  ### #
# ###      physical_entity.n.01 vs abstraction.n.06 (NOT .n.01 — that is  ### #
# ###      a different synset and scores 0/10). ~87% on the spec lists,   ### #
# ###      and it degrades with more senses, so take the first only. It   ### #
# ###      is insurance: Brysbaert already covers 85%, and the words it   ### #
# ###      misses are proper nouns that never reach this filter.          ### #
# ###                                                                     ### #
# ###   4. BRYSBAERT BIGRAMS ............................ NO    [CLOSED]  ### #
# ###      All 2,896 fetched and checked. Overlap with the compounds our  ### #
# ###      own scripts produce: zero. Overlap with the spec's four: zero. ### #
# ###      They are household objects ("beach ball"), not script English. ### #
# ###                                                                     ### #
# ###   5. CHUNKER ....................... noun_chunks on sm    [CLOSED]  ### #
# ###      sm and trf give identical noun_chunks on all seven worked      ### #
# ###      examples; trf costs 9x and lost Molly/PERSON. Stay on sm —     ### #
# ###      the model the splitter already has loaded.                     ### #
# ###                                                                     ### #
# ###   6. IDIOMS AS DATA ................ MAGPIE, eventually   [CLOSED]  ### #
# ###      1,756 idiom types, CC-licensed, one jsonl. Extends our 27 by   ### #
# ###      65x and already contains 5 of the spec's 8 examples. Low       ### #
# ###      priority: cheap failure mode. STREUSLE/PARSEME rejected.       ### #
# ###                                                                     ### #
# ###   7. CONTEXT WINDOWING ......... do not window or parse   [CLOSED]  ### #
# ###      sm runs at a flat 0.10 ms/token; a whole script is 0.56 s. The ### #
# ###      splitter has already parsed it and already hands us the token  ### #
# ###      span of every line. Take its doc and its span. This deletes    ### #
# ###      the offset-alignment hazard rather than solving it.            ### #
# ###                                                                     ### #
# ###   8. DROP OR FLAG AN ABSTRACT TERM? ............. DROP    [CLOSED]  ### #
# ###      Decided, not measured. drop_abstract() removes abstract nouns  ### #
# ###      outright and keeps its list -> list signature; no              ### #
# ###      needs_resolution flag. Accepted cost: the spec's PART 3        ### #
# ###      "resolve it into a concrete stand-in" idea has nothing to      ### #
# ###      work from later. Revisit here if that stage gets built.        ### #
# ###                                                                     ### #
# ###   PLUS ONE THING PHASE 2 DID NOT GO LOOKING FOR — see below.        ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================
#
# PHASE 4 — WHAT THE DOCUMENT PASS COSTS AND WHAT IT STILL MISSES
# -----------------------------------------------------------------------------
#   MEASURED, on the tractor narrative and the three real scripts:
#     coref "off"    recency only          ~0 s   resolved "It then crashed"
#                                                 to "its windscreen" (wrong)
#     coref "fast"   lingmess              ~5 s   resolved it to "the tractor"
#                                                 (right), and fixed 3 of 5
#                                                 pronouns the fallback missed
#     coref "full"   four-model ensemble   51 s
#     the pass itself, excluding models:  < 0.02 s per script
#
#   TWO BUGS THIS PHASE FOUND IN ITS OWN FIRST DRAFT, both worth remembering:
#     * `token_a is token_b` NEVER WORKS in spaCy — a fresh Token proxy is
#       built on every attribute access, so even `tok.head is tok` at the
#       ROOT is False. Every identity test silently did nothing. Compare .i.
#     * change-of-state verbs put the thing that changes in SUBJECT position
#       when they are intransitive ("the windscreen broke", "the ship sank").
#       Testing only for dobj/nsubjpass found none of them.
#
#   KNOWN MISSES, each documented at the function that owns it:
#     * "It then CRASHED INTO the lamppost" is not a change event — WordNet
#       files crash.v.01 under travel.v.01.        (_change_events)
#     * "ITS windscreen broke" changes the tractor, not just the windscreen.
#       Part-to-whole propagation needs reliable meronymy.  (_change_events)
#     * "The tractor and the cat ... They" resolves to only one of the two
#       under lingmess; the full ensemble is the lever.  (resolve_references)
#     * apposition: "the cat, Molly" is still two slots, because spaCy
#       attaches Molly to "tractor". Merging on that parse would be worse.
#
# -----------------------------------------------------------------------------
# ENVIRONMENT: WHAT IS ACTUALLY INSTALLED  (measured, .venv, 2026-09-01)
# -----------------------------------------------------------------------------
# Phase 2 had to inventory the venv to run its measurements, and turned up a
# silent hole that affects the EXISTING code, not just this file:
#
#   * THE WORDNET CORPUS WAS NOT DOWNLOADED. nltk was installed; its data was
#     not. VISUAL_RECOMMENDER._wn() swallows that and caches _WN = False, so
#     kb_is_person / kb_is_place / kb_is_collective / kb_is_natural /
#     kb_is_unit / kb_is_time were ALL silently returning False — i.e. the
#     entire WordNet layer of the recommender was inert, failing to seed
#     lists with no error anywhere. Phase 2 fixed it in place with
#     nltk.download("wordnet"); it now resolves. build_wordlists.py does this
#     download as a documented side effect, so it looks like that script was
#     never run to completion in this checkout. WORTH CHECKING ON WHATEVER
#     MACHINE ACTUALLY RENDERS, because nothing will tell you.
#
#   * spaCy: en_core_web_sm OK, en_core_web_trf OK, md/lg absent. We want sm.
#   * ABSENT: benepar, gender_guesser, dateparser, price_parser,
#     number_parser, geonamescache. The last four are shared_text_logic's
#     optional boosters — STL.booster_report() prints them — so written-out
#     numbers ("seventeen million"), currency parsing and city->country are
#     all currently running in degraded mode. find_number_candidates() should
#     expect that and not depend on them.
#   * PRESENT: torch, transformers, maverick, fastcoref — so
#     abstract_term_resolver.py's four-model ensemble can run for phase 4.
#
# -----------------------------------------------------------------------------
# WHAT CHANGED IN THIS FILE AS A RESULT
# -----------------------------------------------------------------------------
#   * CONCRETENESS_APPLIES_TO added — drop_abstract() may judge KIND_THING
#     and nothing else. This is the single most important phase-2 finding:
#     applying the noun-calibrated scale to adjectives and SFX would have
#     silently deleted "ancient", "brilliant", "enormous" and "whoosh", all
#     of which the spec explicitly names as visualisables. The phase-1
#     outline had this half-right (it exempted names/numbers/dates) and
#     would have shipped the bug.
#   * build_context() now takes an optional pre-parsed doc + token span.
#   * MIN_CONCRETENESS, the WordNet root name, the chunker choice and the
#     idiom-corpus plan are all recorded at the function that uses them.
#
# -----------------------------------------------------------------------------
# REPRODUCING THE MEASUREMENTS
# -----------------------------------------------------------------------------
#   concreteness      ../recommender_data.json  (already on disk)
#   brysbaert source  https://raw.githubusercontent.com/ArtsEngine/
#                     concreteness/master/Concreteness_ratings_Brysbaert_
#                     et_al_BRM.txt        (39,955 rows; col 2 == 1 is bigram)
#   glasgow norms     https://raw.githubusercontent.com/lwdovico/
#                     glasgow-norms/main/dataset.csv    (4,682 words, 1-7)
#   magpie            https://raw.githubusercontent.com/hslh/magpie-corpus/
#                     master/MAGPIE_unfiltered.jsonl    (55 MB, 1,756 types)
#   wordnet           .venv/bin/python -c "import nltk; nltk.download('wordnet')"
# =============================================================================
