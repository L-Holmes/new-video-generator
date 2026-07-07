"""
DECORATE STAGE — pipeline adapter for the standalone decorator package
(___visuals/decorator/): every scene carrying the `decorate` modifier opens
its OWN finished footage in the ONE editor.

Editor tools (see decorator/tools.py) — ALL LIVE, all reusing the proven
GUIs: stamp + zoom (MANUAL_STOCK_PLACEMENT), draw (DECORATE_PREVIOUS's full
editor: text boxes, arrows, highlights, circles, lines, rectangles), and
text (a tilted MAKE_TEXT_OVERLAY caption, prefilled from the search_term).

Model reminder: "zoom into the previous image" = hold_previous + decorate
(hold resolves the previous image as this scene's footage; the zoom tool
crops it) — the stay-same-plus-edit default. On any other base the editor
decorates THAT scene's image.

LIVE VIDEO CHAINS (DECORATE_VIDEO_LIVE, VIDEO_CHAINS.py): when the base is
a stock VIDEO — a video scene with `decorate`, or hold_previous scenes
after a video where any member decorates — the footage does NOT freeze.
The source keeps playing across the scene cuts (each scene continues where
the previous stopped) and the decorations are transparent LAYERS burned
over the moving picture, accumulating down the chain. You decorate the
exact frame on screen when your scene starts (earlier layers included);
only draw + stamp are offered there (zoom/object change the geometry,
which can't sit over moving video). STATIC_RENDER (2.64) cuts the
continuing segments; this stage opens the editor and burns the layers.

Runs at stage 2.645, after every stage that decides a scene's own image and
before colour grade + Ken Burns. Output is a static MP4 so KB never crops
the edits (chain outputs are moving MP4s — KB skips those the same way).
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import re
from pathlib import Path

from ___visuals.CACHE_IO import _resolve_to_local_path
from ___visuals.CONFIG import (
    DECORATE_OUTPUT_DIR,
    DECORATE_RENDER_SAFETY_PAD_SEC,
    IMAGE_EXTENSIONS,
    SearchTermData,
    scene_wants_caption,
    scene_wants_decorate,
)
from ___visuals.decorator import run_decorator
from ___visuals.PREVIOUS_ENTRY_PREVIEW import (
    PreviousEntryPreview,
    build_previous_preview,
)
from ___visuals.TIMING_MERGE import _load_scene_timings


def _is_image(path: str) -> bool:
    from pathlib import Path as _P

    return _P(path).suffix.lower() in IMAGE_EXTENSIONS


def _safe_stem(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:50] or "scene"


def _scene_base_image(entry: dict, out_dir: Path, stem: str) -> str | None:
    """The scene's own footage as a local IMAGE (first frame if it's video)."""
    from ___visuals.MANUAL_STOCK_PLACEMENT import extract_frame  # lazy (Tk)

    footage = (entry or {}).get("footage") or []
    key = next(iter(footage[0]), None) if footage else None
    local = _resolve_to_local_path(key) if key else None
    if not local:
        return None
    if _is_image(local):
        return local
    frame = str(out_dir / f"{stem}_basefrm.png")
    return extract_frame(local, frame)


def swap_stamp_rows_for_review(
    script_to_search_term: dict[str, SearchTermData],
) -> int:
    """Called by main() BEFORE the candidates fetch: every row with a
    stamp_source (hold_previous/background + decorate + a term) temporarily
    BECOMES that media type, so the ordinary machinery fetches and reviews
    its stamp candidates exactly like any stock / wikipedia / ai_stock
    scene — no special cases anywhere in the fetch or the review. The
    original type is kept on the row and put back (with the picks stashed
    as stamps) by restore_stamp_rows_after_review(). In-memory only — the
    tagging json is never touched. Returns the number of rows swapped."""
    from ___visuals.CONFIG import MediaType, scene_stamp_source

    n = 0
    for text, row in script_to_search_term.items():
        src = scene_stamp_source(row)
        if not src or "_stamp_orig_type" in row:
            continue
        row["_stamp_orig_type"] = row["media_type"]
        row["media_type"] = MediaType(src)
        n += 1
        print(
            f"[stamps] '{text[:50]}' reviews as {src} "
            f"(stamp: '{(row.get('search_term') or '')[:40]}')"
        )
    if n:
        print(f"[stamps] {n} stamp scene(s) join the normal fetch + review")
    return n


def restore_stamp_rows_after_review(
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
) -> None:
    """Called by main() AFTER the stage-1 review: puts every swapped row's
    real media type back and moves its review PICKS out of final_data into
    row['stamp_paths'] (resolved to local files; a picked video contributes
    its first frame). The scene's entry is REMOVED from final_data — the
    picks were stamp choices, not scene footage; the hold/background stages
    decide the scene's own picture exactly as before."""
    from ___visuals.CACHE_IO import _classify_footage_path

    swapped = [t for t, r in script_to_search_term.items() if "_stamp_orig_type" in r]
    if not swapped:
        return
    by = {e["script_text"]: e for e in final_data}
    for text in swapped:
        row = script_to_search_term[text]
        row["media_type"] = row.pop("_stamp_orig_type")
        entry = by.get(text)
        picks = [k for item in (entry or {}).get("footage") or [] for k in item]
        paths: list[str] = []
        for key in picks:
            local = _resolve_to_local_path(key)
            if not local:
                print(
                    f"[stamps] WARNING: pick unresolved for "
                    f"'{text[:45]}': {str(key)[:60]}"
                )
                continue
            if _classify_footage_path(local) == "video":
                import hashlib

                from ___visuals.MANUAL_STOCK_PLACEMENT import extract_frame

                DECORATE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                frame = str(
                    DECORATE_OUTPUT_DIR
                    / (
                        "stampfrm_"
                        + hashlib.md5(local.encode()).hexdigest()[:10]
                        + ".png"
                    )
                )
                try:
                    extract_frame(local, frame)
                    local = frame
                except Exception as exc:
                    print(
                        f"[stamps] WARNING: frame extract failed "
                        f"({exc}) — skipping that pick"
                    )
                    continue
            paths.append(local)
        row["stamp_paths"] = paths
        if entry is not None:
            final_data.remove(entry)  # stamp picks, not scene footage
        state = (
            f"{len(paths)} stamp(s) ready" if paths else "NO stamps (was it reviewed?)"
        )
        print(f"[stamps] '{text[:50]}' → {state}")


