# SPLIT_AND_LABEL

Turn a raw narration **script** into a per-line **shot list**: a JSON map from
each phrase-line of the script to a concrete media type (effect) and a search
term the downstream renderer can act on.

```
script-spices.txt  ──▶  spices-script_to_search_term.json
```

This document is the spec. Every decision, number, and path is written down so
the pipeline is legible and tunable. The single most important thing to review
is the per-rule table — see [§4](#4-the-reviewable-part-rule_media_weightspy).

---

## 0. TL;DR

```bash
# deps come from your project (pyproject.toml / uv) — this file has NO inline
# PEP-723 header. Make sure spaCy + the splitter's model are available:
#   spacy, en_core_web_sm  (the splitter loads en_core_web_sm)

uv run SPLIT_AND_LABEL.py          # self-test on script-spices.txt (always TESTING)

# real use, from code:
python -c "from SPLIT_AND_LABEL import generate_script_to_search_term as g; g('script-spices.txt')"
```

### Files in this bundle

| file                          | what it is                                                        |
|-------------------------------|------------------------------------------------------------------|
| `SPLIT_AND_LABEL.py`          | the pipeline (split → weights → label)                           |
| `RULE_MEDIA_WEIGHTS.py`       | **the reviewable table**: each rule id → description + media-type affinities |
| `gen_rule_media_weights.py`   | regenerates `RULE_MEDIA_WEIGHTS.py` (pulls descriptions from the splitter) |
| `README-SPLIT_AND_LABEL.md`   | this file                                                        |

**Imports it expects:** `sentence_splitter` (your splitter, same dir /
`PYTHONPATH`), `MediaType` from `___visuals/CONFIG.py`, and
`RULE_MEDIA_WEIGHTS` from `RULE_MEDIA_WEIGHTS.py`. The effect vocabulary is **not
hardcoded** — `ALL_EFFECTS = [m.value for m in MediaType]`.

---

## 1. What it does, in one breath

1. **SPLIT** — feed the script text through
   `sentence_splitter.split_text_into_sections()`. It returns an ordered list of
   `Chunk(text, ids)`, where `ids` are the *rule ids* the splitter fired to
   produce that line (1–55 splitting rules, 1000+ merge/glue rules; see
   `sentence_splitter.RULE_DESCRIPTIONS`). Those ids **are** "the numbers
   representing what it split on".
2. **WEIGHTS** — for every line, turn its rule ids into a media-type score-sheet
   (via `RULE_MEDIA_WEIGHTS`), then apply the [big general rules](#3-the-big-general-rules).
   The cached score-sheet also stores the ids and their descriptions, so the
   number → meaning → media-type linking is reviewable per line.
3. **LABEL** — collapse each score-sheet into one media type, then emit the final
   config row.

Each stage writes a cache; **if a cache exists that stage is skipped**.

---

## 2. Files on disk (a run)

For prefix `spices`, in **TESTING** mode:

```
.
├── script-spices.txt                              # INPUT
├── TESTING_spices-script_to_search_term.json      # OUTPUT (the shot list)
└── spices-CACHE/
    └── split-and-lable/                           # (spelling kept per spec)
        ├── TESTING_SPLIT-spices.json              # stage 1: [[text, ids], …]
        └── TESTING_WEIGHTS-spices.json            # stage 2: reviewable score-sheets
```

In **LIVE** mode the `TESTING_` prefix is dropped from every written file.

**Prefix derivation** (`prefix_from_script_name`): strip extension, strip a
leading `script-`/`script_`. `script-spices.txt` → `spices`; `script-spices.wav`
→ `spices` (a `.wav` is audio, not a script — `_resolve_script_path` falls back
to the `.txt` sibling; feed it the transcript).

---

## 3. The BIG GENERAL RULES

Applied on top of the per-rule affinities, in `adjust_for_context()`. These are
the "be sensible" defaults — written here and in the code so they're reviewable.

- **R1 — Continuation vs opener (sentence position).** A line that *continues* a
  sentence (no `. ! ? ; :` before it) is much more likely to be a
  **previous-image** type (`static_of_previous` / `zoom_prev_img` /
  `decorate_previous` / `manual_stock_add_to_previous`) — it elaborates on what's
  on screen. A line that *opens* a sentence gets those strongly suppressed and
  leans fresh (stock, etc). Implemented with floors on the previous-family for
  continuations and a ×0.10 suppression for openers.
- **R2 — Named / capitalised things → `wikipedia`.** A line tagged as a named
  entity (rules 18/48/50) or carrying a proper-noun-ish capitalised /
  ALL-CAPS token gets `wikipedia` ×2.6 (and `object_generate` ×1.25).
  Sentence-initial words and pronouns are excluded, so a leading "It"/"They"
  doesn't trigger it.
- **R3 — Most things are `stock`.** `stock` has a high baseline everywhere; the
  specialist types only win when a rule (or a big rule) makes a confident case.
- **R4 — Places → `map`.** A line tagged location/spatial (rules 30/46) or with
  a capitalised word after a locative preposition ("in Oregon", "in modern-day
  Indonesia") gets `map` ×3.

To make R2/R4 easy to fire, the code detects entities/places from **both** the
splitter's rule ids **and** cheap regexes (mid-sentence capitals, ALL-CAPS,
locative-preposition + capital). Add more rules here as you find them; keep the
prose and `adjust_for_context()` in sync.

---

## 4. The reviewable part: `RULE_MEDIA_WEIGHTS.py`

This is the file to open when a choice looks wrong. For **every** tagged rule id
the splitter can stamp, it maps the number to its description **and** an affinity
for each media type:

```python
9: {
    "description": "a comma that introduces a 'who / which / that...' "
                   "description — e.g. 'the dog,' | 'which was huge'",
    "media_type_probabilities": {
        "stock": 0.45, "object_generate": 0.12, "read_out": 0.10,
        "wikipedia": 0.22, "map": 0.05, "joint_3_row": 0.05,
        "manual_stock_add_to_previous": 0.03, "zoom_prev_img": 0.03,
        "static_of_previous": 0.04, "decorate_previous": 0.50,
    },
},
```

- The numbers are **independent affinities** in `[0,1]` — *"how well does this
  media type suit a line the splitter cut for THIS reason"* — **not** a
  distribution (they don't sum to 1). It's the "80% dog / 50% muffin" model.
- The descriptions are **mirrored from `sentence_splitter.RULE_DESCRIPTIONS`**,
  so you review the number → meaning → media-type link in one place.
- Regenerate after editing the generator's tables:
  `python3 gen_rule_media_weights.py > RULE_MEDIA_WEIGHTS.py`. You can also just
  hand-edit `RULE_MEDIA_WEIGHTS.py` directly — it's plain data.

### How a line's score-sheet is built (in `score_line`)

1. Start from a **stock-dominant base prior** (R3).
2. For each rule id on the line, take the element-wise **MAX** with that rule's
   affinities. (MAX, so a rule can only *raise* a type — multiple rules don't
   over-add and stock keeps its floor.)
3. Apply the **big general rules** (R1/R2/R4).
4. Clamp to `[0.005, 0.99]`.

The stage-2 cache stores, per line: `ids`, `opener`, `why` (the descriptions),
and the final `media_type_probabilities`. That's your audit trail.

---

## 5. Choosing one media type (stage 3)

`CHOICE_MODE`:

- **`"sample"`** (default) — normalise the scores and draw one, RNG **seeded per
  line** (`GLOBAL_SEED` + text), so a line always resolves the same way but the
  deck has a hand-made-looking mix.
- **`"argmax"`** — always the top score. Fully deterministic; use it to eyeball
  the model's "intended" pick per line.

`CHOICE_TEMP` (default `3.0`) sharpens sampling: scores are raised to this power
before drawing, so the **top pick wins most of the time** (giving "mostly
stock") while strong runners-up still surface. `1.0` = flat/proportional; higher
= peakier; very high ≈ argmax.

**Guard:** a previous-family type can't be chosen until some fresh visual exists
(nothing to zoom into on line 1).

---

## 6. Search terms, `position`, sfx/music

- **Search terms** — baseline keyword extraction (drop stopwords/punctuation,
  keep content words). Previous-family effects inherit the previous fresh line's
  term; `manual_stock_add_to_previous` phrases it as `add <keywords> to
  previous`; `stickman_text_overlay` (AI) uppercases the caption. Swap
  `_keywords()` for a spaCy noun-chunk extractor or an LLM for richer terms — it's
  one isolated function.
- **`position`** — `"1"` everywhere except across a run of joint/multi-cell
  effects, where it cycles `1→2→3` (`JOINT_EFFECTS`, `JOINT_CELLS`).
- **sfx / music** — constant/blank for now (the `*_DEFAULT` constants), per spec.

---

## 7. Toggles & caching

| toggle                                   | effect                                              |
|------------------------------------------|-----------------------------------------------------|
| `TESTING_SCRIPT_SEARCH_TERM_GENERATION`  | prepend `TESTING_` to the output **and** cache files |
| `AI_ENABLED`                             | `False` → drop every `ai`/`stickman` media type from the choosable set entirely |
| `CHOICE_MODE` / `CHOICE_TEMP` / `GLOBAL_SEED` | how the pick is made / how peaky / reproducible seed |

**Running the file directly is always a TESTING run** (`__main__` forces the flag
on) and uses `script-spices.txt` (writes a small bundled sample if absent).

**Caching** — three points, checked in order: `SPLIT-*.json`, `WEIGHTS-*.json`,
then the final output itself. If a cache exists, that stage is skipped. Delete a
cache to redo just that stage; delete the output to re-roll only the labelling;
delete `*-CACHE/` + the output for a clean run.

> Duplicate lines collapse to one output entry (JSON-object keying, last wins).
> Order-sensitive state (previous-term inheritance, joint position) is computed
> over the full ordered list first, so it stays correct.

---

## 8. Output schema

```jsonc
{
  "<line text>": {
    "search_term": "banda islands",   // what to fetch/build
    "search_type": "wikipedia",       // the chosen MediaType value
    "position": "1",                  // "1" except inside a joint run
    "sfx": "none",                    // constant for now
    "sfx_timing": "loop_start",       // constant for now
    "music": "none",                  // constant for now
    "music_trim_seconds": 0,          // constant for now
    "music_fade_out": 0               // constant for now
  }
}
```

---

## 9. Tuning cheat-sheet

| want to change…                        | edit…                                                |
|----------------------------------------|------------------------------------------------------|
| what a split-reason implies            | `RULE_MEDIA_WEIGHTS.py` (or the generator's `OVERRIDES`) |
| the "mostly stock" prior               | `_NONAI_BASE` in `SPLIT_AND_LABEL.py`                 |
| a big general rule's strength           | the `_CONT_*` / `_OPENER_*` / `_ENTITY_*` / `_PLACE_*` multipliers |
| how deterministic vs varied            | `CHOICE_MODE`, `CHOICE_TEMP`, `GLOBAL_SEED`           |
| entity/place detection                  | `_has_entity` / `_has_place` (+ their rule-id sets)   |
| search-term wording                     | `_keywords()` / `_search_term()`                     |
| joint-cell counting                     | `JOINT_EFFECTS` / `JOINT_CELLS`                       |
| turn AI effects on                      | `AI_ENABLED = True`                                  |

After editing weights, delete `WEIGHTS-*.json` (and the output) so the change
takes effect.

---

## 10. Honest caveats

- **Search terms are keyword soup**, not the crafted phrases of a hand-made map.
  This is the obvious place to bolt on an LLM.
- The rule → media-type affinities are an **educated baseline**, not learned from
  data. They're meant to be edited — that's what `RULE_MEDIA_WEIGHTS.py` is for.
- Entity/place detection is heuristic. It leans on the splitter's spaCy-derived
  rule ids plus light regexes; it will occasionally misfire (e.g. a demonym after
  "to the" reading as a place). Tune `_has_entity` / `_has_place`.
- Identical lines share one output row.
- When `AI_ENABLED = True`, the AI/stickman types currently get only a small flat
  prior (no per-rule affinities) — add entries for them in the generator if you
  start using them for real.
```
