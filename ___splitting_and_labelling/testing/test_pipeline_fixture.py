"""
Tests for the simplified pipeline: splitter meta, MEDIA_TYPES, the empty
emit from SPLIT_AND_LABEL, and MANUAL_TAGGING's server operations
(save / groups / split / join / undo). Runs without spaCy (stub) and
without a browser (direct HTTP against the tool's server).

Lives in testing/ — it adds the parent dir to sys.path itself.
"""
import json, shutil, sys, types, threading, urllib.request, urllib.error
from pathlib import Path

_here = Path(__file__).resolve().parent
for p in (_here, _here.parent):
    sys.path.insert(0, str(p))

spacy_stub = types.ModuleType("spacy"); tok = types.ModuleType("spacy.tokens")
class _T: ...
tok.Doc = tok.Span = tok.Token = _T
spacy_stub.tokens = tok; spacy_stub.language = types.ModuleType("spacy.language")
sys.modules["spacy"] = spacy_stub; sys.modules["spacy.tokens"] = tok

import sentence_splitter as ss
import SPLIT_AND_LABEL as sal
import types as _t
_cge = _t.ModuleType("___visuals.COLOUR_GRADE_ETC"); _cge.DEFAULT_PRESET = "film"
import sys as _s; _s.modules.setdefault("___visuals.COLOUR_GRADE_ETC", _cge)
import MEDIA_TYPES as mtypes
import MANUAL_TAGGING as mt

fails = []
def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond: fails.append(label)

# ---------------------------------------------------------------- fakes
class FT:
    def __init__(self, i, text, pos, lemma=None, ent="", dep="", sent_start=False):
        self.i, self.text, self.pos_, self.dep_ = i, text, pos, dep
        self.lemma_ = lemma or text.lower(); self.lower_ = text.lower()
        self.ent_type_, self.like_num = ent, False
        self.is_punct = pos == "PUNCT"; self.is_space = False
        self.is_sent_start = sent_start
class FD:
    def __init__(self, toks, ents): self.toks, self.ents = toks, ents
    def __len__(self): return len(self.toks)
    def __getitem__(self, k): return self.toks[k]

print("===== sentence splitter =====")
raw = "sailing for months, surviving\n   scurvy,\n   pirates,\nand shipwrecks"
check(ss.rule_normalise_whitespace(raw)
      == "sailing for months, surviving scurvy, pirates, and shipwrecks",
      "whitespace normaliser collapses hard wraps + indentation")
check(ss.rule_normalise_whitespace("it was over\n\nNutmeg grew")
      == "it was over. Nutmeg grew",
      "unpunctuated paragraph break becomes a sentence break")

docV = FD([FT(0,"blaze","NOUN",sent_start=True),
           FT(1,"devoured","VERB",lemma="devour"),
           FT(2,"temples","NOUN",lemma="temple"), FT(3,",","PUNCT"),
           FT(4,"villas","NOUN",lemma="villa")], [])
check(ss.rule_verb_list_reveal(docV) == {2},
      "rule 61: strong verb before a comma list splits after the verb")
docW = FD([FT(0,"list","NOUN",sent_start=True),
           FT(1,"included","VERB",lemma="include"),
           FT(2,"temples","NOUN"), FT(3,",","PUNCT"), FT(4,"villas","NOUN")], [])
check(ss.rule_verb_list_reveal(docW) == set(), "rule 61: weak verb never fires")

docI = FD([FT(0,"and","CCONJ",sent_start=True), FT(1,"the","DET"),
           FT(2,"rest","NOUN"), FT(3,"is","AUX"), FT(4,"history","NOUN")], [])
check(ss._idiom_spans(docI) == [(1, 5)], "idiom span detected")
chI = [ss.Chunk("and the rest is history", [1])]
mI = ss._build_chunks_meta(docI, chI, [(0, 5)])
check(mI[0]["keywords"] == [] and mI[0]["has_visualisable"] is False
      and 1010 in chI[0].ids,
      "idiom chunk: non-visual, tagged 1010")

docL = FD([FT(0,"seed","NOUN",dep="nsubj",sent_start=True),
           FT(1,"was","AUX"), FT(2,"resource","NOUN"), FT(3,"planet","NOUN")], [])
mL = ss._build_chunks_meta(docL,
        [ss.Chunk("this little wrinkled seed was", [15]),
         ss.Chunk("the single most contested resource on", [39]),
         ss.Chunk("the planet.", [1])], [(0, 2), (2, 3), (3, 4)])
check(all(m["list"] is None for m in mL),
      "single stray list boundary never forms a group")
docL2 = FD([FT(0,"scurvy","NOUN"), FT(1,",","PUNCT"), FT(2,"pirates","NOUN"),
            FT(3,",","PUNCT"), FT(4,"shipwrecks","NOUN")], [])
