"""
Integration tests for the new media_type/modifiers/group_id schema in the
video-builder codebase. Stubs the modules that weren't part of this change
(COLOUR_GRADE_ETC, CACHE_IO, TIMING_MERGE, MAKE_TEXT_OVERLAY, requests use
is real) so CONFIG + MODIFIER_STAGE import cleanly in a sandbox.
"""
import json, sys, types, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
work = HERE / "testrun"; shutil.rmtree(work, ignore_errors=True)
work.mkdir(); import os; os.chdir(work)  # CONFIG creates dirs — keep them here

# ---- stubs for modules CONFIG/MODIFIER_STAGE import but we didn't touch ----
cge = types.ModuleType("___visuals.COLOUR_GRADE_ETC"); cge.DEFAULT_PRESET = "film"
sys.modules["___visuals.COLOUR_GRADE_ETC"] = cge
cio = types.ModuleType("___visuals.CACHE_IO")
cio._resolve_to_local_path = lambda p: p  # tests use plain local-ish paths
sys.modules["___visuals.CACHE_IO"] = cio
tm = types.ModuleType("___visuals.TIMING_MERGE")
TIMINGS = {}
tm._load_scene_timings = lambda: TIMINGS
sys.modules["___visuals.TIMING_MERGE"] = tm
mto = types.ModuleType("___visuals.MAKE_TEXT_OVERLAY")
CAPTION_CALLS = []
def _fake_overlay(base_image_path, text, output_path, duration, seed):
    CAPTION_CALLS.append({"base": base_image_path, "text": text,
                          "out": output_path, "dur": duration})
    Path(output_path).write_text("mp4")
mto.make_text_overlay = _fake_overlay
sys.modules["___visuals.MAKE_TEXT_OVERLAY"] = mto

fails = []
def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond: fails.append(label)

from ___visuals.CONFIG import (
    MediaType, normalise_scene_row, scene_residual_modifiers,
    group_scene_rows, new_type_to_media_type, MEDIA_TYPE_CATALOG, MODIFIERS,
)
from ___visuals.MEDIA_CATALOG import to_legacy, residual_modifiers

print("===== catalog + derivation =====")
check(all(d["legacy"] in {t.value for t in MediaType}
          for d in MEDIA_TYPE_CATALOG.values()),
      "every catalog legacy string has a MediaType enum member")
check(new_type_to_media_type("stock", ["group"]) is MediaType.JOINT_3_ROW
      and new_type_to_media_type("ai_stock", ["group"]) is MediaType.STICKMAN_JOINT_3_ROW
      and new_type_to_media_type("hold_previous", ["decorate"]) is MediaType.DECORATE_PREVIOUS
      and new_type_to_media_type("hold_previous", ["caption"]) is MediaType.STICKMAN_TEXT_OVERLAY
      and new_type_to_media_type("wikipedia", ["decorate"]) is MediaType.WIKIPEDIA,
      "the four combined-mode exceptions + plain rule derive correctly")

print("\n===== loader (normalise_scene_row) =====")
row = {"search_term": "ribs bones", "search_type": "joint_3_row",
       "media_type": "stock", "modifiers": ["group"], "group_id": 1,
       "position": "1"}
normalise_scene_row("ribs,", row)
check(row["search_type"] is MediaType.JOINT_3_ROW,
      "new row: enum derived from media_type + modifiers")
old_row = {"search_term": "coin", "search_type": "stickman", "position": "1"}
normalise_scene_row("It costs about", old_row)
check(old_row["search_type"] is MediaType.STICKMAN
      and old_row["modifiers"] == [] and old_row["group_id"] is None,
      "old flat row: converts directly + gains default columns")
err = None
try:
    normalise_scene_row("x", {"media_type": "holograms", "search_term": ""})
except ValueError as e: err = str(e)
check(err and "holograms" in err and "stock" in err,
      "unknown media_type raises with the valid names listed")
err = None
try:
    normalise_scene_row("x", {"search_type": "sparkles", "search_term": ""})
except ValueError as e: err = str(e)
check(err and "sparkles" in err, "unknown legacy search_type still raises")
err = None
try:
    normalise_scene_row("x", {"media_type": "stock", "modifiers": ["confetti"]})
except ValueError as e: err = str(e)
check(err and "confetti" in err, "unknown modifier raises")

print("\n===== residual modifiers (what MODIFIER_STAGE must layer) =====")
check(residual_modifiers("stock", ["group"], "joint_3_row") == []
      and residual_modifiers("hold_previous", ["decorate"], "decorate_previous") == []
      and residual_modifiers("hold_previous", ["caption"], "stickman_text_overlay") == []
      and residual_modifiers("stock", ["caption"], "stock") == ["caption"]
      and residual_modifiers("wikipedia", ["decorate", "group"], "wikipedia") == ["decorate"],
      "baked-in modifiers consumed; leftovers reported for layering")
