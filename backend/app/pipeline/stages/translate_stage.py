import logging
from typing import Awaitable, Callable, Dict, List, Optional
from app.core.config import settings
from app.core.glossary_profiles import load_profile, merge_glossary, save_profile
from app.pipeline.context import PipelineContext, PipelineCancelled
from app.pipeline.stages.base import PipelineStage, ProgressCallback
from app.providers.translation.base import BaseTranslator, TranslatableSegment
from app.utils.languages import language_name
from app.utils.srt_formatter import calculate_cps

logger = logging.getLogger(__name__)

# Called after each translated batch so the orchestrator can persist it immediately
BatchPersistCallback = Callable[[List[TranslatableSegment]], Awaitable[None]]


def _notes_with_hint(notes: Optional[str], hint: Optional[str], rows: List[TranslatableSegment]) -> str:
    """The user's show notes, plus a note that applies only to the rows being re-translated now."""
    base = (notes or "").strip()
    if not hint or not hint.strip() or not rows:
        return base
    first, last = min(r.index for r in rows), max(r.index for r in rows)
    scoped = (
        f"NOTE FOR THE SEGMENTS YOU ARE TRANSLATING NOW (#{first}-#{last}); it overrides everything else, "
        f"including the glossary: {hint.strip()}"
    )
    return f"{base}\n\n{scoped}" if base else scoped


