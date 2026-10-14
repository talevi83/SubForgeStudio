"""
User-editable AI settings (Settings screen), stored in storage/settings.json.

Environment variables (.env) provide the initial defaults; once saved from the UI the file wins.
"""
import json
import logging
import threading
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.core.config import settings
from app.core.model_catalog import (
    CATALOG, RETIRED_MODELS, TASKS, allowed_thinking, resolve_model, validate_flex, validate_thinking,
)

logger = logging.getLogger(__name__)
_lock = threading.Lock()


class GeminiCallSettings(BaseModel):
    model: str
    thinking: str = "default"
    # Flex service tier: 50% cheaper, but slower and sheddable (falls back to Standard when busy)
    flex: bool = False

    @model_validator(mode="after")
    def _check_thinking(self):
        validate_thinking(self.model, self.thinking)
        validate_flex(self.model, self.flex)
        return self


class AppSettings(BaseModel):
    translation: GeminiCallSettings
    glossary: GeminiCallSettings
    vision: GeminiCallSettings = Field(description="Reads on-screen text from video frames")
    vision_media_resolution: Literal["low", "medium", "high"] = "medium"

    @model_validator(mode="after")
    def _check_tasks(self):
        for task in TASKS:
            model = getattr(self, task).model
            if model in RETIRED_MODELS:
                raise ValueError(f"{model} is no longer available; use {RETIRED_MODELS[model]}")
            info = CATALOG.get(model)
            if info is not None and task not in info.tasks:
                raise ValueError(f"{info.label} is not offered for {task}")
        return self


# Cheapest sensible thinking level per task (thinking tokens are billed as output)
_PREFERRED_THINKING = {"translation": ("low",), "glossary": ("low",), "vision": ("minimal", "low")}


def _thinking_for(model: str, task: str, current: str = "default") -> str:
    allowed = allowed_thinking(model)
    if current != "default" and current in allowed:
        return current
    return next((lvl for lvl in _PREFERRED_THINKING[task] if lvl in allowed), "default")


def _defaults() -> AppSettings:
    configured = {"translation": settings.gemini_model, "glossary": settings.gemini_model,
                  "vision": settings.gemini_vision_model}
    calls = {}
    for task, model in configured.items():
        model = resolve_model(model, task)
        calls[task] = GeminiCallSettings(model=model, thinking=_thinking_for(model, task))
    return AppSettings(**calls)


def _migrate(raw: dict) -> bool:
    """Swap retired/unsuitable models in saved settings for working ones. Returns True if changed."""
    changed = False
    for task in TASKS:
        call = raw.get(task)
        if not isinstance(call, dict):
            continue
        old = call.get("model", "")
        new = resolve_model(old, task)
        if new != old:
            logger.info(f"Settings: {task} model {old} is unavailable or unsuitable; using {new}")
            call["model"] = new
            call["thinking"] = _thinking_for(new, task)
            changed = True
    return changed


def _path():
    return settings.storage_dir / "settings.json"


def load_settings() -> AppSettings:
    with _lock:
        path = _path()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                changed = _migrate(raw)
                loaded = AppSettings.model_validate(raw)
                if changed:
                    _write(path, loaded)
                return loaded
            except Exception as e:
                logger.warning(f"Ignoring invalid {path.name} ({e}); using defaults")
        return _defaults()


def _write(path, new: AppSettings) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(new.model_dump(), indent=2), encoding="utf-8")
    tmp.replace(path)


def save_settings(new: AppSettings) -> AppSettings:
    with _lock:
        _write(_path(), new)
    return new
