import pytest
from app.models.schemas import SubtitleStreamInfo, AudioStreamInfo
from app.pipeline.context import PipelineContext
from app.pipeline.stages.audio_stage import AudioExtractionStage
from app.pipeline.stages.ocr_stage import (
    clean_embedded_dialogue_text,
    pick_dialogue_track,
)
from app.pipeline.stages.refine_stage import RefineStage
from app.pipeline.stages.transcribe_stage import TranscribeStage


def test_pick_dialogue_track_prefers_clean_over_sdh():
    streams = [
        SubtitleStreamInfo(index=0, codec_name="subrip", language="eng", title="English (SDH)", forced=False, default=False, text_based=True),
        SubtitleStreamInfo(index=1, codec_name="subrip", language="eng", title="English", forced=False, default=False, text_based=True),
        SubtitleStreamInfo(index=2, codec_name="subrip", language="eng", title="English [Forced]", forced=True, default=False, text_based=True),
        SubtitleStreamInfo(index=3, codec_name="subrip", language="eng", title="Director Commentary", forced=False, default=False, text_based=True),
    ]
    picked = pick_dialogue_track(streams, source_language="auto")
    assert picked is not None
    assert picked.index == 1
    assert picked.title == "English"


def test_pick_dialogue_track_matches_audio_stream_language():
    audio = [AudioStreamInfo(index=0, codec_name="aac", language="spa", title="Spanish Audio")]
    subs = [
        SubtitleStreamInfo(index=0, codec_name="subrip", language="eng", title="English", forced=False, text_based=True),
        SubtitleStreamInfo(index=1, codec_name="subrip", language="spa", title="Spanish", forced=False, text_based=True),
    ]
    picked = pick_dialogue_track(subs, source_language="auto", audio_streams=audio, selected_audio_stream=0)
    assert picked is not None
    assert picked.index == 1
    assert picked.language == "spa"


def test_pick_dialogue_track_foreign_and_explicit_selection():
    # Even when source_language="en", if only a foreign subtitle track (e.g. Spanish/French) is embedded,
    # it can still be picked when use_embedded_subtitles is enabled, or chosen explicitly by index.
    subs = [
        SubtitleStreamInfo(index=0, codec_name="subrip", language="fre", title="French", forced=False, text_based=True),
        SubtitleStreamInfo(index=1, codec_name="subrip", language="spa", title="Spanish", forced=False, text_based=True),
    ]
    picked_auto = pick_dialogue_track(subs, source_language="en")
    assert picked_auto is not None
    assert picked_auto.index == 0

    picked_explicit = pick_dialogue_track(subs, source_language="en", selected_subtitle_stream=1)
    assert picked_explicit is not None
    assert picked_explicit.index == 1
    assert picked_explicit.language == "spa"


@pytest.mark.asyncio
async def test_audio_stage_respects_use_embedded_subtitles_toggle(monkeypatch, tmp_path):
    import app.pipeline.stages.audio_stage as audio_mod
    import app.pipeline.stages.ocr_stage as ocr_mod

    extracted_audio_called = []

    async def fake_extract_audio_track(**kwargs):
        extracted_audio_called.append(kwargs)

    async def fake_extract_dialogue_track(video_path, stream, duration=0.0):
        return (
            [
                {
                    "index": i,
                    "start_time": float(i),
                    "end_time": float(i) + 1.0,
                    "original_text": f"Line {i}",
                    "translated_text": None,
                    "translation_failed": False,
                    "segment_type": "DIALOGUE",
                    "confidence": 1.0,
                }
                for i in range(1, 10)
            ],
            [],
        )

    monkeypatch.setattr(audio_mod, "extract_audio_track", fake_extract_audio_track)
    monkeypatch.setattr(ocr_mod, "extract_dialogue_track", fake_extract_dialogue_track)

    sub_stream = SubtitleStreamInfo(index=0, codec_name="subrip", language="spa", title="Spanish", forced=False, text_based=True)

    # Case 1: use_embedded_subtitles = False -> skips embedded subtitles and extracts audio for Whisper
    ctx_whisper = PipelineContext(
        job_id="job_whisper",
        video_path=str(tmp_path / "video.mkv"),
        file_name="video.mkv",
        duration=60.0,
        subtitle_streams=[sub_stream],
        use_embedded_subtitles=False,
    )
    stage = AudioExtractionStage()
    await stage.execute(ctx_whisper)
    assert len(extracted_audio_called) == 1
    assert ctx_whisper.embedded_dialogue_segments == []

    # Case 2: use_embedded_subtitles = True -> extracts embedded subtitles and skips audio extraction
    extracted_audio_called.clear()
    ctx_embedded = PipelineContext(
        job_id="job_embedded",
        video_path=str(tmp_path / "video.mkv"),
        file_name="video.mkv",
        duration=60.0,
        subtitle_streams=[sub_stream],
        use_embedded_subtitles=True,
    )
    await stage.execute(ctx_embedded)
    assert len(extracted_audio_called) == 0
    assert len(ctx_embedded.embedded_dialogue_segments) == 9
    assert ctx_embedded.detected_language == "es"
    assert ctx_embedded.effective_source_language == "spa"


