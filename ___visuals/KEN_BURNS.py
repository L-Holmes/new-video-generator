"""
Ken Burns (pan/zoom) effect for static images: classification of effects,
the ffmpeg zoompan filter builder, per-clip render + cache, and the
apply_ken_burns_to_final_data() pass over final_data.

Footage-path classification / resolution helpers live in CACHE_IO (shared with
the colour-grade + manual stages); the history identity-entry write is done by
CACHE_IO.add_path_remap_to_history in main().
"""

from __future__ import annotations

# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import hashlib
import random
import subprocess
from enum import Enum
from pathlib import Path

from ___visuals.CACHE_IO import _classify_footage_path, _is_image_path, _resolve_to_local_path
from CONFIG import _CACHE_DIR, APPLY_KEN_BURNS_AFFECT, DEBUG, ProgressTracker


# === BEGIN verbatim move from main.py (Ken Burns) ===
# ===========================================================================
# KEN BURNS EFFECT FOR STATIC IMAGES
# ===========================================================================
# Static images (Pexels / Wikipedia stills selected via the review GUI) are
# converted into short MP4s with a randomly-chosen but weighted Ken Burns
# style motion before being handed to the stitcher. Each (image, effect,
# duration) combo is cached as kb-<md5>.mp4 so re-runs skip re-encoding.


class KenBurnsEffect(Enum):
    ZOOM_IN_CENTER = "zoom_in_center"
    ZOOM_OUT_CENTER = "zoom_out_center"
    PAN_LEFT_TO_RIGHT = "pan_left_to_right"
    PAN_RIGHT_TO_LEFT = "pan_right_to_left"
    TILT_BOTTOM_TO_TOP = "tilt_bottom_to_top"
    TILT_TOP_TO_BOTTOM = "tilt_top_to_bottom"
    ZOOM_IN_PAN_LR = "zoom_in_pan_lr"
    ZOOM_IN_PAN_RL = "zoom_in_pan_rl"
    ZOOM_OUT_PAN_LR = "zoom_out_pan_lr"
    ZOOM_OUT_PAN_RL = "zoom_out_pan_rl"


# Probabilities must sum to ~1.0. random.choices handles normalisation
# internally so small rounding is fine.
KEN_BURNS_EFFECT_PROBABILITIES: dict[KenBurnsEffect, float] = {
    KenBurnsEffect.ZOOM_IN_CENTER: 0.28,
    KenBurnsEffect.ZOOM_OUT_CENTER: 0.22,
    KenBurnsEffect.PAN_LEFT_TO_RIGHT: 0.14,
    KenBurnsEffect.PAN_RIGHT_TO_LEFT: 0.12,
    KenBurnsEffect.TILT_BOTTOM_TO_TOP: 0.06,
    KenBurnsEffect.TILT_TOP_TO_BOTTOM: 0.05,
    KenBurnsEffect.ZOOM_IN_PAN_LR: 0.04,
    KenBurnsEffect.ZOOM_IN_PAN_RL: 0.04,
    KenBurnsEffect.ZOOM_OUT_PAN_LR: 0.03,
    KenBurnsEffect.ZOOM_OUT_PAN_RL: 0.02,
}

# Rendering parameters — tweak these to taste.
KEN_BURNS_OUTPUT_RESOLUTION: tuple[int, int] = (1920, 1080)
KEN_BURNS_WORKING_RESOLUTION: tuple[int, int] = (4000, 2250)  # 1.78 aspect, oversampled
KEN_BURNS_ZOOM_DELTA: float = 0.05  # 5% of frame
KEN_BURNS_PAN_DELTA: float = 0.05  # 5% of working dim
KEN_BURNS_FPS: int = 30
KEN_BURNS_RENDER_SAFETY_PAD_SEC: float = 0.08  # same trick as read-out scenes

KEN_BURNS_CACHE_DIR = Path(f"{_CACHE_DIR}/ken_burns")

def _pick_ken_burns_effect(seed_string: str) -> KenBurnsEffect:
    """Deterministic per-image weighted random pick — same image, same effect."""
    rng = random.Random(seed_string)
    effects = list(KEN_BURNS_EFFECT_PROBABILITIES.keys())
    weights = list(KEN_BURNS_EFFECT_PROBABILITIES.values())
    return rng.choices(effects, weights=weights, k=1)[0]


