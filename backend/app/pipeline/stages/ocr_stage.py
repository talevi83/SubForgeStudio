"""
On-screen text detection ("forced narratives"): time jumps, location/date cards, HUD headers
and plot-critical signs that Whisper can never hear.

Layer 1 — embedded forced subtitle track:
    If the container carries a text-based forced/"signs" subtitle stream, extract it with
    ffmpeg and turn its cues into ON_SCREEN segments. Cheap and exact, so when it exists the
    frame scan is skipped.

Layer 2 — frame sampling + Gemini Vision:
    ffmpeg samples low-res frames (default every 2s) while Whisper is still transcribing.
    Frames are prioritised when they fall in speech gaps or right after scene changes (where
    "One week later..." cards usually live), sent to Gemini in batches with their timestamps,
    and consecutive detections of the same text are merged into one timed, bracketed segment.

The building blocks live next to this stage: `embedded_tracks` (layer 1), `frame_sampling`,
`onscreen_vision` (prompts + Gemini call) and `onscreen_merge` (detections -> segments).
"""
import asyncio
import logging
from typing import Any, List, Optional

from app.core.config import settings
from app.models.db_models import SegmentType
from app.pipeline.context import PipelineCancelled, PipelineContext
from app.pipeline.embedded_tracks import extract_forced_track, pick_forced_track
from app.pipeline.foreign_speech import READ_MARGIN
from app.pipeline.frame_sampling import (
    _SUB_INTERVAL,
    Frame,
    frames_dir,
    sample_frames,
    sample_subtitle_crops,
    select_frames,
)
from app.pipeline.onscreen_merge import FrameDetection, _same_text, merge_frame_detections
from app.pipeline.onscreen_vision import SUBTITLE_INSTRUCTION, _gemini_detect, get_vision_system_instruction
from app.pipeline.stages.base import PipelineStage, ProgressCallback

logger = logging.getLogger(__name__)

_ONSCREEN_REPEAT_WINDOW = 180.0  # the same sign/title seen again within this time is not shown again
_GEMINI_CONCURRENCY = 3
_SUB_MAX_FRAMES = 1500        # beyond this the interval is stretched
_SUB_MARGIN = READ_MARGIN     # always read this far before/after the foreign speech: replies and
                              # short lines are often missed by the speech detector
_SUB_EDGE = 3.0               # a subtitle this close to the scanned edge may continue past it...
_SUB_EXTEND = 6.0             # ...so look this much further
_SUB_MAX_EXTENSIONS = 3


