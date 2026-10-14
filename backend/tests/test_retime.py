"""Timing-only check (retime rules) and drag-to-move rows."""
import uuid
from dataclasses import dataclass
from typing import List

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.database import engine
from app.main import app
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord
from app.pipeline.retime import retime_cues


@dataclass
class Cue:
    start_time: float
    end_time: float
    segment_type: str = "DIALOGUE"


def test_cue_in_silence_waits_for_speech_onset():
    # Speech 0-2, silence, speech from 5.0. The cue starts at 3.0 (2s early) and reaches into the speech.
    cues = [Cue(3.0, 6.5)]
    stats = retime_cues(cues, [(0.0, 2.0), (5.0, 7.0)])
    assert stats.changed == 1
    assert cues[0].start_time == pytest.approx(4.8)
    assert cues[0].end_time == pytest.approx(6.5)


def test_cue_already_on_speech_is_untouched():
    cues = [Cue(4.9, 6.5)]
    assert retime_cues(cues, [(0.0, 2.0), (5.0, 7.0)]).changed == 0
    assert cues[0].start_time == 4.9


def test_on_screen_cues_are_never_touched():
    cues = [Cue(3.0, 6.5, "ON_SCREEN")]
    assert retime_cues(cues, [(0.0, 2.0), (5.0, 7.0)]).changed == 0


def test_silent_shift_only_when_allowed():
    regions = [(0.0, 2.0), (21.0, 22.5)]
    cues = [Cue(19.6, 20.9)]
    assert retime_cues(cues, regions).changed == 0
    assert retime_cues(cues, regions, allow_silent_shift=True).changed == 1
    assert cues[0].start_time == pytest.approx(20.8)
    assert cues[0].end_time - cues[0].start_time == pytest.approx(1.3)


def test_shift_does_not_overlap_next_cue():
    regions = [(0.0, 2.0), (6.0, 7.0), (8.0, 9.0)]
    cues = [Cue(4.5, 7.5), Cue(7.6, 9.0)]
    retime_cues(cues, regions)
    assert cues[0].start_time == pytest.approx(5.8)
    assert cues[0].end_time <= cues[1].start_time


def test_scope_limits_changes():
    regions = [(0.0, 2.0), (5.0, 7.0), (10.0, 12.0), (15.0, 17.0)]
    cues = [Cue(3.0, 6.5), Cue(13.0, 16.5)]
    retime_cues(cues, regions, scope=[1])
    assert cues[0].start_time == 3.0
    assert cues[1].start_time == pytest.approx(14.8)


@pytest.fixture(name="client")
def client_fixture():
    with TestClient(app) as client:
        yield client


def _job(rows: List[tuple]) -> str:
    job_id = f"test_{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(Job(id=job_id, video_path="x.mkv", file_name="x.mkv", status=JobStatus.COMPLETED))
        session.commit()
        for i, (s, e, t) in enumerate(rows, start=1):
            session.add(SubtitleSegmentRecord(job_id=job_id, index=i, start_time=s, end_time=e, original_text=t))
        session.commit()
    return job_id


def _no_video(monkeypatch):
    monkeypatch.setattr("app.api.routes_jobs._can_retime", lambda job: False)


def test_move_swaps_content_and_keeps_time_slots(client, monkeypatch):
    _no_video(monkeypatch)
    job_id = _job([(19.6, 20.9, "We need another"), (21.0, 22.5, "I said I'd try!"), (23.0, 24.0, "Fine.")])
    segs = client.get(f"/api/jobs/{job_id}").json()["segments"]
    first = segs[0]["id"]
    r = client.post(f"/api/jobs/{job_id}/segments/move", json={"segment_id": first, "to_position": 1})
    assert r.status_code == 200
    body = r.json()
    assert [s["original_text"] for s in body["segments"]] == ["I said I'd try!", "We need another", "Fine."]
    assert [(s["start_time"], s["end_time"]) for s in body["segments"]] == [(19.6, 20.9), (21.0, 22.5), (23.0, 24.0)]
    assert body["retimed"] is False


def test_move_runs_section_retime(client, monkeypatch):
    job_id = _job([(19.6, 20.9, "We need another"), (21.0, 22.5, "I said I'd try!"), (30.0, 31.0, "Fine.")])
    monkeypatch.setattr("app.api.routes_jobs._can_retime", lambda job: True)
    seen = {}

    def fake_regions(job_id_, video, stream, channels, boost, start=0.0, end=None):
        seen["window"] = (start, end)
        return [(0.0, 5.0), (20.8, 22.5)]

    monkeypatch.setattr("app.pipeline.speech_regions.speech_regions", fake_regions)
    first = client.get(f"/api/jobs/{job_id}").json()["segments"][0]["id"]
    body = client.post(f"/api/jobs/{job_id}/segments/move", json={"segment_id": first, "to_position": 1}).json()
    assert body["retimed"] is True
    assert seen["window"][1] < 30.0  # only the moved section was analysed
    assert body["segments"][2]["start_time"] == 30.0
    assert body["segments"][0]["original_text"] == "I said I'd try!"


def test_retime_endpoint_requires_video(client):
    job_id = _job([(1.0, 2.0, "Hi")])
    assert client.post(f"/api/jobs/{job_id}/retime", json={}).status_code == 409


def test_retime_endpoint_updates_segments(client, monkeypatch, tmp_path):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"x")
    job_id = _job([(3.0, 6.5, "Hello there")])
    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.video_path = str(video)
        session.add(job)
        session.commit()
    monkeypatch.setattr(
        "app.pipeline.speech_regions.speech_regions", lambda *a, **k: [(0.0, 2.0), (5.0, 7.0)]
    )
    body = client.post(f"/api/jobs/{job_id}/retime", json={}).json()
    assert body["changed"] == 1
    assert body["segments"][0]["start_time"] == pytest.approx(4.8)
