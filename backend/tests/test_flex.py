"""Flex service tier: pricing, settings, the retry/fallback wrapper and how callers use it."""
import json
import types
from datetime import date

import pytest
from fastapi.testclient import TestClient

import app.core.model_catalog as mc
import app.providers.gemini_calls as gc
from app.core.config import settings
from app.core.user_settings import AppSettings
from app.main import app


@pytest.fixture(name="client")
def client_fixture():
    (settings.storage_dir / "settings.json").unlink(missing_ok=True)
    with TestClient(app) as client:
        yield client
    (settings.storage_dir / "settings.json").unlink(missing_ok=True)


class _Meta:
    def __init__(self, traffic=None):
        self.prompt_token_count = 1_000_000
        self.candidates_token_count = 1_000_000
        self.thoughts_token_count = 0
        self.traffic_type = traffic


class FakeClient:
    """Records every request; `script` yields an exception or None (= success) per call."""

    def __init__(self, script=()):
        self.models = self
        self.script = list(script)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        outcome = self.script.pop(0) if self.script else None
        if isinstance(outcome, Exception):
            raise outcome
        return types.SimpleNamespace(text='{"translations": []}', usage_metadata=_Meta())


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(gc.asyncio, "sleep", fake_sleep)
    return slept


def _config():
    from google.genai import types as gt

    return gt.GenerateContentConfig(temperature=0.2)


def _tier(config):
    tier = getattr(config, "service_tier", None)
    return getattr(tier, "value", tier)


# --- pricing -------------------------------------------------------------------------------------

def test_flex_costs_half_on_every_model():
    day = date(2026, 10, 14)
    for model in mc.CATALOG.values():
        assert model.flex
        std = mc.request_cost(model.id, 300_000, 20_000, day)
        assert mc.request_cost(model.id, 300_000, 20_000, day, flex=True) == pytest.approx(std / 2)
    # 3.8 Flash Flex, from Google's price table: $0.375 in / $1.875 out per 1M
    assert mc.request_cost("gemini-3.8-flash", 1_000_000, 1_000_000, day, flex=True) == pytest.approx(2.25)
    payload = {m["id"]: m for m in mc.catalog_payload(day)}
    assert payload["gemini-3.5-flash-lite"]["supports_flex"] is True
    assert payload["gemini-3.5-flash-lite"]["flex_price_factor"] == 0.5


def test_usage_uses_the_tier_the_api_reports():
    usage = mc.UsageTracker()
    usage.record("translation", "gemini-3.8-flash", _Meta("ON_DEMAND_FLEX"))
    usage.record("translation", "gemini-3.8-flash", _Meta("ON_DEMAND"), flex=True)  # API says Standard
    usage.record("translation", "gemini-3.8-flash", _Meta(), flex=True)  # no traffic_type: trust the request
    usage.record("translation", "gemini-3.8-flash", _Meta(), fell_back=True)
    stage = usage.stages["translation"]
    full = mc.request_cost("gemini-3.8-flash", 1_000_000, 1_000_000)
    assert stage.cost_usd == pytest.approx(full / 2 + full + full / 2 + full)
    assert stage.flex_requests == 2 and stage.flex_fallbacks == 1
    assert usage.flex_fallbacks() == 1
    # Older saved usage (no flex fields) still merges
    again = mc.UsageTracker()
    again.merge_dict({"stages": {"glossary": {"model": "m", "requests": 1, "input_tokens": 1,
                                              "output_tokens": 1, "thinking_tokens": 0, "cost_usd": 0.1}}})
    again.merge_dict(usage.to_dict())
    assert again.flex_fallbacks() == 1


def test_estimate_halves_flex_stages():
    base = {"translation": {"model": "gemini-3.8-flash", "thinking": "low"},
            "glossary": {"model": "gemini-3.8-flash", "thinking": "low"},
            "vision": {"model": "gemini-3.5-flash-lite", "thinking": "minimal"}}
    std = mc.estimate_job_cost(2700, True, True, AppSettings.model_validate(base))
    flexed = json.loads(json.dumps(base))
    for step in flexed.values():
        step["flex"] = True
    fl = mc.estimate_job_cost(2700, True, True, AppSettings.model_validate(flexed))
    for name, stage in fl["stages"].items():
        assert stage["flex"] is True
        assert stage["cost_usd"] == pytest.approx(std["stages"][name]["cost_usd"] / 2, abs=1e-4)


# --- settings ------------------------------------------------------------------------------------

