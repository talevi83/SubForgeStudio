"""
Local timing alignment of an existing subtitle file to a video (no AI, no network).

The subtitle file is assumed to be *roughly* right for some version of the video. Typical
problems, all handled here:

* a constant delay (the file starts 2.4 s late),
* a different frame rate (made for a 25 fps release, playing at 23.976 fps -> drifts 4%),
* different cuts (an extra recap, a removed scene: the delay jumps at a few points).

Method
------
1. Global mapping  t -> scale * t + offset  from the cross-correlation of "a subtitle is showing"
   with "someone is speaking" (voice activity), tried for the usual frame-rate ratios.
2. Per-cue offsets on top of that mapping by dynamic programming (Viterbi) over a grid of
   offsets: every cue scores how well its shifted interval lies on speech (and, when available,
   on subtitles burned into the picture / an embedded subtitle track, and on text anchors);
   moving the offset a little between neighbours is cheap, a jump (a cut) has a fixed price.
   Isolated wrong anchors are therefore ignored, while a run of consistent evidence moves the path.
3. The caller then snaps each cue to its exact evidence (text anchor / burned-in interval / speech
   onset) - see sync_stage.py.

All times are seconds. Pure numpy; safe to run in a worker thread.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

Interval = Tuple[float, float]

COARSE_RATE = 10            # samples/s for the global cross-correlation
FINE_RATE = 100             # samples/s for the per-cue scores
STEP = 0.1                  # offset grid of the dynamic programming (s)
MAIN_RANGE = 45.0           # the main window searches +-45 s around the global mapping
EXTRA_RANGE = 12.0          # extra windows around other offsets seen in parts of the file
DRIFT_COST = 4.0            # cost per second of offset change between neighbouring cues
SPLIT_PENALTY = 6.0         # cost of a jump (cut) between neighbours more than 1 s apart
SPLIT_PENALTY_CLOSE = 12.0  # ... and between neighbours less than 1 s apart (cuts fall in pauses)
ANCHOR_WEIGHT = 8.0         # peak bonus of a text/screen anchor
ANCHOR_SIGMA = 0.3          # its width (s)
REFERENCE_WEIGHT = 2.0      # weight of overlap with reference subtitles (burned-in / embedded)
CHUNK = 300.0               # cue window (s) used to look for other offsets (different cuts)
RATE_MARGIN = 0.02          # another frame rate must beat "same rate" by 2% (and 3 points) to win
ONSET_WEIGHT = 1.0          # bonus for a line that starts where speech starts (lines outlast speech,
ONSET_SIGMA = 0.15          #   so overlap alone can't tell where the extra length belongs)
ONSET_GAP = 0.25            # a silence this long before speech makes its start an onset

# Frame-rate conversions seen in practice (subtitle made for A fps, video plays at B fps)
FPS_PAIRS = [
    (25.0, 25.0), (25.0, 23.976), (23.976, 25.0), (24.0, 23.976), (23.976, 24.0),
    (25.0, 24.0), (24.0, 25.0), (29.97, 25.0), (25.0, 29.97), (30.0, 29.97), (29.97, 30.0),
    (30.0, 25.0), (25.0, 30.0),
]


@dataclass
class AlignCue:
    start: float
    end: float
    weight: float = 1.0                    # 0 for cues that should not lie on speech (sounds, signs)
    anchor: Optional[float] = None         # wanted start time from text/screen evidence


@dataclass
class AlignResult:
    scale: float
    offset: float
    fps_from: float
    fps_to: float
    starts: List[float]
    ends: List[float]
    deltas: List[float]                    # per-cue offset on top of the global mapping
    speech_ratio: List[float]              # share of each mapped cue that lies on speech
    anchor_used: List[bool]
    jumps: List[Tuple[float, float]] = field(default_factory=list)  # (time, change of offset)
    global_score: float = 0.0


# --------------------------------------------------------------------------- signals

def _binary(intervals: Sequence[Interval], length: int, rate: float, scale: float = 1.0) -> np.ndarray:
    sig = np.zeros(length, dtype=np.float32)
    for s, e in intervals:
        a = max(0, int(round(s * scale * rate)))
        b = min(length, int(round(e * scale * rate)))
        if b > a:
            sig[a:b] = 1.0
    return sig


class _Integral:
    """F(t) = integral of a +1 (present) / -1 (absent) signal; -1 outside [0, T]."""

    def __init__(self, intervals: Sequence[Interval], total: float, rate: int = FINE_RATE):
        self.rate = rate
        self.total = total
        n = int(np.ceil(total * rate)) + 1
        sig = _binary(intervals, n, rate) * 2.0 - 1.0
        self.cum = np.concatenate([[0.0], np.cumsum(sig, dtype=np.float64) / rate])
        self.grid = np.arange(len(self.cum), dtype=np.float64) / rate
        self.end_value = float(self.cum[-1])
        self.end_time = float(self.grid[-1])

    def __call__(self, t: np.ndarray) -> np.ndarray:
        inside = np.interp(t, self.grid, self.cum)
        before = -t                                              # t < 0: absent
        after = self.end_value - (t - self.end_time)             # t > T: absent
        return np.where(t < 0, before, np.where(t > self.end_time, after, inside))

    def overlap(self, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
        """(time on the signal) - (time off it) for each [start, end]."""
        return self(ends) - self(starts)


class _Onsets:
    """Smooth bump (peak 1) at every speech onset - a start after a silence of ONSET_GAP."""

    def __init__(self, speech: Sequence[Interval], total: float, rate: int = FINE_RATE):
        n = int(np.ceil(total * rate)) + 1
        self.rate = rate
        self.curve = np.zeros(n, dtype=np.float64)
        onsets = [s for k, (s, _) in enumerate(speech) if k == 0 or s - speech[k - 1][1] >= ONSET_GAP]
        half = int(4 * ONSET_SIGMA * rate)
        kernel = np.exp(-0.5 * (np.arange(-half, half + 1) / (ONSET_SIGMA * rate)) ** 2)
        for t in onsets:
            c = int(round(t * rate))
            a, b = max(0, c - half), min(n, c + half + 1)
            if a < b:
                self.curve[a:b] = np.maximum(self.curve[a:b], kernel[a - (c - half): b - (c - half)])
        self.grid = np.arange(n, dtype=np.float64) / rate
        self.onsets = onsets

    def __call__(self, t: np.ndarray) -> np.ndarray:
        return np.interp(t, self.grid, self.curve, left=0.0, right=0.0)


def merge_intervals(intervals: Sequence[Interval], gap: float = 0.0) -> List[Interval]:
    out: List[List[float]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(a, b) for a, b in out]


# --------------------------------------------------------------------------- global mapping

def _correlate(speech_sig: np.ndarray, cue_sig: np.ndarray) -> np.ndarray:
    """score[k] = sum_n speech_pm[n + k - len(cue)] * cue[n] for every shift; outside = -1."""
    pad = len(cue_sig)
    s = np.concatenate([-np.ones(pad, np.float32), speech_sig * 2 - 1, -np.ones(pad, np.float32)])
    n = 1 << int(np.ceil(np.log2(len(s) + len(cue_sig))))
    spec = np.fft.rfft(s, n) * np.conj(np.fft.rfft(cue_sig, n))
    corr = np.fft.irfft(spec, n)[: len(s) - len(cue_sig) + 1]
    return corr  # index k <-> offset (k - pad) / rate


def estimate_global(
    cues: Sequence[AlignCue],
    speech: Sequence[Interval],
    duration: float,
    fps_pairs: Sequence[Tuple[float, float]] = FPS_PAIRS,
) -> List[Tuple[float, float, float, float, float]]:
    """Candidate (score, scale, offset, fps_from, fps_to), best first (one per scale)."""
    spoken = [(c.start, c.end) for c in cues if c.weight > 0] or [(c.start, c.end) for c in cues]
    total_cue = sum(e - s for s, e in spoken) or 1.0
    length = int(np.ceil(max(duration, 1.0) * COARSE_RATE)) + 1
    speech_sig = _binary(speech, length, COARSE_RATE)
    results = []
    seen = set()
    for fps_from, fps_to in fps_pairs:
        scale = round(fps_from / fps_to, 6)
        if scale in seen:
            continue
        seen.add(scale)
        cue_len = int(np.ceil(max(e for _, e in spoken) * scale * COARSE_RATE)) + 1
        cue_sig = _binary(spoken, cue_len, COARSE_RATE, scale)
        corr = _correlate(speech_sig, cue_sig)
        k = int(np.argmax(corr))
        offset = (k - cue_len) / COARSE_RATE
        results.append((float(corr[k]) / COARSE_RATE / total_cue, scale, offset, fps_from, fps_to))
    results.sort(key=lambda r: -r[0])
    return results


def chunk_offsets(
    cues: Sequence[AlignCue], speech: Sequence[Interval], duration: float, scale: float, offset: float,
) -> List[float]:
    """Offsets (relative to the global one) that fit some part of the file clearly better than the
    global offset does - evidence of different cuts. Used to add search windows."""
    length = int(np.ceil(max(duration, 1.0) * COARSE_RATE)) + 1
    speech_sig = _binary(speech, length, COARSE_RATE)
    spoken = [c for c in cues if c.weight > 0]
    if not spoken:
        return []
    extra: List[float] = []
    t0 = spoken[0].start
    while t0 <= spoken[-1].end:
        part = [(c.start * scale, c.end * scale) for c in spoken if t0 <= c.start < t0 + CHUNK]
        t0 += CHUNK / 2
        dur = sum(e - s for s, e in part)
        if dur < 20.0:
            continue
        base = part[0][0]
        shifted = [(s - base, e - base) for s, e in part]
        cue_len = int(np.ceil(shifted[-1][1] * COARSE_RATE)) + 1
        corr = _correlate(speech_sig, _binary(shifted, cue_len, COARSE_RATE))
        k = int(np.argmax(corr))
        best_offset = (k - cue_len) / COARSE_RATE - base           # absolute offset for this chunk
        k_global = int(round((offset + base) * COARSE_RATE)) + cue_len
        at_global = corr[k_global] if 0 <= k_global < len(corr) else -dur * COARSE_RATE
        best, glob = corr[k] / COARSE_RATE, at_global / COARSE_RATE
        rel = best_offset - offset
        if abs(rel) > MAIN_RANGE - 2 and best > 0.35 * dur and best - glob > 0.25 * dur:
            if all(abs(rel - x) > EXTRA_RANGE for x in extra):
                extra.append(round(rel, 1))
    return extra


# --------------------------------------------------------------------------- dynamic programming

def _max_l1(prev: np.ndarray, w: float) -> np.ndarray:
    """out[j] = max_k prev[k] - w * |j - k|   (exact, O(n))."""
    idx = np.arange(len(prev), dtype=np.float64) * w
    fwd = np.maximum.accumulate(prev + idx) - idx
    bwd = np.maximum.accumulate((prev - idx)[::-1])[::-1] + idx
    return np.maximum(fwd, bwd)


def align(
    cues: Sequence[AlignCue],
    speech: Sequence[Interval],
    duration: float,
    reference: Sequence[Interval] = (),
    global_candidates: int = 3,
) -> AlignResult:
    """Map every cue to the video timeline. `cues` must be in file order."""
    if not cues:
        raise ValueError("No cues to align")
    speech = merge_intervals(speech)
    reference = merge_intervals(reference)
    total = max(duration, max(c.end for c in cues)) + 5.0
    speech_f = _Integral(speech, total)
    onset_f = _Onsets(speech, total)
    ref_f = _Integral(reference, total) if reference else None

    ranked = estimate_global(cues, speech, total)
    candidates = ranked[:global_candidates]
    if not any(abs(c[1] - 1.0) < 1e-9 for c in candidates):
        candidates.append(next(c for c in ranked if abs(c[1] - 1.0) < 1e-9))
    runs = []
    for score, scale, offset, fps_from, fps_to in candidates:
        result, value = _viterbi(cues, speech_f, onset_f, ref_f, total, scale, offset, speech)
        result.fps_from, result.fps_to, result.global_score = fps_from, fps_to, score
        runs.append((value, result))
    top = max(v for v, _ in runs)
    close = [r for r in runs if top - r[0] < max(3.0, RATE_MARGIN * abs(top))]
    # Most files have the right frame rate: another rate has to be clearly better. Between close
    # rates (25->24 vs 25->23.976 differ by 0.1%; the offsets absorb that) the correlation decides.
    same = [r for r in close if abs(r[1].scale - 1.0) < 1e-9]
    if same:
        return same[0][1]
    return max(close, key=lambda r: r[1].global_score)[1]


def _viterbi(cues, speech_f: _Integral, onset_f: "_Onsets", ref_f: Optional[_Integral], total: float,
             scale: float, offset: float, speech: Sequence[Interval]) -> Tuple[AlignResult, float]:
    windows = [(-MAIN_RANGE, MAIN_RANGE)]
    for rel in chunk_offsets(cues, speech, total, scale, offset):
        windows.append((rel - EXTRA_RANGE, rel + EXTRA_RANGE))
    grids = [np.round(np.arange(a, b + STEP / 2, STEP), 3) for a, b in windows]
    deltas = np.concatenate(grids)
    bounds = np.cumsum([0] + [len(g) for g in grids])

    starts = np.array([c.start * scale + offset for c in cues])
    ends = np.array([c.end * scale + offset for c in cues])
    weights = np.array([c.weight for c in cues])
    n, m = len(cues), len(deltas)

    # Evidence per cue and offset
    s_mat = starts[:, None] + deltas[None, :]
    e_mat = ends[:, None] + deltas[None, :]
    scores = speech_f.overlap(s_mat, e_mat) * weights[:, None]
    scores += ONSET_WEIGHT * onset_f(s_mat) * weights[:, None]
    if ref_f is not None:
        scores += REFERENCE_WEIGHT * ref_f.overlap(s_mat, e_mat) * (weights[:, None] > 0)
    for i, c in enumerate(cues):
        if c.anchor is not None:
            want = c.anchor - starts[i]
            scores[i] += ANCHOR_WEIGHT * np.exp(-0.5 * ((deltas - want) / ANCHOR_SIGMA) ** 2)
    # Tiny preference for the global mapping, so empty stretches don't wander
    scores -= 0.002 * np.abs(deltas)[None, :]

    gaps = np.concatenate([[np.inf], starts[1:] - ends[:-1]])
    w = DRIFT_COST * STEP
    dp = np.empty((n, m))
    dp[0] = scores[0]
    for i in range(1, n):
        prev = dp[i - 1]
        lin = np.full(m, -np.inf)
        for a, b in zip(bounds[:-1], bounds[1:]):
            lin[a:b] = _max_l1(prev[a:b], w)
        split = prev.max() - (SPLIT_PENALTY if gaps[i] >= 1.0 else SPLIT_PENALTY_CLOSE)
        dp[i] = scores[i] + np.maximum(lin, split)

    # Backtrack
    path = np.empty(n, dtype=int)
    path[-1] = int(np.argmax(dp[-1]))
    window_of = np.searchsorted(bounds, np.arange(m), side="right") - 1
    for i in range(n - 1, 0, -1):
        j = path[i]
        prev = dp[i - 1]
        a, b = bounds[window_of[j]], bounds[window_of[j] + 1]
        cand = prev[a:b] - w * np.abs(np.arange(a, b) - j)
        k = a + int(np.argmax(cand))
        split_k = int(np.argmax(prev))
        split_value = prev[split_k] - (SPLIT_PENALTY if gaps[i] >= 1.0 else SPLIT_PENALTY_CLOSE)
        path[i - 1] = k if cand[k - a] >= split_value else split_k

    chosen = deltas[path]
    new_starts = starts + chosen
    new_ends = ends + chosen
    durations = np.maximum(new_ends - new_starts, 1e-3)
    ratio = (speech_f.overlap(new_starts, new_ends) / durations + 1) / 2
    anchor_used = [
        c.anchor is not None and abs(c.anchor - new_starts[i]) <= 3 * ANCHOR_SIGMA for i, c in enumerate(cues)
    ]
    jumps = [
        (round(float(new_starts[i]), 2), round(float(chosen[i] - chosen[i - 1]), 2))
        for i in range(1, n) if abs(chosen[i] - chosen[i - 1]) >= 1.5
    ]
    result = AlignResult(
        scale=scale, offset=offset, fps_from=0.0, fps_to=0.0,
        starts=[round(float(x), 3) for x in new_starts],
        ends=[round(float(x), 3) for x in new_ends],
        deltas=[round(float(x), 3) for x in chosen],
        speech_ratio=[round(float(x), 3) for x in ratio],
        anchor_used=anchor_used,
        jumps=jumps,
    )
    return result, float(dp[-1].max())


def describe(result: AlignResult) -> Dict:
    """Short summary for the job log / report."""
    fps = (f"{result.fps_from:g} -> {result.fps_to:g} fps" if abs(result.scale - 1.0) > 1e-6 else "same frame rate")
    deltas = np.array(result.deltas) if result.deltas else np.zeros(1)
    return {
        "scale": result.scale,
        "frame_rate": fps,
        "offset": round(result.offset + float(np.median(deltas)), 2),
        "jumps": result.jumps[:20],
        "jump_count": len(result.jumps),
    }
