"""
Previous-entry preview helpers shared by the stock review GUI and the
interactive decorator.

A hold-previous/decorate scene often depends on the image from the line before
it.  This module keeps the lookup + small collapsible Tk popup in one place so
both review and decorate show the same cue.
"""

from __future__ import annotations

# Allow running/importing this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import hashlib
import subprocess
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageTk

from ___visuals.CACHE_IO import _resolve_to_local_path

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}


def _classify_path(path: str) -> str:
    clean = path.split("?", 1)[0].split("#", 1)[0]
    suffix = Path(clean).suffix.lower()
    if suffix in _IMAGE_EXTS:
        return "image"
    if suffix in _VIDEO_EXTS:
        return "video"
    return "other"


try:
    _RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # Pillow < 9.1
    _RESAMPLE = Image.LANCZOS


@dataclass
class PreviousEntryPreview:
    text: str
    image_path: str | None = None


def _row_media_name(row: dict[str, Any] | None) -> str:
    row = row or {}
    media_type = row.get("_stamp_orig_type") or row.get("media_type")
    return str(getattr(media_type, "value", media_type) or "")


def row_uses_previous_context(row: dict[str, Any] | None) -> bool:
    """True for rows where seeing the prior scene is especially important.

    The popup can still be shown more broadly by callers; this predicate is kept
    for places that only want to gate expensive/contextual work.
    """
    row = row or {}
    name = _row_media_name(row)
    return (
        name in {"hold_previous", "ai_edit_previous"}
        or bool(row.get("stamp_source"))
        or "decorate" in (row.get("modifiers") or [])
    )


def row_is_hold_previous(row: dict[str, Any] | None) -> bool:
    """True when this row's original scene media is exactly hold_previous."""
    return _row_media_name(row) == "hold_previous"


def previous_text_for(
    current_text: str,
    script_to_search_term: dict[str, Any] | None,
) -> str | None:
    """Return the immediate previous script entry text, preserving JSON order."""
    if not script_to_search_term:
        return None
    prev: str | None = None
    for text in script_to_search_term:
        if text == current_text:
            return prev
        prev = text
    return None


def _resolve_local_from_history(key: str, history_map: dict | None) -> str | None:
    if not key:
        return None
    if key.startswith(("http://", "https://")):
        local = (history_map or {}).get(key)
        return local if local and Path(local).exists() else None
    return key if Path(key).exists() else _resolve_to_local_path(key)


def _extract_first_frame(video_path: str, output_path: str) -> str | None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        "0.000",
        "-i",
        video_path,
        "-frames:v",
        "1",
        output_path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and Path(output_path).exists():
            return output_path
        # Some files are more reliable when ffmpeg seeks after opening.
        res = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                video_path,
                "-frames:v",
                "1",
                output_path,
            ],
            capture_output=True,
            text=True,
        )
        return (
            output_path if res.returncode == 0 and Path(output_path).exists() else None
        )
    except Exception:
        return None


def preview_image_from_footage(
    footage: list[dict] | None,
    *,
    history_map: dict | None = None,
    frame_dir: str | Path | None = None,
    frame_stem: str = "previous",
) -> str | None:
    """Resolve the first footage item to an image path.

    Images are returned directly. Videos are converted to a cached first-frame
    PNG. If anything cannot be resolved, returns None so callers can still show
    the previous text.
    """
    if not footage:
        return None
    try:
        key = next(iter((footage[0] or {}).keys()))
    except Exception:
        return None
    if not key:
        return None

    local = _resolve_local_from_history(str(key), history_map)
    if not local:
        return None
    kind = _classify_path(local)
    if kind == "image":
        return local
    if kind != "video":
        return None

    out_dir = Path(frame_dir or Path(local).parent)
    digest = hashlib.md5(str(local).encode("utf-8")).hexdigest()[:10]
    out = out_dir / f"{frame_stem}_{digest}_first_frame.png"
    if out.exists() and out.stat().st_size > 0:
        return str(out)
    return _extract_first_frame(local, str(out))


def _footage_from_final_data(text: str, final_data: list[dict] | None):
    for entry in final_data or []:
        if entry.get("script_text") == text:
            return entry.get("footage")
    return None


def _footage_from_review_state(text: str, review_state: dict | None):
    entry = (review_state or {}).get(text)
    return entry.get("footage") if isinstance(entry, dict) else None


def _footage_from_candidates(text: str, candidates_data: list[dict] | None):
    for item in candidates_data or []:
        if item.get("script_text") != text:
            continue
        cands = item.get("candidates", {}) or {}
        # Prefer videos first because the review UI presents video slots first,
        # then stills. This is only a fallback when no accepted pick exists.
        for group in (cands.get("videos") or [], cands.get("images") or []):
            for entry in group:
                if entry:
                    return [entry]
    return None


