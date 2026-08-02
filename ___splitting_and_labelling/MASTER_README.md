uv run SPLIT_AND_LABEL.py
uv run MANUAL_TAGGING.py


# MASTER README

Two steps, run in this directory.

## 1. split the script

```
uv run SPLIT_AND_LABEL.py
```

Splits each script-*.txt into visual-beat lines (spaCy; install the model
once with: uv run python -m spacy download en_core_web_sm) and writes
[TESTING_]<prefix>-script_to_search_term.json. Every line's media_type and
search_term start EMPTY — there is no automatic tagging. Each line carries
the splitter's rule_ids for context. Field-by-field docs: FORMAT.md.

## 2. tag everything by hand

```
uv run MANUAL_TAGGING.py
```

Opens a localhost page: pick one media type per line (grouped NEW / EDIT
PREVIOUS, ai family in red, info icons and a key explaining everything),
stack decorate / caption / group on top, write the search term (ghost
suggestion with tab-to-accept, tap-to-append noun and place chips), split a
line at any character with the golden cursor, join a line to the one above,
undo anything. Groups show as a shared coloured stripe. Works on phones.
Saves instantly.

Optional ai help: the prompt templates in prompts/ take the json plus the
rule lists (BASE_RULES.md and your own MASTER_RULES.md) so an llm can draft
the search terms for you; paste its json reply over the file and review in
MANUAL_TAGGING.

Never delete: MEDIA_TYPES.py, prompts/, BASE_RULES.md, MASTER_RULES.md,
your scripts and finished jsons. Everything under *-CACHE and TESTING_* is
regenerable (see other-markdown-guides/RESET_AFTER_TEST.md).


# Testing

## trying the auto tagger on the ten fixture scripts

```
uv run TEST_AUTO_TAGGER.py --list        what the ten fixtures are
uv run TEST_AUTO_TAGGER.py --test 1      fixture 1: auto-tag it, then open it
```

One fixture at a time. `--test <n>` resets test_jsons/test_json_<n>.json to
its pristine untagged copy, runs Auto_add_mediatypes.py over it (the full
detection table and flowchart printout), prints a line-by-line review of what
it decided, then opens MANUAL_TAGGING.py on that same file so you can see the
result as a page. Ctrl-C the tagger when you have finished with that one and
run the next number. Nothing you do there touches a real script.

The ten are different KINDS of short video on purpose — a mystery, a recipe,
sports stats, a personal story, a money video, a nature fact, a product
review, a travel guide, a history mystery, and a piece of advice with almost
nothing filmable in it. Each is split by the real sentence splitter, so they
arrive with real rule_ids, list runs and reveals, exactly like step 1's
output.

```
uv run TEST_AUTO_TAGGER.py --test 4 --no-manual   printout only, no browser
uv run TEST_AUTO_TAGGER.py --test 4 --keep        carry on, don't reset
uv run TEST_AUTO_TAGGER.py --all                  all ten + a scoreboard,
                                                  no browser — what you watch
                                                  while changing STEP 2 rules
uv run TEST_AUTO_TAGGER.py --build --force        re-split the scripts (only
                                                  after a splitter change)
```

Judge it by the house rule: a row left empty is fine, a row tagged WRONG is
the expensive one. Fixtures, sources and what's regenerable: test_jsons/README.md.
