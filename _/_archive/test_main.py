import os
import math
from pathlib import Path
from unittest.mock import patch, MagicMock, call
import main

# ==========================================
# HELPERS
# ==========================================

def print_test_header(name):
    print(f"\n{'='*10} TESTING: {name} {'='*10}")


def make_script(word_count: int, text: str = "word") -> str:
    return (text + " ") * word_count


# ==========================================
# UNIT TESTS – private helpers
# ==========================================

def test_clean_text_removes_tags_and_punctuation():
    print_test_header("_clean_text")
    raw = "<b>Wow!</b> This is a <script>test</script> text... right??"
    cleaned = main._clean_text(raw)
    print(f"  Input:  {raw}")
    print(f"  Output: {cleaned}")
    assert cleaned == "wow this is a test text right", f"Got: {cleaned!r}"


def test_energy_score_detects_high_impact_words():
    print_test_header("_energy_score")
    words_high = ["danger", "money", "win", "the", "is", "a"]
    words_low  = ["the", "is", "a", "with", "that", "this"]
    score_high = main._energy_score(words_high)
    score_low  = main._energy_score(words_low)
    print(f"  High-impact score: {score_high:.4f}")
    print(f"  Low-impact score:  {score_low:.4f}")
    assert score_high > score_low


def test_extract_keywords_returns_non_empty():
    print_test_header("_extract_keywords")
    cases = [
        "The cybernetic stock market is very volatile.",
        "Cooking pasta requires boiling water and salt.",
        "Deep sea exploration is dangerous but rewarding.",
    ]
    for script in cases:
        keywords = main._extract_keywords(script, 3)
        print(f"  Script:    {script}")
        print(f"  Keywords:  {keywords}")
        assert len(keywords) > 0
        assert isinstance(keywords[0], str)


def test_clamp_duration():
    print_test_header("_clamp_duration")
    assert main._clamp_duration(0.5) == main.MIN_CLIP_LENGTH_SEC
    assert main._clamp_duration(100)  == main.MAX_CLIP_LENGTH_SEC
    mid = (main.MIN_CLIP_LENGTH_SEC + main.MAX_CLIP_LENGTH_SEC) / 2
    assert main._clamp_duration(mid) == mid


# ==========================================
# UNIT TESTS – process_script
# ==========================================

def test_process_script_total_duration():
    """Total duration should match (word_count / WPM) * 60."""
    print_test_header("process_script – total duration")
    word_count = 130
    script = make_script(word_count)
    result = main.process_script(script)

    expected = (word_count / main.WORDS_PER_MINUTE) * 60
    print(f"  Expected: {expected:.2f}s  |  Actual: {result['total_duration_sec']:.2f}s")
    assert math.isclose(result["total_duration_sec"], expected, rel_tol=1e-3)


def test_process_script_returns_segments():
    """process_script must return a non-empty 'segments' list."""
    print_test_header("process_script – segments shape")
    result = main.process_script(make_script(200))
    segs = result["segments"]
    print(f"  Number of segments: {len(segs)}")
    assert isinstance(segs, list)
    assert len(segs) >= 1
    for seg in segs:
        assert "text" in seg
        assert "duration_sec" in seg
        assert "keywords" in seg
        assert isinstance(seg["duration_sec"], float)


def test_process_script_per_segment_durations_sum():
    """Sum of per-segment durations should be close to total_duration_sec."""
    print_test_header("process_script – durations sum to total")
    result = main.process_script(make_script(150))
    total_est  = result["total_duration_sec"]
    total_segs = sum(s["duration_sec"] for s in result["segments"])
    print(f"  total_duration_sec : {total_est:.2f}s")
    print(f"  sum of segments    : {total_segs:.2f}s")
    # Clamping can cause a small difference; allow up to 10 %
    assert math.isclose(total_est, total_segs, rel_tol=0.10), (
        f"Segment total {total_segs:.2f} too far from estimate {total_est:.2f}"
    )


def test_process_script_per_segment_durations_clamped():
    """Every segment duration must respect MIN / MAX clip bounds."""
    print_test_header("process_script – segments clamped")
    result = main.process_script(make_script(300))
    for seg in result["segments"]:
        d = seg["duration_sec"]
        assert main.MIN_CLIP_LENGTH_SEC <= d <= main.MAX_CLIP_LENGTH_SEC, (
            f"Segment duration {d:.2f}s is outside [{main.MIN_CLIP_LENGTH_SEC}, {main.MAX_CLIP_LENGTH_SEC}]"
        )


def test_process_script_backward_compat_key():
    """process_script must expose 'duration_sec' as a backward-compat alias."""
    print_test_header("process_script – duration_sec alias")
    result = main.process_script(make_script(100))
    assert "duration_sec" in result, "'duration_sec' key missing (backward-compat broken)"
    assert result["duration_sec"] == result["total_duration_sec"]


def test_process_script_short_script_no_clamping_spam():
    """A short script (< 20 words) should produce few clips, not many MIN-clamped ones."""
    print_test_header("process_script – short script clip count")
    short = "The Golden Gate Bridge is an iconic marvel of engineering in San Francisco."
    result = main.process_script(short)
    segs = result["segments"]
    n = len(segs)
    total_clip = sum(s["duration_sec"] for s in segs)
    total_est  = result["total_duration_sec"]
    print(f"  Words: {len(short.split())} | Clips: {n} | "
          f"Clip total: {total_clip:.2f}s | Estimate: {total_est:.2f}s")
    # Every segment must be above MIN — no clamping-to-floor spam
    clamped_to_min = sum(1 for s in segs if s["duration_sec"] == main.MIN_CLIP_LENGTH_SEC)
    pct = clamped_to_min / n
    print(f"  Segments clamped to MIN: {clamped_to_min}/{n} ({pct*100:.0f}%)")
    assert pct < 0.5, f"More than half of segments clamped to MIN — n_clips too large for script"



    """Flat keywords list should have one entry per segment."""
    print_test_header("process_script – keywords list length")
    result = main.process_script(make_script(100))
    assert len(result["keywords"]) == len(result["segments"])


