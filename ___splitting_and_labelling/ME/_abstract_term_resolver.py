"""
Ensemble pronoun resolution.

Runs several coreference models over the same text, asks each what every pronoun
refers to, weights their answers, and reports a distribution. Anything the
models miss falls back to recency.

All linguistic decisions (pronoun, entity, determiner, person, number,
possessive, head noun) come from spaCy. No word lists here.

Setup:
    uv pip install maverick-coref fastcoref spacy
    uv run python -m spacy download en_core_web_trf

Usage:
    uv run python ensemble_coref.py script.txt
    uv run python ensemble_coref.py script.txt --debug        # alignment check
    uv run python ensemble_coref.py script.txt --all-pron     # incl. there/which/that
    uv run python ensemble_coref.py script.txt --no-deixis    # don't label I/you/we
    uv run python ensemble_coref.py script.txt --no-fallback  # allow [word -> ?]
    uv run python ensemble_coref.py script.txt --full-spans   # don't shorten names
    uv run python ensemble_coref.py script.txt --online       # allow HF network

AS A LIBRARY -- one call, one map, once per script:

    from _abstract_term_resolver import resolve_all_abstract_terms

    abstract_terms = resolve_all_abstract_terms(the_whole_script)
    # {(395, 397): {"surface": "it", "resolved": "valley", "confidence": 0.26,
    #               "source": "models", "possessive": False, "number": "Sing"}}

The map is keyed by WHERE THE PRONOUN IS, never by rewritten text. The
visualisables code looks answers up in it; it must never be handed a script
with the pronouns swapped out, because the template built from those offsets
is what goes ON SCREEN and the viewer still hears "it".

ONE PARSE OR TWO?  (measured 2026-09-03, and this is the whole answer)
    This file's own default is en_core_web_trf. The visualisables code parses
    with the shared en_core_web_sm. Running both, with the coreference models
    held fixed, over script-whales / script-rome / script-spices:

        11 pronouns, 11 IDENTICAL answers  (2 / 3 / 6 per script)
        parse: trf 0.10-0.16 s vs sm 0.02-0.03 s, plus a second model to load

    So the answers do not depend on which parse this reads morphology and
    entities off, and a caller that already has an sm Doc of the SAME text
    should pass it:  resolve_all_abstract_terms(text, doc=already_parsed).
    myownstuff.py does. The trf default stays for the command line, where
    there is no other parse to share and NER is all this reads.

    What must NEVER cross between the two: a Doc, a Token, a token index.
    sm and trf tokenise differently. Only CHARACTER OFFSETS and STRINGS go in
    the map, which is exactly what makes the two parses interchangeable.
"""

import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

if "--online" not in sys.argv:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import spacy
import torch

for noisy in ("httpx", "urllib3", "filelock", "transformers", "fastcoref", "datasets"):
    logging.getLogger(noisy).setLevel(logging.ERROR)


# --- compat shims -----------------------------------------------------------

_orig_load = torch.load
torch.load = lambda *a, **k: _orig_load(*a, **{**k, "weights_only": False})


def _patch_transformers():
    import transformers

    _orig_cfg = transformers.AutoConfig.from_pretrained

    def _cfg(*a, **k):
        cfg = _orig_cfg(*a, **k)
        try:
            cfg._attn_implementation = "eager"
        except Exception:
            pass
        return cfg

    transformers.AutoConfig.from_pretrained = _cfg

    for cls_name in ("AutoModel", "LongformerModel"):
        cls = getattr(transformers, cls_name, None)
        if cls is None:
            continue

        def make(orig):
            def wrapped(*a, **k):
                k.setdefault("attn_implementation", "eager")
                return orig(*a, **k)
            return wrapped

        cls.from_pretrained = make(cls.from_pretrained)

    base = getattr(transformers, "PreTrainedModel", None)
    if base is not None and not hasattr(base, "all_tied_weights_keys"):
        base.all_tied_weights_keys = {}


_patch_transformers()


