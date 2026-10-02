"""Paper trading: Alpaca mocked with httpx.MockTransport. Guardrails, idempotency, reconciliation,
and proof that nothing can reach a non-paper URL."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from tests.test_journal import _experiment, _synthetic_fetch
from trades.data.synthetic import SyntheticProvider
from trades.paper.alpaca import PAPER_URL, PaperAPIError, PaperClient
from trades.paper.trader import (
    Limits,
    PlannedOrder,
    client_order_id,
    guardrail_problems,
    halted,
    in_submission_window,
    plan_orders,
    read_log,
    run,
)

SESSION = date(2026, 9, 25)  # Friday
NEXT = date(2026, 9, 28)  # Monday, when the orders fill
EVENING = datetime(2026, 9, 26, 0, 30, tzinfo=timezone.utc)  # Friday 8:30pm New York time


class FakeAlpaca:
    """Just enough of Alpaca's paper API, with every request recorded."""

    def __init__(self, equity=100_000.0, last_equity=100_000.0, positions=None):
        self.equity, self.last_equity = equity, last_equity
        self.positions = dict(positions or {})  # symbol -> (qty, price)
        self.orders: dict[str, dict] = {}
        self.urls: list[httpx.URL] = []
        self.posts = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(request.url)
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(200, json={"equity": str(self.equity), "last_equity": str(self.last_equity), "cash": "0"})
        if path == "/v2/positions":
            body = [{"symbol": s, "qty": str(q), "market_value": str(q * p)} for s, (q, p) in self.positions.items()]
            return httpx.Response(200, json=body)
        if path == "/v2/orders:by_client_order_id":
            o = self.orders.get(request.url.params["client_order_id"])
            return httpx.Response(200, json=o) if o else httpx.Response(404, json={"message": "not found"})
        if path == "/v2/orders" and request.method == "GET":
            return httpx.Response(200, json=[o for o in self.orders.values() if o["status"] in ("new", "accepted")])
        if path == "/v2/orders" and request.method == "POST":
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

    def fill_all(self, price_of, fraction=1.0):
        """Fill accepted orders (``fraction`` of each), updating positions like Alpaca would."""
        for o in self.orders.values():
            if o["status"] != "accepted":
                continue
            qty = float(o["qty"]) * fraction
            px = price_of(o["symbol"])
            # A partly filled day/auction order expires with the rest unfilled.
            o.update(status="filled" if fraction == 1 else "canceled", filled_qty=str(qty), filled_avg_price=str(px))
            held, _ = self.positions.get(o["symbol"], (0.0, px))
            self.positions[o["symbol"]] = (held + (qty if o["side"] == "buy" else -qty), px)


def _run(exp, fake, tmp_path, *, now=EVENING, end=SESSION, submit=True, limits=Limits(), **kw):
    lines = []
    code = run(
        exp, fake.client(), _synthetic_fetch(end), limits, submit=submit, now=now,
        orders_path=tmp_path / "orders.csv", log=lines.append, sleep=lambda s: None, **kw,
    )
    return code, lines


# ---------------------------------------------------------------------------------- planning


def test_plan_orders_rounds_like_the_engine():
    prices = {"A": 50.0, "B": 33.0, "C": 10.0}
    whole = plan_orders({"A": 0.2, "B": 0.1, "C": 0.0}, prices, 10_000, {"C": 5.0}, Limits())
    assert [(o.symbol, o.side, o.qty, o.time_in_force) for o in whole] == [
        ("A", "buy", 40, "opg"),  # 2,000 / 50
        ("B", "buy", 30, "opg"),  # 1,000 / 33 = 30.3 -> 30 whole shares
        ("C", "sell", 5, "opg"),  # target 0: close
    ]
    frac = plan_orders({"B": 0.1}, prices, 10_000, {}, Limits(fractional=True))[0]
    assert frac.qty == pytest.approx(30.30303, abs=1e-5) and frac.time_in_force == "day"  # no fractional auction orders
    # A re-size under 0.5% of equity is skipped, as the backtest engine skips it.
    assert plan_orders({"A": 0.1}, prices, 20_000, {"A": 39.0}, Limits()) == []  # $50 < 0.5% of $20,000
    # Alpaca has no fractional shorts, and a position is closed before it flips.
    short = plan_orders({"B": -0.1}, prices, 10_000, {}, Limits(fractional=True, allow_short=True))[0]
    assert short.qty == 30 and short.side == "sell"
    flip = plan_orders({"A": -0.2}, prices, 10_000, {"A": 40.0}, Limits(allow_short=True))[0]
    assert (flip.side, flip.qty, flip.target) == ("sell", 40, 0.0)


