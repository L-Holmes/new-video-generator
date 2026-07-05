"""
GET_MAP
=======

Turn a place name into a clean, highlighted map image — the visual you'd
show when the narration mentions a country, region or city.

It is the map-equivalent of GET_FROM_WIKIPEDIA.py: given a `search_term`
(e.g. "Indonesia", "Bavaria", "Kyoto") it renders a single PNG and returns
its path, so the result slots straight into the video pipeline.

How it decides what to draw
---------------------------
1. Geocode the term with OpenStreetMap's free Nominatim API. That tells us
   the lat/lon, the bounding box, a boundary polygon (when one exists) and —
   crucially — what KIND of place it is (country / state / city / …).
2. Pick a view + emphasis based on that kind:
     - COUNTRY  → the whole world, with the country filled in an accent colour.
     - REGION   → the parent country (with neighbours for context), with the
                  region/state polygon filled in the accent colour.
     - PLACE    → the parent country, with a map pin dropped on the town/city.
3. Render with Pillow over cached Natural Earth country outlines.

No GIS libraries required — just `requests` + `Pillow` (both already project
deps). Country outlines are downloaded once (Natural Earth GeoJSON) and cached
on disk; geocoding results are cached too, so re-runs are offline + instant.

Public entry point
------------------
    get_map_image(search_term, output_path, *, width=1920, height=1080,
                  cache_dir=None, data_dir=None) -> str | None

Returns the path to the written PNG (== output_path) on success, or None only
if not even a fallback frame could be produced.
"""

from __future__ import annotations

# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import difflib
import hashlib
import json
import math
import sys
import threading
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Config — endpoints, polite headers, cache locations
# ---------------------------------------------------------------------------

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy REQUIRES a descriptive User-Agent and a max of
# ~1 request/second. We honour both (see _geocode + _RATE_LIMIT_SECONDS).
MAP_USER_AGENT = (
    "VideoGenerationPipeline/1.0 "
    "(map module; personal research project; contact: logosa1960@gmail.com)"
)
_RATE_LIMIT_SECONDS = 1.1

# Bump this to invalidate previously-cached geocodes when the matching logic
# changes (so e.g. an old "Indonesa" -> wrong-POI cache is refreshed).
_GEOCODE_CACHE_VERSION = 2

# Where the world-countries GeoJSON lives once downloaded. Kept next to this
# module (not under a per-project CACHE dir) so it's fetched ONCE and shared
# across every --name project.
_MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = _MODULE_DIR / "_MAP_DATA"

# Natural Earth admin-0 (country) borders, in preference order. 50m is the
# sweet spot for both world + country-level zoom; the others are fallbacks if
# that URL is unreachable. All three are normalised to the same internal shape.
WORLD_GEOJSON_SOURCES = [
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_50m_admin_0_countries.geojson",
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_110m_admin_0_countries.geojson",
    "https://raw.githubusercontent.com/johan/world.geo.json/master/countries.geo.json",
]


# ---------------------------------------------------------------------------
# Style — tweak these to restyle every map at once
# ---------------------------------------------------------------------------

COLOR_OCEAN = (171, 205, 233)  # background / sea
COLOR_LAND = (236, 232, 222)  # ordinary land
COLOR_LAND_BORDER = (190, 184, 172)  # country outlines
COLOR_PARENT_TINT = (250, 226, 206)  # the country a region/place sits in
COLOR_HIGHLIGHT = (227, 96, 52)  # the highlighted country / region
COLOR_HIGHLIGHT_LN = (150, 48, 22)  # its outline
COLOR_PIN = (227, 96, 52)  # map-pin body
COLOR_PIN_EDGE = (255, 255, 255)  # map-pin ring
COLOR_PIN_DOT = (150, 48, 22)  # map-pin centre dot
COLOR_LABEL = (38, 30, 26)  # place-name text
COLOR_LABEL_HALO = (255, 255, 255)  # text outline for legibility

# Supersample factor: render this many times larger, then downscale with
# LANCZOS so polygon edges + text come out smooth (PIL has no native AA for
# polygons). 2 is a good quality/speed balance.
SUPERSAMPLE = 2

