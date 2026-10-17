"""The simulated broker's working orders: protective stops, take-profits, stop entries, the
closing auction, regulatory fees and the cost stress. Every expected price is worked out by hand
from the rules in ``trades.execution.sim_broker``'s docstring."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from trades.engine.backtest import run_backtest
from trades.execution import costs
from trades.execution.sim_broker import BacktestConfig, ExecutionEngine

NAN = float("nan")
SLIP = 0.001  # 10 bps per fill
STOP_SLIP = 0.002  # +20 bps on stop fills


def cfg(**kw) -> BacktestConfig:
    base = dict(
        initial_cash=10_000.0,
        slippage_bps=10.0,
        stop_slippage_bps=20.0,
        borrow_bps_annual=0.0,
        margin_rate_annual=0.0,
        min_trade_weight=0.0,
    )
    return BacktestConfig(**{**base, **kw})


def bar(engine, t, o, h, lo, c, closing=False):
    return engine.execute(t, pd.Timestamp("2026-01-05 15:00", tz="UTC") + pd.Timedelta(minutes=5 * t),
                          [o], [h], [lo], [c], closing)


def test_market_entry_then_protective_stop_and_no_reentry():
    e = ExecutionEngine(["X"], cfg())
    bar(e, 0, 100, 100, 100, 100)
    e.decide(0, [1.0], stop_losses=[95.0])  # 10_000 / 100 = 100 shares at the next open, stop 95
    buy, stop = bar(e, 1, 100, 101, 94, 96)  # opens 100: buy; the low reaches 95: stopped
    assert buy.qty == 100 and buy.price == pytest.approx(100 * (1 + SLIP)) and buy.tag == "market"
    assert stop.tag == "stop" and stop.qty == -100
    assert stop.price == pytest.approx(95 * (1 - SLIP - STOP_SLIP))
    assert e.ledger.qty("X") == 0 and e.ambiguous_fills == 0
    # The signal still says long: the engine does not buy straight back into the stopped-out trade.
    assert e.decide(1, [1.0], stop_losses=[95.0]) == {}
    assert bar(e, 2, 97, 98, 96, 97) == []
    # Once the target goes flat (or reverses), entries are allowed again.
    e.decide(2, [0.0])
    e.decide(2, [1.0])
    (again,) = bar(e, 3, 98, 99, 97, 98)
    assert again.qty > 0


def test_gap_through_the_stop_fills_at_the_open():
    e = ExecutionEngine(["X"], cfg())
    bar(e, 0, 100, 100, 100, 100)
    e.decide(0, [1.0])
    bar(e, 1, 100, 100, 100, 100)
    e.decide(1, [1.0], stop_losses=[95.0])
    (fill,) = bar(e, 2, 93, 94, 90, 92)  # opened below the stop: out at the open
    assert fill.tag == "stop" and fill.price == pytest.approx(93 * (1 - SLIP - STOP_SLIP))


def test_take_profit_needs_trading_through_and_pays_no_slippage():
    e = ExecutionEngine(["X"], cfg())
    bar(e, 0, 100, 100, 100, 100)
    e.decide(0, [1.0])
    bar(e, 1, 100, 100, 100, 100)
    e.decide(1, [1.0], stop_losses=[90.0], take_profits=[110.0])
    assert bar(e, 2, 105, 110, 104, 108) == []  # touching 110 is not enough: orders queued there first
    e.decide(2, [1.0], stop_losses=[90.0], take_profits=[110.0])
    (fill,) = bar(e, 3, 108, 111, 107, 109)
    assert fill.tag == "take_profit" and fill.price == 110.0 and fill.slippage == 0.0


def test_when_one_bar_could_hit_both_the_stop_wins_and_is_counted():
    e = ExecutionEngine(["X"], cfg())
    bar(e, 0, 100, 100, 100, 100)
    e.decide(0, [1.0])
    bar(e, 1, 100, 100, 100, 100)
    e.decide(1, [1.0], stop_losses=[95.0], take_profits=[105.0])
    (fill,) = bar(e, 2, 100, 106, 94, 100)
    assert fill.tag == "stop" and e.ambiguous_fills == 1


def test_stop_entry_waits_for_the_level_and_fills_with_stop_costs():
    e = ExecutionEngine(["X"], cfg())
    bar(e, 0, 100, 100, 100, 100)
    e.decide(0, [1.0], stop_losses=[100.0], entry_stops=[105.0])
    assert e.working_orders()[0] == {"symbol": "X", "type": "stop_entry", "price": 105.0, "qty": 95.0}
    assert bar(e, 1, 101, 104, 100, 103) == []  # never reached 105
    e.decide(1, [1.0], stop_losses=[100.0], take_profits=[106.0], entry_stops=[105.0])
    (fill,) = bar(e, 2, 103, 107, 102, 106)  # reached 105; 107 > 106 but no take-profit in the entry bar
    assert fill.tag == "stop_entry" and fill.qty == 95
    assert fill.price == pytest.approx(105 * (1 + SLIP + STOP_SLIP))
    assert e.ledger.qty("X") == 95


def test_stop_entry_inside_a_bar_can_be_stopped_in_the_same_bar_and_is_counted():
    e = ExecutionEngine(["X"], cfg())
    bar(e, 0, 100, 100, 100, 100)
    e.decide(0, [1.0], stop_losses=[104.0], entry_stops=[105.0])
    entry, stop = bar(e, 1, 104.5, 106, 103, 104)  # reached 105 and 104: assume the stop came after the entry
    assert entry.price == pytest.approx(105 * (1 + SLIP + STOP_SLIP)) and stop.tag == "stop"
    assert e.ambiguous_fills == 1 and e.ledger.qty("X") == 0


def test_stop_entry_that_gaps_fills_at_the_open_and_the_whole_bar_follows_it():
    e = ExecutionEngine(["X"], cfg())
    bar(e, 0, 100, 100, 100, 100)
    e.decide(0, [1.0], stop_losses=[104.0], take_profits=[108.0], entry_stops=[105.0])
    entry, take = bar(e, 1, 106, 109, 105.5, 108)  # opened above 105: in at the open, then 109 > 108
    assert entry.price == pytest.approx(106 * (1 + SLIP + STOP_SLIP))
    assert take.tag == "take_profit" and take.price == 108.0 and e.ambiguous_fills == 0


def test_short_side_mirrors_long():
    e = ExecutionEngine(["X"], cfg())
    bar(e, 0, 100, 100, 100, 100)
    e.decide(0, [-1.0], stop_losses=[100.0], entry_stops=[95.0])
    (entry,) = bar(e, 1, 99, 99, 94, 96)
    assert entry.qty == -105 and entry.price == pytest.approx(95 * (1 - SLIP - STOP_SLIP))
    e.decide(1, [-1.0], stop_losses=[100.0], take_profits=[90.0])
    (cover,) = bar(e, 2, 96, 101, 95, 99)
    assert cover.tag == "stop" and cover.qty == 105 and cover.price == pytest.approx(100 * (1 + SLIP + STOP_SLIP))


def test_closing_auction_flattens_intraday_positions_and_nothing_carries_overnight():
    e = ExecutionEngine(["X"], cfg(flat_by_close=True))
    bar(e, 0, 100, 100, 100, 100)
    e.decide(0, [1.0])
    bar(e, 1, 100, 101, 99, 100)
    e.decide(1, [1.0])
    (close,) = bar(e, 2, 100, 102, 99, 101, closing=True)
    assert close.tag == "close" and close.price == pytest.approx(101 * (1 - SLIP)) and e.ledger.qty("X") == 0
    assert e.decide(2, [1.0], closing=True) == {}  # no order is queued for the next session's open
    assert bar(e, 3, 103, 104, 102, 103) == []


def test_regulatory_fees_are_charged_on_sales_only():
    c = cfg(sec_fee_per_million=20.60, taf_per_share=0.000195, taf_max_per_trade=9.79)
    assert c.commission(1000, 100.0) == 0.0
    assert c.commission(-1000, 100.0) == pytest.approx(100_000 * 20.60 / 1e6 + 1000 * 0.000195)
    assert c.commission(-100_000, 1.0) == pytest.approx(100_000 * 20.60 / 1e6 + 9.79)  # TAF capped


def test_a_stop_entry_respects_the_leverage_cap():
    e = ExecutionEngine(["X"], cfg(max_gross_leverage=1.0))
    bar(e, 0, 100, 100, 100, 100)
    e.decide(0, [3.0], stop_losses=[100.0], entry_stops=[105.0])  # asks for 3x equity
    (fill,) = bar(e, 1, 104, 106, 103, 105)
    assert fill.qty * 105 <= 10_000 * 1.0 + 1e-6


def test_levels_need_next_open_execution():
    e = ExecutionEngine(["X"], cfg(execution="next_close"))
    bar(e, 0, 100, 100, 100, 100)
    with pytest.raises(ValueError, match="next_open"):
        e.decide(0, [1.0], stop_losses=[95.0])


def test_cost_stress_multiplies_every_trading_cost():
    base = cfg(half_spread_bps=2, commission_per_share=0.005, sec_fee_per_million=20.6, taf_per_share=0.000195)
    double = costs.stressed(base, 2.0)
    for name in costs.STRESSED:
        assert getattr(double, name) == pytest.approx(2 * getattr(base, name))
    assert double.margin_rate_annual == base.margin_rate_annual  # financing is not a trading cost
    with pytest.raises(Exception, match="unknown cost"):
        costs.apply(base, {"slipage_bps": 3})
    assert "half spread 2 bps" in costs.describe(base)


def test_repository_cost_model_loads_from_base_yaml():
    model = costs.with_model(BacktestConfig(), {"half_spread_bps": 15})
    assert model.half_spread_bps == 15  # the spec's override wins
    assert model.sec_fee_per_million > 0 and model.taf_per_share > 0


def test_run_backtest_passes_levels_and_flattens_each_session():
    # Two 5-minute sessions; the strategy is long all day with a stop it never reaches.
    days = []
    for d in ("2026-01-05", "2026-01-06"):
        days.append(pd.date_range(f"{d} 14:30", periods=78, freq="5min", tz="UTC"))
    idx = days[0].append(days[1])
    px = np.linspace(100, 101, len(idx))
    df = pd.DataFrame({"open": px, "high": px + 0.05, "low": px - 0.05, "close": px, "volume": 1e5}, index=idx)
    w = pd.DataFrame({"X": 1.0}, index=idx)
    stops = pd.DataFrame({"X": 90.0}, index=idx)
    res = run_backtest({"X": df}, w, replace(cfg(), flat_by_close=True), stop_losses=stops)
    tags = [f.tag for f in res.fills]
    assert tags.count("close") == 2  # flat at each session's close
    assert res.positions["X"].iloc[77] == 0 and res.positions["X"].iloc[-1] == 0
    assert res.ambiguous_fills == 0
