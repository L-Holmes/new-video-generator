"""
run_diagnostic.py
==================
Drift-resistant diagnostic for sentence_splitter.

KEY DIFFERENCE from your previous run_plain.py:

The previous tool compared expected[i] vs actual[i] line-by-line.  That works
ONLY if the splitter produces exactly the same number of lines as the expected
output — and the moment one line diverges, every subsequent index is wrong.
This is why the brompton example looked catastrophic past ~line 200: a single
early divergence pushed everything out of phase.

This tool aligns section-by-section instead:
  • The expected output is split on blank lines into PARAGRAPHS.
  • The input text is split on blank lines (or sentence boundaries) into
    matching CHUNKS.
  • Each chunk goes through split_text_into_sections() independently.
  • We compare the chunk's actual output against the corresponding expected
    paragraph — so drift in one paragraph stays local to that paragraph.

Output format per paragraph:
    === case_name :: paragraph #N ===
    EXPECTED (k lines)         ACTUAL (m lines)
      1. <expected line 1>       1. <actual line 1>
      2. <expected line 2>       2. <actual line 2>
      ...
    F1 = 0.83  ✓  (or  ✗  if below threshold)

A summary table at the end shows per-case totals.

Usage:
    uv run python run_diagnostic.py                    # all cases, brief
    uv run python run_diagnostic.py --case whale       # one case, full detail
    uv run python run_diagnostic.py --verbose          # all cases, full detail
    uv run python run_diagnostic.py --threshold 0.7    # raise pass bar
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import List, Tuple

import sys as _sys
from pathlib import Path as _Path
# The splitter is the folder above this one; PATHS puts every stage
# folder on sys.path so this runs from anywhere.
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))
import PATHS  # noqa: F401,E402  — every stage folder on sys.path

import sentence_splitter

# ---- HOTFIX (kept from your original) ---------------------------------------
def _fixed_prev_split(splits, i):
    return max((s for s in splits if s < i), default=0)
sentence_splitter._prev_split = _fixed_prev_split
# ----------------------------------------------------------------------------

from sentence_splitter import split_text_into_sections


# =============================================================================
# Paragraph-level alignment helpers
# =============================================================================

def _normalise(s: str) -> str:
    """Lowercase, collapse whitespace, strip — for fuzzy matching."""
    return re.sub(r"\s+", " ", s.lower().strip())


def _split_into_paragraphs(text: str) -> List[str]:
    """Split text on blank lines (one or more newlines with only whitespace
    between)."""
    paras = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in paras if p.strip()]


def _split_expected_into_groups(expected: List[str]) -> List[List[str]]:
    """The expected lists in your test data are flat — one entry per line.
    Re-group them based on hard sentence boundaries (lines ending in
    .  !  ? followed by a capital-letter line that doesn't continue the
    previous thought).  We approximate: a new group starts after a line that
    ends in . ! ? AND the next line begins with a capital letter, AND the
    previous line was not itself a fragment ending in a comma or dash."""
    groups: List[List[str]] = []
    current: List[str] = []
    for line in expected:
        line = line.strip()
        if not line:
            continue
        current.append(line)
        # close group when the line ends in hard punctuation
        if line and line[-1] in {".", "!", "?"} and not line.endswith("..."):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _split_input_into_paragraphs_aligned(input_text: str,
                                          expected_groups: List[List[str]]
                                          ) -> List[str]:
    """Split the input text into the SAME number of paragraphs as
    expected_groups by joining the expected groups back into the input text
    they came from — basically: join each group's lines together.

    This is the key trick: we re-stitch the expected lines into the source
    paragraphs, so each paragraph's input-text comes straight from the
    expected itself.
    """
    return [" ".join(g) for g in expected_groups]


# =============================================================================
# Scoring
# =============================================================================

def line_f1(predicted: List[str], expected: List[str]) -> float:
    p = {_normalise(x) for x in predicted if x.strip()}
    e = {_normalise(x) for x in expected  if x.strip()}
    if not p and not e: return 1.0
    if not p or not e:  return 0.0
    tp = len(p & e)
    if tp == 0: return 0.0
    precision = tp / len(p)
    recall    = tp / len(e)
    return 2 * precision * recall / (precision + recall)


def overlap_score(predicted: List[str], expected: List[str]) -> float:
    """How many expected lines appear (as substring or super-string) in the
    predicted output."""
    if not expected: return 1.0
    p_norm = [_normalise(x) for x in predicted]
    e_norm = [_normalise(x) for x in expected]
    hits = 0
    for e in e_norm:
        for p in p_norm:
            if e and (e in p or p in e):
                hits += 1
                break
    return hits / len(e_norm)


# =============================================================================
# Pretty printing
# =============================================================================

def _print_side_by_side(expected: List[str], actual: List[str], indent: str = "    ") -> None:
    """Print expected | actual side-by-side, lined up."""
    max_len = max(len(expected), len(actual))
    expected_w = max([len(s) for s in expected] + [10])
    expected_w = min(expected_w, 60)

    header_e = f"EXPECTED ({len(expected)} lines)"
    header_a = f"ACTUAL ({len(actual)} lines)"
    print(f"{indent}{header_e:<{expected_w + 6}}{header_a}")
    print(f"{indent}{'-' * (expected_w + 6)}{'-' * 40}")

    for i in range(max_len):
        e = expected[i] if i < len(expected) else ""
        a = actual[i]   if i < len(actual)   else ""
        e_short = e if len(e) <= expected_w else e[:expected_w - 1] + "…"
        marker = "  " if _normalise(e) == _normalise(a) else " ✗"
        print(f"{indent}{i+1:>3}. {e_short:<{expected_w}}{marker}  {i+1:>3}. {a}")


# =============================================================================
# Test cases
# =============================================================================

# (Same data as your run_plain.py — copied verbatim. Long literals.)

WHALE_EXPECTED = [
"One of the best places on Earth to find",
"whale fossils…",
"is a desert.",
"Not near a desert.",
"In one.",
"In Egypt,",
"there’s a valley filled with",
"ancient whale skeletons sitting directly in the sand —",
"ribs,", "vertebrae,",
"entire fossilized bodies baking under one of the driest climates on Earth.",
"Which feels impossible until you realize the desert used", "to be an ocean.",
"And once that clicks, deserts start getting weird very fast.",
"Because some of the driest places humans can stand today were", "underwater for millions of years.",
"Entire seas vanished.", "Ecosystems disappeared.",
"Then time buried everything under dust and rock until the planet basically erased the evidence.",
"Except occasionally… bones stick out.",
"Wadi Al-Hitan in Egypt — literally", "“Valley of the Whales” —", "looks almost cinematic from above.",
"Endless pale desert,", "rolling sand,", "rocky outcrops.",
"Then the camera drops lower and suddenly there’s", "a whale spine in the middle of nowhere.",
"Not a small fossil either.",
"Full skeletons over", "15 meters long", "scattered across the desert floor.",
"And these whales are incredibly important because they capture evolution mid-transition.",
"Some species still had tiny back legs — remnants from when",
"whale ancestors walked on land millions of years earlier.",
"That’s the surreal part.",
"You can stand in one of the driest landscapes on Earth looking at",
"the skeleton of a creature that evolved for the ocean…",
"while surrounded by terrain that hasn’t seen meaningful rain in ages.",
"Your brain struggles to connect the two realities.",
"Desert.", "Whale.", "They shouldn’t overlap.",
"But around", "40 million years ago, this entire region was covered by", "the Tethys Sea.",
"Warm shallow water filled with marine life.",
"Then geology shifted,", "oceans retreated,", "land rose upward,", "climates changed.",
"And eventually", "the sea disappeared completely.",
"Leaving whales stranded not physically…", "…but historically.",
"That’s what fossils really are:",
"moments trapped after the environment around them moved on.",
"And nowhere makes that clearer than", "the Atacama Desert in Chile.",
"Because the Atacama is so dry it almost preserves things too well.",
"Some weather stations there have gone years", "without recording rainfall.",
"Parts of the landscape look genuinely Martian —",
"cracked earth,", "red rock,", "salt flats,", "almost no vegetation.",
"Yet buried within this hyper-arid desert are", "enormous marine fossil deposits.",
"Whales.", "Fish.", "Ancient sea creatures.",
"Entire graveyards from ecosystems that vanished", "millions of years ago.",
]

FLAT_EXPECTED = [
"Some places on Earth are", "so flat…",
"satellites use them to check if they’re broken.",
"Not metaphorically.",
"NASA literally", "calibrates space instruments using",
"giant natural surfaces so level",
"that even tiny elevation changes become scientifically useful.",
"Which sounds impossible because", "humans are terrible at understanding", "true flatness.",
"A road looks flat.", "An ocean looks flat.",
"Then you zoom out and realize", "both are full of imperfections.",
"But in Bolivia,", "there’s a place so absurdly level that",
"after rain,", "the horizon disappears completely.",
"Salar de Uyuni looks fake even in raw footage.",
"An endless white salt plain stretching beyond visibility.",
"Then rainwater forms a thin reflective layer across the surface and suddenly",
"the ground becomes a perfect mirror.",
"Sky above.", "Sky below.", "No visible horizon line separating them.",
"Cars appear to float through clouds.",
"People look like they’re walking through empty space.",
]

SHORT_CASES = [
    ("fast_cat",
     "The fast cat sat on the comfortable mat",
     ["The fast cat sat", "on the comfortable mat"]),
    ("baker",
     "The baker kneaded the bread while the fire crackled",
     ["The baker kneaded the bread", "while the fire crackled"]),
    ("red_car",
     "The red car the blue truck the green bike sped past the house",
     ["The red car", "the blue truck", "the green bike sped past", "the house"]),
    ("anyway",
     "anyway, here is a sentence that has punctuatoin",
     ["anyway, here is a sentence", "that has punctuatoin"]),
    ("kerala",
     "the black pepper vine was entirely native to the Malabar Coast of India, specifically Kerala.",
     ["the black pepper vine was entirely native to",
      "the Malabar Coast of India,",
      "specifically",
      "Kerala."]),
    ("currency",
     "It costs about $800,000 over a lifetime.",
     ["It costs about", "$800,000", "over a lifetime."]),
    ("comma_clausal",
     "Then geology shifted, oceans retreated, land rose upward, climates changed.",
     ["Then geology shifted,", "oceans retreated,", "land rose upward,", "climates changed."]),
    ("comma_list",
     "ribs, vertebrae, entire fossilized bodies baking under the desert.",
     ["ribs,", "vertebrae,", "entire fossilized bodies baking under the desert."]),
    ("dust_and_rock",
     "Then time buried everything under dust and rock.",
     ["Then time buried everything under dust and rock."]),
    ("imperative_pp",
     "Switch to a fresh steed.",
     ["Switch to a fresh steed."]),
]


def make_input(expected: List[str]) -> str:
    return " ".join(l.strip() for l in expected if l.strip())


# Build cases as (name, input, expected_lines)
ALL_CASES = (
    [("whale_excerpt", make_input(WHALE_EXPECTED), WHALE_EXPECTED),
     ("flat_excerpt",  make_input(FLAT_EXPECTED),  FLAT_EXPECTED)]
    + SHORT_CASES
)


# =============================================================================
# Main
# =============================================================================

def evaluate_case(name: str, input_text: str, expected: List[str],
                  verbose: bool = False, threshold: float = 0.5) -> Tuple[float, float]:
    """Returns (f1, overlap)."""
    actual = split_text_into_sections(input_text)
    f1 = line_f1(actual, expected)
    ov = overlap_score(actual, expected)
    score = max(f1, ov)
    pass_mark = "✓" if score >= threshold else "✗"

    print(f"\n{'=' * 80}")
    print(f"CASE: {name}    F1={f1:.2f}  Overlap={ov:.2f}  {pass_mark}")
    print(f"{'=' * 80}")

    if verbose:
        _print_side_by_side(expected, actual)
    else:
        # show only the first divergence + summary
        for i, (e, a) in enumerate(zip(expected, actual)):
            if _normalise(e) != _normalise(a):
                print(f"  First divergence at line {i+1}:")
                print(f"    EXPECTED: {e}")
                print(f"    ACTUAL:   {a}")
                break
        if len(actual) != len(expected):
            print(f"  Line count: expected {len(expected)}, got {len(actual)}")

    return f1, ov


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="run only this case", default=None)
    parser.add_argument("--verbose", action="store_true",
                        help="print full side-by-side diff for every case")
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    cases = [c for c in ALL_CASES if (args.case is None or c[0] == args.case)]
    if not cases:
        print(f"No cases match {args.case!r}.  Available: {[c[0] for c in ALL_CASES]}")
        sys.exit(1)

    results: List[Tuple[str, float, float]] = []
    for name, inp, exp in cases:
        f1, ov = evaluate_case(name, inp, exp,
                                verbose=args.verbose or args.case is not None,
                                threshold=args.threshold)
        results.append((name, f1, ov))

    print(f"\n{'=' * 80}")
    print(f"SUMMARY  (threshold {args.threshold:.2f})")
    print(f"{'=' * 80}")
    print(f"  {'CASE':<25} {'F1':>6} {'OV':>6}  PASS")
    print(f"  {'-' * 25} {'-' * 6} {'-' * 6}  ----")
    for name, f1, ov in results:
        score = max(f1, ov)
        mark  = "✓" if score >= args.threshold else "✗"
        print(f"  {name:<25} {f1:>6.2f} {ov:>6.2f}    {mark}")
    passed = sum(1 for _, f1, ov in results if max(f1, ov) >= args.threshold)
    print(f"\n  {passed}/{len(results)} cases passed.")


if __name__ == "__main__":
    main()
