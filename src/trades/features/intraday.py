"""Intraday features: sessions, the opening range, relative volume, prior-day ATR and volatility.

Bars carry their *start* time in UTC (see ``trades.data.base``); sessions follow the NYSE
calendar, including 13:00 early closes. Every function is causal: a value at a bar uses that
bar and earlier bars only. Anything that needs a completed opening range is NaN until the
range's last bar closes, and anything computed from whole days uses only *previous* sessions.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from trades.data.calendar import NY, is_trading_day, session_bounds
from trades.features import indicators as ind

OPEN_MINUTE = 9 * 60 + 30  # 09:30 New York


def infer_bar_minutes(index: pd.DatetimeIndex) -> int | None:
    """The bar size in minutes (the most common gap between bars of the same day); None for daily bars."""
    idx = pd.DatetimeIndex(index)
    if len(idx) < 2:
        return None
    ny = idx.tz_convert(NY) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(NY)
    gaps = np.asarray((ny[1:] - ny[:-1]).total_seconds()) / 60.0  # unit-agnostic (pandas 3 may store us)
    same_day = ny.date[1:] == ny.date[:-1]
    gaps = gaps[same_day & (gaps > 0)]
    if len(gaps) == 0:
        return None
    values, counts = np.unique(np.round(gaps).astype(int), return_counts=True)
    return int(values[np.argmax(counts)])


def _close_minutes(dates: np.ndarray) -> dict[date, int]:
    """Minutes from 09:30 to each trading day's close (390, or 210 on an early close)."""
    out = {}
    for d in pd.unique(dates):
        if is_trading_day(d):
            open_dt, close_dt = session_bounds(d)
            out[d] = int((close_dt - open_dt).total_seconds() // 60)
        else:
            out[d] = 0
    return out


def session_info(index: pd.DatetimeIndex, bar_minutes: int | None = None) -> pd.DataFrame:
    """Per bar: ``date`` (the New York trading date), ``minute`` (from 09:30 to the bar's start),
    ``close_minute`` (from 09:30 to that day's close: 390, or 210 on an early close), ``regular``
    (inside the regular session), ``first`` (the session's first regular bar in the data) and
    ``last`` (the bar that ends at the close, from the calendar). All of these are causal.

    ``last_available`` also marks the last bar a session has in the data when later bars are
    missing. It looks at the next bar, so only a backtest that holds the whole history may use it
    (to make sure nothing is carried overnight by accident); strategies must use ``last``."""
    idx = pd.DatetimeIndex(index)
    ny = idx.tz_convert(NY) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(NY)
    bm = bar_minutes or infer_bar_minutes(idx) or 1
    dates = np.asarray(ny.date)
    minute = np.asarray(ny.hour * 60 + ny.minute - OPEN_MINUTE, dtype=int)
    closes = _close_minutes(dates)
    close_min = np.array([closes[d] for d in dates], dtype=int)
    regular = (minute >= 0) & (minute < close_min)
    frame = pd.DataFrame(
        {"date": dates, "minute": minute, "close_minute": close_min, "regular": regular}, index=idx
    )
    first = np.zeros(len(idx), dtype=bool)
    available = np.zeros(len(idx), dtype=bool)
    if regular.any():
        pos = np.flatnonzero(regular)
        d = dates[regular]
        first[pos[np.concatenate([[True], d[1:] != d[:-1]])]] = True
        available[pos[np.concatenate([d[1:] != d[:-1], [True]])]] = True
    last = regular & (minute + bm >= close_min)
    frame["first"] = first
    frame["last"] = last
    frame["last_available"] = last | available
    return frame


def daily_from_intraday(df: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLCV built from the regular-session bars, indexed by trading date."""
    info = session_info(df.index)
    reg = df[info["regular"].to_numpy()]
    if reg.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    g = reg.groupby(info.loc[reg.index, "date"].to_numpy())
    return pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
        }
    )


def _per_bar(values: pd.Series, dates: np.ndarray, index: pd.Index) -> pd.Series:
    """Map a per-date series onto bars."""
    return pd.Series(pd.Series(dates).map(values).to_numpy(dtype=float), index=index)


def prior_day(df: pd.DataFrame, atr_days: int = 14, volume_days: int = 14, vol_days: int = 20,
              vol_history: int = 252) -> pd.DataFrame:
    """Per bar, numbers known before the session opened (from previous sessions only):
    ``prior_close``, ``atr`` (Wilder ATR over ``atr_days`` days), ``avg_volume`` (mean daily
    volume over ``volume_days`` days) and ``vol_rank`` (where the realised volatility of the
    last ``vol_days`` daily returns sits among the past ``vol_history`` days, 0 to 1)."""
    info = session_info(df.index)
    daily = daily_from_intraday(df)
    dates = info["date"].to_numpy()
    if daily.empty:
        nan = pd.Series(np.nan, index=df.index)
        return pd.DataFrame({"prior_close": nan, "atr": nan, "avg_volume": nan, "vol_rank": nan})
    atr = ind.atr(daily["high"], daily["low"], daily["close"], atr_days)
    avg_vol = daily["volume"].rolling(volume_days, min_periods=volume_days).mean()
    rets = np.log(daily["close"]).diff()
    rv = rets.rolling(vol_days, min_periods=vol_days).std()
    rank = rv.rolling(vol_history, min_periods=min(60, vol_history)).rank(pct=True)
    known = pd.DataFrame(
        {"prior_close": daily["close"], "atr": atr, "avg_volume": avg_vol, "vol_rank": rank}
    ).shift(1)  # shift one session: today's bars only see yesterday and earlier
    return pd.DataFrame({c: _per_bar(known[c], dates, df.index) for c in known.columns})


def opening_range(df: pd.DataFrame, minutes: int = 5, bar_minutes: int | None = None) -> pd.DataFrame:
    """The opening range (the first ``minutes`` of the session) seen from each bar.

    Columns ``or_open``, ``or_high``, ``or_low``, ``or_close`` and ``or_volume`` are NaN until the
    range's last bar has closed and then hold for the rest of the session. ``decision`` marks that
    last bar: the first close at which a breakout rule can act. A session whose range has a
    missing bar gets no range at all."""
    bm = bar_minutes or infer_bar_minutes(df.index) or 1
    if minutes % bm:
        raise ValueError(f"an opening range of {minutes} minutes needs bars that divide it (bars are {bm} minutes)")
    info = session_info(df.index, bm)
    in_or = info["regular"].to_numpy() & (info["minute"].to_numpy() < minutes)
    dates = info["date"].to_numpy()
    minute = info["minute"].to_numpy()
    sub = df[in_or]
    g = sub.groupby(dates[in_or])
    per_day = pd.DataFrame(
        {
            "or_open": g["open"].first(),
            "or_high": g["high"].max(),
            "or_low": g["low"].min(),
            "or_close": g["close"].last(),
            "or_volume": g["volume"].sum(),
            "bars": g.size(),
            "start": pd.Series(minute[in_or]).groupby(dates[in_or]).min(),
        }
    )
    complete = per_day[(per_day["bars"] == minutes // bm) & (per_day["start"] == 0)]
    seen = info["regular"].to_numpy() & (minute >= minutes - bm) & pd.Series(dates).isin(complete.index).to_numpy()
    out = pd.DataFrame(index=df.index)
    for col in ("or_open", "or_high", "or_low", "or_close", "or_volume"):
        out[col] = _per_bar(complete[col], dates, df.index).where(seen)
    out["decision"] = seen & (minute == minutes - bm)
    return out


def relative_opening_volume(df: pd.DataFrame, minutes: int = 5, days: int = 14) -> pd.Series:
    """Today's opening-range volume divided by its average over the previous ``days`` sessions
    (NaN until today's range is complete and ``days`` earlier ranges exist)."""
    rng = opening_range(df, minutes)
    info = session_info(df.index)
    dates = info["date"].to_numpy()
    daily = rng["or_volume"].groupby(dates).first().dropna()
    avg = daily.rolling(days, min_periods=days).mean().shift(1)
    rel = (daily / avg).replace([np.inf, -np.inf], np.nan)
    return _per_bar(rel, dates, df.index).where(rng["or_volume"].notna())


def price_at(df: pd.DataFrame, minute_end: int, bar_minutes: int | None = None) -> pd.Series:
    """Within each session, the close of the bar that ends ``minute_end`` minutes after the open
    (e.g. 30 = 10:00), from that bar onward; NaN before it and on sessions without that bar."""
    bm = bar_minutes or infer_bar_minutes(df.index) or 1
    if minute_end % bm:
        raise ValueError(f"{minute_end} minutes after the open is not the end of a {bm}-minute bar")
    info = session_info(df.index, bm)
    minute = info["minute"].to_numpy()
    dates = info["date"].to_numpy()
    at = info["regular"].to_numpy() & (minute + bm == minute_end)
    per_day = pd.Series(df["close"].to_numpy()[at], index=dates[at])
    per_day = per_day[~per_day.index.duplicated()]
    seen = info["regular"].to_numpy() & (minute + bm >= minute_end)
    return _per_bar(per_day, dates, df.index).where(seen)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price since the session's open (typical price), causal."""
    info = session_info(df.index)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (typical * df["volume"]).where(info["regular"].to_numpy(), 0.0)
    vol = df["volume"].where(info["regular"].to_numpy(), 0.0)
    dates = info["date"].to_numpy()
    cum_pv = pv.groupby(dates).cumsum()
    cum_v = vol.groupby(dates).cumsum()
    return (cum_pv / cum_v.replace(0.0, np.nan)).where(info["regular"].to_numpy())
