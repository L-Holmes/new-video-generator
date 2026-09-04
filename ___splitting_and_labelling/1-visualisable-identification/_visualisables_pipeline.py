"""
_visualisables_pipeline.py — the visualisables data for ONE line segment.

    from _visualisables_pipeline import get_visualisable_data

    visualisables_data = get_visualisable_data(
        "whale skeletons.",                                 # this segment
        ["In Egypt,", "there's a valley filled with"],      # the ones before it
        "It was once covered")                              # the one after it

    # {"[1].": {"1": {"visualisable": "whale skeletons", "variant": None,
    #                 "action": "filled", "location": "Egypt", ...}}}

    (HOW_TO_USE_visualisables.py is the worked example, with real output.)

ONE SEGMENT IN, ITS MAP OUT. There is no whole-script call: you label the
segment you are on, inside the loop you are already writing.

WHY THE NEIGHBOURS ARE NEEDED
    The segment on its own is not enough, and not only for parsing:

        "It was once covered"   — "It" is only the valley if you can see the
                                  segments before it
        "whale skeletons."      — they are in Egypt because a segment 3 back
                                  said so
        "the tractor"           — it has yellow paint on it because a segment
                                  5 back poured some over it

    So everything up to and including this segment is parsed as ONE doc, each
    segment gets its own per-segment pass against that doc, and then the
    document pass fills variant / action / location and resolves the
    pronouns. Only the target segment's map is handed back.

    No linguistic decisions are made here — they are all in the extractor.

THE PRONOUNS CAN BE ANSWERED BEFORE YOU START
    Resolve the whole script once, up front, and hand the map in:

        abstract_terms = resolve_all_abstract_terms(join_segments(segments))
        get_visualisable_data(segment, previous, next,
                              abstract_terms=abstract_terms)

    Then no coreference model runs in here at all — every "it" is a lookup.
    One ~40 s pass for the script beats ~5 s for every segment with a pronoun
    in it, and four models voting beat the first one that answered.
    join_segments() is what makes the map's character offsets line up with
    what this file parses; see it for why a prefix is safe.
"""
import _visualisables_extractor as VE


# One entry per call, so re-asking for a segment is free.
_CACHE: dict[tuple, dict] = {}


def join_segments(segments: list[str]) -> str:
    """THE ONE TEXT every character offset in this folder is counted in.

    e.g. join_segments(["In Egypt,", "there's  a valley"])
             -->  "In Egypt, there's a valley"

    The abstract-terms map is keyed by character offsets, so the text the
    resolver read and the text this file parses have to be the SAME string,
    down to the space. This is that string: each segment's whitespace
    squeezed, joined with a single space.

    WHY A PREFIX IS SAFE, which is the thing that makes the whole design
    work: joining is left to right with one space, so the join of the first
    n segments IS a prefix of the join of all of them. Every per-segment call
    therefore sees the same offsets the full-script map was built with, and
    the map can be built ONCE over the whole script.

        join_segments(segments[:i]) == join_segments(segments)[:that length]

    (Checked over script-whales / script-rome / script-spices, every prefix
    of every split: it holds.)
    """
    return " ".join(normalise_segment(s) for s in segments)


def normalise_segment(segment: str) -> str:
    """One segment with its whitespace squeezed — the half of join_segments()
    that applies to a single segment.
    e.g. "there's  a\nvalley"  -->  "there's a valley" """
    return " ".join(segment.split())


def parse_script(text: str) -> object:
    """The ONE shared en_core_web_sm parse of a whole script.

    Here so the caller can give the SAME parse to the abstract term resolver
    (resolve_all_abstract_terms(text, doc=parse_script(text))) instead of
    letting it load a second spaCy model — measured to give identical answers
    on all 11 pronouns of the three test scripts. See that file's docstring.
    """
    return VE.nlp()(text)


