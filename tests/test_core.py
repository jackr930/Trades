"""Calendar, timeframes, indicators, statistics and the ledger."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from trades.core import indicators as ind
from trades.core import stats
from trades.core.calendar import (
    easter_sunday,
    is_early_close,
    last_completed_session,
    market_status,
    nyse_holidays,
    trading_days,
)
from trades.core.ledger import Ledger
from trades.core.timeframes import Timeframe

# ---------------------------------------------------------------------------- calendar


def test_known_nyse_holidays_2024():
    assert sorted(nyse_holidays(2024)) == [
        date(2024, 1, 1),
        date(2024, 1, 15),
        date(2024, 2, 19),
        date(2024, 3, 29),
        date(2024, 5, 27),
        date(2024, 6, 19),
        date(2024, 7, 4),
        date(2024, 9, 2),
        date(2024, 11, 28),
        date(2024, 12, 25),
    ]


def test_observance_rules():
    h2022 = nyse_holidays(2022)
    assert date(2021, 12, 31) not in nyse_holidays(2021)  # New Year's on Saturday is not observed
    assert date(2022, 6, 20) in h2022  # Juneteenth on Sunday -> Monday
    assert date(2022, 12, 26) in h2022  # Christmas on Sunday -> Monday
    assert date(2021, 12, 24) in nyse_holidays(2021)  # Christmas on Saturday -> Friday
    assert date(2021, 6, 18) not in nyse_holidays(2021)  # Juneteenth only from 2022
    assert easter_sunday(2024) == date(2024, 3, 31)
    assert easter_sunday(2025) == date(2025, 4, 20)


def test_trading_day_counts():
    assert len(trading_days(date(2024, 1, 1), date(2024, 12, 31))) == 252
    assert len(trading_days(date(2023, 1, 1), date(2023, 12, 31))) == 250


def test_early_closes():
    assert is_early_close(date(2024, 7, 3))
    assert is_early_close(date(2024, 11, 29))
    assert is_early_close(date(2024, 12, 24))
    assert not is_early_close(date(2024, 7, 2))


def test_market_status_and_last_session():
    during = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)  # Monday 11:00 ET
    st = market_status(during)
    assert st.is_open and st.phase == "regular"
    assert last_completed_session(during) == date(2026, 9, 25)
    after = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    assert not market_status(after).is_open
    assert last_completed_session(after) == date(2026, 9, 28)
    weekend = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)
    assert market_status(weekend).phase == "closed"


def test_timeframes():
    assert Timeframe.parse("daily") is Timeframe.D1
    assert Timeframe.parse("60m") is Timeframe.H1
    assert Timeframe.H1.bars_per_day == 7
    assert Timeframe.M5.periods_per_year == 252 * 78
    with pytest.raises(ValueError):
        Timeframe.parse("3d")


# -------------------------------------------------------------------------- indicators


def _naive_wilder(values: np.ndarray, n: int) -> float:
    avg = values[:n].mean()
    for v in values[n:]:
        avg = (avg * (n - 1) + v) / n
    return avg


def test_rsi_matches_wilder_definition():
    rng = np.random.default_rng(1)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))
    rsi = ind.rsi(close, 14)
    d = close.diff().to_numpy()[1:]
    gain, loss = np.clip(d, 0, None), np.clip(-d, 0, None)
    expected = 100 - 100 / (1 + _naive_wilder(gain, 14) / _naive_wilder(loss, 14))
    assert rsi.iloc[-1] == pytest.approx(expected, rel=1e-10)
    assert rsi.iloc[:14].isna().all() and rsi.iloc[14:].between(0, 100).all()


def test_rsi_edge_cases():
    up = pd.Series(np.arange(1.0, 40.0))
    assert ind.rsi(up, 5).iloc[-1] == 100.0
    flat = pd.Series(np.full(30, 10.0))
    assert ind.rsi(flat, 5).iloc[-1] == 50.0


def test_atr_bollinger_donchian():
    rng = np.random.default_rng(2)
    c = pd.Series(100 + np.cumsum(rng.normal(0, 1, 200)))
    h, lo = c + 1, c - 1
    atr = ind.atr(h, lo, c, 14)
    assert (atr.dropna() > 0).all()
    bb = ind.bollinger(c, 20, 2.0)
    window = c.iloc[-20:]
    assert bb["bb_mid"].iloc[-1] == pytest.approx(window.mean())
    assert bb["bb_upper"].iloc[-1] == pytest.approx(window.mean() + 2 * window.std(ddof=0))
    dc = ind.donchian(h, lo, 20)
    # channel excludes the current bar
    assert dc["upper"].iloc[-1] == pytest.approx(h.iloc[-21:-1].max())


def test_indicators_are_causal():
    rng = np.random.default_rng(3)
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))))
    full = pd.concat(
        [ind.sma(c, 50), ind.ema(c, 20), ind.rsi(c, 14), ind.atr(c * 1.01, c * 0.99, c, 14)], axis=1
    )
    part = pd.concat(
        [
            ind.sma(c[:250], 50),
            ind.ema(c[:250], 20),
            ind.rsi(c[:250], 14),
            ind.atr(c[:250] * 1.01, c[:250] * 0.99, c[:250], 14),
        ],
        axis=1,
    )
    np.testing.assert_allclose(full.iloc[:250].to_numpy(), part.to_numpy(), equal_nan=True)


# ------------------------------------------------------------------------------ stats


def test_sharpe_and_drawdown():
    r = np.array([0.01, -0.005, 0.02, 0.0, 0.01])
    assert stats.sharpe_ratio(r, 252) == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(252))
    eq = pd.Series([100, 120, 90, 95, 130, 117])
    dd = stats.max_drawdown(eq)
    assert dd.max_drawdown == pytest.approx(-0.25)
    assert (dd.peak, dd.trough, dd.recovery) == (1, 2, 4)


def test_probabilistic_and_deflated_sharpe():
    assert stats.probabilistic_sharpe_ratio(0.0, 500) == pytest.approx(0.5)
    psr = stats.probabilistic_sharpe_ratio(0.1, 500)
    assert 0.98 < psr < 1.0
    # more trials -> higher bar -> lower deflated probability
    d10 = stats.deflated_sharpe_ratio(0.1, 500, 0.0, 3.0, 10, 0.002)
    d100 = stats.deflated_sharpe_ratio(0.1, 500, 0.0, 3.0, 100, 0.002)
    assert d100 < d10 < psr
    assert stats.expected_max_sharpe(1, 0.01) == 0.0


def test_adf_and_cointegration_match_statsmodels():
    tsa = pytest.importorskip("statsmodels.tsa.stattools")
    rng = np.random.default_rng(0)
    rw = np.cumsum(rng.standard_normal(500))
    ar = np.zeros(500)
    for i in range(1, 500):
        ar[i] = 0.9 * ar[i - 1] + rng.standard_normal()
    for series in (rw, ar):
        expected = tsa.adfuller(series, autolag="AIC", result_object=False)
        got = stats.adf_test(series)
        assert got.stat == pytest.approx(expected[0], rel=1e-8)
        assert got.lags == expected[2]
    x = np.cumsum(rng.standard_normal(400))
    y = 0.5 + 1.3 * x + ar[:400]
    expected = tsa.coint(y, x)
    got = stats.engle_granger(y, x)
    assert got.adf.stat == pytest.approx(expected[0], rel=1e-8)
    assert got.adf.critical_values["5%"] == pytest.approx(expected[2][1], rel=1e-8)
    assert got.cointegrated


def test_ou_half_life_recovers_true_value():
    rng = np.random.default_rng(5)
    phi = 0.9  # half-life = ln 2 / -ln 0.9 ~ 6.6 bars
    s = np.zeros(20000)
    for i in range(1, len(s)):
        s[i] = phi * s[i - 1] + rng.standard_normal()
    assert stats.ou_half_life(s) == pytest.approx(math.log(2) / -math.log(phi), rel=0.1)


# ----------------------------------------------------------------------------- ledger


def test_ledger_round_trip_accounting():
    led = Ledger(10_000)
    led.apply_fill(time=0, index=0, symbol="X", qty=10, price=100, commission=1)
    led.apply_fill(time=1, index=1, symbol="X", qty=10, price=110, commission=1)
    assert led.positions["X"].avg_price == pytest.approx(105)
    led.apply_fill(time=2, index=2, symbol="X", qty=-20, price=120, commission=1)
    assert led.qty("X") == 0
    (t,) = led.closed_trades
    assert t.realized_pnl == pytest.approx(20 * 15)
    assert t.pnl == pytest.approx(300 - 3)
    assert t.bars_held == 2
    assert led.cash == pytest.approx(10_000 + 297)


def test_ledger_reversal_opens_new_trade():
    led = Ledger(10_000)
    led.apply_fill(time=0, index=0, symbol="X", qty=10, price=100)
    led.apply_fill(time=1, index=1, symbol="X", qty=-15, price=90)
    assert led.qty("X") == -5
    assert led.closed_trades[0].pnl == pytest.approx(-100)
    short = led.open_trades["X"]
    assert short.direction == "short" and short.entry_price == 90
    assert led.equity({"X": 80}) == pytest.approx(10_000 - 100 + 50)


def test_ledger_excursions():
    led = Ledger(1_000)
    led.apply_fill(time=0, index=0, symbol="X", qty=1, price=100)
    led.mark_excursions("X", high=110, low=95)
    t = led.open_trades["X"]
    assert t.max_favorable == pytest.approx(0.10)
    assert t.max_adverse == pytest.approx(-0.05)
