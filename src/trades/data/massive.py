"""Massive market data REST adapter (formerly Polygon.io: https://massive.com/docs).

*Data only.* Massive sells market data and has no trading API, so this provider cannot place
orders.

Polygon.io renamed itself Massive on 30 October 2025 and kept its old address working for an
extended period, so the base URL is a setting (``massive_base_url``, or ``MASSIVE_BASE_URL``).
The endpoints themselves did not change.

Plans decide how far back history reaches and how fresh it is (individual plans, October 2026):
Basic is free (end-of-day data, two years of history, 5 requests a minute), Starter $29 a month
(15-minute delayed, five years), Developer $79 (ten years), Advanced $199 (real time). A
research run cannot reach further back than the plan does, so check a run's first bar.

Massive adjusts bars for splits only (``adjusted=true``); dividends are not added back, so a
daily return across an ex-dividend date is a price return. Intraday strategies that are flat
overnight are unaffected.
"""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
import pandas as pd

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
from trades.data.calendar import (
    NY,
    is_trading_day,
    last_completed_session,
    previous_trading_day,
    regular_session_date,
    session_bounds,
)
from trades.data.timeframes import Timeframe

log = logging.getLogger(__name__)

BASE_URL = "https://api.massive.com"
LEGACY_URL = "https://api.polygon.io"  # the address before the rename, still served
# (multiplier, timespan) in /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}
TIMESPANS = {
    Timeframe.D1: (1, "day"),
    Timeframe.H1: (1, "hour"),
    Timeframe.M30: (30, "minute"),
    Timeframe.M15: (15, "minute"),
    Timeframe.M5: (5, "minute"),
    Timeframe.M1: (1, "minute"),
}
PAGE_LIMIT = 50_000  # the most bars one aggregates page holds
MAX_PAGES = 500  # hard stop on pagination (500 pages of 1-minute bars is about 70 years)
RATE_LIMIT_WAIT = 12.0  # seconds; the free plan allows 5 requests a minute
GROUPED_REFRESH = timedelta(minutes=15)  # today's end-of-day bars may still be corrected; past ones are final


class NotEntitled(ProviderNotConfigured):
    """The key works, but the plan does not include this data (Massive answers 403)."""


def _message(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("error") or payload.get("status") or payload)[:300]
    return str(payload)[:300]


def _from_ns(value) -> datetime | None:
    return pd.Timestamp(int(value), unit="ns", tz="UTC").to_pydatetime(warn=False) if value else None


def _from_ms(value) -> datetime | None:
    return pd.Timestamp(int(value), unit="ms", tz="UTC").to_pydatetime(warn=False) if value else None


def _starts_in_session(start: datetime) -> bool:
    """Does a bar that starts at ``start`` belong to the regular session (09:30 to the close)?"""
    ny = start.astimezone(NY)
    if not is_trading_day(ny.date()):
        return False
    open_dt, close_dt = session_bounds(ny.date())
    return open_dt <= ny < close_dt


def _snapshot_quote(snap: dict, now: datetime) -> Quote | None:
    """One ticker of a snapshot as a Quote.

    The price is the latest regular-session print: the last trade if it was inside the session,
    else the last minute bar if it *started* inside it (the 09:29 bar ends at 09:30:00 but is
    pre-market), stamped with its own time. Outside the session it is the day's close (else the
    previous close) stamped with the snapshot's time: a quote stamped outside the session never
    changes a daily bar (``recommend.live.merge_quote``), so pre-market and after-hours prints
    stay out.
    """
    symbol = str(snap.get("ticker") or "").upper()
    trade = snap.get("lastTrade") or {}
    last_quote = snap.get("lastQuote") or {}
    day = snap.get("day") or {}
    prev = snap.get("prevDay") or {}
    minute = snap.get("min") or {}
    price, stamp = None, None
    traded, started = _from_ns(trade.get("t")), _from_ms(minute.get("t"))
    if trade.get("p") and traded is not None and regular_session_date(min(traded, now)) is not None:
        price, stamp = float(trade["p"]), min(traded, now)
    elif minute.get("c") and started is not None and _starts_in_session(started):
        price, stamp = float(minute["c"]), min(started + timedelta(minutes=1), now)
    if price is None:
        value = day.get("c") or prev.get("c")
        if not value:
            return None
        price, stamp = float(value), _from_ns(snap.get("updated")) or now
        session = regular_session_date(stamp)
        if session is not None:  # e.g. 09:30:05 before the first regular trade: still a pre-market price
            stamp = session_bounds(session)[0].astimezone(timezone.utc) - timedelta(microseconds=1)
    return Quote(
        symbol=symbol,
        price=price,
        timestamp=stamp,
        prev_close=prev.get("c") or None,
        open=day.get("o") or None,
        high=day.get("h") or None,
        low=day.get("l") or None,
        volume=day.get("v") or None,
        bid=last_quote.get("p") or None,  # lowercase p is the bid, uppercase P the ask
        ask=last_quote.get("P") or None,
        source="massive",
    )


