"""Bar sources for strategy simulations.

* ``SimulatedFeed``: the steerable simulated market (see ``market.py``);
* ``ReplayFeed``: real (or synthetic) history replayed bar by bar;
* ``RealtimeFeed``: the connected market data provider, polled for newly completed bars.

A feed exposes completed bars only. Bars at or before ``cursor`` are visible; the
simulated and replay feeds may also know bars *after* the cursor (generated ahead or
loaded), which strategies can use to precompute their causal signals but which are
never shown or traded on before the cursor reaches them.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from trades.arena.market import EVENT_KINDS, MarketEvent, SimulatedMarket
from trades.core.calendar import market_status
from trades.core.timeframes import Timeframe
from trades.data.base import DataError, align_bars, last_bar_forming


class Feed(ABC):
    source: str
    symbols: list[str]
    timeframe: Timeframe
    start_index: int  # last warm-up bar: strategies take their first positions at its close

    @abstractmethod
    def known(self) -> int:
        """Number of bars known (visible or not)."""

    def ensure(self, n: int) -> None:
        """Try to know at least ``n`` bars (the simulated market generates ahead)."""

    @abstractmethod
    def last_index(self) -> int | None:
        """Index of the final bar of the run, or None if open-ended."""

    @abstractmethod
    def frames(self) -> dict[str, pd.DataFrame]:
        """All known bars per symbol, on one common index."""

    @abstractmethod
    def arrays(self, t: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Open, high, low and close of bar ``t`` in ``symbols`` order."""

    @abstractmethod
    def time(self, t: int) -> pd.Timestamp: ...

    def regime(self, t: int) -> str | None:
        return None

    def regime_drift(self, upto: int) -> dict[str, float]:
        """Annual drift of each hidden regime seen up to bar ``upto`` (simulated markets only)."""
        return {}

    def bars_payload(self, first: int, last: int) -> dict[str, Any]:
        """Bars ``first..last`` (inclusive) as compact arrays per symbol."""
        frames = self.frames()
        out: dict[str, Any] = {}
        for s in self.symbols:
            df = frames[s].iloc[first : last + 1]
            out[s] = {
                "t": pd.DatetimeIndex(df.index).as_unit("s").asi8.tolist(),
                "o": np.round(df["open"].to_numpy(float), 4).tolist(),
                "h": np.round(df["high"].to_numpy(float), 4).tolist(),
                "l": np.round(df["low"].to_numpy(float), 4).tolist(),
                "c": np.round(df["close"].to_numpy(float), 4).tolist(),
                "v": np.round(df["volume"].to_numpy(float), 0).tolist(),
            }
        return out

    def info(self) -> dict[str, Any]:
        return {"source": self.source, "symbols": self.symbols, "timeframe": self.timeframe.value}

    def status(self) -> dict[str, Any]:
        return {}


class _FrameFeed(Feed):
    """Shared plumbing for feeds backed by aligned DataFrames."""

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self._frames = frames
        self._cols: dict[str, np.ndarray] = {}
        self._refresh()

    def _refresh(self) -> None:
        f = self._frames
        self._cols = {
            k: np.column_stack([f[s][k].to_numpy(float) for s in self.symbols])
            for k in ("open", "high", "low", "close")
        }
        self._index = next(iter(f.values())).index

    def known(self) -> int:
        return len(self._index)

    def frames(self) -> dict[str, pd.DataFrame]:
        return self._frames

    def arrays(self, t):
        c = self._cols
        return c["open"][t], c["high"][t], c["low"][t], c["close"][t]

    def time(self, t: int) -> pd.Timestamp:
        return self._index[t]


class SimulatedFeed(Feed):
    source = "simulated"

    def __init__(self, market: SimulatedMarket, length: int, lookahead: int = 48):
        self.market = market
        self.symbols = list(market.symbols)
        self.timeframe = Timeframe.D1
        self.start_index = market.warmup - 1
        self.length = int(length)
        self.lookahead = lookahead
        self._frames: dict[str, pd.DataFrame] | None = None
        self._frames_n = -1

    def known(self) -> int:
        return len(self.market)

    def last_index(self) -> int | None:
        return self.start_index + self.length

    def ensure(self, n: int) -> None:
        target = min(n + self.lookahead, (self.last_index() or n) + 1)
        if target > len(self.market):
            self.market.generate_until(target)

    def frames(self) -> dict[str, pd.DataFrame]:
        if self._frames is None or self._frames_n != len(self.market):
            self._frames = self.market.frames()
            self._frames_n = len(self.market)
        return self._frames

    def arrays(self, t):
        return self.market.arrays(t)

    def time(self, t: int) -> pd.Timestamp:
        return self.market.time(t)

    def regime(self, t: int) -> str | None:
        return self.market.regime(t)

    def regime_drift(self, upto: int) -> dict[str, float]:
        # Only regimes already revealed: bars generated ahead must not hint at what is coming.
        return self.market.drifts(upto)

    def bars_payload(self, first: int, last: int) -> dict[str, Any]:
        return self.market.bars_payload(first, last)  # straight from the arrays, no frames rebuilt

    def inject(self, kind: str, at: int, **kw) -> MarketEvent:
        ev = self.market.inject(kind, at, **kw)
        self._frames = None
        return ev

    def info(self) -> dict[str, Any]:
        sc = self.market.scenario
        return {
            **super().info(),
            "scenario": sc.id,
            "scenario_label": sc.label,
            "scenario_description": sc.description,
            "seed": self.market.seed,
            "events_available": [{"kind": k, **v} for k, v in EVENT_KINDS.items()],
        }


