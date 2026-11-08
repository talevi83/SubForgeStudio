"""
Video frame sampling for the on-screen text pass: low-res frames plus grayscale thumbnails
(scene cuts, chyron overlays, static cards), the smart selection of frames worth reading, and
bottom-third crops for burned-in subtitles.
"""
import asyncio
import bisect
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.core.config import settings
from app.pipeline.context import PipelineCancelled, PipelineContext
from app.pipeline.stages.audio_stage import get_ffmpeg_bin

# Grayscale thumbnails used to measure frame-to-frame change (scene cuts / chyron overlays / static cards)
_THUMB_W, _THUMB_H = 64, 36
_SCENE_CHANGE = 0.12      # mean absolute pixel difference (0..1) that counts as a cut
_OVERLAY_CHANGE = 0.065   # localized change from a lower-third chyron or title appearing mid-shot
_STATIC = 0.015           # below this two frames are considered identical

# Burned-in subtitles for foreign speech: bottom third of the frame, sampled every 0.5s
_SUB_INTERVAL = 0.5
_SUB_CROP = "crop=iw:ih*0.32:0:ih*0.68,scale=768:-2"   # one 768px tile -> cheapest image tokens


@dataclass
class Frame:
    time: float
    path: Path
    change: float = 0.0                    # difference from the previous sampled frame (0..1)
    static_until: float = 0.0              # the picture stays the same until this time
    overlay_change: Optional[float] = None  # change in chyron/title bands or scene cut


def frames_dir(job_id: str) -> Path:
    return settings.storage_dir / "frames" / job_id


def remove_frames(job_id: str) -> None:
    shutil.rmtree(frames_dir(job_id), ignore_errors=True)


