"""
External candidate fetching: Pexels video/image metadata, the download
helpers (serial + thread-safe parallel + Wikimedia-throttled), and the
top-level load_stock_footage() that gathers + downloads all candidates.

The five public download functions used to repeat the same md5 / extension /
stream-to-file / cache-check / history-record logic. That shared core now
lives in the private helpers below; each public function is a thin wrapper
that keeps its original logging + concurrency (locking / retry) semantics.
"""

from __future__ import annotations

# Allow running this file directly from the repo root (uv run ___visuals/DOWNLOADS.py).
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import hashlib
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from ___visuals.CACHE_IO import _load_history, _save_history
from CONFIG import (
    DOWNLOAD_WORKERS,
    MAX_CLIP_SECONDS,
    PEXELS_API_KEY,
    SYNCHRONIZED_SCRIPT_OUTPUT_FILE,
    STOCK_FOOTAGE_CACHE_DIR,
    WIKI_DOWNLOAD_BASE_BACKOFF_SEC,
    WIKI_DOWNLOAD_MAX_RETRIES,
    ProgressTracker,
    media_props,
    _history_lock,
    _http_session,
    _wiki_download_semaphore,
    _wiki_session,
    COLLAGE_NUM_PICKS,
    scene_wants_collage,
)

from ___visuals.GET_FROM_WIKIPEDIA import get_from_wikipedia

import json


# ===========================================================================
# Scene-count helper
# ===========================================================================


def _get_num_stock_images(input_script: str) -> tuple[int, float]:
    """Decide how many stock images a scene needs and how long it should run."""
    timings_path = Path(SYNCHRONIZED_SCRIPT_OUTPUT_FILE)
    timings: dict = json.loads(timings_path.read_text())

    if input_script not in timings:
        available = "\n".join(f"  - {k}" for k in timings.keys())
        raise KeyError(
            f"[_get_num_stock_images] Could not find timing for:\n"
            f"  '{input_script}'\n\n"
            f"Available keys:\n{available}"
        )

    runtime_per_scene_seconds: float = float(timings[input_script])

    num_images = max(1, math.ceil(runtime_per_scene_seconds / MAX_CLIP_SECONDS))
    max_runtime_per_clip_seconds = runtime_per_scene_seconds / num_images

    return num_images, max_runtime_per_clip_seconds


# ---------------------------------------------------------------------------
# Pexels metadata helpers
# ---------------------------------------------------------------------------


def _get_video_metadata(
    search_term: str, max_results: int = 10, page: int = 1
) -> list[tuple[str, float]]:
    """Hit Pexels Videos API, return (url, duration) pairs — NO downloading yet."""
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={
            "query": search_term,
            "per_page": max_results,
            "orientation": "landscape",
            "page": page,
        },
        timeout=8,
    )
    if resp.status_code != 200:
        print(f"  [video meta] API error {resp.status_code} for '{search_term}'")
        return []

    results = []
    for video in resp.json().get("videos", []):
        files = sorted(
            video.get("video_files", []), key=lambda f: f.get("width", 0), reverse=True
        )
        if files:
            results.append((files[0]["link"], float(video.get("duration", 0))))

    print(f"  [video meta] '{search_term}' p{page} → {len(results)} results")
    return results


def _get_image_metadata(
    search_term: str, max_results: int = 5, page: int = 1
) -> list[str]:
    """Hit Pexels Images API, return URLs only — NO downloading."""
    try:
        resp = _http_session.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={
                "query": search_term,
                "per_page": max_results,
                "orientation": "landscape",
                "page": page,
            },
            timeout=8,
        )
    except Exception as exc:
        print(f"  [image meta] API error: {exc}")
        return []

    if resp.status_code != 200:
        print(f"  [image meta] API error {resp.status_code} for '{search_term}'")
        return []

    urls: list[str] = []
    for p in resp.json().get("photos", []) or []:
        url = (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large")
        if url:
            urls.append(url)
    return urls


# ---------------------------------------------------------------------------
# Shared download core
# ---------------------------------------------------------------------------


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _image_ext_for(url: str, *, use_endswith: bool = False) -> str:
    """
    Pick an image extension from a URL. Pexels image URLs carry the extension
    mid-path (so we look for `ext in url`), whereas Wikimedia URLs end with it
    (and may be `.gif`). Defaults to `.jpg`.
    """
    if use_endswith:
        lower = url.lower()
        for cand_ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            if lower.endswith(cand_ext):
                return cand_ext
        return ".jpg"

    cleaned = url.lower().split("?")[0]
    for cand_ext in (".jpg", ".jpeg", ".png", ".webp"):
        if cand_ext in cleaned:
            return cand_ext
    return ".jpg"


def _cached_local_path(url: str, *, lock: bool) -> str | None:
    """Return the cached local path for `url` if present + on disk, else None."""

    def _check() -> str | None:
        history = _load_history()
        if url in history and Path(history[url]).exists():
            return history[url]
        return None

    if lock:
        with _history_lock:
            return _check()
    return _check()


def _record_download(url: str, dest: Path, *, lock: bool) -> None:
    """Persist a url→local-path mapping to history.json."""

    def _write() -> None:
        history = _load_history()
        history[url] = str(dest)
        _save_history(history)

    if lock:
        with _history_lock:
            _write()
    else:
        _write()


def _stream_to_file(resp, dest: Path) -> None:
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)


