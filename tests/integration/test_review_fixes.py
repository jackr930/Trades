"""Regression tests for problems found reviewing Parts 1-5 (PR #10), ported to this layout.

The paper trader's part (refused orders fail the run, orders already sent stay on record) is
covered for the current runner in tests/integration/test_paper.py.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tests.integration.test_journal import DAYS, _journal, _market
from trades.execution.ledger import Fill
from trades.journal.scorer import outcomes, read_journal
from trades.recommend import AdvisorSettings
from trades.recommend.recommender import suggest_position
from trades.reporting.tax import TaxProfile, after_tax
from trades.settings import SettingsStore
from trades.strategies.consensus import decide


def _fill(qty, pnl, days, commission=0.0, when="2024-06-03"):
    return Fill(pd.Timestamp(when, tz="UTC"), 0, "X", qty, 1, commission, 0, realized_pnl=pnl, holding_days=days)


def test_short_sale_gains_are_short_term_and_commissions_reduce_gains():
    idx = pd.bdate_range("2024-01-02", "2024-12-31", tz="UTC")
    equity = pd.Series(100_000.0, index=idx)
    p = TaxProfile("taxable", 0.30, 0.15)
    # Closing a long held 400 days: long-term. Closing a short (a buy) held 400 days: short-term.
    assert after_tax(equity, [_fill(-1, 1000.0, 400)], p).taxes == {2024: pytest.approx(150.0)}
    assert after_tax(equity, [_fill(+1, 1000.0, 400)], p).taxes == {2024: pytest.approx(300.0)}
    # $100 of commissions (one on opening, one on closing) come off a $1,000 short-term gain.
    fills = [_fill(+1, 0.0, None, commission=50.0), _fill(-1, 1000.0, 30, commission=50.0)]
    assert after_tax(equity, fills, p).taxes == {2024: pytest.approx(900 * 0.30)}


def test_the_benchmark_is_not_scored_against_itself():
    j = pd.concat([_journal(), pd.DataFrame([{**_journal().iloc[0].to_dict(), "symbol": "SPY"}])], ignore_index=True)
    scored = outcomes(j, _market(), "SPY", DAYS[39])
    assert scored[scored["symbol"] == "SPY"]["excess_5"].isna().all()
    assert scored[scored["symbol"] == "A"]["excess_5"].notna().any()


def test_journal_ids_and_symbols_stay_text(tmp_path):
    path = tmp_path / "j.csv"
    path.write_text(
        "session_date,symbol,label,votes,experiment_id,code_version,score,weight\n"
        '2025-01-02,NA,Buy,"{}",123456789012,1e5678901234,0.5,0.1\n'
    )
    j = read_journal(path)
    assert j.loc[0, "symbol"] == "NA" and j.loc[0, "experiment_id"] == "123456789012"
    assert j.loc[0, "code_version"] == "1e5678901234" and j.loc[0, "score"] == 0.5
    assert read_journal(tmp_path / "missing.csv").empty


def test_settings_are_stored_with_their_own_types(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    assert store.update({"account_equity": "25000"}).account_equity == 25_000.0
    assert store.update({"synthetic_seed": 5.0}).synthetic_seed == 5
    for bad in ({"fractional_shares": "false"}, {"account_equity": True}, {"synthetic_seed": 2.5}, {"provider": 3}):
        with pytest.raises(ValueError):
            store.update(bad)


def test_a_position_smaller_than_one_share_says_so():
    settings = AdvisorSettings(strategies=[], account_equity=2_000)
    d = decide({"X": [("Trend", 1.0)] * 2 + [("Trend", 0.0)] * 5}, {"X": 450.0}, {"X": 9.0}, settings.rules())["X"]
    pos = suggest_position(d, 450.0, settings)
    assert pos["shares"] == 0 and pos["held_weight"] == 0.0 and pos["weight"] > 0
    assert "less than one share" in pos["explanation"]


def test_a_backtest_without_a_start_date_loads_from_the_fixed_history_start(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from trades.api import create_app
    from trades.data.base import HISTORY_START
    from trades.data.service import DataService

    starts = []
    real = DataService.bars_many

    def spy(self, symbols, timeframe=None, start=None, *a, **kw):
        starts.append(start)
        return real(self, symbols, timeframe, start, *a, **kw)

    monkeypatch.setattr(DataService, "bars_many", spy)
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as client:
        r = client.post("/api/backtest", json={"strategy": {"id": "faber_trend"}, "symbols": ["SIMIDX"]})
        assert r.status_code == 200 and starts[0] == HISTORY_START  # not the provider's rolling window
