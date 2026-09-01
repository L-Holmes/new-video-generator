"""
visualisables_pipeline.py — run visualisables_extractor over a WHOLE script.

    from sentence_splitter import split_text_into_sections_with_meta
    chunks = split_text_into_sections_with_meta(script_text)
    data   = get_visualisable_data(chunks, script_text)

This is the glue between the sentence splitter and
visualisables_extractor.py, and it exists because the extractor has two
entry points that have to be called in the right order with a shared parse:

    create_visualisables_entry()      per line — what is IN this line
    resolve_visualisable_details()    per SCRIPT — variant / action /
                                      location, and the pronouns

The second one cannot be folded into the first: a thing's `location` is
usually named in a different line ("In Egypt," ... "there's a valley"), and
its `variant` depends on everything that has happened to it earlier. So
this file parses once, runs the per-line pass over every line against that
one doc, and then runs the document pass over the lot.

No linguistic decisions are made here. They all live in the extractor,
which delegates in turn to spaCy, shared_text_logic's word lists, the
Brysbaert concreteness ratings and abstract_term_resolver's coreference.

    uv run visualisables_pipeline.py                 (recency-guessed pronouns)
    uv run visualisables_pipeline.py --coref fast    (the real model, ~5 s)
"""
import sys
from pathlib import Path

# The splitter and the shared word lists live one directory up.
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

import visualisables_extractor as VE


# The built map, cached per script so a re-run inside one process is free.
_CACHE: dict[str, dict] = {}


# =============================================================================
# public API
# =============================================================================

def get_visualisable_data(sentence_splitter_output,
                          script_text: str | None = None,
                          coref: str = VE.COREF_DEFAULT) -> dict:
    """The whole pipeline, returning the json-shaped map:

        {template_line: {"1": {...}, "2": {...}}, ...}

    where each slot record carries visualisable / variant / action /
    location plus the provenance the extractor recorded.

    @param sentence_splitter_output  the splitter's chunks. Prefer
        split_text_into_sections_with_meta(), whose chunks also carry
        .meta: the rule ids let the extractor cross-check its own harvest,
        and meta["span"] saves it locating each line. Plain Chunks work
        too — the lines are joined back up and parsed once — and on the
        scripts tested both routes give identical slots, actions,
        locations and variants.
    @param script_text  the ORIGINAL script. Recommended: with it (and
        meta) the splitter's own token spans are reused as-is; without it
        the text is rebuilt from the lines, which costs one extra parse
        and loses whatever spacing sat between them. See _parse_once.
    @param coref  "fast" (default, ~5 s of model time per script), "full"
        (~51 s) or "off" (recency guessing, ~50-55% right). See
        VE.COREF_DEFAULT.
    """
    key = script_text if script_text is not None else "\n".join(
        chunk.text for chunk in sentence_splitter_output)
    if key not in _CACHE:
        entries = build_visualisable_data(sentence_splitter_output,
                                          script_text, coref)
        _CACHE[key] = entries_to_map(entries)
    return _CACHE[key]


def get_visualisable_entries(sentence_splitter_output,
                             script_text: str | None = None,
                             coref: str = VE.COREF_DEFAULT) -> list:
    """The same pipeline, handing back the VisualisablesEntry objects
    rather than the json-shaped map.

    Use this one from code. The map keys on the line, which is lossy — a
    script really can produce the same template twice ("[1]." turned up
    five times in script-whales) — whereas the list keeps script order and
    every field.
    """
    return build_visualisable_data(sentence_splitter_output, script_text,
                                   coref)