# Drop the Google-Maps-style pin on AREA features (countries / regions) too,
# not just on point-places. Either way the NAME label always sits ABOVE the
# highlighted feature so it never covers it. Set False for pins on cities only.
MAP_PIN_ON_AREAS: bool = True

# Candidate bold fonts for the place-name label (first that loads wins).
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "DejaVuSans-Bold.ttf",  # Pillow can sometimes resolve by name
]


# Classification: which Nominatim "addresstype"/"type" values mean what.
_PLACE_TYPES = {
    "city",
    "town",
    "village",
    "hamlet",
    "suburb",
    "neighbourhood",
    "locality",
    "municipality",
    "isolated_dwelling",
    "quarter",
    "borough",
    "city_district",
    "croft",
    "allotments",
}
_REGION_TYPES = {
    "state",
    "province",
    "region",
    "county",
    "district",
    "department",
    "governorate",
    "canton",
    "prefecture",
    "oblast",
    "krai",
    "territory",
    "division",
    "autonomous_region",
    "administrative",
    "state_district",
    "constituency",
    "municipality_level",
}

# A few common-name mismatches between Nominatim's English country names and
# Natural Earth's ADMIN field. Keys + values are _norm_name()'d.
_COUNTRY_NAME_ALIASES = {
    "united states": "united states of america",
    "usa": "united states of america",
    "russia": "russia",
    "russian federation": "russia",
    "south korea": "south korea",
    "north korea": "north korea",
    "czech republic": "czechia",
    "myanmar burma": "myanmar",
    "burma": "myanmar",
    "the netherlands": "netherlands",
    "uk": "united kingdom",
    "great britain": "united kingdom",
    "tanzania": "united republic of tanzania",
    "republic of the congo": "republic of congo",
    "dr congo": "democratic republic of the congo",
    "ivory coast": "côte d'ivoire",
}


# ---------------------------------------------------------------------------
# Shared HTTP session + Nominatim rate limiter
# ---------------------------------------------------------------------------

_session = requests.Session()
_session.headers.update({"User-Agent": MAP_USER_AGENT})
_session.mount("https://", requests.adapters.HTTPAdapter(max_retries=2))

_rate_lock = threading.Lock()
_last_request_at = [0.0]  # mutable single-cell so the lock can update it

# Parsed world features cached in-process so multiple scenes in one run only
# read + parse the GeoJSON once.
_world_features_cache: list[dict] | None = None


def _polite_wait() -> None:
    """Block until at least _RATE_LIMIT_SECONDS have passed since the last hit."""
    with _rate_lock:
        elapsed = time.time() - _last_request_at[0]
        if elapsed < _RATE_LIMIT_SECONDS:
            time.sleep(_RATE_LIMIT_SECONDS - elapsed)
        _last_request_at[0] = time.time()


# ---------------------------------------------------------------------------
# World country outlines (download + cache + normalise)
# ---------------------------------------------------------------------------


def _prop_ci(props: dict, *keys: str) -> str:
    """Case-insensitive property lookup across a few candidate keys."""
    lower = {str(k).lower(): v for k, v in (props or {}).items()}
    for k in keys:
        v = lower.get(k.lower())
        if v not in (None, "", "-99"):
            return str(v)
    return ""


def _normalise_world_geojson(raw: dict) -> list[dict]:
    """
    Flatten a countries GeoJSON FeatureCollection into:
        [{"name", "iso_a2", "iso_a3", "geometry", "bbox"}, ...]

    Works for both Natural Earth (UPPERCASE props) and johan/world.geo.json
    (lowercase `name` + ISO-A3 in the feature `id`).
    """
    out: list[dict] = []
    for feat in raw.get("features", []) or []:
        geom = feat.get("geometry")
        if not geom:
            continue
        props = feat.get("properties", {}) or {}
        name = _prop_ci(props, "ADMIN", "NAME_LONG", "NAME", "name", "SOVEREIGNT")
        iso_a2 = _prop_ci(props, "ISO_A2", "ISO_A2_EH", "WB_A2", "iso_a2")
        iso_a3 = _prop_ci(props, "ISO_A3", "ISO_A3_EH", "ADM0_A3", "iso_a3")
        if not iso_a3:
            fid = feat.get("id")
            if isinstance(fid, str) and len(fid) == 3:
                iso_a3 = fid
        out.append(
            {
                "name": name,
                "iso_a2": iso_a2.upper(),
                "iso_a3": iso_a3.upper(),
                "geometry": geom,
                "bbox": _geom_bbox(geom),
            }
        )
    return out


