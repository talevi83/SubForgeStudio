"""Re-translating selected rows with a speaker/addressee hint, and editing the glossary."""
import time
import uuid
from typing import List

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.routes_jobs import build_row_hint
from app.core.database import engine
from app.main import app
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord
from app.models.schemas import RetranslateRequest
from app.pipeline.stages.translate_stage import TranslateStage, _notes_with_hint
from app.providers.translation.base import BaseTranslator, TranslatableSegment


class RecordingTranslator(BaseTranslator):
    def __init__(self):
        self.calls: List[List[int]] = []
        self.notes: List[str] = []

    async def build_glossary(self, segments, source_lang, target_lang, context_notes=""):
        return {"summary": "test", "characters": [], "terms": []}

    async def translate_segments(self, segments, source_lang, target_lang, context_notes="", apply_rtl=False,
                                 on_batch_complete=None, glossary=None, context_segments=None):
        self.calls.append([s.index for s in segments])
        self.notes.append(context_notes)
        for s in segments:
            s.translated_text, s.translation_failed = f"new {s.index}", False
        if on_batch_complete:
            await on_batch_complete(segments, 100.0)
        return segments


@pytest.fixture(name="client")
def client_fixture():
    with TestClient(app) as client:
        yield client


def _job(texts: List[str], **kw) -> str:
    job_id = f"test_{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(Job(id=job_id, video_path="x.mkv", file_name="x.mkv", status=JobStatus.COMPLETED, **kw))
        session.commit()
        for i, t in enumerate(texts, start=1):
            session.add(SubtitleSegmentRecord(job_id=job_id, index=i, start_time=i * 2.0, end_time=i * 2.0 + 1.5,
                                              original_text=t, translated_text=f"old {i}"))
        session.commit()
    return job_id


def _wait(client, job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_hint_text_covers_speaker_and_addressee():
    hint = build_row_hint(RetranslateRequest(addressee_gender="female", speaker_gender="male", hint="Dr. Scott"))
    assert "woman" in hint and "man" in hint and "Dr. Scott" in hint
    assert build_row_hint(RetranslateRequest()) is None


def test_scoped_note_is_appended_after_the_user_notes():
    rows = [TranslatableSegment(index=13, start_time=0, end_time=1, original_text="a"),
            TranslatableSegment(index=15, start_time=2, end_time=3, original_text="b")]
    out = _notes_with_hint("Show notes.", "She is a woman.", rows)
    assert out.startswith("Show notes.") and "#13-#15" in out and "She is a woman." in out
    assert _notes_with_hint("Show notes.", None, rows) == "Show notes."


def test_only_selected_rows_are_translated_with_the_hint(client, monkeypatch):
    fake = RecordingTranslator()
    monkeypatch.setattr(TranslateStage, "_make_translator", lambda self, ctx: fake)
    job_id = _job(["a", "b", "c", "d"])
    segs = client.get(f"/api/jobs/{job_id}").json()["segments"]
    ids = [segs[1]["id"], segs[2]["id"]]
    r = client.post(f"/api/jobs/{job_id}/translate", json={"segment_ids": ids, "addressee_gender": "female"})
    assert r.status_code == 200
    job = _wait(client, job_id)
    assert fake.calls[-1] == [2, 3]
    assert "woman" in fake.notes[-1]
    texts = {s["index"]: s["translated_text"] for s in job["segments"]}
    assert texts == {1: "old 1", 2: "new 2", 3: "new 3", 4: "old 4"}
    assert not job["context_notes"]  # not remembered unless asked


def test_remember_hint_keeps_it_in_context_notes(client, monkeypatch):
    monkeypatch.setattr(TranslateStage, "_make_translator", lambda self, ctx: RecordingTranslator())
    job_id = _job(["a", "b"], context_notes="Base.")
    first = client.get(f"/api/jobs/{job_id}").json()["segments"][0]["id"]
    client.post(f"/api/jobs/{job_id}/translate",
                json={"segment_ids": [first], "addressee_gender": "female", "remember_hint": True})
    job = _wait(client, job_id)
    assert job["context_notes"].startswith("Base.") and "[#1-#1]" in job["context_notes"]


def test_unknown_segment_ids_are_rejected(client, monkeypatch):
    monkeypatch.setattr(TranslateStage, "_make_translator", lambda self, ctx: RecordingTranslator())
    job_id = _job(["a"])
    assert client.post(f"/api/jobs/{job_id}/translate", json={"segment_ids": [999999]}).status_code == 404


def test_glossary_can_be_edited(client):
    job_id = _job(["a"])
    body = {
        "summary": "Navy drama",
        "characters": [{"name": " Scott ", "target_name": "סקוט", "gender": "female", "notes": "doctor"},
                       {"name": "  ", "gender": "male"}],
        "terms": [{"source": "XO", "translation": "סמג"}],
        "register_notes": "",
    }
    saved = client.put(f"/api/jobs/{job_id}/glossary", json=body).json()
    assert [c["name"] for c in saved["characters"]] == ["Scott"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["glossary"]["characters"][0]["gender"] == "female"
    assert job["glossary"]["terms"][0]["translation"] == "סמג"


def test_bad_thinking_level_rejected_and_good_one_accepted(client):
    job_id = _job(["a", "b"])
    r = client.post(f"/api/jobs/{job_id}/translate", json={"thinking": "extreme"})
    assert r.status_code == 400
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "COMPLETED"


def test_thinking_override_reaches_the_translator(monkeypatch):
    from app.core.user_settings import load_settings
    from app.pipeline.context import PipelineContext
    from app.providers.translation import gemini_provider

    seen = {}

    class Spy:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(gemini_provider, "GeminiTranslator", Spy)
    ctx = PipelineContext(job_id="x", video_path="x.mkv", file_name="x.mkv", gemini_model="m-explicit", gemini_thinking="high")
    TranslateStage()._make_translator(ctx)
    assert seen["thinking"] == "high" and seen["model_name"] == "m-explicit"
