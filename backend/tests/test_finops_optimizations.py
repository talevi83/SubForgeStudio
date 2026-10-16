"""FinOps cost optimization tests (Steps 1-4)."""
import copy
import json
import types
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import app.providers.transcription.faster_whisper as fw
from app.core.config import Settings, settings
from app.core.user_settings import load_settings, save_settings
from app.main import app
from app.pipeline.context import PipelineContext, RawSegment
from app.pipeline.gender_pretag import (
    find_ambiguous_segments,
    format_hints_for_context_notes,
    pick_ollama_model,
    pretag_gender_hints,
)
from app.pipeline.onscreen_prefilter import prefilter_frames
from app.pipeline.stages.ocr_stage import Frame, OnScreenTextStage
from app.pipeline.stages.transcribe_stage import TranscribeStage
from app.providers.transcription.base import BaseTranscriber
from app.providers.translation.base import TranslatableSegment
from app.providers.translation.gemini_provider import GeminiTranslator
from app.providers.translation.glossary_pruner import prune_glossary_for_batch


@pytest.fixture(autouse=True)
def _clean_settings_file():
    path = settings.storage_dir / "settings.json"
    path.unlink(missing_ok=True)
    yield
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 1. Default batch size 60 & prune_glossary_for_batch
# ---------------------------------------------------------------------------

def test_default_batch_size_is_60():
    assert Settings.model_fields["gemini_batch_size"].default == 60
    assert settings.gemini_batch_size == 60
    assert Settings.model_fields["cost_saver_mode"].default is False


def test_prune_glossary_for_batch_keeps_core_cast_and_matching_items_without_mutating():
    full_glossary = {
        "summary": "Naval sci-fi thriller",
        "characters": [
            {"name": f"MainChar{i}", "target_name": f"דמות{i}", "gender": "male", "notes": "core"}
            for i in range(1, 9)
        ] + [
            {"name": "Admiral Meylan", "target_name": "אדמירל מיילן", "gender": "male", "notes": "guest"},
            {"name": "Dr. Hunter", "target_name": "ד״ר האנטר", "gender": "female", "notes": "guest"},
        ],
        "terms": [
            {"source": "CIC", "translation": "מרכז ידיעות קרב", "notes": ""},
            {"source": "Red Flu", "translation": "השפעת האדומה", "notes": ""},
            {"source": "SONAR", "translation": "סונאר", "notes": ""},
        ],
        "register_notes": "Military tone",
    }
    snapshot = copy.deepcopy(full_glossary)

    pruned = prune_glossary_for_batch(
        glossary=full_glossary,
        batch_texts=["Report to CIC immediately.", "Yes, sir."],
        previous_context=["Meylan is waiting on the bridge."],
        lookahead_context=["Stand by."],
        max_core_characters=6,
    )

    assert pruned is not None
    # Original glossary must remain untouched
    assert full_glossary == snapshot

    char_names = [c["name"] for c in pruned["characters"]]
    # First 6 core characters + Admiral Meylan (matched via token "Meylan" in previous_context)
    assert char_names[:6] == [f"MainChar{i}" for i in range(1, 7)]
    assert "Admiral Meylan" in char_names
    assert "MainChar7" not in char_names
    assert "MainChar8" not in char_names
    assert "Dr. Hunter" not in char_names

    term_sources = [t["source"] for t in pruned["terms"]]
    assert term_sources == ["CIC"]


