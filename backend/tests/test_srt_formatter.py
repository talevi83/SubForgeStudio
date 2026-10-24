import pytest
from app.utils.srt_formatter import (
    format_srt_timestamp,
    format_vtt_timestamp,
    split_long_lines,
    serialize_to_srt,
    serialize_to_vtt,
    calculate_cps,
)
from app.utils.rtl_fixer import RLM, RLE, PDF


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
    expected_text = f"{RLE}{RLM}שלום עולם!{RLM}{PDF}"
    expected = f"1\n00:00:01,000 --> 00:00:03,500\n{expected_text}\n"
    assert srt.strip() == expected.strip()


def test_serialize_to_srt_mixed_hebrew_and_english():
    segments = [
        {
            "start_time": 1.0,
            "end_time": 3.5,
            "original_text": "GPR data",
            "translated_text": "ששיבש את נתוני ה-GPR של יאן.",
        }
    ]
    srt = serialize_to_srt(segments, use_translated=True, apply_rtl=True)
    expected_text = f"{RLE}{RLM}ששיבש את נתוני ה-GPR של יאן.{RLM}{PDF}"
    assert expected_text in srt


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


def test_serialize_onscreen_top_position_in_srt_and_vtt():
    segments = [
        {
            "start_time": 10.0,
            "end_time": 14.0,
            "original_text": "[One week later...]",
            "translated_text": "[שבוע לאחר מכן...]",
            "segment_type": "ON_SCREEN",
        },
        {
            "start_time": 11.0,
            "end_time": 13.0,
            "original_text": "Hello world!",
            "translated_text": "שלום עולם!",
            "segment_type": "DIALOGUE",
        },
    ]
    srt = serialize_to_srt(segments, use_translated=True, apply_rtl=True)
    assert f"{{\\an8}}{RLE}{RLM}[שבוע לאחר מכן...]{RLM}{PDF}" in srt
    assert f"\n{RLE}{RLM}שלום עולם!{RLM}{PDF}\n" in srt

    vtt = serialize_to_vtt(segments, use_translated=True, apply_rtl=True)
    assert "00:00:10.000 --> 00:00:14.000 line:8%" in vtt
    assert "00:00:11.000 --> 00:00:13.000\n" in vtt


def test_onscreen_positions_in_srt_and_vtt():
    segments = [
        {
            "start_time": 5.0,
            "end_time": 9.0,
            "original_text": "[Dr. Jane Doe]",
            "translated_text": "[ד\"ר ג'יין דו]",
            "segment_type": "ON_SCREEN",
        }
    ]
    # Middle right (e.g. for interview chyrons on the right)
    srt_mr = serialize_to_srt(segments, use_translated=False, onscreen_position="middle_right")
    assert "{\\an6}[Dr. Jane Doe]" in srt_mr

    vtt_mr = serialize_to_vtt(segments, use_translated=False, onscreen_position="middle_right")
    assert "line:50% position:90% align:end" in vtt_mr

    # Middle left
    srt_ml = serialize_to_srt(segments, use_translated=False, onscreen_position="middle_left")
    assert "{\\an4}[Dr. Jane Doe]" in srt_ml

    vtt_ml = serialize_to_vtt(segments, use_translated=False, onscreen_position="middle_left")
    assert "line:50% position:10% align:start" in vtt_ml

