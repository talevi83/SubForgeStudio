"""Edit history: undo / redo / jump to a step, coalescing, branching, outside changes, limits."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core import edit_history
from app.core.database import engine
from app.main import app
from app.models.db_models import Job, JobHistoryEntry, JobStatus, SubtitleSegmentRecord


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _job(status=JobStatus.COMPLETED, lines=3) -> str:
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    with Session(engine) as s:
        s.add(Job(id=job_id, video_path="/nonexistent/v.mkv", file_name="v.mkv", status=status))
        for i in range(lines):
            s.add(SubtitleSegmentRecord(job_id=job_id, index=i + 1, start_time=i * 4.0, end_time=i * 4.0 + 2,
                                        original_text=f"line {i + 1}", translated_text=f"שורה {i + 1}"))
        s.commit()
    return job_id


def _segs(client, job_id):
    return sorted(client.get(f"/api/jobs/{job_id}").json()["segments"], key=lambda s: s["index"])


def _hist(client, job_id):
    return client.get(f"/api/jobs/{job_id}/history").json()


def _patch(client, job_id, seg_id, **body):
    r = client.patch(f"/api/jobs/{job_id}/segments/{seg_id}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_no_history_until_the_first_edit(client):
    job_id = _job()
    h = _hist(client, job_id)
    assert h == {"cursor": None, "can_undo": False, "can_redo": False, "entries": []}


def test_undo_and_redo_a_text_edit(client):
    job_id = _job()
    first = _segs(client, job_id)[0]
    _patch(client, job_id, first["id"], translated_text="שונה")
    h = _hist(client, job_id)
    assert [e["action"] for e in h["entries"]] == ["original", "edit"]
    assert h["entries"][1]["details"]["index"] == 1
    assert h["can_undo"] and not h["can_redo"]

    r = client.post(f"/api/jobs/{job_id}/history/undo").json()
    assert r["segments"][0]["translated_text"] == "שורה 1"
    assert r["history"]["can_redo"] and not r["history"]["can_undo"]
    assert r["history"]["cursor"] == h["entries"][0]["id"]

    r = client.post(f"/api/jobs/{job_id}/history/redo").json()
    assert r["segments"][0]["translated_text"] == "שונה"
    assert r["segments"][0]["id"] == first["id"]  # ids survive a round trip
    # nothing more to redo: a no-op, not an error
    assert client.post(f"/api/jobs/{job_id}/history/redo").status_code == 200


def test_typing_in_one_line_is_one_step(client):
    job_id = _job()
    seg_id = _segs(client, job_id)[0]["id"]
    for text in ("ש", "שו", "שונה"):
        _patch(client, job_id, seg_id, translated_text=text)
    assert [e["action"] for e in _hist(client, job_id)["entries"]] == ["original", "edit"]
    # another line, or a timing change, is a new step
    _patch(client, job_id, _segs(client, job_id)[1]["id"], translated_text="אחר")
    _patch(client, job_id, seg_id, start_time=0.5)
    assert [e["action"] for e in _hist(client, job_id)["entries"]] == ["original", "edit", "edit", "edit_timing"]


def test_a_new_edit_after_undo_discards_the_steps_ahead(client):
    job_id = _job()
    segs = _segs(client, job_id)
    _patch(client, job_id, segs[0]["id"], translated_text="A")
    _patch(client, job_id, segs[1]["id"], translated_text="B")
    client.post(f"/api/jobs/{job_id}/history/undo")
    _patch(client, job_id, segs[2]["id"], translated_text="C")
    h = _hist(client, job_id)
    assert len(h["entries"]) == 3 and not h["can_redo"]
    texts = [s["translated_text"] for s in _segs(client, job_id)]
    assert texts == ["A", "שורה 2", "C"]


def test_jump_to_any_step_and_back(client):
    job_id = _job()
    segs = _segs(client, job_id)
    client.delete(f"/api/jobs/{job_id}/segments/{segs[1]['id']}")
    client.post(f"/api/jobs/{job_id}/segments", json={"start_time": 20, "end_time": 21, "original_text": "new"})
    client.request("DELETE", f"/api/jobs/{job_id}/segments", json=[segs[0]["id"]])
    h = _hist(client, job_id)
    assert [e["action"] for e in h["entries"]] == ["original", "delete", "insert", "delete_many"]
    assert h["entries"][1]["details"] == {"index": 2}

    r = client.post(f"/api/jobs/{job_id}/history/{h['entries'][0]['id']}/restore").json()
    assert [s["original_text"] for s in r["segments"]] == ["line 1", "line 2", "line 3"]
    assert r["history"]["can_redo"] and len(r["history"]["entries"]) == 4

    r = client.post(f"/api/jobs/{job_id}/history/{h['entries'][2]['id']}/restore").json()
    assert [s["original_text"] for s in r["segments"]] == ["line 1", "line 3", "new"]
    assert [s["index"] for s in r["segments"]] == [1, 2, 3]

    assert client.post(f"/api/jobs/{job_id}/history/999999/restore").status_code == 404


def test_replace_all_is_one_step(client):
    job_id = _job()
    segs = _segs(client, job_id)
    client.patch(f"/api/jobs/{job_id}/segments", json=[{"id": s["id"], "translated_text": "X"} for s in segs])
    h = _hist(client, job_id)
    assert h["entries"][-1]["action"] == "bulk_edit" and h["entries"][-1]["details"] == {"count": 3}
    r = client.post(f"/api/jobs/{job_id}/history/undo").json()
    assert [s["translated_text"] for s in r["segments"]] == ["שורה 1", "שורה 2", "שורה 3"]


def test_outside_changes_are_kept_as_a_step(client):
    job_id = _job()
    segs = _segs(client, job_id)
    _patch(client, job_id, segs[0]["id"], translated_text="A")
    with Session(engine) as s:  # e.g. a translation run that crashed before recording
        row = s.get(SubtitleSegmentRecord, segs[2]["id"])
        row.translated_text = "from outside"
        s.add(row)
        s.commit()
    r = client.post(f"/api/jobs/{job_id}/history/undo").json()
    actions = [e["action"] for e in r["history"]["entries"]]
    assert actions == ["original", "edit", "external"]
    assert r["segments"][2]["translated_text"] == "שורה 3"
    r = client.post(f"/api/jobs/{job_id}/history/redo").json()
    assert r["segments"][2]["translated_text"] == "from outside"   # not lost


def test_translation_run_is_one_step(client):
    job_id = _job()
    seg_id = _segs(client, job_id)[0]["id"]
    with Session(engine) as s:
        edit_history.begin(s, s.get(Job, job_id))
        s.commit()
        row = s.get(SubtitleSegmentRecord, seg_id)
        row.translated_text = "תורגם מחדש"
        s.add(row)
        s.commit()
    edit_history.record_job(job_id, engine, "translate", {"scope": "all"})
    h = _hist(client, job_id)
    assert [(e["action"], e["details"]) for e in h["entries"]] == [("original", {}), ("translate", {"scope": "all"})]


def test_history_is_limited(client, monkeypatch):
    monkeypatch.setattr(edit_history, "MAX_ENTRIES", 4)
    job_id = _job(lines=6)
    for seg in _segs(client, job_id):
        _patch(client, job_id, seg["id"], translated_text=f"x{seg['index']}")
    h = _hist(client, job_id)
    assert len(h["entries"]) == 4 and h["entries"][-1]["id"] == h["cursor"]
    assert h["entries"][0]["action"] == "edit"  # the oldest steps (incl. "original") were dropped


def test_running_job_records_nothing_and_cannot_undo(client):
    job_id = _job(status=JobStatus.TRANSLATING)
    _patch(client, job_id, _segs(client, job_id)[0]["id"], translated_text="live")
    assert _hist(client, job_id)["entries"] == []
    assert client.post(f"/api/jobs/{job_id}/history/undo").status_code == 409


def test_redo_survives_an_id_taken_by_another_job(client):
    job_id = _job(lines=1)
    created = client.post(f"/api/jobs/{job_id}/segments",
                          json={"start_time": 10, "end_time": 11, "original_text": "mine"}).json()["created_id"]
    client.post(f"/api/jobs/{job_id}/history/undo")
    other = _job(lines=0)
    with Session(engine) as s:  # another project gets the freed id
        s.add(SubtitleSegmentRecord(id=created, job_id=other, index=1, start_time=0, end_time=1, original_text="theirs"))
        s.commit()
    r = client.post(f"/api/jobs/{job_id}/history/redo").json()
    assert [s["original_text"] for s in r["segments"]] == ["line 1", "mine"]
    assert r["segments"][1]["id"] != created
    with Session(engine) as s:
        assert s.get(SubtitleSegmentRecord, created).original_text == "theirs"


def test_deleting_the_project_deletes_its_history(client):
    job_id = _job()
    _patch(client, job_id, _segs(client, job_id)[0]["id"], translated_text="A")
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    with Session(engine) as s:
        assert s.exec(select(JobHistoryEntry).where(JobHistoryEntry.job_id == job_id)).all() == []


def test_cross_site_undo_is_blocked(client):
    job_id = _job()
    assert client.post(f"/api/jobs/{job_id}/history/undo", headers={"Origin": "https://evil.example"}).status_code == 403
