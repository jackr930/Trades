"""Simulated broker for the trading simulator (no real orders, ever).

Fill model (per bar, orders become eligible on the bar *after* they are placed):

* market: fills at the bar's open, plus adverse slippage;
* limit buy: fills at the open if the bar opens below the limit, else at the limit if
  the low trades *through* it (merely touching the limit is not enough: other orders
  were queued at that price first); sells mirror this; limits get no slippage;
* stop sell: triggers at the open on a gap below the stop, else at the stop if the low
  reaches it, then suffers slippage (buy stops mirror this);
* if a bar could trigger both a stop-loss and a take-profit of the same bracket, the
  stop-loss is assumed to fill first (the conservative choice, since the intrabar path
  is unknown);
* bracket children are armed when their parent fills; a parent filled at the open can
  have its children trigger later in the same bar;
* when a parent is canceled, expires or is rejected, its waiting children are canceled.
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

import pandas as pd

from trades.core.ledger import EPS, Ledger


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Status(str, Enum):
    OPEN = "open"
    PENDING = "pending"  # bracket child waiting for its parent to fill
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class OrderError(ValueError):
    pass


@dataclass
class Order:
    id: str
    symbol: str
    side: Side
    qty: float
    type: OrderType
    limit_price: float | None
    stop_price: float | None
    tif: str  # gtc | day
    status: Status
    created_index: int
    created_time: Any
    tag: str = "entry"  # entry | exit | stop_loss | take_profit
    parent_id: str | None = None
    oco_group: str | None = None
    note: str = ""
    filled_index: int | None = None
    filled_time: Any = None
    fill_price: float | None = None
    filled_qty: float = 0.0
    reason: str = ""
    closed_index: int | None = None  # bar at which it was canceled, expired or rejected

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        d["type"] = self.type.value
        d["status"] = self.status.value
        for k in ("created_time", "filled_time"):
            if d[k] is not None:
                d[k] = int(pd.Timestamp(d[k]).timestamp())
        return d


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float


class PaperBroker:
    def __init__(
        self,
        cash: float = 100_000.0,
        *,
        commission_bps: float = 0.0,
        commission_per_share: float = 0.0,
        min_commission: float = 0.0,
        slippage_bps: float = 5.0,
        allow_short: bool = False,
        max_leverage: float = 1.0,
        fractional: bool = False,
        borrow_bps_annual: float = 25.0,
        margin_rate_annual: float = 0.02,
        periods_per_year: int = 252,
    ):
        self.ledger = Ledger(cash)
        self.commission_bps = commission_bps
        self.commission_per_share = commission_per_share
        self.min_commission = min_commission
        self.slip = slippage_bps / 1e4
        self.allow_short = allow_short
        self.max_leverage = max_leverage
        self.fractional = fractional
        # Same financing model as the backtester: shorts pay a borrow fee, borrowed cash pays interest.
        self._borrow = borrow_bps_annual / 1e4 / periods_per_year
        self._margin = margin_rate_annual / periods_per_year
        self.orders: dict[str, Order] = {}
        self.last_prices: dict[str, float] = {}
        self._ids = itertools.count(1)
        self.events: list[dict[str, Any]] = []
        # symbol -> direction ("up"/"down") the price crossed to fill an entry mid-bar this bar
        self._intrabar_entries: dict[str, str] = {}

    # -- helpers -----------------------------------------------------------------------
    def _new_id(self) -> str:
        return f"o{next(self._ids)}"

    def commission(self, qty: float, price: float) -> float:
        fee = abs(qty) * self.commission_per_share + abs(qty) * price * self.commission_bps / 1e4
        return max(fee, self.min_commission) if abs(qty) > EPS else 0.0

    def equity(self) -> float:
        return self.ledger.equity(self.last_prices)

    def buying_power(self) -> float:
        eq = self.equity()
        return max(self.max_leverage * eq - self.ledger.gross_exposure(self.last_prices), 0.0)

    def position(self, symbol: str) -> float:
        return self.ledger.qty(symbol)

    def open_orders(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status in (Status.OPEN, Status.PENDING)]

    # -- order entry -------------------------------------------------------------------
    def submit(
        self,
        symbol: str,
        side: str | Side,
        qty: float,
        *,
        index: int,
        time: Any,
        type: str | OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        tif: str = "gtc",
        stop_loss: float | None = None,
        take_profit: float | None = None,
        note: str = "",
        tag: str | None = None,
    ) -> Order:
        side = Side(side)
        otype = OrderType(type)
        if tif not in ("gtc", "day"):
            raise OrderError("time in force must be 'gtc' or 'day'")
        try:
            qty = float(qty)
        except (TypeError, ValueError) as exc:
            raise OrderError("quantity must be a number") from exc
        if not self.fractional:
            if abs(qty - round(qty)) > 1e-9:
                raise OrderError("whole shares only (fractional trading is off)")
            qty = float(round(qty))
        if qty <= 0:
            raise OrderError("quantity must be positive")
        if otype is OrderType.LIMIT and not (limit_price and limit_price > 0):
            raise OrderError("a limit order needs a positive limit price")
        if otype is OrderType.STOP and not (stop_price and stop_price > 0):
            raise OrderError("a stop order needs a positive stop price")
        ref = self.last_prices.get(symbol)
        pos = self.position(symbol)
        if side is Side.SELL and not self.allow_short and qty > max(pos, 0.0) + EPS:
            raise OrderError(
                f"Short selling is disabled: you can sell at most {max(pos, 0):g} shares of {symbol}."
            )
        increases = (side is Side.BUY and pos >= -EPS) or (side is Side.SELL and pos <= EPS)
        if increases and ref:
            est = qty * (limit_price or stop_price or ref)
            if est > self.buying_power() + 1e-6:
                raise OrderError(
                    f"Insufficient buying power: order needs about ${est:,.0f}, you have ${self.buying_power():,.0f}."
                )
        for label, price in (("stop-loss", stop_loss), ("take-profit", take_profit)):
            if price is not None and price <= 0:
                raise OrderError(f"{label} price must be positive")
        entry_ref = limit_price or stop_price or ref
        if entry_ref and side is Side.BUY:
            if stop_loss is not None and stop_loss >= entry_ref:
                raise OrderError("for a buy, the stop-loss must be below the entry price")
            if take_profit is not None and take_profit <= entry_ref:
                raise OrderError("for a buy, the take-profit must be above the entry price")
        if entry_ref and side is Side.SELL:
            if stop_loss is not None and stop_loss <= entry_ref:
                raise OrderError("for a short sale, the stop-loss must be above the entry price")
            if take_profit is not None and take_profit >= entry_ref:
                raise OrderError("for a short sale, the take-profit must be below the entry price")

        order = Order(
            id=self._new_id(),
            symbol=symbol,
            side=side,
            qty=qty,
            type=otype,
            limit_price=limit_price if otype is OrderType.LIMIT else None,
            stop_price=stop_price if otype is OrderType.STOP else None,
            tif=tif,
            status=Status.OPEN,
            created_index=index,
            created_time=time,
            tag=tag
            or (
                "exit" if (side is Side.SELL and pos > EPS) or (side is Side.BUY and pos < -EPS) else "entry"
            ),
            note=note,
        )
        self.orders[order.id] = order
        if stop_loss is not None or take_profit is not None:
            group = f"g{order.id}"
            exit_side = Side.SELL if side is Side.BUY else Side.BUY
            if stop_loss is not None:
                child = Order(
                    self._new_id(),
                    symbol,
                    exit_side,
                    qty,
                    OrderType.STOP,
                    None,
                    float(stop_loss),
                    "gtc",
                    Status.PENDING,
                    index,
                    time,
                    tag="stop_loss",
                    parent_id=order.id,
                    oco_group=group,
                )
                self.orders[child.id] = child
            if take_profit is not None:
                child = Order(
                    self._new_id(),
                    symbol,
                    exit_side,
                    qty,
                    OrderType.LIMIT,
                    float(take_profit),
                    None,
                    "gtc",
                    Status.PENDING,
                    index,
                    time,
                    tag="take_profit",
                    parent_id=order.id,
                    oco_group=group,
                )
                self.orders[child.id] = child
        return order

    def _close(self, o: Order, status: Status, reason: str, index: int | None) -> None:
        """Cancel, expire or reject ``o``; bracket children still waiting for it go with it."""
        o.status, o.reason, o.closed_index = status, reason, index
        for child in self.orders.values():
            if child.parent_id == o.id and child.status is Status.PENDING:
                child.status = Status.CANCELED
                child.reason = f"parent order {status.value}"
                child.closed_index = index

    def cancel(self, order_id: str, reason: str = "canceled by user", index: int | None = None) -> Order:
        order = self.orders.get(order_id)
        if order is None:
            raise OrderError(f"no order {order_id}")
        if order.status not in (Status.OPEN, Status.PENDING):
            raise OrderError(f"order {order_id} is already {order.status.value}")
        self._close(order, Status.CANCELED, reason, index)
        return order

    @staticmethod
    def _annotate(trade, o: Order, exit_leg: bool) -> None:
        if o.note and o.note not in trade.notes:
            trade.notes.append(o.note)
        if exit_leg and o.tag not in trade.tags:
            trade.tags.append(o.tag)

    # -- bar processing -------------------------------------------------------------
    def _trigger_price(self, o: Order, bar: Bar) -> tuple[float, bool] | None:
        """(fill price, filled at the open?) if ``o`` executes on ``bar``, else None."""
        if o.type is OrderType.MARKET:
            return bar.open * (1 + self.slip if o.side is Side.BUY else 1 - self.slip), True
        if o.type is OrderType.LIMIT:
            lim = o.limit_price
            if o.side is Side.BUY:
                if bar.open < lim:
                    return bar.open, True
                return (lim, False) if bar.low < lim else None
            if bar.open > lim:
                return bar.open, True
            return (lim, False) if bar.high > lim else None
        stop = o.stop_price
        if o.side is Side.BUY:
            if bar.open >= stop:
                return bar.open * (1 + self.slip), True
            return (stop * (1 + self.slip), False) if bar.high >= stop else None
        if bar.open <= stop:
            return bar.open * (1 - self.slip), True
        return (stop * (1 - self.slip), False) if bar.low <= stop else None

    @staticmethod
    def _priority(o: Order) -> tuple[int, int]:
        rank = {OrderType.MARKET: 0, OrderType.STOP: 1, OrderType.LIMIT: 2}[o.type]
        return rank, int(o.id[1:])

    def process_bar(self, index: int, time: Any, bars: dict[str, Bar]) -> list[dict[str, Any]]:
        """Fill eligible orders against this bar, then mark positions to its close."""
        fills: list[dict[str, Any]] = []
        self._intrabar_entries = {}
        for _pass in range(2):  # second pass: bracket children armed by a fill at the open
            eligible = sorted(
                (
                    o
                    for o in self.orders.values()
                    if o.status is Status.OPEN and o.created_index < index and o.symbol in bars
                ),
                key=self._priority,
            )
            if not eligible:
                break
            for o in eligible:
                if o.status is not Status.OPEN:
                    continue  # canceled by an OCO sibling earlier in this bar
                bar = bars[o.symbol]
                hit = self._trigger_price(o, bar)
                if hit is None:
                    continue
                fill = self._execute(o, hit[0], hit[1], index, time)
                if fill:
                    fills.append(fill)
        for o in list(self.orders.values()):
            if o.status is Status.OPEN and o.tif == "day" and o.created_index < index:
                self._close(o, Status.EXPIRED, "day order not filled", index)
        for sym, bar in bars.items():
            self.last_prices[sym] = bar.close
            if abs(self.position(sym)) <= EPS:
                continue
            trade = self.ledger.open_trades.get(sym)
            crossing = self._intrabar_entries.get(sym)
            if crossing and trade is not None and trade.entry_index == index:
                # Entered mid-bar. Only the extreme beyond the fill in the direction the price was
                # crossing is known to come after the fill; the other side may predate the trade.
                if crossing == "down":
                    self.ledger.mark_excursions(sym, bar.close, bar.low)
                else:
                    self.ledger.mark_excursions(sym, bar.high, bar.close)
            else:
                self.ledger.mark_excursions(sym, bar.high, bar.low)
        self._finance()
        return fills

    def _finance(self) -> None:
        led = self.ledger
        if self._borrow > 0:
            short_value = sum(
                -pos.qty * self.last_prices.get(sym, pos.avg_price)
                for sym, pos in led.positions.items()
                if pos.qty < -EPS
            )
            if short_value > 0:
                led.charge(short_value * self._borrow)
        if led.cash < 0 and self._margin > 0:
            led.charge(-led.cash * self._margin)

    def _execute(self, o: Order, price: float, at_open: bool, index: int, time: Any) -> dict[str, Any] | None:
        pos = self.position(o.symbol)
        qty = o.qty
        if o.tag in ("stop_loss", "take_profit"):
            # Exit legs only ever flatten what is left of the position.
            held = abs(pos) if (pos > 0) == (o.side is Side.SELL) else 0.0
            qty = min(qty, held)
            if qty <= EPS:
                self._close(o, Status.CANCELED, "position already closed", index)
                return None
        if o.side is Side.SELL and not self.allow_short:
            qty = min(qty, max(pos, 0.0))
            if qty <= EPS:
                self._close(o, Status.REJECTED, "short selling disabled and no shares to sell", index)
                return None
        signed = qty if o.side is Side.BUY else -qty
        # Buying-power check for exposure-increasing fills, using prices at the fill.
        prices = {**self.last_prices, o.symbol: price}
        new_pos = pos + signed
        if abs(new_pos) > abs(pos) + EPS:
            equity = self.ledger.equity(prices)
            other = sum(abs(self.ledger.qty(s) * p) for s, p in prices.items() if s != o.symbol)
            max_value = max(self.max_leverage * equity - other, 0.0)
            if abs(new_pos) * price > max_value + 1e-6:
                allowed = (
                    max_value / price - abs(pos)
                    if (pos >= 0) == (signed > 0)
                    else max_value / price + abs(pos)
                )
                allowed = allowed if self.fractional else float(int(allowed))
                if allowed <= EPS:
                    self._close(o, Status.REJECTED, "insufficient buying power at fill", index)
                    return None
                qty = min(qty, allowed)
                signed = qty if o.side is Side.BUY else -qty
                o.reason = f"partially filled: buying power limited the order to {qty:g} shares"
        commission = self.commission(qty, price)
        if o.type is OrderType.LIMIT:
            slippage = 0.0
        else:  # price already includes adverse slippage on top of the trigger/open price
            ref = price / (1 + self.slip) if o.side is Side.BUY else price / (1 - self.slip)
            slippage = abs(qty) * abs(price - ref)
        before_open = self.ledger.open_trades.get(o.symbol)
        n_closed = len(self.ledger.closed_trades)
        self.ledger.apply_fill(
            time=time,
            index=index,
            symbol=o.symbol,
            qty=signed,
            price=price,
            commission=commission,
            slippage=slippage,
            order_id=o.id,
            tag=o.tag,
        )
        for closed in self.ledger.closed_trades[n_closed:]:
            self._annotate(closed, o, exit_leg=True)  # this fill closed the trade
        after_open = self.ledger.open_trades.get(o.symbol)
        if after_open is not None:
            same_direction = (after_open.direction == "long") == (signed > 0)
            self._annotate(after_open, o, exit_leg=not same_direction and after_open is before_open)
            if after_open is not before_open and not at_open:
                buy_limit_or_sell_stop = (o.side is Side.BUY) == (o.type is OrderType.LIMIT)
                self._intrabar_entries[o.symbol] = "down" if buy_limit_or_sell_stop else "up"
        o.status = Status.FILLED
        o.filled_index, o.filled_time, o.fill_price, o.filled_qty = index, time, price, qty
        # One-cancels-other siblings and activation of bracket children.
        if o.oco_group:
            for sib in self.orders.values():
                if (
                    sib.oco_group == o.oco_group
                    and sib.id != o.id
                    and sib.status in (Status.OPEN, Status.PENDING)
                ):
                    sib.status, sib.reason = Status.CANCELED, f"OCO: {o.tag.replace('_', '-')} filled"
                    sib.closed_index = index
        for child in self.orders.values():
            if child.parent_id == o.id and child.status is Status.PENDING:
                child.status = Status.OPEN
                child.qty = qty
                # Children of an order filled at the open can trigger later in the same bar.
                child.created_index = index - 1 if at_open else index
        if abs(self.position(o.symbol)) <= EPS:
            # Working exit legs have nothing left to protect. Children still waiting for an
            # unfilled entry (PENDING) belong to a future position and stay.
            for other in self.orders.values():
                if (
                    other.symbol == o.symbol
                    and other.tag in ("stop_loss", "take_profit")
                    and other.status is Status.OPEN
                    and other.parent_id != o.id
                ):
                    self._close(other, Status.CANCELED, "position closed", index)
        return {
            "order_id": o.id,
            "symbol": o.symbol,
            "side": o.side.value,
            "qty": qty,
            "price": price,
            "commission": commission,
            "tag": o.tag,
            "index": index,
            "time": int(pd.Timestamp(time).timestamp()),
        }
