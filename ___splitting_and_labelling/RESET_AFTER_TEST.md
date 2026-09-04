# RESET AFTER A TEST RUN  (Debian 13, run inside this directory)

## safe to delete, all regenerable

* TESTING_* jsons and their .bak files
* the split caches under *-CACHE (only costs a spaCy re-run to rebuild)
* the bundled sample scripts
* __pycache__ (every stage folder has one) and testing/fixture_run

## reset command

```bash
rm -rf ./*-CACHE ./testing/fixture_run \
       ./TESTING_*-script_to_search_term.json ./TESTING_*.json.bak \
       ./script-spices.txt ./script-whales.txt ./script-rome.txt
find . -name __pycache__ -type d -prune -exec rm -rf {} +
```

## never delete

MEDIA_TYPES.py, PATHS.py, prompts/, BASE_RULES.md, MASTER_RULES.md, the
four numbered stage folders, your scripts and any non-TESTING project jsons.
1-visualisable-identification/knowledge_base_data.json is NOT regenerable
without internet — build_wordlists.py next to it re-fetches it.

## remove the whole test suite

```bash
rm -rf testing
```