r = {"media_type": "stock", "modifiers": ["caption"],
     "search_type": MediaType.STOCK}
check(scene_residual_modifiers(r) == ["caption"],
      "scene_residual_modifiers works on a normalised row")
check(scene_residual_modifiers({"search_type": MediaType.STOCK}) == [],
      "old flat rows never have residuals")

print("\n===== joint grouping (group_id first, legacy fallback) =====")
def jrow(gid, pos, mt=MediaType.JOINT_3_ROW):
    return {"search_type": mt, "group_id": gid, "position": pos}
scenes = [("a", jrow(1, "1")), ("b", jrow(1, "2")),
          ("c", jrow(2, "1")), ("d", jrow(2, "2")), ("e", jrow(2, "3"))]
groups = group_scene_rows(scenes)
check([len(g) for g in groups] == [2, 3]
      and [t for t, _ in groups[0]] == ["a", "b"],
      "two back-to-back group_ids split into two groups (old sort would interleave)")
legacy = [("a", {"search_type": MediaType.JOINT_3_ROW, "position": "1"}),
          ("b", {"search_type": MediaType.JOINT_3_ROW, "position": "2"}),
          ("c", {"search_type": MediaType.JOINT_3_ROW, "position": "1"})]
check([len(g) for g in group_scene_rows(legacy)] == [2, 1],
      "legacy rows without group_id fall back to contiguous positions")
mixed = [("a", jrow(3, "1")), ("b", jrow(3, "2", MediaType.STICKMAN_JOINT_3_ROW))]
check([len(g) for g in group_scene_rows(mixed)] == [1, 1],
      "same group_id but different type never merges")

print("\n===== MODIFIER_STAGE (caption layer on own footage) =====")
from ___visuals import MODIFIER_STAGE
TIMINGS.update({"the seed was gold": 2.5, "hold line": 1.5})
sts = {
    "the seed was gold": {"search_term": "nutmeg seed macro",
                          "media_type": "stock", "modifiers": ["caption"],
                          "search_type": MediaType.STOCK, "group_id": None,
                          "position": "1"},
    "hold line": {"search_term": "WORTH ITS WEIGHT",
                  "media_type": "hold_previous", "modifiers": ["caption"],
                  "search_type": MediaType.STICKMAN_TEXT_OVERLAY,
                  "group_id": None, "position": "1"},
}
final_data = [
    {"script_text": "the seed was gold", "footage": [{"/tmp/seed.jpg": 2.5}]},
    {"script_text": "hold line", "footage": [{"/tmp/prev_overlay.mp4": 1.5}]},
]
fd, remap = MODIFIER_STAGE.apply_modifier_layers_to_final_data(fd_in := final_data, sts)
new_key = next(iter(fd[0]["footage"][0]))
check(len(CAPTION_CALLS) == 1 and CAPTION_CALLS[0]["base"] == "/tmp/seed.jpg"
      and CAPTION_CALLS[0]["text"] == "nutmeg seed macro"
      and "caption_" in new_key and remap.get("/tmp/seed.jpg") == new_key,
      "stock+caption: caption composited onto the scene's OWN image, footage swapped")
check(next(iter(fd[1]["footage"][0])) == "/tmp/prev_overlay.mp4",
      "hold_previous+caption untouched here (already handled by the legacy overlay type)")
sts2 = {"deco line": {"search_term": "x", "media_type": "wikipedia",
                      "modifiers": ["decorate"], "search_type": MediaType.WIKIPEDIA,
                      "group_id": None, "position": "1"}}
TIMINGS["deco line"] = 2.0
fd2 = [{"script_text": "deco line", "footage": [{"/tmp/wiki.jpg": 2.0}]}]
fd2, remap2 = MODIFIER_STAGE.apply_modifier_layers_to_final_data(fd2, sts2)
check(next(iter(fd2[0]["footage"][0])) == "/tmp/wiki.jpg" and not remap2,
      "decorate layer without the hook wired: clear notice, footage unchanged")

print("\n===== tagging shim over the shared catalog =====")
tagdir = work / "___splitting_and_labelling"; tagdir.mkdir()
shutil.copy(HERE / "MEDIA_TYPES_shim.py", tagdir / "MEDIA_TYPES.py")
(work / "___visuals").symlink_to(HERE / "___visuals") if not (work / "___visuals").exists() else None
sys.path.insert(0, str(tagdir))
import MEDIA_TYPES as tag_mt
check(tag_mt.MEDIA_TYPES is MEDIA_TYPE_CATALOG and tag_mt.MODIFIERS is MODIFIERS,
      "tagging tool and renderer share the SAME catalog objects")
check(tag_mt.to_legacy("stock", ["group"]) == "joint_3_row"
      and [t.value for t in tag_mt.MEDIA_TYPES["ai_stock"]["tags"]] == ["new", "ai"],
      "shim exposes the exact API MANUAL_TAGGING already uses")

print()
print("FINAL RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
sys.exit(1 if fails else 0)
