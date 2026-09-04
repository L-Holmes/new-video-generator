"""
VISUALISABLE_SUGGESTIONS.py — this stage's answers, shaped for the tagger.

    from VISUALISABLE_SUGGESTIONS import suggestions_for_all_lines

    suggestions_for_all_lines(["In Egypt,",
                               "there's a valley filled with",
                               "whale skeletons."])
    # [...,
    #  {"current":  ["whale skeletons"],
    #   "singles":  [{"term": "valley", "score": 1.5,
    #                 "why": "1 line back"},
    #                {"term": "Egypt",  "score": 1.4,
    #                 "why": "the setting · 2 lines back"}],
    #   "pairs":    [{"term": "whale skeletons Egypt",
    #                 "why": "in the setting named earlier"}],
    #   "pronouns": []}]

WHAT THIS FILE IS FOR
    3-manual-tagging asks one question — "what could go on screen for this
    line, and what did we already put on screen before it?" — and this is
    the only place that question is answered. It is a TRANSLATION layer and
    nothing else: myownstuff.py works the visualisables out, and every field
    below is read straight off its output.

    Nothing linguistic is decided here. If a chip is wrong, the fix is in
    _visualisables_extractor.py or _abstract_term_resolver.py, never here.

    HISTORY: VISUAL_RECOMMENDER.py used to answer this with its own
    from-scratch noun finder — a mini POS tagger, its own compound rules,
    its own pronoun guesser. All of that is deleted. The payload shape it
    handed the tagger is kept verbatim (current / singles / pairs /
    pronouns), because the browser page is built around it and the shape was
    never the part that was wrong.

WHAT COMES OUT, per line
    current   what is IN this line                    ["whale skeletons"]
    singles   things from EARLIER lines, worth reusing, best first, each
              with a score and a plain-English `why`
    pairs     combos worth typing into a search box — a thing plus what the
              script has since said about how it LOOKS, WHERE it is, or HOW
              MANY there are
    pronouns  this line's "it" / "they" / "her", and what they turned out
              to point at, with the resolver's own confidence
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import myownstuff                                          # noqa: E402
# THE assembler. A term is written in one place and read in two, so the chip
# the human is offered and the term the auto-tagger writes cannot disagree.
from VISUALISABLE_SEARCH_TERMS import assemble_term        # noqa: E402


# =============================================================================
# THE DIALS
# =============================================================================

# How much a remembered thing is worth before recency is applied. A NAME is
# the most useful chip on the page ("Wadi Al-Hitan" is one stock search; "the
# valley" is a thousand), a date or a figure the least.
KIND_WEIGHT = {
    "name": 2.0,
    "thing": 1.5,
    "reference": 1.2,      # a pronoun that resolved to something real
    "number": 0.8,
    "date": 0.8,
}

# The setting is what the background is, so it stays useful for far longer
# than a prop does — it is still on screen.
SETTING_BONUS = 0.4

# Every re-mention after the first says the script has not finished with it.
REPEAT_BONUS = 0.3

# Recency: 1.0 for the line just gone, decaying with distance and never
# reaching zero, because early context still matters (a thing introduced in
# line 1 is often what the whole piece is about).
#     1 line back  1.00      5 back  0.42      20 back  0.14
RECENCY_HALF_LIFE = 4.0
RECENCY_FLOOR = 0.12

# A term the user actually typed on an earlier line beats anything we worked
# out ourselves — it is the one piece of ground truth on the page.
CONFIRMED_TERM_SCORE = 3.0

MAX_SINGLES = 8
MAX_PAIRS = 4

# Kinds that are NOT something to go looking for footage of.
#   fallback  the mercy rule fired — there is no picture in this line
#   deictic   "I" / "we" / "you": a person, but not one there is footage of
#   sound     a noise, not a thing
NOT_A_SEARCH_TERM = {"fallback", "deictic", "sound"}

# An unresolved reference still reads as the pronoun itself. Those are a
# "hold what is already on screen" cue, not a search term.
_PRONOUN_SURFACES = {
    "it", "its", "it's", "they", "them", "their", "theirs", "she", "her",
    "hers", "he", "him", "his", "this", "that", "these", "those", "one",
    "i", "we", "us", "our", "you", "your",
}


# =============================================================================
# THE ONE-CALL API FOR 3-manual-tagging
# =============================================================================

def suggestions_for_all_lines(all_lines: list[str],
                              confirmed_terms: dict[int, str] | None = None
                              ) -> list[dict]:
    """Payloads for EVERY line, in one pass over the script.

    @input all_lines = the split lines, in script order — exactly what
        0-sentence-splitter produced and the tagger is showing.
    @input confirmed_terms = {line index: the search term the user has
        already saved there}. Those outrank anything worked out here.

    @output one payload per line, same length and order as all_lines.

    Line i's answer only ever looks at lines 0..i, so a chip can never quote
    something the viewer has not heard yet.

    NEVER RAISES. A recommendation must not be able to take the tagging tool
    down; the worst case for a pathological script is empty chips.
    """
    lines = ["" if ln is None else str(ln) for ln in (all_lines or [])]
    if not lines:
        return []
    empty = [_empty_payload() for _ in lines]
    try:
        # The whole visualisables pipeline, once — the ~40 s of coreference
        # models included. myownstuff caches on the lines themselves, so the
        # tagger's recompute-on-every-save costs nothing while the split is
        # unchanged.
        per_line = myownstuff.get_visualisable_data_for_line_segments(lines)
    except Exception as exc:                           # pragma: no cover
        print(f"[suggestions] visualisables unavailable ({exc}) — "
              f"the chips fall back to the plain per-line words")
        return empty
    try:
        return _payloads(lines, per_line, confirmed_terms or {})
    except Exception as exc:                           # pragma: no cover
        print(f"[suggestions] could not build the chips ({exc})")
        return empty


def suggestions_for_line(all_lines: list[str], current_index: int,
                         confirmed_terms: dict[int, str] | None = None
                         ) -> dict:
    """One line's payload. Same answer suggestions_for_all_lines() gives for
    that index — it is the whole-script call underneath, because a line's
    memory is everything before it."""
    lines = ["" if ln is None else str(ln) for ln in (all_lines or [])]
    if not lines:
        return _empty_payload()
    i = max(0, min(int(current_index), len(lines) - 1))
    return suggestions_for_all_lines(lines, confirmed_terms)[i]


# =============================================================================
# internals
# =============================================================================

def _empty_payload() -> dict:
    return {"current": [], "singles": [], "pairs": [], "pronouns": []}


def _slots_of(line_data: dict) -> list[dict]:
    """The slot dicts of one line, left to right.

    myownstuff hands back {template: {slot number: {...}}} per line — one
    key, but written as a map, so this is the loop that unwraps it.
    """
    out = []
    for slots in (line_data or {}).values():
        for _slot, fields in sorted(slots.items(), key=lambda kv: _as_int(kv[0])):
            out.append(fields)
    return out


def _as_int(slot_key) -> int:
    try:
        return int(slot_key)
    except (TypeError, ValueError):
        return 0


def _is_search_term(fields: dict) -> bool:
    """Is this slot something you could go and find footage of?
    e.g. NO for "the narrator", NO for an "it" nothing resolved."""
    if fields.get("kind") in NOT_A_SEARCH_TERM:
        return False
    name = (fields.get("visualisable") or "").strip()
    return bool(name) and name.lower() not in _PRONOUN_SURFACES


def _recency(distance: int) -> float:
    """1.0 for the line just gone, decaying with distance, never 0.
    e.g. 1 -> 1.00   5 -> 0.42   20 -> 0.14"""
    return max(RECENCY_FLOOR, RECENCY_HALF_LIFE / (RECENCY_HALF_LIFE
                                                   + max(0, distance - 1)))


def _payloads(lines: list[str], per_line: list[dict],
              confirmed_terms: dict) -> list[dict]:
    """One pass down the script, carrying the memory forward as it goes."""
    # identity -> what we remember about it. `identity` is the extractor's
    # own answer to "the same thing under every name the script gives it",
    # so "The tractor" / "the tractor" / "it" share ONE entry.
    memory: dict[str, dict] = {}
    payloads = []

    for i, line in enumerate(lines):
        slots = _slots_of(per_line[i] if i < len(per_line) else {})
        showable = [f for f in slots if _is_search_term(f)]

        current = _dedupe(f["visualisable"] for f in showable)
        payloads.append({
            "current": current,
            "singles": _singles(memory, confirmed_terms, i, current),
            "pairs": _pairs(showable, current),
            "pronouns": _pronouns(slots),
        })
        _remember(memory, showable, i)

    return payloads


def _dedupe(names) -> list[str]:
    """Order-preserving, case-insensitive."""
    seen, out = set(), []
    for name in names:
        key = (name or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(name.strip())
    return out


def _remember(memory: dict, showable: list[dict], index: int) -> None:
    """Fold this line's things into the running memory."""
    for fields in showable:
        key = (fields.get("identity")
               or fields["visualisable"]).strip().lower()
        entry = memory.setdefault(key, {"term": fields["visualisable"],
                                        "kind": fields.get("kind", "thing"),
                                        "count": 0, "last_seen": index,
                                        "is_setting": False})
        entry["count"] += 1
        entry["last_seen"] = index
        entry["is_setting"] = entry["is_setting"] or bool(
            fields.get("is_setting"))
        # A later, fuller name for the same thing wins: "the valley" is a
        # better chip than the "it" that resolved to it.
        if fields.get("kind") != "reference":
            entry["term"] = fields["visualisable"]
            entry["kind"] = fields.get("kind", entry["kind"])


