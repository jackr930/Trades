"""``trades paper``: trade one spec on an Alpaca *paper* account, exactly the way its backtest trades.

The decision path is the backtest's own: ``trades.engine.live.decide`` (strategy, sizing, risk
engine) and ``trades.execution.orders.plan_symbol`` (target to orders). Only the broker differs:
Alpaca's paper account fills the orders instead of ``sim_broker``, and ``trades track`` measures
what that difference costs. Two schedules use it:

* **daily specs** (``run`` below) run once each evening, from the GitHub Actions job or cron:
  reconcile earlier orders, decide on the completed session, send orders for the next open;
* **intraday specs** run through the session on a machine that stays up
  (``trades.engine.intraday``; see deploy/).

Everything a run does is recorded in ``paper/<spec key>/`` (``PaperRecords``), committed so the
record outlives the machine: the order log, every fill (the broker's own record, protective legs
included: the trade database your accountant needs), every decision, the equity after each
session, events (risk interventions, rejections, errors, drills) and the state the next run needs.

Safety: ``--submit`` works only for a spec whose latest research run passed the acceptance
checklist (``paper_allowed``). Dry runs always work, and neither send nor record anything. The
kill switch (the HALT file: ``trades halt``) is checked before every order. Every request goes to
Alpaca's paper endpoint (``trades.execution.alpaca``); there is no live order path.

Daily orders fill at the next open, like the backtest's: whole-share orders go to the opening
auction (``time_in_force="opg"``), fractional ones as day market orders queued for the open.
Alpaca takes opening-auction orders after 7:00pm ET and before 9:28am ET, so that is when daily
orders are sent. Each client_order_id is built from the session, symbol and spec, so a rerun can
never order twice.
"""

from __future__ import annotations

import csv
import json
import math
import time as _time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trades import config
from trades.data.base import HISTORY_START
from trades.data.calendar import NY, last_completed_session, next_trading_day
from trades.engine.live import Decision, decide
from trades.execution import costs
from trades.execution.alpaca import PaperAPIError
from trades.execution.broker import Broker, OrderRequest
from trades.execution.ledger import EPS
from trades.execution.orders import plan_symbol, round_qty
from trades.execution.sim_broker import BacktestConfig
from trades.journal.recorder import Fetch, fetch_session
from trades.risk import RiskEngine, RiskLimits, killswitch
from trades.strategies import StrategyConfig, get_strategy_class

OPEN_AFTER = time(19, 0)  # Alpaca queues opening-auction orders sent after 7:00pm ET ...
OPEN_BEFORE = time(9, 28)  # ... and accepts them until 9:28am ET on the day
FINAL = ("filled", "canceled", "expired", "rejected", "done_for_day", "replaced")
ORDER_COLUMNS = (
    "run_time_utc",
    "session_date",  # the session whose close the decision was made at
    "trade_date",  # the session the order trades in
    "bar_time",  # intraday: the bar the decision was made at
    "symbol",
    "side",
    "qty",
    "type",  # market | stop
    "time_in_force",  # opg | cls | day
    "stop_price",  # a stop entry's trigger
    "stop_loss",  # protective legs attached to an entry
    "take_profit",
    "purpose",  # rebalance (daily) | entry | exit | close (at the session's end) | flatten
    "client_order_id",
    "alpaca_order_id",
    "status",
    "decision_close",
    "open_price",  # daily: the trade date's open ...
    "assumed_price",  # ... moved against us by the spec's costs: what the backtest assumed
    "fill_price",
    "filled_qty",
    "slippage_bps",  # the fill against that open, positive = cost
    "spec",
    "code_version",
)
EQUITY_COLUMNS = ("session_date", "equity", "last_equity", "positions", "run_time_utc")
FILL_COLUMNS = ("fill_id", "time_utc", "symbol", "side", "qty", "price", "order_id", "client_order_id", "purpose", "spec")


