#!/usr/bin/env python3
"""
animate_stickman.py
===================

Automated pipeline that takes a static 2D stickman drawing and produces
a short MP4 of it performing a subtle, natural motion (e.g. a gentle wave).

Wraps Meta FAIR's `AnimatedDrawings` library + its TorchServe-based pose
estimator and renders headlessly via OSMesa.

Pipeline stages:
    1.  Verify environment (Docker, AnimatedDrawings checkout, input image).
    2.  Ensure the `docker_torchserve` container is running and healthy.
    3.  Send the input drawing to TorchServe; receive bbox + mask + pose;
        materialise a per-character annotation directory.
    4.  Synthesise an MVC config (Model-View-Controller) that wires the
        annotated character to a BVH motion clip, enables Mesa/OSMesa
        offscreen rendering, and targets an MP4 output path.
    5.  Invoke `animated_drawings.render.start(...)` to produce the video.
    6.  Move the rendered MP4 next to the input image with the requested
        filename and clean up temporary artefacts.

Idempotent and re-runnable. Designed to fail loudly with actionable
messages rather than silently producing a broken video.

Usage:
    python animate_stickman.py                                # uses defaults
    python animate_stickman.py --input drawing.png --motion dab
    python animate_stickman.py --keep-workdir --verbose

Environment overrides (all optional):
    ANIMATED_DRAWINGS_ROOT   - path to your AnimatedDrawings checkout
                               (default: ~/animated_drawings_src)
    TORCHSERVE_CONTAINER     - docker container name
                               (default: docker_torchserve)
    TORCHSERVE_IMAGE         - docker image name
                               (default: docker_torchserve)
    TORCHSERVE_HOST          - host:port for TorchServe inference API
                               (default: localhost:8080)
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 1) Force OSMesa BEFORE PyOpenGL gets imported anywhere.
#    AnimatedDrawings imports OpenGL at module-load time, so the env var must
#    be set before we touch any of its modules. Setting it via the shell
#    wrapper as well is a belt-and-braces measure.
# ---------------------------------------------------------------------------
os.environ.setdefault("PYOPENGL_PLATFORM", "osmesa")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("animate_stickman")


def _configure_logging(verbose: bool) -> None:
    """Single, consistent logging setup. Verbose flips to DEBUG."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Paths and constants (configurable via env, never hardcoded absolute paths
# beyond the user's home directory)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent

AD_ROOT = Path(
    os.environ.get(
        "ANIMATED_DRAWINGS_ROOT",
        str(Path.home() / "animated_drawings_src"),
    )
).expanduser().resolve()

TS_CONTAINER = os.environ.get("TORCHSERVE_CONTAINER", "docker_torchserve")
TS_IMAGE = os.environ.get("TORCHSERVE_IMAGE", "docker_torchserve")
TS_HOST = os.environ.get("TORCHSERVE_HOST", "localhost:8080")

# How long we'll wait for TorchServe to report healthy after a fresh start.
TS_HEALTH_TIMEOUT_S = 90
TS_HEALTH_POLL_S = 2

# Motions:
#   - "idle"        : a synthetic, very subtle breathing/sway clip we generate
#                     ourselves at runtime from the Mixamo skeleton. Use this
#                     for "the character is just standing there, alive."
#   - the other 4   : ship with the AnimatedDrawings repo at examples/bvh/fair1/.
SHIPPED_MOTIONS = ("wave_hello", "dab", "jumping", "zombie")
ALL_MOTIONS = ("idle",) + SHIPPED_MOTIONS
DEFAULT_MOTION = "idle"


# ---------------------------------------------------------------------------
# Custom exception so callers can distinguish our failures from library bugs
# ---------------------------------------------------------------------------
class PipelineError(RuntimeError):
    """Raised when a pipeline stage fails in a user-actionable way."""


# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------
def _which_or_die(binary: str, hint: str) -> str:
    """Resolve a binary on $PATH or raise a helpful PipelineError."""
    path = shutil.which(binary)
    if path is None:
        raise PipelineError(f"`{binary}` not found on PATH. {hint}")
    return path


def check_python_version() -> None:
    """AnimatedDrawings is pinned to Python 3.8.x; warn if we drift."""
    major, minor = sys.version_info[:2]
    if (major, minor) != (3, 8):
        log.warning(
            "Python %d.%d detected. AnimatedDrawings is pinned to 3.8.x — "
            "if you see import errors, activate the conda env first "
            "(`conda activate animated_drawings`).",
            major,
            minor,
        )


