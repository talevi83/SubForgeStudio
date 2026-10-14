"""API / pipeline behaviour added in the improvement pass."""
import asyncio
import io
import threading
import time
import uuid
from typing import List, Optional

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.core.database import engine
from app.main import app
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord
from app.pipeline.context import PipelineCancelled, PipelineContext, RawSegment
from app.pipeline.stages.transcribe_stage import TranscribeStage
from app.pipeline.stages.translate_stage import TranslateStage
from app.api.routes_media import _parse_range, _display_name
from app.providers.transcription.base import BaseTranscriber
from app.providers.translation.base import BaseTranslator, TranslatableSegment
from app.utils.rtl_fixer import RLM


@pytest.fixture(name="client")
def client_fixture():
    with TestClient(app) as client:
        yield client


def _job_with_segments(texts: List[str], **job_kwargs) -> str:
    job_id = f"test_{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(Job(id=job_id, video_path="x.mkv", file_name="x.mkv", status=JobStatus.COMPLETED, **job_kwargs))
        session.commit()
        for i, t in enumerate(texts, start=1):
            session.add(SubtitleSegmentRecord(job_id=job_id, index=i, start_time=i * 2.0, end_time=i * 2.0 + 1.5,
                                              original_text=t))
        session.commit()
    return job_id


class FakeTranslator(BaseTranslator):
    def __init__(self, fail_indices=()):
        self.fail_indices = set(fail_indices)
        self.calls: List[List[int]] = []

    async def build_glossary(self, segments, source_lang, target_lang, context_notes=""):
        return {"summary": "test", "characters": [], "terms": []}

    async def translate_segments(self, segments, source_lang, target_lang, context_notes="", apply_rtl=False,
                                 on_batch_complete=None, glossary=None, context_segments=None):
        self.calls.append([s.index for s in segments])
        for s in segments:
            if s.index in self.fail_indices:
                s.translated_text, s.translation_failed = None, True
            else:
                s.translated_text, s.translation_failed = f"תרגום {s.index}?", False
        if on_batch_complete:
            await on_batch_complete(segments, 100.0)
        return segments


