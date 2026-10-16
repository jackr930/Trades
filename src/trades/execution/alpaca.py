"""Alpaca's *paper* trading API, and nothing else.

The base URL is a constant. There is no setting, argument or environment variable that
changes it, every request is checked against it before it is sent, and redirects are not
followed, so this client cannot reach Alpaca's live (real-money) trading endpoint. A live
path, if it ever exists, is the playbook's Phase 4 and is added by a person after the
paper-to-live gate passes (see config/live.yaml), never by changing this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from trades.execution.broker import OrderRequest

PAPER_URL = "https://paper-api.alpaca.markets"


class PaperAPIError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Alpaca paper API error {status}: {message}")
        self.status = status


class PaperClient:
    """Account, positions and orders on an Alpaca paper account."""

    def __init__(self, key_id: str, secret_key: str, client: httpx.Client | None = None):
        if not key_id or not secret_key:
            raise PaperAPIError(
                401,
                "paper trading needs an Alpaca paper API key: add it in Settings or set "
                "APCA_API_KEY_ID / APCA_API_SECRET_KEY",
            )
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self._client = client or httpx.Client(timeout=20.0)

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = PAPER_URL + path
        if not path.startswith("/v2/") or httpx.URL(url).host != httpx.URL(PAPER_URL).host:
            raise PaperAPIError(0, f"refusing to call {url!r}: only the paper endpoint is allowed")
        resp = self._client.request(method, url, headers=self._headers, follow_redirects=False, **kwargs)
        if resp.status_code >= 300:
            raise PaperAPIError(resp.status_code, resp.text[:300])
        return resp.json() if resp.content else None

    # -- reads ---------------------------------------------------------------------------
    def account(self) -> dict[str, Any]:
        """Includes ``equity``, ``last_equity`` (at the previous close) and ``cash``."""
        return self._request("GET", "/v2/account")

    def positions(self) -> dict[str, dict[str, float]]:
        """Per symbol: shares held (``qty``, negative = short) and ``market_value``."""
        return {
            p["symbol"]: {"qty": float(p["qty"]), "market_value": float(p.get("market_value") or 0.0)}
            for p in self._request("GET", "/v2/positions")
        }

    def open_orders(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v2/orders", params={"status": "open", "limit": 500})

    def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        try:
            return self._request(
                "GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_order_id}
            )
        except PaperAPIError as exc:
            if exc.status == 404:
                return None
            raise

    def fills(self, since: datetime) -> list[dict[str, Any]]:
        """Every fill after ``since``, oldest first, protective legs included: Alpaca's account
        activities, the broker's own record of what traded."""
        out: list[dict[str, Any]] = []
        params: dict[str, Any] = {"after": since.astimezone(timezone.utc).isoformat(), "direction": "asc", "page_size": 100}
        while True:
            page = self._request("GET", "/v2/account/activities/FILL", params=params) or []
            out += [
                {"id": a["id"], "time": a["transaction_time"], "symbol": a["symbol"],
                 "side": "buy" if a["side"] == "buy" else "sell", "qty": float(a["qty"]), "price": float(a["price"]),
                 "order_id": a.get("order_id", "")}
                for a in page
            ]
            if len(page) < params["page_size"]:
                return out
            params["page_token"] = page[-1]["id"]

    def clock(self) -> dict[str, Any]:
        """Whether the market is open now, and the next open and close."""
        return self._request("GET", "/v2/clock")

    # -- writes --------------------------------------------------------------------------
    def submit(self, order: OrderRequest) -> dict[str, Any]:
        """Send ``order``: a market or stop order, optionally with a protective stop
        (one-triggers-other) and a take-profit (a bracket), as Alpaca's order classes do it."""
        body: dict[str, Any] = {
            "symbol": order.symbol,
            "qty": f"{order.qty:.6f}".rstrip("0").rstrip("."),
            "side": order.side,
            "type": order.type,
            "time_in_force": order.time_in_force,
            "client_order_id": order.client_order_id,
        }
        if order.type == "stop":
            body["stop_price"] = price_text(order.stop_price)
        if order.stop_loss or order.take_profit:
            if abs(order.qty - round(order.qty)) > 1e-9:
                raise PaperAPIError(0, "orders with protective legs must be whole shares")
            body["order_class"] = "bracket" if order.stop_loss and order.take_profit else "oto"
            if order.stop_loss:
                body["stop_loss"] = {"stop_price": price_text(order.stop_loss)}
            if order.take_profit:
                body["take_profit"] = {"limit_price": price_text(order.take_profit)}
        return self._request("POST", "/v2/orders", json=body)

    def submit_order(
        self, symbol: str, qty: float, side: str, time_in_force: str, client_order_id: str
    ) -> dict[str, Any]:
        """A market order. ``time_in_force``: "opg" (opening auction), "cls" (closing) or "day"."""
        return self.submit(OrderRequest(symbol, side, qty, client_order_id, time_in_force=time_in_force))

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"/v2/orders/{order_id}")

    def cancel_all_orders(self) -> None:
        self._request("DELETE", "/v2/orders")

    def close_all_positions(self) -> None:
        """The broker-side kill switch: cancel every open order and close every position."""
        self._request("DELETE", "/v2/positions", params={"cancel_orders": "true"})


def price_text(price: float | None) -> str:
    """Prices in the increments US exchanges accept: cents at $1 and above, 1/100 of a cent below."""
    if price is None:
        raise PaperAPIError(0, "missing price")
    return f"{price:.2f}" if price >= 1 else f"{price:.4f}"
