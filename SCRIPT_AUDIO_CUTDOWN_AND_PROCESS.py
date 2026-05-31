# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "faster-whisper>=1.0",   # alignment + bundled Silero VAD (ONNX, no torch)
#     "num2words>=0.5",
#     "soundfile>=0.12",       # WAV I/O via libsndfile — no ffmpeg, no audioop, 3.13-native
#     "numpy",
# ]
# ///
"""
SCRIPT_AUDIO_CUTDOWN_AND_PROCESS.py
===================================
Tightens a narration WAV: removes dead-air gaps and gives each SENTENCE END
a punchy transition — the next sentence fades in and is *layered over* the
tail of the previous one, so NONE of the previous sentence is lost.

No pydub. Audio is read/written with soundfile (libsndfile) and manipulated
as numpy arrays, so there's no `audioop` / ffmpeg dependency — it just works
on Python 3.13+.

How it decides where to cut
---------------------------
Two independent signals, each doing what it's best at:
  • WHERE the speech is  →  Silero VAD (a trained speech/non-speech model,
    bundled inside faster-whisper as an ONNX graph; the same VAD Whisper uses).
    Far more reliable than a loudness gate at finding the little silences
    *inside* a sentence without clipping soft syllables. Falls back to a
    simple energy gate if the VAD can't load.
  • WHICH gaps are sentence ends  →  forced alignment of your script to the
    Whisper transcription (same idea as AUDIO_SCRIPT_SYNCHRONIZER): each
    sentence is anchored to its own matched words, so a mistranscription on
    one line can't shift another.

The cut points come from measured speech, never from the loose word
timestamps, and every kept chunk carries a little real-audio padding, so
word edges (including the very last word) are preserved.

Outputs (into temp/ by default)
  - temp/<stem>.processed.wav    the tightened audio
  - temp/<stem>.sentences.json   [{index, text, start, end}] seconds-from-start

Run it
------
    uv run SCRIPT_AUDIO_CUTDOWN_AND_PROCESS.py        # uses the hardcoded inputs

All the dials you'll want are in the EASY KNOBS block right below.
"""

# ====================================================================
#                            EASY KNOBS
# ====================================================================

# -- inputs / outputs ------------------------------------------------
AUDIO_FILE   = "script-stickman.wav"
SCRIPT_FILE  = "script-stickman.txt"
OUTPUT_DIR   = "temp"
WHISPER_MODEL = "small.en"

# -- sentence-end transition -----------------------------------------
FADE_IN_MS            = 200   # the next sentence fades in over this long
OVERLAP_MS            = 300   # ...and starts this long BEFORE the previous one
                              #   ends (layered on top — previous is kept 100%)
MIN_CLIP_FOR_FADE_MS  = 800   # sentences shorter than this skip the fade/overlap
                              #   entirely and just play clean (e.g. "Yep...")

# -- how much silence to cut -----------------------------------------
NORMAL_GAP_MS         = 30   # ordinary gaps (incl. mid-sentence) shrink to this
SENTENCE_GAP_MS       = 30    # gap at a sentence end when we DON'T overlap
MIN_SILENCE_MS        = 100   # gaps shorter than this are left untouched
KEEP_PAD_MS           = 30    # real audio kept around each speech chunk
                              #   (protects word onsets/endings from clipping)
LEAD_IN_MS            = 40   # silence kept before the first word
TAIL_MS               = 120   # audio kept after the last word (keeps its decay)

# -- speech detection ------------------------------------------------
DETECTOR              = "vad"  # "vad" (Silero, recommended) | "energy"
VAD_THRESHOLD         = 0.8    # Silero speech probability (higher = stricter)
ENERGY_THRESH_DB      = -30.0  # energy mode: below this dBFS counts as silence

# -- matching tolerance (rarely need to touch) -----------------------
BOUNDARY_TOL_BEFORE_S = 0.30   # how far before a gap a sentence-end may sit
BOUNDARY_TOL_AFTER_S  = 0.15

# ====================================================================

import json
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np


# --- Punctuation normalisation + sentence split ----------------------
def _normalise_punct(text: str) -> str:
    text = text.replace("\u2026", "...")
    text = (text.replace("\u2018", "'").replace("\u2019", "'")
                .replace("\u201c", '"').replace("\u201d", '"'))
    text = re.sub(r"([.!?]+)(?=[A-Za-z])", r"\1 ", text)   # "gold.Nutmeg" -> "gold. Nutmeg"
    text = re.sub(r"[ \t]+", " ", text)
    return text


def _split_sentences(text: str) -> List[str]:
    text = _normalise_punct(text)
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


