"""``trades paper`` for intraday specs: one process per trading day, on a machine that stays up.

The process starts before the open (a systemd timer or docker compose; see deploy/) and loops every
``poll_seconds`` until the session is over:

* **every loop:** write the heartbeat, check the kill switch, read the paper account;
* **when a bar completes:** decide on the completed bars exactly as the backtest does
  (``decide``: strategy, sizing, risk engine) and bring the orders in line with the backtest's own
  rule (``plan_symbol``): a stop entry or market order, with the protective stop and take-profit
  attached as a bracket, so the broker protects the position even if this process dies;
* **15 minutes before the close:** cancel working entries and protective legs and send
  market-on-close orders, like the backtest's closing auction;
* **2 minutes before the close:** close anything still open without a closing order;
* **after the close:** record the day's equity, write the daily report, alert, exit.

A position that a protective order closed is noticed at the next loop, and the symbol is latched
like the backtest's: no re-entry in the same direction until the signal resets. An order is only
replaced once the broker confirms the old one is gone, so two entries are never working at once.

The kill switch (HALT file): nothing new is sent, working entries are cancelled, and positions
keep their protective orders; ``trades paper flatten`` closes everything. The daily and weekly loss
limits belong to the risk engine: it turns every target to zero, so positions are closed.

Where paper differs from the backtest, by design (``trades track`` measures each): an open position
is never re-sized (its protective legs are fixed at entry), no entries go out after the
market-on-close orders, and Alpaca, not the simulated broker, decides the fills.
"""

from __future__ import annotations

import json
import os
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd

from trades import config
from trades.data.calendar import NY, session_bounds
from trades.data.timeframes import Timeframe
from trades.engine.live import Decision, decide
from trades.engine.paper import FINAL, ORDER_COLUMNS, PaperRecords, PaperTarget, _stamp, refresh_orders
from trades.execution.alpaca import PaperAPIError
from trades.execution.broker import Broker, OrderRequest
from trades.execution.ledger import EPS
from trades.execution.orders import plan_symbol
from trades.reporting.alerts import ping
from trades.risk import RiskEngine, RiskLimits

Bars = Callable[[datetime], dict[str, pd.DataFrame]]  # completed bars through ``now``
Alert = Callable[[str, str], Any]
RISK_ALERTS = ("daily_loss", "weekly_loss", "orders")
RECANCEL = timedelta(seconds=60)  # ask again if a cancelled order is still working after this


@dataclass(frozen=True)
class Schedule:
    start: time = time(9, 0)  # ET: when the process starts (it waits for the open)
    moc_before_close: int = 15  # minutes: market-on-close orders replace everything working
    flatten_before_close: int = 2  # minutes: market orders close whatever is still open
    poll_seconds: float = 15.0
    heartbeat_minutes: float = 5.0

    @classmethod
    def from_config(cls) -> Schedule:
        paper = config.section("paper", "paper")
        s = paper.get("schedule") or {}
        return cls(
            start=time.fromisoformat(str(s.get("start", "09:00"))),
            moc_before_close=int(s.get("moc_before_close_minutes", 15)),
            flatten_before_close=int(s.get("flatten_before_close_minutes", 2)),
            poll_seconds=float(paper.get("poll_seconds", 15)),
            heartbeat_minutes=float(paper.get("heartbeat_minutes", 5)),
        )


def _side(qty: float) -> str:
    return "buy" if qty > 0 else "sell"


def _close_enough(a: Any, b: float) -> bool:
    try:
        return abs(float(a) - b) < 0.005  # to the cent: Alpaca rounds prices
    except (TypeError, ValueError):
        return False


