"""Paper broker, replay sessions and the scorecard."""

from __future__ import annotations

import pandas as pd
import pytest

from trades.sim.broker import Bar, OrderError, PaperBroker, Status
from trades.sim.session import SessionStore, SimConfig, SimError, create_session

T0 = pd.Timestamp("2024-01-02", tz="UTC")


def broker(**kw) -> PaperBroker:
    b = PaperBroker(10_000, slippage_bps=kw.pop("slippage_bps", 0), **kw)
    b.last_prices["X"] = 100.0
    return b


def test_market_order_fills_next_open_with_slippage():
    b = PaperBroker(10_000, slippage_bps=10)
    b.last_prices["X"] = 100.0
    b.submit("X", "buy", 10, index=0, time=T0)
    assert b.process_bar(0, T0, {"X": Bar(101, 102, 99, 100)}) == []  # not eligible on the same bar
    (fill,) = b.process_bar(1, T0, {"X": Bar(101, 102, 99, 100)})
    assert fill["price"] == pytest.approx(101 * 1.001)
    assert b.position("X") == 10


def test_limit_orders_fill_at_limit_or_better_on_gaps():
    b = broker()
    b.submit("X", "buy", 5, index=0, time=T0, type="limit", limit_price=95)
    assert not b.process_bar(1, T0, {"X": Bar(100, 101, 96, 97)})  # low never reached 95
    (fill,) = b.process_bar(2, T0, {"X": Bar(97, 98, 94, 96)})
    assert fill["price"] == 95
    b2 = broker()
    b2.submit("X", "buy", 5, index=0, time=T0, type="limit", limit_price=95)
    (gap,) = b2.process_bar(1, T0, {"X": Bar(90, 92, 89, 91)})
    assert gap["price"] == 90  # gapped below the limit: filled at the better open


def test_stop_loss_gap_and_bracket_oco():
    b = broker()
    b.submit("X", "buy", 10, index=0, time=T0, stop_loss=95, take_profit=110)
    b.process_bar(1, T0, {"X": Bar(100, 101, 99, 100)})
    children = [o for o in b.orders.values() if o.parent_id]
    assert all(o.status is Status.OPEN for o in children)
    (fill,) = b.process_bar(2, T0, {"X": Bar(90, 92, 88, 91)})  # gaps through the stop
    assert fill["tag"] == "stop_loss" and fill["price"] == 90
    tp = next(o for o in children if o.tag == "take_profit")
    assert tp.status is Status.CANCELED  # one-cancels-other
    assert b.position("X") == 0
    trade = b.ledger.closed_trades[-1]
    assert "stop_loss" in trade.tags


def test_stop_loss_wins_when_both_brackets_trigger_in_one_bar():
    b = broker()
    b.submit("X", "buy", 10, index=0, time=T0, stop_loss=95, take_profit=105)
    b.process_bar(1, T0, {"X": Bar(100, 100.5, 99.5, 100)})
    (fill,) = b.process_bar(2, T0, {"X": Bar(100, 106, 94, 100)})
    assert fill["tag"] == "stop_loss"  # conservative assumption


def test_bracket_can_trigger_on_entry_bar_for_market_orders():
    b = broker()
    b.submit("X", "buy", 10, index=0, time=T0, stop_loss=95)
    fills = b.process_bar(1, T0, {"X": Bar(100, 101, 94, 96)})
    assert [f["tag"] for f in fills] == ["entry", "stop_loss"]


def test_shorting_and_buying_power_rules():
    b = broker()
    with pytest.raises(OrderError, match="Short selling is disabled"):
        b.submit("X", "sell", 1, index=0, time=T0)
    with pytest.raises(OrderError, match="buying power"):
        b.submit("X", "buy", 1000, index=0, time=T0)
    with pytest.raises(OrderError, match="stop-loss must be below"):
        b.submit("X", "buy", 1, index=0, time=T0, stop_loss=120)
    with pytest.raises(OrderError, match="whole shares"):
        b.submit("X", "buy", 1.5, index=0, time=T0)
    s = broker(allow_short=True)
    s.submit("X", "sell", 10, index=0, time=T0)
    s.process_bar(1, T0, {"X": Bar(100, 100, 100, 100)})
    assert s.position("X") == -10


