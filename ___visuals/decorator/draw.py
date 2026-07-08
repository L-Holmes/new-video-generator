"""
decorator/draw.py — the decorate editor's DRAW CANVAS and its HUB window.
==========================================================================
This is the window the decorator opens STRAIGHT INTO: the draw canvas
(text boxes, arrows, highlights, circles, lines, rectangles) with a TAB
STRIP along the top for the other tools — stamp, zoom, object — switched
by clicking a tab or with Ctrl+Left / Ctrl+Right. Picking a tab bakes the
items drawn so far and hands over to that tool; when it finishes you land
back here. FINISH ends the whole session.

Tools (the side-panel "toolbar" — more can be slotted in later):

  • ADD TEXT
      "Add text" → type → the text follows the cursor immediately (auto-ready
      when >0 non-whitespace chars) → click to drop. Resize with + / −.
      "Edit text" re-opens the box (works after placing too; size retained).

  • ADD ARROW
      "Add arrow" → spin the DIAL to aim (drag the needle, or ←/→ keys) →
      resize with + / − → click to drop. Direction + size are retained.

  • ADD HIGHLIGHT
      "Add highlight" → press-drag-release a box over the area to highlight.
      The boxed pixels are brightened slightly and everything else is darkened
      subtly (a soft spotlight). No size control — the box defines it.

  • ADD CIRCLE
      "Add circle" → click the CENTRE → move the mouse to stretch the radius
      (live ring preview) → click / Enter to confirm. A thin outline ring.

  • ADD LINE  (an underline is just a point-to-point line)
      "Add line" → click the START point → click / Enter at the END point.
      A thin straight line in the same pixellated style.

  • ADD RECTANGLE
      "Add rectangle" → click the CENTRE → a long direction guide appears
      (default horizontal); move to aim it, click / Enter to lock the angle →
      move the mouse to stretch the WIDTH × HEIGHT → click / Enter to confirm.
      A thin outline box.

  • FINISH EDITS AND MOVE ON
      Bakes every item onto the base and returns the placements.

Text reuses the SAME pixel font as STICKMAN_TEXT_OVERLAY / WORDS_ON_SCREEN;
arrows + the new shapes are pixelated to match. Everything bakes to a static
MP4 in the pipeline so the Ken Burns pass leaves the decorated frame untouched.

Public API
----------
decorate_prev_interactive(base, window_title=..., initial=None)
    -> list[TextDeco | ArrowDeco | HighlightDeco
             | CircleDeco | LineDeco | RectDeco] | None    # None == EXIT
composite_text_decorations(base, items, output_path) -> str          # PNG
make_decorated_clip(base, items, output_path, duration, fps=...) -> str  # MP4
render_text_image / render_arrow_image -> PIL.Image (RGBA)
render_circle_image / render_line_image / render_rect_image -> PIL.Image (RGBA)
dump_decos(items) -> list[dict]   /   load_decos(raw) -> list[...]   # resume

Standalone test
---------------
    python DECORATE_PREVIOUS.py BASE                 # decorate -> PNG
    python DECORATE_PREVIOUS.py BASE --duration 5    # decorate -> static MP4

uv run DECORATE_PREVIOUS.py some_image.png --duration 5.
uv run DECORATE_PREVIOUS.py stickman-CACHE/stock_footage/wiki-img-e33fdcc1b657.jpg
"""

from __future__ import annotations

# Allow `uv run ___visuals/decorator/draw.py` from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import argparse
import math
import subprocess
import sys
import tempfile
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageTk

# Reuse the display-fit + frame-extract helpers + Pillow resample shim from the
# sibling manual stage so the two GUIs behave identically.
from ___visuals.MANUAL_STOCK_PLACEMENT import _RESAMPLE, _fit_display, extract_frame
from ___visuals.PREVIOUS_ENTRY_PREVIEW import (
    PreviousEntryPreview,
    PreviousEntryPreviewPopup,
)

# Reuse the EXACT font discovery + pixelation from the words-on-screen renderer
# so decorations match STICKMAN_TEXT_OVERLAY. Fallbacks keep this file usable
# on its own (no circular import: WORDS_ON_SCREEN doesn't import this module).
try:
    from ___visuals.WORDS_ON_SCREEN import _find_font, _find_pixel_font, _pixelate_image
