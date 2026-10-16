"""The nightly paper trader, with Alpaca mocked by httpx.MockTransport: the backtest's order rule,
guardrails, idempotency, reconciliation, the records it keeps, and proof that nothing can reach a
non-paper URL."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from tests.helpers import synthetic_fetch, synthetic_target
from trades import runs, specs
from trades.data.synthetic import SyntheticProvider
from trades.engine.live import Decision
from trades.engine.paper import (
    Limits,
    PaperRecords,
    PlannedOrder,
    _levels_without_orders,
    client_order_id,
    guardrail_problems,
    in_submission_window,
    paper_allowed,
    plan_orders,
    run,
)
from trades.execution.alpaca import PAPER_URL, PaperAPIError, PaperClient
from trades.risk import RiskLimits

SESSION = date(2026, 9, 25)  # Friday
NEXT = date(2026, 9, 28)  # Monday, when the orders fill
EVENING = datetime(2026, 9, 26, 0, 30, tzinfo=timezone.utc)  # Friday 8:30pm New York time


class FakeAlpaca:
    """Just enough of Alpaca's paper API, with every request recorded."""

    def __init__(self, equity=100_000.0, last_equity=100_000.0, positions=None):
        self.equity, self.last_equity = equity, last_equity
        self.positions = dict(positions or {})  # symbol -> (qty, price)
        self.orders: dict[str, dict] = {}
        self.activity: list[dict] = []  # fills, as Alpaca's account activities list them
        self.urls: list[httpx.URL] = []
        self.posts = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(request.url)
        path, method = request.url.path, request.method
        if path == "/v2/account":
            return httpx.Response(200, json={"equity": str(self.equity), "last_equity": str(self.last_equity), "cash": "0"})
        if path == "/v2/positions" and method == "GET":
            body = [{"symbol": s, "qty": str(q), "market_value": str(q * p)} for s, (q, p) in self.positions.items()]
            return httpx.Response(200, json=body)
        if path == "/v2/positions" and method == "DELETE":
            closed = [{"symbol": s, "status": 200} for s in self.positions]
            self.positions.clear()
            return httpx.Response(207, json=closed)
        if path == "/v2/account/activities/FILL":
            after = request.url.params["after"]
            return httpx.Response(200, json=[a for a in self.activity if a["transaction_time"] > after])
        if path == "/v2/orders:by_client_order_id":
            o = self.orders.get(request.url.params["client_order_id"])
            return httpx.Response(200, json=o) if o else httpx.Response(404, json={"message": "not found"})
        if path == "/v2/orders" and method == "GET":
            return httpx.Response(200, json=[o for o in self.orders.values() if o["status"] in ("new", "accepted")])
        if path == "/v2/orders" and method == "DELETE":
            for o in self.orders.values():
                if o["status"] in ("new", "accepted"):
                    o["status"] = "canceled"
            return httpx.Response(207, json=[])
        if path == "/v2/orders" and method == "POST":
            body = json.loads(request.content)
            if body["client_order_id"] in self.orders:
                return httpx.Response(422, json={"message": "client_order_id must be unique"})
            self.posts += 1
            order = {**body, "id": f"o{self.posts}", "status": "accepted", "filled_qty": "0", "filled_avg_price": None}
            self.orders[body["client_order_id"]] = order
            return httpx.Response(200, json=order)
        return httpx.Response(404, json={"message": f"unknown {path}"})

    def client(self) -> PaperClient:
        return PaperClient("key", "secret", httpx.Client(transport=httpx.MockTransport(self.handler)))

    def fill_all(self, price_of, fraction=1.0, when="2026-09-28T13:30:00+00:00"):
        """Fill accepted orders (``fraction`` of each), updating positions like Alpaca would."""
        for o in self.orders.values():
            if o["status"] != "accepted":
                continue
            qty = float(o["qty"]) * fraction
            px = price_of(o["symbol"])
            self.activity.append({"id": f"f{len(self.activity) + 1}", "transaction_time": when, "symbol": o["symbol"],
                                  "side": o["side"], "qty": str(qty), "price": str(px), "order_id": o["id"]})
            # A partly filled day/auction order expires with the rest unfilled.
            o.update(status="filled" if fraction == 1 else "canceled", filled_qty=str(qty), filled_avg_price=str(px))
            held, _ = self.positions.get(o["symbol"], (0.0, px))
            self.positions[o["symbol"]] = (held + (qty if o["side"] == "buy" else -qty), px)


