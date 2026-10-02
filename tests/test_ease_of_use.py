"""Broker CSV import, the one-click comparison with SPY, and the first-run settings."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from trades import holdings
from trades.api import create_app

SCHWAB = '''"Positions for account Roth IRA ...123 as of 09:15 PM ET, 2026/09/25","","","","","",""
"Symbol","Description","Qty (Quantity)","Price","Mkt Val (Market Value)","Cost Basis","Security Type"
"AAPL","APPLE INC","10","$185.64","$1,856.40","$1,500.00","Equity"
"VTI","VANGUARD TOTAL STOCK MARKET ETF","5.5","$250.00","$1,375.00","$1,450.00","ETFs & Closed End Funds"
"Cash & Cash Investments","--","--","--","$1,234.56","--","Cash and Money Market"
"Account Total","--","--","--","$4,465.96","$2,950.00","--"
'''

FIDELITY = '''﻿Account Number,Account Name,Symbol,Description,Quantity,Last Price,Last Price Change,Current Value,Cost Basis Total,Average Cost Basis,Type
Z12345678,Individual,SPAXX**,HELD IN MONEY MARKET,,,,$500.25,,,Cash,
Z12345678,Individual,MSFT,MICROSOFT CORP,4,$400.00,+$1.00,"$1,600.00","$1,200.00",$300.00,Cash,
Z12345678,Individual,Pending Activity,,,,,$-20.00,,,,
X87654321,ROTH IRA,FXAIX,FIDELITY 500 INDEX FUND,10.123,$180.00,-$0.50,"$1,822.14","$1,700.00",$167.93,Cash,

"The data and information in this spreadsheet is provided to you solely for your use and is not for distribution."
"Date downloaded 09/25/2026 10:00 PM ET"
'''

VANGUARD = '''Account Number,Investment Name,Symbol,Shares,Share Price,Total Value,
12345678,VANGUARD TOTAL STOCK MARKET INDEX ADMIRAL,VTSAX,100.5,110.00,11055.00,
12345678,VANGUARD FEDERAL MONEY MARKET FUND,VMFXX,500,1.00,500.00,


Account Number,Trade Date,Settlement Date,Transaction Type,Transaction Description,Investment Name,Symbol,Shares,Share Price,Principal Amount,Commission Fees,Net Amount,Accrued Interest,Account Type,
12345678,2026-09-01,2026-09-02,Buy,Buy,VANGUARD TOTAL STOCK MARKET INDEX ADMIRAL,VTSAX,10,108.00,-1080.00,0.0,-1080.00,0.0,CASH,
'''

ROBINHOOD = '''"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"
"9/20/2026","9/20/2026","9/22/2026","AAPL","Apple","Sell","5","$190.00","$950.00"
"9/10/2026","9/10/2026","9/12/2026","","ACH Deposit","ACH","","","$1,000.00"
"9/05/2026","9/05/2026","9/05/2026","TSLA","Forward split","SPL","2","",""
"9/02/2026","9/02/2026","9/04/2026","TSLA","Tesla","Buy","1","$250.00","($250.00)"
"9/01/2026","9/01/2026","9/03/2026","AAPL","Apple","Buy","10","$180.00","($1,800.00)"
"","","","","","","","","The data provided is for informational purposes only."
'''


def _by_symbol(parsed):
    return {h["symbol"]: h for h in parsed["holdings"]}


def test_schwab_positions():
    p = holdings.parse(SCHWAB)
    rows = _by_symbol(p)
    assert p["broker"] == "Schwab" and set(rows) == {"AAPL", "VTI", "CASH"}
    assert rows["AAPL"] == {
        "account": "Roth IRA ...123", "symbol": "AAPL", "quantity": 10.0, "value": 1856.40, "cost_basis": 1500.0, "cash": False,
    }
    assert rows["VTI"]["quantity"] == 5.5 and rows["CASH"]["value"] == 1234.56 and rows["CASH"]["cash"]
    assert holdings.account_type("Roth IRA ...123") == "tax_advantaged"


def test_fidelity_positions_skip_pending_activity_and_footer():
    p = holdings.parse(FIDELITY)
    assert p["broker"] == "Fidelity"
    assert [(h["account"], h["symbol"]) for h in p["holdings"]] == [
        ("Individual", "CASH"), ("Individual", "MSFT"), ("ROTH IRA", "FXAIX"),
    ]
    msft = _by_symbol(p)["MSFT"]
    assert (msft["quantity"], msft["value"], msft["cost_basis"]) == (4.0, 1600.0, 1200.0)
    assert _by_symbol(p)["CASH"]["value"] == 500.25


def test_vanguard_positions_stop_before_the_transactions():
    p = holdings.parse(VANGUARD)
    rows = _by_symbol(p)
    assert p["broker"] == "Vanguard" and set(rows) == {"VTSAX", "CASH"}  # the money market fund is cash
    assert rows["VTSAX"]["quantity"] == 100.5 and rows["VTSAX"]["cost_basis"] is None
    assert any("no cost basis" in n for n in p["notes"])


def test_robinhood_activity_is_rebuilt_at_average_cost():
    p = holdings.parse(ROBINHOOD)
    rows = _by_symbol(p)
    assert p["broker"] == "Robinhood" and set(rows) == {"AAPL", "TSLA"}
    # Bought 10 for $1,800, sold 5: half the cost remains.
    assert rows["AAPL"]["quantity"] == 5 and rows["AAPL"]["cost_basis"] == pytest.approx(900.0)
    assert rows["TSLA"]["cost_basis"] == 250.0 and any("SPL" in n for n in p["notes"])  # deposits are not mentioned


def test_unreadable_files_and_numbers():
    with pytest.raises(ValueError, match="Symbol column"):
        holdings.parse("Date,Amount\n2026-01-01,5\n")
    assert holdings.number("(1,234.50)") == -1234.5 and holdings.number("+$1.00") == 1.0
    assert holdings.number("--") is None and holdings.number("12.5%") == 12.5


def test_merge_replaces_only_the_accounts_in_the_file():
    old = [{"account": "A", "symbol": "X"}, {"account": "B", "symbol": "Y"}]
    new = [{"account": "B", "symbol": "Z"}]
    assert holdings.merge(old, new) == [{"account": "A", "symbol": "X"}, {"account": "B", "symbol": "Z"}]


def test_holdings_api(tmp_path):
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as client:
        r = client.post("/api/holdings/import", json={"text": FIDELITY}).json()
        assert r["broker"] == "Fidelity" and r["imported"] == 3
        assert r["account_types"] == {"Individual": "taxable", "ROTH IRA": "tax_advantaged"}
        assert r["summary"]["total_value"] == pytest.approx(500.25 + 1600 + 1822.14)
        assert r["summary"]["unrealised"] == pytest.approx(400 + 122.14)
        client.post("/api/holdings/import", json={"text": SCHWAB})
        assert len(client.get("/api/holdings").json()["summary"]["accounts"]) == 3
        left = client.delete("/api/holdings", params={"account": "ROTH IRA"}).json()
        assert "ROTH IRA" not in left["summary"]["accounts"] and "ROTH IRA" not in left["account_types"]
        assert client.delete("/api/holdings").json()["holdings"] == []
        assert client.post("/api/holdings/import", json={"text": "nothing,here\n1,2\n"}).status_code == 400
        assert client.put("/api/settings", json={"holdings": [{"symbol": 5}]}).status_code == 400
        assert client.put("/api/settings", json={"account_types": {"X": "offshore"}}).status_code == 400


def test_vs_spy_is_the_consensus_against_one_benchmark(tmp_path):
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as client:
        r = client.post("/api/vs-spy", json={}).json()
        assert r["demo"] and r["benchmark"] == "SIMIDX buy & hold"  # the demo market's index stands in
        assert r["strategy"]["cagr"] is not None and r["spy"]["cagr"] is not None and 0 <= r["p_beats"] <= 1
        assert r["research_log"]["configurations"] >= 1  # it counts as a trial like any backtest
        assert client.post("/api/vs-spy", json={"universe": "sectors"}).status_code == 400  # needs real prices


def test_first_run_settings(tmp_path):
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as client:
        s = client.get("/api/settings").json()
        assert s["onboarded"] is False and s["ui_mode"] == "full"
        assert client.put("/api/settings", json={"onboarded": True, "ui_mode": "simple"}).json()["ui_mode"] == "simple"
        assert client.put("/api/settings", json={"ui_mode": "expert"}).status_code == 400
