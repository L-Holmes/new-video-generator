"""
Python generate a media review that shows user the media (either image or short mp4).

User clicks 'enter' to accept .

User clicks 'n' to say no. 



output a json of the ordererd file names, and then a represntation of true or false to represent whether the clip was accepted or not. 



Here is code outline (use variables provided):


# USAGE (e.g. in main.py):
```
from STOCK_FOOTAGE_REVIEW import run_media_review

run_media_review(
    script_text_to_media_url_and_runtime=my_clips,
    stock_footage_map_path="CACHE/stock_footage/history.json",
    output_file="review_output.json",
)
"""

# =====================================================================================================================================
# =====================================================================================================================================

script_text_to_media_url_and_runtime = [
    {
        "script_text": "The empire state building is really big.",
        "footage": ["https://images.pexels.com/photos/15299631/pexels-photo-15299631.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 1.68
    },
    {
        "script_text": "Built in Manhattan in the 19th century.",
        "footage": ["https://images.pexels.com/photos/5854264/pexels-photo-5854264.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 1.92
    },
    {
        "script_text": "Back in 1946,",
        "footage": ["https://images.pexels.com/photos/17852301/pexels-photo-17852301.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 0.80
    },
    {
        "script_text": "the technician John Ford the second",
        "footage": ["https://images.pexels.com/photos/33373485/pexels-photo-33373485.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 1.84
    },
    {
        "script_text": "created a new OpenAI carburettor for",
        "footage": ["https://images.pexels.com/photos/5847351/pexels-photo-5847351.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 1.76
    },
    {
        "script_text": "the lift in the skyscraper",
        "footage": ["https://images.pexels.com/photos/8748527/pexels-photo-8748527.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 1.36
    },
    {
        "script_text": "where they drunk chanoyu tea,",
        "footage": ["https://images.pexels.com/photos/6963695/pexels-photo-6963695.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 1.44
    },
    {
        "script_text": "which would go on to revolutionize the entire world.",
        "footage": ["https://images.pexels.com/photos/3683053/pexels-photo-3683053.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 2.40
    },
    {
        "script_text": "But where exactly in the world did this tea originate?",
        "footage": ["https://images.pexels.com/photos/5219982/pexels-photo-5219982.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 2.72
    },
    {
        "script_text": "It was in the newly formed state of Okinawa.",
        "footage": ["https://images.pexels.com/photos/31249245/pexels-photo-31249245.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 2.16
    },
    {
        "script_text": "Back in the 1700s, the samurai of Japan ruled over the kingdom.",
        "footage": ["https://images.pexels.com/photos/35038118/pexels-photo-35038118.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 3.04
    },
    {
        "script_text": "They discovered Koshuta — a type of rare plant",
        "footage": ["https://images.pexels.com/photos/34566446/pexels-photo-34566446.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 2.32
    },
    {
        "script_text": "which only grows in the foothills of the Japanese Alps...",
        "footage": ["https://images.pexels.com/photos/15299631/pexels-photo-15299631.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"],
        "runtime_seconds": 2.56
    }
]
"""
ORDERED LIST of the script text and the associated footage**
e.g. 

list[dict]  e.g.
    [
        {"script_text": "The Empire State Building is really big.",
         "footage":      ["https://images.pexels.com/photos/36042878/...jpeg"],
         "runtime_seconds": 4.0},
        {"script_text": "Back in 1946,",
         "footage":      ["https://images.pexels.com/photos/11223344/...jpeg"],
         "runtime_seconds": 2.0},
    ]
"""

STOCK_FOOTAGE_TO_DOWNLOADED_MEDIA_FILE= "CACHE/stock_footage/history.json"
"""
Maps the stock footage url to the actual downloaded media file name
e.g. 
{
  "https://images.pexels.com/photos/15299631/pexels-photo-15299631.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-15299631.jpeg.jpg",
  "https://images.pexels.com/photos/5854264/pexels-photo-5854264.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-5854264.jpeg.jpg",
  "https://images.pexels.com/photos/17852301/pexels-photo-17852301.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-17852301.jpeg.jpg",
  "https://images.pexels.com/photos/33373485/pexels-photo-33373485.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-33373485.jpeg.jpg",
  "https://images.pexels.com/photos/5847351/pexels-photo-5847351.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-5847351.jpeg.jpg",
  "https://images.pexels.com/photos/8748527/pexels-photo-8748527.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-8748527.jpeg.jpg",
  "https://images.pexels.com/photos/6963695/pexels-photo-6963695.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-6963695.jpeg.jpg",
  "https://images.pexels.com/photos/3683053/pexels-photo-3683053.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-3683053.jpeg.jpg",
  "https://images.pexels.com/photos/5219982/pexels-photo-5219982.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-5219982.jpeg.jpg",
  "https://images.pexels.com/photos/31249245/pexels-photo-31249245.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-31249245.jpeg.jpg",
  "https://images.pexels.com/photos/35038118/pexels-photo-35038118.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-35038118.jpeg.jpg",
  "https://images.pexels.com/photos/34566446/pexels-photo-34566446.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940": "CACHE/stock_footage/pexels-photo-34566446.jpeg.jpg"
}



[**]Each item of footage in the script_text_to_media_url_and_runtime 
    e.g. https://images.pexels.com/photos/36042878/...jpeg
    Is a key in this map
"""


OUTPUT_FILE="CACHE/stock_footage/review_accepting_footage.json"
"""
e.g. {
  "The empire state building is really big.": true,
  "Built in Manhattan in the 19th century.": true,
  "Back in 1946,": true,
  "the technician John Ford the second": false,
  "created a new OpenAI carburettor for": true,
  "the lift in the skyscraper": true,
  "where they drunk chanoyu tea,": true,
  "which would go on to revolutionize the entire world.": true,
  "But where exactly in the world did this tea originate?": false,
  "It was in the newly formed state of Okinawa.": true,
  "Back in the 1700s, the samurai of Japan ruled over the kingdom.": true,
  "They discovered Koshuta — a type of rare plant": true,
  "which only grows in the foothills of the Japanese Alps...": true
}
"""

# =====================================================================================================================================
# =====================================================================================================================================


"""
Media Review Tool
-----------------
Displays each piece of footage (image or MP4) alongside its script text.
  • ENTER / Y / J  → accept  (True)
  • SPACE / N / F  → reject  (False)

Writes results to OUTPUT_FILE as an ordered JSON dict keyed by script_text.

Entry point for external callers:
    from media_review import run_media_review
    run_media_review(script_text_to_media_url_and_runtime, stock_footage_map_path, output_file)
"""

import json
import os
import threading
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageTk


# ── DISPLAY CONSTANTS ─────────────────────────────────────────────────────────

MAX_W, MAX_H = 900, 560
BG       = "#1a1a2e"
PANEL_BG = "#16213e"
ACCENT   = "#e94560"
TEXT_COL = "#eaeaea"
HINT_COL = "#888"
BTN_ACCEPT = "#2ecc71"
BTN_REJECT = "#e74c3c"
FONT_MONO  = ("Courier New", 13)
FONT_UI    = ("Segoe UI", 11)


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _load_stock_map(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"[warn] stock footage map not found at {path!r} – proceeding with empty map.")
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _resolve_media_files(footage: list[dict], stock_map: dict) -> list[str]:
    files = []
    for item in footage:
        url = next(iter(item))          # the key in the dict is the URL
        local = stock_map.get(url)
        if local and Path(local).exists():
            files.append(local)
        else:
            print(f"[warn] no local file for URL: {url}")
    return files


def _is_video(path: str) -> bool:
    return Path(path).suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm"}


# ── VIDEO PLAYER ──────────────────────────────────────────────────────────────

def _play_video_in_label(label: tk.Label, path: str, stop_event: threading.Event):
    try:
        import cv2
    except ImportError:
        label.config(text="[opencv-python not installed – cannot preview video]",
                     fg=HINT_COL, image="")
        return

    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    delay = int(1000 / fps)

    def _next_frame():
        if stop_event.is_set():
            cap.release()
            return
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            label.after(delay, _next_frame)
            return
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(frame)
        img.thumbnail((MAX_W, MAX_H), Image.LANCZOS)
        tk_img = ImageTk.PhotoImage(img)
        label.config(image=tk_img, text="")
        label._img_ref = tk_img
        label.after(delay, _next_frame)

    _next_frame()


# ── MAIN REVIEW GUI ───────────────────────────────────────────────────────────

class _MediaReviewer:
    def __init__(self, items: list[dict], stock_map: dict, output_file: str):
        self.items = items
        self.stock_map = stock_map
        self.output_file = output_file
        self.results: dict[str, bool] = {}
        self.current = 0
        self._video_stop = threading.Event()

        self.root = tk.Tk()
        self.root.title("Media Review")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self._build_ui()
        self._load_item(self.current)

        for key in ("<Return>", "y", "Y", "j", "J"):
            self.root.bind(key, lambda _: self._decide(True))
        for key in ("<space>", "n", "N", "f", "F"):
            self.root.bind(key, lambda _: self._decide(False))

        self.root.mainloop()

    def _build_ui(self):
        root = self.root

        top = tk.Frame(root, bg=PANEL_BG, pady=6)
        top.pack(fill="x")
        self.progress_var = tk.StringVar()
        tk.Label(top, textvariable=self.progress_var,
                 bg=PANEL_BG, fg=HINT_COL, font=FONT_UI).pack(side="left", padx=12)
        tk.Label(top, text="MEDIA REVIEW", bg=PANEL_BG, fg=ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(side="left", expand=True)

        script_frame = tk.Frame(root, bg=PANEL_BG, padx=18, pady=10)
        script_frame.pack(fill="x", padx=20, pady=(8, 4))
        tk.Label(script_frame, text="SCRIPT", bg=PANEL_BG, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.script_var = tk.StringVar()
        tk.Label(script_frame, textvariable=self.script_var,
                 bg=PANEL_BG, fg=TEXT_COL, font=FONT_MONO,
                 wraplength=860, justify="left").pack(anchor="w", pady=(4, 0))
        self.runtime_var = tk.StringVar()
        tk.Label(script_frame, textvariable=self.runtime_var,
                 bg=PANEL_BG, fg=HINT_COL, font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))

        self.media_label = tk.Label(root, bg=BG, cursor="arrow")
        self.media_label.pack(pady=(6, 4))

        btn_row = tk.Frame(root, bg=BG, pady=8)
        btn_row.pack()
        tk.Button(btn_row, text="✔  ACCEPT  [Enter / Y / J]",
                  bg=BTN_ACCEPT, fg="white", activebackground="#27ae60",
                  font=("Segoe UI", 12, "bold"), relief="flat", padx=20, pady=8,
                  command=lambda: self._decide(True)).pack(side="left", padx=16)
        tk.Button(btn_row, text="✘  REJECT  [Space / N / F]",
                  bg=BTN_REJECT, fg="white", activebackground="#c0392b",
                  font=("Segoe UI", 12, "bold"), relief="flat", padx=20, pady=8,
                  command=lambda: self._decide(False)).pack(side="left", padx=16)

        self.status_var = tk.StringVar(value="Enter/Y/J = accept  |  Space/N/F = reject")
        tk.Label(root, textvariable=self.status_var,
                 bg=BG, fg=HINT_COL, font=("Segoe UI", 9)).pack(pady=(0, 8))

    def _load_item(self, idx: int):
        self._video_stop.set()
        self._video_stop = threading.Event()

        item = self.items[idx]
        self.progress_var.set(f"Clip {idx + 1} / {len(self.items)}")
        self.script_var.set(f'"{item["script_text"]}"')
        self.runtime_var.set(f"Runtime: {item.get('runtime_seconds', '?')}s")
        self.status_var.set("Enter/Y/J = accept  |  Space/N/F = reject")

        media_files = _resolve_media_files(item.get("footage", []), self.stock_map)

        if not media_files:
            self.media_label.config(image="", text="⚠  No media file found for this clip",
                                    fg=ACCENT, font=FONT_UI, width=70, height=8, bg=PANEL_BG)
            return

        path = media_files[0]
        if _is_video(path):
            self.media_label.config(image="", text="Loading video…", fg=TEXT_COL, bg=BG)
            threading.Thread(target=_play_video_in_label,
                             args=(self.media_label, path, self._video_stop),
                             daemon=True).start()
        else:
            self._show_image(path)

    def _show_image(self, path: str):
        try:
            img = Image.open(path)
            img.thumbnail((MAX_W, MAX_H), Image.LANCZOS)
            tk_img = ImageTk.PhotoImage(img)
            self.media_label.config(image=tk_img, text="", bg=BG)
            self.media_label._img_ref = tk_img
        except Exception as exc:
            self.media_label.config(image="", text=f"⚠  Could not load image:\n{exc}",
                                    fg=ACCENT, font=FONT_UI, bg=PANEL_BG, width=60, height=6)

    def _decide(self, accepted: bool):
        self.results[self.items[self.current]["script_text"]] = accepted
        self.current += 1
        if self.current < len(self.items):
            self._load_item(self.current)
        else:
            self._finish()

    def _finish(self):
        self._video_stop.set()
        self._write_output()
        self.root.destroy()

    def _write_output(self):
        out = Path(self.output_file)
        if out.parent != Path("."):
            os.makedirs(out.parent, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)
        print(f"\n[✔] Output written to {self.output_file!r}")
        print(json.dumps(self.results, indent=2))


# ── PUBLIC ENTRY POINT ────────────────────────────────────────────────────────

def run_media_review(
    script_text_to_media_url_and_runtime: list[dict],
    stock_footage_map_path: str,
    output_file: str,
) -> None:
    """
    Launch the media review GUI and block until the user finishes.

    Parameters
    ----------
    script_text_to_media_url_and_runtime : list[dict]
        Ordered list of clips, each with keys:
          - "script_text"      : str
          - "footage"          : list[str]  (stock footage URLs)
          - "runtime_seconds"  : float
    stock_footage_map_path : str
        Path to the JSON file mapping footage URLs → local file paths.
        e.g. "CACHE/stock_footage/history.json"
    output_file : str
        Path where the review JSON result will be written.
        e.g. "review_output.json"
    """

    # --- NEW CHECK ---
    # Check if the output file already exists and has content
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        print(f"Review already completed. Found existing data in '{output_file}'.")
        return 
    # -----------------

    if not script_text_to_media_url_and_runtime:
        print("[error] script_text_to_media_url_and_runtime is empty – nothing to review.")
        return

    stock_map = _load_stock_map(stock_footage_map_path)
    print(f"[media_review] Starting review of {len(script_text_to_media_url_and_runtime)} clips…")
    _MediaReviewer(script_text_to_media_url_and_runtime, stock_map, output_file)


# ── STANDALONE USE ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Fill these in when running directly
    _items = [
        # {
        #     "script_text": "The Empire State Building is really big.",
        #     "footage":     ["https://images.pexels.com/photos/36042878/...jpeg"],
        #     "runtime_seconds": 4.0,
        # },
    ]

    run_media_review(
        script_text_to_media_url_and_runtime=script_text_to_media_url_and_runtime,
        stock_footage_map_path=STOCK_FOOTAGE_TO_DOWNLOADED_MEDIA_FILE,
        output_file=OUTPUT_FILE,
    )