def test_guardrails():
    account = {"equity": "10000", "last_equity": "10000"}
    buy = PlannedOrder("A", "buy", 40, "opg", 0, 40, 50.0)
    ok = guardrail_problems([buy], {}, {}, {"A": 50.0}, account, ["A"], Limits())
    assert ok == []

    def problems(orders=(buy,), positions=None, account=account, watchlist=("A",), **limits):
        return guardrail_problems(list(orders), positions or {}, {}, {"A": 50.0, "B": 50.0}, account, list(watchlist), Limits(**limits))

    assert "kill switch" in problems(halted=True)[0]
    assert "daily loss" in problems(account={"equity": "9600", "last_equity": "10000"})[0]  # -4% today
    assert problems(account={"equity": "9800", "last_equity": "10000"}) == []  # -2% is within 3%
    assert "not in the experiment's watchlist" in problems(watchlist=("B",))[0]
    assert "exceed the limit" in problems(orders=[buy] * 3, max_orders=2)[0]
    short = PlannedOrder("A", "sell", 10, "opg", 0, -10, 50.0)
    assert "short" in problems(orders=[short])[0]
    assert problems(orders=[short], allow_short=True) == []
    big = PlannedOrder("A", "buy", 300, "opg", 0, 300, 50.0)  # $15,000 on a $10,000 account
    assert "Gross exposure would be 150.0%" in problems(orders=[big])[0]
    # Over the cap already (prices drifted up): orders that reduce exposure are still allowed.
    trim = PlannedOrder("A", "sell", 10, "opg", 210, 200, 50.0)
    assert problems(orders=[trim], positions={"A": 210.0}) == []


def test_submission_window_is_alpacas_opening_auction_window():
    assert in_submission_window(EVENING, SESSION)  # Friday 8:30pm ET
    assert in_submission_window(datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc), SESSION)  # Monday 9:00am ET
    assert not in_submission_window(datetime(2026, 9, 28, 13, 29, tzinfo=timezone.utc), SESSION)  # 9:29am ET
    assert not in_submission_window(datetime(2026, 9, 25, 22, 15, tzinfo=timezone.utc), SESSION)  # 6:15pm ET


# ---------------------------------------------------------------------------- full runs


def test_dry_run_sends_nothing(tmp_path):
    fake = FakeAlpaca()
    code, lines = _run(_experiment(tmp_path), fake, tmp_path, submit=False)
    assert code == 0 and fake.posts == 0 and any("Dry run" in line for line in lines)
    assert not (tmp_path / "orders.csv").exists()


def test_submit_is_idempotent_and_logged(tmp_path):
    exp, fake = _experiment(tmp_path), FakeAlpaca()
    code, _ = _run(exp, fake, tmp_path)
    assert code == 0 and fake.posts > 0
    rows = read_log(tmp_path / "orders.csv")
    assert len(rows) == fake.posts
    for r in rows:
        assert r["client_order_id"] == client_order_id(SESSION, r["symbol"], exp.id)
        assert r["trade_date"] == "2026-09-28" and r["time_in_force"] == "opg" and r["status"] == "accepted"
    posted = {o["client_order_id"]: o for o in fake.orders.values()}
    assert all(o["type"] == "market" and o["time_in_force"] == "opg" for o in posted.values())
    # Run again: the same client order ids are found on Alpaca, so nothing is sent twice or logged twice.
    before = fake.posts
    code, _ = _run(exp, fake, tmp_path)
    assert code == 0 and fake.posts == before and len(read_log(tmp_path / "orders.csv")) == len(rows)


