"""
Static-frame rendering helpers: extract a frame from a video at a timestamp,
bake a still image into a static MP4 (so the Ken Burns pass leaves it
untouched), and the manual-image stage (manual stock placement / zoom-prev /
freeze-prev) that produces those static MP4s after review.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from CACHE_IO import (
    _classify_footage_path,
    _resolve_to_local_path,
    add_local_paths_to_history,
    save_to_cache,
)
from CONFIG import (
    CANDIDATES_CACHE_FILE,
    DEBUG,
    FINAL_SCRIPT_AND_CLIPS,
    MANUAL_STOCK_ADD_TYPES,
    MANUAL_STOCK_PLACEMENT_OUTPUT_DIR,
    MANUAL_STOCK_PLACEMENT_RENDER_SAFETY_PAD_SEC,
    STATIC_OF_PREVIOUS_TYPES,
    ZOOM_PREV_TYPES,
    SearchTermData,
)
from KEN_BURNS import KEN_BURNS_FPS
from TIMING_MERGE import _load_scene_timings


# === BEGIN verbatim move from main.py (static render + manual stage) ===
def _extract_frame_at_timestamp(
    video_path: str, timestamp_sec: float, output_png: str
) -> str:
    """Grab a single frame from `video_path` at `timestamp_sec` (the moment the
    clip stops being shown) and write it to `output_png`. Used by
    static_of_previous to freeze the last *played* frame of the previous
    scene's video. Seeks just inside the cut so we never run past the clip."""
    import shlex

    vp = Path(video_path)
    if not vp.exists():
        raise RuntimeError(f"video does not exist: {video_path}")

    # Land just inside the played window. -ss AFTER -i = accurate (decoded) seek.
    ts = max(0.0, float(timestamp_sec) - 0.05)
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "warning",
        "-i",
        video_path,
        "-ss",
        f"{ts:.3f}",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        output_png,
    ]
    if DEBUG:
        print(f"[static-prev:ffmpeg]   {shlex.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stderr.strip():
        print(f"[static-prev:ffmpeg]   stderr:\n{result.stderr.rstrip()}")

    out = Path(output_png)
    if result.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        # Fallback: some short/odd clips fail an interior seek — grab the very
        # last frame from end-of-file instead.
        cmd_eof = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-loglevel",
            "warning",
            "-sseof",
            "-0.1",
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            output_png,
        ]
        if DEBUG:
            print(
                f"[static-prev:ffmpeg]   interior seek failed — trying EOF: "
                f"{shlex.join(cmd_eof)}"
            )
        result = subprocess.run(cmd_eof, capture_output=True, text=True)
        if result.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(
                f"frame extraction failed for {video_path} @ {ts:.3f}s "
                f"(returncode={result.returncode})"
            )
    return output_png


