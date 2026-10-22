"""
Split multi-speaker subtitle segments into separate single-speaker segments.

Handles two cases:
1. Subtitles where speaker turns are marked with dialogue hyphens:
   - Two or more lines starting with '- ' (e.g. "- Hello.\\n- Hi.")
   - Second line starting with '- ' after an unmarked first line (e.g. "Show respect.\\n- I do.")
   - Inline speaker change after terminal punctuation (e.g. "I'll come tomorrow. - All right.")
2. Preserves non-dialogue hyphens such as centered titles ("- Summer 2018 -"),
   on-screen bracketed captions ("[POLICE REPORT - CALWELL]"), and hyphenated words ("ה-GPR").
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from app.utils.rtl_fixer import strip_directional_marks
from app.utils.srt_formatter import calculate_cps, split_long_lines
from app.utils.text_rules import ends_sentence

# Leading dialogue dash at the start of a line: "- Hello", "-שלום", "– Hi", "— Hi"
_LEADING_DASH = re.compile(r"^\s*[-–—](?:\s+|(?=[^\s\d\-–—]))")

# Inline dialogue dash following sentence-ending punctuation:
# Matches ". - ", "? - ", "! - ", "... - ", and compact ". -Wow", "? -Yeah"
_INLINE_TURN_SPLIT = re.compile(
    r"(?<=[.!?…])\s+[-–—](?:\s+|(?=[^\s\d\-–—]))|(?<=[.!?…][\"'”’״׳])\s+[-–—](?:\s+|(?=[^\s\d\-–—]))"
)

# Trailing dialogue dash right before a newline after terminal punctuation:
# e.g. "I don't know. -\nI mean..." -> "I don't know.\n- I mean..."
_TRAILING_DASH_BEFORE_NL = re.compile(r"(?<=[.!?…])\s+[-–—]\s*\n\s*")

# Centered title wrapped in dashes on both sides: "- 1 ביולי -", "- קיץ 2018 -"
_CENTERED_TITLE = re.compile(r"^\s*[-–—]\s+.+\s+[-–—]\s*$")


def _is_bracketed_line(line: str) -> bool:
    s = line.strip()
    return (s.startswith("[") and s.endswith("]")) or (s.startswith("(") and s.endswith(")"))


def _is_centered_title(line: str) -> bool:
    return bool(_CENTERED_TITLE.match(line.strip()))


def _strip_leading_dash(text: str) -> str:
    return _LEADING_DASH.sub("", text.strip(), count=1).strip()


def extract_speaker_turns(text: str) -> Tuple[Optional[str], List[str]]:
    """
    Extract speaker turns from a subtitle block.

    Returns (header_caption, turns):
    - `header_caption`: an optional "[...]" on-screen line at the top of the cue, or None.
    - `turns`: list of clean speaker turns (without leading '- '). If len(turns) <= 1,
      the subtitle has only one speaker.
    """
    clean = strip_directional_marks(text or "").strip()
    if not clean:
        return None, []

    # Normalize trailing turn dashes before newline: e.g. "know. -\nI mean" -> "know.\n- I mean"
    clean = _TRAILING_DASH_BEFORE_NL.sub("\n- ", clean)

    raw_lines = [ln.strip() for ln in clean.splitlines() if ln.strip()]
    if not raw_lines:
        return None, []

    # If all lines are bracketed captions or centered titles, never split
    if all(_is_bracketed_line(ln) or _is_centered_title(ln) for ln in raw_lines):
        return None, [clean]

    header: Optional[str] = None
    dialogue_lines = raw_lines
    if len(raw_lines) >= 2 and _is_bracketed_line(raw_lines[0]):
        header = raw_lines[0]
        dialogue_lines = raw_lines[1:]

    if len(dialogue_lines) == 1 and _is_centered_title(dialogue_lines[0]):
        return header, [dialogue_lines[0]]

    # Expand each line by inline turn splits (". - ", "? - ", "! - ")
    pieces: List[Tuple[bool, str]] = []  # (starts_new_turn, text)
    for line_idx, line in enumerate(dialogue_lines):
        has_leading_dash = bool(_LEADING_DASH.match(line))
        inline_parts = _INLINE_TURN_SPLIT.split(line)
        for part_idx, part in enumerate(inline_parts):
            part = part.strip()
            if not part:
                continue
            if part_idx > 0:
                # Inline split after terminal punctuation always starts a new speaker turn
                pieces.append((True, _strip_leading_dash(part)))
            elif has_leading_dash:
                pieces.append((True, _strip_leading_dash(part)))
            else:
                pieces.append((False, part))

    if not pieces:
        return header, [clean]

    # Count how many explicit new-turn markers exist
    explicit_turns = sum(1 for is_new, _ in pieces if is_new)
    if explicit_turns == 0:
        return header, ["\n".join(p for _, p in pieces)]

    # Group pieces into speaker turns:
    # - The first piece always opens Turn 1.
    # - A subsequent piece opens a new turn if `starts_new_turn` is True,
    #   EXCEPT when Turn 1 had NO leading dash and the only dash is on piece 0 (impossible here).
    turns: List[str] = []
    for idx, (is_new, part) in enumerate(pieces):
        if idx == 0:
            turns.append(part)
        elif is_new:
            turns.append(part)
        else:
            turns[-1] = f"{turns[-1]}\n{part}"

    turns = [t.strip() for t in turns if t.strip()]
    return header, turns


def _split_into_sentences(text: str) -> List[str]:
    """Split plain text into sentences using `ends_sentence` word heuristics."""
    words = text.split()
    if not words:
        return []
    sentences: List[str] = []
    cur: List[str] = []
    for w in words:
        cur.append(w)
        if ends_sentence(w):
            sentences.append(" ".join(cur))
            cur = []
    if cur:
        if sentences and len(cur) == 1 and not ends_sentence(cur[0]):
            sentences[-1] = f"{sentences[-1]} {cur[0]}"
        else:
            sentences.append(" ".join(cur))
    return sentences


def _partition_items(items: List[str], target_turns: List[str]) -> List[str]:
    """
    Partition `items` (sentences or words) into K=len(target_turns) contiguous non-empty groups
    whose relative character lengths and sentence counts best match `target_turns`.
    """
    k = len(target_turns)
    m = len(items)
    if k <= 1 or m == 0:
        return [" ".join(items)] if items else [""] * k
    if m == k:
        return list(items)
    if m < k:
        # Fewer items than turns: fall back to word-level partitioning
        words = " ".join(items).split()
        if len(words) >= k:
            return _partition_items(words, target_turns)
        # Pad with empty strings if even words < k
        return list(items) + [""] * (k - m)

    target_lens = [max(1, len(t)) for t in target_turns]
    total_target_len = sum(target_lens)
    target_shares = [tl / total_target_len for tl in target_lens]
    target_sent_counts = [max(1, len(_split_into_sentences(t))) for t in target_turns]
    total_target_sents = sum(target_sent_counts)

    item_lens = [max(1, len(it)) for it in items]
    total_item_len = sum(item_lens)

    # DP over (group_idx 0..k, item_idx 0..m)
    inf = float("inf")
    dp = [[inf] * (m + 1) for _ in range(k + 1)]
    parent = [[0] * (m + 1) for _ in range(k + 1)]
    dp[0][0] = 0.0

    for g in range(1, k + 1):
        for j in range(g, m - (k - g) + 1):
            for i in range(g - 1, j):
                if dp[g - 1][i] == inf:
                    continue
                group_len = sum(item_lens[i:j]) + (j - i - 1)
                share = group_len / max(1, total_item_len)
                len_cost = abs(share - target_shares[g - 1])
                sent_share = (j - i) / max(1, m)
                target_sent_share = target_sent_counts[g - 1] / max(1, total_target_sents)
                sent_cost = abs(sent_share - target_sent_share) * 0.5
                cost = dp[g - 1][i] + len_cost + sent_cost
                if cost < dp[g][j]:
                    dp[g][j] = cost
                    parent[g][j] = i

    groups: List[str] = []
    curr = m
    for g in range(k, 0, -1):
        prev = parent[g][curr]
        groups.append(" ".join(items[prev:curr]))
        curr = prev
    groups.reverse()
    return groups


def split_source_for_turns(original_text: str, target_turns: List[str]) -> List[str]:
    """Match `original_text` into len(target_turns) parts corresponding to `target_turns`."""
    k = len(target_turns)
    if k <= 1:
        return [original_text]

    _, orig_turns = extract_speaker_turns(original_text)
    if len(orig_turns) == k:
        return orig_turns

    clean_orig = " ".join(strip_directional_marks(original_text or "").split())
    if not clean_orig:
        return [""] * k

    sentences = _split_into_sentences(clean_orig)
    if len(sentences) >= k:
        return _partition_items(sentences, target_turns)

    words = clean_orig.split()
    return _partition_items(words, target_turns)


def allocate_turn_timings(
    start_time: float,
    end_time: float,
    turns: List[str],
    min_gap: float = 0.08,
) -> List[Tuple[float, float]]:
    """
    Divide [start_time, end_time] proportionally across `turns` while keeping `min_gap`
    between consecutive turns.
    """
    k = len(turns)
    if k <= 1:
        return [(round(start_time, 3), round(end_time, 3))]

    total_span = max(0.2 * k, end_time - start_time)
    gap = min_gap if total_span - min_gap * (k - 1) >= 0.4 * k else 0.04
    if total_span - gap * (k - 1) < 0.2 * k:
        gap = 0.0

    avail = max(0.1 * k, total_span - gap * (k - 1))
    # Give every turn a floor weight so very short replies ("בסדר.", "No.") still get readable time
    weights = [max(6, len(t.replace("\n", "").strip())) for t in turns]
    total_weight = sum(weights)

    timings: List[Tuple[float, float]] = []
    cursor = start_time
    for idx, w in enumerate(weights):
        if idx == k - 1:
            seg_end = max(cursor + 0.1, end_time)
        else:
            dur = avail * (w / total_weight)
            seg_end = cursor + dur
        timings.append((round(cursor, 3), round(seg_end, 3)))
        cursor = seg_end + gap

    return timings


def split_dual_speaker_segment(
    seg: Dict[str, Any],
    max_chars_per_line: int = 42,
    min_gap: float = 0.08,
) -> List[Dict[str, Any]]:
    """
    If `seg` contains multiple speakers (in `translated_text` or `original_text`),
    split it into separate consecutive single-speaker segment dicts.
    Otherwise return `[seg]` (with any lone dialogue dash cleaned if appropriate).
    """
    seg_type = seg.get("segment_type") or "DIALOGUE"
    if seg_type == "ON_SCREEN":
        return [seg]

    translated = seg.get("translated_text")
    original = seg.get("original_text") or ""

    if translated:
        header, trans_turns = extract_speaker_turns(translated)
        orig_header, _ = extract_speaker_turns(original)
        if len(trans_turns) >= 2 or (header is not None and len(trans_turns) >= 1):
            orig_body = original
            if orig_header and original.lstrip().startswith(orig_header):
                orig_body = original.lstrip()[len(orig_header):].strip()
            orig_turns = split_source_for_turns(orig_body, trans_turns)
            timings = allocate_turn_timings(
                float(seg["start_time"]),
                float(seg["end_time"]),
                trans_turns,
                min_gap=min_gap,
            )
            out: List[Dict[str, Any]] = []
            if header or orig_header:
                onscreen_child = dict(seg)
                onscreen_child.pop("id", None)
                onscreen_child["segment_type"] = "ON_SCREEN"
                onscreen_child["original_text"] = orig_header or header or ""
                onscreen_child["translated_text"] = header or orig_header
                onscreen_child["cps"] = calculate_cps(
                    onscreen_child["translated_text"] or onscreen_child["original_text"],
                    max(0.1, float(seg["end_time"]) - float(seg["start_time"])),
                )
                out.append(onscreen_child)
            for t_turn, o_turn, (st, et) in zip(trans_turns, orig_turns, timings):
                t_formatted = split_long_lines(t_turn, max_chars_per_line=max_chars_per_line)
                o_formatted = split_long_lines(o_turn or orig_body or original, max_chars_per_line=max_chars_per_line)
                child = dict(seg)
                child.pop("id", None)
                child["start_time"] = st
                child["end_time"] = et
                child["original_text"] = o_formatted
                child["translated_text"] = t_formatted
                child["cps"] = calculate_cps(t_formatted, max(0.1, et - st))
                out.append(child)
            return out

    # Also check if untranslated `original_text` itself has multiple speaker turns or a bracketed header
    orig_header, orig_turns = extract_speaker_turns(original)
    if not translated and (len(orig_turns) >= 2 or (orig_header is not None and len(orig_turns) >= 1)):
        timings = allocate_turn_timings(
            float(seg["start_time"]),
            float(seg["end_time"]),
            orig_turns,
            min_gap=min_gap,
        )
        out = []
        if orig_header:
            onscreen_child = dict(seg)
            onscreen_child.pop("id", None)
            onscreen_child["segment_type"] = "ON_SCREEN"
            onscreen_child["original_text"] = orig_header
            onscreen_child["translated_text"] = None
            onscreen_child["cps"] = calculate_cps(
                orig_header,
                max(0.1, float(seg["end_time"]) - float(seg["start_time"])),
            )
            out.append(onscreen_child)
        for o_turn, (st, et) in zip(orig_turns, timings):
            o_formatted = split_long_lines(o_turn, max_chars_per_line=max_chars_per_line)
            child = dict(seg)
            child.pop("id", None)
            child["start_time"] = st
            child["end_time"] = et
            child["original_text"] = o_formatted
            child["translated_text"] = None
            child["cps"] = calculate_cps(o_formatted, max(0.1, et - st))
            out.append(child)
        return out

    return [seg]


# Words that commonly stand alone as a 1-word sentence at the start of a subtitle
# and should NOT be glued onto the end of the previous subtitle unless the previous
# subtitle ends with an explicit weak/incomplete word.
_STANDALONE_ONE_WORD_SENTENCES_HE = {
    "כן", "לא", "טוב", "בסדר", "נכון", "תודה", "סליחה", "רגע", "חכה", "חכי",
    "קדימה", "שלום", "היי", "ביי", "אוקיי", "אלוהים", "לעזאזל", "שיט", "פאק",
    "וואו", "ברור", "בדיוק", "יפה", "נהדר", "מעולה", "סגור", "תירגע", "תירגעי",
    "תפסיק", "תפסיקי", "די", "מה", "למה", "איך", "מי", "איפה", "מתי", "באמת",
    "ברצינות", "הלו", "נו", "יאללה", "אמן", "בול", "חבל", "בטוח", "לגמרי",
}

_STANDALONE_ONE_WORD_SENTENCES_EN = {
    "yes", "no", "yeah", "yep", "nope", "okay", "ok", "right", "sure", "good",
    "great", "fine", "thanks", "sorry", "wait", "stop", "go", "hey", "hi", "hello",
    "bye", "what", "why", "how", "who", "where", "when", "really", "seriously",
    "huh", "wow", "damn", "shit", "fuck", "please", "exactly", "correct", "true",
    "nice", "cool", "awesome", "perfect", "whatever", "never", "always", "maybe",
}

_SENTENCE_CLOSERS_RE = re.compile(r"""[.!?…]+["'”’״׳)\]]*$""")
_STRICT_PERIOD_EXCL_RE = re.compile(r"""(?:(?<!\.)\.(?!\.)|[!])["'”’״׳)\]]*$""")
_ELLIPSIS_END_RE = re.compile(r"""(?:\.\.\.|…)+["'”’״׳)\]]*$""")
_HARD_SENT_END_RE = re.compile(r"""(?:(?<!\.)\.(?!\.)|[!?…]+|\.\.\.)["'”’״׳)\]]*$""")
_PUNCT_STRIP = "\"'“”‘’״׳()[]{}.,!?;:…-—–"

