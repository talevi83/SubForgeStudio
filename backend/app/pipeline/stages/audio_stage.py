import asyncio
import logging
import shutil
from pathlib import Path
from typing import Awaitable, Callable, List, Optional
from app.core.config import settings
from app.pipeline.context import PipelineContext, PipelineCancelled
from app.pipeline.stages.base import PipelineStage, ProgressCallback

logger = logging.getLogger(__name__)

# For 5.1/7.1 tracks, dialogue lives almost entirely in the centre channel. A plain
# downmix also mixes in LFE, surrounds and music, which hurts recognition, so favour FC.
_DIALOGUE_DOWNMIX = "pan=mono|c0=0.707*FC+0.35*FL+0.35*FR"

# "Tactical Comms Boost": isolate the speech band (180Hz-3.8kHz), strip radio static with
# FFT denoising, tame PTT bursts with a compressor, then dynamically normalise so faint or
# distorted radio chatter and whispers reach a level Whisper's VAD does not discard.
COMMS_AUDIO_FILTER = (
    "highpass=f=180,"
    "lowpass=f=3800,"
    "afftdn=nf=-25,"
    "acompressor=threshold=-18dB:ratio=3:attack=5:release=50,"
    "dynaudnorm=f=150:g=15:p=0.92:m=10.0"
)


def get_ffmpeg_bin() -> str:
    """Return path to ffmpeg executable."""
    return shutil.which("ffmpeg") or "ffmpeg"


def build_audio_filter(dialogue_mix: bool, comms_boost: bool) -> Optional[str]:
    """The -af chain for extraction, or None for the plain (fastest) path."""
    parts = []
    if dialogue_mix:
        parts.append(_DIALOGUE_DOWNMIX)
    if comms_boost:
        parts.append(COMMS_AUDIO_FILTER)
    return ",".join(parts) if parts else None


def _build_command(
    video_path: str,
    output_wav_path: str,
    audio_stream_index: int,
    dialogue_mix: bool,
    comms_boost: bool = False,
) -> List[str]:
    command = [
        get_ffmpeg_bin(),
        "-y",
        "-nostats",
        "-progress", "pipe:1",              # machine-readable progress on stdout
        "-i", str(Path(video_path).resolve()),
        "-map", f"0:a:{audio_stream_index}",
        "-vn",
    ]
    audio_filter = build_audio_filter(dialogue_mix, comms_boost)
    if audio_filter:
        command += ["-af", audio_filter]
    command += [
        "-acodec", "pcm_s16le",             # Raw 16-bit PCM
        "-ar", "16000",                     # Whisper's native sample rate
        "-ac", "1",                         # Mono
        str(Path(output_wav_path).resolve()),
    ]
    return command


async def _run_ffmpeg(
    command: List[str],
    duration: float,
    on_percent: Optional[Callable[[float], Awaitable[None]]],
    ctx: Optional[PipelineContext],
) -> None:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_task = asyncio.ensure_future(process.stderr.read())
    last_reported = -10.0
    try:
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            if ctx is not None and ctx.is_cancelled:
                process.kill()
                raise PipelineCancelled()
            key, _, value = line.decode(errors="ignore").strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and duration > 0 and on_percent:
                try:
                    pct = min(99.0, int(value) / 1_000_000 / duration * 100)
                except ValueError:
                    continue
                if pct - last_reported >= 2:
                    last_reported = pct
                    await on_percent(pct)
        await process.wait()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    stderr = await stderr_task
    if process.returncode != 0:
        error_msg = stderr.decode(errors="ignore")[-2000:] if stderr else "Unknown FFmpeg error"
        raise RuntimeError(f"FFmpeg audio extraction failed: {error_msg}")


