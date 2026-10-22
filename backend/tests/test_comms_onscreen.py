"""Tactical Comms Boost and On-Screen Text (forced narratives) features."""
import asyncio
import json
import shutil
import subprocess
import time
import types
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.pipeline.orchestrator as orch
import app.providers.transcription.faster_whisper as fw
from app.main import app
from app.models.schemas import SubtitleStreamInfo
from app.pipeline.context import PipelineContext, RawSegment, TranscribedWord
from app.pipeline.stages.audio_stage import COMMS_AUDIO_FILTER, build_audio_filter, extract_audio_track
from app.pipeline.stages.ocr_stage import (
    Frame,
    FrameDetection,
    OnScreenTextStage,
    clean_embedded_dialogue_text,
    format_onscreen_text,
    get_vision_system_instruction,
    merge_frame_detections,
    parse_srt,
    pick_forced_track,
    select_frames,
    _is_tag,
    _is_unwanted_onscreen,
)
from app.pipeline.stages.refine_stage import classify_comms, is_comms_text, merge_timeline
from app.pipeline.stages.transcribe_stage import TranscribeStage
from app.providers.transcription.base import BaseTranscriber
from app.providers.translation.base import TranslatableSegment
from app.utils.rtl_fixer import RLM, RLE, PDF, apply_rtl_fix
from app.utils.srt_formatter import serialize_to_srt

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _spoken(index, start, end, text, kind="DIALOGUE"):
    return {"index": index, "start_time": start, "end_time": end, "original_text": text,
            "translated_text": None, "segment_type": kind, "confidence": 1.0, "cps": 0}


def _onscreen(start, end, text):
    return {"start_time": start, "end_time": end, "original_text": text, "translated_text": None,
            "segment_type": "ON_SCREEN", "confidence": 1.0}


# ---------------------------------------------------------------------------
# Timeline merging (refine_stage)
# ---------------------------------------------------------------------------

def test_onscreen_in_silence_becomes_its_own_entry_and_reindexes():
    spoken = [_spoken(1, 10.0, 12.0, "First line."), _spoken(2, 20.0, 22.0, "Second line.")]
    merged = merge_timeline(spoken, [_onscreen(14.0, 17.5, "[One week later...]")])
    assert [m["original_text"] for m in merged] == ["First line.", "[One week later...]", "Second line."]
    assert [m["index"] for m in merged] == [1, 2, 3]
    assert merged[1]["segment_type"] == "ON_SCREEN"


def test_minor_overlap_is_trimmed_to_a_clean_separate_entry():
    spoken = [_spoken(1, 10.0, 12.0, "We move at dawn.")]
    # caption starts 0.5s before the dialogue ends and runs well after it
    merged = merge_timeline(spoken, [_onscreen(11.5, 16.0, "[Tehran, Iran]")], min_gap=0.084)
    caption = merged[1]
    assert caption["original_text"] == "[Tehran, Iran]"
    assert caption["start_time"] >= 12.0 + 0.08
    assert caption["end_time"] == 16.0
    assert merged[0]["original_text"] == "We move at dawn."


def test_simultaneous_caption_stays_separate_onscreen_entry():
    spoken = [_spoken(1, 30.0, 33.0, "Get everyone inside.")]
    merged = merge_timeline(spoken, [_onscreen(29.8, 33.2, "[Damascus, Syria]")])
    assert len(merged) == 2
    assert merged[0]["original_text"] == "[Damascus, Syria]"
    assert merged[0]["segment_type"] == "ON_SCREEN"
    assert merged[1]["original_text"] == "Get everyone inside."
    assert merged[1]["segment_type"] == "DIALOGUE"


def test_caption_spanning_several_lines_stays_single_onscreen_entry():
    spoken = [_spoken(1, 5.0, 6.5, "Move."), _spoken(2, 6.6, 8.0, "Now!")]
    merged = merge_timeline(spoken, [_onscreen(5.0, 8.0, "[0400 HOURS]")])
    assert len(merged) == 3
    assert merged[0]["segment_type"] == "ON_SCREEN"
    assert merged[0]["original_text"] == "[0400 HOURS]"
    assert [m["original_text"] for m in merged[1:]] == ["Move.", "Now!"]


