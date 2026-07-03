# BASE RULES — search-text generation for fast-paced explainer videos

These are the PERMANENT foundation rules. User-taught rules (from the
ADD_SEARCH_TEXT review loop) live in MASTER_RULES.md and are numbered from
#1001 so the two lists never collide. Every rule here follows the same
format the review tool produces, so the AI reads one consistent rulebook.

Terminology: each JSON entry is one NARRATION LINE with a chosen SHOT
(`template`/`search_type`); your job is to write its `search_term` — the
stock query / AI prompt / map name / caption text that shot needs.

## THE REVEAL (the single most important idea)

RULE #1)
* [If you open] [your kitchen cupboard right now, you probably have] [a jar of nutmeg.]
* design: line 1-2 show "kitchen cupboard opening" — the cupboard, being opened. The nutmeg appears ONLY on line 3, when it is spoken.
* reason: the splitter cuts lines so each visualisable thing appears the moment it's said — never before. A term must match what's spoken DURING that line.

RULE #2)
* [any line] <later entries>
* design: never use a noun from a LATER line in an earlier line's term.
* reason: you can see the whole script; that power is for resolving references BACKWARDS, never for spoiling forwards.

RULE #3)
* [which brings us to an unliked emperor] <0 more entries> [his name was Caesar]
* design: line 1 = "black silhouette outline of a roman emperor with a question mark over his face"; line 2 = the actual person.
* reason: the person hasn't been revealed yet — tease the identity, pay it off on the name line.

RULE #4)
* [a valley called] <0 more entries> [Wadi Al-Hitan]
* design: line 1 = generic anticipation shot ("desert valley aerial"); line 2 = the named thing itself.
* reason: "a X called" is a built-up reveal — the generic noun first, the name as the payoff.

RULE #5)
* [It was worth more than] <0 more entries> [its weight in gold.]
* design: hold the subject through line 1; gold appears on line 2 only.
* reason: mid-comparison, the second half of the comparison is the reveal.

## REFERENTS (this / that / it / they)

RULE #6)
* [this little wrinkled seed was]
* design: "wrinkled nutmeg seed macro"
* reason: resolve demonstratives to the real referent — the script is about nutmeg. Never emit the bare pronoun phrase.

RULE #7)
* [It was worth more than]
* design: whatever "it" is (the standing subject), not the word "worth".
* reason: bare pronoun subjects inherit the most recent concrete subject.

RULE #8)
* [any line with no concrete noun of its own]
* design: describe the standing subject, or hold/zoom the previous image.
* reason: verb-only and function-word terms ("costs", "brings", "open", "holding", "find") are forbidden — a search engine cannot picture a verb.