@pytest.mark.asyncio
async def test_gemini_translator_prunes_glossary_per_batch():
    seen_instructions = []

    class _FakeModels:
        def generate_content(self, model, contents, config):
            seen_instructions.append(config.system_instruction)
            payload = json.loads(contents)
            return types.SimpleNamespace(
                text=json.dumps({
                    "translations": [
                        {"index": it["index"], "translated_text": f"תרגום {it['index']}"}
                        for it in payload["target_batch"]
                    ]
                })
            )

    glossary = {
        "summary": "Show",
        "characters": [
            {"name": f"Core{i}", "target_name": f"קור{i}", "gender": "male", "notes": ""}
            for i in range(1, 7)
        ] + [
            {"name": "Captain Slattery", "target_name": "קפטן סלאטרי", "gender": "male", "notes": ""},
            {"name": "Lieutenant Burk", "target_name": "סגן ברק", "gender": "male", "notes": ""},
        ],
        "terms": [
            {"source": "Bridge", "translation": "גשר הפיקוד", "notes": ""},
            {"source": "Engine Room", "translation": "חדר מנועים", "notes": ""},
        ],
        "register_notes": "",
    }
    tr = GeminiTranslator(api_key="test-key", batch_size=2, prev_context_size=0, lookahead_size=0)
    tr.client = types.SimpleNamespace(models=_FakeModels())

    segs = [
        TranslatableSegment(1, 0, 1, "Slattery is on the Bridge."),
        TranslatableSegment(2, 1, 2, "Hold course."),
        TranslatableSegment(3, 2, 3, "Burk is in the Engine Room."),
        TranslatableSegment(4, 3, 4, "Aye, sir."),
    ]
    # Ensure fake translation is long enough not to trigger _looks_truncated (>= 35% of source length)
    class _FakeFullModels:
        def generate_content(self, model, contents, config):
            seen_instructions.append(config.system_instruction)
            payload = json.loads(contents)
            return types.SimpleNamespace(
                text=json.dumps({
                    "translations": [
                        {"index": it["index"], "translated_text": f"תרגום מלא של שורה מספר {it['index']}"}
                        for it in payload["target_batch"]
                    ]
                })
            )

    tr.client = types.SimpleNamespace(models=_FakeFullModels())
    await tr.translate_segments(segs, "English", "Hebrew", glossary=glossary)

    assert len(seen_instructions) == 2
    assert "קפטן סלאטרי" in seen_instructions[0]
    assert "סגן ברק" not in seen_instructions[0]
    assert "גשר הפיקוד" in seen_instructions[0]
    assert "חדר מנועים" not in seen_instructions[0]

    assert "סגן ברק" in seen_instructions[1]
    assert "קפטן סלאטרי" not in seen_instructions[1]
    assert "חדר מנועים" in seen_instructions[1]


# ---------------------------------------------------------------------------
# 2. cost_saver_mode=False skips local frame filtering and keeps Whisper loaded
# ---------------------------------------------------------------------------

def _make_blank_frame(path: Path) -> Path:
    img = Image.new("RGB", (640, 360), color=(10, 10, 10))
    img.save(path, format="JPEG")
    return path


def _make_title_frame(path: Path, variant: int = 0) -> Path:
    img = Image.new("RGB", (640, 360), color=(15, 15, 15))
    draw = ImageDraw.Draw(img)
    # Draw high-contrast white title card bars/boxes in the upper 80% region
    for y in range(90, 210, 12):
        for x in range(80, 560, 16):
            draw.rectangle([x, y, x + 10, y + 6], fill=(250 - variant, 250 - variant, 250 - variant))
    img.save(path, format="JPEG", quality=95)
    return path


@pytest.mark.asyncio
async def test_cost_saver_false_skips_local_frame_filter_and_keeps_whisper_loaded(tmp_path, monkeypatch):
    # Ensure cost_saver_mode is False
    s = load_settings()
    assert s.cost_saver_mode is False

    # Blank frame would be dropped if prefilter ran, but with cost_saver_mode=False it is sent to Gemini
    blank_jpg = _make_blank_frame(tmp_path / "f_000001.jpg")
    frames = [Frame(time=2.0, path=blank_jpg, change=0.5, static_until=4.0)]

    seen_batches = []

    class _FakeVisionClient:
        def __init__(self):
            self.models = self

        def generate_content(self, model, contents, config):
            seen_batches.append(contents)
            return types.SimpleNamespace(text=json.dumps({"detections": []}))

    ctx = PipelineContext(job_id="j_test", video_path=str(tmp_path / "v.mp4"), file_name="v.mp4", detect_onscreen_text=True)

    async def _fake_sample():
        return frames

    ctx.frame_task = _fake_sample()
    stage = OnScreenTextStage(client_factory=lambda: _FakeVisionClient())
    await stage.execute(ctx)
    assert len(seen_batches) == 1  # Blank frame was NOT filtered out when cost_saver_mode=False

    # Also verify Whisper stays loaded when cost_saver_mode=False
    class _DummyTranscriber(BaseTranscriber):
        def __init__(self):
            self.unloaded = False

        async def transcribe(self, audio_path, language=None, on_segment_callback=None, cancel_flag=None):
            return [RawSegment(index=1, start_time=0.0, end_time=1.0, text="Hello")]

        def unload_model(self) -> None:
            self.unloaded = True

    dummy = _DummyTranscriber()
    ctx.audio_wav_path = str(tmp_path / "a.wav")
    monkeypatch.setattr(settings, "whisper_gap_recovery", False)
    monkeypatch.setattr(settings, "whisper_snap_starts", False)
    t_stage = TranscribeStage(transcriber=dummy)
    await t_stage.execute(ctx)
    assert dummy.unloaded is False


