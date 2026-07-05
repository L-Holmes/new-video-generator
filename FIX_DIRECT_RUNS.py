"""
FIX_DIRECT_RUNS.py — make EVERY ___visuals file runnable directly.

    uv run FIX_DIRECT_RUNS.py          (from the repo root)

Inserts the standard 4-line sys.path bootstrap into any ___visuals/*.py
(and ___visuals/decorator/*.py) that doesn't have it yet, right after the
module docstring / `from __future__` line. Idempotent — safe to re-run any
time you add a file. Verifies each edited file still parses. Then delete
this script or keep it around for new files; nothing imports it.
"""
import ast
import sys
from pathlib import Path

BOOT = '''# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().{parents}))
'''

def ensure_boot(path: Path, depth: int) -> str:
    src = path.read_text(encoding="utf-8")
    if '__package__ in (None, "")' in src:
        return "ok (already)"
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return f"SKIPPED (does not parse: {exc})"
    lines = src.splitlines(keepends=True)
    insert_at = 0
    body = tree.body
    i = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant):
        insert_at = body[0].end_lineno
        i = 1
    if len(body) > i and isinstance(body[i], ast.ImportFrom) \
            and body[i].module == "__future__":
        insert_at = body[i].end_lineno
    boot = BOOT.format(parents=".".join(["parent"] * depth))
    lines.insert(insert_at, "\n" + boot)
    path.write_text("".join(lines), encoding="utf-8")
    ast.parse(path.read_text(encoding="utf-8"))
    return "bootstrapped"

root = Path.cwd()
pkg = root / "___visuals"
if not pkg.is_dir():
    sys.exit("run this FROM THE REPO ROOT (the folder containing ___visuals/)")

for p in sorted(pkg.glob("*.py")):
    if p.name != "__init__.py":
        print(f"  ___visuals/{p.name}: {ensure_boot(p, 2)}")
dec = pkg / "decorator"
if dec.is_dir():
    for p in sorted(dec.glob("*.py")):
        if p.name != "__init__.py":
            print(f"  ___visuals/decorator/{p.name}: {ensure_boot(p, 3)}")
print("\ndone — every listed file can now run via `uv run ___visuals/<file>.py`")