# ---------------------------------------------------------------------------
# Public download functions
# ---------------------------------------------------------------------------


def _download_clip(url: str) -> str | None:
    """Download a single video to cache. Returns local path, or None on failure."""
    hit = _cached_local_path(url, lock=False)
    if hit:
        print(f"  [cache hit] {Path(hit).name}")
        return hit

    filename = f"pexels-{_url_hash(url)}.mp4"

    print(f"  [download] {filename} ...")
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STOCK_FOOTAGE_CACHE_DIR / filename

    vid_resp = requests.get(url, stream=True, timeout=30)
    if vid_resp.status_code != 200:
        print(f"  [download] FAILED {vid_resp.status_code}")
        return None

    _stream_to_file(vid_resp, dest)
    _record_download(url, dest, lock=False)
    print(f"  [download] done → {dest.name}")
    return str(dest)


def _download_image(url: str) -> str | None:
    """Download a single image to cache. Returns local path, or None on failure."""
    hit = _cached_local_path(url, lock=False)
    if hit:
        print(f"  [cache hit img] {Path(hit).name}")
        return hit

    filename = f"pexels-img-{_url_hash(url)}{_image_ext_for(url)}"

    print(f"  [download img] {filename} ...")
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STOCK_FOOTAGE_CACHE_DIR / filename

    try:
        img_resp = requests.get(url, stream=True, timeout=15)
    except Exception as exc:
        print(f"  [download img] FAILED {exc}")
        return None
    if img_resp.status_code != 200:
        print(f"  [download img] FAILED {img_resp.status_code}")
        return None

    _stream_to_file(img_resp, dest)
    _record_download(url, dest, lock=False)
    print(f"  [download img] done → {dest.name}")
    return str(dest)


def _download_clip_parallel(url: str) -> str | None:
    """Thread-safe, silent version of _download_clip using the shared session."""
    hit = _cached_local_path(url, lock=True)
    if hit:
        return hit

    filename = f"pexels-{_url_hash(url)}.mp4"
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STOCK_FOOTAGE_CACHE_DIR / filename

    try:
        vid_resp = _http_session.get(url, stream=True, timeout=30)
        if vid_resp.status_code != 200:
            return None
        _stream_to_file(vid_resp, dest)
    except Exception:
        return None

    _record_download(url, dest, lock=True)
    return str(dest)


def _download_image_parallel(url: str) -> str | None:
    """Thread-safe, silent version of _download_image using the shared session."""
    hit = _cached_local_path(url, lock=True)
    if hit:
        return hit

    filename = f"pexels-img-{_url_hash(url)}{_image_ext_for(url)}"
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STOCK_FOOTAGE_CACHE_DIR / filename

    try:
        img_resp = _http_session.get(url, stream=True, timeout=15)
        if img_resp.status_code != 200:
            return None
        _stream_to_file(img_resp, dest)
    except Exception:
        return None

    _record_download(url, dest, lock=True)
    return str(dest)