def _patch_fastcoref():
    for mod_path, cls_name in (
        ("fastcoref.modeling_lingmess", "LingMessModel"),
        ("fastcoref.modeling_fcoref", "FCorefModel"),
    ):
        try:
            mod = __import__(mod_path, fromlist=[cls_name])
            cls = getattr(mod, cls_name, None)
            if cls is not None and not isinstance(
                getattr(cls, "all_tied_weights_keys", None), dict
            ):
                cls.all_tied_weights_keys = {}
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Models. maverick-ontonotes (83.6) and lingmess (81.4) are both OntoNotes
# CoNLL-F1 and directly comparable. PreCo and LitBank figures are from their
# own benchmarks -- those weights are a prior, not a measurement.
# ---------------------------------------------------------------------------

MODELS = [
    ("maverick-ontonotes", "maverick",  "sapienzanlp/maverick-mes-ontonotes", 0.836),
    ("maverick-preco",     "maverick",  "sapienzanlp/maverick-mes-preco",     0.820),
    ("lingmess",           "fastcoref", None,                                 0.814),
    ("maverick-litbank",   "maverick",  "sapienzanlp/maverick-mes-litbank",   0.780),
]

CONFIDENCE_THRESHOLD = 0.25
FALLBACK_CONFIDENCE = 0.10     # recency guesses are ~50-55% accurate; flag them
LONG_MENTION_TOKENS = 8
ENTITY_COVERAGE = 0.5

NAME_ENTS = {"PERSON", "GPE", "LOC", "FAC", "ORG", "NORP", "EVENT", "WORK_OF_ART"}

# What an answer is allowed to BE. pick_antecedent() only refuses a pronoun,
# so a cluster can otherwise hand back a verb and "the cat didn't like it"
# resolves to "revved" -- a real answer on the tractor narrative. A thing a
# pronoun stands for is a noun.
MENTION_POS = {"NOUN", "PROPN", "NUM"}


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

def align_tokens(tokens, text):
    offsets, cursor = [], 0
    for tok in tokens:
        idx = text.find(tok, cursor)
        if idx != -1:
            offsets.append((idx, idx + len(tok)))
            cursor = idx + len(tok)
            continue
        core = "".join(ch for ch in tok if ch.isalnum())
        idx = text.find(core, cursor) if core else -1
        if idx == -1:
            offsets.append(None)
            continue
        offsets.append((idx, idx + len(core)))
        cursor = idx + len(core)
    return offsets


def maverick_clusters(out, text):
    offsets = align_tokens(out.get("tokens", []), text)
    clusters = []
    for cl in out.get("clusters_token_offsets", []):
        spans = []
        for ts, te in cl:
            if ts >= len(offsets) or te >= len(offsets):
                continue
            a, b = offsets[ts], offsets[te]
            if a is None or b is None:
                continue
            spans.append((a[0], b[1]))
        if len(spans) >= 2:
            clusters.append(spans)
    return clusters


# ---------------------------------------------------------------------------
# Running models
# ---------------------------------------------------------------------------

def run_maverick(checkpoint, text):
    from maverick import Maverick
    model = Maverick(hf_name_or_path=checkpoint, device="cpu")
    model.model.to(torch.float32)
    out = model.predict(text)
    del model
    return maverick_clusters(out, text)


def run_fastcoref(text):
    _patch_fastcoref()
    from fastcoref import LingMessCoref
    model = LingMessCoref(device="cpu")
    pred = model.predict(texts=[text])[0]
    del model
    return [[tuple(sp) for sp in cl]
            for cl in pred.get_clusters(as_strings=False) if len(cl) >= 2]


def run_model(kind, checkpoint, text):
    return run_maverick(checkpoint, text) if kind == "maverick" else run_fastcoref(text)


# ---------------------------------------------------------------------------
# Turning a mention span into a usable tag
# ---------------------------------------------------------------------------

