import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.model_catalog import catalog_payload, estimate_job_cost
from app.core.user_settings import AppSettings, load_settings, save_settings

router = APIRouter(prefix="/api/settings", tags=["Settings"])


class SettingsResponse(BaseModel):
    settings: AppSettings
    catalog: List[Dict[str, Any]]
    pricing_source: str = "https://ai.google.dev/gemini-api/docs/pricing"
    pricing_checked: str = "2026-10"


class EstimateRequest(BaseModel):
    duration_seconds: float
    enable_translation: bool = True
    detect_onscreen_text: bool = False
    has_forced_track: bool = False
    # Preview unsaved choices from the Settings screen
    settings: Optional[AppSettings] = None


@router.get("", response_model=SettingsResponse)
def get_settings():
    return SettingsResponse(settings=load_settings(), catalog=catalog_payload())


@router.put("", response_model=SettingsResponse)
def put_settings(new: Dict[str, Any]):
    try:
        validated = AppSettings.model_validate(new)
    except (ValidationError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    return SettingsResponse(settings=save_settings(validated), catalog=catalog_payload())


@router.post("/estimate")
def estimate(req: EstimateRequest):
    from app.providers.translation.gemini_provider import build_subtitle_system_prompt

    prompt_tokens = len(build_subtitle_system_prompt("English", "Hebrew")) // 3
    return estimate_job_cost(
        duration_seconds=req.duration_seconds,
        enable_translation=req.enable_translation,
        detect_onscreen_text=req.detect_onscreen_text,
        app_settings=req.settings or load_settings(),
        glossary_pass=settings.gemini_glossary_pass,
        translation_batch_size=settings.gemini_batch_size,
        system_prompt_tokens=prompt_tokens,
        frame_interval=settings.onscreen_frame_interval,
        max_frames=settings.onscreen_max_frames,
        vision_batch_size=settings.onscreen_batch_size,
        has_forced_track=req.has_forced_track,
    )


class CheckRequest(BaseModel):
    settings: Optional[AppSettings] = None
    api_key: Optional[str] = None


# Flex can be slow by design; give the test request longer than a Standard one
_FLEX_CHECK_TIMEOUT = 90


def _make_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


@router.post("/check")
async def check_models(req: CheckRequest):
    """Send a tiny request to each configured model to confirm this API key can use it."""
    key = (req.api_key or "").strip() or (settings.gemini_api_key if settings.is_gemini_configured else "")
    if not key:
        raise HTTPException(status_code=400, detail="No Gemini API key configured")
    chosen = req.settings or load_settings()
    client = _make_client(key)
    steps = {"translation": chosen.translation, "glossary": chosen.glossary, "vision": chosen.vision}

    async def probe(step: str, call):
        from google.genai import types

        from app.core.model_catalog import media_resolution, thinking_config

        from app.providers.gemini_calls import flex_config

        contents: Any = "Reply with the single word OK."
        config = types.GenerateContentConfig(
            max_output_tokens=512, thinking_config=thinking_config(call.model, call.thinking))
        if step == "vision":
            # Prove the model accepts images (and the chosen frame detail) like the real scan does
            contents = [types.Part.from_bytes(data=_probe_png(), mime_type="image/png"),
                        "What color is this image? Reply with one word."]
            config.media_resolution = media_resolution(call.model, chosen.vision_media_resolution)
        if call.flex:
            # Confirms the key may use Flex; no retries or fallback here, so the answer is honest
            config = flex_config(config)
        limit = _FLEX_CHECK_TIMEOUT if call.flex else 30
        try:
            await asyncio.wait_for(asyncio.to_thread(
                client.models.generate_content, model=call.model, contents=contents, config=config,
            ), timeout=limit)
            return step, call.model, None
        except asyncio.TimeoutError:
            return step, call.model, (
                f"Flex did not answer within {limit}s (Google may be busy); "
                "jobs retry and then fall back to Standard" if call.flex else f"No answer within {limit}s")
        except Exception as e:
            return step, call.model, (str(e)[:400] or type(e).__name__)

    outcomes = await asyncio.gather(*(probe(step, call) for step, call in steps.items()))
    results: Dict[str, Dict[str, Any]] = {}
    for step, model, error in outcomes:
        entry = results.setdefault(model, {"ok": True, "error": None})
        if error and entry["ok"]:
            entry.update(ok=False, error=f"{step}: {error}")
    return {"results": results, "steps": {step: {"model": m, "ok": e is None, "error": e} for step, m, e in outcomes}}


def _probe_png(width: int = 32, height: int = 32) -> bytes:
    """A tiny solid-red PNG built in memory (no image library needed)."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b""))