def check_animated_drawings_checkout() -> None:
    """Verify AnimatedDrawings is cloned and importable from AD_ROOT."""
    if not AD_ROOT.exists():
        raise PipelineError(
            f"AnimatedDrawings checkout not found at {AD_ROOT}. "
            f"Run ./setup.sh first, or set ANIMATED_DRAWINGS_ROOT."
        )

    setup_py = AD_ROOT / "setup.py"
    examples_dir = AD_ROOT / "examples"
    if not setup_py.exists() or not examples_dir.exists():
        raise PipelineError(
            f"{AD_ROOT} doesn't look like an AnimatedDrawings checkout "
            f"(missing setup.py or examples/). Re-clone and re-run setup.sh."
        )

    # Make the example helpers importable. They are not part of the
    # installable package, but we need image_to_annotations() from them.
    examples_path = str(examples_dir)
    if examples_path not in sys.path:
        sys.path.insert(0, examples_path)


# ---------------------------------------------------------------------------
# Docker / TorchServe management
# ---------------------------------------------------------------------------
def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Thin wrapper around subprocess that always captures output."""
    log.debug("$ %s", " ".join(cmd))
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False, **kwargs
    )


def docker_available() -> tuple[bool, str]:
    """
    Return (ok, diagnostic_message).

    On failure, the diagnostic includes which docker binary was found, what
    DOCKER_HOST is set to, and the actual stderr from `docker info` — so the
    user isn't left guessing whether it's a permissions issue, a daemon
    issue, a snap conflict, or a stale env var.
    """
    docker_bin = shutil.which("docker")
    if docker_bin is None:
        return False, "`docker` not found on PATH."

    res = _run(["docker", "info"])
    if res.returncode == 0:
        return True, ""

    # Build a diagnostic that actually helps.
    docker_host = os.environ.get("DOCKER_HOST", "(unset)")
    sock = "/var/run/docker.sock"
    sock_info = "missing"
    if os.path.exists(sock):
        st = os.stat(sock)
        sock_info = f"mode={oct(st.st_mode)[-3:]} uid={st.st_uid} gid={st.st_gid}"

    diag = (
        f"`docker info` exited {res.returncode}.\n"
        f"  binary:      {docker_bin}\n"
        f"  DOCKER_HOST: {docker_host}\n"
        f"  socket:      {sock} ({sock_info})\n"
        f"  stderr:      {res.stderr.strip() or '(empty)'}\n"
        f"  stdout tail: {res.stdout.strip().splitlines()[-1] if res.stdout.strip() else '(empty)'}"
    )
    return False, diag


def _container_state() -> Optional[str]:
    """Return docker container state ('running', 'exited', ...) or None."""
    res = _run(
        ["docker", "inspect", "-f", "{{.State.Status}}", TS_CONTAINER]
    )
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def _image_exists() -> bool:
    res = _run(["docker", "image", "inspect", TS_IMAGE])
    return res.returncode == 0


def _build_image_if_missing() -> None:
    """Build docker_torchserve image from AnimatedDrawings/torchserve/."""
    if _image_exists():
        log.debug("Docker image %s already present.", TS_IMAGE)
        return

    torchserve_dir = AD_ROOT / "torchserve"
    if not torchserve_dir.exists():
        raise PipelineError(
            f"Cannot find {torchserve_dir}. Was AnimatedDrawings cloned "
            f"fully? Re-run setup.sh."
        )

    log.info("Building Docker image '%s' from %s (takes 5-10 minutes)...",
             TS_IMAGE, torchserve_dir)
    res = subprocess.run(
        ["docker", "build", "-t", TS_IMAGE, "."],
        cwd=torchserve_dir,
        check=False,
    )
    if res.returncode != 0:
        raise PipelineError(
            "Docker image build failed. Check the build log above. "
            "Common causes: no network, no disk space, or torchserve/ "
            "directory missing files."
        )


def ensure_torchserve_running() -> None:
    """Idempotently ensure the docker_torchserve container is up."""
    ok, diag = docker_available()
    if not ok:
        raise PipelineError(
            "Docker daemon not reachable.\n\n"
            f"{diag}\n\n"
            "Common causes on Debian 13:\n"
            "  * You were added to the 'docker' group but the current shell "
            "predates the change. Run `newgrp docker` IN THIS SHELL (the same "
            "one you run animate.sh from), or log out and back in.\n"
            "  * DOCKER_HOST is set to something stale (e.g. a leftover from "
            "a previous Docker Desktop install). `unset DOCKER_HOST` and retry.\n"
            "  * A snap-installed docker is shadowing the apt one. "
            "`snap remove docker` and use the apt package only.\n"
            "  * Daemon isn't actually running. `sudo systemctl restart docker`."
        )

    _build_image_if_missing()

    state = _container_state()
    if state == "running":
        log.info("TorchServe container '%s' already running.", TS_CONTAINER)
        return

    if state in ("exited", "created", "dead", "paused"):
        log.info("Starting existing container '%s' (state: %s)...",
                 TS_CONTAINER, state)
        res = _run(["docker", "start", TS_CONTAINER])
        if res.returncode != 0:
            log.warning("`docker start` failed; recreating container.")
            _run(["docker", "rm", "-f", TS_CONTAINER])
            state = None

    if state is None:
        log.info("Creating and starting container '%s'...", TS_CONTAINER)
        res = _run([
            "docker", "run", "-d",
            "--name", TS_CONTAINER,
            "-p", "8080:8080",
            "-p", "8081:8081",
            TS_IMAGE,
        ])
        if res.returncode != 0:
            raise PipelineError(
                f"Failed to start container: {res.stderr.strip()}\n"
                f"If the error mentions a port conflict, stop the process "
                f"using ports 8080/8081 or remove the existing container "
                f"with `docker rm -f {TS_CONTAINER}`."
            )


def wait_for_torchserve_healthy() -> None:
    """Block until /ping returns Healthy, or raise after a timeout."""
    import urllib.error
    import urllib.request

    url = f"http://{TS_HOST}/ping"
    deadline = time.monotonic() + TS_HEALTH_TIMEOUT_S
    log.info("Waiting for TorchServe at %s to report healthy...", url)

    last_err: Optional[str] = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if "Healthy" in body:
                    log.info("TorchServe is healthy.")
                    return
                last_err = f"unexpected body: {body.strip()}"
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            last_err = str(e)
        time.sleep(TS_HEALTH_POLL_S)

    # Dump the container logs to help diagnose
    logs = _run(["docker", "logs", "--tail", "40", TS_CONTAINER])
    raise PipelineError(
        f"TorchServe never became healthy within {TS_HEALTH_TIMEOUT_S}s.\n"
        f"Last error: {last_err}\n\n"
        f"--- last 40 lines of `docker logs {TS_CONTAINER}` ---\n"
        f"{logs.stdout}\n{logs.stderr}\n"
        f"-------------------------------------------------------\n"
        f"Common cause: not enough RAM allocated to the container. "
        f"Try `docker stats` to see usage."
    )


# ---------------------------------------------------------------------------
# Video codec workaround
# ---------------------------------------------------------------------------
def _patch_opencv_video_codec() -> None:
    """
    Work around opencv-python's missing H.264 encoder.

    The `opencv-python` pip wheel bundles a libavcodec that excludes H.264
    due to its non-free licence. AnimatedDrawings hard-codes the `avc1`
    FourCC, so `cv2.VideoWriter` silently fails to open with:

        Could not find encoder for codec_id=27, error: Encoder not found

    and every frame write becomes a no-op. We monkey-patch the FourCC lookup
    to substitute `mp4v` (MPEG-4 Part 2, patent-free, bundled with every
    opencv-python build). The resulting .mp4 is then post-transcoded to
    true H.264 by system ffmpeg in `_transcode_to_h264`, so the user's
    final output is a fully spec-compliant H.264 .mp4.

    Must run BEFORE `render.start()` is invoked (the FourCC call happens
    at render-time, not at import-time, so it's enough to patch before
    we kick off the render).
    """
    import cv2  # type: ignore

    if getattr(cv2.VideoWriter_fourcc, "_substituted_avc1", False):
        return  # already patched in this process

    _original = cv2.VideoWriter_fourcc

    def _patched(*chars):
        code = "".join(chars).lower()
        if code in ("avc1", "h264", "x264"):
            log.debug(
                "Substituting cv2 fourcc '%s' -> 'mp4v' "
                "(opencv-python lacks bundled H.264 encoder).",
                code,
            )
            return _original(*"mp4v")
        return _original(*chars)

    _patched._substituted_avc1 = True  # type: ignore[attr-defined]
    cv2.VideoWriter_fourcc = _patched  # type: ignore[assignment]


def _transcode_to_h264(src: Path, dst: Path) -> None:
    """
    Transcode an mp4v-encoded .mp4 to true H.264 using system ffmpeg.

    System ffmpeg (from `apt install ffmpeg`) is built against libx264 on
    Debian 13, so the actual H.264 encode happens here — not inside cv2.

    Settings:
        * libx264, CRF 20         visually-lossless-ish, ~5x smaller than raw
        * pix_fmt yuv420p         max compatibility (Safari, iOS, Android)
        * preset veryfast         the encode is short, CPU savings > size
        * +faststart              moov atom at the front, web-streamable
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise PipelineError(
            "ffmpeg not found on PATH. setup.sh installs it via apt; if you "
            "removed it, run `sudo apt-get install ffmpeg`."
        )

    cmd = [
        ffmpeg, "-y", "-loglevel", "warning",
        "-i", str(src),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-crf", "20",
        "-movflags", "+faststart",
        str(dst),
    ]
    log.debug("$ %s", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise PipelineError(
            f"ffmpeg transcode failed (exit {res.returncode}):\n"
            f"{(res.stderr or res.stdout).strip()}"
        )
    if not dst.exists() or dst.stat().st_size == 0:
        raise PipelineError(f"ffmpeg produced no output at {dst}.")


# ---------------------------------------------------------------------------
# Input preprocessing — thin-line stick figures wreck the pose estimator
# ---------------------------------------------------------------------------
def _preprocess_drawing(src: Path, work_dir: Path, enabled: bool = True) -> Path:
    """
    Auto-dilate the strokes of thin stick-figure drawings.

    Meta's humanoid pose estimator was trained on filled-in cartoon
    characters with lots of "body". A pure stick figure with 2-pixel
    pencil lines hands it almost nothing to segment, which is why we see
    `point not inside any triangle` and `tA1xA1 is singular` warnings,
    followed by joints landing outside the body mesh.

    Fix: detect thin-line stick figures heuristically (mostly white
    background, very few ink pixels) and dilate the ink with a kernel
    sized to the image. The character will end up rendered slightly
    thicker than you drew it, but the rig will actually fit the body.

    Returns the path of the image to feed downstream (preprocessed file
    in `work_dir`, or `src` unchanged if preprocessing is disabled or
    the heuristic decides this isn't a thin stick figure).
    """
    if not enabled:
        log.debug("Preprocessing disabled by flag; using raw input.")
        return src

    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError as e:
        log.warning("Preprocessing skipped (missing dep: %s); using raw input.", e)
        return src

    img_pil = Image.open(src).convert("L")  # grayscale
    arr = np.array(img_pil)
    h, w = arr.shape

    bg_ratio = float((arr > 200).mean())
    ink_ratio = float((arr < 100).mean())
    log.debug("Image stats: %dx%d, %.1f%% bright background, %.1f%% dark ink.",
              w, h, bg_ratio * 100, ink_ratio * 100)

    # Heuristic: mostly white + very little ink ⇒ thin stick figure.
    if bg_ratio < 0.85 or ink_ratio > 0.10:
        log.debug("Doesn't look like a thin stick figure; passing through unchanged.")
        return src

    # Kernel size proportional to image dim, clamped to a sane range.
    k = max(3, min(15, min(h, w) // 150))
    if k % 2 == 0:
        k += 1
    log.info(
        "Detected thin stick figure (%.0f%% white, %.0f%% ink). "
        "Dilating strokes with %dx%d kernel for better rigging.",
        bg_ratio * 100, ink_ratio * 100, k, k,
    )

    ink_mask = (arr < 128).astype(np.uint8) * 255
    kernel = np.ones((k, k), np.uint8)
    dilated = cv2.dilate(ink_mask, kernel, iterations=1)

    out = np.full_like(arr, 255)
    out[dilated > 0] = 0

    dst = work_dir / "preprocessed.png"
    Image.fromarray(out).save(dst)
    log.debug("Preprocessed drawing saved to %s", dst)
    return dst


# ---------------------------------------------------------------------------
# Synthetic "idle" BVH generation
# ---------------------------------------------------------------------------
def _parse_bvh_hierarchy(text: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Split a BVH into (hierarchy_text, [(joint, channel_name), ...]).

    The channels list is in file order — i.e. the same order in which the
    MOTION section's per-frame values appear. We only need it dense enough
    to know which value belongs to which (joint, channel); we don't actually
    care about the tree shape because we're going to write our own motion.
    """
    hier, sep, _ = text.partition("MOTION")
    if not sep:
        raise PipelineError("Template BVH has no MOTION section.")

    channels: list[tuple[str, str]] = []
    current_joint = ""
    # Hierarchy can have ROOT, JOINT, and End Site declarations. End Site
    # blocks don't have CHANNELS so we don't need to track them.
    for raw in hier.splitlines():
        line = raw.strip()
        if line.startswith("ROOT ") or line.startswith("JOINT "):
            current_joint = line.split(None, 1)[1].strip()
        elif line.startswith("CHANNELS"):
            parts = line.split()
            n = int(parts[1])
            for ch in parts[2:2 + n]:
                channels.append((current_joint, ch))
    return hier, channels


def _generate_idle_motion(
    work_dir: Path,
    duration_s: float = 5.0,
    fps: int = 30,
) -> tuple[Path, Path]:
    """
    Generate (motion_cfg, retarget_cfg) for a subtle "idle" animation.

    Reuses the Mixamo skeleton from wave_hello.bvh (so the existing
    fair1_ppf.yaml retarget config Just Works) and replaces the MOTION
    section with gentle sine-wave oscillations on a handful of joints:

      * Spine1 Xrotation  — slight forward/back breathing
      * Neck/Head         — subtle head sway + small tilt
      * Left/Right Arm Z  — small arm drift, opposite phases

    Everything else holds its rest-pose value from the template's first
    frame. The result is "alive-looking but standing still" — no waving,
    no walking, no leg motion.

    Returns paths to the generated motion config and the (reused)
    retarget config.
    """
    import math
    import yaml  # type: ignore

    template_bvh = AD_ROOT / "examples" / "bvh" / "fair1" / "wave_hello.bvh"
    template_motion_cfg = (
        AD_ROOT / "examples" / "config" / "motion" / "wave_hello.yaml"
    )
    retarget_cfg = AD_ROOT / "examples" / "config" / "retarget" / "fair1_ppf.yaml"

    for p in (template_bvh, template_motion_cfg, retarget_cfg):
        if not p.exists():
            raise PipelineError(
                f"Missing template for idle motion: {p}. Re-clone AnimatedDrawings."
            )

    text = template_bvh.read_text()
    hierarchy, channels = _parse_bvh_hierarchy(text)

    # Grab the template's first frame to use as the rest pose. All channels
    # we don't animate just hold this value forever, which keeps the
    # character planted exactly where the BVH originally started it.
    _, _, motion_section = text.partition("MOTION")
    motion_data_lines = [
        ln for ln in motion_section.strip().splitlines()
        if not ln.startswith("Frames:") and not ln.startswith("Frame Time:")
    ]
    if not motion_data_lines:
        raise PipelineError("Template BVH has no motion frames to read rest pose from.")
    rest_pose = [float(x) for x in motion_data_lines[0].split()]
    if len(rest_pose) != len(channels):
        raise PipelineError(
            f"Template BVH channel mismatch: hierarchy says {len(channels)}, "
            f"motion frame has {len(rest_pose)}."
        )

    # Sine-wave targets keyed by (joint, channel). Amplitudes are in degrees.
    # Frequencies are in Hz — kept low so motion reads as "breathing" not "fidget".
    # Phases are in radians.
    targets: dict[tuple[str, str], tuple[float, float, float]] = {
        # breathing — spine bends back/forth a hair
        ("Spine1",     "Xrotation"): (1.0, 0.30, 0.0),
        # head — gentle slow sway side to side, plus tiny tilt
        ("Neck",       "Yrotation"): (1.5, 0.18, 0.0),
        ("Head",       "Yrotation"): (1.5, 0.18, 0.0),
        ("Head",       "Zrotation"): (0.8, 0.22, math.pi / 3),
        # arms drift slightly, opposite phases so it looks asymmetric/natural
        ("LeftArm",    "Zrotation"): (2.0, 0.25, 0.0),
        ("RightArm",   "Zrotation"): (-2.0, 0.25, math.pi),
    }

    n_frames = int(duration_s * fps)
    frame_time = 1.0 / fps

    frames: list[list[float]] = []
    for fi in range(n_frames):
        t = fi * frame_time
        row: list[float] = []
        for i, (joint, ch) in enumerate(channels):
            base = rest_pose[i]
            if "position" in ch.lower():
                # Lock the root in place — no walking, no drifting.
                row.append(base)
                continue
            key = (joint, ch)
            if key in targets:
                amp_deg, freq_hz, phase = targets[key]
                row.append(base + amp_deg * math.sin(2 * math.pi * freq_hz * t + phase))
            else:
                row.append(base)
        frames.append(row)

    # Emit the idle BVH (hierarchy unchanged, motion replaced).
    idle_bvh = work_dir / "idle.bvh"
    with open(idle_bvh, "w") as f:
        f.write(hierarchy)
        f.write("MOTION\n")
        f.write(f"Frames: {n_frames}\n")
        f.write(f"Frame Time: {frame_time:.6f}\n")
        for row in frames:
            f.write(" ".join(f"{v:.6f}" for v in row) + "\n")
    log.debug("Wrote synthetic idle BVH (%d frames, %.1fs) to %s",
              n_frames, duration_s, idle_bvh)

    # Patch the wave_hello motion config to point at our new BVH.
    motion_cfg_data = yaml.safe_load(template_motion_cfg.read_text())
    motion_cfg_data["filepath"] = str(idle_bvh.resolve())
    motion_cfg_data["start_frame_idx"] = 0
    motion_cfg_data["end_frame_idx"] = n_frames - 1
    motion_cfg_data["frame_time"] = frame_time

    idle_motion_cfg = work_dir / "idle_motion.yaml"
    with open(idle_motion_cfg, "w") as f:
        yaml.safe_dump(motion_cfg_data, f, default_flow_style=False)
    log.debug("Wrote patched motion config to %s", idle_motion_cfg)

    return idle_motion_cfg, retarget_cfg


# ---------------------------------------------------------------------------
# Annotation + rendering
# ---------------------------------------------------------------------------
def _resolve_motion_paths(motion_name: str) -> tuple[Path, Path]:
    """Return (motion_cfg_path, retarget_cfg_path) for a shipped motion."""
    if motion_name == "idle":
        # idle is generated at runtime by _generate_idle_motion(); it does
        # not have shipped config files. Callers must handle this branch.
        raise PipelineError(
            "Internal error: 'idle' motion must be resolved via "
            "_generate_idle_motion(), not _resolve_motion_paths()."
        )
    if motion_name not in SHIPPED_MOTIONS:
        raise PipelineError(
            f"Unknown motion '{motion_name}'. "
            f"Options: {', '.join(ALL_MOTIONS)}. "
            f"To use a custom BVH, point --motion-cfg / --retarget-cfg "
            f"at your own files (see examples/config/README.md in the "
            f"AnimatedDrawings repo)."
        )

    motion_cfg = AD_ROOT / "examples" / "config" / "motion" / f"{motion_name}.yaml"
    # All four shipped FAIR1 motions use the same retarget config.
    retarget_cfg = AD_ROOT / "examples" / "config" / "retarget" / "fair1_ppf.yaml"

    for p in (motion_cfg, retarget_cfg):
        if not p.exists():
            raise PipelineError(
                f"Expected config file not found: {p}. "
                f"Did the AnimatedDrawings clone complete successfully?"
            )
    return motion_cfg, retarget_cfg


def generate_annotations(input_image: Path, work_dir: Path) -> Path:
    """
    Call into AnimatedDrawings' image_to_annotations helper.

    Writes char_cfg.yaml, mask.png, texture.png into a subdirectory of
    work_dir, and returns that subdirectory.
    """
    # Imported lazily so failures appear *after* environment checks have
    # given us a chance to print helpful messages.
    try:
        from image_to_annotations import image_to_annotations  # type: ignore
    except ImportError as e:
        raise PipelineError(
            "Could not import image_to_annotations from the AnimatedDrawings "
            "examples directory. Make sure ANIMATED_DRAWINGS_ROOT is correct "
            f"and that you ran `pip install -e .` inside it. Original error: {e}"
        ) from e

    char_dir = work_dir / "character"
    char_dir.mkdir(parents=True, exist_ok=True)

    log.info("Asking TorchServe to detect, segment and rig the character...")
    try:
        image_to_annotations(str(input_image), str(char_dir))
    except Exception as e:
        raise PipelineError(
            f"Annotation step failed: {e}\n"
            "If the model couldn't find a humanoid figure, your stickman "
            "may be too thin or low-contrast. Try thicker lines on a white "
            "background, or run AnimatedDrawings' fix_annotations.py to "
            "correct the joints manually."
        ) from e

    # Sanity check the outputs
    required = ("char_cfg.yaml", "mask.png", "texture.png")
    missing = [n for n in required if not (char_dir / n).exists()]
    if missing:
        raise PipelineError(
            f"Annotation step did not produce expected files: {missing}. "
            f"Check {char_dir} for partial output."
        )

    log.info("Annotations written to %s", char_dir)
    return char_dir


def render_animation(
    char_dir: Path,
    motion_cfg: Path,
    retarget_cfg: Path,
    output_mp4: Path,
) -> None:
    """
    Build an MVC config, dump it to a YAML file, and invoke render.start().

    Two-stage video write:
        1. AnimatedDrawings writes an mp4v-encoded .mp4 to a `_raw.mp4`
           sibling of the final output (mp4v works because we monkey-patch
           opencv's avc1 FourCC).
        2. We transcode that mp4v file to true H.264 using system ffmpeg.

    The base config (animated_drawings/mvc_base_cfg.yaml) is loaded first and
    our file is overlayed on top, so we only specify the fields we override.
    """
    # Patch cv2 BEFORE animated_drawings.render gets a chance to call into
    # its view module. The fourcc lookup happens at render-time, so we just
    # need this in place before render.start().
    _patch_opencv_video_codec()

    try:
        from animated_drawings import render  # type: ignore
    except ImportError as e:
        raise PipelineError(
            "Could not import animated_drawings. Most likely the conda env "
            "isn't active. Activate it (`conda activate animated_drawings`) "
            f"or run via ./animate.sh. Original error: {e}"
        ) from e

    # PyYAML is a transitive dep of animated_drawings, so this always succeeds
    # inside the env. Imported lazily so `--help` works outside the env.
    import yaml  # type: ignore

    # AnimatedDrawings writes mp4v to raw_mp4; ffmpeg transcodes -> output_mp4.
    raw_mp4 = output_mp4.with_name(output_mp4.stem + "_mp4v.mp4")

    mvc_cfg = {
        "scene": {
            "ANIMATED_CHARACTERS": [
                {
                    "character_cfg": str((char_dir / "char_cfg.yaml").resolve()),
                    "motion_cfg":    str(motion_cfg.resolve()),
                    "retarget_cfg":  str(retarget_cfg.resolve()),
                }
            ]
        },
        "controller": {
            "MODE": "video_render",
            "OUTPUT_VIDEO_PATH": str(raw_mp4.resolve()),
        },
        "view": {
            # Headless offscreen rendering — required on a server / minimal
            # desktop without a working GLX context.
            "USE_MESA": True,
        },
    }

    mvc_yaml_path = char_dir.parent / "mvc_config.yaml"
    with open(mvc_yaml_path, "w") as f:
        yaml.safe_dump(mvc_cfg, f, default_flow_style=False)
    log.debug("MVC config written to %s", mvc_yaml_path)

    log.info("Rendering frames + writing intermediate mp4v container (~20s)...")
    try:
        render.start(str(mvc_yaml_path))
    except Exception as e:
        raise PipelineError(
            f"Render failed: {e}\n"
            f"MVC config left at {mvc_yaml_path} for inspection.\n"
            "If the error mentions OpenGL/OSMesa, ensure `libosmesa6` is "
            "installed (`apt list --installed | grep osmesa`) and that "
            "PYOPENGL_PLATFORM=osmesa is set (animate.sh exports it)."
        ) from e

    if not raw_mp4.exists() or raw_mp4.stat().st_size == 0:
        raise PipelineError(
            f"AnimatedDrawings render produced no output at {raw_mp4}.\n"
            "The cv2 codec monkey-patch may have been bypassed — check that "
            "no other code re-imported cv2 after _patch_opencv_video_codec(). "
            f"Run with --keep-workdir and inspect {mvc_yaml_path}."
        )
    log.debug("mp4v intermediate: %s (%.1f KB)",
              raw_mp4, raw_mp4.stat().st_size / 1024)

    log.info("Transcoding mp4v -> H.264 with system ffmpeg (~2s)...")
    _transcode_to_h264(raw_mp4, output_mp4)
    raw_mp4.unlink(missing_ok=True)

    if not output_mp4.exists() or output_mp4.stat().st_size == 0:
        raise PipelineError(
            f"Transcode reported success but {output_mp4} is missing or empty."
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
@contextmanager
def _work_dir(keep: bool):
    """Yield a working directory; clean it up unless --keep-workdir."""
    tmp = Path(tempfile.mkdtemp(prefix="stickman_animator_"))
    log.debug("Work dir: %s", tmp)
    try:
        yield tmp
    finally:
        if keep:
            log.info("Work dir kept at %s", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def run_pipeline(
    input_image: Path,
    output_mp4: Path,
    motion: str,
    preprocess: bool,
    keep_workdir: bool,
) -> None:
    if not input_image.exists():
        raise PipelineError(f"Input image not found: {input_image}")
    if input_image.is_dir():
        raise PipelineError(f"Input must be a file, got directory: {input_image}")

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    if output_mp4.exists():
        log.info("Output %s exists; will overwrite.", output_mp4)
        output_mp4.unlink()

    check_python_version()
    check_animated_drawings_checkout()

    # Validate the motion choice early. For shipped motions, also resolve
    # the config paths up-front so a typo fails before we burn time on
    # docker / annotation. For "idle" we defer because it needs a work_dir.
    if motion != "idle":
        motion_cfg, retarget_cfg = _resolve_motion_paths(motion)
    else:
        motion_cfg = retarget_cfg = None  # populated inside work_dir context

    ensure_torchserve_running()
    wait_for_torchserve_healthy()

    with _work_dir(keep_workdir) as work:
        # Optional preprocessing: dilate thin stick-figure strokes so the
        # pose estimator has actual body silhouette to work with.
        prepped = _preprocess_drawing(input_image, work, enabled=preprocess)
        char_dir = generate_annotations(prepped, work)

        if motion == "idle":
            motion_cfg, retarget_cfg = _generate_idle_motion(work)

        # Render directly to the work dir, then move into place atomically.
        # That way a Ctrl-C mid-render doesn't leave a half-written MP4
        # at the final path.
        tmp_mp4 = work / "video.mp4"
        render_animation(char_dir, motion_cfg, retarget_cfg, tmp_mp4)
        shutil.move(str(tmp_mp4), str(output_mp4))

    log.info("Done. Output: %s (%.1f KB)",
             output_mp4, output_mp4.stat().st_size / 1024)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Animate a 2D stickman drawing into a short MP4.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--input", "-i",
        type=Path,
        default=SCRIPT_DIR / "stick.jpg",
        help="Path to the input drawing.",
    )
    p.add_argument(
        "--output", "-o",
        type=Path,
        default=SCRIPT_DIR / "stick-animated.mp4",
        help="Path for the output MP4.",
    )
    p.add_argument(
        "--motion", "-m",
        choices=ALL_MOTIONS,
        default=DEFAULT_MOTION,
        help=(
            "Which motion to apply. 'idle' (default) is a synthetic, very "
            "subtle breathing/sway clip — no waving, no walking. The other "
            "four are full performance clips shipped with AnimatedDrawings."
        ),
    )
    p.add_argument(
        "--no-preprocess",
        action="store_true",
        help=(
            "Disable auto-dilation of thin stick-figure strokes. Without "
            "preprocessing, the pose estimator often places joints outside "
            "the body mask of pure stick figures, producing a broken rig."
        ),
    )
    p.add_argument(
        "--keep-workdir",
        action="store_true",
        help="Don't delete the temp dir (useful for debugging annotations).",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="DEBUG logging.",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        run_pipeline(
            input_image=args.input.expanduser().resolve(),
            output_mp4=args.output.expanduser().resolve(),
            motion=args.motion,
            preprocess=not args.no_preprocess,
            keep_workdir=args.keep_workdir,
        )
    except PipelineError as e:
        log.error("Pipeline failed: %s", e)
        return 2
    except KeyboardInterrupt:
        log.error("Interrupted by user.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
