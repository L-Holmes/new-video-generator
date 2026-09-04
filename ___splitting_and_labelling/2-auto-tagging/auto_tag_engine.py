"""
###############################################################################
#   auto_tag_engine.py  —  THE MACHINERY BEHIND Auto_add_mediatypes.py        #
###############################################################################

This file exists so that Auto_add_mediatypes.py can be NOTHING BUT the two
step-by-step flows the boss edits:

    STEP 1   which attributes does this line have?         (the enum tagger)
    STEP 2   given those attributes, what goes on screen?  (the flowchart)

Everything mechanical lives here instead: walking the json, building a
LineContext per line, working out what the NEIGHBOURING lines are doing,
printing the attribute table, and actually writing a Decision onto a row
(short-scene gate, search_term auto-fill, chart data, group cells, never
overwriting a row that is already tagged).

Nothing in here decides ANYTHING about media types — it only carries out the
Decision the flowchart returns.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent
                        / "shared"))
    import PATHS  # noqa: F401  — every stage folder on sys.path

import shared_text_logic as stl
from MEDIA_TYPES import MEDIA_TYPES, MODIFIERS
from CONFIG import (  # noqa: E402  (MEDIA_TYPES puts the repo root on sys.path)
    MIN_DURATION_GATED_TYPES,
    data_fields_for,
    line_too_short_for_new_footage,
)

# A group's cells sit side by side in ONE layout, and the layout only has room
# for three. A 4th consecutive line is left empty for the human.
MAX_GROUP_CELLS = 3


# =============================================================================
# THE DECISION — what the flowchart hands back
# =============================================================================

@dataclass(frozen=True)
class Decision:
    """One outcome of the STEP 2 flowchart: what to put on screen."""
    attr: object                    # the attribute that drove this decision —
                                    # its evidence is what fills search_term
                                    # / the chart data
    media_type: str
    modifiers: tuple = ()
    search_term: str = ""           # set explicitly by the flowchart
    fill_search_term: bool = False  # or taken from what the detector matched
    fill_chart_data: bool = False   # write row["data"] from the detector
    needs_a_previous_line: bool = False


def tag(attr, media_type: str, modifiers=(), *, search_term: str = "",
        fill_search_term: bool = False, fill_chart_data: bool = False,
        needs_a_previous_line: bool = False) -> Decision:
    """Build a Decision — see decide()'s docstring in Auto_add_mediatypes.py
    for what each argument means, with examples. Checks the media type and
    the modifiers really exist, so a typo shows up here and not as a broken
    video three stages later."""
    assert media_type in MEDIA_TYPES, f"unknown media_type: {media_type!r}"
    for m in modifiers:
        assert m in MODIFIERS, f"unknown modifier: {m!r}"
    return Decision(attr, media_type, tuple(modifiers), search_term,
                    fill_search_term, fill_chart_data, needs_a_previous_line)


# =============================================================================
# THE NEIGHBOURS — what the flowchart can ask about the lines AROUND this one
# =============================================================================

@dataclass
class Neighbours:
    """Everything about the lines AROUND this one. The STEP 1 attributes say
    what THIS line contains; this says what its neighbours are doing — which
    is the only way to answer "is this a continuation?", "was the previous
    line already a chart?", "is this where that name was introduced?"."""
    index: int
    fragments: list
    data: dict                      # the rows, already mutated up to index-1
    attrs: dict                     # fragment → set of attributes
    line: object = None             # THIS line's LineContext

    is_the_first_line: bool = False
    previous_media_type: str = ""
    previous_modifiers: tuple = ()
    previous_search_term: str = ""

    def what_matched(self, attr) -> str:
        """The clean text the detector behind `attr` found on THIS line —
        the same text fill_search_term would use.
        --> what_matched(Attr.REFERS_BACK_TO_A_NAME) → 'Nero'"""
        if self.line is None:
            return ""
        return self.line.fills.get(attr.value, "")

    def line_above_had(self, attr) -> bool:
        """Did the line directly above have this attribute?
        --> the 2nd line of a list run: line_above_had(CONTAINS_NOUN_LIST)"""
        if self.index == 0:
            return False
        return attr in self.attrs.get(self.fragments[self.index - 1], set())

    def line_below_has(self, attr) -> bool:
        """Does the line directly below have this attribute? (Looking ahead —
        e.g. is this figure the first of several?)"""
        if self.index + 1 >= len(self.fragments):
            return False
        return attr in self.attrs.get(self.fragments[self.index + 1], set())

    def cells_in_the_group_so_far(self) -> int:
        """How many lines directly above this one are already cells of the
        same group — so we never overflow the 3-cell layout."""
        cells, j = 0, self.index - 1
        while j >= 0:
            row = self.data[self.fragments[j]]
            if "group" not in (row.get("modifiers") or []):
                break
            cells += 1
            j -= 1
        return cells

    def previous_line_introduced(self, name: str) -> bool:
        """Is the line right above the one that introduced this name, with a
        plain picture of it? Then holding it really does show them.
        --> "Nero was emperor." [wikipedia "Nero"] / "He burned Rome." → hold"""
        if not name or self.previous_media_type not in ("stock", "wikipedia",
                                                        "map"):
            return False
        return name.lower() in (self.previous_search_term or "").lower()


