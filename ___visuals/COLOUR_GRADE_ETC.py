"""
COLOUR_GRADE_ETC
================


--------------------
Want to add postprocessing to all stock images...
this will be done by a new file.. COLOUR_GRADE_ETC.py


Here is the affects I want you to add to all the stock (both videos, and images... that are stock... )
Apply a unified cinematic film LUT (e.g., Kodak 2383 or Fujifilm Eterna style)
Apply a warm "golden hour" color temperature and tint shift
Apply a cinematic S-curve contrast adjustment
Apply soft highlight rolloff to smooth bright areas
Apply subtle pro-mist glow or bloom to highlights
Apply cinematic split-toning (warm highlights, cool/teal shadows)
Apply a soft light vignette to frame edges
Apply lifted blacks for a softer, less-digital shadow look
Apply fine cinematic sharpening for a crisp, high-end film feel

- Add the 'shot on film a golden hour' appearance to all the stock...
    ... light vingette etc.
    ... colour graded... etc.
    chromatic aberration ??
    so they all look like part of the same collection...
    and got that nice movie type appearance..

--------------------

Give every piece of STOCK footage (Pexels videos + images, Wikipedia stills,
explainer-stock composites, …) one unified "shot on film at golden hour"
cinematic look, so the whole video feels like a single graded collection.

Design
------
The entire grade is expressed as ONE ffmpeg filter chain, so:
  * IMAGES and VIDEOS get an IDENTICAL look (same filters, same numbers).
  * It's fast (no per-frame Python; ffmpeg does the heavy lifting).
  * No proprietary assets required — the film look is built procedurally from
    open, native ffmpeg filters (curves / colorbalance / colortemperature /
    gblur+blend / vignette / unsharp / rgbashift / noise). An optional
    open-source `.cube` LUT can be layered on top via `lut3d` if you have one.

The look is a stack of cinematic moves:
  1. (optional) chromatic aberration — subtle lens fringing            [rgbashift]
  2. (optional) film-stock LUT (Kodak 2383 / Fuji Eterna .cube)        [lut3d]
  3. warm "golden hour" white balance                                 [colortemperature]
  4. S-curve contrast + lifted blacks + soft highlight rolloff         [curves]
  5. split-toning: warm highlights, cool/teal shadows                 [colorbalance]
  6. saturation / exposure trim (e.g. low-sat Eterna feel)            [eq]
  7. pro-mist glow / bloom on highlights                              [split→gblur→screen blend]
  8. soft light vignette                                             [vignette]
  9. fine cinematic sharpening                                       [unsharp]
 10. subtle film grain                                               [noise]

There is ONE unified look (no variations) — a warm Kodak 2383 print vibe with a
teal-shadow / warm-highlight split. It stays CRISP: no chromatic aberration
(that read as a dizzy red/cyan "3D glasses" fringe), and only a whisper of bloom
and black-lift, so it reads clearly without looking blurry or faded.

Public API
----------
    grade_media(input_path, output_path, *, preset=..., config=None)
    grade_image(input_path, output_path, *, preset=..., config=None)
    grade_video(input_path, output_path, *, preset=..., config=None)
    PRESETS            -> dict[str, GradeConfig]   (single entry now)
    DEFAULT_PRESET     -> str
    preset_fingerprint(name) / config_fingerprint(cfg)   (for caching)
(The `preset` argument is kept for compatibility but always resolves to the one
final look.)

Standalone (before / after)
---------------------------
    uv run COLOUR_GRADE_ETC.py
Every file in `_TEST_IMAGES/stock-cinematic-test-stock/` is graded with the one
final look and written to a SINGLE folder `temp/colour_graded_FINAL/`, holding a
side-by-side BEFORE | AFTER per source plus the fully-graded output file.
"""

from __future__ import annotations

# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# Bump when the filter-building algorithm changes, to invalidate caches.
GRADE_VERSION = 2

