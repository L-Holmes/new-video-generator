from __future__ import annotations
"""
compositor.py
=============

Composite images / videos / GIFs / text on top of a background (image, GIF, or MP4)
for horizontal YouTube-style content (1920x1080).

Features
--------
- Random background pick from `_BACKGROUNDS/` (or use a specific path).
- Position by percentage of canvas (x=0,y=0 = bottom-left, x=100,y=100 = top-right),
  CENTER-anchored.
- Optional auto-scale to a percentage of canvas height.
- Optional `rembg` background removal for image overlays.
- Subtle drop-shadow on overlays for legibility on busy backgrounds.
- Animated overlays (mp4 / gif) supported.
- Text overlays (no image needed).
- 10 transitions: fade / slide_left / slide_right / slide_up / slide_down /
  slide_diag / zoom_in / zoom_out / pop / bounce_in (+ "random", "none").
  When a transition is requested but everything is static, output is forced to MP4.
- Composite mode: emits one file per build-up stage (bg+1, bg+1+2, ...).
  In composite mode, ONLY the newest overlay plays its transition — older overlays
  render at their final position so each stage adds one animation cleanly.
- When a transition is used, ALSO emits a `_loop.mp4` companion that is the same
  composition without the intro animation, so it can be looped seamlessly after
  the intro.

Dependencies
------------
    pip install moviepy pillow rembg numpy


MY REQUEST:

 Create python file:

* ...
* Gets a random background from the BACKGROUNDS_DIR = "_BACKGROUNDS" directory (unless its given a specific filename, in which case it will just get that specific one)
* main function param 1) An ordered list of jsons. Json contains:
* path -> <path of image>
* position ->  the position at which it should be positioned .. (x,y) coords, as a percentage of the screen width. i.e. its like maths grid so x across and y up. Bottom left is 0,0. top right is 100,100
* removeBG -> boolean of whether to remove background
* param 2 = the path to the output folder
* param 3 = composite flag (whether to generate on image for each being added)
* param 4(optional) = the background path.


What it does:

* First it gets the background. It may be an mp4, a gif, or an image. Obviously if its an mp4 we want it looped.
* It then layers the things on top in order.
* If something ha sthe remove background flag, it will remove the background first. e.g. like rembg or something like that. whatever is best with python (im on deb 13 if that matters)
* it then calculates the width of the background and then uses the position params to know where to put the image. 
* It adds the image on top at that position. 
* It outputs either an mp4 or an image file depending on whether the background and added elements where vids or images... (obviously needs all images to be an image.. one or more vids means video file...)
* It returns a list with the name of this video file. 
* If composite flag is enabled, it will create one output for each stage. E.g. first would be background + image 1, second would be background +image 1 + image 2 etc.  So then it will return an ordered list of the names of thees generated output files, starting with the most basic and ending with most complex. 

* For the main function in the file, do a test with adds 3 images in a row onto the background. 
* Use a centre anchor point for the images coords. 
* ensure you auto scale the images appropriately. Add an optional param to the json for scale (as a percentage of the page height. E.g. scale-page-height-percentage = "none" (or a value between 1 and 100)... 
* its for a horizontal youtube video so feel free to hardcode dimensions if that owuld be helpful... 

* Add another param: transitions. can be none. Have like 5 sensible defaults that will be good for an explainer video. If its a video output, pick a random transitoin. Of course if its an image but user adds transitoin, it will have to become an output video.. 
* be mindful of if there is a transition... we then want a non-looping video after that so that it doesn't transition in again... or maybe switch to an identical video, which is just everything after the transition, so as to keep the animated background and what not (yeah this is probably better)
* maybe add basic subtle shadow to images to make them more visible on the background???
* maybe a way to add text without linking to an image? as an alterantive json thing if that's possible??!??! If ont possible- don't add it I'll add it myself later as a seperate step!
* by the way, there may be a gif or mp4 passed in as one of the things being overlayed on top...

"""

"""
compositor.py
=============

"""
import logging
import os
import random
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from moviepy import (
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    vfx,
)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

DEBUG: bool = False                # Flip to True for verbose logs from this module

BACKGROUNDS_DIR: str = "_BACKGROUNDS"

CANVAS_WIDTH: int = 1920           # Hardcoded YouTube horizontal dimensions
CANVAS_HEIGHT: int = 1080

DEFAULT_VIDEO_DURATION: float = 5.0  # seconds for video output (intro stage)
DEFAULT_FPS: int = 30
TRANSITION_DURATION: float = 0.6   # seconds — how long an intro animation runs
INTRO_TAIL_PADDING: float = 0.05   # seconds of "settled" frames after the
                                   # transition completes, so the cut doesn't
                                   # land on the exact frame of arrival

# File-extension classification
IMAGE_EXTENSIONS: set = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
VIDEO_EXTENSIONS: set = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
GIF_EXTENSION: str = ".gif"

# Letterbox fill colour for backgrounds that don't match 16:9
LETTERBOX_COLOR: Tuple[int, int, int] = (0, 0, 0)



# ---------------------------------------------------------------------------
# Transition name constants  (single source of truth)
# ---------------------------------------------------------------------------

TRANSITION_NONE: str = "none"
TRANSITION_RANDOM: str = "random"

TRANSITION_FADE: str = "fade"
TRANSITION_SLIDE_LEFT: str = "slide_left"      # enters from right edge
TRANSITION_SLIDE_RIGHT: str = "slide_right"    # enters from left edge
TRANSITION_SLIDE_UP: str = "slide_up"          # enters from below
TRANSITION_SLIDE_DOWN: str = "slide_down"      # enters from above
TRANSITION_SLIDE_DIAG: str = "slide_diag"      # enters from bottom-left
TRANSITION_ZOOM_IN: str = "zoom_in"            # 0.6x -> 1x with crossfade
TRANSITION_ZOOM_OUT: str = "zoom_out"          # 1.5x -> 1x with crossfade
TRANSITION_POP: str = "pop"                    # 0 -> 1x with overshoot
TRANSITION_BOUNCE_IN: str = "bounce_in"        # drops in with bounce easing

# All real (non-special) transitions
TRANSITIONS: List[str] = [
    TRANSITION_FADE,
    TRANSITION_SLIDE_LEFT,
    TRANSITION_SLIDE_RIGHT,
    TRANSITION_SLIDE_UP,
    TRANSITION_SLIDE_DOWN,
    TRANSITION_SLIDE_DIAG,
    TRANSITION_ZOOM_IN,
    TRANSITION_ZOOM_OUT,
    TRANSITION_POP,
    TRANSITION_BOUNCE_IN,
]


# ---------------------------------------------------------------------------
# JSON keys (single source of truth for the overlay-spec dict format)
# ---------------------------------------------------------------------------

KEY_PATH: str = "path"
KEY_TEXT: str = "text"
KEY_POSITION: str = "position"
KEY_REMOVE_BG: str = "removeBG"
KEY_SCALE_PCT: str = "scale-page-height-percentage"
KEY_SCALE_BOX_PCT: str = "scale-fit-box-percentage"
# Like KEY_SCALE_PCT, but caps the LONGEST side at X% of canvas height
# so the image fits inside an X%-by-X% square. Use this for row/grid
# layouts where wide images would otherwise overlap their neighbours.
KEY_TRANSITION: str = "transition"
KEY_SHADOW: str = "shadow"
KEY_ROTATION: str = "rotation"
KEY_TEXT_SIZE: str = "size"
KEY_TEXT_COLOR: str = "color"
KEY_TEXT_MAX_WIDTH: str = "max-width-percentage"  # text wraps at this % of canvas


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("compositor")