def _load_world_features(data_dir: Path | str | None = None) -> list[dict]:
    """
    Return the normalised list of country features, downloading + caching the
    GeoJSON on first use. Returns [] (with a loud warning) if every source
    fails — callers degrade gracefully to a sparse / pin-only map.
    """
    global _world_features_cache
    if _world_features_cache is not None:
        return _world_features_cache

    base = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    base.mkdir(parents=True, exist_ok=True)
    cache_file = base / "world_countries.geojson"

    raw: dict | None = None
    if cache_file.exists() and cache_file.stat().st_size > 1024:
        try:
            raw = json.loads(cache_file.read_text())
            print(f"[map:geo] loaded cached country outlines ({cache_file})")
        except Exception as exc:
            print(f"[map:geo] cached outlines unreadable ({exc}) — re-downloading")
            raw = None

    if raw is None:
        for url in WORLD_GEOJSON_SOURCES:
            try:
                print(f"[map:geo] downloading country outlines:\n           {url}")
                resp = _session.get(url, timeout=45)
                if resp.status_code != 200 or len(resp.content) < 1024:
                    print(
                        f"[map:geo]   ✗ HTTP {resp.status_code} "
                        f"({len(resp.content)} bytes)"
                    )
                    continue
                raw = resp.json()
                cache_file.write_text(json.dumps(raw))
                print(
                    f"[map:geo]   ✓ cached → {cache_file} "
                    f"({len(resp.content) // 1024} KB)"
                )
                break
            except Exception as exc:
                print(f"[map:geo]   ✗ {exc}")

    if raw is None:
        print(
            "[map:geo] WARNING: could not obtain country outlines from any "
            "source — maps will be drawn without land context."
        )
        _world_features_cache = []
        return _world_features_cache

    _world_features_cache = _normalise_world_geojson(raw)
    print(f"[map:geo] {len(_world_features_cache)} country feature(s) ready")
    return _world_features_cache


def _find_country_feature(
    features: list[dict],
    iso_a2: str = "",
    name: str = "",
) -> dict | None:
    """Find the world feature for a country by ISO-A2 first, then by name."""
    a2 = (iso_a2 or "").upper()
    if a2:
        for f in features:
            if f["iso_a2"] == a2:
                return f
    norm = _norm_name(name)
    if norm:
        target = _COUNTRY_NAME_ALIASES.get(norm, norm)
        for f in features:
            if _norm_name(f["name"]) == target:
                return f
    return None


def _norm_name(s: str) -> str:
    return "".join(
        ch for ch in (s or "").lower().strip() if ch.isalnum() or ch == " "
    ).strip()


# ---------------------------------------------------------------------------
# Geocoding (Nominatim) + on-disk cache
# ---------------------------------------------------------------------------


def _classify(item: dict) -> str:
    """Map a Nominatim hit to one of: 'country' | 'region' | 'place'."""
    addresstype = (item.get("addresstype") or "").lower()
    category = (item.get("category") or item.get("class") or "").lower()
    typ = (item.get("type") or "").lower()

    if addresstype == "country" or typ == "country":
        return "country"
    if addresstype in _PLACE_TYPES or (category == "place" and typ in _PLACE_TYPES):
        return "place"
    if addresstype in _REGION_TYPES or category == "boundary":
        return "region"
    # Unknown: a polygon implies an area (region); a bare point implies a place.
    geom = item.get("geojson") or {}
    return "region" if geom.get("type") in ("Polygon", "MultiPolygon") else "place"


# OSM "category" values that are points of interest (companies, buildings,
# shops, quarries, …) rather than the geographic place a map scene means. We
# demote these when ranking so a typo like "Indonesa" can't land on the quarry
# "PT Harmak Indonesa" instead of the country.
_POI_CATEGORIES = {
    "office",
    "shop",
    "amenity",
    "tourism",
    "leisure",
    "building",
    "man_made",
    "craft",
    "highway",
    "railway",
    "aeroway",
    "landuse",
    "historic",
    "military",
    "healthcare",
    "emergency",
    "power",
    "barrier",
    "club",
}


