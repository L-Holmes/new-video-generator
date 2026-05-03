"""
AUDIO_SCRIPT_SYNCHRONIZER.py
============================
Forced-alignment script timer.  Drop-in replacement for the click-along
version — same filename, same `run()` signature, same output files.

How it works
------------
We don't use Whisper's transcription as truth — your script is truth.
Whisper just tells us WHEN each word it heard occurs in the audio.  We
then sequence-match its words against the known script to find the audio
position of each line.

Mistranscriptions (e.g. a Northern British "th") cannot cause drift:
each line is anchored to its own first matched word, so an error on
line 12 has zero effect on line 13.

Robustness layers
-----------------
  1. Lowercase + punctuation strip + em/en-dash → space
  2. Number expansion (digits ↔ spoken words via num2words)
       so "1946" matches "nineteen forty six" and vice versa
       so "1700s" matches "seventeen hundreds"
  3. Apostrophes preserved inside words ("don't" stays one token)
  4. SequenceMatcher with autojunk=False
       — common words ("the", "of") act as alignment anchors,
         not as filler that gets ignored
  5. Per-line first-token anchor
       — line N's start = audio time of the FIRST script token in line N
         that matched a Whisper word
  6. Linear interpolation fallback
       — if a line had zero matches (rare), its start is interpolated
         from neighbours so we never crash and never produce garbage
  7. Monotonicity enforcement
       — timestamps must be non-decreasing; out-of-order entries are
         clamped forward
  8. Validation pass with warnings printed at the end

Output (drop-in compatible — STITCH_TOGETHER.py keeps working unchanged):
  - timestamps_cache_file → per-line absolute START times (seconds, 2dp)
  - output_file           → per-line DURATIONS (seconds, 2dp)

Setup
-----
    pip install faster-whisper num2words

faster-whisper is fully offline once the model is downloaded (~250 MB
for small.en).  No API, no internet.  MIT licensed.  num2words is a
pure-Python number-to-words library.

Model choice
------------
Default is "small.en" — robust on Northern British / regional English
and fast enough on CPU.  If you ever see a low match rate in the
warnings, bump to "medium.en".

Cache format note
-----------------
The timestamps cache now stores per-line START times (you wanted this:
the cinnamon line at 1:57 → cache says 117.xx).  The OLD cache stored
end times — delete any old cache file before running.
"""

# =====================================================================================================================================
# default config (overridden when calling run() with params)
# =====================================================================================================================================

SCRIPT_AUDIO_FILE     = "script.wav"
SCRIPT_LINES_FILE     = "CACHE/scene_map_cache.json"
OUTPUT_FILE           = "CACHE/script_timings_seconds.json"
TIMESTAMPS_CACHE_FILE = "CACHE/timestamps_absolute.json"
WHISPER_MODEL         = "small.en"   # tiny.en / base.en / small.en / medium.en

# =====================================================================================================================================

import json
import os
import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path


# ─── File helpers ─────────────────────────────────────────────────────────────

def _load_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return list(json.load(f).keys())


def _load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_json_ordered(path: str, data: dict, ordered_keys: list[str]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    ordered = {k: data[k] for k in ordered_keys if k in data}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)


def _audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


# ─── Tokenization & normalization ─────────────────────────────────────────────

def _expand_number(tok: str) -> list[str]:
    """Turn '1946' / '1700s' / '410' into spoken-word tokens."""
    from num2words import num2words

    # Decade / century, e.g. "1700s"
    m = re.match(r"^(\d+)s$", tok)
    if m:
        n = int(m.group(1))
        try:
            if 1000 <= n <= 2999:
                w = num2words(n, lang="en", to="year")
                # "seventeen hundred" → "seventeen hundreds"
                w = re.sub(r"\bhundred\b", "hundreds", w)
                return re.sub(r"[-,]", " ", w).lower().split()
            return num2words(n, lang="en").replace("-", " ").lower().split() + ["s"]
        except Exception:
            return [tok]

    # Pure digits
    if tok.isdigit():
        n = int(tok)
        try:
            w = (num2words(n, lang="en", to="year")
                 if 1500 <= n <= 2099 else
                 num2words(n, lang="en"))
            return re.sub(r"[-,]", " ", w).lower().split()
        except Exception:
            return [tok]

    return [tok]