@dataclass(frozen=True)
class PaperTarget:
    """What a paper account trades: one spec version, with the engine settings it was researched with."""

    key: str  # e.g. STRAT-001-v1: in every client_order_id, so two specs or versions never collide
    symbols: list[str]
    strategy: StrategyConfig
    providers: list[str]  # data providers, tried in order
    timeframe: str = "1d"
    intraday: bool = False
    engine: BacktestConfig = field(default_factory=BacktestConfig)  # costs, share rounding, re-size threshold
    history_start: date = HISTORY_START
    benchmark: str = "SPY"

    @property
    def watchlist(self) -> list[str]:  # the name the journal's fetch helper reads
        return self.symbols

    @property
    def slippage_bps(self) -> float:
        """What the research assumed one market fill costs (half spread plus slippage)."""
        return costs.per_fill_bps(self.engine)

    @classmethod
    def from_spec(cls, spec) -> PaperTarget:
        providers = ["synthetic"] if spec.provider == "synthetic" else list(dict.fromkeys([spec.provider, "alpaca"]))
        return cls(
            key=spec.key,
            symbols=spec.symbols,
            strategy=spec.strategy,
            providers=providers,
            timeframe=spec.timeframe,
            intraday=get_strategy_class(spec.strategy.id).intraday,
            engine=costs.with_model(BacktestConfig(), spec.costs),
            benchmark=spec.symbols[0],
        )


def paper_allowed(spec) -> tuple[bool, str]:
    """May orders be sent for ``spec``? Only if its latest research run passed the whole checklist."""
    from trades import runs

    if spec.drift():
        return False, spec.drift()
    last = runs.latest(spec.key)
    if last is None:
        return False, f"{spec.key} has no research run: `trades research {spec.id}` first."
    if last["verdict"] != "pass":
        return False, f"{spec.key}'s latest research run ({last['id']}) did not pass the acceptance checklist."
    return True, f"{spec.key} passed the acceptance checklist in run {last['id']}."


# --------------------------------------------------------------------------------------
# The records: paper/<spec key>/
# --------------------------------------------------------------------------------------


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, columns: Iterable[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns), lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _stamp(t: Any) -> str:
    ts = pd.Timestamp(t)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


