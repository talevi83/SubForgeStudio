"""
Text evidence for syncing an existing subtitle file: which words of the audio each line says.

* Same language as the speech (English file, English audio): the line's words are matched to
  Whisper's word timestamps near where the first alignment pass put the line (difflib).
* Another language (Hebrew file, English audio): Gemini reads the lines next to the transcript
  phrases (and the on-screen texts found in the video) and says which phrase(s) each line
  translates - see match_with_gemini().

Both return anchors {cue position: Anchor}. Anchors are evidence, not orders: the aligner
ignores an anchor that disagrees with its neighbours.
"""
import asyncio
import bisect
import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

WINDOW = 8.0             # same-language search window around the predicted time (s)
WORD_LEAD = 0.25         # time per unmatched leading/trailing word (s)
PHRASE_GAP = 0.3         # a pause this long ends a transcript phrase
PHRASE_WORDS = 7         # ... and so does this many words
GEMINI_BATCH = 40        # subtitle lines per Gemini request
GEMINI_MARGIN = 25.0     # transcript seconds shown before/after the batch's predicted span
GEMINI_CONCURRENCY = 3
SCREEN_WINDOW = 30.0     # on-screen texts considered for a line (s)


@dataclass
class Word:
    token: str           # normalized
    text: str            # as Whisper wrote it
    start: float
    end: float


@dataclass
class Phrase:
    pid: str
    start: float
    end: float
    text: str
    first_word: int
    last_word: int


@dataclass
class Anchor:
    start: float
    end: float
    source: str                  # "text" | "gemini" | "screen"
    screen: Optional[int] = None  # index into the on-screen detections for "screen"
    transcript: str = ""         # the matched words (shown next to a translated line)


# --------------------------------------------------------------------------- normalization

_NIQQUD = re.compile(r"[֑-ׇ]")
_SPLIT = re.compile(r"[^\w']+", re.UNICODE)


def tokens(text: str) -> List[str]:
    text = _NIQQUD.sub("", unicodedata.normalize("NFKC", text)).casefold().replace("’", "'")
    return [t.strip("'") for t in _SPLIT.split(text) if t.strip("'")]


def transcript_words(raw_segments: Sequence[Any]) -> List[Word]:
    """Whisper segments -> one Word per normalized token, in time order."""
    words: List[Word] = []
    for seg in raw_segments:
        seg_words = getattr(seg, "words", None) or []
        if not seg_words:  # no word timestamps: spread the segment over its tokens
            toks = tokens(seg.text)
            span = max(0.1, seg.end_time - seg.start_time)
            for k, tok in enumerate(toks):
                a = seg.start_time + span * k / max(1, len(toks))
                words.append(Word(tok, tok, a, a + span / max(1, len(toks))))
            continue
        for w in seg_words:
            toks = tokens(w.word)
            for tok in toks:
                words.append(Word(tok, w.word.strip(), float(w.start), float(w.end)))
    words.sort(key=lambda w: w.start)
    return words


# --------------------------------------------------------------------------- same language

