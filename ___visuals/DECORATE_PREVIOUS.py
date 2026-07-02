"""
DECORATE_PREVIOUS.py
====================
Interactive, per-scene DECORATION of the PREVIOUS scene's image, performed
AFTER all stock/AI picks are made and BEFORE Ken Burns — exactly like
MANUAL_STOCK_PLACEMENT (placement / zoom). This is the rendering primitive
behind the `decorate_previous` media type.

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

import argparse
import math
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageTk
import tkinter as tk
from tkinter import messagebox

# Reuse the display-fit + frame-extract helpers + Pillow resample shim from the
# sibling manual stage so the two GUIs behave identically.
from ___visuals.MANUAL_STOCK_PLACEMENT import _RESAMPLE, _fit_display, extract_frame

# Reuse the EXACT font discovery + pixelation from the words-on-screen renderer
# so decorations match STICKMAN_TEXT_OVERLAY. Fallbacks keep this file usable
# on its own (no circular import: WORDS_ON_SCREEN doesn't import this module).
try:
    from ___visuals.WORDS_ON_SCREEN import _find_pixel_font, _find_font, _pixelate_image
except Exception:                                              # pragma: no cover
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
TEXT_COLOR:    tuple[int, int, int] = (245, 245, 245)   # near-white
OUTLINE_COLOR: tuple[int, int, int] = (18, 18, 22)      # dark edge
SHADOW_COLOR:  tuple[int, int, int] = (0, 0, 0)
SHADOW_ALPHA:  int = 150                                 # 0 = off … 255

# --- text ---
TEXT_STROKE_FRAC:        float = 0.06   # outline thickness, ×font_size
TEXT_SHADOW_OFFSET_FRAC: float = 0.06   # drop-shadow offset, ×font_size
PIXEL_BLOCK_WHEN_NO_PIXEL_FONT: int = 2 # fallback chunk size (was 3 → less pixelated)
DEFAULT_FONT_FRAC: float = 0.07         # default glyph height ≈ 7% of base height
FONT_STEP_PX:      int   = 6
MIN_FONT_PX:       int   = 8
MAX_FONT_PX:       int   = 1000

# --- arrow ---
ARROW_SHAFT_FRAC:   float = 0.18   # shaft thickness, ×length
ARROW_HEAD_LEN_FRAC: float = 0.42  # head length, ×length
ARROW_HEAD_H_FRAC:  float = 0.46   # head width, ×length
ARROW_STROKE_FRAC:  float = 0.020  # outline thickness, ×length
ARROW_SHADOW_FRAC:  float = 0.022  # shadow offset, ×length
ARROW_PIXEL_DIVS:   int   = 22     # ~ number of pixel blocks along the length
DEFAULT_ARROW_FRAC: float = 0.18   # default length ≈ 18% of base width
ARROW_STEP_PX:      int   = 12
ARROW_ANGLE_STEP:   float = 10.0   # degrees per ←/→ key press
MIN_ARROW_PX:       int   = 16
MAX_ARROW_PX:       int   = 1500

# --- highlight ---
HIGHLIGHT_BRIGHTEN:     float = 1.16   # brightness ×factor INSIDE the box (slight)
HIGHLIGHT_DARKEN:       float = 0.84   # brightness ×factor OUTSIDE (subtle)
HIGHLIGHT_FEATHER_FRAC: float = 0.012  # soft edge radius, ×min(base w, h)

# --- line / underline (point-to-point, REALLY thin — like the circle ring) ---
LINE_THICK_FRAC:   float = 0.0055  # white core thickness, ×length (very thin)
LINE_OUTLINE_FRAC: float = 0.34    # dark edge thickness, ×core thickness
LINE_SHADOW_FRAC:  float = 0.010   # shadow offset, ×length
MIN_LINE_PX:       int   = 8
MAX_LINE_PX:       int   = 2000

# --- circle (outline ring) ---
CIRCLE_THICK_FRAC:   float = 0.045  # white ring thickness, ×radius (thin)
CIRCLE_OUTLINE_FRAC: float = 0.34   # dark edge thickness, ×ring thickness
CIRCLE_SHADOW_FRAC:  float = 0.050  # shadow offset, ×radius
MIN_CIRCLE_PX:       int   = 8
MAX_CIRCLE_PX:       int   = 3000

# --- rectangle (outline border) ---
RECT_THICK_FRAC:   float = 0.025   # white border thickness, ×min(w,h) (thin)
RECT_OUTLINE_FRAC: float = 0.34    # dark edge thickness, ×border thickness
RECT_SHADOW_FRAC:  float = 0.018   # shadow offset, ×min(w,h)
MIN_RECT_PX:       int   = 10
MAX_RECT_PX:       int   = 4000

# --- shape stroke thickness (used for circle, line, rect + / - buttons) ---
DEFAULT_THICKNESS: int = 2
MIN_THICKNESS:     int = 1
MAX_THICKNESS:     int = 60
THICKNESS_STEP_PX: int = 2   # increment step for shape thickness

# Subtle pixelation for the new shapes (matches the gentle text/arrow look).
SHAPE_PIXEL_DIVS: int = 40   # ~ blocks along the longest dimension
SHAPE_PIXEL_MAX:  int = 2    # hard cap so the effect stays "slight"

GHOST_ALPHA: int = 150       # opacity of the live "drop me here" ghost

# Slight nudging of the last-placed item (arrow keys + on-screen d-pad).
NUDGE_FRAC:       float = 0.004   # fraction of the dimension per press (~slight)
NUDGE_FRAC_BIG:   float = 0.020   # Shift+arrow = a bigger step

# Custom on-canvas cursor (thin neutral ring + small pinpoint dot).
CURSOR_RADIUS:        int = 12
CURSOR_DOT_R:         int = 2
CURSOR_RING_COLOR:    str = "#d7dde4"   # soft off-white (was bright cyan)
CURSOR_RING_BG_COLOR: str = "#10131a"   # thin dark backing for contrast
CURSOR_DOT_COLOR:     str = "#d7dde4"

# Live (vector) draw-preview accents.
DRAW_PREVIEW_COLOR: str = "#7CFC00"   # green, matches the active-item outline
DRAW_GUIDE_COLOR:   str = "#5ad1ff"   # cyan, matches the dial

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


# ===========================================================================
# Data
# ===========================================================================

@dataclass(eq=False)         # eq=False → identity semantics (so `x in list` is `is`)
class TextDeco:
    """One text decoration, relative to the BASE image."""
    text: str
    font_size: int           # glyph height in BASE-image px
    cx_frac: float           # center x as a fraction of base width, 0..1
    cy_frac: float           # center y as a fraction of base height, 0..1


@dataclass(eq=False)
class ArrowDeco:
    """One arrow decoration, relative to the BASE image."""
    length: int              # arrow length in BASE-image px
    angle: float             # degrees, 0 = pointing right, +clockwise (screen)
    cx_frac: float
    cy_frac: float


@dataclass(eq=False)
class HighlightDeco:
    """A rectangular highlight region. Stored exactly like RectDeco (width,
    height, angle, centre) so it uses the same draw-click flow."""
    width: int               # along the direction axis, BASE-image px
    height: int              # perpendicular, BASE-image px
    angle: float             # degrees, 0 = width is horizontal, +clockwise
    cx_frac: float
    cy_frac: float


@dataclass(eq=False)
class CircleDeco:
    """A thin outline circle, relative to the BASE image. Drawn centered like a
    sprite, so it moves / resizes with the shared machinery."""
    radius: int              # ring radius in BASE-image px
    cx_frac: float
    cy_frac: float
    thickness: int = DEFAULT_THICKNESS  # ring thickness in BASE-image px


@dataclass(eq=False)
class LineDeco:
    """A straight line / underline. Stored like an arrow (center + length +
    angle) so it slots into the same sprite machinery; the two click endpoints
    reconstruct exactly from the midpoint + length + angle."""
    length: int              # line length in BASE-image px
    angle: float             # degrees, 0 = horizontal, +clockwise (screen)
    cx_frac: float           # midpoint x
    cy_frac: float           # midpoint y
    thickness: int = DEFAULT_THICKNESS  # line thickness in BASE-image px


@dataclass(eq=False)
class RectDeco:
    """A thin outline rectangle, relative to the BASE image. `width` runs along
    the chosen direction axis; `angle` rotates the whole box (screen, +cw)."""
    width: int               # along the direction axis, BASE-image px
    height: int              # perpendicular, BASE-image px
    angle: float             # degrees, 0 = width is horizontal, +clockwise
    cx_frac: float
    cy_frac: float
    thickness: int = DEFAULT_THICKNESS  # border thickness in BASE-image px


# ===========================================================================
# (De)serialisation for resume — discriminated by a "type" field.
# ===========================================================================

def deco_to_dict(d) -> dict:
    if isinstance(d, ArrowDeco):
        return {"type": "arrow", "length": d.length, "angle": d.angle,
                "cx_frac": d.cx_frac, "cy_frac": d.cy_frac}
    if isinstance(d, HighlightDeco):
        return {"type": "highlight", "width": d.width, "height": d.height,
                "angle": d.angle, "cx_frac": d.cx_frac, "cy_frac": d.cy_frac}
    if isinstance(d, CircleDeco):
        return {"type": "circle", "radius": d.radius,
                "cx_frac": d.cx_frac, "cy_frac": d.cy_frac,
                "thickness": d.thickness}
    if isinstance(d, RectDeco):
        return {"type": "rect", "width": d.width, "height": d.height,
                "angle": d.angle, "cx_frac": d.cx_frac, "cy_frac": d.cy_frac,
                "thickness": d.thickness}
    if isinstance(d, LineDeco):
        return {"type": "line", "length": d.length, "angle": d.angle,
                "cx_frac": d.cx_frac, "cy_frac": d.cy_frac,
                "thickness": d.thickness}
    return {"type": "text", "text": d.text, "font_size": d.font_size,
            "cx_frac": d.cx_frac, "cy_frac": d.cy_frac}


def deco_from_dict(r: dict):
    t = r.get("type")
    if t == "highlight" or ("width" in r and "height" in r and "angle" in r and "x0_frac" not in r):
        return HighlightDeco(int(r["width"]), int(r["height"]), float(r["angle"]),
                             float(r["cx_frac"]), float(r["cy_frac"]))
    if t == "circle" or ("radius" in r):
        return CircleDeco(int(r["radius"]),
                          float(r["cx_frac"]), float(r["cy_frac"]),
                          int(r.get("thickness", DEFAULT_THICKNESS)))
    if t == "rect" or ("width" in r and "height" in r):
        return RectDeco(int(r["width"]), int(r["height"]), float(r["angle"]),
                        float(r["cx_frac"]), float(r["cy_frac"]),
                        int(r.get("thickness", DEFAULT_THICKNESS)))
    # Line is stored like an arrow (length+angle); disambiguate by "type" FIRST.
    if t == "line":
        return LineDeco(int(r["length"]), float(r["angle"]),
                        float(r["cx_frac"]), float(r["cy_frac"]),
                        int(r.get("thickness", DEFAULT_THICKNESS)))
    if t == "arrow" or ("length" in r and "text" not in r):
        return ArrowDeco(int(r["length"]), float(r["angle"]),
                         float(r["cx_frac"]), float(r["cy_frac"]))
    # Backward-compatible: old saved files were bare text dicts (no "type").
    return TextDeco(r["text"], int(r["font_size"]),
                    float(r["cx_frac"]), float(r["cy_frac"]))


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

def render_text_image(text: str, font_size: int, *,
                      pixel_block: int | None = None,
                      color: tuple[int, int, int] = TEXT_COLOR,
                      shadow: bool = True,
                      outline: bool = True) -> Image.Image:
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
        d.text((ox + so, oy + so), text, font=font,
               fill=SHADOW_COLOR + (SHADOW_ALPHA,),
               stroke_width=stroke, stroke_fill=SHADOW_COLOR + (SHADOW_ALPHA,))

    if stroke > 0:
        d.text((ox, oy), text, font=font, fill=tuple(color) + (255,),
               stroke_width=stroke, stroke_fill=OUTLINE_COLOR + (255,))
    else:
        d.text((ox, oy), text, font=font, fill=tuple(color) + (255,))

    if block > 1:
        img = _pixelate_image(img.convert("RGBA"), block)
    return img


# ===========================================================================
# Arrow rendering (pixelated, to match the text look)
# ===========================================================================

def render_arrow_image(length: int, angle_deg: float, *,
                       pixel_block: int | None = None,
                       color: tuple[int, int, int] = TEXT_COLOR,
                       shadow: bool = True,
                       outline: bool = True) -> Image.Image:
    """Render a pixelated arrow (white, dark outline + shadow) of `length` px,
    rotated to point at `angle_deg` (0 = right, +clockwise in screen coords)."""
    L = max(MIN_ARROW_PX, int(length))
    shaft_h = max(2, round(L * ARROW_SHAFT_FRAC))
    head_h = max(4, round(L * ARROW_HEAD_H_FRAC))
    head_len = max(2, round(L * ARROW_HEAD_LEN_FRAC))
    block = (max(1, pixel_block) if pixel_block is not None
             else max(1, round(L / ARROW_PIXEL_DIVS)))
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
        d.polygon([(px + so, py + so) for px, py in pts],
                  fill=SHADOW_COLOR + (SHADOW_ALPHA,))
    try:
        if stroke > 0:
            d.polygon(pts, fill=tuple(color) + (255,),
                      outline=OUTLINE_COLOR + (255,), width=stroke)
        else:
            d.polygon(pts, fill=tuple(color) + (255,))
    except TypeError:                       # very old Pillow: no polygon width=
        d.polygon(pts, fill=tuple(color) + (255,), outline=OUTLINE_COLOR + (255,))

    if block > 1:
        img = _pixelate_image(img.convert("RGBA"), block)

    if angle_deg:
        img = img.rotate(-float(angle_deg), expand=True,
                         resample=Image.NEAREST if block > 1 else Image.BICUBIC)
    return img


# ===========================================================================
# Line / circle / rectangle rendering (thin, gently pixelated to match)
# ===========================================================================

def render_line_image(length: int, angle_deg: float, thickness: int = DEFAULT_THICKNESS, *,
                      pixel_block: int | None = None,
                      color: tuple[int, int, int] = TEXT_COLOR,
                      shadow: bool = True,
                      outline: bool = True) -> Image.Image:
    """Render a thin, slightly-pixelated line (white core, dark edge + shadow)
    of `length` px, rotated to `angle_deg` (0 = horizontal, +clockwise)."""
    L = max(MIN_LINE_PX, int(length))
    core = max(2, int(thickness))
    stroke = max(1, round(core * LINE_OUTLINE_FRAC)) if outline else 0
    so = max(1, round(core * 0.75)) if shadow else 0
    full_h = core + 2 * stroke
    block = (max(1, pixel_block) if pixel_block is not None
             else _shape_block(L, full_h))

    margin = stroke + so + block + 4
    W = L + 2 * margin
    H = full_h + 2 * margin
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    x0 = margin
    x1 = margin + L
    cy = margin + full_h / 2

    def _bar(half_h, fill, dx=0, dy=0):
        d.rectangle([x0 + dx, cy - half_h + dy, x1 + dx, cy + half_h + dy],
                    fill=fill)

    if shadow and SHADOW_ALPHA > 0:
        _bar(full_h / 2, SHADOW_COLOR + (SHADOW_ALPHA,), so, so)
    if stroke > 0:
        _bar(core / 2 + stroke, OUTLINE_COLOR + (255,))    # dark edge band
    _bar(core / 2, tuple(color) + (255,))                  # white core

    if block > 1:
        img = _pixelate_image(img.convert("RGBA"), block)
    if angle_deg:
        img = img.rotate(-float(angle_deg), expand=True,
                         resample=Image.NEAREST if block > 1 else Image.BICUBIC)
    return img


def render_circle_image(radius: int, thickness: int = DEFAULT_THICKNESS, *,
                        pixel_block: int | None = None,
                        color: tuple[int, int, int] = TEXT_COLOR,
                        shadow: bool = True,
                        outline: bool = True) -> Image.Image:
    """Render a thin, slightly-pixelated outline ring (white core with a dark
    edge + shadow) of the given `radius` px."""
    R = max(MIN_CIRCLE_PX, int(radius))
    core = max(2, int(thickness))
    stroke = max(1, round(core * CIRCLE_OUTLINE_FRAC)) if outline else 0
    so = max(1, round(core * 0.75)) if shadow else 0
    band = core + 2 * stroke
    block = (max(1, pixel_block) if pixel_block is not None
             else _shape_block(2 * R, band))

    margin = so + block + 4 + band
    size = 2 * R + 2 * margin
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = margin + R

    def _ring(rad, w, fill, dx=0, dy=0):
        bb = [cx - rad + dx, cy - rad + dy, cx + rad + dx, cy + rad + dy]
        try:
            d.ellipse(bb, outline=fill, width=max(1, int(w)))
        except TypeError:                       # ancient Pillow: no width=
            d.ellipse(bb, outline=fill)

    if shadow and SHADOW_ALPHA > 0:
        _ring(R, band, SHADOW_COLOR + (SHADOW_ALPHA,), so, so)
    if stroke > 0:
        _ring(R, band, OUTLINE_COLOR + (255,))      # dark band (both edges)
    _ring(R - stroke, core, tuple(color) + (255,))  # white core, inset

    if block > 1:
        img = _pixelate_image(img.convert("RGBA"), block)
    return img


def render_rect_image(width: int, height: int, angle_deg: float, thickness: int = DEFAULT_THICKNESS, *,
                      pixel_block: int | None = None,
                      color: tuple[int, int, int] = TEXT_COLOR,
                      shadow: bool = True,
                      outline: bool = True) -> Image.Image:
    """Render a thin, slightly-pixelated outline rectangle (white border with a
    dark edge + shadow) of `width` × `height` px, rotated to `angle_deg`
    (0 = width horizontal, +clockwise)."""
    W0 = max(MIN_RECT_PX, int(width))
    H0 = max(MIN_RECT_PX, int(height))
    core = max(2, int(thickness))
    stroke = max(1, round(core * RECT_OUTLINE_FRAC)) if outline else 0
    so = max(1, round(core * 0.75)) if shadow else 0
    band = core + 2 * stroke
    block = (max(1, pixel_block) if pixel_block is not None
             else _shape_block(max(W0, H0), band))

    margin = so + block + 4 + band
    cW = W0 + 2 * margin
    cH = H0 + 2 * margin
    img = Image.new("RGBA", (cW, cH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y0 = margin, margin
    x1, y1 = margin + W0, margin + H0

    def _hollow(cx0, cy0, cx1, cy1, c, s, c_fill, s_fill):
        """Draw a hollow rectangle using 4 solid bars to prevent edge overlap."""
        if cx1 <= cx0 or cy1 <= cy0: return
        if s > 0:
            d.rectangle([cx0, cy0, cx1, cy0+s], fill=s_fill)
            d.rectangle([cx0, cy1-s, cx1, cy1], fill=s_fill)
            d.rectangle([cx0, cy0, cx0+s, cy1], fill=s_fill)
            d.rectangle([cx1-s, cy0, cx1, cy1], fill=s_fill)
        if c > 0:
            ix0, iy0 = cx0+s, cy0+s
            ix1, iy1 = cx1-s, cy1-s
            if ix0 > ix1: ix0, ix1 = ix1, ix0
            if iy0 > iy1: iy0, iy1 = iy1, iy0
            d.rectangle([ix0, iy0, ix1, iy0+c], fill=c_fill)
            d.rectangle([ix0, iy1-c, ix1, iy1], fill=c_fill)
            d.rectangle([ix0, iy0, ix0+c, iy1], fill=c_fill)
            d.rectangle([ix1-c, iy0, ix1, iy1], fill=c_fill)

    if shadow and SHADOW_ALPHA > 0:
        _hollow(x0+so, y0+so, x1+so, y1+so, core, stroke,
                SHADOW_COLOR + (SHADOW_ALPHA,), SHADOW_COLOR + (SHADOW_ALPHA,))
    if stroke > 0:
        _hollow(x0, y0, x1, y1, core, stroke, tuple(color) + (255,), OUTLINE_COLOR + (255,))
    else:
        _hollow(x0, y0, x1, y1, core, 0, tuple(color) + (255,), tuple(color) + (255,))

    if block > 1:
        img = _pixelate_image(img.convert("RGBA"), block)
    if angle_deg:
        img = img.rotate(-float(angle_deg), expand=True,
                         resample=Image.NEAREST if block > 1 else Image.BICUBIC)
    return img


def _render_item(item) -> Image.Image:
    """Render a SPRITE decoration (text/arrow/circle/line/rect) at BASE
    resolution. Highlights are not sprites — they're applied to the base by
    _apply_highlights."""
    if isinstance(item, ArrowDeco):
        return render_arrow_image(item.length, item.angle)
    if isinstance(item, CircleDeco):
        return render_circle_image(item.radius, item.thickness)
    if isinstance(item, LineDeco):
        return render_line_image(item.length, item.angle, item.thickness)
    if isinstance(item, RectDeco):
        return render_rect_image(item.width, item.height, item.angle, item.thickness)
    return render_text_image(item.text, item.font_size)


# ===========================================================================
# Highlight rendering (brighten inside the boxes, darken outside)
# ===========================================================================

def _apply_highlights(rgb: Image.Image, highlights) -> Image.Image:
    """Return a copy of `rgb` with the union of (potentially rotated) highlight
    boxes brightened and everything else darkened, with a soft feathered edge."""
    w, h = rgb.size
    bright = ImageEnhance.Brightness(rgb).enhance(HIGHLIGHT_BRIGHTEN)
    dark = ImageEnhance.Brightness(rgb).enhance(HIGHLIGHT_DARKEN)

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
    mask = mask.filter(ImageFilter.GaussianBlur(feather))
    return Image.composite(bright, dark, mask)   # white(255)=bright, black=dark


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


def composite_text_decorations(base_image_path: str, items,
                               output_path: str) -> str:
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
        layer.alpha_composite(
            im, (round(cx - im.width / 2), round(cy - im.height / 2)))

    out = Image.alpha_composite(base, layer).convert("RGB")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    out.save(output_path)
    return output_path


def make_decorated_clip(base_image_path: str, items, output_path: str,
                        duration: float, fps: int = FPS) -> str:
    """Composite the decorations and encode a STATIC MP4 of `duration` seconds."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tmp_png = tf.name
    try:
        composite_text_decorations(base_image_path, items, tmp_png)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-framerate", str(fps), "-i", tmp_png,
            "-t", f"{float(duration):.3f}",
            "-vf", f"fps={fps},format=yuv420p,scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-an", output_path,
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

    def __init__(self, base_path, title, initial):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            raise RuntimeError(
                "Could not open a display for the decorate GUI — this step "
                f"needs a desktop session (tkinter said: {exc})")
        self.root.title(title)
        self.root.configure(bg="#1e1e24")

        self.base = _load_base_image(base_path)
        self.bw, self.bh = self.base.size

        self.scale, self.disp_w, self.disp_h = _fit_display(
            self.bw, self.bh, self.root.winfo_screenwidth(),
            self.root.winfo_screenheight())
        self.base_disp = self.base.resize((self.disp_w, self.disp_h),
                                          _RESAMPLE).convert("RGBA")

        # Defaults (retained between items).
        self.cur_font_size = max(MIN_FONT_PX, round(DEFAULT_FONT_FRAC * self.bh))
        self.cur_arrow_len = max(MIN_ARROW_PX, round(DEFAULT_ARROW_FRAC * self.bw))
        self.cur_arrow_angle = 0.0

        # State.
        self.items: list = list(initial or [])
        self.active = None               # last-placed item == "selected" target
        self._pending = None             # a floating item (arrow/text) → click to drop
        self._rearm = None               # tool to re-arm after a placement | None
        self.mode = "interactive"        # "interactive" | "typing"
        self._dial_shown = False
        self._mode_draw_highlight = False
        self._box_start = None

        # Multi-click "draw" tools (circle / line / rectangle).
        self._draw_tool = None           # None | "circle" | "line" | "rect"
        self._draw_stage = 0             # which click we're waiting for
        self._draw_anchor = None         # (x, y) in display coords | None
        self._draw_angle = 0.0           # radians, screen coords (rect direction)
        self._last_xy = (0, 0)           # last cursor pos (for Enter-to-confirm)

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
        self._bind_keys()
        self._rebuild_composite()
        self._update_controls()
        self._set_status("Add text, an arrow, a highlight, a circle, "
                         "a line, or a rectangle to start.")

        self.root.update_idletasks()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        self.canvas = tk.Canvas(self.root, width=self.disp_w, height=self.disp_h,
                                bg="#000000", highlightthickness=0, cursor="none")
        self.canvas.pack(side="left", padx=10, pady=10)
        self.canvas_item = self.canvas.create_image(0, 0, anchor="nw", image=self._blank)
        self.ghost_item  = self.canvas.create_image(0, 0, anchor="nw", image=self._blank, state="hidden")
        self.box_item    = self.canvas.create_rectangle(0, 0, 0, 0, dash=(6, 4),
                                                        outline="#ffd166", width=2, state="hidden")
        # Live vector previews for the multi-click draw tools.
        self.draw_oval  = self.canvas.create_oval(0, 0, 0, 0, dash=(5, 3),
                                                  outline=DRAW_PREVIEW_COLOR, width=2, state="hidden")
        self.draw_line  = self.canvas.create_line(0, 0, 0, 0,
                                                  fill=DRAW_PREVIEW_COLOR, width=3, state="hidden")
        self.draw_guide = self.canvas.create_line(0, 0, 0, 0, dash=(4, 3),
                                                  fill=DRAW_GUIDE_COLOR, width=1, state="hidden")
        self.draw_poly  = self.canvas.create_polygon(0, 0, 0, 0, 0, 0, 0, 0,
                                                     fill="", outline=DRAW_PREVIEW_COLOR,
                                                     width=2, state="hidden")
        self.draw_dot   = self.canvas.create_oval(0, 0, 0, 0,
                                                  fill=DRAW_PREVIEW_COLOR, outline="", state="hidden")
        self.cur_ring_bg = self.canvas.create_oval(0, 0, 0, 0, outline=CURSOR_RING_BG_COLOR, width=2, state="hidden")
        self.cur_ring    = self.canvas.create_oval(0, 0, 0, 0, outline=CURSOR_RING_COLOR, width=1, state="hidden")
        self.cur_dot     = self.canvas.create_oval(0, 0, 0, 0, fill=CURSOR_DOT_COLOR, outline=CURSOR_RING_BG_COLOR, state="hidden")
        self.canvas.bind("<Motion>",         self._on_motion)
        self.canvas.bind("<Leave>",          self._on_leave)
        self.canvas.bind("<ButtonPress-1>",  self._on_press)
        self.canvas.bind("<B1-Motion>",      self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        side = tk.Frame(self.root, bg="#1e1e24", width=360)
        side.pack(side="right", fill="y", padx=(0, 10), pady=10)
        side.pack_propagate(False)
        self._side = side

        tk.Label(side, text="DECORATE THE\nPREVIOUS IMAGE:", bg="#1e1e24",
                 fg="#dddddd", justify="left",
                 font=("Arial", 11, "bold")).pack(anchor="w", pady=(2, 8))

        # Toolbar (three rows — room for more tools later).
        tb1 = tk.Frame(side, bg="#1e1e24")
        tb1.pack(anchor="w", fill="x", pady=(0, 4))
        tk.Button(tb1, text="✏  Add text", command=self._add_text,
                  font=("Arial", 11, "bold"), bg="#3a3a46", fg="white",
                  width=11).pack(side="left")
        tk.Button(tb1, text="➤  Add arrow", command=self._add_arrow,
                  font=("Arial", 11, "bold"), bg="#3a3a46", fg="white",
                  width=11).pack(side="left", padx=(6, 0))
        tb2 = tk.Frame(side, bg="#1e1e24")
        tb2.pack(anchor="w", fill="x", pady=(0, 4))
        tk.Button(tb2, text="✦  Add highlight", command=self._add_highlight,
                  font=("Arial", 11, "bold"), bg="#3a3a46", fg="white",
                  width=11).pack(side="left")
        tk.Button(tb2, text="◯  Add circle", command=self._add_circle,
                  font=("Arial", 11, "bold"), bg="#3a3a46", fg="white",
                  width=11).pack(side="left", padx=(6, 0))
        tb3 = tk.Frame(side, bg="#1e1e24")
        tb3.pack(anchor="w", fill="x", pady=(0, 4))
        tk.Button(tb3, text="\u2500  Add line", command=self._add_line,
                  font=("Arial", 11, "bold"), bg="#3a3a46", fg="white",
                  width=11).pack(side="left")
        tk.Button(tb3, text="\u25ad  Add rectangle", command=self._add_rectangle,
                  font=("Arial", 11, "bold"), bg="#3a3a46", fg="white",
                  width=11).pack(side="left", padx=(6, 0))

        # Text-entry group (hidden unless typing).
        self.entry_frame = tk.Frame(side, bg="#1e1e24")
        self.text_label = tk.Label(self.entry_frame, text="Type your text:",
                                   bg="#1e1e24", fg="#bbbbbb", font=("Arial", 10))
        self.text_label.pack(anchor="w")
        self.text_var = tk.StringVar(value="")
        self.entry = tk.Entry(self.entry_frame, textvariable=self.text_var,
                              width=28, font=("Arial", 13),
                              highlightthickness=1, highlightbackground="#1e1e24",
                              highlightcolor="#5ad1ff")
        self.entry.pack(anchor="w", pady=(2, 4))
        self.entry.bind("<Return>", lambda e: self._close_typing_box())
        self.entry.bind("<Escape>", lambda e: self._cancel_typing())
        self.entry.bind("<KeyRelease>", self._on_text_change)
        erow = tk.Frame(self.entry_frame, bg="#1e1e24")
        erow.pack(anchor="w")
        tk.Button(erow, text="Done typing (click to place)", command=self._close_typing_box,
                  font=("Arial", 11, "bold"), bg="#2e7d32", fg="white").pack(side="left")
        tk.Button(erow, text="Cancel", command=self._cancel_typing,
                  font=("Arial", 11)).pack(side="left", padx=(6, 0))

        # Arrow-direction dial (hidden unless an arrow is the active/pending item).
        self.dial_frame = tk.Frame(side, bg="#1e1e24")
        tk.Label(self.dial_frame, text="Arrow direction (drag to aim):",
                 bg="#1e1e24", fg="#bbbbbb", font=("Arial", 10)).pack(anchor="w")
        self.dial = tk.Canvas(self.dial_frame, width=150, height=150,
                              bg="#2a2a33", highlightthickness=0)
        self.dial.pack(anchor="w", pady=(2, 2))
        self.dial.bind("<Button-1>",  self._dial_event)
        self.dial.bind("<B1-Motion>", self._dial_event)
        self.angle_var = tk.StringVar(value="0°")
        tk.Label(self.dial_frame, textvariable=self.angle_var, bg="#1e1e24",
                 fg="#5ad1ff", font=("Arial", 10, "bold")).pack(anchor="w")

        # "Selected item" tweak panel — nudge d-pad.
        self.tweak_frame = tk.Frame(side, bg="#1e1e24")
        tk.Label(self.tweak_frame, text="Move the selected item:",
                 bg="#1e1e24", fg="#bbbbbb", font=("Arial", 10)).pack(anchor="w")
        trow = tk.Frame(self.tweak_frame, bg="#1e1e24")
        trow.pack(anchor="w", pady=(2, 0))
        pad = tk.Frame(trow, bg="#1e1e24")
        pad.pack(side="left")
        bopt = dict(font=("Arial", 12, "bold"), bg="#3a3a46", fg="white",
                    width=3, height=1)
        tk.Button(pad, text="\u2191", command=lambda: self._nudge(0, -1),
                  **bopt).grid(row=0, column=1, padx=1, pady=1)
        tk.Button(pad, text="\u2190", command=lambda: self._nudge(-1, 0),
                  **bopt).grid(row=1, column=0, padx=1, pady=1)
        tk.Label(pad, text="\u2022", bg="#1e1e24", fg="#666",
                 font=("Arial", 12)).grid(row=1, column=1)
        tk.Button(pad, text="\u2192", command=lambda: self._nudge(1, 0),
                  **bopt).grid(row=1, column=2, padx=1, pady=1)
        tk.Button(pad, text="\u2193", command=lambda: self._nudge(0, 1),
                  **bopt).grid(row=2, column=1, padx=1, pady=1)

        # Place-mode controls (Edit text + size).
        self.controls = tk.Frame(side, bg="#1e1e24")
        self.controls.pack(anchor="w", fill="x", pady=(6, 2))
        self.edit_btn = tk.Button(self.controls, text="✎  Edit text",
                                  command=self._edit_text, font=("Arial", 11),
                                  width=14, state="disabled")
        self.edit_btn.pack(anchor="w", pady=(0, 6))

        srow = tk.Frame(self.controls, bg="#1e1e24")
        srow.pack(anchor="w")
        tk.Label(srow, text="Size:", bg="#1e1e24", fg="#dddddd",
                 font=("Arial", 11)).pack(side="left", padx=(0, 6))
        tk.Button(srow, text="\u2212", command=self._size_dec,
                  font=("Arial", 15, "bold"), width=3).pack(side="left")
        self.size_var = tk.StringVar(value=str(self.cur_font_size))
        se = tk.Entry(srow, textvariable=self.size_var, width=5,
                      justify="center", font=("Arial", 14))
        se.pack(side="left", padx=4)
        se.bind("<Return>",   self._size_commit)
        se.bind("<FocusOut>", self._size_commit)
        tk.Button(srow, text="+", command=self._size_inc,
                  font=("Arial", 15, "bold"), width=3).pack(side="left")
        tk.Label(srow, text="px", bg="#1e1e24", fg="#999999",
                 font=("Arial", 10)).pack(side="left", padx=(6, 0))

        self.count_var = tk.StringVar(value="Items: 0")
        tk.Label(side, textvariable=self.count_var, bg="#1e1e24", fg="#7CFC00",
                 font=("Arial", 13, "bold")).pack(anchor="w", pady=(8, 0))

        self.status_var = tk.StringVar(value="")
        tk.Label(side, textvariable=self.status_var, bg="#1e1e24", fg="#5ad1ff",
                 font=("Arial", 10, "bold"), justify="left",
                 wraplength=330).pack(anchor="w", pady=(4, 8))

        tk.Label(side, justify="left", bg="#1e1e24", fg="#bbbbbb", font=("Arial", 10),
                 text=("Add text → type → click to drop (auto-ready).\n"
                       "Add arrow → aim on the dial → click to drop.\n"
                       "Add highlight → drag a box over the area.\n"
                       "Circle / line / rect → click through each step.\n"
                       "Each tool stays on, so click again = a NEW item.\n\n"
                       "  A          add text\n"
                       "  R          add arrow\n"
                       "  H          add highlight\n"
                       "  C          add circle\n"
                       "  L          add line\n"
                       "  B          add rectangle (box)\n"
                       "  E          edit the selected text\n"
                       "  \u2190\u2191\u2192\u2193   nudge the selected item (Shift = more)\n"
                       "  , / .      rotate selected arrow / line / rect\n"
                       "  + / \u2212      size  (type a number for exact)\n"
                       "  Ctrl-Z / U undo last item · Bksp cancel shape\n"
                       "  Enter / D  confirm draw step \u00b7 else finish\n"
                       "  Esc / Q    exit (resume later)")).pack(anchor="w", pady=(2, 8))

        btns = tk.Frame(side, bg="#1e1e24")
        btns.pack(anchor="w", side="bottom", pady=(8, 2))
        self.done_btn = tk.Button(btns, text="✓ Finish edits\n& move on",
                                  command=self._done, font=("Arial", 11, "bold"),
                                  bg="#2e7d32", fg="white", width=13)
        self.done_btn.pack(side="left", padx=(0, 6))
        self.undo_btn = tk.Button(btns, text="\u21b6 Undo\n(Ctrl-Z)", command=self._undo,
                                  font=("Arial", 11), width=10, state="disabled")
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
        r.bind("<Left>",        self._kbd(lambda: self._nudge(-1, 0)))
        r.bind("<Right>",       self._kbd(lambda: self._nudge(1, 0)))
        r.bind("<Up>",          self._kbd(lambda: self._nudge(0, -1)))
        r.bind("<Down>",        self._kbd(lambda: self._nudge(0, 1)))
        r.bind("<Shift-Left>",  self._kbd(lambda: self._nudge(-1, 0, big=True)))
        r.bind("<Shift-Right>", self._kbd(lambda: self._nudge(1, 0, big=True)))
        r.bind("<Shift-Up>",    self._kbd(lambda: self._nudge(0, -1, big=True)))
        r.bind("<Shift-Down>",  self._kbd(lambda: self._nudge(0, 1, big=True)))
        # Rotation moved off the arrow keys onto , / .
        r.bind("<comma>",  self._kbd(lambda: self._rotate_active(-ARROW_ANGLE_STEP)))
        r.bind("<period>", self._kbd(lambda: self._rotate_active(+ARROW_ANGLE_STEP)))
        r.focus_set()

    def _kbd(self, fn):
        def handler(event):
            if isinstance(self.root.focus_get(), tk.Entry):
                return          # don't fire shortcuts while typing in a field
            return fn()
        return handler

    # -- rendering ---------------------------------------------------------
    def _disp_font(self, base_px: int) -> int:
        return max(6, round(base_px * self.scale))

    def _render_item_img(self, item, *, display: bool) -> Image.Image:
        if isinstance(item, ArrowDeco):
            L = max(8, round(item.length * self.scale)) if display else item.length
            return render_arrow_image(L, item.angle)
        if isinstance(item, CircleDeco):
            R = max(8, round(item.radius * self.scale)) if display else item.radius
            t = max(1, round(item.thickness * self.scale)) if display else item.thickness
            return render_circle_image(R, t)
        if isinstance(item, LineDeco):
            L = max(8, round(item.length * self.scale)) if display else item.length
            t = max(1, round(item.thickness * self.scale)) if display else item.thickness
            return render_line_image(L, item.angle, t)
        if isinstance(item, RectDeco):
            if display:
                w = max(10, round(item.width * self.scale))
                h = max(10, round(item.height * self.scale))
                t = max(1, round(item.thickness * self.scale)) if display else item.thickness
            else:
                w, h = item.width, item.height
                t = item.thickness
            return render_rect_image(w, h, item.angle, t)
        fs = self._disp_font(item.font_size) if display else item.font_size
        return render_text_image(item.text, fs)

    def _rebuild_composite(self):
        highlights = [it for it in self.items if isinstance(it, HighlightDeco)]
        if highlights:
            img = _apply_highlights(self.base_disp.convert("RGB"),
                                    highlights).convert("RGBA")
        else:
            img = self.base_disp.copy()
        for it in self.items:               # the floating _pending is NOT in items
            if isinstance(it, HighlightDeco):
                continue
            im = self._render_item_img(it, display=True)
            cx, cy = it.cx_frac * self.disp_w, it.cy_frac * self.disp_h
            img.alpha_composite(im, (round(cx - im.width / 2),
                                     round(cy - im.height / 2)))
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
            self._update_cursor(event.x, event.y)   # keep reticle above ghost

    def _on_leave(self, _event):
        for it in (self.cur_ring_bg, self.cur_ring, self.cur_dot):
            self.canvas.itemconfig(it, state="hidden")
        if self._pending is not None:
            self.canvas.itemconfig(self.ghost_item, state="hidden")

    def _on_press(self, event):
        # Requirement: clicking the canvas with the text tool open but nothing
        # typed must warn the user rather than silently doing nothing.
        if self.mode == "typing":
            if self._pending is not None:       # auto-ready already set it
                self._close_typing_box()
                self._place_pending(event.x, event.y)
            elif isinstance(self.active, TextDeco) and self.active in self.items and self.text_var.get().strip():
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
        self._set_status("Pick a tool to add something (text / arrow / "
                         "highlight / circle / line / rectangle).")

    def _on_drag(self, event):
        self._update_cursor(event.x, event.y)
        self._last_xy = (event.x, event.y)
        if self._draw_tool is not None and self._draw_anchor is not None:
            self._update_draw_preview((event.x, event.y))
            return

    def _on_release(self, event):
        pass  # draw tools are click-move-click, not drag-release

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
            self._pending = ArrowDeco(self.cur_arrow_len, self.cur_arrow_angle,
                                      0.5, 0.5)
            self._regen_ghost()
        else:
            self.canvas.itemconfig(self.ghost_item, state="hidden")
        self._rebuild_composite()
        self._update_controls()
        kind = type(p).__name__.replace("Deco", "").lower()
        extra = (" Click again for another." if self._pending is not None
                 else " Nudge it with the arrow keys, or add more.")
        self._set_status(f"Placed the {kind}. {len(self.items)} item(s)." + extra)

    # -- toolbar: add / edit ----------------------------------------------
    def _add_text(self):
        self._cancel_draw()
        self._mode_draw_highlight = False
        self._pending = None
        self._rearm = None              # a new text needs fresh typing each time
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
        self._rearm = "arrow"           # stays armed → click again = new arrow
        self._pending = ArrowDeco(self.cur_arrow_len, self.cur_arrow_angle, 0.5, 0.5)
        self._update_controls()
        self._regen_ghost()
        self._rebuild_composite()
        self._set_status("Aim it on the dial, set size with +/\u2212, "
                         "click to drop. Click again for another.")

    def _add_highlight(self):
        self._begin_draw("highlight")
        self._set_status("Click the CENTRE, aim the direction (default "
                         "horizontal), click; then stretch W\u00d7H and confirm.")

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
        self._set_status("Click the CENTRE, move to size the radius, then "
                         "click / Enter to confirm. Repeats for another.")

    def _add_line(self):
        self._begin_draw("line")
        self._set_status("Click the START point, then click / Enter at the END "
                         "point. Repeats for another.")

    def _add_rectangle(self):
        self._begin_draw("rect")
        self._set_status("Click the CENTRE, aim the direction (default "
                         "horizontal), click; then stretch W\u00d7H and confirm.")

    def _cancel_draw(self):
        if self._draw_tool is None:
            return
        self._draw_tool = None
        self._draw_stage = 0
        self._draw_anchor = None
        self._draw_angle = 0.0
        self._hide_draw_preview()

    def _hide_draw_preview(self):
        for it in (self.draw_oval, self.draw_line, self.draw_guide,
                   self.draw_poly, self.draw_dot):
            self.canvas.itemconfig(it, state="hidden")

    def _show_anchor_dot(self, x, y):
        r = 4
        self.canvas.coords(self.draw_dot, x - r, y - r, x + r, y + r)
        self.canvas.itemconfig(self.draw_dot, state="normal")
        self.canvas.tag_raise(self.draw_dot)

    def _finish_or_confirm(self):
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
                self._set_status("Aim the direction, then click / Enter "
                                 "(default is horizontal).")
            elif self._draw_stage == 1:
                ax, ay = self._draw_anchor
                if math.hypot(x - ax, y - ay) < 4:
                    self._draw_angle = 0.0      # barely moved → horizontal
                else:
                    deg = math.degrees(math.atan2(y - ay, x - ax))
                    snapped = round(deg / 45) * 45
                    if abs(deg - snapped) < 7:  # snap to 45s if close
                        deg = snapped
                    if abs(deg) == 180: deg = 0 # 180 is same axis as 0
                    self._draw_angle = math.radians(deg)
                self._draw_stage = 2
                self.canvas.itemconfig(self.draw_guide, state="hidden")
                self._update_draw_preview((x, y))
                self._set_status("Now stretch the width \u00d7 height, then "
                                 "click / Enter to confirm.")
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
            self.canvas.coords(self.draw_oval, ax - rad, ay - rad,
                               ax + rad, ay + rad)
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
                    if abs(deg) == 180: deg = 0
                    a = math.radians(deg)
                gl = max(self.disp_w, self.disp_h)
                ca, sa = math.cos(a), math.sin(a)
                self.canvas.coords(self.draw_guide, ax - gl * ca, ay - gl * sa,
                                   ax + gl * ca, ay + gl * sa)
                self.canvas.itemconfig(self.draw_guide, state="normal")
                self.canvas.tag_raise(self.draw_guide)
            elif self._draw_stage == 2:
                a = self._draw_angle
                ca, sa = math.cos(a), math.sin(a)
                dx, dy = x - ax, y - ay
                half_w = abs(dx * ca + dy * sa)         # along the direction
                half_h = abs(-dx * sa + dy * ca)        # perpendicular
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
            self._set_status("Too small — stretch wider / taller, then "
                             "click / Enter.")
            return
        width = max(MIN_RECT_PX, round(2 * half_w / self.scale))
        height = max(MIN_RECT_PX, round(2 * half_h / self.scale))
        deco = RectDeco(width, height, math.degrees(a),
                        ax / self.disp_w, ay / self.disp_h)
        self.items.append(deco)
        self._finish_draw_commit(deco,
                                 f"Rectangle added. {len(self.items)} item(s).")

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
            self._set_status("Too small — stretch wider / taller, then "
                             "click / Enter.")
            return
        width = max(MIN_RECT_PX, round(2 * half_w / self.scale))
        height = max(MIN_RECT_PX, round(2 * half_h / self.scale))
        deco = HighlightDeco(width, height, math.degrees(a),
                             ax / self.disp_w, ay / self.disp_h)
        self.items.append(deco)
        self._finish_draw_commit(deco,
                                 f"Highlight added. {len(self.items)} item(s).")

    def _finish_draw_commit(self, deco, msg):
        """Select the just-committed shape and RE-ARM the same tool so the next
        click starts a fresh shape (it never moves this one)."""
        self.active = deco
        self._pending = None
        self._draw_stage = 0
        self._draw_anchor = None
        self._draw_angle = 0.0
        self._hide_draw_preview()
        self._draw_tool = self._rearm        # re-arm the same draw tool
        self._update_controls()
        self._rebuild_composite()
        self._set_status(msg + "  Nudge with arrow keys / d-pad, "
                         "or click to start another.")

    # -- text typing -------------------------------------------------------
    def _open_typing(self):
        self.mode = "typing"
        if self._dial_shown:
            self.dial_frame.pack_forget()
            self._dial_shown = False
        self._highlight_entry(False)
        self.entry_frame.pack(anchor="w", fill="x", pady=(4, 2),
                              before=self.controls)
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
            "and then you will be able to place.")
        self.entry.focus_set()

    def _highlight_entry(self, on: bool):
        if on:
            self.entry.config(highlightbackground="#ff5555",
                              highlightcolor="#ff5555", highlightthickness=2)
            self.text_label.config(fg="#ff8888",
                                   text="Type your text:  ← required!")
        else:
            self.entry.config(highlightbackground="#1e1e24",
                              highlightcolor="#5ad1ff", highlightthickness=1)
            self.text_label.config(fg="#bbbbbb", text="Type your text:")

    # -- selected-item controls -------------------------------------------
    def _update_controls(self):
        """Show/hide the dial, tweak (nudge) panel, Edit + size to match
        the current pending / selected item."""
        target = self._pending if self._pending is not None else self.active
        # Dial: visible whenever a rotatable item is active/pending.
        show_dial = isinstance(target, (ArrowDeco, LineDeco, RectDeco, HighlightDeco))
        if show_dial:
            if not self._dial_shown:
                self.dial_frame.pack(anchor="w", fill="x", pady=(4, 2),
                                     before=self.controls)
                self._dial_shown = True
            self._redraw_dial()
        elif self._dial_shown:
            self.dial_frame.pack_forget()
            self._dial_shown = False
        # Tweak panel: only meaningful for a placed (selected) item.
        if self.active is not None and self.mode != "typing":
            self.tweak_frame.pack(anchor="w", fill="x", pady=(4, 2),
                                  before=self.controls)
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
        if isinstance(self._pending, (ArrowDeco, LineDeco, RectDeco, HighlightDeco)):
            return self._pending
        if isinstance(self.active, (ArrowDeco, LineDeco, RectDeco, HighlightDeco)):
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
        for a in (0, 90, 180, 270):                # cardinal ticks
            rad = math.radians(a)
            c.create_line(cx + (R - 8) * math.cos(rad), cy + (R - 8) * math.sin(rad),
                          cx + R * math.cos(rad), cy + R * math.sin(rad),
                          fill="#555", width=2)
        tgt = self._dial_target()
        ang = tgt.angle if tgt is not None else self.cur_arrow_angle
        rad = math.radians(ang)
        c.create_line(cx, cy, cx + R * math.cos(rad), cy + R * math.sin(rad),
                      fill="#5ad1ff", width=3, arrow="last")
        c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#5ad1ff", outline="")
        self.angle_var.set(f"Direction: {ang:+.0f}\u00b0")

    def _rotate_active(self, delta):
        tgt = self.active
        if isinstance(tgt, ArrowDeco):
            tgt.angle += delta
            self.cur_arrow_angle = tgt.angle
            self._redraw_dial()
        elif isinstance(tgt, (LineDeco, RectDeco)):
            tgt.angle += delta
        else:
            self._set_status("Selected item can't be rotated.")
            return
        self._rebuild_composite()

    # -- size --------------------------------------------------------------
    def _size_of(self, item) -> int:
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
        if isinstance(tgt, ArrowDeco):
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
    def _undo(self):
        # Mid-draw shape in progress → cancel just that shape.
        if self._draw_tool is not None and self._draw_anchor is not None:
            self._cancel_draw()
            self._draw_tool = self._rearm      # keep the tool armed
            self._update_controls()
            self._set_status("Cancelled the shape you were drawing.")
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

    def _done(self):
        self._cancel_draw()
        self.result = list(self.items)
        self.root.destroy()

    def _exit(self):
        self.result = None
        self.root.destroy()

    # -- misc --------------------------------------------------------------
    def _update_buttons(self):
        n = len(self.items)
        self.count_var.set(f"Items: {n}")
        self.undo_btn.config(state="normal" if n else "disabled")
        self.edit_btn.config(
            state="normal" if (isinstance(self.active, TextDeco)
                               and self.mode != "typing") else "disabled")

    def _set_status(self, msg):
        self.status_var.set(msg)

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._exit)
        self.root.mainloop()