def test_alpaca_rejecting_a_duplicate_is_not_a_crash(tmp_path):
    exp, fake = _experiment(tmp_path), FakeAlpaca()
    _run(exp, fake, tmp_path)
    (tmp_path / "orders.csv").unlink()  # the log is lost, and Alpaca's lookup is down: POST again
    real = fake.handler
    fake.handler = lambda r: (
        httpx.Response(404, json={}) if r.url.path == "/v2/orders:by_client_order_id" else real(r)
    )
    before = fake.posts
    code, lines = _run(exp, fake, tmp_path)
    assert fake.posts == before and any("refused" in line for line in lines)


def test_reconciliation_uses_actual_fills_and_positions(tmp_path):
    exp, fake = _experiment(tmp_path), FakeAlpaca()
    _run(exp, fake, tmp_path)
    opens = {s: float(SyntheticProvider(seed=7, end=NEXT).history(s)["open"].iloc[-1]) for s in exp.watchlist}
    # Monday's open: buys fill 10 bps above it, but only half of each order (the rest expires).
    fake.fill_all(lambda s: opens[s] * 1.0010, fraction=0.5)
    monday_evening = datetime(2026, 9, 29, 0, 30, tzinfo=timezone.utc)
    code, lines = _run(exp, fake, tmp_path, now=monday_evening, end=NEXT)
    assert code == 0
    rows = {r["client_order_id"]: r for r in read_log(tmp_path / "orders.csv")}
    friday = [r for r in rows.values() if r["session_date"] == "2026-09-25"]
    for r in friday:
        assert r["status"] == "canceled" and float(r["fill_price"]) == pytest.approx(opens[r["symbol"]] * 1.001)
        assert float(r["open_price"]) == pytest.approx(opens[r["symbol"]], abs=1e-4)
        assert float(r["assumed_price"]) == pytest.approx(opens[r["symbol"]] * 1.0005, abs=1e-4)  # 5 bps
        assert float(r["slippage_bps"]) == pytest.approx(10.0, abs=0.05)
    # Never assume earlier orders filled: Monday's orders top up the half that did not.
    monday = [r for r in rows.values() if r["session_date"] == "2026-09-28"]
    assert monday and all(r["side"] == "buy" for r in monday)


def test_guardrail_failure_and_closed_window_send_nothing(tmp_path):
    exp = _experiment(tmp_path)
    fake = FakeAlpaca(equity=95_000, last_equity=100_000)  # down 5% today
    code, lines = _run(exp, fake, tmp_path)
    assert code == 1 and fake.posts == 0 and any("daily loss" in line for line in lines)
    fake = FakeAlpaca()
    midday = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)  # Monday noon: the auction window is closed
    code, lines = _run(exp, fake, tmp_path, now=midday, end=SESSION)
    assert code == 1 and fake.posts == 0 and any("9:28am ET" in line for line in lines)


def test_kill_switch_stops_a_run_before_any_order(tmp_path):
    fake = FakeAlpaca()
    code, lines = _run(_experiment(tmp_path), fake, tmp_path, limits=Limits(halted=True))
    assert code == 1 and fake.posts == 0
    code, lines = _run(_experiment(tmp_path), fake, tmp_path, halt_check=lambda: True)  # flipped mid-run
    assert fake.posts == 0 and any("Kill switch" in line for line in lines)
    flag = tmp_path / "PAPER_HALTED"
    assert not halted(False, flag)
    flag.write_text("stop")
    assert halted(False, flag) and halted(True, tmp_path / "missing")


# ---------------------------------------------------------------- no path to a non-paper URL


def test_every_request_goes_to_the_paper_endpoint(tmp_path):
    exp, fake = _experiment(tmp_path), FakeAlpaca()
    _run(exp, fake, tmp_path, submit=False)
    _run(exp, fake, tmp_path)
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
    root = Path(__file__).resolve().parents[1] / "trades"
    for path in root.rglob("*.py"):
        text = path.read_text()
        for host in ("://api.alpaca.markets", "broker-api.alpaca.markets", "://alpaca.markets/v2"):
            assert host not in text, f"{path} mentions {host}"