def test_fill_time_buying_power_check_limits_quantity():
    b = broker()
    b.submit("X", "buy", 99, index=0, time=T0)
    (fill,) = b.process_bar(1, T0, {"X": Bar(200, 200, 200, 200)})  # price doubled overnight
    assert fill["qty"] == 50


def test_day_orders_expire_and_cancel_cascades():
    b = broker()
    o = b.submit("X", "buy", 1, index=0, time=T0, type="limit", limit_price=50, tif="day")
    b.process_bar(1, T0, {"X": Bar(100, 100, 99, 100)})
    assert o.status is Status.EXPIRED
    parent = b.submit("X", "buy", 1, index=1, time=T0, type="limit", limit_price=50, stop_loss=40)
    b.cancel(parent.id)
    assert all(x.status is Status.CANCELED for x in b.orders.values() if x.parent_id == parent.id)


# ----------------------------------------------------------------------------- sessions


def test_session_hides_the_future_and_steps():
    s = create_session(SimConfig(scenario="steady_bull", seed=5, length_bars=60, warmup_bars=100))
    st = s.state()
    assert len(st["bars"]["t"]) == s.cursor + 1 == 100
    assert st["progress"] == {"done": 0, "total": 60}
    s.place_order(side="buy", qty=100, note="test entry")
    s.step(1)
    inc = s.state(since=99)
    assert inc["bars_from"] == 100 and len(inc["bars"]["t"]) == 1
    assert inc["account"]["position"]["qty"] == 100
    s.step(500)
    assert s.finished and s.scorecard is not None
    full = s.state()
    assert len(full["bars"]["t"]) == s.end_index + 1  # the rest is revealed after the end
    with pytest.raises(SimError):
        s.step(1)


def test_session_rejects_bad_orders_and_config():
    s = create_session(SimConfig(scenario="range", seed=2, length_bars=30, warmup_bars=100))
    with pytest.raises(SimError):
        s.place_order(side="sell", qty=10)
    with pytest.raises(ValueError):
        SimConfig(reveal="sometimes")
    with pytest.raises(ValueError):
        SimConfig(scenario="nope")


def test_advisor_ghosts_start_with_the_player():
    s = create_session(SimConfig(scenario="random", seed=9, length_bars=40, warmup_bars=260))
    for a in s.advisors:
        eq = a["result"].equity
        assert (eq.iloc[: s.start_index + 1] == s.config.initial_cash).all()
    assert s.benchmark.equity.iloc[s.start_index] == s.config.initial_cash
    names = [a["strategy"].id for a in s.advisors]
    assert "tsmom" in names
    tsmom = next(a for a in s.advisors if a["strategy"].id == "tsmom")
    assert tsmom["strategy"].params["long_only"] is True  # shorting is off in this session


def test_scorecard_flags_bad_habits():
    s = create_session(SimConfig(scenario="whipsaw", seed=4, length_bars=120, warmup_bars=100))
    # Over-sized, unprotected trades that are closed quickly when winning and held when losing.
    for _ in range(6):
        if s.finished:
            break
        price = float(s.bars["close"].iloc[s.cursor])
        qty = int(s.broker.buying_power() * 0.95 / price)
        s.place_order(side="buy", qty=qty)
        s.step(1)
        held = 0
        while not s.finished and held < 15:
            s.step(1)
            held += 1
            pos = s.broker.ledger.positions["SIM"]
            if s.broker.ledger.unrealized_pnl("SIM", float(s.bars["close"].iloc[s.cursor])) > 0 or held >= 15:
                break
            if abs(pos.qty) == 0:
                break
        if not s.finished and s.broker.position("SIM"):
            s.close_position()
            s.step(1)
    card = s.finish()
    diags = {d["id"]: d for d in card["diagnostics"]}
    assert diags["stops"]["status"] == "bad"
    assert diags["sizing"]["status"] in ("warn", "bad")
    assert card["process_score"] < 60
    assert card["leaderboard"][0]["total_return"] >= card["leaderboard"][-1]["total_return"]
    assert {r["kind"] for r in card["leaderboard"]} >= {"you", "benchmark", "strategy"}


def test_scenario_reveal_contains_regimes():
    s = create_session(SimConfig(scenario="crash", seed=1, length_bars=100, warmup_bars=250))
    s.finish()
    reveal = s.state()["reveal"]
    assert reveal["kind"] == "scenario" and reveal["regimes"]
    assert reveal["regimes"][0]["start"] == 0
    assert all(r["end"] <= 100 for r in reveal["regimes"])


