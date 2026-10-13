from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Awaitable, List, Optional


@dataclass
class TranslatableSegment:
    """Lightweight data contract passed into translation providers."""
    index: int
    start_time: float
    end_time: float
    original_text: str
    translated_text: Optional[str] = None

    @property
    def duration(self) -> float:
        return round(max(0.1, self.end_time - self.start_time), 2)


# Callback type emitted after each batch completes for WebSocket streaming
BatchProgressCallback = Callable[[List[TranslatableSegment], float], Awaitable[None]]


class BaseTranslator(ABC):
    """Abstract interface for all subtitle translation providers."""

    @abstractmethod
    async def translate_segments(
        self,
        segments: List[TranslatableSegment],
        source_lang: str,
        target_lang: str,
        context_notes: str = "",
        apply_rtl: bool = True,
        on_batch_complete: Optional[BatchProgressCallback] = None,
    ) -> List[TranslatableSegment]:
        """
        Translate a full list of subtitle segments while strictly preserving
        indices and timestamps.
        """
        pass
