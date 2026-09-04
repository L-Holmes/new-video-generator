"""
main.py — a narration script in, a tagged shot list out.

    uv run main.py                              the bundled example script
    uv run main.py my-script.txt                your own
    uv run main.py my-script.txt --no-manual    stop before the browser

Four stages, one folder each, run in order by run_all() below. Every stage
is a function you can call on its own, and every one of them ALWAYS runs —
a cache makes a stage fast, it never makes it get skipped.

    stage_0_split           0-sentence-splitter/
    stage_1_visualisables   1-visualisable-identification/
    stage_2_shot_list       2-auto-tagging/
    stage_3_manual_tagging  3-manual-tagging/

WHERE THINGS GO
    Run directly, everything lands under TEST_RESULTS/ — shot list and
    visualisables json at the top, caches in TEST_RESULTS/CACHE/. Nothing
    is written next to the code.

    Imported as a library (the repo's own main.py does this), the defaults
    are the working directory instead, which is where CONFIG expects the
    shot list and the "<prefix>-CACHE" folder.

The output format is documented field-by-field in documentation/FORMAT.md.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

# The folders here are named "0-sentence-splitter", "3-manual-tagging" ... —
# no import statement can name them, so PATHS puts each one on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent / "shared"))
import PATHS  # noqa: F401,E402  — every folder of this package on sys.path


# =============================================================================
# THE DIALS
# =============================================================================

# Where the shot list and the visualisables json are written, and where the
# "<prefix>-CACHE" folders are made. "." — the working directory — is what
# CONFIG expects of a library caller; a direct run points both at
# TEST_RESULTS/ instead (see _run_directly).
OUTPUT_DIR = Path(".")
CACHE_ROOT = Path(".")
TEST_RESULTS_DIR = Path("TEST_RESULTS")

# When True, every file this module WRITES is prefixed with "TESTING_".
TESTING_SCRIPT_SEARCH_TERM_GENERATION = True

# Per-stage off switches. Stage 0 has none — without it there are no lines.
RUN_STAGE_1_VISUALISABLES = True
RUN_STAGE_2_AUTO_TAGGING = True

# The splitter prints its own progress; keep it quiet inside the pipeline.
QUIET_SPLITTER = True

# legacy audio defaults (the renderer reads these as-is)
SFX_DEFAULT = "none"
SFX_TIMING_DEFAULT = "loop_start"
MUSIC_DEFAULT = "none"
MUSIC_TRIM_SECONDS_DEFAULT = 0
MUSIC_FADE_OUT_DEFAULT = 0


# =============================================================================
# THE ORCHESTRATION — the whole point of this file
# =============================================================================

def run_all(script_name: str, out_path: Path | None = None,
            manual: bool = True) -> Path:
    """Every stage, in order, on one script.

    @input script_name = the narration script.  e.g. "script-whales.txt"
    @input out_path = where the shot list goes. None = this module's own
        naming (OUTPUT_DIR / "[TESTING_]<prefix>-script_to_search_term.json").
    @input manual = run stage 3 (opens a browser and blocks). False stops
        after stage 2 and just says what is left.

    @output the path to the shot list.
    """
    prefix = prefix_from_script_name(script_name)
    script_path = _resolve_script_path(script_name)
    if out_path is None:
        out_path = output_path(prefix)

    split = stage_0_split(prefix, script_path)          # (line, rule ids, meta)
    lines = [line for line, _ids, _meta in split]
    visualisables = stage_1_visualisables(prefix, lines, out_path)
    stage_2_shot_list(split, out_path, visualisables)
    if manual:
        stage_3_manual_tagging(out_path)
    else:
        _banner(3, "manual tagging", "3-manual-tagging")
        left = untagged_lines(out_path)
        print(f"  skipped (--no-manual) — {len(left)} row(s) still untagged "
              f"in {out_path}")
    return out_path


def generate_script_to_search_term(script_name: str,
                                   out_path: Path | None = None) -> Path:
    """Stages 0, 1 and 2 — the shot list, without opening a browser.

    What the repo's own main.py calls; it runs stage 3 itself so it can
    check the result and stop the video build if rows are still untagged.
    out_path lets it target CONFIG's filename convention directly.
    """
    return run_all(script_name, out_path, manual=False)


def _banner(number: int, title: str, folder: str) -> None:
    print(f"\n{'─' * 70}\nSTAGE {number} — {title}   ({folder}/)\n{'─' * 70}")


# =============================================================================
# STAGE 0 — cut the script into lines            (0-sentence-splitter/)
# =============================================================================

def stage_0_split(prefix: str, script_path: Path
                  ) -> List[Tuple[str, List[int], dict]]:
    """The script, cut into the lines you will put one shot against each of.

    @output (line, the rule ids that cut it, meta) per line, in order.
        e.g. [("In Egypt,", [7], {...}),
              ("there's a valley filled with", [18], {...})]
        Stage 1 wants the lines; stage 2 wants the rule ids too.

    Cached: the spaCy parse is the expensive part, and it depends only on
    the script.
    """
    _banner(0, "split the script into lines", "0-sentence-splitter")
    cache = split_cache_path(prefix)
    if cache.exists():
        print(f"  cache hit -> {cache}")
        split = [(t, [int(i) for i in ids], meta)
                 for t, ids, meta in _load_json(cache)]
    else:
        if not script_path.exists():
            raise FileNotFoundError(
                f"Script not found: {script_path}. "
                f"Provide a UTF-8 text transcript.")
        text = script_path.read_text(encoding="utf-8")
        print(f"  running the splitter on {script_path} ({len(text)} chars)...")
        split = _run_splitter(text)
        _save_json(cache, [[t, ids, meta] for t, ids, meta in split])
        print(f"  cached -> {cache}")
    print(f"  {len(split)} line(s)")
    return split


def _run_splitter(text: str) -> List[Tuple[str, List[int], dict]]:
    from sentence_splitter import split_text_into_sections_with_meta
    if QUIET_SPLITTER:
        with contextlib.redirect_stdout(io.StringIO()):
            chunks = split_text_into_sections_with_meta(text)
    else:
        chunks = split_text_into_sections_with_meta(text)
    return [(c.text.strip(), [int(i) for i in c.ids], dict(c.meta))
            for c in chunks if c.text.strip()]


# =============================================================================
# STAGE 1 — what could go on screen    (1-visualisable-identification/)
# =============================================================================

def stage_1_visualisables(prefix: str, lines: List[str],
                          out_path: Path | None = None) -> list | None:
    """The things each line could put on screen, and what we know about them.

    @output one entry per line, in order — also written out as json:
        [{"line": "there's a valley filled with",
          "template": "there's a [1] filled with",
          "slots": {"1": {"visualisable": "valley", "action": None,
                          "location": "Egypt", "kind": "thing", ...}}}]
        None when the stage could not run at all.

    Cached on the LINES: same lines, same answer, and the coreference models
    cost tens of seconds a run.

    NOT yet wired into stage 2 — the auto-tagger still decides from its own
    detectors. What this is for today is stage 3's suggestion chips, and the
    json, for whatever comes after tagging.
    """
    _banner(1, "work out the visualisables", "1-visualisable-identification")
    if not RUN_STAGE_1_VISUALISABLES:
        print("  skipped (RUN_STAGE_1_VISUALISABLES is off)")
        return None

    entries = _cached_visualisables(prefix, lines)
    if entries is None:
        print(f"  working out {len(lines)} line(s) — the coreference models "
              f"take a while...")
        try:
            import myownstuff                  # 1-visualisable-identification/
            per_line = myownstuff.get_visualisable_data_for_line_segments(lines)
        except Exception as exc:
            # Never fatal: the shot list does not depend on this, and stage 3
            # works it out for itself when you open the page.
            print(f"  FAILED ({exc}) — carrying on without it")
            return None
        entries = _as_rows(lines, per_line)
        _save_json(visualisables_cache_path(prefix),
                   {"lines_key": "\n".join(lines), "visualisables": entries})
        print(f"  cached -> {visualisables_cache_path(prefix)}")

    written = visualisables_path(prefix, out_path)
    _save_json(written, entries)
    slots = sum(len(e["slots"]) for e in entries)
    print(f"  {slots} visualisable(s) over {len(entries)} line(s) -> {written}")
    return entries


def _cached_visualisables(prefix: str, lines: List[str]) -> list | None:
    """The stage's answer off disk, or None. A hit is also pushed back into
    myownstuff's in-process cache: stage 3 asks it for these SAME lines a
    moment later, and without this it reruns the models we just skipped."""
    cache = visualisables_cache_path(prefix)
    if not cache.exists():
        return None
    try:
        cached = _load_json(cache)
        if cached.get("lines_key") != "\n".join(lines):
            print("  the lines changed since the cache was written")
            return None
        print(f"  cache hit -> {cache}")
        entries = cached["visualisables"]
    except Exception:
        return None                          # unreadable cache = no cache
    try:
        import myownstuff
        myownstuff._CACHED_VISUALISABLE_DATA[tuple(lines)] = [
            {e["template"]: e["slots"]} for e in entries]
    except Exception:
        pass                                 # only a speed-up for stage 3
    return entries


def _as_rows(lines: List[str], per_line: list) -> list:
    """myownstuff's {template: {slot: ...}} per line, flattened to json rows.

    ONE ROW PER LINE whatever comes back — _cached_visualisables rebuilds the
    original shape from this, and a dropped row would misalign every line
    after it. as_map() always has exactly one key.
    """
    rows = []
    for line, entry in zip(lines, per_line):
        items = list((entry or {}).items())
        template, slots = items[0] if items else (line, {})
        rows.append({"line": line, "template": template, "slots": slots})
    return rows


# =============================================================================
# STAGE 2 — the shot list, easy rows filled          (2-auto-tagging/)
# =============================================================================

def stage_2_shot_list(split: List[Tuple[str, List[int], dict]],
                      out_path: Path, visualisables: list | None = None
                      ) -> Path:
    """Write the shot list from stage 0's lines, then auto-tag what it can.

    @input split = exactly what stage_0_split returned.
    @input visualisables = exactly what stage_1_visualisables returned, or
        None. When it is there the auto-tagger can see, for every line, what
        stage 1 found in it and whether anything in it is NEW — see
        VISUALISABLE_SEARCH_TERMS.py. When it is not, the tagger decides
        from its own detectors exactly as it did before stage 1 existed.

    An existing file is never overwritten — your tagging survives a re-run.
    The auto-tagger only ever touches EMPTY rows, so it is safe to run over
    the same file for ever.
    """
    _banner(2, "shot list + auto-tag the easy rows", "2-auto-tagging")
    if out_path.exists():
        print(f"  shot list already there -> {out_path} "
              f"(kept; delete it to start the tagging over)")
    else:
        _save_json(out_path, build_rows(split))
        print(f"  {len(split)} row(s) -> {out_path}")

    facts = _stage_1_facts(visualisables)
    if not RUN_STAGE_2_AUTO_TAGGING:
        print("  auto-tagging skipped (RUN_STAGE_2_AUTO_TAGGING is off)")
    else:
        _auto_tag(out_path, facts)
    _fill_remaining_search_terms(out_path, facts)
    return out_path


def _stage_1_facts(visualisables: list | None) -> dict:
    """Stage 1's rows as {line: LineFacts} — the shape stage 2 asks in."""
    if not visualisables:
        return {}
    try:
        from VISUALISABLE_SEARCH_TERMS import facts_by_line
        facts = facts_by_line(visualisables)
        same = sum(1 for f in facts.values() if f.same_scene_as_previous)
        terms = sum(1 for f in facts.values() if f.search_term)
        print(f"  stage 1 offers {terms} search term(s), and says {same} "
              f"line(s) are the same scene as the line above")
        return facts
    except Exception as exc:
        print(f"  stage 1's answers unusable ({exc}) — deciding without them")
        return {}


