"""
Pipeline orchestrator.

All the heavy lifting now lives in focused modules — main() just wires the
stages together in order, like a bash script. Comment out a stage to resume
from a checkpoint.

  CONFIG               constants, paths, MediaType, sessions, ProgressTracker
  CACHE_IO             cache/JSON I/O, history index, path resolution helpers
  DOWNLOADS            Pexels/Wikipedia metadata + downloads + load_stock_footage
  TIMING_MERGE         scene timings, joint timing, footage merge + integrate
  AI_GENERATION        stickman / stickman-joint / ai_edit generation + review
  SCENE_GENERATORS     joint / read-out / map / explainer / text-overlay
  STATIC_RENDER        frame extraction, still→MP4, manual stock placement stage
  KEN_BURNS            Ken Burns pan/zoom render pass
  PIXELLATE_STAGE      retro pixellation of AI stills
  COLOUR_GRADE_STAGE   cinematic colour grade pass
  AUDIO_EVENTS         per-scene SFX + music map
"""

from __future__ import annotations

import gc
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import ollama

from AI_GENERATION import (
    _regenerate_stickman_joint_scene,
    _regenerate_stickman_scene,
    generate_stickman_candidates,
    generate_stickman_joint_candidates,
    run_ai_edit_stage,
)
from AUDIO_EVENTS import build_audio_events_map
from CACHE_IO import (
    add_path_remap_to_history,
    load_from_cache,
    load_json,
    save_to_cache,
)
from COLOUR_GRADE_STAGE import apply_colour_grading_to_final_data
from CONFIG import (
    _CACHE_DIR,
    AUDIO_CUTDOWN_WHISPER_MODEL,
    AUDIO_EVENTS_FILE,
    AUDIO_START_DELAY_SECONDS,
    CANDIDATES_CACHE_FILE,
    FINAL_SCRIPT_AND_CLIPS,
    FORCE_AUDIO_CUTDOWN,
    HISTORY_FILE,
    LINE_INDEX_TO_SEARCH_TERM_FILE,
    MUSIC_VOLUME,
    OUTPUT_FILE,
    PROCESSED_AUDIO_DIR,
    RAW_SCRIPT_AUDIO_FILE,
    REVIEW_STOCK_FOOTAGE_OUTPUT_FILE,
    SCRIPT_AUDIO_FILE,
    SCRIPT_FILE,
    SFX_VOLUME,
    SYNCHRONIZED_SCRIPT_OUTPUT_FILE,
    media_props,
    TIMESTAMPS_ABSOLUTE_FILE,
    MediaType,
    SearchTermData,
)
from DOWNLOADS import load_stock_footage
from KEN_BURNS import apply_ken_burns_to_final_data
from PIXELLATE_STAGE import pixellate_candidate_bundles
from SCENE_GENERATORS import (
    generate_stickman_explain_scenes,
    generate_text_overlay_scenes,
    run_all_local_generators,
)
from STATIC_RENDER import run_manual_image_stage
from OBJECT_GENERATE_STAGE import run_object_generate_stage
from TIMING_MERGE import integrate_generated_footage

# ===========================================================================
# IMPORTS - LOCAL (external pipeline stages driven directly by main)
# ===========================================================================
from AUDIO_SCRIPT_SYNCHRONIZER import run as run_audio_script_synchronizer
from SCRIPT_AUDIO_CUTDOWN_AND_PROCESS import run as run_audio_cutdown
from STITCH_TOGETHER import stitch_together_video
from STOCK_FOOTAGE_REVIEW import run_media_review

print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
print("running main")
print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")


# ===========================================================================
# MISC
# ===========================================================================


def get_script_text_to_stock_footage_search(
    scene_lines: list[str],
) -> dict[str, SearchTermData]:
    """
    Returns
    -------
    dict[str, str]
        { original_narration_line: pexels_search_term }
    """

    result: dict[str, str] = {}

    return result


def additional_steps_save_for_later():
    # Custom images, Ken Burns effects, etc.
    pass


def verify_environment():
    pass


def split_text_into_sections(section):
    lines = section.split("\n")
    cleaned = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r"^#+\s", line):
            continue
        cleaned.append(line)

    return cleaned


