"""
test_visual_recommender.py — the test suite for VISUAL_RECOMMENDER.

Runs with pytest (`pytest test_visual_recommender.py -q`) OR with plain
python (`python test_visual_recommender.py`) — no dependencies.

Most tests use the format requested: a list of visualisable words from
previous entries with HOW FAR BACK they were, then the current entry's
visualisables, then the target we expect the engine to reach
(e.g. "Jar of Nutmeg").
"""
from __future__ import annotations

import json

from VISUAL_RECOMMENDER import (
    Recommender,
    WEIGHTS,
    extract_visualisables,
    recency_multiplier,
    suggestions_for_line,
    tokenize,
    merge_proper_runs,
    looks_like_verb,
    looks_abstract,
)


# ---------------------------------------------------------------------------
# helper: build a Recommender from the requested test format —
#     history = [(word, distance_back), ...]
# distance_back 1 = the previous sentence, 15 = fifteen entries ago.
# We fabricate one tiny sentence per mention at the right entry index.
# ---------------------------------------------------------------------------
CURRENT = 20   # the "current" entry index used by these fabricated tests


def build_from_distances(history: list[tuple[str, int]],
                         current_index: int = CURRENT) -> Recommender:
    rec = Recommender()
    by_entry: dict[int, list[str]] = {}
    for word, dist in history:
        by_entry.setdefault(current_index - dist, []).append(word)
    for idx in range(current_index):
        words = by_entry.get(idx, [])
        rec.observe_entry(idx, ("some filler words about the " +
                                " and the ".join(words)) if words
                          else "and then and then again")
    return rec


def pair_terms(rec: Recommender, current: list[str],
               current_index: int = CURRENT) -> list[str]:
    return [row["term"].lower()
            for row in rec.pair_suggestions(current_index, current)]


# ===========================================================================
# 1. POS heuristics
# ===========================================================================

def test_proper_noun_mid_sentence():
    trips = merge_proper_runs(tokenize("we met Leonard Nimoy yesterday"),
                              set())
    props = [t for t in trips if t[1] == "PROPER_NOUN"]
    assert props and props[0][0] == "Leonard Nimoy"   # merged into one


def test_sentence_start_common_word_is_not_proper():
    # "Nutmeg" at line start, but we've seen it lowercase => not a name
    trips = merge_proper_runs(tokenize("Nutmeg is lovely"), {"nutmeg"})
    assert all(p != "PROPER_NOUN" for _, p, _ in trips)


def test_sentence_start_unknown_capital_is_proper():
    trips = merge_proper_runs(tokenize("Jerry Patternly gathered nutmeg"),
                              set())
    assert trips[0][0] == "Jerry Patternly"
    assert trips[0][1] == "PROPER_NOUN"


def test_verb_detection():
    assert looks_like_verb("poured")
    assert looks_like_verb("gathering")
    assert looks_like_verb("put")
    assert not looks_like_verb("building")   # ING_NOUNS exception
    assert not looks_like_verb("nutmeg")


def test_abstract_detection():
    assert looks_abstract("happiness")
    assert looks_abstract("inflation")
    assert not looks_abstract("saucepan")


def test_extract_visualisables_basic():
    words = extract_visualisables(
        "when i look through the eye of the needle, am i looking at the "
        "side of the presser foot?")
    lower = [w.lower() for w in words]
    assert "needle" in lower
    assert "presser" in lower or "presser foot" in " ".join(lower)
    assert "eye" in lower
    assert "looking" not in lower      # verb
    assert "i" not in lower            # pronoun/stopword


# ===========================================================================
# 2. Scoring: the individual rules from the spec
# ===========================================================================

def test_recency_decay_monotonic_with_floor():
    vals = [recency_multiplier(d) for d in (1, 2, 5, 10, 15, 40)]
    assert vals[0] == 1.0
    assert all(a >= b for a, b in zip(vals, vals[1:]))     # non-increasing
    assert vals[-1] == WEIGHTS["RECENCY_FLOOR"]            # floored
    assert vals[-1] > 0                                    # "but not zero"


def test_previous_sentence_beats_fifteen_back():
    rec = build_from_distances([("hammer", 1), ("kettle", 15)])
    assert rec.score("hammer", CURRENT) > rec.score("kettle", CURRENT)
    assert rec.score("kettle", CURRENT) > 0    # old context still counts


def test_frequency_raises_score():
    rec = build_from_distances([("nutmeg", 3), ("nutmeg", 2), ("sink", 2)])
    assert rec.score("nutmeg", CURRENT) > rec.score("sink", CURRENT)


def test_proper_noun_beats_common_noun_beats_verb():
    r = Recommender()
    r.observe_entry(0, "Jerry Patternly poured nutmeg")
    s_proper = r.score("jerry patternly", 1)
    s_noun = r.score("nutmeg", 1)
    s_verb = r.score("poured", 1)
    assert s_proper > s_noun > s_verb


def test_confirmed_term_gets_boost():
    a = build_from_distances([("lantern", 2)])
    b = build_from_distances([("lantern", 2)])
    b.confirm("lantern")
    assert b.score("lantern", CURRENT) > a.score("lantern", CURRENT)


def test_abstract_noun_penalised():
    r = Recommender()
    r.observe_entry(0, "the happiness and the saucepan")
    assert r.score("saucepan", 1) > r.score("happiness", 1)


