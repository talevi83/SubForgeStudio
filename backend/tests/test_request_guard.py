"""CSRF guard: state-changing requests and WebSockets must come from a trusted origin."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.request_guard import is_trusted_request
from app.main import app

ALLOWED = ["http://localhost:5173", "http://127.0.0.1:8000"]
EVIL = "https://evil.example"


@pytest.fixture
def client(monkeypatch):
    """A client whose Ollama setup endpoint is faked, so a request that gets through installs nothing."""
    calls = []

    async def fake_setup(model):
        calls.append(model)
        return {"started": True}

    import app.core.ollama_setup as ollama_setup

    monkeypatch.setattr(ollama_setup, "start_ollama_setup", fake_setup)
    with TestClient(app) as c:
        c.setup_calls = calls
        yield c


# ----------------------------------------------------------------------------- pure rule

@pytest.mark.parametrize("origin, host, fetch_site, expected", [
    ("http://localhost:5173", "localhost:8000", None, True),       # Vite dev server
    ("http://127.0.0.1:8000/", "127.0.0.1:8000", None, True),      # trailing slash
    ("HTTP://LOCALHOST:5173", "localhost:8000", None, True),       # case-insensitive
    ("http://127.0.0.1:8123", "127.0.0.1:8123", None, True),       # same origin, any port
    (EVIL, "127.0.0.1:8000", None, False),
    ("http://localhost:9999", "localhost:8000", None, False),      # other local app/port
    ("null", "127.0.0.1:8000", None, False),                       # sandboxed iframe / file://
    ("", "127.0.0.1:8000", None, False),
    ("ftp://127.0.0.1:8000", "127.0.0.1:8000", None, False),
    (None, "127.0.0.1:8000", None, True),                          # launcher, curl, tests
    (None, "127.0.0.1:8000", "same-origin", True),
    (None, "127.0.0.1:8000", "none", True),                        # typed in the address bar
    (None, "127.0.0.1:8000", "cross-site", False),
    (None, "127.0.0.1:8000", "same-site", False),
])
def test_is_trusted_request(origin, host, fetch_site, expected):
    assert is_trusted_request(origin, host, fetch_site, ALLOWED) is expected


# ----------------------------------------------------------------------------- HTTP

def test_cross_site_post_without_body_is_blocked(client):
    """The attack: fetch(url, {method: 'POST', mode: 'no-cors'}) from any website."""
    resp = client.post("/api/settings/ollama/setup", headers={"Origin": EVIL})
    assert resp.status_code == 403
    assert client.setup_calls == []


def test_null_origin_is_blocked(client):
    resp = client.post("/api/settings/ollama/setup", headers={"Origin": "null"})
    assert resp.status_code == 403
    assert client.setup_calls == []


def test_cross_site_fetch_metadata_without_origin_is_blocked(client):
    resp = client.post("/api/settings/ollama/setup", headers={"Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 403
    assert client.setup_calls == []


def test_cross_site_multipart_upload_is_blocked(client):
    resp = client.post(
        "/api/media/upload",
        headers={"Origin": EVIL},
        files={"file": ("junk.mkv", b"0" * 1024, "video/x-matroska")},
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_other_unsafe_methods_are_blocked(client, method):
    resp = client.request(method, "/api/jobs", headers={"Origin": EVIL})
    assert resp.status_code == 403


def test_frontend_dev_origin_is_allowed(client):
    resp = client.post("/api/settings/ollama/setup", headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 200
    assert client.setup_calls == ["qwen2.5:7b"]


def test_same_origin_is_allowed(client):
    """The built frontend / desktop window is served by this server (TestClient's host is 'testserver')."""
    resp = client.post("/api/settings/ollama/setup", headers={"Origin": "http://testserver"})
    assert resp.status_code == 200
    assert len(client.setup_calls) == 1


def test_local_tool_without_origin_is_allowed(client):
    """The desktop launcher's POST /api/desktop/show and curl send no Origin."""
    resp = client.post("/api/settings/ollama/setup")
    assert resp.status_code == 200
    assert len(client.setup_calls) == 1


def test_cross_site_get_is_not_blocked(client):
    """Reads stay protected by CORS (the response can't be read); GETs aren't blocked here."""
    resp = client.get("/api/system/info", headers={"Origin": EVIL})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_cors_preflight_still_works_for_frontend(client):
    resp = client.options(
        "/api/jobs",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


# ----------------------------------------------------------------------------- WebSocket

def test_cross_site_websocket_is_rejected(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/jobs/job_any", headers={"Origin": EVIL}):
            pass
    assert exc.value.code == 1008


def test_trusted_websocket_connects(client):
    with client.websocket_connect("/ws/jobs/job_any", headers={"Origin": "http://localhost:5173"}) as ws:
        ws.send_text("ping")


def test_websocket_without_origin_connects(client):
    with client.websocket_connect("/ws/jobs/job_any") as ws:
        ws.send_text("ping")
