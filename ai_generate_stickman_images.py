"""
Stickman Batch Image Generator + shared AI-image core.

Generates AI stickman images for scenes flagged search_type == "stickman", and
exposes the reusable robust-call core (_call_flux_to_file) + helpers that
ai_edit.py builds on.

## --- OUTPUT ---
For each stickman scene, `num_variants` images are written as:
    <out_dir>/<stem>_<variant>.png        (stem derived from SCRIPT TEXT)
A failed scene gets a captioned placeholder at a DISTINCT name:
    <out_dir>/<stem>_<variant>.placeholder.png

## --- IMAGE FORMAT (black-bar fix) ---
- Images are generated 16:9 (IMAGE_SIZE = "landscape_16_9") to match the video
  and minimise Ken-Burns cropping.
- Every saved image is flattened onto OPAQUE WHITE via _force_white_bg(): any
  alpha left by deai_postprocess (which is what turns into BLACK bars/regions
  once ffmpeg touches the PNG) is composited over white, and the result is
  white-padded to exactly 16:9 as a backstop. No transparency ever reaches the
  stitcher, so no black.

## --- ROBUSTNESS ---
- Each fal call is wrapped in a timeout (PER_IMAGE_TIMEOUT_SEC). An out-of-
  credits account makes fal HANG; the timeout cancels it instead of freezing.
- The first timeout / credit-looking error sets a shared abort flag; remaining
  scenes short-circuit straight to a placeholder without calling fal.

## --- PRE-REQUISITES ---
- FAL_KEY set in .env
- prompts file with entries containing search_term, position, search_type
- Reference images listed in REF_IMAGES
"""

import asyncio, io, pathlib, json, re, hashlib, requests
from dotenv import load_dotenv
load_dotenv()  # <- reads .env before fal_client uses FAL_KEY

import fal_client
from PIL import Image, ImageDraw, ImageFont
from ai__postprocess import deai_postprocess, save_clean

# -- Reference images for style grounding --------------------------------
REF_IMAGES = [
    "_AI_REFERENCE_IMAGES/stick1.jpg",
    "_AI_REFERENCE_IMAGES/stick2.jpg",
    "_AI_REFERENCE_IMAGES/stick3.jpg",
]

# -- Defaults (used when run standalone; the pipeline passes its own) -----
DEFAULT_PROMPTS_FILE = "ai_prompts.json"
DEFAULT_OUT_DIR      = pathlib.Path("ai_output")
DEFAULT_NUM_VARIANTS = 2

MODEL        = "fal-ai/flux-2-max/edit"   # accepts image_urls
IMAGE_SIZE   = "landscape_16_9"           # 16:9 to match the video (was square_hd)
CONCURRENCY  = 1                          # max parallel fal calls
PROCESS_TYPE = "stickman"
POSTPROCESS  = True

# Generous per-call backstop (10 min). A call still pending after this is
# almost certainly a hang (out of credits / fal outage); we cancel it and fall
# back to a placeholder. With CONCURRENCY=1 a systemic hang costs ONE timeout
# period before the abort flag makes the rest instant placeholders.
PER_IMAGE_TIMEOUT_SEC = 600

STYLE_PREFIX = ("generate me, with a minimal, clean line look as if drawn on "
                "microsoft paint with low pixel density: ")
STYLE_SUFFIX = (". Again, in hand drawn ms paint style, in colour, minimal, "
                "white background")

# -- Output canvas / placeholder styling (16:9) --------------------------
CANVAS_W, CANVAS_H = 1920, 1080
TARGET_ASPECT      = CANVAS_W / CANVAS_H
PLACEHOLDER_BG     = (250, 250, 250)
WHITE              = (255, 255, 255)
_PH_RED            = (200, 50, 50)
_PH_DARK           = (40, 40, 40)
_PH_GREY           = (120, 120, 120)


def _search_type_str(value) -> str:
    """Accept either a raw string or a MediaType-like enum exposing `.value`."""
    return value.value if hasattr(value, "value") else value


def _scene_stem(script_text: str) -> str:
    """
    Unique, stable, human-readable filename stem for ONE scene, derived from
    script_text (the unique key everywhere else in the pipeline), NOT from
    `position` (which is not reliably unique).
    """
    safe   = re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:40] or "scene"
    digest = hashlib.md5(script_text.encode("utf-8")).hexdigest()[:8]
    return f"{safe}_{digest}"


# -- Black-bar fix: force opaque white + 16:9 ----------------------------