def _fill_remaining_search_terms(out_path: Path, facts: dict) -> None:
    """Give every still-empty search_term stage 1's answer.

    The flowchart went first on purpose: where it fired it knows something
    stage 1 does not — that a quote is the caption text, that a figure is
    chart data — so its term is the better one and is never overwritten. This
    is the rest, and it is most of what a human used to have to type: the
    row arrives in 3-manual-tagging needing a media type CLICK and nothing
    else. A chart row is skipped: it reads row["data"], not a term.
    """
    if not facts:
        return
    data = _load_json(out_path)
    filled = 0
    for line, row in data.items():
        if (row.get("search_term") or "").strip() or row.get("data"):
            continue
        term = getattr(facts.get(line), "search_term", "")
        if term:
            row["search_term"] = term
            filled += 1
    if filled:
        _save_json(out_path, data)
    still = sum(1 for r in data.values()
                if not (r.get("search_term") or "").strip())
    print(f"  stage 1 filled {filled} more search term(s) — "
          f"{len(data) - still}/{len(data)} rows now have one")


def build_rows(triples: List[Tuple[str, List[int], dict]]) -> dict:
    out = {}
    for text, ids, _meta in triples:
        out[text] = {
            "search_term": "",       # you write this (stage 3 / AI)
            "media_type": "",        # you pick this (stage 3)
            "modifiers": [],         # decorate / caption / group
            "group_id": None,        # lines sharing an id are one group
            "position": "1",
            "sfx": SFX_DEFAULT,
            "sfx_timing": SFX_TIMING_DEFAULT,
            "music": MUSIC_DEFAULT,
            "music_trim_seconds": MUSIC_TRIM_SECONDS_DEFAULT,
            "music_fade_out": MUSIC_FADE_OUT_DEFAULT,
            "rule_ids": ids,         # how the splitter cut this line
        }
    return out