def test_flex_setting_roundtrip_and_old_files(client: TestClient, tmp_path):
    from app.core import user_settings as us

    current = client.get("/api/settings").json()["settings"]
    assert all(current[s]["flex"] is False for s in ("translation", "glossary", "vision"))
    current["translation"]["flex"] = True
    assert client.put("/api/settings", json=current).status_code == 200
    assert client.get("/api/settings").json()["settings"]["translation"]["flex"] is True

    # A settings.json saved before Flex existed still loads (flex off)
    us._path().write_text(json.dumps({
        "translation": {"model": "gemini-3.8-flash", "thinking": "low"},
        "glossary": {"model": "gemini-3.8-flash", "thinking": "low"},
        "vision": {"model": "gemini-3.5-flash-lite", "thinking": "minimal"},
        "vision_media_resolution": "medium"}), encoding="utf-8")
    assert us.load_settings().vision.flex is False


def test_flex_rejected_for_models_without_it(monkeypatch):
    from dataclasses import replace

    monkeypatch.setitem(mc.CATALOG, "gemini-3.1-flash-lite", replace(mc.CATALOG["gemini-3.1-flash-lite"], flex=False))
    with pytest.raises(ValueError, match="Flex"):
        AppSettings.model_validate({
            "translation": {"model": "gemini-3.8-flash"}, "glossary": {"model": "gemini-3.8-flash"},
            "vision": {"model": "gemini-3.1-flash-lite", "flex": True}})


# --- the wrapper ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_standard_call_is_unchanged(no_sleep):
    client, usage = FakeClient(), mc.UsageTracker()
    await gc.generate_content(client, model="gemini-3.8-flash", contents="hi", config=_config(), usage=usage, stage="s")
    assert len(client.calls) == 1 and _tier(client.calls[0][2]) is None
    assert usage.stages["s"].flex_requests == 0


@pytest.mark.asyncio
async def test_flex_request_uses_flex_tier_and_long_timeout(no_sleep):
    client, usage = FakeClient(), mc.UsageTracker()
    cfg = _config()
    await gc.generate_content(client, model="gemini-3.8-flash", contents="hi", config=cfg, flex=True, usage=usage, stage="s")
    sent = client.calls[0][2]
    assert _tier(sent) == "flex"
    assert sent.http_options.timeout == gc.FLEX_TIMEOUT_MS
    assert sent.temperature == 0.2 and _tier(cfg) is None  # the caller's config is not modified
    assert usage.stages["s"].flex_requests == 1


@pytest.mark.asyncio
async def test_flex_retries_capacity_errors_then_succeeds(no_sleep):
    busy = Exception("503 UNAVAILABLE. The model is overloaded.")
    client, usage = FakeClient([busy, busy]), mc.UsageTracker()
    await gc.generate_content(client, model="m", contents="hi", config=_config(), flex=True, usage=usage, stage="s")
    assert [_tier(c[2]) for c in client.calls] == ["flex", "flex", "flex"]
    assert no_sleep == [5.0, 10.0]
    assert usage.flex_fallbacks() == 0


@pytest.mark.asyncio
async def test_flex_falls_back_to_standard_when_busy(no_sleep):
    busy = Exception("429 RESOURCE_EXHAUSTED")
    client, usage = FakeClient([busy, busy, busy]), mc.UsageTracker()
    await gc.generate_content(client, model="m", contents="hi", config=_config(), flex=True, usage=usage, stage="s")
    assert [_tier(c[2]) for c in client.calls] == ["flex", "flex", "flex", None]
    assert usage.stages["s"].flex_fallbacks == 1 and usage.stages["s"].flex_requests == 0


@pytest.mark.asyncio
async def test_flex_timeout_falls_back_at_once(no_sleep):
    class ReadTimeout(Exception):
        pass

    client = FakeClient([ReadTimeout("The read operation timed out")])
    await gc.generate_content(client, model="m", contents="hi", config=_config(), flex=True)
    assert [_tier(c[2]) for c in client.calls] == ["flex", None]
    assert no_sleep == []


@pytest.mark.asyncio
async def test_flex_does_not_hide_real_errors(no_sleep):
    client = FakeClient([Exception("404 NOT_FOUND. models/x is not found")])
    with pytest.raises(Exception, match="404"):
        await gc.generate_content(client, model="x", contents="hi", config=_config(), flex=True)
    assert len(client.calls) == 1


# --- callers -------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_translator_uses_flex_per_step(no_sleep):
    import app.providers.translation.gemini_provider as gp
    from app.providers.translation.base import TranslatableSegment

    t = gp.GeminiTranslator(api_key="k", batch_size=5, flex=True, glossary_flex=False,
                            glossary_model="gemini-3.5-flash-lite")
    t.client = FakeClient()
    segs = [TranslatableSegment(index=1, start_time=0, end_time=1, original_text="hello")]
    await t.build_glossary(segs, "English", "Hebrew")
    await t._generate("{}", "prompt", gp.TranslationBatchResponseSchema, temperature=0.2)
    glossary_call, translation_call = t.client.calls
    assert glossary_call[0] == "gemini-3.5-flash-lite" and _tier(glossary_call[2]) is None
    assert _tier(translation_call[2]) == "flex"


