"""The risk engine: deterministic checks on every bar's targets, the same in backtests and paper.

A strategy says what it wants to hold; the risk engine decides what it may hold. At each bar's
close, before any order is placed, ``RiskEngine.review`` applies ``risk/limits.yaml`` in this order:

1. **Kill switch.** While the HALT file exists, the targets are what the account already holds:
   nothing is bought or sold. (Closing positions is a separate, deliberate act: ``trades paper
   flatten``.)
2. **Loss stops.** If equity has fallen more than ``daily_loss_limit`` since the session opened
   (the previous session's closing equity), flatten and accept no new trades until the next
   session; likewise ``weekly_loss_limit`` since the week opened, until next week.
3. **Symbols.** Blocked symbols, or symbols outside a non-empty allow-list, are not traded.
4. **Shorting.** Without ``allow_short``, short targets become flat.
5. **Position size.** No position larger than ``max_position_pct`` of equity.
6. **Risk per trade.** A new trade with a protective stop is shrunk so that being stopped out
   loses at most ``max_risk_per_trade`` of equity, and the size approved then is remembered: the
   trade may not grow later while it lasts. Trades without a stop are bounded by step 5.
7. **Open positions.** At most ``max_open_positions``: positions already held keep their place,
   new ones are admitted largest first (ties by symbol).
8. **Orders per day.** After ``max_orders_per_day`` fills in a session, only exits are allowed.

Gross exposure (``max_gross_exposure``) is enforced by the broker at fill time, so a stop entry
that never fills does not use up room. Every intervention is recorded as a ``RiskEvent``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from trades.data.calendar import NY
from trades.risk.limits import RiskLimits


@dataclass(frozen=True)
class RiskEvent:
    time: Any
    kind: str  # halt | daily_loss | weekly_loss | symbol | short | position_size | trade_risk | positions | orders
    message: str
    symbol: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["time"] = int(pd.Timestamp(self.time).timestamp()) if self.time is not None else None
        return d


def session_of(time: Any) -> date:
    """The trading date of a bar: daily bars are stamped 00:00 UTC of their date, intraday bars
    with their start time (so they are converted to New York time)."""
    ts = pd.Timestamp(time)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.date() if (ts.hour, ts.minute, ts.second) == (0, 0, 0) else ts.tz_convert(NY).date()


class RiskEngine:
    """Applies ``RiskLimits`` to target weights, keeping the state the limits need between bars."""

    def __init__(self, limits: RiskLimits, symbols: list[str], halt_check: Callable[[], bool] | None = None):
        self.limits = limits
        self.symbols = list(symbols)
        self.halt_check = halt_check
        self.events: list[RiskEvent] = []
        self.session: date | None = None
        self.week: tuple[int, int] | None = None
        self.session_open_equity = math.nan
        self.week_open_equity = math.nan
        self.blocked_until: str | None = None  # "session" | "week": no new trades until it rolls over
        self.fills_today = 0
        self._last_equity = math.nan
        self._last_event: dict[tuple[str, str | None], date] = {}
        self.approved: dict[int, float] = {}  # symbol -> the weight a stop-based trade was approved at

    # -- bookkeeping -------------------------------------------------------------------
    def _event(self, time: Any, kind: str, message: str, symbol: str | None = None, once_per_session: bool = False) -> None:
        if once_per_session:
            key = (kind, symbol)
            if self._last_event.get(key) == self.session:
                return
            self._last_event[key] = self.session  # type: ignore[assignment]
        self.events.append(RiskEvent(time, kind, message, symbol))

    def _roll(self, time: Any, equity: float) -> None:
        """Start a new session or week: its opening equity is the last close of the previous one."""
        day = session_of(time)
        opening = self._last_equity if math.isfinite(self._last_equity) else equity
        if day != self.session:
            self.session = day
            self.session_open_equity = opening
            self.fills_today = 0
            if self.blocked_until == "session":
                self.blocked_until = None
            week = day.isocalendar()[:2]
            if week != self.week:
                self.week = week
                self.week_open_equity = opening
                if self.blocked_until == "week":
                    self.blocked_until = None

    def note_fills(self, n: int) -> None:
        self.fills_today += n

    # -- persistence (the paper runners stop and start; the limits must not forget) ----------
    def state(self) -> dict[str, Any]:
        """What a paper runner saves between runs; ``restore`` reads it back."""
        return {
            "session": self.session.isoformat() if self.session else None,
            "week": list(self.week) if self.week else None,
            "week_open_equity": self.week_open_equity if math.isfinite(self.week_open_equity) else None,
            "blocked_until": self.blocked_until,
            "fills_today": self.fills_today,
            "approved": {self.symbols[j]: float(w) for j, w in self.approved.items()},
        }

    def restore(self, saved: dict[str, Any], opening_equity: float) -> None:
        """Pick up where a saved run stopped. ``opening_equity``: the account's equity at the last
        close (Alpaca's ``last_equity``), which opens the current session."""
        self._last_equity = float(opening_equity)
        if saved.get("week"):
            self.week = tuple(saved["week"])  # type: ignore[assignment]
            self.week_open_equity = float(saved.get("week_open_equity") or opening_equity)
        if saved.get("session"):
            self.session = date.fromisoformat(saved["session"])
            self.session_open_equity = float(opening_equity)
            self.fills_today = int(saved.get("fills_today") or 0)
        self.blocked_until = saved.get("blocked_until")
        self.approved = {self.symbols.index(s): float(w) for s, w in (saved.get("approved") or {}).items() if s in self.symbols}

    # -- the checks -------------------------------------------------------------------
    def review(
        self,
        time: Any,
        equity: float,
        targets,
        current,
        prices,
        stop_losses=None,
        entry_stops=None,
    ) -> np.ndarray:
        """Targets the account may hold after this bar, given the strategy's ``targets``, the
        ``current`` weights, the latest ``prices`` and the strategy's stop and entry levels."""
        lim = self.limits
        W = np.nan_to_num(np.asarray(targets, dtype=float)).copy()
        held = np.nan_to_num(np.asarray(current, dtype=float))
        px = np.asarray(prices, dtype=float)
        self._roll(time, equity)
        self._last_equity = equity

        # 1. Kill switch: hold what is held, trade nothing.
        if self.halt_check is not None and self.halt_check():
            self._event(time, "halt", "The kill switch (HALT file) is on: nothing is traded.", once_per_session=True)
            return held.copy()
        # 2. Loss stops.
        if equity <= 0:
            return self._flat(W)
        day_loss = equity / self.session_open_equity - 1.0 if self.session_open_equity > 0 else 0.0
        week_loss = equity / self.week_open_equity - 1.0 if self.week_open_equity > 0 else 0.0
        if self.blocked_until != "week" and week_loss < -lim.weekly_loss_limit:
            self.blocked_until = "week"
            self._event(time, "weekly_loss", f"Down {-week_loss:.2%} this week, past the {lim.weekly_loss_limit:.1%} limit: flattened until next week.")
            return self._flat(W)
        if self.blocked_until is None and day_loss < -lim.daily_loss_limit:
            self.blocked_until = "session"
            self._event(time, "daily_loss", f"Down {-day_loss:.2%} today, past the {lim.daily_loss_limit:.1%} limit: flattened until the next session.")
            return self._flat(W)
        if self.blocked_until is not None:
            return self._flat(W)

        for j, sym in enumerate(self.symbols):
            approved = self.approved.get(j)
            if approved is not None and (abs(W[j]) <= 1e-12 or np.sign(W[j]) != np.sign(approved)):
                del self.approved[j]  # that trade is over (flat or reversed)
                approved = None
            if abs(W[j]) <= 1e-12:
                continue
            # 3. Symbols.
            if not lim.symbol_allowed(sym):
                self._event(time, "symbol", f"{sym} is not allowed by risk/limits.yaml.", sym, once_per_session=True)
                W[j] = 0.0
                continue
            # 4. Shorting.
            if W[j] < 0 and not lim.allow_short:
                self._event(time, "short", f"Short {sym} refused: shorting is off in risk/limits.yaml.", sym, once_per_session=True)
                W[j] = 0.0
                continue
            # 5. Position size.
            if abs(W[j]) > lim.max_position_pct + 1e-12:
                self._event(time, "position_size", f"{sym} cut from {abs(W[j]):.0%} to {lim.max_position_pct:.0%} of equity.", sym, once_per_session=True)
                W[j] = math.copysign(lim.max_position_pct, W[j])
            # 6. Risk per trade: new trades with a protective stop; an approved trade may not grow.
            stop = float(stop_losses[j]) if stop_losses is not None else math.nan
            if approved is not None:
                W[j] = math.copysign(min(abs(W[j]), abs(approved)), W[j])
            elif math.isfinite(stop) and abs(held[j]) <= 1e-9:
                ref = float(entry_stops[j]) if entry_stops is not None and math.isfinite(float(entry_stops[j])) else float(px[j])
                if math.isfinite(ref) and ref > 0 and abs(ref - stop) > 0:
                    risk = abs(W[j]) * abs(ref - stop) / ref
                    if risk > lim.max_risk_per_trade + 1e-12:
                        W[j] = math.copysign(lim.max_risk_per_trade * ref / abs(ref - stop), W[j])
                        self._event(time, "trade_risk", f"{sym} trade shrunk to risk {lim.max_risk_per_trade:.2%} of equity at its stop.", sym, once_per_session=True)
                    self.approved[j] = W[j]

        # 7. Open positions: positions already held first, then new ones largest first.
        wanted = [j for j in range(len(W)) if abs(W[j]) > 1e-12]
        if len(wanted) > lim.max_open_positions:
            order = sorted(wanted, key=lambda j: (0 if abs(held[j]) > 1e-9 else 1, -abs(W[j]), self.symbols[j]))
            for j in order[lim.max_open_positions :]:
                W[j] = 0.0
            self._event(time, "positions", f"{len(wanted)} positions wanted; the limit is {lim.max_open_positions}.", once_per_session=True)
        # 8. Orders per day: only exits once the day's fills reach the limit.
        if self.fills_today >= lim.max_orders_per_day:
            adds = np.abs(W) > np.abs(held) + 1e-9
            if adds.any():
                W = np.where(adds, held, W)
                self._event(time, "orders", f"{self.fills_today} fills today reach the limit of {lim.max_orders_per_day}: exits only.", once_per_session=True)
        return W

    def _flat(self, W: np.ndarray) -> np.ndarray:
        self.approved.clear()
        return np.zeros_like(W)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.events:
            counts[e.kind] = counts.get(e.kind, 0) + 1
        return counts
