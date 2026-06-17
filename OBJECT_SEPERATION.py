# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "ultralytics>=8.3",
#   "pillow>=10",
#   "numpy>=1.24",
# ]
# ///
"""
OBJECT_SEPERATION.py
====================

Click objects in an image -> each click is segmented with MobileSAM (via
Ultralytics) and added to the subject layer -> refine -> apply effects -> save.

SELECTING
---------
    * left click          -> add the region you clicked (separate clicks stay
                              separate; they do not merge into one blob)
    * Shift + left click    -> remove the region you clicked
    * "Extend selected area (manual draw)" -> draw a loop to add it
    * "Reduce selected area (manual draw)" -> draw a line ACROSS a part to split
                              it off; the smaller piece is removed by default and
                              "Toggle removed area" flips which piece goes.

STILL EFFECTS (saved as an image)
---------------------------------
    Add outline · Highlight subject · Add soft shadow · Bokeh background ·
    Monochrome background · Remove background -> white.
    These bake onto the picture, stack in any order, and are undoable.

ANIMATED EFFECTS (saved as an MP4)
----------------------------------
    Animated border [glow in/out] · [sweep] · Jiggle (subtle motion) ·
    Parallax orbit · Parallax zoom · Depth rack focus.
    These are toggles: clicking one arms it (it turns green with a tick). When
    one is armed, "Finish" renders an MP4 instead of a still. Border styles send
    light around the subject's edge; Jiggle wobbles the subject; the two Parallax
    styles orbit / breathe the subject and an inpainted background at different
    rates for depth; Depth rack focus gently shifts sharpness between background
    and subject.

RUNNING
-------
    uv run OBJECT_SEPERATION.py <image-path>            -> ./temp/sep-edit-<name>
    uv run OBJECT_SEPERATION.py <image-path> --beside   -> <dir>/sep-edit-<name>
    from OBJECT_SEPERATION import run_editor; run_editor("photo.png")

First run downloads the MobileSAM weights (~40 MB) automatically.
On Linux you may need the system Tk package once: `sudo apt install python3-tk`.

TESTING
-------
    uv run OBJECT_SEPERATION.py stickman-CACHE/stock_footage/wiki-img-e33fdcc1b657.jpg
    ...
"""

import os
import sys
import threading

try:
    import tkinter as tk
    from tkinter import messagebox
except Exception as exc:  # pragma: no cover
    sys.stderr.write(
        "Tkinter is required for the GUI but could not be imported.\n"
        "On Debian/Ubuntu install it with:  sudo apt install python3-tk\n"
        f"Original error: {exc}\n"
    )
    raise

import numpy as np
from PIL import Image, ImageTk


MODEL_NAME = "mobile_sam.pt"      # or "sam2_t.pt", "sam2_b.pt", "sam_b.pt"
SIDEBAR_WIDTH = 300
WINDOW_SIZE = "1320x900"
UNDO_LIMIT = 20
BLUE = (40, 110, 255)             # selection outline colour (RGB)
ACTIVE_BG = "#2f6fed"             # "drawing in progress" highlight
ARMED_BG = "#1f9d3d"             # armed animated effect (green)
SIDEBAR_BG = "#f2f2f2"

DRAW_LABEL = "Extend selected area (manual draw)"
CUT_LABEL = "Reduce selected area (manual draw)"

ANIM_DEFS = [  # (style key, button label)
    ("glow", "Animated border [glow in/out]"),
    ("sweep", "Animated border [sweep]"),
    ("jiggle", "Jiggle (subtle motion)"),
    ("parallax", "Parallax orbit (2.5D depth)"),
    ("parallax_zoom", "Parallax zoom (depth breathe)"),
    ("rackfocus", "Depth rack focus"),
]


