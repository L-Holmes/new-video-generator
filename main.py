"""
Pipeline orchestrator.

All the heavy lifting now lives in focused modules — main() just wires the
stages together in order, like a bash script. Comment out a stage to resume
from a checkpoint.

  CONFIG                     constants, paths, MediaType, sessions, tracker
  ___visuals/cache_io        cache/JSON I/O, history index, path resolution
  ___visuals/sources/        where footage comes from: downloads (Pexels),
                             wikipedia, stamp_fetch
  ___visuals/ai/             everything model-generated: generation (the
                             stages), generate_stickman_images (the fal
                             call), edit, postprocess
  ___visuals/timing_merge    scene timings, joint timing, footage integrate
  ___visuals/scene_generators  joint / read-out / map / explainer / typography
  ___visuals/static_render   frame extraction, still→MP4, manual placement
  ___visuals/ken_burns       Ken Burns pan/zoom render pass
  ___visuals/pixellate_stage retro pixellation of AI stills
  ___visuals/colour_grade_stage  cinematic colour grade pass
  ___visuals/audio_events    per-scene SFX + music map
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

from ___visuals.add_relevant_overlays import apply_relevant_overlays_to_final_data
from ___visuals.ai.generation import (
    _regenerate_stickman_joint_scene,
    _regenerate_stickman_scene,
    generate_stickman_candidates,
    generate_stickman_joint_candidates,
    run_ai_edit_stage,
)
from ___visuals.audio_events import build_audio_events_map

# ===========================================================================
# IMPORTS - LOCAL (external pipeline stages driven directly by main)
# ===========================================================================
from ___visuals.audio_script_synchronizer import run as run_audio_script_synchronizer
from ___visuals.cache_io import (
    add_path_remap_to_history,
    load_from_cache,
    load_json,
    save_to_cache,
)
from ___visuals.collage_stage import run_collage_stage
from ___visuals.colour_grade_stage import apply_colour_grading_to_final_data
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
    TIMESTAMPS_ABSOLUTE_FILE,
    MediaType,
    SearchTermData,
    ensure_runtime_dirs,
    media_props,
    normalise_scene_row,
    resolve_group_continuations,
    scene_is_grouped,
)
from ___visuals.decorate_stage import (
    restore_stamp_rows_after_review,
    run_decorate_stage,
    swap_stamp_rows_for_review,
)
from ___visuals.sources.downloads import load_stock_footage
from ___visuals.ken_burns import apply_ken_burns_to_final_data
from ___visuals.pixellate_stage import pixellate_candidate_bundles
from ___visuals.scene_generators import (
    generate_stickman_explain_scenes,
    run_all_local_generators,
)
from ___visuals.script_audio_cutdown_and_process import run as run_audio_cutdown
from ___visuals.static_render import run_manual_image_stage
from ___visuals.stitch_together import stitch_together_video
from ___visuals.stock_footage_review import run_media_review
from ___visuals.timing_merge import integrate_generated_footage
from ___visuals.video_background_stage import run_video_background_stage

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


# ===========================================================================
# STAGE 0 - split + auto-tag + (if needed) manual tagging
# ===========================================================================
# Wires ___splitting_and_labelling (normally run on its own per its
# documentation/MASTER_README.md) into the pipeline so that `uv run main.py`
# alone is enough: split (spaCy, cached) -> work out the visualisables
# (cached) -> emit the shot list at the SAME path CONFIG expects -> auto-tag
# the easy rows (idempotent) -> if anything is STILL untagged, open the
# manual tagging GUI and block until it's done.
_SPLIT_LABEL_DIR = Path(__file__).resolve().parent / "___splitting_and_labelling"


def _untagged_lines(json_path: Path) -> list[str]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return [line for line, row in data.items()
            if not (row.get("media_type") or "").strip()]


def ensure_script_to_search_term_ready() -> None:
    if str(_SPLIT_LABEL_DIR) not in sys.path:
        sys.path.insert(0, str(_SPLIT_LABEL_DIR))
    # That package's own main.py runs its stages 0-2; its stage 3 (the
    # tagger) lives in a folder no import statement can name, so the
    # package __init__ fetches it by name — see its shared/PATHS.py.
    from ___splitting_and_labelling import main as split_and_label

    # We drive the REAL run, not that module's bundled examples — keep
    # cache/filenames clean of the TESTING_ prefix that flag adds, and its
    # output where CONFIG expects it rather than under TEST_RESULTS/.
    split_and_label.TESTING_SCRIPT_SEARCH_TERM_GENERATION = False

    out_path = Path(LINE_INDEX_TO_SEARCH_TERM_FILE)
    print("====================================================================")
    print(f"Ensuring shot list is ready: {out_path}")
    split_and_label.generate_script_to_search_term(SCRIPT_FILE,
                                                   out_path=out_path)

    unresolved = _untagged_lines(out_path)
    if not unresolved:
        print(f"  ✓ every line already has a media type -> {out_path}")
        return

    print(f"  {len(unresolved)} line(s) still need a media type "
          f"— opening manual tagging...")
    from ___splitting_and_labelling import MANUAL_TAGGING

    MANUAL_TAGGING.run_manual_tagging(out_path)

    unresolved = _untagged_lines(out_path)
    if unresolved:
        print(
            f"ERROR: {len(unresolved)} line(s) still have no media_type "
            f"after manual tagging (e.g. {unresolved[0]!r}). Re-run "
            f"`uv run main.py` to resume tagging."
        )
        sys.exit(1)


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
    ensure_runtime_dirs()
    """
    Runs the full pipeline from raw script to finished video.
    Each stage is a clearly labelled block – treat this like a bash script.
    Comment out any stage to resume from a checkpoint.
    """

    verify_environment()

    # 0) Split the raw script into shot lines + auto-tag + (if anything is
    #    left empty) block on manual tagging — see ensure_script_to_search_term_ready.
    ensure_script_to_search_term_ready()

    # 1) Break into scenes / load search-term map
    print("====================================================================")
    print("Breaking into scenes...")
    scriptTextToPexelSearch: dict[str, SearchTermData] = load_json(
        LINE_INDEX_TO_SEARCH_TERM_FILE
    )
    # Normalise every row: media_type becomes the MediaType enum, and the
    # modifiers / group_id / position columns are validated + guaranteed.
    # There is NO legacy layer: old flat files (search_type-only) must be
    # converted ONCE with `uv run tools/UPGRADE_OLD_JSON.py <file>` first.
    for key, value in scriptTextToPexelSearch.items():
        try:
            normalise_scene_row(key, value)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            sys.exit(1)
    # A group's continuation cells are tagged `hold_previous` + group. Give
    # them the group's real base before ANY stage looks at media_type — they
    # each fetch their own picture from it.
    try:
        resolve_group_continuations(scriptTextToPexelSearch)
    except ValueError as exc:
        print(f"ERROR: {exc}")
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

    # Stamp scenes (hold/background + decorate + term + stamp_source) join
    # the NORMAL fetch + review as their source type — swapped in memory
    # here, swapped back (picks stashed as stamps) right after the review.
    swap_stamp_rows_for_review(scriptTextToPexelSearch)

    candidates_data = load_from_cache(CANDIDATES_CACHE_FILE)
    if candidates_data:
        print(f"✅ Loaded {len(candidates_data)} candidate bundle(s) from cache.")
        # Top-up: stamp scenes enabled AFTER this cache was built have no
        # bundle yet — fetch just those and extend the cache.
        _have = {c["script_text"] for c in candidates_data}
        _missing = {
            t: r
            for t, r in scriptTextToPexelSearch.items()
            if "_stamp_orig_type" in r and t not in _have
        }
        if _missing:
            print(
                f"🔍 Fetching candidates for {len(_missing)} stamp "
                f"scene(s) missing from the cache..."
            )
            candidates_data.extend(load_stock_footage(_missing))
            _sm = generate_stickman_candidates(_missing)
            if _sm:
                candidates_data.extend(_sm)
            save_to_cache(candidates_data, CANDIDATES_CACHE_FILE)

        # Re-fetch any bundle whose scene has been RETAGGED since it was
        # cached. The cache is keyed by script_text alone, so without this a
        # line you fixed in the tagger (wikipedia → stock, or just a better
        # search term) keeps serving its old — often empty — bundle for ever,
        # and no amount of re-running helps. load_stock_footage() stamps each
        # bundle with the media_type + search_term it was fetched for; a
        # bundle with neither predates the stamp, so it is refreshed once.
        _have = {c["script_text"] for c in candidates_data}  # the stamp top-up grew it
        _external = {
            t: r
            for t, r in scriptTextToPexelSearch.items()
            if media_props(r.get("media_type")).needs_external_candidates
        }
        _stale, _unstamped = {}, 0
        for _c in candidates_data:
            _row = _external.get(_c["script_text"])
            if _row is None:
                continue  # not an external scene — nothing to compare against
            _want_type = _row["media_type"].value
            _want_term = _row.get("search_term", "")
            if "media_type" not in _c:
                # Written before bundles carried their provenance. We cannot
                # tell what it was fetched for, so refresh it once; the files
                # themselves are hash-cached, so this is cheap.
                _stale[_c["script_text"]] = _row
                _unstamped += 1
            elif (
                _c["media_type"] != _want_type
                or _c.get("search_term") != _want_term
            ):
                _stale[_c["script_text"]] = _row
                print(
                    f"♻️  retagged: '{_c['script_text'][:45]}' was "
                    f"{_c['media_type']}/'{_c.get('search_term', '')}', "
                    f"now {_want_type}/'{_want_term}'"
                )
        # ...and any external scene that has no bundle at all (newly retagged
        # INTO an external type, so the fetch never saw it).
        for _t, _row in _external.items():
            if _t not in _have:
                _stale[_t] = _row
                print(f"♻️  no bundle yet for '{_t[:45]}' — fetching")
        if _unstamped:
            print(
                f"♻️  {_unstamped} bundle(s) predate retag-detection — "
                f"refreshing once (downloads are cached, so this is quick)"
            )
        if _stale:
            print(f"🔍 Re-fetching {len(_stale)} scene(s)...")
            _fresh = {c["script_text"]: c for c in load_stock_footage(_stale)}
            candidates_data = [_fresh.pop(c["script_text"], c) for c in candidates_data]
            candidates_data.extend(_fresh.values())
            save_to_cache(candidates_data, CANDIDATES_CACHE_FILE)
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

        # AI stickman tiles for GROUPED ai_stock scenes — same downstream
        # flow as grouped stock (these bundles feed the joint compositor)
        # but the tiles are AI renders, not Pexels stills.
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
    # stickman / stickman_joint are in candidates_data here; edit gets
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

    # Exclude from the stage-1 review GUI (property-driven, no type lists):
    #   - ai-edit scenes: reviewed later in stage 2 (need the stage-1 picks)
    #   - hold-previous scenes: nothing to review, they derive their image
    def _skip_stage1(script_text: str) -> bool:
        p = media_props(scriptTextToPexelSearch.get(script_text, {}).get("media_type"))
        return p.is_ai_edit or p.is_hold_previous

    non_edit_candidates = [
        c for c in candidates_data if not _skip_stage1(c["script_text"])
    ]

    # A bundle with NOTHING in it (a wikipedia term that isn't an article, a
    # stock term with no Pexels hits) used to sail straight into the review
    # GUI, which then drew five "no image found" boxes with nothing to press:
    # every key is bound to a slot, and the only way out (F = manual) exits
    # the run. Catch it here and say exactly which line, and what to do.
    _empty_bundles = [
        c
        for c in non_edit_candidates
        if not (c.get("candidates", {}) or {}).get("videos")
        and not (c.get("candidates", {}) or {}).get("images")
    ]
    if _empty_bundles:
        print("\n" + "=" * 70)
        print(
            f"[main] FATAL: {len(_empty_bundles)} scene(s) came back with NO "
            f"candidates at all. The review GUI cannot offer you anything to "
            f"pick for them, so it would soft-lock."
        )
        print("=" * 70)
        for c in _empty_bundles:
            text = c["script_text"]
            row = scriptTextToPexelSearch.get(text, {})
            mt = row.get("media_type")
            print(f"\n  line : '{text}'")
            print(f"  type : {mt.value if hasattr(mt, 'value') else mt}")
            print(f"  term : '{row.get('search_term', '')}'")
        print(
            f"\n  Both sources came back empty for these terms — wikipedia "
            f"scenes already\n  fall back to stock stills, so this means "
            f"neither had anything at all.\n"
            f"\n  FIX — retag the line(s) in the tagger:\n"
            f"      uv run ___splitting_and_labelling/3-manual-tagging/"
            f"MANUAL_TAGGING.py "
            f"{LINE_INDEX_TO_SEARCH_TERM_FILE}\n"
            f"    A stock term wants something photographable "
            f"('sailing ship', not '1600s').\n"
            f"    A wikipedia term is the EXACT article name "
            f"('Banda Islands').\n"
            f"    Or pick a type that fetches nothing at all: blank / "
            f"random_background / hold_previous.\n"
            f"\n  Retagging is enough — the candidate cache notices the "
            f"changed type/term and\n  re-fetches that line by itself.\n"
        )
        sys.exit(1)

    def _is_ai_stock(d: dict, grouped: bool) -> bool:
        return (
            d.get("media_type") == MediaType.AI_STOCK and scene_is_grouped(d) == grouped
        )

    _stickman_texts = {
        t for t, d in scriptTextToPexelSearch.items() if _is_ai_stock(d, False)
    }
    _stickman_joint_texts = {
        t for t, d in scriptTextToPexelSearch.items() if _is_ai_stock(d, True)
    }
    _regenerable_stage1 = _stickman_texts | _stickman_joint_texts

    def _regen_stage1(script_text: str) -> list[dict] | None:
        d = scriptTextToPexelSearch.get(script_text, {})
        if _is_ai_stock(d, False):
            return _regenerate_stickman_scene(script_text, scriptTextToPexelSearch)
        if _is_ai_stock(d, True):
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
        script_to_search_term=scriptTextToPexelSearch,
    )
    if has_manual:
        print("\n[main] Exiting so you can perform the manual fixes above.")
        sys.exit(0)

    # Stamp scenes: real media types back, review PICKS stashed as the
    # stamps waiting in the decorate editor (their entries leave final_data
    # — those picks were stamp choices, not scene footage).
    restore_stamp_rows_after_review(scriptTextToPexelSearch, final_data)

    # Persist the stage-1 picks NOW so the edit review (next) can load them
    # as final_cache_data for its previous-entry preview — e.g. when reviewing
    # an edit scene, the preview can show the preceding ai_stock scene's
    # just-picked image. Without this save, final_script_to_clips.json is stale
    # (pre-stage-1) and the preview finds no image.
    save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)

    # 2.55) edit scenes — generated + reviewed ONE AT A TIME, in script
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

    # (object extraction is a decorate-editor tab now — the object media
    #  type is gone; use stock/whatever + the decorate modifier.)

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

    # (captions are a tool of the decorate editor now — see stage 2.645.
    #  the old dedicated text-overlay stage is gone.)

    # 2.64) Manual stock placement: composite each MANUAL_STOCK_ADD_TO_PREVIOUS
    #       scene's chosen still onto the PREVIOUS scene's image at a clicked
    #       position/size. After text-overlay (so the base resolves to a
    #       finished image) and before Ken Burns (static MP4 → KB skips it).
    final_data = run_manual_image_stage(
        script_to_search_term=scriptTextToPexelSearch,
        final_data=final_data,
    )
    save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)

    # 2.645) DECORATE editor — every scene carrying the `decorate` modifier
    #        opens its OWN finished footage in the ONE interactive editor
    #        (tools: draw, text/caption, zoom). Runs after every stage that
    #        decides a scene's image, before colour grade + Ken Burns
    #        (output is a static MP4, so KB never crops the drawings).
    final_data, decorate_remap = run_decorate_stage(
        final_data,
        scriptTextToPexelSearch,
    )
    if decorate_remap:
        add_path_remap_to_history(decorate_remap, label="decorate")
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with decorated scenes → {FINAL_SCRIPT_AND_CLIPS}")

    # 2.646) COLLAGE — scenes carrying the `collage` modifier compose their
    #        SEVERAL review picks into one image: auto scatter, or stamp
    #        them by hand in the decorator. Static MP4 → KB skips it.
    final_data, collage_remap = run_collage_stage(
        final_data,
        scriptTextToPexelSearch,
    )
    if collage_remap:
        add_path_remap_to_history(collage_remap, label="collage")
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with collage scenes → {FINAL_SCRIPT_AND_CLIPS}")

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

    # 2.655) VIDEO BACKGROUND MODE (optional) — the whole edit rides on ONE
    #        long background video (VIDEO_BACKGROUND_FILE @ _START), trimmed
    #        to the narration and playing continuously under every scene.
    #        Scenes with footage become overlays (80% cards, or keyed if
    #        white/transparent-backed); `background` / footage-less scenes
    #        show the bare background; background+decorate draws straight
    #        onto it. After grade + KB (cards carry their grade/motion),
    #        before the badges (they land on the composited frame).
    final_data, video_bg_remap = run_video_background_stage(
        final_data,
        scriptTextToPexelSearch,
    )
    if video_bg_remap:
        add_path_remap_to_history(video_bg_remap, label="video-background")
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(
            f"💾 Updated final_data with background composites → {FINAL_SCRIPT_AND_CLIPS}"
        )
        _dump_final(final_data, "POST-VIDEO-BACKGROUND")

    # 2.66) Auto-detected overlays — small FIXED corner badges (question mark,
    #       metric/imperial measurement chips) burned onto each scene's final
    #       footage. Runs AFTER Ken Burns so a badge never pans/zooms/crops with
    #       the image; handles both stills (PIL composite) and videos (ffmpeg
    #       overlay, motion preserved). Detection is per json section: '?' in the
    #       text → question chip; a measurement that STARTS the section (≤1
    #       leading word) → metric/imperial chip. First chip → top-left, second →
    #       top-right; a decorate-layer caption's own corner is left free.
    final_data, overlays_remap = apply_relevant_overlays_to_final_data(
        final_data,
        scriptTextToPexelSearch,
    )
    if overlays_remap:
        add_path_remap_to_history(overlays_remap, label="auto-overlays")
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with auto-overlays → {FINAL_SCRIPT_AND_CLIPS}")
        _dump_final(final_data, "POST-AUTO-OVERLAYS")

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
