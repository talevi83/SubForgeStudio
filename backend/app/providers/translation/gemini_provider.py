import asyncio
import json
import logging
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from app.core.config import settings
from app.providers.translation.base import (
    BaseTranslator,
    TranslatableSegment,
    BatchProgressCallback,
)
from app.utils.rtl_fixer import apply_rtl_fix

logger = logging.getLogger(__name__)


# =====================================================================
# Pydantic Schemas for Gemini Structured Output
# =====================================================================

class TranslatedItemSchema(BaseModel):
    index: int = Field(description="The exact index of the subtitle segment from the input.")
    translated_text: str = Field(
        description="The translated subtitle text. Use '\\n' if split into two lines (max 2 lines)."
    )


class TranslationBatchResponseSchema(BaseModel):
    translations: List[TranslatedItemSchema] = Field(
        description="List of translated subtitle segments matching the exact input indices."
    )


# =====================================================================
# Broadcast-Grade System Prompt Builder
# =====================================================================

def build_subtitle_system_prompt(
    source_lang: str,
    target_lang: str,
    context_notes: str = "",
    max_chars_per_line: int = 42,
) -> str:
    """Construct the system prompt engineered for cinema/broadcast subtitle translation."""
    extra_context = (
        f"\n### SHOW / MOVIE SPECIFIC CONTEXT:\n{context_notes.strip()}\n"
        if context_notes.strip()
        else ""
    )

    return f"""You are an elite, broadcast-grade audiovisual subtitle translator specializing in translating movies and TV series from {source_lang} to {target_lang}.

### CORE OBJECTIVE
Translate the `target_batch` subtitle segments into natural, idiomatic, spoken {target_lang} suitable for cinema and television (Netflix/HBO broadcast standards), while strictly maintaining a 1-to-1 mapping with the original segment `index`.
{extra_context}
### STRICT TECHNICAL RULES
1. **1-to-1 Index Mapping:**
   - You MUST return the exact same number of items as provided in `target_batch`.
   - Every returned item MUST have the exact `index` from `target_batch`.
   - NEVER merge two segments into one, and NEVER skip or invent an index.
   - Do NOT translate items from `previous_context` or `lookahead_context`—those are provided strictly for narrative continuity.

2. **Line Length & Formatting Constraints:**
   - Maximum **{max_chars_per_line} characters per line**.
   - Maximum **2 lines per subtitle segment** (separated by a single `\\n`).
   - If a translation exceeds {max_chars_per_line} characters, split it into two visually balanced lines at a natural linguistic boundary (e.g., after a comma, conjunction, or preposition).
   - Respect `duration_sec`: If a segment is very short (e.g., under 1.5s), keep the phrasing concise so the viewer can read it comfortably (aim for 18 characters per second or fewer).

3. **Split Sentences & Continuity:**
   - Sentences frequently span across two or more consecutive subtitle segments.
   - Inspect `previous_context` and `lookahead_context` to understand the full sentence before translating.
   - Ensure each segment contains the semantic portion spoken during that exact timeframe so the text stays synchronized with the actor's speech on screen.

4. **Dialogue & Dual Speakers:**
   - If the original segment contains two speakers (indicated by leading hyphens, e.g., `- Hello.\\n- Hi.`), you MUST preserve the two-line hyphen structure (`- ...\\n- ...`).

5. **Natural Spoken Hebrew & Linguistic Guidelines (When Target is Hebrew):**
   - **Avoid "Translationese" (תרגומית):** Do not translate word-for-word. Translate the intent, subtext, and natural Hebrew conversational flow (e.g., translate "Are you kidding me?" as "אתה צוחק עליי?" or "את עובדת עליי?", never literally).
   - **Gender & Plurality Resolution:** English "you" or gender-neutral verbs must be conjugated accurately into masculine/feminine and singular/plural based on speaker/listener clues in the surrounding context and show notes.
   - **Slang, Humor & Profanity:** Preserve the exact register, tone, and intensity of the original dialogue. Do not censor profanity; use authentic Hebrew equivalents that match the character's voice.
   - **Proper Nouns & Technical Terms:** Transliterate character names consistently across batches. Keep standard punctuation at the logical end of sentences (do NOT manually reverse punctuation; a downstream BiDi processor handles Unicode RTL marks).
"""


# =====================================================================
# Gemini Translator Implementation
# =====================================================================

