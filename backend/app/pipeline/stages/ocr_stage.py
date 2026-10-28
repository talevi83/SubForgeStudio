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

# Grayscale thumbnails used to measure frame-to-frame change (scene cuts / chyron overlays / static cards)
_THUMB_W, _THUMB_H = 64, 36
_SCENE_CHANGE = 0.12      # mean absolute pixel difference (0..1) that counts as a cut
_OVERLAY_CHANGE = 0.065   # localized change from a lower-third chyron or title appearing mid-shot
_STATIC = 0.015           # below this two frames are considered identical
_ONSCREEN_BUFFER = 1.0    # seconds added after the last frame a text was seen in
_ONSCREEN_MIN_DURATION = 3.0
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
# Layer 1: embedded forced subtitle tracks & full dialogue tracks
# ---------------------------------------------------------------------------

def pick_forced_track(streams: Sequence[SubtitleStreamInfo]) -> Optional[SubtitleStreamInfo]:
    """The best text-based forced/"signs" subtitle stream, if any."""
    candidates = [s for s in streams if s.looks_forced and s.text_based]
    if not candidates:
        return None
    # Prefer the explicit forced disposition, then English, then the first one
    candidates.sort(key=lambda s: (not s.forced, (s.language or "") not in ("eng", "en"), s.index))
    return candidates[0]


_PURE_MUSIC_LINE = re.compile(r"^[\s♪♫#*+\-—–~]+$")
_INLINE_MUSIC_NOTES = re.compile(r"[♪♫]")
_SDH_SOUND_EFFECT = re.compile(
    r"\[(?:[a-z0-9\s._'-]*?(?:music|applause|laughter|groan|groans|gasp|gasps|sigh|sighs|scream|screams|screaming|cheer|cheers|cheering|chatter|whisper|whispers|whispering|cough|coughing|snicker|snickers|footstep|footsteps|thunder|wind|silence|beep|grunt|grunts|pant|panting|engine|stammer|stammers)[^\]]*?)\]|"
    r"\((?:[a-z0-9\s._'-]*?(?:music|applause|laughter|groan|groans|gasp|gasps|sigh|sighs|scream|screams|screaming|cheer|cheers|cheering|chatter|whisper|whispers|whispering|cough|coughing|snicker|snickers|footstep|footsteps|thunder|wind|silence|beep|grunt|grunts|pant|panting|engine|stammer|stammers)[^)]*?)\)",
    re.IGNORECASE,
)
_SDH_INLINE_SOUND = re.compile(r"\[[a-zA-Z\s_-]{2,35}\]|\([a-zA-Z\s_-]{2,35}\)")
_SDH_SPEAKER_LABEL = re.compile(
    r"^(\s*[-–—]?\s*)\[([A-Z0-9\s._'-]{2,25})\]\s+(?=\S)",
)


def clean_embedded_dialogue_text(text: str, is_sdh: bool = False) -> str:
    """Strip formatting tags, music note lines, and SDH noise while preserving dialogue and turns."""
    clean = _TAGS.sub("", text or "").strip()
    if not clean:
        return ""
    lines = []
    for raw_l in clean.splitlines():
        l = raw_l.strip()
        if not l or _PURE_MUSIC_LINE.match(l):
            continue
        stripped_notes = l.strip("♪♫#*~ \t")
        if not stripped_notes:
            continue
        # Drop pure bracketed/parenthesized sound descriptions
        if (l.startswith("(") and l.endswith(")")) or (is_sdh and l.startswith("[") and l.endswith("]")):
            inner = l[1:-1].strip()
            if any(k in inner.lower() for k in ("music", "sound", "applause", "groan", "sigh", "pant", "gasp", "chatter", "cough", "cheer", "laugh", "stammer", "whisper")):
                continue
        if is_sdh:
            l = _SDH_SOUND_EFFECT.sub("", l)
            # Remove any residual action cues like [stammers] or (sighs)
            l = _SDH_INLINE_SOUND.sub(
                lambda m: "" if any(k in m.group(0).lower() for k in ("stammer", "sigh", "gasp", "chuckle", "snicker", "whisper", "music", "groan", "applause", "laughter")) else m.group(0),
                l,
            )
            l = _SDH_SPEAKER_LABEL.sub(r"\1", l.strip())
        elif "[" in l or "(" in l:
            l = _SDH_SOUND_EFFECT.sub("", l)
            l = _SDH_INLINE_SOUND.sub(
                lambda m: "" if any(k in m.group(0).lower() for k in ("stammer", "sigh", "gasp", "chuckle", "snicker", "whisper", "music", "groan", "applause", "laughter")) else m.group(0),
                l,
            )
            l = _SDH_SPEAKER_LABEL.sub(r"\1", l.strip())
        l = _INLINE_MUSIC_NOTES.sub("", l).strip()
        if l and not _PURE_MUSIC_LINE.match(l):
            lines.append(l)
    return "\n".join(lines).strip()