# ---------------------------------------------------------------------------
# OCR frame de-duplication
# ---------------------------------------------------------------------------

def test_consecutive_frames_with_same_text_merge_into_one_segment():
    dets = [
        FrameDetection(100.0, "One week later..."),
        FrameDetection(102.0, "ONE WEEK LATER"),       # OCR casing/punctuation variation
        FrameDetection(104.0, "One week later..."),
        FrameDetection(140.0, "Damascus, Syria"),
    ]
    segs = merge_frame_detections(dets, frame_interval=2.0)
    assert len(segs) == 2
    first, second = segs
    assert first["original_text"] == "[One week later...]"
    assert first["start_time"] == 100.0 and first["end_time"] == 105.0  # last seen + 1.0s
    assert first["segment_type"] == "ON_SCREEN"
    assert second["original_text"] == "[Damascus, Syria]"
    assert second["end_time"] - second["start_time"] == pytest.approx(1.5)  # minimum duration


def test_static_run_extends_span_and_far_repeats_stay_separate():
    dets = [FrameDetection(10.0, "Sector 7", until=16.0), FrameDetection(60.0, "Sector 7")]
    segs = merge_frame_detections(dets, frame_interval=2.0)
    assert len(segs) == 2
    assert segs[0]["end_time"] == 17.0


def test_format_onscreen_text():
    assert format_onscreen_text("DAMASCUS, SYRIA\n36 HOURS EARLIER") == "[DAMASCUS, SYRIA — 36 HOURS EARLIER]"
    assert format_onscreen_text("[Already bracketed]") == "[Already bracketed]"
    assert format_onscreen_text("   ") == ""


def test_filters_random_documents_and_show_title_but_keeps_messages_and_titles():
    dets = [
        FrameDetection(10.0, "THE BLACK DAGGER BROTHERHOOD"),
        FrameDetection(20.0, "CALWELL POLICE DEPARTMENT ARREST REPORT"),
        FrameDetection(30.0, "Summer 2018"),
        FrameDetection(40.0, "Meet me at the warehouse at midnight."),
        FrameDetection(50.0, "DR. JAN FRANCKE\nINTERNATIONAL GPR EXPERT"),
    ]
    segs = merge_frame_detections(
        dets,
        frame_interval=2.0,
        drop_tags=True,
        file_name="The.Black.Dagger.Brotherhood.S01E01.1080p.mkv",
    )
    assert [s["original_text"] for s in segs] == [
        "[Summer 2018]",
        "[Meet me at the warehouse at midnight.]",
        "[DR. JAN FRANCKE — INTERNATIONAL GPR EXPERT]",
    ]


