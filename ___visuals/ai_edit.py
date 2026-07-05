

"""
example usage:

uv run edit.py output/004.png "raise the pirate's right arm above his head, sword pointing up"
uv run edit.py output/012.png "add a small parrot on his shoulder" -o output/012_parrot.png


uv run ai_edit.py temp/ai_output/005.png "raise the sword hand" -o temp/ai_output/005-hand-raised.png
"""

# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

"""
AI Edit generator.

For scenes flagged search_type == "ai_edit", takes a BASE image (the chosen
image of the nearest preceding AI scene - stickman or another ai_edit) and
applies the scene's search_term as an EDIT INSTRUCTION, producing `num_variants`
edited images for a second review stage.

Base resolution (done in main.py, passed in here as `ordered_scenes`):
  - Walk backwards from the edit scene to the nearest preceding scene that is
    an AI base (stickman or ai_edit). Non-AI scenes (stock/wikipedia/joint/
    read_out) are skipped, not treated as a base.
  - A preceding STICKMAN contributes its STAGE-1 chosen image.
  - A preceding AI_EDIT contributes its own variant-0 output (deterministic
    filename), so edit->edit chains work. NOTE: the chain is built on variant 0
    of each upstream edit; if you later pick a different variant for an upstream
    edit in stage-2 review, the downstream edit was built on variant 0. (Keeping
    it to two review stages requires this; ask if you'd rather review edits one
    scene at a time so each downstream edit uses the actually-chosen upstream.)
  - If no preceding AI base exists at all, the edit is generated FRESH using the
    stickman style (refs + search_term as prompt) - there's nothing to edit.

Output files (same scheme as stickman):
  <out_dir>/<stem>_<variant>.png
  <out_dir>/<stem>_<variant>.placeholder.png   (on failure)

Reuses the robust call core (_call_flux_to_file: timeout, abort flag, white-bg,
placeholder) from ai_generate_stickman_images so edits get the same out-of-
credits / hang protection and the same black-bar fix.
"""

import asyncio, pathlib

import fal_client

import ___visuals.ai_generate_stickman_images as sg
from ___visuals.ai_generate_stickman_images import (
    _call_flux_to_file, _scene_stem, _write_placeholder,
    REF_IMAGES, STYLE_PREFIX, STYLE_SUFFIX, CONCURRENCY,
)

# Appended to an edit instruction to keep the ms-paint look consistent.
EDIT_STYLE_SUFFIX = ("Keep the same hand-drawn ms paint style, in colour, "
                     "minimal, on a white background.")


# Appended to the prompt ONLY when context images are supplied. Nudges the
# model to treat the trailing reference images as consistency cues, not new
# content to merge. Set to "" to disable if your edit model handles
# multi-image refs poorly.
EDIT_CONTEXT_CLAUSE = (" The additional trailing reference images show "
                       "preceding scenes for character and style consistency "
                       "only; do not merge their content into the edit.")


def _variant0_path(out_dir, script_text) -> str:
    """Deterministic path of an edit scene's variant-0 image (used as a base by
    later edits, and computable BEFORE generation)."""
    return str(pathlib.Path(out_dir) / f"{_scene_stem(script_text)}_0.png")


def resolve_edit_bases(ordered_scenes, out_dir):
    """
    PURE (no fal, no I/O). Given the full ordered scene list, return
        { edit_script_text: base_image_path_or_None }
    by walking back to the nearest preceding AI-base scene for each edit.

    ordered_scenes : list of dicts in SCRIPT ORDER, each:
        {
          "script_text":  str,
          "is_edit":      bool,           # this scene is ai_edit
          "is_ai_base":   bool,           # eligible as an edit base (stickman or ai_edit)
          "chosen_image": str | None,     # stage-1 chosen image (for non-edit AI scenes)
        }
    """
    out_dir = pathlib.Path(out_dir)

    # The image each scene contributes if used as a base.
    def base_image_of(scene):
        if scene["is_edit"]:
            return _variant0_path(out_dir, scene["script_text"])  # predicted
        return scene.get("chosen_image")  # stage-1 chosen (may be None)

    resolved = {}
    for i, scene in enumerate(ordered_scenes):
        if not scene["is_edit"]:
            continue
        base = None
        for j in range(i - 1, -1, -1):
            prev = ordered_scenes[j]
            if not prev["is_ai_base"]:
                continue                      # skip stock/wiki/etc, keep walking
            candidate = base_image_of(prev)
            if candidate:                     # found a usable base
                base = candidate
                break
            # AI-base scene but no image (shouldn't happen) -> keep walking
        resolved[scene["script_text"]] = base
    return resolved


