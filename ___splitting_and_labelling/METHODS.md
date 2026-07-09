# METHODS — VISUAL_RECOMMENDER

Bullet tracker of every method the keyword-recommendation engine uses.
Files: `VISUAL_RECOMMENDER.py` (engine) · `BENCHMARK_RECOMMENDER.py`
(gold-standard scenarios / scorecard) · `test_visual_recommender.py`
(unit tests) · `build_wordlists.py` → `recommender_data.json` (open data)
· wired into `MANUAL_TAGGING.py` (chips + pronoun panel).

## Data sources (no cheating rule)
* Open classes come from big maintained resources; small lists only as
  documented FALLBACK seeds; closed grammatical classes (~30 words,
  genuinely finite) stay hardcoded by design.
* Names → gender-guesser (pip): Jörg Michael's ~48k international first
  names (Greek, Slavic, Arabic, Asian ...). ==> person evidence + gender.
* Person / weather / unit / time / collective nouns → NLTK WordNet
  hypernyms (person.n.01, atmospheric_phenomenon, unit_of_measurement,
  time_period, social_group). First-sense-only where precision matters
  ("machine" has a buried person sense; "foot" is a body part first).
* Concreteness → Brysbaert et al. 2014 human ratings, ~37k words, 1–5.
  ==> "can you photograph it": ≤3.0 abstract penalty, ≥4.0 concrete boost.
* Places + demonyms → mledoze/countries (250 countries, capitals, alt
  spellings; Italy→Italian). Cities/ancient world: curated seeds
  (no maintained open dataset exists) + fallback: the place NAME itself
  is the modifier ("Kyoto temples" — grammatical for any place).
* Closed classes kept hardcoded: era markers (AD/BC), absence verbs,
  extraposition adjectives, weather verbs, months, determiners,
  subject pronouns, auxiliaries, the anaphor table itself.
* All layered: library → recommender_data.json → seeds. Engine runs on
  bare Python; gets sharper with the data installed.

## Pipeline / pre-stages
* Tokenize (unicode letters + numbers; contractions stripped to base;
  n't → negation flag; curly quotes/dashes normalised; ALL-CAPS lines
  lose their capital signal; commas tracked per token; mid-line `.` = new sentence).
* Cross-fragment CARRY: each caption fragment is analysed with the tail
  of the previous one ==> names merge across cuts ("the Circus" +
  "Maximus." → Circus Maximus), adjacency learns across cuts, a
  continuing fragment introduces no fake subject/sentence.
* POS by context hints: determiner/preposition before ⇒ noun ("the cut");
  subject pronoun / auxiliary / negated aux before ⇒ verb ("he books",
  "didn't drop"); then suffixes, common-adjective list, WordNet
  verb-only/adj-only, default noun.
* Proper-noun runs merge (with of/de/van connectors), stop at sentence
  boundaries and numbers.
* Noun+noun compounds ("presser foot"), greedy, comma-blocked, generic
  modifier blocked but generic head OK ("needle case").
* Adjective+noun chunks: DISTINCTIVE adjective kept ("religious sect"),
  size/degree adjectives dropped ("small") — the adjective is knowledge
  that changes what the thing looks like.
* Extraction (per entry, carry-aware): nouns+propers, compounds/chunks
  swallow their parts, generic/time/unit/direction/number words dropped,
  idiom + negated words dropped.

## Scene logic
* Negation ⇒ absent from scene: "no jar", "without a map", "not a coin";
  verb-scope: negated ABSENCE verbs only ("couldn't FIND the map" ⇒ no
  map; "didn't DROP the jar" ⇒ jar present; "never FORGOT the temples"
  ⇒ temples present).
* Idioms (curated ~25 + variants): "raining cats and dogs" ⇒ no cats.
* Similes: "like a rocket", "texture of concrete" ⇒ heavy demotion.
* All three are per-mention flags: a word negated once but seen for real
  later recovers fully.

## Scoring (WEIGHTS table at top of engine — the tuning surface)
* score = POS base (proper≫noun≫adj≫verb) × Σ recency-decayed mentions
  (half-life 5 entries, floor 0.15 so nothing fully dies) × boosts/
  penalties: confirmed-by-user ×1.6, multiword ×1.25, concrete ×1.3,
  abstract ×0.35, simile/negation/idiom/generic ×~0.3.
