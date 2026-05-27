#!/usr/bin/env bash
# =============================================================================
# animate.sh - Daily-driver wrapper around animate_stickman.py
#
# Why a wrapper?
#   The pipeline runs inside the `animated_drawings` conda env (Python 3.8.13),
#   not the system Python. Sourcing conda + activating the env + exporting
#   PYOPENGL_PLATFORM=osmesa for headless rendering is boilerplate the user
#   shouldn't have to remember every time.
#
# Pass-through: any flags you give this script are forwarded verbatim to
# animate_stickman.py.
#
# Examples:
#   ./animate.sh                                  # stick.jpg -> stick-animated.mp4
#   ./animate.sh --motion dab
#   ./animate.sh --input drawings/hero.png --output out/hero.mp4 --verbose
# =============================================================================

set -euo pipefail

CONDA_DIR="${CONDA_DIR:-$HOME/miniconda3}"
CONDA_ENV="${CONDA_ENV:-animated_drawings}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- conda activation --------------------------------------------------------
if [[ ! -f "$CONDA_DIR/etc/profile.d/conda.sh" ]]; then
    echo "[fail] Miniconda not found at $CONDA_DIR. Run ./setup.sh first." >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$CONDA_DIR/etc/profile.d/conda.sh"

if ! conda env list | awk '{print $1}' | grep -qx "$CONDA_ENV"; then
    echo "[fail] Conda env '$CONDA_ENV' missing. Run ./setup.sh first." >&2
    exit 1
fi

conda activate "$CONDA_ENV"

# --- headless OpenGL ---------------------------------------------------------
# PyOpenGL picks its backend at import time from this env var. We set it
# *before* invoking python so even imports inside __main__ see it.
export PYOPENGL_PLATFORM="${PYOPENGL_PLATFORM:-osmesa}"

# --- run the pipeline --------------------------------------------------------
exec python "$SCRIPT_DIR/animate_stickman.py" "$@"
