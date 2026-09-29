"""Strategy agents: each strategy trades its own paper account as bars arrive.

An agent decides at the close of each completed bar, using only bars up to that close,
and its orders fill at the next bar's open through the same ``ExecutionEngine`` the
backtester uses. A forward test therefore trades exactly like a backtest of the same
bars would (the test suite checks this), just one bar at a time.

Signals are causal, so an agent may compute them over bars that exist but are not yet
revealed (the simulated market generates a little ahead of the visible cursor): the
value at bar ``t`` never depends on later bars. That keeps fast simulations cheap.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from trades.backtest.engine import BacktestConfig, ExecutionEngine
from trades.backtest.metrics import performance_metrics
from trades.backtest.runner import StrategySpec, trade_payload
from trades.core.ledger import EPS
from trades.strategies.base import Kind, StrategyOutput
from trades.strategies.sizing import apply_sizing

MAX_EVENTS = 400


class StrategyAgent:
    def __init__(
        self,
        agent_id: str,
        spec: StrategySpec,
        symbols: list[str],
        config: BacktestConfig,
        *,
        benchmark: bool = False,
    ):
        self.id = agent_id
        self.spec = spec
        self.strategy, self.sizing = spec.build()
        self.strategy.check_symbols(symbols)
        self.symbols = list(symbols)
        self.benchmark = benchmark
        self.name = "Buy & hold" if benchmark else self.strategy.name
        # A buy-and-hold investor uses no margin; strategies may lever up to their sizing cap.
        self.engine = ExecutionEngine(
            self.symbols, config.capped(1.0 if benchmark else self.sizing.max_leverage)
        )
        self.ppy = config.periods_per_year
        self._out: StrategyOutput | None = None
        self._weights: np.ndarray | None = None
        self._states: np.ndarray | None = None  # long | short | flat | hedge per bar and symbol
        self._valid: np.ndarray | None = None
        self.prepared_until = -1
        self.start_index: int | None = None
        self.t = -1  # last bar processed
        self.times: list[int] = []
        self.equity: list[float] = []
        self.gross: list[float] = []
        self.events: list[dict[str, Any]] = []
        self.explanations: dict[str, dict[str, Any]] = {}
        self._state: dict[str, str] = {}
        self._reasons: dict[str, str] = {}
        self.error: str | None = None

    # -- signals ---------------------------------------------------------------------
    def prepare(self, data: dict[str, pd.DataFrame]) -> None:
        """(Re)compute signals and target weights over all bars in ``data``."""
        sub = {s: data[s] for s in self.symbols}
        out = self.strategy.run(sub)
        weights = apply_sizing(out.signals, sub, self.sizing, self.strategy.kind, self.ppy)
        self._out = out
        self._weights = weights[self.symbols].to_numpy(float)
        self._states = np.column_stack([self.strategy.position_states(out, s) for s in self.symbols])
        self._valid = np.column_stack(
            [
                out.diagnostics[s]["valid"].to_numpy(bool)
                if s in out.diagnostics and "valid" in out.diagnostics[s]
                else np.ones(len(weights), dtype=bool)
                for s in self.symbols
            ]
        )
        self.prepared_until = len(weights) - 1

    def invalidate(self, t: int) -> None:
        """Bars from ``t`` on changed (an injected event): recompute before using them."""
        self.prepared_until = min(self.prepared_until, t - 1)

    def explain(self, symbol: str, t: int) -> dict[str, Any]:
        assert self._out is not None
        return self.strategy.explain(self._out, symbol, t).to_dict()

    # -- trading -----------------------------------------------------------------------
    def begin(self, t: int, time: pd.Timestamp, o, h, l, c) -> list[dict[str, Any]]:
        """Start at the close of bar ``t`` (the end of the warm-up): take initial positions.

        Like the first bar of a backtest: nothing is pending, so this only marks the flat
        account to bar ``t``'s close, then decides the orders for bar ``t+1``.
        """
        self.start_index = self.t = t
        self.engine.execute(t, time, o, h, l, c)
        self.times.append(int(time.timestamp()))
        self.equity.append(self.engine.equity)
        self.gross.append(0.0)
        return self._decide(t, time, first=True)

    def on_bar(self, t: int, time: pd.Timestamp, o, h, l, c) -> list[dict[str, Any]]:
        """Bar ``t`` completed: fill yesterday's orders, mark to market, decide for tomorrow."""
        events = []
        for f in self.engine.execute(t, time, o, h, l, c):
            side = "buy" if f.qty > 0 else "sell"
            events.append(
                {
                    "t": t,
                    "time": int(time.timestamp()),
                    "agent": self.id,
                    "type": "fill",
                    "symbol": f.symbol,
                    "side": side,
                    "qty": abs(f.qty),
                    "price": f.price,
                    "cost": f.commission + f.slippage,
                    "position": self.engine.ledger.qty(f.symbol),
                    "reason": self._reasons.pop(f.symbol, ""),
                }
            )
        # An order that expired without a fill (e.g. trimmed to nothing by the leverage cap) must not
        # lend its reason to a later, unrelated trade.
        waiting = {self.symbols[j] for j in self.engine.pending}
        self._reasons = {s: r for s, r in self._reasons.items() if s in waiting}
        self.t = t
        self.times.append(int(time.timestamp()))
        self.equity.append(self.engine.equity)
        self.gross.append(sum(abs(w) for w in self.engine.weights().values()))
        events += self._decide(t, time)
        self._log(events)
        return events

    def _decide(self, t: int, time: pd.Timestamp, first: bool = False) -> list[dict[str, Any]]:
        assert self._weights is not None and self._states is not None and self._valid is not None
        if self.engine.stopped:
            return []
        events = []
        changed: set[str] = set()
        for j, sym in enumerate(self.symbols):
            state = str(self._states[t, j]) if self._valid[t, j] else "warming_up"
            if state != self._state.get(sym):
                ex = self.explain(sym, t)
                self.explanations[sym] = ex
                if not first and not self.benchmark:
                    events.append(
                        {
                            "t": t,
                            "time": int(time.timestamp()),
                            "agent": self.id,
                            "type": "signal",
                            "symbol": sym,
                            "from": self._state.get(sym),
                            "to": state,
                            "headline": ex["headline"],
                        }
                    )
                self._state[sym] = state
                changed.add(sym)
        orders = self.engine.decide(t, self._weights[t])
        for sym, qty in orders.items():
            if abs(qty) <= EPS or sym in self._reasons:
                continue
            if self.benchmark:
                reason = "Buy and hold: invest equally at the start and never trade again."
            elif sym in changed:
                reason = self.explanations[sym]["headline"]
            else:
                j = self.symbols.index(sym)
                reason = (
                    f"Re-sizing to a {self._weights[t, j]:+.0%} target weight "
                    f"({self.sizing.method.replace('_', ' ')} sizing)."
                )
            self._reasons[sym] = reason
        if first:
            self._log(events)
        return events

    def _log(self, events: list[dict[str, Any]]) -> None:
        self.events.extend(events)
        if len(self.events) > MAX_EVENTS:
            del self.events[: len(self.events) - MAX_EVENTS]

    # -- reporting -----------------------------------------------------------------------
    def snapshot(self, prices: dict[str, float]) -> dict[str, Any]:
        """Compact live state for every bar's update."""
        led = self.engine.ledger
        eq = self.engine.equity
        start = self.engine.cfg.initial_cash
        peak = max(self.equity) if self.equity else start
        return {
            "id": self.id,
            "equity": eq,
            "return": eq / start - 1.0,
            "drawdown": eq / peak - 1.0 if peak > 0 else 0.0,
            "cash": led.cash,
            "gross": self.gross[-1] if self.gross else 0.0,
            "positions": {s: led.qty(s) for s in self.symbols if abs(led.qty(s)) > EPS},
            "weights": {s: w for s, w in self.engine.weights().items() if abs(w) > 1e-9},
            "signals": dict(self._state),
            "pending": {s: q for s, q in self.engine.orders().items() if abs(q) > EPS},
            "trades": len(led.closed_trades),
            "unrealized": sum(led.unrealized_pnl(s, p) for s, p in prices.items() if s in self.symbols),
            "stopped": self.engine.stopped,
        }

    def describe(self) -> dict[str, Any]:
        """Static information about the agent."""
        return {
            "id": self.id,
            "name": self.name,
            "strategy_id": self.strategy.id,
            "kind": "benchmark" if self.benchmark else "strategy",
            "category": self.strategy.category,
            "evidence": self.strategy.evidence.value,
            "symbols": self.symbols,
            "params": self.strategy.params,
            "sizing": self.sizing.to_dict(),
            "pair": self.strategy.kind is Kind.PAIR,
            "error": self.error,
        }

    def detail(self, prices: dict[str, float]) -> dict[str, Any]:
        led = self.engine.ledger
        trades = [
            trade_payload(
                t, led.unrealized_pnl(t.symbol, prices.get(t.symbol, math.nan)) if t.is_open else None
            )
            for t in led.all_trades()
        ][-100:]
        # Re-explain at the latest bar: stored explanations date from the last signal change.
        current = (
            {s: self.explain(s, self.t) for s in self.symbols}
            if self._out is not None and self.t >= 0
            else {}
        )
        return {
            **self.describe(),
            **self.snapshot(prices),
            "explanations": current,
            "trades_list": trades,
            "events": self.events[-150:],
            "financing": led.total_financing,
            "costs": led.total_commission + led.total_slippage,
        }

    def metrics(self, benchmark: pd.Series | None = None) -> dict[str, float | None]:
        if len(self.equity) < 3:
            return {}
        idx = pd.to_datetime(self.times, unit="s", utc=True)
        eq = pd.Series(self.equity, index=idx)
        led = self.engine.ledger
        return performance_metrics(
            eq, self.ppy, led.all_trades(), led.fills, pd.Series(self.gross, index=idx), benchmark
        )

    def equity_series(self) -> pd.Series:
        return pd.Series(self.equity, index=pd.to_datetime(self.times, unit="s", utc=True))
