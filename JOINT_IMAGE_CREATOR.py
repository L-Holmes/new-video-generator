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


import os
import logging
import random
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import numpy as np
from PIL import Image, ImageFilter, ImageDraw, ImageFont

from moviepy import (
    VideoFileClip, ImageClip, CompositeVideoClip, vfx,
)


# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
# Users of the module configure handlers themselves (see __main__ for example).

log = logging.getLogger("compositor")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BACKGROUNDS_DIR = "_BACKGROUNDS"

WIDTH = 1920
HEIGHT = 1080

DEFAULT_DURATION = 5.0          # seconds for video output
DEFAULT_FPS = 30
TRANSITION_DURATION = 0.6       # seconds

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
GIF_EXT = ".gif"

TRANSITIONS = [
    "fade",
    "slide_left",
    "slide_right",
    "slide_up",
    "slide_down",
    "slide_diag",
    "zoom_in",
    "zoom_out",
    "pop",
    "bounce_in",
]


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def _diagnose_missing(path: str, what: str = "file", max_entries: int = 80) -> None:
    """Emit detailed logs to help locate a missing file/dir."""
    p = Path(path)
    log.error("=" * 70)
    log.error("MISSING %s: %r", what, path)
    log.error("  cwd:         %s", os.getcwd())
    try:
        log.error("  resolved:    %s", p.resolve())
    except Exception as ex:                                    # noqa: BLE001
        log.error("  resolved:    <could not resolve: %s>", ex)
    log.error("  absolute:    %s", p.absolute())
    log.error("  exists():    %s", p.exists())
    log.error("  is_file():   %s", p.is_file())
    log.error("  is_dir():    %s", p.is_dir())

    parent = p.parent if str(p.parent) else Path(".")
    log.error("  parent dir:  %s", parent)
    log.error("  parent abs:  %s", parent.absolute())
    log.error("  parent ex:   %s", parent.exists())
    if parent.is_dir():
        try:
            entries = sorted(os.listdir(parent))
        except Exception as ex:                                # noqa: BLE001
            log.error("  could not list parent dir: %s", ex)
        else:
            log.error("  parent contents (%d entries):", len(entries))
            for e in entries[:max_entries]:
                full = parent / e
                if full.is_dir():
                    kind = "DIR "
                elif full.is_symlink():
                    kind = "LINK"
                else:
                    kind = "FILE"
                try:
                    size = full.stat().st_size if full.is_file() else 0
                    log.error("    [%s] %s  (%d bytes)", kind, e, size)
                except Exception:                              # noqa: BLE001
                    log.error("    [%s] %s", kind, e)
            if len(entries) > max_entries:
                log.error("    ... and %d more", len(entries) - max_entries)
    else:
        log.error("  parent dir is missing too")
    log.error("=" * 70)


# ---------------------------------------------------------------------------
# File-type helpers
# ---------------------------------------------------------------------------

def _ext(p: str) -> str:
    return Path(p).suffix.lower()

def is_image(p: str) -> bool:
    return _ext(p) in IMG_EXTS

def is_video_file(p: str) -> bool:
    return _ext(p) in VIDEO_EXTS

def is_gif_file(p: str) -> bool:
    return _ext(p) == GIF_EXT

def is_animated(p: str) -> bool:
    return is_video_file(p) or is_gif_file(p)


# ---------------------------------------------------------------------------
# Background picker
# ---------------------------------------------------------------------------

