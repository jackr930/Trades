"""What the paper trader needs from a broker, and the order it sends.

The paper trader talks to a broker only through this interface. ``trades.execution.alpaca``
implements it for Alpaca's *paper* API; tests implement it with a fake. There is deliberately no
live implementation: see config/live.yaml.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str  # buy | sell
    qty: float  # shares, positive
    client_order_id: str
    type: str = "market"  # market | stop (a stop entry: enter when the price reaches stop_price)
    time_in_force: str = "day"  # day | opg (opening auction) | cls (closing auction) | gtc
    stop_price: float | None = None  # the trigger of a stop entry
    stop_loss: float | None = None  # a protective stop attached to the entry (one-triggers-other)
    take_profit: float | None = None  # a take-profit attached too (a bracket order)
    purpose: str = ""  # entry | exit | resize | close | flatten: for the log, not sent to the broker

    def __post_init__(self):
        if self.side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        if self.qty <= 0:
            raise ValueError("qty must be positive")
        if self.type == "stop" and not self.stop_price:
            raise ValueError("a stop order needs a stop_price")
        if (self.stop_loss or self.take_profit) and self.time_in_force not in ("day", "gtc"):
            raise ValueError("protective legs need a day or gtc order")

    @property
    def signed(self) -> float:
        return self.qty if self.side == "buy" else -self.qty

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Broker(Protocol):
    def account(self) -> dict[str, Any]: ...  # equity, last_equity (previous close), cash

    def positions(self) -> dict[str, dict[str, float]]: ...  # symbol -> qty, market_value

    def open_orders(self) -> list[dict[str, Any]]: ...

    def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None: ...

    def fills(self, since: datetime) -> list[dict[str, Any]]: ...  # id, time, symbol, side, qty, price, order_id

    def submit(self, order: OrderRequest) -> dict[str, Any]: ...

    def cancel_order(self, order_id: str) -> None: ...

    def cancel_all_orders(self) -> None: ...

    def close_all_positions(self) -> None: ...
