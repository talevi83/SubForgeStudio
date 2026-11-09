"""Subtitles inside a copy of the video: soft track (MKV/MP4) and burned in, via real ffmpeg runs."""
import asyncio
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core import video_embed as ve
from app.core.database import engine
from app.main import app
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe not installed")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_video(path: Path, seconds: int = 3) -> Path:
    """A tiny real video (H.264 + AAC) of the given container, made by ffmpeg."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=10:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


def _make_job(video_path: str, *, translated: bool = True, is_upload: bool = False, file_name: str = None) -> str:
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    with Session(engine) as session:
        session.add(Job(id=job_id, video_path=video_path, file_name=file_name or Path(video_path).name,
                        status=JobStatus.COMPLETED, target_language="Hebrew", source_language="en",
                        is_upload=is_upload, video_duration=3.0))
        session.add(SubtitleSegmentRecord(job_id=job_id, index=1, start_time=0.5, end_time=2.5,
                                          original_text="Hello friend?",
                                          translated_text="שלום חבר?" if translated else None))
        session.commit()
    return job_id


def _wait(client, job_id: str, task_id: str, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}/export/embed/{task_id}").json()
        if body["state"] != "running":
            return body
        time.sleep(0.2)
    raise AssertionError("embed did not finish in time")


def _streams(path: Path) -> list:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
                         check=True, capture_output=True).stdout
    return json.loads(out)["streams"]


# ---------------------------------------------------------------- pure helpers

def test_output_names_and_containers():
    assert ve.output_extension("a/Movie.mkv", "soft") == ".mkv"
    assert ve.output_extension("a/Movie.MP4", "soft") == ".mp4"
    assert ve.output_extension("a/Movie.mov", "soft") == ".mov"
    assert ve.output_extension("a/Movie.avi", "soft") == ".mkv"     # AVI can't hold a text track
    assert ve.output_extension("a/Movie.mkv", "burn") == ".mp4"
    assert ve.output_name("Show.S01E02.mkv", "he", "soft", ".mkv") == "Show.S01E02.he.subs.mkv"
    assert ve.output_name("Show.S01E02.mkv", "he", "burn", ".mp4") == "Show.S01E02.he.burned.mp4"
    assert ve.output_name("Show.mkv", None, "soft", ".mkv") == "Show.subs.mkv"


def test_soft_command_for_mp4_keeps_only_text_subs_and_sets_track_tags():
    probe = {"streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        {"index": 2, "codec_type": "subtitle", "codec_name": "mov_text"},
        {"index": 3, "codec_type": "subtitle", "codec_name": "dvd_subtitle"},
    ]}
    cmd = ve.build_soft_command("M.mp4", "s.srt", "out", probe,
                                ve.EmbedOptions(language3="heb", track_title="Hebrew", set_default=True))
    joined = " ".join(cmd)
    assert "-map 0:2" in joined and "-map 0:3" not in joined
    assert "-c:s mov_text" in joined
    assert "-metadata:s:s:1 language=heb" in joined           # new track comes after the one kept
    assert "-disposition:s:1 default" in joined
    assert cmd[-3:-1] == ["-f", "mp4"]


def test_burn_command_converts_uncommon_audio_and_uses_bare_subtitle_name():
    probe = {"streams": [{"index": 0, "codec_type": "video"},
                         {"index": 1, "codec_type": "audio", "codec_name": "dts"},
                         {"index": 2, "codec_type": "audio", "codec_name": "aac"}]}
    cmd = ve.build_burn_command("M.mkv", "subs.srt", "out", probe, ve.EmbedOptions(mode="burn"), "libx264")
    joined = " ".join(cmd)
    assert "subtitles=subs.srt:" in joined
    assert "-c:a aac" in joined and "-map 0:a:0" in joined
    cmd = ve.build_burn_command("M.mkv", "subs.srt", "out", probe,
                                ve.EmbedOptions(mode="burn", audio_stream=1, quality="high"), "libx264")
    joined = " ".join(cmd)
    assert "-map 0:a:1" in joined and "-c:a copy" in joined and "-preset slow" in joined


# ---------------------------------------------------------------- API + real ffmpeg

@needs_ffmpeg
def test_soft_track_into_mkv_next_to_video(client, tmp_path):
    video = _make_video(tmp_path / "Show.S01E02.mkv")
    job_id = _make_job(str(video))
    resp = client.post(f"/api/jobs/{job_id}/export/embed", json={"mode": "soft"})
    assert resp.status_code == 200, resp.text
    body = _wait(client, job_id, resp.json()["task_id"])
    target = tmp_path / "Show.S01E02.he.subs.mkv"
    assert body["state"] == "done", body
    assert body["path"] == str(target) and body["next_to_video"] and body["encoder"] == "copy"
    streams = _streams(target)
    assert [s["codec_type"] for s in streams] == ["video", "audio", "subtitle"]
    sub = streams[2]
    assert sub["codec_name"] == "subrip"
    assert sub["tags"].get("language") == "heb"
    assert sub["disposition"]["default"] == 1
    assert streams[0]["codec_name"] == "h264"                    # copied, not re-encoded
    text = subprocess.run(["ffmpeg", "-v", "error", "-i", str(target), "-map", "0:s:0", "-f", "srt", "-"],
                          check=True, capture_output=True).stdout.decode("utf-8")
    assert "שלום חבר" in text
    assert not list(tmp_path.glob(".*subbuild-tmp"))
    # The latest-run lookup finds it (a reopened dialog shows the result)
    assert client.get(f"/api/jobs/{job_id}/export/embed").json()["task_id"] == body["task_id"]


@needs_ffmpeg
def test_soft_track_into_mp4_is_mov_text(client, tmp_path):
    video = _make_video(tmp_path / "Clip.mp4")
    job_id = _make_job(str(video))
    task_id = client.post(f"/api/jobs/{job_id}/export/embed", json={"mode": "soft"}).json()["task_id"]
    assert _wait(client, job_id, task_id)["state"] == "done"
    streams = _streams(tmp_path / "Clip.he.subs.mp4")
    assert streams[-1]["codec_name"] == "mov_text"


@needs_ffmpeg
def test_existing_output_is_kept_unless_overwrite(client, tmp_path):
    video = _make_video(tmp_path / "Show.mkv")
    target = tmp_path / "Show.he.subs.mkv"
    target.write_bytes(b"mine")
    job_id = _make_job(str(video))
    body = client.post(f"/api/jobs/{job_id}/export/embed", json={"mode": "soft"}).json()
    assert body["state"] == "exists" and body["path"] == str(target)
    assert target.read_bytes() == b"mine"
    task_id = client.post(f"/api/jobs/{job_id}/export/embed", json={"mode": "soft", "overwrite": True}).json()["task_id"]
    assert _wait(client, job_id, task_id)["state"] == "done"
    assert target.stat().st_size > 1000


@needs_ffmpeg
def test_burn_in_makes_an_h264_mp4(client, tmp_path):
    video = _make_video(tmp_path / "Show.mkv")
    job_id = _make_job(str(video))
    task_id = client.post(f"/api/jobs/{job_id}/export/embed", json={"mode": "burn"}).json()["task_id"]
    body = _wait(client, job_id, task_id, timeout=120)
    assert body["state"] == "done", body
    assert body["encoder"] in ("libx264", "h264_nvenc")
    out = tmp_path / "Show.he.burned.mp4"
    kinds = [(s["codec_type"], s["codec_name"]) for s in _streams(out)]
    assert kinds == [("video", "h264"), ("audio", "aac")]       # no subtitle track: it's in the picture


@needs_ffmpeg
def test_uploaded_video_copy_is_a_download(client, tmp_path):
    video = _make_video(tmp_path / "a0958bc6_Show.mkv")
    job_id = _make_job(str(video), is_upload=True, file_name="Show.mkv")
    task_id = client.post(f"/api/jobs/{job_id}/export/embed", json={"mode": "soft"}).json()["task_id"]
    body = _wait(client, job_id, task_id)
    assert body["state"] == "done" and not body["next_to_video"]
    assert body["file_name"] == "Show.he.subs.mkv"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a0958bc6_Show.mkv"]   # nothing next to the upload
    dl = client.get(body["download_url"])
    assert dl.status_code == 200 and len(dl.content) > 1000
    assert "Show.he.subs.mkv" in dl.headers["content-disposition"]


@needs_ffmpeg
def test_cancel_removes_the_partial_file(client, tmp_path):
    video = _make_video(tmp_path / "Long.mkv", seconds=60)
    job_id = _make_job(str(video))
    task_id = client.post(f"/api/jobs/{job_id}/export/embed",
                          json={"mode": "burn", "quality": "high"}).json()["task_id"]
    # A second run for the same project is refused while this one runs
    assert client.post(f"/api/jobs/{job_id}/export/embed", json={"mode": "soft"}).status_code == 409
    time.sleep(0.5)
    assert client.post(f"/api/jobs/{job_id}/export/embed/{task_id}/cancel").status_code == 200
    body = _wait(client, job_id, task_id)
    assert body["state"] == "cancelled"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Long.mkv"]


def test_refusals(client, tmp_path):
    video = tmp_path / "Show.mkv"
    video.write_bytes(b"x")
    assert client.post(f"/api/jobs/{_make_job(str(video), translated=False)}/export/embed",
                       json={"mode": "soft"}).status_code == 400
    gone = _make_job(str(tmp_path / "missing.mkv"))
    assert client.post(f"/api/jobs/{gone}/export/embed", json={"mode": "soft"}).status_code == 404
    assert client.post("/api/jobs/job_nope/export/embed", json={"mode": "soft"}).status_code == 404
    assert client.get("/api/jobs/job_nope/export/embed/abc").status_code == 404


@needs_ffmpeg
def test_nvenc_failure_falls_back_to_libx264(monkeypatch, tmp_path):
    """Builds list h264_nvenc even on PCs without an NVIDIA GPU; the run must still finish."""
    video = _make_video(tmp_path / "Show.mkv")
    target = tmp_path / "Show.he.burned.mp4"
    monkeypatch.setattr(ve, "_nvenc_available", True)
    real_build = ve.build_burn_command

    def build(video_path, subs_name, out_path, probe, opts, encoder):
        cmd = real_build(video_path, subs_name, out_path, probe, opts, encoder)
        if encoder == "h264_nvenc":                      # force the NVENC attempt to fail
            cmd[cmd.index("h264_nvenc")] = "no_such_encoder"
        return cmd

    monkeypatch.setattr(ve, "build_burn_command", build)

    async def run():
        task = ve.start_embed("job_x", str(video), target, "1\n00:00:00,500 --> 00:00:02,000\nHi\n",
                              ve.EmbedOptions(mode="burn"), in_app_storage=False)
        await task.runner
        return task

    task = asyncio.run(run())
    assert task.state == "done", task.error
    assert task.encoder == "libx264" and target.is_file()
