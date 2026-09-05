"""
words_on_screen.py
==================
Kinetic typography renderer.

TWO ENTRY POINTS:

1) run(...)                — render the WHOLE script as one MP4 with audio.
                              (Original behaviour, unchanged.)

2) render_scene_to_video(...) — render a SINGLE scene as a silent MP4 sized
                                to exactly match that scene's runtime.
                                Used by main.py for READ_OUT scenes — the
                                stitcher overlays the global narration on top.

DESIGN
------
All visual / layout / timing knobs live on `WordRenderConfig`. The module
also exports a `DEFAULT_CONFIG` snapshot of the original module constants
so legacy callers keep working without passing a config.

Per-scene rendering accepts an optional `precise_word_timings` list (in
ABSOLUTE seconds, as produced by audio_script_synchronizer) and converts
them to relative time inside the scene. When precise timings are absent
or don't match the word count we fall back to syllable-based estimation
that's scaled to exactly fill the scene duration.

PIXELATED MODE
--------------
`WordRenderConfig.pixelated` (default True) renders BLACK text on a WHITE
background with a chunky low-res "pixel" look to match the ms-paint stickman
aesthetic. The pixel look comes from a pixel FONT when one is found, and/or
from a nearest-neighbour downscale so it still looks pixelated WITHOUT a
special font. See _find_pixel_font() for where pixel fonts are searched.

Requirements
------------
    pip install Pillow
    ffmpeg + ffprobe on PATH
"""

from __future__ import annotations

# Allow running this file directly from the repo root (uv run ___visuals/words_on_screen.py).
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# ============================================================================
# CONFIG  (matches the layout the user uses with audio_script_synchronizer)
# ============================================================================

_NAME = "spices"
_CACHE_DIR = f"{_NAME}-CACHE" if _NAME else "CACHE"
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

# Optional per-word timings produced by audio_script_synchronizer. When
# present, this file is the source of truth for word-level sync. When
# absent (or partial), we fall back to syllable-based estimation.
WORD_TIMINGS_FILE = (
    f"{_CACHE_DIR}/{_NAME}_word_timings.json" if _NAME
    else f"{_CACHE_DIR}/word_timings.json"
)

AUDIO_START_DELAY_SECONDS = 0.5
OUTPUT_VIDEO = f"{_SCRIPT_STEM}_words.mp4"

# Default video specs. 1920x1080 @ 30 fps is YouTube horizontal HD.
# For Shorts swap to 1080x1920.
VIDEO_W = 1920
VIDEO_H = 1080
FPS = 30

# Default text styling.
FONT_SIZE            = 96       # pixels
LINE_HEIGHT          = 140      # vertical spacing between row 0 and row 1
HORIZONTAL_MARGIN    = 100      # left/right safe area
MAX_WORDS_PER_SCREEN = 8
ROW_COUNT            = 2

# Show each word this many seconds BEFORE its computed start time, so
# words land slightly ahead of the audio rather than behind. Whisper's
# word boundaries are a few tens of ms imprecise even in precise mode;
# this gives a small head-start so they always feel anticipatory.
WORD_LEAD_SECONDS = 0.08

# Fallback rate when there's no precise word timing data AND we're
# rendering a single scene (so we don't have a multi-line corpus to
# calibrate from). 4.5 syl/sec is mid-range English narration.
SYLLABLES_PER_SECOND_FALLBACK = 4.5

# Font discovery — first existing path wins.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

# ── Pixel-font discovery (used when WordRenderConfig.pixelated is True) ──────
# Set this to an absolute .ttf path to FORCE a specific pixel font and skip
# the search below. Leave "" to auto-detect.
PIXEL_FONT_OVERRIDE = ""

# Common free pixel-font filenames. Drop one into ~/.local/share/fonts/ (then
# run `fc-cache -f`) or a ./fonts/ folder next to this script and it's picked
# up automatically. Press Start 2P / VT323 / Silkscreen / Pixelify Sans are
# all free on Google Fonts.
_PIXEL_FONT_NAMES = [
    "PressStart2P-Regular.ttf", "PressStart2P.ttf",
    "VT323-Regular.ttf",
    "Silkscreen-Regular.ttf", "Silkscreen-Bold.ttf",
    "PixelifySans-Regular.ttf", "PixelifySans-VariableFont_wght.ttf",
    "PixelOperator.ttf", "PixelOperator-Bold.ttf", "PixelOperator8.ttf",
    "Minecraftia-Regular.ttf",
]

