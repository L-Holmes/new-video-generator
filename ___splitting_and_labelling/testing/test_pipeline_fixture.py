"""
End-to-end test of the SPLIT_AND_LABEL decision ladder.

TEST-DATA POLICY (after the _BUILTIN_FAMOUS incident):
    Production code carries ZERO world-knowledge answer keys.  This test
    INJECTS an explicit fame cache below — that is legitimate because it
    exercises the cache->lock MECHANISM with data the test itself declares.
    What is never allowed again: an assertion that only passes because the
    production code secretly contains the expected answer.

Injects a hand-built stage-1 cache (lines + rule ids + meta) so stages 2-3
run for real without needing the spaCy model.
"""
import json, shutil, sys, types
from pathlib import Path

spacy_stub = types.ModuleType("spacy"); tok = types.ModuleType("spacy.tokens")
class _T: ...
tok.Doc = tok.Span = tok.Token = _T
spacy_stub.tokens = tok; spacy_stub.language = types.ModuleType("spacy.language")
sys.modules["spacy"] = spacy_stub; sys.modules["spacy.tokens"] = tok

import SPLIT_AND_LABEL as sal
import sentence_splitter as ss
from SPLIT_AND_LABEL_CONFIG import (PREVIOUS_FAMILY, AI_TEMPLATES,
                                    GRID_TEMPLATES, to_legacy)

fails = []
def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond: fails.append(label)

def M(**kw):
    base = {"opener": True, "ents": [], "keywords": [], "has_visualisable": True,
            "has_number": False, "has_money": False, "in_quote": False,
            "n_tokens": 4, "span": [0, 4], "list": None}
    base.update(kw)
    return base

FIXTURE = [
    # 1. COLD OPEN: first line of the video, nothing picture-able
    #    -> typography_blank (legacy read_out), term = the line itself
    ["If you open", [7], M(has_visualisable=False)],
    # 2. quote BEFORE any visual: lock wants caption_previous, tier-0 gate
    #    blocks it (typography doesn't count as a visual) -> tier 3
    ['"stop right there"', [5], M(in_quote=True, keywords=["stop"])],
    # 3. obscure place (via INJECTED fame cache) -> tier-1 map lock
    ["the Banda Islands", [18], M(opener=False,
        ents=[{"text": "Banda Islands", "label": "GPE"}],
        keywords=["banda", "islands"])],
    # 4. famous place (via INJECTED fame cache) -> stock lock
    ["across the Sahara", [46], M(opener=False,
        ents=[{"text": "Sahara", "label": "LOC"}], keywords=["sahara"])],
    # 5. money -> object_generate lock
    ["it costs about two dollars", [19], M(has_money=True, has_number=True,
        ents=[{"text": "about two dollars", "label": "MONEY"}],
        keywords=["two", "dollars"])],
    # 6-9. FOUR-item list run (rule of N) -> grid, positions from list meta
    ["ribs,", [15], M(keywords=["ribs"], list={"group": 0, "index": 0, "size": 4})],
    ["vertebrae,", [15], M(opener=False, keywords=["vertebrae"],
                           list={"group": 0, "index": 1, "size": 4})],
    ["teeth,", [15], M(opener=False, keywords=["teeth"],
                       list={"group": 0, "index": 2, "size": 4})],
    ["entire skulls", [1], M(opener=False, keywords=["entire", "skulls"],
                             list={"group": 0, "index": 3, "size": 4})],
    # 9. nothing picture-able mid-video -> hold_previous
    ["but that was", [1008], M(opener=False, has_visualisable=False)],
    # 10. SFX beat -> caption over previous
    ["boom", [60], M(opener=False, keywords=["boom"])],
    # 11. obscure person (INJECTED cache) -> wikipedia lock
    ["Alaric the Goth", [50], M(
        ents=[{"text": "Alaric the Goth", "label": "PERSON"}],
        keywords=["alaric", "goth"])],
    # 12. entity-less plain line -> tier 3; wikipedia/map/grids must not
    #     even be on its menu
    ["the dog jumped down", [1], M(keywords=["dog", "jumped"])],
]