def _hit_score(hit: dict) -> float:
    """Rank a Nominatim hit so real countries/regions/cities beat POIs."""
    cat = (hit.get("category") or hit.get("class") or "").lower()
    typ = (hit.get("type") or "").lower()
    try:
        score = float(hit.get("importance") or 0.0)
    except (TypeError, ValueError):
        score = 0.0

    if cat == "boundary" and typ == "administrative":
        score += 1.5  # countries / states / counties
    elif cat == "place":
        if typ == "country":
            score += 1.6
        elif typ in ("state", "region", "province", "county"):
            score += 1.2
        elif typ in ("city", "town"):
            score += 0.8
        elif typ in ("village", "hamlet", "suburb", "municipality", "borough"):
            score += 0.6
        else:
            score += 0.3
    elif cat in _POI_CATEGORIES:
        score -= 1.0  # demote companies / buildings / etc.
    return score


def _pick_best_hit(hits: list) -> dict | None:
    """Pick the most place-like, prominent hit (not the API's raw #1)."""
    return max(hits, key=_hit_score) if hits else None


def _is_low_confidence(hit: dict) -> bool:
    """True if a hit is a POI / very obscure — i.e. probably not what was meant."""
    cat = (hit.get("category") or hit.get("class") or "").lower()
    typ = (hit.get("type") or "").lower()
    try:
        imp = float(hit.get("importance") or 0.0)
    except (TypeError, ValueError):
        imp = 0.0
    geographic = (cat == "boundary" and typ == "administrative") or cat == "place"
    return (not geographic) or imp < 0.20


def _fuzzy_country_name(term: str, country_names: list) -> str | None:
    """Closest known country name to `term` (repairs typos like 'Indonesa')."""
    by_lower: dict[str, str] = {}
    for n in country_names or []:
        if n:
            by_lower.setdefault(n.lower(), n)
    matches = difflib.get_close_matches(
        term.strip().lower(), list(by_lower), n=1, cutoff=0.84
    )
    return by_lower[matches[0]] if matches else None


def _nominatim_search(query: str) -> list:
    """One rate-limited Nominatim search; returns the raw hit list."""
    _polite_wait()
    try:
        resp = _session.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "polygon_geojson": 1,
                "polygon_threshold": 0.004,  # simplify polygons -> smaller payload
                "addressdetails": 1,
                "dedupe": 1,
                "limit": 10,
                "accept-language": "en",
            },
            timeout=15,
        )
    except Exception as exc:
        print(f"[map:geocode] HTTP error: {exc}")
        return []
    if resp.status_code != 200:
        print(f"[map:geocode] API status {resp.status_code} for '{query}'")
        return []
    return resp.json() or []


