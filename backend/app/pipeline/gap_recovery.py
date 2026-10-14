"""
Recover speech the voice-activity detector (Silero VAD) skipped.

Silero VAD is reliable on clean dialogue, but on TV/film audio it misses speech under gunfire,
engines and music (shouted commands in action scenes) and band-limited radio voices. Whisper
never hears what the VAD removed, so those lines are simply missing from the subtitles.

The recovery pass re-runs Whisper *without* VAD, only over the stretches of audio no subtitle
covers (faster-whisper `clip_timestamps`), and keeps a decoded line only if it passes strict
hallucination checks — decoding music or effects without a VAD is exactly when Whisper invents
text ("Thank you for watching", repeated words, ...).
"""
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

MIN_GAP = 1.0            # seconds of uncovered audio worth re-listening to
EDGE = 0.15              # keep away from the neighbours' first/last words
MIN_LEVEL_DB = -48.0     # quieter than this (RMS, dBFS) is treated as silence
MAX_CLIP = 28.0          # split long gaps so each clip fits one Whisper window

# Acceptance thresholds for recovered lines.
# `avg_logprob` and `no_speech_prob` come from faster-whisper per decoding window, so every line in
# a noisy 30s clip shares the same (poor) values: a confident "Clear!" under gunfire reports the
# window's logprob of -1.4. Word probabilities are the only per-line signal, so the logprob
# threshold is relaxed when the window clearly contains speech (low no_speech) and the words are sure.
MIN_AVG_LOGPROB = -1.0
NOISY_MIN_AVG_LOGPROB = -1.5  # allowed when no_speech is low and the words are confident
NOISY_MAX_NO_SPEECH = 0.3
NOISY_MIN_WORD_PROB = 0.6
MAX_NO_SPEECH = 0.75   # judged per 30s window here, so less telling
MAX_COMPRESSION = 2.4
MIN_WORD_PROB = 0.5
MIN_DURATION = 0.2
# Short lines ("Okay.", "Fire!") are where hallucinations hide: a bit stricter
SHORT_WORDS = 3
SHORT_MIN_AVG_LOGPROB = -0.9
SHORT_MIN_WORD_PROB = 0.5
SHORT_MAX_NO_SPEECH = 0.7
# A line stretched over far more audio than its words need is Whisper bridging silence
STRETCH_PER_WORD = 2.0
STRETCH_SLACK = 2.0

# Shouted commands in action scenes: one or two words, usually over gunfire, often repeated
# ("Fire! ... Fire! ... Fire!"). Word timestamps for a shout can be under 0.2s and the word
# probability under noise is mediocre, so these get their own, looser rules — and a repeat of
# the neighbouring line is expected rather than suspicious.
COMMAND_WORDS = {
    "fire", "move", "go", "clear", "stop", "hold", "down", "contact", "grenade", "ready", "aim",
    "cover", "reload", "moving", "set", "freeze", "breach", "push", "back", "run", "duck", "incoming",
    "sniper", "medic", "wait", "now", "out",
}
COMMAND_PHRASES = {
    "get down", "man down", "cease fire", "hold fire", "fire at will", "frag out", "open fire",
    "hands up", "drop it", "fall back", "take cover", "head shot", "on me", "move out", "let's go",
    "cover me", "moving up", "go go go", "move move", "stop stop", "fire fire",
}
COMMAND_MIN_WORD_PROB = 0.3
COMMAND_MAX_NO_SPEECH = 0.5

# A prompt/hotword list echoed back as "speech" (seen with radio hotwords on silence)
_PROMPT_WORDS = {"over", "copy", "roger", "wilco", "sitrep", "klicks", "callsign", "actual", "say", "again", "radio", "check"}

# Whisper's well-known inventions on non-speech audio (normalized, lower case, no punctuation)
# "Thank you." is Whisper's favourite invention on breaths and silence (large-v3-turbo decodes
# it dozens of times per episode), and broadcast fillers ("We'll be right back") are a close
# second. Real thank-yous the VAD heard are in the main pass; in a gap they are near-certain fakes.
_HALLUCINATIONS = {
    "thank you for watching", "thanks for watching", "thank you so much for watching",
    "please subscribe", "subscribe to my channel", "like and subscribe",
    "subtitles by the amara org community", "transcription by castingwords",
    "you", "bye", "the end",
    "thank you", "thanks", "thank you very much", "thank you so much",
    "we'll be right back", "be right back", "we'll see you next time", "i'll see you next time",
    "see you next time", "see you in the next video", "see you in the next one",
}
_HALLUCINATION_PATTERNS = [
    re.compile(r"\b(subtitles?|captions?|transcri\w+|amara|subscribe)\b"),
    re.compile(r"\b(link in|in the description|description below|check out my|my channel|next video)\b"),
]
_MUSIC = re.compile(r"[♪♫]")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", text.lower())).strip()


