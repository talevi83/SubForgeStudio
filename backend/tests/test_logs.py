"""Detailed logs: per-job log captured from the pipeline task (and its worker threads), exports."""
import asyncio
import logging
import shutil
import subprocess
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import app.pipeline.orchestrator as orch
from app.core import job_logging
from app.main import app
from app.pipeline.context import RawSegment, TranscribedWord
from app.pipeline.stages.transcribe_stage import TranscribeStage
from app.providers.transcription.base import BaseTranscriber

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def test_job_context_routes_records_including_worker_threads(tmp_path):
    job_logging.setup_logging()
    log = logging.getLogger("test.joblog")

    async def work():
        with job_logging.job_context("job_test123"):
            log.info("in the task")
            await asyncio.to_thread(log.warning, "in a worker thread")
        log.info("outside any job")

    asyncio.run(work())
    text = job_logging.job_log_path("job_test123").read_text(encoding="utf-8")
    assert "in the task" in text and "in a worker thread" in text
    assert "outside any job" not in text
    assert job_logging.job_log_path("../evil") is None
    job_logging.delete_job_log("job_test123")
    assert not job_logging.job_log_path("job_test123").exists()


class _Whisper(BaseTranscriber):
    async def transcribe(self, audio_path, language=None, on_segment_callback=None, cancel_flag=None):
        self.detected_language = "en"
        words = [TranscribedWord(" Hello", 0.5, 0.9), TranscribedWord(" there.", 0.95, 1.4)]
        seg = RawSegment(1, 0.5, 1.4, "Hello there.", words=words)
        logging.getLogger("app.test.whisper").error("Simulated decoder error")
        if on_segment_callback:
            await on_segment_callback(seg, 100.0)
        return [seg]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_job_log_export_and_server_log(tmp_path, monkeypatch):
    video = tmp_path / f"v_{uuid.uuid4().hex[:6]}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-shortest", str(video)], check=True)
    monkeypatch.setattr(orch, "TranscribeStage", lambda: TranscribeStage(_Whisper()))

    with TestClient(app) as client:
        job = client.post("/api/jobs", json={"video_path": str(video), "enable_translation": False}).json()
        for _ in range(200):
            job = client.get(f"/api/jobs/{job['id']}").json()
            if job["status"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.1)
        time.sleep(0.2)  # the summary is written in the pipeline's finally block
        res = client.get(f"/api/jobs/{job['id']}/export/log")
        assert res.status_code == 200
        assert "attachment" in res.headers["content-disposition"] and ".log" in res.headers["content-disposition"]
        text = res.text
        assert f"Job:        {job['id']}" in text and "Status:     COMPLETED" in text
        assert "Stage TRANSCRIBING started" in text and "Simulated decoder error" in text
        assert "Result: status COMPLETED, 1 subtitles" in text

        server = client.get("/api/system/log")
        assert server.status_code == 200 and "Simulated decoder error" in server.text

        assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
        assert not job_logging.job_log_path(job["id"]).exists()
        assert client.get("/api/jobs/nope/export/log").status_code == 404