async def extract_audio_track(
    video_path: str,
    output_wav_path: str,
    audio_stream_index: int = 0,
    channels: int = 2,
    duration: float = 0.0,
    on_percent: Optional[Callable[[float], Awaitable[None]]] = None,
    ctx: Optional[PipelineContext] = None,
    comms_boost: bool = False,
) -> None:
    """Extract one audio stream as 16kHz mono WAV (dialogue-focused for surround, optional comms boost)."""
    dialogue_mix = channels >= 6
    try:
        await _run_ffmpeg(
            _build_command(video_path, output_wav_path, audio_stream_index, dialogue_mix, comms_boost),
            duration, on_percent, ctx,
        )
    except RuntimeError:
        if not dialogue_mix:
            raise
        # Unusual channel layouts have no FC/FL/FR; fall back to ffmpeg's default downmix.
        logger.warning("Dialogue downmix failed; retrying with the default downmix")
        await _run_ffmpeg(
            _build_command(video_path, output_wav_path, audio_stream_index, False, comms_boost),
            duration, on_percent, ctx,
        )


class AudioExtractionStage(PipelineStage):
    """Pipeline stage for extracting 16kHz mono WAV from the selected audio stream."""

    @property
    def name(self) -> str:
        return "EXTRACTING_AUDIO"

    async def execute(
        self,
        ctx: PipelineContext,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        if on_progress:
            await on_progress(0.0, None, None, "Starting audio extraction...", None)

        if ctx.use_embedded_subtitles and not ctx.sync_cues and ctx.subtitle_streams:
            from app.pipeline.stages.ocr_stage import pick_dialogue_track, extract_dialogue_track
            from app.utils.languages import language_code

            track = pick_dialogue_track(
                ctx.subtitle_streams,
                source_language=ctx.source_language,
                audio_streams=ctx.audio_streams,
                selected_audio_stream=ctx.selected_audio_stream,
                selected_subtitle_stream=ctx.selected_subtitle_stream,
            )
            if track:
                try:
                    dialogue_segs, onscreen_segs = await extract_dialogue_track(
                        ctx.video_path, track, duration=ctx.duration
                    )
                    if (ctx.duration or 0.0) >= 60.0:
                        min_cues = max(5, int((ctx.duration or 0.0) / 15.0))
                    else:
                        min_cues = max(2, int((ctx.duration or 0.0) / 15.0))
                    if len(dialogue_segs) >= min_cues:
                        ctx.embedded_dialogue_segments = dialogue_segs
                        ctx.embedded_subtitle_stream = track
                        if onscreen_segs:
                            ctx.onscreen_segments.extend(onscreen_segs)
                        track_lang = language_code(track.language) or track.language
                        if track_lang and track_lang.lower() not in ("und", "unknown", "none", "auto"):
                            ctx.detected_language = track_lang
                        elif not ctx.detected_language or ctx.detected_language.lower() in ("auto", "none"):
                            ctx.detected_language = "en"
                        track_desc = f"'{track.title}'" if track.title else f"track #{track.index} ({track.language})"
                        logger.info(
                            f"Using embedded dialogue subtitle track #{track.index} "
                            f"({track.title or track.language}, {len(dialogue_segs)} cues). "
                            "Skipping audio extraction."
                        )
                        if on_progress:
                            await on_progress(
                                100.0,
                                None,
                                0.0,
                                f"Extracted {len(dialogue_segs)} cues from embedded {track_desc} — audio extraction skipped",
                                None,
                            )
                        return
                except Exception as e:
                    logger.warning(f"Could not extract embedded dialogue track #{track.index}: {e}")

        output_wav = settings.storage_dir / "audio" / f"{ctx.job_id}.wav"
        output_wav.parent.mkdir(parents=True, exist_ok=True)

        stream = next((s for s in ctx.audio_streams if s.index == ctx.selected_audio_stream), None)
        channels = stream.channels if stream else 2

        async def _percent(pct: float) -> None:
            if on_progress:
                await on_progress(pct, None, None, f"Extracting audio... {pct:.0f}%", None)

        await extract_audio_track(
            video_path=ctx.video_path,
            output_wav_path=str(output_wav),
            audio_stream_index=ctx.selected_audio_stream,
            channels=channels,
            duration=ctx.duration,
            on_percent=_percent,
            ctx=ctx,
            comms_boost=ctx.comms_boost,
        )

        ctx.audio_wav_path = str(output_wav)
        if on_progress:
            notes = []
            if channels >= 6:
                notes.append("dialogue-focused surround downmix")
            if ctx.comms_boost:
                notes.append("tactical comms boost")
            suffix = f" ({', '.join(notes)})" if notes else ""
            await on_progress(100.0, None, None, f"Audio extraction completed{suffix}", None)
