"""Your real holdings, imported from a broker's CSV export (read-only: nothing is ever traded).

Supported exports, detected from their column names:

* **Schwab**: Accounts -> Positions -> Export.
* **Fidelity**: Positions -> Download (``Portfolio_Positions_<date>.csv``).
* **Vanguard**: Balances and holdings -> Download (the positions block of ``OfxDownload.csv``).
* **Robinhood**: Account -> Reports -> account activity CSV. Robinhood has no positions export,
  so positions are rebuilt from Buy and Sell rows at average cost; splits, transfers and
  option trades in the file are not handled.
* Anything else with a symbol column and a quantity (or shares) column.

Each holding is ``{account, symbol, description, quantity, value, cost_basis, cash}``: ``value`` is the
market value on the day of the export and ``cost_basis`` the total cost, either may be None
(Vanguard's export has no cost basis). Money market funds and cash rows become ``cash``.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any

MAX_HOLDINGS = 500
TAX_ADVANTAGED_WORDS = ("ira", "401", "403", "457", "roth", "hsa", "sep", "keogh", "529")
SKIP_SYMBOLS = {"account total", "pending activity", "total", "totals"}

# Column names, most specific first; matched after lower-casing and trimming.
COLUMNS = {
    "symbol": ("symbol", "ticker", "instrument"),
    "quantity": ("quantity", "qty (quantity)", "qty", "shares"),
    "value": ("mkt val (market value)", "market value", "current value", "total value", "value"),
    "cost_basis": ("cost basis total", "cost basis", "total cost basis", "cost basis ($)", "total cost"),
    "account": ("account name", "account number", "account"),
    "description": ("description", "investment name", "security description", "name"),
    "price": ("price", "last price", "share price"),
    "trans_code": ("trans code",),
    "date": ("activity date", "trade date", "date"),
    "amount": ("amount",),
}


def number(text: Any) -> float | None:
    """'$1,234.50' -> 1234.5, '(12.00)' -> -12.0, '--' or '' -> None."""
    s = str(text or "").strip()
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[$,%()+\s]", "", s)
    if s in ("", "-", "--", "n/a", "N/A"):
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def _columns(header: list[str]) -> dict[str, int]:
    names = [h.strip().lower().lstrip("﻿") for h in header]
    found = {}
    for key, options in COLUMNS.items():
        for option in options:
            if option in names:
                found[key] = names.index(option)
                break
    return found


def _broker(header: list[str], title: str) -> str:
    names = {h.strip().lower() for h in header}
    if "trans code" in names:
        return "Robinhood"
    if "qty (quantity)" in names or title.lower().startswith("positions for"):
        return "Schwab"
    if "account name" in names and "cost basis total" in names:
        return "Fidelity"
    if "investment name" in names:
        return "Vanguard"
    return "CSV"


def account_type(account: str) -> str:
    """A guess from the account's name: IRAs, 401(k)s, HSAs and the like are tax-advantaged."""
    words = account.lower()
    return "tax_advantaged" if any(w in words for w in TAX_ADVANTAGED_WORDS) else "taxable"


