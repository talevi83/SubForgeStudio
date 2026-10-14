"""Foreign-language passages: detection, burned-in subtitle reading, and timeline merge."""
import json
import shutil
import subprocess
import time
import types
import uuid
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.pipeline.orchestrator as orch
import app.providers.transcription.faster_whisper as fw
from app.main import app
from app.pipeline.context import RawSegment, TranscribedWord
from app.pipeline.foreign_speech import build_foreign_spans, group_speech
from app.pipeline.stages.ocr_stage import FrameDetection, OnScreenTextStage, SUBTITLE_INSTRUCTION, merge_frame_detections
from app.pipeline.stages.refine_stage import merge_foreign
from app.pipeline.stages.transcribe_stage import TranscribeStage
from app.providers.transcription.base import BaseTranscriber

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _chunk(start, end, lang="en", prob=0.95):
    return {"start": start, "end": end, "language": lang, "probability": prob}


def _seg(start, end, text, kind="DIALOGUE"):
    return {"index": 0, "start_time": start, "end_time": end, "original_text": text,
            "translated_text": None, "segment_type": kind, "confidence": 1.0, "cps": 0}


# ---------------------------------------------------------------------------
# Span detection
# ---------------------------------------------------------------------------

def test_group_speech_merges_close_pieces_and_caps_length():
    sr = 16000
    ts = [{"start": 0, "end": 2 * sr}, {"start": int(2.5 * sr), "end": 4 * sr},  # merged
          {"start": 10 * sr, "end": 40 * sr}]  # one long piece, split into <=12s chunks
    groups = group_speech(ts)
    assert groups[0] == (0.0, 4.0)
    assert all(e - s <= 12.0 for s, e in groups)
    assert groups[-1][1] == 40.0


def test_spans_from_language_and_untranscribed_speech():
    chunks = [
        _chunk(0, 5, "ru", 0.9),        # Russian, Whisper wrote nothing
        _chunk(6, 9, "ru", 0.85),       # Russian, Whisper "translated" it (covered below)
        _chunk(9.5, 14, "en", 0.99),    # English dialogue
        _chunk(30, 33, "en", 0.4),      # unsure + no text -> untranscribed
        _chunk(50, 54, "en", 0.95),     # sure English with no text (e.g. Whisper skipped it) -> ignored
    ]
    spoken = [(6.2, 8.8), (9.6, 13.9)]
    spans = build_foreign_spans(chunks, "en", spoken)
    assert len(spans) == 2
    first, second = spans
    assert first["language"] == "ru" and first["reason"] == "language"
    assert first["cores"] == [(0, 9)]                  # tight: the English chunk is not a core
    assert first["start"] == 0 and first["end"] == 11  # OCR window padded by 2s
    assert second["reason"] == "untranscribed" and second["language"] is None


def test_nearby_cores_share_one_window_but_stay_separate():
    chunks = [_chunk(3.8, 5.8, "ru", 0.9), _chunk(8, 12, "en", 0.99), _chunk(13.6, 18.8, "ru", 0.9)]
    spans = build_foreign_spans(chunks, "en", [(8, 12)])
    assert len(spans) == 1
    assert spans[0]["cores"] == [(3.8, 5.8), (13.6, 18.8)]
    assert spans[0]["start"] == pytest.approx(1.8) and spans[0]["end"] == pytest.approx(20.8)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

SPAN = {"start": 1.8, "end": 20.8, "cores": [(3.8, 5.8), (13.6, 18.8)], "language": "ru"}


