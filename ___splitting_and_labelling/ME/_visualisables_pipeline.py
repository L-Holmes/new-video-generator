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
"""
import _visualisables_extractor as VE


# One entry per call, so re-asking for a segment is free.
_CACHE: dict[tuple, dict] = {}


def get_visualisable_data(line_segment: str,
                          previous_line_segments: list[str] | None = None,
                          next_line_segment: str | None = None,
                          coref: str = VE.COREF_DEFAULT) -> dict:
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
    key = (tuple(previous), line_segment, next_line_segment, coref)
    if key not in _CACHE:
        _CACHE[key] = _build(previous, line_segment, next_line_segment, coref)
    return _CACHE[key]


# =============================================================================
# internals
# =============================================================================

def _build(previous: list[str], line_segment: str,
           next_line_segment: str | None, coref: str) -> dict:
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
    entries = VE.resolve_visualisable_details(entries, doc, coref=coref)
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
    parts, offsets, cursor = [], [], 0
    for segment in segments:
        text = " ".join(segment.split())
        parts.append(text)
        offsets.append((cursor, cursor + len(text)))
        cursor += len(text) + 1              # the joining space

    doc = VE.nlp()(" ".join(parts))
    spans = []
    for start, end in offsets:
        # "expand" because spaCy's tokenisation may not land on our offsets
        span = doc.char_span(start, end, alignment_mode="expand")
        spans.append((span.start, span.end) if span is not None else (0, 0))
    return doc, spans