def _decorate_stamp(
    path: str, previous_preview: PreviousEntryPreview | None = None
) -> str:
    """stamp_decorate: open the picked stamp itself in the FULL decorator
    (draw / stamp / zoom / object — cut it out, clean it up) BEFORE it's
    offered in the scene's stamp tab. The result caches per source image;
    delete the cached file to redo it."""
    import hashlib

    out = DECORATE_OUTPUT_DIR / (
        "stamp_deco_" + hashlib.md5(str(path).encode()).hexdigest()[:12] + ".png"
    )
    if out.exists() and out.stat().st_size > 0:
        print(f"[stamps]   pre-decorated stamp cached: {out.name} (delete it to redo)")
        return str(out)
    DECORATE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result = run_decorator(
        base_image_path=str(path),
        out_path=str(out),
        title=f"decorate STAMP: {Path(path).name}",
        previous_preview=previous_preview,
    )
    if not result:
        return str(path)  # no edits — use it as picked
    if Path(result).suffix.lower() == ".mp4":  # armed animated object export
        print(
            "[stamps]   WARNING: the stamp editor exported an ANIMATED "
            "result — stamps are stills, using its first frame"
        )
        from ___visuals.MANUAL_STOCK_PLACEMENT import extract_frame

        frame = str(out.with_suffix(".frame.png"))
        try:
            return extract_frame(result, frame)
        except Exception:
            return str(path)
    return result


def _stamps_for_row(
    row: dict,
    previous_preview: PreviousEntryPreview | None = None,
) -> list[str]:
    """The pictures for the editor's stamp tab: the user's REVIEW PICKS
    (stashed on the row by restore_stamp_rows_after_review), each optionally
    pre-decorated first (stamp_decorate). [] when the row has no stamp
    source or nothing was picked — the editor's own 'pick a file' always
    remains."""
    from ___visuals.CONFIG import scene_stamp_source

    if not scene_stamp_source(row):
        return []
    stamps = [p for p in (row.get("stamp_paths") or []) if Path(p).exists()]
    if not stamps:
        print(
            "[decorate]   WARNING: stamp scene has no reviewed picks — "
            "the stamp tab starts empty (was the review completed?)"
        )
        return []
    if row.get("stamp_decorate"):
        stamps = [_decorate_stamp(p, previous_preview) for p in stamps]
    return stamps