class TranslateStage(PipelineStage):
    """Pipeline stage executing contextual batch subtitle translation via Gemini."""

    def __init__(
        self,
        translator: Optional[BaseTranslator] = None,
        on_batch_persist: Optional[BatchPersistCallback] = None,
    ):
        self._translator = translator
        self._on_batch_persist = on_batch_persist

    @property
    def name(self) -> str:
        return "TRANSLATING"

    def _make_translator(self, ctx: PipelineContext) -> BaseTranslator:
        from app.core.user_settings import load_settings
        from app.providers.translation.gemini_provider import GeminiTranslator

        app_settings = load_settings()
        # Thinking and Flex are configured for the Settings model; a per-job override uses defaults
        same_model = ctx.gemini_model in (None, app_settings.translation.model)
        return GeminiTranslator(
            api_key=ctx.gemini_api_key_override,
            model_name=ctx.gemini_model or app_settings.translation.model,
            thinking=ctx.gemini_thinking or (app_settings.translation.thinking if same_model else "default"),
            glossary_model=app_settings.glossary.model,
            glossary_thinking=app_settings.glossary.thinking,
            usage=ctx.usage,
            flex=app_settings.translation.flex if same_model else False,
            glossary_flex=app_settings.glossary.flex,
            flex_state=ctx.flex_state,
        )

    async def execute(
        self,
        ctx: PipelineContext,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        ctx.translated_segments = ctx.refined_segments
        if not ctx.enable_translation:
            if on_progress:
                await on_progress(100.0, None, None, "Translation disabled; skipping", None)
            return

        if not ctx.refined_segments:
            if on_progress:
                await on_progress(100.0, None, None, "No segments to translate", None)
            return

        translator = self._translator
        if translator is None:
            try:
                translator = self._make_translator(ctx)
            except Exception as e:
                # Keep the transcription; tell the user instead of silently skipping.
                ctx.translation_warning = (
                    f"Translation skipped: {e} Add the key and use 'Retranslate' — "
                    f"the transcription is saved."
                )
                logger.warning(ctx.translation_warning)
                if on_progress:
                    await on_progress(100.0, None, None, ctx.translation_warning, None)
                return

        by_index: Dict[int, dict] = {s["index"]: s for s in ctx.refined_segments}
        all_segments: List[TranslatableSegment] = [
            TranslatableSegment(
                index=s["index"],
                start_time=s["start_time"],
                end_time=s["end_time"],
                original_text=s["original_text"],
                translated_text=s.get("translated_text"),
                translation_failed=bool(s.get("translation_failed")),
                segment_type=s.get("segment_type") or "DIALOGUE",
            )
            for s in ctx.refined_segments
        ]
        if ctx.only_indices:
            wanted = set(ctx.only_indices)
            to_translate = [s for s in all_segments if s.index in wanted]
            for s in to_translate:
                s.translated_text = None
                s.translation_failed = False
        elif ctx.only_failed:
            to_translate = [s for s in all_segments if s.translation_failed or not s.translated_text]
        else:
            to_translate = all_segments
            for s in to_translate:
                s.translated_text = None
                s.translation_failed = False

        if not to_translate:
            if on_progress:
                await on_progress(100.0, None, None, "Nothing left to translate", None)
            return

        source_lang = language_name(ctx.effective_source_language, default="the original language")
        target_lang = ctx.target_language

        async def _on_batch_complete(batch: List[TranslatableSegment], progress_pct: float) -> None:
            for b in batch:
                seg = by_index.get(b.index)
                if seg is None:
                    continue
                seg["translated_text"] = b.translated_text
                seg["translation_failed"] = b.translation_failed
                seg["cps"] = calculate_cps(
                    b.translated_text or seg["original_text"],
                    max(0.1, seg["end_time"] - seg["start_time"]),
                )
            if self._on_batch_persist:
                await self._on_batch_persist(batch)
            if on_progress:
                await on_progress(
                    10.0 + progress_pct * 0.9,
                    None,
                    None,
                    f"Translated batch up to #{batch[-1].index}",
                    batch,
                )
            # Raising here stops the translator between batches
            if ctx.is_cancelled:
                raise PipelineCancelled()

        from app.providers.translation.gemini_provider import TranslationAborted

        try:
            known = load_profile(ctx.glossary_profile)
            if settings.gemini_glossary_pass and (ctx.glossary is None or ctx.rebuild_glossary):
                if on_progress:
                    await on_progress(2.0, None, None, "Analysing characters and terminology...", None)
                extra = {"known_glossary": known} if known else {}
                ctx.glossary = merge_glossary(
                    known,
                    await translator.build_glossary(
                        all_segments, source_lang, target_lang, ctx.context_notes or "", **extra
                    ),
                )
                if known and ctx.glossary:
                    # The series profile grows with every episode: new characters are added, the rest stays
                    try:
                        save_profile(ctx.glossary_profile, ctx.glossary)
                    except (OSError, ValueError) as e:
                        logger.warning(f"Could not update glossary profile '{ctx.glossary_profile}': {e}")
                glossary_error = getattr(translator, "last_glossary_error", None)
                if ctx.glossary is None and glossary_error:
                    ctx.warnings.append(f"Glossary pass skipped — {glossary_error[:300]}")
                if on_progress:
                    await on_progress(6.0, None, None, "Glossary analysis complete", None)
                ctx.raise_if_cancelled()
            elif ctx.glossary is None and known:
                ctx.glossary = known

            effective_notes = _notes_with_hint(ctx.context_notes, ctx.translation_hint, to_translate)
            try:
                from app.core.user_settings import load_settings

                if load_settings().cost_saver_mode:
                    from app.pipeline.gender_pretag import (
                        format_hints_for_context_notes,
                        is_gendered_target_language,
                        pretag_gender_hints,
                    )

                    if is_gendered_target_language(target_lang):
                        async def _on_local_batch() -> None:
                            if on_progress:
                                await on_progress(8.0, None, None, "Pre-tagging speaker gender with local AI...", None)

                        local_hints = await pretag_gender_hints(
                            to_translate,
                            target_lang=target_lang,
                            glossary=ctx.glossary,
                            usage=ctx.usage,
                            cloud_model=getattr(translator, "model_name", None),
                            on_batch=_on_local_batch,
                        )
                        if local_hints:
                            hint_block = format_hints_for_context_notes(local_hints)
                            effective_notes = f"{effective_notes}\n\n{hint_block}".strip() if effective_notes else hint_block
            except Exception as e:
                logger.debug(f"Local gender pre-tagging skipped: {e}")

            if on_progress:
                await on_progress(10.0, None, None, f"Translating {len(to_translate)} segments to {target_lang}...", None)

            await translator.translate_segments(
                segments=to_translate,
                source_lang=source_lang,
                target_lang=target_lang,
                context_notes=effective_notes,
                apply_rtl=False,  # stored clean; RTL marks are added at export time
                on_batch_complete=_on_batch_complete,
                glossary=ctx.glossary,
                context_segments=all_segments,
            )
        except TranslationAborted as e:
            for b in to_translate:
                if not b.translated_text:
                    b.translation_failed = True
                    by_index[b.index]["translation_failed"] = True
            if self._on_batch_persist:
                await self._on_batch_persist([b for b in to_translate if not b.translated_text])
            ctx.translation_warning = f"Translation stopped: {e}"
            logger.error(ctx.translation_warning)
            return

        failed = sum(1 for s in ctx.refined_segments if s.get("translation_failed"))
        if failed:
            ctx.translation_warning = (
                f"{failed} segment(s) could not be translated. Use 'Retranslate failed' to retry them."
            )

        if on_progress:
            await on_progress(100.0, None, None, f"Translation finished ({len(to_translate) - failed} ok, {failed} failed)", None)