except Exception:  # pragma: no cover

    def _find_pixel_font():
        return None

    def _find_font():
        for p in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ):
            if Path(p).exists():
                return p
        raise RuntimeError("No font found; install DejaVu or set a font path.")

    def _pixelate_image(img, block: int):
        if block <= 1:
            return img
        w, h = img.size
        small = img.resize((max(1, w // block), max(1, h // block)), Image.BOX)
        return small.resize((w, h), Image.NEAREST)


# ===========================================================================
# CONFIG
# ===========================================================================

FPS: int = 30

# Shared look (no red card / tilt — white glyphs/shapes with a dark outline +
# drop shadow so they read on any background).
TEXT_COLOR: tuple[int, int, int] = (245, 245, 245)  # near-white
OUTLINE_COLOR: tuple[int, int, int] = (18, 18, 22)  # dark edge
SHADOW_COLOR: tuple[int, int, int] = (0, 0, 0)
SHADOW_ALPHA: int = 150  # 0 = off … 255

# --- text ---
TEXT_STROKE_FRAC: float = 0.06  # outline thickness, ×font_size
TEXT_SHADOW_OFFSET_FRAC: float = 0.06  # drop-shadow offset, ×font_size
PIXEL_BLOCK_WHEN_NO_PIXEL_FONT: int = 2  # fallback chunk size (was 3 → less pixelated)
DEFAULT_FONT_FRAC: float = 0.07  # default glyph height ≈ 7% of base height
FONT_STEP_PX: int = 6
MIN_FONT_PX: int = 8
MAX_FONT_PX: int = 1000

# --- arrow ---
ARROW_SHAFT_FRAC: float = 0.18  # shaft thickness, ×length
ARROW_HEAD_LEN_FRAC: float = 0.42  # head length, ×length
ARROW_HEAD_H_FRAC: float = 0.46  # head width, ×length
ARROW_STROKE_FRAC: float = 0.020  # outline thickness, ×length
ARROW_SHADOW_FRAC: float = 0.022  # shadow offset, ×length
ARROW_PIXEL_DIVS: int = 22  # ~ number of pixel blocks along the length
DEFAULT_ARROW_FRAC: float = 0.18  # default length ≈ 18% of base width
ARROW_STEP_PX: int = 12
ARROW_ANGLE_STEP: float = 10.0  # degrees per ←/→ key press
MIN_ARROW_PX: int = 16
MAX_ARROW_PX: int = 1500

# --- highlight ---
HIGHLIGHT_BRIGHTEN: float = 1.16  # brightness ×factor INSIDE the box (slight)
HIGHLIGHT_DARKEN: float = 0.84  # brightness ×factor OUTSIDE (subtle)
HIGHLIGHT_FEATHER_FRAC: float = 0.012  # soft edge radius, ×min(base w, h)

# --- line / underline (point-to-point, REALLY thin — like the circle ring) ---
LINE_THICK_FRAC: float = 0.0055  # white core thickness, ×length (very thin)
LINE_OUTLINE_FRAC: float = 0.34  # dark edge thickness, ×core thickness
LINE_SHADOW_FRAC: float = 0.010  # shadow offset, ×length
MIN_LINE_PX: int = 8
MAX_LINE_PX: int = 2000

# --- circle (outline ring) ---
CIRCLE_THICK_FRAC: float = 0.045  # white ring thickness, ×radius (thin)
CIRCLE_OUTLINE_FRAC: float = 0.34  # dark edge thickness, ×ring thickness
CIRCLE_SHADOW_FRAC: float = 0.050  # shadow offset, ×radius
MIN_CIRCLE_PX: int = 8
MAX_CIRCLE_PX: int = 3000

# --- rectangle (outline border) ---
RECT_THICK_FRAC: float = 0.025  # white border thickness, ×min(w,h) (thin)
RECT_OUTLINE_FRAC: float = 0.34  # dark edge thickness, ×border thickness
RECT_SHADOW_FRAC: float = 0.018  # shadow offset, ×min(w,h)
MIN_RECT_PX: int = 10
MAX_RECT_PX: int = 4000

# --- shape stroke thickness (used for circle, line, rect + / - buttons) ---
DEFAULT_THICKNESS: int = 2
MIN_THICKNESS: int = 1
MAX_THICKNESS: int = 60
THICKNESS_STEP_PX: int = 2  # increment step for shape thickness

# Subtle pixelation for the new shapes (matches the gentle text/arrow look).
SHAPE_PIXEL_DIVS: int = 40  # ~ blocks along the longest dimension
SHAPE_PIXEL_MAX: int = 2  # hard cap so the effect stays "slight"

GHOST_ALPHA: int = 150  # opacity of the live "drop me here" ghost

# Slight nudging of the last-placed item (arrow keys + on-screen d-pad).
NUDGE_FRAC: float = 0.004  # fraction of the dimension per press (~slight)
NUDGE_FRAC_BIG: float = 0.020  # Shift+arrow = a bigger step

# Custom on-canvas cursor (thin neutral ring + small pinpoint dot).
CURSOR_RADIUS: int = 12
CURSOR_DOT_R: int = 2
CURSOR_RING_COLOR: str = "#d7dde4"  # soft off-white (was bright cyan)
CURSOR_RING_BG_COLOR: str = "#10131a"  # thin dark backing for contrast
CURSOR_DOT_COLOR: str = "#d7dde4"

# Live (vector) draw-preview accents.
DRAW_PREVIEW_COLOR: str = "#7CFC00"  # green, matches the active-item outline
DRAW_GUIDE_COLOR: str = "#5ad1ff"  # cyan, matches the dial

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


# ===========================================================================
# Data
# ===========================================================================


@dataclass(eq=False)  # eq=False → identity semantics (so `x in list` is `is`)
class TextDeco:
    """One text decoration, relative to the BASE image."""

    text: str
    font_size: int  # glyph height in BASE-image px
    cx_frac: float  # center x as a fraction of base width, 0..1
    cy_frac: float  # center y as a fraction of base height, 0..1
    angle: float = 0.0  # degrees, 0 = upright, +clockwise (screen)


@dataclass(eq=False)
class StampDeco:
    """A picture stamped onto the base — the old manual stock placement,
    now a native item: click to drop, + / − to resize, nudge, undo."""

    path: str  # the stamp image file
    width: int  # stamp width in BASE-image px (aspect kept)
    cx_frac: float
    cy_frac: float
    remove_bg: bool = False  # key out a near-white background first


_STAMP_SRC_CACHE: dict = {}


def _load_stamp_rgba(path: str, remove_bg: bool) -> "Image.Image":
    key = (path, bool(remove_bg))
    im = _STAMP_SRC_CACHE.get(key)
    if im is None:
        im = Image.open(path).convert("RGBA")
        if remove_bg:
            try:  # lazy: the white-keyer lives with the placement helpers
                from ___visuals.MANUAL_STOCK_PLACEMENT import remove_white_background

                im = remove_white_background(im)
            except Exception as exc:
                print(f"[draw] stamp remove_bg failed ({exc}) — using as-is")
        _STAMP_SRC_CACHE[key] = im
    return im


def render_stamp_image(path: str, width: int, remove_bg: bool) -> "Image.Image":
    src_im = _load_stamp_rgba(path, remove_bg)
    w = max(8, int(width))
    h = max(1, round(w * src_im.height / src_im.width))
    return src_im.resize((w, h), _RESAMPLE)


@dataclass(eq=False)
class ArrowDeco:
    """One arrow decoration, relative to the BASE image."""

    length: int  # arrow length in BASE-image px
    angle: float  # degrees, 0 = pointing right, +clockwise (screen)
    cx_frac: float
    cy_frac: float


@dataclass(eq=False)
class HighlightDeco:
    """A rectangular highlight region. Stored exactly like RectDeco (width,
    height, angle, centre) so it uses the same draw-click flow."""

    width: int  # along the direction axis, BASE-image px
    height: int  # perpendicular, BASE-image px
    angle: float  # degrees, 0 = width is horizontal, +clockwise
    cx_frac: float
    cy_frac: float


@dataclass(eq=False)
class CircleDeco:
    """A thin outline circle, relative to the BASE image. Drawn centered like a
    sprite, so it moves / resizes with the shared machinery."""

    radius: int  # ring radius in BASE-image px
    cx_frac: float
    cy_frac: float
    thickness: int = DEFAULT_THICKNESS  # ring thickness in BASE-image px


@dataclass(eq=False)
class LineDeco:
    """A straight line / underline. Stored like an arrow (center + length +
    angle) so it slots into the same sprite machinery; the two click endpoints
    reconstruct exactly from the midpoint + length + angle."""

    length: int  # line length in BASE-image px
    angle: float  # degrees, 0 = horizontal, +clockwise (screen)
    cx_frac: float  # midpoint x
    cy_frac: float  # midpoint y
    thickness: int = DEFAULT_THICKNESS  # line thickness in BASE-image px


@dataclass(eq=False)
class RectDeco:
    """A thin outline rectangle, relative to the BASE image. `width` runs along
    the chosen direction axis; `angle` rotates the whole box (screen, +cw)."""

    width: int  # along the direction axis, BASE-image px
    height: int  # perpendicular, BASE-image px
    angle: float  # degrees, 0 = width is horizontal, +clockwise
    cx_frac: float
    cy_frac: float
    thickness: int = DEFAULT_THICKNESS  # border thickness in BASE-image px


# ===========================================================================
# (De)serialisation for resume — discriminated by a "type" field.
# ===========================================================================


def deco_to_dict(d) -> dict:
    if isinstance(d, StampDeco):
        return {
            "kind": "stamp",
            "path": d.path,
            "width": d.width,
            "cx_frac": d.cx_frac,
            "cy_frac": d.cy_frac,
            "remove_bg": d.remove_bg,
        }
    if isinstance(d, ArrowDeco):
        return {
            "type": "arrow",
            "length": d.length,
            "angle": d.angle,
            "cx_frac": d.cx_frac,
            "cy_frac": d.cy_frac,
        }
    if isinstance(d, HighlightDeco):
        return {
            "type": "highlight",
            "width": d.width,
            "height": d.height,
            "angle": d.angle,
            "cx_frac": d.cx_frac,
            "cy_frac": d.cy_frac,
        }
    if isinstance(d, CircleDeco):
        return {
            "type": "circle",
            "radius": d.radius,
            "cx_frac": d.cx_frac,
            "cy_frac": d.cy_frac,
            "thickness": d.thickness,
        }
    if isinstance(d, RectDeco):
        return {
            "type": "rect",
            "width": d.width,
            "height": d.height,
            "angle": d.angle,
            "cx_frac": d.cx_frac,
            "cy_frac": d.cy_frac,
            "thickness": d.thickness,
        }
    if isinstance(d, LineDeco):
        return {
            "type": "line",
            "length": d.length,
            "angle": d.angle,
            "cx_frac": d.cx_frac,
            "cy_frac": d.cy_frac,
            "thickness": d.thickness,
        }
    return {
        "type": "text",
        "text": d.text,
        "font_size": d.font_size,
        "cx_frac": d.cx_frac,
        "cy_frac": d.cy_frac,
        "angle": d.angle,
    }


def deco_from_dict(r: dict):
    if r.get("kind") == "stamp":
        return StampDeco(
            r["path"],
            int(r["width"]),
            float(r["cx_frac"]),
            float(r["cy_frac"]),
            bool(r.get("remove_bg", False)),
        )
    t = r.get("type")
    if t == "highlight" or (
        "width" in r and "height" in r and "angle" in r and "x0_frac" not in r
    ):
        return HighlightDeco(
            int(r["width"]),
            int(r["height"]),
            float(r["angle"]),
            float(r["cx_frac"]),
            float(r["cy_frac"]),
        )
    if t == "circle" or ("radius" in r):
        return CircleDeco(
            int(r["radius"]),
            float(r["cx_frac"]),
            float(r["cy_frac"]),
            int(r.get("thickness", DEFAULT_THICKNESS)),
        )
    if t == "rect" or ("width" in r and "height" in r):
        return RectDeco(
            int(r["width"]),
            int(r["height"]),
            float(r["angle"]),
            float(r["cx_frac"]),
            float(r["cy_frac"]),
            int(r.get("thickness", DEFAULT_THICKNESS)),
        )
    # Line is stored like an arrow (length+angle); disambiguate by "type" FIRST.
    if t == "line":
        return LineDeco(
            int(r["length"]),
            float(r["angle"]),
            float(r["cx_frac"]),
            float(r["cy_frac"]),
            int(r.get("thickness", DEFAULT_THICKNESS)),
        )
    if t == "arrow" or ("length" in r and "text" not in r):
        return ArrowDeco(
            int(r["length"]),
            float(r["angle"]),
            float(r["cx_frac"]),
            float(r["cy_frac"]),
        )
    if t == "text" or ("text" in r and "length" not in r):
        return TextDeco(
            r["text"],
            int(r["font_size"]),
            float(r["cx_frac"]),
            float(r["cy_frac"]),
            float(r.get("angle", 0.0)),
        )
    # Backward-compatible: old saved files were bare text dicts (no "type").
    return TextDeco(
        r["text"],
        int(r["font_size"]),
        float(r["cx_frac"]),
        float(r["cy_frac"]),
        float(r.get("angle", 0.0)),
    )


def dump_decos(items) -> list[dict]:
    return [deco_to_dict(d) for d in items]


def load_decos(raw) -> list:
    return [deco_from_dict(r) for r in raw]


# ===========================================================================
# Font + shared helpers
# ===========================================================================


def _resolve_font() -> tuple[str, bool]:
    """(font_path, is_pixel_font) — prefer the pixel font, else a normal one."""
    pf = _find_pixel_font()
    if pf:
        return pf, True
    return _find_font(), False


def _effective_block(is_pixel: bool, override: int | None) -> int:
    if override is not None:
        return max(1, override)
    return 1 if is_pixel else max(1, PIXEL_BLOCK_WHEN_NO_PIXEL_FONT)


def _shape_block(longest: int, thickness: int, divs: int = SHAPE_PIXEL_DIVS) -> int:
    """A gentle pixelation block size for the line / circle / rect shapes.

    Scales with the shape but is capped (SHAPE_PIXEL_MAX) so the effect stays
    *slight*, and never exceeds half the stroke thickness — that keeps thin
    strokes from pixelating away to nothing. Very thin strokes get no
    pixelation at all (block = 1)."""
    if thickness < 4:
        return 1
    block = max(1, round(longest / max(1, divs)))
    block = min(block, SHAPE_PIXEL_MAX)
    block = min(block, max(1, thickness // 2))
    return block


# ===========================================================================
# Text rendering
# ===========================================================================


def render_text_image(
    text: str,
    font_size: int,
    *,
    pixel_block: int | None = None,
    color: tuple[int, int, int] = TEXT_COLOR,
    shadow: bool = True,
    outline: bool = True,
) -> Image.Image:
    """Render `text` as a transparent RGBA image: pixel-font white glyphs with
    an optional dark outline + drop shadow."""
    text = (text or "").strip() or " "
    font_path, is_pixel = _resolve_font()
    fs = max(MIN_FONT_PX, int(font_size))
    font = ImageFont.truetype(font_path, fs)
    block = _effective_block(is_pixel, pixel_block)

    stroke = int(TEXT_STROKE_FRAC * fs) if outline else 0
    so = int(TEXT_SHADOW_OFFSET_FRAC * fs) if shadow else 0

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    l, t, r, b = probe.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw, th = r - l, b - t

    margin = stroke + so + block + 6
    img = Image.new("RGBA", (tw + 2 * margin, th + 2 * margin), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ox, oy = margin - l, margin - t

    if is_pixel and block <= 1:
        try:
            d.fontmode = "1"
        except Exception:
            pass

    if shadow and SHADOW_ALPHA > 0:
        d.text(
            (ox + so, oy + so),
            text,
            font=font,
            fill=SHADOW_COLOR + (SHADOW_ALPHA,),
            stroke_width=stroke,
            stroke_fill=SHADOW_COLOR + (SHADOW_ALPHA,),
        )

    if stroke > 0:
        d.text(
            (ox, oy),
            text,
            font=font,
            fill=tuple(color) + (255,),
            stroke_width=stroke,
            stroke_fill=OUTLINE_COLOR + (255,),
        )
    else:
        d.text((ox, oy), text, font=font, fill=tuple(color) + (255,))

    if block > 1:
        img = _pixelate_image(img.convert("RGBA"), block)
    return img


# ===========================================================================
# Arrow rendering (pixelated, to match the text look)
# ===========================================================================


def render_arrow_image(
    length: int,
    angle_deg: float,
    *,
    pixel_block: int | None = None,
    color: tuple[int, int, int] = TEXT_COLOR,
    shadow: bool = True,
    outline: bool = True,
) -> Image.Image:
    """Render a pixelated arrow (white, dark outline + shadow) of `length` px,
    rotated to point at `angle_deg` (0 = right, +clockwise in screen coords)."""
    L = max(MIN_ARROW_PX, int(length))
    shaft_h = max(2, round(L * ARROW_SHAFT_FRAC))
    head_h = max(4, round(L * ARROW_HEAD_H_FRAC))
    head_len = max(2, round(L * ARROW_HEAD_LEN_FRAC))
    block = (
        max(1, pixel_block)
        if pixel_block is not None
        else max(1, round(L / ARROW_PIXEL_DIVS))
    )
    stroke = max(1, round(L * ARROW_STROKE_FRAC)) if outline else 0
    so = max(1, round(L * ARROW_SHADOW_FRAC)) if shadow else 0

    margin = stroke + so + block + 4
    W = L + 2 * margin
    H = head_h + 2 * margin
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    x0 = margin
    cy = margin + head_h / 2
    pts = [
        (x0, cy - shaft_h / 2),
        (x0 + (L - head_len), cy - shaft_h / 2),
        (x0 + (L - head_len), cy - head_h / 2),
        (x0 + L, cy),
        (x0 + (L - head_len), cy + head_h / 2),
        (x0 + (L - head_len), cy + shaft_h / 2),
        (x0, cy + shaft_h / 2),
    ]

    if shadow and SHADOW_ALPHA > 0:
        d.polygon(
            [(px + so, py + so) for px, py in pts], fill=SHADOW_COLOR + (SHADOW_ALPHA,)
        )
    try:
        if stroke > 0:
            d.polygon(
                pts,
                fill=tuple(color) + (255,),
                outline=OUTLINE_COLOR + (255,),
                width=stroke,
            )
        else:
            d.polygon(pts, fill=tuple(color) + (255,))
    except TypeError:  # very old Pillow: no polygon width=
        d.polygon(pts, fill=tuple(color) + (255,), outline=OUTLINE_COLOR + (255,))

    if block > 1:
        img = _pixelate_image(img.convert("RGBA"), block)

    if angle_deg:
        img = img.rotate(
            -float(angle_deg),
            expand=True,
            resample=Image.NEAREST if block > 1 else Image.BICUBIC,
        )
    return img


# ===========================================================================
# Line / circle / rectangle rendering (thin, gently pixelated to match)
# ===========================================================================


def render_line_image(
    length: int,
    angle_deg: float,
    thickness: int = DEFAULT_THICKNESS,
    *,
    pixel_block: int | None = None,
    color: tuple[int, int, int] = TEXT_COLOR,
    shadow: bool = True,
    outline: bool = True,
) -> Image.Image:
    """Render a thin, slightly-pixelated line (white core, dark edge + shadow)
    of `length` px, rotated to `angle_deg` (0 = horizontal, +clockwise)."""
    L = max(MIN_LINE_PX, int(length))
    core = max(2, int(thickness))
    stroke = max(1, round(core * LINE_OUTLINE_FRAC)) if outline else 0
    so = max(1, round(core * 0.75)) if shadow else 0
    full_h = core + 2 * stroke
    block = max(1, pixel_block) if pixel_block is not None else _shape_block(L, full_h)

    margin = stroke + so + block + 4
    W = L + 2 * margin
    H = full_h + 2 * margin
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    x0 = margin
    x1 = margin + L
    cy = margin + full_h / 2

    def _bar(half_h, fill, dx=0, dy=0):
        d.rectangle([x0 + dx, cy - half_h + dy, x1 + dx, cy + half_h + dy], fill=fill)

    if shadow and SHADOW_ALPHA > 0:
        _bar(full_h / 2, SHADOW_COLOR + (SHADOW_ALPHA,), so, so)
    if stroke > 0:
        _bar(core / 2 + stroke, OUTLINE_COLOR + (255,))  # dark edge band
    _bar(core / 2, tuple(color) + (255,))  # white core

    if block > 1:
        img = _pixelate_image(img.convert("RGBA"), block)
    if angle_deg:
        img = img.rotate(
            -float(angle_deg),
            expand=True,
            resample=Image.NEAREST if block > 1 else Image.BICUBIC,
        )
    return img


def render_circle_image(
    radius: int,
    thickness: int = DEFAULT_THICKNESS,
    *,
    pixel_block: int | None = None,
    color: tuple[int, int, int] = TEXT_COLOR,
    shadow: bool = True,
    outline: bool = True,
) -> Image.Image:
    """Render a thin, slightly-pixelated outline ring (white core with a dark
    edge + shadow) of the given `radius` px."""
    R = max(MIN_CIRCLE_PX, int(radius))
    core = max(2, int(thickness))
    stroke = max(1, round(core * CIRCLE_OUTLINE_FRAC)) if outline else 0
    so = max(1, round(core * 0.75)) if shadow else 0
    band = core + 2 * stroke
    block = (
        max(1, pixel_block) if pixel_block is not None else _shape_block(2 * R, band)
    )

    margin = so + block + 4 + band
    size = 2 * R + 2 * margin
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = margin + R

    def _ring(rad, w, fill, dx=0, dy=0):
        bb = [cx - rad + dx, cy - rad + dy, cx + rad + dx, cy + rad + dy]
        try:
            d.ellipse(bb, outline=fill, width=max(1, int(w)))
        except TypeError:  # ancient Pillow: no width=
            d.ellipse(bb, outline=fill)

    if shadow and SHADOW_ALPHA > 0:
        _ring(R, band, SHADOW_COLOR + (SHADOW_ALPHA,), so, so)
    if stroke > 0:
        _ring(R, band, OUTLINE_COLOR + (255,))  # dark band (both edges)
    _ring(R - stroke, core, tuple(color) + (255,))  # white core, inset

    if block > 1:
        img = _pixelate_image(img.convert("RGBA"), block)
    return img


def render_rect_image(
    width: int,
    height: int,
    angle_deg: float,
    thickness: int = DEFAULT_THICKNESS,
    *,
    pixel_block: int | None = None,
    color: tuple[int, int, int] = TEXT_COLOR,
    shadow: bool = True,
    outline: bool = True,
) -> Image.Image:
    """Render a thin, slightly-pixelated outline rectangle (white border with a
    dark edge + shadow) of `width` × `height` px, rotated to `angle_deg`
    (0 = width horizontal, +clockwise)."""
    W0 = max(MIN_RECT_PX, int(width))
    H0 = max(MIN_RECT_PX, int(height))
    core = max(2, int(thickness))
    stroke = max(1, round(core * RECT_OUTLINE_FRAC)) if outline else 0
    so = max(1, round(core * 0.75)) if shadow else 0
    band = core + 2 * stroke
    block = (
        max(1, pixel_block)
        if pixel_block is not None
        else _shape_block(max(W0, H0), band)
    )

    margin = so + block + 4 + band
    cW = W0 + 2 * margin
    cH = H0 + 2 * margin
    img = Image.new("RGBA", (cW, cH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y0 = margin, margin
    x1, y1 = margin + W0, margin + H0

    def _hollow(cx0, cy0, cx1, cy1, c, s, c_fill, s_fill):
        """Draw a hollow rectangle using 4 solid bars to prevent edge overlap."""
        if cx1 <= cx0 or cy1 <= cy0:
            return
        if s > 0:
            d.rectangle([cx0, cy0, cx1, cy0 + s], fill=s_fill)
            d.rectangle([cx0, cy1 - s, cx1, cy1], fill=s_fill)
            d.rectangle([cx0, cy0, cx0 + s, cy1], fill=s_fill)
            d.rectangle([cx1 - s, cy0, cx1, cy1], fill=s_fill)
        if c > 0:
            ix0, iy0 = cx0 + s, cy0 + s
            ix1, iy1 = cx1 - s, cy1 - s
            if ix0 > ix1:
                ix0, ix1 = ix1, ix0
            if iy0 > iy1:
                iy0, iy1 = iy1, iy0
            d.rectangle([ix0, iy0, ix1, iy0 + c], fill=c_fill)
            d.rectangle([ix0, iy1 - c, ix1, iy1], fill=c_fill)
            d.rectangle([ix0, iy0, ix0 + c, iy1], fill=c_fill)
            d.rectangle([ix1 - c, iy0, ix1, iy1], fill=c_fill)

    if shadow and SHADOW_ALPHA > 0:
        _hollow(
            x0 + so,
            y0 + so,
            x1 + so,
            y1 + so,
            core,
            stroke,
            SHADOW_COLOR + (SHADOW_ALPHA,),
            SHADOW_COLOR + (SHADOW_ALPHA,),
        )
    if stroke > 0:
        _hollow(
            x0, y0, x1, y1, core, stroke, tuple(color) + (255,), OUTLINE_COLOR + (255,)
        )
    else:
        _hollow(x0, y0, x1, y1, core, 0, tuple(color) + (255,), tuple(color) + (255,))

    if block > 1:
        img = _pixelate_image(img.convert("RGBA"), block)
    if angle_deg:
        img = img.rotate(
            -float(angle_deg),
            expand=True,
            resample=Image.NEAREST if block > 1 else Image.BICUBIC,
        )
    return img


def _render_item(item) -> Image.Image:
    """Render a SPRITE decoration (text/arrow/circle/line/rect) at BASE
    resolution. Highlights are not sprites — they're applied to the base by
    _apply_highlights."""
    if isinstance(item, StampDeco):
        return render_stamp_image(item.path, item.width, item.remove_bg)
    if isinstance(item, ArrowDeco):
        return render_arrow_image(item.length, item.angle)
    if isinstance(item, CircleDeco):
        return render_circle_image(item.radius, item.thickness)
    if isinstance(item, LineDeco):
        return render_line_image(item.length, item.angle, item.thickness)
    if isinstance(item, RectDeco):
        return render_rect_image(item.width, item.height, item.angle, item.thickness)
    txt = render_text_image(item.text, item.font_size)
    if getattr(item, "angle", 0.0):
        # negative: PIL rotate is CCW, but the dial angle is clockwise-positive
        # (screen coords) — match the dial so the text turns the SAME way it's aimed.
        txt = txt.rotate(-item.angle, resample=Image.BICUBIC, expand=True)
    return txt


# ===========================================================================
# Highlight rendering (brighten inside the boxes, darken outside)
# ===========================================================================


def _highlight_mask(highlights, size: tuple[int, int]) -> Image.Image:
    """The feathered grayscale mask (white INSIDE the boxes) for a set of
    highlight decorations at `size`. Shared by _apply_highlights (the still /
    preview path) and render_highlight_mask (the burn-over-video path), so
    the two are geometry-identical by construction."""
    w, h = size
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    for hl in highlights:
        cx, cy = hl.cx_frac * w, hl.cy_frac * h
        ca, sa = math.cos(math.radians(hl.angle)), math.sin(math.radians(hl.angle))
        hw, hh = hl.width / 2, hl.height / 2
        pts = []
        for ux, uy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            lx, ly = ux * hw, uy * hh
            pts += [(cx + lx * ca - ly * sa, cy + lx * sa + ly * ca)]
        md.polygon(pts, fill=255)

    feather = max(1, round(min(w, h) * HIGHLIGHT_FEATHER_FRAC))
    return mask.filter(ImageFilter.GaussianBlur(feather))


def _apply_highlights(rgb: Image.Image, highlights) -> Image.Image:
    """Return a copy of `rgb` with the union of (potentially rotated) highlight
    boxes brightened and everything else darkened, with a soft feathered edge."""
    bright = ImageEnhance.Brightness(rgb).enhance(HIGHLIGHT_BRIGHTEN)
    dark = ImageEnhance.Brightness(rgb).enhance(HIGHLIGHT_DARKEN)
    mask = _highlight_mask(highlights, rgb.size)
    return Image.composite(bright, dark, mask)  # white(255)=bright, black=dark


# ===========================================================================
# Non-GUI helpers
# ===========================================================================


def _load_base_image(path: str) -> Image.Image:
    """Load the base as RGBA, extracting a frame first if it's a video."""
    p = str(path)
    if Path(p.split("?", 1)[0]).suffix.lower() in VIDEO_EXTS:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        try:
            extract_frame(p, tmp.name)
            return Image.open(tmp.name).convert("RGBA")
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return Image.open(p).convert("RGBA")


def composite_text_decorations(base_image_path: str, items, output_path: str) -> str:
    """Full-resolution composite. Highlights modify the base first (brighten
    inside / darken outside); sprites (text/arrows/circles/lines/rects) are
    then drawn on top. Saves a PNG."""
    base = _load_base_image(base_image_path)
    bw, bh = base.size

    highlights = [it for it in items if isinstance(it, HighlightDeco)]
    sprites = [it for it in items if not isinstance(it, HighlightDeco)]

    if highlights:
        base = _apply_highlights(base.convert("RGB"), highlights).convert("RGBA")

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    for it in sprites:
        im = _render_item(it)
        cx, cy = it.cx_frac * bw, it.cy_frac * bh
        layer.alpha_composite(im, (round(cx - im.width / 2), round(cy - im.height / 2)))

    out = Image.alpha_composite(base, layer).convert("RGB")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    out.save(output_path)
    return output_path


def render_overlay_layer(items, size: tuple[int, int], output_path: str) -> str | None:
    """Render the SPRITE decorations (text / arrows / circles / lines /
    rects / stamps) onto a TRANSPARENT canvas of `size` — the burn-over-
    moving-video counterpart of composite_text_decorations. Highlights are
    not sprites (they modify the picture multiplicatively); export those
    with render_highlight_mask instead. Same placement maths as the still
    path, so an item lands on the video exactly where it sat in the editor.
    Returns output_path, or None when the items contain no sprites."""
    bw, bh = size
    sprites = [it for it in items if not isinstance(it, HighlightDeco)]
    if not sprites:
        return None
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    for it in sprites:
        im = _render_item(it)
        cx, cy = it.cx_frac * bw, it.cy_frac * bh
        layer.alpha_composite(im, (round(cx - im.width / 2), round(cy - im.height / 2)))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    layer.save(output_path)
    return output_path


def render_highlight_mask(items, size: tuple[int, int], output_path: str) -> str | None:
    """Export the feathered highlight mask (white inside the boxes) at
    `size` as a grayscale PNG — the EXACT mask _apply_highlights composites
    with, so a video render can reproduce brighten-inside / darken-outside
    on MOVING footage: ×HIGHLIGHT_BRIGHTEN and ×HIGHLIGHT_DARKEN branches
    (ffmpeg colorchannelmixer — multiplicative, like PIL Brightness) merged
    through this mask (maskedmerge). Returns output_path, or None when the
    items contain no highlights."""
    highlights = [it for it in items if isinstance(it, HighlightDeco)]
    if not highlights:
        return None
    mask = _highlight_mask(highlights, size)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    mask.save(output_path)
    return output_path


def make_decorated_clip(
    base_image_path: str, items, output_path: str, duration: float, fps: int = FPS
) -> str:
    """Composite the decorations and encode a STATIC MP4 of `duration` seconds."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tmp_png = tf.name
    try:
        composite_text_decorations(base_image_path, items, tmp_png)
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            tmp_png,
            "-t",
            f"{float(duration):.3f}",
            "-vf",
            f"fps={fps},format=yuv420p,scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-an",
            output_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print("[decorate] FATAL: ffmpeg failed")
            print(f"[decorate] stderr (tail): {r.stderr[-800:]}")
            Path(output_path).unlink(missing_ok=True)
            raise RuntimeError(f"decorate render failed for {output_path}")
    finally:
        Path(tmp_png).unlink(missing_ok=True)
    return output_path


# ===========================================================================
# Decorate GUI
# ===========================================================================


class _DecorateApp:
    """Add text / arrows / highlights / circles / lines / rectangles onto the
    previous image.

    Interaction model
    -----------------
    Each tool stays "armed" after you finish an item, so the next click starts a
    BRAND-NEW item of the same kind — clicking never moves an existing one.
    The most-recently-placed item is the "selected" item: nudge it with the
    arrow keys or the on-screen d-pad, resize with + / −, and rotate it with the
    dial (arrows) or the ↺ / ↻ buttons (lines / rectangles). Undo with the Undo
    button, U, Backspace, or Ctrl-Z.
    """

    def __init__(
        self,
        base_path,
        title,
        initial,
        tabs=(),
        stamps=None,
        work_dir=None,
        overlay_mode=False,
        previous_preview: PreviousEntryPreview | None = None,
        stamp_mode: bool = False,
    ):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            raise RuntimeError(
                "Could not open a display for the decorate GUI — this step "
                f"needs a desktop session (tkinter said: {exc})"
            )
        self.root.title(title)
        self.root.configure(bg="#1e1e24")

        # ── session state: this ONE window hosts the whole edit ──────────
        # overlay_mode: the base is a frame of PLAYING footage — nothing is
        # ever baked into it; FINISH hands back self.ops, the ordered recipe
        # [("layer", items), ("zoom", (wpct, cx, cy)), ...] the caller
        # re-applies to the MOVING video (layers burn as transparent PNGs,
        # zooms as real crops of the footage). Callers offer draw + stamp +
        # zoom; object stays out (its result is an opaque re-render).
        self.overlay_mode = bool(overlay_mode)
        self.stamp_mode = bool(stamp_mode)
        self.ops: list = []  # overlay mode's captured operations
        self.base_path = str(base_path)
        self.work_dir = (
            Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="decorator_"))
        )
        self.stamp_paths = [str(s) for s in (stamps or [])]
        self.previous_preview_data = previous_preview
        self.stamp_i = 0
        self.stamp_remove_bg = tk.BooleanVar(value=False)
        self._session_edited = False
        self._bake_n = 0
        # Snapshot stack for undoing DESTRUCTIVE base edits (zoom crops, object
        # edits, bakes). Each entry is (base_path, items_copy, active, ops_len)
        # captured BEFORE the change; _undo pops it and reloads that base.
        self._base_undo_stack: list = []
        self._destroyed = False
        self.action = "exit"  # "finish" | "exit"
        self.final_path = None
        self.final_video = None  # an exported animated MP4 wins on FINISH
        self._busy = False  # true only during modal waits
        self._obj_frame = None  # the mounted object editor, if any
        self._pending_tab = None
        self._end_after_object = False
        self._zoom_mode = False
        self._zoom_frozen = False
        self._zoom_rect = None
        self._zoom_cx = self._zoom_cy = 0.5  # crop centre, fractions
        self._zoom_wpct = 55  # crop width, % of the image
        self._zoom_hpct = None  # crop height %; None = aspect-locked to width

        # ── TAB STRIP (top): selectable title LEFT, tabs at the RIGHT ────
        # Tabs switch tool IN THIS WINDOW: stamp arms a droppable picture,
        # zoom arms a drag-a-box crop, object opens the extraction editor
        # over the top and returns here. Ctrl+Left / Ctrl+Right cycles.
        self.tabs = ["draw"] + [t for t in tabs if t != "draw"]
        self._tab_i = 0
        if len(self.tabs) > 1:
            strip = tk.Frame(self.root, bg="#14141a")
            strip.pack(side="top", fill="x")
            tvar = tk.StringVar(value=title)
            te = tk.Entry(
                strip,
                textvariable=tvar,
                state="readonly",
                readonlybackground="#14141a",
                fg="#8a8a95",
                relief="flat",
                bd=0,
                font=("Arial", 10),
                width=max(18, len(title)),
            )
            te.pack(side="left", padx=(10, 4), pady=6)  # selectable/copyable
            tk.Button(
                strip,
                text="▶",
                command=lambda: self._cycle_tab(+1),
                bg="#14141a",
                fg="#8a8a95",
                bd=0,
                font=("Arial", 11),
            ).pack(side="right", padx=(2, 10))
            self._tab_btns = {}
            for name in reversed(self.tabs):
                b = tk.Button(
                    strip,
                    text=name.upper(),
                    bd=0,
                    padx=14,
                    pady=6,
                    font=("Arial", 10, "bold"),
                    bg="#2b6cb0" if name == "draw" else "#14141a",
                    fg="#ffffff" if name == "draw" else "#8a8a95",
                    activebackground="#2b6cb0",
                    activeforeground="#ffffff",
                    command=(lambda n=name: self._goto_tab(n)),
                )
                b.pack(side="right", padx=2, pady=4)
                self._tab_btns[name] = b
            tk.Button(
                strip,
                text="◀",
                command=lambda: self._cycle_tab(-1),
                bg="#14141a",
                fg="#8a8a95",
                bd=0,
                font=("Arial", 11),
            ).pack(side="right", padx=(8, 2))
            self.root.bind("<Control-Left>", lambda e: self._cycle_tab(-1))
            self.root.bind("<Control-Right>", lambda e: self._cycle_tab(+1))

        self.base = _load_base_image(base_path)
        self.bw, self.bh = self.base.size

        self.scale, self.disp_w, self.disp_h = _fit_display(
            self.bw,
            self.bh,
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
        )
        self.base_disp = self.base.resize(
            (self.disp_w, self.disp_h), _RESAMPLE
        ).convert("RGBA")

        # Defaults (retained between items).
        self.cur_font_size = max(MIN_FONT_PX, round(DEFAULT_FONT_FRAC * self.bh))
        self.cur_arrow_len = max(MIN_ARROW_PX, round(DEFAULT_ARROW_FRAC * self.bw))
        self.cur_arrow_angle = 0.0

        # State.
        self.items: list = list(initial or [])
        self.active = None  # last-placed item == "selected" target
        self._pending = None  # a floating item (arrow/text) → click to drop
        self._rearm = None  # tool to re-arm after a placement | None
        self.mode = "interactive"  # "interactive" | "typing"
        self._dial_shown = False
        self._mode_draw_highlight = False
        self._box_start = None

        # Multi-click "draw" tools (circle / line / rectangle).
        self._draw_tool = None  # None | "circle" | "line" | "rect"
        self._draw_stage = 0  # which click we're waiting for
        self._draw_anchor = None  # (x, y) in display coords | None
        self._draw_angle = 0.0  # radians, screen coords (rect direction)
        self._last_xy = (0, 0)  # last cursor pos (for Enter-to-confirm)

        self.result = None
        for it in reversed(self.items):  # seed "cur" defaults from the last sprite
            if isinstance(it, TextDeco):
                self.cur_font_size = it.font_size
                break
            if isinstance(it, ArrowDeco):
                self.cur_arrow_len, self.cur_arrow_angle = it.length, it.angle
                break

        self._composite_photo = None
        self._ghost_photo = None
        self._ghost_wh = (1, 1)
        self._blank = ImageTk.PhotoImage(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))

        self._build_ui()
        if self.previous_preview_data:
            self.previous_preview.set_preview(self.previous_preview_data)
        self._bind_keys()
        self._rebuild_composite()
        self._update_controls()
        if self.overlay_mode:
            self._set_status(
                "LIVE VIDEO scene — this frame is where your "
                "scene starts; drawings/stamps layer over the "
                "PLAYING footage, zoom crops the footage itself."
            )
        else:
            self._set_status(
                "Add text, an arrow, a highlight, a circle, "
                "a line, or a rectangle to start."
            )
        if self.stamp_paths and "stamp" in self.tabs:
            self._goto_tab("stamp")  # pictures are waiting — start there
        elif "zoom" in self.tabs:
            self._goto_tab("zoom")  # zoom is the default tool tab

        self.root.update_idletasks()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        self.canvas = tk.Canvas(
            self.root,
            width=self.disp_w,
            height=self.disp_h,
            bg="#000000",
            highlightthickness=0,
            cursor="none",
        )
        self.canvas.pack(side="left", padx=10, pady=10)
        self.canvas_item = self.canvas.create_image(
            0, 0, anchor="nw", image=self._blank
        )
        self.ghost_item = self.canvas.create_image(
            0, 0, anchor="nw", image=self._blank, state="hidden"
        )
        self.box_item = self.canvas.create_rectangle(
            0, 0, 0, 0, dash=(6, 4), outline="#ffd166", width=2, state="hidden"
        )
        # Live vector previews for the multi-click draw tools.
        self.draw_oval = self.canvas.create_oval(
            0, 0, 0, 0, dash=(5, 3), outline=DRAW_PREVIEW_COLOR, width=2, state="hidden"
        )
        self.draw_line = self.canvas.create_line(
            0, 0, 0, 0, fill=DRAW_PREVIEW_COLOR, width=3, state="hidden"
        )
        self.draw_guide = self.canvas.create_line(
            0, 0, 0, 0, dash=(4, 3), fill=DRAW_GUIDE_COLOR, width=1, state="hidden"
        )
        self.draw_poly = self.canvas.create_polygon(
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            fill="",
            outline=DRAW_PREVIEW_COLOR,
            width=2,
            state="hidden",
        )
        self.draw_dot = self.canvas.create_oval(
            0, 0, 0, 0, fill=DRAW_PREVIEW_COLOR, outline="", state="hidden"
        )
        self.cur_ring_bg = self.canvas.create_oval(
            0, 0, 0, 0, outline=CURSOR_RING_BG_COLOR, width=2, state="hidden"
        )
        self.cur_ring = self.canvas.create_oval(
            0, 0, 0, 0, outline=CURSOR_RING_COLOR, width=1, state="hidden"
        )
        self.cur_dot = self.canvas.create_oval(
            0,
            0,
            0,
            0,
            fill=CURSOR_DOT_COLOR,
            outline=CURSOR_RING_BG_COLOR,
            state="hidden",
        )
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-3>", self._on_right_click)

        side = tk.Frame(self.root, bg="#1e1e24", width=360)
        side.pack(side="right", fill="y", padx=(0, 10), pady=10)
        side.pack_propagate(False)
        self._side = side

        self.previous_preview = PreviousEntryPreviewPopup(
            self.root,
            bg="#101016",
            panel_bg="#1e1e24",
            text_fg="#eeeeee",
            hint_fg="#8a8a95",
            accent="#ffd166",
            expanded_width=330,
            image_size=(300, 170),
        )

        self.header_var = tk.StringVar(value="DECORATE — DRAW")
        tk.Label(
            side,
            textvariable=self.header_var,
            bg="#1e1e24",
            fg="#dddddd",
            justify="left",
            font=("Arial", 11, "bold"),
        ).pack(anchor="w", pady=(2, 8))
        # Stamp pre-decoration (stamp_decorate): the image being edited IS a
        # picked stamp that will later be stamped onto the previous scene. Show
        # a dismissible banner in the BOTTOM-LEFT (with a big ✕ to close it) so
        # it's unmistakable during this initial stamp-editing session.
        if self.stamp_mode:
            self.stamp_banner = tk.Frame(
                self.root,
                bg="#3b2a05",
                highlightthickness=1,
                highlightbackground="#7a5a10",
            )
            inner = tk.Frame(self.stamp_banner, bg="#3b2a05")
            inner.pack(side="left", fill="both", expand=True, padx=2, pady=2)
            tk.Label(
                inner,
                text=(
                    "★ STAMP MODE ★\n"
                    "This image will be STAMPED onto the\n"
                    "previous scene. Edit it here (cut it out,\n"
                    "clean it up), then Finish to use as a stamp."
                ),
                bg="#3b2a05",
                fg="#ffd166",
                justify="left",
                font=("Arial", 10, "bold"),
                padx=8,
                pady=6,
                wraplength=290,
            ).pack(side="left")
            tk.Button(
                self.stamp_banner,
                text="✕",
                command=self.stamp_banner.place_forget,
                bg="#3b2a05",
                fg="#ffd166",
                activebackground="#5a3f08",
                activeforeground="#ffffff",
                font=("Arial", 14, "bold"),
                bd=0,
                padx=8,
                pady=2,
                cursor="hand2",
            ).pack(side="right", fill="y")
            # place bottom-left, above the previous-entry preview popup
            self.stamp_banner.place(x=8, rely=1.0, y=-210, anchor="sw")
            self.root.after(120, lambda: self.stamp_banner.lift())

        # ── scrollable middle: hosts every per-tab panel + controls ──────
        # (small window → this scrolls; the Finish/Undo row and status are
        #  pinned at the bottom and can never be cut off or squished)
        _mid = tk.Frame(side, bg="#1e1e24")
        _mid.pack(fill="both", expand=True)
        _vsb = tk.Scrollbar(_mid, orient="vertical")
        _vsb.pack(side="right", fill="y")
        self._side_scroll = tk.Canvas(
            _mid, bg="#1e1e24", highlightthickness=0, yscrollcommand=_vsb.set
        )
        self._side_scroll.pack(side="left", fill="both", expand=True)
        _vsb.config(command=self._side_scroll.yview)
        _inner = tk.Frame(self._side_scroll, bg="#1e1e24")
        _iwin = self._side_scroll.create_window((0, 0), window=_inner, anchor="nw")
        _inner.bind(
            "<Configure>",
            lambda e: self._side_scroll.configure(
                scrollregion=self._side_scroll.bbox("all")
            ),
        )
        self._side_scroll.bind(
            "<Configure>", lambda e: self._side_scroll.itemconfig(_iwin, width=e.width)
        )

        def _wheel(e):
            step = -1 if getattr(e, "num", 0) == 4 or e.delta > 0 else 1
            self._side_scroll.yview_scroll(step, "units")

        self._side_scroll.bind(
            "<Enter>",
            lambda e: [
                self._side_scroll.bind_all("<MouseWheel>", _wheel),
                self._side_scroll.bind_all("<Button-4>", _wheel),
                self._side_scroll.bind_all("<Button-5>", _wheel),
            ],
        )
        self._side_scroll.bind(
            "<Leave>",
            lambda e: [
                self._side_scroll.unbind_all("<MouseWheel>"),
                self._side_scroll.unbind_all("<Button-4>"),
                self._side_scroll.unbind_all("<Button-5>"),
            ],
        )
        self._panel_host = _inner

        # ── the swappable tool area: one panel per tab ────────────────────
        self.tool_area = tk.Frame(_inner, bg="#1e1e24")
        self.tool_area.pack(anchor="w", fill="both", expand=True)
        self.draw_panel = tk.Frame(self.tool_area, bg="#1e1e24")
        self.draw_panel.pack(anchor="w", fill="x")

        # Toolbar (three rows — room for more tools later).
        tb1 = tk.Frame(self.draw_panel, bg="#1e1e24")
        tb1.pack(anchor="w", fill="x", pady=(0, 4))
        tk.Button(
            tb1,
            text="✏  Add text",
            command=self._add_text,
            font=("Arial", 11, "bold"),
            bg="#3a3a46",
            fg="white",
            width=11,
        ).pack(side="left")
        tk.Button(
            tb1,
            text="➤  Add arrow",
            command=self._add_arrow,
            font=("Arial", 11, "bold"),
            bg="#3a3a46",
            fg="white",
            width=11,
        ).pack(side="left", padx=(6, 0))
        tb2 = tk.Frame(self.draw_panel, bg="#1e1e24")
        tb2.pack(anchor="w", fill="x", pady=(0, 4))
        tk.Button(
            tb2,
            text="✦  Add highlight",
            command=self._add_highlight,
            font=("Arial", 11, "bold"),
            bg="#3a3a46",
            fg="white",
            width=11,
        ).pack(side="left")
        tk.Button(
            tb2,
            text="◯  Add circle",
            command=self._add_circle,
            font=("Arial", 11, "bold"),
            bg="#3a3a46",
            fg="white",
            width=11,
        ).pack(side="left", padx=(6, 0))
        tb3 = tk.Frame(self.draw_panel, bg="#1e1e24")
        tb3.pack(anchor="w", fill="x", pady=(0, 4))
        tk.Button(
            tb3,
            text="\u2500  Add line",
            command=self._add_line,
            font=("Arial", 11, "bold"),
            bg="#3a3a46",
            fg="white",
            width=11,
        ).pack(side="left")
        tk.Button(
            tb3,
            text="\u25ad  Add rectangle",
            command=self._add_rectangle,
            font=("Arial", 11, "bold"),
            bg="#3a3a46",
            fg="white",
            width=11,
        ).pack(side="left", padx=(6, 0))

        # ── STAMP panel: preview + arrows + repeat stamping ──────────────
        self.stamp_panel = tk.Frame(self.tool_area, bg="#1e1e24")
        self._stamp_prev_photo = None
        self.stamp_preview = tk.Label(
            self.stamp_panel, bg="#101016", bd=1, relief="solid"
        )
        self.stamp_preview.pack(anchor="w", pady=(0, 4))
        nav = tk.Frame(self.stamp_panel, bg="#1e1e24")
        nav.pack(anchor="w", fill="x", pady=(0, 4))
        self.stamp_prev_btn = tk.Button(
            nav, text="◀", width=3, command=lambda: self._stamp_select(self.stamp_i - 1)
        )
        self.stamp_prev_btn.pack(side="left")
        self.stamp_which_var = tk.StringVar(value="")
        tk.Label(
            nav,
            textvariable=self.stamp_which_var,
            bg="#1e1e24",
            fg="#dddddd",
            font=("Arial", 10, "bold"),
        ).pack(side="left", padx=8)
        self.stamp_next_btn = tk.Button(
            nav, text="▶", width=3, command=lambda: self._stamp_select(self.stamp_i + 1)
        )
        self.stamp_next_btn.pack(side="left")
        tk.Button(
            self.stamp_panel,
            text="pick another picture…",
            command=self._stamp_add_file,
            font=("Arial", 10),
        ).pack(anchor="w", pady=(0, 4))
        # "key out white background" toggle. A plain Button (the WHOLE thing
        # is a click target) whose text reflects the on/off state — a
        # Checkbutton's hit area is only the tiny indicator + text glyphs,
        # which is fiddly to click reliably.
        self._remove_bg_btn = tk.Button(
            self.stamp_panel,
            text=self._remove_bg_label(),
            command=self._toggle_remove_bg,
            bg="#1e1e24",
            fg="#bbbbbb",
            activebackground="#1e1e24",
            activeforeground="#dddddd",
            font=("Arial", 10),
            anchor="w",
            padx=8,
            pady=4,
            relief="flat",
        )
        self._remove_bg_btn.pack(anchor="w", fill="x", pady=(0, 4))
        tk.Label(
            self.stamp_panel,
            bg="#1e1e24",
            fg="#bbbbbb",
            font=("Arial", 10),
            justify="left",
            wraplength=330,
            text=(
                "Click the image to stamp it — as many times as you "
                "like. + / − resizes the floating one; arrow keys "
                "nudge the last stamp; Undo removes it."
            ),
        ).pack(anchor="w", pady=(0, 6))

        # ── OBJECT panel: the extraction editor's controls mount here ────
        self.object_panel = tk.Frame(self.tool_area, bg="#1e1e24")

        # ── ZOOM panel: live box on the canvas + size controls ───────────
        self.zoom_panel = tk.Frame(self.tool_area, bg="#1e1e24")
        tk.Label(
            self.zoom_panel,
            bg="#1e1e24",
            fg="#bbbbbb",
            font=("Arial", 10),
            justify="left",
            wraplength=330,
            text=(
                "The gold box is the crop. Move it over the image; "
                "the controls below size it. Click on the image "
                "to apply the zoom instantly."
            ),
        ).pack(anchor="w", pady=(0, 6))

        def _pct_entry(parent, var):
            """A digit-only Entry (no leading zeros) bound to a StringVar."""
            e = tk.Entry(
                parent,
                textvariable=var,
                width=4,
                font=("Arial", 11, "bold"),
                justify="center",
                bg="#2a2a33",
                fg="#dddddd",
                insertbackground="#dddddd",
                relief="flat",
                validate="key",
            )

            # Only allow digits; strip leading zeros. Empty is allowed mid-edit.
            def _validate(new_text):
                if new_text == "":
                    return True
                if not new_text.isdigit():
                    return False
                # no leading zeros (except a bare "0")
                if len(new_text) > 1 and new_text[0] == "0":
                    return False
                return True

            e.config(validatecommand=(_validate, "%P"))
            return e

        def _pct_row(parent, label, dec_cmd, inc_cmd, var):
            row = tk.Frame(parent, bg="#1e1e24")
            row.pack(anchor="w", pady=(0, 4))
            tk.Label(
                row,
                text=label,
                bg="#1e1e24",
                fg="#8a8a95",
                font=("Arial", 10),
                width=10,
                anchor="w",
            ).pack(side="left")
            tk.Button(
                row,
                text="−",
                width=3,
                font=("Arial", 12, "bold"),
                bg="#2a2a33",
                fg="#dddddd",
                relief="flat",
                command=dec_cmd,
            ).pack(side="left", padx=(4, 0))
            ent = _pct_entry(row, var)
            ent.pack(side="left", padx=6)
            tk.Label(
                row,
                text="%",
                bg="#1e1e24",
                fg="#8a8a95",
                font=("Arial", 10),
            ).pack(side="left", padx=(0, 4))
            tk.Button(
                row,
                text="+",
                width=3,
                font=("Arial", 12, "bold"),
                bg="#2a2a33",
                fg="#dddddd",
                relief="flat",
                command=inc_cmd,
            ).pack(side="left")
            return ent

        # Main dual control — sizes BOTH width and height together.
        tk.Label(
            self.zoom_panel,
            text="both (lock ratio):",
            bg="#1e1e24",
            fg="#bbbbbb",
            font=("Arial", 10, "bold"),
        ).pack(anchor="w", pady=(2, 2))
        self.zoom_pct_var = tk.StringVar(value="")
        self.zoom_dual_entry = _pct_row(
            self.zoom_panel,
            "",
            lambda: self._zoom_resize(-10),
            lambda: self._zoom_resize(+10),
            self.zoom_pct_var,
        )
        self.zoom_dual_entry.bind("<Return>", self._zoom_commit_dual)
        self.zoom_dual_entry.bind("<FocusOut>", self._zoom_commit_dual)

        # Separate width / height controls — a sub-box, independent sizing.
        sub = tk.LabelFrame(
            self.zoom_panel,
            text="separate",
            bg="#1e1e24",
            fg="#bbbbbb",
            font=("Arial", 9),
            padx=8,
            pady=6,
            relief="flat",
        )
        sub.pack(anchor="w", fill="x", pady=(6, 4))
        self.zoom_w_var = tk.StringVar(value="")
        self.zoom_h_var = tk.StringVar(value="")
        self.zoom_w_entry = _pct_row(
            sub,
            "width",
            lambda: self._zoom_resize(-10, "w"),
            lambda: self._zoom_resize(+10, "w"),
            self.zoom_w_var,
        )
        self.zoom_w_entry.bind("<Return>", self._zoom_commit_w)
        self.zoom_w_entry.bind("<FocusOut>", self._zoom_commit_w)
        self.zoom_h_entry = _pct_row(
            sub,
            "height",
            lambda: self._zoom_resize(-10, "h"),
            lambda: self._zoom_resize(+10, "h"),
            self.zoom_h_var,
        )
        self.zoom_h_entry.bind("<Return>", self._zoom_commit_h)
        self.zoom_h_entry.bind("<FocusOut>", self._zoom_commit_h)

        # Big warning, shown only while zoom mode is live (box visible but the
        # crop not yet applied). Hidden once the zoom is applied/cancelled.
        self.zoom_warn = tk.Label(
            self.zoom_panel,
            text=("IMAGE NOT CROPPED YET!\nCLICK WHERE YOU WANT THE CROP TO OCCUR."),
            bg="#3b1414",
            fg="#ff5555",
            font=("Arial", 12, "bold"),
            justify="center",
            wraplength=320,
            pady=10,
        )
        # "complete crop" button — shown after a right-click freezes the box in
        # place (so the user can finish without clicking the canvas).
        self.zoom_complete_btn = tk.Button(
            self.zoom_panel,
            text="✓  complete crop",
            command=self._zoom_complete,
            font=("Arial", 12, "bold"),
            bg="#2e7d32",
            fg="white",
        )

        # Text-entry group (hidden unless typing).
        self.entry_frame = tk.Frame(self._panel_host, bg="#1e1e24")
        self.text_label = tk.Label(
            self.entry_frame,
            text="Type your text:",
            bg="#1e1e24",
            fg="#bbbbbb",
            font=("Arial", 10),
        )
        self.text_label.pack(anchor="w")
        self.text_var = tk.StringVar(value="")
        self.entry = tk.Entry(
            self.entry_frame,
            textvariable=self.text_var,
            width=28,
            font=("Arial", 13),
            highlightthickness=1,
            highlightbackground="#1e1e24",
            highlightcolor="#5ad1ff",
        )
        self.entry.pack(anchor="w", pady=(2, 4))
        self.entry.bind("<Return>", lambda e: self._close_typing_box())
        self.entry.bind("<Escape>", lambda e: self._cancel_typing())
        self.entry.bind("<KeyRelease>", self._on_text_change)
        erow = tk.Frame(self.entry_frame, bg="#1e1e24")
        erow.pack(anchor="w")
        tk.Button(
            erow,
            text="Done typing (click to place)",
            command=self._close_typing_box,
            font=("Arial", 11, "bold"),
            bg="#2e7d32",
            fg="white",
        ).pack(side="left")
        tk.Button(
            erow, text="Cancel", command=self._cancel_typing, font=("Arial", 11)
        ).pack(side="left", padx=(6, 0))

        # Rotation dial (hidden unless a rotatable item is the active/pending item).
        self.dial_frame = tk.Frame(side, bg="#1e1e24")
        self.dial_label = tk.Label(
            self.dial_frame,
            text="Rotation (drag to aim):",
            bg="#1e1e24",
            fg="#bbbbbb",
            font=("Arial", 10),
        )
        self.dial_label.pack(anchor="w")
        self.dial = tk.Canvas(
            self.dial_frame, width=150, height=150, bg="#2a2a33", highlightthickness=0
        )
        self.dial.pack(anchor="w", pady=(2, 2))
        self.dial.bind("<Button-1>", self._dial_event)
        self.dial.bind("<B1-Motion>", self._dial_event)
        self.angle_var = tk.StringVar(value="0°")
        tk.Label(
            self.dial_frame,
            textvariable=self.angle_var,
            bg="#1e1e24",
            fg="#5ad1ff",
            font=("Arial", 10, "bold"),
        ).pack(anchor="w")

        # "Selected item" tweak panel — nudge d-pad.
        self.tweak_frame = tk.Frame(self._panel_host, bg="#1e1e24")
        tk.Label(
            self.tweak_frame,
            text="Move the selected item:",
            bg="#1e1e24",
            fg="#bbbbbb",
            font=("Arial", 10),
        ).pack(anchor="w")
        trow = tk.Frame(self.tweak_frame, bg="#1e1e24")
        trow.pack(anchor="w", pady=(2, 0))
        pad = tk.Frame(trow, bg="#1e1e24")
        pad.pack(side="left")
        bopt = dict(
            font=("Arial", 12, "bold"), bg="#3a3a46", fg="white", width=3, height=1
        )
        tk.Button(pad, text="\u2191", command=lambda: self._nudge(0, -1), **bopt).grid(
            row=0, column=1, padx=1, pady=1
        )
        tk.Button(pad, text="\u2190", command=lambda: self._nudge(-1, 0), **bopt).grid(
            row=1, column=0, padx=1, pady=1
        )
        tk.Label(pad, text="\u2022", bg="#1e1e24", fg="#666", font=("Arial", 12)).grid(
            row=1, column=1
        )
        tk.Button(pad, text="\u2192", command=lambda: self._nudge(1, 0), **bopt).grid(
            row=1, column=2, padx=1, pady=1
        )
        tk.Button(pad, text="\u2193", command=lambda: self._nudge(0, 1), **bopt).grid(
            row=2, column=1, padx=1, pady=1
        )

        # Place-mode controls (Edit text + size).
        self.controls = tk.Frame(self._panel_host, bg="#1e1e24")
        self.controls.pack(anchor="w", fill="x", pady=(6, 2))
        self._anchor = tk.Frame(self._panel_host, bg="#1e1e24")
        self._anchor.pack(fill="x")  # permanent pack anchor for the frames above
        self.edit_btn = tk.Button(
            self.controls,
            text="✎  Edit text",
            command=self._edit_text,
            font=("Arial", 11),
            width=14,
            state="disabled",
        )
        self.edit_btn.pack(anchor="w", pady=(0, 6))

        srow = tk.Frame(self.controls, bg="#1e1e24")
        self._size_row = srow
        srow.pack(anchor="w")
        tk.Label(
            srow, text="Size:", bg="#1e1e24", fg="#dddddd", font=("Arial", 11)
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            srow,
            text="\u2212",
            command=self._size_dec,
            font=("Arial", 15, "bold"),
            width=3,
        ).pack(side="left")
        self.size_var = tk.StringVar(value=str(self.cur_font_size))
        se = tk.Entry(
            srow,
            textvariable=self.size_var,
            width=5,
            justify="center",
            font=("Arial", 14),
        )
        se.pack(side="left", padx=4)
        se.bind("<Return>", self._size_commit)
        se.bind("<FocusOut>", self._size_commit)
        tk.Button(
            srow, text="+", command=self._size_inc, font=("Arial", 15, "bold"), width=3
        ).pack(side="left")
        tk.Label(srow, text="px", bg="#1e1e24", fg="#999999", font=("Arial", 10)).pack(
            side="left", padx=(6, 0)
        )

        self.status_var = tk.StringVar(value="")

        btns = tk.Frame(side, bg="#1e1e24")
        btns.pack(fill="x", side="bottom", pady=(4, 2))
        self.status_entry = tk.Entry(
            side,
            textvariable=self.status_var,
            state="readonly",
            readonlybackground="#1e1e24",
            fg="#5ad1ff",
            relief="flat",
            bd=0,
            font=("Arial", 10, "bold"),
        )
        self.status_entry.pack(
            side="bottom", fill="x", pady=(6, 4)
        )  # selectable/copyable, pinned
        self.done_btn = tk.Button(
            btns,
            text="✓ Finish edits\n& move on",
            command=self._done,
            font=("Arial", 11, "bold"),
            bg="#2e7d32",
            fg="white",
            width=13,
        )
        self.done_btn.pack(side="left", padx=(0, 6))
        self.undo_btn = tk.Button(
            btns,
            text="\u21b6 Undo\n(Ctrl-Z)",
            command=self._undo,
            font=("Arial", 11),
            width=10,
            state="disabled",
        )
        self.undo_btn.pack(side="left")

    def _bind_keys(self):
        r = self.root
        for k in ("a", "A"):
            r.bind(k, self._kbd(self._add_text))
        for k in ("r", "R"):
            r.bind(k, self._kbd(self._add_arrow))
        for k in ("h", "H"):
            r.bind(k, self._kbd(self._add_highlight))
        for k in ("c", "C"):
            r.bind(k, self._kbd(self._add_circle))
        for k in ("l", "L"):
            r.bind(k, self._kbd(self._add_line))
        for k in ("b", "B"):
            r.bind(k, self._kbd(self._add_rectangle))
        for k in ("e", "E"):
            r.bind(k, self._kbd(self._edit_text))
        for k in ("u", "U", "<BackSpace>"):
            r.bind(k, self._kbd(self._undo))
        for k in ("<Control-z>", "<Control-Z>"):
            r.bind(k, self._kbd(self._undo))
        for k in ("<Return>", "d", "D"):
            r.bind(k, self._kbd(self._finish_or_confirm))
        for k in ("<Escape>", "q", "Q"):
            r.bind(k, self._kbd(self._exit))
        for k in ("<plus>", "<equal>", "<KP_Add>"):
            r.bind(k, self._kbd(self._size_inc))
        for k in ("<minus>", "<underscore>", "<KP_Subtract>"):
            r.bind(k, self._kbd(self._size_dec))
        # Arrow keys now NUDGE the selected item (Shift = bigger step).
        r.bind("<Left>", self._kbd(lambda: self._nudge(-1, 0)))
        r.bind("<Right>", self._kbd(lambda: self._nudge(1, 0)))
        r.bind("<Up>", self._kbd(lambda: self._nudge(0, -1)))
        r.bind("<Down>", self._kbd(lambda: self._nudge(0, 1)))
        r.bind("<Shift-Left>", self._kbd(lambda: self._nudge(-1, 0, big=True)))
        r.bind("<Shift-Right>", self._kbd(lambda: self._nudge(1, 0, big=True)))
        r.bind("<Shift-Up>", self._kbd(lambda: self._nudge(0, -1, big=True)))
        r.bind("<Shift-Down>", self._kbd(lambda: self._nudge(0, 1, big=True)))
        # Rotation moved off the arrow keys onto , / .
        r.bind("<comma>", self._kbd(lambda: self._rotate_active(-ARROW_ANGLE_STEP)))
        r.bind("<period>", self._kbd(lambda: self._rotate_active(+ARROW_ANGLE_STEP)))
        r.focus_set()

    def _kbd(self, fn):
        def handler(event):
            if self._busy or self._obj_frame is not None:
                return  # the object editor is mounted — keys are its
            if isinstance(self.root.focus_get(), tk.Entry):
                return  # don't fire shortcuts while typing in a field
            return fn()

        return handler

    # -- rendering ---------------------------------------------------------
    def _disp_font(self, base_px: int) -> int:
        return max(6, round(base_px * self.scale))

    def _render_item_img(self, item, *, display: bool) -> Image.Image:
        if isinstance(item, StampDeco):
            w = max(8, round(item.width * self.scale)) if display else item.width
            return render_stamp_image(item.path, w, item.remove_bg)
        if isinstance(item, ArrowDeco):
            L = max(8, round(item.length * self.scale)) if display else item.length
            return render_arrow_image(L, item.angle)
        if isinstance(item, CircleDeco):
            R = max(8, round(item.radius * self.scale)) if display else item.radius
            t = (
                max(1, round(item.thickness * self.scale))
                if display
                else item.thickness
            )
            return render_circle_image(R, t)
        if isinstance(item, LineDeco):
            L = max(8, round(item.length * self.scale)) if display else item.length
            t = (
                max(1, round(item.thickness * self.scale))
                if display
                else item.thickness
            )
            return render_line_image(L, item.angle, t)
        if isinstance(item, RectDeco):
            if display:
                w = max(10, round(item.width * self.scale))
                h = max(10, round(item.height * self.scale))
                t = (
                    max(1, round(item.thickness * self.scale))
                    if display
                    else item.thickness
                )
            else:
                w, h = item.width, item.height
                t = item.thickness
            return render_rect_image(w, h, item.angle, t)
        fs = self._disp_font(item.font_size) if display else item.font_size
        txt = render_text_image(item.text, fs)
        if item.angle:
            # negative: match the dial's clockwise-positive direction (see _render_item)
            txt = txt.rotate(-item.angle, resample=Image.BICUBIC, expand=True)
        return txt

    def _rebuild_composite(self):
        highlights = [it for it in self.items if isinstance(it, HighlightDeco)]
        if highlights:
            img = _apply_highlights(self.base_disp.convert("RGB"), highlights).convert(
                "RGBA"
            )
        else:
            img = self.base_disp.copy()
        for it in self.items:  # the floating _pending is NOT in items
            if isinstance(it, HighlightDeco):
                continue
            im = self._render_item_img(it, display=True)
            cx, cy = it.cx_frac * self.disp_w, it.cy_frac * self.disp_h
            img.alpha_composite(
                im, (round(cx - im.width / 2), round(cy - im.height / 2))
            )
        self._composite_photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfig(self.canvas_item, image=self._composite_photo)

    def _regen_ghost(self):
        """Refresh the floating ghost from the pending item (or hide it)."""
        if self._pending is None:
            self.canvas.itemconfig(self.ghost_item, state="hidden")
            return
        im = self._render_item_img(self._pending, display=True).copy()
        alpha = im.split()[3].point(lambda v: int(v * GHOST_ALPHA / 255))
        im.putalpha(alpha)
        self._ghost_wh = im.size
        self._ghost_photo = ImageTk.PhotoImage(im)
        self.canvas.itemconfig(self.ghost_item, image=self._ghost_photo)

    # -- on-canvas cursor reticle -----------------------------------------
    def _update_cursor(self, x, y):
        r = CURSOR_RADIUS
        for item in (self.cur_ring_bg, self.cur_ring):
            self.canvas.coords(item, x - r, y - r, x + r, y + r)
            self.canvas.itemconfig(item, state="normal")
        dr = CURSOR_DOT_R
        self.canvas.coords(self.cur_dot, x - dr, y - dr, x + dr, y + dr)
        self.canvas.itemconfig(self.cur_dot, state="normal")
        self.canvas.tag_raise(self.cur_ring_bg)
        self.canvas.tag_raise(self.cur_ring)
        self.canvas.tag_raise(self.cur_dot)

    # -- canvas events -----------------------------------------------------
    def _on_motion(self, event):
        self._update_cursor(event.x, event.y)
        self._last_xy = (event.x, event.y)
        if self._zoom_mode:
            if not self._zoom_frozen:
                self._zoom_move(event.x, event.y)  # the box follows the cursor
            return
        if self._draw_tool is not None and self._draw_anchor is not None:
            self._update_draw_preview((event.x, event.y))
            return
        if self._pending is not None:
            gw, gh = self._ghost_wh
            cx = min(max(event.x, 0), self.disp_w)
            cy = min(max(event.y, 0), self.disp_h)
            self.canvas.coords(self.ghost_item, cx - gw / 2, cy - gh / 2)
            self.canvas.itemconfig(self.ghost_item, state="normal")
            self.canvas.tag_raise(self.ghost_item)
            self._update_cursor(event.x, event.y)  # keep reticle above ghost

    def _on_leave(self, _event):
        for it in (self.cur_ring_bg, self.cur_ring, self.cur_dot):
            self.canvas.itemconfig(it, state="hidden")
        if self._pending is not None:
            self.canvas.itemconfig(self.ghost_item, state="hidden")

    def _on_press(self, event):
        if self._busy:
            return
        if self._zoom_mode:
            # Clicking the image centres the crop box there and applies the
            # zoom immediately — no separate "complete" step.
            self._zoom_move(event.x, event.y)
            self._zoom_complete()
            return
        # Requirement: clicking the canvas with the text tool open but nothing
        # typed must warn the user rather than silently doing nothing.
        if self.mode == "typing":
            if self._pending is not None:  # auto-ready already set it
                self._close_typing_box()
                self._place_pending(event.x, event.y)
            elif (
                isinstance(self.active, TextDeco)
                and self.active in self.items
                and self.text_var.get().strip()
            ):
                # Editing existing text with valid text: close box
                self._close_typing_box()
            else:
                self._warn_no_text()
            return
        if self._draw_tool is not None:
            self._draw_click((event.x, event.y))
            return
        if self._pending is not None:
            self._place_pending(event.x, event.y)
            return
        # Nothing armed: do NOT move an existing item — guide the user instead.
        self._set_status(
            "Pick a tool to add something (text / arrow / "
            "highlight / circle / line / rectangle)."
        )

    def _on_drag(self, event):
        self._update_cursor(event.x, event.y)
        self._last_xy = (event.x, event.y)
        if self._zoom_mode:
            self._zoom_frozen = False
            self._zoom_move(event.x, event.y)
            return
        if self._draw_tool is not None and self._draw_anchor is not None:
            self._update_draw_preview((event.x, event.y))
            return

    def _on_release(self, event):
        pass  # zoom applies on click; draw tools are click-move-click

    def _on_right_click(self, event):
        """In zoom mode: freeze the crop box where it is, teleport the cursor
        over to the sidebar (so the width/height buttons are reachable without
        disturbing the box), and reveal a 'complete crop' button to finish
        without clicking the canvas."""
        if not self._zoom_mode:
            return
        # keep the box at the click position (freeze it there)
        self._zoom_move(event.x, event.y)
        self._zoom_frozen = True
        # show the complete-crop button in the sidebar
        self.zoom_complete_btn.pack(anchor="w", fill="x", pady=(4, 4))
        self._set_status(
            "Box frozen in place. Adjust width/height here, then press "
            "✓ complete crop (or left-click the image to apply there)."
        )
        # warp the pointer onto the sidebar (over the zoom controls)
        try:
            self.root.update_idletasks()
            sx = self._side.winfo_rootx() + 60
            sy = self._side.winfo_rooty() + 120
            self.root.tk.call("tk", "warp_pointer", str(sx), str(sy))
        except Exception:
            pass

    def _place_pending(self, x, y):
        """Drop the floating pending item, select it, then re-arm the tool so
        the NEXT click starts a brand-new item (never moves this one)."""
        p = self._pending
        cx = min(max(x, 0), self.disp_w)
        cy = min(max(y, 0), self.disp_h)
        p.cx_frac = cx / self.disp_w
        p.cy_frac = cy / self.disp_h
        self.items.append(p)
        self.active = p
        self._pending = None
        if isinstance(p, ArrowDeco):
            self.cur_arrow_len, self.cur_arrow_angle = p.length, p.angle
        # Re-arm a fresh item of the same kind (text needs re-typing, so not it).
        if self._rearm == "arrow":
            self._pending = ArrowDeco(
                self.cur_arrow_len, self.cur_arrow_angle, 0.5, 0.5
            )
            self._regen_ghost()
        elif self._rearm == "stamp" and self.stamp_paths:
            self._pending = StampDeco(
                self.stamp_paths[self.stamp_i],
                p.width if isinstance(p, StampDeco) else self._default_stamp_w(),
                0.5,
                0.5,
                self.stamp_remove_bg.get(),
            )
            self._regen_ghost()
        else:
            self.canvas.itemconfig(self.ghost_item, state="hidden")
        self._rebuild_composite()
        self._update_controls()
        kind = type(p).__name__.replace("Deco", "").lower()
        extra = (
            " Click again for another."
            if self._pending is not None
            else " Nudge it with the arrow keys, or add more."
        )
        self._set_status(f"Placed the {kind}. {len(self.items)} item(s)." + extra)

    # -- toolbar: add / edit ----------------------------------------------
    def _add_text(self):
        self._cancel_draw()
        self._mode_draw_highlight = False
        self._pending = None
        self._rearm = None  # a new text needs fresh typing each time
        self.active = None
        self.canvas.itemconfig(self.ghost_item, state="hidden")
        self.text_var.set("")
        self._open_typing()

    def _add_arrow(self):
        self._cancel_draw()
        self._mode_draw_highlight = False
        self.entry_frame.pack_forget()
        self.mode = "interactive"
        self.active = None
        self._rearm = "arrow"  # stays armed → click again = new arrow
        self._pending = ArrowDeco(self.cur_arrow_len, self.cur_arrow_angle, 0.5, 0.5)
        self._update_controls()
        self._regen_ghost()
        self._rebuild_composite()
        self._set_status(
            "Aim it on the dial, set size with +/\u2212, "
            "click to drop. Click again for another."
        )

    def _add_highlight(self):
        self._begin_draw("highlight")
        self._set_status(
            "Click the CENTRE, aim the direction (default "
            "horizontal), click; then stretch W\u00d7H and confirm."
        )

    def _edit_text(self):
        if not isinstance(self.active, TextDeco):
            self._set_status("No selected text to edit — add one first.")
            return
        self._cancel_draw()
        self._mode_draw_highlight = False
        self._pending = None
        self.text_var.set(self.active.text)
        self._edit_backup = self.active.text  # save in case of cancel
        self._open_typing()

    # -- draw tools (circle / line / rectangle) ---------------------------
    def _begin_draw(self, tool):
        """Switch into a multi-click draw tool, leaving any other mode cleanly."""
        self.entry_frame.pack_forget()
        self._mode_draw_highlight = False
        self._box_start = None
        self.mode = "interactive"
        self.active = None
        self._pending = None
        self.canvas.itemconfig(self.ghost_item, state="hidden")
        self._rearm = tool
        self._draw_tool = tool
        self._draw_stage = 0
        self._draw_anchor = None
        self._draw_angle = 0.0
        self._hide_draw_preview()
        self._update_controls()

    def _add_circle(self):
        self._begin_draw("circle")
        self._set_status(
            "Click the CENTRE, move to size the radius, then "
            "click / Enter to confirm. Repeats for another."
        )

    def _add_line(self):
        self._begin_draw("line")
        self._set_status(
            "Click the START point, then click / Enter at the END "
            "point. Repeats for another."
        )

    def _add_rectangle(self):
        self._begin_draw("rect")
        self._set_status(
            "Click the CENTRE, aim the direction (default "
            "horizontal), click; then stretch W\u00d7H and confirm."
        )

    def _cancel_draw(self):
        if self._draw_tool is None:
            return
        self._draw_tool = None
        self._draw_stage = 0
        self._draw_anchor = None
        self._draw_angle = 0.0
        self._hide_draw_preview()

    def _hide_draw_preview(self):
        for it in (
            self.draw_oval,
            self.draw_line,
            self.draw_guide,
            self.draw_poly,
            self.draw_dot,
        ):
            self.canvas.itemconfig(it, state="hidden")

    def _show_anchor_dot(self, x, y):
        r = 4
        self.canvas.coords(self.draw_dot, x - r, y - r, x + r, y + r)
        self.canvas.itemconfig(self.draw_dot, state="normal")
        self.canvas.tag_raise(self.draw_dot)

    def _finish_or_confirm(self):
        if self._busy:
            return
        if self._obj_frame is not None:
            self._object_finish_session()  # Enter == the green button
            return
        if self._zoom_mode:
            self._zoom_complete()
            return
        """Enter / D: confirm the current draw STEP if a shape is mid-draw;
        otherwise finish & move on (a re-armed tool with no shape in progress
        must not swallow the finish shortcut)."""
        if self._draw_tool is not None and self._draw_anchor is not None:
            self._draw_click(self._last_xy)
        else:
            self._done()

    def _draw_click(self, xy):
        x = min(max(xy[0], 0), self.disp_w)
        y = min(max(xy[1], 0), self.disp_h)
        tool = self._draw_tool
        if tool == "circle":
            if self._draw_stage == 0:
                self._draw_anchor = (x, y)
                self._draw_stage = 1
                self._show_anchor_dot(x, y)
                self._update_draw_preview((x, y))
                self._set_status("Move to size the radius, then click / Enter.")
            else:
                self._commit_circle((x, y))
        elif tool == "line":
            if self._draw_stage == 0:
                self._draw_anchor = (x, y)
                self._draw_stage = 1
                self._show_anchor_dot(x, y)
                self._update_draw_preview((x, y))
                self._set_status("Move to the END point, then click / Enter.")
            else:
                self._commit_line((x, y))
        elif tool in ("rect", "highlight"):
            if self._draw_stage == 0:
                self._draw_anchor = (x, y)
                self._draw_stage = 1
                self._show_anchor_dot(x, y)
                self._update_draw_preview((x, y))
                self._set_status(
                    "Aim the direction, then click / Enter (default is horizontal)."
                )
            elif self._draw_stage == 1:
                ax, ay = self._draw_anchor
                if math.hypot(x - ax, y - ay) < 4:
                    self._draw_angle = 0.0  # barely moved → horizontal
                else:
                    deg = math.degrees(math.atan2(y - ay, x - ax))
                    snapped = round(deg / 45) * 45
                    if abs(deg - snapped) < 7:  # snap to 45s if close
                        deg = snapped
                    if abs(deg) == 180:
                        deg = 0  # 180 is same axis as 0
                    self._draw_angle = math.radians(deg)
                self._draw_stage = 2
                self.canvas.itemconfig(self.draw_guide, state="hidden")
                self._update_draw_preview((x, y))
                self._set_status(
                    "Now stretch the width \u00d7 height, then "
                    "click / Enter to confirm."
                )
            else:
                if tool == "rect":
                    self._commit_rect((x, y))
                else:
                    self._commit_highlight((x, y))

    def _update_draw_preview(self, xy):
        if self._draw_tool is None or self._draw_anchor is None:
            return
        x = min(max(xy[0], 0), self.disp_w)
        y = min(max(xy[1], 0), self.disp_h)
        ax, ay = self._draw_anchor
        tool = self._draw_tool
        if tool == "circle":
            rad = math.hypot(x - ax, y - ay)
            self.canvas.coords(self.draw_oval, ax - rad, ay - rad, ax + rad, ay + rad)
            self.canvas.itemconfig(self.draw_oval, state="normal")
            self.canvas.tag_raise(self.draw_oval)
        elif tool == "line":
            self.canvas.coords(self.draw_line, ax, ay, x, y)
            self.canvas.itemconfig(self.draw_line, state="normal")
            self.canvas.tag_raise(self.draw_line)
        elif tool in ("rect", "highlight"):
            if self._draw_stage == 1:
                if math.hypot(x - ax, y - ay) < 4:
                    a = 0.0
                else:
                    deg = math.degrees(math.atan2(y - ay, x - ax))
                    snapped = round(deg / 45) * 45
                    if abs(deg - snapped) < 7:
                        deg = snapped
                    if abs(deg) == 180:
                        deg = 0
                    a = math.radians(deg)
                gl = max(self.disp_w, self.disp_h)
                ca, sa = math.cos(a), math.sin(a)
                self.canvas.coords(
                    self.draw_guide,
                    ax - gl * ca,
                    ay - gl * sa,
                    ax + gl * ca,
                    ay + gl * sa,
                )
                self.canvas.itemconfig(self.draw_guide, state="normal")
                self.canvas.tag_raise(self.draw_guide)
            elif self._draw_stage == 2:
                a = self._draw_angle
                ca, sa = math.cos(a), math.sin(a)
                dx, dy = x - ax, y - ay
                half_w = abs(dx * ca + dy * sa)  # along the direction
                half_h = abs(-dx * sa + dy * ca)  # perpendicular
                pts = []
                for ux, uy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                    lx, ly = ux * half_w, uy * half_h
                    pts += [ax + lx * ca - ly * sa, ay + lx * sa + ly * ca]
                self.canvas.coords(self.draw_poly, *pts)
                self.canvas.itemconfig(self.draw_poly, state="normal")
                self.canvas.tag_raise(self.draw_poly)
        self.canvas.tag_raise(self.draw_dot)
        self._update_cursor(x, y)

    def _commit_circle(self, xy):
        ax, ay = self._draw_anchor
        rad_disp = math.hypot(xy[0] - ax, xy[1] - ay)
        if rad_disp < 6:
            self._set_status("Too small — move further out, then click / Enter.")
            return
        radius = max(MIN_CIRCLE_PX, round(rad_disp / self.scale))
        deco = CircleDeco(radius, ax / self.disp_w, ay / self.disp_h)
        self.items.append(deco)
        self._finish_draw_commit(deco, f"Circle added. {len(self.items)} item(s).")

    def _commit_line(self, xy):
        ax, ay = self._draw_anchor
        x = min(max(xy[0], 0), self.disp_w)
        y = min(max(xy[1], 0), self.disp_h)
        dist = math.hypot(x - ax, y - ay)
        if dist < 6:
            self._set_status("Too short — move further, then click / Enter.")
            return
        length = max(MIN_LINE_PX, round(dist / self.scale))
        angle = math.degrees(math.atan2(y - ay, x - ax))
        mx, my = (ax + x) / 2.0, (ay + y) / 2.0
        deco = LineDeco(length, angle, mx / self.disp_w, my / self.disp_h)
        self.items.append(deco)
        self._finish_draw_commit(deco, f"Line added. {len(self.items)} item(s).")

    def _commit_rect(self, xy):
        ax, ay = self._draw_anchor
        x = min(max(xy[0], 0), self.disp_w)
        y = min(max(xy[1], 0), self.disp_h)
        a = self._draw_angle
        ca, sa = math.cos(a), math.sin(a)
        dx, dy = x - ax, y - ay
        half_w = abs(dx * ca + dy * sa)
        half_h = abs(-dx * sa + dy * ca)
        if half_w * 2 < 6 or half_h * 2 < 6:
            self._set_status("Too small — stretch wider / taller, then click / Enter.")
            return
        width = max(MIN_RECT_PX, round(2 * half_w / self.scale))
        height = max(MIN_RECT_PX, round(2 * half_h / self.scale))
        deco = RectDeco(
            width, height, math.degrees(a), ax / self.disp_w, ay / self.disp_h
        )
        self.items.append(deco)
        self._finish_draw_commit(deco, f"Rectangle added. {len(self.items)} item(s).")

    def _commit_highlight(self, xy):
        ax, ay = self._draw_anchor
        x = min(max(xy[0], 0), self.disp_w)
        y = min(max(xy[1], 0), self.disp_h)
        a = self._draw_angle
        ca, sa = math.cos(a), math.sin(a)
        dx, dy = x - ax, y - ay
        half_w = abs(dx * ca + dy * sa)
        half_h = abs(-dx * sa + dy * ca)
        if half_w * 2 < 6 or half_h * 2 < 6:
            self._set_status("Too small — stretch wider / taller, then click / Enter.")
            return
        width = max(MIN_RECT_PX, round(2 * half_w / self.scale))
        height = max(MIN_RECT_PX, round(2 * half_h / self.scale))
        deco = HighlightDeco(
            width, height, math.degrees(a), ax / self.disp_w, ay / self.disp_h
        )
        self.items.append(deco)
        self._finish_draw_commit(deco, f"Highlight added. {len(self.items)} item(s).")

    def _finish_draw_commit(self, deco, msg):
        """Select the just-committed shape and RE-ARM the same tool so the next
        click starts a fresh shape (it never moves this one)."""
        self.active = deco
        self._pending = None
        self._draw_stage = 0
        self._draw_anchor = None
        self._draw_angle = 0.0
        self._hide_draw_preview()
        self._draw_tool = self._rearm  # re-arm the same draw tool
        self._update_controls()
        self._rebuild_composite()
        self._set_status(
            msg + "  Nudge with arrow keys / d-pad, or click to start another."
        )

    # -- text typing -------------------------------------------------------
    def _open_typing(self):
        self.mode = "typing"
        if self._dial_shown:
            self.dial_frame.pack_forget()
            self._dial_shown = False
        self._highlight_entry(False)
        self.entry_frame.pack(anchor="w", fill="x", pady=(4, 2), before=self._anchor)
        self.edit_btn.config(state="disabled")
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        self._on_text_change()  # Trigger immediate update/ghost

    def _cancel_typing(self):
        self.entry_frame.pack_forget()
        self._highlight_entry(False)
        self.mode = "interactive"
        # Revert if we were editing
        if isinstance(self.active, TextDeco) and hasattr(self, "_edit_backup"):
            self.active.text = self._edit_backup
            del self._edit_backup
            self._rebuild_composite()
        self._update_controls()
        self.root.focus_set()
        self._set_status("Cancelled.")

    def _on_text_change(self, event=None):
        """Auto-ready the text to place as soon as >0 non-whitespace chars are typed."""
        text = self.text_var.get().strip()
        editing = isinstance(self.active, TextDeco) and self.active in self.items
        if not text:
            self._pending = None
            self.canvas.itemconfig(self.ghost_item, state="hidden")
            self._highlight_entry(True)
            self._update_controls()
            return

        self._highlight_entry(False)
        if editing:
            self.active.text = text
            self._rebuild_composite()
            self._update_controls()
            self._set_status("Text updated live. Click Done or press Enter to finish.")
        else:
            if not isinstance(self._pending, TextDeco):
                self._pending = TextDeco(text, self.cur_font_size, 0.5, 0.5)
                self._rearm = None
            else:
                self._pending.text = text
            self._regen_ghost()
            self.canvas.itemconfig(self.ghost_item, state="normal")
            self._rebuild_composite()
            self._update_controls()
            self._set_status("Click on the image to drop the text.")

    def _close_typing_box(self):
        """Hide the text entry box but keep the text armed to be placed."""
        text = self.text_var.get().strip()
        if self._pending is None and not text:
            self._warn_no_text()
            return
        self.entry_frame.pack_forget()
        self._highlight_entry(False)
        self.mode = "interactive"
        if hasattr(self, "_edit_backup"):
            del self._edit_backup
        self._update_controls()
        self.root.focus_set()
        if self._pending is not None:
            self._set_status("Move the mouse and click to drop the text.")
        else:
            self._set_status("Ready.")

    def _warn_no_text(self):
        self._highlight_entry(True)
        if self.mode != "typing":
            self._open_typing()
        self.entry.focus_set()
        messagebox.showwarning(
            "No text to place",
            "No text to place on screen! You must type the text first, "
            "and then you will be able to place.",
        )
        self.entry.focus_set()

    def _highlight_entry(self, on: bool):
        if on:
            self.entry.config(
                highlightbackground="#ff5555",
                highlightcolor="#ff5555",
                highlightthickness=2,
            )
            self.text_label.config(fg="#ff8888", text="Type your text:  ← required!")
        else:
            self.entry.config(
                highlightbackground="#1e1e24",
                highlightcolor="#5ad1ff",
                highlightthickness=1,
            )
            self.text_label.config(fg="#bbbbbb", text="Type your text:")

    # -- selected-item controls -------------------------------------------
    def _update_controls(self):
        if self._destroyed:
            return
        """Show/hide the dial, tweak (nudge) panel, Edit + size to match
        the current pending / selected item."""
        target = self._pending if self._pending is not None else self.active
        # Dial: visible whenever a rotatable item is active/pending.
        show_dial = isinstance(
            target, (ArrowDeco, LineDeco, RectDeco, HighlightDeco, TextDeco)
        )
        if show_dial:
            if not self._dial_shown:
                self.dial_frame.pack(
                    anchor="w", fill="x", pady=(4, 2), before=self._anchor
                )
                self._dial_shown = True
            self.dial_label.config(
                text="Direction (drag to aim):"
                if isinstance(target, ArrowDeco)
                else "Rotation (drag to aim):"
            )
            self._redraw_dial()
        elif self._dial_shown:
            self.dial_frame.pack_forget()
            self._dial_shown = False
        # Tweak panel: only meaningful for a placed (selected) item.
        if self.active is not None and self.mode != "typing":
            self.tweak_frame.pack(
                anchor="w", fill="x", pady=(4, 2), before=self._anchor
            )
        else:
            self.tweak_frame.pack_forget()
        if target is not None:
            self.size_var.set(str(self._size_of(target)))
        self._update_buttons()

    def _nudge(self, sx, sy, *, big=False):
        if self.active is None or isinstance(self.active, HighlightDeco):
            self._set_status("Nothing selected to move — place an item first.")
            return
        frac = NUDGE_FRAC_BIG if big else NUDGE_FRAC
        self.active.cx_frac = min(1.0, max(0.0, self.active.cx_frac + sx * frac))
        self.active.cy_frac = min(1.0, max(0.0, self.active.cy_frac + sy * frac))
        self._rebuild_composite()

    # -- arrow dial --------------------------------------------------------
    def _dial_center(self):
        return 75.0, 75.0

    def _dial_target(self):
        if isinstance(
            self._pending,
            (ArrowDeco, LineDeco, RectDeco, HighlightDeco, TextDeco),
        ):
            return self._pending
        if isinstance(
            self.active,
            (ArrowDeco, LineDeco, RectDeco, HighlightDeco, TextDeco),
        ):
            return self.active
        return None

    def _dial_event(self, event):
        cx, cy = self._dial_center()
        ang = math.degrees(math.atan2(event.y - cy, event.x - cx))
        self.cur_arrow_angle = ang
        tgt = self._dial_target()
        if tgt is not None:
            tgt.angle = ang
            if tgt is self._pending:
                self._regen_ghost()
            self._rebuild_composite()
        self._redraw_dial()

    def _redraw_dial(self):
        c = self.dial
        c.delete("all")
        cx, cy = self._dial_center()
        R = 63.0
        c.create_oval(cx - R, cy - R, cx + R, cy + R, outline="#666", width=2)
        for a in (0, 90, 180, 270):  # cardinal ticks
            rad = math.radians(a)
            c.create_line(
                cx + (R - 8) * math.cos(rad),
                cy + (R - 8) * math.sin(rad),
                cx + R * math.cos(rad),
                cy + R * math.sin(rad),
                fill="#555",
                width=2,
            )
        tgt = self._dial_target()
        ang = tgt.angle if tgt is not None else self.cur_arrow_angle
        rad = math.radians(ang)
        c.create_line(
            cx,
            cy,
            cx + R * math.cos(rad),
            cy + R * math.sin(rad),
            fill="#5ad1ff",
            width=3,
            arrow="last",
        )
        c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#5ad1ff", outline="")
        tgt = self._dial_target()
        label = "Direction" if isinstance(tgt, ArrowDeco) else "Rotation"
        self.angle_var.set(f"{label}: {ang:+.0f}\u00b0")

    def _rotate_active(self, delta):
        tgt = self.active
        if isinstance(tgt, ArrowDeco):
            tgt.angle += delta
            self.cur_arrow_angle = tgt.angle
            self._redraw_dial()
        elif isinstance(tgt, (LineDeco, RectDeco, TextDeco)):
            tgt.angle += delta
            if self._dial_shown:
                self._redraw_dial()
        else:
            self._set_status("Selected item can't be rotated.")
            return
        self._rebuild_composite()

    # -- size --------------------------------------------------------------
    def _size_of(self, item) -> int:
        if isinstance(item, StampDeco):
            return item.width
        if isinstance(item, TextDeco):
            return item.font_size
        if isinstance(item, ArrowDeco):
            return item.length
        if isinstance(item, CircleDeco):
            return item.thickness
        if isinstance(item, LineDeco):
            return item.thickness
        if isinstance(item, RectDeco):
            return item.thickness
        return self.cur_font_size

    def _size_target(self):
        return self._pending if self._pending is not None else self.active

    def _active_size(self) -> int:
        tgt = self._size_target()
        return self._size_of(tgt) if tgt is not None else self.cur_font_size

    def _apply_size(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            self.size_var.set(str(self._active_size()))
            return
        tgt = self._size_target()
        if isinstance(tgt, StampDeco):
            v = max(40, min(self.bw, value))
            tgt.width = v
        elif isinstance(tgt, ArrowDeco):
            v = max(MIN_ARROW_PX, min(MAX_ARROW_PX, value))
            tgt.length = v
            self.cur_arrow_len = v
        elif isinstance(tgt, CircleDeco):
            v = max(MIN_THICKNESS, min(MAX_THICKNESS, value))
            tgt.thickness = v
        elif isinstance(tgt, LineDeco):
            v = max(MIN_THICKNESS, min(MAX_THICKNESS, value))
            tgt.thickness = v
        elif isinstance(tgt, RectDeco):
            v = max(MIN_THICKNESS, min(MAX_THICKNESS, value))
            tgt.thickness = v
        elif isinstance(tgt, TextDeco):
            v = max(MIN_FONT_PX, min(MAX_FONT_PX, value))
            tgt.font_size = v
            self.cur_font_size = v
        else:
            v = max(MIN_FONT_PX, min(MAX_FONT_PX, value))
            self.cur_font_size = v
        self.size_var.set(str(v))
        if tgt is not None:
            if tgt is self._pending:
                self._regen_ghost()
            self._rebuild_composite()

    def _size_step(self) -> int:
        tgt = self._size_target()
        if isinstance(tgt, StampDeco):
            return max(20, round(self.bw * 0.04))
        if isinstance(tgt, ArrowDeco):
            return ARROW_STEP_PX
        if isinstance(tgt, (CircleDeco, LineDeco, RectDeco)):
            return THICKNESS_STEP_PX
        return FONT_STEP_PX

    def _size_inc(self):
        self._apply_size(self._active_size() + self._size_step())

    def _size_dec(self):
        self._apply_size(self._active_size() - self._size_step())

    def _size_commit(self, event=None):
        self._apply_size(self.size_var.get().strip())
        if getattr(event, "keysym", "") == "Return":
            self.root.focus_set()
            return "break"
        return None

    # -- finish / undo / exit ---------------------------------------------
    def _push_base_undo(self):
        """Snapshot the current base + placed items so a later _undo can
        reverse a destructive base edit (zoom crop, object edit, bake).
        Call BEFORE the operation replaces the base."""
        self._base_undo_stack.append(
            (self.base_path, list(self.items), self.active, len(self.ops))
        )

    def _undo(self):
        # Mid-draw shape in progress → cancel just that shape.
        if self._draw_tool is not None and self._draw_anchor is not None:
            self._cancel_draw()
            self._draw_tool = self._rearm  # keep the tool armed
            self._update_controls()
            self._set_status("Cancelled the shape you were drawing.")
            return
        # No pending items to remove, but a destructive base edit (e.g. a
        # zoom crop) was applied → restore the previous base + items.
        if not self.items and self._base_undo_stack:
            prev_path, prev_items, prev_active, prev_ops_len = (
                self._base_undo_stack.pop()
            )
            self.items = prev_items
            self.active = prev_active
            self._pending = None
            # Overlay mode: drop the ops this edit appended (layer/zoom) so the
            # final burn no longer re-applies the undone crop.
            if self.overlay_mode and len(self.ops) > prev_ops_len:
                del self.ops[prev_ops_len:]
            self._reload_base(prev_path)
            self._update_controls()
            self._set_status("Undid the last base edit (e.g. zoom).")
            return
        if not self.items:
            self._set_status("Nothing to undo yet.")
            return
        removed = self.items.pop()
        if removed is self.active:
            self.active = None
        self._rebuild_composite()
        self._update_controls()
        self._set_status(f"Removed the last item \u2014 {len(self.items)} left.")

    def _mark_tab(self, name):
        self._tab_i = self.tabs.index(name) if name in self.tabs else 0
        for n, b in getattr(self, "_tab_btns", {}).items():
            on = n == name
            b.config(
                bg="#2b6cb0" if on else "#14141a", fg="#ffffff" if on else "#8a8a95"
            )

    def _show_panel(self, name):
        # Forget EVERY tool panel, then pack only the one for the active tab.
        # object_panel must be forgotten too: the object editor mounts its
        # controls INTO it (and destroys them on close), so if it's left
        # packed when switching away the empty frame stays in tool_area
        # alongside the new panel — both expand=True, so they split the space
        # and the new tab reads as blank/unusable.
        for p in (
            self.draw_panel,
            self.stamp_panel,
            self.zoom_panel,
            self.object_panel,
        ):
            p.pack_forget()
        panel = {
            "stamp": self.stamp_panel,
            "zoom": self.zoom_panel,
            "object": self.object_panel,
        }.get(name, self.draw_panel)
        panel.pack(anchor="w", fill="both", expand=True)
        # per-tab sidebar: item controls for draw + stamp (stamp without the
        # Edit-text button); zoom and object get ONLY their own panel.
        self.controls.pack_forget()
        if name in ("draw", "stamp"):
            self.controls.pack(anchor="w", fill="x", pady=(6, 2), before=self._anchor)
            self.edit_btn.pack_forget()
            if name == "draw":
                self.edit_btn.pack(anchor="w", pady=(0, 6), before=self._size_row)
        self.header_var.set(
            {
                "stamp": "STAMP PICTURES",
                "zoom": "ZOOM / CROP",
                "object": "OBJECT — cut out / effects",
            }.get(name, "DECORATE — DRAW")
        )

    def _goto_tab(self, name):
        """Switch tool INSIDE this window — the sidebar swaps with it."""
        if self._busy or name not in self.tabs:
            return
        if self._obj_frame is not None:
            if name == "object":
                return
            # leaving the object tab applies the STILL edits and switches;
            # an armed animated effect is NOT rendered here — only the green
            # Finish button renders it (and ends the session).
            self._pending_tab = name
            self._obj_frame._finish_still_only()
            return
        self._zoom_hide()
        self._pending = None
        self._rearm = None
        self._cancel_draw()
        self.canvas.itemconfig(self.ghost_item, state="hidden")
        if name == "object":
            self._tab_object()  # returns to the draw tab afterwards
            return
        self._mark_tab(name)
        self._show_panel(name)
        if name == "draw":
            self._set_status(
                "Draw tools: text / arrow / highlight / circle / line / rectangle."
            )
        elif name == "stamp":
            self._tab_stamp()
        elif name == "zoom":
            self._tab_zoom()
        self._update_controls()

    def _cycle_tab(self, step):
        self._goto_tab(self.tabs[(self._tab_i + step) % len(self.tabs)])

    # ── STAMP: pick from the passed-in pictures, stamp again and again ────
    def _default_stamp_w(self) -> int:
        return max(40, round(self.bw * 0.30))

    def _stamp_select(self, i):
        if not self.stamp_paths:
            return
        self.stamp_i = i % len(self.stamp_paths)
        path = self.stamp_paths[self.stamp_i]
        n = len(self.stamp_paths)
        self.stamp_which_var.set(f"{self.stamp_i + 1}/{n}  {Path(path).name[:22]}")
        state = "normal" if n > 1 else "disabled"
        self.stamp_prev_btn.config(state=state)
        self.stamp_next_btn.config(state=state)
        try:
            thumb = _load_stamp_rgba(path, self.stamp_remove_bg.get()).copy()
            thumb.thumbnail((300, 170))
            self._stamp_prev_photo = ImageTk.PhotoImage(thumb)
            self.stamp_preview.config(image=self._stamp_prev_photo, text="")
        except Exception as exc:
            self.stamp_preview.config(image="", text=f"preview failed: {exc}")
        self._stamp_rearm()

    def _remove_bg_label(self) -> str:
        return (
            "☑ key out white background (ON)"
            if self.stamp_remove_bg.get()
            else "☐ key out white background"
        )

    def _toggle_remove_bg(self):
        turning_on = not self.stamp_remove_bg.get()
        self.stamp_remove_bg.set(turning_on)
        # Keying out the background is a synchronous flood-fill over the
        # whole image (no ML, but not instant on a big picture either) —
        # without this, the app just sits there with no feedback while it
        # runs and looks frozen. Force the busy state onto the screen with
        # update_idletasks() BEFORE the blocking call, since Tkinter won't
        # repaint on its own until this event handler returns.
        if hasattr(self, "_remove_bg_btn"):
            if turning_on:
                self._remove_bg_btn.config(
                    text="⏳ keying out background…", state="disabled"
                )
                self.root.config(cursor="watch")
                self.root.update_idletasks()
            try:
                self._stamp_rearm()
            finally:
                self._remove_bg_btn.config(text=self._remove_bg_label(), state="normal")
                self.root.config(cursor="")
        else:
            self._stamp_rearm()

    def _stamp_rearm(self):
        """(Re)float the selected picture on the cursor, keeping the last
        used width, so every click drops another copy."""
        if not self.stamp_paths:
            return
        width = (
            self._pending.width
            if isinstance(self._pending, StampDeco)
            else self._default_stamp_w()
        )
        self._pending = StampDeco(
            self.stamp_paths[self.stamp_i], width, 0.5, 0.5, self.stamp_remove_bg.get()
        )
        self._rearm = "stamp"
        self._regen_ghost()

    def _stamp_add_file(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="pick a picture to stamp",
            filetypes=[
                ("images", "*.png *.jpg *.jpeg *.webp *.bmp"),
                ("all files", "*.*"),
            ],
        )
        if path:
            self.stamp_paths.append(str(path))
            self._stamp_select(len(self.stamp_paths) - 1)

    def _tab_stamp(self):
        if not self.stamp_paths:
            # Don't auto-open the file picker — just tell the user nothing's
            # loaded yet (they can use 'pick a picture' if the tab has one).
            self._set_status("No stamp pictures loaded yet.")
            return
        self._stamp_select(self.stamp_i)
        self._set_status("Click the image to stamp — click again for more.")

    # ── ZOOM: a live box, moved by clicking, applied by the button ────────
    def _tab_zoom(self):
        self._zoom_mode = True
        self._zoom_frozen = False
        self._zoom_redraw()
        self.zoom_warn.pack(anchor="w", fill="x", pady=(4, 4))
        self._set_status(
            "The gold box follows your cursor — − / + sizes it, "
            "click on the image to apply the zoom."
        )

    def _zoom_box_px(self):
        w = self.disp_w * self._zoom_wpct / 100.0
        if self._zoom_hpct is not None:
            h = self.disp_h * self._zoom_hpct / 100.0
        else:
            h = w * self.disp_h / self.disp_w  # aspect locked to the image
        cx = min(max(self._zoom_cx * self.disp_w, w / 2), self.disp_w - w / 2)
        cy = min(max(self._zoom_cy * self.disp_h, h / 2), self.disp_h - h / 2)
        self._zoom_cx, self._zoom_cy = cx / self.disp_w, cy / self.disp_h
        return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2

    def _zoom_sync_entry_vars(self):
        """Push the current wpct/hpct into the sidebar entry StringVars.
        The dual box shows the value when width and height match (or are
        aspect-locked, which means equal %); blank when they differ."""
        eff_h = self._zoom_hpct if self._zoom_hpct is not None else self._zoom_wpct
        self.zoom_w_var.set(str(self._zoom_wpct))
        self.zoom_h_var.set(str(eff_h))
        if self._zoom_hpct is None or self._zoom_hpct == self._zoom_wpct:
            self.zoom_pct_var.set(str(self._zoom_wpct))
        else:
            self.zoom_pct_var.set("")

    def _zoom_redraw(self):
        x0, y0, x1, y1 = self._zoom_box_px()
        if self._zoom_rect is None:
            self._zoom_rect = self.canvas.create_rectangle(
                x0, y0, x1, y1, outline="#e6c15a", width=3, dash=(6, 4)
            )
        else:
            self.canvas.coords(self._zoom_rect, x0, y0, x1, y1)
            self.canvas.itemconfig(self._zoom_rect, state="normal")
        self.canvas.tag_raise(self._zoom_rect)
        self._zoom_sync_entry_vars()

    def _zoom_move(self, x, y):
        self._zoom_cx = min(max(x / self.disp_w, 0.0), 1.0)
        self._zoom_cy = min(max(y / self.disp_h, 0.0), 1.0)
        self._zoom_redraw()

    def _zoom_resize(self, delta_pct, axis=None):
        """Resize the crop. axis=None (the dual control) moves BOTH width and
        height by the fixed amount; axis='w'/'h' moves just that one. Touching
        EITHER axis independently unlocks it from the aspect ratio (so width
        +/- changes ONLY width, height +/- changes ONLY height)."""
        if axis == "w":
            # editing width independently: unlock height first (freeze it at its
            # current effective value) so only width changes.
            if self._zoom_hpct is None:
                self._zoom_hpct = self._zoom_wpct
            self._zoom_wpct = min(100, max(10, self._zoom_wpct + delta_pct))
        elif axis == "h":
            # editing height independently unlocks it.
            cur = self._zoom_hpct if self._zoom_hpct is not None else self._zoom_wpct
            self._zoom_hpct = min(100, max(10, cur + delta_pct))
        else:
            # dual — increment BOTH axes by the fixed amount.
            self._zoom_wpct = min(100, max(10, self._zoom_wpct + delta_pct))
            cur = (
                self._zoom_hpct
                if self._zoom_hpct is not None
                else self._zoom_wpct - delta_pct
            )
            self._zoom_hpct = min(100, max(10, cur + delta_pct))
        self._zoom_redraw()

    def _zoom_commit_int(self, text, axis):
        """Apply a typed value (digits only) to one axis; empty/invalid reverts."""
        text = (text or "").strip()
        if not text or not text.isdigit():
            self._zoom_sync_entry_vars()  # revert the field
            return
        v = min(100, max(10, int(text)))
        if axis == "w":
            # typing width independently unlocks height first.
            if self._zoom_hpct is None:
                self._zoom_hpct = self._zoom_wpct
            self._zoom_wpct = v
        elif axis == "h":
            self._zoom_hpct = v  # typing height unlocks it
        else:  # dual — set width, and re-lock height to width (aspect).
            self._zoom_wpct = v
            self._zoom_hpct = None
        self._zoom_redraw()

    def _zoom_commit_dual(self, _e=None):
        self._zoom_commit_int(self.zoom_pct_var.get(), "dual")

    def _zoom_commit_w(self, _e=None):
        self._zoom_commit_int(self.zoom_w_var.get(), "w")

    def _zoom_commit_h(self, _e=None):
        self._zoom_commit_int(self.zoom_h_var.get(), "h")

    def _zoom_hide(self):
        self._zoom_mode = False
        self._zoom_frozen = False
        if self._zoom_rect is not None:
            self.canvas.itemconfig(self._zoom_rect, state="hidden")
        for w in (self.zoom_warn, self.zoom_complete_btn):
            try:
                w.pack_forget()
            except tk.TclError:
                pass

    def _zoom_complete(self):
        from ___visuals.MANUAL_STOCK_PLACEMENT import CropBox, crop_and_zoom

        self._zoom_hide()
        self._note_supersede()
        # Snapshot BEFORE the crop so _undo can reverse it (restoring the
        # pre-zoom base + any items that get baked in below).
        self._push_base_undo()
        if self.overlay_mode:
            # LIVE video: nothing is baked — the items so far become a layer
            # op and the crop a zoom op, re-applied to the MOVING footage at
            # burn time (the video itself plays cropped from here on). Only
            # the DISPLAY updates, exactly as the still path would look
            # (composite + crop), so editing continues on the zoomed view
            # with coordinates in the new space.
            if self.items:
                self.ops.append(("layer", list(self.items)))
            self.ops.append(
                (
                    "zoom",
                    (self._zoom_wpct, self._zoom_cx, self._zoom_cy, self._zoom_hpct),
                )
            )
            self._bake_n += 1
            shown = self.base_path
            if self.items:
                shown = str(self.work_dir / f"ovbake_{self._bake_n:02d}.png")
                composite_text_decorations(self.base_path, list(self.items), shown)
                self.items = []
                self.active = None
                self._pending = None
            out = str(self.work_dir / f"zoom_{self._bake_n:02d}.png")
            crop_and_zoom(
                shown,
                CropBox(self._zoom_wpct, self._zoom_cx, self._zoom_cy, self._zoom_hpct),
                out,
            )
            self._reload_base(out)
            self._zoom_cx = self._zoom_cy = 0.5
            self._zoom_hpct = None
            self._goto_tab("draw")
            self._set_status(
                "Zoomed — the VIDEO plays cropped to this view "
                "from here on. Keep editing, or FINISH."
            )
            return
        self._bake_to_base()
        self._bake_n += 1
        out = str(self.work_dir / f"zoom_{self._bake_n:02d}.png")
        crop_and_zoom(
            self.base_path,
            CropBox(self._zoom_wpct, self._zoom_cx, self._zoom_cy, self._zoom_hpct),
            out,
        )
        self._session_edited = True
        self._reload_base(out)
        self._zoom_cx = self._zoom_cy = 0.5
        self._zoom_hpct = None
        self._goto_tab("draw")
        self._set_status("Zoomed. Keep editing, or FINISH.")

    # ── OBJECT: the extraction editor opens ON TOP; this window hides ─────
    def _tab_object(self):
        """Mount the object extraction editor IN THIS WINDOW: its canvas
        replaces the image area, its controls fill the sidebar panel under
        the tabs, and the decorator's OWN 'Finish edits & move on', 'Undo'
        and status line are its chrome — no duplicate buttons, no resize.
        Clicking another tab applies the object edit and switches there."""
        if self._busy or self._obj_frame is not None:
            return
        from ___visuals.decorator.object_editor import ObjectSeparator  # lazy

        self._bake_to_base()
        self._mark_tab("object")
        self._show_panel("object")
        self._pending_tab = None
        # pin the window size — mounting must not resize anything
        self.root.update_idletasks()
        geo = f"{self.root.winfo_width()}x{self.root.winfo_height()}"
        self._bake_n += 1
        obj_dir = self.work_dir / f"object_{self._bake_n:02d}"
        obj_dir.mkdir(parents=True, exist_ok=True)

        self.canvas.pack_forget()
        # the decorator's own buttons drive the editor while it's mounted
        self._saved_done_cmd = self.done_btn.cget("command")
        self._saved_undo_cmd = self.undo_btn.cget("command")

        def _done(saved):
            self._obj_frame = None
            self.canvas.pack(side="left", padx=10, pady=10, before=self._side)
            self.done_btn.config(
                command=self._saved_done_cmd, text="✓ Finish edits\n& move on"
            )
            self.undo_btn.config(command=self._saved_undo_cmd)
            self.root.geometry(geo)
            self._apply_object_result(saved)
            if self._end_after_object:
                self._end_after_object = False
                self._done()  # green button = the session ENDS here
                return
            nxt, self._pending_tab = self._pending_tab or "draw", None
            self._goto_tab(nxt)

        try:
            self._obj_frame = ObjectSeparator(
                str(self.base_path),
                str(obj_dir),
                master=self.root,
                on_done=_done,
                hosts={
                    "sidebar": self.object_panel,
                    "status": self.status_var,
                    "undo_btn": self.undo_btn,
                },
            )
            self._obj_frame.pack(
                side="left",
                fill="both",
                expand=True,
                padx=10,
                pady=10,
                before=self._side,
            )
            self.done_btn.config(command=self._object_finish_session)
            self.undo_btn.config(command=self._obj_frame._undo, state="disabled")
            self.root.geometry(geo)  # hold the exact same window size
        except Exception as exc:
            print(f"[draw] object editor failed to mount: {exc}")
            self._obj_frame = None
            self.canvas.pack(side="left", padx=10, pady=10, before=self._side)
            self._goto_tab("draw")

    def _object_finish_session(self):
        """The green button while the object editor is mounted: finish the
        object edit (rendering an armed animated effect) and END the whole
        session — same button, same meaning, on every tab."""
        self._end_after_object = True
        self._obj_frame._finish()

    def _apply_object_result(self, result):
        if not result:
            self._set_status("Object: closed without saving — nothing changed.")
            return
        if Path(str(result)).suffix.lower() in VIDEO_EXTS:
            self.final_video = str(result)
            self._session_edited = True
            self._set_status(
                "Animated MP4 captured as the session result — "
                "press FINISH to use it. (Any further edit "
                "discards it.)"
            )
            print(
                f"[draw]   object: animated result {Path(result).name} — "
                f"applied when you FINISH"
            )
        else:
            self._note_supersede()
            self._session_edited = True
            self._push_base_undo()
            self._reload_base(str(result))
            self._set_status("Object edit applied. Keep editing, or FINISH.")

    def _note_supersede(self):
        if self.final_video:
            print(
                f"[draw]   further edits made — discarding the earlier "
                f"animated result {Path(self.final_video).name}"
            )
            self.final_video = None

    # -- baking / reloading the base IN PLACE -------------------------------
    def _bake_to_base(self):
        """Composite the items placed so far onto the base and carry on
        editing the result (same window)."""
        if not self.items:
            return
        self._note_supersede()
        self._bake_n += 1
        out = str(self.work_dir / f"bake_{self._bake_n:02d}.png")
        composite_text_decorations(self.base_path, list(self.items), out)
        self._session_edited = True
        self.items = []
        self.active = None
        self._pending = None
        self._reload_base(out)

    def _reload_base(self, path):
        self.base_path = str(path)
        self.base = _load_base_image(self.base_path)
        self.bw, self.bh = self.base.size
        self.scale, self.disp_w, self.disp_h = _fit_display(
            self.bw,
            self.bh,
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
        )
        self.base_disp = self.base.resize(
            (self.disp_w, self.disp_h), _RESAMPLE
        ).convert("RGBA")
        self.canvas.config(width=self.disp_w, height=self.disp_h)
        self._rebuild_composite()
        self._update_controls()

    def _done(self):
        self._cancel_draw()
        self.result = list(self.items)
        if self.overlay_mode:  # video overlay: the OPS are the
            if self.items:  # result — nothing is baked, the base
                self.ops.append(("layer", list(self.items)))
            self.action = "finish"  # (a frame of playing footage) stays
            self.final_path = None
        elif len(self.tabs) > 1:  # session mode: this window OWNS baking
            self._bake_to_base()
            self.action = "finish"
            if self.final_video:  # an exported animated MP4 wins
                self.final_path = self.final_video
            else:
                self.final_path = self.base_path if self._session_edited else None
        self._destroyed = True
        self.root.destroy()

    def _exit(self):
        self.result = None
        self._destroyed = True
        self.root.destroy()

    # -- misc --------------------------------------------------------------
    def _update_buttons(self):
        if self._destroyed:
            return
        n = len(self.items)
        # Undo is available when there's an item to remove OR a destructive
        # base edit (zoom crop / object edit / bake) on the undo stack.
        can_undo = bool(n) or bool(self._base_undo_stack)
        self.undo_btn.config(state="normal" if can_undo else "disabled")
        self.edit_btn.config(
            state="normal"
            if (isinstance(self.active, TextDeco) and self.mode != "typing")
            else "disabled"
        )

    def _set_status(self, msg):
        if self._destroyed:
            return
        try:
            self.status_var.set(msg)
        except tk.TclError:
            pass

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._exit)
        self.root.mainloop()


def decorate_prev_interactive(
    base_image_path,
    window_title="Decorate the previous image",
    initial=None,
    previous_preview: PreviousEntryPreview | None = None,
):
    """Open the draw canvas ALONE (no tabs). Returns the item list on
    Finish (possibly empty), or None if the user EXITS (resume later)."""
    app = _DecorateApp(
        base_image_path, window_title, initial, previous_preview=previous_preview
    )
    app.run()
    return app.result


def run_editor_session(
    base_image_path,
    window_title="decorate",
    tabs=("stamp", "zoom", "object"),
    stamps=None,
    work_dir=None,
    overlay_mode=False,
    previous_preview: PreviousEntryPreview | None = None,
    stamp_mode: bool = False,
):
    """Open the ONE decorator window for a whole session: the draw canvas
    with STAMP / ZOOM / OBJECT all working IN-window (the object extraction
    editor mounts under the tab strip). Returns (action, path):
      action — "finish" or "exit"
      path   — the session result on finish (a still, OR an animated MP4 if
               the object editor exported one and nothing was edited after),
               None on finish-with-no-edits / exit

    overlay_mode=True is the LIVE-VIDEO session: the base is a frame of
    playing footage; draw + stamp + zoom are offered (the object tab is
    stripped from `tabs` — its result is an opaque re-render that can't sit
    over moving video); nothing is ever baked; and the return becomes
    (action, ops): the ordered recipe [("layer", [items...]) | ("zoom",
    (wpct, cx_frac, cy_frac)), ...] on finish (None on exit / empty), for
    the caller to re-apply to the video — layers as transparent PNGs
    (render_overlay_layer / render_highlight_mask), zooms as real crops of
    the moving footage.
    """
    if overlay_mode:
        tabs = tuple(t for t in tabs if t in ("stamp", "zoom"))
    app = _DecorateApp(
        base_image_path,
        window_title,
        None,
        tabs=tabs,
        stamps=stamps,
        work_dir=work_dir,
        overlay_mode=overlay_mode,
        previous_preview=previous_preview,
        stamp_mode=stamp_mode,
    )
    app.run()
    if overlay_mode:
        return ("finish", list(app.ops)) if app.action == "finish" else ("exit", None)
    return ("finish", app.final_path) if app.action == "finish" else ("exit", None)


# ===========================================================================
# Standalone test
# ===========================================================================


def _main():
    print(
        "[draw] NOTE: this standalone run opens the draw canvas ALONE.\n"
        "[draw] The full editor (draw + stamp/zoom/object tabs) is:\n"
        "[draw]     uv run ___visuals/decorator/api.py PIC.png\n"
    )
    ap = argparse.ArgumentParser(
        description="Decorate a base image, then bake PNG/MP4."
    )
    ap.add_argument("base", help="base image (or video — first frame is used)")
    ap.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help=">0 → write a static MP4 of this length, else a PNG",
    )
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    items = decorate_prev_interactive(args.base)
    if items is None:
        print("[decorate] exited without finishing.")
        sys.exit(0)
    print(f"[decorate] {len(items)} item(s):")
    for it in items:
        if isinstance(it, ArrowDeco):
            print(
                f"  - arrow      {it.length}px  {it.angle:+.0f}\u00b0  "
                f"@({it.cx_frac:.2f},{it.cy_frac:.2f})"
            )
        elif isinstance(it, HighlightDeco):
            print(
                f"  - highlight  {it.width}x{it.height}px  {it.angle:+.0f}\u00b0  "
                f"@({it.cx_frac:.2f},{it.cy_frac:.2f})"
            )
        elif isinstance(it, CircleDeco):
            print(
                f"  - circle     r={it.radius}px  @({it.cx_frac:.2f},{it.cy_frac:.2f})"
            )
        elif isinstance(it, LineDeco):
            print(
                f"  - line       {it.length}px  {it.angle:+.0f}\u00b0  "
                f"@({it.cx_frac:.2f},{it.cy_frac:.2f})"
            )
        elif isinstance(it, RectDeco):
            print(
                f"  - rectangle  {it.width}x{it.height}px  {it.angle:+.0f}\u00b0  "
                f"@({it.cx_frac:.2f},{it.cy_frac:.2f})"
            )
        else:
            print(
                f"  - text       {it.text!r}  {it.font_size}px  "
                f"@({it.cx_frac:.2f},{it.cy_frac:.2f})"
            )

    if args.out:
        out = args.out
    else:
        Path("temp").mkdir(parents=True, exist_ok=True)
        out = "temp/decorate_test_output." + ("mp4" if args.duration > 0 else "png")

    if args.duration > 0:
        make_decorated_clip(args.base, items, out, duration=args.duration)
    else:
        composite_text_decorations(args.base, items, out)
    print(f"[decorate] OK wrote {out}")


if __name__ == "__main__":
    _main()
