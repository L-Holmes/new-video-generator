#!/usr/bin/env bash
# =============================================================================
# setup.sh - One-time environment bootstrap for the stickman animator
#
# Designed for: Debian 13 (Trixie), AMD64
# Designed against: archived AnimatedDrawings (Meta FAIR) requiring Python 3.8.13
#
# Idempotent: safe to re-run. Each step checks state before acting.
#
# What it does:
#   1.  apt-installs OS-level dependencies (OpenGL/Mesa, GLFW, ffmpeg, docker).
#   2.  Adds the current user to the `docker` group (one-time).
#   3.  Installs Miniconda into ~/miniconda3 if it isn't already there.
#   4.  Creates the `animated_drawings` conda env on Python 3.8.13.
#   5.  Clones AnimatedDrawings into ~/animated_drawings_src and pip-installs it.
#   6.  Builds the docker_torchserve image used by the pose-estimation step.
# =============================================================================

set -euo pipefail

# ---------- knobs the user can override --------------------------------------
AD_ROOT="${ANIMATED_DRAWINGS_ROOT:-$HOME/animated_drawings_src}"
AD_REPO="${ANIMATED_DRAWINGS_REPO:-https://github.com/facebookresearch/AnimatedDrawings.git}"
CONDA_DIR="${CONDA_DIR:-$HOME/miniconda3}"
CONDA_ENV="${CONDA_ENV:-animated_drawings}"
PY_VERSION="${PY_VERSION:-3.8.13}"
TS_IMAGE="${TORCHSERVE_IMAGE:-docker_torchserve}"

