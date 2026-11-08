"""
Edit history for the studio: undo, redo and "jump back to any step", like Photoshop's History panel.

Every change to a job's subtitles stores a compressed snapshot of *all* its lines as they are after
the change; the first change also stores the state before it ("original"). `Job.history_cursor`
points at the entry that matches the subtitles right now:

* undo / redo / jump restore an entry's snapshot and move the cursor (nothing is deleted, so you can
  go forward again);
* a new edit made after going back discards the entries ahead of the cursor;
* typing in the same line within a short time updates one entry instead of adding one per autosave;
* a change made outside the history (an interrupted translation run, an older app version) is
  noticed by its digest and kept as an "external" entry, so undo never silently loses work.

Snapshots (not diffs) keep this simple and safe across renumbering, moves and re-timing; a
2,500-line film is ~80 KB compressed, and only the newest MAX_ENTRIES are kept per job.
"""
import hashlib
import json
import threading
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sqlmodel import Session, select

from app.models.db_models import Job, JobHistoryEntry, SubtitleSegmentRecord

MAX_ENTRIES = 100
COALESCE_SECONDS = 90.0

SNAPSHOT_FIELDS = (
    "id", "index", "start_time", "end_time", "original_text", "translated_text",
    "translation_failed", "segment_type", "confidence", "cps",
)

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@contextmanager
def job_lock(job_id: str) -> Iterator[None]:
    """Serialise history-changing requests of one job (autosaves can overlap)."""
    with _locks_guard:
        lock = _locks.setdefault(job_id, threading.Lock())
    with lock:
        yield