def _visual_for_text(
    text: str,
    *,
    final_data: list[dict] | None,
    review_state: dict | None,
    candidates_data: list[dict] | None,
    history_map: dict | None,
    frame_dir: str | Path | None,
    frame_stem: str,
) -> str | None:
    """Find the best known image for this exact script row (no walking back).

    Tries final_data (the resolved pick), then review_state (an in-progress
    review's committed pick), then candidates_data (falls back to the first
    offered candidate if nothing was committed yet). Returns None if this exact
    row has no resolvable visual in any of them — callers that want "the image
    that was on screen" for a row with no picture of its own (hold_previous,
    connective beats, ...) should walk backwards themselves; see
    _resolve_previous_visual, which is what the previous-entry popup actually
    uses. The popup's TEXT is always the immediate previous entry regardless.
    """
    for footage in (
        _footage_from_final_data(text, final_data),
        _footage_from_review_state(text, review_state),
        _footage_from_candidates(text, candidates_data),
    ):
        image = preview_image_from_footage(
            footage,
            history_map=history_map,
            frame_dir=frame_dir,
            frame_stem=frame_stem,
        )
        if image:
            return image
    return None


def _resolve_previous_visual(
    prev_text: str,
    script_to_search_term: dict[str, Any] | None,
    *,
    final_data: list[dict] | None,
    review_state: dict | None,
    candidates_data: list[dict] | None,
    history_map: dict | None,
    frame_dir: str | Path | None,
    frame_stem: str,
) -> str | None:
    """The image that was actually ON SCREEN during `prev_text`.

    Many entries carry no picture of their own — hold_previous rows, short
    connective beats, an unreviewed/mid-review scene — they just reuse whatever
    was already showing. So "the previous image" isn't necessarily attached to
    the previous entry itself; it's whatever the most recent entry AT OR BEFORE
    it actually resolved. This walks backwards in script order (starting at
    prev_text) trying every source (final_data -> review_state -> candidates)
    at each step, and returns the first image found. Returns None only if
    nothing before it (all the way to the start of the script) has resolved a
    visual yet.
    """
    order = list(script_to_search_term or {})
    if prev_text in order:
        walk_back_through = reversed(order[: order.index(prev_text) + 1])
    else:
        # Not in the map (shouldn't normally happen) — just probe it directly.
        walk_back_through = [prev_text]

    for text in walk_back_through:
        image = _visual_for_text(
            text,
            final_data=final_data,
            review_state=review_state,
            candidates_data=candidates_data,
            history_map=history_map,
            frame_dir=frame_dir,
            frame_stem=frame_stem,
        )
        if image:
            return image
    return None


def build_previous_preview(
    current_text: str,
    script_to_search_term: dict[str, Any] | None,
    *,
    final_data: list[dict] | None = None,
    review_state: dict | None = None,
    candidates_data: list[dict] | None = None,
    history_map: dict | None = None,
    frame_dir: str | Path | None = None,
    frame_stem: str = "previous",
    fallback_image_path: str | None = None,
) -> PreviousEntryPreview | None:
    """Build a preview for the entry directly before current_text.

    The text is always the immediate previous JSON entry. The image is the
    picture that was on screen during that entry — resolved by walking
    backwards (see _resolve_previous_visual) so hold_previous/connective beats
    with no picture of their own still surface the right image, for EVERY
    current row (not just hold_previous ones) so plain stock/decorate scenes
    get useful context too. A caller-supplied fallback (e.g. a hold_previous
    decorate scene's own canvas, which already IS the previous image) is only
    used as an absolute last resort, and only for hold_previous current rows,
    so it never mislabels an unrelated scene's own image as "previous".
    """
    prev_text = previous_text_for(current_text, script_to_search_term)
    if not prev_text:
        return None

    image = _resolve_previous_visual(
        prev_text,
        script_to_search_term,
        final_data=final_data,
        review_state=review_state,
        candidates_data=candidates_data,
        history_map=history_map,
        frame_dir=frame_dir,
        frame_stem=frame_stem,
    )
    if not image and fallback_image_path and Path(fallback_image_path).exists():
        current_row = (script_to_search_term or {}).get(current_text, {})
        if row_is_hold_previous(current_row):
            image = fallback_image_path

    return PreviousEntryPreview(text=prev_text, image_path=image)


