"""
Auto_add_mediatypes.py — FIRST-PASS automatic media-type tagging.

    uv run ___splitting_and_labelling/Auto_add_mediatypes.py script_to_search_term.json
    uv run ___splitting_and_labelling/Auto_add_mediatypes.py script_to_search_term.json --dry-run
    uv run ___splitting_and_labelling/Auto_add_mediatypes.py --selftest

PHILOSOPHY: **False when in doubt.** A wrong empty is free (manual tagging
catches it); a wrong tag costs a bad scene. Every check must earn its True
from at least one RELIABLE signal, and most entries staying all-False is
expected and fine.

Every check can consult THREE data points per entry:
  a) the fragment itself (the json key) and its row (search_term, ...),
  b) the splitter's OWN LABELS — rule_ids on the row, i.e. WHY the
     splitter cut this line (rule 15 = noun list, rule 5 = quotation,
     rule 19 = money, ...). The splitter already did the hard analysis;
     these are the highest-trust signal we have,
  c) the ORIGINAL SENTENCE, rebuilt by joining neighbouring fragments
     between the big enders (. ! ? …) — see Ctx.full_sentence(),
plus an optional NLP layer (spaCy) that RE-VERIFIES candidates — e.g. that
a "list item" really is a noun and not a clause. No spaCy model installed →
the NLP-dependent parts stay conservative (False), never guessy.

The file is split in two halves by the big comment:
  TOP    — DETECTION: every check on every entry, True/False printed,
           with the exact matched thing shown (→ "Portugal", → "£4,000").
  BOTTOM — ASSIGNMENT: the ordered FLOWCHART maps results to a media type
           + modifiers. ONLY entries whose media_type is EMPTY are touched.
"""
from __future__ import annotations

# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from MEDIA_TYPES import MEDIA_TYPES, MODIFIERS

_SENTENCE_END = re.compile('[.!?…]+["\')\\]]*\\s*$')


# ---------------------------------------------------------------------------
# The splitter's rule ids (see RULE_DESCRIPTIONS in the splitter) grouped by
# what they tell US. A rule id on a row = the splitter CUT this line for
# that reason — analysis it already did, so we trust it first.
# ---------------------------------------------------------------------------
LIST_RULES = {15, 16, 17, 25, 51, 61}   # runs of listed things
QUOTE_RULES = {5}                        # a phrase in quotation marks
MONEY_RULES = {19}                       # an amount of money
NUMBER_RULES = {24, 55}                  # sentence-final / approx amounts
DATE_RULES = {53}                        # a number or date opening a sentence
NAME_REVEAL_RULES = {18, 50}             # a name worth a dramatic reveal
RELATIVE_RULES = {14, 30, 39}            # long in/on/at... place phrases
NONVISUAL_RULES = {1008, 1009, 1010}     # merged-back non-visual scraps
SOUND_RULES = {60}                       # boom/crash SFX sync points


# ---------------------------------------------------------------------------
# Optional NLP (spaCy). Missing package or model → None, and every check
# that needs it stays False rather than guessing.
# ---------------------------------------------------------------------------
_NLP = "unloaded"


def _nlp():
    global _NLP
    if _NLP == "unloaded":
        try:
            import spacy
            _NLP = spacy.load("en_core_web_sm")
        except Exception:
            _NLP = None
            print("[auto-tag] note: spaCy model not available — NLP "
                  "re-verification is OFF (checks stay conservative). "
                  "Enable it with:  uv add spacy  &&  "
                  "uv run python -m spacy download en_core_web_sm")
    return _NLP


