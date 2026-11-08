"""
Job log report: what a run was asked to do (settings, models) and what it produced
(subtitle counts, warnings, Gemini usage and cost). Written to the per-job log by the
orchestrator; failures here never break the pipeline.
"""
import logging
from typing import Dict, Optional, Sequence

from sqlmodel import Session

from app.core.config import settings
from app.core.database import engine
from app.models.db_models import Job
from app.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


def log_job_start(
    ctx: PipelineContext,
    mode: str,
    only_failed: bool = False,
    rebuild_glossary: bool = False,
    only_indices: Optional[Sequence[int]] = None,
    hint: Optional[str] = None,
) -> None:
    """Write the job's settings to the job log at the start of a run."""
    try:
        from app.core.user_settings import load_settings

        s = load_settings()

        def call(c) -> str:
            return f"{c.model} (thinking {c.thinking}{', Flex' if c.flex else ''})"

        run = {"translate": "retranslate", "sync": "sync existing subtitles"}.get(mode, "full run")
        logger.info(f"===== Job {ctx.job_id}: {run} =====")
        if mode == "sync":
            logger.info(f"Subtitle file: {ctx.sync_file_name} | {len(ctx.sync_cues or [])} lines | "
                        f"language {ctx.sync_language} | Gemini matching {ctx.sync_use_gemini} | "
                        f"burned-in detection {settings.sync_detect_burned_in}")
        logger.info(f"File: {ctx.file_name} | duration {ctx.duration:.1f}s | path {ctx.video_path}")
        logger.info(
            f"Transcription: Whisper {ctx.whisper_model}, source language {ctx.source_language}, "
            f"VAD {ctx.vad_filter}, comms boost {ctx.comms_boost}, on-screen text {ctx.detect_onscreen_text}"
        )
        translation_model = ctx.gemini_model or s.translation.model
        logger.info(
            f"Gemini: translation {'on' if ctx.enable_translation else 'off'} -> {ctx.target_language}, "
            f"model {call(s.translation) if translation_model == s.translation.model else translation_model}; "
            f"glossary {call(s.glossary)}; vision {call(s.vision)}, frame detail {s.vision_media_resolution}"
        )
        if mode == "translate":
            logger.info(f"Retranslate: only failed={only_failed}, rebuild glossary={rebuild_glossary}, "
                        f"segments={len(ctx.refined_segments)}"
                        + (f", selected rows={len(only_indices)}" if only_indices else "")
                        + (f", hint: {hint}" if hint else ""))
    except Exception as e:  # logging must never break the pipeline
        logger.warning(f"Could not log the job settings: {e}")

def log_job_summary(job_id: str, ctx: Optional[PipelineContext]) -> None:
    """Write the result (subtitle counts, warnings, Gemini usage and cost) to the job log."""
    try:
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            counts: Dict[str, int] = {}
            for seg in job.segments:
                kind = seg.segment_type or "DIALOGUE"
                counts[kind] = counts.get(kind, 0) + 1
            untranslated = sum(1 for seg in job.segments if not seg.translated_text)
            failed = sum(1 for seg in job.segments if seg.translation_failed)
            logger.info(
                f"Result: status {job.status.value}, {len(job.segments)} subtitles {counts}, "
                f"untranslated {untranslated}, flagged failed {failed}"
            )
            if job.warning_message:
                logger.warning(f"Job warning: {job.warning_message}")
            if job.error_message:
                logger.error(f"Job error: {job.error_message}")
        if ctx is not None and ctx.usage is not None:
            for name, st in ctx.usage.stages.items():
                cost = "unknown" if st.cost_usd is None else f"${st.cost_usd:.4f}"
                logger.info(
                    f"Gemini {name}: {st.model}, {st.requests} request(s), in {st.input_tokens}, "
                    f"out {st.output_tokens}, thinking {st.thinking_tokens}, cost {cost}, "
                    f"Flex {st.flex_requests}, Flex->Standard {st.flex_fallbacks}"
                )
            loc = getattr(ctx.usage, "local", None)
            if loc is not None and (loc.requests > 0 or loc.saved_tokens > 0 or loc.frames_filtered > 0):
                logger.info(
                    f"Local AI & Cost-Saver: model {loc.model or 'local'}, {loc.requests} local query(s), "
                    f"local tokens {loc.input_tokens + loc.output_tokens}, frames filtered {loc.frames_filtered}, "
                    f"saved {loc.saved_tokens} cloud tokens (~${loc.saved_cost_usd:.4f})"
                )
        logger.info(f"===== Job {job_id}: end =====")
    except Exception as e:
        logger.warning(f"Could not log the job summary: {e}")