RULE #9)
* [modern-day Indonesia.]
* design: "Indonesia" (the map shot's term is the bare place name).
* reason: place mentioned mid-flow → a map with the place highlighted is the expected beat; the term is a map lookup, not a description.

## PACING (fast-paced = mostly small edits)

RULE #10)
* [any mid-sentence continuation line]
* design: prefer a SUBTLE EDIT of the standing image — hold, zoom, caption, or one added element — over a fresh fetch.
* reason: the video is fast; constant full-frame switching is exhausting. Fresh fetches belong mostly at sentence starts.

RULE #11)
* [growing nutmeg plants] <0 more entries> [harvested by farmers] <0 more entries> [and shipped across the sea]
* design: establish "nutmeg plants growing", then "add farmer harvesting", then "add sailing ship".
* reason: consecutive lines elaborating one scene build UP a composition (layering chain), not three unrelated fetches.

RULE #12)
* [the planet.]
* design: "add planet earth in corner" (composite onto the previous image).
* reason: a single new concrete noun arriving mid-sentence is added ONTO the scene — the fastest readable beat.

RULE #13)
* [two adjacent lines]
* design: never give two adjacent lines the same term with the same shot type.
* reason: an identical repeated frame reads as a glitch; if the subject continues, change the angle (zoom), annotate it, or add to it.

RULE #14)
* [two adjacent text-heavy shots (caption/typography)]
* design: avoid back-to-back; separate text beats with imagery.
* reason: consecutive reading shots kill the pace.

## PER-SHOT-TYPE CONTRACTS

RULE #15)
* [any new__stock line]
* design: 2–4 concrete words: [adjective] noun [action] [setting]. At least one noun, always. Prefer motion phrasing ("waves crashing") — stock VIDEO beats stills for verbs.
* reason: stock APIs match short concrete queries; specificity beats generality ("weathered fisherman hands rope" > "man working").

RULE #16)
* [any new__ai_stock line]
* design: a fuller scene prompt, up to a short sentence: subject + action + props + mood ("tall ship sailing ocean storm").
* reason: AI image prompts reward composed scenes; they are not keyword searches.

RULE #17)
* [any new__wikipedia / new__wikipedia_on_board line]
* design: the EXACT article name, nothing else: "Banda Islands", "Alaric the Goth".
* reason: it's a title lookup — descriptions and extra words break it.

RULE #18)
* [any new__map line]
* design: the exact place name only: "Indonesia", not "modern-day Indonesia", not "volcanic archipelago".
* reason: map lookup, same as above.

RULE #19)
* [any new__object line]
* design: the single object, plainly, isolated: "one dollar coin", "gold bar", "antique parchment".
* reason: the object editor needs a clean subject to cut out.

RULE #20)
* [any new__typography line]
* design: the line's spoken text itself, verbatim.
* reason: kinetic typography IS the visual — the words are the shot.

RULE #21)
* [any editprev__caption line]
* design: the punch words, UPPERCASE, digits kept: "$2 MILLION", "TRADED FOR NUTMEG". If the line contains a quote, the quote verbatim.
* reason: captions are read in half a second — shortest possible punch.

RULE #22)
* [any editprev__add_stock line]
* design: an imperative saying what to add and where: "open the door and add jar of nutmeg into the cupboard". One new thing per line.
* reason: a human/compositor executes this — it's an instruction, not a query.

RULE #23)
* [any editprev__ai_edit line]
* design: a DELTA prompt describing only the change: "add a second one dollar coin", "make the ship sink".
* reason: it edits the previous AI image in place; restating the whole scene causes drift.

RULE #24)
* [any editprev__zoom / editprev__hold line]
* design: keep/inherit the standing subject (optionally "close up" for zoom).
* reason: these shots reuse the existing frame.

RULE #25)
* [any editprev__draw line]
* design: a short drawing note: "circle the island", "arrow pointing at the seed".
* reason: it's a hand-annotation instruction.

RULE #26)
* [any editgroup__* cell]
* design: ONE concrete noun (+ at most one modifier) per cell, matching that cell's line: "scurvy sailor", "pirate flag", "shipwreck".
* reason: grid cells are glanced at, not studied.

RULE #27)
* [lines that are grid cells but do NOT read as an obvious comma list of visualisable nouns]
* design: do not invent cell terms; add a "_note" flagging the mis-grouping.
* reason: grids exist ONLY for obvious comma-separated lists; anything else is a shot-choice bug worth surfacing.

## IDIOMS AND SAYINGS

RULE #28)
* [and the rest is history.]
* design: no new imagery — hold the previous image (term inherits), or a caption of the saying.
* reason: idioms mean something as a unit and paint no literal picture; "history" footage would be nonsense.

RULE #29)
* [an idiom where the literal image is funny ("in a nutshell")]
* design: optionally play it literal for a beat (an actual nutshell) — sparingly, max once per video.
* reason: comedic literalism is a great pattern interrupt but ages fast if repeated.

## NUMBERS, MONEY, DATES

RULE #30)
* [It costs about two dollars.]
* design: "one dollar coin" / "two one-dollar coins" (the object, count shown).
* reason: money beats are object shots; the amount is shown, not written, unless the shot is a caption.

RULE #31)
* [a big number ("10 million tonnes")]
* design: a SCALE COMPARISON the viewer knows: "container ship aerial", "football stadium crowd".
* reason: raw big numbers don't land visually; comparisons do.

RULE #32)
* [any line after an old date has been mentioned ("in the 1600s")]
* design: style people/vessels/conflict stock as period imagery: "old European merchant ship painting", "scurvy sailor historical painting".
* reason: modern footage inside a historical passage breaks the spell.

## QUOTES AND SFX

RULE #33)
* [a quoted line ("stop right there")]
* design: the quoted words verbatim as the caption.
* reason: spoken quotes go on screen as text, exactly as said.

RULE #34)
* [boom]
* design: caption the sound word huge ("BOOM"), timed to the sync point.
* reason: SFX beats are punches on the standing image, not new fetches.

## GENERAL CRAFT (fast-paced entertainment)

RULE #35)
* [an abstract concept line ("the economy collapsed")]
* design: a concrete visual metaphor: "falling stack of coins", "closing shop shutters".
* reason: abstractions need physical stand-ins; pick the most cliché-free one that reads in under a second.

RULE #36)
* [an emotional line (fear, greed, wonder)]
* design: a human FACE carrying that emotion, close up.
* reason: faces out-perform objects for emotional beats.

RULE #37)
* [first line about a new place]
* design: wide establishing shot ("aerial"), then details in following lines.
* reason: wide→close is the natural scene grammar; reversed it disorients.

RULE #38)
* [a "but/however" pivot line]
* design: a visual CONTRAST with the previous shot (dark vs light, still vs motion).
* reason: the edit should feel like the sentence's turn.

RULE #39)
* [an open question / mystery line]
* design: question-mark overlay, silhouette, or an obscured subject.
* reason: the curiosity gap is the retention engine — visualise the unknown as unknown.

RULE #40)
* [any stock term]
* design: use common, searchable English words; avoid jargon and rare terms the stock library won't have.
* reason: the perfect description that returns zero results is worthless.

RULE #41)
* [any stock term]
* design: avoid the tired stock clichés (handshakes, generic boardrooms, thumbs up) unless played as a joke.
* reason: recognisable stock-ness reads as cheap.

RULE #42)
* [a living famous person]
* design: prefer the wikipedia shot / their name; never describe a lookalike for stock.
* reason: "man who looks like X" returns uncanny results; the real image is one lookup away.

RULE #43)
* [consecutive lines within one scene]
* design: keep colour/mood/setting consistent across their terms until the scene changes.
* reason: visual continuity groups beats into scenes; random palettes feel like channel-surfing.

RULE #44)
* [a transformation line ("it went from X to Y")]
* design: a before/after pair — X on this line, Y on the next (or a split composite).
* reason: transformations are the strongest visual story unit; don't collapse them into one frame.

RULE #45)
* [anything violent/graphic in the script]
* design: imply, don't show: aftermath, shadows, maps, museum artefacts.
* reason: platform-safe and usually more powerful.

## OUTPUT CONTRACT

RULE #46)
* [the whole JSON]
* design: return the COMPLETE JSON with ONLY `search_term` fields changed (plus optional `_note` fields). Keys, order, and every other field byte-identical. Valid JSON, no commentary outside it.
* reason: the file is machine-consumed downstream.

RULE #47)
* [any entry whose given shot seems wrong for its line]
* design: still write the best term FOR THE GIVEN template, and add a short "_note" explaining the concern.
* reason: shot choices are fixed upstream; your notes feed the human review loop.

RULE #48)
* [use the provided context fields]
* design: read `rule_ids` + `why` (how/why the splitter cut the line and the tier chose the shot) before writing each term.
* reason: the tags tell you whether a line is a list item, an SFX beat, a quote, a reveal — the term should agree with that structure.