def _previous_preview_for_scene(
    text: str,
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
    stem: str,
    fallback_image_path: str | None = None,
) -> PreviousEntryPreview | None:
    return build_previous_preview(
        text,
        script_to_search_term,
        final_data=final_data,
        frame_dir=DECORATE_OUTPUT_DIR,
        frame_stem=f"{stem}_previous",
        fallback_image_path=fallback_image_path,
    )


def _ops_from_editor(raw_ops: list, out_dir: Path, stem: str):
    """Turn the editor's raw ops recipe ([("layer", items) | ("zoom", box)])
    into burn-ready ops: each layer's sprites + highlight mask rendered to
    PNGs (DecoLayer), zooms passed through. Empty layers are dropped."""
    from ___visuals.decorator.draw import (
        HighlightDeco,
        render_highlight_mask,
        render_overlay_layer,
    )
    from ___visuals.VIDEO_CHAINS import FRAME_H, FRAME_W, DecoLayer

    ops = []
    for oi, (kind, payload) in enumerate(raw_ops or []):
        if kind == "zoom":
            ops.append(("zoom", payload))
            continue
        items = payload
        layer = DecoLayer()
        layer.highlights = [it for it in items if isinstance(it, HighlightDeco)]
        layer.sprites_png = render_overlay_layer(
            items, (FRAME_W, FRAME_H), str(out_dir / f"{stem}_l{oi}_sprites.png")
        )
        layer.highlight_mask = render_highlight_mask(
            items, (FRAME_W, FRAME_H), str(out_dir / f"{stem}_l{oi}_hlmask.png")
        )
        if not layer.empty:
            ops.append(("layer", layer))
    return ops