def get_background(specific_path: Optional[str] = None) -> str:
    """Return a usable background path. If `specific_path` is given, use it,
    else pick a random file from BACKGROUNDS_DIR."""
    if specific_path:
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
        os.path.join(BACKGROUNDS_DIR, f)
        for f in all_entries
        if not f.startswith(".")
        and (is_image(f) or is_animated(f))
    ]
    log.debug("Backgrounds dir %s has %d entries, %d usable",
             BACKGROUNDS_DIR, len(all_entries), len(candidates))
    if not candidates:
        log.error("No usable backgrounds in %s (cwd=%s)",
                  BACKGROUNDS_DIR, os.getcwd())
        log.error("  dir contents: %s", all_entries)
        log.error("  accepted extensions: %s | %s | %s",
                  IMG_EXTS, VIDEO_EXTS, GIF_EXT)
        raise FileNotFoundError(f"No usable backgrounds in {BACKGROUNDS_DIR}")
    chosen = random.choice(candidates)
    log.debug("Picked random background: %s", chosen)
    return chosen


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def calc_top_left(x_pct: float, y_pct: float, w: int, h: int,
                  canvas_w: int = WIDTH, canvas_h: int = HEIGHT
                  ) -> Tuple[int, int]:
    """Convert center-anchored percentage coords (bottom-left origin) to
    PIL/moviepy top-left pixel coords (top-left origin)."""
    cx = (x_pct / 100.0) * canvas_w
    cy_from_bottom = (y_pct / 100.0) * canvas_h
    cy = canvas_h - cy_from_bottom
    return int(round(cx - w / 2)), int(round(cy - h / 2))


# ---------------------------------------------------------------------------
# PIL image transforms
# ---------------------------------------------------------------------------

def remove_background_pil(pil_img: Image.Image) -> Image.Image:
    """Run rembg to strip the background. Returns RGBA."""
    from rembg import remove
    return remove(pil_img.convert("RGBA"))


def scale_pil(pil_img: Image.Image, scale_pct,
              canvas_h: int = HEIGHT) -> Image.Image:
    """Resize so height == scale_pct% of canvas height (preserving aspect)."""
    if scale_pct in (None, "none", "None", "", False):
        return pil_img
    try:
        pct = float(scale_pct)
    except (TypeError, ValueError):
        log.warning("Invalid scale-page-height-percentage value: %r — ignoring",
                    scale_pct)
        return pil_img
    if pct <= 0:
        return pil_img
    target_h = max(1, int(round((pct / 100.0) * canvas_h)))
    if target_h == pil_img.height:
        return pil_img
    ratio = target_h / pil_img.height
    new_w = max(1, int(round(pil_img.width * ratio)))
    return pil_img.resize((new_w, target_h), Image.LANCZOS)


def add_shadow_pil(pil_img: Image.Image,
                   offset: Tuple[int, int] = (6, 10),
                   blur: int = 16,
                   opacity: int = 130) -> Tuple[Image.Image, int]:
    """Add a soft drop shadow. Returns (padded_rgba_image, pad_pixels)."""
    pil_img = pil_img.convert("RGBA")
    pad = blur + max(abs(offset[0]), abs(offset[1])) + 4
    out = Image.new("RGBA",
                    (pil_img.width + pad * 2, pil_img.height + pad * 2),
                    (0, 0, 0, 0))
    alpha = pil_img.split()[-1]
    shadow_alpha = Image.new("L", out.size, 0)
    shadow_alpha.paste(alpha, (pad + offset[0], pad + offset[1]))
    shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(blur))
    shadow_alpha = shadow_alpha.point(lambda v: int(v * opacity / 255))
    shadow_layer = Image.new("RGBA", out.size, (0, 0, 0, 255))
    shadow_layer.putalpha(shadow_alpha)
    out.alpha_composite(shadow_layer)
    out.alpha_composite(pil_img, (pad, pad))
    return out, pad


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]

def _find_font(size: int, override: Optional[str] = None) -> ImageFont.ImageFont:
    if override:
        if os.path.exists(override):
            return ImageFont.truetype(override, size)
        log.warning("Font override missing: %s — falling back", override)
    for f in _FONT_CANDIDATES:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    log.warning("No TTF font found; using PIL default (will be tiny)")
    return ImageFont.load_default()


def make_text_pil(text: str, size: int = 72,
                  color: Tuple[int, int, int, int] = (255, 255, 255, 255),
                  font_path: Optional[str] = None) -> Image.Image:
    font = _find_font(size, font_path)
    tmp = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    tw = max(1, bbox[2] - bbox[0])
    th = max(1, bbox[3] - bbox[1])
    pad = max(8, size // 6)
    img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - bbox[0], pad - bbox[1]),
                              text, font=font, fill=tuple(color))
    return img


