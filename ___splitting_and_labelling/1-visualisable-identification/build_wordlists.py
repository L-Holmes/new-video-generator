"""
build_wordlists.py — fetch big, well-maintained open resources and cache
them into knowledge_base_data.json for _knowledge_base.py.

Run once (needs internet):    python build_wordlists.py

Sources (all public, all actively maintained):
  * Brysbaert, Warriner & Kuperman (2014) concreteness ratings —
    ~40,000 English words human-rated 1 (abstract) .. 5 (concrete).
    THE standard psycholinguistic resource for "can you photograph it".
  * mledoze/countries — 250 countries with English demonyms
    (Italy -> Italian, Greece -> Greek), capitals and alt spellings.
  * NLTK WordNet — downloaded as a side effect so the engine's
    hypernym lookups (person / place / weather / unit / time /
    collective) work offline afterwards.

The engine ALSO uses, at runtime, if installed:
  * gender-guesser (pip) — Jörg Michael's ~48,000 international first
    names database (covers Greek, Slavic, Asian, Arabic ... names),
    replacing any hand-typed name list.
  * NLTK WordNet — see above.
  * spaCy (optional) — vectors for the embedding link.

Everything degrades gracefully: with no data file and no libraries the
engine still runs on its small documented seed lists — but the point of
this script is that it should never have to.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "knowledge_base_data.json"

CONCRETENESS_URL = ("https://raw.githubusercontent.com/ArtsEngine/"
                    "concreteness/master/"
                    "Concreteness_ratings_Brysbaert_et_al_BRM.txt")
COUNTRIES_URL = ("https://raw.githubusercontent.com/mledoze/countries/"
                 "master/countries.json")


def fetch(url: str) -> bytes:
    print(f"fetching {url.split('/')[2]} ...")
    return urllib.request.urlopen(url, timeout=60).read()


def build() -> dict:
    data: dict = {}

    # ---- concreteness ratings (single words only) -----------------------
    txt = fetch(CONCRETENESS_URL).decode("utf-8")
    conc: dict[str, float] = {}
    for line in txt.strip().split("\n")[1:]:
        parts = line.split("\t")
        if len(parts) < 3 or parts[1] != "0":      # skip bigrams
            continue
        try:
            conc[parts[0].lower()] = round(float(parts[2]), 2)
        except ValueError:
            continue
    data["concreteness"] = conc
    print(f"  concreteness: {len(conc)} words")

    # ---- countries: places + demonyms -----------------------------------
    countries = json.loads(fetch(COUNTRIES_URL))
    demonyms: dict[str, str] = {}
    places: set[str] = set()
    for c in countries:
        common = c["name"]["common"].lower()
        dem = (c.get("demonyms", {}).get("eng", {}) or {}).get("m") or ""
        for name in {common, c["name"]["official"].lower(),
                     *[a.lower() for a in c.get("altSpellings", [])
                       if len(a) > 3]}:
            places.add(name)
            if dem:
                demonyms[name] = dem
        for cap in c.get("capital", []):
            places.add(cap.lower())
    data["demonyms"] = demonyms
    data["places"] = sorted(places)
    print(f"  demonyms: {len(demonyms)} | places: {len(places)}")

    # ---- WordNet download (side effect: offline afterwards) -------------
    try:
        import nltk
        nltk.download("wordnet", quiet=True)
        from nltk.corpus import wordnet as wn
        wn.synsets("test")
        print("  wordnet: downloaded and working")
    except Exception as exc:                      # pragma: no cover
        print(f"  wordnet: unavailable ({exc}) — engine will use seeds")

    return data


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), ensure_ascii=False))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
