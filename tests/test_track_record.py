"""The Track Record page's numbers, the journal health check, the weekly digest, backup and restore."""

from __future__ import annotations

import json
from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tests.test_journal import DAYS, SESSION, _experiment, _journal, _synthetic_fetch
from trades import cli
from trades.api import create_app
from trades.backtest import trials
from trades.core import calendar
from trades.journal import source
from trades.journal.track import conclusive_date, digest, health, track_record


def _market() -> dict[str, pd.DataFrame]:
    """A's price rises 1% a session; B and SPY never move."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in DAYS]).tz_localize("UTC")
    a = 100 * 1.01 ** np.arange(len(DAYS))

    def bars(p):
        return pd.DataFrame({"open": p, "high": p, "low": p, "close": p, "volume": 1.0}, index=idx)

    return {"A": bars(a), "B": bars(np.full(len(DAYS), 100.0)), "SPY": bars(np.full(len(DAYS), 100.0))}


def test_track_record_by_hand():
    rule = {"forward": {"labels": ["Strong buy", "Buy"], "horizon": 21, "min_independent_days": 60,
                        "min_mean_excess": 0.0, "min_t": 2.0, "description": "d"}}
    rec = track_record(_journal(), _market(), "SPY", DAYS[39], rule, {"registered": "2025-01-01T00:00:00Z"})
    (exp,) = rec["experiments"]
    assert exp["id"] == "e1" and exp["sessions"] == 4 and exp["rows"] == 6
    # Buy calls held from the next open to the open after, minus SPY (flat): day 0 A +1%, day 1 A and B
    # (+1% and 0, so +0.5%), day 5 A +1%, day 10 A +1%.
    assert exp["curve"]["v"][-1] == pytest.approx(1.01**3 * 1.005 - 1)
    assert len(exp["curve"]["t"]) == 4
    assert [r["symbol"] for r in exp["latest"]] == ["A"] and exp["latest"][0]["label"] == "Strong buy"
    assert exp["verdict"]["status"] == "NOT YET" and exp["independent_days_21"] == 1
    assert rec["rule"]["registered"].startswith("2025") and not rec["rule"]["changed_after_first_row"]
    json.dumps(rec, allow_nan=False)  # ready for the browser: no NaN anywhere
    # Only windows that have finished count.
    early = track_record(_journal(), _market(), "SPY", DAYS[2])["experiments"][0]
    assert early["curve"]["v"] == [pytest.approx(0.01)]


def test_conclusive_date_counts_sessions_forward():
    assert conclusive_date(60, date(2025, 3, 3)) == date(2025, 3, 3)
    # One more 21-session observation needed: 21 sessions after Friday 3 January 2025.
    assert conclusive_date(59, date(2025, 1, 3)) == calendar.trading_days(date(2025, 1, 3), date(2025, 3, 1))[21]


def test_health_flags_gaps_missing_symbols_and_paper_problems():
    rows = [{"session_date": d.isoformat(), "symbol": s} for d in DAYS[:10] if d != DAYS[7] for s in "AB"]
    journal = pd.DataFrame(rows[:-1])  # the last session lacks B
    paper = pd.DataFrame(
        {
            "symbol": ["A", "B", "A"],
            "status": ["accepted", "filled", "filled"],
            "trade_date": [DAYS[3].isoformat(), DAYS[8].isoformat(), DAYS[9].isoformat()],
            "slippage_bps": ["", "75", "4"],
        }
    )
    problems = health(journal, ["A", "B"], DAYS[9], paper)
    assert len(problems) == 4
    assert str(DAYS[7]) in problems[0] and "no row for B" in problems[1]
    assert "still not final" in problems[2] and "more than 50 bps" in problems[3] and "(B)" in problems[3]
    full = pd.DataFrame([{"session_date": d.isoformat(), "symbol": s} for d in DAYS[:10] for s in "AB"])
    assert health(full, ["A", "B"], DAYS[9]) == []
    assert "no rows yet" in health(pd.DataFrame(), ["A"], DAYS[9])[0]


def test_digest_reads_like_a_summary():
    rec = track_record(_journal(), _market(), "SPY", DAYS[39])
    text = digest(rec, DAYS[39])
    assert "Experiment `e1`" in text and "buy A" in text and "+3.5" in text  # 1.01^3 x 1.005 - 1 = 3.54%
    assert "no rows yet" in digest(track_record(pd.DataFrame(), {}, "SPY", DAYS[39]), DAYS[39])


def test_cli_score_writes_the_track_record_and_health_and_digest_read_it(tmp_path, monkeypatch, capsys):
    from tests.test_journal import AFTER_CLOSE
    from trades.journal.recorder import record

    exp = _experiment(tmp_path)
    journal = tmp_path / "recommendations.csv"
    record(exp, _synthetic_fetch(SESSION), journal, now=AFTER_CLOSE, log=lambda m: None)
    monkeypatch.setattr(cli, "_journal_fetcher", lambda: _synthetic_fetch(SESSION))
    monkeypatch.setattr(calendar, "last_completed_session", lambda *a, **k: SESSION)
    paths = ["--experiment", str(exp_path := tmp_path / "experiment.json"), "--journal", str(journal)]
    assert exp_path.exists()
    assert cli.main(["journal", "score", *paths, "--report", str(tmp_path / "REPORT.md")]) == 0
    rec = json.loads((tmp_path / "track_record.json").read_text())
    assert rec["experiment_id"] == exp.id and rec["health"] == [] and rec["experiments"][0]["sessions"] == 1
    capsys.readouterr()
    assert cli.main(["journal", "health", *paths]) == 0 and "healthy" in capsys.readouterr().out
    assert cli.main(["journal", "digest", *paths]) == 0 and exp.id in capsys.readouterr().out
    # A missed session is a problem: exit 1 so the workflow fails and opens an issue.
    monkeypatch.setattr(calendar, "last_completed_session", lambda *a, **k: calendar.next_trading_day(SESSION))
    assert cli.main(["journal", "health", *paths]) == 1 and "No journal rows" in capsys.readouterr().out


def test_track_record_endpoint_reads_the_local_folder_or_github(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "JOURNAL_DIR", tmp_path / "journal")
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as client:
        assert client.get("/api/track-record").json()["record"] is None  # not scored yet
        (tmp_path / "journal").mkdir()
        (tmp_path / "journal" / "track_record.json").write_text(json.dumps({"experiments": [], "benchmark": "SPY"}))
        assert client.get("/api/track-record").json()["record"]["benchmark"] == "SPY"
        # Only GitHub's raw file host can be fetched: nothing else, so the server cannot be pointed inward.
        assert client.put("/api/settings", json={"journal_source": "http://169.254.169.254/x"}).status_code == 400
        url = "https://raw.githubusercontent.com/me/trades/main/journal"
        assert client.put("/api/settings", json={"journal_source": url}).status_code == 200
        calls = []

        def fake_get(u, **kw):
            calls.append(u)
            return httpx.Response(200, json={"experiments": [], "benchmark": "QQQ"}, request=httpx.Request("GET", u))

        monkeypatch.setattr(source.httpx, "get", fake_get)
        source._cache.clear()
        assert client.get("/api/track-record").json()["record"]["benchmark"] == "QQQ"
        client.get("/api/track-record")
        assert calls == [url + "/track_record.json"]  # the second read came from the cache


def test_backup_and_restore_keep_research_history_but_never_keys(tmp_path):
    first = create_app(tmp_path / "a" / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(first) as client:
        client.put("/api/settings", json={"alpaca_secret_key": "supersecret", "state_tax_rate": 0.05})
        trials.record("backtest", "tsmom", "c1", ["SPY"], 0.5)
        backup = client.get("/api/backup").json()
    assert "supersecret" not in json.dumps(backup) and backup["settings"]["state_tax_rate"] == 0.05
    assert "tsmom" in backup["files"]["trials.jsonl"]
    trials.log_path().unlink()  # a fresh server: the research log is gone
    second = create_app(tmp_path / "b" / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(second) as client:
        r = client.post("/api/restore", json=backup).json()
        assert r["lines_added"]["trials.jsonl"] == 1 and r["settings_now"]["state_tax_rate"] == 0.05
        assert r["settings_now"]["alpaca_secret_key"] == ""
        assert client.post("/api/restore", json=backup).json()["lines_added"]["trials.jsonl"] == 0  # no duplicates
        assert client.post("/api/restore", json={"settings": {}}).status_code == 400
        bad = {**backup, "settings": {"state_tax_rate": 0.9}}
        assert client.post("/api/restore", json=bad).status_code == 400
