"""Intraday features and strategies on hand-built sessions, where every trade is known in advance."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from trades.data.calendar import NY, session_bounds, trading_days
from trades.engine.backtest import backtest_strategy
from trades.execution.sim_broker import BacktestConfig
from trades.features.intraday import infer_bar_minutes, opening_range, prior_day, session_info
from trades.strategies import create_strategy
from trades.strategies.sizing import SizingConfig

CFG = BacktestConfig(initial_cash=10_000.0, slippage_bps=0.0, borrow_bps_annual=0.0, margin_rate_annual=0.0, periods_per_year=252 * 78)


def session(day: date, bars: list[tuple[float, float, float, float]], minutes: int = 5, volume=1000.0) -> pd.DataFrame:
    """A session's bars from (open, high, low, close); the rest of the day is flat at the last close."""
    open_dt, close_dt = session_bounds(day)
    n = int((close_dt - open_dt).total_seconds() // 60) // minutes
    last = bars[-1][3]
    rows = list(bars) + [(last, last, last, last)] * (n - len(bars))
    idx = pd.DatetimeIndex([open_dt + pd.Timedelta(minutes=minutes * i) for i in range(n)]).tz_convert("UTC")
    vol = volume if np.ndim(volume) else [volume] * n
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=vol)


def days(*frames: pd.DataFrame) -> pd.DataFrame:
    return pd.concat(frames)


DAY1, DAY2, DAY3 = date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11)
FLAT = [(100.0, 100.0, 100.0, 100.0)]


def test_session_info_and_opening_range_are_causal_and_calendar_based():
    df = days(session(DAY1, FLAT), session(date(2025, 11, 28), FLAT))  # the second is an early close (13:00)
    info = session_info(df.index)
    assert infer_bar_minutes(df.index) == 5
    assert info["last"].sum() == 2 and info["first"].sum() == 2
    assert info.loc[info["last"], "minute"].tolist() == [385, 205]
    up = session(DAY1, [(100, 101, 99.5, 100.8), (100.8, 101, 100.5, 100.9)])
    rng = opening_range(up, 5)
    assert rng["or_high"].iloc[0] == 101  # the 5-minute range completes with the first bar
    assert rng["decision"].sum() == 1 and bool(rng["decision"].iloc[0])
    ten = opening_range(up, 10)
    assert np.isnan(ten["or_high"].iloc[0]) and ten["or_high"].iloc[1] == 101 and bool(ten["decision"].iloc[1])
    with pytest.raises(ValueError, match="divide"):
        opening_range(up, 7)


def test_prior_day_numbers_use_only_previous_sessions():
    df = days(session(DAY1, [(100, 102, 98, 101)]), session(DAY2, [(101, 110, 100, 109)]))
    prior = prior_day(df, atr_days=1)
    day2 = prior.iloc[78:]
    assert (day2["prior_close"] == 101).all()  # yesterday's close, never today's
    assert prior["prior_close"].iloc[:78].isna().all()


def _orb(**params):
    return create_strategy("orb", params)


def test_orb_buys_the_next_open_and_is_stopped_at_the_range_low():
    bars = [
        (100.0, 101.0, 99.5, 100.8),  # up: buy at the next open, stop 99.5, target 100.8 + 10 x 1.3 = 113.8
        (100.8, 101.0, 100.5, 100.9),
        (100.9, 101.2, 100.6, 101.0),
        (101.0, 101.1, 99.4, 99.6),  # reaches the stop
        (99.6, 102.0, 99.6, 101.9),  # no re-entry
    ]
    df = session(DAY1, bars)
    bt = backtest_strategy(_orb(), {"QQQ": df}, None, CFG)
    out = bt.output.diagnostics["QQQ"]
    assert out["signal"].iloc[:3].tolist() == [1.0, 1.0, 1.0] and out["signal"].iloc[3] == 0.0
    assert out["stop_loss"].iloc[0] == 99.5 and out["take_profit"].iloc[0] == pytest.approx(113.8)
    buy, stop = bt.result.fills
    assert buy.index == 1 and buy.price == pytest.approx(100.8) and buy.tag == "market"
    assert stop.index == 3 and stop.tag == "stop" and stop.price == pytest.approx(99.5)
    # Sized so the stop loses 1% of equity at the decision close: 0.01 x 100.8 / 1.3 of equity, capped at 4x.
    assert bt.weights["QQQ"].iloc[0] == pytest.approx(min(0.01 * 100.8 / 1.3, 4.0))
    assert len(bt.result.trades) == 1


