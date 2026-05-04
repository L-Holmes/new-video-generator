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
- Transitions: fade / slide_left / slide_right / slide_up / zoom_in / random / none.
  When a transition is requested but everything is static, output is forced to MP4.
- Composite mode: emits one file per build-up stage (bg+1, bg+1+2, ...).
- When a transition is used, ALSO emits a `_loop.mp4` companion that is the same
  composition without the intro animation, so it can be looped seamlessly after
  the intro.

Dependencies
------------
    pip install moviepy pillow rembg numpy

Author note: targets moviepy >= 2.0 API (`with_position`, `with_effects`, etc.).




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

from __future__ import annotations

import os
import random
import math
from pathlib import Path
from typing import Optional, Union, List, Dict, Any, Tuple

import numpy as np
from PIL import Image, ImageFilter, ImageDraw, ImageFont

from moviepy import (
    VideoFileClip, ImageClip, CompositeVideoClip, vfx,
)


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

TRANSITIONS = ["fade", "slide_left", "slide_right", "slide_up", "zoom_in"]


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
            raise FileNotFoundError(f"Background not found: {specific_path}")
        return specific_path
    if not os.path.isdir(BACKGROUNDS_DIR):
        raise FileNotFoundError(f"Backgrounds dir missing: {BACKGROUNDS_DIR}")
    candidates = [
        os.path.join(BACKGROUNDS_DIR, f)
        for f in os.listdir(BACKGROUNDS_DIR)
        if not f.startswith(".")
        and (is_image(f) or is_animated(f))
    ]
    if not candidates:
        raise FileNotFoundError(f"No usable backgrounds in {BACKGROUNDS_DIR}")
    return random.choice(candidates)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def calc_top_left(x_pct: float, y_pct: float, w: int, h: int,
                  canvas_w: int = WIDTH, canvas_h: int = HEIGHT) -> Tuple[int, int]:
    """Convert center-anchored percentage coords (bottom-left origin) to PIL/moviepy
    top-left pixel coords (top-left origin)."""
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
    if override and os.path.exists(override):
        return ImageFont.truetype(override, size)
    for f in _FONT_CANDIDATES:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
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
        return Overlay("image", pil, x, y, transition)

    # ----- file-based overlay -----
    path = item["path"]
    if not os.path.exists(path):
        raise FileNotFoundError(f"Overlay path not found: {path}")

    scale_pct = item.get("scale-page-height-percentage", "none")

    if is_image(path):
        pil = Image.open(path).convert("RGBA")
        if item.get("removeBG", False):
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
        return Overlay("image", pil, x, y, transition)

    # animated (mp4 or gif)
    clip = VideoFileClip(path, has_mask=is_gif_file(path))
    if scale_pct not in (None, "none", "None", "", False):
        try:
            target_h = int((float(scale_pct) / 100.0) * HEIGHT)
            if target_h > 0:
                clip = clip.resized(height=target_h)
        except (TypeError, ValueError):
            pass
    if item.get("removeBG", False):
        print(f"[warn] removeBG ignored for animated overlay: {path}")
    x, y = calc_top_left(x_pct, y_pct, clip.w, clip.h)
    return Overlay("video", clip, x, y, transition)


# ---------------------------------------------------------------------------
# Transitions (animate position / opacity over the first N seconds)
# ---------------------------------------------------------------------------

def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def apply_transition_to_clip(clip, ov: Overlay, t_dur: float = TRANSITION_DURATION):
    """Apply the chosen transition to a clip whose 'rest' position is (ov.x, ov.y)."""
    name = ov.transition
    if name == "none":
        return clip.with_position((ov.x, ov.y))

    fx, fy = ov.x, ov.y

    if name == "fade":
        return clip.with_position((fx, fy)).with_effects([vfx.CrossFadeIn(t_dur)])

    if name == "slide_left":
        sx = WIDTH                     # come in from right edge
        def pos(t, sx=sx, fx=fx, fy=fy, t_dur=t_dur):
            if t >= t_dur:
                return (fx, fy)
            p = _ease_out_cubic(t / t_dur)
            return (sx + (fx - sx) * p, fy)
        return clip.with_position(pos)

    if name == "slide_right":
        sx = -clip.w                   # come in from left edge
        def pos(t, sx=sx, fx=fx, fy=fy, t_dur=t_dur):
            if t >= t_dur:
                return (fx, fy)
            p = _ease_out_cubic(t / t_dur)
            return (sx + (fx - sx) * p, fy)
        return clip.with_position(pos)

    if name == "slide_up":
        sy = HEIGHT                    # come in from below
        def pos(t, sy=sy, fx=fx, fy=fy, t_dur=t_dur):
            if t >= t_dur:
                return (fx, fy)
            p = _ease_out_cubic(t / t_dur)
            return (fx, sy + (fy - sy) * p)
        return clip.with_position(pos)

    if name == "zoom_in":
        # combine crossfade with a small scale-up
        base_w, base_h = clip.w, clip.h
        def resizer(t, base_w=base_w, base_h=base_h, t_dur=t_dur):
            if t >= t_dur:
                return (base_w, base_h)
            p = _ease_out_cubic(t / t_dur)
            scale = 0.6 + 0.4 * p
            return (max(2, int(base_w * scale)), max(2, int(base_h * scale)))
        # keep centered while zooming
        def pos(t, fx=fx, fy=fy, base_w=base_w, base_h=base_h, t_dur=t_dur):
            if t >= t_dur:
                return (fx, fy)
            p = _ease_out_cubic(t / t_dur)
            scale = 0.6 + 0.4 * p
            cur_w = base_w * scale
            cur_h = base_h * scale
            return (fx + (base_w - cur_w) / 2, fy + (base_h - cur_h) / 2)
        return (clip
                .resized(resizer)
                .with_position(pos)
                .with_effects([vfx.CrossFadeIn(t_dur)]))

    return clip.with_position((fx, fy))


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _load_bg_pil(bg_path: str) -> Image.Image:
    bg = Image.open(bg_path).convert("RGB")
    if bg.size != (WIDTH, HEIGHT):
        bg = bg.resize((WIDTH, HEIGHT), Image.LANCZOS)
    return bg