def _geocode(
    search_term: str,
    cache_dir: Path | str | None = None,
    country_names: list | None = None,
) -> dict | None:
    """
    Geocode `search_term` and return a normalised dict:

        {ok, lat, lon, bbox, kind, name, country, country_code, geometry}

    bbox is (lon_min, lat_min, lon_max, lat_max). Among the API's results we
    pick the most *place-like* one (so a misspelling can't land on a company
    POI), and if the best match still looks like junk we repair the query
    against known country names. Results are cached to disk keyed by the term.
    """
    base = Path(cache_dir) if cache_dir else (DEFAULT_DATA_DIR / "geocode_cache")
    base.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(search_term.strip().lower().encode("utf-8")).hexdigest()[:16]
    cache_file = base / f"geocode_{key}.json"

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if cached.get("_v") == _GEOCODE_CACHE_VERSION:
                print(
                    f"[map:geocode] cache hit for '{search_term}' "
                    f"→ kind={cached.get('kind')}"
                )
                return cached
            print(
                f"[map:geocode] stale cache for '{search_term}' "
                f"(v{cached.get('_v')}) — refreshing"
            )
        except Exception:
            pass

    print(f"[map:geocode] querying Nominatim for '{search_term}'")
    best = _pick_best_hit(_nominatim_search(search_term))

    # Typo / junk guard: if the best match is a POI (or nothing matched), repair
    # the query against known country names — e.g. "Indonesa" -> "Indonesia".
    if best is None or _is_low_confidence(best):
        corrected = _fuzzy_country_name(search_term, country_names or [])
        if corrected and corrected.lower() != search_term.strip().lower():
            print(
                f"[map:geocode] '{search_term}' unmatched/junk "
                f"— retrying as '{corrected}'"
            )
            alt = _pick_best_hit(_nominatim_search(corrected))
            if alt and (best is None or _hit_score(alt) > _hit_score(best)):
                best = alt

    if not best:
        print(f"[map:geocode] no usable results for '{search_term}'")
        result = {"_v": _GEOCODE_CACHE_VERSION, "ok": False, "query": search_term}
        cache_file.write_text(json.dumps(result))
        return result

    item = best
    address = item.get("address", {}) or {}

    bbox = None
    bb = item.get("boundingbox")
    if bb and len(bb) == 4:
        # Nominatim order: [south, north, west, east]
        s, n, w, e = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
        bbox = (w, s, e, n)

    addresstype = (item.get("addresstype") or "").lower()
    name = (
        item.get("name")
        or address.get(addresstype)
        or (item.get("display_name", "").split(",")[0])
    )

    result = {
        "_v": _GEOCODE_CACHE_VERSION,
        "ok": True,
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
        "bbox": bbox,
        "kind": _classify(item),
        "name": name,
        "country": address.get("country", ""),
        "country_code": (address.get("country_code", "") or "").upper(),
        "geometry": item.get("geojson"),
    }
    print(
        f"[map:geocode] '{search_term}' → {result['name']} "
        f"(kind={result['kind']}, country={result['country']})"
    )
    cache_file.write_text(json.dumps(result))
    return result


# ---------------------------------------------------------------------------
# Geometry helpers (rings, bboxes, projection)
# ---------------------------------------------------------------------------


def _iter_exteriors(geometry: dict):
    """Yield the exterior ring (list of [lon, lat]) of each polygon."""
    if not geometry:
        return
    t = geometry.get("type")
    c = geometry.get("coordinates") or []
    if t == "Polygon":
        if c:
            yield c[0]
    elif t == "MultiPolygon":
        for poly in c:
            if poly:
                yield poly[0]


def _iter_all_rings(geometry: dict):
    """Yield every ring (exterior + holes) — used only for bbox computation."""
    if not geometry:
        return
    t = geometry.get("type")
    c = geometry.get("coordinates") or []
    if t == "Polygon":
        for ring in c:
            yield ring
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                yield ring


def _geom_bbox(geometry: dict):
    """(lon_min, lat_min, lon_max, lat_max) over all of a geometry's points."""
    lons: list[float] = []
    lats: list[float] = []
    for ring in _iter_all_rings(geometry):
        for pt in ring:
            lons.append(pt[0])
            lats.append(pt[1])
    if not lons:
        return None
    return (min(lons), min(lats), max(lons), max(lats))


