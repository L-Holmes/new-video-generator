"""
MAKE_TEXT_OVERLAY.py
========================

Composite a "Fireship-style" caption onto a base scene image: chunky white
(slightly pixelated) text on a contrasting dark-red card, tilted a few degrees,
dropped into one of 6 positions. This is the rendering primitive behind the
`stickman_text_overlay` media type.

WHAT IT DOES
------------
Given a BASE image (the previous scene's chosen visual) and a TEXT string (the
scene's `search_term`), it:

  1. Fits + black-pads the base to the video frame (so the caption is always
     the same relative size/position regardless of the base's dimensions, and
     so the background matches how the stitcher will show that scene).
  2. Renders the caption — white text on a dark-red card, optional shadow +
     border, pixel-font when one is available (matching WORDS_ON_SCREEN), and a
     light nearest-neighbour pixelation otherwise so it still looks pixelated.
  3. Tilts the caption (slight CW or CCW) and pastes it at one of 3 anchor
     spots (top-left quadrant / top-right quadrant / centre).
  4. Writes a PNG, or a STATIC MP4 of a given duration (so the later Ken Burns
     pass skips it and the tilted caption never gets cropped/zoomed).

The 6 positions = {top-left, top-right, centre} × {tilt CCW, tilt CW}. The pick
is deterministic per (text, base) unless you pass an explicit position/rotation,
so re-runs are stable.

STANDALONE SMOKE TEST
---------------------
    python make_text_overlay_img.py
    python make_text_overlay_img.py --text "TRADED FOR NUTMEG" --pos TR --rot cw
    python make_text_overlay_img.py --base scene_base.png --duration 5   # -> mp4
    python make_text_overlay_img.py --source-dir spices-CACHE/stock_footage
"""

from __future__ import annotations

# Allow `uv run ___visuals/MAKE_TEXT_OVERLAY.py` from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Reuse the EXACT font discovery + pixelation from the words-on-screen renderer
# so captions match its look. WORDS_ON_SCREEN does not import this module, so
# there's no circular import. Fallbacks keep this file usable on its own.
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

# Output frame (matches the rest of the pipeline). The base is fit+padded here.
FRAME_W: int = 1920
FRAME_H: int = 1080
FPS:     int = 30

# Caption styling.
CARD_COLOR:    tuple[int, int, int] = (155, 24, 34)   # dark red
TEXT_COLOR:    tuple[int, int, int] = (245, 245, 245) # near-white
BORDER_COLOR:  tuple[int, int, int] = (92, 12, 18)    # darker red edge
SHADOW_ALPHA:  int = 110                              # 0=off … 255

FONT_SIZE:        int = 92      # caption text size (px)
PAD_X_FRAC:       float = 0.45  # card horizontal padding, ×font_size
PAD_Y_FRAC:       float = 0.28  # card vertical padding, ×font_size
LINE_GAP_FRAC:    float = 0.18  # gap between wrapped lines, ×font_size
BORDER_W_FRAC:    float = 0.055 # border thickness, ×font_size (0 = no border)
SHADOW_OFFSET:    tuple[int, int] = (0, 8)
TILT_DEGREES:     float = 7.0   # slight tilt magnitude

# Pixelation: 1 = rely on the pixel font; >1 = force chunky pixels. When no
# pixel font is found we fall back to this so captions still look pixelated
# (kept in step with WORDS_ON_SCREEN.pixel_block_size_when_no_pixel_font = 3).
PIXEL_BLOCK_WHEN_NO_PIXEL_FONT: int = 3

# Caption width as a fraction of the frame, per anchor (wrap target).
WIDTH_FRAC_QUADRANT: float = 0.42
WIDTH_FRAC_CENTRE:   float = 0.60

# Anchor = caption CENTRE as a fraction of (FRAME_W, FRAME_H).
ANCHORS: dict[str, tuple[float, float]] = {
    "TL": (0.30, 0.33),   # top-left quadrant
    "TR": (0.70, 0.33),   # top-right quadrant
    "C":  (0.50, 0.50),   # centre
}
# Positive angle = counter-clockwise in PIL.
ROTATIONS: dict[str, float] = {"ccw": +TILT_DEGREES, "cw": -TILT_DEGREES}