def configure_default_logging() -> None:
    """Apply this module's preferred logging defaults.

    Call this from your script's `__main__` block. It silences PIL's debug
    spam and the verbose moviepy/proglog progress chatter, while keeping
    INFO/ERROR from the compositor itself visible. If `DEBUG` is True, the
    compositor logger goes to DEBUG.
    """
    # Silence library noise. These are the loggers responsible for the
    # screenfuls of TIFF tag / PNG chunk dumps and ffmpeg progress lines.
    for noisy in ("PIL", "moviepy", "proglog", "imageio", "imageio_ffmpeg"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    log.setLevel(logging.DEBUG if DEBUG else logging.INFO)


# ---------------------------------------------------------------------------
# File-type helpers
# ---------------------------------------------------------------------------

def _extension(path: str) -> str:
    """Return the lowercase extension (including the leading dot)."""
    return Path(path).suffix.lower()


def is_image(path: str) -> bool:
    """True if `path` looks like a still-image file."""
    return _extension(path) in IMAGE_EXTENSIONS


def is_video_file(path: str) -> bool:
    """True if `path` looks like an mp4/mov/etc."""
    return _extension(path) in VIDEO_EXTENSIONS


def is_gif_file(path: str) -> bool:
    """True if `path` ends in `.gif`."""
    return _extension(path) == GIF_EXTENSION


def is_animated(path: str) -> bool:
    """True if `path` is a video or gif (anything with frames over time)."""
    return is_video_file(path) or is_gif_file(path)



# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def _diagnose_missing(path: str, what: str = "file",
                      max_entries: int = 80) -> None:
    """Emit detailed error logs to help locate a missing file or directory.

    Logs: cwd, resolved/absolute paths, exists/is_file/is_dir flags, the
    parent dir's path/abs/exists, and a listing of the parent dir's contents
    (capped at `max_entries`).
    """
    target = Path(path)

    log.error("=" * 70)
    log.error("MISSING %s: %r", what, path)
    log.error("  cwd:        %s", os.getcwd())

    try:
        log.error("  resolved:   %s", target.resolve())
    except Exception as exc:                                   # noqa: BLE001
        log.error("  resolved:   <could not resolve: %s>", exc)

    log.error("  absolute:   %s", target.absolute())
    log.error("  exists():   %s", target.exists())
    log.error("  is_file():  %s", target.is_file())
    log.error("  is_dir():   %s", target.is_dir())

    parent = target.parent if str(target.parent) else Path(".")
    log.error("  parent:     %s", parent)
    log.error("  parent abs: %s", parent.absolute())
    log.error("  parent ex:  %s", parent.exists())

    if not parent.is_dir():
        log.error("  parent dir is missing too")
        log.error("=" * 70)
        return

    try:
        entries = sorted(os.listdir(parent))
    except OSError as exc:
        log.error("  could not list parent dir: %s", exc)
        log.error("=" * 70)
        return

    log.error("  parent contents (%d entries):", len(entries))
    for entry in entries[:max_entries]:
        full = parent / entry
        if full.is_dir():
            kind = "DIR "
        elif full.is_symlink():
            kind = "LINK"
        else:
            kind = "FILE"

        try:
            size = full.stat().st_size if full.is_file() else 0
            log.error("    [%s] %s  (%d bytes)", kind, entry, size)
        except OSError:
            log.error("    [%s] %s", kind, entry)

    if len(entries) > max_entries:
        log.error("    ... and %d more", len(entries) - max_entries)
    log.error("=" * 70)


# ---------------------------------------------------------------------------
# Background picker
# ---------------------------------------------------------------------------

def get_background(specific_path: Optional[str] = None) -> str:
    """Return a usable background path.

    If `specific_path` is given, it must exist; raises FileNotFoundError if
    not. Otherwise picks one randomly from `BACKGROUNDS_DIR`.
    """
    if specific_path is not None:
        if not os.path.exists(specific_path):
            _diagnose_missing(specific_path, "explicit background")
            raise FileNotFoundError(f"Background not found: {specific_path}")

        log.debug("Using explicit background: %s", specific_path)
        return specific_path

    if not os.path.isdir(BACKGROUNDS_DIR):
        _diagnose_missing(BACKGROUNDS_DIR, "backgrounds directory")
        raise FileNotFoundError(f"Backgrounds dir missing: {BACKGROUNDS_DIR}")

    all_entries = sorted(os.listdir(BACKGROUNDS_DIR))
    candidates = [
        os.path.join(BACKGROUNDS_DIR, name)
        for name in all_entries
        if not name.startswith(".") and (is_image(name) or is_animated(name))
    ]

    log.debug("Backgrounds dir %s has %d entries, %d usable",
              BACKGROUNDS_DIR, len(all_entries), len(candidates))

    if not candidates:
        log.error("No usable backgrounds in %s (cwd=%s)",
                  BACKGROUNDS_DIR, os.getcwd())
        log.error("  dir contents: %s", all_entries)
        log.error("  accepted extensions: img=%s vid=%s gif=%s",
                  IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, GIF_EXTENSION)
        raise FileNotFoundError(f"No usable backgrounds in {BACKGROUNDS_DIR}")

    chosen = random.choice(candidates)
    log.debug("Picked random background: %s", chosen)
    return chosen


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def percent_to_top_left(
    x_pct: float,
    y_pct: float,
    overlay_w: int,
    overlay_h: int,
    canvas_w: int = CANVAS_WIDTH,
    canvas_h: int = CANVAS_HEIGHT,
) -> Tuple[int, int]:
    """Convert center-anchored percentage coords (bottom-left origin) into
    PIL/moviepy top-left pixel coords (top-left origin).

    The percent coords act like a maths grid: (0,0) is bottom-left,
    (100,100) is top-right. We return the top-left pixel of the overlay so
    that the overlay's CENTRE lands on (x_pct, y_pct).
    """
    centre_x = (x_pct / 100.0) * canvas_w
    centre_y_from_bottom = (y_pct / 100.0) * canvas_h
    centre_y = canvas_h - centre_y_from_bottom

    top_left_x = int(round(centre_x - overlay_w / 2))
    top_left_y = int(round(centre_y - overlay_h / 2))
    return top_left_x, top_left_y


def fit_size_into_canvas(
    src_w: int,
    src_h: int,
    canvas_w: int = CANVAS_WIDTH,
    canvas_h: int = CANVAS_HEIGHT,
) -> Tuple[int, int, int, int]:
    """Compute the largest size that fits inside `canvas_w x canvas_h`
    while preserving aspect ratio, and the top-left offset to center it.

    Returns (fitted_w, fitted_h, offset_x, offset_y).
    """
    canvas_ratio = canvas_w / canvas_h
    src_ratio = src_w / src_h

    if src_ratio > canvas_ratio:
        # Source is wider than the canvas — limited by width
        fitted_w = canvas_w
        fitted_h = int(round(canvas_w / src_ratio))
    else:
        # Source is taller (or equal) — limited by height
        fitted_h = canvas_h
        fitted_w = int(round(canvas_h * src_ratio))

    offset_x = (canvas_w - fitted_w) // 2
    offset_y = (canvas_h - fitted_h) // 2
    return fitted_w, fitted_h, offset_x, offset_y


# ---------------------------------------------------------------------------
# PIL image transforms
# ---------------------------------------------------------------------------

def remove_background_with_rembg(pil_image: Image.Image) -> Image.Image:
    """Run rembg to strip a still image's background. Returns RGBA."""
    from rembg import remove
    return remove(pil_image.convert("RGBA"))


