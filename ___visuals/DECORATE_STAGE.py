"""
DECORATE STAGE — the ONE interactive editor, reached via the `decorate`
modifier on ANY base media type.

Its clickable tools (this is the merge you asked for):
  draw          — circles, arrows, underlines (the old decorate_previous)
  text/caption  — big words on top; the row's search_term is offered as the
                  starting text (the old stickman_text_overlay)
  zoom          — crop / push in on part of the image (the old zoom_prev_img)

Model: the modifier applies to the scene's OWN finished footage. "Zoom into
the previous image" is therefore hold_previous + decorate (hold resolves the
previous image as this scene's footage; the editor's zoom tool crops it) —
exactly the "stay same as previous + edit it" default you described. The
same editor on a stock/wikipedia/map base decorates THAT scene's image.

Runs at stage 2.645: after every stage that decides a scene's own image
(review / ai-edit / generators / hold / manual placement), before colour
grade + Ken Burns. Output is a static MP4 so KB never crops the drawings.

STATUS — WIRING PENDING:
The editor GUI itself lives in files not shared in this session:
    ___visuals/DECORATE_PREVIOUS.py   (draw/text editor)
    ___visuals/STATIC_RENDER.py       (previous-frame + still→MP4 plumbing)
    ___visuals/MAKE_TEXT_OVERLAY.py   (the caption renderer to reuse)
plus ___visuals/MANUAL_STOCK_PLACEMENT.py (already shared — crop_and_zoom /
_SizeBox / extract_frame are the zoom + sizing building blocks).
Send those files and this stage becomes the unified editor. Until then it
prints exactly which scenes are waiting and leaves footage unchanged, so
the pipeline still runs end to end.
"""

from __future__ import annotations

# Allow `uv run ___visuals/DECORATE_STAGE.py` from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from ___visuals.CONFIG import (
    DECORATE_OUTPUT_DIR,
    DECORATE_RENDER_SAFETY_PAD_SEC,
    SearchTermData,
    scene_wants_decorate,
)


def run_decorate_stage(
    final_data: list[dict],
    script_to_search_term: dict[str, SearchTermData],
) -> tuple[list[dict], dict[str, str]]:
    """Open the decorate editor for every scene carrying the `decorate`
    modifier. Returns (final_data, path_remap) like the other passes."""
    print("\n" + "=" * 70)
    print("[decorate] scenes carrying the decorate modifier")
    print("=" * 70)

    wanting = [txt for txt, row in script_to_search_term.items()
               if scene_wants_decorate(row)]
    if not wanting:
        print("[decorate] no scenes with the decorate modifier — skipping")
        return final_data, {}

    print(f"[decorate] {len(wanting)} scene(s) want the editor "
          f"(tools: draw / text / zoom):")
    for txt in wanting:
        term = script_to_search_term[txt].get("search_term", "")
        print(f"[decorate]   '{txt[:55]}'  (caption tool prefill: '{term[:40]}')")
    print(
        "[decorate] EDITOR NOT WIRED YET — the GUI lives in "
        "DECORATE_PREVIOUS.py / STATIC_RENDER.py / MAKE_TEXT_OVERLAY.py, "
        "which weren't shared this session (see this file's docstring). "
        "Footage left unchanged."
    )
    DECORATE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _ = DECORATE_RENDER_SAFETY_PAD_SEC  # used once the editor is wired
    return final_data, {}
