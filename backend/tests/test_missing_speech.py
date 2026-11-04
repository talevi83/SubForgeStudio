import pytest
from app.models.db_models import SegmentType
from app.pipeline.context import RawSegment
from app.pipeline.missing_speech import supplement_missing_speech


def test_dialogue_overlap_is_suppressed_no_duplicates():
    """Verify that speech overlapping existing dialogue is suppressed and does not duplicate."""
    existing = [
        {
            "index": 1,
            "start_time": 10.0,
            "end_time": 13.0,
            "original_text": "Hello there, general.",
            "translated_text": None,
            "segment_type": SegmentType.DIALOGUE.value,
        }
    ]
    # Whisper heard the same speech slightly shifted
    whisper_segs = [
        RawSegment(
            index=1,
            start_time=10.2,
            end_time=12.8,
            text="Hello there, general.",
            confidence=0.95,
            words=[],
        )
    ]

    merged, count = supplement_missing_speech(existing, whisper_segs, same_language=True)
    assert count == 0
    assert len(merged) == 1
    assert merged[0]["original_text"] == "Hello there, general."


def test_gap_speech_is_supplemented():
    """Verify that clear speech in a gap where no subtitle exists is recovered."""
    existing = [
        {
            "index": 1,
            "start_time": 5.0,
            "end_time": 8.0,
            "original_text": "We are leaving now.",
            "translated_text": None,
            "segment_type": SegmentType.DIALOGUE.value,
        },
        {
            "index": 2,
            "start_time": 20.0,
            "end_time": 23.0,
            "original_text": "See you at headquarters.",
            "translated_text": None,
            "segment_type": SegmentType.DIALOGUE.value,
        },
    ]

    # Radio comms speech in the gap between 8.0s and 20.0s
    whisper_segs = [
        RawSegment(
            index=1,
            start_time=12.0,
            end_time=14.5,
            text="Base, do you copy? Radio check.",
            confidence=0.88,
            words=[],
        )
    ]

    merged, count = supplement_missing_speech(existing, whisper_segs, same_language=True)
    assert count == 1
    assert len(merged) == 3
    # Sorted and re-indexed
    assert merged[0]["index"] == 1
    assert merged[1]["index"] == 2
    assert merged[1]["start_time"] == 12.0
    assert "Base, do you copy" in merged[1]["original_text"]
    assert merged[1]["segment_type"] == SegmentType.COMMS.value  # recognized as comms keyword
    assert merged[2]["index"] == 3


def test_onscreen_title_coexistence_not_suppressed():
    """Verify that speech occurring during an ON_SCREEN title is recovered without duplicating or clipping."""
    existing = [
        {
            "index": 1,
            "start_time": 10.0,
            "end_time": 14.0,
            "original_text": "[MOSCOW, 1985]",
            "translated_text": None,
            "segment_type": SegmentType.ON_SCREEN.value,
        }
    ]

    # Someone speaking on radio while the on-screen location title is displayed
    whisper_segs = [
        RawSegment(
            index=1,
            start_time=10.5,
            end_time=13.0,
            text="Team Two, initiate extraction.",
            confidence=0.91,
            words=[],
        )
    ]

    merged, count = supplement_missing_speech(existing, whisper_segs, same_language=True)
    assert count == 1
    assert len(merged) == 2
    # The on-screen title is preserved
    assert any(s["segment_type"] == SegmentType.ON_SCREEN.value for s in merged)
    # The comms line is added
    comms_line = next(s for s in merged if s["segment_type"] != SegmentType.ON_SCREEN.value)
    assert "Team Two, initiate extraction" in comms_line["original_text"]
    assert comms_line["start_time"] == 10.5


def test_hallucinations_and_noise_are_filtered():
    """Verify that repetition loops, single syllables, and sound tokens are rejected."""
    existing = []
    whisper_segs = [
        RawSegment(index=1, start_time=1.0, end_time=2.0, text="[Music]", confidence=0.9, words=[]),
        RawSegment(index=2, start_time=3.0, end_time=4.0, text="Thank you. Thank you. Thank you. Thank you.", confidence=0.3, words=[]),
        RawSegment(index=3, start_time=5.0, end_time=5.2, text="Oh", confidence=0.35, words=[]),
    ]

    merged, count = supplement_missing_speech(existing, whisper_segs)
    assert count == 0
    assert len(merged) == 0


