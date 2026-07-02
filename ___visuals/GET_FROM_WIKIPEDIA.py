"""
GET_FROM_WIKIPEDIA
==================

Search Wikipedia for a term, find the best matching page, and download
images from that page. Used as an alternative to Pexels for scenes where
the user wants real reference imagery (historical figures, specific
landmarks, etc.) rather than generic stock footage.

No API key required — Wikipedia's MediaWiki API is open. We just send a
descriptive User-Agent string as their policy requires.

Public entry point
------------------
    get_from_wikipedia(search_term, max_images=5) -> list[dict]

Returns a list of {url: trim_seconds} entries shaped the same as the
existing Pexels image candidates, so it slots directly into the review
pipeline.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"

# Wikipedia requires a descriptive User-Agent. They block generic ones.
# Keep this honest: name + contact / repo / purpose.
WIKIPEDIA_USER_AGENT = (
    "VideoGenerationPipeline/1.0 "
    "(personal research project; contact: local-user@example.com)"
)

# Skip these image types — they're chrome, not content
SKIP_FILENAME_PATTERNS = (
    "icon",     # nav icons
    "logo",     # site logos
    "commons-logo",
    "wiki",     # wikipedia branding
    "edit-",
    "ambox",    # message boxes
    "ombox",
    "stub",
    "symbol",
    "flag_of",  # often tiny generic flags
)

# Reject obviously useless tiny or vector-only files
SKIP_EXTENSIONS = (".svg",)   # SVG is mostly icons/diagrams here
MIN_IMAGE_BYTES = 8 * 1024    # 8KB — anything smaller is almost certainly an icon


# ---------------------------------------------------------------------------
# HTTP session (shared, with pooling)
# ---------------------------------------------------------------------------

_session = requests.Session()
_session.headers.update({"User-Agent": WIKIPEDIA_USER_AGENT})
_adapter = requests.adapters.HTTPAdapter(
    pool_connections=8, pool_maxsize=8, max_retries=2,
)
_session.mount("https://", _adapter)
_session.mount("http://",  _adapter)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _search_page_titles(search_term: str, max_results: int = 5) -> list[str]:
    """
    Find Wikipedia page titles matching `search_term`, ranked by relevance.

    e.g. "alaric the goth" → ["Alaric I", "Alaric II", "Alaric Balth", ...]
    """
    print(f"[wiki:search] querying titles for '{search_term}'")
    try:
        resp = _session.get(
            WIKIPEDIA_API_URL,
            params={
                "action":   "query",
                "list":     "search",
                "srsearch": search_term,
                "srlimit":  max_results,
                "format":   "json",
            },
            timeout=10,
        )
    except Exception as exc:
        print(f"[wiki:search] HTTP error: {exc}")
        return []

    if resp.status_code != 200:
        print(f"[wiki:search] API status {resp.status_code} for '{search_term}'")
        return []

    hits = (resp.json().get("query") or {}).get("search") or []
    titles = [h["title"] for h in hits if "title" in h]
    print(f"[wiki:search] '{search_term}' → {len(titles)} title(s): {titles}")
    return titles


def _get_page_image_filenames(page_title: str, max_images: int = 30) -> list[str]:
    """
    Return the list of image filenames embedded in a Wikipedia page,
    in the order they appear (which roughly matches relevance — the
    infobox image is usually first).

    e.g. ["Alaric_in_Athens.jpg", "Sack_of_Rome_410.png", ...]
    """
    print(f"[wiki:images] listing images for page '{page_title}'")
    try:
        resp = _session.get(
            WIKIPEDIA_API_URL,
            params={
                "action":   "query",
                "titles":   page_title,
                "prop":     "images",
                "imlimit":  max_images,
                "format":   "json",
            },
            timeout=10,
        )
    except Exception as exc:
        print(f"[wiki:images] HTTP error: {exc}")
        return []

    if resp.status_code != 200:
        print(f"[wiki:images] API status {resp.status_code} for '{page_title}'")
        return []

    pages = (resp.json().get("query") or {}).get("pages") or {}
    images: list[str] = []
    for page_data in pages.values():
        for img in page_data.get("images", []) or []:
            title = img.get("title")  # e.g. "File:Alaric_in_Athens.jpg"
            if title and title.startswith("File:"):
                images.append(title[len("File:"):])

    print(f"[wiki:images] '{page_title}' → {len(images)} raw image(s)")
    return images


def _get_image_urls(filenames: list[str]) -> list[tuple[str, int]]:
    """
    Resolve filenames to direct image URLs and sizes.

    Returns [(url, size_bytes), ...] in the same order as input.

    e.g. [("https://upload.wikimedia.org/.../Alaric.jpg", 245678), ...]
    """
    if not filenames:
        return []

    # Wikipedia caps title batches at 50
    out: list[tuple[str, int]] = []
    BATCH = 50
    for i in range(0, len(filenames), BATCH):
        batch = filenames[i:i + BATCH]
        titles_param = "|".join(f"File:{name}" for name in batch)

        print(f"[wiki:urls] resolving URLs for batch of {len(batch)} file(s)")
        try:
            resp = _session.get(
                WIKIPEDIA_API_URL,
                params={
                    "action":  "query",
                    "titles":  titles_param,
                    "prop":    "imageinfo",
                    "iiprop":  "url|size|mime",
                    "format":  "json",
                },
                timeout=10,
            )
        except Exception as exc:
            print(f"[wiki:urls] HTTP error: {exc}")
            continue

        if resp.status_code != 200:
            print(f"[wiki:urls] API status {resp.status_code}")
            continue

        pages = (resp.json().get("query") or {}).get("pages") or {}

        # Re-order results to match input order (Wikipedia normalises titles
        # and returns them as a dict, not a list)
        url_by_name: dict[str, tuple[str, int]] = {}
        for page_data in pages.values():
            title = page_data.get("title", "")
            if not title.startswith("File:"):
                continue
            filename = title[len("File:"):]
            info_list = page_data.get("imageinfo") or []
            if not info_list:
                continue
            info = info_list[0]
            url = info.get("url")
            size = int(info.get("size") or 0)
            if url:
                url_by_name[filename] = (url, size)

        for name in batch:
            # Try exact match first, then with underscores/spaces toggled
            entry = (url_by_name.get(name)
                     or url_by_name.get(name.replace(" ", "_"))
                     or url_by_name.get(name.replace("_", " ")))
            if entry:
                out.append(entry)

    print(f"[wiki:urls] resolved {len(out)}/{len(filenames)} URL(s)")
    return out


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _is_useful_image(filename: str, size_bytes: int) -> tuple[bool, str]:
    """
    Decide whether an image is worth showing to the user.

    Returns (keep, reason). `reason` is for debug printing.
    """
    lower = filename.lower()

    for pattern in SKIP_FILENAME_PATTERNS:
        if pattern in lower:
            return False, f"matches skip pattern '{pattern}'"

    for ext in SKIP_EXTENSIONS:
        if lower.endswith(ext):
            return False, f"extension {ext} (icon/diagram)"

    if size_bytes > 0 and size_bytes < MIN_IMAGE_BYTES:
        return False, f"too small ({size_bytes} bytes)"

    return True, "ok"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_from_wikipedia(
    search_term: str,
    max_images: int = 5,
) -> list[str]:
    """
    Get up to `max_images` image URLs from Wikipedia for `search_term`.

    Strategy:
      1. Search Wikipedia for matching page titles (best-match first).
      2. Walk pages in order; collect images from each.
      3. Filter out icons, logos, tiny files, SVGs.
      4. Stop once we have `max_images` URLs (still preserves page-by-page
         priority — page 1 images come before page 2 images).

    Returns a list of direct image URLs (no downloading).

    e.g. get_from_wikipedia("alaric the goth", 5) →
        ["https://upload.wikimedia.org/.../Alaric_in_Athens.jpg",
         "https://upload.wikimedia.org/.../Sack_of_Rome.png", ...]
    """
    print(f"\n[wiki] >>>>>> get_from_wikipedia('{search_term}', max={max_images})")

    page_titles = _search_page_titles(search_term, max_results=5)
    if not page_titles:
        print(f"[wiki] no matching pages for '{search_term}' — returning empty")
        return []

    collected_urls: list[str] = []
    seen_urls: set[str] = set()

    for page_idx, page_title in enumerate(page_titles):
        if len(collected_urls) >= max_images:
            break

        print(f"\n[wiki] page {page_idx + 1}/{len(page_titles)}: '{page_title}'")
        filenames = _get_page_image_filenames(page_title, max_images=30)
        if not filenames:
            print(f"[wiki]   (no images on this page)")
            continue

        url_pairs = _get_image_urls(filenames)
        kept_for_this_page = 0
        for filename, (url, size) in zip(filenames, url_pairs):
            if len(collected_urls) >= max_images:
                break
            if url in seen_urls:
                continue
            keep, reason = _is_useful_image(filename, size)
            if not keep:
                print(f"[wiki]   ✗ '{filename}' — {reason}")
                continue
            seen_urls.add(url)
            collected_urls.append(url)
            kept_for_this_page += 1
            print(f"[wiki]   ✓ '{filename}' ({size} bytes) → kept")

        print(f"[wiki]   page yielded {kept_for_this_page} usable image(s)  "
              f"(total now {len(collected_urls)}/{max_images})")

    print(f"\n[wiki] <<<<<< done — returning {len(collected_urls)} URL(s) "
          f"for '{search_term}'")
    return collected_urls


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    term = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "alaric the goth"
    urls = get_from_wikipedia(term, max_images=5)
    print("\nFinal URLs:")
    for u in urls:
        print(" ", u)
