"""Password protection for running Trades on a server others can reach (hosted mode).

On your own computer Trades needs no login: ``LocalGuard`` only lets this machine in. On a
server (for example a Render web service) set ``TRADES_PASSWORD`` and every page, API call and
WebSocket requires logging in once per browser. A login is a signed, HTTP-only cookie:

* the signature uses ``TRADES_SECRET_KEY`` (random per process when unset, which logs everyone
  out on a restart) and covers a hash of the password, so changing the password logs everyone
  out and a stolen cookie gives no way to test password guesses offline;
* failed logins are rate limited per client and overall.

A server that accepts outside host names without a password (``TRADES_ALLOWED_HOSTS``, or
Render's hostname) refuses to serve anything but the health check, so a deployment cannot end up
public by accident.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from trades.api.security import HEALTH_PATH, LOCAL_HOSTS, allowed_hosts

COOKIE = "trades_session"
SESSION_DAYS = 30
LOGIN_PAGE = "/login"
OPEN_PATHS = frozenset({HEALTH_PATH, LOGIN_PAGE, "/api/login", "/api/logout"})
MAX_FAILURES_PER_CLIENT = 10
MAX_FAILURES_TOTAL = 300  # across all clients: slows a distributed guesser without an easy lock-out
FAILURE_WINDOW = 15 * 60  # seconds


@dataclass
class AuthConfig:
    password: str = ""
    secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    exposed: bool = False  # outside host names are allowed

    @classmethod
    def from_env(cls) -> AuthConfig:
        key = os.environ.get("TRADES_SECRET_KEY", "")
        return cls(
            password=os.environ.get("TRADES_PASSWORD", ""),
            secret=key.encode() if key else secrets.token_bytes(32),
            exposed=bool(allowed_hosts() - LOCAL_HOSTS),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    # -- session tokens ------------------------------------------------------------------------
    def _sign(self, expires: int) -> str:
        fingerprint = hashlib.sha256(self.password.encode()).hexdigest()
        return hmac.new(self.secret, f"v1|{expires}|{fingerprint}".encode(), hashlib.sha256).hexdigest()

    def issue(self, now: float | None = None) -> str:
        expires = int((now if now is not None else time.time()) + SESSION_DAYS * 86400)
        return f"{expires}.{self._sign(expires)}"

    def valid(self, token: str | None, now: float | None = None) -> bool:
        if not token or "." not in token:
            return False
        exp, sig = token.split(".", 1)
        if not exp.isdigit() or int(exp) < (now if now is not None else time.time()):
            return False
        return hmac.compare_digest(sig, self._sign(int(exp)))

    def check_password(self, attempt: str) -> bool:
        # Compare digests, which have one length, so the timing reveals nothing about the password's.
        given = hashlib.sha256(attempt.encode()).digest()
        return bool(self.password) and hmac.compare_digest(
            given, hashlib.sha256(self.password.encode()).digest()
        )


class LoginLimiter:
    """Counts failed logins in a sliding window, per client and overall."""

    def __init__(self):
        self._lock = threading.Lock()
        self._fails: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def _trim(self, q: deque[float], now: float) -> None:
        while q and now - q[0] > FAILURE_WINDOW:
            q.popleft()

    def blocked(self, client: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            mine = self._fails.get(client, deque())
            self._trim(mine, now)
            if not mine:
                self._fails.pop(client, None)
            self._trim(self._all, now)
            return len(mine) >= MAX_FAILURES_PER_CLIENT or len(self._all) >= MAX_FAILURES_TOTAL

    def failed(self, client: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._fails.setdefault(client, deque()).append(now)
            self._all.append(now)
            if len(self._fails) > 2 * MAX_FAILURES_TOTAL:  # forget clients whose failures have aged out
                for key in [k for k, q in self._fails.items() if now - q[-1] > FAILURE_WINDOW]:
                    del self._fails[key]


def safe_next(target: str | None) -> str:
    """Where to go after logging in: a path on this site only (never another site).

    Browsers drop tabs and newlines from URLs, so "/<tab>/evil.example" would become the
    protocol-relative "//evil.example": strip them before checking.
    """
    cleaned = "".join(ch for ch in (target or "") if ch not in "\t\r\n")
    if not cleaned.startswith("/") or cleaned.startswith(("//", "/\\")):
        return "/"
    return cleaned


def _cookies(headers: dict[str, str]) -> dict[str, str]:
    jar = SimpleCookie()
    try:
        jar.load(headers.get("cookie", ""))
    except Exception:  # malformed cookie header: treat as no cookies
        return {}
    return {k: m.value for k, m in jar.items()}


def _client(request: Request) -> str:
    # Behind a proxy the last X-Forwarded-For entry is the one the proxy added.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


class PasswordGate:
    """ASGI middleware: requires a login in hosted mode (see the module docstring)."""

    def __init__(self, app, config: AuthConfig):
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == HEALTH_PATH:
            return await self.app(scope, receive, send)
        cfg = self.config
        if not cfg.enabled:
            if cfg.exposed:
                return await _deny(
                    scope,
                    receive,
                    send,
                    503,
                    "This server accepts connections from other computers, so it needs a password: set "
                    "TRADES_PASSWORD (and TRADES_SECRET_KEY to keep logins across restarts), then restart it.",
                )
            return await self.app(scope, receive, send)
        if path in OPEN_PATHS:
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if cfg.valid(_cookies(headers).get(COOKIE)):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket" or path.startswith("/api/"):
            return await _deny(scope, receive, send, 401, "Log in first.", code="login_required")
        # A page load: send the browser to the login page, then back here.
        query = scope.get("query_string") or b""
        target = safe_next(path + (("?" + query.decode("latin-1")) if query else ""))
        await send(
            {
                "type": "http.response.start",
                "status": 303,
                "headers": [(b"location", f"{LOGIN_PAGE}?next={quote(target, safe='')}".encode())],
            }
        )
        await send({"type": "http.response.body", "body": b""})


async def _deny(scope, receive, send, status: int, message: str, code: str | None = None) -> None:
    if scope["type"] == "websocket":
        await receive()  # websocket.connect
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"detail": message, **({"code": code} if code else {})}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ----------------------------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------------------------


class LoginBody(BaseModel):
    password: str


def make_router(config: AuthConfig) -> APIRouter:
    router = APIRouter()
    limiter = LoginLimiter()

    @router.get(HEALTH_PATH, include_in_schema=False)
    async def health():
        return {"ok": True}

    @router.get(LOGIN_PAGE, include_in_schema=False)
    async def login_page():
        return HTMLResponse(LOGIN_HTML, headers={"Cache-Control": "no-store"})

    @router.post("/api/login")
    async def login(request: Request, body: LoginBody):
        client = _client(request)
        if limiter.blocked(client):
            return JSONResponse(
                {"detail": "Too many failed attempts. Try again in 15 minutes."}, status_code=429
            )
        if not config.check_password(body.password):
            limiter.failed(client)
            return JSONResponse({"detail": "Wrong password."}, status_code=401)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE,
            config.issue(),
            max_age=SESSION_DAYS * 86400,
            httponly=True,
            samesite="lax",
            secure=_https(request),
            path="/",
        )
        return response

    @router.post("/api/logout")
    async def logout(request: Request):
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax", secure=_https(request))
        return response

    return router


LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trades: log in</title>
<style>
  :root { color-scheme: light dark; --bg: #f7f7f5; --card: #fff; --text: #1a1a1a; --muted: #5c5c5c;
          --border: #d6d6d2; --accent: #1f5fbf; --error: #b42318; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #121212; --card: #1d1d1d; --text: #ececec; --muted: #a8a8a8; --border: #3a3a3a;
            --accent: #6aa0f0; --error: #f97066; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px;
         background: var(--bg); color: var(--text); font: 15px/1.45 system-ui, sans-serif; }
  main { width: 100%; max-width: 360px; background: var(--card); border: 1px solid var(--border);
         border-radius: 12px; padding: 24px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  p { color: var(--muted); margin: 0 0 16px; }
  label { display: block; font-weight: 600; margin-bottom: 6px; }
  input { width: 100%; font: inherit; padding: 9px 10px; border: 1px solid var(--border); border-radius: 8px;
          background: transparent; color: inherit; }
  button { margin-top: 14px; width: 100%; font: inherit; font-weight: 600; padding: 9px; border: 0;
           border-radius: 8px; background: var(--accent); color: #fff; cursor: pointer; }
  button:disabled { opacity: 0.6; cursor: default; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  #error { color: var(--error); margin: 10px 0 0; min-height: 1.45em; }
</style>
</head>
<body>
<main>
  <h1>Trades</h1>
  <p>This server is password-protected.</p>
  <form id="form">
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
    <button type="submit">Log in</button>
    <p id="error" role="alert"></p>
  </form>
</main>
<script>
  const form = document.getElementById("form");
  const error = document.getElementById("error");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    error.textContent = "";
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: document.getElementById("password").value }),
      });
      if (res.ok) {
        // Only ever return to a page on this site: resolve the target the way the browser will.
        const url = new URL(new URLSearchParams(location.search).get("next") || "/", location.origin);
        location.assign(url.origin === location.origin ? url.pathname + url.search + location.hash : "/");
        return;
      }
      const body = await res.json().catch(() => ({}));
      error.textContent = body.detail || "Could not log in.";
    } catch {
      error.textContent = "Cannot reach the server.";
    }
    button.disabled = false;
  });
</script>
</body>
</html>
"""