# ---------------------------------------------------------------------------
# 3. onscreen_prefilter drops blank synthetic frames and merges consecutive identical title frames
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_onscreen_prefilter_drops_blank_and_merges_duplicate_title_frames(tmp_path):
    f_blank1 = _make_blank_frame(tmp_path / "blank1.jpg")
    f_title1 = _make_title_frame(tmp_path / "title1.jpg", variant=0)
    f_title2 = _make_title_frame(tmp_path / "title2.jpg", variant=2)  # nearly identical
    f_blank2 = _make_blank_frame(tmp_path / "blank2.jpg")

    frames = [
        Frame(time=2.0, path=f_blank1, change=0.5, static_until=3.0),
        Frame(time=4.0, path=f_title1, change=0.5, static_until=5.0),
        Frame(time=6.0, path=f_title2, change=0.5, static_until=8.0),
        Frame(time=10.0, path=f_blank2, change=0.5, static_until=11.0),
    ]

    clusters = await prefilter_frames(frames)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.representative_path == f_title1
    assert cluster.start_time == 4.0
    assert cluster.end_time == 8.0


# ---------------------------------------------------------------------------
# 4. gender_pretag silently falls back when Ollama is unreachable and unloads Whisper when active
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gender_pretag_falls_back_when_ollama_unreachable_and_works_when_mocked(tmp_path, monkeypatch):
    # Enable cost_saver_mode
    s = load_settings()
    s.cost_saver_mode = True
    save_settings(s)

    # 4a. Whisper unload_model clears cache and sets _model = None when cost_saver_mode=True
    fake_whisper_obj = types.SimpleNamespace()
    fw._model_cache[("tiny", "cpu", "int8")] = fake_whisper_obj  # type: ignore[assignment]
    transcriber = fw.FasterWhisperTranscriber("tiny", device="cpu", compute_type="int8")
    transcriber._model = fake_whisper_obj  # type: ignore[assignment]
    transcriber.unload_model()
    assert transcriber._model is None
    assert len(fw._model_cache) == 0

    # Also verify TranscribeStage calls unload_model when cost_saver_mode=True
    class _TrackUnloadTranscriber(BaseTranscriber):
        def __init__(self):
            self.unloaded = False

        async def transcribe(self, audio_path, language=None, on_segment_callback=None, cancel_flag=None):
            return [RawSegment(index=1, start_time=0.0, end_time=1.0, text="You are ready.")]

        def unload_model(self) -> None:
            self.unloaded = True

    tracker = _TrackUnloadTranscriber()
    ctx = PipelineContext(job_id="j_unload", video_path="v.mp4", file_name="v.mp4", audio_wav_path="a.wav")
    monkeypatch.setattr(settings, "whisper_gap_recovery", False)
    monkeypatch.setattr(settings, "whisper_snap_starts", False)
    await TranscribeStage(transcriber=tracker).execute(ctx)
    assert tracker.unloaded is True

    # 4b. When Ollama is unreachable, pretag_gender_hints silently returns {}
    async def _unreachable_get(self, url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", _unreachable_get)
    segs = [
        TranslatableSegment(1, 0, 2, "Are you sure you're ready?"),
        TranslatableSegment(2, 2, 4, "The ship is moving."),
    ]
    assert len(find_ambiguous_segments(segs)) == 1
    hints = await pretag_gender_hints(segs, target_lang="Hebrew")
    assert hints == {}

    # 4c. When Ollama responds, picks preferred model (qwen2.5), sets keep_alive=0 on final request, and formats hints
    posted_bodies = []

    async def _fake_get(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"models": [{"name": "mistral:7b"}, {"name": "qwen2.5:7b-instruct"}]},
        )

    async def _fake_post(self, url, json=None, **kwargs):
        posted_bodies.append(json)
        return httpx.Response(
            200,
            json={"response": '{"items": [{"index": 1, "speaker": "M", "to": "F"}]}'},
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    assert pick_ollama_model([{"name": "llama3:8b"}, {"name": "qwen2.5:7b"}]) == "qwen2.5:7b"
    hints = await pretag_gender_hints(segs, target_lang="Hebrew")
    assert hints == {1: "[Speaker: M, To: F]"}
    assert posted_bodies[-1]["model"] == "qwen2.5:7b-instruct"
    assert posted_bodies[-1]["format"] == "json"
    assert posted_bodies[-1]["keep_alive"] == 0
    assert "#1: [Speaker: M, To: F]" in format_hints_for_context_notes(hints)


# ---------------------------------------------------------------------------
# 5. POST /api/settings/estimate returns lower cost when cost_saver_mode=True
# ---------------------------------------------------------------------------

def test_estimate_endpoint_returns_lower_cost_with_cost_saver_mode():
    with TestClient(app) as client:
        normal = client.post(
            "/api/settings/estimate",
            json={
                "duration_seconds": 45 * 60,
                "enable_translation": True,
                "detect_onscreen_text": True,
                "cost_saver_mode": False,
            },
        ).json()
        saver = client.post(
            "/api/settings/estimate",
            json={
                "duration_seconds": 45 * 60,
                "enable_translation": True,
                "detect_onscreen_text": True,
                "cost_saver_mode": True,
            },
        ).json()

        assert saver["stages"]["onscreen"]["frames"] == int(normal["stages"]["onscreen"]["frames"] * 0.25)
        assert saver["stages"]["onscreen"]["cost_usd"] < normal["stages"]["onscreen"]["cost_usd"]
        assert saver["total_cost_usd"] < normal["total_cost_usd"]


@pytest.mark.asyncio
async def test_usage_tracker_models_local_queries_and_saved_tokens(monkeypatch):
    from app.core.model_catalog import UsageTracker

    usage = UsageTracker()
    usage.record(
        "glossary",
        "gemini-3.8-flash",
        types.SimpleNamespace(prompt_token_count=1200, candidates_token_count=400, thoughts_token_count=200),
    )
    usage.record(
        "translation",
        "gemini-3.8-flash",
        types.SimpleNamespace(prompt_token_count=3000, candidates_token_count=1500, thoughts_token_count=500),
    )
    usage.record(
        "onscreen",
        "gemini-3.5-flash-lite",
        types.SimpleNamespace(prompt_token_count=800, candidates_token_count=100, thoughts_token_count=50),
    )
    usage.record_local_savings(saved_tokens=14000, frames_filtered=25, saved_cost_usd=0.005)

    async def _fake_get(self, url, **kwargs):
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})

    async def _fake_post(self, url, json=None, **kwargs):
        return httpx.Response(
            200,
            json={
                "response": '{"items": [{"index": 1, "speaker": "M", "to": "F"}]}',
                "prompt_eval_count": 120,
                "eval_count": 30,
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    segs = [TranslatableSegment(1, 0, 2, "Sarah, are you ready?")]
    await pretag_gender_hints(segs, target_lang="Hebrew", usage=usage, cloud_model="gemini-3.8-flash")

    summary = usage.to_dict()
    assert summary["models"]["gemini-3.8-flash"]["requests"] == 2
    assert summary["models"]["gemini-3.8-flash"]["total_tokens"] == (1200 + 400 + 200) + (3000 + 1500 + 500)
    assert summary["models"]["gemini-3.5-flash-lite"]["requests"] == 1
    assert summary["models"]["gemini-3.5-flash-lite"]["total_tokens"] == 800 + 100 + 50

    assert summary["local"]["model"] == "qwen2.5:7b"
    assert summary["local"]["requests"] == 1
    assert summary["local"]["input_tokens"] == 120
    assert summary["local"]["output_tokens"] == 30
    assert summary["local"]["frames_filtered"] == 25
    assert summary["local"]["saved_tokens"] > 14000


def test_ollama_status_endpoint(monkeypatch):
    async def _fake_get(self, url, **kwargs):
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    with TestClient(app) as client:
        res = client.get("/api/settings/ollama/status")
        assert res.status_code == 200
        data = res.json()
        assert data["installed"] is True
        assert data["running"] is True
        assert "qwen2.5:7b" in data["models"]
        assert data["selected_model"] == "qwen2.5:7b"
        assert data["download_url"] == "https://ollama.com/download"


