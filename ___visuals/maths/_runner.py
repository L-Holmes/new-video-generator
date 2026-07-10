"""Shared plumbing for the maths animations: drive one manim Scene to an mp4,
then freeze its last frame to a png. Every module in this package uses it, so
manim's config surface is touched in exactly one place."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from CONFIG import TIMELINE_FPS, TIMELINE_RESOLUTION


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
