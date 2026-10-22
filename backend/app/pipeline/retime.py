"""
Timing-only correction of finished subtitles against the speech detected in the audio.

Two rules, both driven by speech regions (start, end) from a voice-activity detector:

* R2 (safe everywhere): a cue that starts inside a silence, up to MAX_LEAD seconds before the next
  speech onset, and reaches into that speech, is really waiting for that speech - its start moves to
  just before the onset (the end is kept).
* R1 (only when the caller allows it, e.g. right after the user moved cues around): a cue that
  sits entirely in silence, with a speech onset shortly after its end, is moved as a whole so it
  starts just before that onset (its duration is kept).

Text is never touched, ON_SCREEN cues are never touched, and the cues stay in order without overlaps.
"""
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

PAD = 0.2             # the cue may lead the first sound by this much
MIN_GAP = 0.4         # a silence at least this long separates two speech stretches
MIN_LEAD = 0.6        # R2 only fires if the cue starts at least this far before the onset
MAX_LEAD = 2.6        # ... and at most this far
MIN_TAIL = 0.15       # the cue must reach this far into the speech
MIN_DURATION = 1.0    # a cue shortened by a move is stretched back toward this length
CUE_GAP = 0.04        # smallest gap kept between neighbours

Region = Tuple[float, float]


@dataclass
class RetimeStats:
    checked: int = 0
    changed: int = 0
    max_shift: float = 0.0


def _is_skipped(cue) -> bool:
    return (getattr(cue, "segment_type", None) or "DIALOGUE") == "ON_SCREEN"


def _gaps(regions: Sequence[Region], min_gap: float) -> List[Tuple[float, float]]:
    """(end of speech, start of next speech) for every silence of at least `min_gap`."""
    return [(a[1], b[0]) for a, b in zip(regions, regions[1:]) if b[0] - a[1] >= min_gap]


def _in_silence(regions: Sequence[Region], start: float, end: float) -> bool:
    return not any(r[0] < end and r[1] > start for r in regions)


def _first_onset_after(regions: Sequence[Region], t: float) -> Optional[float]:
    for r in regions:
        if r[0] >= t:
            return r[0]
    return None


def retime_cues(
    cues: Sequence,
    regions: Sequence[Region],
    scope: Optional[Iterable[int]] = None,
    allow_silent_shift: bool = False,
) -> RetimeStats:
    """Adjust start/end of cues in place. `cues` must be in time order; `scope` limits positions."""
    regions = sorted(regions)
    gaps = _gaps(regions, MIN_GAP)
    stats = RetimeStats()
    positions: Set[int] = set(range(len(cues))) if scope is None else {i for i in scope if 0 <= i < len(cues)}
    touched: Set[int] = set()

    for i in sorted(positions):
        cue = cues[i]
        if _is_skipped(cue):
            continue
        stats.checked += 1
        old_start = cue.start_time

        new_start = None
        for gap_start, onset in gaps:
            if (
                cue.start_time >= gap_start - 0.3
                and MIN_LEAD <= onset - cue.start_time <= MAX_LEAD
                and cue.end_time > onset + MIN_TAIL
            ):
                new_start = round(onset - PAD, 3)
                break
        if new_start is not None:
            cue.start_time = new_start
        elif allow_silent_shift and _in_silence(regions, cue.start_time, cue.end_time):
            onset = _first_onset_after(regions, cue.end_time)
            if onset is not None and onset - cue.end_time <= MAX_LEAD:
                length = cue.end_time - cue.start_time
                cue.start_time = round(onset - PAD, 3)
                cue.end_time = round(cue.start_time + length, 3)

        if cue.start_time != old_start:
            touched.add(i)
            stats.changed += 1
            stats.max_shift = max(stats.max_shift, abs(cue.start_time - old_start))

    _settle(cues, touched)
    return stats


def _settle(cues: Sequence, touched: Set[int]) -> None:
    """Keep touched cues in order, without overlaps, and not absurdly short."""
    for i in sorted(touched):
        cue = cues[i]
        nxt = next((cues[k] for k in range(i + 1, len(cues)) if not _is_skipped(cues[k])), None)
        if nxt is not None:
            limit = nxt.start_time - CUE_GAP
            if cue.end_time > limit:
                if limit - cue.start_time >= 0.5:
                    cue.end_time = round(limit, 3)
                else:
                    cue.end_time = round(max(cue.end_time, cue.start_time + 0.5), 3)
            elif cue.end_time - cue.start_time < MIN_DURATION:
                cue.end_time = round(min(cue.start_time + MIN_DURATION, limit), 3)
        elif cue.end_time - cue.start_time < MIN_DURATION:
            cue.end_time = round(cue.start_time + MIN_DURATION, 3)
        prev = next((cues[k] for k in range(i - 1, -1, -1) if not _is_skipped(cues[k])), None)
        if prev is not None and prev.end_time > cue.start_time - CUE_GAP:
            wanted = cue.start_time - CUE_GAP
            if wanted - prev.start_time >= 0.3:
                prev.end_time = round(wanted, 3)
        if cue.end_time <= cue.start_time:
            cue.end_time = round(cue.start_time + 0.5, 3)
