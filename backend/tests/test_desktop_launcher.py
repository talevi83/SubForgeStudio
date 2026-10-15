"""The desktop launcher's window mode (packaging/launcher.py) with pywebview and the tray faked."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "packaging" / "launcher.py"


@pytest.fixture()
def launcher(monkeypatch):
    spec = importlib.util.spec_from_file_location("subforge_launcher", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeEvent(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self

    def fire(self):
        return [fn() for fn in self]


class FakeWindow:
    def __init__(self, **kw):
        self.kw, self.calls = kw, []
        self.width, self.height = 1500, 950
        self.events = types.SimpleNamespace(closing=FakeEvent())
        self.on_top = False

    def __getattr__(self, name):  # show / hide / restore / destroy / load_url / toggle_fullscreen
        if name in ("show", "hide", "restore", "destroy", "load_url", "toggle_fullscreen"):
            return lambda *a: self.calls.append((name, *a))
        raise AttributeError(name)

    def user_closes(self):
        """What WinForms does when the X is clicked: closing handlers decide."""
        allowed = all(v is not False for v in self.events.closing.fire())
        self.calls.append(("closed",) if allowed else ("close-cancelled",))
        return allowed


class FakeWebview(types.ModuleType):
    def __init__(self, after_start=None):
        super().__init__("webview")
        self.settings = {"ALLOW_DOWNLOADS": False, "OPEN_EXTERNAL_LINKS_IN_BROWSER": False}
        self.window = None
        self.start_kwargs = None
        self.after_start = after_start

    def create_window(self, title, **kw):
        self.window = FakeWindow(title=title, **kw)
        return self.window

    def start(self, func=None, **kw):
        self.start_kwargs = kw
        func()                                  # pywebview runs it once the GUI loop is up
        if self.after_start:
            self.after_start(self.window)


class FakeIcon:
    def __init__(self):
        self.calls, self.visible, self.title = [], False, ""

    def run_detached(self):
        self.calls.append("run_detached")

    def stop(self):
        self.calls.append("stop")

    def notify(self, text, title=None):
        self.calls.append(("notify", text))


class FakeServer:
    def __init__(self, ready=True):
        self.ready, self.stopped, self.error = ready, False, "boom"

    def wait_ready(self, timeout=None):
        return self.ready

    def stop(self, timeout=None):
        self.stopped = True


def _setup(monkeypatch, launcher, tmp_path, after_start=None, busy=0, answers=()):
    web = FakeWebview(after_start)
    monkeypatch.setitem(sys.modules, "webview", web)
    icon = FakeIcon()
    menu = {}

    def make_tray(url, home, env_file, open_label, on_open, on_quit, on_restart, with_browser_item):
        menu.update(open=on_open, quit=on_quit, restart=on_restart, browser=with_browser_item)
        return icon

    asked = list(answers)
    messages = []
    monkeypatch.setattr(launcher, "make_tray", make_tray)
    monkeypatch.setattr(launcher, "active_job_count", lambda url: busy)
    monkeypatch.setattr(launcher, "message", lambda text, kind="error": (messages.append((kind, text)),
                                                                          asked.pop(0) if asked else launcher.IDYES)[1])
    spawned = []
    monkeypatch.setattr(launcher, "spawn_again", lambda args: spawned.append(args))
    return web, icon, menu, messages, spawned


def test_window_opens_the_studio_and_closes_cleanly(launcher, monkeypatch, tmp_path):
    web, icon, menu, messages, _ = _setup(monkeypatch, launcher, tmp_path, after_start=lambda w: w.user_closes())
    server = FakeServer()
    launcher.run_window(server, "http://localhost:8000", tmp_path, tmp_path / ".env")
    w = web.window
    assert w.kw["html"] == launcher.LOADING_HTML                      # loading screen first...
    assert ("load_url", "http://localhost:8000") in w.calls           # ...then the studio
    assert w.kw["min_size"] == launcher.MIN_WINDOW_SIZE and w.kw["js_api"] is not None
    assert web.settings["ALLOW_DOWNLOADS"] is True                    # exports open "Save as"
    assert web.start_kwargs["private_mode"] is False                  # language/preferences persist
    assert web.start_kwargs["storage_path"] == str(tmp_path / "webview")
    assert web.start_kwargs["icon"].endswith("icon.ico")
    assert ("closed",) in w.calls and server.stopped
    assert icon.calls[0] == "run_detached" and "stop" in icon.calls and menu["browser"] is True
    assert json.loads((tmp_path / "window.json").read_text()) == {"width": 1500, "height": 950}
    assert launcher.SHOW_WINDOW is None and not messages


def test_window_size_is_restored(launcher, monkeypatch, tmp_path):
    (tmp_path / "window.json").write_text(json.dumps({"width": 1280, "height": 760}))
    web, *_ = _setup(monkeypatch, launcher, tmp_path, after_start=lambda w: w.user_closes())
    launcher.run_window(FakeServer(), "http://x", tmp_path, tmp_path / ".env")
    assert (web.window.kw["width"], web.window.kw["height"]) == (1280, 760)


def test_closing_while_a_job_runs_can_keep_it_in_the_tray(launcher, monkeypatch, tmp_path):
    def user(w):
        assert w.user_closes() is False                   # "No": keep working in the background
        assert ("hide",) in w.calls
        menu["quit"]()                                    # later: Quit from the tray (confirmed)

    web, icon, menu, messages, _ = _setup(monkeypatch, launcher, tmp_path, after_start=user, busy=1,
                                          answers=[launcher.IDNO, launcher.IDYES])
    server = FakeServer()
    launcher.run_window(server, "http://x", tmp_path, tmp_path / ".env")
    assert messages[0][0] == "yesnocancel" and "still running" in messages[0][1]
    assert any(c[0] == "notify" for c in icon.calls if isinstance(c, tuple))
    assert ("destroy",) in web.window.calls and server.stopped


def test_cancel_keeps_the_window_open(launcher, monkeypatch, tmp_path):
    def user(w):
        assert w.user_closes() is False
        assert ("hide",) not in w.calls
        menu["quit"]()

    web, icon, menu, *_ = _setup(monkeypatch, launcher, tmp_path, after_start=user, busy=2, answers=[2, launcher.IDYES])
    launcher.run_window(FakeServer(), "http://x", tmp_path, tmp_path / ".env")
    assert ("destroy",) in web.window.calls


def test_restart_from_the_tray_starts_a_new_copy(launcher, monkeypatch, tmp_path):
    web, icon, menu, messages, spawned = _setup(monkeypatch, launcher, tmp_path, after_start=lambda w: menu["restart"]())
    server = FakeServer()
    launcher.run_window(server, "http://x", tmp_path, tmp_path / ".env")
    assert server.stopped and spawned == [[]]


def test_second_launch_brings_the_window_forward(launcher, monkeypatch, tmp_path):
    seen = {}

    def user(w):
        seen["callback"] = launcher.SHOW_WINDOW
        launcher.SHOW_WINDOW()
        assert ("show",) in w.calls and ("restore",) in w.calls
        w.user_closes()

    _setup(monkeypatch, launcher, tmp_path, after_start=user)
    launcher.run_window(FakeServer(), "http://x", tmp_path, tmp_path / ".env")
    assert seen["callback"] is not None


def test_failed_server_start_shows_the_error_and_closes(launcher, monkeypatch, tmp_path):
    web, icon, menu, messages, _ = _setup(monkeypatch, launcher, tmp_path)
    server = FakeServer(ready=False)
    launcher.run_window(server, "http://x", tmp_path, tmp_path / ".env")
    assert messages and "could not start" in messages[0][1]
    assert ("destroy",) in web.window.calls
    assert not any(c[0] == "load_url" for c in web.window.calls)


def test_fullscreen_bridge_toggles_only_on_change(launcher):
    api = launcher.DesktopApi()
    window = FakeWindow()
    api._window = window
    assert api.set_fullscreen(True) is True
    assert api.set_fullscreen(True) is True          # already fullscreen: nothing to do
    assert api.set_fullscreen(False) is False
    assert [c for c in window.calls if c[0] == "toggle_fullscreen"] == [("toggle_fullscreen",)] * 2


def test_show_route_answers_before_the_static_frontend(launcher):
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.mount("/", StaticFiles(directory=str(Path(__file__).parent)), name="static")  # like the real app
    launcher._add_desktop_route(app)
    client = TestClient(app)
    assert client.post("/api/desktop/show").json() == {"shown": False}
    called = []
    launcher.SHOW_WINDOW = lambda: called.append(True)
    try:
        assert client.post("/api/desktop/show").json() == {"shown": True}
    finally:
        launcher.SHOW_WINDOW = None
    import time
    time.sleep(0.1)
    assert called == [True]


def test_window_problem_without_pywebview(launcher, monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)  # import fails
    assert "pywebview" in launcher.window_problem()
