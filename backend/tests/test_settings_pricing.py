"""Model catalog pricing, thinking configuration, Settings API, estimates and usage tracking."""
import json
import time
import types
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core import model_catalog as mc
from app.core.config import settings
from app.core.database import engine
from app.core.user_settings import AppSettings, GeminiCallSettings
from app.main import app
from app.models.db_models import Job, JobStatus, SubtitleSegmentRecord
from app.pipeline.stages.translate_stage import TranslateStage


@pytest.fixture(name="client")
def client_fixture():
    (settings.storage_dir / "settings.json").unlink(missing_ok=True)
    with TestClient(app) as client:
        yield client
    (settings.storage_dir / "settings.json").unlink(missing_ok=True)


def test_promotional_price_switches_on_date():
    argon = mc.get_model("gemini-4-argon")
    assert argon.price_on(date(2026, 11, 30)).input == 2.00
    assert argon.price_on(date(2026, 12, 1)).input == 4.00
    change = argon.next_price_change(date(2026, 10, 14))
    assert change[0] == date(2026, 11, 30) and change[1].output == 20.00


def test_request_cost_includes_thinking_and_long_context_tier():
    # 3.5 Flash-Lite: $0.30 in / $2.50 out per 1M
    assert mc.request_cost("gemini-3.5-flash-lite", 1_000_000, 1_000_000) == pytest.approx(2.80)
    # 3.1 Pro: long-context rates above 200k prompt tokens
    assert mc.request_cost("gemini-3.1-pro-preview", 300_000, 0) == pytest.approx(300_000 / 1e6 * 4.00)
    assert mc.request_cost("some-unknown-model", 10, 10) is None


def test_thinking_config_mapping():
    lvl = mc.thinking_config("gemini-3.6-flash", "minimal")
    assert lvl.thinking_level.value == "MINIMAL" and lvl.thinking_budget is None
    assert mc.thinking_config("gemini-3.6-flash", "default") is None
    with pytest.raises(ValueError):
        mc.validate_thinking("gemini-3.8-flash", "minimal")  # 3.8 Flash has no "minimal" level
    assert mc.allowed_thinking("custom-model-x") == ("default",)


def test_temperature_and_media_resolution_by_family():
    assert mc.default_temperature("gemini-2.5-flash", 0.2) == 0.2
    assert mc.default_temperature("gemini-3.8-flash", 0.2) is None
    assert mc.media_resolution("gemini-2.5-flash", "low") is None
    assert mc.media_resolution("gemini-3.8-flash", "low") is not None
    assert mc.image_tokens("gemini-2.5-flash") == 258
    assert mc.image_tokens("gemini-3.8-flash", "medium") == 560


def test_settings_api_roundtrip_and_validation(client: TestClient):
    data = client.get("/api/settings").json()
    assert data["settings"]["translation"]["model"] == mc.resolve_model(settings.gemini_model, "translation")
    ids = {m["id"] for m in data["catalog"]}
    assert {"gemini-3.8-flash", "gemini-4-argon", "gemini-3.5-flash-lite"} <= ids
    assert not any(i.startswith("gemini-2.5") for i in ids)

    new = data["settings"]
    new["translation"] = {"model": "gemini-3.6-flash", "thinking": "low"}
    new["vision"] = {"model": "gemini-3.5-flash-lite", "thinking": "minimal"}
    new["vision_media_resolution"] = "low"
    assert client.put("/api/settings", json=new).status_code == 200
    again = client.get("/api/settings").json()["settings"]
    assert again["translation"] == {"model": "gemini-3.6-flash", "thinking": "low", "flex": False}
    assert json.loads((settings.storage_dir / "settings.json").read_text())["vision_media_resolution"] == "low"

    bad = dict(again, translation={"model": "gemini-3.8-flash", "thinking": "minimal"})
    assert client.put("/api/settings", json=bad).status_code == 422
    # A model that can't do the task, and a retired model, are both rejected
    wrong_task = dict(again, translation={"model": "gemini-3.5-flash-lite", "thinking": "low"})
    assert client.put("/api/settings", json=wrong_task).status_code == 422
    retired = dict(again, glossary={"model": "gemini-2.5-flash-lite", "thinking": "default"})
    assert client.put("/api/settings", json=retired).status_code == 422


def test_catalog_lists_only_models_suited_to_each_task():
    assert set(mc.models_for("translation")) == {
        "gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-argon", "gemini-3.1-pro-preview"}
    assert set(mc.models_for("vision")) == {
        "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"}
    assert "gemini-4-argon" not in mc.models_for("glossary")
    for gone in ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash-lite"):
        assert mc.get_model(gone) is None
    assert mc.resolve_model("gemini-2.5-flash", "vision") == "gemini-3.8-flash"
    assert mc.resolve_model("gemini-2.5-flash-lite", "translation") == "gemini-3.8-flash"  # lite can't translate
    assert mc.resolve_model("gemini-3.5-flash", "translation") == "gemini-3.8-flash"
    assert mc.resolve_model("gemini-4-argon", "vision") == "gemini-3.5-flash-lite"
    assert mc.resolve_model("my-custom-model", "translation") == "my-custom-model"


