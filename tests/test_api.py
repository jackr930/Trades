"""HTTP/WebSocket API and CLI."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from trades.api import create_app
from trades.cli import main as cli_main


@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as c:
        yield c


def test_meta_and_settings(client):
    meta = client.get("/api/meta").json()
    assert len(meta["strategies"]) == 13 and meta["disclaimer"]
    assert {p["id"] for p in meta["providers"]} == {"synthetic", "yahoo", "alpaca", "csv"}
    s = client.get("/api/settings").json()
    assert s["provider"] == "synthetic" and s["alpaca_secret_key"] == ""
    r = client.put("/api/settings", json={"alpaca_key_id": "AKXYZ12345", "alpaca_secret_key": "s3cr3tvalue"})
    assert r.json()["alpaca_key_id"] == "****2345"
    assert "s3cr3tvalue" not in r.text
    assert client.put("/api/settings", json={"risk_per_trade": 3}).status_code == 400
    assert client.put("/api/settings", json={"advisors": [{"id": "nope"}]}).status_code == 400


def test_backtest_endpoint(client):
    r = client.post(
        "/api/backtest",
        json={"strategy": {"id": "faber_trend"}, "symbols": ["SIMIDX"], "start": "2018-01-02"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metrics"]["sharpe"] is not None and body["benchmark"]["label"].startswith("Buy & hold")
    assert body["equity"]["t"][0] >= 1514764800  # evaluation starts at the requested date (2018)
    assert any("Synthetic data" in w for w in body["warnings"])
    json.dumps(body, allow_nan=False)


def test_backtest_validation_errors(client):
    bad = client.post("/api/backtest", json={"strategy": {"id": "pairs_trading"}, "symbols": ["SIMPRA"]})
    assert bad.status_code == 400 and "2 symbols" in bad.json()["detail"]
    bad = client.post(
        "/api/backtest", json={"strategy": {"id": "tsmom", "params": {"lookback": 5}}, "symbols": ["X"]}
    )
    assert bad.status_code == 400
    bad = client.post("/api/backtest", json={"strategy": {"id": "tsmom"}, "symbols": []})
    assert bad.status_code == 422


def test_optimize_endpoint(client):
    grid = {"fast": {"min": 20, "max": 50, "step": 30}, "slow": {"values": [150, 200]}}
    r = client.post(
        "/api/optimize", json={"strategy": {"id": "ma_crossover"}, "symbols": ["SIMIDX"], "grid": grid}
    )
    assert r.status_code == 200, r.text
    assert r.json()["n_trials"] == 4 and r.json()["deflated_sharpe"] is not None
    r = client.post(
        "/api/optimize",
        json={"strategy": {"id": "ma_crossover"}, "symbols": ["SIMIDX"], "grid": {"bogus": {"values": [1]}}},
    )
    assert r.status_code == 400


def test_recommendations_and_chart(client):
    r = client.post("/api/recommendations", json={"symbols": ["SIMIDX", "SIMTEC", "SIMBNK"]})
    assert r.status_code == 200, r.text
    recs = r.json()["recommendations"]
    assert len(recs) == 3 and all(rec["votes"] for rec in recs)
    c = client.get(
        "/api/chart", params={"symbol": "SIMIDX", "strategy": "bollinger_reversion", "count": 200}
    ).json()
    assert len(c["bars"]["t"]) == 200 and {o["column"] for o in c["overlays"]} >= {"bb_upper", "bb_lower"}
    assert c["explanation"]["headline"]


def test_bars_and_quotes(client):
    b = client.get("/api/bars", params={"symbol": "SIMGLD", "count": 50}).json()
    assert len(b["bars"]["c"]) == 50
    q = client.get("/api/quotes", params={"symbols": "SIMGLD,SIMBND"}).json()
    assert set(q) == {"SIMGLD", "SIMBND"}


def test_simulator_flow(client):
    st = client.post(
        "/api/sim", json={"scenario": "steady_bull", "seed": 3, "length_bars": 30, "warmup_bars": 80}
    ).json()
    sid = st["id"]
    r = client.post(
        f"/api/sim/{sid}/orders", json={"side": "buy", "qty": 10, "stop_loss": st["bars"]["c"][-1] * 0.8}
    )
    assert r.status_code == 200 and r.json()["order"]["status"] == "open"
    assert client.post(f"/api/sim/{sid}/orders", json={"side": "sell", "qty": 1000}).status_code == 400
    step = client.post(f"/api/sim/{sid}/step", json={"n": 2, "since": st["cursor"]}).json()
    assert step["account"]["position"]["qty"] == 10 and len(step["bars"]["t"]) == 2 and step["new_fills"]
    assert client.post(f"/api/sim/{sid}/journal", json={"text": "holding"}).status_code == 200
    done = client.post(f"/api/sim/{sid}/finish").json()
    assert done["finished"] and done["scorecard"]["leaderboard"]
    assert client.get("/api/sim/history").json()[0]["id"] == sid
    assert client.get("/api/sim/unknown").status_code == 404


def test_live_websocket_and_watchlist(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"demo_speed": 600}))
    app = create_app(tmp_path / "settings.json", start_live=True, web_dist=tmp_path / "none")
    with TestClient(app) as c:
        with c.websocket_connect("/ws/live") as ws:
            got = None
            for _ in range(20):
                msg = json.loads(ws.receive_text())
                if msg["data"]["recommendations"]:
                    got = msg
                    break
            assert got is not None and got["type"] == "update"
            assert got["data"]["demo"] and got["data"]["running"]
        snap = c.put("/api/live/watchlist", json={"symbols": ["simidx", "SIMGLD"]}).json()
        assert snap["symbols"] == ["SIMIDX", "SIMGLD"]
        assert c.post("/api/live/stop").json()["running"] is False


def test_frontend_fallback_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "npm run build" in r.text


def test_cli_strategies_and_backtest(capsys, tmp_path):
    assert cli_main(["strategies"]) == 0
    out = capsys.readouterr().out
    assert "tsmom" in out and "Moskowitz" not in out
    assert cli_main(["backtest", "rsi2_reversion", "SIMIDX", "--start", "2020-01-01"]) == 0
    out = capsys.readouterr().out
    assert "Sharpe ratio" in out and "Buy & hold" in out
    assert cli_main(["recommend", "SIMIDX", "SIMTEC", "SIMBNK"]) == 0
    assert "not investment advice" in capsys.readouterr().out


def test_local_guard_blocks_rebinding_and_cross_site(client):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"host": "127.0.0.1:8000"}).status_code == 200
    r = client.put("/api/settings", json={"risk_per_trade": 0.02}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.put("/api/settings", json={"risk_per_trade": 0.02}, headers={"origin": "http://testserver"})
    assert ok.status_code == 200
    dev = client.post("/api/live/stop", headers={"origin": "http://localhost:5173"})
    assert dev.status_code == 200
    # safe (read-only) cross-origin requests are left to the browser's CORS rules
    assert client.get("/api/health", headers={"origin": "https://evil.example"}).status_code == 200


def test_local_guard_websocket_origin(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/live", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()
