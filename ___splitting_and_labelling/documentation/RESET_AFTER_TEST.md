# RESET AFTER A TEST RUN

(Debian 13, run from the REPO ROOT — the folder with main.py in it. Every
path below is relative to that.)

## safe to delete, all regenerable

* `TESTING-RESOURCES/splitting_and_labelling/TEST_RESULTS/` — the whole lot.
  `uv run ___splitting_and_labelling/main.py` writes everything a direct run
  produces in there, caches included, and rebuilds it.
* `.CACHE/` — every run's scratch space, one `<name>-CACHE` per --name.
  Deleting it costs a re-split and a re-download, nothing else.
* `SCRIPTS/TESTING_*-script_to_search_term.json` — the TESTING_-prefixed shot
  lists a direct run leaves behind. The un-prefixed ones are REAL projects.
* `__pycache__` (every folder gets one)

## reset command

```bash
rm -rf TESTING-RESOURCES/splitting_and_labelling/TEST_RESULTS .CACHE
find . -name __pycache__ -type d -prune -exec rm -rf {} +
```

## never delete

main.py, __init__.py, shared/, prompts/, documentation/, the four numbered
stage folders, SCRIPTS/ (your scripts and any non-TESTING project jsons),
.resources/. 1-visualisable-identification/knowledge_base_data.json is NOT
regenerable without internet — build_wordlists.py next to it re-fetches it.

## where the tests are

All of them, in one place, mirroring the stage folders:

```
TESTING-RESOURCES/splitting_and_labelling/
    sentence-splitter/               stage 0's rule tests
    visualisable-identification/     stage 1: TEST_MANUAL_INTERPRETATION.py
    auto-tagging/                    stage 2: TEST_AUTO_TAGGER.py + the ten
                                     fixtures in test_jsons/, and
                                     AUTO_TAG_SELFTEST.py (Auto_add_mediatypes
                                     --selftest runs this one)
    TEST_RESULTS/                    where a direct run writes
```

shared/PATHS.py names each of those folders (the TESTING_* constants).
