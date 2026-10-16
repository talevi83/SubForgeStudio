"""
Automatic zero-config local gender pre-tagging via Ollama (runs when `cost_saver_mode == True`).

When translating to Hebrew or Arabic, pings `http://127.0.0.1:11434/api/tags` with a 2.0s timeout.
If Ollama is not running or has no models, silently skips in 0s with no errors.
If Ollama is available, selects a local text model (preferring qwen2.5 / gemma / llama), scans
refined segments for ambiguous 1st/2nd-person pronouns, queries Ollama in small batches with
`format="json"` and `keep_alive=0` on the final request (to free VRAM immediately), and produces
`[Speaker: M/F, To: M/F]` hints for `GeminiTranslator`.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_TAGS_URL = f"{OLLAMA_BASE_URL}/api/tags"
OLLAMA_GENERATE_URL = f"{OLLAMA_BASE_URL}/api/generate"

AMBIGUOUS_PRONOUN_RE = re.compile(
    r"\b(you|your|yours|yourself|you're|i am|i'm|i was)\b",
    re.IGNORECASE,
)

PREFERRED_FAMILIES = ("qwen2.5", "gemma", "llama")
PRETAG_BATCH_SIZE = 15


def is_gendered_target_language(target_lang: Optional[str]) -> bool:
    if not target_lang:
        return False
    norm = target_lang.strip().lower()
    return norm in ("hebrew", "he", "עברית", "arabic", "ar", "ערבית", "العربية")


def pick_ollama_model(models_payload: Sequence[Dict[str, Any]]) -> Optional[str]:
    """Choose the best available local text model from Ollama's `/api/tags` list."""
    names: List[str] = []
    for item in models_payload or ():
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name:
            continue
        lower = name.lower()
        if "embed" in lower or "nomic" in lower or "bge" in lower:
            continue
        names.append(name)
    if not names:
        return None
    for pref in PREFERRED_FAMILIES:
        for name in names:
            if pref in name.lower():
                return name
    return names[0]


def find_ambiguous_segments(segments: Sequence[Any]) -> List[Dict[str, Any]]:
    """Return segments whose text contains ambiguous English 1st/2nd-person forms."""
    ambiguous: List[Dict[str, Any]] = []
    for seg in segments:
        idx = seg["index"] if isinstance(seg, dict) else getattr(seg, "index", 0)
        text = seg["original_text"] if isinstance(seg, dict) else getattr(seg, "original_text", "")
        if text and AMBIGUOUS_PRONOUN_RE.search(text):
            ambiguous.append({"index": int(idx), "text": str(text)})
    return ambiguous


def _normalize_gender_code(val: Any) -> Optional[str]:
    if not val:
        return None
    s = str(val).strip().upper()
    if s in ("M", "MALE", "MAN"):
        return "M"
    if s in ("F", "FEMALE", "WOMAN"):
        return "F"
    return None


def format_gender_tag(speaker: Any, addressee: Any) -> Optional[str]:
    sp = _normalize_gender_code(speaker) or "?"
    to = _normalize_gender_code(addressee) or "?"
    if sp == "?" and to == "?":
        return None
    return f"[Speaker: {sp}, To: {to}]"