# Hebrew function/incomplete words that strongly indicate the sentence did not finish
_HE_WEAK_END_WORDS = {
    "מה", "מי", "איך", "איפה", "מתי", "למה", "כמה", "איזה", "איזו", "אילו",
    "של", "על", "אל", "עם", "את", "כל", "גם", "רק", "עוד", "יותר", "פחות",
    "לא", "אין", "יש", "היה", "הייתה", "יהיה", "להיות", "יכול", "יכולה", "יכולים",
    "רוצה", "רוצים", "צריך", "צריכה", "צריכים", "חייב", "חייבת", "חייבים",
    "בטוח", "בטוחה", "יודע", "יודעת", "חושב", "חושבת", "אומר", "אומרת",
    "כדי", "בגלל", "לפני", "אחרי", "כמו", "בין", "תחת", "מול", "ליד", "בתוך",
    "להגיע", "לעשות", "לראות", "לדעת", "ללכת", "לבוא", "לקחת", "לתת", "למצוא",
    "בכירי", "אנשי", "חברי", "ראשי", "בעלי", "בית", "חדר", "מערכת",
}

# Trailing 1-2 words after a finished sentence that clearly open the next sentence
_HE_DANGLING_OPENERS = {
    "אני", "את", "אתה", "הוא", "היא", "אנחנו", "אתם", "הם", "הן",
    "זה", "זאת", "זו", "אלה", "אלו", "יש", "אין", "אל", "לא",
    " אם", "אם", "כש", "כי", "אבל", "אך", "או", "אז", "רק", "גם",
    "היי", "שלום", "תראה", "תראי", "תשמע", "תשמעי", "חכה", "חכי", "בוא", "בואי", "בואו",
    "יש כאן", "אני לא", "זה לא", "אתה לא", "הוא לא", "היא לא", "אנחנו לא",
}