def _auto_tag(out_path: Path, facts: dict | None = None) -> None:
    """EMPTY rows only, so this is idempotent. A failure here must never
    break the emit. For the full detection table, run it standalone:
        uv run 2-auto-tagging/Auto_add_mediatypes.py <json> --dry-run"""
    try:
        import Auto_add_mediatypes as auto            # 2-auto-tagging/
        from auto_tag_engine import apply_flowchart, detect_attributes
    except Exception as exc:
        print(f"  auto-tagger unavailable ({exc}) — skipping")
        return
    try:
        data = _load_json(out_path)
        # shared["visualisables"] is how stage 1 reaches the detectors —
        # see auto_tag_engine.detect_attributes.
        attrs, lines = detect_attributes(data, auto.collect_attributes,
                                         shared={"visualisables": facts or {}})
        print(f"  flowchart over {len(data)} row(s) (EMPTY only):")
        changed = apply_flowchart(data, attrs, lines, auto.decide)
        if changed:
            _save_json(out_path, data)
            print(f"  filled {len(changed)} row(s) -> {out_path}")
        else:
            print("  nothing left it could fill — the rest is stage 3")
    except Exception as exc:
        print(f"  FAILED ({exc}) — the shot list is untouched")


# =============================================================================
# STAGE 3 — you, in a browser                     (3-manual-tagging/)
# =============================================================================

