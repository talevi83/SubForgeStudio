"""
Speech regions (voice-activity detection) for a finished job, re-created from its video.

The extracted WAV is deleted after each run, so a later timing check has to extract the audio
again. Whole-file results are cached next to the other job data; windows are never cached.
"""
import json
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from app.core.config import settings
from app.pipeline.foreign_speech import SAMPLE_RATE, load_wav
from app.pipeline.stages.audio_stage import build_audio_filter, get_ffmpeg_bin

Region = Tuple[float, float]


def _cache_path(job_id: str) -> Path:
    return settings.storage_dir / "regions" / f"{job_id}.json"


def _extract(video_path: str, wav_path: str, stream: int, channels: int, comms_boost: bool,
             start: float, length: Optional[float]) -> None:
    cmd = [get_ffmpeg_bin(), "-y", "-nostats", "-loglevel", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-i", str(Path(video_path).resolve()), "-map", f"0:a:{stream}", "-vn"]
    audio_filter = build_audio_filter(channels >= 6, comms_boost)
    if audio_filter:
        cmd += ["-af", audio_filter]
    cmd += ["-acodec", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", wav_path]
    result = subprocess.run(cmd, capture_output=True, timeout=1800)
    if result.returncode != 0 and channels >= 6:
        # Unusual channel layouts have no FC/FL/FR; fall back to the default downmix.
        cmd = [c for c in cmd]
        if "-af" in cmd:
            k = cmd.index("-af")
            fallback = build_audio_filter(False, comms_boost)
            if fallback:
                cmd[k + 1] = fallback
            else:
                del cmd[k:k + 2]
        result = subprocess.run(cmd, capture_output=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr.decode(errors='ignore')[-500:]}")


def detect_regions(wav_path: str) -> List[Region]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = load_wav(wav_path)
    stamps = get_speech_timestamps(
        audio,
        VadOptions(threshold=0.5, min_speech_duration_ms=100, min_silence_duration_ms=150, speech_pad_ms=0),
    )
    return [(t["start"] / SAMPLE_RATE, t["end"] / SAMPLE_RATE) for t in stamps]


def speech_regions(
    job_id: str,
    video_path: str,
    stream: int,
    channels: int,
    comms_boost: bool,
    start: float = 0.0,
    end: Optional[float] = None,
) -> List[Region]:
    """Speech regions in video time. With `end`, only the window [start, end] is analysed."""
    whole = end is None
    cache = _cache_path(job_id)
    if whole and cache.exists():
        try:
            return [(float(a), float(b)) for a, b in json.loads(cache.read_text())]
        except (ValueError, OSError):
            pass
    with tempfile.TemporaryDirectory() as tmp:
        wav = str(Path(tmp) / "audio.wav")
        _extract(video_path, wav, stream, channels, comms_boost, start, None if whole else max(0.5, end - start))
        regions = [(a + start, b + start) for a, b in detect_regions(wav)]
    if whole:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(regions))
        except OSError:
            pass
    return regions