# ---------------------------------------------------------------------------
# Overlay preparation
# ---------------------------------------------------------------------------

class Overlay:
    """Holds either a static (PIL) layer or a video clip layer plus its placement."""
    def __init__(self, kind: str, content, x: int, y: int,
                 transition: str = "none"):
        self.kind = kind            # "image" or "video"
        self.content = content      # PIL.Image OR moviepy clip
        self.x = x
        self.y = y
        self.transition = transition or "none"

    @property
    def w(self) -> int:
        return self.content.width if self.kind == "image" else self.content.w

    @property
    def h(self) -> int:
        return self.content.height if self.kind == "image" else self.content.h


def _resolve_transition(t) -> str:
    if t in (None, "", "none", "None", False):
        return "none"
    if t == "random":
        return random.choice(TRANSITIONS)
    if t in TRANSITIONS:
        return t
    log.warning("Unknown transition %r — using 'none'. Valid: %s",
                t, TRANSITIONS + ["random", "none"])
    return "none"


def prepare_overlay(item: Dict[str, Any]) -> Overlay:
    """Convert a JSON-like dict into an Overlay."""
    transition = _resolve_transition(item.get("transition", "none"))
    pos = item.get("position", [50, 50])
    x_pct, y_pct = float(pos[0]), float(pos[1])
    shadow_on = item.get("shadow", True)

    # ----- text overlay -----
    if "text" in item and item["text"]:
        size = int(item.get("size", 72))
        color = item.get("color", [255, 255, 255, 255])
        if len(color) == 3:
            color = list(color) + [255]
        pil = make_text_pil(item["text"], size=size, color=tuple(color))
        if shadow_on:
            pil, pad = add_shadow_pil(pil, offset=(4, 6), blur=12, opacity=180)
            x, y = calc_top_left(x_pct, y_pct,
                                 pil.width - pad * 2, pil.height - pad * 2)
            x -= pad
            y -= pad
        else:
            x, y = calc_top_left(x_pct, y_pct, pil.width, pil.height)
        log.debug("Text overlay %r at (%d,%d) %dx%d transition=%s",
                  item["text"], x, y, pil.width, pil.height, transition)
        return Overlay("image", pil, x, y, transition)

    # ----- file-based overlay -----
    if "path" not in item:
        log.error("Overlay item has neither 'text' nor 'path': %r", item)
        raise KeyError("Overlay item must contain 'text' or 'path'")

    path = item["path"]
    if not os.path.exists(path):
        _diagnose_missing(path, "overlay")
        raise FileNotFoundError(f"Overlay path not found: {path}")

    scale_pct = item.get("scale-page-height-percentage", "none")

    if is_image(path):
        pil = Image.open(path).convert("RGBA")
        log.debug("Image overlay %s loaded as %dx%d", path, pil.width, pil.height)
        if item.get("removeBG", False):
            log.debug("Running rembg on %s", path)
            pil = remove_background_pil(pil)
        pil = scale_pil(pil, scale_pct)
        if shadow_on:
            pil, pad = add_shadow_pil(pil)
            x, y = calc_top_left(x_pct, y_pct,
                                 pil.width - pad * 2, pil.height - pad * 2)
            x -= pad
            y -= pad
        else:
            x, y = calc_top_left(x_pct, y_pct, pil.width, pil.height)
        log.debug("Image overlay placed at (%d,%d) final size %dx%d transition=%s",
                  x, y, pil.width, pil.height, transition)
        return Overlay("image", pil, x, y, transition)

    # animated (mp4 or gif)
    log.debug("Loading animated overlay %s", path)
    clip = VideoFileClip(path, has_mask=is_gif_file(path))
    if scale_pct not in (None, "none", "None", "", False):
        try:
            target_h = int((float(scale_pct) / 100.0) * HEIGHT)
            if target_h > 0:
                clip = clip.resized(height=target_h)
        except (TypeError, ValueError):
            log.warning("Invalid scale value %r on %s — ignoring",
                        scale_pct, path)
    if item.get("removeBG", False):
        log.warning("removeBG ignored for animated overlay: %s", path)
    x, y = calc_top_left(x_pct, y_pct, clip.w, clip.h)
    log.debug("Animated overlay placed at (%d,%d) size %dx%d duration=%.2fs "
              "transition=%s", x, y, clip.w, clip.h, clip.duration, transition)
    return Overlay("video", clip, x, y, transition)