class GeminiTranslator(BaseTranslator):
    """
    Context-aware batch subtitle translator powered by the Google GenAI SDK.
    Loads defaults from backend/.env via settings, supports sliding context windows,
    structured JSON outputs, automatic retries, and RTL BiDi post-processing.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        batch_size: Optional[int] = None,
        prev_context_size: int = 5,
        lookahead_size: int = 3,
        max_chars_per_line: Optional[int] = None,
        max_retries: int = 3,
    ):
        resolved_key = api_key or settings.gemini_api_key
        if not resolved_key or resolved_key == "your_gemini_api_key_here":
            raise ValueError(
                "GEMINI_API_KEY is missing. Set it in backend/.env or provide it in the UI."
            )

        self.client = genai.Client(api_key=resolved_key)
        self.model_name = model_name or settings.gemini_model
        self.batch_size = batch_size or settings.gemini_batch_size
        self.prev_context_size = prev_context_size
        self.lookahead_size = lookahead_size
        self.max_chars_per_line = max_chars_per_line or settings.max_chars_per_line
        self.max_retries = max_retries

    async def translate_segments(
        self,
        segments: List[TranslatableSegment],
        source_lang: str = "English",
        target_lang: str = "Hebrew",
        context_notes: str = "",
        apply_rtl: bool = True,
        on_batch_complete: Optional[BatchProgressCallback] = None,
    ) -> List[TranslatableSegment]:
        """Translate all segments in batches with sliding context."""
        if not segments:
            return []

        total_segments = len(segments)
        system_prompt = build_subtitle_system_prompt(
            source_lang=source_lang,
            target_lang=target_lang,
            context_notes=context_notes,
            max_chars_per_line=self.max_chars_per_line,
        )

        is_rtl_target = target_lang.lower() in {"hebrew", "he", "עברית", "arabic", "ar"}

        for start_idx in range(0, total_segments, self.batch_size):
            end_idx = min(start_idx + self.batch_size, total_segments)
            current_batch = segments[start_idx:end_idx]

            # Gather already-translated previous segments for continuity
            prev_start = max(0, start_idx - self.prev_context_size)
            prev_context = segments[prev_start:start_idx]

            # Gather upcoming untranslated segments for split-sentence awareness
            lookahead_end = min(total_segments, end_idx + self.lookahead_size)
            lookahead_context = segments[end_idx:lookahead_end]

            await self._translate_single_batch(
                target_batch=current_batch,
                prev_context=prev_context,
                lookahead_context=lookahead_context,
                system_prompt=system_prompt,
                apply_rtl=(apply_rtl and is_rtl_target),
            )

            # Calculate overall progress and notify WebSocket listener if provided
            progress_pct = round((end_idx / total_segments) * 100, 2)
            logger.info(
                f"Translated batch {start_idx + 1}-{end_idx}/{total_segments} ({progress_pct}%)"
            )

            if on_batch_complete:
                await on_batch_complete(current_batch, progress_pct)

        return segments

    async def _translate_single_batch(
        self,
        target_batch: List[TranslatableSegment],
        prev_context: List[TranslatableSegment],
        lookahead_context: List[TranslatableSegment],
        system_prompt: str,
        apply_rtl: bool,
    ) -> None:
        """Execute a single batch translation request with retry and index verification."""
        payload = {
            "previous_context": [
                {
                    "index": seg.index,
                    "original_text": seg.original_text,
                    "translated_text": seg.translated_text or "",
                }
                for seg in prev_context
            ],
            "target_batch": [
                {
                    "index": seg.index,
                    "duration_sec": seg.duration,
                    "original_text": seg.original_text,
                }
                for seg in target_batch
            ],
            "lookahead_context": [
                {
                    "index": seg.index,
                    "original_text": seg.original_text,
                }
                for seg in lookahead_context
            ],
        }

        user_prompt = json.dumps(payload, ensure_ascii=False, indent=2)

        for attempt in range(1, self.max_retries + 1):
            try:
                # Run synchronous SDK call in a worker thread to avoid blocking the event loop
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        response_schema=TranslationBatchResponseSchema,
                        temperature=0.2,  # Low temperature for faithful, consistent translation
                    ),
                )

                parsed_response = TranslationBatchResponseSchema.model_validate_json(
                    response.text
                )
                translation_map = {
                    item.index: item.translated_text
                    for item in parsed_response.translations
                }

                # Populate translated text back onto the segment objects
                missing_indices = []
                for seg in target_batch:
                    if seg.index in translation_map:
                        raw_translation = translation_map[seg.index].strip()
                        seg.translated_text = (
                            apply_rtl_fix(raw_translation)
                            if apply_rtl
                            else raw_translation
                        )
                    else:
                        missing_indices.append(seg.index)

                if missing_indices:
                    raise ValueError(
                        f"Gemini response omitted segment indices: {missing_indices}"
                    )

                return  # Batch succeeded

            except Exception as exc:
                logger.warning(
                    f"Batch translation attempt {attempt}/{self.max_retries} failed: {exc}"
                )
                if attempt == self.max_retries:
                    logger.error(
                        "Max retries reached for batch. Falling back to original text for missing segments."
                    )
                    for seg in target_batch:
                        if not seg.translated_text:
                            seg.translated_text = seg.original_text
                else:
                    # Exponential backoff before retry
                    await asyncio.sleep(1.5 * (2 ** (attempt - 1)))
