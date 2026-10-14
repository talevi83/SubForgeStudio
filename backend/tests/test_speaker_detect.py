from app.pipeline.speaker_detect import SpeakerGuess, frame_times


def test_frame_times_stay_inside_span_and_cap_long_spans():
    ts = frame_times(10.0, 16.0)
    assert len(ts) == 3 and all(10.0 <= t <= 16.0 for t in ts) and ts == sorted(ts)
    assert max(frame_times(0.0, 500.0)) <= 30.0


def test_frame_times_never_negative_and_handles_tiny_span():
    ts = frame_times(-2.0, -1.9)
    assert all(t >= 0 for t in ts)


def test_guess_defaults_to_unknown():
    g = SpeakerGuess()
    assert g.speaker_gender == "unknown" and g.addressee_gender == "unknown"


def test_endpoint_returns_guess_and_hides_unknown(tmp_path, monkeypatch):
    import uuid
    from fastapi.testclient import TestClient
    from sqlmodel import Session, select

    from app.core.config import settings
    from app.core.database import engine
    from app.main import app
    from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord
    from app.pipeline import speaker_detect as sd

    video = tmp_path / "v.mkv"
    video.write_bytes(b"x")
    job_id = f"test_{uuid.uuid4().hex[:8]}"

    async def fake_detect(client, model, frames, lines, **kw):
        assert frames and lines[0][1] == "You'll stay."
        return SpeakerGuess(speaker_gender="female", addressee_gender="unknown", explanation="Dr. Scott on screen")

    monkeypatch.setattr(sd, "grab_frame", lambda *a, **k: b"jpeg")
    monkeypatch.setattr(sd, "detect_speakers", fake_detect)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    with TestClient(app) as client:
        with Session(engine) as session:
            session.add(Job(id=job_id, video_path=str(video), file_name="v.mkv", status=JobStatus.COMPLETED))
            session.commit()
            session.add(SubtitleSegmentRecord(job_id=job_id, index=1, start_time=2.0, end_time=4.0,
                                              original_text="You'll stay."))
            session.commit()
            seg_id = session.exec(select(SubtitleSegmentRecord).where(SubtitleSegmentRecord.job_id == job_id)).first().id
        r = client.post(f"/api/jobs/{job_id}/detect-speakers", json={"segment_ids": [seg_id]})
        assert r.status_code == 200, r.text
        assert r.json() == {"speaker_gender": "female", "addressee_gender": None, "explanation": "Dr. Scott on screen"}
