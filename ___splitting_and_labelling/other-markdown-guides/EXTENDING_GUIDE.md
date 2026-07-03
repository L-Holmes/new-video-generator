# EXTENDING GUIDE — one location per change

Every common change touches exactly ONE place (plus a test). If you find
yourself editing two files for one concept, something has regressed — fix
the structure, not the symptom.

## Add / change a MEDIA TYPE

**Edit:** `SPLIT_AND_LABEL_CONFIG.py` → `TEMPLATE_DEFS` — one entry.

```python
"new__timelapse": TemplateDef(
    ShotSpec(Strategy.NEW, Material.STOCK),   # its axis coordinates
    legacy="stock",              # what the current renderer receives
    legacy_ai=None,              # different renderer string when AI is on?
    requires=None,               # "named_thing_entity"/"place_entity"/"list"
    lock_only=False,             # True = only a tier-1 lock may choose it
    prior_opener=0.05,           # expected-ratio weight at sentence starts
    prior_cont=0.02),            # expected-ratio weight mid-sentence
```

Naming convention: `new__*` (brand-new material — stock and AI generation
both count), `editprev__*` (acts on the previous image), `editgroup__*`
(a related run of cells, rule of N). Everything else — legacy bridge,
requirement gating, lock-only sets, AI gating, pacing priors, derived
groupings — is computed from this table. Nothing else to update, except:

- optional: a per-rule taste row in `gen_rule_media_weights.py` OVERRIDES
  (then regenerate: `python3 gen_rule_media_weights.py > RULE_MEDIA_WEIGHTS.py`)
- optional: a term contract branch in `SEARCH_TERM_SYNTHESIS.py` +
  a bullet in `SEARCH_TERM_LLM_PROMPT.md`
- a test in `test_pipeline_fixture.py`

## Add a SENTENCE-SPLITTER RULE / TAG

**Edit:** `sentence_splitter.py` only.

1. Write `rule_<name>(doc, splits)` next to its siblings, with the house
   header comment (FIRE / DON'T FIRE examples).
2. Add its number + text to `RULE_DESCRIPTIONS` — **the ONE master copy**
   (the weights file only mirrors a one-line comment; the engine reads
   descriptions from here).
3. Map name→number in `_SPLIT_RULE_IDS`.
4. Register in `_POSITIVE_PIPELINE` (order matters: later rules see earlier
   splits).
5. If its splits must survive merging (one-word lines etc.), add the name to
   `PROTECTED_RULE_NAMES`.
6. Optional: a weights OVERRIDES row (regenerate), a tier-1 lock keyed on the
   rule id (see below).

## Add a TIER-1 LOCK or TIER-2 NUDGE

**Edit:** `SPLIT_AND_LABEL.py`.

- Lock: write `lock_<name>(text, meta, ids, allowed, state)` returning
  `(template, term_override, reason)` or `None`; insert into `TIER1_LOCKS`
  at the right PRIORITY (order is first-match-wins). Design rule: a lock
  must be evidence you'd bet on 100% of the time — otherwise it's a nudge.
- Nudge: write `nudge_<name>(sheet, text, meta, ids, state)` mutating the
  score-sheet and returning a reason string; append to `TIER2_NUDGES`.

## Tune the PACING / expected ratio

**Edit:** `SPLIT_AND_LABEL_CONFIG.py` — `prior_opener` / `prior_cont` on each
TemplateDef, plus `CONT_FRESH_DAMP`. The doctrine: openers fetch fresh,
mid-sentence lines are mostly subtle edits of the previous image. Verify a
change with `calibrate_against_golden.py` against a hand-made reference.

## Add WORDS to a lexicon

**Edit:** `sentence_splitter.py` — ALL word lists live there (the linguistic
home): `WEAK_VERB_LEMMAS`/`_FORMS`, `WEAK_ADJ_LEMMAS`, `TRANSITION_ADVERBS`,
`DISCOURSE_PIVOT_PHRASES`, `SFX_WORDS`, `ERA_STYLE_NOUNS`,
`_GENERIC_TOPIC_NOUNS`, the perception families, etc. Word lists must be
LINGUISTIC CATEGORIES (kinds of words), never world-knowledge answer keys
(specific famous things) — famous/obscure facts belong exclusively in
`ENTITY_FAME_CACHE.json`, populated by `populate_entity_fame.py`.

## Teach it who's FAMOUS

Run `python3 populate_entity_fame.py --from-splitmeta <SPLITMETA cache>` on a
machine with network access. Never hardcode names in source.

## Cache discipline

If the SPLIT output schema or semantics change, bump the cache filename
suffix in `split_cache_path()` (`SPLITMETA3-` → `SPLITMETA4-`) so stale
caches can never be silently misread.

## After ANY change

```
python3 test_pipeline_fixture.py     # engine + meta + contracts (50 checks)
python3 test_v18_rules.py            # splitter rule logic
python3 SPLIT_AND_LABEL.py           # 3 sample scripts end-to-end (needs spaCy)
python3 calibrate_against_golden.py TESTING_<x>.json <golden>.json
```
