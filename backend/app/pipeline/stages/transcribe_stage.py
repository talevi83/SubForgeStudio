import time
from typing import Optional, Any
from app.pipeline.context import PipelineContext, RawSegment
from app.pipeline.stages.base import PipelineStage, ProgressCallback
from app.providers.transcription.faster_whisper import FasterWhisperTranscriber


class TranscribeStage(PipelineStage):
    """Pipeline stage executing Speech-to-Text transcription via Faster-Whisper."""

    def __init__(self, transcriber: Optional[FasterWhisperTranscriber] = None):
        self._transcriber = transcriber

    @property
    def name(self) -> str:
        return "TRANSCRIBING"

    async def execute(
        self,
        ctx: PipelineContext,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        if not ctx.audio_wav_path:
            raise ValueError("Audio wav path missing from pipeline context")

        transcriber = self._transcriber or FasterWhisperTranscriber(
            model_size_or_path=ctx.whisper_model,
            vad_filter=ctx.vad_filter,
        )

        start_time = time.time()
        ctx.raw_segments = []

        async def _segment_callback(raw_seg: RawSegment, progress_pct: float) -> None:
            ctx.raw_segments.append(raw_seg)
            elapsed = time.time() - start_time
            speed_factor = round(raw_seg.end_time / elapsed, 1) if elapsed > 0.5 else 1.0
            remaining_audio = max(0.0, ctx.duration - raw_seg.end_time) if ctx.duration > 0 else 0.0
            eta_seconds = round(remaining_audio / speed_factor, 1) if speed_factor > 0 else None

            if on_progress:
                # extra_data passes the newly discovered raw segment
                await on_progress(
                    progress_pct,
                    speed_factor,
                    eta_seconds,
                    f"Transcribing: segment #{raw_seg.index} recognized",
                    raw_seg,
                )

        if on_progress:
            await on_progress(0.0, None, None, "Initializing Whisper model...", None)

        await transcriber.transcribe(
            audio_path=ctx.audio_wav_path,
            language=ctx.source_language,
            on_segment_callback=_segment_callback,
        )

        if on_progress:
            await on_progress(100.0, None, None, f"Transcribed {len(ctx.raw_segments)} raw segments", None)