def test_clamping_prevents_collision_with_adjacent_dialogue():
    """Verify that recovered segments are clamped so they do not collide with adjacent dialogue."""
    existing = [
        {
            "index": 1,
            "start_time": 10.0,
            "end_time": 12.0,
            "original_text": "First line.",
            "segment_type": SegmentType.DIALOGUE.value,
        },
        {
            "index": 2,
            "start_time": 15.0,
            "end_time": 17.0,
            "original_text": "Second line.",
            "segment_type": SegmentType.DIALOGUE.value,
        },
    ]

    # Whisper segment slightly stretches too early into First line and too late into Second line
    whisper_segs = [
        RawSegment(
            index=1,
            start_time=11.9,  # overlaps First line's end
            end_time=15.1,    # overlaps Second line's start
            text="A quick transmission in between.",
            confidence=0.85,
            words=[],
        )
    ]

    min_gap = 0.04
    merged, count = supplement_missing_speech(existing, whisper_segs, min_gap=min_gap)
    assert count == 1
    recovered_seg = merged[1]
    assert recovered_seg["start_time"] >= round(12.0 + min_gap, 3)
    assert recovered_seg["end_time"] <= round(15.0 - min_gap, 3)


def test_cross_language_translated_suppression():
    """Verify that in translated mode (e.g. Hebrew existing sub), overlapping speech is suppressed, but gap speech is added."""
    existing = [
        {
            "index": 1,
            "start_time": 2.0,
            "end_time": 4.0,
            "original_text": "Hello",
            "translated_text": "שלום לכולם",
            "segment_type": SegmentType.DIALOGUE.value,
        }
    ]

    whisper_segs = [
        # Overlaps Hebrew sub -> should be suppressed (no duplicate)
        RawSegment(
            index=1,
            start_time=2.1,
            end_time=3.9,
            text="Hello everyone",
            confidence=0.92,
            words=[],
        ),
        # Gap speech at 10s -> should be recovered!
        RawSegment(
            index=2,
            start_time=10.0,
            end_time=12.5,
            text="Dispatch, we need backup immediately.",
            confidence=0.89,
            words=[],
        ),
    ]

    merged, count = supplement_missing_speech(existing, whisper_segs, same_language=False)
    assert count == 1
    assert len(merged) == 2
    assert merged[0]["translated_text"] == "שלום לכולם"
    assert "Dispatch" in merged[1]["original_text"]


def test_glued_whisper_segment_recovers_uncovered_sentence():
    """If Whisper grouped an already-subtitled sentence and a missing sentence into one RawSegment,
    the uncovered words after the pause are extracted and recovered instead of suppressed."""
    from app.pipeline.context import TranscribedWord

    existing = [
        {
            "index": 1,
            "start_time": 10.0,
            "end_time": 12.0,
            "original_text": "Hey Pete, thanks for jumping on.",
            "translated_text": None,
            "segment_type": SegmentType.DIALOGUE.value,
        }
    ]
    W = TranscribedWord
    whisper_segs = [
        RawSegment(
            index=1,
            start_time=10.0,
            end_time=15.5,
            text="Hey Pete, thanks for jumping on. Can you hear me on the radio?",
            confidence=0.9,
            words=[
                W(" Hey", 10.0, 10.3, 0.9),
                W(" Pete,", 10.35, 10.8, 0.9),
                W(" thanks", 10.9, 11.2, 0.9),
                W(" for", 11.25, 11.5, 0.9),
                W(" jumping", 11.55, 11.8, 0.9),
                W(" on.", 11.85, 12.0, 0.9),
                W(" Can", 12.8, 13.1, 0.9),
                W(" you", 13.15, 13.4, 0.9),
                W(" hear", 13.45, 13.8, 0.9),
                W(" me", 13.85, 14.1, 0.9),
                W(" on", 14.15, 14.4, 0.9),
                W(" the", 14.45, 14.7, 0.9),
                W(" radio?", 14.75, 15.5, 0.9),
            ],
        )
    ]

    merged, count = supplement_missing_speech(existing, whisper_segs, same_language=True)
    assert count == 1
    assert len(merged) == 2
    assert merged[0]["original_text"] == "Hey Pete, thanks for jumping on."
    assert merged[1]["original_text"] == "Can you hear me on the radio?"
    assert merged[1]["start_time"] == pytest.approx(12.8)