def mention_name(doc, sp, shorten=True):
    """
    A concise, renderable name for a mention.

    Order:
      1. The named entity that contains the mention's head noun. Gives
         'the Atacama Desert in Chile' rather than a truncation of it.
      2. A PERSON entity inside the mention -- people are the strongest visual
         anchor, so 'a technician named Gustav Holm' -> 'Gustav Holm'.
      3. The head noun plus its adjectival / compound / numeric modifiers.
         Keeps descriptive adjectives, drops relative clauses and PPs:
         'marine fossils scattered through regions that are now brutally dry'
         -> 'marine fossils'.
    """
    if not shorten:
        return sp.text.strip()

    root = sp.root
    for ent in doc.ents:
        if ent.start <= root.i < ent.end:
            return ent.text.strip()
    for ent in doc.ents:
        if ent.label_ == "PERSON" and sp.start <= ent.start and ent.end <= sp.end:
            return ent.text.strip()

    keep = {root.i}
    for child in root.children:
        if child.dep_ in ("amod", "compound", "nummod") and sp.start <= child.i < root.i:
            keep.add(child.i)
    lo, hi = min(keep), max(keep)
    toks = [t for t in doc[lo:hi + 1] if t.pos_ != "DET" and not t.is_punct]
    name = " ".join(t.text for t in toks).strip()
    return name or sp.text.strip()


def canonical_key(name):
    return name.lower().strip()


def build_canonical_map(names):
    """
    Collapse name variants document-wide so the renderer keeps one asset per
    entity. Variants are grouped by containment; the longest form in a group
    becomes the canonical label.
    """
    uniq = sorted({canonical_key(n) for n in names}, key=len, reverse=True)
    groups, mapping = [], {}
    for k in uniq:
        for g in groups:
            if k in g[0] or g[0] in k:
                g[1].append(k)
                break
        else:
            groups.append((k, [k]))
    longest = {}
    for n in names:
        k = canonical_key(n)
        longest[k] = max(longest.get(k, ""), n, key=len)
    for head, members in groups:
        label = max((longest[m] for m in members), key=len)
        for m in members:
            mapping[m] = label
    return mapping


# ---------------------------------------------------------------------------
# Answer selection
# ---------------------------------------------------------------------------

def find_cluster(clusters, start, end):
    """The cluster whose mention IS this pronoun -- not one containing it."""
    slack = 2
    for cluster in clusters:
        for cs, ce in cluster:
            if cs <= start and end <= ce and (ce - cs) <= (end - start) + slack:
                return (cs, ce), cluster
    return None, None


def entity_coverage(doc, sp):
    best = 0.0
    for ent in doc.ents:
        overlap = min(sp.end, ent.end) - max(sp.start, ent.start)
        if overlap > 0:
            best = max(best, overlap / len(sp))
    return best


def pick_antecedent(doc, cluster, exclude, pronoun_start):
    """Prefer: before the pronoun > named entity (longest) > short > long."""
    named, plain, overlong = [], [], []
    for cs, ce in cluster:
        if (cs, ce) == exclude:
            continue
        sp = doc.char_span(cs, ce, alignment_mode="expand")
        if sp is None or len(sp) == 0 or sp.root.pos_ == "PRON":
            continue
        before = 0 if ce <= pronoun_start else 1
        if entity_coverage(doc, sp) >= ENTITY_COVERAGE:
            named.append((before, sp))
        elif len(sp) <= LONG_MENTION_TOKENS:
            plain.append((before, sp))
        else:
            overlong.append((before, sp))

    if named:
        return min(named, key=lambda p: (p[0], -len(p[1]), p[1].start))[1]
    if plain:
        return min(plain, key=lambda p: (p[0], len(p[1]), p[1].start))[1]
    if overlong:
        return min(overlong, key=lambda p: (p[0], len(p[1]), p[1].start))[1]
    return None


def merge_votes(votes, display):
    """Fold answers where one contains another; combine their weights."""
    keys = sorted(votes, key=len, reverse=True)
    merged, mapping = {}, {}
    for k in keys:
        for m in merged:
            if k in m:
                mapping[k] = m
                break
        else:
            merged[k] = 0.0
            mapping[k] = k
    for k, w in votes.items():
        merged[mapping[k]] += w
    return {k: (w, display[k]) for k, w in merged.items()}


def deictic_label(token):
    """Narrator / viewer, from spaCy's Person morphology."""
    person = token.morph.get("Person")
    if not person:
        return None
    return {"1": "the narrator", "2": "the viewer"}.get(person[0])




def possessive(name, token):
    """Add an apostrophe when the pronoun itself was possessive (Poss=Yes)."""
    if not is_possessive(token):
        return name
    return possessive_form(name)


