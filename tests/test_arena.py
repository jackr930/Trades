"""Strategy simulations: simulated market, live agents, runs and their API."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trades.api import create_app
from trades.arena.feeds import Feed, RealtimeFeed
from trades.arena.market import SimulatedMarket
from trades.arena.run import CLOSED_MESSAGE, RunConfig, RunError, RunManager, create_run
from trades.backtest.engine import BacktestConfig, run_backtest
from trades.core.timeframes import Timeframe
from trades.strategies.sizing import apply_sizing

SYMS = ["SIMIDX", "SIMTEC", "SIMBND", "SIMPRA", "SIMPRB"]


# ----------------------------------------------------------------------------- market


def test_market_is_deterministic_and_well_formed():
    a = SimulatedMarket(SYMS, "random", seed=4, warmup=120)
    b = SimulatedMarket(SYMS, "random", seed=4, warmup=120)
    a.generate_until(300)
    b.generate_until(300)
    fa, fb = a.frames(), b.frames()
    for s in SYMS:
        pd.testing.assert_frame_equal(fa[s], fb[s])
        df = fa[s]
        assert (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9).all()
        assert (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9).all()
        assert (df["volume"] > 0).all()
    assert fa["SIMIDX"].index.is_monotonic_increasing and fa["SIMIDX"].index[0].year == 2040
    c = SimulatedMarket(SYMS, "random", seed=5, warmup=120)
    assert not np.allclose(c.frames()["SIMIDX"]["close"], fa["SIMIDX"]["close"].iloc[:120])


def test_injected_events_change_only_the_future():
    base = SimulatedMarket(SYMS, "random", seed=9, warmup=120)
    base.generate_until(260)
    hit = SimulatedMarket(SYMS, "random", seed=9, warmup=120)
    hit.generate_until(260)
    hit.inject("crash", 200, size=-0.10, bars=20)
    hit.generate_until(260)
    b, h = base.frames(), hit.frames()
    for s in SYMS:
        pd.testing.assert_frame_equal(b[s].iloc[:200], h[s].iloc[:200])
    # The crash gaps the index down ~10% at the open (plus that bar's own noise).
    gap = h["SIMIDX"]["open"].iloc[200] / h["SIMIDX"]["close"].iloc[199] - 1
    assert -0.14 < gap < -0.06
    assert hit.regime(205) == "crash (injected)"
    # A crash reliably falls over its window: drift dominates its noise.
    moves = []
    for seed in range(12):
        m = SimulatedMarket(["SIMIDX"], "steady_bull", seed=seed, warmup=120)
        m.inject("crash", 130, size=-0.10, bars=30)
        m.generate_until(161)
        c = m.frames()["SIMIDX"]["close"]
        moves.append(c.iloc[159] / c.iloc[129] - 1)
    assert np.median(moves) < -0.2 and max(moves) < -0.05
    # Pair break: the log-spread settles around a level 20% higher.
    brk = SimulatedMarket(SYMS, "random", seed=9, warmup=120)
    brk.inject("break_pair", 150, size=0.2)
    brk.generate_until(400)
    f = brk.frames()
    spread = np.log(f["SIMPRA"]["close"]) - 0.9 * np.log(f["SIMPRB"]["close"])
    assert spread.iloc[300:400].mean() - spread.iloc[60:150].mean() == pytest.approx(0.2, abs=0.08)


def test_event_validation():
    m = SimulatedMarket(["SIMIDX"], "random", seed=1, warmup=100)
    with pytest.raises(ValueError, match="unknown event"):
        m.inject("meteor", 120)
    with pytest.raises(ValueError, match="negative"):
        m.inject("crash", 120, size=0.1)
    with pytest.raises(ValueError, match="SIMPRA"):
        m.inject("break_pair", 120)
    with pytest.raises(ValueError, match="gap"):
        m.inject("gap", 120, symbol="NOPE")
    with pytest.raises(ValueError, match="warm-up"):
        m.inject("rally", 50)


# ----------------------------------------------------------------------------- agents == backtest


def _assert_agents_match_backtests(run):
    frames = {s: df.iloc[: run.cursor + 1] for s, df in run.feed.frames().items()}
    for agent in run.agents:
        data = {s: frames[s] for s in agent.symbols}
        out = agent.strategy.run(data)
        weights = apply_sizing(out.signals, data, agent.sizing, agent.strategy.kind, agent.ppy)
        cfg = BacktestConfig(
            initial_cash=run.config.initial_cash,
            slippage_bps=run.config.slippage_bps,
            commission_bps=run.config.commission_bps,
            allow_short=run.config.allow_short,
        ).capped(1.0 if agent.benchmark else agent.sizing.max_leverage)
        res = run_backtest(data, weights, cfg, run.feed.start_index)
        expected = res.equity.iloc[run.feed.start_index :].to_numpy()
        np.testing.assert_allclose(agent.equity, expected, rtol=1e-10, err_msg=agent.id)
        assert len(agent.engine.ledger.fills) == len(res.fills), agent.id


def test_live_agents_trade_exactly_like_a_backtest():
    run = create_run(RunConfig(seed=3, symbols=SYMS, length=120, warmup=280))
    asyncio.run(run._run_bars(120))
    assert run.status == "finished" and run.summary is not None
    _assert_agents_match_backtests(run)


def test_agents_stay_consistent_after_an_injected_event():
    run = create_run(RunConfig(seed=8, symbols=SYMS, length=150, warmup=280))

    async def go():
        await run._run_bars(40)
        await run.inject("crash", size=-0.15, bars=25)
        await run.inject("break_pair", size=0.25)
        await run._run_bars(60)

    asyncio.run(go())
    _assert_agents_match_backtests(run)
    kinds = [e["type"] for e in run.events]
    assert "injected" in kinds and "fill" in kinds and "regime" in kinds


def test_modern_strategies_get_the_warmup_they_need_and_trade_like_backtests():
    modern = ("cta_trend", "stat_arb", "residual_momentum", "kalman_pairs", "hmm_regime", "ml_ranker")
    run = create_run(
        RunConfig(seed=5, symbols=[*SYMS, "SIMGLD"], length=60, strategies=[{"id": s} for s in modern])
    )
    need = max(a.strategy.warmup() for a in run.agents)
    assert run.config.warmup == need + 5 and any("Warm-up lengthened" in n for n in run.notes)
    assert run.feed.start_index == run.config.warmup - 1
    asyncio.run(run._run_bars(60))
    _assert_agents_match_backtests(run)
    for a in run.agents:  # every strategy can act from the first live bar
        assert a._valid[run.feed.start_index].all(), a.id
    # The usual strategies fit in the default warm-up, which is left alone.
    assert create_run(RunConfig(seed=5, symbols=SYMS)).config.warmup == 300


def test_summary_has_leaderboard_and_regime_attribution():
    run = create_run(
        RunConfig(seed=2, scenario="crash", symbols=["SIMIDX", "SIMBND"], strategies=[{"id": "tsmom"}])
    )

    async def go():
        await run._run_bars(10_000)

    asyncio.run(go())
    s = run.summary
    assert s is not None and {r["kind"] for r in s["leaderboard"]} == {"strategy", "benchmark"}
    regimes = {r["regime"] for r in s["regimes"]}
    assert {"calm", "crash", "recovery"} <= regimes
    assert all(set(r["returns"]) == {"tsmom", "benchmark"} for r in s["regimes"])
    assert s["cautions"]


def test_unusable_strategies_are_skipped_with_a_note():
    run = create_run(
        RunConfig(seed=1, symbols=["SIMIDX", "SIMTEC"], strategies=[{"id": "xs_momentum"}, {"id": "tsmom"}])
    )
    assert [a.id for a in run.agents] == ["tsmom", "benchmark"]
    assert any("at least 3 symbols" in n for n in run.notes)
    with pytest.raises(RunError):
        create_run(RunConfig(seed=1, symbols=["SIMIDX"], strategies=[{"id": "pairs_trading"}]))
    with pytest.raises(ValueError):
        RunConfig(source="teleport")


# ----------------------------------------------------------------------------- real-time feed


class GrowingProvider:
    """Serves daily bars up to a movable 'now', with SIMTEC missing one day."""

    def __init__(self, frames):
        self.frames = frames
        self.visible = 0

    def history(self, symbol, timeframe, start=None, end=None):
        df = self.frames[symbol].iloc[: self.visible]
        if symbol == "SIMTEC":
            df = df.drop(self.frames[symbol].index[-3], errors="ignore")
        return df[df.index >= pd.Timestamp(start)] if start is not None else df


class FakeService:
    def __init__(self, prov):
        self.prov = prov

    def provider(self, pid=None):
        return self.prov

    def bars_many(self, symbols, tf, start, end, provider, count=None):
        return {s: self.prov.history(s, tf).iloc[-(count or 10**9) :] for s in symbols}, {}


def test_realtime_feed_appends_completed_bars_and_fills_gaps(daily):
    frames = {s: daily[s].iloc[-200:] for s in ("SIMIDX", "SIMTEC")}
    prov = GrowingProvider(frames)
    prov.visible = 150
    feed = RealtimeFeed(FakeService(prov), ["SIMIDX", "SIMTEC"], Timeframe.D1, "fake", warmup=100)
    assert feed.known() == 120  # warm-up plus a small margin
    assert feed.start_index == 119
    prov.visible = 200  # 50 more completed sessions (SIMTEC lacks one of them)
    added = feed.poll()
    assert added == 50 and feed.known() == 170
    tec = feed.frames()["SIMTEC"]
    missing = frames["SIMTEC"].index[-3]
    row = tec.loc[missing]
    assert row["volume"] == 0 and row["open"] == row["close"] == tec["close"].shift(1).loc[missing]
    assert feed.poll() == 0


def test_realtime_feed_waits_for_a_symbol_whose_bar_is_late(daily):
    frames = {s: daily[s].iloc[-200:] for s in ("SIMIDX", "SIMTEC")}

    class LaggingProvider:
        visible = {"SIMIDX": 150, "SIMTEC": 150}

        def history(self, symbol, timeframe, start=None, end=None):
            df = frames[symbol].iloc[: self.visible[symbol]]
            return df[df.index >= pd.Timestamp(start)] if start is not None else df

    prov = LaggingProvider()
    feed = RealtimeFeed(FakeService(prov), ["SIMIDX", "SIMTEC"], Timeframe.D1, "fake", warmup=100)
    prov.visible["SIMIDX"] = 151  # SIMIDX's session bar is out; SIMTEC's reaches the provider later
    assert feed.poll() == 0  # held back rather than filled with a made-up flat bar
    prov.visible["SIMTEC"] = 151
    assert feed.poll() == 1
    ts = frames["SIMTEC"].index[150]
    pd.testing.assert_series_equal(feed.frames()["SIMTEC"].loc[ts], frames["SIMTEC"].loc[ts])
    # A symbol that stays silent past the grace period gets a flat bar so the others can go on.
    prov.visible["SIMIDX"] = 152
    feed.grace_seconds = 0.0
    assert feed.poll() == 1
    row = feed.frames()["SIMTEC"].iloc[-1]
    assert row["volume"] == 0 and row["open"] == row["close"] == frames["SIMTEC"]["close"].iloc[150]


def test_realtime_run_trades_bars_as_they_complete(daily):
    from trades.arena.run import StrategyRun, build_agents
    from trades.backtest.engine import BacktestConfig

    frames = {s: daily[s].iloc[-400:] for s in ("SIMIDX", "SIMTEC")}
    prov = GrowingProvider(frames)
    prov.visible = 330
    feed = RealtimeFeed(FakeService(prov), ["SIMIDX", "SIMTEC"], Timeframe.D1, "fake", warmup=280)
    feed.interval = lambda: 0.01  # poll continuously in the test
    config = RunConfig(source="realtime", symbols=["SIMIDX", "SIMTEC"], strategies=[{"id": "ma_crossover"}])
    agents, _ = build_agents(config, feed.symbols, BacktestConfig())
    run = StrategyRun(config, feed, agents, [])
    start = run.cursor

    async def go():
        with pytest.raises(RunError, match="cannot be stepped"):
            await run.step(1)  # a live run moves with the market, not on demand
        q = run.subscribe()
        await run.play()
        await asyncio.sleep(0.05)
        prov.visible = 360  # thirty sessions complete while we watch
        for _ in range(200):
            await asyncio.sleep(0.02)
            if run.cursor >= start + 30:
                break
        await run.stop()
        return q

    q = asyncio.run(go())
    assert run.cursor == start + 30 and run.status == "finished"
    assert all(len(a.equity) == 31 for a in run.agents)
    assert not q.empty()


def test_realtime_needs_a_real_provider(tmp_path):
    class Svc:
        class settings:
            @staticmethod
            def get():
                from trades.config import Settings

                return Settings()

    with pytest.raises(RunError, match="real data source"):
        create_run(RunConfig(source="realtime", symbols=["SPY"]), Svc())


# ----------------------------------------------------------------------------- API


@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path / "settings.json", start_live=False, web_dist=tmp_path / "no-dist")
    with TestClient(app) as c:
        yield c


def test_arena_api_flow(client):
    opts = client.get("/api/arena/options").json()
    assert opts["scenarios"] and opts["events"] and "SIMPRA" in opts["default_symbols"]
    r = client.post(
        "/api/arena/runs",
        json={"seed": 6, "symbols": SYMS, "length": 80, "autoplay": False, "strategies": [{"id": "tsmom"}]},
    )
    assert r.status_code == 200, r.text
    st = r.json()
    run_id = st["id"]
    assert st["status"] == "ready" and [a["id"] for a in st["agents"]] == ["tsmom", "benchmark"]
    assert len(st["bars"]["SIMIDX"]["t"]) == st["cursor"] - st["first_index"] + 1
    json.dumps(st, allow_nan=False)

    with client.websocket_connect(f"/ws/arena/{run_id}") as ws:
        c = client.post(f"/api/arena/runs/{run_id}/control", json={"action": "step", "n": 5})
        assert c.status_code == 200 and c.json()["cursor"] == st["cursor"] + 5
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "update" and len(msg["bars"]["SIMIDX"]["t"]) == 5
        assert len(msg["equity"]["tsmom"]) == 5

    inj = client.post(f"/api/arena/runs/{run_id}/inject", json={"kind": "crash", "size": -0.1, "bars": 10})
    assert inj.status_code == 200 and inj.json()["type"] == "injected"
    bad = client.post(f"/api/arena/runs/{run_id}/inject", json={"kind": "crash", "size": 0.3})
    assert bad.status_code == 400
    detail = client.get(f"/api/arena/runs/{run_id}/agents/tsmom").json()
    assert detail["explanations"] and "trades_list" in detail
    assert client.get(f"/api/arena/runs/{run_id}/agents/nope").status_code == 404
    done = client.post(f"/api/arena/runs/{run_id}/control", json={"action": "stop"}).json()
    assert done["status"] == "finished" and done["summary"]["leaderboard"]
    assert client.post(f"/api/arena/runs/{run_id}/control", json={"action": "play"}).status_code == 400
    assert [x["id"] for x in client.get("/api/arena/runs").json()] == [run_id]
    assert client.delete(f"/api/arena/runs/{run_id}").status_code == 200
    assert client.get(f"/api/arena/runs/{run_id}").status_code == 404


def test_arena_replay_run(client):
    r = client.post(
        "/api/arena/runs",
        json={
            "source": "replay",
            "provider": "synthetic",
            "symbols": ["SIMIDX", "SIMBND"],
            "start": "2020-01-02",
            "length": 30,
            "autoplay": False,
            "strategies": [{"id": "faber_trend"}],
        },
    )
    assert r.status_code == 200, r.text
    st = r.json()
    run_id = st["id"]
    assert st["feed"]["source"] == "replay" and st["regimes"] is None
    step = client.post(f"/api/arena/runs/{run_id}/control", json={"action": "step", "n": 100}).json()
    assert step["status"] == "finished" and step["cursor"] == st["start_index"] + 30
    assert client.post(f"/api/arena/runs/{run_id}/inject", json={"kind": "crash"}).status_code == 400


def test_piled_up_events_stay_finite_and_consistent():
    """Spamming events must give a wild market, never a numeric overflow or a half-built bar."""
    m = SimulatedMarket(SYMS, "whipsaw", seed=5, warmup=100)
    for i in range(40):
        at = 100 + i
        m.inject("vol_spike", at, size=6.0, bars=200)
        m.inject("crash" if i % 2 else "rally", at, size=-0.5 if i % 2 else 1.0, bars=200)
        m.inject("gap", at, size=1.0, symbol="SIMTEC")
    m.generate_until(700)
    f = m.frames()
    assert len(m) == 700 and all(len(df) == 700 for df in f.values())
    for df in f.values():
        assert np.isfinite(df[["open", "high", "low", "close", "volume"]].to_numpy()).all()
        assert (df["close"] > 0).all()
    run = create_run(RunConfig(seed=5, symbols=SYMS, length=60, warmup=280))

    async def go():
        for _ in range(15):
            await run.inject("vol_spike", size=6.0, bars=100)
            await run.inject("crash", size=-0.5, bars=100)
        await run._run_bars(60)

    asyncio.run(go())
    assert run.status == "finished" and run.error is None
    json.dumps(run.state(), allow_nan=False)


# ----------------------------------------------------------------------------- concurrency and messages


async def _until(condition, timeout: float = 5.0) -> bool:
    """Wait (up to ``timeout`` seconds) for ``condition()`` to hold."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.01)
    return True


