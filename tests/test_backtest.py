"""Backtest engine, metrics, runner and optimisation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tests.conftest import UNIVERSE
from trades.backtest.engine import BacktestConfig, buy_and_hold_weights, run_backtest
from trades.backtest.metrics import monthly_returns, performance_metrics
from trades.backtest.optimize import expand_grid, grid_search, range_values, walk_forward
from trades.backtest.runner import StrategySpec, backtest_payload, backtest_strategy, sanitize


def _frame(opens, closes, start="2024-01-02"):
    idx = pd.bdate_range(start, periods=len(closes), tz="UTC")
    o, c = np.asarray(opens, float), np.asarray(closes, float)
    return pd.DataFrame(
        {"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c, "volume": 1e6}, index=idx
    )


def test_fills_happen_at_next_open_with_costs():
    df = _frame([10, 11, 12, 13, 14], [10.5, 11.5, 12.5, 13.5, 14.5])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0, 0.0, 0.0]}, index=df.index)
    cfg = BacktestConfig(initial_cash=1000, slippage_bps=100, commission_bps=0)
    res = run_backtest({"X": df}, w, cfg)
    buy, sell = res.fills
    # decided at bar 0's close (price 10.5): 1000/10.5 -> 95 shares, filled at bar 1's open +1% slippage
    assert buy.index == 1 and buy.qty == 95 and buy.price == pytest.approx(11 * 1.01)
    # target 0 at bar 3's close -> sell at bar 4's open -1%
    assert sell.index == 4 and sell.price == pytest.approx(14 * 0.99)
    expected = 1000 - 95 * 11 * 1.01 + 95 * 14 * 0.99
    assert res.equity.iloc[-1] == pytest.approx(expected)
    (trade,) = res.trades
    assert trade.pnl == pytest.approx(95 * (14 * 0.99 - 11 * 1.01))


def test_next_close_execution_and_no_trades_when_flat():
    df = _frame([10, 11, 12], [10.5, 11.5, 12.5])
    res = run_backtest(
        {"X": df},
        pd.DataFrame({"X": [1.0, 1.0, 1.0]}, index=df.index),
        BacktestConfig(initial_cash=1000, slippage_bps=0, execution="next_close"),
    )
    assert res.fills[0].price == pytest.approx(11.5)
    flat = run_backtest({"X": df}, pd.DataFrame({"X": [0.0] * 3}, index=df.index))
    assert not flat.fills and flat.equity.iloc[-1] == pytest.approx(100_000)


def test_short_selling_profit_and_disallow():
    df = _frame([100, 100, 90, 80], [100, 95, 85, 80])
    w = pd.DataFrame({"X": [-1.0, -1.0, -1.0, -1.0]}, index=df.index)
    res = run_backtest({"X": df}, w, BacktestConfig(initial_cash=1000, slippage_bps=0))
    assert res.positions["X"].iloc[-1] == -10
    assert res.equity.iloc[-1] == pytest.approx(1000 + 10 * (100 - 80))
    blocked = run_backtest({"X": df}, w, BacktestConfig(initial_cash=1000, allow_short=False))
    assert not blocked.fills


def test_commission_and_borrow_fee():
    df = _frame([100] * 5, [100] * 5)
    w = pd.DataFrame({"X": [-0.5] * 5}, index=df.index)
    cfg = BacktestConfig(
        initial_cash=10_000,
        slippage_bps=0,
        commission_per_share=0.01,
        min_commission=1.0,
        borrow_bps_annual=252 * 10,
    )  # 10 bps per bar
    res = run_backtest({"X": df}, w, cfg)
    assert res.fills[0].commission == pytest.approx(1.0)  # 50 shares * $0.01 < $1 minimum
    assert res.ledger.total_financing == pytest.approx(4 * 5000 * 0.001)


def test_trade_pnl_reconciles_with_equity(daily):
    spec = StrategySpec("donchian_breakout")
    strat, sizing = spec.build()
    bt = backtest_strategy(strat, {"SIMTEC": daily["SIMTEC"]}, sizing)
    led = bt.result.ledger
    last = float(daily["SIMTEC"]["close"].iloc[-1])
    realised = sum(t.pnl for t in led.closed_trades) + sum(
        t.realized_pnl - t.commission for t in led.open_trades.values()
    )
    total = realised + led.unrealized_pnl("SIMTEC", last) - led.total_financing
    assert bt.result.equity.iloc[-1] - bt.result.config.initial_cash == pytest.approx(total, abs=1e-6)


def test_buy_and_hold_benchmark_matches_price_change(daily):
    df = daily["SIMIDX"]
    res = run_backtest(
        {"SIMIDX": df},
        buy_and_hold_weights(df.index, ["SIMIDX"]),
        BacktestConfig(slippage_bps=0, fractional=True),
    )
    growth = res.equity.iloc[-1] / res.equity.iloc[0]
    # Shares are sized with the decision bar's close (the only price known then) and filled at
    # the next open, so growth = 1 + (last close - fill price) / decision close.
    c0, o1, c_last = df["close"].iloc[0], df["open"].iloc[1], df["close"].iloc[-1]
    assert growth == pytest.approx(1 + (c_last - o1) / c0, rel=1e-9)
    assert res.positions["SIMIDX"].iloc[1:].nunique() == 1  # bought once, never rebalanced


def test_metrics_on_known_curve():
    idx = pd.bdate_range("2020-01-01", periods=253, tz="UTC")
    eq = pd.Series(100 * 1.1 ** (np.arange(253) / 252), index=idx)
    m = performance_metrics(eq, 252)
    assert m["total_return"] == pytest.approx(0.1, rel=1e-6)
    assert m["cagr"] == pytest.approx(0.1, rel=1e-3)
    assert m["max_drawdown"] == pytest.approx(0.0)
    rows = monthly_returns(eq)
    assert rows[0]["year"] == 2020 and rows[0]["total"] is not None


def test_runner_payload_is_json_safe(daily):
    import json

    for spec, syms in [
        (StrategySpec("tsmom"), ["SIMIDX"]),
        (StrategySpec("pairs_trading"), ["SIMPRA", "SIMPRB"]),
        (StrategySpec("xs_momentum"), UNIVERSE),
    ]:
        strat, sizing = spec.build()
        bt = backtest_strategy(strat, {s: daily[s] for s in syms}, sizing, BacktestConfig(allow_short=True))
        payload = sanitize(backtest_payload(bt))
        text = json.dumps(payload, allow_nan=False)
        assert "NaN" not in text
        assert payload["equity"]["t"][0] == payload["start_time"]
        assert len(payload["charts"]) == (2 if spec.id == "pairs_trading" else 1)
        assert payload["metrics"]["sharpe"] is not None


def test_runner_warnings(daily):
    strat, sizing = StrategySpec("tsmom").build()
    bt = backtest_strategy(strat, {"SIMIDX": daily["SIMIDX"]}, sizing, BacktestConfig(allow_short=False))
    assert any("short" in w for w in bt.warnings)
    assert any("trades" in w for w in bt.warnings)  # TSMOM trades rarely: < 30 round trips
    with pytest.raises(ValueError, match="history"):
        backtest_strategy(strat, {"SIMIDX": daily["SIMIDX"].iloc[:100]}, sizing)


def test_grid_expansion():
    assert expand_grid({"a": [1, 2], "b": [3, 4, 5]}) == [
        {"a": 1, "b": 3},
        {"a": 1, "b": 4},
        {"a": 1, "b": 5},
        {"a": 2, "b": 3},
        {"a": 2, "b": 4},
        {"a": 2, "b": 5},
    ]
    assert range_values(10, 30, 10, True) == [10, 20, 30]
    assert range_values(0.5, 1.0, 0.25, False) == [0.5, 0.75, 1.0]
    with pytest.raises(ValueError):
        expand_grid({"a": list(range(30)), "b": list(range(30))})


def test_grid_search_reports_deflated_sharpe(daily):
    res = grid_search(
        "ma_crossover", {}, {"fast": [10, 20, 50], "slow": [100, 200]}, {"SIMIDX": daily["SIMIDX"]}
    )
    assert res["n_trials"] == 6 and len(res["rows"]) == 6
    best = max(r["metrics"]["sharpe"] for r in res["rows"])
    assert res["best"]["metrics"]["sharpe"] == pytest.approx(best)
    assert 0.0 <= res["deflated_sharpe"] <= res["psr_best"] <= 1.0
    assert res["interpretation"]


def test_grid_search_skips_invalid_combinations(daily):
    res = grid_search("ma_crossover", {}, {"fast": [50, 150], "slow": [100]}, {"SIMIDX": daily["SIMIDX"]})
    assert len(res["rejected"]) == 1 and res["n_trials"] == 1


def test_walk_forward_is_out_of_sample(daily):
    res = walk_forward(
        "ma_crossover",
        {},
        {"fast": [20, 50], "slow": [100, 200]},
        {"SIMIDX": daily["SIMIDX"]},
        train_bars=504,
        test_bars=126,
    )
    windows = res["windows"]
    assert len(windows) >= 3
    for w in windows:
        assert w["train_end"] < w["test_start"]  # parameters chosen before the test window
    # stitched out-of-sample curve covers every test window contiguously
    assert res["oos_equity"]["t"][1] == windows[0]["test_start"]
    assert res["oos_equity"]["t"][-1] == windows[-1]["test_end"]
    assert res["interpretation"]
