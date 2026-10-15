"""How much of a backtest result is the luck of timing?

One backtest is one path through history. This module asks how the comparison with the
benchmark changes when you look at it differently:

* **rolling windows**: every 3-year window, stepped quarterly: in what share did the
  strategy beat the benchmark, and by how much at worst and best?
* **start dates**: had you started following it at the start of any quarter, how often
  would you be ahead of the benchmark today?
* **market regimes**: returns of both while the benchmark was near its highs, in a
  correction, or in a bear market, and in calm versus volatile stretches. The labels use
  the benchmark's own drawdown and recent volatility; they describe the past, they are not
  trading signals;
* **bootstrap ranges**: 5th to 95th percentile of CAGR and maximum drawdown across 1,000
  block-bootstrap resamples (21-day blocks, the same days for both), a sense of how
  different the same strategy's history could plausibly have looked.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

QUARTER = 63  # bars


def _cagr(a: float, b: float, years: float) -> float | None:
    if years <= 0 or a <= 0 or b <= 0:
        return None
    return (b / a) ** (1 / years) - 1


def rolling_windows(
    strategy: pd.Series, benchmark: pd.Series, ppy: int, years: int = 3, step: int = QUARTER
) -> dict[str, Any]:
    """CAGR of both over every ``years``-long window, starting every ``step`` bars."""
    s, b = strategy.to_numpy(float), benchmark.to_numpy(float)
    n = years * ppy
    rows = []
    for i in range(0, len(s) - n, step):
        cs, cb = _cagr(s[i], s[i + n], years), _cagr(b[i], b[i + n], years)
        if cs is not None and cb is not None:
            rows.append({"start": strategy.index[i], "end": strategy.index[i + n], "strategy": cs, "benchmark": cb})
    if not rows:
        return {"years": years, "windows": 0}
    excess = np.array([r["strategy"] - r["benchmark"] for r in rows])
    return {
        "years": years,
        "windows": len(rows),
        "share_beating": float(np.mean(excess > 0)),
        "median_excess": float(np.median(excess)),
        "worst_excess": float(excess.min()),
        "best_excess": float(excess.max()),
        "rows": rows,
    }


def start_dates(
    strategy: pd.Series, benchmark: pd.Series, ppy: int, step: int = QUARTER, min_years: float = 3.0
) -> dict[str, Any]:
    """From each quarterly start with at least ``min_years`` left: CAGR of both to the end."""
    s, b = strategy.to_numpy(float), benchmark.to_numpy(float)
    rows = []
    for i in range(0, len(s), step):
        years = (len(s) - 1 - i) / ppy
        if years < min_years:
            break
        cs, cb = _cagr(s[i], s[-1], years), _cagr(b[i], b[-1], years)
        if cs is not None and cb is not None:
            rows.append({"start": strategy.index[i], "strategy": cs, "benchmark": cb})
    if not rows:
        return {"starts": 0}
    ahead = np.array([r["strategy"] > r["benchmark"] for r in rows])
    return {"starts": len(rows), "share_ahead": float(ahead.mean()), "rows": rows}


def regimes(strategy: pd.Series, benchmark: pd.Series, ppy: int) -> list[dict[str, Any]]:
    """Annualised return of both in each market regime, as labelled by the benchmark."""
    rs = np.log(strategy).diff().iloc[1:]
    rb = np.log(benchmark).diff().iloc[1:]
    dd = (benchmark / benchmark.cummax() - 1).iloc[1:]
    vol = benchmark.pct_change().rolling(QUARTER).std().iloc[1:]
    lo, hi = vol.quantile(1 / 3), vol.quantile(2 / 3)
    labels = {
        "Near highs (benchmark within 10% of its peak)": dd > -0.10,
        "Correction (10-20% below the peak)": (dd <= -0.10) & (dd > -0.20),
        "Bear market (more than 20% below the peak)": dd <= -0.20,
        "Calm (lowest third of 3-month volatility)": vol <= lo,
        "Volatile (highest third of 3-month volatility)": vol >= hi,
    }
    out = []
    for name, mask in labels.items():
        days = int(mask.sum())
        if days == 0:
            continue
        out.append(
            {
                "regime": name,
                "share_of_time": days / len(mask),
                "strategy": float(math.exp(rs[mask].mean() * ppy) - 1),
                "benchmark": float(math.exp(rb[mask].mean() * ppy) - 1),
            }
        )
    return out


def bootstrap_ranges(
    strategy: pd.Series, benchmark: pd.Series, ppy: int, n: int = 1000, block: int = 21, seed: int = 11
) -> dict[str, Any]:
    """5th, 50th and 95th percentiles of CAGR and max drawdown across block-bootstrap paths."""
    a = np.log(strategy).diff().iloc[1:].to_numpy(float)
    b = np.log(benchmark).diff().iloc[1:].to_numpy(float)
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    T = len(a)
    if T < 4 * block:
        return {}
    rng = np.random.default_rng(seed)
    n_blocks = -(-T // block)
    cagr = {"strategy": [], "benchmark": []}
    mdd = {"strategy": [], "benchmark": []}
    for chunk in range(0, n, 200):
        k = min(200, n - chunk)
        starts = rng.integers(0, T, size=(k, n_blocks))
        idx = ((starts[:, :, None] + np.arange(block)) % T).reshape(k, -1)[:, :T]
        for name, r in (("strategy", a[idx]), ("benchmark", b[idx])):
            path = np.cumsum(r, axis=1)
            cagr[name] += list(np.exp(path[:, -1] * ppy / T) - 1)
            peak = np.maximum.accumulate(np.maximum(path, 0.0), axis=1)
            mdd[name] += list(np.exp(np.min(path - peak, axis=1)) - 1)
    q = (5, 50, 95)
    return {
        name: {"cagr": [float(x) for x in np.percentile(cagr[name], q)], "max_drawdown": [float(x) for x in np.percentile(mdd[name], q)]}
        for name in ("strategy", "benchmark")
    }


def report(strategy: pd.Series, benchmark: pd.Series, ppy: int) -> dict[str, Any]:
    """All four views, for equity curves of the strategy and its benchmark over the same bars."""
    both = pd.concat([strategy, benchmark], axis=1, join="inner").dropna()
    both = both[(both > 0).all(axis=1)]
    if len(both) < 2 * ppy:
        return {"note": "Robustness checks need at least two years of results."}
    s, b = both.iloc[:, 0], both.iloc[:, 1]
    return {
        "rolling": rolling_windows(s, b, ppy),
        "starts": start_dates(s, b, ppy),
        "regimes": regimes(s, b, ppy),
        "bootstrap": bootstrap_ranges(s, b, ppy),
    }
