"""
Integration tests for the pure tag-based model (NO legacy layer).
Stubs COLOUR_GRADE_ETC so CONFIG imports in a sandbox.
Lives in testing/ in the repo — it adds the repo root to sys.path itself.
"""
import sys, types, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent):
    sys.path.insert(0, str(p))
work = HERE / "testrun"; shutil.rmtree(work, ignore_errors=True)
work.mkdir(); import os; os.chdir(work)  # CONFIG creates dirs — keep them here

cge = types.ModuleType("___visuals.COLOUR_GRADE_ETC"); cge.DEFAULT_PRESET = "film"
sys.modules["___visuals.COLOUR_GRADE_ETC"] = cge
# stubs for repo-only modules (real in the repo; the sandbox lacks tkinter):
msp = types.ModuleType("___visuals.MANUAL_STOCK_PLACEMENT")
for _n in ("composite_overlays", "crop_and_zoom", "place_overlays_interactive",
           "zoom_prev_interactive", "extract_frame"):
    setattr(msp, _n, lambda *a, **k: None)
sys.modules["___visuals.MANUAL_STOCK_PLACEMENT"] = msp
cio = types.ModuleType("___visuals.CACHE_IO")
cio._resolve_to_local_path = lambda p: p
sys.modules["___visuals.CACHE_IO"] = cio
tmm = types.ModuleType("___visuals.TIMING_MERGE")
tmm._load_scene_timings = lambda: {}
sys.modules["___visuals.TIMING_MERGE"] = tmm
sr = types.ModuleType("___visuals.STATIC_RENDER")
sr._render_image_to_static_mp4 = lambda img, dur, out: Path(out).write_text("mp4")
sys.modules["___visuals.STATIC_RENDER"] = sr

fails = []
def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond: fails.append(label)

from CONFIG import (
    GROUPABLE_TYPES, JOINT_LAYOUT_POSITIONS, JOINT_TYPE_SFX_MAP,
    MEDIA_PROPERTIES, MEDIA_TYPE_CATALOG, MODIFIERS, MediaType,
    MEDIA_TYPE_TABS, TERM_OPTIONAL_TYPES, Tag,
    chart_min_playable_seconds, chart_transition_seconds,
    coerce_scene_data, data_fields_for, format_series,
    group_scene_rows, media_props, normalise_scene_row, parse_series,
    resolve_group_continuations, scene_data, scene_is_group_continuation,
    scene_is_grouped, scene_type, scene_wants_decorate,
    timeline_min_playable_seconds, timeline_transition_seconds,
)
import importlib as _il

print("===== the enum IS the catalog =====")
check({t.value for t in MediaType} == set(MEDIA_TYPE_CATALOG),
      "MediaType members are exactly the catalog names")
dead = {"joint_3_row", "stickman_joint_3_row", "decorate_previous",
        "stickman_text_overlay", "zoom_prev_img", "static_of_previous",
        "read_out", "stickman", "ai_edit", "object_generate",
        "object", "add_stock_to_previous", "manual_stock_add_to_previous"}
# The point of this check is that the DEAD names are gone — object and
# add_stock_to_previous are decorate-editor tabs now, not media types. The
# live count is not asserted: it is just len(MEDIA_TYPE_CATALOG), which the
# check above already pins to the enum, and hardcoding it here only means a
# stale number every time a type is added.
check(not (dead & {t.value for t in MediaType}),
      "object + add_stock_to_previous are decorate-editor tabs now")
check("blank" in MEDIA_TYPE_CATALOG and "random_background" in MEDIA_TYPE_CATALOG,
      "blank + random_background are bookable media types")
check(set(MEDIA_PROPERTIES) == set(MediaType),
      "property table covers the enum exactly")
check(set(MODIFIERS) == {"decorate", "caption", "group", "collage"},
      "modifiers: decorate (editor), caption (automatic), group, collage")
check({MediaType(n) for n in GROUPABLE_TYPES} <= set(JOINT_LAYOUT_POSITIONS)
      and {MediaType(n) for n in GROUPABLE_TYPES} <= set(JOINT_TYPE_SFX_MAP),
      "groupable bases have joint layouts + sfx entries")

print("\n===== loader =====")
row = {"search_term": "x", "media_type": "stock", "modifiers": ["group"],
       "group_id": 1, "position": "1"}
