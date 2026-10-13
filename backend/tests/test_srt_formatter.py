import pytest
from app.utils.srt_formatter import (
    format_srt_timestamp,
    format_vtt_timestamp,
    split_long_lines,
    serialize_to_srt,
    serialize_to_vtt,
    calculate_cps,
)
from app.utils.rtl_fixer import RLM


def test_format_srt_timestamp():
    assert format_srt_timestamp(0.0) == "00:00:00,000"
    assert format_srt_timestamp(65.5) == "00:01:05,500"
    assert format_srt_timestamp(3661.123) == "01:01:01,123"


def test_format_vtt_timestamp():
    assert format_vtt_timestamp(0.0) == "00:00:00.000"
    assert format_vtt_timestamp(65.5) == "00:01:05.500"
    assert format_vtt_timestamp(3661.123) == "01:01:01.123"


def test_split_long_lines_short_unchanged():
    short = "This is a short line."
    assert split_long_lines(short, max_chars_per_line=42) == short


def test_split_long_lines_splits_balanced():
    long_line = "We need to secure the perimeter before nightfall because the enemies are approaching fast."
    result = split_long_lines(long_line, max_chars_per_line=42)
    lines = result.split("\n")
    assert len(lines) == 2
    # Ensure both parts together contain all words
    assert " ".join(lines) == long_line


def test_split_long_lines_preserves_existing_newline():
    two_lines = "Line one.\nLine two."
    assert split_long_lines(two_lines, max_chars_per_line=42) == two_lines


def test_calculate_cps():
    # 20 chars in 2 seconds = 10.0 CPS
    text = "12345678901234567890"
    assert calculate_cps(text, 2.0) == 10.0


def test_serialize_to_srt_original():
    segments = [
        {
            "start_time": 1.0,
            "end_time": 3.5,
            "original_text": "Hello world!",
            "translated_text": "שלום עולם!",
        }
    ]
    srt = serialize_to_srt(segments, use_translated=False, apply_rtl=False)
    expected = "1\n00:00:01,000 --> 00:00:03,500\nHello world!\n"
    assert srt.strip() == expected.strip()


def test_serialize_to_srt_translated_with_rtl():
    segments = [
        {
            "start_time": 1.0,
            "end_time": 3.5,
            "original_text": "Hello world!",
            "translated_text": "שלום עולם!",
        }
    ]
    srt = serialize_to_srt(segments, use_translated=True, apply_rtl=True)
    expected_text = f"{RLM}שלום עולם!{RLM}"
    expected = f"1\n00:00:01,000 --> 00:00:03,500\n{expected_text}\n"
    assert srt.strip() == expected.strip()


def test_serialize_to_vtt():
    segments = [
        {
            "start_time": 1.0,
            "end_time": 3.5,
            "original_text": "Hello world!",
        }
    ]
    vtt = serialize_to_vtt(segments, use_translated=False, apply_rtl=False)
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:01.000 --> 00:00:03.500" in vtt
    assert "Hello world!" in vtt
