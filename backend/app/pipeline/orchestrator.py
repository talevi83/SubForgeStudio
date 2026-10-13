import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from sqlmodel import Session, select

from app.core.database import engine
from app.core.ws_manager import ws_manager
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord
from app.pipeline.context import PipelineContext, RawSegment
from app.pipeline.stages.probe_stage import ProbeStage
from app.pipeline.stages.audio_stage import AudioExtractionStage
from app.pipeline.stages.transcribe_stage import TranscribeStage
from app.pipeline.stages.refine_stage import RefineStage
from app.pipeline.stages.translate_stage import TranslateStage
from app.providers.translation.base import TranslatableSegment

logger = logging.getLogger(__name__)

# Active job cancellation signals
active_cancellations: Dict[str, asyncio.Event] = {}


class PipelineOrchestrator:
    """Executes all subtitling pipeline stages sequentially and coordinates state & WebSockets."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.cancel_event = asyncio.Event()
        active_cancellations[job_id] = self.cancel_event

    @classmethod
    def cancel_job(cls, job_id: str) -> bool:
        """Signal cancellation to an active job."""
        if job_id in active_cancellations:
            active_cancellations[job_id].set()
            return True
        return False

    async def run(self) -> None:
        """Run the full pipeline lifecycle for the job."""
        # 1. Fetch Job from database
        with Session(engine) as session:
            job = session.get(Job, self.job_id)
            if not job:
                logger.error(f"Job {self.job_id} not found in database.")
                return

            ctx = PipelineContext(
                job_id=job.id,
                video_path=job.video_path,
                file_name=job.file_name,
                selected_audio_stream=job.selected_audio_stream,
                whisper_model=job.whisper_model,
                source_language=job.source_language,
                vad_filter=job.vad_filter,
                enable_translation=job.enable_translation,
                target_language=job.target_language,
                gemini_model=job.gemini_model,
                gemini_api_key_override=job.gemini_api_key_override,
                context_notes=job.context_notes,
            )

        stages = [
            (JobStatus.PROBING, ProbeStage()),
            (JobStatus.EXTRACTING_AUDIO, AudioExtractionStage()),
            (JobStatus.TRANSCRIBING, TranscribeStage()),
            (JobStatus.REFINING, RefineStage()),
            (JobStatus.TRANSLATING, TranslateStage()),
        ]

        total_stages = len(stages)

        try:
            for stage_idx, (stage_status, stage_instance) in enumerate(stages):
                if self.cancel_event.is_set():
                    await self._handle_cancellation()
                    return

                # Update job status in DB
                self._update_job_status(stage_status, stage_instance.name, stage_idx / total_stages * 100)

                async def _on_stage_progress(
                    pct: float,
                    speed: Optional[float],
                    eta: Optional[float],
                    msg: Optional[str],
                    extra: Optional[Any],
                ) -> None:
                    if self.cancel_event.is_set():
                        return

                    # Compute combined pipeline progress percent
                    stage_weight = 100.0 / total_stages
                    overall_pct = round((stage_idx * stage_weight) + (pct * stage_weight / 100.0), 1)

                    ws_payload: Dict[str, Any] = {
                        "event": "STAGE_PROGRESS",
                        "job_id": self.job_id,
                        "stage": stage_status.value,
                        "progress_percent": overall_pct,
                        "speed_factor": speed,
                        "eta_seconds": eta,
                        "message": msg,
                    }

                    # If extra is a newly created RawSegment from transcription
                    if isinstance(extra, RawSegment):
                        ws_payload["event"] = "SEGMENT_CREATED"
                        ws_payload["segment"] = {
                            "job_id": self.job_id,
                            "index": extra.index,
                            "start_time": extra.start_time,
                            "end_time": extra.end_time,
                            "original_text": extra.text,
                            "translated_text": None,
                            "confidence": extra.confidence,
                        }
                    # If extra is a translated batch
                    elif isinstance(extra, list) and extra and isinstance(extra[0], TranslatableSegment):
                        ws_payload["event"] = "BATCH_TRANSLATED"
                        ws_payload["segments_batch"] = [
                            {
                                "job_id": self.job_id,
                                "index": item.index,
                                "start_time": item.start_time,
                                "end_time": item.end_time,
                                "original_text": item.original_text,
                                "translated_text": item.translated_text,
                            }
                            for item in extra
                        ]

                    await ws_manager.broadcast(self.job_id, ws_payload)

                # Execute stage
                await stage_instance.execute(ctx, on_progress=_on_stage_progress)

                # If probe stage just finished, persist stream info & duration
                if stage_status == JobStatus.PROBING:
                    with Session(engine) as session:
                        db_job = session.get(Job, self.job_id)
                        if db_job:
                            db_job.video_duration = ctx.duration
                            db_job.audio_streams_json = json.dumps(
                                [s.model_dump() for s in ctx.audio_streams]
                            )
                            session.add(db_job)
                            session.commit()

            # All stages completed successfully! Persist segments and mark complete
            if self.cancel_event.is_set():
                await self._handle_cancellation()
                return

            self._save_final_segments(ctx.refined_segments)

            # Broadcast completion
            await ws_manager.broadcast(
                self.job_id,
                {
                    "event": "JOB_COMPLETED",
                    "job_id": self.job_id,
                    "stage": "COMPLETED",
                    "progress_percent": 100.0,
                    "message": "All pipeline stages completed successfully",
                },
            )

        except Exception as exc:
            logger.exception(f"Pipeline failed for job {self.job_id}: {exc}")
            self._handle_failure(str(exc))
            await ws_manager.broadcast(
                self.job_id,
                {
                    "event": "JOB_FAILED",
                    "job_id": self.job_id,
                    "stage": "FAILED",
                    "progress_percent": 0.0,
                    "message": str(exc),
                },
            )
        finally:
            active_cancellations.pop(self.job_id, None)

    def _update_job_status(self, status: JobStatus, current_stage: str, progress: float) -> None:
        """Update job stage and progress in database."""
        with Session(engine) as session:
            job = session.get(Job, self.job_id)
            if job:
                job.status = status
                job.current_stage = current_stage
                job.progress_percent = progress
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()

    def _save_final_segments(self, refined_segments: list) -> None:
        """Persist refined/translated subtitle segments to the database."""
        with Session(engine) as session:
            job = session.get(Job, self.job_id)
            if not job:
                return

            # Clear any previously existing segments for this job
            statement = select(SubtitleSegmentRecord).where(SubtitleSegmentRecord.job_id == self.job_id)
            existing = session.exec(statement).all()
            for seg in existing:
                session.delete(seg)

            for seg_dict in refined_segments:
                seg_rec = SubtitleSegmentRecord(
                    job_id=self.job_id,
                    index=seg_dict["index"],
                    start_time=seg_dict["start_time"],
                    end_time=seg_dict["end_time"],
                    original_text=seg_dict["original_text"],
                    translated_text=seg_dict.get("translated_text"),
                    confidence=seg_dict.get("confidence", 1.0),
                    cps=seg_dict.get("cps", 0.0),
                )
                session.add(seg_rec)

            job.status = JobStatus.COMPLETED
            job.current_stage = "COMPLETED"
            job.progress_percent = 100.0
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()

    async def _handle_cancellation(self) -> None:
        """Handle user cancellation."""
        with Session(engine) as session:
            job = session.get(Job, self.job_id)
            if job:
                job.status = JobStatus.CANCELLED
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()

        await ws_manager.broadcast(
            self.job_id,
            {
                "event": "JOB_FAILED",
                "job_id": self.job_id,
                "stage": "CANCELLED",
                "progress_percent": 0.0,
                "message": "Job cancelled by user",
            },
        )

    def _handle_failure(self, error_message: str) -> None:
        """Handle pipeline failure."""
        with Session(engine) as session:
            job = session.get(Job, self.job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error_message = error_message
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()
