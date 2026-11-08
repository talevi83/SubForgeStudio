"""
Embedded subtitle tracks: pick a video's forced/"signs" track or its full dialogue track and
extract it with ffmpeg into ON_SCREEN / DIALOGUE segment dicts.

Used by the on-screen text stage (forced track instead of a frame scan), the audio stage
(dialogue track instead of Whisper) and the sync stage (reference timing).
"""
import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.models.db_models import SegmentType
from app.models.schemas import SubtitleStreamInfo
from app.pipeline.onscreen_merge import make_onscreen_segment
from app.pipeline.stages.audio_stage import get_ffmpeg_bin
from app.utils.subtitle_parser import SubtitleParseError, parse_subtitle_text


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
    r"^(\s*[-–—]?\s*)\[([a-zA-Z0-9\s._'-]{2,25})\]\s*:?\s*(?=\S)",
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
        # Drop pure single bracketed/parenthesized sound descriptions (never drop "[Speaker] text [sound]")
        if (l.startswith("(") and l.endswith(")")) or (is_sdh and l.startswith("[") and l.endswith("]")):
            inner = l[1:-1].strip()
            if (
                "[" not in inner
                and "]" not in inner
                and "(" not in inner
                and ")" not in inner
                and len(inner.split()) <= 7
                and any(
                    k in inner.lower()
                    for k in (
                        "music", "sound", "applause", "groan", "sigh", "pant", "gasp",
                        "chatter", "cough", "cheer", "laugh", "stammer", "whisper",
                    )
                )
            ):
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
    try:
        cues, _ = parse_subtitle_text(content, "track.srt")
    except SubtitleParseError:
        return []
    return [{"start_time": c.start, "end_time": c.end, "text": c.text} for c in cues]


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
