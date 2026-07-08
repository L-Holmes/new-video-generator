"""
MANUAL_STOCK_PLACEMENT.py
=========================
Manual, per-scene image edits performed AFTER all stock/AI picks are made and
BEFORE Ken Burns. Two tools, both driven by a sized + positionable box over the
PREVIOUS scene's image:

  • PLACEMENT  (MediaType.ADD_STOCK_TO_PREVIOUS)
      Stamp this scene's chosen stock still onto the previous image as many
      times as you like: click to add one (at the current size), keep clicking
      to add more (sizes can differ), Undo removes the last, Done finishes.
      All stamps are flattened together. Optional white-background knockout.

  • ZOOM/CROP  (a decorate-editor tool now — crop_and_zoom below is the
    building block the merged editor reuses; there is no zoom media type)
      Derive this scene's image by cropping/zooming into the previous image.
      The dashed box (default 90% width, base aspect ratio so it never
      distorts) is centred by default and movable with the mouse; the crop is
      upscaled back to the base resolution.

Both bake to a static MP4 so the Ken Burns pass leaves the framing untouched.

Public API
----------
place_overlays_interactive(base, overlay, window_title=..., initial=None)
    -> list[Placement] | None
composite_overlays(base, overlay, placements, output_path) -> str
composite_overlay(base, overlay, placement, output_path) -> str   # 1-stamp wrapper
remove_white_background(image, thresh=..., near_white=...) -> Image (RGBA)

zoom_prev_interactive(base, window_title=..., initial=None) -> CropBox | None
crop_and_zoom(base, cropbox, output_path) -> str

extract_frame(video_path, output_path, at_seconds=0.0) -> str

Standalone test
---------------
    python MANUAL_STOCK_PLACEMENT.py BASE OVERLAY     # placement (stamp many)
    python MANUAL_STOCK_PLACEMENT.py --zoom BASE      # zoom / crop
"""

from __future__ import annotations

# Allow `uv run ___visuals/MANUAL_STOCK_PLACEMENT.py` from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import subprocess
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageTk

# Pillow renamed the resampling enum in v9.1; keep working on both old + new.
try:
    _RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # very old Pillow
    _RESAMPLE = Image.LANCZOS


# ===========================================================================
# Data
# ===========================================================================


@dataclass
class Placement:
    """One stamp of the overlay, relative to the BASE image."""

    width_pct: int  # overlay width as % of base width, clamped 1..80
    cx_frac: float  # center x as a fraction of base width, 0..1
    cy_frac: float  # center y as a fraction of base height, 0..1
    remove_bg: bool = True  # knock out the overlay's white background (per-overlay)


@dataclass
class CropBox:
    """A crop/zoom rectangle, relative to the BASE image.

    width_pct / height_pct are the crop dimensions as % of base width / height
    (clamped 10..100). height_pct=None (the default) keeps the base aspect
    ratio (height derived from width) — the original behaviour. Setting it
    gives an independent height so the crop can be non-aspect-locked.
    """

    width_pct: int  # crop width as % of base width, clamped 10..100
    cx_frac: float  # center x as a fraction of base width, 0..1
    cy_frac: float  # center y as a fraction of base height, 0..1
    height_pct: int | None = (
        None  # crop height as % of base height (None = lock aspect)
    )


# ── placement sizing knobs ─────────────────────────────────────────────────
MIN_PCT: int = 1
MAX_PCT: int = 80
STEP_PCT: int = 5
DEFAULT_PCT: int = 30
GHOST_ALPHA: int = 110  # 0..255 opacity of the live "next stamp" ghost

# ── zoom/crop knobs ────────────────────────────────────────────────────────
ZOOM_MIN_PCT: int = 10
ZOOM_MAX_PCT: int = 100  # 100% = whole image (no zoom)
ZOOM_STEP_PCT: int = 5
ZOOM_DEFAULT_PCT: int = 90
ZOOM_DIM: float = 0.55  # how far the area outside the crop is darkened

# ── white-background removal knobs ─────────────────────────────────────────
WHITE_BG_NEAR_WHITE: int = 220  # min per-channel brightness for a seed pixel
WHITE_BG_THRESHOLD: int = 90  # PIL flood tolerance (sum of per-channel diff)


# ===========================================================================
# Non-GUI helpers
# ===========================================================================


