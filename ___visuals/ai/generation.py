"""
AI image generation stages: stickman candidates, stickman-joint tiles, the
per-scene regenerators used by the review GUIs, and the edit stage
(generate + review chained AI edits one scene at a time).

The fal / edit / generate_stickman_images dependencies are imported
lazily inside the functions, so pipeline runs that don't use AI never pull
them in.
"""

from __future__ import annotations

# Allow running this file directly from the repo root (uv run ___visuals/ai/generation.py).
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))   # the repo root: this file is two folders deep

import sys
from pathlib import Path

from ___visuals.cache_io import (
    _edit_candidates_cache_file,
    _edit_review_state_file,
    _load_history,
    _resolve_to_local_path,
    _save_history,
    load_from_cache,
    save_to_cache,
)
from CONFIG import (
    _CACHE_DIR,
    AI_EDIT_CONTEXT_NUM_IMAGES,
    AI_EDIT_NUM_VARIANTS,
    AI_EDIT_OUTPUT_DIR,
    FINAL_SCRIPT_AND_CLIPS,
    HISTORY_FILE,
    STICKMAN_CONTEXT_NUM_IMAGES,
    STICKMAN_JOINT_NUM_VARIANTS,
    STICKMAN_JOINT_OUTPUT_DIR,
    STICKMAN_NUM_VARIANTS,
    STICKMAN_OUTPUT_DIR,
    STICKMAN_PROMPTS_FILE,
    MediaType,
    SearchTermData,
    media_props,
    scene_is_grouped,
)
from ___visuals.pixellate_stage import _maybe_pixellate_entries
from ___visuals.stock_footage_review import run_media_review
from ___visuals.timing_merge import _load_scene_timings

# === BEGIN verbatim move from main.py (AI generation) ===
# ===========================================================================
# STICKMAN CANDIDATE GENERATION (AI-generated, reviewed like stock stills)
# ===========================================================================


def generate_stickman_candidates(
    script_to_search_term: dict[str, SearchTermData],
) -> list[dict]:
    """
    For every plain (non-grouped) MediaType.AI_STOCK scene, generate
    STICKMAN_NUM_VARIANTS AI images (same prompt) and return candidate bundles
    in the SAME shape load_stock_footage() returns, so they can be appended to
    candidates_data and reviewed by the same GUI (which will show N options).

    Generated PNGs are keyed in the candidates dict by their local path, and an
    identity entry (path -> path) is added to history.json so the review GUI
    and the stitcher resolve them exactly like downloaded media.

    Returns [] (and does no work) if there are no stickman scenes.
    """
    stickman_scenes = {
        txt: data
        for txt, data in script_to_search_term.items()
        if data["media_type"] == MediaType.AI_STOCK and not scene_is_grouped(data)
    }

    print("\n" + "=" * 70)
    print(f"[stickman] {len(stickman_scenes)} stickman scene(s) found")
    print("=" * 70)

    if not stickman_scenes:
        print("[stickman] nothing to generate — skipping")
        return []

    # Lazy import keeps the fal / dotenv dependency out of pipeline runs that
    # don't use stickman scenes.
    from ___visuals.ai.generate_stickman_images import generate_stickman_images

    print(
        f"[stickman] generating {STICKMAN_NUM_VARIANTS} variant(s) per scene "
        f"→ {STICKMAN_OUTPUT_DIR}"
    )
    generated = generate_stickman_images(
        prompts_file=STICKMAN_PROMPTS_FILE,
        out_dir=STICKMAN_OUTPUT_DIR,
        num_variants=STICKMAN_NUM_VARIANTS,
        context_num_images=STICKMAN_CONTEXT_NUM_IMAGES,
    )
    # generated: { script_text: [path, path, ...] }
    print(f"[stickman] generator returned images for {len(generated)} scene(s)")

    scene_timings = _load_scene_timings()

    bundles: list[dict] = []
    for script_text in stickman_scenes:
        image_paths = generated.get(script_text)
        if not image_paths:  # stripped-key fallback, mirroring the joint path
            for k, v in generated.items():
                if k.strip() == script_text.strip():
                    image_paths = v
                    break

        if not image_paths:
            print(
                f"[stickman] WARNING: no images for '{script_text[:60]}' — "
                f"the review GUI will have no options for this scene"
            )
            continue

        image_paths = [p for p in image_paths if Path(p).exists()]
        if not image_paths:
            print(
                f"[stickman] WARNING: generated paths missing on disk for "
                f"'{script_text[:60]}' — skipping"
            )
            continue

        if script_text not in scene_timings:
            print(f"[stickman] FATAL: no timing for '{script_text[:60]}'")
            sys.exit(1)
        duration = round(float(scene_timings[script_text]), 3)

        # One stickman image fills the whole scene; offer every variant as a
        # choice. num_clips_needed = 1 — the reviewer picks a single image.
        image_candidates = [{p: duration} for p in image_paths]

        bundles.append(
            {
                "script_text": script_text,
                "candidates": {"videos": [], "images": image_candidates},
                "num_clips_needed": 1,
                "max_runtime_per_clip_seconds": duration,
            }
        )
        print(
            f"[stickman]   '{script_text[:50]}' → {len(image_candidates)} "
            f"option(s), {duration:.2f}s each"
        )

    # Register identity entries so the url→local lookup resolves these PNGs.
    history = _load_history()
    added = 0
    for bundle in bundles:
        for cand in bundle["candidates"]["images"]:
            for path in cand:
                if path not in history:
                    history[path] = path
                    added += 1
    _save_history(history)
    print(f"[stickman] added {added} identity entry(ies) to history.json")

    print(f"[stickman] DONE — {len(bundles)} candidate bundle(s)")
    return bundles


