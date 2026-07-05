"""
MAKE_EXPLAINER_IMAGE.py
=====================

Composite a piece of foreground media (a Pexels/Wikipedia still or clip) onto
one of the "Einstein board" base images so the footage looks like it's being
presented on the board. This is the rendering primitive behind the
`stickman_explain_stock` / `stickman_explain_wikipedia` media types.

The board base images live in REUSABLE_IMAGES/. Each one has a rectangular
"board" region (hand-measured, in pixels). We contain-fit the foreground inside
that region -- slightly inset so the board's own edge stays visible all the way
around -- and centre it. The footage is never cropped and never larger than the
board on any axis.

Output container is inferred from the output path's extension:
    foreground IMAGE  + .png/.jpg  ->  a single composited still         (PIL)
    foreground IMAGE  + .mp4       ->  a static composited clip           (ffmpeg)
    foreground VIDEO  + .mp4       ->  the clip playing on the board      (ffmpeg)
    foreground VIDEO  + .png/.jpg  ->  first frame composited            (ffmpeg+PIL)

The board image is always static; only the foreground moves. Output MP4s are
silent (the pipeline overlays narration globally).

Standalone smoke test (grabs a random file from the stock-footage cache):
    python make_explainer_img.py
    python make_explainer_img.py --source-dir spices-CACHE/stock_footage
    python make_explainer_img.py --media REUSABLE_IMAGES/foo.png --base 2
    python make_explainer_img.py --media some_clip.mp4 --duration 6
"""

from __future__ import annotations

# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image


# ===========================================================================
# BOARD CONFIGURATION
# ===========================================================================
# Each board base + the pixel rect of its usable "board" area, measured as
# left/top/right/bottom in the base image's own pixel space.
#
# NOTE: the full base images are 1024 x 576.
#
# board-einstein-1 / -2 share the same board rect; board-einstein-3's board is
# much larger (nearly the full frame).

BOARD_CONFIGS: list[dict] = [
    {
        "path":  "REUSABLE_IMAGES/board-einstein-1.png",
        "board": {"left": 220, "top": 11, "right": 986, "bottom": 466},
    },
    {
        "path":  "REUSABLE_IMAGES/board-einstein-2.png",
        "board": {"left": 220, "top": 11, "right": 986, "bottom": 466},
    },
    {
        # NOTE: your message listed this third file as "board-einstein-1.png"
        # a second time (almost certainly a typo). Assuming board-einstein-3.png
        # here -- rename if it's actually called something else.
        "path":  "REUSABLE_IMAGES/board-big.png",
        "board": {"left": 32, "top": 12, "right": 982, "bottom": 565},
    },
]

# Pixels shaved off each side of the board so its edge stays visible around the
# footage. Your 766x455 board with the requested ~760x450 target works out to
# ~3px/side; 4 is close and looks clean. Bump it for a chunkier border.
BOARD_INSET_PX: int = 4

# Output frame-rate for rendered MP4s (matches the rest of the pipeline).
EXPLAINER_FPS: int = 30

IMAGE_EXTS: set[str] = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXTS: set[str] = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}

DEFAULT_SOURCE_DIR = "spices-CACHE/stock_footage"


# ===========================================================================
# SMALL HELPERS
# ===========================================================================

def _classify(path: str) -> str:
    """'image' | 'video' | 'other' from a file's extension (query-string safe)."""
    clean = path.split("?", 1)[0].split("#", 1)[0]
    suffix = Path(clean).suffix.lower()
    if suffix in IMAGE_EXTS:
        return "image"
    if suffix in VIDEO_EXTS:
        return "video"
    return "other"


def _even(n: float) -> int:
    """Round to the nearest even int (libx264 + yuv420p need even dims)."""
    n = int(round(n))
    return max(2, n - (n % 2))


