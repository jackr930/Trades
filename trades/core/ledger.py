"""Cash/position accounting and round-trip trade tracking.

Shared by the backtester and the paper broker so that a strategy's backtest and a
simulator session account for fills, costs and P&L in exactly the same way.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

EPS = 1e-9


@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0

    @property
    def side(self) -> str:
        return "long" if self.qty > EPS else "short" if self.qty < -EPS else "flat"


@dataclass
class Fill:
    time: Any
    index: int
    symbol: str
    qty: float  # signed: + buy, - sell
    price: float
    commission: float
    slippage: float  # dollar cost of slippage vs. the reference price
    order_id: str | None = None
    tag: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trade:
    """A round trip: from flat to flat (a reversal closes one trade and opens another)."""

    id: int
    symbol: str
    direction: str  # "long" | "short"
    entry_time: Any
    entry_index: int
    entry_price: float
    qty: float = 0.0  # largest absolute size held during the trade
    exit_time: Any = None
    exit_index: int | None = None
    exit_price: float | None = None
    realized_pnl: float = 0.0  # price P&L, before commissions
    commission: float = 0.0
    slippage: float = 0.0
    max_favorable: float = 0.0  # MFE as a fraction of entry price (>= 0)
    max_adverse: float = 0.0  # MAE as a fraction of entry price (<= 0)
    entry_notional: float = 0.0
    _exit_qty: float = 0.0
    _exit_value: float = 0.0
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.exit_index is None

    @property
    def pnl(self) -> float:
        return self.realized_pnl - self.commission

    @property
    def return_pct(self) -> float:
        return self.pnl / self.entry_notional if self.entry_notional > 0 else 0.0

    @property
    def bars_held(self) -> int | None:
        return None if self.exit_index is None else self.exit_index - self.entry_index

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "exit_time": self.exit_time,
            "exit_price": self.exit_price,
            "qty": self.qty,
            "pnl": self.pnl,
            "return_pct": self.return_pct,
            "commission": self.commission,
            "slippage": self.slippage,
            "bars_held": self.bars_held,
            "mfe": self.max_favorable,
            "mae": self.max_adverse,
            "is_open": self.is_open,
            "tags": list(self.tags),
            "notes": list(self.notes),
        }


class Ledger:
    """Tracks cash, positions, fills and round-trip trades."""

    def __init__(self, cash: float):
        self.initial_cash = float(cash)
        self.cash = float(cash)
        self.positions: dict[str, Position] = {}
        self.fills: list[Fill] = []
        self.open_trades: dict[str, Trade] = {}
        self.closed_trades: list[Trade] = []
        self.total_commission = 0.0
        self.total_slippage = 0.0
        self.total_financing = 0.0
        self._next_trade_id = 1

    # -- queries -------------------------------------------------------------------
    def qty(self, symbol: str) -> float:
        pos = self.positions.get(symbol)
        return pos.qty if pos else 0.0

    def market_value(self, prices: dict[str, float]) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            if abs(pos.qty) > EPS:
                px = prices.get(sym)
                if px is None or not math.isfinite(px):
                    px = pos.avg_price
                total += pos.qty * px
        return total

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def gross_exposure(self, prices: dict[str, float]) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym, pos.avg_price)
            total += abs(pos.qty * px)
        return total

    def unrealized_pnl(self, symbol: str, price: float) -> float:
        pos = self.positions.get(symbol)
        if not pos or abs(pos.qty) <= EPS:
            return 0.0
        return pos.qty * (price - pos.avg_price)

    def all_trades(self) -> list[Trade]:
        return sorted([*self.closed_trades, *self.open_trades.values()], key=lambda t: t.id)

    # -- mutations -----------------------------------------------------------------
    def charge(self, amount: float) -> None:
        """Financing charges/credits (borrow fees, interest). Positive = cost."""
        self.cash -= amount
        self.total_financing += amount

    def apply_fill(
        self,
        *,
        time: Any,
        index: int,
        symbol: str,
        qty: float,
        price: float,
        commission: float = 0.0,
        slippage: float = 0.0,
        order_id: str | None = None,
        tag: str = "",
    ) -> Fill:
        if abs(qty) <= EPS:
            raise ValueError("fill quantity must be non-zero")
        fill = Fill(time, index, symbol, qty, price, commission, slippage, order_id, tag)
        self.fills.append(fill)
        self.cash -= qty * price + commission
        self.total_commission += commission
        self.total_slippage += slippage

        pos = self.positions.setdefault(symbol, Position(symbol))
        old_qty = pos.qty
        new_qty = old_qty + qty
        if abs(new_qty) <= EPS:
            new_qty = 0.0

        trade = self.open_trades.get(symbol)
        same_direction = abs(old_qty) <= EPS or (old_qty > 0) == (qty > 0)
        if same_direction:
            # Opening or adding.
            if trade is None:
                trade = self._open_trade(symbol, "long" if qty > 0 else "short", time, index, price)
            total = abs(old_qty) + abs(qty)
            pos.avg_price = (abs(old_qty) * pos.avg_price + abs(qty) * price) / total
            pos.qty = new_qty
            trade.entry_notional += abs(qty) * price
            trade.entry_price = pos.avg_price
            trade.qty = max(trade.qty, abs(new_qty))
            trade.commission += commission
            trade.slippage += slippage
            return fill

        # Reducing, closing or reversing.
        closing = min(abs(qty), abs(old_qty))
        direction = 1.0 if old_qty > 0 else -1.0
        realized = closing * (price - pos.avg_price) * direction
        if trade is None:  # defensive: position without a trade record
            trade = self._open_trade(symbol, "long" if old_qty > 0 else "short", time, index, pos.avg_price)
            trade.entry_notional = abs(old_qty) * pos.avg_price
        close_share = closing / abs(qty)
        trade.realized_pnl += realized
        trade.commission += commission * close_share
        trade.slippage += slippage * close_share
        trade._exit_qty += closing
        trade._exit_value += closing * price

        if abs(qty) - abs(old_qty) > EPS:  # reversal
            self._close_trade(symbol, time, index)
            remainder = qty + old_qty  # signed remainder in the new direction
            pos.qty = remainder
            pos.avg_price = price
            new_trade = self._open_trade(symbol, "long" if remainder > 0 else "short", time, index, price)
            new_trade.entry_notional = abs(remainder) * price
            new_trade.qty = abs(remainder)
            new_trade.commission += commission * (1 - close_share)
            new_trade.slippage += slippage * (1 - close_share)
        else:
            pos.qty = new_qty
            if new_qty == 0.0:
                pos.avg_price = 0.0
                self._close_trade(symbol, time, index)
        return fill

    def mark_excursions(self, symbol: str, high: float, low: float) -> None:
        """Update max favourable/adverse excursion of the open trade with a bar's range."""
        trade = self.open_trades.get(symbol)
        if trade is None or trade.entry_price <= 0:
            return
        if not (math.isfinite(high) and math.isfinite(low)):
            return
        if trade.direction == "long":
            fav, adv = high / trade.entry_price - 1.0, low / trade.entry_price - 1.0
        else:
            fav, adv = 1.0 - low / trade.entry_price, 1.0 - high / trade.entry_price
        trade.max_favorable = max(trade.max_favorable, fav)
        trade.max_adverse = min(trade.max_adverse, adv)

    # -- internals -----------------------------------------------------------------
    def _open_trade(self, symbol: str, direction: str, time: Any, index: int, price: float) -> Trade:
        trade = Trade(self._next_trade_id, symbol, direction, time, index, price)
        self._next_trade_id += 1
        self.open_trades[symbol] = trade
        return trade

    def _close_trade(self, symbol: str, time: Any, index: int) -> None:
        trade = self.open_trades.pop(symbol, None)
        if trade is None:
            return
        trade.exit_time = time
        trade.exit_index = index
        trade.exit_price = trade._exit_value / trade._exit_qty if trade._exit_qty > 0 else None
        self.closed_trades.append(trade)
