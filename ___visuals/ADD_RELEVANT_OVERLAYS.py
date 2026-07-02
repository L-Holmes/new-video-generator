"""
ADD_RELEVANT_OVERLAYS.py
========================

Auto-detect simple things from each scene's script_text and burn small, FIXED
corner badges onto that scene's already-resolved footage:

  - question    : if the section's text contains '?', a '?' badge.
  - measurement : if the section's text STARTS WITH a measurement (allowing one
                  leading word, e.g. "The 5 km ..."), a "<value> <unit> /
                  <converted>" chip showing BOTH metric and imperial.

This is NOT a new MediaType — it's a cross-cutting pass over final_data, run
AFTER Ken Burns so the badge sits on top of the final footage and never gets
panned / zoomed / cropped with the image. It mirrors the colour-grade and
Ken-Burns stages exactly: it transforms footage in place and returns
(final_data, path_remap) for the caller to fold into history.json.

Placement: the first detected badge → TOP-LEFT corner, the second → TOP-RIGHT.
At most two (keeps the frame uncluttered — see MAX_BADGES_PER_SCENE). If the
scene is itself a STICKMAN_TEXT_OVERLAY, the corner its caption occupies is
left free.

Works for both images (PIL composite → PNG) and videos (ffmpeg overlay over the
moving clip, so motion is preserved underneath the fixed badge).

Detection unit = ONE json section (one scene's script_text), per design:
  - '?' anywhere in the section  → question badge.
  - measurement at the START of the section (≤1 leading word) → measurement chip.

Smoke test:
    python ADD_RELEVANT_OVERLAYS.py            # prints detection on sample lines
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from CACHE_IO import _is_image_path, _resolve_to_local_path
from CONFIG import _CACHE_DIR, DEBUG, ProgressTracker, media_props

# Reuse the EXACT caption renderer + deterministic position picker from the
# Fireship-style text overlay, so badges match that look AND so we can recompute
# which corner an existing STICKMAN_TEXT_OVERLAY caption is sitting in.
from MAKE_TEXT_OVERLAY import (
    FPS,
    FRAME_H,
    FRAME_W,
    TILT_DEGREES,
    VIDEO_EXTS,
    _effective_block,
    _fit_pad,
    _load_base_as_frame,
    _pick_combo,
    _render_caption,
    _resolve_caption_font,
)

# ===========================================================================
# CONFIG
# ===========================================================================

# Master switch for the whole stage.
ADD_RELEVANT_OVERLAYS_ENABLE: bool = True

OVERLAY_OUTPUT_DIR = Path(f"{_CACHE_DIR}/relevant_overlays")

# Badge styling. Smaller than the full Fireship caption (FONT_SIZE=92) — these
# are corner UI chips, not the headline.
BADGE_FONT_SIZE: int = 58

# Badge CENTRE as a fraction of (FRAME_W, FRAME_H), per corner — tighter into
# the corners than MAKE_TEXT_OVERLAY's quadrant anchors.
BADGE_ANCHORS: dict[str, tuple[float, float]] = {
    "TL": (0.155, 0.135),
    "TR": (0.845, 0.135),
}
# Order corners are filled in.
ANCHOR_FILL_ORDER: tuple[str, ...] = ("TL", "TR")

# Slight per-corner tilt so the chips read as hand-placed stickers (matches the
# caption tilt magnitude). Positive = CCW in PIL.
BADGE_TILT_BY_ANCHOR: dict[str, float] = {"TL": +TILT_DEGREES, "TR": -TILT_DEGREES}

BADGE_MARGIN_PX: int = 28  # keep chips off the very edge of the frame

# At most this many badges per scene (keep the frame uncluttered).
MAX_BADGES_PER_SCENE: int = 2

# Scene types to skip entirely (by MediaType). Empty by default — badges are
# added to "whatever relevant section". Populate to exclude, e.g. READ_OUT
# (which already shows big text). Compared via media_props flags / type value.
SKIP_TYPE_VALUES: set[str] = set()


# ===========================================================================
# UNIT CONVERSION  (table-driven metric <-> imperial; show BOTH)
# ===========================================================================
# canonical -> (category, factor_to_base, label, counterpart_canonical)
#   length base = metre, mass base = gram, speed base = m/s, volume base = litre.
#   temperature is affine — category "temp", factor unused.
_CANON: dict[str, tuple[str, float, str, str]] = {
    # length (base: metre)
    "mm": ("length", 0.001, "mm", "in"),
    "cm": ("length", 0.01, "cm", "in"),
    "m": ("length", 1.0, "m", "ft"),
    "km": ("length", 1000.0, "km", "mi"),
    "in": ("length", 0.0254, "in", "cm"),
    "ft": ("length", 0.3048, "ft", "m"),
    "yd": ("length", 0.9144, "yd", "m"),
    "mi": ("length", 1609.344, "mi", "km"),
    # mass (base: gram)
    "g": ("mass", 1.0, "g", "oz"),
    "kg": ("mass", 1000.0, "kg", "lb"),
    "oz": ("mass", 28.349523125, "oz", "g"),
    "lb": ("mass", 453.59237, "lb", "kg"),
    "st": ("mass", 6350.29318, "st", "kg"),
    # speed (base: m/s)
    "m/s": ("speed", 1.0, "m/s", "km/h"),
    "km/h": ("speed", 0.277778, "km/h", "mph"),
    "mph": ("speed", 0.44704, "mph", "km/h"),
    "kn": ("speed", 0.514444, "kn", "km/h"),
    # volume (base: litre; US gallon/pint/fl-oz)
    "ml": ("volume", 0.001, "ml", "floz"),
    "l": ("volume", 1.0, "l", "gal"),
    "gal": ("volume", 3.785411784, "gal", "l"),
    "pt": ("volume", 0.473176473, "pt", "l"),
    "floz": ("volume", 0.0295735296, "fl oz", "ml"),
    # temperature (affine)
    "C": ("temp", 0.0, "°C", "F"),
    "F": ("temp", 0.0, "°F", "C"),
    "K": ("temp", 0.0, "K", "C"),
}

# spelling (lower-case) -> canonical. Collision-prone bare forms are dropped:
#   "in" (preposition), "st" (Saint/Street), bare "k" ("5k" == 5000) — the
#   spelled-out / unambiguous forms still match.
_SPELLINGS: dict[str, str] = {
    # length
    "mm": "mm", "millimeter": "mm", "millimeters": "mm",
    "millimetre": "mm", "millimetres": "mm",
    "cm": "cm", "centimeter": "cm", "centimeters": "cm",
    "centimetre": "cm", "centimetres": "cm",
    "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "km": "km", "kilometer": "km", "kilometers": "km",
    "kilometre": "km", "kilometres": "km",
    "inch": "in", "inches": "in",
    "ft": "ft", "foot": "ft", "feet": "ft",
    "yd": "yd", "yard": "yd", "yards": "yd",
    "mi": "mi", "mile": "mi", "miles": "mi",
    # mass
    "g": "g", "gram": "g", "grams": "g", "gramme": "g", "grammes": "g",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kilos": "kg",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "stone": "st", "stones": "st",
    # speed
    "m/s": "m/s", "mps": "m/s",
    "km/h": "km/h", "kph": "km/h", "kmh": "km/h", "kmph": "km/h", "km/hr": "km/h",
    "mph": "mph", "mi/h": "mph",
    "kn": "kn", "kt": "kn", "kts": "kn", "knot": "kn", "knots": "kn",
    # volume
    "ml": "ml", "milliliter": "ml", "milliliters": "ml",
    "millilitre": "ml", "millilitres": "ml",
    "l": "l", "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "gal": "gal", "gallon": "gal", "gallons": "gal",
    "pt": "pt", "pint": "pt", "pints": "pt",
    "floz": "floz", "fl oz": "floz", "fluid ounce": "floz", "fluid ounces": "floz",
    # temperature
    "°c": "C", "c": "C", "celsius": "C", "centigrade": "C",
    "degrees celsius": "C", "degree celsius": "C",
    "°f": "F", "f": "F", "fahrenheit": "F",
    "degrees fahrenheit": "F", "degree fahrenheit": "F",
    "kelvin": "K",  # bare "k" intentionally excluded ("5k" -> 5000 collision)
}

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
# Longest spelling first so "km/h" beats "km", "miles" beats "mi", "fl oz"
# beats "f"/"oz", "°c" beats "c", etc.
_UNIT_ALT = "|".join(
    re.escape(s) for s in sorted(_SPELLINGS, key=len, reverse=True)
)
# ^[<one optional leading word>] <number> [space] <unit> <boundary>
_LEADING_MEASUREMENT_RE = re.compile(
    rf"^\s*(?:[A-Za-z]+[,\s]+)?({_NUMBER})\s*({_UNIT_ALT})\b",
    re.IGNORECASE,
)


def _fmt(x: float) -> str:
    """Short, 'rounded as appropriate' number string (keeps chips compact)."""
    ax = abs(x)
    if ax >= 100:
        s = f"{round(x)}"
    elif ax >= 1:
        s = f"{x:.1f}"
    else:
        s = f"{x:.2f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _temp_str(v: float, canon: str) -> str:
    lbl = _CANON[canon][2]  # "°C" / "°F" / "K"
    return f"{_fmt(v)} K" if canon == "K" else f"{_fmt(v)}{lbl}"


def _convert(value: float, canonical: str) -> str:
    """Return 'X unit / Y otherunit' showing both metric and imperial."""
    cat, factor, label, cp = _CANON[canonical]

    if cat == "temp":
        if canonical == "C":
            c = value
        elif canonical == "F":
            c = (value - 32.0) * 5.0 / 9.0
        else:  # K
            c = value - 273.15
        if cp == "F":
            other = c * 9.0 / 5.0 + 32.0
        elif cp == "C":
            other = c
        else:  # K
            other = c + 273.15
        return f"{_temp_str(value, canonical)} / {_temp_str(other, cp)}"

    base = value * factor
    _, cp_factor, cp_label, _ = _CANON[cp]
    other = base / cp_factor
    return f"{_fmt(value)} {label} / {_fmt(other)} {cp_label}"


# ===========================================================================
# DETECTION  (basic regex, deterministic, per json section)
# ===========================================================================

# Each detector returns a chip string or None. Position policy per type:
#   - question / date / time / percentage : match ANYWHERE in the section.
#   - measurement / money                 : must START the section (≤1 leading
#                                           word, e.g. "The £5 coin").
# (Per the brief: "sentences that have dates get that date on screen"; "just
#  whatever time is mentioned"; but money/measurements "if the sentence starts,
#  or the 2nd word".) Flip a detector between modes by swapping .search/.match.

_LEAD = r"(?:[A-Za-z]+[,\s]+)?"  # optional single leading word ("The ", "About ")


def _detect_question(text: str) -> str | None:
    return "?" if "?" in (text or "") else None


def _detect_leading_measurement(text: str) -> str | None:
    """`text` starts with a measurement (≤1 leading word) → 'metric / imperial'."""
    m = _LEADING_MEASUREMENT_RE.match(text or "")
    if not m:
        return None
    canon = _SPELLINGS.get(m.group(2).lower())
    if not canon:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return _convert(value, canon)


# --- percentages (anywhere) ------------------------------------------------
_PERCENT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:%|(?:per\s?cent|percent)\b)", re.IGNORECASE
)


def _detect_percentage(text: str) -> str | None:
    m = _PERCENT_RE.search(text or "")
    return f"{_fmt(float(m.group(1)))}%" if m else None


# --- times (anywhere) ------------------------------------------------------
# HH:MM[ am/pm]  OR  H am/pm. Two-digit minutes required so "1:1" (ratio) is ignored.
_TIME_RE = re.compile(
    r"\b(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)?\b|\b(\d{1,2})\s*([ap]\.?m\.?)\b",
    re.IGNORECASE,
)


def _detect_time(text: str) -> str | None:
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    if m.group(1) is not None:  # HH:MM form
        h, mm, ap = m.group(1), m.group(2), m.group(3)
        suffix = f" {ap.replace('.', '').upper()}" if ap else ""
        return f"{h}:{mm}{suffix}"
    return f"{m.group(4)} {m.group(5).replace('.', '').upper()}"  # H am/pm


# --- dates (anywhere) ------------------------------------------------------
_MONTHS = {
    "january": "January", "jan": "January",
    "february": "February", "feb": "February",
    "march": "March", "mar": "March",
    "april": "April", "apr": "April",
    "may": "May",
    "june": "June", "jun": "June",
    "july": "July", "jul": "July",
    "august": "August", "aug": "August",
    "september": "September", "sep": "September", "sept": "September",
    "october": "October", "oct": "October",
    "november": "November", "nov": "November",
    "december": "December", "dec": "December",
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th)"
# 14[th] [of] July[, 1789]
_DMY_RE = re.compile(
    rf"\b(\d{{1,2}}){_ORD}?\s+(?:of\s+)?({_MONTH_ALT})\b(?:\s*,?\s*(\d{{4}}))?",
    re.IGNORECASE,
)
# July 14[th][, 1789]
_MDY_RE = re.compile(
    rf"\b({_MONTH_ALT})\s+(\d{{1,2}}){_ORD}?\b(?:\s*,?\s*(\d{{4}}))?",
    re.IGNORECASE,
)
# July 1789
_MY_RE = re.compile(rf"\b({_MONTH_ALT})\s+(\d{{4}})\b", re.IGNORECASE)
# bare year 1000–2099 (no comma → "5,000 men" can't match)
_YEAR_RE = re.compile(r"\b(1\d{3}|20\d{2})\b")


def _month_full(token: str) -> str:
    return _MONTHS.get(token.lower(), token.title())


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _detect_date(text: str) -> str | None:
    text = text or ""
    m = _DMY_RE.search(text)
    if m:
        out = f"{_ordinal(int(m.group(1)))} {_month_full(m.group(2))}"
        return f"{out} {m.group(3)}" if m.group(3) else out
    m = _MDY_RE.search(text)
    if m:
        out = f"{_ordinal(int(m.group(2)))} {_month_full(m.group(1))}"
        return f"{out} {m.group(3)}" if m.group(3) else out
    m = _MY_RE.search(text)
    if m:
        return f"{_month_full(m.group(1))} {m.group(2)}"
    m = _YEAR_RE.search(text)
    return m.group(1) if m else None


# --- money (starts the section, ≤1 leading word) ---------------------------
# Exchange rates are STATIC + APPROXIMATE (units per 1 USD). Update as needed,
# or wire a fetch upstream and pass them in. Order shown: £ · € · $.
MONEY_RATES_PER_USD: dict[str, float] = {"USD": 1.0, "GBP": 0.79, "EUR": 0.92}
_MONEY_DISPLAY = (("GBP", "£"), ("EUR", "€"), ("USD", "$"))

_MONEY_SYMBOL = {"£": "GBP", "$": "USD", "€": "EUR"}
_MONEY_WORD = {
    "dollar": "USD", "dollars": "USD", "usd": "USD",
    "pound": "GBP", "pounds": "GBP", "gbp": "GBP",
    "euro": "EUR", "euros": "EUR", "eur": "EUR",
}
# magnitude suffix -> (compact label kept on screen, multiplier). "Just show 5m."
_MONEY_SUFFIX = {
    "k": ("k", 1e3), "thousand": ("k", 1e3),
    "m": ("m", 1e6), "million": ("m", 1e6), "mn": ("m", 1e6),
    "b": ("bn", 1e9), "bn": ("bn", 1e9), "billion": ("bn", 1e9),
    "tr": ("tr", 1e12), "trillion": ("tr", 1e12),
}
_SUFFIX_ALT = "|".join(sorted(_MONEY_SUFFIX, key=len, reverse=True))
_AMT = r"\d[\d,]*(?:\.\d+)?"
# £5[m]  /  $5 million
_MONEY_SYM_RE = re.compile(
    rf"^\s*{_LEAD}([£$€])\s?({_AMT})\s*({_SUFFIX_ALT})?\b", re.IGNORECASE
)
# 5[m] dollars  /  5 million pounds
_MONEY_WORD_RE = re.compile(
    rf"^\s*{_LEAD}({_AMT})\s*({_SUFFIX_ALT})?\s*"
    rf"(dollars?|pounds?|euros?|usd|gbp|eur)\b",
    re.IGNORECASE,
)


def _money_chip(currency: str, coeff: float, suffix_label: str) -> str:
    usd = coeff / MONEY_RATES_PER_USD[currency]
    parts = [
        f"{sym}{_fmt(usd * MONEY_RATES_PER_USD[code])}{suffix_label}"
        for code, sym in _MONEY_DISPLAY
    ]
    return " · ".join(parts)


def _detect_money(text: str) -> str | None:
    text = text or ""
    m = _MONEY_SYM_RE.match(text)
    if m:
        currency = _MONEY_SYMBOL[m.group(1)]
        coeff = float(m.group(2).replace(",", ""))
        suffix_label = _MONEY_SUFFIX[m.group(3).lower()][0] if m.group(3) else ""
        return _money_chip(currency, coeff, suffix_label)
    m = _MONEY_WORD_RE.match(text)
    if m:
        currency = _MONEY_WORD[m.group(3).lower()]
        coeff = float(m.group(1).replace(",", ""))
        suffix_label = _MONEY_SUFFIX[m.group(2).lower()][0] if m.group(2) else ""
        return _money_chip(currency, coeff, suffix_label)
    return None


# Priority order for "just the first two we detect". Reorder freely.
DETECTORS: list[tuple[str, "callable"]] = [
    ("question", _detect_question),
    ("measurement", _detect_leading_measurement),
    ("money", _detect_money),
    ("percentage", _detect_percentage),
    ("date", _detect_date),
    ("time", _detect_time),
]


def _badges_for_text(text: str) -> list[str]:
    """First MAX_BADGES_PER_SCENE chips, in DETECTORS priority order."""
    badges: list[str] = []
    for _name, detector in DETECTORS:
        chip = detector(text)
        if chip:
            badges.append(chip)
            if len(badges) >= MAX_BADGES_PER_SCENE:
                break
    return badges


def _free_anchors(text: str, script_to_search_term: dict) -> list[str]:
    """Corners available for badges, removing the one a STICKMAN_TEXT_OVERLAY
    caption already occupies on this same scene."""
    free = list(ANCHOR_FILL_ORDER)
    data = script_to_search_term.get(text)
    if data and media_props(data.get("search_type")).is_text_overlay:
        # generate_text_overlay_scenes calls make_text_overlay(seed=txt), so the
        # caption anchor is deterministic: _pick_combo(txt)[0] -> "TL"/"TR"/"C".
        caption_anchor = _pick_combo(text)[0]
        if caption_anchor in free:
            free.remove(caption_anchor)
            if DEBUG:
                print(
                    f"[overlays]   '{text[:40]}' is a text-overlay; caption in "
                    f"{caption_anchor} — leaving that corner free"
                )
    return free


# ===========================================================================
# RENDERING  (reuse the Fireship card look at a smaller size)
# ===========================================================================

def _render_badge_card(badge_text: str, tilt: float) -> Image.Image:
    """Render one badge as a tilted RGBA card (same styling as the captions)."""
    _, is_pixel = _resolve_caption_font()
    block = _effective_block(is_pixel, None)
    card = _render_caption(
        badge_text, anchor="TL", font_size=BADGE_FONT_SIZE, pixel_block=block
    )
    resample = Image.NEAREST if block > 1 else Image.BICUBIC
    return card.rotate(tilt, expand=True, resample=resample)


def _paste_into_layer(layer: Image.Image, card: Image.Image, anchor: str) -> None:
    cw, ch = card.size
    ax, ay = BADGE_ANCHORS[anchor]
    x = int(round(ax * FRAME_W - cw / 2))
    y = int(round(ay * FRAME_H - ch / 2))
    m = BADGE_MARGIN_PX
    x = max(m, min(x, FRAME_W - cw - m))
    y = max(m, min(y, FRAME_H - ch - m))
    layer.alpha_composite(card, (x, y))


def _build_overlay_layer(pairs: list[tuple[str, str]]) -> Image.Image | None:
    """Full-frame transparent RGBA layer with each (badge_text, anchor) placed.
    Returns None if nothing was placed."""
    if not pairs:
        return None
    layer = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
    for badge_text, anchor in pairs:
        card = _render_badge_card(badge_text, BADGE_TILT_BY_ANCHOR.get(anchor, 0.0))
        _paste_into_layer(layer, card, anchor)
    return layer


# ===========================================================================
# COMPOSITING  (image -> PNG, video -> ffmpeg overlay with motion preserved)
# ===========================================================================

def _cache_path(source_local: str, pairs: list[tuple[str, str]], is_image: bool) -> str:
    key = (
        f"{source_local}|fs{BADGE_FONT_SIZE}|"
        + "|".join(f"{a}:{t}" for t, a in pairs)
    )
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    ext = ".png" if is_image else ".mp4"
    return str(OVERLAY_OUTPUT_DIR / f"ov-{h}{ext}")


def _overlay_on_image(source_local: str, layer: Image.Image, out_path: str) -> None:
    base = _fit_pad(_load_base_as_frame(source_local), FRAME_W, FRAME_H)
    out = base.convert("RGBA")
    out.alpha_composite(layer)
    out.convert("RGB").save(out_path)


def _overlay_on_video(source_local: str, layer_png: str, out_path: str) -> None:
    # Fit/pad to frame exactly like the stitcher (scale=decrease + black pad),
    # normalise to 30fps, then overlay the full-frame badge layer at (0,0).
    fc = (
        f"[0:v]scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease,"
        f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}[bg];"
        f"[bg][1:v]overlay=0:0:format=auto[v]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", source_local,
        "-i", layer_png,
        "-filter_complex", fc,
        "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "18", "-an",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        Path(out_path).unlink(missing_ok=True)
        raise RuntimeError(
            f"overlay render failed for {source_local}: {r.stderr[-600:]}"
        )


# ===========================================================================
# PUBLIC PASS  (mirrors apply_ken_burns_to_final_data)
# ===========================================================================

def apply_relevant_overlays_to_final_data(
    final_data: list[dict],
    script_to_search_term: dict,
) -> tuple[list[dict], dict[str, str]]:
    """
    Walk `final_data`, and for every scene whose script_text triggers a badge,
    burn the badge(s) onto that scene's footage (images and videos alike),
    replacing the footage path with the new file.

    Returns (final_data, path_remap) where path_remap is {old_path: new_path},
    so the caller can update history.json — exactly like the Ken Burns /
    colour-grade passes.
    """
    print("\n" + "=" * 70)
    print("[overlays] APPLYING auto-detected overlays (questions / measurements)")
    print(f"[overlays] enabled={ADD_RELEVANT_OVERLAYS_ENABLE}")
    print("=" * 70)

    if not ADD_RELEVANT_OVERLAYS_ENABLE:
        print("[overlays] ADD_RELEVANT_OVERLAYS_ENABLE=False — skipping")
        return final_data, {}

    # Plan first (so we can size the progress bar + skip no-op scenes).
    plan: list[tuple[dict, list[tuple[str, str]]]] = []
    for entry in final_data:
        text = entry.get("script_text", "")
        data = script_to_search_term.get(text)
        if data and data.get("search_type") is not None:
            stype = data["search_type"]
            value = getattr(stype, "value", stype)
            if value in SKIP_TYPE_VALUES:
                continue
        badges = _badges_for_text(text)
        if not badges:
            continue
        free = _free_anchors(text, script_to_search_term)
        pairs = list(zip(badges, free))  # drops extras if a corner is occupied
        if not pairs:
            continue
        plan.append((entry, pairs))

    if not plan:
        print("[overlays] nothing to overlay — no scene matched")
        return final_data, {}

    n_items = sum(len(e.get("footage", []) or [{}]) for e, _ in plan)
    print(f"[overlays] {len(plan)} scene(s) matched; processing {n_items} footage item(s)")
    for entry, pairs in plan:
        chips = ", ".join(f"{a}:{t}" for t, a in pairs)
        print(f"[overlays]   '{entry['script_text'][:55]}' -> {chips}")

    OVERLAY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tracker = ProgressTracker(total=max(1, n_items), label="OVERLAYS")
    path_remap: dict[str, str] = {}
    n_done = n_skipped = n_failed = 0

    for entry, pairs in plan:
        layer = _build_overlay_layer(pairs)
        if layer is None:
            continue

        # Render the layer to a temp PNG once per scene (videos need a file).
        layer_png = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        layer.save(layer_png)

        try:
            new_footage: list[dict] = []
            for footage_item in entry.get("footage", []):
                new_item: dict = {}
                for path, trim in footage_item.items():
                    local = _resolve_to_local_path(path)
                    if not local:
                        print(f"\n[overlays] WARNING: can't resolve to disk: {path}")
                        new_item[path] = trim
                        n_skipped += 1
                        tracker.tick()
                        continue

                    is_image = _is_image_path(local)
                    is_video = Path(local).suffix.lower() in VIDEO_EXTS
                    if not (is_image or is_video):
                        print(f"\n[overlays] WARNING: unknown media type, skipping: {local}")
                        new_item[path] = trim
                        n_skipped += 1
                        tracker.tick()
                        continue

                    out_path = _cache_path(local, pairs, is_image)
                    if not (Path(out_path).exists() and Path(out_path).stat().st_size > 1024):
                        try:
                            if is_image:
                                _overlay_on_image(local, layer, out_path)
                            else:
                                _overlay_on_video(local, layer_png, out_path)
                        except Exception as exc:
                            print(f"\n[overlays] ERROR overlaying {local}: {exc} — keeping original")
                            new_item[path] = trim
                            n_failed += 1
                            tracker.tick()
                            continue
                    elif DEBUG:
                        print(f"  [overlays cache hit] {Path(out_path).name}")

                    new_item[out_path] = trim
                    path_remap[path] = out_path
                    n_done += 1
                    tracker.tick()
                new_footage.append(new_item)
            entry["footage"] = new_footage
        finally:
            Path(layer_png).unlink(missing_ok=True)

    tracker.finish()
    print(
        f"[overlays] DONE — overlaid={n_done}, skipped={n_skipped}, failed={n_failed}"
    )
    return final_data, path_remap


# ===========================================================================
# STANDALONE SMOKE TEST (detection only — no rendering / ffmpeg)
# ===========================================================================

if __name__ == "__main__":
    samples = [
        "But where exactly in the world did this tea originate?",
        "The 5 km journey took them three days.",
        "5km of open ocean lay ahead.",
        "It was about 5 km to the nearest port.",  # 3 leading words -> no chip
        "20°C is a pleasant spring morning.",
        "10 miles from here, the island appears.",
        "60 mph winds battered the deck.",
        "150 kg of nutmeg was worth a fortune.",
        "He walked 5 in the morning.",            # 'in' dropped -> no false chip
        "Manhattan was traded for nutmeg.",        # nothing
        # dates (anywhere)
        "On the 14th of July 1789, revolution began.",
        "It happened in 1623, deep in the spice wars.",
        "By July 1850 the trade had collapsed.",
        "They signed the treaty on March 3.",
        # times (anywhere)
        "The attack came at 3pm sharp.",
        "Trading opened at 9:30 AM.",
        "The signal fired at 15:00.",
        # percentages (anywhere)
        "Nutmeg made up 80% of the cargo.",
        "Prices fell by 12.5 percent overnight.",
        # money (starts / 2nd word)
        "$5 million bought a single ship.",
        "The £10 coin was pure silver.",
        "5,000 dollars was a year's wages.",
        "It cost $5 — but here it's deep in the sentence.",  # 2+ leading words
        # multi-trigger (first two win)
        "In 1789, 50% of the fleet was lost. Was it worth it?",
    ]
    for s in samples:
        print(f"{s!r}\n   -> {_badges_for_text(s)}\n")