def _drain(q) -> list[dict]:
    out = []
    while not q.empty():
        out.append(json.loads(q.get_nowait()))
    return out


def _near_the_end(length=40):
    run = create_run(
        RunConfig(
            seed=3,
            symbols=SYMS,
            length=length,
            warmup=280,
            strategies=[{"id": "tsmom"}, {"id": "ma_crossover"}],
        )
    )
    return run


def test_overlapping_steps_and_play_never_pass_the_last_bar():
    async def go(second):
        run = _near_the_end()
        q = run.subscribe()
        await run._run_bars(39)  # one bar left
        if second == "step":
            results = await asyncio.gather(run.step(1), run.step(1), return_exceptions=True)
            assert not [r for r in results if isinstance(r, Exception)], results
        else:
            step = asyncio.create_task(run.step(1))
            await asyncio.sleep(0)
            await run.play()
            await step
            assert await _until(lambda: not run.running)
        assert run.cursor == run.feed.last_index() and run.status == "finished" and run.error is None
        kinds = [m["type"] for m in _drain(q)]
        assert kinds.count("finished") == 1 and kinds[-1] == "finished"

    asyncio.run(go("step"))
    asyncio.run(go("play"))


def test_stop_halts_a_step_in_progress():
    async def go():
        run = create_run(RunConfig(seed=4, length=300, strategies=[{"id": "tsmom"}]))
        q = run.subscribe()
        step = asyncio.create_task(run.step(200))
        await asyncio.sleep(0.2)
        await run.stop()
        at_stop = run.cursor
        await step
        assert run.cursor == at_stop and run.summary["bars"] == at_stop - run.feed.start_index
        msgs = _drain(q)
        assert msgs[-1]["type"] == "finished"

    asyncio.run(go())