def run_fixture():
    work = Path("fixture_run"); shutil.rmtree(work, ignore_errors=True)
    work.mkdir(); import os; os.chdir(work)

    # INJECTED fame data — tests the cache->lock mechanism (see policy above)
    Path("ENTITY_FAME_CACHE.json").write_text(json.dumps(
        {"banda islands": "obscure", "alaric the goth": "obscure",
         "sahara": "famous"}))
    sal.ENTITY_FAME_CACHE_PATH = Path("ENTITY_FAME_CACHE.json").resolve()

    cache = sal.split_cache_path("fixture")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(FIXTURE))

    out = sal.generate_script_to_search_term("script-fixture.txt")
    rows = json.loads(Path(out).read_text())
    return rows

print("===== AI OFF (default) =====")
rows = run_fixture()
r = rows

cold = r["If you open"]
check(cold["template"] == "new__typography"
      and cold["search_type"] == "read_out",
      "cold open -> new__typography (legacy read_out)")

q = r['"stop right there"']
check(q["tier"].startswith("tier3"), "pre-visual quote gated -> tier 3")
check(any("falling through" in w for w in q["why"]),
      "gate fall-through recorded in the why-trail")

check(r["the Banda Islands"]["template"] == "new__map",
      "obscure place (injected cache) -> map lock")
check(r["across the Sahara"]["template"] == "new__stock",
      "famous place (injected cache) -> stock lock")
check(r["it costs about two dollars"]["template"] == "new__object",
      "money -> object_generate lock")
check([r[t]["template"] for t in ("ribs,", "vertebrae,", "teeth,", "entire skulls")]
      == ["editgroup__stock"] * 4, "list run -> grid for all four items")
check([r[t]["position"] for t in ("ribs,", "vertebrae,", "teeth,", "entire skulls")]
      == ["1", "2", "3", "1"],
      "positions cycle the legacy 3-cell renderer (see TODO switchover)")
check(all(r[t]["shot"]["layout"]["n"] == 4
          for t in ("ribs,", "vertebrae,", "teeth,", "entire skulls")),
      "RULE OF N: emitted shot carries the real cell count (4)")
check(r["but that was"]["template"] == "editprev__hold",
      "mid-video unvisualisable -> hold_previous")
check(r["boom"]["template"] == "editprev__caption"
      and r["boom"]["search_type"] == "decorate_previous",
      "SFX -> editprev__caption (legacy decorate_previous with AI off)")
check(r["Alaric the Goth"]["template"] == "new__wikipedia",
      "obscure person (injected cache) -> wikipedia lock")

check(r["the dog jumped down"]["tier"].startswith("tier3"),
      "plain line -> tier 3 sampling")
_st = sal.ScriptState(has_visual=True, prev_material="stock")
_m = M(keywords=["dog", "jumped"])
sheet, _ = sal.build_scoresheet("the dog jumped down", _m, [1],
                                sal.allowed_templates(_st, _m), _st)
check(not set(sheet) & GRID_TEMPLATES,
      "grids are LOCK-ONLY: absent from every tier-3 score-sheet")
check("new__wikipedia" not in sheet and "new__map" not in sheet,
      "no entity -> wikipedia/map not even on the menu")
check(not set(sheet) & AI_TEMPLATES,
      "AI off -> AI templates absent from the menu")
check(all(row["search_term"] == "" for row in rows.values()),
      "search_term is EMPTY on every row (filled by ADD_SEARCH_TEXT.py)")
check(all("rule_ids" in row for row in rows.values())
      and r["boom"]["rule_ids"] == [60],
      "every row carries the splitter's rule_ids")
ai_legacy = {"stickman", "ai_edit", "stickman_joint_3_row",
             "stickman_explain_stock", "stickman_explain_wikipedia",
             "stickman_text_overlay"}
check(all(row["search_type"] not in ai_legacy for row in rows.values()),
      "AI off -> no AI legacy strings in the output")

print("\n===== AI ON (unit checks on gates/locks/bridge) =====")
sal.AI_ENABLED = True
st_fresh = sal.ScriptState()
allowed = sal.allowed_templates(st_fresh, M())
check("new__ai_stock" in allowed and "editprev__ai_edit" not in allowed,
      "AI on, no frames yet: stickman allowed, ai_edit not (no AI image)")
