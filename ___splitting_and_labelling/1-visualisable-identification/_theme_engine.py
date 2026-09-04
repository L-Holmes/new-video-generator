"""
_theme_engine.py — what a script is ABOUT, and what SORT of thing that is.

    from _theme_engine import themes_for_segments, ThemeKind

    themes = themes_for_segments(segments, doc)
    themes[0]    --> Theme(kind="place", text="Egypt")
    themes[18]   --> None            (nothing is live here any more)

ONE THEME PER SEGMENT, TYPED. A script about Rome in 64 AD is about a PLACE
and an ERA, and those are different jobs downstream: a place can be added to
a search term and an era cannot ("Roman waterfall" finds nothing where
"waterfall" finds a waterfall). So the kind travels with the text and the
consumer decides — see VISUALISABLE_SEARCH_TERMS.APPLY_THEMES.

WHERE THE ANSWER COMES FROM, and what each part is allowed to decide

  THE CANDIDATES   the script's own noun phrases, off the shared spaCy parse.
                   A theme is therefore always something the script SAID.
  WHICH ONE        KeyBERT (MIT), backed by a Model2Vec static embedding
                   (MIT, numpy-only, ~8M parameters, offline once cached).
                   It scores each candidate against the text; it never
                   invents one, because `candidates=` is the whole vocabulary
                   it is allowed to answer from.
  WHAT KIND        spaCy's own NER label on the phrase. No word list:
                       GPE / LOC / FAC -> PLACE      DATE / TIME -> ERA
                       NORP            -> CULTURE    anything else -> SUBJECT
                   NORP is how "Roman" is found — the script's own adjective,
                   read off a tag. AN ADJECTIVE IS NEVER DERIVED FROM A PLACE
                   NAME: if the script only ever says "Rome", there is no
                   CULTURE theme, and that is the correct answer.

THREE SCOPES, SO A THEME CAN END
    whole script    what the piece is about, start to finish
    text so far     segments[:i+1] — nothing the viewer has not heard yet
    a window        WINDOW_SEGMENTS either side of i — what it is about HERE

    A candidate is LIVE at segment i when it is in the top few of BOTH the
    text so far AND the window. Text-so-far is what stops a theme arriving
    before the viewer has heard it; the window is what makes it END, so a
    place the script left ten lines ago is not stapled onto every term after
    it. The theme is then whichever live candidate the WHOLE SCRIPT ranks
    highest — locality decides IF there is a theme, the whole piece decides
    WHICH, so it does not flicker line to line.

    Measured on script-rome: PLACE "Rome" through the fire, ERA "64 AD" on
    the line that says it, SUBJECT "temples" / "Nero" once the script has
    moved indoors. On script-whales: PLACE "Sahara" at the start and SUBJECT
    "whale song" at the end, which is the point of having a window at all.
    The scopes are working, not output — one theme comes out.

WHAT WAS REJECTED, so nobody re-shops it (researched 2026-09-04)
    YAKE      AGPL-3.0, commercial licence sold separately. This ships in
              paid software, so it cannot be used at any quality.
    BERTopic  clusters a CORPUS into topics. We have one script. Wrong shape.
    textacy   Apache-2.0, but last released Apr 2023 and one maintainer.
    Stanza    Apache-2.0 and healthy, but a second full NLP stack beside
              spaCy for no measured gain.

NOTHING HERE RAISES. A missing model, or a missing KeyBERT, means every
segment gets None and the terms are exactly what they were.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# =============================================================================
# THE DIALS
# =============================================================================

# The static embedding KeyBERT scores with. Model2Vec, so numpy only — no
# torch and no sentence-transformers are added by this file.
EMBEDDING_MODEL = "minishlab/potion-base-8M"

# How many segments either side of i count as "here". Six is about a
# paragraph of narration at this split size.
WINDOW_SEGMENTS = 6

# How near the top a candidate has to come, in a scope, to count as live in
# it. Small on purpose: a theme is what the piece is ABOUT, and a script is
# not about eight things at once.
TOP_N = 5

# The longest noun phrase worth offering as a theme. Past this it is a
# sentence, not a subject.
MAX_THEME_WORDS = 4


class ThemeKind(str, Enum):
    """What SORT of thing the theme is. `str` so it serialises straight into
    the slot map as "place" / "era" and the map stays plain json."""
    PLACE = "place"
    ERA = "era"
    CULTURE = "culture"
    SUBJECT = "subject"


# spaCy's entity labels, mapped to the four kinds. This is the whole of the
# kind decision: a tag lookup, so it works on every name in English.
ENTITY_KINDS = {
    "GPE": ThemeKind.PLACE, "LOC": ThemeKind.PLACE, "FAC": ThemeKind.PLACE,
    "DATE": ThemeKind.ERA, "TIME": ThemeKind.ERA,
    "NORP": ThemeKind.CULTURE,
}


@dataclass(frozen=True)
class Theme:
    """One theme: the words, and what sort of thing they name.

    Two fields, deliberately — the slot map takes them as two plain strings
    ("theme_kind", "theme_text") and stays json. There is no Theme object
    downstream and there should not be one: the map is read by three other
    files and its flatness is the point.
    """
    kind: str            # a ThemeKind value
    text: str            # the script's own words for it


# =============================================================================
# THE ONE CALL
# =============================================================================

def themes_for_segments(segments: list, doc=None) -> list:
    """One Theme (or None) per line segment, in script order.

    @input segments = the split lines, in order — the same list
        join_segments() was built from.
    @input doc = the shared spaCy parse of join_segments(segments). Hand it
        over: it is where the candidates and their kinds come from, and
        parsing again would be the third parse rule 7 forbids.

    @output a list the same length as `segments`. themes[i] is what segment i
        is about, or None when nothing is.

    NEVER RAISES. No KeyBERT, no embedding model, no parse — every segment
    gets None and every search term is unchanged.
    """
    segments = [str(s or "") for s in (segments or [])]
    if not segments:
        return []
    blank = [None] * len(segments)
    try:
        candidates = _candidates(doc)
        if not candidates:
            return blank
        model = _keybert()
        if model is None:
            return blank
        return _themes(model, segments, candidates)
    except Exception as exc:                            # pragma: no cover
        print(f"[themes] unavailable ({exc}) — no theme on any line")
        return blank


# =============================================================================
# internals
# =============================================================================

_KEYBERT = None            # None = untried, False = not installed


def _keybert():
    """KeyBERT on a Model2Vec backend, or None.

    Lazy and cached: the model is ~8M parameters and loads in well under a
    second, but importing keybert pulls scikit-learn in with it and most
    callers of this folder never ask for a theme.
    """
    global _KEYBERT
    if _KEYBERT is None:
        try:
            from keybert import KeyBERT
            from model2vec import StaticModel
            _KEYBERT = KeyBERT(StaticModel.from_pretrained(EMBEDDING_MODEL))
        except Exception as exc:                        # pragma: no cover
            _KEYBERT = False
            print(f"[themes] KeyBERT/Model2Vec unavailable ({exc}) — "
                  f"no themes. Install with:  uv pip install keybert "
                  f"--no-deps scikit-learn model2vec")
    return _KEYBERT or None


def _candidates(doc) -> dict:
    """{phrase: ThemeKind} — every noun phrase the script offers as a theme.

    THE PHRASE IS LOWERCASED, because KeyBERT scores through a
    CountVectorizer that lowercases the document and would then match none of
    them. The script's own capitalisation is kept beside it and put back at
    the end, so a theme reads "Egypt" and not "egypt".

    Determiners and possessives are trimmed off the front: "the Roman empire"
    and "Roman empire" are the same theme, and only one of them is a search
    term.
    """
    if doc is None:
        return {}
    kinds, surfaces = {}, {}
    for chunk in doc.noun_chunks:
        start = chunk.start
        while start < chunk.end and doc[start].pos_ in {"DET", "PRON", "PART"}:
            start += 1
        span = doc[start:chunk.end]
        text = " ".join(span.text.split()).strip(" ,.;:!?'\"")
        if not text or len(text.split()) > MAX_THEME_WORDS:
            continue
        if span.root.pos_ == "PRON":
            continue                       # "it" is not what anything is about
        key = text.lower()
        kinds.setdefault(key, _kind_of(span))
        surfaces.setdefault(key, text)
    return {key: (kinds[key], surfaces[key]) for key in kinds}


def _kind_of(span) -> ThemeKind:
    """The phrase's kind, off spaCy's entity labels. SUBJECT is the answer
    whenever no token in it is a named entity of a kind we map — which is
    most noun phrases, and is correct: "the spice trade" is a subject."""
    for token in span:
        kind = ENTITY_KINDS.get(token.ent_type_)
        if kind is not None:
            return kind
    return ThemeKind.SUBJECT


def _themes(model, segments: list, candidates: dict) -> list:
    """The three scopes, then one theme per segment."""
    words = list(candidates)
    longest = max(len(w.split()) for w in words)

    def rank(text: str) -> dict:
        """{phrase: score} for the top TOP_N of one piece of text."""
        if not text.strip():
            return {}
        scored = model.extract_keywords(
            text, candidates=words, top_n=TOP_N,
            keyphrase_ngram_range=(1, longest), stop_words=None)
        return {phrase: score for phrase, score in scored}

    whole = rank(" ".join(segments))
    if not whole:
        return [None] * len(segments)

    out = []
    for i in range(len(segments)):
        so_far = rank(" ".join(segments[:i + 1]))
        lo = max(0, i - WINDOW_SEGMENTS)
        window = rank(" ".join(segments[lo:i + WINDOW_SEGMENTS + 1]))
        # LIVE = said by now, and still being talked about here. The whole
        # script is NOT a membership test — it is the chooser below, so a
        # theme that only matters for one paragraph can still be that
        # paragraph's theme ("64 AD" is nowhere near the top of the whole of
        # script-rome, and it is exactly what its own line is about).
        live = [w for w in so_far if w in window]
        if not live:
            out.append(None)
            continue
        best = max(live, key=lambda w: (whole.get(w, 0.0), window[w]))
        kind, surface = candidates[best]
        out.append(Theme(kind=kind.value, text=surface))
    return out


if __name__ == "__main__":
    # uv run _theme_engine.py ../script-rome.txt — what a script is about.
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
    import PATHS  # noqa: F401
    from sentence_splitter import split_text_into_sections
    from _visualisables_pipeline import join_segments, parse_script

    here = Path(__file__).resolve().parent
    name = sys.argv[1] if len(sys.argv) > 1 else "script-rome.txt"
    path = next((p for p in (Path(name), here.parent / name,
                             here.parent.parent / name) if p.exists()), None)
    if path is None:
        raise SystemExit(f"no such script: {name}")

    chunks = split_text_into_sections(path.read_text())
    segments = [c.text for c in chunks]
    doc = parse_script(join_segments(segments))
    print(f"=== {path.name} ===")
    for segment, theme in zip(segments, themes_for_segments(segments, doc)):
        label = f"{theme.kind:<8} {theme.text}" if theme else "-"
        print(f"  {label:<34} {segment[:40]}")
