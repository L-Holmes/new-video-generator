"""
VIDEO BACKGROUND STAGE — stage 2.655: the whole edit rides on YOUR footage.

One long background video (VIDEO_BACKGROUND_FILE, e.g. 30 minutes of you
walking) plays CONTINUOUSLY under the entire edit, starting at
VIDEO_BACKGROUND_START and trimmed to the narration's length (FATAL if there
isn't enough video left past the start). Scene windows come from the same
absolute timestamps the stitcher plans its frame budgets with, so the
background stays in sync with the cuts; each scene gets its slice via
video_chains.cut_continuing_segment (normalised 1920x1080@30, tail-frozen
if the file runs out inside a pad).

Per scene, on top of the moving background:

  CARD    — a scene that ends with opaque/photographic footage (stock
            video/images, wikipedia stills, joint boards, decorate/KB
            outputs, ...) is contain-fitted into VIDEO_BG_CARD_SCALE of the
            frame and composited as a card: white polaroid border + soft
            drop shadow (the collage look). A video card shorter than its
            window freezes on its last frame while the background walks on.

  KEYED   — a clip with a TRANSPARENT background (alpha) or a ~WHITE one
            (stickman renders, maps, cut-outs) is auto-detected per CLIP
            (border-ring sampling, VIDEO_BG_KEY_*) and keyed so the graphic
            floats directly over your footage: stills through the proven
            border-connected remove_white_background (white INSIDE the
            subject survives) or their own alpha; video clips through
            ffmpeg colorkey.

  BLANK   — a `background` scene, or any line that ends with no footage,
            shows the bare background segment. That's how "only when
            crucial" is expressed: leave the line as background and nothing
            pops on. `background` + decorate opens the LIVE overlay editor
            ON the exact frame where the line starts (draw straight onto
            your own footage — the decorate-chain machinery); + caption
            burns the tilted caption layer over it.

Runs AFTER colour grade + Ken Burns (cards carry their grade/motion) and
BEFORE the auto-overlay badges (badges land on the full composited frame).
Editing is unchanged everywhere else: scenes are reviewed/decorated exactly
as today, blind to the background. Footage stays {path: trim} — the
stitcher needs no changes. The background itself is NOT colour graded.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from ___visuals.cache_io import (
    _classify_footage_path,
    _resolve_to_local_path,
    add_local_paths_to_history,
)
from CONFIG import (
    SCRIPT_AUDIO_FILE,
    TIMESTAMPS_ABSOLUTE_FILE,
    VIDEO_BACKGROUND_FILE,
    VIDEO_BACKGROUND_MODE,
    VIDEO_BACKGROUND_START,
    VIDEO_BG_CARD_BORDER_PX,
    VIDEO_BG_CARD_SCALE,
    VIDEO_BG_CARD_SHADOW,
    VIDEO_BG_KEY_AUTO,
    VIDEO_BG_KEY_BLEND,
    VIDEO_BG_KEY_BORDER_FRAC,
    VIDEO_BG_KEY_SIMILARITY,
    VIDEO_BG_KEY_WHITE_MIN,
    VIDEO_BG_OUTPUT_DIR,
    MediaType,
    SearchTermData,
    scene_type,
    scene_wants_caption,
    scene_wants_decorate,
)
from ___visuals.video_chains import (
    FRAME_H,
    FRAME_W,
    SourceExhausted,
    _probe_duration,
    cut_continuing_segment,
)

# Card shadow — the auto_collage look, so cards and collages match.
_SHADOW_OFFSET = (10, 14)
_SHADOW_BLUR = 18
_SHADOW_ALPHA = 110


def _safe_stem(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:50] or "scene"


def _parse_start(value) -> float:
    """VIDEO_BACKGROUND_START as seconds: a number, or 'mm:ss' / 'hh:mm:ss'."""
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    secs = 0.0
    for part in str(value).strip().split(":"):
        secs = secs * 60.0 + float(part or 0)
    return max(0.0, secs)


# ===========================================================================
# Per-clip inspection: dimensions + overlay treatment
# ===========================================================================

def _is_image(local: str) -> bool:
    return _classify_footage_path(local) == "image"


def _grab_raw_frame(video: str, out_png: str) -> str:
    """First frame of a video, un-normalised (for detection)."""
    r = subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-loglevel", "error",
         "-i", str(video), "-frames:v", "1", "-q:v", "2", out_png],
        capture_output=True, text=True)
    if r.returncode != 0 or not Path(out_png).exists():
        raise RuntimeError(f"frame grab failed for {video}: {r.stderr[-300:]}")
    return out_png


def _dims(local: str) -> tuple[int, int]:
    if _is_image(local):
        with Image.open(local) as im:
            return im.size
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=s=x:p=0", str(local)],
        capture_output=True, text=True)
    try:
        w, h = r.stdout.strip().split("x")
        return int(w), int(h)
    except Exception:
        return FRAME_W, FRAME_H


def _border_white_frac(rgb: Image.Image) -> float:
    """Share of the border ring that is ~white (all channels ≥ the knob)."""
    small = rgb.convert("RGB").copy()
    small.thumbnail((200, 200))
    px = small.load()
    w, h = small.size
    ring = ([(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
            + [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)])
    white = sum(1 for x, y in ring
                if all(c >= VIDEO_BG_KEY_WHITE_MIN for c in px[x, y][:3]))
    return white / max(1, len(ring))


def _detect_treatment(local: str, work_dir: Path, stem: str) -> str:
    """'card' | 'key_alpha' | 'key_white' — decided per CLIP, not per type:
    real transparency → key via alpha; a ~white border ring → key the white
    out; anything else (photographic) → the 80% card."""
    if not VIDEO_BG_KEY_AUTO:
        return "card"
    if _is_image(local):
        with Image.open(local) as im:
            if "A" in im.getbands():
                a_lo, _ = im.convert("RGBA").getchannel("A").getextrema()
                if a_lo < 250:
                    return "key_alpha"
            frame = im.convert("RGB")
            frac = _border_white_frac(frame)
    else:
        probe = str(work_dir / f"{stem}_probe.png")
        try:
            _grab_raw_frame(local, probe)
            with Image.open(probe) as im:
                frac = _border_white_frac(im)
        finally:
            Path(probe).unlink(missing_ok=True)
    return "key_white" if frac >= VIDEO_BG_KEY_BORDER_FRAC else "card"


# ===========================================================================
# Overlay asset builders
# ===========================================================================

def _fit_box(iw: int, ih: int, bw: int, bh: int) -> tuple[int, int]:
    """Contain-fit (even dims) of an iw×ih clip into a bw×bh box."""
    s = min(bw / max(1, iw), bh / max(1, ih))
    return (max(2, int(iw * s) // 2 * 2), max(2, int(ih * s) // 2 * 2))


def _card_geometry(local: str) -> tuple[int, int, int, int]:
    """(content_w, content_h, x, y): the clip contain-fitted into the card
    box (VIDEO_BG_CARD_SCALE of the frame, minus the border), centred."""
    b = max(0, int(VIDEO_BG_CARD_BORDER_PX))
    box_w = int(FRAME_W * VIDEO_BG_CARD_SCALE) // 2 * 2
    box_h = int(FRAME_H * VIDEO_BG_CARD_SCALE) // 2 * 2
    iw, ih = _dims(local)
    cw, ch = _fit_box(iw, ih, box_w - 2 * b, box_h - 2 * b)
    return cw, ch, (FRAME_W - cw) // 2, (FRAME_H - ch) // 2


def _card_backing_png(content_w: int, content_h: int, out_png: str) -> str:
    """Frame-sized transparent PNG with the card's white border rectangle +
    soft drop shadow (auto_collage's offsets/blur/alpha) centred under where
    the content will sit — one static overlay input under the moving clip."""
    b = max(0, int(VIDEO_BG_CARD_BORDER_PX))
    rw, rh = content_w + 2 * b, content_h + 2 * b
    x, y = (FRAME_W - rw) // 2, (FRAME_H - rh) // 2
    layer = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
    if VIDEO_BG_CARD_SHADOW:
        sh = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rectangle(
            [x + _SHADOW_OFFSET[0], y + _SHADOW_OFFSET[1],
             x + rw + _SHADOW_OFFSET[0], y + rh + _SHADOW_OFFSET[1]],
            fill=(0, 0, 0, _SHADOW_ALPHA))
        layer = Image.alpha_composite(
            layer, sh.filter(ImageFilter.GaussianBlur(_SHADOW_BLUR)))
    if b > 0:
        ImageDraw.Draw(layer).rectangle(
            [x, y, x + rw - 1, y + rh - 1], fill=(255, 255, 255, 255))
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    layer.save(out_png)
    return out_png


def _keyed_still_layer(local: str, treatment: str, out_png: str) -> str:
    """A keyed STILL as one frame-sized transparent PNG: white removed with
    the proven border-connected flood (white inside the subject survives) or
    its own alpha kept, contain-fitted into the card box, centred."""
    img = Image.open(local).convert("RGBA")
    if treatment == "key_white":
        try:
            from ___visuals.manual_stock_placement import remove_white_background
            img = remove_white_background(img)
        except Exception as exc:   # headless / import trouble → plain threshold
            print(f"[video-bg]   note: remove_white_background unavailable "
                  f"({exc}) — falling back to a global near-white key")
            px = img.load()
            for yy in range(img.height):
                for xx in range(img.width):
                    r, g, b2, a = px[xx, yy]
                    if r >= VIDEO_BG_KEY_WHITE_MIN and \
                            g >= VIDEO_BG_KEY_WHITE_MIN and \
                            b2 >= VIDEO_BG_KEY_WHITE_MIN:
                        px[xx, yy] = (r, g, b2, 0)
    box_w = int(FRAME_W * VIDEO_BG_CARD_SCALE) // 2 * 2
    box_h = int(FRAME_H * VIDEO_BG_CARD_SCALE) // 2 * 2
    cw, ch = _fit_box(img.width, img.height, box_w, box_h)
    img = img.resize((cw, ch), Image.LANCZOS)
    layer = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
    layer.alpha_composite(img, ((FRAME_W - cw) // 2, (FRAME_H - ch) // 2))
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    layer.save(out_png)
    return out_png


# ===========================================================================
# ffmpeg composites (segment always input 0 = the primary → output length
# follows the background; single-frame / shorter overlays repeat their last
# frame via framesync, so a short card freezes while the background walks on)
# ===========================================================================

def _run_ffmpeg(cmd: list[str], what: str) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = Path(cmd[-1])
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"{what} failed: {r.stderr[-600:]}")


_ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-an"]


def _composite_card(segment: str, local: str, work_dir: Path, stem: str,
                    out_mp4: str) -> str:
    cw, ch, x, y = _card_geometry(local)
    backing = _card_backing_png(cw, ch, str(work_dir / f"{stem}_backing.png"))
    seg_dur = _probe_duration(segment)
    # NOTE: no `-loop 1` anywhere — with framesync, eof_action=repeat applies
    # to WHICHEVER stream ends first (the background too!), so an infinite
    # image input would keep the encode running forever repeating the
    # background's last frame. A plain single-frame still repeats just fine,
    # and `-t <segment>` hard-bounds the output either way (also stopping a
    # card CLIP that runs longer than its window).
    fc = (f"[2:v]scale={cw}:{ch},setsar=1[card];"
          f"[0:v][1:v]overlay=0:0:format=auto[b1];"
          f"[b1][card]overlay={x}:{y}:format=auto[vout]")
    _run_ffmpeg(["ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                 "-i", segment, "-i", backing, "-i", local,
                 "-filter_complex", fc, "-map", "[vout]",
                 "-t", f"{seg_dur:.3f}"] + _ENC + [out_mp4],
                f"card composite for {Path(local).name}")
    return out_mp4


def _composite_keyed(segment: str, local: str, treatment: str,
                     work_dir: Path, stem: str, out_mp4: str) -> str:
    seg_dur = _probe_duration(segment)
    if _is_image(local):   # stills: PIL key → one transparent overlay input
        layer = _keyed_still_layer(
            local, treatment, str(work_dir / f"{stem}_keyed.png"))
        fc = "[0:v][1:v]overlay=0:0:format=auto[vout]"
        _run_ffmpeg(["ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                     "-i", segment, "-i", layer,
                     "-filter_complex", fc, "-map", "[vout]",
                     "-t", f"{seg_dur:.3f}"] + _ENC + [out_mp4],
                    f"keyed-still composite for {Path(local).name}")
        return out_mp4
    # video clips: ffmpeg colorkey (white), fitted into the card box
    box_w = int(FRAME_W * VIDEO_BG_CARD_SCALE) // 2 * 2
    box_h = int(FRAME_H * VIDEO_BG_CARD_SCALE) // 2 * 2
    iw, ih = _dims(local)
    cw, ch = _fit_box(iw, ih, box_w, box_h)
    x, y = (FRAME_W - cw) // 2, (FRAME_H - ch) // 2
    fc = (f"[1:v]format=rgba,"
          f"colorkey=0xFFFFFF:{VIDEO_BG_KEY_SIMILARITY}:{VIDEO_BG_KEY_BLEND},"
          f"scale={cw}:{ch}[g];"
          f"[0:v][g]overlay={x}:{y}:format=auto[vout]")
    _run_ffmpeg(["ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                 "-i", segment, "-i", local,
                 "-filter_complex", fc, "-map", "[vout]",
                 "-t", f"{seg_dur:.3f}"] + _ENC + [out_mp4],
                f"keyed-video composite for {Path(local).name}")
    return out_mp4


# ===========================================================================
# Blank scenes: bare background, optionally decorated/captioned ON it
# ===========================================================================

def _decorate_blank_segment(segment: str, row: dict, text: str,
                            bg_source: str, bg_offset: float,
                            work_dir: Path, stem: str) -> str:
    """`background` + decorate/caption: open the LIVE overlay editor on the
    exact background frame where this line starts (stamp tab pre-loaded from
    the row's stamp_source + term, zoom crops your own footage) and/or add
    the caption layer, and burn onto the bare segment. Returns the segment
    to use (burned, or the original if nothing was added)."""
    from ___visuals.video_chains import (
        DecoLayer,
        burn_ops_onto_segment,
        extract_frame_normalised,
    )

    ops: list = []

    if scene_wants_decorate(row):
        from ___visuals.decorate_stage import _ops_from_editor, _stamps_for_row
        from ___visuals.decorator.api import run_overlay_decorator
        frame = str(work_dir / f"{stem}_frame.png")
        extract_frame_normalised(bg_source, bg_offset, frame)
        raw = run_overlay_decorator(
            frame, stamps=_stamps_for_row(row),
            title=f"decorate (on the BACKGROUND): {text[:40]}")
        ops.extend(_ops_from_editor(raw, work_dir, stem))

    if scene_wants_caption(row):
        cap_text = (row.get("caption_text") or row.get("search_term") or "").strip()
        if not cap_text:
            print(f"[video-bg]   WARNING: caption has no text "
                  f"(caption_text/search_term empty) — skipping it")
        else:
            from ___visuals.make_text_overlay import make_caption_layer
            cap = DecoLayer()
            cap.caption_png = make_caption_layer(
                cap_text, str(work_dir / f"{stem}_caption.png"), seed=text)
            ops.append(("layer", cap))
            print(f"[video-bg]   auto caption (on background): "
                  f"'{cap_text[:40]}'")

    if not ops:
        return segment
    burned = str(work_dir / f"{stem}_deco.mp4")
    burn_ops_onto_segment(segment, ops, burned)
    return burned


# ===========================================================================
# Planning + the stage
# ===========================================================================

def _plan_scene_windows() -> tuple[list[tuple[str, float, float]], float]:
    """[(script_text, start_sec, duration_sec), ...] in play order, from the
    SAME absolute timestamps the stitcher budgets frames with (the last
    scene runs to the narration's end) — so the background lines up with
    the cuts. Returns (plan, audio_len)."""
    p = Path(TIMESTAMPS_ABSOLUTE_FILE)
    if not p.exists():
        print(f"[video-bg] FATAL: timestamps file missing: {p}")
        print("[video-bg]   (did run_audio_script_synchronizer run?)")
        sys.exit(1)
    abs_ts = json.loads(p.read_text())
    audio_len = _probe_duration(SCRIPT_AUDIO_FILE)
    if audio_len <= 0:
        print(f"[video-bg] FATAL: couldn't read the narration's length "
              f"from {SCRIPT_AUDIO_FILE}")
        sys.exit(1)
    anchors = sorted(abs_ts.items(), key=lambda x: float(x[1]))
    plan = []
    for i, (text, start) in enumerate(anchors):
        start = float(start)
        end = float(anchors[i + 1][1]) if i + 1 < len(anchors) else audio_len
        plan.append((text, start, max(0.0, end - start)))
    return plan, audio_len


def run_video_background_stage(
    final_data: list[dict],
    script_to_search_term: dict[str, SearchTermData],
) -> tuple[list[dict], dict[str, str]]:
    """Composite every scene onto its slice of the continuous background
    video (cards / keyed graphics / bare background). Returns
    (final_data, path_remap) like the other passes."""
    print("\n" + "=" * 70)
    print("[video-bg] VIDEO BACKGROUND MODE (overlay the edit on your footage)")
    print(f"[video-bg] enabled={VIDEO_BACKGROUND_MODE}")
    print("=" * 70)

    if not VIDEO_BACKGROUND_MODE:
        stranded = [t for t, r in script_to_search_term.items()
                    if scene_type(r) == MediaType.BACKGROUND]
        if stranded:
            print(f"[video-bg] WARNING: {len(stranded)} scene(s) are typed "
                  f"`background` but VIDEO_BACKGROUND_MODE is False — they "
                  f"will render NOTHING. Enable the mode (and set "
                  f"VIDEO_BACKGROUND_FILE) or retag them:")
            for t in stranded:
                print(f"[video-bg]   - '{t[:70]}'")
        print("[video-bg] mode off — skipping")
        return final_data, {}

    # ── validate the source against the narration ────────────────────────
    bg = str(VIDEO_BACKGROUND_FILE or "")
    if not bg or not Path(bg).exists():
        print(f"[video-bg] FATAL: VIDEO_BACKGROUND_FILE not found: {bg!r}")
        sys.exit(1)
    start = _parse_start(VIDEO_BACKGROUND_START)
    plan, audio_len = _plan_scene_windows()
    bg_len = _probe_duration(bg)
    if start + audio_len > bg_len + 0.05:
        print(f"[video-bg] FATAL: not enough background video.")
        print(f"[video-bg]   {Path(bg).name} is {bg_len:.2f}s long; starting "
              f"at {start:.2f}s leaves {max(0.0, bg_len - start):.2f}s, but "
              f"the narration needs {audio_len:.2f}s.")
        print(f"[video-bg]   Move VIDEO_BACKGROUND_START earlier or use a "
              f"longer video.")
        sys.exit(1)
    print(f"[video-bg] {Path(bg).name}: {bg_len:.2f}s, starting @ {start:.2f}s "
          f"— narration {audio_len:.2f}s across {len(plan)} scene(s)")

    VIDEO_BG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    final_by_text = {e["script_text"]: e for e in final_data}
    path_remap: dict[str, str] = {}

    for n, (text, scene_start, scene_dur) in enumerate(plan):
        row = script_to_search_term.get(text, {})
        entry = final_by_text.get(text)
        footage = (entry or {}).get("footage") or []
        stem = f"bg_{n:03d}_{_safe_stem(text)}"
        offset = start + scene_start

        print(f"\n[video-bg] [{n + 1}/{len(plan)}] '{text[:55]}'  "
              f"(bg @ {offset:.2f}s, {scene_dur:.2f}s)")
        if scene_dur <= 0.0:
            print("[video-bg]   zero window — skipping")
            continue

        try:
            # ── BLANK: the bare background (± decorate/caption ON it) ────
            if not footage:
                seg = str(VIDEO_BG_OUTPUT_DIR / f"{stem}.mp4")
                cut_continuing_segment(bg, offset, scene_dur, seg)
                seg = _decorate_blank_segment(seg, row, text, bg, offset,
                                              VIDEO_BG_OUTPUT_DIR, stem)
                entries = [{seg: round(scene_dur, 3)}]
                if entry is None:
                    entry = {"script_text": text, "footage": entries}
                    final_data.append(entry)
                    final_by_text[text] = entry
                else:
                    entry["footage"] = entries
                add_local_paths_to_history({text: entries})
                print(f"[video-bg]   ✓ background only → {Path(seg).name}")
                continue

            # ── FOOTAGE: each clip over its own slice of the window ──────
            pairs = [next(iter(item.items())) for item in footage]
            rel_sum = sum(float(t) for _, t in pairs) or 1.0
            new_items: list[dict] = []
            cursor = 0.0
            for j, (key, rel) in enumerate(pairs):
                share = (scene_dur - cursor if j == len(pairs) - 1
                         else scene_dur * float(rel) / rel_sum)
                if share <= 0.01:
                    continue
                local = _resolve_to_local_path(key)
                sub = f"{stem}_{j:02d}"
                if not local:
                    print(f"[video-bg]   WARNING: unresolved footage "
                          f"{str(key)[:60]} — keeping it as-is")
                    new_items.append({key: round(share, 3)})
                    cursor += share
                    continue
                seg = str(VIDEO_BG_OUTPUT_DIR / f"{sub}_seg.mp4")
                cut_continuing_segment(bg, offset + cursor, share, seg)
                treatment = _detect_treatment(local, VIDEO_BG_OUTPUT_DIR, sub)
                out = str(VIDEO_BG_OUTPUT_DIR / f"{sub}.mp4")
                if treatment == "card":
                    _composite_card(seg, local, VIDEO_BG_OUTPUT_DIR, sub, out)
                else:
                    _composite_keyed(seg, local, treatment,
                                     VIDEO_BG_OUTPUT_DIR, sub, out)
                new_items.append({out: round(share, 3)})
                path_remap[key] = out
                print(f"[video-bg]   ✓ [{treatment}] {Path(local).name} → "
                      f"{Path(out).name} ({share:.2f}s)")
                cursor += share
            if new_items:
                entry["footage"] = new_items
                add_local_paths_to_history({text: new_items})

        except SourceExhausted as exc:
            # can't happen after the upfront length check unless the
            # timestamps and the audio disagree — treat as data corruption
            print(f"[video-bg] FATAL: {exc}")
            print("[video-bg]   (timestamps run past the validated audio "
                  "length — regenerate the sync files)")
            sys.exit(1)

    print(f"\n[video-bg] DONE — {len(path_remap)} clip(s) composited onto "
          f"the background")
    return final_data, path_remap
