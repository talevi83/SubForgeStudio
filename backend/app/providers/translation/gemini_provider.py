import asyncio
import json
import re
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from app.core.config import settings
from app.core.model_catalog import UsageTracker, default_temperature, thinking_config
from app.providers import gemini_calls
from app.providers.translation.base import (
    BaseTranslator,
    TranslatableSegment,
    BatchProgressCallback,
)
from app.utils.languages import is_rtl
from app.utils.rtl_fixer import apply_rtl_fix, strip_directional_marks

logger = logging.getLogger(__name__)

# Keep the glossary request comfortably inside the model's context window
_GLOSSARY_MAX_CHARS = 150_000


class TranslationAborted(Exception):
    """A non-retryable error (e.g. invalid API key) — stop translating remaining batches."""


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


class GlossaryCharacter(BaseModel):
    name: str = Field(description="Character name as spoken in the source")
    target_name: str = Field(description="How the name should be written in the target language")
    gender: str = Field(description="male, female, mixed group, or unknown")
    notes: str = Field(default="", description="Role, relationships, how they speak (formal, slang...)")


class GlossaryTerm(BaseModel):
    source: str
    translation: str
    notes: str = ""


class GlossarySchema(BaseModel):
    summary: str = Field(description="Two or three sentences: genre, setting, tone")
    characters: List[GlossaryCharacter] = []
    terms: List[GlossaryTerm] = Field(default=[], description="Recurring jargon, ranks, places, call signs")
    register_notes: str = Field(default="", description="Guidance on tone, profanity, formality")


# =====================================================================
# Prompt builders
# =====================================================================

def format_glossary(glossary: Optional[Dict[str, Any]]) -> str:
    if not glossary:
        return ""
    lines = ["### SHOW GLOSSARY (derived from the full transcript — stay consistent with it)"]
    if glossary.get("summary"):
        lines.append(f"Summary: {glossary['summary']}")
    chars = glossary.get("characters") or []
    if chars:
        lines.append("Characters:")
        for c in chars:
            note = f" — {c.get('notes')}" if c.get("notes") else ""
            lines.append(f"- {c.get('name')} → {c.get('target_name') or c.get('name')} ({c.get('gender', 'unknown')}){note}")
    terms = glossary.get("terms") or []
    if terms:
        lines.append("Terminology:")
        for t in terms:
            note = f" ({t.get('notes')})" if t.get("notes") else ""
            lines.append(f"- {t.get('source')} → {t.get('translation')}{note}")
    if glossary.get("register_notes"):
        lines.append(f"Register: {glossary['register_notes']}")
    return "\n".join(lines) + "\n"


def _known_glossary_note(known: Optional[Dict[str, Any]]) -> str:
    """Tell the glossary pass what the series already established, so it only adds what is new."""
    if not known:
        return ""
    return (
        "\n\nThis show has a saved glossary from earlier episodes. Treat it as authoritative and do NOT "
        "repeat or change anything in it; list only characters and terms that are NEW in this transcript.\n"
        + format_glossary(known)
    )


