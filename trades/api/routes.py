"""REST and WebSocket routes."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool

from trades import __version__
from trades.advisor import AdvisorSettings
from trades.backtest.engine import BacktestConfig
from trades.backtest.metrics import METRIC_INFO
from trades.backtest.optimize import grid_search, range_values, walk_forward
from trades.backtest.runner import (
    StrategySpec,
    backtest_payload,
    backtest_strategy,
    bars_payload,
    cost_sensitivity,
    overlays_payload,
    sanitize,
)
from trades.core.timeframes import Timeframe
from trades.data.base import HISTORY_START, DataError, align_bars, history_start, last_bar_forming
from trades.data.synthetic import SCENARIOS, universe_info
from trades.sim.session import DEFAULT_ADVISORS, HISTORICAL_PRESETS, SimConfig, create_session
from trades.strategies import Kind, catalog, create_strategy, get_strategy_class

from .schemas import (
    BacktestBody,
    DataBody,
    NoteBody,
    OptimizeBody,
    OrderBody,
    RecommendBody,
    StepBody,
    WatchlistBody,
)

router = APIRouter()
ws_router = APIRouter()

DISCLAIMER = (
    "Educational software. Recommendations are generated mechanically from historical price data and published "
    "research; they are not investment advice and do not consider your circumstances. Past performance, "
    "especially in backtests, does not predict future results. This app never connects to a brokerage "
    "account and never places real orders."
)


def ctx(request: Request):
    return request.app.state.ctx


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return pd.Timestamp(value).date()
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid date {value!r}") from exc


def _load_bars(c, body: DataBody, warmup: int) -> tuple[dict[str, pd.DataFrame], int | None, str]:
    """Fetch aligned bars with enough history before ``start`` to warm up indicators."""
    tf = Timeframe.parse(body.timeframe)
    start, end = _parse_date(body.start), _parse_date(body.end)
    if start and end and end <= start:
        raise ValueError("end date must be after the start date")
    provider = body.provider or c.settings.get().provider
    fetch_start = None
    if start and tf is Timeframe.D1:
        fetch_start = history_start(start, warmup)  # the Live Desk's first bar, unless warm-up needs earlier
    elif start:
        fetch_start = start - timedelta(days=int(warmup / tf.bars_per_day * 1.6) + 4)
    frames, errors = c.data.bars_many(body.symbols, tf, fetch_start, end, provider)
    if errors:
        raise DataError("; ".join(f"{s}: {e}" for s, e in errors.items()))
    aligned = align_bars({s: frames[s] for s in body.symbols})
    if not aligned or len(next(iter(aligned.values()))) == 0:
        raise DataError("no overlapping bars for the requested symbols and dates")
    eval_start = None
    if start:
        idx = next(iter(aligned.values())).index
        eval_start = int(np.searchsorted(idx, pd.Timestamp(start).tz_localize("UTC")))
    return aligned, eval_start, provider


def _config(c, body: DataBody, overrides: dict[str, Any]) -> BacktestConfig:
    s = c.settings.get()
    tf = Timeframe.parse(body.timeframe)
    base = {
        "commission_bps": s.commission_bps,
        "slippage_bps": s.slippage_bps,
        "allow_short": s.allow_short,
        "initial_cash": s.account_equity,  # the account profile is the default
        "fractional": s.fractional_shares,
    }
    return BacktestConfig.from_dict({**base, **(overrides or {}), "periods_per_year": tf.periods_per_year})


# --------------------------------------------------------------------------------------
# Metadata and settings
# --------------------------------------------------------------------------------------


@router.get("/health")
async def health():
    return {"ok": True, "version": __version__}


@router.get("/meta")
async def meta(request: Request):
    c = ctx(request)
    return sanitize(
        {
            "version": __version__,
            "strategies": catalog(),
            "metrics": METRIC_INFO,
            "providers": c.data.catalog(),
            "timeframes": [{"value": t.value, "label": t.label} for t in Timeframe],
            "scenarios": [
                {"id": s.id, "label": s.label, "description": s.description, "difficulty": s.difficulty}
                for s in SCENARIOS.values()
            ],
            "presets": list(HISTORICAL_PRESETS),
            "default_advisors": DEFAULT_ADVISORS,
            "universe": universe_info(),
            "disclaimer": DISCLAIMER,
            "auth_enabled": request.app.state.auth.enabled,  # hosted with a password: offer log out
        }
    )


@router.get("/strategies")
async def strategies():
    return sanitize(catalog())


@router.get("/settings")
async def get_settings(request: Request):
    return ctx(request).settings.get().public_dict()


@router.put("/settings")
async def put_settings(request: Request, patch: dict[str, Any]):
    c = ctx(request)
    if "advisors" in patch:
        for spec in patch["advisors"] or []:
            strat, _ = StrategySpec.from_dict(spec).build()  # validate ids and params
            if strat.sizes_itself:
                raise ValueError(f"{strat.name} combines the Live Desk's strategies; it cannot be one of them")
    updated = c.settings.update(patch)
    c.data.invalidate()
    c.recommender.clear()  # cached evidence may come from the old source or costs
    c.live.poke()
    return updated.public_dict()


@router.get("/providers")
async def providers(request: Request):
    return ctx(request).data.catalog()


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, request: Request):
    c = ctx(request)
    prov = c.data.provider(provider_id)
    ok, message = await run_in_threadpool(prov.check)
    return {"ok": ok, "message": message}


# --------------------------------------------------------------------------------------
# Market data
# --------------------------------------------------------------------------------------


@router.get("/bars")
async def bars(
    request: Request,
    symbol: str,
    timeframe: str = "1d",
    start: str | None = None,
    end: str | None = None,
    provider: str | None = None,
    count: int | None = Query(None, ge=10, le=20000),
):
    c = ctx(request)
    df = await run_in_threadpool(
        c.data.bars, symbol.upper(), timeframe, _parse_date(start), _parse_date(end), provider, count=count
    )
    return {"symbol": symbol.upper(), "timeframe": timeframe, "bars": bars_payload(df)}


@router.get("/quotes")
async def quotes(request: Request, symbols: str, provider: str | None = None):
    c = ctx(request)
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:50]
    q = await run_in_threadpool(c.data.quotes, syms, provider)
    return {s: v.to_dict() for s, v in q.items()}


@router.get("/chart")
async def chart(
    request: Request,
    symbol: str,
    strategy: str = "tsmom",
    params: str | None = None,
    timeframe: str | None = None,
    provider: str | None = None,
    count: int = Query(400, ge=50, le=5000),
):
    """Bars for one symbol with a single-asset strategy's indicator overlays and signals."""
    c = ctx(request)
    s = c.settings.get()
    sym = symbol.upper()
    tf = Timeframe.parse(timeframe or s.timeframe)
    pid = provider or s.provider
    p = json.loads(params) if params else {}
    strat = create_strategy(strategy, p)

    def work():
        live_bars = c.live.bars(sym) if (pid == s.provider and tf.value == s.timeframe) else None
        if live_bars is not None:
            df = live_bars
        elif tf is Timeframe.D1:  # the same history as the Live Desk's, so the same signals
            df = c.data.bars(sym, tf, HISTORY_START, None, pid)
        else:
            df = c.data.bars(sym, tf, None, None, pid, count=max(count, 1600))
        payload: dict[str, Any] = {"symbol": sym, "strategy": strat.id, "timeframe": tf.value}
        view = df.iloc[-count:]
        payload["bars"] = bars_payload(view)
        if strat.kind is Kind.SINGLE:
            out = strat.run({sym: df})
            diag = out.diagnostics[sym]
            payload["overlays"] = overlays_payload(strat, diag.iloc[-count:])
            sig = out.signals[sym].iloc[-count:] * 1.0
            changes = sig.diff().fillna(0.0)
            markers = []
            for t, (val, ch) in enumerate(zip(sig.to_numpy(), changes.to_numpy(), strict=True)):
                if t == 0 or abs(ch) < 1e-12:
                    continue
                prev = val - ch
                kind = "exit" if abs(val) < 1e-12 else ("long" if val > 0 else "short")
                if prev * val < 0:
                    kind = "reverse_long" if val > 0 else "reverse_short"
                markers.append({"t": int(view.index[t].timestamp()), "kind": kind})
            payload["markers"] = markers
            payload["explanation"] = strat.explain(out, sym).to_dict()
        else:
            payload["overlays"], payload["markers"], payload["explanation"] = [], [], None
        return sanitize(payload)

    return await run_in_threadpool(work)


