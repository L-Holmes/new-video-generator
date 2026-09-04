"""
###############################################################################
#                                                                             #
#   shared_text_logic.py  —  THE ONE PLACE ALL TEXT KNOWLEDGE LIVES           #
#                                                                             #
###############################################################################

Every word list, every regex, every threshold, and every "how do I tell if a
piece of text is X" function used by BOTH halves of this folder:

    sentence_splitter.py    — cuts a script into phrase-lines (the SPLITTER)
    Auto_add_mediatypes.py  — decides what to put on screen (the TAGGER)

RULES OF THIS FILE
  1. NOTHING is hardcoded twice. If the splitter and the tagger both need to
     know what a quote mark is, or which verbs are copulas, it lives HERE and
     they both import it.
  2. Word lists NEVER sit between functions. Every list/set/regex/threshold
     is in SECTION 2, grouped under its own banner. Sections 3-6 are
     functions only.
  3. Every question we ask of text gets a NAMED FUNCTION with a plain-English
     name — `contains_country()`, `is_ordinal_token()`, `has_visualisable_
     content()`. Callers never apply a raw regex or scan a word list by hand.
  4. Prefer spaCy tags (POS / DEP / NER) over word lists. A word list here is
     either (a) a genuinely closed class (punctuation, copulas, pronouns),
     or (b) an explicitly-marked FALLBACK for when no spaCy model is
     installed. Both are labelled as such.
  5. The SENTENCE-END detection in SECTION 3 is the MASTER. If any other
     code disagrees about where a sentence ends, this one wins.

MAP OF THIS FILE
  SECTION 0 — IMPORTS, OPTIONAL LIBRARIES, THE spaCy MODEL
  SECTION 1 — SPLITTER RULE IDS  (RULE_DESCRIPTIONS + _SPLIT_RULE_IDS wiring
                                  + the groups the tagger reads them by)
  SECTION 2 — WORD LISTS, REGEXES AND THRESHOLDS  (data only, no logic)
              2.1  punctuation & symbols
              2.2  spaCy tag / dep / entity label sets
              2.3  function words & closed classes
              2.4  verb meaning families (copula/possession/creation/...)
              2.5  weak (non-visual) vocabulary
              2.6  measurement & number words
              2.7  spatial prepositions
              2.8  fixed multi-word phrases
              2.9  sound effect words
              2.10 topic & era style lexicons
              2.11 names & capitalisation
              2.12 geography
              2.13 regexes
              2.14 tunable thresholds
  SECTION 3 — SENTENCE END DETECTION  (THE MASTER)
  SECTION 4 — NAMED CHECKS ON PLAIN TEXT     (no spaCy needed)
  SECTION 5 — NAMED CHECKS ON PARSED TOKENS  (spaCy Doc/Token in, answer out)
  SECTION 6 — THE TAGGER'S DETECTORS         (one question per script line)
"""

from __future__ import annotations

# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 0 — IMPORTS, OPTIONAL LIBRARIES, THE spaCy MODEL          ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


# -----------------------------------------------------------------------------
# OPTIONAL BOOSTER LIBRARIES — each a well-maintained extractor for exactly one
# of our jobs. ALL optional: a missing one only makes a check cover LESS, never
# makes it guess more.
#     uv add dateparser price-parser number-parser geonamescache
# -----------------------------------------------------------------------------

def _try_import(name):
    try:
        return __import__(name)
    except Exception:
        return None


_dateparser_search = None
if _try_import("dateparser"):
    from dateparser.search import search_dates as _dateparser_search
price_parser = _try_import("price_parser")        # £4,000 → (4000, '£')
number_parser = _try_import("number_parser")      # 'seventeen million' → 17000000
geonamescache = _try_import("geonamescache")      # city → country code


# -----------------------------------------------------------------------------
# THE spaCy MODEL — loaded ONCE for the whole process and shared by the
# splitter and the tagger (they used to load a model each).
#
#   get_nlp()           → the model, or None if it isn't installed.
#                         The TAGGER uses this: no model means its checks stay
#                         conservative (False) rather than guessing.
#   get_nlp_required()  → the model, or a loud error.
#                         The SPLITTER uses this: it cannot work without one.
# -----------------------------------------------------------------------------

_NLP = "unloaded"


def get_nlp():
    """The shared spaCy model, or None when it isn't installed."""
    global _NLP
    if _NLP == "unloaded":
        try:
            import spacy
            _NLP = spacy.load("en_core_web_sm")
        except Exception:
            _NLP = None
            print("[shared-text] note: spaCy model not available — NLP "
                  "re-verification is OFF (checks stay conservative). "
                  "Enable it with:  uv add spacy  &&  "
                  "uv run python -m spacy download en_core_web_sm")
    return _NLP


def get_nlp_required():
    """The shared spaCy model — raises if it isn't installed."""
    nlp = get_nlp()
    if nlp is None:
        raise RuntimeError(
            "spaCy model 'en_core_web_sm' is required here. Install it with: "
            "uv add spacy && uv run python -m spacy download en_core_web_sm")
    return nlp


def booster_report(prefix: str = "[shared-text]") -> None:
    """Print which optional boosters are on/off (used by CLI entry points)."""
    rows = [("dateparser (dates in any format)", _dateparser_search),
            ("price-parser (money amounts)", price_parser),
            ("number-parser (written-out numbers)", number_parser),
            ("geonamescache (city → country)", geonamescache)]
    missing = [n.split(" ")[0] for n, m in rows if m is None]
    on = [n for n, m in rows if m is not None]
    if on:
        print(f"{prefix} boosters ON: {', '.join(on)}")
    if missing:
        print(f"{prefix} boosters OFF: {', '.join(missing)} — enable with: "
              f" uv add {' '.join(missing)}")


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 1 — SPLITTER RULE IDS                                     ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================
#
# The splitter stamps a RULE ID onto every line it cuts, recording WHY it cut
# there. Those ids are the highest-trust signal the tagger has: the splitter
# already did the full spaCy analysis, so "this line carries rule 15" means
# "the splitter proved this is part of a list run".
#
# Three things live here:
#   1.1  RULE_DESCRIPTIONS  — id → plain English (what the rule spotted)
#   1.2  SPLIT_RULE_IDS     — rule function name → the id it stamps
#   1.3  the GROUPS          — ids bundled by what they tell the tagger
# =============================================================================


# -----------------------------------------------------------------------------
# 1.1  RULE_DESCRIPTIONS — decode a chunk's `ids` into human-readable phrases
# -----------------------------------------------------------------------------
# Reading the examples:
#   • For SPLITTING rules, "|" marks where the rule puts the line break, e.g.
#     'The dog jumped.' | 'The man ran.'  means the rule split between them.
#   • For MERGING rules, "+" shows the two pieces being joined and "→" shows
#     the result, e.g.  'is' + 'big' → 'is big'.
#
# NUMBERING SCHEME (matches the "RULE n" headers in sentence_splitter.py):
#   • Rules 0 and 0.5  → the two PRE-PROCESSING passes (strip_markdown /
#     normalise_punct).  They reshape raw text and never create a split, so
#     they are deliberately NOT in this map.
#   • 1–60             → the SPLITTING rules (the positive pipeline).  Numbers
#     1–21 and 23–52 are the original header numbers, kept verbatim.  22 is an
#     unused gap in the historical numbering and is intentionally left absent.
#     53–55 are newly assigned (the "after the existing ones" slots) to the
#     three splitting rules that the codebase never gave a number.
#     56–60 are the v18 additions: comparison reveal, exception reveal,
#     discourse-pivot hook, passive-agent reveal, and SFX beat.
#   • 1000+            → the MERGING rules (the post-processing glue passes:
#     _merge_throwaways / _fuse_orphans / _post_merge_unvisualisable).  They
#     start at 1000 so a merge id can never be confused with a split id.
RULE_DESCRIPTIONS: dict[int, str] = {
    # ---- SPLITTING RULES (positive pipeline) --------------------------------
    1:  "the line ends with . ! ? ; or : — "
        "e.g. 'The dog jumped.' | 'The man ran.'",
    2:  "a dash breaks the line — e.g. 'it was huge —' | 'completely massive'",
    3:  "the line ends on a '...' or '…' — e.g. 'and then...' | 'silence'",
    4:  "the dramatic phrase right before a '...' gets its own line — "
        "e.g. 'the driest place on Earth' | '...'",
    5:  "a phrase in quotation marks gets its own line — "
        "e.g. she yelled | 'stop right there'",
    6:  "an aside in (brackets) gets its own line — "
        "e.g. 'the house' | '(built in 1920)' | 'was old'",
    7:  "a scene-setting opener that ends in a comma — "
        "e.g. 'In the morning,' | 'we left'",
    8:  "a comma that separates two clauses or list items — "
        "e.g. 'she ran,' | 'he walked'",
    9:  "a comma that introduces a 'who / which / that...' description — "
        "e.g. 'the dog,' | 'which was huge'",
    10: "a joining word like 'when', 'because', 'which' or 'that' starts a "
        "new part — e.g. 'he left' | 'because it rained'",
    11: "a 'but', 'or', 'so' or 'yet' after a long first part — "
        "e.g. 'we tried for hours' | 'but it failed'",
    12: "one complete action has finished and the next begins — "
        "e.g. 'the baker kneaded the bread' | 'while the fire crackled'",
    13: "a very long wind-up finally reaches its main verb (a safety net for "
        "run-on sentences) — e.g. 'the tall man in the long red coat' | 'walked in'",
    14: "a long 'in / on / at / with...' phrase — "
        "e.g. 'she hid' | 'beneath the old wooden floor'",
    15: "a run of things listed with no verb between them — "
        "e.g. 'ribs,' | 'vertebrae,' | 'skulls'",
    16: "another 'thing, the thing, the thing' list that the usual list-finder "
        "misses — e.g. 'the red car' | 'the blue truck'",
    17: "a wrap-up word like 'all', 'both', 'each' or 'every' right after a "
        "list — e.g. 'cars, trucks, bikes' | 'all sped past'",
    18: "a name worth a dramatic reveal — a person, place, date or amount — "
        "e.g. 'a valley called' | 'Wadi Al-Hitan'",
    19: "an amount of money gets its own line — e.g. 'it costs' | '$800,000'",
    20: "a short command on its own — e.g. 'Stop.' | 'Look around.'",
    21: "an 'and' or 'or' joining two complete sentences — "
        "e.g. 'she sang' | 'and he danced'",
    # 22 — intentionally unused (historical gap in the numbering)
    23: "a describing word revealed in the middle of a sentence — "
        "e.g. 'the water that was' | 'freezing cold'",
    24: "a sentence ending on a number or amount — "
        "e.g. 'the whales vanished' | 'millions of years ago'",
    25: "an extra comma-list pattern the basic comma rule misses — "
        "e.g. 'soaked,' | 'frozen,' | 'exhausted'",
    26: "a long 'if / when / because...' opener that ends in a comma — "
        "e.g. 'if you already know the answer,' | 'you can skip ahead'",
    27: "a sentence ending on a pair of describing words — "
        "e.g. 'the place felt' | 'calm and alien'",
    28: "a chunky 'of / in / with...' phrase stuffed with nouns and no verb — "
        "e.g. 'a tale' | 'of kings and battles'",
    29: "an '-ing' or '-ed' word that introduces what comes next — "
        "e.g. 'revealing' | 'a hidden cave'",
    30: "after a place or name, the 'in / on / at...' that says WHERE splits "
        "off — e.g. 'Alvord Desert' | 'in Oregon'",
    31: "a comma after a long, meaty clause — "
        "e.g. 'after searching the whole house for hours,' | 'they gave up'",
    32: "a 'to do something' inside a long sentence — "
        "e.g. 'they travelled for days' | 'to reach the coast'",
    33: "a sentence ending on an 'of ...' phrase — "
        "e.g. 'one of the driest' | 'climates on Earth'",
    34: "an 'is / was ...-ing' action in progress — "
        "e.g. 'the crowd was' | 'slowly gathering'",
    35: "an 'is / looks / feels...' followed by the thing it describes — "
        "e.g. 'the sky is' | 'a deep burning red'",
    36: "an 'it / them + -ing/-ed' description after a small word like 'of' — "
        "e.g. 'the feeling' | 'of being watched'",
    37: "a sentence ending on a describing word plus an 'in / on / for...' "
        "phrase — e.g. 'the road is straight' | 'for absurd distances'",
    38: "a two-word verb (like 'set up', 'sped past') and the thing it acts "
        "on — e.g. 'they set up' | 'a huge tent'",
    39: "the thing after a preposition gets its own line — "
        "e.g. 'shapes appeared' | 'in the rock'",
    40: "a scene-change word like 'then', 'later' or 'suddenly' — "
        "e.g. 'they waited' | 'then everything changed'",
    41: "a joining word like 'as', 'while' or 'if' left hanging as a "
        "cliffhanger — e.g. 'it works' | 'because'",
    42: "'X is / looks / becomes Y' — split to reveal the Y — "
        "e.g. 'the desert becomes' | 'a frozen wasteland'",
    43: "'X has / owns / contains Y' — split to reveal the Y — "
        "e.g. 'the valley holds' | 'ancient whale bones'",
    44: "'X made / built / created Y' — split to reveal the Y — "
        "e.g. 'the river carved' | 'a deep canyon'",
    45: "'X saw / found / knew Y' — split to reveal the Y — "
        "e.g. 'scientists discovered' | 'fossil skeletons'",
    46: "'X moves through / into / across Y' — split to reveal the place — "
        "e.g. 'water flowed' | 'across the plain'",
    47: "'so / such / more ... that / than ...' — split right before the "
        "payoff — e.g. 'so flat' | 'that satellites use it'",
    48: "'X means / equals / stands for Y' — split to reveal the meaning — "
        "e.g. 'the name means' | 'Valley of the Whales'",
    49: "an 'and' / 'or' sitting between two picture-able things — "
        "e.g. 'dunes' | 'and blistering heat'",
    50: "after a title-name like 'Alaric the Goth', split before the verb — "
        "e.g. 'Alaric the Goth' | 'invaded Rome'",
    51: "the first item of a list gets its own line too — "
        "e.g. 'dunes,' | 'heat, and silence'",
    52: "a sentence ending on a label that explains the thing just named — "
        "e.g. 'they found bones' | 'the remains of a whale'",
    # 53–55 — newly numbered (formerly unnumbered splitting rules)
    53: "a number or date right at the start of a sentence — "
        "e.g. 'In 1946,' | 'everything changed'",
    54: "the sentence ends on a describing word or phrase that paints the "
        "picture — e.g. 'the water was' | 'freezing cold'",
    55: "a roughly-this-much amount like 'nearly 500' or 'about two miles' — "
        "e.g. 'it stretched' | 'for nearly 100 miles'",
    # 56–60 — v18 additions
    56: "a comparison with 'like' / 'as if' / 'resembling' — cut to the "
        "compared image — e.g. 'it looked' | 'like a graveyard of giants'",
    57: "an exception reveal with 'except' or 'apart from' — cut to the one "
        "thing left out — e.g. 'everything burned' | 'except one house'",
    58: "a retention hook like 'here's the thing' or 'which brings us to' "
        "gets its own beat — e.g. 'here's the thing' | 'the map was wrong'",
    59: "the doer in a passive 'by ...' phrase — cut to who or what did it — "
        "e.g. 'it was discovered' | 'by a local farmer'",
    60: "a sound word like 'boom' or 'crash' stands alone as an SFX sync "
        "point — e.g. 'and then' | 'boom' | 'the roof came down'",
    61: "a strong verb that introduces a comma list is cut from item one — "
        "e.g. 'the blaze devoured' | 'temples,' | 'villas,'",

    # ---- MERGING RULES (post-processing glue passes) ------------------------
    1000: "a tiny leftover bit (like 'and' or 'the') is attached to the line "
          "BEFORE it, where it belongs — "
          "e.g. 'the curious child' + 'and' → 'the curious child and'",
    1001: "a tiny leftover bit is attached to the line AFTER it, where it "
          "belongs — e.g. 'is' + 'big' → 'is big'",
    1002: "a lonely piece of punctuation ('...', a dash, a stray quote) is "
          "stuck back onto the nearest line — e.g. 'Yep.' + '...' → 'Yep....'",
    1003: "a lone noun is reunited with the 'that / which...' description that "
          "follows it — e.g. 'regions' + 'that are now dry' → "
          "'regions that are now dry'",
    1004: "a short scrap is joined back to the 'to / of / with...' word it "
          "completes — e.g. 'it costs about' + 'two dollars' → "
          "'it costs about two dollars'",
    1005: "a lone '-ing / -ed' verb is reunited with the thing it acts on — "
          "e.g. 'revealing' + 'evidence' → 'revealing evidence'",
    1006: "a stranded 'the / a / this' is joined to its noun — "
          "e.g. 'the' + 'mountain' → 'the mountain'",
    1007: "a 'the / a' is pulled back from the next line onto a wordy "
          "connector, so the next line can start on a real word — "
          "e.g. 'but what if' + 'the' (from 'the planet') → "
          "'but what if the' | 'planet'",
    1008: "a line with nothing you could picture is folded into the line "
          "BEFORE it — e.g. 'dogs run' + 'but' → 'dogs run but'",
    1010: "an idiomatic saying ('the rest is history') is kept whole and "
          "counts as non-visual — hold the previous image through it",
    1009: "a line with nothing you could picture is folded into the line "
          "AFTER it — e.g. 'but' + 'the dog runs' → 'but the dog runs'",
}


