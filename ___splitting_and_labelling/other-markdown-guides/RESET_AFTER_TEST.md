# RESET AFTER A TEST RUN  (Debian 13, run inside this directory)

## What's safe to delete

Everything a test/sample run creates is regenerable: `*-CACHE/` dirs
(split caches + per-project search-text state), `TESTING_*` outputs, the
bundled sample scripts, `fixture_run/` (yes — that's a test artifact from
`test_pipeline_fixture.py`, delete it freely), and `__pycache__`.

## Reset command

```bash
rm -rf ./*-CACHE ./fixture_run ./__pycache__ \
       ./TESTING_*-script_to_search_term.json \
       ./script-spices.txt ./script-whales.txt ./script-rome.txt
```

## NEVER delete

`MASTER_RULES.md` (your learned rules), `prompts/` (base rules + prompt
templates), `ENTITY_FAME_CACHE.json` (fame data), and any non-TESTING
project JSONs you care about.

Note: a project's `<prefix>-CACHE/search-text/PROJECT_RULES.md` holds rules
you taught but haven't merged yet — hit `f` in ADD_SEARCH_TEXT.py to merge
them into master BEFORE resetting, or they're gone with the cache.

## Remove the whole test suite

When you no longer want the tests around:

```bash
rm -f test_pipeline_fixture.py test_v18_rules.py
rm -rf fixture_run
```

(`calibrate_against_golden.py` is a tuning tool, not a test — keep it if
you still compare runs against a hand-made golden JSON; otherwise it can go
the same way.)

## Force a clean re-split of one project

The split cache is versioned (`SPLITMETA4-…`), so schema changes never
poison you — but to re-split the same script after editing it:

```bash
rm -rf ./<prefix>-CACHE ./TESTING_<prefix>-script_to_search_term.json
```
