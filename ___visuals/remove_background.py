from __future__ import annotations

# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
"""
remove_background.py
====================

Thin wrapper around `rembg` (the SAME library joint_image_creator.compositor
uses for its `removeBG` overlays) so the review GUI can cut out an image's
background on demand.

After cutting the subject out, it's composited onto a configurable BACKDROP
(see below) so the result isn't transparent — by default a plain white fill.
The result is written as PNG.

`rembg` is heavy (pulls in onnxruntime) and downloads its model on first use,
so it's imported lazily inside the function: nothing here costs anything until
you actually remove a background.
"""

from pathlib import Path
from PIL import Image


# Backdrop placed BEHIND the cut-out subject so the result isn't transparent.
# Either:
#   - an (R, G, B) tuple  → a solid fill of that colour, OR
#   - a path string       → a background image, cover-fitted to the cut-out size.
# Set to None to keep the result transparent (RGBA) instead of compositing.
BACKDROP = (255, 255, 255)                          # plain white
# BACKDROP = ".resources/backgrounds/bg_crumpled_card.png"    # ...or a background file
# BACKDROP = None                                   # ...or keep transparency


def _compose_on_backdrop(cutout: Image.Image, backdrop) -> Image.Image:
    """
    Place an RGBA `cutout` onto `backdrop` and return an OPAQUE RGB image the
    same size as the cut-out.

    `backdrop` is either an (R,G,B) tuple (solid fill) or a path to a
    background image, which is cover-fitted (scaled to fill, centre-cropped)
    to the cut-out's dimensions so it never distorts.
    """
    cutout = cutout.convert("RGBA")
    w, h = cutout.size

    if isinstance(backdrop, (tuple, list)):
        base = Image.new("RGBA", (w, h), tuple(backdrop) + (255,))
    else:
        bg = Image.open(backdrop).convert("RGBA")
        scale = max(w / bg.width, h / bg.height)        # cover-fit
        bg = bg.resize((max(1, round(bg.width * scale)),
                        max(1, round(bg.height * scale))), Image.LANCZOS)
        left = (bg.width - w) // 2
        top  = (bg.height - h) // 2
        base = bg.crop((left, top, left + w, top + h))

    base.alpha_composite(cutout)
    return base.convert("RGB")


def remove_background(input_path: str, output_path: str, backdrop=BACKDROP) -> str:
    """
    Strip the background from `input_path` and write a PNG to `output_path`.

    If `backdrop` is given (the module default is plain white), the cut-out
    subject is composited onto it and the result is an OPAQUE RGB PNG. Pass
    backdrop=None to keep the background transparent (RGBA) instead.
    Returns `output_path`.
    """
    from rembg import remove  # lazy: heavy import + first-run model download

    img = Image.open(input_path).convert("RGBA")
    cut = remove(img)                          # RGBA, background alpha = 0

    if backdrop is not None:
        cut = _compose_on_backdrop(cut, backdrop)   # → opaque RGB on backdrop
    else:
        cut = cut.convert("RGBA")                   # keep transparency

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cut.save(out, "PNG")
    return str(out)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("usage: python remove_background.py <input> <output.png>")
        sys.exit(1)
    print(remove_background(sys.argv[1], sys.argv[2]))