def pick_dialogue_track(
    streams: Sequence[SubtitleStreamInfo],
    source_language: Optional[str] = "auto",
    audio_streams: Sequence[Any] = (),
    selected_audio_stream: int = 0,
    selected_subtitle_stream: Optional[int] = None,
) -> Optional[SubtitleStreamInfo]:
    """
    Find the best embedded text-based full dialogue subtitle track, if any.
    If `selected_subtitle_stream` is explicitly given, return that text-based track directly.
    Otherwise, prefers clean dialogue over SDH, matching source or audio stream language,
    and falls back to any available text-based dialogue track (including foreign languages).
    """
    if selected_subtitle_stream is not None:
        explicit = next(
            (s for s in streams if s.index == selected_subtitle_stream and s.text_based),
            None,
        )
        if explicit is not None:
            return explicit

    candidates = [s for s in streams if s.text_based and not s.looks_forced]
    if not candidates:
        return None

    # Exclude commentary / audio description tracks
    filtered = []
    for s in candidates:
        t = (s.title or "").lower()
        if any(bad in t for bad in ("commentary", "director", "description", "audio desc")):
            continue
        filtered.append(s)
    if not filtered:
        filtered = candidates

    from app.utils.languages import language_code, language_name

    desired_code: Optional[str] = None
    if source_language and source_language.lower() not in ("auto", "none"):
        desired_code = language_code(source_language) or source_language.lower()
    elif audio_streams:
        audio = next((a for a in audio_streams if getattr(a, "index", None) == selected_audio_stream), None)
        if audio and getattr(audio, "language", None) and audio.language.lower() not in ("und", "unknown"):
            desired_code = language_code(audio.language) or audio.language.lower()

    def _lang_match_score(s: SubtitleStreamInfo) -> int:
        s_code = language_code(s.language) or (s.language or "").lower()
        s_title = (s.title or "").lower()
        if desired_code:
            desired_name = language_name(desired_code, "").lower()
            if s_code == desired_code or (desired_name and desired_name in s_title) or desired_code in s_title:
                return 0
            if s_code in ("und", ""):
                return 1
            return 2
        if s_code == "en" or "english" in s_title:
            return 0
        if s_code in ("und", ""):
            return 1
        return 2

    filtered.sort(
        key=lambda s: (
            _lang_match_score(s),
            1 if s.looks_sdh else 0,
            0 if s.default else 1,
            s.index,
        )
    )
    return filtered[0]


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


