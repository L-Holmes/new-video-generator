"""
--- WHAT THIS CODE DOES ---

The user is shown the script lines one at a time.  Audio is loaded once and
played CONTINUOUSLY through the entire script — it is NOT stopped/restarted
between lines (per-line restarts are what caused drift on long videos).

When the user presses <Enter>, the *absolute* audio position (seconds in the
WAV) is recorded as the END of the current line.  The next line is shown
immediately, but the audio just keeps playing.

These absolute end-positions are saved to a cache file
(script_timestamps_seconds.json).

The actual per-line durations the rest of the pipeline cares about are
*derived* from those cached timestamps:

    duration_N  =  timestamp_N  -  timestamp_(N-1)        ( timestamp_(-1) := 0.0 )

…and written to OUTPUT_FILE.

Why this kills the drift:
  Every recorded position is referenced to a single, never-restarted play()
  call, so they all share the same (small, constant) pygame startup latency.
  When you subtract two timestamps to get a duration, that latency cancels.
  In the old design each line had its own play(start=X) with its own
  latency, so the errors accumulated line-by-line.

Backspace rewinds the audio so the previous line can be re-timed.  This does
start a fresh play() session, but only at the rewind point — drift can't
propagate forward from there.

# USAGE (e.g. in main.py):

    from AUDIO_SCRIPT_SYNCHRONIZER import run

    run(
        script_audio_file="path/to/script.wav",
        script_lines_file="path/to/scene_map_cache.json",
        output_file="path/to/script_timings_seconds.json",
        timestamps_cache_file="path/to/script_timestamps_seconds.json",
        audio_start_delay=0.5,
    )
"""

# =====================================================================================================================================
# default config (overridden when calling run() with params)
# =====================================================================================================================================

SCRIPT_AUDIO_FILE     = "script.wav"
SCRIPT_LINES_FILE     = "CACHE/scene_map_cache.json"
OUTPUT_FILE           = "CACHE/script_timings_seconds.json"
TIMESTAMPS_CACHE_FILE = "CACHE/script_timestamps_seconds.json"
AUDIO_START_DELAY     = 0.5   # seconds before audio begins (initial start / after rewind only)

# =====================================================================================================================================

import json
import os
import time
import threading
import tkinter as tk
from tkinter import font as tkfont
import pygame


# ─── File helpers ────────────────────────────────────────────────────────────

def load_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return list(json.load(f).keys())


def load_json(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json_ordered(path: str, data: dict, ordered_keys: list[str]) -> None:
    """Write only the keys in ordered_keys that exist in data, preserving order."""
    ordered = {k: data[k] for k in ordered_keys if k in data}
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)


def derive_durations(timestamps: dict, ordered_lines: list[str]) -> dict:
    """Convert absolute end-timestamps into per-line durations (string, 2dp)."""
    durations: dict = {}
    prev = 0.0
    for ln in ordered_lines:
        if ln not in timestamps:
            break  # stop at the first hole — durations are only valid up to here
        ts = float(timestamps[ln])
        durations[ln] = f"{round(ts - prev, 2):.2f}"
        prev = ts
    return durations


