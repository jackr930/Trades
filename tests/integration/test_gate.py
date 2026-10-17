"""The paper-to-live gate, the kill switch drills and the paper commands, in a scratch copy of the
repository (so a drill's HALT file never lands in the real one)."""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from tests.fakes import SimAlpaca
from tests.helpers import ny_time
from trades import config, runs
from trades.cli import main as cli_main
from trades.cli import trading
from trades.engine.paper import PaperRecords
from trades.execution.broker import OrderRequest
from trades.reporting.tracking import Tracking
from trades.risk import RiskLimits, killswitch, load_limits
from trades.validation import gate

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)
MARKET = date(2026, 8, 26)  # a session the synthetic intraday bars cover


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    for folder in ("config", "risk", "specs"):
        shutil.copytree(REPO / folder, tmp_path / folder)
    monkeypatch.setenv("TRADES_REPO", str(tmp_path))
    config.clear_cache()
    yield tmp_path
    config.clear_cache()


@pytest.fixture
def broker(intraday):
    b = SimAlpaca({"SIMTEC": intraday["SIMTEC"]})
    b.begin(MARKET)
    b.advance(ny_time(MARKET, "11:00"))
    return b


def _spec():
    from trades import specs

    return specs.find("STRAT-002")


GOOD = Tracking(
    "STRAT-002-v1", first="2026-06-01", last="2026-08-28", sessions=64, weeks=12.6, decisions=3700, trades=62,
    compared=900, signal_match=0.97, strategy_match=0.99, expectancy={"mean": 0.0008, "n": 62, "t_stat": 1.1},
    backtest_expectancy=0.0012, fills={"matched": 120, "ratio": 1.2, "actual_bps": 4.2, "model_bps": 3.5},
    daily_correlation=0.91, daily_days=50,
)


def test_the_gate_needs_evidence_a_person_and_a_written_loss_budget(scratch, monkeypatch):
    spec, records = _spec(), PaperRecords("STRAT-002-v1")
    monkeypatch.setattr(runs, "latest", lambda key: {"id": "r1", "verdict": "pass"})
    monkeypatch.setattr(runs, "committed", lambda path: True)
    checks = gate.evaluate(spec, GOOD, records)
    assert [c.n for c in checks] == list(range(1, 11))
    assert [c.n for c in checks if c.status == "pass"] == [1, 2, 3, 4, 5]
    assert gate.verdict(checks) == "not_yet"
    # The drills, the alert test, the two attestations and the loss budget complete it.
    for kind in (*gate.DRILLS, "alert"):
        records.log_event(NOW, "drill", f"{kind}: passed.", drill=kind, passed=True)
    live = scratch / "config" / "live.yaml"
    live.write_text(live.read_text().replace("loss_budget: null", "loss_budget: 3000"))  # written down first ...
    config.clear_cache()
    gate.attest(records, "live_config", now=NOW)  # ... then the files are reviewed as they now stand
    gate.attest(records, "broker_account", now=NOW)
    checks = gate.evaluate(spec, GOOD, records)
    assert gate.verdict(checks) == "pass", [c for c in checks if c.status != "pass"]
    # Editing the reviewed files afterwards voids the review.
    limits = scratch / "risk" / "limits.yaml"
    limits.write_text(limits.read_text() + "\n# loosened later\n")
    review = next(c for c in gate.evaluate(spec, GOOD, records) if c.n == 8)
    assert review.status == "fail" and "risk/limits.yaml" in review.detail


@pytest.mark.parametrize(
    ("change", "failing"),
    [
        ({"signal_match": 0.9}, 3),
        ({"daily_correlation": 0.5}, 3),
        ({"expectancy": {"mean": 0.0004, "n": 62}}, 4),  # a third of the backtest's
        ({"expectancy": {"mean": -0.0001, "n": 62}}, 4),
        ({"fills": {"matched": 120, "ratio": 1.8, "actual_bps": 6.3, "model_bps": 3.5}}, 5),
        ({"errors_recent": 2}, 6),
    ],
)
def test_each_measure_can_fail_the_gate(scratch, monkeypatch, change, failing):
    monkeypatch.setattr(runs, "latest", lambda key: {"id": "r1", "verdict": "pass"})
    monkeypatch.setattr(runs, "committed", lambda path: True)
    checks = gate.evaluate(_spec(), replace(GOOD, **change), PaperRecords("STRAT-002-v1"))
    assert [c.n for c in checks if c.status == "fail"] == [failing] and gate.verdict(checks) == "fail"


