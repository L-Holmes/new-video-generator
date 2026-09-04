"""
Auto_add_mediatypes.py — FIRST-PASS automatic media-type tagging.

    uv run ___splitting_and_labelling/2-auto-tagging/Auto_add_mediatypes.py script_to_search_term.json
    uv run ___splitting_and_labelling/2-auto-tagging/Auto_add_mediatypes.py script_to_search_term.json --dry-run
    uv run ___splitting_and_labelling/2-auto-tagging/Auto_add_mediatypes.py --selftest

THIS FILE IS ONLY TWO LISTS OF `if` STATEMENTS. Read it top to bottom:

    STEP 1   go through every question we know how to ask about a line, and
             for each YES, add an attribute to that line's list.
                 if it contains a place name  → add CONTAINS_PLACE_NAME
                 if it contains a number      → add CONTAINS_BIG_NUMBER

    STEP 2   go through the combinations of those attributes and say what
             goes on screen.
                 if CONTAINS_BIG_NUMBER and CONTAINS_PLACE_NAME:
                     media_type = stock, search_term = the place name

Nothing else lives here. To change WHAT WE PUT ON SCREEN, edit STEP 2.

###############################################################################
#  NOTE TO ANY AI ADDING OR UPDATING A RULE IN THIS FILE                      #
#                                                                             #
#  EVERY rule in STEP 2 gets TWO comment lines, and no more:                  #
#      1. one line saying in plain English what the rule does                 #
#      2. a line under it starting with `--> ` giving a REAL example          #
#                                                                             #
#      # a list spread over several lines becomes one group, a cell per line  #
#      # --> "scurvy," / "pirates and" / "shipwrecks" = 3 cells side by side  #
#                                                                             #
#  No essays, no rationale paragraphs. If a rule needs more explaining than   #
#  that, the rule is too clever — simplify the rule, not the comment.         #
###############################################################################

WHERE THE REST OF IT IS
  • shared_text_logic.py — every word list, regex, threshold and detector,
    shared with sentence_splitter.py. A new question about a line gets a new
    detector THERE (SECTION 6), then one line in STEP 1 here.
  • auto_tag_engine.py — the machinery: reading the json, printing the table,
    writing a decision onto a row (short-scene gate, search_term auto-fill,
    chart data, group cells, never overwriting a row that is already tagged).
  • AUTO_TAG_SELFTEST.py — the worked examples behind `--selftest`.

HOUSE RULE: **False when in doubt.** A wrong empty costs nothing (manual
tagging catches it); a wrong tag costs a bad scene.
"""

from __future__ import annotations

# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent
                        / "shared"))
    import PATHS  # noqa: F401  — every stage folder on sys.path

from enum import Enum

import shared_text_logic as stl
from auto_tag_engine import Decision, Neighbours, main_cli, tag


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   THE ATTRIBUTES  —  everything a line can be found to have         ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================
#
# Each attribute's VALUE is the name of the detector that finds it, over in
# shared_text_logic.py (SECTION 6). Adding an attribute = write the detector
# there, add a line here, add a line in STEP 1.