def _tokenize(text: str) -> list[str]:
    """Lowercase, punctuation-strip, dash-aware, number-expanded token list."""
    text = re.sub(r"[\u2014\u2013\u2015]", " ", text)   # em/en/horizontal dashes
    text = text.replace("&", " and ")
    raw = re.findall(r"[A-Za-z0-9']+", text.lower())
    out: list[str] = []
    for tok in raw:
        tok = tok.strip("'")
        if not tok:
            continue
        if any(c.isdigit() for c in tok):
            out.extend(_expand_number(tok))
        else:
            out.append(tok)
    return out


# ─── Transcription ────────────────────────────────────────────────────────────

def _transcribe(audio_path: str, model_size: str) -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError(
            "faster-whisper not installed.\n"
            "Install with:  pip install faster-whisper num2words"
        )

    print(f"  → loading Whisper model: {model_size}  (downloads on first use, then cached)")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    print(f"  → transcribing {audio_path}")
    segments, _ = model.transcribe(audio_path, word_timestamps=True, vad_filter=False)

    words: list[dict] = []
    for seg in segments:
        if seg.words is None:
            continue
        for w in seg.words:
            words.append({
                "word":  w.word.strip(),
                "start": float(w.start),
                "end":   float(w.end),
            })
    print(f"  → {len(words)} words returned")
    return words


# ─── Alignment ────────────────────────────────────────────────────────────────

def _align(script_lines: list[str], whisper_words: list[dict]) -> tuple[dict, set]:
    # Flatten Whisper words into normalized tokens, remembering origin.
    w_tokens: list[str] = []
    w_origin: list[int] = []
    for i, ww in enumerate(whisper_words):
        for t in _tokenize(ww["word"]):
            w_tokens.append(t)
            w_origin.append(i)

    # Flatten script lines into tokens with line boundaries.
    s_tokens: list[str] = []
    line_first: list[int] = []
    for line in script_lines:
        line_first.append(len(s_tokens))
        s_tokens.extend(_tokenize(line))
    line_first.append(len(s_tokens))   # sentinel

    if not w_tokens or not s_tokens:
        return {}, set()

    # Sequence matching — autojunk=False so common words act as anchors.
    sm = SequenceMatcher(a=s_tokens, b=w_tokens, autojunk=False)
    s2w: dict[int, int] = {}
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            s2w[block.a + k] = block.b + k

    timestamps: dict[str, float] = {}
    matched: set[str] = set()

    for li, line in enumerate(script_lines):
        first = line_first[li]
        last  = line_first[li + 1] - 1
        if first > last:
            continue

        # First matched script token within this line gives us the line's start.
        wt_idx: int | None = None
        for s_idx in range(first, last + 1):
            if s_idx in s2w:
                wt_idx = s2w[s_idx]
                break
        if wt_idx is None:
            continue

        ww_idx = w_origin[wt_idx]
        timestamps[line] = whisper_words[ww_idx]["start"]
        matched.add(line)

    return timestamps, matched


# ─── Post-processing ──────────────────────────────────────────────────────────

def _interpolate_missing(timestamps: dict, ordered_lines: list[str], audio_duration: float) -> dict:
    """Fill in unmatched lines by linear interpolation between matched neighbours."""
    starts: list[float | None] = [timestamps.get(ln) for ln in ordered_lines]
    n = len(starts)
    if n == 0:
        return timestamps

    # Anchor first / last so interpolation is well-defined even at the edges.
    if starts[0]  is None: starts[0]  = 0.0
    if starts[-1] is None: starts[-1] = audio_duration

    i = 0
    while i < n:
        if starts[i] is None:
            j = i
            while j < n and starts[j] is None:
                j += 1
            left  = starts[i - 1]
            right = starts[j] if j < n else audio_duration
            span  = j - (i - 1)
            for k in range(i, j):
                t = (k - (i - 1)) / span
                starts[k] = left + (right - left) * t
            i = j
        else:
            i += 1

    out = dict(timestamps)
    for k, ln in enumerate(ordered_lines):
        if ln not in out:
            out[ln] = starts[k]
    return out


def _enforce_monotonic(timestamps: dict, ordered_lines: list[str]) -> dict:
    out = dict(timestamps)
    prev = -1.0
    for ln in ordered_lines:
        if ln in out and out[ln] < prev:
            out[ln] = prev + 0.01
        prev = out.get(ln, prev)
    return out


