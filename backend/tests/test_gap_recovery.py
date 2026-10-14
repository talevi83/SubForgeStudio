"""Recovering speech the voice detector skipped (Whisper without VAD over uncovered audio)."""
import types
import wave

import numpy as np
import pytest

import app.providers.transcription.faster_whisper as fw
from app.pipeline import gap_recovery as gr
from app.pipeline.context import RawSegment


def test_find_gaps_respects_coverage_exclusions_and_edges():
    covered = [(2.0, 5.0), (5.5, 9.0), (20.0, 25.0)]
    gaps = gr.find_gaps(covered, duration=30.0, exclude=[(12.0, 15.0)])
    # 0-2 (start of file), 9-12 and 15-20 (around the foreign passage), 25-30 (end); 5.0-5.5 is too short
    assert gaps == [(0.0, 1.85), (9.15, 11.85), (15.15, 19.85), (25.15, 30.0)]
    assert gr.split_long([(0.0, 60.0)], 28.0) == [(0.0, 28.0), (28.0, 56.0), (56.0, 60.0)]


def test_level_db_tells_silence_from_sound():
    sr = 16000
    audio = np.zeros(4 * sr, dtype=np.float32)
    audio[2 * sr:] = 0.1
    assert gr.level_db(audio, 0.0, 2.0) < gr.MIN_LEVEL_DB
    assert gr.level_db(audio, 2.0, 4.0) > gr.MIN_LEVEL_DB


@pytest.mark.parametrize("text,logprob,no_speech,ratio,probs,expected", [
    ("Cease fire! Head shot!", -0.4, 0.2, 1.2, [0.8, 0.9, 0.85, 0.7], None),
    ("Fire!", -0.3, 0.3, 1.0, [0.9], None),
    ("Thank you for watching!", -0.2, 0.1, 1.0, [0.9] * 4, "known hallucination"),
    ("Thank you.", -0.5, 0.0, 1.0, [0.9], "known hallucination"),   # turbo's favourite invention on silence
    ("We'll be right back.", -0.6, 0.0, 1.0, [0.9] * 4, "known hallucination"),
    ("♪ ♪", -0.2, 0.1, 1.0, [0.9], "known hallucination"),
    ("Subtitles by the Amara.org community", -0.3, 0.1, 1.1, [0.9] * 5, "known hallucination"),
    ("We need help, we're in the warehouse.", -1.3, 0.2, 1.2, [0.5] * 6, "low confidence"),
    ("We need help, we're in the warehouse.", -1.3, 0.2, 1.2, [0.8] * 6, None),  # sure words under noise
    ("Clear.", -1.46, 0.07, 1.0, [0.8], None),                    # a shout under gunfire (window logprob is the noise's)
    ("Get the hell out of there!", -1.21, 0.19, 1.1, [0.75] * 6, None),
    ("Go, go, go, go, go.", -0.5, 0.1, 1.4, [0.7] * 5, None),       # real repeated command
    ("If you want to know exactly what's going on, you can find the link in", -1.0, 0.5, 1.0, [0.8] * 14, "known hallucination"),
    ("Okay.", -0.95, 0.4, 1.0, [0.8], "low confidence"),          # short lines are held to a higher bar
    ("I know, Mike.", -0.66, 0.03, 1.0, [0.56, 0.6, 0.52], None),  # a real line from the episode
    ("over, copy, roger, wilco, sitrep, klicks, callsign", -0.4, 0.2, 1.3, [0.9] * 7, "echo of the prompt words"),
    ("Get out of there now!", -0.5, 0.8, 1.1, [0.8] * 5, "probably not speech"),
    ("go go go go go go go go go", -0.3, 0.1, 3.1, [0.9] * 9, "repetitive"),
    ("Masks on, everybody, now.", -0.5, 0.2, 1.1, [0.3] * 4, "unsure words"),
])
def test_rejection_reason(text, logprob, no_speech, ratio, probs, expected):
    reason = gr.rejection_reason(text, logprob, no_speech, ratio, probs, duration=1.5)
    assert (reason is None) if expected is None else (reason or "").startswith(expected)


def test_duplicates_of_neighbouring_lines_are_dropped():
    assert gr.rejection_reason("Copy that.", -0.2, 0.1, 1.0, [0.9, 0.9], 1.0, ["Copy that"]) == \
        "duplicate of a neighbouring line"


def test_repeated_shouted_commands_are_kept():
    # "Fire! ... Fire! ... Fire!" on a shooting range: 0.1s words, mediocre probability, same as neighbours
    for text, probs in [("Fire!", [0.41]), ("Fire!", [0.55]), ("Move!", [0.6]), ("Cease fire!", [0.5, 0.5])]:
        assert gr.rejection_reason(text, -0.52, 0.04, 1.0, probs, duration=0.1, neighbours=["Fire!"]) is None
    assert gr.rejection_reason("Fire!", -0.5, 0.6, 1.0, [0.9], duration=0.1) == "probably not speech (no_speech 0.60)"
    assert gr.rejection_reason("Ugh!", -0.8, 0.5, 1.0, [0.03], duration=0.1) == "too short"
    assert gr.rejection_reason("Okay.", -0.5, 0.1, 1.0, [0.9], duration=0.1) == "too short"


