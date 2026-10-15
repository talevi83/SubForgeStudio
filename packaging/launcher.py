"""
SubForge Studio desktop launcher - the entry point of the packaged "SubForge Studio.exe".

Starts the FastAPI backend inside this process and shows the studio in its own desktop window
(pywebview on Microsoft Edge WebView2, which ships with Windows 10/11), with an icon in the
system tray. Without WebView2 - or with --browser - the studio opens in the default browser
instead, and the tray icon is the way back to it.

User data (.env with the API key, storage/ with projects, settings and logs, the window's
size and the studio's own preferences) is kept outside the program folder, so rebuilding or
replacing the program never touches it:
  1. %SUBFORGE_HOME%, if set
  2. a "data" folder next to the .exe (portable mode - create the folder to opt in)
  3. %LOCALAPPDATA%\\SubForge Studio

Also runs from source:  python packaging/launcher.py [--browser] [--no-tray] [--no-browser]
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
from typing import Callable, Optional

APP_NAME = "SubForge Studio"
FROZEN = getattr(sys, "frozen", False)
WINDOWS = sys.platform == "win32"
STARTUP_TIMEOUT = 180  # seconds; the first start preloads the CUDA libraries, which can be slow
ACTIVE_STATUSES = {"QUEUED", "PROBING", "EXTRACTING_AUDIO", "TRANSCRIBING",
                   "SCANNING_ONSCREEN", "REFINING", "TRANSLATING", "SYNCING"}
WINDOW_SIZE = (1440, 900)
MIN_WINDOW_SIZE = (1024, 640)
IDYES, IDNO = 6, 7

# Window mode: brings the window to the front. A second launch of the exe calls it through the
# local server (POST /api/desktop/show) instead of starting another copy.
SHOW_WINDOW: Optional[Callable[[], None]] = None

LOADING_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#0b0b10;color:#e4e4e7;font-family:Segoe UI,system-ui,sans-serif}
body{display:flex;align-items:center;justify-content:center}
.box{text-align:center}.spin{width:34px;height:34px;margin:0 auto 18px;border:3px solid #27272a;
border-top-color:#6366f1;border-radius:50%;animation:s 0.9s linear infinite}@keyframes s{to{transform:rotate(360deg)}}
h1{font-size:18px;font-weight:600;margin:0 0 6px}p{font-size:12px;color:#a1a1aa;margin:2px 0}
</style></head><body><div class="box"><div class="spin"></div><h1>SubForge Studio</h1>
<p>Starting the studio&hellip;</p><p dir="rtl">&#1502;&#1508;&#1506;&#1497;&#1500; &#1488;&#1514; &#1492;&#1505;&#1496;&#1493;&#1491;&#1497;&#1493;&hellip;</p>
</div></body></html>"""


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
    # pythonw.exe (run.bat) has no console either
    if not (WINDOWS and (FROZEN or Path(sys.executable).name.lower() == "pythonw.exe")):
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
    """Native message box on Windows (returns IDYES / IDNO / 2 for cancel); stderr elsewhere."""
    if WINDOWS:
        import ctypes
        flags = {"error": 0x10, "info": 0x40, "yesno": 0x04 | 0x30, "yesnocancel": 0x03 | 0x30}[kind]
        return ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, flags | 0x40000)  # topmost
    print(f"[{kind}] {text}", file=sys.stderr)
    return IDYES


def open_path(path: Path) -> None:
    if not path.exists():
        message(f"Not found yet:\n{path}", "info")
        return
    if WINDOWS:
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


_no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_json(url: str, timeout: float = 2.0, post: bool = False):
    req = urllib.request.Request(url, data=b"" if post else None, method="POST" if post else "GET")
    with _no_proxy.open(req, timeout=timeout) as response:
        return json.loads(response.read())


def is_our_server(url: str) -> bool:
    try:
        info = get_json(url + "/api/system/info")
        return isinstance(info, dict) and "whisper_models" in info
    except Exception:
        return False


def show_running_window(url: str) -> bool:
    """Ask the copy that is already running to bring its window to the front."""
    try:
        return bool(get_json(url + "/api/desktop/show", timeout=3, post=True).get("shown"))
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


def confirm_stop(url: str, action: str) -> bool:
    busy = active_job_count(url)
    if not busy:
        return True
    return message(f"{busy} project(s) are still running. {action} anyway?\n"
                   "They can be resumed later.", "yesno") == IDYES


def spawn_again(extra_args: list) -> None:
    args = [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]
    subprocess.Popen(args + extra_args, cwd=str(program_dir()))