class PaperRecords:
    """``paper/<spec key>/``: everything the paper trader did for one spec version."""

    def __init__(self, key: str, root: Path | None = None):
        self.key = key
        self.folder = (root or config.path(str(config.value("paper", "paper_dir", "paper")))) / key
        self.orders_path = self.folder / "orders.csv"
        self.fills_path = self.folder / "fills.csv"
        self.decisions_path = self.folder / "decisions.jsonl"
        self.equity_path = self.folder / "equity.csv"
        self.events_path = self.folder / "events.jsonl"
        self.state_path = self.folder / "state.json"
        self.reports_dir = self.folder / "reports"

    # orders
    def read_orders(self) -> list[dict[str, Any]]:
        return _read_csv(self.orders_path)

    def write_orders(self, rows: list[dict[str, Any]]) -> None:
        _write_csv(self.orders_path, ORDER_COLUMNS, rows)

    # fills: the broker's record of every execution, with the order that caused it where it was ours
    def read_fills(self) -> list[dict[str, Any]]:
        return _read_csv(self.fills_path)

    def record_fills(self, client: Broker, orders: list[dict[str, Any]], fallback_since: datetime) -> int:
        """Fetch the fills since the last one recorded and add them; returns how many were new."""
        fetch = getattr(client, "fills", None)
        if fetch is None:
            return 0
        known = self.read_fills()
        since = pd.Timestamp(known[-1]["time_utc"]).to_pydatetime() if known else fallback_since
        ours = {r["alpaca_order_id"]: r for r in orders if r.get("alpaca_order_id")}
        seen = {r["fill_id"] for r in known}
        new = []
        for f in fetch(since):
            if f["id"] in seen:
                continue
            mine = ours.get(f["order_id"])
            new.append({
                "fill_id": f["id"], "time_utc": _stamp(f["time"]), "symbol": f["symbol"], "side": f["side"],
                "qty": f"{float(f['qty']):g}", "price": f["price"], "order_id": f["order_id"],
                "client_order_id": mine["client_order_id"] if mine else "",
                "purpose": mine["purpose"] if mine else "protective",  # a bracket leg (or an order sent by hand)
                "spec": self.key,
            })
        if new:
            _write_csv(self.fills_path, FILL_COLUMNS, sorted(known + new, key=lambda r: r["time_utc"]))
        return len(new)

    # decisions and events: one JSON object per line
    def _append(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")

    def log_decision(self, decision: Decision, **extra: Any) -> None:
        self._append(self.decisions_path, {**decision.to_dict(), **extra})

    def log_event(self, when: Any, kind: str, message: str, **extra: Any) -> None:
        self._append(self.events_path, {"time": _stamp(when), "kind": kind, "message": message, **extra})

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def decisions(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.decisions_path)

    def events(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.events_path)

    # equity: one row per session, the last run of the session wins
    def record_equity(self, session: date, equity: float, last_equity: float, positions: int, when: Any) -> None:
        rows = [r for r in _read_csv(self.equity_path) if r["session_date"] != session.isoformat()]
        rows.append({"session_date": session.isoformat(), "equity": round(equity, 2), "last_equity": round(last_equity, 2),
                     "positions": positions, "run_time_utc": _stamp(when)})
        _write_csv(self.equity_path, EQUITY_COLUMNS, sorted(rows, key=lambda r: r["session_date"]))

    def equity(self) -> pd.DataFrame:
        rows = _read_csv(self.equity_path)
        if not rows:
            return pd.DataFrame(columns=["equity", "last_equity", "positions"])
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df.pop("session_date"))
        return df[["equity", "last_equity", "positions"]].astype(float)

    # state for the next run
    def load_state(self) -> dict[str, Any]:
        try:
            saved = json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return saved if saved.get("spec") == self.key else {}

    def save_state(self, state: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"spec": self.key, **state}, indent=1, default=str) + "\n")


# --------------------------------------------------------------------------------------
# Planning and the final batch check (pure functions)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    """The per-run limits the order planner and the final batch check use (from risk/limits.yaml)."""

    halted: bool = False  # the kill switch
    max_daily_loss: float = 0.015  # beyond it, only orders that reduce exposure go out
    max_orders: int = 40
    max_gross: float = 1.0
    allow_short: bool = False
    fractional: bool = False
    min_trade_weight: float = 0.005  # the backtest engine's: skip re-sizing trades under 0.5% of equity

    @classmethod
    def from_risk(cls, risk: RiskLimits, engine: BacktestConfig, *, halted: bool = False) -> Limits:
        return cls(
            halted=halted,
            max_daily_loss=risk.daily_loss_limit,
            max_orders=risk.max_orders_per_day,
            max_gross=risk.max_gross_exposure,
            allow_short=risk.allow_short,
            fractional=engine.fractional,
            min_trade_weight=engine.min_trade_weight,
        )


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


def client_order_id(session: date, symbol: str, key: str) -> str:
    return f"{session:%Y%m%d}-{symbol}-{key}"


def plan_orders(
    targets: dict[str, float],
    prices: dict[str, float],
    equity: float,
    positions: dict[str, float],
    limits: Limits,
    last_targets: dict[str, float] | None = None,
    planned: dict[str, float] | None = None,
) -> tuple[list[PlannedOrder], dict[str, float]]:
    """Orders that move the actual positions toward the targets by the backtest's rule
    (``plan_symbol``), and the target weights to remember for the next run.

    ``last_targets``: the weights wanted at the previous run. ``planned``: the shares it aimed for.
    Where the account holds something else (a partial fill, a refused order), the previous target
    counts as not reached and the symbol is planned afresh: the paper trader never assumes that
    earlier orders filled.
    """
    last_targets, planned = last_targets or {}, planned or {}
    orders, remembered = [], dict(last_targets)
    for sym, w in targets.items():
        price, cur = prices[sym], positions.get(sym, 0.0)
        last: float | None = last_targets.get(sym, 0.0)
        if sym in planned and abs(planned[sym] - cur) > EPS:
            last = None
        plan = plan_symbol(
            w, cur, price, equity, last_target=last, fractional=limits.fractional, min_trade_weight=limits.min_trade_weight
        )
        if plan.skip:
            continue
        remembered[sym] = float(plan.last_target or 0.0)
        if plan.market is None:
            continue
        target = plan.market
        if target < 0:
            target = round_qty(target, fractional=False)  # Alpaca has no fractional short sales
        if cur * target < 0:
            target = 0.0  # close first; the other side opens next run (no position flips in one order)
        delta = target - cur
        if abs(delta) <= EPS:
            continue
        whole = abs(delta - round(delta)) <= 1e-9
        orders.append(
            PlannedOrder(sym, "buy" if delta > 0 else "sell", abs(delta), "opg" if whole else "day", cur, target, price)
        )
    return orders, remembered


