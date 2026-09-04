"""
_knowledge_base.py — the outside world's answers, not ours.

    from _knowledge_base import kb_concreteness, kb_is_place

    kb_concreteness("tractor")   --> 5.0     (Brysbaert, human-rated)
    kb_concreteness("monopoly")  --> 2.69
    kb_is_place("kyoto")         --> True

NO LINGUISTICS ARE DECIDED HERE. Every function is a LOOKUP against a big,
maintained, open resource — that is the whole point of the file. What to DO
with the answer is _visualisables_extractor.py's business.

The lookups layer, in this order:
    1. an installed library    — WordNet, via nltk
    2. knowledge_base_data.json — built by build_wordlists.py from the
       Brysbaert (2014) concreteness ratings + mledoze/countries
    3. the seed list below     — a documented FALLBACK only, so the
       extractor still runs on a bare Python install

Every function answers "no opinion" (None / False / [] / set()) rather than
raising when a resource is missing, so nothing here can ever be the reason
the pipeline fails to run.

    HISTORY: this was the knowledge-base section of VISUAL_RECOMMENDER.py.
    That file's own attempt at finding the visualisables in a line is gone —
    _visualisables_extractor.py does that now — but its lookups were never
    part of the attempt, and they are what the extractor delegates to.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# Built by build_wordlists.py, which sits next to this file.
_DATA_PATH = Path(__file__).resolve().parent / "knowledge_base_data.json"
try:
    _DATA = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
except Exception:                                  # pragma: no cover
    _DATA = {}
_CONC: dict = _DATA.get("concreteness", {})
_DATA_PLACES: set = set(_DATA.get("places", []))

# The FALLBACK gazetteer — only reached when the data file is missing. The
# ancient-world names are here on purpose: no maintained open dataset covers
# "Byzantium" or "Carthage", and a script about Rome is full of them.
PLACE_NAMES = {
    "rome", "greece", "athens", "sparta", "troy", "egypt", "persia",
    "babylon", "carthage", "byzantium", "venice", "florence", "paris",
    "france", "italy", "spain", "germany", "russia", "england", "britain",
    "scotland", "ireland", "wales", "america", "mexico", "china", "japan",
    "india", "korea", "vietnam", "turkey", "arabia", "morocco", "kenya",
    "brazil", "peru", "cuba", "canada", "australia", "norway", "sweden",
    "denmark", "holland", "netherlands", "poland", "hungary", "austria",
    "switzerland", "portugal", "iceland", "mongolia", "tibet",
    "london", "york", "oxford", "cambridge", "edinburgh", "dublin",
    "berlin", "vienna", "moscow", "madrid", "lisbon", "amsterdam",
    "prague", "istanbul", "constantinople", "jerusalem", "mecca",
    "cairo", "alexandria", "pompeii", "tokyo", "kyoto", "beijing",
    "shanghai", "delhi", "mumbai", "chicago", "boston", "texas",
    "california", "hollywood", "manhattan", "brooklyn", "sydney",
}

_WN = None            # lazy WordNet handle: None = untried, False = absent


def _wn():
    """The nltk WordNet corpus, or False when it is not installed.

    Lazy, because loading the corpus is seconds and most callers of this
    module only ever want kb_concreteness().
    """
    global _WN
    if _WN is None:
        try:
            from nltk.corpus import wordnet as wordnet_corpus
            wordnet_corpus.synsets("test")         # force corpus load
            _WN = wordnet_corpus
        except Exception:                          # pragma: no cover
            _WN = False
    return _WN


def _wn_has_hypernym(word: str, targets: frozenset,
                     n_senses: int, pos=None) -> bool:
    """Is `word` a KIND OF anything in `targets`, over its first n senses?
    e.g. _wn_has_hypernym("kyoto", {"geographical_area.n.01"}, 2) --> True

    n_senses is the precision dial: 1 means "only its main sense counts",
    which is what stops "foot" being a unit of measurement.
    """
    wn = _wn()
    if not wn:
        return False
    try:
        synsets = wn.synsets(word, pos=pos or wn.NOUN)[:n_senses]
        for s in synsets:
            for path in s.hypernym_paths():
                if any(h.name() in targets for h in path):
                    return True
    except Exception:                              # pragma: no cover
        pass
    return False


def _wn_supersenses(word: str, n_senses: int, pos=None) -> list:
    """WordNet's own lexname for a word's first n senses — its SUPERSENSE.
    e.g. "cupboard" -> ["noun.artifact"]   "grow" -> ["verb.change", ...]
         "volcanic" -> ["adj.pert", ...]   "weight" -> ["noun.attribute"]

    26 noun types, 15 verb types and 3 adjective classes: the whole English
    vocabulary sorted into kinds for us, and a ready-made answer to "what
    sort of thing is this word". Empty when WordNet is not installed, which
    every caller must read as "no opinion".
    """
    wn = _wn()
    if not wn:
        return []
    try:
        return [s.lexname()
                for s in wn.synsets(word, pos=pos or wn.NOUN)[:n_senses]]
    except Exception:                              # pragma: no cover
        return []


def _wn_attribute_roots(word: str, pos=None) -> set:
    """The taxonomy nodes above every ATTRIBUTE this adjective measures.
    e.g. "old"       -> age.n.01     -> {..., attribute.n.02, property.n.02}
         "important" -> importance.n.01 -> {..., quality.n.01}

    A WordNet adjective often points at the noun naming the property it is a
    value of, and the noun taxonomy is what separates a property you can SEE
    (size, width, colour, integrity) from one you cannot (importance,
    individuality, fear). Empty means WordNet has no attribute pointer for
    it, which is not the same as "not visual" — the caller decides.
    """
    wn = _wn()
    if not wn:
        return set()
    names = set()
    try:
        for synset in wn.synsets(word, pos=pos or wn.ADJ):
            for attribute in synset.attributes():
                for path in attribute.hypernym_paths():
                    names |= {h.name() for h in path}
    except Exception:                              # pragma: no cover
        pass
    return names


@lru_cache(maxsize=50000)
def kb_concreteness(word: str) -> float | None:
    """Brysbaert et al. (2014) human rating, 1 (abstract) .. 5
    (concrete), ~37k words.  None = word not rated."""
    return _CONC.get(word.lower())


@lru_cache(maxsize=50000)
def kb_is_place(word: str) -> bool:
    """250 countries + capitals + alt spellings from mledoze/countries,
    the curated ancient/city seeds, then WordNet's location hypernym."""
    w = word.lower()
    if w in _DATA_PLACES or w in PLACE_NAMES:
        return True
    return _wn_has_hypernym(
        w, frozenset({"geographical_area.n.01", "district.n.01",
                      "urban_area.n.01"}), 2)


if __name__ == "__main__":
    # uv run _knowledge_base.py — is everything actually installed?
    print(f"data file : {_DATA_PATH}")
    print(f"            {len(_CONC)} rated words, {len(_DATA_PLACES)} places")
    print(f"wordnet   : {'yes' if _wn() else 'NO — lookups degrade to seeds'}")
    for w in ("tractor", "monopoly", "district", "whoosh"):
        print(f"  concreteness({w:<10}) = {kb_concreteness(w)}")
    for w in ("kyoto", "egypt", "monopoly"):
        print(f"  is_place({w:<10})     = {kb_is_place(w)}")