# --- Tokenisation (ported from AUDIO_SCRIPT_SYNCHRONIZER) -------------
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
                 if 1500 <= n <= 2099 else num2words(n, lang="en"))
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
    return [w for w in re.split(r"\s+", line.strip()) if w]


# --- Transcription (ported) ------------------------------------------
def _transcribe(audio_path, model_size):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError("faster-whisper not installed. "
                          "pip install faster-whisper num2words soundfile numpy")
    print(f"  -> loading Whisper model: {model_size}")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    print(f"  -> transcribing {audio_path}")
    segments, _ = model.transcribe(audio_path, word_timestamps=True,
                                   vad_filter=False)
    words = []
    for seg in segments:
        if seg.words is None:
            continue
        for w in seg.words:
            words.append({"word": w.word.strip(),
                          "start": float(w.start), "end": float(w.end)})
    print(f"  -> {len(words)} words returned")
    return words


# --- Sentence-level alignment -> per-sentence start/end --------------
def _align_sentences(sentences, whisper_words):
    w_tokens, w_origin = [], []
    for i, ww in enumerate(whisper_words):
        for t in _tokenize(ww["word"]):
            w_tokens.append(t)
            w_origin.append(i)

    s_tokens, spans = [], []
    for sent in sentences:
        start = len(s_tokens)
        for surf in _split_surface(sent):
            s_tokens.extend(_tokenize(surf))
        spans.append((start, len(s_tokens)))

    if not w_tokens or not s_tokens:
        return [{"text": s, "start": None, "end": None, "matched": False}
                for s in sentences]

    sm = SequenceMatcher(a=s_tokens, b=w_tokens, autojunk=False)
    s2w = {}
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            s2w[block.a + k] = block.b + k

    out = []
    for sent, (a, b) in zip(sentences, spans):
        first_w = last_w = None
        for s_idx in range(a, b):
            if s_idx in s2w:
                wi = w_origin[s2w[s_idx]]
                if first_w is None:
                    first_w = wi
                last_w = wi
        if first_w is None:
            out.append({"text": sent, "start": None, "end": None, "matched": False})
        else:
            out.append({"text": sent,
                        "start": whisper_words[first_w]["start"],
                        "end":   whisper_words[last_w]["end"],
                        "matched": True})
    return out


def _fill_missing(vals, total):
    vals = list(vals)
    n = len(vals)
    if n == 0:
        return vals
    if vals[0] is None:
        vals[0] = 0.0
    if vals[-1] is None:
        vals[-1] = total
    i = 0
    while i < n:
        if vals[i] is None:
            j = i
            while j < n and vals[j] is None:
                j += 1
            left = vals[i - 1]
            right = vals[j] if j < n else total
            span = j - (i - 1)
            for k in range(i, j):
                vals[k] = left + (right - left) * ((k - (i - 1)) / span)
            i = j
        else:
            i += 1
    return vals


# --- Speech detection ------------------------------------------------
def _detect_speech_vad(path, threshold, min_silence_ms,
                       min_speech_ms=120) -> List[Tuple[float, float]]:
    """Silero VAD via faster-whisper (ONNX, no torch). Returns [(start_s, end_s)]."""
    try:
        from faster_whisper import decode_audio
    except ImportError:
        from faster_whisper.audio import decode_audio
    from faster_whisper.vad import get_speech_timestamps, VadOptions

    audio = decode_audio(path, sampling_rate=16000)   # float32 mono @16k
    opts = VadOptions(threshold=threshold,
                      min_speech_duration_ms=min_speech_ms,
                      min_silence_duration_ms=min_silence_ms,
                      speech_pad_ms=0)                 # we add our own padding
    segs = get_speech_timestamps(audio, vad_options=opts, sampling_rate=16000)
    out = []
    for s in segs:
        a = s.get("start", s.get("begin"))
        b = s.get("end", s.get("stop"))
        out.append((a / 16000.0, b / 16000.0))
    return out


def _detect_speech_energy(mono, sr, thresh_db, min_silence_ms,
                          min_speech_ms=120) -> List[Tuple[float, float]]:
    """Simple windowed-RMS gate. Returns [(start_s, end_s)]."""
    win = max(1, int(sr * 0.02))                       # 20 ms frames
    n_frames = int(np.ceil(len(mono) / win))
    if n_frames == 0:
        return []
    pad = n_frames * win - len(mono)
    x = np.concatenate([mono, np.zeros(pad, dtype=mono.dtype)]) if pad else mono
    frames = x.reshape(n_frames, win).astype(np.float64)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    db = 20.0 * np.log10(rms + 1e-12)
    voiced = db > thresh_db

    segs = []
    in_sp, start = False, 0.0
    for i, v in enumerate(voiced):
        t1 = (i + 1) * win / sr
        if v and not in_sp:
            start, in_sp = i * win / sr, True
        elif not v and in_sp:
            segs.append([start, i * win / sr])
            in_sp = False
    if in_sp:
        segs.append([start, len(mono) / sr])

    min_sil = min_silence_ms / 1000.0
    merged = []
    for s, e in segs:
        if merged and s - merged[-1][1] < min_sil:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    min_sp = min_speech_ms / 1000.0
    return [(s, e) for s, e in merged if e - s >= min_sp]


