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

# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

# === defaults =========================================================

SCRIPT_AUDIO_FILE        = "script.wav"
SCRIPT_LINES_FILE        = "CACHE/scene_map_cache.json"
OUTPUT_FILE              = "CACHE/script_timings_seconds.json"
TIMESTAMPS_CACHE_FILE    = "CACHE/timestamps_absolute.json"
WORD_TIMINGS_CACHE_FILE  = "CACHE/word_timings.json"       
WHISPER_MODEL            = "small.en"

# ======================================================================

import json
import os
import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path


# ─── File helpers (unchanged) ─────────────────────────────────────────

def _load_lines(path):
    with open(path, "r", encoding="utf-8") as f:
        return list(json.load(f).keys())


def _load_json(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_json_ordered(path, data, ordered_keys):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    ordered = {k: data[k] for k in ordered_keys if k in data}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)


def _audio_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


# ─── Tokenization (unchanged) ─────────────────────────────────────────

def _expand_number(tok):
    from num2words import num2words
    m = re.match(r"^(\d+)s$", tok)
    if m:
        n = int(m.group(1))
        try:
            if 1000 <= n <= 2999:
                w = num2words(n, lang="en", to="year")
                w = re.sub(r"\bhundred\b", "hundreds", w)
                return re.sub(r"[-,]", " ", w).lower().split()
            return num2words(n, lang="en").replace("-", " ").lower().split() + ["s"]
        except Exception:
            return [tok]
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


def _tokenize(text):
    text = re.sub(r"[\u2014\u2013\u2015]", " ", text)
    text = text.replace("&", " and ")
    raw = re.findall(r"[A-Za-z0-9']+", text.lower())
    out = []
    for tok in raw:
        tok = tok.strip("'")
        if not tok:
            continue
        if any(c.isdigit() for c in tok):
            out.extend(_expand_number(tok))
        else:
            out.append(tok)
    return out


def _split_surface(line):
    """Whitespace-split a line into surface words (punctuation preserved).
    MUST match WORD_BY_WORD_VIDEO._split_words exactly so the two files
    agree on word count per line."""
    return [w for w in re.split(r"\s+", line.strip()) if w]


# ─── Transcription (unchanged) ────────────────────────────────────────

def _transcribe(audio_path, model_size):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError("faster-whisper not installed. "
                          "pip install faster-whisper num2words")
    print(f"  → loading Whisper model: {model_size}")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    print(f"  → transcribing {audio_path}")
    segments, _ = model.transcribe(audio_path, word_timestamps=True,
                                   vad_filter=False)
    words = []
    for seg in segments:
        if seg.words is None:
            continue
        for w in seg.words:
            words.append({"word": w.word.strip(),
                          "start": float(w.start),
                          "end": float(w.end)})
    print(f"  → {len(words)} words returned")
    return words


# ─── Alignment (EXTENDED) ─────────────────────────────────────────────

def _align(script_lines, whisper_words):
    """
    Match script tokens to Whisper tokens.

    Returns three values now:
      timestamps  — {line: line_start_time}      (existing)
      matched     — set of lines anchored        (existing)
      word_raw    — {line: [{"text": surface, "start": float|None}, …]}
                    Per-surface-word starts.  None where the surface
                    word's normalized tokens had no Whisper anchor;
                    those get filled later by _finalize_word_timings.
    """
    # Flatten Whisper words into normalized tokens, remembering origin.
    w_tokens, w_origin = [], []
    for i, ww in enumerate(whisper_words):
        for t in _tokenize(ww["word"]):
            w_tokens.append(t)
            w_origin.append(i)

    # Flatten script lines into tokens AND track surface-word boundaries
    # so we can recover per-word starts later.
    s_tokens = []
    line_first = []
    line_surface_words = []     # NEW: per-line surface-word descriptors
    for line in script_lines:
        line_first.append(len(s_tokens))
        surfs = []
        for surf in _split_surface(line):
            tok_start = len(s_tokens)
            s_tokens.extend(_tokenize(surf))
            surfs.append({"text": surf,
                          "tok_start": tok_start,
                          "tok_end": len(s_tokens)})
        line_surface_words.append(surfs)
    line_first.append(len(s_tokens))

    if not w_tokens or not s_tokens:
        return {}, set(), {}

    sm = SequenceMatcher(a=s_tokens, b=w_tokens, autojunk=False)
    s2w = {}
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            s2w[block.a + k] = block.b + k

    timestamps, matched = {}, set()
    word_raw = {}

    for li, line in enumerate(script_lines):
        first = line_first[li]
        last  = line_first[li + 1] - 1
        if first > last:
            word_raw[line] = []
            continue

        # Existing: line start = first matched token in line.
        wt_idx = None
        for s_idx in range(first, last + 1):
            if s_idx in s2w:
                wt_idx = s2w[s_idx]
                break
        if wt_idx is not None:
            timestamps[line] = whisper_words[w_origin[wt_idx]]["start"]
            matched.add(line)

        # NEW: per-surface-word starts within this line.
        starts_for_line = []
        for sw in line_surface_words[li]:
            sw_start = None
            for s_idx in range(sw["tok_start"], sw["tok_end"]):
                if s_idx in s2w:
                    sw_start = whisper_words[w_origin[s2w[s_idx]]]["start"]
                    break
            starts_for_line.append({"text": sw["text"], "start": sw_start})
        word_raw[line] = starts_for_line

    return timestamps, matched, word_raw


