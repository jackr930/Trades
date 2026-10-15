"""Planning tools, checked by hand: goal paths, cost and tax drag, allocation, harvesting."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tests.test_ease_of_use import FIDELITY
from trades import planning
from trades.api import create_app


def _months(values, start="2010-01-31"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="ME", tz="UTC"))


def test_goal_paths_match_the_annuity_formula_when_every_month_is_the_same():
    r, n, c = 0.005, 120, 100.0
    out = planning.goal_paths(_months([r] * 60), 10_000, c, 10, goal=30_000, inflation=0.0)
    expected = 10_000 * (1 + r) ** n + c * ((1 + r) ** n - 1) / r
    assert out["p10"][-1] == pytest.approx(expected) and out["p90"][-1] == pytest.approx(expected)
    assert out["p_goal"] == 1.0 and out["contributed"] == 10_000 + 100 * 120 and len(out["years"]) == 11
    assert planning.goal_paths(_months([r] * 60), 10_000, c, 10, goal=40_000, inflation=0.0)["p_goal"] == 0.0
    # In today's dollars: a 0.5% monthly return with 6.17% inflation (0.5% a month) is worth nothing in real terms.
    real = planning.goal_paths(_months([r] * 60), 10_000, 0.0, 10, goal=0, inflation=1.005**12 - 1)
    assert real["p50"][-1] == pytest.approx(10_000)


def test_goal_paths_spread_out_when_returns_vary():
    rng = np.random.default_rng(0)
    out = planning.goal_paths(_months(rng.normal(0.006, 0.04, 240)), 10_000, 200, 20, goal=100_000)
    assert out["p10"][-1] < out["p50"][-1] < out["p90"][-1] and 0 < out["p_goal"] < 1
    with pytest.raises(ValueError, match="two years"):
        planning.goal_paths(_months([0.01] * 12), 1, 0, 5, 0)


def test_history_stats_find_the_worst_fall_and_the_recovery():
    # Up 10% in month 1, then down 50%, then back up to the old peak over two months.
    mix = _months([0.10, -0.50, 0.40, 0.4286, 0.0])
    h = planning.history_stats(mix, 10_000)
    assert h["max_drawdown"] == pytest.approx(-0.5) and h["start_value_at_trough"] == pytest.approx(5_000)
    assert (h["peak"], h["trough"], h["recovered"]) == ("2010-01", "2010-02", "2010-04")
    assert h["months_below_peak"] == 3
    unrecovered = planning.history_stats(_months([0.1, -0.5, 0.1]), 1)
    assert unrecovered["recovered"] is None and unrecovered["months_below_peak"] == 2


def test_drag_by_hand():
    plain = planning.drag(10_000, 10, 0.07, taxable=False)
    assert plain["final"] == pytest.approx(10_000 * 1.07**10) and plain["lost"] == pytest.approx(0)
    # Buy and hold in a taxable account: no tax as you go, long-term tax on the gain at the end.
    hold = planning.drag(10_000, 10, 0.07, short_rate=0.3, long_rate=0.15)
    gross = 10_000 * 1.07**10
    assert hold["rows"][3]["value"] == pytest.approx(gross)
    assert hold["final"] == pytest.approx(gross - (gross - 10_000) * 0.15)
    # Turnover above 100%: every year's gain is short-term and taxed as it comes.
    churn = planning.drag(10_000, 1, 0.10, turnover=2.0, trade_cost_bps=0, short_rate=0.3, long_rate=0.15)
    assert churn["rows"][3]["value"] == pytest.approx(11_000 - 1_000 * 0.3) and churn["final"] == churn["rows"][3]["value"]
    # Fees and trading costs come off the return: 1% fees and 2 x 100% x 25 bps of trading.
    costly = planning.drag(10_000, 1, 0.10, expense_ratio=0.01, turnover=1.0, trade_cost_bps=25, taxable=False)
    assert [r["value"] for r in costly["rows"]] == pytest.approx([11_000, 10_900, 10_850])


def test_asset_classes():
    assert planning.asset_class("AGG") == ("bonds", False) and planning.asset_class("VNQ") == ("real_estate", False)
    assert planning.asset_class("VBMFX", "VANGUARD TOTAL BOND MARKET INDEX") == ("bonds", False)
    assert planning.asset_class("XYZ") == ("stocks", True)


def _h(account, symbol, value, cost=None, cash=False):
    return {"account": account, "symbol": symbol, "value": value, "cost_basis": cost, "cash": cash, "quantity": 1.0}


def test_allocation_moves_and_location():
    holdings = [_h("Brokerage", "VTI", 50_000), _h("Brokerage", "BND", 30_000), _h("Roth IRA", "VOO", 20_000)]
    types = {"Brokerage": "taxable", "Roth IRA": "tax_advantaged"}
    out = planning.allocation(holdings, types, {"stocks": 0.8, "bonds": 0.2}, band=0.05, short_rate=0.24, long_rate=0.15)
    rows = {r["class"]: r for r in out["rows"]}
    assert rows["stocks"]["share"] == pytest.approx(0.7) and rows["bonds"]["difference"] == pytest.approx(-10_000)
    moves = {m["class"]: m for m in out["moves"]}
    assert moves["stocks"]["action"] == "add" and moves["bonds"]["amount"] == pytest.approx(-10_000)
    assert "taxable" in moves["bonds"]["where"]  # no bonds inside the IRA to sell
    # $20,000 of the taxable bonds could swap with the IRA's stocks: 20,000 x (4% x 24% - 1.5% x 15%).
    assert "$147 a year" in out["location"][0]
    with pytest.raises(ValueError):
        planning.allocation([], types, {"stocks": 1.0})


def test_harvest_candidates_and_wash_sale_warnings():
    holdings = [
        _h("Brokerage", "VOO", 8_000, 10_000),
        _h("Brokerage", "AAPL", 9_950, 10_000),  # a $50 loss: below the threshold
        _h("Roth IRA", "VOO", 5_000, 9_000),  # losses in an IRA cannot be harvested
        _h("Brokerage", "CASH", 1_000, 1_000, cash=True),
    ]
    types = {"Brokerage": "taxable", "Roth IRA": "tax_advantaged"}
    (c,) = planning.harvest(holdings, types, short_rate=0.3, long_rate=0.15)
    assert (c["symbol"], c["loss"], c["replacement"]) == ("VOO", 2_000, "VTI")
    assert c["tax_deferred"] == pytest.approx([300, 600]) and len(c["warnings"]) == 2  # also held in the IRA


def test_planning_api(tmp_path):
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as client:
        goal = client.post(
            "/api/plan/goal", json={"start_value": 10_000, "monthly": 500, "years": 15, "goal": 150_000}
        ).json()
        assert goal["demo"] and goal["proxies"] == {"stocks": "SIMIDX", "bonds": "SIMBND"}
        assert goal["p10"][-1] < goal["p90"][-1] and goal["history"]["max_drawdown"] < 0
        cash = client.post("/api/plan/goal", json={"start_value": 1000, "years": 5, "mix": {"cash": 1}}).json()
        assert cash["p50"][-1] == pytest.approx(1000 / 1.025**5, rel=1e-3)  # the demo's cash earns nothing
        drag = client.post("/api/plan/drag", json={"amount": 10_000, "years": 20, "turnover": 3.0}).json()
        assert drag["scenario"]["final"] < drag["index_fund"]["final"] and drag["taxable"]
        client.post("/api/holdings/import", json={"text": FIDELITY})
        alloc = client.post("/api/plan/allocation", json={"targets": {"stocks": 0.6, "bonds": 0.4}}).json()
        assert alloc["total"] == pytest.approx(500.25 + 1600 + 1822.14) and alloc["moves"]
        assert client.get("/api/plan/harvest").json()["candidates"] == []  # nothing below cost in the sample
