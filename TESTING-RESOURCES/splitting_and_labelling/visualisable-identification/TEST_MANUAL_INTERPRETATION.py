"""
TEST_MANUAL_INTERPRETATION.py -- run the WHOLE thing on a handful of scripts
and write down everything it decided, in one file, for a human to read.

    uv run TEST_MANUAL_INTERPRETATION.py             every example  (~4 min)
    uv run TEST_MANUAL_INTERPRETATION.py tractor     just that one  (~1 min)
    uv run TEST_MANUAL_INTERPRETATION.py tractor bees

    -->  TEST_RESULTS_manual_interpretation.txt      (overwritten each run)

NOTHING IS ASSERTED HERE. There is no expected output to compare against yet,
because what "right" looks like is still being worked out -- so this test's
job is to make the answers READABLE, not to grade them. Each example carries
a LOOK FOR list saying what it was written to exercise; you read the results
file and decide whether it did.

WHAT IT RUNS
    the real entry point, myownstuff.get_visualisable_data(), on the real
    sentence splitter's output. No stubs. So the file is what the video
    pipeline would actually be handed.

WHAT IT WRITES, PER EXAMPLE
    1  the script, and the line segments the splitter cut it into
    2  THE ABSTRACT TERMS MAP -- every pronoun (and every definite noun
       phrase that turned out to be a GENERIC re-mention: "The insect" ->
       the bee), what the four coreference models voted for, the
       confidence, and whether that beat the 0.25 threshold (below it the
       word is deliberately LEFT IN PLACE)
    3  the pronoun-ish words the map does not cover, and why
    4  every line segment with its slots filled in, the SEARCH TERM those
       slots come out as, and whether the line is a new scene or a hold
    5  a count of what got resolved, held and missed, and how many lines
       ended up with a term at all

WHY THE EXAMPLES ARE THESE FOUR
    tractor + spices    the two that were used to check the wiring by hand
                        while it was being written -- they are here so those
                        checks stop being something someone remembers doing
    clockmaker + bees   written FOR this file, and stuffed with the abstract
                        words the pipeline finds hardest: it / this / that /
                        her / their / them, plus the deictics I / you / we

    Adding another is a new Example(...) in EXAMPLES below. Nothing else.

COST
    ~40 s per example: the four coreference models are loaded and run once
    for each script (they are not cached between scripts). The visualisables
    passes on top of that are milliseconds.
"""
import sys
import textwrap
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "___splitting_and_labelling" / "shared"))
import PATHS  # noqa: F401,E402  — the sentence splitter is 0-'s stage folder

import myownstuff
from _abstract_term_resolver import CONFIDENCE_THRESHOLD
from _visualisables_pipeline import join_segments, parse_script
from sentence_splitter import split_text_into_sections
from VISUALISABLE_SEARCH_TERMS import facts_for_lines
# main.py's own flattener. Imported rather than copied: the rows
# facts_for_lines() is fed here have to be the rows the pipeline feeds it,
# or this file grades something the video never sees.
from main import _as_rows

RESULTS_FILE = HERE / "TEST_RESULTS_manual_interpretation.txt"


# =============================================================================
# THE EXAMPLES
# =============================================================================

@dataclass
class Example:
    """One script to run, plus what a reader is supposed to check on it.

    text may be None, in which case `script` names a file next to the other
    scripts (../script-spices.txt) and is read at run time -- so the real
    scripts stay in one place and do not get copied in here to rot.
    """
    name: str
    why: str                              # why this script is in the file
    look_for: list                        # what to check, in the results file
    text: str | None = None
    script: str | None = None

    # Where the real scripts live: SCRIPTS/ at the repo root. The repo root
    # itself is still tried, so a script left lying beside main.py from
    # before the move is still the one that gets picked up.
    _REPO_ROOT = HERE.parents[2]
    SCRIPT_DIRS = (_REPO_ROOT / "SCRIPTS", _REPO_ROOT)

    def load(self) -> str:
        if self.text is not None:
            return self.text.strip()
        for folder in self.SCRIPT_DIRS:
            path = folder / self.script
            if path.exists():
                return path.read_text().strip()
        raise FileNotFoundError(
            f"{self.script} -- looked in "
            + ", ".join(str(d) for d in self.SCRIPT_DIRS))


