import asyncio
import os
import shutil
from pathlib import Path
from typing import Optional
from app.core.config import settings
from app.pipeline.context import PipelineContext
from app.pipeline.stages.base import PipelineStage, ProgressCallback


def get_ffmpeg_bin() -> str:
    """Return path to ffmpeg executable."""
    return shutil.which("ffmpeg") or "ffmpeg"


async def extract_audio_track(
    video_path: str,
    output_wav_path: str,
    audio_stream_index: int = 0,
) -> None:
    """Extract a specific audio stream converted to 16kHz mono WAV using ffmpeg."""
    ffmpeg_bin = get_ffmpeg_bin()
    command = [
        ffmpeg_bin,
        "-y",                               # Overwrite output if exists
        "-i", str(Path(video_path).resolve()),
        "-map", f"0:a:{audio_stream_index}", # Select specific audio stream
        "-vn",                              # Disable video stream
        "-acodec", "pcm_s16le",             # Raw 16-bit PCM
        "-ar", "16000",                     # Whisper optimal sample rate (16kHz)
        "-ac", "1",                         # Mono channel
        str(Path(output_wav_path).resolve()),
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode(errors="ignore") if stderr else "Unknown FFmpeg error"
        raise RuntimeError(f"FFmpeg audio extraction failed: {error_msg}")


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
            await on_progress(10.0, None, None, "Starting audio extraction...", None)

        output_wav = settings.storage_dir / "audio" / f"{ctx.job_id}.wav"
        output_wav.parent.mkdir(parents=True, exist_ok=True)

        await extract_audio_track(
            video_path=ctx.video_path,
            output_wav_path=str(output_wav),
            audio_stream_index=ctx.selected_audio_stream,
        )

        ctx.audio_wav_path = str(output_wav)
        if on_progress:
            await on_progress(100.0, None, None, "Audio extraction completed", None)
