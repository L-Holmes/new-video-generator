"""
uv run ___splitting_and_labelling/Auto_add_mediatypes.py stickman_script_to_search_term.json --dry-run > TEMP_OUTPUT.txt



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

# MEDIA_TYPES put the repo root on sys.path, so the shared config resolves.
from CONFIG import (  # noqa: E402
    MIN_DURATION_GATED_TYPES,
    line_too_short_for_new_footage,
)

# Sentence enders for RECONSTRUCTION: the big punctuation (. ! ? … ; :) —
# found ANYWHERE, including mid-fragment ('shipwrecks. But if you made'),
# not just at fragment ends. ':' is an ender per splitter rule 1, which is
# what makes 'on Earth: The Banda Islands.' split correctly.
_ENDER_RX = re.compile(r'[.!?…;:]+["\')\]]*')


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
# ---------------------------------------------------------------------------
# Optional BOOSTER libraries — each a well-maintained, widely-used extractor
# for exactly one of our jobs. All OPTIONAL: missing ones just mean that
# check falls back to its regex (never guessier, only less covering).
#     uv add dateparser price-parser number-parser geonamescache
# ---------------------------------------------------------------------------
def _try(name):
    try:
        return __import__(name)
    except Exception:
        return None


_dateparser_search = None
if _try("dateparser"):
    from dateparser.search import search_dates as _dateparser_search
_price_parser = _try("price_parser")          # £4,000 → (4000, '£')
_number_parser = _try("number_parser")        # 'seventeen million' → 17000000
_geonamescache = _try("geonamescache")        # city → country code


def _booster_report():
    rows = [("dateparser (dates in any format)", _dateparser_search),
            ("price-parser (money amounts)", _price_parser),
            ("number-parser (written-out numbers)", _number_parser),
            ("geonamescache (city → country)", _geonamescache)]
    missing = [n.split(" ")[0] for n, m in rows if m is None]
    on = [n for n, m in rows if m is not None]
    if on:
        print(f"[auto-tag] boosters ON: {', '.join(on)}")
    if missing:
        print(f"[auto-tag] boosters OFF: {', '.join(missing)} — enable with: "
              f" uv add {' '.join(missing)}")


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
    shared: dict = field(default_factory=dict)   # ONE dict per run_checks
    _cache: dict = field(default_factory=dict)

    def rule_ids(self) -> "set[int]":
        return {int(r) for r in (self.row.get("rule_ids") or [])}

    def _doc_layout(self):
        """The whole file joined once, with each fragment's char range and
        every big-punctuation ender position — shared across all rows."""
        if "layout" not in self.shared:
            text, offsets, pos = "", [], 0
            for f in self.fragments:
                offsets.append((pos, pos + len(f)))
                text += f + " "
                pos += len(f) + 1
            enders = [m.end() for m in _ENDER_RX.finditer(text)]
            self.shared["layout"] = (text, offsets, enders)
        return self.shared["layout"]

    def sentence_span(self) -> "tuple[int, int]":
        """(start, end) chars of this fragment's sentence in the joined
        text: from the ender BEFORE the fragment to the first ender at or
        after the fragment's END (a fragment straddling two sentences gets
        both — that's what it genuinely covers)."""
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

    def full_sentence(self) -> str:
        if "s" not in self._cache:
            text, _, _ = self._doc_layout()
            s, e = self.sentence_span()
            self._cache["s"] = text[s:e].strip()
        return self._cache["s"]

    def doc(self, text: str):
        nlp = _nlp()
        if nlp is None:
            return None
        key = ("d", text)
        if key not in self._cache:
            self._cache[key] = nlp(text)
        return self._cache[key]

    def note(self, check: str, what: str, fill: str = None) -> bool:
        """Record what was matched (shown in the table), optionally with a
        CLEAN value for auto-filling search_term, and return True."""
        self.found[check] = what
        if fill:
            self.found[f"{check}::fill"] = fill
        return True