def _neighbours_for(index: int, fragments: list, data: dict, attrs: dict,
                    line=None) -> Neighbours:
    n = Neighbours(index, fragments, data, attrs, line,
                   is_the_first_line=(index == 0))
    if index > 0:
        previous = data[fragments[index - 1]]
        n.previous_media_type = previous.get("media_type") or ""
        n.previous_modifiers = tuple(previous.get("modifiers") or ())
        n.previous_search_term = previous.get("search_term") or ""
    return n


# =============================================================================
# STEP 1 PLUMBING — run the tagger's collect_attributes over every line
# =============================================================================

def detect_attributes(data: dict, collect_attributes, shared: dict = None):
    """Walk every line of the script and ask the tagger which attributes it
    has. Returns (attrs, lines):
        attrs[fragment] = the set of attributes that line has
        lines[fragment] = its LineContext (holding the evidence + fills)

    shared is the ONE dict every LineContext of this run sees, and is how
    facts worked out BEFORE the tagger reach the detectors. main.py puts
    stage 1's answers in as shared["visualisables"] — {line: LineFacts} —
    and collect_attributes reads them from there. Leave it out and every
    detector that consults it simply finds nothing, which is exactly what
    happens when the tagger is run standalone on a json.
    """
    fragments = list(data.keys())
    attrs, lines = {}, {}
    shared = dict(shared or {})
    for i, (fragment, row) in enumerate(data.items()):
        line = stl.LineContext(fragment, row, i, fragments, shared=shared)
        attrs[fragment] = collect_attributes(line)
        lines[fragment] = line
    return attrs, lines


def evidence_for(line, attr) -> str:
    """What the detector behind `attr` actually matched, for the printout."""
    return line.found.get(attr.value, "")


def search_term_for(line, attr) -> str:
    """The clean value the detector behind `attr` offers as a search term."""
    return line.fills.get(attr.value)


def chart_data_for(line, attr) -> dict:
    """The ready-made chart `data` dict the detector behind `attr` built."""
    return line.charts.get(attr.value)


def print_attribute_table(attrs: dict, lines: dict, attr_enum) -> None:
    """Per line: the rebuilt sentence, WHY the splitter cut here (in plain
    English), then every attribute True/False with what it matched."""
    for fragment, has in attrs.items():
        line = lines[fragment]
        print(f'\n"{fragment[:70]}"')
        sentence = line.full_sentence()
        if sentence and sentence != fragment:
            print(f'     sentence: "{sentence[:90]}"')
        for why in line.why_split_here():
            print(f"     splitter said → {why[:110]}")
        for attr in attr_enum:
            value = attr in has
            found = evidence_for(line, attr)
            extra = f"   → {found}" if value and found else ""
            print(f"     {attr.name} = {value}{extra}")


# =============================================================================
# STEP 2 PLUMBING — carry out the flowchart's Decision
# =============================================================================

