"""
SubForge Studio desktop launcher - the entry point of the packaged "SubForge Studio.exe".

Starts the FastAPI backend inside this process, shows an icon in the Windows system tray and
opens the studio in the default browser. Right-click the tray icon for the menu; Quit stops
the server.

User data (.env with the API key, storage/ with projects, models settings and logs) is kept
outside the program folder, so rebuilding or replacing the program never touches it:
  1. %SUBFORGE_HOME%, if set
  2. a "data" folder next to the .exe (portable mode - create the folder to opt in)
  3. %LOCALAPPDATA%\\SubForge Studio

Also runs from source:  python packaging/launcher.py [--no-tray] [--no-browser]
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

APP_NAME = "SubForge Studio"
FROZEN = getattr(sys, "frozen", False)
WINDOWS = sys.platform == "win32"
STARTUP_TIMEOUT = 180  # seconds; the first start preloads the CUDA libraries, which can be slow
ACTIVE_STATUSES = {"QUEUED", "PROBING", "EXTRACTING_AUDIO", "TRANSCRIBING",
                   "SCANNING_ONSCREEN", "REFINING", "TRANSLATING"}


# ----------------------------------------------------------------------------- paths

def bundle_dir() -> Path:
    """Root of the bundled resources (PyInstaller's _internal folder, or the repo from source)."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def resource(*parts: str) -> Path:
    return bundle_dir().joinpath(*parts)


def program_dir() -> Path:
    return Path(sys.executable).resolve().parent if FROZEN else bundle_dir()


def data_dir() -> Path:
    env = os.environ.get("SUBFORGE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    portable = program_dir() / "data"
    if portable.is_dir():
        return portable
    if WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


# ----------------------------------------------------------------------------- process setup

def redirect_stdio(home: Path) -> None:
    """A windowed .exe has no console: send print/tracebacks/progress bars to launcher.log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log = home / "launcher.log"
    try:
        if log.exists() and log.stat().st_size > 5 * 1024 * 1024:
            log.unlink()
    except OSError:
        pass
    stream = open(log, "a", encoding="utf-8", buffering=1, errors="replace")
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def hide_child_consoles() -> None:
    """Without this every ffmpeg/ffprobe/nvidia-smi call would flash a black console window."""
    if not (WINDOWS and FROZEN):
        return
    create_no_window = 0x08000000
    original_init = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["creationflags"] = (kwargs.get("creationflags") or 0) | create_no_window
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = __init__  # type: ignore[method-assign]


def prepare_environment(home: Path) -> Path:
    """Create the data folder, a first .env, and point the backend at the bundled files."""
    home.mkdir(parents=True, exist_ok=True)
    env_file = home / ".env"
    if not env_file.exists():
        example = resource("backend", ".env.example")
        if example.exists():
            shutil.copyfile(example, env_file)
    # The backend reads ./.env and keeps ./storage relative to the working directory
    os.chdir(home)
    os.environ.setdefault("SUBFORGE_FRONTEND_DIST", str(resource("frontend", "dist")))
    ffmpeg_dir = resource("ffmpeg")
    if ffmpeg_dir.is_dir():  # bundled ffmpeg/ffprobe win over whatever is on PATH
        os.environ["PATH"] = str(ffmpeg_dir) + os.pathsep + os.environ.get("PATH", "")
    if not FROZEN:
        sys.path.insert(0, str(resource("backend")))
    return env_file


# ----------------------------------------------------------------------------- helpers

def message(text: str, kind: str = "error") -> int:
    """Native message box on Windows (returns 6 for "Yes"); stderr elsewhere."""
    if WINDOWS:
        import ctypes
        flags = {"error": 0x10, "info": 0x40, "yesno": 0x04 | 0x30}[kind] | 0x40000  # topmost
        return ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, flags)
    print(f"[{kind}] {text}", file=sys.stderr)
    return 6


def open_path(path: Path) -> None:
    if not path.exists():
        message(f"Not found yet:\n{path}", "info")
        return
    if WINDOWS:
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


_no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_json(url: str, timeout: float = 2.0):
    with _no_proxy.open(url, timeout=timeout) as response:
        return json.loads(response.read())


def is_our_server(url: str) -> bool:
    try:
        info = get_json(url + "/api/system/info")
        return isinstance(info, dict) and "whisper_models" in info
    except Exception:
        return False


def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def active_job_count(url: str) -> int:
    try:
        return sum(1 for job in get_json(url + "/api/jobs", timeout=3) if job.get("status") in ACTIVE_STATUSES)
    except Exception:
        return 0


# ----------------------------------------------------------------------------- server

class ServerThread(threading.Thread):
    def __init__(self, host: str, port: int) -> None:
        super().__init__(name="uvicorn", daemon=True)
        self.host, self.port = host, port
        self.server = None
        self.error: Optional[str] = None

    def run(self) -> None:
        try:
            import uvicorn
            from app.main import app

            config = uvicorn.Config(app, host=self.host, port=self.port, log_config=None, access_log=False)
            self.server = uvicorn.Server(config)
            self.server.run()
            if not self.server.started and self.error is None:
                self.error = f"The server did not start (is port {self.port} free?)."
        except BaseException:  # SystemExit from uvicorn included
            self.error = traceback.format_exc()

    def wait_ready(self, timeout: float = STARTUP_TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server is not None and self.server.started:
                return True
            if not self.is_alive():
                return False
            time.sleep(0.25)
        return False

    def stop(self, timeout: float = 20.0) -> None:
        if self.server is not None:
            self.server.should_exit = True
        self.join(timeout)


def failure_text(server: ServerThread, home: Path) -> str:
    detail = (server.error or "Startup timed out.").strip().splitlines()
    return ("SubForge Studio could not start.\n\n" + "\n".join(detail[-6:]) +
            f"\n\nLogs: {home / 'storage' / 'logs' / 'server.log'}\n{home / 'launcher.log'}")


def exit_now(code: int = 0) -> None:
    """Exit even if a transcription worker thread is still busy (jobs resume as interrupted)."""
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)


# ----------------------------------------------------------------------------- tray

def run_tray(server: ServerThread, url: str, home: Path, env_file: Path, open_browser: bool) -> None:
    import pystray
    from PIL import Image

    image = Image.open(resource("packaging", "icon.ico"))

    def open_studio() -> None:
        webbrowser.open(url)

    def confirm_stop(action: str) -> bool:
        busy = active_job_count(url)
        if not busy:
            return True
        return message(f"{busy} project(s) are still running. {action} anyway?\n"
                       "They can be resumed later.", "yesno") == 6

    def quit_app(icon) -> None:  # type: ignore[no-untyped-def]
        if not confirm_stop("Quit"):
            return
        icon.visible = False
        server.stop()
        icon.stop()

    def restart(icon) -> None:  # type: ignore[no-untyped-def]
        if not confirm_stop("Restart"):
            return
        icon.visible = False
        server.stop()
        args = [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]
        subprocess.Popen(args + ["--no-browser"], cwd=str(program_dir()))
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open SubForge Studio", open_studio, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open data folder", lambda: open_path(home)),
        pystray.MenuItem("Edit settings (.env)", lambda: open_path(env_file)),
        pystray.MenuItem("Server log", lambda: open_path(home / "storage" / "logs" / "server.log")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart server", restart),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("SubForgeStudio", image, f"{APP_NAME} - starting...", menu)

    def setup(icon) -> None:  # type: ignore[no-untyped-def]
        icon.visible = True
        if server.wait_ready():
            icon.title = f"{APP_NAME} - {url}"
            if open_browser:
                open_studio()
            else:
                try:
                    icon.notify("Running in the tray. Click the icon to open the studio.", APP_NAME)
                except Exception:
                    pass
        else:
            message(failure_text(server, home))
            icon.visible = False
            icon.stop()

    icon.run(setup=setup)


# ----------------------------------------------------------------------------- main

def main() -> int:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--no-tray", action="store_true", help="run in the foreground without a tray icon")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args, _ = parser.parse_known_args()

    home = data_dir()
    home.mkdir(parents=True, exist_ok=True)
    redirect_stdio(home)
    hide_child_consoles()
    env_file = prepare_environment(home)

    from app.core.config import settings

    host, port = settings.host, settings.port
    browse_host = "localhost" if host in ("0.0.0.0", "127.0.0.1", "::", "[::]") else host
    url = f"http://{browse_host}:{port}"

    if is_our_server(url):  # already running: just bring up the studio
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if port_busy(port):
        message(f"Port {port} is already used by another program.\n"
                f"Close it, or set PORT=... in:\n{env_file}")
        return 1

    server = ServerThread(host, port)
    server.start()

    if args.no_tray:
        if not server.wait_ready():
            message(failure_text(server, home))
            exit_now(1)
        print(f"{APP_NAME} is running at {url} (data: {home}). Press Ctrl+C to stop.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            while server.is_alive():
                server.join(0.5)
        except KeyboardInterrupt:
            server.stop()
        exit_now(0)

    run_tray(server, url, home, env_file, open_browser=not args.no_browser)
    exit_now(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
