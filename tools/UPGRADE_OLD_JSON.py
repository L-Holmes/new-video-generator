"""
UPGRADE_OLD_JSON.py — one-time converter for OLD flat script_to_search_term
files (the ones with just a search_type string) to the current schema.

    uv run tools/UPGRADE_OLD_JSON.py <file.json> [more files...]

Writes the upgraded file in place (a .pre-upgrade.bak copy is kept). New
files never need this — ___splitting_and_labelling writes the current
schema directly. Run it once per old file and then delete this script if
you like; nothing in the pipeline imports it.

What it does per row:
  - search_type string  ->  media_type (+ modifiers), then the column is
    REMOVED (the renderer dispatches on media_type now)
  - joint_3_row runs            -> stock  + ["group"]  + shared group_id
  - stickman_joint_3_row runs   -> ai_stock + ["group"] + shared group_id
  - zoom_prev_img               -> hold_previous + ["decorate"]   (zoom is a
                                   decorate-editor tool now)
  - decorate_previous           -> hold_previous + ["decorate"]
  - stickman_text_overlay       -> hold_previous + ["decorate"]   (caption is
                                   a decorate-editor tool; the old caption
                                   text stays in search_term as the prefill)
  - static_of_previous          -> hold_previous
  - stickman                    -> ai_stock
  - ai_edit                     -> ai_edit_previous
  - read_out                    -> typography
  - object_generate             -> stock + ["decorate"]  (object tab cuts it out)
  - manual_stock_add_to_previous-> hold_previous + ["decorate"]  (stamp tab)
  - stickman_explain_stock      -> stock_on_board
  - stickman_explain_wikipedia  -> wikipedia_on_board
  - stock / wikipedia / map     -> unchanged names
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

OLD_TO_NEW: dict[str, tuple[str, list[str]]] = {
    "stock": ("stock", []),
    "wikipedia": ("wikipedia", []),
    "map": ("map", []),
    "read_out": ("typography", []),
    "object_generate": ("stock", ["decorate"]),  # cut-out = the object TAB
    "stickman": ("ai_stock", []),
    "ai_edit": ("ai_edit_previous", []),
    "stickman_explain_stock": ("stock_on_board", []),
    "stickman_explain_wikipedia": ("wikipedia_on_board", []),
    "manual_stock_add_to_previous": ("hold_previous", ["decorate"]),  # stamp TAB
    "static_of_previous": ("hold_previous", []),
    "zoom_prev_img": ("hold_previous", ["decorate"]),
    "decorate_previous": ("hold_previous", ["decorate"]),
    "stickman_text_overlay": ("hold_previous", ["decorate"]),
    "joint_3_row": ("stock", ["group"]),
    "stickman_joint_3_row": ("ai_stock", ["group"]),
}


def upgrade(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    shutil.copy2(path, path.with_name(path.name + ".pre-upgrade.bak"))

    out: dict[str, dict] = {}
    group_counter = 0
    prev_grouped = False
    for line, row in data.items():
        if row.get("media_type"):  # already current schema
            row.pop("search_type", None)
            out[line] = row
            prev_grouped = "group" in (row.get("modifiers") or [])
            continue
        legacy = row.pop("search_type", "")
        if legacy not in OLD_TO_NEW:
            sys.exit(f"{path.name}: unknown search_type {legacy!r} on "
                     f"'{line[:60]}' — fix by hand and re-run")
        media_type, modifiers = OLD_TO_NEW[legacy]
        grouped = "group" in modifiers
        if grouped and not prev_grouped:
            group_counter += 1
        prev_grouped = grouped

        new_row = {"search_term": row.pop("search_term", ""),
                   "media_type": media_type,
                   "modifiers": list(modifiers),
                   "group_id": group_counter if grouped else None,
                   "position": row.pop("position", "1")}
        new_row.update(row)          # audio columns etc. carried over as-is
        new_row.setdefault("rule_ids", [])
        out[line] = new_row

    path.write_text(json.dumps(out, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    n_groups = len({r["group_id"] for r in out.values() if r.get("group_id")})
    print(f"{path.name}: upgraded {len(out)} rows "
          f"({n_groups} group(s)) — backup at {path.name}.pre-upgrade.bak")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: uv run UPGRADE_OLD_JSON.py <file.json> [more...]")
    for arg in sys.argv[1:]:
        upgrade(Path(arg))