# a tiny finite-verb stoplist for the no-NLP path: a "list item" containing
# one of these is a CLAUSE, not a thing ("getting it meant", "she ran")
_CLAUSE_WORDS = re.compile(
    r"\b(is|are|was|were|be|been|being|has|have|had|do|does|did|meant|means|"
    r"getting|got|went|goes|came|comes|said|says|made|makes|ran|runs|"
    r"sailing|surviving|took|takes|gave|gives|becomes?|became|ruled|rules)\b", re.I)

_LIST_RX = re.compile(
    r"\b((?:[\w'-]+\s+){0,2}[\w'-]+)\s*,\s*"
    r"((?:[\w'-]+\s+){0,2}[\w'-]+)\s*"
    r"(?:,\s*)?(?:and|or)\s+((?:[\w'-]+\s+){0,2}[\w'-]+)", re.I)


def _validate_list_match(ctx: Ctx, text: str, m) -> "str | None":
    """The strict list matcher: 'A, B(,) and/or C' where every item is a
    short THING — verified by NLP when available (each item holds a noun
    and no verb), else by the clause-word stoplist. The final item is
    trimmed at the first clause word, since the sentence usually carries
    straight on ('...and cinnamon RULED the world'). Returns the matched
    list, rebuilt from its trimmed items, or None."""
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


def _list_spans(ctx: Ctx):
    """All VALIDATED noun-list matches in this entry's sentence, as char
    spans in the JOINED text (see _doc_layout) — a list split over several
    fragments lights up every fragment it touches, and only those. Lists
    never cross a big-punctuation ender."""
    ss, se = ctx.sentence_span()
    key = ("lists", ss, se)
    if key in ctx.shared:
        return ctx.shared[key]
    text, _, _ = ctx._doc_layout()
    sentence = text[ss:se]
    spans = []
    pos = 0
    while True:
        m = _LIST_RX.search(sentence, pos)
        if not m:
            break
        rebuilt = _validate_list_match(ctx, sentence, m)
        if rebuilt:
            # the first item can over-grab words that belong to the
            # sentence before the list — the span a fragment must TOUCH
            # starts at the first item's LAST word (doubt → False).
            ms = m.end(1) - len(m.group(1).split()[-1])
            spans.append((ss + ms, ss + m.end(), rebuilt))
            pos = m.end()
        else:
            pos = m.start() + 1    # an invalid alignment must not shadow a
                                   # valid list starting just after it
    ctx.shared[key] = spans
    return ctx.shared[key]


def is_noun_list(ctx: Ctx) -> bool:
    """A list of things — 'nutmeg, cloves and cinnamon'. TRUSTS the
    splitter first: a LIST rule id (15/16/17/25/51/61) on this row means
    the splitter already identified this line as part of a list run.
    Otherwise the strict matcher finds 'A, B and/or C' in the SENTENCE
    and lights up exactly the fragments the matched list TOUCHES — so
    'scurvy,' + 'pirates and' + 'shipwrecks...' all catch it, while 'a
    merchant, getting it meant' in the same sentence stays False (it
    doesn't overlap the list). Every item is verified as a noun, not a
    clause (NLP when available, stoplist otherwise). Assignment still
    tags ONE row per run; grouping the run properly is TODO #5."""
    if ctx.rule_ids() & LIST_RULES:
        return ctx.note("is_noun_list", f"splitter rule "
                        f"{sorted(ctx.rule_ids() & LIST_RULES)}")
    _, offsets, _ = ctx._doc_layout()
    s, e = offsets[ctx.index]
    for ms, me, rebuilt in _list_spans(ctx):
        if s < me and e > ms:              # this fragment touches the list
            n = rebuilt.count(",") + 2     # item count (for the printout)
            return ctx.note("is_noun_list", f"{rebuilt}  ({n} items)",
                            fill=rebuilt)
    return False


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