def test_saved_settings_with_retired_models_are_migrated(client: TestClient):
    path = settings.storage_dir / "settings.json"
    path.write_text(json.dumps({
        "translation": {"model": "gemini-3.5-flash", "thinking": "default"},
        "glossary": {"model": "gemini-2.5-flash-lite", "thinking": "default"},
        "vision": {"model": "gemini-2.5-flash", "thinking": "default"},
        "vision_media_resolution": "high",
    }))
    s = client.get("/api/settings").json()["settings"]
    assert s["translation"] == {"model": "gemini-3.8-flash", "thinking": "low", "flex": False}
    assert s["glossary"] == {"model": "gemini-3.5-flash-lite", "thinking": "low", "flex": False}
    assert s["vision"] == {"model": "gemini-3.8-flash", "thinking": "low", "flex": False}  # 3.8 has no "minimal"
    assert s["vision_media_resolution"] == "high"
    assert json.loads(path.read_text())["glossary"]["model"] == "gemini-3.5-flash-lite"  # written back


def test_estimate_endpoint(client: TestClient):
    est = client.post("/api/settings/estimate", json={
        "duration_seconds": 45 * 60, "enable_translation": True, "detect_onscreen_text": True}).json()
    assert set(est["stages"]) == {"glossary", "translation", "onscreen", "foreign"}
    assert est["stages"]["foreign"]["frames"] == 540  # 10% of 45 min, 2 crops per second
    assert est["segments"] == 630
    assert est["stages"]["translation"]["requests"] == 21
    assert est["stages"]["onscreen"]["frames"] == 360  # capped by ONSCREEN_MAX_FRAMES
    assert 0 < est["total_cost_usd"] < 1
    assert est["total_cost_usd"] == pytest.approx(sum(s["cost_usd"] for s in est["stages"].values()), abs=1e-3)

    forced = client.post("/api/settings/estimate", json={
        "duration_seconds": 2700, "detect_onscreen_text": True, "has_forced_track": True}).json()
    assert "onscreen" not in forced["stages"] and "foreign" not in forced["stages"]


def test_estimate_responds_to_thinking_level():
    base = AppSettings(translation=GeminiCallSettings(model="gemini-3.8-flash", thinking="low"),
                       glossary=GeminiCallSettings(model="gemini-3.8-flash", thinking="low"),
                       vision=GeminiCallSettings(model="gemini-3.5-flash-lite"))
    high = base.model_copy(update={"translation": GeminiCallSettings(model="gemini-3.8-flash", thinking="high")})
    cheap = mc.estimate_job_cost(2700, True, False, base)["stages"]["translation"]["cost_usd"]
    pricey = mc.estimate_job_cost(2700, True, False, high)["stages"]["translation"]["cost_usd"]
    assert pricey > cheap * 2


class _FakeGenAI:
    """Returns valid translations and realistic usage_metadata; remembers each config."""

    def __init__(self):
        self.configs = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.configs.append((model, config))
        if contents.startswith("{"):
            items = json.loads(contents)["target_batch"]
            body = {"translations": [{"index": i["index"], "translated_text": "שלום"} for i in items]}
        else:
            body = {"summary": "s", "characters": [], "terms": [], "register_notes": ""}
        usage = types.SimpleNamespace(prompt_token_count=1000, candidates_token_count=200, thoughts_token_count=300)
        return types.SimpleNamespace(text=json.dumps(body), usage_metadata=usage)


def test_usage_is_tracked_per_stage_and_accumulates_across_runs(client: TestClient, monkeypatch):
    from app.providers.translation.gemini_provider import GeminiTranslator

    client.put("/api/settings", json={
        "translation": {"model": "gemini-3.8-flash", "thinking": "low"},
        "glossary": {"model": "gemini-3.6-flash", "thinking": "minimal"},
        "vision": {"model": "gemini-3.5-flash-lite", "thinking": "default"},
        "vision_media_resolution": "medium",
    })
    fake = _FakeGenAI()

    def make(self, ctx):
        from app.core.user_settings import load_settings
        s = load_settings()
        tr = GeminiTranslator(api_key="k", model_name=s.translation.model, thinking=s.translation.thinking,
                              glossary_model=s.glossary.model, glossary_thinking=s.glossary.thinking, usage=ctx.usage)
        tr.client = fake
        return tr

    monkeypatch.setattr(TranslateStage, "_make_translator", make)

    job_id = f"test_{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(Job(id=job_id, video_path="x", file_name="x.mkv", status=JobStatus.COMPLETED))
        session.commit()
        for i in range(1, 4):
            session.add(SubtitleSegmentRecord(job_id=job_id, index=i, start_time=i, end_time=i + 1, original_text=f"Line {i}"))
        session.commit()

    def run():
        client.post(f"/api/jobs/{job_id}/translate", json={})
        for _ in range(100):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] == "COMPLETED":
                return job
            time.sleep(0.05)
        raise AssertionError("translation did not finish")

    job = run()
    usage = job["usage"]["stages"]
    assert usage["glossary"]["model"] == "gemini-3.6-flash" and usage["glossary"]["requests"] == 1
    assert usage["translation"]["thinking_tokens"] == 300
    # 1000 in + (200 out + 300 thinking) at today's 3.8 Flash rates, per 1M
    price = mc.get_model("gemini-3.8-flash").price_on()
    assert usage["translation"]["cost_usd"] == pytest.approx((1000 * price.input + 500 * price.output) / 1e6)

    # Thinking config actually sent: glossary = level MINIMAL, translation = level LOW
    glossary_cfg = next(c for m, c in fake.configs if m == "gemini-3.6-flash")
    translation_cfg = next(c for m, c in fake.configs if m == "gemini-3.8-flash")
    assert glossary_cfg.thinking_config.thinking_level.value == "MINIMAL"
    assert glossary_cfg.temperature is None  # Gemini 3+: keep default temperature
    assert translation_cfg.thinking_config.thinking_level.value == "LOW"

    job = run()  # second run (retranslate) adds to the total
    assert job["usage"]["stages"]["translation"]["requests"] == 2
    assert job["usage"]["total_cost_usd"] > 0