# ----------------------------------------------------------------------------- server

def _add_desktop_route(app) -> None:  # type: ignore[no-untyped-def]
    """POST /api/desktop/show - lets a second launch bring this window to the front."""
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def show(_request):  # type: ignore[no-untyped-def]
        callback = SHOW_WINDOW
        if callback is not None:
            threading.Thread(target=callback, daemon=True).start()
        return JSONResponse({"shown": callback is not None})

    # Before the static frontend mounted at "/", which would otherwise answer first
    app.router.routes.insert(0, Route("/api/desktop/show", show, methods=["POST"]))


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

            _add_desktop_route(app)
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

def make_tray(url: str, home: Path, env_file: Path, open_label: str, on_open: Callable[[], None],
              on_quit: Callable, on_restart: Callable, with_browser_item: bool):
    """The tray icon and its menu (shared by window and browser mode)."""
    import pystray
    from PIL import Image

    items = [pystray.MenuItem(open_label, lambda: on_open(), default=True)]
    if with_browser_item:
        items.append(pystray.MenuItem("Open in browser", lambda: webbrowser.open(url)))
    items += [
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open data folder", lambda: open_path(home)),
        pystray.MenuItem("Edit settings (.env)", lambda: open_path(env_file)),
        pystray.MenuItem("Server log", lambda: open_path(home / "storage" / "logs" / "server.log")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart server", on_restart),
        pystray.MenuItem("Quit", on_quit),
    ]
    return pystray.Icon("SubForgeStudio", Image.open(resource("packaging", "icon.ico")),
                        f"{APP_NAME} - starting...", pystray.Menu(*items))


def run_tray(server: ServerThread, url: str, home: Path, env_file: Path, open_browser: bool) -> None:
    """Browser mode: the tray icon runs on the main thread; the studio opens in the browser."""

    def quit_app(icon) -> None:  # type: ignore[no-untyped-def]
        if not confirm_stop(url, "Quit"):
            return
        icon.visible = False
        server.stop()
        icon.stop()

    def restart(icon) -> None:  # type: ignore[no-untyped-def]
        if not confirm_stop(url, "Restart"):
            return
        icon.visible = False
        server.stop()
        spawn_again(["--browser", "--no-browser"])
        icon.stop()

    icon = make_tray(url, home, env_file, "Open SubForge Studio", lambda: webbrowser.open(url),
                     quit_app, restart, with_browser_item=False)

    def setup(icon) -> None:  # type: ignore[no-untyped-def]
        icon.visible = True
        if server.wait_ready():
            icon.title = f"{APP_NAME} - {url}"
            if open_browser:
                webbrowser.open(url)
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


# ----------------------------------------------------------------------------- desktop window

def window_problem() -> Optional[str]:
    """None when the studio can open in its own window; otherwise the reason it can't."""
    try:
        import webview  # noqa: F401
    except Exception as e:
        return f"pywebview is not available ({e})"
    if WINDOWS:
        try:
            from webview.platforms import winforms
        except Exception as e:
            return f"the Windows web view could not start ({e})"
        # Without WebView2 pywebview falls back to Internet Explorer, which can't run the studio
        if getattr(winforms, "renderer", "") != "edgechromium":
            return "Microsoft Edge WebView2 Runtime is not installed"
    return None


class DesktopApi:
    """Called from the page as window.pywebview.api.* (pywebview's JavaScript bridge)."""

    def __init__(self) -> None:
        self._window = None
        self._fullscreen = False

    def set_fullscreen(self, on: bool) -> bool:
        """The video player went (or left) fullscreen: make the whole window follow."""
        on = bool(on)
        if self._window is not None and on != self._fullscreen:
            self._fullscreen = on
            self._window.toggle_fullscreen()
        return self._fullscreen


def _load_window_size(home: Path):
    try:
        data = json.loads((home / "window.json").read_text())
        w, h = int(data["width"]), int(data["height"])
        if w >= MIN_WINDOW_SIZE[0] and h >= MIN_WINDOW_SIZE[1]:
            return w, h
    except Exception:
        pass
    return WINDOW_SIZE


def _save_window_size(home: Path, window) -> None:  # type: ignore[no-untyped-def]
    try:
        w, h = int(window.width), int(window.height)
        if w >= MIN_WINDOW_SIZE[0] and h >= MIN_WINDOW_SIZE[1]:
            (home / "window.json").write_text(json.dumps({"width": w, "height": h}))
    except Exception:
        pass


def run_window(server: ServerThread, url: str, home: Path, env_file: Path) -> None:
    """Window mode: the studio in its own window (main thread); the tray icon runs alongside."""
    global SHOW_WINDOW
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True                 # exports ask where to save
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # e.g. the pricing page
    api = DesktopApi()
    width, height = _load_window_size(home)
    window = webview.create_window(
        APP_NAME, html=LOADING_HTML, width=width, height=height, min_size=MIN_WINDOW_SIZE,
        background_color="#0B0B10", js_api=api, text_select=True,
    )
    api._window = window
    state = {"leaving": False, "restart": False}

    def show_window() -> None:
        try:
            window.show()
            window.restore()
            window.on_top = True   # pull it in front of other windows, then let it behave normally
            window.on_top = False
        except Exception:
            pass

    def leave(restart: bool) -> None:
        if not confirm_stop(url, "Restart" if restart else "Quit"):
            return
        state.update(leaving=True, restart=restart)
        _save_window_size(home, window)
        window.destroy()

    icon = make_tray(url, home, env_file, "Open SubForge Studio", show_window,
                     lambda: leave(False), lambda: leave(True), with_browser_item=True)

    def on_closing() -> bool:
        if state["leaving"]:
            return True
        busy = active_job_count(url)
        if busy:
            answer = message(
                f"{busy} project(s) are still running.\n\n"
                "Yes - quit now (they can be resumed later)\n"
                "No - keep working in the background (tray icon)\n"
                "Cancel - go back", "yesnocancel")
            if answer == IDNO:
                window.hide()
                try:
                    icon.notify("Still working in the background. Click the tray icon to open the window.", APP_NAME)
                except Exception:
                    pass
                return False
            if answer != IDYES:
                return False
        _save_window_size(home, window)
        state["leaving"] = True
        return True

    window.events.closing += on_closing

    def boot() -> None:
        if server.wait_ready():
            icon.title = f"{APP_NAME} - {url}"
            window.load_url(url)
        else:
            message(failure_text(server, home))
            state["leaving"] = True
            window.destroy()

    icon.visible = True
    icon.run_detached()  # Windows: the tray gets its own thread and message loop
    SHOW_WINDOW = show_window
    try:
        webview.start(boot, private_mode=False, storage_path=str(home / "webview"),
                      icon=str(resource("packaging", "icon.ico")))
    finally:
        SHOW_WINDOW = None
        try:
            icon.visible = False
            icon.stop()
        except Exception:
            pass
    server.stop()
    if state["restart"]:
        spawn_again([])


# ----------------------------------------------------------------------------- main

def main() -> int:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--browser", action="store_true", help="open the studio in the browser, not in its own window")
    parser.add_argument("--no-tray", action="store_true", help="run in the foreground without a tray icon or window")
    parser.add_argument("--no-browser", action="store_true", help="browser mode: do not open the browser at start")
    args, _ = parser.parse_known_args()
    use_browser = args.browser or os.environ.get("SUBFORGE_UI", "").lower() == "browser"

    home = data_dir()
    home.mkdir(parents=True, exist_ok=True)
    redirect_stdio(home)
    hide_child_consoles()
    env_file = prepare_environment(home)

    from app.core.config import settings

    host, port = settings.host, settings.port
    browse_host = "localhost" if host in ("0.0.0.0", "127.0.0.1", "::", "[::]") else host
    url = f"http://{browse_host}:{port}"

    if is_our_server(url):  # already running: bring up its window
        if show_running_window(url):
            return 0
        # A copy is running but has no window to show: it runs in the browser, in a console
        # (the old run.bat), or it is an older version. Say so instead of silently opening a tab.
        print(f"{APP_NAME} is already running at {url} without a window.", file=sys.stderr, flush=True)
        if args.no_browser:
            return 0
        if not use_browser and not args.no_tray:
            answer = message(
                f"{APP_NAME} is already running without its own window (in the browser, in a console "
                "window, or as an older version).\n\n"
                "Close that copy first - quit it from its tray icon, or close its black console window - "
                "then start the program again to get the app window.\n\n"
                "Yes - open the running copy in the browser now\n"
                "No - do nothing", "yesno")
            if answer != IDYES:
                return 0
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

    if not use_browser:
        problem = window_problem()
        if problem is None:
            try:
                run_window(server, url, home, env_file)
                exit_now(0)
            except Exception:
                print(f"Could not open the studio window, using the browser instead:\n{traceback.format_exc()}",
                      file=sys.stderr, flush=True)
        else:
            print(f"Opening the studio in the browser: {problem}", file=sys.stderr, flush=True)

    run_tray(server, url, home, env_file, open_browser=not args.no_browser)
    exit_now(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