# --------------------------------------------------------------------------------------
# Research: backtests and optimisation
# --------------------------------------------------------------------------------------


@router.post("/backtest")
async def backtest(request: Request, body: BacktestBody):
    c = ctx(request)
    spec = StrategySpec(body.strategy.id, body.strategy.params, body.strategy.sizing)
    strat, sizing = spec.build()
    strat.check_symbols(body.symbols)
    cfg = _config(c, body, body.config)
    if strat.kind is Kind.PAIR and not cfg.allow_short:
        cfg.allow_short = True  # a pair trade is long one leg and short the other by construction
    if strat.sizes_itself:
        cfg.allow_short = bool(strat.params.get("allow_short"))  # the consensus's own setting decides

    def work():
        data, eval_start, provider = _load_bars(c, body, strat.warmup())
        tax = c.settings.get().tax_profile()
        bt = backtest_strategy(strat, data, sizing, cfg, eval_start, tax=tax)
        payload = backtest_payload(bt)
        payload["tax"] = asdict(tax)
        payload["cost_sensitivity"] = cost_sensitivity(bt, tax)
        payload["provider"] = provider
        payload["timeframe"] = body.timeframe
        if provider == "synthetic":
            payload["warnings"].insert(
                0,
                "Synthetic data: useful for learning the mechanics, but it says nothing "
                "about how the strategy performs in real markets.",
            )
        else:
            payload["warnings"].append(
                "Survivorship bias: symbols chosen today are ones that survived. Delisted and bankrupt "
                "companies are missing from this test, which flatters most strategies."
            )
        return sanitize(payload)

    return await run_in_threadpool(work)