def test_messages_are_numbered_and_snapshots_carry_the_number():
    async def go():
        run = create_run(RunConfig(seed=2, symbols=SYMS, length=60, strategies=[{"id": "tsmom"}]))
        q = run.subscribe()
        await run.step(3)
        await run.inject("crash", size=-0.1, bars=5)
        await run.set_speed(16)
        await run.step(2)
        msgs = _drain(q)
        assert [m["seq"] for m in msgs] == list(range(1, len(msgs) + 1))
        assert run.state()["seq"] == msgs[-1]["seq"]
        bars = [m for m in msgs if m["bars"]]
        assert all(m["cursor"] - len(m["bars"]["SIMIDX"]["t"]) + 1 > 0 for m in bars)
        assert bars[-1]["cursor"] == run.cursor

    asyncio.run(go())


def test_updates_and_injections_never_interleave():
    """A message is built under the run's lock, so an injection can't truncate the market mid-build."""
    run = create_run(
        RunConfig(seed=1, symbols=SYMS, length=500, warmup=600, strategies=[{"id": "ma_crossover"}])
    )
    for _ in range(3):
        run._advance()
    errors = []
    for _ in range(40):
        run.feed.market.generate_until(len(run.feed.market) + 1)
        barrier = threading.Barrier(2)

        def inject(barrier=barrier):
            barrier.wait()
            run._inject("vol_spike", {"size": 1.5, "bars": 3})

        th = threading.Thread(target=inject)
        th.start()
        barrier.wait()
        try:
            json.loads(run._update(run.cursor - 4, []))
        except Exception as exc:  # pragma: no cover - the failure being guarded against
            errors.append(exc)
        th.join()
    assert not errors