def generate_stickman_joint_candidates(
    script_to_search_term: dict[str, SearchTermData],
) -> list[dict]:
    """
    For every GROUPED MediaType.AI_STOCK scene (the ai_stock lines carrying
    the `group` modifier — the grid tiles), generate ONE AI stickman image —
    reusing the SAME generator + prompt engineering as plain ai_stock — and
    return candidate bundles in the SAME shape load_stock_footage() returns.

    These bundles are appended to candidates_data and consumed by the joint
    compositor (generate_joint_scenes) EXACTLY like the Pexels-image bundles
    are for grouped stock. The ONLY difference between a stock group and an
    ai_stock group is the source of the tile images: Pexels vs AI.

    generate_stickman_images(grouped=True) filters the REAL search-term file
    by media_type == "ai_stock" + the group modifier, so it can't collide
    with the ordinary plain-ai_stock pass.

    Returns [] (and does no work) if there are no stickman-joint scenes.
    """
    joint_scenes = {
        txt: data
        for txt, data in script_to_search_term.items()
        if data["media_type"] == MediaType.AI_STOCK and scene_is_grouped(data)
    }

    print("\n" + "=" * 70)
    print(f"[stickman-joint] {len(joint_scenes)} stickman-joint scene(s) found")
    print("=" * 70)

    if not joint_scenes:
        print("[stickman-joint] nothing to generate — skipping")
        return []

    # Lazy import — keeps the fal / dotenv dependency out of runs that don't
    # use any AI-generated scenes.
    from ___visuals.ai.generate_stickman_images import generate_stickman_images

    STICKMAN_JOINT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate once per stickman-joint type (the file filter matches ONE
    # type-string per call). Currently a single type, but looping keeps the
    # set honest if more get added later.
    print(
        f"[stickman-joint] generating {STICKMAN_JOINT_NUM_VARIANTS} variant(s) "
        f"per scene → {STICKMAN_JOINT_OUTPUT_DIR}"
    )
    generated: dict[str, list[str]] = generate_stickman_images(
        prompts_file=STICKMAN_PROMPTS_FILE,  # the real search-term file
        out_dir=STICKMAN_JOINT_OUTPUT_DIR,
        num_variants=STICKMAN_JOINT_NUM_VARIANTS,
        grouped=True,  # the ai_stock lines carrying the group modifier
    )
    # generated: { script_text: [path, ...] }
    print(f"[stickman-joint] generator returned images for {len(generated)} scene(s)")

    scene_timings = _load_scene_timings()

    bundles: list[dict] = []
    history = _load_history()
    added = 0

    for txt in joint_scenes:
        image_paths = generated.get(txt)
        if not image_paths:  # stripped-key fallback (mirrors stickman/joint paths)
            for k, v in generated.items():
                if k.strip() == txt.strip():
                    image_paths = v
                    break

        image_paths = [p for p in (image_paths or []) if Path(p).exists()]
        if not image_paths:
            print(
                f"[stickman-joint] WARNING: no image generated for "
                f"'{txt[:60]}' — the joint compositor will fail for this scene"
            )
            continue

        if txt not in scene_timings:
            print(f"[stickman-joint] FATAL: no timing for '{txt[:60]}'")
            sys.exit(1)
        duration = round(float(scene_timings[txt]), 3)

        # One image fills this scene's tile; the compositor uses the first
        # candidate, so a single variant is all that's needed.
        image_candidates = [{p: duration} for p in image_paths]

        bundles.append(
            {
                "script_text": txt,
                "candidates": {"videos": [], "images": image_candidates},
                "num_clips_needed": 1,
                "max_runtime_per_clip_seconds": duration,
            }
        )
        print(
            f"[stickman-joint]   '{txt[:50]}' → {len(image_candidates)} "
            f"image(s), {duration:.2f}s each"
        )

        # Identity entries so history.get(image_url) in the joint compositor
        # resolves these PNGs straight to disk (no download attempted).
        for p in image_paths:
            if p not in history:
                history[p] = p
                added += 1

    _save_history(history)
    print(f"[stickman-joint] added {added} identity entry(ies) to history.json")
    print(f"[stickman-joint] DONE — {len(bundles)} candidate bundle(s)")
    return bundles


def _regenerate_stickman_joint_scene(
    script_text: str,
    script_to_search_term: dict[str, SearchTermData],
) -> list[dict] | None:
    """
    Re-run the stickman generator for ONE stickman_joint tile → fresh image
    candidates for the review GUI's 'try again' (R). Same generator + prompt
    engineering as stickman, just the joint type/dir/variant count.
    """
    from ___visuals.ai.generate_stickman_images import (
        _scene_stem,
        generate_stickman_images,
    )

    stem = _scene_stem(script_text)
    for v in range(STICKMAN_JOINT_NUM_VARIANTS):
        for fname in (f"{stem}_{v}.png", f"{stem}_{v}.placeholder.png"):
            try:
                (STICKMAN_JOINT_OUTPUT_DIR / fname).unlink(missing_ok=True)
            except Exception:
                pass

    generated = generate_stickman_images(
        prompts_file=STICKMAN_PROMPTS_FILE,
        out_dir=STICKMAN_JOINT_OUTPUT_DIR,
        num_variants=STICKMAN_JOINT_NUM_VARIANTS,
        grouped=True,
    )

    paths = generated.get(script_text)
    if not paths:
        for k, v in generated.items():
            if k.strip() == script_text.strip():
                paths = v
                break
    paths = [p for p in (paths or []) if Path(p).exists()]
    if not paths:
        print(f"[regen] stickman_joint produced nothing for '{script_text[:60]}'")
        return None

    scene_timings = _load_scene_timings()
    duration = round(float(scene_timings[script_text]), 3)

    history = _load_history()
    for p in paths:
        history.setdefault(p, p)
    _save_history(history)

    entries = _maybe_pixellate_entries(
        [{p: duration} for p in paths],
        script_to_search_term.get(script_text, {}).get("media_type"),
    )
    print(f"[regen] stickman_joint '{script_text[:50]}' → {len(entries)} new option(s)")
    return entries


def _regenerate_stickman_scene(
    script_text: str,
    script_to_search_term: dict[str, SearchTermData],
) -> list[dict] | None:
    """
    Re-run the stickman generator for ONE scene → fresh image candidates
    ([{path: trim}, ...]) for the review GUI's 'try again' (R).

    Deletes this scene's existing variant + placeholder PNGs so the generator
    actually re-renders it (it skips files that already exist); every OTHER
    stickman scene keeps its cached image.
    """
    from ___visuals.ai.generate_stickman_images import (
        _scene_stem,
        generate_stickman_images,
    )

    stem = _scene_stem(script_text)
    for v in range(STICKMAN_NUM_VARIANTS):
        for fname in (f"{stem}_{v}.png", f"{stem}_{v}.placeholder.png"):
            try:
                (STICKMAN_OUTPUT_DIR / fname).unlink(missing_ok=True)
            except Exception:
                pass

    generated = generate_stickman_images(
        prompts_file=STICKMAN_PROMPTS_FILE,
        out_dir=STICKMAN_OUTPUT_DIR,
        num_variants=STICKMAN_NUM_VARIANTS,
        context_num_images=STICKMAN_CONTEXT_NUM_IMAGES,
    )

    paths = generated.get(script_text)
    if not paths:  # stripped-key fallback
        for k, v in generated.items():
            if k.strip() == script_text.strip():
                paths = v
                break
    paths = [p for p in (paths or []) if Path(p).exists()]
    if not paths:
        print(f"[regen] stickman produced nothing for '{script_text[:60]}'")
        return None

    scene_timings = _load_scene_timings()
    duration = round(float(scene_timings[script_text]), 3)

    history = _load_history()  # identity entries so lookups resolve
    for p in paths:
        history.setdefault(p, p)
    _save_history(history)

    entries = _maybe_pixellate_entries(
        [{p: duration} for p in paths],
        script_to_search_term.get(script_text, {}).get("media_type"),
    )
    print(f"[regen] stickman '{script_text[:50]}' → {len(entries)} new option(s)")
    return entries


def _regenerate_ai_edit_scene(
    edit_text: str,
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
    cand_cache: str,
) -> list[dict] | None:
    """
    Re-run the edit generator for ONE edit scene → fresh image candidates
    for the review GUI's 'try again' (R).

    Deletes this edit's existing variant + placeholder PNGs (the generator
    skips existing files), rebuilds the candidates from the CURRENT final_data
    (same base/context the user is reviewing), and refreshes the per-edit
    candidate cache so a later resume uses the new images.
    """
    from ___visuals.ai.generate_stickman_images import _scene_stem

    stem = _scene_stem(edit_text)
    for v in range(AI_EDIT_NUM_VARIANTS):
        for fname in (f"{stem}_{v}.png", f"{stem}_{v}.placeholder.png"):
            try:
                (AI_EDIT_OUTPUT_DIR / fname).unlink(missing_ok=True)
            except Exception:
                pass

    bundles = build_ai_edit_candidates_for_target(
        script_to_search_term=script_to_search_term,
        final_data=final_data,
        target_text=edit_text,
    )
    if not bundles:
        print(f"[regen] edit produced nothing for '{edit_text[:60]}'")
        return None

    save_to_cache(bundles, cand_cache)  # keep resume in sync
    images = bundles[0].get("candidates", {}).get("images") or None
    if images:
        print(f"[regen] edit '{edit_text[:50]}' → {len(images)} new option(s)")
    return images


def build_ai_edit_candidates_for_target(
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
    target_text: str,
) -> list[dict]:
    """
    Generate edit image(s) for a SINGLE target edit scene, returned in
    the load_stock_footage() shape so the same review GUI consumes them.

    CRITICAL: `final_data` must already hold the user's picks for every scene
    that PRECEDES `target_text` in script order — including earlier ai_edits.
    That's what makes chains work: edit N is built from the image the user
    actually CHOSE for the preceding AI scene (which may be edit N-1).

    Only the target is flagged is_edit=True, so generate_ai_edits produces
    exactly one scene's images; every other scene is offered purely as a
    potential walk-back base (is_ai_base + chosen_image).
    """
    from ___visuals.ai.edit import generate_ai_edits

    # Resolve each decided scene's CHOSEN image (first footage entry) to disk.
    chosen_by_text: dict[str, str | None] = {}
    for entry in final_data:
        footage = entry.get("footage") or []
        key = next(iter(footage[0]), None) if footage else None
        chosen_by_text[entry["script_text"]] = (
            _resolve_to_local_path(key) if key else None
        )

    # Ordered descriptors (dict order == script order). Collect preceding AI
    # images for optional context as we walk.
    ordered_scenes: list[dict] = []
    preceding_ai_images: list[str] = []  # resolved local paths, script order
    reached_target = False

    for text, data in script_to_search_term.items():
        st = data["media_type"]
        is_target = text == target_text
        chosen_local = chosen_by_text.get(text)

        ordered_scenes.append(
            {
                "script_text": text,
                "is_edit": is_target,  # ONLY the target
                "is_ai_base": media_props(st).is_ai_base,
                "instruction": data["search_term"],
                "chosen_image": None if is_target else chosen_local,
            }
        )

        if is_target:
            reached_target = True
        elif not reached_target and media_props(st).is_ai_base and chosen_local:
            preceding_ai_images.append(chosen_local)

    if not preceding_ai_images:
        print(
            f"[edit] WARNING: no preceding stickman/ai_edit scene before "
            f"'{target_text[:60]}' — there's no base image to edit. Put a "
            f"stickman (or an earlier edit) ahead of it in the script."
        )

    # Optional extra context: the N most recent preceding AI images, EXCLUDING
    # the immediate base (preceding_ai_images[-1]) which is already the edit
    # source. Flip to include the base by dropping the [:-1] slice.
    context_images: list[str] = []
    if AI_EDIT_CONTEXT_NUM_IMAGES > 0 and len(preceding_ai_images) > 1:
        context_images = preceding_ai_images[:-1][-AI_EDIT_CONTEXT_NUM_IMAGES:]

    for sc in ordered_scenes:
        if sc["is_edit"]:
            sc["context_images"] = context_images  # consumed by generate_ai_edits

    print(f"\n[edit] building target '{target_text[:60]}'")
    base_name = Path(preceding_ai_images[-1]).name if preceding_ai_images else "NONE"
    print(
        f"[edit]   preceding AI images: {len(preceding_ai_images)} (base = {base_name})"
    )
    print(f"[edit]   context images passed: {len(context_images)}")
    for ci in context_images:
        print(f"[edit]     context ← {Path(ci).name}")

    generated = generate_ai_edits(
        ordered_scenes,
        out_dir=AI_EDIT_OUTPUT_DIR,
        num_variants=AI_EDIT_NUM_VARIANTS,
    )  # { edit_text: [path, ...] }

    paths = [p for p in generated.get(target_text, []) if Path(p).exists()]
    if not paths:
        print(f"[edit] WARNING: no images generated for '{target_text[:60]}'")
        return []

    scene_timings = _load_scene_timings()
    if target_text not in scene_timings:
        print(f"[edit] FATAL: no timing for '{target_text[:60]}'")
        sys.exit(1)
    dur = round(float(scene_timings[target_text]), 3)

    history = _load_history()  # identity entries so lookups resolve
    for p in paths:
        history.setdefault(p, p)
    _save_history(history)

    # Pixellate the fresh fal output BEFORE review, so you review / hand-edit
    # the pixellated edit. Its BASE was the CHOSEN pixellated image of the
    # preceding AI scene (resolved from final_data above), so the whole chain
    # stays in the pixel look and reflects any manual fixes you painted in.
    pixel_images = _maybe_pixellate_entries(
        [{p: dur} for p in paths], MediaType.AI_EDIT_PREVIOUS
    )

    print(f"[edit]   → {len(paths)} candidate image(s), {dur:.2f}s each")
    return [
        {
            "script_text": target_text,
            "candidates": {"videos": [], "images": pixel_images},
            "num_clips_needed": 1,
            "max_runtime_per_clip_seconds": dur,
        }
    ]


def run_ai_edit_stage(
    script_to_search_term: dict[str, SearchTermData],
    final_data: list[dict],
) -> list[dict]:
    """
    Generate + review every edit scene ONE AT A TIME, in script order.

    An edit edits the image chosen for the nearest preceding AI scene, which
    may itself be an earlier edit — so edit N can't be generated until the
    user has PICKED edit N-1. We therefore loop:

        for each edit (script order):
            build candidates from the CURRENT final_data (has all prior picks)
            review it (blocking GUI, just this one scene)
            merge the pick back into final_data

    Handles a single edit, scattered ai_edits, and arbitrarily long runs of
    consecutive ai_edits identically. Per-edit candidate + review-state files
    are scoped by script index, so a re-run resumes and reuses prior work.
    Delete those files (or the cache dir) to force regeneration.
    """
    edit_texts = [
        txt
        for txt, data in script_to_search_term.items()
        if data["media_type"] == MediaType.AI_EDIT_PREVIOUS
    ]

    print("\n" + "=" * 70)
    print(f"[edit stage] {len(edit_texts)} edit scene(s) to process")
    print("=" * 70)

    if not edit_texts:
        print("[edit stage] no edit scenes — skipping")
        return final_data

    by_script = {e["script_text"]: i for i, e in enumerate(final_data)}
    script_index = {txt: i for i, txt in enumerate(script_to_search_term)}

    for n, edit_text in enumerate(edit_texts, start=1):
        idx = script_index[edit_text]
        cand_cache = _edit_candidates_cache_file(idx)
        state_file = _edit_review_state_file(idx)

        print("\n" + "-" * 70)
        print(
            f"[edit stage] ({n}/{len(edit_texts)}) scene #{idx}: '{edit_text[:60]}'"
        )
        print("-" * 70)

        # Build (or load) THIS edit's candidates from the up-to-date final_data.
        bundles = load_from_cache(cand_cache)
        if bundles:
            print(f"[edit stage]   loaded {len(bundles)} cached bundle(s)")
        else:
            bundles = build_ai_edit_candidates_for_target(
                script_to_search_term=script_to_search_term,
                final_data=final_data,
                target_text=edit_text,
            )
            save_to_cache(bundles, cand_cache)

        if not bundles:
            print(f"[edit stage]   WARNING: nothing generated — skipping")
            continue

        # Review THIS edit (blocking; returns after the user picks).
        print(f"[edit stage]   launching review GUI for this edit...")

        def _regen_edit(
            script_text: str, _t=edit_text, _cc=cand_cache
        ) -> list[dict] | None:
            if script_text != _t:
                return None
            return _regenerate_ai_edit_scene(
                edit_text=_t,
                script_to_search_term=script_to_search_term,
                final_data=final_data,
                cand_cache=_cc,
            )

        edit_final, has_manual = run_media_review(
            candidates_data=bundles,
            history_file=str(HISTORY_FILE),
            review_state_file=state_file,
            cache_dir=_CACHE_DIR,
            regenerate_fn=_regen_edit,
            regenerable_texts={edit_text},
            script_to_search_term=script_to_search_term,
        )

        if has_manual:
            print(
                f"\n[edit stage] Exiting for manual fixes (scene #{idx}). "
                f"Re-run to resume from here."
            )
            sys.exit(0)

        # Merge the pick so the NEXT edit can build on it.
        for e in edit_final:
            if e["script_text"] in by_script:
                final_data[by_script[e["script_text"]]]["footage"] = e["footage"]
            else:
                final_data.append(e)
                by_script[e["script_text"]] = len(final_data) - 1
            for item in e["footage"]:
                for path, trim in item.items():
                    print(
                        f"[edit stage]   ✓ picked {Path(path).name} (trim {trim}s)"
                    )

        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)  # checkpoint each pick

    print("\n" + "=" * 70)
    print(f"[edit stage] DONE — processed {len(edit_texts)} edit scene(s)")
    print("=" * 70)
    return final_data


# ===========================================================================
# GENERATOR: JOINT SCENES
# ===========================================================================
