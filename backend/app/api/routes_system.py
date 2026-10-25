import asyncio
import logging
import time
from typing import List, Optional

from fastapi import APIRouter, Response
from app.core.config import settings
from app.core.hardware import get_hardware_info
from app.models.schemas import SystemInfoResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["System"])

_gemini_models_cache: Optional[List[str]] = None
_gemini_models_cache_at = 0.0


def _fetch_gemini_models() -> List[str]:
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    names = []
    for m in client.models.list():
        name = (getattr(m, "name", "") or "").replace("models/", "")
        actions = getattr(m, "supported_actions", None) or []
        if name.startswith("gemini") and (not actions or "generateContent" in actions):
            if not any(x in name for x in ("embedding", "tts", "image", "audio", "live")):
                names.append(name)
    return sorted(set(names), reverse=True)


async def _gemini_models() -> List[str]:
    """Live model list from the API (cached for an hour), falling back to the configured list."""
    global _gemini_models_cache, _gemini_models_cache_at
    configured = list(dict.fromkeys([settings.gemini_model, *settings.gemini_models]))
    if not settings.is_gemini_configured:
        return configured
    if _gemini_models_cache is None or time.time() - _gemini_models_cache_at > 3600:
        try:
            _gemini_models_cache = await asyncio.wait_for(asyncio.to_thread(_fetch_gemini_models), timeout=5)
            _gemini_models_cache_at = time.time()
        except Exception as e:
            logger.info(f"Could not list Gemini models ({e}); using configured list")
            return configured
    return list(dict.fromkeys(configured + (_gemini_models_cache or [])))


def reset_gemini_models_cache() -> None:
    global _gemini_models_cache, _gemini_models_cache_at
    _gemini_models_cache = None
    _gemini_models_cache_at = 0.0


@router.get("/info", response_model=SystemInfoResponse)
async def get_system_info():
    """Retrieve server hardware capabilities and configuration status."""
    hw = await asyncio.to_thread(get_hardware_info)
    return SystemInfoResponse(
        is_gemini_configured=settings.is_gemini_configured,
        masked_api_key=settings.masked_api_key,
        gemini_model=settings.gemini_model,
        gemini_models=await _gemini_models(),
        whisper_default_model=settings.whisper_default_model,
        whisper_models=settings.whisper_models,
        max_cps_warning=settings.max_cps_warning,
        hardware=hw,
    )


@router.get("/log")
def download_server_log():
    """The backend's own log (previous rotation included), for troubleshooting."""
    from datetime import datetime

    from app.core.job_logging import server_log_path

    current = server_log_path()
    parts = []
    previous = current.with_name(current.name + ".1")
    for path in (previous, current):
        if path.exists():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    content = "".join(parts) or "(The server log is empty.)\n"
    name = f"subforge-server-{datetime.now():%Y%m%d-%H%M}.log"
    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-cache"},
    )
