import re

# Unicode directional control characters
RLM = "\u200f"  # Right-to-Left Mark
LRM = "\u200e"  # Left-to-Right Mark
RLE = "\u202b"  # Right-to-Left Embedding
LRE = "\u202a"  # Left-to-Right Embedding
PDF = "\u202c"  # Pop Directional Formatting

# Regex matching any Hebrew or Arabic character
RTL_CHAR_PATTERN = re.compile(r"[\u0590-\u05FF\u0600-\u06FF]")

# Regex to strip existing directional control characters for idempotency
CONTROL_CHARS_PATTERN = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def contains_rtl(text: str) -> bool:
    """Check whether the given string contains Hebrew or Arabic characters."""
    return bool(RTL_CHAR_PATTERN.search(text))


def strip_directional_marks(text: str) -> str:
    """Remove any existing Unicode BiDi control characters from the text."""
    return CONTROL_CHARS_PATTERN.sub("", text)


def fix_rtl_line(line: str, use_embedding: bool = False) -> str:
    """
    Fix BiDi punctuation placement for a single subtitle line containing RTL text.

    By sandwiching the line between RLM characters (start and end), neutral
    characters such as leading dialogue hyphens ('- ') and trailing punctuation
    ('?', '!', '.', ',') are forced into the RTL layout context across all
    standard media players (VLC, MPV, Plex, HTML5 WebVTT).
    """
    clean_line = strip_directional_marks(line).strip()
    if not clean_line:
        return ""

    if not contains_rtl(clean_line):
        return clean_line

    # Normalize dialogue hyphen spacing (e.g., "-Hello" -> "- Hello")
    if clean_line.startswith("-") and not clean_line.startswith("- "):
        clean_line = "- " + clean_line[1:].lstrip()

    if use_embedding:
        return f"{RLE}{RLM}{clean_line}{RLM}{PDF}"

    return f"{RLM}{clean_line}{RLM}"


def apply_rtl_fix(text: str, use_embedding: bool = False) -> str:
    """
    Apply BiDi RTL fixes line-by-line to a potentially multi-line subtitle block.
    Safe to call multiple times (idempotent).
    """
    if not text:
        return ""

    lines = text.split("\n")
    fixed_lines = [fix_rtl_line(line, use_embedding=use_embedding) for line in lines]
    return "\n".join(fixed_lines)
