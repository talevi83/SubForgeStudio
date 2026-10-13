from abc import ABC, abstractmethod
from typing import Callable, Awaitable, List, Optional
from app.pipeline.context import RawSegment

SegmentProgressCallback = Callable[[RawSegment, float], Awaitable[None]]


class BaseTranscriber(ABC):
    """Abstract interface for all speech-to-text transcription providers."""

    @abstractmethod
    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        on_segment_callback: Optional[SegmentProgressCallback] = None,
    ) -> List[RawSegment]:
        """
        Transcribe an audio file to raw timed segments.
        on_segment_callback: (segment, progress_percent) -> Awaitable[None]
        """
        pass