mL2 = ss._build_chunks_meta(docL2,
        [ss.Chunk("scurvy,", [15]), ss.Chunk("pirates,", [15]),
         ss.Chunk("shipwrecks", [1])], [(0, 2), (2, 4), (4, 5)])
check([m["list"]["index"] if m["list"] else None for m in mL2] == [0, 1, 2],
      "real comma noun list still groups")

print("\n===== MEDIA_TYPES (shared catalog, no legacy layer) =====")
check(all({"tags", "info", "example", "color"} <= set(d)
          for d in mtypes.MEDIA_TYPES.values()),
      "every media type has tags/info/example/color")
check(not any("legacy" in d for d in mtypes.MEDIA_TYPES.values())
      and not hasattr(mtypes, "to_legacy"),
      "no legacy strings and no to_legacy anywhere")
check(set(mtypes.MODIFIERS) == {"decorate", "caption", "group", "collage"},
      "modifiers: decorate (editor) / caption (automatic) / group / collage")
check(mtypes.GROUPABLE_TYPES == {"stock", "ai_stock"}
      and mtypes.COLLAGEABLE_TYPES == {"stock"},
      "group: stock+ai_stock only; collage: stock only")

print("\n===== SPLIT_AND_LABEL emit =====")
work = _here / "fixture_run"; shutil.rmtree(work, ignore_errors=True)
work.mkdir(); import os; os.chdir(work)
FIXTURE = [
    ["If you open", [7], {}],
    ["a jar of nutmeg.", [1], {}],
    ["ribs,", [15], {}],
    ["vertebrae,", [15], {}],
    ["entire skulls", [1], {}],
    ["boom", [60], {}],
]
cache = sal.split_cache_path("fixture")
cache.parent.mkdir(parents=True); cache.write_text(json.dumps(FIXTURE))
out = sal.generate_script_to_search_term("script-fixture.txt")
rows = json.loads(Path(out).read_text())
check(len(rows) == 6 and all(
      r["search_term"] == "" and r["media_type"] == ""
      and "search_type" not in r
      and r["modifiers"] == [] and r["group_id"] is None
      for r in rows.values()),
      "emit: user columns empty, NO search_type column at all")
check(rows["boom"]["rule_ids"] == [60]
      and all({"position", "sfx", "sfx_timing", "music", "music_trim_seconds",
               "music_fade_out"} <= set(r) for r in rows.values()),
      "emit: rule_ids kept, legacy audio columns present")

print("\n===== MANUAL_TAGGING server =====")
mt.HERE = Path(".").resolve()
server = mt.make_server(Path(out))
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"
def call(path, body=None):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        return json.loads(urllib.request.urlopen(req).read()), 200
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}"), e.code
def fresh():
    return json.loads(Path(out).read_text())

payload, _ = call("/data")
check(len(payload["lines"]) == 6
      and {"bases", "modifiers"} <= set(payload["catalog"]),
      "GET /data serves lines + tag-grouped catalog")

call("/save", {"line": "ribs,", "patch": {"media_type": "stock"}})
call("/save", {"line": "ribs,", "patch": {"modifiers": ["group"]}})
call("/save", {"line": "vertebrae,", "patch": {"media_type": "hold_previous"}})
call("/save", {"line": "vertebrae,", "patch": {"modifiers": ["group"]}})
d = fresh()
check(d["ribs,"]["group_id"] == d["vertebrae,"]["group_id"] == 1
      and [d["ribs,"]["position"], d["vertebrae,"]["position"]] == ["1", "2"]
      and "search_type" not in d["ribs,"],
      "stock + group opens, hold_previous + group continues: shared group_id, "
      "positions 1..n, no derived column")
# the same two rows are what the renderer reads back
import CONFIG as cfg
_resolved = fresh()
cfg.resolve_group_continuations(_resolved)
check(_resolved["vertebrae,"]["media_type"] == "stock"
      and _resolved["vertebrae,"]["group_continuation"] is True,
      "resolve_group_continuations gives the cell its group's base")
# a continuation with nothing above it to continue is refused, not written
r, code = call("/save", {"line": "If you open",
                         "patch": {"media_type": "hold_previous",
                                   "modifiers": ["group"]}})
check(code == 400 and "no group is open above" in (r.get("error") or ""),
      "hold_previous + group with no group above it is refused")
# ...and a real base always OPENS a new group rather than joining the one above
call("/save", {"line": "entire skulls", "patch": {"media_type": "stock",
                                                  "modifiers": ["group"]}})
d = fresh()
check(d["entire skulls"]["group_id"] == 2
      and d["entire skulls"]["position"] == "1",
      "a groupable base always opens a NEW group, never joins the one above")
call("/save", {"line": "entire skulls", "patch": {"media_type": "",
                                                  "modifiers": []}})
