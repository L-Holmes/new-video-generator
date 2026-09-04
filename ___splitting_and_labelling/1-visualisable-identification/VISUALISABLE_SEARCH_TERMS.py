"""
VISUALISABLE_SEARCH_TERMS.py — what stage 1 hands the tagging stages.

    from VISUALISABLE_SEARCH_TERMS import facts_for_lines

    facts = facts_for_lines(stage_1_rows)      # main.py's stage 1 output
    facts[3].search_term            --> "whale skeletons"
    facts[3].same_scene_as_previous --> False
    facts[4].same_scene_as_previous --> True     ("It was once covered")

TWO ANSWERS, BOTH READ STRAIGHT OFF STAGE 1

  search_term   the thing to go and find footage of, for this line. The
                visualisable itself, plus how it looks by now when the script
                has said:  "tractor" -> "tractor yellow paint splat".
                assemble_term() is the one place that string is built.

  same_scene_as_previous
                every thing in this line was ALREADY on screen — no new
                identity, same setting. Nothing to fetch: 2-auto-tagging
                should edit the picture that is there rather than pay for a
                second one of the same subject.

    e.g.  "there's a valley filled with"   new: valley        -> new scene
          "whale skeletons."               new: whale skeletons -> new scene
          "It was once covered"            new: (none)        -> SAME scene
          "by the Tethys Sea."             new: Tethys Sea    -> new scene

WHY THIS FILE AND NOT VISUALISABLE_SUGGESTIONS.py
    That one answers "what could the human pick?" and hands 3-manual-tagging
    a ranked menu of chips. This one answers "what would WE pick?" and hands
    2-auto-tagging one term and one yes/no. Same source, different question,
    and mixing them would make both harder to read.

NO LINGUISTICS ARE DECIDED HERE. Every field is read off the slots stage 1
already worked out. If a term is wrong the fix is in
_visualisables_extractor.py, never here.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# =============================================================================
# THE DIALS
# =============================================================================

# Which kinds are worth going and finding footage of, best first. A line's
# term comes from the best kind present, and the leftmost slot of that kind.
#   name    "Wadi Al-Hitan" — one specific thing, the strongest search there is
#   thing   "whale skeletons"
#   number  "$2 million"    — usually a chart, so it loses to a real thing
#   date    "the 1600s"     — almost always a caption
KIND_PREFERENCE = ("name", "thing", "reference", "number", "date")

# Kinds that are not something to film: the mercy rule, the narrator, a noise.
NOT_A_SEARCH_TERM = {"fallback", "deictic", "sound"}

# An unresolved reference still reads as the pronoun. Those mean "hold what is
# already on screen", which is the opposite of a search term.
_PRONOUN_SURFACES = {
    "it", "its", "it's", "they", "them", "their", "theirs", "she", "her",
    "hers", "he", "him", "his", "this", "that", "these", "those", "one",
    "i", "we", "us", "our", "you", "your",
}

# Below this, stage 1 was guessing what a pronoun meant. Its answer is fine as
# a chip the human can look at, but not as a term we tag with unasked.
MIN_REFERENCE_CONFIDENCE = 0.25

# How it looks BY NOW goes in the term — that is the whole point of tracking
# a variant ("tractor broken windscreen" finds a different clip from
# "tractor"). But the result still has to be something you would type into a
# stock search box, so the WHOLE term is capped, not just the variant.
#   e.g. KEEP  "tractor broken windscreen"          3 words
#        DROP  "volcanic archipelago tiny, incredibly remote" -> the details
#              go and you are left with "volcanic archipelago", which is
#              what you would actually search for
#
# THE CAP IS A BACKSTOP, NOT THE CHOOSER. _visualisables_extractor's
# fill_variants() already ordered the details best first (detail_rank(): a
# kind, then a colour, then a look, then everything else), so what this cuts
# is always the least useful thing the script said — not the last one it
# happened to say.
MAX_TERM_WORDS = 4

# =============================================================================
# THE THEME DIAL — off, and here is why
# =============================================================================
#
# Stage 1 works out what the script is ABOUT and what sort of thing that is
# (_theme_engine): "place"/"Egypt", "era"/"64 AD", "culture"/"Roman". This is
# whether any of it is allowed into a search term.
#
# OFF. Adding a word the script did not say to THIS line is the only change
# in this file that can make a WORKING term worthless: "Roman waterfall"
# returns nothing where "waterfall" returns a waterfall. Every other change
# here makes a bad term better or leaves it alone; this one can go backwards.
# Turn it on when there is a measurement to turn it on with.
APPLY_THEMES = False

# ...and even then, only a PLACE. A place is where the shot IS, and a stock
# library is full of "valley Egypt". An ERA and a CULTURE are adjectives
# about a subject rather than a location, and they are what turns a real
# search into an empty one. They stay behind the dial until measured.
THEME_KIND_APPLIED = "place"

# Which slot kinds may take a theme at all. A KIND_NAME is already the most
# specific search there is, a KIND_NUMBER is a figure on a card, and a
# KIND_DATE is a caption — none of them is made better by a place.
THEME_APPLIES_TO_KIND = "thing"


# =============================================================================
# THE ASSEMBLER — the ONE place a search term is built
# =============================================================================

def assemble_term(name: str, variant: str | None = None,
                  theme: str | None = None) -> str:
    """Put a slot's parts together into the thing you would type.

        <name>   [<variant>]   [<theme_text>]

        assemble_term("tractor", "yellow paint")   --> "tractor yellow paint"
        assemble_term("valley", None, "Egypt")     --> "valley Egypt"
        assemble_term("volcanic archipelago", "tiny, incredibly remote")
                                                   --> "volcanic archipelago"

    THE NAME COMES FIRST, ALWAYS. It is the only part that is certainly
    right; everything after it is something the script has since added, and a
    bare correct name beats a padded one. Stock search is word-order
    insensitive at this length, so nothing is lost by not building a phrase.

    A DETAIL IS APPENDED ONLY IF IT FITS. The variant arrives from
    fill_variants() as one comma-joined string, ALREADY ORDERED best first,
    and already limited to what the script had revealed by this line — so
    this walks it in order and stops at the first one that would take the
    term past MAX_TERM_WORDS. Stopping rather than skipping on: once the
    best remaining detail will not fit, putting a worse one in front of it
    would be choosing by length instead of by usefulness.

    WHY BOTH CALLERS COME HERE. VISUALISABLE_SEARCH_TERMS._term_for() and
    VISUALISABLE_SUGGESTIONS._pairs() used to build a term each, so the two
    could — and did — disagree about the same slot: the tagger offered one
    string as a chip and the auto-tagger wrote a different one into the row.
    One function is the fix.

    NEVER RAISES, and an empty name is an empty term.
    """
    name = " ".join((name or "").split())
    if not name:
        return ""
    parts, words = [name], len(name.split())
    for detail in _details(variant):
        length = len(detail.split())
        if words + length > MAX_TERM_WORDS:
            break
        parts.append(detail)
        words += length
    theme = " ".join((theme or "").split())
    if theme and words + len(theme.split()) <= MAX_TERM_WORDS:
        parts.append(theme)
    return " ".join(parts)


def _details(variant: str | None) -> list[str]:
    """The variant back into the details it was made of.
    e.g. "yellow paint, broken windscreen" --> ["yellow paint",
                                                "broken windscreen"]
    The comma is fill_variants()' own join, so this is its other half."""
    return [d.strip() for d in (variant or "").split(",") if d.strip()]


