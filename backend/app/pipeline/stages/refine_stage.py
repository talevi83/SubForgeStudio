from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.pipeline.context import PipelineContext, RawSegment, TranscribedWord
from app.pipeline.stages.base import PipelineStage, ProgressCallback
from app.utils.srt_formatter import split_long_lines, calculate_cps


def refine_raw_segments(
    raw_segments: List[RawSegment],
    max_chars_per_line: int = 42,
    min_duration: float = 0.8,
    max_duration: float = 7.0,
) -> List[Dict[str, Any]]:
    """
    Refines raw transcribed segments into broadcast-standard subtitle segments:
    - Splits segments exceeding max characters (84) or max duration (7.0s) using word-level timing
    - Formats text into at most 2 balanced lines per subtitle
    - Enforces minimum duration (0.8s) and prevents time overlaps
    - Calculates Characters Per Second (CPS)
    """
    if not raw_segments:
        return []

    max_chars_per_block = max_chars_per_line * 2
    split_units: List[Dict[str, Any]] = []

    for raw in raw_segments:
        raw_text = raw.text.strip()
        if not raw_text:
            continue

        raw_dur = max(0.1, raw.end_time - raw.start_time)
        needs_split = (len(raw_text) > max_chars_per_block) or (raw_dur > max_duration)

        # If word timestamps are available and splitting is needed
        if needs_split and raw.words and len(raw.words) > 1:
            current_words: List[TranscribedWord] = []
            current_len = 0

            for word in raw.words:
                w_text = word.word.strip()
                if not w_text:
                    continue

                projected_len = current_len + (1 if current_words else 0) + len(w_text)
                w_start = current_words[0].start if current_words else word.start
                projected_dur = word.end - w_start

                # Split if adding word exceeds length limit or max duration
                if current_words and (projected_len > max_chars_per_block or projected_dur > max_duration):
                    sub_text = " ".join(w.word.strip() for w in current_words)
                    sub_start = current_words[0].start
                    sub_end = current_words[-1].end
                    sub_conf = sum(w.probability for w in current_words) / len(current_words)
                    split_units.append({
                        "start_time": sub_start,
                        "end_time": sub_end,
                        "text": sub_text,
                        "confidence": sub_conf,
                    })
                    current_words = [word]
                    current_len = len(w_text)
                else:
                    current_words.append(word)
                    current_len = projected_len

            if current_words:
                sub_text = " ".join(w.word.strip() for w in current_words)
                sub_start = current_words[0].start
                sub_end = current_words[-1].end
                sub_conf = sum(w.probability for w in current_words) / len(current_words)
                split_units.append({
                    "start_time": sub_start,
                    "end_time": sub_end,
                    "text": sub_text,
                    "confidence": sub_conf,
                })
        else:
            split_units.append({
                "start_time": raw.start_time,
                "end_time": raw.end_time,
                "text": raw_text,
                "confidence": raw.confidence,
            })

    # Timing smoothing, line balancing, and CPS calculation
    refined: List[Dict[str, Any]] = []
    total_units = len(split_units)

    for i, unit in enumerate(split_units):
        start_t = float(unit["start_time"])
        end_t = float(unit["end_time"])

        # Prevent start time from overlapping previous end time
        if refined and start_t < refined[-1]["end_time"]:
            start_t = refined[-1]["end_time"] + 0.04

        # Enforce minimum duration if next segment allows
        if end_t - start_t < min_duration:
            next_start = float(split_units[i + 1]["start_time"]) if i + 1 < total_units else float("inf")
            target_end = start_t + min_duration
            if target_end <= next_start:
                end_t = target_end
            elif next_start > start_t + 0.2:
                end_t = next_start - 0.04
            else:
                end_t = max(start_t + 0.2, end_t)

        formatted_text = split_long_lines(unit["text"], max_chars_per_line=max_chars_per_line)
        duration = max(0.1, end_t - start_t)
        cps = calculate_cps(formatted_text, duration)

        refined.append({
            "index": len(refined) + 1,
            "start_time": round(start_t, 3),
            "end_time": round(end_t, 3),
            "original_text": formatted_text,
            "translated_text": None,
            "confidence": round(float(unit.get("confidence", 1.0)), 2),
            "cps": cps,
        })

    return refined


class RefineStage(PipelineStage):
    """Pipeline stage for refining raw transcribed subtitle blocks to broadcast standards."""

    @property
    def name(self) -> str:
        return "REFINING"

    async def execute(
        self,
        ctx: PipelineContext,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        if on_progress:
            await on_progress(10.0, None, None, "Refining subtitle segments and timing...", None)

        ctx.refined_segments = refine_raw_segments(
            ctx.raw_segments,
            max_chars_per_line=settings.max_chars_per_line,
        )

        if on_progress:
            await on_progress(100.0, None, None, f"Refined {len(ctx.refined_segments)} subtitle segments", None)