def describe_rule(rule_id: int) -> str:
    """Return the human-readable description for a rule id (see
    RULE_DESCRIPTIONS), or a clear placeholder if the id is unknown."""
    return RULE_DESCRIPTIONS.get(rule_id, f"<unknown rule id {rule_id}>")


def describe_rule_ids(rule_ids) -> list[str]:
    """Plain-English descriptions for a whole row's worth of rule ids —
    'why did the splitter cut this line?' in words."""
    return [f"{rid}: {describe_rule(int(rid))}" for rid in (rule_ids or [])]


# -----------------------------------------------------------------------------
# 1.2  SPLIT_RULE_IDS — which positive rule stamps which id
# -----------------------------------------------------------------------------
# Maps a positive-pipeline rule's *function name* to the id it stamps onto a
# chunk when it creates a split (see RULE_DESCRIPTIONS for what each means).
#
# ALL splitting rules are wired (1–60, with 22 being the intentional gap in the
# historical numbering).  When a rule introduces a split, the LEFT piece of that
# split records the rule's id (the recording machinery lives in the splitter's
# split_text_into_sections()).  A boundary is credited to the FIRST rule (in
# pipeline order) that introduces it; later rules that would land on the same
# boundary leave it unchanged, since the split already exists.
SPLIT_RULE_IDS: dict[str, int] = {
    "rule_hard_punct":                  1,
    "rule_dashes":                      2,
    "rule_ellipsis":                    3,
    "rule_pre_ellipsis_reveal":         4,
    "rule_quotes":                      5,
    "rule_brackets":                    6,
    "rule_initial_adverbial_comma":     7,
    "rule_comma_split":                 8,
    "rule_appositive_comma":            9,
    "rule_clause_starters":             10,
    "rule_but_or_coord":                11,
    "rule_verb_clause":                 12,
    "rule_long_lead_in":                13,
    "rule_long_preps":                  14,
    "rule_noun_lists":                  15,
    "rule_bare_noun_lists":             16,
    "rule_list_quantifiers":            17,
    "rule_entity_reveal":               18,
    "rule_currency_reveal":             19,
    "rule_imperative_start":            20,
    "rule_and_or_clause":               21,
    # 22 — intentional gap (no rule owns this number)
    "rule_adjective_reveal":            23,
    "rule_numeric_phrase_reveal":       24,
    "rule_comma_list_extension":        25,
    "rule_long_subord_comma":           26,
    "rule_terminal_adj_coord":          27,
    "rule_pp_intro_reveal":             28,
    "rule_participle_split":            29,
    "rule_post_entity_split":           30,
    "rule_long_clause_comma":           31,
    "rule_infinitive_split":            32,
    "rule_terminal_of_reveal":          33,
    "rule_progressive_split":           34,
    "rule_copula_attr_reveal":          35,
    "rule_pron_participle_pp_reveal":   36,
    "rule_terminal_pp_after_copula":    37,
    "rule_phrasal_object_reveal":       38,
    "rule_prep_object_reveal":          39,
    "rule_transition_adverb":           40,
    "rule_sconj_hang":                  41,
    "rule_copula_reveal_split":         42,
    "rule_possession_reveal_split":     43,
    "rule_creation_reveal_split":       44,
    "rule_perception_reveal_split":     45,
    "rule_spatial_prep_reveal_split":   46,
    "rule_result_clause_reveal_split":  47,
    "rule_equation_reveal_split":       48,
    "rule_and_visualisables_split":     49,
    "rule_title_appositive_verb_split": 50,
    "rule_first_list_item_split":       51,
    "rule_terminal_specifier_reveal":   52,
    # 53–55 — the three rules the codebase never gave a header number
    "rule_numeric_intro_reveal":        53,
    "rule_terminal_descriptor":         54,
    "rule_numeric_approximator_reveal": 55,
    # 56–60 — v18 additions
    "rule_comparison_reveal":           56,
    "rule_exception_reveal":            57,
    "rule_discourse_pivot":             58,
    "rule_passive_agent_reveal":        59,
    "rule_sfx_beat":                    60,
    "rule_verb_list_reveal":            61,
}


# -----------------------------------------------------------------------------
# 1.3  THE GROUPS — splitter rule ids bundled by what they tell the TAGGER
# -----------------------------------------------------------------------------
# "This line carries rule 15" means the splitter PROVED (with the full parse)
# that the line is part of a list run. That is stronger evidence than anything
# the tagger can work out on its own, so the detectors check these first.
LIST_RULES = {15, 16, 17, 25, 51, 61}    # runs of listed things
QUOTE_RULES = {5}                        # a phrase in quotation marks
MONEY_RULES = {19}                       # an amount of money
NUMBER_RULES = {24, 55}                  # sentence-final / approx amounts
DATE_RULES = {53}                        # a number or date opening a sentence
NAME_REVEAL_RULES = {18, 50}             # a name worth a dramatic reveal
RELATIVE_RULES = {14, 30, 39}            # long in/on/at... place phrases
NONVISUAL_RULES = {1008, 1009, 1010}     # merged-back non-visual scraps
SOUND_RULES = {60}                       # boom/crash SFX sync points

# Rule ids that mark a chunk as an item of a list RUN (used by the splitter's
# own per-line metadata builder — a narrower set than LIST_RULES).
LIST_RUN_RULE_IDS = {15, 16, 25, 51}


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 2 — WORD LISTS, REGEXES AND THRESHOLDS                    ### #
# ###               ( D A T A   O N L Y  —  no functions below here       ### #
# ###                 until SECTION 3. Everything hardcoded in this       ### #
# ###                 folder lives in this section. )                     ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================


# =============================================================================
# 2.1  PUNCTUATION & SYMBOLS
# =============================================================================

# Sentence-final punctuation — always closes a line.
HARD_PUNCT       = {".", "!", "?", ";", ":"}

# Dashes — em (—), en (–), figure (—), double-hyphen (--), single hyphen (-),
# minus (−).  In-word hyphens are filtered out via whitespace check.
DASH_PUNCT       = {"—", "–", "--", "-", "−"}

# "Long" dashes — em / en / double-hyphen.  These ALWAYS earn a split before
# AND after themselves when whitespace-flanked.  Single hyphen "-" is more
# delicate (could be in-word) and handled separately in rule_dashes.
LONG_DASH_PUNCT  = {"—", "–", "--"}

# Quotation marks — straight, smart, French «», German „".
OPEN_QUOTES      = {'"', "“", "‘", "«", "‹", "„", "‚", "`"}
CLOSE_QUOTES     = {'"', "”", "’", "»", "›"}
ANY_QUOTE        = OPEN_QUOTES | CLOSE_QUOTES

# Apostrophes — the contraction / possessive markers ("it's", "sailors'").
APOSTROPHES      = {"'", "’", "‘", "`"}

# Brackets.
OPEN_BRACKETS    = {"(", "[", "{"}
CLOSE_BRACKETS   = {")", "]", "}"}

# Currency symbols — split BEFORE these when followed by a digit.
CURRENCY_SYMS    = {"$", "£", "€", "¥", "₹", "₽", "¢"}

# Markdown emphasis characters — *bold*, _italic_.  Never split inside a pair.
MARKDOWN_EMPHASIS_CHARS = {"*", "_"}


# =============================================================================
# 2.2  spaCy TAG / DEP / ENTITY LABEL SETS
#      These are the PREFERRED way to identify things — they generalise across
#      vocabulary, where a word list never can.
# =============================================================================

# Penn-Treebank tags for wh-words.  Replaces any hard-coded
# "where/that/who/..." list — generalises to who / whom / whose / what /
# which / where / when / why / how with no string matching.
#   WDT  wh-determiner    : that, which, what
#   WP   wh-pronoun       : who, whom
#   WP$  poss. wh-pronoun : whose
#   WRB  wh-adverb        : where, when, why, how
WH_TAGS          = {"WDT", "WP", "WP$", "WRB"}

# The same set under the name the merge passes use for relative pronouns.
RELATIVE_PRONOUN_TAGS = WH_TAGS

# Named-entity types worth introducing on their own line ("the reveal").
REVEAL_ENTS      = {
    "PERSON", "ORG", "GPE", "LOC", "FAC", "NORP",
    "EVENT", "WORK_OF_ART", "PRODUCT", "LAW", "LANGUAGE",
    "DATE", "TIME", "MONEY", "QUANTITY", "PERCENT",
}

# Multi-token entity types that earn a "post-entity" split (RULE 30).
LOCATION_ENTS    = {"GPE", "LOC", "FAC", "ORG", "PERSON", "EVENT", "WORK_OF_ART"}

# Numeric/measure entities — atomic, never cut internally.
NUMERIC_ENTS     = {"CARDINAL", "ORDINAL", "QUANTITY", "MONEY",
                    "DATE", "TIME", "PERCENT"}

# Numeric ent labels we DON'T want to "reveal" when single-token (these are
# usually part of measure phrases like "thousands of kilometers").
NUMERIC_NO_REVEAL = {"CARDINAL", "QUANTITY", "PERCENT", "ORDINAL"}

# Numeric/measurement entity types that, when preceded by an ADP in a shorter
# sentence, are qualifier PPs ("in the 19th century", "for 40 years", "by
# 1946") rather than reveals.  Used by RULE 18 condition (ii').
NUMERIC_QUALIFIER_ENTS = {"DATE", "TIME", "MONEY", "QUANTITY",
                          "PERCENT", "CARDINAL", "ORDINAL"}

# Entity labels that vouch for a capitalised run really being a NAME
# (the tagger's wikipedia check).
NAME_ENT_LABELS  = {"PERSON", "ORG", "EVENT", "WORK_OF_ART", "FAC", "GPE",
                    "NORP"}

# ...and the subset that vouches for a SINGLE capitalised word. NORP is
# excluded on purpose: "European" is NORP, and a lone nationality adjective is
# not a name worth a wikipedia image.
NAME_ENT_LABELS_FOR_SINGLE_WORD = NAME_ENT_LABELS - {"NORP"}

# Entity labels for a specific PLACE (the tagger's map check).
PLACE_ENT_LABELS = {"GPE", "LOC"}

# A verb whose dep is one of these is *not heading a top-level clause*,
# so we do NOT use it as a clause boundary.  Includes:
#   amod          : "the running man"
#   acl, acl:relcl: "the man (who is) running for office"
#   advcl         : "running fast, he tripped" / "while the fire crackled"
#   relcl         : UD relative-clause label
#   ccomp         : clausal complement — "she said [he left]"
#   xcomp         : open clausal complement — "tries [to leave]", "left [stranded]"
#   oprd          : object predicate — "called him [crazy]"
#   csubj         : clausal subject — "[that he came] surprised her"
# Adding ccomp/xcomp/oprd keeps verb chains together inside subordinate
# clauses (fixes "feels like it's remembering being underwater" etc.).
VERB_MOD_DEPS    = {"amod", "acl", "acl:relcl", "advcl", "relcl",
                    "ccomp", "xcomp", "oprd", "csubj"}

# Aux / negation deps — never split between aux/neg and the main verb.
#   "doesn't find", "had been running", "is going", "won't say"
AUX_LIKE_DEPS    = {"aux", "auxpass", "neg"}

# Phrasal-verb particle dep — keep the particle joined to its verb.
#   "sped past", "laid down", "set up", "look around"
PARTICLE_DEPS    = {"prt", "compound:prt"}

# POS classes that are too "lightweight" to stand alone as a chunk —
# fragments containing only these will be merged into a neighbour.
LIGHTWEIGHT_POS  = {"CCONJ", "SCONJ", "DET", "ADP", "PART", "PRON", "AUX",
                    "ADV", "INTJ"}


# =============================================================================
# 2.3  FUNCTION WORDS & CLOSED CLASSES
#      Small, genuinely-closed word classes — the one place a word list beats
#      a tag, because the class never grows.
# =============================================================================

# Prepositions that almost never want a split AFTER them (bind tightly to NP).
PROMISCUOUS_PREPS = {"of"}

# Sentence-initial discourse markers that cling to the rest of their sentence
# even when followed by a comma ("Anyway, here is..." → no split after "Anyway,").
DISCOURSE_INIT   = {
    "anyway", "well", "so", "now", "yeah", "yep", "okay", "ok", "right",
    "honestly", "actually", "basically", "essentially", "literally",
    "obviously", "clearly", "frankly", "interestingly", "ironically",
    "fortunately", "unfortunately", "naturally",
    "hmm", "huh", "oh", "ah",
}

# Adverbs that introduce a reveal entity ("specifically Kerala", "namely Smith").
# When one of these immediately precedes a reveal entity, allow the reveal
# even with only 1 token of lead-in.
ADV_INTRODUCERS  = {
    "specifically", "especially", "namely", "particularly", "notably",
    "essentially", "primarily", "mainly", "chiefly", "principally",
    "exactly", "precisely", "literally",
}

# Adverbs that signal a new visual shot/scene transition.
TRANSITION_ADVERBS = {
    "then", "later", "suddenly", "eventually", "finally",
    "afterwards", "subsequently", "next", "soon", "now",
    # v18.4 expansion — more scene-change cues
    "afterward", "meanwhile", "immediately", "instantly", "overnight",
    "tonight", "today", "tomorrow", "yesterday", "abruptly", "gradually",
}

# Comparative markers — when a verb's dobj subtree contains one, the dobj is
# a "reveal NP" worth splitting before instead of gluing.
COMPARATIVE_MARKERS = {"than", "more", "less", "fewer"}

# Negation tokens — when one immediately follows a copula, the split
# should land AFTER the negation so the contraction reads as one unit:
# "isn't | really big"  not  "is | n't really big"
NEGATION_TOKENS = {"n't", "not", "never"}

# Approximator words — "nearly 500", "about two miles".  Both the splitter's
# approximate-amount rule and its anti-rule used to keep private copies.
APPROXIMATOR_WORDS = {"nearly", "almost", "about", "roughly", "approximately",
                      "around", "over", "just", "only", "barely", "merely"}