def test_onscreen_sensitivity_prompts_and_filters():
    # Prompt helper
    s1_prompt = get_vision_system_instruction(1)
    s2_prompt = get_vision_system_instruction(2)
    s3_prompt = get_vision_system_instruction(3)
    assert "Strictly IGNORE" in s1_prompt
    assert "Plot-critical signs" in s2_prompt
    assert "Signs, plaques & tactical HUD" in s3_prompt

    # _is_tag checks
    # Level 1 drops single uppercase words up to 12 chars
    assert _is_tag("PENTAGON", sensitivity=1) is True
    assert _is_tag("LIVE", sensitivity=1) is True
    # Level 2 only drops tags <= 5 chars, keeps meaningful 1-word signs like PENTAGON
    assert _is_tag("LIVE", sensitivity=2) is True
    assert _is_tag("REC", sensitivity=2) is True
    assert _is_tag("PENTAGON", sensitivity=2) is False
    assert _is_tag("RESTRICTED", sensitivity=2) is False
    # Level 3 keeps all single word signs
    assert _is_tag("PENTAGON", sensitivity=3) is False
    assert _is_tag("CLASSIFIED", sensitivity=3) is False
    assert _is_tag("LIVE", sensitivity=3) is False

    # _is_unwanted_onscreen checks
    police_report = "CALWELL POLICE DEPARTMENT ARREST REPORT"
    sms_20_words = "Please make sure you arrive at the safe house before dusk because the patrol cars will be circling the area."
    assert len(sms_20_words.split()) == 20

    # Level 1 (Strict): rejects police report, 20-word SMS (len > 14 words), and document kinds
    assert _is_unwanted_onscreen(police_report, sensitivity=1) is True
    assert _is_unwanted_onscreen(sms_20_words, sensitivity=1) is True
    assert _is_unwanted_onscreen("Safe house note", kind="document", sensitivity=1) is True

    # Level 2 (Balanced): rejects police report (boilerplate), keeps 20-word SMS (up to 26 words), keeps signs/docs
    assert _is_unwanted_onscreen(police_report, sensitivity=2) is True
    assert _is_unwanted_onscreen(sms_20_words, sensitivity=2) is False
    assert _is_unwanted_onscreen("Safe house entrance", kind="sign", sensitivity=2) is False

    # Level 3 (Broad): keeps police report (no boilerplate filter), keeps 20-word SMS
    assert _is_unwanted_onscreen(police_report, sensitivity=3) is False
    assert _is_unwanted_onscreen(sms_20_words, sensitivity=3) is False

    # Series stem normalization filters main title card across all levels
    show_title = "THE BLACK DAGGER BROTHERHOOD"
    file_name = "The.Black.Dagger.Brotherhood.S01E01.1080p.mkv"
    assert _is_unwanted_onscreen(show_title, file_name=file_name, sensitivity=1) is True
    assert _is_unwanted_onscreen(show_title, file_name=file_name, sensitivity=2) is True
    assert _is_unwanted_onscreen(show_title, file_name=file_name, sensitivity=3) is True


def test_merge_frame_detections_with_sensitivity():
    dets = [
        FrameDetection(10.0, "PENTAGON"),
        FrameDetection(20.0, "CALWELL POLICE DEPARTMENT ARREST REPORT"),
        FrameDetection(30.0, "Please make sure you arrive at the safe house before dusk because the patrol cars will be circling the area."),
        FrameDetection(40.0, "Summer 2018"),
    ]
    # Sensitivity 1: drops PENTAGON, police report, and 20-word message -> keeps only Summer 2018
    s1 = merge_frame_detections(dets, drop_tags=True, sensitivity=1)
    assert [s["original_text"] for s in s1] == ["[Summer 2018]"]

    # Sensitivity 2: keeps PENTAGON, 20-word message, and Summer 2018 -> drops police report
    s2 = merge_frame_detections(dets, drop_tags=True, sensitivity=2)
    assert [s["original_text"] for s in s2] == [
        "[PENTAGON]",
        "[Please make sure you arrive at the safe house before dusk because the patrol cars will be circling the area.]",
        "[Summer 2018]",
    ]

    # Sensitivity 3: keeps all of them including police report
    s3 = merge_frame_detections(dets, drop_tags=True, sensitivity=3)
    assert len(s3) == 4
    assert s3[1]["original_text"] == "[CALWELL POLICE DEPARTMENT ARREST REPORT]"


def test_clean_embedded_dialogue_inline_whispering():
    assert clean_embedded_dialogue_text("(whispering) Run!") == "Run!"
    assert clean_embedded_dialogue_text("[whispering] Run!", is_sdh=True) == "Run!"
    assert clean_embedded_dialogue_text("(whispers) Take cover.") == "Take cover."
    assert clean_embedded_dialogue_text("(whispering)") == ""  # pure sound cue dropped


