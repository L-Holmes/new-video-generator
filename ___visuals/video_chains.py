"""
VIDEO CHAINS — "the stock keeps playing" support for decorate on video.

The idea
--------
Normally a hold_previous scene FREEZES the previous image (static_render's
manual stage), and decorate bakes drawings into a still. When the previous
scene is a stock VIDEO and you stack `decorate` on the hold, that freeze is
usually not what you want: the footage should keep playing across the scene
cut, with the decorations layered ON TOP of the moving picture.

A CHAIN is a video scene (the ANCHOR) plus the maximal run of hold_previous
scenes that follows it, where at least one member (the anchor itself, or any
of the holds) carries the `decorate` modifier. For a chain:

  - every hold plays a CONTINUING SEGMENT of the anchor's source: scene k
    starts exactly where scene k-1 stopped (offsets are cumulative — 2.3s +
    1.2s + 1.0s reads as ONE 4.5s take with cuts the viewer never sees);
  - each member's decorations are rendered as TRANSPARENT LAYERS (see
    decorator/draw.py render_overlay_layer / render_highlight_mask) and
    burned over the moving footage with ffmpeg;
  - layers ACCUMULATE down the chain: what you drew at 2.3s is still there
    at 3.5s when the next batch appears.

Everything here is in FRAME coordinates: segments are normalised to the
stitcher's 1920x1080 contain-fit + black-pad (its _VF_BASE) up front, the
editor shows the user that exact padded frame, and the overlay layers are
rendered at that size — so what you place is pixel-for-pixel what's burned.

Detection is pure + deterministic (rows + anchor footage + timings only), so
static_render (which cuts the continuing segments at stage 2.64) and
decorate_stage (which opens the editor + burns the layers at 2.645) both
call detect_video_chains() and always agree.

Headless except for the two lazy imports from decorator/draw.py (the burn /
preview helpers need its highlight constants + renderer — imported inside
the functions so importing THIS module never pulls in Tk).
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ___visuals.cache_io import _classify_footage_path, _resolve_to_local_path
from CONFIG import (
    DECORATE_VIDEO_LIVE,
    VIDEO_CHAIN_SEGMENT_PAD_SEC,
    media_props,
    scene_wants_caption,
    scene_wants_decorate,
)

# The stitcher's output frame (stitch_together TARGET_*). Segments are
# normalised to this up front so overlay coordinates are final-frame exact.
FRAME_W: int = 1920
FRAME_H: int = 1080
FPS: int = 30

# scale=decrease + centre black pad — byte-for-byte the stitcher's _VF_BASE,
# plus the fps normalisation (same as add_relevant_overlays' video path).
_NORMALISE_VF = (
    f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease,"
    f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}"
)


class SourceExhausted(RuntimeError):
    """A continuing segment's offset is at/after the end of the source —
    there is no more video to continue with (callers fall back to freeze)."""


# ===========================================================================
# Chain model + detection
# ===========================================================================


@dataclass
class ChainMember:
    text: str  # the scene's script_text
    offset: float  # seconds into the SOURCE where this scene starts
    duration: float  # how long this scene plays (trim / timing)
    is_anchor: bool  # the video scene itself (offset 0)
    wants_decorate: bool
    wants_caption: bool


@dataclass
class VideoChain:
    source: str  # local path of the anchor's video
    members: list[ChainMember] = field(default_factory=list)

    @property
    def member_texts(self) -> set[str]:
        return {m.text for m in self.members}

    @property
    def hold_texts(self) -> set[str]:
        return {m.text for m in self.members if not m.is_anchor}


def detect_video_chains(
    script_to_search_term: dict,
    final_data: list[dict],
    scene_timings: dict[str, float],
) -> list[VideoChain]:
    """Find every continuing chain: a scene whose LAST footage clip resolves
    to a local VIDEO, followed by 0+ consecutive hold_previous scenes, where
    the anchor or any hold wants `decorate`. Offsets are cumulative from the
    end of the anchor's played window (its last clip's trim — the same
    timestamp the freeze path uses), so the chain reads as one take.

    The anchor is only a member itself (live-decorated at offset 0) when it
    wants decorate AND has a single footage clip; a multi-clip anchor's own
    decorate stays on the existing freeze path (which clip would the layers
    sit on?), but its holds still continue. Pure + deterministic — safe to
    call from more than one stage.
    """
    if not DECORATE_VIDEO_LIVE:
        return []

    ordered = list(script_to_search_term.keys())
    by_text = {e["script_text"]: e for e in final_data}
    chains: list[VideoChain] = []

    i = 0
    while i < len(ordered):
        text = ordered[i]
        row = script_to_search_term[text]
        if media_props(row.get("media_type")).is_hold_previous:
            i += 1  # a hold with no video anchor before it —
            continue  # the freeze path's problem, not ours

        # the maximal run of holds right after this scene, in script order
        j = i + 1
        holds: list[str] = []
        while (
            j < len(ordered)
            and media_props(
                script_to_search_term[ordered[j]].get("media_type")
            ).is_hold_previous
        ):
            holds.append(ordered[j])
            j += 1

        anchor_dec = scene_wants_decorate(row)
        if not (
            anchor_dec
            or any(scene_wants_decorate(script_to_search_term[h]) for h in holds)
        ):
            i = j
            continue  # nobody decorates → old behaviour throughout

        entry = by_text.get(text)
        footage = (entry or {}).get("footage") or []
        if not footage:
            i = j
            continue
        last_key, last_trim = next(iter(footage[-1].items()))
        local = _resolve_to_local_path(last_key)
        if not local or _classify_footage_path(local) != "video":
            i = j
            continue  # image anchor → existing still behaviour

        anchor_live = anchor_dec and len(footage) == 1
        if anchor_dec and not anchor_live:
            print(
                f"[chains] WARNING: '{text[:50]}' wants decorate but has "
                f"{len(footage)} clips — its own decorate stays the freeze "
                f"path; its holds still continue the last clip"
            )
        if not (anchor_live or holds):
            i = j
            continue

        members: list[ChainMember] = []
        if anchor_live:
            members.append(
                ChainMember(
                    text=text,
                    offset=0.0,
                    duration=float(last_trim),
                    is_anchor=True,
                    wants_decorate=True,
                    wants_caption=scene_wants_caption(row),
                )
            )
        off = float(last_trim)
        for h in holds:
            h_row = script_to_search_term[h]
            h_dur = float(scene_timings.get(h, 0.0))
            members.append(
                ChainMember(
                    text=h,
                    offset=off,
                    duration=h_dur,
                    is_anchor=False,
                    wants_decorate=scene_wants_decorate(h_row),
                    wants_caption=scene_wants_caption(h_row),
                )
            )
            off += h_dur

        chains.append(VideoChain(source=local, members=members))
        print(
            f"[chains] LIVE chain on {Path(local).name}: "
            + "  →  ".join(
                f"'{m.text[:28]}' @ {m.offset:.2f}s"
                + (" ✎" if m.wants_decorate else "")
                for m in members
            )
        )
        i = j

    return chains


# ===========================================================================
# ffmpeg: continuing segments + layer burns
# ===========================================================================


def _probe_duration(path: str) -> float:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(r.stdout.strip())
    except (TypeError, ValueError):
        return 0.0


def cut_continuing_segment(
    source: str, offset: float, duration: float, out_path: str
) -> str:
    """Cut [offset, offset + duration + pad] out of `source`, normalised to
    the stitcher frame (1920x1080@30, contain + black pad). If the source
    ends inside the window the LAST FRAME is frozen (tpad clone) so the
    segment always reaches full length; if the offset is already at/after
    the end, SourceExhausted is raised so the caller can fall back to the
    freeze path. -ss before -i + a re-encode = fast AND frame-accurate."""
    need = float(duration) + VIDEO_CHAIN_SEGMENT_PAD_SEC
    src_dur = _probe_duration(source)
    if src_dur and float(offset) >= src_dur - 0.05:
        raise SourceExhausted(
            f"offset {float(offset):.2f}s is at/after the end of "
            f"{Path(source).name} ({src_dur:.2f}s)"
        )
    if src_dur and float(offset) + need > src_dur:
        print(
            f"[chains]   note: {Path(source).name} ends at {src_dur:.2f}s "
            f"— the tail past that freezes on the last frame"
        )

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, float(offset)):.3f}",
        "-i",
        str(source),
        "-t",
        f"{need:.3f}",
        "-vf",
        f"{_NORMALISE_VF},tpad=stop_mode=clone:stop_duration={need + 1.0:.3f}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-an",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = Path(out_path)
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise RuntimeError(
            f"continuing-segment cut failed for {source} @ "
            f"{float(offset):.2f}s: {r.stderr[-600:]}"
        )
    return str(out_path)


@dataclass
class DecoLayer:
    """ONE scene's decorations as burn-ready assets in FRAME coordinates.
    highlights keeps the live HighlightDeco objects so the editor preview
    can reproduce them with the exact PIL code; highlight_mask is the same
    geometry exported for ffmpeg."""

    highlights: list = field(default_factory=list)  # [HighlightDeco, ...]
    highlight_mask: str | None = None  # feathered gray PNG
    sprites_png: str | None = None  # transparent RGBA layer
    caption_png: str | None = None  # transparent caption

    def overlay_pngs(self) -> list[str]:
        return [p for p in (self.sprites_png, self.caption_png) if p]

    @property
    def empty(self) -> bool:
        return not (self.highlight_mask or self.sprites_png or self.caption_png)


def _zoom_rect_px(
    box: tuple, bw: int = FRAME_W, bh: int = FRAME_H
) -> tuple[int, int, int, int]:
    """(w, h, x, y) of a zoom op's crop on a bw×bh frame — arithmetic kept
    IDENTICAL to manual_stock_placement.crop_and_zoom (aspect locked to the
    frame unless an independent height % is given), centre clamped inside, so
    the editor's still preview and the video crop land on the same pixels."""
    if len(box) == 4:
        wpct, cx_frac, cy_frac, hpct = box
    else:
        wpct, cx_frac, cy_frac = box
        hpct = None
    w = max(1, min(bw, round(wpct / 100.0 * bw)))
    if hpct is not None:
        h = max(1, min(bh, round(hpct / 100.0 * bh)))
    else:
        h = max(1, min(bh, round(w * bh / bw)))
    x = max(0, min(round(cx_frac * bw - w / 2), bw - w))
    y = max(0, min(round(cy_frac * bh - h / 2), bh - h))
    return w, h, x, y