def test_clean_embedded_dialogue_text():
    # Pure music line removed
    assert clean_embedded_dialogue_text("♪ (THEME MUSIC PLAYING) ♪", is_sdh=True) == ""
    assert clean_embedded_dialogue_text("♪♪♪") == ""

    # SDH sound effects stripped while keeping dialogue
    sdh_line = "[stammers] I don't know what to say.\n[sighs] - [TRAVIS] Just run."
    cleaned = clean_embedded_dialogue_text(sdh_line, is_sdh=True)
    assert "stammers" not in cleaned
    assert "sighs" not in cleaned
    assert "TRAVIS" not in cleaned
    assert "I don't know what to say." in cleaned
    assert "- Just run." in cleaned

    # Tags stripped
    tagged = "<i>Look out!</i>\n{\\an8}[TOP TITLE]"
    assert clean_embedded_dialogue_text(tagged) == "Look out!\n[TOP TITLE]"


@pytest.mark.asyncio
async def test_transcribe_and_refine_stages_with_embedded_segments():
    ctx = PipelineContext(
        job_id="test_job",
        video_path="fake.mkv",
        file_name="fake.mkv",
        duration=120.0,
        comms_boost=False,
    )
    # Simulate an extracted embedded dialogue track with a dual-speaker cue
    ctx.embedded_dialogue_segments = [
        {
            "index": 1,
            "start_time": 10.0,
            "end_time": 14.0,
            "original_text": "Did you see that? -Yes, I did.",
            "translated_text": None,
            "translation_failed": False,
            "segment_type": "DIALOGUE",
            "confidence": 1.0,
        },
        {
            "index": 2,
            "start_time": 15.0,
            "end_time": 18.0,
            "original_text": "We need to check the mesa.",
            "translated_text": None,
            "translation_failed": False,
            "segment_type": "DIALOGUE",
            "confidence": 1.0,
        },
    ]
    ctx.onscreen_segments = [
        {
            "start_time": 5.0,
            "end_time": 8.0,
            "original_text": "[SKINWALKER RANCH]",
            "translated_text": None,
            "translation_failed": False,
            "segment_type": "ON_SCREEN",
            "confidence": 1.0,
        }
    ]

    # TranscribeStage should skip Whisper completely
    transcribe = TranscribeStage()
    await transcribe.execute(ctx)
    assert len(ctx.raw_segments) == 2
    assert ctx.raw_segments[0].text == "Did you see that? -Yes, I did."

    # RefineStage should preserve exact human timing, split dual speakers, and merge on-screen
    refine = RefineStage()
    await refine.execute(ctx)

    assert len(ctx.refined_segments) == 4  # 1 on-screen + 2 split speakers from seg 1 + 1 seg 2
    types = [s["segment_type"] for s in ctx.refined_segments]
    texts = [s["original_text"] for s in ctx.refined_segments]

    assert types[0] == "ON_SCREEN"
    assert texts[0] == "[SKINWALKER RANCH]"
    assert types[1] == "DIALOGUE"
    assert texts[1] == "Did you see that?"
    assert types[2] == "DIALOGUE"
    assert texts[2] == "Yes, I did."
    assert types[3] == "DIALOGUE"
    assert texts[3] == "We need to check the mesa."