def test_select_frames_prefers_gaps_and_cuts_and_skips_static_repeats():
    frames = [Frame(t, Path(f"{t}.jpg"), change=c) for t, c in [
        (0, 1.0), (2, 0.01), (4, 0.005), (6, 0.3), (8, 0.02), (10, 0.05), (12, 0.4)]]
    speech = [(5.0, 11.0)]
    chosen = [f.time for f in select_frames(frames, speech, budget=100)]
    assert 2 not in chosen and 4 not in chosen   # static repeats of frame 0
    assert 0 in chosen and 12 in chosen          # gaps
    assert 6 in chosen and 8 in chosen           # cut during speech + the frame after it
    assert 10 not in chosen                      # in speech, no cut
    assert [f.time for f in select_frames(frames, speech, budget=2)] == [0, 12]


def test_select_frames_reserves_budget_for_speech_chyrons():
    # Many silent non-cut frames (priority 1) must not starve speech cuts/chyrons (priority 2)
    frames = []
    # 15 non-cut frames inside a silent gap (t=0..28)
    frames.append(Frame(0.0, Path("0.jpg"), change=1.0))
    for k in range(1, 15):
        frames.append(Frame(float(k * 2), Path(f"{k*2}.jpg"), change=0.05))
    # 10 frames during speech (t=30..48), with a chyron cut at t=36
    speech = [(29.0, 50.0)]
    for k in range(15, 25):
        t = float(k * 2)
        c = 0.25 if t == 36.0 else 0.05
        frames.append(Frame(t, Path(f"{int(t)}.jpg"), change=c))
    chosen_times = [f.time for f in select_frames(frames, speech, budget=10)]
    assert 36.0 in chosen_times  # speech cut / chyron frame is kept despite many gap frames


def test_partial_typewriter_reveals_merge_into_complete_title():
    dets = [
        FrameDetection(382.0, "THE MESA DRI"),
        FrameDetection(384.0, "THE MESA DRILL SITE"),
        FrameDetection(500.0, "BARD\nIGATOR"),
        FrameDetection(502.0, "ERIK BARD\nPRINCIPAL INVESTIGATOR"),
    ]
    segs = merge_frame_detections(dets, frame_interval=2.0)
    assert [s["original_text"] for s in segs] == [
        "[THE MESA DRILL SITE]",
        "[ERIK BARD — PRINCIPAL INVESTIGATOR]",
    ]
    assert segs[0]["start_time"] == 382.0 and segs[0]["end_time"] == 385.0
    assert segs[1]["start_time"] == 500.0 and segs[1]["end_time"] == 503.0


# ---------------------------------------------------------------------------
# RTL handling of bracketed Hebrew titles
# ---------------------------------------------------------------------------

def test_rtl_fixer_wraps_bracketed_hebrew_title():
    title = "[שבוע אחד לאחר מכן...]"
    fixed = apply_rtl_fix(title)
    assert fixed == f"{RLE}{RLM}{title}{RLM}{PDF}"
    assert apply_rtl_fix(fixed) == fixed  # idempotent


def test_rtl_export_of_title_above_dialogue():
    seg = {"start_time": 1.0, "end_time": 3.0, "original_text": "x",
           "translated_text": "[דמשק, סוריה]\n- קיבלתי, עבור."}
    srt = serialize_to_srt([seg], use_translated=True, apply_rtl=True)
    assert f"{{\\an8}}{RLE}{RLM}[דמשק, סוריה]{RLM}{PDF}" in srt
    assert f"{RLE}{RLM}- קיבלתי, עבור.{RLM}{PDF}" in srt


# ---------------------------------------------------------------------------
# Forced subtitle tracks
# ---------------------------------------------------------------------------

def test_parse_srt_strips_tags():
    srt = "1\n00:00:05,000 --> 00:00:07,500\n<i>One week later</i>\n\n2\n00:01:00,100 --> 00:01:02,000\n{\\an8}Tehran\n"
    cues = parse_srt(srt)
    assert cues == [
        {"start_time": 5.0, "end_time": 7.5, "text": "One week later"},
        {"start_time": 60.1, "end_time": 62.0, "text": "Tehran"},
    ]