# ---------------------------------------------------------------------------
# Easing functions
# ---------------------------------------------------------------------------

def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def _ease_out_back(t: float) -> float:
    """Overshoot easing — peaks above 1.0 then settles. Good for 'pop'."""
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _ease_out_bounce(t: float) -> float:
    """Classic bounce easing — drops, bounces, settles."""
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def apply_transition_to_clip(clip, ov: Overlay,
                             t_dur: float = TRANSITION_DURATION):
    """Apply the chosen transition to a clip whose 'rest' position is (ov.x, ov.y)."""
    name = ov.transition
    if name == "none":
        return clip.with_position((ov.x, ov.y))

    fx, fy = ov.x, ov.y

    # ---- pure position transitions ----
    if name == "fade":
        return (clip.with_position((fx, fy))
                    .with_effects([vfx.CrossFadeIn(t_dur)]))

    if name == "slide_left":      # comes in from right edge
        sx = WIDTH
        def pos(t, sx=sx, fx=fx, fy=fy, t_dur=t_dur):
            if t >= t_dur:
                return (fx, fy)
            p = _ease_out_cubic(t / t_dur)
            return (sx + (fx - sx) * p, fy)
        return clip.with_position(pos)

    if name == "slide_right":     # comes in from left edge
        sx = -clip.w
        def pos(t, sx=sx, fx=fx, fy=fy, t_dur=t_dur):
            if t >= t_dur:
                return (fx, fy)
            p = _ease_out_cubic(t / t_dur)
            return (sx + (fx - sx) * p, fy)
        return clip.with_position(pos)

    if name == "slide_up":        # comes in from below
        sy = HEIGHT
        def pos(t, sy=sy, fx=fx, fy=fy, t_dur=t_dur):
            if t >= t_dur:
                return (fx, fy)
            p = _ease_out_cubic(t / t_dur)
            return (fx, sy + (fy - sy) * p)
        return clip.with_position(pos)

    if name == "slide_down":      # comes in from above
        sy = -clip.h
        def pos(t, sy=sy, fx=fx, fy=fy, t_dur=t_dur):
            if t >= t_dur:
                return (fx, fy)
            p = _ease_out_cubic(t / t_dur)
            return (fx, sy + (fy - sy) * p)
        return clip.with_position(pos)

    if name == "slide_diag":      # comes in from bottom-left corner
        sx, sy = -clip.w, HEIGHT
        def pos(t, sx=sx, sy=sy, fx=fx, fy=fy, t_dur=t_dur):
            if t >= t_dur:
                return (fx, fy)
            p = _ease_out_cubic(t / t_dur)
            return (sx + (fx - sx) * p, sy + (fy - sy) * p)
        return clip.with_position(pos)

    if name == "bounce_in":       # drops from above with bounce
        sy = -clip.h
        def pos(t, sy=sy, fx=fx, fy=fy, t_dur=t_dur):
            if t >= t_dur:
                return (fx, fy)
            p = _ease_out_bounce(t / t_dur)
            return (fx, sy + (fy - sy) * p)
        return clip.with_position(pos)

    # ---- scaling transitions (need both resizer and centered position) ----
    base_w, base_h = clip.w, clip.h

    if name == "zoom_in":         # 0.6x -> 1.0x with fade
        def scale_at(p): return 0.6 + 0.4 * _ease_out_cubic(p)
        return _scaling_transition(clip, fx, fy, base_w, base_h, t_dur,
                                   scale_at, with_fade=True)

    if name == "zoom_out":        # 1.5x -> 1.0x with fade
        def scale_at(p): return 1.5 - 0.5 * _ease_out_cubic(p)
        return _scaling_transition(clip, fx, fy, base_w, base_h, t_dur,
                                   scale_at, with_fade=True)

    if name == "pop":             # 0 -> 1.0 with overshoot, fade
        def scale_at(p): return max(0.0, _ease_out_back(p))
        return _scaling_transition(clip, fx, fy, base_w, base_h, t_dur,
                                   scale_at, with_fade=True,
                                   fade_dur=t_dur * 0.4)

    log.warning("Unhandled transition %r — falling back to static placement", name)
    return clip.with_position((fx, fy))