_EN_DANGLING_OPENERS = {
    "i", "you", "he", "she", "we", "they", "it", "that", "this", "there", "here",
    "i'm", "you're", "he's", "she's", "we're", "they're", "it's", "that's", "there's",
    "don't", "can't", "won't", "didn't", "isn't", "aren't", "wasn't", "weren't",
    "let's", "please", "hey", "hi", "hello", "look", "listen", "wait", "well",
    "if", "when", "while", "because", "since", "although", "though", "until",
    "a", "an", "the", "my", "your", "his", "her", "our", "their", "some", "any", "no",
    "there is", "there are", "there was", "i am", "i was", "i have", "you have",
    "we have", "he is", "she is", "it is", "that is", "do not", "can not",
}


def _bare_token(word: str) -> str:
    return strip_directional_marks(word or "").strip().strip(_PUNCT_STRIP).lower()


def _ends_with_terminal(text: str) -> bool:
    s = strip_directional_marks(text or "").strip()
    if not s:
        return False
    return bool(_SENTENCE_CLOSERS_RE.search(s))


def _extract_leading_orphan_tail(
    prev_text: str,
    next_text: str,
    *,
    is_hebrew: bool,
    orig_prev_incomplete: bool = False,
) -> Optional[Tuple[str, str]]:
    """
    If `prev_text` is an unfinished sentence and `next_text` begins with 1..3 words
    ending with a period/exclamation (or question mark when `prev_text` is clearly incomplete),
    followed by another sentence in `next_text`, return `(orphan_prefix, remaining_next_text)`.
    """
    prev_clean = " ".join(strip_directional_marks(prev_text or "").split())
    next_clean = " ".join(strip_directional_marks(next_text or "").split())
    if not prev_clean or not next_clean:
        return None

    # Do not merge across explicit speaker dashes or bracketed captions
    if _LEADING_DASH.match(next_clean) or _is_bracketed_line(prev_clean) or _is_bracketed_line(next_clean):
        return None

    # Previous text must NOT already end with terminal punctuation (., ?, !, ...)
    if _ends_with_terminal(prev_clean):
        return None

    prev_words = prev_clean.split()
    next_words = next_clean.split()
    if not prev_words or len(next_words) < 3:
        return None

    prev_ends_comma = prev_clean.endswith((",", ";", ":"))
    prev_last_bare = _bare_token(prev_words[-1])
    from app.utils.text_rules import WEAK_END_WORDS
    prev_has_weak_end = (
        prev_last_bare in (_HE_WEAK_END_WORDS if is_hebrew else WEAK_END_WORDS)
        or len(prev_words) == 1
    )

    # If `prev_text` ends with a comma, only pull a 1-2 word tail if the English source
    # also lacked sentence-ending punctuation (e.g. "...לך וללו," | "לבד. חשבתי..." or "...מפקיסטן," | "מלאלה. אדם...")
    if prev_ends_comma and not orig_prev_incomplete:
        return None

    max_k = 2 if prev_ends_comma else 3
    for k in range(1, min(max_k, len(next_words) - 1) + 1):
        cand_word = next_words[k - 1]
        # Avoid splitting on abbreviations like "Dr." or "Mr."
        if not is_hebrew and not ends_sentence(cand_word):
            continue
        if _ELLIPSIS_END_RE.search(cand_word):
            continue
        is_period_or_excl = bool(_STRICT_PERIOD_EXCL_RE.search(cand_word))
        is_question = cand_word.rstrip("\"'”’״׳)]").endswith("?")
        if not (is_period_or_excl or (is_question and prev_has_weak_end)):
            continue

        # Must have at least 2 words remaining in `next_text` for the new sentence
        rem_words = next_words[k:]
        if len(rem_words) < 2:
            continue

        # Check standalone 1-word responses/exclamations ("כן.", "לא.", "Okay.", "Right.")
        if k == 1:
            w0_bare = _bare_token(cand_word)
            standalone_set = _STANDALONE_ONE_WORD_SENTENCES_HE if is_hebrew else _STANDALONE_ONE_WORD_SENTENCES_EN
            if w0_bare in standalone_set and not prev_has_weak_end:
                return None

        prefix = " ".join(next_words[:k])
        remainder = " ".join(rem_words)
        return prefix, remainder

    return None


