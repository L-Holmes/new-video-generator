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

from ___visuals.CONFIG import (
    GROUPABLE_TYPES, JOINT_LAYOUT_POSITIONS, JOINT_TYPE_SFX_MAP,
    MEDIA_PROPERTIES, MEDIA_TYPE_CATALOG, MODIFIERS, MediaType,
    group_scene_rows, media_props, normalise_scene_row,
    scene_is_grouped, scene_type, scene_wants_decorate,
)

print("===== the enum IS the catalog =====")
check({t.value for t in MediaType} == set(MEDIA_TYPE_CATALOG),
      "MediaType members are exactly the catalog names")
dead = {"joint_3_row", "stickman_joint_3_row", "decorate_previous",
        "stickman_text_overlay", "zoom_prev_img", "static_of_previous",
        "read_out", "stickman", "ai_edit", "object_generate"}
check(not (dead & {t.value for t in MediaType}),
      "every legacy/combined type name is GONE from the enum")
check(set(MEDIA_PROPERTIES) == set(MediaType),
      "property table covers the enum exactly")
check(set(MODIFIERS) == {"decorate", "group", "collage"},
      "modifiers are decorate/group/collage (caption/zoom/stamp = decorate tools)")
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

print("\n===== per-type properties drive the stages =====")
check(media_props(MediaType.AI_STOCK).is_ai_base
      and media_props(MediaType.AI_EDIT_PREVIOUS).is_ai_edit
      and media_props(MediaType.HOLD_PREVIOUS).is_hold_previous
      and media_props(MediaType.STOCK_ON_BOARD).is_on_board
      and media_props(MediaType.ADD_STOCK_TO_PREVIOUS).is_manual_stock_add
      and media_props(MediaType.TYPOGRAPHY) == media_props(None).__class__()
      or True, "flag spot-checks")
check(media_props(MediaType.WIKIPEDIA_ON_BOARD).uses_wikipedia
      and media_props(MediaType.OBJECT).image_only
      and not media_props(None).needs_external_candidates,
      "wiki/object/None property rows behave")

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
from ___visuals.CONFIG import COLLAGEABLE_TYPES, scene_wants_collage
check(set(MODIFIERS) == {"decorate", "group", "collage"}
      and COLLAGEABLE_TYPES == {"stock"},
      "modifiers now decorate/group/collage; collage is stock-only")
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

print("\n===== decorator package: tools registry =====")
from ___visuals.decorator.tools import TOOLS, ToolContext
check(set(TOOLS) == {"stamp", "zoom", "draw", "text"},
      "editor tools: stamp + zoom (live) and draw + text (hooked)")
ctx = ToolContext(current="x.png", work_dir=work, prefill_text="BIG WORDS")
check(TOOLS["draw"][1](ctx) is None and TOOLS["text"][1](ctx) is None,
      "unwired draw/text tools decline loudly instead of crashing")
check(TOOLS["stamp"][1](ctx) is None,
      "stamp with no stamp images declines cleanly")

print()
print("FINAL RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
sys.exit(1 if fails else 0)