def _script_dir() -> str:
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:          # interactive / exec
        return os.getcwd()

_PIXEL_FONT_DIRS = [
    os.path.expanduser("~/.local/share/fonts"),
    os.path.expanduser("~/.fonts"),
    "/usr/local/share/fonts",
    "/usr/share/fonts/truetype",
    os.path.join(_script_dir(), "fonts"),
    os.path.join(_script_dir(), "_FONTS"),
]

# Flat fast-path candidates (dir/name). _find_pixel_font also does a shallow
# recursive walk of the dirs above, so fonts inside subfolders are found too.
PIXEL_FONT_CANDIDATES = [
    os.path.join(d, n) for d in _PIXEL_FONT_DIRS for n in _PIXEL_FONT_NAMES
]


# ============================================================================
# WordRenderConfig — all visual / layout / timing knobs
# ============================================================================

@dataclass
class WordRenderConfig:
    """
    Everything the renderer needs that ISN'T data. Pass a custom instance
    to render_scene_to_video() to override per-scene; otherwise the
    module-level DEFAULT_CONFIG is used.
    """
    # Frame
    video_w: int = VIDEO_W
    video_h: int = VIDEO_H
    fps: int = FPS

    # Type
    font_size: int = FONT_SIZE
    line_height: int = LINE_HEIGHT
    horizontal_margin: int = HORIZONTAL_MARGIN
    text_color: tuple[int, int, int] = (255, 255, 255)
    background_color: tuple[int, int, int] = (0, 0, 0)

    # Layout
    max_words_per_screen: int = MAX_WORDS_PER_SCREEN
    row_count: int = ROW_COUNT

    # Timing
    word_lead_seconds: float = WORD_LEAD_SECONDS
    syllables_per_second_fallback: float = SYLLABLES_PER_SECOND_FALLBACK
    # When estimating with syllables, if total estimated duration is LESS
    # than the scene duration, should we scale UP to fill the scene, or
    # leave slack at the end (last word stays on screen longer)?
    # Leaving slack matches the original run() behaviour and reads more
    # naturally when a scene has a deliberate trailing pause.
    fill_scene_when_estimating: bool = False

    # Encoding
    crf: int = 20
    preset: str = "medium"
    font_path: str | None = None  # auto-detect when None

    # ── Pixelated ("retro" / ms-paint) styling ───────────────────────────
    # When True: WHITE background, BLACK text, chunky low-res pixel look.
    # The pixel look comes from a pixel FONT when one is found (see
    # _find_pixel_font), and/or from a nearest-neighbour downscale so it
    # still looks pixelated even without a pixel font installed.
    pixelated: bool = True
    pixelated_background_color: tuple[int, int, int] = (255, 255, 255)
    pixelated_text_color: tuple[int, int, int] = (0, 0, 0)
    # Nearest-neighbour pixelation block size, in OUTPUT pixels.
    #   1  = no downscale (rely on the pixel font for crisp pixels)
    #   >1 = force solid block-pixels of this size even with an ordinary font
    pixel_block_size: int = 3
    # If pixelated is on but NO pixel font is found, fall back to this block
    # size so the result is still visibly pixelated without a special font.
    pixel_block_size_when_no_pixel_font: int = 3
    # Disable glyph antialiasing when leaning on a pixel font, so edges stay
    # crisp instead of being smoothed to grey.
    disable_antialiasing_for_pixel_font: bool = True

    # ── One-word-at-a-time ────────────────────────────────────────────────
    # When True, show a SINGLE word on screen at a time (each word centred),
    # instead of building words up into a multi-word sentence/screen. Each
    # word is shown for its own time slice (until the next word's start, or
    # the scene end for the last word). Applies to BOTH run() and the
    # per-scene renderer; pass a config with this False to get the old
    # sentence build-up.
    one_word_at_a_time: bool = True

    # ── Colour helpers (respect pixelated mode) ──────────────────────────
    def effective_background_color(self) -> tuple[int, int, int]:
        return (self.pixelated_background_color if self.pixelated
                else self.background_color)

    def effective_text_color(self) -> tuple[int, int, int]:
        return (self.pixelated_text_color if self.pixelated
                else self.text_color)

    # ── Font resolution ──────────────────────────────────────────────────
    def resolve_font(self) -> tuple[str, bool]:
        """Return (font_path, is_pixel_font).

        Priority: explicit font_path / PIXEL_FONT_OVERRIDE → a known pixel
        font from the search dirs → ordinary font fallback.
        """
        if self.font_path:
            return self.font_path, True            # user chose it deliberately
        if self.pixelated:
            pf = _find_pixel_font()
            if pf:
                return pf, True
        return _find_font(), False

    def resolved_font_path(self) -> str:           # back-compat shim
        return self.resolve_font()[0]

    def effective_pixel_block(self, is_pixel_font: bool) -> int:
        """How many output px per pixel-cell. 1 = no downscale."""
        if not self.pixelated:
            return 1
        if is_pixel_font:
            return max(1, self.pixel_block_size)
        # No pixel font → force a chunky downscale so it still looks pixelated.
        return max(self.pixel_block_size, self.pixel_block_size_when_no_pixel_font)