def _dump_final(final_data: list[dict], title: str) -> None:
    """Pretty-print the final_data clip map (used after the merge + KB passes)."""
    print(f"\n=== FINAL SCRIPT → MEDIA ({title}) ===")
    for entry in final_data:
        print(f"\nSCRIPT: {entry['script_text']}")
        for item in entry["footage"]:
            for path_or_url, trim in item.items():
                label = Path(path_or_url).name if "/" in path_or_url else path_or_url
                print(f"  ✓ {label}  (trim: {trim}s)")
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")


# ===========================================================================
# MAIN  –  ORCHESTRATOR
# ===========================================================================
def main() -> None:
    """
    Runs the full pipeline from raw script to finished video.
    Each stage is a clearly labelled block – treat this like a bash script.
    Comment out any stage to resume from a checkpoint.
    """

    verify_environment()

    # 1) Break into scenes / load search-term map
    print("====================================================================")
    print("Breaking into scenes...")
    scriptTextToPexelSearch: dict[str, SearchTermData] = load_json(
        LINE_INDEX_TO_SEARCH_TERM_FILE
    )
    # Convert string search_type to MediaType enum. (Flat schema — no
    # variant field any more; the type encodes everything.)
    for key, value in scriptTextToPexelSearch.items():
        try:
            value["search_type"] = MediaType(value["search_type"])
        except ValueError:
            valid = ", ".join(t.value for t in MediaType)
            print(
                f"ERROR: unknown search_type {value['search_type']!r} "
                f"on scene '{key[:60]}'"
            )
            print(f"       valid values: {valid}")
            sys.exit(1)
    print("!!!!!!script text to pexel search:")
    print(scriptTextToPexelSearch)

    # 1.4) Tighten the narration BEFORE anything time-based runs. The cutdown
    #      removes dead-air + adds the sentence transitions, writing
    #      PROCESSED_AUDIO_DIR/<stem>.processed.wav. Because SCRIPT_AUDIO_FILE
    #      points there, the synchroniser (1.5) and the final stitch both run
    #      on the tightened audio, so the timings and the baked-in narration match.
    print("====================================================================")
    print("Tightening narration audio (silence cutdown + sentence transitions)...")
    if not Path(RAW_SCRIPT_AUDIO_FILE).exists():
        print(f"ERROR! raw narration not found: {RAW_SCRIPT_AUDIO_FILE}")
        sys.exit(1)
    processed_wav, _ = run_audio_cutdown(
        audio_file=RAW_SCRIPT_AUDIO_FILE,
        script_file=SCRIPT_FILE,
        output_dir=PROCESSED_AUDIO_DIR,
        whisper_model=AUDIO_CUTDOWN_WHISPER_MODEL,
        force=FORCE_AUDIO_CUTDOWN,
    )
    if os.path.normpath(str(processed_wav)) != os.path.normpath(SCRIPT_AUDIO_FILE):
        print(
            f"  ! cutdown wrote {processed_wav}, but the pipeline expects "
            f"{SCRIPT_AUDIO_FILE} — check PROCESSED_AUDIO_DIR / the stem."
        )
        sys.exit(1)
    print(f"  ✓ pipeline narration: {SCRIPT_AUDIO_FILE}")

    # 1.5) Audio synchronisation — produces line timings + (optionally) per-word timings
    run_audio_script_synchronizer(
        SCRIPT_AUDIO_FILE,
        LINE_INDEX_TO_SEARCH_TERM_FILE,
        SYNCHRONIZED_SCRIPT_OUTPUT_FILE,
        TIMESTAMPS_ABSOLUTE_FILE,
        AUDIO_START_DELAY_SECONDS,
    )

    # 2) Fetch external candidates (Pexels + Wikipedia) — only for types in
    #    NEEDS_EXTERNAL_CANDIDATES. Other types are produced purely locally.
    print("====================================================================")
    print("Loading stock footage candidates...")

    candidates_data = load_from_cache(CANDIDATES_CACHE_FILE)
    if candidates_data:
        print(f"✅ Loaded {len(candidates_data)} candidate bundle(s) from cache.")
    else:
        print("🔍 Cache miss. Fetching candidates...")

        # External candidates (Pexels videos+images / Wikipedia stills).
        candidates_data = load_stock_footage(scriptTextToPexelSearch)

        # AI-generated stickman candidates (STICKMAN_NUM_VARIANTS images each),
        # reviewed by the SAME GUI alongside the stock candidates.
        stickman_candidates = generate_stickman_candidates(scriptTextToPexelSearch)
        if stickman_candidates:
            candidates_data.extend(stickman_candidates)
            print(
                f"[main] added {len(stickman_candidates)} stickman candidate "
                f"bundle(s) to the review set"
            )

        # AI stickman tiles for stickman_joint scenes — same downstream flow as
        # JOINT_3_ROW (these bundles feed the joint compositor) but the tiles
        # are AI renders, not Pexels stills.
        stickman_joint_candidates = generate_stickman_joint_candidates(
            scriptTextToPexelSearch
        )
        if stickman_joint_candidates:
            candidates_data.extend(stickman_joint_candidates)
            print(
                f"[main] added {len(stickman_joint_candidates)} stickman-joint "
                f"candidate bundle(s) to the review set"
            )

        # Stickman bundles are appended, so re-sort the review list into SCRIPT
        # order. We use each scene's position in the search-term file (its dict
        # insertion order) rather than the `position` FIELD — that field isn't
        # reliably unique/correct, whereas the file is written top-to-bottom in
        # script order. Safe: the review GUI keys its state by script_text.
        _script_order = {txt: i for i, txt in enumerate(scriptTextToPexelSearch)}
        candidates_data.sort(
            key=lambda b: _script_order.get(b["script_text"], 1_000_000)
        )

        save_to_cache(candidates_data, CANDIDATES_CACHE_FILE)
        print(
            f"💾 Cached {len(candidates_data)} candidate bundle(s) to {CANDIDATES_CACHE_FILE}."
        )

    # ── Pixellate AI candidates BEFORE review ────────────────────────────
    # In-memory, AFTER the cache load/fetch (so it applies on cache hits too)
    # and BEFORE non_edit_candidates is built. The candidate cache stays raw,
    # so this is cheap to re-apply and never bakes pixels into the cache. Only
    # stickman / stickman_joint are in candidates_data here; ai_edit gets
    # pixellated later, as each edit is generated (build_ai_edit_candidates…).
    pixellate_candidate_bundles(candidates_data, scriptTextToPexelSearch)

    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
    print("\n=== SCRIPT → CANDIDATE MEDIA ===")
    for entry in candidates_data:
        print(f"\nSCRIPT: {entry['script_text']}")
        print(
            f"  needs {entry.get('num_clips_needed', 1)} clip(s), "
            f"each ≤ {entry.get('max_runtime_per_clip_seconds', 0):.2f}s"
        )
        cands = entry.get("candidates", {}) or {}
        print("  VIDEOS:")
        for item in cands.get("videos", []):
            for url, trim in item.items():
                print(f"    - {url}  (trim: {trim}s)")
        print("  IMAGES:")
        for item in cands.get("images", []):
            for url, trim in item.items():
                print(f"    - {url}  (trim: {trim}s)")

    # 2.5) STAGE 1 review — everything EXCEPT (things that don't need reviewing...)
    print("====================================================================")
    print("Launching media review GUI (stage 1: stock / wiki / joint / stickman)...")
    # Exclude from the stage-1 review GUI:
    #   - ai_edit: reviewed later in stage 2 (needs the stage-1 picks first)
    #   - stickman_joint: the joint compositor always uses candidate [0] and
    #     IGNORES the review pick, so reviewing these is pointless — and the
    #     review's cleanup would delete the "unchosen" local AI tiles, which
    #     (unlike Pexels URLs) can't be re-downloaded. They re-enter final_data
    #     via the generator-merge step (2.6) afterwards.
    _excluded_from_review = {
        MediaType.AI_EDIT,
        MediaType.ZOOM_PREV_IMG,
        MediaType.STATIC_OF_PREVIOUS,
        MediaType.DECORATE_PREVIOUS,
    }
    non_edit_candidates = [
        c
        for c in candidates_data
        if scriptTextToPexelSearch.get(c["script_text"], {}).get("search_type")
        not in _excluded_from_review
    ]

    _stickman_texts = {
        t
        for t, d in scriptTextToPexelSearch.items()
        if d.get("search_type") == MediaType.STICKMAN
    }
    _stickman_joint_texts = {
        t
        for t, d in scriptTextToPexelSearch.items()
        if media_props(d.get("search_type")).is_stickman_joint
    }
    _regenerable_stage1 = _stickman_texts | _stickman_joint_texts

    def _regen_stage1(script_text: str) -> list[dict] | None:
        st = scriptTextToPexelSearch.get(script_text, {}).get("search_type")
        if st == MediaType.STICKMAN:
            return _regenerate_stickman_scene(script_text, scriptTextToPexelSearch)
        if media_props(st).is_stickman_joint:
            return _regenerate_stickman_joint_scene(
                script_text, scriptTextToPexelSearch
            )
        return None

    final_data, has_manual = run_media_review(
        candidates_data=non_edit_candidates,
        history_file=str(HISTORY_FILE),
        review_state_file=REVIEW_STOCK_FOOTAGE_OUTPUT_FILE,
        cache_dir=_CACHE_DIR,
        regenerate_fn=_regen_stage1,
        regenerable_texts=_regenerable_stage1,
    )
    if has_manual:
        print("\n[main] Exiting so you can perform the manual fixes above.")
        sys.exit(0)

    # 2.55) ai_edit scenes — generated + reviewed ONE AT A TIME, in script
    #       order, so chains of consecutive ai_edits work to any depth (each
    #       edit waits for the previous scene's pick before it's generated).
    final_data = run_ai_edit_stage(
        script_to_search_term=scriptTextToPexelSearch,
        final_data=final_data,
    )

    print("\n=== FINAL SCRIPT → CHOSEN MEDIA ===")
    for entry in final_data:
        print(f"\nSCRIPT: {entry['script_text']}")
        for item in entry["footage"]:
            for url, trim in item.items():
                print(f"  ✓ {url}  (trim: {trim}s)")
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

    # 2.56) object_generate scenes — edit each scene's CHOSEN stock image in
    #       the OBJECT_SEPERATION editor (background separation + effects), then
    #       use the edited result. After review/ai_edit (needs the pick) and
    #       before the derive-from-previous stages (so a later scene can point
    #       at this edited image) and before colour-grade / Ken Burns (a still
    #       is graded + KB-animated like any image; an MP4 export is left as-is).
    final_data = run_object_generate_stage(
        script_to_search_term=scriptTextToPexelSearch,
        final_data=final_data,
    )

    # 2.57)
    additional_steps_save_for_later()

    # 2.6) Run every registered local-file generator (joint, read-out, …)
    #      and merge their outputs back into final_data so the stitcher
    #      uses the new local files instead of any prior placeholders.
    generated_footage_map = run_all_local_generators(
        script_to_search_term=scriptTextToPexelSearch,
        candidates_data=candidates_data,
        final_data=final_data,
    )

    final_data = integrate_generated_footage(
        final_data,
        generated_footage_map,
        source_label="local-generators",
        produced_msg="local footage produced — integrating into final_data",
        save_label="local footage",
        empty_msg="no local generators produced anything; final_data unchanged",
    )
    if generated_footage_map:
        _dump_final(final_data, "POST-GENERATOR-MERGE")

    # 2.62) Stickman-explain scenes: composite each scene's CHOSEN stock/wiki
    #       clip onto a board base. Runs AFTER review (needs the picks) and
    #       BEFORE Ken Burns (outputs are MP4s, so KB skips them — and the raw
    #       still, if one was picked, is already replaced here so KB never
    #       animates the board).
    explain_footage_map = generate_stickman_explain_scenes(
        script_to_search_term=scriptTextToPexelSearch,
        final_data=final_data,
    )
    final_data = integrate_generated_footage(
        final_data,
        explain_footage_map,
        source_label="stickman-explain",
        produced_msg="explainer footage produced — integrating into final_data",
        save_label="explainer footage",
        empty_msg="no explainer scenes; final_data unchanged",
    )

    # 2.63) Text-overlay scenes: caption (search_term) composited onto the
    #       PREVIOUS scene's chosen image. After explainer (so any prior scene
    #       type resolves) and before Ken Burns (static MP4 → KB skips it, so
    #       the tilted caption is never cropped).
    overlay_footage_map = generate_text_overlay_scenes(
        script_to_search_term=scriptTextToPexelSearch,
        final_data=final_data,
    )
    final_data = integrate_generated_footage(
        final_data,
        overlay_footage_map,
        source_label="text-overlay",
        produced_msg="text-overlay footage produced — integrating into final_data",
        save_label="text-overlay footage",
        empty_msg="no text-overlay scenes; final_data unchanged",
    )

    # 2.64) Manual stock placement: composite each MANUAL_STOCK_ADD_TO_PREVIOUS
    #       scene's chosen still onto the PREVIOUS scene's image at a clicked
    #       position/size. After text-overlay (so the base resolves to a
    #       finished image) and before Ken Burns (static MP4 → KB skips it).
    final_data = run_manual_image_stage(
        script_to_search_term=scriptTextToPexelSearch,
        final_data=final_data,
    )
    save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)

    # 2.648) Cinematic colour grade — give all STOCK footage one unified
    #        "shot on film at golden hour" look BEFORE Ken Burns, so stills are
    #        graded once (then KB animates the graded still) and stock videos /
    #        composites are graded in place. Toggle via
    #        TOGGLE_STOCK_COLOUR_GRADING_ETC / APPLY_COLOUR_GRADING_TO_ALL.
    final_data, colour_grade_remap = apply_colour_grading_to_final_data(
        final_data,
        scriptTextToPexelSearch,
    )
    if colour_grade_remap:
        add_path_remap_to_history(colour_grade_remap, label="colour-grade")
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with graded footage → {FINAL_SCRIPT_AND_CLIPS}")

    # 2.65) Convert remaining static images in final_data to Ken Burns MP4s.
    #       Joint/read-out outputs are already MP4s by this point, so only
    #       Pexels/Wikipedia stills the review GUI selected get processed.
    final_data, ken_burns_remap = apply_ken_burns_to_final_data(final_data)
    if ken_burns_remap:
        add_path_remap_to_history(ken_burns_remap, label="ken-burns")
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with Ken Burns MP4s → {FINAL_SCRIPT_AND_CLIPS}")
        _dump_final(final_data, "POST-KEN-BURNS")

    # 2.7) Build audio events (SFX + music) and persist for the stitcher
    print("====================================================================")
    print("Building audio events map...")
    audio_events_map = build_audio_events_map(scriptTextToPexelSearch)
    Path(AUDIO_EVENTS_FILE).write_text(json.dumps(audio_events_map, indent=2))
    print(f"💾 Audio events written to {AUDIO_EVENTS_FILE}")

    additional_steps_save_for_later()

    # 3) Stitch together into the final video.
    print("====================================================================")
    print("Stitching final video...")
    gc.collect()

    # Some upstream stages only write this conditionally; guarantee it exists
    # so the stitcher can always load the latest picks.
    save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
    print(f"💾 Final clip map → {FINAL_SCRIPT_AND_CLIPS}")

    stitch_together_video(
        FINAL_SCRIPT_AND_CLIPS,
        TIMESTAMPS_ABSOLUTE_FILE,
        HISTORY_FILE,
        SCRIPT_AUDIO_FILE,
        OUTPUT_FILE,
        AUDIO_EVENTS_FILE,
        SFX_VOLUME,
        MUSIC_VOLUME,
    )

    # so that our terminal doesn't mess up! (so i can still see what I'm typing...)
    subprocess.run(["stty", "sane"])

    print("done")