def _run(target, fake, tmp_path, *, now=EVENING, end=SESSION, submit=True, limits=RiskLimits(), **kw):
    lines: list[str] = []
    code = run(
        target, fake.client(), synthetic_fetch(end), limits, PaperRecords(target.key, root=tmp_path / "paper"),
        submit=submit, now=now, log=lines.append, sleep=lambda s: None, **kw,
    )
    return code, lines


# ---------------------------------------------------------------------------------- planning


def test_plan_orders_follows_the_backtest_rule():
    prices = {"A": 50.0, "B": 33.0, "C": 10.0}
    whole, remembered = plan_orders({"A": 0.2, "B": 0.1, "C": 0.0}, prices, 10_000, {"C": 5.0}, Limits())
    assert [(o.symbol, o.side, o.qty, o.time_in_force) for o in whole] == [
        ("A", "buy", 40, "opg"),  # 2,000 / 50
        ("B", "buy", 30, "opg"),  # 1,000 / 33 = 30.3 -> 30 whole shares
        ("C", "sell", 5, "opg"),  # target 0: close
    ]
    assert remembered == {"A": 0.2, "B": 0.1, "C": 0.0}
    frac = plan_orders({"B": 0.1}, prices, 10_000, {}, Limits(fractional=True))[0][0]
    assert frac.qty == pytest.approx(30.30303, abs=1e-5) and frac.time_in_force == "day"  # no fractional auction orders
    # A re-size under 0.5% of equity is skipped, as the backtest engine skips it.
    assert plan_orders({"A": 0.1}, prices, 20_000, {"A": 39.0}, Limits())[0] == []  # $50 < 0.5% of $20,000
    # Like the backtest, an unchanged target is not re-traded as the price drifts ...
    assert plan_orders({"A": 0.2}, {"A": 55.0}, 10_000, {"A": 40.0}, Limits(), {"A": 0.2}, {"A": 40.0})[0] == []
    # ... unless the last plan was not reached (half of it filled): then it is planned afresh.
    topup = plan_orders({"A": 0.2}, {"A": 50.0}, 10_000, {"A": 20.0}, Limits(), {"A": 0.2}, {"A": 40.0})[0]
    assert [(o.side, o.qty) for o in topup] == [("buy", 20)]
    # Alpaca has no fractional shorts, and a position is closed before it flips.
    short = plan_orders({"B": -0.1}, prices, 10_000, {}, Limits(fractional=True, allow_short=True))[0][0]
    assert short.qty == 30 and short.side == "sell"
    flip = plan_orders({"A": -0.2}, prices, 10_000, {"A": 40.0}, Limits(allow_short=True))[0][0]
    assert (flip.side, flip.qty, flip.target) == ("sell", 40, 0.0)