def test_check_models_reports_access_per_model(client: TestClient, monkeypatch):
    import app.api.routes_settings as rs

    calls = []

    class _Client:
        def __init__(self):
            self.models = self

        def generate_content(self, model, contents, config):
            calls.append((model, contents, config))
            if model == "gemini-3.6-flash":
                raise Exception("404 NOT_FOUND. This model is only available to users who have used it before.")
            if model == "gemini-3.1-flash-lite" and isinstance(contents, list):
                raise Exception("400 INVALID_ARGUMENT. Image input is not supported.")
            return types.SimpleNamespace(text="OK")

    monkeypatch.setattr(rs, "_make_client", lambda key: _Client())
    body = {"api_key": "k", "settings": {
        "translation": {"model": "gemini-3.8-flash", "thinking": "low"},
        "glossary": {"model": "gemini-3.6-flash", "thinking": "default"},
        "vision": {"model": "gemini-3.1-flash-lite", "thinking": "default"},
        "vision_media_resolution": "low"}}
    data = client.post("/api/settings/check", json=body).json()
    results = data["results"]
    assert results["gemini-3.8-flash"] == {"ok": True, "error": None}
    assert results["gemini-3.6-flash"]["ok"] is False and "only available" in results["gemini-3.6-flash"]["error"]
    assert results["gemini-3.1-flash-lite"]["ok"] is False and "vision:" in results["gemini-3.1-flash-lite"]["error"]
    assert data["steps"]["translation"]["ok"] is True

    # The probe uses the chosen thinking level, and the vision probe sends a real image
    tr = next(c for c in calls if c[0] == "gemini-3.8-flash")
    assert tr[2].thinking_config.thinking_level.value == "LOW"
    vision = next(c for c in calls if c[0] == "gemini-3.1-flash-lite")
    assert vision[1][0].inline_data.mime_type == "image/png"
    assert vision[2].media_resolution is not None
    assert client.post("/api/settings/check", json={}).status_code == 400  # tests run without a key


def test_defaults_use_current_models(client: TestClient):
    from app.core.config import Settings

    # Code defaults (a GEMINI_MODEL line in backend/.env still takes precedence)
    assert Settings.model_fields["gemini_model"].default == "gemini-3.8-flash"
    assert Settings.model_fields["gemini_vision_model"].default == "gemini-3.5-flash-lite"
    s = client.get("/api/settings").json()["settings"]
    assert s["translation"] == {"model": mc.resolve_model(settings.gemini_model, "translation"), "thinking": "low", "flex": False}
    assert s["vision"]["model"] == mc.resolve_model(settings.gemini_vision_model, "vision")


def test_glossary_model_failure_is_reported_but_translation_continues(client: TestClient, monkeypatch):
    from app.providers.translation.gemini_provider import GeminiTranslator

    class _Client:
        def __init__(self):
            self.models = self

        def generate_content(self, model, contents, config):
            if model == "gemini-2.5-flash-lite":
                raise Exception("404 NOT_FOUND: model not available for new users")
            items = json.loads(contents)["target_batch"]
            return types.SimpleNamespace(text=json.dumps({"translations": [
                {"index": i["index"], "translated_text": "בסדר"} for i in items]}), usage_metadata=None)

    def make(self, ctx):
        tr = GeminiTranslator(api_key="k", model_name="gemini-3.8-flash", glossary_model="gemini-2.5-flash-lite", usage=ctx.usage)
        tr.client = _Client()
        return tr

    monkeypatch.setattr(TranslateStage, "_make_translator", make)
    job_id = f"test_{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(Job(id=job_id, video_path="x", file_name="x.mkv", status=JobStatus.COMPLETED))
        session.add(SubtitleSegmentRecord(job_id=job_id, index=1, start_time=1, end_time=2, original_text="Okay."))
        session.commit()
    client.post(f"/api/jobs/{job_id}/translate", json={})
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "COMPLETED":
            break
        time.sleep(0.05)
    assert job["segments"][0]["translated_text"] == "בסדר"
    assert "Glossary pass skipped" in job["warning_message"] and "gemini-2.5-flash-lite" in job["warning_message"]