# ─────────────────────────────────────────────────────────────────────────────
class ScriptTimerApp:

    def __init__(self, root: tk.Tk, script_audio_file: str, script_lines_file: str,
                 output_file: str, timestamps_cache_file: str, audio_start_delay: float):
        self.root                  = root
        self.script_audio_file     = script_audio_file
        self.script_lines_file     = script_lines_file
        self.output_file           = output_file
        self.timestamps_cache_file = timestamps_cache_file
        self.audio_start_delay     = audio_start_delay

        self.root.title("Script Timer")
        self.root.configure(bg="#1a1a2e")
        self.root.resizable(True, True)
        self.root.geometry("960x500")

        # ── Data ──────────────────────────────────────────────────────────────
        self.lines      = load_lines(self.script_lines_file)
        self.n          = len(self.lines)
        self.timestamps = load_json(self.timestamps_cache_file)   # {line: "1.68"}

        # Resume position: continue at the first un-timestamped line
        # (walk consecutively from the start so a gap in the cache doesn't skip lines)
        self.index = 0
        while self.index < self.n and self.lines[self.index] in self.timestamps:
            self.index += 1
        if self.index >= self.n:
            self.index = 0   # everything done — shouldn't reach here (run() exits early)

        # ── Audio state ──────────────────────────────────────────────────────
        # We use ONE continuous play() call for the whole pass through the script
        # (re-started only at the very beginning and after Backspace rewinds).
        pygame.mixer.music.load(self.script_audio_file)
        self._audio_playing       = False
        self._play_session_offset = 0.0    # WAV pos where the current play() began
        self._play_token          = 0      # invalidates pending delayed-start threads
        self._splash_active       = False

        # ── Build UI then show splash ────────────────────────────────────────
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
            "  ▸  Audio plays CONTINUOUSLY through the whole script.\n"
            "  ▸  When you hear a line finish — press  Enter.\n"
            "  ▸  We record the absolute audio timestamp; the next line shows\n"
            "       instantly while the audio keeps playing without interruption.\n"
            "  ▸  Per-line durations are derived from those timestamps, so drift\n"
            "       can't accumulate across long scripts.\n"
            "  ▸  Backspace  rewinds the audio so you can redo the previous line.\n"
            "  ▸  Saved to:\n"
            f"       {self.output_file}\n"
            f"       {self.timestamps_cache_file}   (raw timestamps cache)"
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

        # Kick off the single continuous playback for this session.
        self._show_current_line(restart_audio=True)

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

    # ─── Audio control ───────────────────────────────────────────────────────

    def _current_audio_pos(self) -> float:
        """Absolute position in the WAV right now, in seconds."""
        if self._audio_playing:
            return self._play_session_offset + pygame.mixer.music.get_pos() / 1000.0
        return self._play_session_offset

    def _stop_audio(self):
        pygame.mixer.music.stop()
        self._audio_playing = False
        self._play_token   += 1   # cancels any pending delayed-start

    def _start_play_at(self, position: float, delay: float):
        """(Re)start playback at the given absolute WAV position, after `delay` s."""
        self._play_token += 1
        my_token = self._play_token

        def _run():
            time.sleep(delay)
            if my_token != self._play_token:
                return                                # superseded
            self._play_session_offset = position
            try:
                pygame.mixer.music.play(start=position)
            except pygame.error:
                # Fallback if start= unsupported for this build / format
                pygame.mixer.music.play()
                try:
                    pygame.mixer.music.set_pos(position)
                except pygame.error:
                    pass
            self._audio_playing = True
            self._set_status("🔊  Listening…  press  Enter  when the line ends")

        threading.Thread(target=_run, daemon=True).start()

    def _line_start_pos(self, idx: int) -> float:
        """Absolute WAV position where line `idx` begins (= end of previous line)."""
        if idx <= 0:
            return 0.0
        prev = self.lines[idx - 1]
        return float(self.timestamps.get(prev, 0.0))

    # ─── Persistence ─────────────────────────────────────────────────────────

    def _save_all(self):
        """Save raw timestamps cache AND derived durations."""
        save_json_ordered(self.timestamps_cache_file, self.timestamps, self.lines)
        durations = derive_durations(self.timestamps, self.lines)
        save_json_ordered(self.output_file, durations, self.lines)

    # ─── Navigation ──────────────────────────────────────────────────────────

    def _show_current_line(self, restart_audio: bool = False):
        if self.index >= self.n:
            self._finish()
            return

        line = self.lines[self.index]
        self.lbl_line.config(text=f'"{line}"', fg="#e0e0ff")
        self.lbl_progress.config(text=f"Line  {self.index + 1}  /  {self.n}")
        self._update_timing_preview()

        if restart_audio or not self._audio_playing:
            # First start, or after a rewind — kick off a fresh play() session.
            start_pos = self._line_start_pos(self.index)
            self._stop_audio()
            self._set_status("⏳  Audio starting…")
            self._start_play_at(start_pos, self.audio_start_delay)
        else:
            # Audio is already playing continuously — just update the display.
            self._set_status("🔊  Listening…  press  Enter  when the line ends")

    def _on_enter(self, _event=None):
        if self.index >= self.n:
            return

        if not self._audio_playing:
            # User pressed Enter during the initial start delay — just ignore.
            self._set_status("⚠️  Wait for the audio to start…")
            return

        timestamp = self._current_audio_pos()
        line      = self.lines[self.index]
        start_ts  = self._line_start_pos(self.index)
        duration  = round(timestamp - start_ts, 2)

        if duration < 0.05:
            self._set_status("⚠️  Too fast — wait for the audio, then press Enter")
            return

        # Store the *absolute* end-timestamp; the duration written to OUTPUT_FILE
        # is derived from this and the previous line's timestamp by _save_all().
        self.timestamps[line] = f"{timestamp:.2f}"
        self._save_all()

        self.index += 1

        self._set_status(f"✅  Logged  {duration:.2f}s   (audio @ {timestamp:.2f}s)")
        self._update_timing_preview()

        # Critical: do NOT restart the audio here — it just keeps playing.
        self._show_current_line(restart_audio=False)

    def _on_back(self, _event=None):
        """Rewind to redo the previous line (or current line if at the start)."""
        self._stop_audio()

        if self.index > 0:
            going_back_to = self.lines[self.index - 1]
            self.timestamps.pop(going_back_to, None)
            self.index -= 1
        else:
            # already at line 0 — clear its timestamp if we have one
            self.timestamps.pop(self.lines[0], None)

        self._save_all()
        self._set_status("⏪  Rewinding…")
        self._show_current_line(restart_audio=True)

    # ─── UI helpers ──────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self.lbl_status.config(text=msg)
        self.root.update_idletasks()

    def _update_timing_preview(self):
        durations = derive_durations(self.timestamps, self.lines)
        if not durations:
            self.lbl_timing.config(text="")
            return
        recent = list(durations.items())[-4:]
        rows   = [f"{v}s  ←  {k[:60]}{'…' if len(k) > 60 else ''}" for k, v in recent]
        self.lbl_timing.config(text="\n".join(rows))

    # ─── Finish / close ──────────────────────────────────────────────────────

    def _finish(self):
        """All lines done — stop audio, save, close window."""
        self._stop_audio()
        self._save_all()
        try:
            pygame.mixer.quit()
        except pygame.error:
            pass
        self.root.after(300, self.root.destroy)

    def _on_close(self):
        self._stop_audio()
        self._save_all()
        try:
            pygame.mixer.quit()
        except pygame.error:
            pass
        self.root.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def run(
    script_audio_file:     str   = SCRIPT_AUDIO_FILE,
    script_lines_file:     str   = SCRIPT_LINES_FILE,
    output_file:           str   = OUTPUT_FILE,
    timestamps_cache_file: str   = TIMESTAMPS_CACHE_FILE,
    audio_start_delay:     float = AUDIO_START_DELAY,
) -> None:
    """
    Launch the Script Timer GUI and block until the window is closed.

    Parameters
    ----------
    script_audio_file     : path to the WAV file containing the full script read-out.
    script_lines_file     : path to the ordered JSON whose keys are the script lines.
    output_file           : path where per-line DURATIONS (seconds) will be written.
    timestamps_cache_file : path where absolute end-TIMESTAMPS (seconds) are cached.
    audio_start_delay     : seconds to wait before audio begins (initial start / after rewind).
    """

    # --- already done? ---
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        print(f"Found existing data in '{output_file}'. Task complete. Returning.")
        return

    # --- timestamps already fully captured but durations not yet derived? ---
    # (e.g. user closed the window after the last Enter without us getting to save —
    #  shouldn't normally happen since we save on every Enter, but be safe)
    if os.path.exists(timestamps_cache_file):
        ts    = load_json(timestamps_cache_file)
        lines = load_lines(script_lines_file)
        if lines and all(ln in ts for ln in lines):
            durations = derive_durations(ts, lines)
            save_json_ordered(output_file, durations, lines)
            print(f"All timestamps cached. Derived durations written to '{output_file}'.")
            return

    pygame.mixer.init()
    root = tk.Tk()
    ScriptTimerApp(
        root,
        script_audio_file=script_audio_file,
        script_lines_file=script_lines_file,
        output_file=output_file,
        timestamps_cache_file=timestamps_cache_file,
        audio_start_delay=audio_start_delay,
    )
    root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run()