class OnScreenTextStage(PipelineStage):
    """Pipeline stage producing ON_SCREEN segments (forced track, else frame scan)."""

    def __init__(self, client_factory=None):
        # Injectable for tests: () -> google.genai.Client-like object
        self._client_factory = client_factory

    @property
    def name(self) -> str:
        return "SCANNING_ONSCREEN"

    @staticmethod
    def start_frame_sampling(ctx: PipelineContext) -> None:
        """Kick off ffmpeg frame sampling in the background (called right after audio extraction)."""
        if ctx.detect_onscreen_text and ctx.frame_task is None and pick_forced_track(ctx.subtitle_streams) is None:
            ctx.frame_task = asyncio.ensure_future(sample_frames(ctx))

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

        ctx.onscreen_segments = []
        ctx.foreign_ocr_segments = []
        if not ctx.detect_onscreen_text:
            await progress(100.0, "On-screen text detection disabled; skipping")
            return

        client = self._client(ctx)
        used_forced = await self._narrative_text(ctx, client, progress)
        await self._foreign_subtitles(ctx, client, progress, used_forced)
        await progress(
            100.0,
            f"Found {len(ctx.onscreen_segments)} on-screen captions"
            + (f" and {len(ctx.foreign_ocr_segments)} subtitles for foreign speech" if ctx.foreign_spans else ""),
        )

    # -- narrative titles / signs ------------------------------------------------

    async def _narrative_text(self, ctx: PipelineContext, client: Any, progress) -> bool:
        """Fill ctx.onscreen_segments. Returns True if an embedded forced track was used."""
        forced = pick_forced_track(ctx.subtitle_streams)
        if forced is not None:
            await progress(10.0, f"Reading embedded forced subtitle track #{forced.index}...")
            try:
                cues = await extract_forced_track(ctx.video_path, forced)
                # Forced-track cues during foreign speech are subtitles for that speech, not titles
                for cue in cues:
                    mid = (cue["start_time"] + cue["end_time"]) / 2
                    if any(span["start"] <= mid <= span["end"] for span in ctx.foreign_spans):
                        cue["segment_type"] = SegmentType.FOREIGN.value
                        ctx.foreign_ocr_segments.append(cue)
                    else:
                        ctx.onscreen_segments.append(cue)
                self._cancel_sampling(ctx)
                return True
            except Exception as e:
                logger.warning(f"Forced track extraction failed ({e}); falling back to frame scan")

        if [s for s in ctx.subtitle_streams if s.looks_forced and not s.text_based]:
            logger.info("Forced subtitle track is image-based (PGS/VobSub); using the frame scan instead")

        if client is None:
            self._cancel_sampling(ctx)
            ctx.warnings.append("On-screen text scan skipped: it needs a Gemini API key.")
            return False
        try:
            await self._scan_frames(ctx, client, progress)
        except (PipelineCancelled, asyncio.CancelledError):
            raise
        except Exception as e:
            # Never lose the transcription because of the optional on-screen scan
            logger.error(f"On-screen text scan failed: {e}")
            ctx.warnings.append(f"On-screen text scan failed: {e}")
            self._cancel_sampling(ctx)
        return False

    async def _detect_batches(
        self,
        ctx: PipelineContext,
        client: Any,
        frames: List[Frame],
        instruction: str,
        stage: str,
        on_batch=None,
        sensitivity: int = 2,
    ):
        """Send frames to Gemini Vision in concurrent batches. Returns (detections, failed_batches)."""
        from app.core.user_settings import load_settings

        app_settings = load_settings()
        model = app_settings.vision.model
        batch_size = max(1, settings.onscreen_batch_size)
        batches = [frames[i:i + batch_size] for i in range(0, len(frames), batch_size)]
        detections: List[FrameDetection] = []
        failed = 0
        errors: List[str] = []
        sem = asyncio.Semaphore(_GEMINI_CONCURRENCY)
        from app.core.model_catalog import is_unavailable_error

        async def run_batch(batch: List[Frame]) -> None:
            nonlocal failed
            async with sem:
                ctx.raise_if_cancelled()
                for attempt in range(3):
                    try:
                        detections.extend(await _gemini_detect(
                            client, model, batch,
                            thinking=app_settings.vision.thinking,
                            resolution=app_settings.vision_media_resolution,
                            usage=ctx.usage,
                            instruction=instruction,
                            stage=stage,
                            flex=app_settings.vision.flex,
                            flex_state=ctx.flex_state,
                            sensitivity=sensitivity,
                        ))
                        break
                    except Exception as e:
                        if is_unavailable_error(e):
                            # Same answer for every batch (no access to the model, bad key): stop now
                            raise RuntimeError(f"{model}: {e}") from e
                        if not errors:
                            errors.append(f"{model}: {e}")
                        logger.warning(f"Vision batch failed (attempt {attempt + 1}/3): {e}")
                        await asyncio.sleep(2 * (attempt + 1))
                else:
                    failed += 1
                if on_batch:
                    await on_batch(len(batches))

        await asyncio.gather(*(run_batch(b) for b in batches))
        if failed and errors:
            ctx.warnings.append(f"Vision ({stage}): {failed} frame batch(es) failed — {errors[0][:300]}")
        return detections, failed

    async def _scan_frames(self, ctx: PipelineContext, client: Any, progress) -> None:
        if ctx.frame_task is None:
            ctx.frame_task = asyncio.ensure_future(sample_frames(ctx))
        await progress(5.0, "Sampling video frames...")
        task = asyncio.ensure_future(ctx.frame_task)
        out_dir = frames_dir(ctx.job_id)
        expected = max(1, int((ctx.duration or 0.0) / max(0.5, settings.onscreen_frame_interval)))
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
                break
            except asyncio.TimeoutError:
                ctx.raise_if_cancelled()
                if out_dir.exists():
                    written = sum(1 for _ in out_dir.glob("f_*.jpg"))
                    if written > 0:
                        pct = min(14.0, 5.0 + 9.0 * min(1.0, written / expected))
                        await progress(pct, f"Sampling video frames... ({written}/{expected})")
        frames: List[Frame] = await task
        ctx.raise_if_cancelled()

        if ctx.embedded_dialogue_segments:
            speech = [(float(s["start_time"]), float(s["end_time"])) for s in ctx.embedded_dialogue_segments]
        else:
            speech = [(s.start_time, s.end_time) for s in ctx.raw_segments]

        sensitivity = getattr(ctx, "onscreen_sensitivity", 2)
        if sensitivity == 1:
            frame_budget = int(settings.onscreen_max_frames * 0.75)
        elif sensitivity == 3:
            frame_budget = int(settings.onscreen_max_frames * 1.35)
        else:
            frame_budget = settings.onscreen_max_frames

        if getattr(ctx, "skip_end_credits", True):
            from app.pipeline.credits_detect import detect_visual_credit_spans, filter_frames_for_credits

            thumbs_file = out_dir / "thumbs.raw"
            thumbs_bytes = thumbs_file.read_bytes() if thumbs_file.exists() else b""
            chapters = (ctx.video_metadata or {}).get("chapters") or ()
            ctx.visual_credit_spans = detect_visual_credit_spans(
                thumbs_bytes,
                settings.onscreen_frame_interval,
                ctx.duration or 0.0,
            )
            frames, _ = filter_frames_for_credits(
                frames,
                ctx.duration or 0.0,
                chapters=chapters,
                thumbs_bytes=thumbs_bytes,
                frame_interval=settings.onscreen_frame_interval,
            )

        selected = select_frames(frames, speech, frame_budget)

        from app.core.user_settings import load_settings

        app_settings = load_settings()
        if app_settings.cost_saver_mode and selected:
            from math import ceil
            from app.core.model_catalog import estimated_thinking_tokens, image_tokens, request_cost
            from app.pipeline.onscreen_prefilter import prefilter_frames

            before_count = len(selected)
            clusters = await prefilter_frames(selected)
            after_count = len(clusters)
            filtered_frames = max(0, before_count - after_count)
            if filtered_frames > 0 and ctx.usage is not None and hasattr(ctx.usage, "record_local_savings"):
                v = app_settings.vision
                img_tok = image_tokens(v.model, app_settings.vision_media_resolution)
                b_size = max(1, settings.onscreen_batch_size)
                saved_batches = max(0, ceil(before_count / b_size) - (ceil(after_count / b_size) if after_count else 0))
                think_tok = estimated_thinking_tokens(v.model, v.thinking)
                saved_in = filtered_frames * img_tok + saved_batches * 450
                saved_out = saved_batches * (150 + think_tok)
                saved_cost = request_cost(v.model, saved_in, saved_out, flex=getattr(v, "flex", False)) or 0.0
                ctx.usage.record_local_savings(
                    saved_tokens=saved_in + saved_out,
                    frames_filtered=filtered_frames,
                    saved_cost_usd=saved_cost,
                )
            logger.info(
                f"Cost-Saver frame pre-filter: {before_count} selected -> {after_count} cluster(s) sent to Gemini"
            )
            selected = [
                Frame(
                    time=c.start_time,
                    path=c.representative_path,
                    change=1.0,
                    static_until=c.end_time,
                )
                for c in clusters
            ]
            if not selected:
                await progress(65.0, f"Filtered {filtered_frames} blank frames locally")
                return

        await progress(15.0, f"Scanning {len(selected)} of {len(frames)} frames for on-screen text...")
        done = 0

        async def on_batch(total: int) -> None:
            nonlocal done
            done += 1
            await progress(15.0 + 50.0 * done / max(1, total), f"Scanned {done}/{total} frame batches")

        instruction = get_vision_system_instruction(sensitivity)
        detections, failed = await self._detect_batches(
            ctx, client, selected, instruction, "onscreen", on_batch, sensitivity=sensitivity
        )
        found = merge_frame_detections(
            detections,
            settings.onscreen_frame_interval,
            drop_tags=True,
            repeat_window=_ONSCREEN_REPEAT_WINDOW,
            file_name=ctx.file_name or "",
            sensitivity=sensitivity,
        )
        dialogue_list = ctx.embedded_dialogue_segments or [
            {"start_time": s.start_time, "end_time": s.end_time, "original_text": s.text}
            for s in ctx.raw_segments
        ]
        filtered_found = []
        for f in found:
            is_dialogue_dup = any(
                _same_text(f["original_text"], d["original_text"])
                and max(0.0, min(f["end_time"], d["end_time"]) - max(f["start_time"], d["start_time"])) > 0.0
                for d in dialogue_list
            )
            if is_dialogue_dup:
                continue
            is_onscreen_dup = any(
                _same_text(f["original_text"], o["original_text"])
                and max(0.0, min(f["end_time"], o["end_time"]) - max(f["start_time"], o["start_time"])) > 0.0
                for o in ctx.onscreen_segments
            )
            if is_onscreen_dup:
                continue
            filtered_found.append(f)

        ctx.onscreen_segments = sorted(ctx.onscreen_segments + filtered_found, key=lambda s: s["start_time"])
        for seg in ctx.onscreen_segments:
            logger.info(f"On-screen text {seg['start_time']:.1f}-{seg['end_time']:.1f}s: {seg['original_text']!r}")

    # -- burned-in subtitles for foreign speech -------------------------------------

    async def _foreign_subtitles(self, ctx: PipelineContext, client: Any, progress, used_forced: bool) -> None:
        if not ctx.foreign_spans or used_forced:
            return
        if client is None:
            ctx.warnings.append(
                "Speech in another language was found, but reading its burned-in subtitles needs a Gemini "
                "API key; Whisper's transcription of that speech was used instead."
            )
            return
        try:
            await self._read_subtitles(ctx, client, progress)
        except (PipelineCancelled, asyncio.CancelledError):
            raise
        except Exception as e:
            logger.error(f"Reading burned-in subtitles failed: {e}")
            ctx.warnings.append(f"Reading burned-in subtitles failed: {e}")

    async def _read_subtitles(self, ctx: PipelineContext, client: Any, progress) -> None:
        duration = ctx.duration or float("inf")
        spans_to_scan = list(ctx.foreign_spans)
        if getattr(ctx, "skip_end_credits", True):
            from app.pipeline.credits_detect import parse_chapter_spans

            chapters = (ctx.video_metadata or {}).get("chapters") or ()
            chapter_credits, chapter_scenes = parse_chapter_spans(
                chapters, ctx.duration or 0.0
            )
            credit_spans = list(chapter_credits) + list(getattr(ctx, "visual_credit_spans", ()) or ())
            if credit_spans:
                filtered_spans = []
                for sp in spans_to_scan:
                    mid = (float(sp["start"]) + float(sp["end"])) / 2.0
                    in_scene = any(s - 1.0 <= mid <= e + 1.0 for s, e in chapter_scenes)
                    in_cred = any(s - 1.0 <= mid <= e + 1.0 for s, e in credit_spans)
                    if in_cred and not in_scene:
                        logger.info(
                            f"Foreign passage at {sp['start']:.1f}-{sp['end']:.1f}s skipped "
                            f"(falls inside end-credits roll)"
                        )
                        continue
                    filtered_spans.append(sp)
                spans_to_scan = filtered_spans

        if not spans_to_scan:
            return

        total_seconds = sum(s["end"] - s["start"] for s in spans_to_scan)
        interval = max(_SUB_INTERVAL, total_seconds / _SUB_MAX_FRAMES)
        detections: List[FrameDetection] = []
        await progress(70.0, f"Reading burned-in subtitles in {len(spans_to_scan)} foreign-language passage(s)...")

        for n, span in enumerate(spans_to_scan):
            # The scanned window: the span plus a fixed margin on both sides
            start = max(0.0, span["start"] - _SUB_MARGIN)
            end = min(duration, span["end"] + _SUB_MARGIN)
            frames = await sample_subtitle_crops(ctx, start, end, f"s{n}", interval)
            found, _ = await self._detect_batches(ctx, client, frames, SUBTITLE_INSTRUCTION, "foreign")
            detections.extend(found)
            logger.info(f"Foreign passage {n + 1}: {start:.1f}-{end:.1f}s, {len(frames)} crops, "
                        f"{len(found)} subtitle reading(s)")

            # Keep looking outward while a subtitle is still on screen near the edge of what we scanned
            main_found = found
            read_start, read_end = start, end
            for side in ("before", "after"):
                found = main_found
                edge_start, edge_end = start, end
                for k in range(_SUB_MAX_EXTENSIONS):
                    edge_hit = any(
                        (d.time <= edge_start + _SUB_EDGE) if side == "before" else (d.time >= edge_end - _SUB_EDGE)
                        for d in found
                    )
                    if not edge_hit:
                        break
                    if side == "before":
                        if edge_start <= 0:
                            break
                        a, b = max(0.0, edge_start - _SUB_EXTEND), edge_start
                        edge_start = a
                    else:
                        if edge_end >= duration:
                            break
                        a, b = edge_end, min(duration, edge_end + _SUB_EXTEND)
                        edge_end = b
                    frames = await sample_subtitle_crops(ctx, a, b, f"s{n}{side[0]}{k}", interval)
                    found, _ = await self._detect_batches(ctx, client, frames, SUBTITLE_INSTRUCTION, "foreign")
                    detections.extend(found)
                    logger.info(f"Foreign passage {n + 1}: subtitles continue {side} the window; "
                                f"read {a:.1f}-{b:.1f}s, {len(found)} more reading(s)")
                read_start, read_end = min(read_start, edge_start), max(read_end, edge_end)
            # Subtitles read anywhere in this window belong to the passage (see merge_foreign)
            span["read_start"], span["read_end"] = read_start, read_end
            await progress(70.0 + 28.0 * (n + 1) / len(spans_to_scan), f"Read subtitles in passage {n + 1}/{len(spans_to_scan)}")

        ctx.foreign_ocr_segments = merge_frame_detections(
            detections,
            frame_interval=interval,
            buffer=interval,
            min_duration=0.8,
            bracket=False,
            segment_type=SegmentType.FOREIGN.value,
        )

    @staticmethod
    def _cancel_sampling(ctx: PipelineContext) -> None:
        if ctx.frame_task is not None and not ctx.frame_task.done():
            ctx.frame_task.cancel()
