"""
Local Ollama status check and one-click setup (CLI installation, server launch, and model pull).
"""
import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.pipeline.gender_pretag import OLLAMA_BASE_URL, OLLAMA_TAGS_URL, pick_ollama_model

logger = logging.getLogger(__name__)

RECOMMENDED_LOCAL_MODEL = "qwen2.5:7b"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
OLLAMA_PULL_URL = f"{OLLAMA_BASE_URL}/api/pull"

_state: Dict[str, Any] = {
    "installing": False,
    "step": None,
    "error": None,
}
_setup_task: Optional[asyncio.Task] = None


def find_ollama_bin() -> Optional[str]:
    """Locate the `ollama` executable on PATH or in standard Windows install directories."""
    found = shutil.which("ollama")
    if found:
        return found
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
            Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return None


TupleResult = tuple[bool, List[str], Optional[str]]


async def _fetch_running_models(timeout: float = 1.5) -> TupleResult:
    """Return (running, models_list, selected_model)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(OLLAMA_TAGS_URL)
            if resp.status_code != 200:
                return False, [], None
            data = resp.json()
            raw_models = (data.get("models") if isinstance(data, dict) else None) or []
            names: List[str] = [
                str(m.get("name") or m.get("model")).strip()
                for m in raw_models
                if isinstance(m, dict) and (m.get("name") or m.get("model"))
            ]
            selected = pick_ollama_model(raw_models)
            return True, names, selected
    except Exception:
        return False, [], None


async def get_ollama_status() -> Dict[str, Any]:
    """Return current local Ollama installation, daemon, and model status."""
    binary = find_ollama_bin()
    running, models, selected = await _fetch_running_models(timeout=1.5)
    return {
        "installed": bool(binary or running),
        "running": running,
        "models": models,
        "selected_model": selected,
        "recommended_model": RECOMMENDED_LOCAL_MODEL,
        "download_url": OLLAMA_DOWNLOAD_URL,
        "installing": bool(_state["installing"]),
        "step": _state["step"],
        "error": _state["error"],
    }


async def _install_ollama_cli() -> None:
    if sys.platform == "win32":
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "irm https://ollama.com/install.ps1 | iex",
        ]
    else:
        cmd = ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 and not find_ollama_bin():
        err_text = stderr.decode(errors="ignore").strip()[-400:] if stderr else "Installer exited with error"
        raise RuntimeError(f"Ollama installation failed: {err_text}")


async def _ensure_server_running(binary: str) -> None:
    running, _, _ = await _fetch_running_models(timeout=1.5)
    if running:
        return

    kwargs: Dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen([binary, "serve"], **kwargs)

    for _ in range(20):
        await asyncio.sleep(0.5)
        running, _, _ = await _fetch_running_models(timeout=1.0)
        if running:
            return
    raise RuntimeError("Ollama server started but did not respond on 127.0.0.1:11434")


async def _pull_model(model: str) -> None:
    async with httpx.AsyncClient(timeout=1800.0) as client:
        resp = await client.post(
            OLLAMA_PULL_URL,
            json={"model": model, "stream": False},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Could not pull model {model}: HTTP {resp.status_code}")


async def _run_setup_workflow(model: str) -> None:
    try:
        _state["installing"] = True
        _state["error"] = None

        binary = find_ollama_bin()
        running, _, _ = await _fetch_running_models(timeout=1.5)
        if not binary and not running:
            _state["step"] = "installing_ollama"
            logger.info("Installing Ollama CLI...")
            await _install_ollama_cli()
            binary = find_ollama_bin()

        if not running:
            if not binary:
                raise RuntimeError("Ollama binary not found after installation.")
            _state["step"] = "starting_server"
            logger.info("Starting local Ollama server...")
            await _ensure_server_running(binary)

        _, models, selected = await _fetch_running_models(timeout=2.0)
        if not selected or (model not in models and not any(m.startswith(model.split(":")[0]) for m in models)):
            _state["step"] = "pulling_model"
            logger.info(f"Pulling recommended Ollama model '{model}'...")
            await _pull_model(model)

        _state["step"] = "ready"
        logger.info("Local Ollama setup completed successfully.")
    except Exception as e:
        logger.warning(f"Ollama setup failed: {e}")
        _state["error"] = str(e)
        _state["step"] = "failed"
    finally:
        _state["installing"] = False


async def start_ollama_setup(model: str = RECOMMENDED_LOCAL_MODEL) -> Dict[str, Any]:
    """Trigger background Ollama installation + server start + model pull and return current status."""
    global _setup_task
    if not _state["installing"]:
        _state["installing"] = True
        _state["step"] = "starting"
        _state["error"] = None
        _setup_task = asyncio.create_task(_run_setup_workflow(model or RECOMMENDED_LOCAL_MODEL))
    return await get_ollama_status()
