"""
HOW_TO_USE_visualisables.py — how to call the visualisables pipeline.

    uv run HOW_TO_USE_visualisables.py

ONE call, ONE line segment. That is the whole API.
Everything below is real output, copied from an actual run of this file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "shared"))
import PATHS  # noqa: F401,E402  — every stage folder on sys.path

from _visualisables_pipeline import get_visualisable_data


# The script, already cut into the sections you want on screen — one segment
# per shot. Whatever did the cutting is not this folder's business; by the
# time we are called they are just strings.
SCRIPT_SPLIT_INTO_LINE_SEGMENTS = [
    "In Egypt,",
    "there's a valley filled with",
    "whale skeletons.",
    "It was once covered",
    "by the Tethys Sea.",
]


# =============================================================================
# THE ONE CALL          <-- this is step 2) of myownstuff.py
# =============================================================================

visualisables_data = get_visualisable_data(
    "whale skeletons.",                                # line_segment
    ["In Egypt,", "there's a valley filled with"],     # previous_line_segments
    "It was once covered")                             # next_line_segment
# {
#   "[1].": {
#       "1": {"visualisable": "whale skeletons",
#             "variant": None,             # nothing has changed how it looks
#             "action": "filled",          # a verb is a field, not a slot
#             "location": "Egypt",         # from 3 segments back
#             "kind": "thing",
#             "identity": "whale skeletons",
#             "is_setting": False,
#             "amount": 1,
#             "confidence": 0.9}}
# }


# =============================================================================
# THE PARAMETERS
# =============================================================================
#
# line_segment            the ONE segment you want labelled. A section of a
#                         line, not a whole sentence.
#                         e.g. "whale skeletons."
#
# previous_line_segments  every segment before it, in order. None for the
#                         first segment of the script. Needed because a
#                         segment does not carry its own context:
#                             "It was once covered"  -- "It" is only the
#                                 valley if you can see what came before
#                             "whale skeletons."     -- they are in Egypt
#                                 because a segment 3 back said so
#                         e.g. ["In Egypt,", "there's a valley filled with"]
#
# next_line_segment       the segment after it, or None at the end. Parsing
#                         context only — nothing is ever taken out of it.
#                         spaCy reads a half sentence badly on its own.
#                         e.g. "It was once covered"
#
# coref                   how to resolve "it" / "they" / "she".
#                         "fast" (the default) runs a real model, ~5 s, and
#                             only when this segment HAS a pronoun in it
#                         "off"  assumes the last thing mentioned — instant,
#                             and about 50-55% right
#                         "full" the 4-model ensemble, ~51 s, ~2 F1 better
#                         e.g. get_visualisable_data("It was once covered",
#                                                    previous, next,
#                                                    coref="off")
#
# abstract_terms          the whole script's pronouns, already resolved, from
#                         _abstract_term_resolver. PREFER THIS to `coref`:
#                         one ~40 s pass for the script instead of ~5 s for
#                         every segment with an "it" in it, four models
#                         voting instead of the first one that answered, and
#                         it also carries the deictics and the possessives.
#                         Build it ONCE, before the loop, over
#                         join_segments(all your segments) -- the offsets are
#                         what the lookup uses, so it has to be the same text.
#                         e.g.
#                           from _abstract_term_resolver import resolve_all_abstract_terms
#                           from _visualisables_pipeline import join_segments, parse_script
#
#                           script = join_segments(SCRIPT_SPLIT_INTO_LINE_SEGMENTS)
#                           terms = resolve_all_abstract_terms(script,
#                                                              doc=parse_script(script))
#                           get_visualisable_data(segment, previous, following,
#                                                 abstract_terms=terms)
#
# @output  {template: {slot: {...}}} — one key, this segment with its
#          visualisables punched out into numbered slots.


# =============================================================================
# WHAT THE FIELDS MEAN
# =============================================================================
#
# visualisable  the thing to put on screen, trimmed to what you would type
#               into a search box.  e.g. "jar of nutmeg", not "a jar of
#               nutmeg that sat on the shelf"
#
# variant       extra description of how it looks BY NOW, accumulated over
#               the script. None means the plain, unmodified thing.
#                   "yellow paint splat, broken window"
#                   "really big"
#                   "fast"
#
# action        what it is doing in this segment.  e.g. "flew away"
#
# amount        how many of it are on screen. 1 unless the segment actually
#               counted them:  "900 ships" --> visualisable "ships", amount
#               900. NOT "the number in the text" — a money or measure slot
#               is ONE picture of a figure, so "two dollars" is amount 1 and
#               carries its number in the name.
#
# location      the setting it is standing in, carried forward from whichever
#               segment named it.  e.g. "Egypt"
#
# kind          "thing" / "name" / "number" / "date" — something to film.
#               These are the only kinds that earn a SLOT. A verb and an
#               adjective do not: a verb is the thing's `action` and an
#               adjective its `variant`, so they arrive as fields ON a slot
#               rather than as slots of their own.
#               "reference" — this slot was a pronoun, and what you see in
#               `visualisable` is what it was resolved to. If it still reads
#               like a pronoun ("It"), nothing confident enough came back —
#               hold the picture you already have.
#               "deictic" — "I" / "we" / "you": the narrator or the viewer.
#               A person, but not one there is any footage of. Only ever
#               appears when you passed abstract_terms.
#               "fallback" — no picture in this segment at all; hold whatever
#               is already on screen.
#
# identity      the same thing under every name the script gives it, so "The
#               tractor", "the tractor" and "it" share one variant history.
#
# is_setting    put this one in the BACKGROUND. Everything else goes on top.
#
# owner         the WHOLE this thing is a part of, when a possessive pronoun
#               said so:  "Its windscreen broke."  -->  visualisable is "the
#               tractor's windscreen", owner is "tractor". None otherwise.
#               So whatever happens to the part can be shown on the whole.
#               Only ever filled when you passed abstract_terms.
#
# theme_kind    what the SCRIPT is about at this segment, and what sort of
# theme_text    thing that is — "place"/"Egypt", "era"/"64 AD",
#               "culture"/"Roman", "subject"/"the spice trade". Worked out
#               once per script by _theme_engine.py; the KIND comes off
#               spaCy's own entity labels. Both None when nothing is live
#               here, which is a normal answer.
#               A LINE-level fact: every slot of a segment carries the same
#               pair, so a consumer holding one slot need not go and find its
#               siblings. It is NOT in the search term unless
#               VISUALISABLE_SEARCH_TERMS.APPLY_THEMES is on, and it ships
#               off — adding a word the script did not say is the one change
#               that can make a working term worthless.
#
# abstract_concepts   the heads this segment LOST for not being a picture:
#               ["monopoly"], ["weight"]. Also a line-level fact. Stage 1
#               found them, judged them unfilmable and dropped them, and this
#               is the record — "The company held a monopoly" is a line about
#               something, it just is not a line with a monopoly in it.
#               When a segment has these AND nothing filmable survived, the
#               segment IS an abstract concept, which is what
#               shared_text_logic.is_abstract_concept() now answers.
#
# confidence    0..1. On a "reference" slot without abstract_terms: 0.75
#               means a coreference model answered, 0.10 means it was only a
#               "last thing mentioned" guess, 0.0 means we could not resolve
#               it at all.
#               WITH abstract_terms it is the real number — the share of the
#               models' weight that voted for this answer (0.26 when two of
#               four agreed, 1.00 for a deictic). Below 0.25 the pronoun is
#               left in place rather than swapped for a guess.


# =============================================================================
# IN A LOOP — one segment at a time, which is how myownstuff.py uses it
# =============================================================================

def show(template, slots):
    """Print the segment with the NAMES in the brackets rather than the slot
    numbers:   "In [1],"  -->  "In [Egypt],"   """
    for slot, v in slots.items():
        template = template.replace(f"[{slot}]", f"[{v['visualisable']}]")
    print(f"\n{template}")
    for slot, v in slots.items():
        setting = "*" if v["is_setting"] else " "
        print(f"  [{slot}]{setting} {v['visualisable']:<18} {v['kind']:<10}"
              f" x{v['amount']:<3} variant={str(v['variant']):<12}"
              f" action={str(v['action']):<8} in={v['location']}")


for i, line_segment in enumerate(SCRIPT_SPLIT_INTO_LINE_SEGMENTS):
    previous = SCRIPT_SPLIT_INTO_LINE_SEGMENTS[:i]
    following = (SCRIPT_SPLIT_INTO_LINE_SEGMENTS[i + 1]
                 if i + 1 < len(SCRIPT_SPLIT_INTO_LINE_SEGMENTS) else None)

    visualisables_data = get_visualisable_data(line_segment, previous, following)

    for template, slots in visualisables_data.items():
        show(template, slots)

# In [Egypt],
#   [1]* Egypt              name       x1   variant=None         action=None     in=None
#
# there's a [valley] filled with
#   [1]  valley             thing      x1   variant=None         action=None     in=Egypt
#
# [whale skeletons].
#   [1]  whale skeletons    thing      x1   variant=None         action=filled   in=Egypt
#
# [valley] was once covered
#   [1]  valley             reference  x1   variant=None         action=covered  in=Tethys Sea
#
# by the [Tethys Sea].
#   [1]* Tethys Sea         name       x1   variant=None         action=covered  in=None
#
# NOTE "filled" and "covered" have no slots of their own -- a verb is the
# thing's `action`, which is where you can see both of them. And the search
# terms are "Tethys Sea" and "Egypt", not "the Tethys Sea": the article stays
# in the line the viewer reads and comes off the thing we go looking for.