# ═══════════════════════════════════════════════════════════════════════════
# TOP HALF — DETECTION
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Ctx:
    """Everything a check may look at for one entry, plus `note()` so a
    check can record the EXACT thing it matched for the printout."""
    fragment: str
    row: dict
    index: int
    fragments: list
    found: dict = field(default_factory=dict)
    _cache: dict = field(default_factory=dict)

    def rule_ids(self) -> "set[int]":
        return {int(r) for r in (self.row.get("rule_ids") or [])}

    def full_sentence(self) -> str:
        if "s" not in self._cache:
            lo = self.index
            while lo > 0 and not _SENTENCE_END.search(self.fragments[lo - 1]):
                lo -= 1
            hi = self.index
            while hi < len(self.fragments) - 1 and not _SENTENCE_END.search(
                    self.fragments[hi]):
                hi += 1
            self._cache["s"] = " ".join(self.fragments[lo:hi + 1])
        return self._cache["s"]

    def doc(self, text: str):
        nlp = _nlp()
        if nlp is None:
            return None
        key = ("d", text)
        if key not in self._cache:
            self._cache[key] = nlp(text)
        return self._cache[key]

    def note(self, check: str, what: str) -> bool:
        """Record what was matched (shown in the table) and return True."""
        self.found[check] = what
        return True


# a tiny finite-verb stoplist for the no-NLP path: a "list item" containing
# one of these is a CLAUSE, not a thing ("getting it meant", "she ran")
_CLAUSE_WORDS = re.compile(
    r"\b(is|are|was|were|be|been|being|has|have|had|do|does|did|meant|means|"
    r"getting|got|went|goes|came|comes|said|says|made|makes|ran|runs|"
    r"sailing|surviving|took|takes|gave|gives|becomes?|became|ruled|rules)\b", re.I)

_LIST_RX = re.compile(
    r"((?:[\w'-]+\s+){0,2}[\w'-]+)\s*,\s*"
    r"((?:[\w'-]+\s+){0,2}[\w'-]+)\s*"
    r"(?:,\s*)?(?:and|or)\s+((?:[\w'-]+\s+){0,2}[\w'-]+)", re.I)


def _find_noun_list(ctx: Ctx, text: str) -> "str | None":
    """The strict list matcher: 'A, B(,) and/or C' where every item is a
    short THING — verified by NLP when available (each item holds a noun
    and no verb), else by the clause-word stoplist. The final item is
    trimmed at the first clause word, since the sentence usually carries
    straight on ('...and cinnamon RULED the world'). Returns the matched
    list, rebuilt from its trimmed items, or None."""
    m = _LIST_RX.search(text)
    if not m:
        return None
    items = [m.group(i).strip() for i in (1, 2, 3)]
    # trim the final item where the sentence's verb takes over
    tail_words = []
    doc = ctx.doc(text)
    for w in items[2].split():
        if _CLAUSE_WORDS.search(w):
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
        if any(_CLAUSE_WORDS.search(item) for item in items):
            return None
    return f"{items[0]}, {items[1]} and {items[2]}"


def is_noun_list(ctx: Ctx) -> bool:
    """A list of things — 'nutmeg, cloves and cinnamon'. TRUSTS the
    splitter first: a LIST rule id (15/16/17/25/51/61) on this row means
    the splitter already identified this line as part of a list run.
    Otherwise the strict matcher must find 'A, B and/or C' IN THIS
    FRAGMENT, with NLP verifying every item is a noun and not a clause.
    (Deliberately NOT sentence-wide: that marked every fragment of a
    list-bearing sentence, drowning the table in misleading Trues — the
    splitter's rule ids already mark the actual list rows. A run of
    consecutive rows carrying LIST ids is the real multi-row list —
    turning those into a GROUP is TODO #5.)"""
    if ctx.rule_ids() & LIST_RULES:
        return ctx.note("is_noun_list", f"splitter rule "
                        f"{sorted(ctx.rule_ids() & LIST_RULES)}")
    hit = _find_noun_list(ctx, ctx.fragment)
    return ctx.note("is_noun_list", hit) if hit else False


def _country_names() -> "set[str]":
    global _COUNTRIES
    try:
        return _COUNTRIES
    except NameError:
        pass
    geo = Path(__file__).resolve().parent.parent / "___visuals" / \
        "_MAP_DATA" / "world_countries.geojson"
    names = set()
    if geo.exists():
        try:
            data = json.loads(geo.read_text(encoding="utf-8"))
            for f in data.get("features", []):
                n = (f.get("properties") or {}).get("name")
                if n:
                    names.add(n.lower())
        except Exception:
            pass
    if not names:
        names = {"indonesia", "france", "italy", "china", "india", "japan",
                 "portugal", "spain", "england", "netherlands"}
    _COUNTRIES = names
    return names