# ===========================================================================

if __name__ == "__main__":
    main()
# ================================================
# ==== OTHER THINGS MAYBE USEFUL DOWN THE LINE ===
# ================================================


def splitSceneIntoPowerpointSlideImages():
    twotest = "But where exactly in the world did this tea originate"

    ai_request = f"Split this sentence into the different images that would make up this slide on my powerpoint. Identify the key nouns and visual elements. Just simple bullet point: \n{twotest}"

    response2 = ollama.chat(
        model="qwen2.5:7b", messages=[{"role": "user", "content": ai_request}]
    )

    reply2 = response2["message"]["content"]
    print(reply2)

    ai_request2 = f"Strip out any ai fulff, explanations, headings or follow up questions: give me just a csv of identified key terms. nothing else: \n{reply2}"

    response3 = ollama.chat(
        model="qwen2.5:7b", messages=[{"role": "user", "content": ai_request2}]
    )
    reply3 = response3["message"]["content"]
    print(reply3)


def determineIfStockVideo():
    scenes_text = """
    The empire state building is really big.
    Built in Manhattan in the 19th century.
    """
    scenes = [line.strip() for line in scenes_text.split("\n") if line.strip()]

    for scene in scenes:
        ai_request = f"""
    Would this scene be likely to have nice stock footage available?
    Scene: {scene}
    Just output: yes or no
    """

        response4 = ollama.chat(
            model="qwen2.5:7b", messages=[{"role": "user", "content": ai_request}]
        )

        reply4 = response4["message"]["content"].strip()
        print(scene)
        print(reply4)
