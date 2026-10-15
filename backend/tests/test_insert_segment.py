"""Adding a subtitle line by hand between existing lines (POST /api/jobs/{id}/segments)."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.database import engine
from app.main import app
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord


def _job(status=JobStatus.COMPLETED, duration=120.0, translated=False):
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    with Session(engine) as session:
        session.add(Job(id=job_id, video_path="x.mkv", file_name="x.mkv", status=status, video_duration=duration))
        for i, (a, b, text) in enumerate([(1.0, 3.0, "First"), (10.0, 12.0, "Second"), (20.0, 22.5, "Third")], start=1):
            session.add(SubtitleSegmentRecord(job_id=job_id, index=i, start_time=a, end_time=b, original_text=text,
                                              translated_text=f"{text} (he)" if translated else None))
        session.commit()
    return job_id


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_adds_a_line_between_two_lines_and_renumbers(client):
    job_id = _job(translated=True)
    res = client.post(f"/api/jobs/{job_id}/segments", json={
        "start_time": 4.0, "end_time": 6.25, "original_text": "In between", "translated_text": "‏באמצע",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    segs = body["segments"]
    assert [s["original_text"] for s in segs] == ["First", "In between", "Second", "Third"]
    assert [s["index"] for s in segs] == [1, 2, 3, 4]
    new = next(s for s in segs if s["id"] == body["created_id"])
    assert new["index"] == 2 and new["start_time"] == 4.0 and new["end_time"] == 6.25
    assert new["translated_text"] == "באמצע"          # direction marks are stripped like other edits
    assert new["cps"] > 0 and new["segment_type"] == "DIALOGUE"
    # The job reads back the same way
    again = client.get(f"/api/jobs/{job_id}").json()["segments"]
    assert [s["index"] for s in again if s["original_text"] == "Second"] == [3]


def test_rejects_an_overlap_and_names_the_line(client):
    job_id = _job()
    res = client.post(f"/api/jobs/{job_id}/segments", json={"start_time": 11.5, "end_time": 14.0, "original_text": "x"})
    assert res.status_code == 409
    assert "#2" in res.json()["detail"] and "00:00:10,000" in res.json()["detail"]
    # Touching a neighbour exactly is fine
    ok = client.post(f"/api/jobs/{job_id}/segments", json={"start_time": 12.0, "end_time": 14.0, "original_text": "x"})
    assert ok.status_code == 200


@pytest.mark.parametrize("payload,code", [
    ({"start_time": 5.0, "end_time": 5.0, "original_text": "x"}, 422),     # no length
    ({"start_time": 6.0, "end_time": 5.0, "original_text": "x"}, 422),     # reversed
    ({"start_time": -1.0, "end_time": 0.5, "original_text": "x"}, 422),    # negative
    ({"start_time": 5.0, "end_time": 6.0, "original_text": "   "}, 422),   # no text
    ({"start_time": 130.0, "end_time": 131.0, "original_text": "x"}, 422),  # after the video
    ({"start_time": 5.0, "end_time": 6.0, "original_text": "x", "segment_type": "BOGUS"}, 422),
])
def test_rejects_invalid_lines(client, payload, code):
    assert client.post(f"/api/jobs/{_job()}/segments", json=payload).status_code == code


def test_end_past_the_video_is_clamped_and_on_screen_type_kept(client):
    job_id = _job(duration=30.0)
    res = client.post(f"/api/jobs/{job_id}/segments", json={
        "start_time": 28.0, "end_time": 33.0, "original_text": "[THE END]", "segment_type": "on_screen"})
    assert res.status_code == 200
    new = next(s for s in res.json()["segments"] if s["id"] == res.json()["created_id"])
    assert new["end_time"] == 30.0 and new["segment_type"] == "ON_SCREEN"


def test_running_job_and_unknown_job(client):
    assert client.post(f"/api/jobs/{_job(status=JobStatus.TRANSLATING)}/segments",
                       json={"start_time": 4, "end_time": 5, "original_text": "x"}).status_code == 409
    assert client.post("/api/jobs/job_missing/segments",
                       json={"start_time": 4, "end_time": 5, "original_text": "x"}).status_code == 404