def _extract_trailing_orphan_lead(
    prev_text: str,
    next_text: str,
    *,
    is_hebrew: bool,
) -> Optional[Tuple[str, str]]:
    """
    If `prev_text` contains a complete sentence ending with `.`, `?`, `!`, or `...`
    followed at the very end by 1..2 unfinished opener words that belong to `next_text`,
    return `(trimmed_prev_text, orphan_suffix)`.
    """
    prev_clean = " ".join(strip_directional_marks(prev_text or "").split())
    next_clean = " ".join(strip_directional_marks(next_text or "").split())
    if not prev_clean or not next_clean:
        return None

    if _LEADING_DASH.match(next_clean) or _is_bracketed_line(prev_clean) or _is_bracketed_line(next_clean):
        return None

    # `prev_text` itself must NOT end with terminal punctuation
    if _ends_with_terminal(prev_clean):
        return None

    words = prev_clean.split()
    if len(words) < 3:
        return None

    # Find the last word in `prev_text` that ends a sentence
    last_sent_idx = -1
    for idx in range(len(words) - 2, 0, -1):
        w = words[idx]
        if _HARD_SENT_END_RE.search(w) and (is_hebrew or ends_sentence(w)):
            last_sent_idx = idx
            break

    if last_sent_idx < 0:
        return None

    tail_words = words[last_sent_idx + 1:]
    if not (1 <= len(tail_words) <= 2):
        return None

    tail_bare_join = " ".join(_bare_token(w) for w in tail_words).strip()
    if not tail_bare_join:
        return None

    from app.utils.text_rules import WEAK_END_WORDS
    if is_hebrew:
        is_valid_opener = (
            tail_bare_join in _HE_DANGLING_OPENERS
            or (len(tail_words) == 1 and tail_bare_join in _HE_WEAK_END_WORDS)
        )
    else:
        is_valid_opener = (
            tail_bare_join in _EN_DANGLING_OPENERS
            or (len(tail_words) == 1 and tail_bare_join in WEAK_END_WORDS)
            or (len(tail_words) == 2 and _bare_token(tail_words[-1]) in WEAK_END_WORDS)
        )

    if not is_valid_opener:
        return None

    trimmed_prev = " ".join(words[:last_sent_idx + 1])
    orphan_suffix = " ".join(tail_words)
    return trimmed_prev, orphan_suffix