def _scaling_transition(clip, fx, fy, base_w, base_h, t_dur, scale_at,
                        with_fade: bool = False,
                        fade_dur: Optional[float] = None):
    """Build a clip that scales over `t_dur` seconds via `scale_at(p)` (0..1).
    Keeps the visible center pinned to (fx + base_w/2, fy + base_h/2)."""
    def resizer(t, base_w=base_w, base_h=base_h, t_dur=t_dur, scale_at=scale_at):
        if t >= t_dur:
            return (base_w, base_h)
        s = max(0.01, scale_at(t / t_dur))
        return (max(2, int(base_w * s)), max(2, int(base_h * s)))

    def pos(t, fx=fx, fy=fy, base_w=base_w, base_h=base_h,
            t_dur=t_dur, scale_at=scale_at):
        if t >= t_dur:
            return (fx, fy)
        s = max(0.01, scale_at(t / t_dur))
        cur_w = base_w * s
        cur_h = base_h * s
        return (fx + (base_w - cur_w) / 2, fy + (base_h - cur_h) / 2)

    out = clip.resized(resizer).with_position(pos)
    if with_fade:
        out = out.with_effects([vfx.CrossFadeIn(fade_dur or t_dur)])
    return out


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _load_bg_pil(bg_path: str) -> Image.Image:
    bg = Image.open(bg_path).convert("RGB")
    if bg.size != (WIDTH, HEIGHT):
        log.debug("Resizing bg from %s to %dx%d", bg.size, WIDTH, HEIGHT)
        bg = bg.resize((WIDTH, HEIGHT), Image.LANCZOS)
    return bg


def render_image(bg_path: str, overlays: List[Overlay],
                 out_folder: str, name: str) -> str:
    canvas = _load_bg_pil(bg_path).convert("RGBA")
    for ov in overlays:
        canvas.alpha_composite(ov.content, (ov.x, ov.y))
    out_path = os.path.join(out_folder, f"{name}.png")
    canvas.convert("RGB").save(out_path, "PNG")
    log.info("Wrote %s", out_path)
    return out_path


def _bg_clip(bg_path: str, duration: float):
    if is_animated(bg_path):
        c = VideoFileClip(bg_path, has_mask=is_gif_file(bg_path)).without_audio()
        if c.duration < duration:
            c = c.with_effects([vfx.Loop(duration=duration)])
        else:
            c = c.subclipped(0, duration)
        if (c.w, c.h) != (WIDTH, HEIGHT):
            c = c.resized((WIDTH, HEIGHT))
        return c.with_duration(duration)
    pil = _load_bg_pil(bg_path)
    return ImageClip(np.array(pil), duration=duration)


def _overlay_to_clip(ov: Overlay, duration: float, with_transition: bool):
    if ov.kind == "image":
        clip = ImageClip(np.array(ov.content), duration=duration, transparent=True)
    else:
        sub = ov.content
        if sub.duration < duration:
            sub = sub.with_effects([vfx.Loop(duration=duration)])
        else:
            sub = sub.subclipped(0, duration)
        clip = sub.with_duration(duration)

    if with_transition and ov.transition != "none":
        clip = apply_transition_to_clip(clip, ov)
    else:
        clip = clip.with_position((ov.x, ov.y))
    return clip


