"""
On-screen text segments: build bracketed ON_SCREEN segments, and turn the per-frame text
detections of the vision pass into timed segments (merging repeats and partial reveals,
dropping HUD/callsign labels, credits and document boilerplate).
"""
import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.models.db_models import SegmentType

logger = logging.getLogger(__name__)

_ONSCREEN_BUFFER = 1.0    # seconds added after the last frame a text was seen in
_ONSCREEN_MIN_DURATION = 3.0
_TAG_MAX_CHARS = 6               # a lone short word ("EAGLE", "LIVE") is a HUD/callsign label, not a title
_TAG_MAX_UPPER = 12              # a lone ALL-CAPS word ("VULTURE") is a callsign even when longer


def format_onscreen_text(text: str) -> str:
    """'One week later...' -> '[One week later...]'; multi-line cards joined with an em dash."""
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    lines = [l.strip("[]").strip() for l in lines if l.strip("[] ")]
    return f"[{' — '.join(lines)}]" if lines else ""


def make_onscreen_segment(
    start: float,
    end: float,
    text: str,
    bracket: bool = True,
    min_duration: float = _ONSCREEN_MIN_DURATION,
) -> Dict[str, Any]:
    clean = format_onscreen_text(text) if bracket else text.strip()
    return {
        "start_time": round(start, 3),
        "end_time": round(max(end, start + min_duration), 3),
        "original_text": clean,
        "translated_text": None,
        "translation_failed": False,
        "segment_type": SegmentType.ON_SCREEN.value,
        "confidence": 1.0,
    }


