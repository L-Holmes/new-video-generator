"""
STAMP FETCH — pull a handful of pictures for the decorate editor's STAMP
tab, at decorate time.

A hold_previous + decorate scene reuses the previous image as its footage,
so its search_term is free to describe what to STAMP ("jar of nutmeg").
When the row also carries a stamp_source (one of STAMP_SOURCE_TYPES, chosen
in the tagging tool's step 3), DECORATE_STAGE calls fetch_stamps() right
before opening the editor and the stamp tab starts pre-loaded (and active).

Sources: "stock" = the Pexels image search (same PEXELS_API_KEY the rest of
the pipeline uses); "wikipedia" = the MediaWiki search API's page thumbnails
(sized ~1400px — originals can be 50MB TIFFs). Results cache per
(source, term) under STAMPS_CACHE_DIR, so re-runs and re-opened editors are
instant and offline-safe. Every failure degrades gracefully to fewer (or
zero) stamps — the editor's own "pick a file" button always remains.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import hashlib
from pathlib import Path

import requests

from CONFIG import (
    PEXELS_API_KEY,
    STAMP_FETCH_COUNT,
    STAMP_SOURCE_TYPES,
    STAMPS_CACHE_DIR,
)

_UA = {"User-Agent": "stickman-vid-generator/1.0 (decorate stamp fetch)"}
_IMG_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _cache_dir(source: str, term: str) -> Path:
    key = hashlib.md5(f"{source}|{term.lower()}".encode()).hexdigest()[:12]
    return STAMPS_CACHE_DIR / f"{source}_{key}"


def _cached(d: Path) -> list[str]:
    if not d.is_dir():
        return []
    return sorted(str(p) for p in d.iterdir()
                  if p.suffix.lower() in _IMG_SUFFIXES and p.stat().st_size > 1024)


def _pexels_urls(term: str, n: int) -> list[str]:
    r = requests.get("https://api.pexels.com/v1/search",
                     params={"query": term, "per_page": n},
                     headers={"Authorization": PEXELS_API_KEY}, timeout=20)
    r.raise_for_status()
    urls = []
    for photo in r.json().get("photos", []):
        src = photo.get("src") or {}
        u = src.get("large2x") or src.get("large") or src.get("original")
        if u:
            urls.append(u)
    return urls[:n]


def _wikipedia_urls(term: str, n: int) -> list[str]:
    r = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "format": "json", "redirects": 1,
                "generator": "search", "gsrsearch": term, "gsrlimit": n * 2,
                "prop": "pageimages", "piprop": "thumbnail",
                "pithumbsize": 1400},
        headers=_UA, timeout=20)
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or {}
    urls = []
    for page in sorted(pages.values(), key=lambda p: p.get("index", 999)):
        u = ((page.get("thumbnail") or {}).get("source") or "")
        suffix = Path(u.split("?", 1)[0]).suffix.lower()
        if u and suffix in _IMG_SUFFIXES:      # skip svg/tif/gif originals
            urls.append(u)
    return urls[:n]


def fetch_stamps(term: str, source: str, n: int | None = None) -> list[str]:
    """Local image paths for the stamp tab: up to `n` pictures matching
    `term` from `source` ("stock" | "wikipedia"), downloaded once and cached
    per (source, term). Returns [] (with a warning) on any total failure —
    never raises for network trouble."""
    term = (term or "").strip()
    n = n or STAMP_FETCH_COUNT
    if not term:
        return []
    if source not in STAMP_SOURCE_TYPES:
        print(f"[stamps] WARNING: unknown stamp_source {source!r} "
              f"(valid: {', '.join(STAMP_SOURCE_TYPES)}) — no stamps")
        return []

    d = _cache_dir(source, term)
    hit = _cached(d)
    if hit:
        print(f"[stamps] '{term[:40]}' ({source}): {len(hit)} cached")
        return hit[:n]

    print(f"[stamps] '{term[:40]}' ({source}): fetching up to {n}…")
    try:
        urls = (_pexels_urls if source == "stock" else _wikipedia_urls)(term, n)
    except Exception as exc:
        print(f"[stamps] WARNING: {source} search failed ({exc}) — no stamps "
              f"(the editor's 'pick a file' still works)")
        return []
    if not urls:
        print(f"[stamps] no {source} results for '{term[:40]}'")
        return []

    d.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for i, url in enumerate(urls):
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        p = d / f"stamp_{i:02d}{suffix if suffix in _IMG_SUFFIXES else '.jpg'}"
        try:
            r = requests.get(url, headers=_UA, timeout=30)
            r.raise_for_status()
            p.write_bytes(r.content)
            paths.append(str(p))
        except Exception as exc:
            print(f"[stamps]   WARNING: download failed ({exc}) — skipping one")
    print(f"[stamps]   ✓ {len(paths)} stamp(s) ready in {d.name}/")
    return paths
