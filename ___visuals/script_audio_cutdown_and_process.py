# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "faster-whisper>=1.0",
#     "num2words>=0.5",
#     "pydub>=0.25",
#     "audioop-lts<1.0; python_version >= '3.13'",  # pydub uses stdlib `audioop`, removed in 3.13 (PEP 594); this backports it
# ]
# ///
"""
script_audio_cutdown_and_process.py
===================================
Tightens a narration WAV: removes the long dead-air gaps and gives every
SENTENCE END a punchy transition (the next sentence overlaps the tail of the
previous one and fades in across that overlap -- a crossfade).

WHY WERE WORDS GETTING CUT? (e.g. "British" -> "Brit")
------------------------------------------------------
It was NOT the crossfade -- the proof is that setting the crossfade to 0 still
clipped the word. The real cause is the SILENCE DETECTOR: the soft "-ish" tail
of "British" is low energy, so the energy gate counts it as silence and ends
the chunk at "Brit". A small trailing pad only recovered part of the fricative.

THE FIX: a generous TRAILING pad (TAIL_PAD_MS) that keeps the word's decay no
matter where the detector drew the line. As a bonus, this also means the
crossfade now lands on that quiet trailing pad instead of on the word itself,
so the blend no longer eats word cores either.

Padding is ASYMMETRIC: a small LEAD_PAD_MS before each phrase (onset safety)
and a large TAIL_PAD_MS after it (preserve word endings). As long as
LEAD_PAD_MS + TAIL_PAD_MS < MIN_SILENCE_LEN_MS, separate sentences never merge.

How it decides where to cut
---------------------------
  - WHERE the speech is  -> pydub silence detection (energy-based). Decides the
    actual cut points so a cut never lands mid-word (with the pads above).
  - WHICH gaps are sentence ends -> forced alignment of the script to the
    Whisper transcription (same SequenceMatcher idea as
    audio_script_synchronizer, reading each sentence's END time). A gap is a
    "sentence transition" only if a sentence-end time falls in it.

Outputs (into temp/ by default)
  - temp/<stem>.processed.wav    the tightened audio
  - temp/<stem>.sentences.json   [{index, text, start, end, matched}] seconds

Run it
------
    uv run script_audio_cutdown_and_process.py        # uses the hardcoded inputs

`uv run` auto-installs the deps above (incl. audioop-lts so pydub works on
3.13). pydub also needs the system `ffmpeg` binary, which you already have.
"""

from __future__ import annotations

# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

# ====================================================================
#                            EASY KNOBS
# ====================================================================

# -- inputs / outputs ------------------------------------------------
AUDIO_FILE    = "script-stickman.wav"
SCRIPT_FILE   = "script-stickman.txt"
OUTPUT_DIR    = "temp"
WHISPER_MODEL = "small.en"

# -- WORD PRESERVATION (start here if words are being clipped) -------
# Trailing audio kept after each detected phrase. This is THE knob that
# stops word endings ("British", "nutmeg") being cut. Bigger = safer words,
# but keep TAIL_PAD_MS + LEAD_PAD_MS < MIN_SILENCE_LEN_MS (below) or adjacent
# sentences will merge together.
TAIL_PAD_MS           = 111
LEAD_PAD_MS           = 30    # audio kept BEFORE each phrase (onset safety) 60, 165...
END_PAD_MS            = 370   # extra trailing audio on the VERY LAST word
SILENCE_THRESH_OFFSET_DB = -19.5  # lower (more negative) keeps quieter tails as
                                  # speech; raise toward 0 if too little is cut

# -- sentence-end transition -----------------------------------------
SENTENCE_MODE         = "crossfade"  # "crossfade" (overlap+blend) | "fade" (no overlap)
SENTENCE_CROSSFADE_MS = 165   # overlap/blend length. KEEP THIS <= TAIL_PAD_MS so the
                              # blend lands on the trailing pad, not on the word.
SENTENCE_FADE_MS      = 400   # "fade" mode only: fade-in length, no overlap (outgoing kept 100%)
MIN_CLIP_FOR_FADE_MS  = 700   # sentences shorter than this are NEVER crossfaded
                              #   (they play clean with a small gap) -> protects "Yep..."

