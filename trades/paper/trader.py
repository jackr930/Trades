"""``trades paper``: trade the experiment's strategy on an Alpaca *paper* account.

Each run, in the evening after the close:

1. reconciles earlier orders: their status and Alpaca's actual fill, next to the price the
   backtest engine would have assumed (that session's open, moved against you by the
   slippage setting);
2. computes the strategy's target weights from completed bars (the same bars as the journal);
3. reads the paper account's actual positions and equity from Alpaca (it never assumes
   earlier orders filled) and orders the difference, rounded like the backtest engine, and
   skipping re-sizing trades under ``min_trade_weight`` of equity, as the engine does;
4. checks the guardrails, and only with ``--submit`` sends the orders.

Orders fill at the next open like the engine's: whole-share orders go to the opening
auction (``time_in_force="opg"``); fractional quantities, which Alpaca does not accept in
the auction, go as day market orders queued for the open. Alpaca accepts opening-auction
orders before 9:28am ET or after 7:00pm ET, so orders are only sent between 7:00pm ET after
the decision session and 9:28am ET on the next trading day. Each order's client_order_id
is built from the session date, symbol and experiment id, so a rerun can never order twice.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trades.backtest.engine import round_qty
from trades.backtest.runner import StrategySpec
from trades.core.calendar import NY, last_completed_session, next_trading_day
from trades.core.ledger import EPS
from trades.data.base import align_bars
from trades.journal.experiment import JOURNAL_DIR, Experiment
from trades.journal.recorder import Fetch, fetch_session
from trades.paper.alpaca import PaperAPIError, PaperClient
from trades.strategies.sizing import apply_sizing

ORDERS_FILE = JOURNAL_DIR / "paper_orders.csv"
HALT_FILE = JOURNAL_DIR / "PAPER_HALTED"  # committed, so `trades paper halt` also stops GitHub Actions
OPEN_AFTER = time(19, 0)  # Alpaca queues opening-auction orders sent after 7:00pm ET ...
OPEN_BEFORE = time(9, 28)  # ... and accepts them until 9:28am ET on the day
FINAL = ("filled", "canceled", "expired", "rejected", "done_for_day", "replaced")
ORDER_COLUMNS = (
    "run_time_utc",
    "session_date",
    "trade_date",
    "symbol",
    "side",
    "qty",
    "time_in_force",
    "client_order_id",
    "alpaca_order_id",
    "status",
    "decision_close",
    "open_price",
    "assumed_price",
    "fill_price",
    "filled_qty",
    "slippage_bps",
    "experiment_id",
    "code_version",
)


@dataclass(frozen=True)
class Limits:
    halted: bool = False  # the kill switch
    max_daily_loss: float = 0.03  # no orders after the paper account lost more than this since the prior close
    max_orders: int = 20
    max_gross: float = 1.0
    allow_short: bool = False
    fractional: bool = False
    min_trade_weight: float = 0.005  # the backtest engine's: skip re-sizing trades under 0.5% of equity


@dataclass
class PlannedOrder:
    symbol: str
    side: str  # buy | sell
    qty: float  # positive
    time_in_force: str  # opg | day
    current: float  # shares held now
    target: float  # shares wanted
    price: float  # the decision close

    @property
    def signed(self) -> float:
        return self.qty if self.side == "buy" else -self.qty


def client_order_id(session: date, symbol: str, experiment_id: str) -> str:
    return f"{session:%Y%m%d}-{symbol}-{experiment_id}"


def halted(settings_halted: bool, halt_file: Path = HALT_FILE) -> bool:
    return settings_halted or halt_file.exists()


# --------------------------------------------------------------------------------------
# Planning and guardrails (pure functions)
# --------------------------------------------------------------------------------------


def target_weights(exp: Experiment, frames: dict[str, pd.DataFrame]) -> dict[str, float]:
    """The strategy's target weights at the last completed bar, sized as in a backtest."""
    spec = StrategySpec.from_dict(exp.definition.get("paper_strategy") or {"id": "consensus"})
    if spec.id == "consensus" and not spec.params:
        spec.params = exp.consensus_params()
    strat, sizing = spec.build()
    data = align_bars({s: frames[s] for s in exp.watchlist})
    weights = apply_sizing(strat.run(data).signals, data, sizing, strat.kind)
    return {s: float(w) for s, w in weights.iloc[-1].items()}


