"""Alpaca's *paper* trading API, and nothing else.

The base URL is a constant. There is no setting, argument or environment variable that
changes it, every request is checked against it before it is sent, and redirects are not
followed, so this client cannot reach Alpaca's live (real-money) trading endpoint.
Moving to real money is deliberately out of scope for this app.
"""

from __future__ import annotations

from typing import Any

import httpx

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

    # -- writes --------------------------------------------------------------------------
    def submit_order(
        self, symbol: str, qty: float, side: str, time_in_force: str, client_order_id: str
    ) -> dict[str, Any]:
        """A market order. ``time_in_force``: "opg" (opening auction) or "day"."""
        body = {
            "symbol": symbol,
            "qty": f"{qty:.6f}".rstrip("0").rstrip("."),
            "side": side,
            "type": "market",
            "time_in_force": time_in_force,
            "client_order_id": client_order_id,
        }
        return self._request("POST", "/v2/orders", json=body)