def scale_image_by_height_percent(
    pil_image: Image.Image,
    scale_pct: Any,
    canvas_h: int = CANVAS_HEIGHT,
) -> Image.Image:
    """Resize so the image's height equals `scale_pct`% of canvas height,
    preserving aspect ratio. Returns the original image unchanged for
    `"none"`, None, empty string, or invalid values.
    """
    if scale_pct in (None, "none", "None", "", False):
        return pil_image

    try:
        pct = float(scale_pct)
    except (TypeError, ValueError):
        log.warning("Invalid %s value: %r — leaving size unchanged",
                    KEY_SCALE_PCT, scale_pct)
        return pil_image

    if pct <= 0:
        return pil_image

    target_h = max(1, int(round((pct / 100.0) * canvas_h)))
    if target_h == pil_image.height:
        return pil_image

    ratio = target_h / pil_image.height
    target_w = max(1, int(round(pil_image.width * ratio)))
    return pil_image.resize((target_w, target_h), Image.LANCZOS)


def scale_image_to_fit_box(
    pil_image: Image.Image,
    box_pct: Any,
    canvas_h: int = CANVAS_HEIGHT,
) -> Image.Image:
    """Resize so the image's LONGEST side equals `box_pct`% of canvas height,
    preserving aspect ratio. Use for predictable row/grid layouts where
    wide images would otherwise overlap their neighbours.

    Returns the image unchanged for "none", None, "", or invalid values.
    """
    if box_pct in (None, "none", "None", "", False):
        return pil_image

    try:
        pct = float(box_pct)
    except (TypeError, ValueError):
        log.warning("Invalid %s value: %r — leaving size unchanged",
                    KEY_SCALE_BOX_PCT, box_pct)
        return pil_image

    if pct <= 0:
        return pil_image

    box_size = max(1, int(round((pct / 100.0) * canvas_h)))
    longest = max(pil_image.width, pil_image.height)
    if longest == box_size:
        return pil_image

    ratio = box_size / longest
    target_w = max(1, int(round(pil_image.width * ratio)))
    target_h = max(1, int(round(pil_image.height * ratio)))
    log.debug("box-fit %dx%d → %dx%d (box=%dpx)",
              pil_image.width, pil_image.height, target_w, target_h, box_size)
    return pil_image.resize((target_w, target_h), Image.LANCZOS)


def add_drop_shadow(
    pil_image: Image.Image,
    offset: Tuple[int, int] = (6, 10),
    blur_radius: int = 16,
    shadow_opacity: int = 130,
) -> Tuple[Image.Image, int]:
    """Add a soft drop shadow to an RGBA image.

    Returns `(padded_image, padding_pixels)` — the shadow extends past the
    original bounds, so the result is bigger than the input. The padding
    value is needed by the caller to keep the visible centre aligned.
    """
    pil_image = pil_image.convert("RGBA")

    pad = blur_radius + max(abs(offset[0]), abs(offset[1])) + 4
    canvas_w = pil_image.width + pad * 2
    canvas_h = pil_image.height + pad * 2

    out = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    # Build the shadow's alpha channel from the source's alpha
    source_alpha = pil_image.split()[-1]
    shadow_alpha = Image.new("L", out.size, 0)
    shadow_alpha.paste(source_alpha, (pad + offset[0], pad + offset[1]))
    shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(blur_radius))
    shadow_alpha = shadow_alpha.point(
        lambda value: int(value * shadow_opacity / 255)
    )

    shadow_layer = Image.new("RGBA", out.size, (0, 0, 0, 255))
    shadow_layer.putalpha(shadow_alpha)

    out.alpha_composite(shadow_layer)
    out.alpha_composite(pil_image, (pad, pad))
    return out, pad


def rotate_rgba(pil_image: Image.Image, degrees: float) -> Image.Image:
    """Rotate an RGBA image by `degrees` (counter-clockwise) and expand the
    canvas so nothing is clipped. Zero or near-zero is a no-op.
    """
    if abs(degrees) < 0.01:
        return pil_image
    return pil_image.convert("RGBA").rotate(
        degrees, resample=Image.BICUBIC, expand=True,
    )


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------

_FONT_CANDIDATES: List[str] = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def _load_font(size: int, override: Optional[str] = None) -> ImageFont.ImageFont:
    """Return a TTF font of the requested size, or a tiny fallback."""
    if override is not None:
        if os.path.exists(override):
            return ImageFont.truetype(override, size)
        log.warning("Font override missing: %s — falling back", override)

    for candidate in _FONT_CANDIDATES:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)

    log.warning("No TTF font found; using PIL default (will be tiny)")
    return ImageFont.load_default()


def _wrap_text_to_width(
    text: str,
    font: ImageFont.ImageFont,
    max_pixel_width: int,
) -> List[str]:
    """Greedy word-wrap. Returns a list of lines.

    Honours explicit "\n" line breaks in `text`. Words longer than the max
    width still occupy a line by themselves rather than being split.
    """
    measure_image = Image.new("RGBA", (10, 10))
    measure_draw = ImageDraw.Draw(measure_image)

    def measure_width(string: str) -> int:
        bbox = measure_draw.textbbox((0, 0), string, font=font)
        return bbox[2] - bbox[0]

    output_lines: List[str] = []

    for hard_line in text.split("\n"):
        words = hard_line.split(" ")
        current_line = ""

        for word in words:
            candidate = word if not current_line else f"{current_line} {word}"
            if measure_width(candidate) <= max_pixel_width or not current_line:
                current_line = candidate
            else:
                output_lines.append(current_line)
                current_line = word

        if current_line or not output_lines:
            output_lines.append(current_line)

    return output_lines


