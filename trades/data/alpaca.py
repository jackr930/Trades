"""Alpaca Market Data API adapter (https://docs.alpaca.markets/docs/about-market-data-api).

*Data only.* This module talks to ``data.alpaca.markets`` and the market-data stream;
it never calls Alpaca's trading API, so it cannot place orders.

The free plan provides real-time data from the IEX exchange (``feed="iex"``); a paid
plan unlocks the consolidated SIP feed (``feed="sip"``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from datetime import date, datetime, timezone

import httpx
import pandas as pd

from trades.core.calendar import NY, regular_session_date, session_bounds
from trades.core.timeframes import Timeframe
from trades.data.base import (
    DataError,
    DataProvider,
    ProviderInfo,
    ProviderNotConfigured,
    Quote,
    SymbolNotFound,
    TradeHandler,
    normalize_bars,
    slice_bars,
    to_utc_timestamp,
)

log = logging.getLogger(__name__)

DATA_URL = "https://data.alpaca.markets"
STREAM_URL = "wss://stream.data.alpaca.markets/v2/{feed}"
TIMEFRAMES = {
    Timeframe.D1: "1Day",
    Timeframe.H1: "1Hour",
    Timeframe.M15: "15Min",
    Timeframe.M5: "5Min",
    Timeframe.M1: "1Min",
}


def _rfc3339(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _regular_session_price(trade: dict, daily: dict) -> tuple[float | None, datetime]:
    """Latest regular-session price and its time.

    The latest trade may be a pre-market or after-hours print; those are not part of
    the daily bar, so fall back to the daily bar's close, stamped at its session close
    (or now, if that session is still trading).
    """
    now = datetime.now(timezone.utc)
    if trade.get("p") is not None and trade.get("t"):
        ts = pd.Timestamp(trade["t"]).to_pydatetime(warn=False)
        if regular_session_date(ts) is not None:
            return float(trade["p"]), ts
    if daily.get("c") is None:
        return None, now
    if daily.get("t"):
        session = pd.Timestamp(daily["t"]).tz_convert(NY).date()
        close_dt = session_bounds(session)[1].astimezone(timezone.utc)
        return float(daily["c"]), min(now, close_dt)
    return float(daily["c"]), now


class AlpacaProvider(DataProvider):
    info = ProviderInfo(
        id="alpaca",
        label="Alpaca Market Data",
        description=(
            "Real-time US stock data (free plan: IEX exchange feed; paid plan: full SIP feed) "
            "with years of intraday and daily history. Needs a free Alpaca account API key. "
            "Data only -- this provider never uses Alpaca's trading endpoints (the optional paper trader "
            "uses the paper endpoint alone)."
        ),
        requires_key=True,
        realtime="real-time (streaming)",
        timeframes=(Timeframe.D1, Timeframe.H1, Timeframe.M15, Timeframe.M5, Timeframe.M1),
        docs_url="https://docs.alpaca.markets/docs/about-market-data-api",
        key_fields=("alpaca_key_id", "alpaca_secret_key", "alpaca_feed"),
    )
    supports_streaming = True

    def __init__(
        self,
        key_id: str | None,
        secret_key: str | None,
        feed: str = "iex",
        client: httpx.Client | None = None,
        base_url: str = DATA_URL,
    ):
        if not key_id or not secret_key:
            raise ProviderNotConfigured(
                "Alpaca needs an API key ID and secret (free at https://alpaca.markets). "
                "Add them in Settings or set APCA_API_KEY_ID / APCA_API_SECRET_KEY."
            )
        self.key_id = key_id
        self.secret_key = secret_key
        self.feed = feed if feed in ("iex", "sip", "delayed_sip") else "iex"
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=20.0)

    @property
    def _headers(self) -> dict[str, str]:
        return {"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret_key}

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}{path}"
        for attempt in range(4):
            try:
                resp = self._client.get(url, params=params, headers=self._headers)
            except httpx.HTTPError as exc:
                if attempt == 3:
                    raise DataError(f"Alpaca request failed: {exc}") from exc
                _time.sleep(0.5 * 2**attempt)
                continue
            if resp.status_code == 429 and attempt < 3:
                _time.sleep(float(resp.headers.get("Retry-After", 1.0 * 2**attempt)))
                continue
            if resp.status_code in (401, 403):
                raise ProviderNotConfigured(
                    f"Alpaca rejected the credentials ({resp.status_code}): {resp.text[:200]}"
                )
            if resp.status_code == 404:
                raise SymbolNotFound(f"Alpaca: not found ({path})")
            if resp.status_code >= 400:
                raise DataError(f"Alpaca error {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise DataError("Alpaca request failed after retries")

    def history(self, symbol, timeframe=Timeframe.D1, start=None, end=None) -> pd.DataFrame:
        timeframe = Timeframe.parse(timeframe)
        now = pd.Timestamp.now(tz="UTC")
        end_ts = to_utc_timestamp(end) or now
        if isinstance(end, date) and not isinstance(end, datetime):
            end_ts = end_ts + pd.Timedelta(hours=23, minutes=59)
        default_span = pd.Timedelta(days=365 * 10) if timeframe is Timeframe.D1 else pd.Timedelta(days=30)
        start_ts = to_utc_timestamp(start) or end_ts - default_span
        params = {
            "timeframe": TIMEFRAMES[timeframe],
            "start": _rfc3339(start_ts),
            "end": _rfc3339(min(end_ts, now)),
            "limit": 10000,
            "adjustment": "all",
            "feed": self.feed,
            "sort": "asc",
        }
        rows: list[dict] = []
        page_token = None
        for _ in range(200):  # hard stop on pagination
            if page_token:
                params["page_token"] = page_token
            payload = self._get(f"/v2/stocks/{symbol.upper()}/bars", params)
            rows.extend(payload.get("bars") or [])
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        if not rows:
            raise SymbolNotFound(f"Alpaca returned no {timeframe.label.lower()} bars for {symbol!r}")
        df = pd.DataFrame(rows)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df.index = pd.to_datetime(df["t"], utc=True, format="ISO8601")
        return slice_bars(normalize_bars(df, timeframe), start, end)

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        payload = self._get(
            "/v2/stocks/snapshots", {"symbols": ",".join(s.upper() for s in symbols), "feed": self.feed}
        )
        out: dict[str, Quote] = {}
        for sym in symbols:
            snap = payload.get(sym.upper())
            if not snap:
                continue
            trade = snap.get("latestTrade") or {}
            quote = snap.get("latestQuote") or {}
            daily = snap.get("dailyBar") or {}
            prev = snap.get("prevDailyBar") or {}
            price, ts = _regular_session_price(trade, daily)
            if price is None:
                continue
            out[sym] = Quote(
                symbol=sym,
                price=float(price),
                timestamp=ts,
                prev_close=prev.get("c"),
                open=daily.get("o"),
                high=daily.get("h"),
                low=daily.get("l"),
                volume=daily.get("v"),
                bid=quote.get("bp"),
                ask=quote.get("ap"),
                source=f"alpaca-{self.feed}",
            )
        return out

    def check(self) -> tuple[bool, str]:
        try:
            q = self.quotes(["SPY"])
        except DataError as exc:
            return False, str(exc)
        if "SPY" not in q:
            return False, "Connected, but no SPY snapshot was returned"
        return True, f"OK - SPY {q['SPY'].price:.2f} ({self.feed.upper()} feed)"

    async def stream_trades(self, symbols: list[str], on_trade: TradeHandler) -> None:
        """Stream real-time trades over Alpaca's market-data websocket until cancelled."""
        import websockets  # noqa: PLC0415

        url = STREAM_URL.format(feed=self.feed)
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, max_size=2**22) as ws:
                    await ws.recv()  # [{"T":"success","msg":"connected"}]
                    await ws.send(
                        json.dumps({"action": "auth", "key": self.key_id, "secret": self.secret_key})
                    )
                    auth = json.loads(await ws.recv())
                    if any(m.get("T") == "error" for m in auth):
                        raise ProviderNotConfigured(f"Alpaca stream auth failed: {auth}")
                    await ws.send(json.dumps({"action": "subscribe", "trades": [s.upper() for s in symbols]}))
                    backoff = 1.0
                    async for raw in ws:
                        for msg in json.loads(raw):
                            if msg.get("T") == "t":
                                ts = pd.Timestamp(msg["t"]).to_pydatetime(warn=False)
                                await on_trade(msg["S"], float(msg["p"]), float(msg.get("s", 0)), ts)
                            elif msg.get("T") == "error":
                                log.warning("Alpaca stream error: %s", msg)
            except asyncio.CancelledError:
                raise
            except ProviderNotConfigured:
                raise
            except Exception as exc:  # network hiccup: reconnect with backoff
                log.warning("Alpaca stream disconnected (%s); reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