async def detect_ollama_model(timeout: float = 2.0) -> Optional[str]:
    """Ping local Ollama; return the chosen model name or None if unreachable / empty."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(OLLAMA_TAGS_URL)
            if resp.status_code != 200:
                return None
            data = resp.json()
            models = data.get("models") if isinstance(data, dict) else None
            return pick_ollama_model(models or [])
    except Exception as e:
        logger.debug(f"Local Ollama not available ({e}); skipping local gender pre-tagging")
        return None


async def pretag_gender_hints(
    segments: Sequence[Any],
    target_lang: str = "Hebrew",
    glossary: Optional[Dict[str, Any]] = None,
    batch_size: int = PRETAG_BATCH_SIZE,
    usage: Any = None,
    cloud_model: Optional[str] = None,
    on_batch: Optional[Any] = None,
) -> Dict[int, str]:
    """
    Query local Ollama for ambiguous lines and return `{segment_index: "[Speaker: M/F, To: M/F]"}`.
    Silently returns `{}` if Ollama is not running, has no models, or if any network error occurs.
    """
    if not is_gendered_target_language(target_lang) or not segments:
        return {}

    ambiguous = find_ambiguous_segments(segments)
    if not ambiguous:
        return {}

    model = await detect_ollama_model(timeout=2.0)
    if not model:
        return {}

    cast_lines = []
    for c in (glossary or {}).get("characters") or []:
        if isinstance(c, dict) and c.get("name"):
            cast_lines.append(f"{c.get('name')}: {c.get('gender', 'unknown')}")
    cast_summary = ", ".join(cast_lines[:12]) if cast_lines else "none"

    batches = [ambiguous[i:i + batch_size] for i in range(0, len(ambiguous), batch_size)]
    hints: Dict[int, str] = {}

    try:
        from app.core.model_catalog import estimated_thinking_tokens, request_cost

        ref_model = cloud_model or "gemini-3.8-flash"
        async with httpx.AsyncClient(timeout=90.0) as client:
            for batch_idx, batch in enumerate(batches):
                is_last = batch_idx == len(batches) - 1
                prompt = (
                    "Infer the speaker gender and the listener/addressee ('to') gender (M, F, or ?) for each subtitle line.\n"
                    "Note: if a line addresses a name directly (e.g. 'Sarah, are you ready?'), that name is the listener ('to'), not the speaker.\n"
                    f"Known characters: {cast_summary}\n"
                    'Return JSON: {"items": [{"index": <int>, "speaker": "M"|"F"|"?", "to": "M"|"F"|"?"}]}\n'
                    f"Lines: {json.dumps(batch, ensure_ascii=False)}"
                )
                body: Dict[str, Any] = {
                    "model": model,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0},
                }
                if is_last:
                    body["keep_alive"] = 0

                resp = await client.post(OLLAMA_GENERATE_URL, json=body)
                if resp.status_code != 200:
                    continue
                payload = resp.json()
                raw_text = payload.get("response") if isinstance(payload, dict) else None
                if not raw_text:
                    continue
                parsed = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
                items = parsed.get("items") if isinstance(parsed, dict) else None
                batch_tagged = 0
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict) or "index" not in item:
                            continue
                        tag = format_gender_tag(
                            item.get("speaker") or item.get("speaker_gender"),
                            item.get("to") or item.get("addressee") or item.get("addressee_gender"),
                        )
                        if tag:
                            hints[int(item["index"])] = tag
                            batch_tagged += 1

                if usage is not None and hasattr(usage, "record_local_ai"):
                    in_tok = int(payload.get("prompt_eval_count") or max(1, len(prompt) // 4)) if isinstance(payload, dict) else max(1, len(prompt) // 4)
                    out_tok = int(payload.get("eval_count") or max(1, len(str(raw_text)) // 4)) if isinstance(payload, dict) else max(1, len(str(raw_text)) // 4)
                    think_tok = estimated_thinking_tokens(ref_model, "low")
                    saved_tok = in_tok + out_tok + think_tok
                    saved_cost = request_cost(ref_model, in_tok, out_tok + think_tok) or 0.0
                    usage.record_local_ai(
                        model=model,
                        requests=1,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        segments_tagged=batch_tagged,
                        saved_tokens=saved_tok,
                        saved_cost_usd=saved_cost,
                    )
                if callable(on_batch):
                    await on_batch()
    except Exception as e:
        logger.debug(f"Local gender pre-tagger stopped early ({e}); continuing with Gemini")
        return hints

    if hints:
        logger.info(f"Local Ollama ({model}) pre-tagged gender hints for {len(hints)} segment(s)")
    return hints


def format_hints_for_context_notes(hints: Dict[int, str]) -> str:
    """Format `{index: '[Speaker: M, To: F]'}` into a compact context block for GeminiTranslator."""
    if not hints:
        return ""
    lines = [f"- #{idx}: {tag}" for idx, tag in sorted(hints.items())]
    return "Local speaker/addressee gender hints:\n" + "\n".join(lines)
