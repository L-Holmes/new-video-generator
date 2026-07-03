"""
The decorator's TOOLS. Each tool is `fn(ctx) -> new_image_path | None`
(None = tool cancelled / did nothing; ctx.current stays). Proven GUIs are
REUSED, never rewritten:

  stamp — MANUAL_STOCK_PLACEMENT's multi-stamp placement GUI, run once per
          stamp image (click to place, sizes differ, undo, done). This is
          the old "add stock to previous" machinery, generalised: any base,
          any stamp pics.
  zoom  — MANUAL_STOCK_PLACEMENT's crop/zoom GUI (the old zoom_previous).
  draw  — PENDING: point _draw_editor at the drawing editor inside
          DECORATE_PREVIOUS.py once that file is shared.
  text  — PENDING: point _text_renderer at MAKE_TEXT_OVERLAY.make_text_overlay
          once that file is shared (ctx.prefill_text is the starting text).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

from dataclasses import dataclass, field
from pathlib import Path

from ___visuals.MANUAL_STOCK_PLACEMENT import (
    composite_overlays,
    crop_and_zoom,
    place_overlays_interactive,
    zoom_prev_interactive,
)


@dataclass
class ToolContext:
    current: str                       # path of the image as edited so far
    work_dir: Path                     # where each tool writes its step file
    stamps: list[str] = field(default_factory=list)
    prefill_text: str = ""
    title: str = "decorate"
    step: int = 0

    def next_path(self, tool: str) -> str:
        self.step += 1
        return str(self.work_dir / f"step_{self.step:02d}_{tool}.png")


def tool_stamp(ctx: ToolContext) -> str | None:
    """Stamp each of ctx.stamps onto the image, one placement GUI per stamp
    (skip one by pressing its GUI's cancel). Edits chain."""
    if not ctx.stamps:
        print("[decorator] stamp: no stamp images were passed in — skipping")
        return None
    current = ctx.current
    changed = False
    for i, stamp in enumerate(ctx.stamps, start=1):
        placements = place_overlays_interactive(
            current, stamp,
            window_title=f"{ctx.title} — stamp {i}/{len(ctx.stamps)}: "
                         f"{Path(stamp).name}",
        )
        if not placements:
            print(f"[decorator]   stamp {i}: skipped")
            continue
        out = ctx.next_path("stamp")
        composite_overlays(current, stamp, placements, out)
        current = out
        changed = True
        print(f"[decorator]   stamp {i}: {len(placements)} placement(s)")
    return current if changed else None


def tool_zoom(ctx: ToolContext) -> str | None:
    box = zoom_prev_interactive(ctx.current, window_title=f"{ctx.title} — zoom")
    if not box:
        return None
    out = ctx.next_path("zoom")
    crop_and_zoom(ctx.current, box, out)
    return out


# ---------------------------------------------------------------------------
# PENDING HOOKS — wire these two lines when the files are shared:
#   from ___visuals.DECORATE_PREVIOUS import <draw editor fn>  -> _draw_editor
#   from ___visuals.MAKE_TEXT_OVERLAY import make_text_overlay -> _text_renderer
# _draw_editor(image_path, out_path) -> out_path | None
# _text_renderer(image_path, text, out_path) -> out_path | None
# ---------------------------------------------------------------------------
_draw_editor = None
_text_renderer = None


def tool_draw(ctx: ToolContext) -> str | None:
    if _draw_editor is None:
        print("[decorator] draw: NOT WIRED YET — needs DECORATE_PREVIOUS.py "
              "(see the hook at the top of decorator/tools.py)")
        return None
    return _draw_editor(ctx.current, ctx.next_path("draw"))


def tool_text(ctx: ToolContext) -> str | None:
    if _text_renderer is None:
        print("[decorator] text: NOT WIRED YET — needs MAKE_TEXT_OVERLAY.py "
              "(see the hook at the top of decorator/tools.py). starting "
              f"text would be: '{ctx.prefill_text[:50]}'")
        return None
    return _text_renderer(ctx.current, ctx.prefill_text, ctx.next_path("text"))


TOOLS: dict[str, tuple[str, callable]] = {
    "stamp": ("stamp pics onto it", tool_stamp),
    "zoom": ("zoom / crop into part of it", tool_zoom),
    "draw": ("draw on it (circles, arrows)", tool_draw),
    "text": ("big text / caption on it", tool_text),
}
