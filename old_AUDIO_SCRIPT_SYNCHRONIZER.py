"""
--- WHAT THIS CODE DOES ---

- User is presented with a gui:
    - The first line on screen from the json
- After a short while (e.g. 0.5 seconds), the audio of the script being read out starts to play.

- When user clicks <enter> that triggers:
    - The played audio stops
    - The timestamp of the audio is logged
    - The code does: length_of_clip = ROUND(this_timestamp - previous_timestamp, 2)

- Code logs the calculated length of clip as the 'value' in the json- next to the line shown on screen. 

- Then, the next script line is shown, with a little delay of like maybe half a second, and then the audio continues to play again, and the loop continues.


Also:
- maybe have a way to skip back to the previous line?
    - so it will load the previous timestamp, rewind the audio to that timestamp
    - and then go back to the previous line as well.

# USAGE (e.g. in main.py):
from AUDIO_SCRIPT_SYNCHRONIZER import run

run(
    script_audio_file="path/to/script.wav",
    script_lines_file="path/to/scene_map_cache.json",
    output_file="path/to/script_timings_seconds.json",
    audio_start_delay=0.5,
)
"""

"""
Basically:
  where it shows the line by line of hte script
  then the audio plays whilst the user sees the text. and when it gets to the end of the line, the user presses enter.
  (so then we generate a list of timestamps, and work out how long each clip should be)
"""

# =====================================================================================================================================
# =====================================================================================================================================

# default config: 
# (these will be overridden when calling the run function with params)

SCRIPT_AUDIO_FILE="script.wav"

SCRIPT_LINES_FILE="CACHE/scene_map_cache.json"
"""
e.g. SCRIPT_LINES_FILE content:
{
  "The empire state building is really big.": "empire state building",
  "Built in Manhattan in the 19th century.": "Manhattan the 19th century",
  "Back in 1946,": "1946",
  "the technician John Ford the second": "John Ford second",
  "created a new OpenAI carburettor for": "carburettor empire states building",
  "the lift in the skyscraper": "lift empire states building",
  "where they drunk chanoyu tea,": "chanoyu tea",
  "which would go on to revolutionize the entire world.": "world revolutionize",
  "But where exactly in the world did this tea originate?": "earth question mark",
  "It was in the newly formed state of Okinawa.": "Okinawa ",
  "Back in the 1700s, the samurai of Japan ruled over the kingdom.": "1700s Japan samurai kingdom",
  "They discovered Koshuta \u2014 a type of rare plant": "Koshuta type plant",
  "which only grows in the foothills of the Japanese Alps...": "Japanese Alps foothills"
}


To note:
    - the above is an ordered json.
    - so top to bottom represents the order in which they should be read.
    - for the purpose of this code, just ignore the values of each entry in the json. We only care about the keys (these are the input lines)
"""

OUTPUT_FILE="CACHE/script_timings_seconds.json"
"""
example output:
{
  "The empire state building is really big.": "1.68",
  "Built in Manhattan in the 19th century.": "1.92",
  "Back in 1946,": "0.80",
  "the technician John Ford the second": "1.84",
  "created a new OpenAI carburettor for": "1.76",
  "the lift in the skyscraper": "1.36",
  "where they drunk chanoyu tea,": "1.44",
  "which would go on to revolutionize the entire world.": "2.40",
  "But where exactly in the world did this tea originate?": "2.72",
  "It was in the newly formed state of Okinawa.": "2.16",
  "Back in the 1700s, the samurai of Japan ruled over the kingdom.": "3.04",
  "They discovered Koshuta — a type of rare plant": "2.32",
  "which only grows in the foothills of the Japanese Alps...": "2.56"
}
"""

AUDIO_START_DELAY = 0.5   # seconds before audio begins after showing a line

# =====================================================================================================================================
# =====================================================================================================================================

import json
import os
import time
import threading
import tkinter as tk
from tkinter import font as tkfont
import pygame

def load_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return list(data.keys())