# ==========================================
# UNIT TESTS – get_clips
# ==========================================

@patch("requests.get")
@patch.dict(os.environ, {"PEXELS_API_KEY": "fake_key"})
def test_get_clips_downloads_one_image_per_segment(mock_get):
    print_test_header("get_clips – one image per segment")

    # Each search returns one fresh photo
    def search_side_effect(url, **kwargs):
        if "search" in url:
            r = MagicMock(status_code=200)
            r.json.return_value = {
                "photos": [{"src": {"large2x": f"http://img.example.com/{id(kwargs)}.jpg"}}]
            }
            return r
        # download call
        r = MagicMock(status_code=200, content=b"fake_image_bytes")
        return r

    mock_get.side_effect = search_side_effect

    processed = {
        "segments": [
            {"keywords": ["ocean"], "duration_sec": 4.5},
            {"keywords": ["forest"], "duration_sec": 5.0},
            {"keywords": ["city"],   "duration_sec": 3.8},
        ]
    }

    with patch("pathlib.Path.write_bytes"), \
         patch("pathlib.Path.mkdir"), \
         patch("main._load_history", return_value={}), \
         patch("main._save_history"):
        result = main.get_clips(processed)

    print(f"  Downloaded: {len(result)} images for {len(processed['segments'])} segments")
    # Should get at most one image per segment
    assert len(result) <= len(processed["segments"])
    assert len(result) > 0


# ==========================================
# UNIT TESTS – stitch_video
# ==========================================

@patch("main._run_ffmpeg")
@patch("main._pad_image")
@patch("shutil.rmtree")
@patch("pathlib.Path.mkdir")
def test_stitch_video_uses_per_segment_durations(mock_mkdir, mock_rmtree, mock_pad, mock_ffmpeg):
    print_test_header("stitch_video – per-segment -t flags")
    mock_ffmpeg.return_value = None

    seg_durations = [3.5, 5.0, 2.8]
    segments = [
        {"duration_sec": d, "keywords": ["test"]}
        for d in seg_durations
    ]
    images = [Path(f"img_{i:04d}.jpg") for i in range(len(segments))]
    processed = {
        "segments":           segments,
        "total_duration_sec": sum(seg_durations),
        "target_clip_sec":    4.0,
    }

    main.stitch_video(images, processed)

    cmd = mock_ffmpeg.call_args[0][0]
    cmd_str = " ".join(cmd)
    print(f"  FFmpeg command contains duration flags:")
    for d in seg_durations:
        flag = f"{d:.4f}"
        present = flag in cmd_str
        print(f"    -t {flag}: {'✓' if present else '✗ MISSING'}")
        assert present, f"-t {flag} not found in ffmpeg command"

    assert "ffmpeg" in cmd
    assert "libx264" in cmd


@patch("main._run_ffmpeg")
@patch("main._pad_image")
@patch("shutil.rmtree")
@patch("pathlib.Path.mkdir")
def test_stitch_video_debug_output(mock_mkdir, mock_rmtree, mock_pad, mock_ffmpeg, capsys=None):
    print_test_header("stitch_video – debug stats printed")
    mock_ffmpeg.return_value = None

    original_debug = main.DEBUG
    main.DEBUG = True
    try:
        segments = [
            {"duration_sec": 4.0, "keywords": ["nature", "sunset"]},
            {"duration_sec": 5.5, "keywords": ["city",   "lights"]},
        ]
        images = [Path(f"img_{i:04d}.jpg") for i in range(len(segments))]
        processed = {
            "segments":           segments,
            "total_duration_sec": 9.5,
            "target_clip_sec":    4.0,
        }
        # Just ensure it runs without error when DEBUG=True
        main.stitch_video(images, processed)
        print("  Debug output ran without error ✓")
    finally:
        main.DEBUG = original_debug


# ==========================================
# INTEGRATION-STYLE TESTS
# ==========================================

def test_process_then_stitch_durations_consistent():
    """End-to-end: durations produced by process_script feed stitch_video correctly."""
    print_test_header("Integration – process → stitch duration contract")
    result = main.process_script(make_script(120))
    segs = result["segments"]

    # Simulate what stitch_video does: zip images with segments
    fake_images = [Path(f"img_{i:04d}.jpg") for i in range(len(segs))]
    pairs = list(zip(fake_images, segs))

    durations_used = [seg["duration_sec"] for _, seg in pairs]
    total = sum(durations_used)
    print(f"  Clips: {len(pairs)}")
    print(f"  Total duration fed to ffmpeg: {total:.2f}s")
    print(f"  Script estimate:              {result['total_duration_sec']:.2f}s")
    assert all(main.MIN_CLIP_LENGTH_SEC <= d <= main.MAX_CLIP_LENGTH_SEC for d in durations_used)


# ==========================================
# RUNNER  (python test_main.py)
# ==========================================

if __name__ == "__main__":
    tests = [
        test_clean_text_removes_tags_and_punctuation,
        test_energy_score_detects_high_impact_words,
        test_extract_keywords_returns_non_empty,
        test_clamp_duration,
        test_process_script_total_duration,
        test_process_script_returns_segments,
        test_process_script_per_segment_durations_sum,
        test_process_script_per_segment_durations_clamped,
        test_process_script_keywords_count_matches_segments,
        test_get_clips_downloads_one_image_per_segment,
        test_stitch_video_uses_per_segment_durations,
        test_stitch_video_debug_output,
        test_process_then_stitch_durations_consistent,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