def test_simile_word_penalised():
    r = Recommender()
    r.observe_entry(0, "it had the texture of concrete")
    r.observe_entry(1, "the nutmeg was fresh")
    # 'concrete' only ever appeared as a comparison — should trail nutmeg
    assert r.score("nutmeg", 2) > r.score("concrete", 2)


def test_stopwords_never_scored():
    r = Recommender()
    r.observe_entry(0, "the of and with under")
    assert r.score("the", 1) == 0.0
    assert r.score("with", 1) == 0.0


def test_top_singles_threshold_and_cap():
    rec = build_from_distances(
        [("hammer", 1), ("kettle", 1), ("ladder", 1), ("mirror", 1),
         ("candle", 1), ("barrel", 1), ("anchor", 1), ("drum", 1)])
    rows = rec.top_singles(CURRENT)
    assert 0 < len(rows) <= WEIGHTS["MAX_SINGLES"]
    assert all(r["score"] >= WEIGHTS["SHOW_THRESHOLD"] for r in rows)
    # sorted best-first
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


# ===========================================================================
# 3. Pairing — the requested list-of-distances format
# ===========================================================================

def test_jar_of_nutmeg_from_distances():
    # THE headline example.
    rec = build_from_distances(
        [("nutmeg", 3), ("nutmeg", 2), ("sink", 2), ("concrete", 1)])
    terms = pair_terms(rec, ["jar"])
    assert "jar of nutmeg" in terms


def test_jar_of_concrete_rejected():
    # concrete is not in the JARRABLE whitelist -> no suggestion
    rec = build_from_distances([("concrete", 1)])
    assert "jar of concrete" not in pair_terms(rec, ["jar"])


def test_jar_of_sadness_rejected():
    rec = build_from_distances([("sadness", 1)])
    assert pair_terms(rec, ["jar"]) == []


def test_bag_of_flour():
    rec = build_from_distances([("flour", 2), ("flour", 4)])
    assert "bag of flour" in pair_terms(rec, ["bag"])


def test_container_in_memory_current_is_substance():
    # order-agnostic: the container was in the PAST, substance is current
    rec = build_from_distances([("bottle", 1)])
    assert "bottle of milk" in pair_terms(rec, ["milk"])


def test_pile_of_books_group_rule():
    rec = build_from_distances([("books", 1), ("books", 3)])
    assert "pile of books" in pair_terms(rec, ["pile"])


def test_group_of_abstract_rejected():
    rec = build_from_distances([("happiness", 1)])
    assert pair_terms(rec, ["pile"]) == []


def test_man_wearing_hat():
    rec = build_from_distances([("hat", 1), ("hat", 2)])
    assert "man wearing hat" in pair_terms(rec, ["man"])


def test_learned_adjacency_sausage_roll():
    # "sausage roll" seen as adjacent words earlier => recombine, in the
    # ORIGINAL order, even though no curated rule knows about it
    r = Recommender()
    r.observe_entry(0, "he dropped a sausage roll on the floor")
    r.observe_entry(1, "the floor was sticky")
    r.observe_entry(2, "he picked up the sausage")
    terms = [row["term"].lower() for row in
             r.pair_suggestions(2, ["roll"])]
    assert "sausage roll" in terms


def test_far_context_scores_lower_than_near():
    near = build_from_distances([("nutmeg", 1)])
    far = build_from_distances([("nutmeg", 14)])
    n = near.pair_suggestions(CURRENT, ["jar"])
    f = far.pair_suggestions(CURRENT, ["jar"])
    assert n and f                       # both still suggest (floor > 0)...
    assert n[0]["score"] > f[0]["score"]  # ...but nearer scores higher


def test_pair_cap_and_ordering():
    rec = build_from_distances(
        [("nutmeg", 1), ("flour", 1), ("sugar", 2), ("coffee", 2),
         ("honey", 3)])
    rows = rec.pair_suggestions(CURRENT, ["jar"])
    assert 0 < len(rows) <= WEIGHTS["MAX_PAIRS"]
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_pair_display_capitalisation():
    rec = build_from_distances([("nutmeg", 1)])
    rows = rec.pair_suggestions(CURRENT, ["jar"])
    assert rows[0]["term"] == "Jar of Nutmeg"


# ===========================================================================
# 4. Pronoun resolution
# ===========================================================================

def prob_of(rows, pronoun, candidate, occurrence=1):
    for r in rows:
        if (r["pronoun"] == pronoun and r["occurrence"] == occurrence
                and r["candidate"].lower() == candidate.lower()):
            return r["prob"]
    return None


def test_he_points_at_person_not_thing():
    r = Recommender()
    r.observe_entry(0, "Jerry Patternly walked past the saucepan")
    rows = r.resolve_pronouns(1, "he waved at the crowd")
    assert prob_of(rows, "he", "Jerry Patternly") is not None
    assert prob_of(rows, "he", "saucepan") is None


def test_it_points_at_thing_not_person():
    r = Recommender()
    r.observe_entry(0, "Jerry Patternly dropped the saucepan")
    rows = r.resolve_pronouns(1, "it clattered on the floor")
    assert prob_of(rows, "it", "saucepan") is not None
    assert prob_of(rows, "it", "Jerry Patternly") is None


def test_they_prefers_plural():
    r = Recommender()
    r.observe_entry(0, "the horses stood near the barn")
    rows = r.resolve_pronouns(1, "they galloped away")
    p_horses = prob_of(rows, "they", "horses")
    p_barn = prob_of(rows, "they", "barn")
    assert p_horses is not None
    assert p_barn is None or p_horses > p_barn