async def sample_frames(ctx: PipelineContext, interval: Optional[float] = None) -> List[Frame]:
    """
    One ffmpeg pass writes 640px JPEGs (for Gemini) and 32x18 grayscale thumbnails (for scene
    change / static detection). Runs in the background while Whisper transcribes.
    """
    interval = interval or settings.onscreen_frame_interval
    out_dir = frames_dir(ctx.job_id)
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    thumbs_path = out_dir / "thumbs.raw"

    # Use GPU hardware decoding (-hwaccel auto -> NVDEC/D3D11VA) whenever available.
    # NOTE: Do NOT use `-discard nokey` here — in Web-DL H.264/HEVC files keyframes can be
    # 5-10 seconds apart, causing `-discard nokey` to completely skip lower-third chyrons
    # (e.g., speaker names/titles) and brief on-screen text that appear between keyframes.
    input_flags = ["-hwaccel", "auto"]

    command = [
        get_ffmpeg_bin(), "-v", "error", "-nostats", "-y",
        *input_flags,
        "-i", str(Path(ctx.video_path).resolve()),
        "-filter_complex",
        f"[0:v:0]fps=1/{interval},split=2[big][small];"
        f"[big]scale=640:-2[o1];[small]scale={_THUMB_W}:{_THUMB_H},format=gray[o2]",
        "-map", "[o1]", "-q:v", "5", str(out_dir / "f_%06d.jpg"),
        "-map", "[o2]", "-f", "rawvideo", str(thumbs_path),
    ]
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        while True:
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
                break
            except asyncio.TimeoutError:
                if ctx.is_cancelled:
                    raise PipelineCancelled()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0:
        err = (await process.stderr.read()).decode(errors="ignore") if process.stderr else ""
        raise RuntimeError(f"Frame sampling failed: {err[-500:]}")

    jpgs = sorted(out_dir.glob("f_*.jpg"))
    thumbs = thumbs_path.read_bytes() if thumbs_path.exists() else b""
    size = _THUMB_W * _THUMB_H
    top_k = max(1, size // 20)  # ~5% of frame area (catches lower-third chyron / title overlays)
    chyron_start = 12 * _THUMB_W
    chyron_end = 16 * _THUMB_W
    chyron_len = max(1, chyron_end - chyron_start)
    frames: List[Frame] = []
    prev: Optional[bytes] = None
    for i, jpg in enumerate(jpgs):
        thumb = thumbs[i * size:(i + 1) * size]
        if prev is None or len(thumb) < size:
            change = 1.0
            overlay_change = 1.0
        else:
            raw_diffs = [abs(a - b) for a, b in zip(thumb, prev)]
            global_change = sum(raw_diffs) / (size * 255)
            chyron_band_change = sum(raw_diffs[chyron_start:chyron_end]) / (chyron_len * 255)
            diffs = sorted(raw_diffs, reverse=True)
            top_zone_change = sum(diffs[:top_k]) / (top_k * 255)
            # A lower-third chyron or title overlay appearing over a static talking head changes
            # ~4-8% of the frame strongly while keeping global_change small; blending the top-5%
            # localized change ensures chyron fade-ins register above _SCENE_CHANGE (0.12).
            change = max(global_change, top_zone_change * 0.75)
            # For static duration tracking, ignore mid-frame speaker head movement unless the
            # whole shot cuts (global_change >= _SCENE_CHANGE) or the lower-third band changes.
            overlay_change = global_change if global_change >= _SCENE_CHANGE else chyron_band_change
        frames.append(Frame(time=round(i * interval, 3), path=jpg, change=change, overlay_change=overlay_change))
        prev = thumb if len(thumb) == size else prev
    _fill_static_runs(frames, interval)
    if getattr(ctx, "skip_end_credits", True) and thumbs:
        from app.pipeline.credits_detect import detect_visual_credit_spans

        ctx.visual_credit_spans = detect_visual_credit_spans(
            thumbs, interval, ctx.duration or (len(frames) * interval), width=_THUMB_W, height=_THUMB_H
        )
    return frames


def _fill_static_runs(frames: List[Frame], interval: float) -> None:
    """For each frame, how long the picture or overlay stays on screen before a cut or graphic fade-out."""
    for i in range(len(frames) - 1, -1, -1):
        nxt = frames[i + 1] if i + 1 < len(frames) else None
        nxt_diff = (nxt.overlay_change if nxt.overlay_change is not None else nxt.change) if nxt is not None else 1.0
        if nxt is not None and nxt_diff < _OVERLAY_CHANGE:
            frames[i].static_until = nxt.static_until
        else:
            frames[i].static_until = frames[i].time + interval * 0.5


def _in_speech(t: float, starts: Sequence[float], speech: Sequence[Tuple[float, float]], pad: float = 0.25) -> bool:
    """True if t falls inside (or within `pad` of) a spoken segment. `speech` must be sorted."""
    i = bisect.bisect_right(starts, t + pad) - 1
    # Segments can overlap slightly, so also look at the previous one
    return any(speech[j][1] + pad >= t for j in (i, i - 1) if 0 <= j < len(speech))


def select_frames(
    frames: Sequence[Frame],
    speech: Sequence[Tuple[float, float]],
    budget: int,
) -> List[Frame]:
    """
    Pick the frames worth sending to Gemini:
      1. frames right after a scene cut (and the ones shortly following it, where lower-third chyrons settle)
      2. frames inside speech gaps
      3. localized overlay transitions during speech (when a chyron or graphic appears mid-shot)
    Within a static run (same picture) only the first frame is kept — its `static_until`
    covers the rest. If over budget, cuts in gaps win, then speech cuts and gaps share the
    remaining budget so lower-third speaker chyrons during dialogue are never starved.
    """
    speech = sorted(speech)
    starts = [s for s, _ in speech]
    extended_post_cut = len(frames) > 20
    picked: Dict[int, int] = {}  # frame index -> priority (lower = more important)
    for i, f in enumerate(frames):
        is_cut = f.change >= _SCENE_CHANGE
        after_cut = (
            (i > 0 and frames[i - 1].change >= _SCENE_CHANGE)
            or (extended_post_cut and i > 1 and frames[i - 2].change >= _SCENE_CHANGE)
            or (extended_post_cut and i > 2 and frames[i - 3].change >= _SCENE_CHANGE)
        )
        overlay_shift = extended_post_cut and (
            f.change >= _OVERLAY_CHANGE
            or (i > 0 and frames[i - 1].change >= _OVERLAY_CHANGE)
        )
        static_repeat = i > 0 and f.change < _STATIC
        if static_repeat:
            continue
        gap = not _in_speech(f.time, starts, speech)
        if (is_cut or after_cut) and gap:
            picked[i] = 0
        elif gap:
            picked[i] = 1
        elif is_cut or after_cut or overlay_shift:
            picked[i] = 2
        elif extended_post_cut and f.change >= _STATIC * 2.0:
            picked[i] = 3

    chosen: List[int] = []
    tiers = {p: [i for i, pr in picked.items() if pr == p] for p in (0, 1, 2, 3)}

    def _take(tier: List[int], limit: int) -> List[int]:
        if limit <= 0 or not tier:
            return []
        if len(tier) <= limit:
            return list(tier)
        step = len(tier) / limit
        return [tier[int(k * step)] for k in range(limit)]

    # Always prioritize cuts inside speech gaps (title cards / location cards).
    chosen.extend(_take(tiers[0], budget))
    room = budget - len(chosen)
    if room > 0:
        if budget >= 10 and tiers[1] and tiers[2]:
            # Reserve up to half of the remaining budget for speech cuts / post-cut frames
            # so lower-third speaker chyrons during dialogue are never starved by long silent scenes.
            speech_quota = min(len(tiers[2]), max(1, room // 2))
            gap_quota = min(len(tiers[1]), room - speech_quota)
            if gap_quota + speech_quota < room:
                speech_quota = min(len(tiers[2]), room - gap_quota)
                gap_quota = min(len(tiers[1]), room - speech_quota)
            chosen.extend(_take(tiers[1], gap_quota))
            chosen.extend(_take(tiers[2], speech_quota))
        else:
            for priority in (1, 2):
                rem = budget - len(chosen)
                if rem <= 0:
                    break
                chosen.extend(_take(tiers[priority], rem))
        rem = budget - len(chosen)
        if rem > 0 and tiers[3]:
            chosen.extend(_take(tiers[3], rem))
    return [frames[i] for i in sorted(chosen)]


async def _run_cancellable(command: List[str], ctx: PipelineContext) -> None:
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        while True:
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
                break
            except asyncio.TimeoutError:
                if ctx.is_cancelled:
                    raise PipelineCancelled()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0:
        err = (await process.stderr.read()).decode(errors="ignore") if process.stderr else ""
        raise RuntimeError(f"ffmpeg failed: {err[-500:]}")


async def sample_subtitle_crops(
    ctx: PipelineContext, start: float, end: float, tag: str, interval: float = _SUB_INTERVAL
) -> List[Frame]:
    """Bottom-third crops between start and end (seconds), one every `interval` seconds."""
    if end - start < interval:
        return []
    out_dir = frames_dir(ctx.job_id) / "subs"
    out_dir.mkdir(parents=True, exist_ok=True)
    await _run_cancellable([
        get_ffmpeg_bin(), "-v", "error", "-nostats", "-y",
        "-hwaccel", "auto",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
        "-i", str(Path(ctx.video_path).resolve()),
        "-map", "0:v:0", "-an", "-sn",
        "-vf", f"fps=1/{interval},{_SUB_CROP}",
        "-q:v", "4", str(out_dir / f"{tag}_%05d.jpg"),
    ], ctx)
    files = sorted(out_dir.glob(f"{tag}_*.jpg"))
    return [
        Frame(time=round(start + i * interval, 3), path=p, change=1.0, static_until=round(start + i * interval, 3))
        for i, p in enumerate(files)
    ]