_MODULE_DIR = Path(__file__).resolve().parent

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi", ".gif"}


# ===========================================================================
# CONFIG
# ===========================================================================


@dataclass
class GradeConfig:
    """All knobs for the cinematic grade. 0 disables an individual effect."""

    name: str = "cinematic_film"

    # --- lens / optics ------------------------------------------------------
    chromatic_aberration: bool = False
    ca_shift: int = 2  # px R/B split when CA is on

    # --- optional film-stock LUT (open-source .cube) ------------------------
    lut_cube_path: str | None = None

    # --- white balance ------------------------------------------------------
    warmth: float = 0.13  # 0..~0.25, golden-hour warmth

    # --- tone curve (lifted blacks + S-curve + highlight rolloff) -----------
    lifted_black: float = 0.045  # raise the black point (0 = true black)
    contrast: float = 0.16  # S-curve strength
    highlight_rolloff: float = 0.05  # soften whites (1.0 -> 1-rolloff)

    # --- split toning -------------------------------------------------------
    split_shadow_teal: float = 0.05  # push shadows toward teal
    split_highlight_warm: float = 0.06  # push highlights warm

    # --- exposure / saturation ---------------------------------------------
    saturation: float = 1.02
    brightness: float = 0.0  # eq brightness (-1..1)
    gamma: float = 1.0

    # --- pro-mist glow / bloom ---------------------------------------------
    bloom: float = 0.30  # screen-blend opacity 0..1
    bloom_sigma: float = 11.0  # glow softness
    bloom_threshold: float = 0.62  # highlight isolation knee 0..1

    # --- framing / texture --------------------------------------------------
    vignette: float = 0.32  # 0..1 soft vignette strength
    sharpen: float = 0.55  # unsharp luma amount
    grain: float = 5.0  # film grain strength (0 = off)


# ---------------------------------------------------------------------------
# THE LOOK — one unified cinematic grade (no variations to pick from).
#
# A warm Kodak 2383 print vibe (the favourite) with a "teal & orange" split.
# Deliberately NO chromatic aberration — it created a dizzy red/cyan "3D glasses"
# fringe at the edges. Bloom and black-lift are kept to a whisper, and blacks
# stay deep, so the result is warm and filmic but CRISP — not blurry or faded.
#
# ONE dial to rule them all: GRADE_INTENSITY scales every effect at once.
#   1.00 = full look,  0.28 = ~72% weaker (current, dialed down ~30% further
#   from the previous 0.4 to tone down the orange/colour-shift),  0.0 = no grade.
# Tweak just this number to make the whole grade stronger or subtler.
# ---------------------------------------------------------------------------

GRADE_INTENSITY = 0.28


def _build_final_grade(k: float) -> GradeConfig:
    """The one look, with every effect strength scaled by `k` (1.0 = full)."""
    return GradeConfig(
        name="cinematic_film",
        # OFF: chromatic aberration is what looked like 3D-glasses fringing.
        chromatic_aberration=False,
        # warm golden-hour white balance
        warmth=0.12 * k,
        # punchy cinematic S-curve; keep blacks deep so it never looks faded
        contrast=0.22 * k,
        lifted_black=0.02 * k,
        highlight_rolloff=0.03 * k,
        # teal shadows / warm highlights (the "teal & orange" split)
        split_shadow_teal=0.07 * k,
        split_highlight_warm=0.07 * k,
        # rich (but not garish) Kodak colour. Saturation is centred on 1.0, so
        # scale only the distance from neutral.
        saturation=1.0 + (1.08 - 1.0) * k,
        # only a whisper of pro-mist glow, on the brightest highlights only, so
        # the image stays sharp instead of hazy/blurry
        bloom=0.10 * k,
        bloom_sigma=8,  # bloom SHAPE, not strength — left fixed
        bloom_threshold=0.72,  # highlight knee, not strength — left fixed
        # framing + crisp high-end film feel + fine grain
        vignette=0.28 * k,
        sharpen=0.70 * k,
        grain=4 * k,
    )


