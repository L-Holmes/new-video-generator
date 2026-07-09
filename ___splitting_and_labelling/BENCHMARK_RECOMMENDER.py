"""
BENCHMARK_RECOMMENDER.py — the gold-standard scenario suite.

Each scenario is a messy, realistic transcript plus what a smart human
would expect the engine to do.  Run this file to get a scorecard:

    python BENCHMARK_RECOMMENDER.py            # scorecard + failures
    python BENCHMARK_RECOMMENDER.py -v         # every check, pass or fail

The test suite imports check_scenario() so every scenario is ALSO a unit
test — but the scorecard view is what you tune WEIGHTS against.

Scenario keys (all optional):
    lines            transcript, current line = last one (or set "index")
    index            which line is current (default: last)
    pair_has         phrases that MUST appear in pairs (lowercase)
    pair_not         phrases that must NOT appear
    single_top       the term that must be ranked FIRST in singles
    single_has       terms that must appear somewhere in singles
    single_not       terms that must NOT appear in singles
    current_has      words that must be in the current-line extraction
    current_not      words that must NOT be
    pron             {"pronoun": "expected candidate"} — top guess per
                     pronoun (use "pronoun#2" for the 2nd occurrence)
    pron_not         [("pronoun", "candidate"), ...] — guesses that must
                     NOT appear at all
    pron_none        pronouns that must produce NO rows (pleonastic etc.)
"""
from __future__ import annotations

import sys

from VISUAL_RECOMMENDER import suggestions_for_line

# ============================================================================
# THE SCENARIOS — grouped by what they try to catch out
# ============================================================================
_ROME = ["Rome was not built in a day.", "But it burned in six.",
         "In 64 AD,", "a fire started in", "the merchant stalls near",
         "the Circus", "Maximus. Driven by", "wind,",
         "this hungry blaze devoured", "temples,", "villas, and",
         "entire districts.", "The city was rebuilt by",
         "a very unpopular emperor.", "Which brings us to Nero.",
         "He blamed", "a small religious sect, and",
         "the rest is history."]

