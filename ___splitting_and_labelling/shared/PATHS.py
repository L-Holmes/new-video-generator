"""
PATHS.py — put every folder in this package on sys.path.

    import PATHS  # noqa: F401     (anywhere under ___splitting_and_labelling)

WHY THIS FILE EXISTS
    The stages are folders named "0-sentence-splitter", "2-auto-tagging" ...
    — they start with a digit and contain a hyphen, so they can never be
    Python package names and `from 0-sentence-splitter import ...` is not a
    thing that can ever be written. Every cross-folder import therefore goes
    through sys.path, and this is the ONE place that list is written down.

    Importing it is a side effect on purpose: there is nothing to call.

HOW TO IMPORT IT
    It lives in shared/, so reach that folder first and then import it —
    three lines, the same three everywhere, only the number of .parent's
    changing with how deep the file is:

        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                               / "shared"))       # a stage folder: one up
        import PATHS  # noqa: F401,E402

    (A file in 0-sentence-splitter/testing/ is two up, __init__.py is none.)

THE FOLDERS
    The pipeline, in the order it runs:
        0-sentence-splitter           script         -> line segments
        1-visualisable-identification line segments  -> what to put on screen
        2-auto-tagging                the easy rows, filled automatically
        3-manual-tagging              you, filling in the rest, in a browser

    And the ones that are not a stage:
        shared            what more than one stage needs: this file, the
                          word lists (shared_text_logic), the media-type
                          catalog (MEDIA_TYPES)
        documentation     every .md
        prompts           the llm prompt templates

    main.py at the package root runs 0 -> 1 -> 2 -> 3 in order.

    A thing two stages both import belongs in shared/, not in the lower-
    numbered stage — a stage folder owning something another stage needs is
    how a "which number does this belong to" argument starts.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent      # ___splitting_and_labelling
REPO_ROOT = HERE.parent                            # for CONFIG.py

SENTENCE_SPLITTER_DIR = HERE / "0-sentence-splitter"
VISUALISABLES_DIR     = HERE / "1-visualisable-identification"
AUTO_TAGGING_DIR      = HERE / "2-auto-tagging"
MANUAL_TAGGING_DIR    = HERE / "3-manual-tagging"

STAGE_DIRS = [SENTENCE_SPLITTER_DIR, VISUALISABLES_DIR,
              AUTO_TAGGING_DIR, MANUAL_TAGGING_DIR]

SHARED_DIR        = HERE / "shared"
DOCUMENTATION_DIR = HERE / "documentation"
PROMPTS_DIR       = HERE / "prompts"

# Last in the list ends up FIRST on sys.path, so this order is deliberate:
# the repo root goes on furthest back and the package's own folders sit in
# front of it, which is what makes a stage's module win over a same-named
# one further out.
for _d in [REPO_ROOT, HERE, SHARED_DIR] + STAGE_DIRS:
    _s = str(_d)
    if _s not in sys.path:
        sys.path.insert(0, _s)