def test_history_session_blind_mode(provider):
    class Service:
        class settings:
            @staticmethod
            def get():
                from trades.config import Settings

                return Settings(provider="yahoo")

        @staticmethod
        def bars(symbol, timeframe, start, end, provider_id):
            df = provider.history("SIMTEC")
            return df[(df.index.date >= start) & (df.index.date <= end)]

    cfg = SimConfig(source="history", symbol="SIMTEC", start="2020-03-02", length_bars=50, blind=True)
    s = create_session(cfg, Service())
    assert s.symbol == "MYSTERY"
    assert s.bars["close"].iloc[s.start_index] == pytest.approx(100.0)
    assert s.bars.index[0].year == 2040
    s.finish()
    rv = s.state()["reveal"]
    assert rv["symbol"] == "SIMTEC" and rv["first_date"] < "2020-03-02" <= rv["last_date"]


def test_session_store_history(tmp_path):
    store = SessionStore(max_sessions=2, history_path=tmp_path / "h.jsonl")
    sessions = [
        store.add(create_session(SimConfig(scenario="range", seed=i, length_bars=20, warmup_bars=60)))
        for i in range(3)
    ]
    with pytest.raises(KeyError):
        store.get(sessions[0].id)  # evicted
    sessions[2].finish()
    store.record(sessions[2])
    (row,) = store.history()
    assert row["id"] == sessions[2].id and row["process_score"] is not None or row["trades"] == 0


# ----------------------------------------------------------------------------- reviewer regressions


def test_going_flat_keeps_brackets_of_an_unfilled_entry():
    b = broker()
    b.submit("X", "buy", 50, index=0, time=T0)
    b.process_bar(1, T0, {"X": Bar(100, 101, 99, 100)})
    parent = b.submit(
        "X", "buy", 10, index=1, time=T0, type="limit", limit_price=95, stop_loss=90, take_profit=110
    )
    b.submit("X", "sell", 50, index=1, time=T0)  # close the existing position
    b.process_bar(2, T0, {"X": Bar(100, 101, 99, 100)})
    assert b.position("X") == 0
    children = [o for o in b.orders.values() if o.parent_id == parent.id]
    assert {o.status for o in children} == {Status.PENDING}  # still waiting for their entry
    b.process_bar(3, T0, {"X": Bar(96, 97, 94, 95)})  # limit trades through 95
    assert b.position("X") == 10
    assert {o.status for o in children} == {Status.OPEN}  # the new position is protected


def test_limit_orders_need_a_trade_through():
    b = broker()
    b.submit("X", "buy", 5, index=0, time=T0, type="limit", limit_price=95)
    assert not b.process_bar(1, T0, {"X": Bar(100, 101, 95, 97)})  # touched, not traded through
    (fill,) = b.process_bar(2, T0, {"X": Bar(97, 98, 94.9, 96)})
    assert fill["price"] == 95


def test_expired_or_rejected_parents_cancel_their_children():
    b = broker()
    day = b.submit("X", "buy", 1, index=0, time=T0, type="limit", limit_price=50, tif="day", stop_loss=40)
    b.process_bar(1, T0, {"X": Bar(100, 100, 99, 100)})
    assert day.status is Status.EXPIRED
    kids = [o for o in b.orders.values() if o.parent_id == day.id]
    assert kids and all(o.status is Status.CANCELED and o.closed_index == 1 for o in kids)

    r = broker()
    parent = r.submit("X", "buy", 99, index=0, time=T0, stop_loss=90)
    r.process_bar(1, T0, {"X": Bar(20_000, 20_000, 20_000, 20_000)})  # cannot afford a single share
    assert parent.status is Status.REJECTED
    assert all(o.status is Status.CANCELED for o in r.orders.values() if o.parent_id == parent.id)


def test_children_of_a_limit_filled_at_the_open_can_trigger_that_bar():
    b = broker()
    b.submit("X", "buy", 10, index=0, time=T0, type="limit", limit_price=95, stop_loss=85)
    fills = b.process_bar(1, T0, {"X": Bar(90, 91, 80, 82)})  # gaps below the limit, then collapses
    assert [f["tag"] for f in fills] == ["entry", "stop_loss"]
    assert fills[0]["price"] == 90 and b.position("X") == 0