SCENARIOS = [

    # ---------------------------------------------------------------- basics
    dict(name="nutmeg_story_headline",
         lines=["he gathered nutmeg",
                "then poured the nutmeg down the sink",
                "it had the texture of concrete",
                "he put it in a jar"],
         pair_has=["jar of nutmeg"], pair_not=["jar of concrete"],
         single_top="nutmeg", pron={"it": "nutmeg"},
         pron_not=[("it", "jar"), ("it", "texture")]),

    dict(name="proper_name_survives_15_lines",
         lines=["Jerry Patternly opened his shop"] +
               ["and then and then again"] * 15 +
               ["the sign swung in the wind"],
         single_has=["Jerry Patternly"], pron={}),

    # ------------------------------------------------- messy POS ambiguity
    dict(name="noun_verb_ambiguity_the_cut",
         lines=["the cut on the fabric was clean",
                "she measured it twice"],
         pron={"it": "cut"},
         current_has=[]),

    dict(name="he_books_a_room",   # 'books' after subject pronoun = VERB
         lines=["he books a room at the inn"],
         current_has=["room", "inn"], current_not=["books"]),

    dict(name="the_rolls_are_nouns",   # 'rolls' after determiner = NOUN
         lines=["she baked the rolls at dawn",
                "they smelled wonderful"],
         pron={"they": "rolls"}),

    dict(name="animal_and_metal_are_nouns_not_adjectives",
         lines=["the animal sniffed the metal bowl"],
         current_has=["animal"]),

    # ------------------------------------------------------- negation logic
    dict(name="negated_thing_is_demoted",
         lines=["there was no map in the drawer",
                "the compass gleamed",
                "he grabbed it"],
         pron={"it": "compass"}, pron_not=[("it", "map")]),

    dict(name="without_a_boat",
         lines=["they crossed without a boat",
                "the rope held firm",
                "it frayed at the end"],
         pron={"it": "rope"}, pron_not=[("it", "boat")]),

    dict(name="negated_only_mention_scores_low",
         lines=["he had no lantern", "the candle flickered",
                "and then and then again"],
         single_top="candle"),

    # ---------------------------------------------------------- idiom traps
    dict(name="raining_cats_and_dogs",
         lines=["it was raining cats and dogs",
                "the umbrella turned inside out",
                "it snapped"],
         pron={"it": "umbrella"},
         pron_not=[("it", "cats"), ("it", "dogs")],
         single_not=["cats", "dogs"]),

    dict(name="piece_of_cake_idiom",
         lines=["fixing the wheel was a piece of cake",
                "he spun it"],
         pron={"it": "wheel"}, pron_not=[("it", "cake")]),

    dict(name="kicked_the_bucket",
         lines=["the old mule finally kicked the bucket",
                "they buried it by the barn"],
         pron={"it": "old mule"},   # the adjective chunk: strictly
         pron_not=[("it", "bucket")]),  # more descriptive than "mule"

    # ------------------------------------------------------ pleonastic 'it'
    dict(name="it_was_raining_refers_to_nothing",
         lines=["the market was busy", "it was raining"],
         pron_none=["it"]),

    dict(name="it_seems_that",
         lines=["the bridge creaked", "it seems that nobody noticed"],
         pron_none=["it"]),

    dict(name="it_was_important_to",
         lines=["the letters sat unopened",
                "it was important to read them"],
         pron_none=["it"], pron={"them": "letters"}),

    dict(name="it_was_hot_is_still_referential",   # the conservative side
         lines=["the saucepan rattled", "it was hot"],
         pron={"it": "saucepan"}),

    # ------------------------------------------------------- gender + names
    dict(name="she_prefers_the_female_name",
         lines=["Mary Bell argued with Jerry Patternly",
                "she slammed the door"],
         pron={"she": "Mary Bell"}),

    dict(name="he_prefers_the_male_name",
         lines=["Mary Bell argued with Jerry Patternly",
                "he apologised at once"],
         pron={"he": "Jerry Patternly"}),

    dict(name="unknown_name_stays_open",
         lines=["Zorblat Quinn waved", "he smiled"],
         pron={"he": "Zorblat Quinn"}),

    # --------------------------------------------------- subject salience
    dict(name="subject_beats_object_for_it",
         lines=["the kettle sat on the stove", "it whistled"],
         pron={"it": "kettle"}),

    dict(name="reflexive_binds_same_sentence",
         lines=["Jerry Patternly met the tailor",
                "Mary Bell poured herself some tea"],
         pron={"herself": "Mary Bell"}),

    # ------------------------------------------------------------- pairing
    dict(name="bottle_of_milk_across_gap",
         lines=["the milk was fresh that morning",
                "and then and then again",
                "and then and then again",
                "he fetched a bottle"],
         pair_has=["bottle of milk"]),

    dict(name="slice_of_bread_portion_rule",
         lines=["the bread cooled on the rack", "she cut a slice"],
         pair_has=["slice of bread"]),

    dict(name="bar_of_chocolate",
         lines=["the chocolate melted slightly", "he unwrapped the bar"],
         pair_has=["bar of chocolate"]),

    dict(name="no_pair_for_negated_partner",
         lines=["there was no flour left", "she found a bag"],
         pair_not=["bag of flour"]),

    dict(name="sausage_roll_adjacency_recombines",
         lines=["he dropped a sausage roll on the path",
                "the pigeons noticed",
                "he rescued the sausage"],
         pair_has=["sausage roll"]),

    dict(name="jar_of_sadness_still_rejected",
         lines=["the sadness lingered", "he sealed the jar"],
         pair_not=["jar of sadness"]),

    dict(name="pile_of_books_but_not_pile_of_happiness",
         lines=["the books and the happiness filled the room",
                "she made a pile"],
         pair_has=["pile of books"], pair_not=["pile of happiness"]),

    # ------------------------------------------------ compound extraction
    dict(name="presser_foot_compound",
         lines=["lower the presser foot onto the fabric"],
         current_has=["presser foot"], current_not=["presser"]),

    dict(name="kitchen_sink_compound",
         lines=["grime coated the kitchen sink"],
         current_has=["kitchen sink"]),

    dict(name="generic_nouns_dropped_from_extraction",
         lines=["the thing about the way of the week was odd"],
         current_not=["thing", "way", "week"]),

    dict(name="time_words_never_recommended",
         lines=["it took a week", "the harvest began", "carts rolled in"],
         single_not=["week"]),

    # --------------------------------------------- multi-pronoun sentences
    dict(name="second_it_indexed_and_resolved",
         lines=["the saucepan sat beside the coins",
                "it tipped over and they scattered and it clanged"],
         pron={"it": "saucepan", "it#2": "saucepan", "they": "coins"}),

    dict(name="he_and_it_in_one_line",
         lines=["Jerry Patternly lifted the crate",
                "he dropped it"],
         pron={"he": "Jerry Patternly", "it": "crate"}),

    # -------------------------------------------------------- misc nasties
    dict(name="simile_like_a_rocket",
         lines=["the cart shot off like a rocket",
                "its wheel wobbled",
                "it needed grease"],
         pron_not=[("it", "rocket")]),

    dict(name="quotes_and_allcaps_mix",
         lines=["the sign read 'FRESH NUTMEG SOLD HERE'",
                "he repainted it"],
         pron={"it": "sign"}),

    dict(name="statue_of_liberty_is_one_name",
         lines=["they photographed the Statue of Liberty",
                "it glowed at dusk"],
         pron={"it": "Statue of Liberty"}),

    dict(name="former_latter_in_context",
         lines=["the cat eyed the dog across the yard",
                "the former hissed while the latter barked"],
         pron={"former": "cat", "latter": "dog"}),

    dict(name="relative_clause_mid_story",
         lines=["he oiled the hinge",
                "the door that squeaked all winter finally opened"],
         pron={"that": "door"}),

    dict(name="one_substitution_after_gap",
         lines=["she admired the lantern in the window",
                "and then and then again",
                "she finally bought one"],
         pron={"one": "lantern"}),

    dict(name="contraction_storm",
         lines=["the coins spilled from Jerry's pouch",
                "they're everywhere and he's furious"],
         pron={"they": "coins", "he": "jerry"}),

    # =====================================================================
    # WAVE 2 — the adversarial set
    # =====================================================================

    # ------------------------------------------------ unicode + typography
    dict(name="pinata_survives_unicode",
         lines=["the piñata hung over the café table", "he whacked it"],
         current_has=[], pron={"it": "piñata"}),

    dict(name="obrien_is_a_person",
         lines=["O'Brien fixed the wagon", "he wiped his hands"],
         pron={"he": "O'Brien"}),

    dict(name="dashes_and_semicolons",
         lines=["the anvil — heavy, black — sat there; it gleamed"],
         pron={"it": "anvil"}),

    dict(name="quoted_dialogue_then_pronouns",
         lines=["'give me the map' said the captain",
                "he unfolded it slowly"],
         pron={"he": "captain", "it": "map"}),

    # -------------------------------------------------- object-case logic
    dict(name="him_prefers_the_object",
         lines=["Jerry Patternly met Leonard Nimoy",
                "he greeted him warmly"],
         pron={"he": "Jerry Patternly", "him": "Leonard Nimoy"}),

    dict(name="her_object_case",
         lines=["Mary Bell visited Lucy Gray", "she hugged her"],
         pron={"she": "Mary Bell", "her": "Lucy Gray"}),

    # ------------------------------------------------ compounds, round 2
    dict(name="compound_chain_greedy",
         lines=["the sewing machine needle case fell open"],
         current_has=["sewing machine", "needle case"],
         current_not=["machine needle"]),

    dict(name="generic_head_compound_ok",
         lines=["she opened the needle case"],
         current_has=["needle case"]),

    dict(name="generic_modifier_compound_blocked",
         lines=["the thing table wobbled"],
         current_not=["thing table", "thing"]),

    dict(name="hyphenated_compound_kept_whole",
         lines=["the sewing-machine hummed", "it needed oil"],
         pron={"it": "sewing-machine"}),

    # ------------------------------------------------ units + quantities
    dict(name="units_never_extracted",
         lines=["he walked five miles with a kilogram of rice"],
         current_has=["rice"], current_not=["miles", "kilogram"]),

    dict(name="presser_foot_unhurt_by_units",   # foot != a unit here
         lines=["lower the presser foot gently"],
         current_has=["presser foot"]),

    dict(name="couple_of_ducks",
         lines=["a couple of ducks crossed the road", "they quacked"],
         pron={"they": "ducks"}, current_not=["couple"]),

    # --------------------------------------------- negation transitions
    dict(name="negated_then_found",
         lines=["there was no jar anywhere",
                "at last he found a jar",
                "he polished it"],
         pron={"it": "jar"}),

    dict(name="idiom_then_literal",
         lines=["it was raining cats and dogs",
                "the dogs barked at the gate",
                "they would not stop"],
         pron={"they": "dogs"}),

    # ------------------------------------------------- pleonastic, round 2
    dict(name="weather_then_referential_same_line",
         lines=["the tent sagged", "it was raining and it leaked"],
         pron={"it#2": "tent"}, pron_none=[]),

    dict(name="it_takes_time_to",
         lines=["the dough rested", "it takes patience to bake"],
         pron_none=["it"]),

    # ------------------------------------------------ caps + shouting
    dict(name="allcaps_person_still_a_person",
         lines=["JERRY SHOUTED AT THE STORM", "he trembled"],
         pron={"he": "jerry"}),

    dict(name="allcaps_sign_content",
         lines=["the notice said 'NO DOGS ALLOWED BEYOND THE GATE'",
                "the gate stood open anyway",
                "it creaked"],
         pron={"it": "gate"}),

    # ------------------------------------------------ chains + distractors
    dict(name="pronoun_chain_stays_consistent",
         lines=["he picked up the jar",
                "he shook it",
                "he dropped it"],
         pron={"it": "jar"}),

    dict(name="distractor_flood_recency_wins",
         lines=["the hammer the kettle the ladder the mirror lay about",
                "the candle the barrel the anchor gathered dust",
                "the lantern flickered",
                "it went out"],
         pron={"it": "lantern"}),

    dict(name="verb_frame_across_lines",
         lines=["she plucked the goose by the fire",
                "later she plucked it again"],
         pron={"it": "goose"}),

    # ------------------------------------------------ pairing, round 2
    dict(name="cup_of_tea_classic",
         lines=["the tea steamed in the pot", "she fetched a cup"],
         pair_has=["cup of tea"]),

    dict(name="pair_survives_pronoun_line_between",
         lines=["the nutmeg spilled",
                "it rolled everywhere",
                "he grabbed the jar"],
         pair_has=["jar of nutmeg"]),

    # =====================================================================
    # WAVE 3 — the deep-logic set (verb-scope negation, collectives,
    # grammar hints, conservatism checks)
    # =====================================================================

    # ------------------------------- negation with verb scope (the smart bit)
    dict(name="couldnt_find_means_absent",
         lines=["he couldn't find the map anywhere",
                "the compass pointed north",
                "it spun"],
         pron={"it": "compass"}, pron_not=[("it", "map")],
         single_not=["north"]),

    dict(name="didnt_drop_means_still_present",   # the flip side!
         lines=["he didn't drop the jar", "it cracked anyway"],
         pron={"it": "jar"}),

    dict(name="never_saw_the_ghost",
         lines=["they never saw the ghost",
                "the floorboards creaked",
                "he stared at them"],
         pron_not=[("them", "ghost")]),

    # --------------------------------------------------- collectives
    dict(name="crowd_is_they",
         lines=["the crowd cheered in the square", "they surged forward"],
         pron={"they": "crowd"}),

    dict(name="flock_is_they",
         lines=["a flock settled on the wire", "they scattered at the bang"],
         pron={"they": "flock"}),

    # --------------------------------------------------- grammar-hint traps
    dict(name="time_flies_like_an_arrow",
         lines=["time flies like an arrow", "the fruit ripened"],
         single_not=["arrow", "time"],
         current_not=["fruit flies"]),

    dict(name="aux_verb_never_a_noun",
         lines=["she must hide the letters", "he could climb the wall"],
         index=1,
         current_has=["wall"], current_not=["climb"],
         single_not=["hide"]),

    # --------------------------------------------------- determiner 'her'
    dict(name="her_as_determiner_still_points_at_owner",
         lines=["Mary Bell polished her locket"],
         pron={"her": "Mary Bell"}),

    # --------------------------------------------------- names with titles
    dict(name="mr_patternly_resolves",
         lines=["Mr Patternly waved from the door", "he left"],
         pron={"he": "Mr Patternly"}),

    # --------------------------------------------------- plural lists
    dict(name="fruit_list_is_they",
         lines=["she bought apples and pears and plums",
                "they were ripe"],
         pron={"they": "apples"}),

    # --------------------------------------------------- conservatism checks
    dict(name="it_looked_heavy_still_referential",
         lines=["the anvil arrived", "it looked heavy"],
         pron={"it": "anvil"}),

    dict(name="nothing_but_a_jar_is_present",
         lines=["there was nothing but a jar on the shelf",
                "he opened it"],
         pron={"it": "jar"}),

    dict(name="directions_dropped_from_extraction",
         lines=["the compass pointed north near the harbour"],
         current_has=["compass", "harbour"], current_not=["north"]),

    dict(name="topic_shift_recency_beats_frequency",
         lines=["the ship creaked", "the ship rolled", "the ship groaned",
                "they reached the market at noon",
                "stalls lined the alley",
                "a fiddler played by the fountain",
                "it sparkled in the sun"],
         pron={"it": "fountain"}),

    dict(name="possessive_chain_owner_counted",
         lines=["Jerry's kettle whistled on Jerry's stove",
                "he silenced it"],
         pron={"he": "jerry"}),

    dict(name="no_crowd_of_jar_nonsense",
         lines=["the nutmeg sat by the jar and the sink",
                "the crowd watched as it sealed itself"],
         pair_not=["crowd of jar", "crowd of nutmeg", "crowd of sink"]),

    dict(name="flock_of_geese_animate_group",
         lines=["the geese honked across the pond", "a flock gathered"],
         pair_has=["flock of geese"]),

    dict(name="pile_of_jar_blocked_singular",
         lines=["the jar glinted", "he made a pile"],
         pair_not=["pile of jar"]),

    # =====================================================================
    # WAVE 4 — partitives, cataphora, number words, candidate hygiene
    # =====================================================================

    dict(name="one_of_the_lanterns_partitive",
         lines=["three lamps hung in the hall",
                "one of the lanterns toppled"],
         pron={"one": "lanterns"}),

    dict(name="each_of_the_horses_partitive",
         lines=["the stable stood quiet", "each of the horses stirred"],
         pron={"each": "horses"}),

    dict(name="cataphora_before_he_left",
         lines=["before he left, Jerry Patternly locked the door"],
         pron={"he": "Jerry Patternly"}),

    dict(name="cataphora_respects_gender",
         lines=["before she left, Jerry Patternly locked the door"],
         pron_not=[("she", "Jerry Patternly")]),

    dict(name="cataphora_needs_a_proper_noun",
         lines=["before he left, the door slammed"],
         pron_not=[("he", "door")]),

    dict(name="number_words_never_extracted",
         lines=["three lamps hung in the hall near two benches"],
         current_has=["lamps", "benches"],
         current_not=["three", "two", "hung"]),

    dict(name="irregular_past_verbs_not_nouns",
         lines=["the curtain rose and the gull flew and the bell rang"],
         current_has=["curtain", "gull", "bell"],
         current_not=["rose", "flew", "rang"]),

    dict(name="compound_suppresses_its_parts_in_panel",
         lines=["the sewing machine jammed", "it rattled"],
         pron={"it": "sewing machine"},
         pron_not=[("it", "sewing"), ("it", "machine")]),

    dict(name="its_determiner_owner",
         lines=["the jar stood open", "its lid was missing"],
         pron={"its": "jar"}),

    dict(name="he_saw_the_saw",
         lines=["he saw the saw on the bench"],
         current_has=["saw", "bench"]),

    dict(name="dozens_of_boxes",
         lines=["dozens of boxes arrived"],
         current_has=["boxes"], current_not=["dozens"]),

    # =====================================================================
    # WAVE 5 — the real-world fragment test (the user's actual Rome
    # script, caption fragments and all) + the theme layer
    # =====================================================================

    dict(name="rome_ad_is_not_a_visualisable",
         lines=_ROME, index=2,
         current_not=["AD", "Ad"]),

    dict(name="rome_circus_maximus_merges_across_fragments",
         lines=_ROME, index=6,
         current_has=["Circus Maximus"], current_not=["Maximus Driven"]),

    dict(name="rome_theme_combo_merchant_stalls",
         lines=_ROME, index=4,
         pair_has=["roman merchant stalls",
                   "roman merchant stalls 64 ad"],
         pair_not=["ad rome", "stalls stalls",
                   "merchant stalls stalls"]),

    dict(name="rome_theme_combo_temples",
         lines=_ROME, index=9,
         pair_has=["roman temples"],
         pair_not=["temples fire", "temple stalls", "temples villas"]),

    dict(name="rome_districts_clean",
         lines=_ROME, index=11,
         current_has=["districts"], current_not=["entire districts"],
         pair_has=["roman districts"],
         pair_not=["entire districts districts"]),

    dict(name="rome_wind_stays_standalone",
         lines=_ROME, index=7,
         pair_not=["roman wind", "roman wind 64 ad"]),

    dict(name="rome_city_anchors_back_to_rome",
         lines=_ROME, index=12,
         pair_has=["rome"], pair_not=["roman city"]),

    dict(name="rome_emperor_nero_title_first",
         lines=_ROME, index=14,
         pair_has=["emperor nero"], pair_not=["nero emperor"]),

    dict(name="rome_he_blamed_is_nero",
         lines=_ROME, index=15,
         pron={"he": "Nero"}),

    dict(name="rome_rest_is_history_idiom",
         lines=_ROME, index=17,
         current_not=["rest", "history"],
         pair_not=["roman rest", "roman history"]),

    dict(name="rome_theme_survives_whole_script",
         lines=_ROME, index=16,
         single_has=["Rome"]),

    # ------------------------ theme layer, synthetic checks -------------
    dict(name="medieval_theme_from_era_word",
         lines=["the medieval fair opened at dawn",
                "knights paraded past the medieval gates",
                "a blacksmith hammered away"],
         pair_has=["medieval blacksmith"]),

    dict(name="no_theme_no_combo",
         lines=["the fair opened at dawn", "a blacksmith hammered away"],
         pair_not=["roman blacksmith", "medieval blacksmith"]),

    dict(name="comma_lists_never_compound",
         lines=["she packed hats, boots, and scarves"],
         current_has=["hats", "boots", "scarves"],
         current_not=["hats boots", "boots scarves"]),

    dict(name="modern_year_not_appended",
         lines=["back in 2019 the cafe opened",
                "the espresso machine gleamed"],
         pair_not=["espresso machine 2019"]),

    # =====================================================================
    # WAVE 6 — generalisation: the engine must work for ANY text, not
    # just Rome.  Names via gender-guesser (48k, international), person /
    # weather / unit / collective classes via WordNet, places + demonyms
    # via mledoze/countries, concreteness via Brysbaert ratings.
    # =====================================================================

    dict(name="greek_prophets_generalise",       # the user's challenge
         lines=["Delphi sat high in the mountains of Greece",
                "pilgrims climbed to Greece's sacred slopes",
                "the prophets spoke in riddles"],
         pair_has=["greek prophets"]),

    dict(name="greek_name_gender_resolves",
         lines=["Ioannis argued with Thalia at the harbour",
                "she stormed off"],
         pron={"she": "Thalia"}),

    dict(name="greek_name_gender_he",
         lines=["Ioannis argued with Thalia at the harbour",
                "he apologised"],
         pron={"he": "Ioannis"}),

    dict(name="japan_demonym_from_dataset",
         lines=["Japan in the 1600s was closed to the world",
                "Japan guarded its ports",
                "a merchant ship appeared"],
         pair_has=["japanese merchant ship",
                   "japanese merchant ship 1600s"]),

    dict(name="kyoto_name_as_modifier_fallback",
         lines=["Kyoto glowed at dusk",
                "lanterns lined Kyoto's streets",
                "the temples fell silent"],
         pair_has=["kyoto temples"]),

    dict(name="wordnet_person_centurion",
         lines=["a centurion marched past the aqueduct",
                "he saluted"],
         pron={"he": "centurion"}),

    dict(name="wordnet_weather_immune",
         lines=["Egypt shimmered in the heat", "Egypt slept",
                "a sandstorm rolled in"],
         pair_not=["egyptian sandstorm"]),

    dict(name="wordnet_unit_furlong_demoted",
         lines=["the horse ran a furlong past the mill"],
         current_has=["horse", "mill"], current_not=["furlong"]),

    dict(name="wordnet_collective_congregation",
         lines=["the congregation gathered at dawn", "they sang"],
         pron={"they": "congregation"}),

    dict(name="brysbaert_abstract_melancholy",
         lines=["a deep melancholy settled over the docks",
                "the cranes stood idle"],
         single_not=["melancholy"]),

    dict(name="adjective_chunk_religious_sect",
         lines=["Rome was not built in a day.", "Rome burned.",
                "a small religious sect was blamed"],
         current_has=["religious sect"], current_not=["sect", "small"],
         pair_has=["roman religious sect"]),

    dict(name="adjective_chunk_skips_bland_sizes",
         lines=["a big wooden cart rolled by"],
         current_has=["wooden cart"], current_not=["big wooden cart"]),

    dict(name="complementizer_that_after_verb",
         lines=["the bridge creaked", "he said that all was well"],
         pron_not=[("that", "bridge")]),
]


