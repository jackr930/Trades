"""Recommendation engine and the live service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pandas as pd
import pytest

from tests.conftest import UNIVERSE
from trades.advisor import AdvisorSettings, Recommender
from trades.advisor.recommender import consensus_label, suggest_position
from trades.backtest.runner import StrategySpec
from trades.config import Settings, SettingsStore
from trades.core.timeframes import Timeframe
from trades.data.base import Quote
from trades.data.service import DataService
from trades.live.service import DemoClock, LiveService, merge_quote, quote_session_date


def _settings(**kw) -> AdvisorSettings:
    specs = [
        StrategySpec(i) for i in ("tsmom", "faber_trend", "rsi2_reversion", "xs_momentum", "pairs_trading")
    ]
    return AdvisorSettings(strategies=specs, pairs=[("SIMPRA", "SIMPRB")], **kw)


def test_recommendations_structure(daily):
    rec = Recommender().recommend(daily, _settings(), namespace="test")
    by_sym = {r["symbol"]: r for r in rec["recommendations"]}
    assert set(by_sym) == set(daily)
    r = by_sym["SIMIDX"]
    ids = [v["strategy_id"] for v in r["votes"]]
    assert ids.count("tsmom") == 1 and "xs_momentum" in ids and "pairs_trading" not in ids
    assert "pairs_trading" in [v["strategy_id"] for v in by_sym["SIMPRA"]["votes"]]
    c = r["consensus"]
    assert -1 <= c["score"] <= 1 and c["bullish"] + c["bearish"] + c["neutral"] == c["n_votes"]
    vote = r["votes"][0]
    assert vote["headline"] and vote["evidence"]["sharpe"] is not None and vote["evidence"]["years"] > 1


def test_evidence_is_cached(daily):
    r = Recommender()
    data = {s: daily[s] for s in UNIVERSE[:3]}
    r.recommend(data, _settings(), namespace="x")
    n = len(r._evidence)
    r.recommend(data, _settings(), namespace="x")
    assert len(r._evidence) == n


def test_provisional_excludes_forming_bar_from_evidence(daily):
    data = {"SIMIDX": daily["SIMIDX"]}
    res = Recommender().recommend(data, _settings(), provisional=True, namespace="p")
    rec = res["recommendations"][0]
    assert rec["provisional"] and any("still-forming" in f for f in rec["risk"]["flags"])


def test_consensus_labels_and_sizing():
    assert consensus_label(0.6, False) == ("BUY", "Strong buy")
    assert consensus_label(-0.3, False) == ("SELL", "Sell / avoid")
    assert consensus_label(-0.3, True)[0] == "SHORT"
    assert consensus_label(0.0, False)[0] == "HOLD"
    s = AdvisorSettings(
        strategies=[], account_equity=100_000, risk_per_trade=0.01, stop_atr=2, max_position_pct=0.2
    )
    pos = suggest_position(price=50.0, atr=1.0, score=1.0, settings=s)
    # risk: 1000 / 2 = 500 shares; cap: 20000 / 50 = 400 shares -> 400
    assert (
        pos["shares"] == 400
        and pos["stop"] == pytest.approx(48.0)
        and pos["limited_by"] == "max position size"
    )
    assert suggest_position(price=50.0, atr=1.0, score=-1.0, settings=s)["side"] == "flat"  # no shorting
    half = suggest_position(price=50.0, atr=5.0, score=0.5, settings=s)
    assert half["shares"] == 50  # 1000 / 10 = 100 shares, scaled by 50% conviction


def test_quote_session_date_rules():
    # Saturday quote belongs to Friday's session; a pre-market quote to the previous day.
    assert quote_session_date(datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)).isoformat() == "2026-09-25"
    assert quote_session_date(datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)).isoformat() == "2026-09-25"
    assert quote_session_date(datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)).isoformat() == "2026-09-28"


def test_merge_quote_updates_or_appends(daily):
    df = daily["SIMIDX"].iloc[-5:]
    last_day = df.index[-1]
    same = Quote(
        "SIMIDX", 999.0, (last_day + pd.Timedelta(hours=19)).to_pydatetime(), high=1000.0, low=1.0, volume=5.0
    )
    out = merge_quote(df, same)
    assert len(out) == 5 and out["close"].iloc[-1] == 999.0 and out["high"].iloc[-1] == 1000.0
    nxt = Quote("SIMIDX", 555.0, datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc), open=550.0)
    out2 = merge_quote(df, nxt)
    assert len(out2) == 6 and out2["open"].iloc[-1] == 550.0
    old = Quote("SIMIDX", 1.0, datetime(2020, 1, 2, 15, 0, tzinfo=timezone.utc))
    assert merge_quote(df, old).equals(df)


def test_demo_clock_forms_a_partial_bar():
    clock = DemoClock(seed=7, speed=3600, symbols=["SIMIDX"], timeframe=Timeframe.D1)
    df, tau = clock.bars("SIMIDX")
    assert 0 <= tau < 1
    assert df.index[-1].date() > clock.base_end
    last = df.iloc[-1]
    assert last["low"] <= min(last["open"], last["close"]) and last["high"] >= max(
        last["open"], last["close"]
    )
    assert clock.status()["phase"] == "demo"


def test_live_service_demo_tick(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.update({"watchlists": {"synthetic": ["SIMIDX", "SIMTEC", "SIMBNK"]}})
    svc = LiveService(DataService(store), store)

    async def run():
        s = store.get()
        await svc._reload(s)
        await svc._tick(s)
        return svc.snapshot()

    snap = asyncio.run(run())
    assert snap["demo"] and set(snap["quotes"]) == {"SIMIDX", "SIMTEC", "SIMBNK"}
    assert len(snap["recommendations"]) == 3 and snap["provisional"]
    assert snap["market"]["phase"] == "demo"


def test_live_service_broadcast_queue(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    svc = LiveService(DataService(store), store)

    async def run():
        q = svc.subscribe()
        for i in range(12):  # overflow: oldest messages are dropped, newest kept
            await svc._broadcast({"type": "update", "n": i})
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        svc.unsubscribe(q)
        return items

    items = asyncio.run(run())
    assert len(items) == 8 and '"n": 11' in items[-1]


def test_settings_default_advisors_are_valid():
    for spec in Settings().advisors:
        StrategySpec.from_dict(spec).build()