def is_location(ctx: Ctx) -> bool:
    """A named COUNTRY the map data can actually render, found in the
    fragment or search_term (word-boundary match against the SAME geojson
    the map type draws from). Prints the exact country matched."""
    hay = f"{ctx.fragment} {ctx.row.get('search_term', '')}".lower()
    for c in _country_names():
        if re.search(rf"\b{re.escape(c)}\b", hay):
            return ctx.note("is_location", c.title())
    return False


_QUOTE_RX = re.compile(r'["“”\'‘’]([^"“”\'‘’]+\s+[^"“”\'‘’]+)["“”\'‘’]')


def is_quote_or_speech(ctx: Ctx) -> bool:
    """Direct speech / a quoted phrase — '"worth its weight in gold"'.
    Splitter rule 5 (a phrase in quotation marks) on this row wins first;
    else quotation marks wrapping ≥2 words in the fragment or sentence.
    Feeds: typography. Prints the exact quote."""
    if ctx.rule_ids() & QUOTE_RULES:
        return ctx.note("is_quote_or_speech", "splitter rule [5]")
    m = _QUOTE_RX.search(ctx.fragment) or _QUOTE_RX.search(ctx.full_sentence())
    return ctx.note("is_quote_or_speech", f'"{m.group(1)}"') if m else False


_NUMBER_RX = re.compile(
    r"([£$€]\s?\d[\d,.]*(?:\s*(?:million|billion|thousand|k|m|bn))?"
    r"|\b\d+(?:\.\d+)?\s*(?:percent|%)"
    r"|\b\d[\d,.]*\s+(?:million|billion|thousand)\b"
    r"|\b\d{1,3}(?:,\d{3})+\b)", re.I)


def is_big_number_or_statistic(ctx: Ctx) -> bool:
    """A striking figure — '£4,000', '90 percent', '17 million'. Splitter
    rules 19 (money) / 24 / 55 (amounts) win first; else the currency /
    percent / magnitude regex. Feeds: typography (or a caption on the
    previous image). Prints the exact figure."""
    m = _NUMBER_RX.search(ctx.fragment)
    if ctx.rule_ids() & (MONEY_RULES | NUMBER_RULES):
        rid = sorted(ctx.rule_ids() & (MONEY_RULES | NUMBER_RULES))
        return ctx.note("is_big_number_or_statistic",
                        m.group(1) if m else f"splitter rule {rid}")
    return ctx.note("is_big_number_or_statistic", m.group(1)) if m else False


_YEAR_RX = re.compile(
    r"\b((?:in|by|of|from|until|since|around|circa)\s+(?:the\s+)?"
    r"(?:1[0-9]{3}s?|20[0-2][0-9]s?)"
    r"|(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{0,4})\b")


def is_year_or_date(ctx: Ctx) -> bool:
    """A specific year or date — 'in 1667', 'by the 1800s'. Splitter rule
    53 (a date opening a sentence) wins first; else a year with a
    preposition, or a written-out date. Feeds: typography (or map when a
    location co-occurs). Prints the exact date."""
    m = _YEAR_RX.search(ctx.fragment)
    if ctx.rule_ids() & DATE_RULES:
        return ctx.note("is_year_or_date",
                        m.group(1) if m else "splitter rule [53]")
    return ctx.note("is_year_or_date", m.group(1)) if m else False


_TITLECASE_RX = re.compile(
    r"\b([A-Z][\w'-]+(?:\s+(?:of|the|van|de|da|al)\s+|\s+)"
    r"[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)*)\b")