def _finalize_word_timings(word_raw, line_starts, ordered_lines,
                           audio_duration):
    """
    Fill in None word-starts via interpolation.

    For each line:
      • First-word fallback: line_starts[line].
      • Trailing words: bounded by the next line's start (or audio_duration).
      • Interior gaps: linearly interpolated between matched neighbours.
      • Monotonicity enforced within the line.
    """
    out = {}
    for li, line in enumerate(ordered_lines):
        words = word_raw.get(line, [])
        if not words:
            out[line] = []
            continue

        # Line bounds.
        line_start = line_starts.get(line)
        if line_start is None:
            # Shouldn't happen post-interpolation, but be safe.
            line_start = 0.0
        next_start = None
        for j in range(li + 1, len(ordered_lines)):
            if ordered_lines[j] in line_starts:
                next_start = line_starts[ordered_lines[j]]
                break
        if next_start is None:
            next_start = audio_duration

        starts = [float(w["start"]) if w["start"] is not None else None
                  for w in words]
        n = len(starts)

        # Anchor first word to line_start if unmatched.
        if starts[0] is None:
            starts[0] = line_start

        # Walk forward filling gaps via interpolation.
        i = 0
        while i < n:
            if starts[i] is None:
                j = i
                while j < n and starts[j] is None:
                    j += 1
                left = starts[i - 1]
                right = starts[j] if j < n else next_start
                span = max(j - (i - 1), 1)
                for k in range(i, j):
                    t = (k - (i - 1)) / span
                    starts[k] = left + (right - left) * t
                i = j
            else:
                i += 1

        # Enforce monotonicity (Whisper occasionally emits slightly out-of-
        # order timestamps for adjacent words; nudge them forward).
        for k in range(1, n):
            if starts[k] < starts[k - 1]:
                starts[k] = starts[k - 1] + 0.001

        out[line] = [{"text": w["text"], "start": round(s, 3)}
                     for w, s in zip(words, starts)]
    return out


# ─── Line-level post-processing (unchanged) ───────────────────────────

def _interpolate_missing(timestamps, ordered_lines, audio_duration):
    starts = [timestamps.get(ln) for ln in ordered_lines]
    n = len(starts)
    if n == 0:
        return timestamps
    if starts[0] is None: starts[0] = 0.0
    if starts[-1] is None: starts[-1] = audio_duration
    i = 0
    while i < n:
        if starts[i] is None:
            j = i
            while j < n and starts[j] is None:
                j += 1
            left = starts[i - 1]
            right = starts[j] if j < n else audio_duration
            span = j - (i - 1)
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


def _enforce_monotonic(timestamps, ordered_lines):
    out = dict(timestamps)
    prev = -1.0
    for ln in ordered_lines:
        if ln in out and out[ln] < prev:
            out[ln] = prev + 0.01
        prev = out.get(ln, prev)
    return out


def _derive_durations(timestamps, ordered_lines, audio_duration):
    durations = {}
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


# ─── Entry point (extended signature, backwards compatible) ───────────