@dataclass
class FrameDetection:
    time: float
    text: str
    until: Optional[float] = None  # last moment the same picture was on screen


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _is_partial_reveal(a: str, b: str) -> bool:
    """True if one text is a mid-animation typewriter/fade-in fragment of the other
    (e.g. 'THE MESA DRI' vs 'THE MESA DRILL SITE', or 'BARD — IGATOR' vs
    'ERIK BARD — PRINCIPAL INVESTIGATOR')."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb or na == nb:
        return False
    short_n, long_n = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short_n) >= 4 and short_n in long_n:
        return True
    short_raw, long_raw = (a, b) if len(na) <= len(nb) else (b, a)
    tokens = [_norm(t) for t in re.split(r"[\s—–\-/,:;|]+", short_raw) if len(_norm(t)) >= 3]
    if len(tokens) >= 2 and sum(len(t) for t in tokens) >= 6:
        pos = 0
        for tok in tokens:
            idx = long_n.find(tok, pos)
            if idx < 0:
                return False
            pos = idx + len(tok)
        return True
    return False


def _same_text(a: str, b: str, threshold: float = 0.85) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return (
        na == nb
        or difflib.SequenceMatcher(None, na, nb).ratio() >= threshold
        or _is_partial_reveal(a, b)
    )


def merge_frame_detections(
    detections: Sequence[FrameDetection],
    frame_interval: float = 2.0,
    buffer: float = _ONSCREEN_BUFFER,
    min_duration: float = _ONSCREEN_MIN_DURATION,
    bracket: bool = True,
    segment_type: str = SegmentType.ON_SCREEN.value,
    drop_tags: bool = False,
    repeat_window: float = 0.0,
    file_name: str = "",
    sensitivity: int = 2,
) -> List[Dict[str, Any]]:
    """
    Collapse consecutive frames showing the same text into one ON_SCREEN segment:
    start = first appearance, end = last appearance (+ static run) + buffer, at least
    min_duration long, text in brackets. Slight OCR variations ("One Week Later" vs
    "One week later...") count as the same text.

    For narrative titles (not burned-in subtitles) `drop_tags` discards lone short words
    (HUD labels, unit callsigns), random document boilerplate, and the show's own title logo,
    and `repeat_window` keeps a sign that was already shown from being repeated when the
    camera returns to it shortly after.
    """
    max_gap = frame_interval * 2.5
    groups: List[Dict[str, Any]] = []
    for det in sorted(detections, key=lambda d: d.time):
        if not det.text or not det.text.strip():
            continue
        if not re.search(r"[^\W\d_]", det.text):
            continue  # bare numbers / clocks ("151", "00:35") are not readable text
        if drop_tags and (
            _is_tag(det.text, sensitivity=sensitivity)
            or _is_unwanted_onscreen(det.text, file_name=file_name, sensitivity=sensitivity)
        ):
            logger.info(f"On-screen text at {det.time:.1f}s dropped as non-narrative/label: {det.text.strip()!r}")
            continue
        last_seen = max(det.time, det.until or det.time)
        match = None
        for g in reversed(groups):
            if det.time - g["last"] > max_gap:
                break  # older groups are even further away
            if _same_text(g["text"], det.text):
                match = g
                break
        if match is None:
            groups.append({"start": det.time, "last": last_seen, "text": det.text.strip()})
        else:
            match["last"] = max(match["last"], last_seen)
            # Keep the longest (most complete) reading of the text
            if len(det.text.strip()) > len(match["text"]):
                match["text"] = det.text.strip()

    segments = []
    shown: List[Dict[str, Any]] = []
    for idx, g in enumerate(groups):
        if repeat_window and any(
            g["start"] - p["start"] <= repeat_window and _same_text(p["text"], g["text"]) for p in shown
        ):
            logger.info(f"On-screen text at {g['start']:.1f}s already shown, not repeated: {g['text']!r}")
            continue
        shown.append(g)
        clean_len = len(re.sub(r"[\s\[\]]+", "", g["text"]))
        reading_time = max(min_duration, clean_len / 15.0) if segment_type == SegmentType.ON_SCREEN.value else min_duration
        end = max(g["last"] + buffer, g["start"] + reading_time)
        if segment_type == SegmentType.ON_SCREEN.value and idx + 1 < len(groups):
            nxt_start = groups[idx + 1]["start"]
            if nxt_start > g["start"] and end > nxt_start:
                end = max(g["start"] + 0.5, nxt_start - 0.084)
        seg = make_onscreen_segment(
            g["start"],
            end,
            g["text"],
            bracket=bracket,
            min_duration=min_duration if segment_type == SegmentType.ON_SCREEN.value else 0.5,
        )
        seg["segment_type"] = segment_type
        if seg["original_text"]:
            segments.append(seg)
    return segments


def _is_tag(text: str, sensitivity: int = 2) -> bool:
    """One short word with no digits: a HUD label or callsign rather than a title card.
    An ALL-CAPS single word ("VULTURE") is a callsign even when a bit longer; a capitalized
    one ("Siberia") may be a location title and is kept."""
    if sensitivity >= 3:
        return False
    words = text.split()
    if len(words) != 1 or any(c.isdigit() for c in words[0]):
        return False
    bare = _norm(words[0])
    if len(bare) <= _TAG_MAX_CHARS:
        return True
    if sensitivity == 2:
        return len(bare) <= 5
    return words[0].isupper() and len(bare) <= _TAG_MAX_UPPER


_DOCUMENT_BOILERPLATE = re.compile(
    r"""\b(
        arrest\s+report
      | police\s+(department|report)
      | incident\s+report
      | medical\s+(report|record)
      | autopsy\s+report
      | lab(oratory)?\s+report
      | case\s+(file|number|no\b)
      | personnel\s+file
      |search\s+warrant
      | certificate\s+of
      | department\s+of\s+justice
      | form\s+#?\d+
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

_ALWAYS_REJECTED_KINDS = {"credits", "show_title", "background", "incidental"}
_REJECTED_KINDS = {"document", "report", "form", "credits", "show_title", "background", "incidental"}


def _series_stem_norm(file_name: str) -> str:
    """Extract normalized series/movie title stem before S01E01 / year tokens."""
    if not file_name:
        return ""
    stem = Path(file_name).stem
    m = re.split(r"[.\s_-]+(?:S\d{1,2}E\d{1,2}|\d{3,4}p|(?:19|20)\d{2})\b", stem, maxsplit=1, flags=re.IGNORECASE)
    head = m[0] if m else stem
    return _norm(head)


def _is_unwanted_onscreen(text: str, kind: str = "", file_name: str = "", sensitivity: int = 2) -> bool:
    """
    Return True if `text` looks like random document/form boilerplate, an overly long
    document dump, or the show's own main title card rather than narrative titles,
    speaker identification chyrons, or viewer-intended messages (like phone texts).
    """
    k = (kind or "").strip().lower()
    if k in _ALWAYS_REJECTED_KINDS:
        return True
    if sensitivity <= 1:
        if k in {"document", "report", "form", "sign", "hud"}:
            return True
    elif sensitivity == 2:
        if k in {"report", "form"}:
            return True

    clean = text.strip().strip("[]").strip()
    if not clean:
        return True
    if sensitivity < 3 and _DOCUMENT_BOILERPLATE.search(clean):
        return True
    words = clean.split()
    if sensitivity <= 1:
        if len(words) > 14 or len(clean) > 95:
            return True
    elif sensitivity == 2:
        if len(words) > 26 or len(clean) > 180:
            return True
    else:  # Level 3 (Broad)
        if len(words) > 45 or len(clean) > 300:
            return True

    series_norm = _series_stem_norm(file_name)
    if series_norm and len(series_norm) >= 6 and _norm(clean) == series_norm:
        return True
    return False