# Pronouns that make a line lean on an earlier subject ("It was worth...").
# "you" is deliberately absent — narration addresses the viewer constantly.
ANAPHORIC_SUBJECT_PRONOUNS = {"it", "they", "he", "she"}

# Prepositions that bind too tightly to their noun to split after them when
# scanning a run of noun chunks for a list (RULE 15).
NOUN_LIST_BLOCKED_PREPS = {
    "of", "with", "for", "about", "as", "like", "than", "per", "via"
}

# ...and the prepositions between two noun chunks that DO earn a split.
NOUN_LIST_SPLITTABLE_PREPS = {
    # location
    "in", "at", "on", "under", "over", "above", "below", "beneath",
    "behind", "beside", "between", "among", "around", "near", "by",
    "inside", "outside", "within", "into", "onto", "through", "across",
    "along", "past", "toward", "towards", "off", "up", "down",
    "underneath", "against", "upon",
    # time
    "after", "before", "during", "since", "until", "till"
}


# =============================================================================
# 2.4  VERB MEANING FAMILIES
#      Closed-ish lemma sets that say WHAT KIND of thing a verb does. Every
#      one of them is only ever a CANDIDATE filter: the actual decision is
#      made structurally, by the SECTION 5 checks reading spaCy's dep labels
#      (hardcoded lemma → NLP disambiguator → fire/skip).
# =============================================================================

# -----------------------------------------------------------------------------
# COPULA LEMMAS  (Family 1 — linking verbs / "X is/looks/becomes Y")
# -----------------------------------------------------------------------------
# "be" group — the only sub-family that REQUIRES an intensifier or
# substantial complement to trigger a reveal split.  Bare "is + ADJ"
# does NOT split — "is happy", "is simple", "is busy" stay whole.
COPULA_BE_LEMMAS = {"be"}

# Sensory copulas — perception-based linking verbs.  Fire even on a
# bare ADJ complement because the verb itself carries reveal weight.
# "looks microscopic" splits at "looks |".
COPULA_SENSORY_LEMMAS = {
    "look", "sound", "feel", "taste", "smell", "seem", "appear",
}

# Becoming copulas — transformation linking verbs.  Disambiguator
# blocks transitive uses ("got a letter", "turned the wheel").
COPULA_BECOMING_LEMMAS = {
    "become", "get", "grow", "turn", "go", "come", "fall", "run",
}

# Staying copulas — persistence linking verbs.
COPULA_STAYING_LEMMAS = {
    "remain", "stay", "keep", "continue",
}

# Judgment / verdict copulas.
COPULA_JUDGMENT_LEMMAS = {
    "prove",
}

# "Strong" copulas — fire on a bare ADJ complement (no intensifier needed).
STRONG_COPULA_LEMMAS = (COPULA_SENSORY_LEMMAS
                        | COPULA_BECOMING_LEMMAS
                        | COPULA_STAYING_LEMMAS
                        | COPULA_JUDGMENT_LEMMAS)

# Full union — every lemma the copula rule recognises.
ALL_COPULA_LEMMAS = COPULA_BE_LEMMAS | STRONG_COPULA_LEMMAS

# The broader "acts as a copula here" sets used by the copula-ATTRIBUTE
# reveal (RULE 35), which matches on surface form as well as lemma.
COPULAR_LEMMAS = {
    "be", "feel", "look", "seem", "appear", "become", "remain", "stay",
    "sound", "taste", "smell", "prove", "turn"
}
COPULAR_FORMS = {
    "is", "are", "was", "were", "am", "be", "been", "being",
    "'s", "’s", "'re", "’re", "'m", "’m",
    "feels", "looks", "seems", "appears", "becomes", "remains", "stays",
    "felt", "looked", "seemed", "appeared", "became", "remained", "stayed"
}

# -----------------------------------------------------------------------------
# POSSESSION LEMMAS  (Family 2 — "X has/owns/contains/lacks Y")
# -----------------------------------------------------------------------------
# Verbs whose direct-object subtree is the reveal payload.  Each verb's "is
# this a reveal use" decision is fully structural (see
# verb_has_substantial_object): verb has a dobj, the dobj subtree is
# substantial, and the verb's own dep is not aux/auxpass (which filters
# perfect-aspect "have").
#
# Excluded by design:
#   • hold, carry, bear — too ambiguous between contain-sense and
#     transport/grip-sense; both take dobj so structural disambiguation
#     isn't reliable.
#   • want — desire-sense vs lack-sense too overlapping.

# Core possession.  "have" is the trickiest because it doubles as perfect-
# aspect auxiliary; the dep != aux check filters that out.
POSSESSION_CORE_LEMMAS = {"have", "own", "possess"}

# Containment verbs.
POSSESSION_CONTAIN_LEMMAS = {
    "contain", "include", "comprise", "encompass",
}

# Featuring / providing verbs.
POSSESSION_FEATURE_LEMMAS = {
    "feature", "boast", "offer", "provide", "present",
}

# Negative possession — often a punchline reveal.
POSSESSION_NEGATIVE_LEMMAS = {
    "lack", "miss", "need", "require",
}

# Hidden-containment.
POSSESSION_HIDDEN_LEMMAS = {
    "harbor", "harbour", "house",
}

ALL_POSSESSION_LEMMAS = (POSSESSION_CORE_LEMMAS
                         | POSSESSION_CONTAIN_LEMMAS
                         | POSSESSION_FEATURE_LEMMAS
                         | POSSESSION_NEGATIVE_LEMMAS
                         | POSSESSION_HIDDEN_LEMMAS)

# -----------------------------------------------------------------------------
# CREATION LEMMAS  (Family 3 — "X produced/created/built Y")
# -----------------------------------------------------------------------------
# Verbs whose direct-object subtree IS the visual reveal — what was made,
# built, designed, formed, or transformed.  Same structural pattern as
# Family 2 (possession): verb + substantial dobj → split AFTER verb.
#
# Excluded by design:
#   • "make" — too ambiguous: causative ("make him cry"), idiomatic
#     ("make sense"), and creation ("make a chair") all take dobj-like
#     structures.  The causative/idiomatic uses dominate by frequency.
#   • "grow" — could be cultivation ("grew tomatoes") but also copular
#     ("grew tired"), already in Family 1's COPULA_BECOMING set.
#   • "form" — same conflict: "form a circle" vs "crystals form quickly".
#   • "cast" — too ambiguous: cast a vote / cast iron / cast a shadow.

# Production / manufacture.
CREATION_PRODUCE_LEMMAS = {
    "produce", "manufacture", "generate", "fabricate", "yield",
}

# Building / construction.
CREATION_BUILD_LEMMAS = {
    "build", "construct", "assemble", "erect", "raise",
}

# Pure creation (bringing into existence).
CREATION_CREATE_LEMMAS = {
    "create", "invent", "conceive", "establish", "found", "launch",
    "introduce",
}

# Crafting / shaping (hands-on creation).
CREATION_CRAFT_LEMMAS = {
    "craft", "shape", "sculpt", "mold", "mould", "forge",
}

# Design / development.
CREATION_DESIGN_LEMMAS = {
    "design", "develop", "devise", "engineer", "pioneer", "architect",
}

# Causation / triggering — reveals what was caused or made possible.
CREATION_CAUSE_LEMMAS = {
    "cause", "trigger", "spark", "prompt", "drive",
}

# Enabling — reveals what was made possible.
CREATION_ENABLE_LEMMAS = {
    "enable", "allow", "permit", "let",
}

ALL_CREATION_LEMMAS = (CREATION_PRODUCE_LEMMAS
                       | CREATION_BUILD_LEMMAS
                       | CREATION_CREATE_LEMMAS
                       | CREATION_CRAFT_LEMMAS
                       | CREATION_DESIGN_LEMMAS
                       | CREATION_CAUSE_LEMMAS
                       | CREATION_ENABLE_LEMMAS)

# -----------------------------------------------------------------------------
# PERCEPTION / COGNITION LEMMAS  (Family 4 — "X sees/finds/knows Y")
# -----------------------------------------------------------------------------
# Verbs whose dobj OR ccomp carries the reveal payload — what was seen,
# found, realized, claimed, suggested.  Accepts BOTH dobj-style ("noticed
# the stain") and ccomp-style ("noticed that he was late") complements.

# Perceive — direct sensory perception.
PERCEPTION_SEE_LEMMAS = {
    "see", "spot", "notice", "observe", "witness", "glimpse",
    "perceive", "detect",
}

# Find — discovery / encounter.
PERCEPTION_FIND_LEMMAS = {
    "find", "discover", "uncover", "unearth", "encounter",
}

# Realize — cognitive arrival.
PERCEPTION_REALIZE_LEMMAS = {
    "realize", "realise", "recognize", "recognise",
    "understand", "grasp", "comprehend",
}

# Think — opinion / supposition.  v18: + picture/envision/visualise —
# direct-address imagery cues ("picture a wall of water ten stories tall").
PERCEPTION_THINK_LEMMAS = {
    "think", "believe", "suspect", "assume", "suppose", "reckon",
    "imagine", "guess",
    "picture", "envision", "visualize", "visualise",
}

# Know / mean / imply — knowledge & signification.
PERCEPTION_KNOW_LEMMAS = {
    "know", "mean", "signify", "imply", "indicate", "suggest",
}

# Reveal — disclosure verbs (often the reveal IS the punchline).
PERCEPTION_REVEAL_LEMMAS = {
    "reveal", "show", "demonstrate", "expose", "disclose",
}

# Say — speech-act introducers.  v18: + swear/insist/warn/whisper/promise —
# unquoted claims ("locals swear the lights move on their own").
PERCEPTION_SAY_LEMMAS = {
    "say", "claim", "argue", "declare", "announce", "report",
    "state", "mention", "admit", "confess",
    "swear", "insist", "warn", "whisper", "promise",
}

# Sense — v18: non-visual sensory perception ("you can hear the ice
# cracking", "watch the tide swallow the road").  The copular uses of
# "feel"/"smell"/"taste" ("feels cold") take acomp, not dobj/ccomp, so
# verb_has_substantial_complement keeps them out of RULE 45 automatically —
# only the transitive perception uses fire.
PERCEPTION_SENSE_LEMMAS = {
    "hear", "overhear", "watch", "smell", "taste", "sense", "feel",
}

ALL_PERCEPTION_LEMMAS = (PERCEPTION_SEE_LEMMAS
                         | PERCEPTION_FIND_LEMMAS
                         | PERCEPTION_REALIZE_LEMMAS
                         | PERCEPTION_THINK_LEMMAS
                         | PERCEPTION_KNOW_LEMMAS
                         | PERCEPTION_REVEAL_LEMMAS
                         | PERCEPTION_SAY_LEMMAS
                         | PERCEPTION_SENSE_LEMMAS)

# -----------------------------------------------------------------------------
# EQUATION / DEFINITION LEMMAS  (Family 7 — "X means/equals/represents Y")
# -----------------------------------------------------------------------------
# Verbs whose complement defines, equates, or signifies the subject.  These
# often appear in short definitional sentences ("X means Y."), so the
# sentence-length threshold is lower than Families 2-4.

# Direct equation.
EQUATION_EQUAL_LEMMAS = {
    "equal", "represent", "signify", "symbolize", "symbolise",
    "denote", "stand",  # "stands for X"
}

# Definition / explanation.
EQUATION_MEAN_LEMMAS = {
    "mean", "imply", "indicate", "suggest", "translate",  # "translates to/into"
}

# Refer / amount to.
EQUATION_REFER_LEMMAS = {
    "refer",  # "refers to"
    "amount",  # "amounts to"
    "boil",    # "boils down to"
    "come",    # "comes down to"
}

ALL_EQUATION_LEMMAS = (EQUATION_EQUAL_LEMMAS
                       | EQUATION_MEAN_LEMMAS
                       | EQUATION_REFER_LEMMAS)

# Some equation verbs are phrasal — they need a specific particle/prep
# to be in the equation sense.  Without the particle, they're something
# else entirely:
#   • "stand" alone = stand up.  "stand for X" = represent.
#   • "refer" alone = make a reference.  "refer to X" = mean.
#   • "amount" alone = (rare).  "amount to X" = equal.
#   • "boil" alone = cook.  "boil down to X" = equal in essence.
#   • "come" alone = arrive.  "come down to X" = equal in essence.
#   • "translate" alone = render.  "translate to/into X" = equate.
EQUATION_PHRASAL_PARTICLES = {
    "stand": {"for"},
    "refer": {"to"},
    "amount": {"to"},
    "boil": {"to"},          # "boil down to" — "to" is the key prep
    "come": {"to"},          # "comes down to" — "to" is the key prep
    "translate": {"to", "into"},
}

# -----------------------------------------------------------------------------
# COMPARISON LEMMAS  (RULE 56)
# -----------------------------------------------------------------------------
# Resemblance verbs whose dobj IS the compared image ("resembles a giant
# ribcage").  Kept tiny; "look/seem/appear like" is handled via the
# ADP-'like' branch, not here.
COMPARISON_RESEMBLE_LEMMAS = {"resemble", "mimic", "mirror"}

# -----------------------------------------------------------------------------
# RESULT-CLAUSE INTENSIFIERS  (Family 6 — "so X that Y", "more X than Y")
# -----------------------------------------------------------------------------
# Words that, when paired with a downstream connector ("that", "than", "to"),
# form a result-clause construction whose downstream payload is the reveal:
#   • "so" + ADJ/ADV + "that"          → split AFTER "that"
#   • "such" + (DET) + NOUN + "that"   → split AFTER "that"
#   • "more/less/fewer" + ... + "than" → split AFTER "than"
#   • "too" + ADJ + "to"               → split AFTER "to"
#   • ADJ + "enough" + "to"            → split AFTER "to"
# Without an intensifier the connector is not a result-clause introducer
# ("the man that left", "I want to leave").

# Intensifiers that, paired with "that", introduce a result clause.
RESULT_THAT_INTENSIFIERS = {"so", "such"}

# Intensifiers that, paired with "than", introduce a comparison.
# (RESULT_THAN comparatives also include JJR/RBR tags — handled
# structurally, not by this set.)
RESULT_THAN_INTENSIFIERS = {"more", "less", "fewer"}

# Intensifiers that, paired with "to + VERB", introduce a result.
RESULT_TO_INTENSIFIERS = {"too", "enough"}

# Combined for fast membership check
ALL_RESULT_INTENSIFIERS = (RESULT_THAT_INTENSIFIERS
                           | RESULT_THAN_INTENSIFIERS
                           | RESULT_TO_INTENSIFIERS)


# =============================================================================
# 2.5  WEAK (NON-VISUAL) VOCABULARY
#      Words that paint no picture on their own. A line made only of these
#      cannot be filmed, so it holds the previous image instead.
# =============================================================================

