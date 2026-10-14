"""
Detailed logs for troubleshooting.

  * storage/logs/server.log         - everything the backend logs (rotating, 3 x 5 MB)
  * storage/logs/jobs/<job_id>.log  - every log line emitted while that job's pipeline ran,
                                      including Gemini/HTTP errors, retries and Flex fallbacks

A job's pipeline runs as its own asyncio task; `job_context(job_id)` marks that task (and the
worker threads it starts with asyncio.to_thread, which copy the context) so the JobLogHandler
can route each record to the right file.
"""
import contextvars
import logging
import logging.handlers
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from app.core.config import settings

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_current_job: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("subforge_job_id", default=None)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def logs_dir() -> Path:
    path = settings.storage_dir / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def server_log_path() -> Path:
    return logs_dir() / "server.log"


def job_log_path(job_id: str) -> Optional[Path]:
    if not _SAFE_ID.match(job_id or ""):
        return None
    folder = logs_dir() / "jobs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{job_id}.log"


@contextmanager
def job_context(job_id: str) -> Iterator[None]:
    """Route log records emitted inside this block (same task / its to_thread workers) to the job's log."""
    token = _current_job.set(job_id)
    try:
        yield
    finally:
        _current_job.reset(token)


def current_job_id() -> Optional[str]:
    return _current_job.get()


class JobLogHandler(logging.Handler):
    """Appends records emitted under job_context() to storage/logs/jobs/<job_id>.log."""

    def emit(self, record: logging.LogRecord) -> None:
        job_id = _current_job.get()
        if not job_id:
            return
        path = job_log_path(job_id)
        if path is None:
            return
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)


def setup_logging(level: int = logging.INFO) -> None:
    """Console + rotating server log + per-job logs. Safe to call more than once."""
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
    if not any(getattr(h, "_subforge", False) for h in root.handlers):
        server = logging.handlers.RotatingFileHandler(
            server_log_path(), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8", delay=True)
        server.setFormatter(formatter)
        server._subforge = True  # type: ignore[attr-defined]
        jobs = JobLogHandler()
        jobs.setFormatter(formatter)
        jobs._subforge = True  # type: ignore[attr-defined]
        root.addHandler(server)
        root.addHandler(jobs)


def delete_job_log(job_id: str) -> None:
    path = job_log_path(job_id)
    if path is not None:
        path.unlink(missing_ok=True)
