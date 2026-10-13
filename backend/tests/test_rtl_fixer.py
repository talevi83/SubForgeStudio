from app.utils.rtl_fixer import (
    RLM,
    contains_rtl,
    fix_rtl_line,
    apply_rtl_fix,
)


def test_contains_rtl_detects_hebrew():
    assert contains_rtl("שלום עולם") is True
    assert contains_rtl("Hello world!") is False


def test_fix_rtl_line_wraps_hebrew_with_rlm():
    line = "אתה צוחק עליי?"
    fixed = fix_rtl_line(line)
    assert fixed == f"{RLM}אתה צוחק עליי?{RLM}"


def test_fix_rtl_line_leaves_english_untouched():
    line = "Wait for me!"
    assert fix_rtl_line(line) == "Wait for me!"


def test_fix_rtl_line_normalizes_dialogue_hyphen():
    line = "-לא עכשיו!\n-למה?"
    fixed = apply_rtl_fix(line)
    expected = f"{RLM}- לא עכשיו!{RLM}\n{RLM}- למה?{RLM}"
    assert fixed == expected


def test_apply_rtl_fix_is_idempotent():
    line = "זה עובד מצוין..."
    once = apply_rtl_fix(line)
    twice = apply_rtl_fix(once)
    assert once == twice
