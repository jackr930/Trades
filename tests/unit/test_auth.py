"""Hosted mode: the password gate, sessions, the login limiter and the public-without-password guard."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from trades.api import create_app
from trades.api.auth import COOKIE, AuthConfig, LoginLimiter, safe_next
from trades.api.security import allowed_hosts
from trades.cli import main as cli_main

SECRET = b"s" * 32


def client_for(tmp_path, auth: AuthConfig, **kw) -> TestClient:
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist", auth=auth)
    return TestClient(app, **kw)


@pytest.fixture()
def hosted(tmp_path):
    with client_for(tmp_path, AuthConfig(password="correct horse", secret=SECRET)) as c:
        yield c


def login(c: TestClient, password: str = "correct horse"):
    return c.post("/api/login", json={"password": password})


def test_without_a_password_a_local_server_needs_no_login(tmp_path):
    with client_for(tmp_path, AuthConfig()) as c:
        meta = c.get("/api/meta")
        assert meta.status_code == 200 and meta.json()["auth_enabled"] is False


def test_everything_needs_a_login_when_a_password_is_set(hosted):
    r = hosted.get("/api/meta")
    assert r.status_code == 401 and r.json()["code"] == "login_required"
    page = hosted.get("/lab?x=1", follow_redirects=False)  # a page load goes to the login page
    assert page.status_code == 303 and page.headers["location"] == "/login?next=%2Flab%3Fx%3D1"
    with pytest.raises(WebSocketDisconnect):
        with hosted.websocket_connect("/ws/live") as ws:
            ws.receive_text()
    form = hosted.get("/login")
    assert form.status_code == 200 and 'type="password"' in form.text
    assert hosted.get("/healthz").json() == {"ok": True}


def test_logging_in_opens_the_app_and_logging_out_closes_it(hosted):
    assert login(hosted, "wrong").status_code == 401
    r = login(hosted)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert f"{COOKIE}=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    meta = hosted.get("/api/meta")
    assert meta.status_code == 200 and meta.json()["auth_enabled"] is True
    token = hosted.cookies[COOKIE]
    with hosted.websocket_connect("/ws/live", headers={"cookie": f"{COOKIE}={token}"}) as ws:
        assert '"type": "update"' in ws.receive_text()
    assert hosted.post("/api/logout").status_code == 204
    assert hosted.get("/api/meta").status_code == 401


def test_the_cookie_is_marked_secure_behind_https(hosted):
    r = hosted.post("/api/login", json={"password": "correct horse"}, headers={"x-forwarded-proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_sessions_cannot_be_forged_reused_after_expiry_or_kept_after_a_password_change():
    cfg = AuthConfig(password="pw-one", secret=SECRET)
    token = cfg.issue()
    assert cfg.valid(token)
    exp, sig = token.split(".")
    assert not cfg.valid(f"{int(exp) + 1}.{sig}")  # a longer life needs a new signature
    assert not cfg.valid(f"{exp}.{'0' * len(sig)}")
    assert not cfg.valid("garbage") and not cfg.valid(None)
    assert not cfg.valid(cfg.issue(now=time.time() - 31 * 86400))  # expired
    assert not AuthConfig(password="pw-two", secret=SECRET).valid(token)  # password changed
    assert not AuthConfig(password="pw-one", secret=b"x" * 32).valid(token)  # another server's key


def test_repeated_failed_logins_are_slowed_down(hosted):
    for _ in range(10):
        assert login(hosted, "guess").status_code == 401
    assert login(hosted).status_code == 429  # even the right password waits out the window
    limiter = LoginLimiter()
    for _ in range(10):
        limiter.failed("a", now=0.0)
    assert limiter.blocked("a", now=1.0) and not limiter.blocked("b", now=1.0)
    assert not limiter.blocked("a", now=16 * 60.0)  # the window slides


def test_login_only_ever_returns_to_this_site():
    assert safe_next("/lab?strategy=tsmom") == "/lab?strategy=tsmom"
    bad = (
        "https://evil.example",
        "//evil.example",
        "/\\evil.example",
        "/\t/evil.example",
        "/\n/evil.example",
        "",
        None,
    )
    for target in bad:
        assert safe_next(target) == "/", target


def test_the_login_limiter_forgets_old_clients():
    limiter = LoginLimiter()
    for i in range(700):
        limiter.failed(f"client-{i}", now=float(i))
    limiter.failed("latest", now=2000.0)  # the first 700 have aged out of the window by now
    assert len(limiter._fails) <= 2 and not limiter.blocked("client-0", now=2000.0)


def test_a_public_server_without_a_password_serves_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "trades-demo.onrender.com")
    assert "trades-demo.onrender.com" in allowed_hosts()
    with client_for(tmp_path, AuthConfig.from_env(), base_url="https://trades-demo.onrender.com") as c:
        r = c.get("/api/meta")
        assert r.status_code == 503 and "TRADES_PASSWORD" in r.json()["detail"]
        assert c.get("/healthz").status_code == 200
    monkeypatch.setenv("TRADES_PASSWORD", "hunter22")
    with client_for(tmp_path, AuthConfig.from_env(), base_url="https://trades-demo.onrender.com") as c:
        assert c.get("/api/meta").status_code == 401  # now it asks for the password instead
        assert login(c, "hunter22").status_code == 200
        assert c.get("/api/meta").status_code == 200


def test_health_checks_pass_whatever_host_name_they_use(hosted):
    assert hosted.get("/healthz", headers={"host": "10.0.3.7:10000"}).status_code == 200
    assert hosted.get("/api/meta", headers={"host": "10.0.3.7:10000"}).status_code == 400


def test_serve_listens_on_the_port_a_host_assigns(monkeypatch):
    seen = {}
    monkeypatch.setattr("trades.cli.cmd_serve", lambda args: seen.update(port=args.port, host=args.host) or 0)
    monkeypatch.setenv("PORT", "10000")
    assert cli_main(["serve"]) == 0 and seen["port"] == 10000
    monkeypatch.delenv("PORT")
    assert cli_main(["serve"]) == 0 and seen["port"] == 8000