def apply_flowchart(data: dict, attrs: dict, lines: dict, decide) -> list[str]:
    """Run the flowchart over every line and write the result onto its row.
    ONLY rows whose media_type is EMPTY are ever touched."""
    changed = []
    fragments = list(data.keys())
    for i, (fragment, row) in enumerate(data.items()):
        if row.get("media_type") not in ("", None):
            continue                       # already tagged — NEVER overwrite

        line = lines[fragment]
        neighbours = _neighbours_for(i, fragments, data, attrs, line)
        decision = decide(attrs[fragment], neighbours)
        if decision is None:
            continue                       # nothing fired — leave it for manual

        if decision.needs_a_previous_line and neighbours.is_the_first_line:
            continue                       # nothing before it — leave it

        # a group cell can only exist while the layout has room for it
        if "group" in decision.modifiers \
                and neighbours.cells_in_the_group_so_far() >= MAX_GROUP_CELLS:
            print(f'  · "{fragment[:50]}"  the group is full '
                  f'({MAX_GROUP_CELLS} cells) — left empty')
            continue

        fill = decision.search_term or search_term_for(line, decision.attr)

        # SHORT-SCENE INTELLIGENCE (see MIN_NEW_FOOTAGE_SECONDS in CONFIG):
        # brand-new footage on a line too short to stand on its own would just
        # flash. Prefer editing the previous image instead — hold_previous +
        # decorate, stamping the thing onto it when we have a term ("add
        # decorated stock", the common case). Joining neighbouring lines is
        # left to the human in MANUAL_TAGGING (too destructive to do blind).
        # A group cell is exempt: its whole point is that the cells are quick.
        if (decision.media_type in MIN_DURATION_GATED_TYPES
                and "group" not in decision.modifiers
                and not neighbours.is_the_first_line
                and line_too_short_for_new_footage(fragment)):
            row["media_type"] = "hold_previous"
            if "decorate" not in (row.get("modifiers") or []):
                row["modifiers"] = (row.get("modifiers") or []) + ["decorate"]
            filled = ""
            if fill and not (row.get("search_term") or "").strip():
                row["search_term"] = fill
                row["stamp_source"] = "stock"     # stamp it onto the hold
                filled = (f'   search_term="{fill[:40]}"'
                          f'   stamp_source="stock"')
            changed.append(fragment)
            print(f'  → "{fragment[:50]}"  =  hold_previous[\'decorate\']'
                  f'{filled}   (too short for new {decision.media_type} — '
                  f'edit + add to the previous scene instead)')
            continue

        row["media_type"] = decision.media_type
        new_mods = [m for m in decision.modifiers
                    if m not in (row.get("modifiers") or [])]
        row["modifiers"] = (row.get("modifiers") or []) + new_mods

        filled = ""
        if (decision.fill_search_term or decision.search_term) and fill \
                and not (row.get("search_term") or "").strip():
            row["search_term"] = fill
            filled = f'   search_term="{fill[:40]}"'

        if decision.fill_chart_data:
            chart = chart_data_for(line, decision.attr) or {}
            keep = {f.name for f in data_fields_for(decision.media_type)}
            row["data"] = {k: v for k, v in chart.items() if k in keep}
            filled += f"   data={row['data']}"

        changed.append(fragment)
        found = evidence_for(line, decision.attr)
        print(f'  → "{fragment[:50]}"  =  {decision.media_type}'
              f'{list(decision.modifiers) or ""}'
              f'{f"   [{found}]" if found else ""}{filled}')

    _derive_group_ids(data)
    return changed


def _derive_group_ids(data: dict) -> None:
    """Let the tagging tool itself derive group_id + position from the
    opener/continuation shape we just wrote, so the json we hand over is
    spelled exactly the way MANUAL_TAGGING and the renderer expect. Never
    let this break tagging — a missing tool just means the ids get derived
    when the file is opened instead."""
    try:
        from MANUAL_TAGGING import recompute
        recompute(data)
    except Exception as exc:  # pragma: no cover
        print(f"  · (group ids will be derived when the file is opened: {exc})")


# =============================================================================
# SANITY CHECKS — run at startup so mistakes surface immediately
# =============================================================================

# Media types the auto-tagger deliberately NEVER picks, and the reason. The
# coverage check below asserts this list plus what the flowchart can reach
# covers the WHOLE catalog — so "can we theoretically fill every row?" has a
# real answer rather than a shrug.
NEVER_AUTO_ASSIGNED = {
    "ai_stock": "never use an AI one by default — you pick these by hand",
    "ai_edit_previous": "never use an AI one by default",
    "stock_on_board": "the stickman board is an AI/style choice, not automatic",
    "wikipedia_on_board": "the stickman board is an AI/style choice",
    "background": "ignore — that is for background-video mode, logic comes later",
    "typography": "manual-only for now (swap the cold-open branch on to enable)",
    "blank": "manual-only for now — a deliberate breath is a human call",
    "random_background": "manual-only for now — a last-resort filler",
}