def test_daily_specs_need_twelve_weeks(scratch, monkeypatch):
    from trades import specs

    monkeypatch.setattr(runs, "latest", lambda key: None)
    daily = specs.find("STRAT-000")
    check = gate.evaluate(daily, replace(GOOD, spec="STRAT-000-v1", weeks=10.0), PaperRecords("STRAT-000-v1"))[1]
    assert check.status == "pending" and "10 of 12 weeks" in check.detail


# ------------------------------------------------------------------------------------- drills


def test_the_four_kill_switch_drills(scratch, broker):
    records, limits = PaperRecords("STRAT-002-v1"), load_limits()
    broker.submit(OrderRequest("SIMTEC", "buy", 2, "STRAT-002-v1-x-SIMTEC-entry"))
    broker.advance(ny_time(MARKET, "11:10"))
    assert broker.positions()
    quiet = {"log": lambda m: None, "sleep": lambda s: None, "now": NOW}
    # HALT: refused until the switch is on, and while an order that adds exposure is still working.
    assert not gate.run_drill("halt", lambda: broker, records, limits, **quiet)[0]
    killswitch.halt("drill", limits)
    entry = broker.submit(OrderRequest("SIMTEC", "buy", 1, "STRAT-002-v1-y-SIMTEC-entry", type="stop", stop_price=1e6))
    assert not gate.run_drill("halt", lambda: broker, records, limits, **quiet)[0]
    broker.cancel_order(entry["id"])
    assert gate.run_drill("halt", lambda: broker, records, limits, **quiet)[0]
    # Flatten: everything closed, and the switch stays on.
    assert gate.run_drill("flatten", lambda: broker, records, limits, **quiet) == (
        True, "The paper account is flat and the kill switch is on.")
    assert broker.positions() == {} and killswitch.is_halted(limits)
    # The daily loss stop: the risk engine flattens and blocks, and the account ends flat.
    broker.submit(OrderRequest("SIMTEC", "buy", 1, "STRAT-002-v1-z-SIMTEC-entry"))
    broker.advance(ny_time(MARKET, "11:20"))
    ok, detail = gate.run_drill("daily-loss", lambda: broker, records, limits, **quiet)
    assert ok and "flattened and blocked" in detail and broker.positions() == {}
    # A revoked key: passes only once Alpaca refuses it.
    assert not gate.run_drill("revoke-key", lambda: broker, records, limits, **quiet)[0]
    broker.revoked = True
    assert gate.run_drill("revoke-key", lambda: broker, records, limits, **quiet)[0]
    assert set(gate.drills_passed(records)) == set(gate.DRILLS)
    assert gate.run_drill("alert", lambda: broker, records, limits, alerts=lambda k, m: True, **quiet)[0]


# ----------------------------------------------------------------------------------- commands


def test_paper_commands(scratch, broker, monkeypatch, capsys):
    monkeypatch.setattr(trading, "_paper_client", lambda: broker)
    broker.submit(OrderRequest("SIMTEC", "buy", 1, "manual"))
    broker.advance(ny_time(MARKET, "11:10"))
    assert cli_main(["paper", "status", "--spec", "STRAT-002"]) == 0
    out = capsys.readouterr().out
    assert "STRAT-002-v1" in out and "Kill switch: off" in out and "SIMTEC: 1 shares" in out
    assert cli_main(["paper", "flatten", "--spec", "STRAT-002", "--reason", "testing"]) == 0
    assert broker.positions() == {} and "testing" in killswitch.reason(load_limits())
    assert cli_main(["paper", "drill", "--spec", "STRAT-002"]) == 1  # which drill?
    broker.revoked = True
    assert cli_main(["paper", "drill", "revoke-key", "--spec", "STRAT-002"]) == 0
    assert "Drill revoke-key: PASSED" in capsys.readouterr().out
    assert cli_main(["resume"]) == 0 and not killswitch.is_halted(load_limits())


