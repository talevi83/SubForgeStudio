from fastapi import APIRouter
from app.core.config import settings
from app.core.hardware import get_hardware_info
from app.models.schemas import SystemInfoResponse

router = APIRouter(prefix="/api/system", tags=["System"])


@router.get("/info", response_model=SystemInfoResponse)
def get_system_info():
    """Retrieve server hardware capabilities and configuration status."""
    hw = get_hardware_info()
    return SystemInfoResponse(
        is_gemini_configured=settings.is_gemini_configured,
        gemini_model=settings.gemini_model,
        whisper_default_model=settings.whisper_default_model,
        hardware=hw,
    )
