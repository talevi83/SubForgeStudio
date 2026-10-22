import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Coroutine, Dict, List, Optional, Set

from sqlmodel import Session, select

from app.core.config import settings
from app.core.model_catalog import UsageTracker
from app.core.database import engine
from app.core.job_logging import job_context
from app.core.ws_manager import ws_manager
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord
from app.models.schemas import AudioStreamInfo
from app.pipeline.context import PipelineCancelled, PipelineContext, RawSegment
from app.pipeline.stages.audio_stage import AudioExtractionStage
from app.pipeline.stages.base import PipelineStage
from app.pipeline.stages.ocr_stage import OnScreenTextStage, remove_frames
from app.pipeline.stages.probe_stage import ProbeStage
from app.pipeline.stages.refine_stage import RefineStage
from app.pipeline.stages.sync_stage import SyncStage, sync_summary
from app.pipeline.stages.transcribe_stage import TranscribeStage
from app.pipeline.stages.translate_stage import TranslateStage
from app.providers.gemini_calls import FlexState
from app.providers.translation.base import TranslatableSegment
from app.utils.rtl_fixer import strip_directional_marks

logger = logging.getLogger(__name__)

# Live pipelines by job id (used for cancellation)
active_pipelines: Dict[str, "PipelineOrchestrator"] = {}

# Strong references to running background tasks. The event loop only keeps weak
# references, so a task created with asyncio.create_task() and not stored anywhere
# may be garbage-collected before it finishes.
_background_tasks: Set[asyncio.Task] = set()

# Limits how many jobs transcribe at once (each one loads a model onto the GPU).
_gpu_semaphore: Optional[asyncio.Semaphore] = None

ACTIVE_STATUSES = {
    JobStatus.IDLE,
    JobStatus.QUEUED,
    JobStatus.PROBING,
    JobStatus.EXTRACTING_AUDIO,
    JobStatus.TRANSCRIBING,
    JobStatus.SCANNING_ONSCREEN,
    JobStatus.REFINING,
    JobStatus.TRANSLATING,
    JobStatus.SYNCING,
}