class Attr(Enum):
    # ---- what the line CONTAINS ------------------------------------------
    CONTAINS_NOUN_LIST = "contains_noun_list"            # nutmeg, cloves and cinnamon
    CONTAINS_PLACE_NAME = "contains_place_name"          # a place the MAP can draw
    CONTAINS_QUOTE = "contains_quote"                    # "worth its weight in gold"
    CONTAINS_BIG_NUMBER = "contains_big_number"          # £4,000 / 90 percent
    CONTAINS_DATE = "contains_date"                      # in 1667 / the 1600s
    CONTAINS_FAMOUS_NAME = "contains_famous_name"        # Alaric the Goth
    OPENS_RELATIVE_TO_SOMETHING = "opens_relative_to_something"  # beneath the floor
    IS_QUESTION_TO_VIEWER = "asks_the_viewer_a_question"  # So what happened?
    HAS_SOMETHING_WE_COULD_FILM = "has_something_we_could_film"  # a jar of nutmeg

    # ---- statistics / data: which CHART does it want? --------------------
    CONTAINS_PERCENTAGE = "contains_percentage"          # 73% of the ocean
    CONTAINS_SHARES_OF_A_WHOLE = "contains_shares_of_a_whole"  # 40%, 35%, 25%
    CONTAINS_TREND_OVER_TIME = "contains_trend_over_time"  # grew from 200 to 4,000
    CONTAINS_SEVERAL_NUMBERS = "contains_several_comparable_numbers"  # 900 vs 300
    CONTAINS_SINGLE_STATISTIC = "contains_single_statistic"  # the fine was £4,000
    OPENS_WITH_A_YEAR = "opens_with_a_year"              # In 1946, ...

    # ---- where the line sits, and who it leans on ------------------------
    STARTS_A_NEW_SENTENCE = "starts_a_new_sentence"      # a new thought begins
    REFERS_BACK_TO_A_NAME = "refers_back_to_something_named"  # "He" = Nero

    # ---- planned: the detector exists but always says False --------------
    IS_SINGLE_OBJECT_FOCUS = "is_single_object_focus"    # one dollar coin
    ADDS_TO_SCENE = "adds_something_to_the_scene"        # add a jar into the cupboard
    TRANSFORMS_PREVIOUS = "transforms_the_previous_image"  # the map turns red
    CONTINUES_PREVIOUS = "continues_the_previous_line"   # which meant...
    IS_ABSTRACT_CONCEPT = "is_abstract_concept"          # monopoly, inflation
    SUITS_THE_BOARD = "suits_the_board_composite"        # one strong image
    IS_STICKMAN_STORY_BEAT = "is_stickman_story_beat"    # a merchant sails east
    IS_CAPTION_PUNCHLINE = "is_caption_punchline"        # worth its WEIGHT in GOLD
    IS_COMPARISON_PAIR = "is_comparison_pair"            # silver versus nutmeg


# The chart types, so a rule can ask "was the previous line already a chart?"
CHART_TYPES = {"timeline", "counter", "progress_bar", "bar_chart",
               "pie_chart", "line_graph"}


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   STEP 1  —  WHAT DOES THIS LINE CONTAIN?                           ### #
# ###                                                                     ### #
# ###   One `if` per question. Every YES adds an attribute. Order does     ### #
# ###   NOT matter here — we are only collecting facts, not deciding.      ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================

