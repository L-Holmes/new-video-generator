uv run main.py


# MASTER README

One command, run in this directory:

```
uv run main.py
```

That is all four stages in order — split, work out the visualisables,
auto-tag the easy rows, then open the browser for whatever is left.

## the folders

One numbered folder per stage of the pipeline, in the order it runs:

```
0-sentence-splitter/            script            -> line segments
1-visualisable-identification/  line segments     -> what to put on screen
2-auto-tagging/                 the easy rows, filled in automatically
3-manual-tagging/               you, filling in the rest, in a browser
```

### how stage 1 feeds 2 and 3

Stage 1 reads the WHOLE script with coreference models, so it knows what
every "it" points at and how a thing looks by now. Both tagging stages get
that instead of guessing from one line at a time:

* **search terms.** The flowchart goes first, because where it fires it
  knows something stage 1 does not (a quote is the caption text, a figure is
  chart data), and its term is never overwritten. Stage 1 then fills every
  term still empty. Most rows reach the browser needing a media-type CLICK
  and nothing else.
* **"is this the same scene?"** When every thing in a line was already on
  screen, stage 1 says so and the flowchart edits the picture that is there
  (`hold_previous` + `decorate`) instead of paying for a second stock clip
  of the same subject. See `SAME_SCENE_AS_PREVIOUS` in
  `2-auto-tagging/Auto_add_mediatypes.py`.
* **the chips** in the browser are the same answers, ranked.

The translation lives in
`1-visualisable-identification/VISUALISABLE_SEARCH_TERMS.py`. Run the
auto-tagger standalone on a json and stage 1 is simply absent: both
attributes read False and the flowchart decides from its own single-line
detectors, exactly as it did before stage 1 existed.

And the folders that are not a stage:

```
shared/             what more than one stage needs: PATHS.py,
                    shared_text_logic.py (the word lists),
                    MEDIA_TYPES.py (the catalog)
documentation/      every .md, including this one
prompts/            the llm prompt templates
```

Two files sit loose at the root, and both have to. `main.py` is what you
run — it is the thing the folders are numbered FOR. `__init__.py` names the
folder it sits in, so it cannot move without the package moving with it.

Read `shared/PATHS.py` before you add a file. It is why
`import sentence_splitter` still works from a folder called
`0-sentence-splitter`, and it has the three lines a new file needs.

## running it

```
uv run main.py                     the bundled example script
uv run main.py my-script.txt       your own
uv run main.py x.txt --no-manual   stop after stage 2, no browser
```

`run_all()` in main.py is the whole orchestration — four calls, in order:

```python
split = stage_0_split(prefix, script_path)     # 0-sentence-splitter/
lines = [line for line, _ids, _meta in split]
stage_1_visualisables(prefix, lines, out_path) # 1-visualisable-identification/
stage_2_shot_list(split, out_path)             # 2-auto-tagging/
stage_3_manual_tagging(out_path)               # 3-manual-tagging/
```

Every stage runs every time and says so — a cache makes a stage fast, it
never makes it get skipped. Each is callable on its own if you want to run
one stage by hand. `RUN_STAGE_1_VISUALISABLES` / `RUN_STAGE_2_AUTO_TAGGING`
at the top of the file turn 1 and 2 off.

Run directly, EVERYTHING it writes goes under `TEST_RESULTS/` — the shot
lists and the visualisables json at the top, the caches under
`TEST_RESULTS/CACHE/`. Nothing is written next to the code. (The repo's own
main.py imports it as a library instead, and then the defaults are the
working directory, which is where CONFIG expects them.)

Stage 0 needs the spaCy model — install it once with
`uv run python -m spacy download en_core_web_sm`. Stage 2 only ever fills
rows that are EMPTY, so re-running is always safe. Each line carries the
splitter's rule_ids for context. Field-by-field docs: FORMAT.md, next to
this file.

Stage 3 can also be opened on its own against a shot list you already have:

```
uv run 3-manual-tagging/MANUAL_TAGGING.py TEST_RESULTS/TESTING_whales-script_to_search_term.json
```

Opens a localhost page: pick one media type per line (grouped NEW / EDIT
PREVIOUS, ai family in red, info icons and a key explaining everything),
stack decorate / caption / group on top, write the search term (ghost
suggestion with tab-to-accept, tap-to-append chips), split a
line at any character with the golden cursor, join a line to the one above,
undo anything. Groups show as a shared coloured stripe. Works on phones.
Saves instantly.

The chips — what is in this line, what was on screen before it, the
combos, and what this line's "it" turned out to mean — all come from
`1-visualisable-identification`, so they say the same thing the rest of
the pipeline will act on. The first load runs the coreference models over
the whole script (tens of seconds, with a countdown on the page); after
that it is cached until the lines themselves change.

Optional ai help: the prompt templates in prompts/ take the json plus the
rule lists (BASE_RULES.md and your own MASTER_RULES.md) so an llm can draft
the search terms for you; paste its json reply over the file and review in
MANUAL_TAGGING.

Never delete: main.py, __init__.py, shared/, prompts/, documentation/,
your scripts and finished jsons. Everything under *-CACHE and TESTING_* is
regenerable (see RESET_AFTER_TEST.md, next to this file).


# Testing

## trying the auto tagger on the ten fixture scripts

```
uv run 2-auto-tagging/TEST_AUTO_TAGGER.py --list     the ten fixtures
uv run 2-auto-tagging/TEST_AUTO_TAGGER.py --test 1   fixture 1: auto-tag
                                                     it, then open it
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
cd 2-auto-tagging
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
