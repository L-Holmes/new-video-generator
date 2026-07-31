"""Shared plumbing for the maths animations: drive one manim Scene to an mp4,
then freeze its last frame to a png. Every module in this package uses it, so
manim's config surface is touched in exactly one place."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple

from CONFIG import TIMELINE_FPS, TIMELINE_RESOLUTION


class MathsRender(NamedTuple):
    """What every maths renderer hands back. See ___visuals/maths and point 1
    of AI_READ_THIS.txt for how the generator chooses between the two files."""

    transition_mp4: str  # the animation, at its natural length
    still_png: str  # its final frame, for the hold (and the too-short case)
    transition_secs: float  # the animation's REAL duration, probed
    min_playable_secs: float  # the shortest cut that still says something —
    #                          everything past it is a trailing settle beat the
    #                          stitcher may trim. Below it: show the still.


def render_manim_scene(
    scene_factory,
    out_mp4: str,
    background_colour: str,
    fps: int = TIMELINE_FPS,
    resolution: tuple[int, int] = TIMELINE_RESOLUTION,
) -> None:
    """Render `scene_factory()` (a zero-arg callable returning a manim Scene)
    to `out_mp4`.

    manim is imported HERE, not at module import: it drags in cairo, pango and
    a few hundred ms of setup, and a run with no maths scenes should pay none
    of that.
    """
    try:
        from manim import tempconfig
    except ImportError as exc:  # pragma: no cover - install guidance
        raise RuntimeError(
            "maths scenes need manim, which needs system cairo + pango:\n"
            "    sudo apt install -y libcairo2-dev libpango1.0-dev "
            "pkg-config python3-dev\n"
            "    uv add manim\n"
            f"(underlying import error: {exc})"
        ) from exc

    out = Path(out_mp4)
    out.parent.mkdir(parents=True, exist_ok=True)
    width, height = resolution

    # manim insists on owning its output tree, so give it a scratch one and
    # move the single file we want out of it.
    with tempfile.TemporaryDirectory(prefix="manim_") as scratch:
        with tempconfig(
            {
                "media_dir": scratch,
                "pixel_width": width,
                "pixel_height": height,
                "frame_rate": fps,
                "background_color": background_colour,
                "disable_caching": True,  # our own cache is the output files
                "flush_cache": True,
                "write_to_movie": True,
                "save_last_frame": False,
                "progress_bar": "none",
                "verbosity": "ERROR",
            }
        ):
            scene = scene_factory()
            scene.render()
            produced = Path(scene.renderer.file_writer.movie_file_path)
            if not produced.exists():
                raise RuntimeError(
                    f"manim reported success but wrote no movie at {produced}"
                )
            shutil.move(str(produced), out)


def extract_last_frame(video_path: str, out_png: str) -> None:
    """Freeze a video's final frame. `-sseof` seeks from the END, so this is
    the exact image the animation settles on — which is what the scene holds
    for the rest of its runtime, and what a too-short scene shows instead."""
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
        "-sseof", "-0.2", "-i", str(video_path),
        "-update", "1", "-q:v", "2", str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(
            f"could not extract the last frame of {video_path}: "
            f"{result.stderr.strip() or 'ffmpeg wrote nothing'}"
        )


def run_cached_maths_render(
    *,
    kind: str,
    cache_key: str,
    out_dir: str,
    scene_factory,
    background_colour: str,
    essential_ratio: float,
) -> MathsRender:
    """The shared tail of every maths renderer: look `cache_key` up on disk,
    render the scene + freeze its last frame on a miss, then probe the REAL
    duration and scale the essential part by it (the encoder lands a frame or
    two off what the config arithmetic promises, so never trust the sums).

    `essential_ratio` is min_playable / transition as the CONFIG timings have
    it; `cache_key` must already cover every input to the render — the data
    AND the look (see AI_READ_THIS.txt, point 1)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    mp4, png = out / f"{cache_key}.mp4", out / f"{cache_key}.png"

    if mp4.exists() and png.exists():
        print(f"[{kind}]   cached: {mp4.name}")
    else:
        print(f"[{kind}]   rendering (manim)")
        render_manim_scene(
            scene_factory=scene_factory,
            out_mp4=str(mp4),
            background_colour=background_colour,
        )
        extract_last_frame(str(mp4), str(png))
        print(f"[{kind}]   ✓ transition {mp4.name} + final still {png.name}")

    natural = probe_duration(str(mp4))
    return MathsRender(
        transition_mp4=str(mp4),
        still_png=str(png),
        transition_secs=natural,
        min_playable_secs=natural * essential_ratio,
    )


def probe_duration(video_path: str) -> float:
    """The video's real duration, straight from the container."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {video_path}: {result.stderr}")
    return float(result.stdout.strip())