def _bbox_intersect(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _bbox_reasonable(bbox) -> bool:
    """True if a bbox is a sane, non-degenerate, non-globe-spanning window."""
    lon_min, lat_min, lon_max, lat_max = bbox
    return (
        lon_max > lon_min
        and lat_max > lat_min
        and (lon_max - lon_min) <= 170
        and (lat_max - lat_min) <= 130
    )


def _pad_bbox(bbox, frac: float):
    lon_min, lat_min, lon_max, lat_max = bbox
    dl = (lon_max - lon_min) * frac
    da = (lat_max - lat_min) * frac
    return (lon_min - dl, lat_min - da, lon_max + dl, lat_max + da)


def _around_point(lon: float, lat: float, lat_span_deg: float):
    """A square-ish geo window centred on a point (aspect fixed up later)."""
    half = lat_span_deg / 2.0
    return (lon - half, lat - half, lon + half, lat + half)


def _expand_to_min_span(bbox, min_span_deg: float):
    """Grow a bbox so neither side is smaller than `min_span_deg`, centred."""
    lon_min, lat_min, lon_max, lat_max = bbox
    cx, cy = (lon_min + lon_max) / 2, (lat_min + lat_max) / 2
    lon_span = max(lon_max - lon_min, min_span_deg)
    lat_span = max(lat_max - lat_min, min_span_deg)
    return (cx - lon_span / 2, cy - lat_span / 2, cx + lon_span / 2, cy + lat_span / 2)


def _fit_bbox_to_aspect(bbox, target_aspect: float):
    """
    Grow a geo window so that, after a flat (equirectangular) projection with a
    latitude-cosine correction, it fills `target_aspect` (W/H) without
    squashing the land. Only ever expands, so the feature stays fully inside.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    cx, cy = (lon_min + lon_max) / 2, (lat_min + lat_max) / 2
    lon_span = max(lon_max - lon_min, 1e-4)
    lat_span = max(lat_max - lat_min, 1e-4)
    cosphi = max(math.cos(math.radians(cy)), 0.15)

    current = (lon_span * cosphi) / lat_span
    if current < target_aspect:
        lon_span = target_aspect * lat_span / cosphi
    else:
        lat_span = lon_span * cosphi / target_aspect

    lat_span = min(lat_span, 178.0)
    return (cx - lon_span / 2, cy - lat_span / 2, cx + lon_span / 2, cy + lat_span / 2)


def _make_projector(view, pixel_rect):
    """Build a fn mapping (lon, lat) → (px, py) for a geo `view` into a pixel box."""
    lon_min, lat_min, lon_max, lat_max = view
    x0, y0, x1, y1 = pixel_rect
    dlon = (lon_max - lon_min) or 1e-9
    dlat = (lat_max - lat_min) or 1e-9

    def proj(lon, lat):
        x = x0 + (lon - lon_min) / dlon * (x1 - x0)
        y = y0 + (lat_max - lat) / dlat * (y1 - y0)  # north is up
        return (x, y)

    return proj


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def _load_font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, px)
        except Exception:
            continue
    # Pillow >= 10 supports a size on the bundled bitmap fallback.
    try:
        return ImageFont.load_default(size=px)
    except Exception:
        return ImageFont.load_default()


def _draw_base_map(draw, proj, features, view, border_w: int) -> None:
    """Fill every country that touches the view with land colour + thin border."""
    for f in features:
        fb = f["bbox"]
        if fb and not _bbox_intersect(fb, view):
            continue
        for ext in _iter_exteriors(f["geometry"]):
            pts = [proj(lon, lat) for lon, lat in ext]
            if len(pts) >= 3:
                draw.polygon(
                    pts, fill=COLOR_LAND, outline=COLOR_LAND_BORDER, width=border_w
                )


def _draw_geometry(draw, proj, geometry, fill, outline, width: int) -> None:
    """Fill a single geometry (country/region) in a solid colour."""
    for ext in _iter_exteriors(geometry):
        pts = [proj(lon, lat) for lon, lat in ext]
        if len(pts) >= 3:
            draw.polygon(pts, fill=fill, outline=outline, width=width)


def _draw_pin(draw, x: float, y: float, r: float) -> None:
    """Draw a classic map pin whose tip points exactly at (x, y)."""
    cy = y - 1.7 * r  # circle centre sits above the tip
    # Pointer triangle (drawn first so the circle laps over its top edge).
    draw.polygon(
        [(x - r * 0.75, cy + r * 0.45), (x + r * 0.75, cy + r * 0.45), (x, y)],
        fill=COLOR_PIN,
    )
    ring_w = max(2, int(r / 5))
    draw.ellipse(
        [x - r, cy - r, x + r, cy + r],
        fill=COLOR_PIN,
        outline=COLOR_PIN_EDGE,
        width=ring_w,
    )
    ir = r * 0.4
    draw.ellipse([x - ir, cy - ir, x + ir, cy + ir], fill=COLOR_PIN_DOT)


def _draw_label(draw, x: float, y: float, text: str, font, anchor: str) -> None:
    if not text:
        return
    stroke = max(2, font.size // 7)
    draw.text(
        (x, y),
        text,
        font=font,
        fill=COLOR_LABEL,
        stroke_width=stroke,
        stroke_fill=COLOR_LABEL_HALO,
        anchor=anchor,
    )


def _world_view(render_w: int, render_h: int):
    """A natural 2:1 world projection, letterboxed (extra ocean) into the canvas."""
    map_h = min(render_h, render_w / 2.0)
    map_w = map_h * 2.0
    ox = (render_w - map_w) / 2.0
    oy = (render_h - map_h) / 2.0
    view = (-180.0, -90.0, 180.0, 90.0)
    proj = _make_projector(view, (ox, oy, ox + map_w, oy + map_h))
    return proj, view


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def get_map_image(
    search_term: str,
    output_path: str,
    *,
    width: int = 1920,
    height: int = 1080,
    cache_dir: Path | str | None = None,
    data_dir: Path | str | None = None,
    supersample: int = SUPERSAMPLE,
) -> str | None:
    """
    Render a highlighted map for `search_term` to `output_path` (PNG).

    Returns output_path on success, or None if even the fallback frame could
    not be written.
    """
    print(f"\n[map] >>>>>> get_map_image('{search_term}') → {output_path}")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    features = _load_world_features(data_dir)
    country_names = [f["name"] for f in features if f.get("name")]
    geo = _geocode(search_term, cache_dir, country_names=country_names)

    render_w, render_h = width * supersample, height * supersample
    border_w = max(1, render_h // 1100)
    highlight_w = max(2, render_h // 520)
    label_font = _load_font(int(render_h * 0.052))
    pin_r = render_h * 0.026

    img = Image.new("RGB", (render_w, render_h), COLOR_OCEAN)
    draw = ImageDraw.Draw(img)

    if not geo or not geo.get("ok"):
        print(f"[map] geocoding failed for '{search_term}' — drawing fallback world")
        proj, view = _world_view(render_w, render_h)
        _draw_base_map(draw, proj, features, view, border_w)
        _draw_label(
            draw,
            render_w / 2,
            render_h * 0.93,
            f"{search_term} (not found)",
            label_font,
            anchor="mm",
        )
        return _finish(img, out, width, height)

    kind = geo["kind"]
    print(f"[map] rendering kind='{kind}' for '{geo['name']}'")

    if kind == "country":
        # Whole world, with the country filled in the accent colour.
        proj, view = _world_view(render_w, render_h)
        _draw_base_map(draw, proj, features, view, border_w)

        cf = _find_country_feature(
            features, geo["country_code"], geo["country"] or geo["name"]
        )
        highlight_geom = cf["geometry"] if cf else geo.get("geometry")
        if highlight_geom:
            _draw_geometry(
                draw,
                proj,
                highlight_geom,
                COLOR_HIGHLIGHT,
                COLOR_HIGHLIGHT_LN,
                highlight_w,
            )
        _annotate(
            draw,
            proj,
            name=geo["name"],
            font=label_font,
            pin_r=pin_r,
            render_w=render_w,
            render_h=render_h,
            point=(geo["lon"], geo["lat"]),
            geometry=highlight_geom,
            draw_pin=MAP_PIN_ON_AREAS,
        )

    else:
        # REGION / PLACE → show the parent country (with neighbours), then the
        # region polygon or a pin on top.
        cf = _find_country_feature(features, geo["country_code"], geo["country"])

        if cf and cf["bbox"] and _bbox_reasonable(cf["bbox"]):
            view_src = _pad_bbox(cf["bbox"], 0.08)
        elif geo["bbox"] and _bbox_reasonable(geo["bbox"]):
            view_src = _pad_bbox(_expand_to_min_span(geo["bbox"], 16), 0.4)
        else:
            view_src = _around_point(geo["lon"], geo["lat"], 18)

        view = _fit_bbox_to_aspect(view_src, render_w / render_h)
        proj = _make_projector(view, (0, 0, render_w, render_h))

        _draw_base_map(draw, proj, features, view, border_w)
        if cf:  # tint the parent country so the subject reads in context
            _draw_geometry(
                draw,
                proj,
                cf["geometry"],
                COLOR_PARENT_TINT,
                COLOR_LAND_BORDER,
                border_w,
            )

        if kind == "region" and geo.get("geometry", {}).get("type") in (
            "Polygon",
            "MultiPolygon",
        ):
            _draw_geometry(
                draw,
                proj,
                geo["geometry"],
                COLOR_HIGHLIGHT,
                COLOR_HIGHLIGHT_LN,
                highlight_w,
            )
            _annotate(
                draw,
                proj,
                name=geo["name"],
                font=label_font,
                pin_r=pin_r,
                render_w=render_w,
                render_h=render_h,
                point=(geo["lon"], geo["lat"]),
                geometry=geo["geometry"],
                draw_pin=MAP_PIN_ON_AREAS,
            )
        else:
            _annotate(
                draw,
                proj,
                name=geo["name"],
                font=label_font,
                pin_r=pin_r,
                render_w=render_w,
                render_h=render_h,
                point=(geo["lon"], geo["lat"]),
                geometry=None,
                draw_pin=True,
            )

    return _finish(img, out, width, height)


def _annotate(
    draw,
    proj,
    *,
    name,
    font,
    pin_r,
    render_w,
    render_h,
    point=None,
    geometry=None,
    draw_pin=True,
) -> None:
    """
    Drop a Google-Maps-style pin on the feature and place its NAME label just
    ABOVE the whole feature, never on top of it (clamped to stay on-screen).

    `point` (lon, lat) positions the pin; `geometry`, when given, is used to
    find the feature's top edge so the label clears the entire highlight.
    """
    if point is not None:
        px, py = proj(point[0], point[1])
    elif geometry is not None:
        gb = _geom_bbox(geometry)
        if not gb:
            return
        px, py = proj((gb[0] + gb[2]) / 2, (gb[1] + gb[3]) / 2)
    else:
        return

    if draw_pin:
        _draw_pin(draw, px, py, pin_r)

    # Pixels the label must clear: the pin AND (for areas) the highlighted shape.
    obstacle_top = py - (2.7 * pin_r if draw_pin else 0.0)
    obstacle_bottom = py
    center_x = px
    if geometry is not None:
        gb = _geom_bbox(geometry)
        if gb:
            cx = (gb[0] + gb[2]) / 2
            obstacle_top = min(obstacle_top, proj(cx, gb[3])[1])  # north edge
            obstacle_bottom = max(obstacle_bottom, proj(cx, gb[1])[1])  # south edge
            center_x = proj(cx, gb[3])[0]

    stroke = max(2, int(getattr(font, "size", render_h * 0.05)) // 7)
    left, top, right, bottom = draw.textbbox(
        (0, 0), name, font=font, stroke_width=stroke
    )
    text_w, text_h = right - left, bottom - top

    gap = max(pin_r * 0.8, render_h * 0.012)
    margin = render_h * 0.025

    if obstacle_top - gap - text_h >= margin:
        y, anchor = obstacle_top - gap, "mb"  # preferred: above
    elif obstacle_bottom + gap + text_h <= render_h - margin:
        y, anchor = obstacle_bottom + gap, "mt"  # fallback: below
    else:
        y, anchor = max(margin + text_h, obstacle_top - gap), "mb"

    half = text_w / 2 + margin
    x = min(max(center_x, half), render_w - half)
    _draw_label(draw, x, y, name, font, anchor)


def _finish(img: Image.Image, out: Path, width: int, height: int) -> str | None:
    """Downscale the supersampled render to final size and save."""
    try:
        if img.size != (width, height):
            img = img.resize((width, height), Image.Resampling.LANCZOS)
        img.save(out)
        print(f"[map] <<<<<< wrote {out} ({width}x{height})")
        return str(out)
    except Exception as exc:
        print(f"[map] ERROR saving {out}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Standalone test:  python GET_MAP.py "Indonesia"  ["Bavaria"  "Kyoto" ...]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    terms = sys.argv[1:] or [
        "Indonesia",
        "Brazil",
        "Bavaria",
        "Kyoto",
        "Lancashire, England",
        "Hoddlesden, England",
    ]
    test_dir = DEFAULT_DATA_DIR / "test_output"
    test_dir.mkdir(parents=True, exist_ok=True)
    for t in terms:
        stem = "".join(ch if ch.isalnum() else "_" for ch in t)[:40]
        path = test_dir / f"map_{stem}.png"
        result = get_map_image(t, str(path))
        print(f"  → {t}: {result}")