def load_existing_output(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_output(path: str, timings: dict, ordered_lines: list[str]) -> None:
    """Write timings in the same order as the original script lines."""
    ordered = {ln: timings[ln] for ln in ordered_lines if ln in timings}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
class ScriptTimerApp:

    def __init__(self, root: tk.Tk, script_audio_file: str, script_lines_file: str,
                 output_file: str, audio_start_delay: float):
        self.root              = root
        self.script_audio_file = script_audio_file
        self.script_lines_file = script_lines_file
        self.output_file       = output_file
        self.audio_start_delay = audio_start_delay

        self.root.title("Script Timer")
        self.root.configure(bg="#1a1a2e")
        self.root.resizable(True, True)
        self.root.geometry("960x500")

        # ── Data ──────────────────────────────────────────────────────────────
        self.lines   = load_lines(self.script_lines_file)
        self.timings = load_existing_output(self.output_file)
        self.n       = len(self.lines)
        self.index   = 0
        self.timestamps: list[float] = [0.0]

        already_done = sum(1 for ln in self.lines if ln in self.timings)
        self.index = min(already_done, self.n - 1) if already_done < self.n else 0

        # ── Audio ─────────────────────────────────────────────────────────────
        pygame.mixer.music.load(self.script_audio_file)
        self._audio_playing = False
        self._delay_thread: threading.Thread | None = None
        self._paused_pos    = 0.0

        self._rebuild_timestamps_from_timings()

        # ── Build UI then show splash ─────────────────────────────────────────
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._show_splash()

    # ─── Splash screen ───────────────────────────────────────────────────────

    def _show_splash(self):
        self._splash_active = True

        for w in (self.lbl_progress, self.lbl_line, self.lbl_status,
                  self.lbl_timing, self.lbl_hint):
            w.pack_forget()

        resuming    = self.index > 0
        resume_note = (
            f"\n  ▸  Resuming from line {self.index + 1} / {self.n}  "
            f"({self.index} line{'s' if self.index != 1 else ''} already saved)"
            if resuming else ""
        )

        instructions = (
            "  HOW THIS WORKS\n\n"
            "  ▸  Each script line is shown one at a time.\n"
            "  ▸  The audio plays automatically after a short pause.\n"
            "  ▸  Listen — when you hear the line finish, press  Enter.\n"
            "  ▸  This stamps the audio, logs the clip duration, and moves on.\n"
            "  ▸  Backspace  at any time to redo the previous line\n"
            "       (audio rewinds to that line's start position).\n"
            "  ▸  Results saved to:\n"
            f"       {self.output_file}"
            f"{resume_note}"
        )

        self.lbl_splash = tk.Label(
            self.root, text=instructions,
            bg="#1a1a2e", fg="#c0c0e0",
            font=tkfont.Font(family="Courier", size=13),
            justify="left", anchor="w"
        )
        self.lbl_splash.pack(expand=True, padx=50, pady=30, anchor="w")

        self.lbl_splash_hint = tk.Label(
            self.root,
            text="Press  Enter  to begin",
            bg="#1a1a2e", fg="#4a90d9",
            font=tkfont.Font(family="Helvetica", size=15, weight="bold")
        )
        self.lbl_splash_hint.pack(pady=(0, 30))

        self.root.bind("<Return>",    self._dismiss_splash)
        self.root.bind("<BackSpace>", lambda e: None)

    def _dismiss_splash(self, _event=None):
        self._splash_active = False
        self.lbl_splash.destroy()
        self.lbl_splash_hint.destroy()

        self.lbl_progress.pack(padx=30, pady=(20, 4))
        self.lbl_line.pack(expand=True, padx=30, pady=16)
        self.lbl_status.pack(padx=30, pady=4)
        self.lbl_timing.pack(padx=30, pady=8)
        self.lbl_hint.pack(pady=(0, 20))

        self.root.bind("<Return>",    self._on_enter)
        self.root.bind("<BackSpace>", self._on_back)

        self._show_current_line()

    # ─── GUI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        """Create widgets but do NOT pack them — splash screen is shown first."""
        self.lbl_progress = tk.Label(
            self.root, text="", bg="#1a1a2e", fg="#7f8c8d",
            font=tkfont.Font(family="Helvetica", size=13)
        )
        self.lbl_line = tk.Label(
            self.root, text="", bg="#1a1a2e", fg="#e0e0ff",
            font=tkfont.Font(family="Helvetica", size=28, weight="bold"),
            wraplength=900, justify="center"
        )
        self.lbl_status = tk.Label(
            self.root, text="", bg="#1a1a2e", fg="#95a5a6",
            font=tkfont.Font(family="Helvetica", size=13)
        )
        self.lbl_timing = tk.Label(
            self.root, text="", bg="#1a1a2e", fg="#27ae60",
            font=tkfont.Font(family="Courier", size=11)
        )
        self.lbl_hint = tk.Label(
            self.root,
            text="[ Enter ] → mark end of line        [ Backspace ] → redo previous line",
            bg="#1a1a2e", fg="#4a4a6a",
            font=tkfont.Font(family="Helvetica", size=11)
        )

    # ─── Timestamp helpers ───────────────────────────────────────────────────

    def _rebuild_timestamps_from_timings(self):
        """Reconstruct cumulative audio positions from previously saved timings."""
        self.timestamps = [0.0]
        for ln in self.lines[: self.index]:
            dur = float(self.timings.get(ln, 0.0))
            self.timestamps.append(self.timestamps[-1] + dur)

    def _current_audio_pos(self) -> float:
        if self._audio_playing:
            return self._paused_pos + pygame.mixer.music.get_pos() / 1000.0
        return self._paused_pos

    # ─── Audio control ───────────────────────────────────────────────────────

    def _start_audio_delayed(self):
        def _run():
            time.sleep(self.audio_start_delay)
            if not self._audio_playing:
                pygame.mixer.music.play(start=self._paused_pos)
                self._audio_playing = True
                self._set_status("🔊  Listening…  press  Enter  when the line ends")
        self._delay_thread = threading.Thread(target=_run, daemon=True)
        self._delay_thread.start()

    def _pause_audio(self) -> float:
        pos = self._current_audio_pos()
        pygame.mixer.music.stop()
        self._audio_playing = False
        self._paused_pos = pos
        return pos

    # ─── Navigation ──────────────────────────────────────────────────────────

    def _show_current_line(self):
        if self.index >= self.n:
            self._finish()
            return

        line = self.lines[self.index]
        self.lbl_line.config(text=f'"{line}"', fg="#e0e0ff")
        self.lbl_progress.config(text=f"Line  {self.index + 1}  /  {self.n}")
        self._set_status("⏳  Audio starting…")
        self._update_timing_preview()

        self._paused_pos    = self.timestamps[self.index] if self.index < len(self.timestamps) else 0.0
        self._audio_playing = False

        self._start_audio_delayed()

    def _on_enter(self, _event=None):
        if self.index >= self.n:
            return

        timestamp = self._pause_audio()
        line      = self.lines[self.index]

        start_ts = self.timestamps[self.index] if self.index < len(self.timestamps) else 0.0
        duration = round(timestamp - start_ts, 2)

        if duration < 0.05:
            self._set_status("⚠️  Too fast — wait for the audio, then press Enter")
            self._start_audio_delayed()
            return

        self.timings[line] = f"{duration:.2f}"
        save_output(self.output_file, self.timings, self.lines)

        self.index += 1
        if self.index >= len(self.timestamps):
            self.timestamps.append(timestamp)
        else:
            self.timestamps[self.index] = timestamp

        self._set_status(f"✅  Logged  {duration:.2f}s")
        self._update_timing_preview()

        self.root.after(400, self._show_current_line)

    def _on_back(self, _event=None):
        """Rewind to the start of the current or previous line."""
        self._pause_audio()

        if self.index == 0:
            self.timings.pop(self.lines[0], None)
            save_output(self.output_file, self.timings, self.lines)
            self.timestamps     = [0.0]
            self._paused_pos    = 0.0
            self._audio_playing = False
            self._set_status("⏪  Rewound to start of first line…")
            self.root.after(200, self._show_current_line)
            return

        going_back_to = self.lines[self.index - 1]
        self.timings.pop(going_back_to, None)
        save_output(self.output_file, self.timings, self.lines)

        self.index         -= 1
        self._paused_pos    = self.timestamps[self.index] if self.index < len(self.timestamps) else 0.0
        self._audio_playing = False

        self._set_status("⏪  Going back one line…")
        self.root.after(200, self._show_current_line)

    # ─── UI helpers ──────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self.lbl_status.config(text=msg)
        self.root.update_idletasks()

    def _update_timing_preview(self):
        done = [(ln, self.timings[ln]) for ln in self.lines if ln in self.timings]
        if not done:
            self.lbl_timing.config(text="")
            return
        recent = done[-4:]
        rows   = [f"{v}s  ←  {k[:60]}{'…' if len(k) > 60 else ''}" for k, v in recent]
        self.lbl_timing.config(text="\n".join(rows))

    # ─── Finish / close ──────────────────────────────────────────────────────

    def _finish(self):
        """All lines done — stop audio and close the window."""
        pygame.mixer.music.stop()
        pygame.mixer.quit()
        self.root.after(300, self.root.destroy)

    def _on_close(self):
        pygame.mixer.music.stop()
        pygame.mixer.quit()
        self.root.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def run(
    script_audio_file: str = SCRIPT_AUDIO_FILE,
    script_lines_file: str = SCRIPT_LINES_FILE,
    output_file:       str = OUTPUT_FILE,
    audio_start_delay: float = AUDIO_START_DELAY,
) -> None:
    """
    Launch the Script Timer GUI and block until the window is closed.

    Parameters
    ----------
    script_audio_file : path to the WAV file containing the full script read-out.
    script_lines_file : path to the ordered JSON whose keys are the script lines.
    output_file       : path where per-line durations (seconds) will be written.
    audio_start_delay : seconds to wait before audio begins after each new line.
    """

    # --- NEW CHECK ---
    # Check if file exists and size is greater than 0 bytes
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        print(f"Found existing data in '{output_file}'. Task complete. Returning.")
        return 
    # -----------------

    pygame.mixer.init()
    root = tk.Tk()
    ScriptTimerApp(
        root,
        script_audio_file=script_audio_file,
        script_lines_file=script_lines_file,
        output_file=output_file,
        audio_start_delay=audio_start_delay,
    )
    root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run()