_GEO_SUFFIX_RX = re.compile(
    r"\b((?:[A-Z][\w'-]+\s+){1,3}"
    r"(?:Islands?|Archipelago|Sea|Ocean|Mountains?|Valley|Desert|River|Bay|"
    r"Coast|Peninsula|Strait|Gulf|Canyon|Falls|Plateau|Plains?|Delta|"
    r"Volcano|Reef|Highlands?|Lakes?))\b")


def _map_country(ctx: Ctx) -> "str | None":
    """The country the MAP TYPE can actually render for this row: a
    geojson country named in the fragment/search_term, or (booster) a big
    city found there via geonamescache, resolved to its country."""
    hay = f"{ctx.fragment} {ctx.row.get('search_term', '')}"
    low = hay.lower()
    for c in _country_names():
        if re.search(rf"\b{re.escape(c)}\b", low):
            return c.title()
    if _geonamescache is not None:
        gc = ctx.shared.setdefault("gc", _geonamescache.GeonamesCache())
        cities = ctx.shared.setdefault("gc_cities", [
            (city["name"], city["countrycode"])
            for city in gc.get_cities().values()
            if city.get("population", 0) >= 200_000])
        countries = gc.get_countries()
        for name, code in cities:
            if re.search(rf"\b{re.escape(name)}\b", hay):
                cname = countries.get(code, {}).get("name", "")
                if cname.lower() in _country_names():
                    return f"{name} → {cname}"
    return None


def is_location(ctx: Ctx) -> bool:
    """A place the MAP type can render. The country comes from the SAME
    geojson the map draws (word-boundary match on fragment/search_term),
    optionally via a big city resolved to its country (geonamescache
    booster). A more SPECIFIC place name in the fragment ('the Banda
    Islands', NER GPE/LOC) is captured for display — but the printed
    'map renders:' country is what the map will actually show, since the
    map data only knows countries."""
    country = _map_country(ctx)
    if country is None:
        return False
    specific = None
    m = _GEO_SUFFIX_RX.search(ctx.fragment)
    if m:
        specific = m.group(1)
    else:
        doc = ctx.doc(ctx.fragment)
        if doc is not None:
            for ent in doc.ents:
                if ent.label_ in ("GPE", "LOC") \
                        and ent.text.lower() not in _country_names():
                    specific = ent.text
                    break
    plain_country = country.split("→")[-1].strip()
    if specific and specific.lower() != plain_country.lower():
        return ctx.note("is_location",
                        f"{specific} (map renders: {plain_country})",
                        fill=f"{specific}, {plain_country}")
    return ctx.note("is_location", country, fill=plain_country)


_QUOTE_RX = re.compile(r'["“”\'‘’]([^"“”\'‘’]+\s+[^"“”\'‘’]+)["“”\'‘’]')


def is_quote_or_speech(ctx: Ctx) -> bool:
    """Direct speech / a quoted phrase — '"worth its weight in gold"'.
    Splitter rule 5 (a phrase in quotation marks) on this row wins first;
    else quotation marks wrapping ≥2 words in the fragment or sentence.
    Feeds: typography. Prints the exact quote."""
    if ctx.rule_ids() & QUOTE_RULES:
        return ctx.note("is_quote_or_speech", "splitter rule [5]")
    m = _QUOTE_RX.search(ctx.fragment) or _QUOTE_RX.search(ctx.full_sentence())
    return ctx.note("is_quote_or_speech", f'"{m.group(1)}"',
                    fill=m.group(1)) if m else False


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
    if m:
        return ctx.note("is_big_number_or_statistic", m.group(1),
                        fill=m.group(1))
    if _price_parser is not None:          # £4 000, USD 12.5m, 4,000 GBP...
        p = _price_parser.Price.fromstring(ctx.fragment)
        if p.amount is not None and p.currency:
            return ctx.note("is_big_number_or_statistic",
                            f"{p.currency}{p.amount_text}",
                            fill=f"{p.currency}{p.amount_text}")
    if _number_parser is not None:         # 'seventeen million'
        for size in (3, 2):
            words = ctx.fragment.split()
            for j in range(len(words) - size + 1):
                window = " ".join(words[j:j + size]).strip(",.!?…")
                try:
                    val = _number_parser.parse_number(window)
                except Exception:
                    val = None
                if val is not None and val >= 1000:
                    return ctx.note("is_big_number_or_statistic",
                                    f"{window} (= {val:,})", fill=window)
    return False


