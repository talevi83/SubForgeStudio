"""Behaviour of the rewritten refine stage (segmentation, fragments, timing)."""
from app.pipeline.context import RawSegment, TranscribedWord
from app.pipeline.stages.refine_stage import refine_raw_segments, _collapse_repetitions, _Tok


def _words(text: str, start: float, step: float = 0.3):
    out, t = [], start
    for w in text.split():
        out.append(TranscribedWord(word=" " + w, start=t, end=t + step - 0.05))
        t += step
    return out


def _seg(idx: int, text: str, start: float, step: float = 0.3) -> RawSegment:
    words = _words(text, start, step)
    return RawSegment(index=idx, start_time=words[0].start, end_time=words[-1].end, text=text, words=words)


def test_rejoins_sentence_cut_by_whisper_window():
    # Whisper ended a window mid-sentence: "Just to" | "come down with the virus."
    raw = [_seg(1, "Just to", 10.0), _seg(2, "come down with the virus.", 10.6)]
    refined = refine_raw_segments(raw)
    assert len(refined) == 1
    assert refined[0]["original_text"] == "Just to come down with the virus."


def test_does_not_merge_sentences_from_different_whisper_segments():
    # Likely two speakers — must stay two subtitles even though both are short.
    raw = [_seg(1, "I'm Commander Chandler.", 5.0), _seg(2, "Rachel Scott.", 5.7)]
    refined = refine_raw_segments(raw)
    assert [r["original_text"] for r in refined] == ["I'm Commander Chandler.", "Rachel Scott."]


def test_dangling_fragment_merges_into_next_block():
    raw = [_seg(1, "Put", 20.0), _seg(2, "that down over there. Be careful.", 20.9)]
    refined = refine_raw_segments(raw)
    assert refined[0]["original_text"].startswith("Put that down")


def test_long_sentence_split_prefers_clause_boundary_and_respects_line_limit():
    text = ("We need to secure the entire perimeter before nightfall, because the hostile forces "
            "are approaching our base from the north and we have no air support left.")
    refined = refine_raw_segments([_seg(1, text, 0.0, step=0.25)])
    assert len(refined) >= 2
    joined = " ".join(r["original_text"].replace("\n", " ") for r in refined)
    assert joined == text
    from app.utils.text_rules import WEAK_END_WORDS, bare
    for seg in refined:
        assert bare(seg["original_text"].split()[-1]) not in WEAK_END_WORDS
        lines = seg["original_text"].split("\n")
        assert len(lines) <= 2
        assert all(len(line) <= 42 for line in lines)
        assert seg["end_time"] - seg["start_time"] <= 7.0 + 1e-6


def test_display_time_extended_toward_reading_speed_but_not_into_next():
    # 37 chars spoken in 1.2s (31 cps); 2s of silence after it
    raw = [_seg(1, "Anything I should be concerned about?", 0.0, step=0.2), _seg(2, "No.", 3.5)]
    refined = refine_raw_segments(raw, target_cps=17.0)
    first, second = refined
    assert first["end_time"] - first["start_time"] >= 37 / 17.0 - 0.01
    assert first["end_time"] <= second["start_time"] - 0.08


def test_minimum_gap_and_no_overlaps():
    raw = [_seg(1, "First one.", 0.0), _seg(2, "Second one.", 0.62), _seg(3, "Third one.", 1.25)]
    refined = refine_raw_segments(raw)
    for a, b in zip(refined, refined[1:]):
        assert b["start_time"] - a["end_time"] >= 0.08


def test_small_gaps_are_closed():
    raw = [_seg(1, "Hello there, my friend.", 0.0), _seg(2, "How are you doing today?", 1.6)]
    refined = refine_raw_segments(raw)
    gap = refined[1]["start_time"] - refined[0]["end_time"]
    assert gap < 0.1


def test_collapses_hallucination_loops_only():
    loop = [_Tok(f" {w}", i * 0.3, i * 0.3 + 0.2) for i, w in enumerate(("thank you for watching " * 6).split())]
    assert len(_collapse_repetitions(loop)) == 4
    real = [_Tok(f" {w}", i * 0.3, i * 0.3 + 0.2) for i, w in enumerate("no no no no stop it now".split())]
    assert len(_collapse_repetitions(real)) == len(real)


def test_word_stretched_over_silence_is_clamped():
    words = [
        TranscribedWord(word=" Just", start=65.4, end=65.7),
        TranscribedWord(word=" to", start=65.8, end=72.3),  # stretched across 6s of music
    ]
    raw = [RawSegment(index=1, start_time=65.4, end_time=72.3, text="Just to", words=words)]
    refined = refine_raw_segments(raw)
    assert refined[0]["end_time"] < 68.0
