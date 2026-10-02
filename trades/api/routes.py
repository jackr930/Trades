"""REST and WebSocket routes."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from datetime import date, timedelta
from typing import Any

import httpx
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool

from trades import __version__, holdings, planning
from trades.advisor import AdvisorSettings
from trades.backtest import robustness, trials
from trades.backtest.engine import BacktestConfig
from trades.backtest.metrics import METRIC_INFO
from trades.backtest.optimize import grid_search, range_values, walk_forward
from trades.backtest.runner import (
    BENCHMARKS,
    Benchmark,
    StrategySpec,
    backtest_payload,
    backtest_strategy,
    bars_payload,
    cost_sensitivity,
    overlays_payload,
    sanitize,
)
from trades.backup import make_backup, restore_backup
from trades.config import clean_symbols
from trades.core.timeframes import Timeframe
from trades.data.base import HISTORY_START, DataError, align_bars, history_start, last_bar_forming
from trades.data.synthetic import SCENARIOS, universe_info
from trades.journal.source import load_track_record
from trades.sim.session import DEFAULT_ADVISORS, HISTORICAL_PRESETS, SimConfig, create_session
from trades.strategies import Kind, catalog, create_strategy, get_strategy_class
from trades.strategies.consensus import params_from_settings
from trades.universes import UNIVERSES

from .schemas import (
    AllocationBody,
    BacktestBody,
    DataBody,
    DragBody,
    GoalBody,
    HoldingsImportBody,
    NoteBody,
    OptimizeBody,
    OrderBody,
    RecommendBody,
    StepBody,
    StrategyBody,
    VsSpyBody,
    WatchlistBody,
)

router = APIRouter()
ws_router = APIRouter()

DISCLAIMER = (
    "Educational software. Recommendations are generated mechanically from historical price data and published "
    "research; they are not investment advice and do not consider your circumstances. Past performance, "
    "especially in backtests, does not predict future results. This app never places real-money orders; "
    "its optional paper trader uses an Alpaca paper (practice) account only."
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
    if tf is Timeframe.D1:
        # The Live Desk's first bar (unless warm-up needs earlier), with or without a start date:
        # a provider's default window would move the first bar and so change the calls.
        fetch_start = history_start(start, warmup)
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


def _with_cash(c, cfg: BacktestConfig, body, data, provider: str, notes: list[str]) -> BacktestConfig:
    """``cfg`` with idle cash earning T-bill returns (daily bars), unless switched off."""
    want = (body.config or {}).get("cash_yield", c.settings.get().cash_yield)
    if want not in (True, "tbill") or Timeframe.parse(body.timeframe) is not Timeframe.D1:
        return cfg
    rets, note = c.data.cash_returns(next(iter(data.values())).index, provider)
    if note:
        notes.append(note)
    return replace(cfg, cash_returns=rets) if rets is not None else cfg


def _benchmark(c, body, data, provider: str) -> Benchmark | None:
    """The benchmark chosen in the request's config (default: equal-weight of the symbols)."""
    key = (body.config or {}).get("benchmark", "ew")
    spec = BENCHMARKS.get(key)
    if spec is None and clean_symbols([key]) == [str(key)]:  # a single symbol, such as SPY
        spec = {"label": f"{key} buy & hold", "weights": {key: 1.0}}
    if spec is None:
        raise ValueError(f"unknown benchmark {key!r}; choose a symbol or one of {', '.join(BENCHMARKS)}")
    if spec["weights"] is None:
        return None
    index = next(iter(data.values())).index
    frames, errors = c.data.bars_many(
        list(spec["weights"]), body.timeframe, index[0].date(), index[-1].date(), provider
    )
    if errors:
        raise DataError("benchmark: " + "; ".join(f"{s}: {e}" for s, e in errors.items()))
    return Benchmark(str(spec["label"]), dict(spec["weights"]), frames)


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
            "universes": UNIVERSES,
            "benchmarks": {k: {"label": v["label"], "weights": v["weights"]} for k, v in BENCHMARKS.items()},
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


def _backup_files(c) -> dict:
    return {"trials.jsonl": trials.log_path(), "sim_history.jsonl": c.sims.history_path}


@router.get("/backup")
async def backup(request: Request):
    """Settings (without API keys), the research log and the simulator history, as one JSON file."""
    c = ctx(request)
    return make_backup(c.settings, _backup_files(c))


@router.post("/restore")
async def restore(request: Request, body: dict[str, Any]):
    c = ctx(request)
    summary = restore_backup(c.settings, _backup_files(c), body)
    c.data.invalidate()
    c.recommender.clear()
    c.live.poke()
    return {**summary, "settings_now": c.settings.get().public_dict()}