def plan_orders(
    targets: dict[str, float], prices: dict[str, float], equity: float, positions: dict[str, float], limits: Limits
) -> list[PlannedOrder]:
    """Orders that move the actual positions to the target weights, as the engine would."""
    orders = []
    for sym, w in targets.items():
        price, cur = prices[sym], positions.get(sym, 0.0)
        target = round_qty(w * equity / price, limits.fractional)
        if target < 0:
            target = round_qty(target, fractional=False)  # Alpaca has no fractional short sales
        if cur * target < 0:
            target = 0.0  # close first; the other side opens next run (no position flips in one order)
        delta = target - cur
        if abs(delta) <= EPS:
            continue
        resizing = abs(cur) > EPS and abs(target) > EPS
        if resizing and abs(delta) * price < limits.min_trade_weight * equity:
            continue  # a small re-size the backtest engine would skip too
        whole = abs(delta - round(delta)) <= 1e-9
        orders.append(
            PlannedOrder(sym, "buy" if delta > 0 else "sell", abs(delta), "opg" if whole else "day", cur, target, price)
        )
    return orders


def guardrail_problems(
    orders: list[PlannedOrder],
    positions: dict[str, float],
    values: dict[str, float],
    prices: dict[str, float],
    account: dict[str, Any],
    watchlist: list[str],
    limits: Limits,
) -> list[str]:
    """Reasons not to send these orders; empty when every guardrail passes.

    ``values`` are the market values of the current positions (for symbols without a price).
    """
    problems = []
    equity, last = float(account["equity"]), float(account.get("last_equity") or 0)
    if limits.halted:
        problems.append("The kill switch is on (`trades paper resume` turns it off).")
    if last > 0 and equity / last - 1 < -limits.max_daily_loss:
        problems.append(
            f"The paper account is down {1 - equity / last:.1%} since the prior close, more than the "
            f"{limits.max_daily_loss:.0%} daily loss limit: no new orders today."
        )
    if len(orders) > limits.max_orders:
        problems.append(f"{len(orders)} orders exceed the limit of {limits.max_orders} per run.")
    for o in orders:
        if o.symbol not in watchlist:
            problems.append(f"{o.symbol} is not in the experiment's watchlist.")
    after = dict(positions)
    for o in orders:
        after[o.symbol] = after.get(o.symbol, 0.0) + o.signed
    if not limits.allow_short:
        problems += [f"{s} would end up short, and shorting is off." for s, q in after.items() if q < -EPS]

    def gross(held: dict[str, float]) -> float:
        total = 0.0
        for s, q in held.items():
            if s in prices:
                total += abs(q * prices[s])
            elif abs(positions.get(s, 0.0)) > EPS:
                total += abs(values.get(s, 0.0) * q / positions[s])
        return total

    before_g, after_g = gross(positions), gross(after)
    # Orders may never take gross exposure above the cap; ones that reduce it are always allowed.
    if after_g > limits.max_gross * equity + 1e-6 and after_g > before_g + 1e-6:
        problems.append(
            f"Gross exposure would be {after_g / equity:.1%} of equity, above the {limits.max_gross:.0%} cap."
        )
    return problems


def in_submission_window(now: datetime, session: date) -> bool:
    """Between 7:00pm ET after the decision session and 9:28am ET on the next trading day."""
    ny = now.astimezone(NY)
    start = datetime.combine(session, OPEN_AFTER, NY)
    end = datetime.combine(next_trading_day(session), OPEN_BEFORE, NY)
    return start <= ny < end