def test_pick_forced_track():
    streams = [
        SubtitleStreamInfo(index=0, codec_name="subrip", language="eng", title="Full"),
        SubtitleStreamInfo(index=1, codec_name="hdmv_pgs_subtitle", language="eng", forced=True, text_based=False),
        SubtitleStreamInfo(index=2, codec_name="subrip", language="eng", title="English (Signs & Songs)"),
        SubtitleStreamInfo(index=3, codec_name="ass", language="eng", forced=True),
    ]
    assert pick_forced_track(streams).index == 3
    assert pick_forced_track(streams[:3]).index == 2
    assert pick_forced_track(streams[:2]) is None  # image-based forced track can't be parsed


# ---------------------------------------------------------------------------
# Tactical Comms Boost
# ---------------------------------------------------------------------------

def test_comms_filter_chain():
    assert build_audio_filter(False, False) is None
    assert build_audio_filter(False, True) == COMMS_AUDIO_FILTER
    assert build_audio_filter(True, True).startswith("pan=mono|") and build_audio_filter(True, True).endswith(COMMS_AUDIO_FILTER)
    assert "highpass=f=180" in COMMS_AUDIO_FILTER and "dynaudnorm" in COMMS_AUDIO_FILTER


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.asyncio
async def test_ffmpeg_accepts_comms_chain_on_surround_audio(tmp_path):
    src = tmp_path / "surround.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=800:duration=2",
                    "-ac", "6", "-channel_layout", "5.1", str(src)], check=True)
    out = tmp_path / "out.wav"
    await extract_audio_track(str(src), str(out), 0, channels=6, duration=2.0, comms_boost=True)
    assert out.stat().st_size > 30000  # ~2s of 16kHz mono PCM


def test_comms_classification():
    assert is_comms_text("Dr. Scott, come in.")
    assert is_comms_text("Bravo team, do you copy? Over.")
    assert is_comms_text("This is Nathan James actual.")
    assert not is_comms_text("It's over now.")
    assert not is_comms_text("Come in and sit down.")
    segs = [_spoken(1, 0, 1, "Roger that."), _spoken(2, 1, 2, "Hello."), _onscreen(3, 4, "[Over.]")]
    assert classify_comms(segs) == 1
    assert [s["segment_type"] for s in segs] == ["COMMS", "DIALOGUE", "ON_SCREEN"]


def test_comms_prompt_rules():
    p = fw.build_comms_prompt("Navy destroyer thriller. XO is Mike Slattery.", "auto")
    assert p.startswith("Navy destroyer thriller") and p.endswith(fw.TACTICAL_PRIMER)
    assert fw.build_comms_prompt("שרה מדברת בלשון נקבה", None) == fw.TACTICAL_PRIMER  # RTL notes excluded
    assert fw.build_comms_prompt(None, "ru") is None  # English primer only for English/unknown audio


class _FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        seg = types.SimpleNamespace(start=0.0, end=1.0, text=" Copy that.", avg_logprob=-0.2,
                                    words=[types.SimpleNamespace(word=" Copy", start=0.0, end=0.4, probability=0.9)])
        return iter([seg]), types.SimpleNamespace(duration=1.0, language="en")


@pytest.mark.asyncio
async def test_whisper_comms_decoding_parameters(monkeypatch):
    model = _FakeModel()
    monkeypatch.setattr(fw, "_load_model", lambda *a: model)
    monkeypatch.setattr(fw, "get_hardware_info", lambda: {"cuda_available": False})
    t = fw.FasterWhisperTranscriber("tiny", device="cpu", vad_filter=False, comms_boost=True,
                                    context_notes="Submarine hunt.")
    await t.transcribe("a.wav")
    kw = model.calls[0]
    assert kw["vad_filter"] is True
    assert kw["vad_parameters"] == {"threshold": 0.28, "min_speech_duration_ms": 120,
                                    "min_silence_duration_ms": 400, "speech_pad_ms": 450}
    assert kw["no_speech_threshold"] == 0.88 and kw["log_prob_threshold"] == -1.25
    assert kw["condition_on_previous_text"] is False
    assert kw["initial_prompt"].startswith("Submarine hunt.") and "walkie-talkie" in kw["initial_prompt"]
    assert "sitrep" in kw["hotwords"]

    plain = _FakeModel()
    monkeypatch.setattr(fw, "_load_model", lambda *a: plain)
    await fw.FasterWhisperTranscriber("tiny", device="cpu").transcribe("a.wav")
    assert "vad_parameters" not in plain.calls[0] and "initial_prompt" not in plain.calls[0]


