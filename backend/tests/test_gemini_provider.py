"""GeminiTranslator batching/retry logic, with the Google client replaced by a fake."""
import json
import types

import pytest

import app.providers.translation.gemini_provider as gp
from app.providers.translation.base import TranslatableSegment


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.requests = []

    def generate_content(self, model, contents, config):
        payload = json.loads(contents) if contents.startswith("{") else contents
        self.requests.append(payload)
        result = self.behaviour(payload, len(self.requests))
        if isinstance(result, Exception):
            raise result
        return types.SimpleNamespace(text=json.dumps(result))


def _translator(behaviour, monkeypatch):
    monkeypatch.setattr(gp.asyncio, "sleep", _no_sleep)
    t = gp.GeminiTranslator(api_key="test-key", batch_size=3)
    t.client = types.SimpleNamespace(models=FakeModels(behaviour))
    return t


async def _no_sleep(_):
    return None


def _segs(n):
    return [TranslatableSegment(index=i, start_time=i, end_time=i + 1, original_text=f"line {i}") for i in range(1, n + 1)]


@pytest.mark.asyncio
async def test_missing_indices_are_retried_then_flagged(monkeypatch):
    def behaviour(payload, call):
        items = payload["target_batch"]
        # Always drop index 2; translate everything else
        return {"translations": [{"index": it["index"], "translated_text": f"T{it['index']}"} for it in items if it["index"] != 2]}

    t = _translator(behaviour, monkeypatch)
    segs = _segs(4)
    await t.translate_segments(segs, "English", "Hebrew")
    assert [s.translated_text for s in segs] == ["T1", None, "T3", "T4"]
    assert [s.translation_failed for s in segs] == [False, True, False, False]
    # Retries only re-sent the missing segment
    retried = [r for r in t.client.models.requests if [i["index"] for i in r["target_batch"]] == [2]]
    assert len(retried) == t.max_retries - 1


@pytest.mark.asyncio
async def test_invalid_key_aborts_without_retrying(monkeypatch):
    err = Exception("400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key.")
    t = _translator(lambda p, c: err, monkeypatch)
    segs = _segs(5)
    with pytest.raises(gp.TranslationAborted):
        await t.translate_segments(segs, "English", "Hebrew")
    assert len(t.client.models.requests) == 1
    assert all(s.translation_failed for s in segs)


@pytest.mark.asyncio
async def test_context_comes_from_full_job_when_retrying_subset(monkeypatch):
    seen = []

    def behaviour(payload, call):
        seen.append(payload)
        return {"translations": [{"index": it["index"], "translated_text": "X"} for it in payload["target_batch"]]}

    t = _translator(behaviour, monkeypatch)
    everything = _segs(10)
    for s in everything:
        s.translated_text = f"done {s.index}"
    subset = [everything[6]]
    subset[0].translated_text = None
    await t.translate_segments(subset, "English", "Hebrew", context_segments=everything)
    prev = [p["index"] for p in seen[0]["previous_context"]]
    nxt = [p["index"] for p in seen[0]["lookahead_context"]]
    assert prev == [2, 3, 4, 5, 6] and nxt == [8, 9, 10]
    assert seen[0]["previous_context"][-1]["translated_text"] == "done 6"


@pytest.mark.asyncio
async def test_glossary_is_parsed_and_injected(monkeypatch):
    glossary = {"summary": "Navy thriller", "characters": [
        {"name": "Rachel Scott", "target_name": "רייצ'ל סקוט", "gender": "female", "notes": "virologist"}],
        "terms": [{"source": "XO", "translation": "סגן המפקד", "notes": ""}], "register_notes": "military"}
    t = _translator(lambda p, c: glossary, monkeypatch)
    result = await t.build_glossary(_segs(3), "English", "Hebrew")
    assert result["characters"][0]["gender"] == "female"
    prompt = gp.build_subtitle_system_prompt("English", "Hebrew", glossary=result)
    assert "רייצ'ל סקוט" in prompt and "female" in prompt and "סגן המפקד" in prompt


@pytest.mark.asyncio
async def test_cut_off_translations_are_asked_again(monkeypatch):
    def behaviour(payload, call):
        items = payload["target_batch"]
        if call == 1:  # the model stops mid-word for one line
            return {"translations": [{"index": it["index"], "translated_text": "המתן לנ" if it["index"] == 2 else f"T{it['index']}"} for it in items]}
        return {"translations": [{"index": it["index"], "translated_text": f"T{it['index']} full translation"} for it in items]}

    t = _translator(behaviour, monkeypatch)
    segs = _segs(3)
    segs[1].original_text = "Stand by for exact coordinates.\nNishioka, eyes on."
    await t.translate_segments(segs, "English", "Hebrew")
    assert segs[1].translated_text == "T2 full translation" and not segs[1].translation_failed
    assert gp._looks_truncated("Yes, sir.", "כן") is False  # short sources are left alone


def test_metric_conversion_prompt_injection():
    prompt_without_metric = gp.build_subtitle_system_prompt("English", "Hebrew", convert_to_metric=False)
    assert "Metric System Conversion" not in prompt_without_metric

    prompt_with_metric = gp.build_subtitle_system_prompt("English", "Hebrew", convert_to_metric=True)
    assert "Metric System Conversion" in prompt_with_metric
    assert "miles" in prompt_with_metric and "Fahrenheit" in prompt_with_metric


