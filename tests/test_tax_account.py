"""Account profile: fractional shares, after-tax results (worked by hand) and cost sensitivity."""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trades.advisor import AdvisorSettings
from trades.advisor.recommender import suggest_position
from trades.api import create_app
from trades.arena.run import RunConfig
from trades.backtest.engine import BacktestConfig
from trades.backtest.runner import backtest_strategy
from trades.backtest.tax import AfterTax, TaxProfile, after_tax, liquidation, net_year
from trades.core.ledger import Fill, Ledger
from trades.sim.session import SimConfig
from trades.strategies import create_strategy
from trades.strategies.consensus import ConsensusRules, decide

TAXABLE = TaxProfile("taxable", 0.22, 0.15)


def _fill(day: str, realized: float, held: float) -> Fill:
    t = pd.Timestamp(day, tz="UTC")
    return Fill(t, 0, "X", -1.0, 1.0, 0.0, 0.0, realized_pnl=realized, holding_days=held)


def test_ledger_dates_each_gain_by_the_fill_that_realized_it():
    led = Ledger(100_000)
    t0 = pd.Timestamp("2023-01-03", tz="UTC")
    led.apply_fill(time=t0, index=0, symbol="X", qty=10, price=100.0)
    led.apply_fill(time=t0 + pd.Timedelta(days=100), index=1, symbol="X", qty=10, price=110.0)
    sell = led.apply_fill(time=t0 + pd.Timedelta(days=450), index=2, symbol="X", qty=-20, price=120.0)
    # Average cost 105 -> 20 x 15 = 300; shares bought on average on day 50 -> held 400 days.
    assert sell.realized_pnl == pytest.approx(300.0) and sell.holding_days == pytest.approx(400.0)
    assert led.fills[0].realized_pnl == 0.0 and led.fills[0].holding_days is None


def test_net_year_offsets_and_carries_losses_of_the_right_kind():
    assert net_year(1000, -400, 0, 0) == (600, 0, 0, 0)  # long-term loss offsets a short-term gain
    assert net_year(-3000, 1000, 0, 0) == (0, 0, -2000, 0)  # leftover loss stays short-term
    assert net_year(5000, 0, -2000, 0) == (3000, 0, 0, 0)  # carried loss used the next year
    assert net_year(-100, -200, -50, 0) == (0, 0, -150, -200)


def test_after_tax_by_hand():
    idx = pd.bdate_range("2024-01-02", "2026-03-31", tz="UTC")
    equity = pd.Series(100_000.0, index=idx)  # flat, so each year's tax is easy to read off
    fills = [
        _fill("2024-03-01", 1000, 30),  # short-term gain
        _fill("2024-06-03", -400, 400),  # long-term loss -> 2024 taxable: 600 short-term -> 132 tax
        _fill("2025-02-03", -3000, 20),  # short-term loss
        _fill("2025-05-01", 1000, 500),  # long-term gain -> 2025: no tax, 2,000 short-term loss carried
        _fill("2026-02-02", 5000, 10),  # 2026: 5,000 - 2,000 = 3,000 short-term -> 660 tax
    ]
    res = after_tax(equity, fills, TAXABLE)
    assert res.taxes == {2024: pytest.approx(132.0), 2025: 0.0, 2026: pytest.approx(660.0)}
    # Tax leaves the account at each year's last bar, as a share of the (flat) pre-tax equity.
    assert res.equity.loc["2024-12-30"] == pytest.approx(100_000.0)
    assert res.equity.loc["2024-12-31"] == pytest.approx(99_868.0)
    assert res.equity.iloc[-1] == pytest.approx(100_000 * (1 - 0.00132) * (1 - 0.0066))
    # A tax-advantaged account pays nothing as it goes.
    assert after_tax(equity, fills, TaxProfile("tax_advantaged")).equity.equals(equity)