# --- numpy audio helpers (replaces pydub) ----------------------------
def _fade_in(arr: np.ndarray, n: int) -> np.ndarray:
    n = min(n, len(arr))
    if n <= 0:
        return arr
    arr = arr.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32).reshape(-1, 1)
    arr[:n] *= ramp
    return arr


def _silent(n: int, ch: int) -> np.ndarray:
    return np.zeros((max(0, n), ch), dtype=np.float32)


def _overlay_extend(base: np.ndarray, b: np.ndarray, pos: int) -> np.ndarray:
    """Mix `b` into `base` starting at sample `pos`, extending `base` with
    zeros if `b` runs past the end. `base` is preserved in full (additive)."""
    end = pos + len(b)
    if end > len(base):
        base = np.concatenate([base, _silent(end - len(base), base.shape[1])], axis=0)
    else:
        base = base.copy()
    base[pos:end] += b
    return base


# --- Build the processed audio ---------------------------------------
@dataclass
class Params:
    fade_ms: int = FADE_IN_MS
    overlap_ms: int = OVERLAP_MS
    min_clip_ms: int = MIN_CLIP_FOR_FADE_MS
    normal_gap_ms: int = NORMAL_GAP_MS
    sentence_gap_ms: int = SENTENCE_GAP_MS
    keep_pad_ms: int = KEEP_PAD_MS
    lead_in_ms: int = LEAD_IN_MS
    tail_ms: int = TAIL_MS
    tol_before: float = BOUNDARY_TOL_BEFORE_S
    tol_after: float = BOUNDARY_TOL_AFTER_S


