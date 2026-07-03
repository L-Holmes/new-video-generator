"""
MEDIA_TYPES.py — the tagging tool's view of the media-type catalog.

The catalog itself now lives in ONE place shared with the video builder:
    ___visuals/MEDIA_CATALOG.py
(re-exported by ___visuals/CONFIG.py for the renderer). This file just
imports it, so the buttons/colours/info in MANUAL_TAGGING and the types the
renderer understands can never drift apart.

To add a media type: add ONE entry to MEDIA_TYPE_CATALOG in
___visuals/MEDIA_CATALOG.py (and give the renderer its enum + property row
in ___visuals/CONFIG.py — CONFIG refuses to import if they drift). It then
appears in MANUAL_TAGGING automatically: button, colour, info popup, key.
Optionally drop an example image at examples/<name>.png.

The public names below (Tag, MEDIA_TYPES, MODIFIERS, to_legacy) are exactly
what MANUAL_TAGGING and the tests import — unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

# This folder (___splitting_and_labelling) sits next to ___visuals in the
# repo root — put the root on sys.path so the shared catalog imports whether
# you run from the repo root or from inside this folder.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from ___visuals.MEDIA_CATALOG import (  # noqa: F401  (re-exports)
        MEDIA_TYPE_CATALOG as MEDIA_TYPES,
        MODIFIERS,
        Tag,
        to_legacy,
    )
except ImportError as exc:  # pragma: no cover - setup guidance
    raise ImportError(
        "MEDIA_TYPES.py could not import the shared catalog from "
        "___visuals/MEDIA_CATALOG.py. This folder must live next to "
        "___visuals in the repo (or add the repo root to PYTHONPATH). "
        f"Underlying error: {exc}"
    ) from exc