def _wait_for(client: TestClient, job_id: str, statuses=("COMPLETED", "FAILED", "CANCELLED"), timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {job['status']}")


def test_untrusted_host_is_rejected(client: TestClient):
    res = client.get("/api/system/info", headers={"host": "evil.example.com"})
    assert res.status_code == 400


def test_system_info_lists_models(client: TestClient):
    data = client.get("/api/system/info").json()
    assert "large-v3-turbo" in data["whisper_models"]
    assert settings.gemini_model in data["gemini_models"]


def test_list_jobs_is_slim_but_has_counts(client: TestClient):
    job_id = _job_with_segments(["a", "b", "c"])
    jobs = {j["id"]: j for j in client.get("/api/jobs").json()}
    assert jobs[job_id]["segments"] == []
    assert jobs[job_id]["segment_count"] == 3


def test_retranslate_flags_failures_then_retries_only_failed(client: TestClient, monkeypatch):
    fake = FakeTranslator(fail_indices={2})
    monkeypatch.setattr(TranslateStage, "_make_translator", lambda self, ctx: fake)
    job_id = _job_with_segments(["Hello?", "World.", "Bye."], target_language="Hebrew")

    assert client.post(f"/api/jobs/{job_id}/translate", json={}).status_code == 200
    job = _wait_for(client, job_id)
    segs = {s["index"]: s for s in job["segments"]}
    assert segs[1]["translated_text"] == "תרגום 1?"
    assert RLM not in segs[1]["translated_text"]  # stored clean, RTL marks only at export
    assert segs[2]["translation_failed"] is True and segs[2]["translated_text"] is None
    assert job["failed_translation_count"] == 1
    assert "could not be translated" in job["warning_message"]
    assert job["glossary"]["summary"] == "test"

    fake.fail_indices = set()
    client.post(f"/api/jobs/{job_id}/translate", json={"only_failed": True})
    job = _wait_for(client, job_id)
    assert fake.calls[-1] == [2]
    assert job["failed_translation_count"] == 0
    assert job["warning_message"] is None

    srt = client.get(f"/api/jobs/{job_id}/export/srt?use_translated=true").text
    assert f"{RLM}תרגום 1?{RLM}" in srt


def test_translation_without_key_keeps_transcription(client: TestClient):
    job_id = _job_with_segments(["Hello."])
    client.post(f"/api/jobs/{job_id}/translate", json={})
    job = _wait_for(client, job_id)
    assert job["status"] == "COMPLETED"
    assert "GEMINI_API_KEY" in job["warning_message"]
    assert job["segments"][0]["original_text"] == "Hello."


def test_bulk_update_and_validation(client: TestClient):
    job_id = _job_with_segments(["one", "two"])
    segs = client.get(f"/api/jobs/{job_id}").json()["segments"]
    res = client.patch(f"/api/jobs/{job_id}/segments", json=[
        {"id": segs[0]["id"], "translated_text": f"{RLM}אחד{RLM}"},
        {"id": segs[1]["id"], "original_text": "TWO"},
    ])
    assert res.status_code == 200
    assert res.json()[0]["translated_text"] == "אחד"
    assert res.json()[1]["original_text"] == "TWO"

    bad = client.patch(f"/api/jobs/{job_id}/segments/{segs[0]['id']}", json={"end_time": 0.5})
    assert bad.status_code == 422


def test_range_parsing():
    assert _parse_range("bytes=0-99", 1000) == (0, 99)
    assert _parse_range("bytes=900-", 1000) == (900, 999)
    assert _parse_range("bytes=900-5000", 1000) == (900, 999)  # clamped, not 416
    assert _parse_range("bytes=-100", 1000) == (900, 999)
    assert _parse_range("bytes=1000-", 1000) is None
    assert _parse_range("garbage", 1000) is None


def test_upload_name_and_dedupe(client: TestClient, monkeypatch):
    async def fake_probe(path):
        from app.models.schemas import ProbeResponse
        from pathlib import Path
        p = Path(path)
        return ProbeResponse(file_path=str(p.resolve()), file_name=p.name, file_size=p.stat().st_size,
                             duration=1.0, format_name="matroska")

    monkeypatch.setattr("app.api.routes_media.probe_media_file", fake_probe)
    data = b"x" * 1234
    first = client.post("/api/media/upload", files={"file": ("My Show [1080p].mkv", io.BytesIO(data))}).json()
    assert first["file_name"] == "My Show [1080p].mkv"
    assert first["is_upload"] is True

    found = client.get("/api/media/upload/existing", params={"name": "My Show [1080p].mkv", "size": 1234}).json()
    assert found["file_path"] == first["file_path"]

    second = client.post("/api/media/upload", files={"file": ("My Show [1080p].mkv", io.BytesIO(data))}).json()
    assert second["file_path"] == first["file_path"]  # no duplicate copy kept
    assert _display_name("1a2b3c4d_Movie.mkv") == "Movie.mkv"


def test_delete_job_removes_unused_upload(client: TestClient):
    upload = (settings.storage_dir / "uploads" / "deadbeef_clip.mkv")
    upload.write_bytes(b"123")
    res = client.post("/api/jobs", json={"video_path": str(upload), "enable_translation": False})
    job_id = res.json()["id"]
    assert res.json()["is_upload"] is True
    PipelineCancelledWait = _wait_for(client, job_id)  # probe fails on a fake file -> FAILED
    assert PipelineCancelledWait["status"] in ("FAILED", "CANCELLED", "COMPLETED")
    assert client.delete(f"/api/jobs/{job_id}").json()["upload_deleted"] is True
    assert not upload.exists()


class SlowTranscriber(BaseTranscriber):
    """Emits segments slowly from a worker thread and honours the cancel flag."""

    def __init__(self):
        self.stopped_early = False

    async def transcribe(self, audio_path, language=None, on_segment_callback=None, cancel_flag=None):
        for i in range(1, 200):
            if cancel_flag is not None and cancel_flag.is_set():
                self.stopped_early = True
                raise PipelineCancelled()
            await asyncio.sleep(0.01)
            if on_segment_callback:
                await on_segment_callback(RawSegment(index=i, start_time=i, end_time=i + 0.5, text="hi"), i / 2)
        return []


@pytest.mark.asyncio
async def test_cancel_stops_transcription_midway():
    transcriber = SlowTranscriber()
    ctx = PipelineContext(job_id="t", video_path="x", file_name="x", audio_wav_path="x.wav")

    async def cancel_soon():
        await asyncio.sleep(0.1)
        ctx.cancel_flag.set()

    asyncio.ensure_future(cancel_soon())
    with pytest.raises(PipelineCancelled):
        await TranscribeStage(transcriber).execute(ctx)
    assert transcriber.stopped_early
    assert len(ctx.raw_segments) < 50


def test_migration_adds_missing_columns(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    import app.core.database as db

    old = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE jobs (id VARCHAR PRIMARY KEY, video_path VARCHAR, file_name VARCHAR, gemini_api_key_override VARCHAR)"))
        conn.execute(text("CREATE TABLE subtitle_segments (id INTEGER PRIMARY KEY, job_id VARCHAR)"))
        conn.execute(text("INSERT INTO jobs VALUES ('j', 'x', 'x.mkv', 'secret')"))
        conn.execute(text(
            "INSERT INTO jobs VALUES ('u', 'C:\\Tools\\app\\storage\\uploads\\1a2b3c4d_Show [720p].mkv', '1a2b3c4d_Show [720p].mkv', NULL)"
        ))
    original = db.engine
    db.engine = old
    try:
        db._migrate()
    finally:
        db.engine = original
    cols = {c["name"] for c in inspect(old).get_columns("jobs")}
    assert {"is_upload", "warning_message", "detected_language", "glossary_json"} <= cols
    assert "translation_failed" in {c["name"] for c in inspect(old).get_columns("subtitle_segments")}
    with old.begin() as conn:
        assert conn.execute(text("SELECT gemini_api_key_override FROM jobs WHERE id='j'")).scalar() is None
        row = conn.execute(text("SELECT is_upload, file_name FROM jobs WHERE id='u'")).one()
        assert row == (1, "Show [720p].mkv")
        assert conn.execute(text("SELECT is_upload FROM jobs WHERE id='j'")).scalar() == 0


def test_cleanup_never_deletes_files_placed_by_hand(tmp_path):
    import os
    from app.core.housekeeping import cleanup_storage

    uploads = settings.storage_dir / "uploads"
    old = time.time() - 10 * 24 * 3600
    by_hand = uploads / "The Last Ship S01E01 Phase Six.mkv"
    app_copy = uploads / "0badc0de_abandoned.mkv"
    for f in (by_hand, app_copy):
        f.write_bytes(b"x")
        os.utime(f, (old, old))
    cleanup_storage()
    assert by_hand.exists()          # user's file: untouched, however old
    # The app's own abandoned copy goes only once it is old by both mtime and ctime;
    # ctime can't be faked in a test, so it survives here — the point is the prefix rule.
    by_hand.unlink()
    app_copy.unlink(missing_ok=True)