def build_visualisable_data(chunks, script_text: str | None = None,
                            coref: str = VE.COREF_DEFAULT) -> list:
    """Split lines in, list[VisualisablesEntry] out. Three steps.

        0  parse the whole script ONCE, and get one token span per line
        1  per line   — what is in this line
        2  per script — variant / action / location, and the pronouns

    ==========================================================================
    THE SPEC THIS WAS BUILT FROM
    Lifted verbatim from myownstuff.py's _build_visualisable_data(), which
    is where this was planned before it had an implementation. Kept as
    written -- it is the description of what the output is FOR, and the
    "hmmmmmmmmmm jump" note below is the design decision this whole file
    turns on.
    ==========================================================================

    @input The text.
    e.g.
    "
    [
        The tractor and the cat, Molly, went down the lane. 
        They passed a bee.
        It revved really loud. The cat didn't like it.
        So she poured yellow paint onto the tractor. It swished and swerved after having the paint splatter its windscreen.
        It then crashed into the lampost by accident.
        The yellow and black guy flew away.
        Its windscreen broke.
    ]

    @generates:
    (as a pseudo intermediary step)

    [tractor][v2-with-yellow-paint-splat-and-broken-window][ploughing]

    [tractor]
    --> [v2-with-yellow-paint-splat-and-broken-window]
        --> [ploughing]

    @output
    
    {

        The tractor and the cat, Molly, went down the lane.:{
                "cumulative_abstract_terms":["tractor", "cat", "lane", "bee"],


                --------------------------------------------------------------
                hmmmmmmmmmm jump.
                perhaps a map from the visualisable to all we know about it?
                e.g.

                "The [1] and the [2], Molly, went down the lane":{
                    [1]:{
                            "visualisable":tractor
                            "variant":null / base version
                            "action":null / unknown / base action
                            "location"?: null/ base / unknown / presumably farmland.
                    },
                    [2]:{
                            ...
                    }
                },
                "[3] passed a [4]":{
                    [1]:{
                            "visualisable": [1], [2]
                            "variant":null / base version
                            "action":null / unknown / base action
                            "location"?: the lane
                     },
                     [2]:{
                            "visualisable": "bee"
                            "variant":null / base version
                            "action":null / unknown / base action
                            "location"?:  null/ base / unknown / presumably farmland.
                     }
                }
                            
                --------------------------------------------------------------

                importance?
                    - which are the most important visualisables in the sentence?
                        - based on prevoius context?
                        - based on what has recently changed (obviolulsy most important)

        }


        They passed a bee.
        It revved really loud. The cat didn't like it.
        So she poured yellow paint onto the tractor. It swished and swerved after having the paint splatter its windscreen.
        It then crashed into the lampost by accident.
        The yellow and black guy flew away.
        Its windscreen broke.
        
    ==========================================================================
    WHAT OF THAT IS DONE
      "The [1] and the [2], Molly, ..." template + numbered slots   -> done
      visualisable / variant / action / location per slot           -> done
      the [v2-with-yellow-paint-splat-and-broken-window] chain      -> done,
          as the `variant` field, accumulated per identity across the script
      "cumulative_abstract_terms"                                   -> not
          built as such; the same information is the per-line slots plus
          `identity`, which is what links a thing across lines
      importance ranking (which visualisable matters most in a line) -> TODO
    ==========================================================================
    """
    chunks = list(chunks)
    if not chunks:
        return []

    # 0) PARSE — once, for the whole script. Everything downstream depends
    #    on a SHARED doc: it is what lets step 2 compare a pronoun in line
    #    9 with the noun it refers to in line 1.
    doc, spans = _parse_once(chunks, script_text)

    # 1) PER LINE — what is IN this line.
    #    keep_pronouns=True so "It"/"They" survive as reference slots for
    #    step 2 to resolve; without it they are dropped as non-pictures.
    entries = [
        VE.create_visualisables_entry(
            chunk.text, None, None,
            rule_ids=getattr(chunk, "ids", None),
            splitter_meta=getattr(chunk, "meta", None),
            doc=doc,
            line_token_span=span,
            keep_pronouns=True)
        for chunk, span in zip(chunks, spans)
    ]

    # 2) ACROSS LINES — variant / action / location, and the pronouns.
    #    This is myownstuff.py's "resolve all abstract terms" step: for
    #    each unknown, work out which visualisable it points to — "*it*
    #    ploughed the field" -> "[the tractor] ploughed the field". It also
    #    does that file's "match verbs to visualisables", as `action`.
    return VE.resolve_visualisable_details(entries, doc, coref=coref)


# =============================================================================
# internals
# =============================================================================