def test_simulated_bars_payload_matches_the_frames():
    run = create_run(RunConfig(seed=9, symbols=SYMS, length=50, strategies=[{"id": "tsmom"}]))
    first, last = run.cursor - 10, run.cursor
    assert run.feed.bars_payload(first, last) == Feed.bars_payload(run.feed, first, last)


def test_hidden_regimes_are_not_revealed_early():
    run = create_run(RunConfig(seed=7, scenario="crash", symbols=SYMS, strategies=[{"id": "tsmom"}]))
    for _ in range(250):
        run._advance()
        msg = json.loads(run._update(run.cursor, []))
        seen = set(run.feed.market._regimes[: run.cursor + 1])
        assert set(msg["regime_drift"]) <= seen
    assert set(run.state()["regime_drift"]) <= set(run.feed.market._regimes[: run.cursor + 1])


def test_run_manager_limits_playing_runs_and_closes_evicted_ones():
    async def go():
        mgr = RunManager(max_runs=2, max_active=1)
        a, b, c = (
            create_run(RunConfig(seed=i, length=400, speed=0.5, strategies=[{"id": "tsmom"}]))
            for i in range(3)
        )
        await mgr.add(a, play=True)
        await mgr.add(b)  # added paused: fine
        with pytest.raises(RunError, match="already running"):
            await mgr.play(b)  # starting it would exceed the limit
        await a.pause()
        assert await _until(lambda: not a.running, timeout=1.0)  # not after its 2-second wait
        q = b.subscribe()
        await mgr.add(c)  # evicts the oldest idle run (a), which is closed
        assert [r["id"] for r in mgr.list()] == [c.id, b.id]
        await a.pause()
        with pytest.raises(RunError):
            await a.play()  # a closed simulation can't be restarted
        qa_msgs = _drain(q)
        assert CLOSED_MESSAGE not in [json.dumps(m) for m in qa_msgs]
        await mgr.shutdown()
        assert _drain(q)[-1] == {"type": "closed"}

    asyncio.run(go())