def _choose_board(base_choice) -> dict:
    """
    Pick a board config.

    base_choice:
        None / ""      -> random
        int (1-based)  -> that entry in BOARD_CONFIGS
        str            -> first config whose path contains the string
    """
    if base_choice is None or base_choice == "":
        return random.choice(BOARD_CONFIGS)

    if isinstance(base_choice, int) or (isinstance(base_choice, str) and base_choice.isdigit()):
        idx = int(base_choice) - 1
        if not (0 <= idx < len(BOARD_CONFIGS)):
            raise ValueError(f"--base {base_choice} out of range 1..{len(BOARD_CONFIGS)}")
        return BOARD_CONFIGS[idx]

    for cfg in BOARD_CONFIGS:
        if base_choice in cfg["path"]:
            return cfg
    raise ValueError(f"no board config matching '{base_choice}'")


def _probe_dims(path: str, kind: str) -> tuple[int, int]:
    """Native (width, height) of an image or video."""
    if kind == "image":
        with Image.open(path) as im:
            return im.size

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    raw = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
    try:
        w_str, h_str = raw.split(",")[:2]
        return int(w_str), int(h_str)
    except Exception as exc:
        raise RuntimeError(
            f"ffprobe couldn't read dimensions of {path}: "
            f"{out.stderr.strip() or raw!r}"
        ) from exc


def _placement(board: dict, fg_w: int, fg_h: int,
               inset_px: int) -> tuple[int, int, int, int]:
    """
    Contain-fit (fg_w, fg_h) inside the inset board rect and centre it on the
    board. Returns (x, y, w, h) -- all even -- in base-image pixel space.
    """
    board_w = board["right"] - board["left"]
    board_h = board["bottom"] - board["top"]

    box_w = max(2, board_w - 2 * inset_px)
    box_h = max(2, board_h - 2 * inset_px)

    scale = min(box_w / fg_w, box_h / fg_h)
    w = _even(fg_w * scale)
    h = _even(fg_h * scale)

    cx = board["left"] + board_w / 2
    cy = board["top"] + board_h / 2
    x = _even(cx - w / 2)
    y = _even(cy - h / 2)
    return x, y, w, h


# ===========================================================================
# COMPOSITORS
# ===========================================================================

def _composite_still(base_path: str, fg_image_path: str, output_path: str,
                     x: int, y: int, w: int, h: int) -> None:
    """Paste a (resized) still onto the board with PIL. Respects alpha."""
    base = Image.open(base_path).convert("RGBA")
    fg = Image.open(fg_image_path).convert("RGBA").resize((w, h), Image.LANCZOS)
    base.alpha_composite(fg, (x, y))

    out_ext = Path(output_path).suffix.lower()
    if out_ext in (".jpg", ".jpeg"):
        base.convert("RGB").save(output_path, quality=95)
    else:
        base.save(output_path)


