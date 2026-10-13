import asyncio
import math
import logging
from typing import List, Optional
from faster_whisper import WhisperModel

from app.core.config import settings
from app.core.hardware import get_hardware_info
from app.pipeline.context import RawSegment, TranscribedWord
from app.providers.transcription.base import BaseTranscriber, SegmentProgressCallback

logger = logging.getLogger(__name__)


class FasterWhisperTranscriber(BaseTranscriber):
    """
    Local Speech-to-Text provider powered by faster-whisper (CTranslate2).
    Supports CUDA FP16 and CPU INT8, Silero VAD filtering, and word-level timestamps.
    """

    def __init__(
        self,
        model_size_or_path: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
        vad_filter: bool = True,
    ):
        self.model_name = model_size_or_path or settings.whisper_default_model
        hw_info = get_hardware_info()

        # Resolve device
        target_device = device or settings.whisper_device
        if target_device == "auto":
            target_device = "cuda" if hw_info["cuda_available"] else "cpu"
        self.device = target_device

        # Resolve compute type
        target_compute = compute_type or settings.whisper_compute_type
        if target_compute == "auto":
            target_compute = "float16" if self.device == "cuda" else "int8"
        self.compute_type = target_compute

        self.vad_filter = vad_filter
        self._model: Optional[WhisperModel] = None

    def _get_or_load_model(self) -> WhisperModel:
        """Load Faster-Whisper model on demand and cache instance."""
        if self._model is None:
            logger.info(
                f"Loading Faster-Whisper model '{self.model_name}' on {self.device} ({self.compute_type})..."
            )
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        on_segment_callback: Optional[SegmentProgressCallback] = None,
    ) -> List[RawSegment]:
        """
        Transcribe audio file asynchronously without blocking the event loop.
        Streams each segment through on_segment_callback as it is recognized.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        lang_arg = None if (not language or language.lower() in ("auto", "none")) else language

        def _worker():
            try:
                model = self._get_or_load_model()
                segments_iter, info = model.transcribe(
                    audio_path,
                    language=lang_arg,
                    word_timestamps=True,
                    vad_filter=self.vad_filter,
                )

                duration = float(info.duration) if info.duration else 1.0
                loop.call_soon_threadsafe(queue.put_nowait, ("INFO", duration))

                idx = 1
                for s in segments_iter:
                    words = [
                        TranscribedWord(
                            word=w.word,
                            start=w.start,
                            end=w.end,
                            probability=float(getattr(w, "probability", 1.0)),
                        )
                        for w in (getattr(s, "words", []) or [])
                    ]
                    # Convert avg_logprob to approximate confidence 0.0 - 1.0
                    conf = max(0.0, min(1.0, math.exp(s.avg_logprob))) if getattr(s, "avg_logprob", None) else 0.95
                    raw_seg = RawSegment(
                        index=idx,
                        start_time=float(s.start),
                        end_time=float(s.end),
                        text=s.text.strip(),
                        confidence=round(conf, 2),
                        words=words,
                    )
                    idx += 1
                    loop.call_soon_threadsafe(queue.put_nowait, ("SEGMENT", raw_seg))

                loop.call_soon_threadsafe(queue.put_nowait, ("DONE", None))
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("ERROR", e))

        # Launch worker in background thread
        worker_task = asyncio.to_thread(_worker)
        asyncio.create_task(worker_task)

        results: List[RawSegment] = []
        audio_duration = 1.0

        while True:
            msg_type, payload = await queue.get()
            if msg_type == "INFO":
                audio_duration = max(1.0, float(payload))
            elif msg_type == "SEGMENT":
                seg: RawSegment = payload
                results.append(seg)
                progress_pct = min(99.0, round((seg.end_time / audio_duration) * 100, 2))
                if on_segment_callback:
                    await on_segment_callback(seg, progress_pct)
            elif msg_type == "DONE":
                break
            elif msg_type == "ERROR":
                raise RuntimeError(f"Whisper transcription failed: {payload}") from payload

        return results