class MassiveProvider(DataProvider):
    info = ProviderInfo(
        id="massive",
        label="Massive (formerly Polygon.io)",
        description=(
            "Deep US stock history for research: 1-minute to daily bars, split-adjusted. Basic is free "
            "(end-of-day data, 2 years, 5 requests a minute); Starter $29/month (5 years, 15-minute "
            "delayed); Developer $79 (10 years); Advanced $199 (real time). Needs an API key. Data only: "
            "Massive has no trading API."
        ),
        requires_key=True,
        realtime="end of day to real time, by plan (polled)",
        timeframes=(Timeframe.D1, Timeframe.H1, Timeframe.M30, Timeframe.M15, Timeframe.M5, Timeframe.M1),
        docs_url="https://massive.com/docs",
        key_fields=("massive_api_key", "massive_base_url"),
    )

    def __init__(
        self,
        api_key: str | None,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = _time.sleep,
        clock: Callable[[], datetime] | None = None,
    ):
        if not api_key:
            raise ProviderNotConfigured(
                "Massive needs an API key (free at https://massive.com). "
                "Add it in Settings or set MASSIVE_API_KEY."
            )
        self.api_key = api_key
        self.base_url = (base_url or BASE_URL).rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ProviderNotConfigured(f"Massive's base URL must start with https:// (got {self.base_url!r})")
        self._client = client or httpx.Client(timeout=30.0)
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # Every ticker's daily bar by session (when fetched, the bars), for quotes on plans without
        # snapshots: a past session's are final and fetched once; today's are re-read now and then.
        self._grouped_days: dict[date, tuple[datetime, dict[str, tuple]]] = {}

    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET a path on the configured host. The key goes in a header, never in the URL."""
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for attempt in range(4):
            try:
                resp = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == 3:
                    raise DataError(f"Massive request failed: {exc}") from exc
                self._sleep(0.5 * 2**attempt)
                continue
            if resp.status_code == 429:
                if attempt == 3:
                    raise DataError(
                        "Massive's rate limit was hit four times in a row (the free plan allows 5 requests "
                        "a minute). Wait a minute, or use a paid plan for long research runs."
                    )
                wait = float(resp.headers.get("Retry-After") or RATE_LIMIT_WAIT * 2**attempt)
                log.info("Massive rate limit: waiting %.0fs", wait)
                self._sleep(min(wait, 60.0))
                continue
            if resp.status_code == 401:
                raise ProviderNotConfigured(f"Massive rejected the API key (401): {_message(resp)}")
            if resp.status_code == 403:
                raise NotEntitled(f"Massive: your plan does not include this data (403): {_message(resp)}")
            if resp.status_code == 404:
                raise SymbolNotFound(f"Massive: not found ({path})")
            if resp.status_code >= 400:
                raise DataError(f"Massive error {resp.status_code}: {_message(resp)}")
            payload = resp.json()
            if isinstance(payload, dict) and payload.get("status") == "ERROR":
                raise DataError(f"Massive error: {payload.get('error') or payload.get('message')}")
            return payload
        raise DataError("Massive request failed after retries")  # pragma: no cover - loop always returns

    def _next_page(self, next_url: str) -> dict:
        """Follow ``next_url`` on the configured host only, so the key never goes anywhere else."""
        parts = urlsplit(next_url)
        return self._get(parts.path, dict(parse_qsl(parts.query)))

    def history(self, symbol, timeframe=Timeframe.D1, start=None, end=None) -> pd.DataFrame:
        timeframe = Timeframe.parse(timeframe)
        multiplier, timespan = TIMESPANS[timeframe]
        now = pd.Timestamp.now(tz="UTC")
        end_ts = to_utc_timestamp(end) or now
        if isinstance(end, date) and not isinstance(end, datetime):
            end_ts = end_ts + pd.Timedelta(hours=23, minutes=59, seconds=59)
        end_ts = min(end_ts, now)
        default_span = pd.Timedelta(days=365 * 10) if timeframe is Timeframe.D1 else pd.Timedelta(days=30)
        start_ts = to_utc_timestamp(start) or end_ts - default_span
        if timeframe is Timeframe.D1:  # daily bars are addressed by their New York trading date
            lo = start.isoformat() if _is_date(start) else start_ts.tz_convert(NY).date().isoformat()
            hi = end.isoformat() if _is_date(end) else end_ts.tz_convert(NY).date().isoformat()
        else:  # intraday: millisecond timestamps, both ends inclusive
            lo, hi = str(int(start_ts.timestamp() * 1000)), str(int(end_ts.timestamp() * 1000))
        path = f"/v2/aggs/ticker/{quote(symbol.upper(), safe='.:-')}/range/{multiplier}/{timespan}/{lo}/{hi}"
        payload = self._get(path, {"adjusted": "true", "sort": "asc", "limit": PAGE_LIMIT})
        rows: list[dict] = []
        for _ in range(MAX_PAGES):
            rows.extend(payload.get("results") or [])
            next_url = payload.get("next_url")
            if not next_url:
                break
            payload = self._next_page(next_url)
        else:
            raise DataError(f"Massive: more than {MAX_PAGES} pages of {symbol} bars; request a shorter range")
        if not rows:
            raise SymbolNotFound(
                f"Massive returned no {timeframe.label.lower()} bars for {symbol!r} from {lo} to {hi} "
                "(check the ticker, and how far back your plan reaches)"
            )
        df = pd.DataFrame(rows).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
        return slice_bars(normalize_bars(df, timeframe), start, end)

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Snapshots on paid plans; on the free plan, the latest end-of-day bars instead."""
        if not symbols:
            return {}
        wanted = [s.upper() for s in symbols]
        try:
            payload = self._get("/v2/snapshot/locale/us/markets/stocks/tickers", {"tickers": ",".join(wanted)})
        except NotEntitled:
            return self._end_of_day_quotes(wanted)
        now = self._clock()
        out: dict[str, Quote] = {}
        for snap in payload.get("tickers") or []:
            q = _snapshot_quote(snap, now)
            if q is not None and q.symbol in wanted:
                out[q.symbol] = q
        return out

    def _end_of_day_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """The latest completed session's close against the session before it: two requests for any
        number of symbols; a past session is fetched once, today's at most every 15 minutes."""
        session = last_completed_session(self._clock())
        day = self._grouped(session)
        if not day:  # the latest session is not published yet
            session = previous_trading_day(session)
            day = self._grouped(session)
        prev = self._grouped(previous_trading_day(session))
        stamp = session_bounds(session)[1].astimezone(timezone.utc)
        out: dict[str, Quote] = {}
        for sym in symbols:
            bar = day.get(sym)
            if not bar:
                continue
            o, h, low, c, v = bar
            before = prev.get(sym)
            out[sym] = Quote(
                symbol=sym,
                price=c,
                timestamp=stamp,
                prev_close=before[3] if before else None,
                open=o,
                high=h,
                low=low,
                volume=v,
                source="massive-eod",
            )
        return out

    def _grouped(self, session: date) -> dict[str, tuple]:
        now = self._clock()
        held = self._grouped_days.get(session)
        # Final once fetched on a later New York date; fetched on the day itself (after the close)
        # it may still be corrected, so it is read again after GROUPED_REFRESH.
        if held and (held[0].astimezone(NY).date() > session or now - held[0] < GROUPED_REFRESH):
            return held[1]
        payload = self._get(f"/v2/aggs/grouped/locale/us/market/stocks/{session.isoformat()}", {"adjusted": "true"})
        bars = {
            str(r["T"]).upper(): (r.get("o"), r.get("h"), r.get("l"), float(r["c"]), r.get("v"))
            for r in payload.get("results") or []
            if r.get("T") and r.get("c")
        }
        if not bars:
            return held[1] if held else {}  # not published yet: ask again next time
        self._grouped_days[session] = (now, bars)
        for old in sorted(self._grouped_days)[:-3]:
            del self._grouped_days[old]
        return bars

    def check(self) -> tuple[bool, str]:
        """One request any plan allows: SPY's daily bars for the last two weeks."""
        try:
            bars = self.history("SPY", Timeframe.D1, start=date.today() - timedelta(days=14))
        except DataError as exc:
            return False, str(exc)
        return True, f"OK - SPY {bars['close'].iloc[-1]:.2f} (daily bar of {bars.index[-1].date()})"


def _is_date(value) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)