class HistoryError(Exception):
    """A restore that can't be done (unknown entry, job busy); the message is shown to the user."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)  # SQLite drops the zone


# ----------------------------------------------------------------------------- snapshots

def _rows(session: Session, job_id: str) -> List[SubtitleSegmentRecord]:
    rows = session.exec(select(SubtitleSegmentRecord).where(SubtitleSegmentRecord.job_id == job_id)).all()
    return sorted(rows, key=lambda r: (r.index, r.id or 0))


def _encode(rows: List[SubtitleSegmentRecord]) -> Tuple[bytes, str, int]:
    data = [{f: getattr(r, f) for f in SNAPSHOT_FIELDS} for r in rows]
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return zlib.compress(raw, 6), hashlib.sha1(raw).hexdigest(), len(data)


def _decode(blob: bytes) -> List[Dict[str, Any]]:
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def _entry_ids(session: Session, job_id: str) -> List[int]:
    return list(session.exec(
        select(JobHistoryEntry.id).where(JobHistoryEntry.job_id == job_id).order_by(JobHistoryEntry.id)
    ).all())


def _cursor(job: Job, ids: List[int]) -> Optional[int]:
    if not ids:
        return None
    return job.history_cursor if job.history_cursor in ids else ids[-1]


def _add(session: Session, job: Job, action: str, rows: List[SubtitleSegmentRecord],
         details: Optional[Dict[str, Any]] = None, coalesce_key: Optional[str] = None) -> JobHistoryEntry:
    blob, digest, count = _encode(rows)
    entry = JobHistoryEntry(
        job_id=job.id, action=action, details_json=json.dumps(details, ensure_ascii=False) if details else None,
        coalesce_key=coalesce_key, segment_count=count, digest=digest, snapshot=blob, created_at=_now(),
    )
    session.add(entry)
    session.flush()
    job.history_cursor = entry.id
    session.add(job)
    return entry


def _drop_after(session: Session, ids: List[int], cursor: Optional[int]) -> None:
    if cursor is None:
        return
    for entry_id in ids[ids.index(cursor) + 1:]:
        session.delete(session.get(JobHistoryEntry, entry_id))
    session.flush()


def _sync_with_current(session: Session, job: Job) -> None:
    """Make sure the entry at the cursor matches the subtitles now (adding "original"/"external")."""
    ids = _entry_ids(session, job.id)
    rows = _rows(session, job.id)
    if not ids:
        _add(session, job, "original", rows)
        return
    cursor = _cursor(job, ids)
    current = session.get(JobHistoryEntry, cursor)
    _, digest, _ = _encode(rows)
    if current.digest != digest:
        _drop_after(session, ids, cursor)
        _add(session, job, "external", rows)
    elif job.history_cursor != cursor:
        job.history_cursor = cursor
        session.add(job)


# ----------------------------------------------------------------------------- recording edits

def begin(session: Session, job: Job) -> None:
    """Call before changing a job's subtitles, in the same session (rolled back with it on error).

    Stores the "before" state if nothing was recorded yet, and drops the steps ahead of the cursor:
    an edit made after an undo starts a new branch, as in Photoshop.
    """
    _sync_with_current(session, job)
    _drop_after(session, _entry_ids(session, job.id), job.history_cursor)


def record(session: Session, job: Job, action: str, details: Optional[Dict[str, Any]] = None,
           coalesce_key: Optional[str] = None) -> Optional[JobHistoryEntry]:
    """Call after the change was committed: store the new state as the current step and commit.

    Nothing is added when the subtitles did not actually change. With `coalesce_key`, a change that
    follows the same kind of change within COALESCE_SECONDS updates that step instead.
    """
    rows = _rows(session, job.id)
    blob, digest, count = _encode(rows)
    current = session.get(JobHistoryEntry, job.history_cursor) if job.history_cursor else None
    if current is not None and current.digest == digest:
        return None
    if (
        current is not None and coalesce_key and current.coalesce_key == coalesce_key
        and (_now() - _as_utc(current.created_at)).total_seconds() <= COALESCE_SECONDS
        and current.id == (_entry_ids(session, job.id) or [None])[-1]
    ):
        current.snapshot, current.digest, current.segment_count, current.created_at = blob, digest, count, _now()
        session.add(current)
        session.commit()
        return current
    entry = _add(session, job, action, rows, details, coalesce_key)
    ids = _entry_ids(session, job.id)
    for old_id in ids[: max(0, len(ids) - MAX_ENTRIES)]:
        session.delete(session.get(JobHistoryEntry, old_id))
    session.commit()
    return entry


def record_job(job_id: str, engine, action: str, details: Optional[Dict[str, Any]] = None) -> None:
    """Record a step for a change made outside a request (the background translation run)."""
    with job_lock(job_id), Session(engine) as session:
        job = session.get(Job, job_id)
        if job is not None and _entry_ids(session, job_id):
            record(session, job, action, details)


# ----------------------------------------------------------------------------- listing & restoring

def listing(session: Session, job: Job) -> Dict[str, Any]:
    rows = session.exec(
        select(JobHistoryEntry.id, JobHistoryEntry.action, JobHistoryEntry.details_json,
               JobHistoryEntry.created_at, JobHistoryEntry.segment_count)
        .where(JobHistoryEntry.job_id == job.id).order_by(JobHistoryEntry.id)
    ).all()
    ids = [r[0] for r in rows]
    cursor = _cursor(job, ids)
    position = ids.index(cursor) if cursor is not None else -1
    return {
        "cursor": cursor,
        "can_undo": position > 0,
        "can_redo": 0 <= position < len(ids) - 1,
        "entries": [
            {
                "id": r[0], "action": r[1], "details": json.loads(r[2]) if r[2] else {},
                "created_at": _as_utc(r[3]), "segment_count": r[4],
            }
            for r in rows
        ],
    }


def _apply(session: Session, job: Job, entry: JobHistoryEntry) -> None:
    for row in _rows(session, job.id):
        session.delete(row)
    session.flush()
    snapshot = _decode(entry.snapshot)
    # An id freed by an undo may have been reused by another job meanwhile: those rows get a new id
    wanted = [s["id"] for s in snapshot if s.get("id") is not None]
    taken = set(session.exec(select(SubtitleSegmentRecord.id).where(SubtitleSegmentRecord.id.in_(wanted))).all()) if wanted else set()
    for data in snapshot:
        fields = {k: v for k, v in data.items() if k in SNAPSHOT_FIELDS}
        if fields.get("id") in taken:
            fields.pop("id")
        session.add(SubtitleSegmentRecord(job_id=job.id, **fields))
    job.history_cursor = entry.id
    job.updated_at = _now()
    session.add(job)


def restore(session: Session, job: Job, entry_id: int) -> None:
    """Bring the subtitles back to the state of `entry_id` and point the cursor at it (commits)."""
    _sync_with_current(session, job)  # keep unrecorded outside changes as a step first
    entry = session.get(JobHistoryEntry, entry_id)
    if entry is None or entry.job_id != job.id:
        raise HistoryError("That history step no longer exists")
    if entry.id != job.history_cursor:
        _apply(session, job, entry)
    session.commit()


def step(session: Session, job: Job, offset: int) -> bool:
    """Undo (offset -1) or redo (+1); False when there is nothing in that direction (commits)."""
    _sync_with_current(session, job)
    ids = _entry_ids(session, job.id)
    position = ids.index(job.history_cursor) + offset if job.history_cursor in ids else -1
    if not 0 <= position < len(ids):
        session.commit()
        return False
    _apply(session, job, session.get(JobHistoryEntry, ids[position]))
    session.commit()
    return True
