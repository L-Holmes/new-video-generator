"""
Retro pixellation pass for AI-generated stills (stickman / edit /
stickman-joint tiles). Wraps pixellate.pixellate_image with caching and the
in-place candidate-bundle pixellation used before review.
"""

from __future__ import annotations

# Allow running this file directly from the repo root (uv run ___visuals/pixellate_stage.py).
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import hashlib
from pathlib import Path

from ___visuals.cache_io import _is_image_path, _load_history, _resolve_to_local_path, _save_history
from CONFIG import _CACHE_DIR, DEBUG, MediaType, SearchTermData
from ___visuals.pixellate import pixellate_image


# === BEGIN verbatim move from main.py (pixellation) ===
# ===========================================================================
# PIXELLATION (RETRO LOOK FOR AI-GENERATED IMAGES)
# ===========================================================================
# Runs AFTER both review stages and BEFORE the local generators + Ken Burns.
#   - WHY after edit: an edit edits the CHOSEN preceding AI image (and
#     edits chain). Pixellate before the edit and fal redraws on top of pixels.
#     So we wait until ALL editing is done, then pixellate the results.
#   - WHY before the generators: stickman_joint tiles must be pixellated BEFORE
#     generate_joint_scenes bakes them into the collage MP4. That generator
#     reads the chosen image straight from final_data, so replacing the path
#     here is all it takes — it then tiles the pixellated version.

PIXELLATE_AI_IMAGES: bool = True

# Which MediaTypes get pixellated — the "genuinely AI-generated still" types.
# AI_STOCK covers BOTH plain scenes and grouped grid tiles (grouping is the
# `group` modifier now, not a separate type). NOTE on grouped tiles: they are
# line art on a forced-white background that the compositor keys out
# (removeBG=True). Pixellation averages colours, so near-white can drift
# slightly off-white and leave a faint fringe after keying — if that looks
# bad, gate on scene_is_grouped in pixellate_candidate_bundles.
PIXELLATE_AI_TYPES: set[MediaType] = {
    MediaType.AI_STOCK,
    MediaType.AI_EDIT_PREVIOUS,
}

# Forwarded to pixellate_image. Smaller grid = chunkier pixels.
PIXELLATE_GRID_WIDTH: int = 400
PIXELLATE_GRID_HEIGHT: int = 200
PIXELLATE_TOLERANCE: int = 80

PIXELLATE_CACHE_DIR = Path(f"{_CACHE_DIR}/pixellated")


def _pixellate_cache_path(image_path: str) -> Path:
    """Stable cache filename keyed on (image, grid, tolerance)."""
    key = f"{image_path}|{PIXELLATE_GRID_WIDTH}x{PIXELLATE_GRID_HEIGHT}|{PIXELLATE_TOLERANCE}"
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    return PIXELLATE_CACHE_DIR / f"px-{h}.png"


def _maybe_pixellate_entries(
    image_entries: list[dict],
    media_type: MediaType | None,
) -> list[dict]:
    """
    Pixellate every image path in a list of {path: trim} candidate entries,
    returning NEW entries that point at the pixellated copies. Originals are
    left untouched on disk; identity history entries are registered so the
    review GUI + stitcher resolve the new PNGs.

    Key properties:
      - No-op when pixellation is disabled OR the scene's media_type isn't a
        pixellated type — callers can pass anything and let this decide.
      - Cached + idempotent: if the px copy already exists it is REUSED (not
        re-rendered), so a HAND-EDITED px file survives re-runs, and a path
        that's already a px copy is passed straight through (no double-pixel).
    """
    if not PIXELLATE_AI_IMAGES or media_type not in PIXELLATE_AI_TYPES:
        return image_entries
    if not image_entries:
        return image_entries

    PIXELLATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    history = _load_history()
    out: list[dict] = []
    changed = False

    for entry in image_entries:
        new_entry: dict = {}
        for path, trim in entry.items():
            if not _is_image_path(path):
                new_entry[path] = trim  # videos / other → leave
                continue

            local_path = _resolve_to_local_path(path)
            if not local_path:
                print(
                    f"[pixellate] WARNING: can't resolve to disk: {path} "
                    f"— keeping original"
                )
                new_entry[path] = trim
                continue

            # Already a pixellated copy? don't pixellate the pixels again.
            if str(PIXELLATE_CACHE_DIR) in local_path:
                new_entry[path] = trim
                continue

            out_path = _pixellate_cache_path(local_path)
            if out_path.exists() and out_path.stat().st_size > 1024:
                if DEBUG:
                    print(f"  [pixellate cache hit] {out_path.name}")
            else:
                try:
                    pixellate_image(
                        input_path=local_path,
                        output_path=str(out_path),
                        target_width=PIXELLATE_GRID_WIDTH,
                        target_height=PIXELLATE_GRID_HEIGHT,
                        tolerance=PIXELLATE_TOLERANCE,
                    )
                except Exception as exc:
                    print(
                        f"[pixellate] ERROR pixellating {local_path}: {exc} "
                        f"— keeping original"
                    )
                    new_entry[path] = trim
                    continue

            history.setdefault(str(out_path), str(out_path))
            new_entry[str(out_path)] = trim
            changed = True
        out.append(new_entry)

    if changed:
        _save_history(history)
    return out


def pixellate_candidate_bundles(
    bundles: list[dict],
    script_to_search_term: dict[str, SearchTermData],
) -> None:
    """
    Pixellate the AI-generated image candidates in `bundles` IN PLACE, BEFORE
    review — so the review GUI shows (and any manual fix paints onto) the
    pixellated versions. Bundles whose scene isn't a PIXELLATE_AI_TYPES type
    (stock / wiki / Pexels-joint) pass through untouched. Idempotent.
    """
    if not PIXELLATE_AI_IMAGES:
        return

    n = 0
    for bundle in bundles:
        st = script_to_search_term.get(bundle["script_text"], {}).get("media_type")
        if st not in PIXELLATE_AI_TYPES:
            continue
        imgs = (bundle.get("candidates") or {}).get("images") or []
        if not imgs:
            continue
        bundle["candidates"]["images"] = _maybe_pixellate_entries(imgs, st)
        n += 1

    if n:
        print(f"[pixellate] pre-review: pixellated AI candidates in {n} bundle(s)")