def extract_frame(video_path: str, output_path: str, at_seconds: float = 0.0) -> str:
    """Extract a single frame from a video to a PNG via ffmpeg."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, at_seconds):.3f}",
        "-i",
        video_path,
        "-frames:v",
        "1",
        output_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not Path(output_path).exists():
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-frames:v", "1", output_path],
            capture_output=True,
            text=True,
        )
    return output_path


def _load_rgba(path: str) -> Image.Image:
    return Image.open(path).convert("RGBA")


def remove_white_background(
    image: Image.Image,
    thresh: int = WHITE_BG_THRESHOLD,
    near_white: int = WHITE_BG_NEAR_WHITE,
) -> Image.Image:
    """
    Knock out the border-connected ~white background (simple, no ML): only
    white that touches the image edges is made transparent, so white *inside*
    the subject is preserved. Returns an RGBA copy.
    """
    import numpy as np
    from PIL import ImageDraw

    rgba = image.convert("RGBA")
    rgb = rgba.convert("RGB")
    w, h = rgb.size
    orig = np.asarray(rgb, dtype=np.int16)

    flood = rgb.copy()
    px = flood.load()
    SENTINEL = (0, 0, 0)

    def _near_white(p) -> bool:
        return p[0] >= near_white and p[1] >= near_white and p[2] >= near_white

    border = (
        [(x, 0) for x in range(w)]
        + [(x, h - 1) for x in range(w)]
        + [(0, y) for y in range(h)]
        + [(w - 1, y) for y in range(h)]
    )
    seeds_used = 0
    for x, y in border:
        p = px[x, y]
        if p == SENTINEL:
            continue
        if _near_white(p):
            ImageDraw.floodfill(flood, (x, y), SENTINEL, thresh=thresh)
            seeds_used += 1

    flooded = np.asarray(flood, dtype=np.int16)
    bg_mask = np.any(flooded != orig, axis=-1)

    def _dilate(m):
        d = m.copy()
        d[1:, :] |= m[:-1, :]
        d[:-1, :] |= m[1:, :]
        d[:, 1:] |= m[:, :-1]
        d[:, :-1] |= m[:, 1:]
        return d

    near_white_mask = np.all(orig >= near_white, axis=-1)
    fringe = _dilate(bg_mask) & near_white_mask & (~bg_mask)

    alpha = np.array(rgba.split()[3], dtype=np.uint8)
    alpha[bg_mask] = 0
    alpha[fringe] = 0

    removed = int(bg_mask.sum() + fringe.sum())
    total = w * h
    print(
        f"[manual] white-bg removal: {seeds_used} border seed(s), "
        f"{removed}/{total} px ({100.0 * removed / max(1, total):.1f}%) "
        f"made transparent"
    )

    out = rgba.copy()
    out.putalpha(Image.fromarray(alpha, mode="L"))
    return out


def composite_overlays(
    base_image_path: str, overlay_image_path: str, placements, output_path: str
) -> str:
    """Full-resolution composite: stamp the overlay once per placement (each at
    its own width_pct/position). The white-bg knockout (from the first
    placement's remove_bg) is applied to the overlay a single time."""
    if not placements:
        raise ValueError("composite_overlays: no placements given")

    base = _load_rgba(base_image_path)
    overlay = _load_rgba(overlay_image_path)
    if getattr(placements[0], "remove_bg", False):
        try:
            overlay = remove_white_background(overlay)
        except Exception as exc:
            print(
                f"[manual] white-bg removal failed during composite "
                f"({exc}); using the original overlay"
            )

    bw, bh = base.size
    ow, oh = overlay.size
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    for p in placements:
        target_w = max(1, round(p.width_pct / 100.0 * bw))
        target_h = max(1, round(target_w * oh / ow))
        ov = overlay.resize((target_w, target_h), _RESAMPLE)
        cx = p.cx_frac * bw
        cy = p.cy_frac * bh
        layer.paste(ov, (round(cx - target_w / 2), round(cy - target_h / 2)), ov)

    result = Image.alpha_composite(base, layer).convert("RGB")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(output_path)
    return output_path


def composite_overlay(
    base_image_path: str,
    overlay_image_path: str,
    placement: Placement,
    output_path: str,
) -> str:
    """Single-stamp convenience wrapper around composite_overlays."""
    return composite_overlays(
        base_image_path, overlay_image_path, [placement], output_path
    )


def crop_and_zoom(base_image_path: str, cropbox: CropBox, output_path: str) -> str:
    """Crop the base to `cropbox` (base aspect ratio, kept inside the image)
    and upscale back to the base resolution. Saves a PNG."""
    base = Image.open(base_image_path).convert("RGB")
    bw, bh = base.size

    box_w = max(1, min(bw, round(cropbox.width_pct / 100.0 * bw)))
    if cropbox.height_pct is not None:
        box_h = max(1, min(bh, round(cropbox.height_pct / 100.0 * bh)))
    else:
        box_h = max(1, min(bh, round(box_w * bh / bw)))  # base aspect → no distortion

    cx = cropbox.cx_frac * bw
    cy = cropbox.cy_frac * bh
    left = max(0, min(round(cx - box_w / 2), bw - box_w))
    top = max(0, min(round(cy - box_h / 2), bh - box_h))

    crop = base.crop((left, top, left + box_w, top + box_h))
    zoomed = crop.resize((bw, bh), _RESAMPLE)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    zoomed.save(output_path)
    return output_path


# ===========================================================================
# Shared GUI piece: the resize control
# ===========================================================================


class _SizeControl:
    """
    Reusable width control: [label] [−] [entry] [+] [% of base width], plus a
    dashed box that previews the footprint relative to the base. Owns an
    integer percent in [min_pct, max_pct] and calls on_change(pct) on changes.
    """

    def __init__(
        self,
        parent,
        *,
        base_wh,
        box_aspect,
        min_pct,
        max_pct,
        step_pct,
        initial_pct,
        on_change,
        label="Width:",
        bg="#1e1e24",
        fg="#dddddd",
    ):
        self._base_wh = base_wh
        self._aspect = box_aspect
        self._min, self._max, self._step = min_pct, max_pct, step_pct
        self._on_change = on_change
        self.pct = max(min_pct, min(max_pct, int(initial_pct)))

        row = tk.Frame(parent, bg=bg)
        row.pack(anchor="w", pady=(8, 4))
        tk.Label(row, text=label, bg=bg, fg=fg, font=("Arial", 11)).pack(
            side="left", padx=(0, 6)
        )
        tk.Button(
            row, text="\u2212", command=self.dec, font=("Arial", 16, "bold"), width=3
        ).pack(side="left")
        self._var = tk.StringVar(value=str(self.pct))
        entry = tk.Entry(
            row, textvariable=self._var, width=4, justify="center", font=("Arial", 15)
        )
        entry.pack(side="left", padx=4)
        entry.bind("<Return>", self._commit)
        entry.bind("<FocusOut>", self._commit)
        tk.Button(
            row, text="+", command=self.inc, font=("Arial", 16, "bold"), width=3
        ).pack(side="left")
        tk.Label(
            row, text="% of base width", bg=bg, fg="#999999", font=("Arial", 10)
        ).pack(side="left", padx=(6, 0))

        self._canvas = tk.Canvas(
            parent, width=300, height=150, bg="#2a2a33", highlightthickness=0
        )
        self._canvas.pack(anchor="w", pady=(6, 10))
        self._draw_box()

    def get(self) -> int:
        return self.pct

    def set_aspect(self, aspect: float) -> None:
        self._aspect = aspect
        self._draw_box()

    def _apply(self, value) -> None:
        try:
            value = int(value)
        except (TypeError, ValueError):
            self._var.set(str(self.pct))
            return
        self.pct = max(self._min, min(self._max, value))
        self._var.set(str(self.pct))
        self._draw_box()
        if self._on_change:
            self._on_change(self.pct)

    def inc(self) -> None:
        self._apply(self.pct + self._step)

    def dec(self) -> None:
        self._apply(self.pct - self._step)

    def _commit(self, event=None):
        self._apply(self._var.get().strip())
        if getattr(event, "keysym", "") == "Return":
            self._canvas.winfo_toplevel().focus_set()
            return "break"
        return None

    def _draw_box(self) -> None:
        c = self._canvas
        c.delete("all")
        CW, CH = 300, 150
        bw, bh = self._base_wh
        bs = min((CW - 20) / bw, (CH - 20) / bh)
        bdw, bdh = bw * bs, bh * bs
        ox, oy = (CW - bdw) / 2, (CH - bdh) / 2
        c.create_rectangle(ox, oy, ox + bdw, oy + bdh, outline="#666", width=1)
        iw = self.pct / 100.0 * bdw
        ih = iw * self._aspect
        c.create_rectangle(
            CW / 2 - iw / 2,
            CH / 2 - ih / 2,
            CW / 2 + iw / 2,
            CH / 2 + ih / 2,
            dash=(6, 4),
            outline="#5ad1ff",
            width=2,
        )
        c.create_text(
            CW / 2,
            CH - 9,
            text=f"{self.pct}% of width",
            fill="#aaaaaa",
            font=("Arial", 9),
        )


def _fit_display(base_w: int, base_h: int, screen_w: int, screen_h: int):
    """Common base→display scaling (leaves room for the side panel)."""
    max_w = min(1200, max(640, screen_w - 380))
    max_h = min(760, max(420, screen_h - 170))
    scale = min(max_w / base_w, max_h / base_h)
    return scale, max(1, round(base_w * scale)), max(1, round(base_h * scale))


# ===========================================================================
# Placement GUI  (multi-stamp)
# ===========================================================================


class _PlacementApp:
    """Click to stamp the overlay, keep clicking to add more, Undo removes the
    last, Done finishes. All stamps are flattened together."""

    def __init__(self, base_path, overlay_path, title, initial):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            raise RuntimeError(
                "Could not open a display for the placement GUI — this step "
                f"needs a desktop session (tkinter said: {exc})"
            )
        self.root.title(title)
        self.root.configure(bg="#1e1e24")

        self.base = _load_rgba(base_path)
        self.overlay = _load_rgba(overlay_path)
        self.bw, self.bh = self.base.size
        self.ow, self.oh = self.overlay.size

        try:
            self.overlay_nobg = remove_white_background(self.overlay)
        except Exception as exc:
            print(
                f"[manual] white-bg removal failed at GUI init ({exc}); "
                f"the checkbox will fall back to the original image"
            )
            self.overlay_nobg = None

        self.scale, self.disp_w, self.disp_h = _fit_display(
            self.bw,
            self.bh,
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
        )
        self.base_disp = self.base.resize((self.disp_w, self.disp_h), _RESAMPLE)
        self.base_photo = ImageTk.PhotoImage(self.base_disp)

        self.pct = max(
            MIN_PCT, min(MAX_PCT, int(initial.width_pct) if initial else DEFAULT_PCT)
        )
        self.remove_bg = bool(initial.remove_bg) if initial else True
        self.placements: list[Placement] = []
        self.result = None

        self._blank = ImageTk.PhotoImage(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
        self._ghost_photo = None
        self._composite_photo = None
        self._overlay_thumb_photo = None

        self._build_ui()
        self._bind_keys()
        self._refresh_overlay_visuals()  # thumb + ghost + composite
        self._update_count("Click on the image to add the first one.")

        self.root.update_idletasks()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def _build_ui(self):
        self.canvas = tk.Canvas(
            self.root,
            width=self.disp_w,
            height=self.disp_h,
            bg="#000000",
            highlightthickness=0,
            cursor="tcross",
        )
        self.canvas.pack(side="left", padx=10, pady=10)
        self.canvas_item = self.canvas.create_image(
            0, 0, anchor="nw", image=self.base_photo
        )
        self.ghost_item = self.canvas.create_image(
            0, 0, anchor="nw", image=self._blank, state="hidden"
        )
        self.aim_rect = self.canvas.create_rectangle(
            0, 0, 0, 0, dash=(7, 4), outline="#5ad1ff", width=2, state="hidden"
        )
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click)

        side = tk.Frame(self.root, bg="#1e1e24", width=340)
        side.pack(side="right", fill="y", padx=(0, 10), pady=10)
        side.pack_propagate(False)

        tk.Label(
            side,
            text="ADDING THIS ONTO THE\nPREVIOUS IMAGE:",
            bg="#1e1e24",
            fg="#dddddd",
            justify="left",
            font=("Arial", 11, "bold"),
        ).pack(anchor="w", pady=(2, 6))

        tw = 290
        th = max(1, round(tw * self.oh / self.ow))
        if th > 170:
            th = 170
            tw = max(1, round(th * self.ow / self.oh))
        self._thumb_wh = (tw, th)
        self.thumb_label = tk.Label(
            side, image=self._blank, bg="#2a2a33", bd=1, relief="solid"
        )
        self.thumb_label.pack(anchor="w")

        self.removebg_var = tk.BooleanVar(value=self.remove_bg)
        tk.Checkbutton(
            side,
            text="Remove white background",
            variable=self.removebg_var,
            command=self._toggle_removebg,
            bg="#1e1e24",
            fg="#dddddd",
            selectcolor="#2a2a33",
            activebackground="#1e1e24",
            activeforeground="#ffffff",
            font=("Arial", 10),
        ).pack(anchor="w", pady=(6, 4))

        self.count_var = tk.StringVar(value="Added: 0")
        tk.Label(
            side,
            textvariable=self.count_var,
            bg="#1e1e24",
            fg="#7CFC00",
            font=("Arial", 14, "bold"),
        ).pack(anchor="w", pady=(6, 0))

        self.size_ctrl = _SizeControl(
            side,
            base_wh=(self.bw, self.bh),
            box_aspect=self.oh / self.ow,
            min_pct=MIN_PCT,
            max_pct=MAX_PCT,
            step_pct=STEP_PCT,
            initial_pct=self.pct,
            on_change=self._on_size_change,
        )

        tk.Label(
            side,
            justify="left",
            bg="#1e1e24",
            fg="#bbbbbb",
            font=("Arial", 10),
            text=(
                "Click to stamp the image (current size).\n"
                "Keep clicking to add more — sizes can differ.\n\n"
                "  + / \u2212  resize 5%  (type 1\u201380 for exact)\n"
                "  U / Bksp    undo the last one\n"
                "  Enter / D   done\n"
                "  Esc / Q     exit (resume later)"
            ),
        ).pack(anchor="w", pady=(2, 8))

        self.status_var = tk.StringVar(value="")
        tk.Label(
            side,
            textvariable=self.status_var,
            bg="#1e1e24",
            fg="#5ad1ff",
            font=("Arial", 10, "bold"),
            justify="left",
            wraplength=310,
        ).pack(anchor="w", pady=(0, 10))

        btns = tk.Frame(side, bg="#1e1e24")
        btns.pack(anchor="w", side="bottom", pady=(8, 2))
        self.done_btn = tk.Button(
            btns,
            text="\u2713 Done",
            command=self._done,
            font=("Arial", 12, "bold"),
            bg="#2e7d32",
            fg="white",
            width=11,
        )
        self.done_btn.pack(side="left", padx=(0, 6))
        self.undo_btn = tk.Button(
            btns, text="\u21b6 Undo", command=self._undo, font=("Arial", 12), width=11
        )
        self.undo_btn.pack(side="left")

    def _kbd(self, fn):
        def handler(event):
            if isinstance(self.root.focus_get(), tk.Entry):
                return
            return fn()

        return handler

    def _bind_keys(self):
        r = self.root
        for k in ("<Return>", "d", "D"):
            r.bind(k, self._kbd(self._done))
        for k in ("u", "U", "<BackSpace>"):
            r.bind(k, self._kbd(self._undo))
        for k in ("<Escape>", "q", "Q"):
            r.bind(k, self._kbd(self._exit))
        for k in ("<plus>", "<equal>", "<KP_Add>"):
            r.bind(k, self._kbd(self.size_ctrl.inc))
        for k in ("<minus>", "<underscore>", "<KP_Subtract>"):
            r.bind(k, self._kbd(self.size_ctrl.dec))
        r.focus_set()

    def _effective_overlay(self):
        if self.remove_bg and self.overlay_nobg is not None:
            return self.overlay_nobg
        return self.overlay

    def _toggle_removebg(self):
        self.remove_bg = bool(self.removebg_var.get())
        # Apply the new setting to every existing stamp too.
        self.placements = [
            Placement(p.width_pct, p.cx_frac, p.cy_frac, self.remove_bg)
            for p in self.placements
        ]
        self._refresh_overlay_visuals()

    def _refresh_overlay_visuals(self):
        eff = self._effective_overlay()
        tw, th = self._thumb_wh
        self._overlay_thumb_photo = ImageTk.PhotoImage(eff.resize((tw, th), _RESAMPLE))
        self.thumb_label.config(image=self._overlay_thumb_photo)
        self._regen_ghost()
        self._rebuild_composite()

    def _disp_footprint(self, pct):
        fw = max(1, round(pct / 100.0 * self.disp_w))
        fh = max(1, round(fw * self.oh / self.ow))
        return fw, fh

    def _on_size_change(self, pct):
        self.pct = pct
        self._regen_ghost()  # only the *next* stamp changes size

    def _regen_ghost(self):
        eff = self._effective_overlay()
        fw, fh = self._disp_footprint(self.pct)
        g = eff.resize((fw, fh), _RESAMPLE).copy()
        alpha = g.split()[3].point(lambda v: int(v * GHOST_ALPHA / 255))
        g.putalpha(alpha)
        self._ghost_photo = ImageTk.PhotoImage(g)
        self.canvas.itemconfig(self.ghost_item, image=self._ghost_photo)

    def _rebuild_composite(self):
        eff = self._effective_overlay()
        img = self.base_disp.copy()
        for p in self.placements:
            fw, fh = self._disp_footprint(p.width_pct)
            ov = eff.resize((fw, fh), _RESAMPLE)
            cx = p.cx_frac * self.disp_w
            cy = p.cy_frac * self.disp_h
            img.paste(ov, (round(cx - fw / 2), round(cy - fh / 2)), ov)
        self._composite_photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfig(self.canvas_item, image=self._composite_photo)

    def _on_motion(self, event):
        fw, fh = self._disp_footprint(self.pct)
        cx = min(max(event.x, 0), self.disp_w)
        cy = min(max(event.y, 0), self.disp_h)
        tlx, tly = cx - fw / 2, cy - fh / 2
        self.canvas.coords(self.ghost_item, tlx, tly)
        self.canvas.itemconfig(self.ghost_item, state="normal")
        self.canvas.coords(self.aim_rect, tlx, tly, tlx + fw, tly + fh)
        self.canvas.itemconfig(self.aim_rect, state="normal")

    def _on_leave(self, _event):
        self.canvas.itemconfig(self.ghost_item, state="hidden")
        self.canvas.itemconfig(self.aim_rect, state="hidden")

    def _on_click(self, event):
        cx = min(max(event.x, 0), self.disp_w)
        cy = min(max(event.y, 0), self.disp_h)
        self.placements.append(
            Placement(self.pct, cx / self.disp_w, cy / self.disp_h, self.remove_bg)
        )
        self._rebuild_composite()
        self._update_count(f"Stamped #{len(self.placements)} (at {self.pct}% width).")

    def _undo(self):
        if not self.placements:
            self._update_count("Nothing to undo yet.")
            return
        self.placements.pop()
        self._rebuild_composite()
        self._update_count(f"Removed the last one \u2014 {len(self.placements)} left.")

    def _update_count(self, status=""):
        n = len(self.placements)
        self.count_var.set(f"Added: {n}")
        self.done_btn.config(state="normal" if n else "disabled")
        self.undo_btn.config(state="normal" if n else "disabled")
        if status:
            self.status_var.set(status)

    def _done(self):
        if not self.placements:
            self._update_count("Add at least one before pressing Done.")
            return
        self.result = list(self.placements)
        self.root.destroy()

    def _exit(self):
        self.result = None
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._exit)
        self.root.mainloop()