FINAL_GRADE = _build_final_grade(GRADE_INTENSITY)

# Kept as a dict for API / cache-key compatibility, but there is only ONE look.
PRESETS: dict[str, GradeConfig] = {FINAL_GRADE.name: FINAL_GRADE}

DEFAULT_PRESET = FINAL_GRADE.name


def list_presets() -> list[str]:
    return list(PRESETS)


def _resolve_config(_preset: str | None, config: GradeConfig | None) -> GradeConfig:
    # One unified look now: an explicit `config` still wins, but any preset name
    # (including legacy names like "kodak_2383") resolves to the single final
    # grade — there are no variations to choose between anymore, so `_preset`
    # is accepted for API compatibility and otherwise ignored.
    if config is not None:
        return config
    return FINAL_GRADE


# ===========================================================================
# CACHE FINGERPRINTS
# ===========================================================================


def config_fingerprint(cfg: GradeConfig) -> str:
    """Stable short hash of (algorithm version + every knob) for cache keys."""
    blob = json.dumps({"v": GRADE_VERSION, **asdict(cfg)}, sort_keys=True)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:12]


def preset_fingerprint(preset: str) -> str:
    return config_fingerprint(_resolve_config(preset, None))


# ===========================================================================
# FILTER CHAIN
# ===========================================================================


def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _tone_curve_points(cfg: GradeConfig) -> str:
    """
    5-point master curve giving lifted blacks + an S-curve + highlight rolloff.
    """
    lb, c, hr = cfg.lifted_black, cfg.contrast, cfg.highlight_rolloff
    pts = [
        (0.00, _clip(lb)),
        (0.25, _clip(0.25 - c * 0.18 + lb * 0.5)),
        (0.50, 0.50),
        (0.75, _clip(0.75 + c * 0.12)),
        (1.00, _clip(1.0 - hr)),
    ]
    return " ".join(f"{x:.3f}/{y:.4f}" for x, y in pts)


def _escape_lut_path(path: str) -> str:
    # ffmpeg filtergraph: escape ':' and '\' inside option values.
    return path.replace("\\", "/").replace(":", r"\:")