POSITIONS = ("TL", "TR", "C")
ROTS      = ("ccw", "cw")
# The 6 combos.
COMBOS = [(p, r) for p in POSITIONS for r in ROTS]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}

DEFAULT_SOURCE_DIR = "spices-CACHE/stock_footage"


# ===========================================================================
# HELPERS
# ===========================================================================

def _classify(path: str) -> str:
    clean = path.split("?", 1)[0].split("#", 1)[0]
    s = Path(clean).suffix.lower()
    if s in IMAGE_EXTS:
        return "image"
    if s in VIDEO_EXTS:
        return "video"
    return "other"


def _pick_combo(seed: str) -> tuple[str, str]:
    """Deterministic per-seed pick of one of the 6 (position, rotation) combos."""
    return random.Random(seed).choice(COMBOS)


def _extract_first_frame(video_path: str) -> str:
    """Grab frame 1 of a video to a temp PNG. Caller deletes it."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", video_path,
         "-vframes", "1", tmp.name],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        Path(tmp.name).unlink(missing_ok=True)
        raise RuntimeError(f"couldn't extract first frame of {video_path}: "
                           f"{r.stderr[-500:]}")
    return tmp.name


def _load_base_as_frame(base_path: str) -> Image.Image:
    """Load the base as a PIL RGB image, extracting a frame if it's a video."""
    kind = _classify(base_path)
    if kind == "video":
        frame = _extract_first_frame(base_path)
        try:
            return Image.open(frame).convert("RGB")
        finally:
            Path(frame).unlink(missing_ok=True)
    return Image.open(base_path).convert("RGB")