normalise_scene_row("a", row)
check(row["media_type"] is MediaType.STOCK and scene_is_grouped(row),
      "grouped stock row normalises; grouping read from the modifier")
row2 = {"search_term": "x", "media_type": "hold_previous",
        "modifiers": ["decorate"]}
normalise_scene_row("b", row2)
check(row2["media_type"] is MediaType.HOLD_PREVIOUS
      and scene_wants_decorate(row2) and row2["group_id"] is None,
      "hold_previous + decorate (the freeze-and-edit default) normalises")
stale = {"search_term": "x", "media_type": "map",
         "search_type": "map", "tier": "t", "why": []}
normalise_scene_row("c", stale)
check("search_type" not in stale and "tier" not in stale,
      "stale legacy columns are stripped on load")
err = ""
try: normalise_scene_row("d", {"search_term": "x"})
except ValueError as e: err = str(e)
check("UPGRADE_OLD_JSON" in err,
      "old flat rows are refused with a pointer to the upgrade script")
err = ""
try: normalise_scene_row("e", {"media_type": "wikipedia", "modifiers": ["group"]})
except ValueError as e: err = str(e)
check("group" in err and "wikipedia" in err,
      "group on a non-groupable base is refused")
row3 = {"search_term": "x", "media_type": "hold_previous",
        "modifiers": ["group"]}
normalise_scene_row("e2", row3)
check(row3["media_type"] is MediaType.HOLD_PREVIOUS and scene_is_grouped(row3),
      "hold_previous + group (a group's continuation cell) normalises")
err = ""
try: normalise_scene_row("f", {"media_type": "hologram"})
except ValueError as e: err = str(e)
check("hologram" in err and "stock" in err,
      "unknown media_type refused with the valid names")

print("\n===== grouping =====")
def g(mt, gid): return {"media_type": MediaType(mt), "group_id": gid,
                        "modifiers": ["group"], "position": "1"}
scenes = [("a", g("stock", 1)), ("b", g("stock", 1)),
          ("c", g("stock", 2)), ("d", g("ai_stock", 2))]
groups = group_scene_rows(scenes)
check([[t for t, _ in grp] for grp in groups] == [["a", "b"], ["c"], ["d"]],
      "same gid groups; gid change or base change splits")

# how the TAGGER writes a group now: an opener + `hold_previous` cells that
# continue it. resolve_group_continuations hands each cell the group's base,
# so every stage downstream keeps dispatching on media_type alone.
def raw(mt, mods, gid=None):
    return {"media_type": mt, "modifiers": mods, "group_id": gid,
            "search_term": "t", "position": "1"}
tagged = {"a": raw("stock", ["group"], 1), "b": raw("hold_previous", ["group"], 1),
          "c": raw("hold_previous", ["group"], 1), "d": raw("hold_previous", []),
          "e": raw("ai_stock", ["group"], 2), "f": raw("hold_previous", ["group"], 2)}
resolve_group_continuations(tagged)
check([tagged[k]["media_type"] for k in "abcdef"]
      == ["stock", "stock", "stock", "hold_previous", "ai_stock", "ai_stock"]
      and [scene_is_group_continuation(tagged[k]) for k in "abcdef"]
      == [False, True, True, False, False, True],
      "continuation cells take their group's base; a plain hold is untouched")
groups = group_scene_rows([(k, tagged[k]) for k in "abcef"])
check([[t for t, _ in grp] for grp in groups] == [["a", "b", "c"], ["e", "f"]],
      "resolved cells group with the opener that they continue")
err = ""
try: resolve_group_continuations({"a": raw("hold_previous", ["group"])})
except ValueError as e: err = str(e)
check("no group is open above" in err,
      "a continuation with no opener above it is refused")
err = ""
try: resolve_group_continuations({"a": raw("stock", ["group"]),
                                  **{k: raw("hold_previous", ["group"])
                                     for k in "bcd"}})
except ValueError as e: err = str(e)
check("draws 3" in err, "a group longer than its layout is refused")

print("\n===== maths types: tabs + the `data` column =====")
check("timeline" in MEDIA_TYPE_CATALOG
      and Tag.MATHS in MEDIA_TYPE_CATALOG["timeline"]["tags"],
      "timeline is a catalog type on the maths tag")