def is_possessive(token):
    """Was the pronoun ITSELF possessive?  "its windscreen" -> True"""
    return "Yes" in token.morph.get("Poss")


def possessive_form(name):
    """The apostrophe half of possessive(), for a caller that kept only the
    flag and not the token -- which is every caller of the map, because a
    spaCy Token must never cross that boundary (see ONE PARSE OR TWO above).
    e.g. "the tractor" -> "the tractor's"      "Locals" -> "Locals'"
    """
    return name + ("'" if name.endswith("s") else "'s")


def number_of(token):
    n = token.morph.get("Number")
    return n[0] if n else None


# ---------------------------------------------------------------------------
# THE LIBRARY CALL -- one map for a whole script
#
# This is what myownstuff.py step 1) runs ONCE, before any visualisable is
# looked at:
#
#     abstract_terms = resolve_all_abstract_terms(the_whole_script)
#     # {(395, 397): {"surface": "it", "resolved": "valley", ...}}
#
# and then hands to the visualisables pipeline, which LOOKS THE ANSWER UP
# rather than running a coreference model of its own.
#
# main() below is now argv parsing and printing only -- every decision it
# used to make lives in resolve_all_abstract_terms().
# ---------------------------------------------------------------------------


class AbstractTerms(dict):
    """{(start_char, end_char): row} -- one row per pronoun, keyed by WHERE
    THE PRONOUN IS in the text it was resolved against.

    Keyed by POSITION and not by word, because the same "It" turns up ten
    times in a script and means something different each time.

    e.g. terms[(395, 397)] == {
             "surface":    "it",       # what the script says, verbatim
             "resolved":   "valley",   # what it points at, canonicalised
             "confidence": 0.26,       # score / total model weight, 0..1
             "source":     "models",   # models | deictic | recency | none
             "possessive": False,      # "its windscreen" -> True
             "number":     "Sing",     # Sing | Plur | None
         }

    A dict, so a lookup is a lookup. The two extras ride alongside:

        .canonical   {canonical_key(name): label} -- the document-wide name
                     map "resolved" has already been put through, so a caller
                     can collapse ITS OWN names onto the same labels.
                     e.g. {"the valley": "the valley", "valley": "the valley"}
        .models_run  which coreference models actually answered.
                     e.g. ["maverick-ontonotes", "lingmess"]
    """

    def __init__(self, rows=(), canonical=None, models_run=()):
        super().__init__(rows)
        self.canonical = dict(canonical or {})
        self.models_run = list(models_run)


def _silent(*args, **kwargs):
    """The default for resolve_all_abstract_terms(log=...): say nothing.
    A library that prints in the middle of a video render is a bug."""


_NLP = None


def load_nlp(log=None):
    """The parser the resolver reads morphology and entities off. Cached --
    en_core_web_trf is ~10 s to load and this may be called per script."""
    global _NLP
    say = log or _silent
    if _NLP is None:
        say("loading spaCy...")
        try:
            _NLP = spacy.load("en_core_web_trf")
        except OSError:
            say("  !! en_core_web_trf missing -- NER will be weaker.")
            say("     uv run python -m spacy download en_core_web_trf")
            _NLP = spacy.load("en_core_web_sm")
    return _NLP