def heal_split_sentences(
    segments: List[Dict[str, Any]],
    max_chars_per_line: int = 42,
    min_gap: float = 0.08,
    max_pause_gap: float = 4.0,
) -> List[Dict[str, Any]]:
    """
    Repair consecutive subtitle segments where:
    - (FIX-A) The last 1..3 words of an unfinished sentence in segment `i` landed at the
      start of segment `i + 1` followed by a period and the next sentence.
    - (FIX-B) The first 1..2 words of a new sentence in segment `i + 1` were stranded at
      the tail of segment `i` after a finished sentence.
    """
    if len(segments) < 2:
        return segments

    out = [dict(s) for s in segments]

    for i in range(len(out) - 1):
        a, b = out[i], out[i + 1]
        if (a.get("segment_type") or "DIALOGUE") == "ON_SCREEN":
            continue
        if (b.get("segment_type") or "DIALOGUE") == "ON_SCREEN":
            continue

        a_start, a_end = float(a.get("start_time", 0.0)), float(a.get("end_time", 0.0))
        b_start, b_end = float(b.get("start_time", 0.0)), float(b.get("end_time", 0.0))
        if b_start - a_end > max_pause_gap:
            continue

        a_trans = a.get("translated_text")
        b_trans = b.get("translated_text")
        a_orig = a.get("original_text") or ""
        b_orig = b.get("original_text") or ""

        orig_prev_incomplete = bool(a_orig.strip()) and not _ends_with_terminal(a_orig)

        # ------------------------------------------------------------------
        # 1. Try FIX-A: pull leading 1..3 word sentence tail from `b` into `a`
        # ------------------------------------------------------------------
        if a_trans and b_trans:
            he_fix_a = _extract_leading_orphan_tail(
                a_trans,
                b_trans,
                is_hebrew=True,
                orig_prev_incomplete=orig_prev_incomplete,
            )
            if he_fix_a is not None:
                he_prefix, he_rem = he_fix_a
                new_a_trans = f"{strip_directional_marks(a_trans).strip()} {he_prefix}"
                new_b_trans = he_rem
                a["translated_text"] = split_long_lines(new_a_trans, max_chars_per_line=max_chars_per_line)
                b["translated_text"] = split_long_lines(new_b_trans, max_chars_per_line=max_chars_per_line)

                # Also heal `original_text` if it has a matching split or unpunctuated continuation
                en_fix_a = _extract_leading_orphan_tail(
                    a_orig,
                    b_orig,
                    is_hebrew=False,
                    orig_prev_incomplete=True,
                )
                if en_fix_a is not None:
                    en_prefix, en_rem = en_fix_a
                    a["original_text"] = split_long_lines(
                        f"{a_orig.strip()} {en_prefix}", max_chars_per_line=max_chars_per_line
                    )
                    b["original_text"] = split_long_lines(en_rem, max_chars_per_line=max_chars_per_line)
                elif orig_prev_incomplete:
                    # If English was unpunctuated (e.g. "yeah i'm not sure what" | "this is what in the world..."),
                    # shift the corresponding number of words if `a_orig` ends on a weak word.
                    a_orig_words = a_orig.split()
                    b_orig_words = b_orig.split()
                    from app.utils.text_rules import WEAK_END_WORDS
                    if (
                        a_orig_words
                        and _bare_token(a_orig_words[-1]) in WEAK_END_WORDS
                        and len(b_orig_words) >= 4
                    ):
                        k_en = min(2, max(1, len(he_prefix.split())))
                        # If moving 2 words lands on "is"/"it"/"that"/"this", move 2; else move 1
                        if k_en == 1 and len(b_orig_words) >= 5 and _bare_token(b_orig_words[0]) in {"this", "that", "it"} and _bare_token(b_orig_words[1]) in {"is", "was"}:
                            k_en = 2
                        moved_en = " ".join(b_orig_words[:k_en])
                        rem_en = " ".join(b_orig_words[k_en:])
                        a["original_text"] = split_long_lines(
                            f"{a_orig.strip()} {moved_en}", max_chars_per_line=max_chars_per_line
                        )
                        b["original_text"] = split_long_lines(rem_en, max_chars_per_line=max_chars_per_line)

                # Shift timing boundary forward slightly so `a` covers its ending word(s)
                b_dur = max(0.2, b_end - b_start)
                if b_dur >= 1.2 and b_start - a_end <= 1.2:
                    ratio = len(he_prefix) / max(1, len(he_prefix) + len(he_rem))
                    shift = min(0.55, max(0.18, b_dur * ratio))
                    if b_end - (b_start + shift) >= 0.8:
                        new_b_start = round(b_start + shift, 3)
                        new_a_end = round(max(a_end, new_b_start - min_gap), 3)
                        a["end_time"] = new_a_end
                        b["start_time"] = new_b_start

                a["cps"] = calculate_cps(a["translated_text"], max(0.1, float(a["end_time"]) - float(a["start_time"])))
                b["cps"] = calculate_cps(b["translated_text"], max(0.1, float(b["end_time"]) - float(b["start_time"])))
                continue
        elif not a_trans and not b_trans and a_orig and b_orig:
            en_fix_a = _extract_leading_orphan_tail(
                a_orig,
                b_orig,
                is_hebrew=False,
                orig_prev_incomplete=orig_prev_incomplete,
            )
            if en_fix_a is not None:
                en_prefix, en_rem = en_fix_a
                a["original_text"] = split_long_lines(
                    f"{a_orig.strip()} {en_prefix}", max_chars_per_line=max_chars_per_line
                )
                b["original_text"] = split_long_lines(en_rem, max_chars_per_line=max_chars_per_line)
                b_dur = max(0.2, b_end - b_start)
                if b_dur >= 1.2 and b_start - a_end <= 1.2:
                    ratio = len(en_prefix) / max(1, len(en_prefix) + len(en_rem))
                    shift = min(0.55, max(0.18, b_dur * ratio))
                    if b_end - (b_start + shift) >= 0.8:
                        new_b_start = round(b_start + shift, 3)
                        new_a_end = round(max(a_end, new_b_start - min_gap), 3)
                        a["end_time"] = new_a_end
                        b["start_time"] = new_b_start
                a["cps"] = calculate_cps(a["original_text"], max(0.1, float(a["end_time"]) - float(a["start_time"])))
                b["cps"] = calculate_cps(b["original_text"], max(0.1, float(b["end_time"]) - float(b["start_time"])))
                continue

        # ------------------------------------------------------------------
        # 2. Try FIX-B: push trailing 1..2 word sentence opener from `a` into `b`
        # ------------------------------------------------------------------
        if a_trans and b_trans:
            he_fix_b = _extract_trailing_orphan_lead(a_trans, b_trans, is_hebrew=True)
            if he_fix_b is not None:
                he_trimmed_a, he_suffix = he_fix_b
                new_b_trans = f"{he_suffix} {strip_directional_marks(b_trans).strip()}"
                a["translated_text"] = split_long_lines(he_trimmed_a, max_chars_per_line=max_chars_per_line)
                b["translated_text"] = split_long_lines(new_b_trans, max_chars_per_line=max_chars_per_line)

                en_fix_b = _extract_trailing_orphan_lead(a_orig, b_orig, is_hebrew=False)
                if en_fix_b is not None:
                    en_trimmed_a, en_suffix = en_fix_b
                    a["original_text"] = split_long_lines(en_trimmed_a, max_chars_per_line=max_chars_per_line)
                    b["original_text"] = split_long_lines(
                        f"{en_suffix} {b_orig.strip()}", max_chars_per_line=max_chars_per_line
                    )
                else:
                    # Also check if unpunctuated English ends with the same number of trailing opener words
                    a_orig_words = a_orig.split()
                    k_en = len(he_suffix.split())
                    if len(a_orig_words) > k_en + 2:
                        tail_en = " ".join(_bare_token(w) for w in a_orig_words[-k_en:])
                        if tail_en in _EN_DANGLING_OPENERS:
                            moved_en = " ".join(a_orig_words[-k_en:])
                            a["original_text"] = split_long_lines(
                                " ".join(a_orig_words[:-k_en]), max_chars_per_line=max_chars_per_line
                            )
                            b["original_text"] = split_long_lines(
                                f"{moved_en} {b_orig.strip()}", max_chars_per_line=max_chars_per_line
                            )

                a_dur = max(0.2, a_end - a_start)
                if a_dur >= 1.2 and b_start - a_end <= 1.2:
                    ratio = len(he_suffix) / max(1, len(he_trimmed_a) + len(he_suffix))
                    shift = min(0.45, max(0.15, a_dur * ratio))
                    if (a_end - shift) - a_start >= 0.8:
                        new_a_end = round(a_end - shift, 3)
                        new_b_start = round(min(b_start, new_a_end + min_gap), 3)
                        a["end_time"] = new_a_end
                        b["start_time"] = new_b_start

                a["cps"] = calculate_cps(a["translated_text"], max(0.1, float(a["end_time"]) - float(a["start_time"])))
                b["cps"] = calculate_cps(b["translated_text"], max(0.1, float(b["end_time"]) - float(b["start_time"])))
        elif not a_trans and not b_trans and a_orig and b_orig:
            en_fix_b = _extract_trailing_orphan_lead(a_orig, b_orig, is_hebrew=False)
            if en_fix_b is not None:
                en_trimmed_a, en_suffix = en_fix_b
                a["original_text"] = split_long_lines(en_trimmed_a, max_chars_per_line=max_chars_per_line)
                b["original_text"] = split_long_lines(
                    f"{en_suffix} {b_orig.strip()}", max_chars_per_line=max_chars_per_line
                )
                a_dur = max(0.2, a_end - a_start)
                if a_dur >= 1.2 and b_start - a_end <= 1.2:
                    ratio = len(en_suffix) / max(1, len(en_trimmed_a) + len(en_suffix))
                    shift = min(0.45, max(0.15, a_dur * ratio))
                    if (a_end - shift) - a_start >= 0.8:
                        new_a_end = round(a_end - shift, 3)
                        new_b_start = round(min(b_start, new_a_end + min_gap), 3)
                        a["end_time"] = new_a_end
                        b["start_time"] = new_b_start
                a["cps"] = calculate_cps(a["original_text"], max(0.1, float(a["end_time"]) - float(a["start_time"])))
                b["cps"] = calculate_cps(b["original_text"], max(0.1, float(b["end_time"]) - float(b["start_time"])))

    return out


def split_dual_speaker_segments(
    segments: List[Dict[str, Any]],
    max_chars_per_line: int = 42,
    min_gap: float = 0.08,
) -> List[Dict[str, Any]]:
    """
    Heal cross-segment sentence splits, split any multi-speaker segments in `segments`
    into separate single-speaker segments, and re-index 1..N.
    """
    healed = heal_split_sentences(
        segments,
        max_chars_per_line=max_chars_per_line,
        min_gap=min_gap,
    )
    result: List[Dict[str, Any]] = []
    for seg in healed:
        result.extend(
            split_dual_speaker_segment(
                seg,
                max_chars_per_line=max_chars_per_line,
                min_gap=min_gap,
            )
        )
    result = heal_split_sentences(
        result,
        max_chars_per_line=max_chars_per_line,
        min_gap=min_gap,
    )
    for idx, seg in enumerate(result, start=1):
        seg["index"] = idx
    return result
