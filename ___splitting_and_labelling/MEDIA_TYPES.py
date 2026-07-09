"""
MEDIA_TYPES.py — the tagging tool's view of the media-type catalog.

The catalog lives in ONE place, inside the video builder's config:
    CONFIG.py   (repo root — the MEDIA TYPES section near the top)
This file just imports it, so the buttons/colours/info in MANUAL_TAGGING
and the types the renderer understands can never drift apart.

To add a media type: one entry in MEDIA_TYPE_CATALOG plus its enum property
row in MEDIA_PROPERTIES — both in CONFIG.py, right next to each
other (CONFIG refuses to import if they drift). It then appears in
MANUAL_TAGGING automatically: button, colour, info popup, key. Optionally
drop an example image at examples/<name>.png.

Importing CONFIG from here is side-effect free: it parses no relevant argv
(parse_known_args ignores the json filename) and creates no directories
(dir creation moved into CONFIG.ensure_runtime_dirs, called by main.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

# This folder (___splitting_and_labelling) sits next to ___visuals in the
# repo root — put the root on sys.path so the shared config imports whether
# you run from the repo root or from inside this folder.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from CONFIG import (  # noqa: F401  (re-exports)
        COLLAGEABLE_TYPES,
        GROUPABLE_TYPES,
        MEDIA_TYPE_CATALOG as MEDIA_TYPES,
        MODIFIERS,
        STAMP_SOURCE_TYPES,
        TERM_OPTIONAL_TYPES,
        Tag,
    )
except ImportError as exc:  # pragma: no cover - setup guidance
    raise ImportError(
        "MEDIA_TYPES.py could not import the shared catalog from "
        "CONFIG.py at the repo root (or add the repo root to PYTHONPATH). "
        f"Underlying error: {exc}"
    ) from exc