# ============================================================================
# the checker + scorecard
# ============================================================================

def _pron_lookup(rows, spec):
    """spec 'it' -> occurrence 1; 'it#2' -> occurrence 2."""
    name, _, occ = spec.partition("#")
    occ = int(occ or 1)
    best = [r for r in rows if r["pronoun"] == name
            and r["occurrence"] == occ]
    best.sort(key=lambda r: -r["prob"])
    return best


def check_scenario(sc) -> list[str]:
    """Run one scenario; return a list of failure strings (empty = pass)."""
    lines = sc["lines"]
    idx = sc.get("index", len(lines) - 1)
    p = suggestions_for_line(lines, idx)
    fails = []

    pairs = [x["term"].lower() for x in p["pairs"]]
    singles = [x["term"] for x in p["singles"]]
    singles_low = [s.lower() for s in singles]
    current_low = [c.lower() for c in p["current"]]

    for want in sc.get("pair_has", []):
        if want not in pairs:
            fails.append(f"pair missing: {want!r} (got {pairs})")
    for bad in sc.get("pair_not", []):
        if bad in pairs:
            fails.append(f"pair must not appear: {bad!r}")
    if "single_top" in sc:
        top = singles_low[0] if singles_low else None
        if top != sc["single_top"].lower():
            fails.append(f"single_top: wanted {sc['single_top']!r}, "
                         f"got {top!r} (all: {singles})")
    for want in sc.get("single_has", []):
        if want.lower() not in singles_low:
            fails.append(f"single missing: {want!r} (got {singles})")
    for bad in sc.get("single_not", []):
        if bad.lower() in singles_low:
            fails.append(f"single must not appear: {bad!r}")
    for want in sc.get("current_has", []):
        if want.lower() not in current_low:
            fails.append(f"current missing: {want!r} (got {p['current']})")
    for bad in sc.get("current_not", []):
        if bad.lower() in current_low:
            fails.append(f"current must not contain: {bad!r}")

    for spec, want in sc.get("pron", {}).items():
        rows = _pron_lookup(p["pronouns"], spec)
        got = rows[0]["candidate"].lower() if rows else None
        if got != want.lower():
            fails.append(f"pron {spec!r}: wanted {want!r}, got {got!r} "
                         f"(rows: {[(r['candidate'], r['prob']) for r in rows]})")
    for pron, bad in sc.get("pron_not", []):
        rows = _pron_lookup(p["pronouns"], pron)
        if any(r["candidate"].lower() == bad.lower() for r in rows):
            fails.append(f"pron {pron!r} must not offer {bad!r}")
    for pron in sc.get("pron_none", []):
        rows = _pron_lookup(p["pronouns"], pron)
        if rows:
            fails.append(f"pron {pron!r} must produce NO rows, got "
                         f"{[(r['candidate'], r['prob']) for r in rows]}")
    return fails


def run(verbose: bool = False) -> tuple[int, int]:
    passed = 0
    for sc in SCENARIOS:
        fails = check_scenario(sc)
        if not fails:
            passed += 1
            if verbose:
                print(f"  ok    {sc['name']}")
        else:
            print(f"  FAIL  {sc['name']}")
            for f in fails:
                print(f"          - {f}")
    total = len(SCENARIOS)
    pct = 100.0 * passed / total
    print(f"\nSCORECARD: {passed}/{total} scenarios  ({pct:.0f}%)")
    return passed, total


if __name__ == "__main__":
    passed, total = run(verbose="-v" in sys.argv)
    sys.exit(0 if passed == total else 1)
