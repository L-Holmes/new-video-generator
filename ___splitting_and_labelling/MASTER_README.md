
uv run SPLIT_AND_LABEL.py
uv run ADD_SEARCH_TEXT.py
uv run MANUAL_TAGGING.py



# MASTER README — script → shot list → search terms

Three primary files, run in this directory:

## 1. Decide the shots

```
uv run SPLIT_AND_LABEL.py
```

Splits each bundled/`script-*.txt` narration into visual-beat lines (spaCy;
needs `en_core_web_sm` installed once: `uv run python -m spacy download
en_core_web_sm`) and walks every line down the decision ladder (gates →
locks → nudges → weighted sample). Writes
`[TESTING_]<prefix>-script_to_search_term.json` with the legacy renderer
columns, the shot template/axes, the tier + why audit trail, the splitter's
`rule_ids` — and **empty `search_term` fields, on purpose**.

To run your own script: drop `script-<name>.txt` here and call
`generate_script_to_search_term("script-<name>.txt")`, or add it to
`_SAMPLE_SCRIPTS`. Optional once per project:
`uv run populate_entity_fame.py --from-splitmeta <the SPLITMETA cache>` to
teach the famous/obscure locks real data. Toggle `AI_ENABLED` in
`SPLIT_AND_LABEL.py` for the stickman/AI shot family.

## 2. Fill the search terms (AI + your rules)

```
uv run ADD_SEARCH_TEXT.py
```

Interactive loop: `p` exports a prompt (base rules + learned rules + the
JSON), you paste it into your LLM and save its JSON reply, `i <file>`
imports it, you select any bad entries and `e` teach a rule (what should go
there + why), `p` again for a revision pass on just those, and `f` merges
what you taught into `MASTER_RULES.md` so every future project starts
smarter. Full docs: `prompts/README.md`, tool help: `h`.

## 3. Hand-tag anything (point and click)

```
uv run MANUAL_TAGGING.py
```

Opens a localhost page on the same JSON: the whole script scrollable on the
left for context (the focused line shows its neighbours dimmed above/below),
media-type buttons grouped NEW / EDIT PREVIOUS / EDIT GROUP with the AI
family in red shades (expandable key on the page explains the grouping),
optional overlay layering ON a chosen base (+ caption / + draw / + object
edit — disabled until there's something to decorate), and a greyed
click-to-expand search-term pane with quick-append chips (the nouns, places,
names and keywords the splitter extracted from that line). Works in either
order — type first or term first. Every change saves instantly (one .bak per
session) and is marked `manual` so the AI prompts and calibration can tell
your choices from sampled ones. Use it before or after step 2, in any mix:
hand-set the lines you care about, let the AI fill the rest.

## Files that PERSIST (never delete)

`MASTER_RULES.md`, `prompts/`, `ENTITY_FAME_CACHE.json`, your scripts and
finished JSONs.

## Housekeeping

Resetting after a test run, and removing the test suite: see
`RESET_AFTER_TEST.md`. Extending anything (media types, splitter rules,
locks, pacing): `EXTENDING_GUIDE.md`. Retiring the legacy renderer strings:
`TODO_LEGACY_SWITCHOVER.md`.