EXAMPLES = [

    # -- 1 --------------------------------------------------------------
    Example(
        name="tractor",
        why="The narrative myownstuff.py's own docstring is written around. "
            "It is the example every design decision in this folder was "
            "argued over, so it is the one to run first.",
        look_for=[
            '"Its windscreen broke." -- the slot should read "the tractor\'s '
            'windscreen", not "Its windscreen", and carry owner=tractor.',
            'That owner is the half that was missing from the KNOWN MISS in '
            '_variant_descriptions(): a part\'s damage can only reach the whole '
            'once we know whose part it is. Note the script stops on the break, '
            'so no later segment mentions the tractor for the broken windscreen '
            'to show up in -- the OWNER column is where to check it landed.',
            '"The cat didn\'t like it." -- "it" is the TRACTOR (or its revving). '
            'It is NOT "revved": a verb is not a thing to film. Read it in the '
            'ABSTRACT TERMS map now rather than the slot table -- "revved" no '
            'longer takes a slot anywhere, so the only place this can still go '
            'wrong is what the pronoun resolved to.',
            '"So she poured yellow paint onto the tractor." -- "she" is the cat, '
            'Molly. Then the tractor\'s variant should pick the paint up, and '
            'keep it for the rest of the script.',
            '"The yellow and black guy flew away." -- the bee, and it should '
            'STAY "guy". A DESCRIPTIVE re-mention is the case the coreference '
            'models cannot do: measured, 0 of 3 gave the bee, and the one link '
            'on offer was to the windscreen. The generic re-mention IS built '
            '("The insect" -> bee, source cluster-np), and the test that keeps '
            'it out of here is the same one -- "guy" is not a WordNet hypernym '
            'of "bee". A resolved slot on this line is the regression.',
        ],
        text="""
The tractor and the cat, Molly, went down the lane.
They passed a bee.
It revved really loud. The cat didn't like it.
So she poured yellow paint onto the tractor. It swished and swerved after having the paint splatter its windscreen.
It then crashed into the lampost by accident.
The yellow and black guy flew away.
Its windscreen broke.
""",
    ),

    # -- 2 --------------------------------------------------------------
    Example(
        name="spices",
        why="A real script, in the voice these videos are actually written "
            "in -- which means it talks TO the viewer. The narrator's 'you' "
            "is the thing to watch.",
        script="script-spices.txt",
        look_for=[
            '"your kitchen cupboard" -- should read "the viewer\'s kitchen '
            'cupboard". "you" is nobody in the script; it is whoever is '
            'watching, so it must never resolve to a noun from the text.',
            '"It costs about two dollars." -- the jar of nutmeg.',
            '"It was worth more than its weight in gold." -- BOTH the "It" and '
            'the "its" are the nutmeg (the seed, by now, not the jar).',
            'Nothing here should come out as a deictic except the "you"/"your".',
        ],
    ),

    # -- 3 --------------------------------------------------------------
    Example(
        name="clockmaker",
        why="Written for this file. One woman, one object, and a pile of "
            "pronouns pointing at both of them from every direction -- "
            "she / her / herself / he / it / this / that.",
        look_for=[
            'Every "she"/"her" should be Elena, and every "it" the clock. They '
            'alternate deliberately, so a model that just takes the nearest '
            'noun will visibly swap them.',
            '"This was not sentimental" and "She could not fix that herself" -- '
            '"this" and "that" point at a whole SITUATION, not a thing. They are '
            'tagged DT, not PRP, so they are not even in the map; check they '
            'show up in the NOT COVERED section rather than silently resolving '
            'to something wrong.',
            '"Her father had built the clock" -- possessive: the slot should '
            'read "Elena\'s father".',
            '"He sent back a spring" -- the man in Bern, and he is mentioned '
            'exactly once before. A recency guess would say "Bern".',
            'The last two "It"s ("It said:", "It held for thirty") are the NOTE '
            'and the SPRING, in adjacent sentences. If both come back the same, '
            'that is the interesting failure.',
        ],
        text="""
Elena kept a workshop above the bakery.
Her father had built the clock in the window, and she wound it every morning.
This was not sentimental. The thing paid the rent.
Tourists came for it, and they left with postcards of it.
One winter the pendulum cracked.
She could not fix that herself, so she wrote to a man in Bern.
He sent back a spring, wrapped in newspaper, and a note.
It said the spring would hold for a year.
It held for thirty.
""",
    ),

    # -- 4 --------------------------------------------------------------
    Example(
        name="bees",
        why="Written for this file. The plural and the deictic cases, which "
            "the tractor script barely touches: they / them / their, and a "
            "narrator who is IN the scene saying I / you / we / us.",
        look_for=[
            '"I", "we" and "us" should all become "the narrator", kind=deictic, '
            'confidence 1.00 -- never a noun out of the script. Same for "you" '
            'and "the viewer". This is the case that used to resolve "I" to '
            'whatever noun happened to be nearest.',
            '"their wings caught the light" -- possessive, plural: the slot '
            'should read "the bees\' wings" (apostrophe AFTER the s).',
            '"her glove" -- the beekeeper\'s, and she is only ever called "the '
            'beekeeper" and "She". Check the owner lands on the same identity '
            'the rest of the script uses for her, not on a second one.',
            '"it" runs through the whole thing meaning the FRAME (and once, at '
            'the end, a bee). Watch where it switches.',
            '"This is what she wanted me to see" -- another situational "this", '
            'expected in NOT COVERED.',
        ],
        text="""
The beekeeper opened the hive and lifted out a frame.
The bees crawled over it, and their wings caught the light.
She said you can hear them thinking.
I could not hear anything, but I could smell the wax.
One of them landed on her glove and stayed there.
This is what she wanted me to see: it was not afraid of us.
We put the frame back, and they closed over it again.
""",
    ),

    # -- 5 --------------------------------------------------------------
    Example(
        name="whales",
        why="A real script, and the ONLY one with money, a bare count, a "
            "date and a simile in it -- so it is the only place the edge "
            "trimming, the amount field and the hypothetical flag have "
            "anything to bite on.",
        script="script-whales.txt",
        look_for=[
            '"Recovering it cost about $2 million." -- the money slot should '
            'read "$2 million", not "about $2 million": the hedge is a hedge, '
            'not part of the figure. Same for "a decade" -> "decade".',
            '"The ground looked as if an ocean had simply dried up around '
            'them." -- there IS no ocean. The slot may stay, but it is a '
            'hypothetical, and nothing in this segment should become the '
            'setting the other segments stand in.',
            '"Every fossil was intact except one." -- "one" is a COUNT with '
            'nothing to count. It should not get a slot of its own.',
            '"Locals swear the wind sounds like whale song at night." -- '
            '"whale song" is the known residual: Brysbaert rates it 4.46, '
            'like the letter it is not, so expect it to survive. "at night" '
            'is a look, not a thing.',
            '"Here\'s the thing." -- discourse furniture, already blocked. It '
            'should get nothing, or the length fallback, and never a slot for '
            '"thing".',
        ],
    ),
]