def build_subtitle_system_prompt(
    source_lang: str,
    target_lang: str,
    context_notes: str = "",
    max_chars_per_line: int = 42,
    glossary: Optional[Dict[str, Any]] = None,
) -> str:
    """Construct the system prompt engineered for cinema/broadcast subtitle translation."""
    extra_context = (
        f"\n### SHOW / MOVIE SPECIFIC CONTEXT (from the user — highest priority):\n{context_notes.strip()}\n"
        if context_notes and context_notes.strip()
        else ""
    )
    glossary_block = format_glossary(glossary)
    hebrew_rules = ""
    if target_lang.strip().lower() in ("hebrew", "he", "עברית"):
        hebrew_rules = """
6. **Natural Spoken Hebrew & Linguistic Guidelines:**
   - **Avoid "Translationese" (תרגומית):** Do not translate word-for-word. Translate the intent, subtext, and natural Hebrew conversational flow (e.g., translate "Are you kidding me?" as "אתה צוחק עליי?" or "את עובדת עליי?", never literally).
   - **Gender & Plurality Resolution:** English "you" or gender-neutral verbs must be conjugated accurately into masculine/feminine and singular/plural based on speaker/listener clues, the glossary and the show notes.
   - **Slang, Humor & Profanity:** Preserve the exact register, tone, and intensity of the original dialogue. Do not censor profanity; use authentic Hebrew equivalents that match the character's voice.
   - **Proper Nouns & Technical Terms:** Transliterate character names consistently across batches (use the glossary spellings).
   - **On-screen titles (`segment_type` = "ON_SCREEN", or any line wrapped in `[...]`):** translate concisely as an official Hebrew broadcast screen title and keep the square brackets, e.g. `[One week later...]` → `[שבוע לאחר מכן...]`, `[Damascus, Syria]` → `[דמשק, סוריה]`. Write dates/times the way Israeli broadcasters do.
   - **Radio & military comms (`segment_type` = "COMMS", or military/radio terminology in any line):** use authentic IDF/tactical Hebrew radio phrasing, short and clipped, e.g. "Copy that" → "קיבלתי" / "רות", "Over" → "עבור", "Over and out" → "סוף", "Roger" → "רות", "Say again" → "חזור", "Sitrep" → "דו"ח מצב", "Do you copy?" → "שומע?". Keep callsigns as transliterated names.
"""

    return f"""You are an elite, broadcast-grade audiovisual subtitle translator specializing in translating movies and TV series from {source_lang} to {target_lang}.

### CORE OBJECTIVE
Translate the `target_batch` subtitle segments into natural, idiomatic, spoken {target_lang} suitable for cinema and television (Netflix/HBO broadcast standards), while strictly maintaining a 1-to-1 mapping with the original segment `index`.
{extra_context}
{glossary_block}
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
   - Respect `duration_sec`: keep phrasing concise so the viewer can read it comfortably (aim for {settings.target_cps:.0f} characters per second or fewer). Condense rather than overflow.

3. **Split Sentences & Continuity:**
   - Sentences frequently span across two or more consecutive subtitle segments.
   - Inspect `previous_context` and `lookahead_context` to understand the full sentence before translating.
   - Ensure each segment contains the semantic portion spoken during that exact timeframe so the text stays synchronized with the actor's speech on screen.

4. **Dialogue & Dual Speakers:**
   - If the original segment contains two speakers (indicated by leading hyphens, e.g., `- Hello.\\n- Hi.`), you MUST preserve the two-line hyphen structure (`- ...\\n- ...`).

5. **Segment Types (`segment_type`):**
   - "DIALOGUE": normal speech.
   - "COMMS": a radio / walkie-talkie transmission — keep it terse, in radio procedure style.
   - "ON_SCREEN": text shown on screen (time jump, location, date, sign). Translate it as a short screen title and keep any surrounding square brackets `[...]`. A bracketed first line inside a DIALOGUE segment is also an on-screen title: translate it the same way and keep it as the first line.
   - "FOREIGN": a line spoken in a different language than the rest of the show (e.g. Russian in an English series). Its text is either the show's own subtitle for that line or a transcription in the original language — either way, translate it into the target language as ordinary dialogue (no brackets).
{hebrew_rules}
7. **Output hygiene:** Plain text only. Keep punctuation at the logical end of sentences and do NOT insert Unicode direction marks or manually reverse punctuation — a downstream processor handles right-to-left display. Never put the ASCII double-quote character (") inside a translation — it ends the JSON string and cuts the subtitle off. For Hebrew abbreviations use gershayim ״ and geresh ׳ (ד״ר, אב״כ, נ״צ, כנ״ל, צה״ל, ג׳יימס); for quotations use “ ”.
"""