def stage_3_manual_tagging(out_path: Path) -> bool:
    """Open the tagger for whatever is still untagged. True = all done.

    Blocks until you finish there. Stage 1 has already run in THIS process,
    so the suggestion chips are ready the moment the page loads.
    """
    _banner(3, "tag the rest by hand", "3-manual-tagging")
    unresolved = untagged_lines(out_path)
    if not unresolved:
        print(f"  every row already has a media type -> {out_path}")
        return True
    print(f"  {len(unresolved)} row(s) still need one — opening the tagger...")
    import MANUAL_TAGGING                            # 3-manual-tagging/
    MANUAL_TAGGING.run_manual_tagging(out_path)

    unresolved = untagged_lines(out_path)
    if unresolved:
        print(f"  {len(unresolved)} row(s) STILL have no media type "
              f"(e.g. {unresolved[0]!r}) — run this again to carry on.")
        return False
    print("  all done.")
    return True


# =============================================================================
# paths + small helpers
# =============================================================================

def prefix_from_script_name(script_name: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", Path(script_name).name)
    return re.sub(r"^script[-_]", "", stem, flags=re.IGNORECASE) or "script"


def _tag() -> str:
    return "TESTING_" if TESTING_SCRIPT_SEARCH_TERM_GENERATION else ""


def cache_dir_for(prefix: str) -> Path:
    # 'split-and-lable' spelling kept verbatim from the original path spec.
    return CACHE_ROOT / f"{prefix}-CACHE" / "split-and-lable"


def split_cache_path(prefix: str) -> Path:
    # The name carries a schema version: if the split output ever changes,
    # bump the number so stale caches can never be silently misread.
    return cache_dir_for(prefix) / f"{_tag()}SPLITMETA4-{prefix}.json"


def visualisables_cache_path(prefix: str) -> Path:
    return (CACHE_ROOT / f"{prefix}-CACHE" / "visualisables"
            / f"{_tag()}VISUALISABLES1-{prefix}.json")


def output_path(prefix: str) -> Path:
    return OUTPUT_DIR / f"{_tag()}{prefix}-script_to_search_term.json"


def visualisables_path(prefix: str, out_path: Path | None = None) -> Path:
    """Next to the shot list, wherever the caller put that."""
    if out_path is not None:
        stem = re.sub(r"[-_]script_to_search_term$", "", out_path.stem)
        return out_path.with_name(f"{stem}-visualisables.json")
    return OUTPUT_DIR / f"{_tag()}{prefix}-visualisables.json"


def untagged_lines(json_path: Path) -> List[str]:
    return [line for line, row in _load_json(json_path).items()
            if not (row.get("media_type") or "").strip()]


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _resolve_script_path(script_name: str) -> Path:
    p = Path(script_name)
    if p.exists():
        return p
    alt = p.with_suffix(".txt")
    return alt if alt.exists() else p


# =============================================================================
# running it directly
# =============================================================================

BUNDLED_EXAMPLE = "script-whales.txt"
BUNDLED_EXAMPLE_TEXT = (
    "Here's the thing. In the middle of the Sahara sits a valley called "
    "Wadi Al-Hitan. Scientists digging there found ribs, vertebrae, teeth, "
    "and entire skulls. Whale skulls. The ground looked as if an ocean had "
    "simply dried up around them. Every fossil was intact except one. "
    "Locals swear the wind sounds like whale song at night. And then boom, "
    "a sandstorm buried the site for a decade. Recovering it cost about "
    "$2 million."
)


def _run_directly(argv: List[str]) -> None:
    """Everything a direct run writes goes under TEST_RESULTS/."""
    global OUTPUT_DIR, CACHE_ROOT

    manual = "--no-manual" not in argv
    scripts = [a for a in argv if not a.startswith("--")]

    OUTPUT_DIR = TEST_RESULTS_DIR
    CACHE_ROOT = TEST_RESULTS_DIR / "CACHE"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not scripts:
        example = TEST_RESULTS_DIR / BUNDLED_EXAMPLE
        if not example.exists():
            example.write_text(BUNDLED_EXAMPLE_TEXT, encoding="utf-8")
            print(f"wrote the bundled example -> {example}")
        scripts = [str(example)]

    for script in scripts:
        print(f"\n{'=' * 70}\n{script}   ->   {OUTPUT_DIR.resolve()}\n{'=' * 70}")
        out = run_all(script, manual=manual)
        _summarise(out)


def _summarise(out_path: Path, limit: int = 12) -> None:
    data = _load_json(out_path)
    tagged = sum(1 for r in data.values() if (r.get("media_type") or "").strip())
    print(f"\n----- {out_path.name}: {tagged}/{len(data)} rows tagged -----")
    for i, (line, row) in enumerate(data.items()):
        if i >= limit:
            print(f"  ... ({len(data) - limit} more)")
            break
        print(f"  {line!r:<48} {row.get('media_type') or '-':<16} "
              f"{row.get('search_term') or ''}")
    print()


if __name__ == "__main__":
    _run_directly(sys.argv[1:])