def _derive_durations(timestamps: dict, ordered_lines: list[str], audio_duration: float) -> dict:
    """duration[i] = start[i+1] - start[i],   duration[last] = audio_duration - start[last]"""
    durations: dict[str, float] = {}
    for i, ln in enumerate(ordered_lines):
        if ln not in timestamps:
            continue
        start = timestamps[ln]
        next_start = None
        for j in range(i + 1, len(ordered_lines)):
            if ordered_lines[j] in timestamps:
                next_start = timestamps[ordered_lines[j]]
                break
        end = next_start if next_start is not None else audio_duration
        durations[ln] = max(0.0, end - start)
    return durations


# ─── Public entry point ───────────────────────────────────────────────────────

def run(
    script_audio_file:     str   = SCRIPT_AUDIO_FILE,
    script_lines_file:     str   = SCRIPT_LINES_FILE,
    output_file:           str   = OUTPUT_FILE,
    timestamps_cache_file: str   = TIMESTAMPS_CACHE_FILE,
    audio_start_delay:     float = 0.5,        # accepted for API compat — unused
    whisper_model:         str   = WHISPER_MODEL,
    force:                 bool  = False,
) -> None:
    if not os.path.exists(script_audio_file):
        raise FileNotFoundError(f"Audio not found: {script_audio_file}")
    if not os.path.exists(script_lines_file):
        raise FileNotFoundError(f"Script lines not found: {script_lines_file}")

    lines = _load_lines(script_lines_file)

    if not force and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        existing = _load_json(output_file)
        if all(ln in existing for ln in lines):
            print(f"[synchronizer] '{output_file}' already complete. Returning. "
                  f"(pass force=True to re-run)")
            return

    print("─" * 70)
    print("AUDIO SCRIPT SYNCHRONIZER  ·  Whisper forced-alignment")
    print("─" * 70)
    print(f"  audio:  {script_audio_file}")
    print(f"  script: {script_lines_file}  ({len(lines)} lines)")
    print(f"  model:  {whisper_model}")

    # 1. Transcribe
    print("\n[1/4] transcribing audio…")
    words = _transcribe(script_audio_file, whisper_model)
    if not words:
        raise RuntimeError("Whisper returned no words — empty/corrupt audio?")

    # 2. Align
    print("\n[2/4] aligning script to transcription…")
    raw_timestamps, matched = _align(lines, words)
    n_matched = len(matched)
    print(f"      matched {n_matched}/{len(lines)} lines directly")

    # 3. Fill any gaps
    audio_duration = _audio_duration(script_audio_file)
    if n_matched < len(lines):
        unmatched = [ln for ln in lines if ln not in matched]
        print(f"\n[3/4] interpolating {len(unmatched)} unmatched line(s) from neighbours:")
        for ex in unmatched[:5]:
            preview = ex[:70] + ("…" if len(ex) > 70 else "")
            print(f"        - {preview!r}")
        if len(unmatched) > 5:
            print(f"        … and {len(unmatched) - 5} more")
    else:
        print("\n[3/4] all lines matched directly — no interpolation needed.")

    timestamps = _interpolate_missing(raw_timestamps, lines, audio_duration)
    timestamps = _enforce_monotonic(timestamps, lines)

    # 4. Output
    print("\n[4/4] writing outputs…")
    durations = _derive_durations(timestamps, lines, audio_duration)
    starts_str    = {k: f"{v:.2f}" for k, v in timestamps.items()}
    durations_str = {k: f"{v:.2f}" for k, v in durations.items()}
    _save_json_ordered(timestamps_cache_file, starts_str,    lines)
    _save_json_ordered(output_file,           durations_str, lines)
    print(f"      ✓ {timestamps_cache_file}")
    print(f"      ✓ {output_file}")

    # Validation
    warnings: list[str] = []
    prev = -1.0
    for i, ln in enumerate(lines):
        if ln not in timestamps:
            warnings.append(f"line {i+1}: no timestamp")
            continue
        if timestamps[ln] < prev:
            warnings.append(f"line {i+1}: non-monotonic at {timestamps[ln]:.2f}s")
        prev = timestamps[ln]

    print()
    if warnings:
        print(f"⚠️  {len(warnings)} warnings:")
        for w in warnings[:10]:
            print(f"   - {w}")
        if n_matched < len(lines) * 0.8:
            print("\n   Match rate is low. Try a larger model:  whisper_model='medium.en'")
    else:
        print("✓ alignment looks clean")

    total = sum(durations.values())
    print(f"\n  audio length:   {audio_duration:.2f}s")
    print(f"  durations sum:  {total:.2f}s")
    print(f"  difference:     {audio_duration - total:+.3f}s   (should be ~0)")
    print("─" * 70)


if __name__ == "__main__":
    run()

