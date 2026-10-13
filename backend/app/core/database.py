from typing import Generator
from sqlmodel import create_engine, Session, SQLModel
from app.core.config import settings

# SQLite connection configuration
connect_args = {"check_same_thread": False}
engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)


def init_db() -> None:
    """Initialize SQLite database tables."""
    # Ensure tables are registered
    import app.models.db_models  # noqa: F401
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """Provide a transactional database session per request."""
    with Session(engine) as session:
        yield session
