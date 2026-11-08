"""
Timeline merge: combine the refined spoken subtitles with the other cue kinds.

* COMMS — radio-procedure lines are tagged so they get radio phrasing in translation.
* ON_SCREEN — titles and signs are woven into the timeline (overlapping dialogue when allowed,
  otherwise placed in a free window between spoken lines).
* FOREIGN — passages in another language replace the overlapping transcript lines.
"""
import re
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.models.db_models import SegmentType
from app.utils.srt_formatter import calculate_cps


# ---------------------------------------------------------------------------
# Radio / comms classification
# ---------------------------------------------------------------------------

# Radio-procedure phrases. Deliberately specific so ordinary dialogue rarely matches.
_COMMS_PATTERNS = re.compile(
    r"""(
        (^|[,.;!?-]\s*)over[.!]?$          # "..., over." / a lone "Over."
      | \bover\s+and\s+out\b
      | \b(copy|roger)\s+that\b
      | \bdo\s+you\s+(copy|read)\b
      | \bhow\s+copy\b
      | ^(roger|wilco|copy)[.!]?$
      | \bwilco\b
      | \bsay\s+again\b
      | \bradio\s+check\b
      | \bsitrep\b
      | \bcome\s+in[.!?]*$                # "Dr. Scott, come in."
      | \bthis\s+is\s+[\w-]+(\s+[\w-]+)?\s+actual\b
      | \bbreak,?\s+break\b
      | \bmayday\b
      | \blima\s+charlie\b
      | \bfive\s+by\s+five\b
      | \bklicks?\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def is_comms_text(text: str) -> bool:
    """Heuristic: does this subtitle read like a radio transmission?"""
    flat = " ".join(text.split())
    return bool(_COMMS_PATTERNS.search(flat))


def classify_comms(segments: List[Dict[str, Any]]) -> int:
    """Mark DIALOGUE segments that look like radio traffic as COMMS. Returns how many changed."""
    changed = 0
    for seg in segments:
        if seg.get("segment_type", SegmentType.DIALOGUE.value) == SegmentType.DIALOGUE.value and is_comms_text(seg["original_text"]):
            seg["segment_type"] = SegmentType.COMMS.value
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# Timeline merge: spoken + on-screen
# ---------------------------------------------------------------------------

_MIN_STANDALONE_ONSCREEN = 3.0  # shortest on-screen caption worth showing on its own


def _overlap(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    return min(a["end_time"], b["end_time"]) - max(a["start_time"], b["start_time"])


def _free_window(onscreen: Dict[str, Any], spoken: List[Dict[str, Any]], min_gap: float):
    """Largest part of the on-screen span not covered by any spoken segment."""
    start, end = onscreen["start_time"], onscreen["end_time"]
    busy = sorted(
        (max(start, s["start_time"] - min_gap), min(end, s["end_time"] + min_gap))
        for s in spoken
        if _overlap(s, onscreen) > -min_gap
    )
    best, cursor = (0.0, start, start), start
    for b_start, b_end in busy:
        if b_start > cursor and b_start - cursor > best[0]:
            best = (b_start - cursor, cursor, b_start)
        cursor = max(cursor, b_end)
    if end > cursor and end - cursor > best[0]:
        best = (end - cursor, cursor, end)
    return best[1], best[2]


def merge_timeline(
    spoken: List[Dict[str, Any]],
    onscreen: List[Dict[str, Any]],
    max_chars_per_line: int = 42,
    min_gap: Optional[float] = None,
    allow_overlap: bool = True,
) -> List[Dict[str, Any]]:
    """
    Merge ON_SCREEN captions into the spoken timeline, sorted by start time and re-indexed 1..N.

    When allow_overlap is True (default):
    - ON_SCREEN captions (displayed at the top of the screen via {\\an8} / line:8%) are allowed to
      overlap with spoken dialogue at the bottom of the screen.
    - Each ON_SCREEN caption is kept for its full duration based on its on-screen appearance and
      comfortable reading speed, without being shortened or delayed by speech.
    - Spoken dialogue continues unaffected at the bottom according to its own reading pace and gaps.
    - Consecutive ON_SCREEN captions are kept from colliding with each other at the top.

    When allow_overlap is False (legacy):
    - Minor overlaps are trimmed into the largest free window between speech lines.
    """
    min_gap = settings.min_gap_seconds if min_gap is None else min_gap
    result = [dict(s) for s in spoken]
    spoken_view = list(result)

    onscreen_clean: List[Dict[str, Any]] = []
    for raw_cap in sorted(onscreen, key=lambda c: c["start_time"]):
        caption = dict(raw_cap)
        text = caption.get("original_text", "").strip()
        if not text:
            continue
        caption.setdefault("segment_type", SegmentType.ON_SCREEN.value)
        caption["end_time"] = max(float(caption["end_time"]), round(float(caption["start_time"]) + _MIN_STANDALONE_ONSCREEN, 3))
        # Ensure consecutive ON_SCREEN captions don't collide with each other at the top
        if onscreen_clean:
            prev = onscreen_clean[-1]
            if caption["start_time"] < prev["end_time"] + min_gap:
                prev["end_time"] = max(prev["start_time"] + 0.5, round(caption["start_time"] - min_gap, 3))
        onscreen_clean.append(caption)

    for idx, caption in enumerate(onscreen_clean):
        if allow_overlap:
            overlapping = [s for s in spoken_view if _overlap(s, caption) > 0]
            if overlapping:
                first_s = overlapping[0]
                last_s = overlapping[-1]
                min_allowed_start = (
                    round(onscreen_clean[idx - 1]["end_time"] + min_gap, 3)
                    if idx > 0
                    else 0.0
                )
                max_allowed_end = (
                    round(onscreen_clean[idx + 1]["start_time"] - min_gap, 3)
                    if idx + 1 < len(onscreen_clean)
                    else float("inf")
                )
                # Snap ON_SCREEN start to the overlapping spoken cue start when within frame-sampling jitter
                if (
                    abs(caption["start_time"] - first_s["start_time"]) <= 0.85
                    and first_s["start_time"] >= min_allowed_start
                    and caption["end_time"] - first_s["start_time"] >= _MIN_STANDALONE_ONSCREEN
                ):
                    caption["start_time"] = round(first_s["start_time"], 3)

                # Align ON_SCREEN end with the overlapping spoken cue end so top & bottom don't flicker mid-line
                if (
                    caption["end_time"] < last_s["end_time"]
                    and last_s["end_time"] - caption["end_time"] <= 2.2
                    and last_s["end_time"] <= max_allowed_end
                ):
                    caption["end_time"] = round(last_s["end_time"], 3)
                elif (
                    abs(caption["end_time"] - last_s["end_time"]) <= 0.85
                    and last_s["end_time"] - caption["start_time"] >= _MIN_STANDALONE_ONSCREEN
                    and last_s["end_time"] <= max_allowed_end
                ):
                    caption["end_time"] = round(last_s["end_time"], 3)

                # If a speaker chyron/title is still under 4.2s and the speaker immediately continues
                # into the next chained dialogue cue, hold the chyron until the end of that next cue.
                text_str = caption.get("original_text", "")
                if (
                    ("—" in text_str or len(text_str) >= 24)
                    and caption["end_time"] - caption["start_time"] < 4.2
                ):
                    chained = [
                        s for s in spoken_view
                        if 0.0 <= s["start_time"] - caption["end_time"] <= 0.45
                    ]
                    if chained:
                        next_s = chained[0]
                        if (
                            next_s["end_time"] - caption["start_time"] <= 6.5
                            and next_s["end_time"] <= max_allowed_end
                        ):
                            caption["end_time"] = round(next_s["end_time"], 3)

            result.append(caption)
            continue

        # Legacy behavior: trim minor overlaps to free window
        overlaps = [(s, _overlap(s, caption)) for s in spoken_view if _overlap(s, caption) > 0]
        if not overlaps:
            result.append(caption)
            continue

        caption_len = caption["end_time"] - caption["start_time"]
        free_start, free_end = _free_window(caption, spoken_view, min_gap)
        simultaneous = [
            s for s, ov in overlaps
            if ov >= 0.5 * caption_len
        ]
        if not simultaneous and free_end - free_start >= _MIN_STANDALONE_ONSCREEN:
            caption["start_time"], caption["end_time"] = round(free_start, 3), round(free_end, 3)
            result.append(caption)
            continue

        result.append(caption)

    result.sort(key=lambda s: (s["start_time"], s.get("segment_type") != SegmentType.ON_SCREEN.value))
    for i, seg in enumerate(result, start=1):
        seg["index"] = i
        text_for_cps = seg.get("translated_text") or seg["original_text"]
        seg["cps"] = calculate_cps(text_for_cps, max(0.1, seg["end_time"] - seg["start_time"]))
    return result


# ---------------------------------------------------------------------------
# Foreign-language passages
# ---------------------------------------------------------------------------

def _mid(seg: Dict[str, Any]) -> float:
    return (seg["start_time"] + seg["end_time"]) / 2


def merge_foreign(
    spoken: List[Dict[str, Any]],
    spans: List[Dict[str, Any]],
    ocr: List[Dict[str, Any]],
    audio: List[Dict[str, Any]],
    min_gap: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Put the right text on passages spoken in another language.

    For each span, the show's own burned-in/forced subtitles (`ocr`) are preferred; if none were
    found, Whisper's re-transcription in that language (`audio`) is used. Main-pass segments are
    removed only where they overlap the span's *cores* (the speech that really was foreign or
    untranscribed), so regular dialogue around a foreign line is kept. Spans with no
    replacement text are left untouched.
    """
    if not spans:
        return spoken
    min_gap = settings.min_gap_seconds if min_gap is None else min_gap
    kept = [dict(s) for s in spoken]
    added: List[Dict[str, Any]] = []

    for span in spans:
        in_span = lambda seg: span["start"] <= _mid(seg) <= span["end"]  # noqa: E731
        # Subtitles were read in a wider window than the speech span (short replies right after
        # the passage are often missed by the speech detector); clashes with kept dialogue are
        # dropped below.
        lo, hi = span.get("read_start", span["start"]), span.get("read_end", span["end"])
        in_window = lambda seg: lo <= _mid(seg) <= hi  # noqa: E731
        replacement = [dict(o) for o in ocr if in_window(o)] or [dict(a) for a in audio if in_span(a)]
        if not replacement:
            continue
        cores = span.get("cores") or [(span["start"], span["end"])]

        def in_cores(seg: Dict[str, Any]) -> bool:
            dur = max(0.01, seg["end_time"] - seg["start_time"])
            ov = sum(max(0.0, min(seg["end_time"], e) - max(seg["start_time"], s)) for s, e in cores)
            return ov >= 0.3 * dur

        kept = [s for s in kept if not in_cores(s)]
        for rep in replacement:
            rep["segment_type"] = SegmentType.FOREIGN.value
        added.extend(replacement)

    # A subtitle that sits on top of dialogue we kept is most likely not for foreign speech
    def clashes(seg: Dict[str, Any]) -> bool:
        dur = max(0.01, seg["end_time"] - seg["start_time"])
        return any(_overlap(seg, k) > 0.5 * dur for k in kept)

    merged = sorted(kept + [a for a in added if not clashes(a)], key=lambda s: s["start_time"])

    # Resolve small overlaps at the edges of a passage
    for prev, cur in zip(merged, merged[1:]):
        if cur["start_time"] < prev["end_time"] + min_gap:
            trimmed = cur["start_time"] - min_gap
            if trimmed - prev["start_time"] >= 0.4:
                prev["end_time"] = round(trimmed, 3)
            else:
                cur["start_time"] = round(prev["end_time"] + min_gap, 3)
                cur["end_time"] = max(cur["end_time"], round(cur["start_time"] + 0.4, 3))
    return merged