class ObjectSeparator(tk.Tk):
    def __init__(self, image_path: str, output_dir: str):
        super().__init__()
        self.image_path = image_path
        self.output_dir = output_dir
        self.title("Object Separation")
        self.geometry(WINDOW_SIZE)
        self.minsize(920, 560)

        pil = Image.open(image_path)
        self.alpha = pil.getchannel("A") if "A" in pil.getbands() else None
        self.rgb_clean = np.array(pil.convert("RGB"))
        self.work = self.rgb_clean.copy()
        self.H, self.W = self.rgb_clean.shape[:2]

        # selection state
        self.model = None
        self._model_ready = False
        self._model_error = None
        self.points = []
        self.labels = []
        self.click_masks = []
        self.sam_mask = None
        self.manual_add = np.zeros((self.H, self.W), bool)
        self.manual_remove = np.zeros((self.H, self.W), bool)
        self.object_mask = None
        self.selected_item = None

        # pending cut state
        self._cut_active = False
        self._cut_components = []
        self._cut_line = None
        self._cut_target = None
        self._cut_base_remove = None
        self._cut_removed_idx = 0

        # interaction / view / output
        self.mode = "select"
        self.show_result = False
        self.anim_style = None        # None or one of ANIM_DEFS keys
        self.saved_path = None        # set on finish; returned by run_editor
        self._stroke_img = []
        self._stroke_canvas = []
        self.undo_stack = []

        self._scale, self._off_x, self._off_y, self._photo = 1.0, 0, 0, None

        self._build_ui()
        self.canvas.bind("<Configure>", lambda e: self._render())
        self.after(50, self._render)
        # Load the model in the background so the first-run download + init
        # never freezes the window (which looks like a hang).
        self.status.set("Loading MobileSAM (first run downloads ~40 MB)...")
        threading.Thread(target=self._prewarm_model, daemon=True).start()
        self.after(300, self._poll_model)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        sidebar = tk.Frame(self, width=SIDEBAR_WIDTH, bg=SIDEBAR_BG)
        sidebar.pack(side="right", fill="y")
        sidebar.pack_propagate(False)

        canvas_frame = tk.Frame(self, bg="#1e1e1e")
        canvas_frame.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_frame, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        # --- pinned top: Undo ----------------------------------------------
        topbar = tk.Frame(sidebar, bg=SIDEBAR_BG)
        topbar.pack(side="top", fill="x", padx=12, pady=(10, 2))
        self._undo_icon = self._make_undo_icon()
        self.undo_btn = tk.Button(topbar, text=" Undo", command=self._undo,
                                  state="disabled", compound="left")
        if self._undo_icon is not None:
            self.undo_btn.config(image=self._undo_icon)
        else:
            self.undo_btn.config(text="\u21B6 Undo")
        self.undo_btn.pack(side="left")
        tk.Frame(sidebar, height=1, bg="#cccccc").pack(side="top", fill="x",
                                                       padx=12, pady=6)

        # --- pinned bottom: status + finish --------------------------------
        self.finish_btn = tk.Button(
            sidebar, text="Finish edits and continue", command=self._finish,
            bg=ARMED_BG, fg="white", activebackground="#178233",
            activeforeground="white", font=("TkDefaultFont", 10, "bold"),
            relief="flat",
        )
        self.finish_btn.pack(side="bottom", fill="x", padx=12, pady=12, ipady=6)
        self.status = tk.StringVar(value="Click an object to begin.")
        tk.Label(sidebar, textvariable=self.status, bg=SIDEBAR_BG, fg="#444",
                 wraplength=SIDEBAR_WIDTH - 24, justify="left", anchor="w"
                 ).pack(side="bottom", fill="x", padx=12, pady=(0, 4))

        # --- scrollable middle ---------------------------------------------
        mid = tk.Frame(sidebar, bg=SIDEBAR_BG)
        mid.pack(side="top", fill="both", expand=True)
        vsb = tk.Scrollbar(mid, orient="vertical")
        vsb.pack(side="right", fill="y")
        self._scroll_canvas = tk.Canvas(mid, bg=SIDEBAR_BG, highlightthickness=0,
                                        yscrollcommand=vsb.set)
        self._scroll_canvas.pack(side="left", fill="both", expand=True)
        vsb.config(command=self._scroll_canvas.yview)
        inner = tk.Frame(self._scroll_canvas, bg=SIDEBAR_BG)
        self._inner_win = self._scroll_canvas.create_window((0, 0), window=inner,
                                                            anchor="nw")
        inner.bind("<Configure>", lambda e: self._scroll_canvas.configure(
            scrollregion=self._scroll_canvas.bbox("all")))
        self._scroll_canvas.bind("<Configure>", lambda e: self._scroll_canvas.itemconfig(
            self._inner_win, width=e.width))
        self._scroll_canvas.bind("<Enter>", lambda e: self._bind_wheel())
        self._scroll_canvas.bind("<Leave>", lambda e: self._unbind_wheel())

        self._build_controls(inner)

    def _build_controls(self, inner):
        pad = {"padx": 12, "pady": (4, 0)}
        tk.Label(inner, text="How to select", bg=SIDEBAR_BG,
                 font=("TkDefaultFont", 11, "bold")).pack(anchor="w", **pad)
        tk.Label(
            inner, bg=SIDEBAR_BG, justify="left", wraplength=SIDEBAR_WIDTH - 40,
            text="Click an object to select it.\n"
                 "Click another area to add it too.\n"
                 "Shift+Click an area to remove it.",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        tk.Frame(inner, height=1, bg="#cccccc").pack(fill="x", padx=12, pady=6)

        tk.Label(inner, text="Selected", bg=SIDEBAR_BG,
                 font=("TkDefaultFont", 11, "bold")).pack(anchor="w", **pad)
        self.listbox = tk.Listbox(inner, height=3, exportselection=False,
                                  activestyle="dotbox")
        self.listbox.pack(fill="x", padx=12, pady=(2, 4))
        self.listbox.bind("<<ListboxSelect>>", self._on_item_select)

        self.draw_btn = tk.Button(inner, text=DRAW_LABEL,
                                  command=lambda: self._toggle_mode("draw"))
        self.draw_btn.pack(fill="x", padx=12, pady=(0, 4))
        self._btn_bg = self.draw_btn.cget("background")
        self._btn_fg = self.draw_btn.cget("foreground")
        self._btn_abg = self.draw_btn.cget("activebackground")
        self._btn_afg = self.draw_btn.cget("activeforeground")

        self.cut_btn = tk.Button(inner, text=CUT_LABEL,
                                 command=lambda: self._toggle_mode("cut"))
        self.cut_btn.pack(fill="x", padx=12, pady=(0, 4))
        self.toggle_cut_btn = tk.Button(inner, text="Toggle removed area",
                                        state="disabled",
                                        command=self._toggle_removed_area)
        self.toggle_cut_btn.pack(fill="x", padx=12, pady=(0, 4))
        self.reset_btn = tk.Button(inner, text="Discard selection & try again",
                                   command=self._reset_selection)
        self.reset_btn.pack(fill="x", padx=12, pady=(0, 6))

        tk.Frame(inner, height=1, bg="#cccccc").pack(fill="x", padx=12, pady=6)

        tk.Label(inner, text="Still effects (saved as image)", bg=SIDEBAR_BG,
                 font=("TkDefaultFont", 11, "bold")).pack(anchor="w", **pad)
        self.outline_btn = tk.Button(inner, text="Add outline", state="disabled",
                                     command=self._add_outline)
        self.outline_btn.pack(fill="x", padx=12, pady=(2, 4))
        self.hl_btn = tk.Button(inner, text="Highlight subject", state="disabled",
                                command=self._highlight)
        self.hl_btn.pack(fill="x", padx=12, pady=(0, 4))
        self.shadow_btn = tk.Button(inner, text="Add soft shadow", state="disabled",
                                    command=self._soft_shadow)
        self.shadow_btn.pack(fill="x", padx=12, pady=(0, 4))
        self.bokeh_btn = tk.Button(inner, text="Bokeh background", state="disabled",
                                   command=self._bokeh)
        self.bokeh_btn.pack(fill="x", padx=12, pady=(0, 4))
        self.mono_btn = tk.Button(inner, text="Monochrome background",
                                  state="disabled", command=self._mono_background)
        self.mono_btn.pack(fill="x", padx=12, pady=(0, 4))
        self.bg_btn = tk.Button(inner, text="Remove background \u2192 white",
                                state="disabled", command=self._remove_background)
        self.bg_btn.pack(fill="x", padx=12, pady=(0, 6))

        tk.Frame(inner, height=1, bg="#cccccc").pack(fill="x", padx=12, pady=6)

        tk.Label(inner, text="Animated effects (saved as MP4)", bg=SIDEBAR_BG,
                 font=("TkDefaultFont", 11, "bold")).pack(anchor="w", **pad)
        self._anim_buttons = []
        for style, label in ANIM_DEFS:
            b = tk.Button(inner, text=label, state="disabled",
                          command=lambda s=style: self._toggle_anim(s))
            b.pack(fill="x", padx=12, pady=(2, 2))
            self._anim_buttons.append((style, b, label))

    def _make_undo_icon(self):
        try:
            import math
            from PIL import ImageDraw
            size = 20
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            cx, cy, r = 10.0, 10.5, 6.0
            col = (45, 45, 45, 255)
            start, end, n = -45, 205, 28
            pts = []
            for i in range(n + 1):
                a = math.radians(start + (end - start) * i / n)
                pts.append((cx + r * math.cos(a), cy - r * math.sin(a)))
            d.line(pts, fill=col, width=2, joint="curve")
            (x0, y0), (x1, y1) = pts[0], pts[1]
            ang = math.atan2(y0 - y1, x0 - x1)
            L = 5.5
            p1 = (x0 - L * math.cos(ang - math.radians(30)),
                  y0 - L * math.sin(ang - math.radians(30)))
            p2 = (x0 - L * math.cos(ang + math.radians(30)),
                  y0 - L * math.sin(ang + math.radians(30)))
            d.polygon([(x0, y0), p1, p2], fill=col)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    # ------------------------------------------------------ scroll wheel
    def _bind_wheel(self):
        self._scroll_canvas.bind_all("<MouseWheel>", self._on_wheel)
        self._scroll_canvas.bind_all("<Button-4>", self._on_wheel)
        self._scroll_canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self):
        self._scroll_canvas.unbind_all("<MouseWheel>")
        self._scroll_canvas.unbind_all("<Button-4>")
        self._scroll_canvas.unbind_all("<Button-5>")

    def _on_wheel(self, e):
        if getattr(e, "num", None) == 4:
            self._scroll_canvas.yview_scroll(-1, "units")
        elif getattr(e, "num", None) == 5:
            self._scroll_canvas.yview_scroll(1, "units")
        else:
            self._scroll_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def _set_effect_buttons(self, enabled):
        st = "normal" if enabled else "disabled"
        for b in (self.outline_btn, self.hl_btn, self.shadow_btn,
                  self.bokeh_btn, self.mono_btn, self.bg_btn):
            b.config(state=st)
        for _, b, _ in self._anim_buttons:
            b.config(state=st)
        self._refresh_anim_buttons()

    # ------------------------------------------------------- coordinate map
    def _canvas_to_image(self, cx, cy, clamp=False):
        ix = (cx - self._off_x) / self._scale
        iy = (cy - self._off_y) / self._scale
        if clamp:
            return int(min(max(ix, 0), self.W - 1)), int(min(max(iy, 0), self.H - 1))
        if 0 <= ix < self.W and 0 <= iy < self.H:
            return int(ix), int(iy)
        return None

    # -------------------------------------------------------------- render
    def _render(self):
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        self._scale = min(cw / self.W, ch / self.H)
        disp_w = max(int(self.W * self._scale), 1)
        disp_h = max(int(self.H * self._scale), 1)
        self._off_x = (cw - disp_w) // 2
        self._off_y = (ch - disp_h) // 2

        frame = self._composite_display()
        img = Image.fromarray(frame).resize((disp_w, disp_h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(self._off_x, self._off_y, anchor="nw",
                                 image=self._photo)
        if not self.show_result:
            for (x, y), lab in zip(self.points, self.labels):
                sx = self._off_x + x * self._scale
                sy = self._off_y + y * self._scale
                color = "#2ecc71" if lab == 1 else "#e74c3c"
                self.canvas.create_oval(sx - 4, sy - 4, sx + 4, sy + 4,
                                        fill=color, outline="white")

    def _composite_display(self):
        out = self.work.copy()
        if self.show_result or self.object_mask is None:
            return out
        try:
            import cv2
        except Exception:
            cv2 = None
        dim_region = (self.object_mask if self.selected_item == "background"
                      else ~self.object_mask)
        out[dim_region] = (out[dim_region] * 0.45).astype(np.uint8)
        if cv2 is not None:
            m = self.object_mask.astype(np.uint8)
            contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(out, contours, -1, BLUE,
                             max(2, int(round(min(self.H, self.W) / 500))))
        return out

    # --------------------------------------------------------- mouse events
    def _on_press(self, event):
        if self.mode in ("draw", "cut"):
            self._stroke_img = [self._canvas_to_image(event.x, event.y, clamp=True)]
            self._stroke_canvas = [(event.x, event.y)]
            return
        pt = self._canvas_to_image(event.x, event.y)
        if pt is None:
            return
        self._segment_click(pt[0], pt[1], subtract=bool(event.state & 0x0001))

    def _on_drag(self, event):
        if self.mode not in ("draw", "cut"):
            return
        self._stroke_img.append(self._canvas_to_image(event.x, event.y, clamp=True))
        if self._stroke_canvas:
            px, py = self._stroke_canvas[-1]
            self.canvas.create_line(px, py, event.x, event.y, fill="#ffcc00",
                                    width=2, tags="stroke")
        self._stroke_canvas.append((event.x, event.y))

    def _on_release(self, event):
        if self.mode == "draw":
            self._finish_stroke()
        elif self.mode == "cut":
            self._finish_cut()

    def _on_item_select(self, _event):
        sel = self.listbox.curselection()
        if not sel:
            return
        self.selected_item = "object" if sel[0] == 0 else "background"
        self._set_effect_buttons(True)
        self.status.set(f"Selected: {self.selected_item}.")
        self._render()

    # ------------------------------------------------------------- modes
    def _toggle_mode(self, which):
        self._set_mode("select" if self.mode == which else which)

    def _style_mode_btn(self, btn, active, on_text, off_text):
        if active:
            btn.config(text=on_text, bg=ACTIVE_BG, fg="white",
                       activebackground=ACTIVE_BG, activeforeground="white",
                       font=("TkDefaultFont", 9, "bold"), relief="sunken")
        else:
            btn.config(text=off_text, bg=self._btn_bg, fg=self._btn_fg,
                       activebackground=self._btn_abg, activeforeground=self._btn_afg,
                       font=("TkDefaultFont", 9, "normal"), relief="raised")

    def _set_mode(self, mode):
        self.mode = mode
        self._style_mode_btn(self.draw_btn, mode == "draw",
                             "\u25CF DRAWING - click to stop", DRAW_LABEL)
        self._style_mode_btn(self.cut_btn, mode == "cut",
                             "\u25CF DRAWING LINE - click to stop", CUT_LABEL)
        self.canvas.config(cursor="crosshair" if mode in ("draw", "cut") else "")
        if mode == "draw":
            self.status.set(f"Draw a loop to add to the "
                            f"{self.selected_item or 'object'}. Release to apply.")
        elif mode == "cut":
            self.status.set("Draw a line across a part of the subject to remove it.")
        else:
            self.status.set("Back to click-to-select.")

    def _toggle_anim(self, style):
        self.anim_style = None if self.anim_style == style else style
        self._refresh_anim_buttons()
        if self.anim_style:
            self.status.set("Armed - 'Finish' will export an MP4 of this border.")
        else:
            self.status.set("Animated border off - 'Finish' saves a still image.")

    def _refresh_anim_buttons(self):
        for style, btn, label in self._anim_buttons:
            if self.anim_style == style:
                btn.config(text="\u2713 " + label, bg=ARMED_BG, fg="white",
                           activebackground="#178233", activeforeground="white",
                           font=("TkDefaultFont", 9, "bold"), relief="sunken")
            else:
                btn.config(text=label, bg=self._btn_bg, fg=self._btn_fg,
                           activebackground=self._btn_abg,
                           activeforeground=self._btn_afg,
                           font=("TkDefaultFont", 9, "normal"), relief="raised")
        if self.anim_style:
            self.finish_btn.config(text="Finish & export MP4 \u25B6")
        else:
            self.finish_btn.config(text="Finish edits and continue")

    def _reset_selection(self):
        self.points.clear()
        self.labels.clear()
        self.click_masks.clear()
        self.sam_mask = None
        self.manual_add[:] = False
        self.manual_remove[:] = False
        self.object_mask = None
        self.selected_item = None
        self.show_result = False
        self.anim_style = None
        self.undo_stack.clear()
        self.undo_btn.config(state="disabled")
        self._clear_cut()
        self.listbox.delete(0, "end")
        self._set_effect_buttons(False)
        self._set_mode("select")
        self.status.set("Selection discarded. Click an object to begin.")
        self._render()

    # --------------------------------------------------------------- undo
    def _snapshot(self):
        self.undo_stack.append({
            "work": self.work,
            "click_masks": list(self.click_masks),
            "manual_add": self.manual_add.copy(),
            "manual_remove": self.manual_remove.copy(),
            "points": list(self.points),
            "labels": list(self.labels),
            "selected_item": self.selected_item,
            "show_result": self.show_result,
            "alpha": self.alpha,
        })
        if len(self.undo_stack) > UNDO_LIMIT:
            self.undo_stack.pop(0)
        self.undo_btn.config(state="normal")

    def _undo(self):
        if not self.undo_stack:
            return
        s = self.undo_stack.pop()
        self.work = s["work"]
        self.click_masks = list(s["click_masks"])
        self.manual_add = s["manual_add"]
        self.manual_remove = s["manual_remove"]
        self.points = list(s["points"])
        self.labels = list(s["labels"])
        self.selected_item = s["selected_item"]
        self.show_result = s["show_result"]
        self.alpha = s["alpha"]
        if not self.undo_stack:
            self.undo_btn.config(state="disabled")
        self._clear_cut()
        self._recompute_from_clicks()
        self.status.set("Undid the last step.")

    # -------------------------------------------------------- segmentation
    def _prewarm_model(self):
        """Runs in a background thread — must NOT touch Tk widgets. Loads the
        model (downloading weights on first run) and reports back via plain
        flags that _poll_model (main thread) checks."""
        try:
            from ultralytics import SAM
            self.model = SAM(MODEL_NAME)
            self._model_ready = True
        except Exception as exc:
            self._model_error = str(exc)

    def _poll_model(self):
        if self._model_error is not None and self._model_error != "__shown__":
            self.config(cursor="")
            self.status.set("Model failed to load — see the dialog.")
            messagebox.showerror(
                "Model error",
                "Could not load MobileSAM:\n"
                f"{self._model_error}\n\n"
                "If you launched this from the pipeline (main.py), the editor "
                "runs in the PROJECT environment, which needs the dependency "
                "installed once:\n\n    uv add ultralytics",
            )
            self._model_error = "__shown__"
            return
        if self._model_ready:
            self.status.set("Model ready. Click an object to begin.")
            return
        self.after(300, self._poll_model)

    def _segment_click(self, x, y, subtract):
        if subtract and self.object_mask is None:
            self.status.set("Select something first, then Shift+Click to remove.")
            return
        if self.model is None:
            if self._model_error not in (None, "__shown__"):
                self._poll_model()   # surface the error dialog
            else:
                self.status.set("Model still loading — one moment...")
            return
        model = self.model

        self.status.set("Segmenting...")
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            bgr = self.rgb_clean[:, :, ::-1]
            results = model(bgr, points=[[x, y]], labels=[1], verbose=False)
            mask = self._best_mask(results)
        except Exception as exc:
            self.config(cursor="")
            messagebox.showerror("Segmentation error", str(exc))
            return
        self.config(cursor="")
        if mask is None:
            self.status.set("Nothing found there - try another spot.")
            return

        self._snapshot()
        self._clear_cut()
        self.show_result = False
        self.click_masks.append((mask, 0 if subtract else 1))
        self.points.append([x, y])
        self.labels.append(0 if subtract else 1)
        self._recompute_from_clicks()
        n_add = sum(1 for _, l in self.click_masks if l == 1)
        n_sub = sum(1 for _, l in self.click_masks if l == 0)
        self.status.set(f"{n_add} area(s) added, {n_sub} removed. "
                        f"Click more, draw, or add effects.")

    def _best_mask(self, results):
        if not results:
            return None
        masks = results[0].masks
        if masks is None or masks.data is None or len(masks.data) == 0:
            return None
        arr = masks.data.cpu().numpy()
        idx = max(range(len(arr)), key=lambda i: arr[i].sum())
        m = arr[idx] > 0.5
        if m.shape != (self.H, self.W):
            try:
                import cv2
                m = cv2.resize(m.astype(np.uint8), (self.W, self.H),
                               interpolation=cv2.INTER_NEAREST).astype(bool)
            except Exception:
                return None
        return m

    # ----------------------------------------------------- compose + items
    def _recompute_from_clicks(self):
        fg = np.zeros((self.H, self.W), bool)
        for m, lab in self.click_masks:
            if lab == 1:
                fg |= m
            else:
                fg &= ~m
        self.sam_mask = fg if fg.any() else None
        self._recompute_mask()

    def _recompute_mask(self):
        combined = (self.sam_mask.copy() if self.sam_mask is not None
                    else np.zeros((self.H, self.W), bool))
        combined |= self.manual_add
        combined &= ~self.manual_remove
        self.object_mask = combined if combined.any() else None

        if self.object_mask is not None:
            self._populate_items()
            if self.selected_item is None:
                self.listbox.selection_clear(0, "end")
                self.listbox.selection_set(0)
                self.selected_item = "object"
            self._set_effect_buttons(True)
        else:
            self.listbox.delete(0, "end")
            self._set_effect_buttons(False)
        self._render()

    def _populate_items(self):
        obj_px = int(self.object_mask.sum())
        bg_px = self.H * self.W - obj_px
        keep = self.selected_item
        self.listbox.delete(0, "end")
        self.listbox.insert("end", f"Subject  ({obj_px:,} px)")
        self.listbox.insert("end", f"Background  ({bg_px:,} px)")
        if keep == "background":
            self.listbox.selection_set(1)
        elif keep == "object":
            self.listbox.selection_set(0)

    # ---------------------------------------------------- manual draw (loop)
    def _finish_stroke(self):
        self.canvas.delete("stroke")
        pts = self._stroke_img
        self._stroke_img, self._stroke_canvas = [], []
        if len(pts) < 3:
            return
        try:
            import cv2
        except Exception as exc:
            messagebox.showerror("OpenCV missing", str(exc))
            return
        poly = np.array(pts, dtype=np.int32)
        m = np.zeros((self.H, self.W), np.uint8)
        cv2.fillPoly(m, [poly], 1)
        brush = max(3, int(round(min(self.H, self.W) / 150)))
        cv2.polylines(m, [poly], True, 1, brush)
        add = m.astype(bool)

        self._snapshot()
        self._clear_cut()
        self.show_result = False
        if self.selected_item == "background":
            self.manual_remove |= add
            self.status.set("Drawn region removed from the subject.")
        else:
            self.manual_add |= add
            self.status.set("Drawn region added to the subject.")
        self._recompute_mask()

    # ------------------------------------------------ cut (draw line to remove)
    def _clear_cut(self):
        self._cut_active = False
        self._cut_components = []
        self._cut_line = None
        self._cut_target = None
        self._cut_base_remove = None
        self.toggle_cut_btn.config(state="disabled")

    def _finish_cut(self):
        self.canvas.delete("stroke")
        pts = self._stroke_img
        self._stroke_img, self._stroke_canvas = [], []
        if self.object_mask is None:
            self.status.set("Select something first, then draw a line to cut it.")
            return
        if len(pts) < 2:
            return
        try:
            import cv2
        except Exception as exc:
            messagebox.showerror("OpenCV missing", str(exc))
            return

        obj = self.object_mask
        brush = max(3, int(round(min(self.H, self.W) / 200)))
        line = np.zeros((self.H, self.W), np.uint8)
        cv2.polylines(line, [np.array(pts, np.int32)], False, 1, brush)
        line_bool = line.astype(bool)

        num0, lbl0 = cv2.connectedComponents(obj.astype(np.uint8), connectivity=8)
        line_dil = cv2.dilate(line, np.ones((3, 3), np.uint8), iterations=1) > 0
        touched = [int(l) for l in np.unique(lbl0[line_dil]) if l != 0]
        if not touched:
            self.status.set("Draw the line across the part you want to remove.")
            return
        target = np.isin(lbl0, touched)

        severed = (target & ~line_bool).astype(np.uint8)
        numt, lblt = cv2.connectedComponents(severed, connectivity=8)
        min_area = max(30, int(target.sum() * 0.005))
        subpieces = [lblt == i for i in range(1, numt)]
        subpieces = [c for c in subpieces if int(c.sum()) >= min_area]
        if len(subpieces) < 2:
            self.status.set("The line didn't split that part - draw fully across it.")
            return

        self._snapshot()
        self._clear_cut()
        self.show_result = False
        self._cut_active = True
        self._cut_components = subpieces
        self._cut_line = line_bool
        self._cut_target = target
        self._cut_base_remove = self.manual_remove.copy()
        self._cut_removed_idx = int(np.argmin([c.sum() for c in subpieces]))
        self._apply_cut()
        self.toggle_cut_btn.config(state="normal")
        self.status.set(f"Split that part into {len(subpieces)}; removed the "
                        f"smallest. Wrong one? Use 'Toggle removed area'.")

    def _apply_cut(self):
        removed = (self._cut_components[self._cut_removed_idx]
                   | (self._cut_line & self._cut_target))
        self.manual_remove = self._cut_base_remove | removed
        self._recompute_mask()

    def _toggle_removed_area(self):
        if not self._cut_active or not self._cut_components:
            return
        self._cut_removed_idx = (self._cut_removed_idx + 1) % len(self._cut_components)
        self._apply_cut()
        self.status.set(f"Now removing piece {self._cut_removed_idx + 1} "
                        f"of {len(self._cut_components)} from that part.")

    # --------------------------------------------------------- still effects
    def _add_outline(self):
        if self.object_mask is None:
            return
        try:
            import cv2
        except Exception as exc:
            messagebox.showerror("OpenCV missing", str(exc))
            return
        self._snapshot()
        self._clear_cut()
        new = self.work.copy()
        m = self.object_mask.astype(np.uint8)
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        thickness = max(2, int(round(min(self.H, self.W) / 400)))
        cv2.drawContours(new, contours, -1, (0, 0, 0), thickness)
        self.work = new
        self.show_result = True
        self.status.set("Outline added - showing final image. Use Undo to go back.")
        self._render()

    def _highlight(self):
        if self.object_mask is None:
            return
        self._snapshot()
        self._clear_cut()
        m = self.object_mask
        new = self.work.astype(np.float32)
        new[~m] *= 0.72          # darken the background (stacks on repeat clicks)
        new[m] *= 1.18           # lift the subject so it stands out
        self.work = np.clip(new, 0, 255).astype(np.uint8)
        self.show_result = True
        self.status.set("Subject highlighted, background dimmed. Use Undo to go back.")
        self._render()

    def _soft_shadow(self):
        if self.object_mask is None:
            return
        try:
            import cv2
        except Exception as exc:
            messagebox.showerror("OpenCV missing", str(exc))
            return
        self._snapshot()
        self._clear_cut()
        m = self.object_mask.astype(np.float32)
        k = max(9, int(min(self.H, self.W) / 60) | 1)
        blurred = cv2.GaussianBlur(m, (k, k), 0)
        off = max(2, int(min(self.H, self.W) / 200))
        warp = np.float32([[1, 0, off], [0, 1, off]])
        blurred = cv2.warpAffine(blurred, warp, (self.W, self.H))
        shadow = blurred * (~self.object_mask)
        factor = (1.0 - 0.55 * shadow)[..., None]
        new = self.work.astype(np.float32) * factor
        new[self.object_mask] = self.work[self.object_mask]
        self.work = np.clip(new, 0, 255).astype(np.uint8)
        self.show_result = True
        self.status.set("Soft shadow added around the subject. Use Undo to go back.")
        self._render()

    def _bokeh(self):
        if self.object_mask is None:
            return
        try:
            import cv2
        except Exception as exc:
            messagebox.showerror("OpenCV missing", str(exc))
            return
        self._snapshot()
        self._clear_cut()
        k = max(5, int(min(self.H, self.W) / 110) | 1)   # slight blur kernel
        blurred = cv2.GaussianBlur(self.work, (k, k), 0)
        new = self.work.copy()
        new[~self.object_mask] = blurred[~self.object_mask]  # subject stays sharp
        self.work = new
        self.show_result = True
        self.status.set("Background blurred (bokeh). Use Undo to go back.")
        self._render()

    def _mono_background(self):
        if self.object_mask is None:
            return
        self._snapshot()
        self._clear_cut()
        new = self.work.copy()
        bg = ~self.object_mask
        px = self.work[bg].astype(np.float32)
        lum = px[:, 0] * 0.299 + px[:, 1] * 0.587 + px[:, 2] * 0.114
        lum = np.clip(lum, 0, 255).astype(np.uint8)
        new[bg] = np.stack([lum, lum, lum], axis=1)
        self.work = new
        self.show_result = True
        self.status.set("Background set to monochrome. Use Undo to go back.")
        self._render()

    def _remove_background(self):
        if self.object_mask is None:
            return
        self._snapshot()
        self._clear_cut()
        new = np.full((self.H, self.W, 3), 255, np.uint8)
        new[self.object_mask] = self.work[self.object_mask]
        self.work = new
        self.alpha = None
        self.show_result = True
        self.status.set("Background removed onto white. Use Undo to go back.")
        self._render()

    # ------------------------------------------------------ animated border
    def _band_intensity(self, style, ang, t):
        """Per-edge-pixel glow intensity (0..1) for phase t in [0,1)."""
        two_pi = 2 * np.pi
        if style == "glow":
            pulse = 0.5 * (1.0 - np.cos(two_pi * t))   # 0 -> 1 -> 0, smooth
            return np.full_like(ang, 0.15 + 0.85 * pulse)
        head = -np.pi + two_pi * t                      # sweep
        d = (ang - head + np.pi) % two_pi - np.pi
        return np.exp(-(d / 0.45) ** 2)

    def _render_animation(self, out_path):
        try:
            import cv2
        except Exception as exc:
            messagebox.showerror("OpenCV missing", str(exc))
            return False
        vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"),
                             30, (self.W, self.H))
        if not vw.isOpened():
            messagebox.showerror("Video error",
                                 "Could not open the MP4 writer (mp4v codec).")
            return False
        self.config(cursor="watch")
        try:
            if self.anim_style == "jiggle":
                ok = self._frames_jiggle(vw, cv2)
            elif self.anim_style == "parallax":
                ok = self._frames_parallax(vw, cv2)
            elif self.anim_style == "parallax_zoom":
                ok = self._frames_parallax_zoom(vw, cv2)
            elif self.anim_style == "rackfocus":
                ok = self._frames_rackfocus(vw, cv2)
            else:
                ok = self._frames_border(vw, cv2)
        finally:
            vw.release()
            self.config(cursor="")
        return ok

    def _frames_border(self, vw, cv2):
        H, W = self.H, self.W
        mask = self.object_mask
        k = max(3, int(min(H, W) / 100) | 1)
        ker = np.ones((k, k), np.uint8)
        band = (cv2.dilate(mask.astype(np.uint8), ker) > 0) & \
               (cv2.erode(mask.astype(np.uint8), ker) == 0)
        ys, xs = np.where(band)
        if len(ys) == 0:
            messagebox.showerror("Animation", "The subject edge is too small to animate.")
            return False
        cy, cx = ys.mean(), xs.mean()
        ang = np.arctan2(ys - cy, xs - cx)
        frames = 60 if self.anim_style == "glow" else 90
        blur_k = max(5, int(min(H, W) / 110) | 1)
        col_bgr = np.array([255, 245, 210], np.float32)   # warm white light (BGR)
        base_bgr = self.work[:, :, ::-1].astype(np.float32)
        for f in range(frames):
            t = f / frames
            inten = np.zeros((H, W), np.float32)
            inten[ys, xs] = self._band_intensity(self.anim_style, ang, t)
            inten = cv2.GaussianBlur(inten, (blur_k, blur_k), 0)
            add = inten[..., None] * col_bgr[None, None, :]
            frame = 255.0 - (255.0 - base_bgr) * (255.0 - add) / 255.0  # screen
            vw.write(np.clip(frame, 0, 255).astype(np.uint8))
            if f % 6 == 0:
                self.status.set(f"Rendering MP4... {int(100 * f / frames)}%")
                self.update_idletasks()
        return True

    def _frames_jiggle(self, vw, cv2):
        H, W = self.H, self.W
        mask = self.object_mask
        ys, xs = np.where(mask)
        if len(ys) == 0:
            messagebox.showerror("Animation", "Select a subject to jiggle.")
            return False
        cy, cx = float(ys.mean()), float(xs.mean())
        work_bgr = self.work[:, :, ::-1]
        # inpaint the subject area to get a clean plate to move the subject over
        k = max(3, int(min(H, W) / 120) | 1)
        md = cv2.dilate(mask.astype(np.uint8) * 255, np.ones((k, k), np.uint8))
        plate = cv2.inpaint(work_bgr, md, 3, cv2.INPAINT_TELEA).astype(np.float32)
        subj = work_bgr.astype(np.float32)
        fa = max(3, int(min(H, W) / 200) | 1)
        alpha = cv2.GaussianBlur(mask.astype(np.float32), (fa, fa), 0)  # soft edge
        A = 0.68 * max(1.5, min(H, W) / 140.0)   # 20% less travel than before
        R = 0.6                                    # gentle rotation amplitude (deg)
        frames = 24                                # ~0.8s loop (fast game-item wobble)
        two_pi = 2 * np.pi
        for f in range(frames):
            t = f / frames
            dx = A * np.sin(two_pi * t)
            dy = A * 0.6 * np.sin(two_pi * t + np.pi / 2)   # slight elliptical sway
            rot = R * np.sin(two_pi * t)
            M = cv2.getRotationMatrix2D((cx, cy), rot, 1.0)
            M[0, 2] += dx
            M[1, 2] += dy
            ws = cv2.warpAffine(subj, M, (W, H), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT)
            wa = cv2.warpAffine(alpha, M, (W, H), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT,
                                borderValue=0)[..., None]
            frame = plate * (1.0 - wa) + ws * wa
            vw.write(np.clip(frame, 0, 255).astype(np.uint8))
            if f % 6 == 0:
                self.status.set(f"Rendering MP4... {int(100 * f / frames)}%")
                self.update_idletasks()
        return True

    def _frames_parallax(self, vw, cv2):
        H, W = self.H, self.W
        mask = self.object_mask
        ys, xs = np.where(mask)
        if len(ys) == 0:
            messagebox.showerror("Animation", "Select a subject for the parallax.")
            return False
        work_bgr = self.work[:, :, ::-1]
        # background plate (inpaint behind subject) + soft-edged subject layer
        k = max(3, int(min(H, W) / 120) | 1)
        md = cv2.dilate(mask.astype(np.uint8) * 255, np.ones((k, k), np.uint8))
        plate = cv2.inpaint(work_bgr, md, 3, cv2.INPAINT_TELEA).astype(np.float32)
        subj = work_bgr.astype(np.float32)
        fa = max(3, int(min(H, W) / 200) | 1)
        alpha = cv2.GaussianBlur(mask.astype(np.float32), (fa, fa), 0)
        cx, cy = W / 2.0, H / 2.0
        amp = max(3.0, min(H, W) * 0.015)      # camera orbit radius (px)
        s_bg, s_fg = 1.07, 1.03                # slight zoom so edges never show
        frames = 90                            # ~3s slow loop
        two_pi = 2 * np.pi
        for f in range(frames):
            t = f / frames
            ox, oy = np.sin(two_pi * t), np.cos(two_pi * t)   # circular orbit
            mbg = cv2.getRotationMatrix2D((cx, cy), 0, s_bg)
            mbg[0, 2] += -0.45 * amp * ox
            mbg[1, 2] += -0.30 * amp * oy
            bg = cv2.warpAffine(plate, mbg, (W, H), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT)
            mfg = cv2.getRotationMatrix2D((cx, cy), 0, s_fg)
            mfg[0, 2] += -1.0 * amp * ox          # foreground moves more -> nearer
            mfg[1, 2] += -0.65 * amp * oy
            fg = cv2.warpAffine(subj, mfg, (W, H), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT)
            wa = cv2.warpAffine(alpha, mfg, (W, H), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)[..., None]
            frame = bg * (1.0 - wa) + fg * wa
            vw.write(np.clip(frame, 0, 255).astype(np.uint8))
            if f % 6 == 0:
                self.status.set(f"Rendering MP4... {int(100 * f / frames)}%")
                self.update_idletasks()
        return True

    def _frames_parallax_zoom(self, vw, cv2):
        H, W = self.H, self.W
        mask = self.object_mask
        ys, xs = np.where(mask)
        if len(ys) == 0:
            messagebox.showerror("Animation", "Select a subject for the parallax.")
            return False
        work_bgr = self.work[:, :, ::-1]
        k = max(3, int(min(H, W) / 120) | 1)
        md = cv2.dilate(mask.astype(np.uint8) * 255, np.ones((k, k), np.uint8))
        plate = cv2.inpaint(work_bgr, md, 3, cv2.INPAINT_TELEA).astype(np.float32)
        subj = work_bgr.astype(np.float32)
        fa = max(3, int(min(H, W) / 200) | 1)
        alpha = cv2.GaussianBlur(mask.astype(np.float32), (fa, fa), 0)
        cx, cy = W / 2.0, H / 2.0
        frames = 90
        two_pi = 2 * np.pi
        amp_bg, amp_fg = 0.03, 0.07     # foreground breathes more -> reads nearer
        for f in range(frames):
            t = f / frames
            breath = 0.5 - 0.5 * np.cos(two_pi * t)   # 0->1->0; scale stays >= 1
            mbg = cv2.getRotationMatrix2D((cx, cy), 0, 1.0 + amp_bg * breath)
            bg = cv2.warpAffine(plate, mbg, (W, H), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT)
            mfg = cv2.getRotationMatrix2D((cx, cy), 0, 1.0 + amp_fg * breath)
            fg = cv2.warpAffine(subj, mfg, (W, H), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT)
            wa = cv2.warpAffine(alpha, mfg, (W, H), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)[..., None]
            frame = bg * (1.0 - wa) + fg * wa
            vw.write(np.clip(frame, 0, 255).astype(np.uint8))
            if f % 6 == 0:
                self.status.set(f"Rendering MP4... {int(100 * f / frames)}%")
                self.update_idletasks()
        return True

    def _frames_rackfocus(self, vw, cv2):
        H, W = self.H, self.W
        mask = self.object_mask
        ys, xs = np.where(mask)
        if len(ys) == 0:
            messagebox.showerror("Animation", "Select a subject for the rack focus.")
            return False
        work_bgr = self.work[:, :, ::-1]
        k = max(3, int(min(H, W) / 120) | 1)
        md = cv2.dilate(mask.astype(np.uint8) * 255, np.ones((k, k), np.uint8))
        plate = cv2.inpaint(work_bgr, md, 3, cv2.INPAINT_TELEA).astype(np.float32)
        subj = work_bgr.astype(np.float32)
        fa = max(3, int(min(H, W) / 200) | 1)
        alpha0 = cv2.GaussianBlur(mask.astype(np.float32), (fa, fa), 0)
        max_k = max(7, int(min(H, W) / 45) | 1)
        frames = 90
        two_pi = 2 * np.pi

        def blur(img, frac):
            kk = int(max_k * frac)
            if kk < 3:
                return img
            return cv2.GaussianBlur(img, (kk | 1, kk | 1), 0)

        for f in range(frames):
            t = f / frames
            p = 0.5 - 0.5 * np.cos(two_pi * t)   # 0=focus bg -> 1=focus subject -> 0
            bg = blur(plate, p)                  # bg softens as focus moves to subject
            sj = blur(subj, 1.0 - p)             # subject softens as focus moves to bg
            a = np.clip(blur(alpha0, 1.0 - p), 0, 1)[..., None]
            frame = bg * (1.0 - a) + sj * a
            vw.write(np.clip(frame, 0, 255).astype(np.uint8))
            if f % 6 == 0:
                self.status.set(f"Rendering MP4... {int(100 * f / frames)}%")
                self.update_idletasks()
        return True

    # ------------------------------------------------------------ save
    def _finish(self):
        os.makedirs(self.output_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(self.image_path))[0]

        if self.anim_style and self.object_mask is not None:
            out_path = os.path.join(self.output_dir, f"sep-edit-{stem}.mp4")
            self.status.set("Rendering MP4...")
            self.update_idletasks()
            if not self._render_animation(out_path):
                return
            print(f"[object-separation] saved video -> {out_path}")
            self.saved_path = out_path
            self.destroy()
            return

        out_path = os.path.join(self.output_dir,
                                f"sep-edit-{os.path.basename(self.image_path)}")
        result = Image.fromarray(self.work)
        if self.alpha is not None:
            result = result.convert("RGBA")
            result.putalpha(self.alpha)
        try:
            result.save(out_path)
        except (KeyError, OSError):
            out_path = os.path.splitext(out_path)[0] + ".png"
            result.save(out_path)
        print(f"[object-separation] saved -> {out_path}")
        self.saved_path = out_path
        self.destroy()


# --------------------------------------------------------------- entry points
def run_editor(image_path: str, output_dir: str = None) -> "str | None":
    """Open the editor; return the saved file path (an image, or an .mp4 if an
    animated effect was armed), or None if the window was closed without
    finishing.

    output_dir=None -> ./temp/sep-edit-<name>; otherwise saves there.
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(image_path)
    if output_dir is None:
        output_dir = os.path.join(os.getcwd(), "temp")
    app = ObjectSeparator(image_path, output_dir)
    app.mainloop()
    return getattr(app, "saved_path", None)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    beside = "--beside" in argv
    argv = [a for a in argv if a != "--beside"]
    if len(argv) != 1:
        sys.stderr.write(
            "Usage: uv run OBJECT_SEPERATION.py <image-path> [--beside]\n"
        )
        return 2
    image_path = argv[0]
    if not os.path.isfile(image_path):
        sys.stderr.write(f"File not found: {image_path}\n")
        return 1
    out_dir = os.path.dirname(os.path.abspath(image_path)) if beside else None
    run_editor(image_path, output_dir=out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