class ReplayFeed(_FrameFeed):
    source = "replay"

    def __init__(
        self, frames: dict[str, pd.DataFrame], start_index: int, timeframe: Timeframe, provider: str
    ):
        aligned = align_bars(frames)
        self.symbols = list(aligned)
        self.timeframe = timeframe
        self.provider = provider
        n = len(next(iter(aligned.values())))
        if n < 3 or start_index >= n - 1:
            raise ValueError("not enough bars to replay after the warm-up")
        self.start_index = int(start_index)
        super().__init__(aligned)

    def last_index(self) -> int | None:
        return self.known() - 1

    def info(self) -> dict[str, Any]:
        return {**super().info(), "provider": self.provider}


class RealtimeFeed(_FrameFeed):
    """Completed bars from the connected provider as they happen.

    Polls the provider; a bar is appended once it has completed (its session or interval
    has ended) for every symbol. A symbol's bar can reach the provider a little later than
    another's, so a timestamp is held back until each symbol has either its bar there or a
    later one. A symbol that moved on without a bar at that time (no trades in that minute),
    or that is still silent after a grace period, gets a flat bar at its previous close,
    keeping the agents in step.
    """

    source = "realtime"

    def __init__(self, data_service, symbols: list[str], timeframe: Timeframe, provider: str, warmup: int):
        self.data = data_service
        self.symbols = [s.upper() for s in dict.fromkeys(symbols)]
        self.timeframe = timeframe
        self.provider = provider
        self.poll_seconds = 5.0 if timeframe.is_intraday else 30.0
        self.grace_seconds = 3 * self.poll_seconds  # how long to wait for a lagging symbol's bar
        self._held: dict[pd.Timestamp, float] = {}  # held-back timestamp -> when first seen
        frames, errors = data_service.bars_many(
            self.symbols, timeframe, None, None, provider, count=warmup + 20
        )
        if errors:
            raise DataError("; ".join(f"{s}: {e}" for s, e in errors.items()))
        completed = {s: self._completed(df) for s, df in frames.items()}
        aligned = align_bars(completed)
        n = len(next(iter(aligned.values())))
        if n < 30:
            raise DataError("not enough history from the provider to warm up the strategies")
        self.start_index = n - 1
        self.forming: dict[str, dict[str, float]] = {}
        self.last_poll: str | None = None
        self.error: str | None = None
        super().__init__(aligned)

    def _completed(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.iloc[:-1] if last_bar_forming(df, self.timeframe) else df

    def interval(self) -> float:
        """Seconds between polls: frequent during the session, relaxed while it is closed."""
        return self.poll_seconds if market_status().is_open else 60.0

    def last_index(self) -> int | None:
        return None

    def poll(self) -> int:
        """Fetch recent bars and append newly completed ones. Returns how many were added."""
        return self.apply(self.fetch())

    def fetch(self) -> dict[str, pd.DataFrame]:
        """Recent bars from the provider (network I/O; changes nothing)."""
        prov = self.data.provider(self.provider)
        start = self._index[-1] - pd.Timedelta(days=5 if self.timeframe is Timeframe.D1 else 1)
        return {s: prov.history(s, self.timeframe, start.to_pydatetime(), None) for s in self.symbols}

    def apply(self, recent: dict[str, pd.DataFrame]) -> int:
        """Append the completed bars in ``recent`` that are newer than the last known one."""
        self.last_poll = datetime.now(timezone.utc).isoformat()
        self.forming = {}
        for s, df in recent.items():
            if len(df) and last_bar_forming(df, self.timeframe):
                row = df.iloc[-1]
                self.forming[s] = {
                    "t": int(df.index[-1].timestamp()),
                    **{k[0]: float(row[k]) for k in ("open", "high", "low", "close", "volume")},
                }
        last = self._index[-1]
        done = {s: self._completed(recent[s]) for s in self.symbols}
        now = time.monotonic()
        new_times = []
        for ts in sorted({ts for df in done.values() for ts in df.index if ts > last}):
            lagging = [s for s in self.symbols if ts not in done[s].index and not (done[s].index > ts).any()]
            if lagging and now - self._held.setdefault(ts, now) < self.grace_seconds:
                break  # wait for the lagging symbols' bars (this and later timestamps)
            new_times.append(ts)
        self._held = {ts: t0 for ts, t0 in self._held.items() if not new_times or ts > new_times[-1]}
        if not new_times:
            return 0
        frames = dict(self._frames)
        for s in self.symbols:
            prev_close = float(frames[s]["close"].iloc[-1])
            rows = []
            for ts in new_times:
                if ts in done[s].index:
                    r = done[s].loc[ts]
                    rows.append([r["open"], r["high"], r["low"], r["close"], r["volume"]])
                    prev_close = float(r["close"])
                else:  # no trade in this interval: a flat bar keeps every symbol in step
                    rows.append([prev_close, prev_close, prev_close, prev_close, 0.0])
            add = pd.DataFrame(
                rows, columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(new_times)
            )
            frames[s] = pd.concat([frames[s], add])
        self._frames = frames
        self._refresh()
        return len(new_times)

    def info(self) -> dict[str, Any]:
        return {**super().info(), "provider": self.provider, "poll_seconds": self.poll_seconds}

    def status(self) -> dict[str, Any]:
        return {
            "market": market_status().to_dict(),
            "forming": self.forming,
            "last_poll": self.last_poll,
            "error": self.error,
        }