_YEAR_RX = re.compile(
    r"\b(?:(?:in|by|of|from|until|since|around|circa)\s+(?:the\s+)?"
    r"(?P<year>1[0-9]{3}s?|20[0-2][0-9]s?)"
    r"|(?P<decade>1[0-9]{2}0s|20[0-2]0s)"
    r"|(?P<century>\d{1,2}(?:st|nd|rd|th)[- ]century)"
    r"|(?P<full>(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{0,4}))\b")

_HAS_DIGIT_OR_MONTH = re.compile(
    r"\d|January|February|March|April|May|June|July|August|September|"
    r"October|November|December", re.I)


def is_year_or_date(ctx: Ctx) -> bool:
    """A specific year, decade, century or date — 'in 1667', 'the 1600s',
    '17th century', 'March 3rd, 1667'. Splitter rule 53 wins first; then
    the regex (capturing JUST the date part — '1600s', not 'in the
    1600s'); then the dateparser booster for every other format under the
    sun (its hits must contain a digit or month name — dateparser is
    eager, and False-when-in-doubt applies). Feeds: typography."""
    m = _YEAR_RX.search(ctx.fragment)
    hit = next((g for g in (m.group("year"), m.group("decade"),
                            m.group("century"), m.group("full"))
                if g), None) if m else None
    if ctx.rule_ids() & DATE_RULES:
        return ctx.note("is_year_or_date", hit or "splitter rule [53]",
                        fill=hit)
    if hit:
        return ctx.note("is_year_or_date", hit, fill=hit)
    if _dateparser_search is not None:
        try:
            for text, _ in (_dateparser_search(
                    ctx.fragment, languages=["en"]) or []):
                if _HAS_DIGIT_OR_MONTH.search(text):
                    return ctx.note("is_year_or_date", text, fill=text)
        except Exception:
            pass
    return False


# Words that may sit INSIDE a name run without breaking it (both
# neighbours must be Capitalised): 'Alaric the Goth', 'John Paul II of
# Spain', 'Joan of Arc'.
_NAME_CONNECTORS = {"of", "the", "de", "da", "van", "der", "von", "al",
                    "el", "la", "le", "bin", "ibn"}
