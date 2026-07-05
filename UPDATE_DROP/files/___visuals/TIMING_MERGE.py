"""
Scene-timing lookups, joint-stage timing maths, and the generator-agnostic
merge that folds locally-produced footage back into final_data.

`integrate_generated_footage` collapses the merge → history → cache-save block
that main() used to repeat once per local-footage stage.
"""

from __future__ import annotations

# Allow running this file directly from the repo root (uv run ___visuals/TIMING_MERGE.py).
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import sys
from pathlib import Path

from ___visuals.CACHE_IO import add_local_paths_to_history, save_to_cache
from ___visuals.CONFIG import (
    FINAL_SCRIPT_AND_CLIPS,
    JOINT_INTRO_DURATION_SEC,
    JOINT_MIN_SCENE_DURATION_FOR_TRANSITION_SEC,
    SYNCHRONIZED_SCRIPT_OUTPUT_FILE,
)


# === BEGIN verbatim move from main.py (scene-timing + merge helpers) ===
def _load_scene_timings() -> dict[str, float]:
    """Return {script_text → runtime_seconds} from the audio-sync output."""
    p = Path(SYNCHRONIZED_SCRIPT_OUTPUT_FILE)
    print(f"\n[timings] loading scene timings from {p}")
    if not p.exists():
        print(f"[timings] FATAL: timings file missing: {p}")
        print("[timings]   (did run_audio_script_synchronizer run?)")
        sys.exit(1)
    timings = {k: float(v) for k, v in json.loads(p.read_text()).items()}
    print(f"[timings] loaded {len(timings)} timing entries")
    return timings


def _compute_joint_stage_timing(
    script_text: str,
    scene_timings: dict[str, float],
) -> dict:
    """Decide how to split a single joint stage's runtime between intro and loop."""
    if script_text not in scene_timings:
        available = "\n".join(f"    - {k}" for k in scene_timings)
        print(f"[joint:timings] FATAL: no timing for joint stage:")
        print(f"   '{script_text}'")
        print(f"   Available timings:\n{available}")
        sys.exit(1)

    total = scene_timings[script_text]
    use_transition = total >= JOINT_MIN_SCENE_DURATION_FOR_TRANSITION_SEC
    intro = JOINT_INTRO_DURATION_SEC if use_transition else 0.0
    loop = max(0.0, total - intro)

    print(f"[joint:timings] '{script_text[:70]}'")
    print(f"[joint:timings]   total={total:.3f}s  use_transition={use_transition}")
    print(f"[joint:timings]   intro={intro:.3f}s  loop={loop:.3f}s")

    return {
        "script_text": script_text,
        "total_duration": total,
        "use_transition": use_transition,
        "intro_duration": intro,
        "loop_duration": loop,
    }


def _stage_file_paths(
    group_output_folder: Path,
    stage_index: int,
    num_stages: int,
) -> tuple[Path, Path]:
    """Return (intro_path, loop_path) for one stage of a joint group."""
    intro = group_output_folder / f"stage_{stage_index + 1:02d}_of_{num_stages:02d}.mp4"
    loop = (
        group_output_folder
        / f"stage_{stage_index + 1:02d}_of_{num_stages:02d}_loop.mp4"
    )
    return intro, loop