st_ai = sal.ScriptState(has_visual=True, prev_material="ai_stock")
check("editprev__ai_edit" in sal.allowed_templates(st_ai, M()),
      "previous frame AI -> ai_edit_previous on the menu")
st_stock = sal.ScriptState(has_visual=True, prev_material="stock")
check("editprev__ai_edit" not in sal.allowed_templates(st_stock, M()),
      "previous frame stock -> ai_edit_previous off the menu")
li_meta = M(list={"group": 0, "index": 0, "size": 3})
tpl, _ = sal.lock_list_grid("x", li_meta, [15],
                               sal.allowed_templates(st_ai, li_meta), st_ai)
check(tpl == "editgroup__ai", "AI on -> list lock picks the stickman grid")
check(to_legacy("editprev__caption", True) == "stickman_text_overlay"
      and to_legacy("editprev__caption", False) == "decorate_previous",
      "caption_previous legacy string switches with the AI flag")
check(to_legacy("editgroup__ai", True) == "stickman_joint_3_row"
      and to_legacy("editprev__ai_edit", True) == "ai_edit",
      "AI templates map to their legacy strings")
sal.AI_ENABLED = False

print("\n===== whitespace normaliser (A1) =====")
raw = "sailing for months, surviving\n   scurvy,\n   pirates,\nand shipwrecks"
norm = ss.rule_normalise_whitespace(raw)
check("\n" not in norm and "  " not in norm and
      norm == "sailing for months, surviving scurvy, pirates, and shipwrecks",
      "hard-wrapped + indented text collapses to single spaces")
para = "the islands were remote\n\nNutmeg grew there"
check(ss.rule_normalise_whitespace(para)
      == "the islands were remote. Nutmeg grew there",
      "unpunctuated paragraph break becomes a sentence break")
para2 = "It was over.\n\nNutmeg grew there"
check(ss.rule_normalise_whitespace(para2) == "It was over. Nutmeg grew there",
      "already-punctuated paragraph break gains no extra period")
check(all("\n" not in row_text for row_text in rows),
      "no output line contains a newline")

# =====================================================================
# GROUP C — subject tracking, layering nudge, search-term contracts
# (appended checks run after the main suite; they re-use `check`)
# =====================================================================
print("\n===== C1: splitter meta (head noun / anaphora / topic) =====")
class FT:
    def __init__(self, i, text, pos, lemma=None, ent="", dep="", sent_start=False):
        self.i, self.text, self.pos_, self.dep_ = i, text, pos, dep
        self.lemma_ = lemma or text.lower(); self.lower_ = text.lower()
        self.ent_type_, self.like_num = ent, False
        self.is_punct = pos == "PUNCT"; self.is_space = False
        self.is_sent_start = sent_start
class FE:
    def __init__(self, s, e, text, label):
        self.start, self.end, self.text, self.label_ = s, e, text, label
class FD:
    def __init__(self, toks, ents): self.toks, self.ents = toks, ents
    def __len__(self): return len(self.toks)
    def __getitem__(self, k): return self.toks[k]

# "this little wrinkled seed ... nutmeg nutmeg" (topic voting)
doc = FD([FT(0,"this","DET",dep="det",sent_start=True),
          FT(1,"little","ADJ"), FT(2,"wrinkled","ADJ"),
          FT(3,"seed","NOUN",dep="nsubj"),
          FT(4,"nutmeg","NOUN"), FT(5,"nutmeg","NOUN")], [])
metas = ss._build_chunks_meta(doc, [ss.Chunk("this little wrinkled seed", []),
                                    ss.Chunk("nutmeg nutmeg", [])],
                              [(0, 4), (4, 6)])
check(metas[0]["head_noun"] == "seed" and metas[0]["demonstrative"] is True,
      "demonstrative NP detected with head noun 'seed'")
check(metas[0]["script_topic"] == "nutmeg" == metas[1]["script_topic"],
      "script topic voted across the whole doc ('nutmeg')")
check(metas[0]["nouns"] == ["seed"], "nouns list carries noun lemmas only")

