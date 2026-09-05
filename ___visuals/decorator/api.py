"""
The decorator's entry point: base picture in, edited picture out — ONE
persistent window for the whole session.

The window IS the draw canvas; the sidebar swaps with the tab you're on:
  DRAW   — text / arrows / highlights / circles / lines / rectangles
  STAMP  — preview of the passed-in pictures (◀ ▶ to choose, or pick a
           file), click the image to stamp as many copies as you like,
           + / − resizes, white-background keying toggle
  ZOOM   — a live gold crop box: click/drag to move, − / + to size,
           ✓ complete zoom applies it and you carry on editing
  OBJECT — the cut-out extraction editor mounts INSIDE the same window:
           its canvas takes the image area, its controls take the sidebar,
           the tab strip stays put. Finishing there restores the decorate
           layout. An armed animated effect renders an MP4 that becomes the
           SESSION result, applied when you press FINISH — nothing closes
           or applies early.

FINISH saves; closing the window abandons (the caller keeps the original).
Captions are NOT here — they're the automatic `caption` modifier
(decorate_stage). Hand-placed text is the canvas's own Add text.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import shutil
import tempfile
from pathlib import Path

from ___visuals.previous_entry_preview import PreviousEntryPreview

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def run_decorator(
    base_image_path: str,
    out_path: str,
    stamps: list[str] | tuple = (),
    title: str = "decorate",
    tools: tuple[str, ...] = ("stamp", "zoom", "object"),
    previous_preview: PreviousEntryPreview | None = None,
    stamp_mode: bool = False,
) -> str | None:
    """Open the decorate editor on base_image_path. Returns the saved result
    path once anything changed and FINISH was pressed (normally out_path;
    with the matching video suffix instead if the session result is an
    animated MP4); None if the session ended with no edits.

    stamp_mode=True marks this as a STAMP pre-decoration session: the image
    being edited is a picked stamp that will be stamped onto the previous
    scene — the editor shows a banner saying so."""
    # Imported lazily so importing the decorator never pulls in Tk.
    from ___visuals.decorator.draw import run_editor_session

    base_image_path = str(base_image_path)
    work = Path(tempfile.mkdtemp(prefix="decorator_"))
    tabs = tuple(t for t in tools if t in ("stamp", "zoom", "object"))
    print(
        f"[decorator] editing {Path(base_image_path).name}  "
        f"(tabs: draw, {', '.join(tabs)})"
    )

    kwargs = {
        "window_title": title,
        "tabs": tabs,
        "stamps": [str(s) for s in stamps],
        "work_dir": work,
        "stamp_mode": stamp_mode,
    }
    if previous_preview is not None:
        kwargs["previous_preview"] = previous_preview
    action, result = run_editor_session(base_image_path, **kwargs)

    if action == "exit":
        print("[decorator] window closed — abandoning (footage kept)")
        return None
    if not result:
        print("[decorator] finished with no edits — keeping the original")
        return None

    out = Path(out_path)
    suffix = Path(result).suffix.lower()
    if suffix in VIDEO_EXTS and out.suffix.lower() not in VIDEO_EXTS:
        out = out.with_suffix(suffix)  # the session result is an animated MP4
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result, out)
    print(f"[decorator] ✓ saved {out}")
    return str(out)


def run_overlay_decorator(
    base_image_path: str,
    stamps: list[str] | tuple = (),
    title: str = "decorate (LIVE video)",
    previous_preview: PreviousEntryPreview | None = None,
):
    """Open the decorate editor in LIVE-VIDEO overlay mode: the base is a
    frame of the PLAYING footage (extract it at the moment the scene starts,
    with any earlier chain ops applied), the draw + stamp + zoom tools are
    offered (stamp opens pre-loaded and ACTIVE when `stamps` are passed),
    and NOTHING is baked — what comes back is the ordered ops recipe
        [("layer", [deco items...]) | ("zoom", (wpct, cx, cy)), ...]
    for the caller to re-apply to the moving video: layers as transparent
    PNGs (draw.render_overlay_layer / draw.render_highlight_mask), zooms as
    real crops of the footage (video_chains.burn_ops_onto_segment).

    Returns the ops list, or None if the window was closed or nothing was
    placed."""
    from ___visuals.decorator.draw import run_editor_session

    work = Path(tempfile.mkdtemp(prefix="decorator_ov_"))
    print(
        f"[decorator] LIVE overlay editing {Path(str(base_image_path)).name}"
        f"  (tabs: draw, stamp, zoom — over playing footage"
        f"{f'; {len(stamps)} stamp(s) ready' if stamps else ''})"
    )
    kwargs = {
        "window_title": title,
        "tabs": ("stamp", "zoom"),
        "stamps": [str(s) for s in stamps],
        "work_dir": work,
        "overlay_mode": True,
    }
    if previous_preview is not None:
        kwargs["previous_preview"] = previous_preview
    action, ops = run_editor_session(str(base_image_path), **kwargs)
    if action == "exit":
        print("[decorator] window closed — abandoning (footage kept)")
        return None
    if not ops:
        print("[decorator] finished with no edits — nothing to layer")
        return None
    n_layers = sum(1 for k, _ in ops if k == "layer")
    n_zooms = sum(1 for k, _ in ops if k == "zoom")
    print(
        f"[decorator] ✓ {n_layers} layer(s) + {n_zooms} zoom(s) to apply to the video"
    )
    return ops


def _main() -> None:
    """Direct run — the ONE editor window (draw canvas + tab sidebar):

    uv run ___visuals/decorator/api.py PIC.png
    uv run ___visuals/decorator/api.py PIC.png --stamps coin.png jar.png
    uv run ___visuals/decorator/api.py PIC.png --out edited.png
    """
    import argparse

    ap = argparse.ArgumentParser(description=_main.__doc__)
    ap.add_argument("base", help="the picture to start with")
    ap.add_argument(
        "--stamps", nargs="*", default=[], help="pictures offered by the stamp tab"
    )
    ap.add_argument(
        "--out", default=None, help="output path (default: <base>_decorated.png)"
    )
    ap.add_argument(
        "--tabs",
        nargs="*",
        default=["stamp", "zoom", "object"],
        help="which tool tabs to offer besides draw",
    )
    args = ap.parse_args()

    base = Path(args.base)
    out = args.out or str(base.with_name(base.stem + "_decorated.png"))
    result = run_decorator(
        str(base),
        out,
        stamps=args.stamps,
        tools=tuple(args.tabs),
        title=f"decorate: {base.name}",
    )
    if result is None:
        print("[decorator] no edits — nothing saved")


if __name__ == "__main__":
    _main()
