"""Word starts that Whisper placed inside a pause are pulled forward to the speech onset."""
import sys
import types
import wave

import numpy as np
import pytest

from app.pipeline.context import RawSegment, TranscribedWord
from app.pipeline.stages.refine_stage import refine_raw_segments
from app.pipeline.word_snap import snap_word_starts
from app.providers.transcription import faster_whisper as fw


def W(word, start, end):
    return TranscribedWord(word=word, start=start, end=end, probability=0.9)


def seg(index, words):
    return RawSegment(index, words[0].start, words[-1].end, "".join(w.word for w in words), words=words)


REGIONS = [(10.0, 10.7), (15.0, 15.6)]


def test_word_after_a_pause_starts_at_the_speech_onset():
    # Whisper chained "bridge" to the end of "the" although nobody speaks until 15.0s
    words = [W(" Captain's", 10.0, 10.5), W(" on", 10.5, 10.7), W(" the", 10.7, 10.9), W(" bridge.", 10.9, 15.5)]
    s = seg(1, words)
    stats = snap_word_starts([s], REGIONS)
    assert (stats.moved, round(words[3].start, 2)) == (1, 14.8)
    assert [w.start for w in words[:3]] == [10.0, 10.5, 10.7]
    assert stats.max_shift == pytest.approx(3.9, abs=0.01)


def test_segment_start_follows_its_first_word():
    words = [W(" Hello", 10.2, 15.4)]  # padded chunk start, word stuck at the beginning of the chunk
    s = RawSegment(1, 10.2, 15.4, "Hello", words=words)
    snap_word_starts([s], REGIONS)
    assert (words[0].start, s.start_time) == (14.8, 14.8)


@pytest.mark.parametrize("start,end", [
    (10.1, 10.6),    # inside speech already
    (14.8, 15.4),    # lead is only 0.2s: below the minimum correction
    (10.9, 11.2),    # entirely in silence, no speech reached
    (15.6, 15.9),    # after the last region
])
def test_words_that_need_no_correction_are_left_alone(start, end):
    words = [W(" word", start, end)]
    s = seg(1, words)
    stats = snap_word_starts([s], REGIONS)
    assert (stats.moved, words[0].start) == (0, start)


def test_short_pauses_are_not_gaps():
    # 0.3s of silence between two bursts is just a breath, not worth correcting
    regions = [(10.0, 10.7), (11.0, 12.0)]
    words = [W(" word", 10.7, 12.0)]
    stats = snap_word_starts([seg(1, words)], regions)
    assert (stats.moved, words[0].start) == (0, 10.7)


def test_a_start_inside_the_previous_words_speech_region_is_still_caught():
    # The detector's region ends at 10.7 although Whisper ended "on" at 10.5: the stuck start (10.5)
    # is inside that region, but the word crosses 4.3s of silence
    words = [W(" on", 10.3, 10.5), W(" bridge.", 10.5, 15.5)]
    stats = snap_word_starts([seg(1, words)], REGIONS)
    assert (stats.moved, words[1].start, words[0].start) == (1, 14.8, 10.3)


def test_speech_the_detector_missed_keeps_whispers_timing():
    # Shouting under gunfire: no region at all around 20-24s, so nothing to snap to
    words = [W(" Fire!", 20.0, 20.6), W(" Fire!", 21.5, 22.0)]
    snap_word_starts([seg(1, words)], REGIONS)
    assert [w.start for w in words] == [20.0, 21.5]


def test_word_must_reach_into_the_speech_it_snaps_to():
    words = [W(" ghost", 11.0, 15.05)]  # ends 0.05s after the onset: probably an artefact
    snap_word_starts([seg(1, words)], REGIONS)
    assert words[0].start == 11.0


def test_subtitle_no_longer_appears_before_anyone_speaks():
    # "Captain's" is stuck to the end of "Okay." although nothing is said until 15.0s
    def build():
        return [
            seg(1, [W(" Okay.", 10.0, 10.5)]),
            seg(2, [W(" Captain's", 10.5, 15.2), W(" on", 15.2, 15.4), W(" the", 15.4, 15.5), W(" bridge.", 15.5, 15.9)]),
        ]

    regions = [(10.0, 10.6), (15.0, 15.95)]
    before = refine_raw_segments(build())
    fixed = build()
    snap_word_starts(fixed, regions)
    after = refine_raw_segments(fixed)

    start = lambda blocks: min(b["start_time"] for b in blocks if "Captain" in b["original_text"])
    assert start(before) < 14.4          # shown a second or more before it is said
    assert 14.45 <= start(after) <= 14.85  # at the speech, with at most the usual short lead-in


@pytest.mark.asyncio
async def test_provider_snaps_with_tight_vad_settings(tmp_path, monkeypatch):
    sr = 16000
    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(np.zeros(20 * sr, dtype=np.int16).tobytes())

    calls = {}

    def fake_vad(audio, options):
        calls["options"] = options
        return [{"start": 10 * sr, "end": int(10.7 * sr)}, {"start": 15 * sr, "end": int(15.6 * sr)}]

    import faster_whisper.vad as vad
    monkeypatch.setattr(vad, "get_speech_timestamps", fake_vad)
    monkeypatch.setattr(fw, "get_hardware_info", lambda: {"cuda_available": False})

    words = [W(" Captain's", 10.0, 10.5), W(" bridge.", 10.5, 15.5)]
    spoken = [seg(1, words)]
    t = fw.FasterWhisperTranscriber("tiny", device="cpu")
    stats = await t.snap_starts_to_speech(str(wav), spoken)

    assert stats.moved == 1 and words[1].start == 14.8
    assert calls["options"].speech_pad_ms == 0 and calls["options"].min_silence_duration_ms == 150


@pytest.mark.asyncio
async def test_stage_aligns_starts_after_everything_else(monkeypatch):
    from app.core.config import settings
    from app.pipeline.context import PipelineContext
    from app.pipeline.stages.transcribe_stage import TranscribeStage

    class FakeTranscriber:
        detected_language = "en"
        snapped = None

        async def transcribe(self, audio_path, language, on_segment_callback, cancel_flag):
            await on_segment_callback(seg(1, [W(" Hello", 10.0, 10.5)]), 100.0)

        async def snap_starts_to_speech(self, audio_path, spoken):
            FakeTranscriber.snapped = [s.index for s in spoken]
            return None

    ctx = PipelineContext(job_id="j", video_path="v.mkv", file_name="v.mkv")
    ctx.audio_wav_path = "a.wav"
    ctx.detect_onscreen_text = False
    monkeypatch.setattr(settings, "whisper_gap_recovery", False)
    monkeypatch.setattr(settings, "whisper_snap_starts", True)
    await TranscribeStage(FakeTranscriber()).execute(ctx)
    assert FakeTranscriber.snapped == [1]

    FakeTranscriber.snapped = None
    monkeypatch.setattr(settings, "whisper_snap_starts", False)
    await TranscribeStage(FakeTranscriber()).execute(ctx)
    assert FakeTranscriber.snapped is None
