"""
"Sync existing subtitles": re-time a subtitle file the user already has to this video.

Runs after probe -> audio -> Whisper (-> on-screen text scan, optional):

1. speech map: Silero VAD over the audio, plus Whisper's word times;
2. reference timing from the video itself, when it has it: an embedded text subtitle track,
   else subtitles burned into the picture (detected locally while Whisper ran);
3. first alignment pass (sync_align): offset, frame rate, cuts;
4. text anchors near the predicted times: the line's own words in the transcript (same language)
   or Gemini's line-to-phrase matches (other language); lines that repeat an on-screen title;
5. second alignment pass with the anchors, then each line snaps to its best evidence and the
   timeline is settled (order, no overlaps, readable durations).

Text is never changed. A line in the spoken language becomes the "original" text; a line in
another language becomes the "translation", with the matching speech as the original.
"""
import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import settings
from app.models.db_models import SegmentType
from app.pipeline.context import PipelineCancelled, PipelineContext
from app.pipeline.retime import retime_cues
from app.pipeline.stages.base import PipelineStage, ProgressCallback
from app.pipeline.sync_align import AlignCue, align, describe, merge_intervals
from app.pipeline.sync_burned import validate_against_speech
from app.pipeline.sync_text import (
    Anchor, match_same_language, match_screens_same_language, match_with_gemini, transcript_words,
)
from app.utils.languages import language_code, language_name
from app.utils.srt_formatter import calculate_cps
from app.utils.subtitle_parser import ParsedCue, detect_language

logger = logging.getLogger(__name__)

LEAD = 0.1              # a line appears this long before the first spoken word
HOLD = 0.3              # ... and stays this long after the last one
MIN_IOU = 0.5           # overlap needed to take a reference (burned-in / embedded) interval
MIN_DURATION = 0.5
CUE_GAP = 0.04
MAX_TRACKS = 3          # embedded subtitle tracks tried as a timing reference
ONSET_SNAP = 0.5        # a speech-only line starts at a speech onset this close to it

Interval = Tuple[float, float]


def _iou(a: Interval, b: Interval) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def _best_reference(span: Interval, reference: Sequence[Interval], starts: Sequence[float]) -> Optional[Interval]:
    import bisect

    k = bisect.bisect_left(starts, span[0] - 15.0)
    best, best_iou = None, MIN_IOU
    for ref in reference[k:]:
        if ref[0] > span[1] + 15.0:
            break
        value = _iou(span, ref)
        if value >= best_iou:
            best, best_iou = ref, value
    return best


def _words_text(words, start: float, end: float, limit: int = 240) -> str:
    out: List[str] = []
    prev = None
    for w in words:
        if w.end <= start:
            continue
        if w.start >= end:
            break
        key = (w.text, w.start)
        if key != prev and (w.start + w.end) / 2 >= start - 0.05:
            out.append(w.text)
        prev = key
    return " ".join(out)[:limit].strip()


