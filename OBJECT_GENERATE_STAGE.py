"""
Object-generate stage.

For every OBJECT_GENERATE scene, open the scene's CHOSEN stock image (picked in
stage-1 review, image-only) in the OBJECT_SEPERATION editor — background
separation + effects — and swap the edited result into final_data.

Runs AFTER stage-1 review + ai_edit (it needs the pick) and BEFORE the
derive-from-previous stages (so a later scene can reference the edited image)
and before colour-grade / Ken Burns:
  • a STILL output is left as a plain image path, so it is graded + Ken-Burns
    animated exactly like any other still;
  • an MP4 output (produced when an animated border / parallax / jiggle effect
    was armed in the editor) is left as-is — Ken Burns skips MP4s.

Resume: each scene's edit is written under
OBJECT_GENERATE_OUTPUT_DIR/scene_<idx>/sep-edit-*. If one already exists it is
reused without re-opening the GUI; delete that scene folder to redo it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from CACHE_IO import (
    _classify_footage_path,
    _resolve_to_local_path,
    add_local_paths_to_history,
    save_to_cache,
)
from CONFIG import (
    FINAL_SCRIPT_AND_CLIPS,
    OBJECT_GENERATE_OUTPUT_DIR,
    SearchTermData,
    media_props,
)
from TIMING_MERGE import _load_scene_timings


def _resolve_scene_still(text: str, final_data: list[dict]) -> "str | None":
    """The image the reviewer picked for this scene. object_generate is
    image-only, so footage[0]'s key resolves to a still."""
    entry = next((e for e in final_data if e["script_text"] == text), None)
    footage = (entry or {}).get("footage") or []
    key = next(iter(footage[0]), None) if footage else None
    if not key:
        return None
    return _resolve_to_local_path(key)


def _find_existing_edit(scene_dir: Path) -> "Path | None":
    """Return a previously produced edit (sep-edit-*) in scene_dir, if any."""
    if not scene_dir.exists():
        return None
    for p in sorted(scene_dir.glob("sep-edit-*")):
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def run_object_generate_stage(
    script_to_search_term: dict[str, "SearchTermData"],
    final_data: list[dict],
) -> list[dict]:
    """Edit each object_generate scene's chosen image and merge the result
    back into final_data (one scene at a time, saving after each)."""
    ordered_texts = list(script_to_search_term.keys())
    og_texts = [
        t
        for t in ordered_texts
        if media_props(script_to_search_term[t]["search_type"]).is_object_generate
    ]

    print("\n" + "=" * 70)
    print(f"[object-gen] {len(og_texts)} object_generate scene(s) to edit")
    print("=" * 70)
    if not og_texts:
        print("[object-gen] none — skipping")
        return final_data

    # Imported lazily so the rest of the pipeline doesn't pull in Tk / the
    # editor unless this stage actually runs.
    from OBJECT_SEPERATION import run_editor

    scene_timings = _load_scene_timings()
    by_script = {e["script_text"]: i for i, e in enumerate(final_data)}
    script_index = {txt: i for i, txt in enumerate(script_to_search_term)}

    OBJECT_GENERATE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for n, text in enumerate(og_texts, start=1):
        idx = script_index[text]
        print("\n" + "-" * 70)
        print(f"[object-gen] ({n}/{len(og_texts)}) scene #{idx}: '{text[:55]}'")
        print("-" * 70)

        if text not in scene_timings:
            print(f"[object-gen] FATAL: no timing for '{text[:60]}'")
            sys.exit(1)
        duration = float(scene_timings[text])
        if duration <= 0:
            print(f"[object-gen] WARNING: zero duration — skipping '{text[:55]}'")
            continue

        src_still = _resolve_scene_still(text, final_data)
        if not src_still:
            print(
                f"[object-gen] FATAL: no chosen stock image for '{text[:60]}'. "
                f"Its source is picked in stage-1 review — did you select one?"
            )
            sys.exit(1)
        if _classify_footage_path(src_still) == "video":
            print(
                f"[object-gen] FATAL: chosen media is a VIDEO but object_generate "
                f"is image-only: {src_still}"
            )
            sys.exit(1)
        print(f"[object-gen]   source image = {Path(src_still).name}")

        scene_dir = OBJECT_GENERATE_OUTPUT_DIR / f"scene_{idx:03d}"

        # Resume: reuse a prior edit without re-opening the GUI.
        edited = _find_existing_edit(scene_dir)
        if edited is not None:
            print(f"[object-gen]   resume: reusing {edited.name}")
        else:
            scene_dir.mkdir(parents=True, exist_ok=True)
            print(f"[object-gen]   opening editor… (output → {scene_dir})")
            out_path = run_editor(src_still, output_dir=str(scene_dir))
            # Trust the returned path, but fall back to scanning the scene
            # folder: the editor writes sep-edit-* on finish, so if it saved we
            # proceed even if the return value didn't propagate (e.g. odd Tk
            # state when launched mid-pipeline).
            if out_path and Path(out_path).exists():
                edited = Path(out_path)
            else:
                edited = _find_existing_edit(scene_dir)
            if edited is None:
                print(
                    f"\n[object-gen] Editor closed without saving scene #{idx}. "
                    f"Re-run to resume."
                )
                sys.exit(0)

        print(f"[object-gen]   edited result = {edited.name}")

        # Image → graded + Ken-Burns animated later; MP4 → left untouched.
        entries = [{str(edited): round(duration, 3)}]
        if text in by_script:
            final_data[by_script[text]]["footage"] = entries
        else:
            final_data.append({"script_text": text, "footage": entries})
            by_script[text] = len(final_data) - 1
        add_local_paths_to_history({text: entries})
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"[object-gen]   OK {edited.name} (trim {round(duration, 3)}s)")

    print("\n" + "=" * 70)
    print(f"[object-gen] DONE — edited {len(og_texts)} scene(s)")
    print("=" * 70)
    return final_data