def get_visualisable_data(line_segment: str,
                          previous_line_segments: list[str] | None = None,
                          next_line_segment: str | None = None,
                          coref: str = VE.COREF_DEFAULT,
                          abstract_terms: dict | None = None) -> dict:
    """Everything we can put on screen for ONE line segment.

    @input line_segment = the segment to label.
        e.g. "whale skeletons."
    @input previous_line_segments = every segment before it, in order. None
        for the first segment of the script.
        e.g. ["In Egypt,", "there's a valley filled with"]
    @input next_line_segment = the segment after it, or None at the end.
        Context for the parser only — nothing is ever taken out of it.
        e.g. "It was once covered"
    @input coref = how to resolve "it" / "they" / "she":
        "fast" (default — a real model, ~5 s, and only when this segment
        actually has a pronoun in it), "full" (the 4-model ensemble, ~51 s)
        or "off" (assume the last thing mentioned; instant, ~50-55% right).
        IGNORED when abstract_terms is given — the answers are already in it.
    @input abstract_terms = the whole script's pronouns, resolved ONCE before
        this loop started, by resolve_all_abstract_terms(). PREFER THIS: no
        model runs per segment, and the answer is four models' weighted vote
        rather than the first one that spoke. It must have been built over
        join_segments(all the segments) or its offsets will not line up.
        e.g. {(395, 397): {"surface": "it", "resolved": "valley",
                           "confidence": 0.26, "source": "models", ...}}

    @output {template: {slot: {...}}} — ONE key: this segment, with its
        visualisables punched out into numbered slots.
        e.g. {"[1].": {
                  "1": {"visualisable": "whale skeletons",
                        "variant": None,          # no extra description yet
                        "action": "filled",
                        "location": "Egypt",      # from 3 segments back
                        "kind": "thing",
                        "identity": "whale skeletons",
                        "is_setting": False,
                        "confidence": 0.9}}}
    """
    previous = [s for s in (previous_line_segments or []) if s and s.strip()]
    # id() for the map: it is one object built once per script and passed
    # down the whole loop, and a dict cannot be a dict key.
    key = (tuple(previous), line_segment, next_line_segment, coref,
           id(abstract_terms))
    if key not in _CACHE:
        _CACHE[key] = _build(previous, line_segment, next_line_segment, coref,
                             abstract_terms)
    return _CACHE[key]


# =============================================================================
# internals
# =============================================================================

def _build(previous: list[str], line_segment: str,
           next_line_segment: str | None, coref: str,
           abstract_terms: dict | None = None) -> dict:
    """Three steps.

        0  parse everything up to here as ONE doc, one token span per segment
        1  per segment    — what is in it
        2  across segments — variant / action / location, and the pronouns
    """
    target = len(previous)
    segments = previous + [line_segment]
    if next_line_segment:
        # Labelled as well, though its map is never returned: a setting is
        # often named in the segment AFTER the thing standing in it — "It was
        # once covered" | "by the Tethys Sea." — and without an entry for it
        # step 2 could not give this segment that location.
        segments = segments + [next_line_segment]
    doc, spans = _parse_together(segments)

    # 1) PER SEGMENT. keep_pronouns only for the one we were asked about:
    #    those are the slots that need resolving, and it is what keeps the
    #    coreference model out of the way on a segment with no pronoun in it.
    entries = [
        VE.create_visualisables_entry(segment,
                                      doc=doc,
                                      line_token_span=span,
                                      keep_pronouns=(i == target))
        for i, (segment, span) in enumerate(zip(segments, spans))
    ]

    # 2) ACROSS SEGMENTS — this is myownstuff.py's "resolve all abstract
    #    terms" step: "*it* ploughed the field" --> "[the tractor] ploughed
    #    the field", plus what each thing is doing, where, and looking like.
    entries = VE.resolve_visualisable_details(entries, doc, coref=coref,
                                             abstract_terms=abstract_terms)
    return entries[target].as_map()


def _parse_together(segments: list[str]):
    """Parse the segments as one piece of text; return (doc, span per segment).

    ONE shared doc is what lets step 2 compare a pronoun in segment 9 with
    the noun it refers to in segment 1 — and spaCy parses a fragment badly on
    its own ("went down the lane" gets no subject), so even the per-segment
    pass wants the neighbours present.

    Offsets are accumulated as the text is joined, and never searched for
    afterwards, because a segment can legitimately occur twice ("It was.").
    """
    offsets, cursor = [], 0
    for segment in segments:
        text = normalise_segment(segment)
        offsets.append((cursor, cursor + len(text)))
        cursor += len(text) + 1              # the joining space

    # join_segments(), not a second copy of the same rule: these offsets and
    # the abstract-terms map's offsets have to be counted in the same string.
    doc = VE.nlp()(join_segments(segments))
    spans = []
    for start, end in offsets:
        # "expand" because spaCy's tokenisation may not land on our offsets
        span = doc.char_span(start, end, alignment_mode="expand")
        spans.append((span.start, span.end) if span is not None else (0, 0))
    return doc, spans