# =============================================================================
# RUNNING ONE EXAMPLE
# =============================================================================

@dataclass
class Run:
    """Everything one example produced, so the reporting below is pure
    formatting and the running above is pure running."""
    example: Example
    text: str
    segments: list
    terms: dict                           # the AbstractTerms map
    script_text: str                      # the ONE text the map is keyed to
    doc: object                           # ...and its parse, for the PRON scan
    data: list                            # one {template: slots} per segment
    seconds: float
    facts: list = field(default_factory=list)   # one LineFacts per segment
    error: str | None = None


# The maps, kept alive on purpose. _visualisables_pipeline._CACHE keys its
# entries on id(abstract_terms), and CPython reuses the id of an object it has
# freed -- so if example 3's map were collected, example 4's could be handed
# the same number and read a stale entry. Holding a reference makes that
# impossible.
_KEEP_ALIVE = []


def run_example(example: Example) -> Run:
    """The real pipeline, end to end, on one script.

    Nothing here reaches inside myownstuff -- it is called exactly the way
    the video code would call it -- except for the one spy below, which
    listens in on the abstract terms map as it goes past so that the results
    file can show BOTH halves: what the pronouns resolved to, and what the
    segments did with those answers. Building the map a second time to get a
    look at it would cost another 40 s of models per example.
    """
    text = example.load()
    started = time.perf_counter()

    print("  splitting ...", end=" ", flush=True)
    chunks = split_text_into_sections(text)          # what the video code passes in
    segments = [chunk.text for chunk in chunks]      # ...the same thing, as strings
    print(f"{len(segments)} segments")

    captured = {}
    real_resolver = myownstuff.resolve_all_abstract_terms

    def spy(script_text, **kwargs):
        print("  resolving abstract terms (4 models, ~40 s) ...",
              end=" ", flush=True)
        t0 = time.perf_counter()
        terms = real_resolver(script_text, **kwargs)
        print(f"{time.perf_counter() - t0:.0f}s  ({len(terms)} pronouns)")
        captured["terms"] = terms
        captured["text"] = script_text
        return terms

    myownstuff.resolve_all_abstract_terms = spy
    try:
        # The resolving happens INSIDE this call (myownstuff step 1)i)), which
        # is why the spy above is what reports it, and this only says what is
        # left afterwards.
        data = myownstuff.get_visualisable_data(chunks)
        print(f"  labelled {len(data)} segments")
    finally:
        myownstuff.resolve_all_abstract_terms = real_resolver

    terms = captured.get("terms", {})
    _KEEP_ALIVE.append(terms)
    script_text = captured.get("text") or join_segments(segments)

    # The ANSWER, off the same rows main.py hands to stage 2 -- so what is
    # printed below is the search term the video pipeline would really use.
    facts = facts_for_lines(_as_rows(segments, data))

    return Run(example=example,
               text=text,
               segments=segments,
               terms=terms,
               script_text=script_text,
               doc=parse_script(script_text),
               data=data,
               facts=facts,
               seconds=time.perf_counter() - started)