def resolve_all_abstract_terms(text,
                               models=MODELS,
                               doc=None,
                               all_pron=False,
                               use_deixis=True,
                               use_fallback=True,
                               shorten=True,
                               debug=False,
                               log=None):
    """Every pronoun in TEXT, and the thing each one points at.

    @input text = the whole script, as ONE string. Whatever text you pass is
        the coordinate system the answers are keyed by, so pass the SAME text
        the visualisables pipeline will see -- join_segments(all_segments).
        e.g. "In Egypt, there's a valley ... It was once covered ..."
    @input models = which coreference models to vote. All four by default,
        ~40 s. e.g. [m for m in MODELS if m[0] == "lingmess"]  -> ~4 s, one
        model, no vote.
    @input doc = a spaCy Doc of the SAME text, if you already have one, to
        skip the second parse. Only its morphology and entities are used.
    @input all_pron / use_deixis / use_fallback / shorten / debug = the
        command line's flags, same meanings (see the module docstring).
    @input log = where progress goes. log=print for the command line; the
        default says nothing at all.

    @output AbstractTerms -- {(start_char, end_char): row}, one row per
        pronoun. Keyed by where the pronoun IS, NEVER by the rewritten text:
        the visualisables template is what goes on screen, and swapping the
        words would move every offset in it.
        e.g. {(230, 234): {"surface": "them", "resolved": "Whale skulls",
                           "confidence": 0.26, "source": "models",
                           "possessive": False, "number": "Plur"},
              (395, 397): {"surface": "it", "resolved": "valley", ...}}

    THE STEPS  (this is main()'s old body, unchanged in order)
        1  targets   every PRP / PRP$ in the text
        2  models    run each one over the raw text -> clusters of spans
        3  vote      per pronoun, the weighted answer across the models
        3b BUT       an answer has to be a NOUN, or "it" resolves to "revved"
        4  deictic   no model answered? "I"/"we" -> the narrator, "you" -> the viewer
        5  recency   still nothing? the last thing mentioned, at 0.10
        6  canon     one label per entity, document-wide
    """
    say = log or _silent
    if doc is None:
        doc = load_nlp(log)(text)

    # 1) TARGETS -- the pronouns worth asking about.
    toks = ([t for t in doc if t.pos_ == "PRON"] if all_pron
            else [t for t in doc if t.tag_ in ("PRP", "PRP$")])
    targets = [(t.idx, t.idx + len(t.text), t.text, t) for t in toks]

    skipped = sum(1 for t in doc if t.pos_ == "PRON") - len(targets)
    say(f"{len(targets)} referential pronouns to resolve"
        f"{f' ({skipped} non-referential skipped)' if skipped else ''}\n")

    # 2) MODELS -- each one reads the RAW TEXT, not the parse, so this is the
    #    one place the two tokenisations cannot disagree.
    results = {}
    for label, kind, checkpoint, weight in models:
        say(f"running {label} ...", end=" ", flush=True)
        t0 = time.perf_counter()
        try:
            results[label] = run_model(kind, checkpoint, text)
            say(f"{time.perf_counter() - t0:.1f}s  ({len(results[label])} clusters)")
        except Exception as e:
            say(f"SKIPPED -- {type(e).__name__}: {e}")

    active = sorted([m for m in models if m[0] in results], key=lambda m: -m[3])
    if not active:
        say("no models ran")
        return AbstractTerms()
    total_weight = sum(m[3] for m in active)

    if debug:
        say("\n--- alignment check: first cluster of each model ---")
        for label, *_ in active:
            cl = results[label][0] if results[label] else []
            say(f"  {label}: {[text[s:e] for s, e in cl[:6]]}")
        say("")

    resolutions = {}
    recent = []   # (char_pos, name, number) of everything resolved so far

    for n, (start, end, surface, token) in enumerate(targets, 1):
        say(f"\n{'-' * 70}")
        say(f"abstract word/phrase #{n}) {surface!r} at index {start}:")
        say("models sorted most to least accurate\n")

        # every row starts as "we could not tell", and a step below fills it
        row = {"surface": surface, "resolved": None, "confidence": 0.0,
               "source": "none", "possessive": is_possessive(token),
               "number": number_of(token)}
        resolutions[(start, end)] = row

        # 3) VOTE
        votes, display, answered = defaultdict(float), {}, 0

        for rank, (label, _, _, weight) in enumerate(active, 1):
            span, cluster = find_cluster(results[label], start, end)
            ante = pick_antecedent(doc, cluster, span, start) if cluster else None
            if ante is not None and ante.root.pos_ not in MENTION_POS:
                ante = None          # a verb is not a thing -- see MENTION_POS
            if ante is not None:
                name = mention_name(doc, ante, shorten)
                shown = name
            else:
                name, shown = None, "-- no antecedent --"
            if len(shown) > 58:
                shown = shown[:55] + "..."
            say(f"  * model {rank}) {label:<20} (w={weight:.3f})  {shown}")
            if name:
                key = canonical_key(name)
                votes[key] += weight
                display.setdefault(key, name)
                answered += 1

        say("\nOverall:")
        if votes:
            merged = merge_votes(votes, display)
            ranked = sorted(merged.items(), key=lambda kv: -kv[1][0])
            for key, (score, shown) in ranked:
                say(f"  * {shown:<50} : {score / total_weight:.2f}")
            say(f"  ({answered}/{len(active)} models gave an answer)")
            score, name = ranked[0][1]
            row.update(resolved=name, confidence=score / total_weight,
                       source="models")
            recent.append((start, name, None))
            continue

        # 4) DEICTIC -- nothing from the models -------------------------------
        label = deictic_label(token) if use_deixis else None
        if label:
            say(f"  * {label:<50} : 1.00   [deictic, spaCy morphology]")
            row.update(resolved=label, confidence=1.0, source="deictic")
            continue

        # 5) RECENCY ----------------------------------------------------------
        if use_fallback and recent:
            want = number_of(token)
            match = None
            for pos, name, num in reversed(recent):
                if pos >= start:
                    continue
                if want and num and want != num:
                    continue
                match = name
                break
            if match is None:
                match = recent[-1][1]
            say(f"  * {match:<50} : {FALLBACK_CONFIDENCE:.2f}   "
                f"[recency fallback -- no model clustered this; ~50-55% reliable]")
            row.update(resolved=match, confidence=FALLBACK_CONFIDENCE,
                       source="recency")
            continue

        say("  * (unresolved -- no model clustered this pronoun)")

    # 6) CANONICALISE names document-wide -----------------------------------
    all_names = [r["resolved"] for r in resolutions.values() if r["resolved"]]
    canon = build_canonical_map(all_names)

    say(f"\n\n{'=' * 70}\nENTITY CANONICALISATION\n{'=' * 70}")
    shown_groups = defaultdict(set)
    for name in all_names:
        shown_groups[canon[canonical_key(name)]].add(name)
    for label, variants in sorted(shown_groups.items()):
        others = sorted(v for v in variants if v != label)
        if others:
            say(f"  {label}  <-  {', '.join(others)}")

    for row in resolutions.values():
        if row["resolved"]:
            row["resolved"] = canon.get(canonical_key(row["resolved"]),
                                        row["resolved"])
    return AbstractTerms(resolutions, canon, [m[0] for m in active])