def _render_image_to_static_mp4(
    image_path: str, duration: float, output_path: str
) -> str:
    """Bake a still into a silent, perfectly static H.264 MP4 of `duration`s
    (+ a tiny safety pad the stitcher trims). Output is forced to EVEN
    dimensions (libx264/yuv420p requires it — odd dims are what made the
    encoder fail) and verbosely logged so any failure is diagnosable."""
    import shlex

    from PIL import Image as _PILImage

    img_path = Path(image_path)
    if not img_path.exists():
        raise RuntimeError(f"input image does not exist: {image_path}")
    img_bytes = img_path.stat().st_size
    try:
        with _PILImage.open(image_path) as _im:
            iw, ih = _im.size
            imode = _im.mode
    except Exception as exc:
        raise RuntimeError(f"could not open input image {image_path}: {exc}")

    render_duration = duration + MANUAL_STOCK_PLACEMENT_RENDER_SAFETY_PAD_SEC
    even_w, even_h = (iw // 2) * 2, (ih // 2) * 2
    is_even = iw % 2 == 0 and ih % 2 == 0

    print(f"[manual-place:ffmpeg] input  = {image_path}")
    print(
        f"[manual-place:ffmpeg]   exists={img_path.exists()} size={img_bytes}B "
        f"dims={iw}x{ih} mode={imode} "
        f"({'even' if is_even else 'ODD -> scaling to even'})"
    )
    print(
        f"[manual-place:ffmpeg]   duration={duration:.3f}s "
        f"pad={MANUAL_STOCK_PLACEMENT_RENDER_SAFETY_PAD_SEC:.3f}s "
        f"render={render_duration:.3f}s fps={KEN_BURNS_FPS}"
    )
    print(f"[manual-place:ffmpeg]   target dims (even) = {even_w}x{even_h}")

    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "warning",
        "-loop",
        "1",
        "-framerate",
        str(KEN_BURNS_FPS),
        "-i",
        image_path,
        "-t",
        f"{render_duration:.3f}",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # <- the fix: even dims
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-r",
        str(KEN_BURNS_FPS),
        "-an",
        output_path,
    ]
    print(f"[manual-place:ffmpeg]   cmd: {shlex.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stderr.strip():
        print(f"[manual-place:ffmpeg]   stderr:\n{result.stderr.rstrip()}")
    print(f"[manual-place:ffmpeg]   returncode={result.returncode}")

    out_path = Path(output_path)
    out_size = out_path.stat().st_size if out_path.exists() else 0
    print(
        f"[manual-place:ffmpeg]   output = {output_path} "
        f"exists={out_path.exists()} size={out_size}B"
    )

    if result.returncode != 0 or out_size == 0:
        raise RuntimeError(
            f"static MP4 render failed for {image_path} "
            f"(returncode={result.returncode}, output_size={out_size}B). "
            f"See ffmpeg stderr above."
        )
    return output_path


def run_manual_image_stage(
    script_to_search_term: dict[str, "SearchTermData"],
    final_data: list[dict],
) -> list[dict]:
    """
    Single script-order pass over all "derive-from-previous" image scenes:
      • MANUAL_STOCK_ADD_TO_PREVIOUS — composite this scene's chosen still onto
        the PREVIOUS scene's image at a clicked position/size.
      • ZOOM_PREV_IMG — crop/zoom into the PREVIOUS scene's image.
      • STATIC_OF_PREVIOUS — reuse the PREVIOUS scene's image as-is, OR freeze
        the last *played* frame of the PREVIOUS scene's last video clip
        (timestamp from the JSON-derived trim). Non-interactive.

    All processed together, ONE AT A TIME, merging each result back into
    final_data before the next, so any mix chains correctly (zoom into a
    composite, place onto a zoom, freeze a video then zoom the frozen frame,
    ...). "Previous" = the nearest preceding scene that resolves to usable
    footage (videos → a frame). Runs after stage-1 review + the
    explainer/text-overlay generators and before Ken Burns (output is a static
    MP4 → KB skips it).

    Resume: placement_<idx>.json / crop_<idx>.json are reused without
    re-opening the GUI; delete one to redo that scene. STATIC_OF_PREVIOUS has
    no GUI, so it's just recomputed each run (cheap + deterministic).
    """
    manual_set = MANUAL_STOCK_ADD_TYPES | ZOOM_PREV_TYPES | STATIC_OF_PREVIOUS_TYPES
    ordered_texts = list(script_to_search_term.keys())
    manual_texts = [
        t
        for t in ordered_texts
        if script_to_search_term[t]["search_type"] in manual_set
    ]

    print("\n" + "=" * 70)
    print(f"[manual-img] {len(manual_texts)} derive-from-previous scene(s) to process")
    print("=" * 70)
    if not manual_texts:
        print("[manual-img] none — skipping")
        return final_data

    from MANUAL_STOCK_PLACEMENT import (
        CropBox,
        Placement,
        composite_overlays,
        crop_and_zoom,
        extract_frame,
        place_overlays_interactive,
        zoom_prev_interactive,
    )

    scene_timings = _load_scene_timings()
    by_script = {e["script_text"]: i for i, e in enumerate(final_data)}
    script_index = {txt: i for i, txt in enumerate(script_to_search_term)}

    out_dir = MANUAL_STOCK_PLACEMENT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    def _dims(p: str) -> str:
        try:
            from PIL import Image as _I

            with _I.open(p) as im:
                return f"{im.size[0]}x{im.size[1]}"
        except Exception:
            return "?x?"

    def _resolve_scene_still(text: str) -> str | None:
        entry = next((e for e in final_data if e["script_text"] == text), None)
        footage = (entry or {}).get("footage") or []
        key = next(iter(footage[0]), None) if footage else None
        if not key:
            return None
        local = _resolve_to_local_path(key)
        if not local:
            return None
        if _classify_footage_path(local) == "video":
            frame_png = (
                out_dir / f"frame_{hashlib.md5(local.encode()).hexdigest()[:12]}.png"
            )
            if not (frame_png.exists() and frame_png.stat().st_size > 1024):
                try:
                    extract_frame(local, str(frame_png))
                except Exception as exc:
                    print(
                        f"[manual-img] WARNING: frame extract failed for "
                        f"{Path(local).name}: {exc}"
                    )
                    return None
            return str(frame_png) if frame_png.exists() else None
        return local

    def _resolve_base(idx: int) -> tuple[str | None, str | None]:
        for j in range(idx - 1, -1, -1):
            still = _resolve_scene_still(ordered_texts[j])
            if still:
                return still, ordered_texts[j]
        return None, None

    def _resolve_static_source(idx: int) -> tuple[str | None, str | None]:
        """For STATIC_OF_PREVIOUS: walk back to the nearest scene with footage,
        then reuse its image as-is, or freeze the last *played* frame of its
        last video clip (timestamp = that clip's trim, from the JSON timings)."""
        for j in range(idx - 1, -1, -1):
            prev_text = ordered_texts[j]
            entry = next((e for e in final_data if e["script_text"] == prev_text), None)
            footage = (entry or {}).get("footage") or []
            if not footage:
                continue
            last_path, last_trim = next(
                iter(footage[-1].items())
            )  # LAST clip of prev scene
            local = _resolve_to_local_path(last_path)
            if not local:
                continue

            if _classify_footage_path(local) == "video":
                key = f"{local}|{round(float(last_trim), 3)}"
                freeze_png = (
                    out_dir
                    / f"static_src_{hashlib.md5(key.encode()).hexdigest()[:12]}.png"
                )
                if not (freeze_png.exists() and freeze_png.stat().st_size > 1024):
                    try:
                        _extract_frame_at_timestamp(
                            local, float(last_trim), str(freeze_png)
                        )
                    except Exception as exc:
                        print(
                            f"[manual-img] WARNING: freeze-frame failed for "
                            f"{Path(local).name}: {exc}"
                        )
                        continue
                print(
                    f"[manual-img]   static source ← '{prev_text[:45]}' "
                    f"(VIDEO {Path(local).name}, freeze @ {float(last_trim):.2f}s)"
                )
                return str(freeze_png), prev_text

            print(
                f"[manual-img]   static source ← '{prev_text[:45]}' "
                f"(IMAGE {Path(local).name}, reused as-is)"
            )
            return local, prev_text
        return None, None

    for n, text in enumerate(manual_texts, start=1):
        idx = script_index[text]
        stype = script_to_search_term[text]["search_type"]
        kind = (
            "static"
            if stype in STATIC_OF_PREVIOUS_TYPES
            else "zoom"
            if stype in ZOOM_PREV_TYPES
            else "place"
        )

        print("\n" + "-" * 70)
        print(
            f"[manual-img] ({n}/{len(manual_texts)}) [{kind}] scene #{idx}: '{text[:55]}'"
        )
        print("-" * 70)

        if text not in scene_timings:
            print(f"[manual-img] FATAL: no timing for '{text[:60]}'")
            sys.exit(1)
        duration = float(scene_timings[text])
        if duration <= 0:
            print(f"[manual-img] WARNING: zero duration — skipping '{text[:55]}'")
            continue

        safe_stem = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:50] or "scene"
        result_png = out_dir / f"manual_{kind}_{idx:03d}_{safe_stem}.png"
        output_mp4 = out_dir / f"manual_{kind}_{idx:03d}_{safe_stem}.mp4"
        print(f"[manual-img]   duration = {duration:.3f}s")

        if kind == "static":
            # Non-interactive: reuse prev image, or freeze prev video's last frame.
            src_still, _src_text = _resolve_static_source(idx)
            if not src_still:
                print(
                    f"[manual-img] FATAL: no preceding media to derive a still "
                    f"from for '{text[:60]}'. static_of_previous needs a normal "
                    f"image/video scene before it."
                )
                sys.exit(1)
            try:
                shutil.copyfile(src_still, str(result_png))
            except Exception as exc:
                print(
                    f"[manual-img] FATAL: couldn't stage still from "
                    f"{Path(src_still).name}: {exc}"
                )
                sys.exit(1)

        else:
            # place & zoom both use the PREVIOUS scene's image as the backdrop.
            base_still, base_text = _resolve_base(idx)
            if not base_still:
                print(
                    f"[manual-img] FATAL: no preceding image for '{text[:60]}'. "
                    f"This type needs a normal scene before it whose image is "
                    f"the backdrop."
                )
                sys.exit(1)
            print(
                f"[manual-img]   base <- '{base_text[:50]}' "
                f"({Path(base_still).name}, {_dims(base_still)})"
            )

            if kind == "place":
                overlay_still = _resolve_scene_still(text)
                if not overlay_still:
                    print(
                        f"[manual-img] FATAL: no chosen stock for '{text[:60]}'. Its "
                        f"overlay is picked in stage-1 review — did you select one? "
                        f"(Delete {CANDIDATES_CACHE_FILE} and re-run if stale.)"
                    )
                    sys.exit(1)
                print(
                    f"[manual-img]   overlay = {Path(overlay_still).name} "
                    f"({_dims(overlay_still)})"
                )

                state_file = out_dir / f"placement_{idx:03d}.json"
                placements = None
                if state_file.exists():
                    try:
                        d = json.loads(state_file.read_text())
                        remove_bg = bool(d.get("remove_bg", True))
                        # new format: {"remove_bg":.., "placements":[{...}, ...]}
                        # old format (single stamp): {"width_pct":.., "cx_frac":.., ...}
                        raw = d["placements"] if "placements" in d else [d]
                        placements = [
                            Placement(
                                int(r["width_pct"]),
                                float(r["cx_frac"]),
                                float(r["cy_frac"]),
                                remove_bg,
                            )
                            for r in raw
                        ]
                        print(
                            f"[manual-img]   resume: reusing {len(placements)} saved "
                            f"placement(s)"
                        )
                    except Exception as exc:
                        print(
                            f"[manual-img]   couldn't read {state_file.name} ({exc}); "
                            f"re-opening GUI"
                        )
                        placements = None
                if placements is None:
                    placements = place_overlays_interactive(
                        base_image_path=base_still,
                        overlay_image_path=overlay_still,
                        window_title=(
                            f"Place '{script_to_search_term[text]['search_term']}' "
                            f"(scene {n}/{len(manual_texts)})"
                        ),
                    )
                    if not placements:
                        print(
                            f"\n[manual-img] Exited without placing scene #{idx}. "
                            f"Re-run to resume."
                        )
                        sys.exit(0)
                    state_file.write_text(
                        json.dumps(
                            {
                                "remove_bg": placements[0].remove_bg,
                                "placements": [
                                    {
                                        "width_pct": p.width_pct,
                                        "cx_frac": p.cx_frac,
                                        "cy_frac": p.cy_frac,
                                    }
                                    for p in placements
                                ],
                            },
                            indent=2,
                        )
                    )
                print(f"[manual-img]   stamps = {len(placements)}")
                try:
                    composite_overlays(
                        base_still, overlay_still, placements, str(result_png)
                    )
                except Exception as exc:
                    print(f"[manual-img] FATAL: composite failed: {exc}")
                    sys.exit(1)

            else:  # zoom
                state_file = out_dir / f"crop_{idx:03d}.json"
                crop = None
                if state_file.exists():
                    try:
                        d = json.loads(state_file.read_text())
                        crop = CropBox(
                            int(d["width_pct"]),
                            float(d["cx_frac"]),
                            float(d["cy_frac"]),
                        )
                        print(f"[manual-img]   resume: reusing saved crop box")
                    except Exception as exc:
                        print(
                            f"[manual-img]   couldn't read {state_file.name} ({exc}); re-opening GUI"
                        )
                        crop = None
                if crop is None:
                    crop = zoom_prev_interactive(
                        base_image_path=base_still,
                        window_title=f"Zoom into previous image (scene {n}/{len(manual_texts)})",
                    )
                    if crop is None:
                        print(
                            f"\n[manual-img] Exited without zooming scene #{idx}. Re-run to resume."
                        )
                        sys.exit(0)
                    state_file.write_text(
                        json.dumps(
                            {
                                "width_pct": crop.width_pct,
                                "cx_frac": crop.cx_frac,
                                "cy_frac": crop.cy_frac,
                            },
                            indent=2,
                        )
                    )
                try:
                    crop_and_zoom(base_still, crop, str(result_png))
                except Exception as exc:
                    print(f"[manual-img] FATAL: crop/zoom failed: {exc}")
                    sys.exit(1)

        print(
            f"[manual-img]   result = {Path(result_png).name} ({_dims(str(result_png))})"
        )
        try:
            _render_image_to_static_mp4(str(result_png), duration, str(output_mp4))
        except Exception as exc:
            print(f"[manual-img] FATAL: MP4 render failed: {exc}")
            sys.exit(1)

        entries = [{str(output_mp4): round(duration, 3)}]
        if text in by_script:
            final_data[by_script[text]]["footage"] = entries
        else:
            final_data.append({"script_text": text, "footage": entries})
            by_script[text] = len(final_data) - 1
        add_local_paths_to_history({text: entries})
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"[manual-img]   OK {Path(output_mp4).name} (trim {round(duration, 3)}s)")

    print("\n" + "=" * 70)
    print(f"[manual-img] DONE — processed {len(manual_texts)} scene(s)")
    print("=" * 70)
    return final_data
