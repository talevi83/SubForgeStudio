import pytest
from app.pipeline.context import RawSegment, TranscribedWord
from app.pipeline.stages.refine_stage import refine_raw_segments


def test_refine_empty_segments():
    assert refine_raw_segments([]) == []


def test_refine_single_short_segment():
    raw = [
        RawSegment(
            index=1,
            start_time=1.0,
            end_time=3.0,
            text="Short dialogue line.",
            confidence=0.95,
        )
    ]
    refined = refine_raw_segments(raw)
    assert len(refined) == 1
    assert refined[0]["index"] == 1
    assert refined[0]["start_time"] == 1.0
    assert refined[0]["end_time"] == 3.0
    assert refined[0]["original_text"] == "Short dialogue line."
    assert refined[0]["cps"] > 0


def test_refine_enforces_min_duration():
    raw = [
        RawSegment(
            index=1,
            start_time=1.0,
            end_time=1.2,  # Only 0.2s
            text="Hi.",
            confidence=0.99,
        )
    ]
    refined = refine_raw_segments(raw, min_duration=0.8)
    assert len(refined) == 1
    duration = refined[0]["end_time"] - refined[0]["start_time"]
    assert duration >= 0.8


def test_refine_splits_long_segment_with_word_timestamps():
    words = [
        TranscribedWord(word="We", start=0.0, end=0.4),
        TranscribedWord(word="need", start=0.5, end=0.8),
        TranscribedWord(word="to", start=0.9, end=1.1),
        TranscribedWord(word="secure", start=1.2, end=1.7),
        TranscribedWord(word="the", start=1.8, end=2.0),
        TranscribedWord(word="entire", start=2.1, end=2.6),
        TranscribedWord(word="perimeter", start=2.7, end=3.3),
        TranscribedWord(word="before", start=3.4, end=3.8),
        TranscribedWord(word="nightfall", start=3.9, end=4.5),
        TranscribedWord(word="because", start=4.6, end=5.1),
        TranscribedWord(word="the", start=5.2, end=5.3),
        TranscribedWord(word="hostile", start=5.4, end=5.9),
        TranscribedWord(word="forces", start=6.0, end=6.5),
        TranscribedWord(word="are", start=6.6, end=6.8),
        TranscribedWord(word="approaching", start=6.9, end=7.6),
        TranscribedWord(word="our", start=7.7, end=7.9),
        TranscribedWord(word="base", start=8.0, end=8.4),
    ]
    full_text = " ".join(w.word for w in words)
    raw = [
        RawSegment(
            index=1,
            start_time=0.0,
            end_time=8.4,
            text=full_text,
            words=words,
        )
    ]
    # Max line is 42 chars -> block max is 84 chars
    refined = refine_raw_segments(raw, max_chars_per_line=42)
    assert len(refined) >= 2
    for seg in refined:
        assert seg["end_time"] > seg["start_time"]
        assert len(seg["original_text"]) > 0


def test_refine_prevents_overlap():
    raw = [
        RawSegment(index=1, start_time=1.0, end_time=2.5, text="First line"),
        RawSegment(index=2, start_time=2.0, end_time=3.5, text="Second line"),
    ]
    refined = refine_raw_segments(raw)
    assert len(refined) == 2
    assert refined[1]["start_time"] >= refined[0]["end_time"]