def _build_ken_burns_filter(effect: KenBurnsEffect, duration: float) -> str:
    """
    Build the ffmpeg -vf chain for `effect` over `duration` seconds.

    Uses zoompan — the canonical Ken Burns filter. (The `crop` filter's
    w/h are not per-frame, so it can't do zoom; zoompan handles zoom+pan
    in one go for all 10 effects.)

    Pipeline:  cover-fit to oversampled canvas  ->  zoompan  ->  output
    Easing:    smoothstep on `on/(tf-1)` clamped to [0,1]  (safety pad
               frames at the end hold the final position stationary).
    """
    out_w, out_h = KEN_BURNS_OUTPUT_RESOLUTION
    over_w, over_h = KEN_BURNS_WORKING_RESOLUTION
    fps = KEN_BURNS_FPS
    z_delta = KEN_BURNS_ZOOM_DELTA  # 0.05
    pan_z = 1 + KEN_BURNS_PAN_DELTA  # 1.05 — baseline zoom
    # for pan-only effects so
    # there's room to move

    # Total animation frames — clamped to ≥2 so on/(tf-1) is safe.
    tf = max(2, int(round(duration * fps)))

    # smoothstep-eased progress p ∈ [0,1] across the scene's actual runtime.
    # `\,` escapes the comma so the filtergraph parser doesn't treat it as
    # a filter separator.
    p = f"min(on/{tf - 1}\\,1)"
    s = f"({p}*{p}*(3-2*{p}))"

    # Visible window in input space is (iw/zoom, ih/zoom).
    # x/y are the top-left corner of that window in input coords.
    cx, cy = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    max_x, max_y = "(iw-iw/zoom)", "(ih-ih/zoom)"

    match effect:
        case KenBurnsEffect.ZOOM_IN_CENTER:
            z, x, y = f"(1+{z_delta}*{s})", cx, cy
        case KenBurnsEffect.ZOOM_OUT_CENTER:
            z, x, y = f"(1+{z_delta}-{z_delta}*{s})", cx, cy
        case KenBurnsEffect.PAN_LEFT_TO_RIGHT:
            z, x, y = f"{pan_z}", f"{max_x}*{s}", cy
        case KenBurnsEffect.PAN_RIGHT_TO_LEFT:
            z, x, y = f"{pan_z}", f"{max_x}*(1-{s})", cy
        case KenBurnsEffect.TILT_BOTTOM_TO_TOP:
            z, x, y = f"{pan_z}", cx, f"{max_y}*(1-{s})"
        case KenBurnsEffect.TILT_TOP_TO_BOTTOM:
            z, x, y = f"{pan_z}", cx, f"{max_y}*{s}"
        case KenBurnsEffect.ZOOM_IN_PAN_LR:
            z = f"(1+{z_delta}*{s})"
            x, y = f"{max_x}*(0.3+0.4*{s})", cy
        case KenBurnsEffect.ZOOM_IN_PAN_RL:
            z = f"(1+{z_delta}*{s})"
            x, y = f"{max_x}*(0.7-0.4*{s})", cy
        case KenBurnsEffect.ZOOM_OUT_PAN_LR:
            z = f"(1+{z_delta}-{z_delta}*{s})"
            x, y = f"{max_x}*(0.3+0.4*{s})", cy
        case KenBurnsEffect.ZOOM_OUT_PAN_RL:
            z = f"(1+{z_delta}-{z_delta}*{s})"
            x, y = f"{max_x}*(0.7-0.4*{s})", cy
        case _:
            raise ValueError(f"Unknown Ken Burns effect: {effect}")

    # Cover-fit to oversampled canvas; gives zoompan plenty of pixels to
    # crop from when zoomed in. force_original_aspect_ratio=increase scales
    # up so both dims meet/exceed the target, then crop trims the excess.
    prep = (
        f"scale={over_w}:{over_h}:force_original_aspect_ratio=increase,"
        f"crop={over_w}:{over_h},setsar=1"
    )

    # d=1 → each input frame produces exactly 1 output frame. Combined with
    # `-loop 1 -framerate fps -i image -t duration` at the CLI level, this
    # gives us a clean monotonic `on` from 0 to duration*fps.
    zp = f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={out_w}x{out_h}:fps={fps}"

    return f"{prep},{zp}"


def _ken_burns_cache_path(
    image_path: str, effect: KenBurnsEffect, duration: float
) -> Path:
    """Stable cache filename keyed on (image, effect, duration)."""
    key = f"{image_path}|{effect.value}|{round(duration, 3)}"
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    return KEN_BURNS_CACHE_DIR / f"kb-{h}.mp4"