class PreviousEntryPreviewPopup:
    """Small bottom-left, collapsible Tk popup for a previous scene cue."""

    def __init__(
        self,
        parent,
        *,
        bg: str = "#101016",
        panel_bg: str = "#16213e",
        text_fg: str = "#eaeaea",
        hint_fg: str = "#a7a7b2",
        accent: str = "#e94560",
        expanded_width: int = 320,
        image_size: tuple[int, int] = (290, 165),
        x: int = 6,
        y: int = -10,
    ):
        self.parent = parent
        self.bg = bg
        self.panel_bg = panel_bg
        self.text_fg = text_fg
        self.hint_fg = hint_fg
        self.accent = accent
        self.expanded_width = expanded_width
        self.image_size = image_size
        self.x = x
        self.y = y
        self.preview: PreviousEntryPreview | None = None
        self.collapsed = False
        self._photo = None

        self.frame = tk.Frame(
            parent,
            bg=panel_bg,
            bd=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground="#303044",
        )
        self.toggle_btn = tk.Label(
            self.frame,
            text="◀",
            width=2,
            bg=panel_bg,
            fg=hint_fg,
            font=("Segoe UI", 16, "bold"),
            cursor="hand2",
            padx=3,
            pady=0,
        )
        self.toggle_btn.bind("<Button-1>", lambda _e: self.toggle())
        self.content = tk.Frame(self.frame, bg=panel_bg, padx=8, pady=7)

        header = tk.Frame(self.content, bg=panel_bg)
        header.pack(fill="x")
        tk.Label(
            header,
            text="Previous entry",
            bg=panel_bg,
            fg=accent,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")

        self.text_label = tk.Label(
            self.content,
            text="",
            bg=panel_bg,
            fg=text_fg,
            justify="left",
            wraplength=expanded_width - 30,
            font=("Segoe UI", 9),
        )
        self.text_label.pack(anchor="w", fill="x", pady=(4, 6))

        self.image_frame = tk.Frame(
            self.content,
            width=image_size[0],
            height=image_size[1],
            bg=bg,
        )
        self.image_frame.pack_propagate(False)
        self.image_label = tk.Label(self.image_frame, text="", bg=bg)
        self.image_label.pack(expand=True, fill="both")

    def set_preview(self, preview: PreviousEntryPreview | None) -> None:
        self.preview = preview
        self._photo = None
        if not preview:
            self.hide()
            return
        self.text_label.config(text=f"“{preview.text}”")
        self._load_image(preview.image_path)
        self.show()

    def _load_image(self, image_path: str | None) -> None:
        if not image_path:
            self._hide_image()
            return
        try:
            im = Image.open(image_path).convert("RGB")
            im.thumbnail(self.image_size, _RESAMPLE)
            self._photo = ImageTk.PhotoImage(im)
            self.image_label.config(image=self._photo, text="")
            self.image_label._img_ref = self._photo
            if not self.image_frame.winfo_ismapped():
                self.image_frame.pack(anchor="w", fill="x")
        except Exception:
            # Do not show clipped technical text in the UI. If the image cannot
            # be displayed, the popup simply becomes text-only.
            self._hide_image()

    def _hide_image(self) -> None:
        self._photo = None
        self.image_label.config(image="", text="")
        self.image_label._img_ref = None
        try:
            self.image_frame.pack_forget()
        except tk.TclError:
            pass

    def show(self) -> None:
        if not self.preview:
            return
        self._layout()
        try:
            self.frame.lift()
        except tk.TclError:
            pass

    def hide(self) -> None:
        try:
            self.frame.place_forget()
        except tk.TclError:
            pass

    def toggle(self) -> None:
        self.collapsed = not self.collapsed
        self._layout()
        try:
            self.frame.lift()
        except tk.TclError:
            pass

    def _layout(self) -> None:
        # Collapse/expand swaps the ONE placed frame's own contents for the
        # arrow button — no separate popup window. A prior version spawned a
        # detached overrideredirect Toplevel as the "expand" arrow when
        # collapsed; those rarely receive clicks reliably (focus/stacking
        # quirks vary by window manager), which is why expanding silently did
        # nothing. Keeping everything in the single, always-placed frame
        # means the same click binding works both ways every time.
        for child in self.frame.winfo_children():
            child.pack_forget()

        if self.collapsed:
            self.toggle_btn.config(text="▶")
            self.toggle_btn.pack(side="left", fill="y", padx=0, pady=0)
        else:
            self.toggle_btn.config(text="◀")
            self.content.pack(side="left", fill="both", expand=True)
            self.toggle_btn.pack(side="right", fill="y", padx=0, pady=0)

        self.frame.place(x=self.x, rely=1.0, y=self.y, anchor="sw")
