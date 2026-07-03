"""
calibrate_against_golden.py  —  diff a generated shot list against a
hand-made golden reference (Task D1).

    python3 calibrate_against_golden.py GENERATED.json GOLDEN.json

The golden file (e.g. stickman_script_to_search_term.json) was split by a
human, so its line keys DON'T match the splitter's lines one-to-one.  The
harness fuzzy-aligns lines (difflib ratio on normalised text) and reports:

  1. MEDIA DISTRIBUTION  — legacy search_type counts side by side, with the
     delta.  This is the primary tuning signal: if the generated column is
     32% grids and the golden is 14% stickman-heavy, the priors are wrong.
  2. TERM QUALITY        — flags suspicious generated terms: single-word
     terms, terms with no alphabetic word longer than 3 chars, terms equal
     to a bare stopword/verb (a small syntactic check, not a POS parse).
  3. GRID PLACEMENT      — every line that is a grid on one side but not
     the (aligned) other.
  4. SIDE-BY-SIDE SHEET  — a TSV of aligned lines for eyeballing, written
     next to the generated file.

Exit code 0 always — this is a REPORT, not a gate; tuning is a human loop.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

_GRID_LEGACY = {"joint_3_row", "stickman_joint_3_row"}

# Closed-class FUNCTION words (grammar, not content).  A principled check:
# a term whose every word is a function word cannot describe a visual.
# (An earlier version had a hand-list of bad terms copied from one broken
# output — that was circular and is banned; this is a grammar category.)
_FUNCTION_WORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "by",
    "from", "and", "or", "but", "if", "as", "so", "than", "then", "that",
    "this", "these", "those", "it", "its", "is", "was", "are", "were",
    "be", "been", "being", "has", "have", "had", "do", "does", "did",
    "not", "no", "yes", "you", "your", "we", "our", "they", "their",
    "he", "she", "his", "her", "there", "here", "what", "which", "who",
    "how", "when", "where", "why", "all", "some", "any", "more", "most",
    "very", "just", "only", "about", "into", "over", "under", "up",
    "down", "out", "now", "yep", "yeah", "well", "also", "still",
}


def _term_flags(term: str):
    words = re.findall(r"[a-zA-Z]+", term)
    reasons = []
    if len(words) == 1:
        reasons.append("single word")
    if words and all(w.lower() in _FUNCTION_WORDS for w in words):
        reasons.append("function words only")
    if words and max((len(w) for w in words), default=0) <= 3:
        reasons.append("no substantial word")
    return reasons


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def align(gen_lines, gold_lines):
    """Best-overlap golden line for each generated line (greedy, ratio>=0.35)."""
    pairs = []
    for g in gen_lines:
        best, best_r = None, 0.35
        for h in gold_lines:
            r = SequenceMatcher(None, _norm(g), _norm(h)).ratio()
            if r > best_r:
                best, best_r = h, r
        pairs.append((g, best, round(best_r, 2) if best else 0.0))
    return pairs


def distribution(rows) -> Counter:
    return Counter(v["search_type"] for v in rows.values())


def pct(n, total) -> str:
    return f"{100 * n / max(total, 1):5.1f}%"


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    gen_path, gold_path = Path(sys.argv[1]), Path(sys.argv[2])
    gen = json.loads(gen_path.read_text(encoding="utf-8"))
    gold = json.loads(gold_path.read_text(encoding="utf-8"))

    # ---- 1. media distribution ---------------------------------------------
    dg, dh = distribution(gen), distribution(gold)
    all_types = sorted(set(dg) | set(dh))
    print(f"\n== MEDIA DISTRIBUTION ==  generated n={len(gen)}  golden n={len(gold)}")
    print(f"{'search_type':<30}{'generated':>12}{'golden':>12}{'delta pp':>10}")
    for t in all_types:
        a = 100 * dg.get(t, 0) / max(len(gen), 1)
        b = 100 * dh.get(t, 0) / max(len(gold), 1)
        print(f"{t:<30}{pct(dg.get(t, 0), len(gen)):>12}"
              f"{pct(dh.get(t, 0), len(gold)):>12}{a - b:>+9.1f}")

    # ---- 2. term quality ----------------------------------------------------
    print("\n== TERM QUALITY (generated) ==")
    flagged = 0
    for line, cfg in gen.items():
        reasons = _term_flags(str(cfg.get("search_term", "")))
        term = str(cfg.get("search_term", ""))
        if reasons:
            flagged += 1
            print(f"  [{' & '.join(reasons)}] {term!r}  <- {line[:60]!r}")
    print(f"  {flagged}/{len(gen)} terms flagged")

    # ---- 3. grid placement --------------------------------------------------
    print("\n== GRID PLACEMENT (vs aligned golden line) ==")
    pairs = align(list(gen), list(gold))
    mismatches = 0
    for g, h, r in pairs:
        gen_is_grid = gen[g]["search_type"] in _GRID_LEGACY
        gold_is_grid = bool(h) and gold[h]["search_type"] in _GRID_LEGACY
        if gen_is_grid != gold_is_grid and h:
            mismatches += 1
            side = "generated=grid, golden=not" if gen_is_grid \
                else "golden=grid, generated=not"
            print(f"  [{side}] {g[:55]!r} ~ {h[:55]!r} (sim {r})")
    print(f"  {mismatches} grid mismatches on aligned lines")

    # ---- 4. side-by-side sheet ----------------------------------------------
    sheet = gen_path.with_name(gen_path.stem + "-VS-GOLDEN.tsv")
    with sheet.open("w", encoding="utf-8") as fh:
        fh.write("generated_line\tgen_type\tgen_term\t"
                 "golden_line\tgold_type\tgold_term\tsimilarity\n")
        for g, h, r in pairs:
            gc = gen[g]
            hc = gold.get(h, {}) if h else {}
            fh.write(f"{g}\t{gc['search_type']}\t{gc['search_term']}\t"
                     f"{h or ''}\t{hc.get('search_type', '')}\t"
                     f"{hc.get('search_term', '')}\t{r}\n")
    print(f"\nside-by-side sheet -> {sheet}")


if __name__ == "__main__":
    main()
