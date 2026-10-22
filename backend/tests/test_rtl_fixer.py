from app.utils.rtl_fixer import (
    RLM,
    RLE,
    PDF,
    contains_rtl,
    fix_rtl_line,
    apply_rtl_fix,
)


def test_contains_rtl_detects_hebrew():
    assert contains_rtl("שלום עולם") is True
    assert contains_rtl("Hello world!") is False


def test_fix_rtl_line_wraps_hebrew_with_rle_and_rlm():
    line = "אתה צוחק עליי?"
    fixed = fix_rtl_line(line)
    assert fixed == f"{RLE}{RLM}אתה צוחק עליי?{RLM}{PDF}"


def test_fix_rtl_line_leaves_english_untouched():
    line = "Wait for me!"
    assert fix_rtl_line(line) == "Wait for me!"


def test_fix_rtl_line_normalizes_dialogue_hyphen():
    line = "-לא עכשיו!\n-למה?"
    fixed = apply_rtl_fix(line)
    expected = f"{RLE}{RLM}- לא עכשיו!{RLM}{PDF}\n{RLE}{RLM}- למה?{RLM}{PDF}"
    assert fixed == expected


def test_fix_rtl_line_mixed_hebrew_and_english():
    line = "ששיבש את נתוני ה-GPR של יאן."
    fixed = fix_rtl_line(line)
    assert fixed == f"{RLE}{RLM}ששיבש את נתוני ה-GPR של יאן.{RLM}{PDF}"


def test_apply_rtl_fix_is_idempotent():
    line = "זה עובד מצוין..."
    once = apply_rtl_fix(line)
    twice = apply_rtl_fix(once)
    assert once == twice