# Lemma set for "weak" verbs that shouldn't qualify a chunk as visualisable
# on their own — copulas and similar functional verbs.  A chunk containing
# ONLY weak verbs (no nouns, no adjectives, no concrete verbs) is essentially
# a connective phrase ("It's", "that has", "which were", "had been").
WEAK_VERB_LEMMAS = {
    "be", "have", "do", "get", "make", "go", "come",
    "seem", "appear", "become", "remain", "stay",
    # v18.2 — transactional / light verbs that paint no picture on their
    # own ("It costs", "Which brings us to", "what you're holding").  The
    # image, when there is one, always lives in the accompanying NOUN, so
    # excluding these verbs from visualisability/keywords never loses a
    # visual — it only stops verb-only lines counting as picture-able and
    # verb-only search terms ("costs", "brings", "find") being emitted.
    "cost", "bring", "take", "find", "mean", "keep", "let", "need",
    "want", "know", "think", "say", "tell", "happen", "matter",
    "include", "involve", "require", "use", "try", "hold",
    # v18.4 expansion — perception/cognition/aspect verbs whose image
    # always lives in the accompanying noun
    "allow", "begin", "start", "stop", "end", "continue", "cause",
    "help", "consider", "believe", "decide", "expect", "feel", "hear",
    "see", "look", "call", "ask", "understand", "realize", "realise",
    "remember", "forget", "learn", "notice", "wonder", "agree",
    "describe", "explain", "refer", "relate", "depend", "occur",
    "exist", "provide", "offer", "receive", "own", "contain", "belong",
    "manage", "fail", "attempt", "plan", "intend", "tend", "suppose",
}

# All surface forms of the above weak verbs, including clitic contractions.
# Some spaCy parses tag clitics with non-canonical lemmas ("'re" instead of
# "be") so the lemma-based check alone misses them.  We also check the
# token's text.lower() against this set.
WEAK_VERB_FORMS = {
    # be
    "be", "am", "is", "are", "was", "were", "been", "being",
    "'s", "’s", "'re", "’re", "'m", "’m", "'ve", "’ve",
    # have
    "have", "has", "had", "having", "'d", "’d", "'ll", "’ll",
    # do
    "do", "does", "did", "done", "doing",
    # get
    "get", "gets", "got", "gotten", "getting",
    # make
    "make", "makes", "made", "making",
    # go
    "go", "goes", "went", "gone", "going",
    # come
    "come", "comes", "came", "coming",
    # seem / appear / become / remain / stay
    "seem", "seems", "seemed", "seeming",
    "appear", "appears", "appeared", "appearing",
    "become", "becomes", "became", "becoming",
    "remain", "remains", "remained", "remaining",
    "stay", "stays", "stayed", "staying",
    # v18.2 additions (irregular surfaces of the new weak lemmas)
    "cost", "costs", "brought", "bring", "brings", "bringing",
    "take", "takes", "took", "taken", "taking",
    "find", "finds", "found", "finding",
    "mean", "means", "meant", "meaning",
    "keep", "keeps", "kept", "keeping",
    "know", "knows", "knew", "known", "knowing",
    "think", "thinks", "thought", "thinking",
    "say", "says", "said", "saying", "tell", "tells", "told", "telling",
    "hold", "holds", "held", "holding",
    # v18.4 irregular surfaces of the expanded weak lemmas
    "began", "begun", "saw", "seen", "heard", "felt", "understood",
    "forgot", "forgotten", "learnt", "realised", "realized",
}

# Lemma set for "weak" adjectives — quantifier-ish words that spaCy tags
# as ADJ but don't add visual content on their own.  A chunk like "many",
# "much", "few", "some" alone is not visualisable.  Real descriptive ADJs
# ("red", "tiny", "ancient", "brilliant") are visualisable.
WEAK_ADJ_LEMMAS = {
    "many", "much", "more", "less", "few", "fewer", "some", "any",
    "such", "other", "same", "different", "various", "several",
    "certain", "particular", "specific", "general",
    "own", "whole", "entire", "main", "only", "very", "too",
    # v18.4 expansion — quantity / hedging / abstract adjectives that paint
    # no picture on their own
    "numerous", "countless", "multiple", "additional", "further", "extra",
    "overall", "usual", "common", "typical", "normal", "standard",
    "possible", "likely", "probable", "potential", "available", "able",
    "mere", "single", "sole", "former", "latter", "recent", "current",
    "actual", "eventual", "respective", "relevant", "similar", "equal",
}

# A FALLBACK ONLY — the tiny finite-verb stoplist used when NO spaCy model is
# installed. With the model, "is this a clause not a thing?" is answered from
# POS tags (VERB/AUX); see has_finite_verb().
CLAUSE_WORDS_FALLBACK = (
    "is|are|was|were|be|been|being|has|have|had|do|does|did|meant|means|"
    "getting|got|went|goes|came|comes|said|says|made|makes|ran|runs|"
    "sailing|surviving|took|takes|gave|gives|becomes?|became|ruled|rules"
)


# =============================================================================
# 2.6  MEASUREMENT & NUMBER WORDS
# =============================================================================

# Common measurement / time / quantifier words that mustn't split from a
# preceding number — "15 meters", "40 million", "3 thousand years".
MEASURE_NOUNS    = {
    "meter", "meters", "metre", "metres",
    "foot", "feet", "yard", "yards", "mile", "miles",
    "kilometer", "kilometers", "kilometre", "kilometres",
    "inch", "inches", "centimeter", "centimeters", "centimetre", "centimetres",
    "millimeter", "millimeters", "millimetre", "millimetres",
    "pound", "pounds", "kilogram", "kilograms", "kilo", "kilos",
    "ton", "tons", "tonne", "tonnes", "ounce", "ounces", "gram", "grams",
    "thousand", "million", "billion", "trillion", "hundred", "dozen",
    "second", "seconds", "minute", "minutes", "hour", "hours",
    "day", "days", "week", "weeks", "month", "months",
    "year", "years", "decade", "decades", "century", "centuries",
    "millennium", "millennia",
    "degree", "degrees", "percent", "percentage",
}


# =============================================================================
# 2.7  SPATIAL PREPOSITIONS  (Family 5 — "X moves/sits/extends through Y")
# =============================================================================
# Prepositions whose object NP is a visual locative or trajectory reveal.
# Distinguishes "spatial-reveal" preps from "qualifier" preps:
#   • SPATIAL preps open into rich locative / directional NPs that paint a
#     visual ("through heat haze for absurd distances") — they earn a split
#     AFTER the prep when the subtree is substantial.
#   • QUALIFIER preps bind tightly to their head NP ("of India", "with care")
#     and should NOT be split after.  These overlap with PROMISCUOUS_PREPS.
#
# Notes on tricky members:
#   • "around" — spatial usually, but also approximative ("around 40 years")
#     which spaCy tags as a quantmod / advmod, not an ADP.  The ADP-pos
#     check filters that out.
#   • "over" — spatial AND temporal AND "about" sense.  All accepted, since
#     they all open into rich subtrees.
#   • "before / after / during / since / until" — primarily temporal, but
#     visually-rich temporal reveals still benefit from a split.  Included.

# Locative / static-position prepositions.
SPATIAL_LOCATIVE_PREPS = {
    "in", "on", "at", "under", "beneath", "below", "above", "over",
    "behind", "between", "among", "amongst", "amid", "amidst",
    "around", "near", "beside", "inside", "outside", "within",
    "throughout", "against", "atop", "upon", "underneath",
}

# Directional / trajectory prepositions.
SPATIAL_DIRECTIONAL_PREPS = {
    "into", "onto", "through", "across", "along", "past",
    "toward", "towards", "beyond", "off", "via",
}

# Temporal prepositions — often qualifier-flavored, but accepted when
# subtree is rich enough.
SPATIAL_TEMPORAL_PREPS = {
    "during", "since", "until", "till", "before", "after", "while",
}

ALL_SPATIAL_PREPS = (SPATIAL_LOCATIVE_PREPS
                     | SPATIAL_DIRECTIONAL_PREPS
                     | SPATIAL_TEMPORAL_PREPS)


# =============================================================================
# 2.8  FIXED MULTI-WORD PHRASES
#      Word sequences that mean something as a UNIT — never cut inside one.
# =============================================================================

# Frozen bigrams we never split inside (kept tiny — POS/DEP do the rest).
# When token i.lower_ == first and (i+1).lower_ == second, no split is
# allowed between them.
FROZEN_BIGRAMS   = {
    ("what", "if"),       # hypothetical opener
    ("as", "if"),         # similarity
    ("even", "if"),
    ("as", "though"),
    ("kind", "of"),       # hedges
    ("sort", "of"),
    ("type", "of"),
    ("a", "lot"),
    ("at", "least"),
    ("at", "most"),
    ("at", "all"),
    ("of", "course"),
    ("used", "to"),       # "used to be" — keep verb glued to "to"
    ("able", "to"),
    ("going", "to"),
    ("have", "to"),
    ("had", "to"),
    ("got", "to"),
    ("want", "to"),
    ("wants", "to"),
    ("wanted", "to"),
    ("need", "to"),
    ("needs", "to"),
    ("needed", "to"),
    ("try", "to"),
    ("tried", "to"),
}

# Idiomatic SAYINGS — a stretch of words that means something as a UNIT and
# paints no literal picture ("the rest is history" is not about history
# footage).  Two effects, both automatic:
#   1. the splitter never cuts INSIDE an idiom — it stays one line;
#   2. its tokens don't count as keywords/nouns/visualisable content, so an
#      idiom-only line correctly reads as "hold the previous image".
# Token tuples, lowercased, matched against the parsed doc.
IDIOM_PHRASES = {
    ("the", "rest", "is", "history"),
    ("long", "story", "short"),
    ("at", "the", "end", "of", "the", "day"),
    ("when", "all", "is", "said", "and", "done"),
    ("believe", "it", "or", "not"),
    ("truth", "be", "told"),
    ("needless", "to", "say"),
    ("as", "it", "turns", "out"),
    ("for", "what", "it", "'s", "worth"),
    ("against", "all", "odds"),
    ("sooner", "or", "later"),
    ("little", "did", "they", "know"),
    ("little", "did", "he", "know"),
    ("little", "did", "she", "know"),
    ("lo", "and", "behold"),
    ("by", "and", "large"),
    ("all", "things", "considered"),
    ("time", "will", "tell"),
    ("in", "a", "nutshell"),
    ("out", "of", "the", "blue"),
    ("come", "rain", "or", "shine"),
    ("against", "the", "odds"),
    ("easier", "said", "than", "done"),
    ("last", "but", "not", "least"),
    ("more", "or", "less"),
    ("give", "or", "take"),
    ("one", "way", "or", "another"),
}

# Fixed multi-word retention hooks (RULE 58).  Matched on lowercase token
# sequences (spaCy tokenizes "here's" as "here" + "'s"; smart apostrophes are
# normalised in the matcher).  Purely lexical by design — these are
# script-writing idioms, not grammar.
DISCOURSE_PIVOT_PHRASES: list[tuple[str, ...]] = [
    ("here", "'s", "the", "thing"),  ("here", "is", "the", "thing"),
    ("here", "'s", "the", "catch"),  ("here", "is", "the", "catch"),
    ("here", "'s", "the", "kicker"), ("here", "is", "the", "kicker"),
    ("here", "'s", "why"),           ("here", "is", "why"),
    ("which", "brings", "us", "to"), ("which", "brings", "me", "to"),
    ("believe", "it", "or", "not"),
    ("long", "story", "short"),
    ("it", "gets", "worse"), ("it", "gets", "better"),
    ("it", "gets", "weirder"), ("it", "gets", "stranger"),
    ("as", "a", "result"),
    ("in", "other", "words"),
    ("wait", "for", "it"),
    ("but", "wait"),
    ("plot", "twist"),
    ("fun", "fact"),
    ("spoiler", "alert"),
    ("get", "this"),        # extra guard in the rule: must be clause-final
]
# Pre-sorted longest-first so the matcher prefers the longest hook at
# any position ("here's the thing" beats a hypothetical ("here", "'s")).
DISCOURSE_PIVOT_PHRASES.sort(key=len, reverse=True)

# Exception markers (RULE 57).  "besides" is deliberately absent — its
# discourse use ("Besides, ...") dominates in scripts.
EXCEPTION_SINGLE_MARKERS = {"except", "excluding"}
# Two-token exception markers, matched on (token, next_token) lowercase.
EXCEPTION_BIGRAMS = {
    ("apart", "from"), ("aside", "from"),
    ("other", "than"), ("save", "for"),
}


# =============================================================================
# 2.9  SOUND EFFECT WORDS  (RULE 60)
# =============================================================================
# Sound-effect words that earn their own line when used BARE (no determiner,
# no subject, no object — "and then boom the roof came down").  Noun uses
# ("the crash"), verb uses ("cars crash", "snap a photo") and compounds
# ("crash site", "pop culture") are filtered structurally in the rule.
SFX_WORDS = {
    "boom", "kaboom", "bang", "crash", "smash", "bam", "wham", "pow",
    "whoosh", "woosh", "swoosh", "thud", "thump", "snap", "crack",
    "pop", "buzz", "roar", "splash", "slam", "screech", "clang",
    "crunch", "zap", "thwack", "clunk",
    # v18.4 expansion
    "click", "rattle", "rumble", "hiss", "sizzle", "fizz", "thunk",
    "plop", "splat", "squelch", "ding", "honk", "vroom", "whirr",
    "clatter", "creak", "crackle", "whack", "boing", "ping",
}


# =============================================================================
# 2.10  TOPIC & ERA STYLE LEXICONS
# =============================================================================

# Generic nouns that must never win the "script topic" vote — they appear in
# every script regardless of subject.  A LINGUISTIC category list (like the
# weak-verb sets), not a world-knowledge answer key.
GENERIC_TOPIC_NOUNS = {
    "thing", "way", "time", "year", "day", "part", "place", "people",
    "world", "lot", "kind", "sort", "one", "story", "fact", "reason",
    # v18.4 expansion
    "bit", "case", "side", "end", "point", "idea", "example", "problem",
    "question", "answer", "moment", "name", "word", "line", "video",
    "subject", "matter", "area", "number", "amount", "group", "level",
    "order", "form", "type", "use", "need", "man", "woman", "guy",
}

# Noun lemmas whose stock footage should be styled "historical" once the
# script has mentioned an old date: people-roles, vessels, conflict,
# institutions.  Consumed by SEARCH_TERM_SYNTHESIS.
ERA_STYLE_NOUNS = {
    # people & roles
    "merchant", "sailor", "trader", "soldier", "pirate", "explorer",
    "settler", "colonist", "peasant", "farmer", "monk", "priest",
    "blacksmith", "knight", "samurai", "warrior", "gladiator", "viking",
    "crusader", "conqueror", "slave", "servant", "messenger", "scribe",
    # rulers & nobility
    "king", "queen", "emperor", "empress", "prince", "princess",
    "pharaoh", "sultan", "tsar", "czar", "monarch", "duke", "duchess",
    "lord", "lady", "nobleman", "chief", "chieftain", "shogun",
    # military & command
    "general", "admiral", "captain", "commander", "army", "navy",
    "legion", "regiment", "cavalry", "infantry", "archer", "musketeer",
    # vessels & travel
    "ship", "boat", "vessel", "fleet", "galleon", "caravel", "frigate",
    "voyage", "expedition", "caravan", "chariot", "carriage",
    # conflict & power
    "battle", "war", "siege", "conquest", "invasion", "rebellion",
    "revolution", "uprising", "raid", "duel", "crusade", "plague",
    # institutions & places-of-power
    "empire", "kingdom", "dynasty", "colony", "monopoly", "treaty",
    "throne", "crown", "court", "castle", "fortress", "palace",
    "temple", "cathedral", "monastery", "harbor", "harbour", "port",
    # objects of the era
    "sword", "shield", "spear", "cannon", "musket", "armor", "armour",
    "scroll", "parchment", "coin", "chest", "spice", "silk",
}


# =============================================================================
# 2.11  NAMES & CAPITALISATION
# =============================================================================

# Words that may sit INSIDE a name run without breaking it (both neighbours
# must be Capitalised): 'Alaric the Goth', 'John Paul II of Spain'.
NAME_CONNECTORS = {"of", "the", "de", "da", "van", "der", "von", "al",
                   "el", "la", "le", "bin", "ibn"}

