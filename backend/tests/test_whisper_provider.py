"""FasterWhisperTranscriber threading, cancellation and batched->sequential fallback (fake model)."""
import threading
import time
import types

import pytest

import app.providers.transcription.faster_whisper as fw
from app.pipeline.context import PipelineCancelled


def _segment(i):
    word = types.SimpleNamespace(word=" hi", start=i, end=i + 0.4, probability=0.9)
    return types.SimpleNamespace(start=i, end=i + 0.5, text=" hi", avg_logprob=-0.1, words=[word])


class FakeModel:
    def __init__(self, n=5, delay=0.0):
        self.n, self.delay, self.calls = n, delay, []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)

        def gen():
            for i in range(self.n):
                time.sleep(self.delay)
                yield _segment(i)

        return gen(), types.SimpleNamespace(duration=float(self.n), language="en")


class ExplodingBatched:
    def __init__(self, model):
        pass

    def transcribe(self, audio, **kwargs):
        def gen():
            raise RuntimeError("CUDA failed with error out of memory")
            yield  # pragma: no cover

        return gen(), types.SimpleNamespace(duration=5.0, language="en")


def _transcriber(model, monkeypatch, device="cpu"):
    monkeypatch.setattr(fw, "_load_model", lambda name, device, compute: model)
    monkeypatch.setattr(fw, "get_hardware_info", lambda: {"cuda_available": device == "cuda"})
    return fw.FasterWhisperTranscriber("tiny", device=device, compute_type="int8")


@pytest.mark.asyncio
async def test_streams_segments_and_reports_language(monkeypatch):
    model = FakeModel(n=5)
    t = _transcriber(model, monkeypatch)
    seen = []

    async def cb(seg, pct):
        seen.append(pct)

    result = await t.transcribe("a.wav", on_segment_callback=cb)
    assert len(result) == 5 and len(seen) == 5
    assert t.detected_language == "en"
    assert model.calls[0]["condition_on_previous_text"] is False
    assert model.calls[0]["word_timestamps"] is True


@pytest.mark.asyncio
async def test_cancel_stops_worker_thread(monkeypatch):
    model = FakeModel(n=1000, delay=0.01)
    t = _transcriber(model, monkeypatch)
    flag = threading.Event()
    count = 0

    async def cb(seg, pct):
        nonlocal count
        count += 1
        if count == 3:
            flag.set()

    started = time.time()
    with pytest.raises(PipelineCancelled):
        await t.transcribe("a.wav", on_segment_callback=cb, cancel_flag=flag)
    assert time.time() - started < 2  # did not decode all 1000 segments


@pytest.mark.asyncio
async def test_batched_oom_falls_back_to_sequential(monkeypatch):
    model = FakeModel(n=3)
    monkeypatch.setattr(fw, "BatchedInferencePipeline", ExplodingBatched)
    t = _transcriber(model, monkeypatch, device="cuda")
    t.batch_size = 8
    result = await t.transcribe("a.wav")
    assert len(result) == 3  # recovered via sequential decoding
