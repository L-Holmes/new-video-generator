"""
populate_entity_fame.py  —  build ENTITY_FAME_CACHE.json from real data
========================================================================
The decision engine's famous/obscure locks read ONLY from
ENTITY_FAME_CACHE.json.  This script populates that cache from the
Wikipedia/Wikidata public APIs.  Run it on a machine with network access:

    # classify every entity the splitter found in a script's cache:
    python3 populate_entity_fame.py --from-splitmeta spices-CACHE/split-and-lable/TESTING_SPLITMETA2-spices.json

    # or classify explicit names:
    python3 populate_entity_fame.py "Banda Islands" "Alaric the Goth" "Sahara"

It MERGES into the existing cache (never overwrites), so it is safe to
re-run per script.  Names it cannot resolve are written as "obscure" if
Wikipedia has an article for them but the signals are weak, and are left
OUT of the cache entirely if no article exists (the engine treats absent
names as "unknown" -> soft nudge, never a hard lock).

CLASSIFICATION SIGNALS + THRESHOLDS (documented so they're tunable):
  • Wikidata sitelink count (how many language editions have an article) —
    a famous thing is written about everywhere.       FAMOUS_SITELINKS = 40
  • Wikipedia pageviews over the last 60 days.        FAMOUS_VIEWS_60D = 150_000
  A name is "famous" if EITHER threshold is met, "obscure" if it has an
  article but meets neither.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parent / "ENTITY_FAME_CACHE.json"

FAMOUS_SITELINKS = 40
FAMOUS_VIEWS_60D = 150_000

_UA = {"User-Agent": "split-and-label-fame/1.0 (media pipeline; contact: local)"}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=20) as fh:
        return json.load(fh)


def resolve_article(name: str):
    """Search Wikipedia for the canonical article title, or None."""
    q = urllib.parse.quote(name)
    data = _get_json(
        "https://en.wikipedia.org/w/api.php?action=query&list=search"
        f"&srsearch={q}&srlimit=1&format=json")
    hits = data.get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def sitelink_count(title: str) -> int:
    q = urllib.parse.quote(title)
    data = _get_json(
        "https://en.wikipedia.org/w/api.php?action=query&prop=pageprops"
        f"&titles={q}&format=json")
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        qid = page.get("pageprops", {}).get("wikibase_item")
        if not qid:
            return 0
        wd = _get_json("https://www.wikidata.org/w/api.php?action=wbgetentities"
                       f"&ids={qid}&props=sitelinks&format=json")
        return len(wd.get("entities", {}).get(qid, {}).get("sitelinks", {}))
    return 0


def pageviews_60d(title: str) -> int:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=60)
    t = urllib.parse.quote(title.replace(" ", "_"), safe="")
    data = _get_json(
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"en.wikipedia/all-access/user/{t}/daily/"
        f"{start:%Y%m%d}/{end:%Y%m%d}")
    return sum(item.get("views", 0) for item in data.get("items", []))


def classify(name: str):
    """Return 'famous' / 'obscure' / None (no article -> stay unknown)."""
    title = resolve_article(name)
    if title is None:
        return None, None
    links = sitelink_count(title)
    views = 0
    if links < FAMOUS_SITELINKS:          # only fetch views when undecided
        try:
            views = pageviews_60d(title)
        except Exception:
            views = 0
    fame = ("famous" if links >= FAMOUS_SITELINKS or views >= FAMOUS_VIEWS_60D
            else "obscure")
    return fame, {"title": title, "sitelinks": links, "views_60d": views}


def entities_from_splitmeta(path: Path):
    triples = json.loads(path.read_text(encoding="utf-8"))
    names = []
    for _text, _ids, meta in triples:
        for e in meta.get("ents", []):
            if e["label"] in {"GPE", "LOC", "PERSON", "ORG", "FAC",
                              "EVENT", "WORK_OF_ART", "NORP"}:
                names.append(e["text"])
    return sorted(set(names), key=str.lower)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("names", nargs="*", help="entity names to classify")
    ap.add_argument("--from-splitmeta", type=Path,
                    help="pull entity names out of a SPLITMETA cache file")
    args = ap.parse_args()

    names = list(args.names)
    if args.from_splitmeta:
        names += entities_from_splitmeta(args.from_splitmeta)
    if not names:
        ap.error("give entity names and/or --from-splitmeta")

    cache = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    for name in names:
        key = name.lower().strip()
        if key in cache:
            print(f"[skip]    {name!r} already cached -> {cache[key]}")
            continue
        try:
            fame, info = classify(name)
        except Exception as exc:
            print(f"[error]   {name!r}: {exc}")
            continue
        if fame is None:
            print(f"[unknown] {name!r}: no Wikipedia article — left uncached")
            continue
        cache[key] = fame
        print(f"[{fame:>7}] {name!r}  ({info})")
        time.sleep(0.3)          # be polite to the APIs

    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False,
                                     sort_keys=True), encoding="utf-8")
    print(f"\nwrote {len(cache)} entries -> {CACHE_PATH}")


if __name__ == "__main__":
    main()
