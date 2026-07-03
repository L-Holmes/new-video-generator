"""
MODIFIER LAYERS — decorate / caption stacked on a scene's OWN footage.

The tagging tool lets any base media type carry stackable modifiers. Two
combos route through DEDICATED legacy types and are handled by the existing
stages exactly as before (this pass never touches them):

    hold_previous + decorate  ->  MediaType.DECORATE_PREVIOUS
    hold_previous + caption   ->  MediaType.STICKMAN_TEXT_OVERLAY

Every OTHER base + decorate/caption combo (stock + caption, wikipedia +
decorate, ...) reaches the renderer as the BASE legacy type plus leftover
modifiers in the row's `modifiers` column. This pass runs over final_data
AFTER the scene's own footage is fully resolved (post review / ai_edit /
generators / previous-image stages) and BEFORE colour grade + Ken Burns,
and applies those leftovers as layers on the scene's own image:

  caption  — the caption text is composited onto the scene's own footage
             with the SAME renderer the stickman_text_overlay type uses
             (MAKE_TEXT_OVERLAY), output as a static MP4 so Ken Burns
             leaves the tilted caption alone. The caption text is the
             row's `caption_text` field if present, else its search_term
             (for bases that don't use the term for fetching, the term IS
             the caption — same contract as the legacy overlay type).

  decorate — opens your interactive decorate editor on the scene's own
             image. The editor lives outside this module; point
             DECORATE_LAYER_HOOK at it (one line, signature below). Until
             it's wired, decorate layers print a clear notice and leave
             the footage unchanged — nothing breaks.

Returns (final_data, path_remap) like the other passes so main() registers
history identity entries and re-saves the clip map.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from ___visuals.CACHE_IO import _resolve_to_local_path
from ___visuals.CONFIG import (
    MODIFIER_LAYER_OUTPUT_DIR,
    MODIFIER_LAYER_RENDER_SAFETY_PAD_SEC,
    SearchTermData,
    scene_residual_modifiers,
)
from ___visuals.TIMING_MERGE import _load_scene_timings

# ---------------------------------------------------------------------------
# DECORATE HOOK — wire your interactive decorate editor here (one line).
# Signature: fn(base_local_path: str, output_mp4_path: str, duration: float)
# It must write a video (or still) to output_mp4_path. Example:
#     from ___visuals.STATIC_RENDER import my_decorate_editor
#     DECORATE_LAYER_HOOK = my_decorate_editor
# ---------------------------------------------------------------------------
DECORATE_LAYER_HOOK: Callable[[str, str, float], None] | None = None


def _safe_stem(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:50] or "scene"


def apply_modifier_layers_to_final_data(
    final_data: list[dict],
    script_to_search_term: dict[str, SearchTermData],
) -> tuple[list[dict], dict[str, str]]:
    """Apply every scene's leftover decorate/caption modifiers as layers on
    its OWN finished footage. In-place on final_data; returns it plus a
    {old_path: new_path} remap for history registration."""
    print("\n" + "=" * 70)
    print("[modifier layers] decorate / caption layers on own footage")
    print("=" * 70)

    # Which scenes actually have leftover layers?
    layered: list[tuple[str, list[str]]] = []
    for txt, row in script_to_search_term.items():
        residual = scene_residual_modifiers(row)
        if residual:
            layered.append((txt, residual))
    if not layered:
        print("[modifier layers] no scenes with leftover modifiers — skipping")
        return final_data, {}

    print(f"[modifier layers] {len(layered)} scene(s) carry layers: "
          + ", ".join(f"'{t[:40]}' +{'+'.join(m)}" for t, m in layered))

    from ___visuals.MAKE_TEXT_OVERLAY import make_text_overlay  # lazy import

    scene_timings = _load_scene_timings()
    final_by_text = {e["script_text"]: e for e in final_data}
    MODIFIER_LAYER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    path_remap: dict[str, str] = {}
    for idx, (txt, residual) in enumerate(layered):
        entry = final_by_text.get(txt)
        footage = (entry or {}).get("footage") or []
        key = next(iter(footage[0]), None) if footage else None
        base_local = _resolve_to_local_path(key) if key else None
        if not base_local:
            print(f"[modifier layers] WARNING: no resolved footage for "
                  f"'{txt[:60]}' — cannot layer {residual}; leaving as-is")
            continue

        duration = float(scene_timings.get(txt, 0.0))
        if duration <= 0:
            print(f"[modifier layers] WARNING: no/zero timing for '{txt[:60]}' "
                  f"— leaving as-is")
            continue
        render_duration = duration + MODIFIER_LAYER_RENDER_SAFETY_PAD_SEC
        row = script_to_search_term[txt]
        current_base = base_local

        # Layer order: decorate first (draw on the image), caption on top.
        if "decorate" in residual:
            out = str(MODIFIER_LAYER_OUTPUT_DIR
                      / f"decorate_{idx:03d}_{_safe_stem(txt)}.mp4")
            if DECORATE_LAYER_HOOK is None:
                print(
                    f"[modifier layers] NOTE: '{txt[:50]}' wants a decorate "
                    f"layer, but DECORATE_LAYER_HOOK isn't wired yet — see "
                    f"MODIFIER_STAGE.py (one line). Footage left unchanged. "
                    f"(hold_previous + decorate works today via "
                    f"decorate_previous.)"
                )
            else:
                DECORATE_LAYER_HOOK(current_base, out, render_duration)
                current_base = out

        if "caption" in residual:
            caption = (row.get("caption_text") or row.get("search_term") or "").strip()
            if not caption:
                print(f"[modifier layers] WARNING: caption layer on "
                      f"'{txt[:50]}' has no text (caption_text/search_term "
                      f"both empty) — skipping the caption")
            else:
                out = str(MODIFIER_LAYER_OUTPUT_DIR
                          / f"caption_{idx:03d}_{_safe_stem(txt)}.mp4")
                make_text_overlay(
                    base_image_path=current_base,
                    text=caption,
                    output_path=out,
                    duration=render_duration,
                    seed=txt,  # deterministic position/tilt per scene
                )
                current_base = out

        if current_base != base_local:
            entry["footage"] = [{current_base: round(duration, 3)}]
            if key:
                path_remap[key] = current_base
            print(f"[modifier layers]   ✓ '{txt[:50]}' → "
                  f"{Path(current_base).name} (trim {round(duration, 3)}s)")

    print(f"[modifier layers] DONE — {len(path_remap)} scene(s) re-layered")
    return final_data, path_remap
