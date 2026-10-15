"""Syncing an existing subtitle file: parsing, alignment, text anchors, burned-in subtitles, API."""
import asyncio
import json
import random
import re
import shutil
import subprocess
import time
import uuid
from types import SimpleNamespace

import numpy as np
import pytest

from app.pipeline.context import RawSegment, TranscribedWord
from app.pipeline.sync_align import AlignCue, align, describe
from app.pipeline.sync_burned import BurnedInAnalyzer, HEIGHT, WIDTH, validate_against_speech
from app.pipeline.sync_text import (
    build_phrases, match_same_language, match_screens_same_language, match_with_gemini, transcript_words,
)
from app.utils.subtitle_parser import SubtitleParseError, decode_subtitle_bytes, detect_language, parse_subtitle_file

# --------------------------------------------------------------------------- parsing

SRT = ("1\r\n00:00:01,000 --> 00:00:02,500\r\n{\\an8}<i>Damascus</i>\r\n\r\n"
       "2\r\n00:00:03,000 --> 00:00:04,000\r\n[door slams]\r\n\r\n"
       "3\r\n00:00:05,500 --> 00:00:07,000\r\nשלום לך\r\nמה שלומך, דניאל?\r\n")


@pytest.mark.parametrize("encoding,expected", [
    ("utf-8", "utf-8"), ("utf-8-sig", "utf-8-sig"), ("utf-16", "utf-16"), ("cp1255", "windows-1255"),
])
def test_decodes_common_encodings(encoding, expected):
    text, used = decode_subtitle_bytes(SRT.encode(encoding))
    assert used == expected
    assert "שלום לך" in text


def test_parses_srt_positions_sounds_and_language():
    r = parse_subtitle_file(SRT.encode("cp1255"), "show.he.srt")
    assert r["format"] == "srt" and r["cue_count"] == 3
    first, second, third = r["cues"]
    assert first["text"] == "Damascus" and first["position"] == "top"
    assert second["kind"] == "sound"
    assert third["text"] == "שלום לך\nמה שלומך, דניאל?" and third["start"] == 5.5
    assert r["language"] == "Hebrew"  # Latin names don't outvote the Hebrew text


def test_parses_vtt_and_ass():
    vtt = "WEBVTT\n\nNOTE x\n\n00:01.000 --> 00:02.000 line:0\nSign\n\n01:00:03.250 --> 01:00:04.000\n- Hi\n- Bye\n"
    cues = parse_subtitle_file(vtt.encode(), "a.vtt")["cues"]
    assert cues[0]["position"] == "top" and cues[1]["start"] == 3603.25
    ass = ("[Script Info]\n\n[V4+ Styles]\nFormat: Name, Fontname, Alignment\nStyle: Default,Arial,2\nStyle: Sign,Arial,8\n\n"
           "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
           "Dialogue: 0,0:00:01.50,0:00:03.20,Default,,0,0,0,,Hello, world\\Nsecond\n"
           "Dialogue: 0,0:00:04.00,0:00:05.00,Sign,,0,0,0,,{\\fad(200,200)}DAMASCUS\n")
    cues = parse_subtitle_file(ass.encode(), "a.ass")["cues"]
    assert cues[0]["text"] == "Hello, world\nsecond" and cues[0]["end"] == 3.2
    assert cues[1]["text"] == "DAMASCUS" and cues[1]["position"] == "top"


def test_rejects_files_without_subtitles():
    with pytest.raises(SubtitleParseError):
        parse_subtitle_file(b"just some text", "notes.txt")


def test_language_detection_by_script():
    from app.utils.subtitle_parser import ParsedCue

    assert detect_language([ParsedCue(0, 1, "Привет, как дела?")]) == "Russian"
    assert detect_language([ParsedCue(0, 1, "Where is John?")]) == "English"


# --------------------------------------------------------------------------- alignment