def test_orb_takes_profit_at_ten_r_and_otherwise_exits_at_the_close():
    win = [(100.0, 100.5, 99.9, 100.4), (100.4, 104.6, 100.3, 104.5)]  # R = 0.5: target 105.4
    win += [(104.5, 105.5, 104.4, 105.0)]  # trades through 105.4
    df = session(DAY1, win)
    bt = backtest_strategy(_orb(), {"QQQ": df}, None, CFG)
    tags = [f.tag for f in bt.result.fills]
    assert tags == ["market", "take_profit"] and bt.result.fills[-1].price == pytest.approx(105.4)
    # A quiet day: in at the second bar's open, out in the closing auction.
    quiet = session(DAY2, [(100.0, 100.5, 99.9, 100.4), (100.4, 100.6, 100.2, 100.5)])
    bt = backtest_strategy(_orb(), {"QQQ": quiet}, None, CFG)
    assert [f.tag for f in bt.result.fills] == ["market", "close"]
    assert bt.result.positions["QQQ"].iloc[-1] == 0


def test_orb_skips_unchanged_openings_and_unwanted_shorts():
    doji = session(DAY1, [(100.0, 100.5, 99.5, 100.0)])
    assert backtest_strategy(_orb(), {"QQQ": doji}, None, CFG).result.fills == []
    down = session(DAY1, [(100.0, 100.2, 99.0, 99.2)])
    assert backtest_strategy(_orb(allow_short=False), {"QQQ": down}, None, CFG).result.fills == []
    bt = backtest_strategy(_orb(), {"QQQ": down}, None, replace_cfg(allow_short=True))
    assert bt.result.fills[0].qty < 0


def replace_cfg(**kw) -> BacktestConfig:
    from dataclasses import replace

    return replace(CFG, **kw)


def test_orb_breakout_entry_waits_for_the_range_extreme():
    bars = [
        (100.0, 101.0, 99.5, 100.8),  # up; buy stop at the range high 101.0
        (100.8, 100.95, 100.5, 100.9),  # not reached
        (100.9, 101.3, 100.8, 101.2),  # reached: in at 101.0
    ]
    df = session(DAY1, bars)
    bt = backtest_strategy(_orb(entry="breakout", target_r=0.0), {"QQQ": df}, None, CFG)
    entry = bt.result.fills[0]
    assert entry.tag == "stop_entry" and entry.index == 2 and entry.price == pytest.approx(101.0)
    diag = bt.output.diagnostics["QQQ"]
    assert diag["state"].iloc[1] == 1 and diag["state"].iloc[2] == 2
    assert bt.strategy.position_states(bt.output, "QQQ")[1] == "flat"  # waiting is not a position


def test_intraday_momentum_trades_the_last_half_hour_in_the_morning_direction():
    flat_day = session(DAY1, [(100.0, 100.0, 100.0, 100.0)], minutes=30)
    up_morning = [(100.5, 101.2, 100.4, 101.0)] + [(101.0, 101.0, 101.0, 101.0)] * 11 + [(101.0, 102.0, 100.9, 101.8)]
    day2 = session(DAY2, up_morning, minutes=30)
    strat = create_strategy("intraday_momentum")
    bt = backtest_strategy(strat, {"SPY": days(flat_day, day2)}, None, replace_cfg(periods_per_year=252 * 13))
    buy, sell = bt.result.fills
    assert buy.time == pd.Timestamp(datetime(2026, 3, 10, 15, 30), tz=NY).tz_convert("UTC") and buy.price == 101.0
    assert sell.tag == "close" and sell.price == pytest.approx(101.8)
    diag = bt.output.diagnostics["SPY"]
    assert diag["r1"].iloc[13] == pytest.approx(0.01)  # 10:00 close 101 over yesterday's close 100


