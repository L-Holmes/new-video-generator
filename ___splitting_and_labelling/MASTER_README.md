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
