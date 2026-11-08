"""POST /api/jobs/{id}/export/save-next-to-video writes the subtitles into the video's folder."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.database import engine
from app.main import app
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_job(video_path: str, *, translated: bool = True, is_upload: bool = False) -> str:
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    with Session(engine) as session:
        session.add(Job(id=job_id, video_path=video_path, file_name="Show.S01E02.mkv", status=JobStatus.COMPLETED,
                        target_language="Hebrew", source_language="en", is_upload=is_upload))
        session.add(SubtitleSegmentRecord(job_id=job_id, index=1, start_time=1.0, end_time=3.0,
                                          original_text="Hello friend?",
                                          translated_text="שלום חבר?" if translated else None))
        session.commit()
    return job_id


def _url(job_id: str) -> str:
    return f"/api/jobs/{job_id}/export/save-next-to-video"


def test_saves_translated_srt_next_to_the_video(client, tmp_path):
    video = tmp_path / "Show.S01E02.mkv"
    video.write_bytes(b"x")
    resp = client.post(_url(_make_job(str(video))), json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    target = tmp_path / "Show.S01E02.he.srt"
    assert body == {"saved": True, "path": str(target), "exists": False}
    data = target.read_bytes()
    assert data.startswith("﻿".encode("utf-8"))           # BOM, same as the download
    text = data.decode("utf-8")
    assert "שלום חבר" in text and "‏" in text                # RTL marks applied by default
    assert not list(tmp_path.glob(".*subforge-tmp"))              # temp file cleaned up


def test_existing_file_is_kept_unless_overwrite(client, tmp_path):
    video = tmp_path / "Show.S01E02.mkv"
    video.write_bytes(b"x")
    target = tmp_path / "Show.S01E02.he.srt"
    target.write_text("mine", encoding="utf-8")
    job_id = _make_job(str(video))

    resp = client.post(_url(job_id), json={})
    assert resp.json() == {"saved": False, "path": str(target), "exists": True}
    assert target.read_text(encoding="utf-8") == "mine"

    resp = client.post(_url(job_id), json={"overwrite": True})
    assert resp.json() == {"saved": True, "path": str(target), "exists": True}
    assert "שלום חבר" in target.read_text(encoding="utf-8-sig")


def test_original_vtt_without_rtl(client, tmp_path):
    video = tmp_path / "Show.S01E02.mkv"
    video.write_bytes(b"x")
    resp = client.post(_url(_make_job(str(video), translated=False)),
                       json={"format": "vtt", "use_translated": False, "apply_rtl": False})
    assert resp.status_code == 200, resp.text
    target = tmp_path / "Show.S01E02.en.vtt"
    assert resp.json()["path"] == str(target)
    assert target.read_text(encoding="utf-8").startswith("WEBVTT")


def test_uploaded_video_is_refused(client, tmp_path):
    resp = client.post(_url(_make_job(str(tmp_path / "Show.S01E02.mkv"), is_upload=True)), json={})
    assert resp.status_code == 400
    assert not list(tmp_path.iterdir())


def test_no_translation_yet_is_refused(client, tmp_path):
    video = tmp_path / "Show.S01E02.mkv"
    video.write_bytes(b"x")
    resp = client.post(_url(_make_job(str(video), translated=False)), json={})
    assert resp.status_code == 400


def test_missing_folder_and_job(client, tmp_path):
    resp = client.post(_url(_make_job(str(tmp_path / "gone" / "Show.S01E02.mkv"))), json={})
    assert resp.status_code == 404
    assert client.post(_url("job_missing"), json={}).status_code == 404


def test_cross_site_request_is_blocked(client, tmp_path):
    video = tmp_path / "Show.S01E02.mkv"
    video.write_bytes(b"x")
    resp = client.post(_url(_make_job(str(video))), json={}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert not (tmp_path / "Show.S01E02.he.srt").exists()
