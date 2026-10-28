"""Language name <-> ISO 639-1 code helpers shared by export, translation and the UI."""
from typing import Optional

LANGUAGE_NAMES = {
    "en": "English", "he": "Hebrew", "ar": "Arabic", "ru": "Russian", "es": "Spanish",
    "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese", "ja": "Japanese",
    "zh": "Chinese", "ko": "Korean", "nl": "Dutch", "tr": "Turkish", "pl": "Polish",
    "uk": "Ukrainian", "el": "Greek", "hi": "Hindi", "fa": "Persian", "sv": "Swedish",
    "da": "Danish", "no": "Norwegian", "fi": "Finnish", "cs": "Czech", "hu": "Hungarian",
    "ro": "Romanian", "th": "Thai", "vi": "Vietnamese", "id": "Indonesian", "yi": "Yiddish",
    "ur": "Urdu",
}

_NAME_TO_CODE = {name.lower(): code for code, name in LANGUAGE_NAMES.items()}
_NAME_TO_CODE.update({
    "עברית": "he", "hebrew (עברית)": "he", "farsi": "fa", "mandarin": "zh",
    # ISO 639-2/B and 639-2/T 3-letter codes commonly reported by ffprobe / MKV containers
    "eng": "en", "heb": "he", "ara": "ar", "rus": "ru", "spa": "es", "fre": "fr", "fra": "fr",
    "ger": "de", "deu": "de", "ita": "it", "por": "pt", "jpn": "ja", "zho": "zh", "chi": "zh",
    "kor": "ko", "dut": "nl", "nld": "nl", "tur": "tr", "pol": "pl", "ukr": "uk", "gre": "el",
    "ell": "el", "hin": "hi", "per": "fa", "fas": "fa", "swe": "sv", "dan": "da", "nor": "no",
    "nob": "no", "nno": "no", "fin": "fi", "cze": "cs", "ces": "cs", "hun": "hu", "rum": "ro",
    "ron": "ro", "tha": "th", "vie": "vi", "ind": "id", "yid": "yi", "urd": "ur",
})

RTL_CODES = {"he", "ar", "fa", "ur", "yi"}


def language_code(language: Optional[str]) -> Optional[str]:
    """Return an ISO code for a language name or code; None for unknown/auto."""
    if not language:
        return None
    lang = language.strip().lower()
    if lang in ("auto", "none", ""):
        return None
    if lang in LANGUAGE_NAMES:
        return lang
    if lang in _NAME_TO_CODE:
        return _NAME_TO_CODE[lang]
    if len(lang) in (2, 3) and lang.isalpha():
        return lang
    return None


def language_name(language: Optional[str], default: str = "the source language") -> str:
    """Return a human-readable language name for prompts ("en" -> "English")."""
    code = language_code(language)
    if code and code in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[code]
    if language and language.strip().lower() not in ("auto", "none", ""):
        return language.strip()
    return default


def is_rtl(language: Optional[str]) -> bool:
    return language_code(language) in RTL_CODES