# =============================================================================
# WRITING IT DOWN
# =============================================================================

RULE = "=" * 79
THIN = "-" * 79


def report(run: Run) -> list:
    """One example's whole section of the results file, as lines."""
    out = []
    add = out.append

    add(RULE)
    add(f"EXAMPLE: {run.example.name}")
    add(RULE)
    add("")
    add(_wrap("WHY THIS ONE IS HERE: " + run.example.why, indent=2))
    add("")
    add("LOOK FOR")
    for i, item in enumerate(run.example.look_for, 1):
        add(_wrap(f"{i}) {item}", indent=5, first_indent=2))
    add("")

    add(THIN)
    add("THE SCRIPT")
    add(THIN)
    for line in run.text.splitlines():
        # Wrapped only so it can be read here -- script-spices.txt is one
        # 379-character line. The pipeline was given the real thing.
        add(_wrap(line, indent=4, first_indent=2))
    add("")

    add(THIN)
    add(f"SPLIT INTO {len(run.segments)} LINE SEGMENTS")
    add(THIN)
    add("  (by sentence_splitter.split_text_into_sections -- one segment is")
    add("   one shot on screen, so this is what has to be picture-able)")
    add("")
    for i, segment in enumerate(run.segments, 1):
        add(f"  {i:>3}  {segment}")
    add("")

    out += _report_terms(run)
    out += _report_uncovered(run)
    out += _report_segments(run)
    out += _report_summary(run)
    return out


def _report_terms(run: Run) -> list:
    """The map: every pronoun, its answer, and whether the answer was USED.

        WHERE      SURFACE    RESOLVED       CONF   SOURCE   USED?
        103-105    It         the tractor    0.84   models   used
                   ...down the lane. [It] revved really loud...

    USED?  used   = the segment shows the thing instead of the word
           HELD   = an answer came back, but too weak to swap in
           MISSED = nobody had an answer at all
    """
    out = ["", THIN,
           "ABSTRACT TERMS -- every pronoun and generic noun phrase, and what",
           "                  the models voted for",
           THIN,
           "  Four coreference models read the whole script and vote; CONF is",
           f"  the share of their weight behind this answer. Below "
           f"{CONFIDENCE_THRESHOLD} the answer",
           "  is NOT used -- the segment keeps its pronoun, which tells the",
           "  renderer to hold the picture it already has. A wrong picture is",
           "  worse than the last right one.",
           "",
           "  SOURCE  models  = the four voted        deictic = I/we/you",
           "          recency = last thing mentioned  none    = nobody knew",
           "          cluster-np = NOT a pronoun. A definite noun phrase",
           "                    (\"The insect\") the models put in the same",
           "                    cluster as something more specific (the bee).",
           ""]

    if not run.terms:
        out += ["  (no pronouns in this script at all)", ""]
        return out

    out.append(f"  {'WHERE':<10} {'SURFACE':<10} {'RESOLVED':<24}"
               f" {'CONF':<6} {'SOURCE':<8} USED?")
    for (start, end), row in sorted(run.terms.items()):
        if not row["resolved"] or row["source"] == "none":
            used = "MISSED"
        elif row["confidence"] < CONFIDENCE_THRESHOLD:
            used = "HELD"
        else:
            used = "used"
        flags = []
        if row.get("possessive"):
            flags.append("possessive")
        if row.get("number"):
            flags.append(row["number"])
        out.append(f"  {f'{start}-{end}':<10} {row['surface']:<10}"
                   f" {str(row['resolved'] or '--'):<24}"
                   f" {row['confidence']:<6.2f} {row['source']:<8} {used}"
                   + (f"   [{', '.join(flags)}]" if flags else ""))
        out.append(f"  {'':<10} {_context(run.script_text, start, end)}")
    out.append("")

    if getattr(run.terms, "models_run", None):
        out.append("  models that answered: "
                   + ", ".join(run.terms.models_run))
        out.append("")
    return out


