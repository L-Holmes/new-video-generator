"""
TESTING:
uv run OBJECT_SEPERATION.py stickman-CACHE/stock_footage/wiki-img-e33fdcc1b657.jpg
"""
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
Ultralytics) and added to the subject layer -> refine -> apply effects (outline)
-> save.

SELECTING
---------
    * left click          -> add the region you clicked (separate clicks stay
                              separate; they do not merge into one blob)
    * Shift + left click    -> remove the region you clicked
    * "Extend selected area (draw)" -> draw a loop to add it to the subject
    * "Remove a part (draw line)"   -> draw a line ACROSS a part to split it off.
                              Only the blob the line crosses is affected; the
                              smaller piece is removed by default and
                              "Toggle removed area" flips which of THAT blob's
                              two pieces is removed. Other selected areas are
                              left untouched.

EFFECTS / UNDO
--------------
"Add outline" bakes a thin black outline onto the image and shows the final
result (no overlay). The Undo button (top, curved arrow) steps back the last
action.

RUNNING
-------
    uv run OBJECT_SEPERATION.py <image-path>            -> ./temp/sep-edit-<name>
    uv run OBJECT_SEPERATION.py <image-path> --beside   -> <dir>/sep-edit-<name>
    from OBJECT_SEPERATION import run_editor; run_editor("photo.png")

