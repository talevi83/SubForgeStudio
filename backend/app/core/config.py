from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    # Gemini API Settings
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_batch_size: int = 30

    # Whisper Settings
    whisper_default_model: str = "medium"
    whisper_device: str = "auto"          # "auto", "cuda", or "cpu"
    whisper_compute_type: str = "auto"    # "auto", "float16", or "int8"

    # Subtitle Rules
    max_chars_per_line: int = 42
    max_cps_warning: float = 20.0

    # Storage & Database
    storage_dir: Path = Path("./storage")
    database_url: str = "sqlite:///./storage/subforge.db"
    host: str = "0.0.0.0"
    port: int = 8000

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
