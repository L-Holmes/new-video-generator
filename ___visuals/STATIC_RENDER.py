"""
Static-frame rendering helpers: extract a frame from a video at a timestamp,
bake a still image into a static MP4 (so the Ken Burns pass leaves it
untouched), and the manual-image stage (stock placement onto the previous
image, and hold-previous freezes) that produces those static MP4s after
review. Zooming and drawing/captions are decorate-editor tools now
(___visuals/decorator/, applied by DECORATE_STAGE) — not stages here.
"""

from __future__ import annotations

# Allow `uv run ___visuals/STATIC_RENDER.py` from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ___visuals.CACHE_IO import (
    _classify_footage_path,
    _resolve_to_local_path,
    add_local_paths_to_history,
    save_to_cache,
)
from CONFIG import (
    CANDIDATES_CACHE_FILE,
    DEBUG,
    FINAL_SCRIPT_AND_CLIPS,
    MANUAL_STOCK_PLACEMENT_OUTPUT_DIR,
    MANUAL_STOCK_PLACEMENT_RENDER_SAFETY_PAD_SEC,
    SearchTermData,
    media_props,
)
from ___visuals.KEN_BURNS import KEN_BURNS_FPS
from ___visuals.TIMING_MERGE import _load_scene_timings


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
    Single script-order pass over the HOLD_PREVIOUS scenes: reuse the
    PREVIOUS scene's image as-is, OR freeze the last *played* frame of the
    PREVIOUS scene's last video clip (timestamp from the JSON-derived
    trim). Non-interactive.

    EVERYTHING manual (stamping pictures in, zooming, drawing, object
    extraction) is the decorate modifier — the ONE editor, applied by
    DECORATE_STAGE at 2.645 on the scene's OWN footage. hold + decorate
    chains naturally: this stage freezes, the editor then edits.

    Runs after stage-1 review + the local generators and before Ken Burns
    (output is a static MP4 → KB skips it). No GUI, so scenes are just
    recomputed each run (cheap + deterministic).
    """
    ordered_texts = list(script_to_search_term.keys())
    manual_texts = [
        t
        for t in ordered_texts
        if media_props(script_to_search_term[t]["media_type"]).is_hold_previous
    ]

    print("\n" + "=" * 70)
    print(f"[manual-img] {len(manual_texts)} hold-previous scene(s) to process")
    print("=" * 70)
    if not manual_texts:
        print("[manual-img] none — skipping")
        return final_data

    from ___visuals.MANUAL_STOCK_PLACEMENT import extract_frame
    from ___visuals.VIDEO_CHAINS import (
        SourceExhausted,
        cut_continuing_segment,
        detect_video_chains,
    )

    scene_timings = _load_scene_timings()

    # LIVE chains (DECORATE_VIDEO_LIVE): a video scene + its hold run where
    # someone decorates. Those holds don't freeze — they CONTINUE the source
    # at a cumulative offset (2.3s in, then 3.5s in, ...), so the footage
    # keeps playing across the cuts. DECORATE_STAGE (2.645) then burns the
    # accumulated decoration layers onto these segments. Detection is the
    # same pure function DECORATE_STAGE calls, so the stages always agree.
    continuing: dict[str, tuple[str, float]] = {}   # text → (source, offset)
    for _chain in detect_video_chains(script_to_search_term, final_data,
                                      scene_timings):
        for _m in _chain.members:
            if not _m.is_anchor:
                continuing[_m.text] = (_chain.source, _m.offset)
    if continuing:
        print(f"[manual-img] {len(continuing)} hold scene(s) CONTINUE their "
              f"video (live decorated chain) instead of freezing")

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
        kind = "static"  # hold-previous is the only kind this stage handles

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

        # LIVE chain member: the previous video keeps PLAYING — cut its
        # continuing segment (normalised to the stitcher frame) instead of
        # freezing a still. If the source has already run out, fall back to
        # the normal freeze below.
        did_live = False
        if text in continuing:
            src, offset = continuing[text]
            output_mp4 = out_dir / f"manual_live_{idx:03d}_{safe_stem}.mp4"
            print(f"[manual-img]   LIVE: continuing {Path(src).name} "
                  f"@ {offset:.3f}s (+{duration:.3f}s) — no freeze")
            try:
                cut_continuing_segment(src, offset, duration, str(output_mp4))
                did_live = True
            except SourceExhausted as exc:
                print(f"[manual-img]   WARNING: {exc} — falling back to the "
                      f"freeze-frame behaviour")
                output_mp4 = out_dir / f"manual_{kind}_{idx:03d}_{safe_stem}.mp4"

        if did_live:
            pass  # segment written straight to output_mp4 — skip the freeze
        elif kind == "static":
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

        if did_live:
            print(f"[manual-img]   result = {Path(output_mp4).name} "
                  f"(continuing segment)")
        else:
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