doc2 = FD([FT(0,"It","PRON",dep="nsubj",sent_start=True),
           FT(1,"was","AUX",dep="ROOT")], [])
m2 = ss._build_chunks_meta(doc2, [ss.Chunk("It was", [])], [(0, 2)])
check(m2[0]["pronoun_subject"] is True, "bare-pronoun subject flagged")

def TM(**kw):
    base = {"opener": False, "ents": [], "keywords": [], "nouns": [],
            "head_noun": "", "demonstrative": False, "pronoun_subject": False,
            "script_topic": "nutmeg", "has_visualisable": True,
            "has_number": False, "has_money": False, "in_quote": False}
    base.update(kw); return base

print("\n===== C1/C2: engine state + layering nudge =====")
st = sal.ScriptState()
sal._advance_state_for("new__stock", st,
                       meta=TM(head_noun="nutmeg",
                               ents=[{"text": "Banda Islands", "label": "GPE"}]))
check(st.subjects[0] == "banda islands" and "nutmeg" in st.subjects,
      "subjects registry: most recent first, head noun + entities")
check(st.has_visual and st.prev_material == "stock",
      "fresh stock advances has_visual + prev_material")

st2 = sal.ScriptState(has_visual=True, prev_material="stock",
                      subjects=["nutmeg"])
allowed2 = sal.allowed_templates(st2, TM(head_noun="nutmeg"))
sheet_same, reasons_same = sal.build_scoresheet(
    "x", TM(head_noun="nutmeg"), [1], allowed2, st2)
st3 = sal.ScriptState(has_visual=True, prev_material="stock", subjects=[])
sheet_diff, _ = sal.build_scoresheet("x", TM(head_noun="canyon"), [1],
                                     sal.allowed_templates(st3, TM()), st3)
check(sheet_same["editprev__add_stock"] > sheet_diff["editprev__add_stock"]
      and sheet_same["new__stock"] < sheet_diff["new__stock"],
      "same-subject continuation boosts layering, damps fresh stock")
check(any("layer onto" in r for r in reasons_same),
      "layering nudge recorded in reasons")

print("\n===== v18.4: strict lists, pacing priors, map floor =====")
docL = FD([FT(0,"seed","NOUN",dep="nsubj",sent_start=True),
           FT(1,"was","AUX"), FT(2,"resource","NOUN"), FT(3,"planet","NOUN")], [])
mL = ss._build_chunks_meta(docL,
        [ss.Chunk("this little wrinkled seed was", [15]),
         ss.Chunk("the single most contested resource on", [39]),
         ss.Chunk("the planet.", [1])],
        [(0, 2), (2, 3), (3, 4)])
check(all(m["list"] is None for m in mL),
      "single stray list boundary (no comma) never forms a grid group")

docL2 = FD([FT(0,"scurvy","NOUN"), FT(1,",","PUNCT"),
            FT(2,"pirates","NOUN"), FT(3,",","PUNCT"),
            FT(4,"shipwrecks","NOUN")], [])
mL2 = ss._build_chunks_meta(docL2,
        [ss.Chunk("scurvy,", [15]), ss.Chunk("pirates,", [15]),
         ss.Chunk("shipwrecks", [1])],
        [(0, 2), (2, 4), (4, 5)])
check([m["list"]["index"] if m["list"] else None for m in mL2] == [0, 1, 2],
      "real comma list of nouns still forms a 3-cell group")

st_c = sal.ScriptState(has_visual=True, prev_material="stock")
sheet_c, reasons_c = sal.build_scoresheet(
    "x", M(opener=False), [1], sal.allowed_templates(st_c, M(opener=False)), st_c)
check(sheet_c["editprev__hold"] > sheet_c["new__stock"],
      "continuation: hold beats fresh stock even with rule-1 stock weights")
check(any("mostly small edits" in r for r in reasons_c),
      "continuation damp recorded in reasons")

meta_indo = M(opener=False, ents=[{"text": "Indonesia", "label": "GPE"}],
              keywords=["indonesia"])
sheet_i, _ = sal.build_scoresheet(
    "modern-day Indonesia.", meta_indo, [1],
    sal.allowed_templates(st_c, meta_indo), st_c)