def _fit_pad(img: Image.Image, w: int, h: int,
             pad_color=(0, 0, 0)) -> Image.Image:
    """Contain-fit `img` into w×h on a padded canvas (matches the stitcher's
    scale=decrease + black pad), so the caption sits on the same picture the
    final video will show for that scene."""
    img = img.convert("RGB")
    iw, ih = img.size
    if iw == 0 or ih == 0:
        return Image.new("RGB", (w, h), pad_color)
    scale = min(w / iw, h / ih)
    nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
    resized = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (w, h), pad_color)
    canvas.paste(resized, ((w - nw) // 2, (h - nh) // 2))
    return canvas


def _wrap_lines(text: str, font: ImageFont.FreeTypeFont,
                max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Greedy word-wrap to a pixel width. Words longer than max_w stay whole."""
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _render_caption(text: str, *, anchor: str, font_size: int,
                    pixel_block: int) -> Image.Image:
    """Render the dark-red caption card with white text as an RGBA image
    (un-rotated). Pixel-font + optional pixelation give the retro look."""
    font_path, is_pixel = _resolve_caption_font()
    font = ImageFont.truetype(font_path, font_size)

    # Wrap to the target width for this anchor.
    max_text_w = int((WIDTH_FRAC_CENTRE if anchor == "C"
                      else WIDTH_FRAC_QUADRANT) * FRAME_W)
    pad_x = int(PAD_X_FRAC * font_size)
    pad_y = int(PAD_Y_FRAC * font_size)
    gap   = int(LINE_GAP_FRAC * font_size)

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines = _wrap_lines(text, font, max(40, max_text_w - 2 * pad_x), probe)

    # Measure each line.
    sizes = []
    for ln in lines:
        l, t, r, b = probe.textbbox((0, 0), ln or " ", font=font)
        sizes.append((r - l, b - t, l, t))
    text_w = max((w for w, _, _, _ in sizes), default=1)
    text_h = sum(h for _, h, _, _ in sizes) + gap * (len(lines) - 1)

    card_w = text_w + 2 * pad_x
    card_h = text_h + 2 * pad_y

    # Build card (with optional shadow). Use a margin so the shadow + later
    # rotation expansion aren't clipped.
    border_w = int(BORDER_W_FRAC * font_size)
    sx, sy = SHADOW_OFFSET
    margin = max(abs(sx), abs(sy)) + border_w + 4
    canvas_w = card_w + 2 * margin
    canvas_h = card_h + 2 * margin
    card = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)

    card_x0, card_y0 = margin, margin
    card_x1, card_y1 = margin + card_w, margin + card_h

    if SHADOW_ALPHA > 0:
        cd.rectangle(
            [card_x0 + sx, card_y0 + sy, card_x1 + sx, card_y1 + sy],
            fill=(0, 0, 0, SHADOW_ALPHA),
        )
    cd.rectangle([card_x0, card_y0, card_x1, card_y1], fill=CARD_COLOR + (255,))
    if border_w > 0:
        cd.rectangle([card_x0, card_y0, card_x1, card_y1],
                     outline=BORDER_COLOR + (255,), width=border_w)

    # Crisp glyphs when using a pixel font (block<=1); otherwise AA is fine
    # because the pixelation pass below defines the pixels.
    if is_pixel and pixel_block <= 1:
        try:
            cd.fontmode = "1"
        except Exception:
            pass

    # Draw text lines, left-aligned within the card's padded box.
    y = card_y0 + pad_y
    for ln, (w, h, lx, lt) in zip(lines, sizes):
        cd.text((card_x0 + pad_x - lx, y - lt), ln, font=font,
                fill=TEXT_COLOR + (255,))
        y += h + gap

    if pixel_block > 1:
        card = _pixelate_image(card.convert("RGBA"), pixel_block)

    return card


def _resolve_caption_font() -> tuple[str, bool]:
    """(font_path, is_pixel_font) — prefer a pixel font, else a normal one."""
    pf = _find_pixel_font()
    if pf:
        return pf, True
    return _find_font(), False


def _effective_block(is_pixel: bool, override: int | None) -> int:
    if override is not None:
        return max(1, override)
    return 1 if is_pixel else max(1, PIXEL_BLOCK_WHEN_NO_PIXEL_FONT)


def _placement(size: tuple[int, int], caption_rgba: Image.Image,
               anchor: str) -> tuple[Image.Image, int, int]:
    """Where the (already-rotated) caption goes on a `size` canvas: centre
    on the anchor, nudged inward so it never spills off-frame. Returns the
    (possibly safety-downscaled) caption and its paste position — shared by
    the baked path (_place) and the transparent-layer path
    (make_caption_layer), so the two land pixel-identically."""
    W, H = size
    cw, ch = caption_rgba.size

    # Safety: if the caption is somehow bigger than the frame, scale it down.
    if cw > W * 0.96 or ch > H * 0.96:
        s = min(W * 0.96 / cw, H * 0.96 / ch)
        caption_rgba = caption_rgba.resize(
            (max(1, int(cw * s)), max(1, int(ch * s))), Image.LANCZOS)
        cw, ch = caption_rgba.size

    ax, ay = ANCHORS[anchor]
    cx, cy = ax * W, ay * H
    x = int(round(cx - cw / 2))
    y = int(round(cy - ch / 2))

    # Keep on-frame with a small margin.
    m = 24
    x = max(m, min(x, W - cw - m))
    y = max(m, min(y, H - ch - m))
    return caption_rgba, x, y


def _place(base: Image.Image, caption_rgba: Image.Image,
           anchor: str) -> Image.Image:
    """Paste the (already-rotated) caption so its centre sits on the anchor,
    nudged inward so it never spills off-frame."""
    caption_rgba, x, y = _placement(base.size, caption_rgba, anchor)
    out = base.convert("RGBA")
    out.alpha_composite(caption_rgba, (x, y))
    return out.convert("RGB")


# ===========================================================================
# PUBLIC ENTRY POINTS
# ===========================================================================

def make_caption_layer(
    text: str,
    output_path: str,
    *,
    position: str | None = None,         # "TL" | "TR" | "C"
    rotation: str | None = None,         # "ccw" | "cw"
    font_size: int = FONT_SIZE,
    pixel_block: int | None = None,      # None = auto (1 w/ pixel font else 3)
    seed: str | None = None,
) -> str:
    """The tilted caption card on a TRANSPARENT frame-sized canvas (RGBA
    PNG at 1920x1080) instead of baked onto a base — for LIVE VIDEO scenes,
    where the caption is burned over the MOVING footage as an overlay
    (VIDEO_CHAINS.burn_layers_onto_segment). Card, tilt and the
    deterministic per-seed position/rotation pick are exactly
    make_text_overlay's; only the background differs (transparent instead
    of the fitted base). Returns output_path."""
    output_path = str(output_path)
    text = (text or "").strip()

    pos, rot = _pick_combo(seed or text)
    if position:
        position = position.upper()
        if position not in ANCHORS:
            raise ValueError(f"position must be one of {list(ANCHORS)}")
        pos = position
    if rotation:
        rotation = rotation.lower()
        if rotation not in ROTATIONS:
            raise ValueError(f"rotation must be one of {list(ROTATIONS)}")
        rot = rotation

    _, is_pixel = _resolve_caption_font()
    block = _effective_block(is_pixel, pixel_block)
    caption = _render_caption(text or " ", anchor=pos,
                              font_size=font_size, pixel_block=block)
    resample = Image.NEAREST if block > 1 else Image.BICUBIC
    caption = caption.rotate(ROTATIONS[rot], expand=True, resample=resample)

    layer = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
    caption, x, y = _placement(layer.size, caption, pos)
    layer.alpha_composite(caption, (x, y))

    print(f"[text-overlay] LAYER (transparent)  pos={pos} rot={rot}  "
          f"pixel_font={is_pixel} block={block}  "
          f"text='{text[:50]}{'…' if len(text) > 50 else ''}'")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    layer.save(output_path)
    return output_path


def make_text_overlay(
    base_image_path: str,
    text: str,
    output_path: str,
    *,
    duration: float | None = None,
    position: str | None = None,         # "TL" | "TR" | "C"
    rotation: str | None = None,         # "ccw" | "cw"
    font_size: int = FONT_SIZE,
    pixel_block: int | None = None,      # None = auto (1 w/ pixel font else 3)
    seed: str | None = None,
) -> str:
    """
    Composite a tilted Fireship-style caption (`text`) onto `base_image_path`
    and write it to `output_path`.

    Output container is inferred from the suffix:
        .png/.jpg  → still
        .mp4 (etc) → STATIC clip of `duration` seconds (required for video)

    `position`/`rotation` override the otherwise-deterministic per-`seed` pick
    of one of the 6 combos.

    Returns output_path.
    """
    base_image_path = str(base_image_path)
    output_path = str(output_path)
    text = (text or "").strip()

    out_ext = Path(output_path).suffix.lower()
    out_is_video = out_ext in VIDEO_EXTS
    out_is_image = out_ext in IMAGE_EXTS
    if not (out_is_video or out_is_image):
        raise ValueError(f"unsupported output extension: {output_path}")
    if out_is_video and duration is None:
        raise ValueError("duration (seconds) is required for video output")

    # Resolve position + rotation.
    pos, rot = _pick_combo(seed or (base_image_path + "|" + text))
    if position:
        position = position.upper()
        if position not in ANCHORS:
            raise ValueError(f"position must be one of {list(ANCHORS)}")
        pos = position
    if rotation:
        rotation = rotation.lower()
        if rotation not in ROTATIONS:
            raise ValueError(f"rotation must be one of {list(ROTATIONS)}")
        rot = rotation

    # 1) Base → fit/pad to frame (or a plain dark card if no base).
    if base_image_path and Path(base_image_path).exists():
        base = _fit_pad(_load_base_as_frame(base_image_path), FRAME_W, FRAME_H)
    else:
        print(f"[text-overlay] WARNING: base missing ({base_image_path!r}) — "
              f"using a plain background")
        base = Image.new("RGB", (FRAME_W, FRAME_H), (20, 20, 26))

    # 2) Caption card.
    _, is_pixel = _resolve_caption_font()
    block = _effective_block(is_pixel, pixel_block)
    caption = _render_caption(text or " ", anchor=pos,
                              font_size=font_size, pixel_block=block)

    # 3) Tilt. NEAREST keeps pixel blocks crunchy; BICUBIC is cleaner for a
    #    crisp pixel-font card. Expand so corners aren't clipped; transparent fill.
    resample = Image.NEAREST if block > 1 else Image.BICUBIC
    caption = caption.rotate(ROTATIONS[rot], expand=True,
                             resample=resample)

    # 4) Composite.
    composed = _place(base, caption, pos)

    print(f"[text-overlay] base='{Path(base_image_path).name}'  "
          f"pos={pos} rot={rot}  pixel_font={is_pixel} block={block}  "
          f"text='{text[:50]}{'…' if len(text) > 50 else ''}'")
    print(f"[text-overlay] → {output_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if out_is_image:
        if out_ext in (".jpg", ".jpeg"):
            composed.save(output_path, quality=95)
        else:
            composed.save(output_path)
        return output_path

    # Static MP4: encode the composed still for `duration` seconds.
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tmp_png = tf.name
    try:
        composed.save(tmp_png)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-framerate", str(FPS), "-i", tmp_png,
            "-t", f"{float(duration):.3f}",
            "-vf", f"fps={FPS},format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-an", output_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print("[text-overlay] FATAL: ffmpeg failed")
            print(f"[text-overlay] stderr (tail): {r.stderr[-800:]}")
            Path(output_path).unlink(missing_ok=True)
            raise RuntimeError(f"text-overlay render failed for {output_path}")
    finally:
        Path(tmp_png).unlink(missing_ok=True)

    return output_path


# ===========================================================================
# STANDALONE SMOKE TEST
# ===========================================================================

def _pick_random_media(source_dir: str) -> str:
    p = Path(source_dir)
    if not p.exists():
        print(f"[text-overlay] source dir not found: {source_dir}")
        sys.exit(1)
    files = [f for f in p.iterdir()
             if f.is_file() and _classify(str(f)) in ("image", "video")]
    if not files:
        print(f"[text-overlay] no image/video files in {source_dir}")
        sys.exit(1)
    chosen = random.choice(files)
    print(f"[text-overlay] randomly chose base: {chosen.name}")
    return str(chosen)


def _main() -> None:
    ap = argparse.ArgumentParser(description="Composite a tilted caption onto a base image.")
    ap.add_argument("--base", default="",
                    help="base image/video (default: random from --source-dir)")
    ap.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR)
    ap.add_argument("--text", default="NEW YORK WAS TRADED FOR NUTMEG")
    ap.add_argument("--pos", default="", choices=["", "TL", "TR", "C"])
    ap.add_argument("--rot", default="", choices=["", "ccw", "cw"])
    ap.add_argument("--font-size", type=int, default=FONT_SIZE)
    ap.add_argument("--block", type=int, default=-1,
                    help="pixelation block (-1 = auto)")
    ap.add_argument("--duration", type=float, default=0.0,
                    help=">0 → write an MP4 of this length")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    base = args.base or _pick_random_media(args.source_dir)
    if args.out:
        out = args.out
    else:
        Path("temp").mkdir(parents=True, exist_ok=True)
        ext = ".mp4" if args.duration > 0 else ".png"
        out = f"temp/text_overlay_test_output{ext}"

    make_text_overlay(
        base, args.text, out,
        duration=(args.duration if args.duration > 0 else None),
        position=(args.pos or None),
        rotation=(args.rot or None),
        font_size=args.font_size,
        pixel_block=(None if args.block < 0 else args.block),
    )
    print(f"\n[text-overlay] OK wrote {out}")


if __name__ == "__main__":
    _main()
