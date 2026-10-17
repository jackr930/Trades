"""The validation tools behind the acceptance checklist, each on inputs with a known answer."""

from __future__ import annotations

import copy
import math

import numpy as np
import pandas as pd
import pytest

from tests.helpers import make_bars
from trades.execution.ledger import Fill
from trades.strategies import StrategyConfig
from trades.strategies.base import Evidence, SingleAssetStrategy
from trades.validation import checklist, lookahead, null, robustness


def test_expectancy_and_its_t_statistic():
    e = robustness.expectancy([0.01, -0.005, 0.02, 0.0], r_multiples=[1.0, -0.5, 2.0, 0.0])
    assert e.n == 4 and e.mean == pytest.approx(0.00625)
    assert e.t_stat == pytest.approx(0.00625 / (np.std([0.01, -0.005, 0.02, 0.0], ddof=1) / 2))
    assert e.hit_rate == 0.5 and e.mean_r == pytest.approx(0.625)
    assert math.isnan(robustness.expectancy([]).mean)


def test_monte_carlo_drawdown_is_seeded_and_sane():
    assert robustness.monte_carlo_drawdown([0.01] * 50, 500)["p95"] == 0.0  # never a losing trade
    a = robustness.monte_carlo_drawdown([0.02, -0.01] * 50, 2000, seed=3)
    assert a == robustness.monte_carlo_drawdown([0.02, -0.01] * 50, 2000, seed=3)
    assert 0 < a["p50"] < a["p95"] <= a["p99"] <= a["worst"] < 1


def _variants(n_days: int, skill: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n_days)
    data = {f"v{i}": rng.normal(0.0, 0.01, n_days) for i in range(8)}
    data["v0"] = data["v0"] + skill  # one variant with a real, constant edge
    return pd.DataFrame(data, index=idx)


def test_pbo_separates_skill_from_noise():
    noise = robustness.probability_of_backtest_overfitting(_variants(1000, 0.0, 1), splits=10)
    skill = robustness.probability_of_backtest_overfitting(_variants(1000, 0.004, 1), splits=10)
    assert noise["combinations"] == 252  # C(10, 5)
    assert skill["pbo"] < 0.05 < noise["pbo"]
    assert robustness.probability_of_backtest_overfitting(_variants(1000, 0, 1)[["v0"]]) is None  # needs 2+ variants


def test_walk_forward_trades_only_unseen_windows():
    idx = pd.bdate_range("2020-01-01", periods=400)
    a = np.where(np.arange(400) < 200, 0.002, -0.002) + 0.0005 * np.sin(np.arange(400))
    b = -a + 0.0004 * np.cos(np.arange(400))
    wf = robustness.walk_forward(pd.DataFrame({"a": a, "b": b}, index=idx), train_days=100, test_days=50)
    assert wf["days"] == 300 and [w["variant"] for w in wf["windows"]][:2] == ["a", "a"]
    assert wf["windows"][-1]["variant"] == "b"  # it adapts after the regime change ...
    assert robustness.walk_forward(pd.DataFrame({"a": a}, index=idx), 380, 50) is None  # ... given enough data


def test_by_year_and_capacity():
    s = pd.Series([0.02, -0.01, 0.01, 0.03], index=pd.to_datetime(["2021-03-01", "2021-07-01", "2022-02-01", "2023-05-01"]))
    y = robustness.by_year(s)
    assert y["profitable_share"] == 1.0 and y["max_year_share"] == pytest.approx(0.03 / 0.05)
    df = make_bars(np.full(10, 100.0), volume=78_000.0)  # 200 shares a minute on a 390-minute day
    fills = [Fill(df.index[3], 3, "X", 4.0, 100.0, 0.0, 0.0, tag="market"), Fill(df.index[5], 5, "X", -4.0, 100.0, 0.0, 0.0, tag="close")]
    cap = robustness.participation(fills, {"X": df}, None)
    assert cap["n"] == 1 and cap["p95"] == pytest.approx(4 / 200)


def test_more_trials_deflate_the_sharpe_ratio():
    daily = pd.Series(np.random.default_rng(0).normal(0.001, 0.01, 750))
    one = robustness.deflated_sharpe(daily, [1.5], 1)
    many = robustness.deflated_sharpe(daily, list(np.linspace(-0.5, 1.6, 50)), 50)
    assert many["dsr"] < one["dsr"] and many["expected_max_sharpe"] > 0


class PeekingStrategy(SingleAssetStrategy):
    """Buys today if tomorrow's close is higher: the classic look-ahead bug."""

    id, name, category, summary = "peek", "Peek", "Test", "Uses tomorrow's close."
    rules_text, rationale, failure_modes = ("peek",), "none", "all"
    evidence, evidence_text, references = Evidence.EXPERIMENTAL, "none", ()

    def warmup(self) -> int:
        return 1

    def compute(self, df):
        return pd.DataFrame({"signal": (df["close"].shift(-1) > df["close"]).astype(float)}, index=df.index)