def build_filterchain(cfg: GradeConfig) -> str:
    """
    Build the full ffmpeg -vf filtergraph string for `cfg`. Identical for
    images and videos.
    """
    pre: list[str] = []

    # 1) Chromatic aberration (subtle optical fringing).
    if cfg.chromatic_aberration and cfg.ca_shift:
        s = int(cfg.ca_shift)
        pre.append(f"rgbashift=rh=-{s}:bh={s}:edge=smear")

    # 2) Optional film-stock LUT.
    if cfg.lut_cube_path:
        pre.append(f"lut3d=file='{_escape_lut_path(cfg.lut_cube_path)}'")

    # 3) Warm "golden hour" white balance. ffmpeg colortemperature: lower
    #    Kelvin = warmer. Map warmth 0..~0.25 down from 6500K.
    if cfg.warmth:
        kelvin = int(round(6500 - cfg.warmth * 11000))
        kelvin = max(3200, min(8000, kelvin))
        pre.append(f"colortemperature=temperature={kelvin}:pl=1")

    # 4) Master tone curve (lifted blacks + S-curve + highlight rolloff).
    pre.append(f"curves=m='{_tone_curve_points(cfg)}'")

    # 5) Split toning — teal shadows, warm highlights.
    if cfg.split_shadow_teal or cfg.split_highlight_warm:
        st, hw = cfg.split_shadow_teal, cfg.split_highlight_warm
        # Highlights are pushed toward AMBER (red + green up, blue down) — NOT
        # magenta — so bright skies go warm-cream, never pink. Shadows go teal.
        pre.append(
            "colorbalance="
            f"rs={-st:.3f}:gs={st * 0.30:.3f}:bs={st:.3f}:"
            f"rm={hw * 0.18:.3f}:gm={hw * 0.08:.3f}:bm={-hw * 0.16:.3f}:"
            f"rh={hw * 0.85:.3f}:gh={hw * 0.55:.3f}:bh={-hw * 0.45:.3f}"
        )

    # 6) Exposure / saturation trim.
    if cfg.saturation != 1.0 or cfg.brightness != 0.0 or cfg.gamma != 1.0:
        pre.append(
            f"eq=saturation={cfg.saturation:.3f}"
            f":brightness={cfg.brightness:.3f}:gamma={cfg.gamma:.3f}"
        )

    pre_str = ",".join(pre)

    # 8/9/10) Post-bloom moves: vignette -> sharpen -> grain.
    post: list[str] = []
    if cfg.vignette:
        # Map strength 0..1 to the vignette lens angle (bigger = darker corners).
        angle = 0.18 + _clip(cfg.vignette) * 0.52
        post.append(f"vignette=angle={angle:.4f}")
    if cfg.sharpen:
        post.append(
            "unsharp=luma_msize_x=5:luma_msize_y=5"
            f":luma_amount={cfg.sharpen:.3f}:chroma_amount=0"
        )
    if cfg.grain:
        post.append(f"noise=alls={int(round(cfg.grain))}:allf=t+u")
    post_str = ",".join(post)

    # 7) Pro-mist glow / bloom: isolate highlights, blur, screen-blend back.
    if cfg.bloom > 0:
        thr = _clip(cfg.bloom_threshold, 0.05, 0.95)
        knee = min(thr + 0.20, 0.95)
        hi_curve = f"0/0 {thr:.3f}/0 {knee:.3f}/0.45 1/1"
        # CRITICAL: the split / blur / screen-blend MUST run in RGB (format=gbrp).
        # ffmpeg's blend 'screen' is per-plane; in YUV it screens the CHROMA
        # planes too, blowing highlights toward magenta even with a neutral glow.
        # We blend in RGB, then convert back to yuv420p for the post chain.
        # The glow is also lightly desaturated so it reads as clean diffusion.
        graph = (
            f"{pre_str},format=gbrp,split=2[cg_base][cg_hi];"
            f"[cg_hi]curves=m='{hi_curve}',eq=saturation=0.12,"
            f"gblur=sigma={cfg.bloom_sigma:.2f}[cg_glow];"
            f"[cg_base][cg_glow]blend=all_mode=screen:all_opacity={cfg.bloom:.3f}"
            f",format=yuv420p"
        )
        return f"{graph},{post_str}" if post_str else graph

    chain = pre_str
    return f"{chain},{post_str}" if post_str else chain


# ===========================================================================
# APPLYING THE GRADE
# ===========================================================================


def _run_ffmpeg(cmd: list[str], what: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or "")[-1200:]
        raise RuntimeError(f"[grade] ffmpeg failed ({what}):\n{tail}")


def grade_image(
    input_path: str,
    output_path: str,
    *,
    preset: str | None = DEFAULT_PRESET,
    config: GradeConfig | None = None,
) -> str:
    """Grade a single still. Returns output_path."""
    cfg = _resolve_config(preset, config)
    vf = build_filterchain(cfg)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        input_path,
        "-vf",
        vf,
        "-frames:v",
        "1",
    ]
    if out.suffix.lower() in {".jpg", ".jpeg"}:
        cmd += ["-q:v", "2"]
    cmd.append(str(out))

    _run_ffmpeg(cmd, f"image {Path(input_path).name} [{cfg.name}]")
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"[grade] produced no output for image {input_path}")
    return str(out)


