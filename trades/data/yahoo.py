"""Yahoo Finance adapter (via the open-source ``yfinance`` package).

No API key is needed. Quotes are polled; Yahoo's prices for most US-listed stocks are
real-time or near real-time during regular hours. Yahoo's API is unofficial: use it
for personal, educational purposes and expect occasional throttling.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from trades.core.calendar import session_bounds
from trades.core.timeframes import Timeframe
from trades.data.base import (
    DataError,
    DataProvider,
    ProviderInfo,
    ProviderNotConfigured,
    Quote,
    SymbolNotFound,
    normalize_bars,
    slice_bars,
    to_utc_timestamp,
)

log = logging.getLogger(__name__)

INTERVALS = {
    Timeframe.D1: "1d",
    Timeframe.H1: "1h",
    Timeframe.M15: "15m",
    Timeframe.M5: "5m",
    Timeframe.M1: "1m",
}
# How far back Yahoo serves each intraday interval (days).
MAX_LOOKBACK_DAYS = {Timeframe.M1: 7, Timeframe.M5: 59, Timeframe.M15: 59, Timeframe.H1: 729}


def _yf():
    try:
        import yfinance as yf  # noqa: PLC0415 - optional heavy import
    except ImportError as exc:  # pragma: no cover
        raise ProviderNotConfigured("yfinance is not installed: pip install yfinance") from exc
    return yf


class YahooProvider(DataProvider):
    info = ProviderInfo(
        id="yahoo",
        label="Yahoo Finance",
        description=(
            "Free, no API key. Near real-time quotes for most US-listed stocks and ETFs "
            "(polled), decades of daily history, limited intraday history "
            "(1m: 7 days, 5m/15m: 60 days, 1h: 2 years). Unofficial API via yfinance -- "
            "for personal/educational use."
        ),
        requires_key=False,
        realtime="near real-time (polled)",
        timeframes=(Timeframe.D1, Timeframe.H1, Timeframe.M15, Timeframe.M5, Timeframe.M1),
        docs_url="https://github.com/ranaroussi/yfinance",
    )

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    # Thin wrappers around yfinance so tests can substitute canned responses.
    def _download_history(self, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
        return (
            _yf()
            .Ticker(symbol)
            .history(
                interval=interval, start=start, end=end, auto_adjust=True, actions=False, timeout=self.timeout
            )
        )

    def _download_recent(self, symbols: list[str]) -> pd.DataFrame:
        return _yf().download(
            symbols,
            period="5d",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
            timeout=self.timeout,
            multi_level_index=True,
        )

    def history(self, symbol, timeframe=Timeframe.D1, start=None, end=None) -> pd.DataFrame:
        timeframe = Timeframe.parse(timeframe)
        now = datetime.now(timezone.utc)
        end_ts = to_utc_timestamp(end) or pd.Timestamp(now)
        if isinstance(end, date) and not isinstance(end, datetime):
            end_ts = end_ts + pd.Timedelta(days=1)  # yfinance's end is exclusive
        if timeframe is Timeframe.D1:
            start_ts = to_utc_timestamp(start) or end_ts - pd.Timedelta(days=365 * 10)
        else:
            earliest = pd.Timestamp(now) - pd.Timedelta(days=MAX_LOOKBACK_DAYS[timeframe])
            start_ts = max(to_utc_timestamp(start) or earliest, earliest)
        try:
            raw = self._download_history(
                symbol,
                INTERVALS[timeframe],
                start_ts.to_pydatetime(),
                (end_ts + pd.Timedelta(minutes=1)).to_pydatetime(),
            )
        except DataError:
            raise
        except Exception as exc:
            raise DataError(f"Yahoo Finance request for {symbol} failed: {exc}") from exc
        if raw is None or raw.empty:
            raise SymbolNotFound(f"Yahoo Finance returned no {timeframe.label.lower()} data for {symbol!r}")
        bars = normalize_bars(raw, timeframe)
        return slice_bars(bars, start, end)

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        try:
            raw = self._download_recent(list(symbols))
        except Exception as exc:
            raise DataError(f"Yahoo Finance quote request failed: {exc}") from exc
        if raw is None or raw.empty:
            return {}
        out: dict[str, Quote] = {}
        now = datetime.now(timezone.utc)
        for sym in symbols:
            try:
                frame = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            frame = frame.dropna(subset=["Close"])
            if frame.empty:
                continue
            last = frame.iloc[-1]
            prev = frame.iloc[-2] if len(frame) > 1 else None
            # Stamp the quote within the session it belongs to (never later than that day's close).
            session_day = pd.Timestamp(frame.index[-1]).date()
            _, close_dt = session_bounds(session_day)
            out[sym] = Quote(
                symbol=sym,
                price=float(last["Close"]),
                timestamp=min(now, close_dt.astimezone(timezone.utc)),
                prev_close=float(prev["Close"]) if prev is not None else None,
                open=float(last["Open"]),
                high=float(last["High"]),
                low=float(last["Low"]),
                volume=float(last.get("Volume", 0.0) or 0.0),
                source="yahoo",
            )
        return out

    def check(self) -> tuple[bool, str]:
        try:
            bars = self.history("SPY", Timeframe.D1, start=datetime.now(timezone.utc) - timedelta(days=10))
        except DataError as exc:
            return False, f"Could not reach Yahoo Finance: {exc}"
        return True, f"OK - SPY last close {bars['close'].iloc[-1]:.2f}"
