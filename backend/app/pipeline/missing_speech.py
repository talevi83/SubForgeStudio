"""
Missing speech recovery: compare existing subtitles (embedded or synced) against Whisper
speech-to-text to recover un-subtitled dialogue (e.g. radio/walkie-talkie chatter,
missed speech during on-screen titles, background voices).

Guarantees ZERO duplicate subtitles:
1. Existing DIALOGUE subtitles that overlap a Whisper segment in time suppress it.
2. If same-language, fuzzy token comparison verifies matching phrases.
3. ON_SCREEN subtitles (titles, signs at screen top) do NOT suppress spoken speech;
   spoken speech is recovered at screen bottom without clipping the on-screen title.
4. Recovered lines are clamped to prevent collision with adjacent dialogue.
"""

import difflib
import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple
from app.models.db_models import SegmentType
from app.pipeline.context import RawSegment
from app.utils.srt_formatter import calculate_cps, split_long_lines

logger = logging.getLogger(__name__)

# Comms / radio keyword patterns (case-insensitive)
_COMMS_REGEX = re.compile(
    r"\b(copy\s+that|roger(\s+that)?|over\s+and\s+out|over\b|dispatch|command|base\b|"
    r"mayday|affirmative|negative|standing\s+by|radio|comms|walkie|channel\s+\d+|10-\d+)\b",
    re.IGNORECASE,
)

# Common sound effect or music tokens that Whisper hallucinates
_NOISE_REGEX = re.compile(
    r"^\s*([\[\(].*?[\]\)]|[♪#♩♫\*\-_]+|\.+)\s*$"
)

# Repetition loop detector (Whisper hallucination)
_REPETITION_REGEX = re.compile(r"(\b.+?\b)(?:\s+\1){3,}", re.IGNORECASE)

_NIQQUD = re.compile(r"[֑-ׇ]")
_PUNCT_SPLIT = re.compile(r"[^\w']+", re.UNICODE)


def _normalize_tokens(text: str) -> List[str]:
    text = _NIQQUD.sub("", unicodedata.normalize("NFKC", text)).casefold().replace("’", "'")
    return [t.strip("'") for t in _PUNCT_SPLIT.split(text) if t.strip("'")]


