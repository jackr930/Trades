"""Technical indicators.

Every function is *causal*: the value at bar ``t`` uses only bars ``<= t``. Strategies
rely on this to avoid look-ahead bias, and ``tests/test_strategies.py`` checks it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, length: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(length, min_periods=length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential moving average with smoothing 2/(length+1), seeded by the first value."""
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's moving average (alpha = 1/length), seeded with the SMA of the first
    ``length`` valid values -- the convention in Wilder (1978) used for RSI and ATR."""
    values = series.to_numpy(dtype=float)
    out = np.full(values.shape, np.nan)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < length:
        return pd.Series(out, index=series.index)
    start = valid[0]
    seed_end = start + length
    window = values[start:seed_end]
    if np.isnan(window).any():  # gaps inside the seed window: fall back to pandas ewm
        return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    prev = window.mean()
    out[seed_end - 1] = prev
    alpha = 1.0 / length
    for i in range(seed_end, len(values)):
        v = values[i]
        if not np.isnan(v):
            prev = prev + alpha * (v - prev)
        out[i] = prev
    return pd.Series(out, index=series.index)


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder, 1978). Range 0-100."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100.0 - 100.0 / (1.0 + rs)
    # No losses in the window -> RSI 100; flat window -> 50.
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    return out.where(avg_gain.notna())


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    tr = ranges.max(axis=1, skipna=True)
    return tr.where(high.notna() & low.notna())


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing)."""
    return rma(true_range(high, low, close), length)


def bollinger(close: pd.Series, length: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands (population standard deviation, as in Bollinger 2001)."""
    mid = sma(close, length)
    std = close.rolling(length, min_periods=length).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    width = upper - lower
    with np.errstate(divide="ignore", invalid="ignore"):
        pct_b = (close - lower) / width
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_pct_b": pct_b, "bb_width": width / mid},
        index=close.index,
    )


def donchian(high: pd.Series, low: pd.Series, length: int, *, exclude_current: bool = True) -> pd.DataFrame:
    """Donchian channel. With ``exclude_current`` the channel at bar t spans bars
    t-length .. t-1, so "close above the channel" is a genuine breakout."""
    upper = high.rolling(length, min_periods=length).max()
    lower = low.rolling(length, min_periods=length).min()
    if exclude_current:
        upper, lower = upper.shift(1), lower.shift(1)
    return pd.DataFrame({"upper": upper, "lower": lower}, index=high.index)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig}, index=close.index)


def rolling_zscore(series: pd.Series, length: int) -> pd.Series:
    mean = series.rolling(length, min_periods=length).mean()
    std = series.rolling(length, min_periods=length).std(ddof=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (series - mean) / std


def simple_returns(close: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    return close.pct_change(fill_method=None)


def log_returns(close: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    return np.log(close).diff()


def realized_vol(returns: pd.Series, length: int, periods_per_year: int) -> pd.Series:
    """Annualised rolling standard deviation of returns."""
    return returns.rolling(length, min_periods=length).std() * np.sqrt(periods_per_year)


def ewma_vol(returns: pd.Series, com: float, periods_per_year: int, min_periods: int = 20) -> pd.Series:
    """Annualised EWMA volatility (RiskMetrics-style, zero-mean), as used by
    Moskowitz, Ooi & Pedersen (2012) with a 60-day centre of mass."""
    var = (returns**2).ewm(com=com, adjust=True, min_periods=min_periods).mean()
    return np.sqrt(var * periods_per_year)
