# RESET AFTER A TEST RUN  (Debian 13, run inside this directory)

## safe to delete, all regenerable

* TESTING_* jsons and their .bak files
* the split caches under *-CACHE (only costs a spaCy re-run to rebuild)
* the bundled sample scripts
* __pycache__ and testing/fixture_run (test artifacts)

## reset command

```bash
rm -rf ./*-CACHE ./__pycache__ ./testing/fixture_run \
       ./TESTING_*-script_to_search_term.json ./TESTING_*.json.bak \
       ./script-spices.txt ./script-whales.txt ./script-rome.txt
```

## never delete

MEDIA_TYPES.py, prompts/, BASE_RULES.md, MASTER_RULES.md, your scripts and
any non-TESTING project jsons.

## remove the whole test suite

```bash
rm -rf testing
```