def render_image(bg_path: str, overlays: List[Overlay],
                 out_folder: str, name: str) -> str:
    canvas = _load_bg_pil(bg_path).convert("RGBA")
    for ov in overlays:
        canvas.alpha_composite(ov.content, (ov.x, ov.y))
    out_path = os.path.join(out_folder, f"{name}.png")
    canvas.convert("RGB").save(out_path, "PNG")
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
                 duration: float = DEFAULT_DURATION) -> List[str]:
    """Render the main mp4. If any overlay has a transition, ALSO render
    a `_loop.mp4` companion (same composition, no intro animation)."""
    bg = _bg_clip(bg_path, duration)
    layers_main = [bg] + [_overlay_to_clip(ov, duration, with_transition=True)
                          for ov in overlays]
    main_clip = CompositeVideoClip(layers_main, size=(WIDTH, HEIGHT))
    main_path = os.path.join(out_folder, f"{name}.mp4")
    main_clip.write_videofile(main_path, fps=DEFAULT_FPS, codec="libx264",
                              audio=False, logger=None)

    out_files = [main_path]

    if any(ov.transition != "none" for ov in overlays):
        # second pass: same content, no transitions, for clean looping
        bg2 = _bg_clip(bg_path, duration)
        layers_loop = [bg2] + [_overlay_to_clip(ov, duration, with_transition=False)
                               for ov in overlays]
        loop_clip = CompositeVideoClip(layers_loop, size=(WIDTH, HEIGHT))
        loop_path = os.path.join(out_folder, f"{name}_loop.mp4")
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
        transition                      "none" | "fade" | "slide_left"
                                        | "slide_right" | "slide_up"
                                        | "zoom_in" | "random"
        shadow                          bool             default True

    output_folder    : where to write outputs (created if missing)
    composite_flag   : if True, emit one file per build-up stage
    background_path  : optional explicit bg; otherwise random from _BACKGROUNDS/
    duration         : video duration in seconds (only used for video output)

    Returns: list of generated file paths, in build order.
    """
    os.makedirs(output_folder, exist_ok=True)
    bg_path = get_background(background_path)
    print(f"[bg] using {bg_path}")

    prepared = [prepare_overlay(it) for it in items]

    bg_is_video = is_animated(bg_path)
    has_video_overlay = any(ov.kind == "video" for ov in prepared)
    has_transition = any(ov.transition != "none" for ov in prepared)
    output_is_video = bg_is_video or has_video_overlay or has_transition

    print(f"[mode] video={output_is_video} (bg_video={bg_is_video}, "
          f"vid_ovl={has_video_overlay}, transitions={has_transition})")

    stages: List[List[Overlay]] = (
        [prepared[: i + 1] for i in range(len(prepared))]
        if composite_flag else
        [prepared]
    )

    out_files: List[str] = []
    for idx, stage in enumerate(stages):
        if composite_flag:
            stage_name = f"stage_{idx + 1:02d}_of_{len(stages):02d}"
        else:
            stage_name = "output"
        print(f"[render] {stage_name} ({len(stage)} overlay(s))")
        if output_is_video:
            out_files.extend(render_video(bg_path, stage, output_folder,
                                          stage_name, duration=duration))
        else:
            out_files.append(render_image(bg_path, stage, output_folder,
                                          stage_name))

    return out_files


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _ensure_test_assets():
    """Create dummy backgrounds and overlay images so the demo can run
    standalone."""
    os.makedirs(BACKGROUNDS_DIR, exist_ok=True)

    bg_demo = os.path.join(BACKGROUNDS_DIR, "_demo_bg.jpg")
    if not os.path.exists(bg_demo):
        # gradient background
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


if __name__ == "__main__":
    test_imgs = _ensure_test_assets()

    items = [
        {
            "path": test_imgs[0],
            "position": [25, 50],
            "removeBG": False,
            "scale-page-height-percentage": 35,
            "transition": "random",
        },
        {
            "path": test_imgs[1],
            "position": [50, 50],
            "removeBG": False,
            "scale-page-height-percentage": 35,
            "transition": "random",
        },
        {
            "path": test_imgs[2],
            "position": [75, 50],
            "removeBG": False,
            "scale-page-height-percentage": 35,
            "transition": "random",
        },
    ]

    out = composite(items, "_OUTPUT", composite_flag=True)
    print("\nGenerated:")
    for f in out:
        print("  ", f)