def _synthetic(seed=1, n=400, scale=25 / 23.976, offset=2.4, cut_at=None, cut=0.0, noise=0.15):
    rnd = random.Random(seed)
    t, true = 5.0, []
    for _ in range(n):
        d = rnd.uniform(0.8, 4.5)
        true.append((t, t + d))
        t += d + rnd.choice([0.1, 0.3, 0.6, 1.5, 3, 8])
    duration = t + 10
    speech = [(s + 0.1, e - 0.2) for s, e in true]
    for _ in range(n // 10):  # speech nobody subtitled
        x = rnd.uniform(0, duration)
        speech.append((x, x + rnd.uniform(0.3, 2)))
    cues = []
    for i, (s, e) in enumerate(true):
        shift = cut if (cut_at is not None and i >= cut_at) else 0.0
        fs = (s - offset - shift) / scale + rnd.gauss(0, noise)
        fe = (e - offset - shift) / scale + rnd.gauss(0, noise)
        cues.append(AlignCue(fs, max(fe, fs + 0.3)))
    return true, speech, duration, cues


@pytest.mark.parametrize("kw,frame_rate", [
    ({}, "25 -> 23.976 fps"),
    ({"scale": 1.0, "offset": -7.3}, "same frame rate"),
    ({"cut_at": 200, "cut": 30.0}, "25 -> 23.976 fps"),
    ({"scale": 1.0, "offset": 1.0, "cut_at": 150, "cut": -90.0}, "same frame rate"),
])
def test_align_recovers_offset_frame_rate_and_cuts(kw, frame_rate):
    true, speech, duration, cues = _synthetic(**kw)
    result = align(cues, speech, duration)
    err = np.abs(np.array(result.starts) - np.array([s for s, _ in true]))
    assert describe(result)["frame_rate"] == frame_rate
    assert np.median(err) < 0.25
    assert (err > 1.0).sum() <= 3
    if "cut" in kw:
        assert any(abs(j - kw["cut"]) < 1.0 for _, j in result.jumps)


def test_align_ignores_a_wrong_anchor_but_follows_good_ones():
    true, speech, duration, cues = _synthetic(n=120, scale=1.0, offset=3.0, noise=0.0)
    cues[50].anchor = true[50][0] + 25.0          # a wrong match far away
    for i in (10, 11, 12):
        cues[i].anchor = true[i][0]
    result = align(cues, speech, duration)
    assert abs(result.starts[50] - true[50][0]) < 0.5 and not result.anchor_used[50]
    assert all(result.anchor_used[i] for i in (10, 11, 12))


# --------------------------------------------------------------------------- text anchors

def _segments(utterances):
    segs = []
    for k, (start, text) in enumerate(utterances, start=1):
        words, t = [], start
        for w in text.split():
            words.append(TranscribedWord(word=" " + w, start=t, end=t + 0.3))
            t += 0.35
        segs.append(RawSegment(index=k, start_time=start, end_time=words[-1].end, text=text, words=words))
    return segs


def test_same_language_anchors_find_the_words_near_the_prediction():
    segs = _segments([(10.0, "Where were you last night?"), (14.0, "I was at the station, okay?"),
                      (30.0, "You were at the station"), (40.0, "Go")])
    words = transcript_words(segs)
    texts = ["Where were you last night?", "I was at the station.", "Go!"]
    predicted = [(11.5, 13.0), (12.5, 14.5), (41.0, 41.5)]
    anchors = match_same_language(texts, predicted, words)
    assert anchors[0].start == pytest.approx(10.0) and anchors[0].source == "text"
    assert anchors[1].start == pytest.approx(14.0)          # not the later "at the station"
    assert 2 not in anchors or anchors[2].start == pytest.approx(40.0)


def test_screen_text_in_the_same_language_takes_the_title_timing():
    screens = [{"start_time": 100.0, "end_time": 103.0, "original_text": "[DAMASCUS, SYRIA]"}]
    anchors = match_screens_same_language(["Damascus, Syria"], [(95.0, 97.0)], screens)
    assert anchors[0].source == "screen" and anchors[0].start == 100.0


class FakeGemini:
    """Answers like Gemini by looking the Hebrew lines up in a table of English phrases."""

    def __init__(self, table, screen_table=None):
        self.table, self.screen_table = table, screen_table or {}
        self.calls = 0
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls += 1
        lines = dict(re.findall(r"^L(\d+): (.*)$", contents, re.M))
        phrases = re.findall(r"^(P\d+) \[[\d.]+s\] (.*)$", contents, re.M)
        screens = re.findall(r"^(S\d+) \[[\d.]+s\] (.*)$", contents, re.M)
        matches = []
        for num, text in lines.items():
            if text in self.screen_table:
                sid = next((s for s, t in screens if t == self.screen_table[text]), None)
                matches.append({"line": int(num), "kind": "screen", "screen": sid})
                continue
            want = self.table.get(text)
            hits = [pid for pid, ptext in phrases
                    if want and (want in ptext or (ptext.strip(" ,.?!") and ptext.strip(" ,.?!") in want))]
            if hits:
                matches.append({"line": int(num), "kind": "speech", "first": hits[0], "last": hits[-1]})
            else:
                matches.append({"line": int(num), "kind": "none"})
        return SimpleNamespace(text=json.dumps({"matches": matches}), usage_metadata=None)


def test_gemini_matching_turns_phrases_into_anchors():
    segs = _segments([(10.0, "Where were you last night?"), (20.0, "Nobody believes that"),
                      (25.0, "Then ask Daniel about it")])
    words = transcript_words(segs)
    texts = ["איפה היית אתמול בלילה?", "אף אחד לא מאמין לזה", "[דמשק]", "אז תשאל את דניאל"]
    table = {texts[0]: "Where were you", texts[1]: "Nobody believes", texts[3]: "Then ask Daniel"}
    screens = [{"start_time": 22.0, "end_time": 24.0, "original_text": "DAMASCUS"}]
    fake = FakeGemini(table, {texts[2]: "DAMASCUS"})
    call = SimpleNamespace(model="gemini-3.8-flash", thinking="default", flex=False)
    predicted = [(14.0, 16.0), (24.0, 26.0), (27.0, 28.0), (29.0, 31.0)]
    anchors, stats = asyncio.run(match_with_gemini(fake, call, texts, predicted, words, screens, "Hebrew", "English"))
    assert anchors[0].start == pytest.approx(10.0) and "Where were you" in anchors[0].transcript
    assert anchors[1].start == pytest.approx(20.0)
    assert anchors[2].source == "screen" and anchors[2].start == 22.0
    assert anchors[3].start == pytest.approx(25.0)
    assert stats["speech"] == 3 and stats["screen"] == 1 and fake.calls == 1


def test_phrases_split_at_pauses_and_punctuation():
    words = transcript_words(_segments([(0.0, "Hello there, my old friend how are you today my dear"), (9.0, "Bye")]))
    phrases = build_phrases(words)
    assert phrases[0].text == "Hello there,"
    assert phrases[-1].text == "Bye" and all(len(p.text.split()) <= 7 for p in phrases)


# --------------------------------------------------------------------------- burned-in subtitles

def _frames_with_text(n, on):
    """Synthetic subtitle band frames: noisy background, white 'letters' with a black outline."""
    rng = np.random.default_rng(0)
    frames = rng.integers(90, 150, size=(n, HEIGHT, WIDTH), dtype=np.uint8)
    for i in range(n):
        for (a, b, word) in on:
            if a <= i < b:
                x = 200 + 7 * word
                for k in range(word * 2 + 4):
                    x0 = x + k * 9
                    frames[i, 40:62, x0 - 2:x0 + 6] = 10      # outline
                    frames[i, 42:60, x0:x0 + 4] = 240         # stroke
    return frames


def test_burned_in_analyzer_finds_intervals_and_text_changes():
    an = BurnedInAnalyzer(fps=5.0)
    frames = _frames_with_text(120, [(10, 25, 3), (25, 40, 6), (70, 90, 1)])  # back-to-back lines, then a gap
    an.feed(frames[:50])
    an.feed(frames[50:])  # chunk boundary in the middle must not matter
    got = an.intervals()
    assert [(round(a, 1), round(b, 1)) for a, b in got] == [(1.9, 4.9), (4.9, 7.9), (13.9, 17.9)]


def test_burned_in_validation_needs_speech_overlap():
    intervals = [(i * 10.0, i * 10.0 + 2.0) for i in range(20)]
    speech = [(i * 10.0 + 0.2, i * 10.0 + 1.8) for i in range(20)]
    assert validate_against_speech(intervals, speech, 200.0, 20)[0]
    assert not validate_against_speech(intervals, [(500.0, 600.0)], 700.0, 20)[0]
    assert not validate_against_speech(intervals[:3], speech, 200.0, 20)[0]


# --------------------------------------------------------------------------- end to end through the API

UTTERANCES = [
    (2.0, "Where were you last night"), (5.0, "I was at the station"), (9.0, "Nobody believes that"),
    (12.5, "Then ask Daniel"), (16.0, "We leave at dawn tomorrow"), (20.0, "Bring the radio with you"),
    (24.0, "Did anyone see the car"), (28.0, "It was parked outside the gate"), (32.0, "Keep your voice down"),
    (35.5, "They can hear us from here"), (39.0, "Fine, I am going"), (43.0, "Wait for me at the bridge"),
]
HEBREW = {
    "Where were you last night": "איפה היית אתמול בלילה", "I was at the station": "הייתי בתחנה",
    "Nobody believes that": "אף אחד לא מאמין לזה", "Then ask Daniel": "אז תשאל את דניאל",
    "We leave at dawn tomorrow": "אנחנו יוצאים מחר עם שחר", "Bring the radio with you": "תביא איתך את הקשר",
    "Did anyone see the car": "מישהו ראה את המכונית", "It was parked outside the gate": "היא חנתה מחוץ לשער",
    "Keep your voice down": "תנמיך את הקול", "They can hear us from here": "הם שומעים אותנו מכאן",
    "Fine, I am going": "בסדר, אני הולך", "Wait for me at the bridge": "חכה לי ליד הגשר",
}


class FakeWhisper:
    detected_language = "en"

    async def transcribe(self, audio_path, language=None, on_segment_callback=None, cancel_flag=None):
        segs = _segments(UTTERANCES)
        for s in segs:
            if on_segment_callback:
                await on_segment_callback(s, s.index * 8.0)
        return segs


def _true_spans():
    return [(s.start_time, s.end_time) for s in _segments(UTTERANCES)]


def _run_sync_job(tmp_path, monkeypatch, cues, language, gemini=None):
    from fastapi.testclient import TestClient

    import app.pipeline.orchestrator as orch
    import app.pipeline.speech_regions as regions_mod
    from app.main import app
    from app.pipeline.stages.sync_stage import SyncStage
    from app.pipeline.stages.transcribe_stage import TranscribeStage

    clip = tmp_path / f"clip_{uuid.uuid4().hex[:6]}.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=48",
                    "-f", "lavfi", "-i", "color=c=gray:s=320x180:d=48", "-shortest", str(clip)], check=True)
    monkeypatch.setattr(orch, "TranscribeStage", lambda: TranscribeStage(FakeWhisper()))
    monkeypatch.setattr(regions_mod, "detect_regions", lambda wav: _true_spans())  # VAD on a sine tone hears nothing
    monkeypatch.setattr("app.core.config.settings.whisper_gap_recovery", False)
    if gemini is not None:
        monkeypatch.setattr(orch, "SyncStage", lambda: SyncStage(client_factory=lambda: gemini))
    with TestClient(app) as client:
        job = client.post("/api/jobs", json={
            "video_path": str(clip), "sync_cues": cues, "sync_file_name": "show.srt", "sync_language": language,
        }).json()
        assert job["job_kind"] == "sync", job
        deadline = time.time() + 60
        while time.time() < deadline:
            done = client.get(f"/api/jobs/{job['id']}").json()
            if done["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.3)
        translated = done.get("segments") and done["segments"][0].get("translated_text")
        return done, client.get(f"/api/jobs/{job['id']}/export/srt?use_translated={'true' if translated else 'false'}")


ffmpeg_only = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@ffmpeg_only
def test_sync_job_same_language(tmp_path, monkeypatch):
    # The file is 3.2 s late and its lines are a little off
    cues = [{"start": s + 3.2 + (0.2 if i % 2 else -0.1), "end": e + 3.6, "text": t}
            for i, ((s, e), (_, t)) in enumerate(zip(_true_spans(), UTTERANCES))]
    done, export = _run_sync_job(tmp_path, monkeypatch, cues, "English")
    assert done["status"] == "COMPLETED", done.get("error_message")
    segs = done["segments"]
    assert [s["original_text"] for s in segs] == [t for _, t in UTTERANCES]
    assert all(s["translated_text"] is None for s in segs)
    for seg, (start, _) in zip(segs, _true_spans()):
        assert abs(seg["start_time"] - (start - 0.1)) < 0.15
    report = done["sync_report"]
    assert report["methods"]["text"] == len(UTTERANCES) and report["matching"] == "text"
    assert ".en.srt" in export.headers["content-disposition"]


@ffmpeg_only
def test_sync_job_hebrew_file_with_gemini(tmp_path, monkeypatch):
    cues = [{"start": s - 4.0, "end": e - 3.5, "text": HEBREW[t]} for (s, e), (_, t) in zip(_true_spans(), UTTERANCES)]
    table = {HEBREW[t]: t for _, t in UTTERANCES}
    fake = FakeGemini(table)
    done, export = _run_sync_job(tmp_path, monkeypatch, cues, "Hebrew", gemini=fake)
    assert done["status"] == "COMPLETED", done.get("error_message")
    assert fake.calls >= 1
    segs = done["segments"]
    assert [s["translated_text"] for s in segs] == [HEBREW[t] for _, t in UTTERANCES]
    assert segs[0]["original_text"].startswith("Where were you")   # what is said, next to the translation
    for seg, (start, _) in zip(segs, _true_spans()):
        assert abs(seg["start_time"] - (start - 0.1)) < 0.15
    assert done["target_language"] == "Hebrew"
    assert done["sync_report"]["methods"]["gemini"] == len(UTTERANCES)
    assert ".he.srt" in export.headers["content-disposition"]


@ffmpeg_only
def test_parse_subtitles_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        ok = client.post("/api/media/parse-subtitles", files={"file": ("show.srt", SRT.encode("cp1255"))})
        assert ok.status_code == 200 and ok.json()["cue_count"] == 3 and ok.json()["encoding"] == "windows-1255"
        bad = client.post("/api/media/parse-subtitles", files={"file": ("x.srt", b"nothing here")})
        assert bad.status_code == 400


def _drawtext_clip(path, lines, duration):
    """A clip with these lines burned in; None when this ffmpeg has no usable drawtext/font."""
    def esc(t):
        return t.replace("'", "").replace(",", r"\,").replace(":", r"\:")
    draws = ",".join(
        f"drawtext=text='{esc(t)}':fontcolor=white:fontsize=40:borderw=3:bordercolor=black:"
        f"x=(w-text_w)/2:y=h-80:enable='between(t\\,{a:.2f}\\,{b:.2f})'" for (a, b), t in lines)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=960x540:rate=24:d={duration}",
           "-f", "lavfi", "-i", f"sine=frequency=300:duration={duration}", "-vf", draws, "-shortest",
           "-c:v", "libx264", "-preset", "ultrafast", str(path)]
    return path if subprocess.run(cmd, capture_output=True).returncode == 0 else None