def _build(data: np.ndarray, sr: int,
           speech: List[Tuple[float, float]],
           sentence_end_times, P: Params):
    dur = len(data) / sr
    ch = data.shape[1]
    n = lambda t: int(max(0, min(len(data), round(t * sr))))
    ms = lambda x: int(round(x / 1000.0 * sr))
    stats = {"regions": 0, "transitions": 0, "gaps": 0}

    if not speech:
        return data.copy(), stats

    # Keep-regions: real audio around each speech segment. Outer edges use
    # LEAD_IN / TAIL; internal edges use KEEP_PAD. Then merge any overlaps.
    N = len(speech)
    K = []
    for i, (s, e) in enumerate(speech):
        lo = s - (P.lead_in_ms if i == 0 else P.keep_pad_ms) / 1000.0
        hi = e + (P.tail_ms if i == N - 1 else P.keep_pad_ms) / 1000.0
        K.append([max(0.0, lo), min(dur, hi)])
    merged = [K[0]]
    for s, e in K[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    stats["regions"] = len(merged)

    end_times = sorted(t for t in sentence_end_times if t is not None)

    def is_boundary(g0, g1):
        lo, hi = g0 - P.tol_before, g1 + P.tol_after
        return any(lo <= t <= hi for t in end_times)

    overlap_n = ms(P.overlap_ms)
    fade_n = ms(P.fade_ms)
    min_clip_n = ms(P.min_clip_ms)

    out = data[n(merged[0][0]):n(merged[0][1])].copy()
    prev_region_n = len(out)

    for i in range(1, len(merged)):
        a, b = merged[i]
        region = data[n(a):n(b)].copy()
        gap = a - merged[i - 1][1]
        boundary = is_boundary(merged[i - 1][1], a)

        long_enough = prev_region_n >= min_clip_n and len(region) >= min_clip_n
        if boundary and long_enough and len(out) >= overlap_n and len(region) > overlap_n:
            # Layer the next sentence (faded in) over the tail of everything so
            # far. The previous audio is kept 100% — this is an additive mix,
            # not a crossfade, so nothing is replaced or faded out.
            region = _fade_in(region, fade_n)
            out = _overlay_extend(out, region, len(out) - overlap_n)
            stats["transitions"] += 1
        else:
            target = P.sentence_gap_ms if boundary else P.normal_gap_ms
            gap_n = max(0, min(ms(gap * 1000), ms(target)))
            if gap_n:
                out = np.concatenate([out, _silent(gap_n, ch)], axis=0)
            out = np.concatenate([out, region], axis=0)
            stats["gaps"] += 1

        prev_region_n = len(region)

    return np.clip(out, -1.0, 1.0), stats


# --- Entry point -----------------------------------------------------
def run(audio_file: str = AUDIO_FILE,
        script_file: str = SCRIPT_FILE,
        output_dir: str = OUTPUT_DIR,
        whisper_model: str = WHISPER_MODEL,
        detector: str = DETECTOR,
        force: bool = False,
        **param_overrides):
    """Tighten `audio_file` against `script_file`. Any Params field can be
    overridden as a kwarg, e.g. run(overlap_ms=150, fade_ms=300)."""
    import soundfile as sf

    P = Params(**param_overrides)
    audio_path, script_path = Path(audio_file), Path(script_file)
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)
    if not script_path.exists():
        raise FileNotFoundError(script_path)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_wav = out_dir / f"{audio_path.stem}.processed.wav"
    out_json = out_dir / f"{audio_path.stem}.sentences.json"

    if not force and out_wav.exists() and out_wav.stat().st_size > 0 and out_json.exists():
        print(f"[cutdown] outputs already present: {out_wav}  (pass force=True to re-run)")
        return out_wav, out_json

    sentences = _split_sentences(script_path.read_text(encoding="utf-8"))

    print("-" * 70)
    print("SCRIPT AUDIO CUTDOWN & PROCESS")
    print("-" * 70)
    print(f"  audio:    {audio_path}")
    print(f"  script:   {script_path}  ({len(sentences)} sentences)")
    print(f"  model:    {whisper_model}")
    print(f"  detector: {detector}")

    print("\n[1/4] transcribing audio...")
    words = _transcribe(str(audio_path), whisper_model)
    if not words:
        raise RuntimeError("Whisper returned no words — empty/corrupt audio?")

    print("\n[2/4] aligning sentences to transcription...")
    aligned = _align_sentences(sentences, words)
    n_matched = sum(1 for a in aligned if a["matched"])
    print(f"      matched {n_matched}/{len(sentences)} sentence ends directly")
    sentence_end_times = [a["end"] for a in aligned if a["matched"]]

    print("\n[3/4] loading audio + detecting speech...")
    data, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
    dur = len(data) / sr

    used_detector = detector
    speech: List[Tuple[float, float]] = []
    if detector == "vad":
        try:
            speech = _detect_speech_vad(str(audio_path), VAD_THRESHOLD, MIN_SILENCE_MS)
            print(f"      Silero VAD found {len(speech)} speech segment(s)")
        except Exception as ex:
            used_detector = "energy"
            print(f"      ! VAD unavailable ({type(ex).__name__}: {ex}); "
                  f"falling back to energy gate")
    if not speech and used_detector != "vad":
        mono = data.mean(axis=1)
        speech = _detect_speech_energy(mono, sr, ENERGY_THRESH_DB, MIN_SILENCE_MS)
        print(f"      energy gate found {len(speech)} speech segment(s) "
              f"(thresh {ENERGY_THRESH_DB} dBFS)")
    if not speech:
        raise RuntimeError("No speech detected — check the audio / detector settings.")

    print("\n[4/4] cutting gaps + layering sentence transitions...")
    processed, stats = _build(data, sr, speech, sentence_end_times, P)

    # Write, preserving the original PCM subtype where we can.
    try:
        subtype = sf.info(str(audio_path)).subtype
        if subtype not in sf.available_subtypes("WAV"):
            subtype = "PCM_16"
    except Exception:
        subtype = "PCM_16"
    sf.write(str(out_wav), processed, sr, subtype=subtype)

    starts = _fill_missing([a["start"] for a in aligned], dur)
    ends = _fill_missing([a["end"] for a in aligned], dur)
    records = [{"index": i, "text": aligned[i]["text"],
                "start": round(starts[i], 3), "end": round(ends[i], 3),
                "matched": aligned[i]["matched"]} for i in range(len(aligned))]
    out_json.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    new_dur = len(processed) / sr
    print("\n" + "-" * 70)
    print(f"  detector used:         {used_detector}")
    print(f"  speech chunks kept:    {stats['regions']}")
    print(f"  sentence transitions:  {stats['transitions']}  "
          f"(fade {P.fade_ms}ms / layer {P.overlap_ms}ms)")
    print(f"  ordinary gaps shrunk:  {stats['gaps']}")
    print(f"  original length:       {dur:6.2f}s")
    print(f"  processed length:      {new_dur:6.2f}s   "
          f"({dur - new_dur:+.2f}s, {100 * (dur - new_dur) / dur:.1f}% removed)")
    print(f"  ok {out_wav}")
    print(f"  ok {out_json}")
    print("-" * 70)
    return out_wav, out_json


if __name__ == "__main__":
    run()