def test_excursions_use_fill_prices_and_only_post_fill_range():
    b = broker()
    b.submit("X", "buy", 10, index=0, time=T0, type="limit", limit_price=95)
    b.process_bar(1, T0, {"X": Bar(100, 110, 90, 96)})  # filled at 95 on the way down
    t = b.ledger.open_trades["X"]
    assert t.max_adverse == pytest.approx(90 / 95 - 1)  # the low came after the fill
    assert t.max_favorable == pytest.approx(96 / 95 - 1)  # the 110 high may predate the entry
    b.submit("X", "sell", 10, index=1, time=T0)
    b.process_bar(2, T0, {"X": Bar(120, 121, 119, 120)})  # gap up: exit at the open
    closed = b.ledger.closed_trades[-1]
    assert closed.max_favorable == pytest.approx(120 / 95 - 1)  # exit price counts


def test_close_position_keeps_pending_brackets():
    s = create_session(SimConfig(scenario="range", seed=3, length_bars=40, warmup_bars=100))
    s.place_order(side="buy", qty=10)
    price = float(s.bars["close"].iloc[s.cursor])
    s.step(1)
    parent = s.place_order(
        side="buy", qty=5, type="limit", limit_price=round(price * 0.5, 2), stop_loss=round(price * 0.4, 2)
    )
    s.close_position()
    s.step(1)
    assert s.broker.position("SIM") == 0
    kids = [o for o in s.broker.orders.values() if o.parent_id == parent["id"]]
    assert kids and all(o.status is Status.PENDING for o in kids)


def test_stop_coverage_needs_a_stop_that_kept_working():
    from trades.sim.scorecard import stop_protected

    def bars(n):
        return {"X": Bar(100, 101, 99, 100)}

    b = broker()
    b.submit("X", "buy", 10, index=0, time=T0)
    b.process_bar(1, T0, bars(1))
    placed = b.submit("X", "sell", 10, index=1, time=T0, type="stop", stop_price=90)
    b.cancel(placed.id, index=1)  # placed and immediately removed
    for i in range(2, 12):
        b.process_bar(i, T0, bars(i))
    b.submit("X", "sell", 10, index=11, time=T0)
    b.process_bar(12, T0, bars(12))
    (naked,) = b.ledger.closed_trades
    assert not stop_protected(naked, b.orders, cursor=12)

    g = broker()
    g.submit("X", "buy", 10, index=0, time=T0)
    g.process_bar(1, T0, bars(1))
    g.submit("X", "sell", 10, index=1, time=T0, type="stop", stop_price=90)  # kept until the exit
    for i in range(2, 12):
        g.process_bar(i, T0, bars(i))
    g.submit("X", "sell", 10, index=11, time=T0)
    g.process_bar(12, T0, bars(12))
    (guarded,) = g.ledger.closed_trades
    assert stop_protected(guarded, g.orders, cursor=12)

    bracket = broker()  # bracket stop that fires protects by definition
    bracket.submit("X", "buy", 10, index=0, time=T0, stop_loss=95)
    bracket.process_bar(1, T0, bars(1))
    bracket.process_bar(2, T0, {"X": Bar(96, 97, 94, 95)})
    (stopped,) = bracket.ledger.closed_trades
    assert stop_protected(stopped, bracket.orders, cursor=2)


def test_blind_session_hides_identifying_details(provider):
    class Service:
        class settings:
            @staticmethod
            def get():
                from trades.config import Settings

                return Settings(provider="yahoo")

        @staticmethod
        def bars(symbol, timeframe, start, end, provider_id):
            df = provider.history("SIMTEC")
            return df[(df.index.date >= start) & (df.index.date <= end)]

    s = create_session(
        SimConfig(source="history", symbol="SIMTEC", start="2020-03-02", length_bars=40), Service()
    )
    cfg = s.state()["config"]
    assert cfg["symbol"] is None and cfg["start"] is None and cfg["seed"] is None
    assert s.bars["volume"].iloc[: s.start_index + 1].median() == pytest.approx(1e6, rel=1e-3)
    s.finish()
    assert s.state()["config"]["symbol"] == "SIMTEC"

    sc = create_session(SimConfig(scenario="crash", seed=11, length_bars=30, warmup_bars=100))
    assert sc.state()["config"]["seed"] is None  # the seed would regenerate the future
    sc.finish()
    assert sc.state()["config"]["seed"] == 11