def _singles(memory: dict, confirmed_terms: dict, index: int,
             current: list[str]) -> list[dict]:
    """Things from EARLIER lines, best first — the "from memory" chips."""
    already = {c.lower() for c in current}
    rows = []

    # 1) what the user actually typed earlier. Ground truth, so it outranks
    #    everything we worked out for ourselves.
    for line_index, term in sorted(confirmed_terms.items()):
        term = (term or "").strip()
        if not term or line_index >= index or term.lower() in already:
            continue
        already.add(term.lower())
        rows.append({"term": term,
                     "score": round(CONFIRMED_TERM_SCORE
                                    * _recency(index - line_index), 3),
                     "why": f"you used this on line {line_index + 1}"})

    # 2) everything the extractor found before this line.
    for entry in memory.values():
        if entry["term"].lower() in already:
            continue
        distance = index - entry["last_seen"]
        if distance <= 0:
            continue
        score = KIND_WEIGHT.get(entry["kind"], 1.0) * _recency(distance)
        score += REPEAT_BONUS * (entry["count"] - 1)
        if entry["is_setting"]:
            score += SETTING_BONUS
        rows.append({"term": entry["term"], "score": round(score, 3),
                     "why": _why(entry, distance)})

    rows.sort(key=lambda r: -r["score"])
    return rows[:MAX_SINGLES]