check(sheet_i["new__map"] >= 0.5 and
      sheet_i["new__map"] > sheet_i["editprev__hold"],
      "unknown-fame place mid-sentence: map floored above hold")

st_p = sal.ScriptState(has_visual=True, prev_material="stock",
                       subjects=["nutmeg", "seed"])
meta_planet = M(opener=False, keywords=["planet"])
meta_planet["nouns"] = ["planet"]; meta_planet["head_noun"] = "planet"
sheet_p, reasons_p = sal.build_scoresheet(
    "the planet.", meta_planet, [1],
    sal.allowed_templates(st_p, meta_planet), st_p)
check(any("add it onto the previous" in r for r in reasons_p)
      and sheet_p["editprev__add_stock"] >= 0.30,
      "new concrete noun mid-sentence boosts add-onto-previous")

print("\n===== v18.5: rule 61 (verb->list) + idioms =====")
# strong verb 'devoured' introducing a comma list -> split after the verb
docV = FD([FT(0,"blaze","NOUN",sent_start=True),
           FT(1,"devoured","VERB",lemma="devour"),
           FT(2,"temples","NOUN",lemma="temple"), FT(3,",","PUNCT"),
           FT(4,"villas","NOUN",lemma="villa"), FT(5,",","PUNCT"),
           FT(6,"districts","NOUN",lemma="district")], [])
check(ss.rule_verb_list_reveal(docV) == {2},
      "rule 61: split right after strong verb 'devoured' (POS, no whitelist)")
docW = FD([FT(0,"list","NOUN",sent_start=True),
           FT(1,"included","VERB",lemma="include"),
           FT(2,"temples","NOUN",lemma="temple"), FT(3,",","PUNCT"),
           FT(4,"villas","NOUN",lemma="villa")], [])
check(ss.rule_verb_list_reveal(docW) == set(),
      "rule 61: weak verb ('included') never fires")

# idioms: detected, kept non-visual, tagged 1010
docI = FD([FT(0,"and","CCONJ",sent_start=True), FT(1,"the","DET"),
           FT(2,"rest","NOUN"), FT(3,"is","AUX"),
           FT(4,"history","NOUN")], [])
check(ss._idiom_spans(docI) == [(1, 5)],
      "idiom span detected ('the rest is history')")
chI = [ss.Chunk("and the rest is history", [1])]
mI = ss._build_chunks_meta(docI, chI, [(0, 5)])
check(mI[0]["keywords"] == [] and mI[0]["nouns"] == []
      and mI[0]["has_visualisable"] is False and 1010 in chI[0].ids,
      "idiom chunk: no content words, unvisualisable, tagged 1010")

print("\n===== ADD_SEARCH_TEXT.py (review tool smoke test) =====")
import ADD_SEARCH_TEXT as ast_tool
ast_tool.HERE = Path(".").resolve()
ast_tool.MASTER_RULES_PATH = Path("MASTER_RULES.md").resolve()
ast_tool.PROMPTS_DIR = Path("prompts").resolve()
Path("prompts").mkdir(exist_ok=True)
for f in ("BASE_RULES.md", "01_initial_generation.txt", "02_revision_pass.txt"):
    Path("prompts", f).write_text(f"stub {f} {{{{BASE_RULES}}}} "
                                  "{{MASTER_RULES}} {{PROJECT_RULES}} "
                                  "{{FLAGGED}} {{JSON}}")
sess = ast_tool.Session(Path("TESTING_fixture-script_to_search_term.json"))
check(all(s == "empty" for s in sess.status.values()),
      "review tool: fresh file -> every entry status 'empty'")
sess.export_prompt()
p1 = sess.state_dir / "PROMPT-pass1.txt"
check(p1.exists() and "{{JSON}}" not in p1.read_text()
      and '"If you open"' in p1.read_text(),
      "prompt export fills every placeholder incl. the JSON")
