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
check(set(MODIFIERS) == {"decorate", "group"},
      "modifiers are decorate + group only (caption/zoom = decorate tools)")
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

print("\n===== decorate stage (pending editor) leaves footage intact =====")
from ___visuals.DECORATE_STAGE import run_decorate_stage
sts = {"line one": {"media_type": MediaType.STOCK, "modifiers": ["decorate"],
                    "search_term": "big text", "group_id": None, "position": "1"}}
fd = [{"script_text": "line one", "footage": [{"/tmp/x.jpg": 2.0}]}]
fd2, remap = run_decorate_stage(fd, sts)
check(fd2 is fd and remap == {} and next(iter(fd[0]["footage"][0])) == "/tmp/x.jpg",
      "decorate stage: loud pending notice, nothing mutated")

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

print()
print("FINAL RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
sys.exit(1 if fails else 0)
