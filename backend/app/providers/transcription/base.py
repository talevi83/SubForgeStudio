import threading
from abc import ABC, abstractmethod
from typing import Any, Callable, Awaitable, Dict, List, Optional, Tuple
from app.pipeline.context import RawSegment

SegmentProgressCallback = Callable[[RawSegment, float], Awaitable[None]]


class BaseTranscriber(ABC):
    """Abstract interface for all speech-to-text transcription providers."""

    #: ISO code of the language detected during the last transcribe() call
    detected_language: Optional[str] = None

    @abstractmethod
    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        on_segment_callback: Optional[SegmentProgressCallback] = None,
        cancel_flag: Optional[threading.Event] = None,
    ) -> List[RawSegment]:
        """
        Transcribe an audio file to raw timed segments.
        on_segment_callback: (segment, progress_percent) -> Awaitable[None]
        cancel_flag: when set, transcription stops and PipelineCancelled is raised.
        """
        pass

    async def analyze_foreign_speech(
        self,
        audio_path: str,
        main_language: Optional[str],
        spoken: List[RawSegment],
        cancel_flag: Optional[threading.Event] = None,
    ) -> Tuple[List[Dict[str, Any]], List[RawSegment]]:
        """Find passages in other languages; returns (spans, fallback segments). Optional."""
        return [], []

    def unload_model(self) -> None:
        """Free the loaded transcription model from GPU/CPU memory."""
        return None
