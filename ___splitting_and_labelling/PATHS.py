"""
PATHS.py — put every stage folder on sys.path.

    import PATHS  # noqa: F401     (anywhere under ___splitting_and_labelling)

WHY THIS FILE EXISTS
    The stages are folders named "0-sentence-splitter", "2-auto-tagging" ...
    — they start with a digit and contain a hyphen, so they can never be
    Python package names and `from 0-sentence-splitter import ...` is not a
    thing that can ever be written. Every cross-stage import therefore goes
    through sys.path, and this is the ONE place that list is written down.

    Importing it is a side effect on purpose: there is nothing to call.

THE STAGES, in the order the pipeline runs them
    0-sentence-splitter          script      -> line segments
    1-visualisable-identification line segments -> what to put on screen
    2-auto-tagging               the easy rows filled in automatically
    3-manual-tagging             you, filling in the rest, in a browser

    Anything SHARED by more than one stage stays here at the top level
    (shared_text_logic.py, MEDIA_TYPES.py, SPLIT_AND_LABEL.py), because a
    stage folder owning something two other stages import is how a "which
    number does this belong to" argument starts.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent                     # for CONFIG.py

SENTENCE_SPLITTER_DIR = HERE / "0-sentence-splitter"
VISUALISABLES_DIR     = HERE / "1-visualisable-identification"
AUTO_TAGGING_DIR      = HERE / "2-auto-tagging"
MANUAL_TAGGING_DIR    = HERE / "3-manual-tagging"

STAGE_DIRS = [SENTENCE_SPLITTER_DIR, VISUALISABLES_DIR,
              AUTO_TAGGING_DIR, MANUAL_TAGGING_DIR]

# Last in the list ends up FIRST on sys.path, so this order is deliberate:
# the repo root goes on last-but-nothing and the stage folders sit in front
# of it, which is what makes a stage's own module win over a same-named one
# further out.
for _d in [REPO_ROOT, HERE] + STAGE_DIRS:
    _s = str(_d)
    if _s not in sys.path:
        sys.path.insert(0, _s)