async def _edit_one(sem, script_text, instruction, base_image_path, ref_urls,
                    context_urls, out_dir, variant, abort_event, client):
    stem        = _scene_stem(script_text)
    real        = pathlib.Path(out_dir) / f"{stem}_{variant}.png"
    placeholder = pathlib.Path(out_dir) / f"{stem}_{variant}.placeholder.png"

    if real.exists():
        print(f"-> edit exists, skipping - {script_text[:55]}")
        return script_text, variant, str(real), False

    # Resolve base -> upload. If a base was expected but is missing on disk,
    # fall back to FRESH generation (refs only) rather than crashing.
    use_fresh = base_image_path is None or not pathlib.Path(base_image_path).exists()
    if base_image_path is not None and use_fresh:
        print(f"[ai_edit] base missing on disk for '{script_text[:45]}' "
              f"({base_image_path}) - generating fresh instead")

    try:
        if use_fresh:
            # Nothing to edit: context (preceding AI scenes) + style refs only.
            image_urls = [*context_urls, *ref_urls]
            prompt = f"{STYLE_PREFIX} {instruction}. {STYLE_SUFFIX}"
        else:
            base_url = await asyncio.to_thread(fal_client.upload_file, base_image_path)
            # Base (the thing being edited) FIRST, then context images
            # (preceding AI scenes for consistency), then style refs. If the
            # model leans too hard on context/refs instead of the base,
            # reorder or drop entries here.
            image_urls = [base_url, *context_urls, *ref_urls]
            prompt = f"{instruction}. {EDIT_STYLE_SUFFIX}"
        if context_urls:
            prompt += EDIT_CONTEXT_CLAUSE
    except Exception as e:
        if sg._looks_like_credit_error(e):
            abort_event.set()
        p = _write_placeholder(placeholder, script_text,
                               f"Could not upload base image: {e}",
                               title="AI EDIT UNAVAILABLE")
        print(f"XX edit upload failed - placeholder - {script_text[:40]} - {e}")
        return script_text, variant, p, True

    path, is_ph = await _call_flux_to_file(
        prompt=prompt, image_urls=image_urls,
        real_path=real, placeholder_path=placeholder,
        scene_text=script_text, abort_event=abort_event, sem=sem, client=client,
    )
    return script_text, variant, path, is_ph


async def _generate_all(ordered_scenes, out_dir, num_variants):
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    edit_scenes = [s for s in ordered_scenes if s["is_edit"]]
    print(f"Processing {len(edit_scenes)} ai_edit scene(s) x {num_variants} variant(s)")
    if not edit_scenes:
        return {}

    client = fal_client.AsyncClient()

    # Upload the style refs ONCE and reuse the URLs.
    ref_urls = [await asyncio.to_thread(fal_client.upload_file, p) for p in REF_IMAGES]

    bases = resolve_edit_bases(ordered_scenes, out_dir)
    sem = asyncio.Semaphore(CONCURRENCY)
    abort_event = asyncio.Event()

    mapping, placeholder_scenes = {}, set()

    instr_by_text = {s["script_text"]: s.get("instruction", "") for s in edit_scenes}
    context_by_text = {
        s["script_text"]: list(s.get("context_images", []) or [])
        for s in edit_scenes
    }

    # Upload each distinct context image once; reuse the URL across variants
    # (and across scenes, should the same image recur).
    _ctx_url_cache: dict[str, str] = {}
    async def _upload_ctx(path):
        if path in _ctx_url_cache:
            return _ctx_url_cache[path]
        if not pathlib.Path(path).exists():
            print(f"[ai_edit] context image missing on disk, skipping: {path}")
            return None
        url = await asyncio.to_thread(fal_client.upload_file, path)
        _ctx_url_cache[path] = url
        return url

    # Generate SCENE-BY-SCENE in script order (variants within a scene run
    # concurrently, bounded by sem). In the one-at-a-time review flow there's
    # exactly one edit scene per call.
    for scene in edit_scenes:
        text = scene["script_text"]
        base = bases.get(text)

        context_urls = []
        for cp in context_by_text.get(text, []):
            u = await _upload_ctx(cp)
            if u:
                context_urls.append(u)
        if context_by_text.get(text):
            print(f"[ai_edit] '{text[:45]}' using {len(context_urls)} "
                  f"context image(s)")

        tasks = [
            _edit_one(sem, text, instr_by_text[text], base, ref_urls,
                      context_urls, out_dir, v, abort_event, client)
            for v in range(num_variants)
        ]
        for t, variant, path, is_ph in await asyncio.gather(*tasks):
            if path is None:
                continue
            mapping.setdefault(t, []).append((variant, path))
            if is_ph:
                placeholder_scenes.add(t)

    if placeholder_scenes:
        print("\n" + "!" * 70)
        print(f"[ai_edit] WARNING: {len(placeholder_scenes)} edit scene(s) got a "
              f"PLACEHOLDER. To retry: delete the per-edit edit_candidates_NNN.json "
              f"and the *.placeholder.png files in {out_dir}, top up, re-run.")
        print("!" * 70)

    return {
        t: [p for _, p in sorted(pairs, key=lambda pv: pv[0])]
        for t, pairs in mapping.items()
    }


def generate_ai_edits(ordered_scenes, out_dir, num_variants=1):
    """
    Synchronous entry point. Returns { edit_script_text: [image_path, ...] }.
    Never raises on a generation failure - failed edits get a placeholder path.
    """
    return asyncio.run(_generate_all(ordered_scenes, str(out_dir), num_variants))