def test_lookahead_check_catches_a_strategy_that_peeks(daily):
    data = {"SIMTEC": daily["SIMTEC"].iloc[-300:]}
    ok, why = lookahead.check(PeekingStrategy(), data)
    assert not ok and "changed when later bars were added" in why
    ok, _ = lookahead.check(StrategyConfig("tsmom").build()[0], data)
    assert ok


def test_random_walk_bars_and_the_null_test():
    bars = null.random_walk_bars(["A", "B"], 5, sessions=20)
    assert len(bars["A"]) == 19 * 78 + 42  # 20 sessions, one of them Christmas Eve (a 13:00 close)
    assert (bars["A"]["high"] >= bars["A"][["open", "close"]].max(axis=1)).all()
    result = null.null_test(StrategyConfig("orb"), ["A"], 5, 252 * 78, sessions=120)
    assert result["passed"] and result["trades"] > 50


# --------------------------------------------------------------------------------------
# The checklist
# --------------------------------------------------------------------------------------

ACCEPT = {
    "min_oos_trades": 200, "min_oos_trades_one_per_day": 100, "min_expectancy_tstat": 2.0,
    "min_stressed_expectancy": 0.0, "min_walk_forward_sharpe": 0.75, "min_deflated_sharpe": 0.95,
    "max_pbo": 0.2, "locked_test_min_fraction": 0.5, "neighborhood_min_retained": 0.7,
    "max_drawdown_budget": 0.15, "min_profitable_year_share": 0.6, "max_single_year_profit_share": 0.5,
    "max_participation": 0.01,
}
GOOD = {
    "spec": {"frozen": True, "frozen_on": "2026-10-15", "drift": None, "committed": True},
    "universe": {"survivorship_free": True, "real_data": True, "provider": "alpaca"},
    "lookahead": {"passed": True, "detail": "unchanged"},
    "one_trade_per_day": True,
    "validation": {"expectancy": {"n": 480, "mean": 0.0008, "t_stat": 2.6}, "stressed_expectancy": {"n": 480, "mean": 0.0002}, "sharpe": 1.2},
    "walk_forward": {"sharpe": 0.9, "days": 900, "windows": [1, 2]},
    "deflated": {"dsr": 0.97, "sharpe": 1.1, "expected_max_sharpe": 0.4, "n_trials": 6},
    "pbo": {"pbo": 0.1, "combinations": 252},
    "locked_test": {"opened": False},
    "neighborhood": {"retained": 0.8, "detail": ""},
    "monte_carlo": {"p95": 0.09},
    "regimes": {"profitable_share": 0.75, "max_year_share": 0.3},
    "capacity": {"p95": 0.001},
    "reproducible": {"clean": True, "dirty": []},
}


def test_a_spec_that_passes_everything_still_waits_for_its_locked_test():
    checks = checklist.evaluate(GOOD, ACCEPT)
    assert [c.n for c in checks] == list(range(1, 15))
    assert [c.n for c in checks if c.status != "pass"] == [9]
    assert checklist.verdict(checks) == "ready_for_locked_test"
    opened = copy.deepcopy(GOOD)
    opened["locked_test"] = {"opened": True, "opened_on": "2026-11-02", "sharpe": 0.8, "validation_sharpe": 1.2}
    assert checklist.verdict(checklist.evaluate(opened, ACCEPT)) == "pass"
    opened["locked_test"]["sharpe"] = 0.5  # below 50% of the validation Sharpe: rejected, not retuned
    assert checklist.verdict(checklist.evaluate(opened, ACCEPT)) == "fail"


def test_pbo_not_computed_is_not_a_failure_but_everything_else_is():
    r = copy.deepcopy(GOOD)
    r["pbo"] = None
    assert checklist.verdict(checklist.evaluate(r, ACCEPT)) == "ready_for_locked_test"
    for path, value, n in (
        (("universe", "survivorship_free"), False, 2),
        (("validation", "expectancy", "n"), 99, 3),
        (("validation", "stressed_expectancy", "mean"), -0.0001, 5),
        (("deflated", "dsr"), 0.94, 7),
        (("monte_carlo", "p95"), 0.2, 11),
        (("reproducible", "clean"), False, 14),
    ):
        bad = copy.deepcopy(GOOD)
        target = bad
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        checks = checklist.evaluate(bad, ACCEPT)
        assert [c.n for c in checks if c.status == "fail"] == [n], path
        assert checklist.verdict(checks) == "fail"
