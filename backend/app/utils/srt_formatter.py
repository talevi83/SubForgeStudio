from typing import List, Any
from app.utils.rtl_fixer import apply_rtl_fix, strip_directional_marks
from app.utils.text_rules import (
    BREAK_BEFORE_WORDS,
    WEAK_END_WORDS,
    bare,
    ends_clause,
    ends_sentence,
)


def format_srt_timestamp(seconds: float) -> str:
    """Convert float seconds to SRT timecode: HH:MM:SS,mmm"""
    # Round once on the total millisecond count so carries propagate correctly
    # (e.g. 59.9996s -> 00:01:00,000 instead of the invalid 00:00:60,000).
    total_ms = int(round(max(0.0, float(seconds)) * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_vtt_timestamp(seconds: float) -> str:
    """Convert float seconds to WebVTT timecode: HH:MM:SS.mmm"""
    return format_srt_timestamp(seconds).replace(",", ".")


def calculate_cps(text: str, duration: float) -> float:
    """Calculate Characters Per Second (CPS) ignoring newlines and BiDi marks."""
    if duration <= 0:
        return 0.0
    clean = strip_directional_marks(text or "").replace("\n", "").strip()
    return round(len(clean) / duration, 1)


def _split_unspaced(text: str, max_chars_per_line: int) -> str:
    """Line-break text without spaces (Chinese/Japanese), preferring punctuation near the middle."""
    mid = len(text) // 2
    best, best_score = mid, float("inf")
    for i in range(1, len(text)):
        score = abs(i - mid) - (6 if text[i - 1] in "，、。！？,;:" else 0)
        if score < best_score:
            best, best_score = i, score
    return f"{text[:best]}\n{text[best:]}"


def split_long_lines(text: str, max_chars_per_line: int = 42) -> str:
    """
    Break a single-line subtitle into at most two lines if it exceeds the limit.

    Picks the break that keeps both lines within the limit and as balanced as possible,
    preferring sentence/clause boundaries and avoiding lines that end on an article or
    preposition ("the", "to", ...).
    """
    if "\n" in text:
        return text
    text = text.strip()
    if len(text) <= max_chars_per_line:
        return text

    words = text.split()
    if len(words) <= 1:
        return _split_unspaced(text, max_chars_per_line) if len(text) > max_chars_per_line else text

    best_i, best_score = 1, float("inf")
    for i in range(1, len(words)):
        line1 = " ".join(words[:i])
        line2 = " ".join(words[i:])
        overflow = max(0, len(line1) - max_chars_per_line) + max(0, len(line2) - max_chars_per_line)
        score = overflow * 1000 + abs(len(line1) - len(line2))
        if ends_sentence(words[i - 1]):
            score -= 14
        elif ends_clause(words[i - 1]):
            score -= 7
        if bare(words[i - 1]) in WEAK_END_WORDS:
            score += 8
        if bare(words[i]) in BREAK_BEFORE_WORDS:
            score -= 3
        if score < best_score:
            best_i, best_score = i, score

    return f"{' '.join(words[:best_i])}\n{' '.join(words[best_i:])}"


def _get_seg_attr(seg: Any, key: str, default: Any = None) -> Any:
    """Retrieve attribute from dict or object."""
    if isinstance(seg, dict):
        return seg.get(key, default)
    return getattr(seg, key, default)


def _is_bracketed_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("[") and s.endswith("]")


def _ensure_brackets(text: str) -> str:
    s = text.strip()
    if not s:
        return s
    if s.startswith("[") and s.endswith("]"):
        return s
    lines = [ln.strip("[] ").strip() for ln in s.splitlines() if ln.strip("[] ")]
    return f"[{' — '.join(lines)}]" if lines else s


_SRT_POS_MAP = {
    "top_center": "{\\an8}",
    "top_left": "{\\an7}",
    "top_right": "{\\an9}",
    "middle_left": "{\\an4}",
    "middle_right": "{\\an6}",
    "middle_center": "{\\an5}",
}

_VTT_POS_MAP = {
    "top_center": " line:8%",
    "top_left": " line:8% position:10% align:start",
    "top_right": " line:8% position:90% align:end",
    "middle_left": " line:50% position:10% align:start",
    "middle_right": " line:50% position:90% align:end",
    "middle_center": " line:50% position:50% align:center",
}


def _serialize_blocks(
    segments: List[Any],
    use_translated: bool,
    apply_rtl: bool,
    max_chars_per_line: int,
    format_ts,
    fmt: str = "srt",
    onscreen_position: str = "top_center",
) -> List[str]:
    blocks: List[str] = []
    ordered = sorted(
        segments,
        key=lambda s: (
            float(_get_seg_attr(s, "start_time", 0.0)),
            (_get_seg_attr(s, "segment_type") or "DIALOGUE") != "ON_SCREEN",
        ),
    )

    def _append_cue(cue_text: str, start_time: float, end_time: float, is_top: bool) -> None:
        if is_top:
            cue_text = _ensure_brackets(cue_text)
        formatted_text = split_long_lines(cue_text, max_chars_per_line=max_chars_per_line)
        if apply_rtl:
            formatted_text = apply_rtl_fix(formatted_text)
        if is_top and fmt == "srt":
            pos_tag = _SRT_POS_MAP.get(onscreen_position, "{\\an8}")
            formatted_text = f"{pos_tag}{formatted_text}"
        cue_settings = _VTT_POS_MAP.get(onscreen_position, " line:8%") if (is_top and fmt == "vtt") else ""
        blocks.append(
            f"{len(blocks) + 1}\n{format_ts(start_time)} --> {format_ts(end_time)}{cue_settings}\n{formatted_text}\n"
        )

    for seg in ordered:
        translated = _get_seg_attr(seg, "translated_text")
        original = _get_seg_attr(seg, "original_text", "")
        raw_text = translated if (use_translated and translated) else original
        clean_text = strip_directional_marks((raw_text or "").strip())
        if not clean_text:
            continue

        seg_type = _get_seg_attr(seg, "segment_type") or "DIALOGUE"
        start_time = float(_get_seg_attr(seg, "start_time", 0.0))
        end_time = float(_get_seg_attr(seg, "end_time", 0.0))
        raw_lines = [ln.strip() for ln in clean_text.splitlines() if ln.strip()]

        if seg_type == "ON_SCREEN" or (raw_lines and all(_is_bracketed_line(ln) for ln in raw_lines)):
            _append_cue(clean_text, start_time, end_time, is_top=True)
        elif len(raw_lines) >= 2 and _is_bracketed_line(raw_lines[0]):
            # Separate a bracketed on-screen top line from spoken dialogue below it
            _append_cue(raw_lines[0], start_time, end_time, is_top=True)
            _append_cue("\n".join(raw_lines[1:]), start_time, end_time, is_top=False)
        else:
            _append_cue(clean_text, start_time, end_time, is_top=False)

    return blocks


def serialize_to_srt(
    segments: List[Any],
    use_translated: bool = False,
    apply_rtl: bool = True,
    max_chars_per_line: int = 42,
    onscreen_position: str = "top_center",
) -> str:
    """Serialize segment dictionaries or models into a standard .srt file string."""
    return "\n".join(
        _serialize_blocks(
            segments,
            use_translated,
            apply_rtl,
            max_chars_per_line,
            format_srt_timestamp,
            fmt="srt",
            onscreen_position=onscreen_position,
        )
    )


def serialize_to_vtt(
    segments: List[Any],
    use_translated: bool = False,
    apply_rtl: bool = True,
    max_chars_per_line: int = 42,
    onscreen_position: str = "top_center",
) -> str:
    """Serialize segment dictionaries or models into a WebVTT file string."""
    blocks = ["WEBVTT\n"]
    blocks.extend(
        _serialize_blocks(
            segments,
            use_translated,
            apply_rtl,
            max_chars_per_line,
            format_vtt_timestamp,
            fmt="vtt",
            onscreen_position=onscreen_position,
        )
    )
    return "\n".join(blocks)
