"""
Auto collage: scatter N images onto a background with slight rotations,
white borders and soft shadows — overlapping, hand-placed feel, zero
interaction. Deterministic per seed. Pure PIL (headless-testable).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import random
from pathlib import Path

from PIL import Image, ImageFilter

try:
    _RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # very old Pillow
    _RESAMPLE = Image.LANCZOS

CANVAS_W, CANVAS_H = 1920, 1080
BORDER_PX = 14                # white polaroid-style border around each tile
MAX_TILT_DEG = 7.0
SHADOW_BLUR = 18
SHADOW_ALPHA = 110
SHADOW_OFFSET = (10, 14)
DEFAULT_BG = "#e8e2d4"        # plain warm card when no background image given


def _slot_centres(n: int, rng: random.Random) -> list[tuple[float, float]]:
    """Loose layout centres (fractions of canvas) with jitter: a row for 2,
    a triangle for 3, a jittered grid beyond — spaced so tiles overlap."""
    if n == 2:
        base = [(0.36, 0.5), (0.64, 0.5)]
    elif n == 3:
        base = [(0.30, 0.42), (0.62, 0.36), (0.47, 0.66)]
    else:
        cols = 2 if n <= 4 else 3
        rows = (n + cols - 1) // cols
        base = [((c + 0.5) / cols * 0.84 + 0.08,
                 (r + 0.5) / rows * 0.8 + 0.1)
                for r in range(rows) for c in range(cols)][:n]
    return [(x + rng.uniform(-0.035, 0.035), y + rng.uniform(-0.045, 0.045))
            for x, y in base]


def _prep_tile(path: str, tile_w: int, rng: random.Random) -> Image.Image:
    img = Image.open(path).convert("RGB")
    tile_h = max(1, round(tile_w * img.height / img.width))
    img = img.resize((tile_w, tile_h), _RESAMPLE)
    bordered = Image.new("RGB", (tile_w + 2 * BORDER_PX, tile_h + 2 * BORDER_PX),
                         "#ffffff")
    bordered.paste(img, (BORDER_PX, BORDER_PX))
    tilt = rng.uniform(-MAX_TILT_DEG, MAX_TILT_DEG)
    return bordered.convert("RGBA").rotate(tilt, expand=True, resample=Image.BICUBIC)


def auto_collage(image_paths: list[str], out_path: str,
                 background: str | None = None,
                 seed: str = "collage") -> str:
    """Compose image_paths (2+) into one collage PNG at out_path.
    `background` is an image path, or a '#rrggbb' colour, or None (default
    plain card). `seed` makes the layout deterministic per scene."""
    if len(image_paths) < 2:
        raise ValueError("auto_collage needs at least 2 images")
    rng = random.Random(seed)

    if background and not str(background).startswith("#") \
            and Path(str(background)).exists():
        canvas = Image.open(background).convert("RGB").resize(
            (CANVAS_W, CANVAS_H), _RESAMPLE)
    else:
        canvas = Image.new("RGB", (CANVAS_W, CANVAS_H),
                           str(background) if background else DEFAULT_BG)
    canvas = canvas.convert("RGBA")

    n = len(image_paths)
    tile_w = round(CANVAS_W * (0.46 if n <= 3 else 0.34 if n <= 6 else 0.26))
    order = list(image_paths)
    rng.shuffle(order)

    for path, (cx, cy) in zip(order, _slot_centres(n, rng)):
        tile = _prep_tile(path, tile_w, rng)
        px = round(cx * CANVAS_W - tile.width / 2)
        py = round(cy * CANVAS_H - tile.height / 2)
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sil = Image.new("RGBA", tile.size, (0, 0, 0, SHADOW_ALPHA))
        shadow.paste(sil, (px + SHADOW_OFFSET[0], py + SHADOW_OFFSET[1]),
                     tile.split()[3])
        canvas = Image.alpha_composite(
            canvas, shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR)))
        canvas.paste(tile, (px, py), tile)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path)
    return out_path