def test_glass_is_not_plural():
    r = Recommender()
    r.observe_entry(0, "the glass sat beside the coins")
    rows = r.resolve_pronouns(1, "they sparkled")
    assert prob_of(rows, "they", "coins") is not None
    # 'glass' ends in s but is singular -> must not be the plural pick
    p_glass = prob_of(rows, "they", "glass")
    assert p_glass is None or p_glass < prob_of(rows, "they", "coins")


def test_recency_beats_older_candidate():
    r = Recommender()
    r.observe_entry(0, "the kettle whistled")
    r.observe_entry(1, "and then and then again")
    r.observe_entry(2, "and then and then again")
    r.observe_entry(3, "the lantern flickered")
    rows = r.resolve_pronouns(4, "it went dark")
    assert prob_of(rows, "it", "lantern") > prob_of(rows, "it", "kettle")


def test_frequency_beats_equal_recency():
    # nutmeg and sink both in the previous sentence; nutmeg seen twice
    r = Recommender()
    r.observe_entry(0, "he gathered nutmeg")
    r.observe_entry(1, "then poured the nutmeg down the sink")
    rows = r.resolve_pronouns(2, "it had the texture of concrete")
    assert prob_of(rows, "it", "nutmeg") > prob_of(rows, "it", "sink")


def test_same_sentence_after_pronoun_excluded():
    # "he put it in a jar" — 'it' cannot be the jar
    r = Recommender()
    r.observe_entry(0, "he gathered nutmeg")
    r.observe_entry(1, "he put it in a jar")
    rows = r.resolve_pronouns(1, "he put it in a jar")
    assert prob_of(rows, "it", "jar") is None
    assert prob_of(rows, "it", "nutmeg") is not None


def test_verb_frame_boost():
    # "poured the nutmeg" earlier; "poured it" now => it≈nutmeg gets boost
    r = Recommender()
    r.observe_entry(0, "he poured the nutmeg and admired the sink")
    base_rows = r.resolve_pronouns(1, "he lifted it")
    boosted_rows = r.resolve_pronouns(1, "he poured it")
    assert (prob_of(boosted_rows, "it", "nutmeg")
            >= prob_of(base_rows, "it", "nutmeg"))


def test_second_it_indexed():
    r = Recommender()
    r.observe_entry(0, "the saucepan sat by the kettle")
    rows = r.resolve_pronouns(
        1, "it was hot and it was heavy")
    occs = {r_["occurrence"] for r_ in rows if r_["pronoun"] == "it"}
    assert occs == {1, 2}


def test_probabilities_are_sane():
    r = Recommender()
    r.observe_entry(0, "the saucepan sat by the kettle and the ladder")
    rows = r.resolve_pronouns(1, "it wobbled")
    probs = [x["prob"] for x in rows if x["pronoun"] == "it"]
    assert probs
    assert all(0.0 < p < 1.0 for p in probs)
    assert sum(probs) < 1.0        # smoothing leaves "none of these" mass


def test_pronoun_candidate_cap():
    r = Recommender()
    r.observe_entry(0, "a hammer a kettle a ladder a mirror a candle")
    rows = r.resolve_pronouns(1, "it fell")
    its = [x for x in rows if x["pronoun"] == "it"]
    assert 0 < len(its) <= WEIGHTS["PRONOUN_MAX_CANDIDATES"]


# ===========================================================================
# 5. End-to-end: the exact story from the brief
# ===========================================================================

STORY = [
    "he gathered nutmeg",
    "then poured the nutmeg down the sink",
    "it had the texture of concrete",
    "he put it in a jar",
]


def test_story_reaches_jar_of_nutmeg():
    payload = suggestions_for_line(STORY, 3)
    pair_terms_ = [p["term"].lower() for p in payload["pairs"]]
    assert "jar of nutmeg" in pair_terms_
    assert "jar of concrete" not in pair_terms_


def test_story_pronoun_panel():
    payload = suggestions_for_line(STORY, 3)
    rows = payload["pronouns"]
    p_nutmeg = prob_of(rows, "it", "nutmeg")
    assert p_nutmeg is not None
    p_jar = prob_of(rows, "it", "jar")
    assert p_jar is None                        # after the pronoun
    p_concrete = prob_of(rows, "it", "concrete")
    assert p_concrete is None or p_concrete < p_nutmeg   # simile demoted


def test_story_singles_led_by_nutmeg():
    payload = suggestions_for_line(STORY, 3)
    singles = [s["term"].lower() for s in payload["singles"]]
    assert singles and singles[0] == "nutmeg"


def test_payload_is_json_serialisable():
    payload = suggestions_for_line(STORY, 3)
    blob = json.loads(json.dumps(payload))
    assert set(blob) == {"current", "singles", "pairs", "pronouns"}


def test_confirmed_terms_flow_through():
    lines = ["the lantern glowed", "and then and then again", "shadows moved"]
    without = suggestions_for_line(lines, 2)
    with_conf = suggestions_for_line(lines, 2, confirmed_terms={0: "lantern"})
    s_wo = next((s["score"] for s in without["singles"]
                 if s["term"].lower() == "lantern"), 0)
    s_w = next((s["score"] for s in with_conf["singles"]
                if s["term"].lower() == "lantern"), 0)
    assert s_w > s_wo


def test_stateless_wrapper_only_uses_past():
    # a word that only appears AFTER the current line must not leak in
    lines = ["the kettle whistled", "steam rose", "the dragon appeared"]
    payload = suggestions_for_line(lines, 1)
    all_terms = [s["term"].lower() for s in payload["singles"]]
    assert "dragon" not in all_terms


