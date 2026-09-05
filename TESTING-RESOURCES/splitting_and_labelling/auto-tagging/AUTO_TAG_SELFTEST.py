"""
AUTO_TAG_SELFTEST.py — the worked examples behind
`Auto_add_mediatypes.py --selftest`.

Kept out of Auto_add_mediatypes.py so that file stays a readable flowchart.
Every example here is a real line from a real script; each assert is the
behaviour that keeps it right.

    uv run ___splitting_and_labelling/2-auto-tagging/Auto_add_mediatypes.py --selftest
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]
                        / "___splitting_and_labelling" / "shared"))
    import PATHS  # noqa: F401  — every stage folder on sys.path

import shared_text_logic as stl
from auto_tag_engine import (apply_flowchart, check_flowchart, coverage_report,
                             detect_attributes, evidence_for, search_term_for)
from Auto_add_mediatypes import Attr, collect_attributes, decide


def _row(**kw) -> dict:
    """An untagged row, as the splitter hands it over."""
    return dict({"media_type": "", "modifiers": [], "search_term": "",
                 "rule_ids": []}, **kw)


def _detect(data: dict):
    """STEP 1 over a little script. Returns (attrs, lines)."""
    return detect_attributes(data, collect_attributes)


def _tag(data: dict):
    """STEP 1 + STEP 2 over a little script. Returns (attrs, lines)."""
    attrs, lines = _detect(data)
    apply_flowchart(data, attrs, lines, decide)
    return attrs, lines


def run_selftest() -> int:
    check_flowchart(Attr, decide)

    LIST = Attr.CONTAINS_NOUN_LIST
    NAME = Attr.CONTAINS_FAMOUS_NAME
    PLACE = Attr.CONTAINS_PLACE_NAME

    # ── a list split over THREE lines becomes ONE group, a cell per line ──
    multi = {
        "If you were a European merchant, getting it meant": _row(),
        "sailing for months, surviving": _row(),
        "scurvy,": _row(),
        "pirates and": _row(),
        "shipwrecks. But if you made it back with a": _row(),
    }
    attrs, lines = _detect(multi)
    assert LIST in attrs["scurvy,"], "line 1 of the split list"
    assert LIST in attrs["pirates and"], "line 2 of the split list"
    assert LIST in attrs["shipwrecks. But if you made it back with a"], \
        "line 3 of the split list"
    assert LIST not in attrs[
        "If you were a European merchant, getting it meant"], \
        "same sentence, but it does not overlap the list"
    assert LIST not in attrs["sailing for months, surviving"]
    # each line offers its OWN item, because every group cell needs one
    assert search_term_for(lines["scurvy,"], LIST) == "scurvy"
    assert search_term_for(lines["pirates and"], LIST) == "pirates"
    apply_flowchart(multi, attrs, lines, decide)
    opener = multi["scurvy,"]
    assert opener["media_type"] == "stock", "a real base OPENS the group"
    assert opener["modifiers"] == ["group"]
    assert opener["search_term"] == "scurvy"
    cell2 = multi["pirates and"]
    assert cell2["media_type"] == "hold_previous", "hold + group CONTINUES it"
    assert cell2["modifiers"] == ["group"]
    assert cell2["search_term"] == "pirates"
    # group_id + position come out derived, exactly as the tagging tool does it
    assert opener["group_id"] == cell2["group_id"] is not None
    assert [opener["position"], cell2["position"]] == ["1", "2"]

    # ── a whole list on ONE line wants several pictures in that one scene ─
    one_liner = {"nutmeg, cloves and cinnamon ruled the world.": _row()}
    attrs, lines = _tag(one_liner)
    row = one_liner["nutmeg, cloves and cinnamon ruled the world."]
    assert row["media_type"] == "stock", row
    assert row["modifiers"] == ["collage"], row

    # ── a line holding only the over-grabbed LEAD of a list stays False ───
    junk = {"two dollars": _row(),
            "nutmeg, cloves and cinnamon ruled the world.": _row()}
    attrs, _ = _detect(junk)
    assert LIST not in attrs["two dollars"], "over-grabbed lead"
    assert LIST in attrs["nutmeg, cloves and cinnamon ruled the world."]

    # ── the boss's famous-name examples (the name engine's truth table) ───
    fam = {
        "And that brings us to John Paul II of Spain - a big leader": _row(),
        "And the leader of the barbarians was Alaric the Goth of "
        "north macedonia": _row(),
        "Jenson Button wasn't the only person there who lived": _row(),
        "Ronaldo and Bradd Pitt fought hard against the Ferrari "
        "Escaplito": _row(),
        "The Banda Islands. A tiny, incredibly remote volcanic "
        "archipelago": _row(search_term="Banda Islands Indonesia"),
        "So the European merchants sailed on": _row(),
    }
    attrs, lines = _detect(fam)
    keys = list(fam)
    # these two sit MID-sentence, so their capitals are proof enough on their
    # own — they pass with or without the spaCy model
    assert "John Paul II of Spain" in evidence_for(lines[keys[0]], NAME)
    assert evidence_for(lines[keys[1]], NAME) == "Alaric the Goth"
    # 'Jenson Button' STARTS its sentence, where a capital proves nothing
    # ('New taxes' vs 'New York'). It may only pass when spaCy's NER vouches
    # for it — with no model the house rule says stay False.
    if stl.get_nlp() is not None:
        assert evidence_for(lines[keys[2]], NAME) == "Jenson Button"
    else:
        assert NAME not in attrs[keys[2]], \
            "no model → a sentence-initial name must NOT be guessed at"
    both = evidence_for(lines[keys[3]], NAME)
    assert "Ronaldo" in both and "Bradd Pitt" in both \
        and "Ferrari Escaplito" in both
    assert NAME not in attrs[keys[4]], "geo-suffix names belong to the map"
    assert NAME not in attrs["So the European merchants sailed on"], \
        "a lone nationality adjective is not a name"
    assert PLACE in attrs[keys[4]]
    banda = evidence_for(lines[keys[4]], PLACE)
    assert "Banda Islands" in banda and "Indonesia" in banda, banda

    # ── sentence reconstruction across lines (the MASTER ender detection) ─
    sentences = {"Nutmeg only grew in one place on Earth:": _row(),
                 "The Banda Islands. A tiny, incredibly remote volcanic "
                 "archipelago": _row(),
                 "in modern-day Indonesia.": _row()}
    line = stl.LineContext(list(sentences)[1], list(sentences.values())[1], 1,
                           list(sentences), shared={})
    whole = line.full_sentence()
    # ':' is NOT a sentence ender, so the lead-in stays attached; the '.' in
    # the MIDDLE of line 2 is one, so the sentence runs on to "Indonesia."
    assert "The Banda Islands" in whole, whole
    assert whole.rstrip().endswith("Indonesia."), whole

    # ── STATISTICS AND DATA GET THE RIGHT CHART ──────────────────────────
    stats = {
        "It was a golden age.": _row(media_type="stock"),
        "In 1946, everything changed.": _row(),
        "the fine was £4,000 per sailor.": _row(),
        "Around 73% of the ocean is still unexplored.": _row(),
        "Rome fielded 900 ships, Athens just 300.": _row(),
    }
    attrs, lines = _tag(stats)
    # a year the sentence opens on travels back on the timeline
    year = stats["In 1946, everything changed."]
    assert year["media_type"] == "timeline", year
    assert year["data"] == {"year": "1946"}, year
    # one big figure ticks up on a counter, currency into the prefix
    fine = stats["the fine was £4,000 per sailor."]
    assert fine["media_type"] == "counter", fine
    assert fine["data"]["value"] == "4000" and fine["data"]["prefix"] == "£"
    # one quantity out of a whole fills a progress bar
    ocean = stats["Around 73% of the ocean is still unexplored."]
    assert ocean["media_type"] == "progress_bar", ocean
    assert ocean["data"]["percent"] == "73"
    # several quantities side by side become bars
    ships = stats["Rome fielded 900 ships, Athens just 300."]
    assert ships["media_type"] == "bar_chart", ships
    assert "900" in ships["data"]["bars"] and "300" in ships["data"]["bars"]

    # ── a chart stays up while the sentence carries on ───────────────────
    carry_on = {"Rome fielded 900 ships, Athens just 300,": _row(),
                "which was barely a fleet at all.": _row()}
    attrs, lines = _tag(carry_on)
    assert carry_on["Rome fielded 900 ships, Athens just 300,"][
        "media_type"] == "bar_chart"
    assert carry_on["which was barely a fleet at all."][
        "media_type"] == "hold_previous", "don't start a second chart mid-thought"

    # ── A PRONOUN THAT LEANS ON SOMEBODY WE ALREADY NAMED ────────────────
    if stl.get_nlp() is not None:
        # the line above is where we introduced them → hold that picture
        just_introduced = {"The emperor Nero watched Rome burn.": _row(),
                           "He said nothing at all.": _row()}
        attrs, lines = _tag(just_introduced)
        him = just_introduced["He said nothing at all."]
        assert Attr.REFERS_BACK_TO_A_NAME in attrs["He said nothing at all."]
        assert him["media_type"] == "hold_previous", him
        assert "decorate" in him["modifiers"], him

        # named further up, so build ONE scene holding several pictures
        further_up = {"The emperor Nero watched Rome burn.": _row(),
                      "The years passed quietly.": _row(),
                      "He said nothing at all.": _row()}
        attrs, lines = _tag(further_up)
        him = further_up["He said nothing at all."]
        assert him["media_type"] == "stock", him
        assert him["modifiers"] == ["collage"], him
        assert him["search_term"].startswith("Nero"), him

    # ── part of a sentence → edit previous; a new sentence → something new ─
    # note the first line has NO full stop, so the second is still part of it
    flow = {"A merchant loaded the crates": _row(),
            "which meant that": _row()}
    attrs, lines = _tag(flow)
    assert Attr.STARTS_A_NEW_SENTENCE in attrs["A merchant loaded the crates"]
    assert Attr.STARTS_A_NEW_SENTENCE not in attrs["which meant that"], \
        "no sentence ender above it, so it is still mid-sentence"
    assert flow["which meant that"]["media_type"] == "hold_previous", \
        "part of a sentence already going → carry the image on"
    if stl.get_nlp() is not None:
        assert flow["A merchant loaded the crates"]["media_type"] == "stock", \
            "a new sentence naming something filmable → new footage"

    # ── dates: capture JUST the date part, in several formats ────────────
    dates = {"But in the 1600s, this little wrinkled seed was the": _row(),
             "back in the 17th century": _row()}
    attrs, lines = _detect(dates)
    assert evidence_for(lines[list(dates)[0]], Attr.CONTAINS_DATE) == "1600s"
    assert "17th century" in evidence_for(lines["back in the 17th century"],
                                          Attr.CONTAINS_DATE)

    # ── the optional boosters (only asserted when installed) ─────────────
    if stl._dateparser_search is not None:
        x = {"It happened on 03/07/1667 at dawn": _row()}
        attrs, _ = _detect(x)
        assert Attr.CONTAINS_DATE in attrs[next(iter(x))], "dateparser booster"
    if stl.number_parser is not None:
        x = {"seventeen million people watched": _row()}
        attrs, lines = _detect(x)
        key = next(iter(x))
        assert Attr.CONTAINS_BIG_NUMBER in attrs[key], "number-parser booster"
        assert "17,000,000" in evidence_for(lines[key],
                                            Attr.CONTAINS_BIG_NUMBER)
    if stl.geonamescache is not None:
        x = {"the port of Lisbon grew rich": _row()}
        attrs, lines = _detect(x)
        key = next(iter(x))
        assert PLACE in attrs[key], "geonamescache booster"
        assert "Portugal" in search_term_for(lines[key], PLACE)

    # ── the full demo script, through both steps ─────────────────────────
    demo = {
        # FIRST line: needs_a_previous_line must leave the holds alone
        "So what happened next?": _row(),
        # the two real-world FALSE POSITIVES from review — must stay False:
        "If you were a European merchant, getting it meant": _row(),
        "sailing for months, surviving": _row(),
        "It costs about": _row(search_term="coin"),
        "two dollars": _row(search_term="two dollars"),
        # real signals:
        "ribs,": _row(rule_ids=[15]),              # the splitter says: list run
        "in modern-day Indonesia.": _row(),
        'they called it "worth its weight in gold".': _row(),
        "beneath the old wooden floor": _row(),
        "already tagged, and stays that way.": _row(media_type="stock"),
    }
    attrs, lines = _detect(demo)
    assert LIST not in attrs["If you were a European merchant, getting it "
                             "meant"], "a clause list is not a noun list"
    assert LIST not in attrs["sailing for months, surviving"]
    assert LIST in attrs["ribs,"], "splitter rule_id 15 must win on its own"
    assert PLACE in attrs["in modern-day Indonesia."]
    assert evidence_for(lines["in modern-day Indonesia."], PLACE) == "Indonesia"
    quoted = 'they called it "worth its weight in gold".'
    assert Attr.CONTAINS_QUOTE in attrs[quoted]
    assert "worth its weight" in evidence_for(lines[quoted],
                                              Attr.CONTAINS_QUOTE)
    assert Attr.CONTAINS_BIG_NUMBER not in attrs["two dollars"], "no digits"
    assert Attr.OPENS_RELATIVE_TO_SOMETHING in attrs[
        "beneath the old wooden floor"]
    assert Attr.IS_QUESTION_TO_VIEWER in attrs["So what happened next?"]
    if stl.get_nlp() is None:      # the False-when-in-doubt contract
        assert not any(NAME in attrs[f] for f in demo), \
            "without the model, no name may pass"

    changed = apply_flowchart(demo, attrs, lines, decide)
    assert demo["already tagged, and stays that way."]["media_type"] == "stock"
    indonesia = demo["in modern-day Indonesia."]
    assert indonesia["media_type"] == "map"
    assert indonesia["search_term"] == "Indonesia"
    quote_row = demo[quoted]
    assert quote_row["media_type"] == "hold_previous"
    assert quote_row["modifiers"] == ["caption"]
    assert demo["beneath the old wooden floor"]["media_type"] == "hold_previous"
    # an auto-fill never overwrites a search_term that is already there
    assert demo["two dollars"]["search_term"] == "two dollars"
    # the FIRST line never gets a hold/caption — nothing sits before it
    assert demo["So what happened next?"]["media_type"] == "", \
        "line 1 has nothing to hold, so it is left for you"
    assert not any(v.get("media_type") == "typography" for v in demo.values())

    stl.booster_report("[auto-tag]")
    print(coverage_report(Attr, decide))
    print(f"\nselftest OK — {len(changed)} demo lines assigned. Lists become "
          f"GROUPS (a cell per line, each with its own term); statistics pick "
          f"their own chart (timeline / counter / progress bar / bar chart); "
          f"a chart stays up mid-sentence; pronouns find who they mean; "
          f"mid-sentence lines edit the previous and new sentences start "
          f"something new; existing tags untouched; NLP "
          f"{'ON' if stl.get_nlp() else 'OFF (conservative)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_selftest())