# Relative share of the overall progress bar per stage (transcription dominates runtime)
STAGE_WEIGHTS = {
    JobStatus.PROBING: 2,
    JobStatus.EXTRACTING_AUDIO: 6,
    JobStatus.TRANSCRIBING: 62,
    JobStatus.SCANNING_ONSCREEN: 12,
    JobStatus.REFINING: 2,
    JobStatus.TRANSLATING: 28,
    JobStatus.SYNCING: 10,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_gpu_semaphore() -> asyncio.Semaphore:
    global _gpu_semaphore
    if _gpu_semaphore is None:
        _gpu_semaphore = asyncio.Semaphore(max(1, settings.max_concurrent_transcriptions))
    return _gpu_semaphore


def spawn_background(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Schedule a coroutine as a background task while holding a strong reference to it."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def recover_interrupted_jobs() -> int:
    """
    Fix up jobs left in a non-terminal state by a crash or restart. Must be called on
    startup, before any new pipeline is launched. Jobs that already have subtitles
    (interrupted while translating) are kept usable; others are marked FAILED.
    Returns the number of recovered jobs.
    """
    with Session(engine) as session:
        stale = session.exec(select(Job).where(Job.status.in_(ACTIVE_STATUSES))).all()
        for job in stale:
            stage = job.status.value
            if job.segments:
                untranslated = 0
                if job.enable_translation:
                    for seg in job.segments:
                        if not seg.translated_text:
                            seg.translation_failed = True
                            untranslated += 1
                            session.add(seg)
                job.status = JobStatus.COMPLETED
                job.progress_percent = 100.0
                job.warning_message = (
                    f"Interrupted during {stage} (server was restarted). "
                    + (f"{untranslated} segment(s) untranslated — use 'Retranslate failed'." if untranslated else "")
                ).strip()
            else:
                job.error_message = f"Interrupted during {stage} (server was restarted)"
                job.status = JobStatus.FAILED
            job.updated_at = _now()
            session.add(job)
        session.commit()
        return len(stale)


def _segment_payload(job_id: str, seg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": seg.get("id"),
        "job_id": job_id,
        "index": seg["index"],
        "start_time": seg["start_time"],
        "end_time": seg["end_time"],
        "original_text": seg["original_text"],
        "translated_text": seg.get("translated_text"),
        "translation_failed": bool(seg.get("translation_failed")),
        "segment_type": seg.get("segment_type", "DIALOGUE"),
        "confidence": seg.get("confidence", 1.0),
        "cps": seg.get("cps", 0.0),
    }


class PipelineOrchestrator:
    """
    Runs the pipeline stages for one job and coordinates DB state and WebSocket events.

    mode="full":      probe → extract audio → transcribe → refine → translate
    mode="translate": (re)translate the job's existing segments only
    mode="sync":      probe → extract audio → transcribe (→ on-screen text) → sync the user's subtitle file
    """

    def __init__(
        self,
        job_id: str,
        mode: str = "full",
        api_key: Optional[str] = None,
        only_failed: bool = False,
        rebuild_glossary: bool = False,
        only_indices: Optional[List[int]] = None,
        hint: Optional[str] = None,
        thinking: Optional[str] = None,
    ):
        self.job_id = job_id
        self.mode = mode
        self.api_key = api_key  # kept in memory only
        self.only_failed = only_failed
        self.rebuild_glossary = rebuild_glossary
        self.only_indices = only_indices
        self.hint = hint
        self.thinking = thinking
        self.cancel_event = asyncio.Event()
        self.cancel_flag = threading.Event()
        self._gpu_held = False
        self._segments_saved = False
        self._prior_usage_dict: Optional[Dict[str, Any]] = None
        self._ctx: Optional[PipelineContext] = None
        active_pipelines[job_id] = self

    # ------------------------------------------------------------------ control

    @classmethod
    def cancel_job(cls, job_id: str) -> bool:
        """Signal cancellation to an active job. Returns False if no live pipeline exists."""
        pipeline = active_pipelines.get(job_id)
        if pipeline is None:
            return False
        pipeline.cancel_flag.set()
        pipeline.cancel_event.set()
        return True

    @classmethod
    def is_running(cls, job_id: str) -> bool:
        return job_id in active_pipelines

    # ------------------------------------------------------------------ helpers

    def _build_context(self) -> Optional[PipelineContext]:
        with Session(engine) as session:
            job = session.get(Job, self.job_id)
            if not job:
                logger.error(f"Job {self.job_id} not found in database.")
                return None
            streams: List[AudioStreamInfo] = []
            if job.audio_streams_json:
                try:
                    streams = [AudioStreamInfo(**s) for s in json.loads(job.audio_streams_json)]
                except Exception:
                    pass
            glossary = None
            if job.glossary_json:
                try:
                    glossary = json.loads(job.glossary_json)
                except Exception:
                    pass
            if job.usage_json:
                try:
                    self._prior_usage_dict = json.loads(job.usage_json)
                except Exception:
                    self._prior_usage_dict = None
            ctx = PipelineContext(
                job_id=job.id,
                video_path=job.video_path,
                file_name=job.file_name,
                duration=job.video_duration or 0.0,
                audio_streams=streams,
                selected_audio_stream=job.selected_audio_stream,
                whisper_model=job.whisper_model,
                source_language=job.source_language,
                detected_language=job.detected_language,
                vad_filter=job.vad_filter,
                comms_boost=job.comms_boost,
                use_embedded_subtitles=getattr(job, "use_embedded_subtitles", True),
                detect_onscreen_text=job.detect_onscreen_text,
                enable_translation=job.enable_translation or self.mode == "translate",
                target_language=job.target_language,
                gemini_model=job.gemini_model,
                gemini_api_key_override=self.api_key,
                context_notes=job.context_notes,
                glossary=glossary,
                glossary_profile=job.glossary_profile,
                rebuild_glossary=self.rebuild_glossary,
                only_failed=self.only_failed,
                only_indices=self.only_indices,
                translation_hint=self.hint,
                gemini_thinking=self.thinking,
                cancel_flag=self.cancel_flag,
            )
            if self.mode == "sync" and job.sync_source_json:
                source = json.loads(job.sync_source_json)
                ctx.sync_cues = source.get("cues") or []
                ctx.sync_language = source.get("language")
                ctx.sync_use_gemini = bool(source.get("use_gemini", True))
                ctx.sync_file_name = job.sync_file_name
                ctx.enable_translation = False
            ctx.usage = UsageTracker()
            ctx.flex_state = FlexState()
            if self.mode == "translate":
                ctx.refined_segments = [
                    {
                        "id": s.id,
                        "index": s.index,
                        "start_time": s.start_time,
                        "end_time": s.end_time,
                        "original_text": s.original_text,
                        "translated_text": strip_directional_marks(s.translated_text) if s.translated_text else None,
                        "translation_failed": s.translation_failed,
                        "segment_type": s.segment_type or "DIALOGUE",
                        "confidence": s.confidence,
                        "cps": s.cps,
                    }
                    for s in job.segments
                ]
                self._segments_saved = True
            self._ctx = ctx
            return ctx

    def _stages(self, ctx: PipelineContext) -> List[tuple]:
        translate = TranslateStage(on_batch_persist=self._persist_translations)
        if self.mode == "translate":
            return [(JobStatus.TRANSLATING, translate)]
        if self.mode == "sync":
            stages_sync: List[tuple] = [
                (JobStatus.PROBING, ProbeStage()),
                (JobStatus.EXTRACTING_AUDIO, AudioExtractionStage()),
                (JobStatus.TRANSCRIBING, TranscribeStage()),
            ]
            if ctx.detect_onscreen_text:
                stages_sync.append((JobStatus.SCANNING_ONSCREEN, OnScreenTextStage()))
            stages_sync.append((JobStatus.SYNCING, SyncStage()))
            return stages_sync
        stages: List[tuple] = [
            (JobStatus.PROBING, ProbeStage()),
            (JobStatus.EXTRACTING_AUDIO, AudioExtractionStage()),
            (JobStatus.TRANSCRIBING, TranscribeStage()),
        ]
        if ctx.detect_onscreen_text:
            stages.append((JobStatus.SCANNING_ONSCREEN, OnScreenTextStage()))
        stages.append((JobStatus.REFINING, RefineStage()))
        if ctx.enable_translation:
            stages.append((JobStatus.TRANSLATING, translate))
        return stages

    def _update_job(self, **fields: Any) -> None:
        with Session(engine) as session:
            job = session.get(Job, self.job_id)
            if job:
                for key, value in fields.items():
                    setattr(job, key, value)
                job.updated_at = _now()
                session.add(job)
                session.commit()

    def _current_usage(self, ctx: Optional[PipelineContext] = None) -> Optional[Dict[str, Any]]:
        c = ctx if ctx is not None else self._ctx
        tracker = getattr(c, "usage", None) if c is not None else None
        total = UsageTracker()
        if self._prior_usage_dict:
            total.merge_dict(self._prior_usage_dict)
        if tracker is not None and hasattr(tracker, "to_dict"):
            total.merge_dict(tracker.to_dict())
        return total.to_dict() if total.has_activity() else None

    async def _broadcast(self, payload: Dict[str, Any]) -> None:
        payload.setdefault("job_id", self.job_id)
        if "usage" not in payload:
            u = self._current_usage()
            if u is not None:
                payload["usage"] = u
        await ws_manager.broadcast(self.job_id, payload)

    async def _stop_frame_sampling(self, ctx: Optional[PipelineContext]) -> None:
        task = getattr(ctx, "frame_task", None) if ctx is not None else None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        remove_frames(self.job_id)

    async def _stop_burned_detection(self, ctx: Optional[PipelineContext]) -> None:
        task = getattr(ctx, "burned_task", None) if ctx is not None else None
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _persist_usage(self, ctx: Optional[PipelineContext]) -> None:
        """Persist this run's accumulated Gemini + local AI usage to the job (idempotent)."""
        merged = self._current_usage(ctx)
        if not merged:
            return
        try:
            with Session(engine) as session:
                job = session.get(Job, self.job_id)
                if not job:
                    return
                job.usage_json = json.dumps(merged)
                session.add(job)
                session.commit()
        except Exception as e:  # never break the pipeline over bookkeeping
            logger.warning(f"Could not save Gemini usage: {e}")

    def _delete_audio(self) -> None:
        wav = settings.storage_dir / "audio" / f"{self.job_id}.wav"
        try:
            if wav.exists():
                wav.unlink()
        except OSError as e:  # pragma: no cover - file locked on Windows etc.
            logger.warning(f"Could not delete {wav}: {e}")

    async def _acquire_gpu(self, overall_pct: float) -> None:
        sem = _get_gpu_semaphore()
        if sem.locked():
            self._update_job(status=JobStatus.QUEUED, current_stage="QUEUED", progress_percent=overall_pct)
            await self._broadcast({
                "event": "STAGE_PROGRESS",
                "stage": JobStatus.QUEUED.value,
                "progress_percent": overall_pct,
                "message": "Waiting for another job to finish transcribing...",
            })
        acquire = asyncio.ensure_future(sem.acquire())
        cancelled = asyncio.ensure_future(self.cancel_event.wait())
        await asyncio.wait({acquire, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        cancelled.cancel()
        if acquire.done() and not acquire.cancelled():
            self._gpu_held = True
        else:
            acquire.cancel()
        if self.cancel_flag.is_set():
            raise PipelineCancelled()

    def _release_gpu(self) -> None:
        if self._gpu_held:
            self._gpu_held = False
            _get_gpu_semaphore().release()

    def _save_segments(self, segments: List[Dict[str, Any]]) -> None:
        """Replace the job's segments and write the new DB ids back into the dicts."""
        with Session(engine) as session:
            for seg in session.exec(select(SubtitleSegmentRecord).where(SubtitleSegmentRecord.job_id == self.job_id)).all():
                session.delete(seg)
            records = []
            for s in segments:
                rec = SubtitleSegmentRecord(
                    job_id=self.job_id,
                    index=s["index"],
                    start_time=s["start_time"],
                    end_time=s["end_time"],
                    original_text=s["original_text"],
                    translated_text=s.get("translated_text"),
                    translation_failed=bool(s.get("translation_failed")),
                    segment_type=s.get("segment_type", "DIALOGUE"),
                    confidence=s.get("confidence", 1.0),
                    cps=s.get("cps", 0.0),
                )
                session.add(rec)
                records.append(rec)
            session.commit()
            for s, rec in zip(segments, records):
                s["id"] = rec.id
        self._segments_saved = True

    async def _persist_translations(self, batch: List[TranslatableSegment]) -> None:
        """Write translated batches as they arrive, so nothing is lost on a crash."""
        if not batch:
            return
        by_index = {b.index: b for b in batch}
        with Session(engine) as session:
            rows = session.exec(
                select(SubtitleSegmentRecord).where(
                    SubtitleSegmentRecord.job_id == self.job_id,
                    SubtitleSegmentRecord.index.in_(list(by_index.keys())),
                )
            ).all()
            from app.utils.srt_formatter import calculate_cps

            for row in rows:
                b = by_index[row.index]
                row.translated_text = b.translated_text
                row.translation_failed = b.translation_failed
                row.cps = calculate_cps(b.translated_text or row.original_text, max(0.1, row.end_time - row.start_time))
                session.add(row)
            session.commit()
            ids = {row.index: row.id for row in rows}
        await self._broadcast({
            "event": "BATCH_TRANSLATED",
            "segments_batch": [
                {
                    "id": ids.get(b.index),
                    "job_id": self.job_id,
                    "index": b.index,
                    "start_time": b.start_time,
                    "end_time": b.end_time,
                    "original_text": b.original_text,
                    "translated_text": b.translated_text,
                    "translation_failed": b.translation_failed,
                    "segment_type": b.segment_type,
                }
                for b in batch
            ],
        })

    # ------------------------------------------------------------------ main

    async def run(self) -> None:
        """Run the pipeline lifecycle for the job (its log lines also go to the job's own log)."""
        with job_context(self.job_id):
            await self._run()

    async def _run(self) -> None:
        ctx: Optional[PipelineContext] = None
        try:
            ctx = self._build_context()
            if ctx is None:
                return
            self._log_start(ctx)
            stages = self._stages(ctx)
            total_weight = sum(STAGE_WEIGHTS[s] for s, _ in stages) or 1
            done_weight = 0.0

            for stage_status, stage in stages:
                ctx.raise_if_cancelled()
                weight = STAGE_WEIGHTS[stage_status]
                base_pct = done_weight / total_weight * 100

                if stage_status == JobStatus.TRANSCRIBING:
                    if not ctx.embedded_dialogue_segments:
                        await self._acquire_gpu(round(base_pct, 1))

                self._update_job(
                    status=stage_status,
                    current_stage=stage.name,
                    progress_percent=round(base_pct, 1),
                    error_message=None,
                    warning_message=None,
                )
                await self._broadcast({
                    "event": "STAGE_PROGRESS",
                    "stage": stage_status.value,
                    "progress_percent": round(base_pct, 1),
                })

                logger.info(f"Stage {stage_status.value} started")
                began = time.monotonic()
                await stage.execute(ctx, on_progress=self._progress_callback(stage_status, base_pct, weight / total_weight))
                logger.info(f"Stage {stage_status.value} finished in {time.monotonic() - began:.1f}s")
                done_weight += weight

                if stage_status == JobStatus.PROBING:
                    self._update_job(
                        video_duration=ctx.duration,
                        audio_streams_json=json.dumps([s.model_dump() for s in ctx.audio_streams]),
                    )
                elif stage_status == JobStatus.EXTRACTING_AUDIO:
                    # Sample video frames in the background while the GPU transcribes
                    OnScreenTextStage.start_frame_sampling(ctx)
                    if self.mode == "sync" and settings.sync_detect_burned_in:
                        from app.pipeline.sync_burned import detect_burned_subtitles

                        ctx.burned_task = asyncio.ensure_future(
                            detect_burned_subtitles(ctx.video_path, ctx.cancel_flag, settings.sync_burned_in_fps))
                elif stage_status == JobStatus.TRANSCRIBING:
                    try:
                        from app.core.user_settings import load_settings

                        if load_settings().cost_saver_mode:
                            transcriber = getattr(stage, "_last_transcriber", None) or getattr(stage, "_transcriber", None)
                            unload = getattr(transcriber, "unload_model", None)
                            if callable(unload):
                                unload()
                    except Exception as e:
                        logger.debug(f"Could not unload Whisper model after transcription: {e}")
                    self._release_gpu()
                    if self.mode != "sync":  # syncing still needs the audio for voice detection
                        self._delete_audio()
                    self._update_job(detected_language=ctx.detected_language)
                elif stage_status == JobStatus.SCANNING_ONSCREEN:
                    await self._stop_frame_sampling(ctx)
                elif stage_status == JobStatus.REFINING:
                    self._save_segments(ctx.refined_segments)
                    await self._broadcast({
                        "event": "SEGMENTS_REFINED",
                        "stage": JobStatus.REFINING.value,
                        "progress_percent": round(done_weight / total_weight * 100, 1),
                        "segments_batch": [_segment_payload(self.job_id, s) for s in ctx.refined_segments],
                    })
                elif stage_status == JobStatus.SYNCING:
                    self._save_segments(ctx.refined_segments)
                    self._update_job(
                        target_language=ctx.target_language,
                        sync_report_json=json.dumps(ctx.sync_report or {}, ensure_ascii=False),
                    )
                    self._delete_audio()
                    await self._broadcast({
                        "event": "SEGMENTS_REFINED",
                        "stage": JobStatus.SYNCING.value,
                        "progress_percent": round(done_weight / total_weight * 100, 1),
                        "segments_batch": [_segment_payload(self.job_id, s) for s in ctx.refined_segments],
                    })
                elif stage_status == JobStatus.TRANSLATING:
                    if ctx.glossary is not None:
                        self._update_job(glossary_json=json.dumps(ctx.glossary, ensure_ascii=False))
                    if any(s.get("id") is None for s in ctx.refined_segments):
                        self._save_segments(ctx.refined_segments)
                        await self._broadcast({
                            "event": "SEGMENTS_REFINED",
                            "stage": JobStatus.TRANSLATING.value,
                            "progress_percent": round(done_weight / total_weight * 100, 1),
                            "segments_batch": [_segment_payload(self.job_id, s) for s in ctx.refined_segments],
                        })

            ctx.raise_if_cancelled()
            warnings = [w for w in [*ctx.warnings, ctx.translation_warning] if w]
            fallbacks = ctx.usage.flex_fallbacks() if hasattr(ctx.usage, "flex_fallbacks") else 0
            if fallbacks:
                switched = sorted(getattr(ctx.flex_state, "disabled", ()) or ())
                warnings.append(
                    f"Flex was busy: {fallbacks} Gemini request(s) ran on the Standard tier (full price)."
                    + (f" After repeated failures the rest of the job used Standard for {', '.join(switched)}."
                       if switched else "")
                )
            warning = " ".join(warnings) or None
            self._persist_usage(ctx)
            self._update_job(
                status=JobStatus.COMPLETED,
                current_stage="COMPLETED",
                progress_percent=100.0,
                speed_factor=None,
                eta_seconds=None,
                warning_message=warning,
            )
            await self._broadcast({
                "event": "JOB_COMPLETED",
                "stage": "COMPLETED",
                "progress_percent": 100.0,
                "message": warning or (sync_summary(ctx.sync_report) if ctx.sync_report
                                       else "All pipeline stages completed successfully"),
            })

        except PipelineCancelled:
            await self._handle_cancellation()
        except Exception as exc:
            if self.cancel_flag.is_set():
                await self._handle_cancellation()
            else:
                logger.exception(f"Pipeline failed for job {self.job_id}: {exc}")
                await self._handle_failure(str(exc))
        finally:
            self._release_gpu()
            self._delete_audio()
            await self._stop_frame_sampling(ctx)
            await self._stop_burned_detection(ctx)
            self._persist_usage(ctx)
            self._log_summary(ctx)
            active_pipelines.pop(self.job_id, None)

    # ------------------------------------------------------------------ job log

    def _log_start(self, ctx: PipelineContext) -> None:
        try:
            from app.core.user_settings import load_settings

            s = load_settings()

            def call(c) -> str:
                return f"{c.model} (thinking {c.thinking}{', Flex' if c.flex else ''})"

            run = {"translate": "retranslate", "sync": "sync existing subtitles"}.get(self.mode, "full run")
            logger.info(f"===== Job {ctx.job_id}: {run} =====")
            if self.mode == "sync":
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
            if self.mode == "translate":
                logger.info(f"Retranslate: only failed={self.only_failed}, rebuild glossary={self.rebuild_glossary}, "
                            f"segments={len(ctx.refined_segments)}"
                            + (f", selected rows={len(self.only_indices)}" if self.only_indices else "")
                            + (f", hint: {self.hint}" if self.hint else ""))
        except Exception as e:  # logging must never break the pipeline
            logger.warning(f"Could not log the job settings: {e}")

    def _log_summary(self, ctx: Optional[PipelineContext]) -> None:
        try:
            with Session(engine) as session:
                job = session.get(Job, self.job_id)
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
            logger.info(f"===== Job {self.job_id}: end =====")
        except Exception as e:
            logger.warning(f"Could not log the job summary: {e}")

    def _progress_callback(self, stage_status: JobStatus, base_pct: float, share: float):
        last_db_write = [0.0]

        async def _on_stage_progress(
            pct: float,
            speed: Optional[float],
            eta: Optional[float],
            msg: Optional[str],
            extra: Optional[Any],
        ) -> None:
            if self.cancel_flag.is_set():
                return
            overall_pct = round(base_pct + pct * share, 1)
            payload: Dict[str, Any] = {
                "event": "STAGE_PROGRESS",
                "stage": stage_status.value,
                "progress_percent": overall_pct,
                "speed_factor": speed,
                "eta_seconds": eta,
                "message": msg,
            }
            if isinstance(extra, RawSegment) and self.mode != "sync":
                payload["event"] = "SEGMENT_CREATED"
                payload["segment"] = {
                    "job_id": self.job_id,
                    "index": extra.index,
                    "start_time": extra.start_time,
                    "end_time": extra.end_time,
                    "original_text": extra.text,
                    "translated_text": None,
                    "confidence": extra.confidence,
                }
            elif isinstance(extra, list):
                # Translated batches are broadcast by _persist_translations
                payload["event"] = "STAGE_PROGRESS"
            # Persist progress occasionally so a page reload shows a sensible state
            if overall_pct - last_db_write[0] >= 2:
                last_db_write[0] = overall_pct
                self._update_job(progress_percent=overall_pct, speed_factor=speed, eta_seconds=eta)
                self._persist_usage(self._ctx)
            await self._broadcast(payload)

        return _on_stage_progress

    async def _handle_cancellation(self) -> None:
        if self.mode == "translate":
            # The transcription is intact; only the retranslation was stopped.
            self._update_job(
                status=JobStatus.COMPLETED,
                current_stage="COMPLETED",
                progress_percent=100.0,
                warning_message="Translation cancelled — some segments may be untranslated.",
            )
            await self._broadcast({"event": "JOB_COMPLETED", "stage": "COMPLETED", "progress_percent": 100.0,
                                   "message": "Translation cancelled"})
            return
        self._update_job(status=JobStatus.CANCELLED, current_stage="CANCELLED", speed_factor=None, eta_seconds=None)
        await self._broadcast({
            "event": "JOB_CANCELLED",
            "stage": "CANCELLED",
            "progress_percent": 0.0,
            "message": "Job cancelled by user",
        })

    async def _handle_failure(self, error_message: str) -> None:
        self._update_job(status=JobStatus.FAILED, error_message=error_message, speed_factor=None, eta_seconds=None)
        await self._broadcast({
            "event": "JOB_FAILED",
            "stage": "FAILED",
            "progress_percent": 0.0,
            "message": error_message,
        })