def test_sewing_machine_question():
    # the presser-foot sentence from the brief, in context
    lines = [
        "thread the needle before you start",
        "lower the presser foot onto the fabric",
        "when i look through the eye of the needle, am i looking at the "
        "side of the presser foot?",
    ]
    payload = suggestions_for_line(lines, 2)
    current = [w.lower() for w in payload["current"]]
    assert "needle" in current
    remembered = [s["term"].lower() for s in payload["singles"]]
    combined = " ".join(remembered + current)
    assert "presser" in combined and "fabric" in " ".join(remembered)


def test_proper_name_recommendation_persists():
    lines = ["Jerry Patternly opened the shop"] + \
            ["and then and then again"] * 10 + \
            ["the door creaked"]
    payload = suggestions_for_line(lines, 11)
    singles = [s["term"] for s in payload["singles"]]
    assert "Jerry Patternly" in singles     # very high + floor keeps it alive




# --- added after reviewing real payloads -----------------------------------

def test_comparison_head_demoted_in_singles():
    payload = suggestions_for_line(STORY, 3)
    singles = [s["term"].lower() for s in payload["singles"]]
    assert "texture" not in singles


def test_comparison_head_never_a_pronoun_candidate():
    payload = suggestions_for_line(STORY, 3)
    cands = [r["candidate"].lower() for r in payload["pronouns"]]
    assert "texture" not in cands
    # and nutmeg should now lead the 'it' guesses
    its = [r for r in payload["pronouns"] if r["pronoun"] == "it"]
    assert its and its[0]["candidate"].lower() == "nutmeg"


def test_confirm_unseen_term_is_harmless():
    r = Recommender()
    r.confirm("dragon")            # never observed — must not crash
    assert r.score("dragon", 5) == 0.0

# ===========================================================================
# 6. FULL anaphor coverage — every type of abstract word
# ===========================================================================
from VISUAL_RECOMMENDER import suggestions_for_all_lines
from VISUAL_RECOMMENDER import noun_compounds, WEIGHTS


def test_reflexive_himself_is_person_only():
    r = Recommender()
    r.observe_entry(0, "Jerry Patternly polished the saucepan")
    rows = r.resolve_pronouns(1, "he hurt himself")
    assert prob_of(rows, "himself", "Jerry Patternly") is not None
    assert prob_of(rows, "himself", "saucepan") is None


def test_reflexive_itself_is_thing():
    r = Recommender()
    r.observe_entry(0, "the kettle rattled near Jerry Patternly")
    rows = r.resolve_pronouns(1, "the lid moved by itself")
    assert prob_of(rows, "itself", "kettle") is not None
    assert prob_of(rows, "itself", "Jerry Patternly") is None


def test_reflexive_themselves_prefers_plural():
    r = Recommender()
    r.observe_entry(0, "the horses stood by the barn")
    rows = r.resolve_pronouns(1, "they arranged themselves neatly")
    assert prob_of(rows, "themselves", "horses") is not None


def test_possessive_theirs_and_hers():
    r = Recommender()
    r.observe_entry(0, "Mary Bell met the villagers")
    rows = r.resolve_pronouns(1, "the cart was theirs and the map was hers")
    assert prob_of(rows, "theirs", "villagers") is not None
    assert prob_of(rows, "hers", "Mary Bell") is not None


def test_demonstratives_these_those_plural():
    r = Recommender()
    r.observe_entry(0, "coins spilled beside the lantern")
    rows = r.resolve_pronouns(1, "these rolled away and those stayed")
    assert prob_of(rows, "these", "coins") is not None
    assert prob_of(rows, "those", "coins") is not None
    assert prob_of(rows, "these", "lantern") is None


def test_one_substitutes_a_thing():
    r = Recommender()
    r.observe_entry(0, "he admired the lantern in the shop")
    rows = r.resolve_pronouns(1, "he wanted one")
    assert prob_of(rows, "one", "lantern") is not None


def test_others_prefers_plural():
    r = Recommender()
    r.observe_entry(0, "the coins shone next to the kettle")
    rows = r.resolve_pronouns(1, "the others were dull")
    assert prob_of(rows, "others", "coins") is not None


def test_both_and_neither():
    r = Recommender()
    r.observe_entry(0, "the horses and the dogs waited")
    rows = r.resolve_pronouns(1, "both were tired but neither moved")
    assert prob_of(rows, "both", "horses") or prob_of(rows, "both", "dogs")
    assert prob_of(rows, "neither", "horses") or prob_of(rows, "neither", "dogs")


def test_relative_which_takes_nearest_preceding_noun():
    r = Recommender()
    rows = r.resolve_pronouns(0, "he dropped the jar which cracked loudly")
    assert prob_of(rows, "which", "jar") == 0.85


def test_relative_who_needs_a_person():
    r = Recommender()
    rows = r.resolve_pronouns(
        0, "the saucepan belonged to Mary Bell who laughed")
    assert prob_of(rows, "who", "Mary Bell") is not None
    assert prob_of(rows, "who", "saucepan") is None


def test_that_after_noun_is_relative():
    r = Recommender()
    rows = r.resolve_pronouns(0, "he sold the kettle that whistled")
    assert prob_of(rows, "that", "kettle") == 0.85


def test_that_not_after_noun_is_demonstrative():
    r = Recommender()
    r.observe_entry(0, "the lantern flickered")
    rows = r.resolve_pronouns(1, "he remembered that fondly")
    # 'that' after a verb -> demonstrative -> resolved from memory
    assert prob_of(rows, "that", "lantern") is not None