@ffmpeg_only
def test_sync_job_uses_burned_in_subtitles(tmp_path, monkeypatch):
    """Hebrew file for a 25 fps release, no Gemini: the burned-in English subtitles give the timing."""
    from fastapi.testclient import TestClient

    import app.pipeline.orchestrator as orch
    import app.pipeline.speech_regions as regions_mod
    from app.main import app
    from app.pipeline.stages.transcribe_stage import TranscribeStage

    shown = [((s - 0.1, e + 0.5), t) for (s, e), (_, t) in zip(_true_spans(), UTTERANCES)]
    clip = _drawtext_clip(tmp_path / "burned.mp4", shown, 48)
    if clip is None:
        pytest.skip("ffmpeg drawtext (or a default font) is not available")
    monkeypatch.setattr(orch, "TranscribeStage", lambda: TranscribeStage(FakeWhisper()))
    monkeypatch.setattr(regions_mod, "detect_regions", lambda wav: _true_spans())
    monkeypatch.setattr("app.core.config.settings.whisper_gap_recovery", False)
    scale = 25 / 23.976
    cues = [{"start": (a - 1.5) / scale, "end": (b - 1.5) / scale, "text": HEBREW[t]} for (a, b), t in shown]
    with TestClient(app) as client:
        job = client.post("/api/jobs", json={"video_path": str(clip), "sync_cues": cues, "sync_language": "Hebrew",
                                             "sync_use_gemini": False}).json()
        deadline = time.time() + 90
        while time.time() < deadline:
            done = client.get(f"/api/jobs/{job['id']}").json()
            if done["status"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.3)
    assert done["status"] == "COMPLETED", done.get("error_message")
    report = done["sync_report"]
    assert report["reference"]["source"] == "burned_in", report
    assert report["methods"].get("burned_in", 0) >= len(UTTERANCES) - 1
    for seg, ((a, b), _) in zip(done["segments"], shown):
        assert abs(seg["start_time"] - a) < 0.25 and abs(seg["end_time"] - b) < 0.3