def is_famous_person_or_thing(ctx: Ctx) -> bool:
    """A person/organisation/named thing — 'Isaac Newton', 'the VOC'.
    Splitter rules 18/50 (a name worth a dramatic reveal) win first; else
    an NLP named-entity (PERSON/ORG/EVENT/WORK_OF_ART) — countries go to
    is_location instead. WITHOUT the NLP model this stays False rather
    than trusting TitleCase alone (False when in doubt). Feeds: wikipedia.
    Prints the exact name. TODO #4 adds a cached wikipedia page-existence
    verification on top."""
    m = _TITLECASE_RX.search(ctx.fragment)
    if ctx.rule_ids() & NAME_REVEAL_RULES:
        rid = sorted(ctx.rule_ids() & NAME_REVEAL_RULES)
        return ctx.note("is_famous_person_or_thing",
                        m.group(1) if m else f"splitter rule {rid}")
    doc = ctx.doc(ctx.fragment)
    if doc is None:
        return False
    for ent in doc.ents:
        if ent.label_ in ("PERSON", "ORG", "EVENT", "WORK_OF_ART", "FAC") \
                and len(ent.text.split()) >= 2:
            return ctx.note("is_famous_person_or_thing", ent.text)
    return False


_RELATIVE_RX = re.compile(
    r"^(in|on|at|under|beneath|inside|behind|above|atop|within|around|"
    r"beside|near|upon|over|underneath|across|through)\s+"
    r"(the|a|an|it|its|them|this|that|these|those|his|her|their)\b", re.I)


def is_relative_position_phrase(ctx: Ctx) -> bool:
    """The fragment describes something RELATIVE to another thing — 'in
    the rock', 'beneath the old wooden floor', 'under it'. Splitter rules
    14/30/39 (in/on/at place phrases) win first; else the fragment OPENS
    with preposition + article/pronoun. These lean on the previous image →
    hold_previous + decorate. Prints the opening phrase."""
    if ctx.rule_ids() & RELATIVE_RULES:
        rid = sorted(ctx.rule_ids() & RELATIVE_RULES)
        return ctx.note("is_relative_position_phrase", f"splitter rule {rid}")
    m = _RELATIVE_RX.match(ctx.fragment.strip())
    if m:
        opening = " ".join(ctx.fragment.strip().split()[:4])
        return ctx.note("is_relative_position_phrase", f"'{opening}…'")
    return False


def is_question_to_viewer(ctx: Ctx) -> bool:
    """A question aimed at the viewer — 'So what happened?'. Detected when
    this fragment ENDS its sentence with a '?'. Feeds: typography."""
    if ctx.fragment.rstrip().endswith("?"):
        return ctx.note("is_question_to_viewer", ctx.fragment.strip()[-40:])
    return False


# (is_short_no_visual is GONE on purpose: the splitter's merge rules
#  1008/1009/1010 already fold non-visual scraps back into their
#  neighbours at split time — what survives is usually visual, like
#  'two dollars'. Non-visual detection belongs upstream, not here.)

# ------------------------------------------------------------------- planned
def is_single_object_focus(ctx: Ctx) -> bool:
    """ONE concrete object is the star — 'one dollar coin', 'a gold bar'.
    Detect: search_term of 1-3 nouns with a singular determiner (NLP:
    exactly one noun chunk, no plural). Feeds: stock + decorate (cut it
    out with the object tab)."""
    return False


def is_addition_to_scene(ctx: Ctx) -> bool:
    """Something ADDED into the previous shot — 'add a jar of nutmeg into
    the cupboard'. Detect: add|put|place|appears + into/onto/in over the
    full sentence. Feeds: hold_previous + decorate (stamp tab)."""
    return False


def is_transformation_of_previous(ctx: Ctx) -> bool:
    """The previous image CHANGES — 'the map turns red', 'it rots away'.
    Detect: turns|becomes|transforms|changes + colour/state word (NLP:
    subject is a pronoun referring back). Feeds: ai_edit_previous."""
    return False


def is_continuation_of_previous(ctx: Ctx) -> bool:
    """The sentence carries on over the same visual — 'which meant...',
    'and so...'. Detect: mid-sentence fragment opening with
    which/and/so/that/it AND no concrete noun (NLP-verified).
    Feeds: hold_previous."""
    return False