def test_translate_stage_reads_flex_from_settings(client: TestClient):
    from app.core.user_settings import load_settings, save_settings
    from app.pipeline.context import PipelineContext
    from app.pipeline.stages.translate_stage import TranslateStage

    s = load_settings()
    s.translation.flex, s.glossary.flex = True, False
    save_settings(s)
    ctx = PipelineContext.__new__(PipelineContext)
    ctx.gemini_api_key_override, ctx.gemini_model, ctx.usage = "k", None, None
    tr = TranslateStage()._make_translator(ctx)
    assert tr.flex is True and tr.glossary_flex is False
    ctx.gemini_model = "gemini-4-argon"  # per-job model override: Settings' Flex choice doesn't apply
    assert TranslateStage()._make_translator(ctx).flex is False


@pytest.mark.asyncio
async def test_vision_scan_sends_flex(tmp_path, no_sleep):
    from app.pipeline.stages import ocr_stage

    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"\xff\xd8\xff")
    client = FakeClient()
    client.generate_content = (lambda orig: lambda model, contents, config: (
        orig(model, contents, config), types.SimpleNamespace(text='{"detections": []}', usage_metadata=None))[1]
    )(client.generate_content)
    await ocr_stage._gemini_detect(client, "gemini-3.5-flash-lite", [ocr_stage.Frame(time=1.0, path=frame)], flex=True)
    assert _tier(client.calls[0][2]) == "flex"


def test_check_probes_flex_when_enabled(client: TestClient, monkeypatch):
    import app.api.routes_settings as rs

    fake = FakeClient()
    monkeypatch.setattr(rs, "_make_client", lambda key: fake)
    body = {"api_key": "k", "settings": {
        "translation": {"model": "gemini-3.8-flash", "thinking": "low", "flex": True},
        "glossary": {"model": "gemini-3.6-flash", "thinking": "default"},
        "vision": {"model": "gemini-3.5-flash-lite", "thinking": "minimal", "flex": True},
        "vision_media_resolution": "low"}}
    data = client.post("/api/settings/check", json=body).json()
    assert all(step["ok"] for step in data["steps"].values())
    tiers = {m: _tier(c) for m, _, c in fake.calls}
    assert tiers == {"gemini-3.8-flash": "flex", "gemini-3.6-flash": None, "gemini-3.5-flash-lite": "flex"}


@pytest.mark.asyncio
async def test_flex_switches_to_standard_after_repeated_fallbacks(no_sleep):
    busy = Exception("503 UNAVAILABLE. This model is currently experiencing high demand.")
    state, usage = gc.FlexState(), mc.UsageTracker()
    # Two requests in a row fall back (3 Flex attempts + 1 Standard each)
    client = FakeClient([busy] * 3 + [None] + [busy] * 3 + [None])
    for _ in range(2):
        await gc.generate_content(client, model="m", contents="x", config=_config(), flex=True,
                                  usage=usage, stage="s", flex_state=state)
    assert "m" in state.disabled and len(client.calls) == 8
    # From now on this model goes straight to Standard; other models still try Flex
    await gc.generate_content(client, model="m", contents="x", config=_config(), flex=True,
                              usage=usage, stage="s", flex_state=state)
    await gc.generate_content(client, model="other", contents="x", config=_config(), flex=True,
                              usage=usage, stage="s", flex_state=state)
    assert [_tier(c[2]) for c in client.calls[8:]] == [None, "flex"]


@pytest.mark.asyncio
async def test_a_flex_success_resets_the_count(no_sleep):
    busy = Exception("503 UNAVAILABLE")
    state = gc.FlexState()
    client = FakeClient([busy] * 3 + [None] + [None] + [busy] * 3 + [None])
    for _ in range(3):
        await gc.generate_content(client, model="m", contents="x", config=_config(), flex=True, flex_state=state)
    assert not state.disabled


@pytest.mark.asyncio
async def test_slow_flex_answers_also_switch_to_standard(no_sleep, monkeypatch):
    monkeypatch.setattr(gc, "FLEX_SLOW_SECONDS", -1.0)  # every answer counts as slow
    state = gc.FlexState()
    client = FakeClient()
    for _ in range(2):   # two slow Flex answers in a row
        await gc.generate_content(client, model="m", contents="x", config=_config(), flex=True, flex_state=state)
    assert "m" in state.disabled
    await gc.generate_content(client, model="m", contents="x", config=_config(), flex=True, flex_state=state)
    assert [_tier(c[2]) for c in client.calls] == ["flex", "flex", None]