def test_intraday_momentum_decides_at_12_30_on_an_early_close():
    friday = date(2025, 11, 28)  # closes at 13:00
    prior = session(date(2025, 11, 26), [(100.0, 100.0, 100.0, 100.0)], minutes=30)
    morning = [(99.0, 99.5, 98.0, 98.5)] + [(98.5, 98.5, 98.5, 98.5)] * 5 + [(98.5, 98.6, 97.0, 97.5)]
    bt = backtest_strategy(
        create_strategy("intraday_momentum"),
        {"SPY": days(prior, session(friday, morning, minutes=30))},
        None,
        replace_cfg(periods_per_year=252 * 13),
    )
    short, cover = bt.result.fills
    assert short.qty < 0 and short.time.tz_convert(NY).hour == 12 and short.time.tz_convert(NY).minute == 30
    assert cover.tag == "close"


def test_stocks_in_play_trades_only_the_busiest_eligible_openings():
    def history(price: float, busy_today: float):
        frames = []
        for d in trading_days(date(2026, 2, 2), date(2026, 2, 27)):  # 19 sessions (Presidents' Day is off)
            last = d == date(2026, 2, 27)
            vol = np.full(78, 20_000.0)
            vol[0] = 40_000.0 * (busy_today if last else 1.0)  # opening 5-minute volume
            first = (price, price * 1.01, price * 0.99, price * 1.005) if last else (price, price * 1.02, price * 0.98, price)
            frames.append(session(d, [first], volume=vol))
        return pd.concat(frames)

    data = {"AAA": history(50, 3.0), "BBB": history(50, 2.0), "CCC": history(3, 5.0)}  # CCC is under $5
    strat = create_strategy("orb_sip", {"top_n": 1, "min_avg_volume": 1_000_000, "min_atr": 0.5})
    out = strat.run(data)
    selected = {s: bool(out.diagnostics[s]["selected"].any()) for s in data}
    assert selected == {"AAA": True, "BBB": False, "CCC": False}
    assert out.entry_stops["AAA"].dropna().iloc[0] == pytest.approx(50 * 1.01)  # buy stop at the range high


def test_intraday_strategies_refuse_daily_bars(daily):
    with pytest.raises(ValueError, match="intraday"):
        backtest_strategy(_orb(), {"SIMTEC": daily["SIMTEC"]}, None, BacktestConfig())


def test_orb_on_the_synthetic_market_trades_once_a_day_and_never_overnight(intraday):
    df = intraday["SIMTEC"]
    bt = backtest_strategy(_orb(), {"SIMTEC": df}, SizingConfig.from_dict({"method": "stop_risk", "max_leverage": 1.0}), CFG)
    info = session_info(df.index)
    pos = bt.result.positions["SIMTEC"].to_numpy()
    assert (pos[info["last"].to_numpy()] == 0).all()  # flat at every close
    entries = [f for f in bt.result.fills if f.tag in ("market", "stop_entry") and f.qty != 0]
    per_day = pd.Series([info["date"].iloc[f.index] for f in entries]).value_counts()
    assert per_day.max() == 1


def test_the_random_walk_market_gives_no_strategy_an_edge():
    """On the null market (intraday paths are martingales) even gross P&L must look like noise.
    On the default bridge market ORB shows a spurious edge: its paths are pinned to a known close."""
    from tests.helpers import END
    from trades.data.synthetic import SyntheticProvider

    null = SyntheticProvider(seed=7, end=END, intraday_paths="random_walk")
    pnl = []
    for sym in ("SIMTEC", "SIMIDX", "SIMBNK"):
        df = null.history(sym, "5m", start=date(2026, 3, 2))
        bt = backtest_strategy(_orb(), {sym: df}, None, CFG)
        pnl += [t.pnl for t in bt.result.trades if not t.is_open]
    a = np.array(pnl)
    t_stat = a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))
    assert len(a) > 400 and abs(t_stat) < 2
    with pytest.raises(ValueError, match="bridge"):
        SyntheticProvider(intraday_paths="smooth")