@dataclass
class LineFacts:
    """Stage 1's answer for ONE line, in the terms stage 2 asks in."""
    line: str
    search_term: str = ""              # "" = nothing here worth filming
    identities: tuple = ()             # every thing in this line
    new_identities: tuple = ()         # the ones not seen in any earlier line
    setting: str | None = None
    # the heads this line lost for being unfilmable — ("monopoly",). Stage 1
    # found something here; it just is not a picture. 2-auto-tagging reads
    # this to answer "is this line an abstract concept" — see
    # shared_text_logic.is_abstract_concept().
    abstract_concepts: tuple = ()
    same_scene_as_previous: bool = False
    why: str = ""                      # plain English, for the printout
    slots: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.search_term or self.identities)


# =============================================================================
# THE ONE CALL
# =============================================================================

def facts_for_lines(rows: list) -> list[LineFacts]:
    """One LineFacts per line, in script order.

    @input rows = stage 1's output — [{"line", "template", "slots"}] as
        main.py's stage_1_visualisables writes it.
    @output the same length and order, so facts[i] belongs to line i.

    NEVER RAISES: a stage that cannot answer must not be able to stop the
    tagging. The worst case is empty facts, which every caller reads as
    "no opinion".
    """
    out, seen, previous_setting = [], set(), None
    for row in (rows or []):
        try:
            facts = _facts_for_row(row, seen, previous_setting,
                                   is_first=not out)
        except Exception:                              # pragma: no cover
            facts = LineFacts(line=(row or {}).get("line", ""))
        seen.update(facts.identities)
        if facts.setting:
            previous_setting = facts.setting
        out.append(facts)
    return out