def is_abstract_concept(ctx: Ctx) -> bool:
    """Unfilmable abstractions — 'monopoly', 'inflation', 'betrayal'.
    Detect: search_term noun in a curated abstract list, or NLP noun with
    no physical hypernym. Feeds: ai_stock or typography."""
    return False


def is_on_board_suitable(ctx: Ctx) -> bool:
    """A single strong image suiting the board composite (stock_on_board /
    wikipedia_on_board). Detect: sentence-opening fragment whose
    search_term is one concrete noun phrase. Feeds: stock_on_board."""
    return False


def is_stickman_story_beat(ctx: Ctx) -> bool:
    """A narrative action beat for the stickman/AI style — 'a merchant
    sails east'. Detect: subject + action verb, no filmable noun,
    CONSECUTIVE with other such beats (then: group modifier + shared
    group_id). Feeds: ai_stock (+ group)."""
    return False


def is_caption_emphasis(ctx: Ctx) -> bool:
    """A punchline where the AUTOMATIC tilted caption lands — 'worth its
    WEIGHT in GOLD.'. Detect: sentence-final fragment with '!', an
    ALL-CAPS word or a superlative. Feeds: the caption modifier on
    whatever base the row has."""
    return False


def is_comparison_pair(ctx: Ctx) -> bool:
    """An explicit A-versus-B — 'silver versus nutmeg'. Detect: vs|versus|
    compared to between two noun phrases. Feeds: two grouped stock rows
    (needs the multi-row splitter, TODO #5)."""
    return False


CHECKS = [
    is_noun_list,
    is_location,
    is_quote_or_speech,
    is_big_number_or_statistic,
    is_year_or_date,
    is_famous_person_or_thing,
    is_relative_position_phrase,
    is_question_to_viewer,
    is_single_object_focus,
    is_addition_to_scene,
    is_transformation_of_previous,
    is_continuation_of_previous,
    is_abstract_concept,
    is_on_board_suitable,
    is_stickman_story_beat,
    is_caption_emphasis,
    is_comparison_pair,
]


def run_checks(data: dict):
    """TOP-HALF entry point. Returns (results, founds): per-entry bools,
    and per-entry {check: exact matched thing} for the printout."""
    fragments = list(data.keys())
    results, founds = {}, {}
    for i, (frag, row) in enumerate(data.items()):
        ctx = Ctx(frag, row, i, fragments)
        results[frag] = {fn.__name__: bool(fn(ctx)) for fn in CHECKS}
        founds[frag] = dict(ctx.found)
        founds[frag]["__sentence__"] = ctx.full_sentence()
        founds[frag]["__rule_ids__"] = sorted(ctx.rule_ids())
    return results, founds


def print_check_table(results: dict, founds: dict) -> None:
    """The concept-notes format, plus the rebuilt sentence, the splitter's
    rule ids, and the exact matched thing for every True."""
    for frag, checks in results.items():
        f = founds.get(frag, {})
        print(f'\n"{frag[:70]}"')
        sent = f.get("__sentence__", "")
        if sent and sent != frag:
            print(f'     sentence: "{sent[:90]}"')
        if f.get("__rule_ids__"):
            print(f"     splitter rule_ids: {f['__rule_ids__']}")
        for name, val in checks.items():
            extra = f"   → {f[name]}" if val and name in f else ""
            print(f"     {name.capitalize()}() = {val}{extra}")


# ═══════════════════════════════════════════════════════════════════════════
# BOTTOM HALF — ASSIGNMENT (the flowchart)
# First matching rule wins; entries with a NON-empty media_type are sacred.
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Rule:
    check: str
    media_type: str
    modifiers: tuple = ()
    note: str = ""
    first_of_run: bool = False   # skip if the PREVIOUS row matched too —
                                 # one list split over rows = ONE collage