def _download_wikipedia_image_parallel(url: str) -> str | None:
    """
    Thread-safe Wikipedia image downloader.

    Wikimedia 429s under parallel load, so this:
      - limits concurrent Wikimedia connections via a dedicated semaphore
        (independent of the 12-worker Pexels pool),
      - retries 429/503 with exponential backoff, honouring Retry-After,
      - sends Wikimedia's required descriptive User-Agent.
    """
    hit = _cached_local_path(url, lock=True)
    if hit:
        return hit

    filename = f"wiki-img-{_url_hash(url)}{_image_ext_for(url, use_endswith=True)}"
    STOCK_FOOTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STOCK_FOOTAGE_CACHE_DIR / filename

    with _wiki_download_semaphore:  # throttle Wikimedia concurrency
        for attempt in range(1, WIKI_DOWNLOAD_MAX_RETRIES + 1):
            resp = None
            try:
                resp = _wiki_session.get(url, stream=True, timeout=30)
            except Exception as exc:
                print(f"  [wiki download] conn error (attempt {attempt}): {exc}")

            # Success.
            if resp is not None and resp.status_code == 200:
                try:
                    _stream_to_file(resp, dest)
                except Exception as exc:
                    print(f"  [wiki download] write failed: {exc}")
                    return None
                break

            status = resp.status_code if resp is not None else "conn-error"

            # Non-retryable HTTP error → give up immediately.
            if resp is not None and resp.status_code not in (429, 503):
                print(f"  [wiki download] FAILED {status} for {url}")
                return None

            # Out of attempts.
            if attempt == WIKI_DOWNLOAD_MAX_RETRIES:
                print(
                    f"  [wiki download] FAILED {status} after "
                    f"{WIKI_DOWNLOAD_MAX_RETRIES} attempts for {url}"
                )
                return None

            # Honour Retry-After if present, else exponential backoff + jitter.
            wait = WIKI_DOWNLOAD_BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            if resp is not None:
                ra = resp.headers.get("Retry-After")
                if ra:
                    try:
                        wait = max(wait, float(ra))
                    except ValueError:
                        pass
            wait += random.uniform(0, 0.5)
            print(
                f"  [wiki download] {status} — retry "
                f"{attempt}/{WIKI_DOWNLOAD_MAX_RETRIES} in {wait:.1f}s"
            )
            time.sleep(wait)

    _record_download(url, dest, lock=True)
    return str(dest)


# ===========================================================================
# EXTERNAL CANDIDATE FETCHING (Pexels + Wikipedia)
# ===========================================================================


def refetch_candidates(
    search_term: str,
    *,
    max_runtime: float,
    n_videos: int = 0,
    n_stock_images: int = 0,
    n_wiki_images: int = 0,
    exclude_urls=(),
) -> tuple[dict, dict]:
    """
    Fetch ONE scene's worth of fresh candidates for `search_term`, on demand.
    Used by the review GUI's "search again" options — the user rejected every
    option, so they type a new term and choose how wide to cast the net.

    `exclude_urls` are never returned: an already-offered (and rejected) URL
    coming back would just waste one of the five slots. Pages are walked until
    enough NEW urls are found, or the source runs dry.

    Returns (candidates, url_to_local) where candidates is the same shape the
    review GUI already renders — {"videos": [{url: trim}], "images": [...]} —
    and url_to_local feeds the GUI's history map so the picks resolve to disk.
    """
    skip = set(exclude_urls or ())
    trim = round(float(max_runtime), 2)

    video_meta: list[tuple[str, float]] = []
    if n_videos > 0:
        for page in range(1, 5):
            if len(video_meta) >= n_videos:
                break
            for url, dur in _get_video_metadata(search_term, max_results=10, page=page):
                if url in skip or dur <= 0:
                    continue
                skip.add(url)
                video_meta.append((url, dur))
                if len(video_meta) >= n_videos:
                    break

    stock_images: list[str] = []
    if n_stock_images > 0:
        for page in range(1, 5):
            if len(stock_images) >= n_stock_images:
                break
            for url in _get_image_metadata(
                search_term, max_results=max(5, n_stock_images), page=page
            ):
                if url in skip:
                    continue
                skip.add(url)
                stock_images.append(url)
                if len(stock_images) >= n_stock_images:
                    break

    wiki_images: list[str] = []
    if n_wiki_images > 0:
        # Wikipedia has no paging here — ask for extra and drop the seen ones.
        for url in get_from_wikipedia(search_term, max_images=n_wiki_images + len(skip)):
            if url in skip:
                continue
            skip.add(url)
            wiki_images.append(url)
            if len(wiki_images) >= n_wiki_images:
                break

    print(
        f"[refetch] '{search_term}' → {len(video_meta)} video(s), "
        f"{len(stock_images)} stock still(s), {len(wiki_images)} wikipedia still(s) "
        f"({len(exclude_urls or ())} already-seen url(s) skipped)"
    )

    tasks = (
        [("videos", u, min(d, max_runtime)) for u, d in video_meta]
        + [("images", u, max_runtime) for u in stock_images]
        + [("wiki_images", u, max_runtime) for u in wiki_images]
    )
    if not tasks:
        return {"videos": [], "images": []}, {}

    def download_one(task):
        kind, url, t = task
        if kind == "videos":
            local = _download_clip_parallel(url)
        elif kind == "wiki_images":
            local = _download_wikipedia_image_parallel(url)
        else:
            local = _download_image_parallel(url)
        return kind, url, t, local

    candidates: dict = {"videos": [], "images": []}
    url_to_local: dict = {}
    # Preserve the requested order (stock stills before wikipedia stills) —
    # ThreadPoolExecutor.map returns results in submission order.
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        for kind, url, t, local in ex.map(download_one, tasks):
            if local is None:
                print(f"[refetch]   download failed, dropping: {url[:70]}")
                continue
            bucket = "videos" if kind == "videos" else "images"
            candidates[bucket].append({url: round(float(t), 2)})
            url_to_local[url] = local

    return candidates, url_to_local