def grade_video(
    input_path: str,
    output_path: str,
    *,
    preset: str | None = DEFAULT_PRESET,
    config: GradeConfig | None = None,
) -> str:
    """Grade a video (preserving duration + audio). Returns output_path."""
    cfg = _resolve_config(preset, config)
    vf = build_filterchain(cfg)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        input_path,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "copy",
        str(out),
    ]
    try:
        _run_ffmpeg(cmd, f"video {Path(input_path).name} [{cfg.name}]")
    except RuntimeError:
        # Some sources have no audio stream — retry without copying audio.
        cmd_noaudio = [a for a in cmd if a not in ("-c:a", "copy")]
        cmd_noaudio.insert(-1, "-an")
        _run_ffmpeg(
            cmd_noaudio, f"video(no-audio) {Path(input_path).name} [{cfg.name}]"
        )
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"[grade] produced no output for video {input_path}")
    return str(out)


def is_video(path: str) -> bool:
    return Path(path.split("?", 1)[0]).suffix.lower() in VIDEO_EXTS


def is_image(path: str) -> bool:
    return Path(path.split("?", 1)[0]).suffix.lower() in IMAGE_EXTS


def grade_media(
    input_path: str,
    output_path: str,
    *,
    preset: str | None = DEFAULT_PRESET,
    config: GradeConfig | None = None,
) -> str:
    """Dispatch to grade_image / grade_video by file extension."""
    if is_video(input_path):
        return grade_video(input_path, output_path, preset=preset, config=config)
    return grade_image(input_path, output_path, preset=preset, config=config)


# ===========================================================================
# STANDALONE — grade every test source with the one look; write BEFORE | AFTER
# pairs + the fully-graded files into a single folder.
# ===========================================================================

_TEST_SRC_DIR = _MODULE_DIR / "_TEST_IMAGES" / "stock-cinematic-test-stock"
_TEST_OUT_DIR = _MODULE_DIR / "temp"
# Everything from a standalone run lands in this ONE folder.
_FINAL_OUT_DIR = _TEST_OUT_DIR / "colour_graded_FINAL"

# Also grade full videos end-to-end (slower). The BEFORE | AFTER comparison
# still is produced either way; set False to skip the full-video render.
GRADE_FULL_VIDEOS = False


def _extract_frame(video_path: str, out_png: str, at_seconds: float = 1.0) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        f"{at_seconds:.2f}",
        "-i",
        video_path,
        "-frames:v",
        "1",
        str(out_png),
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    p = Path(out_png)
    if p.exists() and p.stat().st_size > 0:
        return True
    # Fallback: very first frame.
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            video_path,
            "-frames:v",
            "1",
            str(out_png),
        ],
        capture_output=True,
        text=True,
    )
    return p.exists() and p.stat().st_size > 0


def _load_font(px: int):
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, px)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=px)
    except Exception:
        return ImageFont.load_default()