def rewrite_with_resolutions(text, terms):
    """The script with every pronoun swapped for what it points at.
    e.g. "Recovering it cost $2 million"
             -->  "Recovering [valley] cost $2 million"

    FOR A HUMAN TO READ, AND FOR NOTHING ELSE. Never hand this to the
    visualisables code: the substitution moves every character offset after
    it, and the template built from those offsets is what goes ON SCREEN --
    the viewer hears "it", so the screen has to say "it".
    """
    out, cursor = [], 0
    for (start, end) in sorted(terms):
        row = terms[(start, end)]
        out.append(text[cursor:start])
        if row["resolved"]:
            name = row["resolved"]
            out.append(f"[{possessive_form(name) if row['possessive'] else name}]")
        else:
            out.append(f"[{row['surface']} -> ?]")
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


# ---------------------------------------------------------------------------

def main():
    """The command line: read a file, resolve it, print the workings."""
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)

    text = Path(args[0]).read_text()

    terms = resolve_all_abstract_terms(
        text,
        all_pron="--all-pron" in sys.argv,
        use_deixis="--no-deixis" not in sys.argv,
        use_fallback="--no-fallback" not in sys.argv,
        shorten="--full-spans" not in sys.argv,
        debug="--debug" in sys.argv,
        log=print)
    if not terms.models_run:
        sys.exit(1)

    print(f"\n\n{'=' * 70}")
    print("FINAL TEXT")
    print("=" * 70 + "\n")
    print(rewrite_with_resolutions(text, terms))

    strong = sum(1 for r in terms.values()
                 if r["resolved"] and r["confidence"] >= CONFIDENCE_THRESHOLD)
    weak = sum(1 for r in terms.values()
               if r["resolved"] and r["confidence"] < CONFIDENCE_THRESHOLD)
    none_ = sum(1 for r in terms.values() if not r["resolved"])
    print(f"\n\n{strong} confident, {weak} low-confidence (recency/deictic), "
          f"{none_} unresolved, out of {len(terms)}.")


if __name__ == "__main__":
    main()
