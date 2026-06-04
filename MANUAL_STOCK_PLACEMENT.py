"""
MANUAL_STOCK_PLACEMENT.py
=========================
Interactive placement of one stock still (the "overlay") on top of another
image (the "base" = the PREVIOUS scene's chosen image), for the
MediaType.MANUAL_STOCK_ADD_TO_PREVIOUS pipeline step.

The pipeline calls this AFTER all stock/AI picks are made (so the overlay and
the base are both resolved), opens the GUI, and bakes the result to a static
MP4 so the Ken Burns pass leaves the placement untouched.

Public API
----------
place_overlay_interactive(base_image_path, overlay_image_path,
                          window_title=..., initial=None) -> Placement | None
    Opens a tkinter window. Returns a Placement when the user ACCEPTS, or
    None if the user EXITS without accepting (Esc / window close).

composite_overlay(base_image_path, overlay_image_path, placement,
                  output_path) -> str
    Renders the full-resolution composite PNG and returns output_path.

extract_frame(video_path, output_path, at_seconds=0.0) -> str
    Pulls a single frame out of a video (used when a base/overlay resolves to
    a video instead of a still).

Standalone test
---------------
    python MANUAL_STOCK_PLACEMENT.py BASE_IMAGE OVERLAY_IMAGE
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageTk
import tkinter as tk


# Pillow renamed the resampling enum in v9.1; keep working on both old + new.
try:
    _RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:                       # very old Pillow
    _RESAMPLE = Image.LANCZOS


@dataclass
class Placement:
    """Where + how big the overlay goes, all relative to the BASE image."""
    width_pct: int       # overlay width as % of base width, clamped 1..80
    cx_frac: float       # center x as a fraction of base width, 0..1
    cy_frac: float       # center y as a fraction of base height, 0..1


# ── sizing knobs ───────────────────────────────────────────────────────────
MIN_PCT: int     = 1
MAX_PCT: int     = 80        # "max size = 80% of the image width"
STEP_PCT: int    = 5         # big +/- buttons step by this
DEFAULT_PCT: int = 30
GHOST_ALPHA: int = 110       # 0..255 opacity of the live placement ghost


# ===========================================================================
# Non-GUI helpers
# ===========================================================================

def extract_frame(video_path: str, output_path: str,
                  at_seconds: float = 0.0) -> str:
    """Extract a single frame from a video to a PNG via ffmpeg."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{max(0.0, at_seconds):.3f}",
        "-i", video_path,
        "-frames:v", "1",
        output_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not Path(output_path).exists():
        # Retry without seeking (some containers don't like -ss before a tiny
        # clip); grab the very first frame instead.
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-frames:v", "1", output_path],
            capture_output=True, text=True,
        )
    return output_path


def _load_rgba(path: str) -> Image.Image:
    return Image.open(path).convert("RGBA")


def composite_overlay(base_image_path: str, overlay_image_path: str,
                      placement: Placement, output_path: str) -> str:
    """
    Render the FULL-RESOLUTION composite: the overlay resized to
    `placement.width_pct`% of the base width (aspect preserved) and pasted
    centered at (cx_frac, cy_frac). Overlays that hang off an edge are clipped
    automatically by PIL. Saves a PNG and returns its path.
    """
    base = _load_rgba(base_image_path)
    overlay = _load_rgba(overlay_image_path)
    bw, bh = base.size
    ow, oh = overlay.size

    target_w = max(1, round(placement.width_pct / 100.0 * bw))
    target_h = max(1, round(target_w * oh / ow))
    overlay_resized = overlay.resize((target_w, target_h), _RESAMPLE)

    cx = placement.cx_frac * bw
    cy = placement.cy_frac * bh
    tlx = round(cx - target_w / 2)
    tly = round(cy - target_h / 2)

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(overlay_resized, (tlx, tly), overlay_resized)   # PIL clips OOB
    result = Image.alpha_composite(base, layer).convert("RGB")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(output_path)
    return output_path