def place_overlays_interactive(
    base_image_path,
    overlay_image_path,
    window_title="Place stock on the previous image",
    initial=None,
):
    """Returns a list[Placement] on Done, or None if the user EXITS."""
    app = _PlacementApp(base_image_path, overlay_image_path, window_title, initial)
    app.run()
    return app.result


# ===========================================================================
# Zoom / crop GUI
# ===========================================================================


class _ZoomApp:
    """Move a dashed crop box (base aspect) over the previous image; centre it
    or drag it; accept to crop+zoom."""

    def __init__(self, base_path, title, initial):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            raise RuntimeError(
                "Could not open a display for the zoom GUI — this step needs a "
                f"desktop session (tkinter said: {exc})"
            )
        self.root.title(title)
        self.root.configure(bg="#1e1e24")

        self.base_rgb = Image.open(base_path).convert("RGB")
        self.bw, self.bh = self.base_rgb.size

        self.scale, self.disp_w, self.disp_h = _fit_display(
            self.bw,
            self.bh,
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
        )
        self.base_disp = self.base_rgb.resize((self.disp_w, self.disp_h), _RESAMPLE)
        dark = Image.blend(
            self.base_disp, Image.new("RGB", self.base_disp.size, (0, 0, 0)), ZOOM_DIM
        )
        self.dark_photo = ImageTk.PhotoImage(dark)

        self.pct = max(
            ZOOM_MIN_PCT,
            min(ZOOM_MAX_PCT, int(initial.width_pct) if initial else ZOOM_DEFAULT_PCT),
        )
        self.cx_frac = initial.cx_frac if initial else 0.5
        self.cy_frac = initial.cy_frac if initial else 0.5
        self.result = None

        self._blank = ImageTk.PhotoImage(Image.new("RGB", (1, 1), (0, 0, 0)))
        self._patch_photo = None
        self._preview_photo = None

        self._build_ui()
        self._bind_keys()
        self._clamp_center()
        self._render()

        self.root.update_idletasks()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def _build_ui(self):
        self.canvas = tk.Canvas(
            self.root,
            width=self.disp_w,
            height=self.disp_h,
            bg="#000000",
            highlightthickness=0,
            cursor="fleur",
        )
        self.canvas.pack(side="left", padx=10, pady=10)
        self.dark_item = self.canvas.create_image(
            0, 0, anchor="nw", image=self.dark_photo
        )
        self.patch_item = self.canvas.create_image(
            0, 0, anchor="nw", image=self._blank, state="hidden"
        )
        self.box_rect = self.canvas.create_rectangle(
            0, 0, 0, 0, dash=(7, 4), outline="#5ad1ff", width=2
        )
        self.canvas.bind("<Button-1>", self._on_drag)
        self.canvas.bind("<B1-Motion>", self._on_drag)

        side = tk.Frame(self.root, bg="#1e1e24", width=340)
        side.pack(side="right", fill="y", padx=(0, 10), pady=10)
        side.pack_propagate(False)

        tk.Label(
            side,
            text="ZOOM INTO THE\nPREVIOUS IMAGE:",
            bg="#1e1e24",
            fg="#dddddd",
            justify="left",
            font=("Arial", 11, "bold"),
        ).pack(anchor="w", pady=(2, 6))

        tw = 290
        th = max(1, round(tw * self.bh / self.bw))
        if th > 190:
            th = 190
            tw = max(1, round(th * self.bw / self.bh))
        self._preview_wh = (tw, th)
        tk.Label(
            side, text="result preview:", bg="#1e1e24", fg="#999999", font=("Arial", 9)
        ).pack(anchor="w")
        self.preview_label = tk.Label(
            side, image=self._blank, bg="#2a2a33", bd=1, relief="solid"
        )
        self.preview_label.pack(anchor="w", pady=(0, 4))

        self.size_ctrl = _SizeControl(
            side,
            base_wh=(self.bw, self.bh),
            box_aspect=self.bh / self.bw,
            min_pct=ZOOM_MIN_PCT,
            max_pct=ZOOM_MAX_PCT,
            step_pct=ZOOM_STEP_PCT,
            initial_pct=self.pct,
            on_change=self._on_size_change,
            label="Crop:",
        )

        tk.Button(
            side,
            text="\u25a3  Centre cropped area",
            command=self._centre,
            font=("Arial", 11),
            width=24,
        ).pack(anchor="w", pady=(0, 8))

        tk.Label(
            side,
            justify="left",
            bg="#1e1e24",
            fg="#bbbbbb",
            font=("Arial", 10),
            text=(
                "Click or drag on the image to move the crop box.\n\n"
                "  + / \u2212  resize 5%  (type 10\u2013100 for exact)\n"
                "  C           centre the crop box\n"
                "  Enter / Y   accept\n"
                "  Esc / Q     exit (resume later)"
            ),
        ).pack(anchor="w", pady=(2, 8))

        self.status_var = tk.StringVar(value="")
        tk.Label(
            side,
            textvariable=self.status_var,
            bg="#1e1e24",
            fg="#5ad1ff",
            font=("Arial", 10, "bold"),
            justify="left",
            wraplength=310,
        ).pack(anchor="w", pady=(0, 10))

        btns = tk.Frame(side, bg="#1e1e24")
        btns.pack(anchor="w", side="bottom", pady=(8, 2))
        tk.Button(
            btns,
            text="\u2713 Accept",
            command=self._accept,
            font=("Arial", 12, "bold"),
            bg="#2e7d32",
            fg="white",
            width=11,
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            btns, text="Centre", command=self._centre, font=("Arial", 12), width=10
        ).pack(side="left")

    def _kbd(self, fn):
        def handler(event):
            if isinstance(self.root.focus_get(), tk.Entry):
                return
            return fn()

        return handler

    def _bind_keys(self):
        r = self.root
        for k in ("<Return>", "y", "Y"):
            r.bind(k, self._kbd(self._accept))
        for k in ("<Escape>", "q", "Q"):
            r.bind(k, self._kbd(self._exit))
        for k in ("c", "C"):
            r.bind(k, self._kbd(self._centre))
        for k in ("<plus>", "<equal>", "<KP_Add>"):
            r.bind(k, self._kbd(self.size_ctrl.inc))
        for k in ("<minus>", "<underscore>", "<KP_Subtract>"):
            r.bind(k, self._kbd(self.size_ctrl.dec))
        r.focus_set()

    def _box_disp(self):
        fw = max(1, round(self.pct / 100.0 * self.disp_w))
        fh = max(1, round(fw * self.disp_h / self.disp_w))
        return fw, fh

    def _clamp_center(self):
        fw, fh = self._box_disp()
        hw = (fw / 2) / self.disp_w
        hh = (fh / 2) / self.disp_h
        self.cx_frac = min(max(self.cx_frac, hw), 1 - hw)
        self.cy_frac = min(max(self.cy_frac, hh), 1 - hh)

    def _on_size_change(self, pct):
        self.pct = pct
        self._clamp_center()
        self._render()

    def _on_drag(self, event):
        self.cx_frac = min(max(event.x, 0), self.disp_w) / self.disp_w
        self.cy_frac = min(max(event.y, 0), self.disp_h) / self.disp_h
        self._clamp_center()
        self._render()

    def _centre(self):
        self.cx_frac, self.cy_frac = 0.5, 0.5
        self._clamp_center()
        self._render()

    def _render(self):
        fw, fh = self._box_disp()
        cx = self.cx_frac * self.disp_w
        cy = self.cy_frac * self.disp_h
        x0 = max(0, min(round(cx - fw / 2), self.disp_w - fw))
        y0 = max(0, min(round(cy - fh / 2), self.disp_h - fh))
        x1, y1 = x0 + fw, y0 + fh

        patch = self.base_disp.crop((x0, y0, x1, y1))
        self._patch_photo = ImageTk.PhotoImage(patch)
        self.canvas.itemconfig(self.patch_item, image=self._patch_photo, state="normal")
        self.canvas.coords(self.patch_item, x0, y0)
        self.canvas.coords(self.box_rect, x0, y0, x1, y1)

        tw, th = self._preview_wh
        self._preview_photo = ImageTk.PhotoImage(patch.resize((tw, th), _RESAMPLE))
        self.preview_label.config(image=self._preview_photo)

        self.status_var.set(
            f"Crop {self.pct}% of width  (~{100.0 / self.pct:.2f}x zoom)"
        )

    def _accept(self):
        self.result = CropBox(self.pct, self.cx_frac, self.cy_frac)
        self.root.destroy()

    def _exit(self):
        self.result = None
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._exit)
        self.root.mainloop()


def zoom_prev_interactive(
    base_image_path, window_title="Zoom into the previous image", initial=None
):
    """Returns a CropBox on ACCEPT, or None if the user EXITS."""
    app = _ZoomApp(base_image_path, window_title, initial)
    app.run()
    return app.result


# ===========================================================================

if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if args and args[0] == "--zoom" and len(args) >= 2:
        _c = zoom_prev_interactive(args[1])
        print("CropBox:", _c)
        if _c is not None:
            crop_and_zoom(args[1], _c, "zoom_test.png")
            print("Wrote zoom_test.png")
    elif len(args) >= 2:
        _ps = place_overlays_interactive(args[0], args[1])
        print("Placements:", _ps)
        if _ps:
            composite_overlays(args[0], args[1], _ps, "manual_placement_test.png")
            print("Wrote manual_placement_test.png")
    else:
        print("usage:")
        print(
            "  python MANUAL_STOCK_PLACEMENT.py BASE OVERLAY     # placement (stamp many)"
        )
        print("  python MANUAL_STOCK_PLACEMENT.py --zoom BASE      # zoom / crop")