def render_video(bg_path: str, overlays: List[Overlay],
                 out_folder: str, name: str,
                 duration: float = DEFAULT_DURATION,
                 transition_mask: Optional[List[bool]] = None) -> List[str]:
    """Render an mp4. `transition_mask` selects which overlays animate in.
    None means all of them. If any overlay actually animates, ALSO emit a
    `_loop.mp4` companion with everything at its final position."""
    if transition_mask is None:
        transition_mask = [True] * len(overlays)
    elif len(transition_mask) != len(overlays):
        raise ValueError(
            f"transition_mask length {len(transition_mask)} != "
            f"overlays length {len(overlays)}"
        )

    log.debug("render_video: %s | overlays=%d | mask=%s",
              name, len(overlays), transition_mask)

    bg = _bg_clip(bg_path, duration)
    layers_main = [bg] + [
        _overlay_to_clip(ov, duration, with_transition=m)
        for ov, m in zip(overlays, transition_mask)
    ]
    main_clip = CompositeVideoClip(layers_main, size=(WIDTH, HEIGHT))
    main_path = os.path.join(out_folder, f"{name}.mp4")
    log.info("Encoding %s ...", main_path)
    main_clip.write_videofile(main_path, fps=DEFAULT_FPS, codec="libx264",
                              audio=False, logger=None)

    out_files = [main_path]

    any_active_transition = any(
        ov.transition != "none" and m
        for ov, m in zip(overlays, transition_mask)
    )
    if any_active_transition:
        bg2 = _bg_clip(bg_path, duration)
        layers_loop = [bg2] + [
            _overlay_to_clip(ov, duration, with_transition=False)
            for ov in overlays
        ]
        loop_clip = CompositeVideoClip(layers_loop, size=(WIDTH, HEIGHT))
        loop_path = os.path.join(out_folder, f"{name}_loop.mp4")
        log.info("Encoding %s ...", loop_path)
        loop_clip.write_videofile(loop_path, fps=DEFAULT_FPS, codec="libx264",
                                  audio=False, logger=None)
        out_files.append(loop_path)

    return out_files


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def composite(items: List[Dict[str, Any]],
              output_folder: str,
              composite_flag: bool = False,
              background_path: Optional[str] = None,
              duration: float = DEFAULT_DURATION) -> List[str]:
    """
    items : ordered list of overlay descriptors. Each dict may contain:
        path                            path to png/jpg/gif/mp4
        text                            (alternative to path) string to render
        size, color                     text-only options
        position                        [x_pct, y_pct]   center-anchored
        removeBG                        bool             (image overlays only)
        scale-page-height-percentage    "none" | 1..100
        transition                      "none" | "fade" | "slide_left" |
                                        "slide_right" | "slide_up" |
                                        "slide_down" | "slide_diag" |
                                        "zoom_in" | "zoom_out" | "pop" |
                                        "bounce_in" | "random"
        shadow                          bool             default True

    output_folder    : where to write outputs (created if missing)
    composite_flag   : if True, emit one file per build-up stage. In this mode
                       only the newest overlay in each stage plays its
                       transition; older overlays are placed in their final
                       position so they don't replay their intro.
    background_path  : optional explicit bg; otherwise random from _BACKGROUNDS/
    duration         : video duration in seconds (only used for video output)

    Returns: list of generated file paths, in build order.
    """
    os.makedirs(output_folder, exist_ok=True)
    bg_path = get_background(background_path)
    log.info("Background: %s", bg_path)

    prepared = [prepare_overlay(it) for it in items]

    bg_is_video = is_animated(bg_path)
    has_video_overlay = any(ov.kind == "video" for ov in prepared)
    has_transition = any(ov.transition != "none" for ov in prepared)
    output_is_video = bg_is_video or has_video_overlay or has_transition

    log.info("Mode: video=%s (bg_video=%s, vid_ovl=%s, transitions=%s)",
             output_is_video, bg_is_video, has_video_overlay, has_transition)

    stages: List[List[Overlay]] = (
        [prepared[: i + 1] for i in range(len(prepared))]
        if composite_flag else
        [prepared]
    )

    out_files: List[str] = []
    for idx, stage in enumerate(stages):
        if composite_flag:
            stage_name = f"stage_{idx + 1:02d}_of_{len(stages):02d}"
            # Only the newest (last) overlay animates in. Earlier overlays
            # are pinned to final position so we don't replay older intros.
            transition_mask = [False] * (len(stage) - 1) + [True]
        else:
            stage_name = "output"
            transition_mask = None

        log.info("[render] %s — %d overlay(s) — mask=%s",
                 stage_name, len(stage), transition_mask)

        if output_is_video:
            out_files.extend(render_video(bg_path, stage, output_folder,
                                          stage_name, duration=duration,
                                          transition_mask=transition_mask))
        else:
            out_files.append(render_image(bg_path, stage, output_folder,
                                          stage_name))

    return out_files


