"""Replay trading sessions: step through history bar by bar with a paper account.

The future is never sent to the client: ``state()`` only includes bars up to the
cursor. Advisor strategies run on the same data (their signals are causal, so
computing them once over the full series and revealing them bar by bar is equivalent
to recomputing each bar), and their "ghost" portfolios trade from the same starting
point so you can race them.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, fields
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trades.backtest.engine import BacktestConfig, buy_and_hold_weights, run_backtest
from trades.backtest.runner import StrategySpec, bars_payload, trade_payload
from trades.core.calendar import trading_days
from trades.core.ledger import EPS
from trades.data.synthetic import SCENARIO_BASE_DATE, SCENARIOS, scenario_bars, scenario_regimes
from trades.sim.broker import Bar, OrderError, PaperBroker, Side, Status
from trades.strategies import Kind, get_strategy_class
from trades.strategies.sizing import apply_sizing

DEFAULT_ADVISORS = [
    {"id": "tsmom"},
    {"id": "faber_trend"},
    {"id": "donchian_breakout"},
    {"id": "rsi2_reversion"},
    {"id": "bollinger_reversion"},
]

RANDOM_HISTORY_UNIVERSE = (
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "AAPL",
    "MSFT",
    "AMZN",
    "GOOGL",
    "NVDA",
    "JPM",
    "XOM",
    "JNJ",
    "PG",
    "KO",
    "WMT",
    "DIS",
    "BA",
    "INTC",
    "CSCO",
    "CAT",
    "GLD",
    "TLT",
    "XLE",
    "XLF",
)

HISTORICAL_PRESETS: tuple[dict[str, str], ...] = (
    {
        "id": "gfc",
        "label": "Global Financial Crisis",
        "symbol": "SPY",
        "start": "2008-05-01",
        "description": "From the Bear Stearns aftermath through the Lehman collapse to the March 2009 bottom.",
    },
    {
        "id": "covid",
        "label": "COVID-19 crash and rebound",
        "symbol": "SPY",
        "start": "2020-01-02",
        "description": "The fastest 30% decline in history, followed by a powerful stimulus-driven recovery.",
    },
    {
        "id": "dotcom",
        "label": "Dot-com bust",
        "symbol": "QQQ",
        "start": "2000-03-01",
        "description": "The Nasdaq-100 from its bubble peak into the first year of a 78% decline.",
    },
    {
        "id": "hikes_2022",
        "label": "2022 rate-hike bear market",
        "symbol": "QQQ",
        "start": "2022-01-03",
        "description": "Growth stocks de-rate as the Fed raises rates at the fastest pace in decades.",
    },
    {
        "id": "gme",
        "label": "GameStop short squeeze",
        "symbol": "GME",
        "start": "2020-10-01",
        "description": "Extreme volatility: a retail-driven short squeeze and its aftermath.",
    },
    {
        "id": "ai_boom",
        "label": "AI boom",
        "symbol": "NVDA",
        "start": "2023-01-03",
        "description": "A mega-cap in a powerful momentum run. Can you hold a winner?",
    },
    {
        "id": "banks_2023",
        "label": "Regional bank crisis",
        "symbol": "KRE",
        "start": "2022-09-01",
        "description": "Regional banks through the Silicon Valley Bank failure in March 2023.",
    },
)


@dataclass
class SimConfig:
    source: str = "scenario"  # scenario | history
    scenario: str = "random"
    symbol: str = ""  # history: blank = random symbol
    start: str | None = None  # history: first date of the trading session (history before it is warm-up)
    preset: str | None = None
    provider: str | None = None
    seed: int | None = None
    initial_cash: float = 100_000.0
    commission_bps: float = 0.0
    slippage_bps: float = 5.0
    allow_short: bool = False
    max_leverage: float = 1.0
    advisors: list[dict[str, Any]] = field(default_factory=lambda: [dict(a) for a in DEFAULT_ADVISORS])
    reveal: str = "live"  # live | end | off
    blind: bool = True
    warmup_bars: int = 250
    length_bars: int = 250

    def __post_init__(self):
        if self.source not in ("scenario", "history"):
            raise ValueError("source must be 'scenario' or 'history'")
        if self.reveal not in ("live", "end", "off"):
            raise ValueError("reveal must be 'live', 'end' or 'off'")
        if not (50 <= self.warmup_bars <= 1000):
            raise ValueError("warmup_bars must be between 50 and 1000")
        if not (20 <= self.length_bars <= 1500):
            raise ValueError("length_bars must be between 20 and 1500")
        if not (1_000 <= self.initial_cash <= 1e9):
            raise ValueError("initial_cash must be between 1,000 and 1,000,000,000")
        if not (0.1 <= self.max_leverage <= 4):
            raise ValueError("max_leverage must be between 0.1 and 4")
        if self.source == "scenario" and self.scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {self.scenario!r}")

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> SimConfig:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known and v is not None})


class SimError(ValueError):
    pass


class SimSession:
    def __init__(
        self,
        config: SimConfig,
        bars: pd.DataFrame,
        *,
        symbol: str,
        reveal: dict[str, Any],
        title: str,
        description: str,
    ):
        if len(bars) < 30:
            raise SimError("not enough bars for a session")
        self.id = secrets.token_hex(6)
        self.created_at = time.time()
        self.config = config
        self.bars = bars
        self.symbol = symbol
        self.title = title
        self.description = description
        self._reveal = reveal
        self.start_index = min(config.warmup_bars, len(bars) - 2) - 1
        self.start_index = max(self.start_index, 0)
        self.end_index = min(self.start_index + config.length_bars, len(bars) - 1)
        self.cursor = self.start_index
        self.broker = PaperBroker(
            config.initial_cash,
            commission_bps=config.commission_bps,
            slippage_bps=config.slippage_bps,
            allow_short=config.allow_short,
            max_leverage=config.max_leverage,
        )
        self.broker.last_prices[symbol] = float(bars["close"].iloc[self.cursor])
        self.equity: list[float] = [config.initial_cash]
        self.exposure: list[float] = [0.0]
        self.decisions: list[dict[str, Any]] = []
        self.journal: list[dict[str, Any]] = []
        self.finished = False
        self.scorecard: dict[str, Any] | None = None
        self.lock = threading.RLock()
        self._build_ghosts()

    # -- advisors / ghost portfolios ------------------------------------------------------
    def _build_ghosts(self) -> None:
        data = {self.symbol: self.bars}
        cfg = BacktestConfig(
            initial_cash=self.config.initial_cash,
            commission_bps=self.config.commission_bps,
            slippage_bps=self.config.slippage_bps,
            allow_short=self.config.allow_short,
        )
        self.advisors: list[dict[str, Any]] = []
        for d in self.config.advisors:
            try:
                spec = StrategySpec.from_dict(d)
                cls = get_strategy_class(spec.id)
                if not self.config.allow_short:
                    # Keep the ghosts' opinions consistent with a no-shorting account.
                    names = {p.name for p in cls.params_spec}
                    if "long_only" in names:
                        spec.params.setdefault("long_only", True)
                    if "allow_short" in names:
                        spec.params["allow_short"] = False
                strat, sizing = spec.build()
            except (KeyError, ValueError):
                continue
            if strat.kind is not Kind.SINGLE:
                continue
            out = strat.run(data)
            weights = apply_sizing(out.signals, data, sizing, strat.kind)
            res = run_backtest(data, weights, cfg, self.start_index)
            self.advisors.append({"strategy": strat, "output": out, "result": res})
        bh = run_backtest(
            data,
            buy_and_hold_weights(self.bars.index, [self.symbol], self.start_index),
            cfg,
            self.start_index,
        )
        self.benchmark = bh

    def consensus(self, t: int | None = None) -> float | None:
        t = self.cursor if t is None else t
        votes = []
        for a in self.advisors:
            ex = a["strategy"].explain(a["output"], self.symbol, t)
            if ex.state not in ("warming_up",):
                votes.append(np.sign(ex.signal))
        return float(np.mean(votes)) if votes else None

    # -- actions -------------------------------------------------------------------------
    def step(self, n: int = 1) -> list[dict[str, Any]]:
        with self.lock:
            if self.finished:
                raise SimError("session is finished")
            fills: list[dict[str, Any]] = []
            for _ in range(max(1, min(int(n), 500))):
                if self.cursor >= self.end_index:
                    break
                self.cursor += 1
                row = self.bars.iloc[self.cursor]
                bar = Bar(float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
                fills += self.broker.process_bar(
                    self.cursor, self.bars.index[self.cursor], {self.symbol: bar}
                )
                eq = self.broker.equity()
                self.equity.append(eq)
                self.exposure.append(
                    abs(self.broker.position(self.symbol)) * bar.close / eq if eq > 0 else 0.0
                )
                if eq <= 0:
                    break
            if self.cursor >= self.end_index or self.equity[-1] <= 0:
                self.finish()
            return fills

    def place_order(
        self,
        *,
        side: str,
        qty: float,
        type: str = "market",
        limit_price: float | None = None,
        stop_price: float | None = None,
        tif: str = "gtc",
        stop_loss: float | None = None,
        take_profit: float | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        with self.lock:
            if self.finished:
                raise SimError("session is finished")
            try:
                order = self.broker.submit(
                    self.symbol,
                    side,
                    qty,
                    index=self.cursor,
                    time=self.bars.index[self.cursor],
                    type=type,
                    limit_price=limit_price,
                    stop_price=stop_price,
                    tif=tif,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    note=note.strip()[:500],
                )
            except OrderError as exc:
                raise SimError(str(exc)) from exc
            self.decisions.append(
                {
                    "index": self.cursor,
                    "order_id": order.id,
                    "side": order.side.value,
                    "qty": order.qty,
                    "type": order.type.value,
                    "tag": order.tag,
                    "consensus": self.consensus(),
                    "has_stop": stop_loss is not None,
                    "note": bool(note.strip()),
                    "equity": self.broker.equity(),
                    "price": float(self.bars["close"].iloc[self.cursor]),
                }
            )
            return order.to_dict()

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        with self.lock:
            try:
                return self.broker.cancel(order_id).to_dict()
            except OrderError as exc:
                raise SimError(str(exc)) from exc

    def close_position(self, note: str = "") -> dict[str, Any]:
        pos = self.broker.position(self.symbol)
        if abs(pos) <= EPS:
            raise SimError("no open position")
        for o in self.broker.open_orders():  # avoid double exits
            if o.tag in ("stop_loss", "take_profit", "exit"):
                o.status, o.reason = Status.CANCELED, "replaced by close-position order"
        side = Side.SELL.value if pos > 0 else Side.BUY.value
        return self.place_order(side=side, qty=abs(pos), note=note or "close position")

    def add_note(self, text: str) -> dict[str, Any]:
        entry = {
            "index": self.cursor,
            "time": int(self.bars.index[self.cursor].timestamp()),
            "text": text.strip()[:1000],
        }
        self.journal.append(entry)
        return entry

    def finish(self) -> dict[str, Any]:
        with self.lock:
            if not self.finished:
                from trades.sim.scorecard import build_scorecard  # noqa: PLC0415 - avoid import cycle

                self.finished = True
                self.scorecard = build_scorecard(self)
            assert self.scorecard is not None
            return self.scorecard

    # -- serialisation ------------------------------------------------------------------
    def reveal_info(self) -> dict[str, Any] | None:
        return self._reveal if self.finished else None

    def state(self, since: int | None = None) -> dict[str, Any]:
        with self.lock:
            # Only the past is visible while trading; once finished, the rest of the session is revealed.
            last = self.end_index if self.finished else self.cursor
            first = 0 if since is None else max(0, min(int(since) + 1, last + 1))
            visible = self.bars.iloc[first : last + 1]
            idx = self.bars.index[self.start_index : self.cursor + 1]
            times = [int(t.timestamp()) for t in idx]
            price = float(self.bars["close"].iloc[self.cursor])
            led = self.broker.ledger
            pos = led.positions.get(self.symbol)
            qty = pos.qty if pos else 0.0
            show_advisors = self.config.reveal == "live" or (self.finished and self.config.reveal != "off")
            advisors = []
            if show_advisors:
                for a in self.advisors:
                    strat = a["strategy"]
                    ex = strat.explain(a["output"], self.symbol, self.cursor)
                    eq = a["result"].equity.iloc[self.start_index : self.cursor + 1]
                    advisors.append(
                        {
                            "id": strat.id,
                            "name": strat.name,
                            "category": strat.category,
                            "explanation": ex.to_dict(),
                            "equity": [round(float(v), 2) for v in eq],
                            "return": float(eq.iloc[-1] / eq.iloc[0] - 1.0),
                        }
                    )
            bench = self.benchmark.equity.iloc[self.start_index : self.cursor + 1]
            return {
                "id": self.id,
                "title": self.title,
                "description": self.description,
                "symbol": self.symbol,
                "config": asdict(self.config),
                "cursor": self.cursor,
                "start_index": self.start_index,
                "end_index": self.end_index,
                "progress": {
                    "done": self.cursor - self.start_index,
                    "total": self.end_index - self.start_index,
                },
                "finished": self.finished,
                "bars_from": first,
                "bars": bars_payload(visible),
                "time": int(self.bars.index[self.cursor].timestamp()),
                "account": {
                    "initial": self.config.initial_cash,
                    "cash": led.cash,
                    "equity": self.equity[-1],
                    "return_pct": self.equity[-1] / self.config.initial_cash - 1.0,
                    "buying_power": self.broker.buying_power(),
                    "realized_pnl": sum(t.pnl for t in led.closed_trades),
                    "costs": led.total_commission + led.total_slippage,
                    "position": {
                        "qty": qty,
                        "avg_price": pos.avg_price if pos and abs(qty) > EPS else None,
                        "market_value": qty * price,
                        "unrealized_pnl": led.unrealized_pnl(self.symbol, price),
                        "side": pos.side if pos else "flat",
                    },
                },
                "equity_curve": {"t": times, "v": [round(v, 2) for v in self.equity]},
                "benchmark_curve": {"t": times, "v": [round(float(v), 2) for v in bench]},
                "orders": [o.to_dict() for o in self.broker.orders.values()][-60:],
                "trades": [
                    trade_payload(t, led.unrealized_pnl(t.symbol, price) if t.is_open else None)
                    for t in led.all_trades()
                ],
                "fills": [
                    {
                        "order_id": f.order_id,
                        "time": int(pd.Timestamp(f.time).timestamp()),
                        "qty": f.qty,
                        "price": f.price,
                        "tag": f.tag,
                    }
                    for f in led.fills
                ],
                "advisors": advisors,
                "consensus": self.consensus() if show_advisors else None,
                "journal": self.journal,
                "scorecard": self.scorecard,
                "reveal": self.reveal_info(),
            }

    def summary(self) -> dict[str, Any]:
        sc = self.scorecard or {}
        you = sc.get("you", {})
        return {
            "id": self.id,
            "finished_at": time.time(),
            "title": self.title,
            "symbol": (self._reveal or {}).get("symbol", self.symbol),
            "bars": self.cursor - self.start_index,
            "return": you.get("total_return"),
            "benchmark_return": sc.get("benchmark", {}).get("total_return"),
            "max_drawdown": you.get("max_drawdown"),
            "trades": you.get("n_trades"),
            "process_score": sc.get("process_score"),
            "rank": sc.get("rank"),
        }


# ------------------------------------------------------------------------------------------
# Session factory and store
# ------------------------------------------------------------------------------------------


def _relabel_dates(n: int) -> pd.DatetimeIndex:
    days = trading_days(SCENARIO_BASE_DATE, SCENARIO_BASE_DATE + timedelta(days=int(n * 1.5) + 30))[:n]
    return pd.DatetimeIndex(pd.to_datetime(days)).tz_localize("UTC")


def create_session(config: SimConfig, data_service=None) -> SimSession:
    rng = np.random.default_rng(config.seed)
    seed = int(config.seed if config.seed is not None else rng.integers(1, 2**31 - 1))
    total = config.warmup_bars + config.length_bars
    if config.source == "scenario":
        sc = SCENARIOS[config.scenario]
        n = max(total, sc.bars)
        bars = scenario_bars(config.scenario, seed, n)
        segments = scenario_regimes(config.scenario, seed, n)
        start_bar = config.warmup_bars - 1
        visible = [
            {**s, "start": s["start"] - start_bar, "end": s["end"] - start_bar}
            for s in segments
            if s["end"] >= start_bar
        ]
        length = config.length_bars
        reveal = {
            "kind": "scenario",
            "scenario": sc.id,
            "label": sc.label,
            "seed": seed,
            "regimes": [
                {**s, "start": max(s["start"], 0), "end": min(s["end"], length)}
                for s in visible
                if s["start"] <= length
            ],
        }
        config.seed = seed
        return SimSession(
            config, bars.iloc[:total], symbol="SIM", reveal=reveal, title=sc.label, description=sc.description
        )

    # Historical replay
    if data_service is None:
        raise SimError("historical sessions need a data service")
    preset = (
        next((p for p in HISTORICAL_PRESETS if p["id"] == config.preset), None) if config.preset else None
    )
    symbol = (preset["symbol"] if preset else config.symbol).strip().upper()
    start = preset["start"] if preset else config.start
    provider = config.provider or data_service.settings.get().provider
    if provider == "synthetic":
        provider = "yahoo"
    if not symbol:
        symbol = str(rng.choice(RANDOM_HISTORY_UNIVERSE))
    if not start:
        # random start between 2005 and ~2 years ago
        lo, hi = (
            date(2005, 1, 3).toordinal(),
            (date.today() - timedelta(days=int(config.length_bars * 1.5))).toordinal(),
        )
        start = date.fromordinal(int(rng.integers(lo, max(lo + 1, hi)))).isoformat()
    start_d = pd.Timestamp(start).date()
    fetch_from = start_d - timedelta(days=int(config.warmup_bars * 1.5) + 20)
    fetch_to = start_d + timedelta(days=int(config.length_bars * 1.5) + 20)
    bars = data_service.bars(symbol, "1d", fetch_from, fetch_to, provider)
    first = int(np.searchsorted(bars.index.date, start_d))
    if first < 50:
        raise SimError(f"not enough history before {start_d} for {symbol} (need a warm-up period)")
    lo = max(0, first - config.warmup_bars)
    bars = bars.iloc[lo : first + config.length_bars]
    warm = first - lo
    cfg = SimConfig.from_dict({**asdict(config), "warmup_bars": max(warm, 50), "seed": seed})
    reveal = {
        "kind": "history",
        "symbol": symbol,
        "provider": provider,
        "blind": config.blind,
        "first_date": bars.index[warm - 1].date().isoformat(),
        "last_date": bars.index[-1].date().isoformat(),
        "preset": preset["label"] if preset else None,
    }
    title = (
        preset["label"] if (preset and not config.blind) else ("Mystery chart" if config.blind else symbol)
    )
    desc = (
        preset["description"]
        if (preset and not config.blind)
        else (
            "A real stock or ETF from a hidden period. Ticker, dates and price level are revealed at the end."
            if config.blind
            else f"Replay of {symbol} from {start_d}."
        )
    )
    display = symbol
    if config.blind:
        scale = 100.0 / float(bars["close"].iloc[warm - 1])
        bars = bars.copy()
        for col in ("open", "high", "low", "close"):
            bars[col] = bars[col] * scale
        bars.index = _relabel_dates(len(bars))
        reveal["scale"] = scale
        display = "MYSTERY"
    return SimSession(cfg, bars, symbol=display, reveal=reveal, title=title, description=desc)


class SessionStore:
    def __init__(self, max_sessions: int = 40, history_path: Path | None = None):
        self._sessions: OrderedDict[str, SimSession] = OrderedDict()
        self._lock = threading.Lock()
        self.max_sessions = max_sessions
        self.history_path = history_path

    def add(self, session: SimSession) -> SimSession:
        with self._lock:
            self._sessions[session.id] = session
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
        return session

    def get(self, session_id: str) -> SimSession:
        with self._lock:
            s = self._sessions.get(session_id)
        if s is None:
            raise KeyError(session_id)
        return s

    def record(self, session: SimSession) -> None:
        if self.history_path is None:
            return
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            with self.history_path.open("a") as fh:
                fh.write(json.dumps(session.summary(), default=str) + "\n")
        except OSError:
            pass

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        if self.history_path is None or not self.history_path.exists():
            return []
        rows = []
        for line in self.history_path.read_text().splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[::-1]