def collect_attributes(line: stl.LineContext) -> set[Attr]:
    """Ask every question we know how to ask about one line of the script."""
    has = set()

    # ---- what the line contains ------------------------------------------
    if stl.contains_noun_list(line):
        has.add(Attr.CONTAINS_NOUN_LIST)

    if stl.contains_place_name(line):
        has.add(Attr.CONTAINS_PLACE_NAME)

    if stl.contains_quote(line):
        has.add(Attr.CONTAINS_QUOTE)

    if stl.contains_big_number(line):
        has.add(Attr.CONTAINS_BIG_NUMBER)

    if stl.contains_date(line):
        has.add(Attr.CONTAINS_DATE)

    if stl.contains_famous_name(line):
        has.add(Attr.CONTAINS_FAMOUS_NAME)

    if stl.opens_relative_to_something(line):
        has.add(Attr.OPENS_RELATIVE_TO_SOMETHING)

    if stl.asks_the_viewer_a_question(line):
        has.add(Attr.IS_QUESTION_TO_VIEWER)

    if stl.has_something_we_could_film(line):
        has.add(Attr.HAS_SOMETHING_WE_COULD_FILM)

    # ---- statistics / data: which chart does it want? --------------------
    if stl.contains_percentage(line):
        has.add(Attr.CONTAINS_PERCENTAGE)

    if stl.contains_shares_of_a_whole(line):
        has.add(Attr.CONTAINS_SHARES_OF_A_WHOLE)

    if stl.contains_trend_over_time(line):
        has.add(Attr.CONTAINS_TREND_OVER_TIME)

    if stl.contains_several_comparable_numbers(line):
        has.add(Attr.CONTAINS_SEVERAL_NUMBERS)

    if stl.contains_single_statistic(line):
        has.add(Attr.CONTAINS_SINGLE_STATISTIC)

    if stl.opens_with_a_year(line):
        has.add(Attr.OPENS_WITH_A_YEAR)

    # ---- where the line sits, and who it leans on ------------------------
    if stl.starts_a_new_sentence(line):
        has.add(Attr.STARTS_A_NEW_SENTENCE)

    if stl.refers_back_to_something_named(line):
        has.add(Attr.REFERS_BACK_TO_A_NAME)

    # ---- the planned questions (all still answer False) -------------------
    if stl.is_single_object_focus(line):
        has.add(Attr.IS_SINGLE_OBJECT_FOCUS)

    if stl.adds_something_to_the_scene(line):
        has.add(Attr.ADDS_TO_SCENE)

    if stl.transforms_the_previous_image(line):
        has.add(Attr.TRANSFORMS_PREVIOUS)

    if stl.continues_the_previous_line(line):
        has.add(Attr.CONTINUES_PREVIOUS)

    if stl.is_abstract_concept(line):
        has.add(Attr.IS_ABSTRACT_CONCEPT)

    if stl.suits_the_board_composite(line):
        has.add(Attr.SUITS_THE_BOARD)

    if stl.is_stickman_story_beat(line):
        has.add(Attr.IS_STICKMAN_STORY_BEAT)

    if stl.is_caption_punchline(line):
        has.add(Attr.IS_CAPTION_PUNCHLINE)

    if stl.is_comparison_pair(line):
        has.add(Attr.IS_COMPARISON_PAIR)

    return has


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   STEP 2  —  THE FLOWCHART: WHAT GOES ON SCREEN?                    ### #
# ###                                                                     ### #
# ###   Read top to bottom. THE FIRST BRANCH THAT MATCHES WINS, so the     ### #
# ###   more specific a branch is, the higher up it goes.                  ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================