FLOWCHART = [
    Rule("is_noun_list", "stock", ("collage",),
         "rule-of-three noun list in this fragment → collage",
         first_of_run=True),
    Rule("is_location", "map", (),
         "a country the map data can actually render"),
    Rule("is_famous_person_or_thing", "wikipedia", (),
         "a named person/org → its wikipedia image"),
    Rule("is_quote_or_speech", "typography", (),
         "a quoted phrase → big words on screen"),
    Rule("is_big_number_or_statistic", "typography", (),
         "a striking figure → big words on screen"),
    Rule("is_year_or_date", "typography", (),
         "a specific year/date → big words on screen"),
    Rule("is_question_to_viewer", "typography", (),
         "a question at the viewer → big words on screen"),
    Rule("is_relative_position_phrase", "hold_previous", ("decorate",),
         "relative to another thing → hold the previous image + decorate"),
]

for _r in FLOWCHART:
    assert _r.media_type in MEDIA_TYPES, f"unknown media_type: {_r.media_type}"
    assert all(m in MODIFIERS for m in _r.modifiers), f"bad modifiers: {_r}"
    assert any(fn.__name__ == _r.check for fn in CHECKS), f"bad check: {_r}"


def assign(data: dict, results: dict, founds: dict = None) -> "list[str]":
    """Apply the flowchart to EMPTY media_type entries only."""
    founds = founds or {}
    changed = []
    frags = list(data.keys())
    for i, (frag, row) in enumerate(data.items()):
        if row.get("media_type") not in ("", None):
            continue                       # already tagged — NEVER overwrite
        for rule in FLOWCHART:
            if results[frag][rule.check]:
                if rule.first_of_run and i > 0 \
                        and results[frags[i - 1]].get(rule.check):
                    # a continuation of the run above — ONE tag per run;
                    # the rest stay empty for manual (see TODO #5: groups)
                    print(f'  · "{frag[:50]}"  continues the run above — '
                          f'left empty (one {rule.media_type} per run)')
                    break
                row["media_type"] = rule.media_type
                mods = [m for m in rule.modifiers
                        if m not in (row.get("modifiers") or [])]
                row["modifiers"] = (row.get("modifiers") or []) + mods
                changed.append(frag)
                what = founds.get(frag, {}).get(rule.check, "")
                print(f'  → "{frag[:50]}"  =  {rule.media_type}'
                      f'{list(rule.modifiers) or ""}'
                      f'{f"   [{what}]" if what else ""}   ({rule.note})')
                break
    return changed


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return run_selftest()
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    if len(argv) != 1:
        print(__doc__)
        return 2
    path = Path(argv[0])
    data = json.loads(path.read_text(encoding="utf-8"))

    print("=" * 70)
    print("DETECTION — every check on every entry (False when in doubt)")
    print("=" * 70)
    results, founds = run_checks(data)
    print_check_table(results, founds)

    print("\n" + "=" * 70)
    print("ASSIGNMENT — first matching flowchart rule; EMPTY rows only")
    print("=" * 70)
    changed = assign(data, results, founds)
    untouched = sum(1 for f in data if f not in changed)
    print(f"\nassigned {len(changed)} entry(ies); {untouched} left for "
          f"MANUAL_TAGGING (all-False or already tagged — that's fine).")

    if dry:
        print("[dry-run] nothing written.")
    elif changed:
        bak = path.with_suffix(path.suffix + ".bak")
        bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        print(f"written → {path}   (backup: {bak.name})")
    else:
        print("nothing to write.")
    return 0