# ---------------------------------------------------------------------------
# Test asset bootstrap (idempotent)
# ---------------------------------------------------------------------------

def _ensure_test_assets():
    """Create dummy backgrounds and overlay images so the demo can run
    standalone."""
    os.makedirs(BACKGROUNDS_DIR, exist_ok=True)

    bg_demo = os.path.join(BACKGROUNDS_DIR, "_demo_bg.jpg")
    if not os.path.exists(bg_demo):
        arr = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        for y in range(HEIGHT):
            t = y / HEIGHT
            arr[y, :, 0] = int(20 + 60 * t)
            arr[y, :, 1] = int(30 + 90 * (1 - t))
            arr[y, :, 2] = int(80 + 120 * t)
        Image.fromarray(arr).save(bg_demo, quality=92)

    test_dir = "_TEST_IMAGES"
    os.makedirs(test_dir, exist_ok=True)
    paths = []
    palette = [(230, 80, 80), (80, 200, 120), (80, 130, 230)]
    for i, color in enumerate(palette, 1):
        p = os.path.join(test_dir, f"img{i}.png")
        if not os.path.exists(p):
            img = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.ellipse((50, 50, 550, 550), fill=color + (255,))
            font = _find_font(180)
            d.text((260, 230), str(i), font=font, fill=(255, 255, 255, 255))
            img.save(p)
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Self-test / manual runner
# ---------------------------------------------------------------------------

# if __name__ == "__main__":
    # import sys
# 
    # logging.basicConfig(
        # level=logging.DEBUG,
        # format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        # handlers=[logging.StreamHandler(sys.stdout)],
    # )
# 
    # test_imgs = _ensure_test_assets()
# 
    # # Demo: 3 images in a row, composite mode -> only newest animates per stage
    # items = [
        # {"path": test_imgs[0], "position": [25, 50],
         # "scale-page-height-percentage": 35, "transition": "random"},
        # {"path": test_imgs[1], "position": [50, 50],
         # "scale-page-height-percentage": 35, "transition": "random"},
        # {"path": test_imgs[2], "position": [75, 50],
         # "scale-page-height-percentage": 35, "transition": "random"},
    # ]
# 
    # out = composite(items, "_OUTPUT", composite_flag=True)
    # print("\nGenerated:")
    # for f in out:
        # print("  ", f)

