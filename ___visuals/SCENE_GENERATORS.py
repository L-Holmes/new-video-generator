"""
Locally-rendered scene generators (no external candidates, no review):
  - generate_joint_scenes      — grouped scenes (the `group` modifier) as
                                 multi-image collage MP4s (joint compositor)
  - generate_read_out_scenes   — kinetic typography (media_type "typography")
  - generate_map_scenes        — highlighted place maps
  - generate_blank_scenes      — an empty canvas: a flat colour ("blank") or a
                                 backdrop from _BACKGROUNDS/ ("random_background")
  - generate_maths_scenes      — manim animations of row["data"] ("timeline")
  - generate_stickman_explain_scenes — chosen stock/wiki clip on a board base

plus the LOCAL_FOOTAGE_GENERATORS registry and run_all_local_generators that
drives the registry-based ones. The explainer generator is invoked directly
by main() (it runs after review), not via the registry. Captions live in the
decorate editor now (DECORATE_STAGE) — the old text-overlay generator is gone.

MAKE_EXPLAINER_IMAGE is imported lazily inside its generator.
"""

from __future__ import annotations

# Allow running this file directly from the repo root (uv run ___visuals/SCENE_GENERATORS.py).
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import random
import re
import sys
from pathlib import Path
from typing import Callable

from ___visuals.CACHE_IO import _resolve_to_local_path, load_json
from CONFIG import (
    _CACHE_DIR,
    BLANK_SCENE_COLOUR,
    BLANK_SCENE_OUTPUT_DIR,
    BLANK_SCENE_RESOLUTION,
    CANDIDATES_CACHE_FILE,
    JOINT_BASE_DURATION_FALLBACK_SEC,
    JOINT_LAYOUT_POSITIONS,
    MAP_ENABLE,
    MAP_GEOCODE_CACHE_DIR,
    MAP_OUTPUT_DIR,
    MATHS_SCENE_OUTPUT_DIR,
    RANDOM_BACKGROUND_DIR,
    RANDOM_BACKGROUND_SEED,
    TYPOGRAPHY_ENABLE,
    TYPOGRAPHY_RENDER_SAFETY_PAD_SEC,
    STICKMAN_EXPLAIN_OUTPUT_DIR,
    STICKMAN_EXPLAIN_RENDER_SAFETY_PAD_SEC,
    TIMESTAMPS_ABSOLUTE_FILE,
    WORD_TIMINGS_FILE,
    GROUPABLE_TYPES,
    MediaType,
    SearchTermData,
    group_scene_rows,
    media_props,
    scene_data,
    scene_is_grouped,
)
from ___visuals.DOWNLOADS import _download_image
from ___visuals.GET_MAP import get_map_image
from ___visuals.JOINT_IMAGE_CREATOR import TRANSITION_RANDOM
from ___visuals.JOINT_IMAGE_CREATOR import composite as create_joint_scene
from ___visuals.JOINT_IMAGE_CREATOR import is_animated, is_image
from ___visuals.STATIC_RENDER import (
    _render_image_to_static_mp4,
    render_background_to_mp4,
    render_solid_colour_mp4,
)
from ___visuals.TIMING_MERGE import (
    _build_footage_entries_for_stage,
    _compute_joint_stage_timing,
    _load_scene_timings,
)
from ___visuals.WORDS_ON_SCREEN import WordRenderConfig, render_scene_to_video


