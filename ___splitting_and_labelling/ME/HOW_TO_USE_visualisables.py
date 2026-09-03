"""
HOW_TO_USE_visualisables.py — how to call the visualisables pipeline.

    uv run HOW_TO_USE_visualisables.py

ONE call, ONE line segment. That is the whole API.
Everything below is real output, copied from an actual run of this file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
#             "action": "filled",
#             "location": "Egypt",         # from 3 segments back
#             "kind": "thing",
#             "identity": "whale skeletons",
#             "is_setting": False,
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
# location      the setting it is standing in, carried forward from whichever
#               segment named it.  e.g. "Egypt"
#
# kind          "thing" / "name" / "number" / "date" — something to film.
#               "action" / "quality" — NOT things to film; they are there so
#               the thing next to them can borrow them.
#               "reference" — this slot was a pronoun, and what you see in
#               `visualisable` is what it was resolved to.
#               "fallback" — no picture in this segment at all; hold whatever
#               is already on screen.
#
# identity      the same thing under every name the script gives it, so "The
#               tractor", "the tractor" and "it" share one variant history.
#
# is_setting    put this one in the BACKGROUND. Everything else goes on top.
#
# confidence    0..1. On a "reference" slot: 0.75 means a coreference model
#               answered, 0.10 means it was only a "last thing mentioned"
#               guess, 0.0 means we could not resolve it at all.


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
              f" variant={str(v['variant']):<12}"
              f" action={str(v['action']):<8} in={v['location']}")


for i, line_segment in enumerate(SCRIPT_SPLIT_INTO_LINE_SEGMENTS):
    previous = SCRIPT_SPLIT_INTO_LINE_SEGMENTS[:i]
    following = (SCRIPT_SPLIT_INTO_LINE_SEGMENTS[i + 1]
                 if i + 1 < len(SCRIPT_SPLIT_INTO_LINE_SEGMENTS) else None)

    visualisables_data = get_visualisable_data(line_segment, previous, following)

    for template, slots in visualisables_data.items():
        show(template, slots)

# In [Egypt],
#   [1]* Egypt              name       variant=None         action=None     in=None
#
# there's a [valley] [filled] with
#   [1]  valley             thing      variant=None         action=None     in=Egypt
#   [2]  filled             action     variant=None         action=None     in=Egypt
#
# [whale skeletons].
#   [1]  whale skeletons    thing      variant=None         action=filled   in=Egypt
#
# [valley] was once [covered]
#   [1]  valley             reference  variant=None         action=covered  in=the Tethys Sea
#   [2]  covered            action     variant=None         action=None     in=the Tethys Sea
#
# by [the Tethys Sea].
#   [1]* the Tethys Sea     name       variant=None         action=covered  in=None