def _context(text: str, start: int, end: int, width: int = 26) -> str:
    """The pronoun in its sentence, in brackets, so the table can be read
    without scrolling back up to the script.
    e.g.  ...down the lane. [It] revved really loud. The...
    """
    before = text[max(0, start - width):start].replace("\n", " ")
    after = text[end:end + width].replace("\n", " ")
    lead = "..." if start - width > 0 else ""
    tail = "..." if end + width < len(text) else ""
    return f"{lead}{before}[{text[start:end]}]{after}{tail}"


# "this" / "that" / "these" / "those" used on their OWN, as a pronoun --
# never as the determiner of a noun ("this seed", dep_ det) and never as the
# complementiser ("she said that it would", dep_ mark). Both of those are
# ordinary words with nothing to resolve.
DEMONSTRATIVES = {"this", "that", "these", "those"}
NOT_A_PRONOUN_DEP = {"det", "mark"}


def uncovered_tokens(run: Run) -> list:
    """The tokens the NOT COVERED section lists -- and the count SUMMARY
    reports, which is why it is one function and not two rules."""
    covered = set(run.terms.keys())
    return [t for t in run.doc
            if (t.pos_ == "PRON"
                or (t.text.lower() in DEMONSTRATIVES
                    and t.dep_ not in NOT_A_PRONOUN_DEP))
            and (t.idx, t.idx + len(t.text)) not in covered]


def _report_uncovered(run: Run) -> list:
    """The pronoun-shaped words the map has no row for.

    Almost always a demonstrative standing on its own. The resolver targets
    PRP and PRP$ (he, it, their), and a bare demonstrative is tagged DT --
    or worse: spaCy calls the "that" in "she could not fix THAT herself" a
    subordinating conjunction, so it is not even a PRON to scan for. Hence
    DEMONSTRATIVES above, rather than a tag test.

    They are listed because they LOOK like the thing this pipeline resolves,
    and because they usually point at a whole SITUATION ("This was not
    sentimental") rather than at anything you could film -- which is an
    argument for leaving them alone, but only once you can see them.
    """
    missed = uncovered_tokens(run)

    out = ["", THIN,
           "PRONOUN-ISH WORDS THE MAP DOES NOT COVER",
           THIN]
    if not missed:
        out += ["  (none -- every pronoun in this script got a row above)", ""]
        return out
    out += ["  These read like pronouns but were never asked about, because",
            "  the resolver targets PRP/PRP$ and these are tagged something",
            "  else. Listed so the miss is visible rather than silent.",
            "",
            "  Most are nothing to chase: an indefinite (\"anything\") stands",
            "  for no particular thing, and a wh-word (\"what\") points forward",
            "  rather than back. The ones worth an argument are the bare",
            "  demonstratives -- \"This was not sentimental\" -- and they",
            "  usually point at a whole situation rather than at anything you",
            "  could film. That is a judgement, and this is where you make it.",
            ""]
    for t in missed:
        out.append(f"  {f'{t.idx}-{t.idx + len(t.text)}':<10} {t.text:<10}"
                   f" tag={t.tag_:<5} pos={t.pos_:<6} dep={t.dep_}")
        out.append(f"  {'':<10} "
                   f"{_context(run.script_text, t.idx, t.idx + len(t.text))}")
    out.append("")
    return out