def match_same_language(
    cue_texts: Sequence[str], predicted: Sequence[Tuple[float, float]], words: Sequence[Word],
    skip: Sequence[bool] = (),
) -> Dict[int, Anchor]:
    """Anchor each line on the transcript words it shares, near its predicted time."""
    starts = [w.start for w in words]
    anchors: Dict[int, Anchor] = {}
    for i, text in enumerate(cue_texts):
        if skip and skip[i]:
            continue
        cue_toks = tokens(text)
        if not cue_toks:
            continue
        ps, pe = predicted[i]
        lo = bisect.bisect_left(starts, ps - WINDOW)
        hi = bisect.bisect_right(starts, pe + WINDOW)
        window = [w.token for w in words[lo:hi]]
        if not window:
            continue
        matcher = difflib.SequenceMatcher(None, cue_toks, window, autojunk=False)
        blocks = [b for b in matcher.get_matching_blocks() if b.size]
        if not blocks:
            continue
        # The densest group of blocks (common words like "you" also match far away)
        span = len(cue_toks) * 2 + 3
        best: List[Any] = []
        for k, first in enumerate(blocks):
            group = [b for b in blocks[k:] if b.b - first.b <= span]
            if sum(b.size for b in group) > sum(b.size for b in best):
                best = group
        matched = sum(b.size for b in best)
        need = 1 if len(cue_toks) == 1 else max(2, (len(cue_toks) + 1) // 2)
        if matched < need:
            continue
        if len(cue_toks) == 1 and (len(cue_toks[0]) < 3 or window.count(cue_toks[0]) > 1):
            continue  # a lone short or repeated word is no evidence
        first_b, last_b = best[0], best[-1]
        w_first = words[lo + first_b.b]
        w_last = words[lo + last_b.b + last_b.size - 1]
        lead = first_b.a * WORD_LEAD
        tail = (len(cue_toks) - (last_b.a + last_b.size)) * WORD_LEAD
        anchors[i] = Anchor(
            start=round(max(0.0, w_first.start - lead), 3),
            end=round(w_last.end + tail, 3),
            source="text",
            transcript=" ".join(_dedupe_text(words[lo + first_b.b: lo + last_b.b + last_b.size])),
        )
    return _monotonic(anchors)


def _monotonic(anchors: Dict[int, Anchor]) -> Dict[int, Anchor]:
    """Keep the longest run of anchors whose times increase with the line order."""
    keys = sorted(anchors)
    if len(keys) < 2:
        return anchors
    values = [anchors[k].start for k in keys]
    # Longest non-decreasing subsequence (patience sorting with back-pointers)
    tails: List[int] = []
    tail_vals: List[float] = []
    prev = [-1] * len(keys)
    for i, v in enumerate(values):
        pos = bisect.bisect_right(tail_vals, v)
        if pos > 0:
            prev[i] = tails[pos - 1]
        if pos == len(tails):
            tails.append(i)
            tail_vals.append(v)
        else:
            tails[pos] = i
            tail_vals[pos] = v
    keep = set()
    i = tails[-1] if tails else -1
    while i >= 0:
        keep.add(keys[i])
        i = prev[i]
    return {k: anchors[k] for k in keys if k in keep}


def match_screens_same_language(
    cue_texts: Sequence[str], predicted: Sequence[Tuple[float, float]], screens: Sequence[Dict[str, Any]],
) -> Dict[int, Anchor]:
    """Lines that repeat an on-screen text (same language) take that text's timing."""
    anchors: Dict[int, Anchor] = {}
    used = set()
    for i, text in enumerate(cue_texts):
        a = "".join(tokens(text))
        if len(a) < 3:
            continue
        ps, pe = predicted[i]
        best, best_ratio = None, 0.0
        for k, scr in enumerate(screens):
            if k in used or abs(scr["start_time"] - ps) > SCREEN_WINDOW:
                continue
            b = "".join(tokens(scr["original_text"]))
            if not b:
                continue
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            if a in b or b in a:
                ratio = max(ratio, 0.9)
            if ratio > best_ratio:
                best, best_ratio = k, ratio
        if best is not None and best_ratio >= 0.75:
            used.add(best)
            scr = screens[best]
            anchors[i] = Anchor(scr["start_time"], scr["end_time"], "screen", screen=best,
                                transcript=scr["original_text"])
    return anchors


# --------------------------------------------------------------------------- other language (Gemini)

def build_phrases(words: Sequence[Word]) -> List[Phrase]:
    """Short transcript phrases (split at pauses, sentence marks and every few words)."""
    phrases: List[Phrase] = []
    current: List[int] = []

    def flush() -> None:
        if current:
            a, b = current[0], current[-1]
            text = " ".join(_dedupe_text(words[a:b + 1]))
            phrases.append(Phrase(f"P{len(phrases) + 1}", words[a].start, words[b].end, text, a, b))
            current.clear()

    for k, w in enumerate(words):
        if current:
            prev = words[current[-1]]
            if (w.start - prev.end >= PHRASE_GAP or len(current) >= PHRASE_WORDS
                    or re.search(r"[.?!,;:]$", prev.text)):
                flush()
        current.append(k)
    flush()
    return phrases


def _dedupe_text(ws: Sequence[Word]) -> List[str]:
    """Whisper words split into several tokens ("U.S." -> u, s) appear once in the text."""
    out: List[str] = []
    prev = None
    for w in ws:
        key = (w.text, w.start)
        if key != prev:
            out.append(w.text)
        prev = key
    return out


SYNC_SYSTEM_PROMPT = """You align a subtitle file with the speech of the same video.

You get:
- numbered subtitle lines (L...) in {file_lang}, in order;
- the transcript of the audio ({spoken_lang}) as short phrases (P...) with their start time;
- texts shown on screen (S...) with their time, when there are any.

The subtitle timing may be off by many seconds, so match by MEANING, not by time.
For every subtitle line return one match:
- kind "speech": the first and last phrase whose words the line translates (one line may span several phrases; two consecutive lines may share a phrase when a sentence was split);
- kind "screen": the on-screen text the line translates (a sign, a place or time title);
- kind "none": nothing matches (a sound description, song lyrics, or speech missing from the transcript).
Lines and phrases are both in time order, so the matches must move forward. Never invent ids."""


def _gemini_schema():
    from typing import Literal

    from pydantic import BaseModel

    class LineMatch(BaseModel):
        line: int
        kind: Literal["speech", "screen", "none"]
        first: Optional[str] = None
        last: Optional[str] = None
        screen: Optional[str] = None

    class MatchResponse(BaseModel):
        matches: List[LineMatch]

    return MatchResponse


def _batch_prompt(batch: Sequence[int], cue_texts: Sequence[str], phrases: Sequence[Phrase],
                  screens: Sequence[Tuple[int, Dict[str, Any]]]) -> str:
    lines = "\n".join(f"L{i + 1}: {cue_texts[i].replace(chr(10), ' / ')}" for i in batch)
    trans = "\n".join(f"{p.pid} [{p.start:.1f}s] {p.text}" for p in phrases) or "(no speech)"
    out = f"Subtitle lines:\n{lines}\n\nTranscript phrases:\n{trans}"
    if screens:
        out += "\n\nOn-screen texts:\n" + "\n".join(
            f"S{k + 1} [{s['start_time']:.1f}s] {s['original_text']}" for k, s in screens)
    return out


async def match_with_gemini(
    client: Any,
    call: Any,                                  # GeminiCallSettings (model, thinking, flex)
    cue_texts: Sequence[str],
    predicted: Sequence[Tuple[float, float]],
    words: Sequence[Word],
    screens: Sequence[Dict[str, Any]],
    file_lang: str,
    spoken_lang: str,
    usage: Any = None,
    flex_state: Any = None,
    cancel: Any = None,
    on_batch: Any = None,
) -> Tuple[Dict[int, Anchor], Dict[str, int]]:
    """Anchors from Gemini, batch by batch. Failed batches are skipped (stats["failed"])."""
    from google.genai import types

    from app.core.model_catalog import default_temperature, is_unavailable_error, thinking_config
    from app.providers import gemini_calls

    schema = _gemini_schema()
    phrases = build_phrases(words)
    p_starts = [p.start for p in phrases]
    system = SYNC_SYSTEM_PROMPT.format(file_lang=file_lang, spoken_lang=spoken_lang)
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=default_temperature(call.model, 0.0),
        thinking_config=thinking_config(call.model, call.thinking),
    )
    order = list(range(len(cue_texts)))
    batches = [order[k:k + GEMINI_BATCH] for k in range(0, len(order), GEMINI_BATCH)]
    anchors: Dict[int, Anchor] = {}
    stats = {"batches": len(batches), "failed": 0, "speech": 0, "screen": 0, "none": 0}
    sem = asyncio.Semaphore(GEMINI_CONCURRENCY)
    fatal: List[Exception] = []

    async def run(batch: List[int]) -> None:
        if fatal or (cancel is not None and cancel.is_set()):
            return
        lo_t = min(predicted[i][0] for i in batch) - GEMINI_MARGIN
        hi_t = max(predicted[i][1] for i in batch) + GEMINI_MARGIN
        a, b = bisect.bisect_left(p_starts, lo_t), bisect.bisect_right(p_starts, hi_t)
        window = phrases[a:b]
        scr = [(k, s) for k, s in enumerate(screens) if lo_t - SCREEN_WINDOW <= s["start_time"] <= hi_t + SCREEN_WINDOW]
        prompt = _batch_prompt(batch, cue_texts, window, scr)
        async with sem:
            for attempt in range(3):
                if fatal:
                    return
                try:
                    response = await gemini_calls.generate_content(
                        client, model=call.model, contents=prompt, config=config, flex=call.flex,
                        usage=usage, stage="sync", flex_state=flex_state,
                    )
                    parsed = schema.model_validate_json(response.text)
                    _apply(parsed.matches, batch, window, scr, words, anchors, stats)
                    break
                except Exception as e:
                    if is_unavailable_error(e):
                        fatal.append(e)
                        return
                    logger.warning(f"Sync matching batch failed (attempt {attempt + 1}/3): {e}")
                    await asyncio.sleep(2 * (attempt + 1))
            else:
                stats["failed"] += 1
        if on_batch:
            await on_batch(len(batches))

    await asyncio.gather(*(run(b) for b in batches))
    if fatal:
        raise RuntimeError(f"{call.model}: {fatal[0]}")
    speech = {k: v for k, v in anchors.items() if v.source == "gemini"}
    others = {k: v for k, v in anchors.items() if v.source != "gemini"}
    kept = _monotonic(_split_shared(speech, cue_texts))
    return {**kept, **others}, stats


