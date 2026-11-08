"""Edit history of a project: list the steps, undo, redo, or jump to any step (see core/edit_history.py)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.routes_jobs import _get_job_or_404, _ordered_rows, _segment_to_schema
from app.core import edit_history as history
from app.core.database import get_session
from app.models.db_models import Job
from app.models.schemas import HistoryResponse, HistoryRestoreResponse
from app.pipeline.orchestrator import ACTIVE_STATUSES, PipelineOrchestrator

router = APIRouter(prefix="/api/jobs", tags=["History"])


def _require_idle(job: Job) -> None:
    if PipelineOrchestrator.is_running(job.id) or job.status in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="The project is still running; undo when it has finished")


def _result(session: Session, job: Job) -> HistoryRestoreResponse:
    session.refresh(job)
    return HistoryRestoreResponse(
        segments=[_segment_to_schema(r) for r in _ordered_rows(session, job.id)],
        history=HistoryResponse(**history.listing(session, job)),
    )


@router.get("/{job_id}/history", response_model=HistoryResponse)
def get_history(job_id: str, session: Session = Depends(get_session)):
    """The steps, oldest first, and which one the subtitles are at now (snapshots are not sent)."""
    job = _get_job_or_404(session, job_id)
    return HistoryResponse(**history.listing(session, job))


@router.post("/{job_id}/history/undo", response_model=HistoryRestoreResponse)
def undo(job_id: str, session: Session = Depends(get_session)):
    job = _get_job_or_404(session, job_id)
    _require_idle(job)
    with history.job_lock(job_id):
        history.step(session, job, -1)
    return _result(session, job)


@router.post("/{job_id}/history/redo", response_model=HistoryRestoreResponse)
def redo(job_id: str, session: Session = Depends(get_session)):
    job = _get_job_or_404(session, job_id)
    _require_idle(job)
    with history.job_lock(job_id):
        history.step(session, job, +1)
    return _result(session, job)


@router.post("/{job_id}/history/{entry_id}/restore", response_model=HistoryRestoreResponse)
def restore(job_id: str, entry_id: int, session: Session = Depends(get_session)):
    """Jump to any step. Later steps stay available until a new edit is made."""
    job = _get_job_or_404(session, job_id)
    _require_idle(job)
    with history.job_lock(job_id):
        try:
            history.restore(session, job, entry_id)
        except history.HistoryError as e:
            raise HTTPException(status_code=404, detail=str(e))
    return _result(session, job)