def facts_by_line(rows: list) -> dict:
    """The same answers keyed by line TEXT — what 2-auto-tagging looks up,
    because the flowchart only ever holds the line it is on.

    A script CAN repeat a line ("It was."). The first one wins: they are the
    same words, and stage 2 only ever asks "what is in this text".
    """
    by_line = {}
    for facts in facts_for_lines(rows):
        by_line.setdefault(facts.line, facts)
    return by_line


# =============================================================================
# internals
# =============================================================================

def _facts_for_row(row: dict, seen: set, previous_setting: str | None,
                   is_first: bool) -> LineFacts:
    line = (row or {}).get("line", "")
    slots = (row or {}).get("slots") or {}
    filmable = [s for s in _in_slot_order(slots) if _is_filmable(s)]

    identities = tuple(_identity(s) for s in filmable)
    new_identities = tuple(i for i in identities if i not in seen)
    setting = _setting_of(filmable) or previous_setting

    facts = LineFacts(line=line, slots=slots,
                      search_term=_term_for(filmable),
                      identities=identities,
                      new_identities=new_identities,
                      setting=setting,
                      abstract_concepts=_abstract_concepts(slots))
    facts.same_scene_as_previous, facts.why = _same_scene(
        facts, previous_setting, is_first)
    return facts


def _in_slot_order(slots: dict) -> list:
    return [slots[k] for k in sorted(slots, key=_as_int)]


def _as_int(key) -> int:
    try:
        return int(key)
    except (TypeError, ValueError):
        return 0


def _is_filmable(slot: dict) -> bool:
    """Is this slot something you could go and find footage of?"""
    if slot.get("kind") in NOT_A_SEARCH_TERM:
        return False
    name = (slot.get("visualisable") or "").strip()
    if not name or name.lower() in _PRONOUN_SURFACES:
        return False                       # an "it" nothing resolved
    if slot.get("kind") == "reference" \
            and (slot.get("confidence") or 0) < MIN_REFERENCE_CONFIDENCE:
        return False                       # resolved, but only just
    return True


def _identity(slot: dict) -> str:
    """One label for this thing across the whole script — stage 1's own
    answer, so "The tractor", "the tractor" and a resolved "it" are one."""
    return (slot.get("identity")
            or slot.get("visualisable") or "").strip().lower()


def _abstract_concepts(slots: dict) -> tuple:
    """The unfilmable heads stage 1 dropped on this line.

    A LINE-level fact, so every slot carries the same list and the first one
    that has it is the answer. Empty when the line had nothing abstract in
    it, and empty when the line got no slots at all — see
    _visualisables_extractor._stamp_abstract_concepts() for that gap.
    """
    for slot in _in_slot_order(slots):
        found = slot.get("abstract_concepts")
        if found:
            return tuple(found)
    return ()


def _setting_of(filmable: list) -> str | None:
    """The background this line stands in: a slot that IS the setting, else
    whatever location stage 1 carried forward onto these slots."""
    for slot in filmable:
        if slot.get("is_setting"):
            return (slot.get("visualisable") or "").strip() or None
    for slot in filmable:
        if slot.get("location"):
            return str(slot["location"]).strip() or None
    return None


def _term_for(filmable: list) -> str:
    """The one thing this line puts on screen, as you would type it.

    e.g. slots for "there's a valley filled with"  -> "valley"
         after the script has splattered it        -> "tractor yellow paint"

    The best kind present wins (KIND_PREFERENCE), leftmost of that kind, and
    then assemble_term() does the writing — the same function the suggestion
    chips are built with, so the two can never offer different strings for
    the same slot.
    """
    if not filmable:
        return ""
    best = min(filmable, key=lambda s: _kind_rank(s.get("kind")))
    return assemble_term(best.get("visualisable"), best.get("variant"),
                         _theme_for(best))


