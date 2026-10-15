"""The decision gate, and the data a user can take away or delete."""

from __future__ import annotations

from fastapi.testclient import TestClient

from trades.api import create_app
from trades.journal.rule import decision
from trades.journal.track import digest

PASS = {"status": "PASS", "detail": "ok"}
FAIL = {"status": "FAIL", "detail": "no"}
WAIT = {"status": "NOT YET", "detail": "wait"}


def test_real_money_only_when_every_check_passed():
    assert decision(PASS, PASS, PASS)["status"] == "YES, WITH CARE"
    assert decision(PASS, FAIL, WAIT)["status"] == "NO"  # one failure is enough
    assert decision(PASS, PASS, WAIT)["status"] == "NOT YET"
    assert decision(None, None, None)["status"] == "NOT YET"
    assert decision(PASS, PASS, PASS, changed_after_first_row=True)["status"] == "INVALID"
    d = decision(None, WAIT, None)
    assert [c["status"] for c in d["checks"]] == ["NOT YET"] * 3 and "consensus_check" in d["checks"][0]["detail"]
    text = digest({"experiments": [], "decision": decision(FAIL, None, None)}, None)
    assert "Decision gate: NO" in text


def test_users_files_live_in_one_home_and_can_be_deleted(tmp_path):
    home = tmp_path / "me"
    app = create_app(home / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as client:
        client.put("/api/settings", json={"alpaca_secret_key": "supersecret", "account_equity": 5000})
        client.post("/api/backtest", json={"strategy": {"id": "faber_trend"}, "symbols": ["SIMIDX"], "start": "2016-01-04"})
        assert (home / "trials.jsonl").exists() and (home / "settings.json").exists()  # all under this user's home
        assert client.post("/api/delete-my-data", json={}).status_code == 400  # needs the confirmation
        r = client.post("/api/delete-my-data", json={"confirm": "delete everything"}).json()
        assert "trials.jsonl" in r["deleted"] and r["settings_now"]["account_equity"] == 100_000
        assert r["settings_now"]["alpaca_secret_key"] == "" and not (home / "trials.jsonl").exists()
        assert not (home / "settings.json").exists()
