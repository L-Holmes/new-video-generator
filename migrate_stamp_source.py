"""
migrate_stamp_source.py — one-shot: bring a script_to_search_term.json up
to the stamp_source schema.

    uv run migrate_stamp_source.py stickman_script_to_search_term.json

What it does, per row:
  - adds "stamp_source": null if the key is missing (every row gets it);
  - where the row is hold_previous + decorate + a NON-EMPTY search_term, it
    sets stamp_source to "stock" (the workhorse default) — because on such
    rows the term doesn't pick footage (the hold reuses the previous image),
    it describes what to STAMP, and the decorate stage now fetches those
    pictures into the editor's stamp tab from this source;
  - never overwrites a stamp_source that is already set.

A .bak copy is written next to the file first. The script prints every row
it touched so you can review — in particular, terms that were really meant
as caption text (e.g. "TRADED FOR NUTMEG!") will now fetch stamps for that
text; retag those in MANUAL_TAGGING (pick a different source, empty the
term — it's optional for hold_previous now — or move the text to the
caption modifier's caption_text).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def migrate(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    bak = path.with_name(path.name + ".bak")
    shutil.copy2(path, bak)
    print(f"[migrate] backup written: {bak.name}")

    added, defaulted = 0, []
    for line, row in data.items():
        if "stamp_source" not in row:
            row["stamp_source"] = None
            added += 1
        if row["stamp_source"] is None \
                and row.get("media_type") == "hold_previous" \
                and "decorate" in (row.get("modifiers") or []) \
                and (row.get("search_term") or "").strip():
            row["stamp_source"] = "stock"
            defaulted.append((line, row["search_term"]))

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"[migrate] {added} row(s) gained the stamp_source column "
          f"(null by default)")
    if defaulted:
        print(f"[migrate] {len(defaulted)} hold_previous+decorate row(s) with "
              f"a term now fetch STAMPS from \"stock\" — review these:")
        for line, term in defaulted:
            print(f"[migrate]   - '{line[:52]}'  →  stamps for "
                  f"'{term[:40]}' (stock)")
        print(f"[migrate] to change one: rerun MANUAL_TAGGING and use its "
              f"step 3 (or edit the json's stamp_source by hand — "
              f"\"wikipedia\" is the other option, null turns it off)")
    else:
        print("[migrate] no rows qualified for a default stamp source")
    print(f"[migrate] ✓ {path.name} updated")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).exists():
        print(__doc__)
        sys.exit(1)
    migrate(Path(sys.argv[1]))