class SyncStage(PipelineStage):
    def __init__(self, client_factory=None):
        self._client_factory = client_factory  # tests: () -> google.genai.Client-like

    @property
    def name(self) -> str:
        return "SYNCING"

    def _client(self, ctx: PipelineContext):
        if self._client_factory:
            return self._client_factory()
        key = (ctx.gemini_api_key_override or "").strip() or settings.gemini_api_key
        if not key or key == "your_gemini_api_key_here":
            return None
        from google import genai

        return genai.Client(api_key=key)

    async def execute(self, ctx: PipelineContext, on_progress: Optional[ProgressCallback] = None) -> None:
        async def progress(pct: float, msg: str) -> None:
            if on_progress:
                await on_progress(pct, None, None, msg, None)

        cues = [ParsedCue(**c) for c in (ctx.sync_cues or [])]
        if not cues:
            raise ValueError("The subtitle file has no lines to sync")
        file_lang = ctx.sync_language or detect_language(cues)
        spoken = ctx.effective_source_language
        same_language = language_code(file_lang) is not None and language_code(file_lang) == language_code(spoken)
        texts = [c.text for c in cues]
        report: Dict[str, Any] = {
            "file_name": ctx.sync_file_name,
            "language": file_lang,
            "spoken_language": language_name(spoken, "unknown"),
            "lines": len(cues),
        }

        # 1. Speech map
        await progress(2.0, "Mapping the speech in the audio...")
        words = transcript_words(ctx.raw_segments)
        regions: List[Interval] = []
        if ctx.audio_wav_path:
            from app.pipeline.speech_regions import detect_regions

            try:
                regions = await asyncio.to_thread(detect_regions, ctx.audio_wav_path)
            except Exception as e:
                logger.warning(f"Voice activity detection failed, using Whisper's timing only: {e}")
        speech = merge_intervals(list(regions) + [(w.start, w.end) for w in words], gap=0.15)
        ctx.raise_if_cancelled()

        # 2. Reference timing from the video (embedded track, else burned-in subtitles)
        await progress(10.0, "Looking for subtitles in the video itself...")
        reference, ref_info = await self._reference(ctx, len(cues), speech)
        report["reference"] = ref_info
        ref_starts = [r[0] for r in reference]

        # 3. First pass
        await progress(20.0, "Matching the subtitle rhythm to the speech...")
        align_cues = [
            AlignCue(c.start, c.end, weight=0.0 if (c.kind == "sound" or c.position == "top") else 1.0) for c in cues
        ]
        first = await asyncio.to_thread(align, align_cues, speech, ctx.duration, reference)
        predicted = list(zip(first.starts, first.ends))
        logger.info(f"Sync pass 1: {describe(first)}")
        ctx.raise_if_cancelled()

        # 4. Text anchors
        anchors: Dict[int, Anchor] = {}
        screens = list(ctx.onscreen_segments or [])
        if same_language:
            await progress(35.0, "Finding each line's words in the transcript...")
            anchors = match_same_language(texts, predicted, words, skip=[c.kind == "sound" for c in cues])
            for i, a in match_screens_same_language(texts, predicted, screens).items():
                if i not in anchors or cues[i].position == "top":
                    anchors[i] = a
            report["matching"] = "text"
        elif ctx.sync_use_gemini:
            anchors = await self._gemini_anchors(ctx, texts, predicted, words, screens, file_lang, spoken, report, progress)
        else:
            report["matching"] = "off"
        ctx.raise_if_cancelled()

        # 5. Second pass with the anchors
        await progress(85.0, "Placing every line...")
        for i, a in anchors.items():
            align_cues[i].anchor = a.start
            if a.source == "screen":
                align_cues[i].weight = 0.0
        final = await asyncio.to_thread(align, align_cues, speech, ctx.duration, reference) if anchors else first
        summary = describe(final)
        report.update({k: summary[k] for k in ("frame_rate", "offset", "jump_count", "jumps")})
        logger.info(f"Sync pass 2: {summary}")

        segments, methods = self._place(cues, final, anchors, reference, ref_starts, ref_info.get("source"),
                                        speech, words, same_language)
        report["methods"] = methods
        ctx.refined_segments = segments
        ctx.sync_report = report
        if same_language is False:
            ctx.target_language = file_lang
        logger.info(f"Sync result: {methods}")
        await progress(100.0, sync_summary(report))

    # ------------------------------------------------------------------ evidence

    async def _reference(self, ctx: PipelineContext, lines: int, speech: List[Interval]) -> Tuple[List[Interval], Dict]:
        tracks = [s for s in (ctx.subtitle_streams or []) if s.text_based and not s.looks_forced][:MAX_TRACKS]
        best: Tuple[List[Interval], Dict] = ([], {"source": None})
        if tracks:
            from app.pipeline.stages.ocr_stage import extract_forced_track

            for track in tracks:
                try:
                    cues = await extract_forced_track(ctx.video_path, track)
                except Exception as e:
                    logger.warning(f"Could not read subtitle track #{track.index}: {e}")
                    continue
                intervals = [(c["start_time"], c["end_time"]) for c in cues]
                ok, detail = validate_against_speech(intervals, speech, ctx.duration, lines)
                logger.info(f"Embedded subtitle track #{track.index} ({track.language}): {detail}")
                closer = not best[0] or abs(len(intervals) - lines) < abs(len(best[0]) - lines)
                if ok and closer:
                    best = (intervals, {"source": "embedded", "detail": f"track #{track.index} ({track.language or 'und'}), {detail}"})
        task = getattr(ctx, "burned_task", None)
        if best[0]:
            if task is not None and not task.done():
                task.cancel()
            return best
        if task is None:
            return best
        try:
            result = await task
        except asyncio.CancelledError:
            if ctx.is_cancelled:
                raise PipelineCancelled()
            return best
        except PipelineCancelled:
            raise
        except Exception as e:
            logger.warning(f"Burned-in subtitle detection failed: {e}")
            return best
        if result.error:
            logger.warning(f"Burned-in subtitle detection: {result.error}")
        ok, detail = validate_against_speech(result.intervals, speech, ctx.duration, lines)
        logger.info(f"Burned-in subtitles: {detail} ({result.frames} frames analysed)")
        if ok:
            return result.intervals, {"source": "burned_in", "detail": detail}
        return [], {"source": None, "detail": f"no burned-in subtitles ({detail})"}

    async def _gemini_anchors(self, ctx, texts, predicted, words, screens, file_lang, spoken, report, progress):
        client = self._client(ctx)
        if client is None:
            ctx.warnings.append("Matching the lines to the speech with Gemini needs an API key; "
                                "the subtitles were synced by the speech rhythm only.")
            report["matching"] = "no_key"
            return {}
        from app.core.user_settings import load_settings

        call = load_settings().translation
        await progress(35.0, f"Matching each line to the speech with Gemini ({call.model})...")
        done = 0

        async def on_batch(total: int) -> None:
            nonlocal done
            done += 1
            await progress(35.0 + 48.0 * done / max(1, total), f"Matched {done}/{total} batches of lines")

        try:
            anchors, stats = await match_with_gemini(
                client, call, texts, predicted, words, screens,
                file_lang=file_lang, spoken_lang=language_name(spoken, "the original language"),
                usage=ctx.usage, flex_state=ctx.flex_state, cancel=ctx.cancel_flag, on_batch=on_batch,
            )
        except (PipelineCancelled, asyncio.CancelledError):
            raise
        except Exception as e:
            logger.error(f"Gemini line matching failed: {e}")
            ctx.warnings.append(f"Gemini line matching failed ({str(e)[:200]}); synced by the speech rhythm only.")
            report["matching"] = "failed"
            return {}
        report["matching"] = "gemini"
        report["gemini"] = stats
        if stats.get("failed"):
            ctx.warnings.append(f"Gemini line matching: {stats['failed']} of {stats['batches']} batch(es) failed; "
                                "those lines were synced by the speech rhythm.")
        logger.info(f"Gemini matching: {stats}, {len(anchors)} anchors kept")
        return anchors

    # ------------------------------------------------------------------ placement

    def _place(self, cues: Sequence[ParsedCue], final, anchors: Dict[int, Anchor], reference: Sequence[Interval],
               ref_starts: Sequence[float], ref_source: Optional[str], speech: Sequence[Interval], words,
               same_language: bool):
        methods = {"text": 0, "gemini": 0, "screen": 0, "reference": 0, "speech": 0, "low_confidence": 0}
        placed = []
        for i, cue in enumerate(cues):
            start, end = final.starts[i], final.ends[i]
            method, confidence, kind = "speech", 0.0, None
            anchor = anchors.get(i)
            if anchor is not None and final.anchor_used[i]:
                if anchor.source == "screen":
                    start, end, method, kind = anchor.start, anchor.end, "screen", SegmentType.ON_SCREEN.value
                else:
                    start = max(0.0, anchor.start - LEAD)
                    end = max(anchor.end + HOLD, start + 0.7 * (final.ends[i] - final.starts[i]))
                    method = anchor.source
                confidence = 1.0
            if reference and method != "screen":
                ref = _best_reference((start, end), reference, ref_starts)
                if ref is not None:
                    start, end, method, confidence = ref[0], ref[1], "reference", 1.0
            if method == "speech":
                ratio = final.speech_ratio[i]
                confidence = 0.8 if cue.kind == "sound" or cue.position == "top" else (
                    0.9 if ratio >= 0.6 else 0.6 if ratio >= 0.3 else 0.3)
            if kind is None:
                kind = SegmentType.ON_SCREEN.value if cue.position == "top" else SegmentType.DIALOGUE.value
            methods[method] += 1
            placed.append(SimpleNamespace(
                start_time=round(start, 3), end_time=round(end, 3), segment_type=kind, method=method,
                confidence=confidence, cue=cue, anchor=anchor if method in ("text", "gemini", "screen") else None,
                orig=(cue.start, cue.end),
            ))

        # Speech-only lines: start where the nearest speech starts...
        self._snap_onsets([p for p in placed if p.method == "speech" and p.segment_type != SegmentType.ON_SCREEN.value
                           and p.cue.kind != "sound"], speech)
        # ...and starts that still wait in a silence move to the speech that follows
        order = sorted(range(len(placed)), key=lambda k: placed[k].start_time)
        timeline = [placed[k] for k in order]
        scope = [pos for pos, p in enumerate(timeline) if p.method == "speech"]
        if scope:
            retime_cues(timeline, speech, scope=scope, allow_silent_shift=True)
        self._settle(placed)

        segments: List[Dict[str, Any]] = []
        for index, p in enumerate(sorted(placed, key=lambda q: (q.start_time, q.end_time)), start=1):
            if p.method == "speech" and p.confidence < 0.5:
                methods["low_confidence"] += 1
            text = p.cue.text
            if same_language:
                original, translated = text, None
            else:
                heard = p.anchor.transcript if p.anchor is not None else _words_text(words, p.start_time, p.end_time)
                original, translated = heard, text
            segments.append({
                "index": index,
                "start_time": p.start_time,
                "end_time": p.end_time,
                "original_text": original,
                "translated_text": translated,
                "translation_failed": False,
                "segment_type": p.segment_type,
                "confidence": round(p.confidence, 2),
                "cps": calculate_cps(text, max(0.1, p.end_time - p.start_time)),
            })
        if ref_source:
            methods[ref_source] = methods.pop("reference")
        else:
            methods.pop("reference")
        return segments, methods

    @staticmethod
    def _snap_onsets(lines: List[Any], speech: Sequence[Interval]) -> None:
        import bisect

        from app.pipeline.sync_align import ONSET_GAP

        onsets = [s for k, (s, _) in enumerate(speech) if k == 0 or s - speech[k - 1][1] >= ONSET_GAP]
        for p in lines:
            k = bisect.bisect_left(onsets, p.start_time)
            near = [o for o in onsets[max(0, k - 1):k + 1] if abs(o - p.start_time) <= ONSET_SNAP]
            if not near:
                continue
            onset = min(near, key=lambda o: abs(o - p.start_time))
            new_start = round(max(0.0, onset - LEAD), 3)
            if p.end_time - new_start >= MIN_DURATION:
                p.start_time = new_start

    @staticmethod
    def _settle(placed: List[Any]) -> None:
        """File order is the story order: keep it, without overlaps the file itself did not have."""
        for k, p in enumerate(placed):
            if p.end_time - p.start_time < MIN_DURATION:
                p.end_time = round(p.start_time + MIN_DURATION, 3)
            if k == 0:
                continue
            prev = placed[k - 1]
            if p.start_time < prev.start_time:
                p.start_time = round(prev.start_time + CUE_GAP, 3)
                p.end_time = round(max(p.end_time, p.start_time + MIN_DURATION), 3)
            file_overlap = p.orig[0] < prev.orig[1] - 0.01
            if not file_overlap and prev.end_time > p.start_time - CUE_GAP:
                wanted = p.start_time - CUE_GAP
                if wanted - prev.start_time >= MIN_DURATION:
                    prev.end_time = round(wanted, 3)
                else:
                    prev.end_time = round(prev.start_time + MIN_DURATION, 3)
                    p.start_time = round(max(p.start_time, prev.end_time + CUE_GAP), 3)
                    p.end_time = round(max(p.end_time, p.start_time + MIN_DURATION), 3)

    @staticmethod
    def summary(report: Dict[str, Any]) -> str:
        return sync_summary(report)


def sync_summary(report: Dict[str, Any]) -> str:
    """One line for the job log and the completion message."""
    m = report.get("methods", {})
    parts = [f"{v} {k.replace('_', '-')}" for k, v in m.items() if v and k != "low_confidence"]
    text = f"Synced {report.get('lines', 0)} lines ({', '.join(parts)})"
    if report.get("frame_rate") and report["frame_rate"] != "same frame rate":
        text += f"; frame rate {report['frame_rate']}"
    if report.get("offset") is not None:
        text += f"; offset {report['offset']:+.1f}s"
    if report.get("jump_count"):
        text += f"; {report['jump_count']} cut(s)"
    if m.get("low_confidence"):
        text += f"; {m['low_confidence']} uncertain"
    return text