def run_selftest() -> int:
    R = lambda **kw: dict({"media_type": "", "modifiers": [],
                           "search_term": "", "rule_ids": []}, **kw)
    demo = {
        # the two real-world FALSE POSITIVES from review — must stay False:
        "If you were a European merchant, getting it meant": R(),
        "sailing for months, surviving": R(),
        # 'two dollars' must NOT be tagged (the short-no-visual rule is gone;
        # it IS a big-number though — that's the correct call):
        "It costs about": R(search_term="coin"),
        "two dollars": R(search_term="two dollars"),
        # real signals:
        "nutmeg, cloves and cinnamon ruled the world.": R(),
        "ribs,": R(rule_ids=[15]),                 # splitter says: list run
        "in modern-day Indonesia.": R(search_term="Indonesia"),
        'they called it "worth its weight in gold".': R(),
        "the fine was £4,000 per sailor.": R(),
        "in 1667, everything changed.": R(),
        "beneath the old wooden floor": R(),
        "So what happened next?": R(),
        "already tagged, and stays that way.": R(media_type="stock"),
    }
    results, founds = run_checks(demo)
    A = results
    assert not A["If you were a European merchant, getting it meant"][
        "is_noun_list"], "clause list must be False"
    assert not A["sailing for months, surviving"]["is_noun_list"]
    assert A["nutmeg, cloves and cinnamon ruled the world."]["is_noun_list"]
    assert A["ribs,"]["is_noun_list"], "splitter rule_id 15 must win"
    assert A["in modern-day Indonesia."]["is_location"]
    assert founds["in modern-day Indonesia."]["is_location"] == "Indonesia"
    assert A['they called it "worth its weight in gold".']["is_quote_or_speech"]
    assert "worth its weight" in founds[
        'they called it "worth its weight in gold".']["is_quote_or_speech"]
    assert A["the fine was £4,000 per sailor."]["is_big_number_or_statistic"]
    assert "£4,000" in founds["the fine was £4,000 per sailor."][
        "is_big_number_or_statistic"]
    assert A["two dollars"]["is_big_number_or_statistic"] is False  # no digits
    assert A["in 1667, everything changed."]["is_year_or_date"]
    assert A["beneath the old wooden floor"]["is_relative_position_phrase"]
    assert A["So what happened next?"]["is_question_to_viewer"]
    if _nlp() is None:   # False-when-in-doubt contract without the model
        assert not any(A[f]["is_famous_person_or_thing"] for f in demo)
    changed = assign(demo, results, founds)
    assert demo["already tagged, and stays that way."]["media_type"] == "stock"
    assert demo["If you were a European merchant, getting it meant"][
        "media_type"] == ""
    assert demo["beneath the old wooden floor"]["media_type"] == "hold_previous"
    assert demo["in 1667, everything changed."]["media_type"] == "typography"
    # consecutive list rows: ONE collage, the rest left for manual
    demo2 = {"scurvy, pirates,": R(rule_ids=[15]),
             "and storms,": R(rule_ids=[15])}
    r2, f2 = run_checks(demo2)
    assign(demo2, r2, f2)
    assert demo2["scurvy, pirates,"]["media_type"] == "stock"
    assert demo2["and storms,"]["media_type"] == ""
    print(f"\nselftest OK — {len(changed)} demo entries assigned; clause "
          f"lists rejected; existing tag untouched; NLP "
          f"{'ON' if _nlp() else 'OFF (conservative)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ═══════════════════════════════════════════════════════════════════════════
# TODO — future dev work, in order of value-for-effort
# ═══════════════════════════════════════════════════════════════════════════
# 1. Install the NLP model on the video machine so the re-verification and
#    named-entity checks come alive:
#        uv add spacy && uv run python -m spacy download en_core_web_sm
# 2. Implement is_caption_emphasis + is_continuation_of_previous (regex +
#    the NONVISUAL_RULES ids) — cheap, high-value.
# 3. Implement is_addition_to_scene + is_transformation_of_previous
#    (verb patterns over the full sentence) → hold+decorate / ai_edit.
# 4. Wikipedia page-existence verification for is_famous_person_or_thing
#    (requests HEAD, cached to a json next to this file) — upgrades NER
#    candidates into certainties.
# 5. Multi-row lists → GROUPS: consecutive rows all carrying LIST rule ids
#    are ONE list; give them a shared group_id + the group modifier
#    instead of tagging each row separately.
# 6. Probability scoring: each check returns 0..1 from its several signals
#    (rule_ids strongest), a --min-confidence flag decides; replaces the
#    hard booleans without changing the flowchart.
# 7. SOUND_RULES (60): rows split on 'boom/crash' should auto-fill the sfx
#    column — not a media type, but the same trust-the-splitter trick.
# 8. --report mode: an HTML table of every check × entry for eyeballing
#    new checks before trusting them.
# ═══════════════════════════════════════════════════════════════════════════