def test_a_real_time_run_refuses_to_step_without_pausing(daily):
    async def go():
        prov = GrowingProvider({s: daily[s] for s in ("SIMIDX", "SIMTEC")})
        prov.visible = 400
        feed = RealtimeFeed(FakeService(prov), ["SIMIDX", "SIMTEC"], Timeframe.D1, "fake", warmup=280)
        feed.interval = lambda: 0.05
        cfg = RunConfig(source="realtime", symbols=["SIMIDX", "SIMTEC"], strategies=[{"id": "ma_crossover"}])
        from trades.arena.run import StrategyRun, build_agents  # noqa: PLC0415

        agents, _ = build_agents(cfg, feed.symbols, BacktestConfig())
        run = StrategyRun(cfg, feed, agents, [])
        await run.play()
        with pytest.raises(RunError, match="cannot be stepped"):
            await run.step(1)
        assert run.status == "running" and run.running
        await run.close()

    asyncio.run(go())


def test_event_sizes_stay_within_what_one_bar_can_move():
    m = SimulatedMarket(SYMS, "random", seed=3, warmup=100)
    with pytest.raises(ValueError, match="-50%"):
        m.inject("gap", 100, size=-0.6, symbol="SIMTEC")
    m.inject("gap", 100, size=-0.5, symbol="SIMTEC")
    m.generate_until(101)
    f = m.frames()["SIMTEC"]
    gap = f["open"].iloc[100] / f["close"].iloc[99] - 1
    assert gap == pytest.approx(-0.5, abs=0.06)  # the stock's own noise moves the open a little


def test_an_order_that_never_fills_does_not_lend_its_reason_to_later_trades():
    run = create_run(RunConfig(seed=2, symbols=SYMS, length=30, strategies=[{"id": "tsmom"}]))
    agent = run.agents[0]
    agent._reasons["SIMIDX"] = "stale reason from an order that expired"
    agent.engine.pending.pop(agent.symbols.index("SIMIDX"), None)
    run._advance()
    assert agent._reasons.get("SIMIDX") != "stale reason from an order that expired"


def test_pausing_or_changing_speed_acts_at_once_on_a_slow_run():
    async def go():
        run = create_run(RunConfig(seed=4, length=300, speed=0.2, strategies=[{"id": "tsmom"}]))
        await run.play()
        assert await _until(lambda: run.cursor > run.feed.start_index)  # one bar, then a 5-second wait
        before = run.cursor
        await run.set_speed(200)
        assert await _until(lambda: run.cursor >= before + 20, timeout=2.0)
        await run.set_speed(0.2)
        await run.pause()
        assert await _until(lambda: not run.running, timeout=1.0)
        await run.close()

    asyncio.run(go())
