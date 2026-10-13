import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord
from app.models.schemas import (
    JobCreate,
    JobResponse,
    SubtitleSegmentSchema,
    SegmentUpdate,
    AudioStreamInfo,
)
from app.pipeline.orchestrator import PipelineOrchestrator
from app.utils.srt_formatter import calculate_cps

router = APIRouter(prefix="/api/jobs", tags=["Jobs"])


def _job_to_response(job: Job) -> JobResponse:
    """Helper to convert Job ORM entity into JobResponse schema."""
    streams: List[AudioStreamInfo] = []
    if job.audio_streams_json:
        try:
            raw_streams = json.loads(job.audio_streams_json)
            streams = [AudioStreamInfo(**s) for s in raw_streams]
        except Exception:
            pass

    segments = [
        SubtitleSegmentSchema(
            id=s.id,
            job_id=s.job_id,
            index=s.index,
            start_time=s.start_time,
            end_time=s.end_time,
            original_text=s.original_text,
            translated_text=s.translated_text,
            confidence=s.confidence,
            cps=s.cps,
        )
        for s in (job.segments or [])
    ]

    return JobResponse(
        id=job.id,
        video_path=job.video_path,
        file_name=job.file_name,
        file_size=job.file_size,
        status=job.status,
        current_stage=job.current_stage,
        progress_percent=job.progress_percent,
        speed_factor=job.speed_factor,
        eta_seconds=job.eta_seconds,
        error_message=job.error_message,
        video_duration=job.video_duration,
        audio_streams=streams,
        selected_audio_stream=job.selected_audio_stream,
        whisper_model=job.whisper_model,
        source_language=job.source_language,
        vad_filter=job.vad_filter,
        enable_translation=job.enable_translation,
        target_language=job.target_language,
        gemini_model=job.gemini_model,
        context_notes=job.context_notes,
        segments=segments,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("", response_model=JobResponse)
async def create_job(req: JobCreate, session: Session = Depends(get_session)):
    """Create a new subtitling job and start the processing pipeline in the background."""
    path = Path(req.video_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Target video file not found: {req.video_path}")

    job_id = f"job_{uuid.uuid4().hex[:10]}"
    file_name = req.file_name or path.name

    new_job = Job(
        id=job_id,
        video_path=str(path.resolve()),
        file_name=file_name,
        file_size=path.stat().st_size if path.exists() else 0,
        status=JobStatus.IDLE,
        selected_audio_stream=req.selected_audio_stream,
        whisper_model=req.whisper_model,
        source_language=req.source_language,
        vad_filter=req.vad_filter,
        enable_translation=req.enable_translation,
        target_language=req.target_language,
        gemini_model=req.gemini_model,
        gemini_api_key_override=req.gemini_api_key_override,
        context_notes=req.context_notes,
    )

    session.add(new_job)
    session.commit()
    session.refresh(new_job)

    # Launch pipeline orchestrator asynchronously in the background
    orchestrator = PipelineOrchestrator(job_id=job_id)
    asyncio.create_task(orchestrator.run())

    return _job_to_response(new_job)


@router.get("", response_model=List[JobResponse])
def list_jobs(session: Session = Depends(get_session)):
    """List all subtitling jobs sorted by creation date descending."""
    statement = select(Job).order_by(Job.created_at.desc())
    jobs = session.exec(statement).all()
    return [_job_to_response(j) for j in jobs]


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, session: Session = Depends(get_session)):
    """Retrieve full job details and all subtitle segments."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _job_to_response(job)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, session: Session = Depends(get_session)):
    """Cancel an actively executing job pipeline."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    cancelled = PipelineOrchestrator.cancel_job(job_id)
    if not cancelled:
        job.status = JobStatus.CANCELLED
        job.updated_at = datetime.now(timezone.utc)
        session.add(job)
        session.commit()

    return {"status": "CANCELLED", "job_id": job_id}


@router.delete("/{job_id}")
def delete_job(job_id: str, session: Session = Depends(get_session)):
    """Delete a job and remove associated cached audio files."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    PipelineOrchestrator.cancel_job(job_id)

    # Delete cached audio if exists
    try:
        from app.core.config import settings
        audio_path = settings.storage_dir / "audio" / f"{job_id}.wav"
        if audio_path.exists():
            audio_path.unlink()
    except Exception:
        pass

    session.delete(job)
    session.commit()
    return {"deleted": True, "job_id": job_id}


@router.patch("/{job_id}/segments/{segment_id}", response_model=SubtitleSegmentSchema)
def update_segment(
    job_id: str,
    segment_id: int,
    patch: SegmentUpdate,
    session: Session = Depends(get_session),
):
    """Update subtitle segment timestamps or text (Original or Translated)."""
    seg = session.get(SubtitleSegmentRecord, segment_id)
    if not seg or seg.job_id != job_id:
        raise HTTPException(status_code=404, detail="Subtitle segment not found")

    if patch.start_time is not None:
        seg.start_time = round(patch.start_time, 3)
    if patch.end_time is not None:
        seg.end_time = round(patch.end_time, 3)
    if patch.original_text is not None:
        seg.original_text = patch.original_text
    if patch.translated_text is not None:
        seg.translated_text = patch.translated_text

    # Recalculate reading speed (CPS)
    duration = max(0.1, seg.end_time - seg.start_time)
    text_for_cps = seg.translated_text if seg.translated_text else seg.original_text
    seg.cps = calculate_cps(text_for_cps, duration)

    session.add(seg)
    session.commit()
    session.refresh(seg)

    return SubtitleSegmentSchema(
        id=seg.id,
        job_id=seg.job_id,
        index=seg.index,
        start_time=seg.start_time,
        end_time=seg.end_time,
        original_text=seg.original_text,
        translated_text=seg.translated_text,
        confidence=seg.confidence,
        cps=seg.cps,
    )