def test_former_and_latter():
    r = Recommender()
    r.observe_entry(0, "the cat watched the dog")
    rows_f = r.resolve_pronouns(1, "the former hissed")
    rows_l = r.resolve_pronouns(1, "the latter barked")
    assert prob_of(rows_f, "former", "cat") == 0.7
    assert prob_of(rows_l, "latter", "dog") == 0.7


def test_first_and_second_person_never_resolved():
    r = Recommender()
    r.observe_entry(0, "Jerry Patternly opened the shop")
    rows = r.resolve_pronouns(
        1, "I waved and you laughed and we left ourselves a note")
    assert rows == []


def test_non_referential_indefinites_skipped():
    r = Recommender()
    r.observe_entry(0, "the kettle boiled")
    rows = r.resolve_pronouns(1, "something happened and nothing changed")
    assert rows == []


def test_anaphors_never_become_visualisables():
    r = Recommender()
    r.observe_entry(0, "the former and the latter and himself and one")
    for junk in ("former", "latter", "himself", "one"):
        assert r.score(junk, 1) == 0.0


# ===========================================================================
# 7. Robustness — the 99%-of-scenarios battery
# ===========================================================================

def test_contraction_its_resolves():
    r = Recommender()
    r.observe_entry(0, "the saucepan gleamed")
    rows = r.resolve_pronouns(1, "it's shiny")
    assert prob_of(rows, "it", "saucepan") is not None


def test_curly_apostrophe_contraction():
    r = Recommender()
    r.observe_entry(0, "the saucepan gleamed")
    rows = r.resolve_pronouns(1, "it’s shiny")
    assert prob_of(rows, "it", "saucepan") is not None


def test_theyre_resolves():
    r = Recommender()
    r.observe_entry(0, "the coins jingled")
    rows = r.resolve_pronouns(1, "they're heavy")
    assert prob_of(rows, "they", "coins") is not None


def test_possessive_s_counts_as_mention():
    r = Recommender()
    r.observe_entry(0, "Jerry's jar wobbled")
    assert r.score("jerry", 1) > 0          # Jerry's -> mention of Jerry


def test_negative_contractions_produce_no_junk():
    r = Recommender()
    r.observe_entry(0, "he didn't and couldn't and won't")
    junk = [k for k in r.records if k in ("didn", "couldn", "won", "wo")]
    assert junk == []


def test_all_caps_line_spawns_no_fake_names():
    r = Recommender()
    r.observe_entry(0, "HE PUT THE NUTMEG IN A JAR")
    assert not r.records["nutmeg"].is_proper
    assert not r.records["jar"].is_proper
    # ...but the nouns themselves still got recorded
    assert r.score("nutmeg", 1) > 0


def test_midline_sentence_boundary_blocks_fake_proper():
    trips = merge_proper_runs(tokenize("He left. Nutmeg fell."), set())
    # nutmeg is a known common word => capital after '.' is not a name
    props = [s for s, p, _ in trips if p == "PROPER_NOUN"]
    assert "Nutmeg" not in props


def test_statue_of_liberty_merges():
    trips = merge_proper_runs(
        tokenize("we photographed the Statue of Liberty at dawn"), set())
    props = [s for s, p, _ in trips if p == "PROPER_NOUN"]
    assert "Statue of Liberty" in props


def test_empty_and_junk_lines_survive():
    lines = ["", "   ", "!!! ??? ...", "12345 67", None, "😀 😂"]
    payload = suggestions_for_line(lines, 5)
    assert payload["singles"] == [] and payload["pronouns"] == []


def test_empty_list_returns_empty_payload():
    payload = suggestions_for_line([], 0)
    assert payload == {"current": [], "singles": [],
                       "pairs": [], "pronouns": []}


def test_out_of_range_index_clamped():
    payload = suggestions_for_line(["the jar sat there"], 99)
    assert "jar" in [w.lower() for w in payload["current"]]


def test_hyphenated_and_dashed_text():
    payload = suggestions_for_line(
        ["the well-worn saucepan — dented – gleamed"], 0)
    cur = " ".join(w.lower() for w in payload["current"])
    assert "saucepan" in cur


def test_all_lines_helper_matches_per_line_calls():
    lines = STORY + ["the jar sat on the shelf"]
    bulk = suggestions_for_all_lines(lines)
    assert len(bulk) == len(lines)
    for i in range(len(lines)):
        single = suggestions_for_line(lines, i)
        assert bulk[i] == single       # identical semantics, one pass


def test_all_lines_helper_confirmed_terms():
    lines = ["the lantern glowed", "shadows moved"]
    bulk = suggestions_for_all_lines(lines, confirmed_terms={0: "lantern"})
    s = next((x["score"] for x in bulk[1]["singles"]
              if x["term"].lower() == "lantern"), 0)
    bulk_plain = suggestions_for_all_lines(lines)
    s0 = next((x["score"] for x in bulk_plain[1]["singles"]
               if x["term"].lower() == "lantern"), 0)
    assert s > s0


def test_confirmed_term_none_value_tolerated():
    payload = suggestions_for_line(["the jar"], 0, confirmed_terms={0: None})
    assert isinstance(payload, dict)