def _composite_video(base_path: str, fg_path: str, fg_is_video: bool,
                     output_path: str, x: int, y: int, w: int, h: int,
                     duration: float) -> None:
    """
    Render an MP4 of the foreground sitting on the static board.

    fg_is_video=False -> a still image held for `duration` (static clip).
    fg_is_video=True  -> the clip looped (-stream_loop) to fill `duration`.
    The board image is always static. Output is silent.
    """
    if fg_is_video:
        fg_input = ["-stream_loop", "-1", "-i", fg_path]
    else:
        fg_input = ["-loop", "1", "-framerate", str(EXPLAINER_FPS), "-i", fg_path]

    filtergraph = (
        f"[1:v]scale={w}:{h},setsar=1[fg];"
        f"[0:v][fg]overlay={x}:{y}[v]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(EXPLAINER_FPS), "-i", base_path,
        *fg_input,
        "-filter_complex", filtergraph,
        "-map", "[v]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-r", str(EXPLAINER_FPS),
        "-an",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[make_explainer] FATAL: ffmpeg failed")
        print(f"[make_explainer] filter: {filtergraph}")
        print(f"[make_explainer] stderr (tail): {result.stderr[-1200:]}")
        if Path(output_path).exists():
            Path(output_path).unlink()
        raise RuntimeError(f"explainer render failed for {fg_path}")


def _extract_first_frame(video_path: str) -> str:
    """Grab frame 1 of a video to a temp PNG. Caller deletes it."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vframes", "1", tmp.name],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        Path(tmp.name).unlink(missing_ok=True)
        raise RuntimeError(
            f"couldn't extract first frame of {video_path}: "
            f"{result.stderr[-600:]}"
        )
    return tmp.name


# ===========================================================================
# PUBLIC ENTRY POINT
# ===========================================================================

def make_explainer(media_path: str, output_path: str,
                   duration: float | None = None,
                   base_choice=None,
                   inset_px: int = BOARD_INSET_PX) -> str:
    """
    Composite `media_path` (image or video) onto a board base and write it to
    `output_path`. Output container is inferred from output_path's suffix:

        .png/.jpg  -> still composite
        .mp4 (etc) -> video composite (requires `duration` seconds)

    Returns output_path.
    """
    media_path = str(media_path)
    output_path = str(output_path)

    in_kind = _classify(media_path)
    if in_kind == "other":
        raise ValueError(f"unsupported foreground media type: {media_path}")
    if not Path(media_path).exists():
        raise FileNotFoundError(f"foreground media not found: {media_path}")

    out_ext = Path(output_path).suffix.lower()
    out_is_video = out_ext in VIDEO_EXTS
    out_is_image = out_ext in IMAGE_EXTS
    if not (out_is_video or out_is_image):
        raise ValueError(f"unsupported output extension: {output_path}")
    if out_is_video and duration is None:
        raise ValueError("duration (seconds) is required for video output")

    board = _choose_board(base_choice)
    base_path = board["path"]
    if not Path(base_path).exists():
        raise FileNotFoundError(f"board base image not found: {base_path}")

    base_w, base_h = Image.open(base_path).size
    fg_w, fg_h = _probe_dims(media_path, in_kind)
    x, y, w, h = _placement(board["board"], fg_w, fg_h, inset_px)

    print(f"[make_explainer] base   : {base_path} ({base_w}x{base_h})")
    print(f"[make_explainer] fg     : {media_path} ({fg_w}x{fg_h}, {in_kind})")
    print(f"[make_explainer] placed : {w}x{h} @ ({x},{y})  inset={inset_px}px")
    print(f"[make_explainer] output : {output_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if out_is_image:
        if in_kind == "video":
            frame = _extract_first_frame(media_path)
            try:
                _composite_still(base_path, frame, output_path, x, y, w, h)
            finally:
                Path(frame).unlink(missing_ok=True)
        else:
            _composite_still(base_path, media_path, output_path, x, y, w, h)
    else:
        _composite_video(base_path, media_path, in_kind == "video",
                         output_path, x, y, w, h, float(duration))

    return output_path


# ===========================================================================
# STANDALONE SMOKE TEST
# ===========================================================================

def _pick_random_media(source_dir: str) -> str:
    p = Path(source_dir)
    if not p.exists():
        print(f"[make_explainer] source dir not found: {source_dir}")
        sys.exit(1)
    files = [f for f in p.iterdir()
             if f.is_file() and _classify(str(f)) in ("image", "video")]
    if not files:
        print(f"[make_explainer] no image/video files in {source_dir}")
        sys.exit(1)
    chosen = random.choice(files)
    print(f"[make_explainer] randomly chose: {chosen.name}")
    return str(chosen)


def _main() -> None:
    ap = argparse.ArgumentParser(description="Composite media onto an Einstein board.")
    ap.add_argument("--media", default="",
                    help="foreground image/video (default: random from --source-dir)")
    ap.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR,
                    help="where to pick a random file from")
    ap.add_argument("--base", default="",
                    help="board to use: 1/2/3 or a path substring (default: random)")
    ap.add_argument("--duration", type=float, default=5.0,
                    help="seconds, for MP4 output")
    ap.add_argument("--out", default="",
                    help="output path (default: temp/explainer_image_test_output.<ext>)")
    args = ap.parse_args()

    media = args.media or _pick_random_media(args.source_dir)
    kind = _classify(media)

    if args.out:
        out_path = args.out
    else:
        Path("temp").mkdir(parents=True, exist_ok=True)
        ext = ".png" if kind == "image" else ".mp4"
        out_path = f"temp/explainer_image_test_output{ext}"

    base_choice = args.base or None
    make_explainer(media, out_path, duration=args.duration, base_choice=base_choice)
    print(f"\n[make_explainer] OK wrote {out_path}")


if __name__ == "__main__":
    _main()
