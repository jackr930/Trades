"""``trades track`` on a week of synthetic paper trading: a runner that follows its backtest matches
its replay signal for signal, and the costs the paper account actually paid show up against the model."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from tests.fakes import BAR, SimAlpaca
from tests.helpers import ny_time, orb_target
from trades.data.calendar import trading_days
from trades.engine.intraday import IntradayRunner
from trades.engine.paper import PaperRecords
from trades.reporting.tracking import Tracking, render, track
from trades.risk import RiskLimits

WEEK = trading_days(date(2026, 8, 24), date(2026, 8, 31))  # six sessions


def paper_week(intraday, tmp_path, slip_bps: float = 0.0):
    target = orb_target()
    bars = {"SIMTEC": intraday["SIMTEC"]}
    broker = SimAlpaca(bars, slip_bps=slip_bps)
    records = PaperRecords(target.key, root=tmp_path / "paper")

    def completed(now):
        cut = pd.Timestamp(now) - BAR
        return {s: df[(df.index <= cut) & (df.index > cut - pd.Timedelta(days=40))] for s, df in bars.items()}

    for day in WEEK:
        broker.begin(day)
        runner = IntradayRunner(target, broker, completed, RiskLimits(), records, session=day, submit=True,
                                log=lambda m: None)
        t = ny_time(day, "09:25")
        while True:
            broker.advance(t)
            if not runner.step(t):
                break
            t += timedelta(minutes=2)
    spec = SimpleNamespace(key=target.key, timeframe="5m", provider="synthetic", symbols=target.symbols,
                           strategy=target.strategy, costs={"half_spread_bps": 2.0, "slippage_bps": 1.0,
                                                            "stop_slippage_bps": 5.0})
    return spec, records


def fetcher(intraday):
    def fetch(provider, symbols, timeframe, start, end):
        assert provider == "synthetic" and timeframe == "5m"
        cut = pd.Timestamp(end + timedelta(days=1)).tz_localize("UTC")
        return {s: intraday[s][(intraday[s].index >= pd.Timestamp(start).tz_localize("UTC")) & (intraday[s].index < cut)]
                for s in symbols}, {}
    return fetch


def test_a_runner_that_follows_its_backtest_matches_its_replay(intraday, tmp_path):
    spec, records = paper_week(intraday, tmp_path)
    t = track(spec, records, fetcher(intraday), limits=RiskLimits(), now=ny_time(WEEK[-1], "17:00"))
    assert (t.first, t.last, t.sessions, t.missed_sessions, t.missed_bars) == ("2026-08-24", "2026-08-31", 6, 0, 0)
    assert t.signal_match == 1.0 and t.strategy_match == 1.0 and t.compared > 50
    assert t.trades >= 4 and t.expectancy["n"] == t.trades and t.replay_expectancy["n"] == t.trades
    assert t.fills["matched"] == 2 * t.trades and t.fills["paper_only"] == 0 and t.fills["replay_only"] == 0
    assert t.fills["actual_bps"] == pytest.approx(0.0, abs=0.05)  # the fake account charges nothing: all model
    assert t.daily_days == 6 and t.daily_correlation > 0.95
    report = render(t)
    assert "| Signal match | 100.0% of" in report and "at least 95%" in report


def test_costs_the_paper_account_paid_show_up_against_the_model(intraday, tmp_path):
    spec, records = paper_week(intraday, tmp_path, slip_bps=6.0)
    t = track(spec, records, fetcher(intraday), limits=RiskLimits(), now=ny_time(WEEK[-1], "17:00"))
    assert t.fills["actual_bps"] == pytest.approx(6.0, abs=0.05)  # whatever the model said, paper paid 6 bps
    assert t.fills["ratio"] == pytest.approx(6.0 / t.fills["model_bps"], rel=1e-2)
    assert t.signal_match == 1.0  # costs change the P&L, not the decisions


def test_tracking_without_paper_decisions(tmp_path):
    spec = SimpleNamespace(key="NONE-v1")
    t = track(spec, PaperRecords("NONE-v1", root=tmp_path))
    assert isinstance(t, Tracking) and t.first is None and "No paper decisions yet" in render(t)
