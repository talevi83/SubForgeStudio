"""Unit tests for splitting multi-speaker subtitles into single-speaker segments."""
from app.utils.speaker_split import (
    extract_speaker_turns,
    split_dual_speaker_segment,
    split_dual_speaker_segments,
)


def test_extract_speaker_turns_two_leading_dashes():
    header, turns = extract_speaker_turns("- היא הבת שלי, טור.\n- היא בנילאית, דאריוס.")
    assert header is None
    assert turns == ["היא הבת שלי, טור.", "היא בנילאית, דאריוס."]


def test_extract_speaker_turns_second_line_dash_only():
    header, turns = extract_speaker_turns("תפגין קצת כבוד.\n- אני מביע המון כבוד, אבל זה רעיון רע.")
    assert header is None
    assert turns == ["תפגין קצת כבוד.", "אני מביע המון כבוד, אבל זה רעיון רע."]


def test_extract_speaker_turns_inline_dash_after_punctuation():
    header, turns = extract_speaker_turns("אני בסדר, רק עייפה.\nאגיע לתחנה מחר. - בסדר.")
    assert header is None
    assert turns == ["אני בסדר, רק עייפה.\nאגיע לתחנה מחר.", "בסדר."]


def test_extract_speaker_turns_preserves_header_caption():
    header, turns = extract_speaker_turns("[דו״ח משטרה]\n- היי, דיק.\n- בת'.")
    assert header == "[דו״ח משטרה]"
    assert turns == ["היי, דיק.", "בת'."]


def test_extract_speaker_turns_ignores_centered_titles_and_brackets():
    _, turns1 = extract_speaker_turns("- קיץ 2018 -")
    assert len(turns1) == 1
    _, turns2 = extract_speaker_turns("[POLICE REPORT - CALWELL]")
    assert len(turns2) == 1
    _, turns3 = extract_speaker_turns("ששיבש את נתוני ה-GPR של יאן.")
    assert len(turns3) == 1


def test_split_dual_speaker_segment_matches_english_and_hebrew_turns():
    seg = {
        "id": 42,
        "index": 1,
        "start_time": 31.49,
        "end_time": 34.49,
        "original_text": "She's my daughter, Thor.\nShe's a half-breed, Darius.",
        "translated_text": "- היא הבת שלי, טור.\n- היא בנילאית, דאריוס.",
        "translation_failed": False,
        "segment_type": "DIALOGUE",
        "confidence": 0.95,
        "cps": 12.0,
    }
    split = split_dual_speaker_segment(seg, min_gap=0.08)
    assert len(split) == 2
    assert split[0]["original_text"] == "She's my daughter, Thor."
    assert split[0]["translated_text"] == "היא הבת שלי, טור."
    assert split[1]["original_text"] == "She's a half-breed, Darius."
    assert split[1]["translated_text"] == "היא בנילאית, דאריוס."
    assert split[0]["start_time"] == 31.49
    assert split[1]["end_time"] == 34.49
    assert split[1]["start_time"] - split[0]["end_time"] >= 0.079
    assert "id" not in split[0]


def test_split_dual_speaker_segment_handles_no_period_sentence():
    seg = {
        "index": 300,
        "start_time": 2313.41,
        "end_time": 2315.31,
        "original_text": "No. Perhaps when you return?",
        "translated_text": "- לא.\n- אולי שתחזור?",
        "segment_type": "DIALOGUE",
    }
    split = split_dual_speaker_segments([seg])
    assert len(split) == 2
    assert [s["index"] for s in split] == [1, 2]
    assert [s["original_text"] for s in split] == ["No.", "Perhaps when you return?"]
    assert [s["translated_text"] for s in split] == ["לא.", "אולי שתחזור?"]


def test_heal_split_sentences_pulls_orphan_ending_word_back():
    """When segment N ends mid-sentence and segment N+1 starts with the final word + period
    followed by a new sentence, heal_split_sentences moves that final word back to segment N."""
    segs = [
        {
            "index": 14,
            "start_time": 47.92,
            "end_time": 52.86,
            "original_text": "so you see that feature right here\nyeah i'm not sure what",
            "translated_text": "אז אתה רואה את המאפיין הזה ממש כאן?\nכן, אני לא בטוח מה",
            "segment_type": "DIALOGUE",
        },
        {
            "index": 15,
            "start_time": 52.94,
            "end_time": 56.51,
            "original_text": "this is what in the world is that\nthat's gotta be There is a",
            "translated_text": "זה. מה לעזאזל זה?\nזה חייב להיות... יש כאן",
            "segment_type": "DIALOGUE",
        },
        {
            "index": 16,
            "start_time": 56.60,
            "end_time": 59.17,
            "original_text": "metallic something or other that is\npretty reflective",
            "translated_text": "משהו מתכתי כזה או אחר\nשהוא די מחזיר אור.",
            "segment_type": "DIALOGUE",
        },
    ]
    healed = split_dual_speaker_segments(segs)
    assert healed[0]["translated_text"].replace("\n", " ").endswith("אני לא בטוח מה זה.")
    assert healed[1]["translated_text"].startswith("מה לעזאזל זה?")
    assert not healed[1]["translated_text"].replace("\n", " ").endswith("יש כאן")
    assert healed[2]["translated_text"].startswith("יש כאן משהו מתכתי")