def parse(text: str) -> dict[str, Any]:
    """Holdings from a broker CSV export: ``{broker, holdings, notes}``. Raises ValueError if unreadable."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    head = next((i for i, r in enumerate(rows) if {"symbol", "quantity"} <= set(_columns(r))), None)
    if head is None:
        raise ValueError(
            "No positions found: the file needs a Symbol column and a Quantity (or Shares) column. "
            "Export positions from Schwab, Fidelity or Vanguard, or account activity from Robinhood."
        )
    header, cols = rows[head], _columns(rows[head])
    title = ",".join(rows[0]) if head > 0 else ""
    broker = _broker(header, title)
    match = re.search(r"Positions for account (.+?) as of", title)
    default_account = match.group(1).strip() if match else broker
    body = []
    for r in rows[head + 1 :]:
        if not any(c.strip() for c in r):
            break  # a blank line ends the block (Vanguard's transactions follow it)
        body.append(r)
    if broker == "Robinhood":
        holdings, notes = _from_activity(body, cols)
    else:
        holdings, notes = _from_positions(body, cols, default_account)
    if not holdings:
        raise ValueError("The file has a positions header but no positions under it.")
    if len(holdings) > MAX_HOLDINGS:
        raise ValueError(f"More than {MAX_HOLDINGS} positions: split the file by account.")
    return {"broker": broker, "holdings": holdings, "notes": notes}


def _cell(r: list[str], cols: dict[str, int], key: str) -> str:
    i = cols.get(key)
    return r[i].strip() if i is not None and i < len(r) else ""


def _from_positions(body: list[list[str]], cols: dict[str, int], default_account: str) -> tuple[list[dict], list[str]]:
    out, notes = [], []
    for r in body:
        symbol = _cell(r, cols, "symbol")
        description = _cell(r, cols, "description")
        if not symbol or symbol.lower() in SKIP_SYMBOLS:
            continue
        account = _cell(r, cols, "account") or default_account
        value = number(_cell(r, cols, "value"))
        is_cash = (
            symbol.lower().startswith("cash")
            or symbol.endswith("**")
            or "money market" in description.lower()
        )
        quantity = number(_cell(r, cols, "quantity"))
        if is_cash:
            if value is None:
                value = quantity  # a money market fund's shares are worth $1 each
            out.append(_holding(account, "CASH", value, value, value, cash=True, description=description))
            continue
        if quantity is None:
            notes.append(f"Skipped {symbol}: no quantity.")
            continue
        if value is None:
            price = number(_cell(r, cols, "price"))
            value = price * quantity if price is not None else None
        cost = number(_cell(r, cols, "cost_basis"))
        out.append(_holding(account, symbol, quantity, value, cost, description=description))
    if any(h["cost_basis"] is None and not h["cash"] for h in out):
        notes.append("Some positions have no cost basis, so their gains and losses are unknown.")
    return out, notes


def _from_activity(body: list[list[str]], cols: dict[str, int]) -> tuple[list[dict], list[str]]:
    """Robinhood: positions from Buy and Sell rows at average cost, oldest first."""

    def when(r: list[str]) -> datetime:
        try:
            return datetime.strptime(_cell(r, cols, "date"), "%m/%d/%Y")
        except ValueError:
            return datetime.min

    rows = sorted(reversed(body), key=when)  # newest first in the file: reverse, then a stable sort by date
    qty: dict[str, float] = {}
    cost: dict[str, float] = {}
    ignored = set()
    for r in rows:
        code = _cell(r, cols, "trans_code").lower()
        symbol = _cell(r, cols, "symbol").upper()
        q, amount = number(_cell(r, cols, "quantity")), number(_cell(r, cols, "amount"))
        if not symbol or q is None:
            continue
        if code == "buy":
            qty[symbol] = qty.get(symbol, 0.0) + q
            cost[symbol] = cost.get(symbol, 0.0) + abs(amount or 0.0)
        elif code == "sell" and qty.get(symbol, 0.0) > 0:
            share = min(q / qty[symbol], 1.0)
            cost[symbol] *= 1 - share
            qty[symbol] -= min(q, qty[symbol])
        elif code not in ("buy", "sell"):
            ignored.add(code.upper())
    out = [
        _holding("Robinhood", s, round(q, 6), None, round(cost[s], 2))
        for s, q in sorted(qty.items())
        if q > 1e-9
    ]
    notes = ["Rebuilt from account activity at average cost; market values are not in the file."]
    if ignored:
        notes.append(f"Ignored activity types: {', '.join(sorted(ignored))} (splits, transfers and options are not handled).")
    return out, notes


def _holding(account: str, symbol: str, quantity: float | None, value: float | None, cost: float | None,
             cash: bool = False, description: str = "") -> dict[str, Any]:
    return {
        "account": account,
        "symbol": symbol.upper(),
        "description": description,
        "quantity": quantity,
        "value": value,
        "cost_basis": cost,
        "cash": cash,
    }


def merge(existing: list[dict], imported: list[dict]) -> list[dict]:
    """Replace the accounts present in ``imported``; keep every other account as it was."""
    accounts = {h["account"] for h in imported}
    return [h for h in existing if h.get("account") not in accounts] + imported


def valid(holdings: Any) -> bool:
    if not isinstance(holdings, list) or len(holdings) > MAX_HOLDINGS:
        return False
    for h in holdings:
        if not isinstance(h, dict) or not isinstance(h.get("symbol"), str) or not isinstance(h.get("account"), str):
            return False
        for key in ("quantity", "value", "cost_basis"):
            if h.get(key) is not None and not isinstance(h[key], (int, float)):
                return False
    return True


def summary(holdings: list[dict], account_types: dict[str, str]) -> dict[str, Any]:
    """Totals by account, and unrealised gain or loss where the cost basis is known."""
    by_account: dict[str, dict[str, Any]] = {}
    for h in holdings:
        a = by_account.setdefault(
            h["account"],
            {"type": account_types.get(h["account"], account_type(h["account"])), "value": 0.0, "cash": 0.0, "positions": 0},
        )
        a["value"] += h.get("value") or 0.0
        a["cash"] += (h.get("value") or 0.0) if h.get("cash") else 0.0
        a["positions"] += 0 if h.get("cash") else 1
    known = [h for h in holdings if not h.get("cash") and h.get("value") is not None and h.get("cost_basis") is not None]
    return {
        "total_value": sum(a["value"] for a in by_account.values()),
        "unrealised": sum(h["value"] - h["cost_basis"] for h in known),
        "accounts": by_account,
    }