def test_pronoun_occurrence_counting_across_types():
    r = Recommender()
    r.observe_entry(0, "the saucepan sat by the coins")
    rows = r.resolve_pronouns(1, "it fell and they scattered and it broke")
    it_occs = sorted({x["occurrence"] for x in rows if x["pronoun"] == "it"})
    assert it_occs == [1, 2]
    assert any(x["pronoun"] == "they" for x in rows)

# ===========================================================================
# 8. Wave-3 logic — verb-scope negation, grammar hints, collectives
# ===========================================================================

def test_couldnt_find_flags_object_absent():
    r = Recommender()
    r.observe_entry(0, "he couldn't find the map anywhere")
    assert r.records["map"].all_negated


def test_didnt_drop_object_stays_present():
    r = Recommender()
    r.observe_entry(0, "he didn't drop the jar")
    assert not r.records["jar"].all_negated
    # and "drop" was understood as a verb, not recorded as a noun subject
    assert "drop" not in r.records or \
        r.records["drop"].best_pos() == "VERB"


def test_aux_hint_makes_verbs():
    trips = merge_proper_runs(tokenize("she must hide the letters"), set())
    d = {s.lower(): p for s, p, _ in trips}
    assert d["hide"] == "VERB" and d["letters"] == "NOUN"


def test_collective_noun_is_plural_kind():
    r = Recommender()
    r.observe_entry(0, "the crowd cheered")
    rows = r.resolve_pronouns(1, "they surged")
    assert prob_of(rows, "they", "crowd") is not None


def test_object_case_prefers_non_subject():
    r = Recommender()
    r.observe_entry(0, "Jerry Patternly met Leonard Nimoy")
    rows = r.resolve_pronouns(1, "he greeted him")
    he = max((x for x in rows if x["pronoun"] == "he"),
             key=lambda x: x["prob"])
    him = max((x for x in rows if x["pronoun"] == "him"),
              key=lambda x: x["prob"])
    assert he["candidate"] == "Jerry Patternly"
    assert him["candidate"] == "Leonard Nimoy"


def test_pleonastic_it_weather_and_seems():
    r = Recommender()
    r.observe_entry(0, "the kettle sat there")
    assert r.resolve_pronouns(1, "it was raining") == []
    assert r.resolve_pronouns(1, "it seems that all was well") == []


def test_pleonastic_stays_conservative():
    r = Recommender()
    r.observe_entry(0, "the anvil arrived")
    rows = r.resolve_pronouns(1, "it looked heavy")
    assert prob_of(rows, "it", "anvil") is not None
    rows = r.resolve_pronouns(1, "it was hot")
    assert prob_of(rows, "it", "anvil") is not None


def test_like_is_a_stopword_no_junk_compound():
    r = Recommender()
    r.observe_entry(0, "time flies like an arrow")
    assert "flies like" not in r.records and "like" not in r.records


def test_units_and_directions_are_generic():
    r = Recommender()
    r.observe_entry(0, "he walked five miles north")
    assert r.score("miles", 1) < WEIGHTS["SHOW_THRESHOLD"]
    assert r.score("north", 1) < WEIGHTS["SHOW_THRESHOLD"]


def test_idiom_mentions_flagged():
    r = Recommender()
    r.observe_entry(0, "it was raining cats and dogs")
    assert r.records["cats"].all_idiom and r.records["dogs"].all_idiom


def test_negated_pair_partner_blocked():
    r = Recommender()
    r.observe_entry(0, "there was no flour left")
    pairs = r.pair_suggestions(1, ["bag"])
    assert all("flour" not in p["term"].lower() for p in pairs)


def test_gender_mismatch_demotes():
    r = Recommender()
    r.observe_entry(0, "Mary Bell argued with Jerry Patternly")
    rows = r.resolve_pronouns(1, "she frowned")
    mary = prob_of(rows, "she", "Mary Bell")
    jerry = prob_of(rows, "she", "Jerry Patternly")
    assert mary is not None and (jerry is None or mary > jerry)


def test_common_adjectives_not_nouns():
    trips = merge_proper_runs(tokenize("the old mule stumbled"), set())
    d = {s.lower(): p for s, p, _ in trips}
    assert d["old"] == "ADJECTIVE" and d["mule"] == "NOUN"


def test_compound_greedy_no_overlap():
    trips = merge_proper_runs(
        tokenize("the sewing machine needle case fell"), set())
    comps = [s for s, _, _ in noun_compounds(trips)]
    assert "sewing machine" in comps and "needle case" in comps
    assert "machine needle" not in comps


def test_subject_boost_prefers_the_topic():
    r = Recommender()
    r.observe_entry(0, "the kettle sat on the stove")
    rows = r.resolve_pronouns(1, "it whistled")
    kettle = prob_of(rows, "it", "kettle")
    stove = prob_of(rows, "it", "stove")
    assert kettle is not None and (stove is None or kettle > stove)

# ===========================================================================
# 9. Wave-4 — partitives, cataphora, number words, candidate hygiene
# ===========================================================================

def test_partitive_one_of_the():
    r = Recommender()
    r.observe_entry(0, "three lamps hung in the hall")
    r.observe_entry(1, "one of the lanterns toppled")
    rows = r.resolve_pronouns(1, "one of the lanterns toppled")
    assert prob_of(rows, "one", "lanterns") == 0.85


def test_partitive_skips_generic_nouns():
    r = Recommender()
    rows = r.resolve_pronouns(0, "one of the things fell")
    # 'things' is generic — no confident forward pointer, and no junk row
    assert prob_of(rows, "one", "things") is None