if __name__ == "__main__":
    import logging, sys
    from pathlib import Path

    # ---------- logging ----------
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    log = logging.getLogger("compositor")

    def run_manual_tests():
        HERE = Path(__file__).parent.resolve()
        IMG_DIR = HERE / "_TEST_IMAGES"
        IMG1 = str(IMG_DIR / "img1.jpg")
        IMG2 = str(IMG_DIR / "img2.jpg")
        GIF1 = str(IMG_DIR / "img3.gif")
        VID1 = str(IMG_DIR / "img4.mp4")

        # sanity check
        for p in [IMG1, IMG2, GIF1, VID1]:
            log.info(f"Test asset: {p} exists={Path(p).exists()}")

        base = HERE / "_OUTPUT" / "manual_tests"
        base.mkdir(parents=True, exist_ok=True)
        all_outputs = []

        # 1) Baseline PNG
        log.info("TEST 1: static PNG composite")
        all_outputs += composite(
            items=[
                {"path": IMG1, "position": [25, 50], "scale-page-height-percentage": 30, "transition": "none"},
                {"path": IMG2, "position": [50, 50], "scale-page-height-percentage": 30, "transition": "none"},
                {"path": IMG1, "position": [75, 50], "scale-page-height-percentage": 30, "transition": "none"},
            ],
            output_folder=str(base / "01_static_png"),
            composite_flag=True,
        )

        # 2) removeBG
        log.info("TEST 2: removeBG")
        all_outputs += composite(
            items=[
                {"path": IMG1, "position": [50, 50], "removeBG": True, "scale-page-height-percentage": 40},
            ],
            output_folder=str(base / "02_removebg"),
        )

        # 3) Text overlay
        log.info("TEST 3: text")
        all_outputs += composite(
            items=[
                {"text": "EXPLAINER TEST", "position": [50, 90], "size": 96, "color": [255, 220, 0, 255]},
                {"path": IMG2, "position": [50, 45], "scale-page-height-percentage": 35},
            ],
            output_folder=str(base / "03_text"),
        )

        # 4) Corners + shadow off
        log.info("TEST 4: corners")
        all_outputs += composite(
            items=[
                {"path": IMG1, "position": [0, 0], "scale-page-height-percentage": 20, "shadow": False},
                {"path": IMG2, "position": [100, 100], "scale-page-height-percentage": 20, "shadow": False},
                {"path": IMG1, "position": [0, 100], "scale-page-height-percentage": 20},
                {"path": IMG2, "position": [100, 0], "scale-page-height-percentage": 20},
            ],
            output_folder=str(base / "04_corners"),
            composite_flag=True,
        )

        # 5-9) All transitions
        for trans in ["fade", "slide_left", "slide_right", "slide_up", "zoom_in"]:
            log.info(f"TEST 5: transition {trans}")
            all_outputs += composite(
                items=[{"path": IMG1, "position": [50, 50], "scale-page-height-percentage": 35, "transition": trans}],
                output_folder=str(base / f"05_trans_{trans}"),
                duration=4.0,
            )

        # 10) Random
        log.info("TEST 10: random")
        all_outputs += composite(
            items=[{"path": IMG2, "position": [50, 50], "scale-page-height-percentage": 35, "transition": "random"}],
            output_folder=str(base / "06_random"),
        )

        # 11) Video background
        log.info("TEST 11: video bg")
        all_outputs += composite(
            items=[
                {"path": IMG1, "position": [30, 50], "scale-page-height-percentage": 25},
                {"path": IMG2, "position": [70, 50], "scale-page-height-percentage": 25},
            ],
            output_folder=str(base / "07_video_bg"),
            composite_flag=True,
            background_path=VID1,
            duration=5.0,
        )

        # 12) GIF background
        log.info("TEST 12: gif bg")
        all_outputs += composite(
            items=[{"text": "GIF BG TEST", "position": [50, 85], "size": 72}],
            output_folder=str(base / "08_gif_bg"),
            background_path=GIF1,
            duration=4.0,
        )

        # 13) Animated GIF overlay
        log.info("TEST 13: gif overlay")
        all_outputs += composite(
            items=[{"path": GIF1, "position": [50, 50], "scale-page-height-percentage": 50}],
            output_folder=str(base / "09_animated_gif"),
            duration=5.0,
        )

        # 14) Animated MP4 overlay
        log.info("TEST 14: mp4 overlay")
        all_outputs += composite(
            items=[{"path": VID1, "position": [50, 50], "scale-page-height-percentage": 45}],
            output_folder=str(base / "10_animated_mp4"),
            duration=5.0,
        )

        # 15) Full mix
        log.info("TEST 15: full mix")
        all_outputs += composite(
            items=[
                {"path": IMG1, "position": [20, 30], "removeBG": True, "scale-page-height-percentage": 30, "transition": "slide_right"},
                {"path": GIF1, "position": [50, 50], "scale-page-height-percentage": 35, "transition": "zoom_in"},
                {"path": VID1, "position": [80, 30], "scale-page-height-percentage": 30, "transition": "fade"},
                {"text": "FINAL MIX", "position": [50, 85], "size": 84, "color": [255,255,255,255], "transition": "slide_up"},
            ],
            output_folder=str(base / "11_full_mix"),
            composite_flag=True,
            background_path=VID1,
            duration=6.0,
        )

        log.info("=== ALL TESTS DONE ===")
        for f in all_outputs:
            log.info(f"  {f}")
        return all_outputs

    run_manual_tests()