def _run_video_chain(
    chain,  # VIDEO_CHAINS.VideoChain
    chain_idx: int,
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
    final_by_text: dict[str, dict],
    path_remap: dict[str, str],
) -> int:
    """ONE live chain: the source video keeps playing across its scenes, and
    decorations are burned OVER the moving footage, accumulating.

    Per member, in scene order:
      1. if it decorates: grab the frame at its offset (the exact picture on
         screen when the scene starts), apply every EARLIER op to it, and
         open the overlay editor (draw + stamp + zoom; the stamp tab is
         pre-loaded from the row's stamp_source + search_term and opens
         ACTIVE) — the ops come back un-baked: layers become transparent
         PNGs, zooms become real crops of the moving footage;
      2. if it captions: a transparent MAKE_TEXT_OVERLAY layer joins it
         (same per-seed position pick as the still path);
      3. burn ALL ops so far onto the member's continuing segment (cut by
         STATIC_RENDER at 2.64; the anchor's is cut here at offset 0) and
         remap its footage — so a hold that adds nothing itself still
         carries everything drawn (and any zoom) before it.

    Returns the number of scenes whose footage changed."""
    from ___visuals.decorator.api import run_overlay_decorator
    from ___visuals.MAKE_TEXT_OVERLAY import make_caption_layer
    from ___visuals.VIDEO_CHAINS import (
        DecoLayer,
        SourceExhausted,
        burn_ops_onto_segment,
        composite_ops_for_preview,
        cut_continuing_segment,
        extract_frame_normalised,
    )

    ops: list = []  # accumulated down the chain
    changed = 0
    total = len(chain.members)

    for k, m in enumerate(chain.members):
        row = script_to_search_term.get(m.text, {})
        entry = final_by_text.get(m.text)
        stem = f"chain_{chain_idx:03d}_{k:02d}_{_safe_stem(m.text)}"
        print(
            f"\n[decorate] [chain {chain_idx + 1} · {k + 1}/{total}] "
            f"'{m.text[:55]}'  (source @ {m.offset:.2f}s)"
        )

        if not entry or not entry.get("footage"):
            print(f"[decorate]   WARNING: no footage entry — skipping")
            continue
        if m.duration <= 0:
            print(f"[decorate]   WARNING: no/zero duration — skipping")
            continue

        # 1) the overlay editor, on the frame the viewer sees at m.offset
        #    with everything applied so far already on it.
        if m.wants_decorate:
            frame = str(DECORATE_OUTPUT_DIR / f"{stem}_frame.png")
            extract_frame_normalised(chain.source, m.offset, frame)
            if ops:
                composite_ops_for_preview(frame, ops, frame)
            previous_preview = _previous_preview_for_scene(
                m.text,
                script_to_search_term,
                final_data,
                stem,
                fallback_image_path=frame,
            )
            raw = run_overlay_decorator(
                frame,
                stamps=_stamps_for_row(row, previous_preview),
                title=f"decorate (LIVE video): {m.text[:40]}",
                previous_preview=previous_preview,
            )
            ops.extend(_ops_from_editor(raw, DECORATE_OUTPUT_DIR, stem))

        # 2) the AUTOMATIC caption — as a transparent layer here, so it sits
        #    over the moving footage too (same seed → same corner as the
        #    still path would have picked).
        if m.wants_caption:
            text = (row.get("caption_text") or row.get("search_term") or "").strip()
            if not text:
                print(
                    f"[decorate]   WARNING: caption has no text "
                    f"(caption_text/search_term empty) — skipping it"
                )
            else:
                cap = DecoLayer()
                cap.caption_png = make_caption_layer(
                    text, str(DECORATE_OUTPUT_DIR / f"{stem}_caption.png"), seed=m.text
                )
                ops.append(("layer", cap))
                print(f"[decorate]   auto caption (layer): '{text[:40]}'")

        if not ops:
            # nothing applied yet anywhere in the chain — this member's
            # continuing segment (from stage 2.64) already plays untouched.
            print(f"[decorate]   no ops yet — segment plays undecorated")
            continue

        # 3) burn EVERYTHING accumulated so far onto this member's segment.
        if m.is_anchor:
            seg = str(DECORATE_OUTPUT_DIR / f"{stem}_seg.mp4")
            try:
                cut_continuing_segment(chain.source, 0.0, m.duration, seg)
            except SourceExhausted as exc:  # can't happen at offset 0, but…
                print(f"[decorate]   WARNING: {exc} — leaving as-is")
                continue
        else:
            key = next(iter(entry["footage"][0]))
            seg = _resolve_to_local_path(key)
            if not seg:
                print(
                    f"[decorate]   WARNING: segment unresolved "
                    f"({key[:60]}) — leaving as-is"
                )
                continue

        mp4 = str(DECORATE_OUTPUT_DIR / f"{stem}.mp4")
        burn_ops_onto_segment(seg, ops, mp4)
        old_key = next(iter(entry["footage"][0]))
        entry["footage"] = [{mp4: round(float(m.duration), 3)}]
        path_remap[old_key] = mp4
        changed += 1
        print(
            f"[decorate]   ✓ {Path(mp4).name} "
            f"({len(ops)} op(s), trim {round(float(m.duration), 3)}s)"
        )

    return changed


