"""
Low-level cache / JSON I/O, the url→local-path history index, and the shared
footage-path resolution helpers.

These are the leaf utilities every other stage builds on. The module depends
only on CONFIG (paths + IMAGE_EXTENSIONS), so it never creates an import cycle.
"""

from __future__ import annotations

# Allow running this file directly from the repo root (uv run ___visuals/CACHE_IO.py).
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import sys
from pathlib import Path
from typing import Iterable

from ___visuals.CONFIG import _CACHE_DIR, HISTORY_FILE, IMAGE_EXTENSIONS, STOCK_FOOTAGE_CACHE_DIR


# ===========================================================================
# AI-EDIT cache-file path helpers
# ===========================================================================


def _edit_candidates_cache_file(scene_index: int) -> str:
    return f"{_CACHE_DIR}/edit_candidates_{scene_index:03d}.json"


def _edit_review_state_file(scene_index: int) -> str:
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return str(
        STOCK_FOOTAGE_CACHE_DIR / f"review_accepting_edits_{scene_index:03d}.json"
    )


# ===========================================================================
# Cache / JSON load + save
# ===========================================================================


def save_to_cache(data: list, file_path: str):
    """Saves the script and footage data to a JSON file."""
    try:
        Path(file_path).write_text(json.dumps(data, indent=4))
    except Exception as e:
        print(f"Error saving cache: {e}")


def load_from_cache(file_path: str) -> list | None:
    """Loads data from a cache JSON file. Returns None if the file doesn't
    exist (a normal cache miss) or can't be parsed (worth flagging)."""
    p = Path(file_path)
    if not p.exists():
        # Not an error — we just haven't generated this cache yet.
        print(f"ℹ️  [cache miss] no file yet at {file_path} — will generate it.")
        return None
    try:
        return json.loads(p.read_text())
    except Exception as exc:
        print(
            f"⚠️  [cache] {file_path} exists but couldn't be parsed "
            f"({exc}) — regenerating."
        )
        return None


def load_json(file_path: str) -> dict:
    """Loads JSON data from file into a dictionary. Exits if file doesn't exist or is invalid."""
    p = Path(file_path)

    if not p.exists():
        print(f"ERROR! THE FILE DOESN'T EXIST: {file_path}")
        sys.exit(1)

    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        print(f"ERROR! INVALID JSON IN FILE: {file_path}")
        sys.exit(1)


# ===========================================================================
# History index (cache index: url → local file path)
# ===========================================================================


def _load_history() -> dict:
    """Load the URL→local-path cache index from disk."""
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_history(history: dict) -> None:
    """Persist the URL→local-path cache index to disk."""
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def _augment_history_with_identity(
    paths: Iterable[str], *, verbose_label: str | None = None
) -> tuple[int, int]:
    """
    Add identity entries (path → path) to history.json for any path not
    already present. Locally-generated files (joint/read-out/Ken-Burns/graded
    output) reuse the stitcher's url→local lookup this way with no stitcher
    changes. Returns (added, skipped).
    """
    history = _load_history()
    added = 0
    skipped = 0
    for path in paths:
        if path in history:
            skipped += 1
            continue
        history[path] = path
        added += 1
        if verbose_label:
            print(f"[{verbose_label}]   + identity entry for {Path(path).name}")
    _save_history(history)
    return added, skipped


def add_local_paths_to_history(generated_footage_map: dict[str, list[dict]]) -> None:
    """
    The stitcher's history.json maps {url → local_path}. For locally-generated
    files (joint scenes, read-out scenes, future types) we add identity
    entries (path → path) so the same lookup mechanism resolves them with no
    stitcher changes.
    """
    print(
        f"\n[history] augmenting history.json (currently {len(_load_history())} entries)"
    )
    paths = [
        path
        for entries in generated_footage_map.values()
        for entry in entries
        for path in entry
    ]
    added, skipped = _augment_history_with_identity(paths, verbose_label="history")
    print(
        f"[history] done — added={added}, already_present={skipped}, "
        f"history now has {len(_load_history())} entries"
    )


def add_path_remap_to_history(path_remap: dict[str, str], *, label: str) -> None:
    """
    Add identity entries so the stitcher's url→local lookup finds the new files
    produced by a remap stage (Ken Burns, colour grade, …). `label` only
    affects the log line.
    """
    if not path_remap:
        return
    added, _ = _augment_history_with_identity(path_remap.values())
    print(f"[{label}] added {added} identity entry(ies) to history.json")


# ===========================================================================
# Footage-path resolution + classification (shared by every render stage)
# ===========================================================================


def _classify_footage_path(path: str) -> str:
    """Return 'image', 'video', or 'other' for a footage entry key."""
    # Strip URL query strings and fragments before reading the extension —
    # Pexels image URLs look like `....jpeg?auto=compress&...` which would
    # otherwise produce a suffix of `.jpeg?auto=compress&...`.
    clean = path.split("?", 1)[0].split("#", 1)[0]
    suffix = Path(clean).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
        return "video"
    return "other"


def _is_image_path(path: str) -> bool:
    return _classify_footage_path(path) == "image"


def _resolve_to_local_path(path: str) -> str | None:
    """
    Resolve a footage entry key to an on-disk path.

    final_data entries can be keyed by:
      - a remote URL (Pexels/Wikipedia) — resolve via history.json
      - an already-local path (joint/read-out generators, or prior KB pass)

    Returns the local path if found on disk, else None.
    """
    if path.startswith(("http://", "https://")):
        history = _load_history()
        local = history.get(path)
        if local and Path(local).exists():
            return local
        return None

    return path if Path(path).exists() else None