def test_lines_stretched_over_silence_are_dropped():
    assert gr.rejection_reason("There you go.", -0.5, 0.1, 1.0, [0.9] * 3, duration=11.2).startswith("stretched")
    assert gr.rejection_reason("There you go.", -0.5, 0.1, 1.0, [0.9] * 3, duration=1.2) is None


class _GapModel:
    """Pretends to decode the clips it is given: real shouting, a hallucination, and a dupe."""

    def __init__(self):
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs

        def seg(s, e, text, lp=-0.3, ns=0.2, cr=1.1, wp=0.85):
            words = [types.SimpleNamespace(word=" " + w, start=s + i * 0.3, end=s + i * 0.3 + 0.25, probability=wp)
                     for i, w in enumerate(text.split())]
            return types.SimpleNamespace(start=s, end=e, text=" " + text, avg_logprob=lp, no_speech_prob=ns,
                                         compression_ratio=cr, words=words)

        return iter([
            seg(12.2, 13.4, "Cease fire! Cease fire!"),
            seg(14.0, 15.0, "Thank you for watching."),
            seg(16.0, 17.0, "Hello there."),            # repeats the line right before the gap
            seg(4.0, 5.0, "Inside covered speech"),    # outside any gap -> ignored
        ]), None


@pytest.mark.asyncio
async def test_recover_missed_speech_with_fake_model(tmp_path, monkeypatch):
    sr = 16000
    audio = np.full(20 * sr, 0.05, dtype=np.float32)
    wav_path = tmp_path / "a.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    model = _GapModel()
    monkeypatch.setattr(fw, "_load_model", lambda *a: model)
    monkeypatch.setattr(fw, "get_hardware_info", lambda: {"cuda_available": False})

    t = fw.FasterWhisperTranscriber("tiny", device="cpu", comms_boost=True)
    spoken = [RawSegment(1, 1.0, 11.0, "Hello there.")]
    recovered = await t.recover_missed_speech(str(wav_path), "en", spoken, exclude=[(18.0, 20.0)])

    assert [(r.text, r.start_time) for r in recovered] == [("Cease fire! Cease fire!", 12.2)]
    assert recovered[0].words and recovered[0].words[0].word == " Cease"
    # Only the uncovered audio is decoded, without VAD; the foreign passage (18-20s) is skipped
    assert model.kwargs["vad_filter"] is False
    assert model.kwargs["clip_timestamps"] == [11.15, 17.85]  # 0-1s is too short to bother
    assert "hotwords" not in model.kwargs and "initial_prompt" not in model.kwargs
    assert model.kwargs["language"] == "en"


class _RecoveringWhisper:
    """Main pass finds one line; the gap pass finds a shouted one before it."""

    detected_language = "en"

    async def transcribe(self, audio_path, language=None, on_segment_callback=None, cancel_flag=None):
        from app.pipeline.context import TranscribedWord

        seg = RawSegment(1, 3.0, 4.0, "Hello there.", words=[TranscribedWord(" Hello", 3.0, 3.4),
                                                              TranscribedWord(" there.", 3.45, 4.0)])
        if on_segment_callback:
            await on_segment_callback(seg, 100.0)
        return [seg]

    async def recover_missed_speech(self, audio_path, language, spoken, exclude=None, cancel_flag=None):
        from app.pipeline.context import TranscribedWord

        assert language == "en" and [s.text for s in spoken] == ["Hello there."]
        return [RawSegment(0, 0.5, 1.5, "Cease fire!", words=[TranscribedWord(" Cease", 0.5, 0.9),
                                                               TranscribedWord(" fire!", 0.95, 1.5)])]


def test_recovered_lines_reach_the_subtitles(tmp_path, monkeypatch):
    import shutil
    import subprocess
    import time
    import uuid

    from fastapi.testclient import TestClient

    import app.pipeline.orchestrator as orch
    from app.main import app
    from app.pipeline.stages.transcribe_stage import TranscribeStage

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    video = tmp_path / f"v_{uuid.uuid4().hex[:6]}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:d=6",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=6", "-shortest", str(video)], check=True)
    monkeypatch.setattr(orch, "TranscribeStage", lambda: TranscribeStage(_RecoveringWhisper()))
    with TestClient(app) as client:
        job = client.post("/api/jobs", json={"video_path": str(video), "enable_translation": False}).json()
        for _ in range(200):
            job = client.get(f"/api/jobs/{job['id']}").json()
            if job["status"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.1)
    assert job["status"] == "COMPLETED", job.get("error_message")
    assert [s["original_text"] for s in job["segments"]] == ["Cease fire!", "Hello there."]