# --------------------------------------------------------------------------------------
# The order log and reconciliation
# --------------------------------------------------------------------------------------


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_log(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ORDER_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def reconcile(
    rows: list[dict[str, Any]], client: PaperClient, opens: Callable[[list[tuple[str, date]]], dict], slippage_bps: float
) -> list[dict[str, Any]]:
    """Fill in each logged order's status and Alpaca's fill, next to what the engine assumed.

    ``opens([(symbol, date)])`` returns the open price of each (symbol, trade date) that has one.
    """
    for r in rows:
        if r["status"] not in FINAL or not r["fill_price"]:
            o = client.order_by_client_id(r["client_order_id"])
            if o is not None:
                r["alpaca_order_id"] = o.get("id", r["alpaca_order_id"])
                r["status"] = o.get("status", r["status"])
                r["filled_qty"] = o.get("filled_qty") or r["filled_qty"]
                r["fill_price"] = o.get("filled_avg_price") or r["fill_price"]
    need = [(r["symbol"], date.fromisoformat(r["trade_date"])) for r in rows if not r["open_price"]]
    known = opens(need) if need else {}
    for r in rows:
        key = (r["symbol"], date.fromisoformat(r["trade_date"]))
        if not r["open_price"] and key in known:
            r["open_price"] = round(known[key], 4)
        open_px = _num(r["open_price"])
        if math.isfinite(open_px) and open_px > 0:
            sign = 1.0 if r["side"] == "buy" else -1.0
            r["assumed_price"] = round(open_px * (1 + sign * slippage_bps / 1e4), 4)
            fill = _num(r["fill_price"])
            if math.isfinite(fill):
                r["slippage_bps"] = round(sign * (fill / open_px - 1) * 1e4, 2)  # positive = cost
    return rows


# --------------------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------------------


def run(
    exp: Experiment,
    client: PaperClient,
    fetch: Fetch,
    limits: Limits,
    *,
    submit: bool = False,
    now: datetime | None = None,
    orders_path: Path = ORDERS_FILE,
    halt_check: Callable[[], bool] | None = None,
    version: str = "unknown",
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """Plan (and with ``submit``, send) today's orders. Returns a process exit code."""
    now = now or datetime.now(timezone.utc)
    session = last_completed_session(now)
    trade_date = next_trading_day(session)
    slippage = float(exp.account["slippage_bps"])

    def opens(keys: list[tuple[str, date]]) -> dict[tuple[str, date], float]:
        frames: dict[str, pd.DataFrame] = {}
        for provider in exp.providers:  # the first provider that answers
            try:
                frames, _ = fetch(provider, sorted({s for s, _ in keys}), min(d for _, d in keys))
            except Exception:  # e.g. no Alpaca keys: try the next provider
                continue
            if frames:
                break
        out = {}
        for sym, d in keys:
            df = frames.get(sym)
            ts = pd.Timestamp(d).tz_localize("UTC")
            if df is not None and ts in df.index:
                out[(sym, d)] = float(df.loc[ts, "open"])
        return out

    rows = reconcile(read_log(orders_path), client, opens, slippage)
    kwargs = {"sleep": sleep} if sleep is not None else {}
    frames, _ = fetch_session(exp, fetch, session, log=log, **kwargs)
    targets = target_weights(exp, frames)
    prices = {s: float(frames[s]["close"].iloc[-1]) for s in exp.watchlist}
    account = client.account()
    held = client.positions()
    positions = {s: p["qty"] for s, p in held.items()}
    values = {s: p["market_value"] for s, p in held.items()}
    equity = float(account["equity"])
    orders = plan_orders(targets, prices, equity, positions, limits)
    problems = guardrail_problems(orders, positions, values, prices, account, exp.watchlist, limits)
    ours = {client_order_id(session, s, exp.id) for s in exp.watchlist}  # this session's: a rerun skips them
    waiting = [
        o for o in client.open_orders() if o.get("symbol") in exp.watchlist and o.get("client_order_id") not in ours
    ]
    if waiting:
        problems.append(f"{len(waiting)} earlier order(s) are still open: resolve them before ordering more.")

    log(f"Decision on the {session} close for the {trade_date} open; paper equity ${equity:,.2f}.")
    for o in orders:
        log(
            f"  {o.side.upper():4s} {o.qty:g} {o.symbol} ({o.time_in_force}): {o.current:g} -> {o.target:g} shares, "
            f"target weight {targets[o.symbol]:+.1%}"
        )
    if not orders:
        log("  No orders: the paper account already matches the targets.")
    for p in problems:
        log(f"  GUARDRAIL: {p}")
    if rows:  # save the reconciled fills whatever happens next
        write_log(orders_path, rows)
    if not submit:
        log("Dry run: nothing was sent. Add --submit to send these orders to the Alpaca paper account.")
        return 0
    if problems:
        log("Nothing was sent.")
        return 1
    if orders and not in_submission_window(now, session):
        log(
            "Nothing was sent: orders for the next open go out between 7:00pm ET after the close and 9:28am ET "
            "(Alpaca's opening-auction window)."
        )
        return 1
    run_time = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sent = 0
    for o in orders:
        if halt_check is not None and halt_check():  # the kill switch, checked before every order
            log("Kill switch turned on during the run: stopping.")
            break
        cid = client_order_id(session, o.symbol, exp.id)
        existing = client.order_by_client_id(cid)
        if existing is None:
            try:
                existing = client.submit_order(o.symbol, o.qty, o.side, o.time_in_force, cid)
                sent += 1
            except PaperAPIError as exc:
                log(f"  {o.symbol}: Alpaca refused the order ({exc}).")
                continue
        if any(r["client_order_id"] == cid for r in rows):
            continue  # already logged by an earlier run
        rows.append(
            {
                **dict.fromkeys(ORDER_COLUMNS, ""),
                "run_time_utc": run_time,
                "session_date": session.isoformat(),
                "trade_date": trade_date.isoformat(),
                "symbol": o.symbol,
                "side": o.side,
                "qty": f"{o.qty:g}",
                "time_in_force": o.time_in_force,
                "client_order_id": cid,
                "alpaca_order_id": existing.get("id", ""),
                "status": existing.get("status", ""),
                "decision_close": round(o.price, 4),
                "experiment_id": exp.id,
                "code_version": version,
            }
        )
    write_log(orders_path, rows)
    log(f"Sent {sent} order(s) to the Alpaca paper account.")
    return 0

