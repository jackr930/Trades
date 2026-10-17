"""Robustness tests: is a backtest's edge real, stable and tradable?

Each function takes plain inputs (per-trade returns, daily returns, fills) so it can be tested
on its own and reused by the acceptance checklist (``trades.validation.checklist``).

References
----------
* Bailey, D. H., Borwein, J., Lopez de Prado, M. & Zhu, Q. J. (2017). The Probability of Backtest
  Overfitting. Journal of Computational Finance 20(4) -- combinatorially symmetric cross-validation.
* Bailey, D. H. & Lopez de Prado, M. (2014). The Deflated Sharpe Ratio. Journal of Portfolio
  Management 40(5).
* Pardo, R. (2008). The Evaluation and Optimization of Trading Strategies, 2nd ed. Wiley --
  walk-forward analysis.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from trades.validation import stats


@dataclass(frozen=True)
class Expectancy:
    n: int
    mean: float  # average net result per trade, as a fraction of equity at entry
    std: float
    t_stat: float
    hit_rate: float
    mean_r: float | None  # average result in R (multiples of the risk at the stop), when stops exist

    def to_dict(self) -> dict:
        return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in self.__dict__.items()}


def expectancy(returns: Sequence[float], r_multiples: Sequence[float] | None = None) -> Expectancy:
    """Per-trade expectancy and its t-statistic (mean / standard error)."""
    r = np.asarray([x for x in returns if math.isfinite(x)], dtype=float)
    n = len(r)
    mean = float(r.mean()) if n else math.nan
    std = float(r.std(ddof=1)) if n > 1 else math.nan
    t = mean / (std / math.sqrt(n)) if n > 1 and std > 0 else math.nan
    hit = float((r > 0).mean()) if n else math.nan
    rm = None
    if r_multiples is not None:
        rr = np.asarray([x for x in r_multiples if math.isfinite(x)], dtype=float)
        rm = float(rr.mean()) if len(rr) else None
    return Expectancy(n, mean, std, t, hit, rm)


def daily_returns(equity: pd.Series) -> pd.Series:
    """One return per trading day from an equity curve of any bar size (the last value of each day)."""
    eq = equity.dropna()
    if eq.empty:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex(eq.index)
    days = idx.tz_convert("America/New_York").normalize() if idx.tz is not None else idx.normalize()
    # Daily bars are stamped 00:00 UTC of their date; converting them would shift them a day back.
    if (idx.hour == 0).all() and (idx.minute == 0).all():
        days = idx.tz_localize(None).normalize() if idx.tz is not None else idx.normalize()
    daily = pd.Series(eq.to_numpy(), index=days).groupby(level=0).last()
    return daily.pct_change(fill_method=None).dropna()


def annual_sharpe(daily: pd.Series | np.ndarray) -> float:
    return stats.sharpe_ratio(daily, 252)


def monte_carlo_drawdown(trade_returns: Sequence[float], resamples: int = 10_000, seed: int = 7) -> dict[str, float]:
    """Resample the trades with replacement ``resamples`` times and compound them: the spread of
    maximum drawdowns says how bad the same edge could have looked with another order of luck."""
    r = np.asarray([x for x in trade_returns if math.isfinite(x)], dtype=float)
    if len(r) < 2:
        return {"p50": math.nan, "p95": math.nan, "p99": math.nan, "worst": math.nan}
    rng = np.random.default_rng(seed)
    out = np.empty(resamples)
    chunk = 1000  # bounded memory for long trade lists
    for start in range(0, resamples, chunk):
        k = min(chunk, resamples - start)
        draws = r[rng.integers(0, len(r), size=(k, len(r)))]
        curve = np.cumprod(1.0 + np.clip(draws, -0.999999, None), axis=1)
        peaks = np.maximum.accumulate(np.concatenate([np.ones((k, 1)), curve], axis=1), axis=1)[:, 1:]
        out[start : start + k] = (1.0 - curve / peaks).max(axis=1)
    return {
        "p50": float(np.percentile(out, 50)),
        "p95": float(np.percentile(out, 95)),
        "p99": float(np.percentile(out, 99)),
        "worst": float(out.max()),
    }


def probability_of_backtest_overfitting(returns: pd.DataFrame, splits: int = 10) -> dict[str, float] | None:
    """PBO by combinatorially symmetric cross-validation (Bailey et al. 2017).

    ``returns``: one column per parameter variant, one row per day. The rows are cut into
    ``splits`` blocks; for every way of choosing half of them as in-sample, the variant with the
    best in-sample Sharpe ratio is ranked out of sample. PBO is the share of those choices in
    which the in-sample winner lands in the bottom half out of sample: the chance that picking
    the best backtest picked noise. None when there are fewer than two variants or too little data.
    """
    m = returns.dropna(how="all").fillna(0.0).to_numpy(float)
    T, N = m.shape
    if N < 2 or splits < 2 or splits % 2 or T < splits * 5:
        return None
    blocks = np.array_split(np.arange(T), splits)
    logits = []
    for chosen in itertools.combinations(range(splits), splits // 2):
        is_rows = np.concatenate([blocks[i] for i in chosen])
        oos_rows = np.concatenate([blocks[i] for i in range(splits) if i not in chosen])
        is_sr = _sharpe_columns(m[is_rows])
        oos_sr = _sharpe_columns(m[oos_rows])
        best = int(np.nanargmax(np.nan_to_num(is_sr, nan=-np.inf)))
        rank = pd.Series(oos_sr).rank(method="average").iloc[best]  # 1 = worst ... N = best
        omega = rank / (N + 1)
        logits.append(math.log(omega / (1 - omega)))
    lam = np.array(logits)
    return {"pbo": float((lam <= 0).mean()), "combinations": len(lam), "median_logit": float(np.median(lam))}


def _sharpe_columns(m: np.ndarray) -> np.ndarray:
    mu = m.mean(axis=0)
    sd = m.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 0, mu / sd, np.nan)


def walk_forward(returns: pd.DataFrame, train_days: int, test_days: int) -> dict | None:
    """Re-choose the variant with the best Sharpe ratio on each ``train_days`` window and hold it for
    the next ``test_days``, stitching only those unseen days together. Works on daily returns of
    variants backtested once over the whole period: exact for strategies that are flat every night,
    a close approximation for ones that carry positions across a window boundary."""
    r = returns.dropna(how="all").fillna(0.0)
    T = len(r)
    if T < train_days + test_days:
        return None
    pieces, choices = [], []
    start = 0
    while start + train_days + test_days <= T:
        train = r.iloc[start : start + train_days]
        test = r.iloc[start + train_days : start + train_days + test_days]
        sr = _sharpe_columns(train.to_numpy(float))
        best = r.columns[int(np.nanargmax(np.nan_to_num(sr, nan=-np.inf)))]
        pieces.append(test[best])
        choices.append({"from": str(test.index[0].date()), "to": str(test.index[-1].date()), "variant": str(best)})
        start += test_days
    stitched = pd.concat(pieces)
    return {"sharpe": annual_sharpe(stitched), "days": len(stitched), "windows": choices, "returns": stitched}


def by_year(trade_returns: pd.Series) -> dict:
    """Per calendar year: total result, the share of years that made money, and the largest share
    of the total profit any single year contributed (a strategy that earned it all in one year is a
    bet on that year)."""
    if trade_returns.empty:
        return {"years": {}, "profitable_share": math.nan, "max_year_share": math.nan}
    years = trade_returns.groupby(pd.DatetimeIndex(trade_returns.index).year).sum()
    total = float(years.sum())
    share = float((years > 0).mean())
    biggest = float(years.max() / total) if total > 0 else math.inf
    return {"years": {int(y): float(v) for y, v in years.items()}, "profitable_share": share, "max_year_share": biggest}


def participation(fills, data: dict[str, pd.DataFrame], bar_minutes: int | None) -> dict[str, float]:
    """Order size as a share of the traded volume per minute in the bar each entry filled in."""
    minutes = bar_minutes or 390
    shares = []
    for f in fills:
        if f.tag not in ("market", "stop_entry", ""):
            continue
        df = data.get(f.symbol)
        if df is None or f.index >= len(df):
            continue
        per_minute = float(df["volume"].iloc[f.index]) / minutes
        shares.append(abs(f.qty) / per_minute if per_minute > 0 else math.inf)
    if not shares:
        return {"p95": math.nan, "max": math.nan, "n": 0}
    a = np.asarray(shares)
    return {"p95": float(np.percentile(a, 95)), "max": float(a.max()), "n": len(a)}


def deflated_sharpe(daily: pd.Series, trial_sharpes: Sequence[float], n_trials: int) -> dict[str, float]:
    """The Deflated Sharpe Ratio of ``daily`` returns given ``n_trials`` variants tried, whose
    annual Sharpe ratios were ``trial_sharpes`` (their spread sets how lucky the best could look)."""
    r = np.asarray(daily, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 3:
        return {"dsr": math.nan, "sharpe": math.nan, "expected_max_sharpe": math.nan}
    sr_pp = stats.sharpe_ratio(r, 1)
    trials = np.asarray([s for s in trial_sharpes if math.isfinite(s)], dtype=float) / math.sqrt(252)
    var = float(np.var(trials, ddof=1)) if len(trials) > 1 else 0.0
    dsr = stats.deflated_sharpe_ratio(sr_pp, len(r), stats.skewness(r), stats.kurtosis(r), max(n_trials, 1), var)
    e_max = stats.expected_max_sharpe(max(n_trials, 1), var) * math.sqrt(252)
    return {"dsr": dsr, "sharpe": sr_pp * math.sqrt(252), "expected_max_sharpe": e_max}