# teach a rule for two adjacent + one distant line -> adjacency notation
sess.selection = [0, 1, 3]
import builtins
answers = iter(["silhouette with question mark", "person not revealed yet"])
builtins.input, _orig = (lambda *_: next(answers)), builtins.input
sess.teach()
builtins.input = _orig
rules_txt = sess.project_rules_path.read_text()
check("RULE #1001)" in rules_txt and "<1 more entry>" in rules_txt
      and "<0 more entries>" in rules_txt,
      "taught rule saved with computed adjacency notation")
check(all(sess.status[sess.lines[i]] == "flagged" for i in (0, 1, 3)),
      "taught entries marked flagged")
# import an AI reply that fills two terms
reply = json.loads(Path("TESTING_fixture-script_to_search_term.json").read_text())
reply["boom"]["search_term"] = "BOOM"
reply["the Banda Islands"]["search_term"] = "Banda Islands"
Path("ai_reply.json").write_text(json.dumps(reply))
sess.import_reply("ai_reply.json")
data2 = json.loads(Path("TESTING_fixture-script_to_search_term.json").read_text())
check(data2["boom"]["search_term"] == "BOOM"
      and sess.status["boom"] == "filled",
      "import updates terms + statuses, backup written")
sess.finish()
master = Path("MASTER_RULES.md").read_text()
check("RULE #1001)" in master and not sess.project_rules_path.exists(),
      "finish merges project rules into MASTER_RULES.md")

print("\n===== MANUAL_TAGGING.py (catalog, suggestions, HTTP round trip) =====")
import urllib.request
import MANUAL_TAGGING as mt
mt.HERE = Path(".").resolve()
cat = mt.build_catalog()
check(len(cat) == len(sal.SHOT_TEMPLATES)
      and all(c["strategy"] in ("new", "edit_previous") for c in cat),
      "catalog: one button per TemplateDef with strategy grouping")
ai_btn = next(c for c in cat if c["template"] == "new__ai_stock")
stock_btn = next(c for c in cat if c["template"] == "new__stock")
board_btn = next(c for c in cat if c["template"] == "new__stock_on_board")
check(ai_btn["color"] == "#c0392b" and board_btn["color"] == "#e74c3c"
      and stock_btn["color"] != ai_btn["color"],
      "AI family buttons are red shades; materials get distinct colours")
check(ai_btn["legacy_ai"] == "stickman"
      and next(c for c in cat if c["template"] == "editprev__caption")["legacy_ai"]
      == "stickman_text_overlay",
      "catalog carries both legacy strings per button")

server = mt.make_server(Path("TESTING_fixture-script_to_search_term.json"))
port = server.server_address[1]
th = __import__("threading").Thread(target=server.serve_forever, daemon=True)
th.start()
payload = json.loads(urllib.request.urlopen(
    f"http://127.0.0.1:{port}/data").read())
check(len(payload["lines"]) == len(rows)
      and all("suggest" in L and "row" in L for L in payload["lines"]),
      "GET /data serves every line with row + suggestion chips")
check("Banda Islands" in payload["lines"][2]["suggest"]["places"],
      "place entity extracted as a quick chip")
req = urllib.request.Request(
    f"http://127.0.0.1:{port}/save", method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"line": "boom",
                     "patch": {"search_term": "KABOOM",
                               "template": "new__typography",
                               "search_type": "read_out",
                               "shot": stock_btn["shot"]}}).encode())
resp = json.loads(urllib.request.urlopen(req).read())
saved = json.loads(Path("TESTING_fixture-script_to_search_term.json").read_text())
check(resp["ok"] and saved["boom"]["search_term"] == "KABOOM"
      and saved["boom"]["search_type"] == "read_out"
      and saved["boom"]["manual"] is True
      and any("manual" in w for w in saved["boom"]["why"]),
      "POST /save patches the JSON, marks manual, appends why note")
check(Path("TESTING_fixture-script_to_search_term.json.bak").exists(),
      "a .bak is written before the first manual save")
bad = urllib.request.Request(
    f"http://127.0.0.1:{port}/save", method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"line": "NOT A LINE", "patch": {}}).encode())
try:
    urllib.request.urlopen(bad)
    check(False, "unknown line rejected with 400")
except urllib.error.HTTPError as e:
    check(e.code == 400, "unknown line rejected with 400")
server.shutdown()

print()
print("FINAL RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
sys.exit(1 if fails else 0)