def _apply(matches, batch, window: Sequence[Phrase], screens, words: Sequence[Word],
           anchors: Dict[int, Anchor], stats: Dict[str, int]) -> None:
    by_id = {p.pid: p for p in window}
    scr_by_id = {f"S{k + 1}": (k, s) for k, s in screens}
    allowed = {i + 1 for i in batch}
    for m in matches:
        if m.line not in allowed:
            continue
        i = m.line - 1
        if m.kind == "speech" and m.first in by_id:
            p1 = by_id[m.first]
            p2 = by_id.get(m.last or m.first, p1)
            if p2.start < p1.start:
                p1, p2 = p2, p1
            text = " ".join(_dedupe_text(words[p1.first_word:p2.last_word + 1]))
            anchors[i] = Anchor(round(p1.start, 3), round(p2.end, 3), "gemini", transcript=text)
            stats["speech"] += 1
        elif m.kind == "screen" and m.screen in scr_by_id:
            k, s = scr_by_id[m.screen]
            anchors[i] = Anchor(s["start_time"], s["end_time"], "screen", screen=k, transcript=s["original_text"])
            stats["screen"] += 1
        else:
            stats["none"] += 1


def _split_shared(anchors: Dict[int, Anchor], cue_texts: Sequence[str]) -> Dict[int, Anchor]:
    """Consecutive lines matched to the same phrase span share its time by text length."""
    keys = sorted(anchors)
    out = dict(anchors)
    k = 0
    while k < len(keys):
        group = [keys[k]]
        while (k + len(group) < len(keys) and keys[k + len(group)] == group[-1] + 1
               and _same_span(anchors[keys[k + len(group)]], anchors[group[0]])):
            group.append(keys[k + len(group)])
        if len(group) > 1:
            a = anchors[group[0]]
            sizes = [max(1, len(cue_texts[i])) for i in group]
            total, t = sum(sizes), a.start
            for i, size in zip(group, sizes):
                length = (a.end - a.start) * size / total
                out[i] = Anchor(round(t, 3), round(t + length, 3), a.source, transcript=a.transcript)
                t += length
        k += len(group)
    return out


def _same_span(a: Anchor, b: Anchor) -> bool:
    return abs(a.start - b.start) < 0.05 and abs(a.end - b.end) < 0.05
