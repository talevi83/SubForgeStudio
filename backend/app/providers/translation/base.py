from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, Dict, List, Optional


@dataclass
class TranslatableSegment:
    """Lightweight data contract passed into translation providers."""
    index: int
    start_time: float
    end_time: float
    original_text: str
    translated_text: Optional[str] = None
    translation_failed: bool = False
    segment_type: str = "DIALOGUE"  # DIALOGUE | COMMS | ON_SCREEN

    @property
    def duration(self) -> float:
        return round(max(0.1, self.end_time - self.start_time), 2)


# Callback type emitted after each batch completes for WebSocket streaming
BatchProgressCallback = Callable[[List[TranslatableSegment], float], Awaitable[None]]


class BaseTranslator(ABC):
    """Abstract interface for all subtitle translation providers."""

    async def build_glossary(
        self,
        segments: List[TranslatableSegment],
        source_lang: str,
        target_lang: str,
        context_notes: str = "",
        known_glossary: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Optionally analyse the whole transcript once (characters, genders, terms)."""
        return None

    @abstractmethod
    async def translate_segments(
        self,
        segments: List[TranslatableSegment],
        source_lang: str,
        target_lang: str,
        context_notes: str = "",
        apply_rtl: bool = False,
        on_batch_complete: Optional[BatchProgressCallback] = None,
        glossary: Optional[Dict[str, Any]] = None,
        context_segments: Optional[List[TranslatableSegment]] = None,
    ) -> List[TranslatableSegment]:
        """
        Translate segments while strictly preserving indices and timestamps.

        Segments that could not be translated must be returned with
        translation_failed=True (and translated_text left as None) — never silently
        filled with the source text.

        context_segments: the full, ordered segment list of the job. When only a subset
        is being (re)translated, neighbouring context is taken from here.
        """
        pass