def test_coverage_comes_from_words_not_segment_bounds():
    from app.pipeline.context import TranscribedWord

    # One batched segment 57-85s whose words leave 60-84s empty (VAD cut a shooting range out)
    seg = RawSegment(1, 57.0, 85.0, "Let me off the ship. Weapons on set!",
                     words=[TranscribedWord(" Let", 57.0, 57.3), TranscribedWord(" me", 57.35, 57.5),
                            TranscribedWord(" off", 57.6, 58.0), TranscribedWord(" Weapons", 84.6, 85.0)])
    covered = gr.covered_intervals([seg])
    assert covered == [(57.0, 58.0), (84.6, 85.0)]
    assert (58.15, 84.45) in gr.find_gaps(covered, duration=90.0)


def test_recovered_lines_keep_their_place_inside_a_batched_segment():
    """A batched segment spanning 57-85s must not push lines recovered at 73-82s after 85s."""
    from app.pipeline.context import TranscribedWord
    from app.pipeline.stages.refine_stage import refine_raw_segments, split_at_pauses

    W = TranscribedWord
    batched = RawSegment(1, 57.0, 85.0, "Let me off the ship this time. Weapons on set!", words=[
        W(" Let", 57.0, 57.2), W(" me", 57.25, 57.4), W(" off", 57.45, 57.6), W(" the", 57.65, 57.8),
        W(" ship", 57.85, 58.1), W(" this", 58.15, 58.3), W(" time.", 58.35, 58.7),
        W(" Weapons", 84.66, 84.9), W(" on", 84.95, 85.0), W(" set!", 85.05, 85.4)])
    recovered = [RawSegment(0, 73.1, 74.7, "Move! Fire!", words=[W(" Move!", 73.1, 73.6), W(" Fire!", 74.0, 74.7)]),
                 RawSegment(0, 82.0, 82.4, "Head shot!", words=[W(" Head", 82.0, 82.2), W(" shot!", 82.2, 82.4)])]
    parts = split_at_pauses([batched] + recovered)
    assert [p.text for p in parts] == ["Let me off the ship this time.", "Move! Fire!", "Head shot!", "Weapons on set!"]
    refined = refine_raw_segments([batched] + recovered)
    starts = {r["original_text"]: r["start_time"] for r in refined}
    assert starts["Move! Fire!"] == pytest.approx(73.1, abs=0.05)
    assert starts["Head shot!"] == pytest.approx(82.0, abs=0.05)
    assert starts["Weapons on set!"] == pytest.approx(84.66, abs=0.05)
    assert all(r["end_time"] - r["start_time"] >= 0.4 for r in refined)


def test_bare_numbers_and_clocks_are_not_on_screen_text():
    from app.pipeline.stages.ocr_stage import FrameDetection, merge_frame_detections

    dets = [FrameDetection(146.0, "151"), FrameDetection(822.0, "01:00"), FrameDetection(1540.0, "NAVAL HOSPITAL")]
    segs = merge_frame_detections(dets, frame_interval=2.0)
    assert [s["original_text"] for s in segs] == ["[NAVAL HOSPITAL]"]


def test_hud_labels_and_repeated_signs_are_not_on_screen_titles():
    from app.pipeline.stages.ocr_stage import FrameDetection, merge_frame_detections

    dets = [
        FrameDetection(844.0, "EAGLE"), FrameDetection(846.0, "EAGLE"),            # radio HUD label
        FrameDetection(848.0, "VULTURE"),                                          # ALL-CAPS callsign, 7 letters
        FrameDetection(1398.0, "JTF-GTMO FUEL DEPOT"),
        FrameDetection(1429.0, "JTF-GTMO FUEL DEPOT"),                            # camera comes back to the sign
        FrameDetection(1700.0, "JTF-GTMO FUEL DEPOT"),                            # much later: shown again
        FrameDetection(1800.0, "Siberia"),                                        # a 7-letter one-word place title stays
    ]
    segs = merge_frame_detections(dets, frame_interval=2.0, drop_tags=True, repeat_window=180.0)
    assert [(s["start_time"], s["original_text"]) for s in segs] == [
        (1398.0, "[JTF-GTMO FUEL DEPOT]"), (1700.0, "[JTF-GTMO FUEL DEPOT]"), (1800.0, "[Siberia]"),
    ]
    # Burned-in subtitles keep short words and repeats
    subs = merge_frame_detections([FrameDetection(10.0, "Да."), FrameDetection(20.0, "Да.")], frame_interval=2.0, bracket=False)
    assert [s["original_text"] for s in subs] == ["Да.", "Да."]


def test_prompt_forbids_ascii_quotes_in_hebrew_abbreviations():
    from app.providers.translation.gemini_provider import build_subtitle_system_prompt

    prompt = build_subtitle_system_prompt("English", "Hebrew")
    assert "ד״ר" in prompt and 'ASCII double-quote' in prompt