def _is_fatal(exc: Exception) -> bool:
    """Errors retrying won't fix: bad/missing key, permission, unknown model."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    msg = str(exc).lower()
    if code in (401, 403):
        return True
    if code in (400, 404) and ("api key" in msg or "api_key" in msg or "not found" in msg or "permission" in msg):
        return True
    return "api key not valid" in msg


def _looks_truncated(source: str, translation: str) -> bool:
    """A translation far shorter than its source is almost always cut off mid-answer
    (seen: 'Stand by for exact coordinates. Nishioka, eyes on.' -> 'המתן לנ')."""
    src = len(re.sub(r"\s+", " ", source).strip())
    return src >= 25 and len(translation.strip()) < 0.35 * src


def _is_rate_limit(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code == 429 or "resource_exhausted" in str(exc).lower()


# =====================================================================
# Gemini Translator Implementation
# =====================================================================

class GeminiTranslator(BaseTranslator):
    """
    Context-aware batch subtitle translator powered by the Google GenAI SDK.
    Supports a whole-transcript glossary pass, sliding context windows, structured JSON
    output, partial retries of missing indices, and explicit failure flags.
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
        thinking: str = "default",
        glossary_model: Optional[str] = None,
        glossary_thinking: str = "default",
        usage: Optional[UsageTracker] = None,
        flex: bool = False,
        glossary_flex: bool = False,
        flex_state: Optional[gemini_calls.FlexState] = None,
    ):
        resolved_key = (api_key or "").strip() or settings.gemini_api_key
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
        self.thinking = thinking
        self.glossary_model = glossary_model or self.model_name
        self.glossary_thinking = glossary_thinking
        self.usage = usage
        # Flex service tier: half price, slower, may fall back to Standard when Google is busy
        self.flex = flex
        self.glossary_flex = glossary_flex
        self.flex_state = flex_state if flex_state is not None else gemini_calls.FlexState()
        self.last_glossary_error: Optional[str] = None

    async def _generate(
        self,
        contents: str,
        system_prompt: str,
        schema,
        temperature: float,
        stage: str = "translation",
        model: Optional[str] = None,
        thinking: Optional[str] = None,
        flex: Optional[bool] = None,
    ):
        model = model or self.model_name
        thinking = self.thinking if thinking is None else thinking
        return await gemini_calls.generate_content(
            self.client,
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=default_temperature(model, temperature),
                thinking_config=thinking_config(model, thinking),
            ),
            flex=self.flex if flex is None else flex,
            usage=self.usage,
            stage=stage,
            flex_state=self.flex_state,
        )

    async def build_glossary(
        self,
        segments: List[TranslatableSegment],
        source_lang: str,
        target_lang: str,
        context_notes: str = "",
        known_glossary: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        transcript = "\n".join(f"{s.index}: {s.original_text.replace(chr(10), ' ')}" for s in segments)
        if len(transcript) > _GLOSSARY_MAX_CHARS:
            transcript = transcript[:_GLOSSARY_MAX_CHARS]
        notes = f"\nUser notes about the show:\n{context_notes.strip()}\n" if context_notes.strip() else ""
        system_prompt = (
            f"You prepare subtitle translators. Read the full {source_lang} transcript of a movie/episode and "
            f"produce a concise glossary for translating it into {target_lang}: the main characters (with their "
            f"gender, inferred from pronouns, titles and how others address them), recurring terminology, ranks, "
            f"places and call signs with a recommended {target_lang} rendering, and notes on tone/register. "
            f"Only include what the transcript supports; mark gender 'unknown' when unsure.{notes}"
            f"{_known_glossary_note(known_glossary)}"
        )
        try:
            response = await self._generate(
                transcript, system_prompt, GlossarySchema, temperature=0.1,
                stage="glossary", model=self.glossary_model, thinking=self.glossary_thinking,
                flex=self.glossary_flex,
            )
            glossary = GlossarySchema.model_validate_json(response.text).model_dump()
            logger.info(
                f"Glossary built: {len(glossary.get('characters', []))} characters, "
                f"{len(glossary.get('terms', []))} terms"
            )
            return glossary
        except Exception as exc:
            msg = str(exc).lower()
            # Only a bad key stops everything; a problem with the glossary *model* must not
            # prevent the translation (which may use a different model).
            if getattr(exc, "code", None) == 401 or "api key not valid" in msg or "api_key_invalid" in msg:
                raise TranslationAborted(str(exc)) from exc
            self.last_glossary_error = f"{self.glossary_model}: {exc}"
            logger.warning(f"Glossary pass failed (continuing without it): {exc}")
            return None

    async def translate_segments(
        self,
        segments: List[TranslatableSegment],
        source_lang: str = "English",
        target_lang: str = "Hebrew",
        context_notes: str = "",
        apply_rtl: bool = False,
        on_batch_complete: Optional[BatchProgressCallback] = None,
        glossary: Optional[Dict[str, Any]] = None,
        context_segments: Optional[List[TranslatableSegment]] = None,
    ) -> List[TranslatableSegment]:
        """Translate segments in batches with sliding context."""
        if not segments:
            return []

        context = context_segments or segments
        position = {seg.index: pos for pos, seg in enumerate(context)}
        total_segments = len(segments)
        from app.providers.translation.glossary_pruner import prune_glossary_for_batch

        do_rtl = apply_rtl and is_rtl(target_lang)

        for start_idx in range(0, total_segments, self.batch_size):
            end_idx = min(start_idx + self.batch_size, total_segments)
            current_batch = segments[start_idx:end_idx]

            first_pos = position.get(current_batch[0].index, 0)
            last_pos = position.get(current_batch[-1].index, len(context) - 1)
            prev_context = context[max(0, first_pos - self.prev_context_size):first_pos]
            lookahead_context = context[last_pos + 1:last_pos + 1 + self.lookahead_size]

            batch_glossary = prune_glossary_for_batch(
                glossary=glossary,
                batch_texts=[s.original_text for s in current_batch],
                previous_context=[s.original_text for s in prev_context],
                lookahead_context=[s.original_text for s in lookahead_context],
            )
            if glossary and batch_glossary and self.usage is not None and hasattr(self.usage, "record_local_savings"):
                from app.core.model_catalog import request_cost

                saved_chars = max(0, len(format_glossary(glossary)) - len(format_glossary(batch_glossary)))
                saved_toks = saved_chars // 4
                if saved_toks > 0:
                    saved_cost = request_cost(self.model_name, saved_toks, 0, flex=self.flex) or 0.0
                    self.usage.record_local_savings(saved_tokens=saved_toks, saved_cost_usd=saved_cost)

            system_prompt = build_subtitle_system_prompt(
                source_lang=source_lang,
                target_lang=target_lang,
                context_notes=context_notes,
                max_chars_per_line=self.max_chars_per_line,
                glossary=batch_glossary,
            )

            try:
                await self._translate_single_batch(
                    target_batch=current_batch,
                    prev_context=prev_context,
                    lookahead_context=lookahead_context,
                    system_prompt=system_prompt,
                    apply_rtl=do_rtl,
                )
            except TranslationAborted:
                for seg in segments[start_idx:]:
                    if not seg.translated_text:
                        seg.translation_failed = True
                raise

            progress_pct = round((end_idx / total_segments) * 100, 2)
            logger.info(f"Translated batch {start_idx + 1}-{end_idx}/{total_segments} ({progress_pct}%)")

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
        """Translate one batch. Missing indices are retried; leftovers are flagged as failed."""
        pending = list(target_batch)
        for seg in pending:
            seg.translation_failed = False

        for attempt in range(1, self.max_retries + 1):
            payload = {
                "previous_context": [
                    {
                        "index": seg.index,
                        "original_text": seg.original_text,
                        "translated_text": strip_directional_marks(seg.translated_text or ""),
                    }
                    for seg in prev_context
                ],
                "target_batch": [
                    {
                        "index": seg.index,
                        "segment_type": seg.segment_type,
                        "duration_sec": seg.duration,
                        "original_text": seg.original_text,
                    }
                    for seg in pending
                ],
                "lookahead_context": [
                    {"index": seg.index, "original_text": seg.original_text}
                    for seg in lookahead_context
                ],
            }
            try:
                response = await self._generate(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    system_prompt,
                    TranslationBatchResponseSchema,
                    temperature=0.2,
                )
                parsed = TranslationBatchResponseSchema.model_validate_json(response.text)
                translation_map = {item.index: item.translated_text for item in parsed.translations}

                still_missing = []
                for seg in pending:
                    raw = strip_directional_marks((translation_map.get(seg.index) or "").strip())
                    if raw and _looks_truncated(seg.original_text, raw):
                        logger.warning(f"Translation of #{seg.index} looks cut off ({raw!r} for "
                                       f"{seg.original_text!r}); asking again")
                        raw = ""
                    if raw:
                        seg.translated_text = apply_rtl_fix(raw) if apply_rtl else raw
                    else:
                        still_missing.append(seg)
                pending = still_missing
                if not pending:
                    return
                raise ValueError(f"Gemini response omitted segment indices: {[s.index for s in pending]}")

            except Exception as exc:
                if _is_fatal(exc):
                    raise TranslationAborted(str(exc)) from exc
                logger.warning(f"Batch translation attempt {attempt}/{self.max_retries} failed: {exc}")
                if attempt < self.max_retries:
                    delay = 1.5 * (2 ** (attempt - 1))
                    if _is_rate_limit(exc):
                        delay *= 8
                    await asyncio.sleep(delay)

        logger.error(f"Giving up on {len(pending)} segment(s) in this batch; flagged as failed")
        for seg in pending:
            seg.translated_text = None
            seg.translation_failed = True