def _render_ken_burns_clip(
    image_path: str, effect: KenBurnsEffect, duration: float
) -> str:
    """
    Render a Ken Burns MP4 from `image_path`. Caches by (image, effect, duration).
    Returns the output path (str).
    """
    KEN_BURNS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _ken_burns_cache_path(image_path, effect, duration)

    if output_path.exists() and output_path.stat().st_size > 1024:
        if DEBUG:
            print(f"  [ken-burns cache hit] {output_path.name}")
        return str(output_path)

    render_duration = duration + KEN_BURNS_RENDER_SAFETY_PAD_SEC
    filter_str = _build_ken_burns_filter(effect, duration)  # ← was render_duration

    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(KEN_BURNS_FPS),
        "-i",
        image_path,
        "-t",
        f"{render_duration:.3f}",
        "-vf",
        filter_str,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-r",
        str(KEN_BURNS_FPS),
        "-an",
        str(output_path),
    ]

    if DEBUG:
        print(
            f"  [ken-burns render] {Path(image_path).name} "
            f"effect={effect.value} dur={duration:.2f}s -> {output_path.name}"
        )

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ken-burns] FATAL: ffmpeg failed for {image_path}")
        print(f"[ken-burns] filter: {filter_str}")
        print(f"[ken-burns] stderr (tail): {result.stderr[-800:]}")
        if output_path.exists():
            output_path.unlink()
        raise RuntimeError(f"Ken Burns render failed: {image_path}")

    return str(output_path)
def apply_ken_burns_to_final_data(
    final_data: list[dict],
) -> tuple[list[dict], dict[str, str]]:
    """
    Walk `final_data` and replace every static-image footage entry with a
    freshly-rendered Ken Burns MP4 of the same trim duration.

    Image entries may be keyed by URL (need history.json lookup) or by an
    already-local path. We handle both.

    Returns (final_data, path_remap) where path_remap is {old_key: new_mp4}
    so the caller can update history.json.
    """
    print("\n" + "=" * 70)
    print("[ken-burns] APPLYING Ken Burns to static images in final_data")
    print(f"[ken-burns] enabled={APPLY_KEN_BURNS_AFFECT}")
    print("=" * 70)

    if not APPLY_KEN_BURNS_AFFECT:
        print("[ken-burns] APPLY_KEN_BURNS_AFFECT=False — skipping")
        return final_data, {}

    # ── Diagnostic scan — categorise everything in final_data ─────────
    print(f"\n[ken-burns:scan] Scanning {len(final_data)} scene(s) in final_data...")
    video_exts = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
    n_total = n_img_url = n_img_local = n_videos = n_other = 0

    for entry in final_data:
        for footage_item in entry.get("footage", []):
            for path in footage_item:
                n_total += 1
                kind = _classify_footage_path(path)
                if kind == "image":
                    if path.startswith(("http://", "https://")):
                        n_img_url += 1
                    else:
                        n_img_local += 1
                elif kind == "video":
                    n_videos += 1
                else:
                    n_other += 1

    print(f"[ken-burns:scan]   total footage entries: {n_total}")
    print(f"[ken-burns:scan]   images (URL keys):     {n_img_url}")
    print(f"[ken-burns:scan]   images (local keys):   {n_img_local}")
    print(f"[ken-burns:scan]   videos:                {n_videos}")
    print(f"[ken-burns:scan]   other/unknown:         {n_other}")

    total_images = n_img_url + n_img_local
    if total_images == 0:
        print("[ken-burns] no static images in final_data — nothing to do")
        return final_data, {}

    print(f"\n[ken-burns] processing {total_images} static image(s)...")
    tracker = ProgressTracker(total=total_images, label="KEN BURNS")
    path_remap: dict[str, str] = {}
    n_rendered = n_skipped_missing = n_failed = 0

    for entry in final_data:
        new_footage: list[dict] = []
        for footage_item in entry.get("footage", []):
            new_item: dict = {}
            for path, trim in footage_item.items():
                if not _is_image_path(path):
                    new_item[path] = trim
                    continue

                local_path = _resolve_to_local_path(path)
                if not local_path:
                    print(f"\n[ken-burns] WARNING: can't resolve to disk: {path}")
                    print(
                        f"[ken-burns]   (not a URL in history.json AND not a "
                        f"valid local path) — keeping original entry"
                    )
                    new_item[path] = trim
                    n_skipped_missing += 1
                    tracker.tick()
                    continue

                duration = float(trim)
                effect = _pick_ken_burns_effect(path)  # seed on original key
                try:
                    mp4_path = _render_ken_burns_clip(local_path, effect, duration)
                except Exception as exc:
                    print(
                        f"\n[ken-burns] ERROR rendering {local_path}: {exc} "
                        f"— keeping original entry"
                    )
                    new_item[path] = trim
                    n_failed += 1
                    tracker.tick()
                    continue

                new_item[mp4_path] = trim
                path_remap[path] = mp4_path
                n_rendered += 1
                tracker.tick()
            new_footage.append(new_item)
        entry["footage"] = new_footage

    tracker.finish()
    print(
        f"[ken-burns] DONE — rendered={n_rendered}, "
        f"skipped_missing={n_skipped_missing}, failed={n_failed}"
    )
    return final_data, path_remap
