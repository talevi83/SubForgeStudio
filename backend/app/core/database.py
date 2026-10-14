import logging
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Generator
from sqlalchemy import inspect, text
from sqlmodel import create_engine, Session, SQLModel
from app.core.config import settings

logger = logging.getLogger(__name__)

# SQLite connection configuration
connect_args = {"check_same_thread": False}
engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)

# Columns added after the first release. SQLModel.create_all() never alters existing
# tables, so older databases get these through a tiny additive migration.
_ADDED_COLUMNS = {
    "jobs": {
        "is_upload": "BOOLEAN NOT NULL DEFAULT 0",
        "warning_message": "VARCHAR",
        "detected_language": "VARCHAR",
        "glossary_json": "VARCHAR",
        "comms_boost": "BOOLEAN NOT NULL DEFAULT 0",
        "detect_onscreen_text": "BOOLEAN NOT NULL DEFAULT 0",
        "usage_json": "VARCHAR",
        "glossary_profile": "VARCHAR",
    },
    "subtitle_segments": {
        "translation_failed": "BOOLEAN NOT NULL DEFAULT 0",
        "segment_type": "VARCHAR NOT NULL DEFAULT 'DIALOGUE'",
    },
}


_UPLOAD_PREFIX = re.compile(r"^[0-9a-f]{8}_")


def _backfill_uploads(conn) -> None:
    """Jobs created before is_upload existed: mark browser uploads and drop the random
    "1a2b3c4d_" prefix from their display name (it also leaked into exported file names)."""
    for job_id, video_path, file_name in conn.execute(text("SELECT id, video_path, file_name FROM jobs")).fetchall():
        if not video_path:
            continue
        p = PureWindowsPath(video_path) if "\\" in video_path else PurePosixPath(video_path)
        # Only a display flag: deleting files always re-checks the real location first.
        in_uploads = p.parent.name.lower() == "uploads" and _UPLOAD_PREFIX.match(p.name) is not None
        if in_uploads:
            conn.execute(
                text("UPDATE jobs SET is_upload = 1, file_name = :name WHERE id = :id"),
                {"name": _UPLOAD_PREFIX.sub("", file_name or "", count=1) or file_name, "id": job_id},
            )


def _migrate() -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    logger.info(f"Migrating database: adding {table}.{name}")
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    if (table, name) == ("jobs", "is_upload"):
                        try:
                            _backfill_uploads(conn)
                        except Exception as e:  # cosmetic; never block startup
                            logger.warning(f"Upload backfill skipped: {e}")
        # API keys used to be stored in plain text; scrub any that are left over.
        if inspector.has_table("jobs"):
            conn.execute(text("UPDATE jobs SET gemini_api_key_override = NULL WHERE gemini_api_key_override IS NOT NULL"))


def init_db() -> None:
    """Initialize SQLite database tables and apply additive migrations."""
    import app.models.db_models  # noqa: F401  (register tables)
    SQLModel.metadata.create_all(engine)
    _migrate()


def get_session() -> Generator[Session, None, None]:
    """Provide a transactional database session per request."""
    with Session(engine) as session:
        yield session