def test_burned_in_subtitles_replace_garbage_and_keep_english_between():
    spoken = [_seg(4.0, 5.5, "Something garbled."), _seg(8.0, 12.0, "English line in between."),
              _seg(14.0, 18.0, "More garbage."), _seg(25.0, 27.0, "Later dialogue.")]
    ocr = [_seg(2.5, 6.0, "We're going to Guantanamo Bay...", "FOREIGN"),
           _seg(13.5, 16.0, "Stall them.", "FOREIGN"),
           _seg(16.1, 18.5, "How am I supposed to stall them?", "FOREIGN")]
    audio = [_seg(4.0, 5.5, "Мы идём в Гуантанамо.")]
    merged = merge_foreign(spoken, [SPAN], ocr, audio, min_gap=0.084)
    texts = [(m["segment_type"], m["original_text"]) for m in merged]
    assert texts == [
        ("FOREIGN", "We're going to Guantanamo Bay..."),
        ("DIALOGUE", "English line in between."),
        ("FOREIGN", "Stall them."),
        ("FOREIGN", "How am I supposed to stall them?"),
        ("DIALOGUE", "Later dialogue."),
    ]


def test_falls_back_to_whisper_in_the_foreign_language():
    spoken = [_seg(4.0, 5.5, "Garbled.")]
    audio = [_seg(4.0, 5.6, "Мы идём в Гуантанамо.")]
    merged = merge_foreign(spoken, [SPAN], [], audio)
    assert [(m["segment_type"], m["original_text"]) for m in merged] == [("FOREIGN", "Мы идём в Гуантанамо.")]


def test_span_without_any_replacement_is_left_alone():
    spoken = [_seg(4.0, 5.5, "Whatever Whisper heard.")]
    assert merge_foreign(spoken, [SPAN], [], []) == spoken


def test_subtitle_on_top_of_kept_dialogue_is_dropped_and_edges_dont_overlap():
    spoken = [_seg(8.0, 12.0, "English line."), _seg(18.9, 21.0, "Right after.")]
    ocr = [_seg(8.5, 11.5, "Stray subtitle", "FOREIGN"), _seg(14.0, 19.2, "Last foreign line.", "FOREIGN")]
    merged = merge_foreign(spoken, [SPAN], ocr, [], min_gap=0.084)
    assert "Stray subtitle" not in [m["original_text"] for m in merged]
    for a, b in zip(merged, merged[1:]):
        assert b["start_time"] >= a["end_time"] + 0.08


def test_subtitle_frames_merge_without_brackets():
    dets = [FrameDetection(4.0, "We're going to Guantanamo Bay..."), FrameDetection(4.5, "We're going to Guantanamo Bay..."),
            FrameDetection(5.0, "- Stall them.\n- How?")]
    segs = merge_frame_detections(dets, frame_interval=0.5, buffer=0.5, min_duration=0.8,
                                  bracket=False, segment_type="FOREIGN")
    assert [(s["start_time"], s["end_time"], s["original_text"]) for s in segs] == [
        (4.0, 5.0, "We're going to Guantanamo Bay..."),
        (5.0, 5.8, "- Stall them.\n- How?"),
    ]
    assert all(s["segment_type"] == "FOREIGN" for s in segs)


# ---------------------------------------------------------------------------
# Whisper analysis (fake model, real WAV)
# ---------------------------------------------------------------------------

class _LangModel:
    """detect_language says Russian for loud audio; transcribe returns one Russian line."""

    def __init__(self):
        self.transcribe_calls = []

    def detect_language(self, audio):
        return ("ru", 0.92, []) if float(np.abs(audio).mean()) > 0.3 else ("en", 0.97, [])

    def transcribe(self, audio, **kwargs):
        self.transcribe_calls.append(kwargs)
        w = types.SimpleNamespace(word=" Привет", start=0.2, end=0.8, probability=0.9)
        seg = types.SimpleNamespace(start=0.2, end=0.8, text=" Привет.", avg_logprob=-0.2, words=[w])
        return iter([seg]), None


