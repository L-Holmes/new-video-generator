"""
Cinematic colour-grading pass over final_data (runs just before Ken Burns).
Grades the chosen stock footage — or every scene when
APPLY_COLOUR_GRADING_TO_ALL — through COLOUR_GRADE_ETC, with caching keyed on
(source file, preset+algorithm fingerprint).

The history identity-entry write is done by CACHE_IO.add_path_remap_to_history
in main().
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ___visuals import COLOUR_GRADE_ETC

from ___visuals.CACHE_IO import _classify_footage_path, _resolve_to_local_path
from ___visuals.CONFIG import (
    _CACHE_DIR,
    APPLY_COLOUR_GRADING_TO_ALL,
    MediaType,
    STOCK_COLOUR_GRADE_PRESET,
    SearchTermData,
    TOGGLE_STOCK_COLOUR_GRADING_ETC,
    ProgressTracker,
    MEDIA_PROPERTIES,
)


# === BEGIN verbatim move from main.py (colour grading) ===
# ===========================================================================
# CINEMATIC COLOUR GRADING (unified "shot on film at golden hour" look)
# ===========================================================================
# Runs as a pass over final_data right BEFORE Ken Burns. Stills are graded once
# (one cheap ffmpeg image op) and then KB animates the already-graded still;
# stock videos + the stock/wiki explainer & joint composites + manual stock
# placements are graded in place. The whole look lives in COLOUR_GRADE_ETC.py
# (one ffmpeg filter chain, identical for images and videos) so every piece of
# stock ends up part of the same graded "collection".
#
# Scope:
#   TOGGLE_STOCK_COLOUR_GRADING_ETC=False  -> nothing is graded.
#   APPLY_COLOUR_GRADING_TO_ALL=True       -> EVERY scene is graded.
#   otherwise                              -> only "real-world stock" scenes,
#                                             i.e. COLOUR_GRADE_STOCK_TYPES.
#
# Graded output is cached by (source file, preset+algorithm fingerprint) so
# re-runs are instant and changing the preset/algorithm transparently re-grades.

# "Real-world stock" = every type that fetches external candidates (Pexels
# videos+images, Wikipedia stills, the stock/wiki explainer composites, joint
# collages built from stock, manual stock placement). Defined off
# NEEDS_EXTERNAL_CANDIDATES so any new stock type is covered automatically. AI
# stickman / ai_edit / read-out / maps / pure text overlays are intentionally
# excluded so the film look doesn't fight the illustrated/synthetic styling —
# flip APPLY_COLOUR_GRADING_TO_ALL to grade those too. To drop a specific stock
# type (e.g. JOINT_3_ROW), subtract it from this set.
COLOUR_GRADE_STOCK_TYPES: set[MediaType] = {
    mt for mt, p in MEDIA_PROPERTIES.items() if p.needs_external_candidates
}

COLOUR_GRADE_CACHE_DIR = Path(f"{_CACHE_DIR}/colour_graded")


def _colour_grade_cache_path(local_path: str, fingerprint: str, is_video: bool) -> Path:
    """Stable cache filename keyed on (source file, grade fingerprint)."""
    clean = local_path.split("?", 1)[0]
    ext = ".mp4" if is_video else (Path(clean).suffix.lower() or ".jpg")
    h = hashlib.md5(f"{local_path}|{fingerprint}".encode()).hexdigest()[:16]
    return COLOUR_GRADE_CACHE_DIR / f"cg-{fingerprint}-{h}{ext}"


def apply_colour_grading_to_final_data(
    final_data: list[dict],
    script_to_search_term: dict[str, SearchTermData],
) -> tuple[list[dict], dict[str, str]]:
    """
    Give the CHOSEN footage one unified cinematic film grade.

    Walks final_data and, for every grade-eligible scene, replaces each image /
    video footage entry with a graded copy (cached). Eligibility is decided per
    scene from its MediaType (COLOUR_GRADE_STOCK_TYPES / APPLY_COLOUR_GRADING_TO_ALL).

    Returns (final_data, path_remap) where path_remap is {old_key: graded_path}
    so the caller can register identity entries in history.json.
    """
    print("\n" + "=" * 70)
    print("[colour-grade] CINEMATIC GRADE over final_data")
    print(
        f"[colour-grade] enabled={TOGGLE_STOCK_COLOUR_GRADING_ETC} "
        f"all={APPLY_COLOUR_GRADING_TO_ALL} preset={STOCK_COLOUR_GRADE_PRESET!r}"
    )
    print("=" * 70)

    if not TOGGLE_STOCK_COLOUR_GRADING_ETC:
        print("[colour-grade] TOGGLE_STOCK_COLOUR_GRADING_ETC=False — skipping")
        return final_data, {}

    try:
        fingerprint = COLOUR_GRADE_ETC.preset_fingerprint(STOCK_COLOUR_GRADE_PRESET)
    except KeyError as exc:
        print(f"[colour-grade] {exc} — skipping (fix STOCK_COLOUR_GRADE_PRESET)")
        return final_data, {}

    def _eligible(script_text: str) -> bool:
        if APPLY_COLOUR_GRADING_TO_ALL:
            return True
        st = script_to_search_term.get(script_text, {}).get("search_type")
        return st in COLOUR_GRADE_STOCK_TYPES

    # Pre-scan so the progress bar has an accurate total.
    to_grade = 0
    for entry in final_data:
        if not _eligible(entry.get("script_text", "")):
            continue
        for footage_item in entry.get("footage", []):
            for path in footage_item:
                if _classify_footage_path(path) in ("image", "video"):
                    to_grade += 1

    if to_grade == 0:
        print("[colour-grade] no eligible footage in final_data — nothing to do")
        return final_data, {}

    print(
        f"[colour-grade] grading {to_grade} footage file(s)  "
        f"[stock types: {sorted(t.value for t in COLOUR_GRADE_STOCK_TYPES)}]"
    )
    COLOUR_GRADE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tracker = ProgressTracker(total=to_grade, label="COLOUR GRADE")
    path_remap: dict[str, str] = {}
    n_graded = n_cached = n_skipped = n_failed = 0

    for entry in final_data:
        eligible = _eligible(entry.get("script_text", ""))
        new_footage: list[dict] = []
        for footage_item in entry.get("footage", []):
            new_item: dict = {}
            for path, trim in footage_item.items():
                kind = _classify_footage_path(path)
                if not eligible or kind not in ("image", "video"):
                    new_item[path] = trim
                    continue

                local_path = _resolve_to_local_path(path)
                if not local_path:
                    print(
                        f"\n[colour-grade] WARNING: can't resolve to disk: {path} "
                        f"— keeping original"
                    )
                    new_item[path] = trim
                    n_skipped += 1
                    tracker.tick()
                    continue

                is_vid = kind == "video"
                out = _colour_grade_cache_path(local_path, fingerprint, is_vid)
                if out.exists() and out.stat().st_size > 1024:
                    n_cached += 1
                else:
                    try:
                        COLOUR_GRADE_ETC.grade_media(
                            local_path,
                            str(out),
                            preset=STOCK_COLOUR_GRADE_PRESET,
                        )
                        n_graded += 1
                    except Exception as exc:
                        print(
                            f"\n[colour-grade] ERROR grading {local_path}: {exc} "
                            f"— keeping original"
                        )
                        new_item[path] = trim
                        n_failed += 1
                        tracker.tick()
                        continue

                new_item[str(out)] = trim
                path_remap[path] = str(out)
                tracker.tick()
            new_footage.append(new_item)
        entry["footage"] = new_footage

    tracker.finish()
    print(
        f"[colour-grade] DONE — graded={n_graded}, cached={n_cached}, "
        f"skipped={n_skipped}, failed={n_failed}"
    )
    return final_data, path_remap