def test_guardrails():
    account = {"equity": "10000", "last_equity": "10000"}
    buy = PlannedOrder("A", "buy", 40, "opg", 0, 40, 50.0)
    ok = guardrail_problems([buy], {}, {}, {"A": 50.0}, account, ["A"], Limits())
    assert ok == []

    def problems(orders=(buy,), positions=None, account=account, watchlist=("A",), **limits):
        return guardrail_problems(list(orders), positions or {}, {}, {"A": 50.0, "B": 50.0}, account, list(watchlist), Limits(**limits))

    assert "kill switch" in problems(halted=True)[0]
    down = {"equity": "9600", "last_equity": "10000"}  # -4% today
    assert "daily loss" in problems(account=down)[0]
    sell = PlannedOrder("A", "sell", 20, "opg", 40, 20, 50.0)
    assert problems(orders=[sell], positions={"A": 40.0}, account=down) == []  # reducing exposure is allowed
    assert problems(account={"equity": "9900", "last_equity": "10000"}) == []  # -1% is within 1.5%
    assert "not in the spec's universe" in problems(watchlist=("B",))[0]
    assert "exceed the limit" in problems(orders=[buy] * 3, max_orders=2)[0]
    short = PlannedOrder("A", "sell", 10, "opg", 0, -10, 50.0)
    assert "short" in problems(orders=[short])[0]
    assert problems(orders=[short], allow_short=True) == []
    big = PlannedOrder("A", "buy", 300, "opg", 0, 300, 50.0)  # $15,000 on a $10,000 account
    assert "Gross exposure would be 150.0%" in problems(orders=[big])[0]
    # Over the cap already (prices drifted up): orders that reduce exposure are still allowed.
    trim = PlannedOrder("A", "sell", 10, "opg", 210, 200, 50.0)
    assert problems(orders=[trim], positions={"A": 210.0}) == []


def test_the_nightly_runner_refuses_levels_it_cannot_place():
    t = datetime(2026, 9, 25, tzinfo=timezone.utc)
    plain = Decision(t, {"A": 0.2}, {"A": 0.2}, {"A": 50.0}, {"A": None}, {"A": None}, {"A": None})
    assert _levels_without_orders(plain) == []
    stopped = Decision(t, {"A": 0.2}, {"A": 0.2}, {"A": 50.0}, {"A": 47.5}, {"A": None}, {"A": None})
    assert _levels_without_orders(stopped) == ["A"]


def test_submission_window_is_alpacas_opening_auction_window():
    assert in_submission_window(EVENING, SESSION)  # Friday 8:30pm ET
    assert in_submission_window(datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc), SESSION)  # Monday 9:00am ET
    assert not in_submission_window(datetime(2026, 9, 28, 13, 29, tzinfo=timezone.utc), SESSION)  # 9:29am ET
    assert not in_submission_window(datetime(2026, 9, 25, 22, 15, tzinfo=timezone.utc), SESSION)  # 6:15pm ET


def test_paper_needs_a_research_run_that_passed(monkeypatch):
    spec = specs.find("STRAT-002")
    monkeypatch.setattr(runs, "latest", lambda key: None)
    assert paper_allowed(spec) == (False, f"{spec.key} has no research run: `trades research STRAT-002` first.")
    monkeypatch.setattr(runs, "latest", lambda key: {"id": "r1", "verdict": "ready_for_locked_test"})
    assert not paper_allowed(spec)[0]
    monkeypatch.setattr(runs, "latest", lambda key: {"id": "r2", "verdict": "pass"})
    assert paper_allowed(spec) == (True, f"{spec.key} passed the acceptance checklist in run r2.")


# ---------------------------------------------------------------------------- full runs


def test_dry_run_sends_and_records_nothing(tmp_path):
    fake = FakeAlpaca()
    code, lines = _run(synthetic_target(), fake, tmp_path, submit=False)
    assert code == 0 and fake.posts == 0 and any("Dry run" in line for line in lines)
    assert not (tmp_path / "paper").exists()


def test_submit_needs_a_validated_spec(tmp_path):
    fake = FakeAlpaca()
    code, lines = _run(synthetic_target(), fake, tmp_path, validated=False)
    assert code == 1 and fake.posts == 0
    assert any(line.startswith("UNVALIDATED") for line in lines) and "acceptance checklist" in lines[-1]