def burn_ops_onto_segment(segment: str, ops: list, out_path: str) -> str:
    """Apply an ordered ops recipe over a (normalised) video segment,
    IN ORDER — ops are ("layer", DecoLayer) or ("zoom", (wpct, cx, cy)):

      layer — its highlight pass first (brighten inside the boxes / darken
              outside, multiplicatively — the exact ffmpeg port of
              draw._apply_highlights via colorchannelmixer + maskedmerge
              with the feathered mask), then its sprite PNG, then its
              caption PNG;
      zoom  — a REAL crop of the moving footage (crop + scale back to the
              frame), so everything burned before it zooms with the picture
              and everything after sits on the zoomed view — exactly the
              editor's semantics.

    A later layer's spotlight therefore dims everything already on screen,
    same as the editor preview. The whole chain runs in gbrp (no chroma
    subsampling), so odd crop offsets are fine. Single-frame PNG inputs
    repeat automatically (framesync eof_action=repeat), so no -loop
    juggling."""
    from ___visuals.decorator.draw import (  # lazy: draw.py imports Tk
        HIGHLIGHT_BRIGHTEN,
        HIGHLIGHT_DARKEN,
    )

    inputs: list[str] = ["-i", str(segment)]
    fc: list[str] = ["[0:v]format=gbrp[v0]"]  # colorchannelmixer needs RGB
    cur, in_idx, n = "v0", 1, 0

    for kind, payload in ops:
        if kind == "zoom":
            w, h, x, y = _zoom_rect_px(payload)
            o = f"zm{n}"
            # crop then scale back to the frame, preserving the crop's aspect
            # ratio and padding the rest white (letterbox) so a non-aspect-
            # locked crop is NEVER stretched.
            fc.append(
                f"[{cur}]crop={w}:{h}:{x}:{y},"
                f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease,"
                f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2:color=white,"
                f"setsar=1[{o}]"
            )
            cur, n = o, n + 1
            continue
        layer = payload
        if layer.highlight_mask:
            inputs += ["-i", layer.highlight_mask]
            b, d, m, o = f"br{n}", f"dk{n}", f"mk{n}", f"hl{n}"
            fc.append(f"[{cur}]split[{b}s][{d}s]")
            fc.append(
                f"[{b}s]colorchannelmixer="
                f"rr={HIGHLIGHT_BRIGHTEN}:gg={HIGHLIGHT_BRIGHTEN}:"
                f"bb={HIGHLIGHT_BRIGHTEN}[{b}]"
            )
            fc.append(
                f"[{d}s]colorchannelmixer="
                f"rr={HIGHLIGHT_DARKEN}:gg={HIGHLIGHT_DARKEN}:"
                f"bb={HIGHLIGHT_DARKEN}[{d}]"
            )
            fc.append(f"[{in_idx}:v]format=gbrp,scale={FRAME_W}:{FRAME_H}[{m}]")
            fc.append(f"[{d}][{b}][{m}]maskedmerge[{o}]")  # white = bright
            cur, in_idx, n = o, in_idx + 1, n + 1
        for png in layer.overlay_pngs():
            inputs += ["-i", png]
            o = f"ov{n}"
            fc.append(f"[{cur}][{in_idx}:v]overlay=0:0:format=auto[{o}]")
            cur, in_idx, n = o, in_idx + 1, n + 1

    fc.append(f"[{cur}]format=yuv420p[vout]")
    cmd = (
        ["ffmpeg", "-y", "-nostdin", "-loglevel", "error"]
        + inputs
        + [
            "-filter_complex",
            ";".join(fc),
            "-map",
            "[vout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-an",
            str(out_path),
        ]
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = Path(out_path)
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"ops burn failed for {segment}: {r.stderr[-600:]}")
    return str(out_path)


