# TESTING-RESOURCES

Every test in the repo, and every fixture a test needs. Nothing here runs as
part of a real render, and nothing here is imported by production code — the
one exception is `AUTO_TAG_SELFTEST.py`, which
`Auto_add_mediatypes.py --selftest` reaches for by adding this folder to
`sys.path` for that one call.

```
test_integration.py                the whole tag-based scene model, end to
                                   end, on stubs. Writes into testrun/ (which
                                   it wipes first) and never touches .CACHE/.
images/                            pictures + clips the tests composite with,
                                   including stock-cinematic-test-stock/ which
                                   colour_grade_etc.py grades as its preview
scripts/                           narration used by tests only. REAL scripts
                                   live in SCRIPTS/ at the repo root.
splitting_and_labelling/           stage 1's tests, one folder per stage
    sentence-splitter/             stage 0: rule tests + the two diagnostics
    visualisable-identification/   stage 1: TEST_MANUAL_INTERPRETATION.py and
                                   the results file it overwrites
    auto-tagging/                  stage 2: TEST_AUTO_TAGGER.py, the ten
                                   fixtures in test_jsons/, AUTO_TAG_SELFTEST
    TEST_RESULTS/                  where a DIRECT `uv run
                                   ___splitting_and_labelling/main.py` writes
```

## running them

```
uv run TESTING-RESOURCES/test_integration.py
uv run ___splitting_and_labelling/2-auto-tagging/Auto_add_mediatypes.py --selftest

TAGGER=TESTING-RESOURCES/splitting_and_labelling/auto-tagging/TEST_AUTO_TAGGER.py
uv run $TAGGER --list
uv run $TAGGER --test 1 --no-manual
uv run $TAGGER --all

SPLIT=TESTING-RESOURCES/splitting_and_labelling/sentence-splitter
uv run $SPLIT/test_v18_rules.py
uv run $SPLIT/slentence_tester.py
uv run $SPLIT/run_diagnostics_on_sentence_splitter.py

uv run TESTING-RESOURCES/splitting_and_labelling/visualisable-identification/TEST_MANUAL_INTERPRETATION.py
```

## how these files find the code they test

The stage folders are named `0-sentence-splitter`, `2-auto-tagging` … — no
`import` statement can name them, so every one of these files reaches
`___splitting_and_labelling/shared/PATHS.py` first and lets it put the
stage folders on `sys.path`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]
                       / "___splitting_and_labelling" / "shared"))
import PATHS  # noqa: F401,E402
```

`parents[3]` is the repo root from a file three folders down
(`TESTING-RESOURCES/splitting_and_labelling/<stage>/x.py`). PATHS also names
each folder here — the `TESTING_*` constants — so production code that needs
one (only the selftest) has somewhere to look it up rather than guessing.

## what is safe to delete

`testrun/` and `splitting_and_labelling/TEST_RESULTS/` are both generated and
both gitignored. Everything else is a fixture: `test_jsons/untagged/` is the
pristine copy every `--test <n>` run resets from, so losing it means
rebuilding the fixtures with `--build --force`.