SLOT_HEADER = (f"  {'SLOT':<5} {'VISUALISABLE':<24} {'KIND':<10} {'CONF':<5}"
               f" {'AMT':<4} {'VARIANT':<18} {'ACTION':<13} {'LOCATION':<18}"
               f" OWNER")


def _report_segments(run: Run) -> list:
    """Every segment with its slots filled in -- the actual output of the
    whole pipeline, which is the thing being judged."""
    out = ["", THIN,
           "WHAT EACH LINE SEGMENT GOT",
           THIN,
           "  The line is printed with the NAMES in the brackets instead of",
           "  the slot numbers, so a wrong answer reads as a wrong sentence.",
           "  A * on the slot number means it is the SETTING: the background",
           "  everything else stands on.",
           "",
           "  KIND    thing/name/number/date  = film it. These are the only",
           "                                    kinds that earn a slot: a verb",
           "                                    is the thing's ACTION and an",
           "                                    adjective its VARIANT, so",
           "                                    neither gets one of its own.",
           "          reference               = was a pronoun. Still reads as",
           "                                    one? nothing confident came",
           "                                    back -- hold the picture.",
           "          deictic                 = the narrator / the viewer",
           "          fallback                = no picture here at all",
           "  AMT     how many of it are on screen. 1 unless the segment",
           '          counted them ("900 ships" -> ships, 900). A money or',
           '          measure slot is ONE picture of a figure, so "two',
           '          dollars" is 1.',
           "  CONF    on a reference slot this is the models' vote. Still",
           "          reading as a pronoun at 0.00 means nobody answered; at",
           f"          0.10 it was a recency guess, refused by the {CONFIDENCE_THRESHOLD}",
           "          threshold.",
           "  << hypothetical  the segment only imagined this -- an \"if\", a",
           "          simile, a modal. NOT a drop: these are often the most",
           "          visual lines in a script. What to do about it is the",
           "          renderer's call.",
           "  OWNER   the whole this thing is a part of, when a possessive",
           '          said so:  "Its windscreen"  -->  owner=tractor',
           "  A ~ on the end of a cell means it was cut to fit the column.",
           "  TERM    THE ANSWER -- what VISUALISABLE_SEARCH_TERMS would go",
           "          and find footage of for this segment, read off the",
           "          slots under it. (none) means nothing here is worth",
           "          filming.",
           "  SCENE   new  = something in this segment was not on screen",
           "                 before, so fetch a picture",
           "          same = every thing here was already up; hold the one",
           "                 that is there. The reason follows it.",
           "  THEME   what the SCRIPT is about here, and what sort of thing",
           "          that is (_theme_engine). place/era/culture/subject. It",
           "          is NOT in the term above unless APPLY_THEMES is on, and",
           "          it ships off -- see VISUALISABLE_SEARCH_TERMS.",
           "  ABSTRACT  the heads this line LOST for not being a picture",
           "          (\"monopoly\", \"weight\"). Printed only when there are",
           "          any. When a line has these AND no filmable slot, it IS",
           "          an abstract concept -- shared_text_logic says so and",
           "          2-auto-tagging tags it.",
           "",
           SLOT_HEADER,
           ""]

    for i, (segment, line) in enumerate(zip(run.segments, run.data), 1):
        facts = run.facts[i - 1] if i - 1 < len(run.facts) else None
        for template, slots in line.items():
            named = template
            for slot, v in slots.items():
                named = named.replace(f"[{slot}]", f"[{v['visualisable']}]")
            imagined = any(v.get("hypothetical") for v in slots.values())
            out.append(f"  {i:>3}  {segment}")
            out.append(f"       {named}"
                       + ("   << hypothetical -- it did not happen"
                          if imagined else ""))
            out += _term_lines(facts)
            if not slots:
                out.append("       (no slots -- nothing to put on screen)")
            for slot, v in slots.items():
                star = "*" if v["is_setting"] else " "
                out.append(
                    f"  [{slot}]{star} {_cut(v['visualisable'], 24):<24}"
                    f" {v['kind']:<10} {v['confidence']:<5.2f}"
                    f" {_cut(v.get('amount'), 4):<4}"
                    f" {_cut(v['variant'], 18):<18} {_cut(v['action'], 13):<13}"
                    f" {_cut(v['location'], 18):<18} {_cut(v.get('owner'), 12)}")
            out.append("")
    return out