def guardrail_problems(
    orders: list[PlannedOrder],
    positions: dict[str, float],
    values: dict[str, float],
    prices: dict[str, float],
    account: dict[str, Any],
    watchlist: list[str],
    limits: Limits,
) -> list[str]:
    """Reasons not to send this batch; empty when every check passes. The risk engine has already
    shaped the targets; this is a second, independent look at the actual orders before they leave.

    ``values`` are the market values of the current positions (for symbols without a price).
    """
    problems = []
    equity, last = float(account["equity"]), float(account.get("last_equity") or 0)
    if limits.halted:
        problems.append("The kill switch is on (`trades resume` turns it off).")
    after = dict(positions)
    for o in orders:
        after[o.symbol] = after.get(o.symbol, 0.0) + o.signed
    adds = [o for o in orders if abs(after[o.symbol]) > abs(positions.get(o.symbol, 0.0)) + EPS]
    if last > 0 and equity / last - 1 < -limits.max_daily_loss and adds:
        problems.append(
            f"The paper account is down {1 - equity / last:.1%} since the prior close, more than the "
            f"{limits.max_daily_loss:.1%} daily loss limit: only orders that reduce exposure may go out."
        )
    if len(orders) > limits.max_orders:
        problems.append(f"{len(orders)} orders exceed the limit of {limits.max_orders} per run.")
    for o in orders:
        if o.symbol not in watchlist:
            problems.append(f"{o.symbol} is not in the spec's universe.")
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


def flatten(
    client: Broker,
    limits: RiskLimits | None = None,
    *,
    reason: str = "flatten",
    halt: bool = True,
    sleep: Callable[[float], None] | None = None,
    tries: int = 15,
    log: Callable[[str], None] = print,
) -> tuple[bool, str]:
    """The broker-side kill switch: turn the HALT file on (so nothing re-enters), cancel every open
    order and close every position on the paper account, then wait until it is flat. Market orders
    only fill while the market is open: outside the session they wait for the next open."""
    if halt:
        killswitch.halt(reason, limits)
        log("Kill switch ON (the HALT file): nothing new will be sent.")
    client.close_all_positions()
    log("Asked Alpaca to cancel every open order and close every position.")
    wait = sleep or _time.sleep
    held: dict[str, float] = {}
    for attempt in range(tries):
        held = {s: p["qty"] for s, p in client.positions().items() if abs(p["qty"]) > EPS}
        if not held:
            return True, "The paper account is flat" + (" and the kill switch is on." if halt else ".")
        if attempt < tries - 1:
            wait(2.0)
    return False, f"Still holding {held}: market orders fill only while the market is open. Check Alpaca's dashboard."


def in_submission_window(now: datetime, session: date) -> bool:
    """Between 7:00pm ET after the decision session and 9:28am ET on the next trading day."""
    ny = now.astimezone(NY)
    start = datetime.combine(session, OPEN_AFTER, NY)
    end = datetime.combine(next_trading_day(session), OPEN_BEFORE, NY)
    return start <= ny < end


