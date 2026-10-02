"""Robustness views, benchmark mixes, universes and the pre-registered variants."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_bars
from trades.api import create_app
from trades.backtest import robustness
from trades.backtest.engine import BacktestConfig
from trades.backtest.runner import BENCHMARKS, Benchmark, backtest_strategy, fixed_mix
from trades.journal.rule import backtest_verdict
from trades.strategies import create_strategy
from trades.universes import UNIVERSES

PPY = 252


def _curves(n=6 * PPY, edge=0.02, seed=5):
    """A benchmark path and a strategy that tracks it plus ``edge`` a year."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2012-01-02", periods=n, tz="UTC")
    rb = rng.normal(0.0003, 0.01, n)
    b = pd.Series(100 * np.exp(np.cumsum(rb)), index=idx)
    s = pd.Series(100 * np.exp(np.cumsum(rb + edge / PPY)), index=idx)
    return s, b


def test_rolling_windows_and_start_dates():
    s, b = _curves()
    roll = robustness.rolling_windows(s, b, PPY)
    assert roll["share_beating"] == 1.0 and roll["median_excess"] == pytest.approx(0.02, abs=0.005)
    assert roll["windows"] == len(range(0, len(s) - 3 * PPY, 63))
    starts = robustness.start_dates(s, b, PPY)
    assert starts["share_ahead"] == 1.0 and starts["starts"] == len(range(0, len(s) - 3 * PPY, 63))
    worse = robustness.rolling_windows(b, s, PPY)  # the other way round: never ahead
    assert worse["share_beating"] == 0.0


def test_regimes_label_the_benchmarks_own_drawdown():
    idx = pd.bdate_range("2012-01-02", periods=400, tz="UTC")
    # Up 50%, then down 40% (a bear market), then flat.
    path = np.concatenate([np.linspace(100, 150, 150), np.linspace(150, 90, 150), np.full(100, 90.0)])
    b = pd.Series(path, index=idx)
    rows = {r["regime"].split(" (")[0]: r for r in robustness.regimes(b, b, PPY)}
    assert rows["Bear market"]["share_of_time"] > 0.3 and rows["Near highs"]["benchmark"] > 0.4
    assert rows["Bear market"]["benchmark"] < 0  # lost money while more than 20% below the peak


def test_bootstrap_ranges_and_short_histories():
    s, b = _curves()
    boot = robustness.bootstrap_ranges(s, b, PPY)
    lo, mid, hi = boot["strategy"]["cagr"]
    assert lo < mid < hi and boot["strategy"]["max_drawdown"][2] <= 0
    assert boot["strategy"]["cagr"][1] > boot["benchmark"]["cagr"][1]  # the 2%-a-year edge shows in the median
    assert "two years" in robustness.report(s.iloc[:300], b.iloc[:300], PPY)["note"]


def test_fixed_mix_rebalances_monthly_and_never_invents_history():
    idx = pd.bdate_range("2020-01-02", periods=300, tz="UTC")
    up = make_bars(100 * 1.002 ** np.arange(300)).set_axis(idx)
    flat = make_bars(np.full(300, 50.0)).set_axis(idx)
    res, m = fixed_mix({"A": up, "B": flat}, {"A": 0.6, "B": 0.4}, BacktestConfig(slippage_bps=0), 0, 21)
    w = res.weights.iloc[25:]
    assert (w["A"] - 0.6).abs().max() < 0.05  # A keeps rising, yet monthly rebalancing holds it near 60%
    assert len([f for f in res.fills if f.symbol == "A"]) >= 10
    # A fund that did not exist yet is bought when it starts, not back-filled with a flat price.
    late = flat.copy()
    late.iloc[:100] = np.nan
    strat = create_strategy("buy_hold")
    bt = backtest_strategy(strat, {"A": up}, benchmark=Benchmark("mix", {"A": 0.5, "B": 0.5}, {"A": up, "B": late}))
    held_b = bt.benchmark.positions["B"]
    assert (held_b.iloc[:100] == 0).all() and held_b.iloc[150] > 0


def test_bonferroni_raises_the_bar_for_variants():
    rule = {"metric": "cagr", "must_beat": "SPY", "min_probability": 0.95}
    assert backtest_verdict({"cagr": 0.1}, {"cagr": 0.08}, 0.96, rule, 1)["status"] == "PASS"
    assert backtest_verdict({"cagr": 0.1}, {"cagr": 0.08}, 0.96, rule, 2)["status"] == "FAIL"  # needs 97.5%


def test_universes_and_benchmarks_are_offered(tmp_path):
    assert {"sectors", "countries", "multi_asset", "large_caps_2010"} <= set(UNIVERSES)
    assert all(abs(sum(b["weights"].values()) - 1) < 1e-9 for b in BENCHMARKS.values() if b["weights"])
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as client:
        meta = client.get("/api/meta").json()
        assert set(meta["universes"]) == set(UNIVERSES) and "60_40" in meta["benchmarks"]
        body = {
            "strategy": {"id": "faber_trend"},
            "symbols": ["SIMIDX"],
            "start": "2016-01-04",
            "config": {"benchmark": "60_40", "min_trade_weight": 0.02},
        }
        r = client.post("/api/backtest", json=body).json()
        assert r["benchmark"]["label"].startswith("60/40") and r["config"]["min_trade_weight"] == 0.02
        assert r["robustness"]["rolling"]["windows"] > 0 and r["robustness"]["regimes"]
        body["config"]["benchmark"] = "gold_bugs"
        assert client.post("/api/backtest", json=body).status_code == 400
