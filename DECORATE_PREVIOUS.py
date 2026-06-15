"""
DECORATE_PREVIOUS.py
====================
Interactive, per-scene DECORATION of the PREVIOUS scene's image, performed
AFTER all stock/AI picks are made and BEFORE Ken Burns — exactly like
MANUAL_STOCK_PLACEMENT (placement / zoom). This is the rendering primitive
behind the `decorate_previous` media type.

Tools (the side-panel "toolbar" — more can be slotted in later):

  • ADD TEXT
      "Add text" → type → Enter / "Text ready to place ✓" → the text follows
      the cursor → click to drop. Resize with + / −. "Edit text" re-opens the
      box (works after placing too; size retained).

  • ADD ARROW
      "Add arrow" → spin the DIAL to aim (drag the needle, or ←/→ keys) →
      resize with + / − → click to drop. Direction + size are retained.

  • ADD HIGHLIGHT
      "Add highlight" → press-drag-release a box over the area to highlight.
      The boxed pixels are brightened slightly and everything else is darkened
      subtly (a soft spotlight). No size control — the box defines it.

  • FINISH EDITS AND MOVE ON
      Bakes every item onto the base and returns the placements.

Text reuses the SAME pixel font as STICKMAN_TEXT_OVERLAY / WORDS_ON_SCREEN;
arrows are pixelated to match. Everything bakes to a static MP4 in the pipeline
so the Ken Burns pass leaves the decorated frame untouched.

Public API
----------
decorate_prev_interactive(base, window_title=..., initial=None)
    -> list[TextDeco | ArrowDeco | HighlightDeco] | None  # None == EXIT
composite_text_decorations(base, items, output_path) -> str          # PNG
make_decorated_clip(base, items, output_path, duration, fps=...) -> str  # MP4
render_text_image / render_arrow_image -> PIL.Image (RGBA)
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

# Reuse the display-fit + frame-extract helpers + Pillow resample shim from the
# sibling manual stage so the two GUIs behave identically.
from MANUAL_STOCK_PLACEMENT import _RESAMPLE, _fit_display, extract_frame

# Reuse the EXACT font discovery + pixelation from the words-on-screen renderer
# so decorations match STICKMAN_TEXT_OVERLAY. Fallbacks keep this file usable
# on its own (no circular import: WORDS_ON_SCREEN doesn't import this module).
try:
    from WORDS_ON_SCREEN import _find_pixel_font, _find_font, _pixelate_image
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

GHOST_ALPHA: int = 150       # opacity of the live "drop me here" ghost

# Custom on-canvas cursor (thin neutral ring + small pinpoint dot).
CURSOR_RADIUS:        int = 12
CURSOR_DOT_R:         int = 2
CURSOR_RING_COLOR:    str = "#d7dde4"   # soft off-white (was bright cyan)
CURSOR_RING_BG_COLOR: str = "#10131a"   # thin dark backing for contrast
CURSOR_DOT_COLOR:     str = "#d7dde4"

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
    """A rectangular highlight region, as fractions of the BASE image. Inside
    is brightened; outside is darkened."""
    x0_frac: float           # left
    y0_frac: float           # top
    x1_frac: float           # right
    y1_frac: float           # bottom


# ===========================================================================
# (De)serialisation for resume — discriminated by a "type" field.
# ===========================================================================

def deco_to_dict(d) -> dict:
    if isinstance(d, ArrowDeco):
        return {"type": "arrow", "length": d.length, "angle": d.angle,
                "cx_frac": d.cx_frac, "cy_frac": d.cy_frac}
    if isinstance(d, HighlightDeco):
        return {"type": "highlight", "x0_frac": d.x0_frac, "y0_frac": d.y0_frac,
                "x1_frac": d.x1_frac, "y1_frac": d.y1_frac}
    return {"type": "text", "text": d.text, "font_size": d.font_size,
            "cx_frac": d.cx_frac, "cy_frac": d.cy_frac}


def deco_from_dict(r: dict):
    t = r.get("type")
    if t == "highlight" or "x0_frac" in r:
        return HighlightDeco(float(r["x0_frac"]), float(r["y0_frac"]),
                             float(r["x1_frac"]), float(r["y1_frac"]))
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


def _render_item(item) -> Image.Image:
    """Render a SPRITE decoration (text/arrow) at BASE resolution. Highlights
    are not sprites — they're applied to the base by _apply_highlights."""
    if isinstance(item, ArrowDeco):
        return render_arrow_image(item.length, item.angle)
    return render_text_image(item.text, item.font_size)