def _force_white_bg(img: Image.Image) -> Image.Image:
    """
    Return an OPAQUE RGB image on a white background, padded to exactly 16:9.

    Why: deai_postprocess can leave an alpha channel (transparent background).
    ffmpeg renders transparency as BLACK, which is the "black bars/regions"
    being seen. Compositing over white kills that, and white-padding to 16:9
    guarantees that even a non-16:9 return never letterboxes to black.
    """
    # 1) composite any alpha over white -> opaque RGB
    rgba = img.convert("RGBA")
    flat = Image.new("RGBA", rgba.size, WHITE + (255,))
    flat.alpha_composite(rgba)
    rgb = flat.convert("RGB")

    # 2) white-pad to 16:9 if needed (backstop; with IMAGE_SIZE=16:9 it's a no-op)
    w, h = rgb.size
    if h == 0 or w == 0:
        return rgb
    aspect = w / h
    if abs(aspect - TARGET_ASPECT) < 1e-3:
        return rgb
    if aspect > TARGET_ASPECT:      # too wide -> pad top/bottom
        new_w, new_h = w, round(w / TARGET_ASPECT)
    else:                           # too tall -> pad left/right
        new_w, new_h = round(h * TARGET_ASPECT), h
    canvas = Image.new("RGB", (new_w, new_h), WHITE)
    canvas.paste(rgb, ((new_w - w) // 2, (new_h - h) // 2))
    return canvas


# -- Credit / billing detection ------------------------------------------

_CREDIT_ERROR_NEEDLES = (
    "credit", "quota", "insufficient", "balance", "exhaust", "payment",
    "billing", "out of funds", "402", "403", "unauthorized", "forbidden",
)


def _looks_like_credit_error(exc) -> bool:
    msg = str(exc).lower()
    return any(n in msg for n in _CREDIT_ERROR_NEEDLES)


# -- Placeholder rendering (16:9) ----------------------------------------

def _load_font(size):
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)   # Pillow >= 10
    except TypeError:
        return ImageFont.load_default()


def _text_size(draw, text, font):
    try:
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        return r - l, b - t
    except Exception:
        try:
            return int(draw.textlength(text, font=font)), getattr(font, "size", 12)
        except Exception:
            return (len(text) * 8, 12)


def _wrap(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        tw, _ = _text_size(draw, trial, font)
        if tw <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _write_placeholder(path, script_text: str, reason: str,
                       title: str = "AI IMAGE UNAVAILABLE") -> str:
    """Render a captioned 16:9 'unavailable' card so a failed scene is obvious."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    W, H = CANVAS_W, CANVAS_H
    img  = Image.new("RGB", (W, H), PLACEHOLDER_BG)
    draw = ImageDraw.Draw(img)

    for i in range(8):  # red warning border
        draw.rectangle([i, i, W - 1 - i, H - 1 - i], outline=_PH_RED)

    margin = 120
    max_w  = W - 2 * margin

    f_title  = _load_font(64)
    f_reason = _load_font(40)
    f_label  = _load_font(30)
    f_script = _load_font(44)

    blocks = [(title, f_title, _PH_RED, 30)]
    for ln in _wrap(draw, reason, f_reason, max_w):
        blocks.append((ln, f_reason, _PH_DARK, 6))
    blocks.append(("", f_label, _PH_DARK, 26))
    blocks.append(("SCENE:", f_label, _PH_GREY, 10))
    for ln in _wrap(draw, script_text, f_script, max_w):
        blocks.append((ln, f_script, _PH_DARK, 8))

    sized, total_h = [], 0
    for text, font, colour, gap in blocks:
        _, th = _text_size(draw, text or "X", font)
        sized.append((text, font, colour, th, gap))
        total_h += th + gap

    y = max(margin, (H - total_h) // 2)
    for text, font, colour, th, gap in sized:
        if text:
            tw, _ = _text_size(draw, text, font)
            draw.text(((W - tw) // 2, y), text, fill=colour, font=font)
        y += th + gap

    img.save(path)
    return str(path)


# -- SHARED ROBUST CALL CORE (used by stickman AND ai_edit) ---------------

async def _call_flux_to_file(*, prompt, image_urls, real_path, placeholder_path,
                             scene_text, abort_event, sem):
    """
    Run ONE flux call to disk with full robustness. Returns
    (path, is_placeholder); `path` is always a real file (image or placeholder
    card). `image_urls` must be ALREADY-UPLOADED fal URLs (caller uploads).
    """
    real_path        = pathlib.Path(real_path)
    placeholder_path = pathlib.Path(placeholder_path)

    async with sem:
        if real_path.exists():
            print(f"-> exists, skipping - {scene_text[:55]}")
            return str(real_path), False

        if abort_event.is_set():
            p = _write_placeholder(
                placeholder_path, scene_text,
                "Generation stopped - earlier scene ran out of credits or timed out.",
            )
            print(f"// PLACEHOLDER (batch aborted) - {scene_text[:45]}")
            return p, True

        try:
            result = await asyncio.wait_for(
                fal_client.subscribe_async(MODEL, arguments={
                    "prompt": prompt,
                    "image_urls": image_urls,
                    "image_size": IMAGE_SIZE,
                }),
                timeout=PER_IMAGE_TIMEOUT_SEC,
            )
            data = await asyncio.to_thread(
                lambda: requests.get(result["images"][0]["url"], timeout=60).content
            )
            img = Image.open(io.BytesIO(data))
            if POSTPROCESS:
                img = deai_postprocess(img)
            img = _force_white_bg(img)          # <- black-bar fix
            save_clean(img, real_path)
            if placeholder_path.exists():
                try:
                    placeholder_path.unlink()
                except Exception:
                    pass
            print(f"OK {scene_text[:60]}")
            return str(real_path), False

        except asyncio.TimeoutError:
            abort_event.set()
            mins = PER_IMAGE_TIMEOUT_SEC // 60
            p = _write_placeholder(
                placeholder_path, scene_text,
                f"Timed out after {mins} min (fal hung - likely out of credits).",
            )
            print(f"XX TIMEOUT after {PER_IMAGE_TIMEOUT_SEC}s - placeholder - "
                  f"{scene_text[:40]}")
            return p, True

        except Exception as e:
            if _looks_like_credit_error(e):
                abort_event.set()
                p = _write_placeholder(
                    placeholder_path, scene_text,
                    "Ran out of fal credits while generating this scene.",
                )
                print(f"XX CREDIT ERROR - placeholder - {scene_text[:40]} - {e}")
                return p, True
            p = _write_placeholder(
                placeholder_path, scene_text, f"Generation error: {e}",
            )
            print(f"XX ERROR - placeholder - {scene_text[:40]} - {e}")
            return p, True


# -- STICKMAN GENERATION --------------------------------------------------

async def _generate_one(sem, narration, entry, ref_urls, out_dir, variant,
                        abort_event):
    stem        = _scene_stem(narration)
    real        = pathlib.Path(out_dir) / f"{stem}_{variant}.png"
    placeholder = pathlib.Path(out_dir) / f"{stem}_{variant}.placeholder.png"
    prompt      = f"{STYLE_PREFIX} {entry['search_term']}. {STYLE_SUFFIX}"
    path, is_ph = await _call_flux_to_file(
        prompt=prompt, image_urls=ref_urls,
        real_path=real, placeholder_path=placeholder,
        scene_text=narration, abort_event=abort_event, sem=sem,
    )
    return narration, variant, path, is_ph


async def _generate_all(prompts_file, out_dir, num_variants, process_type):
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = json.loads(pathlib.Path(prompts_file).read_text())
    targets = [
        (k, v) for k, v in data.items()
        if _search_type_str(v.get("search_type")) == process_type
    ]
    print(f"Processing {len(targets)} {process_type} scene(s) "
          f"x {num_variants} variant(s)")
    if not targets:
        return {}

    ref_urls = [await asyncio.to_thread(fal_client.upload_file, p) for p in REF_IMAGES]
    sem = asyncio.Semaphore(CONCURRENCY)
    abort_event = asyncio.Event()

    tasks = [
        _generate_one(sem, narration, entry, ref_urls, out_dir, variant, abort_event)
        for narration, entry in targets
        for variant in range(num_variants)
    ]
    results = await asyncio.gather(*tasks)

    mapping, placeholder_scenes = {}, set()
    for narration, variant, path, is_ph in results:
        if path is None:
            continue
        mapping.setdefault(narration, []).append((variant, path))
        if is_ph:
            placeholder_scenes.add(narration)

    if placeholder_scenes:
        print("\n" + "!" * 70)
        print(f"[stickman] WARNING: {len(placeholder_scenes)} scene(s) got a "
              f"PLACEHOLDER image. To retry: delete footage_candidates.json and "
              f"the *.placeholder.png files, top up, re-run.")
        print("!" * 70)

    return {
        narration: [p for _, p in sorted(pairs, key=lambda pv: pv[0])]
        for narration, pairs in mapping.items()
    }


def generate_stickman_images(prompts_file=DEFAULT_PROMPTS_FILE,
                             out_dir=DEFAULT_OUT_DIR,
                             num_variants=DEFAULT_NUM_VARIANTS,
                             process_type=PROCESS_TYPE):
    """
    Synchronous entry point. Returns { script_text: [image_path, ...] } with up
    to `num_variants` images per stickman scene. Never raises on a generation
    failure - failed scenes get a placeholder image path instead.
    """
    return asyncio.run(
        _generate_all(str(prompts_file), out_dir, num_variants, process_type)
    )


if __name__ == "__main__":
    generate_stickman_images()