# --------------------------------------------------------------------------------------
# Your holdings, imported from a broker's CSV export (read-only)
# --------------------------------------------------------------------------------------


def _holdings_payload(c) -> dict:
    s = c.settings.get()
    return sanitize(
        {
            "holdings": s.holdings,
            "account_types": s.account_types,
            "summary": holdings.summary(s.holdings, s.account_types),
        }
    )


@router.get("/holdings")
async def get_holdings(request: Request):
    return _holdings_payload(ctx(request))


@router.post("/holdings/import")
async def import_holdings(request: Request, body: HoldingsImportBody):
    """Replace the accounts in the file with its positions; other accounts stay as they were."""
    c = ctx(request)
    parsed = holdings.parse(body.text)
    s = c.settings.get()
    types = dict(s.account_types)
    for h in parsed["holdings"]:
        types.setdefault(h["account"], holdings.account_type(h["account"]))
    c.settings.update({"holdings": holdings.merge(s.holdings, parsed["holdings"]), "account_types": types})
    return {**_holdings_payload(c), "broker": parsed["broker"], "imported": len(parsed["holdings"]), "notes": parsed["notes"]}


@router.delete("/holdings")
async def delete_holdings(request: Request, account: str | None = None):
    """Forget the imported holdings: one account's, or all of them."""
    c = ctx(request)
    s = c.settings.get()
    keep = [h for h in s.holdings if account is not None and h["account"] != account]
    types = {a: t for a, t in s.account_types.items() if account is not None and a != account}
    c.settings.update({"holdings": keep, "account_types": types})
    return _holdings_payload(c)


# --------------------------------------------------------------------------------------
# Planning: goals, cost and tax drag, allocation and asset location, tax-loss harvesting
# --------------------------------------------------------------------------------------

PLAN_HISTORY_START = date(1990, 1, 1)  # as far back as the proxy funds go


@router.post("/plan/goal")
async def plan_goal(request: Request, body: GoalBody):
    """Will a stock/bond/cash mix reach a goal? Bootstrapped from the proxy funds' own history."""
    c = ctx(request)
    s = c.settings.get()
    demo = s.provider == "synthetic"
    proxies = planning.DEMO_PROXIES if demo else planning.PROXIES
    used = {cls: sym for cls, sym in proxies.items() if body.mix.get(cls, 0) > 0}  # only what the mix holds

    def work():
        closes = {}
        if used:
            frames, errors = c.data.bars_many(list(used.values()), Timeframe.D1, PLAN_HISTORY_START, None, s.provider)
            if errors:
                raise DataError("; ".join(f"{k}: {v}" for k, v in errors.items()))
            closes = {cls: frames[sym]["close"] for cls, sym in used.items()}
        if not closes:  # all cash in the demo: a flat series
            idx = pd.date_range("2010-01-31", periods=180, freq="ME", tz="UTC")
            returns = pd.DataFrame({"cash": 0.0}, index=idx)
        else:
            returns = planning.monthly_returns(closes)
        mix = planning.mix_returns(returns, {k: v for k, v in body.mix.items() if k in planning.CLASSES})
        out = planning.goal_paths(mix, body.start_value, body.monthly, body.years, body.goal, body.inflation)
        out["history"] = planning.history_stats(mix, body.start_value)
        out["proxies"] = used
        out["demo"] = demo
        return sanitize(out)

    return await run_in_threadpool(work)


@router.post("/plan/drag")
async def plan_drag(request: Request, body: DragBody):
    """Fees, trading costs and taxes over the years, for buy-and-hold and for your scenario."""
    s = ctx(request).settings.get()
    taxable = s.account_type == "taxable"
    tax = s.tax_profile()
    rates = {"short_rate": tax.short_term, "long_rate": tax.long_term}
    common = {"amount": body.amount, "years": body.years, "gross_return": body.gross_return, "taxable": taxable, **rates}
    index_fund = planning.drag(**common, expense_ratio=0.0003, turnover=0.02, trade_cost_bps=1.0)
    scenario = planning.drag(
        **common,
        expense_ratio=body.expense_ratio,
        advisory_fee=body.advisory_fee,
        turnover=body.turnover,
        trade_cost_bps=body.trade_cost_bps,
    )
    return sanitize({"index_fund": index_fund, "scenario": scenario, "taxable": taxable, **rates})


@router.post("/plan/allocation")
async def plan_allocation(request: Request, body: AllocationBody):
    s = ctx(request).settings.get()
    tax = s.tax_profile()
    rates = {"short_rate": tax.short_term, "long_rate": tax.long_term}
    return sanitize(planning.allocation(s.holdings, s.account_types, body.targets, body.band, **rates))


