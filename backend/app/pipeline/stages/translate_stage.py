import logging
from typing import List, Optional, Dict, Any
from app.pipeline.context import PipelineContext
from app.pipeline.stages.base import PipelineStage, ProgressCallback
from app.providers.translation.base import BaseTranslator, TranslatableSegment
from app.providers.translation.noop import NoopTranslator
from app.providers.translation.gemini_provider import GeminiTranslator

logger = logging.getLogger(__name__)


class TranslateStage(PipelineStage):
    """Pipeline stage executing contextual batch subtitle translation via Gemini or Noop."""

    def __init__(self, translator: Optional[BaseTranslator] = None):
        self._translator = translator

    @property
    def name(self) -> str:
        return "TRANSLATING"

    async def execute(
        self,
        ctx: PipelineContext,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        if not ctx.enable_translation:
            if on_progress:
                await on_progress(100.0, None, None, "Translation disabled; skipping", None)
            ctx.translated_segments = ctx.refined_segments
            return

        if not ctx.refined_segments:
            if on_progress:
                await on_progress(100.0, None, None, "No segments to translate", None)
            ctx.translated_segments = []
            return

        # Prepare translatable segments
        translatable: List[TranslatableSegment] = [
            TranslatableSegment(
                index=s["index"],
                start_time=s["start_time"],
                end_time=s["end_time"],
                original_text=s["original_text"],
            )
            for s in ctx.refined_segments
        ]

        translator = self._translator
        if translator is None:
            try:
                translator = GeminiTranslator(
                    api_key=ctx.gemini_api_key_override,
                    model_name=ctx.gemini_model,
                )
            except Exception as e:
                logger.error(f"Failed to initialize GeminiTranslator: {e}")
                # If key missing or failed, fall back to Noop
                translator = NoopTranslator()

        async def _on_batch_complete(batch: List[TranslatableSegment], progress_pct: float) -> None:
            # Map batch translations back to refined segments
            batch_dict = {b.index: b.translated_text for b in batch}
            for s in ctx.refined_segments:
                if s["index"] in batch_dict:
                    s["translated_text"] = batch_dict[s["index"]]

            if on_progress:
                await on_progress(
                    progress_pct,
                    None,
                    None,
                    f"Translated batch up to #{batch[-1].index}",
                    batch,
                )

        if on_progress:
            await on_progress(0.0, None, None, f"Starting translation to {ctx.target_language}...", None)

        translated = await translator.translate_segments(
            segments=translatable,
            source_lang=ctx.source_language or "English",
            target_lang=ctx.target_language,
            context_notes=ctx.context_notes or "",
            apply_rtl=True,
            on_batch_complete=_on_batch_complete,
        )

        # Merge back onto ctx.refined_segments
        trans_map = {t.index: t.translated_text for t in translated}
        for s in ctx.refined_segments:
            if s["index"] in trans_map:
                s["translated_text"] = trans_map[s["index"]]

        ctx.translated_segments = ctx.refined_segments

        if on_progress:
            await on_progress(100.0, None, None, f"Completed translation for {len(translated)} segments", None)