# === BEGIN verbatim move from main.py (scene generators) ===
def generate_joint_scenes(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],
    final_data: list[dict] | None = None,
) -> dict[str, list[dict]]:
    """
    Build joint composite scenes for every GROUPED scene (rows carrying the
    `group` modifier — a group OF stock, a group OF ai_stock), returning a
    stitcher-ready map of:

        { script_text: [ {local_path: trim_seconds}, ... ], ... }

    Each joint stage typically contributes TWO entries (intro + loop). Very
    short scenes get just one entry — the loop file alone.

    Adjacent scenes sharing the SAME base media_type AND the SAME group_id
    (assigned by the tagging tool) are one group = one composite render.
    """

    print("\n" + "=" * 70)
    print("[joint scenes] STARTING generate_joint_scenes")
    print(
        f"[joint scenes] script_to_search_term has {len(script_to_search_term)} entries"
    )
    print(f"[joint scenes] candidates_data has {len(candidates_data)} entries")
    print(f"[joint scenes] groupable base types: {sorted(GROUPABLE_TYPES)}")
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

    # 1) Locate all GROUPED scenes (the `group` modifier on a groupable base).
    joint_scenes: list[tuple[str, SearchTermData]] = []
    for script_text, scene_data in script_to_search_term.items():
        if not scene_is_grouped(scene_data):
            continue
        joint_scenes.append((script_text, scene_data))

    print(f"\n[joint scenes] found {len(joint_scenes)} joint scene(s)")
    if not joint_scenes:
        print("[joint scenes] no joint scenes — returning empty map")
        return {}

    # Sort by SCRIPT ORDER (dict insertion order of the search-term file).
    # NOT by the position field: positions restart at 1 for every group, so
    # with two or more groups a position sort would interleave them.
    _script_order = {txt: i for i, txt in enumerate(script_to_search_term)}
    joint_scenes.sort(key=lambda scene: _script_order.get(scene[0], 1_000_000))
    for i, (txt, data) in enumerate(joint_scenes):
        print(
            f"[joint scenes]   sorted[{i}]: pos={data['position']}, "
            f"group_id={data.get('group_id')}, "
            f"type={data['media_type'].value}, script='{txt[:60]}...'"
        )

    # 2) Group consecutive joints. New rows group by shared group_id (set by
    #    the tagging tool's 'group' modifier); old rows fall back to the
    #    contiguous-position rule. Logic lives in CONFIG.group_scene_rows.
    grouped_joint_scenes = group_scene_rows(joint_scenes)

    print(f"\n[joint scenes] formed {len(grouped_joint_scenes)} group(s)")
    for gi, grp in enumerate(grouped_joint_scenes):
        positions = [s[1]["position"] for s in grp]
        joint_type = grp[0][1]["media_type"]
        print(
            f"[joint scenes]   group {gi}: type={joint_type.value}, "
            f"positions={positions}, size={len(grp)}"
        )

    # 3) Generate each group + collect footage entries.
    script_text_to_footage_entries: dict[str, list[dict]] = {}

    for group_index, group in enumerate(grouped_joint_scenes):
        joint_type = group[0][1]["media_type"]  # the group's BASE type
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
            case MediaType.STOCK:
                box_percentage = 50
                transition = TRANSITION_RANDOM
                background_path = "_BACKGROUNDS/bg_crumpled_card.mp4"
                base_duration = JOINT_BASE_DURATION_FALLBACK_SEC
                remove_bg = True

            case MediaType.AI_STOCK:
                # Identical to a stock group; the ONLY difference is the tiles
                # come from the AI stickman generator instead of Pexels. The
                # stickman images are line art on forced-white backgrounds, so
                # remove_bg cuts the figure out onto the crumpled card. Flip to
                # False if you ever want the white kept.
                box_percentage = 50
                transition = TRANSITION_RANDOM
                background_path = "_BACKGROUNDS/bg_crumpled_card.mp4"
                base_duration = JOINT_BASE_DURATION_FALLBACK_SEC
                remove_bg = True

            case _:
                print(f"[joint scenes] FATAL: unsupported group base type: {joint_type}")
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
                        f"  this scene had a different media_type) pointed elsewhere."
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
    Render a silent kinetic-typography MP4 for every MediaType.TYPOGRAPHY
    scene, and return a stitcher-ready map of:

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

    if not TYPOGRAPHY_ENABLE:
        print("[read-out scenes] TYPOGRAPHY_ENABLE is False — skipping")
        return {}

    read_outs = [
        (txt, data)
        for txt, data in script_to_search_term.items()
        if data["media_type"] == MediaType.TYPOGRAPHY
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
        render_duration = duration + TYPOGRAPHY_RENDER_SAFETY_PAD_SEC

        print(
            f"\n[read-out scenes] [{idx + 1}/{len(read_outs)}] "
            f"'{script_text[:60]}{'...' if len(script_text) > 60 else ''}'"
        )
        print(f"[read-out scenes]   scene duration   = {duration:.3f}s")
        print(
            f"[read-out scenes]   render duration  = {render_duration:.3f}s "
            f"(+{TYPOGRAPHY_RENDER_SAFETY_PAD_SEC:.3f}s safety pad)"
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
    Render a highlighted map for every MediaType.MAP scene and return a
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
        if data["media_type"] == MediaType.MAP
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


# ===========================================================================
# GENERATOR: BLANK / RANDOM-BACKGROUND SCENES
# ===========================================================================


def _pick_random_background(script_text: str) -> str:
    """A background from RANDOM_BACKGROUND_DIR, chosen at random but SEEDED on
    the scene's own text — so the same line keeps the same backdrop across
    re-runs (bump RANDOM_BACKGROUND_SEED to reshuffle them all)."""
    root = Path(RANDOM_BACKGROUND_DIR)
    if not root.is_dir():
        raise RuntimeError(
            f"backgrounds folder not found: {RANDOM_BACKGROUND_DIR} "
            f"(cwd={Path.cwd()})"
        )
    choices = sorted(
        str(p)
        for p in root.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and (is_image(p.name) or is_animated(p.name))
    )
    if not choices:
        raise RuntimeError(
            f"no usable backgrounds in {RANDOM_BACKGROUND_DIR} — expected "
            f"images or videos, found: {sorted(p.name for p in root.iterdir())}"
        )
    seed = f"{RANDOM_BACKGROUND_SEED}:{script_text}"
    return random.Random(seed).choice(choices)


def generate_blank_scenes(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],  # unused — registry signature uniformity
    final_data: list[dict] | None = None,  # unused — registry signature uniformity
) -> dict[str, list[dict]]:
    """
    Fill the frame for every BLANK and RANDOM_BACKGROUND scene, returning a
    stitcher-ready map of:

        { script_text: [ {local_mp4_path: trim_seconds} ], ... }

    blank             → a flat BLANK_SCENE_COLOUR fill (white by default).
    random_background → one file from RANDOM_BACKGROUND_DIR, scaled to cover
                        the frame; a video loops, a still is held.

    Both bake to an MP4 so the Ken Burns pass leaves them alone — a blank
    canvas that slowly zoomed would defeat the point. Neither needs external
    candidates nor review, so this is a registry generator like map /
    read-out: main()'s generic merge folds its output into final_data, and a
    stacked `decorate` / `caption` then runs on top of the canvas it made.
    """
    print("\n" + "=" * 70)
    print("[blank scenes] STARTING generate_blank_scenes")
    print("=" * 70)

    scenes = [
        (txt, data)
        for txt, data in script_to_search_term.items()
        if data["media_type"] in (MediaType.BLANK, MediaType.RANDOM_BACKGROUND)
    ]
    if not scenes:
        print("[blank scenes] no blank / random-background scenes — returning empty map")
        return {}

    print(f"[blank scenes] found {len(scenes)} scene(s)")

    scene_timings = _load_scene_timings()

    output_dir = BLANK_SCENE_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[blank scenes] output dir: {output_dir}")

    footage_map: dict[str, list[dict]] = {}

    for idx, (script_text, scene_data) in enumerate(scenes):
        media_type = scene_data["media_type"]

        if script_text not in scene_timings:
            print(f"[blank scenes] FATAL: no timing for '{script_text[:80]}'")
            sys.exit(1)

        duration = float(scene_timings[script_text])
        if duration <= 0:
            print(
                f"[blank scenes] WARNING: zero/negative duration "
                f"({duration}s) — skipping '{script_text[:60]}'"
            )
            continue

        safe_stem = (
            re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:50] or "scene"
        )
        mp4_path = str(output_dir / f"{media_type.value}_{idx:03d}_{safe_stem}.mp4")

        print(
            f"\n[blank scenes] [{idx + 1}/{len(scenes)}] "
            f"'{script_text[:60]}{'...' if len(script_text) > 60 else ''}'"
        )
        print(f"[blank scenes]   type           = {media_type.value}")
        print(f"[blank scenes]   scene duration = {duration:.3f}s")
        print(f"[blank scenes]   -> {mp4_path}")

        try:
            if media_type == MediaType.BLANK:
                render_solid_colour_mp4(
                    BLANK_SCENE_COLOUR, duration, mp4_path, BLANK_SCENE_RESOLUTION
                )
            else:
                render_background_to_mp4(
                    _pick_random_background(script_text),
                    duration,
                    mp4_path,
                    BLANK_SCENE_RESOLUTION,
                )
        except Exception as exc:
            print(
                f"[blank scenes] FATAL: {media_type.value} render failed for "
                f"'{script_text[:50]}': {exc}"
            )
            sys.exit(1)

        footage_map[script_text] = [{mp4_path: round(duration, 3)}]
        print(f"[blank scenes]   ✓ done — stitcher trim = {round(duration, 3)}s")

    print("\n" + "=" * 70)
    print(f"[blank scenes] DONE — produced {len(footage_map)} scene(s)")
    print("=" * 70)
    return footage_map


