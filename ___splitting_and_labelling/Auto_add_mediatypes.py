"""
Auto_add_mediatypes.py — FIRST-PASS automatic media-type tagging.

    uv run ___splitting_and_labelling/Auto_add_mediatypes.py script_to_search_term.json
    uv run ___splitting_and_labelling/Auto_add_mediatypes.py script_to_search_term.json --dry-run
    uv run ___splitting_and_labelling/Auto_add_mediatypes.py --selftest

The file is split in two halves (marked by the big comment):

  TOP    — DETECTION. Every entry is run through every CHECK function and
           the True/False results are printed, so you can eyeball whether
           each check finds the right things before trusting it.
  BOTTOM — ASSIGNMENT. The ordered FLOWCHART maps check results to a
           media type + modifiers, and updates the json — but an entry is
           NEVER touched unless its media_type is EMPTY. Existing tags
           (yours or a previous run's) are never overwritten.

Most entries getting all-False is EXPECTED and fine: they simply stay
empty and you tag them in MANUAL_TAGGING as usual. This pass only claims
the easy wins it can detect RELIABLY.

Each check can look at several data points:
  - the fragment itself (the json key),
  - the row (search_term etc.),
  - the FULL original sentence, rebuilt by joining neighbouring fragments
    between sentence enders (. ! ? …) — see full_sentence().
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

# The shared catalog (___visuals/CONFIG.py via the MEDIA_TYPES shim) — used
# only to VALIDATE that this file never assigns a type that doesn't exist.
from MEDIA_TYPES import MEDIA_TYPES, MODIFIERS

_SENTENCE_END = re.compile('[.!?…]+["\')\\]]*\\s*$')


# ═══════════════════════════════════════════════════════════════════════════
# TOP HALF — DETECTION
# Every check is `fn(ctx) -> bool`. Keep each one tiny, obvious, and honest:
# if it can't be detected RELIABLY, return False and say so in the docstring.
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Ctx:
    """Everything a check may look at for one entry."""
    fragment: str                 # the json key (this scene's script text)
    row: dict                     # its json value (search_term, ...)
    index: int                    # position in the file
    fragments: list               # every key, in order
    _sentence_cache: dict = field(default_factory=dict)

    def full_sentence(self) -> str:
        """Rebuild the ORIGINAL sentence this fragment came from, by
        walking to the previous sentence ender and the next one."""
        if "s" in self._sentence_cache:
            return self._sentence_cache["s"]
        lo = self.index
        while lo > 0 and not _SENTENCE_END.search(self.fragments[lo - 1]):
            lo -= 1
        hi = self.index
        while hi < len(self.fragments) - 1 and not _SENTENCE_END.search(
                self.fragments[hi]):
            hi += 1
        s = " ".join(self.fragments[lo:hi + 1])
        self._sentence_cache["s"] = s
        return s

    def words(self) -> list:
        return re.findall(r"[A-Za-z']+", self.fragment)


# ---------------------------------------------------------------- implemented
_NOUN_LIST_RX = re.compile(
    r"\b[\w'-]+(?:\s+[\w'-]+)?\s*,\s*[\w'-]+(?:\s+[\w'-]+)?\s*"
    r"(?:,\s*)?(?:and|or)\s+[\w'-]+", re.I)


def is_noun_list(ctx: Ctx) -> bool:
    """A list of things — 'nutmeg, cloves and cinnamon' — the classic
    rule-of-three. Detected by the 'X, Y(,) and/or Z' comma pattern in the
    fragment (checked against the full sentence too, since splits often cut
    lists in half)."""
    return bool(_NOUN_LIST_RX.search(ctx.fragment)
                or _NOUN_LIST_RX.search(ctx.full_sentence()))


def _country_names() -> "set[str]":
    """Country names from ___visuals/_MAP_DATA/world_countries.geojson —
    the SAME data the map renderer uses, so anything detected here is
    guaranteed renderable. Falls back to a tiny builtin set if missing."""
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
    if not names:   # fallback so the check still works standalone
        names = {"indonesia", "france", "italy", "china", "india", "japan",
                 "portugal", "spain", "england", "netherlands"}
    _COUNTRIES = names
    return names


def is_location(ctx: Ctx) -> bool:
    """A named COUNTRY appears in the fragment or its search_term →
    renderable by the map type. Only countries the map data actually
    contains count, so this can never assign an unrenderable map."""
    hay = f"{ctx.fragment} {ctx.row.get('search_term', '')}".lower()
    return any(re.search(rf"\b{re.escape(c)}\b", hay)
               for c in _country_names())


def is_short_no_visual(ctx: Ctx) -> bool:
    """A short connective beat with nothing concrete to show — 'It costs
    about', 'two dollars' — best served by holding the previous image (and
    decorating it if you want). Detected as: 4 words or fewer, no noun
    list, no country, and not the sentence's opening fragment (openers
    usually deserve their own footage)."""
    w = ctx.words()
    if not w or len(w) > 4:
        return False
    if is_noun_list(ctx) or is_location(ctx):
        return False
    sentence = ctx.full_sentence()
    return not sentence.lower().startswith(ctx.fragment.lower())


# ------------------------------------------------------------------- planned
# Every stub below returns False for now. Each docstring records WHAT it
# should catch, HOW to detect it reliably, and WHICH type it feeds — so a
# future dev (or future us) can implement one at a time.

def is_quote_or_speech(ctx: Ctx) -> bool:
    """Direct speech / a quoted phrase — '"worth its weight in gold"'.
    Detect: quotation marks wrapping ≥2 words in the fragment.
    Feeds: typography (big words on screen)."""
    return False


def is_big_number_or_statistic(ctx: Ctx) -> bool:
    """A striking figure — '£4,000', '90 percent', '17 million'.
    Detect: currency symbols / digit groups + percent|million|billion.
    Feeds: typography, or caption on the previous image."""
    return False


def is_year_or_date(ctx: Ctx) -> bool:
    """A specific year or date — 'in 1667', 'by the 1800s'.
    Detect: \\b1[0-9]{3}s?\\b / \\b20[0-2][0-9]\\b with a preposition.
    Feeds: typography (or map when a location co-occurs)."""
    return False


def is_famous_person_or_thing(ctx: Ctx) -> bool:
    """A person/entity with a Wikipedia page — 'Isaac Newton', 'the VOC'.
    Detect: TitleCase multi-word proper noun (mid-sentence) + a live
    wikipedia page-existence lookup (cache it!).
    Feeds: wikipedia."""
    return False


def is_single_object_focus(ctx: Ctx) -> bool:
    """ONE concrete object is the star — 'one dollar coin', 'a gold bar'.
    Detect: search_term of 1-3 nouns with a singular determiner.
    Feeds: stock + decorate (cut it out with the object tab)."""
    return False


def is_addition_to_scene(ctx: Ctx) -> bool:
    """Something ADDED into the previous shot — 'add a jar of nutmeg
    into the cupboard'. Detect: add|put|place|appears + 'into/onto/in'
    referencing the previous scene's subject.
    Feeds: hold_previous + decorate (stamp tab)."""
    return False


def is_transformation_of_previous(ctx: Ctx) -> bool:
    """The previous image CHANGES — 'the map turns red', 'it rots away'.
    Detect: turns|becomes|transforms|changes + colour/state word.
    Feeds: ai_edit_previous."""
    return False


def is_continuation_of_previous(ctx: Ctx) -> bool:
    """The sentence carries on over the same visual — pronoun-led
    fragments ('which meant...', 'and so...').
    Detect: fragment starts with which/and/so/that/it + not sentence-start.
    Feeds: hold_previous."""
    return False


def is_abstract_concept(ctx: Ctx) -> bool:
    """Unfilmable abstractions — 'monopoly', 'inflation', 'betrayal'.
    Detect: search_term noun in a curated abstract-noun list.
    Feeds: ai_stock (generate it) or typography."""
    return False


def is_question_to_viewer(ctx: Ctx) -> bool:
    """A rhetorical question aimed at the viewer — 'So what happened?'.
    Detect: fragment ends the sentence with '?'.
    Feeds: typography."""
    return False


def is_comparison_pair(ctx: Ctx) -> bool:
    """An explicit A-versus-B — 'silver versus nutmeg'.
    Detect: vs|versus|compared to between two noun phrases.
    Feeds: two grouped stock rows (needs the multi-row splitter below)."""
    return False


CHECKS = [
    is_noun_list,
    is_location,
    is_short_no_visual,
    is_quote_or_speech,
    is_big_number_or_statistic,
    is_year_or_date,
    is_famous_person_or_thing,
    is_single_object_focus,
    is_addition_to_scene,
    is_transformation_of_previous,
    is_continuation_of_previous,
    is_abstract_concept,
    is_question_to_viewer,
    is_comparison_pair,
]


def run_checks(data: dict) -> "dict[str, dict[str, bool]]":
    """TOP-HALF entry point: every check on every entry, results printed."""
    fragments = list(data.keys())
    results: dict = {}
    for i, (frag, row) in enumerate(data.items()):
        ctx = Ctx(frag, row, i, fragments)
        results[frag] = {fn.__name__: bool(fn(ctx)) for fn in CHECKS}
    return results


def print_check_table(results: dict) -> None:
    for frag, checks in results.items():
        print(f'\n"{frag[:60]}"')
        for name, val in checks.items():
            print(f"     {name}() = {val}")


# ═══════════════════════════════════════════════════════════════════════════
# BOTTOM HALF — ASSIGNMENT (the flowchart)
# Ordered rules: the FIRST matching check decides the entry, then we stop.
# An entry whose media_type is NOT empty is never touched.
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Rule:
    check: str                    # a CHECKS function name
    media_type: str               # a MEDIA_TYPES catalog name
    modifiers: tuple = ()
    note: str = ""


FLOWCHART = [
    # Is it a list of nouns?  → several picks composed on one card.
    Rule("is_noun_list", "stock", ("collage",),
         "rule-of-three noun list → collage of the picks"),
    # Is it a location?       → the map.
    Rule("is_location", "map", (),
         "a country the map data can actually render"),
    # Short with nothing to show? → hold the previous image + decorate it.
    Rule("is_short_no_visual", "hold_previous", ("decorate",),
         "short connective beat → same as previous, decorated"),
]

# Validate the flowchart against the real catalog at import time — a rule
# naming a type or modifier that doesn't exist must fail LOUDLY, not tag.
for _r in FLOWCHART:
    assert _r.media_type in MEDIA_TYPES, f"unknown media_type: {_r.media_type}"
    assert all(m in MODIFIERS for m in _r.modifiers), f"bad modifiers: {_r}"
    assert any(fn.__name__ == _r.check for fn in CHECKS), f"bad check: {_r}"


def assign(data: dict, results: dict) -> "list[str]":
    """Apply the flowchart. ONLY entries with an EMPTY media_type are
    updated (existing tags are sacred). Returns the changed fragments."""
    changed = []
    for frag, row in data.items():
        if row.get("media_type") not in ("", None):
            continue                       # already tagged — NEVER overwrite
        for rule in FLOWCHART:
            if results[frag][rule.check]:
                row["media_type"] = rule.media_type
                mods = [m for m in rule.modifiers
                        if m not in (row.get("modifiers") or [])]
                row["modifiers"] = (row.get("modifiers") or []) + mods
                changed.append(frag)
                print(f'  → "{frag[:50]}"  =  {rule.media_type}'
                      f'{list(rule.modifiers) or ""}   ({rule.note})')
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
    print("DETECTION — every check on every entry")
    print("=" * 70)
    results = run_checks(data)
    print_check_table(results)

    print("\n" + "=" * 70)
    print("ASSIGNMENT — first matching flowchart rule wins; empty rows only")
    print("=" * 70)
    changed = assign(data, results)
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
    demo = {
        "nutmeg, cloves and cinnamon ruled the world.":
            {"media_type": "", "modifiers": [], "search_term": "spices"},
        "in modern-day Indonesia.":
            {"media_type": "", "modifiers": [], "search_term": "Indonesia"},
        "It costs about": {"media_type": "", "modifiers": [],
                           "search_term": "coin"},
        "two dollars.": {"media_type": "", "modifiers": [],
                         "search_term": "two dollars"},
        "already tagged, and stays that way.":
            {"media_type": "stock", "modifiers": [], "search_term": "x"},
    }
    res = run_checks(demo)
    assert res["nutmeg, cloves and cinnamon ruled the world."]["is_noun_list"]
    assert res["in modern-day Indonesia."]["is_location"]
    assert res["two dollars."]["is_short_no_visual"]
    assert not res["It costs about"]["is_short_no_visual"]  # sentence opener
    changed = assign(demo, res)
    assert demo["in modern-day Indonesia."]["media_type"] == "map"
    assert demo["two dollars."]["media_type"] == "hold_previous"
    assert demo["two dollars."]["modifiers"] == ["decorate"]
    assert demo["already tagged, and stays that way."]["media_type"] == "stock"
    assert "already tagged, and stays that way." not in changed
    print(f"selftest OK — {len(changed)} demo entries assigned, "
          f"existing tag untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ═══════════════════════════════════════════════════════════════════════════
# TODO — future dev work, in order of value-for-effort
# ═══════════════════════════════════════════════════════════════════════════
# 1. Implement is_quote_or_speech + is_question_to_viewer (pure regex, an
#    afternoon) → typography, the highest-confidence easy wins.
# 2. Implement is_big_number_or_statistic + is_year_or_date (regex) →
#    typography / caption.
# 3. Implement is_continuation_of_previous (pronoun/conjunction openers,
#    mid-sentence) → hold_previous; pairs beautifully with rule 3.
# 4. Implement is_famous_person_or_thing with a CACHED wikipedia
#    page-existence lookup → wikipedia.
# 5. Implement is_addition_to_scene + is_transformation_of_previous
#    (verb-pattern regexes over the full sentence) → hold+decorate /
#    ai_edit_previous.
# 6. Noun lists across MULTIPLE consecutive fragments: today a list split
#    over rows tags each row separately; a smarter pass should instead
#    create a GROUP (same group_id, group modifier) across those rows —
#    the new-world rule-of-three.
# 7. Probability scoring: several weak signals per check (fragment, full
#    sentence, search_term, spaCy POS tags) combined into a confidence,
#    with a --min-confidence flag, instead of hard booleans.
# 8. A --report mode writing an HTML table of every check result per
#    entry, for eyeballing new checks before trusting them.
# ═══════════════════════════════════════════════════════════════════════════