# ---------------------------------------------------------------------------
# Gemini translator payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_translator_sends_segment_type(monkeypatch):
    import app.providers.translation.gemini_provider as gp

    seen = []

    def generate_content(model, contents, config):
        payload = json.loads(contents)
        seen.append((payload, config.system_instruction))
        return types.SimpleNamespace(text=json.dumps({"translations": [
            {"index": i["index"], "translated_text": "ת"} for i in payload["target_batch"]]}))

    t = gp.GeminiTranslator(api_key="k")
    t.client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate_content))
    segs = [TranslatableSegment(1, 0, 2, "[One week later...]", segment_type="ON_SCREEN"),
            TranslatableSegment(2, 3, 5, "Copy that, over.", segment_type="COMMS")]
    await t.translate_segments(segs, "English", "Hebrew")
    payload, prompt = seen[0]
    assert [i["segment_type"] for i in payload["target_batch"]] == ["ON_SCREEN", "COMMS"]
    assert "ON_SCREEN" in prompt and "רות" in prompt and "עבור" in prompt


# ---------------------------------------------------------------------------
# End-to-end with real ffmpeg (Whisper and Gemini faked)
# ---------------------------------------------------------------------------

class _FakeWhisper(BaseTranscriber):
    def __init__(self, lines):
        self.lines = lines

    async def transcribe(self, audio_path, language=None, on_segment_callback=None, cancel_flag=None):
        out = []
        for i, (start, text) in enumerate(self.lines, start=1):
            words, t = [], start
            for w in text.split():
                words.append(TranscribedWord(word=" " + w, start=t, end=t + 0.25))
                t += 0.3
            seg = RawSegment(index=i, start_time=start, end_time=words[-1].end, text=text, words=words)
            out.append(seg)
            if on_segment_callback:
                await on_segment_callback(seg, 50.0)
        self.detected_language = "en"
        return out


def _run_job(client, video, **config):
    job = client.post("/api/jobs", json={"video_path": str(video), "enable_translation": False, **config}).json()
    deadline = time.time() + 30
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job['id']}").json()
        if data["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return data
        time.sleep(0.1)
    raise AssertionError("job did not finish")


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_e2e_forced_track_becomes_onscreen_segments(tmp_path, monkeypatch):
    srt = tmp_path / "forced.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nOne week later...\n\n"
                   "2\n00:00:05,000 --> 00:00:07,000\nTehran, Iran\n", encoding="utf-8")
    video = tmp_path / f"clip_{uuid.uuid4().hex[:6]}.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=8",
                    "-i", str(srt), "-map", "0", "-map", "1", "-map", "2",
                    "-c:s", "srt", "-disposition:s:0", "forced", "-shortest", str(video)], check=True)
    monkeypatch.setattr(orch, "TranscribeStage",
                        lambda: TranscribeStage(_FakeWhisper([(3.0, "Copy that, over."), (5.2, "Move out now.")])))

    with TestClient(app) as client:
        probe = client.post("/api/media/probe-path", json={"file_path": str(video)}).json()
        assert probe["subtitle_streams"][0]["forced"] is True
        job = _run_job(client, video, detect_onscreen_text=True, comms_boost=True)

    assert job["status"] == "COMPLETED", job.get("error_message")
    segs = job["segments"]
    texts = [(s["segment_type"], s["original_text"]) for s in segs]
    assert texts[0] == ("ON_SCREEN", "One week later...")
    assert ("COMMS", "Copy that, over.") in texts
    assert ("ON_SCREEN", "Tehran, Iran") in texts
    assert ("DIALOGUE", "Move out now.") in texts
    assert [s["index"] for s in segs] == list(range(1, len(segs) + 1))
    assert job["comms_boost"] is True and job["detect_onscreen_text"] is True


