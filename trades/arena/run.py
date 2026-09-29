"""Strategy simulations: several strategies trade the same market, bar by bar, live.

A run owns a feed (simulated, replayed or real-time bars), one agent per strategy plus
a buy-and-hold benchmark, and an asyncio loop that reveals bars at a chosen speed and
pushes updates to websocket subscribers. Nothing here places real orders.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, fields
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from trades.arena.agent import StrategyAgent
from trades.arena.feeds import Feed, RealtimeFeed, ReplayFeed, SimulatedFeed
from trades.arena.market import ARCHETYPES, EVENT_KINDS, SimulatedMarket
from trades.backtest.engine import BacktestConfig
from trades.backtest.runner import StrategySpec, sanitize
from trades.core.timeframes import Timeframe
from trades.data.synthetic import PAIR_A, PAIR_B, SCENARIOS
from trades.strategies import Kind, get_strategy_class

log = logging.getLogger(__name__)

DEFAULT_SIM_SYMBOLS = ["SIMIDX", "SIMTEC", "SIMBND", "SIMGLD", PAIR_A, PAIR_B]
DEFAULT_STRATEGIES = [
    {"id": s}
    for s in (
        "tsmom",
        "faber_trend",
        "ma_crossover",
        "donchian_breakout",
        "bollinger_reversion",
        "rsi2_reversion",
        "pairs_trading",
        "xs_momentum",
    )
]
MAX_SPEED = 200.0  # bars per second
MIN_LOOKAHEAD = 48  # simulated bars generated ahead of the cursor
UPDATES_PER_SECOND = 10.0
MAX_EVENTS = 1500
HISTORY_SHOWN = 150  # warm-up bars included in the chart
MAX_WARMUP = 1500
WARMUP_MARGIN = 5  # extra history beyond a strategy's first possible signal


@dataclass
class RunConfig:
    source: str = "simulated"  # simulated | replay | realtime
    scenario: str = "random"
    seed: int | None = None
    symbols: list[str] = field(default_factory=lambda: list(DEFAULT_SIM_SYMBOLS))
    length: int = 0  # live bars; 0 = the scenario's story (simulated) or all data (replay)
    provider: str | None = None
    timeframe: str = "1d"
    start: str | None = None  # replay: first date traded (earlier bars warm up)
    end: str | None = None
    strategies: list[dict[str, Any]] = field(default_factory=lambda: [dict(s) for s in DEFAULT_STRATEGIES])
    pairs: list[list[str]] = field(default_factory=list)
    initial_cash: float = 100_000.0
    commission_bps: float = 0.0
    slippage_bps: float = 5.0
    allow_short: bool = True
    speed: float = 4.0  # bars per second
    warmup: int = 300

    def __post_init__(self):
        if self.source not in ("simulated", "replay", "realtime"):
            raise ValueError("source must be 'simulated', 'replay' or 'realtime'")
        if self.source == "simulated" and self.scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {self.scenario!r}")
        self.symbols = [str(s).strip().upper() for s in dict.fromkeys(self.symbols) if str(s).strip()]
        if not 1 <= len(self.symbols) <= 12:
            raise ValueError("pick between 1 and 12 symbols")
        if not 0 <= int(self.length) <= 5000:
            raise ValueError("length must be between 0 and 5000 bars")
        if not self.strategies:
            raise ValueError("pick at least one strategy")
        if len(self.strategies) > 16:
            raise ValueError("at most 16 strategies per simulation")
        if not 1_000 <= float(self.initial_cash) <= 1e9:
            raise ValueError("initial cash must be between 1,000 and 1,000,000,000")
        if not 0 <= self.slippage_bps <= 200 or not 0 <= self.commission_bps <= 200:
            raise ValueError("costs must be between 0 and 200 bps")
        if not 60 <= int(self.warmup) <= MAX_WARMUP:
            raise ValueError(f"warm-up must be between 60 and {MAX_WARMUP} bars")
        self.speed = min(max(float(self.speed), 0.1), MAX_SPEED)
        self.length = int(self.length)
        self.warmup = int(self.warmup)
        Timeframe.parse(self.timeframe)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> RunConfig:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known and v is not None})


class RunError(ValueError):
    pass


# ------------------------------------------------------------------------------------------
# Construction
# ------------------------------------------------------------------------------------------


def _pair_for(config: RunConfig, symbols: list[str]) -> list[str] | None:
    for pair in config.pairs:
        p = [s.upper() for s in pair]
        if len(p) == 2 and all(s in symbols for s in p):
            return p
    if PAIR_A in symbols and PAIR_B in symbols:
        return [PAIR_A, PAIR_B]
    return symbols[:2] if len(symbols) >= 2 else None


def build_agents(
    config: RunConfig, symbols: list[str], bt: BacktestConfig
) -> tuple[list[StrategyAgent], list[str]]:
    agents: list[StrategyAgent] = []
    notes: list[str] = []
    seen: dict[str, int] = {}
    for d in config.strategies:
        spec = StrategySpec.from_dict(d)
        try:
            cls = get_strategy_class(spec.id)
        except KeyError as exc:
            raise RunError(str(exc)) from exc
        if cls.kind is Kind.PAIR:
            pair = _pair_for(config, symbols)
            if pair is None:
                notes.append(f"{cls.name} needs two symbols; skipped.")
                continue
            syms = pair
        else:
            syms = symbols
        if len(syms) < cls.min_symbols:
            notes.append(f"{cls.name} needs at least {cls.min_symbols} symbols; skipped.")
            continue
        seen[spec.id] = seen.get(spec.id, 0) + 1
        agent_id = spec.id if seen[spec.id] == 1 else f"{spec.id}-{seen[spec.id]}"
        try:
            agent = StrategyAgent(agent_id, spec, syms, bt)
        except ValueError as exc:
            raise RunError(f"{cls.name}: {exc}") from exc
        if seen[spec.id] > 1:
            agent.name = f"{agent.name} #{seen[spec.id]}"
        if cls.uses_short and not bt.allow_short:
            notes.append(f"{cls.name} is designed to short; with shorting off it only takes the long side.")
        agents.append(agent)
    if not agents:
        raise RunError("none of the chosen strategies can trade these symbols")
    agents.append(StrategyAgent("benchmark", StrategySpec("buy_hold"), symbols, bt, benchmark=True))
    return agents, notes


def fit_warmup(config: RunConfig) -> list[str]:
    """Lengthen the warm-up so every chosen strategy can signal from the first live bar."""
    need, slowest = 0, ""
    for d in config.strategies:
        try:
            strat, _ = StrategySpec.from_dict(d).build()
        except (KeyError, TypeError, ValueError):
            continue  # reported when the agents are built
        if strat.warmup() > need:
            need, slowest = strat.warmup(), strat.name
    if need + WARMUP_MARGIN <= config.warmup:
        return []
    config.warmup = min(need + WARMUP_MARGIN, MAX_WARMUP)
    if need + WARMUP_MARGIN > MAX_WARMUP:
        return [
            f"{slowest} needs {need} bars of history, more than the {MAX_WARMUP}-bar warm-up; "
            "it stays in cash until it has enough."
        ]
    return [f"Warm-up lengthened to {config.warmup} bars: {slowest} needs {need} bars of history to start."]


def create_run(config: RunConfig, data_service=None) -> StrategyRun:
    tf = Timeframe.parse(config.timeframe)
    seed = config.seed if config.seed is not None else int(np.random.default_rng().integers(1, 1_000_000))
    notes: list[str] = fit_warmup(config)
    feed: Feed
    if config.source == "simulated":
        market = SimulatedMarket(config.symbols, config.scenario, seed, config.warmup)
        length = config.length or _story_length(market)
        feed = SimulatedFeed(market, length)
        config.seed = seed
        tf = Timeframe.D1
    else:
        if data_service is None:
            raise RunError("replay and real-time runs need a data service")
        provider = config.provider or data_service.settings.get().provider
        if config.source == "replay":
            feed = _replay_feed(config, data_service, provider, tf)
        else:
            if provider == "synthetic":
                raise RunError(
                    "Real-time runs need a real data source: choose Yahoo Finance or Alpaca in Settings "
                    "(or run a simulated market)."
                )
            feed = RealtimeFeed(data_service, config.symbols, tf, provider, config.warmup)
            notes.append(
                "Forward test on live data: strategies act when each bar completes"
                + (" (once a day at the close for daily bars)." if tf is Timeframe.D1 else ".")
            )
        config.provider = provider
    if tf.is_intraday:
        notes.append(
            "Strategy parameters are in bars. The published defaults assume daily bars, so on intraday bars "
            "a '252-bar' lookback covers days, not a year."
        )
    bt = BacktestConfig(
        initial_cash=config.initial_cash,
        commission_bps=config.commission_bps,
        slippage_bps=config.slippage_bps,
        allow_short=config.allow_short,
        periods_per_year=tf.periods_per_year,
    )
    agents, agent_notes = build_agents(config, feed.symbols, bt)
    return StrategyRun(config, feed, agents, notes + agent_notes)


def _story_length(market: SimulatedMarket) -> int:
    if market.script:
        return sum(n for _, n in market.script) - market.warmup
    return 500


def _replay_feed(config: RunConfig, data_service, provider: str, tf: Timeframe) -> ReplayFeed:
    if config.start:
        start = pd.Timestamp(config.start)
        if tf is Timeframe.D1:
            fetch_from = (start - timedelta(days=int(config.warmup * 1.5) + 20)).date()
        else:
            fetch_from = (start - timedelta(days=int(config.warmup / tf.bars_per_day * 1.6) + 5)).date()
        frames, errors = data_service.bars_many(config.symbols, tf, fetch_from, config.end, provider)
    else:
        count = config.warmup + (config.length or 500)
        frames, errors = data_service.bars_many(config.symbols, tf, None, config.end, provider, count=count)
    if errors:
        raise RunError("; ".join(f"{s}: {e}" for s, e in errors.items()))
    from trades.data.base import align_bars  # noqa: PLC0415 - keep import local to the replay path

    aligned = align_bars(frames)
    n = len(next(iter(aligned.values())))
    if config.start:
        first = int(np.searchsorted(next(iter(aligned.values())).index, pd.Timestamp(config.start, tz="UTC")))
        start_index = first - 1
        if start_index < 60:
            raise RunError(f"not enough history before {config.start} to warm up the strategies")
    else:
        start_index = min(config.warmup, n - 2) - 1
    if config.length:
        end = min(start_index + config.length, n - 1)
        aligned = {s: df.iloc[: end + 1] for s, df in aligned.items()}
    return ReplayFeed(aligned, start_index, tf, provider)


# ------------------------------------------------------------------------------------------
# The run
# ------------------------------------------------------------------------------------------


class StrategyRun:
    def __init__(self, config: RunConfig, feed: Feed, agents: list[StrategyAgent], notes: list[str]):
        self.id = secrets.token_hex(5)
        self.created_at = time.time()
        self.config = config
        self.feed = feed
        self.agents = agents
        self.notes = notes
        self.status = "ready"  # ready | running | paused | finished | error
        self.error: str | None = None
        self.speed = config.speed
        self.events: list[dict[str, Any]] = []
        self.market_events: list[dict[str, Any]] = []
        self.summary: dict[str, Any] | None = None
        self._lock = threading.RLock()
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._cols = {a.id: [feed.symbols.index(s) for s in a.symbols] for a in agents}
        self.cursor = feed.start_index
        self._regime: str | None = None
        self._fit_lookahead()
        self._prepare(self.cursor)
        t0 = feed.time(self.cursor)
        o, h, l, c = feed.arrays(self.cursor)
        for a in agents:
            j = self._cols[a.id]
            a.begin(self.cursor, t0, o[j], h[j], l[j], c[j])
        self._note_regime(self.cursor, initial=True)

    # -- bar processing (worker thread) ---------------------------------------------------
    def _fit_lookahead(self) -> None:
        """Generate about a second of play ahead of the cursor. Agents recompute their signals
        when the market grows, so fast runs recompute less often (signals are causal, so the
        unrevealed bars never change what an agent sees)."""
        if isinstance(self.feed, SimulatedFeed):
            self.feed.lookahead = int(min(max(self.speed, MIN_LOOKAHEAD), MAX_SPEED))

    def _prepare(self, t: int) -> None:
        stale = [a for a in self.agents if a.prepared_until < t]
        if not stale:
            return
        self.feed.ensure(t + 1)
        frames = self.feed.frames()
        for a in stale:
            a.prepare(frames)

    def _advance(self) -> list[dict[str, Any]]:
        """Reveal bar ``cursor + 1``: every agent fills, marks and decides."""
        with self._lock:
            t = self.cursor + 1
            self.feed.ensure(t + 1)  # the simulated market generates ahead (under the lock)
            self._prepare(t)
            o, h, l, c = self.feed.arrays(t)
            ts = self.feed.time(t)
            events: list[dict[str, Any]] = []
            for a in self.agents:
                j = self._cols[a.id]
                events += a.on_bar(t, ts, o[j], h[j], l[j], c[j])
            self.cursor = t
            events += self._note_regime(t)
            self._log(events)
            return events

    def _note_regime(self, t: int, initial: bool = False) -> list[dict[str, Any]]:
        regime = self.feed.regime(t)
        if regime is None or regime == self._regime:
            return []
        prev, self._regime = self._regime, regime
        if initial:
            return []
        ev = {
            "t": t,
            "time": int(self.feed.time(t).timestamp()),
            "agent": None,
            "type": "regime",
            "from": prev,
            "to": regime,
            "headline": f"Hidden regime changed: {prev} -> {regime}. The strategies cannot see this; "
            "watch how long each takes to adapt.",
        }
        self.market_events.append(ev)
        return [ev]

    def _log(self, events: list[dict[str, Any]]) -> None:
        self.events.extend(events)
        if len(self.events) > MAX_EVENTS:
            del self.events[: len(self.events) - MAX_EVENTS]

    # -- state ---------------------------------------------------------------------------
    def prices(self) -> dict[str, float]:
        _, _, _, c = self.feed.arrays(self.cursor)
        return {s: float(c[i]) for i, s in enumerate(self.feed.symbols)}

    def progress(self) -> dict[str, Any]:
        last = self.feed.last_index()
        done = self.cursor - self.feed.start_index
        return {"done": done, "total": (last - self.feed.start_index) if last is not None else None}

    def _update(self, first: int, last: int, events: list[dict[str, Any]]) -> dict[str, Any]:
        prices = self.prices()
        n = last - first + 1
        return {
            "type": "update",
            "status": self.status,
            "cursor": self.cursor,
            "progress": self.progress(),
            "bars": self.feed.bars_payload(first, last) if n > 0 else {},
            "regimes": [self.feed.regime(t) for t in range(first, last + 1)] if n > 0 else [],
            "regime_drift": self.feed.regime_drift(),
            "equity": {a.id: [round(v, 2) for v in a.equity[-n:]] for a in self.agents} if n > 0 else {},
            "agents": [a.snapshot(prices) for a in self.agents],
            "events": events,
            "feed_status": self.feed.status(),
            "speed": self.speed,
            "summary": self.summary,
            "error": self.error,
        }

    def state(self) -> dict[str, Any]:
        with self._lock:
            prices = self.prices()
            first = max(0, self.feed.start_index - HISTORY_SHOWN)
            start = self.feed.start_index
            regimes = None
            if self.feed.regime(start) is not None:
                regimes = [self.feed.regime(t) for t in range(first, self.cursor + 1)]
            return sanitize(
                {
                    "id": self.id,
                    "created_at": self.created_at,
                    "status": self.status,
                    "error": self.error,
                    "config": asdict(self.config),
                    "feed": self.feed.info(),
                    "feed_status": self.feed.status(),
                    "notes": self.notes,
                    "cursor": self.cursor,
                    "start_index": start,
                    "first_index": first,
                    "progress": self.progress(),
                    "speed": self.speed,
                    "bars": self.feed.bars_payload(first, self.cursor),
                    "regimes": regimes,
                    "regime_drift": self.feed.regime_drift(),
                    "agents": [
                        {
                            **a.describe(),
                            **a.snapshot(prices),
                            "equity_curve": {"t": a.times, "v": [round(v, 2) for v in a.equity]},
                        }
                        for a in self.agents
                    ],
                    "events": self.events[-400:],
                    "market_events": self.market_events,
                    "summary": self.summary,
                }
            )

    def agent_detail(self, agent_id: str) -> dict[str, Any]:
        with self._lock:
            agent = next((a for a in self.agents if a.id == agent_id), None)
            if agent is None:
                raise KeyError(agent_id)
            return sanitize(agent.detail(self.prices()))

    def brief(self) -> dict[str, Any]:
        leader = max(self.agents, key=lambda a: a.engine.equity)
        info = self.feed.info()
        return sanitize(
            {
                "id": self.id,
                "created_at": self.created_at,
                "status": self.status,
                "source": self.config.source,
                "title": info.get("scenario_label") or ", ".join(self.feed.symbols[:4]),
                "symbols": self.feed.symbols,
                "strategies": len(self.agents) - 1,
                "progress": self.progress(),
                "leader": {
                    "name": leader.name,
                    "return": leader.engine.equity / leader.engine.cfg.initial_cash - 1,
                },
            }
        )

    # -- summary ---------------------------------------------------------------------------
    def _summarise(self) -> dict[str, Any]:
        with self._lock:  # a cancelled loop's last bar may still be finishing in a worker
            return self._summary_locked()

    def _summary_locked(self) -> dict[str, Any]:
        bench = next((a for a in self.agents if a.benchmark), None)
        bench_eq = bench.equity_series() if bench is not None else None
        rows = []
        for a in self.agents:
            m = a.metrics(None if a.benchmark else bench_eq)
            rows.append(
                {
                    "id": a.id,
                    "name": a.name,
                    "kind": "benchmark" if a.benchmark else "strategy",
                    "total_return": m.get("total_return"),
                    "cagr": m.get("cagr"),
                    "sharpe": m.get("sharpe"),
                    "max_drawdown": m.get("max_drawdown"),
                    "psr": m.get("psr"),
                    "trades": m.get("n_trades"),
                    "win_rate": m.get("win_rate"),
                    "exposure": m.get("exposure"),
                    "total_costs": m.get("total_costs"),
                    "beta": m.get("beta"),
                }
            )
        rows.sort(key=lambda r: -(r["total_return"] if r["total_return"] is not None else -math.inf))
        bars = self.cursor - self.feed.start_index
        cautions = []
        years = bars / max(Timeframe.parse(self.config.timeframe).periods_per_year, 1)
        if years < 3:
            cautions.append(
                f"{bars} bars is a short sample: rankings over a single path owe a lot to luck. Re-run with "
                "other seeds (or periods) before drawing conclusions."
            )
        if self.config.source == "simulated":
            cautions.append(
                "Simulated prices follow a model with no built-in edge for any strategy; what you learn here is "
                "how each rule behaves in different conditions, not which one makes money."
            )
        return {
            "leaderboard": rows,
            "bars": bars,
            "regimes": self._regime_attribution(),
            "cautions": cautions,
        }

    def _regime_attribution(self) -> list[dict[str, Any]] | None:
        start = self.feed.start_index
        if self.feed.regime(start) is None or self.cursor <= start:
            return None
        labels = [self.feed.regime(t) for t in range(start + 1, self.cursor + 1)]
        out = []
        for label in dict.fromkeys(labels):
            mask = np.array([x == label for x in labels])
            row = {
                "regime": label,
                "bars": int(mask.sum()),
                "drift": self.feed.regime_drift().get(label),
                "returns": {},
            }
            for a in self.agents:
                eq = np.asarray(a.equity, float)
                growth = eq[1:] / eq[:-1]
                g = growth[: len(mask)][mask[: len(growth)]]
                row["returns"][a.id] = float(np.prod(g) - 1.0) if len(g) else None
            out.append(row)
        return out

    # -- pub/sub -------------------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def _broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(sanitize(message), default=str)
        for q in list(self._subscribers):
            if q.full():  # a slow client: drop its backlog and ask it to reload
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(json.dumps({"type": "resync"}))
                continue
            q.put_nowait(payload)

    # -- controls ------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def play(self) -> None:
        if self.status in ("finished", "error"):
            raise RunError(f"this simulation is {self.status}")
        self.status = "running"
        if not self.running:
            loop = self._realtime_loop if isinstance(self.feed, RealtimeFeed) else self._loop
            self._task = asyncio.create_task(loop(), name=f"arena-{self.id}")
        await self._broadcast(self._update(self.cursor + 1, self.cursor, []))

    async def pause(self) -> None:
        if self.status == "running":
            self.status = "paused"
        await self._broadcast(self._update(self.cursor + 1, self.cursor, []))

    async def step(self, n: int = 1) -> None:
        if self.status in ("finished", "error"):
            raise RunError(f"this simulation is {self.status}")
        if self.status == "running":
            await self.pause()
            if self._task is not None:
                await asyncio.wait({self._task}, timeout=5)
        if isinstance(self.feed, RealtimeFeed):
            raise RunError("a real-time run advances with the market; it cannot be stepped")
        self.status = "paused"
        await self._run_bars(max(1, min(int(n), 1000)))

    async def set_speed(self, speed: float) -> None:
        self.speed = min(max(float(speed), 0.1), MAX_SPEED)
        self._fit_lookahead()
        await self._broadcast(self._update(self.cursor + 1, self.cursor, []))

    async def stop(self) -> None:
        if self.status in ("finished", "error"):
            return
        self.status = "finished"
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._finish()

    async def inject(self, kind: str, **params) -> dict[str, Any]:
        if not isinstance(self.feed, SimulatedFeed):
            raise RunError("events can only be injected into a simulated market")
        if self.status in ("finished", "error"):
            raise RunError(f"this simulation is {self.status}")
        # Taking the lock can wait for a bar in progress: do it off the event loop.
        record = await asyncio.to_thread(self._inject, kind, params)
        await self._broadcast({**self._update(self.cursor + 1, self.cursor, [record])})
        return record

    def _inject(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(self.feed, SimulatedFeed)
        with self._lock:
            at = self.cursor + 1
            try:
                ev = self.feed.inject(kind, at, **params)
            except ValueError as exc:
                raise RunError(str(exc)) from exc
            for a in self.agents:
                a.invalidate(at)
            record = {
                "t": at,
                "time": int(self.feed.time(at).timestamp()),
                "agent": None,
                "type": "injected",
                "kind": kind,
                "headline": f"You injected an event: {ev.description} It starts on the next bar.",
                "event": ev.to_dict(),
            }
            self.market_events.append(record)
            self._log([record])
            return record

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._subscribers.clear()

    # -- loops ---------------------------------------------------------------------------
    def _has_next(self) -> bool:
        last = self.feed.last_index()
        return last is None or self.cursor < last

    async def _run_bars(self, n: int) -> int:
        """Advance up to ``n`` bars and broadcast them as one update."""
        first = self.cursor + 1
        events: list[dict[str, Any]] = []
        done = 0
        for _ in range(n):
            if not self._has_next():
                await self._broadcast(self._update(first, self.cursor, events))
                await self._finish()
                return done
            events += await asyncio.to_thread(self._advance)
            done += 1
        if done:
            await self._broadcast(self._update(first, self.cursor, events))
        last = self.feed.last_index()
        if last is not None and self.cursor >= last:
            await self._finish()
        return done

    async def _loop(self) -> None:
        try:
            while self.status == "running":
                started = time.monotonic()
                batch = max(1, round(self.speed / UPDATES_PER_SECOND))
                await self._run_bars(batch)
                if self.status != "running":
                    break
                wait = batch / self.speed - (time.monotonic() - started)
                await asyncio.sleep(max(wait, 0.0))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("strategy simulation %s failed", self.id)
            self.status, self.error = "error", f"{type(exc).__name__}: {exc}"
            await self._broadcast(self._update(self.cursor + 1, self.cursor, []))

    async def _realtime_loop(self) -> None:
        feed = self.feed
        assert isinstance(feed, RealtimeFeed)
        try:
            while self.status == "running":
                try:
                    recent = await asyncio.to_thread(feed.fetch)  # network I/O, outside the lock
                    await asyncio.to_thread(self._apply_bars, recent)
                    feed.error = None
                except Exception as exc:  # keep polling; surface the problem
                    feed.error = f"{type(exc).__name__}: {exc}"
                first = self.cursor + 1
                events: list[dict[str, Any]] = []
                while self.status == "running" and feed.known() > self.cursor + 1:
                    events += await asyncio.to_thread(self._advance)
                await self._broadcast(self._update(first, self.cursor, events))
                await asyncio.sleep(feed.interval())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("real-time simulation %s failed", self.id)
            self.status, self.error = "error", f"{type(exc).__name__}: {exc}"
            await self._broadcast(self._update(self.cursor + 1, self.cursor, []))

    def _apply_bars(self, recent: dict[str, pd.DataFrame]) -> int:
        with self._lock:
            assert isinstance(self.feed, RealtimeFeed)
            return self.feed.apply(recent)

    async def _finish(self) -> None:
        self.status = "finished"
        self.summary = await asyncio.to_thread(self._summarise)
        await self._broadcast({**self._update(self.cursor + 1, self.cursor, []), "type": "finished"})


class RunManager:
    def __init__(self, max_runs: int = 12, max_active: int = 4):
        self._runs: OrderedDict[str, StrategyRun] = OrderedDict()
        self.max_runs = max_runs
        self.max_active = max_active

    def add(self, run: StrategyRun) -> StrategyRun:
        running = [r for r in self._runs.values() if r.running]
        if len(running) >= self.max_active:
            raise RunError(
                f"{len(running)} simulations are already running; pause or stop one before starting another."
            )
        self._runs[run.id] = run
        while len(self._runs) > self.max_runs:
            victim = next((r for r in self._runs.values() if not r.running), None)
            if victim is None:
                break
            self._runs.pop(victim.id)
        return run

    def get(self, run_id: str) -> StrategyRun:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def list(self) -> list[dict[str, Any]]:
        return [r.brief() for r in reversed(self._runs.values())]

    async def delete(self, run_id: str) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            raise KeyError(run_id)
        await run.close()

    async def shutdown(self) -> None:
        for run in list(self._runs.values()):
            await run.close()


def options(settings) -> dict[str, Any]:
    """Choices for the set-up form."""
    from trades.data.synthetic import universe_info  # noqa: PLC0415

    return {
        "scenarios": [
            {"id": s.id, "label": s.label, "description": s.description, "difficulty": s.difficulty}
            for s in SCENARIOS.values()
        ],
        "symbols": universe_info(),
        "default_symbols": DEFAULT_SIM_SYMBOLS,
        "default_strategies": DEFAULT_STRATEGIES,
        "events": [{"kind": k, **v} for k, v in EVENT_KINDS.items()],
        "regimes": list(ARCHETYPES),
        "max_speed": MAX_SPEED,
        "provider": settings.provider,
        "realtime_available": settings.provider != "synthetic",
        "watchlist": settings.watchlist(),
    }
