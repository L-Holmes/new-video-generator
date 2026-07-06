"""
SETUP.py — sets the whole update up in one go.

    unzip decorator_update.zip          (in the repo root, next to main.py)
    uv run UPDATE_DROP/SETUP.py
    uv run UPDATE_DROP/SETUP.py --clean-baks    (later: also delete backups)
    rm -r UPDATE_DROP decorator_update.zip      (when done)

It copies every file from UPDATE_DROP/files/ into its proper place (backing
up what it replaces to <name>.bak), deletes dead files AND the stray
duplicates sitting in your repo ROOT (CONFIG.py, draw.py, api.py, ... which
belong only under ___visuals/), and clears stale __pycache__. It never
touches anything it doesn't know about. NOTE: a stray _MAP_DATA folder in
the root is left alone — check it against ___visuals/_MAP_DATA yourself.
"""
import shutil
import sys
from pathlib import Path

DEAD = ["___visuals/OBJECT_SEPERATION.py",  # moved INTO ___visuals/decorator/object_editor.py
        '___visuals/OBJECT_GENERATE_STAGE.py', '___visuals/decorator/tools.py', '___visuals/decorator/MEDIA_TYPES.py', '___visuals/decorator/INTEGRATION_NOTES.md', '___visuals/DECORATE_PREVIOUS.py', '___visuals/MEDIA_CATALOG.py', '___visuals/MODIFIER_STAGE.py', '___visuals/api.py', '___visuals/tools.py', '___visuals/auto_collage.py', '___visuals/UPGRADE_OLD_JSON.py', '___visuals/test_integration.py', '___visuals/INTEGRATION_NOTES.md', '___splitting_and_labelling/test_pipeline_fixture.py']
STRAYS = ["APPLY_UPDATE.py", "INSTALL_DECORATOR.py.bak", 'CONFIG.py', 'DECORATE_STAGE.py', 'COLLAGE_STAGE.py', 'DOWNLOADS.py', 'STATIC_RENDER.py', 'draw.py', 'api.py', 'auto_collage.py', 'INSTALL_DECORATOR.py', 'test_pipeline_fixture.py']

here = Path(__file__).resolve().parent          # .../UPDATE_DROP
root = Path.cwd()
if not (root / "___visuals").is_dir():
    sys.exit("run this FROM THE REPO ROOT (the folder containing ___visuals/):"
             "\n    uv run UPDATE_DROP/SETUP.py")

def backup(p: Path):
    if p.exists():
        bak = p.with_name(p.name + ".bak")
        if bak.exists():
            bak.unlink()
        p.rename(bak)
        return True
    return False

print("— removing dead files + stray root duplicates —")
for rel in DEAD:
    if backup(root / rel):
        print(f"  removed {rel}  (backup kept)")
for name in STRAYS:
    p = root / name
    if p.is_file():
        backup(p)
        print(f"  removed stray root copy: {name}  (backup kept)")

print("— installing the drop —")
src_root = here / "files"
for src in sorted(src_root.rglob("*")):
    if src.is_dir():
        continue
    rel = src.relative_to(src_root)
    dst = root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        backup(dst)
    shutil.copy2(src, dst)
    print(f"  installed {rel}")

print("— clearing stale __pycache__ —")
for pyc in root.rglob("__pycache__"):
    s = str(pyc)
    if "___visuals" in s or "___splitting_and_labelling" in s or pyc.parent == root:
        shutil.rmtree(pyc, ignore_errors=True)
        print(f"  cleared {pyc.relative_to(root)}")

if "--clean-baks" in sys.argv:
    print("— deleting *.bak —")
    for bak in root.rglob("*.bak"):
        if "UPDATE_DROP" not in str(bak):
            bak.unlink()
            print(f"  deleted {bak.relative_to(root)}")

print("— verifying the install —")
CHECKS = [
    ("___visuals/decorator/draw.py", "stamp_panel",
     "per-tab sidebar + stamp preview"),
    ("___visuals/decorator/draw.py", "_zoom_frozen",
     "zoom box follows the cursor"),

    ("___visuals/decorator/draw.py", "on_done=_done",
     "object editor mounts INSIDE the decorator window"),
    ("___visuals/decorator/object_editor.py", "_build_scroll_controls",
     "object controls mount into the decorator sidebar (host chrome)"),
    ("___visuals/decorator/draw.py", "_side_scroll",
     "sidebar: pinned Finish/Undo + scrollable middle (nothing cut off)"),
    ("___visuals/decorator/object_editor.py", "_finish_still_only",
     "tab switch never renders MP4; green Finish renders AND ends session"),
    ("___splitting_and_labelling/Auto_add_mediatypes.py", "FLOWCHART",
     "auto media-type tagger installed"),
    ("___visuals/CONFIG.py", "ensure_runtime_dirs",
     "catalog-in-CONFIG generation"),
]
bad = 0
for rel, marker, label in CHECKS:
    ok = marker in (root / rel).read_text(encoding="utf-8")
    print(f"  {'✓' if ok else '✗ FAILED'}  {label}  ({rel})")
    bad += 0 if ok else 1
if bad:
    sys.exit(f"\n{bad} verification(s) FAILED — the new code is NOT "
             f"installed. Tell Claude exactly this output.")

print("""
✓ set up AND verified. try:
    uv run main.py
    uv run ___visuals/decorator/api.py PIC.png
then:  rm -r UPDATE_DROP decorator_update.zip
(re-run with --clean-baks when happy, to delete the .bak backups)""")
