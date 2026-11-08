"""Cross-site request guard (CSRF protection for the local API).

CORS only stops a foreign website from *reading* a response; it does not stop the browser
from *sending* "simple" requests (a POST with no body, a form, a multipart upload) to
http://127.0.0.1:8000. Any page the user visits while the studio is running could otherwise
start an Ollama install, upload junk files, cancel jobs, or listen on a job WebSocket.

Rule: a state-changing HTTP request (anything but GET/HEAD/OPTIONS) and every WebSocket
handshake must come from a trusted origin:

* ``Origin`` header present -> it must be in ``cors_origins`` or be the same origin as the
  request's ``Host`` (the Host itself is already validated by ``TrustedHostMiddleware``).
  ``Origin: null`` (sandboxed iframes, file:// pages, some redirects) is rejected.
* ``Origin`` absent -> browsers always send it on cross-origin POST/WebSocket, so this is a
  local tool (the desktop launcher, curl, tests). It is allowed unless ``Sec-Fetch-Site``
  says the browser made it on behalf of another site.
"""
import logging
from typing import Iterable, Optional
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

logger = logging.getLogger("subforge")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Sec-Fetch-Site values that mean "not initiated by another website"
_TRUSTED_FETCH_SITES = frozenset({"same-origin", "none"})


def _normalize_origin(value: str) -> str:
    return value.strip().rstrip("/").lower()


def is_trusted_request(origin: Optional[str], host: Optional[str], fetch_site: Optional[str],
                       allowed_origins: Iterable[str]) -> bool:
    """Decide whether a request may change state. Pure function, unit-tested."""
    if origin is not None:
        origin = _normalize_origin(origin)
        if not origin or origin == "null":
            return False
        if origin in {_normalize_origin(o) for o in allowed_origins}:
            return True
        parts = urlsplit(origin)
        return bool(host) and parts.scheme in ("http", "https") and parts.netloc == host.strip().lower()
    if fetch_site is not None:
        return fetch_site.strip().lower() in _TRUSTED_FETCH_SITES
    return True


class CrossSiteRequestGuard:
    """Pure ASGI middleware applying :func:`is_trusted_request` to HTTP and WebSocket traffic."""

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed_origins = [_normalize_origin(o) for o in allowed_origins]

    def _trusted(self, scope: Scope) -> bool:
        headers = Headers(scope=scope)
        return is_trusted_request(
            headers.get("origin"), headers.get("host"), headers.get("sec-fetch-site"), self.allowed_origins
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type == "http" and scope["method"] not in SAFE_METHODS and not self._trusted(scope):
            self._log_blocked(scope)
            response = JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
            await response(scope, receive, send)
            return
        if scope_type == "websocket" and not self._trusted(scope):
            self._log_blocked(scope)
            await WebSocketClose(code=1008)(scope, receive, send)  # 1008 = policy violation
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _log_blocked(scope: Scope) -> None:
        headers = Headers(scope=scope)
        logger.warning(
            "Blocked cross-site %s %s (origin=%r, sec-fetch-site=%r)",
            scope.get("method", "WEBSOCKET"), scope.get("path"),
            headers.get("origin"), headers.get("sec-fetch-site"),
        )
