
# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import io
import numpy as np
from PIL import Image

def deai_postprocess(img: Image.Image, strength: float = 1.0) -> Image.Image:
    """Strip metadata + gentle anti-fingerprint pass.
    strength: 0.5 = subtle, 1.0 = default, 2.0 = aggressive (visible)."""
    img = img.convert("RGB")

    # 1. Color quantization — merges near-identical shades.
    # Stickman line art has ~5 real colors; 32 is invisible to the eye, kills gradient tells.
    n_colors = max(8, int(48 / strength))
    img = img.quantize(colors=n_colors,
                       method=Image.Quantize.MEDIANCUT,
                       dither=Image.Dither.NONE).convert("RGB")

    # 2. Gaussian noise — disrupts diffusion's high-frequency spectral signature.
    arr = np.array(img, dtype=np.int16)
    arr += np.random.normal(0, 1.8 * strength, arr.shape).astype(np.int16)
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)

    # 3. Resize jitter — breaks pixel-aligned periodic patterns.
    w, h = img.size
    s = 1.0 - 0.025 * strength
    img = img.resize((int(w * s), int(h * s)), Image.LANCZOS) \
             .resize((w, h), Image.LANCZOS)

    # 4. Fresh canvas — drops EVERY metadata field. No EXIF, no XMP, no PNG text chunks.
    clean = Image.new("RGB", img.size)
    clean.paste(img)
    return clean

def save_clean(img: Image.Image, path) -> None:
    """Save PNG with no metadata."""
    img.save(path, format="PNG", optimize=True, pnginfo=None)
