"""Series glossary profiles: saved once, reused (and extended) by every episode."""
import time
import uuid
from typing import List

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.database import engine
from app.core.glossary_profiles import clean_name, load_profile, merge_glossary, save_profile
from app.main import app
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord
from app.pipeline.stages.translate_stage import TranslateStage
from app.providers.translation.base import BaseTranslator


def test_clean_name_removes_path_characters():
    assert clean_name('  The Last Ship: S01/S02 ') == "The Last Ship S01S02"
    assert clean_name(None) == ""


def test_merge_keeps_profile_and_adds_only_new_entries():
    known = {"summary": "Navy drama", "characters": [{"name": "Scott", "gender": "female", "target_name": "סקוט"}],
             "terms": [{"source": "XO", "translation": "סמג"}], "register_notes": "Terse"}
    fresh = {"summary": "other", "characters": [{"name": "scott", "gender": "male"}, {"name": "Green", "gender": "male"}],
             "terms": [{"source": "xo", "translation": "wrong"}, {"source": "CIC", "translation": "חמ\"ל"}],
             "register_notes": "x"}
    out = merge_glossary(known, fresh)
    assert [c["name"] for c in out["characters"]] == ["Scott", "Green"]
    assert out["characters"][0]["gender"] == "female"
    assert [t["source"] for t in out["terms"]] == ["XO", "CIC"]
    assert out["summary"] == "Navy drama" and out["register_notes"] == "Terse"
    assert merge_glossary(None, fresh) is fresh and merge_glossary(known, None) is known


class GlossaryTranslator(BaseTranslator):
    def __init__(self):
        self.known = "unset"

    async def build_glossary(self, segments, source_lang, target_lang, context_notes="", known_glossary=None):
        self.known = known_glossary
        return {"summary": "ep2", "characters": [{"name": "Green", "gender": "male", "target_name": "גרין"}],
                "terms": [], "register_notes": ""}

    async def translate_segments(self, segments, source_lang, target_lang, context_notes="", apply_rtl=False,
                                 on_batch_complete=None, glossary=None, context_segments=None):
        self.seen = glossary
        for s in segments:
            s.translated_text = f"t{s.index}"
        if on_batch_complete:
            await on_batch_complete(segments, 100.0)
        return segments


@pytest.fixture(name="client")
def client_fixture():
    with TestClient(app) as client:
        yield client


def _job(**kw) -> str:
    job_id = f"test_{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(Job(id=job_id, video_path="x.mkv", file_name="x.mkv", status=JobStatus.COMPLETED, **kw))
        session.commit()
        session.add(SubtitleSegmentRecord(job_id=job_id, index=1, start_time=1, end_time=2, original_text="Hi"))
        session.commit()
    return job_id


def _wait(client, job_id):
    end = time.time() + 10
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_saving_a_glossary_with_a_profile_attaches_and_lists_it(client):
    job_id = _job()
    name = f"Show {uuid.uuid4().hex[:6]}"
    body = {"summary": "s", "characters": [{"name": "Scott", "gender": "female"}], "terms": [], "profile": name}
    assert client.put(f"/api/jobs/{job_id}/glossary", json=body).status_code == 200
    assert client.get(f"/api/jobs/{job_id}").json()["glossary_profile"] == name
    assert name in client.get("/api/glossary-profiles").json()
    assert load_profile(name)["characters"][0]["name"] == "Scott"
    body["profile"] = ""
    client.put(f"/api/jobs/{job_id}/glossary", json=body)
    assert client.get(f"/api/jobs/{job_id}").json()["glossary_profile"] is None


def test_next_episode_gets_the_profile_and_extends_it(client, monkeypatch):
    name = f"Series {uuid.uuid4().hex[:6]}"
    save_profile(name, {"summary": "Navy", "characters": [{"name": "Scott", "gender": "female", "target_name": "סקוט"}],
                        "terms": [], "register_notes": ""})
    fake = GlossaryTranslator()
    monkeypatch.setattr(TranslateStage, "_make_translator", lambda self, ctx: fake)
    job_id = _job(glossary_profile=name)
    assert client.post(f"/api/jobs/{job_id}/translate", json={}).status_code == 200
    job = _wait(client, job_id)
    assert fake.known["characters"][0]["name"] == "Scott"  # the glossary pass was told what is known
    names = [c["name"] for c in job["glossary"]["characters"]]
    assert names == ["Scott", "Green"]
    assert [c["name"] for c in load_profile(name)["characters"]] == ["Scott", "Green"]  # profile grew


def test_jobs_without_a_profile_behave_as_before(client, monkeypatch):
    fake = GlossaryTranslator()
    monkeypatch.setattr(TranslateStage, "_make_translator", lambda self, ctx: fake)
    job_id = _job()
    client.post(f"/api/jobs/{job_id}/translate", json={})
    _wait(client, job_id)
    assert fake.known is None