# -- how much silence to cut -----------------------------------------
MIN_SILENCE_LEN_MS    = 142   # only gaps LONGER than this are cut (lower => also
                              #   tightens within-sentence pauses)
NORMAL_GAP_MS         = 100   # ordinary gaps shrink to this (capped; never grown)
SENTENCE_GAP_MS       = 70    # gap at a sentence end we don't crossfade (e.g. short clip)
LEAD_IN_MS            = 150   # leading silence kept before the very first word
MIN_BOUNDARY_GAP_MS   = 80    # don't "transition" near-continuous speech
EDGE_FADE_MS          = 4     # tiny anti-click fade on every chunk edge

# -- silence threshold (advanced) ------------------------------------
SILENCE_THRESH_DB     = None  # absolute dBFS; None => relative (audio.dBFS + offset above)
SEEK_STEP_MS          = 5

# -- matching tolerance (rarely need to touch) -----------------------
BOUNDARY_TOL_BEFORE_S = 0.45
BOUNDARY_TOL_AFTER_S  = 0.20

# ====================================================================

import json
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional


# --- Punctuation normalisation (conservative) ------------------------
def _normalise_punct(text: str) -> str:
    text = text.replace("\u2026", "...")
    text = (text.replace("\u2018", "'").replace("\u2019", "'")
                .replace("\u201c", '"').replace("\u201d", '"'))
    text = re.sub(r"([.!?]+)(?=[A-Za-z])", r"\1 ", text)   # "gold.Nutmeg" -> "gold. Nutmeg"
    text = re.sub(r"[ \t]+", " ", text)
    return text


def _split_sentences(text: str):
    text = _normalise_punct(text)
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


# --- Tokenisation (ported from audio_script_synchronizer) -------------
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


# --- Transcription (ported from audio_script_synchronizer) ------------
def _transcribe(audio_path, model_size):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError("faster-whisper not installed. "
                          "pip install faster-whisper num2words pydub")
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


# --- Sentence-level alignment ----------------------------------------
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


# --- Audio processing ------------------------------------------------
@dataclass
class Params:
    # silence detection
    min_silence_len_ms: int = MIN_SILENCE_LEN_MS
    silence_thresh_db: Optional[float] = SILENCE_THRESH_DB
    silence_thresh_offset_db: float = SILENCE_THRESH_OFFSET_DB
    seek_step_ms: int = SEEK_STEP_MS
    # word-preservation padding (asymmetric)
    lead_pad_ms: int = LEAD_PAD_MS
    tail_pad_ms: int = TAIL_PAD_MS
    end_pad_ms: int = END_PAD_MS
    # gap targets
    normal_gap_ms: int = NORMAL_GAP_MS
    sentence_gap_ms: int = SENTENCE_GAP_MS
    lead_in_ms: int = LEAD_IN_MS
    edge_fade_ms: int = EDGE_FADE_MS
    # sentence-end transition
    sentence_mode: str = SENTENCE_MODE
    sentence_crossfade_ms: int = SENTENCE_CROSSFADE_MS
    sentence_fade_ms: int = SENTENCE_FADE_MS
    min_clip_for_fade_ms: int = MIN_CLIP_FOR_FADE_MS
    min_boundary_gap_ms: int = MIN_BOUNDARY_GAP_MS
    # how close a sentence END must be to a silence window to count
    boundary_tol_before: float = BOUNDARY_TOL_BEFORE_S
    boundary_tol_after: float = BOUNDARY_TOL_AFTER_S


