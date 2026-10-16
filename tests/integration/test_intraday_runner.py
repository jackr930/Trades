"""The intraday paper runner over whole synthetic sessions, against an in-memory Alpaca paper account
(tests/fakes.py): the decisions match the backtest's bar for bar, the orders follow the backtest's
rule, and the kill switch, loss limits, restarts, refusals and data gaps do what they promise."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx
import pandas as pd
import pytest

from tests.fakes import BAR, SimAlpaca
from tests.helpers import ny_time as at
from tests.helpers import orb_target as orb
from trades.data.calendar import NY
from trades.engine.backtest import backtest_strategy
from trades.engine.intraday import IntradayRunner
from trades.engine.paper import PaperRecords, PaperTarget
from trades.reporting.alerts import Alerts
from trades.risk import RiskLimits

HOLDS = date(2026, 8, 26)  # the backtest buys SIMTEC at 09:35 and holds it into the closing auction
STOPPED = date(2026, 7, 23)  # the backtest buys at 09:35 and is stopped out at 10:20


class Day:
    """One session: the fake market, the runner, and what it logged and alerted."""

    def __init__(self, intraday, tmp_path, day, target=None, *, broker=None, bars_until=None, **kw):
        self.day, self.target = day, target or orb()
        self.bars = {"SIMTEC": intraday["SIMTEC"]}
        self.broker = broker or SimAlpaca(self.bars)
        self.broker.begin(day)
        self.records = PaperRecords(self.target.key, root=tmp_path / "paper")
        self.lines: list[str] = []
        self.alerts: list[tuple[str, str]] = []
        self.halted = False
        self.bars_until = bars_until  # a data feed that stops here
        self.kw = {"submit": True, **kw}
        self.runner = self.new_runner()

    def new_runner(self) -> IntradayRunner:
        return IntradayRunner(
            self.target, self.broker, self.completed, RiskLimits(), self.records, session=self.day,
            halt_check=lambda: self.halted, alert=lambda kind, message: self.alerts.append((kind, message)),
            log=self.lines.append, **self.kw,
        )

    def completed(self, now: datetime) -> dict[str, pd.DataFrame]:
        cut = pd.Timestamp(now) - BAR
        if self.bars_until is not None:
            cut = min(cut, pd.Timestamp(self.bars_until))
        return {s: df[(df.index <= cut) & (df.index > cut - pd.Timedelta(days=40))] for s, df in self.bars.items()}

    def run(self, start: str = "09:20", until: str = "16:10", every: timedelta = timedelta(minutes=1), events=None):
        t, end = at(self.day, start), at(self.day, until)
        pending = dict(events or {})
        while t <= end:
            self.broker.advance(t)
            for when in [w for w in pending if t >= at(self.day, w, 0)]:
                pending.pop(when)(self)
            if not self.runner.step(t):
                return
            t += every

    def orders(self) -> list[dict]:
        return self.records.read_orders()

    def purposes(self) -> list[str]:
        return [r["purpose"] for r in self.orders()]


def backtest(intraday, target: PaperTarget, day: date):
    strat, sizing = target.strategy.build()
    df = intraday["SIMTEC"]
    df = df[df.index.tz_convert(NY).date <= day]
    return backtest_strategy(strat, {"SIMTEC": df}, sizing, target.engine, risk=RiskLimits(), with_benchmark=False).result


# ------------------------------------------------------------------------------- a normal day


def test_a_paper_day_decides_exactly_like_the_backtest(intraday, tmp_path):
    d = Day(intraday, tmp_path, HOLDS)
    d.run()
    assert d.runner.done and d.broker.positions() == {}
    # The orders: a market entry with its bracket at 09:35, and the closing auction.
    entry, close = d.orders()
    assert (entry["purpose"], entry["type"], entry["status"]) == ("entry", "market", "filled")
    assert entry["stop_loss"] and entry["take_profit"] and entry["bar_time"] == "2026-08-26T13:30:00Z"
    assert (close["purpose"], close["time_in_force"], close["status"]) == ("close", "cls", "filled")
    # The same decisions as the backtest, bar for bar (the strategy, sizing and risk engine are shared).
    replay = backtest(intraday, d.target, HOLDS)
    decisions = d.records.decisions()
    assert len(decisions) == 74  # every bar from 09:30 to 15:35; the closing auction takes over at 15:45
    for dec in decisions:
        bar = pd.Timestamp(dec["time"])
        assert dec["raw"]["SIMTEC"] == pytest.approx(replay.targets.loc[bar, "SIMTEC"], abs=1e-12)
        assert dec["targets"]["SIMTEC"] == pytest.approx(replay.approved.loc[bar, "SIMTEC"], rel=1e-9, abs=1e-12)
    # The entry filled where the backtest's did, before its modelled costs.
    sim = [f for f in replay.fills if pd.Timestamp(f.time).tz_convert(NY).date() == HOLDS]
    assert float(entry["fill_price"]) == pytest.approx(sim[0].price / (1 + 3e-4))
    # The day's records.
    report = (d.records.reports_dir / "2026-08-26.md").read_text()
    assert "Flat at the close." in report and "Decisions on 74 bars" in report
    assert d.records.equity().index[-1] == pd.Timestamp("2026-08-26")
    assert (d.records.folder / "heartbeat.json").exists()
    assert [k for k, _ in d.alerts] == ["daily_report"]


def test_a_protective_stop_closes_the_trade_and_latches_the_symbol(intraday, tmp_path):
    d = Day(intraday, tmp_path, STOPPED)
    d.run()
    assert d.purposes() == ["entry"]  # the broker's stop leg closed it: no exit or close order from us
    legs = [o for o in d.broker.orders.values() if o["parent"]]
    assert {(o["type"], o["status"]) for o in legs} == {("stop", "filled"), ("limit", "canceled")}
    assert [e["kind"] for e in d.records.events()].count("protective_exit") == 1
    assert d.broker.positions() == {}
    # The fill record has both sides of the trade, the stop leg included (it is not in our order log).
    assert [(f["side"], f["purpose"]) for f in d.records.read_fills()] == [("buy", "entry"), ("sell", "protective")]


def test_breakout_entries_wait_at_the_level_without_churning(intraday, tmp_path):
    d = Day(intraday, tmp_path, HOLDS, orb("breakout"))
    d.run()
    entry, close = d.orders()
    assert (entry["type"], entry["status"], close["purpose"]) == ("stop", "filled", "close")
    assert float(entry["fill_price"]) == pytest.approx(float(entry["stop_price"]))  # filled at the level
    cancelled = [d.broker.orders[i] for i in d.broker.cancels]
    assert all(o["parent"] for o in cancelled)  # the same stop entry stayed working from bar to bar ...
    assert {o["type"] for o in cancelled} == {"stop", "limit"}  # ... and its legs made way for the closing auction
    assert d.broker.positions() == {}


# ----------------------------------------------------------------------------- the safety net


def halt(day: Day) -> None:
    day.halted = True


def test_the_kill_switch_cancels_working_entries_and_sends_nothing(intraday, tmp_path):
    d = Day(intraday, tmp_path, HOLDS, orb("breakout"))
    d.run(events={"09:37": halt})  # the stop entry is working, the breakout comes at 09:40
    assert d.purposes() == ["entry"] and d.orders()[0]["status"] == "canceled"
    assert d.broker.positions() == {} and [o["status"] for o in d.broker.orders.values()] == ["canceled"] * 3
    assert ("halt" in [k for k, _ in d.alerts]) and any("kill switch is on" in m for _, m in d.alerts)


def test_the_kill_switch_leaves_a_position_with_its_protection(intraday, tmp_path):
    d = Day(intraday, tmp_path, HOLDS)
    d.run(events={"10:00": halt})
    assert d.purposes() == ["entry"]  # no closing auction order: nothing is sent while halted
    assert d.broker.positions()["SIMTEC"]["qty"] > 0
    assert [k for k, _ in d.alerts] == ["halt", "error", "daily_report"]  # "still holding" after the close


def test_the_daily_loss_limit_stops_the_day(intraday, tmp_path):
    broker = SimAlpaca({"SIMTEC": intraday["SIMTEC"]}, cash=100_000.0, last_equity=102_000.0)  # down 2% already
    d = Day(intraday, tmp_path, HOLDS, broker=broker)
    d.run()
    assert d.orders() == [] and ("risk_limit" in [k for k, _ in d.alerts])
    assert [e["kind"] for e in d.records.events()].count("daily_loss") == 1


def test_a_restart_mid_session_picks_up_where_it_stopped(intraday, tmp_path):
    d = Day(intraday, tmp_path, HOLDS)
    d.run(until="11:00")
    d.runner = d.new_runner()  # the process died and systemd started a new one
    d.run(start="11:03")
    assert d.purposes() == ["entry", "close"] and d.broker.positions() == {}
    assert len({o["client_order_id"] for o in d.broker.orders.values() if not o["parent"]}) == 2


def test_positions_carried_overnight_are_closed_at_the_open(intraday, tmp_path):
    broker = SimAlpaca({"SIMTEC": intraday["SIMTEC"]})
    broker.qty["SIMTEC"] = 2.0
    broker.cash -= 2 * 5300.0
    d = Day(intraday, tmp_path, HOLDS, broker=broker)
    d.run()
    first = d.orders()[0]
    assert first["purpose"] == "exit" and "overnight" in first["client_order_id"] and first["status"] == "filled"
    assert d.alerts[0][0] == "error" and "overnight" in d.alerts[0][1]
    assert d.purposes()[1:] == ["entry", "close"] and d.broker.positions() == {}


def test_a_refused_closing_order_is_flattened_before_the_close(intraday, tmp_path):
    broker = SimAlpaca({"SIMTEC": intraday["SIMTEC"]})
    broker.refuse = {"cls"}
    d = Day(intraday, tmp_path, HOLDS, broker=broker)
    d.run()
    assert d.purposes() == ["entry", "flatten"] and d.broker.positions() == {}
    kinds = [k for k, _ in d.alerts]
    assert kinds.count("rejection") == 3 and "error" in kinds  # three attempts, then it gives up on the auction


def test_a_data_gap_is_reported_once(intraday, tmp_path):
    d = Day(intraday, tmp_path, HOLDS, bars_until=pd.Timestamp("2026-08-26 15:00", tz="UTC"))  # 11:00 ET
    d.run(until="12:00")
    assert [k for k, _ in d.alerts] == ["data_gap"]


def test_a_dry_run_decides_but_sends_nothing(intraday, tmp_path):
    d = Day(intraday, tmp_path, HOLDS, submit=False)
    d.run()
    assert d.broker.orders == {} and d.orders() == [] and len(d.records.decisions()) == 74
    assert sum("DRY RUN, not sent" in line for line in d.lines) == 1


def test_an_unvalidated_spec_cannot_send(intraday, tmp_path):
    d = Day(intraday, tmp_path, HOLDS, validated=False)
    d.run(until="10:00")
    assert d.broker.orders == {} and not d.runner.submit


# ------------------------------------------------------------------------------------- alerts


def test_alerts_go_to_telegram_only_when_configured_and_not_too_often():
    posted = []

    def post(url, **kw):
        posted.append((url, kw["json"]["text"]))
        return httpx.Response(200)

    off = Alerts(telegram=True, token=None, chat_id="1", post=post, log=lambda m: None)
    assert not off("error", "no token, so only logged") and posted == []
    alerts = Alerts(kinds=("error", "halt"), telegram=True, token="T", chat_id="42", post=post, log=lambda m: None)
    t0 = datetime(2026, 8, 26, 14, 0)
    assert alerts("error", "first", t0) and not alerts("error", "again soon", t0 + timedelta(minutes=2))
    assert alerts("error", "later", t0 + timedelta(minutes=11))
    assert not alerts("data_gap", "a kind that is switched off", t0)
    assert alerts("halt", "always", t0) and alerts("halt", "always", t0)
    assert posted[0] == ("https://api.telegram.org/botT/sendMessage", "ERROR: first") and len(posted) == 4

    def broken(url, **kw):
        raise httpx.ConnectError("offline")

    assert not Alerts(telegram=True, token="T", chat_id="1", post=broken, log=lambda m: None)("error", "x")
