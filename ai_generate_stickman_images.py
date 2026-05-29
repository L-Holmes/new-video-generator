"""
Stickman Batch Image Generator

Generates AI stickman images for scenes flagged search_type == "stickman".

## --- USAGE (standalone) ---
uv run ai_generate_stickman_images.py
    -> uses the module defaults (DEFAULT_PROMPTS_FILE / DEFAULT_OUT_DIR below)

## --- USAGE (from the pipeline) ---
from ai_generate_stickman_images import generate_stickman_images
mapping = generate_stickman_images(
    prompts_file="myproj_script_to_search_term.json",
    out_dir="myproj-CACHE/stickman_scenes",
    num_variants=2,
)
# mapping: { script_text: [path_variant0, path_variant1, ...], ... }

## --- OUTPUT ---
For each stickman scene, `num_variants` images are written as:
    <out_dir>/<stem>_<variant>.png
where <stem> is derived from the SCRIPT TEXT (readable prefix + 8-char hash):
    Take_black_pepper__a1b2c3d4_0.png

A scene whose generation fails (out of credits, timeout, or any other error)
gets a PLACEHOLDER image written to a DISTINCT name instead:
    <out_dir>/<stem>_<variant>.placeholder.png
The placeholder is a captioned card ("STICKMAN IMAGE UNAVAILABLE" + reason +
the scene text) so the pipeline keeps running and the failure is obvious in
the review grid. Because the placeholder uses a different filename, a later
re-run (after topping up credits) will see no real <stem>_<variant>.png and
regenerate it - and on success the stale placeholder is deleted.

## --- ROBUSTNESS ---
- Each fal call is wrapped in a timeout (PER_IMAGE_TIMEOUT_SEC). When fal hangs
  - which is exactly what an out-of-credits account does - the call is
  cancelled instead of freezing the whole pipeline forever.
- The FIRST time a call times out OR returns a credit/billing-looking error we
  set a shared abort flag: every remaining scene then short-circuits straight
  to a placeholder WITHOUT calling fal. So a systemic failure costs at most one
  timeout period, not one per scene.

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
CONCURRENCY  = 1                          # max parallel fal calls
PROCESS_TYPE = "stickman"
POSTPROCESS  = True

# Generous per-call backstop. fal normally answers in well under a minute; a
# call still pending after this long is almost certainly a hang (out of
# credits / fal outage), so we cancel it and fall back to a placeholder. With
# CONCURRENCY=1 a systemic hang costs ONE timeout period before the abort flag
# trips and the rest become instant placeholders. Lower this for faster
# failure (e.g. 180 = 3 min) if you'd rather not wait the full 10.
PER_IMAGE_TIMEOUT_SEC = 360

STYLE_PREFIX = ("generate me, with a minimal, clean line look as if drawn on "
                "microsoft paint with low pixel density: ")
STYLE_SUFFIX = (". Again, in hand drawn ms paint style, in colour, minimal, "
                "white background")

# -- Placeholder card styling --------------------------------------------
PLACEHOLDER_SIZE = 1024            # match square_hd so it sits in the grid right
PLACEHOLDER_BG   = (250, 250, 250)
_PH_RED          = (200, 50, 50)
_PH_DARK         = (40, 40, 40)
_PH_GREY         = (120, 120, 120)


def _search_type_str(value) -> str:
    """Accept either a raw string or a MediaType-like enum exposing `.value`."""
    return value.value if hasattr(value, "value") else value


def _scene_stem(script_text: str) -> str:
    """
    Unique, stable, human-readable filename stem for ONE scene.

    Derived from script_text (the unique key everywhere else in the pipeline),
    NOT from `position` (which is not reliably unique). The 8-char hash keeps it
    unique even when two scenes share the first 40 readable characters.
    """
    safe   = re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:40] or "scene"
    digest = hashlib.md5(script_text.encode("utf-8")).hexdigest()[:8]
    return f"{safe}_{digest}"


# Substrings that suggest "the account can't make this call" rather than a
# transient blip. Heuristic - fal's exact wording may differ, so add to this
# list if a credit error ever slips through as an ordinary per-scene error.
_CREDIT_ERROR_NEEDLES = (
    "credit", "quota", "insufficient", "balance", "exhaust", "payment",
    "billing", "out of funds", "402", "403", "unauthorized", "forbidden",
)


def _looks_like_credit_error(exc) -> bool:
    msg = str(exc).lower()
    return any(n in msg for n in _CREDIT_ERROR_NEEDLES)


# -- Placeholder rendering -----------------------------------------------

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


def _write_placeholder(path: pathlib.Path, script_text: str, reason: str) -> str:
    """Render a captioned 'unavailable' card so a failed scene is obvious."""
    path.parent.mkdir(parents=True, exist_ok=True)
    W = H = PLACEHOLDER_SIZE
    img  = Image.new("RGB", (W, H), PLACEHOLDER_BG)
    draw = ImageDraw.Draw(img)

    for i in range(8):  # red warning border
        draw.rectangle([i, i, W - 1 - i, H - 1 - i], outline=_PH_RED)

    margin = 80
    max_w  = W - 2 * margin

    f_title  = _load_font(50)
    f_reason = _load_font(38)
    f_label  = _load_font(28)
    f_script = _load_font(40)

    blocks = [("STICKMAN IMAGE UNAVAILABLE", f_title, _PH_RED, 28)]
    for ln in _wrap(draw, reason, f_reason, max_w):
        blocks.append((ln, f_reason, _PH_DARK, 6))
    blocks.append(("", f_label, _PH_DARK, 24))
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


async def _generate_one(sem, narration, entry, ref_urls, out_dir, variant,
                        abort_event: asyncio.Event):
    """
    Generate ONE variant for a single scene.

    Returns (narration, variant, path, is_placeholder). `path` is always a real
    file on disk - either the generated image or a placeholder card. The path
    is returned even on skip so the caller always learns where the image lives.
    """
    async with sem:
        stem        = _scene_stem(narration)
        real        = pathlib.Path(out_dir) / f"{stem}_{variant}.png"
        placeholder = pathlib.Path(out_dir) / f"{stem}_{variant}.placeholder.png"

        if real.exists():
            print(f"-> v{variant} exists, skipping - {narration[:55]}")
            return narration, variant, str(real), False

        # A previous call already tripped the abort flag (credits/timeout):
        # don't bother calling fal, just emit a placeholder instantly.
        if abort_event.is_set():
            p = _write_placeholder(
                placeholder, narration,
                "Generation stopped - earlier scene ran out of credits or timed out.",
            )
            print(f"// v{variant} PLACEHOLDER (batch aborted) - {narration[:45]}")
            return narration, variant, p, True

        try:
            result = await asyncio.wait_for(
                fal_client.subscribe_async(MODEL, arguments={
                    "prompt": f"{STYLE_PREFIX} {entry['search_term']}. {STYLE_SUFFIX}",
                    "image_urls": ref_urls,
                    "image_size": "square_hd",
                }),
                timeout=PER_IMAGE_TIMEOUT_SEC,
            )
            data = await asyncio.to_thread(
                lambda: requests.get(result["images"][0]["url"], timeout=60).content
            )
            img = Image.open(io.BytesIO(data))
            if POSTPROCESS:
                img = deai_postprocess(img)
            save_clean(img, real)
            if placeholder.exists():       # we succeeded this time - clear stale card
                try:
                    placeholder.unlink()
                except Exception:
                    pass
            print(f"OK v{variant} {narration[:55]}")
            return narration, variant, str(real), False

        except asyncio.TimeoutError:
            abort_event.set()  # a 10-min hang is almost certainly systemic
            mins = PER_IMAGE_TIMEOUT_SEC // 60
            p = _write_placeholder(
                placeholder, narration,
                f"Timed out after {mins} min (fal hung - likely out of credits).",
            )
            print(f"XX v{variant} TIMEOUT after {PER_IMAGE_TIMEOUT_SEC}s "
                  f"- placeholder - {narration[:40]}")
            return narration, variant, p, True

        except Exception as e:
            if _looks_like_credit_error(e):
                abort_event.set()
                p = _write_placeholder(
                    placeholder, narration,
                    "Ran out of fal credits while generating this scene.",
                )
                print(f"XX v{variant} CREDIT ERROR - placeholder - "
                      f"{narration[:40]} - {e}")
                return narration, variant, p, True
            # One-off error (bad decode, single 5xx): placeholder this scene
            # only, keep going for the rest.
            p = _write_placeholder(
                placeholder, narration, f"Generation error: {e}",
            )
            print(f"XX v{variant} ERROR - placeholder - {narration[:40]} - {e}")
            return narration, variant, p, True


async def _generate_all(prompts_file, out_dir, num_variants, process_type):
    """
    Generate `num_variants` images for every scene whose search_type matches
    `process_type`. Returns { narration: [path, ...] } in variant order. Always
    returns normally (failures become placeholder paths) so the pipeline can
    proceed; a summary of any placeholders is printed at the end.
    """
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

    mapping = {}
    placeholder_scenes = set()
    for narration, variant, path, is_placeholder in results:
        if path is None:
            continue
        mapping.setdefault(narration, []).append((variant, path))
        if is_placeholder:
            placeholder_scenes.add(narration)

    if placeholder_scenes:
        print("\n" + "!" * 70)
        print(f"[stickman] WARNING: {len(placeholder_scenes)} scene(s) got a "
              f"PLACEHOLDER image instead of a real one.")
        if abort_event.is_set():
            print("[stickman] The batch ABORTED early (out of credits / timeout).")
        print("[stickman] To retry after topping up: delete the candidates cache "
              "(footage_candidates.json) and the *.placeholder.png files, then "
              "re-run. Real images already on disk are kept and skipped.")
        print("!" * 70)

    return {
        narration: [p for _, p in sorted(pairs, key=lambda pv: pv[0])]
        for narration, pairs in mapping.items()
    }


def generate_stickman_images(
    prompts_file=DEFAULT_PROMPTS_FILE,
    out_dir=DEFAULT_OUT_DIR,
    num_variants=DEFAULT_NUM_VARIANTS,
    process_type=PROCESS_TYPE,
):
    """
    Synchronous entry point for the pipeline.

    Returns { script_text: [image_path, ...] } with up to `num_variants`
    images per stickman scene. Never raises on a generation failure - failed
    scenes get a placeholder image path instead.
    """
    return asyncio.run(
        _generate_all(str(prompts_file), out_dir, num_variants, process_type)
    )


if __name__ == "__main__":
    generate_stickman_images()