def test_the_backstop_flattens_only_when_the_runner_has_stopped(scratch, broker, monkeypatch, capsys):
    import json

    import trades.data.calendar as calendar

    monkeypatch.setattr(trading, "_paper_client", lambda: broker)
    monkeypatch.setattr(calendar, "session_bounds", lambda d: (datetime.min.replace(tzinfo=timezone.utc),
                                                               datetime.max.replace(tzinfo=timezone.utc)))
    monkeypatch.setattr(calendar, "is_trading_day", lambda d: True)  # the market is open, whatever today is
    broker.submit(OrderRequest("SIMTEC", "buy", 1, "manual"))
    broker.advance(ny_time(MARKET, "11:10"))
    beat = PaperRecords("STRAT-002-v1").folder / "heartbeat.json"
    beat.parent.mkdir(parents=True)
    beat.write_text(json.dumps({"time": datetime.now(timezone.utc).isoformat()}))
    assert cli_main(["paper", "backstop", "--spec", "STRAT-002"]) == 0
    assert "The runner is alive" in capsys.readouterr().out and broker.positions()
    beat.write_text(json.dumps({"time": "2026-08-26T15:00:00Z"}))  # long silent
    assert cli_main(["paper", "backstop", "--spec", "STRAT-002"]) == 0
    assert broker.positions() == {} and "backstop" in killswitch.reason(load_limits())


def test_track_and_gate_commands(scratch, capsys):
    assert cli_main(["track", "STRAT-002"]) == 0 and "No paper decisions yet" in capsys.readouterr().out
    assert cli_main(["gate", "STRAT-002"]) == 0 and "NOT YET" in capsys.readouterr().out
    # An attestation needs a person at the terminal (or --yes).
    assert cli_main(["gate", "STRAT-002", "--attest", "live_config"]) == 1
    assert gate.attestations(PaperRecords("STRAT-002-v1")) == {}
    assert cli_main(["gate", "STRAT-002", "--attest", "broker_account", "--yes", "--note", "margin, $25k"]) == 0
    saved = gate.attestations(PaperRecords("STRAT-002-v1"))["broker_account"]
    assert saved["note"] == "margin, $25k" and saved["statement"] == gate.ATTESTATIONS["broker_account"]


def test_drills_never_touch_the_real_repository():
    assert not (REPO / "HALT").exists()
    assert RiskLimits().halt_file == "HALT"


def test_the_pipeline_in_the_app(scratch, tmp_path):
    from fastapi.testclient import TestClient

    from trades.api import create_app

    app = create_app(tmp_path / "home" / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as client:
        overview = client.get("/api/pipeline").json()
        by_key = {s["key"]: s for s in overview["specs"]}
        assert {"STRAT-000-v1", "STRAT-002-v1"} <= set(by_key) and overview["kill_switch"] == {"on": False, "reason": ""}
        assert by_key["STRAT-000-v1"]["paper_target"] and not by_key["STRAT-002-v1"]["paper_target"]
        assert by_key["STRAT-002-v1"]["latest_run"] is None and by_key["STRAT-002-v1"]["paper"] == {"started": False}
        detail = client.get("/api/pipeline/specs/STRAT-002").json()
        assert detail["spec"]["key"] == "STRAT-002-v1" and detail["runs"] == [] and "Hypothesis" in detail["body"]
        assert client.get("/api/pipeline/specs/STRAT-999").status_code == 404
        assert client.get("/api/pipeline/paper/STRAT-002").json()["equity"] == []
        # The kill switch, from the app.
        on = client.post("/api/killswitch", json={"on": True, "reason": "checking the data feed"}).json()
        assert on["on"] and "checking the data feed" in on["reason"] and killswitch.is_halted(load_limits())
        assert client.post("/api/killswitch", json={"on": False}).json() == {"on": False, "reason": ""}
        gate_now = client.post("/api/pipeline/gate/STRAT-002").json()
        assert gate_now["verdict"] == "not_yet" and len(gate_now["checks"]) == 10
        # The top bar's clock and the alert feed.
        assert client.get("/api/clock").json()["phase"] in ("regular", "pre", "post", "closed")
        records = PaperRecords("STRAT-002-v1")
        records.log_event(datetime.now(timezone.utc), "error", "the feed stopped")
        records.log_event(datetime.now(timezone.utc), "close", "market-on-close sent")
        feed = client.get("/api/alerts").json()
        assert [a["severity"] for a in feed["alerts"][:2]] in (["info", "crit"], ["crit", "info"])
        assert feed["counts_24h"] == {"crit": 1, "warn": 0, "info": 1} and feed["alerts"][0]["spec"] == "STRAT-002-v1"