def _make_contact_sheet(
    tiles: list[tuple[str, str]], out_path: str, cols: int = 3, tile_w: int = 620
) -> None:
    """tiles = [(label, image_path), ...] -> labelled grid jpg."""
    from PIL import Image, ImageDraw

    loaded = []
    for label, img_path in tiles:
        try:
            im = Image.open(img_path).convert("RGB")
        except Exception:
            continue
        w, h = im.size
        th = max(1, round(tile_w * h / w))
        loaded.append((label, im.resize((tile_w, th), Image.LANCZOS)))
    if not loaded:
        return

    bar = max(26, tile_w // 18)
    tile_h = max(im.size[1] for _, im in loaded) + bar
    rows = (len(loaded) + cols - 1) // cols
    pad = 8
    sheet = Image.new(
        "RGB",
        (cols * tile_w + (cols + 1) * pad, rows * tile_h + (rows + 1) * pad),
        (18, 18, 20),
    )
    draw = ImageDraw.Draw(sheet)
    font = _load_font(max(16, bar - 10))

    for i, (label, im) in enumerate(loaded):
        r, c = divmod(i, cols)
        x = pad + c * (tile_w + pad)
        y = pad + r * (tile_h + pad)
        draw.rectangle([x, y, x + tile_w, y + bar], fill=(0, 0, 0))
        draw.text(
            (x + 8, y + bar // 2), label, fill=(255, 220, 150), font=font, anchor="lm"
        )
        sheet.paste(im, (x, y + bar))

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=90)


def _run_standalone() -> None:
    print("=" * 70)
    print("COLOUR_GRADE_ETC — standalone BEFORE / AFTER (one unified look)")
    print("=" * 70)
    if not _TEST_SRC_DIR.exists():
        print(f"[grade] test source dir not found: {_TEST_SRC_DIR}")
        print("[grade] put some stock images/videos there and re-run.")
        return

    sources = sorted(
        p
        for p in _TEST_SRC_DIR.iterdir()
        if p.suffix.lower() in (IMAGE_EXTS | VIDEO_EXTS)
    )
    if not sources:
        print(f"[grade] no media in {_TEST_SRC_DIR}")
        return

    cfg = _resolve_config(DEFAULT_PRESET, None)
    out_dir = _FINAL_OUT_DIR
    frames_dir = out_dir / "_frames"  # scratch stills for the comparisons
    frames_dir.mkdir(parents=True, exist_ok=True)

    print(f"[grade] {len(sources)} source(s) — single look: '{cfg.name}'")
    print(f"[grade] writing to one folder: {out_dir.relative_to(_MODULE_DIR)}/")

    for src in sources:
        stem = src.stem
        print(f"\n[grade] ── {src.name} ──")
        video = is_video(str(src))

        # 1) Full processed output + the 'after' still for the comparison.
        if video:
            before_still = frames_dir / f"{stem}_before.png"
            if not _extract_frame(str(src), str(before_still)):
                print(f"[grade]   could not extract a frame from {src.name} — skipping")
                continue
            before_still = str(before_still)

            after_still = frames_dir / f"{stem}_after.jpg"
            try:
                grade_image(before_still, str(after_still), config=cfg)
            except Exception as exc:
                print(f"[grade]   ✗ grade failed: {exc}")
                continue
            after_still = str(after_still)

            if GRADE_FULL_VIDEOS:
                graded_vid = out_dir / f"{stem}_graded.mp4"
                try:
                    grade_video(str(src), str(graded_vid), config=cfg)
                    print(
                        f"[grade]   ✓ graded video -> {graded_vid.relative_to(_MODULE_DIR)}"
                    )
                except Exception as exc:
                    print(f"[grade]   ✗ full-video grade failed: {exc}")
        else:
            before_still = str(src)
            graded_img = out_dir / f"{stem}_graded.jpg"
            try:
                grade_image(str(src), str(graded_img), config=cfg)
            except Exception as exc:
                print(f"[grade]   ✗ grade failed: {exc}")
                continue
            after_still = str(graded_img)
            print(f"[grade]   ✓ graded image -> {graded_img.relative_to(_MODULE_DIR)}")

        # 2) Side-by-side BEFORE | AFTER, into the same single folder.
        sheet = out_dir / f"{stem}__before_after.jpg"
        _make_contact_sheet(
            [("BEFORE", before_still), ("AFTER", after_still)], str(sheet), cols=2
        )
        print(f"[grade]   ✓ before/after -> {sheet.relative_to(_MODULE_DIR)}")

    # Drop the scratch stills so the folder holds only before/afters + outputs.
    try:
        import shutil

        shutil.rmtree(frames_dir, ignore_errors=True)
    except Exception:
        pass

    print("\n" + "=" * 70)
    print("[grade] DONE — one folder of before/afters + graded outputs:")
    print(f"[grade]   {out_dir}")
    print("=" * 70)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] not in PRESETS:
        # Ad-hoc one-off: COLOUR_GRADE_ETC.py <input> [output]
        inp = sys.argv[1]
        outp = (
            sys.argv[2]
            if len(sys.argv) > 2
            else str(_FINAL_OUT_DIR / f"{Path(inp).stem}_graded{Path(inp).suffix}")
        )
        print(grade_media(inp, outp))
    else:
        _run_standalone()
