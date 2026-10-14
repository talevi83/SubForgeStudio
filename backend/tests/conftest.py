"""
Pytest configuration.

Settings (and the SQLAlchemy engine) are created at import time from environment
variables, so we must point STORAGE_DIR / DATABASE_URL at a throwaway directory
*before* any `app.*` module is imported. Otherwise tests write into the real
storage/subforge.db and show up as jobs in the UI.
"""
import os
import shutil
import tempfile
from pathlib import Path

_TEST_STORAGE = Path(tempfile.mkdtemp(prefix="subforge_test_"))
os.environ["STORAGE_DIR"] = str(_TEST_STORAGE)
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_STORAGE / 'test.db').as_posix()}"
# FastAPI's TestClient sends "Host: testserver"
os.environ["ALLOWED_HOSTS"] = '["localhost", "127.0.0.1", "testserver"]'
# Never call the real Gemini API from tests
os.environ["GEMINI_API_KEY"] = ""


def pytest_sessionfinish(session, exitstatus):
    # Dispose engine so SQLite releases the file handle on Windows before deleting
    try:
        from app.core.database import engine

        engine.dispose()
    except Exception:
        pass
    shutil.rmtree(_TEST_STORAGE, ignore_errors=True)