check([t["name"] for t in MEDIA_TYPE_TABS] == ["material", "maths"]
      and [tag for tag, _ in MEDIA_TYPE_TABS[1]["columns"]] == [Tag.MATHS],
      "the tagger's tabs come from CONFIG: material, then maths")
_reachable = {n for n, e in MEDIA_TYPE_CATALOG.items()
              for tab in MEDIA_TYPE_TABS for tag, _ in tab["columns"]
              if tag in e["tags"]}
check(_reachable == set(MEDIA_TYPE_CATALOG),
      "every media type sits on some tab (none is unreachable in the tagger)")
check([f.name for f in data_fields_for("timeline")] == ["year"]
      and data_fields_for("stock") == (),
      "timeline declares one data field; ordinary types declare none")
tl = {"media_type": "timeline", "data": {"year": "1600"}}
normalise_scene_row("t", tl)
check(scene_data(tl) == {"year": 1600} and isinstance(scene_data(tl)["year"], int),
      "a year typed into the tagger's <input> loads as an int")
check(scene_data({"media_type": MediaType.STOCK}) == {},
      "scene_data is {} for a row with no data column")
for bad, want in (({"year": 99999}, "typo"), ({}, "needs data.year"),
                  ({"yr": 1600}, "unknown data key")):
    err = ""
    try: normalise_scene_row("t", {"media_type": "timeline", "data": bad})
    except ValueError as e: err = str(e)
    check(want in err, f"timeline data refused: {want}")
err = ""
try: normalise_scene_row("t", {"media_type": "stock", "data": {"year": 1600}})
except ValueError as e: err = str(e)
check("takes no data" in err, "data on a type that declares none is refused")
check(coerce_scene_data("timeline", {}, "t", require_all=False) == {},
      "require_all=False lets the tagger save a half-typed form")
check(0 < timeline_min_playable_seconds() < timeline_transition_seconds(),
      "the timeline's animation ends on a trimmable settle beat")
check(timeline_transition_seconds() <= 2.5,
      "the animation is short enough to actually play on a typical narration "
      "line (the median is ~1.3s; a 3s+ animation always falls to its still)")

print("\n===== timeline: the axis never spoils its own reveal =====")
from ___visuals.maths.timeline import _axis_bounds, _tick_years
def ticks(start, target):
    lo, hi = _axis_bounds(start, target)
    return _tick_years(lo, hi, target, start)
check(ticks(2026, 1600) == [1700, 1800, 1900],
      "a four-century journey is marked per century, clear of both ends")
check(1600 not in ticks(2026, 1600) and 2026 not in ticks(2026, 1600),
      "neither the target (it is the REVEAL) nor the start is a tick label")
check(2000 not in ticks(2026, 1600),
      "a round year too close to an endpoint is dropped (2000 would hit 2026)")
check(all(1969 != y for y in ticks(2026, 1969))
      and max(ticks(2026, 1969)) - min(ticks(2026, 1969)) <= 2026 - 1969,
      "a fifty-year journey is marked per decade, inside the axis")
check(ticks(2026, 2026) and 2026 not in ticks(2026, 2026),
      "a timeline to THIS year still draws an axis (zero span is padded)")
from ___visuals.maths.timeline import _cache_key
import CONFIG as _cfg
_k1 = _cache_key(2026, 1600)
_old_travel = _cfg.TIMELINE_TRAVEL_SEC
_cfg.TIMELINE_TRAVEL_SEC = _old_travel + 1.0
import ___visuals.maths.timeline as _tl
_il.reload(_tl)
check(_tl._cache_key(2026, 1600) != _k1,
      "the render cache key covers the TIMINGS, not just the years — changing "
      "them must not serve the old animation")
_cfg.TIMELINE_TRAVEL_SEC = _old_travel
_il.reload(_tl)
check(_tl._cache_key(2026, 1600) == _k1, "...and restoring them restores the key")

print("\n===== the chart types: counter / progress_bar / bar / pie / line =====")
_charts = ("counter", "progress_bar", "bar_chart", "pie_chart", "line_graph")
check(all(c in MEDIA_TYPE_CATALOG
          and Tag.MATHS in MEDIA_TYPE_CATALOG[c]["tags"] for c in _charts),
      "every chart type is a catalog entry on the maths tag (so the tagger's "
      "maths tab shows it with no UI change)")
check(all(c in TERM_OPTIONAL_TYPES for c in _charts),
      "chart types need no search term — they are driven by row['data']")
