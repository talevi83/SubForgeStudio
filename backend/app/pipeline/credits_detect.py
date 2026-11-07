"""
Smart end-credits detection and mid/post-credits scene preservation.

Movies and TV episodes often contain several minutes of end credits accompanied by:
- Scrolling or card-based cast/crew credits ("Directed by...", "Produced by...", etc.)
- End-credits songs (transcribed by Whisper or included in embedded subtitle tracks)
- Whisper music hallucinations ("Thank you for watching", repetitive vocal loops)
- Subtitle vendor tags ("Subtitles by...", "Synced by...", copyright notices)

However, movies (especially blockbusters like Marvel films) frequently include mid-credits
or post-credits scenes ("stingers") containing real dialogue and narrative title cards
(e.g. "[THOR WILL RETURN]", "[MEANWHILE...]").

This module models credits as intervals/classifications rather than a blind cutoff:
1. Container chapter markers (`ffprobe -show_chapters`) identify explicit credits and post-credits chapters.
2. Grayscale thumbnail analysis (`thumbs.raw` from frame sampling) identifies dark scrolling credit rolls
   while splitting spans whenever a normal live-action/animated scene resumes.
3. Segment-level text & dialogue analysis strips credit cards, song lyrics, and music hallucinations
   in the tail of the video while preserving coherent conversational dialogue scenes and narrative stinger cards.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Minimum video duration (seconds) for end-credits filtering to run.
# Short clips (< 5 minutes) are never filtered for end credits.
MIN_VIDEO_DURATION_FOR_CREDITS = 300.0

# Credits detection only inspects the tail of the video (last 25%, capped at 22 minutes).
TAIL_FRACTION = 0.25
MAX_TAIL_SECONDS = 1320.0


@dataclass
class CreditAnalysis:
    """Summary of detected credit intervals and protected mid/post-credits scene intervals."""
    tail_start: float
    duration: float
    chapter_credit_spans: List[Tuple[float, float]] = field(default_factory=list)
    chapter_scene_spans: List[Tuple[float, float]] = field(default_factory=list)
    visual_credit_spans: List[Tuple[float, float]] = field(default_factory=list)
    credits_onset: Optional[float] = None


# ---------------------------------------------------------------------------
# 1. Chapter marker analysis
# ---------------------------------------------------------------------------

_CREDITS_CHAPTER_RE = re.compile(
    r"""(
        \bend\s+credits?\b
      | \bclosing\s+credits?\b
      | \bcredits?\b
      | \bend\s+titles?\b
      | \bclosing\s+titles?\b
      | \bmain\s+on\s+end\b
      | \bcast\s*(&|and)\s*crew\b
      | \babspann\b
      | \bg[eé]n[eé]rique\s*(de\s+fin)?\b
      | \bcr[eé]ditos(\s+finales)?\b
      | \btitoli\s+di\s+coda\b
      | כתוביות\s*סיום
    )""",
    re.IGNORECASE | re.VERBOSE,
)

_POST_CREDITS_SCENE_CHAPTER_RE = re.compile(
    r"""(
        \b(post|mid|after)[\s\-_]*credits?\b
      | \bstinger\b
      | \btag\s+scene\b
      | \bbonus(\s+scene)?\b
      | \bepilogue\b
      | \bextra\s+scene\b
      | \bhidden\s+scene\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def tail_window_start(duration: float) -> float:
    """Return the earliest timestamp (seconds) considered part of the video's end-credits tail."""
    if duration < MIN_VIDEO_DURATION_FOR_CREDITS:
        return float("inf")
    tail_len = min(MAX_TAIL_SECONDS, duration * TAIL_FRACTION)
    return max(0.0, duration - tail_len)


def parse_chapter_spans(
    chapters: Sequence[Dict[str, Any]],
    duration: float,
) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """
    Extract `(credit_spans, protected_scene_spans)` from ffprobe chapter dicts in the tail of the video.
    """
    if not chapters or duration < MIN_VIDEO_DURATION_FOR_CREDITS:
        return [], []

    earliest = duration * 0.68
    credit_spans: List[Tuple[float, float]] = []
    scene_spans: List[Tuple[float, float]] = []

    for ch in chapters:
        try:
            start = float(ch.get("start_time", 0.0) or 0.0)
            end = float(ch.get("end_time", duration) or duration)
        except (TypeError, ValueError):
            continue
        if end <= start or start < earliest:
            continue
        title = str(ch.get("title") or (ch.get("tags") or {}).get("title") or "").strip()
        if not title:
            continue
        if _POST_CREDITS_SCENE_CHAPTER_RE.search(title):
            scene_spans.append((start, end))
        elif _CREDITS_CHAPTER_RE.search(title):
            credit_spans.append((start, end))

    return credit_spans, scene_spans


# ---------------------------------------------------------------------------
# 2. Visual thumbnail analysis (scrolling credits on dark background vs scene)
# ---------------------------------------------------------------------------

def is_credit_roll_thumbnail(thumb: bytes, width: int = 64, height: int = 36) -> bool:
    """
    Return True if a grayscale 64x36 thumbnail exhibits the visual signature of an end-credits screen:
    - Near-black background (>= 88% of pixels have luma <= 42, mean luma <= 26)
    - Sparse bright text pixels:
      - Standard scrolling credits: 0.6% .. 22% of pixels have luma >= 125
      - Minimalist single-line credit cards on pure black (dark_ratio >= 0.94, mean <= 16):
        as few as 3 bright pixels (~0.13% of 64x36) with luma >= 125
    - Center columns contain at least as much bright text density as the far left/right margins
    """
    size = width * height
    if len(thumb) < size or size == 0:
        return False

    dark_count = 0
    bright_count = 0
    total_luma = 0
    center_bright = 0
    edge_bright = 0

    left_margin = width // 6
    right_margin = width - left_margin

    for idx in range(size):
        px = thumb[idx]
        total_luma += px
        if px <= 42:
            dark_count += 1
        if px >= 125:
            bright_count += 1
            col = idx % width
            if left_margin <= col < right_margin:
                center_bright += 1
            else:
                edge_bright += 1

    mean_luma = total_luma / size
    dark_ratio = dark_count / size
    bright_ratio = bright_count / size

    if dark_ratio < 0.88 or mean_luma > 26.0:
        return False
    min_bright_ratio = 0.0012 if (dark_ratio >= 0.94 and mean_luma <= 16.0) else 0.006
    if not (min_bright_ratio <= bright_ratio <= 0.22):
        return False
    # In credits, text is centered or in two inner columns, not solely on the outer frame edges
    if bright_count >= 6 and center_bright < edge_bright:
        return False
    return True


def _is_pure_black_fade_thumbnail(thumb: bytes, width: int = 64, height: int = 36) -> bool:
    """Return True if a thumbnail is a pure black fade-between-cards frame (no scene content)."""
    size = width * height
    if len(thumb) < size or size == 0:
        return False
    dark_count = sum(1 for i in range(size) if thumb[i] <= 42)
    mean_luma = sum(thumb[:size]) / size
    return (dark_count / size) >= 0.97 and mean_luma <= 5.0


def detect_visual_credit_spans(
    thumbs: bytes,
    interval: float,
    duration: float,
    width: int = 64,
    height: int = 36,
    min_run_seconds: float = 12.0,
) -> List[Tuple[float, float]]:
    """
    Scan grayscale thumbnails in the tail of the video and return time intervals `[(start, end), ...]`
    where a visual credit roll is active.

    If a mid-credits or post-credits live-action scene interrupts the credits for >= 6 seconds,
    the span splits into two separate credit spans so the intermediate scene is preserved.
    """
    if not thumbs or duration < MIN_VIDEO_DURATION_FOR_CREDITS or interval <= 0:
        return []

    size = width * height
    n_frames = len(thumbs) // size
    if n_frames < 6:
        return []

    tail_start = tail_window_start(duration)
    start_idx = max(0, int(tail_start / interval))

    flags: List[bool] = []
    black_fades: List[bool] = []
    for i in range(start_idx, n_frames):
        chunk = thumbs[i * size:(i + 1) * size]
        flags.append(is_credit_roll_thumbnail(chunk, width=width, height=height))
        black_fades.append(_is_pure_black_fade_thumbnail(chunk, width=width, height=height))

    if not any(flags):
        return []

    # Bridge single-frame (2s) dips, or up to 2 pure-black fade frames (4s) between credit cards,
    # while never bridging a live-action/animated scene frame.
    smoothed = list(flags)
    for i in range(1, len(smoothed) - 1):
        if not smoothed[i] and smoothed[i - 1] and smoothed[i + 1]:
            smoothed[i] = True
    for i in range(1, len(smoothed) - 2):
        if (
            not smoothed[i]
            and not smoothed[i + 1]
            and black_fades[i]
            and black_fades[i + 1]
            and smoothed[i - 1]
            and smoothed[i + 2]
        ):
            smoothed[i] = True
            smoothed[i + 1] = True
    # Also include a leading 1-frame black fade right before the credit cards start
    for i in range(len(smoothed) - 1):
        if not smoothed[i] and black_fades[i] and smoothed[i + 1]:
            smoothed[i] = True

    spans: List[Tuple[float, float]] = []
    run_start: Optional[int] = None
    for idx, is_cred in enumerate(smoothed):
        if is_cred and run_start is None:
            run_start = idx
        elif not is_cred and run_start is not None:
            t0 = round((start_idx + run_start) * interval, 3)
            t1 = round((start_idx + idx) * interval, 3)
            if t1 - t0 >= min_run_seconds:
                spans.append((t0, t1))
            run_start = None

    if run_start is not None:
        t0 = round((start_idx + run_start) * interval, 3)
        t1 = round(min(duration, (start_idx + len(smoothed)) * interval), 3)
        if t1 - t0 >= min_run_seconds:
            spans.append((t0, t1))

    return spans


# ---------------------------------------------------------------------------
# 3. Textual & Dialogue vs. Credits / Song Lyrics Classification
# ---------------------------------------------------------------------------

# Narrative post-credits / stinger cards that MUST be kept even at the very end of the movie
_NARRATIVE_STINGER_CARD_RE = re.compile(
    r"""(
        \bwill\s+return\b
      | \bto\s+be\s+continued\b
      | \bthe\s+end\s*\?\b
      | \bmeanwhile\b
      | \b(hours?|days?|weeks?|months?|years?|moments?)\s+(later|earlier|ago)\b
      | \blater\s+that\s+(night|day|evening|morning)\b
      | \bthe\s+next\s+(day|morning|night)\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Unmistakable credit roll / crew / cast / subtitle-vendor / copyright patterns
_EXPLICIT_CREDIT_TEXT_RE = re.compile(
    r"""(
        \bdirected\s+by\b
      | \bproduced\s+by\b
      | \bexecutive\s+producers?\b
      | \bco[\s\-]?producers?\b
      | \bassociate\s+producers?\b
      | \bline\s+producers?\b
      | \bwritten\s+by\b
      | \bscreenplay\s+by\b
      | \bteleplay\s+by\b
      | \bstory\s+by\b
      | \bbased\s+on\s+(the\s+)?(novel|book|characters|comic|story|true\s+story)\b
      | \bdirector\s+of\s+photography\b
      | \bcinematograph(y|er)\b
      | \bproduction\s+design(er)?\b
      | \bedited\s+by\b
      | \bfilm\s+editor\b
      | \b(original\s+)?music\s+(composed\s+)?by\b
      | \bscore\s+by\b
      | \bcasting\s+by\b
      | \bcostume\s+design(er)?\b
      | \bvisual\s+effects\b
      | \bspecial\s+effects\b
      | \bsound\s+(designer|re[\s\-]?recording|mixer|supervisor|editor)\b
      | \bstunt\s+coordinator\b
      | \bunit\s+production\s+manager\b
      | \b(first|second)\s+assistant\s+director\b
      | \bart\s+director\b
      | \bset\s+decorator\b
      | \bmake[\s\-]?up\s+department\b
      | \bin\s+association\s+with\b
      | \ba\s+[\w\s]{2,25}\s+production\b
      | \bdistributed\s+by\b
      | \b(subtitles?|subtitled|captions?|captioned|synced|resync|transcribed|encoded|ripped)\s+(and\s+corrected\s+)?by\b
      | \bopensubtitles\b
      | \baddic7ed\b
      | \bsubscene\b
      | \bsdi\s+media\b
      | \biyuno\b
      | \bwgbh\b
      | \bvitac\b
      | \bmedia\s+access\s+group\b
      | \ball\s+rights\s+reserved\b
      | \bcopyright\s*©?\s*(19|20)\d{2}\b
      | \bno\s+similarity\s+to\s+actual\s+persons\b
      | \bthis\s+motion\s+picture\s+is\s+protected\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Whisper hallucinations commonly triggered by end-credits music or silence
_TAIL_HALLUCINATION_RE = re.compile(
    r"""^\s*[\[\(♪♫*~\s]*(
        thank\s+you(\s+so\s+much)?\s+for\s+watching\b.*
      | thanks\s+for\s+watching\b.*
      | please\s+(like\s+and\s+)?subscribe\b.*
      | don'?t\s+forget\s+to\s+subscribe\b.*
      | subtitles?\s+by\s+the\s+amara\.org\s+community.*
      | www\.[a-z0-9\-]+\.[a-z]{2,}.*
    )[\]\)♪♫*~\s]*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Vocalizations / filler syllables typical in song lyrics
_LYRIC_VOCAL_RE = re.compile(
    r"\b(ooh+|aah+|ah+|oh+|yeah+|la+|na+|da+|mmm+|whoa+|woah+|hey\s+yeah|baby\s+baby)\b",
    re.IGNORECASE,
)

# Explicit song-lyric / music-cue vocabulary common in end-credits songs (pop, hip-hop, R&B)
_SONG_LYRIC_PHRASE_RE = re.compile(
    r"""(
        \b(forever\s+and\s+ever|forever\s+young|together\s+forever|live\s+life\s+with\s+you)\b
      | \b(music'?s\s+loud|come\s+dance\s+along|dance\s+with\s+me|sing\s+along)\b
      | \b(forever\s*,\s*forever)\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Strong conversational dialogue indicators (questions, discourse markers, direct address, contractions)
_DIALOGUE_DISCOURSE_RE = re.compile(
    r"""(
        ^\s*[-–—]\s*\S
      | \?
      | \b(what|why|who|where|when|how)\s+(are|is|was|were|did|do|does|have|has|had|can|could|will|would|should)\b
      | \b(what'?s|who'?s|where'?s|how'?s|why'?s|that'?s|there'?s|here'?s|let'?s|it'?s|i'?m|you'?re|we'?re|they'?re)\b
      | \b(didn'?t|doesn'?t|don'?t|aren'?t|isn'?t|wasn'?t|weren'?t|wouldn'?t|couldn'?t|shouldn'?t|won'?t|can'?t|haven'?t|hasn'?t|hadn'?t)\b
      | \b(excuse\s+me|i'?m\s+sorry|of\s+course|all\s+right|alright|hold\s+on|wait\s+a\s+(second|minute)|listen\s+to\s+me|look\s+at\s+(this|that|me|him|her))\b
      | \b(mr\.|mrs\.|ms\.|dr\.|sir|ma'?am|captain|agent|detective|professor|general|commander|lieutenant|sergeant|officer)\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _strip_brackets(text: str) -> str:
    return (text or "").strip().strip("[]()").strip()


def is_stinger_narrative_card(text: str) -> bool:
    """Return True if `text` is a post-credits narrative card like '[THOR WILL RETURN]' or '[MEANWHILE...]'."""
    clean = _strip_brackets(text)
    if not clean:
        return False
    return bool(_NARRATIVE_STINGER_CARD_RE.search(clean))


def is_explicit_credit_text(text: str) -> bool:
    """
    Return True if `text` is an unmistakable cast/crew credit, subtitle vendor tag,
    copyright notice, or end-of-video Whisper hallucination.
    """
    if not text or not text.strip():
        return False
    if is_stinger_narrative_card(text):
        return False
    clean = _strip_brackets(text)
    if _EXPLICIT_CREDIT_TEXT_RE.search(clean) or _TAIL_HALLUCINATION_RE.match(clean):
        return True
    return False


def is_music_or_lyric_cue(text: str) -> bool:
    """Return True if a cue explicitly contains music notes or pure vocalization loops."""
    if not text:
        return False
    if "♪" in text or "♫" in text:
        return True
    clean = _strip_brackets(text)
    if _TAIL_HALLUCINATION_RE.match(clean):
        return True
    # Pure repetitive vocalizations like "Yeah, yeah, yeah" or "Oh, oh, oh"
    words = [re.sub(r"[\W_]+", "", w).lower() for w in clean.split()]
    words = [w for w in words if w]
    if len(words) >= 3 and len(set(words)) == 1:
        return True
    if len(words) >= 4 and len(set(words)) <= 2 and all(_LYRIC_VOCAL_RE.fullmatch(w) for w in words):
        return True
    return False


def _norm_line(text: str) -> str:
    return re.sub(r"[\W_]+", " ", (text or "").casefold()).strip()


def _in_any_span(t: float, spans: Sequence[Tuple[float, float]], pad: float = 0.0) -> bool:
    return any(s - pad <= t <= e + pad for s, e in spans)


def _cluster_segments(
    segments: Sequence[Dict[str, Any]],
    gap_threshold: float = 3.5,
) -> List[List[Dict[str, Any]]]:
    """Group time-ordered segments into contiguous clusters separated by pauses >= `gap_threshold`."""
    if not segments:
        return []
    clusters: List[List[Dict[str, Any]]] = [[segments[0]]]
    for prev, cur in zip(segments, segments[1:]):
        gap = float(cur["start_time"]) - float(prev["end_time"])
        if gap >= gap_threshold:
            clusters.append([cur])
        else:
            clusters[-1].append(cur)
    return clusters


def _is_conversational_cluster(
    cluster: Sequence[Dict[str, Any]],
    credit_spans: Sequence[Tuple[float, float]] = (),
    scene_spans: Sequence[Tuple[float, float]] = (),
) -> bool:
    """
    Decide whether a cluster of segments after the credits onset represents a genuine
    mid-credits / post-credits dialogue scene (`True`) vs. an end-credits song or credit list (`False`).
    """
    if not cluster:
        return False

    spoken = [
        s for s in cluster
        if s.get("segment_type", "DIALOGUE") != "ON_SCREEN"
        and not is_explicit_credit_text(s.get("original_text", ""))
        and not is_music_or_lyric_cue(s.get("original_text", ""))
    ]
    if not spoken:
        return False

    mid_t = (float(spoken[0]["start_time"]) + float(spoken[-1]["end_time"])) / 2.0
    if _in_any_span(mid_t, scene_spans, pad=2.0):
        return True

    # Count repeated normalized lines (choruses/refrains in songs repeat lines)
    norm_lines = [_norm_line(s.get("original_text", "")) for s in spoken]
    norm_lines = [nl for nl in norm_lines if nl]
    if not norm_lines:
        return False

    unique_ratio = len(set(norm_lines)) / len(norm_lines)
    if len(norm_lines) >= 4 and unique_ratio <= 0.65:
        return False

    # Check sentence punctuation & conversational discourse features
    punct_end_count = 0
    question_or_turn_count = 0
    discourse_count = 0
    vocal_count = 0
    lyric_phrase_count = 0

    for s in spoken:
        txt = (s.get("original_text") or "").strip()
        if not txt:
            continue
        if txt.endswith((".", "?", "!", '."', '?"', '!"', "…", "...")):
            punct_end_count += 1
        if "?" in txt or txt.startswith(("-", "–", "—")) or "\n-" in txt or "\n–" in txt or "\n—" in txt:
            question_or_turn_count += 1
        if _DIALOGUE_DISCOURSE_RE.search(txt):
            discourse_count += 1
        if _LYRIC_VOCAL_RE.search(txt):
            vocal_count += 1
        if _SONG_LYRIC_PHRASE_RE.search(txt):
            lyric_phrase_count += 1

    n = len(spoken)
    punct_ratio = punct_end_count / n
    discourse_ratio = discourse_count / n
    in_credits_visual_or_chapter = _in_any_span(mid_t, credit_spans, pad=1.0)

    # Strong lyrics signature: multiple lines without sentence-ending periods or with heavy vocalizations
    if n >= 3 and punct_ratio < 0.35 and question_or_turn_count == 0:
        return False
    if n >= 3 and vocal_count >= max(2, n // 2) and question_or_turn_count == 0:
        return False
    if lyric_phrase_count >= 2:
        return False

    # Inside a known credit span (chapter or dark visual roll), require clear conversational evidence
    # so punctuated song lyrics don't masquerade as a scene, while real mid/post-credits dialogue passes easily.
    if in_credits_visual_or_chapter:
        if n <= 1:
            return False
        if question_or_turn_count >= 1 and punct_ratio >= 0.5:
            return True
        if discourse_count >= 2 and punct_ratio >= 0.6 and vocal_count == 0 and lyric_phrase_count == 0 and unique_ratio >= 0.85:
            return True
        return False

    # Outside visual/chapter credit rolls (e.g. visual roll stopped because a post-credits scene began!),
    # any normal punctuated speech or conversational line is a real scene.
    if question_or_turn_count >= 1 or discourse_ratio >= 0.35 or punct_ratio >= 0.5:
        return True

    return False


def filter_frames_for_credits(
    frames: Sequence[Any],
    duration: float,
    chapters: Sequence[Dict[str, Any]] = (),
    thumbs_bytes: bytes = b"",
    frame_interval: float = 2.0,
) -> Tuple[List[Any], int]:
    """
    Filter sampled `Frame` objects before sending them to Gemini Vision so scrolling end-credits
    frames do not consume the frame budget or waste Vision API tokens.

    Preserves:
    - All frames before the tail window
    - All frames inside non-credits visual scenes (mid-credits / post-credits scenes)
    - Static cards held for >= 3.0s at the end of a credits roll (e.g. "[THOR WILL RETURN]")
    Returns `(kept_frames, dropped_count)`.
    """
    if not frames or duration < MIN_VIDEO_DURATION_FOR_CREDITS:
        return list(frames), 0

    chapter_credits, chapter_scenes = parse_chapter_spans(chapters, duration)
    visual_credits = detect_visual_credit_spans(thumbs_bytes, frame_interval, duration)
    all_credit_spans = chapter_credits + visual_credits
    if not all_credit_spans:
        return list(frames), 0

    kept: List[Any] = []
    dropped = 0
    for f in frames:
        t = float(getattr(f, "time", 0.0))
        static_until = float(getattr(f, "static_until", t))
        # Always keep frames in explicit post-credits scene chapters
        if _in_any_span(t, chapter_scenes, pad=1.0):
            kept.append(f)
            continue
        # If visual analysis is available and this frame is NOT in a visual credit span,
        # it may be a live-action mid/post-credits scene embedded inside a single "End Credits" chapter!
        if visual_credits and not _in_any_span(t, visual_credits, pad=1.0):
            kept.append(f)
            continue
        if _in_any_span(t, all_credit_spans, pad=0.5):
            # Keep static cards held for >= 3.5s near the end of a span (e.g. "THOR WILL RETURN")
            if static_until - t >= 3.5:
                kept.append(f)
                continue
            dropped += 1
            continue
        kept.append(f)

    if dropped > 0:
        logger.info(
            f"End-credits frame filter: skipped {dropped} scrolling-credits frame(s) in tail "
            f"(preserved {len(kept)} frame(s) including any mid/post-credits scenes)"
        )
    return kept, dropped


def filter_credits_segments(
    segments: Sequence[Dict[str, Any]],
    duration: float,
    chapters: Sequence[Dict[str, Any]] = (),
    visual_credit_spans: Sequence[Tuple[float, float]] = (),
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Remove end-credits subtitles (cast/crew cards, song lyrics, Whisper music hallucinations,
    and subtitle vendor tags) in the tail of the video while preserving mid-credits and
    post-credits scenes and narrative stinger cards ("[THOR WILL RETURN]").

    Returns `(kept_segments_reindexed, removed_count)`.
    """
    if not segments or duration < MIN_VIDEO_DURATION_FOR_CREDITS:
        return [dict(s) for s in segments], 0

    tail_start = tail_window_start(duration)
    chapter_credits, chapter_scenes = parse_chapter_spans(chapters, duration)
    if chapter_credits:
        tail_start = min(tail_start, min(s for s, _ in chapter_credits))

    credit_spans = list(chapter_credits) + list(visual_credit_spans)

    head_segments: List[Dict[str, Any]] = []
    tail_segments: List[Dict[str, Any]] = []
    for s in segments:
        if float(s["start_time"]) < tail_start:
            head_segments.append(dict(s))
        else:
            tail_segments.append(dict(s))

    if not tail_segments:
        return [dict(s) for s in segments], 0

    # Determine where the end credits begin inside `tail_segments`.
    # Only consider the final 15% of the video for explicit-credit text onset so a mid-movie sign
    # or music hallucination cannot pull `credits_onset` 20 minutes early.
    late_threshold = duration * 0.85
    credits_onset: Optional[float] = None
    if credit_spans:
        credits_onset = min(s for s, _ in credit_spans)

    for s in tail_segments:
        txt = s.get("original_text", "")
        t = float(s["start_time"])
        if t < late_threshold and not _in_any_span(t, credit_spans, pad=5.0):
            continue
        if is_explicit_credit_text(txt):
            credits_onset = t if credits_onset is None else min(credits_onset, t)
            break

    # Even without explicit credit text or chapters, check if the tail ends with a lyric/music cluster
    clusters = _cluster_segments(tail_segments, gap_threshold=3.5)
    if credits_onset is None:
        # Look from the end backwards for contiguous non-conversational lyric/music clusters in the last 15%
        for cl in reversed(clusters):
            cl_start = float(cl[0]["start_time"])
            if cl_start < late_threshold:
                break
            if not _is_conversational_cluster(cl, credit_spans=credit_spans, scene_spans=chapter_scenes):
                credits_onset = cl_start
            else:
                break

    # Also check if an end-credits song began slightly before the visual credit roll (e.g. song starts over the
    # final 10-20s montage and continues seamlessly into the black credit screen):
    if credits_onset is not None:
        for cl in reversed(clusters):
            cl_start = float(cl[0]["start_time"])
            cl_end = float(cl[-1]["end_time"])
            if cl_start >= credits_onset or cl_start < late_threshold:
                continue
            if credits_onset - cl_end <= 10.0 and not _is_conversational_cluster(
                cl, credit_spans=credit_spans, scene_spans=chapter_scenes
            ):
                credits_onset = cl_start

    if credits_onset is None:
        return [dict(s) for s in segments], 0

    # Classify each cluster from `credits_onset` onwards:
    # - Keep clusters before `credits_onset`
    # - After `credits_onset`, keep conversational mid/post-credits scene clusters and stinger cards;
    #   drop explicit credits, song lyrics, and music hallucinations.
    kept_tail: List[Dict[str, Any]] = []
    protected_windows: List[Tuple[float, float]] = list(chapter_scenes)

    # First pass: identify conversational scene clusters after `credits_onset`
    cluster_is_scene: List[bool] = []
    for cl in clusters:
        cl_end = float(cl[-1]["end_time"])
        if cl_end <= credits_onset:
            cluster_is_scene.append(True)
            continue
        is_scene = _is_conversational_cluster(
            cl,
            credit_spans=credit_spans,
            scene_spans=chapter_scenes,
        )
        cluster_is_scene.append(is_scene)
        if is_scene:
            spoken_in_cl = [
                s for s in cl
                if s.get("segment_type", "DIALOGUE") != "ON_SCREEN"
                and not is_explicit_credit_text(s.get("original_text", ""))
                and not is_music_or_lyric_cue(s.get("original_text", ""))
            ]
            if spoken_in_cl:
                protected_windows.append((
                    float(spoken_in_cl[0]["start_time"]) - 6.0,
                    float(spoken_in_cl[-1]["end_time"]) + 4.0,
                ))

    # Second pass: filter segments within each tail cluster
    removed_count = 0
    for cl, is_scene in zip(clusters, cluster_is_scene):
        for s in cl:
            t_start = float(s["start_time"])
            t_mid = (t_start + float(s["end_time"])) / 2.0
            txt = s.get("original_text", "")
            seg_type = s.get("segment_type", "DIALOGUE")

            # 1. Always preserve explicit stinger narrative cards ("[THOR WILL RETURN]")
            if is_stinger_narrative_card(txt):
                kept_tail.append(dict(s))
                continue

            # 2. Always drop unmistakable credit lines, vendor tags, and tail hallucinations
            if is_explicit_credit_text(txt) or is_music_or_lyric_cue(txt):
                removed_count += 1
                continue

            # 3. Before credits_onset, keep everything else
            if t_start < credits_onset - 0.5:
                kept_tail.append(dict(s))
                continue

            # 4. ON_SCREEN captions after credits_onset:
            # Keep if they introduce or overlap a protected mid/post-credits scene (e.g. "[S.H.I.E.L.D. HQ]"),
            # otherwise drop if they sit inside the credits roll (e.g. actor/crew names).
            if seg_type == "ON_SCREEN":
                if _in_any_span(t_mid, protected_windows, pad=0.0):
                    kept_tail.append(dict(s))
                else:
                    removed_count += 1
                continue

            # 5. Dialogue/Comms/Foreign segments after credits_onset:
            if is_scene:
                kept_tail.append(dict(s))
            else:
                removed_count += 1

    if removed_count == 0:
        return [dict(s) for s in segments], 0

    result = head_segments + kept_tail
    for idx, seg in enumerate(result, start=1):
        seg["index"] = idx

    logger.info(
        f"End-credits filter: removed {removed_count} credit/lyric segment(s) after {credits_onset:.1f}s "
        f"(kept {len(kept_tail)} tail segment(s) across {len(protected_windows)} mid/post-credits window(s))"
    )
    return result, removed_count