First run downloads the MobileSAM weights (~40 MB) automatically.
On Linux you may need the system Tk package once: `sudo apt install python3-tk`.
"""

import os
import sys

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
WINDOW_SIZE = "1320x840"
UNDO_LIMIT = 20
BLUE = (40, 110, 255)             # selection outline colour (RGB)
ACTIVE_BG = "#2f6fed"             # "drawing in progress" highlight

DRAW_LABEL = "Extend selected area (draw)"
CUT_LABEL = "Remove a part (draw line)"


class ObjectSeparator(tk.Tk):
    def __init__(self, image_path: str, output_dir: str):
        super().__init__()
        self.image_path = image_path
        self.output_dir = output_dir
        self.title("Object Separation")
        self.geometry(WINDOW_SIZE)
        self.minsize(900, 600)

        pil = Image.open(image_path)
        self.alpha = pil.getchannel("A") if "A" in pil.getbands() else None
        self.rgb_clean = np.array(pil.convert("RGB"))
        self.work = self.rgb_clean.copy()
        self.H, self.W = self.rgb_clean.shape[:2]

        # selection state
        self.model = None
        self.points = []
        self.labels = []
        self.click_masks = []
        self.sam_mask = None
        self.manual_add = np.zeros((self.H, self.W), bool)
        self.manual_remove = np.zeros((self.H, self.W), bool)
        self.object_mask = None
        self.selected_item = None

        # pending "cut" state, valid until the next action
        self._cut_active = False
        self._cut_components = []
        self._cut_line = None
        self._cut_target = None
        self._cut_base_remove = None
        self._cut_removed_idx = 0

        # interaction / view
        self.mode = "select"
        self.show_result = False
        self._stroke_img = []
        self._stroke_canvas = []
        self.undo_stack = []

        self._scale, self._off_x, self._off_y, self._photo = 1.0, 0, 0, None

        self._build_ui()
        self.bind("<Configure>", lambda e: self._render())
        self.after(50, self._render)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        sidebar = tk.Frame(self, width=SIDEBAR_WIDTH, bg="#f2f2f2")
        sidebar.pack(side="right", fill="y")
        sidebar.pack_propagate(False)

        canvas_frame = tk.Frame(self, bg="#1e1e1e")
        canvas_frame.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_frame, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        # --- top bar: Undo --------------------------------------------------
        topbar = tk.Frame(sidebar, bg="#f2f2f2")
        topbar.pack(fill="x", padx=12, pady=(10, 2))
        self._undo_icon = self._make_undo_icon()
        self.undo_btn = tk.Button(topbar, text=" Undo", command=self._undo,
                                  state="disabled", compound="left")
        if self._undo_icon is not None:
            self.undo_btn.config(image=self._undo_icon)
        else:
            self.undo_btn.config(text="\u21B6 Undo")
        self.undo_btn.pack(side="left")

        tk.Frame(sidebar, height=1, bg="#cccccc").pack(fill="x", padx=12, pady=6)

        pad = {"padx": 12, "pady": (4, 0)}
        tk.Label(sidebar, text="How to select", bg="#f2f2f2",
                 font=("TkDefaultFont", 11, "bold")).pack(anchor="w", **pad)
        tk.Label(
            sidebar, bg="#f2f2f2", justify="left", wraplength=SIDEBAR_WIDTH - 24,
            text="Click an object to select it.\n"
                 "Click another area to add it too.\n"
                 "Shift+Click an area to remove it.",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        tk.Frame(sidebar, height=1, bg="#cccccc").pack(fill="x", padx=12, pady=6)

        tk.Label(sidebar, text="Selected", bg="#f2f2f2",
                 font=("TkDefaultFont", 11, "bold")).pack(anchor="w", **pad)
        self.listbox = tk.Listbox(sidebar, height=3, exportselection=False,
                                  activestyle="dotbox")
        self.listbox.pack(fill="x", padx=12, pady=(2, 4))
        self.listbox.bind("<<ListboxSelect>>", self._on_item_select)

        self.draw_btn = tk.Button(sidebar, text=DRAW_LABEL,
                                  command=lambda: self._toggle_mode("draw"))
        self.draw_btn.pack(fill="x", padx=12, pady=(0, 4))
        # capture system-default button colours so we can restore them
        self._btn_bg = self.draw_btn.cget("background")
        self._btn_fg = self.draw_btn.cget("foreground")
        self._btn_abg = self.draw_btn.cget("activebackground")
        self._btn_afg = self.draw_btn.cget("activeforeground")

        self.cut_btn = tk.Button(sidebar, text=CUT_LABEL,
                                 command=lambda: self._toggle_mode("cut"))
        self.cut_btn.pack(fill="x", padx=12, pady=(0, 4))
        self.toggle_cut_btn = tk.Button(
            sidebar, text="Toggle removed area", state="disabled",
            command=self._toggle_removed_area)
        self.toggle_cut_btn.pack(fill="x", padx=12, pady=(0, 4))
        self.reset_btn = tk.Button(sidebar, text="Discard selection & try again",
                                   command=self._reset_selection)
        self.reset_btn.pack(fill="x", padx=12, pady=(0, 6))

        tk.Frame(sidebar, height=1, bg="#cccccc").pack(fill="x", padx=12, pady=6)

        tk.Label(sidebar, text="Add effects", bg="#f2f2f2",
                 font=("TkDefaultFont", 11, "bold")).pack(anchor="w", **pad)
        self.outline_btn = tk.Button(sidebar, text="Add outline", state="disabled",
                                     command=self._add_outline)
        self.outline_btn.pack(fill="x", padx=12, pady=(2, 6))

        tk.Frame(sidebar, bg="#f2f2f2").pack(fill="both", expand=True)

        self.status = tk.StringVar(value="Click an object to begin.")
        tk.Label(sidebar, textvariable=self.status, bg="#f2f2f2", fg="#444",
                 wraplength=SIDEBAR_WIDTH - 24, justify="left", anchor="w"
                 ).pack(fill="x", padx=12, pady=(0, 4))

        finish = tk.Button(
            sidebar, text="Finish edits and continue", command=self._finish,
            bg="#1f9d3d", fg="white", activebackground="#178233",
            activeforeground="white", font=("TkDefaultFont", 10, "bold"),
            relief="flat",
        )
        finish.pack(fill="x", padx=12, pady=12, ipady=6)

    def _make_undo_icon(self):
        """Draw a small curved 'undo' arrow with PIL (no font / SVG dependency)."""
        try:
            import math
            from PIL import ImageDraw
            size = 20
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            cx, cy, r = 10.0, 10.5, 6.0
            col = (45, 45, 45, 255)
            start, end, n = -45, 205, 28          # degrees, counter-clockwise
            pts = []
            for i in range(n + 1):
                a = math.radians(start + (end - start) * i / n)
                pts.append((cx + r * math.cos(a), cy - r * math.sin(a)))
            d.line(pts, fill=col, width=2, joint="curve")
            # arrowhead at the first point, pointing along the curve
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
        self.outline_btn.config(state="normal")
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
        self.undo_stack.clear()
        self.undo_btn.config(state="disabled")
        self._clear_cut()
        self.listbox.delete(0, "end")
        self.outline_btn.config(state="disabled")
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
        if not self.undo_stack:
            self.undo_btn.config(state="disabled")
        self._clear_cut()
        self._recompute_from_clicks()
        self.status.set("Undid the last step.")

    # -------------------------------------------------------- segmentation
    def _load_model(self):
        if self.model is None:
            self.status.set("Loading MobileSAM (first run downloads ~40 MB)...")
            self.config(cursor="watch")
            self.update_idletasks()
            from ultralytics import SAM
            self.model = SAM(MODEL_NAME)
            self.config(cursor="")
        return self.model

    def _segment_click(self, x, y, subtract):
        if subtract and self.object_mask is None:
            self.status.set("Select something first, then Shift+Click to remove.")
            return
        try:
            model = self._load_model()
        except Exception as exc:
            messagebox.showerror("Model error", f"Could not load model:\n{exc}")
            return

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
            self.outline_btn.config(state="normal")
        else:
            self.listbox.delete(0, "end")
            self.outline_btn.config(state="disabled")
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

        # Which connected blob(s) of the subject does the line actually cross?
        num0, lbl0 = cv2.connectedComponents(obj.astype(np.uint8), connectivity=8)
        line_dil = cv2.dilate(line, np.ones((3, 3), np.uint8), iterations=1) > 0
        touched = [int(l) for l in np.unique(lbl0[line_dil]) if l != 0]
        if not touched:
            self.status.set("Draw the line across the part you want to remove.")
            return
        target = np.isin(lbl0, touched)          # ONLY the blob(s) being cut

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

    # --------------------------------------------------------------- effects
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

    # ------------------------------------------------------------ save
    def _finish(self):
        out_name = f"sep-edit-{os.path.basename(self.image_path)}"
        os.makedirs(self.output_dir, exist_ok=True)
        out_path = os.path.join(self.output_dir, out_name)
        result = Image.fromarray(self.work)
        if self.alpha is not None:
            result = result.convert("RGBA")
            result.putalpha(self.alpha)
        try:
            result.save(out_path)
        except (KeyError, OSError):
            out_path = os.path.splitext(out_path)[0] + ".png"
            result.save(out_path)
        messagebox.showinfo("Saved", f"Saved to:\n{out_path}")
        self.destroy()


# --------------------------------------------------------------- entry points
def run_editor(image_path: str, output_dir: str = None) -> None:
    """output_dir=None -> ./temp/sep-edit-<name>; otherwise saves there."""
    if not os.path.isfile(image_path):
        raise FileNotFoundError(image_path)
    if output_dir is None:
        output_dir = os.path.join(os.getcwd(), "temp")
    ObjectSeparator(image_path, output_dir).mainloop()


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