# Capitalised words that must never START a name run (sentence furniture,
# months, pronouns...). A run beginning with one drops it and continues.
CAP_STOPWORDS = {
    "The", "A", "An", "But", "And", "Or", "So", "If", "When", "While",
    "Here", "There", "This", "That", "These", "Those", "It", "Its", "He",
    "She", "We", "They", "You", "I", "His", "Her", "Our", "Their", "My",
    "Not", "No", "Yes", "Now", "Then", "Also", "In", "On", "At", "By",
    "For", "From", "To", "With", "As", "Because", "Which", "What", "Who",
    "Why", "How", "Every", "Each", "All", "Both", "Some", "Any", "Once",
    "After", "Before", "During", "January", "February", "March", "April",
    "May", "June", "July", "August", "September", "October", "November",
    "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday"}


# =============================================================================
# 2.12  GEOGRAPHY
# =============================================================================

# Where the map's own country list comes from — a hit here is a country the
# map can actually DRAW, which is the only kind that matters.
MAP_COUNTRIES_GEOJSON = (Path(__file__).resolve().parent.parent.parent
                         / "___visuals" / "_MAP_DATA" / "world_countries.geojson")

# Used only when that geojson is missing.
FALLBACK_COUNTRIES = {"indonesia", "france", "italy", "china", "india",
                      "japan", "portugal", "spain", "england", "netherlands"}

# A big city must be at least this populous before we trust it as a place.
BIG_CITY_MIN_POPULATION = 200_000


# =============================================================================
# 2.13  REGEXES
#       Every compiled pattern in the folder. Each one is wrapped by a named
#       function in SECTION 4 — callers use the function, not the regex.
# =============================================================================

# --- sentence structure ------------------------------------------------------
# THE MASTER sentence-end pattern. See SECTION 3 for what it does and why.
SENTENCE_ENDER_RX = re.compile(
    r'(?:'
        r'(?<!\d)(?<!\b[A-Z])'         # Prevents mid-sentence decimals (5.2) and initials (J. K.)
        r'(?:[.!?…]|\.{3})'            # Matches ., !, ?, …, or literal '...'
        r'[\'"\)\ ]*'                  # Optional trailing quotes/parens
        r'(?=\s+|\n|$)'                # Followed by space, newline, or EOF (no uppercase required)
    r'|'
        r'[\w\'"\)\]]'                 # Line ending char
        r'\n(?=\s*[-*+]\s|\s*\d+\.\s)'  # Unpunctuated line followed by bullet (-,*,+) or number (1.)
    r')'
)

# A blank line between two lines of prose = a paragraph break.
PARAGRAPH_BREAK_RX = re.compile(r"([^\s.!?;:…])[ \t]*\n[ \t]*\n\s*")

# Any run of whitespace.
WHITESPACE_RUN_RX = re.compile(r"\s+")

# A string of pure punctuation — no letters, no digits.
PUNCT_ONLY_RX = re.compile(r"^[^\w\s]+$")

# An ordinal written in digits: 1st, 3rd, 17th.
ORDINAL_DIGITS_RX = re.compile(r"^\d+(st|nd|rd|th)$")

# ... or ...., or the single-character ellipsis …
ELLIPSIS_RX = re.compile(r"\.{2,}|…+")

# --- lists -------------------------------------------------------------------
# The surface shape of a list: 'A, B(,) and/or C', each item ≤3 words.
NOUN_LIST_RX = re.compile(
    r"\b((?:[\w'-]+\s+){0,2}[\w'-]+)\s*,\s*"
    r"((?:[\w'-]+\s+){0,2}[\w'-]+)\s*"
    r"(?:,\s*)?(?:and|or)\s+((?:[\w'-]+\s+){0,2}[\w'-]+)", re.I)

# The no-spaCy FALLBACK clause detector (see CLAUSE_WORDS_FALLBACK).
CLAUSE_WORDS_RX = re.compile(rf"\b({CLAUSE_WORDS_FALLBACK})\b", re.I)

# --- geography ---------------------------------------------------------------
# Capitalised words followed by a geographic suffix: 'the Banda Islands'.
GEO_SUFFIX_RX = re.compile(
    r"\b((?:[A-Z][\w'-]+\s+){1,3}"
    r"(?:Islands?|Archipelago|Sea|Ocean|Mountains?|Valley|Desert|River|Bay|"
    r"Coast|Peninsula|Strait|Gulf|Canyon|Falls|Plateau|Plains?|Delta|"
    r"Volcano|Reef|Highlands?|Lakes?))\b")

# --- quotes ------------------------------------------------------------------
# A quoted phrase of two or more words.
QUOTE_RX = re.compile(r'["“”\'‘’]([^"“”\'‘’]+\s+[^"“”\'‘’]+)["“”\'‘’]')

# --- numbers / money / statistics -------------------------------------------
NUMBER_RX = re.compile(
    r"([£$€]\s?\d[\d,.]*(?:\s*(?:million|billion|thousand|k|m|bn))?"
    r"|\b\d+(?:\.\d+)?\s*(?:percent|%)"
    r"|\b\d[\d,.]*\s+(?:million|billion|thousand)\b"
    r"|\b\d{1,3}(?:,\d{3})+\b)", re.I)

# --- years / dates -----------------------------------------------------------
YEAR_RX = re.compile(
    r"\b(?:(?:in|by|of|from|until|since|around|circa)\s+(?:the\s+)?"
    r"(?P<year>1[0-9]{3}s?|20[0-2][0-9]s?)"
    r"|(?P<decade>1[0-9]{2}0s|20[0-2]0s)"
    r"|(?P<century>\d{1,2}(?:st|nd|rd|th)[- ]century)"
    r"|(?P<full>(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{0,4}))\b", re.I)   # re.I: a sentence
# opens with "In 1946", not "in 1946" — without it every sentence-initial date
# was missed.

# dateparser is eager, so anything it returns must still look like a date.
HAS_DIGIT_OR_MONTH_RX = re.compile(
    r"\d|January|February|March|April|May|June|July|August|September|"
    r"October|November|December", re.I)

# --- names -------------------------------------------------------------------
# A roman numeral inside a name: 'John Paul II'.
ROMAN_NUMERAL_RX = re.compile(r"^(?:I{1,3}|IV|V|VI{0,3}|IX|X{1,3})$")

# Word / punctuation tokenizer used by the name-run scanner.
WORD_OR_PUNCT_RX = re.compile(r"[\w'-]+|[^\w\s]")

# --- statistics: which CHART does this line want? ---------------------------
# One quantity out of a whole — '73% of the ocean', 'ninety percent'.
PERCENTAGE_RX = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", re.I)

# Any plain figure, for counting how many a sentence holds.
ANY_FIGURE_RX = re.compile(
    r"[£$€¥]\s?\d[\d,.]*|\b\d[\d,.]*\s*(?:%|percent|million|billion|"
    r"thousand|trillion)?\b", re.I)

# A figure worth ticking up on a counter: an optional currency, the digits,
# and an optional magnitude word. (value / prefix / suffix for the data form.)
COUNTER_FIGURE_RX = re.compile(
    r"(?P<prefix>[£$€¥])?\s?(?P<value>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<suffix>million|billion|thousand|trillion|percent|%)?", re.I)

# Words that say a quantity CHANGED over time → a line graph, not a bar chart.
TREND_OVER_TIME_RX = re.compile(
    r"\b(grew|grown|fell|fallen|rose|risen|dropped|climbed|doubled|tripled|"
    r"quadrupled|halved|soared|plunged|declined|increased|decreased|"
    r"shrank|shrunk|expanded|year after year|decade after decade)\b"
    r"|\bover the (?:years|decades|centuries|next \w+)\b"
    r"|\bby \d{3,4}\b|\bbetween \d{3,4} and \d{3,4}\b", re.I)

# Words that say a quantity is a SHARE of something → a pie chart.
SHARE_OF_A_WHOLE_RX = re.compile(
    r"\b(?:a |the )?(?:share|slice|portion|fraction|proportion|split|"
    r"breakdown|rest|remainder)\b|\b(?:made up|make up|accounted for|"
    r"accounts for|consisted of|consists of)\b", re.I)

# --- pronouns that lean on something named earlier ---------------------------
# 'He', 'his', 'they' — the picture should show whoever that is. 'it' is
# deliberately absent: far too vague to resolve safely.
PERSON_PRONOUN_RX = re.compile(
    r"\b(he|him|his|she|her|hers|they|them|their|theirs)\b", re.I)

# --- relative position -------------------------------------------------------
# 'beneath the old wooden floor', 'under it' — leans on the previous image.
RELATIVE_POSITION_RX = re.compile(
    r"^(in|on|at|under|beneath|inside|behind|above|atop|within|around|"
    r"beside|near|upon|over|underneath|across|through)\s+"
    r"(the|a|an|it|its|them|this|that|these|those|his|her|their)\b", re.I)


# =============================================================================
# 2.14  TUNABLE THRESHOLDS
#       Every magic number, in one place. Nothing else in the folder should
#       contain a bare integer that means "how long / how many".
# =============================================================================

# --- splitter: general lead-in / clause lengths ------------------------------
MIN_LEAD_FOR_CLAUSE_SPLIT = 3       # tokens before wh/SCONJ to enable split
MIN_LEAD_FOR_BUT_OR       = 3       # tokens before "but"/"or" coord
MIN_LEAD_FOR_AND_CLAUSE   = 5       # tokens before clause-and ("X and he Y")
MIN_LEAD_FOR_ENTITY       = 2       # tokens before entity to count as a "reveal"
LONG_PREP_SUBTREE_MIN     = 5       # ADP subtree size before we split after it
                                    # (was 7 — missed "in a very physical way" type splits)
RUNON_SENT_MIN_TOKENS     = 30      # sentence length needed to enable runon-suppress
RUNON_WINDOW              = 18      # tokens either side checked for punctuation
LONG_LEAD_TO_ROOT         = 12      # force split BEFORE ROOT after this long a lead
SHORT_TAIL_TO_PUNCT       = 3       # don't split verb if remainder to next punct ≤ this
SHORT_SUBORD_CLAUSE       = 2       # don't split before SCONJ if its clause is ≤ this many tokens
                                    # (was 3 — over-suppressed "anyway, here is a sentence /
                                    #  that has punctuatoin" because "that has punctuatoin" has
                                    #  exactly 3 tokens to next punct.)
SHORT_SENT_NO_SPLIT       = 4       # sentences with ≤ this many *non-punct* tokens are
                                    # never split internally.  Per-rule guards inside
                                    # rule_verb_clause / rule_long_preps / rule_clause_starters
                                    # do most of the work now (≤ 8-9 token threshold there);
                                    # this just catches the genuinely tiny ones.
LONG_SUBORD_OPENER_TOKENS = 6       # tokens needed inside an SCONJ/wh-led opener
                                    # before we split after its closing comma (RULE 26)

# --- splitter: rules 31-38 ---------------------------------------------------
LONG_COMMA_LEAD_CONTENT   = 5       # min content tokens in lead before generic-long-comma split (RULE 31)
LONG_COMMA_TAIL_CONTENT   = 3       # min content tokens in tail
INFINITIVE_SPLIT_SENT_MIN = 12      # RULE 32: min sent ntok to allow `to + VERB` split
INFINITIVE_SPLIT_LEAD_MIN = 2       # Was 6. FROZEN_BIGRAMS protects "want to", etc. = 6
INFINITIVE_SPLIT_TAIL_MIN = 4
OF_REVEAL_SENT_MIN        = 12      # RULE 33: terminal-of reveal
OF_REVEAL_LEAD_MIN        = 5
PROGRESSIVE_SENT_MIN      = 10      # RULE 34: split before VBG in `be + Ving`
PROGRESSIVE_LEAD_MIN      = 3
COPULA_REVEAL_SENT_MIN    = 7       # RULE 35: copula-attribute reveal
COPULA_REVEAL_CHUNK_MIN   = 2       # RULE 35: min noun-chunk length to count as reveal
PP_PRON_PART_SENT_MIN     = 9       # RULE 36: PP-with-PRON-participle reveal
PP_PRON_PART_LEAD_MIN     = 2
AUX_PP_REVEAL_LEAD_MIN    = 3       # RULE 37: terminal `'s about X` reveal
CHAINED_PART_SENT_MIN     = 11      # RULE 38: chained-participle reveal
DOBJ_DISQUAL_SENT_MIN     = 8       # min sent length to allow dobj-disqualifier in RULE 12

# --- splitter: rule 54 (terminal descriptor) ---------------------------------
MIN_LEAD_FOR_DESCRIPTOR   = 5

# --- splitter: verb families -------------------------------------------------
RESULT_INTENSIFIER_LOOKBACK    = 6  # how far back to look for so/such/more
SPATIAL_PREP_SUBTREE_MIN_NOUNS = 2  # nouns required in subtree
SPATIAL_PREP_SENT_MIN_TOKENS   = 8  # sentence length floor
SPATIAL_PREP_LEAD_MIN          = 3  # content tokens before the prep

# --- splitter: v18 rules 56-60 ----------------------------------------------
COMPARISON_SENT_MIN      = 7        # min non-punct tokens in sentence
COMPARISON_LEAD_MIN      = 2        # min tokens since last split before the marker
EXCEPTION_SENT_MIN       = 5
EXCEPTION_LEAD_MIN       = 2
DISCOURSE_PIVOT_MIN_TAIL = 2        # non-punct tokens needed after the hook
                                    # before we also split AFTER it
AGENT_REVEAL_SENT_MIN    = 6
AGENT_REVEAL_LEAD_MIN    = 3

# --- splitter: per-line metadata --------------------------------------------
LIST_ITEM_MAX_TOKENS = 5   # a real list item is short ("scurvy," "pirates,")
LIST_MIN_TAGGED      = 2   # >=2 tagged boundaries -> >=3 on-screen cells


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 3 — SENTENCE END DETECTION   ( T H E   M A S T E R )      ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================
#
# Finds big-punctuation enders (. ! ? …) ANYWHERE, including mid-fragment
# ('shipwrecks. But if you made'), plus an unpunctuated line followed by a
# bullet or numbered item. It deliberately does NOT treat ':' as an ender —
# 'on Earth: The Banda Islands.' is one sentence.
#
# This detection is PERFECTED. If anything else in the codebase disagrees
# about where a sentence ends, THIS is the master and the other one is wrong.
# =============================================================================


def sentence_ender_positions(text: str) -> list[int]:
    """Every character offset in `text` just AFTER a sentence ender."""
    return [m.end() for m in SENTENCE_ENDER_RX.finditer(text)]


def ends_a_sentence(text: str) -> bool:
    """Does `text` finish on a sentence ender?"""
    return bool(SENTENCE_ENDER_RX.search(text.rstrip() + " "))


def split_into_sentences(text: str) -> list[str]:
    """`text` cut into whole sentences at the master ender positions."""
    out, start = [], 0
    for end in sentence_ender_positions(text):
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        start = end
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 4 — NAMED CHECKS ON PLAIN TEXT                            ### #
# ###               (no spaCy needed; a `doc` may be passed to sharpen    ### #
# ###                the answer, and is always optional)                  ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================


# -----------------------------------------------------------------------------
# CLAUSE vs THING
# -----------------------------------------------------------------------------

def has_finite_verb(text: str, doc=None) -> bool:
    """Does `text` contain a finite verb — i.e. is it a CLAUSE rather than a
    THING? spaCy POS tags first (VERB/AUX among these words); the
    CLAUSE_WORDS_RX fallback when no model is loaded."""
    if doc is not None:
        words = set(text.split())
        if any(t.text in words and t.pos_ in ("VERB", "AUX") for t in doc):
            return True
    return bool(CLAUSE_WORDS_RX.search(text))