def test_cataphora_person_forward():
    r = Recommender()
    r.observe_entry(0, "before he left, Jerry Patternly locked the door")
    rows = r.resolve_pronouns(
        0, "before he left, Jerry Patternly locked the door")
    assert prob_of(rows, "he", "Jerry Patternly") == 0.6


def test_cataphora_never_offers_things():
    r = Recommender()
    r.observe_entry(0, "before he left, the door slammed")
    rows = r.resolve_pronouns(0, "before he left, the door slammed")
    assert prob_of(rows, "he", "door") is None


def test_number_words_are_stopwords():
    r = Recommender()
    r.observe_entry(0, "three lamps and two benches")
    assert "three" not in r.records and "two" not in r.records


def test_irregular_pasts_are_verbs():
    trips = merge_proper_runs(tokenize("the curtain rose and fell"), set())
    d = {s.lower(): p for s, p, _ in trips}
    assert d["rose"] == "VERB" and d["curtain"] == "NOUN"


def test_compound_parts_suppressed_in_pronoun_panel():
    r = Recommender()
    r.observe_entry(0, "the sewing machine jammed")
    rows = r.resolve_pronouns(1, "it rattled")
    cands = {x["candidate"].lower() for x in rows if x["pronoun"] == "it"}
    assert "sewing machine" in cands
    assert "sewing" not in cands and "machine" not in cands


def test_group_of_needs_plural_or_mass():
    r = Recommender()
    r.observe_entry(0, "the jar glinted")
    pairs = r.pair_suggestions(1, ["pile"])
    assert all("pile of jar" != p["term"].lower() for p in pairs)


def test_animate_group_rule():
    r = Recommender()
    r.observe_entry(0, "the geese honked across the pond")
    pairs = r.pair_suggestions(1, ["flock"])
    assert any(p["term"].lower() == "flock of geese" for p in pairs)


def test_crowd_never_pairs_with_objects():
    r = Recommender()
    r.observe_entry(0, "the nutmeg sat by the jar")
    pairs = r.pair_suggestions(1, ["crowd"])
    assert pairs == []

# ===========================================================================
# 10. Wave-5 — fragments, carry, themes, eras, anchors
# ===========================================================================
from VISUAL_RECOMMENDER import DEMONYMS, THEME_IMMUNE


def test_carry_merges_names_across_fragments():
    r = Recommender()
    r.observe_entry(0, "the Circus")
    r.observe_entry(1, "Maximus. Driven by")
    assert "circus maximus" in r.records
    assert "maximus driven" not in r.records


def test_carry_respects_full_stops():
    r = Recommender()
    r.observe_entry(0, "they visited the Circus.")   # sentence CLOSED
    r.observe_entry(1, "Maximus waved")
    assert "circus maximus" not in r.records


def test_proper_run_never_crosses_midline_stop():
    trips = merge_proper_runs(tokenize("Maximus. Driven by wind"), set())
    props = [s for s, p, _ in trips if p == "PROPER_NOUN"]
    assert "Maximus Driven" not in props


def test_carry_extraction_used_for_current():
    lines = ["the Circus", "Maximus. Driven by"]
    p = suggestions_for_line(lines, 1)
    assert "Circus Maximus" in p["current"]
    assert "Maximus" not in p["current"]


def test_comma_blocks_compounds_and_adjacency():
    r = Recommender()
    r.observe_entry(0, "she packed hats, boots")
    assert "hats boots" not in r.records
    assert ("hats", "boots") not in r.adjacent_pairs


def test_era_markers_never_visualisable():
    r = Recommender()
    r.observe_entry(0, "In 64 AD, the city burned")
    assert "ad" not in r.records
    assert r.eras and r.eras[0][1] == "64 AD"


def test_theme_detected_and_adjectivised():
    r = Recommender()
    r.observe_entry(0, "Rome was not built in a day")
    r.observe_entry(1, "the streets of Rome were narrow")
    themes = r.current_themes(1)
    assert themes and themes[0][0] == "Roman"


def test_theme_resists_decay():
    r = Recommender()
    r.observe_entry(0, "Rome was glorious")
    r.observe_entry(1, "Rome ruled the seas")
    for i in range(2, 20):
        r.observe_entry(i, "and then and then again")
    assert r.score("rome", 19) >= WEIGHTS["SHOW_THRESHOLD"]


def test_theme_combo_and_era_suffix():
    r = Recommender()
    r.observe_entry(0, "Rome burned in 64 AD")
    r.observe_entry(1, "Rome never forgot")
    r.observe_entry(2, "the temples smouldered")
    terms = [x["term"].lower() for x in r.pair_suggestions(2, ["temples"])]
    assert "roman temples" in terms
    assert "roman temples 64 ad" in terms


def test_theme_immune_words_stay_standalone():
    r = Recommender()
    r.observe_entry(0, "Rome sweltered")
    r.observe_entry(1, "Rome baked in the heat")
    terms = [x["term"].lower() for x in r.pair_suggestions(2, ["wind"])]
    assert "roman wind" not in terms
    assert "wind" not in DEMONYMS and "wind" in THEME_IMMUNE


def test_modern_years_not_appended():
    r = Recommender()
    r.observe_entry(0, "back in 2019 Rome hosted a fair")
    r.observe_entry(1, "Rome glittered")
    assert r.current_era(1) is None      # 2019 is too modern to search by


def test_city_anchor_to_theme_place():
    r = Recommender()
    r.observe_entry(0, "Rome fell silent")
    r.observe_entry(1, "Rome waited")
    pairs = r.pair_suggestions(2, ["city"])
    assert any(x["term"] == "Rome" for x in pairs)