check(parse_series("Rome:900, Athens : 300.5") == [("Rome", 900.0),
                                                   ("Athens", 300.5)]
      and format_series(parse_series("a: 1.50, b: 2")) == "a: 1.5, b: 2",
      "'label: value' pairs parse and re-print canonically")
check(parse_series("Q1: 2020: 5") == [("Q1: 2020", 5.0)],
      "the LAST colon splits, so a label may itself contain one")
pc = {"media_type": "pie_chart",
      "data": {"slices": "Gold:40, Silver:25.5,Bronze: 10", "title": "medals"}}
normalise_scene_row("p", pc)
check(scene_data(pc)["slices"] == "Gold: 40, Silver: 25.5, Bronze: 10",
      "a typed slice list is stored in the canonical spelling (a STRING — it "
      "round-trips through the tagger's text input)")
for mt, bad, want in (
        ("pie_chart", {"slices": "Gold: 40"}, "2–6"),
        ("pie_chart", {"slices": "a: 4, b: -2"}, "negative"),
        ("pie_chart", {"slices": "a: 0, b: 0"}, "all zero"),
        ("bar_chart", {"bars": "Rome 900, Athens 300"}, "not 'label: value'"),
        ("bar_chart", {"bars": "a: 1, b: two"}, "not a number"),
        ("line_graph", {"points": ", ".join(f"p{i}: {i}" for i in range(9))},
         "2–8"),
        ("progress_bar", {"percent": 130}, "not between 0 and 100"),
        ("counter", {"value": "many"}, "must be a number")):
    err = ""
    try: normalise_scene_row("t", {"media_type": mt, "data": bad})
    except ValueError as e: err = str(e)
    check(want in err, f"{mt} data refused: {want}")
lg = {"media_type": "line_graph", "data": {"points": "1900: -5, 1950: 3"}}
normalise_scene_row("l", lg)
check(scene_data(lg)["points"] == "1900: -5, 1950: 3",
      "a line graph may dip negative (a trend isn't shares of a whole)")
pb = {"media_type": "progress_bar", "data": {"percent": "73"}}
normalise_scene_row("pb", pb)
check(scene_data(pb)["percent"] == 73.0,
      "a percent typed into the tagger's <input> loads as a float")
check(0 < chart_min_playable_seconds() < chart_transition_seconds()
      and chart_transition_seconds() <= 2.5,
      "chart animations end on a trimmable settle beat and stay short enough "
      "to actually play (same budget note as the timeline)")
from ___visuals.maths import bar_chart as _bc
_k_bar = _bc._cache_key("a: 1, b: 2", "t")
_old_anim = _cfg.CHART_ANIM_SEC
_cfg.CHART_ANIM_SEC = _old_anim + 1.0
check(_bc._cache_key("a: 1, b: 2", "t") != _k_bar,
      "chart cache keys cover the look/timings, not just the data")
_cfg.CHART_ANIM_SEC = _old_anim
check(_bc._cache_key("a: 1, b: 2", "t") == _k_bar,
      "...and restoring them restores the key")
from ___visuals.maths.pie_chart import _slices as _pie_slices
_sl = _pie_slices([("a", 1.0), ("b", 0.0), ("c", 3.0)])
check([s[0] for s in _sl] == ["a", "c"]
      and abs(sum(s[1] for s in _sl) - 1.0) < 1e-9
      and _sl[0][2] != _sl[1][2],
      "pie: zero shares are dropped, fractions sum to 1, colours come from "
      "the palette in fixed slot order")
from ___visuals.maths.line_graph import _positions as _lg_pos
_ps = _lg_pos([("a", 5.0), ("b", 5.0), ("c", 5.0)])
check(len({y for _, y in _ps}) == 1 and _ps[0][0] < _ps[1][0] < _ps[2][0],
      "line: a flat series still draws (zero value-span is padded)")
# SCENE_GENERATORS drags in the whole pipeline (downloads, whisper, …), which
# the sandbox doesn't stub — check the registry at source level instead.
_sg_src = (HERE / "___visuals" / "SCENE_GENERATORS.py").read_text()
_registry = _sg_src.split("_MATHS_RENDERERS", 1)[1].split("}", 1)[0]
check(all(f"MediaType.{c.upper()}:" in _registry
          for c in ("timeline", *_charts)),
      "every maths catalog type has a renderer in _MATHS_RENDERERS")