def run_decorate_stage(
    final_data: list[dict],
    script_to_search_term: dict[str, SearchTermData],
) -> tuple[list[dict], dict[str, str]]:
    """Open the decorator for every scene carrying `decorate`. Returns
    (final_data, path_remap) like the other passes."""
    print("\n" + "=" * 70)
    print("[decorate] decorate (editor) + caption (automatic) scenes")
    print("=" * 70)

    scene_timings = _load_scene_timings()
    final_by_text = {e["script_text"]: e for e in final_data}

    wanting_all = [
        txt
        for txt, row in script_to_search_term.items()
        if scene_wants_decorate(row) or scene_wants_caption(row)
    ]
    if not wanting_all:
        print("[decorate] no decorate/caption scenes — skipping")
        return final_data, {}

    chains = []
    if any(scene_wants_decorate(row) for row in script_to_search_term.values()):
        try:
            from ___visuals.VIDEO_CHAINS import detect_video_chains
        except ImportError as exc:
            print(
                f"[decorate] WARNING: live video chain detection unavailable "
                f"({exc}) — using still decorate path"
            )
        else:
            # LIVE chains: video anchor + hold run where someone decorates → the
            # source keeps playing and decorations are LAYERS over the moving
            # footage (accumulating). Their members are handled by _run_video_chain
            # (decorate, caption and all) and excluded from the still loop below —
            # the same detection STATIC_RENDER used to cut the continuing segments.
            chains = detect_video_chains(
                script_to_search_term, final_data, scene_timings
            )
    chain_texts: set[str] = set()
    for _c in chains:
        chain_texts |= _c.member_texts

    wanting = [txt for txt in wanting_all if txt not in chain_texts]

    from ___visuals.STATIC_RENDER import _render_image_to_static_mp4  # lazy

    DECORATE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path_remap: dict[str, str] = {}

    if chains:
        print(
            f"\n[decorate] {len(chains)} LIVE video chain(s) — the footage "
            f"keeps playing; decorations become layers"
        )
        for chain_idx, chain in enumerate(chains):
            _run_video_chain(
                chain,
                chain_idx,
                script_to_search_term,
                final_data,
                final_by_text,
                path_remap,
            )

    for idx, txt in enumerate(wanting):
        row = script_to_search_term[txt]
        entry = final_by_text.get(txt)
        stem = f"decorate_{idx:03d}_{_safe_stem(txt)}"
        base = _scene_base_image(entry, DECORATE_OUTPUT_DIR, stem)
        if not base:
            print(
                f"[decorate] WARNING: no resolved footage for '{txt[:60]}' "
                f"— leaving as-is"
            )
            continue
        duration = float(scene_timings.get(txt, 0.0))
        if duration <= 0:
            print(
                f"[decorate] WARNING: no/zero timing for '{txt[:60]}' — leaving as-is"
            )
            continue

        print(f"\n[decorate] [{idx + 1}/{len(wanting)}] '{txt[:60]}'")
        current = base
        changed = False

        # 1) the interactive editor (draw hub + stamp/zoom/object tabs).
        #    The stamp tab is pre-loaded (and opens active) when the row has
        #    a stamp_source — its search_term fetched via STAMP_FETCH.
        if scene_wants_decorate(row):
            previous_preview = _previous_preview_for_scene(
                txt,
                script_to_search_term,
                final_data,
                stem,
                fallback_image_path=current,
            )
            edited = run_decorator(
                base_image_path=current,
                out_path=str(DECORATE_OUTPUT_DIR / f"{stem}.png"),
                stamps=_stamps_for_row(row, previous_preview),
                title=f"decorate: {txt[:40]}",
                previous_preview=previous_preview,
            )
            if edited:
                current = edited
                changed = True

        # 2) the AUTOMATIC tilted caption (the `caption` modifier) — no GUI.
        #    Text = the row's caption_text if present, else its search_term.
        #    Baked ON TOP of whatever the editor produced.
        if scene_wants_caption(row):
            text = (row.get("caption_text") or row.get("search_term") or "").strip()
            if not text:
                print(
                    f"[decorate] WARNING: caption on '{txt[:50]}' has no "
                    f"text (caption_text/search_term empty) — skipping it"
                )
            else:
                from ___visuals.MAKE_TEXT_OVERLAY import make_text_overlay

                cap_png = str(DECORATE_OUTPUT_DIR / f"{stem}_caption.png")
                make_text_overlay(current, text, cap_png, seed=txt)
                current = cap_png
                changed = True
                print(f"[decorate]   auto caption: '{text[:40]}'")

        if not changed:
            continue  # no edits — keep the original footage
        edited = current

        mp4 = str(DECORATE_OUTPUT_DIR / f"{stem}.mp4")
        if Path(edited).suffix.lower() == ".mp4":
            # the editor's object tab exported an ANIMATED result — use it
            # directly (Ken Burns skips MP4s), no still-baking. run_decorator
            # redirects its own save to <stem>.mp4, so `edited` is usually
            # ALREADY this exact path — copying a file onto itself raises
            # shutil.SameFileError.
            if Path(edited).resolve() != Path(mp4).resolve():
                import shutil

                shutil.copy2(edited, mp4)
        else:
            _render_image_to_static_mp4(
                edited, duration + DECORATE_RENDER_SAFETY_PAD_SEC, mp4
            )
        old_key = next(iter(entry["footage"][0]))
        entry["footage"] = [{mp4: round(duration, 3)}]
        path_remap[old_key] = mp4
        print(f"[decorate]   ✓ {Path(mp4).name} (trim {round(duration, 3)}s)")

    print(f"\n[decorate] DONE — {len(path_remap)} scene(s) decorated")
    return final_data, path_remap