# --------------------------------------------------------------------------------------
# Reconciliation: what Alpaca actually did, next to what the backtest assumed
# --------------------------------------------------------------------------------------


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def refresh_orders(rows: list[dict[str, Any]], client: Broker) -> list[dict[str, Any]]:
    """Update each logged order that is not final (or filled without a price yet) from the broker."""
    for r in rows:
        if r["status"] not in FINAL or (r["status"] == "filled" and not r["fill_price"]):
            o = client.order_by_client_id(r["client_order_id"])
            if o is not None:
                r["alpaca_order_id"] = o.get("id", r["alpaca_order_id"])
                r["status"] = o.get("status", r["status"])
                r["filled_qty"] = o.get("filled_qty") or r["filled_qty"]
                r["fill_price"] = o.get("filled_avg_price") or r["fill_price"]
    return rows


def reconcile(
    rows: list[dict[str, Any]], client: Broker, opens: Callable[[list[tuple[str, date]]], dict], slippage_bps: float
) -> list[dict[str, Any]]:
    """Fill in each logged order's status and Alpaca's fill, next to the price the backtest engine
    assumed (that session's open, moved against us by the spec's costs).

    ``opens([(symbol, date)])`` returns the open price of each (symbol, trade date) that has one.
    """
    refresh_orders(rows, client)
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
# One nightly run (daily specs)
# --------------------------------------------------------------------------------------


def _levels_without_orders(decision: Decision) -> list[str]:
    """Symbols the decision wants with a stop, take-profit or entry level the nightly runner cannot place."""
    out = []
    for s, w in decision.targets.items():
        if abs(w) > 1e-12 and any(lv.get(s) is not None for lv in (decision.stop_losses, decision.take_profits, decision.entry_stops)):
            out.append(s)
    return out


