from types import SimpleNamespace as NS

from app.pipeline.qa import check_rows

GLOSS = {
    "characters": [{"name": "Chandler", "target_name": "צ'נדלר"}],
    "terms": [{"source": "bridge", "translation": "גשר"}],
}


def row(i, orig, tr, typ="DIALOGUE", failed=False):
    return NS(id=i, index=i, original_text=orig, translated_text=tr, segment_type=typ, translation_failed=failed)


def kinds(rows, g=GLOSS):
    return [(i.segment_id, i.kind) for i in check_rows(rows, g)]


def test_latin_word_flagged_but_acronyms_allowed():
    assert kinds([row(1, "x", "זה somehow קרה")]) == [(1, "latin")]
    assert kinds([row(2, "x", "הם מ-FBI והכל OK")]) == []


def test_agreement_mismatch():
    assert kinds([row(1, "x", "את תהיה שם")]) == [(1, "agreement")]
    assert kinds([row(2, "x", "אתה תהיי שם")]) == [(2, "agreement")]
    assert kinds([row(3, "x", "את תהיי שם"), row(4, "x", "אתה תהיה שם"), row(5, "x", "תן את הכל")]) == []


def test_glossary_name_and_term():
    assert kinds([row(1, "Chandler is here", "צנדלר כאן")]) == [(1, "name")]
    assert kinds([row(2, "Chandler is here", "צ'נדלר כאן")]) == []
    assert kinds([row(3, "On the bridge now", "על הסיפון עכשיו")]) == [(3, "term")]


def test_missing_translation_and_on_screen():
    assert kinds([row(1, "hello", "", failed=True)]) == [(1, "missing")]
    assert kinds([row(2, "SIGN", None, typ="ON_SCREEN")]) == []