@pytest.mark.asyncio
async def test_analyze_foreign_speech_with_fake_model(tmp_path, monkeypatch):
    sr = 16000
    audio = np.full(20 * sr, 0.1, dtype=np.float32)
    audio[10 * sr:14 * sr] = 0.5  # the "Russian" part
    wav_path = tmp_path / "a.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())

    import faster_whisper.vad as vad
    monkeypatch.setattr(vad, "get_speech_timestamps", lambda a, o=None, **k: [
        {"start": 1 * sr, "end": 4 * sr}, {"start": 10 * sr, "end": 14 * sr}])
    model = _LangModel()
    monkeypatch.setattr(fw, "_load_model", lambda *a: model)
    monkeypatch.setattr(fw, "get_hardware_info", lambda: {"cuda_available": False})

    t = fw.FasterWhisperTranscriber("tiny", device="cpu")
    spoken = [RawSegment(1, 1.0, 4.0, "Hello there.")]
    spans, fallback = await t.analyze_foreign_speech(str(wav_path), "en", spoken)
    assert len(spans) == 1 and spans[0]["language"] == "ru" and spans[0]["cores"] == [(10.0, 14.0)]
    assert model.transcribe_calls[0]["language"] == "ru"
    assert fallback[0].text == "Привет." and fallback[0].start_time == pytest.approx(10.2)
    assert fallback[0].words[0].end == pytest.approx(10.8)


# ---------------------------------------------------------------------------
# End to end (real ffmpeg; Whisper and Gemini faked)
# ---------------------------------------------------------------------------

class _ForeignWhisper(BaseTranscriber):
    async def transcribe(self, audio_path, language=None, on_segment_callback=None, cancel_flag=None):
        self.detected_language = "en"
        out = []
        for i, (start, text) in enumerate([(2.5, "Garbled nonsense here."), (8.0, "Back in English now.")], start=1):
            words, t = [], start
            for w in text.split():
                words.append(TranscribedWord(" " + w, t, t + 0.3)); t += 0.35
            seg = RawSegment(i, start, words[-1].end, text, words=words)
            out.append(seg)
            if on_segment_callback:
                await on_segment_callback(seg, 50.0)
        return out

    async def analyze_foreign_speech(self, audio_path, main_language, spoken, cancel_flag=None):
        assert main_language == "en"
        span = {"start": 1.5, "end": 6.0, "cores": [(2.0, 5.5)], "language": "ru", "probability": 0.9, "reason": "language"}
        return [span], [RawSegment(1, 2.4, 4.0, "Мы идём в Гуантанамо.")]


class _SubtitleVision:
    """'Reads' two subtitles; the second stays on screen until 7.5s — past the 6.0s window edge."""

    def __init__(self):
        self.models = self
        self.subtitle_stamps = []

    def generate_content(self, model, contents, config):
        stamps = [float(c.split("t=")[1].rstrip("s:")) for c in contents if isinstance(c, str) and c.startswith("Frame at")]
        hits = []
        if config.system_instruction == SUBTITLE_INSTRUCTION:
            self.subtitle_stamps.extend(stamps)
            for t in stamps:
                if 2.0 <= t < 4.0:
                    hits.append({"timestamp": t, "text": "We're going to Guantanamo Bay..."})
                elif 4.0 <= t <= 7.5:
                    hits.append({"timestamp": t, "text": "I don't know how long we'll be there."})
        usage = types.SimpleNamespace(prompt_token_count=100, candidates_token_count=10, thoughts_token_count=0)
        return types.SimpleNamespace(text=json.dumps({"detections": hits}), usage_metadata=usage)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_e2e_burned_in_subtitles_for_foreign_speech(tmp_path, monkeypatch):
    video = tmp_path / f"ru_{uuid.uuid4().hex[:6]}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:d=14",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=14", "-shortest", str(video)], check=True)
    vision = _SubtitleVision()
    monkeypatch.setattr(orch, "TranscribeStage", lambda: TranscribeStage(_ForeignWhisper()))
    monkeypatch.setattr(orch, "OnScreenTextStage", type("S", (OnScreenTextStage,), {
        "__init__": lambda self: OnScreenTextStage.__init__(self, client_factory=lambda: vision)}))

    with TestClient(app) as client:
        job = client.post("/api/jobs", json={"video_path": str(video), "enable_translation": False,
                                             "detect_onscreen_text": True}).json()
        for _ in range(300):
            job = client.get(f"/api/jobs/{job['id']}").json()
            if job["status"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.1)

    assert job["status"] == "COMPLETED", job.get("error_message")
    segs = [(s["segment_type"], s["original_text"], s["start_time"], s["end_time"]) for s in job["segments"]]
    kinds = [(k, t) for k, t, _, _ in segs]
    assert kinds == [
        ("FOREIGN", "We're going to Guantanamo Bay..."),
        ("FOREIGN", "I don't know how long we'll be there."),
        ("DIALOGUE", "Back in English now."),
    ]
    # Timing follows the subtitles: 2.0s on, and the second line runs past the original window
    assert segs[0][2] == pytest.approx(2.0, abs=0.01)
    assert segs[1][3] >= 7.5
    assert max(vision.subtitle_stamps) > 6.0  # the window was extended
    assert job["usage"]["stages"]["foreign"]["requests"] >= 1


