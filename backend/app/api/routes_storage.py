from typing import Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core import storage_cleanup as sc
from app.core.database import get_session
from app.models.db_models import Job
from app.pipeline.orchestrator import ACTIVE_STATUSES, PipelineOrchestrator

router = APIRouter(prefix="/api/storage", tags=["Storage"])


class StorageCategory(BaseModel):
    id: str
    bytes: int
    count: int


class StorageReport(BaseModel):
    categories: List[StorageCategory]
    database_bytes: int


class CleanRequest(BaseModel):
    categories: List[str] = Field(min_length=1)
    older_than_days: Optional[float] = Field(default=None, ge=0, description="Only for 'jobs'")


class CleanResponse(BaseModel):
    freed_bytes: int
    removed: Dict[str, Dict[str, int]]


def _active_ids(session: Session) -> Set[str]:
    jobs = session.exec(select(Job)).all()
    return {j.id for j in jobs if j.status in ACTIVE_STATUSES or PipelineOrchestrator.is_running(j.id)}


@router.get("", response_model=StorageReport)
def storage_report(older_than_days: float = 30, session: Session = Depends(get_session)):
    """What the storage folder holds, per cleanable category (nothing is deleted)."""
    report = sc.collect(session, _active_ids(session), older_than_days)
    return StorageReport(
        categories=[StorageCategory(id=c, bytes=report[c].bytes, count=report[c].count) for c in sc.ALL_CATEGORIES],
        database_bytes=sc.database_size(),
    )


@router.post("/clean", response_model=CleanResponse)
def storage_clean(req: CleanRequest, session: Session = Depends(get_session)):
    unknown = [c for c in req.categories if c not in sc.ALL_CATEGORIES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown category: {', '.join(unknown)}")
    try:
        removed = sc.clean(session, req.categories, _active_ids(session), req.older_than_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return CleanResponse(freed_bytes=sum(v["bytes"] for v in removed.values()), removed=removed)
