"""
On-screen text detection ("forced narratives"): time jumps, location/date cards, HUD headers
and plot-critical signs that Whisper can never hear.

Layer 1 — embedded forced subtitle track:
    If the container carries a text-based forced/"signs" subtitle stream, extract it with
    ffmpeg and turn its cues into ON_SCREEN segments. Cheap and exact, so when it exists the
    frame scan is skipped.

Layer 2 — frame sampling + Gemini Vision:
    ffmpeg samples low-res frames (default every 2s) while Whisper is still transcribing.
    Frames are prioritised when they fall in speech gaps or right after scene changes (where
    "One week later..." cards usually live), sent to Gemini in batches with their timestamps,
    and consecutive detections of the same text are merged into one timed, bracketed segment.
"""
import asyncio
import bisect
import difflib
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import settings
from app.models.db_models import SegmentType
from app.models.schemas import SubtitleStreamInfo
from app.pipeline.context import PipelineCancelled, PipelineContext
from app.pipeline.foreign_speech import READ_MARGIN
from app.pipeline.stages.audio_stage import get_ffmpeg_bin
from app.pipeline.stages.base import PipelineStage, ProgressCallback

logger = logging.getLogger(__name__)

# Tiny grayscale thumbnails used to measure frame-to-frame change (scene cuts / static cards)
_THUMB_W, _THUMB_H = 32, 18
_SCENE_CHANGE = 0.12      # mean absolute pixel difference (0..1) that counts as a cut
_STATIC = 0.015           # below this two frames are considered identical
_ONSCREEN_BUFFER = 1.0    # seconds added after the last frame a text was seen in
_ONSCREEN_MIN_DURATION = 1.5
_ONSCREEN_REPEAT_WINDOW = 180.0  # the same sign/title seen again within this time is not shown again
_TAG_MAX_CHARS = 6               # a lone short word ("EAGLE", "LIVE") is a HUD/callsign label, not a title
_TAG_MAX_UPPER = 12              # a lone ALL-CAPS word ("VULTURE") is a callsign even when longer
_GEMINI_CONCURRENCY = 3

# Burned-in subtitles for foreign speech: bottom third of the frame, sampled every 0.5s
_SUB_INTERVAL = 0.5
_SUB_CROP = "crop=iw:ih*0.32:0:ih*0.68,scale=768:-2"   # one 768px tile -> cheapest image tokens
_SUB_MAX_FRAMES = 1500        # beyond this the interval is stretched
_SUB_MARGIN = READ_MARGIN     # always read this far before/after the foreign speech: replies and
                              # short lines are often missed by the speech detector
_SUB_EDGE = 3.0               # a subtitle this close to the scanned edge may continue past it...
_SUB_EXTEND = 6.0             # ...so look this much further
_SUB_MAX_EXTENSIONS = 3


# ---------------------------------------------------------------------------
# Layer 1: embedded forced subtitle tracks
# ---------------------------------------------------------------------------

def pick_forced_track(streams: Sequence[SubtitleStreamInfo]) -> Optional[SubtitleStreamInfo]:
    """The best text-based forced/"signs" subtitle stream, if any."""
    candidates = [s for s in streams if s.looks_forced and s.text_based]
    if not candidates:
        return None
    # Prefer the explicit forced disposition, then English, then the first one
    candidates.sort(key=lambda s: (not s.forced, (s.language or "") not in ("eng", "en"), s.index))
    return candidates[0]


_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
_TAGS = re.compile(r"<[^>]+>|\{\\[^}]*\}")