class _FakeVision:
    """Stands in for google.genai.Client: 'sees' a title card between t=10s and t=14s."""

    def __init__(self):
        self.requests = 0
        self.models = self

    def generate_content(self, model, contents, config):
        self.requests += 1
        stamps = [float(c.split("t=")[1].rstrip("s:")) for c in contents if isinstance(c, str) and c.startswith("Frame at")]
        hits = [{"timestamp": t, "text": "One week later...", "kind": "time_jump"} for t in stamps if 10 <= t <= 14]
        return types.SimpleNamespace(text=json.dumps({"detections": hits}))


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_e2e_frame_scan_with_fake_gemini_vision(tmp_path, monkeypatch):
    video = tmp_path / f"scan_{uuid.uuid4().hex[:6]}.mp4"
    # 0-10s red (dialogue), 10-15s white card (silence), 15-20s blue (dialogue)
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=red:s=160x90:d=10",
                    "-f", "lavfi", "-i", "color=c=white:s=160x90:d=5",
                    "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=5",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=20",
                    "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                    "-map", "[v]", "-map", "3:a", "-shortest", str(video)], check=True)
    vision = _FakeVision()
    monkeypatch.setattr(orch, "TranscribeStage",
                        lambda: TranscribeStage(_FakeWhisper([(1.0, "We hold here."), (16.0, "Let's go.")])))
    monkeypatch.setattr(orch, "OnScreenTextStage", type("S", (OnScreenTextStage,), {
        "__init__": lambda self: OnScreenTextStage.__init__(self, client_factory=lambda: vision)}))

    with TestClient(app) as client:
        job = _run_job(client, video, detect_onscreen_text=True)

    assert job["status"] == "COMPLETED", job.get("error_message")
    onscreen = [s for s in job["segments"] if s["segment_type"] == "ON_SCREEN"]
    assert len(onscreen) == 1
    seg = onscreen[0]
    assert seg["original_text"] == "[One week later...]"
    assert 9.9 <= seg["start_time"] <= 10.1 and seg["end_time"] >= 15.0
    assert vision.requests >= 1
    from app.core.config import settings
    assert not (settings.storage_dir / "frames" / job["id"]).exists()  # temp frames cleaned up


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_unavailable_vision_model_stops_early_with_clear_warning(tmp_path, monkeypatch):
    video = tmp_path / f"v_{uuid.uuid4().hex[:6]}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=160x90:d=30",
                    "-f", "lavfi", "-i", "sine=duration=30", "-shortest", str(video)], check=True)

    class _Denied:
        def __init__(self):
            self.models = self
            self.calls = 0

        def generate_content(self, model, contents, config):
            self.calls += 1
            raise Exception("404 NOT_FOUND. models/gemini-2.5-flash is not available to new users.")

    denied = _Denied()
    monkeypatch.setattr(orch, "TranscribeStage", lambda: TranscribeStage(_FakeWhisper([(1.0, "Hello there.")])))
    monkeypatch.setattr(orch, "OnScreenTextStage", type("S", (OnScreenTextStage,), {
        "__init__": lambda self: OnScreenTextStage.__init__(self, client_factory=lambda: denied)}))
    with TestClient(app) as client:
        job = _run_job(client, video, detect_onscreen_text=True)
    assert job["status"] == "COMPLETED"
    assert "not available to new users" in job["warning_message"]
    assert denied.calls <= 3  # stopped at the first refusal (one per concurrent batch at most)