def test_submit_is_idempotent_and_recorded(tmp_path):
    target, fake = synthetic_target(), FakeAlpaca()
    code, _ = _run(target, fake, tmp_path)
    assert code == 0 and fake.posts > 0
    records = PaperRecords(target.key, root=tmp_path / "paper")
    rows = records.read_orders()
    assert len(rows) == fake.posts
    for r in rows:
        assert r["client_order_id"] == client_order_id(SESSION, r["symbol"], target.key) and r["spec"] == target.key
        assert r["trade_date"] == "2026-09-28" and r["time_in_force"] == "opg" and r["status"] == "accepted"
    posted = {o["client_order_id"]: o for o in fake.orders.values()}
    assert all(o["type"] == "market" and o["time_in_force"] == "opg" for o in posted.values())
    # What the next run needs, and what tracking reads: the decision, the equity, the state.
    assert [d["session"] for d in records.decisions()] == ["2026-09-25"]
    assert records.equity()["equity"].tolist() == [100_000.0]
    state = records.load_state()
    assert set(state["last_targets"]) == set(target.symbols) and state["risk"]["session"] == "2026-09-25"
    # Run again: the same client order ids are found on Alpaca, so nothing is sent twice or logged twice.
    before = fake.posts
    code, _ = _run(target, fake, tmp_path)
    assert code == 0 and fake.posts == before and len(records.read_orders()) == len(rows)


def test_alpaca_rejecting_a_duplicate_is_not_a_crash(tmp_path):
    target, fake = synthetic_target(), FakeAlpaca()
    _run(target, fake, tmp_path)
    records = PaperRecords(target.key, root=tmp_path / "paper")
    records.orders_path.unlink()  # the log is lost, and Alpaca's lookup is down: POST again
    real = fake.handler
    fake.handler = lambda r: (
        httpx.Response(404, json={}) if r.url.path == "/v2/orders:by_client_order_id" else real(r)
    )
    before = fake.posts
    code, lines = _run(target, fake, tmp_path)
    assert fake.posts == before and any("refused" in line for line in lines)
    assert {e["kind"] for e in records.events()} == {"rejection"}


def test_reconciliation_uses_actual_fills_and_positions(tmp_path):
    target, fake = synthetic_target(), FakeAlpaca()
    _run(target, fake, tmp_path)
    opens = {s: float(SyntheticProvider(seed=7, end=NEXT).history(s)["open"].iloc[-1]) for s in target.symbols}
    # Monday's open: buys fill 10 bps above it, but only half of each order (the rest expires).
    fake.fill_all(lambda s: opens[s] * 1.0010, fraction=0.5)
    monday_evening = datetime(2026, 9, 29, 0, 30, tzinfo=timezone.utc)
    code, lines = _run(target, fake, tmp_path, now=monday_evening, end=NEXT)
    assert code == 0
    rows = {r["client_order_id"]: r for r in PaperRecords(target.key, root=tmp_path / "paper").read_orders()}
    friday = [r for r in rows.values() if r["session_date"] == "2026-09-25"]
    for r in friday:
        assert r["status"] == "canceled" and float(r["fill_price"]) == pytest.approx(opens[r["symbol"]] * 1.001)
        assert float(r["open_price"]) == pytest.approx(opens[r["symbol"]], abs=1e-4)
        assert float(r["assumed_price"]) == pytest.approx(opens[r["symbol"]] * 1.0005, abs=1e-4)  # 5 bps
        assert float(r["slippage_bps"]) == pytest.approx(10.0, abs=0.05)
    # Never assume earlier orders filled: Monday's orders top up the half that did not.
    monday = [r for r in rows.values() if r["session_date"] == "2026-09-28"]
    assert monday and all(r["side"] == "buy" for r in monday)
    # The broker's own record of each fill, tied to the order that caused it.
    fills = PaperRecords(target.key, root=tmp_path / "paper").read_fills()
    assert sorted(f["client_order_id"] for f in fills) == sorted(r["client_order_id"] for r in friday)
    assert {f["purpose"] for f in fills} == {"rebalance"} and fills[0]["time_utc"] == "2026-09-28T13:30:00Z"


