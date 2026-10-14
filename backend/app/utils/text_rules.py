"""Shared linguistic heuristics for where subtitle blocks and lines may break."""
import re

# A token that ends a sentence: "word." "word?!" "word…" 'word."' and CJK full stops.
SENTENCE_END = re.compile(r"""[.!?…。！？]+["'”’)\]]*$""")
# A token that ends a clause: commas, semicolons, colons, dashes.
CLAUSE_END = re.compile(r"""[,;:،、，—–]["'”’)\]]*$|--$""")

# Abbreviations whose trailing period does not end a sentence.
ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "st.", "sr.", "jr.", "lt.", "col.", "gen.", "capt.", "cpt.",
    "sgt.", "cmdr.", "adm.", "prof.", "vs.", "etc.", "no.", "u.s.", "u.k.", "a.m.", "p.m.",
}

# Lines/blocks should not end on these (they belong to the following words).
WEAK_END_WORDS = {
    "the", "a", "an", "to", "of", "and", "or", "but", "my", "your", "his", "her", "our",
    "their", "its", "in", "on", "at", "for", "with", "from", "by", "as", "into", "onto",
    "that", "this", "these", "those", "is", "are", "was", "were", "be", "i", "we", "you",
    "he", "she", "they", "it", "not", "no", "if", "so", "than", "very", "mr.", "mrs.", "dr.",
}

# Natural places to start a new line/block.
BREAK_BEFORE_WORDS = {
    "and", "but", "or", "because", "so", "that", "which", "who", "whom", "whose", "when",
    "if", "while", "although", "though", "until", "unless", "then", "where", "after",
    "before", "since", "whether",
}

_STRIP = "\"'“”‘’()[]{}.,!?;:…-—–"


def bare(word: str) -> str:
    """Lower-cased word without surrounding punctuation."""
    return word.strip().strip(_STRIP).lower()


def ends_sentence(word: str) -> bool:
    w = word.strip()
    if not w or w.lower() in ABBREVIATIONS:
        return False
    return bool(SENTENCE_END.search(w))


def ends_clause(word: str) -> bool:
    return bool(CLAUSE_END.search(word.strip()))
