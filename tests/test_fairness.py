"""Fair comparisons: T-bill cash yield, P(beats buy-and-hold), the research log, the decision rule, state tax."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_bars
from tests.test_journal import DAYS, _journal, _market
from trades.api import create_app
from trades.backtest import trials
from trades.backtest.engine import BacktestConfig, run_backtest
from trades.backtest.metrics import performance_metrics
from trades.backtest.tax import TaxProfile, after_tax
from trades.config import SettingsStore
from trades.core.ledger import Fill
from trades.core.stats import outperformance_probability
from trades.data.service import DataService
from trades.journal import rule as decision
from trades.journal.scorer import outcomes


def _flat(n=300):
    df = make_bars(np.full(n, 100.0), spread=0.0)
    return {"X": df}, df.index


# ------------------------------------------------------------------------------- cash yield


def test_idle_cash_earns_the_cash_series_and_borrowing_pays_it_plus_the_spread():
    data, idx = _flat()
    rf = pd.Series(0.0002, index=idx)  # about 5% a year
    flat = pd.DataFrame({"X": 0.0}, index=idx)
    cfg = BacktestConfig(initial_cash=10_000, slippage_bps=0, cash_returns=rf)
    res = run_backtest(data, flat, cfg)
    assert res.equity.iloc[-1] == pytest.approx(10_000 * 1.0002 ** len(idx))
    # 150% invested in a stock that never moves: the borrowed 50% pays T-bills plus the 2% spread.
    lever = pd.DataFrame({"X": 1.5}, index=idx)
    spread = 0.02 / 252
    res = run_backtest(data, lever, replace(cfg, max_gross_leverage=2.0))
    daily = res.equity.pct_change().iloc[5:].to_numpy()
    borrowed = (-res.cash / res.equity.shift(1)).iloc[5:].to_numpy()  # interest on the close's loan
    assert daily == pytest.approx(-borrowed * (0.0002 + spread), rel=1e-3)  # spread-only would be 30%+ off
    # Without a series nothing changes: cash earns cash_rate_annual (0 by default).
    assert run_backtest(data, flat, replace(cfg, cash_returns=None)).equity.iloc[-1] == 10_000
    assert "cash_returns" not in cfg.to_dict() and cfg.to_dict()["cash_yield"] is True


def test_sharpe_is_in_excess_of_the_cash_series():
    idx = pd.bdate_range("2020-01-01", periods=500, tz="UTC")
    rng = np.random.default_rng(3)
    rf = pd.Series(0.0002, index=idx)
    # Sitting in T-bills (tiny noise) has a big raw Sharpe but none in excess of T-bills.
    cash_like = pd.Series(10_000 * np.cumprod(1 + 0.0002 + rng.normal(0, 1e-6, 500)), index=idx)
    raw = performance_metrics(cash_like, 252)["sharpe"]
    excess = performance_metrics(cash_like, 252, risk_free=rf)["sharpe"]
    assert raw > 50 and abs(excess) < 3


def test_cash_returns_come_from_a_t_bill_etf(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.update({"csv_dir": str(tmp_path / "csv")})
    (tmp_path / "csv").mkdir()
    bil = make_bars(91.0 * 1.0002 ** np.arange(60), start="2024-01-02", spread=0.0)
    bil.index = bil.index.date  # CSV files carry plain dates
    bil.rename_axis("Date").reset_index().rename(columns=str.title).to_csv(tmp_path / "csv" / "BIL.csv", index=False)
    data = DataService(store)
    index = pd.bdate_range("2023-12-01", periods=80, tz="UTC")  # starts before BIL's history
    rets, note = data.cash_returns(index, "csv")
    assert rets.loc["2024-01-04"] == pytest.approx(0.0002, rel=1e-6)
    assert rets.loc["2023-12-15"] == 0.0 and "begins on 2024-01-02" in note
    assert data.cash_returns(index, "synthetic") == (None, data.cash_returns(index, "synthetic")[1])


# -------------------------------------------------------------------- P(beats buy-and-hold)


def test_outperformance_probability():
    rng = np.random.default_rng(1)
    b = rng.normal(0.0004, 0.01, 2000)
    assert outperformance_probability(b + 0.0005, b) == {"p_growth": 1.0, "p_sharpe": 1.0}
    assert outperformance_probability(b, b)["p_growth"] == 0.0  # a tie is not a win
    noisy = b + rng.normal(0, 0.004, 2000)
    assert outperformance_probability(noisy, b) == outperformance_probability(noisy, b)  # reproducible
    assert math.isnan(outperformance_probability(b[:20], b[:20])["p_growth"])  # too short to resample


# --------------------------------------------------------------------------- research log


def test_research_log_counts_distinct_configurations(tmp_path):
    path = tmp_path / "trials.jsonl"
    m = {"sharpe": 0.9, "skew": 0.0, "kurtosis": 3.0}
    trials.record("backtest", "tsmom", "a", ["SPY"], 0.9, path)
    first = trials.summarise(["SPY"], m, 2500, 252, path)
    assert first["configurations"] == 1 and "first configuration" in first["interpretation"]
    trials.record("backtest", "tsmom", "a", ["SPY"], 0.9, path)  # the same configuration again
    assert trials.summarise(["SPY"], m, 2500, 252, path)["configurations"] == 1
    for i, sr in enumerate([0.1, -0.3, 0.5, 0.2, -0.1, 0.4, 0.0, 0.3]):
        trials.record("grid", "tsmom", f"c{i}", ["SPY"], sr, path)
    trials.record("grid", "tsmom", "other", ["QQQ"], 2.0, path)  # other symbols do not count
    many = trials.summarise(["SPY"], m, 2500, 252, path)
    assert many["configurations"] == 9 and many["runs"] == 10
    assert many["deflated_sharpe"] < 0.99 and many["expected_max_sharpe"] > 0.3


# -------------------------------------------------------------------------- decision rule


def test_decision_rule_verdicts():
    rule = decision.load_rule(Path(__file__).resolve().parents[1] / "journal" / "decision_rule.json")
    assert {"backtest", "forward", "paper"} <= set(rule)
    scored = outcomes(_journal(), _market(), "SPY", DAYS[39])
    v = decision.forward_verdict(scored, rule["forward"])
    assert v["status"] == "NOT YET" and "1 independent days" in v["detail"]
    easy = {**rule["forward"], "min_independent_days": 1, "horizon": 5, "min_t": 2.0}
    assert decision.forward_verdict(scored, easy)["status"] == "PASS"  # Buy days +5%, +10%: t = 3
    hard = {**easy, "min_t": 4.0}
    assert decision.forward_verdict(scored, hard)["status"] == "FAIL"
    fills = pd.DataFrame({"slippage_bps": ["4", "6", "", "8"]})
    assert decision.paper_verdict(fills, {"min_fills": 3, "max_mean_slippage_bps": 10})["status"] == "PASS"
    assert decision.paper_verdict(fills, {"min_fills": 4, "max_mean_slippage_bps": 10})["status"] == "NOT YET"
    bt = rule["backtest"]
    assert decision.backtest_verdict({bt["metric"]: 0.08}, {bt["metric"]: 0.07}, 0.97, bt)["status"] == "PASS"
    assert decision.backtest_verdict({bt["metric"]: 0.08}, {bt["metric"]: 0.07}, 0.80, bt)["status"] == "FAIL"
    assert decision.changed_after({"last_changed": "2026-11-01T10:00:00Z"}, "2026-10-02")
    assert not decision.changed_after({"last_changed": "2026-10-01T10:00:00Z"}, "2026-10-02")


# ------------------------------------------------------------------------------ state tax


def test_state_tax_adds_to_both_rates():
    p = TaxProfile("taxable", 0.22, 0.15, 0.05)
    assert (p.short_term, p.long_term) == (pytest.approx(0.27), pytest.approx(0.20))
    idx = pd.bdate_range("2024-01-02", "2024-12-31", tz="UTC")
    fill = Fill(pd.Timestamp("2024-03-01", tz="UTC"), 0, "X", -1, 1, 0, 0, realized_pnl=1000.0, holding_days=30)
    assert after_tax(pd.Series(100_000.0, index=idx), [fill], p).taxes == {2024: pytest.approx(270.0)}
    with pytest.raises(ValueError):
        TaxProfile("taxable", 0.9, 0.15, 0.2)


def test_lab_reports_p_beats_and_the_research_log(tmp_path):
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as client:
        body = {"strategy": {"id": "faber_trend"}, "symbols": ["SIMIDX"], "start": "2016-01-04"}
        first = client.post("/api/backtest", json=body).json()
        assert 0 <= first["metrics"]["p_beats_growth"] <= 1
        assert first["research_log"]["configurations"] == 1
        assert any("no T-bill series" in w for w in first["warnings"])  # synthetic market
        body["strategy"]["params"] = {"sma_length": 150}
        second = client.post("/api/backtest", json=body).json()
        assert second["research_log"]["configurations"] == 2 and second["research_log"]["deflated_sharpe"] is not None
        assert client.put("/api/settings", json={"cash_yield": "maybe"}).status_code == 400
        assert client.put("/api/settings", json={"state_tax_rate": 0.05}).json()["state_tax_rate"] == 0.05