def load_stock_footage(all_scenes: dict) -> list[dict]:
    """
    Two phases:
      A) gather metadata in parallel across scenes
      B) download every candidate file in parallel with one progress bar

    Only fetches candidates for scenes whose media_type has
    needs_external_candidates. Within that set, uses_wikipedia types pull
    from Wikipedia; everything else goes via Pexels. Collage rows get
    COLLAGE_NUM_PICKS review slots (and extra image candidates) so the
    review GUI collects several picks for the one line.

    Returns:
        [{"script_text", "candidates": {"videos": [...], "images": [...]},
          "num_clips_needed", "max_runtime_per_clip_seconds"}, ...]
    """
    eligible: dict = {}
    skipped_by_type: dict[str, int] = {}
    for k, v in all_scenes.items():
        st = v.get("media_type")
        if media_props(st).needs_external_candidates:
            eligible[k] = v
        else:
            type_name = st.value if hasattr(st, "value") else str(st)
            skipped_by_type[type_name] = skipped_by_type.get(type_name, 0) + 1

    scene_items = list(eligible.items())
    print(
        f"\n[fetch] Phase A: gathering metadata for {len(scene_items)} scene(s) "
        f"in parallel..."
    )
    if skipped_by_type:
        skipped_summary = ", ".join(f"{n} {t}" for t, n in skipped_by_type.items())
        print(f"[fetch]   (skipped {skipped_summary} — produced by local generators)")

    def fetch_meta_for_scene(idx_and_scene):
        idx, (script_text, scene_data) = idx_and_scene
        search_term = scene_data["search_term"]
        media_type = scene_data["media_type"]
        props = media_props(media_type)
        num_clips, max_runtime = _get_num_stock_images(script_text)
        is_collage = scene_wants_collage(scene_data)

        if props.is_on_board:
            max_runtime = num_clips * max_runtime  # == full scene runtime
            num_clips = 1
        elif is_collage:
            # collage: SEVERAL still picks for this ONE line; the composed
            # collage spans the whole scene, so each pick gets full runtime.
            max_runtime = num_clips * max_runtime
            num_clips = COLLAGE_NUM_PICKS

        print(f"\n[fetch:meta] scene[{idx}] '{script_text[:50]}...'")
        print(f"[fetch:meta]   search='{search_term}', type={media_type.value}"
              + (" +collage" if is_collage else ""))

        # ── WIKIPEDIA path ───────────────────────────────────────────
        if props.uses_wikipedia:
            print(f"[fetch:meta]   → using WIKIPEDIA source")
            wiki_urls = get_from_wikipedia(search_term, max_images=5)
            print(f"[fetch:meta]   wikipedia returned {len(wiki_urls)} URL(s)")
            if wiki_urls:
                return (idx, script_text, num_clips, max_runtime, [], [], wiki_urls)
            # A term that isn't an article name ('1600th cen') yields nothing,
            # and a scene with nothing in it soft-locks the review GUI. Rather
            # than hand the user an empty screen, fall back to Pexels stills
            # for the same term — they can still swap the term in the review.
            print(
                f"[fetch:meta]   wikipedia had NOTHING for '{search_term}' — "
                f"falling back to PEXELS stills so the scene is reviewable"
            )
            fallback = []
            seen_fb: set[str] = set()
            for url in _get_image_metadata(search_term, max_results=5, page=1):
                if url in seen_fb:
                    continue
                seen_fb.add(url)
                fallback.append(url)
                if len(fallback) >= 5:
                    break
            print(f"[fetch:meta]   pexels fallback returned {len(fallback)} still(s)")
            return (idx, script_text, num_clips, max_runtime, [], fallback, [])

        # ── PEXELS path ──────────────────────────────────────────────
        print(f"[fetch:meta]   → using PEXELS source")

        video_meta: list[tuple[str, float]] = []
        # image_only types use a STILL only — never fetch stock video for
        # them. Collages compose stills too, so skip videos there as well.
        fetch_videos = not props.image_only and not is_collage
        seen: set[str] = set()
        if fetch_videos:
            for page in range(1, 4):
                if len(video_meta) >= 2:
                    break
                for url, dur in _get_video_metadata(
                    search_term, max_results=10, page=page
                ):
                    if url in seen or dur <= 0:
                        continue
                    seen.add(url)
                    video_meta.append((url, dur))
                    if len(video_meta) >= 2:
                        break

        # collage rows need more raw material to pick from
        want_images = max(3, num_clips + 3) if is_collage else 3
        image_urls: list[str] = []
        seen_img: set[str] = set()
        for url in _get_image_metadata(
            search_term, max_results=max(5, want_images), page=1
        ):
            if url in seen_img:
                continue
            seen_img.add(url)
            image_urls.append(url)
            if len(image_urls) >= want_images:
                break

        return (idx, script_text, num_clips, max_runtime, video_meta, image_urls, [])

    out: list[dict] = [None] * len(scene_items)  # type: ignore[list-item]
    all_tasks: list[tuple] = []
    # task = (scene_idx, kind, url, trim_seconds)
    # kind is one of: "videos", "images", "wiki_images"

    if not scene_items:
        print(f"[fetch] no eligible scenes — returning empty candidates list")
        return []

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        for result in ex.map(fetch_meta_for_scene, enumerate(scene_items)):
            (
                idx,
                script_text,
                num_clips,
                max_runtime,
                video_meta,
                pexels_img_urls,
                wiki_img_urls,
            ) = result

            # Stamp the bundle with WHAT it was fetched for. The candidate
            # cache is keyed by script_text alone and reused wholesale, so
            # without this a line retagged in the tagger (wikipedia → stock,
            # or just a new search term) would silently keep serving the old
            # bundle forever. main() compares these against the live row and
            # re-fetches the ones that no longer match.
            _row = scene_items[idx][1]
            _mt = _row.get("media_type")
            out[idx] = {
                "script_text": script_text,
                "media_type": getattr(_mt, "value", str(_mt)),
                "search_term": _row.get("search_term", ""),
                "candidates": {"videos": [], "images": []},
                "num_clips_needed": num_clips,
                "max_runtime_per_clip_seconds": max_runtime,
            }

            for url, dur in video_meta:
                trim = min(dur, max_runtime)
                all_tasks.append((idx, "videos", url, round(trim, 2)))

            for url in pexels_img_urls:
                all_tasks.append((idx, "images", url, round(float(max_runtime), 2)))

            for url in wiki_img_urls:
                all_tasks.append(
                    (idx, "wiki_images", url, round(float(max_runtime), 2))
                )

    print(f"[fetch] Phase A done — {len(all_tasks)} files queued.")

    if not all_tasks:
        return out

    # ── Phase B: parallel download with progress bar ──────────────────
    print(
        f"[fetch] Phase B: downloading {len(all_tasks)} files "
        f"with {DOWNLOAD_WORKERS} workers..."
    )
    tracker = ProgressTracker(total=len(all_tasks), label="DOWNLOADING")

    def download_one(task):
        scene_idx, kind, url, trim = task
        if kind == "videos":
            local = _download_clip_parallel(url)
        elif kind == "wiki_images":
            local = _download_wikipedia_image_parallel(url)
        else:
            local = _download_image_parallel(url)
        tracker.tick()
        return scene_idx, kind, url, trim, local

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        for scene_idx, kind, url, trim, local in ex.map(download_one, all_tasks):
            if local is None:
                continue
            bucket = "videos" if kind == "videos" else "images"
            out[scene_idx]["candidates"][bucket].append({url: trim})

    tracker.finish()
    print(f"[fetch] Phase B done.")
    return out