def _clip(audio, s_ms, e_ms, edge_fade_ms):
    seg = audio[int(s_ms):int(e_ms)]
    f = min(edge_fade_ms, len(seg) // 2)
    if f > 0:
        seg = seg.fade_in(f).fade_out(f)
    return seg


def _process_audio(audio, sentence_end_times, p: Params):
    """Return (processed_audio, stats_dict)."""
    from pydub import AudioSegment
    from pydub.silence import detect_nonsilent

    if p.silence_thresh_db is not None:
        thresh = p.silence_thresh_db
    else:
        base = audio.dBFS
        if base == float("-inf"):
            base = -50.0
        thresh = base + p.silence_thresh_offset_db

    nonsilent = detect_nonsilent(
        audio,
        min_silence_len=p.min_silence_len_ms,
        silence_thresh=thresh,
        seek_step=p.seek_step_ms,
    )
    stats = {"chunks": len(nonsilent), "boundaries": 0, "short_boundaries": 0,
             "normal_gaps": 0, "thresh_db": round(thresh, 1),
             "merged_pairs": 0}
    if not nonsilent:
        print("  ! no silence detected -- returning audio unchanged "
              "(raise silence_thresh_offset_db toward 0, e.g. -12)")
        return audio, stats

    # Pad each phrase ASYMMETRICALLY: a little before (onset), a lot after
    # (so soft word endings like the "-ish" of "British" are never clipped).
    # Then merge any ranges the padding caused to touch.
    padded = [[max(0, s - p.lead_pad_ms), min(len(audio), e + p.tail_pad_ms)]
              for s, e in nonsilent]
    merged = [padded[0]]
    for s, e in padded[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
            stats["merged_pairs"] += 1
        else:
            merged.append([s, e])

    # Even more trailing audio on the very last word's decay.
    extra_tail = max(0, p.end_pad_ms - p.tail_pad_ms)
    merged[-1][1] = min(len(audio), merged[-1][1] + extra_tail)

    end_times = sorted(t for t in sentence_end_times if t is not None)

    def _is_boundary(g0_ms, g1_ms):
        g0, g1 = g0_ms / 1000.0, g1_ms / 1000.0
        lo, hi = g0 - p.boundary_tol_before, g1 + p.boundary_tol_after
        return any(lo <= t <= hi for t in end_times)

    # First chunk (keep a little lead-in silence).
    first_s, first_e = merged[0]
    lead = min(first_s, p.lead_in_ms)
    out = _clip(audio, first_s - lead, first_e, p.edge_fade_ms)
    prev_end = first_e
    prev_chunk_len = len(out)

    for s, e in merged[1:]:
        chunk = _clip(audio, s, e, p.edge_fade_ms)
        gap_ms = s - prev_end
        boundary = gap_ms >= p.min_boundary_gap_ms and _is_boundary(prev_end, s)
        long_enough = (prev_chunk_len >= p.min_clip_for_fade_ms
                       and len(chunk) >= p.min_clip_for_fade_ms)

        if boundary and long_enough:
            # Punchy transition. Because each chunk now ends with TAIL_PAD_MS of
            # quiet decay, a crossfade <= TAIL_PAD_MS blends that pad, not the word.
            if p.sentence_mode == "fade":
                # No overlap -> the outgoing sentence is kept 100%.
                out += chunk.fade_in(p.sentence_fade_ms)
            else:
                cf = min(p.sentence_crossfade_ms, len(out) - 1, len(chunk) - 1)
                if cf > 0:
                    out = out.append(chunk, crossfade=cf)
                else:
                    out += chunk
            stats["boundaries"] += 1
        elif boundary:
            # Sentence end, but a short clip is involved -> do NOT crossfade
            # (it would gut it). Play it clean with a small gap.
            if p.sentence_gap_ms > 0:
                out += AudioSegment.silent(duration=p.sentence_gap_ms)
            out += chunk
            stats["short_boundaries"] += 1
        else:
            target = max(0, min(gap_ms, p.normal_gap_ms))
            if target:
                out += AudioSegment.silent(duration=target)
            out += chunk
            stats["normal_gaps"] += 1

        prev_end = e
        prev_chunk_len = len(chunk)

    return out, stats


# --- Entry point -----------------------------------------------------
def run(audio_file: str = AUDIO_FILE,
        script_file: str = SCRIPT_FILE,
        output_dir: str = OUTPUT_DIR,
        whisper_model: str = WHISPER_MODEL,
        force: bool = False,
        **param_overrides):
    """Process `audio_file` against `script_file`, writing the tightened WAV
    and the sentence-timing JSON into `output_dir`.

    Any Params field can be overridden as a keyword arg, e.g.
        run(tail_pad_ms=260, sentence_crossfade_ms=180)
        run(sentence_mode="fade")        # keep outgoing 100%, fade incoming in
    """
    from pydub import AudioSegment

    p = Params(**param_overrides)

    audio_path = Path(audio_file)
    script_path = Path(script_file)
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)
    if not script_path.exists():
        raise FileNotFoundError(script_path)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_wav = out_dir / f"{audio_path.stem}.processed.wav"
    out_json = out_dir / f"{audio_path.stem}.sentences.json"

    if not force and out_wav.exists() and out_wav.stat().st_size > 0 \
            and out_json.exists():
        print(f"[cutdown] outputs already present: {out_wav}  "
              f"(pass force=True to re-run)")
        return out_wav, out_json

    text = script_path.read_text(encoding="utf-8")
    sentences = _split_sentences(text)

    # Sanity: warn if pads can merge separate sentences.
    if p.lead_pad_ms + p.tail_pad_ms >= p.min_silence_len_ms:
        print(f"  ! note: LEAD_PAD_MS+TAIL_PAD_MS ({p.lead_pad_ms}+{p.tail_pad_ms}) "
              f">= MIN_SILENCE_LEN_MS ({p.min_silence_len_ms}); close sentences may merge")

    print("-" * 70)
    print("SCRIPT AUDIO CUTDOWN & PROCESS")
    print("-" * 70)
    print(f"  audio:   {audio_path}")
    print(f"  script:  {script_path}  ({len(sentences)} sentences)")
    print(f"  model:   {whisper_model}")
    print(f"  pads:    lead {p.lead_pad_ms}ms / tail {p.tail_pad_ms}ms / end {p.end_pad_ms}ms")
    print(f"  mode:    {p.sentence_mode}  (crossfade {p.sentence_crossfade_ms}ms, "
          f"short-clip guard {p.min_clip_for_fade_ms}ms)")

    print("\n[1/4] transcribing audio...")
    words = _transcribe(str(audio_path), whisper_model)
    if not words:
        raise RuntimeError("Whisper returned no words -- empty/corrupt audio?")

    print("\n[2/4] aligning sentences to transcription...")
    aligned = _align_sentences(sentences, words)
    n_matched = sum(1 for a in aligned if a["matched"])
    print(f"      matched {n_matched}/{len(sentences)} sentence ends directly")
    if n_matched < len(sentences):
        for i, a in enumerate(aligned):
            if not a["matched"]:
                preview = (a["text"][:60] + "...") if len(a["text"]) > 60 else a["text"]
                print(f"        . unmatched sentence {i}: {preview!r}")

    sentence_end_times = [a["end"] for a in aligned if a["matched"]]

    print("\n[3/4] loading audio + detecting silence...")
    audio = AudioSegment.from_file(str(audio_path))
    audio_dur = len(audio) / 1000.0

    print("\n[4/4] cutting gaps + applying sentence transitions...")
    processed, stats = _process_audio(audio, sentence_end_times, p)
    processed.export(str(out_wav), format="wav")

    starts = _fill_missing([a["start"] for a in aligned], audio_dur)
    ends = _fill_missing([a["end"] for a in aligned], audio_dur)
    records = [{"index": i, "text": aligned[i]["text"],
                "start": round(starts[i], 3), "end": round(ends[i], 3),
                "matched": aligned[i]["matched"]} for i in range(len(aligned))]
    out_json.write_text(json.dumps(records, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    new_dur = len(processed) / 1000.0
    print("\n" + "-" * 70)
    print(f"  silence threshold:     {stats['thresh_db']} dBFS")
    print(f"  phrases kept:          {stats['chunks']}")
    print(f"  sentence transitions:  {stats['boundaries']}  (crossfaded)")
    print(f"  short-clip boundaries: {stats['short_boundaries']}  (played clean)")
    print(f"  ordinary gaps shrunk:  {stats['normal_gaps']}")
    if stats["merged_pairs"]:
        print(f"  phrases merged by pad: {stats['merged_pairs']}")
    print(f"  original length:       {audio_dur:6.2f}s")
    print(f"  processed length:      {new_dur:6.2f}s   "
          f"({audio_dur - new_dur:+.2f}s, "
          f"{100 * (audio_dur - new_dur) / audio_dur:.1f}% removed)")
    print(f"  ok {out_wav}")
    print(f"  ok {out_json}")
    print("-" * 70)
    return out_wav, out_json


if __name__ == "__main__":
    run()