def _theme_for(slot: dict) -> str | None:
    """The theme this slot may take, or None — which is the normal answer.

    "valley" -> "valley Egypt", and nothing else in the five scripts.

    FOUR CONDITIONS, ALL OF THEM. The first is the dial; the rest are the
    task's own three, and each of them is a way the term could get worse:

      i)   APPLY_THEMES. Off, so this returns None and every term is exactly
           what it was. That is the proof the feature cannot hurt anything.
      ii)  THE TERM IS A SINGLE COMMON NOUN. One word, kind "thing", and no
           variant — a term that is already specific ("Wadi Al-Hitan",
           "$2 million", "tractor yellow paint") is left alone, because
           adding a place to it can only narrow a search that was already
           going to work.
      iii) THE THEME IS LIVE HERE. _theme_engine only fills these fields when
           the theme is in the top of BOTH the text so far AND the window
           around this line, so a theme_text that is present IS a live one —
           a place the script left ten lines ago is already None by here.
      iv)  IT IS A PLACE. See THEME_KIND_APPLIED.
    """
    if not APPLY_THEMES:
        return None
    if slot.get("theme_kind") != THEME_KIND_APPLIED:
        return None
    if slot.get("kind") != THEME_APPLIES_TO_KIND:
        return None
    if (slot.get("variant") or "").strip():
        return None
    name = (slot.get("visualisable") or "").strip()
    if len(name.split()) != 1:
        return None
    theme = (slot.get("theme_text") or "").strip()
    if not theme or theme.lower() == name.lower():
        return None
    return theme


def _kind_rank(kind) -> int:
    try:
        return KIND_PREFERENCE.index(kind)
    except ValueError:
        return len(KIND_PREFERENCE)


def _same_scene(facts: LineFacts, previous_setting: str | None,
                is_first: bool) -> tuple[bool, str]:
    """Is everything in this line ALREADY on screen?

    Yes means 2-auto-tagging should edit the picture that is there instead of
    fetching a second one of the same subject. Deliberately strict — a wrong
    "yes" holds a picture the line has moved on from, which is worse than
    paying for one more stock clip.
    """
    if is_first:
        return False, "the first line — there is nothing to hold"
    if not facts.identities:
        return False, "nothing filmable here"      # PART F already holds these
    if facts.new_identities:
        return False, (f"introduces {', '.join(facts.new_identities[:3])}")
    return True, ("everything here was already on screen: "
                  + ", ".join(facts.identities[:3]))


# DO NOT add "...and the setting is the same" to the rule above. Stage 1's
# `location` looks AHEAD — a thing is given the setting named in a LATER
# segment, which is the whole point of it ("whale skeletons." is in Egypt
# because a segment 3 back said so, and "It was once covered" is in the
# Tethys Sea because the segment AFTER it says so). Comparing settings
# therefore reports a move one line early and calls a plain hold a new
# scene. Measured on script-whales: it broke "It was once covered".
#
# Nothing is lost by leaving it out: you cannot move the camera somewhere
# without naming the place, and naming it makes it a new identity, which the
# rule above already catches, on exactly the right line.


if __name__ == "__main__":
    # uv run VISUALISABLE_SEARCH_TERMS.py — the worked example end to end.
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
    import PATHS  # noqa: F401
    import myownstuff

    demo = ["In Egypt,", "there's a valley filled with", "whale skeletons.",
            "It was once covered", "by the Tethys Sea."]
    per_line = myownstuff.get_visualisable_data_for_line_segments(demo)
    rows = []
    for line, entry in zip(demo, per_line):
        template, slots = next(iter((entry or {"": {}}).items()))
        rows.append({"line": line, "template": template, "slots": slots})

    print(f"\n{'line':<32} {'search term':<24} same scene?  why")
    for f in facts_for_lines(rows):
        print(f"{f.line[:31]:<32} {f.search_term[:23]:<24} "
              f"{str(f.same_scene_as_previous):<12} {f.why}")