def render_text_image(
    text: str,
    size: int = 72,
    color: Tuple[int, int, int, int] = (255, 255, 255, 255),
    font_path: Optional[str] = None,
    max_width_pct: Optional[float] = None,
    canvas_w: int = CANVAS_WIDTH,
) -> Image.Image:
    """Render `text` into a transparent RGBA PIL image.

    If `max_width_pct` is given (1..100), the text wraps at that percentage
    of canvas width.
    """
    font = _load_font(size, font_path)

    # Decide wrap width
    if max_width_pct is None:
        max_pixel_width = 10 ** 9
    else:
        max_pixel_width = int((max_width_pct / 100.0) * canvas_w)

    lines = _wrap_text_to_width(text, font, max_pixel_width)

    # Measure each line and the total height
    measure_image = Image.new("RGBA", (10, 10))
    measure_draw = ImageDraw.Draw(measure_image)

    line_metrics: List[Tuple[int, int, int, int]] = []  # (w, h, x_off, y_off)
    line_spacing = max(2, size // 6)
    total_h = -line_spacing
    max_line_w = 0

    for line in lines:
        bbox = measure_draw.textbbox((0, 0), line, font=font)
        line_w = max(1, bbox[2] - bbox[0])
        line_h = max(1, bbox[3] - bbox[1])
        line_metrics.append((line_w, line_h, bbox[0], bbox[1]))
        max_line_w = max(max_line_w, line_w)
        total_h += line_h + line_spacing

    pad = max(8, size // 6)
    image = Image.new(
        "RGBA",
        (max_line_w + pad * 2, total_h + pad * 2),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(image)

    cursor_y = pad
    for line, (line_w, line_h, x_off, y_off) in zip(lines, line_metrics):
        # Centre each line horizontally
        line_x = pad + (max_line_w - line_w) // 2 - x_off
        draw.text((line_x, cursor_y - y_off), line, font=font, fill=tuple(color))
        cursor_y += line_h + line_spacing

    return image


# ---------------------------------------------------------------------------
# Overlay model
# ---------------------------------------------------------------------------

class Overlay:
    """A prepared overlay: either a static PIL layer or a moviepy clip,
    plus its top-left placement on the canvas and its intro transition.
    """

    KIND_IMAGE = "image"
    KIND_VIDEO = "video"

    def __init__(
        self,
        kind: str,
        content: Any,
        top_left_x: int,
        top_left_y: int,
        transition: str = TRANSITION_NONE,
    ):
        self.kind = kind
        self.content = content
        self.top_left_x = top_left_x
        self.top_left_y = top_left_y
        self.transition = transition or TRANSITION_NONE

    @property
    def width(self) -> int:
        if self.kind == Overlay.KIND_IMAGE:
            return self.content.width
        return self.content.w

    @property
    def height(self) -> int:
        if self.kind == Overlay.KIND_IMAGE:
            return self.content.height
        return self.content.h


# ---------------------------------------------------------------------------
# Overlay preparation
# ---------------------------------------------------------------------------

def _resolve_transition(value: Any) -> str:
    """Validate and normalise a transition spec from user JSON."""
    if value in (None, "", TRANSITION_NONE, "None", False):
        return TRANSITION_NONE

    if value == TRANSITION_RANDOM:
        return random.choice(TRANSITIONS)

    if value in TRANSITIONS:
        return value

    log.warning(
        "Unknown transition %r — using %r. Valid options: %s",
        value, TRANSITION_NONE, TRANSITIONS + [TRANSITION_RANDOM, TRANSITION_NONE],
    )
    return TRANSITION_NONE


def _prepare_text_overlay(item: Dict[str, Any], transition: str) -> Overlay:
    """Build an Overlay from a text-spec dict."""
    text = item[KEY_TEXT]
    size = int(item.get(KEY_TEXT_SIZE, 72))
    color = list(item.get(KEY_TEXT_COLOR, [255, 255, 255, 255]))
    if len(color) == 3:
        color.append(255)

    max_width_pct = item.get(KEY_TEXT_MAX_WIDTH)

    text_image = render_text_image(
        text=text,
        size=size,
        color=tuple(color),
        max_width_pct=max_width_pct,
    )

    rotation_degrees = float(item.get(KEY_ROTATION, 0))
    text_image = rotate_rgba(text_image, rotation_degrees)

    position = item.get(KEY_POSITION, [50, 50])
    x_pct, y_pct = float(position[0]), float(position[1])
    shadow_on = item.get(KEY_SHADOW, True)

    if not shadow_on:
        x, y = percent_to_top_left(x_pct, y_pct,
                                   text_image.width, text_image.height)
    else:
        text_image, pad = add_drop_shadow(
            text_image, offset=(4, 6), blur_radius=12, shadow_opacity=180,
        )
        x, y = percent_to_top_left(
            x_pct, y_pct,
            text_image.width - pad * 2, text_image.height - pad * 2,
        )
        x -= pad
        y -= pad

    log.debug("Text overlay %r at (%d,%d) %dx%d transition=%s",
              text, x, y, text_image.width, text_image.height, transition)
    return Overlay(Overlay.KIND_IMAGE, text_image, x, y, transition)


def _prepare_image_overlay(item: Dict[str, Any], path: str,
                           transition: str) -> Overlay:
    """Build an Overlay from a still-image-spec dict."""
    pil_image = Image.open(path).convert("RGBA")
    log.debug("Image overlay %s loaded as %dx%d",
              path, pil_image.width, pil_image.height)

    if item.get(KEY_REMOVE_BG, False):
        log.debug("Running rembg on %s", path)
        pil_image = remove_background_with_rembg(pil_image)

    # Box-fit takes precedence over height-only scaling — it's strictly
    # more constrained, so doing both doesn't make sense.
    if item.get(KEY_SCALE_BOX_PCT) not in (None, "none", "None", "", False):
        pil_image = scale_image_to_fit_box(
            pil_image, item.get(KEY_SCALE_BOX_PCT),
        )
    else:
        pil_image = scale_image_by_height_percent(
            pil_image, item.get(KEY_SCALE_PCT, "none"),
        )

    rotation_degrees = float(item.get(KEY_ROTATION, 0))
    pil_image = rotate_rgba(pil_image, rotation_degrees)

    position = item.get(KEY_POSITION, [50, 50])
    x_pct, y_pct = float(position[0]), float(position[1])
    shadow_on = item.get(KEY_SHADOW, True)

    if not shadow_on:
        x, y = percent_to_top_left(x_pct, y_pct,
                                   pil_image.width, pil_image.height)
    else:
        pil_image, pad = add_drop_shadow(pil_image)
        x, y = percent_to_top_left(
            x_pct, y_pct,
            pil_image.width - pad * 2, pil_image.height - pad * 2,
        )
        x -= pad
        y -= pad

    log.debug("Image overlay placed at (%d,%d) final size %dx%d transition=%s",
              x, y, pil_image.width, pil_image.height, transition)
    return Overlay(Overlay.KIND_IMAGE, pil_image, x, y, transition)


def _prepare_animated_overlay(item: Dict[str, Any], path: str,
                              transition: str) -> Overlay:
    """Build an Overlay from a video/gif-spec dict.

    Note: rotation is not supported for animated overlays (would need a
    per-frame transform). It's silently ignored with a warning.
    """
    log.debug("Loading animated overlay %s", path)
    clip = VideoFileClip(path, has_mask=is_gif_file(path))



    box_pct = item.get(KEY_SCALE_BOX_PCT)
    scale_pct = item.get(KEY_SCALE_PCT, "none")

    if box_pct not in (None, "none", "None", "", False):
        try:
            box_size = int((float(box_pct) / 100.0) * CANVAS_HEIGHT)
            if box_size > 0:
                # Cap whichever side is longer — same idea as the still-image version
                if clip.w >= clip.h:
                    clip = clip.resized(width=box_size)
                else:
                    clip = clip.resized(height=box_size)
                log.debug("box-fit animated overlay → %dx%d (box=%dpx)",
                          clip.w, clip.h, box_size)
        except (TypeError, ValueError):
            log.warning("Invalid %s value %r on %s — ignoring",
                        KEY_SCALE_BOX_PCT, box_pct, path)
    elif scale_pct not in (None, "none", "None", "", False):
        try:
            target_h = int((float(scale_pct) / 100.0) * CANVAS_HEIGHT)
            if target_h > 0:
                clip = clip.resized(height=target_h)
        except (TypeError, ValueError):
            log.warning("Invalid %s value %r on %s — ignoring",
                        KEY_SCALE_PCT, scale_pct, path)




    if item.get(KEY_REMOVE_BG, False):
        log.warning("removeBG ignored for animated overlay: %s", path)

    if abs(float(item.get(KEY_ROTATION, 0))) > 0.01:
        log.warning("rotation ignored for animated overlay: %s", path)

    position = item.get(KEY_POSITION, [50, 50])
    x_pct, y_pct = float(position[0]), float(position[1])
    x, y = percent_to_top_left(x_pct, y_pct, clip.w, clip.h)

    log.debug("Animated overlay placed at (%d,%d) size %dx%d duration=%.2fs "
              "transition=%s", x, y, clip.w, clip.h, clip.duration, transition)
    return Overlay(Overlay.KIND_VIDEO, clip, x, y, transition)


def prepare_overlay(item: Dict[str, Any]) -> Overlay:
    """Convert a JSON-like dict into an Overlay, dispatching on type.

    The dict is either a text overlay (has KEY_TEXT) or a file overlay
    (has KEY_PATH). Everything else is metadata.
    """
    transition = _resolve_transition(item.get(KEY_TRANSITION, TRANSITION_NONE))

    if item.get(KEY_TEXT):
        return _prepare_text_overlay(item, transition)

    if KEY_PATH not in item:
        log.error("Overlay item has neither %r nor %r: %r",
                  KEY_TEXT, KEY_PATH, item)
        raise KeyError(f"Overlay item must contain {KEY_TEXT!r} or {KEY_PATH!r}")

    path = item[KEY_PATH]
    if not os.path.exists(path):
        _diagnose_missing(path, "overlay")
        raise FileNotFoundError(f"Overlay path not found: {path}")

    if is_image(path):
        return _prepare_image_overlay(item, path, transition)
    return _prepare_animated_overlay(item, path, transition)


# ---------------------------------------------------------------------------
# Easing functions
# ---------------------------------------------------------------------------

def _ease_out_cubic(progress: float) -> float:
    """Smooth deceleration. progress in [0,1] -> output in [0,1]."""
    return 1 - (1 - progress) ** 3


def _ease_out_back(progress: float) -> float:
    """Overshoot easing — peaks above 1.0 then settles. Good for "pop"."""
    overshoot_strength = 1.70158
    overshoot_plus = overshoot_strength + 1
    return (
        1
        + overshoot_plus * (progress - 1) ** 3
        + overshoot_strength * (progress - 1) ** 2
    )


def _ease_out_bounce(progress: float) -> float:
    """Classic bounce easing — drops, bounces, settles."""
    base_factor = 7.5625
    divisor = 2.75

    if progress < 1 / divisor:
        return base_factor * progress * progress

    if progress < 2 / divisor:
        progress -= 1.5 / divisor
        return base_factor * progress * progress + 0.75

    if progress < 2.5 / divisor:
        progress -= 2.25 / divisor
        return base_factor * progress * progress + 0.9375

    progress -= 2.625 / divisor
    return base_factor * progress * progress + 0.984375


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def _scaling_transition(
    clip: Any,
    final_x: int,
    final_y: int,
    base_w: int,
    base_h: int,
    duration: float,
    scale_at: Callable[[float], float],
    with_fade: bool = False,
    fade_dur: Optional[float] = None,
) -> Any:
    """Build a clip whose size scales over `duration` seconds via
    `scale_at(progress)` (progress runs 0..1). Keeps the visible centre
    pinned to the centre of (final_x, final_y, base_w, base_h).
    """
    def resizer(time_s: float) -> Tuple[int, int]:
        if time_s >= duration:
            return (base_w, base_h)
        scale = max(0.01, scale_at(time_s / duration))
        return (max(2, int(base_w * scale)), max(2, int(base_h * scale)))

    def position(time_s: float) -> Tuple[float, float]:
        if time_s >= duration:
            return (final_x, final_y)
        scale = max(0.01, scale_at(time_s / duration))
        current_w = base_w * scale
        current_h = base_h * scale
        return (
            final_x + (base_w - current_w) / 2,
            final_y + (base_h - current_h) / 2,
        )

    out = clip.resized(resizer).with_position(position)
    if with_fade:
        out = out.with_effects([vfx.CrossFadeIn(fade_dur or duration)])
    return out


def apply_transition(
    clip: Any,
    overlay: Overlay,
    duration: float = TRANSITION_DURATION,
) -> Any:
    """Apply the chosen transition to `clip`, whose final resting position
    is `(overlay.top_left_x, overlay.top_left_y)`. Returns a new clip.
    """
    name = overlay.transition
    final_x = overlay.top_left_x
    final_y = overlay.top_left_y

    if name == TRANSITION_NONE:
        return clip.with_position((final_x, final_y))

    if name == TRANSITION_FADE:
        return (clip.with_position((final_x, final_y))
                    .with_effects([vfx.CrossFadeIn(duration)]))

    if name == TRANSITION_SLIDE_LEFT:        # comes in from right edge
        start_x = CANVAS_WIDTH

        def position(time_s: float) -> Tuple[float, float]:
            if time_s >= duration:
                return (final_x, final_y)
            progress = _ease_out_cubic(time_s / duration)
            return (start_x + (final_x - start_x) * progress, final_y)

        return clip.with_position(position)

    if name == TRANSITION_SLIDE_RIGHT:       # comes in from left edge
        start_x = -clip.w

        def position(time_s: float) -> Tuple[float, float]:
            if time_s >= duration:
                return (final_x, final_y)
            progress = _ease_out_cubic(time_s / duration)
            return (start_x + (final_x - start_x) * progress, final_y)

        return clip.with_position(position)

    if name == TRANSITION_SLIDE_UP:          # comes in from below
        start_y = CANVAS_HEIGHT

        def position(time_s: float) -> Tuple[float, float]:
            if time_s >= duration:
                return (final_x, final_y)
            progress = _ease_out_cubic(time_s / duration)
            return (final_x, start_y + (final_y - start_y) * progress)

        return clip.with_position(position)

    if name == TRANSITION_SLIDE_DOWN:        # comes in from above
        start_y = -clip.h

        def position(time_s: float) -> Tuple[float, float]:
            if time_s >= duration:
                return (final_x, final_y)
            progress = _ease_out_cubic(time_s / duration)
            return (final_x, start_y + (final_y - start_y) * progress)

        return clip.with_position(position)

    if name == TRANSITION_SLIDE_DIAG:        # comes in from bottom-left
        start_x = -clip.w
        start_y = CANVAS_HEIGHT

        def position(time_s: float) -> Tuple[float, float]:
            if time_s >= duration:
                return (final_x, final_y)
            progress = _ease_out_cubic(time_s / duration)
            return (
                start_x + (final_x - start_x) * progress,
                start_y + (final_y - start_y) * progress,
            )

        return clip.with_position(position)

    if name == TRANSITION_BOUNCE_IN:         # drops from above with bounce
        start_y = -clip.h

        def position(time_s: float) -> Tuple[float, float]:
            if time_s >= duration:
                return (final_x, final_y)
            progress = _ease_out_bounce(time_s / duration)
            return (final_x, start_y + (final_y - start_y) * progress)

        return clip.with_position(position)

    # ---- scale-based transitions ----
    base_w, base_h = clip.w, clip.h

    if name == TRANSITION_ZOOM_IN:
        return _scaling_transition(
            clip, final_x, final_y, base_w, base_h, duration,
            scale_at=lambda p: 0.6 + 0.4 * _ease_out_cubic(p),
            with_fade=True,
        )

    if name == TRANSITION_ZOOM_OUT:
        return _scaling_transition(
            clip, final_x, final_y, base_w, base_h, duration,
            scale_at=lambda p: 1.5 - 0.5 * _ease_out_cubic(p),
            with_fade=True,
        )

    if name == TRANSITION_POP:
        return _scaling_transition(
            clip, final_x, final_y, base_w, base_h, duration,
            scale_at=lambda p: max(0.0, _ease_out_back(p)),
            with_fade=True,
            fade_dur=duration * 0.4,
        )

    log.warning("Unhandled transition %r — falling back to static placement",
                name)
    return clip.with_position((final_x, final_y))


# ---------------------------------------------------------------------------
# Background loading + canvas fitting
# ---------------------------------------------------------------------------

def _fit_pil_to_canvas(
    background_pil: Image.Image,
    canvas_w: int = CANVAS_WIDTH,
    canvas_h: int = CANVAS_HEIGHT,
    fill_color: Tuple[int, int, int] = LETTERBOX_COLOR,
) -> Image.Image:
    """Return a `canvas_w x canvas_h` RGB image containing
    `background_pil` fitted in the largest aspect-correct size,
    letterboxed with `fill_color`.
    """
    if background_pil.size == (canvas_w, canvas_h):
        return background_pil.convert("RGB")

    fitted_w, fitted_h, off_x, off_y = fit_size_into_canvas(
        background_pil.width, background_pil.height, canvas_w, canvas_h,
    )

    resized = background_pil.convert("RGB").resize(
        (fitted_w, fitted_h), Image.LANCZOS,
    )
    canvas = Image.new("RGB", (canvas_w, canvas_h), fill_color)
    canvas.paste(resized, (off_x, off_y))
    return canvas


def _load_background_pil(background_path: str) -> Image.Image:
    """Load a still-image background and fit it into the canvas with
    letterboxing if its aspect ratio doesn't match.
    """
    raw = Image.open(background_path)
    if raw.size != (CANVAS_WIDTH, CANVAS_HEIGHT):
        log.debug("Letterboxing bg from %s to %dx%d (was %dx%d)",
                  background_path, CANVAS_WIDTH, CANVAS_HEIGHT,
                  raw.width, raw.height)
    return _fit_pil_to_canvas(raw)


def _fit_clip_to_canvas(
    clip: Any,
    canvas_w: int = CANVAS_WIDTH,
    canvas_h: int = CANVAS_HEIGHT,
) -> Any:
    """Return a CompositeVideoClip of `clip` fitted into the canvas with
    letterbox bars, preserving aspect ratio.
    """
    if (clip.w, clip.h) == (canvas_w, canvas_h):
        return clip

    fitted_w, fitted_h, off_x, off_y = fit_size_into_canvas(
        clip.w, clip.h, canvas_w, canvas_h,
    )
    resized = clip.resized((fitted_w, fitted_h)).with_position((off_x, off_y))
    return CompositeVideoClip(
        [resized],
        size=(canvas_w, canvas_h),
        bg_color=LETTERBOX_COLOR,
    ).with_duration(clip.duration)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_image_output(
    background_path: str,
    overlays: List[Overlay],
    output_folder: str,
    output_name: str,
) -> str:
    """Composite all overlays onto a still background, write a PNG."""
    canvas = _load_background_pil(background_path).convert("RGBA")

    for overlay in overlays:
        canvas.alpha_composite(
            overlay.content,
            (overlay.top_left_x, overlay.top_left_y),
        )

    output_path = os.path.join(output_folder, f"{output_name}.png")
    canvas.convert("RGB").save(output_path, "PNG")
    log.info("Wrote %s", output_path)
    return output_path


def _build_background_clip(
    background_path: str,
    duration: float,
    start_offset: float = 0.0,
) -> Any:
    """Build a moviepy clip for the background, fitted to canvas.

    `start_offset` lets the clip resume from a particular timestamp into
    the source — used to pick up after the intro stage so the loop video
    is continuous with where the intro left off.
    """
    if not is_animated(background_path):
        background_pil = _load_background_pil(background_path)
        return ImageClip(np.array(background_pil), duration=duration)

    raw_clip = VideoFileClip(
        background_path, has_mask=is_gif_file(background_path),
    ).without_audio()

    source_duration = raw_clip.duration

    if start_offset > 0 and source_duration > 0:
        # Wrap the offset around so we never seek past the source's end
        start_offset = start_offset % source_duration
        if start_offset > 0:
            raw_clip = raw_clip.subclipped(start_offset)

    if raw_clip.duration < duration:
        raw_clip = raw_clip.with_effects([vfx.Loop(duration=duration)])
    else:
        raw_clip = raw_clip.subclipped(0, duration)

    fitted = _fit_clip_to_canvas(raw_clip)
    return fitted.with_duration(duration)


def _overlay_to_clip(
    overlay: Overlay,
    duration: float,
    apply_intro_transition: bool,
    overlay_start_offset: float = 0.0,
) -> Any:
    """Convert an Overlay into a moviepy clip of the given duration.

    `overlay_start_offset` works like the background's start_offset and is
    used when emitting the loop stage so animated overlays continue from
    where they left off.
    """
    if overlay.kind == Overlay.KIND_IMAGE:
        clip = ImageClip(
            np.array(overlay.content), duration=duration, transparent=True,
        )
    else:
        source = overlay.content
        if overlay_start_offset > 0 and source.duration > 0:
            offset = overlay_start_offset % source.duration
            if offset > 0:
                source = source.subclipped(offset)

        if source.duration < duration:
            source = source.with_effects([vfx.Loop(duration=duration)])
        else:
            source = source.subclipped(0, duration)
        clip = source.with_duration(duration)

    if apply_intro_transition and overlay.transition != TRANSITION_NONE:
        return apply_transition(clip, overlay)
    return clip.with_position((overlay.top_left_x, overlay.top_left_y))


def render_video_output(
    background_path: str,
    overlays: List[Overlay],
    output_folder: str,
    output_name: str,
    intro_duration: float = DEFAULT_VIDEO_DURATION,
    loop_duration: float = DEFAULT_VIDEO_DURATION,
    transition_mask: Optional[List[bool]] = None,
) -> List[str]:
    """Render an mp4. `transition_mask` selects which overlays animate in
    (None = all of them).

    If any overlay actually animates, also emits a `_loop.mp4` companion.
    The intro stage is trimmed to just past the transition end, and the
    loop stage starts from that timestamp into the source media so the
    background and any animated overlays stay continuous.
    """
    if transition_mask is None:
        transition_mask = [True] * len(overlays)
    elif len(transition_mask) != len(overlays):
        raise ValueError(
            f"transition_mask length {len(transition_mask)} != "
            f"overlays length {len(overlays)}"
        )

    log.debug("render_video_output: %s | overlays=%d | mask=%s | "
              "intro=%.2fs loop=%.2fs",
              output_name, len(overlays), transition_mask,
              intro_duration, loop_duration)

    has_active_transition = any(
        overlay.transition != TRANSITION_NONE and apply
        for overlay, apply in zip(overlays, transition_mask)
    )

    # ---- intro stage ----
    # If there's a transition, trim the intro to just past the animation
    # end so the static dwell time isn't long. If there isn't, the intro
    # is the only output and runs the full requested length.
    if has_active_transition:
        intro_actual = TRANSITION_DURATION + INTRO_TAIL_PADDING
    else:
        intro_actual = intro_duration

    intro_bg = _build_background_clip(background_path, intro_actual)
    intro_layers = [intro_bg] + [
        _overlay_to_clip(overlay, intro_actual, apply_intro_transition=apply)
        for overlay, apply in zip(overlays, transition_mask)
    ]
    intro_clip = CompositeVideoClip(
        intro_layers, size=(CANVAS_WIDTH, CANVAS_HEIGHT),
    )
    intro_path = os.path.join(output_folder, f"{output_name}.mp4")
    log.info("Encoding %s ...", intro_path)
    intro_clip.write_videofile(
        intro_path, fps=DEFAULT_FPS, codec="libx264",
        audio=False, logger=None,
    )

    output_paths = [intro_path]

    if not has_active_transition:
        return output_paths

    # ---- loop stage ----
    # Resume the background and any animated overlays from where the intro
    # left off so the cut is seamless.
    loop_bg = _build_background_clip(
        background_path, loop_duration, start_offset=intro_actual,
    )
    loop_layers = [loop_bg] + [
        _overlay_to_clip(
            overlay, loop_duration,
            apply_intro_transition=False,
            overlay_start_offset=intro_actual,
        )
        for overlay in overlays
    ]
    loop_clip = CompositeVideoClip(
        loop_layers, size=(CANVAS_WIDTH, CANVAS_HEIGHT),
    )
    loop_path = os.path.join(output_folder, f"{output_name}_loop.mp4")
    log.info("Encoding %s ...", loop_path)
    loop_clip.write_videofile(
        loop_path, fps=DEFAULT_FPS, codec="libx264",
        audio=False, logger=None,
    )
    output_paths.append(loop_path)
    return output_paths


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def composite(
    items: List[Dict[str, Any]],
    output_folder: str,
    composite_flag: bool = False,
    background_path: Optional[str] = None,
    duration: float = DEFAULT_VIDEO_DURATION,
) -> List[str]:
    """
    Build composite outputs for a list of overlay specs.

    Parameters
    ----------
    items
        Ordered list of overlay descriptors. Each dict may contain:
            path                            path to png/jpg/gif/mp4
            text                            (alternative to path) string
            size, color, max-width-percentage   text-only options
            position                        [x_pct, y_pct] center-anchored
            removeBG                        bool (image overlays only)
            scale-page-height-percentage    "none" | 1..100
            rotation                        degrees (image/text only)
            transition                      see TRANSITIONS / "random" / "none"
            shadow                          bool, default True
    output_folder
        Where to write outputs (created if missing).
    composite_flag
        If True, emit one file per build-up stage. In this mode only the
        newest overlay in each stage plays its transition.
    background_path
        Optional explicit background; otherwise picked at random from
        BACKGROUNDS_DIR.
    duration
        Video duration (seconds) for the LOOP stage. The intro stage is
        trimmed to just past the transition end automatically.

    Returns
    -------
    list[str]
        Generated file paths in build order. With transitions, each stage
        contributes both `<name>.mp4` and `<name>_loop.mp4`.
    """
    os.makedirs(output_folder, exist_ok=True)

    chosen_background = get_background(background_path)
    log.info("Background: %s", chosen_background)

    prepared_overlays = [prepare_overlay(item) for item in items]

    has_video_background = is_animated(chosen_background)
    has_video_overlay = any(
        overlay.kind == Overlay.KIND_VIDEO for overlay in prepared_overlays
    )
    has_any_transition = any(
        overlay.transition != TRANSITION_NONE for overlay in prepared_overlays
    )
    output_is_video = (
        has_video_background or has_video_overlay or has_any_transition
    )

    log.info("Mode: video=%s (bg_video=%s, vid_ovl=%s, transitions=%s)",
             output_is_video, has_video_background, has_video_overlay,
             has_any_transition)

    if composite_flag:
        stages = [prepared_overlays[: i + 1]
                  for i in range(len(prepared_overlays))]
    else:
        stages = [prepared_overlays]

    output_paths: List[str] = []

    for index, stage in enumerate(stages):
        if composite_flag:
            stage_name = f"stage_{index + 1:02d}_of_{len(stages):02d}"
            # Only the newest overlay animates; older ones sit at final pos
            transition_mask = [False] * (len(stage) - 1) + [True]
        else:
            stage_name = "output"
            transition_mask = None

        log.info("[render] %s — %d overlay(s) — mask=%s",
                 stage_name, len(stage), transition_mask)

        if not output_is_video:
            output_paths.append(render_image_output(
                chosen_background, stage, output_folder, stage_name,
            ))
            continue

        output_paths.extend(render_video_output(
            background_path=chosen_background,
            overlays=stage,
            output_folder=output_folder,
            output_name=stage_name,
            intro_duration=duration,
            loop_duration=duration,
            transition_mask=transition_mask,
        ))

    return output_paths


# ---------------------------------------------------------------------------
# Layout templates
# ---------------------------------------------------------------------------
#
# Templates return a list of (x_pct, y_pct, optional scale_pct) tuples. Use
# them by zipping with your overlay items, e.g.:
#
#     positions = apply_template("triple_row", count=3)
#     for item, pos in zip(items, positions):
#         item["position"] = [pos[0], pos[1]]
#         if pos[2] is not None:
#             item["scale-page-height-percentage"] = pos[2]
#
# Each function takes a `count` and returns exactly `count` slots, raising
# ValueError if the count exceeds what the template supports.

TemplateSlot = Tuple[float, float, Optional[float]]  # (x%, y%, scale%)


def _template_centre_stage(count: int) -> List[TemplateSlot]:
    """Single big focus, optional caption above. Up to 2 slots:
    [hero centre] [caption near top]."""
    if count > 2:
        raise ValueError("centre_stage supports at most 2 slots")

    slots: List[TemplateSlot] = [(50.0, 50.0, 50.0)]
    if count >= 2:
        slots.append((50.0, 88.0, None))
    return slots[:count]


def _template_triple_row(count: int) -> List[TemplateSlot]:
    """Up to 3 evenly-spaced items in a horizontal row at vertical centre."""
    if count > 3:
        raise ValueError("triple_row supports at most 3 slots")

    base: List[TemplateSlot] = [
        (25.0, 50.0, 35.0),
        (50.0, 50.0, 35.0),
        (75.0, 50.0, 35.0),
    ]
    return base[:count]


def _template_quad_grid(count: int) -> List[TemplateSlot]:
    """2x2 grid. Up to 4 slots."""
    if count > 4:
        raise ValueError("quad_grid supports at most 4 slots")

    base: List[TemplateSlot] = [
        (30.0, 70.0, 35.0),
        (70.0, 70.0, 35.0),
        (30.0, 30.0, 35.0),
        (70.0, 30.0, 35.0),
    ]
    return base[:count]


def _template_left_focus(count: int) -> List[TemplateSlot]:
    """One large item on the left, captions/sub-items stacked on the right.
    Up to 4 slots: [left hero] [right top] [right mid] [right bottom]."""
    if count > 4:
        raise ValueError("left_focus supports at most 4 slots")

    base: List[TemplateSlot] = [
        (28.0, 50.0, 60.0),
        (72.0, 75.0, 22.0),
        (72.0, 50.0, 22.0),
        (72.0, 25.0, 22.0),
    ]
    return base[:count]


def _template_title_with_demo(count: int) -> List[TemplateSlot]:
    """Title at top, demo image/video below it. Up to 2 slots."""
    if count > 2:
        raise ValueError("title_with_demo supports at most 2 slots")

    slots: List[TemplateSlot] = [(50.0, 85.0, None)]      # title
    if count >= 2:
        slots.append((50.0, 40.0, 60.0))                   # demo
    return slots[:count]


def _template_versus(count: int) -> List[TemplateSlot]:
    """Two items facing off left vs right at the same height. Up to 3 slots
    (the optional 3rd is a "vs" caption between them)."""
    if count > 3:
        raise ValueError("versus supports at most 3 slots")

    slots: List[TemplateSlot] = [
        (25.0, 50.0, 50.0),
        (75.0, 50.0, 50.0),
    ]
    if count >= 3:
        slots.insert(1, (50.0, 50.0, None))
    return slots[:count]


TEMPLATES: Dict[str, Callable[[int], List[TemplateSlot]]] = {
    "centre_stage": _template_centre_stage,
    "triple_row": _template_triple_row,
    "quad_grid": _template_quad_grid,
    "left_focus": _template_left_focus,
    "title_with_demo": _template_title_with_demo,
    "versus": _template_versus,
}


def apply_template(template_name: str, count: int) -> List[TemplateSlot]:
    """Look up a layout template and return `count` slots from it."""
    if template_name not in TEMPLATES:
        raise KeyError(
            f"Unknown template {template_name!r}. "
            f"Available: {list(TEMPLATES)}"
        )
    return TEMPLATES[template_name](count)


# ---------------------------------------------------------------------------
# Test asset bootstrap (idempotent)
# ---------------------------------------------------------------------------

def _ensure_test_assets() -> List[str]:
    """Create a dummy background and three coloured-circle overlay images
    if they're not already present. Returns the overlay paths.
    """
    os.makedirs(BACKGROUNDS_DIR, exist_ok=True)

    bg_demo = os.path.join(BACKGROUNDS_DIR, "_demo_bg.jpg")
    if not os.path.exists(bg_demo):
        gradient = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH, 3), dtype=np.uint8)
        for y in range(CANVAS_HEIGHT):
            mix = y / CANVAS_HEIGHT
            gradient[y, :, 0] = int(20 + 60 * mix)
            gradient[y, :, 1] = int(30 + 90 * (1 - mix))
            gradient[y, :, 2] = int(80 + 120 * mix)
        Image.fromarray(gradient).save(bg_demo, quality=92)

    overlay_dir = "_TEST_IMAGES"
    os.makedirs(overlay_dir, exist_ok=True)

    palette = [(230, 80, 80), (80, 200, 120), (80, 130, 230)]
    paths: List[str] = []

    for index, color in enumerate(palette, start=1):
        path = os.path.join(overlay_dir, f"img{index}.png")
        if not os.path.exists(path):
            image = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse((50, 50, 550, 550), fill=color + (255,))
            font = _load_font(180)
            draw.text((260, 230), str(index), font=font,
                      fill=(255, 255, 255, 255))
            image.save(path)
        paths.append(path)

    return paths


# ---------------------------------------------------------------------------
# Self-test / manual runner
# ---------------------------------------------------------------------------

# if __name__ == "__main__":
    # import sys
# 
    # logging.basicConfig(
        # level=logging.DEBUG if DEBUG else logging.INFO,
        # format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        # handlers=[logging.StreamHandler(sys.stdout)],
    # )
    # configure_default_logging()
# 
    # test_image_paths = _ensure_test_assets()
# 
    # # Demo: 3 images in a row, composite mode -> only newest animates per stage
    # demo_items = [
        # {KEY_PATH: test_image_paths[0], KEY_POSITION: [25, 50],
         # KEY_SCALE_PCT: 35, KEY_TRANSITION: TRANSITION_RANDOM},
        # {KEY_PATH: test_image_paths[1], KEY_POSITION: [50, 50],
         # KEY_SCALE_PCT: 35, KEY_TRANSITION: TRANSITION_RANDOM},
        # {KEY_PATH: test_image_paths[2], KEY_POSITION: [75, 50],
         # KEY_SCALE_PCT: 35, KEY_TRANSITION: TRANSITION_RANDOM},
    # ]
# 
    # generated_files = composite(
        # items=demo_items,
        # output_folder="_OUTPUT",
        # composite_flag=True,
    # )
    # print("\nGenerated:")
    # for output_file in generated_files:
        # print("  ", output_file)


if __name__ == "__main__":
    import sys, shutil
    from pathlib import Path

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    configure_default_logging()

    # ---- locate your 4 test assets ----
    HERE = Path.cwd()
    IMG1 = str(next(HERE.rglob("img1.jpg")))
    IMG2 = str(next(HERE.rglob("img2.jpg")))
    GIF1 = str(next(HERE.rglob("img3.gif")))
    VID1 = str(next(HERE.rglob("img4.mp4")))

    # make sure we have at least one background
    Path("_BACKGROUNDS").mkdir(exist_ok=True)
    if not any(Path("_BACKGROUNDS").iterdir()):
        shutil.copy(IMG1, "_BACKGROUNDS/_auto_bg.jpg")

    OUT = Path("_OUTPUT/manual")
    OUT.mkdir(parents=True, exist_ok=True)

    def run_manual_tests():
        all_outputs = []

        # 1) PURE STATIC → should output PNG (no video, no transition)
        print("\n[TEST 1] static PNG, 3 images, no transitions")
        all_outputs += composite(
            items=[
                {"path": IMG1, "position": [25, 50], "scale-page-height-percentage": 35},
                {"path": IMG2, "position": [50, 50], "scale-page-height-percentage": 35},
                {"path": IMG1, "position": [75, 50], "scale-page-height-percentage": 35},
            ],
            output_folder=str(OUT/"01_static"),
            composite_flag=False,
        )

        # 2) REMBG + SCALE + ROTATION + SHADOW OFF
        print("\n[TEST 2] removeBG, rotation, no shadow")
        all_outputs += composite(
            items=[
                {"path": IMG2, "position": [50, 50], "scale-page-height-percentage": 45,
                 "removeBG": True, "rotation": 15, "shadow": False},
            ],
            output_folder=str(OUT/"02_rembg"),
            composite_flag=False,
        )

        # 3) TEXT OVERLAYS (no image path) → tests text rendering, wrap, color
        print("\n[TEST 3] text only")
        all_outputs += composite(
            items=[
                {"text": "CENTER TITLE", "position": [50, 85], "size": 110,
                 "color": [255,255,255,255]},
                {"text": "This is a long subtitle that should wrap because we set max-width-percentage",
                 "position": [50, 20], "size": 56, "color": [255,220,100,255],
                 "max-width-percentage": 70},
            ],
            output_folder=str(OUT/"03_text"),
            composite_flag=False,
        )

        # 4) VIDEO BACKGROUND → forces MP4 output even with static overlays
        print("\n[TEST 4] video background (mp4)")
        all_outputs += composite(
            items=[
                {"path": IMG1, "position": [50, 50], "scale-page-height-percentage": 40},
            ],
            output_folder=str(OUT/"04_vid_bg"),
            composite_flag=False,
            background_path=VID1,  # use your mp4 as bg
            duration=4.0,
        )

        # 5) ANIMATED OVERLAYS (gif + mp4) on image bg
        print("\n[TEST 5] gif + mp4 overlays")
        all_outputs += composite(
            items=[
                {"path": GIF1, "position": [30, 50], "scale-page-height-percentage": 40},
                {"path": VID1, "position": [70, 50], "scale-page-height-percentage": 40},
            ],
            output_folder=str(OUT/"05_animated"),
            composite_flag=False,
            duration=4.0,
        )

        # 6) COMPOSITE MODE + TRANSITIONS → only newest animates, creates _loop.mp4
        print("\n[TEST 6] composite_flag=True with transitions")
        all_outputs += composite(
            items=[
                {"path": IMG1, "position": [25, 50], "scale-page-height-percentage": 30,
                 "transition": TRANSITION_RANDOM},
                {"path": IMG2, "position": [50, 50], "scale-page-height-percentage": 30,
                 "transition": TRANSITION_RANDOM},
                {"path": IMG1, "position": [75, 50], "scale-page-height-percentage": 30,
                 "transition": TRANSITION_RANDOM},
            ],
            output_folder=str(OUT/"06_composite"),
            composite_flag=True,
            duration=3.0,
        )

        # 7) POSITIONING EXTREMES (tests 0,0 bottom-left and 100,100 top-right anchor)
        print("\n[TEST 7] positioning extremes")
        all_outputs += composite(
            items=[
                {"text": "0,0", "position": [0, 0], "size": 60},
                {"text": "100,100", "position": [100, 100], "size": 60},
                {"text": "0,100", "position": [0, 100], "size": 60},
                {"text": "100,0", "position": [100, 0], "size": 60},
                {"path": IMG2, "position": [50, 50], "scale-page-height-percentage": 20},
            ],
            output_folder=str(OUT/"07_positions"),
            composite_flag=False,
        )

        # 8) FULL MIX → everything at once (image+text+gif+video+rembg+rotation+transition)
        print("\n[TEST 8] full mix - validates loop continuity")
        all_outputs += composite(
            items=[
                {"text": "FULL MIX TEST", "position": [50, 90], "size": 90},
                {"path": IMG2, "position": [20, 55], "scale-page-height-percentage": 35,
                 "removeBG": True, "rotation": -10, "transition": "pop"},
                {"path": GIF1, "position": [50, 50], "scale-page-height-percentage": 38,
                 "transition": "zoom_in"},
                {"path": VID1, "position": [80, 45], "scale-page-height-percentage": 35,
                 "transition": "slide_left"},
            ],
            output_folder=str(OUT/"08_full"),
            composite_flag=True,
            background_path=VID1,
            duration=4.0,
        )

        print("\n=== DONE ===")
        for f in all_outputs:
            print(" ", f)
        return all_outputs

    run_manual_tests()