class IntradayRunner:
    """One trading day of one intraday spec on the paper account.

    ``step(now)`` does one loop's work and is safe to repeat (orders are found again by their
    client_order_id); ``run()`` loops it in real time. A restart mid-session picks up the state
    saved in ``paper/<spec key>/state.json``.
    """

    def __init__(
        self,
        target: PaperTarget,
        broker: Broker,
        bars: Bars,
        risk_limits: RiskLimits,
        records: PaperRecords,
        *,
        session: date,
        schedule: Schedule = Schedule(),
        submit: bool = False,
        validated: bool = True,
        halt_check: Callable[[], bool] | None = None,
        alert: Alert | None = None,
        version: str = "unknown",
        log: Callable[[str], None] = print,
    ):
        self.target, self.broker, self.bars, self.records = target, broker, bars, records
        self.session, self.schedule = session, schedule
        self.validated = validated
        self.submit = submit and validated
        self.halt_check = halt_check or (lambda: False)
        self.alert = alert or (lambda kind, message: None)
        self.version, self.log = version, log
        self.open, self.close = session_bounds(session)
        self.moc_at = self.close - timedelta(minutes=schedule.moc_before_close)
        self.flatten_at = self.close - timedelta(minutes=schedule.flatten_before_close)
        self.tf = Timeframe.parse(target.timeframe)
        self.symbols = list(target.symbols)
        self.risk = RiskEngine(risk_limits, self.symbols, halt_check=self.halt_check)

        saved = records.load_state()
        today = saved.get("session") == session.isoformat()
        get = (lambda key, default: saved.get(key, default)) if today else (lambda key, default: default)
        self._risk_state: dict[str, Any] = saved.get("risk", {})  # the week's state carries across days
        self.last_bar = pd.Timestamp(get("last_bar", None)) if get("last_bar", None) else None
        self.last_targets: dict[str, float] = dict(get("last_targets", {}))
        self.latched: dict[str, float] = {s: float(d) for s, d in get("latched", {}).items()}
        self.held: dict[str, float] = dict(get("held", {}))
        self.closing: dict[str, dict[str, str]] = dict(get("closing", {}))  # symbol -> exit job {purpose, cid}
        self.entering: dict[str, dict[str, Any]] = dict(get("entering", {}))  # symbol -> entry waiting to go out
        self.seen_fills: set[str] = set(get("seen_fills", []))
        self.phase: str = get("phase", "trading")
        self.done = self.phase == "done"
        self.rows = records.read_orders()
        self._risk_ready = False
        self._halted_before = False
        self._cancelled: dict[str, datetime] = {}
        self._dry: set[str] = set()
        self._resized: set[str] = set()
        self._gap_alerted: Any = "none yet"
        self._events_seen = 0
        self._last_ping: datetime | None = None
        self._values: dict[str, float] = {}
        self._working: list[dict[str, Any]] = []

    # -- the loop -------------------------------------------------------------------------
    def run(self, clock: Callable[[], datetime] | None = None, sleep: Callable[[float], None] = _time.sleep) -> int:
        """Loop until the day's work is done. An unexpected error is recorded and alerted, and the
        loop carries on: the broker's protective orders stand meanwhile."""
        clock = clock or (lambda: datetime.now(timezone.utc))
        mode = "SENDING ORDERS to the paper account" if self.submit else "dry run: nothing is sent"
        self.log(f"{self.target.key}: intraday paper runner for {self.session} ({mode}).")
        if not self.validated:
            self.log("UNVALIDATED: this spec has not passed the acceptance checklist, so this can only be a dry run.")
        while True:
            now = clock()
            try:
                if not self.step(now):
                    return 0
            except Exception as exc:  # noqa: BLE001 - recorded, alerted, and the loop goes on
                self._note(now, "error", f"{type(exc).__name__}: {exc}", alert="error")
            if now >= self.close + timedelta(minutes=30):
                self._note(now, "error", "The runner could not finish the day cleanly: check the account.", alert="error")
                return 1
            sleep(self.schedule.poll_seconds)

    def step(self, now: datetime) -> bool:
        """One loop's work. Returns False once the day is done."""
        if self.done:
            return False
        self._heartbeat(now)
        halted = bool(self.halt_check())
        account, positions = self._sync(now)
        if halted and not self._halted_before:
            self._note(now, "halt", "The kill switch is on: nothing new is sent, working entries are cancelled, and "
                       "positions keep their protective orders (`trades paper flatten` closes them).", alert="halt")
            self.entering.clear()
            self.closing.clear()
            for o in self._working:
                if str(o.get("client_order_id", "")).endswith("-entry"):
                    self._cancel(now, o)
        elif self._halted_before and not halted:
            self._note(now, "resume", "The kill switch is off: trading resumes at the next bar.")
            if self.phase in ("close", "flatten"):
                self.phase = "trading"  # redo the end-of-day close-out for what is held now
        self._halted_before = halted
        if now >= self.close + timedelta(minutes=5):
            self._finish(now, account, positions)
            return False
        if halted or now < self.open:
            self._save(now)
            return True
        if now >= self.flatten_at:
            self._start_phase(now, "flatten", positions)
        elif now >= self.moc_at:
            self._start_phase(now, "close", positions)
        else:
            frames = self.bars(now)
            lasts = [df.index[-1] for df in frames.values() if len(df)]
            newest = min(lasts) if len(lasts) == len(self.symbols) else None  # the latest bar every symbol has
            if newest is not None and newest < pd.Timestamp(self.open):
                newest = None  # only yesterday's bars so far
            if newest is not None and (self.last_bar is None or newest > self.last_bar):
                self._decide(now, frames, account, positions)
            else:
                self._check_gap(now, newest)
        self._work_orders(now, positions)
        self._save(now)
        return True

    # -- reading the account ------------------------------------------------------------------
    def _sync(self, now: datetime) -> tuple[dict[str, Any], dict[str, float]]:
        account = self.broker.account()
        raw = self.broker.positions()
        positions = {s: float(p["qty"]) for s, p in raw.items()}
        self._values = {s: float(p.get("market_value", 0.0)) for s, p in raw.items()}
        self._working = self.broker.open_orders()
        if not self._risk_ready:
            self.risk.restore(self._risk_state, opening_equity=float(account.get("last_equity") or account["equity"]))
            self._risk_ready = True
            unknown = {s: q for s, q in positions.items() if s in self.symbols and abs(q) > EPS and s not in self.held}
            if unknown:  # a fresh day: nothing should be held yet
                self._note(now, "error", f"Positions carried overnight {unknown}: they are closed at the open.",
                           alert="error")
                for s in unknown:
                    self.closing[s] = {"purpose": "exit", "cid": self._cid("overnight", s, "exit")}
        today = [r for r in self.rows if r["session_date"] == self.session.isoformat()]
        if today:
            refresh_orders(today, self.broker)
            self.records.write_orders(self.rows)
        # A position that a protective order closed: latch the symbol, like the backtest.
        changed = 0
        for s in self.symbols:
            before, after = self.held.get(s, 0.0), positions.get(s, 0.0)
            changed += abs(before - after) > EPS
            entered_and_gone = [
                r for r in today if r["symbol"] == s and r["purpose"] == "entry" and r["status"] == "filled"
                and r["client_order_id"] not in self.seen_fills and abs(after) <= EPS
            ]
            closed = abs(before) > EPS and abs(after) <= EPS
            if (closed or entered_and_gone) and s not in self.closing:
                direction = before if closed else (1.0 if entered_and_gone[0]["side"] == "buy" else -1.0)
                self.latched[s] = 1.0 if direction > 0 else -1.0
                self._note(now, "protective_exit", f"{s}: a protective order closed the position; no re-entry that "
                           "way until the signal resets.", symbol=s)
        self.seen_fills |= {r["client_order_id"] for r in today if r["status"] == "filled"}
        self.risk.note_fills(changed)
        self.held = {s: positions.get(s, 0.0) for s in self.symbols}
        return account, positions

    # -- deciding ----------------------------------------------------------------------------
    def _decide(self, now: datetime, frames: dict[str, pd.DataFrame], account: dict[str, Any],
                positions: dict[str, float]) -> None:
        equity = float(account["equity"])
        current = {s: self._values.get(s, 0.0) / equity for s in self.symbols} if equity > 0 else {}
        decision = decide(self.target.strategy, frames, periods_per_year=self.tf.periods_per_year, risk=self.risk,
                          equity=equity, current=current)
        self.last_bar = pd.Timestamp(decision.time)
        self.records.log_decision(decision, session=self.session.isoformat(), equity=equity)
        self._risk_events(now)
        if decision.closing:
            return  # the session's last bar: the closing auction has the last word
        for s in self.symbols:
            held = positions.get(s, 0.0)
            plan = plan_symbol(
                decision.targets[s], held, decision.prices[s], equity,
                last_target=self.last_targets.get(s, 0.0), latched=self.latched.get(s),
                entry=decision.entry_stops.get(s), stop=decision.stop_losses.get(s), take=decision.take_profits.get(s),
                fractional=False, min_trade_weight=self.target.engine.min_trade_weight,
            )
            if plan.skip:
                continue
            self.latched.pop(s, None)
            self.last_targets[s] = float(plan.last_target or 0.0)
            if s in self.closing:
                continue  # already on its way out
            if abs(held) <= EPS:
                self._plan_entry(now, decision, s, plan, equity)
            elif plan.market is not None:
                if abs(plan.market) <= EPS or plan.market * held < 0:  # exit (a reversal exits first)
                    self.closing[s] = {"purpose": "exit", "cid": self._cid(decision.time, s, "exit")}
                elif s not in self._resized:
                    self._resized.add(s)
                    self._note(now, "resize_skipped", f"{s}: the backtest would re-size to {plan.market:g} shares; "
                               "the paper runner keeps the position it entered.", symbol=s)

    def _plan_entry(self, now: datetime, decision: Decision, s: str, plan, equity: float) -> None:
        """What should be working to enter ``s`` (flat now): a stop entry, a market order, or nothing."""
        want = plan.entry  # (level, signed shares) of a stop entry
        market = plan.market if plan.market is not None and abs(plan.market) > EPS else None
        if want is None and market is None:
            self.entering.pop(s, None)
            for o in self._working:
                if o.get("symbol") == s:
                    self._cancel(now, o)
            return
        qty = want[1] if want is not None else market
        price = want[0] if want is not None else decision.prices[s]
        qty = self._within_gross(s, qty, price, equity)
        stop, take = plan.protect or (None, None)
        job = {
            "symbol": s, "side": _side(qty), "qty": abs(qty), "client_order_id": self._cid(decision.time, s, "entry"),
            "type": "stop" if want is not None else "market", "stop_price": want[0] if want is not None else None,
            "stop_loss": stop, "take_profit": take, "purpose": "entry",
            "bar_time": _stamp(decision.time), "decision_close": round(decision.prices[s], 4),
        }
        for o in self._working:
            if o.get("symbol") != s:
                continue
            same = (
                o.get("type") == job["type"] and o.get("side") == job["side"] and _close_enough(o.get("qty"), job["qty"])
                and (job["stop_price"] is None or _close_enough(o.get("stop_price"), job["stop_price"]))
            )
            if same:
                self.entering.pop(s, None)
                return  # the entry already working is the one wanted
            self._cancel(now, o)
        if abs(qty) > EPS:
            self.entering[s] = job

    def _within_gross(self, s: str, qty: float, price: float, equity: float) -> float:
        """Shrink an entry so gross exposure, counting other working entries as filled, stays within the cap."""
        cap = self.risk.limits.max_gross_exposure * equity
        held = sum(abs(v) for v in self._values.values())
        pending = sum(
            float(o.get("qty") or 0) * float(o.get("stop_price") or o.get("limit_price") or price)
            for o in self._working
            if o.get("symbol") != s and str(o.get("client_order_id", "")).endswith("-entry")
        )
        pending += sum(j["qty"] * (j["stop_price"] or price) for k, j in self.entering.items() if k != s)
        most = float(int(max(cap - held - pending, 0.0) / price)) if price > 0 else 0.0
        return qty if abs(qty) <= most else (most if qty > 0 else -most)

    # -- the end of the session ------------------------------------------------------------------
    def _start_phase(self, now: datetime, phase: str, positions: dict[str, float]) -> None:
        if self.phase == phase or (phase == "close" and self.phase == "flatten"):
            return
        self.phase = phase
        self.entering.clear()  # no new entries from here on
        if phase == "close":
            self._note(now, "close", "Market-on-close: working orders cancelled; open positions go to the closing auction.")
            for o in self._working:
                if o.get("symbol") in self.symbols:
                    self._cancel(now, o)
            for s in self.symbols:
                if abs(positions.get(s, 0.0)) > EPS:
                    self.closing[s] = {"purpose": "close", "cid": self._cid("close", s, "close")}
        else:
            for s in self.symbols:
                if abs(positions.get(s, 0.0)) > EPS and not self._close_working(s):
                    self.closing[s] = {"purpose": "flatten", "cid": self._cid("flatten", s, "flatten")}

    def _close_working(self, s: str) -> bool:
        """Is a market-on-close order for ``s`` accepted and waiting for the auction?"""
        return any(
            r["symbol"] == s and r["purpose"] == "close" and r["session_date"] == self.session.isoformat()
            and r["status"] and r["status"] not in FINAL
            for r in self.rows
        )

    def _work_orders(self, now: datetime, positions: dict[str, float]) -> None:
        """Send what is due, once the orders in its way are gone: exits first (protective legs hold
        the shares until they are cancelled), then entries (the old entry must be gone before the
        new one goes out, or both could fill)."""
        for s, job in list(self.closing.items()):
            q = positions.get(s, 0.0)
            if abs(q) <= EPS:
                del self.closing[s]
                continue
            sent = job.get("sent", job["cid"])
            mine = [o for o in self._working if o.get("symbol") == s]
            others = [o for o in mine if o.get("client_order_id") != sent]
            if others:
                for o in others:
                    self._cancel(now, o)
                continue
            if mine:
                continue  # our exit is working
            tif = "cls" if job["purpose"] == "close" else "day"
            if not self.submit:
                self._send(now, OrderRequest(s, _side(-q), abs(q), sent, time_in_force=tif, purpose=job["purpose"]))
                continue
            tries = int(job.get("tries", 0))
            if tries:
                last = self.broker.order_by_client_id(sent)
                if last is not None and last.get("status") not in ("canceled", "expired", "rejected"):
                    continue  # filled or on its way: the position catches up at the next loop
                if tries >= 3:
                    if not job.get("gave_up"):
                        job["gave_up"] = True
                        self._note(now, "error", f"{s}: the {job['purpose']} order failed three times; close it by "
                                   "hand (`trades paper flatten`).", alert="error", symbol=s)
                    continue
            job["sent"] = job["cid"] if not tries else f"{job['cid']}-r{tries}"
            job["tries"] = tries + 1
            self._send(now, OrderRequest(s, _side(-q), abs(q), job["sent"], time_in_force=tif, purpose=job["purpose"]))
        for s, job in list(self.entering.items()):
            if abs(positions.get(s, 0.0)) > EPS:
                del self.entering[s]  # the previous entry filled before it could be cancelled
                continue
            if any(o.get("symbol") == s for o in self._working):
                continue  # wait for the cancel to go through
            fields = {k: job[k] for k in ("type", "stop_price", "stop_loss", "take_profit", "purpose")}
            order = OrderRequest(job["symbol"], job["side"], job["qty"], job["client_order_id"], **fields)
            self._send(now, order, bar_time=job["bar_time"], decision_close=job["decision_close"])
            del self.entering[s]

    def _finish(self, now: datetime, account: dict[str, Any], positions: dict[str, float]) -> None:
        equity, last = float(account["equity"]), float(account.get("last_equity") or account["equity"])
        self.records.record_fills(self.broker, self.rows, self.open - timedelta(days=7))
        held = {s: q for s, q in positions.items() if abs(q) > EPS}
        if held:
            self._note(now, "error", f"Still holding {held} after the close: check the account.", alert="error")
        self.records.record_equity(self.session, equity, last, len(held), now)
        self.records.reports_dir.mkdir(parents=True, exist_ok=True)
        (self.records.reports_dir / f"{self.session}.md").write_text(self.daily_report(equity, last, held))
        change = equity / last - 1 if last > 0 else 0.0
        self.alert("daily_report", f"{self.target.key} {self.session}: equity ${equity:,.2f} ({change:+.2%}), "
                   f"{len(self._today_rows())} orders, {'flat' if not held else f'HOLDING {held}'}.")
        self.phase, self.done = "done", True
        self._save(now)

    def _today_rows(self) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["session_date"] == self.session.isoformat()]

    def daily_report(self, equity: float, last: float, held: dict[str, float]) -> str:
        day = self.session.isoformat()
        rows = self._today_rows()
        events = [e for e in self.records.events() if e["time"][:10] == day]
        decisions = [d for d in self.records.decisions() if d.get("session") == day]
        change = equity / last - 1 if last > 0 else 0.0
        lines = [
            f"# {self.target.key}: paper trading, {day}",
            "",
            f"Equity ${last:,.2f} -> ${equity:,.2f} ({change:+.2%}). Decisions on {len(decisions)} bars. "
            + ("Flat at the close." if not held else f"STILL HOLDING {held}."),
            "",
            "| Sent (UTC) | Order | Purpose | Status | Fill |",
            "| --- | --- | --- | --- | --- |",
        ]
        for r in rows:
            level = f" at {r['stop_price']}" if r["stop_price"] else ""
            fill = f"{r['filled_qty']} @ {r['fill_price']}" if r["fill_price"] else "-"
            lines.append(f"| {r['run_time_utc'][11:16]} | {r['side']} {r['qty']} {r['symbol']} {r['type']}{level} "
                         f"({r['time_in_force']}) | {r['purpose']} | {r['status']} | {fill} |")
        if not rows:
            lines.append("| - | no orders | | | |")
        if events:
            lines += ["", "Events:", ""] + [f"- {e['time'][11:16]} {e['kind']}: {e['message']}" for e in events]
        lines += ["", "Hypothetical paper trading on a simulated account. Not investment advice.", ""]
        return "\n".join(lines)

    # -- sending, cancelling, noting ---------------------------------------------------------------
    def _cid(self, tag: Any, symbol: str, purpose: str) -> str:
        if isinstance(tag, (pd.Timestamp, datetime)):
            tag = pd.Timestamp(tag).tz_convert(NY).strftime("%H%M")
        return f"{self.target.key}-{self.session:%Y%m%d}-{tag}-{symbol}-{purpose}"

    def _send(self, now: datetime, order: OrderRequest, decision: Decision | None = None, *,
              bar_time: str = "", decision_close: Any = "") -> dict[str, Any] | None:
        if self.halt_check():  # the kill switch, checked before every order
            self.log(f"  Kill switch on: {self._describe(order)} not sent.")
            return None
        if not self.submit:  # the account never changes in a dry run, so say each intention once
            intention = f"{order.symbol}-{order.purpose}-{order.side}"
            if intention not in self._dry:
                self._dry.add(intention)
                self.log(f"  DRY RUN, not sent: {self._describe(order)}")
            return None
        existing = self.broker.order_by_client_id(order.client_order_id)
        if existing is None:
            try:
                existing = self.broker.submit(order)
            except PaperAPIError as exc:
                self._note(now, "rejection", f"Alpaca refused {self._describe(order)}: {exc}", alert="rejection",
                           symbol=order.symbol)
                return None
            self.log(f"  SENT {self._describe(order)}")
        if not any(r["client_order_id"] == order.client_order_id for r in self.rows):
            if decision is not None:
                bar_time, decision_close = _stamp(decision.time), round(decision.prices[order.symbol], 4)
            self.rows.append({
                **dict.fromkeys(ORDER_COLUMNS, ""),
                "run_time_utc": _stamp(now),
                "session_date": self.session.isoformat(),
                "trade_date": self.session.isoformat(),
                "bar_time": bar_time,
                "symbol": order.symbol,
                "side": order.side,
                "qty": f"{order.qty:g}",
                "type": order.type,
                "time_in_force": order.time_in_force,
                "stop_price": order.stop_price or "",
                "stop_loss": order.stop_loss or "",
                "take_profit": order.take_profit or "",
                "purpose": order.purpose,
                "client_order_id": order.client_order_id,
                "alpaca_order_id": existing.get("id", ""),
                "status": existing.get("status", ""),
                "decision_close": decision_close,
                "spec": self.target.key,
                "code_version": self.version,
            })
            self.records.write_orders(self.rows)
        return existing

    @staticmethod
    def _describe(o: OrderRequest) -> str:
        text = f"{o.side} {o.qty:g} {o.symbol} {o.type} ({o.time_in_force})"
        if o.stop_price:
            text += f" at {o.stop_price:g}"
        if o.stop_loss or o.take_profit:
            text += f", stop {o.stop_loss or '-'} / take {o.take_profit or '-'}"
        return f"{text} [{o.purpose}]"

    def _cancel(self, now: datetime, o: dict[str, Any]) -> None:
        oid = str(o.get("id", ""))
        last = self._cancelled.get(oid)
        if not oid or not self.submit or (last is not None and now - last < RECANCEL):
            return
        self._cancelled[oid] = now
        try:
            self.broker.cancel_order(oid)
        except PaperAPIError as exc:
            self._note(now, "error", f"Could not cancel order {oid} ({o.get('symbol')}): {exc}", alert="error")

    def _note(self, now: datetime, kind: str, message: str, alert: str | None = None, **extra: Any) -> None:
        self.log(f"  {kind.upper()}: {message}")
        self.records.log_event(now, kind, message, **extra)
        if alert:
            self.alert(alert, f"{self.target.key}: {message}")

    def _risk_events(self, now: datetime) -> None:
        for e in self.risk.events[self._events_seen:]:
            self._note(now, e.kind, e.message, alert="risk_limit" if e.kind in RISK_ALERTS else None, symbol=e.symbol)
        self._events_seen = len(self.risk.events)

    def _check_gap(self, now: datetime, newest: Any) -> None:
        """Alert when bars stop arriving during the session (once per gap)."""
        bar = timedelta(minutes=self.tf.minutes)
        due = self.open + bar if newest is None else pd.Timestamp(newest).to_pydatetime() + 2 * bar  # the next bar's end
        if now > due + bar and now < self.moc_at and self._gap_alerted != newest:
            self._gap_alerted = newest
            self._note(now, "data_gap", f"No new {self.tf.value} bar since {newest}: decisions wait until data arrives.",
                       alert="data_gap")

    def _heartbeat(self, now: datetime) -> None:
        beat = {"time": _stamp(now), "session": self.session.isoformat(), "phase": self.phase,
                "last_bar": _stamp(self.last_bar) if self.last_bar is not None else None, "pid": os.getpid()}
        self.records.folder.mkdir(parents=True, exist_ok=True)
        (self.records.folder / "heartbeat.json").write_text(json.dumps(beat) + "\n")
        if self._last_ping is None or now - self._last_ping >= timedelta(minutes=self.schedule.heartbeat_minutes):
            self._last_ping = now
            ping(os.environ.get("TRADES_HEARTBEAT_URL"), log=self.log)

    def _save(self, now: datetime) -> None:
        self.records.save_state({
            "updated": _stamp(now),
            "session": self.session.isoformat(),
            "phase": self.phase,
            "last_bar": _stamp(self.last_bar) if self.last_bar is not None else None,
            "risk": self.risk.state() if self._risk_ready else self._risk_state,
            "last_targets": self.last_targets,
            "latched": self.latched,
            "held": self.held,
            "closing": self.closing,
            "entering": self.entering,
            "seen_fills": sorted(self.seen_fills),
        })


def live_bars(target: PaperTarget, provider: str | None = None) -> Bars:
    """Completed bars from the spec's data provider, with enough history for the strategy's warm-up."""
    from trades.data.base import last_bar_forming, lookback_start
    from trades.data.service import DataService
    from trades.settings import SettingsStore

    data = DataService(SettingsStore())
    tf = Timeframe.parse(target.timeframe)
    warmup = target.strategy.build()[0].warmup()
    source = provider or target.providers[0]

    def bars(now: datetime) -> dict[str, pd.DataFrame]:
        data.invalidate()
        start = lookback_start(tf, warmup + 2 * tf.bars_per_day, now).date()
        frames, errors = data.bars_many(target.symbols, tf.value, start, None, source)
        if errors:
            raise RuntimeError(f"{source}: {errors}")
        return {s: df.iloc[:-1] if last_bar_forming(df, tf, now) else df for s, df in frames.items()}

    return bars
