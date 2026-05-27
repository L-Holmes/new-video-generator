

# SETUP

sudo apt-get update
sudo apt-get install -y docker-cli
chmod +x setup.sh animate.sh
./setup.sh
<ensure there is a stick.jpg in the dir>
./animate.sh --vrebose






may have to do:

source ~/miniconda3/etc/profile.d/conda.sh
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
newgrp docker
./setup.sh           # picks up from where it failed; takes ~10 min for the docker build



and:


sudo usermod -aG docker $USER
newgrp docker









ontainerd.sock
newgrp docker
./animate.sh
 (see README), ensure `systemctl status docker` is active, and verify that your user is in the `docker` group (you may need to log out and back in after `sudo usermod -aG docker $USER`).
sudo usermod -aG docker $USER
./animate.sh
 (see README), ensure `systemctl status docker` is active, and verify that your user is in the `docker` group (you may need to log out and back in after `sudo usermod -aG docker $USER`).
groups | grep -q docker && echo "✓ in docker group" || echo "✗ NOT in docker group"

systemctl is-active docker

docker info >/dev/null 2>&1 && echo "✓ docker reachable" || echo "✗ docker NOT reachable"

sudo chmod 666 /var/run/docker.sock    # temporary, until reboot
newgrp docker
./animate.sh --verbose
15:20:00  ERROR    Pipeline failed: Docker daemon not reachable. Install docker (see README), ensure `systemctl status docker` is active, and verify that your user is in the `docker` group (you may need to log out and back in after `sudo usermod -aG docker $USER`).



# Stickman Animator

A small, automated local pipeline that takes a static 2D stickman drawing
(`stick.jpg`) and renders a short MP4 of it doing a subtle, natural motion
(a gentle wave by default).

