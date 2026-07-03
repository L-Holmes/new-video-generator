"""
COLLAGE STAGE — the `collage` modifier: SEVERAL review picks on ONE line,
composed together. After picking, you choose per scene:

  auto collage      — the picks are scattered with overlaps, slight tilts,
                      white borders and shadows onto a plain background
                      (decorator/auto_collage.py — zero interaction)
  stamp it yourself — the picks load into the decorate editor as stamps and
                      you place each one by hand (the old manual stock
                      placement GUI, generalised)

Runs right after the decorate stage (2.646), before colour grade + Ken
Burns. Output is a static MP4 so KB never crops the composition.

NOTE for the review side: collage rows need MULTIPLE image picks. The
candidate-bundle builder (___visuals/DOWNLOADS.py — not shared yet) should
set num_clips_needed for collage rows so the review GUI collects several;
until then this stage uses however many image entries the scene's footage
holds and asks for at least two.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import re
from pathlib import Path

from PIL import Image

from ___visuals.CACHE_IO import _resolve_to_local_path
from ___visuals.CONFIG import (
    IMAGE_EXTENSIONS,
    COLLAGE_BACKGROUND,
    COLLAGE_OUTPUT_DIR,
    COLLAGE_RENDER_SAFETY_PAD_SEC,
    SearchTermData,
    scene_wants_collage,
)
from ___visuals.MANUAL_STOCK_PLACEMENT import extract_frame
from ___visuals.TIMING_MERGE import _load_scene_timings
from ___visuals.decorator import auto_collage, run_decorator
from ___visuals.decorator.auto_collage import CANVAS_H, CANVAS_W, DEFAULT_BG


def _is_image(path: str) -> bool:
    from pathlib import Path as _P
    return _P(path).suffix.lower() in IMAGE_EXTENSIONS


def _safe_stem(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:50] or "scene"


def _choose_mode(txt: str, n: int) -> str:
    """'auto' or 'stamp' — tk buttons, terminal fallback."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title("collage")
        root.configure(bg="#1e1e24")
        choice = {"v": "auto"}
        tk.Label(root, text=f"{n} picks for:\n{txt[:70]}", bg="#1e1e24",
                 fg="#dddddd", font=("Arial", 11, "bold"),
                 justify="left").pack(padx=18, pady=(14, 8))

        def pick(v):
            choice["v"] = v
            root.destroy()

        tk.Button(root, text="auto collage — scatter them for me", width=36,
                  command=lambda: pick("auto")).pack(padx=18, pady=3)
        tk.Button(root, text="stamp it yourself — place each by hand",
                  width=36, command=lambda: pick("stamp")).pack(padx=18,
                                                                pady=(3, 14))
        root.lift()
        root.attributes("-topmost", True)
        root.mainloop()
        return choice["v"]
    except Exception:
        ans = input(f"[collage] '{txt[:40]}': auto or stamp? ").strip().lower()
        return "stamp" if ans.startswith("s") else "auto"


def _blank_background(out_dir: Path) -> str:
    """A plain-card base for stamp-yourself mode when COLLAGE_BACKGROUND
    isn't an image file."""
    if COLLAGE_BACKGROUND and not str(COLLAGE_BACKGROUND).startswith("#") \
            and Path(str(COLLAGE_BACKGROUND)).exists():
        return str(COLLAGE_BACKGROUND)
    path = out_dir / "collage_blank_bg.png"
    if not path.exists():
        Image.new("RGB", (CANVAS_W, CANVAS_H),
                  str(COLLAGE_BACKGROUND or DEFAULT_BG)).save(path)
    return str(path)


def run_collage_stage(
    final_data: list[dict],
    script_to_search_term: dict[str, SearchTermData],
) -> tuple[list[dict], dict[str, str]]:
    print("\n" + "=" * 70)
    print("[collage] scenes carrying the collage modifier")
    print("=" * 70)

    wanting = [txt for txt, row in script_to_search_term.items()
               if scene_wants_collage(row)]
    if not wanting:
        print("[collage] no collage scenes — skipping")
        return final_data, {}

    from ___visuals.STATIC_RENDER import _render_image_to_static_mp4  # lazy

    scene_timings = _load_scene_timings()
    final_by_text = {e["script_text"]: e for e in final_data}
    COLLAGE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path_remap: dict[str, str] = {}

    for idx, txt in enumerate(wanting):
        entry = final_by_text.get(txt) or {}
        stem = f"collage_{idx:03d}_{_safe_stem(txt)}"

        # every picked footage item, resolved to a local IMAGE
        images: list[str] = []
        for f_i, item in enumerate(entry.get("footage") or []):
            key = next(iter(item), None)
            local = _resolve_to_local_path(key) if key else None
            if not local:
                continue
            if not _is_image(local):
                local = extract_frame(
                    local, str(COLLAGE_OUTPUT_DIR / f"{stem}_pick{f_i}.png"))
            images.append(local)

        if len(images) < 2:
            print(f"[collage] WARNING: '{txt[:55]}' has {len(images)} usable "
                  f"pick(s) — a collage needs at least 2 (did the review "
                  f"stage collect several?). Leaving as-is.")
            continue
        duration = float(scene_timings.get(txt, 0.0))
        if duration <= 0:
            print(f"[collage] WARNING: no/zero timing for '{txt[:60]}' "
                  f"— leaving as-is")
            continue

        print(f"\n[collage] [{idx + 1}/{len(wanting)}] '{txt[:60]}' "
              f"— {len(images)} picks")
        png = str(COLLAGE_OUTPUT_DIR / f"{stem}.png")
        if _choose_mode(txt, len(images)) == "auto":
            auto_collage(images, png, background=COLLAGE_BACKGROUND, seed=txt)
        else:
            result = run_decorator(
                base_image_path=_blank_background(COLLAGE_OUTPUT_DIR),
                out_path=png,
                stamps=images,
                title=f"collage: {txt[:40]}",
                tools=("stamp", "zoom"),
            )
            if not result:
                print("[collage]   no edits made — falling back to auto")
                auto_collage(images, png, background=COLLAGE_BACKGROUND,
                             seed=txt)

        mp4 = str(COLLAGE_OUTPUT_DIR / f"{stem}.mp4")
        _render_image_to_static_mp4(
            png, duration + COLLAGE_RENDER_SAFETY_PAD_SEC, mp4)
        old_key = next(iter(entry["footage"][0]))
        entry["footage"] = [{mp4: round(duration, 3)}]
        path_remap[old_key] = mp4
        print(f"[collage]   ✓ {Path(mp4).name} (trim {round(duration, 3)}s)")

    print(f"\n[collage] DONE — {len(path_remap)} collage scene(s)")
    return final_data, path_remap
