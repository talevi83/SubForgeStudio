import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from app.main import app
from app.core.database import get_session, engine
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord
from app.providers.translation.noop import NoopTranslator
from app.providers.translation.base import TranslatableSegment
from app.pipeline.context import PipelineContext, RawSegment
from app.pipeline.stages.refine_stage import RefineStage


@pytest.fixture(name="client")
def client_fixture():
    with TestClient(app) as client:
        yield client


def test_system_info(client: TestClient):
    response = client.get("/api/system/info")
    assert response.status_code == 200
    data = response.json()
    assert "hardware" in data
    assert "is_gemini_configured" in data
    assert "whisper_default_model" in data


@pytest.mark.asyncio
async def test_noop_translator():
    translator = NoopTranslator()
    segments = [
        TranslatableSegment(
            index=1,
            start_time=1.0,
            end_time=3.0,
            original_text="Hello world",
        )
    ]
    res = await translator.translate_segments(segments, "en", "he")
    assert len(res) == 1
    assert res[0].original_text == "Hello world"


@pytest.mark.asyncio
async def test_refine_stage_execution():
    stage = RefineStage()
    ctx = PipelineContext(
        job_id="test_job",
        video_path="test.mp4",
        file_name="test.mp4",
        raw_segments=[
            RawSegment(index=1, start_time=0.0, end_time=2.0, text="First subtitle block"),
            RawSegment(index=2, start_time=2.1, end_time=4.0, text="Second subtitle block"),
        ],
    )
    await stage.execute(ctx)
    assert len(ctx.refined_segments) == 2
    assert ctx.refined_segments[0]["index"] == 1
    assert ctx.refined_segments[1]["index"] == 2


import uuid


def test_export_endpoints(client: TestClient):
    # Insert test job and segments into db
    job_id = f"test_export_{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = Job(
            id=job_id,
            video_path="test.mp4",
            file_name="test_export.mp4",
            status=JobStatus.COMPLETED,
        )
        session.add(job)
        session.commit()

        seg1 = SubtitleSegmentRecord(
            job_id=job_id,
            index=1,
            start_time=1.0,
            end_time=3.5,
            original_text="Hello friend",
            translated_text="שלום חבר",
        )
        session.add(seg1)
        session.commit()

    # Test SRT export
    res_srt = client.get(f"/api/jobs/{job_id}/export/srt?use_translated=true")
    assert res_srt.status_code == 200
    assert "00:00:01,000 --> 00:00:03,500" in res_srt.text
    assert "שלום חבר" in res_srt.text

    # Test VTT export
    res_vtt = client.get(f"/api/jobs/{job_id}/export/vtt?use_translated=false")
    assert res_vtt.status_code == 200
    assert "WEBVTT" in res_vtt.text
    assert "Hello friend" in res_vtt.text