Under the hood it wraps Meta FAIR's
[**AnimatedDrawings**](https://github.com/facebookresearch/AnimatedDrawings)

The pipeline:

1. Spins up the bundled TorchServe Docker container that hosts the pose
   estimator and humanoid detector.
2. Sends `stick.jpg` to it and receives a bounding box, segmentation mask
   and joint positions.
3. Synthesises an MVC config that binds the rigged character to a BVH
   motion clip and turns on OSMesa offscreen rendering.
4. Renders to `stick-animated.mp4` and cleans up.

---

## Hardware tested against

| Spec | Value |
|---|---|
| Machine | ThinkPad L13 Gen 4 (21FQ) |
| CPU | AMD Ryzen 7 PRO 7730U (8C/16T) |
| GPU | Radeon iGPU (Barcelo), `amdgpu` driver |
| RAM | 32 GiB |
| Storage | 477 GiB NVMe (Samsung PM9B1) |
| OS | Debian 13 "Trixie" |
| Kernel | 6.12 |

The pipeline is CPU-only by design. Rendering goes through OSMesa
(software OpenGL), so it works on a headless server too. On the spec above
a 5-second wave renders in roughly 60-90 seconds.

---

## Prerequisites

### System packages (installed automatically by `setup.sh`)

Installed via `apt-get`:

| Package | Why it's needed |
|---|---|
| `build-essential`, `git`, `curl`, `wget`, `ca-certificates` | Toolchain to build pip wheels and fetch sources. |
| `ffmpeg` | Encodes the rendered frames into an MP4. AnimatedDrawings shells out to it. |
| `libgl1`, `libglu1-mesa`, `libglfw3` | Core OpenGL + GLFW runtime. GLFW is imported even in headless mode. |
| `libosmesa6`, `libosmesa6-dev` | **Headless offscreen OpenGL.** This is what `PYOPENGL_PLATFORM=osmesa` binds to. Without it, render fails with "Attempt to call an undefined function glutInit". |
| `libglib2.0-0`, `libsm6`, `libxext6`, `libxrender1`, `libxi6` | Transitive deps of OpenCV / GLFW. |
| `libsndfile1` | Pulled in by `scipy`/`librosa` chain in the AnimatedDrawings dependency tree. |
| `docker.io` | Runs the TorchServe container that holds the ML models. |

### Python toolchain

| Tool | Version | Why |
|---|---|---|
| Miniconda | latest | The only sane way to get **Python 3.8.13** on Debian 13 (which ships 3.13). Installed into `~/miniconda3` by the setup script. |
| Python | **3.8.13** (pinned) | AnimatedDrawings was authored against 3.8; newer Pythons break some of its pinned scientific-Python deps. |

### Python packages (installed via `pip install -e .` inside the conda env)

You don't install these by hand — `pip install -e .` against the
AnimatedDrawings checkout pulls them. Listed here for transparency:

`numpy`, `scipy`, `scikit-image`, `opencv-python`, `Pillow`, `PyOpenGL`,
`glfw`, `Shapely`, `requests`, `PyYAML`, `torch`, `torchserve`,
`torch-model-archiver`, `flask` (used by the optional joint-correction UI).

### Disk + RAM budget

| | Min | Recommended |
|---|---|---|
| Disk | ~6 GB | ~10 GB (Docker image is ~3.5 GB, conda env ~2 GB, models ~500 MB) |
| RAM | 4 GB free for TorchServe alone | 8 GB+ |

Your machine (32 GiB / 477 GiB) is well over.

---

## Installation

```bash
# 1. Make the scripts executable (only needed once)
chmod +x setup.sh animate.sh

# 2. Run the one-shot bootstrap. This is idempotent — re-run it any time.
./setup.sh
```

`setup.sh` will, in order:

1. `apt-get install` the system packages above (requires sudo).
2. Create a `docker` group if missing and add you to it.
3. Install Miniconda into `~/miniconda3` if not already there.
4. Create a conda env named `animated_drawings` on Python 3.8.13.
5. `git clone` AnimatedDrawings into `~/animated_drawings_src` and
   `pip install -e .` it into the env.
6. `docker build` the `docker_torchserve` image from the cloned repo
   (this is the long step — ~5–10 minutes the first time).

**Note about the docker group:** the first time you're added to the
`docker` group, you have to log out and back in (or run `newgrp docker`)
before `docker` commands work without `sudo`. If `setup.sh` can't talk to
the daemon yet, it skips the image build and `animate.sh` will build it on
first run instead.

### Customising install paths

All paths are env-var-driven, no hardcoded `/opt/...`:

```bash
ANIMATED_DRAWINGS_ROOT=/data/ad CONDA_DIR=/opt/conda ./setup.sh
```

The same variables are read by `animate.sh` and `animate_stickman.py`, so
once you've set them at install time you can keep using them.

---

## Usage

### The 10-second version

```bash
# put your drawing in this directory as stick.jpg, then:
./animate.sh
# -> writes ./stick-animated.mp4
```

### Full CLI

```bash
./animate.sh --help
```

| Flag | Default | Notes |
|---|---|---|
| `--input`, `-i` | `./stick.jpg` | Path to the drawing. PNG, JPG, BMP all work. |
| `--output`, `-o` | `./stick-animated.mp4` | Output MP4 path; parent dirs are created. |
| `--motion`, `-m` | `wave_hello` | One of `wave_hello`, `dab`, `jumping`, `zombie`. `wave_hello` is the subtlest. |
| `--keep-workdir` | off | Don't delete the temp dir holding `mask.png` / `char_cfg.yaml`. Useful for debugging bad rigs. |
| `--verbose`, `-v` | off | DEBUG logging. |

### What you should expect

```
$ ./animate.sh
14:02:11  INFO     TorchServe container 'docker_torchserve' already running.
14:02:11  INFO     Waiting for TorchServe at localhost:8080 to report healthy...
14:02:11  INFO     TorchServe is healthy.
14:02:11  INFO     Asking TorchServe to detect, segment and rig the character...
14:02:14  INFO     Annotations written to /tmp/stickman_animator_xyz/character
14:02:14  INFO     Rendering to /home/main/stick-animated.mp4 (this is the slow step, ~30-90s)...
14:03:19  INFO     Done. Output: /home/main/stick-animated.mp4 (842.3 KB)
```

### Drawing guidelines for good results

The pose estimator was trained on children's drawings. It handles a wide
range of styles but it does have failure modes:

* **Solid white background, thick dark strokes.** A stickman on lined
  paper or with very thin pencil lines often confuses the segmentation.
* **One figure per image.** Multi-character mode exists but isn't wired up
  in this pipeline.
* **Standing T-pose-ish posture.** Roughly upright, with arms and legs
  visible and non-overlapping, gives the best rig.

If the joints come out wrong, re-run with `--keep-workdir`, then point
AnimatedDrawings' built-in joint editor at the work dir:

```bash
conda activate animated_drawings
cd ~/animated_drawings_src/examples
python fix_annotations.py /tmp/stickman_animator_xxx/character
# opens a Flask UI at http://127.0.0.1:5050 — drag joints, click Submit
```

---

## Troubleshooting

### `Failed to get bounding box, please check if the 'docker_torchserve' is running and healthy`

The TorchServe container is up but the model is OOM-killing or hasn't
finished loading. Check:

```bash
docker logs --tail 60 docker_torchserve
docker stats docker_torchserve   # watch RAM live
```

If RAM is the issue, the container needs at least 4 GiB. On the target
machine (32 GiB) this is rarely a problem.

### Render fails with `OpenGL.error.NullFunctionError: ... glutInit`

OSMesa isn't installed or `PYOPENGL_PLATFORM` isn't set. `animate.sh`
exports it for you; if you're invoking `animate_stickman.py` directly:

```bash
apt list --installed 2>/dev/null | grep osmesa  # should show libosmesa6
export PYOPENGL_PLATFORM=osmesa
```

### `permission denied while trying to connect to the Docker daemon socket`

You're not in the `docker` group yet, or you haven't started a fresh
session since being added. Run `newgrp docker` or log out and back in.

### `command not found: conda` after running setup.sh

The setup script deliberately doesn't run `conda init` against your
shell rc — it doesn't want to silently edit your `.bashrc`. Use
`./animate.sh` (which sources conda for you) or add this to your shell
rc yourself:

```bash
source ~/miniconda3/etc/profile.d/conda.sh
```

### Want to nuke everything and start fresh

```bash
docker rm -f docker_torchserve
docker rmi docker_torchserve
conda env remove -n animated_drawings
rm -rf ~/animated_drawings_src ~/miniconda3
```

Then re-run `./setup.sh`.

---

## File layout

```
stickman-animator/
├── README.md              ← this file
├── setup.sh               ← one-time bootstrap (apt, conda, clone, docker build)
├── animate.sh             ← daily-driver wrapper (activates env, sets OSMesa, runs Python)
├── animate_stickman.py    ← the actual pipeline
└── stick.jpg              ← (you provide this)
```

Nothing is installed inside this directory — the conda env, the
AnimatedDrawings checkout, Miniconda, and the Docker image all live in
well-known locations under `$HOME` (or wherever your env vars point).
Deleting this directory leaves no orphans behind on disk apart from the
items listed in the "nuke everything" section above.

---

## Licensing notes

* This wrapper code: do whatever you want with it.
* AnimatedDrawings itself: [MIT](https://github.com/facebookresearch/AnimatedDrawings/blob/main/LICENSE).
* The shipped BVH motion clips (`wave_hello`, `dab`, `jumping`, `zombie`)
  ship with the AnimatedDrawings repo under the same MIT licence.

