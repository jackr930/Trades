"""``trades research STRAT-xxx``: put a spec through the acceptance checklist and record the run.

1. Load the spec; refuse a frozen spec whose rules changed after it was frozen.
2. Load bars from the train period's start (plus warm-up) to the validation period's end. The
   locked test's bars are not even loaded unless the test is being opened.
3. Backtest the spec's strategy once over train and validation, under the repository's cost model
   and risk limits (the risk engine the paper trader uses), then again at twice the costs.
4. Backtest every variant in the spec's parameter grid and every numeric parameter moved +/-20%;
   they all count as variants tested, now and in every later run of this spec version.
5. Check causality twice: the strategy's past must not change when later bars are added, and it
   must show no edge on a random walk.
6. Score the fourteen checks (``trades.validation.checklist``).
7. Only with ``open_locked_test`` (Jack's decision, blocked for Claude Code), only for a frozen
   spec whose other checks all passed, and only once per spec version: record the opening, then
   backtest the test period.
8. Write ``runs/<id>/`` (``trades.runs``).
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from trades import config, runs, specs
from trades.data.base import align_bars, history_start
from trades.data.timeframes import Timeframe
from trades.engine.backtest import StrategyBacktest, backtest_strategy
from trades.execution import costs
from trades.execution.sim_broker import BacktestConfig
from trades.features.intraday import infer_bar_minutes
from trades.reporting.tearsheet import render
from trades.risk import RiskLimits, load_limits
from trades.risk.engine import session_of
from trades.strategies import StrategyConfig, get_strategy_class
from trades.validation import checklist, lookahead, null, robustness

# fetch(provider, symbols, timeframe, start, end) -> (frames by symbol, errors by symbol)
Fetch = Callable[[str, list[str], str, date, date], tuple[dict[str, pd.DataFrame], dict[str, str]]]


class ResearchError(RuntimeError):
    """A run that must not happen (for example, opening a locked test twice)."""


def default_fetch(provider: str, symbols: list[str], timeframe: str, start: date, end: date):
    from trades.data.service import DataService
    from trades.settings import SettingsStore

    return DataService(SettingsStore()).bars_many(symbols, timeframe, start, end, provider)


def _variant_label(params: dict[str, Any]) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(params.items())) or "spec"


def _in(ts: Any, period: specs.Period) -> bool:
    return period.start <= session_of(ts) <= period.end


class Context:
    """What one run needs: the spec, the data, the engine configuration and the limits."""

    def __init__(self, spec: specs.Spec, data: dict[str, pd.DataFrame], cfg: BacktestConfig, limits: RiskLimits,
                 provider: str, eval_start: int):
        self.spec, self.data, self.cfg, self.limits = spec, data, cfg, limits
        self.provider, self.eval_start = provider, eval_start
        self.index = next(iter(data.values())).index
        self.bar_minutes = infer_bar_minutes(self.index)

    def backtest(self, params: dict[str, Any] | None = None, cfg: BacktestConfig | None = None) -> StrategyBacktest:
        s = self.spec.strategy
        strat, sizing = StrategyConfig(s.id, {**s.params, **(params or {})}, s.sizing).build()
        return backtest_strategy(strat, self.data, sizing, cfg or self.cfg, self.eval_start, with_benchmark=False, risk=self.limits)

    def trade_returns(self, bt: StrategyBacktest, periods: tuple[str, ...]) -> tuple[pd.Series, list[float]]:
        """Per-trade net results (fraction of equity at entry) of trades entered in ``periods``,
        indexed by entry time, and their R multiples where the strategy sets stops."""
        eq = bt.result.equity
        stops = bt.output.stop_losses
        rets, times, r_mult = [], [], []
        for t in bt.result.trades:
            if t.is_open or not any(_in(t.entry_time, self.spec.periods[p]) for p in periods):
                continue
            before = float(eq.iloc[max(t.entry_index - 1, 0)])
            if before <= 0:
                continue
            rets.append(t.pnl / before)
            times.append(t.entry_time)
            if stops is not None and t.symbol in stops:
                stop = float(stops[t.symbol].iloc[max(t.entry_index - 1, 0)])
                risk = t.qty * abs(t.entry_price - stop)
                if math.isfinite(stop) and risk > 0:
                    r_mult.append(t.pnl / risk)
        return pd.Series(rets, index=pd.DatetimeIndex(times), dtype=float), r_mult

    def daily(self, bt: StrategyBacktest, periods: tuple[str, ...]) -> pd.Series:
        d = robustness.daily_returns(bt.result.equity.iloc[self.eval_start :])
        keep = [any(self.spec.periods[p].start <= ts.date() <= self.spec.periods[p].end for p in periods) for ts in d.index]
        return d[np.asarray(keep, dtype=bool)]


def _load(spec: specs.Spec, provider: str, fetch: Fetch, through: str) -> tuple[dict[str, pd.DataFrame], int]:
    tf = Timeframe.parse(spec.timeframe)
    strat, _ = spec.strategy.build()
    train_start = spec.periods["train"].start
    if tf is Timeframe.D1:
        start = history_start(train_start, strat.warmup())
    else:
        sessions = math.ceil(strat.warmup() / max(tf.bars_per_day, 1)) + 1
        start = train_start - timedelta(days=int(sessions * 1.5) + 7)
    end = spec.periods[through].end
    frames, errors = fetch(provider, spec.symbols, tf.value, start, end)
    if errors:
        raise ResearchError(f"data errors from {provider}: {errors}")
    if not frames:
        raise ResearchError(f"{provider} returned no data for {', '.join(spec.symbols)}")
    # Defensive: whatever the provider returned, nothing after ``end`` enters the run.
    data = align_bars({s: df[df.index < pd.Timestamp(end + timedelta(days=1)).tz_localize("UTC")] for s, df in frames.items()})
    index = next(iter(data.values())).index
    eval_start = int(index.searchsorted(pd.Timestamp(train_start).tz_localize("UTC")))
    if eval_start >= len(index) - 2:
        raise ResearchError("the data does not reach the train period")
    return data, eval_start


def _context(spec: specs.Spec, provider: str, fetch: Fetch, through: str) -> Context:
    research = config.section("backtest", "research")
    tf = Timeframe.parse(spec.timeframe)
    data, eval_start = _load(spec, provider, fetch, through)
    cfg = BacktestConfig(initial_cash=float(research["initial_cash"]), periods_per_year=tf.periods_per_year)
    cfg = costs.with_model(cfg, spec.costs)
    return Context(spec, data, cfg, load_limits(), provider, eval_start)


def _grid(spec: specs.Spec) -> list[dict[str, Any]]:
    names = list(spec.parameters)
    combos = [dict(zip(names, values, strict=True)) for values in itertools.product(*(spec.parameters[n] for n in names))]
    return combos or [{}]


def _neighbours(spec: specs.Spec, step: float) -> list[dict[str, Any]]:
    """Every numeric parameter moved down and up by ``step`` (one at a time), kept in range."""
    cls = get_strategy_class(spec.strategy.id)
    base = cls.resolve_params(spec.strategy.params)
    out = []
    for p in cls.params_spec:
        value = base.get(p.name)
        if p.kind not in ("int", "float") or not isinstance(value, (int, float)) or value == 0:
            continue
        for factor in (1 - step, 1 + step):
            moved = value * factor
            if p.kind == "int":
                moved = int(round(moved))
            if p.min is not None:
                moved = max(moved, p.min)
            if p.max is not None:
                moved = min(moved, p.max)
            if moved != value:
                out.append({p.name: moved})
    return out


def research(
    ref: str,
    *,
    open_locked_test: bool = False,
    provider: str | None = None,
    fetch: Fetch | None = None,
    now: datetime | None = None,
    write: bool = True,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    spec = specs.find(ref)
    if spec.drift():
        raise specs.SpecError(spec.drift())
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if open_locked_test:
        if not spec.is_frozen:
            raise ResearchError(f"{spec.id} is a draft: only a frozen spec's locked test can be opened.")
        prior = runs.locked_test_run(spec.key)
        if prior is not None:
            raise ResearchError(
                f"{spec.key}'s locked test was already opened on {prior['locked_test'].get('opened_on')} "
                f"(run {prior['id']}). It cannot be unseen, and its result stands; to test changed rules, "
                "freeze a new version."
            )
    research_cfg = config.section("backtest", "research")
    acceptance = config.section("backtest", "acceptance")
    seed = int(research_cfg.get("seed", 7))
    provider = provider or spec.provider
    fetch = fetch or default_fetch
    run_id = runs.new_run_id(spec.key, now)
    log(f"{spec.key}: {spec.name} -- run {run_id}")
    log(f"Loading {spec.timeframe} bars for {len(spec.symbols)} symbol(s) from {provider} (train and validation only) ...")
    ctx = _context(spec, provider, fetch, "validation")
    log(f"Costs: {costs.describe(ctx.cfg)}")

    log("Backtesting the spec under the risk limits, and again at 2x costs ...")
    base = ctx.backtest()
    research_periods = ("train", "validation")
    val_rets, val_r = ctx.trade_returns(base, ("validation",))
    all_rets, all_r = ctx.trade_returns(base, research_periods)
    stressed_cfg = costs.stressed(ctx.cfg, float(research_cfg.get("cost_stress", 2.0)))
    stressed = ctx.backtest(cfg=stressed_cfg)
    stressed_val, stressed_r = ctx.trade_returns(stressed, ("validation",))
    base_daily = ctx.daily(base, research_periods)

    variants: list[dict[str, Any]] = [{"params": {}, "purpose": "spec", "sharpe": robustness.annual_sharpe(base_daily), "trades": len(all_rets)}]
    grid = [g for g in _grid(spec) if g]
    returns = {"spec": base_daily}
    if grid:
        log(f"Backtesting {len(grid)} parameter variants from the spec's grid ...")
    for params in grid:
        full = {**spec.strategy.params, **params}
        if full == spec.strategy.params:
            continue
        bt = ctx.backtest(params)
        d = ctx.daily(bt, research_periods)
        returns[_variant_label(params)] = d
        variants.append({"params": full, "purpose": "grid", "sharpe": robustness.annual_sharpe(d), "trades": len(bt.result.trades)})
    matrix = pd.DataFrame(returns)

    step = float(research_cfg.get("neighborhood", 0.2))
    neighbours = _neighbours(spec, step)
    log(f"Moving {len(neighbours) // 2 or len(neighbours)} numeric parameter(s) by +/-{step:.0%} ...")
    base_sharpe = robustness.annual_sharpe(base_daily)
    tested = []
    for params in neighbours:
        try:
            bt = ctx.backtest(params)
        except ValueError as exc:  # e.g. an opening range the bar size cannot express
            tested.append({"params": params, "skipped": str(exc)})
            continue
        sr = robustness.annual_sharpe(ctx.daily(bt, research_periods))
        tested.append({"params": params, "sharpe": sr})
        variants.append({"params": {**spec.strategy.params, **params}, "purpose": "neighbour", "sharpe": sr, "trades": len(bt.result.trades)})
    measured = [t for t in tested if "sharpe" in t and math.isfinite(t["sharpe"])]
    if base_sharpe > 0 and measured:
        worst = min(measured, key=lambda t: t["sharpe"])
        retained = worst["sharpe"] / base_sharpe
        detail = f"Worst neighbour {_variant_label(worst['params'])}: Sharpe {worst['sharpe']:.2f} vs {base_sharpe:.2f}."
    elif not measured:
        retained, detail = math.nan, "No numeric parameter could be moved."
    else:
        retained, detail = math.nan, f"The spec's own Sharpe ({base_sharpe:.2f}) is not positive: there is nothing to retain."

    log("Checking causality (look-ahead, and a random walk where no edge exists) ...")
    strat, _ = spec.strategy.build()
    look_ok, look_detail = lookahead.check(strat, ctx.data)
    tf = Timeframe.parse(spec.timeframe)
    cls = get_strategy_class(spec.strategy.id)
    null_syms = [f"NULL{i}" for i in range(max(cls.min_symbols, min(len(spec.symbols), 5)))]
    null_result = null.null_test(spec.strategy, null_syms, ctx.bar_minutes, tf.periods_per_year, seed)
    if not null_result["passed"]:
        look_ok = False
        look_detail = (f"on a random walk the strategy made money before costs (t = {null_result['t_stat']:.2f}), "
                       "which usually means it or the data sees the future")

    previous = runs.variants_tested(spec.key)
    seen = {json.dumps(v["params"], sort_keys=True): v for v in previous}
    for v in variants:
        seen.setdefault(json.dumps(v["params"], sort_keys=True), v)
    trial_sharpes = [v.get("sharpe") for v in seen.values() if isinstance(v.get("sharpe"), (int, float))]
    deflated = robustness.deflated_sharpe(base_daily, trial_sharpes, len(seen)) | {"n_trials": len(seen)}

    wf_cfg = research_cfg.get("walk_forward", {})
    wf = robustness.walk_forward(matrix, int(wf_cfg.get("train_days", 504)), int(wf_cfg.get("test_days", 126)))
    pbo = robustness.probability_of_backtest_overfitting(matrix, int(research_cfg.get("pbo_splits", 10)))
    mc = robustness.monte_carlo_drawdown(all_rets.to_numpy(), int(research_cfg.get("monte_carlo_resamples", 10_000)), seed)
    regimes = robustness.by_year(all_rets)
    research_fills = [f for f in base.result.fills if any(_in(f.time, spec.periods[p]) for p in research_periods)]
    capacity = robustness.participation(research_fills, ctx.data, ctx.bar_minutes)
    git = runs.git_state()
    results: dict[str, Any] = {
        "spec": {
            "frozen": spec.is_frozen,
            "frozen_on": spec.frozen.isoformat() if spec.frozen else None,
            "drift": spec.drift(),
            "committed": runs.committed(spec.path),
        },
        "universe": {
            "survivorship_free": bool(spec.universe.get("survivorship_free", False)),
            "real_data": provider not in ("synthetic",),
            "provider": provider,
        },
        "lookahead": {"passed": look_ok, "detail": look_detail, "null_test": null_result},
        "one_trade_per_day": spec.one_trade_per_day,
        "validation": {
            "expectancy": robustness.expectancy(val_rets.to_numpy(), val_r).to_dict(),
            "stressed_expectancy": robustness.expectancy(stressed_val.to_numpy(), stressed_r).to_dict(),
            "sharpe": robustness.annual_sharpe(ctx.daily(base, ("validation",))),
        },
        "train": {
            "expectancy": robustness.expectancy(ctx.trade_returns(base, ("train",))[0].to_numpy()).to_dict(),
            "sharpe": robustness.annual_sharpe(ctx.daily(base, ("train",))),
        },
        "walk_forward": None if wf is None else {k: v for k, v in wf.items() if k != "returns"},
        "deflated": deflated,
        "pbo": pbo,
        "locked_test": {"opened": False},
        "neighborhood": {"retained": retained, "detail": detail, "tested": tested},
        "monte_carlo": mc,
        "regimes": regimes,
        "capacity": capacity,
        "reproducible": {"clean": git["available"] and not git["dirty"], "dirty": git["dirty"]},
        "ambiguous_fills": base.result.ambiguous_fills,
        "risk_events": _count(base.result.risk_events),
        "warnings": base.warnings,
    }
    checks = checklist.evaluate(results, acceptance)
    verdict = checklist.verdict(checks)
    record: dict[str, Any] = {
        "id": run_id,
        "created": now.isoformat(),
        "spec": {"id": spec.id, "version": spec.version, "key": spec.key, "name": spec.name, "status": spec.status,
                 "frozen": results["spec"]["frozen_on"], "rules_hash": spec.computed_hash(), "path": str(spec.path.relative_to(config.repo_root()))},
        "git": git,
        "data": {"provider": provider, "timeframe": spec.timeframe, **runs.data_manifest(ctx.data)},
        "config": {"engine": ctx.cfg.to_dict(), "costs": costs.describe(ctx.cfg), "risk_limits": ctx.limits.to_dict(),
                   "acceptance": acceptance, "research": research_cfg},
        "seed": seed,
        "periods": {k: p.to_list() for k, p in spec.periods.items()},
        "results": results,
        "checklist": [c.to_dict() for c in checks],
        "verdict": verdict,
        "variants_tested": len(seen),
        "locked_test": {"opened": False},
    }

    if open_locked_test:
        if verdict != "ready_for_locked_test":
            failing = [c.n for c in checks if c.status == checklist.FAIL]
            record["locked_test"]["refused"] = f"checks {', '.join(map(str, failing))} failed; the locked test stays sealed"
            log(f"The locked test stays sealed: checks {', '.join(map(str, failing))} failed. Opening it now would waste it.")
        else:
            record = _open_locked_test(record, ctx, spec, provider, fetch, acceptance, now, write, log)

    if write:
        folder = runs.write_run(record, render(record), variants, {"equity": base.result.equity.iloc[ctx.eval_start :]})
        log(f"Wrote {folder.relative_to(config.repo_root())}/")
    log(checklist.VERDICT_TEXT[record["verdict"]])
    return record


def _count(events) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    return counts


def _open_locked_test(record, ctx: Context, spec, provider, fetch, acceptance, now, write, log) -> dict[str, Any]:
    """Record the opening first (a crash after this point still counts as opened), then test."""
    record["locked_test"] = {"opened": True, "opened_on": now.date().isoformat()}
    if write:
        runs.write_run({**record, "verdict": "opening"}, "Opening the locked test ...\n", [])
    log(f"Opening {spec.key}'s locked test ({spec.periods['test'].start} to {spec.periods['test'].end}). This happens once.")
    full = _context(spec, provider, fetch, "test")
    bt = full.backtest()
    test_daily = full.daily(bt, ("test",))
    val_daily = full.daily(bt, ("validation",))
    test_rets, _ = full.trade_returns(bt, ("test",))
    lt = {
        "opened": True,
        "opened_on": now.date().isoformat(),
        "sharpe": robustness.annual_sharpe(test_daily),
        "validation_sharpe": robustness.annual_sharpe(val_daily),
        "expectancy": robustness.expectancy(test_rets.to_numpy()).to_dict(),
        "data": runs.data_manifest(full.data)["hash"],
    }
    record["results"]["locked_test"] = lt
    record["locked_test"] = {"opened": True, "opened_on": lt["opened_on"]}
    checks = checklist.evaluate(record["results"], acceptance)
    record["checklist"] = [c.to_dict() for c in checks]
    record["verdict"] = checklist.verdict(checks)
    return record
