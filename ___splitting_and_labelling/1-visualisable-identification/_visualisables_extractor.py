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
    _knowledge_base.py       Brysbaert (2014) concreteness 1..5, and the
                             WordNet lookups, via kb()
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

# The shared word lists live one directory up; _knowledge_base.py is here
# beside us. PATHS puts every stage folder on sys.path, so both resolve
# however this file was reached.
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import PATHS  # noqa: F401,E402

# Safe to import eagerly: its spaCy load is lazy, its body is just word lists.
import shared_text_logic as STL          # noqa: E402


# =============================================================================
# SECTION 0 — WIRING TO THE TOOLS WE REUSE
#
# Nothing here decides anything, and it all loads LAZILY: importing this
# module must stay cheap, because myownstuff.py imports it long before it
# has any text to work on.
# =============================================================================

_KB = None                                # _knowledge_base: None=untried, False=absent
_IDIOM_MEMO: tuple = (None, None, None)   # (id(doc), doc, spans) — see _idiom_spans()


def kb():
    """_knowledge_base — concreteness + the WordNet lookups — or None.

    Lazy: it reads ~740 KB of knowledge_base_data.json at import time.
    None means those checks are OFF and we degrade to spaCy alone.
    """
    global _KB
    if _KB is None:
        try:
            import _knowledge_base as _kb
            _kb.kb_concreteness("test")          # force the data file to load
            _KB = _kb
        except Exception as exc:
            _KB = False
            print(f"[visualisables] note: _knowledge_base unavailable "
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

# How far up the tree to look for a comparison frame ("more ... THAN ..."),
# which a thing may sit deep inside:  gold <- in <- weight <- than <- more.
COMPARISON_FRAME_DEPTH = 6

# Longer than this is a sentence, not something you would type into a stock
# search box.  e.g. "a jar of nutmeg that sat on the shelf" --> "jar of nutmeg"
MAX_PHRASE_TOKENS = 6

# Does an adjective belong in the thing's NAME, or only in its `variant`?
#     a VOLCANIC archipelago is a different THING from an archipelago
#     a REMOTE   archipelago is the same thing, differently
# True keeps the KIND-forming ones in the name (WordNet's adj.pert class, the
# relational adjectives — "of or pertaining to <noun>", which is what makes
# them name a subtype). Set it False and NO adjective ever stays: every one
# of them becomes variant, which is the safe answer if the adj.pert rule is
# ever caught being wrong in a way that matters.
#   e.g. True   "A tiny, incredibly remote volcanic archipelago"
#                   --> "volcanic archipelago", variant "tiny, incredibly remote"
#        False  --> "archipelago",  variant "tiny, incredibly remote, volcanic"
#
# NOTHING IS LOST BY MOVING AN ADJECTIVE — this is why the split is safe. The
# search layer can always put the two back together: "yellow" + "paint" is
# still "yellow paint" when it wants a picture of one. What the split ADDS is
# the knowledge of WHICH half is the thing, so a later segment can change how
# it looks without changing what it is.
#
# COMPOUND NOUNS ARE NOT ADJECTIVES and always stay, whatever this says:
# "kitchen cupboard", "whale skulls", "spice trade" — two words, one thing.
KEEP_KIND_FORMING_ADJECTIVES = True


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

# The kinds that earn a SLOT. Everything else is an attribute OF a slot: a
# verb is the thing's `action`, an adjective its `variant`, and neither is a
# thing to film.
#   e.g. "If you open your kitchen cupboard"
#            [1] the viewer        deictic   action="open"
#            [2] kitchen cupboard  thing     action="open"
#        — "open" itself is not a third slot; it is already on both of them.
# KIND_ACTION and KIND_QUALITY are still HARVESTED (they are the definition
# of a strong verb / a strong adjective that fill_actions() and the variant
# path lean on) and still absorb into the noun phrase around them. They are
# only refused a slot of their own, by drop_attribute_kinds().
SLOT_KINDS = CONCRETE_KINDS | {KIND_SOUND, KIND_REFERENCE,
                               KIND_DEICTIC, KIND_FALLBACK}


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

    # amount        HOW MANY of `visualisable` are on screen. Read off the
    #               parse by fill_amounts(); ALWAYS 1 unless the segment
    #               actually counted them, because "a tractor" and "the
    #               tractor" are both one tractor.
    #                   "three ships"  -->  visualisable "ships", amount 3
    #                   "900 ships"    -->  visualisable "ships", amount 900
    #               NOT "the number in the text": a money or measure slot is
    #               ONE picture of a figure, so "two dollars" and "$2 million"
    #               keep amount 1 and carry their number in the name.
    #               A bare plural with no numeral ("the bees") is also 1 —
    #               wrong, but the only alternative is changing the type, and
    #               `plural` off spaCy's Number=Plur is the cheap fix if the
    #               renderer ever asks for it.
    amount: int = 1

    # hypothetical  the segment did not say this HAPPENED — it is inside an
    #               "if", a simile or a modal.  "The ground looked AS IF an
    #               ocean had dried up" — there was no ocean.
    #               NOT a drop: these are usually the most visual sentences in
    #               a script, and what to do about it is the renderer's
    #               decision (film it as a dream, a sketch, a ghost), not this
    #               file's. All this does is say so.
    hypothetical: bool = False

    # trimmed_description   the adjectives cut off the front of the name
    #               because they describe the LOOK rather than name the KIND.
    #               A working, not an answer: fill_variants() turns it into
    #               `variant` and nothing else should read it.
    #               e.g. "tiny, incredibly remote volcanic archipelago"
    #                        visualisable "volcanic archipelago"
    #                        trimmed_description ["tiny", "incredibly remote"]
    trimmed_description: list = field(default_factory=list)

    def is_concrete(self) -> bool:
        """A thing, not an action or a quality."""
        return self.kind in CONCRETE_KINDS

    def as_dict(self) -> dict:
        """The json row: the answer first, then just enough workings.

        e.g. {"visualisable": "tractor", "variant": "yellow paint",
              "action": "crashed", "location": "lampost", "kind": "thing",
              "identity": "tractor", "is_setting": False, "confidence": 0.9,
              "owner": None, "amount": 1, "hypothetical": False}

        owner is here and not left on the object because it is an ANSWER, not
        a working: "the tractor's windscreen" is only half the fact, and the
        other half — that the windscreen belongs to the tractor, so damaging
        it damages the tractor — is unreachable to anything holding the map.

        surface is here for the same reason: on a KIND_REFERENCE slot the
        answer is what the pronoun RESOLVED to, and "which word was that?"
        is unanswerable from the map without it ("[1] was once covered" does
        not say whether [1] read "It" or "They"). 3-manual-tagging shows the
        viewer both halves.

        The rest of the record (detector / char_span / concreteness /
        token_span / setting_score) stays on this object, for anything that
        holds the Visualisable rather than the map.
        """
        return {
            "visualisable": self.visualisable,
            "surface": self.surface,
            "variant": self.variant,
            "action": self.action,
            "location": self.location,
            "kind": self.kind,
            "identity": self.identity,
            "is_setting": self.is_setting,
            "confidence": round(self.confidence, 3),
            "owner": self.owner,
            "amount": self.amount,
            "hypothetical": self.hypothetical,
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

    # "at night" — how the SETTING looks in this segment, not a slot of its
    # own. fill_variants() is what does something with it.
    time_descriptions: list = field(default_factory=list)

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
    hypothetical: bool = False          # inside an "if" / a simile / a modal
    dropped_by: str | None = None       # e.g. "abstract(2.69)", "weak-verb"
    # the describing adjectives trim_to_searchable_core() took OFF the front,
    # left to right — "tiny", "incredibly remote". They are not thrown away:
    # they are the thing's `variant`, and fill_variants() is where they land.
    trimmed_description: list = field(default_factory=list)


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
    attributes_only: bool = False     # every survivor was an action/quality —
                                      # set by drop_attribute_kinds(), read by
                                      # apply_length_fallback()
    # "at night", "in the morning" — a time of day is how the SETTING looks,
    # so move_time_to_setting() puts it here and fill_variants() hands it to
    # whatever this segment is standing in.
    time_descriptions: list = field(default_factory=list)

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


# -----------------------------------------------------------------------------
# IS THIS WORD A PICTURE AT ALL?
#
# Three word classes, three computed tests, and NOT ONE WORD WRITTEN DOWN.
# Everything below names a TYPE — a WordNet supersense, a taxonomy node, a
# spaCy tag — so it works on every word in English, including the ones no
# script has used yet. A rule that named "contested" or "worth" would fix one
# script and no other.
#
# BRYSBAERT IS NOT USED HERE, and must not be: measured on the scripts'
# adjectives it does not separate the classes at all —
#     pictures      rusted 4.44  yellow 4.30  broken 4.11  little 3.67
#                   remote 3.44  old 2.72  ancient 2.04
#     not pictures  single 3.27  modern 2.31  important 2.14  famous 2.07
#                   worth 1.89   contested (no rating at all)
# single 3.27 outranks ancient 2.04, so no threshold exists. That is what
# CONCRETENESS_APPLIES_TO has always been saying; this is the evidence.
# -----------------------------------------------------------------------------

# 5a — NOUNS. WordNet's supersense says what TYPE of thing a word is, and six
# of the 26 are decisive: nothing filed under them is ever filmable.
#   e.g. noun.attribute weight 3.94  size 3.13     noun.state    thing 3.17
#        noun.cognition idea 1.61                  noun.relation percentage
# All of weight / size / thing PASS Brysbaert's 3.0 today — which is exactly
# the "nutmeg's weight" bug. The supersense overrules the rating.
#
# THE OTHER 20 GO BOTH WAYS, so Brysbaert stays in charge of them. Measured,
# and written down so it is not rediscovered:
#   noun.possession    gold 4.81 / resource 2.55
#   noun.act           battle 4.00, war 3.63 / journey 2.57
#   noun.communication postcard 4.93, note 4.61, letter 4.70 / song, story
#   noun.time          spring 3.89 (a coil) / moment 1.61
# Making those decisive would delete the clockmaker's postcards and note, a
# coil spring, and a battle. Do not.
#
# EVENT AND ACT NOUNS are the pair most likely to be argued about next:
# "journey" 2.57, "revolution" 2.87, "trade" 3.08, "voyage" 3.43 name spans
# of time rather than framings, and Brysbaert already drops the low ones —
# but "battle" 4.00 really is filmable, so noun.act / noun.event cannot be
# made decisive either. Watch it on the whales and rome scripts, which are
# full of them, and only then decide. Deliberately not acted on.
NEVER_FILMABLE_SUPERSENSES = frozenset({
    "noun.attribute", "noun.cognition", "noun.feeling",
    "noun.motive", "noun.relation", "noun.state"})

# CONTAINER AND PARTITIVE HEADS. "a handful of spices", "a lot of ships", "a
# series of raids", "the rest of the fleet" — the head noun is a measuring
# word and the picture is the OBJECT of "of". Same six-supersense vocabulary,
# used the other way round: these are the types that COUNT things rather than
# being things. promote_partitives() is the rule.
CONTAINER_SUPERSENSES = frozenset({
    "noun.quantity", "noun.group", "noun.attribute", "noun.relation"})

# ROLE NOUNS — "the owner OF the tractor", "a member OF the crew". They point
# at somebody without saying what they look like, and the thing they relate
# to is the picture.
#
# OFF, and here is the evidence rather than an opinion: the test that finds
# "owner" (noun.person with an `of` complement) finds "the King OF Spain" and
# "the Queen OF England" too, and those ARE portraits — promoting them would
# film the country instead of the monarch. WordNet has no class that
# separates a role from a title, and none of the five scripts contains either
# shape, so there is nothing to measure the discriminator against yet. Turn
# it on when a script produces one. The noun.relation half ("the rest of the
# fleet") is already live above, because that supersense IS the answer.
PROMOTE_ROLE_NOUNS = False

# 5b i — PARTICIPLES. An adjective wearing a verb's past participle is a verb
# in adjective's clothes, so ask what the VERB does.
#   e.g. rust -> verb.change   wrinkle -> verb.contact   break -> verb.change
#        contest -> verb.communication   (a picture of nothing)
# THREE senses, not one: "bury" is verb.perception first and verb.contact
# only third, and "the site was buried" is a picture.
PHYSICAL_VERB_SUPERSENSES = frozenset({
    "verb.change", "verb.contact", "verb.body",
    "verb.motion", "verb.creation", "verb.weather"})

# 5b ii — ATTRIBUTE POINTERS. A WordNet adjective often names the property it
# measures, and the NOUN taxonomy splits the properties you can see from the
# ones you cannot. Measured, 13 of 13 correct on four node names:
#   A LOOK      little->size  narrow->width  old->age  loud->volume
#               black->value(colour)  broken->integrity  dead->animation
#                   ... all reach property.n.02 or state.n.02
#   NOT A LOOK  important->importance  single->individuality  afraid->fear
#               modern->modernity
#                   ... all also reach one of these four
# "also" is the operative word — "afraid" reaches state.n.02 AND feeling.n.01.
# Whichever of these four appears, wins.
NON_VISUAL_ATTRIBUTE_ROOTS = frozenset({
    "quality.n.01", "trait.n.01", "feeling.n.01", "temporal_property.n.01"})
VISUAL_ATTRIBUTE_ROOTS = frozenset({"property.n.02", "state.n.02"})

# 5b iii — RELATIONAL ADJECTIVES: the ones that need a complement to mean
# anything. "It was worth more THAN its weight in gold" states a relation; it
# does not say how anything LOOKS. It is not a participle and has no
# attribute pointer, so (i) and (ii) both wave it through — but the PARSE
# gives it away. Measured:
#     worth    acomp, subtree contains a `prep` ("than ...")   -> drop
#     famous   acomp, no children                              -> keep
#     loud     acomp, only an advmod ("really")                -> keep
#     ancient  amod,  no children                              -> keep
# SUBTREE, not children: spaCy hangs the "than" phrase off "more", which
# hangs off "worth".
RELATIONAL_ADJ_DEPS = frozenset({"prep", "pcomp", "ccomp"})

# How many of an adjective's senses to read. Adjectives have few, and
# adj.pert is never buried deep, so this is "all of them" in practice.
ADJ_SENSE_WINDOW = 10

# 5c — VERBS, the same supersense test as a backstop. WEAK_VERB_LEMMAS is a
# good list and it is shared with the sentence splitter, so it stays — but it
# is a list, so it can only ever know the verbs someone typed into it.
#   e.g. IN THE LIST ALREADY  cost -> verb.stative   say -> communication
#        CAUGHT BY THIS       swear -> verb.communication at all three senses
#        UNAFFECTED           open -> contact  grow -> change  sail -> motion
# ALL of the first three senses must be non-physical, so a verb with one
# filmable sense anywhere near the top keeps its action.
NON_PHYSICAL_VERB_SUPERSENSES = frozenset({
    "verb.stative", "verb.cognition", "verb.communication",
    "verb.possession", "verb.social", "verb.emotion"})


def _supersenses(word: str, n_senses: int, pos=None) -> list:
    """WordNet's lexnames for a word's first n senses, or [] when WordNet is
    not installed. [] always means NO OPINION — every caller keeps the word."""
    knowledge = kb()
    if knowledge is None:
        return []
    return knowledge._wn_supersenses(word, n_senses, pos=pos)


def is_never_filmable_noun(head: str) -> bool:
    """Is this noun's FIRST sense one of the six supersenses that never hold
    a picture?  e.g. "weight" noun.attribute -> True   "gold" -> False

    First sense only, matching _wordnet_verdict(): widening it starts eating
    the physical sense of words whose abstract sense happens to come first.
    """
    senses = _supersenses(head, 1)
    return bool(senses) and senses[0] in NEVER_FILMABLE_SUPERSENSES


def is_visual_quality(doc, tok) -> bool:
    """Does this adjective change what the picture LOOKS like?

    THE ONE GATE, THREE CALLERS: find_quality_candidates() (so the harvest is
    honest), _variant_descriptions() and trim_to_searchable_core()'s cast-off
    adjectives (so the variant is too). Written once because otherwise a word
    refused a slot walks straight back in as a variant.

        "rusted"     -> True    a look
        "contested"  -> False   a verb of COMMUNICATION in adjective's clothes
        "worth"      -> False   a relation, and it needs a "than" to state it
        "important"  -> False   points at importance, which is a quality
        "yellow"     -> True    no pointer, no complement — nothing against it

    Four tests, in order. Anything WordNet has no opinion on is KEPT: a wrong
    drop blanks the description, a wrong keep costs one adjective.
    """
    lemma = tok.lemma_.lower()
    word = tok.lower_

    # 0. the shared list, so this gate subsumes the old weak-adjective test
    #    and the three callers can ask one question instead of two.
    if lemma in STL.WEAK_ADJ_LEMMAS:
        return False

    # i. a PARTICIPLE is decided by the verb underneath it.
    adj_classes = _supersenses(word, 3, pos="a")
    if tok.tag_ == "VBN" or "adj.ppl" in adj_classes:
        verb_senses = _supersenses(word, 3, pos="v") or _supersenses(lemma, 3,
                                                                     pos="v")
        if verb_senses:
            return any(s in PHYSICAL_VERB_SUPERSENSES for s in verb_senses)

    # ii. the ATTRIBUTE it measures, if WordNet says it measures one.
    knowledge = kb()
    roots = knowledge._wn_attribute_roots(word) if knowledge else set()
    if not roots and lemma != word:
        roots = knowledge._wn_attribute_roots(lemma) if knowledge else set()
    if roots & NON_VISUAL_ATTRIBUTE_ROOTS:
        return False
    if roots & VISUAL_ATTRIBUTE_ROOTS:
        return True

    # iii. no pointer and not a participle: does it need a COMPLEMENT to mean
    #      anything? Then it states a relation, not a look.
    if any(t.dep_ in RELATIONAL_ADJ_DEPS
           for t in tok.subtree if t.i != tok.i):
        return False

    # iv. nothing against it.
    return True


# The CARDINAL NUMERALS, so a count can be read as a number.
#
# This is a word list, and the plan that asked for `amount` forbids those —
# so here is why it is allowed, and where the line is. English's cardinals
# are a CLOSED GRAMMATICAL CLASS, exactly like the determiners and the
# hedges in STL.APPROXIMATOR_WORDS that the same plan permits: the language
# has about thirty of them and has not gained one in a thousand years.
# Reading it is closer to reading a tag than to writing a lexicon, and there
# is no computed alternative — spaCy says "three" is a NUM but not that it
# is 3, and WordNet does not carry values either.
# It is NOT a licence to write any other list: every noun, verb and
# adjective decision in this file stays computed.
CARDINAL_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000, "million": 1000000,
    "billion": 1000000000,
}


def _as_count(tok) -> int | None:
    """A numeral token as an integer, or None when it will not read as one.
    e.g. "900" -> 900   "3,000" -> 3000   "three" -> 3   "some" -> None"""
    digits = tok.text.replace(",", "").replace("_", "")
    if digits.isdigit():
        return int(digits)
    return CARDINAL_WORDS.get(tok.lower_)


def is_bare_count(doc, start: int, end: int) -> bool:
    """A number with nothing to count is not a picture.

    KIND_NUMBER exists because a figure goes on screen as a counter or a bar:
    "two dollars", "15 metres", "$2 million", "30 percent" — there the NUMBER
    is the picture. A bare cardinal is not:
        "in ONE place on Earth"        the picture is the place
        "Every fossil was intact except ONE"   the picture is the fossil
        "900 ships"                    the picture is the ships, 900 of them
    In all three the count belongs in the thing's `amount`, which
    fill_amounts() reads straight off the parse — so nothing is lost by
    refusing the slot.

    Anything glued to a measure word or a currency keeps its slot.
    """
    span = doc[start:end]
    if any(t.lower_ in STL.MEASURE_NOUNS for t in span):
        return False
    if any(t.ent_type_ in {"MONEY", "PERCENT", "QUANTITY"} for t in span):
        return False
    if any(t.is_currency for t in span):
        return False
    return True


# The two negators spaCy does NOT tag `neg`: the determiner "no" ("no
# ships") and the preposition "without" ("without water"). Both say exactly
# what the `neg` dependency says, wearing different grammar. Closed-class
# function words, the same exception STL.APPROXIMATOR_WORDS is — and the only
# two in English that negate a NOUN PHRASE rather than a clause.
NEGATING_FUNCTION_WORDS = frozenset({"no", "without"})


def is_negated(tok) -> bool:
    """Is what this word says being DENIED?

        "it was NOT afraid of us"   the script says it was not. There is no
                                    picture of "not afraid" — the absence of
                                    a state is not a shot — so the
                                    description is dropped, never inverted.
        "It NEVER rusted"           same, on a verb.
        "NO ships sailed"           same fact, wearing a determiner.
        "a valley WITHOUT water"    same fact, wearing a preposition.

    THE DEPENDENCY IS THE TEST. spaCy tags "not" / "n't" / "never" as `neg`
    outright; the other two are the closed-class pair above.

    A PREDICATE ADJECTIVE IS DENIED BY ITS COPULA: in "it was not afraid" the
    `neg` hangs off "was", not off "afraid", so an acomp/oprd adjective asks
    its head too. Deliberately ONLY those two deps — climbing from any token
    to any head would make "he did not say the ship SANK" a negated sinking.
    """
    for child in tok.children:
        if (child.dep_ == "neg"
                or child.lower_ in STL.NEGATION_TOKENS
                or (child.dep_ == "det" and child.lower_ in NEGATING_FUNCTION_WORDS)):
            return True
    if tok.dep_ == "pobj" and tok.head.lower_ in NEGATING_FUNCTION_WORDS:
        return True
    if tok.pos_ == "ADJ" and tok.dep_ in {"acomp", "oprd"}:
        head = tok.head
        if head.i != tok.i and any(ch.dep_ == "neg" for ch in head.children):
            return True
    return False


# IRREALIS — the grammar of things that did not happen.
#
# SUBORDINATORS that open a hypothetical clause, as a `mark`:
#     "IF you open your kitchen cupboard"      it is not open
#     "as IF an ocean had dried up"            there was no ocean
#     "as THOUGH the ship were sinking"        it is not sinking
#     "LIKE a bomb had gone off"               no bomb
# ("as if" arrives as two tokens and "if" is the one tagged `mark`, so it is
#  caught either way round.)
#
# MODAL AUXILIARIES in their irrealis senses. spaCy tags all nine English
# modals MD, and "will"/"can"/"must" are not irrealis — so the tag alone is
# too wide and these four are named. English has had the same nine modals
# for centuries: another closed grammatical class, like the determiners.
IRREALIS_MARKERS = frozenset({"if", "though", "unless", "whether", "like"})
IRREALIS_MODALS = frozenset({"would", "could", "might", "may"})


def is_hypothetical(doc, start: int, end: int) -> bool:
    """Did the segment say this HAPPENED, or only imagine it?

        "The ground looked as if an ocean had simply dried up around them."
             -> the ocean is hypothetical. There is no ocean in this video.
        "If you open your kitchen cupboard"
             -> so is the cupboard, though harmlessly: it is the viewer's own.

    NOT A DROP. These are often the most visual sentences in a script, and
    the right thing to do with one — film it as a dream, a sketch, a ghost,
    or just film it — belongs to whatever draws the shot. This only says so.

    THE TEST, climbing from the candidate to the clause it is in:
        a `mark` child in IRREALIS_MARKERS on any verb above it
        an irrealis modal `aux` on any verb above it
    Same climb as _in_comparison_frame(), same depth, and for the same
    reason: the marker sits on the clause, not on the thing.

    KNOWN OVER-FIRE, stated rather than hacked: "but I COULD smell the wax"
    is an ability, not a supposition, and it is flagged anyway — English
    spells the two the same and nothing in the parse separates them. It costs
    nothing here because this is a FLAG: a renderer that films a flagged
    segment straight is no worse off than before the flag existed.
    """
    node = doc[start:end].root
    for _ in range(COMPARISON_FRAME_DEPTH):
        for child in node.children:
            if child.dep_ == "mark" and child.lower_ in IRREALIS_MARKERS:
                return True
            if child.dep_ in {"aux", "auxpass"} and child.lower_ in IRREALIS_MODALS:
                return True
        head = node.head
        if head.i == node.i:
            return False
        node = head
    return False


def is_kind_forming_adjective(tok) -> bool:
    """Does this adjective make a DIFFERENT KIND of thing, or only say how
    this one looks?

        volcanic archipelago   a different thing from an archipelago  -> True
        remote archipelago     the same thing, differently            -> False

    WordNet answers it with a class, not a list. A RELATIONAL (pertainymic)
    adjective is one it files as adj.pert: it means "of or pertaining to
    <noun>", it is DERIVED from that noun, and it therefore names a subtype.
    Measured on the scripts' own adjectives:
        adj.pert   volcanic, royal, solar, medieval        -> forms a kind
        adj.all    tiny, remote, old, huge, ancient, loud, black, yellow,
                   wooden, rusted, wrinkled, broken        -> describes a look
    ANY sense counts: "royal" is adj.pert at senses 1 and 2 and adj.all at 3,
    and it is still forming a kind.
    """
    return "adj.pert" in _supersenses(tok.lower_, ADJ_SENSE_WINDOW, pos="a")


def is_filmable_action(tok) -> bool:
    """Is this verb worth attaching to a thing as its `action`?

    STL.WEAK_VERB_LEMMAS / _FORMS first — the shared list, which the sentence
    splitter and the tagger also read, so the three cannot disagree about
    what a weak verb is. Then the computed backstop for every verb nobody
    ever typed into it: all three of its first senses non-physical means the
    picture is not in the verb.
        e.g. KEEP  open (contact)  grow (change)  sail (motion)  sound
                   (perception — not in the set, and rightly: you can film
                   something making a noise)
             DROP  swear (communication x3)
    """
    if (tok.lemma_.lower() in STL.WEAK_VERB_LEMMAS
            or tok.text.lower() in STL.WEAK_VERB_FORMS):
        return False
    senses = _supersenses(tok.lemma_.lower(), 3, pos="v")
    return not (senses and all(s in NON_PHYSICAL_VERB_SUPERSENSES
                               for s in senses))


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
       2b  promote the container phrases ("a lot of ships" -> ships)
        3  drop the PART 2 non-visualisables (pronouns, weak words, bare
           units, ...)
        4  drop the PART 3 abstract nouns, and the counts with nothing to
           count ("one place" -> the place is the picture, not the one)
        5  trim each survivor to its searchable core  ("a jar of" -> "jar")
        6  split "X and Y" into two candidates
        7  resolve overlaps — one picture per piece of text
        8  refuse a slot to the attributes (a verb, a bare adjective)
        9  order left to right, which fixes the slot numbers [1] [2] [3]
       10  mercy rule: nothing survived, but it is 4+ real words
       11  classify settings, then build the template + the map
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

    # 2b) PROMOTE — "a lot of ships" is a picture of ships. Before the
    #     filters, or "lot" is judged and the ships go down with it.
    candidates = promote_partitives(candidates, ctx)

    # 3) FILTER — PART 2. Nothing that only POINTS at a picture.
    candidates = drop_blocked(candidates, blocked)
    candidates = drop_bare_measures(candidates, ctx)
    candidates = move_time_to_setting(candidates, ctx)
    candidates = drop_non_visualisable(candidates, ctx)

    # 4) FILTER — PART 3. Nothing you cannot point a camera at.
    candidates = drop_abstract(candidates, ctx)
    candidates = drop_bare_counts(candidates, ctx)
    candidates = drop_negated(candidates, ctx)

    # 5) TIDY — cut each down to what you would type into a search box.
    candidates = trim_to_searchable_core(candidates, ctx)

    # 6) SPLIT — "dust and rock" is two pictures, not one.
    candidates = split_coordinated_candidates(candidates, ctx)

    # 7) DEDUPE — the harvesters overlap by design; pick one per span.
    candidates = resolve_overlaps(candidates, ctx)

    # 8) ATTRIBUTES — a verb is the thing's `action`, an adjective its
    #    `variant`. Neither is a slot. AFTER the dedupe, so an adjective
    #    inside a noun phrase is still absorbed the way it always was and
    #    only the ones that survived on their own are cut.
    candidates = drop_attribute_kinds(candidates, ctx)

    # 9) ORDER — left to right.
    candidates = order_candidates(candidates)

    # 10) FALLBACK — the mercy rule, so a real segment is never left empty.
    candidates = apply_length_fallback(candidates, ctx)

    # 11) BUILD — mark settings and hypotheticals, punch out the template,
    #     number the slots.
    candidates = classify_settings(candidates, ctx)
    candidates = flag_hypotheticals(candidates, ctx)
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

    Test is ADJ and is_visual_quality() — the shared gate, which starts with
    STL.WEAK_ADJ_LEMMAS (where "many", "several", "possible" get thrown out)
    and then asks WordNet whether the word describes a LOOK at all. That is
    what keeps "contested" and "worth" out of here, and out of `variant`,
    which reads the same gate.

    An adjective INSIDE a noun chunk we already harvested ("the OLD wooden
    ship") must not become its own slot — emit it anyway, resolve_overlaps()
    absorbs it. Since STEP 1 it never stands alone either: what survives here
    is dropped by drop_attribute_kinds() and lives on as the thing's
    `variant`. It is harvested because THIS function is the definition of a
    strong adjective that the variant path leans on.
    """
    out = []
    for tok in ctx.target_tokens():
        if tok.pos_ != "ADJ":
            continue
        if not is_visual_quality(ctx.doc, tok):
            continue
        out.append(Candidate(tok.i, tok.i + 1, KIND_QUALITY,
                             "quality:adj", confidence=0.6))
    return out


def find_action_candidates(ctx: Context) -> list[Candidate]:
    """PART 1 bullet 8 — strong, filmable verbs. KIND_ACTION.
    e.g. "jumped", "sailed", "exploded", "sank"

    Test: VERB and is_filmable_action() — WEAK_VERB_LEMMAS / WEAK_VERB_FORMS
    (which remove be/have/do/get/make/know/seem, the verbs whose picture
    always lives in the noun next to them), and then the supersense backstop
    for every verb that list has never heard of.

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
        if not is_filmable_action(tok):
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


def promote_partitives(candidates: list[Candidate],
                       ctx: Context) -> list[Candidate]:
    """THE HEAD NOUN IS NOT ALWAYS THE THING.

        "a handful of spices"     film the spices, not a handful
        "a lot of ships"          film the ships
        "a series of raids"       film the raids
        "the rest of the fleet"   film the fleet
        "the owner of the tractor"  film the tractor    (PROMOTE_ROLE_NOUNS)

    A container word measures or groups; it is not itself a picture. So when
    the head's supersense is one that COUNTS things (CONTAINER_SUPERSENSES)
    and it has an "of" complement with a noun in it, the candidate becomes
    that noun. The count is not lost — fill_amounts() reads it off the same
    parse and puts it in `amount`, which is step 4's partitive rule doing
    exactly this job for the numeral case ("One of them"). The two are the
    same rule and are written next to each other on purpose.

    RUNS BEFORE THE FILTERS, and that is the whole trick: "a lot of ships"
    would otherwise be dropped for "lot" being abstract, taking the ships
    with it. Promote first, judge the thing you actually meant second.
    """
    doc, out = ctx.doc, []
    for c in candidates:
        if c.kind != KIND_THING:
            out.append(c)
            continue
        root = doc[c.start:c.end].root
        senses = _supersenses(root.lemma_.lower(), 1)
        first = senses[0] if senses else None
        promotable = (first in CONTAINER_SUPERSENSES
                      or (PROMOTE_ROLE_NOUNS and first == "noun.person"))
        if not promotable:
            out.append(c)
            continue
        prep = next((ch for ch in root.children
                     if ch.dep_ == "prep" and ch.lower_ == "of"), None)
        obj = next((ch for ch in prep.children if ch.dep_ == "pobj"),
                   None) if prep is not None else None
        if obj is None or obj.pos_ not in {"NOUN", "PROPN", "PRON"}:
            out.append(c)
            continue
        indices = [t.i for t in obj.subtree if c.start <= t.i < c.end]
        if not indices:
            out.append(c)
            continue
        c.start, c.end = min(indices), max(indices) + 1
        c.detector += "+partitive"
        out.append(c)
    return out


def move_time_to_setting(candidates: list[Candidate],
                        ctx: Context) -> list[Candidate]:
    """"AT NIGHT" IS NOT A THING, IT IS HOW THE PLACE LOOKS.

        "the wind sounds like whale song AT NIGHT"
             -> not a slot. The valley, at night.
        "IN THE MORNING she wound it"
             -> the workshop, in the morning.

    So the phrase is dropped from the slots and handed to fill_variants(),
    which adds it to the variant of whatever this segment is standing in — a
    night scene being a real look, and one the renderer can actually draw.

    NARROW ON PURPOSE, because noun.time is the supersense the plan that
    asked for this warned twice about: WordNet's FIRST sense of "spring" is
    the season, and the clockmaker's spring is a coil. So this fires only on
    a TEMPORAL ADJUNCT — the object of a preposition hanging off a VERB,
    which is what "at night" is and what "He sent back a spring" (a dobj) is
    not. Dates keep their own kind and never reach here.
    """
    doc, kept = ctx.doc, []
    for c in candidates:
        if c.kind != KIND_THING:
            kept.append(c)
            continue
        root = doc[c.start:c.end].root
        senses = _supersenses(root.lemma_.lower(), 1)
        adjunct = (root.dep_ == "pobj" and root.head.pos_ == "ADP"
                   and root.head.head.pos_ in {"VERB", "AUX"})
        if not (senses and senses[0] == "noun.time" and adjunct):
            kept.append(c)
            continue
        c.dropped_by = "time-as-variant"
        # ...unless a DATE candidate already covers it. Dates keep their own
        # kind (spec PART 1 bullet 5), and recording "at night" as the
        # setting's look as WELL as a date slot would say it twice.
        if any(o.kind == KIND_DATE and o.dropped_by is None
               and o.start < c.end and o.end > c.start for o in candidates):
            continue
        ctx.time_descriptions.append(
            " ".join(doc[root.head.i:c.end].text.split()).strip(" ,.;:!?"))
    return kept


def drop_bare_measures(candidates: list[Candidate],
                       ctx: Context) -> list[Candidate]:
    """A UNIT WITH NO NUMBER IS NOT A PICTURE. "metres", "percent",
    "degrees", "decades" on their own measure nothing and show nothing.

    Half of this already exists: find_number_candidates() glues a measure
    word to its numeral precisely so "900 SHIPS" cannot come apart. This is
    the other half — the ones that arrive with no numeral anywhere near them,
    as ordinary noun phrases.

    STL.MEASURE_NOUNS is the test, and it is the one place in this file where
    a word list is exactly the right tool: units are a finite list BY
    DEFINITION. A candidate with a numeral in it or on it keeps its slot, and
    dates never reach here — they are their own kind.
    """
    doc, kept = ctx.doc, []
    for c in candidates:
        if c.kind != KIND_THING:
            kept.append(c)
            continue
        span = doc[c.start:c.end]
        root = span.root
        counted = (any(t.pos_ == "NUM" or t.like_num for t in span)
                   or any(ch.dep_ == "nummod" for ch in root.children))
        if root.lemma_.lower() in STL.MEASURE_NOUNS and not counted:
            c.dropped_by = "bare-measure"
        else:
            kept.append(c)
    return kept


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
        elif root.pos_ == "ADJ" and not is_visual_quality(doc, root):
            # the shared gate: the weak-adjective list, then "is this word a
            # description of a LOOK at all" (contested / worth / important).
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

      0. WORDNET'S SUPERSENSE, which overrules Brysbaert when it fires.
         Six of the 26 noun types never hold a picture whatever anyone rated
         them (NEVER_FILMABLE_SUPERSENSES): "nutmeg's weight" is
         noun.attribute and scores 3.94, "thing" is noun.state and scores
         3.17, and both sail through step 1. This is the step that stops
         them. It is deliberately narrow — the other 20 supersenses go both
         ways, so Brysbaert keeps them.

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

        # 0. WordNet's supersense — the one answer Brysbaert cannot overrule.
        if is_never_filmable_noun(head):
            c.dropped_by = f"not-a-picture({_supersenses(head, 1)[0]})"
            continue

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


def drop_bare_counts(candidates: list[Candidate],
                     ctx: Context) -> list[Candidate]:
    """A COUNT IS NOT A PICTURE. It is the counted thing's `amount`.

        "Nutmeg only grew in ONE place on Earth"   the place is the picture
        "Every fossil was intact except ONE"       the fossil is
        "900 ships"                                the ships are, 900 of them

    "one" survives everything else in this file because spaCy hands it over
    as a CARDINAL entity, so the generic-noun rule that kills "one place"
    never sees it. This is the rule that sees it.

    RUN BEFORE resolve_overlaps(), on purpose: "900 ships" arrives twice, as
    a KIND_NUMBER (which outranks a thing) and as a KIND_THING noun chunk.
    Cutting the number here is what lets the SHIPS win the span — and
    fill_amounts() then reads the 900 off the parse and puts it on them. Cut
    it later and the segment would lose the picture along with the count.

    MONEY AND MEASURES KEEP THEIR SLOTS (is_bare_count) — there the number IS
    the picture.

    Nothing left? Say so, the same way drop_attribute_kinds() does: a
    segment whose only content was a count is a segment with nothing to film,
    and the mercy rule should hold the picture rather than leave it blank.
    """
    doc, kept = ctx.doc, []
    for c in candidates:
        if c.kind == KIND_NUMBER and is_bare_count(doc, c.start, c.end):
            c.dropped_by = "count-without-thing"
        else:
            kept.append(c)
    if candidates and not kept:
        ctx.attributes_only = True
    return kept


def drop_negated(candidates: list[Candidate],
                 ctx: Context) -> list[Candidate]:
    """A thing the segment says is NOT there is not a thing to film.
    e.g. "NO ships sailed"        -> no picture of ships
         "a valley WITHOUT water" -> no picture of water

    The same fact 7a drops off a variant and an action, one layer up: the
    absence of something cannot be photographed, and photographing it anyway
    puts exactly the wrong thing on screen.
    """
    doc, kept = ctx.doc, []
    for c in candidates:
        if c.kind in {KIND_THING, KIND_NAME} and is_negated(doc[c.start:c.end].root):
            c.dropped_by = "negated"
        else:
            kept.append(c)
    return kept


# =============================================================================
# SECTION 7 — STEPS 6 TO 10: TIDY, SPLIT, DEDUPE, ORDER, FALLBACK
# =============================================================================


def _trim_edges(doc, start: int, end: int) -> tuple[int, int]:
    """The edges NO kind of candidate ever wants, cut off every one of them.

        LEADING DETERMINER    DET tagged `det`
              "the Banda Islands" --> "Banda Islands"
              "the 1600s"         --> "1600s"
              "a decade"          --> "decade"
        LEADING APPROXIMATOR  STL.APPROXIMATOR_WORDS
              "about two dollars" --> "two dollars"
              "about $2 million"  --> "$2 million"
              "over 900 ships"    --> "900 ships"
        PUNCTUATION at either end.

    Both are CLOSED GRAMMATICAL CLASSES: English has about a dozen
    determiners and a dozen hedges, and has not gained one in a century. So
    reading STL.APPROXIMATOR_WORDS is closer to reading a tag than to writing
    a lexicon — which is why this is allowed to name words at all.

    WHY IT MUST RUN ON NAMES, NUMBERS AND DATES TOO — the kinds this function
    used to wave through as "already exactly the thing you would search".
    Measured: they are not, because spaCy's ENTITY spans swallow the edges.
        "about two dollars"  is one MONEY entity, hedge included
        "the Banda Islands"  is one LOC entity, article included

    Only the SPAN shrinks. The template is built by replacing a slot's
    characters, so the trimmed words stay in the line the viewer reads:
        "the Banda Islands."  -->  "the [1]."  +  search term "Banda Islands"
    The viewer still hears "the Banda Islands"; only what we go looking for
    changes. (A stock search for "Hague" still finds the Hague, so "The
    Hague" losing its article is accepted rather than excepted — an exception
    list would be a lexicon.)
    """
    while start < end:
        tok = doc[start]
        if (tok.is_punct
                or (tok.pos_ == "DET" and tok.dep_ == "det")
                or tok.lower_ in STL.APPROXIMATOR_WORDS):
            start += 1
            continue
        break
    while end > start and doc[end - 1].is_punct:
        end -= 1
    return start, end


def _hyphenated_to_next(doc, tok) -> bool:
    """Is this token the first half of a hyphenated word? "modern-day" -> True
    for "modern". Glued (no trailing whitespace) AND followed by a hyphen —
    "tiny," is glued to its comma too, and that is a different thing."""
    nxt = tok.i + 1
    return (tok.whitespace_ == "" and nxt < len(doc)
            and doc[nxt].text in {"-", "\u2013", "\u2014"})


def _trim_leading_descriptions(doc, start: int, end: int,
                               cand: Candidate) -> int:
    """Take the adjectives that only DESCRIBE off the front of the name, and
    hand them to the candidate's `trimmed_description` instead of binning
    them. Returns the new start.

        "A tiny, incredibly remote volcanic archipelago"
             name                 "volcanic archipelago"
             trimmed_description  ["tiny", "incredibly remote"]

    WHAT STAYS
        a COMPOUND NOUN            "kitchen cupboard", "whale skulls"
                                   two words, one thing — never touched here,
                                   because this only walks over ADJ tokens
        a KIND-FORMING ADJECTIVE   is_kind_forming_adjective(), while
                                   KEEP_KIND_FORMING_ADJECTIVES is on. It
                                   names a subtype, so it is part of what the
                                   thing IS, and the walk stops at it.

    WHAT MOVES
        every other amod — but only if is_visual_quality() agrees it is worth
        describing anything with. THAT GATE IS THE POINT: step 5 refuses
        "contested" a slot, and without asking the same question here this
        step would hand it straight back as a variant.

    Adverbs ride with their adjective ("incredibly remote"), so the variant
    reads the way the script does. `pending` is what makes that safe in the
    other direction: an adverb read before an adjective that turns out to
    STAY has to be given back, or "extremely volcanic archipelago" would lose
    its "extremely".

    A HYPHENATED WORD IS ONE WORD. spaCy splits "modern-day" into three
    tokens and calls "modern" an amod, but trimming it leaves "day
    Indonesia", which is not a thing. A token glued to a following hyphen is
    half of one word, so the walk stops there.

    AND A NOUN USED ATTRIBUTIVELY IS STILL A NOUN. spaCy calls "whale" an ADJ
    in "whale skeletons" and "kitchen" an ADJ in "kitchen cupboard" often
    enough that the dep label alone would take the front off both. WordNet
    settles it without a list: measured, "whale", "kitchen", "spice",
    "glass", "police", "sports" have ZERO adjective senses — they are nouns
    doing an adjective's job, which is a compound — while every real
    adjective in the five scripts has at least one. So an ADJ WordNet has
    never heard of as an adjective stays in the name.
    (Only ADJ is tested this way. A participle arrives tagged VERB/VBN —
    "little WRINKLED seed" — and is decided by the verb underneath it.)
    """
    pending = None            # first adverb of a run we have not decided yet
    while start < end:
        tok = doc[start]
        # nothing left after it to describe? then it IS the candidate — a
        # bare KIND_QUALITY adjective, "The yellow and". Trimming it would
        # leave an empty span, and drop it without anyone recording why.
        if tok.i + 1 >= end:
            break
        if tok.is_punct:
            start, pending = start + 1, None
            continue
        # an adverb modifying a LATER word in the phrase rides with it
        if tok.dep_ == "advmod" and start < tok.head.i < end:
            pending = start if pending is None else pending
            start += 1
            continue
        # A PARTICIPLE COUNTS. "little wrinkled seed" has "wrinkled" tagged
        # VERB/VBN, not ADJ, and it is still describing the seed.
        if (tok.dep_ != "amod"
                or tok.pos_ not in {"ADJ", "VERB"}
                or _hyphenated_to_next(doc, tok)
                or (tok.pos_ == "ADJ" and not _supersenses(tok.lower_,
                                                           ADJ_SENSE_WINDOW,
                                                           pos="a"))
                or (KEEP_KIND_FORMING_ADJECTIVES
                    and is_kind_forming_adjective(tok))):
            return start if pending is None else pending
        if is_visual_quality(doc, tok) and not is_negated(tok):
            cand.trimmed_description.append(_adjective_phrase(doc, tok))
        start, pending = tok.i + 1, None
    return start


def trim_to_searchable_core(candidates: list[Candidate],
                            ctx: Context) -> list[Candidate]:
    """Cut each candidate down to what you would type into a search box.
    e.g. "a jar of nutmeg sat on the shelf" --> "jar of nutmeg"
         "several rusted anchors"           --> "rusted anchors"
         "about two dollars"                --> "two dollars"

    Every candidate gets its EDGES trimmed (_trim_edges: articles, hedges,
    punctuation). Names, numbers, dates and sounds get nothing else done to
    them — inside those edges they really are the thing you would search for.

    The rest also lose, from the front: weak/quantifier adjectives, and then
    the DESCRIBING adjectives (_trim_leading_descriptions — the step that
    separates what the thing IS from how it LOOKS); from the back: dangling
    prepositions.
    Compounds stay — "kitchen cupboard" is two words for one thing. So do
    kind-forming adjectives, while KEEP_KIND_FORMING_ADJECTIVES says so.
    A NUMMOD goes, because a count is the thing's `amount` and fill_amounts()
    reads it off the parse: "900 ships" --> "ships", amount 900. (A money or
    measure figure is a KIND_NUMBER and never reaches this branch, so "two
    dollars" keeps its number, which is the whole picture there.)
    Still too long? Keep the head plus its amod/compound/nummod children,
    which is what abstract_term_resolver.mention_name() does too.
    """
    doc, out = ctx.doc, []
    for c in candidates:
        start, end = _trim_edges(doc, c.start, c.end)
        # Inside its edges, one of these IS the search term. Nothing more.
        already_searchable = c.kind in {KIND_NAME, KIND_NUMBER,
                                        KIND_DATE, KIND_SOUND}
        if not already_searchable:
            while start < end and (
                    doc[start].pos_ in {"DET", "PUNCT", "PART", "CCONJ"}
                    or (doc[start].pos_ == "ADJ"
                        and doc[start].lemma_.lower() in STL.WEAK_ADJ_LEMMAS)
                    # a count is the thing's `amount`, not part of its name:
                    # "900 ships" is a picture of ships, 900 of them.
                    or (doc[start].pos_ == "NUM"
                        and doc[start].dep_ == "nummod")):
                start += 1
            start = _trim_leading_descriptions(doc, start, end, c)
            while end > start and (doc[end - 1].is_punct
                                   or doc[end - 1].pos_ in {"ADP", "PART",
                                                            "CCONJ"}):
                end -= 1
        if start >= end:
            c.dropped_by = "trimmed-to-nothing"
            continue
        if not already_searchable and end - start > MAX_PHRASE_TOKENS:
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
            first = (a == c.start)
            while a < b and doc[a].pos_ in {"CCONJ", "DET", "PUNCT"}:
                a += 1
            while b > a and doc[b - 1].is_punct:
                b -= 1
            if a < b:
                out.append(Candidate(a, b, c.kind, c.detector + "+conj",
                                     confidence=c.confidence * 0.95,
                                     # the adjectives were trimmed off the
                                     # FRONT, so they describe the first piece
                                     trimmed_description=(
                                         list(c.trimmed_description)
                                         if first else [])))
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


def drop_attribute_kinds(candidates: list[Candidate],
                         ctx: Context) -> list[Candidate]:
    """A SLOT IS A THING TO FILM. Everything else the segment says about that
    thing is an ATTRIBUTE OF a slot, and the Visualisable already has a field
    for each one:

        a verb        -->  the thing's `action`     "open", "grew"
        an adjective  -->  the thing's `variant`    "tiny, remote"

    So KIND_ACTION and KIND_QUALITY never earn a slot of their own. They are
    still harvested (find_action_candidates / find_quality_candidates are the
    definition of a strong verb and a strong adjective, which fill_actions()
    and _variant_descriptions() both lean on) and they still get absorbed by
    the noun phrase around them in resolve_overlaps(). This runs AFTER that,
    so all it cuts is the ones that survived alone.

    e.g. "If you open your kitchen cupboard"
             before   [1] the viewer  [2] open  [3] the viewer's cupboard
             after    [1] the viewer  [2] kitchen cupboard   — both with
                      action="open", which fill_actions() already put there.

    Nothing is deleted silently: dropped_by says "not-a-slot:action" /
    "not-a-slot:quality", and entry.dropped still shows the working.

    SETS ctx.attributes_only when the cut emptied the segment. A segment that
    says only what is HAPPENING, with nothing to film, is exactly the case
    KIND_FALLBACK was invented for — see apply_length_fallback().
    """
    kept = []
    for c in candidates:
        if c.kind in SLOT_KINDS:
            kept.append(c)
        else:
            c.dropped_by = f"not-a-slot:{c.kind}"
    if candidates and not kept:
        ctx.attributes_only = True
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

    THE LENGTH IS WAIVED when drop_attribute_kinds() is what emptied the
    segment (ctx.attributes_only). "swerved after having" and "lifted out"
    have nothing in them BUT a verb, and they are too short for the mercy
    rule — but a segment that says only what is happening, with nothing to
    film, is precisely the "hold the picture you already have" case this kind
    exists for. Waiving the length here is the right fix; lowering
    LENGTH_FALLBACK_MIN_TOKENS would let every other short segment through
    too.
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
    if len(content) < LENGTH_FALLBACK_MIN_TOKENS and not ctx.attributes_only:
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

    A COMPARISON FRAME IS NEVER A SETTING. "It was worth more THAN its weight
    in gold" hangs "gold" off a locative preposition like any other place —
    and there is no gold in this video, so everything else in the sentence
    ended up standing in some. A thing named only to measure another thing
    against is not where anything is; see _in_comparison_frame().

    Does not remove anything — a setting is still a visualisable with a slot.
    """
    knowledge, doc = kb(), ctx.doc
    spatial_preps = STL.SPATIAL_LOCATIVE_PREPS | STL.SPATIAL_DIRECTIONAL_PREPS
    for c in candidates:
        if c.kind not in {KIND_THING, KIND_NAME}:
            continue
        if _in_comparison_frame(doc, c.start, c.end):
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


def flag_hypotheticals(candidates: list[Candidate],
                       ctx: Context) -> list[Candidate]:
    """Mark the candidates the segment only IMAGINED. Removes nothing —
    see is_hypothetical() for why flagging is the whole job."""
    for c in candidates:
        c.hypothetical = is_hypothetical(ctx.doc, c.start, c.end)
    return candidates


# =============================================================================
# SECTION 9 — STEP 11b: BUILDING THE ANSWER
# =============================================================================


def _in_comparison_frame(doc, start: int, end: int) -> bool:
    """Is this candidate inside a "more X THAN Y" measuring stick?

    "It was worth more than its weight in gold." The gold is not a place, and
    nothing in the video is standing in it — it is the ruler being held up
    against the nutmeg. Left alone, the locative "in" made it the sentence's
    setting and every other slot inherited it, which is the bug this fixes.

    THE TEST, both off the parse and neither of them a word this file chose:
        an ancestor in STL.COMPARATIVE_MARKERS  ("than", "more", "less")
        an ancestor tagged JJR / RBR            (any comparative at all)
    Ancestors, not the head: spaCy hangs "than its weight in gold" off "more",
    which hangs off "worth" — so the walk has to climb.
    """
    node = doc[start:end].root
    for _ in range(COMPARISON_FRAME_DEPTH):
        head = node.head
        if head.i == node.i:
            return False
        if head.lower_ in STL.COMPARATIVE_MARKERS or head.tag_ in {"JJR", "RBR"}:
            return True
        node = head
    return False


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
                              visualisables=vis,
                              time_descriptions=list(ctx.time_descriptions))


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
        hypothetical=cand.hypothetical,
        confidence=cand.confidence,
        token_span=(cand.start, cand.end),
        trimmed_description=list(cand.trimmed_description),
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
        2  amount    how many of it are on screen
        3  identity  collapse mentions, so one thing has one history
        4  coref     turn "it"/"they" into the thing they point at
        5  location  the line's setting, carried forward
        6  variant   what each thing looks like by now, described
    """
    entries = fill_actions(entries, doc)
    entries = fill_amounts(entries, doc)
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
    mat" is the mat, not the being. Nor does a verb whose senses are all
    non-physical (is_filmable_action), nor a NEGATED one — "the cat didn't
    like it" is not a shot of liking, and inverting it is not a shot either.
    A KIND_ACTION slot is skipped — it does not HAVE an action, it IS one.
    """
    for entry in entries:
        for vis in entry.visualisables.values():
            if vis.token_span is None:
                continue
            if vis.kind in {KIND_ACTION, KIND_FALLBACK}:
                continue
            verb = _governing_verb(doc, *vis.token_span)
            if verb is None or not is_filmable_action(verb):
                continue
            if is_negated(verb):
                # "The cat didn't like it" — the thing is not doing this. An
                # action that did not happen is not a shot; hold the picture.
                continue
            vis.action = _verb_phrase(doc, verb)
    return entries


# -----------------------------------------------------------------------------
# step 2 — amount
# -----------------------------------------------------------------------------

def fill_amounts(entries: list[VisualisablesEntry], doc) -> list[VisualisablesEntry]:
    """`amount` = how many of this thing are on screen. Off the parse; the
    default is 1, and it is only ever raised by a segment that COUNTED.

      a) A NUMERAL MODIFYING THE THING — dep_ == "nummod".
             "three ships"  -->  ships, amount 3
             "900 ships"    -->  ships, amount 900
         The numeral is no longer part of the name (trim_to_searchable_core
         cuts it), so this is where it goes instead of being lost.

      b) A PARTITIVE — NUM -> "of" -> the thing.
             "One of them landed"  -->  the slot is "them" (-> bees), amount 1
         The head noun of a partitive is not the picture; the object of "of"
         is. STEP 7d generalises this to the non-numeral containers ("a
         handful of spices"), which is the same rule again.

      c) NOTHING SAID  -->  1.  "a tractor" and "the tractor" are both one
         tractor, and a bare plural ("the bees") stays 1 too — see the
         field's own note for why that is deliberate rather than forgotten.

    NUMBERS AND DATES ARE SKIPPED. A money or measure slot is ONE picture of
    a figure: "two dollars" is a single shot of two dollars, not two shots.
    """
    for entry in entries:
        for vis in entry.visualisables.values():
            if vis.token_span is None or vis.kind in {KIND_NUMBER, KIND_DATE}:
                continue
            vis.amount = _amount_of(doc, vis)
    return entries


def _amount_of(doc, vis) -> int:
    """How many of this thing the segment says there are. 1 unless it counted."""
    root = doc[vis.token_span[0]:vis.token_span[1]].root

    # a) "three ships" — the numeral hangs off the thing
    for child in root.children:
        if child.dep_ == "nummod":
            count = _as_count(child)
            if count is not None:
                return count

    # b) "One of them" — the numeral is the HEAD and the thing is the object
    prep = root.head
    if (root.dep_ == "pobj" and prep.lower_ == "of"
            and (prep.head.pos_ == "NUM" or prep.head.like_num)):
        count = _as_count(prep.head)
        if count is not None:
            return count

    return 1


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
# step 3 — identity
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
# step 4 — coreference
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

    AN OWNER YOU CANNOT FILM IS RECORDED, NEVER SPLICED.
        "the tractor's windscreen"  is a good search term — someone can film
                                    a tractor, so the whole names the part
        "the viewer's cupboard"     is not — nobody can film the viewer, and
                                    no stock library has the shot
    So when the owner is a deictic ("you", "I", "we") the possessive is
    dropped from the NAME and kept as the FACT:
        "your kitchen cupboard"  -->  visualisable "kitchen cupboard"
                                      owner        "the viewer"
    Both halves are still true, and that pair is what tells the renderer to
    show a cupboard rather than a person. KIND_DEICTIC already means "a
    person, but not one there is any footage of"; this is that sentence
    enforced one layer down. Only the name changes — the slot still covers
    the same characters, so the viewer still reads "your kitchen cupboard".
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
            if _is_unfilmable_owner(entries, row):
                vis.visualisable = " ".join(p for p in (before, after) if p)
                # the PLAIN label, not the possessive form: "the viewer",
                # never "the viewer's".
                vis.owner = owner
            else:
                vis.visualisable = " ".join(
                    part for part in (before, _possessive_form(owner), after)
                    if part)
                vis.owner = _owner_identity(entries, owner)
            vis.identity = vis.visualisable.lower()
            vis.detector += "+abstract:poss"


def _is_unfilmable_owner(entries: list[VisualisablesEntry], row) -> bool:
    """Would we ever go and fetch footage of this owner? False = no.

    Two ways of being nobody, and they are the same fact seen from either
    end of the pipeline:
        the abstract-terms row came back source="deictic"   ("you", "I", "we")
        that same name already took a slot as KIND_DEICTIC somewhere

    The second is what generalises it past the deictic sources: whatever
    route a name took to KIND_DEICTIC, that kind is the file's own statement
    that there is no footage of it.
    """
    if row.get("source") == "deictic":
        return True
    want = (row.get("resolved") or "").lower()
    if not want:
        return False
    return any(v.kind == KIND_DEICTIC
               and (v.visualisable or "").lower() == want
               for entry in entries for v in entry.visualisables.values())


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
# step 5 — location
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
# step 6 — variant
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

    FOUR SOURCES, left to right:
      0  the describing adjectives trim_to_searchable_core() took off the
         front of the NAME (vis.trimmed_description) — "tiny", "incredibly
         remote". They were always part of this segment's description of the
         thing; the only change is that they are no longer mistaken for part
         of what it IS.
      1-3  _variant_descriptions(), which reads them off the parse.
    """
    history: dict[str, list[str]] = {}
    for entry in entries:
        # "at night" — how the PLACE looks in this segment (STEP 7h). It goes
        # on the setting's history before this segment's own slots are
        # stamped, so a setting named here carries it straight away.
        for description in entry.time_descriptions:
            target = _setting_identity(entries, entry)
            if not target:
                continue
            bucket = history.setdefault(target, [])
            if description not in bucket:
                bucket.append(description)
        for _n, vis in sorted(entry.visualisables.items()):
            if not vis.identity or vis.kind in {KIND_ACTION, KIND_QUALITY,
                                                KIND_FALLBACK}:
                continue
            so_far = history.setdefault(vis.identity, [])
            for description in (list(vis.trimmed_description)
                                + _variant_descriptions(doc, vis)):
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
         Gated by is_visual_quality(), the SAME test find_quality_candidates()
         uses — otherwise a word refused a slot for not being a picture
         ("It was WORTH more than...") walks straight back in as a variant.

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
                    and is_visual_quality(doc, sibling)
                    and not is_negated(sibling)):
                out.append(_adjective_phrase(doc, sibling))

    verb = _governing_verb(doc, *vis.token_span)
    if verb is None or is_negated(verb):
        # "It never rusted", "the paint did not splatter" — the absence of a
        # change is not a picture of anything, so neither (b) nor (c) has
        # anything to say. Drop it rather than invert it.
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


def _setting_identity(entries: list[VisualisablesEntry],
                      entry: VisualisablesEntry) -> str | None:
    """The identity of whatever this segment is standing in — its own setting
    slot if it has one, otherwise the location fill_locations() carried in.
    None when nothing has named a place yet, in which case a time of day has
    nowhere to go and is simply not recorded."""
    own = next((v for v in entry.visualisables.values() if v.is_setting), None)
    if own is not None and own.identity:
        return own.identity
    location = next((v.location for v in entry.visualisables.values()
                     if v.location), None)
    return _owner_identity(entries, location) if location else None


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