def _why(entry: dict, distance: int) -> str:
    """e.g. "the setting · 2 lines back · mentioned 3x" """
    bits = []
    if entry["is_setting"]:
        bits.append("the setting")
    elif entry["kind"] == "name":
        bits.append("a name")
    bits.append("1 line back" if distance == 1 else f"{distance} lines back")
    if entry["count"] > 1:
        bits.append(f"mentioned {entry['count']}x")
    return " · ".join(bits)


def _pairs(showable: list[dict], current: list[str]) -> list[dict]:
    """Combos worth typing into a search box.

    Everything here is something the SCRIPT said about a thing in THIS line
    — how it looks by now, where it is standing, how many there are — so a
    pair is never a guess, only two facts put next to each other.
    """
    known = {c.lower() for c in current}
    rows = []

    def add(term: str, why: str) -> None:
        term = " ".join((term or "").split())
        if term and term.lower() not in known:
            known.add(term.lower())
            rows.append({"term": term, "why": why})

    for fields in showable:
        name = fields["visualisable"]
        if fields.get("variant"):
            # "tractor" + "yellow paint splat, broken window", capped and
            # ordered by the one assembler — see VISUALISABLE_SEARCH_TERMS.
            add(assemble_term(name, fields["variant"]), "how it looks by now")
        if fields.get("amount") and fields["amount"] > 1:
            add(f"{fields['amount']} {name}", "the script counted them")
        if fields.get("location"):
            add(f"{name} {fields['location']}",
                "in the setting named earlier")
        if fields.get("action"):
            add(f"{name} {fields['action']}", "what it is doing here")

    return rows[:MAX_PAIRS]


def _pronouns(slots: list[dict]) -> list[dict]:
    """This line's abstract words and what they turned out to point at.

    Straight off the resolver: `prob` is its own weighted vote, not a number
    invented here. A pronoun nothing resolved is left out — there is nothing
    to offer, and the extractor's answer for that case is "hold the picture
    you already have".
    """
    rows, seen_surface = [], {}
    for fields in slots:
        if fields.get("kind") not in ("reference", "deictic"):
            continue
        surface = (fields.get("surface") or "").strip()
        candidate = (fields.get("visualisable") or "").strip()
        if not surface or not candidate or candidate.lower() == surface.lower():
            continue
        seen_surface[surface.lower()] = seen_surface.get(surface.lower(), 0) + 1
        rows.append({"pronoun": surface,
                     "occurrence": seen_surface[surface.lower()],
                     "candidate": candidate,
                     "prob": float(fields.get("confidence") or 0.0)})
    return rows


if __name__ == "__main__":
    # uv run VISUALISABLE_SUGGESTIONS.py — what 3-manual-tagging will show.
    demo = ["In Egypt,", "there's a valley filled with", "whale skeletons.",
            "It was once covered", "by the Tethys Sea."]
    for line, payload in zip(demo, suggestions_for_all_lines(demo)):
        print(f"\n{line!r}")
        print(f"   current : {payload['current']}")
        for s in payload["singles"]:
            print(f"   memory  : {s['term']:<20} {s['score']:<6} {s['why']}")
        for p in payload["pairs"]:
            print(f"   combo   : {p['term']:<20}        {p['why']}")
        for r in payload["pronouns"]:
            print(f"   pronoun : '{r['pronoun']}' -> {r['candidate']} "
                  f"({r['prob']:.2f})")
