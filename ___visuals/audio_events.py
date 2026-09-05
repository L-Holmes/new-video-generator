"""
Build the per-scene audio-events map (SFX + music) the stitcher consumes,
resolving per-scene `sfx` overrides and the joint-type auto-injected defaults.
"""

from __future__ import annotations

# Allow running this file directly from the repo root (uv run ___visuals/audio_events.py).
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))


from CONFIG import (
    JOINT_TYPE_SFX_MAP,
    MUSIC_VOLUME,
    SFX_VOLUME,
    SOUND_EFFECTS_DIR,
    SearchTermData,
    scene_is_grouped,
)


# === BEGIN verbatim move from main.py (audio events) ===
# ===========================================================================
# AUDIO EVENTS
# ===========================================================================


def build_audio_events_map(
    script_to_search_term: dict[str, SearchTermData],
) -> dict[str, list[dict]]:
    """
    Resolve per-scene audio events from the JSON.

    Priority for SFX:
      1. Per-scene `sfx` field if not "none"
      2. Grouped-scene default from JOINT_TYPE_SFX_MAP (auto-injected for
         GROUPED scenes only, keyed by their base media_type — a plain
         stock scene never gets the pop)
      3. Nothing
    """
    print("\n" + "=" * 70)
    print("[audio events] BUILDING audio events map")
    print(f"[audio events] {len(script_to_search_term)} scene(s) to process")
    print(
        f"[audio events] hardcoded SFX_VOLUME={SFX_VOLUME}, MUSIC_VOLUME={MUSIC_VOLUME}"
    )
    print("=" * 70)

    out: dict[str, list[dict]] = {}

    def _is_none(value) -> bool:
        return value in (None, "none", "None", "")

    for script_text, scene_data in script_to_search_term.items():
        events: list[dict] = []
        short = script_text[:60]
        print(
            f"\n[audio events] scene: '{short}{'...' if len(script_text) > 60 else ''}'"
        )

        media_type = scene_data.get("media_type")

        # ── SFX resolution ──────────────────────────────────────────
        user_sfx = scene_data.get("sfx", "none")

        if not _is_none(user_sfx):
            timing = scene_data.get("sfx_timing", "loop_start")
            sfx_path = str(SOUND_EFFECTS_DIR / user_sfx)
            events.append(
                {
                    "type": "sfx",
                    "path": sfx_path,
                    "timing": timing,
                    "_debug": f"user-defined sfx '{user_sfx}'",
                }
            )
            print(f"[audio events]   + SFX (user): {user_sfx} @ {timing}")
        else:
            if scene_is_grouped(scene_data) and media_type in JOINT_TYPE_SFX_MAP:
                default = JOINT_TYPE_SFX_MAP[media_type]
                sfx_path = str(SOUND_EFFECTS_DIR / default["path"])
                events.append(
                    {
                        "type": "sfx",
                        "path": sfx_path,
                        "timing": default["timing"],
                        "_debug": f"auto-injected for grouped {media_type.value}",
                    }
                )
                print(
                    f"[audio events]   + SFX (auto, grouped {media_type.value}): "
                    f"{default['path']} @ {default['timing']}"
                )
            else:
                print(f"[audio events]   (no SFX for this scene)")

        # ── Music resolution ────────────────────────────────────────
        user_music = scene_data.get("music", "none")

        if not _is_none(user_music):
            trim_raw = float(scene_data.get("music_trim_seconds", 0))
            trim = None if trim_raw == 0 else trim_raw

            fade_raw = float(scene_data.get("music_fade_out", 0))
            fade = fade_raw

            music_path = str(SOUND_EFFECTS_DIR / user_music)
            events.append(
                {
                    "type": "music",
                    "path": music_path,
                    "timing": "scene_start",
                    "duration": trim,
                    "fade_out": fade,
                    "_debug": (
                        f"user-defined music '{user_music}' (trim={trim}, fade={fade}s)"
                    ),
                }
            )
            print(f"[audio events]   + MUSIC: {user_music} trim={trim} fade={fade}s")
        else:
            print(f"[audio events]   (no music for this scene)")

        if events:
            out[script_text] = events

    print("\n" + "=" * 70)
    print(f"[audio events] DONE — {len(out)} scene(s) have audio events")
    print("=" * 70)

    return out