def _term_lines(facts) -> list:
    """The two lines under a segment that say what would actually be fetched.

        TERM   "middle of the Sahara"
        SCENE  new -- introduces middle of the sahara
        THEME  place -- Sahara
    """
    if facts is None:
        return ["       TERM   (no answer -- facts_for_lines gave this "
                "segment no row)"]
    term = f'"{facts.search_term}"' if facts.search_term else "(none)"
    return [f"       TERM   {term}",
            f"       SCENE  {'same' if facts.same_scene_as_previous else 'new'}"
            f" -- {facts.why}",
            f"       THEME  {_theme_of(facts)}"] + (
        [f"       ABSTRACT  {', '.join(facts.abstract_concepts)}"
         + ("   << and nothing filmable survived -- this line IS an "
            "abstract concept" if not facts.identities else "")]
        if facts.abstract_concepts else [])


def _theme_of(facts) -> str:
    """The line's theme, off any one of its slots -- apply_theme() stamps the
    same pair on all of them, because a theme belongs to the line."""
    for slot in (facts.slots or {}).values():
        if slot.get("theme_text"):
            return f"{slot['theme_kind']} -- {slot['theme_text']}"
    return "(none)"


def _report_summary(run: Run) -> list:
    """The counts, so a run can be compared with the last one at a glance."""
    counts = tally(run)
    out = ["", THIN, "SUMMARY", THIN,
           f"  {counts['pronouns']} mentions in the map -- pronouns, plus the",
           "    definite noun phrases that turned out to be generic",
           f"    {counts['used']} used -- the segment shows the thing, not the word",
           f"    {counts['held']} held back (confidence below "
           f"{CONFIDENCE_THRESHOLD}) -- pronoun kept on purpose",
           f"    {counts['none']} nobody could answer",
           f"    {counts['deictic']} narrator / viewer",
           f"    {counts['possessive']} possessive (\"its windscreen\")",
           f"  {counts['uncovered']} pronoun-ish words not in the map at all",
           "",
           f"  {counts['segments']} line segments, {counts['slots']} slots",
           f"    {counts['reference_slots']} slots came from a pronoun",
           f"    {counts['still_pronoun']} of those still read as a pronoun",
           f"    {counts['deictic_slots']} are the narrator or the viewer",
           f"    {counts['owned']} know what they are part of (owner=)",
           f"    {counts['empty']} segments got nothing to put on screen",
           "",
           f"  {counts['with_term']} lines have a search term",
           f"  {counts['same_scene']} lines held as the same scene as the "
           f"line above",
           "",
           f"  took {run.seconds:.0f}s",
           ""]
    return out


def tally(run: Run) -> dict:
    """The numbers behind SUMMARY, also used for the roll-up at the top."""
    used = held = nothing = deictic = possessive = 0
    for row in run.terms.values():
        if row["source"] == "deictic":
            deictic += 1
        if row.get("possessive"):
            possessive += 1
        if not row["resolved"] or row["source"] == "none":
            nothing += 1
        elif row["confidence"] < CONFIDENCE_THRESHOLD:
            held += 1
        else:
            used += 1

    uncovered = len(uncovered_tokens(run))

    slots = reference_slots = still_pronoun = deictic_slots = owned = empty = 0
    for line in run.data:
        for slots_map in line.values():
            if not slots_map:
                empty += 1
            for v in slots_map.values():
                slots += 1
                if v["kind"] == "reference":
                    reference_slots += 1
                    if v["confidence"] < CONFIDENCE_THRESHOLD:
                        still_pronoun += 1
                if v["kind"] == "deictic":
                    deictic_slots += 1
                if v.get("owner"):
                    owned += 1

    with_term = sum(1 for f in run.facts if f.search_term)
    same_scene = sum(1 for f in run.facts if f.same_scene_as_previous)

    return {"pronouns": len(run.terms), "used": used, "held": held,
            "none": nothing, "deictic": deictic, "possessive": possessive,
            "uncovered": uncovered, "segments": len(run.segments),
            "slots": slots, "reference_slots": reference_slots,
            "still_pronoun": still_pronoun, "deictic_slots": deictic_slots,
            "owned": owned, "empty": empty,
            "with_term": with_term, "same_scene": same_scene}