# ===========================================================================
# The placement GUI
# ===========================================================================

class _PlacementApp:
    """tkinter window: aim with the mouse, click to drop, accept / reposition."""

    def __init__(self, base_path: str, overlay_path: str,
                 title: str, initial: Placement | None):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            raise RuntimeError(
                "Could not open a display for the manual placement GUI — this "
                "step needs a desktop session (it can't run headless). "
                f"(tkinter said: {exc})"
            )

        self.root.title(title)
        self.root.configure(bg="#1e1e24")

        # ── images ──────────────────────────────────────────────────
        self.base = _load_rgba(base_path)
        self.overlay = _load_rgba(overlay_path)
        self.bw, self.bh = self.base.size
        self.ow, self.oh = self.overlay.size

        # Fit the base into a sensible on-screen size (leave room for the panel).
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        max_w = min(1200, max(640, sw - 380))
        max_h = min(760, max(420, sh - 170))
        self.scale = min(max_w / self.bw, max_h / self.bh)
        self.disp_w = max(1, round(self.bw * self.scale))
        self.disp_h = max(1, round(self.bh * self.scale))

        self.base_disp = self.base.resize((self.disp_w, self.disp_h), _RESAMPLE)
        self.base_photo = ImageTk.PhotoImage(self.base_disp)

        # ── state ───────────────────────────────────────────────────
        self.pct = max(MIN_PCT, min(MAX_PCT,
                                    int(initial.width_pct) if initial else DEFAULT_PCT))
        self.mode = "aim"                       # "aim" | "placed"
        self.placement: Placement | None = None
        self.cx_frac = initial.cx_frac if initial else 0.5
        self.cy_frac = initial.cy_frac if initial else 0.5
        self.last_click_disp: tuple[int, int] | None = None
        self.result: Placement | None = None

        # PhotoImage refs (must survive GC).
        self._blank = ImageTk.PhotoImage(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
        self._ghost_photo = None
        self._placed_photo = None
        self._overlay_thumb_photo = None

        self._build_ui()
        self._bind_keys()
        self._regen_ghost()
        self._update_panel_box()
        self._set_mode("aim")

        # Bring the window forward and give it focus so shortcuts work at once.
        self.root.update_idletasks()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    # ---------------------------------------------------------------- UI build

    def _build_ui(self) -> None:
        # Main canvas (the base image).
        self.canvas = tk.Canvas(
            self.root, width=self.disp_w, height=self.disp_h,
            bg="#000000", highlightthickness=0, cursor="tcross",
        )
        self.canvas.pack(side="left", padx=10, pady=10)

        self.base_item   = self.canvas.create_image(0, 0, anchor="nw",
                                                     image=self.base_photo)
        self.ghost_item  = self.canvas.create_image(0, 0, anchor="nw",
                                                     image=self._blank, state="hidden")
        self.aim_rect    = self.canvas.create_rectangle(0, 0, 0, 0, dash=(7, 4),
                                                         outline="#5ad1ff", width=2,
                                                         state="hidden")
        self.prev_rect   = self.canvas.create_rectangle(0, 0, 0, 0, dash=(4, 4),
                                                         outline="#888888", width=1,
                                                         state="hidden")
        self.placed_item = self.canvas.create_image(0, 0, anchor="nw",
                                                     image=self._blank, state="hidden")
        self.placed_rect = self.canvas.create_rectangle(0, 0, 0, 0, dash=(7, 4),
                                                         outline="#7CFC00", width=2,
                                                         state="hidden")

        self.canvas.bind("<Motion>",  self._on_motion)
        self.canvas.bind("<Leave>",   self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click)

        # Right-hand control panel.
        side = tk.Frame(self.root, bg="#1e1e24", width=340)
        side.pack(side="right", fill="y", padx=(0, 10), pady=10)
        side.pack_propagate(False)

        tk.Label(side, text="ADDING THIS ONTO THE\nPREVIOUS IMAGE:",
                 bg="#1e1e24", fg="#dddddd", justify="left",
                 font=("Arial", 11, "bold")).pack(anchor="w", pady=(2, 6))

        # Overlay preview (top-right, as requested).
        tw = 290
        th = max(1, round(tw * self.oh / self.ow))
        if th > 190:
            th = 190
            tw = max(1, round(th * self.ow / self.oh))
        self._overlay_thumb_photo = ImageTk.PhotoImage(
            self.overlay.resize((tw, th), _RESAMPLE)
        )
        tk.Label(side, image=self._overlay_thumb_photo,
                 bg="#2a2a33", bd=1, relief="solid").pack(anchor="w")

        # Size control (the "+ / -" + custom-entry block).
        size_frame = tk.Frame(side, bg="#1e1e24")
        size_frame.pack(anchor="w", pady=(12, 4))
        tk.Label(size_frame, text="Width:", bg="#1e1e24", fg="#dddddd",
                 font=("Arial", 11)).pack(side="left", padx=(0, 6))
        tk.Button(size_frame, text="\u2212", command=self._dec,
                  font=("Arial", 16, "bold"), width=3).pack(side="left")
        self.pct_var = tk.StringVar(value=str(self.pct))
        entry = tk.Entry(size_frame, textvariable=self.pct_var, width=4,
                         justify="center", font=("Arial", 15))
        entry.pack(side="left", padx=4)
        entry.bind("<Return>",   self._commit_entry)
        entry.bind("<FocusOut>", self._commit_entry)
        tk.Button(size_frame, text="+", command=self._inc,
                  font=("Arial", 16, "bold"), width=3).pack(side="left")
        tk.Label(size_frame, text="% of base width", bg="#1e1e24",
                 fg="#999999", font=("Arial", 10)).pack(side="left", padx=(6, 0))

        # Dashed-box size indicator (relative footprint preview).
        self.box_canvas = tk.Canvas(side, width=300, height=160,
                                    bg="#2a2a33", highlightthickness=0)
        self.box_canvas.pack(anchor="w", pady=(6, 10))

        tk.Label(
            side, justify="left", bg="#1e1e24", fg="#bbbbbb", font=("Arial", 10),
            text=("Move the mouse over the image to aim.\n"
                  "Click to drop the overlay there.\n\n"
                  "  + / \u2212  resize 5%  (type 1\u201380 for exact)\n"
                  "  Enter / Y   accept\n"
                  "  N / R       reposition\n"
                  "  Esc / Q     exit (resume later)"),
        ).pack(anchor="w", pady=(2, 8))

        self.status_var = tk.StringVar(value="")
        tk.Label(side, textvariable=self.status_var, bg="#1e1e24", fg="#5ad1ff",
                 font=("Arial", 10, "bold"), justify="left",
                 wraplength=310).pack(anchor="w", pady=(0, 10))

        btns = tk.Frame(side, bg="#1e1e24")
        btns.pack(anchor="w", side="bottom", pady=(8, 2))
        self.accept_btn = tk.Button(btns, text="\u2713 Accept", command=self._accept,
                                    font=("Arial", 12, "bold"),
                                    bg="#2e7d32", fg="white", width=11)
        self.accept_btn.pack(side="left", padx=(0, 6))
        self.reject_btn = tk.Button(btns, text="\u21ba Reposition", command=self._reject,
                                    font=("Arial", 12), width=12)
        self.reject_btn.pack(side="left")

    # ------------------------------------------------------------ keybindings

    def _kbd(self, fn):
        """Wrap a shortcut so it is ignored while the user is typing a number."""
        def handler(event):
            if isinstance(self.root.focus_get(), tk.Entry):
                return
            return fn()
        return handler

    def _bind_keys(self) -> None:
        r = self.root
        for k in ("<Return>", "y", "Y"):
            r.bind(k, self._kbd(self._accept))
        for k in ("n", "N", "r", "R"):
            r.bind(k, self._kbd(self._reject))
        for k in ("<Escape>", "q", "Q"):
            r.bind(k, self._kbd(self._exit))
        for k in ("<plus>", "<equal>", "<KP_Add>"):
            r.bind(k, self._kbd(self._inc))
        for k in ("<minus>", "<underscore>", "<KP_Subtract>"):
            r.bind(k, self._kbd(self._dec))
        r.focus_set()

    # ------------------------------------------------------------ geometry

    def _disp_footprint(self) -> tuple[int, int]:
        """Overlay footprint in DISPLAY pixels at the current size %."""
        fw = max(1, round(self.pct / 100.0 * self.disp_w))
        fh = max(1, round(fw * self.oh / self.ow))
        return fw, fh

    def _regen_ghost(self) -> None:
        """Rebuild the semi-transparent ghost image used during aiming."""
        fw, fh = self._disp_footprint()
        g = self.overlay.resize((fw, fh), _RESAMPLE).copy()
        alpha = g.split()[3].point(lambda v: int(v * GHOST_ALPHA / 255))
        g.putalpha(alpha)
        self._ghost_photo = ImageTk.PhotoImage(g)
        self.canvas.itemconfig(self.ghost_item, image=self._ghost_photo)

    def _update_panel_box(self) -> None:
        """Draw the dashed footprint box in the side panel (relative size)."""
        c = self.box_canvas
        c.delete("all")
        CW, CH = 300, 160
        bs = min((CW - 20) / self.bw, (CH - 20) / self.bh)
        bdw, bdh = self.bw * bs, self.bh * bs
        ox, oy = (CW - bdw) / 2, (CH - bdh) / 2
        c.create_rectangle(ox, oy, ox + bdw, oy + bdh, outline="#666", width=1)
        iw = self.pct / 100.0 * bdw
        ih = iw * self.oh / self.ow
        c.create_rectangle(CW / 2 - iw / 2, CH / 2 - ih / 2,
                           CW / 2 + iw / 2, CH / 2 + ih / 2,
                           dash=(6, 4), outline="#5ad1ff", width=2)
        c.create_text(CW / 2, CH - 9, text=f"{self.pct}% of width",
                      fill="#aaaaaa", font=("Arial", 9))

    # ------------------------------------------------------------ mouse

    def _on_motion(self, event) -> None:
        if self.mode != "aim":
            return
        fw, fh = self._disp_footprint()
        cx = min(max(event.x, 0), self.disp_w)
        cy = min(max(event.y, 0), self.disp_h)
        tlx, tly = cx - fw / 2, cy - fh / 2
        self.canvas.coords(self.ghost_item, tlx, tly)
        self.canvas.itemconfig(self.ghost_item, state="normal")
        self.canvas.coords(self.aim_rect, tlx, tly, tlx + fw, tly + fh)
        self.canvas.itemconfig(self.aim_rect, state="normal")

    def _on_leave(self, _event) -> None:
        if self.mode == "aim":
            self.canvas.itemconfig(self.ghost_item, state="hidden")
            self.canvas.itemconfig(self.aim_rect, state="hidden")

    def _on_click(self, event) -> None:
        # Clicking always (re)places — works to drop, and to nudge while placed.
        cx = min(max(event.x, 0), self.disp_w)
        cy = min(max(event.y, 0), self.disp_h)
        self.cx_frac = cx / self.disp_w
        self.cy_frac = cy / self.disp_h
        self.last_click_disp = (cx, cy)
        self.placement = Placement(self.pct, self.cx_frac, self.cy_frac)
        self._set_mode("placed")
        self._render_placed_preview()

    # ------------------------------------------------------------ rendering

    def _render_placed_preview(self) -> None:
        """Show the actual composite (full opacity) at display resolution."""
        fw, fh = self._disp_footprint()
        ov = self.overlay.resize((fw, fh), _RESAMPLE)
        img = self.base_disp.copy()
        cx = self.cx_frac * self.disp_w
        cy = self.cy_frac * self.disp_h
        tlx, tly = round(cx - fw / 2), round(cy - fh / 2)
        img.paste(ov, (tlx, tly), ov)
        self._placed_photo = ImageTk.PhotoImage(img)

        self.canvas.itemconfig(self.placed_item, image=self._placed_photo,
                               state="normal")
        self.canvas.coords(self.placed_item, 0, 0)
        self.canvas.coords(self.placed_rect, tlx, tly, tlx + fw, tly + fh)
        self.canvas.itemconfig(self.placed_rect, state="normal")

        for item in (self.ghost_item, self.aim_rect, self.prev_rect):
            self.canvas.itemconfig(item, state="hidden")

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "aim":
            self.canvas.itemconfig(self.placed_item, state="hidden")
            self.canvas.itemconfig(self.placed_rect, state="hidden")
            self.accept_btn.config(state="disabled")
            self.status_var.set("AIM \u2014 move the mouse, click to place.")
            # Show where it was last dropped (the rejected position), correct size.
            if self.last_click_disp is not None:
                fw, fh = self._disp_footprint()
                cx, cy = self.last_click_disp
                self.canvas.coords(self.prev_rect,
                                   cx - fw / 2, cy - fh / 2, cx + fw / 2, cy + fh / 2)
                self.canvas.itemconfig(self.prev_rect, state="normal")
        else:  # placed
            self.accept_btn.config(state="normal")
            self.status_var.set("PLACED \u2014 Accept, Reposition, or click to move.")

    # ------------------------------------------------------------ size control

    def _apply_pct(self, value) -> None:
        try:
            value = int(value)
        except (TypeError, ValueError):
            self.pct_var.set(str(self.pct))
            return
        self.pct = max(MIN_PCT, min(MAX_PCT, value))
        self.pct_var.set(str(self.pct))
        self._regen_ghost()
        self._update_panel_box()
        if self.mode == "placed":
            self.placement = Placement(self.pct, self.cx_frac, self.cy_frac)
            self._render_placed_preview()

    def _inc(self) -> None:
        self._apply_pct(self.pct + STEP_PCT)

    def _dec(self) -> None:
        self._apply_pct(self.pct - STEP_PCT)

    def _commit_entry(self, event=None):
        raw = self.pct_var.get().strip()
        try:
            value = int(raw)
        except ValueError:
            self.pct_var.set(str(self.pct))
            return "break" if getattr(event, "keysym", "") == "Return" else None
        self._apply_pct(value)
        if getattr(event, "keysym", "") == "Return":
            self.root.focus_set()       # drop focus so shortcuts work again
            return "break"
        return None

    # ------------------------------------------------------------ exits

    def _accept(self) -> None:
        if self.placement is None:
            return
        self.result = Placement(self.pct, self.cx_frac, self.cy_frac)
        self.root.destroy()

    def _reject(self) -> None:
        # Keep last_click_disp so aim mode shows the previous footprint outline.
        self._set_mode("aim")

    def _exit(self) -> None:
        self.result = None
        self.root.destroy()

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._exit)
        self.root.mainloop()


def place_overlay_interactive(base_image_path: str, overlay_image_path: str,
                              window_title: str = "Place stock on the previous image",
                              initial: Placement | None = None) -> Placement | None:
    """
    Open the placement GUI for one overlay on one base image.

    Returns a Placement on ACCEPT, or None if the user EXITS (Esc / window
    close) without accepting.
    """
    app = _PlacementApp(base_image_path, overlay_image_path, window_title, initial)
    app.run()
    return app.result


# ===========================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
        _base, _overlay = sys.argv[1], sys.argv[2]
        _p = place_overlay_interactive(_base, _overlay)
        print("Placement:", _p)
        if _p is not None:
            _out = "manual_placement_test.png"
            composite_overlay(_base, _overlay, _p, _out)
            print("Wrote", _out)
    else:
        print("usage: python MANUAL_STOCK_PLACEMENT.py BASE_IMAGE OVERLAY_IMAGE")

