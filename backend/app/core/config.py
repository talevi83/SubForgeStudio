from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    # Gemini API Settings
    gemini_api_key: Optional[str] = None
    # Gemini 2.5 models are only served to keys that used them before (Google, 2026); current defaults:
    gemini_model: str = "gemini-3.8-flash"
    gemini_batch_size: int = 60
    # Models offered in the UI when the live model list can't be fetched from the API
    gemini_models: List[str] = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    # Model used to read on-screen text from sampled video frames
    gemini_vision_model: str = "gemini-3.5-flash-lite"
    # On-screen text scan: seconds between sampled frames, frame budget, frames per request
    onscreen_frame_interval: float = 2.0
    onscreen_max_frames: int = 660
    onscreen_batch_size: int = 12
    # Run a one-off glossary pass (characters, genders, terms) before translating
    gemini_glossary_pass: bool = True
    # Local PC pre-processing (frame pre-filtering, Whisper VRAM unload, local gender pre-tagging)
    cost_saver_mode: bool = False

    # Whisper Settings
    whisper_default_model: str = "medium"
    whisper_device: str = "auto"          # "auto", "cuda", or "cpu"
    whisper_compute_type: str = "auto"    # "auto", "float16", or "int8"
    # >0 enables faster-whisper's BatchedInferencePipeline on CUDA (much faster, more VRAM).
    # 0 = classic sequential decoding.
    whisper_batch_size: int = 8
    whisper_models: List[str] = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
    # How many jobs may use the GPU (transcription) at the same time
    max_concurrent_transcriptions: int = 1
    # Re-listen (Whisper without VAD) to stretches no subtitle covers: recovers speech the voice
    # detector misses under gunfire, engines, music and radio filtering
    whisper_gap_recovery: bool = True
    whisper_snap_starts: bool = True      # pull word starts that sit inside a pause to the speech onset
    # "Sync existing subtitles": look for subtitles burned into the picture (local, no AI)
    sync_detect_burned_in: bool = True
    sync_burned_in_fps: float = 5.0

    # Subtitle Rules
    max_chars_per_line: int = 42
    max_cps_warning: float = 20.0
    target_cps: float = 17.0            # reading speed the refine stage aims for
    min_subtitle_duration: float = 1.0
    max_subtitle_duration: float = 7.0
    min_gap_seconds: float = 0.084      # ~2 frames @ 24fps

    # Storage & Database
    storage_dir: Path = Path("./storage")
    database_url: str = "sqlite:///./storage/subforge.db"
    # Unreferenced browser uploads older than this are deleted on startup
    upload_retention_hours: float = 24.0

    # Bind to localhost by default. Set HOST=0.0.0.0 only if you deliberately want LAN access —
    # the API can read any file path on this machine.
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    # Host headers accepted by the server (blocks DNS-rebinding attacks from websites).
    # Add your machine's LAN name/IP here if you set HOST=0.0.0.0.
    allowed_hosts: List[str] = ["localhost", "127.0.0.1", "[::1]"]

    model_config = SettingsConfigDict(
        env_file=(".env", str(Path(__file__).resolve().parent.parent.parent / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_gemini_configured(self) -> bool:
        """Return True if a valid non-placeholder Gemini API key is present."""
        return bool(
            self.gemini_api_key
            and self.gemini_api_key.strip()
            and self.gemini_api_key != "your_gemini_api_key_here"
        )


settings = Settings()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
(settings.storage_dir / "uploads").mkdir(parents=True, exist_ok=True)
(settings.storage_dir / "audio").mkdir(parents=True, exist_ok=True)
(settings.storage_dir / "exports").mkdir(parents=True, exist_ok=True)
(settings.storage_dir / "frames").mkdir(parents=True, exist_ok=True)
