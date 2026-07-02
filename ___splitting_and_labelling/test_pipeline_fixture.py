"""
End-to-end test of the SPLIT_AND_LABEL decision ladder.

Injects a hand-built stage-1 cache (lines + rule ids + meta) so stages 2-3
run for real without needing the spaCy model.  Covers: tier-0 gating,
every tier-1 lock, tier-2 nudges, tier-3 sampling, grid positions, search
terms, the legacy bridge, and the review sheet.
"""
import json, shutil, sys, types
from pathlib import Path

# ---- stub spacy so sentence_splitter imports (the splitter never runs) -----
spacy_stub = types.ModuleType("spacy"); tok = types.ModuleType("spacy.tokens")
class _T: ...
tok.Doc = tok.Span = tok.Token = _T
spacy_stub.tokens = tok; spacy_stub.language = types.ModuleType("spacy.language")
sys.modules["spacy"] = spacy_stub; sys.modules["spacy.tokens"] = tok

import SPLIT_AND_LABEL as sal
from SPLIT_AND_LABEL_CONFIG import PREVIOUS_FAMILY, TEMPLATE_TO_LEGACY

def M(**kw):
    base = {"opener": True, "ents": [], "keywords": [], "has_visualisable": True,
            "has_number": False, "has_money": False, "in_quote": False,
            "n_tokens": 4, "span": [0, 4], "list": None}
    base.update(kw)
    return base

FIXTURE = [
    # 1. quote on the FIRST line: lock wants decorate_previous, tier-0 gate
    #    blocks it (nothing on screen) -> falls through to tier 3
    ['"stop right there"', [5], M(in_quote=True, keywords=["stop"])],
    # 2. obscure place (via fame cache) -> tier-1 map lock
    ["the Banda Islands", [18], M(opener=False,
        ents=[{"text": "Banda Islands", "label": "GPE"}],
        keywords=["banda", "islands"])],
    # 3. famous place (builtin list) -> tier-1 stock lock, term = the place
    ["across the Sahara", [46], M(opener=False,
        ents=[{"text": "Sahara", "label": "LOC"}], keywords=["sahara"])],
    # 4. money -> tier-1 object_generate lock
    ["it costs about two dollars", [19], M(has_money=True, has_number=True,
        ents=[{"text": "about two dollars", "label": "MONEY"}],
        keywords=["costs", "two", "dollars"])],
    # 5-7. list run -> grid, positions 1/2/3
    ["ribs,", [15], M(keywords=["ribs"], list={"group": 0, "index": 0, "size": 3})],
    ["vertebrae,", [15], M(opener=False, keywords=["vertebrae"],
                           list={"group": 0, "index": 1, "size": 3})],
    ["entire skulls", [1], M(opener=False, keywords=["entire", "skulls"],
                             list={"group": 0, "index": 2, "size": 3})],
    # 8. nothing picture-able -> hold_previous
    ["but that was", [1008], M(opener=False, has_visualisable=False,
                               keywords=[])],
    # 9. SFX beat (rule 60) -> decorate_previous, uppercase term
    ["boom", [60], M(opener=False, keywords=["boom"])],
    # 10. obscure person (fame cache) -> wikipedia lock
    ["Alaric the Goth", [50], M(
        ents=[{"text": "Alaric the Goth", "label": "PERSON"}],
        keywords=["alaric", "goth"])],
    # 11. plain line, no signals -> tier 3 sampling
    ["the dog jumped down", [1], M(keywords=["dog", "jumped"])],
]

def main():
    work = Path("fixture_run"); shutil.rmtree(work, ignore_errors=True)
    work.mkdir(); import os; os.chdir(work)

    # fame cache: teaches the engine who's obscure
    Path("ENTITY_FAME_CACHE.json").write_text(json.dumps(
        {"banda islands": "obscure", "alaric the goth": "obscure"}))
    sal.ENTITY_FAME_CACHE_PATH = Path("ENTITY_FAME_CACHE.json").resolve()

    # inject the stage-1 cache; stage 1 will cache-hit and never call spaCy
    cache = sal.split_cache_path("fixture")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(FIXTURE))

    out = sal.generate_script_to_search_term("script-fixture.txt")
    rows = json.loads(Path(out).read_text())
    r = {t: rows[t] for t in rows}

    fails = []
    def check(cond, label):
        print(("  PASS  " if cond else "  FAIL  ") + label)
        if not cond: fails.append(label)

    q = r['"stop right there"']
    check(q["tier"].startswith("tier3"), "first-line quote gated -> tier 3")
    check(q["search_type"] not in
          {TEMPLATE_TO_LEGACY[t] for t in PREVIOUS_FAMILY},
          "gated line never resolves to a previous-family type")
    check(any("falling through" in w for w in q["why"]),
          "gate fall-through recorded in the why-trail")

    check(r["the Banda Islands"]["template"] == "map"
          and r["the Banda Islands"]["search_term"] == "Banda Islands",
          "obscure place -> map lock, term = place name")
    check(r["across the Sahara"]["template"] == "stock"
          and r["across the Sahara"]["search_term"] == "sahara",
          "famous place -> stock lock, term = place name")
    check(r["it costs about two dollars"]["template"] == "object_generate",
          "money -> object_generate lock")
    check([r[t]["template"] for t in ("ribs,", "vertebrae,", "entire skulls")]
          == ["grid_different"] * 3, "list run -> grid for all three items")
    check([r[t]["position"] for t in ("ribs,", "vertebrae,", "entire skulls")]
          == ["1", "2", "3"], "grid positions advance 1/2/3")
    check(r["ribs,"]["search_type"] == "joint_3_row",
          "grid_different maps to legacy joint_3_row")
    check(r["but that was"]["template"] == "hold_previous"
          and r["but that was"]["search_type"] == "static_of_previous"
          and r["but that was"]["search_term"],
          "unvisualisable -> hold_previous (legacy static_of_previous)")
    check(r["boom"]["template"] == "decorate_previous"
          and r["boom"]["search_term"] == "BOOM",
          "SFX beat -> decorate_previous with uppercase term")
    check(r["Alaric the Goth"]["template"] == "wikipedia"
          and r["Alaric the Goth"]["search_term"] == "Alaric the Goth",
          "obscure person -> wikipedia lock, term = name")
    check(r["the dog jumped down"]["tier"].startswith("tier3"),
          "plain line -> tier 3 sampling")
    check(all("why" in row and "tier" in row and "shot" in row
              for row in rows.values()),
          "every output row carries why/tier/shot")
    check(sal.review_sheet_path("fixture").exists(), "review sheet written")

    print()
    print("----- REVIEW SHEET PREVIEW -----")
    for line in sal.review_sheet_path("fixture").read_text().splitlines()[:6]:
        print("  " + line[:150])
    print()
    print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