def decorate_prev_interactive(base_image_path,
                              window_title="Decorate the previous image",
                              initial=None):
    """Open the decorate GUI. Returns list[TextDeco | ArrowDeco | HighlightDeco
    | CircleDeco | LineDeco | RectDeco] on Finish (possibly empty), or None if
    the user EXITS (resume later)."""
    app = _DecorateApp(base_image_path, window_title, initial)
    app.run()
    return app.result


# ===========================================================================
# Standalone test
# ===========================================================================

def _main():
    ap = argparse.ArgumentParser(
        description="Decorate a base image, then bake PNG/MP4.")
    ap.add_argument("base", help="base image (or video — first frame is used)")
    ap.add_argument("--duration", type=float, default=0.0,
                    help=">0 → write a static MP4 of this length, else a PNG")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    items = decorate_prev_interactive(args.base)
    if items is None:
        print("[decorate] exited without finishing.")
        sys.exit(0)
    print(f"[decorate] {len(items)} item(s):")
    for it in items:
        if isinstance(it, ArrowDeco):
            print(f"  - arrow      {it.length}px  {it.angle:+.0f}\u00b0  "
                  f"@({it.cx_frac:.2f},{it.cy_frac:.2f})")
        elif isinstance(it, HighlightDeco):
            print(f"  - highlight  {it.width}x{it.height}px  {it.angle:+.0f}\u00b0  "
                  f"@({it.cx_frac:.2f},{it.cy_frac:.2f})")
        elif isinstance(it, CircleDeco):
            print(f"  - circle     r={it.radius}px  "
                  f"@({it.cx_frac:.2f},{it.cy_frac:.2f})")
        elif isinstance(it, LineDeco):
            print(f"  - line       {it.length}px  {it.angle:+.0f}\u00b0  "
                  f"@({it.cx_frac:.2f},{it.cy_frac:.2f})")
        elif isinstance(it, RectDeco):
            print(f"  - rectangle  {it.width}x{it.height}px  {it.angle:+.0f}\u00b0  "
                  f"@({it.cx_frac:.2f},{it.cy_frac:.2f})")
        else:
            print(f"  - text       {it.text!r}  {it.font_size}px  "
                  f"@({it.cx_frac:.2f},{it.cy_frac:.2f})")

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