def find_gaps(
    covered: Iterable[Tuple[float, float]],
    duration: float,
    exclude: Iterable[Tuple[float, float]] = (),
    min_gap: float = MIN_GAP,
    edge: float = EDGE,
) -> List[Tuple[float, float]]:
    """Uncovered stretches of [0, duration] at least `min_gap` long, outside `exclude`."""
    busy = sorted([(s, e) for s, e in covered] + [(s, e) for s, e in exclude])
    gaps: List[Tuple[float, float]] = []
    cursor = 0.0
    for s, e in busy:
        if s - cursor >= min_gap + 2 * edge:
            gaps.append((round(cursor + (edge if cursor > 0 else 0.0), 3), round(s - edge, 3)))
        cursor = max(cursor, e)
    if duration - cursor >= min_gap + edge:
        gaps.append((round(cursor + (edge if cursor > 0 else 0.0), 3), round(duration, 3)))
    return gaps


def split_long(gaps: Sequence[Tuple[float, float]], max_len: float = MAX_CLIP) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for s, e in gaps:
        while e - s > max_len:
            out.append((s, s + max_len))
            s += max_len
        out.append((s, e))
    return out


def level_db(audio: np.ndarray, start: float, end: float, sample_rate: int = 16000) -> float:
    piece = audio[int(start * sample_rate):int(end * sample_rate)]
    if piece.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(piece.astype(np.float64)))))
    return 20 * np.log10(max(rms, 1e-6))


def is_command(norm: str) -> bool:
    """A normalized shouted command ("fire", "go go go", "cease fire")."""
    words = norm.split()
    if not words or len(words) > 3:
        return False
    if norm in COMMAND_PHRASES:
        return True
    return all(w in COMMAND_WORDS for w in words)


def rejection_reason(
    text: str,
    avg_logprob: Optional[float],
    no_speech_prob: Optional[float],
    compression_ratio: Optional[float],
    word_probs: Sequence[float],
    duration: float,
    neighbours: Sequence[str] = (),
) -> Optional[str]:
    """None if a recovered line looks like real speech, else a short reason (for the job log)."""
    norm = normalize(text)
    if _MUSIC.search(text):
        return "known hallucination (music notes)"
    if not norm:
        return "empty"
    if norm in _HALLUCINATIONS or any(p.search(norm) for p in _HALLUCINATION_PATTERNS):
        return "known hallucination"
    if len(set(norm.split()) & _PROMPT_WORDS) >= 4:
        return "echo of the prompt words"
    words = norm.split()
    mean_word_prob = float(np.mean(word_probs)) if word_probs else None
    if duration > STRETCH_PER_WORD * len(words) + STRETCH_SLACK:
        return f"stretched over {duration:.1f}s for {len(words)} word(s)"

    if is_command(norm):
        # Shouts: judged by the words themselves; the window stats are dominated by the noise
        if no_speech_prob is not None and no_speech_prob > COMMAND_MAX_NO_SPEECH:
            return f"probably not speech (no_speech {no_speech_prob:.2f})"
        if mean_word_prob is not None and mean_word_prob < COMMAND_MIN_WORD_PROB:
            return f"unsure words (mean {mean_word_prob:.2f})"
        return None

    if duration < MIN_DURATION:
        return "too short"
    short = len(words) <= SHORT_WORDS
    min_logprob = SHORT_MIN_AVG_LOGPROB if short else MIN_AVG_LOGPROB
    max_no_speech = SHORT_MAX_NO_SPEECH if short else MAX_NO_SPEECH
    min_word_prob = SHORT_MIN_WORD_PROB if short else MIN_WORD_PROB
    noisy_speech = (
        no_speech_prob is not None and no_speech_prob <= NOISY_MAX_NO_SPEECH
        and mean_word_prob is not None and mean_word_prob >= NOISY_MIN_WORD_PROB
    )
    if noisy_speech:
        min_logprob = NOISY_MIN_AVG_LOGPROB
    if avg_logprob is not None and avg_logprob < min_logprob:
        return f"low confidence (logprob {avg_logprob:.2f})"
    if no_speech_prob is not None and no_speech_prob > max_no_speech:
        return f"probably not speech (no_speech {no_speech_prob:.2f})"
    if compression_ratio is not None and compression_ratio > MAX_COMPRESSION:
        return f"repetitive (compression {compression_ratio:.1f})"
    if mean_word_prob is not None and mean_word_prob < min_word_prob:
        return f"unsure words (mean {mean_word_prob:.2f})"
    # "Go, go, go!" / "It's okay, it's okay" are real; hallucinated loops run much longer
    if len(words) >= 8 and len(set(words)) <= len(words) // 4:
        return "repeated words"
    if norm in {normalize(n) for n in neighbours}:
        return "duplicate of a neighbouring line"
    return None


def covered_intervals(segments: Sequence, join: float = 0.3) -> List[Tuple[float, float]]:
    """Where speech was actually recognized. Word timestamps are used when available: a batched
    Whisper segment can span audio the VAD removed in the middle of it (e.g. 0:57-1:25 around a
    shooting range full of shouted commands), and the segment bounds would hide that gap."""
    spans: List[Tuple[float, float]] = []
    for seg in segments:
        words = [w for w in (getattr(seg, "words", None) or []) if w.end > w.start]
        if words:
            spans.extend((float(w.start), float(w.end)) for w in words)
        else:
            spans.append((float(seg.start_time), float(seg.end_time)))
    spans.sort()
    merged: List[Tuple[float, float]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + join:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def neighbours_of(start: float, end: float, segments: Sequence[Dict], window: float = 6.0) -> List[str]:
    return [s["text"] for s in segments if s["end"] >= start - window and s["start"] <= end + window]
