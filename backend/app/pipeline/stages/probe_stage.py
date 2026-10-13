import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Optional
from app.models.schemas import ProbeResponse, AudioStreamInfo
from app.pipeline.context import PipelineContext
from app.pipeline.stages.base import PipelineStage, ProgressCallback


def get_ffprobe_bin() -> str:
    """Return path to ffprobe executable."""
    return shutil.which("ffprobe") or "ffprobe"


async def probe_media_file(file_path: str) -> ProbeResponse:
    """
    Run ffprobe on the given file path and parse streams, metadata, and format info.
    Raises FileNotFoundError or RuntimeError on failure.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    ffprobe_bin = get_ffprobe_bin()
    command = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path.resolve()),
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        err = stderr.decode(errors="ignore") if stderr else "Unknown ffprobe error"
        raise RuntimeError(f"ffprobe failed: {err}")

    data = json.loads(stdout.decode(errors="ignore"))
    format_info = data.get("format", {})
    streams = data.get("streams", [])

    duration_val = float(format_info.get("duration", 0.0) or 0.0)
    file_size = int(format_info.get("size", 0) or os.path.getsize(path))
    format_name = format_info.get("format_name", "unknown")

    video_streams = []
    audio_streams = []
    audio_idx = 0

    for stream in streams:
        c_type = stream.get("codec_type")
        if c_type == "video":
            video_streams.append({
                "codec_name": stream.get("codec_name", "unknown"),
                "width": stream.get("width"),
                "height": stream.get("height"),
                "fps": stream.get("r_frame_rate"),
                "duration": stream.get("duration"),
            })
        elif c_type == "audio":
            tags = stream.get("tags") or {}
            lang = tags.get("language") or "und"
            title = tags.get("title") or tags.get("handler_name") or f"Audio Track #{audio_idx}"
            audio_streams.append(AudioStreamInfo(
                index=audio_idx,
                codec_name=stream.get("codec_name", "unknown"),
                channels=int(stream.get("channels") or 2),
                channel_layout=stream.get("channel_layout"),
                sample_rate=stream.get("sample_rate"),
                language=lang,
                title=title,
            ))
            audio_idx += 1

    return ProbeResponse(
        file_path=str(path.resolve()),
        file_name=path.name,
        file_size=file_size,
        duration=duration_val,
        format_name=format_name,
        video_streams=video_streams,
        audio_streams=audio_streams,
    )


class ProbeStage(PipelineStage):
    """Pipeline stage that discovers streams and extracts container metadata."""

    @property
    def name(self) -> str:
        return "PROBING"

    async def execute(
        self,
        ctx: PipelineContext,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        if on_progress:
            await on_progress(10.0, None, None, "Probing media streams with ffprobe...", None)

        probe_res = await probe_media_file(ctx.video_path)
        ctx.duration = probe_res.duration
        ctx.audio_streams = probe_res.audio_streams
        ctx.video_metadata = {
            "format_name": probe_res.format_name,
            "video_streams": probe_res.video_streams,
            "file_size": probe_res.file_size,
        }

        if on_progress:
            await on_progress(100.0, None, None, f"Found {len(probe_res.audio_streams)} audio streams", None)
