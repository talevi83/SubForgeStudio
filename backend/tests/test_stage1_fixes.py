"""Regression tests for stage-1 fixes (timestamps, recovery, cancel guard, upload/export safety)."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.core.database import engine
from app.main import app
from app.models.db_models import Job, JobStatus
from app.pipeline.orchestrator import recover_interrupted_jobs
from app.api.routes_media import _sanitize_filename
from app.utils.srt_formatter import format_srt_timestamp, format_vtt_timestamp


@pytest.fixture(name="client")
def client_fixture():
    with TestClient(app) as client:
        yield client


def _insert_job(status: JobStatus, **kwargs) -> str:
    job_id = f"test_{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(Job(id=job_id, video_path="x.mkv", file_name=kwargs.pop("file_name", "x.mkv"),
                        status=status, **kwargs))
        session.commit()
    return job_id


def test_tests_use_isolated_database():
    assert "subforge_test_" in settings.database_url


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (59.9996, "00:01:00,000"),
        (3599.9999, "01:00:00,000"),
        (1.25, "00:00:01,250"),
        (0, "00:00:00,000"),
        (-5, "00:00:00,000"),
        (3725.25, "01:02:05,250"),
    ],
)
def test_timestamp_carry(seconds, expected):
    result = format_srt_timestamp(seconds)
    assert result == expected
    assert format_vtt_timestamp(seconds) == result.replace(",", ".")


def test_recover_interrupted_jobs(client: TestClient):
    stuck = _insert_job(JobStatus.TRANSCRIBING)
    done = _insert_job(JobStatus.COMPLETED)

    assert recover_interrupted_jobs() >= 1

    with Session(engine) as session:
        stuck_job = session.get(Job, stuck)
        assert stuck_job.status == JobStatus.FAILED
        assert "TRANSCRIBING" in stuck_job.error_message
        assert session.get(Job, done).status == JobStatus.COMPLETED


def test_cancel_completed_job_is_rejected(client: TestClient):
    job_id = _insert_job(JobStatus.COMPLETED)
    res = client.post(f"/api/jobs/{job_id}/cancel")
    assert res.status_code == 409
    with Session(engine) as session:
        assert session.get(Job, job_id).status == JobStatus.COMPLETED


def test_cancel_orphaned_active_job(client: TestClient):
    job_id = _insert_job(JobStatus.TRANSLATING)
    res = client.post(f"/api/jobs/{job_id}/cancel")
    assert res.status_code == 200
    with Session(engine) as session:
        assert session.get(Job, job_id).status == JobStatus.CANCELLED


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../evil.bat", "evil.bat"),
        ("..\\..\\Windows\\evil.exe", "evil.exe"),
        ("C:\\Users\\me\\movie.mkv", "movie.mkv"),
        ("bad:name?.mkv", "bad_name_.mkv"),
        ("", "upload.bin"),
        (None, "upload.bin"),
        ("..", "upload.bin"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert _sanitize_filename(raw) == expected


def test_export_filename_uses_language_and_supports_unicode(client: TestClient):
    job_id = _insert_job(JobStatus.COMPLETED, file_name="סרט.mkv", target_language="Russian",
                         source_language="en")
    res = client.get(f"/api/jobs/{job_id}/export/srt?use_translated=true")
    assert res.status_code == 200
    cd = res.headers["content-disposition"]
    assert ".ru.srt" in cd
    assert "filename*=UTF-8''" in cd

    res = client.get(f"/api/jobs/{job_id}/export/vtt?use_translated=false")
    assert ".en.vtt" in res.headers["content-disposition"]
