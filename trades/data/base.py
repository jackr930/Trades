"""Provider interface and bar-normalisation conventions.

Bars are a DataFrame with float columns ``open, high, low, close, volume`` and a
timezone-aware UTC ``DatetimeIndex``:

* daily bars are stamped at 00:00 UTC of the trading date;
* intraday bars are stamped with the bar's *start* time in UTC.

Prices are split- and dividend-adjusted where the provider supports it, so returns
computed from ``close`` are total returns.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import ClassVar

import numpy as np
import pandas as pd

from trades.core.calendar import NY, is_trading_day, session_bounds
from trades.core.timeframes import Timeframe

BAR_COLUMNS = ("open", "high", "low", "close", "volume")


class DataError(RuntimeError):
    """Raised when market data cannot be retrieved or is invalid."""


class ProviderNotConfigured(DataError):
    """The provider needs credentials or an optional dependency."""


class SymbolNotFound(DataError):
    pass


@dataclass
class Quote:
    symbol: str
    price: float
    timestamp: datetime
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    bid: float | None = None
    ask: float | None = None
    source: str = ""

    @property
    def change(self) -> float | None:
        if not self.prev_close:
            return None
        return self.price - self.prev_close

    @property
    def change_pct(self) -> float | None:
        if not self.prev_close:
            return None
        return self.price / self.prev_close - 1.0

    def to_dict(self) -> dict:
        def f(x):
            return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else float(x)

        return {
            "symbol": self.symbol,
            "price": f(self.price),
            "prev_close": f(self.prev_close),
            "open": f(self.open),
            "high": f(self.high),
            "low": f(self.low),
            "volume": f(self.volume),
            "bid": f(self.bid),
            "ask": f(self.ask),
            "change": f(self.change),
            "change_pct": f(self.change_pct),
            "timestamp": int(self.timestamp.timestamp()),
            "source": self.source,
        }


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    description: str
    requires_key: bool
    realtime: str  # human readable latency class
    timeframes: tuple[Timeframe, ...]
    docs_url: str | None = None
    key_fields: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "requires_key": self.requires_key,
            "realtime": self.realtime,
            "timeframes": [t.value for t in self.timeframes],
            "docs_url": self.docs_url,
            "key_fields": list(self.key_fields),
        }


TradeHandler = Callable[[str, float, float, datetime], Awaitable[None]]


class DataProvider(ABC):
    info: ClassVar[ProviderInfo]
    supports_streaming: ClassVar[bool] = False

    @abstractmethod
    def history(
        self,
        symbol: str,
        timeframe: Timeframe = Timeframe.D1,
        start: datetime | date | None = None,
        end: datetime | date | None = None,
    ) -> pd.DataFrame:
        """Historical OHLCV bars in [start, end] (both optional)."""

    @abstractmethod
    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Latest price snapshot for each symbol (symbols that fail are omitted)."""

    def check(self) -> tuple[bool, str]:
        """Cheap connectivity/credential check."""
        try:
            q = self.quotes(["SPY"])
        except DataError as exc:
            return False, str(exc)
        except Exception as exc:  # pragma: no cover - network specific
            return False, f"{type(exc).__name__}: {exc}"
        if not q:
            return False, "No quote returned for SPY"
        return True, f"OK - SPY {q['SPY'].price:.2f}"

    async def stream_trades(self, symbols: list[str], on_trade: TradeHandler) -> None:  # pragma: no cover
        """Stream trades until cancelled: ``await on_trade(symbol, price, size, time)``."""
        raise NotImplementedError


def to_utc_timestamp(value: datetime | date | str | None) -> pd.Timestamp | None:
    if value is None:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def normalize_bars(df: pd.DataFrame, timeframe: Timeframe = Timeframe.D1) -> pd.DataFrame:
    """Coerce provider output into the canonical bar format (see module docstring)."""
    if df is None or len(df) == 0:
        return empty_bars()
    out = df.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    if "adj_close" in out.columns and "close" in out.columns:
        # Scale OHLC by the adjustment factor so the whole bar is adjusted consistently.
        factor = (out["adj_close"] / out["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
        for col in ("open", "high", "low", "close"):
            out[col] = out[col] * factor
    missing = [c for c in BAR_COLUMNS if c not in out.columns]
    if "volume" in missing:
        out["volume"] = 0.0
        missing.remove("volume")
    if missing:
        raise DataError(f"bars missing columns: {missing}")
    out = out[list(BAR_COLUMNS)].astype(float)

    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if timeframe is Timeframe.D1:
        if idx.tz is not None:
            idx = idx.tz_convert(NY)
        dates = idx.tz_localize(None).normalize() if idx.tz is not None else idx.normalize()
        idx = pd.DatetimeIndex(dates).tz_localize("UTC")
    else:
        idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out.index = idx
    out.index.name = "time"

    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out[(out["close"] > 0) & (out["open"] > 0)]
    # Repair inconsistent highs/lows occasionally present in vendor data.
    out["high"] = out[["open", "high", "low", "close"]].max(axis=1)
    out["low"] = out[["open", "high", "low", "close"]].min(axis=1)
    out["volume"] = out["volume"].fillna(0.0).clip(lower=0.0)
    return out


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {c: pd.Series(dtype=float) for c in BAR_COLUMNS},
        index=pd.DatetimeIndex([], tz="UTC", name="time"),
    )


def slice_bars(df: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    s, e = to_utc_timestamp(start), to_utc_timestamp(end)
    if isinstance(end, date) and not isinstance(end, datetime) and e is not None:
        e = e + pd.Timedelta(hours=23, minutes=59, seconds=59)
    if s is not None:
        df = df[df.index >= s]
    if e is not None:
        df = df[df.index <= e]
    return df


def lookback_start(timeframe: Timeframe, bars: int, end: datetime | None = None) -> datetime:
    """A start time that comfortably yields ``bars`` bars ending at ``end``."""
    end = end or datetime.now(timezone.utc)
    if timeframe is Timeframe.D1:
        days = int(bars * 365.25 / 252 * 1.04) + 10
    else:
        days = int(math.ceil(bars / timeframe.bars_per_day) * 7 / 5 * 1.1) + 4
    return end - timedelta(days=days)


def last_bar_forming(df: pd.DataFrame | None, timeframe: Timeframe, now: datetime | None = None) -> bool:
    """Is the last bar still forming at ``now`` (its session or interval not yet over)?

    A forming bar's close is only the latest price: signals computed on it can change
    before the bar completes, so it must not feed cached statistics.
    """
    if df is None or len(df) == 0:
        return False
    now = now or datetime.now(timezone.utc)
    last = pd.Timestamp(df.index[-1])
    if timeframe is Timeframe.D1:
        d = last.date()  # daily bars are stamped 00:00 UTC of their trading date
        return is_trading_day(d) and now < session_bounds(d)[1]
    start = last.to_pydatetime()
    end = start + timedelta(minutes=timeframe.minutes)
    d = start.astimezone(NY).date()
    if is_trading_day(d):
        close = session_bounds(d)[1]
        if start < close:  # a regular-session bar ends at the close (the last hourly bar is short);
            end = min(end, close)  # an extended-hours bar after the close runs its full interval
    return now < end


def align_bars(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Restrict all frames to their common timestamps (inner join)."""
    if not frames:
        return {}
    common = None
    for df in frames.values():
        common = df.index if common is None else common.intersection(df.index)
    assert common is not None
    return {sym: df.loc[common] for sym, df in frames.items()}