# ---------- helpers ----------------------------------------------------------
log()  { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[fail]\033[0m  %s\n' "$*" >&2; exit 1; }

need_sudo() {
    if [[ $EUID -eq 0 ]]; then SUDO=""; else SUDO="sudo"; fi
}

# ---------- 0. sanity --------------------------------------------------------
if ! command -v apt-get >/dev/null 2>&1; then
    die "This script targets Debian/Ubuntu (apt-get not found)."
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
    warn "Untested on $(uname -m). The Torch wheels expect x86_64."
fi

need_sudo

# ---------- 1. apt packages --------------------------------------------------
log "Updating apt index and installing OS-level dependencies..."
$SUDO apt-get update -qq

# Three groups:
#   build tools, runtime libs for OpenGL/GLFW/Mesa headless rendering,
#   and the docker engine.
APT_PACKAGES=(
    # general build / fetch
    build-essential
    ca-certificates
    curl
    git
    wget

    # video muxing for the MP4 output
    ffmpeg

    # OpenGL + windowing libs (GLFW pulls some even in headless mode)
    libgl1
    libglu1-mesa
    libglfw3
    libglib2.0-0
    libsm6
    libxext6
    libxrender1
    libxi6

    # offscreen / headless rendering — this is the critical bit for a
    # machine without a working display server.
    libosmesa6
    libosmesa6-dev

    # tiny but commonly required by pip-installed image libs
    libsndfile1

    # container runtime
    docker.io
)

$SUDO apt-get install -y --no-install-recommends "${APT_PACKAGES[@]}"

# ---------- 2. docker group --------------------------------------------------
if ! getent group docker >/dev/null; then
    $SUDO groupadd docker
fi
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    log "Adding $USER to the docker group..."
    $SUDO usermod -aG docker "$USER"
    warn "You must log out and log back in (or run \`newgrp docker\`) before"
    warn "docker commands will work without sudo."
fi

# Make sure the daemon is up.
if command -v systemctl >/dev/null 2>&1; then
    $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
fi

# ---------- 3. miniconda -----------------------------------------------------
if [[ ! -x "$CONDA_DIR/bin/conda" ]]; then
    log "Installing Miniconda into $CONDA_DIR ..."
    tmp_installer="$(mktemp --suffix=.sh)"
    curl -fsSL -o "$tmp_installer" \
        "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
    bash "$tmp_installer" -b -p "$CONDA_DIR"
    rm -f "$tmp_installer"
else
    log "Miniconda already present at $CONDA_DIR."
fi

# Make `conda` available to this shell. We deliberately do NOT call `conda init`
# globally — the user may have their own shell setup; we just source the hook.
# shellcheck disable=SC1091
source "$CONDA_DIR/etc/profile.d/conda.sh"

# ---------- 4. conda env -----------------------------------------------------
# Anaconda's default channels (`pkgs/main`, `pkgs/r`) require explicit Terms
# of Service acceptance as of conda 25.x — without this, `conda create` fails
# with `CondaToSNonInteractiveError`.
#
# The ToS permits free use for individuals and organisations under 200
# employees. If that doesn't fit your situation, the right move is to swap
# Miniconda for Miniforge (community-maintained, defaults to conda-forge,
# no ToS gate): https://github.com/conda-forge/miniforge — drop-in
# replacement, same `conda` CLI.
#
# We feed `yes |` so that we accept whatever interactive prompt conda throws
# at us across versions. `|| true` keeps re-runs quiet when the ToS is
# already on file.
log "Accepting Anaconda default-channel ToS (required by conda 25+)..."
yes 2>/dev/null | conda tos accept --override-channels \
    --channel https://repo.anaconda.com/pkgs/main 2>/dev/null || true
yes 2>/dev/null | conda tos accept --override-channels \
    --channel https://repo.anaconda.com/pkgs/r    2>/dev/null || true

if conda env list | awk '{print $1}' | grep -qx "$CONDA_ENV"; then
    log "Conda env '$CONDA_ENV' already exists."
else
    log "Creating conda env '$CONDA_ENV' on Python $PY_VERSION..."
    conda create -y -n "$CONDA_ENV" "python=$PY_VERSION"
fi

conda activate "$CONDA_ENV"

# ---------- 5. AnimatedDrawings ---------------------------------------------
if [[ ! -d "$AD_ROOT/.git" ]]; then
    log "Cloning AnimatedDrawings into $AD_ROOT ..."
    mkdir -p "$(dirname "$AD_ROOT")"
    git clone --depth 1 "$AD_REPO" "$AD_ROOT"
else
    log "AnimatedDrawings checkout already at $AD_ROOT."
fi

# Install into the conda env. Re-running pip install -e is cheap if nothing
# changed; it just resolves and confirms.
if ! python -c "import animated_drawings" 2>/dev/null; then
    log "pip-installing AnimatedDrawings (editable) into '$CONDA_ENV'..."
    (cd "$AD_ROOT" && pip install -e .)
else
    log "animated_drawings already importable in '$CONDA_ENV'."
fi

# ---------- 6. torchserve docker image ---------------------------------------
build_image=true
if command -v docker >/dev/null 2>&1; then
    if docker image inspect "$TS_IMAGE" >/dev/null 2>&1; then
        log "Docker image '$TS_IMAGE' already built."
        build_image=false
    fi
else
    warn "docker CLI not yet usable in this shell (group change pending). "
    warn "Skipping image build — animate.sh will build it on first run."
    build_image=false
fi

if [[ "$build_image" == "true" ]]; then
    log "Building docker image '$TS_IMAGE' (5-10 minutes)..."
    if docker info >/dev/null 2>&1; then
        (cd "$AD_ROOT/torchserve" && docker build -t "$TS_IMAGE" .)
    else
        warn "docker daemon not reachable from this shell. The image will be"
        warn "built automatically the first time you run animate.sh."
    fi
fi

# ---------- done -------------------------------------------------------------
cat <<EOF

============================================================
  Setup complete.
============================================================

  Conda env:       $CONDA_ENV  (Python $PY_VERSION)
  AnimatedDrawings: $AD_ROOT
  Docker image:    $TS_IMAGE

  Next steps:
    1. Place your drawing as ./stick.jpg in this directory.
    2. If you were just added to the 'docker' group, log out & back in.
    3. Run:   ./animate.sh
       Or:    ./animate.sh --motion dab --verbose

EOF
