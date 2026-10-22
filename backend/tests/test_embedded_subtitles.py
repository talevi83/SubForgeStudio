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
