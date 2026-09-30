"""Protection for a local, login-free API that stores market-data API keys.

* Host allow-list: blocks DNS-rebinding attacks, where a malicious website re-points its
  domain at 127.0.0.1 so the victim's browser talks to this server as "same origin".
* Origin check: state-changing requests and WebSocket handshakes coming from another
  website are refused (a browser always sends ``Origin`` for those), which stops cross-site
  request forgery. Tools such as curl or the CLI send no ``Origin`` and are unaffected.

Extra host names (e.g. when running on a server) can be allowed with the
``TRADES_ALLOWED_HOSTS`` environment variable (comma separated); on Render the service's own
hostname is allowed automatically. Allowing any outside host name also requires a password
(see ``auth.py``).
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", "testserver"})
DEV_ORIGINS = frozenset({"http://localhost:5173", "http://127.0.0.1:5173"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
HEALTH_PATH = "/healthz"  # for a host's health checks, which may use any host name


def allowed_hosts() -> frozenset[str]:
    extra = os.environ.get("TRADES_ALLOWED_HOSTS", "").split(",")
    extra.append(os.environ.get("RENDER_EXTERNAL_HOSTNAME", ""))  # set by Render on its services
    return LOCAL_HOSTS | {h.strip().lower() for h in extra if h.strip()}


def _hostname(hostport: str) -> str:
    hostport = hostport.strip().lower()
    if hostport.startswith("["):  # [::1]:8000
        end = hostport.find("]")
        return hostport[: end + 1] if end != -1 else hostport
    return hostport.rsplit(":", 1)[0] if hostport.count(":") == 1 else hostport


class LocalGuard:
    def __init__(self, app, hosts: frozenset[str] | None = None, dev_origins: frozenset[str] = DEV_ORIGINS):
        self.app = app
        self.hosts = hosts or allowed_hosts()
        self.dev_origins = dev_origins

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or scope.get("path") == HEALTH_PATH:
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        if _hostname(host) not in self.hosts:
            return await self._reject(
                scope, receive, send, 400, "Host not allowed. Set TRADES_ALLOWED_HOSTS to add it."
            )
        origin = headers.get("origin")
        needs_check = scope["type"] == "websocket" or scope.get("method", "GET") not in SAFE_METHODS
        if origin and needs_check and not self._origin_ok(origin, host):
            return await self._reject(scope, receive, send, 403, "Cross-site request refused.")
        return await self.app(scope, receive, send)

    def _origin_ok(self, origin: str, host: str) -> bool:
        if origin in self.dev_origins:
            return True
        parts = urlsplit(origin)
        return bool(parts.netloc) and parts.netloc.lower() == host.strip().lower()

    @staticmethod
    async def _reject(scope, receive, send, status: int, message: str) -> None:
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": message}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
