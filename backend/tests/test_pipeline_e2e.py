"""Full pipeline on a generated clip, with Whisper replaced by a fake transcriber."""
import shutil
import subprocess
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import app.pipeline.orchestrator as orch
from app.core.config import settings
from app.main import app
from app.pipeline.context import RawSegment, TranscribedWord
from app.pipeline.stages.transcribe_stage import TranscribeStage
from app.providers.transcription.base import BaseTranscriber

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


class FakeWhisper(BaseTranscriber):
    async def transcribe(self, audio_path, language=None, on_segment_callback=None, cancel_flag=None):
        assert audio_path.endswith(".wav")
        self.detected_language = "en"
        texts = [("Just to", 0.2), ("come down with the virus.", 0.9), ("Birds?", 2.5)]
        out = []
        for i, (text, start) in enumerate(texts, start=1):
            words, t = [], start
            for w in text.split():
                words.append(TranscribedWord(word=" " + w, start=t, end=t + 0.25))
                t += 0.3
            seg = RawSegment(index=i, start_time=start, end_time=words[-1].end, text=text, words=words)
            out.append(seg)
            if on_segment_callback:
                await on_segment_callback(seg, i * 33.0)
        return out


def test_full_pipeline_with_fake_whisper(tmp_path, monkeypatch):
    clip = tmp_path / f"clip_{uuid.uuid4().hex[:6]}.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-f", "lavfi", "-i", "color=c=black:s=64x64:d=4", "-shortest", str(clip)],
        check=True,
    )
    monkeypatch.setattr(orch, "TranscribeStage", lambda: TranscribeStage(FakeWhisper()))

    with TestClient(app) as client:
        job = client.post("/api/jobs", json={"video_path": str(clip), "enable_translation": False}).json()
        with client.websocket_connect(f"/ws/jobs/{job['id']}") as ws:
            events = []
            deadline = time.time() + 20
            while time.time() < deadline:
                msg = ws.receive_json()
                events.append(msg["event"])
                if msg["event"] in ("JOB_COMPLETED", "JOB_FAILED"):
                    break
        assert "JOB_COMPLETED" in events, events
        assert "SEGMENTS_REFINED" in events

        done = client.get(f"/api/jobs/{job['id']}").json()
        assert done["status"] == "COMPLETED"
        assert done["detected_language"] == "en"
        texts = [s["original_text"] for s in done["segments"]]
        assert texts == ["Just to come down with the virus.", "Birds?"]
        assert all(s["id"] for s in done["segments"])
        # Temporary audio is removed once transcription is done
        assert not (settings.storage_dir / "audio" / f"{job['id']}.wav").exists()
        # Export uses the detected language in the filename
        cd = client.get(f"/api/jobs/{job['id']}/export/srt").headers["content-disposition"]
        assert ".en.srt" in cd
