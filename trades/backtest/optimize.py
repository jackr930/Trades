"""Parameter search with honest statistics.

* ``grid_search`` evaluates every parameter combination on the same sample and reports
  the Deflated Sharpe Ratio of the winner: the probability its Sharpe ratio is real
  after accounting for how many combinations were tried (Bailey & Lopez de Prado 2014).
* ``walk_forward`` re-optimises on a rolling training window and trades the chosen
  parameters only on the *following*, unseen window, stitching those out-of-sample
  periods into one equity curve. Comparing in-sample and out-of-sample results shows
  how much of an optimised backtest is curve-fitting.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pandas as pd

from trades.backtest.engine import BacktestConfig, run_backtest
from trades.backtest.metrics import performance_metrics
from trades.backtest.runner import _ts, _vals
from trades.core import stats
from trades.data.base import align_bars
from trades.strategies import create_strategy, get_strategy_class
from trades.strategies.sizing import SizingConfig, apply_sizing

MAX_COMBOS = 400
OBJECTIVES = ("sharpe", "cagr", "calmar", "sortino", "total_return")
OBJECTIVE_LABELS = {
    "sharpe": "Sharpe ratio",
    "cagr": "CAGR",
    "calmar": "Calmar ratio",
    "sortino": "Sortino ratio",
    "total_return": "total return",
}
SUMMARY_KEYS = (
    "sharpe",
    "cagr",
    "total_return",
    "max_drawdown",
    "calmar",
    "sortino",
    "n_trades",
    "psr",
    "win_rate",
)


def expand_grid(grid: dict[str, list[Any]], limit: int = MAX_COMBOS) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    names = list(grid)
    values = [list(dict.fromkeys(grid[n])) for n in names]
    total = math.prod(len(v) for v in values)
    if total > limit:
        raise ValueError(
            f"{total} parameter combinations requested; the limit is {limit}. Use coarser steps."
        )
    return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*values)]


def range_values(lo: float, hi: float, step: float, integer: bool) -> list[float]:
    if step <= 0 or hi < lo:
        raise ValueError("invalid range")
    n = int(math.floor((hi - lo) / step + 1e-9)) + 1
    vals = [lo + i * step for i in range(n)]
    return [int(round(v)) for v in vals] if integer else [round(v, 10) for v in vals]


def _objective(m: dict[str, float | None], objective: str) -> float:
    v = m.get(objective)
    return -math.inf if v is None or not math.isfinite(v) else float(v)


def _capped(cfg: BacktestConfig, strategy_id: str, sizing: dict[str, Any] | None) -> BacktestConfig:
    cls = get_strategy_class(strategy_id)
    return cfg.capped(SizingConfig.from_dict(sizing, cls.default_sizing).max_leverage)


def _prepare(strategy_id, base_params, combos, data, sizing, config):
    """Build (combo, strategy, weights) for every valid combination on aligned data."""
    cls = get_strategy_class(strategy_id)
    aligned = align_bars(data)
    ppy = config.periods_per_year
    prepared, rejected = [], []
    for combo in combos:
        params = {**(base_params or {}), **combo}
        try:
            strat = create_strategy(strategy_id, params)
        except ValueError as exc:
            rejected.append({"params": combo, "error": str(exc)})
            continue
        sz = SizingConfig.from_dict(sizing, cls.default_sizing)
        out = strat.run(aligned)
        weights = apply_sizing(out.signals, aligned, sz, strat.kind, ppy)
        prepared.append((combo, strat, weights))
    return aligned, prepared, rejected


def grid_search(
    strategy_id: str,
    base_params: dict[str, Any] | None,
    grid: dict[str, list[Any]],
    data: dict[str, pd.DataFrame],
    sizing: dict[str, Any] | None = None,
    config: BacktestConfig | None = None,
    objective: str = "sharpe",
    eval_start: int | None = None,
) -> dict[str, Any]:
    if objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {OBJECTIVES}")
    cfg = _capped(config or BacktestConfig(), strategy_id, sizing)
    combos = expand_grid(grid)
    aligned, prepared, rejected = _prepare(strategy_id, base_params, combos, data, sizing, cfg)
    if not prepared:
        raise ValueError("no valid parameter combinations")
    T = len(next(iter(aligned.values())))
    warm = max(s.warmup() for _, s, _ in prepared) - 1
    start = max(warm, eval_start or 0)
    if start >= T - 2:
        raise ValueError("not enough history for the largest lookback in the grid")
    rows, sr_pp, best_i = [], [], None
    for i, (combo, _strat, weights) in enumerate(prepared):
        res = run_backtest(aligned, weights, cfg, start)
        eq = res.equity.iloc[start:]
        m = performance_metrics(eq, cfg.periods_per_year, res.trades, res.fills)
        rets = eq.pct_change(fill_method=None).dropna()
        row = {"params": combo, "metrics": {k: m.get(k) for k in SUMMARY_KEYS}}
        rows.append(row)
        s = m.get("sharpe")
        sr_pp.append(s / math.sqrt(cfg.periods_per_year) if s is not None else np.nan)
        row["_rets"] = rets
        if best_i is None or _objective(m, objective) > _objective(rows[best_i]["metrics"], objective):
            best_i = i
    assert best_i is not None
    best = rows[best_i]
    finite = np.array([x for x in sr_pp if np.isfinite(x)])
    n_trials = len(finite)
    var_sr = float(np.var(finite, ddof=1)) if n_trials > 1 else 0.0
    best_rets = best.pop("_rets")
    for r in rows:
        r.pop("_rets", None)
    best_sr = sr_pp[best_i]
    dsr = (
        stats.deflated_sharpe_ratio(
            best_sr, len(best_rets), stats.skewness(best_rets), stats.kurtosis(best_rets), n_trials, var_sr
        )
        if np.isfinite(best_sr)
        else None
    )
    psr = (
        stats.probabilistic_sharpe_ratio(
            best_sr, len(best_rets), stats.skewness(best_rets), stats.kurtosis(best_rets)
        )
        if np.isfinite(best_sr)
        else None
    )
    e_max = stats.expected_max_sharpe(n_trials, var_sr) * math.sqrt(cfg.periods_per_year)
    return {
        "objective": objective,
        "rows": rows,
        "rejected": rejected,
        "best": best,
        "n_trials": n_trials,
        "deflated_sharpe": _clean(dsr),
        "psr_best": _clean(psr),
        "expected_max_sharpe_under_null": _clean(e_max),
        "start_time": int(aligned[next(iter(aligned))].index[start].timestamp()),
        "interpretation": dsr_interpretation(_clean(dsr), n_trials),
    }


def dsr_interpretation(dsr: float | None, n_trials: int) -> str:
    if dsr is None:
        return "Not enough information to compute the Deflated Sharpe Ratio."
    if n_trials <= 1:
        return "Only one configuration was tested, so no multiple-testing correction applies."
    if dsr >= 0.95:
        return (
            f"After correcting for {n_trials} trials, the best configuration's Sharpe ratio is still "
            f"significant (DSR {dsr:.0%}). Confirm with walk-forward testing before trusting it."
        )
    return (
        f"After correcting for {n_trials} trials, there is only a {dsr:.0%} probability that the best "
        "configuration's Sharpe ratio reflects real skill rather than the luck of picking the maximum. "
        "Treat the 'best' parameters with suspicion."
    )


def _clean(x):
    return None if x is None or not math.isfinite(x) else float(x)


def walk_forward(
    strategy_id: str,
    base_params: dict[str, Any] | None,
    grid: dict[str, list[Any]],
    data: dict[str, pd.DataFrame],
    sizing: dict[str, Any] | None = None,
    config: BacktestConfig | None = None,
    objective: str = "sharpe",
    train_bars: int = 756,
    test_bars: int = 252,
    anchored: bool = False,
) -> dict[str, Any]:
    if objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {OBJECTIVES}")
    if train_bars < 60 or test_bars < 20:
        raise ValueError("training window must be >= 60 bars and test window >= 20 bars")
    cfg = _capped(config or BacktestConfig(), strategy_id, sizing)
    combos = expand_grid(grid, limit=200)
    aligned, prepared, rejected = _prepare(strategy_id, base_params, combos, data, sizing, cfg)
    if not prepared:
        raise ValueError("no valid parameter combinations")
    index = next(iter(aligned.values())).index
    T = len(index)
    warm = max(s.warmup() for _, s, _ in prepared) - 1
    first = max(warm, 0)
    if first + train_bars + test_bars > T:
        raise ValueError(
            f"Need at least {first + train_bars + test_bars} bars (warm-up {first} + train {train_bars} + "
            f"test {test_bars}); only {T} available."
        )
    windows = []
    stitched = np.zeros((T, len(aligned)))
    s = first
    while s + train_bars + test_bars <= T:
        tr0 = first if anchored else s
        tr1, te1 = s + train_bars, s + train_bars + test_bars
        best = None
        for combo, _strat, weights in prepared:
            sub = {k: v.iloc[tr0:tr1] for k, v in aligned.items()}
            res = run_backtest(sub, weights.iloc[tr0:tr1], cfg, 0)
            m = performance_metrics(res.equity, cfg.periods_per_year, res.trades, res.fills)
            score = _objective(m, objective)
            if best is None or score > best[0]:
                best = (score, combo, weights, m)
        assert best is not None
        score, combo, weights, m_is = best
        # Decisions taken at closes tr1-1 .. te1-2 are the ones held during test bars tr1 .. te1-1;
        # they use parameters chosen on data up to bar tr1-1 only.
        stitched[tr1 - 1 : te1 - 1] = weights.iloc[tr1 - 1 : te1 - 1].to_numpy(float)
        windows.append(
            {
                "train_start": int(index[tr0].timestamp()),
                "train_end": int(index[tr1 - 1].timestamp()),
                "test_start": int(index[tr1].timestamp()),
                "test_end": int(index[te1 - 1].timestamp()),
                "best_params": combo,
                "in_sample": {k: m_is.get(k) for k in SUMMARY_KEYS},
                "_range": (tr1, te1),
            }
        )
        s += test_bars
    oos_start = first + train_bars
    oos_end = windows[-1]["_range"][1]
    cols = list(aligned)
    w_df = pd.DataFrame(stitched, index=index, columns=cols)
    # Start one bar early: the first out-of-sample position is decided at the prior close.
    sub = {k: v.iloc[oos_start - 1 : oos_end] for k, v in aligned.items()}
    res = run_backtest(sub, w_df.iloc[oos_start - 1 : oos_end], cfg, 0)
    eq = res.equity
    oos_m = performance_metrics(eq, cfg.periods_per_year, res.trades, res.fills, res.gross_exposure)
    for w in windows:
        a, b = w.pop("_range")
        seg = eq.loc[index[a - 1] : index[b - 1]]
        w["out_of_sample"] = performance_metrics(seg, cfg.periods_per_year)
        w["out_of_sample"] = {
            k: w["out_of_sample"].get(k) for k in ("sharpe", "total_return", "max_drawdown", "cagr")
        }

    # Baseline: the strategy's default parameters over the same out-of-sample period.
    default_strat = create_strategy(strategy_id, base_params or {})
    cls = get_strategy_class(strategy_id)
    d_out = default_strat.run(aligned)
    d_w = apply_sizing(
        d_out.signals,
        aligned,
        SizingConfig.from_dict(sizing, cls.default_sizing),
        default_strat.kind,
        cfg.periods_per_year,
    )
    d_res = run_backtest(sub, d_w.iloc[oos_start - 1 : oos_end], cfg, 0)
    d_m = performance_metrics(d_res.equity, cfg.periods_per_year, d_res.trades, d_res.fills)

    # Compare like with like: a total return over a 3-year training window and one over the whole
    # stitched test period differ in length, so the total-return objective is compared annualised.
    compare = "cagr" if objective == "total_return" else objective
    is_scores = [w["in_sample"].get(compare) for w in windows if w["in_sample"].get(compare) is not None]
    is_mean = float(np.mean(is_scores)) if is_scores else None
    oos_score = oos_m.get(compare)
    efficiency = oos_score / is_mean if (is_mean and oos_score is not None and is_mean > 0) else None
    return {
        "objective": objective,
        "windows": windows,
        "rejected": rejected,
        "oos_equity": {"t": _ts(eq.index), "v": _vals(eq, 2)},
        "default_equity": {"t": _ts(d_res.equity.index), "v": _vals(d_res.equity, 2)},
        "oos_metrics": oos_m,
        "default_metrics": d_m,
        "in_sample_mean": is_mean,
        "efficiency": efficiency,
        "compared_on": compare,
        "interpretation": wf_interpretation(compare, is_mean, oos_score, d_m.get(compare)),
    }


def wf_interpretation(objective: str, is_mean, oos, default_oos) -> str:
    if is_mean is None or oos is None:
        return "Not enough data to compare in-sample and out-of-sample results."
    label = OBJECTIVE_LABELS.get(objective, objective)
    fmt = "{:.1%}" if objective in ("cagr", "total_return") else "{:.2f}"
    parts = [
        f"Average in-sample {label}: {fmt.format(is_mean)}; stitched out-of-sample {label}: {fmt.format(oos)}."
    ]
    if is_mean > 0 and oos < 0.5 * is_mean:
        parts.append(
            "Out-of-sample performance fell by more than half: much of the in-sample result was curve-fitting."
        )
    elif is_mean > 0:
        parts.append("Out-of-sample performance held up reasonably well.")
    if default_oos is not None:
        better = oos > default_oos
        parts.append(
            f"Re-optimising {'beat' if better else 'did not beat'} simply keeping the default parameters "
            f"({fmt.format(default_oos)}) over the same unseen periods."
        )
    return " ".join(parts)
