"""
TEST_AUTO_TAGGER.py — try Auto_add_mediatypes.py on ten fixture scripts and
eyeball what it did, one at a time.

    uv run TEST_AUTO_TAGGER.py --list             what the ten fixtures are
    uv run TEST_AUTO_TAGGER.py --test 1           tag fixture 1, then open the
                                                  manual tagger on the result
    uv run TEST_AUTO_TAGGER.py --test 1 --no-manual    just the printout
    uv run TEST_AUTO_TAGGER.py --test 1 --keep    carry on from last time
                                                  (don't reset the fixture)
    uv run TEST_AUTO_TAGGER.py --all              tag all ten, print a
                                                  scoreboard, no browser
    uv run TEST_AUTO_TAGGER.py --build --force    re-split the scripts
                                                  (only after changing the
                                                   sentence splitter)

WHAT ONE RUN DOES
  1. RESET   test_jsons/test_json_<n>.json is overwritten with the pristine
             untagged copy from test_jsons/untagged/, so every run starts
             from the same place. --keep skips this.
  2. AUTO    Auto_add_mediatypes.py runs over it exactly as it does inside
             main.py — the full STEP 1 detection table and STEP 2
             flowchart printout.
  3. REVIEW  a compact line-by-line summary of what it decided, then
             MANUAL_TAGGING.py opens on that same file so you can see it as
             a page and fix what is wrong. Ctrl-C the tagger when you're done
             and run the next fixture.

WHERE THE FIXTURES COME FROM
  test_jsons/scripts/script-test_json_<n>.txt is a real short-video narration.
  --build runs the REAL sentence splitter over it (same code path as
  main.py), so every fixture is split on visualisables, list runs,
  reveals and the rest, and carries the splitter's real rule_ids. Nothing in
  here fakes a split. The split meta is cached next to it as
  <fixture>-CACHE/split-and-lable/, which is also what gives MANUAL_TAGGING
  its noun/place suggestion chips.

The ten scripts are deliberately NOT ten explainers — a memorial in a field,
a recipe, a sports-stats rundown, a personal story, a money video, a nature
fact, a product review, a travel guide, a history mystery and a piece of
pure advice with almost nothing filmable in it. Between them they hit every
branch of the flowchart, including the ones that should decide to do nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

# Unconditional: PATHS is not only the sys.path bootstrap here, it is also
# where this file looks up 3-manual-tagging to launch the tagger.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]
                       / "___splitting_and_labelling" / "shared"))
import PATHS  # noqa: E402  — every stage folder on sys.path
# AUTO_TAG_SELFTEST and this file are neighbours, not stage modules; their
# own folder is not one of the ones PATHS puts on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

HERE = Path(__file__).resolve().parent
TESTS = HERE / "test_jsons"
SCRIPTS = TESTS / "scripts"
UNTAGGED = TESTS / "untagged"


# =============================================================================
# THE TEN FIXTURES — what each one is here to prove
# =============================================================================
#
# 'probes' is the thing to look at when you review it. If a fixture stops
# probing what it says here, change the script, not the note.

FIXTURES = {
    1:  ("the wind phone",     "mystery / emotional",
         "a quote, a year, a big count and a country in one short script"),
    2:  ("garlic bread",       "recipe / how-to",
         "list runs (butter, salt, time / no pan, no board, no crying), "
         "commands, small numbers that are NOT worth a counter"),
    3:  ("the three pointer",  "sports statistics",
         "a trend over time, a percentage, and figures side by side — "
         "line graph vs progress bar vs bar chart"),
    4:  ("fired on a Tuesday", "personal story",
         "first person, pronouns, almost no proper nouns — most rows SHOULD "
         "come back empty"),
    5:  ("the coffee money",   "personal finance",
         "money counters, shares of a whole (pie), a percentage, a trend"),
    6:  ("the immortal jellyfish", "nature / animal fact",
         "one named creature carried by 'it' all the way through — the "
         "refers-back-to-a-name branch"),
    7:  ("the £29 keyboard",   "product review",
         "two prices compared, everyday objects, opinion lines with nothing "
         "to film"),
    8:  ("skip Rome",          "travel guide",
         "place after place after place — the map branch, and whether it "
         "fires too often"),
    9:  ("the dancing plague", "history mystery",
         "an opening year (timeline), counts that climb, a named city"),
    10: ("one bad sentence",   "advice / philosophy",
         "abstract nouns and questions to the viewer, nothing filmable — "
         "the honest answer is mostly 'leave it empty'"),
}


def script_path(n: int) -> Path:
    return SCRIPTS / f"script-test_json_{n}.txt"


def untagged_path(n: int) -> Path:
    return UNTAGGED / f"test_json_{n}.json"


def working_path(n: int) -> Path:
    """The file you actually tag. Named so MANUAL_TAGGING finds the split
    cache for its suggestion chips (prefix = the file's stem)."""
    return TESTS / f"test_json_{n}.json"


def cache_path(n: int) -> Path:
    stem = f"test_json_{n}"
    return TESTS / f"{stem}-CACHE" / "split-and-lable" / f"SPLITMETA4-{stem}.json"


# =============================================================================
# BUILD — split the scripts into untagged fixtures (the real splitter)
# =============================================================================

def build(numbers, force: bool = False) -> None:
    import main as sl                    # the pipeline runner, one up

    UNTAGGED.mkdir(parents=True, exist_ok=True)
    for n in numbers:
        out = untagged_path(n)
        if out.exists() and not force:
            print(f"[build] {out.name} already there (--force to re-split)")
            continue
        src = script_path(n)
        if not src.exists():
            print(f"[build] no script for fixture {n} at {src} — skipped")
            continue
        text = src.read_text(encoding="utf-8")
        print(f"[build] splitting {src.name} ({len(text)} chars)...")
        triples = sl._run_splitter(text)          # the same call main.py makes

        cache = cache_path(n)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps([[t, ids, meta] for t, ids, meta in triples],
                       indent=2, ensure_ascii=False), encoding="utf-8")

        rows = sl.build_rows(triples)             # media_type + search_term EMPTY
        out.write_text(json.dumps(rows, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        print(f"[build] {len(rows)} lines -> {out.name}")


def ensure_built(n: int) -> None:
    if not untagged_path(n).exists():
        build([n])


# =============================================================================
# RUN — reset, auto-tag, summarise
# =============================================================================

def reset(n: int) -> Path:
    """Put the pristine untagged fixture back, so a run always starts clean."""
    live = working_path(n)
    shutil.copyfile(untagged_path(n), live)
    stale = live.with_suffix(live.suffix + ".bak")
    if stale.exists():
        stale.unlink()
    return live


def auto_tag(path: Path, verbose: bool = True) -> None:
    """Run Auto_add_mediatypes over the file, exactly as the pipeline does."""
    import Auto_add_mediatypes as auto

    if verbose:
        auto.main([str(path)])
    else:
        from auto_tag_engine import apply_flowchart, detect_attributes
        data = json.loads(path.read_text(encoding="utf-8"))
        attrs, lines = detect_attributes(data, auto.collect_attributes)
        apply_flowchart(data, attrs, lines, auto.decide)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    backup = path.with_suffix(path.suffix + ".bak")
    if backup.exists():
        backup.unlink()                 # untagged/ is the real backup


def summarise(path: Path) -> tuple[int, int]:
    """The line-by-line review table: what it decided, and where it gave up."""
    data = json.loads(path.read_text(encoding="utf-8"))
    print("\n" + "=" * 78)
    print(f"REVIEW — {path.name}: every line, and what the auto tagger did")
    print("=" * 78)
    filled = 0
    for i, (line, row) in enumerate(data.items(), start=1):
        media = row.get("media_type") or ""
        mods = "+".join(row.get("modifiers") or [])
        term = row.get("search_term") or ""
        data_bit = row.get("data") or {}
        if media:
            filled += 1
            shot = media + (f" +{mods}" if mods else "")
            extra = f'  "{term}"' if term else ""
            if data_bit:
                extra += f"  {data_bit}"
        else:
            shot, extra = "— empty —", ""
        print(f"  {i:>3}. {line[:52]:<54} {shot:<26}{extra}")
    total = len(data)
    print(f"\n  {filled}/{total} rows tagged, {total - filled} left for you.")
    return filled, total


def run_one(n: int, *, manual: bool = True, keep: bool = False) -> None:
    if n not in FIXTURES:
        sys.exit(f"no fixture {n} — try --list")
    name, genre, probes = FIXTURES[n]
    ensure_built(n)

    print("=" * 78)
    print(f"FIXTURE {n} — {name}  ({genre})")
    print(f"  looking for: {probes}")
    print("=" * 78)

    live = working_path(n) if keep else reset(n)
    if keep:
        print(f"[keep] carrying on with {live.name} as it is\n")

    auto_tag(live)
    summarise(live)

    if manual:
        print(f"\nopening MANUAL_TAGGING on {live.name} — "
              f"Ctrl-C there when you are done with this one.")
        subprocess.run([sys.executable,
                        str(PATHS.MANUAL_TAGGING_DIR / "MANUAL_TAGGING.py"),
                        live.name], cwd=TESTS)
    else:
        print(f"\n(--no-manual) the tagged file is {live}")


def run_all() -> None:
    """Every fixture, no browser — the scoreboard you watch while you are
    changing rules in Auto_add_mediatypes.py."""
    scores = []
    for n in sorted(FIXTURES):
        ensure_built(n)
        live = reset(n)
        print(f"\n{'=' * 78}\nFIXTURE {n} — {FIXTURES[n][0]}\n{'=' * 78}")
        auto_tag(live, verbose=False)
        scores.append((n, *summarise(live)))
    print("\n" + "=" * 78)
    print("SCOREBOARD — how much of each fixture the auto tagger could fill")
    print("=" * 78)
    for n, filled, total in scores:
        bar = "#" * round(20 * filled / total) if total else ""
        print(f"  {n:>2}. {FIXTURES[n][0]:<24} {filled:>3}/{total:<3} "
              f"{bar:<20} {FIXTURES[n][1]}")
    whole = sum(t for _, _, t in scores)
    done = sum(f for _, f, _ in scores)
    print(f"\n  {done}/{whole} rows overall.  Remember the house rule: a row "
          f"left empty is fine,\n  a row tagged WRONG is the expensive one.")


# =============================================================================
# THE COMMAND LINE
# =============================================================================

def list_fixtures() -> None:
    print("\nthe ten test fixtures  (uv run TEST_AUTO_TAGGER.py --test <n>)\n")
    for n, (name, genre, probes) in FIXTURES.items():
        built = "" if untagged_path(n).exists() else "   [not built yet]"
        print(f"  {n:>2}. {name:<24} {genre:<22}{built}")
        print(f"      {probes}")
    print()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    if "--list" in argv:
        list_fixtures()
        return 0
    if "--build" in argv:
        build(sorted(FIXTURES), force="--force" in argv)
        return 0
    if "--all" in argv:
        run_all()
        return 0
    if "--test" in argv:
        i = argv.index("--test")
        if i + 1 >= len(argv):
            sys.exit("--test needs a fixture number, e.g. --test 1")
        run_one(int(argv[i + 1]),
                manual="--no-manual" not in argv,
                keep="--keep" in argv)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
