"""
DECORATE STAGE — pipeline adapter for the standalone decorator package
(___visuals/decorator/): every scene carrying the `decorate` modifier opens
its OWN finished footage in the ONE editor.

Editor tools (see decorator/tools.py): stamp + zoom are LIVE today (they
reuse the proven MANUAL_STOCK_PLACEMENT GUIs); draw + text are two one-line
hooks away, pending ___visuals/DECORATE_PREVIOUS.py and
___visuals/MAKE_TEXT_OVERLAY.py.

Model reminder: "zoom into the previous image" = hold_previous + decorate
(hold resolves the previous image as this scene's footage; the zoom tool
crops it) — the stay-same-plus-edit default. On any other base the editor
decorates THAT scene's image.

Runs at stage 2.645, after every stage that decides a scene's own image and
before colour grade + Ken Burns. Output is a static MP4 so KB never crops
the edits.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import re
from pathlib import Path

from ___visuals.CACHE_IO import _resolve_to_local_path
from ___visuals.CONFIG import (
    IMAGE_EXTENSIONS,
    DECORATE_OUTPUT_DIR,
    DECORATE_RENDER_SAFETY_PAD_SEC,
    SearchTermData,
    scene_wants_decorate,
)
from ___visuals.MANUAL_STOCK_PLACEMENT import extract_frame
from ___visuals.TIMING_MERGE import _load_scene_timings
from ___visuals.decorator import run_decorator


def _is_image(path: str) -> bool:
    from pathlib import Path as _P
    return _P(path).suffix.lower() in IMAGE_EXTENSIONS


def _safe_stem(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:50] or "scene"


def _scene_base_image(entry: dict, out_dir: Path, stem: str) -> str | None:
    """The scene's own footage as a local IMAGE (first frame if it's video)."""
    footage = (entry or {}).get("footage") or []
    key = next(iter(footage[0]), None) if footage else None
    local = _resolve_to_local_path(key) if key else None
    if not local:
        return None
    if _is_image(local):
        return local
    frame = str(out_dir / f"{stem}_basefrm.png")
    return extract_frame(local, frame)


def run_decorate_stage(
    final_data: list[dict],
    script_to_search_term: dict[str, SearchTermData],
) -> tuple[list[dict], dict[str, str]]:
    """Open the decorator for every scene carrying `decorate`. Returns
    (final_data, path_remap) like the other passes."""
    print("\n" + "=" * 70)
    print("[decorate] scenes carrying the decorate modifier")
    print("=" * 70)

    wanting = [txt for txt, row in script_to_search_term.items()
               if scene_wants_decorate(row)]
    if not wanting:
        print("[decorate] no scenes with the decorate modifier — skipping")
        return final_data, {}

    from ___visuals.STATIC_RENDER import _render_image_to_static_mp4  # lazy

    scene_timings = _load_scene_timings()
    final_by_text = {e["script_text"]: e for e in final_data}
    DECORATE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path_remap: dict[str, str] = {}

    for idx, txt in enumerate(wanting):
        row = script_to_search_term[txt]
        entry = final_by_text.get(txt)
        stem = f"decorate_{idx:03d}_{_safe_stem(txt)}"
        base = _scene_base_image(entry, DECORATE_OUTPUT_DIR, stem)
        if not base:
            print(f"[decorate] WARNING: no resolved footage for '{txt[:60]}' "
                  f"— leaving as-is")
            continue
        duration = float(scene_timings.get(txt, 0.0))
        if duration <= 0:
            print(f"[decorate] WARNING: no/zero timing for '{txt[:60]}' "
                  f"— leaving as-is")
            continue

        print(f"\n[decorate] [{idx + 1}/{len(wanting)}] '{txt[:60]}'")
        edited = run_decorator(
            base_image_path=base,
            out_path=str(DECORATE_OUTPUT_DIR / f"{stem}.png"),
            prefill_text=row.get("search_term", ""),
            title=f"decorate: {txt[:40]}",
        )
        if not edited:
            continue  # finished with no edits — keep the original footage

        mp4 = str(DECORATE_OUTPUT_DIR / f"{stem}.mp4")
        _render_image_to_static_mp4(
            edited, duration + DECORATE_RENDER_SAFETY_PAD_SEC, mp4)
        old_key = next(iter(entry["footage"][0]))
        entry["footage"] = [{mp4: round(duration, 3)}]
        path_remap[old_key] = mp4
        print(f"[decorate]   ✓ {Path(mp4).name} (trim {round(duration, 3)}s)")

    print(f"\n[decorate] DONE — {len(path_remap)} scene(s) decorated")
    return final_data, path_remap
