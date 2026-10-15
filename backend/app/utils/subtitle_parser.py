"""
Read a subtitle file the user already has (SRT, WebVTT or ASS/SSA) for "sync existing subtitles".

* Decoding: BOM (UTF-8 / UTF-16), else strict UTF-8, else Windows-1255 (most Hebrew SRTs made on
  Windows), else Windows-1252.
* Cues come back in file order with formatting removed but line breaks kept. A cue positioned at
  the top of the picture ({\\an8}, ASS alignment 7-9, VTT "line:0") is usually a sign or title, so
  it is marked with position="top" - a hint for matching on-screen text later.
* The language is guessed from the script (Hebrew/Arabic/Cyrillic/Greek letters, else English).
"""
import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple


class SubtitleParseError(ValueError):
    pass


@dataclass
class ParsedCue:
    start: float
    end: float
    text: str
    position: str = "bottom"     # "top" when the file places the cue at the top of the picture
    kind: str = "speech"         # "sound" for hearing-impaired descriptions such as [door slams] or ♪ ...

    def to_dict(self) -> Dict:
        return asdict(self)


# --------------------------------------------------------------------------- decoding

_HEBREW = re.compile(r"[֐-׿]")
_ARABIC = re.compile(r"[؀-ۿ]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_GREEK = re.compile(r"[Ͱ-Ͽ]")
_LATIN = re.compile(r"[A-Za-z]")


def decode_subtitle_bytes(data: bytes) -> Tuple[str, str]:
    """Bytes -> (text, encoding name)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace"), "utf-16"
    # UTF-16 without a BOM: every other byte of ASCII text is zero
    sample = data[:4000]
    if sample and sample.count(b"\x00") > len(sample) // 4:
        enc = "utf-16-le" if sample[1:2] == b"\x00" else "utf-16-be"
        return data.decode(enc, errors="replace"), enc
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    hebrew = data.decode("cp1255", errors="replace")
    letters = sum(1 for ch in hebrew if ch.isalpha())
    if letters and len(_HEBREW.findall(hebrew)) / letters > 0.2:
        return hebrew, "windows-1255"
    arabic = data.decode("cp1256", errors="replace")
    if letters and len(_ARABIC.findall(arabic)) / max(1, sum(1 for ch in arabic if ch.isalpha())) > 0.2:
        return arabic, "windows-1256"
    return data.decode("cp1252", errors="replace"), "windows-1252"


def detect_language(cues: List[ParsedCue]) -> str:
    """Language name from the dominant script of the cue text."""
    text = " ".join(c.text for c in cues)
    counts = {
        "Hebrew": len(_HEBREW.findall(text)),
        "Arabic": len(_ARABIC.findall(text)),
        "Russian": len(_CYRILLIC.findall(text)),
        "Greek": len(_GREEK.findall(text)),
        "English": len(_LATIN.findall(text)),
    }
    letters = sum(counts.values())
    if not letters:
        return "English"
    # A non-Latin script that makes up a real share of the letters wins (names stay in Latin)
    other = max((k for k in counts if k != "English"), key=counts.get)
    return other if counts[other] >= 0.3 * letters else "English"


# --------------------------------------------------------------------------- text cleanup

_HTML = re.compile(r"<[^>]+>")
_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")
_TOP_TAG = re.compile(r"\{\\an?[789]\}|\{[^}]*\\an[789][^}]*\}")
_SOUND = re.compile(r"^\s*(\[[^\]]*\]|\([^)]*\)|[♪♫#*]+.*)\s*$")
_SPACES = re.compile(r"[ \t]+")


def _clean(text: str) -> str:
    text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    text = _ASS_OVERRIDE.sub("", _HTML.sub("", text))
    lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _kind(text: str) -> str:
    """'sound' when every line is a bracketed description or music note (nothing is spoken)."""
    lines = [line for line in text.splitlines() if line.strip()]
    return "sound" if lines and all(_SOUND.match(line) for line in lines) else "speech"


# --------------------------------------------------------------------------- formats

_TIME = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})"
_CUE_TIME = re.compile(_TIME + r"\s*-->\s*" + _TIME + r"(.*)$")


def _seconds(h: Optional[str], m: str, s: str, frac: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(frac.ljust(3, "0")[:3]) / 1000


def _parse_timed_blocks(content: str, vtt: bool) -> List[ParsedCue]:
    """SRT and WebVTT share the "start --> end" block structure."""
    cues: List[ParsedCue] = []
    blocks = re.split(r"\n\s*\n", content)
    for block in blocks:
        lines = block.split("\n")
        for i, line in enumerate(lines):
            match = _CUE_TIME.search(line)
            if not match:
                continue
            g = match.groups()
            settings_part = g[8] or ""
            raw = "\n".join(lines[i + 1:])
            top = bool(_TOP_TAG.search(raw)) or (vtt and re.search(r"\bline:(0|[0-9](\.\d+)?%|1[0-9]%)", settings_part))
            text = _clean(raw)
            if text:
                start, end = _seconds(*g[0:4]), _seconds(*g[4:8])
                cues.append(ParsedCue(start, max(end, start + 0.1), text, "top" if top else "bottom", _kind(text)))
            break
    return cues


def _parse_ass(content: str) -> List[ParsedCue]:
    styles_top: Dict[str, bool] = {}
    fields: List[str] = []
    style_fields: List[str] = []
    cues: List[ParsedCue] = []
    section = ""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line.lower()
            continue
        if line.lower().startswith("format:"):
            names = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
            if "styles" in section:
                style_fields = names
            elif "events" in section:
                fields = names
            continue
        if line.lower().startswith("style:") and style_fields:
            values = [v.strip() for v in line.split(":", 1)[1].split(",")]
            row = dict(zip(style_fields, values))
            styles_top[row.get("name", "")] = row.get("alignment", "2") in ("7", "8", "9")
            continue
        if line.lower().startswith("dialogue:") and fields:
            values = line.split(":", 1)[1].split(",", len(fields) - 1)
            row = dict(zip(fields, [v.strip() if k < len(fields) - 1 else v for k, v in enumerate(values)]))
            try:
                start = _ass_time(row.get("start", ""))
                end = _ass_time(row.get("end", ""))
            except ValueError:
                continue
            raw = row.get("text", "")
            text = _clean(raw)
            if not text:
                continue
            top = bool(_TOP_TAG.search(raw)) or styles_top.get(row.get("style", ""), False)
            cues.append(ParsedCue(start, max(end, start + 0.1), text, "top" if top else "bottom", _kind(text)))
    return cues


def _ass_time(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[.,](\d{1,3})", value.strip())
    if not match:
        raise ValueError(value)
    h, m, s, cs = match.groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs.ljust(2, "0")) / (10 ** len(cs))


def parse_subtitle_text(content: str, file_name: str = "") -> Tuple[List[ParsedCue], str]:
    """Text -> (cues sorted by start, format name). Raises SubtitleParseError if nothing is found."""
    content = content.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    name = file_name.lower()
    head = content.lstrip()[:200].lower()
    if name.endswith((".ass", ".ssa")) or "[script info]" in head or "[events]" in content.lower():
        cues, fmt = _parse_ass(content), "ass"
    elif name.endswith(".vtt") or head.startswith("webvtt"):
        cues, fmt = _parse_timed_blocks(content, vtt=True), "vtt"
    else:
        cues, fmt = _parse_timed_blocks(content, vtt=False), "srt"
    if not cues:
        raise SubtitleParseError("No subtitles found - supported formats are SRT, WebVTT and ASS/SSA.")
    cues.sort(key=lambda c: (c.start, c.end))
    return cues, fmt


def parse_subtitle_file(data: bytes, file_name: str = "") -> Dict:
    """Bytes of an uploaded file -> everything the setup screen and the sync job need."""
    if len(data) > 20 * 1024 * 1024:
        raise SubtitleParseError("The subtitle file is too large (over 20 MB).")
    text, encoding = decode_subtitle_bytes(data)
    cues, fmt = parse_subtitle_text(text, file_name)
    return {
        "file_name": file_name,
        "format": fmt,
        "encoding": encoding,
        "language": detect_language(cues),
        "cue_count": len(cues),
        "first_start": cues[0].start,
        "last_end": max(c.end for c in cues),
        "top_count": sum(1 for c in cues if c.position == "top"),
        "sound_count": sum(1 for c in cues if c.kind == "sound"),
        "cues": [c.to_dict() for c in cues],
    }
