# RESET AFTER A TEST RUN

(Debian 13, run from ___splitting_and_labelling — the package root, one
level up from this file.)

## safe to delete, all regenerable

* TEST_RESULTS/ — the whole lot. `uv run main.py` writes everything a
  direct run produces in there, caches included, and rebuilds it.
* any TESTING_* jsons and *-CACHE folders left in the working directory by
  a library caller (the repo's main.py)
* __pycache__ (every folder gets one)

## reset command

```bash
rm -rf ./TEST_RESULTS
find . -name __pycache__ -type d -prune -exec rm -rf {} +
```

## never delete

main.py, __init__.py, shared/, prompts/, documentation/, the four
numbered stage folders, your scripts and any non-TESTING project jsons.
1-visualisable-identification/knowledge_base_data.json is NOT regenerable
without internet — build_wordlists.py next to it re-fetches it.

## where the tests are

Each stage keeps its own, so there is no one folder to delete:

* `0-sentence-splitter/testing/` — the splitter's rule tests
* `2-auto-tagging/TEST_AUTO_TAGGER.py` — the ten fixture scripts
* `1-visualisable-identification/TEST_MANUAL_INTERPRETATION.py`