def _is_clean_candidate(text: str, confidence: float, min_confidence: float, min_words: int) -> bool:
    if not text or not text.strip():
        return False
    clean = text.strip()
    if _NOISE_REGEX.match(clean):
        return False
    if _REPETITION_REGEX.search(clean):
        return False
    if confidence < min_confidence:
        return False
    tokens = _normalize_tokens(clean)
    if len(tokens) < min_words and len(clean) < 6:
        return False
    return True


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _iou(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    inter = _overlap(a_start, a_end, b_start, b_end)
    union = max(a_end, b_end) - min(a_start, b_start)
    return inter / union if union > 0 else 0.0


def _text_similarity(a: str, b: str) -> float:
    toks_a = _normalize_tokens(a)
    toks_b = _normalize_tokens(b)
    if not toks_a or not toks_b:
        return 0.0
    s_a = " ".join(toks_a)
    s_b = " ".join(toks_b)
    if s_a in s_b or s_b in s_a:
        return 1.0
    return difflib.SequenceMatcher(None, s_a, s_b).ratio()


def _expand_whisper_candidates(
    raw_whisper_segments: Sequence[RawSegment],
    dialogue_segs: Sequence[Dict[str, Any]],
) -> List[RawSegment]:
    """
    Split Whisper segments at internal pauses (> 0.6s) and strip words already covered by
    existing dialogue segments so an un-subtitled sentence glued to an already-subtitled
    sentence inside the same Whisper decoding chunk is recovered cleanly.
    """
    candidates: List[RawSegment] = []
    for seg in raw_whisper_segments:
        words = [w for w in (getattr(seg, "words", None) or []) if getattr(w, "word", "").strip()]
        if len(words) < 2:
            candidates.append(seg)
            continue

        # Keep only words whose midpoint is not already covered by an existing dialogue cue
        uncovered_runs: List[List[Any]] = []
        for w in words:
            ws, we = float(w.start), float(w.end)
            w_mid = (ws + we) / 2.0
            in_existing = any(
                float(d["start_time"]) - 0.08 <= w_mid <= float(d["end_time"]) + 0.08
                for d in dialogue_segs
            )
            if in_existing:
                if uncovered_runs and uncovered_runs[-1]:
                    uncovered_runs.append([])
                continue
            if not uncovered_runs:
                uncovered_runs.append([w])
            else:
                if uncovered_runs[-1] and ws - float(uncovered_runs[-1][-1].end) > 0.65:
                    uncovered_runs.append([w])
                else:
                    uncovered_runs[-1].append(w)

        non_empty = [r for r in uncovered_runs if r]
        if not non_empty:
            continue
        if len(non_empty) == 1 and len(non_empty[0]) == len(words):
            candidates.append(seg)
            continue
        for run in non_empty:
            text = "".join(w.word for w in run).strip()
            if text:
                probs = [float(getattr(w, "probability", 0.8) or 0.8) for w in run]
                conf = sum(probs) / len(probs) if probs else (seg.confidence or 0.8)
                candidates.append(
                    RawSegment(
                        index=seg.index,
                        start_time=float(run[0].start),
                        end_time=float(run[-1].end),
                        text=text,
                        confidence=conf,
                        words=list(run),
                    )
                )
    return candidates


def supplement_missing_speech(
    existing_segments: List[Dict[str, Any]],
    raw_whisper_segments: Sequence[RawSegment],
    same_language: bool = True,
    min_gap: float = 0.04,
    min_confidence: float = 0.40,
    min_words: int = 2,
    max_chars_per_line: int = 42,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Compare existing subtitle segments with Whisper transcription to recover missing speech.

    Returns:
        (merged_segments, recovered_count)
    """
    if not raw_whisper_segments:
        return existing_segments, 0

    # Separate dialogue segments from onscreen segments
    dialogue_segs = [
        s for s in existing_segments
        if s.get("segment_type") != SegmentType.ON_SCREEN.value
    ]

    recovered: List[Dict[str, Any]] = []

    # Expand and sort Whisper segments by time
    expanded_whisper = _expand_whisper_candidates(raw_whisper_segments, dialogue_segs)
    whisper_sorted = sorted(expanded_whisper, key=lambda s: (s.start_time, s.end_time))

    for w_seg in whisper_sorted:
        w_start = round(float(w_seg.start_time), 3)
        w_end = round(float(w_seg.end_time), 3)
        w_text = w_seg.text.strip()
        w_conf = float(w_seg.confidence) if w_seg.confidence is not None else 0.8
        dur = max(0.1, w_end - w_start)

        # 1. Cleanliness & quality filter
        if not _is_clean_candidate(w_text, w_conf, min_confidence, min_words):
            continue

        # 2. Check overlap with existing DIALOGUE segments
        # Any substantial overlap with dialogue means this speech is already subtitled
        covered_by_dialogue = False
        for d in dialogue_segs:
            d_start, d_end = float(d["start_time"]), float(d["end_time"])
            inter = _overlap(w_start, w_end, d_start, d_end)
            if inter > 0:
                # If inter covers more than 30% of candidate or 30% of dialogue
                if inter >= 0.3 * dur or inter >= 0.3 * (d_end - d_start):
                    if same_language:
                        existing_text = d.get("original_text", "")
                        sim = _text_similarity(w_text, existing_text)
                        if sim >= 0.25:
                            covered_by_dialogue = True
                            break
                    else:
                        # Translated: an existing dialogue subtitle is already showing
                        covered_by_dialogue = True
                        break

            # Midpoint check: if midpoint falls inside an existing dialogue subtitle
            w_mid = (w_start + w_end) / 2
            if d_start - 0.2 <= w_mid <= d_end + 0.2:
                if same_language:
                    existing_text = d.get("original_text", "")
                    sim = _text_similarity(w_text, existing_text)
                    if sim >= 0.25:
                        covered_by_dialogue = True
                        break
                else:
                    covered_by_dialogue = True
                    break

        if covered_by_dialogue:
            continue

        # 3. Check overlap with previously recovered candidates to prevent self-duplication
        already_recovered = False
        for r in recovered:
            r_start, r_end = float(r["start_time"]), float(r["end_time"])
            if _overlap(w_start, w_end, r_start, r_end) >= 0.4 * dur:
                already_recovered = True
                break
        if already_recovered:
            continue

        # 4. Determine segment type (only mark COMMS when radio/comms phrasing is present)
        is_comms = bool(_COMMS_REGEX.search(w_text))
        seg_type = SegmentType.COMMS.value if is_comms else SegmentType.DIALOGUE.value

        # 6. Clamp timing so it doesn't collide with adjacent dialogue segments
        clamped_start = w_start
        clamped_end = w_end

        # Preceding dialogue
        preceding = [d for d in dialogue_segs if float(d["end_time"]) <= clamped_start + 0.1]
        if preceding:
            prev_d = max(preceding, key=lambda d: float(d["end_time"]))
            if clamped_start < float(prev_d["end_time"]) + min_gap:
                clamped_start = round(float(prev_d["end_time"]) + min_gap, 3)

        # Succeeding dialogue
        succeeding = [d for d in dialogue_segs if float(d["start_time"]) >= clamped_start]
        if succeeding:
            next_d = min(succeeding, key=lambda d: float(d["start_time"]))
            if clamped_end > float(next_d["start_time"]) - min_gap:
                clamped_end = round(float(next_d["start_time"]) - min_gap, 3)

        # Ensure valid duration after clamping
        if clamped_end - clamped_start < 0.5:
            # If the remaining gap is too tight for readable speech, skip
            continue

        # Split text into readable subtitle lines if long
        formatted_text = split_long_lines(w_text, max_chars_per_line=max_chars_per_line)

        recovered.append({
            "index": 0,  # will be re-indexed below
            "start_time": clamped_start,
            "end_time": clamped_end,
            "original_text": formatted_text,
            "translated_text": None,
            "translation_failed": False,
            "segment_type": seg_type,
            "confidence": round(w_conf, 2),
            "cps": calculate_cps(formatted_text, max(0.1, clamped_end - clamped_start)),
        })

    if not recovered:
        return existing_segments, 0

    logger.info(f"Supplemented {len(recovered)} missing speech segments from Whisper transcription.")

    # 7. Merge, sort and re-index
    all_segments = list(existing_segments) + recovered
    all_segments.sort(
        key=lambda s: (float(s["start_time"]), s.get("segment_type") != SegmentType.ON_SCREEN.value)
    )

    for i, seg in enumerate(all_segments, start=1):
        seg["index"] = i
        text_for_cps = seg.get("translated_text") or seg.get("original_text", "")
        dur = max(0.1, float(seg["end_time"]) - float(seg["start_time"]))
        seg["cps"] = calculate_cps(text_for_cps, dur)

    return all_segments, len(recovered)