def _ts(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_srt(content: str) -> List[Dict[str, Any]]:
    """Parse SRT text into [{start_time, end_time, text}] (formatting tags removed)."""
    cues = []
    blocks = re.split(r"\r?\n\s*\r?\n", content.replace("﻿", "").strip())
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        for i, line in enumerate(lines):
            match = _SRT_TIME.search(line)
            if not match:
                continue
            text = "\n".join(_TAGS.sub("", l).strip() for l in lines[i + 1:]).strip()
            if text:
                g = match.groups()
                cues.append({"start_time": _ts(*g[:4]), "end_time": _ts(*g[4:]), "text": text})
            break
    return cues


async def extract_forced_track(video_path: str, stream: SubtitleStreamInfo) -> List[Dict[str, Any]]:
    """Convert one embedded subtitle stream to ON_SCREEN segment dicts via ffmpeg."""
    process = await asyncio.create_subprocess_exec(
        get_ffmpeg_bin(), "-v", "error", "-i", str(Path(video_path).resolve()),
        "-map", f"0:s:{stream.index}", "-f", "srt", "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"Could not extract subtitle stream {stream.index}: {stderr.decode(errors='ignore')[-500:]}")
    return [
        make_onscreen_segment(c["start_time"], c["end_time"], c["text"], bracket=False)
        for c in parse_srt(stdout.decode("utf-8", errors="ignore"))
    ]


# ---------------------------------------------------------------------------
# Segment helpers
# ---------------------------------------------------------------------------

def format_onscreen_text(text: str) -> str:
    """'One week later...' -> '[One week later...]'; multi-line cards joined with an em dash."""
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    lines = [l.strip("[]").strip() for l in lines if l.strip("[] ")]
    return f"[{' — '.join(lines)}]" if lines else ""


def make_onscreen_segment(start: float, end: float, text: str, bracket: bool = True) -> Dict[str, Any]:
    clean = format_onscreen_text(text) if bracket else text.strip()
    return {
        "start_time": round(start, 3),
        "end_time": round(max(end, start + 0.5), 3),
        "original_text": clean,
        "translated_text": None,
        "translation_failed": False,
        "segment_type": SegmentType.ON_SCREEN.value,
        "confidence": 1.0,
    }


# ---------------------------------------------------------------------------
# Layer 2: frame sampling, smart selection, Gemini Vision, de-duplication
# ---------------------------------------------------------------------------

@dataclass
class Frame:
    time: float
    path: Path
    change: float = 0.0       # difference from the previous sampled frame (0..1)
    static_until: float = 0.0  # the picture stays the same until this time


@dataclass
class FrameDetection:
    time: float
    text: str
    until: Optional[float] = None  # last moment the same picture was on screen


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

    # Use GPU hardware decoding (-hwaccel auto -> NVDEC/D3D11VA) whenever available, and for
    # real episodes (>= 60s) discard non-keyframe packets at the demuxer so high-FPS / AV1
    # videos do not decode 99% of intermediate frames on CPU just to drop them in fps=1/2.
    input_flags = ["-hwaccel", "auto"]
    if ctx.duration <= 0 or ctx.duration >= 60.0:
        input_flags += ["-discard", "nokey"]

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
    frames: List[Frame] = []
    prev: Optional[bytes] = None
    for i, jpg in enumerate(jpgs):
        thumb = thumbs[i * size:(i + 1) * size]
        change = 1.0 if prev is None or len(thumb) < size else (
            sum(abs(a - b) for a, b in zip(thumb, prev)) / (size * 255)
        )
        frames.append(Frame(time=round(i * interval, 3), path=jpg, change=change))
        prev = thumb if len(thumb) == size else prev
    _fill_static_runs(frames, interval)
    return frames


def _fill_static_runs(frames: List[Frame], interval: float) -> None:
    """For each frame, how long the picture stays (nearly) unchanged afterwards."""
    for i in range(len(frames) - 1, -1, -1):
        nxt = frames[i + 1] if i + 1 < len(frames) else None
        if nxt is not None and nxt.change < _STATIC:
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
      1. frames right after a scene cut (and the one following it)
      2. frames inside speech gaps
    Within a static run (same picture) only the first frame is kept — its `static_until`
    covers the rest. If over budget, cuts in gaps win, then gaps, then cuts during speech,
    spread evenly across the episode.
    """
    speech = sorted(speech)
    starts = [s for s, _ in speech]
    picked: Dict[int, int] = {}  # frame index -> priority (lower = more important)
    for i, f in enumerate(frames):
        is_cut = f.change >= _SCENE_CHANGE
        after_cut = i > 0 and frames[i - 1].change >= _SCENE_CHANGE
        static_repeat = i > 0 and f.change < _STATIC
        if static_repeat:
            continue
        gap = not _in_speech(f.time, starts, speech)
        if (is_cut or after_cut) and gap:
            picked[i] = 0
        elif gap:
            picked[i] = 1
        elif is_cut or after_cut:
            picked[i] = 2

    chosen: List[int] = []
    for priority in (0, 1, 2):
        tier = [i for i, p in picked.items() if p == priority]
        room = budget - len(chosen)
        if room <= 0:
            break
        if len(tier) > room:
            step = len(tier) / room
            tier = [tier[int(k * step)] for k in range(room)]
        chosen.extend(tier)
    return [frames[i] for i in sorted(chosen)]


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _same_text(a: str, b: str, threshold: float = 0.85) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= threshold


def merge_frame_detections(
    detections: Sequence[FrameDetection],
    frame_interval: float = 2.0,
    buffer: float = _ONSCREEN_BUFFER,
    min_duration: float = _ONSCREEN_MIN_DURATION,
    bracket: bool = True,
    segment_type: str = SegmentType.ON_SCREEN.value,
    drop_tags: bool = False,
    repeat_window: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Collapse consecutive frames showing the same text into one ON_SCREEN segment:
    start = first appearance, end = last appearance (+ static run) + buffer, at least
    min_duration long, text in brackets. Slight OCR variations ("One Week Later" vs
    "One week later...") count as the same text.

    For narrative titles (not burned-in subtitles) `drop_tags` discards lone short words
    (HUD labels, unit callsigns) and `repeat_window` keeps a sign that was already shown
    from being repeated when the camera returns to it shortly after.
    """
    max_gap = frame_interval * 2.5
    groups: List[Dict[str, Any]] = []
    for det in sorted(detections, key=lambda d: d.time):
        if not det.text or not det.text.strip():
            continue
        if not re.search(r"[^\W\d_]", det.text):
            continue  # bare numbers / clocks ("151", "00:35") are not readable text
        if drop_tags and _is_tag(det.text):
            logger.info(f"On-screen text at {det.time:.1f}s dropped as a label: {det.text.strip()!r}")
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
    for g in groups:
        if repeat_window and any(
            g["start"] - p["start"] <= repeat_window and _same_text(p["text"], g["text"]) for p in shown
        ):
            logger.info(f"On-screen text at {g['start']:.1f}s already shown, not repeated: {g['text']!r}")
            continue
        shown.append(g)
        end = max(g["last"] + buffer, g["start"] + min_duration)
        seg = make_onscreen_segment(g["start"], end, g["text"], bracket=bracket)
        seg["segment_type"] = segment_type
        if seg["original_text"]:
            segments.append(seg)
    return segments


def _is_tag(text: str) -> bool:
    """One short word with no digits: a HUD label or callsign rather than a title card.
    An ALL-CAPS single word ("VULTURE") is a callsign even when a bit longer; a capitalized
    one ("Siberia") may be a location title and is kept."""
    words = text.split()
    if len(words) != 1 or any(c.isdigit() for c in words[0]):
        return False
    bare = _norm(words[0])
    if len(bare) <= _TAG_MAX_CHARS:
        return True
    return words[0].isupper() and len(bare) <= _TAG_MAX_UPPER


VISION_SYSTEM_INSTRUCTION = """You read video frames from a movie or TV episode for a subtitle editor.
For each frame, report ONLY narrative on-screen text the viewer is meant to read:
- time jumps ("One week later...", "36 Hours Earlier")
- location / date / time stamps ("Damascus, Syria", "March 3, 2014 - 0400 hours")
- tactical HUD headers, mission titles or timestamps
- plot-critical signs, documents, screens or text messages shown to the viewer
- establishing shots framed on a sign that names the place, facility, ship or unit
  ("NAVAL HOSPITAL GUANTANAMO BAY", "JTF-GTMO FUEL DEPOT") — report the sign text
- close-ups the camera deliberately frames on a warning plate or screen message
  ("HALON GAS SYSTEM", "SYSTEM ERROR", "ACCESS DENIED")

Strictly IGNORE: channel watermarks and network logos, hardcoded dialogue subtitles, opening or
closing credits (actor/crew names, titles of the show), and incidental background text (store
signs, posters, labels, license plates) that is not the focus of the shot. Also ignore hull
numbers and other bare numbers, running clocks/countdowns, equipment brand or gauge labels
(e.g. "SCOTT", "PSI X 100"), and one-word HUD/radio labels or unit callsigns ("EAGLE", "LIVE", "REC").

Copy the text exactly as shown (keep its language and line breaks). Use the timestamp given before
each frame. Frames without qualifying text must be omitted. Return an empty list if none qualify."""


SUBTITLE_INSTRUCTION = """Each image is the bottom part of a frame from a movie or TV episode, cropped where subtitles appear.
If the image shows a burned-in subtitle — dialogue text overlaid by the film makers, typically translating
speech in a foreign language — copy that subtitle text exactly as shown, keeping its line breaks and any
leading dialogue dashes. Do NOT translate or correct it.
Ignore channel logos, watermarks, credits, and location/date titles. Use the timestamp given before each
image. Omit images without a subtitle. Return an empty list if none have one."""


async def _gemini_detect(
    client: Any,
    model: str,
    batch: Sequence[Frame],
    thinking: str = "default",
    resolution: str = "medium",
    usage: Any = None,
    instruction: str = VISION_SYSTEM_INSTRUCTION,
    stage: str = "onscreen",
    flex: bool = False,
    flex_state: Any = None,
) -> List[FrameDetection]:
    from google.genai import types
    from app.core.model_catalog import default_temperature, media_resolution, thinking_config
    from pydantic import BaseModel, Field

    class OnScreenText(BaseModel):
        timestamp: float = Field(description="Timestamp (seconds) of the frame, copied from the input")
        text: str = Field(description="The narrative on-screen text exactly as shown")
        kind: str = Field(default="other", description="time_jump | location | date_time | hud | sign | message | other")

    class OnScreenBatch(BaseModel):
        detections: List[OnScreenText] = []

    contents: List[Any] = [f"{len(batch)} frames follow, each preceded by its timestamp in seconds."]
    for f in batch:
        contents.append(f"Frame at t={f.time:.1f}s:")
        contents.append(types.Part.from_bytes(data=f.path.read_bytes(), mime_type="image/jpeg"))

    from app.providers import gemini_calls

    response = await gemini_calls.generate_content(
        client,
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=instruction,
            response_mime_type="application/json",
            response_schema=OnScreenBatch,
            temperature=default_temperature(model, 0.0),
            thinking_config=thinking_config(model, thinking),
            media_resolution=media_resolution(model, resolution),
        ),
        flex=flex,
        usage=usage,
        stage=stage,
        flex_state=flex_state,
    )
    parsed = OnScreenBatch.model_validate_json(response.text)
    out = []
    for d in parsed.detections:
        # Snap to the nearest real frame in this batch (models occasionally round timestamps)
        frame = min(batch, key=lambda f: abs(f.time - d.timestamp))
        out.append(FrameDetection(time=frame.time, text=d.text, until=frame.static_until))
    return out


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


class OnScreenTextStage(PipelineStage):
    """Pipeline stage producing ON_SCREEN segments (forced track, else frame scan)."""

    def __init__(self, client_factory=None):
        # Injectable for tests: () -> google.genai.Client-like object
        self._client_factory = client_factory

    @property
    def name(self) -> str:
        return "SCANNING_ONSCREEN"

    @staticmethod
    def start_frame_sampling(ctx: PipelineContext) -> None:
        """Kick off ffmpeg frame sampling in the background (called right after audio extraction)."""
        if ctx.detect_onscreen_text and ctx.frame_task is None and pick_forced_track(ctx.subtitle_streams) is None:
            ctx.frame_task = asyncio.ensure_future(sample_frames(ctx))

    def _client(self, ctx: PipelineContext):
        if self._client_factory:
            return self._client_factory()
        key = (ctx.gemini_api_key_override or "").strip() or settings.gemini_api_key
        if not key or key == "your_gemini_api_key_here":
            return None
        from google import genai

        return genai.Client(api_key=key)

    async def execute(self, ctx: PipelineContext, on_progress: Optional[ProgressCallback] = None) -> None:
        async def progress(pct: float, msg: str) -> None:
            if on_progress:
                await on_progress(pct, None, None, msg, None)

        ctx.onscreen_segments = []
        ctx.foreign_ocr_segments = []
        if not ctx.detect_onscreen_text:
            await progress(100.0, "On-screen text detection disabled; skipping")
            return

        client = self._client(ctx)
        used_forced = await self._narrative_text(ctx, client, progress)
        await self._foreign_subtitles(ctx, client, progress, used_forced)
        await progress(
            100.0,
            f"Found {len(ctx.onscreen_segments)} on-screen captions"
            + (f" and {len(ctx.foreign_ocr_segments)} subtitles for foreign speech" if ctx.foreign_spans else ""),
        )

    # -- narrative titles / signs ------------------------------------------------

    async def _narrative_text(self, ctx: PipelineContext, client: Any, progress) -> bool:
        """Fill ctx.onscreen_segments. Returns True if an embedded forced track was used."""
        forced = pick_forced_track(ctx.subtitle_streams)
        if forced is not None:
            await progress(10.0, f"Reading embedded forced subtitle track #{forced.index}...")
            try:
                cues = await extract_forced_track(ctx.video_path, forced)
                # Forced-track cues during foreign speech are subtitles for that speech, not titles
                for cue in cues:
                    mid = (cue["start_time"] + cue["end_time"]) / 2
                    if any(span["start"] <= mid <= span["end"] for span in ctx.foreign_spans):
                        cue["segment_type"] = SegmentType.FOREIGN.value
                        ctx.foreign_ocr_segments.append(cue)
                    else:
                        ctx.onscreen_segments.append(cue)
                self._cancel_sampling(ctx)
                return True
            except Exception as e:
                logger.warning(f"Forced track extraction failed ({e}); falling back to frame scan")

        if [s for s in ctx.subtitle_streams if s.looks_forced and not s.text_based]:
            logger.info("Forced subtitle track is image-based (PGS/VobSub); using the frame scan instead")

        if client is None:
            self._cancel_sampling(ctx)
            ctx.warnings.append("On-screen text scan skipped: it needs a Gemini API key.")
            return False
        try:
            await self._scan_frames(ctx, client, progress)
        except (PipelineCancelled, asyncio.CancelledError):
            raise
        except Exception as e:
            # Never lose the transcription because of the optional on-screen scan
            logger.error(f"On-screen text scan failed: {e}")
            ctx.warnings.append(f"On-screen text scan failed: {e}")
            self._cancel_sampling(ctx)
        return False

    async def _detect_batches(
        self, ctx: PipelineContext, client: Any, frames: List[Frame], instruction: str, stage: str, on_batch=None
    ):
        """Send frames to Gemini Vision in concurrent batches. Returns (detections, failed_batches)."""
        from app.core.user_settings import load_settings

        app_settings = load_settings()
        model = app_settings.vision.model
        batch_size = max(1, settings.onscreen_batch_size)
        batches = [frames[i:i + batch_size] for i in range(0, len(frames), batch_size)]
        detections: List[FrameDetection] = []
        failed = 0
        errors: List[str] = []
        sem = asyncio.Semaphore(_GEMINI_CONCURRENCY)
        from app.core.model_catalog import is_unavailable_error

        async def run_batch(batch: List[Frame]) -> None:
            nonlocal failed
            async with sem:
                ctx.raise_if_cancelled()
                for attempt in range(3):
                    try:
                        detections.extend(await _gemini_detect(
                            client, model, batch,
                            thinking=app_settings.vision.thinking,
                            resolution=app_settings.vision_media_resolution,
                            usage=ctx.usage,
                            instruction=instruction,
                            stage=stage,
                            flex=app_settings.vision.flex,
                            flex_state=ctx.flex_state,
                        ))
                        break
                    except Exception as e:
                        if is_unavailable_error(e):
                            # Same answer for every batch (no access to the model, bad key): stop now
                            raise RuntimeError(f"{model}: {e}") from e
                        if not errors:
                            errors.append(f"{model}: {e}")
                        logger.warning(f"Vision batch failed (attempt {attempt + 1}/3): {e}")
                        await asyncio.sleep(2 * (attempt + 1))
                else:
                    failed += 1
                if on_batch:
                    await on_batch(len(batches))

        await asyncio.gather(*(run_batch(b) for b in batches))
        if failed and errors:
            ctx.warnings.append(f"Vision ({stage}): {failed} frame batch(es) failed — {errors[0][:300]}")
        return detections, failed

    async def _scan_frames(self, ctx: PipelineContext, client: Any, progress) -> None:
        if ctx.frame_task is None:
            ctx.frame_task = asyncio.ensure_future(sample_frames(ctx))
        await progress(5.0, "Sampling video frames...")
        task = asyncio.ensure_future(ctx.frame_task)
        out_dir = frames_dir(ctx.job_id)
        expected = max(1, int((ctx.duration or 0.0) / max(0.5, settings.onscreen_frame_interval)))
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
                break
            except asyncio.TimeoutError:
                ctx.raise_if_cancelled()
                if out_dir.exists():
                    written = sum(1 for _ in out_dir.glob("f_*.jpg"))
                    if written > 0:
                        pct = min(14.0, 5.0 + 9.0 * min(1.0, written / expected))
                        await progress(pct, f"Sampling video frames... ({written}/{expected})")
        frames: List[Frame] = await task
        ctx.raise_if_cancelled()

        speech = [(s.start_time, s.end_time) for s in ctx.raw_segments]
        selected = select_frames(frames, speech, settings.onscreen_max_frames)

        from app.core.user_settings import load_settings

        app_settings = load_settings()
        if app_settings.cost_saver_mode and selected:
            from math import ceil
            from app.core.model_catalog import estimated_thinking_tokens, image_tokens, request_cost
            from app.pipeline.onscreen_prefilter import prefilter_frames

            before_count = len(selected)
            clusters = await prefilter_frames(selected)
            after_count = len(clusters)
            filtered_frames = max(0, before_count - after_count)
            if filtered_frames > 0 and ctx.usage is not None and hasattr(ctx.usage, "record_local_savings"):
                v = app_settings.vision
                img_tok = image_tokens(v.model, app_settings.vision_media_resolution)
                b_size = max(1, settings.onscreen_batch_size)
                saved_batches = max(0, ceil(before_count / b_size) - (ceil(after_count / b_size) if after_count else 0))
                think_tok = estimated_thinking_tokens(v.model, v.thinking)
                saved_in = filtered_frames * img_tok + saved_batches * 450
                saved_out = saved_batches * (150 + think_tok)
                saved_cost = request_cost(v.model, saved_in, saved_out, flex=getattr(v, "flex", False)) or 0.0
                ctx.usage.record_local_savings(
                    saved_tokens=saved_in + saved_out,
                    frames_filtered=filtered_frames,
                    saved_cost_usd=saved_cost,
                )
            logger.info(
                f"Cost-Saver frame pre-filter: {before_count} selected -> {after_count} cluster(s) sent to Gemini"
            )
            selected = [
                Frame(
                    time=c.start_time,
                    path=c.representative_path,
                    change=1.0,
                    static_until=c.end_time,
                )
                for c in clusters
            ]
            if not selected:
                ctx.onscreen_segments = []
                await progress(65.0, f"Filtered {filtered_frames} blank frames locally")
                return

        await progress(15.0, f"Scanning {len(selected)} of {len(frames)} frames for on-screen text...")
        done = 0

        async def on_batch(total: int) -> None:
            nonlocal done
            done += 1
            await progress(15.0 + 50.0 * done / max(1, total), f"Scanned {done}/{total} frame batches")

        detections, failed = await self._detect_batches(
            ctx, client, selected, VISION_SYSTEM_INSTRUCTION, "onscreen", on_batch
        )
        ctx.onscreen_segments = merge_frame_detections(
            detections, settings.onscreen_frame_interval, drop_tags=True, repeat_window=_ONSCREEN_REPEAT_WINDOW
        )
        for seg in ctx.onscreen_segments:
            logger.info(f"On-screen text {seg['start_time']:.1f}-{seg['end_time']:.1f}s: {seg['original_text']!r}")

    # -- burned-in subtitles for foreign speech -------------------------------------

    async def _foreign_subtitles(self, ctx: PipelineContext, client: Any, progress, used_forced: bool) -> None:
        if not ctx.foreign_spans or used_forced:
            return
        if client is None:
            ctx.warnings.append(
                "Speech in another language was found, but reading its burned-in subtitles needs a Gemini "
                "API key; Whisper's transcription of that speech was used instead."
            )
            return
        try:
            await self._read_subtitles(ctx, client, progress)
        except (PipelineCancelled, asyncio.CancelledError):
            raise
        except Exception as e:
            logger.error(f"Reading burned-in subtitles failed: {e}")
            ctx.warnings.append(f"Reading burned-in subtitles failed: {e}")

    async def _read_subtitles(self, ctx: PipelineContext, client: Any, progress) -> None:
        total_seconds = sum(s["end"] - s["start"] for s in ctx.foreign_spans)
        interval = max(_SUB_INTERVAL, total_seconds / _SUB_MAX_FRAMES)
        duration = ctx.duration or float("inf")
        detections: List[FrameDetection] = []
        await progress(70.0, f"Reading burned-in subtitles in {len(ctx.foreign_spans)} foreign-language passage(s)...")

        for n, span in enumerate(ctx.foreign_spans):
            # The scanned window: the span plus a fixed margin on both sides
            start = max(0.0, span["start"] - _SUB_MARGIN)
            end = min(duration, span["end"] + _SUB_MARGIN)
            frames = await sample_subtitle_crops(ctx, start, end, f"s{n}", interval)
            found, _ = await self._detect_batches(ctx, client, frames, SUBTITLE_INSTRUCTION, "foreign")
            detections.extend(found)
            logger.info(f"Foreign passage {n + 1}: {start:.1f}-{end:.1f}s, {len(frames)} crops, "
                        f"{len(found)} subtitle reading(s)")

            # Keep looking outward while a subtitle is still on screen near the edge of what we scanned
            main_found = found
            read_start, read_end = start, end
            for side in ("before", "after"):
                found = main_found
                edge_start, edge_end = start, end
                for k in range(_SUB_MAX_EXTENSIONS):
                    edge_hit = any(
                        (d.time <= edge_start + _SUB_EDGE) if side == "before" else (d.time >= edge_end - _SUB_EDGE)
                        for d in found
                    )
                    if not edge_hit:
                        break
                    if side == "before":
                        if edge_start <= 0:
                            break
                        a, b = max(0.0, edge_start - _SUB_EXTEND), edge_start
                        edge_start = a
                    else:
                        if edge_end >= duration:
                            break
                        a, b = edge_end, min(duration, edge_end + _SUB_EXTEND)
                        edge_end = b
                    frames = await sample_subtitle_crops(ctx, a, b, f"s{n}{side[0]}{k}", interval)
                    found, _ = await self._detect_batches(ctx, client, frames, SUBTITLE_INSTRUCTION, "foreign")
                    detections.extend(found)
                    logger.info(f"Foreign passage {n + 1}: subtitles continue {side} the window; "
                                f"read {a:.1f}-{b:.1f}s, {len(found)} more reading(s)")
                read_start, read_end = min(read_start, edge_start), max(read_end, edge_end)
            # Subtitles read anywhere in this window belong to the passage (see merge_foreign)
            span["read_start"], span["read_end"] = read_start, read_end
            await progress(70.0 + 28.0 * (n + 1) / len(ctx.foreign_spans), f"Read subtitles in passage {n + 1}/{len(ctx.foreign_spans)}")

        ctx.foreign_ocr_segments = merge_frame_detections(
            detections,
            frame_interval=interval,
            buffer=interval,
            min_duration=0.8,
            bracket=False,
            segment_type=SegmentType.FOREIGN.value,
        )

    @staticmethod
    def _cancel_sampling(ctx: PipelineContext) -> None:
        if ctx.frame_task is not None and not ctx.frame_task.done():
            ctx.frame_task.cancel()
