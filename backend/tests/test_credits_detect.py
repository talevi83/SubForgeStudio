"""Tests for smart end-credits detection and mid/post-credits scene preservation."""
from pathlib import Path
from types import SimpleNamespace

from app.pipeline.credits_detect import (
    detect_visual_credit_spans,
    filter_credits_segments,
    filter_frames_for_credits,
    is_credit_roll_thumbnail,
    is_explicit_credit_text,
    is_music_or_lyric_cue,
    is_stinger_narrative_card,
    parse_chapter_spans,
)


def _make_thumb(bg_luma: int = 10, text_luma: int = 220, text_fraction: float = 0.05) -> bytes:
    """Create a synthetic 64x36 grayscale thumbnail."""
    w, h = 64, 36
    pixels = bytearray([bg_luma] * (w * h))
    target_bright = int(w * h * text_fraction)
    placed = 0
    for row in range(4, h - 4, 2):
        for col in range(w // 4, (3 * w) // 4, 2):
            if placed >= target_bright:
                break
            pixels[row * w + col] = text_luma
            placed += 1
        if placed >= target_bright:
            break
    return bytes(pixels)


def test_is_credit_roll_thumbnail_distinguishes_credits_from_live_scene():
    credit_thumb = _make_thumb(bg_luma=12, text_luma=230, text_fraction=0.06)
    assert is_credit_roll_thumbnail(credit_thumb) is True

    # Bright live-action frame
    live_thumb = _make_thumb(bg_luma=110, text_luma=200, text_fraction=0.10)
    assert is_credit_roll_thumbnail(live_thumb) is False

    # Pure black frame (no text at all)
    pure_black = _make_thumb(bg_luma=5, text_luma=5, text_fraction=0.0)
    assert is_credit_roll_thumbnail(pure_black) is False


def test_detect_visual_credit_spans_splits_around_mid_credits_scene():
    duration = 3600.0  # 60-minute movie
    interval = 2.0
    n_frames = int(duration / interval)
    live_thumb = _make_thumb(bg_luma=105, text_luma=180, text_fraction=0.08)
    credit_thumb = _make_thumb(bg_luma=10, text_luma=225, text_fraction=0.05)

    # Normal movie up to 50:00 (3000s), credits 3000..3180s, mid-credits live scene 3180..3260s,
    # credits resume 3260..3540s, post-credits live scene 3540..3600s.
    chunks = []
    for i in range(n_frames):
        t = i * interval
        if 3000.0 <= t < 3180.0 or 3260.0 <= t < 3540.0:
            chunks.append(credit_thumb)
        else:
            chunks.append(live_thumb)

    spans = detect_visual_credit_spans(b"".join(chunks), interval=interval, duration=duration)
    assert spans == [(3000.0, 3180.0), (3260.0, 3540.0)]


def test_parse_chapter_spans_separates_credits_and_post_credits_scenes():
    duration = 7200.0
    chapters = [
        {"start_time": 0.0, "end_time": 6000.0, "title": "Main Feature"},
        {"start_time": 6000.0, "end_time": 6240.0, "title": "End Credits"},
        {"start_time": 6240.0, "end_time": 6360.0, "title": "Mid-Credits Scene"},
        {"start_time": 6360.0, "end_time": 7080.0, "title": "Closing Credits"},
        {"start_time": 7080.0, "end_time": 7200.0, "title": "Post-Credits Stinger"},
    ]
    credit_spans, scene_spans = parse_chapter_spans(chapters, duration)
    assert credit_spans == [(6000.0, 6240.0), (6360.0, 7080.0)]
    assert scene_spans == [(6240.0, 6360.0), (7080.0, 7200.0)]


def test_explicit_credit_and_stinger_card_classification():
    assert is_explicit_credit_text("Directed by Jon Favreau") is True
    assert is_explicit_credit_text("[EXECUTIVE PRODUCERS — KEVIN FEIGE]") is True
    assert is_explicit_credit_text("Subtitles by SDI Media Group") is True
    assert is_explicit_credit_text("Synced and corrected by Callisto") is True
    assert is_explicit_credit_text("Thank you for watching!") is True

    assert is_stinger_narrative_card("[THOR WILL RETURN]") is True
    assert is_stinger_narrative_card("[DOCTOR STRANGE WILL RETURN IN AVENGERS]") is True
    assert is_stinger_narrative_card("[SIX MONTHS LATER]") is True
    assert is_explicit_credit_text("[THOR WILL RETURN]") is False

    assert is_music_or_lyric_cue("♪ Walking in the shadows ♪") is True
    assert is_music_or_lyric_cue("Yeah yeah yeah yeah") is True


def test_marvel_style_credits_with_mid_and_post_credits_scenes_preserved():
    """
    Simulate a 2-hour movie (7200s):
    - 0..6300s: main movie dialogue
    - 6300..6450s: main-on-end credits cards + end credits song lyrics
    - 6480..6540s: Mid-credits scene (with a location ON_SCREEN card + 3 dialogue lines)
    - 6560..7050s: scrolling credits + second song lyrics + subtitle vendor credit
    - 7080..7180s: Post-credits dialogue scene + "[THOR WILL RETURN]" card
    """
    duration = 7200.0
    segments = [
        # Main feature
        {"index": 1, "start_time": 120.0, "end_time": 123.0, "original_text": "We have to stop the reactor now.", "segment_type": "DIALOGUE"},
        {"index": 2, "start_time": 6250.0, "end_time": 6254.0, "original_text": "We did it. The city is safe.", "segment_type": "DIALOGUE"},
        # Credits roll #1 + song lyrics
        {"index": 3, "start_time": 6305.0, "end_time": 6309.0, "original_text": "[DIRECTED BY SHANE BLACK]", "segment_type": "ON_SCREEN"},
        {"index": 4, "start_time": 6312.0, "end_time": 6316.0, "original_text": "[PRODUCED BY KEVIN FEIGE]", "segment_type": "ON_SCREEN"},
        {"index": 5, "start_time": 6320.0, "end_time": 6324.0, "original_text": "♪ Shining through the darkest night ♪", "segment_type": "DIALOGUE"},
        {"index": 6, "start_time": 6325.0, "end_time": 6329.0, "original_text": "Rising like a fire in the sky", "segment_type": "DIALOGUE"},
        {"index": 7, "start_time": 6330.0, "end_time": 6334.0, "original_text": "Never gonna fade away tonight", "segment_type": "DIALOGUE"},
        {"index": 8, "start_time": 6335.0, "end_time": 6339.0, "original_text": "Rising like a fire in the sky", "segment_type": "DIALOGUE"},
        # Mid-credits scene (6480..6540s)
        {"index": 9, "start_time": 6478.0, "end_time": 6482.0, "original_text": "[S.H.I.E.L.D. FACILITY — NEW MEXICO]", "segment_type": "ON_SCREEN"},
        {"index": 10, "start_time": 6480.0, "end_time": 6483.5, "original_text": "Sir, what did you find in the crater?", "segment_type": "DIALOGUE"},
        {"index": 11, "start_time": 6484.0, "end_time": 6488.0, "original_text": "I don't know, Agent Coulson, but it isn't from Earth.", "segment_type": "DIALOGUE"},
        {"index": 12, "start_time": 6489.0, "end_time": 6492.0, "original_text": "Call the Director immediately.", "segment_type": "DIALOGUE"},
        # Credits roll #2 + vendor credit + hallucination
        {"index": 13, "start_time": 6600.0, "end_time": 6604.0, "original_text": "[VISUAL EFFECTS SUPERVISOR — JOHN KNOLL]", "segment_type": "ON_SCREEN"},
        {"index": 14, "start_time": 6700.0, "end_time": 6704.0, "original_text": "Ooh baby yeah yeah", "segment_type": "DIALOGUE"},
        {"index": 15, "start_time": 6705.0, "end_time": 6709.0, "original_text": "Dancing in the moonlight", "segment_type": "DIALOGUE"},
        {"index": 16, "start_time": 6710.0, "end_time": 6714.0, "original_text": "Ooh baby yeah yeah", "segment_type": "DIALOGUE"},
        {"index": 17, "start_time": 7010.0, "end_time": 7015.0, "original_text": "Subtitles by SDI Media", "segment_type": "DIALOGUE"},
        {"index": 18, "start_time": 7020.0, "end_time": 7024.0, "original_text": "Thank you for watching", "segment_type": "DIALOGUE"},
        # Post-credits scene (7080..7185s)
        {"index": 19, "start_time": 7080.0, "end_time": 7083.0, "original_text": "- You're still awake?\n- I couldn't sleep.", "segment_type": "DIALOGUE"},
        {"index": 20, "start_time": 7084.0, "end_time": 7088.0, "original_text": "Wait, did you hear that sound outside?", "segment_type": "DIALOGUE"},
        {"index": 21, "start_time": 7180.0, "end_time": 7185.0, "original_text": "[THOR WILL RETURN]", "segment_type": "ON_SCREEN"},
    ]

    kept, removed = filter_credits_segments(segments, duration=duration)
    assert removed == 12
    kept_texts = [s["original_text"] for s in kept]
    assert kept_texts == [
        "We have to stop the reactor now.",
        "We did it. The city is safe.",
        "[S.H.I.E.L.D. FACILITY — NEW MEXICO]",
        "Sir, what did you find in the crater?",
        "I don't know, Agent Coulson, but it isn't from Earth.",
        "Call the Director immediately.",
        "- You're still awake?\n- I couldn't sleep.",
        "Wait, did you hear that sound outside?",
        "[THOR WILL RETURN]",
    ]
    # Indices must be re-numbered 1..N
    assert [s["index"] for s in kept] == list(range(1, len(kept) + 1))


def test_filter_frames_for_credits_skips_scrolling_credits_but_keeps_static_stinger():
    duration = 3600.0
    chapters = [{"start_time": 3000.0, "end_time": 3600.0, "title": "End Credits"}]
    frames = [
        SimpleNamespace(time=1200.0, static_until=1202.0, path=Path("f1.jpg")),
        # Scrolling credit frames (static_until - time == 1.0s)
        SimpleNamespace(time=3100.0, static_until=3101.0, path=Path("f2.jpg")),
        SimpleNamespace(time=3200.0, static_until=3201.0, path=Path("f3.jpg")),
        # Static stinger card held for 5 seconds ("THOR WILL RETURN")
        SimpleNamespace(time=3580.0, static_until=3585.0, path=Path("f4.jpg")),
    ]
    kept, dropped = filter_frames_for_credits(frames, duration=duration, chapters=chapters)
    assert dropped == 2
    assert [f.time for f in kept] == [1200.0, 3580.0]


def test_hiphop_end_credits_song_with_visual_credit_spans_is_filtered():
    """
    Regression test for How High 2 (5318.1s):
    - Movie ends around 5075s; hip-hop/rap song ("Forever Young") starts at 5079.4s
      with punctuated rap verses ("It's on now, we forever young.", "The music's loud and I'm feeling wild.")
      followed by a chorus ("But we could be together forever", "Forever", "And ever") while visual
      credit cards/scroll run from 5098.0s to 5308.0s.
    """
    duration = 5318.1
    visual_spans = [(5098.0, 5308.0)]
    segments = [
        # Last real movie scene (6 MONTHS LATER)
        {"index": 1, "start_time": 5010.0, "end_time": 5013.0, "original_text": "[6 MONTHS LATER]", "segment_type": "ON_SCREEN"},
        {"index": 2, "start_time": 5068.0, "end_time": 5072.0, "original_text": "We finally made it, man. What's next for us?", "segment_type": "DIALOGUE"},
        {"index": 3, "start_time": 5072.5, "end_time": 5075.5, "original_text": "Whatever we want, bro.", "segment_type": "DIALOGUE"},
        # End-credits song starting at 5079.4s and running through visual credits (5098-5308s)
        {"index": 4, "start_time": 5079.4, "end_time": 5082.3, "original_text": "It's on now, we forever young.", "segment_type": "DIALOGUE"},
        {"index": 5, "start_time": 5083.3, "end_time": 5084.9, "original_text": "The music's loud and I'm feeling wild.", "segment_type": "DIALOGUE"},
        {"index": 6, "start_time": 5085.2, "end_time": 5086.5, "original_text": "Come dance along with the golden child.", "segment_type": "DIALOGUE"},
        {"index": 7, "start_time": 5086.9, "end_time": 5088.4, "original_text": "I don't know much about you, let me check your files.", "segment_type": "DIALOGUE"},
        {"index": 8, "start_time": 5096.9, "end_time": 5100.2, "original_text": "But we could be together forever", "segment_type": "DIALOGUE"},
        {"index": 9, "start_time": 5100.2, "end_time": 5103.4, "original_text": "We could be together", "segment_type": "DIALOGUE"},
        {"index": 10, "start_time": 5105.0, "end_time": 5106.4, "original_text": "Forever", "segment_type": "DIALOGUE"},
        {"index": 11, "start_time": 5108.3, "end_time": 5110.4, "original_text": "And ever", "segment_type": "DIALOGUE"},
        {"index": 12, "start_time": 5110.8, "end_time": 5114.4, "original_text": "I wanna live life with you", "segment_type": "DIALOGUE"},
        {"index": 13, "start_time": 5115.7, "end_time": 5116.7, "original_text": "Forever", "segment_type": "DIALOGUE"},
        {"index": 14, "start_time": 5167.2, "end_time": 5170.0, "original_text": "But we could be together forever", "segment_type": "DIALOGUE"},
        {"index": 15, "start_time": 5170.0, "end_time": 5172.9, "original_text": "We could be together", "segment_type": "DIALOGUE"},
        {"index": 16, "start_time": 5172.9, "end_time": 5177.1, "original_text": "I wanna live life with you", "segment_type": "DIALOGUE"},
        {"index": 17, "start_time": 5178.2, "end_time": 5179.2, "original_text": "Forever", "segment_type": "DIALOGUE"},
        {"index": 18, "start_time": 5182.4, "end_time": 5187.0, "original_text": "Forever, forever, forever", "segment_type": "DIALOGUE"},
        {"index": 19, "start_time": 5187.0, "end_time": 5189.2, "original_text": "Forever young", "segment_type": "DIALOGUE"},
        {"index": 20, "start_time": 5232.5, "end_time": 5232.6, "original_text": "Hey!", "segment_type": "DIALOGUE"},
    ]

    kept, removed = filter_credits_segments(
        segments,
        duration=duration,
        visual_credit_spans=visual_spans,
    )
    assert removed == 17
    assert [s["original_text"] for s in kept] == [
        "[6 MONTHS LATER]",
        "We finally made it, man. What's next for us?",
        "Whatever we want, bro.",
    ]