# Module-level default — snapshot of the original constants so existing
# callers (run()) keep working unchanged.
DEFAULT_CONFIG = WordRenderConfig()


# ============================================================================
# Small helpers
# ============================================================================

def _find_font() -> str:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise RuntimeError(
        "No font found on disk.  Edit FONT_CANDIDATES at the top of "
        "words_on_screen.py to point at a .ttf you have."
    )


def _find_pixel_font() -> str | None:
    """Locate a pixel font, or None if none is installed.

    Checks PIXEL_FONT_OVERRIDE, then the flat candidate list, then a shallow
    recursive walk of the font dirs (so e.g. ~/.local/share/fonts/PressStart2P/
    PressStart2P-Regular.ttf is found even though it's in a subfolder).
    """
    if PIXEL_FONT_OVERRIDE and os.path.exists(PIXEL_FONT_OVERRIDE):
        return PIXEL_FONT_OVERRIDE
    for p in PIXEL_FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    wanted = {n.lower() for n in _PIXEL_FONT_NAMES}
    for d in _PIXEL_FONT_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            for root, _dirs, files in os.walk(d):
                for fn in files:
                    if fn.lower() in wanted:
                        return os.path.join(root, fn)
        except OSError:
            continue
    return None


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
    to the next line). Slower lines are inflated by silence; faster lines
    are closer to the speaker's true pace. Median of the fastest half is
    a robust estimate. Clamped to a plausible English narration range so
    a degenerate dataset can't produce nonsense.
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
        return SYLLABLES_PER_SECOND_FALLBACK
    ratios.sort()
    fastest_half = ratios[: max(1, len(ratios) // 2)]
    median = fastest_half[len(fastest_half) // 2]
    return max(2.5, min(7.0, 1.0 / median))


# ============================================================================
# Word-level timing — multi-line (used by run())
# ============================================================================

def _build_word_timings(
    line_starts: dict,
    line_durations: dict,
    ordered_lines: list[str],
    rate: float,
    precise_word_timings: dict | None = None,
    cfg: WordRenderConfig = DEFAULT_CONFIG,
) -> list[dict]:
    """
    Produce per-word [{text, start, end, line_idx}] events for the FULL
    script. See render_scene_to_video() for the single-scene version.

    Two modes per line:

    1) PRECISE — when precise_word_timings[line] exists AND its length
       matches the whitespace-split word count, use those start times
       directly. Each word's end = next word's start (or line end).

    2) ESTIMATE — distribute the line's duration across words using
       syllable-based estimates at the auto-tuned `rate`. If total
       estimate exceeds the line duration, scale DOWN to fit. Trailing
       slack is left as silence (last word stays on screen).
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
                # Clamp into the line window in case of off-by-tiny
                # upstream rounding.
                start = max(line_start, min(start, line_end))
                end   = max(start,      min(end,   line_end))
                out.append({"text": wd, "start": start, "end": end,
                            "line_idx": li})
            continue

        # --- estimate mode ------------------------------------------------
        start = float(line_starts[line])
        dur   = float(line_durations[line])
        per_word = [_syllables(w) / rate for w in words]
        total_est = sum(per_word)
        if total_est > dur and total_est > 0:
            scale = dur / total_est
            per_word = [d * scale for d in per_word]
        cursor = start
        for wd, d in zip(words, per_word):
            out.append({"text": wd, "start": cursor,
                        "end": cursor + d, "line_idx": li})
            cursor += d

    # Apply a small global lead so words appear slightly ahead of audio.
    if cfg.word_lead_seconds > 0:
        for ev in out:
            ev["start"] = max(0.0, ev["start"] - cfg.word_lead_seconds)
            ev["end"]   = max(ev["start"], ev["end"] - cfg.word_lead_seconds)
    return out


def _build_word_timings_for_scene(
    script_text: str,
    line_duration: float,
    precise_word_timings: list[dict] | None,
    line_start_absolute: float,
    cfg: WordRenderConfig,
) -> list[dict]:
    """
    Per-word events for a SINGLE scene, in RELATIVE time (scene starts
    at t=0 in the output MP4). Same two-mode logic as the multi-line
    version, but tuned for stand-alone scene rendering.
    """
    words = _split_words(script_text)
    if not words:
        return []

    out: list[dict] = []

    # --- precise mode --------------------------------------------------
    if (precise_word_timings is not None
            and len(precise_word_timings) == len(words)):
        for i, (wd, pw) in enumerate(zip(words, precise_word_timings)):
            rel_start = float(pw["start"]) - line_start_absolute
            if i + 1 < len(precise_word_timings):
                rel_end = (float(precise_word_timings[i + 1]["start"])
                           - line_start_absolute)
            else:
                rel_end = line_duration
            # Clamp into [0, line_duration]
            rel_start = max(0.0, min(rel_start, line_duration))
            rel_end   = max(rel_start, min(rel_end, line_duration))
            out.append({"text": wd, "start": rel_start, "end": rel_end,
                        "line_idx": 0})

    # --- estimate mode -------------------------------------------------
    else:
        per_word = [_syllables(w) / cfg.syllables_per_second_fallback
                    for w in words]
        total_est = sum(per_word)
        if total_est > 0:
            if total_est > line_duration:
                # Scale DOWN to fit
                scale = line_duration / total_est
                per_word = [d * scale for d in per_word]
            elif cfg.fill_scene_when_estimating:
                # Scale UP to exactly fill the scene
                scale = line_duration / total_est
                per_word = [d * scale for d in per_word]
            # else: leave trailing slack (last word stays on screen)
        cursor = 0.0
        for wd, d in zip(words, per_word):
            out.append({"text": wd, "start": cursor,
                        "end": min(cursor + d, line_duration),
                        "line_idx": 0})
            cursor += d

    # Apply global lead
    if cfg.word_lead_seconds > 0:
        for ev in out:
            ev["start"] = max(0.0, ev["start"] - cfg.word_lead_seconds)
            ev["end"]   = max(ev["start"], ev["end"] - cfg.word_lead_seconds)

    return out


# ============================================================================
# Layout — measure words, pack into screens
# ============================================================================

def _measure_words(words: list[dict], cfg: WordRenderConfig) -> int:
    from PIL import ImageFont
    font = ImageFont.truetype(cfg.resolved_font_path(), cfg.font_size)
    # Width of a single space character — used as the inter-word gap.
    space_w = font.getbbox(" ")[2] or int(cfg.font_size * 0.3)
    for w in words:
        l, _, r, _ = font.getbbox(w["text"])
        w["width"] = r - l
    return space_w


def _pack_screens(words: list[dict], space_w: int,
                  cfg: WordRenderConfig) -> list[list[dict]]:
    """
    Greedy two-row packer with STRICT left-to-right, top-to-bottom order.

    Once a word goes to row 1, all subsequent words on this screen also
    go to row 1. We never backtrack to row 0 — that would put words out
    of reading order. (Old bug: "if the sharks becommed the fish" rendered
    as "if the sharks the / becommed fish" because short later words
    slipped back into row 0's leftover space.)

    When row 1 is also full or out of horizontal space, the screen closes
    and we start a new one on row 0.

    ONE-WORD-AT-A-TIME (cfg.one_word_at_a_time): short-circuit the packer and
    make every word its own single-word screen, so the renderer shows one
    centred word at a time instead of a building sentence.
    """
    # One word at a time → each word is its own (single-word, row-0) screen.
    if getattr(cfg, "one_word_at_a_time", False):
        screens = []
        for w in words:
            placed = dict(w)
            placed["row"] = 0
            placed["x"] = 0
            screens.append([placed])
        return screens

    max_text_w = cfg.video_w - 2 * cfg.horizontal_margin
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
                    and len(current) < cfg.max_words_per_screen)

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
    total_duration: float,
) -> list[dict]:
    """
    A "state" is what the frame looks like at a given time. We emit one
    state every time the visible word set changes (i.e. each time a new
    word appears, plus an initial blank if the first word isn't at t=0).

    Each state stores:
      visible            — list of word dicts currently on screen
      screen_row_widths  — final row widths of this state's screen, so
                           positions don't shift as words appear
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

    # Each state runs until the next one starts; last runs to end.
    for i in range(len(states) - 1):
        states[i]["end"] = states[i + 1]["start"]
    states[-1]["end"] = total_duration

    # Drop zero-duration entries (can happen if two words land on the
    # exact same timestamp due to a degenerate line).
    states = [s for s in states if s["end"] - s["start"] > 1e-4]
    return states


# ============================================================================
# Rendering
# ============================================================================

def _pixelate_image(img, block: int):
    """Nearest-neighbour pixelation.

    Area-downscale (BOX) to a coarse grid, then NEAREST upscale back to full
    size — producing solid `block`×`block` block-pixels. Works with any font.
    """
    from PIL import Image
    if block <= 1:
        return img
    w, h = img.size
    sw, sh = max(1, w // block), max(1, h // block)
    small = img.resize((sw, sh), Image.BOX)
    return small.resize((w, h), Image.NEAREST)


def _render_state(state: dict, font, out_path: str,
                  cfg: WordRenderConfig, pixelate_block: int = 1) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (cfg.video_w, cfg.video_h),
                    color=cfg.effective_background_color())
    if state["visible"]:
        draw = ImageDraw.Draw(img)

        # Crisp (no-AA) glyphs when we're leaning on a pixel font. The
        # downscale path (pixelate_block > 1) keeps AA on, since the block
        # averaging is what defines the pixels there anyway.
        if (cfg.pixelated and pixelate_block <= 1
                and cfg.disable_antialiasing_for_pixel_font):
            try:
                draw.fontmode = "1"     # "1" = 1-bit, no antialiasing
            except Exception:
                pass

        rw = state["screen_row_widths"]
        two_rows = rw[1] > 0

        if two_rows:
            block_h = cfg.line_height + cfg.font_size
            y0 = (cfg.video_h - block_h) // 2
        else:
            y0 = (cfg.video_h - cfg.font_size) // 2
        y1 = y0 + cfg.line_height
        ys = [y0, y1]

        fill = cfg.effective_text_color()
        for w in state["visible"]:
            row = w["row"]
            row_offset = (cfg.video_w - rw[row]) // 2
            draw.text(
                (row_offset + w["x"], ys[row]),
                w["text"],
                font=font,
                fill=fill,
            )

    if pixelate_block > 1:
        img = _pixelate_image(img, pixelate_block)

    img.save(out_path, "PNG")


# ============================================================================
# ffmpeg assembly
# ============================================================================

def _write_concat_list(states: list[dict], frame_dir: str,
                       list_path: str) -> None:
    """
    Write an ffconcat list with absolute paths. The last image is
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


def _run_ffmpeg(
    list_path: str,
    audio_path: str | None,
    output_path: str,
    cfg: WordRenderConfig,
) -> None:
    """
    Encode the frame sequence to MP4.

    When audio_path is None, emits a silent video. This is what scene-
    level rendering uses — the stitcher overlays the global narration
    audio on top of the scene's MP4, so the scene's own audio track
    would either double up or get muted depending on mix order.
    """
    out_parent = os.path.dirname(output_path)
    if out_parent:
        os.makedirs(out_parent, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-loglevel", "warning",
        "-stats",
        "-f", "concat", "-safe", "0", "-i", list_path,
    ]
    if audio_path:
        cmd += ["-i", audio_path]
    cmd += [
        "-vf", f"fps={cfg.fps},format=yuv420p",
        "-c:v", "libx264",
        "-preset", cfg.preset,
        "-crf", str(cfg.crf),
    ]
    if audio_path:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd.append(output_path)
    subprocess.run(cmd, check=True)


# ============================================================================
# PUBLIC: per-scene renderer (used by main.py for READ_OUT scenes)
# ============================================================================

def render_scene_to_video(
    script_text: str,
    line_duration: float,
    output_path: str,
    *,
    precise_word_timings: list[dict] | None = None,
    line_start_absolute: float = 0.0,
    config: WordRenderConfig | None = None,
) -> str:
    """
    Render ONE scene's word-by-word kinetic typography to a SILENT MP4
    sized to exactly `line_duration` seconds.

    Parameters
    ----------
    script_text
        The narration line to display, e.g.:
        "If you open your kitchen cupboard right now,"
    line_duration
        Output MP4 length, in seconds. Must equal the scene's runtime in
        the final cut so the stitcher can drop it in without re-trimming.
    output_path
        Where to write the .mp4. Parent dirs are created if missing.
    precise_word_timings
        Optional Whisper-derived per-word timings for THIS line — a list
        of dicts each containing at least a "start" key, in ABSOLUTE
        seconds (i.e. position within the full narration recording).
        If list length matches the word count, this overrides syllable
        estimation. If absent or mismatched, falls back gracefully.
    line_start_absolute
        Where the scene starts in the full narration audio. Used only to
        convert `precise_word_timings` from absolute → relative time.
    config
        Visual / layout overrides. Defaults to DEFAULT_CONFIG.

    Returns
    -------
    str
        The output_path, for chaining convenience.
    """
    cfg = config or DEFAULT_CONFIG
    _check_tool("ffmpeg")
    _check_tool("ffprobe")

    if line_duration <= 0:
        raise ValueError(
            f"line_duration must be > 0 (got {line_duration}) for "
            f"script_text={script_text!r}"
        )

    out_parent = os.path.dirname(output_path)
    if out_parent:
        os.makedirs(out_parent, exist_ok=True)

    print(f"[words-on-screen] rendering scene → {output_path}")
    print(f"[words-on-screen]   text:     '{script_text}'")
    print(f"[words-on-screen]   duration: {line_duration:.3f}s")
    print(f"[words-on-screen]   precise:  "
          f"{'yes' if precise_word_timings else 'no'} "
          f"({len(precise_word_timings) if precise_word_timings else 0} words)")

    events = _build_word_timings_for_scene(
        script_text=script_text,
        line_duration=line_duration,
        precise_word_timings=precise_word_timings,
        line_start_absolute=line_start_absolute,
        cfg=cfg,
    )
    print(f"[words-on-screen]   built {len(events)} word event(s)")

    # Resolve font + pixelation ONCE (file checks are cheap but no need to
    # repeat them per frame).
    font_path, is_pixel = cfg.resolve_font()
    pixel_block = cfg.effective_pixel_block(is_pixel)
    if cfg.pixelated:
        print(f"[words-on-screen]   pixelated: font={os.path.basename(font_path)} "
              f"(pixel_font={is_pixel}), block={pixel_block}px, "
              f"bg={cfg.effective_background_color()}, "
              f"fg={cfg.effective_text_color()}")

    # Empty / whitespace-only script — emit a blank silent MP4 of the
    # right length instead of crashing. (Edge case for a scene whose
    # text is e.g. just punctuation.)
    if not events:
        print(f"[words-on-screen]   (no words — emitting blank silent video)")
        _emit_blank_silent_video(output_path, line_duration, cfg)
        return output_path

    space_w = _measure_words(events, cfg)
    screens = _pack_screens(events, space_w, cfg)
    states  = _build_states(screens, line_duration)
    print(f"[words-on-screen]   packed into {len(screens)} screen(s), "
          f"{len(states)} state frame(s)")

    from PIL import ImageFont
    font = ImageFont.truetype(font_path, cfg.font_size)

    with tempfile.TemporaryDirectory(prefix="wbw_scene_") as tmpdir:
        for i, s in enumerate(states):
            _render_state(s, font,
                          os.path.join(tmpdir, f"frame_{i:05d}.png"), cfg,
                          pixelate_block=pixel_block)
        list_path = os.path.join(tmpdir, "concat.txt")
        _write_concat_list(states, tmpdir, list_path)
        _run_ffmpeg(list_path, None, output_path, cfg)  # silent

    print(f"[words-on-screen] ✓ wrote {output_path}")
    return output_path


def _emit_blank_silent_video(output_path: str, duration: float,
                             cfg: WordRenderConfig) -> None:
    """Edge case: render a silent solid-colour clip of given duration."""
    bg_hex = "#{:02x}{:02x}{:02x}".format(*cfg.effective_background_color())
    cmd = [
        "ffmpeg", "-y",
        "-loglevel", "warning",
        "-stats",
        "-f", "lavfi",
        "-i", f"color=c={bg_hex}:s={cfg.video_w}x{cfg.video_h}:r={cfg.fps}",
        "-t", f"{duration:.6f}",
        "-c:v", "libx264",
        "-preset", cfg.preset,
        "-crf", str(cfg.crf),
        "-pix_fmt", "yuv420p",
        "-an",
        output_path,
    ]
    subprocess.run(cmd, check=True)


# ============================================================================
# PUBLIC: whole-script renderer (original entry point)
# ============================================================================

def run(
    script_audio_file:         str = SCRIPT_AUDIO_FILE,
    timestamps_absolute_file:  str = TIMESTAMPS_ABSOLUTE_FILE,
    script_timings_file:       str = SYNCHRONIZED_SCRIPT_OUTPUT_FILE,
    output_video:              str = OUTPUT_VIDEO,
    word_timings_file:         str = WORD_TIMINGS_FILE,
    config:                    WordRenderConfig | None = None,
) -> None:
    cfg = config or DEFAULT_CONFIG
    _check_tool("ffmpeg")
    _check_tool("ffprobe")

    for label, path in (
        ("audio",          script_audio_file),
        ("line starts",    timestamps_absolute_file),
        ("line durations", script_timings_file),
    ):
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{label} not found: {path!r}\n"
                f"  → run audio_script_synchronizer.run() first to "
                f"generate the timing files."
            )

    print("─" * 70)
    print("WORDS-ON-SCREEN VIDEO GENERATOR")
    print("─" * 70)
    print(f"  audio:   {script_audio_file}")
    print(f"  starts:  {timestamps_absolute_file}")
    print(f"  durs:    {script_timings_file}")
    print(f"  output:  {output_video}")

    line_starts    = _load_json(timestamps_absolute_file)
    line_durations = _load_json(script_timings_file)
    ordered_lines  = list(line_starts.keys())
    audio_dur      = _audio_duration(script_audio_file)
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
                                precise_word_timings=precise,
                                cfg=cfg)
    n_precise = 0
    if precise:
        for ln in ordered_lines:
            if ln in precise and len(precise[ln]) == len(_split_words(ln)):
                n_precise += len(precise[ln])
    print(f"      {len(words)} word events "
          f"({n_precise} precise, {len(words) - n_precise} estimated)")

    # Resolve font + pixelation once.
    font_path, is_pixel = cfg.resolve_font()
    pixel_block = cfg.effective_pixel_block(is_pixel)

    print(f"\n[2/4] measuring + packing with {font_path}")
    if cfg.pixelated:
        print(f"      pixelated: pixel_font={is_pixel}, block={pixel_block}px, "
              f"bg={cfg.effective_background_color()}, "
              f"fg={cfg.effective_text_color()}")
    space_w = _measure_words(words, cfg)
    screens = _pack_screens(words, space_w, cfg)
    print(f"      packed into {len(screens)} screens "
          f"(avg {len(words) / max(1, len(screens)):.1f} words/screen)")

    states = _build_states(screens, audio_dur)
    print(f"\n[3/4] rendering {len(states)} frames…")

    from PIL import ImageFont
    font = ImageFont.truetype(font_path, cfg.font_size)

    with tempfile.TemporaryDirectory(prefix="wbw_") as tmpdir:
        for i, s in enumerate(states):
            _render_state(s, font,
                          os.path.join(tmpdir, f"frame_{i:05d}.png"), cfg,
                          pixelate_block=pixel_block)
            if (i + 1) % 50 == 0:
                print(f"      {i + 1}/{len(states)}")

        list_path = os.path.join(tmpdir, "concat.txt")
        _write_concat_list(states, tmpdir, list_path)

        print("\n[4/4] muxing with ffmpeg…")
        _run_ffmpeg(list_path, script_audio_file, output_video, cfg)

    print()
    print(f"✓ wrote {output_video}")
    print("─" * 70)


if __name__ == "__main__":
    run()
