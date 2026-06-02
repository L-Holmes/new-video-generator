"""
Multi-Candidate Media Review Tool
=================================

For each script segment, the fetch step has downloaded:
    - 2 candidate videos
    - 3 candidate images

This tool displays all 5 simultaneously:

    +----------------------------+----------------------------+
    |  (1) VIDEO                 |  (2) VIDEO                 |
    +-------------+--------------+--------------+-------------+
    |  (3) IMAGE  |  (4) IMAGE   |  (5) IMAGE                 |
    +-------------+--------------+----------------------------+

Key bindings
------------
    1 / 2          → choose video 1 / video 2
    3 / 4 / 5      → choose image 1 / 2 / 3
    E              → toggle EDIT MODE (blue outlines appear on editable images)
    F or BACKSPACE → reject all → mark scene for MANUAL INTERVENTION

Edit mode
---------
Press E to enter edit mode. Editable image slots get a blue outline and a
banner appears. Now pressing an image number (3/4/5, or a promoted image in
slots 1/2) does NOT pick it directly — instead it PAUSES review and opens that
image in KolourPaint for touch-ups, with a control bar offering:

    • Save & Continue  → saves the edit and uses it as this scene's pick,
                         then advances to the next review item.
    • Exit             → discards the edit and returns to the five options.

KolourPaint is embedded directly in the window via X11 reparenting (needs
`xdotool`). On sessions where reparenting isn't possible (or `xdotool` is
missing) it falls back to opening KolourPaint as a separate window while the
Save & Continue / Exit controls stay in this window.

Requirements for edit mode (Debian/Ubuntu):
    sudo apt install kolourpaint   # the editor itself (required)
    sudo apt install xdotool       # only needed to EMBED it in the window

If KolourPaint isn't installed, edit mode refuses to turn on and tells you how
to install it. Videos can't be edited (KolourPaint is an image editor).

Multi-clip scenes
-----------------
If `_get_num_stock_images(...)` says a scene needs 2+ clips, the picker repeats
N times for that scene. Already-chosen candidates are greyed out so the user
sees what they already picked but can't pick the same one twice.

Resuming
--------
Review state is persisted to `review_state_file` after every decision, so the
program can be killed/relaunched and pick up exactly where it left off.

Manual intervention flow
------------------------
After the GUI session, if any scenes are flagged for manual intervention,
this module prints precise instructions (drop file at PATH, edit history.json
like THIS, edit review file like THIS) and returns has_manual=True so the
caller can `sys.exit(0)`. The user fixes the JSONs, re-runs, and the program
continues.

Cleanup
-------
At the end of every GUI session we sweep all DECIDED items: any candidate URL
that was downloaded but not chosen gets its file deleted and its entry pulled
from history.json. Pending (still-undecided) items are not touched.
Edited images are NOT candidates, so cleanup never deletes them.
"""

from __future__ import annotations

import json
import os
import gc
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from uuid import uuid4

from PIL import Image, ImageTk


# ── DISPLAY CONSTANTS ─────────────────────────────────────────────────────────

WINDOW_W = 1120
WINDOW_H = 820

VIDEO_W = 460
VIDEO_H = 260
IMAGE_W = 320
IMAGE_H = 200

BG          = "#1a1a2e"
PANEL_BG    = "#16213e"
ACCENT      = "#e94560"
TEXT_COL    = "#eaeaea"
HINT_COL    = "#888"
SLOT_BG     = "#0f1626"
DISABLED_BG = "#2a2a3a"
CHOSEN_BG   = "#2ecc71"
EDIT_OUTLINE = "#3498db"   # blue ring shown on editable slots in edit mode

FONT_MONO  = ("Courier New", 13)
FONT_UI    = ("Segoe UI", 11)
FONT_LABEL = ("Segoe UI", 14, "bold")
FONT_KEY   = ("Segoe UI", 18, "bold")

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


# ── JSON / FILE HELPERS ───────────────────────────────────────────────────────

def _load_json_safe(path: str | Path, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[review] could not parse {p}: {exc}")
        return default


def _save_json(data, path: str | Path):
    """
    Atomically write `data` as JSON to `path`.

    - Writes to a sibling .tmp file first and renames over the target so a
      Ctrl-C / crash mid-write can never produce a half-written file.
    - Forces ASCII-safe output (`ensure_ascii=True`) so the file is pure
      7-bit and any downstream reader (regardless of locale or `read_text()`
      encoding choice) can decode it without surprises.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def _is_video_file(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def _resolve_local(url: str, history: dict) -> str | None:
    p = history.get(url)
    return p if p and Path(p).exists() else None


# ── EDIT-MODE / KOLOURPAINT (X11 EMBED) HELPERS ────────────────────────────────
# These shell out to `xdotool` to reparent KolourPaint's X11 window into a Tk
# frame. They only work under X11 (or XWayland — both KolourPaint and Tk run as
# X11 clients there, so reparenting between them works). Every call is
# best-effort and never raises; the caller falls back to a separate window if
# embedding can't be established.

def _have_cmd(name: str) -> bool:
    """True if `name` is on PATH."""
    return shutil.which(name) is not None


def _window_area(wid: str) -> int:
    """Pixel area of an X11 window id, or -1 if it can't be queried."""
    try:
        out = subprocess.check_output(
            ["xdotool", "getwindowgeometry", "--shell", str(wid)],
            stderr=subprocess.DEVNULL,
        ).decode()
    except Exception:
        return -1
    w = h = 0
    for line in out.splitlines():
        if line.startswith("WIDTH="):
            try:
                w = int(line.split("=", 1)[1])
            except ValueError:
                pass
        elif line.startswith("HEIGHT="):
            try:
                h = int(line.split("=", 1)[1])
            except ValueError:
                pass
    return w * h


def _search_windows(args: list[str]) -> list[str]:
    """Run `xdotool search --onlyvisible <args>` → list of window ids."""
    try:
        out = subprocess.check_output(
            ["xdotool", "search", "--onlyvisible"] + args,
            stderr=subprocess.DEVNULL,
        ).decode().split()
        return out
    except Exception:
        return []


def _wait_kolourpaint_window(pid: int, timeout: float = 15.0, proc=None) -> str | None:
    """
    Poll for KolourPaint's main X11 window after launch and return the
    largest-area visible match (skips splash/utility windows). Tries the
    process pid first, then class/name. Bails early if `proc` has already died.
    """
    deadline = time.time() + timeout
    queries = (
        ["--pid", str(pid)],
        ["--class", "kolourpaint"],
        ["--classname", "kolourpaint"],
        ["--name", "KolourPaint"],
    )
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return None
        for q in queries:
            best, best_area = None, 0
            for wid in _search_windows(q):
                area = _window_area(wid)
                if area > best_area:
                    best, best_area = wid, area
            # Require a real, sized window — the editor canvas is large.
            if best and best_area >= 150 * 150:
                return best
        time.sleep(0.25)
    return None


def _reparent_window(wid: str, parent_id: int, w: int, h: int) -> None:
    """Reparent `wid` into the X window `parent_id`, then fill it."""
    subprocess.run(["xdotool", "windowreparent", str(wid), str(parent_id)],
                   check=False, stderr=subprocess.DEVNULL)
    subprocess.run(["xdotool", "windowmove", str(wid), "0", "0"],
                   check=False, stderr=subprocess.DEVNULL)
    subprocess.run(["xdotool", "windowsize", str(wid), str(w), str(h)],
                   check=False, stderr=subprocess.DEVNULL)


def _resize_window(wid: str, w: int, h: int) -> None:
    """Keep an embedded window pinned to (0,0) at the frame's size."""
    subprocess.run(["xdotool", "windowmove", str(wid), "0", "0"],
                   check=False, stderr=subprocess.DEVNULL)
    subprocess.run(["xdotool", "windowsize", str(wid), str(w), str(h)],
                   check=False, stderr=subprocess.DEVNULL)


def _send_ctrl_s(wid: str) -> None:
    """
    Best-effort 'save' in KolourPaint. We set input focus directly
    (XSetInputFocus works even for reparented/unmanaged windows), send a real
    XTEST Ctrl+S, and ALSO send a synthetic one straight to the window as a
    fallback. Because we always edit a PNG copy, KolourPaint saves it without
    a format dialog.
    """
    subprocess.run(["xdotool", "windowfocus", str(wid)],
                   check=False, stderr=subprocess.DEVNULL)
    time.sleep(0.15)
    subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+s"],
                   check=False, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["xdotool", "key", "--clearmodifiers", "--window", str(wid), "ctrl+s"],
        check=False, stderr=subprocess.DEVNULL,
    )


