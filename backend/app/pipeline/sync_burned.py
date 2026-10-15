"""
Find subtitles burned into the picture - locally, without OCR - to use their exact on/off times
when syncing a subtitle file.

ffmpeg decodes the bottom 30% of the picture at a few frames per second as small grayscale
images. Subtitle text is bright (white/yellow) with a dark outline or shadow, roughly centred,
and it appears and disappears from one frame to the next while the scene behind it may not
change. Per frame we measure the density of "bright pixel touching a dark pixel" and where it
sits; consecutive frames with the same text pattern form one on-screen interval, and a different
pattern (Jaccard of the masks) starts a new one.

The detector does not know whether a video *has* burned-in subtitles. The caller validates the
result against the speech (validate_against_speech) and ignores it when it does not fit.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from app.pipeline.context import PipelineCancelled

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 640, 100        # analysed band (bottom 30% of the picture, scaled)
FPS = 5.0
BRIGHT, DARK = 180, 70
CHANGE_JACCARD = 0.4            # below this, two frames show different text
MIN_DURATION, MAX_DURATION = 0.6, 12.0
CHUNK_FRAMES = 150
STRIP_ROWS = 30               # one line of subtitle text in the analysed band

Interval = Tuple[float, float]


@dataclass
class BurnedInResult:
    intervals: List[Interval] = field(default_factory=list)
    frames: int = 0
    threshold: float = 0.0
    error: Optional[str] = None


class BurnedInAnalyzer:
    """Feed uint8 frames of shape (n, HEIGHT, WIDTH); then intervals()."""

    def __init__(self, fps: float = FPS):
        self.fps = fps
        self.density: List[float] = []
        self.centred: List[bool] = []
        self.jaccard: List[float] = []   # with the previous frame
        self._prev: Optional[np.ndarray] = None

    def feed(self, frames: np.ndarray) -> None:
        if frames.size == 0:
            return
        bright = frames >= BRIGHT
        dark = frames <= DARK
        near = dark.copy()
        for k in (1, 2):
            near[:, :, k:] |= dark[:, :, :-k]
            near[:, :, :-k] |= dark[:, :, k:]
            near[:, k:, :] |= dark[:, :-k, :]
            near[:, :-k, :] |= dark[:, k:, :]
        mask = bright & near
        n, h, w = mask.shape
        # Densest text-line strip (a short word in one line counts as much as two full lines)
        rows = mask.sum(axis=2).astype(np.float64)                  # (n, h)
        cum = np.concatenate([np.zeros((n, 1)), np.cumsum(rows, axis=1)], axis=1)
        strip = min(STRIP_ROWS, h)
        best = (cum[:, strip:] - cum[:, :-strip]).max(axis=1)
        self.density.extend((best / (strip * w)).tolist())
        cols = mask.sum(axis=1).astype(np.float64)                  # (n, w)
        x = np.arange(w)
        with np.errstate(invalid="ignore", divide="ignore"):
            centroid = (cols * x).sum(axis=1) / cols.sum(axis=1)
        self.centred.extend((np.abs(np.nan_to_num(centroid, nan=0.0) - w / 2) < 0.15 * w).tolist())
        # 2x2 max-pooled masks tolerate compression jitter when comparing frames
        pooled = mask[:, : h // 2 * 2, : w // 2 * 2].reshape(n, h // 2, 2, w // 2, 2).max(axis=(2, 4))
        for i in range(n):
            cur = pooled[i]
            if self._prev is None:
                self.jaccard.append(0.0)
            else:
                union = np.count_nonzero(cur | self._prev)
                self.jaccard.append(np.count_nonzero(cur & self._prev) / union if union else 1.0)
            self._prev = cur

    def threshold(self) -> float:
        if not self.density:
            return 1.0
        low = float(np.percentile(self.density, 20))
        return max(0.004, 2.5 * low + 0.002)

    def intervals(self) -> List[Interval]:
        tau = self.threshold()
        step = 1.0 / self.fps
        out: List[Interval] = []
        begin: Optional[int] = None
        for i, d in enumerate(self.density):
            present = d >= tau and self.centred[i]
            new_text = begin is not None and present and self.jaccard[i] < CHANGE_JACCARD
            if begin is not None and (not present or new_text):
                out.append((begin * step - step / 2, i * step - step / 2))
                begin = None
            if present and begin is None:
                begin = i
        if begin is not None:
            out.append((begin * step - step / 2, len(self.density) * step - step / 2))
        return [(round(max(0.0, a), 3), round(b, 3)) for a, b in out if MIN_DURATION <= b - a <= MAX_DURATION]


def validate_against_speech(
    intervals: Sequence[Interval], speech: Sequence[Interval], duration: float, expected_lines: int,
) -> Tuple[bool, str]:
    """Do these intervals look like the subtitles of this video's dialogue?"""
    if len(intervals) < max(8, int(0.15 * expected_lines)):
        return False, f"only {len(intervals)} text interval(s)"
    covered = sum(b - a for a, b in intervals)
    if duration and covered > 0.75 * duration:
        return False, "text on screen almost all the time"
    padded = sorted((s - 0.5, e + 0.5) for s, e in speech)
    starts = [s for s, _ in padded]
    import bisect

    on_speech = 0.0
    for a, b in intervals:
        k = max(0, bisect.bisect_right(starts, a) - 1)
        for s, e in padded[k:]:
            if s >= b:
                break
            on_speech += max(0.0, min(b, e) - max(a, s))
    ratio = on_speech / covered if covered else 0.0
    if ratio < 0.55:
        return False, f"only {ratio:.0%} of it during speech"
    return True, f"{len(intervals)} intervals, {ratio:.0%} during speech"


async def detect_burned_subtitles(video_path: str, cancel_flag=None, fps: float = FPS) -> BurnedInResult:
    """Decode the subtitle band of the whole video and return the on-screen text intervals."""
    from app.pipeline.stages.audio_stage import get_ffmpeg_bin

    command = [
        get_ffmpeg_bin(), "-v", "error", "-nostats", "-i", str(Path(video_path).resolve()),
        "-map", "0:v:0", "-an", "-sn",
        "-vf", f"fps={fps},crop=iw:ih*0.30:0:ih*0.70,scale={WIDTH}:{HEIGHT},format=gray",
        "-f", "rawvideo", "pipe:1",
    ]
    analyzer = BurnedInAnalyzer(fps)
    frame_size = WIDTH * HEIGHT
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    result = BurnedInResult()
    try:
        while True:
            if cancel_flag is not None and cancel_flag.is_set():
                raise PipelineCancelled()
            try:
                data = await process.stdout.readexactly(frame_size * CHUNK_FRAMES)
            except asyncio.IncompleteReadError as e:
                data = e.partial
            usable = len(data) // frame_size * frame_size
            if usable:
                frames = np.frombuffer(data[:usable], dtype=np.uint8).reshape(-1, HEIGHT, WIDTH)
                await asyncio.to_thread(analyzer.feed, frames)
            if len(data) < frame_size * CHUNK_FRAMES:
                break
        await process.wait()
        if process.returncode not in (0, None):
            err = (await process.stderr.read()).decode(errors="ignore")[-300:]
            result.error = f"ffmpeg exited with {process.returncode}: {err}"
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    result.frames = len(analyzer.density)
    result.threshold = analyzer.threshold()
    result.intervals = analyzer.intervals()
    return result
