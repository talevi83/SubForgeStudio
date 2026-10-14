import pytest
from app.pipeline.context import RawSegment, TranscribedWord
from app.pipeline.stages.refine_stage import refine_raw_segments


def test_refine_empty_segments():
    assert refine_raw_segments([]) == []


def test_refine_single_short_segment():
    raw = [
        RawSegment(
            index=1,
            start_time=1.0,
            end_time=3.0,
            text="Short dialogue line.",
            confidence=0.95,
        )
    ]
    refined = refine_raw_segments(raw)
    assert len(refined) == 1
    assert refined[0]["index"] == 1
    assert refined[0]["start_time"] == 1.0
    assert refined[0]["end_time"] == 3.0
    assert refined[0]["original_text"] == "Short dialogue line."
    assert refined[0]["cps"] > 0


def test_refine_enforces_min_duration():
    raw = [
        RawSegment(
            index=1,
            start_time=1.0,
            end_time=1.2,  # Only 0.2s
            text="Hi.",
            confidence=0.99,
        )
    ]
    refined = refine_raw_segments(raw, min_duration=0.8)
    assert len(refined) == 1
    duration = refined[0]["end_time"] - refined[0]["start_time"]
    assert duration >= 0.8


def test_refine_splits_long_segment_with_word_timestamps():
    words = [
        TranscribedWord(word="We", start=0.0, end=0.4),
        TranscribedWord(word="need", start=0.5, end=0.8),
        TranscribedWord(word="to", start=0.9, end=1.1),
        TranscribedWord(word="secure", start=1.2, end=1.7),
        TranscribedWord(word="the", start=1.8, end=2.0),
        TranscribedWord(word="entire", start=2.1, end=2.6),
        TranscribedWord(word="perimeter", start=2.7, end=3.3),
        TranscribedWord(word="before", start=3.4, end=3.8),
        TranscribedWord(word="nightfall", start=3.9, end=4.5),
        TranscribedWord(word="because", start=4.6, end=5.1),
        TranscribedWord(word="the", start=5.2, end=5.3),
        TranscribedWord(word="hostile", start=5.4, end=5.9),
        TranscribedWord(word="forces", start=6.0, end=6.5),
        TranscribedWord(word="are", start=6.6, end=6.8),
        TranscribedWord(word="approaching", start=6.9, end=7.6),
        TranscribedWord(word="our", start=7.7, end=7.9),
        TranscribedWord(word="base", start=8.0, end=8.4),
    ]
    full_text = " ".join(w.word for w in words)
    raw = [
        RawSegment(
            index=1,
            start_time=0.0,
            end_time=8.4,
            text=full_text,
            words=words,
        )
    ]
    # Max line is 42 chars -> block max is 84 chars
    refined = refine_raw_segments(raw, max_chars_per_line=42)
    assert len(refined) >= 2
    for seg in refined:
        assert seg["end_time"] > seg["start_time"]
        assert len(seg["original_text"]) > 0


def test_refine_prevents_overlap():
    raw = [
        RawSegment(index=1, start_time=1.0, end_time=2.5, text="First line."),
        RawSegment(index=2, start_time=2.0, end_time=3.5, text="Second line."),
    ]
    refined = refine_raw_segments(raw)
    assert len(refined) == 2
    assert refined[1]["start_time"] >= refined[0]["end_time"]


# ---------------------------------------------------------------------------
# Orphan sentence openers stamped at the tail of the previous line
# ---------------------------------------------------------------------------

def _words(pairs):
    from app.pipeline.context import TranscribedWord
    return [TranscribedWord(word=f" {w}", start=s, end=s + 0.3, probability=0.9) for w, s in pairs]


def _raw(idx, words):
    from app.pipeline.context import RawSegment
    return RawSegment(index=idx, start_time=float(words[0].start), end_time=float(words[-1].end),
                      text="".join(w.word for w in words).strip(), words=words)


def test_orphan_opener_moves_across_the_pause():
    # Real case: "...aren't we? Feet" at 11:40, "dry on the east beach." at 12:01 —
    # batched Whisper stamped "Feet" at the end of the previous utterance.
    from app.pipeline.stages.refine_stage import reattach_orphan_leads

    a = _raw(1, _words([("aren't", 699.6), ("we?", 700.1), ("Feet", 700.6)]))
    b = _raw(2, _words([("dry", 721.9), ("on", 722.3), ("the", 722.6), ("east", 722.9), ("beach.", 723.3)]))
    out = reattach_orphan_leads([a, b])
    assert out[0].text == "aren't we?"
    assert out[1].text.startswith("Feet dry on")
    assert out[1].start_time > 721.0  # retimed to its own line, not 700.6


def test_orphan_after_a_comma_moves_but_the_greeting_stays():
    # "Hey, Commodore, don't" / "give him a damn thing." — only "don't" belongs to the second line.
    from app.pipeline.stages.refine_stage import reattach_orphan_leads

    a = _raw(1, _words([("Hey,", 1910.1), ("Commodore,", 1910.6), ("don't", 1911.6)]))
    b = _raw(2, _words([("give", 1915.6), ("him", 1915.9), ("a", 1916.1), ("damn", 1916.3), ("thing.", 1916.6)]))
    out = reattach_orphan_leads([a, b])
    assert out[0].text == "Hey, Commodore,"
    assert out[1].text == "don't give him a damn thing."
    assert 1910.0 < out[0].start_time < 1911.0 and out[1].start_time > 1915.0


def test_whole_unpunctuated_fragment_merges_into_its_sentence():
    # "We need another" at 0:19.6 with the sentence resuming at 0:23.1.
    from app.pipeline.stages.refine_stage import reattach_orphan_leads

    a = _raw(1, _words([("We", 19.6), ("need", 19.9), ("another", 20.3)]))
    b = _raw(2, _words([("20", 23.1), ("amps", 23.5), ("for", 23.8), ("a", 24.0), ("new", 24.2), ("circuit,", 24.5)]))
    out = reattach_orphan_leads([a, b])
    assert len(out) == 1
    assert out[0].text == "We need another 20 amps for a new circuit,"
    assert out[0].start_time > 22.0


def test_finished_sentences_and_short_gaps_are_left_alone():
    from app.pipeline.stages.refine_stage import reattach_orphan_leads

    a = _raw(1, _words([("I'll", 10.0), ("try.", 10.4)]))
    b = _raw(2, _words([("Good.", 20.0)]))
    out = reattach_orphan_leads([a, b])
    assert [s.text for s in out] == ["I'll try.", "Good."]

    # An unfinished tail with no real pause (same flow of speech) stays put
    c = _raw(1, _words([("the", 30.0), ("last", 30.3)]))
    d = _raw(2, _words([("ship", 31.0)]))
    assert [s.text for s in reattach_orphan_leads([c, d])] == ["the last", "ship"]