print("\n===== downloads: history.json is an index, not the only record =====")
import json, importlib as _il
# CACHE_IO is stubbed at the top of this file; the download cache is the real
# thing we want to exercise, so put the real module back for this block.
_stub_cio = sys.modules.pop("___visuals.CACHE_IO")
_cio = _il.import_module("___visuals.CACHE_IO")
_dl = _il.import_module("___visuals.DOWNLOADS")
_sf = work / "stock_footage"; _sf.mkdir(parents=True, exist_ok=True)
_dl.STOCK_FOOTAGE_CACHE_DIR = _sf
_cio.HISTORY_FILE = _sf / "history.json"
class _NoNetwork:
    def get(self, *a, **k):
        raise AssertionError("tried to download something already on disk")
_dl.requests = _dl._http_session = _dl._wiki_session = _NoNetwork()
_url = "https://videos.pexels.com/example/clip.mp4"
_dest = _sf / f"pexels-{_dl._url_hash(_url)}.mp4"
_dest.write_bytes(b"pretend footage")          # on disk, but NOT in history
check(not (_sf / "history.json").exists(),
      "no history.json: exactly what a `find -name '*.json' -delete` leaves")
check(_dl._download_clip_parallel(_url) == str(_dest),
      "a file on disk is a cache hit even with history.json gone (no network)")
check(json.loads((_sf / "history.json").read_text()) == {_url: str(_dest)},
      "...and the cache hit re-indexes it into a fresh history.json")
_part = _sf / "pexels-deadbeefdead.mp4"
_part.with_name(_part.name + ".part").write_bytes(b"half")
check(_dl._already_downloaded("http://x/a.mp4", _part, lock=True) is None,
      "a killed download's .part file is never mistaken for a finished one")
_empty = _sf / "pexels-cafecafecafe.mp4"; _empty.touch()
check(_dl._already_downloaded("http://x/b.mp4", _empty, lock=True) is None,
      "a zero-byte file is not a cache hit")
sys.modules["___visuals.CACHE_IO"] = _stub_cio   # the later stages want the stub

print("\n===== per-type properties drive the stages =====")
check(media_props(MediaType.AI_STOCK).is_ai_base
      and media_props(MediaType.AI_EDIT_PREVIOUS).is_ai_edit
      and media_props(MediaType.HOLD_PREVIOUS).is_hold_previous
      and media_props(MediaType.STOCK_ON_BOARD).is_on_board,
      "flag spot-checks")
check(media_props(MediaType.WIKIPEDIA_ON_BOARD).uses_wikipedia
      and not media_props(None).needs_external_candidates,
      "wiki/None property rows behave")

print("\n===== decorate + collage stages import and short-circuit =====")
from ___visuals.DECORATE_STAGE import run_decorate_stage
from ___visuals.COLLAGE_STAGE import run_collage_stage
fd = [{"script_text": "plain", "footage": [{"/tmp/x.jpg": 2.0}]}]
sts = {"plain": {"media_type": MediaType.STOCK, "modifiers": [],
                 "search_term": "x", "group_id": None, "position": "1"}}
check(run_decorate_stage(fd, sts) == (fd, {})
      and run_collage_stage(fd, sts) == (fd, {}),
      "no decorate/collage rows -> both stages no-op")
fd2 = [{"script_text": "one pick", "footage": [{"/tmp/only.jpg": 2.0}]}]
sts2 = {"one pick": {"media_type": MediaType.STOCK, "modifiers": ["collage"],
                     "search_term": "x", "group_id": None, "position": "1"}}
_, remap2 = run_collage_stage(fd2, sts2)
check(remap2 == {} and next(iter(fd2[0]["footage"][0])) == "/tmp/only.jpg",
      "collage with <2 usable picks: warned and left untouched")

print("\n===== stickman generator filter (raw json rows) =====")
sys.path.insert(0, str(HERE.parent))
import importlib
sg = importlib.import_module("ai_generate_stickman_images") if \
     (HERE.parent / "ai_generate_stickman_images.py").exists() else None