async def extract_dialogue_track(
    video_path: str,
    stream: SubtitleStreamInfo,
    duration: float = 0.0,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Extract embedded subtitle stream via ffmpeg into:
    - dialogue_segments: clean dialogue cues
    - onscreen_segments: top-of-screen titles (marked with {\\an8} in SRT/ASS)
    """
    process = await asyncio.create_subprocess_exec(
        get_ffmpeg_bin(), "-v", "error", "-i", str(Path(video_path).resolve()),
        "-map", f"0:s:{stream.index}", "-f", "srt", "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"Could not extract subtitle stream {stream.index}: {stderr.decode(errors='ignore')[-500:]}")

    content = stdout.decode("utf-8", errors="ignore").replace("\ufeff", "").strip()
    blocks = re.split(r"\r?\n\s*\r?\n", content)

    dialogue: List[Dict[str, Any]] = []
    onscreen: List[Dict[str, Any]] = []

    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        for i, line in enumerate(lines):
            match = _SRT_TIME.search(line)
            if not match:
                continue
            g = match.groups()
            start_ts = _ts(*g[:4])
            end_ts = _ts(*g[4:])
            raw_text = "\n".join(lines[i + 1:])
            is_top = "{\\an8}" in raw_text or "\\an8" in raw_text
            cleaned = clean_embedded_dialogue_text(raw_text, is_sdh=stream.looks_sdh)
            if not cleaned:
                continue
            if is_top:
                onscreen.append(make_onscreen_segment(start_ts, end_ts, cleaned, bracket=True))
            else:
                dialogue.append({
                    "index": len(dialogue) + 1,
                    "start_time": round(start_ts, 3),
                    "end_time": round(max(end_ts, start_ts + 0.4), 3),
                    "original_text": cleaned,
                    "translated_text": None,
                    "translation_failed": False,
                    "segment_type": SegmentType.DIALOGUE.value,
                    "confidence": 1.0,
                })
            break

    return dialogue, onscreen


# ---------------------------------------------------------------------------
# Segment helpers
# ---------------------------------------------------------------------------

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
    frames: List[Frame] = []
    prev: Optional[bytes] = None
    for i, jpg in enumerate(jpgs):
        thumb = thumbs[i * size:(i + 1) * size]
        if prev is None or len(thumb) < size:
            change = 1.0
        else:
            diffs = [abs(a - b) for a, b in zip(thumb, prev)]
            global_change = sum(diffs) / (size * 255)
            diffs.sort(reverse=True)
            top_zone_change = sum(diffs[:top_k]) / (top_k * 255)
            # A lower-third chyron or title overlay appearing over a static talking head changes
            # ~4-8% of the frame strongly while keeping global_change small; blending the top-5%
            # localized change ensures chyron fade-ins register above _SCENE_CHANGE (0.12).
            change = max(global_change, top_zone_change * 0.75)
        frames.append(Frame(time=round(i * interval, 3), path=jpg, change=change))
        prev = thumb if len(thumb) == size else prev
    _fill_static_runs(frames, interval)
    return frames


def _fill_static_runs(frames: List[Frame], interval: float) -> None:
    """For each frame, how long the picture or overlay stays on screen before a cut or graphic fade-out."""
    for i in range(len(frames) - 1, -1, -1):
        nxt = frames[i + 1] if i + 1 < len(frames) else None
        if nxt is not None and nxt.change < _OVERLAY_CHANGE:
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


VISION_SYSTEM_INSTRUCTION = """You read video frames from a movie or TV episode for a subtitle editor.
Report ONLY on-screen text that the viewer is genuinely meant to read to follow the story or programme:
1. **Speaker / interviewee / expert / character identification chyrons & lower-thirds (`chyron`):**
   - Lower-third or overlaid captions introducing a person on screen with their name, title, role, or affiliation (e.g. "DR. JAN FRANCKE — INTERNATIONAL GPR EXPERT", "MARTY LAGINA", "JOHN SMITH — FORMER FBI AGENT"). Combine the name and role/title lines with a newline (`\\n`).
2. **Narrative overlaid titles & captions (`time_jump`, `location`, `date_time`, `title_card`):**
   - Time jumps ("One week later...", "36 Hours Earlier", "Summer 2018")
   - Location / date / time stamps ("Damascus, Syria", "March 3, 2014 - 0400 hours", "1st July", "Oak Island, Nova Scotia", "Skinwalker Ranch", "Skinwalker Ranch — Command Center")
   - Narrative chapter titles, documentary fact/info overlays, map/elevation labels ("1000 FEET", "THE MESA DRILL SITE"), or news ticker headlines deliberately overlaid for the audience
3. **Viewer-facing messages (`text_message`, `screen_message`):**
   - Text messages / SMS / chat messages on a phone screen when the camera frames the phone so the audience can read the message
   - A deliberate close-up insert shot of a short computer/screen alert, note, or headline that the viewer must read to understand the scene

Strictly IGNORE and DO NOT report:
- **Random documents, paperwork, forms, or files in the frame:** police/arrest reports ("CALWELL POLICE DEPARTMENT ARREST REPORT"), medical charts, case files, folders, ID badges, newspapers, book pages, letters, or printed forms lying on a desk or held by a character.
- **Opening/closing credits and the show's own stylized main title logo** (e.g. the full series title card "THE SECRET OF SKINWALKER RANCH" or actor/crew credits; however, DO report narrative location titles like "SKINWALKER RANCH" or "OAK ISLAND" when they identify a location in a scene).
- **Incidental background or environmental text:** building/street/store signs, posters, banners, license plates, computer UI chrome, phone status bars/clocks/contact headers, equipment/gauge labels, bare numbers, channel watermarks, and bottom dialogue subtitles.
- **One-word HUD/radio tags or callsigns** ("EAGLE", "LIVE", "REC").

If a chyron or title is animating in/out across consecutive frames in the batch, report its complete reading and skip half-typed mid-transition fragments (e.g. report "THE MESA DRILL SITE", not "THE MESA DRI").
If a frame only contains a paper document, report header, background sign, or the show's main title logo, omit it completely.
Copy qualifying text concisely as shown (for phone messages, copy only the message text itself, not the phone UI/clock). Use the timestamp given before each frame. Return an empty list if none qualify."""

VISION_SYSTEM_INSTRUCTION_BALANCED = """You read video frames from a movie or TV episode for a subtitle editor.
Report ONLY on-screen text that the viewer is genuinely meant to read to follow the story or programme:
1. **Speaker / interviewee / expert / character identification chyrons & lower-thirds (`chyron`):**
   - Lower-third or overlaid captions introducing a person on screen with their name, title, role, or affiliation (e.g. "DR. JAN FRANCKE — INTERNATIONAL GPR EXPERT", "MARTY LAGINA", "JOHN SMITH — FORMER FBI AGENT"). Combine the name and role/title lines with a newline (`\\n`).
2. **Narrative overlaid titles & captions (`time_jump`, `location`, `date_time`, `title_card`):**
   - Time jumps ("One week later...", "36 Hours Earlier", "Summer 2018")
   - Location / date / time stamps ("Damascus, Syria", "March 3, 2014 - 0400 hours", "1st July", "Oak Island, Nova Scotia", "Skinwalker Ranch", "Skinwalker Ranch — Command Center")
   - Narrative chapter titles, documentary fact/info overlays, map/elevation labels ("1000 FEET", "THE MESA DRILL SITE"), or news ticker headlines deliberately overlaid for the audience
3. **Viewer-facing messages (`text_message`, `screen_message`):**
   - Text messages / SMS / chat messages on a phone screen when the camera frames the phone so the audience can read the message
   - A deliberate close-up insert shot of a short computer/screen alert, note, or headline that the viewer must read to understand the scene
4. **Plot-critical signs & key close-up headlines (`sign`, `document`):**
   - Significant signs that the camera deliberately frames in establishing shots or close-ups (e.g. facility/building names, warning plates, entrance plaques) that are essential for following the scene
   - Short close-up headlines or notes that the viewer is explicitly meant to read

Strictly IGNORE and DO NOT report:
- **Incidental background or environmental text:** fleeting background street signs, posters, license plates, computer UI chrome, phone status bars/clocks/contact headers, equipment/gauge labels, bare numbers, channel watermarks, and bottom dialogue subtitles.
- **Boilerplate paperwork or forms:** dense police/arrest reports, medical charts, full form paragraphs, case files, ID badges, or dense book pages lying on a desk.
- **Opening/closing credits and the show's own stylized main title logo** (e.g. actor/crew credits or full series title card; however, DO report narrative location titles like "SKINWALKER RANCH").
- **One-word HUD/radio tags or callsigns** ("EAGLE", "LIVE", "REC").

If a chyron or title is animating in/out across consecutive frames in the batch, report its complete reading and skip half-typed mid-transition fragments.
Copy qualifying text concisely as shown (for phone messages, copy only the message text itself, not the phone UI/clock). Use the timestamp given before each frame. Return an empty list if none qualify."""

VISION_SYSTEM_INSTRUCTION_BROAD = """You read video frames from a movie or TV episode for a subtitle editor.
Report readable narrative on-screen text that conveys story context or information:
1. **Speaker / interviewee / character chyrons & lower-thirds (`chyron`):**
   - Names, roles, affiliations overlaid on screen.
2. **Narrative titles & timestamps (`time_jump`, `location`, `date_time`, `title_card`):**
   - Time jumps, location/date/time stamps, chapter titles, documentary facts, map labels.
3. **Screens, phones & viewer messages (`text_message`, `screen_message`):**
   - Phone text messages, emails, computer monitors, screen alerts.
4. **Signs, plaques & tactical HUD (`sign`, `hud`):**
   - Building/facility signs, warning plates, street plaques, and tactical HUD headers/displays.
5. **Documents & close-up files (`document`):**
   - Close-up headlines, letters, files, or documents framed by the camera.

Strictly IGNORE and DO NOT report:
- **Channel watermarks / station logos**
- **Hardcoded bottom dialogue subtitles** (burned-in translations of spoken dialogue)
- **Opening/closing actor & crew credits**
- **The show's own main stylized title logo card**

Copy qualifying text concisely as shown. Use the timestamp given before each frame. Return an empty list if none qualify."""


def get_vision_system_instruction(sensitivity: int = 2) -> str:
    if sensitivity <= 1:
        return VISION_SYSTEM_INSTRUCTION
    if sensitivity >= 3:
        return VISION_SYSTEM_INSTRUCTION_BROAD
    return VISION_SYSTEM_INSTRUCTION_BALANCED


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
    sensitivity: int = 2,
) -> List[FrameDetection]:
    from google.genai import types
    from app.core.model_catalog import default_temperature, media_resolution, thinking_config
    from pydantic import BaseModel, Field

    class OnScreenText(BaseModel):
        timestamp: float = Field(description="Timestamp (seconds) of the frame, copied from the input")
        text: str = Field(description="The speaker chyron, narrative title, or viewer-intended message text exactly as shown")
        kind: str = Field(
            default="other",
            description="chyron | time_jump | location | date_time | title_card | text_message | screen_message | sign | hud | document | other",
        )

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
        if stage == "onscreen" and _is_unwanted_onscreen(d.text, d.kind, sensitivity=sensitivity):
            logger.info(f"On-screen detection at {d.timestamp:.1f}s ignored ({d.kind}): {d.text!r}")
            continue
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
        self,
        ctx: PipelineContext,
        client: Any,
        frames: List[Frame],
        instruction: str,
        stage: str,
        on_batch=None,
        sensitivity: int = 2,
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
                            sensitivity=sensitivity,
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

        if ctx.embedded_dialogue_segments:
            speech = [(float(s["start_time"]), float(s["end_time"])) for s in ctx.embedded_dialogue_segments]
        else:
            speech = [(s.start_time, s.end_time) for s in ctx.raw_segments]

        sensitivity = getattr(ctx, "onscreen_sensitivity", 2)
        if sensitivity == 1:
            frame_budget = int(settings.onscreen_max_frames * 0.75)
        elif sensitivity == 3:
            frame_budget = int(settings.onscreen_max_frames * 1.35)
        else:
            frame_budget = settings.onscreen_max_frames

        selected = select_frames(frames, speech, frame_budget)

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
                await progress(65.0, f"Filtered {filtered_frames} blank frames locally")
                return

        await progress(15.0, f"Scanning {len(selected)} of {len(frames)} frames for on-screen text...")
        done = 0

        async def on_batch(total: int) -> None:
            nonlocal done
            done += 1
            await progress(15.0 + 50.0 * done / max(1, total), f"Scanned {done}/{total} frame batches")

        instruction = get_vision_system_instruction(sensitivity)
        detections, failed = await self._detect_batches(
            ctx, client, selected, instruction, "onscreen", on_batch, sensitivity=sensitivity
        )
        found = merge_frame_detections(
            detections,
            settings.onscreen_frame_interval,
            drop_tags=True,
            repeat_window=_ONSCREEN_REPEAT_WINDOW,
            file_name=ctx.file_name or "",
            sensitivity=sensitivity,
        )
        dialogue_list = ctx.embedded_dialogue_segments or [
            {"start_time": s.start_time, "end_time": s.end_time, "original_text": s.text}
            for s in ctx.raw_segments
        ]
        filtered_found = []
        for f in found:
            is_dialogue_dup = any(
                _same_text(f["original_text"], d["original_text"])
                and max(0.0, min(f["end_time"], d["end_time"]) - max(f["start_time"], d["start_time"])) > 0.0
                for d in dialogue_list
            )
            if is_dialogue_dup:
                continue
            is_onscreen_dup = any(
                _same_text(f["original_text"], o["original_text"])
                and max(0.0, min(f["end_time"], o["end_time"]) - max(f["start_time"], o["start_time"])) > 0.0
                for o in ctx.onscreen_segments
            )
            if is_onscreen_dup:
                continue
            filtered_found.append(f)

        ctx.onscreen_segments = sorted(ctx.onscreen_segments + filtered_found, key=lambda s: s["start_time"])
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
