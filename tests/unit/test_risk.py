"""The risk engine: every rule in risk/limits.yaml, one at a time, then inside a backtest."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trades.engine.backtest import backtest_strategy
from trades.execution.sim_broker import BacktestConfig
from trades.risk import RiskEngine, RiskLimits, killswitch
from trades.risk.engine import session_of
from trades.strategies import create_strategy

SYMS = ["AAA", "BBB", "CCC"]
P = np.array([100.0, 50.0, 20.0])
NONE = np.zeros(3)


def at(day: str, hhmm: str = "15:00") -> pd.Timestamp:
    return pd.Timestamp(f"{day} {hhmm}", tz="America/New_York").tz_convert("UTC")


def engine(**limits) -> RiskEngine:
    base = dict(max_position_pct=1.0, max_gross_exposure=1.0, allow_short=True)
    return RiskEngine(RiskLimits(**{**base, **limits}), SYMS)


def test_sessions_of_daily_and_intraday_bars():
    assert session_of(pd.Timestamp("2026-03-10", tz="UTC")) == date(2026, 3, 10)  # daily bars: 00:00 UTC
    assert session_of(at("2026-03-10", "09:30")) == date(2026, 3, 10)


def test_kill_switch_flattens_everything():
    halted = {"on": False}
    r = RiskEngine(RiskLimits(), SYMS, halt_check=lambda: halted["on"])
    w = np.array([0.2, 0.0, 0.0])
    assert r.review(at("2026-03-10"), 10_000, w, NONE, P).tolist() == [0.2, 0.0, 0.0]
    halted["on"] = True
    assert r.review(at("2026-03-10", "15:05"), 10_000, w, w, P).tolist() == [0.0, 0.0, 0.0]
    r.review(at("2026-03-10", "15:10"), 10_000, w, w, P)
    assert [e.kind for e in r.events] == ["halt"]  # reported once per session, not every bar


def test_daily_loss_limit_flattens_until_the_next_session():
    r = engine(daily_loss_limit=0.015)
    w = np.array([0.5, 0.0, 0.0])
    r.review(at("2026-03-09", "15:55"), 10_000, w, NONE, P)  # yesterday's close: 10,000
    assert r.review(at("2026-03-10", "10:00"), 9_900, w, w, P)[0] == 0.5  # -1.0%: fine
    assert r.review(at("2026-03-10", "10:05"), 9_840, w, w, P)[0] == 0.0  # -1.6%: flatten
    assert r.review(at("2026-03-10", "11:00"), 9_990, w, NONE, P)[0] == 0.0  # still blocked today
    assert r.events[-1].kind == "daily_loss"
    assert r.review(at("2026-03-11", "10:00"), 9_990, w, NONE, P)[0] == 0.5  # a new session


def test_weekly_loss_limit_blocks_until_next_week():
    r = engine(daily_loss_limit=0.03, weekly_loss_limit=0.04)
    w = np.array([0.5, 0.0, 0.0])
    r.review(at("2026-03-06"), 10_000, w, NONE, P)  # Friday close
    r.review(at("2026-03-09"), 9_750, w, w, P)  # Monday -2.5%
    assert r.review(at("2026-03-10"), 9_550, w, w, P)[0] == 0.0  # Tuesday: -4.5% on the week
    assert r.review(at("2026-03-12"), 9_600, w, NONE, P)[0] == 0.0  # still this week
    assert r.review(at("2026-03-16"), 9_600, w, NONE, P)[0] == 0.5  # next Monday
    assert [e.kind for e in r.events] == ["weekly_loss"]


def test_symbols_shorts_and_position_size():
    r = engine(blocked_symbols=("BBB",), allow_short=False, max_position_pct=0.3)
    out = r.review(at("2026-03-10"), 10_000, np.array([0.8, 0.2, -0.2]), NONE, P)
    assert out.tolist() == [0.3, 0.0, 0.0]
    assert {e.kind for e in r.events} == {"position_size", "symbol", "short"}


def test_a_new_trade_is_shrunk_to_the_maximum_risk_at_its_stop():
    r = engine(max_risk_per_trade=0.005)
    stops = np.array([99.0, np.nan, np.nan])  # 1% below the price: a full position would risk 1%
    out = r.review(at("2026-03-10"), 10_000, np.array([1.0, 0.0, 0.0]), NONE, P, stops)
    assert out[0] == pytest.approx(0.5)
    # Once approved, a trade may not grow while it lasts, even if the strategy asks for more ...
    held = np.array([out[0], 0.0, 0.0])
    assert r.review(at("2026-03-10", "15:05"), 10_000, np.array([1.0, 0.0, 0.0]), held, P, stops)[0] == pytest.approx(0.5)
    # ... and when it ends, the next trade is judged afresh.
    r.review(at("2026-03-10", "15:10"), 10_000, NONE, held, P)
    assert r.review(at("2026-03-10", "15:15"), 10_000, np.array([0.3, 0.0, 0.0]), NONE, P, stops)[0] == pytest.approx(0.3)


def test_a_stop_entry_is_measured_from_its_entry_level():
    r = engine(max_risk_per_trade=0.005)
    stops, entries = np.array([101.0, np.nan, np.nan]), np.array([102.0, np.nan, np.nan])
    out = r.review(at("2026-03-10"), 10_000, np.array([1.0, 0.0, 0.0]), NONE, P, stops, entries)
    assert out[0] == pytest.approx(0.005 * 102 / 1.0)  # not from the current price of 100


def test_open_positions_keep_their_place_and_new_ones_come_largest_first():
    r = RiskEngine(RiskLimits(max_open_positions=2, max_position_pct=1.0), ["A", "B", "C", "D"])
    held = np.array([0.0, 0.0, 0.0, 0.1])
    out = r.review(at("2026-03-10"), 10_000, np.array([0.2, 0.3, 0.1, 0.1]), held, np.ones(4) * 10)
    assert out.tolist() == [0.0, 0.3, 0.0, 0.1]


def test_after_the_daily_order_limit_only_exits_are_allowed():
    r = engine(max_orders_per_day=3)
    r.review(at("2026-03-10"), 10_000, NONE, NONE, P)
    r.note_fills(3)
    held = np.array([0.5, 0.2, 0.0])
    out = r.review(at("2026-03-10", "15:05"), 10_000, np.array([0.8, 0.0, 0.3]), held, P)
    assert out.tolist() == [0.5, 0.0, 0.0]  # no adds, no new trades; the exit in BBB is allowed


def test_halt_file_helpers(tmp_path):
    limits = RiskLimits()
    assert not killswitch.is_halted(limits, tmp_path)
    path = killswitch.halt("testing the switch", limits, tmp_path)
    assert path == tmp_path / "HALT" and killswitch.is_halted(limits, tmp_path)
    assert "testing the switch" in killswitch.reason(limits, tmp_path)
    assert killswitch.resume(limits, tmp_path) and not killswitch.is_halted(limits, tmp_path)


def test_a_backtest_under_the_limits_never_risks_more_than_allowed(intraday):
    df = intraday["SIMTEC"]
    limits = RiskLimits(max_risk_per_trade=0.005, allow_short=False, max_gross_exposure=1.0, max_position_pct=1.0)
    cfg = BacktestConfig(initial_cash=25_000, slippage_bps=0, periods_per_year=252 * 78)
    strat = create_strategy("orb", {"stop_mode": "atr", "stop_atr_frac": 0.5})
    bt = backtest_strategy(strat, {"SIMTEC": df}, None, cfg, risk=limits)
    entries = [f for f in bt.result.fills if f.tag in ("market", "stop_entry") and f.qty != 0]
    assert entries and all(f.qty > 0 for f in entries)  # no shorts
    stops = bt.output.stop_losses["SIMTEC"].to_numpy()
    equity = bt.result.equity.to_numpy()
    for f in entries:
        stop = stops[f.index - 1]  # the stop placed with the order at the previous close
        risk = f.qty * (f.price - stop) / equity[f.index - 1]
        assert risk <= 0.005 * 1.01  # within the limit (small slack: the fill moves from the decision price)
    gross = bt.result.gross_exposure.to_numpy()
    assert gross.max() <= 1.0 + 1e-6
    assert any("wanted to go short" in w for w in bt.warnings)  # the skipped shorts are reported
    assert {e.kind for e in bt.result.risk_events} >= {"trade_risk"}