# ===========================================================================
# Editor-preview helpers (what the user sees IS what gets burned)
# ===========================================================================


def extract_frame_normalised(source: str, at_seconds: float, out_png: str) -> str:
    """One frame of `source` at `at_seconds`, contain-fitted + black-padded
    to the 1920x1080 stitcher frame — the exact picture on screen at that
    instant, and the canvas every overlay coordinate is relative to. Seeks
    that land past the end fall back to the last frame (matching the
    tpad-clone freeze the segment render does)."""
    from ___visuals.make_text_overlay import _fit_pad  # headless (PIL only)

    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    grab = str(Path(out_png).with_suffix(".grab.png"))
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, float(at_seconds)):.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        grab,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not Path(grab).exists() or Path(grab).stat().st_size == 0:
        r = subprocess.run(  # past EOF / odd clip → the very last frame
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-loglevel",
                "error",
                "-sseof",
                "-0.1",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                grab,
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 or not Path(grab).exists():
            raise RuntimeError(
                f"frame grab failed for {source} @ {float(at_seconds):.2f}s"
            )
    try:
        img = Image.open(grab).convert("RGB")
        _fit_pad(img, FRAME_W, FRAME_H).save(out_png)
    finally:
        Path(grab).unlink(missing_ok=True)
    return str(out_png)


def composite_ops_for_preview(frame_png: str, ops: list, out_png: str) -> str:
    """Apply an ops recipe to a frame still, in order (highlights with the
    editor's own PIL code, PNG layers composited, zooms as crop + resize
    with crop_and_zoom's exact arithmetic) — so when the user opens the
    NEXT chain scene they decorate the exact picture the viewer will be
    seeing at that moment."""
    try:
        _RES = Image.Resampling.LANCZOS
    except AttributeError:
        _RES = Image.LANCZOS
    img = Image.open(frame_png).convert("RGB")
    for kind, payload in ops:
        if kind == "zoom":
            w, h, x, y = _zoom_rect_px(payload, *img.size)
            crop = img.crop((x, y, x + w, y + h))
            bw, bh = img.size
            # preserve the crop's aspect ratio; letterbox the rest white
            # (matches crop_and_zoom / the ffmpeg burn, so no stretching)
            scale = min(bw / w, bh / h)
            nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
            scaled = crop.resize((nw, nh), _RES)
            if (nw, nh) == (bw, bh):
                img = scaled
            else:
                canvas = Image.new("RGB", (bw, bh), (255, 255, 255))
                canvas.paste(scaled, ((bw - nw) // 2, (bh - nh) // 2))
                img = canvas
            continue
        layer = payload
        if layer.highlights:
            from ___visuals.decorator.draw import _apply_highlights  # lazy (Tk)

            img = _apply_highlights(img, layer.highlights)
        for png in layer.overlay_pngs():
            base = img.convert("RGBA")
            base.alpha_composite(Image.open(png).convert("RGBA"))
            img = base.convert("RGB")
    img.save(out_png)
    return str(out_png)
