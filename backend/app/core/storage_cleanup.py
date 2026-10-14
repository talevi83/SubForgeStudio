"""Disk usage report and cleanup of the app's own storage folder.

Only ever touches files inside `storage/` that the app created itself. Videos the user
selected by path are never deleted, and nothing belonging to a running job is touched.
"""
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from sqlalchemy import text
from sqlmodel import Session, select

from app.core.config import settings
from app.models.db_models import Job, JobStatus

logger = logging.getLogger(__name__)

# Categories that are plain files; "jobs" (finished jobs) is handled separately
FILE_CATEGORIES = ("temp", "cache", "logs", "exports", "uploads")
ALL_CATEGORIES = FILE_CATEGORIES + ("jobs",)

_APP_UPLOAD = re.compile(r"^[0-9a-f]{8}_")
FINISHED = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


@dataclass
class Item:
    path: Path
    size: int


@dataclass
class Report:
    bytes: int = 0
    count: int = 0
    items: List[Item] = field(default_factory=list)

    def add(self, path: Path, size: int) -> None:
        self.items.append(Item(path, size))
        self.bytes += size
        self.count += 1


def _tree_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _job_id_of(name: str) -> str:
    return name.split(".")[0]


def _children(folder: Path) -> Iterable[Path]:
    return sorted(folder.iterdir()) if folder.is_dir() else []


def collect(session: Session, active_ids: Set[str], older_than_days: Optional[float] = None) -> Dict[str, Report]:
    """What each category currently holds (and would free). Nothing is deleted here."""
    root = settings.storage_dir
    out: Dict[str, Report] = {c: Report() for c in ALL_CATEGORIES}

    # temp: leftover audio and video frames (rebuilt on demand)
    for p in _children(root / "audio"):
        if _job_id_of(p.name) not in active_ids:
            out["temp"].add(p, _tree_size(p))
    for p in _children(root / "frames"):
        if p.name not in active_ids:
            out["temp"].add(p, _tree_size(p))

    # cache: speech-region caches used by "re-check timing" (re-computed when needed)
    for p in _children(root / "regions"):
        if _job_id_of(p.name) not in active_ids:
            out["cache"].add(p, _tree_size(p))

    # logs: per-job logs and rotated server logs (the live server.log is kept)
    for p in _children(root / "logs" / "jobs"):
        if p.is_file() and _job_id_of(p.name) not in active_ids:
            out["logs"].add(p, _tree_size(p))
    for p in _children(root / "logs"):
        if p.is_file() and re.match(r"^server\.log\.\d+$", p.name):
            out["logs"].add(p, _tree_size(p))

    # exports: subtitle files written for download
    for p in _children(root / "exports"):
        if p.is_file():
            out["exports"].add(p, _tree_size(p))

    # uploads: copies made by the app that no job points at any more
    jobs = session.exec(select(Job)).all()
    referenced = {str(Path(j.video_path).resolve()) for j in jobs if j.video_path}
    for p in _children(root / "uploads"):
        if p.is_file() and _APP_UPLOAD.match(p.name) and str(p.resolve()) not in referenced:
            out["uploads"].add(p, _tree_size(p))

    # jobs: finished jobs older than N days (with their uploaded copy, if no other job uses it)
    if older_than_days is not None:
        cutoff = time.time() - older_than_days * 86400
        for j in jobs:
            if j.id in active_ids or j.status not in FINISHED:
                continue
            stamp = (j.updated_at or j.created_at)
            if stamp is None or stamp.timestamp() > cutoff:
                continue
            size = 0
            vp = Path(j.video_path) if j.video_path else None
            if vp is not None and _owns_upload(j) and vp.is_file():
                others = [o for o in jobs if o.id != j.id and o.video_path == j.video_path]
                if not others:
                    size = _tree_size(vp)
            out["jobs"].add(Path(j.id), size)
    return out


def _owns_upload(job: Job) -> bool:
    from app.core.housekeeping import is_in_uploads

    return bool(job.is_upload or is_in_uploads(job.video_path))


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _delete_job(session: Session, job: Job) -> None:
    from app.core.housekeeping import delete_upload_if_unused
    from app.core.job_logging import delete_job_log

    job_id, video_path, owns = job.id, job.video_path, _owns_upload(job)
    (settings.storage_dir / "audio" / f"{job_id}.wav").unlink(missing_ok=True)
    session.delete(job)
    session.commit()
    delete_job_log(job_id)
    (settings.storage_dir / "regions" / f"{job_id}.json").unlink(missing_ok=True)
    shutil.rmtree(settings.storage_dir / "frames" / job_id, ignore_errors=True)
    if owns:
        delete_upload_if_unused(video_path)


def clean(session: Session, categories: Iterable[str], active_ids: Set[str],
          older_than_days: Optional[float] = None) -> Dict[str, Dict[str, int]]:
    """Delete the chosen categories; returns {category: {"bytes": freed, "count": removed}}."""
    wanted = [c for c in categories if c in ALL_CATEGORIES]
    if "jobs" in wanted and older_than_days is None:
        raise ValueError("older_than_days is required to clean jobs")
    report = collect(session, active_ids, older_than_days if "jobs" in wanted else None)
    result: Dict[str, Dict[str, int]] = {}
    # Jobs first, so uploads/logs they leave behind are already gone when sizes are measured
    for cat in sorted(wanted, key=lambda c: c != "jobs"):
        freed = removed = 0
        if cat == "jobs":
            for item in report["jobs"].items:
                job = session.get(Job, item.path.name)
                if job is None:
                    continue
                _delete_job(session, job)
                freed += item.size
                removed += 1
            if removed:
                _vacuum(session)
        else:
            for item in report[cat].items:
                try:
                    _remove(item.path)
                    freed += item.size
                    removed += 1
                except OSError as exc:
                    logger.warning(f"Could not remove {item.path}: {exc}")
        result[cat] = {"bytes": freed, "count": removed}
    return result


def _vacuum(session: Session) -> None:
    """Give the SQLite file's freed pages back to the disk."""
    try:
        session.commit()
        conn = session.connection().execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text("VACUUM"))
    except Exception as exc:  # best effort; the rows are already gone
        logger.info(f"VACUUM skipped: {exc}")


def database_size() -> int:
    for name in ("subforge.db",):
        p = settings.storage_dir / name
        if p.is_file():
            return p.stat().st_size
    return 0
