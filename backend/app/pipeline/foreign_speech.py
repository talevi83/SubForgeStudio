"""
Find passages spoken in a different language than the rest of the video.

Whisper decides the language once (from the first 30 seconds) and then decodes everything as
that language, so a Russian conversation inside an English episode is dropped, garbled or
silently "translated". To catch those passages we:

1. run Silero VAD over the whole track and group speech into utterance chunks (<= 12s),
2. ask Whisper which language each chunk is (`detect_language`, encoder only — fast),
3. turn runs of chunks that are confidently another language — or that contain speech Whisper
   produced no text for — into spans.

Spans are later filled with the show's own burned-in subtitles (OCR) or, as a fallback, with
Whisper re-run on just that span in the span's language.
"""
import wave
from typing import Any, Dict, List, Optional, Sequence, Tuple

SAMPLE_RATE = 16000
_GROUP_GAP = 1.0           # merge speech pieces closer than this (s)
_GROUP_MAX = 12.0          # longest chunk sent to language detection (s)
_GROUP_MIN = 1.0           # shorter chunks are too unreliable to classify
_FOREIGN_MIN_PROB = 0.5    # minimum confidence to call a chunk "another language"
_MAIN_SURE_PROB = 0.7      # an untranscribed chunk this sure to be the main language is ignored
_UNCOVERED = 0.2           # at most this share of a chunk covered by Whisper text = "untranscribed"
_CORE_JOIN_GAP = 3.0       # chunks closer than this form one "core" (text to replace / re-transcribe)
_SPAN_JOIN_GAP = 8.0       # cores closer than this share one OCR window
_SPAN_MIN = 1.5            # ignore shorter cores
_SPAN_PAD = 2.0            # OCR window padding: subtitles often outlast the speech VAD detects


def load_wav(path: str):
    """16kHz mono PCM WAV -> float32 numpy array."""
    import numpy as np

    with wave.open(path, "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("Expected 16kHz mono 16-bit WAV")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def group_speech(timestamps: Sequence[Dict[str, int]], sample_rate: int = SAMPLE_RATE) -> List[Tuple[float, float]]:
    """VAD timestamps (in samples) -> utterance chunks in seconds, merged and capped in length."""
    groups: List[List[float]] = []
    for ts in timestamps:
        start, end = ts["start"] / sample_rate, ts["end"] / sample_rate
        if groups and start - groups[-1][1] <= _GROUP_GAP and end - groups[-1][0] <= _GROUP_MAX:
            groups[-1][1] = end
        else:
            groups.append([start, end])
    # Split anything still too long (a single long VAD segment)
    out: List[Tuple[float, float]] = []
    for start, end in groups:
        while end - start > _GROUP_MAX:
            out.append((start, start + _GROUP_MAX))
            start += _GROUP_MAX
        out.append((start, end))
    return [(s, e) for s, e in out if e - s >= _GROUP_MIN]


def _covered(start: float, end: float, spoken: Sequence[Tuple[float, float]]) -> float:
    total = sum(max(0.0, min(end, e) - max(start, s)) for s, e in spoken)
    return total / max(0.01, end - start)


# The burned-in subtitle reader scans this far before/after each foreign span (replies and short
# lines the speech detector missed); other passes treat that whole window as the passage.
READ_MARGIN = 6.0


def build_foreign_spans(
    chunks: Sequence[Dict[str, Any]],
    main_language: Optional[str],
    spoken: Sequence[Tuple[float, float]],
) -> List[Dict[str, Any]]:
    """
    chunks: [{"start", "end", "language", "probability"}] in time order.
    spoken: (start, end) of the segments Whisper produced in the main pass.

    Returns spans: {"start", "end"} is the (padded) window to scan for burned-in subtitles;
    "cores" are the tight ranges that really are foreign/untranscribed speech — only text
    there is replaced, and only those ranges are re-transcribed as a fallback.
    """
    main = (main_language or "").lower()
    candidates = []
    for c in chunks:
        lang, prob = (c.get("language") or "").lower(), float(c.get("probability") or 0)
        foreign = bool(main) and lang != main and prob >= _FOREIGN_MIN_PROB
        untranscribed = _covered(c["start"], c["end"], spoken) <= _UNCOVERED and not (
            lang == main and prob >= _MAIN_SURE_PROB
        )
        if foreign or untranscribed:
            candidates.append({**c, "language": lang, "foreign": foreign})

    # 1. cores: runs of candidate chunks
    cores: List[Dict[str, Any]] = []
    for c in candidates:
        if cores and c["start"] - cores[-1]["end"] <= _CORE_JOIN_GAP:
            cores[-1]["end"] = c["end"]
            cores[-1]["chunks"].append(c)
        else:
            cores.append({"start": c["start"], "end": c["end"], "chunks": [c]})
    cores = [c for c in cores if c["end"] - c["start"] >= _SPAN_MIN]

    # 2. spans: nearby cores share one OCR window
    spans: List[Dict[str, Any]] = []
    for core in cores:
        if spans and core["start"] - spans[-1]["cores"][-1]["end"] <= _SPAN_JOIN_GAP:
            spans[-1]["cores"].append(core)
        else:
            spans.append({"cores": [core]})

    result = []
    for span in spans:
        chunks_in = [c for core in span["cores"] for c in core["chunks"]]
        votes: Dict[str, float] = {}
        for c in chunks_in:
            if c["foreign"]:
                votes[c["language"]] = votes.get(c["language"], 0.0) + (c["end"] - c["start"])
        language = max(votes, key=votes.get) if votes else None
        first, last = span["cores"][0]["start"], span["cores"][-1]["end"]
        result.append({
            "start": round(max(0.0, first - _SPAN_PAD), 3),
            "end": round(last + _SPAN_PAD, 3),
            "cores": [(round(c["start"], 3), round(c["end"], 3)) for c in span["cores"]],
            "language": language,
            "probability": round(max((c["probability"] for c in chunks_in if c["foreign"]), default=0.0), 3),
            "reason": "language" if language else "untranscribed",
        })
    return result


def summarize_spans(spans: Sequence[Dict[str, Any]]) -> str:
    langs = sorted({s["language"] for s in spans if s.get("language")})
    seconds = sum(s["end"] - s["start"] for s in spans)
    return f"{len(spans)} passage(s), {seconds / 60:.1f} min" + (f" ({', '.join(langs)})" if langs else "")