@router.post("/optimize")
async def optimize(request: Request, body: OptimizeBody):
    c = ctx(request)
    cls = get_strategy_class(body.strategy.id)
    spec_by_name = {p.name: p for p in cls.params_spec}
    grid: dict[str, list[Any]] = {}
    for name, g in body.grid.items():
        if name not in spec_by_name:
            raise ValueError(f"{cls.name} has no parameter {name!r}")
        p = spec_by_name[name]
        if g.values:
            grid[name] = [p.coerce(v) for v in g.values]
        elif g.min is not None and g.max is not None and g.step:
            grid[name] = [p.coerce(v) for v in range_values(g.min, g.max, g.step, p.kind == "int")]
        else:
            raise ValueError(f"grid for {name!r} needs values or min/max/step")
    cfg = _config(c, body, body.config)
    if cls.kind is Kind.PAIR:
        cfg.allow_short = True
    probe = create_strategy(body.strategy.id, body.strategy.params)
    if cls.sizes_itself:
        cfg.allow_short = bool(probe.params.get("allow_short"))
    max_warm = probe.warmup()
    for name, values in grid.items():  # warm-up must cover the largest lookback in the grid
        for v in values:
            try:
                max_warm = max(
                    max_warm, create_strategy(body.strategy.id, {**body.strategy.params, name: v}).warmup()
                )
            except ValueError:
                continue

    def work():
        data, eval_start, provider = _load_bars(c, body, max_warm)
        if body.mode == "grid":
            res = grid_search(
                body.strategy.id,
                body.strategy.params,
                grid,
                data,
                body.strategy.sizing,
                cfg,
                body.objective,
                eval_start,
            )
        else:
            res = walk_forward(
                body.strategy.id,
                body.strategy.params,
                grid,
                data,
                body.strategy.sizing,
                cfg,
                body.objective,
                body.train_bars,
                body.test_bars,
                body.anchored,
            )
        res["mode"] = body.mode
        res["provider"] = provider
        res["grid"] = grid
        return sanitize(res)

    return await run_in_threadpool(work)


# --------------------------------------------------------------------------------------
# Recommendations (one-shot) and the live feed
# --------------------------------------------------------------------------------------


@router.post("/recommendations")
async def recommendations(request: Request, body: RecommendBody):
    c = ctx(request)
    s = c.settings.get()
    provider = body.provider or s.provider
    tf = Timeframe.parse(body.timeframe or s.timeframe)
    symbols = [x.upper() for x in (body.symbols or s.watchlist(provider))]
    if not symbols:
        raise ValueError("no symbols: add some to your watchlist")
    strategies = [x.model_dump() for x in body.strategies] if body.strategies is not None else None
    adv = AdvisorSettings.from_settings(s, strategies, tf.periods_per_year)
    adv.pairs = [(a.upper(), b.upper()) for a, b in body.pairs] or s.active_pairs(symbols)

    def work():
        daily = tf is Timeframe.D1
        frames, errors = c.data.bars_many(
            symbols, tf, HISTORY_START if daily else None, None, provider, count=None if daily else 1600
        )
        # The synthetic history is complete; real providers include today's forming bar.
        forming = {s: provider != "synthetic" and last_bar_forming(df, tf) for s, df in frames.items()}
        res = c.recommender.recommend(frames, adv, provisional=forming, namespace=f"{provider}:{tf.value}")
        res["errors"] = errors
        return sanitize(res)

    return await run_in_threadpool(work)