# a group written the OLD way (the same base repeated) is re-spelled on open,
# so opening an existing file does not shatter a group the user already built
legacy = {"one": {"media_type": "stock", "modifiers": ["group"], "group_id": 1},
          "two": {"media_type": "stock", "modifiers": ["group"], "group_id": 1},
          "three": {"media_type": "stock", "modifiers": ["group"], "group_id": 1},
          "four": {"media_type": "stock", "modifiers": [], "group_id": None}}
moved = mt.migrate_legacy_groups(legacy)
mt.recompute(legacy)
check(moved == 2
      and [legacy[k]["media_type"] for k in ("one", "two", "three")]
          == ["stock", "hold_previous", "hold_previous"]
      and {legacy[k]["group_id"] for k in ("one", "two", "three")} == {1}
      and [legacy[k]["position"] for k in ("one", "two", "three")]
          == ["1", "2", "3"],
      "a legacy same-base group is re-spelled to opener + continuation cells")
cfg.resolve_group_continuations(legacy)
check([legacy[k]["media_type"] for k in ("one", "two", "three")]
      == ["stock", "stock", "stock"],
      "...and the renderer reads the migrated group back identically")

call("/save", {"line": "boom", "patch": {"media_type": "hold_previous",
                                         "modifiers": ["decorate"]}})
check(fresh()["boom"]["modifiers"] == ["decorate"],
      "hold + decorate saves cleanly (the freeze-and-draw default)")
r, code = call("/save", {"line": "boom", "patch": {"media_type": "wikipedia",
                                                   "modifiers": ["group"]}})
check(code == 400 and "grouped" in (r.get("error") or ""),
      "group on a non-groupable base is rejected")
r, code = call("/save", {"line": "boom", "patch": {"media_type": "map",
                                                   "modifiers": ["collage"]}})
check(code == 400 and "collaged" in (r.get("error") or ""),
      "collage on a non-collageable base is rejected")
call("/save", {"line": "a jar of nutmeg.", "patch": {"media_type": "stock",
                                                     "modifiers": ["group"]}})
call("/save", {"line": "a jar of nutmeg.",
               "patch": {"modifiers": ["group", "collage"]}})
d2 = fresh()["a jar of nutmeg."]
check(d2["modifiers"] == ["collage"] and d2["group_id"] is None,
      "toggling collage onto a grouped line swaps group out (last wins)")
call("/save", {"line": "boom", "patch": {"media_type": "hold_previous",
                                         "modifiers": ["decorate"]}})

r, code = call("/split", {"line": "If you open", "index": 2})
d = fresh()
check(code == 200 and "If" in d and "you open" in d
      and list(d)[0] == "If" and list(d)[1] == "you open"
      and d["If"]["rule_ids"] == d["you open"]["rule_ids"] == [7],
      "split: two entries in place, order kept, both inherit everything")
r, code = call("/split", {"line": "boom", "index": 0})
check(code == 400, "split at the very start is rejected")

r, code = call("/join", {"line": "you open"})
d = fresh()
check(code == 200 and "If you open" in d and "you open" not in d,
      "join to above: merged back into one entry")
r, code = call("/join", {"line": list(fresh())[0]})
check(code == 400, "the first line cannot join to above")

# split mid-word then rejoin: NO phantom space (uses split provenance)
call("/split", {"line": "entire skulls", "index": 3})       # "ent" | "ire skulls"
d = fresh()
check("ent" in d and "ire skulls" in d, "mid-word split produced both halves")
call("/join", {"line": "ire skulls"})
d = fresh()
check("entire skulls" in d and "ent ire skulls" not in d,
      "rejoining our own mid-word split restores the word with NO space")
# split at a real space then rejoin: keeps the single space
call("/split", {"line": "entire skulls", "index": 6})       # "entire" | "skulls"
call("/join", {"line": "skulls"})
check("entire skulls" in fresh(),
      "rejoining a split made at a space keeps exactly one space")
check(Path(str(out) + ".bak").exists(), "a .bak exists after the first edit")
server.shutdown()

print("\n===== Auto_add_mediatypes: checks + no-overwrite flowchart =====")
import Auto_add_mediatypes as AUTO
try:
    AUTO.run_selftest()
    check(True, "auto-tagger selftest: 3 checks fire right; existing tags "
                "never overwritten")
except AssertionError as exc:
    import traceback; traceback.print_exc()
    check(False, f"auto-tagger selftest failed: {exc}")
from auto_tag_engine import check_flowchart
try:
    # every branch the flowchart can reach must name a real catalog type and
    # real modifiers, and every attribute must point at a real detector
    check_flowchart(AUTO.Attr, AUTO.decide)
    check(True, "every flowchart branch targets a REAL catalog type")
except AssertionError as exc:
    check(False, f"flowchart targets something unknown: {exc}")

print()
print("FINAL RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
sys.exit(1 if fails else 0)