# ── AI-GENERATION TIMING STATS (for the 'try again' ETA) ────────────────────
# We persist how long regenerations take, keyed by how many images they
# produced, to a small JSON file OUTSIDE the cache dir so clearing the cache
# doesn't wipe the learned averages. With data we can show an estimated time
# remaining; without it we just show a spinner.
#
# File shape:
#   {"by_count": {"1": {"samples": 5, "total_seconds": 232.4}, "2": {...}}}

def _load_gen_timings(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _record_gen_timing(path: str, n_images: int, seconds: float) -> None:
    """Fold one (n_images, seconds) sample into the averages file."""
    data = _load_gen_timings(path)
    by = data.setdefault("by_count", {})
    key = str(max(1, int(n_images)))
    bucket = by.setdefault(key, {"samples": 0, "total_seconds": 0.0})
    bucket["samples"] += 1
    bucket["total_seconds"] = float(bucket["total_seconds"]) + float(seconds)
    try:
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[review] couldn't write timings file {path}: {exc}")


def _estimate_gen_seconds(path: str, n_images: int) -> float | None:
    """
    Best estimate of how long generating `n_images` will take, or None if we
    have no data at all.

    Prefers the exact-count bucket's average; otherwise derives a per-image
    rate from every bucket and scales it by n_images.
    """
    n_images = max(1, int(n_images))
    by = _load_gen_timings(path).get("by_count", {})

    exact = by.get(str(n_images))
    if exact and exact.get("samples"):
        return float(exact["total_seconds"]) / exact["samples"]

    total_seconds = 0.0
    total_images = 0
    for k, v in by.items():
        try:
            cnt = int(k)
        except ValueError:
            continue
        samples = int(v.get("samples", 0))
        total_seconds += float(v.get("total_seconds", 0.0))
        total_images += cnt * samples
    if total_images > 0:
        return (total_seconds / total_images) * n_images
    return None


# ── REVIEWER GUI ──────────────────────────────────────────────────────────────

class _MediaReviewer:
    """
    Multi-candidate review GUI.

    Each `pending_items` element looks like::

        {
            "script_text": "...",
            "candidates": {
                "videos": [{url: trim_secs}, ...],   # up to 2
                "images": [{url: trim_secs}, ...],   # up to 3
            },
            "num_clips_needed": int,
            "max_runtime_per_clip_seconds": float,
        }

    Decisions are written into `review_state` (mutated in place) and persisted
    to `state_file` after every selection.
    """

    def __init__(self, pending_items, history_map, state_file, review_state,
                 regenerate_fn=None, regenerable_texts=None,
                 timings_file=".ai_generation_timings.json"):
        self.items        = pending_items
        self.history      = history_map
        self.state_file   = state_file
        self.review_state = review_state

        self.item_idx       = 0
        self.clip_slot_idx  = 0
        self.chosen: list[dict] = []
        self.chosen_urls: set[str] = set()

        # Active slots for the current view: slot_num (1-5) -> (url, trim)
        self._slot_to_choice: dict[int, tuple[str, float]] = {}

        # Cancellation token for in-flight video playback threads
        self._video_stop = threading.Event()

        # Track scheduled tk after() ids for the video display loops, so we
        # can cancel them when stopping. Otherwise tk prints
        # "invalid command name ...display_step" warnings on close.
        self._video_after_ids: list[str] = []

        # Ignore keypresses while we're transitioning between items, so
        # double-presses during a slow load don't land on the next scene.
        self._advancing = False

        # ── Edit-mode state ──────────────────────────────────────────────
        self._edit_mode = False
        self._editable_slots: set[int] = set()   # slot nums holding an image
        # Active KolourPaint edit session (None when not editing)
        self._editor_frame = None                 # Tk overlay Frame
        self._embed_frame  = None                 # Frame KolourPaint reparents into
        self._embed_status = None                 # status label inside embed frame
        self._save_btn     = None
        self._exit_btn     = None
        self._embedded_wid = None                 # X11 id of embedded KolourPaint
        self._edit_proc    = None                 # KolourPaint subprocess
        self._edit_path    = None                 # working PNG copy being edited
        self._edit_trim    = 0.0
        self._edit_original_url = None            # candidate this edit derived from
        self._finalizing_edit = False             # guard during save/exit

        # ── "Try again" (regenerate) state ───────────────────────────────
        # regenerate_fn(script_text) -> list[{local_path: trim}] | None
        #   Returns fresh image candidates to REPLACE this scene's options,
        #   or None if regeneration isn't applicable / produced nothing.
        # regenerable_texts: optional set of script_texts that support it; when
        #   given, R is gated to those (so non-AI scenes don't even try).
        self._regenerate_fn     = regenerate_fn
        self._regenerable_texts = regenerable_texts
        self._regen_frame       = None            # "Regenerating…" overlay
        # Spinner + ETA state for the regenerate overlay
        self._timings_file      = timings_file
        self._spin_canvas       = None
        self._spin_arc          = None
        self._spin_after        = None            # scheduled after() id
        self._spin_angle        = 0
        self._regen_status_lbl  = None
        self._regen_start       = None            # time.time() at regen start
        self._regen_estimate    = None            # estimated total seconds | None
        self._regen_n           = 0               # expected image count

        # Tk
        self.root = tk.Tk()
        self.root.title("Media Review — Multi-Candidate")
        self.root.configure(bg=BG)
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._bind_keys()
        self._load_current()

        self.root.mainloop()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        # Top progress bar
        top = tk.Frame(self.root, bg=PANEL_BG, pady=6)
        top.pack(fill="x")
        self.progress_var = tk.StringVar()
        tk.Label(top, textvariable=self.progress_var, bg=PANEL_BG,
                 fg=HINT_COL, font=FONT_UI).pack(side="left", padx=12)
        tk.Label(top, text="MEDIA REVIEW", bg=PANEL_BG, fg=ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(side="left", expand=True)

        # Edit-mode banner (single line → constant height, no layout jump)
        self.edit_banner = tk.Label(self.root, text="", bg=BG, fg="white",
                                    font=("Segoe UI", 11, "bold"), pady=4)
        self.edit_banner.pack(fill="x")

        # Script panel
        script_frame = tk.Frame(self.root, bg=PANEL_BG, padx=18, pady=8)
        script_frame.pack(fill="x", padx=14, pady=(6, 4))
        tk.Label(script_frame, text="SCRIPT", bg=PANEL_BG, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.script_var = tk.StringVar()
        tk.Label(script_frame, textvariable=self.script_var, bg=PANEL_BG,
                 fg=TEXT_COL, font=FONT_MONO,
                 wraplength=WINDOW_W - 60, justify="left"
                 ).pack(anchor="w", pady=(4, 0))
        self.runtime_var = tk.StringVar()
        tk.Label(script_frame, textvariable=self.runtime_var, bg=PANEL_BG,
                 fg=HINT_COL, font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))

        # Top row: 2 videos
        video_row = tk.Frame(self.root, bg=BG)
        video_row.pack(pady=(8, 4))
        self.video_slots = []
        for i in range(2):
            slot = self._make_slot(video_row, i + 1, VIDEO_W, VIDEO_H, "VIDEO")
            slot.pack(side="left", padx=12)
            self.video_slots.append(slot)

        # Bottom row: 3 images
        image_row = tk.Frame(self.root, bg=BG)
        image_row.pack(pady=(4, 8))
        self.image_slots = []
        for i in range(3):
            slot = self._make_slot(image_row, i + 3, IMAGE_W, IMAGE_H, "IMAGE")
            slot.pack(side="left", padx=8)
            self.image_slots.append(slot)

        # Hint
        tk.Label(self.root,
                 text="1 / 2 = video    "
                      "3 / 4 / 5 = image    "
                      "E = edit    "
                      "R = try again (AI)    "
                      "F / BACKSPACE = manual",
                 bg=BG, fg=TEXT_COL, font=("Segoe UI", 10)).pack(pady=(2, 2))
        self.status_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.status_var, bg=BG, fg=HINT_COL,
                 font=("Segoe UI", 9)).pack(pady=(0, 6))

    def _make_slot(self, parent, slot_num, w, h, kind_label):
        wrap = tk.Frame(parent, bg=SLOT_BG, padx=6, pady=6, bd=0)

        head = tk.Frame(wrap, bg=SLOT_BG)
        head.pack(fill="x")
        tk.Label(head, text=f"({slot_num})", bg=SLOT_BG, fg=ACCENT,
                 font=FONT_KEY).pack(side="left")
        tk.Label(head, text=f"   {kind_label}", bg=SLOT_BG, fg=HINT_COL,
                 font=("Segoe UI", 9)).pack(side="left")

        # Fixed-size pixel container so all slots stay the same size
        media_container = tk.Frame(wrap, width=w, height=h, bg=SLOT_BG)
        media_container.pack()
        media_container.pack_propagate(False)
        media = tk.Label(media_container, bg=SLOT_BG, fg=HINT_COL)
        media.pack(expand=True, fill="both")

        cap = tk.Label(wrap, text="", bg=SLOT_BG, fg=HINT_COL,
                       font=("Segoe UI", 9))
        cap.pack(anchor="w", pady=(2, 0))

        wrap.media_label = media
        wrap.cap_label   = cap
        wrap.slot_num    = slot_num
        wrap.target_w    = w
        wrap.target_h    = h
        return wrap

    def _bind_keys(self):
        for n in "12345":
            self.root.bind(n, lambda e, k=n: self._on_select(int(k)))
        for k in ("e", "E"):
            self.root.bind(k, lambda e: self._toggle_edit_mode())
        for k in ("r", "R"):
            self.root.bind(k, lambda e: self._on_try_again())
        for k in ("f", "F"):
            self.root.bind(k, lambda e: self._on_manual())
        self.root.bind("<BackSpace>", lambda e: self._on_manual())

    # ── Loading / rendering ─────────────────────────────────────────────────

    def _stop_all_videos(self):
        self._video_stop.set()
        # Cancel any pending display_step callbacks so tk doesn't print
        # "invalid command name ...display_step" when we destroy or rebuild.
        for aid in self._video_after_ids:
            try:
                self.root.after_cancel(aid)
            except tk.TclError:
                pass
        self._video_after_ids = []
        self._video_stop = threading.Event()

    def _load_current(self):
        self._stop_all_videos()

        if self.item_idx >= len(self.items):
            self._on_close()
            return

        item   = self.items[self.item_idx]
        nclips = int(item.get("num_clips_needed", 1) or 1)
        maxrt  = float(item.get("max_runtime_per_clip_seconds", 0.0) or 0.0)

        self.progress_var.set(
            f"Item {self.item_idx + 1} / {len(self.items)}   ·   "
            f"pick clip {self.clip_slot_idx + 1} of {nclips}"
        )
        self.script_var.set(f'"{item["script_text"]}"')
        self.runtime_var.set(
            f"Each clip up to {maxrt:.2f}s · this scene needs {nclips} "
            f"clip{'s' if nclips != 1 else ''}"
        )

        candidates = item.get("candidates", {}) or {}
        videos = candidates.get("videos", []) or []
        images = candidates.get("images", []) or []

        # Wikipedia / image-only scenes: no videos, possibly up to 5 images.
        # Promote extra images into the empty video slots so the user can
        # actually pick them. Slots 1+2 hold the first two; slots 3+4+5
        # hold images 3, 4, 5.
        is_image_only = len(videos) == 0 and len(images) > 3
        if is_image_only:
            print(f"[review] image-only scene with {len(images)} image(s) — "
                  f"promoting first 2 into video slots")
            videos = images[:2]
            images = images[2:5]

        self._slot_to_choice = {}

        # Top: video slots (1, 2)
        for i, slot in enumerate(self.video_slots):
            slot_num = i + 1
            if i >= len(videos):
                self._render_unavailable(slot, "no video found")
                continue
            url, trim = next(iter(videos[i].items()))
            if url in self.chosen_urls:
                self._render_already_chosen(slot)
                continue

            self._slot_to_choice[slot_num] = (url, float(trim))
            local = _resolve_local(url, self.history)
            slot.cap_label.config(text=f"{float(trim):.1f}s · video", fg=HINT_COL)
            if local and _is_video_file(local):
                self._play_video_in_slot(slot, local)
            elif local:
                self._render_image_in_slot(slot, local)
            else:
                self._render_unavailable(slot, "file missing on disk")

        # Bottom: image slots (3, 4, 5)
        for i, slot in enumerate(self.image_slots):
            slot_num = i + 3
            if i >= len(images):
                self._render_unavailable(slot, "no image found")
                continue
            url, trim = next(iter(images[i].items()))
            if url in self.chosen_urls:
                self._render_already_chosen(slot)
                continue

            self._slot_to_choice[slot_num] = (url, float(trim))
            local = _resolve_local(url, self.history)
            slot.cap_label.config(text=f"{float(trim):.1f}s · image", fg=HINT_COL)
            if local:
                self._render_image_in_slot(slot, local)
            else:
                self._render_unavailable(slot, "file missing on disk")

        # Recompute which slots currently hold an editable IMAGE. A slot is
        # editable iff it has a choice AND that file resolves to a non-video
        # (this correctly treats images promoted into the video slots as
        # editable, and real videos as not editable).
        self._editable_slots = set()
        for sn, (u, _t) in self._slot_to_choice.items():
            lp = _resolve_local(u, self.history)
            if lp and not _is_video_file(lp):
                self._editable_slots.add(sn)

        if self.chosen:
            self.status_var.set(
                f"This scene's picks so far: "
                + ", ".join(f"#{i + 1}" for i in range(len(self.chosen)))
            )
        else:
            self.status_var.set("")

        self._apply_edit_mode_visuals()

        # UI is ready for input again
        self._advancing = False

    def _render_image_in_slot(self, slot, path: str):
        try:
            img = Image.open(path)
            img.thumbnail((slot.target_w, slot.target_h), Image.LANCZOS)
            tk_img = ImageTk.PhotoImage(img)
            slot.media_label.config(image=tk_img, text="", bg=SLOT_BG)
            slot.media_label._img_ref = tk_img
        except Exception as exc:
            self._render_unavailable(slot, f"image error: {exc}")

    def _play_video_in_slot(self, slot, path: str):
        """
        Play a video in `slot`. Decoding + resize runs on a worker thread and
        pushes the latest frame onto a 1-slot queue; the UI thread only does
        the cheap PhotoImage swap. This keeps the main loop responsive even
        with two videos playing side-by-side.
        """
        try:
            import cv2
        except ImportError:
            self._render_unavailable(slot, "[install opencv-python to preview]")
            return

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            self._render_unavailable(slot, "video error: could not open")
            return

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        # Cap at ~24 fps — plenty for preview, and prevents the UI loop from
        # being saturated when both video slots are active.
        target_fps = min(max(fps, 1.0), 24.0)
        delay      = max(42, int(1000 / target_fps))

        stop = self._video_stop  # snapshot the current event for this run
        label = slot.media_label
        target_w, target_h = slot.target_w, slot.target_h

        # 1-slot queue → always show the freshest frame, no backlog of stale ones
        frame_q: queue.Queue = queue.Queue(maxsize=1)

        def decode_loop():
            try:
                while not stop.is_set():
                    ret, frame = cap.read()
                    if not ret:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    # cv2.resize is ~10× faster than PIL.Image.thumbnail(LANCZOS)
                    h, w = frame.shape[:2]
                    if w > 0 and h > 0:
                        scale = min(target_w / w, target_h / h, 1.0)
                        if scale < 1.0:
                            frame = cv2.resize(
                                frame,
                                (max(1, int(w * scale)), max(1, int(h * scale))),
                                interpolation=cv2.INTER_AREA,
                            )
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    # drop older frame if UI hasn't picked it up yet
                    try:
                        frame_q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        frame_q.put_nowait(frame)
                    except queue.Full:
                        pass
                    # Pace the decoder; .wait() returns immediately on stop.set()
                    if stop.wait(timeout=delay / 1000.0):
                        break
            finally:
                try:
                    cap.release()
                except Exception:
                    pass

        threading.Thread(target=decode_loop, daemon=True).start()

        def display_step():
            if stop.is_set():
                return
            try:
                frame = frame_q.get_nowait()
                img    = Image.fromarray(frame)
                tk_img = ImageTk.PhotoImage(img)
                label.config(image=tk_img, text="", bg=SLOT_BG)
                label._img_ref = tk_img
            except queue.Empty:
                pass
            except tk.TclError:
                return
            try:
                aid = self.root.after(delay, display_step)
                self._video_after_ids.append(aid)
            except tk.TclError:
                return

        try:
            aid = self.root.after(0, display_step)
            self._video_after_ids.append(aid)
        except tk.TclError:
            return

    def _render_unavailable(self, slot, msg: str):
        slot.media_label.config(image="", text=msg, fg=HINT_COL,
                                bg=DISABLED_BG, font=FONT_UI)
        slot.media_label._img_ref = None
        slot.cap_label.config(text="—", fg=HINT_COL)

    def _render_already_chosen(self, slot):
        slot.media_label.config(image="", text="✓ already chosen",
                                fg="white", bg=CHOSEN_BG, font=FONT_LABEL)
        slot.media_label._img_ref = None
        slot.cap_label.config(text="locked", fg=CHOSEN_BG)

    # ── Edit-mode visuals ────────────────────────────────────────────────────

    def _apply_edit_mode_visuals(self):
        """Show/hide the blue banner + outline editable image slots."""
        on = self._edit_mode

        if on:
            self.edit_banner.config(
                text="✏  EDIT MODE — press an image key (3 / 4 / 5) to edit it "
                     "in KolourPaint    ·    press E to cancel",
                bg=EDIT_OUTLINE, fg="white",
            )
        else:
            self.edit_banner.config(text="", bg=BG)

        for slot in (*self.video_slots, *self.image_slots):
            try:
                if on and slot.slot_num in self._editable_slots:
                    slot.config(highlightthickness=3,
                                highlightbackground=EDIT_OUTLINE,
                                highlightcolor=EDIT_OUTLINE)
                else:
                    slot.config(highlightthickness=0)
            except tk.TclError:
                pass

    def _toggle_edit_mode(self):
        # Ignore while transitioning or while an edit session is open.
        if self._advancing or self._editor_frame is not None:
            return

        if not self._edit_mode:
            # Turning ON requires KolourPaint to be installed.
            if not _have_cmd("kolourpaint"):
                messagebox.showerror(
                    "KolourPaint not found",
                    "Edit mode needs KolourPaint, which doesn't appear to be "
                    "installed.\n\nInstall it with:\n\n"
                    "    sudo apt install kolourpaint\n\n"
                    "Then press E again. (Your review progress is safe.)",
                )
                self.status_var.set("KolourPaint isn't installed — see the dialog.")
                return
            self._edit_mode = True
            if not _have_cmd("xdotool"):
                self.status_var.set(
                    "Edit mode ON — note: install 'xdotool' "
                    "(sudo apt install xdotool) to embed KolourPaint in this window."
                )
            else:
                self.status_var.set("Edit mode ON — pick an image to edit it.")
        else:
            self._edit_mode = False
            self.status_var.set("")

        self._apply_edit_mode_visuals()

    # ── "Try again" (regenerate AI candidates) ───────────────────────────────

    def _on_try_again(self):
        """
        R — re-run the generator for the CURRENT scene and replace its options.

        Only meaningful for AI scenes (stickman / ai_edit / stickman_joint); the
        caller supplies `regenerate_fn` + `regenerable_texts`. Generation is slow
        and blocking, so it runs on a worker thread behind an animated overlay
        (spinner + ETA when we have timing data); the slots refresh on the main
        thread when it returns.
        """
        # Inert while transitioning, during an edit session, or mid-regen.
        if (self._advancing or self._editor_frame is not None
                or self._regen_frame is not None):
            return
        if self.item_idx >= len(self.items):
            return

        item = self.items[self.item_idx]
        script_text = item["script_text"]

        if self._regenerate_fn is None or (
            self._regenerable_texts is not None
            and script_text not in self._regenerable_texts
        ):
            self.status_var.set(
                "Try-again only works for AI scenes (stickman / ai_edit)."
            )
            return

        # Expected image count = how many options this scene currently shows.
        # Regeneration produces the same number (same variant count), so this
        # is what we estimate against and record under.
        n_images = len((item.get("candidates", {}) or {}).get("images", []) or [])
        if n_images <= 0:
            n_images = 1

        estimate = _estimate_gen_seconds(self._timings_file, n_images)
        self._regen_n        = n_images
        self._regen_start    = time.time()
        self._regen_estimate = estimate

        est_txt = f"~{estimate:0.0f}s" if estimate else "unknown (no timing data yet)"
        print(f"[review] regenerating '{script_text[:60]}' — "
              f"{n_images} image(s), estimated {est_txt}")

        self._advancing = True            # block other keys until it returns
        self._stop_all_videos()
        self._build_regen_overlay(n_images, estimate)
        self.root.update_idletasks()

        fn = self._regenerate_fn

        def worker():
            result, err = None, None
            try:
                result = fn(script_text)
            except Exception as exc:       # never let a generator crash the GUI
                err = exc
            try:
                self.root.after(0, lambda: self._finish_try_again(result, err))
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _build_regen_overlay(self, n_images: int, estimate_seconds):
        """Full-window 'Regenerating…' cover with an animated spinner + ETA."""
        self._regen_frame = tk.Frame(self.root, bg=BG)
        self._regen_frame.place(x=0, y=0, relwidth=1, relheight=1)

        box = tk.Frame(self._regen_frame, bg=BG)
        box.place(relx=0.5, rely=0.5, anchor="center")

        # Spinner: a faint full ring with a brighter arc that rotates.
        self._spin_canvas = tk.Canvas(box, width=72, height=72, bg=BG,
                                      highlightthickness=0)
        self._spin_canvas.pack(pady=(0, 14))
        self._spin_canvas.create_oval(10, 10, 62, 62, outline=PANEL_BG, width=6)
        self._spin_arc = self._spin_canvas.create_arc(
            10, 10, 62, 62, start=0, extent=90, style="arc",
            outline=EDIT_OUTLINE, width=6)

        tk.Label(box, text="Regenerating…", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 20, "bold")).pack()
        plural = "s" if n_images != 1 else ""
        tk.Label(box,
                 text=f"Re-running the AI request for this scene "
                      f"({n_images} image{plural}).",
                 bg=BG, fg=TEXT_COL, font=FONT_UI, justify="center"
                 ).pack(pady=(6, 10))

        self._regen_status_lbl = tk.Label(box, text="", bg=BG, fg=HINT_COL,
                                          font=("Segoe UI", 10))
        self._regen_status_lbl.pack()

        self._spin_angle = 0
        self._animate_spinner()

    def _animate_spinner(self):
        """Rotate the arc and refresh the elapsed/remaining line (~25 fps)."""
        if self._regen_frame is None or self._spin_canvas is None:
            return
        try:
            self._spin_angle = (self._spin_angle - 14) % 360
            self._spin_canvas.itemconfig(self._spin_arc, start=self._spin_angle)
        except tk.TclError:
            return

        if self._regen_start is not None and self._regen_status_lbl is not None:
            elapsed = time.time() - self._regen_start
            if self._regen_estimate:
                remaining = self._regen_estimate - elapsed
                if remaining > 0:
                    txt = (f"elapsed {elapsed:0.0f}s   ·   "
                           f"~{remaining:0.0f}s remaining")
                else:
                    txt = f"elapsed {elapsed:0.0f}s   ·   almost there…"
            else:
                txt = f"elapsed {elapsed:0.0f}s"
            try:
                self._regen_status_lbl.config(text=txt)
            except tk.TclError:
                return

        try:
            self._spin_after = self.root.after(40, self._animate_spinner)
        except tk.TclError:
            return

    def _teardown_regen_overlay(self):
        if self._spin_after is not None:
            try:
                self.root.after_cancel(self._spin_after)
            except (tk.TclError, AttributeError):
                pass
        self._spin_after       = None
        self._spin_canvas      = None
        self._spin_arc         = None
        self._regen_status_lbl = None
        frame = self._regen_frame
        self._regen_frame = None
        if frame is not None:
            try:
                frame.destroy()
            except tk.TclError:
                pass

    def _finish_try_again(self, result, err):
        # The window may have been closed while the generator ran.
        if self.root is None:
            return

        elapsed = None
        if self._regen_start is not None:
            elapsed = time.time() - self._regen_start

        self._teardown_regen_overlay()

        # Record timing only on a successful generation (so failures/timeouts
        # don't poison the averages). Key on the count we actually got back.
        if elapsed is not None and err is None and result:
            n_got = len(result) or self._regen_n or 1
            _record_gen_timing(self._timings_file, n_got, elapsed)
            print(f"[review] regeneration done in {elapsed:0.1f}s "
                  f"({n_got} image(s)) — timing averages updated")
        elif elapsed is not None:
            why = f"error: {err}" if err else "nothing returned"
            print(f"[review] regeneration ended after {elapsed:0.1f}s "
                  f"({why}) — not recording timing")

        self._regen_start    = None
        self._regen_estimate = None

        if err is not None:
            self.status_var.set(f"Regeneration failed: {err}")
            self._advancing = False
            return
        if not result:
            self.status_var.set(
                "Nothing came back — keeping the current options."
            )
            self._advancing = False
            return

        # Swap in the fresh image candidates and make their local paths
        # resolvable (generated files are keyed by their own path).
        item  = self.items[self.item_idx]
        cands = item.setdefault("candidates", {})
        cands.setdefault("videos", [])
        cands["images"] = result
        for entry in result:
            for path in entry:
                self.history[path] = path

        # Re-render this same scene with the new options (resets _advancing).
        self._load_current()
        self.status_var.set(
            "Fresh options ready — pick one, edit it (E), or try again (R)."
        )

    # ── Decisions ────────────────────────────────────────────────────────────

    def _commit_choice(self, footage_key: str, trim: float,
                       block_urls: set[str] | None = None):
        """
        Record `footage_key` (a URL or local path) as a pick for the current
        clip slot, then either advance to the next clip slot or finalise the
        scene. `block_urls` additionally marks source candidates as used so
        they grey out for the rest of a multi-clip scene.
        """
        self.chosen.append({footage_key: trim})
        self.chosen_urls.add(footage_key)
        if block_urls:
            self.chosen_urls.update(block_urls)

        item = self.items[self.item_idx]
        self.clip_slot_idx += 1

        if self.clip_slot_idx >= int(item.get("num_clips_needed", 1) or 1):
            self.review_state[item["script_text"]] = {
                "footage": list(self.chosen),
                "manual_intervention": False,
            }
            self._save_state()
            self._advance_to_next_item()
        else:
            # Same scene, next clip slot — re-render with chosen greyed out.
            self._load_current()

    def _on_select(self, slot_num: int):
        if self._advancing:
            return
        if self._editor_frame is not None:
            # An edit session is open — number keys are inert here.
            return
        if slot_num not in self._slot_to_choice:
            self.status_var.set(f"Slot {slot_num} unavailable — try another key")
            return

        url, trim = self._slot_to_choice[slot_num]

        # ── Edit-mode branch: open the chosen IMAGE in KolourPaint ──────────
        if self._edit_mode:
            if slot_num not in self._editable_slots:
                self.status_var.set(
                    "That's a video — KolourPaint only edits images. "
                    "Pick an image, or press E to leave edit mode and pick this video."
                )
                return
            local = _resolve_local(url, self.history)
            if not local:
                self.status_var.set("Can't find that file on disk to edit.")
                return
            self._advancing = True  # pause review while the editor is open
            self._open_editor(original_url=url, trim=float(trim),
                              source_local=local)
            return

        # ── Normal selection ────────────────────────────────────────────────
        self._advancing = True
        self._commit_choice(url, float(trim))

    def _on_manual(self):
        if self._advancing:
            return
        if self._editor_frame is not None:
            return
        self._advancing = True
        item = self.items[self.item_idx]
        self.review_state[item["script_text"]] = {
            "footage": [],
            "manual_intervention": True,
            "max_runtime_per_clip_seconds":
                float(item.get("max_runtime_per_clip_seconds", 0.0) or 0.0),
            "num_clips_needed":
                int(item.get("num_clips_needed", 1) or 1),
        }
        self._save_state()
        self._advance_to_next_item()

    def _advance_to_next_item(self):
        self.item_idx       += 1
        self.clip_slot_idx   = 0
        self.chosen          = []
        self.chosen_urls     = set()
        self._load_current()

    def _save_state(self):
        _save_json(self.review_state, self.state_file)

    # ── KolourPaint edit session ──────────────────────────────────────────────

    def _open_editor(self, original_url: str, trim: float, source_local: str):
        """
        Make a PNG working copy of the chosen image and open it in KolourPaint
        inside an overlay with Save & Continue / Exit controls.

        We always convert to a flattened RGB PNG so Ctrl+S in KolourPaint never
        triggers a lossy-format confirmation dialog, and the original candidate
        file is left untouched.
        """
        self._stop_all_videos()

        src = Path(source_local)
        edited = src.parent / f"edited-{uuid4().hex[:10]}.png"
        try:
            im = Image.open(src)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                white = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(white, im).convert("RGB")
            else:
                im = im.convert("RGB")
            im.save(edited, "PNG")
        except Exception as exc:
            messagebox.showerror(
                "Edit mode",
                f"Couldn't prepare that image for editing:\n\n{exc}",
            )
            self._advancing = False
            return

        self._edit_original_url = original_url
        self._edit_trim         = float(trim)
        self._edit_path         = str(edited)

        self._build_editor_overlay(edited.name)
        self.root.update_idletasks()  # realise geometry before reading size/id
        self._launch_and_embed_kolourpaint(str(edited))

    def _build_editor_overlay(self, filename: str):
        """Overlay covering the whole window: control bar + embed area."""
        self._editor_frame = tk.Frame(self.root, bg=BG)
        self._editor_frame.place(x=0, y=0, relwidth=1, relheight=1)

        bar = tk.Frame(self._editor_frame, bg=PANEL_BG, pady=8)
        bar.pack(fill="x")
        tk.Label(bar, text=f"✏  EDITING:  {filename}", bg=PANEL_BG, fg=ACCENT,
                 font=("Segoe UI", 12, "bold")).pack(side="left", padx=14)

        self._save_btn = tk.Button(
            bar, text="✓  Save & Continue",
            command=self._editor_save_and_continue,
            bg=CHOSEN_BG, fg="white",
            activebackground=CHOSEN_BG, activeforeground="white",
            font=("Segoe UI", 11, "bold"), relief="flat",
            padx=14, pady=6, cursor="hand2")
        self._save_btn.pack(side="right", padx=(8, 14))

        self._exit_btn = tk.Button(
            bar, text="✕  Exit (back to options)",
            command=self._editor_exit,
            bg=ACCENT, fg="white",
            activebackground=ACCENT, activeforeground="white",
            font=("Segoe UI", 11, "bold"), relief="flat",
            padx=14, pady=6, cursor="hand2")
        self._exit_btn.pack(side="right", padx=8)

        tk.Label(
            self._editor_frame,
            text="Draw in KolourPaint → press Ctrl+S → click \u201cSave & Continue\u201d.   "
                 "\u201cExit\u201d discards this edit and returns to the five options.",
            bg=BG, fg=HINT_COL, font=("Segoe UI", 9)).pack(fill="x", pady=(6, 2))

        self._embed_frame = tk.Frame(
            self._editor_frame, bg="#000000",
            highlightthickness=2, highlightbackground=EDIT_OUTLINE)
        self._embed_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._embed_frame.pack_propagate(False)

        self._embed_status = tk.Label(
            self._embed_frame, text="Launching KolourPaint…",
            bg="#000000", fg=TEXT_COL, font=FONT_UI, justify="center")
        self._embed_status.pack(expand=True)
        self._embed_frame.bind("<Configure>", self._on_embed_configure)

    def _launch_and_embed_kolourpaint(self, path: str):
        self._embedded_wid = None
        try:
            self._edit_proc = subprocess.Popen(
                ["kolourpaint", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            if self._embed_status is not None:
                self._embed_status.config(text=f"Failed to launch KolourPaint:\n{exc}")
            return

        if not _have_cmd("xdotool"):
            if self._embed_status is not None:
                self._embed_status.config(
                    text="KolourPaint opened in a SEPARATE window.\n\n"
                         "Install 'xdotool' (sudo apt install xdotool) to embed it here.\n\n"
                         "Edit it, press Ctrl+S, then click \u201cSave & Continue\u201d.")
            return

        # Find KolourPaint's window off-thread (polling is slow), then embed it
        # back on the main thread.
        proc = self._edit_proc

        def worker():
            wid = _wait_kolourpaint_window(proc.pid, timeout=15.0, proc=proc)
            try:
                self.root.after(0, lambda: self._finish_embed(wid))
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_embed(self, wid):
        # The session may have been exited (or the app closed) while we were
        # searching for the window.
        if self._editor_frame is None or self._embed_frame is None:
            return
        if self._edit_proc is None or self._edit_proc.poll() is not None:
            return

        if not wid:
            if self._embed_status is not None:
                self._embed_status.config(
                    text="Couldn't embed KolourPaint (your session may block "
                         "window reparenting).\n\n"
                         "It's open in a SEPARATE window — edit it, press Ctrl+S, "
                         "then click \u201cSave & Continue\u201d.")
            return

        self.root.update_idletasks()
        w = max(100, self._embed_frame.winfo_width())
        h = max(100, self._embed_frame.winfo_height())
        parent_id = self._embed_frame.winfo_id()
        _reparent_window(wid, parent_id, w, h)
        self._embedded_wid = wid

        if self._embed_status is not None:
            try:
                self._embed_status.pack_forget()
            except tk.TclError:
                pass

        # Re-assert geometry once it has mapped into our frame.
        def _resize_again():
            if self._embedded_wid and self._embed_frame is not None:
                _resize_window(
                    self._embedded_wid,
                    max(100, self._embed_frame.winfo_width()),
                    max(100, self._embed_frame.winfo_height()),
                )

        self.root.after(300, _resize_again)

    def _on_embed_configure(self, event):
        if self._embedded_wid:
            _resize_window(self._embedded_wid,
                           max(100, event.width), max(100, event.height))

    def _set_editor_busy(self, msg: str):
        for btn in (self._save_btn, self._exit_btn):
            try:
                if btn is not None:
                    btn.config(state="disabled")
            except tk.TclError:
                pass
        if self._embed_status is not None:
            try:
                self._embed_status.config(text=msg)
            except tk.TclError:
                pass

    def _editor_save_and_continue(self):
        if self._finalizing_edit:
            return
        self._finalizing_edit = True
        self._set_editor_busy("Saving your edit…")

        wid  = self._embedded_wid
        proc = self._edit_proc

        def worker():
            # Best-effort: tell KolourPaint to save, give it time to flush.
            try:
                target = wid
                if (not target and proc is not None
                        and proc.poll() is None and _have_cmd("xdotool")):
                    target = _wait_kolourpaint_window(proc.pid, timeout=3.0, proc=proc)
                if target and _have_cmd("xdotool"):
                    _send_ctrl_s(target)
                    time.sleep(1.4)
            except Exception:
                pass
            # Kill KolourPaint BEFORE the overlay (and its embed frame) are
            # destroyed, so its reparented window doesn't die under it.
            self._kill_edit_proc()
            try:
                self.root.after(0, self._finalize_save)
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finalize_save(self):
        self._finalizing_edit = False
        edited_path  = self._edit_path
        trim         = self._edit_trim
        original_url = self._edit_original_url

        self._teardown_editor_overlay()
        self._edit_mode = False

        if not edited_path or not Path(edited_path).exists():
            self.status_var.set("Edit didn't save — please choose an option again.")
            self._load_current()
            return

        # Resolve this local file for the rest of the session. (Downstream
        # resolves local paths by existence, so no history.json write needed.)
        self.history[edited_path] = edited_path

        block = {original_url} if original_url else set()
        self._commit_choice(edited_path, float(trim), block_urls=block)

    def _editor_exit(self):
        if self._finalizing_edit:
            return
        self._kill_edit_proc()

        # Discard the working copy.
        p = self._edit_path
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
        self._edit_path = None

        self._teardown_editor_overlay()
        self._edit_mode = False
        self.status_var.set("Edit discarded — pick one of the five options.")
        self._load_current()

    def _teardown_editor_overlay(self):
        self._embedded_wid = None
        self._save_btn     = None
        self._exit_btn     = None
        self._embed_status = None
        self._embed_frame  = None
        frame = self._editor_frame
        self._editor_frame = None
        if frame is not None:
            try:
                frame.destroy()
            except tk.TclError:
                pass
        # KolourPaint held the keyboard focus; once it's gone the WM often
        # leaves our window unfocused, so the user has to click it before the
        # number/letter keys work again. Grab focus back — immediately, and
        # again shortly after so we win even if the WM is still settling.
        self._refocus_main_window()
        try:
            self.root.after(120, self._refocus_main_window)
            self.root.after(400, self._refocus_main_window)
        except tk.TclError:
            pass

    def _refocus_main_window(self):
        """Best-effort: return keyboard focus to the review window."""
        if self.root is None:
            return
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            return
        # X11 backup — helps when a child app (KolourPaint) had grabbed focus.
        if _have_cmd("xdotool"):
            try:
                wid = self.root.winfo_id()
                subprocess.run(["xdotool", "windowactivate", str(wid)],
                               check=False, stderr=subprocess.DEVNULL)
                subprocess.run(["xdotool", "windowfocus", str(wid)],
                               check=False, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    def _kill_edit_proc(self):
        proc = self._edit_proc
        self._edit_proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        pass
        except Exception:
            pass

    def _purge_tk(self):
        """
        Tear the Tk interpreter down COMPLETELY, on the calling (main) thread.

        Every ImageTk.PhotoImage we created holds a Tcl-backed resource whose
        __del__ calls into the Tcl interpreter. If those objects are instead
        finalized later by a garbage-collection pass that happens to run on a
        WORKER thread (e.g. the stitcher's 8-way ThreadPoolExecutor), Tcl kills
        the process with:
            Tcl_AsyncDelete: async handler deleted by the wrong thread
        Dropping every PhotoImage reference and destroying root HERE forces all
        that finalization to happen now, on the main thread.
        """
        # Kill any live KolourPaint FIRST so its reparented window doesn't get
        # destroyed out from under the still-running process.
        self._kill_edit_proc()
        self._stop_all_videos()
        for slot in (*getattr(self, "video_slots", []),
                     *getattr(self, "image_slots", [])):
            try:
                slot.media_label._img_ref = None
                slot.media_label.config(image="")
            except Exception:
                pass
        if self.root is not None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            self.root = None

    def _on_close(self):
        if self.root is None:
            return
        self._save_state()
        self._purge_tk()


# ── CLEANUP ───────────────────────────────────────────────────────────────────

def _cleanup_unchosen(review_state, candidates_data, history_file, history_map):
    """
    Delete files for candidate URLs that:
      • Belong to items already DECIDED in `review_state`.
      • Were not chosen.
    Update history.json on disk to drop the deleted entries.

    Pending items (still being reviewed) are intentionally not touched.
    Edited images are NOT candidates, so they're never deleted here; the
    original they were derived from may be (it's no longer needed once the
    edited copy is the recorded pick).
    """
    chosen_urls: set[str] = set()
    for entry in review_state.values():
        if not isinstance(entry, dict) or entry.get("manual_intervention"):
            continue
        for f in entry.get("footage", []):
            if isinstance(f, dict):
                chosen_urls.update(f.keys())

    decided_candidate_urls: set[str] = set()
    for item in candidates_data:
        if item.get("script_text") not in review_state:
            continue
        cands = item.get("candidates", {}) or {}
        for cat in ("videos", "images"):
            for entry in cands.get(cat, []) or []:
                if isinstance(entry, dict):
                    decided_candidate_urls.update(entry.keys())

    new_history = dict(history_map)
    deleted = 0
    for url in list(history_map.keys()):
        if url in decided_candidate_urls and url not in chosen_urls:
            local = history_map[url]
            try:
                lp = Path(local)
                if lp.exists():
                    lp.unlink()
                    deleted += 1
            except Exception as exc:
                print(f"  [cleanup] could not delete {local}: {exc}")
            new_history.pop(url, None)

    _save_json(new_history, history_file)
    if deleted:
        print(f"[cleanup] removed {deleted} unchosen file(s).")


# ── MANUAL INTERVENTION (interactive CLI resolver) ────────────────────────────

def _resolve_manual_interventions_interactively(
    review_state, review_state_file, history_file, cache_dir
):
    """
    Interactive loop that walks the user through replacing flagged scenes.

    Flow per pass:
        1) Print the numbered list of scenes still flagged.
        2) Ask which one to resolve.
        3) Ask for the filename of each clip (relative to stock_footage/).
        4) Verify the file exists.
        5) Auto-update history.json AND the review state file.
        6) Loop until no flagged scenes remain (or Ctrl-C exits cleanly —
           state is already saved on disk so re-running just resumes).
    """
    drop_dir = Path(cache_dir) / "stock_footage"
    drop_dir.mkdir(parents=True, exist_ok=True)

    while True:
        manual_items = [
            (st, entry) for st, entry in review_state.items()
            if isinstance(entry, dict) and entry.get("manual_intervention")
        ]
        if not manual_items:
            print("\n✓ All manual interventions resolved.")
            return

        print()
        print("=" * 78)
        print(f"⚠   MANUAL INTERVENTION  —  {len(manual_items)} scene(s) remaining")
        print("=" * 78)
        print(f"\nDrop your replacement files into:\n  {drop_dir}\n")

        for n, (st_text, entry) in enumerate(manual_items, 1):
            nclips = int(entry.get("num_clips_needed", 1) or 1)
            maxrt  = float(entry.get("max_runtime_per_clip_seconds", 0.0) or 0.0)
            preview = st_text if len(st_text) <= 70 else st_text[:67] + "..."
            print(f"  [{n}] \"{preview}\"")
            print(f"      → {nclips} clip(s), each ≤ {maxrt:.2f}s")

        print()
        try:
            raw = input("Scene # to resolve  (Ctrl-C to exit): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[manual] Cancelled. State saved — re-run anytime to continue.")
            sys.exit(0)

        if not raw.isdigit():
            print("→ please enter a number\n")
            continue

        idx = int(raw) - 1
        if idx < 0 or idx >= len(manual_items):
            print(f"→ out of range (1–{len(manual_items)})\n")
            continue

        st_text, entry = manual_items[idx]
        nclips = int(entry.get("num_clips_needed", 1) or 1)
        maxrt  = float(entry.get("max_runtime_per_clip_seconds", 0.0) or 0.0)

        print(f"\nResolving [{idx + 1}]: \"{st_text}\"")
        print(f"  needs {nclips} clip(s), each ≤ {maxrt:.2f}s\n")

        # Reload history fresh in case it was edited externally
        history     = _load_json_safe(history_file, {}) or {}
        new_footage = []
        aborted     = False

        for c in range(nclips):
            try:
                fname = input(
                    f"  clip {c + 1}/{nclips} — filename in stock_footage/ "
                    f"(or blank to abort): "
                ).strip()
            except (KeyboardInterrupt, EOFError):
                print("\n[manual] Cancelled. State saved.")
                sys.exit(0)

            if not fname:
                print("  → aborted, returning to menu\n")
                aborted = True
                break

            # Strip any path components if the user pasted a full path
            fname     = Path(fname).name
            full_path = drop_dir / fname

            if not full_path.exists():
                print(f"  ✗ not found: {full_path}")
                print(f"     drop the file there and retry the scene\n")
                aborted = True
                break

            # URL key just needs to be unique per local file. Using the
            # basename keeps history.json readable and de-dupes if the same
            # file is reused across scenes.
            url_key            = f"local:{fname}"
            history[url_key]   = str(full_path)
            new_footage.append({url_key: round(maxrt, 2)})
            print(f"  ✓ {fname}")

        if aborted:
            continue

        # Persist both files atomically-ish (history first, then review state)
        _save_json(history, history_file)
        review_state[st_text] = {
            "footage": new_footage,
            "manual_intervention": False,
        }
        _save_json(review_state, review_state_file)
        print(f"\n✓ scene [{idx + 1}] resolved and saved.")


# ── PUBLIC ENTRY POINT ────────────────────────────────────────────────────────

def run_media_review(
    candidates_data: list[dict],
    history_file: str,
    review_state_file: str,
    cache_dir: str = "CACHE",
    regenerate_fn=None,
    regenerable_texts=None,
    timings_file: str = ".ai_generation_timings.json",
) -> tuple[list[dict] | None, bool]:
    """
    Display the multi-candidate review GUI and return the final ordered footage
    list once all scenes are decided.

    Parameters
    ----------
    candidates_data : list[dict]
        Each item:
            {
                "script_text": "...",
                "candidates": {
                    "videos": [{url: trim_secs}, ...],   # up to 2
                    "images": [{url: trim_secs}, ...],   # up to 3
                },
                "num_clips_needed": int,
                "max_runtime_per_clip_seconds": float,
            }

    history_file : str
        Path to URL→local-path map (history.json). Cleanup updates this file.

    review_state_file : str
        Where the persistent review state is read/written.
        Format::
            {
              "<script_text>": {
                "footage": [{url: trim_secs}, ...],
                "manual_intervention": false
              },
              ...
            }

    cache_dir : str
        Root cache dir (only used for printing manual-intervention paths).

    regenerate_fn : callable | None
        Optional ``regenerate_fn(script_text) -> list[{local_path: trim}] | None``.
        When the user presses **R** on a scene, this is called (on a worker
        thread) to re-run that scene's generator; its return value REPLACES the
        scene's image candidates in the GUI. Return None if regeneration isn't
        applicable or produced nothing. Intended for AI scenes (stickman /
        ai_edit) — the caller owns deleting stale outputs and registering the
        new files in history.json.

    regenerable_texts : set[str] | None
        Optional set of script_texts that support R. When given, R is gated to
        those scenes (others show a hint and don't invoke the generator). When
        None, every scene attempts regeneration and relies on `regenerate_fn`
        returning None for the inapplicable ones.

    timings_file : str
        Where to persist AI-generation timing averages (used to show an
        estimated time remaining on the regenerate overlay). Defaults to
        ``.ai_generation_timings.json`` in the CURRENT WORKING DIRECTORY — i.e.
        deliberately OUTSIDE the cache dir, so clearing the cache keeps the
        learned averages. With no data yet, the overlay shows just a spinner.

    Returns
    -------
    (final_data, has_manual) : tuple
        final_data : ordered list shaped like the legacy stitch_together input:
            [{"script_text": ..., "footage": [{url: trim}, ...]}, ...]
            None if `has_manual` is True.
        has_manual : True if any scenes still need manual intervention; the
            caller should typically print/exit so the user can fix the JSONs.
    """
    if not candidates_data:
        print("[review] No candidates to review.")
        return [], False

    # ── Load existing review state (for resume) ─────────────────────────────
    review_state = _load_json_safe(review_state_file, {}) or {}
    if not isinstance(review_state, dict):
        review_state = {}
    # Filter out malformed entries (e.g. from older bool-based formats)
    review_state = {
        k: v for k, v in review_state.items()
        if isinstance(v, dict) and "footage" in v
    }

    history_map = _load_json_safe(history_file, {}) or {}
    if not isinstance(history_map, dict):
        history_map = {}

    # ── Determine which scenes still need review ────────────────────────────
    pending = [
        item for item in candidates_data
        if item.get("script_text") not in review_state
    ]

    if pending:
        print(f"[review] {len(pending)} item(s) need review "
              f"({len(review_state)} already decided). Launching GUI…")
        reviewer = _MediaReviewer(pending, history_map, review_state_file, review_state,
                                  regenerate_fn=regenerate_fn,
                                  regenerable_texts=regenerable_texts,
                                  timings_file=timings_file)
        # Tk teardown MUST finish on this (main) thread. If a later GC pass on a
        # worker thread (the stitcher's ThreadPoolExecutor) finalizes any
        # lingering Tcl-backed objects, the process dies with
        #   "Tcl_AsyncDelete: async handler deleted by the wrong thread".
        # Purge + del + collect forces that finalization here, before any
        # threads are spawned downstream.
        try:
            reviewer._purge_tk()
        except Exception:
            pass
        del reviewer
        gc.collect()
        # GUI persists state; reload from disk to pick up final saved version
        review_state = _load_json_safe(review_state_file, {}) or {}
    else:
        print("[review] All items already decided — skipping GUI.")

    # ── Cleanup unchosen downloaded files (safe for decided items only) ────
    _cleanup_unchosen(review_state, candidates_data, history_file, history_map)

    # ── Manual intervention: resolve interactively (in-process) ────────────
    has_manual_initially = any(
        isinstance(entry, dict) and entry.get("manual_intervention")
        for entry in review_state.values()
    )
    if has_manual_initially:
        _resolve_manual_interventions_interactively(
            review_state, review_state_file, history_file, cache_dir
        )
        # Reload from disk to be safe (resolver writes after every fix)
        review_state = _load_json_safe(review_state_file, {}) or {}

    # ── Build final ordered list in stitch_together format ─────────────────
    final_data: list[dict] = []
    skipped: list[str] = []
    for item in candidates_data:
        st = item["script_text"]
        entry = review_state.get(st)
        if entry and entry.get("footage"):
            final_data.append({
                "script_text": st,
                "footage": entry["footage"],
            })
        else:
            skipped.append(st)

    if skipped:
        print(f"[review] WARNING: {len(skipped)} scene(s) had no footage and "
              f"were skipped: {skipped[:3]}{'…' if len(skipped) > 3 else ''}")

    return final_data, False


# ── STANDALONE QUICK TEST ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("This module is meant to be imported by main.py.")
    print("Run:   python main.py")