def test_risk_limits_and_guardrails_stop_what_they_should(tmp_path):
    target = synthetic_target()
    # Down 2.5% today: the risk engine flattens (nothing is held, so nothing to sell) and buys nothing.
    fake = FakeAlpaca(equity=97_500, last_equity=100_000)
    code, lines = _run(target, fake, tmp_path)
    assert code == 0 and fake.posts == 0 and any("RISK: Down 2.50% today" in line for line in lines)
    # Something outside the spec already fills the account: buying more would breach the gross cap.
    fake = FakeAlpaca(positions={"XYZ": (950, 100.0)})
    code, lines = _run(target, fake, tmp_path / "2")
    assert code == 1 and fake.posts == 0 and any("Gross exposure would be" in line for line in lines)
    # Monday noon: the opening-auction window is closed.
    fake = FakeAlpaca()
    midday = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)
    code, lines = _run(target, fake, tmp_path / "3", now=midday, end=SESSION)
    assert code == 1 and fake.posts == 0 and any("9:28am ET" in line for line in lines)


def test_kill_switch_stops_a_run_before_any_order(tmp_path):
    target, fake = synthetic_target(), FakeAlpaca()
    code, lines = _run(target, fake, tmp_path, halt_check=lambda: True)
    assert code == 1 and fake.posts == 0 and any("kill switch is on" in line for line in lines)
    calls = []

    def flips_after_the_decision():  # off when the run starts, on before the first order
        calls.append(1)
        return len(calls) > 1

    code, lines = _run(target, fake, tmp_path, halt_check=flips_after_the_decision)
    assert fake.posts == 0 and "Kill switch turned on during the run: stopping." in lines
    assert [e["kind"] for e in PaperRecords(target.key, root=tmp_path / "paper").events()] == ["halt"]


# ---------------------------------------------------------------- no path to a non-paper URL


def test_every_request_goes_to_the_paper_endpoint(tmp_path):
    target, fake = synthetic_target(), FakeAlpaca()
    _run(target, fake, tmp_path, submit=False)
    _run(target, fake, tmp_path)
    client = fake.client()
    client.cancel_all_orders()
    client.close_all_positions()
    assert fake.urls and {u.host for u in fake.urls} == {"paper-api.alpaca.markets"}
    assert {u.scheme for u in fake.urls} == {"https"} and PAPER_URL == "https://paper-api.alpaca.markets"


def test_client_refuses_other_hosts_and_redirects():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(301, headers={"location": "https://api.alpaca.markets/v2/account"})

    client = PaperClient("k", "s", httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PaperAPIError):
        client.account()  # the redirect to the live endpoint is not followed
    assert seen == ["https://paper-api.alpaca.markets/v2/account"]
    for path in ("@api.alpaca.markets/v2/account", "/../v2/account", "//api.alpaca.markets/v2/orders"):
        with pytest.raises(PaperAPIError, match="only the paper endpoint"):
            client._request("GET", path)
    # A client configured with another base URL changes nothing: requests use absolute paper URLs.
    other = httpx.Client(base_url="https://api.alpaca.markets", transport=httpx.MockTransport(handler))
    seen.clear()
    with pytest.raises(PaperAPIError):
        PaperClient("k", "s", other).account()
    assert seen == ["https://paper-api.alpaca.markets/v2/account"]


def test_no_live_trading_url_in_the_code():
    """The only Alpaca hosts in the package are the paper trading API and the market-data API."""
    root = Path(__file__).resolve().parents[2] / "src" / "trades"
    files = list(root.rglob("*.py"))
    assert len(files) > 50, f"found only {len(files)} files under {root}: this test must never pass vacuously"
    for path in files:
        text = path.read_text()
        for host in ("://api.alpaca.markets", "broker-api.alpaca.markets", "://alpaca.markets/v2"):
            assert host not in text, f"{path} mentions {host}"