if sg:
    check(sg._row_matches({"media_type": "ai_stock", "modifiers": []}, False)
          and sg._row_matches({"media_type": "ai_stock", "modifiers": ["group"]}, True)
          and not sg._row_matches({"media_type": "ai_stock", "modifiers": ["group"]}, False)
          and not sg._row_matches({"media_type": "stock", "modifiers": []}, False)
          and sg._row_matches({"media_type": MediaType.AI_STOCK, "modifiers": []}, False),
          "raw-file filter: media_type + grouped-ness (string or enum)")

print("\n===== collage modifier =====")
from CONFIG import COLLAGEABLE_TYPES, scene_wants_collage
check("caption" in MODIFIERS and COLLAGEABLE_TYPES == {"stock"},
      "caption is a modifier again (automatic); collage is stock-only")
row = {"search_term": "x", "media_type": "stock", "modifiers": ["collage"]}
normalise_scene_row("c1", row)
check(scene_wants_collage(row), "stock + collage normalises")
err = ""
try: normalise_scene_row("c2", {"media_type": "wikipedia", "modifiers": ["collage"]})
except ValueError as e: err = str(e)
check("collage" in err and "wikipedia" in err, "collage on wikipedia refused")
err = ""
try: normalise_scene_row("c3", {"media_type": "stock",
                                "modifiers": ["group", "collage"]})
except ValueError as e: err = str(e)
check("cannot combine" in err, "group + collage together refused")

print("\n===== decorator: auto collage renders headless =====")
from PIL import Image
from ___visuals.decorator import auto_collage
pics = []
for i, col in enumerate(("#c0392b", "#2e6da4", "#1e8449")):
    p = str(work / f"pic{i}.png")
    Image.new("RGB", (640, 480), col).save(p)
    pics.append(p)
out = auto_collage(pics, str(work / "collage.png"), seed="scene x")
img = Image.open(out)
check(img.size == (1920, 1080), "collage canvas is 1920x1080")
out2 = auto_collage(pics, str(work / "collage2.png"), seed="scene x")
check(Image.open(out2).tobytes() == img.tobytes(),
      "same seed -> identical layout (deterministic)")
out3 = auto_collage(pics, str(work / "collage3.png"), seed="different")
check(Image.open(out3).tobytes() != img.tobytes(),
      "different seed -> different layout")
err = ""
try: auto_collage([pics[0]], str(work / "bad.png"))
except ValueError as e: err = str(e)
check("at least 2" in err, "collage needs at least two images")

print("\n===== decorator: ONE persistent window per session =====")
# draw.py needs tkinter (absent here) — stub the session; capture the runner
SESSIONS = []
drawstub = types.ModuleType("___visuals.decorator.draw")
def _fake_session(base, window_title, tabs, stamps, work_dir, **kwargs):
    # **kwargs: the editor keeps growing options (stamp_mode, …) the stub
    # doesn't care about — swallow them so the suite doesn't break each time.
    SESSIONS.append({"base": base, "stamps": list(stamps or [])})
    return _fake_session.result
drawstub.run_editor_session = _fake_session
sys.modules["___visuals.decorator.draw"] = drawstub
from ___visuals.decorator.api import run_decorator

img = work / "base.png"; img.write_text("p")
edited = work / "edited.png"; edited.write_text("p")
_fake_session.result = ("finish", str(edited))
out = run_decorator(str(img), str(work / "out1.png"),
                    stamps=[str(img)], title="t")
check(out is not None and Path(out).exists() and len(SESSIONS) == 1
      and SESSIONS[0]["stamps"] == [str(img)],
      "one session = ONE window; stamps passed through; result saved")
_fake_session.result = ("exit", None)
check(run_decorator(str(img), str(work / "out2.png")) is None,
      "closing the window abandons the session (footage kept)")
vid = work / "anim.mp4"; vid.write_text("v")
_fake_session.result = ("finish", str(vid))
out3 = run_decorator(str(img), str(work / "out3.png"))
check(out3 is not None and out3.endswith(".mp4") and Path(out3).exists(),
      "an animated MP4 session result saves with the right suffix")
import ___visuals.decorator as _dpkg
_dec_dir = Path(_dpkg.__file__).resolve().parent
import ast as _ast
for _f in sorted(_dec_dir.glob("*.py")):
    _ast.parse(_f.read_text())
check(True, "every decorator file parses (syntax gate)")
draw_src = (_dec_dir / "draw.py").read_text()
obj_src = (_dec_dir / "object_editor.py").read_text()
check("ObjectSeparator(" in draw_src and "master=self.root" in draw_src
      and "on_done=_done" in draw_src,
      "object tab MOUNTS the editor inside the decorator window")