# ===========================================================================
# GENERATOR: MATHS SCENES  (manim animations of row["data"])
# ===========================================================================
# A maths animation has a FIXED length; the scene it must fill does not. So
# every maths renderer produces a pair — the transition mp4, and its final
# frame as a still — and this generator chooses between them per scene:
#
#   scene SHORTER than the transition -> the finished still, full scene length.
#       (playing the animation would cut it off mid-journey, which reads as a
#        glitch rather than as a timeline.)
#   scene LONGER  than the transition -> the transition, then the still held
#       for whatever runtime is left. Two footage entries, played back to back,
#       exactly as a joint scene plays its intro then its loop.
#
# This is the house pattern for fixed-length animations — see AI_READ_THIS.txt.


def _maths_render_timeline(data: dict, stem: str) -> tuple[str, str, float]:
    """Render a timeline. Returns (transition_mp4, final_png, transition_secs)."""
    from ___visuals.maths import render_timeline
    from ___visuals.maths._runner import probe_duration
    from ___visuals.maths.timeline import current_year

    out_dir = Path(MATHS_SCENE_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    year = int(data["year"])
    # Keyed on the render's INPUTS, not the scene: two lines travelling back to
    # the same year share one manim render (which takes tens of seconds). The
    # start year is in the key because it comes from the clock — a run in
    # January of the next year must not reuse December's line.
    key = f"timeline_{current_year()}_to_{year}"
    mp4, png = out_dir / f"{key}.mp4", out_dir / f"{key}.png"
    render_timeline(year, str(mp4), str(png))
    return str(mp4), str(png), probe_duration(str(mp4))


# media_type -> renderer. A new maths type lands here and nowhere else in this
# file; the duration logic below is shared by all of them.
_MATHS_RENDERERS: dict[MediaType, "Callable[[dict, str], tuple[str, str, float]]"] = {
    MediaType.TIMELINE: _maths_render_timeline,
}


def generate_maths_scenes(
    script_to_search_term: dict[str, SearchTermData],
    candidates_data: list[dict],  # unused — registry signature uniformity
    final_data: list[dict] | None = None,  # unused — registry signature uniformity
) -> dict[str, list[dict]]:
    """
    Render every MATHS scene (timeline today; charts later) and return a
    stitcher-ready map of:

        { script_text: [ {local_mp4_path: trim_seconds}, ... ], ... }

    One entry when the scene is too short for the animation, two when it plays
    the animation and then holds the finished frame. Needs no candidates and no
    review, so this is a registry generator like map / blank.
    """
    print("\n" + "=" * 70)
    print("[maths scenes] STARTING generate_maths_scenes")
    print("=" * 70)

    scenes = [
        (txt, data)
        for txt, data in script_to_search_term.items()
        if data["media_type"] in _MATHS_RENDERERS
    ]
    if not scenes:
        print("[maths scenes] no maths scenes — returning empty map")
        return {}

    print(f"[maths scenes] found {len(scenes)} maths scene(s)")

    scene_timings = _load_scene_timings()
    out_dir = Path(MATHS_SCENE_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    footage_map: dict[str, list[dict]] = {}

    for idx, (script_text, row) in enumerate(scenes):
        media_type = row["media_type"]

        if script_text not in scene_timings:
            print(f"[maths scenes] FATAL: no timing for '{script_text[:80]}'")
            sys.exit(1)
        duration = float(scene_timings[script_text])
        if duration <= 0:
            print(
                f"[maths scenes] WARNING: zero/negative duration "
                f"({duration}s) — skipping '{script_text[:60]}'"
            )
            continue

        safe_stem = (
            re.sub(r"[^a-zA-Z0-9]+", "_", script_text).strip("_")[:50] or "scene"
        )
        print(
            f"\n[maths scenes] [{idx + 1}/{len(scenes)}] "
            f"'{script_text[:60]}{'...' if len(script_text) > 60 else ''}'"
        )
        print(f"[maths scenes]   type           = {media_type.value}")
        print(f"[maths scenes]   data           = {scene_data(row)}")
        print(f"[maths scenes]   scene duration = {duration:.3f}s")

        try:
            transition_mp4, final_png, transition = _MATHS_RENDERERS[media_type](
                scene_data(row), safe_stem
            )
        except Exception as exc:
            print(
                f"[maths scenes] FATAL: {media_type.value} render failed for "
                f"'{script_text[:50]}': {exc}"
            )
            sys.exit(1)

        print(f"[maths scenes]   transition     = {transition:.3f}s")

        if duration <= transition:
            # No room for the journey — show where it ends up, for the whole
            # scene. Never a clipped animation.
            still_mp4 = str(out_dir / f"{media_type.value}_{idx:03d}_{safe_stem}.mp4")
            _render_image_to_static_mp4(final_png, duration, still_mp4)
            footage_map[script_text] = [{still_mp4: round(duration, 3)}]
            print(
                f"[maths scenes]   ✓ scene is shorter than the animation — "
                f"finished still only, {duration:.3f}s"
            )
            continue

        hold = duration - transition
        hold_mp4 = str(out_dir / f"{media_type.value}_{idx:03d}_{safe_stem}_hold.mp4")
        _render_image_to_static_mp4(final_png, hold, hold_mp4)
        footage_map[script_text] = [
            {transition_mp4: round(transition, 3)},
            {hold_mp4: round(hold, 3)},
        ]
        print(
            f"[maths scenes]   ✓ animation {transition:.3f}s "
            f"then hold {hold:.3f}s"
        )

    print("\n" + "=" * 70)
    print(f"[maths scenes] DONE — produced {len(footage_map)} maths scene(s)")
    print("=" * 70)
    return footage_map


def generate_stickman_explain_scenes(
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
) -> dict[str, list[dict]]:
    """
    For every on-board scene (stock_on_board / wikipedia_on_board), composite
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
        if media_props(data["media_type"]).is_on_board
    ]
    if not explain_scenes:
        print("[explain scenes] no explainer scenes — returning empty map")
        return {}

    print(f"[explain scenes] found {len(explain_scenes)} explainer scene(s)")

    # Lazy import keeps the PIL/ffmpeg-only dep out of runs that don't use it.
    from ___visuals.MAKE_EXPLAINER_IMAGE import make_explainer

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


# ===========================================================================
# GENERATOR REGISTRY
# ===========================================================================
# Every entry here is a "local file generator": given the script and (for
# types that use them) external candidates, it writes one or more MP4/image
# files to disk and returns a {script_text → [{path: trim_seconds}, ...]}
# map. The merge helpers above are completely generator-agnostic — they
# integrate any such map into final_data + history.json identically.
#
# Note: a single generator can handle multiple bases — generate_joint_scenes
# dispatches internally on a group's base type (stock vs ai_stock).

LOCAL_FOOTAGE_GENERATORS: dict[
    str,
    Callable[
        [dict[str, SearchTermData], list[dict]],
        dict[str, list[dict]],
    ],
] = {
    "group": generate_joint_scenes,  # every grouped run (stock / ai_stock)
    "typography": generate_read_out_scenes,  # MediaType.TYPOGRAPHY
    "map": generate_map_scenes,  # MediaType.MAP
    "blank": generate_blank_scenes,  # MediaType.BLANK + RANDOM_BACKGROUND
    "maths": generate_maths_scenes,  # MediaType.TIMELINE (+ future charts)
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
