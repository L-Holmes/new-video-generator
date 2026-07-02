"""
Locally-rendered scene generators (no external candidates, no review):
  - generate_joint_scenes      — multi-image collage MP4s (joint compositor)
  - generate_read_out_scenes   — kinetic typography
  - generate_map_scenes        — highlighted place maps
  - generate_stickman_explain_scenes — chosen stock/wiki clip on a board base
  - generate_text_overlay_scenes     — caption on the previous scene's image

plus the LOCAL_FOOTAGE_GENERATORS registry and run_all_local_generators that
drives the registry-based ones. The explainer / text-overlay generators are
invoked directly by main() (they run after review), not via the registry.

MAKE_EXPLAINER_IMAGE / MAKE_TEXT_OVERLAY are imported lazily inside their
generators.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable

from CACHE_IO import _resolve_to_local_path, load_json
from CONFIG import (
    _CACHE_DIR,
    CANDIDATES_CACHE_FILE,
    JOINT_BASE_DURATION_FALLBACK_SEC,
    JOINT_LAYOUT_POSITIONS,
    MAP_ENABLE,
    MAP_GEOCODE_CACHE_DIR,
    MAP_OUTPUT_DIR,
    READ_OUT_ENABLE,
    READ_OUT_RENDER_SAFETY_PAD_SEC,
    STICKMAN_EXPLAIN_OUTPUT_DIR,
    STICKMAN_EXPLAIN_RENDER_SAFETY_PAD_SEC,
    STICKMAN_TEXT_OVERLAY_OUTPUT_DIR,
    STICKMAN_TEXT_OVERLAY_RENDER_SAFETY_PAD_SEC,
    TIMESTAMPS_ABSOLUTE_FILE,
    WORD_TIMINGS_FILE,
    MediaType,
    SearchTermData,
    MEDIA_PROPERTIES,
    media_props,
)
from DOWNLOADS import _download_image
from GET_MAP import get_map_image
from JOINT_IMAGE_CREATOR import TRANSITION_RANDOM
from JOINT_IMAGE_CREATOR import composite as create_joint_scene
from STATIC_RENDER import _render_image_to_static_mp4
from TIMING_MERGE import (
    _build_footage_entries_for_stage,
    _compute_joint_stage_timing,
    _load_scene_timings,
)
from WORDS_ON_SCREEN import WordRenderConfig, render_scene_to_video


# === BEGIN verbatim move from main.py (scene generators) ===
def generate_joint_scenes(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],
    final_data: list[dict] | None = None,
) -> dict[str, list[dict]]:
    """
    Build joint composite scenes for any scene whose search_type is in
    JOINT_TYPES, and return a stitcher-ready map of:

        { script_text: [ {local_path: trim_seconds}, ... ], ... }

    Each joint stage typically contributes TWO entries (intro + loop). Very
    short scenes get just one entry — the loop file alone.

    Adjacent scenes are grouped if they share the SAME joint search_type
    AND have contiguous positions. Each group becomes one composite render.
    """

    print("\n" + "=" * 70)
    print("[joint scenes] STARTING generate_joint_scenes")
    print(
        f"[joint scenes] script_to_search_term has {len(script_to_search_term)} entries"
    )
    print(f"[joint scenes] candidates_data has {len(candidates_data)} entries")
    print(f"[joint scenes] joint types registered: " f"{[mt.value for mt, p in MEDIA_PROPERTIES.items() if p.is_joint]}")
    print("=" * 70)

    scene_timings = _load_scene_timings()

    candidates_by_text: dict[str, dict] = {c["script_text"]: c for c in candidates_data}
    candidates_by_stripped: dict[str, dict] = {
        c["script_text"].strip(): c for c in candidates_data
    }
    print(
        f"[joint scenes] candidates lookup built with {len(candidates_by_text)} entries"
    )

    # Map script_text -> the image the user CHOSE in review (url or local path).
    # The compositor uses this instead of candidate[0], so edited/regenerated
    # tiles flow through correctly.
    chosen_by_text: dict[str, str | None] = {}
    for entry in final_data or []:
        footage = entry.get("footage") or []
        key = next(iter(footage[0]), None) if footage else None
        chosen_by_text[entry["script_text"]] = key

    # 1) Locate all scenes whose search_type is a joint type.
    joint_scenes: list[tuple[str, SearchTermData]] = []
    for script_text, scene_data in script_to_search_term.items():
        if not media_props(scene_data["search_type"]).is_joint:
            continue
        joint_scenes.append((script_text, scene_data))

    print(f"\n[joint scenes] found {len(joint_scenes)} joint scene(s)")
    if not joint_scenes:
        print("[joint scenes] no joint scenes — returning empty map")
        return {}

    joint_scenes.sort(key=lambda scene: int(scene[1]["position"]))
    for i, (txt, data) in enumerate(joint_scenes):
        print(
            f"[joint scenes]   sorted[{i}]: pos={data['position']}, "
            f"type={data['search_type'].value}, script='{txt[:60]}...'"
        )

    # 2) Group consecutive joints by (same search_type + contiguous position).
    grouped_joint_scenes: list[list[tuple[str, SearchTermData]]] = []
    current_group: list[tuple[str, SearchTermData]] = []
    previous_scene_data = None

    for script_text, scene_data in joint_scenes:
        if not previous_scene_data:
            current_group.append((script_text, scene_data))
            previous_scene_data = scene_data
            continue

        same_type = scene_data["search_type"] == previous_scene_data["search_type"]
        next_position = (
            int(scene_data["position"]) == int(previous_scene_data["position"]) + 1
        )

        if same_type and next_position:
            current_group.append((script_text, scene_data))
        else:
            grouped_joint_scenes.append(current_group)
            current_group = [(script_text, scene_data)]

        previous_scene_data = scene_data

    if current_group:
        grouped_joint_scenes.append(current_group)

    print(f"\n[joint scenes] formed {len(grouped_joint_scenes)} group(s)")
    for gi, grp in enumerate(grouped_joint_scenes):
        positions = [s[1]["position"] for s in grp]
        joint_type = grp[0][1]["search_type"]
        print(
            f"[joint scenes]   group {gi}: type={joint_type.value}, "
            f"positions={positions}, size={len(grp)}"
        )

    # 3) Generate each group + collect footage entries.
    script_text_to_footage_entries: dict[str, list[dict]] = {}

    for group_index, group in enumerate(grouped_joint_scenes):
        joint_type = group[0][1]["search_type"]
        print(
            f"\n[joint scenes] processing group {group_index}: "
            f"type={joint_type.value}, size={len(group)}"
        )

        # Look up layout for this joint type.
        layout_positions = JOINT_LAYOUT_POSITIONS.get(joint_type)
        if not layout_positions:
            print(
                f"[joint scenes] FATAL: no layout registered for "
                f"{joint_type.value} in JOINT_LAYOUT_POSITIONS"
            )
            sys.exit(1)

        # Per-joint-type rendering config. Add a `case` here when adding a
        # new joint layout (and an entry in JOINT_LAYOUT_POSITIONS).
        match joint_type:
            case MediaType.JOINT_3_ROW:
                box_percentage = 50
                transition = TRANSITION_RANDOM
                background_path = "_BACKGROUNDS/bg_crumpled_card.mp4"
                base_duration = JOINT_BASE_DURATION_FALLBACK_SEC
                remove_bg = True

            case MediaType.STICKMAN_JOINT_3_ROW:
                # Identical to JOINT_3_ROW; the ONLY difference is the tiles come
                # from the AI stickman generator instead of Pexels. The stickman
                # images are line art on forced-white backgrounds, so remove_bg
                # cuts the figure out onto the crumpled card. Flip to False if you
                # ever want the white kept.
                box_percentage = 50
                transition = TRANSITION_RANDOM
                background_path = "_BACKGROUNDS/bg_crumpled_card.mp4"
                base_duration = JOINT_BASE_DURATION_FALLBACK_SEC
                remove_bg = True

            case _:
                print(f"[joint scenes] FATAL: unsupported joint type: {joint_type}")
                sys.exit(1)

        stage_timings = [
            _compute_joint_stage_timing(script_text, scene_timings)
            for script_text, _ in group
        ]

        max_loop_duration = max(
            (t["loop_duration"] for t in stage_timings if t["use_transition"]),
            default=0.0,
        )
        max_static_duration = max(
            (t["total_duration"] for t in stage_timings if not t["use_transition"]),
            default=0.0,
        )
        composite_duration = max(max_loop_duration, max_static_duration, base_duration)
        print(f"[joint scenes:timing] composite_duration = {composite_duration:.3f}s")

        items = []
        for item_index, (script_text, _) in enumerate(group):
            if item_index >= len(layout_positions):
                print(
                    f"[joint scenes] FATAL: item_index {item_index} >= layout length {len(layout_positions)}"
                )
                sys.exit(1)

            matching_candidate = candidates_by_text.get(script_text)
            if not matching_candidate:
                matching_candidate = candidates_by_stripped.get(script_text.strip())

            if not matching_candidate:
                print(
                    f"[joint scenes] FATAL: no matching candidate for: '{script_text}'"
                )
                print(f"  HINT: delete {CANDIDATES_CACHE_FILE} and re-run to refresh.")
                sys.exit(1)

            image_candidates = matching_candidate.get("candidates", {}).get(
                "images", []
            )
            if not image_candidates:
                print(f"[joint scenes] FATAL: no image candidates for: '{script_text}'")
                sys.exit(1)

            # Prefer the reviewed pick; fall back to candidate[0] only if this
            # scene was never reviewed (shouldn't happen now joint scenes are
            # in stage-1 review).
            image_url = chosen_by_text.get(script_text) or ""
            if not image_url:
                first_image = image_candidates[0]
                image_url = next(iter(first_image), "")
                print(
                    f"[joint scenes]   no review pick for '{script_text[:50]}' "
                    f"— falling back to candidate[0]"
                )
            else:
                print(
                    f"[joint scenes]   using reviewed pick for "
                    f"'{script_text[:50]}': "
                    f"{Path(image_url).name if '/' in image_url else image_url}"
                )
            if not image_url:
                print(f"[joint scenes] FATAL: no image_url for '{script_text}'")
                sys.exit(1)

            # Resolve the candidate to an on-disk file. Pexels joint_3_row
            # candidates are URLs (download if not cached); stickman_joint
            # candidates are ALREADY-LOCAL AI tiles — never try to download a
            # local path (that's what produced the "No scheme supplied" error).
            local_path = _resolve_to_local_path(image_url)
            if not local_path and image_url.startswith(("http://", "https://")):
                local_path = _download_image(image_url)
            if not local_path:
                print(
                    f"[joint scenes] FATAL: could not resolve image to disk: {image_url}"
                )
                if not image_url.startswith(("http://", "https://")):
                    print(
                        f"  It's a LOCAL file that's gone — most likely the review-GUI"
                    )
                    print(
                        f"  cleanup deleted it because a STALE review decision (from when"
                    )
                    print(
                        f"  this scene had a different search_type) pointed elsewhere."
                    )
                    print(
                        f"  Delete {CANDIDATES_CACHE_FILE} and re-run to regenerate it."
                    )
                else:
                    print(
                        f"  HINT: delete {CANDIDATES_CACHE_FILE} and re-run to refresh."
                    )
                sys.exit(1)

            items.append(
                {
                    "path": local_path,
                    "position": layout_positions[item_index],
                    "scale-fit-box-percentage": box_percentage,
                    "transition": transition,
                    "removeBG": remove_bg,
                }
            )

        if not items:
            print(
                f"[joint scenes] FATAL: no items to composite for group {group_index}"
            )
            sys.exit(1)

        output_folder = Path(_CACHE_DIR) / "joint_scenes" / f"group_{group_index}"
        output_folder.mkdir(parents=True, exist_ok=True)

        create_joint_scene(
            items=items,
            output_folder=str(output_folder),
            composite_flag=True,
            background_path=background_path,
            duration=composite_duration,
        )
        print(f"[joint scenes] ✓ generated group {group_index}")

        num_stages = len(group)
        for stage_index, (script_text, _) in enumerate(group):
            timing = stage_timings[stage_index]
            entries = _build_footage_entries_for_stage(
                group_output_folder=output_folder,
                stage_index=stage_index,
                num_stages=num_stages,
                timing=timing,
            )
            script_text_to_footage_entries[script_text] = entries

    print("\n" + "=" * 70)
    print(
        f"[joint scenes] DONE — produced footage entries for "
        f"{len(script_text_to_footage_entries)} stage(s)"
    )
    print("=" * 70)
    return script_text_to_footage_entries


# ===========================================================================
# GENERATOR: READ-OUT (KINETIC TYPOGRAPHY) SCENES
# ===========================================================================


def generate_read_out_scenes(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],  # unused — registry signature uniformity
    final_data: list[dict] | None = None,  # unused — registry signature uniformity
) -> dict[str, list[dict]]:
    """
    Render a silent kinetic-typography MP4 for every scene flagged
    MediaType.READ_OUT, and return a stitcher-ready map of:

        { script_text: [ {local_path: trim_seconds} ], ... }

    Each read-out scene produces ONE entry — a single MP4 rendered slightly
    longer than the scene's runtime (for safe trimming) but reported to the
    stitcher with `trim_seconds = scene_runtime` so the cut lands cleanly.

    The rendered MP4 is silent — the stitcher overlays the global narration
    audio across all scenes.
    """
    print("\n" + "=" * 70)
    print("[read-out scenes] STARTING generate_read_out_scenes")
    print("=" * 70)

    if not READ_OUT_ENABLE:
        print("[read-out scenes] READ_OUT_ENABLE is False — skipping")
        return {}

    read_outs = [
        (txt, data)
        for txt, data in script_to_search_term.items()
        if data["search_type"] == MediaType.READ_OUT
    ]

    if not read_outs:
        print("[read-out scenes] no read-out scenes — returning empty map")
        return {}

    print(f"[read-out scenes] found {len(read_outs)} read-out scene(s)")

    # Inputs we need from the audio-sync stage.
    scene_timings = _load_scene_timings()  # text → duration
    line_starts = load_json(TIMESTAMPS_ABSOLUTE_FILE)  # text → abs start

    # Optional precise per-word timings (Whisper word-level).
    precise: dict | None = None
    if Path(WORD_TIMINGS_FILE).exists():
        try:
            precise = json.loads(Path(WORD_TIMINGS_FILE).read_text())
            n_covered = sum(1 for txt, _ in read_outs if precise.get(txt))
            print(
                f"[read-out scenes] loaded precise word timings "
                f"({n_covered}/{len(read_outs)} read-out lines covered)"
            )
        except Exception as exc:
            print(f"[read-out scenes] couldn't parse {WORD_TIMINGS_FILE}: {exc}")
            precise = None
    else:
        print(f"[read-out scenes] no {WORD_TIMINGS_FILE} — using syllable estimation")

    # One config shared by every read-out scene.
    cfg = WordRenderConfig()

    output_dir = Path(_CACHE_DIR) / "read_out_scenes"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[read-out scenes] output dir: {output_dir}")

    footage_map: dict[str, list[dict]] = {}

    for idx, (script_text, scene_data) in enumerate(read_outs):
        if script_text not in scene_timings:
            print(f"[read-out scenes] FATAL: no timing for '{script_text[:80]}'")
            sys.exit(1)

        duration = float(scene_timings[script_text])
        if duration <= 0:
            print(
                f"[read-out scenes] WARNING: scene has zero/negative duration "
                f"({duration}s) — skipping '{script_text[:60]}'"
            )
            continue

        line_start = float(line_starts.get(script_text, 0.0))
        per_line_words = (precise or {}).get(script_text)

        # Build a safe filename. Strip non-alphanumerics, prefix with idx.
        safe_stem = (
            re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:50] or "scene"
        )
        output_path = str(output_dir / f"read_out_{idx:03d}_{safe_stem}.mp4")

        # Render slightly longer than the scene runtime so the stitcher's
        # trim never falls short due to libx264 keyframe alignment. The
        # extra footage (last word stationary) is invisible after trim.
        render_duration = duration + READ_OUT_RENDER_SAFETY_PAD_SEC

        print(
            f"\n[read-out scenes] [{idx + 1}/{len(read_outs)}] "
            f"'{script_text[:60]}{'...' if len(script_text) > 60 else ''}'"
        )
        print(f"[read-out scenes]   scene duration   = {duration:.3f}s")
        print(
            f"[read-out scenes]   render duration  = {render_duration:.3f}s "
            f"(+{READ_OUT_RENDER_SAFETY_PAD_SEC:.3f}s safety pad)"
        )
        print(f"[read-out scenes]   line_start (abs) = {line_start:.3f}s")
        print(
            f"[read-out scenes]   precise words    = "
            f"{'yes (' + str(len(per_line_words)) + ' words)' if per_line_words else 'no'}"
        )
        print(f"[read-out scenes]   → {output_path}")

        try:
            render_scene_to_video(
                script_text=script_text,
                line_duration=render_duration,
                output_path=output_path,
                precise_word_timings=per_line_words,
                line_start_absolute=line_start,
                config=cfg,
            )
        except Exception as exc:
            print(f"[read-out scenes] FATAL: render failed: {exc}")
            sys.exit(1)

        # Report the ORIGINAL scene duration as trim. The extra pad gets
        # discarded by the stitcher.
        footage_map[script_text] = [{output_path: round(duration, 3)}]
        print(f"[read-out scenes]   ✓ done — stitcher trim = {round(duration, 3)}s")

    print("\n" + "=" * 70)
    print(f"[read-out scenes] DONE — produced {len(footage_map)} read-out scene(s)")
    print("=" * 70)
    return footage_map


def generate_map_scenes(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],  # unused — registry signature uniformity
    final_data: list[dict] | None = None,  # unused — registry signature uniformity
) -> dict[str, list[dict]]:
    """
    Render a highlighted map for every scene flagged MediaType.MAP and return a
    stitcher-ready map:

        { script_text: [ {local_mp4_path: trim_seconds} ], ... }

    The scene's `search_term` is treated as a PLACE NAME. GET_MAP geocodes it
    and decides what to draw:
      - a country   -> the whole world, with that country highlighted
      - a region    -> the parent country, with the region/state highlighted
      - a city/town -> the parent country, with a pin dropped on the place

    Each map renders to a PNG, then bakes into a STATIC MP4 (exactly like the
    text-overlay / manual-placement scenes) so the Ken Burns pass skips it and
    the composed map is never cropped. One entry per scene, trimmed to runtime.

    Like read-out scenes, map scenes need NO external candidates and NO review,
    so this generator is registered in LOCAL_FOOTAGE_GENERATORS and its output
    is appended into final_data by the generic merge step.
    """
    print("\n" + "=" * 70)
    print("[map scenes] STARTING generate_map_scenes")
    print("=" * 70)

    if not MAP_ENABLE:
        print("[map scenes] MAP_ENABLE is False — skipping")
        return {}

    map_scenes = [
        (txt, data)
        for txt, data in script_to_search_term.items()
        if data["search_type"] == MediaType.MAP
    ]
    if not map_scenes:
        print("[map scenes] no map scenes — returning empty map")
        return {}

    print(f"[map scenes] found {len(map_scenes)} map scene(s)")

    scene_timings = _load_scene_timings()

    output_dir = MAP_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[map scenes] output dir: {output_dir}")

    footage_map: dict[str, list[dict]] = {}

    for idx, (script_text, scene_data) in enumerate(map_scenes):
        place = scene_data["search_term"]

        if script_text not in scene_timings:
            print(f"[map scenes] FATAL: no timing for '{script_text[:80]}'")
            sys.exit(1)

        duration = float(scene_timings[script_text])
        if duration <= 0:
            print(
                f"[map scenes] WARNING: zero/negative duration "
                f"({duration}s) — skipping '{script_text[:60]}'"
            )
            continue

        safe_stem = (
            re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:50] or "scene"
        )
        png_path = str(output_dir / f"map_{idx:03d}_{safe_stem}.png")
        mp4_path = str(output_dir / f"map_{idx:03d}_{safe_stem}.mp4")

        print(
            f"\n[map scenes] [{idx + 1}/{len(map_scenes)}] "
            f"'{script_text[:60]}{'...' if len(script_text) > 60 else ''}'"
        )
        print(f"[map scenes]   place          = '{place}'")
        print(f"[map scenes]   scene duration = {duration:.3f}s")
        print(f"[map scenes]   -> {png_path}")

        rendered = get_map_image(place, png_path, cache_dir=MAP_GEOCODE_CACHE_DIR)
        if not rendered:
            print(
                f"[map scenes] FATAL: could not render a map for '{place}' "
                f"(scene '{script_text[:60]}')"
            )
            sys.exit(1)

        # Bake the still into a static MP4 so the Ken Burns pass skips it and
        # the composed map (whole-world highlight / pin) is never cropped.
        try:
            _render_image_to_static_mp4(rendered, duration, mp4_path)
        except Exception as exc:
            print(
                f"[map scenes] FATAL: static MP4 render failed for "
                f"'{script_text[:50]}': {exc}"
            )
            sys.exit(1)

        footage_map[script_text] = [{mp4_path: round(duration, 3)}]
        print(f"[map scenes]   ✓ done — stitcher trim = {round(duration, 3)}s")

    print("\n" + "=" * 70)
    print(f"[map scenes] DONE — produced {len(footage_map)} map scene(s)")
    print("=" * 70)
    return footage_map


def generate_stickman_explain_scenes(
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
) -> dict[str, list[dict]]:
    """
    For every scene whose search_type is in STICKMAN_EXPLAIN_TYPES, composite
    the clip the user CHOSE in review (stored in final_data) onto a randomly
    selected Einstein board base, and return a stitcher-ready map:

        { script_text: [ {local_mp4_path: trim_seconds} ], ... }

    Always renders an MP4 (even when the chosen footage is a still) so the
    board stays static under the later Ken Burns pass — Ken Burns only touches
    image entries, and these are already video.

    NOT in LOCAL_FOOTAGE_GENERATORS because it needs the post-review picks
    (final_data), not the raw candidates the registry generators receive.
    """
    print("\n" + "=" * 70)
    print("[explain scenes] STARTING generate_stickman_explain_scenes")
    print("=" * 70)

    explain_scenes = [
        (txt, data)
        for txt, data in script_to_search_term.items()
        if media_props(data["search_type"]).is_stickman_explain
    ]
    if not explain_scenes:
        print("[explain scenes] no explainer scenes — returning empty map")
        return {}

    print(f"[explain scenes] found {len(explain_scenes)} explainer scene(s)")

    # Lazy import keeps the PIL/ffmpeg-only dep out of runs that don't use it.
    from MAKE_EXPLAINER_IMAGE import make_explainer

    scene_timings = _load_scene_timings()

    # Map script_text -> the LOCAL PATH of the clip the user CHOSE in review.
    # IMPORTANT: unlike the joint compositor (which resolves/downloads URLs
    # itself further down), make_explainer needs an on-disk file — so we MUST
    # resolve the key (URL → local via history.json) right here.
    chosen_by_text: dict[str, str | None] = {}
    for entry in final_data or []:
        footage = entry.get("footage") or []
        key = next(iter(footage[0]), None) if footage else None
        chosen_by_text[entry["script_text"]] = (
            _resolve_to_local_path(key) if key else None
        )

    out_dir = STICKMAN_EXPLAIN_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[explain scenes] output dir: {out_dir}")

    footage_map: dict[str, list[dict]] = {}

    for idx, (script_text, _) in enumerate(explain_scenes):
        chosen = chosen_by_text.get(script_text)
        if not chosen:
            print(
                f"[explain scenes] FATAL: no chosen footage in final_data for "
                f"'{script_text[:70]}' — was it picked in review?"
            )
            sys.exit(1)

        if script_text not in scene_timings:
            print(f"[explain scenes] FATAL: no timing for '{script_text[:70]}'")
            sys.exit(1)

        duration = float(scene_timings[script_text])
        if duration <= 0:
            print(
                f"[explain scenes] WARNING: zero/negative duration "
                f"({duration}s) — skipping '{script_text[:60]}'"
            )
            continue

        render_duration = duration + STICKMAN_EXPLAIN_RENDER_SAFETY_PAD_SEC

        safe_stem = (
            re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:50] or "scene"
        )
        output_path = str(out_dir / f"explain_{idx:03d}_{safe_stem}.mp4")

        print(
            f"\n[explain scenes] [{idx + 1}/{len(explain_scenes)}] "
            f"'{script_text[:60]}{'...' if len(script_text) > 60 else ''}'"
        )
        print(f"[explain scenes]   base footage   = {chosen}")
        print(f"[explain scenes]   scene duration = {duration:.3f}s")
        print(f"[explain scenes]   render dur     = {render_duration:.3f}s")

        try:
            make_explainer(
                media_path=chosen,
                output_path=output_path,
                duration=render_duration,
            )
        except Exception as exc:
            print(f"[explain scenes] FATAL: explainer render failed: {exc}")
            sys.exit(1)

        # Report the ORIGINAL scene duration as trim; the safety pad is trimmed off.
        footage_map[script_text] = [{output_path: round(duration, 3)}]
        print(f"[explain scenes]   ✓ done — stitcher trim = {round(duration, 3)}s")

    print("\n" + "=" * 70)
    print(f"[explain scenes] DONE — produced {len(footage_map)} explainer scene(s)")
    print("=" * 70)
    return footage_map


def generate_text_overlay_scenes(
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
) -> dict[str, list[dict]]:
    """
    For every MediaType.STICKMAN_TEXT_OVERLAY scene, composite a tilted
    Fireship-style caption (the scene's search_term) onto the PREVIOUS scene's
    chosen image, returning a stitcher-ready map:

        { script_text: [ {local_mp4_path: trim_seconds} ], ... }

    "Previous image" = the nearest preceding scene that is NOT itself a
    text-overlay and whose final footage resolves to an image (or a video,
    whose first frame is used). Output is a STATIC MP4 so the Ken Burns pass
    skips it and the tilted caption is never cropped/zoomed.

    NOT in LOCAL_FOOTAGE_GENERATORS: it needs the post-review picks
    (final_data), not the raw candidates.
    """
    print("\n" + "=" * 70)
    print("[text-overlay] STARTING generate_text_overlay_scenes")
    print("=" * 70)

    overlay_scenes = [
        (txt, data)
        for txt, data in script_to_search_term.items()
        if media_props(data["search_type"]).is_text_overlay
    ]
    if not overlay_scenes:
        print("[text-overlay] no text-overlay scenes — returning empty map")
        return {}

    print(f"[text-overlay] found {len(overlay_scenes)} text-overlay scene(s)")

    from MAKE_TEXT_OVERLAY import make_text_overlay  # lazy import

    scene_timings = _load_scene_timings()
    ordered_texts = list(script_to_search_term.keys())
    final_by_text = {e["script_text"]: e for e in final_data}

    def _resolve_base_for(idx: int) -> str | None:
        """Nearest preceding non-overlay scene's resolved local image/video."""
        for j in range(idx - 1, -1, -1):
            prev_text = ordered_texts[j]
            if media_props(
                script_to_search_term[prev_text]["search_type"]
            ).is_text_overlay:
                continue  # skip other captions
            footage = (final_by_text.get(prev_text) or {}).get("footage") or []
            if not footage:
                continue
            key = next(iter(footage[0]), None)  # url or local path
            local = _resolve_to_local_path(key) if key else None
            if local:
                print(
                    f"[text-overlay]   base for '{ordered_texts[idx][:45]}' "
                    f"← '{prev_text[:45]}' ({Path(local).name})"
                )
                return local
        print(
            f"[text-overlay]   WARNING: no prior image for "
            f"'{ordered_texts[idx][:45]}' — using a plain background"
        )
        return None

    out_dir = STICKMAN_TEXT_OVERLAY_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    footage_map: dict[str, list[dict]] = {}
    for txt, data in overlay_scenes:
        idx = ordered_texts.index(txt)
        base_local = _resolve_base_for(idx)

        if txt not in scene_timings:
            print(f"[text-overlay] FATAL: no timing for '{txt[:60]}'")
            sys.exit(1)
        duration = float(scene_timings[txt])
        if duration <= 0:
            print(
                f"[text-overlay] WARNING: zero/negative duration — skipping "
                f"'{txt[:60]}'"
            )
            continue

        render_duration = duration + STICKMAN_TEXT_OVERLAY_RENDER_SAFETY_PAD_SEC
        safe_stem = re.sub(r"[^a-zA-Z0-9]+", "_", txt).strip("_")[:50] or "scene"
        output_path = str(out_dir / f"text_overlay_{idx:03d}_{safe_stem}.mp4")

        try:
            make_text_overlay(
                base_image_path=base_local or "",
                text=data["search_term"],  # the caption text
                output_path=output_path,
                duration=render_duration,
                seed=txt,  # deterministic position/tilt per scene
            )
        except Exception as exc:
            print(f"[text-overlay] FATAL: render failed for '{txt[:50]}': {exc}")
            sys.exit(1)

        footage_map[txt] = [{output_path: round(duration, 3)}]
        print(
            f"[text-overlay]   ✓ '{txt[:50]}' → {Path(output_path).name} "
            f"(trim {round(duration, 3)}s)"
        )

    print("\n" + "=" * 70)
    print(f"[text-overlay] DONE — produced {len(footage_map)} scene(s)")
    print("=" * 70)
    return footage_map
# ===========================================================================
# GENERATOR REGISTRY
# ===========================================================================
# Every entry here is a "local file generator": given the script and (for
# types that use them) external candidates, it writes one or more MP4/image
# files to disk and returns a {script_text → [{path: trim_seconds}, ...]}
# map. The merge helpers above are completely generator-agnostic — they
# integrate any such map into final_data + history.json identically.
#
# Note: a single generator can handle multiple MediaTypes — generate_joint_scenes
# already does, dispatching internally based on which types are in JOINT_TYPES.

LOCAL_FOOTAGE_GENERATORS: dict[
    str,
    Callable[
        [dict[str, SearchTermData], list[dict]],
        dict[str, list[dict]],
    ],
] = {
    "joint": generate_joint_scenes,  # handles every type in JOINT_TYPES
    "read_out": generate_read_out_scenes,  # handles MediaType.READ_OUT
    "map": generate_map_scenes,  # handles MediaType.MAP
}


def run_all_local_generators(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],
    final_data: list[dict] | None = None,
) -> dict[str, list[dict]]:
    """
    Invoke every registered generator and merge their outputs into a single
    {script_text → footage_entries} map.

    Generators are run in registry order. If two generators produce entries
    for the same script_text (shouldn't happen with sensible config), the
    later one wins and a warning is printed.
    """
    print("\n" + "=" * 70)
    print(f"[generators] running {len(LOCAL_FOOTAGE_GENERATORS)} local generator(s)")
    print("=" * 70)

    combined: dict[str, list[dict]] = {}
    for name, generator in LOCAL_FOOTAGE_GENERATORS.items():
        print(f"\n[generators] → {name}: {generator.__name__}")
        try:
            produced = generator(script_to_search_term, candidates_data, final_data)
        except Exception as exc:
            print(f"[generators] FATAL: {generator.__name__} raised: {exc}")
            raise

        for script_text in produced:
            if script_text in combined:
                print(
                    f"[generators] WARNING: '{script_text[:60]}' already produced by "
                    f"another generator — overwriting with {name}"
                )
        combined.update(produced)
        print(f"[generators] ← {name} produced {len(produced)} entry(ies)")

    print(f"\n[generators] all generators done — {len(combined)} total entry(ies)")
    return combined
