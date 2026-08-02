# test_jsons — the ten fixtures the auto tagger is tried on

Driven entirely by `uv run TEST_AUTO_TAGGER.py` in the directory above.

```
scripts/          the ten narrations. THE SOURCE — edit these to change a test.
untagged/         each one split by the REAL sentence splitter, media_type and
                  search_term empty. Pristine: never tagged, never edited.
test_json_<n>.json        the working copy you actually look at. Overwritten
                          from untagged/ at the start of every --test run.
test_json_<n>-CACHE/      the splitter's meta for that fixture — this is what
                          gives MANUAL_TAGGING its noun/place chips.
```

Regenerable: everything except `scripts/`. Delete `untagged/` and the
`*-CACHE` folders and `uv run TEST_AUTO_TAGGER.py --build` puts them back
(it re-runs the splitter, which takes a minute).

The ten are deliberately different kinds of video, not ten explainers — see
`uv run TEST_AUTO_TAGGER.py --list` for what each one is there to probe.