def decide(has: set[Attr], neighbours: Neighbours) -> Decision | None:
    """Given everything a line contains, decide what goes on screen.

    WHAT YOU GET
      has         — the attributes THIS line has, straight from STEP 1.
                    --> if Attr.CONTAINS_PLACE_NAME in has:

      neighbours  — what the lines AROUND it are doing. Only the engine can
                    know this, because it depends on what got tagged above:
                      neighbours.is_the_first_line       → nothing before it
                      neighbours.previous_media_type     → 'counter', 'map'…
                      neighbours.previous_search_term    → 'Nero'
                      neighbours.line_above_had(Attr.X)  → same run as above?
                      neighbours.line_below_has(Attr.X)  → look ahead
                      neighbours.what_matched(Attr.X)    → 'Nero' (what THIS
                                                           line's detector found)
                      neighbours.previous_line_introduced("Nero")

    WHAT YOU RETURN — tag(...), or None to leave the line for MANUAL_TAGGING.

      tag( WHICH ATTRIBUTE DROVE THIS,   ← its evidence is what gets used for
                                           the search term / the chart data
           "the media type",             ← must exist in the catalog
           ["modifiers"],                ← optional
           ... )

      The optional arguments, each with a real example:

        fill_search_term=True
            put whatever that detector matched into an EMPTY search_term.
            --> CONTAINS_PLACE_NAME matched "Indonesia", so search_term
                becomes "Indonesia". A search_term you filled in by hand is
                NEVER overwritten.

        search_term="text you build yourself"
            use this exact text instead of what the detector matched.
            --> tag(..., search_term=neighbours.what_matched(Attr.X))

        fill_chart_data=True
            write row["data"] from the chart dict the detector built.
            --> CONTAINS_PERCENTAGE matched "73%", so data={"percent": "73"},
                which is exactly what the progress bar's form expects.

        needs_a_previous_line=True
            never give this to the very FIRST line of the script.
            --> hold_previous on line 1 would be holding nothing, so line 1
                is left empty for you instead.

    THERE IS NO `one_per_run` FLAG ANY MORE. That was the hidden version of
    the "one picture per run" rule — it is now written out in the open in
    PART C, where a list becomes a group and each line lands one more cell.
    """

    # =========================================================================
    # PART A — A PRONOUN THAT LEANS ON SOMEBODY WE ALREADY NAMED
    # =========================================================================

    if Attr.REFERS_BACK_TO_A_NAME in has:
        name = neighbours.what_matched(Attr.REFERS_BACK_TO_A_NAME)

        # the line right above is where we introduced them, so hold that
        # picture and decorate it — it is already a picture of them
        # --> "Nero was emperor." [wikipedia Nero] / "He burned Rome." = hold + decorate
        if neighbours.previous_line_introduced(name):
            return tag(Attr.REFERS_BACK_TO_A_NAME, "hold_previous",
                       ["decorate"], search_term=name,
                       needs_a_previous_line=True)

        # they were named further up, so build ONE scene with several
        # pictures in it — them, plus whatever else this line mentions
        # --> "He watched the fires spread." = stock + collage, "Nero fires"
        alongside = neighbours.what_matched(Attr.HAS_SOMETHING_WE_COULD_FILM)
        return tag(Attr.REFERS_BACK_TO_A_NAME, "stock", ["collage"],
                   search_term=f"{name} {alongside}".strip())

    # =========================================================================
    # PART B — STATISTICS AND DATA GET THE RIGHT CHART
    # =========================================================================

    # a chart is already up and the sentence is still going, so keep it there
    # rather than starting a second chart mid-thought
    # --> "…rose to 4,000 tons," [line_graph] / "which was unheard of." = hold
    if (neighbours.previous_media_type in CHART_TYPES
            and Attr.STARTS_A_NEW_SENTENCE not in has):
        return tag(Attr.STARTS_A_NEW_SENTENCE, "hold_previous",
                   needs_a_previous_line=True)

    # the parts that make up one whole → a pie, one slice each
    # --> "40% gold, 35% silver and 25% spice" = pie_chart
    if Attr.CONTAINS_SHARES_OF_A_WHOLE in has:
        return tag(Attr.CONTAINS_SHARES_OF_A_WHOLE, "pie_chart",
                   fill_chart_data=True)

    # a quantity that changed over time → a line graph draws the trend
    # --> "trade grew from 200 tons in 1600 to 4,000 by 1700" = line_graph
    if Attr.CONTAINS_TREND_OVER_TIME in has:
        return tag(Attr.CONTAINS_TREND_OVER_TIME, "line_graph",
                   fill_chart_data=True)

    # one quantity out of a whole → the bar fills up to it
    # --> "73% of the ocean is unexplored" = progress_bar
    if Attr.CONTAINS_PERCENTAGE in has:
        return tag(Attr.CONTAINS_PERCENTAGE, "progress_bar",
                   fill_chart_data=True)

    # several quantities side by side → bars to compare them
    # --> "Rome had 900 ships, Athens 300" = bar_chart
    if Attr.CONTAINS_SEVERAL_NUMBERS in has:
        return tag(Attr.CONTAINS_SEVERAL_NUMBERS, "bar_chart",
                   fill_chart_data=True)

    # a year the sentence opens on → the timeline marker travels back to it
    # --> "In 1946, everything changed." = timeline, year=1946
    if Attr.OPENS_WITH_A_YEAR in has:
        return tag(Attr.OPENS_WITH_A_YEAR, "timeline", fill_chart_data=True)

    # one big figure you narrate → the counter ticks up to it
    # --> "the fine was £4,000 per sailor." = counter, value=4000, prefix=£
    if Attr.CONTAINS_SINGLE_STATISTIC in has:
        return tag(Attr.CONTAINS_SINGLE_STATISTIC, "counter",
                   fill_chart_data=True)

    # =========================================================================
    # PART C — A LIST OF VISUALISABLE NOUNS BECOMES A GROUP
    #          (this is the "one picture per run" rule, written out: the run
    #           gets ONE layout, and each line lands one more cell in it)
    # =========================================================================

    if Attr.CONTAINS_NOUN_LIST in has:

        # a later line of the same list adds the next cell to the group above
        # (only if there really IS a group above — a cell with no opener is
        #  not a cell of anything)
        # --> "pirates and" (under "scurvy,") = hold_previous + group, "pirates"
        if neighbours.line_above_had(Attr.CONTAINS_NOUN_LIST) \
                and "group" in neighbours.previous_modifiers:
            return tag(Attr.CONTAINS_NOUN_LIST, "hold_previous", ["group"],
                       fill_search_term=True, needs_a_previous_line=True)

        # the first line of a list that carries on below OPENS the group
        # --> "scurvy," (with "pirates and" under it) = stock + group, "scurvy"
        if neighbours.line_below_has(Attr.CONTAINS_NOUN_LIST):
            return tag(Attr.CONTAINS_NOUN_LIST, "stock", ["group"],
                       fill_search_term=True)

        # a whole list on ONE line wants several pictures in that one scene
        # --> "nutmeg, cloves and cinnamon ruled the world." = stock + collage
        return tag(Attr.CONTAINS_NOUN_LIST, "stock", ["collage"],
                   fill_search_term=True)

    # =========================================================================
    # PART D — NAMED THINGS AND NAMED PLACES
    # =========================================================================

    # a place the map can actually draw → the map, with the place highlighted
    # --> "in modern-day Indonesia." = map, "Indonesia"
    if Attr.CONTAINS_PLACE_NAME in has:
        return tag(Attr.CONTAINS_PLACE_NAME, "map", fill_search_term=True)

    # a named person or thing → its wikipedia image. This is also the backup
    # for a place the map has no country for — it arrives here as a plain name
    # --> "Alaric the Goth" = wikipedia, "Alaric the Goth"
    if Attr.CONTAINS_FAMOUS_NAME in has:
        return tag(Attr.CONTAINS_FAMOUS_NAME, "wikipedia",
                   fill_search_term=True)

    # =========================================================================
    # PART E — TEXT MOMENTS LAND ON THE IMAGE THAT IS ALREADY THERE
    #          (never typography: the tilted caption is automatic, and
    #           hold_previous never fetches stock, so the search term safely
    #           doubles as the caption text)
    # =========================================================================

    # something quotable → caption it over the picture already on screen
    # --> 'they called it "worth its weight in gold".' = hold + caption
    if Attr.CONTAINS_QUOTE in has:
        return tag(Attr.CONTAINS_QUOTE, "hold_previous", ["caption"],
                   fill_search_term=True, needs_a_previous_line=True)

    # a question at the viewer → caption it over the picture already there
    # --> "So what happened next?" = hold + caption
    if Attr.IS_QUESTION_TO_VIEWER in has:
        return tag(Attr.IS_QUESTION_TO_VIEWER, "hold_previous", ["caption"],
                   fill_search_term=True, needs_a_previous_line=True)

    # a date that is NOT the one the sentence opens on → caption, not timeline
    # --> "back in the 17th century" = hold + caption, "17th century"
    if Attr.CONTAINS_DATE in has:
        return tag(Attr.CONTAINS_DATE, "hold_previous", ["caption"],
                   fill_search_term=True, needs_a_previous_line=True)

    # a figure we could not build a chart out of → caption it instead
    # --> "worth twenty times its weight" = hold + caption
    if Attr.CONTAINS_BIG_NUMBER in has:
        return tag(Attr.CONTAINS_BIG_NUMBER, "hold_previous", ["caption"],
                   fill_search_term=True, needs_a_previous_line=True)

    # =========================================================================
    # PART F — IF IT IS PART OF A SENTENCE ALREADY GOING, EDIT THE PREVIOUS
    # =========================================================================

    # described relative to another thing → lean on the image already there
    # --> "beneath the old wooden floor" = hold + decorate
    if Attr.OPENS_RELATIVE_TO_SOMETHING in has:
        return tag(Attr.OPENS_RELATIVE_TO_SOMETHING, "hold_previous",
                   ["decorate"], needs_a_previous_line=True)

    if Attr.STARTS_A_NEW_SENTENCE not in has:

        # mid-sentence and it names something → stamp that onto the scene
        # --> "…and a jar of nutmeg" = hold + decorate, "jar of nutmeg"
        if Attr.HAS_SOMETHING_WE_COULD_FILM in has:
            return tag(Attr.HAS_SOMETHING_WE_COULD_FILM, "hold_previous",
                       ["decorate"], fill_search_term=True,
                       needs_a_previous_line=True)

        # mid-sentence with nothing to picture → just carry the image on
        # --> "which meant that" = hold_previous
        return tag(Attr.STARTS_A_NEW_SENTENCE, "hold_previous",
                   needs_a_previous_line=True)

    # =========================================================================
    # PART G — A NEW SENTENCE STARTS, SO DO SOMETHING NEW
    # =========================================================================

    # a new thought that names something filmable → go and get new footage
    # --> "A merchant loaded the crates." = stock, "merchant"
    if Attr.HAS_SOMETHING_WE_COULD_FILM in has:
        return tag(Attr.HAS_SOMETHING_WE_COULD_FILM, "stock",
                   fill_search_term=True)

    # nothing fired — leave it empty for MANUAL_TAGGING. That is fine.
    return None