def reachable_media_types(attr_enum, decide) -> set:
    """Every media type the flowchart can actually produce, found by running
    it over each attribute alone and over the pairs that share a branch."""
    reachable = set()

    class _AnyNeighbours(Neighbours):
        """Answers 'yes' to everything, so branches guarded by a neighbour
        question are still reachable when we probe them."""

        def what_matched(self, attr):
            return "probe"

        def line_above_had(self, attr):
            return True

        def line_below_has(self, attr):
            return True

        def cells_in_the_group_so_far(self):
            return 0

        def previous_line_introduced(self, name):
            return True

    for say_yes in (True, False):
        probe = (_AnyNeighbours(0, [""], {"": {}}, {})
                 if say_yes else Neighbours(0, [""], {"": {}}, {}))
        probe.previous_media_type = "counter" if say_yes else ""
        for attr in attr_enum:
            for combo in ({attr}, set(attr_enum)):
                decision = decide(combo, probe)
                if decision is not None:
                    reachable.add(decision.media_type)
    return reachable


def check_flowchart(attr_enum, decide) -> None:
    """Every attribute's VALUE must name a real detector in
    shared_text_logic, and every branch the flowchart can reach must return a
    real media type (tag() asserts that as it builds each Decision)."""
    for attr in attr_enum:
        detector = getattr(stl, attr.value, None)
        assert callable(detector), (
            f"{attr.name} says its detector is shared_text_logic."
            f"{attr.value}(), but there is no such function")
    reachable_media_types(attr_enum, decide)


def coverage_report(attr_enum, decide) -> str:
    """CAN WE THEORETICALLY FILL EVERY ROW? Lists every media type in the
    catalog as either something the flowchart can reach, or something we
    deliberately never auto-assign (with the reason)."""
    reachable = reachable_media_types(attr_enum, decide)
    lines = ["", "MEDIA TYPE COVERAGE — every type in the catalog:"]
    unaccounted = []
    for name in MEDIA_TYPES:
        if name in reachable:
            lines.append(f"   auto ✓   {name}")
        elif name in NEVER_AUTO_ASSIGNED:
            lines.append(f"   manual   {name:<20} ({NEVER_AUTO_ASSIGNED[name]})")
        else:
            lines.append(f"   MISSING  {name}   ← nothing can produce this")
            unaccounted.append(name)
    assert not unaccounted, (
        f"these media types are neither reachable nor deliberately excluded: "
        f"{unaccounted} — add a branch in decide(), or a reason in "
        f"NEVER_AUTO_ASSIGNED")
    return "\n".join(lines)


# =============================================================================
# THE COMMAND LINE
# =============================================================================

def main_cli(argv, attr_enum, collect_attributes, decide, usage: str) -> int:
    """`Auto_add_mediatypes.py <script_to_search_term.json> [--dry-run]`."""
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    if len(argv) != 1:
        print(usage)
        return 2
    path = Path(argv[0])
    data = json.loads(path.read_text(encoding="utf-8"))

    check_flowchart(attr_enum, decide)
    stl.booster_report("[auto-tag]")

    print("=" * 70)
    print("STEP 1 — DETECTION: what does each line contain? "
          "(False when in doubt)")
    print("=" * 70)
    attrs, lines = detect_attributes(data, collect_attributes)
    print_attribute_table(attrs, lines, attr_enum)

    print("\n" + "=" * 70)
    print("STEP 2 — ASSIGNMENT: the flowchart, first match wins; EMPTY rows only")
    print("=" * 70)
    changed = apply_flowchart(data, attrs, lines, decide)
    untouched = sum(1 for f in data if f not in changed)
    print(f"\nassigned {len(changed)} line(s); {untouched} left for "
          f"MANUAL_TAGGING (nothing fired, or already tagged — that's fine).")
    print(coverage_report(attr_enum, decide))

    if dry_run:
        print("[dry-run] nothing written.")
    elif changed:
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        print(f"written → {path}   (backup: {backup.name})")
    else:
        print("nothing to write.")
    return 0