class _LateReplyWhisper(_ForeignWhisper):
    """Russian at 2-6s (span ends at 6.0); the next English line only at 22s."""

    async def transcribe(self, audio_path, language=None, on_segment_callback=None, cancel_flag=None):
        self.detected_language = "en"
        words = [TranscribedWord(" Back", 22.0, 22.3), TranscribedWord(" in", 22.35, 22.6),
                 TranscribedWord(" English.", 22.65, 23.0)]
        seg = RawSegment(1, 22.0, 23.0, "Back in English.", words=words)
        if on_segment_callback:
            await on_segment_callback(seg, 50.0)
        return [seg]


class _LateReplyVision:
    """Subtitles keep coming after the speech detector's span: a gap, then two short replies."""

    def __init__(self):
        self.models = self
        self.stamps = []

    def generate_content(self, model, contents, config):
        stamps = [float(c.split("t=")[1].rstrip("s:")) for c in contents if isinstance(c, str) and c.startswith("Frame at")]
        hits = []
        if config.system_instruction == SUBTITLE_INSTRUCTION:
            self.stamps.extend(stamps)
            for t in stamps:
                if 2.0 <= t < 4.5:
                    hits.append({"timestamp": t, "text": "Stall them?"})
                elif 9.0 <= t <= 10.5:   # after a 4.5s gap with no subtitle on screen
                    hits.append({"timestamp": t, "text": "Ok. I'll try."})
                elif 13.0 <= t <= 14.5:  # beyond the fixed margin: found by extending
                    hits.append({"timestamp": t, "text": "I said I'd try!"})
        usage = types.SimpleNamespace(prompt_token_count=100, candidates_token_count=10, thoughts_token_count=0)
        return types.SimpleNamespace(text=json.dumps({"detections": hits}), usage_metadata=usage)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_e2e_subtitles_after_the_speech_span_are_read(tmp_path, monkeypatch):
    video = tmp_path / f"ru_{uuid.uuid4().hex[:6]}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:d=26",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=26", "-shortest", str(video)], check=True)
    vision = _LateReplyVision()
    monkeypatch.setattr(orch, "TranscribeStage", lambda: TranscribeStage(_LateReplyWhisper()))
    monkeypatch.setattr(orch, "OnScreenTextStage", type("S", (OnScreenTextStage,), {
        "__init__": lambda self: OnScreenTextStage.__init__(self, client_factory=lambda: vision)}))

    with TestClient(app) as client:
        job = client.post("/api/jobs", json={"video_path": str(video), "enable_translation": False,
                                             "detect_onscreen_text": True}).json()
        for _ in range(400):
            job = client.get(f"/api/jobs/{job['id']}").json()
            if job["status"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.1)

    assert job["status"] == "COMPLETED", job.get("error_message")
    texts = [(s["segment_type"], s["original_text"]) for s in job["segments"]]
    assert texts == [
        ("FOREIGN", "Stall them?"),
        ("FOREIGN", "Ok. I'll try."),
        ("FOREIGN", "I said I'd try!"),
        ("DIALOGUE", "Back in English."),
    ]
    assert max(vision.stamps) > 12.0  # extended past the 6s margin