# -----------------------------------------------------------------------------
# LISTS OF THINGS
# -----------------------------------------------------------------------------

def find_noun_list_matches(text: str):
    """Every 'A, B and/or C' shape in `text`, as raw regex matches (they
    still have to survive validate_noun_list_match)."""
    return list(NOUN_LIST_RX.finditer(text))


def validate_noun_list_match(m, doc=None) -> str | None:
    """The strict list validator: 'A, B(,) and/or C' where every item is a
    short THING — verified by NLP when available (each item holds a noun and
    no verb), else by the clause-word fallback. The final item is trimmed at
    the first clause word, since the sentence usually carries straight on
    ('...and cinnamon RULED the world'). Returns the matched list, rebuilt
    from its trimmed items, or None."""
    items = [m.group(i).strip() for i in (1, 2, 3)]
    # trim the final item where the sentence's verb takes over
    tail_words = []
    for w in items[2].split():
        if CLAUSE_WORDS_RX.search(w):
            break
        if doc is not None and any(
                t.text == w and t.pos_ in ("VERB", "AUX") for t in doc):
            break
        tail_words.append(w)
    if not tail_words:
        return None
    items[2] = " ".join(tail_words)
    if doc is not None:
        for item in items:
            span = [t for t in doc if t.text in item.split()]
            if any(t.pos_ in ("VERB", "AUX") for t in span):
                return None
            if not any(t.pos_ in ("NOUN", "PROPN") for t in span):
                return None
    else:
        if any(CLAUSE_WORDS_RX.search(item) for item in items):
            return None
    return f"{items[0]}, {items[1]} and {items[2]}"


# -----------------------------------------------------------------------------
# PLACES
# -----------------------------------------------------------------------------

_COUNTRIES = None


def country_names() -> set[str]:
    """Lowercase names of every country the MAP can render — read from the
    SAME geojson the map draws, so a hit here is a renderable hit."""
    global _COUNTRIES
    if _COUNTRIES is not None:
        return _COUNTRIES
    names = set()
    if MAP_COUNTRIES_GEOJSON.exists():
        try:
            data = json.loads(MAP_COUNTRIES_GEOJSON.read_text(encoding="utf-8"))
            for f in data.get("features", []):
                n = (f.get("properties") or {}).get("name")
                if n:
                    names.add(n.lower())
        except Exception:
            pass
    _COUNTRIES = names or set(FALLBACK_COUNTRIES)
    return _COUNTRIES


def contains_country(text: str) -> str | None:
    """The first renderable country named in `text` (word-boundary match
    against the map's own geojson), Title-cased, or None."""
    low = text.lower()
    for c in country_names():
        if re.search(rf"\b{re.escape(c)}\b", low):
            return c.title()
    return None


def contains_big_city(text: str, cache: dict = None) -> str | None:
    """(booster) A big city named in `text`, resolved to its country via
    geonamescache — returned as 'City → Country' when that country is one
    the map can render. `cache` (any dict) avoids rebuilding the city table
    on every call."""
    if geonamescache is None:
        return None
    cache = cache if cache is not None else {}
    gc = cache.setdefault("gc", geonamescache.GeonamesCache())
    cities = cache.setdefault("gc_cities", [
        (city["name"], city["countrycode"])
        for city in gc.get_cities().values()
        if city.get("population", 0) >= BIG_CITY_MIN_POPULATION])
    countries = gc.get_countries()
    for name, code in cities:
        if re.search(rf"\b{re.escape(name)}\b", text):
            cname = countries.get(code, {}).get("name", "")
            if cname.lower() in country_names():
                return f"{name} → {cname}"
    return None


def extract_geo_suffix_place(text: str) -> str | None:
    """A 'Banda Islands'-style place: Capitalised words + a geographic
    suffix (Islands / Sea / Valley / ...), or None."""
    m = GEO_SUFFIX_RX.search(text)
    return m.group(1) if m else None


def extract_named_place(text: str, doc=None) -> str | None:
    """The most SPECIFIC place named in `text`: a geo-suffix run first, else
    a spaCy GPE/LOC entity that is not itself a country. None when in doubt
    (or when no model is loaded)."""
    specific = extract_geo_suffix_place(text)
    if specific:
        return specific
    if doc is not None:
        for ent in doc.ents:
            if ent.label_ in PLACE_ENT_LABELS \
                    and ent.text.lower() not in country_names():
                return ent.text
    return None


# -----------------------------------------------------------------------------
# QUOTES
# -----------------------------------------------------------------------------

def extract_quote(text: str) -> str | None:
    """A quoted phrase of ≥2 words in `text`, without its quote marks."""
    m = QUOTE_RX.search(text)
    return m.group(1) if m else None


# -----------------------------------------------------------------------------
# NUMBERS, MONEY, DATES
# -----------------------------------------------------------------------------

def extract_number_or_stat(text: str) -> str | None:
    """A striking figure — '£4,000', '90 percent', '17 million' — by regex
    first, then the price-parser / number-parser boosters. None when no
    reliable signal is present ('two dollars' has no digits → None)."""
    m = NUMBER_RX.search(text)
    if m:
        return m.group(1)
    if price_parser is not None:           # £4 000, USD 12.5m, 4,000 GBP...
        p = price_parser.Price.fromstring(text)
        if p.amount is not None and p.currency:
            return f"{p.currency}{p.amount_text}"
    if number_parser is not None:          # 'seventeen million'
        words = text.split()
        for size in (3, 2):
            for j in range(len(words) - size + 1):
                window = " ".join(words[j:j + size]).strip(",.!?…")
                try:
                    val = number_parser.parse_number(window)
                except Exception:
                    val = None
                if val is not None and val >= 1000:
                    return f"{window} (= {val:,})"
    return None


def extract_year_or_date(text: str) -> str | None:
    """JUST the date part of a year / decade / century / full date in `text`
    ('1600s', not 'in the 1600s') — regex first, then the dateparser booster
    (whose eager hits must still contain a digit or a month name)."""
    m = YEAR_RX.search(text)
    if m:
        hit = next((g for g in (m.group("year"), m.group("decade"),
                                m.group("century"), m.group("full"))
                    if g), None)
        if hit:
            return hit
    if _dateparser_search is not None:
        try:
            for found, _ in (_dateparser_search(text, languages=["en"])
                             or []):
                if HAS_DIGIT_OR_MONTH_RX.search(found):
                    return found
        except Exception:
            pass
    return None


# -----------------------------------------------------------------------------
# NAMES
# -----------------------------------------------------------------------------

def extract_name_runs(text: str, doc=None) -> list[str]:
    """Runs of Capitalised words (connectors and roman numerals allowed
    inside) that look like NAMES. The rules that keep this honest:
      • a run may not START on a stopword ('The Banda...' → drops 'The'),
      • geo-suffix runs ('Banda Islands') and country names belong to the
        PLACE checks, not here,
      • a SINGLE-word run only counts mid-sentence, or when coordinated with
        an accepted name ('Ronaldo and Bradd Pitt' vouches for Ronaldo even
        at the sentence start),
      • single words must be NER-confirmed as something OTHER than a
        nationality (NORP) — 'European' is not a name,
      • a run that STARTS the sentence — of ANY length — must also be
        NER-confirmed: sentence-initial capitalisation proves nothing ('New'
        capitalises the same way in 'New taxes were introduced' and 'New
        York City never sleeps'). No model → reject rather than guess."""
    tokens = list(WORD_OR_PUNCT_RX.finditer(text))
    words = [t.group(0) for t in tokens]

    def is_cap(w):
        return w[:1].isupper() and (w[1:2].islower() or len(w) == 1
                                    or ROMAN_NUMERAL_RX.match(w)
                                    or w.isupper())

    runs, i = [], 0
    while i < len(words):
        w = words[i]
        if is_cap(w) and w not in CAP_STOPWORDS:
            start = i
            j = i + 1
            while j < len(words):
                nxt = words[j]
                if is_cap(nxt) and nxt not in CAP_STOPWORDS:
                    j += 1
                elif (nxt.lower() in NAME_CONNECTORS
                      and j + 1 < len(words) and is_cap(words[j + 1])
                      and words[j + 1] not in CAP_STOPWORDS):
                    j += 2
                else:
                    break
            run_words = words[start:j]
            sentence_initial = (
                not text[:tokens[start].start()].strip()
                or text[:tokens[start].start()].rstrip()[-1] in ".!?…;:")
            runs.append({"text": " ".join(run_words), "n": len(run_words),
                         "start_i": start, "end_i": j,
                         "sentence_initial": sentence_initial})
            i = j
        else:
            i += 1

    ner_any = ner_single = None
    if doc is not None:
        ner_any = {ent.text for ent in doc.ents
                   if ent.label_ in NAME_ENT_LABELS}
        ner_single = {ent.text for ent in doc.ents
                      if ent.label_ in NAME_ENT_LABELS_FOR_SINGLE_WORD}

    accepted = []
    for r in runs:
        t = r["text"]
        if t.lower() in country_names():
            continue                       # the PLACE checks' job
        if GEO_SUFFIX_RX.fullmatch(t) or GEO_SUFFIX_RX.search(t):
            continue                       # 'Banda Islands' → PLACE checks
        vouchers = ner_single if r["n"] == 1 else ner_any
        ner_confirmed = vouchers is not None and any(
            t in e or e in t for e in vouchers)
        if r["sentence_initial"]:
            if ner_confirmed:
                accepted.append(r)
        elif r["n"] >= 2:
            accepted.append(r)
        elif ner_confirmed:
            accepted.append(r)
    # coordination rescue: a sentence-initial SINGLE name joined by
    # 'and/&/,' to an already-accepted name is vouched for
    acc_starts = {r["start_i"] for r in accepted}
    for r in runs:
        if r in accepted or r["n"] != 1 or not r["sentence_initial"]:
            continue
        k = r["end_i"]
        if k < len(words) and words[k].lower() in ("and", "&", ",") \
                and (k + 1) in acc_starts:
            accepted.insert(0, r)
    return [r["text"] for r in accepted]


# -----------------------------------------------------------------------------
# SHAPE OF THE LINE
# -----------------------------------------------------------------------------

def opens_with_relative_position(text: str) -> str | None:
    """Does `text` OPEN with preposition + article/pronoun ('beneath the old
    wooden floor', 'under it')? Returns the opening few words, else None."""
    if RELATIVE_POSITION_RX.match(text.strip()):
        return " ".join(text.strip().split()[:4])
    return None


def ends_as_question(text: str) -> bool:
    """Does `text` end on a '?' — a question aimed at the viewer?"""
    return text.rstrip().endswith("?")


def is_only_punctuation(text: str) -> bool:
    """Is `text` nothing but punctuation (no letters, no digits)?"""
    stripped = text.strip()
    return bool(stripped) and bool(PUNCT_ONLY_RX.match(stripped))


def is_ellipsis_text(text: str) -> bool:
    """Detect ..., ...., or … (single-character ellipsis)."""
    return bool(ELLIPSIS_RX.fullmatch(text))


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 5 — NAMED CHECKS ON PARSED TOKENS                         ### #
# ###               (spaCy Doc/Token in, plain answer out. These are the  ### #
# ###                structural disambiguators: a word list only ever     ### #
# ###                proposes a candidate, these decide.)                 ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================


# -----------------------------------------------------------------------------
# WHAT KIND OF USE IS THIS VERB?
# -----------------------------------------------------------------------------

def verb_is_used_as_equation(verb_tok) -> bool:
    """Is an equation-family verb in its EQUATION sense?

    For non-phrasal verbs (mean, represent, signify, equal, denote,
    symbolize, imply, indicate, suggest), any transitive use with a
    dobj or ccomp qualifies.

    For phrasal verbs (stand for, refer to, amount to, boil down to,
    come down to, translate to/into), the specific particle/prep must
    appear as a child (or in the immediate forward context).

    Examples:
        "X means Y"               → True
        "X stands for Y"          → True (particle "for" present)
        "X stands tall"           → False (no "for" particle)
        "X refers to Y"           → True ("to" prep present)
        "X refers back to Y"      → True ("to" prep present)
        "X refers a case"         → False (no "to" prep)
    """
    lemma = verb_tok.lemma_.lower()
    if lemma not in EQUATION_PHRASAL_PARTICLES:
        return True  # non-phrasal equation verb, always counts
    required_parts = EQUATION_PHRASAL_PARTICLES[lemma]
    # Look in immediate children for prep/particle matching
    for child in verb_tok.children:
        if child.lower_ in required_parts and child.pos_ in {"ADP", "PART"}:
            return True
    # Also scan up to 3 tokens ahead for the particle (spaCy may attach
    # it elsewhere in the tree, e.g. as a separate ADP token)
    doc = verb_tok.doc
    for j in range(verb_tok.i + 1, min(verb_tok.i + 4, len(doc))):
        if doc[j].lower_ in required_parts:
            return True
        if doc[j].text in HARD_PUNCT:
            break
    return False


def verb_is_used_as_copula(verb_tok) -> bool:
    """Is this verb LINKING a subject to a predicate (copular), rather than
    acting transitively or prepositionally?

    Also accepts a ccomp child whose ROOT is a participle (VBN/VBG) or
    adjective: spaCy's small model sometimes parses "remained convinced
    that..." with "convinced" as ccomp rather than xcomp.
    """
    for child in verb_tok.children:
        if child.dep_ in {"acomp", "oprd", "attr"}:
            return True
        if child.dep_ == "xcomp" and child.pos_ in {"ADJ", "VERB", "AUX"}:
            return True
        if child.dep_ == "ccomp" and (child.tag_ in {"VBN", "VBG"}
                                       or child.pos_ == "ADJ"):
            return True
    return False


def verb_has_substantial_object(verb_tok) -> bool:
    """Does this verb have a direct-object REVEAL PAYLOAD worth its own line?

    Used by the possession (Family 2) and creation (Family 3) rules.
    A "substantial" dobj has at least one of:

        • ≥1 ADJ modifier in subtree      ("great cycle paths")
        • ≥2 NOUN/PROPN tokens in subtree ("calibration tools larger than cities")
        • a relative-clause child         ("evidence that he was lying")
        • a comparative marker            ("more sand than ever before")
        • ≥3 total tokens in subtree      ("a long winding road")

    False when the verb has no dobj, or the dobj is a bare pronoun / single
    short noun ("has it", "owns one").
    """
    dobj = next((c for c in verb_tok.children if c.dep_ in {"dobj", "obj"}), None)
    if dobj is None:
        return False
    subtree = list(dobj.subtree)
    if len(subtree) >= 3:
        return True
    n_adj = sum(1 for x in subtree if x.pos_ == "ADJ")
    n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
    has_relcl = any(c.dep_ in {"acl", "acl:relcl", "relcl"}
                    for c in dobj.children)
    has_comp = any(x.lower_ in COMPARATIVE_MARKERS for x in subtree)
    return n_adj >= 1 or n_nouns >= 2 or has_relcl or has_comp