@router.get("/live")
async def live_snapshot(request: Request):
    live = ctx(request).live
    live.touch()  # someone is looking: keep (or resume) live updates
    return sanitize(live.snapshot())


@router.post("/live/start")
async def live_start(request: Request):
    c = ctx(request)
    await c.live.start()
    c.live.touch()
    c.live.poke()
    return sanitize(c.live.snapshot())


@router.post("/live/stop")
async def live_stop(request: Request):
    c = ctx(request)
    await c.live.stop()
    return sanitize(c.live.snapshot())


@router.put("/live/watchlist")
async def live_watchlist(request: Request, body: WatchlistBody):
    c = ctx(request)
    provider = body.provider or c.settings.get().provider
    c.settings.update({"watchlists": {provider: body.symbols}})
    c.live.poke()
    return sanitize(c.live.snapshot())


@ws_router.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    c = ws.app.state.ctx
    queue = c.live.subscribe()

    async def sender():
        await ws.send_text(json.dumps(sanitize({"type": "update", "data": c.live.snapshot()}), default=str))
        while True:
            await ws.send_text(await queue.get())

    async def receiver():
        while True:
            await ws.receive_text()

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        c.live.unsubscribe(queue)


# --------------------------------------------------------------------------------------
# Simulator
# --------------------------------------------------------------------------------------


def _session(c, session_id: str):
    try:
        return c.sims.get(session_id)
    except KeyError as exc:
        raise HTTPException(404, "simulation session not found (sessions are kept in memory)") from exc


def _after_change(c, sess) -> None:
    if sess.finished and not getattr(sess, "_recorded", False):
        c.sims.record(sess)
        sess._recorded = True


@router.get("/sim/history")
async def sim_history(request: Request):
    return sanitize(ctx(request).sims.history())


@router.post("/sim")
async def sim_create(request: Request, body: dict[str, Any]):
    c = ctx(request)
    config = SimConfig.from_dict(body)
    sess = await run_in_threadpool(create_session, config, c.data)
    c.sims.add(sess)
    return sanitize(sess.state())


@router.get("/sim/{session_id}")
async def sim_state(request: Request, session_id: str, since: int | None = None):
    return sanitize(_session(ctx(request), session_id).state(since))


@router.post("/sim/{session_id}/step")
async def sim_step(request: Request, session_id: str, body: StepBody):
    c = ctx(request)
    sess = _session(c, session_id)
    fills = await run_in_threadpool(sess.step, body.n)
    _after_change(c, sess)
    state = sess.state(body.since)
    state["new_fills"] = fills
    return sanitize(state)


@router.post("/sim/{session_id}/orders")
async def sim_order(request: Request, session_id: str, body: OrderBody):
    sess = _session(ctx(request), session_id)
    order = sess.place_order(**body.model_dump())
    return sanitize({"order": order, "state": sess.state(sess.cursor)})


@router.delete("/sim/{session_id}/orders/{order_id}")
async def sim_cancel(request: Request, session_id: str, order_id: str):
    sess = _session(ctx(request), session_id)
    order = sess.cancel_order(order_id)
    return sanitize({"order": order, "state": sess.state(sess.cursor)})


@router.post("/sim/{session_id}/close")
async def sim_close(request: Request, session_id: str, body: dict[str, Any] | None = None):
    sess = _session(ctx(request), session_id)
    order = sess.close_position(note=str((body or {}).get("note", "")))
    return sanitize({"order": order, "state": sess.state(sess.cursor)})


@router.post("/sim/{session_id}/journal")
async def sim_journal(request: Request, session_id: str, body: NoteBody):
    sess = _session(ctx(request), session_id)
    return sanitize({"entry": sess.add_note(body.text), "journal": sess.journal})


@router.post("/sim/{session_id}/finish")
async def sim_finish(request: Request, session_id: str):
    c = ctx(request)
    sess = _session(c, session_id)
    await run_in_threadpool(sess.finish)
    _after_change(c, sess)
    return sanitize(sess.state())
