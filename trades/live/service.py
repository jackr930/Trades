"""Live recommendation service.

Keeps recent bars for the watchlist, merges fresh quotes (polled, or streamed for
providers that support it) into the forming bar, re-runs the recommendation engine and
pushes snapshots to subscribed websocket clients.

With the synthetic provider it runs a *demo clock*: an accelerated trading day (by
default one simulated minute per real second) so the whole pipeline can be explored
offline. Nothing here places orders.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from trades.advisor import AdvisorSettings, Recommender
from trades.backtest.runner import sanitize
from trades.config import SettingsStore
from trades.core.calendar import (
    market_status,
    next_trading_day,
    regular_session_date,
    session_bounds,
)
from trades.core.timeframes import SESSION_MINUTES, Timeframe
from trades.data.base import HISTORY_START, DataError, Quote, last_bar_forming
from trades.data.service import DataService
from trades.data.synthetic import SyntheticProvider, universe_info

log = logging.getLogger(__name__)

IDLE_AFTER = 60.0  # seconds without anyone watching before the live loop parks

# Intraday history is the latest N bars; daily history always starts at HISTORY_START.
HISTORY_BARS = {
    Timeframe.H1: 1200,
    Timeframe.M15: 1200,
    Timeframe.M5: 1500,
    Timeframe.M1: 1500,
}


def merge_quote(df: pd.DataFrame, q: Quote) -> pd.DataFrame:
    """Fold a quote into daily bars: update the session's bar or start a new one.

    Only regular-session quotes count (daily bars cover 09:30-16:00 ET): pre-market and
    after-hours prints are ignored, and bars of earlier sessions are never modified.
    """
    day = regular_session_date(q.timestamp)
    if day is None:
        return df
    ts = pd.Timestamp(day).tz_localize("UTC")
    price = float(q.price)
    if len(df) and df.index[-1] == ts:
        df = df.copy()
        last = df.iloc[-1]
        df.iloc[-1, df.columns.get_loc("close")] = price
        df.iloc[-1, df.columns.get_loc("high")] = max(last["high"], q.high or price, price)
        df.iloc[-1, df.columns.get_loc("low")] = min(last["low"], q.low or price, price)
        if q.volume:
            df.iloc[-1, df.columns.get_loc("volume")] = float(q.volume)
        return df
    if len(df) == 0 or ts > df.index[-1]:
        row = pd.DataFrame(
            {
                "open": [q.open or price],
                "high": [max(q.high or price, price)],
                "low": [min(q.low or price, price)],
                "close": [price],
                "volume": [float(q.volume or 0.0)],
            },
            index=pd.DatetimeIndex([ts], name="time"),
        )
        return pd.concat([df, row])
    return df


class DemoClock:
    """Accelerated synthetic trading day for offline live mode."""

    def __init__(self, seed: int, speed: float, symbols: list[str], timeframe: Timeframe):
        self.seed = seed
        self.speed = max(float(speed), 1.0)
        self.symbols = symbols
        self.timeframe = timeframe
        self.base = SyntheticProvider(seed=seed)
        self.base_end = self.base.end_date()
        self.t0 = time.monotonic()
        self._providers: dict[int, SyntheticProvider] = {}
        self._paths: dict[tuple[str, int], np.ndarray] = {}

    def position(self) -> tuple[int, float]:
        elapsed = (time.monotonic() - self.t0) * self.speed
        seconds = SESSION_MINUTES * 60
        return int(elapsed // seconds), (elapsed % seconds) / seconds

    def _provider(self, extra: int) -> SyntheticProvider:
        if extra not in self._providers:
            if len(self._providers) > 4:
                self._providers.pop(min(self._providers))
            self._providers[extra] = SyntheticProvider(seed=self.seed, end=self.base_end, extra_days=extra)
        return self._providers[extra]

    def session_date(self, day: int):
        d = self.base_end
        for _ in range(day + 1):
            d = next_trading_day(d)
        return d

    def bars(self, symbol: str) -> tuple[pd.DataFrame, float]:
        day, tau = self.position()
        full = self._provider(day + 1).daily(symbol)
        hist, today = full.iloc[:-1], full.iloc[-1]
        d = full.index[-1].date()
        key = (symbol, day)
        if key not in self._paths:
            if len(self._paths) > 64:
                self._paths.clear()
            self._paths[key] = self.base.market.intraday_path(symbol.upper(), today, d)
        path = self._paths[key]
        k = max(1, int(tau * (len(path) - 1)))
        seg = np.exp(path[: k + 1])
        if self.timeframe is Timeframe.D1:
            row = pd.DataFrame(
                {
                    "open": [seg[0]],
                    "high": [seg.max()],
                    "low": [seg.min()],
                    "close": [seg[-1]],
                    "volume": [round(float(today["volume"]) * tau)],
                },
                index=pd.DatetimeIndex([full.index[-1]], name="time"),
            )
            return pd.concat([hist, row]), tau
        # Intraday demo: previous sessions' intraday bars plus today's partial bars.
        prev = self._provider(day).history(symbol, self.timeframe)
        prev = prev.iloc[-HISTORY_BARS[self.timeframe] :]
        substeps = 6
        step = self.timeframe.minutes * substeps
        open_dt, _ = session_bounds(d)
        rows, idx = [], []
        for i in range(0, k + 1, step):
            part = np.exp(path[i : min(i + step, k) + 1])
            if len(part) == 0:
                break
            rows.append(
                (part[0], part.max(), part.min(), part[-1], float(today["volume"]) * len(part) / len(path))
            )
            idx.append(pd.Timestamp(open_dt).tz_convert("UTC") + pd.Timedelta(minutes=(i // substeps)))
        today_df = pd.DataFrame(
            rows, columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(idx, name="time")
        )
        return pd.concat([prev, today_df]), tau

    def status(self) -> dict[str, Any]:
        day, tau = self.position()
        d = self.session_date(day)
        minutes = int(tau * SESSION_MINUTES)
        clock = f"{9 + (30 + minutes) // 60:02d}:{(30 + minutes) % 60:02d}"
        return {
            "is_open": True,
            "phase": "demo",
            "session_date": d.isoformat(),
            "progress": tau,
            "clock": clock,
            "speed": self.speed,
        }


class LiveService:
    def __init__(self, data: DataService, settings: SettingsStore, recommender: Recommender | None = None):
        self.data = data
        self.settings = settings
        self.recommender = recommender or Recommender()
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._stream_task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._sig: tuple | None = None
        self._bars: dict[str, pd.DataFrame] = {}
        self._quotes: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, str] = {}
        self._streamed: dict[str, tuple[float, datetime]] = {}
        self._recs: dict[str, Any] | None = None
        self._last_recs = 0.0
        self._demo: DemoClock | None = None
        self._interest = asyncio.Event()  # set when someone looks at live data
        self._last_interest = time.monotonic()
        self.state: dict[str, Any] = {"status": "stopped", "error": None, "last_update": None}

    # -- lifecycle -----------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if not self.running:
            self.state["status"] = "starting"
            self._task = asyncio.create_task(self._run(), name="live-service")

    async def stop(self) -> None:
        for task in (self._task, self._stream_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._task = self._stream_task = None
        self._sig = None
        self.state["status"] = "stopped"
        await self._broadcast({"type": "update", "data": self.snapshot()})

    def poke(self) -> None:
        """Re-read settings and refresh immediately (after watchlist/provider changes)."""
        self._sig = None
        self._wake.set()

    # -- pub/sub -------------------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subscribers.add(q)
        self.touch()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)
        self._last_interest = time.monotonic()  # the grace period runs from the last viewer leaving

    def touch(self) -> None:
        """Someone is looking at live data (a Live Desk tab or an API call): keep updating, or resume."""
        self._last_interest = time.monotonic()
        self._interest.set()

    def _unwatched(self) -> bool:
        return not self._subscribers and time.monotonic() - self._last_interest > IDLE_AFTER

    async def _park(self) -> None:
        """Nobody is watching: stop polling, streaming and recomputing until someone is.

        Polling a provider and re-running every strategy for no one wastes CPU and data quota
        (and on a small hosted instance, slows everything else). Live data is dropped rather
        than left to go stale; it reloads when a viewer returns.
        """
        if self._stream_task is not None:
            self._stream_task.cancel()
            self._stream_task = None
        self._bars, self._quotes, self._errors, self._recs = {}, {}, {}, None
        self._streamed.clear()
        self._sig = None  # reload on waking (the demo clock stays, so the snapshot still says "demo")
        self.state.update(status="idle")
        self._interest.clear()
        await self._interest.wait()
        self.state.update(status="starting")

    async def _broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(sanitize(message), default=str)
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(payload)

    # -- snapshot --------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        s = self.settings.get()
        provider = s.provider
        info = next((p for p in self.data.catalog() if p["id"] == provider), {})
        market = self._demo.status() if self._demo else market_status().to_dict()
        return {
            **self.state,
            "running": self.running,
            "provider": provider,
            "provider_label": info.get("label", provider),
            "realtime": info.get("realtime"),
            "timeframe": s.timeframe,
            "symbols": s.watchlist(),
            "quotes": self._quotes,
            "errors": self._errors,
            "recommendations": (self._recs or {}).get("recommendations", []),
            "notes": (self._recs or {}).get("notes", []),
            "provisional": (self._recs or {}).get("provisional", False),
            "portfolio": (self._recs or {}).get("portfolio"),
            "market": market,
            "demo": self._demo is not None,
            "streaming": self._stream_task is not None and not self._stream_task.done(),
        }

    def bars(self, symbol: str) -> pd.DataFrame | None:
        return self._bars.get(symbol.upper())

    # -- main loop ---------------------------------------------------------------------------
    async def _run(self) -> None:
        while True:
            if self._unwatched():
                await self._park()
            s = self.settings.get()
            try:
                sig = (
                    s.provider,
                    s.timeframe,
                    tuple(s.watchlist()),
                    s.alpaca_feed,
                    s.alpaca_credentials(),
                    s.synthetic_seed,
                    s.demo_speed,
                    s.csv_dir,
                )
                if sig != self._sig:
                    await self._reload(s)
                    self._sig = sig
                await self._tick(s)
                self.state.update(
                    status="running", error=None, last_update=datetime.now(timezone.utc).isoformat()
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep the loop alive; surface the error to the UI
                log.exception("live update failed")
                self.state.update(status="error", error=f"{type(exc).__name__}: {exc}")
            await self._broadcast({"type": "update", "data": self.snapshot()})
            interval = self._interval(s)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def _interval(self, s) -> float:
        if self._demo is not None:
            return 1.0
        if self._stream_task is not None and not self._stream_task.done():
            return 2.0
        if not market_status().is_open:
            return max(s.poll_seconds, 60.0)
        return float(s.poll_seconds)

    async def _reload(self, s) -> None:
        tf = Timeframe.parse(s.timeframe)
        symbols = s.watchlist()
        self._bars, self._quotes, self._errors, self._recs = {}, {}, {}, None
        self._streamed.clear()
        if self._stream_task is not None:
            self._stream_task.cancel()
            self._stream_task = None
        self._demo = None
        if s.provider == "synthetic":
            self._demo = DemoClock(s.synthetic_seed, s.demo_speed, symbols, tf)
            return
        start, count = (HISTORY_START, None) if tf is Timeframe.D1 else (None, HISTORY_BARS[tf])
        frames, errors = await asyncio.to_thread(self.data.bars_many, symbols, tf, start, None, None, count=count)
        self._bars, self._errors = frames, errors
        provider = self.data.provider()
        if provider.supports_streaming and tf is Timeframe.D1 and frames:
            self._stream_task = asyncio.create_task(provider.stream_trades(list(frames), self._on_trade))
            self._stream_task.add_done_callback(self._stream_done)

    def _stream_done(self, task: asyncio.Task) -> None:
        """Streaming ended (e.g. rejected credentials): fall back to polling and say why."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.warning("trade stream stopped: %s", exc)
            self._errors["_stream"] = f"Real-time stream stopped ({exc}); falling back to polling."

    async def _on_trade(self, symbol: str, price: float, size: float, ts: datetime) -> None:
        if regular_session_date(ts) is None:
            return  # extended-hours trade: not part of the daily bar
        self._streamed[symbol.upper()] = (price, ts)

    async def _tick(self, s) -> None:
        tf = Timeframe.parse(s.timeframe)
        if self._demo is not None:
            for sym in s.watchlist():
                df, tau = self._demo.bars(sym)
                self._bars[sym] = df
        elif tf is Timeframe.D1:
            symbols = list(self._bars) or s.watchlist()
            try:
                quotes = await asyncio.to_thread(self.data.quotes, symbols)
            except DataError as exc:
                quotes = {}
                self._errors["_quotes"] = str(exc)
            for sym, q in quotes.items():
                if sym in self._streamed:
                    price, ts = self._streamed[sym]
                    if ts > q.timestamp:
                        q.price, q.timestamp = price, ts
                if sym in self._bars:
                    self._bars[sym] = merge_quote(self._bars[sym], q)
        else:
            frames, errors = await asyncio.to_thread(
                self.data.bars_many, s.watchlist(), tf, None, None, None, count=HISTORY_BARS[tf]
            )
            self._bars.update(frames)
            self._errors.update(errors)
        # Which symbols' last bar is still forming (the demo's current bar always is).
        now = datetime.now(timezone.utc)
        provisional = {
            sym: self._demo is not None or last_bar_forming(df, tf, now) for sym, df in self._bars.items()
        }

        for sym, df in self._bars.items():
            if len(df) >= 2:
                last, prev = df.iloc[-1], df.iloc[-2]
                self._quotes[sym] = {
                    "symbol": sym,
                    "price": float(last["close"]),
                    "open": float(last["open"]),
                    "high": float(last["high"]),
                    "low": float(last["low"]),
                    "volume": float(last["volume"]),
                    "prev_close": float(prev["close"]),
                    "change_pct": float(last["close"] / prev["close"] - 1.0),
                    "time": int(df.index[-1].timestamp()),
                    "spark": [round(float(x), 4) for x in df["close"].iloc[-40:]],
                }

        min_gap = 4.0 if self._demo is not None else 0.0
        if self._bars and time.monotonic() - self._last_recs >= min_gap:
            adv = AdvisorSettings.from_settings(s, periods_per_year=tf.periods_per_year)
            adv.pairs = s.active_pairs(list(self._bars))
            names = {row["symbol"]: row["name"] for row in universe_info()} if self._demo else None
            data = dict(self._bars)
            self._recs = await asyncio.to_thread(
                self.recommender.recommend,
                data,
                adv,
                provisional=provisional,
                namespace=f"{s.provider}:{tf.value}",
                names=names,
            )
            self._last_recs = time.monotonic()