def _parse_once(chunks, script_text: str | None):
    """Parse the whole script once; return (doc, one token span per line).

    Two ways in, and the pipeline ends up with a doc either way — which
    matters, because without one there is no step 2 at all and variant /
    action / location would silently come back empty.

      * WE HAVE THE SCRIPT AND THE SPLITTER'S META. Then the spans are
        already known: split_text_into_sections_with_meta() stamps every
        line with meta["span"], the token indices of that line in its own
        parse, and a fresh parse of the same text reproduces them exactly
        (verified 15/15 on script-spices). Nothing to locate, nothing to
        align. This is the path to prefer.

      * OTHERWISE we rebuild the text by joining the lines back together
        and record each line's offsets BY ACCUMULATION as we go — never by
        searching for the line afterwards, because a line can legitimately
        occur twice ("It was."). Costs one parse and loses only whatever
        punctuation or spacing sat between lines in the original.

    Measured on a 143-line script: the first path 2.8 s, the second 5.6 s.
    The gap widens with length, since the alternative to one shared parse
    is re-parsing a growing context window per line.
    """
    if script_text and _has_spans(chunks):
        return VE.nlp()(script_text), [tuple(c.meta["span"]) for c in chunks]

    parts, offsets, cursor = [], [], 0
    for chunk in chunks:
        text = " ".join(chunk.text.split())
        parts.append(text)
        offsets.append((cursor, cursor + len(text)))
        cursor += len(text) + 1          # the joining space
    doc = VE.nlp()(" ".join(parts))

    spans = []
    for start, end in offsets:
        span = doc.char_span(start, end, alignment_mode="expand")
        spans.append((span.start, span.end) if span is not None else (0, 0))
    return doc, spans


def _has_spans(chunks) -> bool:
    """True when these are ChunkWithMeta carrying token spans — i.e. they
    came from split_text_into_sections_with_meta() rather than the classic
    split_text_into_sections()."""
    return bool(chunks) and all(
        isinstance(getattr(c, "meta", None), dict) and "span" in c.meta
        for c in chunks)


def entries_to_map(entries) -> dict:
    """list[VisualisablesEntry] -> the json-shaped map.

    Keyed by the template line. Templates DO collide in real scripts
    ("[1]." turned up five times in script-whales), and a plain dict would
    silently drop the later ones, so a colliding key gets a "  #2" suffix
    rather than losing the line. Use get_visualisable_entries() if you need
    the unambiguous, ordered version.
    """
    out: dict = {}
    for entry in entries:
        key = entry.template
        if key in out:
            n = 2
            while f"{key}  #{n}" in out:
                n += 1
            key = f"{key}  #{n}"
        out[key] = {str(slot): vis.as_dict()
                    for slot, vis in sorted(entry.visualisables.items())}
    return out


# =============================================================================

def debug_output_resolved_visualisables(entries) -> None:
    """Print what we decided, one line at a time, in the order the video
    will play. `*` marks the setting, `->` the running variant."""
    for entry in entries:
        print(f"\n{entry.template}")
        if not entry.visualisables:
            print("    (nothing to show — holds the previous image)")
        for slot, vis in sorted(entry.visualisables.items()):
            mark = "*" if vis.is_setting else " "
            bits = [f"[{slot}]{mark} {vis.visualisable:<24} {vis.kind:<9}"]
            if vis.variant and vis.variant != "base":
                bits.append(f"-> {vis.variant}")
            if vis.action:
                bits.append(f"doing={vis.action}")
            if vis.location:
                bits.append(f"in={vis.location}")
            print("    " + "  ".join(bits))
        for note in entry.notes:
            print(f"    ! {note}")


# =============================================================================
# self-test — the worked example from myownstuff.py
# =============================================================================

_SAMPLE = (
    "The tractor and the cat, Molly, went down the lane. "
    "They passed a bee. "
    "It revved really loud. The cat did not like it. "
    "So she poured yellow paint onto the tractor. "
    "It swished and swerved after having the paint splatter its windscreen. "
    "It then crashed into the lampost by accident. "
    "The yellow and black guy flew away. "
    "Its windscreen broke."
)


def _selftest(coref: str = "off") -> None:
    import contextlib
    import io
    import json
    from sentence_splitter import split_text_into_sections_with_meta

    with contextlib.redirect_stdout(io.StringIO()):
        chunks = split_text_into_sections_with_meta(_SAMPLE)

    entries = get_visualisable_entries(chunks, _SAMPLE, coref=coref)
    print("=" * 74)
    print(f"visualisables_pipeline — {len(chunks)} lines   (coref={coref})")
    print("=" * 74)
    debug_output_resolved_visualisables(entries)

    data = get_visualisable_data(chunks, _SAMPLE, coref=coref)
    print("\n" + "=" * 74)
    print("get_visualisable_data() — the map, first line")
    print("=" * 74)
    print(json.dumps(dict(list(data.items())[:1]), indent=2)[:700])


if __name__ == "__main__":
    _argv = sys.argv[1:]
    _selftest(_argv[_argv.index("--coref") + 1] if "--coref" in _argv else "off")
