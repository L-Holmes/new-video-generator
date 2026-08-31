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
    if "Yes" not in token.morph.get("Poss"):
        return name
    return name + ("'" if name.endswith("s") else "'s")


def number_of(token):
    n = token.morph.get("Number")
    return n[0] if n else None


# ---------------------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    debug = "--debug" in sys.argv
    all_pron = "--all-pron" in sys.argv
    use_deixis = "--no-deixis" not in sys.argv
    use_fallback = "--no-fallback" not in sys.argv
    shorten = "--full-spans" not in sys.argv
    if not args:
        print(__doc__)
        sys.exit(1)

    text = Path(args[0]).read_text()

    print("loading spaCy...")
    try:
        nlp = spacy.load("en_core_web_trf")
    except OSError:
        print("  !! en_core_web_trf missing -- NER will be weaker.")
        print("     uv run python -m spacy download en_core_web_trf")
        nlp = spacy.load("en_core_web_sm")
    doc = nlp(text)

    toks = ([t for t in doc if t.pos_ == "PRON"] if all_pron
            else [t for t in doc if t.tag_ in ("PRP", "PRP$")])
    targets = [(t.idx, t.idx + len(t.text), t.text, t) for t in toks]

    skipped = sum(1 for t in doc if t.pos_ == "PRON") - len(targets)
    print(f"{len(targets)} referential pronouns to resolve"
          f"{f' ({skipped} non-referential skipped)' if skipped else ''}\n")

    results = {}
    for label, kind, checkpoint, weight in MODELS:
        print(f"running {label} ...", end=" ", flush=True)
        t0 = time.perf_counter()
        try:
            results[label] = run_model(kind, checkpoint, text)
            print(f"{time.perf_counter() - t0:.1f}s  ({len(results[label])} clusters)")
        except Exception as e:
            print(f"SKIPPED -- {type(e).__name__}: {e}")

    active = sorted([m for m in MODELS if m[0] in results], key=lambda m: -m[3])
    if not active:
        print("no models ran")
        sys.exit(1)
    total_weight = sum(m[3] for m in active)

    if debug:
        print("\n--- alignment check: first cluster of each model ---")
        for label, *_ in active:
            cl = results[label][0] if results[label] else []
            print(f"  {label}: {[text[s:e] for s, e in cl[:6]]}")
        print()

    resolutions = {}
    recent = []   # (char_pos, name, number) of everything resolved so far

    for n, (start, end, surface, token) in enumerate(targets, 1):
        print(f"\n{'-' * 70}")
        print(f"abstract word/phrase #{n}) {surface!r} at index {start}:")
        print("models sorted most to least accurate\n")

        votes, display, answered = defaultdict(float), {}, 0

        for rank, (label, _, _, weight) in enumerate(active, 1):
            span, cluster = find_cluster(results[label], start, end)
            ante = pick_antecedent(doc, cluster, span, start) if cluster else None
            if ante is not None:
                name = mention_name(doc, ante, shorten)
                shown = name
            else:
                name, shown = None, "-- no antecedent --"
            if len(shown) > 58:
                shown = shown[:55] + "..."
            print(f"  * model {rank}) {label:<20} (w={weight:.3f})  {shown}")
            if name:
                key = canonical_key(name)
                votes[key] += weight
                display.setdefault(key, name)
                answered += 1

        print("\nOverall:")
        if votes:
            merged = merge_votes(votes, display)
            ranked = sorted(merged.items(), key=lambda kv: -kv[1][0])
            for key, (score, shown) in ranked:
                print(f"  * {shown:<50} : {score / total_weight:.2f}")
            print(f"  ({answered}/{len(active)} models gave an answer)")
            score, name = ranked[0][1]
            resolutions[(start, end)] = (name, score / total_weight, surface, token)
            recent.append((start, name, None))
            continue

        # --- nothing from the models ---------------------------------------
        label = deictic_label(token) if use_deixis else None
        if label:
            print(f"  * {label:<50} : 1.00   [deictic, spaCy morphology]")
            resolutions[(start, end)] = (label, 1.0, surface, token)
            continue

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
            print(f"  * {match:<50} : {FALLBACK_CONFIDENCE:.2f}   "
                  f"[recency fallback -- no model clustered this; ~50-55% reliable]")
            resolutions[(start, end)] = (match, FALLBACK_CONFIDENCE, surface, token)
            continue

        print("  * (unresolved -- no model clustered this pronoun)")
        resolutions[(start, end)] = (None, 0.0, surface, token)

    # ---- canonicalise names document-wide ---------------------------------
    all_names = [r[0] for r in resolutions.values() if r[0]]
    canon = build_canonical_map(all_names)

    print(f"\n\n{'=' * 70}\nENTITY CANONICALISATION\n{'=' * 70}")
    shown_groups = defaultdict(set)
    for name in all_names:
        shown_groups[canon[canonical_key(name)]].add(name)
    for label, variants in sorted(shown_groups.items()):
        others = sorted(v for v in variants if v != label)
        if others:
            print(f"  {label}  <-  {', '.join(others)}")

    # ---- rebuild ----------------------------------------------------------
    print(f"\n\n{'=' * 70}")
    print("FINAL TEXT")
    print("=" * 70 + "\n")

    out, cursor = [], 0
    for (start, end) in sorted(resolutions):
        answer, conf, surface, token = resolutions[(start, end)]
        out.append(text[cursor:start])
        if answer:
            name = canon.get(canonical_key(answer), answer)
            out.append(f"[{possessive(name, token)}]")
        else:
            out.append(f"[{surface} -> ?]")
        cursor = end
    out.append(text[cursor:])
    print("".join(out))

    strong = sum(1 for a, c, _, _ in resolutions.values() if a and c >= CONFIDENCE_THRESHOLD)
    weak = sum(1 for a, c, _, _ in resolutions.values() if a and c < CONFIDENCE_THRESHOLD)
    none_ = sum(1 for a, _, _, _ in resolutions.values() if not a)
    print(f"\n\n{strong} confident, {weak} low-confidence (recency/deictic), "
          f"{none_} unresolved, out of {len(targets)}.")


if __name__ == "__main__":
    main()