def test_title_anchor_is_grammatical():
    r = Recommender()
    r.observe_entry(0, "a very unpopular emperor ruled")
    r.observe_entry(1, "which brings us to Nero")
    terms = [x["term"] for x in r.pair_suggestions(1, ["Nero"])]
    assert "Emperor Nero" in terms
    assert "Nero Emperor" not in terms


def test_title_anchor_rejects_monuments():
    r = Recommender()
    r.observe_entry(0, "the Circus Maximus stood empty")
    r.observe_entry(1, "the emperor arrived")
    terms = [x["term"] for x in r.pair_suggestions(1, ["emperor"])]
    assert "Emperor Circus Maximus" not in terms


def test_singles_dedupe_compound_parts():
    r = Recommender()
    r.observe_entry(0, "the Circus")
    r.observe_entry(1, "Maximus. Driven by")
    singles = [x["term"] for x in r.top_singles(2)]
    assert "Circus Maximus" in singles
    assert "Circus" not in singles and "Maximus" not in singles

# ===========================================================================
# 11. Knowledge base — big maintained resources, not hardcoded lists
# ===========================================================================
from VISUAL_RECOMMENDER import (kb_gender, kb_is_person, kb_is_natural,
                                kb_is_unit, kb_is_place, kb_demonym,
                                kb_concreteness, kb_is_collective,
                                adj_noun_chunks)


def test_kb_gender_is_international():
    # gender-guesser: 48k names — Greek, Slavic, Asian, Arabic ...
    assert kb_gender("Ioannis") == "male"
    assert kb_gender("Thalia") == "female"
    assert kb_gender("Dimitrios") == "male"
    assert kb_gender("Priya") == "female"
    assert kb_gender("saucepan") is None       # not a name => stays open


def test_kb_person_via_wordnet():
    for w in ("prophet", "centurion", "oracle", "apostle", "scribe"):
        assert kb_is_person(w), w
    for w in ("machine", "temple", "jar", "market"):
        assert not kb_is_person(w), w          # first-sense precision


def test_kb_natural_phenomena():
    for w in ("drizzle", "aurora", "gale", "hail"):
        assert kb_is_natural(w), w
    assert not kb_is_natural("temple")


def test_kb_units_first_sense_only():
    assert kb_is_unit("furlong") and kb_is_unit("hectare")
    assert not kb_is_unit("foot")              # body part first


def test_kb_places_and_demonyms_from_dataset():
    assert kb_is_place("japan") and kb_demonym("japan") == "Japanese"
    assert kb_is_place("greece") and kb_demonym("greece") == "Greek"
    assert kb_is_place("kyoto") and kb_demonym("kyoto") is None
    # ^ no demonym => the engine uses "Kyoto" itself as the modifier


def test_kb_concreteness_ratings():
    assert kb_concreteness("jar") >= 4.5
    assert kb_concreteness("melancholy") <= 2.5
    assert kb_concreteness("zzzznotaword") is None


def test_kb_collective_congregation():
    assert kb_is_collective("congregation")


def test_greek_prophets_end_to_end():
    p = suggestions_for_line(
        ["Delphi sat high in the mountains of Greece",
         "pilgrims climbed to Greece's sacred slopes",
         "the prophets spoke in riddles"], 2)
    assert any(x["term"].lower() == "greek prophets" for x in p["pairs"])


def test_adjective_chunk_keeps_distinctive():
    trips = [("small", "ADJECTIVE", 1), ("religious", "ADJECTIVE", 2),
             ("sect", "NOUN", 3)]
    toks = tokenize("a small religious sect")
    chunks = [s for s, _, _ in adj_noun_chunks(trips, toks)]
    assert chunks == ["religious sect"]        # 'small' dropped


def test_adjective_chunk_respects_commas():
    toks = tokenize("red, boots")
    trips = [("red", "ADJECTIVE", 0), ("boots", "NOUN", 1)]
    assert adj_noun_chunks(trips, toks) == []


def test_complementizer_that_skipped():
    r = Recommender()
    r.observe_entry(0, "the bridge creaked")
    rows = r.resolve_pronouns(1, "he said that all was well")
    assert prob_of(rows, "that", "bridge") is None


def test_engine_survives_missing_data_file(monkeypatch=None):
    # the seeds keep the engine alive with no data and no libraries
    import VISUAL_RECOMMENDER as V
    saved = V._CONC, V._DATA_DEMONYMS, V._DATA_PLACES
    try:
        V._CONC, V._DATA_DEMONYMS, V._DATA_PLACES = {}, {}, set()
        V.kb_concreteness.cache_clear(); V.kb_demonym.cache_clear()
        V.kb_is_place.cache_clear()
        p = suggestions_for_line(["he gathered nutmeg",
                                  "he put it in a jar"], 1)
        assert any(x["term"].lower() == "jar of nutmeg"
                   for x in p["pairs"])
    finally:
        V._CONC, V._DATA_DEMONYMS, V._DATA_PLACES = saved
        V.kb_concreteness.cache_clear(); V.kb_demonym.cache_clear()
        V.kb_is_place.cache_clear()

# ===========================================================================
# plain-python runner (no pytest needed)
# ===========================================================================
if __name__ == "__main__":
    import sys
    import traceback

    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok    {name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}")
            traceback.print_exc(limit=3)
    print(f"\n{len(fns) - failed}/{len(fns)} tests passed")
    sys.exit(1 if failed else 0)