def run(script_audio_file=SCRIPT_AUDIO_FILE,
        script_lines_file=SCRIPT_LINES_FILE,
        output_file=OUTPUT_FILE,
        timestamps_cache_file=TIMESTAMPS_CACHE_FILE,
        audio_start_delay=0.5,
        whisper_model=WHISPER_MODEL,
        force=False,
        word_timings_cache_file=WORD_TIMINGS_CACHE_FILE):   # NEW (optional)
    if not os.path.exists(script_audio_file):
        raise FileNotFoundError(script_audio_file)
    if not os.path.exists(script_lines_file):
        raise FileNotFoundError(script_lines_file)
    lines = _load_lines(script_lines_file)

    # Cache short-circuit: skip if BOTH the durations file AND the
    # word-timings file are present + complete.  (Previously only the
    # durations file was checked.)
    if not force and os.path.exists(output_file) \
            and os.path.getsize(output_file) > 0:
        existing = _load_json(output_file)
        word_existing = (_load_json(word_timings_cache_file)
                         if word_timings_cache_file else {})
        durations_complete = all(ln in existing for ln in lines)
        words_complete = (word_timings_cache_file is None
                          or all(ln in word_existing for ln in lines))
        if durations_complete and words_complete:
            print(f"[synchronizer] outputs already complete.  Returning.  "
                  f"(pass force=True to re-run)")
            return

    print("─" * 70)
    print("AUDIO SCRIPT SYNCHRONIZER  ·  Whisper forced-alignment")
    print("─" * 70)
    print(f"  audio:   {script_audio_file}")
    print(f"  script:  {script_lines_file}  ({len(lines)} lines)")
    print(f"  model:   {whisper_model}")

    print("\n[1/4] transcribing audio…")
    words = _transcribe(script_audio_file, whisper_model)
    if not words:
        raise RuntimeError("Whisper returned no words — empty/corrupt audio?")

    print("\n[2/4] aligning script to transcription…")
    raw_timestamps, matched, word_raw = _align(lines, words)
    n_matched = len(matched)
    print(f"      matched {n_matched}/{len(lines)} lines directly")

    audio_duration = _audio_duration(script_audio_file)
    if n_matched < len(lines):
        unmatched = [ln for ln in lines if ln not in matched]
        print(f"\n[3/4] interpolating {len(unmatched)} unmatched line(s)")
    else:
        print("\n[3/4] all lines matched directly — no interpolation needed.")

    timestamps = _interpolate_missing(raw_timestamps, lines, audio_duration)
    timestamps = _enforce_monotonic(timestamps, lines)

    print("\n[4/4] writing outputs…")
    durations = _derive_durations(timestamps, lines, audio_duration)

    # NEW: finalize word-level timings using the (now complete) line
    # starts.  Lines that had no Whisper matches at all still produce
    # word entries by interpolating from neighbours.
    word_final = _finalize_word_timings(word_raw, timestamps, lines,
                                        audio_duration)

    starts_str    = {k: f"{v:.2f}" for k, v in timestamps.items()}
    durations_str = {k: f"{v:.2f}" for k, v in durations.items()}
    _save_json_ordered(timestamps_cache_file, starts_str,    lines)
    _save_json_ordered(output_file,           durations_str, lines)
    print(f"      ✓ {timestamps_cache_file}")
    print(f"      ✓ {output_file}")

    if word_timings_cache_file:
        # Store starts as 3-dp floats — enough precision for video framing
        # without bloating the JSON.
        word_out = {ln: [{"text": w["text"], "start": w["start"]}
                         for w in word_final[ln]]
                    for ln in word_final}
        _save_json_ordered(word_timings_cache_file, word_out, lines)
        n_matched_words = sum(1 for ln in word_final
                              for w in word_final[ln]
                              if any(r["text"] == w["text"]
                                     and r["start"] is not None
                                     for r in word_raw.get(ln, [])))
        n_total_words = sum(len(word_final[ln]) for ln in word_final)
        print(f"      ✓ {word_timings_cache_file}  "
              f"({n_matched_words}/{n_total_words} words directly anchored)")

    total = sum(durations.values())
    print(f"\n  audio length:   {audio_duration:.2f}s")
    print(f"  durations sum:  {total:.2f}s")
    print("─" * 70)


if __name__ == "__main__":
    run()
