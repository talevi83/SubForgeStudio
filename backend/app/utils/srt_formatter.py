import math
from typing import List, Any, Union
from app.utils.rtl_fixer import apply_rtl_fix, strip_directional_marks


def format_srt_timestamp(seconds: float) -> str:
    """Convert float seconds to SRT timecode: HH:MM:SS,mmm"""
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - math.floor(seconds)) * 1000))
    if millis >= 1000:
        secs += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_vtt_timestamp(seconds: float) -> str:
    """Convert float seconds to WebVTT timecode: HH:MM:SS.mmm"""
    return format_srt_timestamp(seconds).replace(",", ".")


def calculate_cps(text: str, duration: float) -> float:
    """Calculate Characters Per Second (CPS) ignoring newlines and BiDi marks."""
    if duration <= 0:
        return 0.0
    clean = strip_directional_marks(text).replace("\n", "").strip()
    return round(len(clean) / duration, 1)


def split_long_lines(text: str, max_chars_per_line: int = 42) -> str:
    """Split single-line text into maximum two balanced lines if it exceeds limit."""
    if "\n" in text:
        return text

    words = text.split()
    if len(text) <= max_chars_per_line or len(words) <= 1:
        return text

    mid = len(text) // 2
    best_split = min(
        range(1, len(words)),
        key=lambda i: abs(len(" ".join(words[:i])) - mid),
    )
    line1 = " ".join(words[:best_split])
    line2 = " ".join(words[best_split:])
    return f"{line1}\n{line2}"


def _get_seg_attr(seg: Any, key: str, default: Any = None) -> Any:
    """Retrieve attribute from dict or object."""
    if isinstance(seg, dict):
        return seg.get(key, default)
    return getattr(seg, key, default)


def serialize_to_srt(
    segments: List[Any],
    use_translated: bool = False,
    apply_rtl: bool = True,
    max_chars_per_line: int = 42,
) -> str:
    """Serialize segment dictionaries or models into a standard .srt file string."""
    blocks = []
    for idx, seg in enumerate(segments, start=1):
        translated = _get_seg_attr(seg, "translated_text")
        original = _get_seg_attr(seg, "original_text", "")
        raw_text = translated if (use_translated and translated) else original
        clean_text = strip_directional_marks((raw_text or "").strip())
        formatted_text = split_long_lines(clean_text, max_chars_per_line=max_chars_per_line)

        if apply_rtl:
            formatted_text = apply_rtl_fix(formatted_text)

        start_time = float(_get_seg_attr(seg, "start_time", 0.0))
        end_time = float(_get_seg_attr(seg, "end_time", 0.0))
        start_tc = format_srt_timestamp(start_time)
        end_tc = format_srt_timestamp(end_time)
        blocks.append(f"{idx}\n{start_tc} --> {end_tc}\n{formatted_text}\n")

    return "\n".join(blocks)


def serialize_to_vtt(
    segments: List[Any],
    use_translated: bool = False,
    apply_rtl: bool = True,
    max_chars_per_line: int = 42,
) -> str:
    """Serialize segment dictionaries or models into a WebVTT file string."""
    blocks = ["WEBVTT\n"]
    for idx, seg in enumerate(segments, start=1):
        translated = _get_seg_attr(seg, "translated_text")
        original = _get_seg_attr(seg, "original_text", "")
        raw_text = translated if (use_translated and translated) else original
        clean_text = strip_directional_marks((raw_text or "").strip())
        formatted_text = split_long_lines(clean_text, max_chars_per_line=max_chars_per_line)

        if apply_rtl:
            formatted_text = apply_rtl_fix(formatted_text)

        start_time = float(_get_seg_attr(seg, "start_time", 0.0))
        end_time = float(_get_seg_attr(seg, "end_time", 0.0))
        start_tc = format_vtt_timestamp(start_time)
        end_tc = format_vtt_timestamp(end_time)
        blocks.append(f"{idx}\n{start_tc} --> {end_tc}\n{formatted_text}\n")

    return "\n".join(blocks)
