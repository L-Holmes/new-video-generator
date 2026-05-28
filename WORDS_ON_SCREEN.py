"""
WORD_BY_WORD_VIDEO.py
=====================
Renders a YouTube-ready MP4 where each word of your script appears on
screen in sync with the recorded audio.  White text, black background,
two rows, max 8 words on screen at once.

Reads the timing JSONs already produced by AUDIO_SCRIPT_SYNCHRONIZER.py
(line start times + line durations) — does NOT re-run Whisper.  Within
each line, the line's duration is distributed across its words in
proportion to character count, which is close enough to natural speech
pacing for kinetic-typography purposes.

Pipeline
--------
  1. Read line-level start times and durations from the timing JSONs.
  2. Distribute each line's duration across its words.
  3. Pack words into "screens" of ≤ 8.  A new word tries row 0 first;
     if it doesn't fit horizontally, tries row 2; if neither fits or
     the screen is already full, start a new screen.
  4. Render one PNG per state change with Pillow (white text on black).
  5. Use ffmpeg's concat demuxer + audio mux to produce the final MP4.

Requirements
------------
    pip install Pillow
    ffmpeg + ffprobe on PATH

Run
---
    python WORD_BY_WORD_VIDEO.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ============================================================================
# CONFIG  (matches the layout the user uses with AUDIO_SCRIPT_SYNCHRONIZER)
# ============================================================================

_NAME = "spices"
_CACHE_DIR = "spices-CACHE"
_SCRIPT_STEM = f"script-{_NAME}" if _NAME else "script"

SCRIPT_AUDIO_FILE = f"{_SCRIPT_STEM}.wav"
LINE_INDEX_TO_SEARCH_TERM_FILE = (
    f"{_NAME}_script_to_search_term.json" if _NAME
    else "script_to_search_term.json"
)
SYNCHRONIZED_SCRIPT_OUTPUT_FILE = f"{_CACHE_DIR}/script_timings_seconds.json"
TIMESTAMPS_ABSOLUTE_FILE = (
    f"{_CACHE_DIR}/{_NAME}_timestamps_absolute.json" if _NAME
    else f"{_CACHE_DIR}/timestamps_absolute.json"
)

# NEW: optional per-word timings produced by AUDIO_SCRIPT_SYNCHRONIZER.
# When present, this file is the source of truth for word-level sync.
# When absent (or partial), we fall back to syllable-based estimation.
WORD_TIMINGS_FILE = (
    f"{_CACHE_DIR}/{_NAME}_word_timings.json" if _NAME
    else f"{_CACHE_DIR}/word_timings.json"
)

# AUDIO_START_DELAY_SECONDS is unused by the video generator — the line
# start times in TIMESTAMPS_ABSOLUTE_FILE already reflect any silence at
# the start of the recording.  Kept here for API parity.
AUDIO_START_DELAY_SECONDS = 0.5

OUTPUT_VIDEO = f"{_SCRIPT_STEM}_words.mp4"

# Video specs.  1920x1080 @ 30 fps is the YouTube horizontal HD standard.
# For YouTube Shorts swap to 1080x1920.
VIDEO_W = 1920
VIDEO_H = 1080
FPS = 30

# Text styling.
FONT_SIZE          = 96         # pixels
LINE_HEIGHT        = 140        # vertical spacing between row 0 and row 1
HORIZONTAL_MARGIN  = 100        # left/right safe-area inside the frame
MAX_WORDS_PER_SCREEN = 8
ROW_COUNT          = 2

# Show each word this many seconds BEFORE its computed start time, so
# words appear slightly ahead of the audio rather than behind.  Whisper's
# word boundaries are a few tens of ms imprecise even in precise mode;
# this gives a small head-start so they always feel anticipatory.
WORD_LEAD_SECONDS = 0.08

# Font discovery — first existing path wins.  DejaVu Sans Bold ships on
# almost every Linux box; Arial Bold is the macOS / Windows fallback.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]




# ============================================================================
# Small helpers
# ============================================================================

def _find_font() -> str:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise RuntimeError(
        "No font found on disk.  Edit FONT_CANDIDATES at the top of "
        "WORD_BY_WORD_VIDEO.py to point at a .ttf you have."
    )


def _check_tool(name: str) -> None:
    if not shutil.which(name):
        raise RuntimeError(f"{name!r} not found on PATH.  Install ffmpeg first.")


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def _split_words(line: str) -> list[str]:
    """Whitespace tokenise a script line, keeping punctuation attached."""
    return [w for w in re.split(r"\s+", line.strip()) if w]


def _syllables(word: str) -> int:
    """Crude English syllable count — vowel groups, drop silent terminal 'e'."""
    w = re.sub(r"[^a-zA-Z]", "", word.lower())
    if not w:
        return 1
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and n > 1:
        n -= 1
    return max(n, 1)


def _estimate_speech_rate(line_durations: dict, ordered_lines: list[str]) -> float:
    """
    Syllables per second, estimated from the FASTEST half of lines.

    A line's recorded duration is (real speech time) + (trailing silence
    to the next line).  Slower lines are inflated by silence; faster
    lines are closer to the speaker's true pace.  Median of the fastest
    half is a robust estimate.  Clamped to a plausible English narration
    range so a degenerate dataset can't produce nonsense.
    """
    ratios = []
    for line in ordered_lines:
        if line not in line_durations:
            continue
        syls = sum(_syllables(w) for w in _split_words(line))
        d = float(line_durations[line])
        if syls > 0 and d > 0:
            ratios.append(d / syls)
    if not ratios:
        return 4.5
    ratios.sort()
    fastest_half = ratios[: max(1, len(ratios) // 2)]
    median = fastest_half[len(fastest_half) // 2]
    return max(2.5, min(7.0, 1.0 / median))


# ============================================================================
# Word-level timing
# ============================================================================

def _build_word_timings(
    line_starts: dict,
    line_durations: dict,
    ordered_lines: list[str],
    rate: float,
    precise_word_timings: dict | None = None,
) -> list[dict]:
    """
    Produce per-word [{text, start, end, line_idx}] events.

    Two modes:

    1) PRECISE MODE (preferred).
       If `precise_word_timings` is provided and contains an entry for a
       line whose word count matches our whitespace-split count, we use
       the exact per-word start times that AUDIO_SCRIPT_SYNCHRONIZER
       derived from Whisper's word-level timestamps.  Each word's "end"
       is the next word's start (within the line) or the line's end.

    2) ESTIMATE MODE (fallback).
       For any line missing from the precise dict OR where the word count
       doesn't match (defensive — tokenisation discrepancy), distribute
       the line's duration across its words using syllable-based
       estimates at the auto-tuned `rate`, with any trailing slack left
       as silence.  Same logic as before.
    """
    out: list[dict] = []
    for li, line in enumerate(ordered_lines):
        if line not in line_starts or line not in line_durations:
            continue
        words = _split_words(line)
        if not words:
            continue

        # --- precise mode -------------------------------------------------
        precise_line = (precise_word_timings or {}).get(line)
        if precise_line is not None and len(precise_line) == len(words):
            line_start = float(line_starts[line])
            line_end   = line_start + float(line_durations[line])
            for j, (wd, pw) in enumerate(zip(words, precise_line)):
                start = float(pw["start"])
                if j + 1 < len(precise_line):
                    end = float(precise_line[j + 1]["start"])
                else:
                    end = line_end
                # Defensive: clamp into the line window in case of any
                # off-by-tiny upstream rounding.
                start = max(line_start, min(start, line_end))
                end   = max(start,      min(end,   line_end))
                out.append({"text": wd, "start": start, "end": end,
                            "line_idx": li})
            continue

        # --- estimate mode (unchanged fallback) ---------------------------
        start = float(line_starts[line])
        dur   = float(line_durations[line])
        per_word = [_syllables(w) / rate for w in words]
        total_est = sum(per_word)
        if total_est > dur:
            scale = dur / total_est
            per_word = [d * scale for d in per_word]
        cursor = start
        for wd, d in zip(words, per_word):
            out.append({"text": wd, "start": cursor,
                        "end": cursor + d, "line_idx": li})
            cursor += d

    # Apply a small global lead so words appear slightly ahead of audio
    # rather than behind.  Clamped so the first word never goes negative.
    if WORD_LEAD_SECONDS > 0:
        for ev in out:
            ev["start"] = max(0.0, ev["start"] - WORD_LEAD_SECONDS)
            ev["end"]   = max(ev["start"], ev["end"] - WORD_LEAD_SECONDS)
    return out


# ============================================================================
# Layout — measure words, pack into screens
# ============================================================================

def _measure_words(words: list[dict], font_path: str) -> int:
    from PIL import ImageFont
    font = ImageFont.truetype(font_path, FONT_SIZE)
    # Width of a single space character — used as the inter-word gap.
    space_w = font.getbbox(" ")[2] or int(FONT_SIZE * 0.3)
    for w in words:
        l, _, r, _ = font.getbbox(w["text"])
        w["width"] = r - l
    return space_w


def _pack_screens(words: list[dict], space_w: int) -> list[list[dict]]:
    """
    Greedy two-row packer with STRICT left-to-right, top-to-bottom order.

    Once a word goes to row 1, all subsequent words on this screen also
    go to row 1.  We never backtrack to row 0 — that would put words
    out of reading order on screen.  (Old bug: "if the sharks becommed
    the fish" rendered as "if the sharks the / becommed fish" because
    short later words slipped back into row 0's leftover space.)

    When row 1 is also full or out of horizontal space, the screen
    closes and we start a new one on row 0.
    """
    max_text_w = VIDEO_W - 2 * HORIZONTAL_MARGIN
    screens: list[list[dict]] = []
    current: list[dict] = []
    row_widths = [0, 0]
    current_row = 0

    def flush() -> None:
        nonlocal current, row_widths, current_row
        if current:
            screens.append(current)
        current = []
        row_widths = [0, 0]
        current_row = 0

    for w in words:
        def fits(row: int) -> bool:
            gap = space_w if row_widths[row] > 0 else 0
            return (row_widths[row] + gap + w["width"] <= max_text_w
                    and len(current) < MAX_WORDS_PER_SCREEN)

        if fits(current_row):
            fit_row = current_row
        elif current_row == 0 and fits(1):
            fit_row = 1
            current_row = 1
        else:
            flush()
            fit_row = 0

        gap = space_w if row_widths[fit_row] > 0 else 0
        placed = dict(w)
        placed["row"] = fit_row
        placed["x"]   = row_widths[fit_row] + gap
        row_widths[fit_row] += gap + w["width"]
        current.append(placed)

    flush()
    return screens


def _row_widths_of(screen: list[dict]) -> list[int]:
    rw = [0, 0]
    for w in screen:
        right = w["x"] + w["width"]
        if right > rw[w["row"]]:
            rw[w["row"]] = right
    return rw


# ============================================================================
# States — one frame per "this word just appeared"
# ============================================================================

def _build_states(
    screens: list[list[dict]],
    audio_duration: float,
) -> list[dict]:
    """
    A "state" is what the frame looks like at a given time.  We emit one
    state every time the visible word set changes (i.e. each time a new
    word appears, plus an initial blank if the first word isn't at t=0).

    Each state stores:
      visible            — list of word dicts currently on screen
      screen_row_widths  — final row widths of this state's screen, so
                           that positions don't shift as words appear
      start, end         — the time window this state is shown
    """
    states: list[dict] = []

    # Optional leading blank frame.
    first_start = screens[0][0]["start"] if screens else 0.0
    if first_start > 0.01:
        states.append({"visible": [], "screen_row_widths": [0, 0],
                       "start": 0.0})

    for screen in screens:
        rw = _row_widths_of(screen)
        for i, w in enumerate(screen):
            states.append({
                "visible":           screen[: i + 1],
                "screen_row_widths": rw,
                "start":             w["start"],
            })

    if not states:
        states.append({"visible": [], "screen_row_widths": [0, 0],
                       "start": 0.0})

    # Each state runs until the next one starts; last runs to end of audio.
    for i in range(len(states) - 1):
        states[i]["end"] = states[i + 1]["start"]
    states[-1]["end"] = audio_duration

    # Drop zero-duration entries (can happen if two words land on the
    # exact same timestamp due to a degenerate line).
    states = [s for s in states if s["end"] - s["start"] > 1e-4]
    return states


# ============================================================================
# Rendering
# ============================================================================

def _render_state(state: dict, font, out_path: str) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (VIDEO_W, VIDEO_H), color=(0, 0, 0))
    if state["visible"]:
        draw = ImageDraw.Draw(img)
        rw = state["screen_row_widths"]
        two_rows = rw[1] > 0

        if two_rows:
            block_h = LINE_HEIGHT + FONT_SIZE
            y0 = (VIDEO_H - block_h) // 2
        else:
            y0 = (VIDEO_H - FONT_SIZE) // 2
        y1 = y0 + LINE_HEIGHT
        ys = [y0, y1]

        for w in state["visible"]:
            row = w["row"]
            row_offset = (VIDEO_W - rw[row]) // 2
            draw.text(
                (row_offset + w["x"], ys[row]),
                w["text"],
                font=font,
                fill=(255, 255, 255),
            )
    img.save(out_path, "PNG")


# ============================================================================
# ffmpeg assembly
# ============================================================================

def _write_concat_list(states: list[dict], frame_dir: str,
                       list_path: str) -> None:
    """
    Write an ffconcat list with absolute paths.  The last image is
    repeated without a `duration` line because ffmpeg's concat demuxer
    needs a "next file" hint to terminate the final segment cleanly.
    """
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for i, s in enumerate(states):
            path = os.path.abspath(os.path.join(frame_dir, f"frame_{i:05d}.png"))
            dur = s["end"] - s["start"]
            f.write(f"file '{path}'\n")
            f.write(f"duration {dur:.6f}\n")
        last = os.path.abspath(
            os.path.join(frame_dir, f"frame_{len(states) - 1:05d}.png")
        )
        f.write(f"file '{last}'\n")


def _run_ffmpeg(list_path: str, audio_path: str, output_path: str) -> None:
    out_parent = os.path.dirname(output_path)
    if out_parent:
        os.makedirs(out_parent, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-loglevel", "warning",
        "-stats",
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-i", audio_path,
        "-vf", f"fps={FPS},format=yuv420p",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, check=True)


# ============================================================================
# Public entry point
# ============================================================================

def run(
    script_audio_file:         str = SCRIPT_AUDIO_FILE,
    timestamps_absolute_file:  str = TIMESTAMPS_ABSOLUTE_FILE,
    script_timings_file:       str = SYNCHRONIZED_SCRIPT_OUTPUT_FILE,
    output_video:              str = OUTPUT_VIDEO,
    word_timings_file:         str = WORD_TIMINGS_FILE,
) -> None:
    _check_tool("ffmpeg")
    _check_tool("ffprobe")

    for label, path in (
        ("audio",         script_audio_file),
        ("line starts",   timestamps_absolute_file),
        ("line durations", script_timings_file),
    ):
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{label} not found: {path!r}\n"
                f"  → run AUDIO_SCRIPT_SYNCHRONIZER.run() first to "
                f"generate the timing files."
            )

    print("─" * 70)
    print("WORD-BY-WORD VIDEO GENERATOR")
    print("─" * 70)
    print(f"  audio:   {script_audio_file}")
    print(f"  starts:  {timestamps_absolute_file}")
    print(f"  durs:    {script_timings_file}")
    print(f"  output:  {output_video}")

    line_starts   = _load_json(timestamps_absolute_file)
    line_durations = _load_json(script_timings_file)
    ordered_lines = list(line_starts.keys())
    audio_dur     = _audio_duration(script_audio_file)
    print(f"  lines:   {len(ordered_lines)}")
    print(f"  length:  {audio_dur:.2f}s")

    rate = _estimate_speech_rate(line_durations, ordered_lines)

    precise = None
    if os.path.exists(word_timings_file):
        precise = _load_json(word_timings_file)
        n_lines_with_precise = sum(1 for ln in ordered_lines
                                    if ln in precise and precise[ln])
        print(f"  precise word timings: {word_timings_file}  "
              f"({n_lines_with_precise}/{len(ordered_lines)} lines)")
    else:
        print(f"  precise word timings: not found  "
              f"(falling back to {rate:.2f} syl/sec estimate)")

    print("\n[1/4] building word events…")
    words = _build_word_timings(line_starts, line_durations,
                                ordered_lines, rate,
                                precise_word_timings=precise)
    n_precise = 0
    if precise:
        for ln in ordered_lines:
            if ln in precise and len(precise[ln]) == len(_split_words(ln)):
                n_precise += len(precise[ln])
    print(f"      {len(words)} word events "
          f"({n_precise} precise, {len(words) - n_precise} estimated)")

    font_path = _find_font()
    print(f"\n[2/4] measuring + packing with {font_path}")
    space_w = _measure_words(words, font_path)
    screens = _pack_screens(words, space_w)
    print(f"      packed into {len(screens)} screens "
          f"(avg {len(words) / max(1, len(screens)):.1f} words/screen)")

    states = _build_states(screens, audio_dur)
    print(f"\n[3/4] rendering {len(states)} frames…")

    from PIL import ImageFont
    font = ImageFont.truetype(font_path, FONT_SIZE)

    with tempfile.TemporaryDirectory(prefix="wbw_") as tmpdir:
        for i, s in enumerate(states):
            _render_state(s, font, os.path.join(tmpdir, f"frame_{i:05d}.png"))
            if (i + 1) % 50 == 0:
                print(f"      {i + 1}/{len(states)}")

        list_path = os.path.join(tmpdir, "concat.txt")
        _write_concat_list(states, tmpdir, list_path)

        print("\n[4/4] muxing with ffmpeg…")
        _run_ffmpeg(list_path, script_audio_file, output_video)

    print()
    print(f"✓ wrote {output_video}")
    print("─" * 70)


if __name__ == "__main__":
    run()