_ROMAN_RX = re.compile(r"^(?:I{1,3}|IV|V|VI{0,3}|IX|X{1,3})$")
# Capitalised words that must never START a name run (sentence furniture,
# months, pronouns...). A run beginning with one drops it and continues.
_CAP_STOPWORDS = {
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


def _name_runs(ctx: Ctx, text: str) -> "list[str]":
    """Runs of Capitalised words (connectors and roman numerals allowed
    inside). The rules that keep this honest:
      • a run may not START on a stopword ('The Banda...' → drops 'The'),
      • geo-suffix runs ('Banda Islands') belong to is_location, not here,
      • country names belong to is_location,
      • a SINGLE-word run only counts mid-sentence, or when coordinated
        with an accepted name ('Ronaldo and Bradd Pitt' vouches for
        Ronaldo even at the sentence start),
      • with the NLP model, single-word runs must also be NER-confirmed,
      • a run that STARTS the sentence — of ANY length — must also be
        NER-confirmed: sentence-initial capitalisation alone proves
        nothing ('New' capitalises the same way whether it's 'New taxes
        were introduced' or 'New York City never sleeps'; the run only
        earns a pass when spaCy's NER agrees it's a genuine entity, the
        same signal a lone mid-sentence word already needs). No model ->
        reject rather than guess (false when in doubt)."""
    tokens = list(re.finditer(r"[\w'-]+|[^\w\s]", text))
    words = [t.group(0) for t in tokens]

    def is_cap(w):
        return w[:1].isupper() and (w[1:2].islower() or len(w) == 1
                                    or _ROMAN_RX.match(w) or w.isupper())

    runs, i = [], 0
    while i < len(words):
        w = words[i]
        if is_cap(w) and w not in _CAP_STOPWORDS:
            start = i
            j = i + 1
            while j < len(words):
                nxt = words[j]
                if is_cap(nxt) and nxt not in _CAP_STOPWORDS:
                    j += 1
                elif (nxt.lower() in _NAME_CONNECTORS
                      and j + 1 < len(words) and is_cap(words[j + 1])
                      and words[j + 1] not in _CAP_STOPWORDS):
                    j += 2
                else:
                    break
            run_words = words[start:j]
            run = " ".join(run_words)
            sentence_initial = tokens[start].start() == 0 or not \
                text[:tokens[start].start()].rstrip()[-1:] not in ".!?…;:" \
                if False else (
                    not text[:tokens[start].start()].strip()
                    or text[:tokens[start].start()].rstrip()[-1] in ".!?…;:")
            runs.append({"text": run, "n": len(run_words),
                         "start_i": start, "end_i": j,
                         "sentence_initial": sentence_initial})
            i = j
        else:
            i += 1

    doc = ctx.doc(text)
    ner_ok = None
    if doc is not None:
        ner_ok = {ent.text for ent in doc.ents
                  if ent.label_ in ("PERSON", "ORG", "EVENT",
                                    "WORK_OF_ART", "FAC", "GPE", "NORP")}

    accepted = []
    for r in runs:
        t = r["text"]
        if t.lower() in _country_names():
            continue                       # is_location's job
        if _GEO_SUFFIX_RX.fullmatch(t) or _GEO_SUFFIX_RX.search(t):
            continue                       # 'Banda Islands' → is_location
        ner_confirmed = ner_ok is not None and any(
            t in e or e in t for e in ner_ok)
        if r["sentence_initial"]:
            # The leading word's capital proves nothing on its own — it
            # capitalises the same way whether it's really a name or just
            # sentence-initial orthography ('New' in 'New taxes were
            # introduced' vs 'New' in 'New York City never sleeps'). Trust
            # NER instead of the capital letter, for a run of ANY length;
            # no model -> reject rather than guess.
            if ner_confirmed:
                accepted.append(r)
        elif r["n"] >= 2:
            accepted.append(r)
        elif ner_confirmed:
            # single mid-sentence word: only with NER confirmation —
            # without the model, 'European' and friends would slip in.
            # (coordination rescue below still vouches for e.g. Ronaldo.)
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


def is_famous_person_or_thing(ctx: Ctx) -> bool:
    """A person / named thing worth a wikipedia image — 'Alaric the Goth',
    'John Paul II of Spain', 'Jenson Button', 'Ferrari Escaplito'. The
    capitalisation-run engine (see _name_runs) finds every name in the
    fragment; splitter rules 18/50 (name reveals) count as confirmation
    too. Prints ALL names found; auto-fill uses the FIRST (one wikipedia
    lookup per row). TODO #4 adds cached page-existence verification."""
    names = _name_runs(ctx, ctx.fragment)
    if ctx.rule_ids() & NAME_REVEAL_RULES and not names:
        rid = sorted(ctx.rule_ids() & NAME_REVEAL_RULES)
        return ctx.note("is_famous_person_or_thing", f"splitter rule {rid}")
    if names:
        return ctx.note("is_famous_person_or_thing", ", ".join(names),
                        fill=names[0])
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
        q = ctx.fragment.strip()
        return ctx.note("is_question_to_viewer", q[-40:], fill=q)
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
    shared: dict = {}
    for i, (frag, row) in enumerate(data.items()):
        ctx = Ctx(frag, row, i, fragments, shared=shared)
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
    fill_search: bool = False    # auto-fill an EMPTY search_term with the
                                 # check's clean capture (per-rule switch)
    needs_previous: bool = False # never assign to the very first row


# No typography rules (per the boss): text moments become hold_previous +
# the AUTOMATIC caption — the tilted text lands ON the previous image, and
# hold_previous never fetches stock, so search_term safely doubles as the
# caption text (DECORATE_STAGE reads caption_text or search_term).
FLOWCHART = [
    # collage is never auto-assigned — the boss picks it by hand in
    # MANUAL_TAGGING when a noun list actually warrants several images.
    Rule("is_noun_list", "stock", (),
         "noun list → stock (add collage yourself if it needs several picks)",
         first_of_run=True, fill_search=True),
    Rule("is_location", "map", (),
         "a renderable place → the map", fill_search=True),
    Rule("is_famous_person_or_thing", "wikipedia", (),
         "a named person/thing → its wikipedia image", fill_search=True),
    Rule("is_quote_or_speech", "hold_previous", ("caption",),
         "a quote → caption it over the previous image",
         fill_search=True, needs_previous=True),
    Rule("is_big_number_or_statistic", "hold_previous", ("caption",),
         "a striking figure → caption it over the previous image",
         fill_search=True, needs_previous=True),
    Rule("is_year_or_date", "hold_previous", ("caption",),
         "a year/date → caption it over the previous image",
         fill_search=True, needs_previous=True),
    Rule("is_question_to_viewer", "hold_previous", ("caption",),
         "a question → caption it over the previous image",
         fill_search=True, needs_previous=True),
    Rule("is_relative_position_phrase", "hold_previous", ("decorate",),
         "relative to another thing → hold + decorate",
         needs_previous=True),
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
                if rule.needs_previous and i == 0:
                    break            # nothing before it to hold — manual
                fill = founds.get(frag, {}).get(f"{rule.check}::fill")

                # SHORT-SCENE INTELLIGENCE (see MIN_NEW_FOOTAGE_SECONDS):
                # brand-new footage on a line too short to stand on its own
                # would just flash. Prefer editing the previous image instead
                # — hold_previous + decorate, stamping the thing onto it when
                # we have a term ("add decorated stock", the common case).
                # Joining neighbours is left to the human in MANUAL_TAGGING
                # (too destructive to do blind — "very rarely"). The very
                # first line has no previous to hold, so it keeps the new type
                # and the manual tagger's guard catches it.
                if (rule.media_type in MIN_DURATION_GATED_TYPES and i > 0
                        and line_too_short_for_new_footage(frag)):
                    row["media_type"] = "hold_previous"
                    if "decorate" not in (row.get("modifiers") or []):
                        row["modifiers"] = (row.get("modifiers") or []) + ["decorate"]
                    filled = ""
                    if rule.fill_search and fill \
                            and not (row.get("search_term") or "").strip():
                        row["search_term"] = fill
                        row["stamp_source"] = "stock"   # stamp it onto the hold
                        filled = (f'   search_term="{fill[:40]}"'
                                  f'   stamp_source="stock"')
                    changed.append(frag)
                    print(f'  → "{frag[:50]}"  =  hold_previous[\'decorate\']'
                          f'{filled}   (too short for new {rule.media_type} — '
                          f'edit + add to the previous scene instead)')
                    break

                row["media_type"] = rule.media_type
                mods = [m for m in rule.modifiers
                        if m not in (row.get("modifiers") or [])]
                row["modifiers"] = (row.get("modifiers") or []) + mods
                filled = ""
                if rule.fill_search and fill \
                        and not (row.get("search_term") or "").strip():
                    row["search_term"] = fill
                    filled = f'   search_term="{fill[:40]}"'
                changed.append(frag)
                what = founds.get(frag, {}).get(rule.check, "")
                print(f'  → "{frag[:50]}"  =  {rule.media_type}'
                      f'{list(rule.modifiers) or ""}'
                      f'{f"   [{what}]" if what else ""}{filled}'
                      f'   ({rule.note})')
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

    _booster_report()
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

    # — the multi-row list from review: NO rule_ids, split over 3 rows —
    multi = {
        "If you were a European merchant, getting it meant": R(),
        "sailing for months, surviving": R(),
        "scurvy,": R(),
        "pirates and": R(),
        "shipwrecks. But if you made it back with a": R(),
    }
    mres, mfound = run_checks(multi)
    assert mres["scurvy,"]["is_noun_list"], "row 1 of the split list"
    assert mres["pirates and"]["is_noun_list"], "row 2 of the split list"
    assert mres["shipwrecks. But if you made it back with a"][
        "is_noun_list"], "row 3 of the split list"
    assert not mres["If you were a European merchant, getting it meant"][
        "is_noun_list"], "same sentence, no overlap with the list"
    assert not mres["sailing for months, surviving"]["is_noun_list"]
    assert "scurvy, pirates and shipwrecks" in mfound["scurvy,"][
        "is_noun_list"]
    assign(multi, mres, mfound)
    assert multi["scurvy,"]["media_type"] == "stock"
    assert multi["pirates and"]["media_type"] == ""      # one tag per run
    assert multi["shipwrecks. But if you made it back with a"][
        "media_type"] == ""

    # — a fragment holding only the over-grabbed lead of a list must stay
    #   False (no ender between lines here, worst case on purpose) —
    junk = {"two dollars": R(),
            "nutmeg, cloves and cinnamon ruled the world.": R()}
    jres, _ = run_checks(junk)
    assert not jres["two dollars"]["is_noun_list"], "over-grab lead"
    assert jres["nutmeg, cloves and cinnamon ruled the world."][
        "is_noun_list"]

    # — Banda Islands: specific place shown, map country explained —
    banda = {"The Banda Islands. A tiny, incredibly remote volcanic "
             "archipelago": R(search_term="Banda Islands Indonesia")}
    bres, bfound = run_checks(banda)
    cap = bfound[next(iter(banda))].get("is_location", "")
    assert "Banda Islands" in cap and "Indonesia" in cap, cap

    # — the boss's famous-name examples (the engine's truth table) —
    fam = {
        "And that brings us to John Paul II of Spain - a big leader": R(),
        "And the leader of the barbarians was Alaric the Goth of "
        "north macedonia": R(),
        "Jenson Button wasn't the only person there who lived": R(),
        "Ronaldo and Bradd Pitt fought hard against the Ferrari "
        "Escaplito": R(),
        "The Banda Islands. A tiny, incredibly remote volcanic "
        "archipelago": R(search_term="Banda Islands Indonesia"),
        "So the European merchants sailed on": R(),
    }
    fres, ffound = run_checks(fam)
    F = "is_famous_person_or_thing"
    assert "John Paul II of Spain" in ffound[list(fam)[0]][F]
    assert ffound[list(fam)[1]][F] == "Alaric the Goth"
    assert ffound[list(fam)[2]][F] == "Jenson Button"
    r4 = ffound[list(fam)[3]][F]
    assert "Ronaldo" in r4 and "Bradd Pitt" in r4 and "Ferrari Escaplito" in r4
    assert not fres[list(fam)[4]][F], "geo-suffix names are is_location's"
    assert not fres["So the European merchants sailed on"][F], \
        "lone nationality adjectives must not pass"
    assert fres[list(fam)[4]]["is_location"]
    assert "Banda Islands" in ffound[list(fam)[4]]["is_location"]

    # — sentence reconstruction: mid-fragment enders + ':' both respected —
    sent = {"Nutmeg only grew in one place on Earth:": R(),
            "The Banda Islands. A tiny, incredibly remote volcanic "
            "archipelago": R(),
            "in modern-day Indonesia.": R()}
    sctx = Ctx(list(sent)[1], list(sent.values())[1], 1, list(sent),
               shared={})
    s = sctx.full_sentence()
    assert s.startswith("The Banda Islands"), s
    assert s.rstrip().endswith("Indonesia."), s

    # — dates: capture just the date part, in several formats —
    dates = {"But in the 1600s, this little wrinkled seed was the": R(),
             "back in the 17th century": R()}
    dres, dfound = run_checks(dates)
    assert dfound[list(dates)[0]]["is_year_or_date"] == "1600s"
    assert "17th century" in dfound["back in the 17th century"][
        "is_year_or_date"]

    # — boosters (only asserted when the library is installed) —
    if _dateparser_search is not None:
        x = {"It happened on 03/07/1667 at dawn": R()}
        xr, _ = run_checks(x)
        assert xr[next(iter(x))]["is_year_or_date"], "dateparser booster"
    if _price_parser is not None:
        x = {"the ransom was USD 4000 in silver": R()}
        xr, _ = run_checks(x)
        assert xr[next(iter(x))]["is_big_number_or_statistic"], "price booster"
    if _number_parser is not None:
        x = {"seventeen million people watched": R()}
        xr, xf = run_checks(x)
        assert xr[next(iter(x))]["is_big_number_or_statistic"], "number booster"
        assert "17,000,000" in xf[next(iter(x))]["is_big_number_or_statistic"]
    if _geonamescache is not None:
        x = {"the port of Lisbon grew rich": R()}
        xr, xf = run_checks(x)
        assert xr[next(iter(x))]["is_location"], "geonamescache booster"
        assert "Portugal" in xf[next(iter(x))]["is_location"]

    demo = {
        # FIRST row: a date — needs_previous must leave it for manual
        "In 1946, everything changed.": R(),
        # the two real-world FALSE POSITIVES from review — must stay False:
        "If you were a European merchant, getting it meant": R(),
        "sailing for months, surviving": R(),
        "It costs about": R(search_term="coin"),
        "two dollars": R(search_term="two dollars"),
        # real signals:
        "nutmeg, cloves and cinnamon ruled the world.": R(),
        "ribs,": R(rule_ids=[15]),                 # splitter says: list run
        "in modern-day Indonesia.": R(),
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
    # no typography anywhere: text moments are hold_previous + caption,
    # with search_term auto-filled as the caption text
    y = demo["in 1667, everything changed."]
    assert y["media_type"] == "hold_previous" and y["modifiers"] == ["caption"]
    assert y["search_term"] == "1667"
    q = demo["So what happened next?"]
    assert q["media_type"] == "hold_previous" and q["modifiers"] == ["caption"]
    m = demo["in modern-day Indonesia."]
    assert m["media_type"] == "map" and m["search_term"] == "Indonesia"
    # fills never overwrite an existing search_term
    assert demo["two dollars"]["search_term"] == "two dollars"
    # the FIRST row is never given a hold/caption (nothing before it)
    assert demo["In 1946, everything changed."]["media_type"] == ""
    assert not any(v.get("media_type") == "typography" for v in demo.values())
    # consecutive list rows: ONE collage, the rest left for manual
    demo2 = {"scurvy, pirates,": R(rule_ids=[15]),
             "and storms,": R(rule_ids=[15])}
    r2, f2 = run_checks(demo2)
    assign(demo2, r2, f2)
    assert demo2["scurvy, pirates,"]["media_type"] == "stock"
    assert demo2["and storms,"]["media_type"] == ""
    _booster_report()
    print(f"\nselftest OK — {len(changed)} demo entries assigned; the "
          f"3-row split list caught (ONE collage); clause lists rejected; "
          f"date captures exact; fills applied (never overwriting); "
          f"no typography; existing tag untouched; NLP "
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