@router.get("/plan/harvest")
async def plan_harvest(request: Request, min_loss: float = Query(200.0, ge=0)):
    s = ctx(request).settings.get()
    tax = s.tax_profile()  # the account profile's rates; harvesting only applies to taxable accounts anyway
    return sanitize(
        {
            "candidates": planning.harvest(s.holdings, s.account_types, tax.short_term, tax.long_term, min_loss),
            "short_rate": tax.short_term,
            "long_rate": tax.long_term,
        }
    )


# --------------------------------------------------------------------------------------
# The forward track record (committed by the journal workflow)
# --------------------------------------------------------------------------------------


@router.get("/track-record")
async def track_record(request: Request):
    source = ctx(request).settings.get().journal_source
    try:
        record = await run_in_threadpool(load_track_record, source)
    except (httpx.HTTPError, ValueError) as exc:  # unreachable, or not JSON
        raise HTTPException(502, f"Could not read the track record from {source}: {exc}") from exc
    return sanitize({"source": source or "the local journal/ folder", "record": record})


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
        notes: list[str] = []
        run_cfg = _with_cash(c, cfg, body, data, provider, notes)
        tax = c.settings.get().tax_profile()
        bench = _benchmark(c, body, data, provider)
        bt = backtest_strategy(strat, data, sizing, run_cfg, eval_start, tax=tax, benchmark=bench)
        payload = backtest_payload(bt)
        if bt.benchmark is not None:
            payload["robustness"] = robustness.report(
                bt.equity, bt.benchmark.equity.iloc[bt.start :], run_cfg.periods_per_year
            )
        payload["warnings"] += notes
        payload["research_log"] = trials.record_and_summarise(
            "backtest", spec, list(data), bt.metrics, len(bt.equity) - 1, run_cfg.periods_per_year
        )
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


VS_SPY_START = "2010-01-01"  # the start the decision rule registers for the backtest
VS_KEYS = ("cagr", "after_tax_cagr_if_sold", "max_drawdown", "sharpe", "total_return")


@router.post("/vs-spy")
async def vs_spy(request: Request, body: VsSpyBody):
    """One click: would the Live Desk's consensus have beaten simply buying SPY, after costs and taxes?

    It is the Strategy Lab's backtest with nothing left to choose: the consensus under your
    Live Desk settings, on your watchlist or the nine sector ETFs, from 2010, against SPY.
    """
    c = ctx(request)
    s = c.settings.get()
    demo = s.provider == "synthetic"
    if body.universe == "sectors":
        if demo:
            raise ValueError("The sector ETFs need real prices: choose Yahoo (no key needed) as the data source in Settings.")
        symbols = list(UNIVERSES["sectors"]["symbols"])
    else:
        symbols = list(s.watchlists.get(s.provider) or [])
    if not symbols:
        raise ValueError("Your watchlist is empty: add symbols on the Live Desk first.")
    run = BacktestBody(
        strategy=StrategyBody(id="consensus", params=params_from_settings(s)),
        symbols=symbols,
        start=VS_SPY_START,
        config={"benchmark": "SIMIDX" if demo else "SPY"},  # the demo market's index stands in for SPY
    )
    res = await backtest(request, run)
    bench = res.get("benchmark") or {}
    return {
        "universe": body.universe,
        "symbols": symbols,
        "demo": demo,
        "benchmark": bench.get("label"),
        "start_time": res["start_time"],
        "end_time": res["end_time"],
        "taxable": res["metrics"].get("after_tax_cagr_if_sold") is not None,
        "strategy": {k: res["metrics"].get(k) for k in VS_KEYS},
        "spy": {k: (bench.get("metrics") or {}).get(k) for k in VS_KEYS},
        "p_beats": res["metrics"].get("p_beats_growth"),
        "research_log": res.get("research_log"),
        "warnings": res.get("warnings", []),
    }


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
        notes: list[str] = []
        run_cfg = _with_cash(c, cfg, body, data, provider, notes)
        if body.mode == "grid":
            res = grid_search(
                body.strategy.id,
                body.strategy.params,
                grid,
                data,
                body.strategy.sizing,
                run_cfg,
                body.objective,
                eval_start,
            )
        else:
            # Walk forward from the requested start: earlier bars only warm the indicators up.
            first = max((eval_start or 0) - max_warm + 1, 0)
            res = walk_forward(
                body.strategy.id,
                body.strategy.params,
                grid,
                {s: df.iloc[first:] for s, df in data.items()},
                body.strategy.sizing,
                run_cfg,
                body.objective,
                body.train_bars,
                body.test_bars,
                body.anchored,
            )
        res["mode"] = body.mode
        res["provider"] = provider
        res["notes"] = notes
        if body.mode == "grid":
            trials.record_grid(body.strategy.id, list(data), res, body.strategy.params)
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
