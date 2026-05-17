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
    F or BACKSPACE → reject all → mark scene for MANUAL INTERVENTION

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
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path

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

FONT_MONO  = ("Courier New", 13)
FONT_UI    = ("Segoe UI", 11)
FONT_LABEL = ("Segoe UI", 14, "bold")
FONT_KEY   = ("Segoe UI", 18, "bold")

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


import os


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

    def __init__(self, pending_items, history_map, state_file, review_state):
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
                 text="1 / 2 = pick a video    "
                      "3 / 4 / 5 = pick an image    "
                      "F or BACKSPACE = mark for manual intervention",
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

        if self.chosen:
            self.status_var.set(
                f"This scene's picks so far: "
                + ", ".join(f"#{i + 1}" for i in range(len(self.chosen)))
            )
        else:
            self.status_var.set("")

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

    # ── Decisions ────────────────────────────────────────────────────────────

    def _on_select(self, slot_num: int):
        if self._advancing:
            return
        if slot_num not in self._slot_to_choice:
            self.status_var.set(f"Slot {slot_num} unavailable — try another key")
            return
        self._advancing = True
        url, trim = self._slot_to_choice[slot_num]
        self.chosen.append({url: trim})
        self.chosen_urls.add(url)

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
            # Same scene, next clip slot — re-render with chosen-greyed
            self._load_current()

    def _on_manual(self):
        if self._advancing:
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

    def _on_close(self):
        self._stop_all_videos()
        self._save_state()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


# ── CLEANUP ───────────────────────────────────────────────────────────────────

def _cleanup_unchosen(review_state, candidates_data, history_file, history_map):
    """
    Delete files for candidate URLs that:
      • Belong to items already DECIDED in `review_state`.
      • Were not chosen.
    Update history.json on disk to drop the deleted entries.

    Pending items (still being reviewed) are intentionally not touched.
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
        _MediaReviewer(pending, history_map, review_state_file, review_state)
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