def run(
    target: PaperTarget,
    client: Broker,
    fetch: Fetch,
    risk_limits: RiskLimits,
    records: PaperRecords,
    *,
    submit: bool = False,
    validated: bool = True,
    now: datetime | None = None,
    halt_check: Callable[[], bool] | None = None,
    version: str = "unknown",
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """Plan (and with ``submit``, send) tonight's orders for a daily spec. Returns a process exit code.

    ``validated``: whether the spec passed the acceptance checklist (``paper_allowed``)."""
    now = now or datetime.now(timezone.utc)
    session = last_completed_session(now)
    trade_date = next_trading_day(session)
    halted = bool(halt_check and halt_check())
    limits = Limits.from_risk(risk_limits, target.engine, halted=halted)

    def opens(keys: list[tuple[str, date]]) -> dict[tuple[str, date], float]:
        frames: dict[str, pd.DataFrame] = {}
        for provider in target.providers:  # the first provider that answers
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

    rows = reconcile(records.read_orders(), client, opens, target.slippage_bps)
    if rows:
        records.record_fills(client, rows, pd.Timestamp(rows[0]["run_time_utc"]).to_pydatetime())
    kwargs = {"sleep": sleep} if sleep is not None else {}
    frames, provider = fetch_session(target, fetch, session, log=log, **kwargs)
    account = client.account()
    held = client.positions()
    positions = {s: p["qty"] for s, p in held.items()}
    values = {s: p["market_value"] for s, p in held.items()}
    equity = float(account["equity"])
    last_equity = float(account.get("last_equity") or equity)
    current = {s: values.get(s, 0.0) / equity for s in target.symbols} if equity > 0 else {}

    saved = records.load_state()
    risk = RiskEngine(risk_limits, target.symbols, halt_check=lambda: halted)
    risk.restore(saved.get("risk", {}), opening_equity=last_equity)
    decision = decide(target.strategy, {s: frames[s] for s in target.symbols}, risk=risk, equity=equity, current=current)
    orders, remembered = plan_orders(
        decision.targets, decision.prices, equity, positions, limits, saved.get("last_targets"), saved.get("planned")
    )
    problems = guardrail_problems(orders, positions, values, decision.prices, account, target.symbols, limits)
    if levels := _levels_without_orders(decision):
        problems.append(
            f"The decision has stop, take-profit or entry levels for {', '.join(levels)}, which the nightly runner "
            "does not place: run this spec on the intraday runner."
        )
    ours = {client_order_id(session, s, target.key) for s in target.symbols}  # this session's: a rerun skips them
    waiting = [
        o for o in client.open_orders() if o.get("symbol") in target.symbols and o.get("client_order_id") not in ours
    ]
    if waiting:
        problems.append(f"{len(waiting)} earlier order(s) are still open: resolve them before ordering more.")

    log(f"{target.key}: decision on the {session} close for the {trade_date} open; paper equity ${equity:,.2f}.")
    if not validated:
        log("UNVALIDATED: this spec has not passed the acceptance checklist, so this can only be a dry run.")
    for e in risk.events:
        log(f"  RISK: {e.message}")
    for o in orders:
        log(
            f"  {o.side.upper():4s} {o.qty:g} {o.symbol} ({o.time_in_force}): {o.current:g} -> {o.target:g} shares, "
            f"target weight {decision.targets[o.symbol]:+.1%}"
        )
    if not orders:
        log("  No orders: the paper account already matches the targets.")
    for p in problems:
        log(f"  GUARDRAIL: {p}")
    if rows:  # save the reconciled fills whatever happens next
        records.write_orders(rows)
    if not submit:
        log("Dry run: nothing was sent or recorded. Add --submit to send these orders to the Alpaca paper account.")
        return 0
    if not validated:
        log("Nothing was sent: --submit needs a spec that passed the acceptance checklist.")
        return 1
    if problems:
        log("Nothing was sent.")
        return 1
    if orders and not in_submission_window(now, session):
        log(
            "Nothing was sent: orders for the next open go out between 7:00pm ET after the close and 9:28am ET "
            "(Alpaca's opening-auction window)."
        )
        return 1

    run_time = _stamp(now)
    sent, refused, stopped = 0, 0, False
    try:
        for o in orders:
            if halt_check is not None and halt_check():  # the kill switch, checked before every order
                log("Kill switch turned on during the run: stopping.")
                records.log_event(now, "halt", "The kill switch was turned on during the run; the rest was not sent.")
                stopped = True
                break
            cid = client_order_id(session, o.symbol, target.key)
            existing = client.order_by_client_id(cid)
            if existing is None:
                try:
                    existing = client.submit(
                        OrderRequest(o.symbol, o.side, o.qty, cid, time_in_force=o.time_in_force, purpose="rebalance")
                    )
                    sent += 1
                except PaperAPIError as exc:
                    log(f"  {o.symbol}: Alpaca refused the order ({exc}).")
                    records.log_event(now, "rejection", f"Alpaca refused {o.side} {o.qty:g} {o.symbol}: {exc}", symbol=o.symbol)
                    refused += 1
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
                    "type": "market",
                    "time_in_force": o.time_in_force,
                    "purpose": "rebalance",
                    "client_order_id": cid,
                    "alpaca_order_id": existing.get("id", ""),
                    "status": existing.get("status", ""),
                    "decision_close": round(o.price, 4),
                    "spec": target.key,
                    "code_version": version,
                }
            )
    finally:
        records.write_orders(rows)  # orders already sent stay on record even if the run fails partway
    records.log_decision(decision, session=session.isoformat(), provider=provider, run_time_utc=run_time)
    for e in risk.events:
        records.log_event(e.time, e.kind, e.message, symbol=e.symbol)
    records.record_equity(session, equity, last_equity, sum(1 for q in positions.values() if abs(q) > EPS), now)
    aimed = {s: positions.get(s, 0.0) for s in target.symbols} | {o.symbol: o.target for o in orders}
    records.save_state({"updated": run_time, "risk": risk.state(), "last_targets": remembered, "planned": aimed})
    log(f"Sent {sent} order(s) to the Alpaca paper account" + (f"; {refused} refused." if refused else "."))
    return 1 if refused or stopped else 0  # a refused order or a mid-run halt is a failure the job reports