check("class ObjectSeparator(tk.Frame)" in obj_src
      and "self._owns_root" in obj_src
      and "apply effect & back" in obj_src,
      "editor is dual-mode: standalone window OR embedded frame, no "
      "export-now wording when embedded")

print("\n===== the caption modifier is AUTOMATIC (no editor) =====")
CAPTIONS = []
mtostub = types.ModuleType("___visuals.MAKE_TEXT_OVERLAY")
mtostub.make_text_overlay = lambda base, text, out, **k: (
    CAPTIONS.append({"base": base, "text": text}), Path(out).write_text("p"))
sys.modules["___visuals.MAKE_TEXT_OVERLAY"] = mtostub
import ___visuals.DECORATE_STAGE as DS
DS._load_scene_timings = lambda: {"cap line": 2.0}
DS._resolve_to_local_path = lambda p: p
sts_c = {"cap line": {"media_type": MediaType.STOCK, "modifiers": ["caption"],
                      "search_term": "WORTH ITS WEIGHT", "group_id": None,
                      "position": "1"}}
img = work / "capbase.png"; img.write_text("p")
fd_c = [{"script_text": "cap line", "footage": [{str(img): 2.0}]}]
fd_c2, remap_c = DS.run_decorate_stage(fd_c, sts_c)
new_key = next(iter(fd_c2[0]["footage"][0]))
check(CAPTIONS and CAPTIONS[0]["text"] == "WORTH ITS WEIGHT"
      and CAPTIONS[0]["base"] == str(img)
      and new_key.endswith(".mp4") and remap_c,
      "caption row: tilted caption baked hands-free, footage swapped to mp4")

print("\n===== decorate stage: animated MP4 results used directly =====")
import ___visuals.DECORATE_STAGE as DS2
anim = work / "sess_result.mp4"; anim.write_text("v")
apistub = sys.modules["___visuals.decorator.api"] if \
    "___visuals.decorator.api" in sys.modules else None
import ___visuals.decorator.api as dapi
dapi_run = dapi.run_decorator
dapi.run_decorator = lambda **k: str(anim)
DS2.run_decorator = dapi.run_decorator
DS2._load_scene_timings = lambda: {"anim line": 2.0}
DS2._resolve_to_local_path = lambda p: p
base2 = work / "b2.png"; base2.write_text("p")
sts_a = {"anim line": {"media_type": MediaType.STOCK, "modifiers": ["decorate"],
                       "search_term": "x", "group_id": None, "position": "1"}}
fd_a = [{"script_text": "anim line", "footage": [{str(base2): 2.0}]}]
fd_a2, remap_a = DS2.run_decorate_stage(fd_a, sts_a)
key_a = next(iter(fd_a2[0]["footage"][0]))
check(key_a.endswith(".mp4") and Path(key_a).exists() and remap_a,
      "decorate stage: animated session result copied in, no still-baking")
dapi.run_decorator = dapi_run

print("\n===== stages import + short-circuit cleanly =====")
from ___visuals import COLLAGE_STAGE
from ___visuals.DECORATE_STAGE import run_decorate_stage
fd0 = [{"script_text": "plain", "footage": [{"/tmp/a.jpg": 1.0}]}]
sts0 = {"plain": {"media_type": MediaType.STOCK, "modifiers": [],
                  "search_term": "x", "group_id": None, "position": "1"}}
fd1, r1 = run_decorate_stage(fd0, sts0)
check(fd1 is fd0 and r1 == {}, "decorate stage: no decorate scenes → no-op")
COLLAGE_STAGE._load_scene_timings = lambda: {"col line": 2.0}
sts1 = {"col line": {"media_type": MediaType.STOCK, "modifiers": ["collage"],
                     "search_term": "x", "group_id": None, "position": "1"}}
fd2 = [{"script_text": "col line", "footage": [{"/tmp/only_one.jpg": 2.0}]}]
fd3, r3 = COLLAGE_STAGE.run_collage_stage(fd2, sts1)
check(r3 == {} and next(iter(fd3[0]["footage"][0])) == "/tmp/only_one.jpg",
      "collage stage: <2 usable picks → clear warning, footage untouched")

print()
print("FINAL RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
sys.exit(1 if fails else 0)
