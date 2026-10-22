"""
Pull word start times forward to where the speech really begins.

Whisper's word timestamps come from cross-attention alignment, which knows nothing about silence:
when a pause sits in the middle of a decoding window, the pause is absorbed by the word that
follows it, so that word "starts" right when the previous word ended. Subtitles built from those
times appear one to two seconds before anyone speaks (and, because the batched pipeline pads each
voice-activity chunk, the typical lead is already a few tenths of a second).

A voice-activity detector (Silero VAD) sees the audio itself, so it knows where each stretch of
speech starts. A word that spans a silent gap - it starts before the pause and ends inside the
speech after it - cannot really be one continuous sound: its end is right (it is tied to the
speech) and its start is the previous word's end. It is moved to just before the speech that
follows the gap. (The word may start *inside* the speech region of the previous word, because the
detector's region usually ends a little after Whisper's end of that word, so the test is "crosses
a silent gap", not "starts in silence".)

Starts are only ever moved *later*, and only when the detector actually found speech inside the
word - so speech the detector misses (shouting under gunfire, radio voices under music) is left
exactly as Whisper timed it.
"""
from bisect import bisect_right
from dataclasses import dataclass
from typing import List, Sequence, Tuple

SNAP_PAD = 0.2        # the subtitle may lead the first sound by this much
MIN_GAP = 0.4         # a silence at least this long splits two stretches of speech
MIN_SHIFT = 0.4       # smaller corrections are not worth touching
MIN_TAIL = 0.15       # the word must reach at least this far into the speech after the gap


@dataclass
class SnapStats:
    words: int = 0
    moved: int = 0
    total_shift: float = 0.0
    max_shift: float = 0.0

    @property
    def mean_shift(self) -> float:
        return self.total_shift / self.moved if self.moved else 0.0


def _onsets_after_gaps(regions: Sequence[Tuple[float, float]], min_gap: float) -> List[float]:
    """Start of every speech region preceded by a silence of at least `min_gap` seconds."""
    return [b[0] for a, b in zip(regions, regions[1:]) if b[0] - a[1] >= min_gap]


from app.utils.text_rules import ends_sentence


def snap_word_starts(
    raw_segments: Sequence,
    regions: Sequence[Tuple[float, float]],
    pad: float = SNAP_PAD,
    min_gap: float = MIN_GAP,
    min_shift: float = MIN_SHIFT,
    min_tail: float = MIN_TAIL,
) -> SnapStats:
    """Move word starts that sit before a silent gap to the speech onset after it."""
    stats = SnapStats()
    onsets = _onsets_after_gaps(sorted(regions), min_gap)

    for seg in raw_segments:
        words: List = list(getattr(seg, "words", None) or [])
        for i, w in enumerate(words):
            stats.words += 1
            # The last onset (after a real pause) that lies inside the word, leaving some of it
            inside = [a for a in onsets[bisect_right(onsets, w.start):bisect_right(onsets, w.end - min_tail)]]
            if not inside:
                continue
            # If `w` closes a sentence that began immediately before it ("Military officials.")
            # and another sentence follows in this segment ("I thought..."), Whisper stretched
            # `w.end` across the inter-sentence pause. Snapping `w.start` after the pause would
            # detach "officials." from "Military" and glue it onto "I thought...". Clamp `w.end`
            # before the pause instead so `w` stays with its own sentence.
            if (
                0 < i < len(words) - 1
                and ends_sentence(getattr(w, "word", "") or "")
                and not ends_sentence(getattr(words[i - 1], "word", "") or "")
                and float(w.start) - float(words[i - 1].end) < min_gap
            ):
                clamped_end = max(float(w.start) + 0.15, inside[0] - pad)
                if clamped_end < float(w.end):
                    w.end = round(clamped_end, 3)
                continue
            new_start = inside[-1] - pad
            shift = new_start - w.start
            if shift < min_shift:
                continue
            w.start = round(new_start, 3)
            stats.moved += 1
            stats.total_shift += shift
            stats.max_shift = max(stats.max_shift, shift)
        if words and words[0].start > seg.start_time:
            seg.start_time = min(words[0].start, seg.end_time)
    return stats