def _build_footage_entries_for_stage(
    group_output_folder: Path,
    stage_index: int,
    num_stages: int,
    timing: dict,
) -> list[dict]:
    """
    Build the {path: trim_secs} entries the stitcher plays back-to-back for
    a single joint stage.
    """
    intro_path, loop_path = _stage_file_paths(
        group_output_folder,
        stage_index,
        num_stages,
    )

    print(f"\n[joint:footage] stage {stage_index + 1}/{num_stages}")
    print(f"[joint:footage]   intro: {intro_path}  exists={intro_path.exists()}")
    print(f"[joint:footage]   loop:  {loop_path}   exists={loop_path.exists()}")
    print(f"[joint:footage]   timing: {timing}")

    if not intro_path.exists():
        print(f"[joint:footage] FATAL: expected intro file missing: {intro_path}")
        sys.exit(1)

    entries: list[dict] = []

    if timing["use_transition"]:
        if not loop_path.exists():
            print(
                f"[joint:footage] FATAL: transition stage missing loop file: {loop_path}"
            )
            sys.exit(1)

        entries.append({str(intro_path): round(timing["intro_duration"], 3)})
        print(
            f"[joint:footage]   → intro entry: {intro_path.name}  "
            f"trim={timing['intro_duration']:.3f}s"
        )

        if timing["loop_duration"] > 0.01:
            entries.append({str(loop_path): round(timing["loop_duration"], 3)})
            print(
                f"[joint:footage]   → loop  entry: {loop_path.name}  "
                f"trim={timing['loop_duration']:.3f}s"
            )
        else:
            print(f"[joint:footage]   (loop omitted — duration <= 0.01s)")
    else:
        use_path = loop_path if loop_path.exists() else intro_path
        entries.append({str(use_path): round(timing["total_duration"], 3)})
        print(
            f"[joint:footage]   → static entry: {use_path.name}  "
            f"trim={timing['total_duration']:.3f}s  (no transition: scene too short)"
        )

    print(f"[joint:footage]   total entries for this stage: {len(entries)}")
    return entries


# ===========================================================================
# GENERIC FOOTAGE-MERGE HELPERS
# ===========================================================================
# Used by every local-file generator (joint, read-out, future types). They
# don't care which generator produced the entries — they just integrate any
# script_text → footage map into the master final_data list and history.


def _merge_generated_footage_into_final_data(
    final_data: list[dict],
    generated_footage_map: dict[str, list[dict]],
    source_label: str = "generated",
) -> list[dict]:
    """
    Replace the `footage` list in `final_data` for any script_text that the
    generator produced output for. Entries not already in final_data are
    appended at the end.
    """
    print("\n" + "=" * 70)
    print(
        f"[merge:{source_label}] merging {len(generated_footage_map)} entry(ies) "
        f"into final_data"
    )
    print(
        f"[merge:{source_label}] final_data currently has {len(final_data)} entry(ies)"
    )
    print("=" * 70)

    by_script = {entry["script_text"]: i for i, entry in enumerate(final_data)}

    replaced = 0
    appended = 0

    for script_text, entries in generated_footage_map.items():
        if script_text in by_script:
            idx = by_script[script_text]
            old_count = len(final_data[idx].get("footage", []))
            final_data[idx]["footage"] = entries
            replaced += 1
            print(
                f"[merge:{source_label}] REPLACED '{script_text[:60]}...'  "
                f"(was {old_count} entry(ies), now {len(entries)})"
            )
            for e in entries:
                for path, trim in e.items():
                    print(f"[merge:{source_label}]     {Path(path).name}  trim={trim}s")
        else:
            final_data.append({"script_text": script_text, "footage": entries})
            appended += 1
            print(
                f"[merge:{source_label}] APPENDED '{script_text[:60]}...'  "
                f"({len(entries)} entry(ies))"
            )
            for e in entries:
                for path, trim in e.items():
                    print(f"[merge:{source_label}]     {Path(path).name}  trim={trim}s")

    print(
        f"\n[merge:{source_label}] done — replaced={replaced}, appended={appended}, "
        f"final_data size now {len(final_data)}"
    )
    return final_data


# === END verbatim move ===


def integrate_generated_footage(
    final_data: list[dict],
    footage_map: dict[str, list[dict]],
    *,
    source_label: str,
    produced_msg: str,
    save_label: str,
    empty_msg: str,
) -> list[dict]:
    """
    Fold a generator's {script_text → footage} map into final_data, register
    its local paths in history.json, and persist final_data — the merge →
    history → cache-save block main() used to repeat for every local-footage
    stage (local generators, explainer). Returns the (possibly
    updated) final_data; a no-op when `footage_map` is empty.
    """
    if footage_map:
        print(f"\n[main] {produced_msg}")
        final_data = _merge_generated_footage_into_final_data(
            final_data,
            footage_map,
            source_label=source_label,
        )
        add_local_paths_to_history(footage_map)
        save_to_cache(final_data, FINAL_SCRIPT_AND_CLIPS)
        print(f"💾 Updated final_data with {save_label} → {FINAL_SCRIPT_AND_CLIPS}")
    else:
        print(f"\n[main] {empty_msg}")
    return final_data