def _cut(value, width: int) -> str:
    """A cell that fits: None becomes "-", anything too long is elided."""
    text = "-" if value is None else str(value)
    return text if len(text) <= width else text[:width - 1] + "~"


def _wrap(text: str, indent: int = 0, first_indent: int | None = None,
          width: int = 79) -> str:
    """Paragraph wrapped to the file's width, hanging-indented so a numbered
    LOOK FOR item stays readable."""
    return "\n".join(textwrap.wrap(
        text, width=width,
        initial_indent=" " * (indent if first_indent is None else first_indent),
        subsequent_indent=" " * indent))


# =============================================================================
# THE FILE
# =============================================================================

def header(chosen: list) -> list:
    return [RULE,
            "TEST RESULTS -- visualisables + abstract term resolution",
            RULE,
            f"run  {datetime.now():%Y-%m-%d %H:%M}",
            "by   TEST_MANUAL_INTERPRETATION.py",
            f"on   {', '.join(e.name for e in chosen)}",
            "",
            "READ THIS FIRST",
            "  Nothing below is pass or fail -- there is no expected output to",
            "  compare against yet. Every example starts with a LOOK FOR list",
            "  saying what it was written to exercise; the sections after it",
            "  are what actually happened. The judging is yours.",
            "",
            "  Each example has six parts:",
            "    THE SCRIPT              what went in",
            "    LINE SEGMENTS           how the splitter cut it -- one shot each",
            "    ABSTRACT TERMS          every pronoun and what it resolved to",
            "    NOT COVERED             pronoun-ish words nothing was asked about",
            "    WHAT EACH SEGMENT GOT   the slots, the SEARCH TERM they",
            "                            produce, and whether the line is a",
            "                            new scene -- the real output",
            "    SUMMARY                 the counts",
            ""]


def rollup(runs: list) -> list:
    """The one screen at the top: every example, side by side."""
    out = [RULE, "ALL EXAMPLES AT A GLANCE", RULE, "",
           f"  {'EXAMPLE':<12} {'SEGS':<6} {'MENTIONS':<9} {'USED':<6}"
           f" {'HELD':<6} {'NONE':<6} {'DEICTIC':<8} {'OWNED':<6} {'TIME':<6}"]
    for run in runs:
        if run.error:
            out.append(f"  {run.example.name:<12} FAILED -- see its section below")
            continue
        c = tally(run)
        out.append(f"  {run.example.name:<12} {c['segments']:<6}"
                   f" {c['pronouns']:<9} {c['used']:<6} {c['held']:<6}"
                   f" {c['none']:<6} {c['deictic']:<8} {c['owned']:<6}"
                   f" {run.seconds:.0f}s")
    out.append("")
    return out


def main():
    wanted = [a.lower() for a in sys.argv[1:]]
    chosen = [e for e in EXAMPLES if not wanted or e.name.lower() in wanted]
    if not chosen:
        print(f"no such example. have: {', '.join(e.name for e in EXAMPLES)}")
        return 1

    runs, sections = [], []
    for i, example in enumerate(chosen, 1):
        print(f"\n[{i}/{len(chosen)}] {example.name}")
        try:
            run = run_example(example)
            runs.append(run)
            sections += report(run)
        except Exception:
            print(f"  FAILED\n{traceback.format_exc()}")
            failed = Run(example=example, text="", segments=[], terms={},
                         script_text="", doc=None, data=[], seconds=0.0,
                         facts=[], error=traceback.format_exc())
            runs.append(failed)
            sections += [RULE, f"EXAMPLE: {example.name}", RULE, "",
                         "  THIS ONE BLEW UP. The traceback, verbatim:", "",
                         *("  " + line for line in
                           failed.error.splitlines()), ""]

    lines = header(chosen) + rollup(runs) + sections
    RESULTS_FILE.write_text(
        "\n".join(line.rstrip() for line in lines) + "\n")
    print(f"\nwrote {RESULTS_FILE.name} "
          f"({len(RESULTS_FILE.read_text().splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