def test_liquidation_taxes_open_gains_with_the_last_year():
    led = Ledger(0)
    led.apply_fill(time=pd.Timestamp("2023-01-03", tz="UTC"), index=0, symbol="X", qty=100, price=50.0)
    end = pd.Timestamp("2025-01-02", tz="UTC")
    equity = pd.Series([5000.0, 8000.0], index=[pd.Timestamp("2023-01-03", tz="UTC"), end])
    # Held two years: the 3,000 gain is long-term -> 450 tax, 5.625% of the 8,000 account.
    sold = liquidation(AfterTax(equity), led, {"X": 80.0}, end, TAXABLE, 8000.0)
    assert sold == pytest.approx(8000 - 450)
    # A realized short-term loss earlier in the same last year shrinks that tax.
    sold = liquidation(AfterTax(equity, last_year=(-1000.0, 0.0)), led, {"X": 80.0}, end, TAXABLE, 8000.0)
    assert sold == pytest.approx(8000 - 2000 * 0.15)
    assert liquidation(AfterTax(equity), led, {"X": 80.0}, end, TaxProfile("tax_advantaged"), 8000.0) == 8000


def test_backtest_reports_after_tax_results(daily):
    data = {"SIMIDX": daily["SIMIDX"]}
    strat = create_strategy("rsi2_reversion")
    bt = backtest_strategy(strat, data, tax=TAXABLE)
    m, b = bt.metrics, bt.benchmark_metrics
    assert m["taxes_paid"] > 0 and m["after_tax_cagr"] < m["cagr"]
    # Buy-and-hold realizes nothing, so it pays tax only if sold at the end.
    assert b["taxes_paid"] == 0 and b["after_tax_cagr"] == pytest.approx(b["cagr"])
    assert b["after_tax_cagr_if_sold"] < b["cagr"]
    sheltered = backtest_strategy(strat, data, tax=TaxProfile("tax_advantaged"))
    assert sheltered.metrics["after_tax_cagr"] == pytest.approx(sheltered.metrics["cagr"])
    assert sheltered.metrics["after_tax_cagr_if_sold"] == pytest.approx(sheltered.metrics["cagr"])


def test_live_desk_respects_fractional_shares():
    votes, prices, atrs = {"X": [("Trend", 1.0), ("Trend", 0.0)]}, {"X": 33.0}, {"X": 3.3}
    d = decide(votes, prices, atrs, ConsensusRules())["X"]  # 0.5 x min(1% x 33 / 6.6, 20%) = 2.5%
    whole = suggest_position(d, 33.0, AdvisorSettings(strategies=[], account_equity=1000))
    part = suggest_position(d, 33.0, AdvisorSettings(strategies=[], account_equity=1000, fractional=True))
    assert whole["shares"] == 0  # $25 buys no whole $33 share
    assert part["shares"] == pytest.approx(25 / 33, abs=1e-6) and part["weight"] == pytest.approx(0.025)


def test_simulators_accept_a_100_dollar_account():
    assert SimConfig(initial_cash=100).initial_cash == 100
    assert RunConfig(initial_cash=100).initial_cash == 100
    with pytest.raises(ValueError):
        SimConfig(initial_cash=99)
    with pytest.raises(ValueError):
        RunConfig(initial_cash=99)


def test_lab_backtest_uses_the_account_profile_and_reruns_costs(tmp_path):
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as client:
        r = client.put("/api/settings", json={"account_equity": 2500, "fractional_shares": True, "short_term_tax_rate": 0.3})
        assert r.status_code == 200
        assert client.put("/api/settings", json={"account_type": "roth"}).status_code == 400
        body = client.post(
            "/api/backtest", json={"strategy": {"id": "rsi2_reversion"}, "symbols": ["SIMIDX"], "start": "2018-01-02"}
        ).json()
        assert body["config"]["initial_cash"] == 2500 and body["config"]["fractional"] is True
        assert body["tax"]["short_term_rate"] == 0.3 and body["after_tax_equity"] is not None
        cs = body["cost_sensitivity"]
        assert [(row["multiplier"], row["slippage_bps"]) for row in cs["rows"]] == [(1, 5), (2, 10), (4, 20)]
        assert cs["rows"][0]["cagr"] >= cs["rows"][1]["cagr"] >= cs["rows"][2]["cagr"]
        assert "double costs" in cs["verdict"]


def test_backtest_config_starting_cash_minimum():
    assert BacktestConfig(initial_cash=100).initial_cash == 100