def verb_has_substantial_complement(verb_tok) -> tuple[bool, object]:
    """Like verb_has_substantial_object, but ALSO accepts clausal
    complements — used by the perception family (Family 4), where the
    payload may be a clause rather than a noun phrase.

    Returns (True, complement_head) or (False, None). Substantial means:
        • a dobj with a substantial subtree (same test as above), OR
        • a ccomp clause with ≥3 tokens in its subtree, OR
        • an xcomp with a VERB head ("saw him leave").

    Examples:
        "noticed the strange shape"   → dobj subtree of 3 → True
        "noticed that he was late"    → ccomp ≥3 tokens   → True
        "knew what to do"             → ccomp WH-clause   → True
        "saw him leave"               → xcomp VERB        → True
        "saw it"                      → PRON, length 1    → False
        "knows."                      → no complement     → False
    """
    # Check dobj first
    dobj = next((c for c in verb_tok.children if c.dep_ in {"dobj", "obj"}), None)
    if dobj is not None:
        subtree = list(dobj.subtree)
        if len(subtree) >= 3:
            return (True, dobj)
        n_adj = sum(1 for x in subtree if x.pos_ == "ADJ")
        n_nouns = sum(1 for x in subtree if x.pos_ in {"NOUN", "PROPN"})
        has_relcl = any(c.dep_ in {"acl", "acl:relcl", "relcl"}
                        for c in dobj.children)
        has_comp = any(x.lower_ in COMPARATIVE_MARKERS for x in subtree)
        if n_adj >= 1 or n_nouns >= 2 or has_relcl or has_comp:
            return (True, dobj)

    # Check ccomp
    ccomp = next((c for c in verb_tok.children if c.dep_ == "ccomp"), None)
    if ccomp is not None:
        if len(list(ccomp.subtree)) >= 3:
            return (True, ccomp)

    # Check xcomp with VERB head ("saw him leave", "watched it fall")
    xcomp = next((c for c in verb_tok.children
                  if c.dep_ == "xcomp" and c.pos_ == "VERB"), None)
    if xcomp is not None:
        return (True, xcomp)

    return (False, None)


# -----------------------------------------------------------------------------
# WHAT IS THIS ONE TOKEN?
# -----------------------------------------------------------------------------

def is_ordinal_token(tok) -> bool:
    """Detect ordinals (first, 3rd, ...) without a hard-coded word list."""
    if tok.ent_type_ == "ORDINAL":
        return True
    return bool(ORDINAL_DIGITS_RX.match(tok.lower_))


# -----------------------------------------------------------------------------
# WOULD CUTTING HERE BREAK SOMETHING?
# -----------------------------------------------------------------------------

def is_inside_compound_named_entity(doc, i: int) -> bool:
    """True if cutting at *i* would split a multi-token named entity
    (e.g. between "John" and "Ford", or "New" and "York City")."""
    if i <= 0 or i >= len(doc):
        return False
    left, right = doc[i - 1], doc[i]
    return (left.ent_iob_ in {"B", "I"}
            and right.ent_iob_ == "I"
            and left.ent_type_ == right.ent_type_)


def is_inside_hyphen_compound(doc, i: int) -> bool:
    """True if *i* would cut a hyphenated compound: 'self-driving', 'follow-up'."""
    if i <= 0 or i >= len(doc):
        return False
    if doc[i - 1].text == "-" and not doc[i - 1].whitespace_:
        return True
    if doc[i].text == "-" and not doc[i - 1].whitespace_:
        return True
    return False


def is_inside_frozen_bigram(doc, i: int) -> bool:
    """True if cutting at *i* would split a frozen bigram like 'what if'."""
    if i <= 0 or i >= len(doc):
        return False
    return (doc[i - 1].lower_, doc[i].lower_) in FROZEN_BIGRAMS


def is_big_punctuation_split_point(doc, i: int) -> bool:
    """True if the split at *i* is "punctuation-driven" — either the token
    immediately to its LEFT or to its RIGHT is "big" (non-comma) punctuation
    that should always force a line-break:

        • hard-punct      .  !  ?  ;  :
        • ellipsis        ...  ….
        • dash            —   –   --   -
        • quote           "  '  “  ”  ‘  ’  «  »  ‹  ›  „  ‚  `
        • bracket         (  )  [  ]  {  }

    These splits are NEVER wiped by any anti-rule.  Big punctuation marks
    a deliberate visual break by the writer — even short sentences should
    honour it.  (Commas are deliberately NOT included: they're soft
    break-points and the existing comma rules decide when to split.)
    """
    if i <= 0 or i > len(doc):
        return False
    big = HARD_PUNCT | DASH_PUNCT | ANY_QUOTE | OPEN_BRACKETS | CLOSE_BRACKETS
    if i > 0:
        lt = doc[i - 1]
        if lt.text in big or is_ellipsis_text(lt.text):
            return True
    if i < len(doc):
        rt = doc[i]
        if rt.text in big or is_ellipsis_text(rt.text):
            return True
    return False


def is_inside_runon_sentence(doc, i: int) -> bool:
    """True if token *i* lives in a long sentence (≥ RUNON_SENT_MIN_TOKENS)
    AND no hard punctuation appears within RUNON_WINDOW tokens either side.
    Used to suppress fine-grained verb splits inside list-heavy passages.
    CRUCIAL: returns False for short sentences so ordinary S-V-O sentences
    ("the cat sat on the mat") still split after the verb."""
    sent = doc[i].sent
    if len(sent) < RUNON_SENT_MIN_TOKENS:
        return False
    lo = max(0, i - RUNON_WINDOW)
    hi = min(len(doc), i + RUNON_WINDOW)
    return not any(t.text in HARD_PUNCT for t in doc[lo:hi])


# -----------------------------------------------------------------------------
# MEASURING A SPAN
# -----------------------------------------------------------------------------

def noun_chunk_containing(doc, i: int):
    """Return the noun_chunk containing token index *i*, or None."""
    for nc in doc.noun_chunks:
        if nc.start <= i < nc.end:
            return nc
    return None


def tokens_to_next_punctuation(doc, i: int) -> int:
    """How many tokens from *i* (inclusive) up to the next HARD_PUNCT or
    comma (or the end of the doc)."""
    j = i
    while j < len(doc) and doc[j].text not in HARD_PUNCT and doc[j].text != ",":
        j += 1
    return j - i


def count_content_tokens(doc, lo: int, hi: int) -> int:
    """Count NOUN/PROPN/VERB/ADJ/ADV/NUM tokens in doc[lo:hi] — how much
    "real content" sits on one side of a candidate split."""
    return sum(1 for x in doc[lo:hi]
               if x.pos_ in {"NOUN", "PROPN", "VERB", "ADJ", "ADV", "NUM"})


def has_visualisable_content(doc, lo: int, hi: int) -> bool:
    """Is there anything in doc[lo:hi] you could actually PUT ON SCREEN?

    True if the span holds at least one tangible/concrete content token, OR
    the span is long enough (≥4 non-punct tokens) to read as a legitimate
    line even without one — that handles relative/wh-clauses like "what had
    to be done", which are reveal-worthy despite weak POS density.
    """
    ntok = sum(1 for t in doc[lo:hi] if not t.is_punct and not t.is_space)
    if ntok >= 4:
        return True

    for t in doc[lo:hi]:
        if t.pos_ in {"NOUN", "PROPN", "NUM"}:
            return True
        if t.pos_ == "ADJ" and t.lemma_.lower() not in WEAK_ADJ_LEMMAS:
            return True
        if t.pos_ == "VERB":
            lemma_weak = t.lemma_.lower() in WEAK_VERB_LEMMAS
            text_weak = t.text.lower() in WEAK_VERB_FORMS
            if not (lemma_weak or text_weak):
                return True
    return False


def find_idiom_spans(doc) -> list[tuple[int, int]]:
    """All (lo, hi) token spans in the doc matching an IDIOM_PHRASES entry."""
    lowers = [t.lower_ for t in doc]
    spans: list[tuple[int, int]] = []
    for phrase in IDIOM_PHRASES:
        L = len(phrase)
        for i in range(len(lowers) - L + 1):
            if tuple(lowers[i:i + L]) == phrase:
                spans.append((i, i + L))
    return spans


# =============================================================================
# =============================================================================
# ###                                                                     ### #
# ###   SECTION 6 — THE TAGGER'S DETECTORS                                ### #
# ###                                                                     ### #
# ###   One named function per QUESTION the auto-tagger asks about one    ### #
# ###   line of a script. Each returns True/False and records WHAT it     ### #
# ###   matched, so the tagger can print it and reuse it as a search      ### #
# ###   term. Auto_add_mediatypes.py does nothing but call these in       ### #
# ###   order and map each True onto an enum.                             ### #
# ###                                                                     ### #
# ###   HOUSE RULE: **False when in doubt.** A wrong empty is free        ### #
# ###   (manual tagging catches it); a wrong tag costs a bad scene.       ### #
# ###                                                                     ### #
# ###   Each detector may consult THREE things:                           ### #
# ###     a) the line itself and its row (search_term, ...),              ### #
# ###     b) the SPLITTER'S OWN rule_ids (SECTION 1.3 groups) — the       ### #
# ###        highest-trust signal there is,                               ### #
# ###     c) the WHOLE SENTENCE, rebuilt from neighbouring lines using    ### #
# ###        the SECTION 3 master ender detection.                        ### #
# ###                                                                     ### #
# =============================================================================
# =============================================================================


@dataclass
class LineContext:
    """Everything a detector may look at for ONE line of the script, plus
    note() so a detector can record the exact thing it matched."""
    fragment: str            # the line itself (the json key)
    row: dict                # its row: media_type, search_term, rule_ids...
    index: int               # which line this is
    fragments: list          # every line in the script, in order
    found: dict = field(default_factory=dict)   # detector name → what matched
    fills: dict = field(default_factory=dict)   # detector name → search term
    charts: dict = field(default_factory=dict)  # detector name → chart `data`
    shared: dict = field(default_factory=dict)  # ONE dict for the whole run
    _cache: dict = field(default_factory=dict)

    # ---- the three data points -------------------------------------------
    def rule_ids(self) -> set[int]:
        """The splitter's rule ids for this line — WHY it was cut here."""
        return {int(r) for r in (self.row.get("rule_ids") or [])}

    def why_split_here(self) -> list[str]:
        """Those rule ids in plain English."""
        return describe_rule_ids(sorted(self.rule_ids()))

    def full_sentence(self) -> str:
        """The whole sentence this line belongs to, rebuilt by joining
        neighbouring lines between master sentence enders."""
        if "s" not in self._cache:
            text, _, _ = self._doc_layout()
            s, e = self.sentence_span()
            self._cache["s"] = text[s:e].strip()
        return self._cache["s"]

    def doc(self, text: str):
        """`text` parsed by spaCy, or None when no model is installed."""
        nlp = get_nlp()
        if nlp is None:
            return None
        key = ("d", text)
        if key not in self._cache:
            self._cache[key] = nlp(text)
        return self._cache[key]

    # ---- machinery behind full_sentence ----------------------------------
    def _doc_layout(self):
        """The whole script joined once, with each line's char range and
        every master ender position — computed once, shared by all lines."""
        if "layout" not in self.shared:
            text, offsets, pos = "", [], 0
            for f in self.fragments:
                offsets.append((pos, pos + len(f)))
                text += f + "\n"   # "\n" preserves line breaks for bullets
                pos += len(f) + 1
            self.shared["layout"] = (text, offsets,
                                     sentence_ender_positions(text))
        return self.shared["layout"]

    def sentence_span(self) -> tuple[int, int]:
        """(start, end) chars of this line's sentence in the joined text:
        from the ender BEFORE the line to the first ender at or after the
        line's END (a line straddling two sentences gets both — that's what
        it genuinely covers)."""
        text, offsets, enders = self._doc_layout()
        fs, fe = offsets[self.index]
        start = 0
        for e in enders:
            if e <= fs:
                start = e
            else:
                break
        end = len(text)
        for e in enders:
            if e >= fe:
                end = e
                break
        return start, end

    # ---- recording what was found ----------------------------------------
    def note(self, detector: str, what: str, fill: str = None) -> bool:
        """Record what was matched (shown in the printout), optionally with
        a CLEAN value for auto-filling search_term, and return True."""
        self.found[detector] = what
        if fill:
            self.fills[detector] = fill
        return True

    def note_chart(self, detector: str, what: str, data: dict) -> bool:
        """Same as note(), but for a CHART: `data` is the ready-made dict the
        chart's own form expects (CONFIG.MEDIA_TYPE_DATA_FIELDS), so the
        flowchart never has to build one — it just says fill_chart_data=True.
        --> note_chart("contains_percentage", "73%", {"percent": "73"})"""
        self.found[detector] = what
        self.charts[detector] = data
        return True


def _validated_list_spans(line: LineContext):
    """All VALIDATED noun-list matches in this line's sentence, as char spans
    in the joined text — a list split over several lines lights up every line
    it touches, and only those. Lists never cross a sentence ender."""
    ss, se = line.sentence_span()
    key = ("lists", ss, se)
    if key in line.shared:
        return line.shared[key]
    text, _, _ = line._doc_layout()
    sentence = text[ss:se]
    doc = line.doc(sentence)
    spans = []
    pos = 0
    while True:
        m = NOUN_LIST_RX.search(sentence, pos)
        if not m:
            break
        rebuilt = validate_noun_list_match(m, doc)
        if rebuilt:
            # the first item can over-grab words belonging to the sentence
            # BEFORE the list — the span a line must TOUCH starts at the
            # first item's LAST word (doubt → False).
            ms = m.end(1) - len(m.group(1).split()[-1])
            spans.append((ss + ms, ss + m.end(), rebuilt))
            pos = m.end()
        else:
            pos = m.start() + 1    # an invalid alignment must not shadow a
                                   # valid list starting just after it
    line.shared[key] = spans
    return line.shared[key]


# -----------------------------------------------------------------------------
# THE LIVE DETECTORS
# -----------------------------------------------------------------------------

def _this_line_s_own_item(fragment: str, whole_list: str = "") -> str:
    """The ONE item of the list that this line actually holds. A list run
    becomes a group, and every cell of a group needs its OWN search term —
    so a line gets its own item, not the whole list.
    --> "pirates and" out of "scurvy, pirates and shipwrecks" → 'pirates'"""
    cleaned = fragment.strip()
    if whole_list:
        items = [i.strip() for part in whole_list.split(",")
                 for i in part.split(" and ") if i.strip()]
        for item in items:
            if item.lower() in fragment.lower():
                return item
    # no rebuilt list to match against (the splitter's rule ids fired on
    # their own) — strip the joining words off the line and use what is left
    for joiner in ("and ", "or "):
        if cleaned.lower().startswith(joiner):
            cleaned = cleaned[len(joiner):]
    if cleaned.lower().endswith(" and"):
        cleaned = cleaned[:-4]
    elif cleaned.lower().endswith(" or"):
        cleaned = cleaned[:-3]
    return cleaned.strip().strip(",.;:!?…").strip()


def contains_noun_list(line: LineContext) -> bool:
    """A list of things — 'nutmeg, cloves and cinnamon'. TRUSTS the splitter
    first (a LIST rule id means it already proved this is a list run);
    otherwise the strict matcher finds 'A, B and/or C' in the SENTENCE and
    lights up exactly the lines the list TOUCHES — so 'scurvy,' + 'pirates
    and' + 'shipwrecks...' all catch it, while 'a merchant, getting it
    meant' in the same sentence stays False.

    The fill is THIS LINE'S OWN ITEM, because a list run becomes a group and
    every cell needs its own search term."""
    if line.rule_ids() & LIST_RULES:
        mine = _this_line_s_own_item(line.fragment)
        return line.note("contains_noun_list", f"splitter rule "
                         f"{sorted(line.rule_ids() & LIST_RULES)}"
                         f"{f' → {mine}' if mine else ''}", fill=mine)
    _, offsets, _ = line._doc_layout()
    s, e = offsets[line.index]
    for ms, me, rebuilt in _validated_list_spans(line):
        if s < me and e > ms:              # this line touches the list
            n = rebuilt.count(",") + 2     # item count (for the printout)
            mine = _this_line_s_own_item(line.fragment, rebuilt)
            return line.note("contains_noun_list",
                             f"{rebuilt}  ({n} items) → {mine}",
                             fill=mine or rebuilt)
    return False