def test_heal_split_sentences_handles_multiword_tail_and_trailing_lead():
    segs = [
        {
            "index": 129,
            "start_time": 635.72,
            "end_time": 637.40,
            "original_text": "I really can't make",
            "translated_text": "אני באמת לא יכולה להגיע",
            "segment_type": "DIALOGUE",
        },
        {
            "index": 130,
            "start_time": 637.48,
            "end_time": 640.30,
            "original_text": "it tonight. You getting busy with someone?",
            "translated_text": "הלילה. את עסוקה עם מישהו?",
            "segment_type": "DIALOGUE",
        },
    ]
    healed = split_dual_speaker_segments(segs)
    assert healed[0]["translated_text"].replace("\n", " ") == "אני באמת לא יכולה להגיע הלילה."
    assert healed[0]["original_text"].replace("\n", " ") == "I really can't make it tonight."
    assert healed[1]["translated_text"].replace("\n", " ") == "את עסוקה עם מישהו?"
    assert healed[1]["original_text"].replace("\n", " ") == "You getting busy with someone?"


def test_reattach_orphan_tails_and_word_snap_guard():
    from app.pipeline.context import RawSegment, TranscribedWord
    from app.pipeline.stages.refine_stage import reattach_orphan_tails
    from app.pipeline.word_snap import snap_word_starts

    # 1. snap_word_starts does not snap a sentence-ending word ("officials.") across a pause
    # when preceded by an unpunctuated word ("Military") and followed by another sentence.
    seg = RawSegment(
        index=1,
        start_time=10.0,
        end_time=15.0,
        text="Military officials. I thought cooing would be easier.",
        confidence=0.95,
        words=[
            TranscribedWord(" Military", 10.0, 10.5, 0.95),
            TranscribedWord(" officials.", 10.5, 12.6, 0.95),  # stretched across silence 10.9..12.2
            TranscribedWord(" I", 12.6, 12.8, 0.95),
            TranscribedWord(" thought", 12.8, 13.2, 0.95),
        ],
    )
    snap_word_starts([seg], [(10.0, 10.9), (12.2, 15.0)])
    assert seg.words[1].start == 10.5
    assert seg.words[1].end <= 12.1

    # 2. reattach_orphan_tails moves "it tonight." back to "I really can't make"
    s1 = RawSegment(
        index=1,
        start_time=635.72,
        end_time=637.16,
        text="I really can't make",
        confidence=0.95,
        words=[
            TranscribedWord(" I", 635.72, 636.0, 0.95),
            TranscribedWord(" really", 636.0, 636.4, 0.95),
            TranscribedWord(" can't", 636.4, 636.8, 0.95),
            TranscribedWord(" make", 636.8, 637.16, 0.95),
        ],
    )
    s2 = RawSegment(
        index=2,
        start_time=637.20,
        end_time=640.0,
        text="it tonight. You getting busy?",
        confidence=0.95,
        words=[
            TranscribedWord(" it", 637.20, 637.35, 0.95),
            TranscribedWord(" tonight.", 637.35, 637.80, 0.95),
            TranscribedWord(" You", 638.10, 638.40, 0.95),
            TranscribedWord(" getting", 638.40, 638.90, 0.95),
            TranscribedWord(" busy?", 638.90, 640.0, 0.95),
        ],
    )
    fixed = reattach_orphan_tails([s1, s2])
    assert fixed[0].text == "I really can't make it tonight."
    assert fixed[1].text == "You getting busy?"


def test_extract_speaker_turns_compact_inline_dash():
    header, turns = extract_speaker_turns("and finally give us\nsome clear data. -Wow.")
    assert header is None
    assert turns == ["and finally give us\nsome clear data.", "Wow."]

    _, turns2 = extract_speaker_turns("So, you think\nthat'll take all day? -Yeah.")
    assert turns2 == ["So, you think\nthat'll take all day?", "Yeah."]


def test_extract_speaker_turns_trailing_dash_before_newline():
    header, turns = extract_speaker_turns("I-I don't know. -\nI mean, that could be it.")
    assert header is None
    assert turns == ["I-I don't know.", "I mean, that could be it."]