# ===========================================================================
# Highlight rendering (brighten inside the boxes, darken outside)
# ===========================================================================

def _apply_highlights(rgb: Image.Image, highlights) -> Image.Image:
    """Return a copy of `rgb` with the union of highlight boxes brightened and
    everything else darkened, with a soft feathered edge between them."""
    w, h = rgb.size
    bright = ImageEnhance.Brightness(rgb).enhance(HIGHLIGHT_BRIGHTEN)
    dark = ImageEnhance.Brightness(rgb).enhance(HIGHLIGHT_DARKEN)

    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    for hl in highlights:
        x0 = max(0, min(w, round(hl.x0_frac * w)))
        y0 = max(0, min(h, round(hl.y0_frac * h)))
        x1 = max(0, min(w, round(hl.x1_frac * w)))
        y1 = max(0, min(h, round(hl.y1_frac * h)))
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        md.rectangle([x0, y0, x1, y1], fill=255)

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
    inside / darken outside); text + arrows are then drawn on top. Saves a PNG."""
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
    """Add text / arrows / highlights onto the previous image."""

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
        self.active = None               # TextDeco | ArrowDeco | None
        self._await_place = False        # active is floating, follows the cursor
        self.mode = "interactive"        # "interactive" | "typing"
        self._dial_shown = False
        self._mode_draw_highlight = False
        self._box_start = None
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
        self._update_buttons()
        self._set_status("Add text, an arrow, or a highlight to start.")

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
        self.active_rect = self.canvas.create_rectangle(0, 0, 0, 0, dash=(6, 4),
                                                        outline="#7CFC00", width=2, state="hidden")
        self.box_item    = self.canvas.create_rectangle(0, 0, 0, 0, dash=(6, 4),
                                                        outline="#ffd166", width=2, state="hidden")
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

        # Toolbar (two rows — room for more tools later).
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
                  width=24).pack(side="left")

        # Text-entry group (hidden unless typing).
        self.entry_frame = tk.Frame(side, bg="#1e1e24")
        tk.Label(self.entry_frame, text="Type your text:", bg="#1e1e24",
                 fg="#bbbbbb", font=("Arial", 10)).pack(anchor="w")
        self.text_var = tk.StringVar(value="")
        self.entry = tk.Entry(self.entry_frame, textvariable=self.text_var,
                              width=28, font=("Arial", 13))
        self.entry.pack(anchor="w", pady=(2, 4))
        self.entry.bind("<Return>", lambda e: self._ready())
        self.entry.bind("<Escape>", lambda e: self._cancel_typing())
        erow = tk.Frame(self.entry_frame, bg="#1e1e24")
        erow.pack(anchor="w")
        tk.Button(erow, text="Text ready to place ✓", command=self._ready,
                  font=("Arial", 11, "bold"), bg="#2e7d32", fg="white").pack(side="left")
        tk.Button(erow, text="Cancel", command=self._cancel_typing,
                  font=("Arial", 11)).pack(side="left", padx=(6, 0))

        # Arrow-direction dial (hidden unless an arrow is the active item).
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
                 text=("Add text → type → Enter → click to drop.\n"
                       "Add arrow → aim on the dial → click to drop.\n"
                       "Add highlight → drag a box over the area.\n\n"
                       "  A          add text\n"
                       "  R          add arrow\n"
                       "  H          add highlight\n"
                       "  E          edit the active text\n"
                       "  \u2190 / \u2192      rotate the active arrow\n"
                       "  + / \u2212      size  (type a number for exact)\n"
                       "  U / Bksp   undo the last item\n"
                       "  Enter / D  finish edits and move on\n"
                       "  Esc / Q    exit (resume later)")).pack(anchor="w", pady=(2, 8))

        btns = tk.Frame(side, bg="#1e1e24")
        btns.pack(anchor="w", side="bottom", pady=(8, 2))
        self.done_btn = tk.Button(btns, text="✓ Finish edits\n& move on",
                                  command=self._done, font=("Arial", 11, "bold"),
                                  bg="#2e7d32", fg="white", width=13)
        self.done_btn.pack(side="left", padx=(0, 6))
        self.undo_btn = tk.Button(btns, text="\u21b6 Undo", command=self._undo,
                                  font=("Arial", 11), width=9, state="disabled")
        self.undo_btn.pack(side="left")

    def _bind_keys(self):
        r = self.root
        for k in ("a", "A"):
            r.bind(k, self._kbd(self._add_text))
        for k in ("r", "R"):
            r.bind(k, self._kbd(self._add_arrow))
        for k in ("h", "H"):
            r.bind(k, self._kbd(self._add_highlight))
        for k in ("e", "E"):
            r.bind(k, self._kbd(self._edit_text))
        for k in ("u", "U", "<BackSpace>"):
            r.bind(k, self._kbd(self._undo))
        for k in ("<Return>", "d", "D"):
            r.bind(k, self._kbd(self._done))
        for k in ("<Escape>", "q", "Q"):
            r.bind(k, self._kbd(self._exit))
        for k in ("<plus>", "<equal>", "<KP_Add>"):
            r.bind(k, self._kbd(self._size_inc))
        for k in ("<minus>", "<underscore>", "<KP_Subtract>"):
            r.bind(k, self._kbd(self._size_dec))
        r.bind("<Left>",  self._kbd(lambda: self._rotate_active(-ARROW_ANGLE_STEP)))
        r.bind("<Right>", self._kbd(lambda: self._rotate_active(+ARROW_ANGLE_STEP)))
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
        fs = self._disp_font(item.font_size) if display else item.font_size
        return render_text_image(item.text, fs)

    def _rebuild_composite(self):
        highlights = [it for it in self.items if isinstance(it, HighlightDeco)]
        if highlights:
            img = _apply_highlights(self.base_disp.convert("RGB"),
                                    highlights).convert("RGBA")
        else:
            img = self.base_disp.copy()
        for it in self.items:
            if isinstance(it, HighlightDeco):
                continue
            if it is self.active and self._await_place:
                continue        # floating active is shown as the ghost instead
            im = self._render_item_img(it, display=True)
            cx, cy = it.cx_frac * self.disp_w, it.cy_frac * self.disp_h
            img.alpha_composite(im, (round(cx - im.width / 2),
                                     round(cy - im.height / 2)))
        self._composite_photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfig(self.canvas_item, image=self._composite_photo)
        self._update_active_outline()

    def _update_active_outline(self):
        if (self.active is not None and self.active in self.items
                and not self._await_place):
            im = self._render_item_img(self.active, display=True)
            cx = self.active.cx_frac * self.disp_w
            cy = self.active.cy_frac * self.disp_h
            x0, y0 = cx - im.width / 2, cy - im.height / 2
            self.canvas.coords(self.active_rect, x0 - 4, y0 - 4,
                               x0 + im.width + 4, y0 + im.height + 4)
            self.canvas.itemconfig(self.active_rect, state="normal")
        else:
            self.canvas.itemconfig(self.active_rect, state="hidden")

    def _regen_ghost(self):
        if self.active is None:
            return
        im = self._render_item_img(self.active, display=True).copy()
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
        if self._await_place and self.active is not None:
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
        if self._await_place:
            self.canvas.itemconfig(self.ghost_item, state="hidden")

    def _on_press(self, event):
        if self._mode_draw_highlight:
            x = min(max(event.x, 0), self.disp_w)
            y = min(max(event.y, 0), self.disp_h)
            self._box_start = (x, y)
            self.canvas.coords(self.box_item, x, y, x, y)
            self.canvas.itemconfig(self.box_item, state="normal")
            self.canvas.tag_raise(self.box_item)
            return
        if self.active is None:
            self._set_status("Click 'Add text', 'Add arrow', or 'Add highlight' first.")
            return
        self._place_active(event)

    def _on_drag(self, event):
        self._update_cursor(event.x, event.y)
        if self._mode_draw_highlight and self._box_start is not None:
            x = min(max(event.x, 0), self.disp_w)
            y = min(max(event.y, 0), self.disp_h)
            x0, y0 = self._box_start
            self.canvas.coords(self.box_item, x0, y0, x, y)
            self.canvas.tag_raise(self.box_item)
            self._update_cursor(event.x, event.y)

    def _on_release(self, event):
        if not (self._mode_draw_highlight and self._box_start is not None):
            return
        x = min(max(event.x, 0), self.disp_w)
        y = min(max(event.y, 0), self.disp_h)
        x0, y0 = self._box_start
        self._box_start = None
        self.canvas.itemconfig(self.box_item, state="hidden")
        lx, rx = sorted((x0, x))
        ty, by = sorted((y0, y))
        if (rx - lx) < 6 or (by - ty) < 6:
            self._set_status("Box too small — drag a larger area.")
            return
        self.items.append(HighlightDeco(lx / self.disp_w, ty / self.disp_h,
                                        rx / self.disp_w, by / self.disp_h))
        self._mode_draw_highlight = False
        self._rebuild_composite()
        self._update_buttons()
        self._set_status(f"Highlight added. {len(self.items)} item(s).")

    def _place_active(self, event):
        cx = min(max(event.x, 0), self.disp_w)
        cy = min(max(event.y, 0), self.disp_h)
        self.active.cx_frac = cx / self.disp_w
        self.active.cy_frac = cy / self.disp_h
        if self.active not in self.items:
            self.items.append(self.active)
        self._await_place = False
        self.canvas.itemconfig(self.ghost_item, state="hidden")
        self._rebuild_composite()
        self._update_buttons()
        self._set_status(f"Placed. {len(self.items)} item(s). "
                         f"Tweak it, add another, or finish.")

    # -- toolbar: add / edit ----------------------------------------------
    def _add_text(self):
        self._mode_draw_highlight = False
        self.active = None              # a fresh text is created on 'ready'
        self._await_place = False
        self.text_var.set("")
        self._open_typing()

    def _add_arrow(self):
        self._mode_draw_highlight = False
        self.active = ArrowDeco(self.cur_arrow_len, self.cur_arrow_angle, 0.5, 0.5)
        self.mode = "interactive"
        self.entry_frame.pack_forget()
        self._await_place = True        # follows the cursor; click to drop
        self._show_for_active()
        self._regen_ghost()
        self._rebuild_composite()
        self._set_status("Aim it on the dial, set size with +/\u2212, click to drop.")

    def _add_highlight(self):
        self.active = None
        self._await_place = False
        self.canvas.itemconfig(self.ghost_item, state="hidden")
        self.entry_frame.pack_forget()
        if self._dial_shown:
            self.dial_frame.pack_forget()
            self._dial_shown = False
        self.mode = "interactive"
        self._mode_draw_highlight = True
        self._box_start = None
        self._update_buttons()
        self._set_status("Drag a box over the area to highlight (press, drag, release).")

    def _edit_text(self):
        if not isinstance(self.active, TextDeco):
            self._set_status("No active text to edit — add one first.")
            return
        self.text_var.set(self.active.text)
        self._open_typing()

    def _open_typing(self):
        self.mode = "typing"
        if self._dial_shown:
            self.dial_frame.pack_forget()
            self._dial_shown = False
        self.entry_frame.pack(anchor="w", fill="x", pady=(4, 2),
                              before=self.controls)
        self.edit_btn.config(state="disabled")
        self.canvas.itemconfig(self.ghost_item, state="hidden")
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        self._set_status("Type your text, then Enter / 'Text ready to place ✓'.")

    def _cancel_typing(self):
        self.entry_frame.pack_forget()
        self.mode = "interactive"
        self._show_for_active()
        self.root.focus_set()
        self._set_status("Cancelled.")

    def _ready(self):
        text = self.text_var.get().strip()
        if not text:
            self._set_status("Type something first (or press Cancel).")
            return
        if not isinstance(self.active, TextDeco):       # brand-new text
            self.active = TextDeco(text, self.cur_font_size, 0.5, 0.5)
        else:                                           # editing existing
            self.active.text = text
        self.entry_frame.pack_forget()
        self.mode = "interactive"
        self._await_place = True                        # click to (re)place
        self._show_for_active()
        self._regen_ghost()
        self._rebuild_composite()
        self.root.focus_set()
        self._set_status("Move the mouse and click to drop the text.")

    def _show_for_active(self):
        """Show/hide the dial + Edit button to match the active item type."""
        is_arrow = isinstance(self.active, ArrowDeco)
        if is_arrow:
            if not self._dial_shown:
                self.dial_frame.pack(anchor="w", fill="x", pady=(4, 2),
                                     before=self.controls)
                self._dial_shown = True
            self._redraw_dial()
        elif self._dial_shown:
            self.dial_frame.pack_forget()
            self._dial_shown = False
        if self.active is not None:
            self.size_var.set(str(self._active_size()))
        self._update_buttons()

    # -- arrow dial --------------------------------------------------------
    def _dial_center(self):
        return 75.0, 75.0

    def _dial_event(self, event):
        cx, cy = self._dial_center()
        ang = math.degrees(math.atan2(event.y - cy, event.x - cx))
        self.cur_arrow_angle = ang
        if isinstance(self.active, ArrowDeco):
            self.active.angle = ang
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
        ang = (self.active.angle if isinstance(self.active, ArrowDeco)
               else self.cur_arrow_angle)
        rad = math.radians(ang)
        c.create_line(cx, cy, cx + R * math.cos(rad), cy + R * math.sin(rad),
                      fill="#5ad1ff", width=3, arrow="last")
        c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#5ad1ff", outline="")
        self.angle_var.set(f"{ang:+.0f}\u00b0")

    def _rotate_active(self, delta):
        if not isinstance(self.active, ArrowDeco):
            return
        self.active.angle += delta
        self.cur_arrow_angle = self.active.angle
        self._redraw_dial()
        self._regen_ghost()
        self._rebuild_composite()

    # -- size --------------------------------------------------------------
    def _active_size(self) -> int:
        if isinstance(self.active, TextDeco):
            return self.active.font_size
        if isinstance(self.active, ArrowDeco):
            return self.active.length
        return self.cur_font_size

    def _apply_size(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            self.size_var.set(str(self._active_size()))
            return
        if isinstance(self.active, ArrowDeco):
            v = max(MIN_ARROW_PX, min(MAX_ARROW_PX, value))
            self.active.length = v
            self.cur_arrow_len = v
        elif isinstance(self.active, TextDeco):
            v = max(MIN_FONT_PX, min(MAX_FONT_PX, value))
            self.active.font_size = v
            self.cur_font_size = v
        else:
            v = max(MIN_FONT_PX, min(MAX_FONT_PX, value))
            self.cur_font_size = v
        self.size_var.set(str(v))
        if self.active is not None:
            self._regen_ghost()
            self._rebuild_composite()

    def _size_step(self) -> int:
        return ARROW_STEP_PX if isinstance(self.active, ArrowDeco) else FONT_STEP_PX

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
        if not self.items:
            self._set_status("Nothing to undo yet.")
            return
        removed = self.items.pop()
        if removed is self.active:
            self.active = None
            self._await_place = False
            self.canvas.itemconfig(self.ghost_item, state="hidden")
            self._show_for_active()
        self._rebuild_composite()
        self._update_buttons()
        self._set_status(f"Removed the last item \u2014 {len(self.items)} left.")

    def _done(self):
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
    """Open the decorate GUI. Returns list[TextDeco | ArrowDeco | HighlightDeco]
    on Finish (possibly empty), or None if the user EXITS (resume later)."""
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
            print(f"  - highlight  ({it.x0_frac:.2f},{it.y0_frac:.2f})"
                  f"-({it.x1_frac:.2f},{it.y1_frac:.2f})")
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