* Perf: incremental record aggregates, per-line score memoisation,
  floor shortcut (old mentions counted, not iterated) ⇒ near-linear.

## Themes (the setting colours everything)
* Detect: proper place (KB) or era adjective (medieval, victorian ...)
  that opens the piece (first 3 entries ⇒ +2) or recurs; strength =
  distinct-entry spread; NO recency decay (held at high floor).
* ==> combos "{Demonym} {noun}": Roman Temples, Greek Prophets,
  Japanese Merchant Ship; fallback modifier = place name itself
  (Kyoto Temples).
* ==> era suffix at the END, only when old (≤1990 / BC / "Nth century"):
  "Roman Merchant Stalls 64 AD"; digits are tokens; AD/BC never objects.
* Theme-immune words (WordNet natural/atmospheric phenomena + seeds):
  wind, drizzle, fire ... never themed — wind is wind everywhere.
* Category anchors (mini-hypernyms): "the city" ⇒ the theme place
  ("Rome"); title word + person ⇒ "{Title} {Name}" ("Emperor Nero") —
  templates are fixed word order ⇒ grammatical by construction, no
  grammar checker needed.

## Pairs / combos (a combo = knowledge that changes the visual)
* Allowed sources ONLY: literal adjacency seen in the text ("sausage
  roll" recombines), grammar templates (jar of nutmeg, slice of bread,
  flock of geese — animate groups need animate plurals, groups need
  plural/mass right sides), themes, anchors. Co-occurrence is evidence,
  NEVER a join ("Rome Day" ban).
* Hygiene: partners come from memory (not introduced by the current
  line), sides never share a word ("stalls stalls" ban), negated/idiom
  partners banned, propers don't take themes, score = geometric mean of
  both words × link strength, threshold-gated, deduped, capped.

## Pronouns → the scrollable panel ('it' (2) → saucepan (0.20))
* Full anaphor table: personal/reflexive/possessive/demonstrative/
  indefinite/relative/positional; kinds person|thing|plural|named;
  deictics (I/you) + non-referential indefinites documented as excluded.
* Candidate score = compatibility × pronoun recency (half-life 1.5) ×
  frequency salience × subject boost (centering; FLIPPED for object-case
  him/her/them) × gender match (48k names) × reflexive same-sentence
  binding × verb-frame memory ("plucked it" → the goose she plucked).
* Special resolvers: relative (which/who/that-after-noun → nearest
  preceding noun, 0.85), partitive ("one OF THE lanterns" → forward,
  0.85), former/latter (subject+object of last 2-participant sentence),
  cataphora fallback ("before HE left, Jerry ..." → 0.6, person+proper
  only), pleonastic 'it' skipped (weather/seems/extraposition/takes-to,
  conservatively), complementizer 'that' after verb+clause skipped.
* Panel hygiene: compound suppresses its parts; generic/time/negated/
  idiom candidates excluded; probabilities normalised with smoothing so
  they never sum to 1 (leaves room for "none of these").

## Weighting philosophy
* Themes ≫ recency for setting words; recency ≫ frequency for objects;
  user-confirmed terms beat both; everything tunable in one WEIGHTS dict
  and measured against BENCHMARK_RECOMMENDER (122 scenarios incl. the
  real Rome fragment script + Greek/Japan/Kyoto generalisation).

## Deliberate non-choices (and why)
* No grammar checker (LanguageTool = heavy Java; templates are correct
  by construction).
* No neural coreference by default (good open ones — fastcoref/LingMess —
  need PyTorch, ~0.5 GB; our heuristics + panel probabilities cover the
  captions use-case). spaCy hook stays auto-detected for embeddings.
* WordNet checks sense-limited on purpose: precision for demotions,
  recall for person/weather.

## Research the design leans on
* Centering theory (subject salience for pronouns).
* Gazetteer NER + demonym maps (how news search expands queries).
* Burst/TF-IDF-style salience with earliness prior (theme detection).
* Hearst-style hypernym anchors (city→Rome, emperor→Nero).
* Brysbaert concreteness norms (visualisability).
