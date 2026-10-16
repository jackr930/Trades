"""An in-memory Alpaca paper account for the paper runners' tests.

Orders fill against synthetic bars with Alpaca's rules where they matter to the runners: bracket
legs are held until their entry fills and cancel each other (one-cancels-other), shares held by
working sell orders cannot be sold twice ("insufficient qty"), market-on-close orders are refused
after 3:50pm ET, and day orders expire at the close.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from trades.data.calendar import NY, session_bounds
from trades.execution.alpaca import PaperAPIError
from trades.execution.broker import OrderRequest
from trades.execution.ledger import EPS

OPEN = ("new", "accepted", "held", "pending_new")
BAR = timedelta(minutes=5)


class SimAlpaca:
    def __init__(self, bars: dict[str, pd.DataFrame], cash: float = 100_000.0, last_equity: float | None = None,
                 slip_bps: float = 0.0):
        self.bars = bars
        self.slip = slip_bps / 1e4  # every fill this much worse than the bar's price
        self._close_equity: float | None = None  # becomes last_equity overnight, as at Alpaca
        self.cash = cash
        self.last_equity = cash if last_equity is None else last_equity
        self.qty: dict[str, float] = {}
        self.orders: dict[str, dict[str, Any]] = {}  # by id, in submission order
        self.now: datetime | None = None
        self.done_until: pd.Timestamp | None = None  # bars processed while their end <= this
        self.refuse: set[str] = set()  # order types or time_in_force values to refuse ("cls", "market", ...)
        self.revoked = False
        self.cancels: list[str] = []
        self.activity: list[dict[str, Any]] = []  # every fill, like Alpaca's account activities
        self.n = 0
        self._times = pd.DatetimeIndex(sorted(set().union(*(df.index for df in bars.values()))))

    # -- prices --------------------------------------------------------------------------
    def price(self, s: str) -> float:
        """The close of the latest completed bar."""
        df = self.bars[s]
        if self.now is None:
            return float(df["close"].iloc[-1])
        i = int(df.index.searchsorted(pd.Timestamp(self.now) - BAR, side="right"))
        return float(df["close"].iloc[i - 1]) if i else float(df["open"].iloc[0])

    # -- the Broker protocol ---------------------------------------------------------------
    def _check(self) -> None:
        if self.revoked:
            raise PaperAPIError(401, '{"message": "unauthorized"}')

    def equity(self) -> float:
        return self.cash + sum(q * self.price(s) for s, q in self.qty.items())

    def account(self) -> dict[str, Any]:
        self._check()
        return {"equity": str(self.equity()), "last_equity": str(self.last_equity), "cash": str(self.cash)}

    def positions(self) -> dict[str, dict[str, float]]:
        self._check()
        return {s: {"qty": q, "market_value": q * self.price(s)} for s, q in self.qty.items() if abs(q) > EPS}

    def open_orders(self) -> list[dict[str, Any]]:
        self._check()
        return [dict(o) for o in self.orders.values() if o["status"] in OPEN]

    def order_by_client_id(self, cid: str) -> dict[str, Any] | None:
        self._check()
        for o in self.orders.values():
            if o["client_order_id"] == cid:
                return dict(o)
        return None

    def fills(self, since: datetime) -> list[dict[str, Any]]:
        self._check()
        return [dict(a) for a in self.activity if pd.Timestamp(a["time"]) > pd.Timestamp(since)]

    def _new(self, **fields: Any) -> dict[str, Any]:
        self.n += 1
        o = {"id": f"o{self.n}", "client_order_id": f"alpaca-{self.n}", "status": "accepted", "filled_qty": "0",
             "filled_avg_price": None, "stop_price": None, "limit_price": None, "parent": None,
             "submitted": pd.Timestamp(self.now), **fields}
        self.orders[o["id"]] = o
        return o

    def submit(self, order: OrderRequest) -> dict[str, Any]:
        self._check()
        if order.type in self.refuse or order.time_in_force in self.refuse:
            raise PaperAPIError(422, f'{{"message": "refused by the test ({order.time_in_force})"}}')
        if any(o["client_order_id"] == order.client_order_id for o in self.orders.values()):
            raise PaperAPIError(422, '{"message": "client_order_id must be unique"}')
        if order.time_in_force == "cls":
            close = session_bounds(pd.Timestamp(self.now).tz_convert(NY).date())[1]
            if pd.Timestamp(self.now) >= pd.Timestamp(close) - timedelta(minutes=10):
                raise PaperAPIError(422, '{"message": "cls orders are not accepted after 3:50pm ET"}')
        held = self.qty.get(order.symbol, 0.0)
        if held and (order.side == "sell") == (held > 0):  # an order that reduces the position
            reserved = sum(float(o["qty"]) for o in self.open_orders()
                           if o["symbol"] == order.symbol and o["side"] == order.side and o["status"] != "held")
            if order.qty > abs(held) - reserved + EPS:
                raise PaperAPIError(403, '{"message": "insufficient qty available for order"}')
        parent = self._new(symbol=order.symbol, side=order.side, qty=str(order.qty), type=order.type,
                           time_in_force=order.time_in_force, stop_price=order.stop_price,
                           client_order_id=order.client_order_id)
        exit_side = "sell" if order.side == "buy" else "buy"
        if order.stop_loss:
            self._new(symbol=order.symbol, side=exit_side, qty=str(order.qty), type="stop", time_in_force="day",
                      stop_price=order.stop_loss, status="held", parent=parent["id"])
        if order.take_profit:
            self._new(symbol=order.symbol, side=exit_side, qty=str(order.qty), type="limit", time_in_force="day",
                      limit_price=order.take_profit, status="held", parent=parent["id"])
        return dict(parent)

    def cancel_order(self, order_id: str) -> None:
        self._check()
        self.cancels.append(order_id)
        o = self.orders[order_id]
        if o["status"] in OPEN:
            o["status"] = "canceled"
        for leg in self.orders.values():
            if leg["parent"] == order_id and leg["status"] in OPEN:
                leg["status"] = "canceled"

    def cancel_all_orders(self) -> None:
        for o in list(self.orders.values()):
            if o["status"] in OPEN:
                self.cancel_order(o["id"])

    def close_all_positions(self) -> None:
        self.cancel_all_orders()
        for s, q in list(self.qty.items()):
            if abs(q) > EPS:
                self._fill(self._new(symbol=s, side="sell" if q > 0 else "buy", qty=str(abs(q)), type="market",
                                     time_in_force="day"), self.price(s))

    # -- the market ------------------------------------------------------------------------
    def _fill(self, o: dict[str, Any], price: float, when: Any = None) -> None:
        qty = float(o["qty"])
        signed = qty if o["side"] == "buy" else -qty
        price *= 1 + self.slip if signed > 0 else 1 - self.slip
        self.qty[o["symbol"]] = self.qty.get(o["symbol"], 0.0) + signed
        self.cash -= signed * price
        when = pd.Timestamp(when if when is not None else self.now)
        o.update(status="filled", filled_qty=str(qty), filled_avg_price=str(round(price, 4)), filled_at=when)
        self.activity.append({"id": f"fill-{len(self.activity) + 1}", "time": when.tz_convert("UTC").isoformat(),
                              "symbol": o["symbol"], "side": o["side"], "qty": qty, "price": round(price, 4),
                              "order_id": o["id"]})
        for leg in self.orders.values():
            if leg["parent"] == o["id"] and leg["status"] == "held":
                leg["status"] = "new"  # the protective legs go live
        if o["parent"]:  # one leg filled: its sibling is cancelled
            for leg in self.orders.values():
                if leg["parent"] == o["parent"] and leg["id"] != o["id"] and leg["status"] in OPEN:
                    leg["status"] = "canceled"

    def begin(self, day) -> None:
        """Start the market at ``day``'s pre-market: earlier bars count as already traded."""
        open_ = pd.Timestamp(session_bounds(day)[0])
        self.now = open_.to_pydatetime() - timedelta(minutes=30)
        if self._close_equity is not None:
            self.last_equity = self._close_equity
        before = self._times[self._times < open_]
        self.done_until = before[-1] if len(before) else None

    def advance(self, now: datetime) -> None:
        """Let the market run until ``now``: every bar that has completed is checked against the orders."""
        self.now = now
        lo = 0 if self.done_until is None else int(self._times.searchsorted(self.done_until, side="right"))
        hi = int(self._times.searchsorted(pd.Timestamp(now) - BAR, side="right"))
        for t in self._times[lo:hi]:
            self._bar(t)
            self.done_until = t

    def _bar(self, t: pd.Timestamp) -> None:
        day = t.tz_convert(NY).date()
        close = pd.Timestamp(session_bounds(day)[1])
        last_bar = t + BAR >= close
        for o in list(self.orders.values()):
            if o["status"] not in ("new", "accepted") or o["submitted"] >= t + BAR:
                continue  # not working, or sent after this bar ended
            bar = self.bars[o["symbol"]]
            if t not in bar.index:
                continue
            op, hi, lo, cl = (float(bar.loc[t, k]) for k in ("open", "high", "low", "close"))
            buy = o["side"] == "buy"
            early = o["submitted"] < t + timedelta(minutes=1)  # in time for this bar's open
            if o["time_in_force"] == "cls":
                if last_bar:
                    self._fill(o, cl, t + BAR)
            elif o["type"] == "market":
                self._fill(o, op if early else cl, t if early else t + BAR)
            elif o["type"] == "stop":
                level = float(o["stop_price"])
                if buy and hi >= level:
                    self._fill(o, max(op, level) if early else level, t)
                elif not buy and lo <= level:
                    self._fill(o, min(op, level) if early else level, t)
            elif o["type"] == "limit":
                level = float(o["limit_price"])
                if (not buy and hi > level) or (buy and lo < level):
                    self._fill(o, level, t)
        if last_bar:
            for o in self.orders.values():
                if o["status"] in OPEN and o["time_in_force"] in ("day", "cls"):
                    o["status"] = "expired"
            self._close_equity = self.equity()
