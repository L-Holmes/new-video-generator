"""
MANUAL_TAGGING.py  —  step 2 of 2: hand-set every line's media type and
search term in a point-and-click page (localhost, zero dependencies).

    uv run MANUAL_TAGGING.py [path/to/output.json]

(no argument: newest *-script_to_search_term.json here; a browser tab opens)

What's on the page
------------------
  - the whole script scrollable on the left (that IS the context). one dot
    per row: gold when the required steps are done (media type + term —
    the term is OPTIONAL for hold_previous/background/blank/
    random_background; hold_previous + decorate + a term additionally
    needs step 3's stamp source).
    grouped lines share a coloured stripe. no badge until a type is picked.
  - SPLIT, inline on the left: hover a line's text — it expands to full
    length, a golden cursor snaps to the nearest gap between letters with a
    "click to break here" popup (never at the very start or end); click to
    break into two entries that inherit everything. a toolbar toggle
    switches this to "hover the ✂ at the row's end instead" if hovering
    the text gets in your way.
  - JOIN: hover a row's end for the shimmering "join to above" button —
    the row above previews the appended text as ghost text and the current
    row is struck through. on phones it's a two-step tap + confirm.
  - STEP 1 (blue card): pick ONE media type, grouped NEW / EDIT PREVIOUS,
    ai family in reds, unavailable options flat grey. stack decorate /
    caption / group on top — disabled until a base exists. (i) icons show
    an info card (hover on desktop, tap on phones) docked out of the way
    top-right; the key scrolls through the same cards.
  - GROUPS (rule of n): open one with stock / ai stock + group, then continue
    it on each following line with hold previous + group — the group picture
    stays up and one more cell lands on it. step onto a blank line below a
    cell and it is tagged that way for you. every cell needs its OWN search
    term (it is its own picture). group_id + position are derived from that
    pattern on every save, never assigned by hand.
  - STEP 2 (gold card, always pinned on screen at the bottom of the panel):
    the search term. grey ghost suggestion appears when the box is focused
    — tab to accept — plus big tap-to-append noun/place chips. once both
    steps are done a "continue to next" button appears right there.
  - WHY? (bottom-right): every action — a split, a join, the first media
    type a line ever gets as much as a re-tag, a search-term edit, decorate,
    a data form, a stamp pick — offers to record why you did it, named by
    the line index and the action ("Line 12 — search term changed"). Offers
    queue, so carrying on working never loses the chance to explain an
    earlier one; answer them one at a time, or skip. NOTHING reaches
    manual_tagging_changes_report.txt unless you actually give a reason.
  - a FINISH button that pulses green when every line is done.
  - phones: no index numbers, per-row join buttons, a ✂ split tool (tap
    the tool, tap a line, tap where — nudge arrows move the point one
    character, then "split at the selected point"), the line being tagged
    shown big in a gold box, and a proper "done — back to list" button.

Every change saves to the JSON instantly (one .bak per session).

(no term-prediction library is bundled: nothing off the shelf hits the
"works very well" bar for narration-to-search-term — the ghost text covers
the quick case and prompts/ covers the quality case.)
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional

from MEDIA_TYPES import (COLLAGEABLE_TYPES, GROUPABLE_TYPES, MEDIA_TYPES,
                         MODIFIERS, Tag)

try:    # new names — re-export them from MEDIA_TYPES.py (one line) to keep
        # this in sync with CONFIG; the fallback mirrors CONFIG's defaults.
    from MEDIA_TYPES import (GROUP_CONTINUATION_TYPE, JOINT_GROUP_CELLS,
                             MEDIA_TYPE_TABS, STAMP_SOURCE_TYPES,
                             TERM_OPTIONAL_TYPES, coerce_scene_data,
                             data_fields_for)
except ImportError:
    STAMP_SOURCE_TYPES = ("stock", "wikipedia", "ai_stock")
    TERM_OPTIONAL_TYPES = ("hold_previous", "background",
                           "blank", "random_background", "timeline",
                           "counter", "progress_bar", "bar_chart",
                           "pie_chart", "line_graph")
    JOINT_GROUP_CELLS = {"stock": 3, "ai_stock": 3}
    GROUP_CONTINUATION_TYPE = "hold_previous"
    MEDIA_TYPE_TABS = [{"name": "material", "label": "material", "columns": [
                            (Tag.NEW, "NEW — brand-new material"),
                            (Tag.EDIT_PREVIOUS,
                             "EDIT PREVIOUS — act on what is on screen")]},
                       {"name": "maths", "label": "maths",
                        "columns": [(Tag.MATHS, "NEW — MATHS")]}]

    def data_fields_for(name):
        return ()

    def coerce_scene_data(name, raw, script_text, *, require_all=True):
        return dict(raw or {})

# Brand-new-footage minimum-duration guard (task 11). MEDIA_TYPES already put
# the repo root on sys.path, so the shared config resolves; fall back to safe
# defaults if it somehow can't (keeps the tagger usable standalone).
try:
    from CONFIG import (MIN_DURATION_GATED_TYPES, min_words_for_new_footage,
                        words_needed_for_new_footage)
    _MIN_NEW_WORDS = min_words_for_new_footage()
except Exception:
    MIN_DURATION_GATED_TYPES = {
        "stock", "ai_stock", "wikipedia", "map",
        "stock_on_board", "wikipedia_on_board"}
    _MIN_NEW_WORDS = 3

    def words_needed_for_new_footage(text: str) -> int:
        return max(0, _MIN_NEW_WORDS - len(re.findall(r"[\w']+", text or "")))


# Keyword recommendation engine (memory + "jar of nutmeg" pairing + the
# pronoun panel). Optional in the same spirit as the CONFIG import above:
# if VISUAL_RECOMMENDER.py is missing the tagger still runs, the chips
# simply fall back to the plain per-line suggestions.
try:
    from VISUAL_RECOMMENDER import suggestions_for_all_lines
    _HAS_RECOMMENDER = True
except Exception:
    _HAS_RECOMMENDER = False

    def suggestions_for_all_lines(lines, confirmed_terms=None):
        return [{"current": [], "singles": [], "pairs": [], "pronouns": []}
                for _ in (lines or [])]


# ---- recommendation timing estimates ---------------------------------------
# Past full-recompute timings, persisted so the loading screen can show an
# honest countdown: recommender_timings.json = [{"words": N, "seconds": S}].
_TIMINGS_PATH = Path(__file__).resolve().parent / "recommender_timings.json"
_DEFAULT_BASE_S = 8.0          # cold start (WordNet corpus load etc.)
_DEFAULT_PER_WORD_S = 0.015


def _load_timings() -> list:
    try:
        return json.loads(_TIMINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _record_timing(words: int, seconds: float) -> None:
    try:
        rows = _load_timings()
        rows.append({"words": int(words), "seconds": round(seconds, 2)})
        _TIMINGS_PATH.write_text(json.dumps(rows[-20:]), encoding="utf-8")
    except Exception:                              # pragma: no cover
        pass


def _estimate_seconds(words: int) -> float:
    rows = [r for r in _load_timings() if r.get("words")]
    if not rows:
        return _DEFAULT_BASE_S + _DEFAULT_PER_WORD_S * words
    rates = sorted(r["seconds"] / max(1, r["words"]) for r in rows)
    rate = rates[len(rates) // 2]                  # median s/word
    return max(1.0, min(300.0, rate * words + 0.5))


def _word_count(text: str) -> int:
    return len(re.findall(r"[\w']+", text or ""))

HERE = Path(__file__).resolve().parent
# every EXPLAINED change (split, join, media type, extras, search term, data
# form, stamp) lands here, whichever json is being tagged — one shared,
# append-only, human-readable report. No reason given, nothing written.
REPORT_PATH = HERE.parent / "manual_tagging_changes_report.txt"
# how many un-explained changes keep their "why?" offer alive at once. The
# overlay works through them newest-first; beyond this the oldest are dropped
# (never written), because an offer from 40 changes ago is noise, not a prompt.
_MAX_PENDING = 40
_PLACE_LABELS = {"GPE", "LOC"}
_NAME_LABELS = {"PERSON", "ORG", "FAC", "EVENT", "WORK_OF_ART", "NORP"}


# =============================================================================
# pure helpers (unit-testable)
# =============================================================================

def build_catalog() -> dict:
    bases = [{"name": n, "label": n.replace("_", " "),
              "color": d["color"], "tags": [t.value for t in d["tags"]],
              # groupable = can OPEN a group. group_continues = can be one of
              # the cells that JOIN the group above (hold previous + group).
              "groupable": n in GROUPABLE_TYPES,
              "group_continues": n == GROUP_CONTINUATION_TYPE,
              "group_cells": JOINT_GROUP_CELLS.get(n, 0),
              "collageable": n in COLLAGEABLE_TYPES,
              "term_optional": n in TERM_OPTIONAL_TYPES,
              "stampable": n in STAMP_SOURCE_TYPES,
              "new_footage": n in MIN_DURATION_GATED_TYPES,
              # structured input this type needs instead of (or as well as) a
              # search term — the tagger builds its form straight from this
              "data_fields": [{"name": f.name, "label": f.label,
                               "kind": f.kind, "help": f.help,
                               "placeholder": f.placeholder,
                               "required": f.required}
                              for f in data_fields_for(n)],
              "info": d["info"], "example": d["example"]}
             for n, d in MEDIA_TYPES.items()]
    mods = [{"name": n, "label": n, "color": d["color"],
             "info": d["info"], "example": d["example"]}
            for n, d in MODIFIERS.items()]
    # the media-type picker's tabs (material / maths / …), straight from CONFIG
    tabs = [{"name": t["name"], "label": t["label"],
             "columns": [{"tag": tag.value, "title": title}
                         for tag, title in t["columns"]]}
            for t in MEDIA_TYPE_TABS]
    return {"bases": bases, "modifiers": mods, "tabs": tabs,
            "group_continuation_type": GROUP_CONTINUATION_TYPE}


def _suggest_from_meta(meta: dict, line: str) -> dict:
    ents = meta.get("ents", [])
    nouns = list(dict.fromkeys(meta.get("nouns", [])))
    keywords = [k for k in meta.get("keywords", []) if k not in nouns]
    places = [e["text"] for e in ents if e["label"] in _PLACE_LABELS]
    names = [e["text"] for e in ents if e["label"] in _NAME_LABELS]
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-']+", line)
             if len(w) > 3 and w.lower() not in nouns][:8]
    ghost = (places[0] if places
             else " ".join(nouns[:2]) if nouns else "")
    return {"nouns": nouns, "places": places, "names": names,
            "keywords": keywords[:8], "words": words, "ghost": ghost}


def recompute(data: Dict[str, dict]) -> None:
    """Derive every group's id + cell positions from the rows themselves, and
    purge derived/legacy columns. Called after every mutation so the file is
    always consistent.

    A group is an OPENER (a groupable base + group) followed by its
    CONTINUATION cells (hold previous + group) — the same shape
    CONFIG.resolve_group_continuations reads back at render time. group_id is
    derived here rather than assigned when the modifier is toggled, so a split
    or a join can never leave the ids describing a layout that no longer
    exists.

    A continuation with no opener above it (a join tore the two apart) is not a
    cell of anything: it silently loses the modifier rather than being written
    to a json the renderer would refuse."""
    # drop split-provenance that no longer describes the very next line
    # (the halves were separated or the right half changed), so a stale
    # no-space rejoin can never fire.
    keys = list(data)
    for idx, key in enumerate(keys):
        prov = data[key].get("_split_before")
        nxt = keys[idx + 1] if idx + 1 < len(keys) else None
        if prov and prov.get("text") != nxt:
            data[key].pop("_split_before", None)

    next_gid, gid, pos = 0, None, 0
    for key, row in data.items():
        row.pop("search_type", None)   # legacy column — purged on every save
        # typography renders the line's OWN words — the term is never
        # freeform, it's always exactly this line's text (kept in sync
        # through splits/joins/base switches, not just set once on pick).
        if row.get("media_type") == "typography":
            row["search_term"] = key
        mods = row.get("modifiers") or []
        base = row.get("media_type") or ""
        # `data` belongs to the BASE. Switching away from timeline must not
        # leave its year behind for the loader to trip over.
        keep = {f.name for f in data_fields_for(base)}
        row["data"] = {k: v for k, v in (row.get("data") or {}).items()
                       if k in keep}
        if "group" not in mods:
            gid, pos = None, 0
        elif base != GROUP_CONTINUATION_TYPE:
            next_gid += 1
            gid, pos = next_gid, 1     # a real base always OPENS a new group
        elif gid is not None:
            pos += 1                   # hold previous + group joins it
        else:
            row["modifiers"] = [m for m in mods if m != "group"]
            gid, pos = None, 0         # orphan cell — nothing to continue
        row["group_id"] = gid
        row["position"] = str(pos if pos else 1)


def migrate_legacy_groups(data: Dict[str, dict]) -> int:
    """Rewrite groups written the OLD way into the opener/continuation spelling,
    returning how many cells were converted.

    A group used to be N consecutive lines carrying the SAME base + group and a
    shared group_id. It is now an opener plus `hold previous` + group cells. The
    two are indistinguishable to recompute() — which would read the old shape as
    N separate one-cell groups and quietly shatter a group the user had already
    built — so an old run is converted the moment its file is opened. The
    renderer reads both shapes (CONFIG.group_scene_rows), so a file that is
    never re-saved keeps rendering exactly as before."""
    converted = 0
    opener: "dict | None" = None
    opener_base = ""
    for row in data.values():
        if "group" not in (row.get("modifiers") or []):
            opener, opener_base = None, ""
            continue
        base = row.get("media_type") or ""
        if base == GROUP_CONTINUATION_TYPE:
            continue                   # already a cell of the run above
        # Compare against the run's OPENER, not the previous row — by now that
        # row may itself have been re-spelled to the continuation type.
        if (opener is not None and base == opener_base
                and row.get("group_id") is not None
                and row.get("group_id") == opener.get("group_id")):
            row["media_type"] = GROUP_CONTINUATION_TYPE
            converted += 1
            continue
        opener, opener_base = row, base    # this row really opens a group
    return converted


def group_run(data: Dict[str, dict], line: str, base: str) -> "tuple[int, str]":
    """The group `line` would sit in if it carried `base` + group, as
    (cell_count, opening_base).

    A group is an OPENER (a groupable base + group) followed by its
    CONTINUATION cells (hold previous + group), which is exactly the run the
    compositor renders as one composite (CONFIG.group_scene_rows). Walk up to
    the opener, then down over its continuations. `opening_base` is "" when
    `line` is not in a group at all, or when it is a continuation with no
    opener above it."""
    lines = list(data)
    i = lines.index(line)

    def cell(j: "int") -> "tuple[bool, str]":
        """(carries group, base) for row j — with `line` seen as `base`."""
        if j == i:
            return True, base
        row = data[lines[j]]
        return ("group" in (row.get("modifiers") or []),
                row.get("media_type") or "")

    start = i                              # walk up to the run's opener
    while cell(start)[1] == GROUP_CONTINUATION_TYPE:
        if start == 0 or not cell(start - 1)[0]:
            return 1, ""                   # nothing above it to continue
        start -= 1
    opening_base = cell(start)[1]
    size, j = 1, start + 1                 # then down over its continuations
    while j < len(lines) and cell(j)[0] \
            and cell(j)[1] == GROUP_CONTINUATION_TYPE:
        size += 1
        j += 1
    return size, opening_base


def split_line(data: Dict[str, dict], line: str, index: int) -> Optional[str]:
    """Split one entry at a character index. Both halves keep the media
    type, modifiers, term and rule ids. We remember that the left half was
    immediately followed by the right half (`_split_before`) so that if you
    re-join them we can rebuild the original text WITHOUT inserting a space
    (the split point may have been mid-word). Returns an error or None."""
    if line not in data:
        return "unknown line"
    a, b = line[:index].strip(), line[index:].strip()
    if not a or not b:
        return "cannot break at the very start or end"
    if a in data or b in data:
        return "that split would duplicate an existing line"
    # did we cut at whitespace, or mid-token?  (drives space-free rejoin)
    at_space = bool(re.match(r"\s", line[index - 1:index])) or \
        bool(re.match(r"\s", line[index:index + 1]))
    out = {}
    for key, row in data.items():
        if key == line:
            left = copy.deepcopy(row)
            right = copy.deepcopy(row)
            left["_split_before"] = {"text": b, "glue": " " if at_space else ""}
            out[a] = left
            out[b] = right
        else:
            out[key] = row
    data.clear()
    data.update(out)
    return None


def join_to_above(data: Dict[str, dict], line: str) -> Optional[str]:
    """Merge an entry into the one above it. The merged entry keeps the
    ABOVE line's media type / modifiers / term; rule ids are combined. If
    the above line was split FROM this exact line, we rejoin with the glue
    recorded at split time (no phantom space); otherwise a single space."""
    lines = list(data)
    if line not in data:
        return "unknown line"
    i = lines.index(line)
    if i == 0:
        return "the first line has nothing above it"
    above = lines[i - 1]
    prov = data[above].get("_split_before")
    if prov and prov.get("text") == line:
        merged_text = f"{above}{prov['glue']}{line}"
    else:
        merged_text = f"{above} {line}"
    if merged_text in data and merged_text not in (above, line):
        return "that join would duplicate an existing line"
    merged = copy.deepcopy(data[above])
    merged.pop("_split_before", None)
    ids = list(dict.fromkeys(list(data[above].get("rule_ids", []))
                             + list(data[line].get("rule_ids", []))))
    merged["rule_ids"] = ids
    out = {}
    for key, row in data.items():
        if key == above:
            out[merged_text] = merged
        elif key == line:
            continue
        else:
            out[key] = row
    data.clear()
    data.update(out)
    return None


def apply_patch(data: Dict[str, dict], line: str, patch: dict) -> Optional[str]:
    if line not in data:
        return "unknown line"
    row = data[line]
    mods = list(patch.get("modifiers", row.get("modifiers", [])) or [])
    base = patch.get("media_type", row.get("media_type", ""))
    # a modifier this base doesn't support (leftover from before a base
    # switch, say) is dropped silently — no warning, it simply isn't an
    # option for this type any more, same as the modifier bar just not
    # rendering a button for it.
    orig_mods = mods
    if "group" in mods and base not in GROUPABLE_TYPES \
            and base != GROUP_CONTINUATION_TYPE:
        mods = [m for m in mods if m != "group"]
    if "collage" in mods and base not in COLLAGEABLE_TYPES:
        mods = [m for m in mods if m != "collage"]
    # group and collage are mutually exclusive — picking one drops the other
    # (group = one cell per LINE across neighbours; collage = many images on
    # THIS line). Last toggled wins.
    if {"group", "collage"} <= set(mods) and "modifiers" in patch:
        prev = set(row.get("modifiers", []))
        newly = set(mods) - prev
        drop = "collage" if "group" in newly else "group"
        mods = [m for m in mods if m != drop]
    if mods != orig_mods:
        patch = dict(patch, modifiers=mods)
    # Only checked when 'group' is newly gained (or the base changed under
    # it) — never on a plain term save.
    gained_group = "group" in mods and (
        "group" not in (row.get("modifiers") or [])
        or base != row.get("media_type")
    )
    if gained_group:
        would_be, opening_base = group_run(data, line, base)
        # `hold previous` + group means "add my cell to the group above". With
        # no group above, there is nothing to add it to.
        if not opening_base:
            return ("'hold previous' + group continues the group above — but "
                    "no group is open above this line. Open one first: give "
                    "the line that starts it a base of "
                    + " or ".join(sorted(GROUPABLE_TYPES)).replace("_", " ")
                    + " and stack group on that.")
        # A group can only hold as many lines as its layout has cells; a
        # fourth would make generate_joint_scenes hard-exit mid-render. Refuse
        # it here, while there is still a person to tell.
        cells = JOINT_GROUP_CELLS.get(opening_base, 0)
        if would_be > cells:
            return (f"a group of {opening_base.replace('_', ' ')} holds "
                    f"{cells} lines — this would make {would_be}. Start a new "
                    f"group further down, or ungroup one of the neighbours.")
    if "stamp_source" in patch and patch["stamp_source"] is not None \
            and patch["stamp_source"] not in STAMP_SOURCE_TYPES:
        return ("stamp source must be one of "
                + ", ".join(STAMP_SOURCE_TYPES).replace("_", " "))
    # `data` is checked against the BASE's DataFields — the same table the
    # loader validates against, so a value the tagger accepts always loads.
    # require_all is off: the form is half-filled while it is being typed into,
    # and `lineDone` (not this) is what refuses to let you finish on a gap.
    if "data" in patch:
        try:
            patch = dict(patch,
                         data=coerce_scene_data(base, patch["data"], line,
                                                require_all=False))
        except ValueError as exc:
            return str(exc)
    for key in {"media_type", "modifiers", "search_term", "data",
                "stamp_source", "stamp_decorate"} & set(patch):
        row[key] = patch[key]
    row["stamp_decorate"] = bool(row.get("stamp_decorate", False))
    # stamp settings only mean something on a TERM_OPTIONAL_TYPES line +
    # decorate + a non-empty term — clear them the moment the combo breaks
    # so a stale choice can never linger in the json. A group cell is never
    # one of those: its term is its OWN tile, not something to stamp.
    if (row.get("media_type") not in TERM_OPTIONAL_TYPES
            or "group" in (row.get("modifiers") or [])
            or "decorate" not in (row.get("modifiers") or [])
            or not (row.get("search_term") or "").strip()):
        row["stamp_source"] = None
        row["stamp_decorate"] = False
    # group_id + position are DERIVED from the opener/continuation pattern —
    # recompute() (which _commit always runs) is the one place that sets them.
    return None


# =============================================================================
# too-short-for-new-footage resolution (task 11)
# =============================================================================
# When a line too short to stand on its own is given a BRAND-NEW footage type
# (stock / wikipedia / map / ...), the tagger offers ways to fix it instead of
# letting the clip just flash. Each option is ONE atomic mutation (so it's one
# undo away). The heavy lifting reuses split_line / join_to_above / apply_patch.

def _borrow_words_from_previous(data: Dict[str, dict], line: str
                                ) -> "tuple[Optional[str], Optional[str]]":
    """DEPRECATED — no longer called. Option (3) used to auto-guess a split
    point and move words; that removed the user's control over WHERE the
    previous scene is cut. It is now a manual handoff (the frontend arms
    split mode on the previous line), so this helper is retained only for
    reference / any external callers and is not wired into resolve_short_scene.
    """
    lines = list(data)
    if line not in data:
        return "unknown line", None
    i = lines.index(line)
    if i == 0:
        return "the first line has nothing before it", None
    above = lines[i - 1]
    above_words = above.split()
    need = words_needed_for_new_footage(line)
    if need <= 0:
        return None, line                      # already long enough
    if len(above_words) - need < 1:
        return ("the previous scene is too short to spare "
                f"{need} word(s)"), None
    moved = above_words[-need:]
    new_above = " ".join(above_words[:-need]).strip()
    new_line = (" ".join(moved) + " " + line).strip()
    if not new_above:
        return "that would empty the previous scene", None
    if new_above in data or new_line in data:
        return "that move would duplicate an existing line", None
    above_row = copy.deepcopy(data[above])
    above_row.pop("_split_before", None)       # the split point moved
    line_row = copy.deepcopy(data[line])
    line_row.pop("_split_before", None)
    line_row["rule_ids"] = list(dict.fromkeys(
        list(data[above].get("rule_ids", [])) + list(line_row.get("rule_ids", []))))
    out = {}
    for key, row in data.items():
        if key == above:
            out[new_above] = above_row
        elif key == line:
            out[new_line] = line_row
        else:
            out[key] = row
    data.clear()
    data.update(out)
    return None, new_line


def resolve_short_scene(data: Dict[str, dict], line: str, choice: str,
                        media_type: str, modifiers: list) -> Optional[str]:
    """Apply the chosen fix for a too-short brand-new-footage line. `choice`
    is one of edit_prev / join_prev / borrow_prev / next_edit / join_next /
    group_start / override; media_type + modifiers are the type the user was
    TRYING to pick (re-applied for the choices that keep this line as its own
    scene)."""
    if line not in data:
        return "unknown line"
    lines = list(data)
    i = lines.index(line)
    nxt = lines[i + 1] if i + 1 < len(lines) else None
    pend = {"media_type": media_type, "modifiers": list(modifiers or [])}

    if choice == "edit_prev":                  # (1) hold + decorate the prev image
        if i == 0:
            return "there is no previous scene to edit"
        mods = list(dict.fromkeys(list(modifiers or []) + ["decorate"]))
        return apply_patch(data, line,
                           {"media_type": "hold_previous", "modifiers": mods})

    if choice == "join_prev":                  # (2) merge this INTO the previous
        return join_to_above(data, line)

    if choice == "borrow_prev":                # (3) MANUAL: user splits the
        if i == 0:                             #     previous scene themselves
            return "there is no previous scene to split"
        # no mutation here — the frontend navigates to the previous line
        # and arms split mode so the USER chooses the cut point, then joins
        # the tail onto this scene. (See the "manual" branch in do_POST.)
        return None

    if choice == "next_edit":                  # (4) make the NEXT scene hold this
        if nxt is None:
            return "there is no scene after this one"
        err = apply_patch(data, line, pend)    # this line keeps the new footage
        if err:
            return err
        return apply_patch(data, nxt, {"media_type": "hold_previous"})

    if choice == "join_next":                  # (5) MANUAL: user splits the
        if nxt is None:                        #     next scene, joins part here
            return "there is no scene after this one"
        # no mutation — frontend navigates to the next line and arms split
        # so the user picks how much to bring back. (manual branch, do_POST.)
        return None

    if choice == "group_start":                # (6) this line opens a group
        # A short line is only a problem ALONE: as one cell of a group the
        # composite it belongs to stays on screen for the whole run, so
        # nothing flashes. The line keeps the base it was given and gains
        # `group`, which OPENS the group. The lines that fill the other cells
        # join it as `hold_previous` + group as the user walks down the script
        # (the frontend's autoJoinGroup tags each blank line for them).
        if media_type not in GROUPABLE_TYPES:
            return (f"'{media_type.replace('_', ' ')}' cannot be grouped "
                    f"(groupable: "
                    f"{', '.join(sorted(GROUPABLE_TYPES)).replace('_', ' ')})")
        mods = list(dict.fromkeys(list(modifiers or []) + ["group"]))
        return apply_patch(data, line, {"media_type": media_type,
                                        "modifiers": mods})

    if choice == "override":                   # (X) use the quick new footage anyway
        return apply_patch(data, line, pend)

    return "unknown short-scene choice"


# =============================================================================
# state + server
# =============================================================================

class _State:
    def __init__(self, json_path: Path):
        self.json_path = json_path
        # the ongoing change report: every split / join / media-type change /
        # search-term change is appended here with the full sentence(s) it sits
        # in, before + after, and the user's reason (when they give one through
        # the bottom-right overlay). Plain text, append-only.
        self.log_path = REPORT_PATH
        # NOTHING IS EVER WRITTEN AUTOMATICALLY. A change only reaches the
        # report when the user types a reason and clicks "save reason" in the
        # bottom-right overlay. Until then its entries just QUEUE UP here in
        # memory: every change keeps its offer alive (a newer one no longer
        # supersedes it), so making three changes in a row and then explaining
        # them one by one works. Anything still un-explained when the session
        # ends was never written — the file only ever gets reasoned changes.
        self._log_lock = threading.Lock()
        self._change_seq = 0
        # change_id -> {"entries": [entry dict], "label": str}, oldest first
        self._pending: Dict[int, dict] = {}
        self.data: Dict[str, dict] = json.loads(
            json_path.read_text(encoding="utf-8"))
        # CONFIG.py's own convention is "<name>_script_to_search_term.json"
        # (underscore, no TESTING_ prefix) — accept either separator so the
        # split-meta cache (for noun/place suggestion chips) is still found
        # when this tool is driven by main.py instead of run standalone.
        m = re.match(r"(?:TESTING_)?(.+?)[-_]script_to_search_term",
                     json_path.stem)
        prefix = m.group(1) if m else json_path.stem
        triples = None
        # cwd, not HERE: standalone use runs from this directory (cwd==HERE)
        # per MASTER_README; embedded use (main.py) runs from the repo root,
        # where CONFIG._CACHE_DIR ("<prefix>-CACHE") actually lives.
        hits = sorted(Path.cwd().glob(
            f"{prefix}-CACHE/split-and-lable/*SPLITMETA*-{prefix}.json"))
        if hits:
            triples = json.loads(hits[-1].read_text(encoding="utf-8"))
        by_text = {t[0]: t[2] for t in (triples or [])}
        self.suggest = {line: _suggest_from_meta(by_text.get(line, {}), line)
                        for line in self.data}
        self.catalog = build_catalog()
        self.backed_up = False
        # per-action undo: each successful mutation pushes the PRE-state, so
        # the user can click an option and immediately revert it (task 11).
        self.undo_stack: List[Dict[str, dict]] = []
        # set by the browser's finish button (POST /finish) — lets
        # run_manual_tagging() block only until the user is actually done,
        # instead of forever (the standalone CLI still just uses Ctrl-C).
        self.finished_event = threading.Event()
        moved = migrate_legacy_groups(self.data)
        if moved:
            print(f"  · {moved} group cell(s) written the old way "
                  f"(same base repeated) re-spelled as "
                  f"{GROUP_CONTINUATION_TYPE.replace('_', ' ')} + group")
        recompute(self.data)
        # ---- background recommendation worker ---------------------------
        # Computing suggestions (WordNet lookups, 37k-word ratings ...)
        # can take seconds on a cold start.  It must NEVER run inside a
        # request: /data serves whatever is ready instantly (keyed by line
        # TEXT, so unchanged lines keep their chips through splits/joins),
        # and a daemon thread recomputes whenever the data changes.  This
        # is also what fixes the "unknown line" popups: a slow /data left
        # the browser acting on a stale line list.
        self._rec_lock = threading.Lock()
        self._recs_by_text: Dict[str, dict] = {}
        self._rec_state = "loading" if _HAS_RECOMMENDER else "ready"
        self._rec_gen = 0
        self._rec_busy = False
        self._rec_started = time.time()
        self._rec_words = sum(_word_count(ln) for ln in self.data)
        self._kick_recompute()

    _EMPTY_REC = {"current": [], "singles": [], "pairs": [], "pronouns": []}

    def _kick_recompute(self) -> None:
        if not _HAS_RECOMMENDER:
            return
        with self._rec_lock:
            self._rec_gen += 1
            self._rec_state = "loading"
            self._rec_started = time.time()
            self._rec_words = sum(_word_count(ln) for ln in self.data)
            # ONE worker: while `_rec_busy` it loops until it has published
            # the newest generation — never a thundering herd of concurrent
            # cold recomputes. `_rec_busy` (not thread.is_alive()) is what
            # marks the handoff, because the worker clears it in the SAME
            # locked block that publishes. Testing is_alive() instead used to
            # lose the race: a worker that had published and broken out of its
            # loop was still "alive" for the moment it spent recording timings,
            # so a kick landing there would bump the generation, flip the state
            # to "loading", and then decline to start a worker — leaving the
            # page stuck on "updating suggestions…" with nobody to publish.
            if self._rec_busy:
                return
            self._rec_busy = True
        threading.Thread(target=self._rec_worker, daemon=True).start()

    def _rec_worker(self) -> None:
        t_first = time.time()      # what the loading screen experiences:
        lines: List[str] = []      # total wall time until first publish
        try:
            while True:
                with self._rec_lock:
                    gen = self._rec_gen
                    lines = list(self.data)
                    terms = {i: (row.get("search_term") or "").strip()
                             for i, row in enumerate(self.data.values())}
                try:
                    recs = suggestions_for_all_lines(
                        lines, confirmed_terms={i: t for i, t in terms.items()
                                                if t})
                except Exception:                      # pragma: no cover
                    recs = [dict(self._EMPTY_REC) for _ in lines]
                with self._rec_lock:
                    if gen != self._rec_gen:
                        continue      # edits landed meanwhile — go again
                    self._recs_by_text = dict(zip(lines, recs))
                    self._rec_state = "ready"
                    self._rec_busy = False   # published + freed, atomically
                    break
        finally:
            # a crash must not strand the page on the loading screen: free the
            # slot, and let the chips fall back to the plain per-line words.
            with self._rec_lock:
                if self._rec_busy:
                    self._rec_busy = False
                    self._rec_state = "ready"
        elapsed = time.time() - t_first
        if elapsed > 1.0:
            # only COLD runs inform the loading-screen estimate — a warm
            # 0.0s recompute would corrupt future countdowns
            _record_timing(sum(_word_count(ln) for ln in lines), elapsed)

    def _rec_status(self) -> dict:
        if self._rec_state == "ready":
            return {"state": "ready", "eta_seconds": 0}
        est = _estimate_seconds(self._rec_words)
        remaining = max(1, int(round(est - (time.time()
                                            - self._rec_started))))
        return {"state": "loading", "eta_seconds": remaining}

    def payload(self) -> dict:
        # suggestions for lines created by splits/joins fall back to words
        for line in self.data:
            if line not in self.suggest:
                self.suggest[line] = _suggest_from_meta({}, line)
        with self._rec_lock:
            recs = self._recs_by_text
            status = self._rec_status()
        return {"json_path": self.json_path.name,
                "catalog": self.catalog,
                "can_undo": bool(self.undo_stack),
                # brand-new footage shorter than this many words just flashes;
                # the UI offers the fix-it options when a new type is picked
                # on a line below it (task 11).
                "min_new_words": _MIN_NEW_WORDS,
                "recommend_status": status,
                # changes still waiting for a "why" — a page reload keeps the
                # offers instead of quietly losing them
                "pending": self.pending(),
                "lines": [{"line": line, "row": row,
                           "words": _word_count(line),
                           "suggest": self.suggest[line],
                           "recommend": recs.get(line, self._EMPTY_REC)}
                          for line, row in self.data.items()]}

    def _checkpoint(self) -> None:
        if not self.backed_up:
            shutil.copy2(self.json_path,
                         self.json_path.with_name(self.json_path.name + ".bak"))
            self.backed_up = True

    def _commit(self) -> None:
        recompute(self.data)
        self.json_path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8")

    def mutate(self, op: str, req: dict
               ) -> "tuple[Optional[str], Optional[int], str]":
        """Apply one mutation. Returns (error, change_id, label): change_id is
        set when the change produced report entries — the browser's reason
        overlay quotes it back through /reason — and label is a short human
        description of what was logged ("Line split", …)."""
        self._checkpoint()
        if op == "undo":
            if not self.undo_stack:
                return "nothing to undo", None, ""
            self.data = self.undo_stack.pop()
            self._commit()
            self._kick_recompute()
            cid, label = self._write_entries([self._entry(
                "Undo", ["(the change above)"],
                ["the change above"], ["reverted"])])
            return None, cid, label or "Undo"
        # snapshot BEFORE the change so a failed op leaves no undo entry
        snapshot = copy.deepcopy(self.data)
        if op == "save":
            err = apply_patch(self.data, req.get("line", ""),
                              req.get("patch", {}))
        elif op == "split":
            err = split_line(self.data, req.get("line", ""),
                             int(req.get("index", 0)))
        elif op == "join":
            err = join_to_above(self.data, req.get("line", ""))
        elif op == "shortfix":
            err = resolve_short_scene(
                self.data, req.get("line", ""), req.get("choice", ""),
                req.get("media_type", ""), req.get("modifiers", []))
        else:
            err = "unknown operation"
        if err:
            self.data = snapshot            # roll back any partial mutation
            return err, None, ""
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        self._commit()
        self._kick_recompute()
        cid, label = self._log_mutation(op, req, snapshot)
        return None, cid, label

    # ---- the change report ------------------------------------------------
    # NOTHING GOES IN THE REPORT UNLESS THE USER CLICKS "SAVE REASON".
    #
    # EVERY action offers a why: a split, a join, tagging a media type (the
    # FIRST one on a line just as much as a change to an existing one),
    # editing the search term, adding/removing decorate & friends, filling in
    # a data form, picking a stamp source. Each builds its "### …" entry — the
    # full sentence(s) the affected line sits in, then before + after of
    # whatever actually changed — and that entry WAITS IN MEMORY. Only when
    # the user types a reason in the bottom-right overlay and clicks "save
    # reason" is it appended to manual_tagging_changes_report.txt, reason and
    # all. Tag away without explaining yourself and the file stays untouched.
    #
    # Offers QUEUE (see `_pending`): a newer change no longer throws the older
    # one away, so you can tag a media type, type a term, move to the next
    # line, and still be offered the why for each of them in turn.
    #
    # Titles say WHICH action on WHICH line ("Line 12 — search term changed"),
    # matching the index shown down the left of the line list, so a reason can
    # never be attached to the wrong thing by accident.
    #
    # One mutation that changes several things builds several entries (all
    # under one change_id, so one reason covers them). Typed fields (search
    # term, data forms) save on a per-keystroke debounce: their entries
    # COALESCE — the pending offer keeps its original "before" and just moves
    # its "after" along — so an edit is one offer, not one per letter.

    # the row fields a change is worth reporting, with how to say them out
    # loud. Everything else on a row (position, group_id, …) is derived by
    # recompute() rather than chosen by the user, so it is never logged.
    _LOGGED_FIELDS = (("media_type", "media type"),
                      ("modifiers", "extras"),
                      ("search_term", "search term"),
                      ("data", "data fields"),
                      ("stamp_source", "stamp source"),
                      ("stamp_decorate", "decorate the stamp first"))
    # typed fields — one debounced save per keystroke, so their offers merge
    _COALESCING_FIELDS = {"search_term", "data"}

    @staticmethod
    def _full_sentences(keys, targets) -> "list[str]":
        """The full sentence(s) the target line(s) are part of, rebuilt from
        the whole script in order. A line that straddles a sentence border
        (splits don't respect punctuation) reports every sentence it
        touches."""
        targets = set(targets)
        full, spans = "", []
        for k in keys:
            if full:
                full += " "
            start = len(full)
            full += k
            spans.append((start, len(full), k))
        sents, start = [], 0
        for m in re.finditer(r"[.!?]+[\"'”’)\]]*(?:\s+|$)", full):
            sents.append((start, m.end(), full[start:m.end()].strip()))
            start = m.end()
        if start < len(full):
            sents.append((start, len(full), full[start:].strip()))
        tspans = [(s, e) for s, e, k in spans if k in targets]
        return [txt for s, e, txt in sents
                if any(s < te and e > ts for ts, te in tspans)]

    @staticmethod
    def _verb(old, new) -> str:
        return "set" if not old else ("cleared" if not new else "changed")

    @staticmethod
    def _entry(title: str, sentences, before, after,
               coalesce=None, stem: str = "", raw=(None, None)) -> dict:
        """One report entry, kept STRUCTURED until it is written: a coalescing
        entry (a typed field) has its `after` replaced as the user keeps
        typing, which pre-formatted text could not do. before/after are lists
        of ready-to-print item strings; `coalesce` is the (line, field) key
        that later saves of the same edit merge into; `stem` + `raw` are what
        a merge needs to re-say the title ("… search term set" can become
        "… search term changed" as the edit grows)."""
        return {"title": title, "sentences": list(sentences),
                "before": list(before), "after": list(after),
                "coalesce": coalesce, "stem": stem, "raw": tuple(raw)}

    @staticmethod
    def _render(entry: dict, reason: str) -> str:
        out = f"### {entry['title']}\nFull sentence(s):\n"
        out += "".join(f'- "{s}"\n' for s in entry["sentences"]) \
            or "- (unknown)\n"
        out += "Before:\n" + "".join(f"- {b}\n" for b in entry["before"])
        out += "After:\n" + "".join(f"- {a}\n" for a in entry["after"])
        return out + f'Reason:\n- "{reason}"\n\n'

    @staticmethod
    def _label(entries: "list[dict]") -> str:
        return " + ".join(e["title"] for e in entries)

    def _write_entries(self, entries: "list[dict]"
                       ) -> "tuple[Optional[int], str]":
        """QUEUE the entries in memory — nothing is written to the report
        here. They are only ever put on disk by attach_reason(), i.e. when the
        user clicks "save reason". A change nobody explains is never written.

        A single coalescing entry (one keystroke of a search term, say) folds
        into the pending offer for the same line+field instead of queueing a
        new one: same change_id, same "before", newer "after". Typing an edit
        and then undoing it by hand cancels the offer altogether."""
        if not entries:
            return None, ""
        with self._log_lock:
            key = entries[0]["coalesce"] if len(entries) == 1 else None
            if key:
                for cid, pend in list(self._pending.items()):
                    prev = pend["entries"][0]
                    if len(pend["entries"]) != 1 or prev["coalesce"] != key:
                        continue
                    merged = dict(entries[0], before=prev["before"])
                    if merged["before"] == merged["after"]:
                        del self._pending[cid]      # typed back to how it was
                        return None, ""
                    # the whole edit, not its last keystroke, decides whether
                    # this reads as "set", "changed" or "cleared"
                    old_raw, new_raw = prev["raw"][0], merged["raw"][1]
                    merged["raw"] = (old_raw, new_raw)
                    if merged["stem"]:
                        merged["title"] = (f"{merged['stem']} "
                                           f"{self._verb(old_raw, new_raw)}")
                    pend["entries"] = [merged]
                    pend["label"] = self._label([merged])
                    return cid, pend["label"]
            self._change_seq += 1
            label = self._label(entries)
            self._pending[self._change_seq] = {"entries": entries,
                                               "label": label}
            while len(self._pending) > _MAX_PENDING:
                self._pending.pop(next(iter(self._pending)))
            return self._change_seq, label

    def pending(self) -> "list[dict]":
        """The still-un-explained changes, oldest first — the browser offers
        them one at a time (newest first) in the bottom-right overlay."""
        with self._log_lock:
            return [{"id": cid, "label": p["label"]}
                    for cid, p in self._pending.items()]

    def dismiss(self, change_id: int, every: bool = False) -> None:
        """"no reason needed" — drop the offer (or the whole queue, when the
        user clicks skip all) without writing anything."""
        with self._log_lock:
            if every:
                self._pending.clear()
            else:
                self._pending.pop(change_id, None)

    def attach_reason(self, change_id: int, reason: str) -> None:
        """THE ONLY THING THAT EVER WRITES TO THE CHANGE REPORT. The user has
        typed a reason and clicked "save reason", so now — and only now — the
        change it belongs to is appended, with the reason on the end.

        ANY still-pending change can take a reason (not just the newest): the
        overlay works through a queue, and a reason typed while a later change
        lands must still land on the change it was written about. An empty
        reason writes nothing. Logging must never break tagging, so any I/O
        problem is reported and swallowed."""
        reason = (reason or "").strip()
        if not reason:
            return
        try:
            with self._log_lock:
                pend = self._pending.pop(change_id, None)  # written once only
                if not pend:
                    return
                body = "".join(self._render(e, reason)
                               for e in pend["entries"])
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write(body)
        except Exception as exc:  # pragma: no cover - disk trouble only
            print(f"  · could not write the change report: {exc}")

    @staticmethod
    def _where(keys, key) -> str:
        """"Line 12" — the index the line list shows down its left-hand side
        (0-based, same as the browser), so the report names the same line the
        user was looking at."""
        keys = list(keys)
        return f"Line {keys.index(key)}" if key in keys else "Line"

    @staticmethod
    def _value(field: str, value) -> str:
        """A field's value the way a human reads it back."""
        if field == "modifiers":
            return ", ".join(value or []).replace("_", " ") or "(none)"
        if field == "stamp_decorate":
            return "yes" if value else "no"
        if field == "data":
            return ", ".join(f"{k}={v}" for k, v in (value or {}).items()) \
                or "(none)"
        return str(value or "").strip() or "(none)"

    @staticmethod
    def _normalise(field: str, row: dict):
        val = row.get(field)
        if field == "modifiers":
            return list(val or [])
        if field == "data":
            return dict(val or {})
        if field == "stamp_decorate":
            return bool(val)
        return (val or "").strip()

    def _field_entries(self, key: str, old_row: dict,
                       new_row: dict) -> "list[dict]":
        """One entry per field the user actually changed on this line —
        media type, extras, search term, data form, stamp settings — whether
        it is the FIRST value the field has ever had ("set") or a change to
        one that was already there ("changed")."""
        out = []
        where = self._where(self.data, key)
        sentences = self._full_sentences(self.data, [key])
        for field, label in self._LOGGED_FIELDS:
            old = self._normalise(field, old_row)
            new = self._normalise(field, new_row)
            if old == new:
                continue
            stem = f"{where} — {label}"
            title = f"{stem} {self._verb(old, new)}"
            if field == "media_type":
                before = [f'"{key}" [{old or "untagged"}]']
                after = [f'"{key}" [{new or "untagged"}]']
            else:
                def item(mt, val):
                    tag = f" [{mt}]" if mt else ""
                    return (f'"{key}"{tag}\n'
                            f'    - {label}: {self._value(field, val)}')
                before = [item(self._normalise("media_type", old_row), old)]
                after = [item(self._normalise("media_type", new_row), new)]
            out.append(self._entry(
                title, sentences, before, after,
                coalesce=((key, field) if field in self._COALESCING_FIELDS
                          else None),
                stem=stem, raw=(old, new)))
        return out

    def _log_mutation(self, op: str, req: dict, before: Dict[str, dict]
                      ) -> "tuple[Optional[int], str]":
        """Work out what a successful mutation changed (by comparing the
        pre-change snapshot with the live data) and offer it — one entry PER
        KIND of change, so a shortfix that joins two lines and retags a third
        offers a Line join entry and a media type entry under one why."""
        entries = []
        line = req.get("line", "")
        if op == "split":
            halves = [k for k in self.data if k not in before]
            entries.append(self._entry(
                f"{self._where(before, line)} split",
                self._full_sentences(before, [line]),
                [f'"{line}"'], [f'"{k}"' for k in halves]))
        elif op == "join":
            gone = [k for k in before if k not in self.data]
            merged = [k for k in self.data if k not in before]
            entries.append(self._entry(
                f"{self._where(before, line)} joined into the line above",
                self._full_sentences(before, gone),
                [f'"{k}"' for k in gone], [f'"{k}"' for k in merged]))
        # field changes on ANY surviving line, whatever op caused them (a
        # plain retag, a term edit, a shortfix retagging this line AND a
        # neighbour, the auto-tagging of a group cell …)
        for key in self.data:
            if key in before:
                entries.extend(
                    self._field_entries(key, before[key], self.data[key]))
        return self._write_entries(entries)


def make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json", code=200):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/data":
                self._send(json.dumps(state.payload()))
            elif self.path.startswith("/example/"):
                name = re.sub(r"[^a-z0-9_]", "", self.path.split("/")[-1])
                p = HERE / "examples" / f"{name}.png"
                if p.exists():
                    self._send(p.read_bytes(), "image/png")
                else:
                    self._send("{}", code=404)
            else:
                self._send(PAGE, "text/html")

        def do_POST(self):
            op = self.path.lstrip("/")
            n = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                req = {}
            if op == "finish":
                state.finished_event.set()
                self._send(json.dumps({"ok": True}))
                return
            if op == "reason":
                # the optional "why" from the bottom-right overlay, folded
                # into the change it belongs to. The reply carries whatever
                # is still un-explained, so the overlay can offer the next one.
                state.attach_reason(int(req.get("change_id") or 0),
                                    req.get("reason") or "")
                self._send(json.dumps({"ok": True,
                                       "pending": state.pending()}))
                return
            if op == "dismiss":
                # "no reason needed" — the offer goes away, nothing is written
                state.dismiss(int(req.get("change_id") or 0),
                              bool(req.get("all")))
                self._send(json.dumps({"ok": True,
                                       "pending": state.pending()}))
                return
            err, cid, label = state.mutate(op, req)
            body = json.dumps({"ok": err is None, "error": err,
                               "change_id": cid, "label": label,
                               "pending": state.pending()})
            self._send(body, code=200 if err is None else 400)
    return Handler


def make_server(json_path: Path, port: int = 0) -> ThreadingHTTPServer:
    state = _State(json_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    server.state = state
    return server


def _pick_json(arg: Optional[str]) -> Path:
    if arg:
        return Path(arg)
    hits = sorted(HERE.glob("*-script_to_search_term.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not hits:
        sys.exit("No *-script_to_search_term.json found. "
                 "Run SPLIT_AND_LABEL.py first (see MASTER_README.md).")
    return hits[0]


PAGE = r'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>manual tagging</title><style>
 :root{--gold:#e6c15a;--bg:#16181d;--card:#1c1f27;--blue:#2c4a72}
 body{margin:0;font:14px/1.45 system-ui,sans-serif;background:var(--bg);color:#e8e8e8}
 #bar{display:flex;gap:14px;align-items:center;padding:6px 14px;border-bottom:1px solid #2a2e38;position:sticky;top:0;background:var(--bg);z-index:5}
 #bar details{margin:0;flex:1}
 #finish{padding:7px 18px;border-radius:8px;border:1px solid #3a7a4a;background:#234a30;color:#bfe8c8;cursor:pointer;font-size:14px}
 #finish.ready{background:#2e7d32;color:#fff;border-color:#66bb6a;font-size:16px;padding:9px 26px;animation:pulse 1.6s infinite}
 @keyframes pulse{0%,100%{box-shadow:0 0 0 0 #2e7d3266}50%{box-shadow:0 0 0 9px #2e7d3200}}
 #undo{padding:7px 14px;border-radius:8px;border:1px solid #4a5060;background:#232733;color:#cdd;cursor:pointer;font-size:14px}
 #undo:disabled{opacity:.4;cursor:default}
 #shortcard{background:#2a2012;border:2px solid #c9a13b}
 #shortcard h3{color:#f0c95a}
 #shortmsg{color:#e8dcc0;font-size:14px;line-height:1.5;margin:0 0 12px}
 #shortmsg b{color:#fff}
 #shortopts{max-height:340px;overflow-y:auto}
 #shortopts button{display:block;width:100%;text-align:left;margin:6px 0;padding:10px 12px;border-radius:8px;border:1px solid #4a5262;background:#20242e;color:#dfe4ee;cursor:pointer;font-size:13.5px}
 #shortopts button:hover{background:#2b3444;border-color:#c9a13b}
 #shortopts button.rec{border-color:#66bb6a;background:#1e3324}
 #shortopts button.rec:hover{background:#254a30}
 #shortopts button.ovr{border-style:dashed;color:#c9a}
 #shortopts button:disabled{opacity:.4;cursor:not-allowed;background:#20242e;border-color:#3a4150}
 #shortcancel{margin-top:10px;padding:7px 14px;border-radius:8px;border:1px solid #4a5060;background:#191c24;color:#aab;cursor:pointer;font-size:13px}
 #manualhint{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:60;display:none;align-items:center;gap:14px;max-width:560px;padding:12px 16px;border-radius:10px;background:#2a2012;border:1px solid #c9a13b;box-shadow:0 6px 22px #0009;color:#e8dcc0;font-size:13.5px;line-height:1.5}
 #manualhint .mh-txt b{color:#f0c95a}
 #manualhint button{flex:none;padding:6px 14px;border-radius:7px;border:1px solid #66bb6a;background:#1e3324;color:#bfe8c8;cursor:pointer;font-size:13px}
 #manualhint button:hover{background:#254a30}
 #wrap{display:flex;height:calc(100vh - 44px)}
 #list{width:48%;overflow-y:auto;padding:8px;box-sizing:border-box}
 .navbtn{display:block;width:100%;padding:7px;margin:6px 0;border:1px solid #3a4356;border-radius:7px;background:#20242e;color:#aeb6c6;cursor:pointer;font-size:13px}
 .navbtn:hover{background:#2a2f3c}
 .navbtn small{color:#667;margin-left:6px}
 #editor{flex:1;overflow-y:auto;padding:12px 16px;box-sizing:border-box;display:flex;flex-direction:column}
 .row{padding:7px 8px;border-radius:6px;cursor:pointer;display:flex;gap:8px;align-items:baseline;border-left:4px solid transparent;position:relative}
 .row:hover{background:#22252d}.row.sel{background:#2b3040;outline:1px solid #4a5578}
 .idx{color:#666;width:2em;text-align:right;flex:none;font-size:12px}
 .dot{flex:none;font-size:14px;color:#3a4150;width:1em;text-align:center}
 .dot.done{color:#7bd88f}
 .badge{font-size:11px;padding:1px 7px;border-radius:9px;color:#fff;flex:none}
 .cellno{font-size:10.5px;padding:1px 6px;border-radius:9px;flex:none;border:1px solid currentColor;opacity:.85}
 .ltxt{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:15.5px;position:relative}
 .ltxt.expand{white-space:normal;overflow:visible}
 .ltxt span.ch{position:relative}
 .lterm{color:#9aa;font-size:12px;flex:none;max-width:9em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .joinbtn{flex:none;visibility:hidden;border:0;background:#2c3342;color:#bcd;border-radius:6px;padding:2px 9px;cursor:pointer;font-size:12px}
 .row:hover .joinbtn{visibility:visible}
 .joinbtn:hover{background:linear-gradient(90deg,var(--gold),#f5dd9a,var(--gold));background-size:200% 100%;color:#222;font-weight:600;animation:shimmer 1s linear infinite}
 @keyframes shimmer{0%{background-position:200% 0}100%{background-position:0 0}}
 .row.joinghost{opacity:.55}.row.joinghost .ltxt{text-decoration:line-through}
 .row.joinghost .joinbtn{opacity:1;visibility:visible}
 .ghostadd{color:#8a8;font-style:italic}
 #caret{position:fixed;width:2px;background:var(--gold);pointer-events:none;display:none;box-shadow:0 0 6px var(--gold);z-index:30}
 #splittip{position:fixed;background:var(--gold);color:#222;font-weight:600;font-size:12px;padding:3px 9px;border-radius:6px;pointer-events:none;display:none;white-space:nowrap;z-index:30}
 h3{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.08em}
 .req{font-size:11px;margin-left:6px;opacity:.8}
 .info{display:inline-block;width:15px;height:15px;line-height:15px;text-align:center;border-radius:50%;background:#2c3342;color:#9ab;font-size:11px;cursor:help;margin-left:5px;font-style:normal}
 #curline{display:none;background:#26210f;border:2px solid var(--gold);border-radius:10px;padding:10px 12px;margin-bottom:10px;font-size:17px}
 #curline small{display:block;color:#a99a6a;font-size:11px;margin-bottom:4px;letter-spacing:.06em}
 .card{overflow-y:scroll;border-radius:10px;margin:0 0 12px;}
 .card .head{padding:11px 14px;cursor:pointer;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
 .card .head h3{margin:0}
 .card .body{padding:0 14px 12px}
 .card.collapsed .body{display:none}
 .card.collapsed .head{opacity:.75}
 #mediacard.attn{animation:flashblue 1.2s ease-out}
 @keyframes flashblue{0%{box-shadow:0 0 0 0 #6fa6ff99}70%{box-shadow:0 0 0 10px #6fa6ff00}}
 #termcard.attn{animation:flashgold 1.2s ease-out}
 @keyframes flashgold{0%{box-shadow:0 0 0 0 #e6c15a99}70%{box-shadow:0 0 0 10px #e6c15a00}}
 .stepbtn{margin-top:10px;padding:8px 26px;border-radius:8px;border:0;background:#35507a;color:#fff;cursor:pointer;font-weight:600}
 #tostep2{min-width:230px}
 #tostep2.glow{background:var(--gold);color:#222;animation:pulse 1.6s infinite}
 .kbhint{color:#5a6478;font-size:11px;width:100%}
 #mediacard{background:#1a2130;border:1px solid #35507a}
 #mediacard h3{color:#8fb4e8}
 #tabbar{display:flex;align-items:center;gap:9px;margin:2px 0 12px}
 #tabbar .arw{width:26px;height:26px;line-height:1;border-radius:50%;border:1px solid #3a4a66;background:#141a26;color:#8fb4e8;cursor:pointer;font-size:15px;padding:0}
 #tabbar .arw:hover{background:#22304a}
 #tabdots{display:flex;gap:7px;align-items:center}
 .tdot{width:9px;height:9px;border-radius:50%;background:#3a4356;cursor:pointer}
 .tdot:hover{background:#55607a}
 .tdot.on{background:#8fb4e8;box-shadow:0 0 0 3px #8fb4e82e}
 #tabname{color:#8fb4e8;font-size:12px;letter-spacing:.08em;text-transform:uppercase}
 #tabhint{color:#5a6478;font-size:11px;margin-left:auto}
 #datacard{background:#16241d;border:1px solid #3f6b52}
 #datacard h3{color:#7fc9a0}
 #datacard.attn{animation:flashgreen 1.2s ease-out}
 @keyframes flashgreen{0%{box-shadow:0 0 0 0 #7fc9a099}70%{box-shadow:0 0 0 10px #7fc9a000}}
 .dfield{display:block;margin:0 0 12px}
 .dfield .dlbl{display:block;color:#cfe4d8;font-size:13px;margin-bottom:4px}
 .dfield .dlbl i{color:#6d8a7c;font-style:normal}
 .dfield input{width:100%;box-sizing:border-box;background:#0f1116;color:#fff;border:1px solid #3f6b52;border-radius:6px;padding:8px;font:inherit}
 .dfield .dhelp{display:block;color:#6d8a7c;font-size:11.5px;margin-top:4px;line-height:1.4}
 #bottomstick{padding-top:4px}
 #termcard{background:#241f12;border:1px solid #6e5c2a;box-shadow:0 -8px 18px #0008;margin-bottom:8px}
 #termcard h3{color:var(--gold)}
 .cols{display:flex;gap:14px;flex-wrap:wrap}
 .col{min-width:160px;flex:1}.col h4{margin:4px 0;font-size:12px;color:#aab}
 button.tpl{display:flex;justify-content:space-between;align-items:center;width:100%;margin:3px 0;padding:6px 9px;border:0;border-radius:6px;color:#fff;cursor:pointer;opacity:.94}
 button.tpl:hover{opacity:1}button.tpl.on{outline:2px solid #fff}
 button.tpl.kb{outline:2px dashed #fff}
 button.tpl:disabled{background:#3a4150 !important;color:#79808f;cursor:not-allowed;opacity:1}
 .modrow{margin-top:10px;padding-top:8px;border-top:1px dashed #39404f}
 .modrow .lbl{color:#aab;font-size:12px;margin-right:8px}
 button.mod{margin:2px 6px 2px 0;padding:5px 12px;border-radius:14px;border:1px dashed var(--gold);background:transparent;color:var(--gold);cursor:pointer}
 button.mod.on{background:var(--gold);color:#222;border-style:solid}
 button.mod:disabled{opacity:.3;cursor:not-allowed}
 .hint{color:#667;font-size:12px}
 textarea{width:100%;box-sizing:border-box;background:#0f1116;color:#fff;border:1px solid #6e5c2a;border-radius:6px;padding:8px;font:inherit;min-height:54px}
 #termwrap{position:relative}
 #ghost{position:absolute;left:9px;top:9px;color:#556;pointer-events:none;display:none}
 #tabpill{background:#2c3342;color:#9ab;border-radius:5px;font-size:11px;padding:1px 7px;margin-left:8px}
 .chips button{margin:4px 5px 0 0;border-radius:12px;cursor:pointer}
 .chips .big{padding:5px 12px;font-size:14px;border:1px solid #6fae6f;background:#20302a;color:#cfe8cf}
 .chips .big.place{border-color:#5f9ec0;background:#202a33;color:#cfe0ee}
 .chips .small{padding:2px 9px;font-size:12px;border:1px solid #3a4356;background:#20242e;color:#aab}
 .chiplbl{color:#667;font-size:11px;margin:6px 6px 0 0;display:inline-block}
 .chips .pair{padding:5px 12px;font-size:14px;border:1px solid var(--gold);background:#2a2412;color:#f0dfa0;font-weight:600}
 .chips .pair:hover{background:#3a3218}
 .chips .rec{padding:3px 10px;font-size:12.5px;border:1px solid #7a6a9a;background:#241f2e;color:#cabfe0}
 .chips .rec small{color:#8a7aaa;font-size:10px;margin-left:4px}
 #pronounbox{display:none;margin-top:8px;border:1px solid #3a4356;border-radius:8px;background:#191c24;max-height:96px;overflow-y:auto;padding:4px 8px}
 #pronounbox .plbl{color:#667;font-size:11px;display:block;margin:2px 0}
 .prow{display:flex;gap:8px;align-items:baseline;padding:2px 4px;border-radius:5px;cursor:pointer;font-size:12.5px}
 .prow:hover{background:#242938}
 .prow .pn{color:#8fb4d8;min-width:4.5em}
 .prow .arrow{color:#556}
 .prow .cand{color:#d8cfa8;flex:1}
 .prow .pp{color:#7a8}
 #nextbtn{display:block;visibility:hidden;margin:0 0 12px;padding:10px 20px;border-radius:8px;border:0;background:var(--gold);color:#222;font-weight:700;cursor:pointer;font-size:15px}
 #nextbtn.amber{background:#c9a13b}
 #nextbtn.fin{background:#2e7d32;color:#fff}
 details{background:var(--card);border-radius:8px;padding:5px 12px}
 summary{cursor:pointer;color:#9aa;font-size:13px}
 #keycards{max-height:46vh;overflow-y:auto}
 .icard{display:flex;gap:12px;align-items:flex-start;border-top:1px solid #262b36;padding:8px 0}
 .icard img{width:96px;height:64px;object-fit:cover;border-radius:6px;background:#333;flex:none}
 .icard .nm{font-weight:600}
 #pop{position:fixed;z-index:50;right:14px;bottom:14px;background:#232836;border:1px solid #4a5578;border-radius:10px;padding:10px 12px;width:300px;display:none;box-shadow:0 6px 24px #0009}
 #finwrap{display:none;position:fixed;inset:0;z-index:70;background:#000b;align-items:center;justify-content:center}
 #finwrap.open{display:flex}
 #finbox{background:#1d2b1f;border:2px solid #2e7d32;border-radius:14px;padding:28px 36px;text-align:center;display:flex;flex-direction:column;gap:10px;font-size:18px;max-width:420px}
 #finbox button{margin-top:8px;padding:9px 18px;border-radius:8px;border:1px solid #4a5060;background:#232733;color:#cdd;cursor:pointer;font-size:14px}
 #toast{position:fixed;left:14px;bottom:14px;background:#20302a;color:#7bd88f;border-radius:8px;padding:5px 13px;font-size:12px;opacity:0;transition:opacity .3s;z-index:40;pointer-events:none}
 #whycard{position:fixed;right:14px;bottom:14px;z-index:45;width:310px;background:#241f12;border:1px solid var(--gold);border-radius:10px;padding:10px 12px;box-shadow:0 6px 24px #0009;display:none}
 #whycard .wtitle{color:var(--gold);font-size:12.5px;cursor:pointer}
 #whycard .wtitle .wkind{background:#3a3218;border-radius:5px;padding:2px 7px;font-weight:600;display:inline-block;line-height:1.35}
 #whycard .wtitle:hover .wkind{text-decoration:underline}
 #whycard .whyhint{color:#b9a877;font-size:11.5px;margin-top:5px;cursor:pointer}
 #whycard .whymore{color:#e0a44a;font-size:11px;margin-top:5px}
 #whycard .whymore b{color:var(--gold)}
 #whycard .whymore .skipall{color:#9a8c66;text-decoration:underline;cursor:pointer;margin-left:6px}
 #whybody{display:none;margin-top:8px}
 #whybody input{width:100%;box-sizing:border-box;background:#0f1116;color:#fff;border:1px solid #6e5c2a;border-radius:6px;padding:7px;font:inherit}
 #whybody button{margin-top:7px;padding:6px 14px;border-radius:7px;border:0;background:var(--gold);color:#222;font-weight:600;cursor:pointer;font-size:13px}
 #whybody button.wskip{background:#3a3218;color:#cbbc8e;font-weight:500;margin-left:6px}
 #whycard.saved .wtitle{color:#7bd88f;cursor:default}
 #mback,#donebtn,#mobactions,#msplit,#joinconfirm,#joinshield{display:none}
 @media (max-width:700px){
  #wrap{display:block;height:auto}
  #list{width:100%;padding-bottom:70px}
  .idx,.lterm,.joinbtn,.navbtn{display:none}
  .row{display:block;border-radius:0;border-bottom:1px solid rgba(255,255,255,.14);padding:11px 8px;padding-right:104px}
  .badge{display:block;width:max-content;margin:0 0 5px}
  .ltxt{white-space:normal;font-size:16px}
  .mjoin{position:absolute;right:8px;top:10px;padding:9px 12px;border-radius:8px;border:1px solid #5f9ec0;background:#16222c;color:#a9d0ea;font-size:13px}
  .row.joinpend{position:relative;z-index:29;pointer-events:none}
  .row.joinpend .mjoin{pointer-events:auto}
  .row.joinpend .ltxt{text-decoration:line-through;opacity:.6}
  .mjoin.confirm{background:#2e7d32;border-color:#66bb6a;color:#fff;font-weight:700;z-index:29;animation:pulse 1.2s infinite;box-shadow:0 0 14px #66bb6acc}
  .mjoin.confirm.nudgeit{transform:scale(1.12)}
  #joinshield.open{display:block;position:fixed;inset:0;z-index:28;background:transparent}
  #editor{display:none;position:fixed;inset:0;background:var(--bg);z-index:20;padding:10px;padding-bottom:80px}
  #editor.open{display:flex;overflow-y:auto}
  #mback{display:block;background:none;border:0;color:#aab;font-size:30px;line-height:1;padding:0 12px 8px 2px;cursor:pointer;align-self:flex-start}
  #curline{display:block}
  #donebtn{display:block;position:fixed;left:50%;transform:translateX(-50%);bottom:10px;z-index:25;padding:9px 22px;border-radius:9px;border:1px solid #4a5060;background:#232733;color:#cdd;font-size:14px;cursor:pointer}
  #donebtn svg{vertical-align:-2px;margin-right:7px}
  #mobactions{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:15;background:#1c1f27;border-top:1px solid #333;padding:9px;gap:8px;justify-content:center}
  #mobactions button{padding:9px 16px;border-radius:8px;border:1px solid #6e5c2a;background:#241f12;color:var(--gold);font-size:14px}
  #mobactions button.on{background:var(--gold);color:#222;font-weight:700}
  #msplit.open{display:flex;position:fixed;inset:0;z-index:60;background:var(--bg);flex-direction:column;padding:12px}
  #mstitle{color:var(--gold);font-size:13px;margin-bottom:6px}
  #mstext{font-size:23px;line-height:2.1;flex:1;overflow-y:auto;word-break:break-word;user-select:none}
  #mstext span{position:relative;padding:2px 0}
  #mscaret{display:inline-block;width:3px;height:1.15em;background:var(--gold);vertical-align:middle;box-shadow:0 0 7px var(--gold);margin:0 -1px;animation:blink 1s step-end infinite}
  @keyframes blink{50%{opacity:.35}}
  #msbar{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;padding:10px 0}
  #msbar button{padding:11px 14px;border-radius:8px;border:1px solid #555;background:#232733;color:#ddd;font-size:14px}
  #msbar .go{background:var(--gold);color:#222;border:0;font-weight:700}
  #joinconfirm.open{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:30;background:#26210f;border-top:2px solid var(--gold);padding:12px;gap:10px;justify-content:center}
  #joinconfirm button{padding:10px 16px;border-radius:8px;border:0;font-size:14px}
  #joinconfirm .yes{background:var(--gold);color:#222;font-weight:700}
  #joinconfirm .no{background:#333;color:#ddd}
 }
 #loadscreen{position:fixed;inset:0;background:#16181dee;z-index:99;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px}
 #loadscreen .spin{width:46px;height:46px;border:4px solid #2a2e38;border-top-color:var(--gold);border-radius:50%;animation:lspin 0.9s linear infinite}
 @keyframes lspin{to{transform:rotate(360deg)}}
 #loadscreen .lmsg{color:#cfd4e0;font-size:16px}
 #loadscreen .lsub{color:#778;font-size:13px}
 #loadscreen .leta{color:var(--gold);font-size:26px;font-variant-numeric:tabular-nums}
 .recupdating{color:#8a7aaa;font-size:11px;font-style:italic;margin-left:6px}
</style></head><body>
<div id="loadscreen">
 <div class="spin"></div>
 <div class="lmsg">preparing keyword suggestions&hellip;</div>
 <div class="leta" id="leta"></div>
 <div class="lsub">first run builds the word-knowledge caches &mdash; later runs are faster</div>
</div>
<div id="bar">
 <details><summary>key — groups, colours, stacking (hover any ⓘ for its card)</summary>
  <p class="hint">pick ONE base media type per line. NEW puts brand-new material on screen
  (stock and ai stock are both brand-new — ai family is red). EDIT PREVIOUS acts on the
  image already on screen, so it is greyed on the first line. then STACK extras on top:
  decorate, caption, or group — grouped neighbours share a coloured stripe (rule of n).
  a group OPENS with stock / ai stock + group and CONTINUES on the next lines with
  hold previous + group; step onto a blank line below a cell and it joins the group for
  you. every cell needs its own search term.
  a line is done when it gets its green tick: media type AND search term both set.
  splitting: hover a line's text — a golden cursor snaps between letters, click to
  break; the space after the LAST character just selects the line; both halves
  inherit the media type and search term. joining: hover a
  row's end for the join-to-above arrow — the preview shows exactly what you'll get.</p>
  <div id="keycards"></div>
 </details>
 <button id="undo" onclick="undo()" disabled title="revert the last change">↶ undo</button>
 <button id="finish" onclick="finish()">finish</button>
</div>
<div id="wrap">
 <div id="list"></div>
 <div id="editor">
  <button id="mback" onclick="closeEditor()">‹</button>
  <div id="curline"><small>YOU ARE TAGGING THIS LINE</small><span id="curlinetxt"></span></div>
  <div class="card" id="shortcard" style="display:none">
    <div class="head"><h3>⚠ too short for new footage</h3></div>
    <div class="body">
      <p id="shortmsg"></p>
      <div id="shortopts"></div>
      <button id="shortcancel" onclick="closeShort()">cancel — pick a different media type</button>
    </div>
  </div>
  <div class="card" id="mediacard">
    <div class="head" onclick="expand('media')">
      <h3>step 1 · media type</h3><span class="req">required — pick one</span>
      <i class="info" data-info="_types">i</i>
      <span class="kbhint">← → arrow keys move between types · enter picks · tab → step 2</span>
    </div>
    <div class="body">
      <div id="tabbar">
        <button class="arw" onclick="event.stopPropagation();moveTab(-1)">‹</button>
        <div id="tabdots"></div>
        <button class="arw" onclick="event.stopPropagation();moveTab(1)">›</button>
        <span id="tabname"></span>
        <span id="tabhint">[ and ] switch tab</span>
      </div>
      <div class="cols" id="typepanel"></div>
      <div class="modrow"><span class="lbl">stack on top — optional, any
        <i class="info" data-info="_mods">i</i></span>
        <span id="modbar"></span><span id="modhint" class="hint"></span></div>
      <button class="stepbtn" id="tostep2" style="display:none"
        onclick="event.stopPropagation();expand(nextStep())">continue to step 2 ↓</button>
    </div>
  </div>
  <div class="card" id="datacard" style="display:none">
    <div class="head" onclick="expand('data')">
      <h3>step 2 · data</h3><span class="req">required — this type is drawn from it</span>
    </div>
    <div class="body"><div id="datapanel"></div></div>
  </div>
  <div id="bottomstick">
  <div class="card" id="termcard">
    <div class="head" onclick="expand('term')">
      <h3>step 2 · search term</h3><span class="req" id="termreq">required</span>
    </div>
    <div class="body">
      <div id="termwrap">
        <textarea id="term"></textarea>
        <div id="ghost"></div>
      </div>
      <div class="chips" id="chips"></div>
      <div id="pronounbox"></div>
    </div>
  </div>
  <div class="card" id="stampcard" style="display:none">
    <div class="head">
      <h3>step 3 · stamp pictures from</h3><span class="req">required — pick one</span>
      <i class="info" data-info="_stamp">i</i>
    </div>
    <div class="body">
      <div class="cols"><div class="col" id="stamppanel"></div></div>
    </div>
  </div>
  <button id="nextbtn"></button>
  </div>
  <button id="donebtn" onclick="closeEditor()"><svg width="14" height="12" viewBox="0 0 14 12" fill="none"><rect y="0" width="14" height="2" rx="1" fill="#cdd"/><rect y="5" width="14" height="2" rx="1" fill="#cdd"/><rect y="10" width="14" height="2" rx="1" fill="#cdd"/></svg>done — back to list</button>
 </div>
</div>
<div id="pop"></div>
<div id="caret"></div><div id="splittip">click to break here</div>
<div id="toast">saved ✓</div>
<div id="whycard"></div>
<div id="finwrap"><div id="finbox">
 <div style="font-size:42px;color:#7bd88f">✓</div>
 <div>finished — everything is saved to the json.</div>
 <div class="hint">your terminal has already resumed. this tab is trying to close itself now — if your browser blocks that, feel free to close it by hand.</div>
 <button onclick="hideFinish()">← return back to list</button>
</div></div>
<div id="mobactions">
 <button id="mscis" onclick="toggleScissors()">✂ split a line</button>
</div>
<div id="joinshield" onclick="joinWarn()"></div>
<div id="joinconfirm">
 <span style="color:#cbb26a;align-self:center;font-size:13px">confirm the join above ↑ &nbsp;or&nbsp;</span>
 <button class="no" onclick="cancelJoin()">cancel</button>
</div>
<div id="msplit">
 <div id="mstitle">tap where to split — or nudge with the arrows below</div>
 <div id="mstext"></div>
 <div id="msbar">
  <button onclick="nudge(-1)">◀ move split point one character left</button>
  <button onclick="nudge(1)">move split point one character right ▶</button>
  <button class="go" onclick="mobileSplitGo()">✂ split the text at the selected point</button>
  <button onclick="closeMobileSplit()">cancel</button>
 </div>
</div>
<script>
let D=null, sel=0, scissors=false, pendJoin=-1, msLine=null, msB=1;
let step='media', kbType=-1, ghostDismissed=false, tab=0;
const $=q=>document.querySelector(q);
const isMobile=()=>window.innerWidth<=700;
const PLACEHOLDER='data:image/svg+xml;utf8,'+encodeURIComponent(
 '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="64"><rect width="96" height="64" fill="#3a4150"/><text x="48" y="36" fill="#99a" font-size="10" text-anchor="middle">example image</text></svg>');
function toast(msg,color){const t=$('#toast');t.textContent=msg||'saved ✓';
 t.style.color=color||'#7bd88f';t.style.opacity=1;
 clearTimeout(t._h);t._h=setTimeout(()=>t.style.opacity=0,1400);}
function esc(s){return (s||'').replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}
const GP=['#e6c15a','#5fb0c0','#b06fc0','#7bd88f','#c08a5f','#6f8ac0'];
const gcol=g=>GP[(g-1)%GP.length];

async function load(keepLine){
 D=await (await fetch('/data')).json();
 if(keepLine){const i=D.lines.findIndex(L=>L.line===keepLine); if(i>=0) sel=i;}
 sel=Math.max(0,Math.min(sel,D.lines.length-1));
 renderKey(); renderList(); renderEditor(); updateFinish();
 setPending(D.pending);          // offers survive a reload of the page state
}
function baseOf(n){return D.catalog.bases.find(b=>b.name===n);}
function isCell(L){return (L.row.modifiers||[]).includes('group');}
// the structured input this line's type needs (a timeline's year, …) — the
// server sends the field list, so a new maths type needs no code here
function dataFields(L){const b=baseOf(L.row.media_type);return (b&&b.data_fields)||[];}
function dataOk(L){
 const d=L.row.data||{};
 return dataFields(L).every(f=>!f.required||String(d[f.name]??'').trim()!=='');}
// a type drawn from `data` whose term means nothing (timeline) hides step 2's
// search box entirely — there is nothing to type in it
function usesTerm(L){
 const b=baseOf(L.row.media_type);
 return !(b&&b.term_optional&&dataFields(L).length);}
function stampNeeded(L,term){
 term=term===undefined?(L.row.search_term||''):term;
 const b=baseOf(L.row.media_type);
 return !!(b&&b.term_optional)&&!isCell(L)
  &&(L.row.modifiers||[]).includes('decorate')&&!!term.trim();}
function lineDone(L){
 if(!L.row.media_type)return false;
 const b=baseOf(L.row.media_type);
 // every cell of a group brings its OWN picture, so a cell always needs its
 // own term — even hold previous, whose term is optional on its own.
 const termOk=!!(L.row.search_term||'').trim()
   ||(!!(b&&b.term_optional)&&!isCell(L));
 return termOk&&dataOk(L)&&(!stampNeeded(L)||!!L.row.stamp_source);}
function updateFinish(){
 const all=D.lines.every(lineDone);
 $('#finish').classList.toggle('ready',all);
 $('#finish').textContent=all?'✓ finish':'finish';
 $('#undo').disabled=!D.can_undo;
}
async function undo(){await post('/undo',{},D.lines[sel]&&D.lines[sel].line);}
function renderList(){
 const el=$('#list'); el.innerHTML='';
 const nav=(dir,label,key)=>{
   const b=document.createElement('button');b.className='navbtn';
   b.innerHTML=`${label} <small>(${key} arrow key)</small>`;
   b.onclick=()=>focusLine(sel+dir);return b;};
 el.appendChild(nav(-1,'▲ move to entry above','up'));
 D.lines.forEach((L,i)=>{
  const r=document.createElement('div');
  const pend=isMobile()&&i===pendJoin;
  r.className='row'+(i===sel?' sel':'')+(pend?' joinpend':'');
  const gid=L.row.group_id;
  if(gid) r.style.borderLeftColor=gcol(gid);
  const b=baseOf(L.row.media_type);
  const badge=L.row.media_type
    ? `<span class="badge" style="background:${b?b.color:'#3a4150'}">${esc(L.row.media_type)}${(L.row.modifiers||[]).length?' + …':''}</span>`
    : '';
  // which cell of its group this line is — the composite draws them in order
  const g=gid?groupAt(i):null;
  const cell=g?`<span class="cellno" style="color:${gcol(gid)}">cell `+
    `${L.row.position}/${g.cells}</span>`:'';
  r.innerHTML=`<span class="idx">${i}</span>`+
   `<span class="dot ${lineDone(L)?'done':''}" title="${lineDone(L)?'media type + search term set':'still needs media type and/or search term'}">${lineDone(L)?'✓':'○'}</span>`+
   badge+cell+
   `<span class="ltxt" data-i="${i}">${esc(L.line)}</span>`+
   `<span class="lterm">${esc(L.row.search_term||'')}</span>`+
   (i>0?`<button class="joinbtn">⤴ join to above</button>`:'')+
   (isMobile()&&i>0?`<button class="mjoin${pend?' confirm':''}">${pend?'✓ confirm join':'⤴ join to above'}</button>`:'');
  r.onclick=e=>{
   if(e.target.closest('.joinbtn,.mjoin'))return;
   if(scissors){openMobileSplit(i);return;}
   focusLine(i);};
  const jb=r.querySelector('.joinbtn');
  if(jb){jb.onmouseenter=()=>joinGhost(i,true);
         jb.onmouseleave=()=>joinGhost(i,false);
         jb.onclick=e=>{e.stopPropagation();doJoin(i);};}
  const mj=r.querySelector('.mjoin');
  if(mj)mj.onclick=e=>{e.stopPropagation();pend?confirmJoin():startJoin(i);};
  const tx=r.querySelector('.ltxt');
  if(!isMobile()){
    tx.onmouseenter=()=>armSplit(tx,i);
    tx.onmouseleave=()=>disarmSplit(tx,i);
  }
  el.appendChild(r);
 });
 el.appendChild(nav(1,'▼ move to entry below','down'));
 if(isMobile()&&pendJoin>0){
   const rows=document.querySelectorAll('#list .row');
   const prevTx=rows[pendJoin-1]&&rows[pendJoin-1].querySelector('.ltxt');
   if(prevTx&&!prevTx.querySelector('.ghostadd'))
     prevTx.insertAdjacentHTML('beforeend',` <span class="ghostadd">${esc(D.lines[pendJoin].line)}</span>`);
 }
}
// ---- inline splitting on the left ------------------------------------------
function armSplit(tx,i){
 if(tx.dataset.armed)return;
 tx.dataset.armed='1'; tx.classList.add('expand');
 const line=D.lines[i].line;
 tx.innerHTML=[...line].map((c,k)=>`<span class="ch" data-k="${k}">${esc(c)}</span>`).join('');
 tx.onmousemove=e=>{
   // the split zone runs exactly to the LAST CHARACTER: past it (on its own
   // visual line) the caret cancels, so the space after the end of the text
   // is the click-to-select area — always lined up with where the text
   // really ends. wrapped middle lines are splittable across their full
   // width (their text runs to the wrap, so there is no dead zone).
   let limit=Infinity;
   const lastCh=tx.children[line.length-1];
   if(lastCh){
     const lr=lastCh.getBoundingClientRect();
     if(e.clientY>=lr.top&&e.clientY<=lr.bottom)limit=lr.right;
   }
   if(e.clientX>limit){
     $('#caret').style.display='none';$('#splittip').style.display='none';
     tx.dataset.b='';return;}
   const t=document.elementFromPoint(e.clientX,e.clientY);
   if(!t||t.dataset.k===undefined)return;
   const rect=t.getBoundingClientRect();
   let b=+t.dataset.k + (e.clientX>rect.left+rect.width/2?1:0);
   b=Math.max(1,Math.min(b,line.length-1));
   const ref=tx.children[Math.min(b,line.length-1)].getBoundingClientRect();
   const x=b<line.length?ref.left:ref.right;
   const c=$('#caret');
   c.style.display='block';c.style.left=x+'px';c.style.top=ref.top+'px';c.style.height=ref.height+'px';
   const tip=$('#splittip');
   tip.style.display='block';tip.style.left=(x+10)+'px';tip.style.top=(ref.top-28)+'px';
   tx.dataset.b=b;
 };
 tx.onclick=async e=>{
   const b=+tx.dataset.b||0;
   if(b<1)return;              // no break point armed -> bubbles -> selects
   e.stopPropagation();
   await doSplit(line,b);
 };
}
function disarmSplit(tx,i){
 if(!tx.dataset.armed)return;
 delete tx.dataset.armed; delete tx.dataset.b;
 tx.classList.remove('expand');
 tx.onmousemove=null; tx.onclick=null;
 tx.textContent=D.lines[i].line;
 $('#caret').style.display='none';$('#splittip').style.display='none';
}
// ---- change-report reasons ---------------------------------------------------
// NOTHING IS WRITTEN TO THE CHANGE REPORT UNTIL YOU CLICK "save reason".
// EVERY action offers one: splits, joins, tagging a media type (the first one
// on a line as much as a re-tag), search-term edits, decorate/caption/group,
// data forms, stamp picks. The server prepares the entry (full sentence(s) +
// before/after) and hands back the QUEUE of changes nobody has explained yet;
// the overlay offers them bottom-right, newest first, and never throws one
// away just because you carried on working — tag a line, type its term, walk
// on to the next one, and each of those is still waiting to be explained.
// Type why and click save reason (POST /reason) and THAT is what writes it to
// manual_tagging_changes_report.txt. Skip (POST /dismiss), or leave it hanging
// when you finish, and the entry never reaches the file.
let whyQ=[];            // [{id,label}] un-explained changes, oldest first
let whyId=null;         // the one the card is offering right now
function setPending(list){whyQ=list||[];renderWhy();}
function whyOpen(){const b=$('#whybody');return !!b&&b.style.display==='block';}
function renderWhy(){
 const c=$('#whycard');
 if(!whyQ.length){whyId=null;c.style.display='none';c.innerHTML='';return;}
 // A reason being typed is never yanked away: while the box is open it keeps
 // offering the change it was opened for, and anything that lands meanwhile
 // simply queues up behind it (the counter says how many).
 if(whyOpen()&&whyId!==null&&whyQ.some(p=>p.id===whyId)){renderWhyMore();return;}
 const top=whyQ[whyQ.length-1];
 whyId=top.id;
 c.classList.remove('saved');
 c.innerHTML=`<div class="wtitle" onclick="expandWhy()">✎ `+
  `<span class="wkind">${esc(top.label)}</span></div>`+
  `<div class="whyhint" onclick="expandWhy()">click (or press <b>space</b>) `+
  `to say why you did this (optional)</div>`+
  `<div class="whymore"></div>`+
  `<div id="whybody"><input id="whytext" placeholder="why did you do this?…">`+
  `<button onclick="sendWhy()">save reason</button>`+
  `<button class="wskip" onclick="skipWhy()">skip</button></div>`;
 c.style.display='block';
 renderWhyMore();
}
// "+3 more changes waiting" — the queue behind the one on offer
function renderWhyMore(){
 const m=document.querySelector('#whycard .whymore'); if(!m)return;
 const n=whyQ.filter(p=>p.id!==whyId).length;
 m.innerHTML=n?`<b>+${n}</b> more change${n===1?'':'s'} waiting for a why`+
   `<span class="skipall" onclick="skipAllWhy()">· skip all</span>`:'';
}
function expandWhy(){
 const b=$('#whybody'); if(!b)return;
 b.style.display='block';
 const t=$('#whytext'); t.focus();
 t.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();sendWhy();}
                 if(e.key==='Escape'){skipWhy();}};
}
// the offer for THIS change goes away (nothing is written) and the next one
// in the queue is offered straight away
async function skipWhy(){
 if(whyId===null){setPending(whyQ);return;}
 const id=whyId; whyId=null;
 const j=await postJSON('/dismiss',{change_id:id});
 setPending(j?j.pending:whyQ.filter(p=>p.id!==id));
}
async function skipAllWhy(){
 whyId=null;
 const j=await postJSON('/dismiss',{all:true});
 setPending(j?j.pending:[]);
}
async function sendWhy(){
 const t=$('#whytext'); const reason=t?t.value.trim():'';
 if(!reason||whyId===null){skipWhy();return;}
 const id=whyId; whyId=null;
 const j=await postJSON('/reason',{change_id:id,reason});
 const c=$('#whycard');
 c.classList.add('saved');
 c.innerHTML='<div class="wtitle">reason saved ✓</div>';
 // a beat to read the tick, then the next un-explained change is offered
 setTimeout(()=>{c.classList.remove('saved');
                 setPending(j?j.pending:whyQ.filter(p=>p.id!==id));},900);
}
async function postJSON(url,body){
 try{
  const r=await fetch(url,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return await r.json();
 }catch(e){return null;}
}
async function doSplit(line,b){
 await post('/split',{line,index:b},line.slice(0,b).trim());
 $('#caret').style.display='none';$('#splittip').style.display='none';
}
// ---- joining ----------------------------------------------------------------
function joinGhost(i,on){
 const rows=document.querySelectorAll('#list .row');
 const cur=rows[i], prev=rows[i-1];
 if(!cur||!prev)return;
 cur.classList.toggle('joinghost',on);
 const t=prev.querySelector('.ltxt');
 const g=prev.querySelector('.ghostadd');
 if(on&&!g) t.insertAdjacentHTML('beforeend',` <span class="ghostadd">${esc(D.lines[i].line)}</span>`);
 if(!on&&g) g.remove();
}
async function doJoin(i){
 if(i<=0)return;
 await post('/join',{line:D.lines[i].line},D.lines[i-1].line);
}
function startJoin(i){
 // MODAL: nothing else can happen until the user confirms or cancels.
 // State-driven: renderList paints the green in-place confirm button (and
 // the ghost preview) FROM pendJoin, so any re-render in between — a saved
 // term reloading, anything — keeps the confirm state instead of quietly
 // reverting the button to plain blue underneath the shield.
 pendJoin=i;
 renderList();
 $('#joinshield').classList.add('open');
 $('#joinconfirm').classList.add('open');
}
function joinWarn(){
 toast('confirm or cancel the join first','#e6c15a');
 const btn=document.querySelector('.mjoin.confirm');
 if(btn){btn.classList.remove('nudgeit');void btn.offsetWidth;btn.classList.add('nudgeit');}
}
function cancelJoin(){
 pendJoin=-1;
 $('#joinshield').classList.remove('open');
 $('#joinconfirm').classList.remove('open');
 renderList();                       // restores the button + clears the ghost
}
async function confirmJoin(){
 const i=pendJoin; pendJoin=-1;
 $('#joinshield').classList.remove('open');
 $('#joinconfirm').classList.remove('open');
 await doJoin(i);
}
// ---- mobile scissors flow -----------------------------------------------------
function toggleScissors(){scissors=!scissors;$('#mscis').classList.toggle('on',scissors);
 $('#mscis').textContent=scissors?'now tap the line you want to split':'✂ split a line';}
function openMobileSplit(i){
 scissors=false;$('#mscis').classList.remove('on');
 $('#mscis').textContent='✂ split a line';
 msLine=D.lines[i].line; msB=Math.max(1,Math.round(msLine.length/2));
 $('#msplit').classList.add('open'); renderMs();
 $('#mstext').onclick=e=>{
   const t=e.target.closest('span[data-k]'); if(!t)return;
   const r2=t.getBoundingClientRect();
   msB=Math.max(1,Math.min(+t.dataset.k+(e.clientX>r2.left+r2.width/2?1:0),msLine.length-1));
   renderMs();};
}
function renderMs(){
 // the caret is an INLINE element inside the text, so it is always visible
 let h='';
 [...msLine].forEach((c,k)=>{
   if(k===msB)h+='<span id="mscaret"></span>';
   h+=`<span data-k="${k}">${esc(c)}</span>`;});
 $('#mstext').innerHTML=h;
}
function nudge(d){msB=Math.max(1,Math.min(msB+d,msLine.length-1));renderMs();}
function closeMobileSplit(){$('#msplit').classList.remove('open');msLine=null;}
async function mobileSplitGo(){
 const line=msLine,b=msB; closeMobileSplit();
 await doSplit(line,b);
}
// ---- editor ------------------------------------------------------------------
function focusLine(i){
 flushTermSave();   // leaving a line saves its last keystrokes right away, so
                    // the change (and its why) is the one you just made
 const from=sel;
 sel=Math.max(0,Math.min(i,D.lines.length-1));
 step='media'; kbType=-1; ghostDismissed=false;   // fresh line: ghost is
                                                  // offered again by default
 // show the tab this line's type lives on, so its button is visibly selected.
 // Only on a LINE change: a plain re-render must not yank the tab back while
 // you are browsing the others.
 const t=tabOf(D.lines[sel].row.media_type); if(t>=0)tab=t;
 closeShort();                       // moving to another line dismisses the panel
 renderList(); renderEditor();
 const r=document.querySelectorAll('#list .row')[sel];
 if(r)r.scrollIntoView({block:'nearest'});
 if(isMobile()) $('#editor').classList.add('open');
 autoJoinGroup(from,sel);            // async, fire-and-forget
}
// The group that line index `i` belongs to, as {size, cells, base} \u2014 or null
// when that line is not a cell. Its members are the lines sharing its
// group_id, which the server derives from the opener/continuation pattern
// (recompute), and the group's BASE \u2014 where every cell's picture comes from \u2014
// is the base of the line that opened it.
function groupAt(i){
 const gid=D.lines[i].row.group_id;
 if(!gid)return null;
 const members=D.lines.filter(L=>L.row.group_id===gid);
 if(!members.length)return null;
 const base=members[0].row.media_type;
 const b=baseOf(base);
 return {size:members.length, cells:(b&&b.group_cells)||0, base};
}
// You marked a line as a group cell and stepped onto the next one. The lines
// that JOIN a group are tagged `hold previous` + group \u2014 the group picture
// stays on screen and this line lands one more cell on it \u2014 so if that next
// line is still blank, tag it for you and drop the cursor in its search box
// (every cell brings its own picture, so it still needs its own term). Stops
// once the group has all the cells its layout draws, and never touches a line
// you already tagged.
async function autoJoinGroup(from,to){
 if(to!==from+1)return;                       // only the line directly below
 const P=D.lines[from], L=D.lines[to];
 if(!P||!L)return;
 const g=groupAt(from);
 if(!g)return;                                // the line above is not a cell
 if(L.row.media_type||(L.row.modifiers||[]).length)return;   // not a blank line
 if(g.size>=g.cells){
   toast(`that group is full (${g.cells} cells) \u2014 this line starts `+
         `something new`,'#e6c15a');
   return;
 }
 const cont=D.catalog.group_continuation_type;
 const ok=await post('/save',{line:L.line,
   patch:{media_type:cont,modifiers:['group']}},L.line);
 if(!ok)return;
 expand('term');
 toast(`cell ${g.size+1} of ${g.cells} \u2014 ${cont.replace(/_/g,' ')} + `+
       `group. give it its own search term`);
}
function closeEditor(){$('#editor').classList.remove('open');}
function expand(which){
 step=which;
 applyCollapse();
 const card=$({media:'#mediacard',data:'#datacard',term:'#termcard'}[which]);
 card.classList.remove('attn'); void card.offsetWidth;   // restart animation
 if(which==='term'){
   // refresh + focus in one frame so the ghost/tab-pill always appears,
   // whichever path got us here (button, tab key, header click)
   requestAnimationFrame(()=>{renderTerm();$('#term').focus();updateGhost();});
 }
 if(which==='data'){
   requestAnimationFrame(()=>{
     const first=dataFields(D.lines[sel])[0];
     const el=first&&$('#d_'+first.name);
     if(el)el.focus();
   });
 }
}
function applyCollapse(){
 $('#mediacard').classList.toggle('collapsed',step!=='media');
 $('#datacard').classList.toggle('collapsed',step!=='data');
 $('#termcard').classList.toggle('collapsed',step!=='term');
}
function flash(id){const c=$(id);c.classList.remove('attn');void c.offsetWidth;c.classList.add('attn');}
function renderEditor(){
 const L=D.lines[sel];
 $('#curlinetxt').textContent=L.line;
 renderTabs(); renderTypes(); renderMods(); renderData(); renderTerm();
 const ts=$('#tostep2');
 ts.style.display=L.row.media_type?'inline-block':'none';
 ts.classList.toggle('glow',!!L.row.media_type);
 applyCollapse();
}
// step 2 is the data form for the types that have one, the search term for the
// rest — `nextStep` is what "continue to step 2" actually means for this line
function nextStep(){
 const L=D.lines[sel];
 return dataFields(L).length?'data':'term';
}
function renderData(){
 const L=D.lines[sel]; const fields=dataFields(L);
 $('#datacard').style.display=fields.length?'block':'none';
 if(!fields.length)return;
 const d=L.row.data||{};
 $('#datapanel').innerHTML=fields.map(f=>
   `<label class="dfield"><span class="dlbl">${esc(f.label)}`+
   `${f.required?'':' <i>(optional)</i>'}</span>`+
   `<input id="d_${f.name}" type="${['text','series','shares'].includes(f.kind)?'text':'number'}"`+
   ` step="${['number','percent'].includes(f.kind)?'any':'1'}"`+
   ` value="${esc(String(d[f.name]??''))}" placeholder="${esc(f.placeholder)}">`+
   `<span class="dhelp">${esc(f.help)}</span></label>`).join('');
 fields.forEach(f=>{
  const el=$('#d_'+f.name);
  el.addEventListener('input',debSaveData);
  el.addEventListener('keydown',e=>{
    if(e.key==='Enter'){e.preventDefault();el.blur();saveData();}
  });
 });
}
function collectData(){
 const out={};
 dataFields(D.lines[sel]).forEach(f=>{
  const el=$('#d_'+f.name);
  const v=el?el.value.trim():'';
  if(v!=='')out[f.name]=v;      // the server coerces to the field's kind
 });
 return out;
}
async function saveData(){
 await post('/save',{line:D.lines[sel].line,patch:{data:collectData()}},
            D.lines[sel].line,true);
}
let dtmr=null;
function debSaveData(){clearTimeout(dtmr);dtmr=setTimeout(saveData,450);}
// ---- media-type tabs (material / maths / …) ---------------------------------
// The tabs, their headings and which types sit under them all come from the
// catalog (CONFIG.MEDIA_TYPE_TABS), so a new maths type appears here on its own.
function tabOf(name){
 const b=baseOf(name); if(!b)return -1;
 return D.catalog.tabs.findIndex(t=>t.columns.some(c=>b.tags.includes(c.tag)));
}
function setTab(i){
 const n=D.catalog.tabs.length;
 tab=((i%n)+n)%n; kbType=-1;
 renderTabs(); renderTypes();
}
function moveTab(d){setTab(tab+d);}
function renderTabs(){
 const T=D.catalog.tabs;
 $('#tabbar').style.display=T.length>1?'flex':'none';
 $('#tabdots').innerHTML=T.map((t,i)=>
   `<span class="tdot${i===tab?' on':''}" title="${esc(t.label)}"
     onclick="event.stopPropagation();setTab(${i})"></span>`).join('');
 $('#tabname').textContent=T[tab].label;
}
let kbAvail=[];
function renderTypes(){
 const L=D.lines[sel];
 let html=''; kbAvail=[];
 for(const col of D.catalog.tabs[tab].columns){
  html+=`<div class="col"><h4>${esc(col.title)}</h4>`;
  D.catalog.bases.filter(b=>b.tags.includes(col.tag)).forEach(b=>{
   const disabled=(col.tag==='edit_previous'&&sel===0);
   if(!disabled)kbAvail.push(b.name);
   const on=L.row.media_type===b.name?' on':'';
   const kb=(kbType>=0&&kbAvail[kbType]===b.name&&!disabled)?' kb':'';
   html+=`<button class="tpl${on}${kb}" ${disabled?'disabled':''} style="background:${b.color}" onclick="pick('${b.name}')">`+
     `<span>${b.label}</span><i class="info" data-info="${b.name}">i</i></button>`;});
  html+='</div>';
 }
 $('#typepanel').innerHTML=html;
 hookInfos($('#typepanel'));
}
function modAllowed(m,b){
 // not available for this base -> not offered at all (no disabled+warning)
 // group is offered both on the bases that can OPEN one and on hold previous,
 // which is how a line JOINS the group above it.
 if(m.name==='group')return !!(b&&(b.groupable||b.group_continues));
 if(m.name==='collage')return !!(b&&b.collageable);
 return true;
}
function renderMods(){
 const L=D.lines[sel]; const has=!!L.row.media_type;
 const b=baseOf(L.row.media_type);
 const avail=has?D.catalog.modifiers.filter(m=>modAllowed(m,b)):D.catalog.modifiers;
 $('#modbar').innerHTML=avail.map(m=>{
  const on=(L.row.modifiers||[]).includes(m.name)?' on':'';
  return `<button class="mod${on}" ${has?'':'disabled'} onclick="toggleMod('${m.name}')">${m.label}`+
         ` <i class="info" data-info="${m.name}">i</i></button>`;}).join('');
 $('#modhint').textContent=has?'':'pick a base first — there must be something to put these on';
 hookInfos($('#modbar').parentElement);
}
// ---- search-term changes -----------------------------------------------------
// The term saves on a per-keystroke debounce, and the SERVER folds those saves
// together: every keystroke of one edit updates the same pending offer (its
// "before" stays the term you started from), so the overlay shows one
// "Line 12 — search term changed" for the edit, not one per letter. Typing an
// edit and then typing it back to what it was cancels the offer.
function renderTerm(){
 const L=D.lines[sel];
 const isTypo=L.row.media_type==='typography';
 // timeline & friends: the term is never read, so the card is not offered
 $('#termcard').style.display=usesTerm(L)?'block':'none';
 $('#term').value=L.row.search_term||'';
 $('#term').readOnly=isTypo;
 const b=baseOf(L.row.media_type);
 $('#termreq').textContent=isTypo?"auto-set to this line's own text"
   :isCell(L)?"required — this cell's own picture"
   :(b&&b.term_optional)?'optional for this type'
   :'required';
 updateGhost();
 renderChips();
 renderStamp(L);
 renderNext(L);
}
// Only the suggestion chips + pronoun panel. Split out of renderTerm so the
// background-recommender poll can refresh them WITHOUT touching #term —
// re-rendering the whole step would wipe out whatever you were typing.
function renderChips(){
 const L=D.lines[sel];
 const s=L.suggest; let chips='';
 const add=(lbl,arr,cls)=>{if(arr&&arr.length){chips+=`<span class="chiplbl">${lbl}:</span>`+
   arr.map(w=>`<button tabindex="-1" class="${cls}" onclick="chip('${esc(w).replace(/'/g,"\\'")}')">${esc(w)}</button>`).join('');}};
 // memory-based recommendations (VISUAL_RECOMMENDER): combos first — a
 // current word paired with something remembered ("Jar of Nutmeg") — then
 // high-scoring words from previous entries, each with its score.
 const R=L.recommend||{};
 if(R.pairs&&R.pairs.length){chips+='<span class="chiplbl">combos:</span>'+
   R.pairs.map(p=>`<button tabindex="-1" class="pair" title="${esc(p.why||'')}"
     onclick="chip('${esc(p.term).replace(/'/g,"\\'")}')">✦ ${esc(p.term)}</button>`).join('');}
 if(R.singles&&R.singles.length){chips+='<span class="chiplbl">from memory:</span>'+
   R.singles.map(x=>`<button tabindex="-1" class="rec" title="${esc(x.why||'')}"
     onclick="chip('${esc(x.term).replace(/'/g,"\\'")}')">${esc(x.term)}<small>${x.score.toFixed(1)}</small></button>`).join('');}
 add('places',s.places,'big place'); add('names',s.names,'big place');
 add('nouns',s.nouns,'big'); add('keywords',s.keywords,'small'); add('words',s.words,'small');
 if(D.recommend_status&&D.recommend_status.state==='loading')
   chips+='<span class="recupdating">updating suggestions\u2026</span>';
 $('#chips').innerHTML=chips||'<span class="chiplbl">(no extracted suggestions for this line)</span>';
 renderPronouns(R.pronouns||[]);
 scheduleRecPoll();
}
// Every edit kicks a background recompute, so the /data that load() just
// fetched almost always says "loading". Nothing ever asked again, so the chip
// sat there until the NEXT edit happened to catch a ready worker — which reads
// as "updating suggestions..." forever the moment you stop editing and look at
// the chips. Poll until ready, then swap the chips in place.
let recPoll=null;
function scheduleRecPoll(){
 if(recPoll)return;
 if(!(D.recommend_status&&D.recommend_status.state==='loading'))return;
 recPoll=setTimeout(async()=>{
  recPoll=null;
  let N;
  try{N=await (await fetch('/data')).json();}catch(e){scheduleRecPoll();return;}
  // merge ONLY the suggestion payload. The rows are deliberately left alone:
  // a save may be in flight, and #term may hold half-typed text.
  const by={}; N.lines.forEach(L=>{by[L.line]=L;});
  D.lines.forEach(L=>{const n=by[L.line]; if(n){L.recommend=n.recommend; L.suggest=n.suggest;}});
  D.recommend_status=N.recommend_status;
  if(step==='term'&&D.lines[sel])renderChips(); else scheduleRecPoll();
 },1000);
}
function renderPronouns(rows){
 // the small scrollable panel:  'it' (2) → saucepan (0.20)
 const box=$('#pronounbox');
 if(!rows.length){box.style.display='none';box.innerHTML='';return;}
 box.innerHTML='<span class="plbl">this line\u2019s abstract words probably point at\u2026 (click to use)</span>'+
   rows.map(r=>{
     const n=r.occurrence>1?` (${r.occurrence})`:'';
     return `<div class="prow" onclick="chip('${esc(r.candidate).replace(/'/g,"\\'")}')">
       <span class="pn">'${esc(r.pronoun)}'${n}</span><span class="arrow">\u2192</span>
       <span class="cand">${esc(r.candidate)}</span><span class="pp">(${r.prob.toFixed(2)})</span></div>`;
   }).join('');
 box.style.display='block';
}
function renderStamp(L){
 const card=$('#stampcard'); const need=stampNeeded(L);
 card.style.display=need?'block':'none';
 if(!need)return;
 const deco=L.row.stamp_decorate?' on':'';
 $('#stamppanel').innerHTML=D.catalog.bases.filter(b=>b.stampable).map(b=>{
  const on=L.row.stamp_source===b.name?' on':'';
  return `<button class="tpl${on}" style="background:${b.color}" onclick="pickStamp('${b.name}')">`+
    `<span>${b.label}</span><i class="info" data-info="${b.name}">i</i></button>`;}).join('')
  +`<button class="tpl${deco}" style="background:#7a4a8a" onclick="toggleStampDeco()">`+
   `<span>✎ decorate the pick first</span><i class="info" data-info="_stampdeco">i</i></button>`;
 hookInfos($('#stamppanel'));
}
async function pickStamp(name){const L=D.lines[sel];
 await post('/save',{line:L.line,patch:{stamp_source:name}},L.line);
 // once the stamp source is chosen the line is usually DONE — behave like
 // step 2: send focus back to the term field so the next Tab jumps to the
 // next line instead of cycling through the remaining stamp buttons.
 const done=lineDone(D.lines[sel]);
 const t=$('#term');
 if(done){ if(t){t.focus();} }
 else if(t){t.focus();}
}
async function toggleStampDeco(){const L=D.lines[sel];
 await post('/save',{line:L.line,patch:{stamp_decorate:!L.row.stamp_decorate}},L.line);}
function renderNext(L){
 const nb=$('#nextbtn');
 if(!lineDone(L)){nb.style.visibility='hidden';return;}
 nb.style.visibility='visible';
 const all=D.lines.every(lineDone);
 if(all){nb.className='fin';nb.textContent='✓ finish';
   nb.onclick=()=>showFinish();}
 else if(sel>=D.lines.length-1){nb.className='amber';
   nb.textContent='not all items are done — continue to next incomplete item →';
   nb.onclick=()=>focusLine(D.lines.findIndex(x=>!lineDone(x)));}
 else{nb.className='';nb.textContent='✓ continue to next line →';
   nb.onclick=()=>focusLine(sel+1);}
}
function updateGhost(){
 const L=D.lines[sel]; const g=$('#ghost');
 const focused=document.activeElement===$('#term');
 if(!$('#term').value && L.suggest.ghost && focused){
   g.innerHTML=esc(L.suggest.ghost)+'<span id="tabpill">tab ⇥ accept · → skip</span>';
   g.style.display='block';
 } else g.style.display='none';
}
$('#term').addEventListener('focus',updateGhost);
$('#term').addEventListener('blur',()=>setTimeout(updateGhost,120));
$('#term').addEventListener('blur',flushTermSave);
$('#term').addEventListener('input',()=>{updateGhost();debSave();});
// dismiss the ghost suggestion WITHOUT accepting it: press → (right arrow)
// while the field is empty. Handy when you're e.g. just decorating the
// previous scene and don't want the auto-suggested term at all.
$('#term').addEventListener('keydown',e=>{
 if(e.key==='ArrowRight' && !$('#term').value && D.lines[sel].suggest.ghost){
   ghostDismissed=true; $('#ghost').style.display='none'; e.preventDefault();
   return;
 }
 if(e.key==='Tab'&&e.shiftKey){e.preventDefault();expand('media');return;}
 if(e.key!=='Tab')return;
 // accept the ghost on Tab ONLY if it hasn't been dismissed this visit
 if(!$('#term').value&&D.lines[sel].suggest.ghost&&!ghostDismissed){
   e.preventDefault();$('#term').value=D.lines[sel].suggest.ghost;
   updateGhost();debSave();return;}
 const L=D.lines[sel];
 // line already satisfied (has type + either a term or an optional-term
 // type, and any needed stamp source is chosen) -> Tab goes to next line,
 // exactly like finishing step 2
 if(lineDone(L)){
   e.preventDefault();$('#nextbtn').click();return;}
 if(L.row.media_type&&$('#term').value.trim()){
   e.preventDefault();
   if(stampNeeded(L,$('#term').value)&&!L.row.stamp_source){
     flash('#stampcard');                     // step 3 still needs a pick
     $('#stampcard').style.display='block';
   } else $('#nextbtn').click();}   // straight to the next line
});
function chip(w){const t=$('#term');t.value=(t.value?t.value+' ':'')+w;updateGhost();debSave();}
let tmr=null;
function debSave(){clearTimeout(tmr);tmr=setTimeout(()=>{
 tmr=null;
 post('/save',{line:D.lines[sel].line,patch:{search_term:$('#term').value}},
      D.lines[sel].line,true);
 if(!D.lines[sel].row.media_type)flash('#mediacard');},400);}
// Leaving the box (or the line) must not leave the last keystrokes waiting in
// that 400ms timer: save them NOW so the change — and the why it is offered
// for — belongs to the line you were actually typing on. Deliberately no
// reload: we are usually mid-render for another line, and pulling the rows out
// from under it would fight the caret. The pending queue is still refreshed.
function flushTermSave(){
 if(tmr!==null){clearTimeout(tmr);tmr=null;}
 const L=D.lines[sel], t=$('#term');
 if(!L||!t||(L.row.search_term||'')===t.value)return;
 L.row.search_term=t.value;                  // keep the line list in step
 postJSON('/save',{line:L.line,patch:{search_term:t.value}})
   .then(j=>{if(j&&j.ok)setPending(j.pending);});
}
async function pick(name){const L=D.lines[sel];
 const b=baseOf(name);
 // BRAND-NEW footage on a line too short to stand on its own would just
 // flash — offer the fix-it options instead of applying it (task 11).
 // A cell of a group never flashes: the composite it belongs to stays up for
 // the group's whole run, so the short-line guard does not apply to it.
 const grouped=(L.row.modifiers||[]).includes('group')&&b&&b.groupable;
 if(b&&b.new_footage&&L.words<D.min_new_words&&!grouped){openShort(name);return;}
 closeShort();                       // a fine choice — drop any open guard panel
 // CHANGING an existing type prepares a report entry (the server diffs it)
 // and offers it through the bottom-right reason overlay — it is only
 // written if the user clicks "save reason". First-time tagging isn't logged.
 await post('/save',{line:L.line,patch:{media_type:name}},L.line);
 const t2=$('#tostep2');
 t2.style.display='inline-block'; t2.classList.add('glow');
 // a type drawn from data (timeline) sends you to its form, not to a search
 // box it will never read
 if((b&&b.data_fields||[]).length){expand('data');flash('#datacard');}
 else flash('#termcard');}
// ---- too-short-for-new-footage panel (task 11) ------------------------------
let shortPend=null;   // {name, mods} of the new type the user tried to pick
function openShort(name){
 const L=D.lines[sel];
 shortPend={name, mods:(L.row.modifiers||[]).slice()};
 const need=Math.max(1,D.min_new_words-L.words);
 const first=sel===0, last=sel>=D.lines.length-1;
 $('#shortmsg').innerHTML=
   `That sentence is too short to have new footage.`+
   `It will just flash on the screen for the viewer.`+
   `You need <b>${need}</b> more word${need===1?'':'s'} to make the scene `+
   `long enough to stand by itself.`;
 const opt=(cls,label,choice,dis,hoverI)=>
   `<button class="${cls}"${dis?' disabled':''} `+
   `onclick="applyShort('${choice}')"`+
   (hoverI!==undefined&&!dis?` onmouseenter="shortHover(${hoverI},true)" `+
     `onmouseleave="shortHover(${hoverI},false)"`:'')+
   `>${label}${dis?' <i style="opacity:.7">(not available here)</i>':''}</button>`;
 // (6) is only offered for a base that HAS a group layout (stock / ai stock).
 const pb=baseOf(name), canGroup=!!(pb&&pb.groupable);
 $('#shortopts').innerHTML=
   opt('rec','(1) Edit and add to the previous scene  [recommended]',
       'edit_prev',first)+
   opt('','(2) Join this scene to the previous scene, thus making the '+
       'previous scene be on the screen for longer','join_prev',first,sel)+
   opt('','(3) Split the previous scene, join it to the start of this scene',
       'borrow_prev',first)+
   opt('','(4) Make the scene after this scene be an edit of this scene',
       'next_edit',last)+
   opt('','(5) Join [at least part of] the scene after this scene to this '+
       'scene','join_next',last,last?undefined:sel+1)+
   opt('',`(6) Make this the first cell of a group \u2014 the next lines join `+
       `it (hold previous + group) and the ${canGroup?pb.group_cells:3} `+
       `pictures sit on screen together`,'group_start',!canGroup)+
   opt('ovr','(X) Manual override and use quick stock anyway','override',false);
 $('#shortcard').style.display='block';
 $('#shortcard').scrollIntoView({block:'nearest'});
}
function closeShort(){shortPend=null;$('#shortcard').style.display='none';
 // clear any leftover hover preview in the list
 document.querySelectorAll('#list .joinghost').forEach(r=>r.classList.remove('joinghost'));
 document.querySelectorAll('#list .ghostadd').forEach(g=>g.remove());}
function shortHover(i,on){joinGhost(i,on);}   // reuse the join-to-above preview
async function applyShort(choice){
 if(!shortPend)return;
 const L=D.lines[sel];
 // Options 3 and 5 are MANUAL: nothing is auto-applied. We just take the
 // user to the right line with split mode ready and tell them what to do —
 // THEY choose the cut point (and the join), which is the whole point.
 if(choice==='borrow_prev'||choice==='join_next'){
   const target=choice==='borrow_prev'?sel-1:sel+1;
   if(target<0||target>=D.lines.length){
     alert(choice==='borrow_prev'?'there is no previous scene to split'
                                  :'there is no scene after this one');
     return;
   }
   shortPend=null;$('#shortcard').style.display='none';
   const verb=choice==='borrow_prev'
     ? 'Split the <b>previous</b> scene where you like, then use its ↑ join '+
       'button to attach the tail to <b>this</b> scene.'
     : 'Split the <b>next</b> scene where you like, then join the part you '+
       'want back onto <b>this</b> scene with its ↑ join button.';
   focusLine(target);
   showManualHint(verb);
   return;
 }
 // (1)(2)(4)(6)(X) are still one atomic, undoable mutation
 const ok=await post('/shortfix',
   {line:L.line,choice,media_type:shortPend.name,modifiers:shortPend.mods},
   L.line);
 if(ok){shortPend=null;$('#shortcard').style.display='none';
   if(choice==='group_start'){
     expand('term');
     toast('group opened \u2713 \u2014 give this cell its own search term, then move '+
           'down: each blank line below joins the group as hold previous + '+
           'group');
   } else toast('applied \u2713 \u2014 use \u21b6 undo to revert');}
}
function showManualHint(html){
 let h=$('#manualhint');
 if(!h){h=document.createElement('div');h.id='manualhint';
   document.body.appendChild(h);}
 h.innerHTML=`<span class="mh-txt">${html}</span>`+
   `<button onclick="document.getElementById('manualhint').remove()">got it</button>`;
 h.style.display='flex';
}
async function toggleMod(name){const L=D.lines[sel];
 const mods=(L.row.modifiers||[]).slice();
 const k=mods.indexOf(name); if(k>=0)mods.splice(k,1); else mods.push(name);
 await post('/save',{line:L.line,patch:{modifiers:mods}},L.line);}
async function post(url,body,keepLine,quietReload){
 if(url!=='/save')clearTimeout(tmr);   // a pending debounced save would
                                       // post the OLD line text after a
                                       // split/join => "unknown line"
 const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify(body)});
 const j=await r.json().catch(()=>({}));
 if(!r.ok){
   if((j.error||'')==='unknown line'){
     // the browser's list was out of date (an edit landed while this
     // click was in flight) — resync silently and let the user retry
     await load(keepLine);
     alert('the line list was out of date and has been refreshed \u2014 please try that again');
     return false;
   }
   alert(j.error||'error');return false;}
 toast();
 // the overlay mirrors the server's queue of un-explained changes: this
 // mutation's own offer joins it (per-keystroke term/data saves fold into the
 // offer already there rather than piling up), and nothing already waiting is
 // thrown away just because another change happened.
 setPending(j.pending);
 if(quietReload){
   // Keep whatever the user is typing in: the reload rebuilds these elements,
   // so remember the focused field BY ID and put its value + caret back. (The
   // search box and every data input go through here.)
   const cur=document.activeElement;
   const id=cur&&cur.id, val=cur&&cur.value, pos=cur&&cur.selectionStart;
   await load(keepLine);
   const back=id&&document.getElementById(id);
   if(back){back.value=val; back.focus();
            try{back.setSelectionRange(pos,pos);}catch(e){}}
   updateGhost();          // re-evaluate ghost/tab-pill for the restored value
 } else await load(keepLine);
 return true;
}
// ---- info cards ---------------------------------------------------------------
function cardHTML(name){
 const all=[...D.catalog.bases,...D.catalog.modifiers];
 const e=all.find(x=>x.name===name);
 if(name==='_types')return '<div class="nm">media type</div><div class="hint">every line needs exactly one base media type. NEW fetches brand-new material; EDIT PREVIOUS reuses or changes the image already on screen. colours show the family — ai in reds.</div>';
 if(name==='_mods')return '<div class="nm">stack on top</div><div class="hint">optional extras layered onto the base you picked: decorate (draw on it), caption (text on it), group (this line is one cell of a multi-cell group). to build a group: OPEN it on the first line with stock (or ai stock) + group, then CONTINUE it on each following line with hold previous + group — their pictures share the screen, one cell appearing per line. every cell needs its own search term, because every cell is its own picture.</div>';
 if(name==='_stamp')return '<div class="nm">stamp pictures from</div><div class="hint">hold previous/background + decorate + a search term: the term describes what to STAMP ("jar of nutmeg"). the scene joins the NORMAL candidates review as this type — you click the picture you want there, and it waits ready (and active) in the decorate editor\'s STAMP tab.</div>';
 if(name==='_stampdeco')return '<div class="nm">decorate the pick first</div><div class="hint">after you click your pick in the review, it opens in the decorator ON ITS OWN first — cut it out, remove its background, clean it up — BEFORE it\'s offered as a stamp.</div>';
 if(!e)return '';
 return `<div class="icard"><img src="/example/${e.name}" onerror="this.src='${PLACEHOLDER}'">`+
   `<div><div class="nm">${e.label||e.name}</div><div class="hint">${esc(e.info)}</div></div></div>`;
}
function hookInfos(root){
 root.querySelectorAll('.info').forEach(el=>{
  const show=e=>{e.stopPropagation();$('#pop').innerHTML=cardHTML(el.dataset.info);$('#pop').style.display='block';};
  el.onmouseenter=show;
  el.onmouseleave=()=>{$('#pop').style.display='none';};
  el.onclick=show;
 });
}
function renderKey(){
 $('#keycards').innerHTML=[...D.catalog.bases,...D.catalog.modifiers]
   .map(e=>cardHTML(e.name)).join('');
}
function finish(){
 const bad=D.lines.findIndex(L=>!lineDone(L));
 if(bad>=0){focusLine(bad);
   toast('not finished yet — this line still needs tagging','#e6c15a');return;}
 showFinish();
}
function showFinish(){$('#finwrap').classList.add('open');
 fetch('/finish',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(()=>{});
 // best-effort: browsers only allow a script to close a tab it opened
 // itself, so this silently no-ops in most desktop browsers — the
 // fallback message in the modal above covers that case.
 setTimeout(()=>{window.close();},300);}
function hideFinish(){$('#finwrap').classList.remove('open');closeEditor();}
document.addEventListener('keydown',e=>{
 if(e.target.tagName==='TEXTAREA'||e.target.tagName==='INPUT')return;
 // space = shortcut for "click the ✎ card" — opens the why box and focuses it,
 // so a reason can be typed without reaching for the mouse.
 if((e.key===' '||e.key==='Spacebar')&&whyId!==null&&$('#whycard').style.display==='block'){
   e.preventDefault();expandWhy();return;}
 if(e.key==='ArrowDown'){focusLine(sel+1);e.preventDefault();}
 else if(e.key==='ArrowUp'){focusLine(sel-1);e.preventDefault();}
 else if(step==='media'&&(e.key==='['||e.key===']')){
   moveTab(e.key===']'?1:-1);e.preventDefault();}
 else if(step==='media'&&(e.key==='ArrowLeft'||e.key==='ArrowRight')&&kbAvail.length){
   kbType=kbType<0?0:(kbType+(e.key==='ArrowRight'?1:-1)+kbAvail.length)%kbAvail.length;
   renderTypes();e.preventDefault();}
 else if(step==='media'&&e.key==='Enter'&&kbType>=0&&kbAvail[kbType]){
   pick(kbAvail[kbType]);e.preventDefault();}
 else if(e.key==='Tab'&&!e.shiftKey&&step==='media'
         &&D.lines[sel].row.media_type){expand(nextStep());e.preventDefault();}
});
document.addEventListener('click',e=>{if(!e.target.closest('.info,#pop'))$('#pop').style.display='none';});
// ---- boot with a loading screen ------------------------------------------
// /data itself is instant; recommend_status says whether the background
// suggestion worker has finished its first pass.  Until then: overlay +
// countdown (server estimates from recommender_timings.json history).
let etaLeft=0, etaTick=null;
function showEta(){
 const el=$('#leta');
 el.textContent = etaLeft>0 ? `about ${etaLeft}s left` : 'almost done\u2026';
}
async function boot(){
 D=await (await fetch('/data')).json();
 const st=D.recommend_status||{state:'ready'};
 if(st.state==='ready'){ $('#loadscreen').style.display='none';
   sel=0; renderKey(); renderList(); renderEditor(); updateFinish();
   setPending(D.pending); return; }
 etaLeft=st.eta_seconds||3; showEta();
 etaTick=setInterval(()=>{etaLeft=Math.max(0,etaLeft-1);showEta();},1000);
 const poll=async()=>{
   try{ D=await (await fetch('/data')).json(); }catch(e){}
   const s=(D.recommend_status||{}).state;
   if(s==='ready'){
     clearInterval(etaTick); $('#loadscreen').style.display='none';
     sel=0; renderKey(); renderList(); renderEditor(); updateFinish();
     setPending(D.pending);
   } else {
     if(D.recommend_status&&D.recommend_status.eta_seconds<etaLeft)
       etaLeft=D.recommend_status.eta_seconds;
     setTimeout(poll,1000);
   }
 };
 setTimeout(poll,1000);
}
boot();
</script></body></html>'''


def run_manual_tagging(json_path: Path, port: int = 0,
                        auto_open_browser: bool = True) -> None:
    """Blocking helper for embedding (main.py): opens the tagging page and
    returns once the user clicks finish in the browser (every line done),
    or Ctrl-C. Unlike main() below, the server runs in a background thread
    so the finish button can shut it down instead of running forever."""
    server = make_server(json_path, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"manual tagging: {json_path.name}\n  open {url}  "
          f"(finish in the browser, or Ctrl-C here)")
    if auto_open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        server.state.finished_event.wait()
    except KeyboardInterrupt:
        print("\n  stopped manually. edits are already saved.")
    finally:
        server.shutdown()
        thread.join(timeout=5)
    print("  manual tagging done — resuming pipeline.")


def main() -> None:
    json_path = _pick_json(sys.argv[1] if len(sys.argv) > 1 else None)
    server = make_server(json_path)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"manual tagging: {json_path.name}\n  open {url}  (Ctrl-C to stop)")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped. edits are already saved.")


if __name__ == "__main__":
    main()