def contains_place_name(line: LineContext) -> bool:
    """A place the MAP can render. The country comes from the same geojson
    the map draws, optionally via a big city resolved to its country. A more
    SPECIFIC place in the line ('the Banda Islands') is captured for display,
    but the printed 'map renders:' country is what will actually appear."""
    hay = f"{line.fragment} {line.row.get('search_term', '')}"
    country = contains_country(hay) or contains_big_city(hay, cache=line.shared)
    if country is None:
        return False
    specific = extract_named_place(line.fragment, line.doc(line.fragment))
    plain_country = country.split("→")[-1].strip()
    if specific and specific.lower() != plain_country.lower():
        return line.note("contains_place_name",
                         f"{specific} (map renders: {plain_country})",
                         fill=f"{specific}, {plain_country}")
    return line.note("contains_place_name", country, fill=plain_country)


def contains_quote(line: LineContext) -> bool:
    """Direct speech / a quoted phrase — '"worth its weight in gold"'.
    Splitter rule 5 wins first; else quote marks wrapping ≥2 words in the
    line or its sentence."""
    if line.rule_ids() & QUOTE_RULES:
        return line.note("contains_quote", "splitter rule [5]")
    q = extract_quote(line.fragment) or extract_quote(line.full_sentence())
    return line.note("contains_quote", f'"{q}"', fill=q) if q else False


def contains_big_number(line: LineContext) -> bool:
    """A striking figure — '£4,000', '90 percent', '17 million'. Splitter
    rules 19 (money) / 24 / 55 (amounts) win first; else the shared
    number extractor with its boosters."""
    hit = extract_number_or_stat(line.fragment)
    if line.rule_ids() & (MONEY_RULES | NUMBER_RULES):
        rid = sorted(line.rule_ids() & (MONEY_RULES | NUMBER_RULES))
        return line.note("contains_big_number", hit or f"splitter rule {rid}")
    if hit:
        return line.note("contains_big_number", hit, fill=hit)
    return False


def contains_date(line: LineContext) -> bool:
    """A specific year, decade, century or date — 'in 1667', 'the 1600s',
    '17th century'. Splitter rule 53 wins first; else the shared date
    extractor (which captures JUST the date part)."""
    hit = extract_year_or_date(line.fragment)
    if line.rule_ids() & DATE_RULES:
        return line.note("contains_date", hit or "splitter rule [53]",
                         fill=hit)
    if hit:
        return line.note("contains_date", hit, fill=hit)
    return False


def contains_famous_name(line: LineContext) -> bool:
    """A person / named thing worth a wikipedia image — 'Alaric the Goth',
    'Jenson Button'. The shared capitalisation-run engine finds every name;
    splitter rules 18/50 (name reveals) count as confirmation too. Prints
    ALL names found; the auto-fill uses the FIRST (one lookup per row)."""
    names = extract_name_runs(line.fragment, line.doc(line.fragment))
    if line.rule_ids() & NAME_REVEAL_RULES and not names:
        rid = sorted(line.rule_ids() & NAME_REVEAL_RULES)
        return line.note("contains_famous_name", f"splitter rule {rid}")
    if names:
        return line.note("contains_famous_name", ", ".join(names),
                         fill=names[0])
    return False


def opens_relative_to_something(line: LineContext) -> bool:
    """The line describes something RELATIVE to another thing — 'in the
    rock', 'beneath the old wooden floor'. Splitter rules 14/30/39 win
    first; else the line OPENS with preposition + article/pronoun. These
    lean on the previous image."""
    if line.rule_ids() & RELATIVE_RULES:
        rid = sorted(line.rule_ids() & RELATIVE_RULES)
        return line.note("opens_relative_to_something",
                         f"splitter rule {rid}")
    opening = opens_with_relative_position(line.fragment)
    if opening:
        return line.note("opens_relative_to_something", f"'{opening}…'")
    return False


def asks_the_viewer_a_question(line: LineContext) -> bool:
    """A question aimed at the viewer — 'So what happened?'."""
    if ends_as_question(line.fragment):
        q = line.fragment.strip()
        return line.note("asks_the_viewer_a_question", q[-40:], fill=q)
    return False


# -----------------------------------------------------------------------------
# WHERE THIS LINE SITS IN ITS SENTENCE
# A line that STARTS a sentence is a new thought and usually wants a new
# picture; a line in the MIDDLE of one is a quick beat that usually wants to
# lean on the picture already there.
# -----------------------------------------------------------------------------

def starts_a_new_sentence(line: LineContext) -> bool:
    """This line opens its sentence — nothing but whitespace sits between the
    sentence start and the line start (see SECTION 3 for where sentences
    begin and end)."""
    text, offsets, _ = line._doc_layout()
    start_of_line = offsets[line.index][0]
    start_of_sentence = line.sentence_span()[0]
    if text[start_of_sentence:start_of_line].strip():
        return False
    return line.note("starts_a_new_sentence",
                     f'opens: "{line.fragment.strip()[:40]}…"')


# -----------------------------------------------------------------------------
# PRONOUNS THAT LEAN ON SOMETHING NAMED EARLIER
# -----------------------------------------------------------------------------

def find_last_name_before(line: LineContext) -> "str | None":
    """The most recent name introduced ABOVE this line — what a later 'He'
    is most likely talking about. Scans backwards, so the nearest name wins.
    --> "Nero fiddled." / "He watched it burn." → 'Nero'"""
    key = ("last_name", line.index)
    if key in line.shared:
        return line.shared[key]
    found = None
    for j in range(line.index - 1, -1, -1):
        earlier = line.fragments[j]
        doc = line.doc(earlier)
        names = extract_name_runs(earlier, doc)
        if not names:
            continue
        # a PERSON wins over a place — "he" is far more likely to be Nero
        # than Rome in "Nero watched Rome burn"
        people = ({e.text for e in doc.ents if e.label_ == "PERSON"}
                  if doc is not None else set())
        found = next((n for n in names
                      if any(n in p or p in n for p in people)), names[0])
        break
    line.shared[key] = found
    return found


def refers_back_to_something_named(line: LineContext) -> bool:
    """This line says 'he / she / they / his / their' and we know who that
    is, because a name was introduced further up. The fill is that NAME, so
    whatever we put on screen can show them again. ('it' is deliberately not
    a trigger — far too vague to resolve safely.)
    --> "Nero was emperor." / "He set Rome alight." → 'Nero'"""
    if not PERSON_PRONOUN_RX.search(line.fragment):
        return False
    name = find_last_name_before(line)
    if not name:
        return False               # a pronoun with nothing to point at
    pronoun = PERSON_PRONOUN_RX.search(line.fragment).group(1)
    return line.note("refers_back_to_something_named",
                     f"'{pronoun}' → {name}", fill=name)


# -----------------------------------------------------------------------------
# IS THERE ANYTHING HERE WE COULD ACTUALLY FILM?
# -----------------------------------------------------------------------------

def has_something_we_could_film(line: LineContext) -> bool:
    """The line names a concrete thing we could go and fetch footage of, and
    the fill is that thing — ready to be a search term. Skips the words that
    paint no picture (WEAK_ADJ_LEMMAS / WEAK_VERB_LEMMAS / GENERIC_TOPIC_NOUNS).
    Needs the spaCy model: with no model it stays False rather than guessing.
    --> "a jar of nutmeg sat on the shelf" → 'jar of nutmeg'"""
    doc = line.doc(line.fragment)
    if doc is None:
        return False
    best = None
    for chunk in doc.noun_chunks:
        head = chunk.root
        if head.pos_ not in ("NOUN", "PROPN"):
            continue
        if head.lemma_.lower() in GENERIC_TOPIC_NOUNS:
            continue
        if head.pos_ == "PRON" or head.lemma_.lower() in WEAK_VERB_LEMMAS:
            continue
        words = [t.text for t in chunk
                 if t.pos_ != "DET" and t.lemma_.lower() not in WEAK_ADJ_LEMMAS]
        if not words:
            continue
        term = " ".join(words)
        if best is None or len(term) > len(best):
            best = term
    if not best:
        return False
    return line.note("has_something_we_could_film", best, fill=best)


# -----------------------------------------------------------------------------
# STATISTICS — WHICH CHART DOES THIS LINE WANT?
# Each of these builds the chart's `data` dict as it goes. If it cannot build
# a valid one, it says False and the line falls through to something else —
# a broken chart is far worse than no chart.
# -----------------------------------------------------------------------------

def _figures_in(text: str) -> "list[str]":
    """Every figure in `text` — used to tell one stat from a comparison."""
    return [m.group(0).strip() for m in ANY_FIGURE_RX.finditer(text)
            if any(c.isdigit() for c in m.group(0))]


def contains_percentage(line: LineContext) -> bool:
    """ONE quantity out of a whole → the progress bar fills to it.
    --> "73% of the ocean is unexplored" → progress_bar, percent=73"""
    m = PERCENTAGE_RX.search(line.fragment)
    if not m:
        return False
    percent = float(m.group(1))
    if not 0 <= percent <= 100:
        return False               # not a percentage of anything
    label = " ".join(PERCENTAGE_RX.sub("", line.fragment).split()).strip(" ,.—-…")
    data = {"percent": m.group(1)}
    if label:
        data["label"] = label[:60]
    return line.note_chart("contains_percentage", f"{m.group(1)}%", data)


def contains_shares_of_a_whole(line: LineContext) -> bool:
    """The parts that make up ONE whole → a pie chart, one slice each. Needs
    at least two shares to be a pie at all.
    --> "40% gold, 35% silver and 25% spice" → pie_chart"""
    percents = PERCENTAGE_RX.findall(line.full_sentence())
    if len(percents) < 2:
        return False
    if not SHARE_OF_A_WHOLE_RX.search(line.full_sentence()) \
            and len(percents) < 3:
        return False               # two numbers alone are a comparison, not a pie
    slices = ", ".join(f"part {i + 1}: {p}" for i, p in enumerate(percents))
    return line.note_chart("contains_shares_of_a_whole",
                           f"{len(percents)} shares", {"slices": slices})


def contains_trend_over_time(line: LineContext) -> bool:
    """A quantity that CHANGED over time → a line graph draws the trend.
    --> "trade grew from 200 tons in 1600 to 4,000 by 1700" → line_graph"""
    sentence = line.full_sentence()
    if not TREND_OVER_TIME_RX.search(sentence):
        return False
    figures = _figures_in(sentence)
    if len(figures) < 2:
        return False               # a trend needs at least two points
    points = ", ".join(f"point {i + 1}: {f}" for i, f in enumerate(figures[:6]))
    return line.note_chart("contains_trend_over_time",
                           f"{len(figures)} points over time", {"points": points})


def contains_several_comparable_numbers(line: LineContext) -> bool:
    """Several quantities side by side → bars to compare them.
    --> "Rome had 900 ships, Athens 300" → bar_chart"""
    figures = _figures_in(line.full_sentence())
    if not 2 <= len(figures) <= 6:
        return False
    bars = ", ".join(f"bar {i + 1}: {f}" for i, f in enumerate(figures))
    return line.note_chart("contains_several_comparable_numbers",
                           f"{len(figures)} figures", {"bars": bars})


def contains_single_statistic(line: LineContext) -> bool:
    """ONE big figure you narrate → the counter ticks up to it. Not a
    percentage (that is the progress bar) and not a year (that is the
    timeline). Small counts like 'two dollars' are not worth an animation.
    --> "the fine was £4,000 per sailor" → counter, value=4000, prefix=£"""
    if PERCENTAGE_RX.search(line.fragment):
        return False
    if extract_year_or_date(line.fragment):
        return False
    m = COUNTER_FIGURE_RX.search(line.fragment)
    if not m or not m.group("value"):
        return False
    digits = m.group("value").replace(",", "")
    try:
        value = float(digits)
    except ValueError:
        return False
    magnitude = (m.group("suffix") or "").lower()
    if value < 1000 and not m.group("prefix") and not magnitude:
        return False               # too small to be worth ticking up to
    data = {"value": digits}
    if m.group("prefix"):
        data["prefix"] = m.group("prefix")
    if magnitude:
        data["suffix"] = f" {magnitude}"
    shown = f"{m.group('prefix') or ''}{m.group('value')}{' ' + magnitude if magnitude else ''}"
    return line.note_chart("contains_single_statistic", shown, data)


def opens_with_a_year(line: LineContext) -> bool:
    """A year the sentence OPENS on → the timeline marker travels back to it.
    Splitter rule 53 ('a number or date right at the start of a sentence')
    is the strongest signal there is, so it wins first.
    --> "In 1946, everything changed." → timeline, year=1946"""
    year_text = extract_year_or_date(line.fragment)
    if not year_text:
        return False
    digits = re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", year_text)
    if not digits:
        return False               # '17th century' — no year to travel to
    if not (line.rule_ids() & DATE_RULES) and not starts_a_new_sentence(line):
        return False               # mid-sentence date → not a timeline
    return line.note_chart("opens_with_a_year", digits.group(1),
                           {"year": digits.group(1)})


# -----------------------------------------------------------------------------
# THE PLANNED DETECTORS
# Each one is wired into the tagger already and always answers False, so it
# is safe to leave alone. Fill in the body and it comes alive — nothing else
# needs changing. (A "line with nothing to picture" detector is deliberately
# absent: the splitter's merge rules 1008/1009/1010 already fold non-visual
# scraps into their neighbours at split time.)
# -----------------------------------------------------------------------------

def is_single_object_focus(line: LineContext) -> bool:
    """ONE concrete object is the star — 'one dollar coin', 'a gold bar'.
    TO DO: search_term of 1-3 nouns with a singular determiner (NLP:
    exactly one noun chunk, no plural)."""
    return False


def adds_something_to_the_scene(line: LineContext) -> bool:
    """Something ADDED into the previous shot — 'add a jar of nutmeg into
    the cupboard'. TO DO: add|put|place|appears + into/onto/in across the
    full sentence."""
    return False


def transforms_the_previous_image(line: LineContext) -> bool:
    """The previous image CHANGES — 'the map turns red', 'it rots away'.
    TO DO: turns|becomes|transforms|changes + colour/state word (NLP:
    subject is a pronoun referring back)."""
    return False


def continues_the_previous_line(line: LineContext) -> bool:
    """The sentence carries on over the same visual — 'which meant...'.
    TO DO: mid-sentence line opening with which/and/so/that/it AND no
    concrete noun (NLP-verified), plus the NONVISUAL_RULES ids."""
    return False


def is_abstract_concept(line: LineContext) -> bool:
    """Unfilmable abstractions — 'monopoly', 'inflation', 'betrayal'.
    TO DO: search_term noun with no physical hypernym (has_visualisable_
    content in SECTION 5 is the start of this)."""
    return False


def suits_the_board_composite(line: LineContext) -> bool:
    """A single strong image suiting the board composite. TO DO:
    sentence-opening line whose search_term is one concrete noun phrase."""
    return False


def is_stickman_story_beat(line: LineContext) -> bool:
    """A narrative action beat for the stickman/AI style — 'a merchant
    sails east'. TO DO: subject + action verb, no filmable noun,
    CONSECUTIVE with other such beats (then: group modifier + group_id)."""
    return False


def is_caption_punchline(line: LineContext) -> bool:
    """A punchline where the AUTOMATIC tilted caption lands — 'worth its
    WEIGHT in GOLD.'. TO DO: sentence-final line with '!', an ALL-CAPS word
    or a superlative."""
    return False


def is_comparison_pair(line: LineContext) -> bool:
    """An explicit A-versus-B — 'silver versus nutmeg'. TO DO: vs|versus|
    compared to between two noun phrases (needs the multi-row splitter)."""
    return False
