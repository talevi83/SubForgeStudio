from typing import List, Optional
from app.providers.translation.base import (
    BaseTranslator,
    TranslatableSegment,
    BatchProgressCallback,
)


class NoopTranslator(BaseTranslator):
    """Pass-through translation provider when translation is disabled."""

    async def translate_segments(
        self,
        segments: List[TranslatableSegment],
        source_lang: str = "English",
        target_lang: str = "Hebrew",
        context_notes: str = "",
        apply_rtl: bool = True,
        on_batch_complete: Optional[BatchProgressCallback] = None,
    ) -> List[TranslatableSegment]:
        if on_batch_complete and segments:
            await on_batch_complete(segments, 100.0)
        return segments
