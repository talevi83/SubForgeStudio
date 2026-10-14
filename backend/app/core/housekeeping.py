"""Startup cleanup of temporary audio and abandoned browser uploads."""
import logging
import re
import time
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import engine
from app.models.db_models import Job

logger = logging.getLogger(__name__)

# Files the app itself saved from browser uploads look like "1a2b3c4d_Movie.mkv"
_APP_UPLOAD = re.compile(r"^[0-9a-f]{8}_")


def uploads_dir() -> Path:
    return (settings.storage_dir / "uploads").resolve()


def is_in_uploads(path: str) -> bool:
    try:
        return Path(path).resolve().parent == uploads_dir()
    except OSError:
        return False


def delete_upload_if_unused(path: str, exclude_job_id: Optional[str] = None) -> bool:
    """Delete an uploaded copy once no (other) job references it."""
    if not is_in_uploads(path):
        return False
    resolved = str(Path(path).resolve())
    with Session(engine) as session:
        stmt = select(Job).where(Job.video_path == resolved)
        if exclude_job_id:
            stmt = stmt.where(Job.id != exclude_job_id)
        if session.exec(stmt).first():
            return False
    try:
        Path(resolved).unlink(missing_ok=True)
        logger.info(f"Deleted uploaded file {resolved}")
        return True
    except OSError as e:
        logger.warning(f"Could not delete upload {resolved}: {e}")
        return False


def cleanup_storage() -> None:
    """Remove leftover WAVs (only needed during transcription) and stale unreferenced uploads."""
    audio_dir = settings.storage_dir / "audio"
    removed_audio = 0
    for wav in audio_dir.glob("*.wav"):
        try:
            wav.unlink()
            removed_audio += 1
        except OSError:
            pass
    if removed_audio:
        logger.info(f"Removed {removed_audio} leftover audio file(s)")

    # Temporary video frames from on-screen text scans
    frames_root = settings.storage_dir / "frames"
    if frames_root.is_dir():
        import shutil

        for d in frames_root.iterdir():
            shutil.rmtree(d, ignore_errors=True)

    with Session(engine) as session:
        referenced = {str(Path(p).resolve()) for p in session.exec(select(Job.video_path)).all()}
    cutoff = time.time() - settings.upload_retention_hours * 3600
    for f in uploads_dir().glob("*"):
        # Only ever remove copies the app made itself — never files a user put here by hand
        if not f.is_file() or not _APP_UPLOAD.match(f.name) or str(f.resolve()) in referenced:
            continue
        try:
            st = f.stat()
            # A copied file keeps its old mtime; on Windows st_ctime is when it arrived here
            if max(st.st_mtime, st.st_ctime) < cutoff:
                f.unlink()
                logger.info(f"Removed abandoned upload {f.name}")
        except OSError:
            pass