# =============================================================================
# THE COMMAND LINE  (all the machinery is in auto_tag_engine.py)
# =============================================================================

def main(argv=None) -> int:
    if argv is None:
        import sys
        argv = sys.argv[1:]
    argv = list(argv)
    if "--selftest" in argv:
        return run_selftest()
    return main_cli(argv, Attr, collect_attributes, decide, __doc__)


def run_selftest() -> int:
    """The worked examples — kept in their own file so this one stays a
    readable flowchart."""
    from AUTO_TAG_SELFTEST import run_selftest as _run_selftest
    return _run_selftest()


if __name__ == "__main__":
    raise SystemExit(main())


# =============================================================================
# TODO — future dev work, in order of value-for-effort
# =============================================================================
# 1. Reuse the ACTUAL earlier picture for a repeated named thing (the same
#    Nero image again, stamped in or auto-arranged into the scene) — needs
#    main.py / ___visuals/ work, because collage re-searches instead of
#    reusing an earlier pick.
# 2. Fill in the PLANNED detectors in shared_text_logic.py SECTION 6 — each is
#    already wired into STEP 1, so a body is all that is missing. Cheapest
#    first: is_caption_punchline + continues_the_previous_line, then
#    adds_something_to_the_scene + transforms_the_previous_image.
# 3. Better chart labels: the bar / pie / line data currently names its points
#    'bar 1, bar 2…'. Pulling the real labels out of the sentence would make
#    those charts self-explanatory.
# 4. Turn on typography / blank / random_background if you want the cold-open
#    and the breath moments automated too — see NEVER_AUTO_ASSIGNED in
#    auto_tag_engine.py, each has a one-line reason you can delete.
# 5. Probability scoring: each detector returns 0..1 from its several signals
#    (rule_ids strongest), and a --min-confidence flag decides.
# 6. SOUND_RULES (60): lines split on 'boom/crash' should auto-fill the sfx
#    column — not a media type, but the same trust-the-splitter trick.
# =============================================================================
