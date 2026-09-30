"""Performance metrics for equity curves and trade lists."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from trades.core import stats
from trades.core.ledger import Fill, Trade

# Definitions shown in the UI: label, explanation, display format, which direction is better.
METRIC_INFO: dict[str, dict[str, Any]] = {
    "total_return": {
        "label": "Total return",
        "fmt": "pct",
        "better": "higher",
        "help": "Percentage change of the account over the whole period, after costs.",
    },
    "cagr": {
        "label": "CAGR",
        "fmt": "pct",
        "better": "higher",
        "help": "Compound annual growth rate: the constant yearly return that would produce the same result.",
    },
    "volatility": {
        "label": "Volatility",
        "fmt": "pct",
        "better": "lower",
        "help": "Annualised standard deviation of returns -- how bumpy the ride is.",
    },
    "sharpe": {
        "label": "Sharpe ratio",
        "fmt": "ratio",
        "better": "higher",
        "help": "Annualised return per unit of volatility (risk-free rate assumed 0). Above 1 is good for a "
        "single strategy; be sceptical of very high values from backtests.",
    },
    "sortino": {
        "label": "Sortino ratio",
        "fmt": "ratio",
        "better": "higher",
        "help": "Like Sharpe but only penalises downside volatility.",
    },
    "max_drawdown": {
        "label": "Max drawdown",
        "fmt": "pct",
        "better": "higher",
        "help": "Largest peak-to-trough loss. Ask yourself honestly whether you could sit through it.",
    },
    "max_dd_duration": {
        "label": "Longest drawdown",
        "fmt": "bars",
        "better": "lower",
        "help": "Most bars spent below a previous equity high.",
    },
    "calmar": {
        "label": "Calmar ratio",
        "fmt": "ratio",
        "better": "higher",
        "help": "CAGR divided by the absolute max drawdown.",
    },
    "psr": {
        "label": "Prob. Sharpe > 0",
        "fmt": "pct",
        "better": "higher",
        "help": "Probabilistic Sharpe Ratio (Bailey & Lopez de Prado 2012): the probability the true Sharpe "
        "ratio is above zero, given sample length, skewness and fat tails. Below 95% means the result "
        "could plausibly be luck.",
    },
    "skew": {
        "label": "Skewness",
        "fmt": "ratio",
        "better": "higher",
        "help": "Asymmetry of returns. Negative = occasional large losses (e.g. selling insurance).",
    },
    "kurtosis": {
        "label": "Kurtosis",
        "fmt": "ratio",
        "better": "lower",
        "help": "Tail heaviness (normal = 3). High values mean extreme days are more common.",
    },
    "exposure": {
        "label": "Time in market",
        "fmt": "pct",
        "better": None,
        "help": "Share of bars with an open position.",
    },
    "avg_gross_exposure": {
        "label": "Avg. gross exposure",
        "fmt": "pct",
        "better": None,
        "help": "Average of |long| + |short| positions as a fraction of equity.",
    },
    "turnover": {
        "label": "Annual turnover",
        "fmt": "x",
        "better": "lower",
        "help": "Traded value per year divided by average equity. High turnover makes costs matter.",
    },
    "n_trades": {"label": "Trades", "fmt": "int", "better": None, "help": "Closed round-trip trades."},
    "win_rate": {
        "label": "Win rate",
        "fmt": "pct",
        "better": "higher",
        "help": "Share of closed trades that made money. Trend followers often win < 40% yet profit.",
    },
    "profit_factor": {
        "label": "Profit factor",
        "fmt": "ratio",
        "better": "higher",
        "help": "Gross profits divided by gross losses. Above 1 means winners outweigh losers.",
    },
    "payoff_ratio": {
        "label": "Avg win / avg loss",
        "fmt": "ratio",
        "better": "higher",
        "help": "Size of the average winning trade relative to the average losing trade.",
    },
    "expectancy": {
        "label": "Expectancy / trade",
        "fmt": "money",
        "better": "higher",
        "help": "Average profit per closed trade after costs.",
    },
    "avg_bars_held": {
        "label": "Avg. holding",
        "fmt": "bars",
        "better": None,
        "help": "Average number of bars a trade stayed open.",
    },
    "total_costs": {
        "label": "Costs paid",
        "fmt": "money",
        "better": "lower",
        "help": "Commissions plus estimated slippage.",
    },
    "kelly": {
        "label": "Kelly leverage",
        "fmt": "x",
        "better": None,
        "help": "Growth-optimal leverage mu/sigma^2 estimated from returns. Extremely sensitive to estimation "
        "error; practitioners use half or less.",
    },
    "beta": {
        "label": "Beta vs benchmark",
        "fmt": "ratio",
        "better": None,
        "help": "Sensitivity to the benchmark's returns (1 = moves with it).",
    },
    "alpha": {
        "label": "Alpha (annual)",
        "fmt": "pct",
        "better": "higher",
        "help": "Annualised return not explained by benchmark exposure (CAPM regression intercept).",
    },
    "correlation": {
        "label": "Correlation",
        "fmt": "ratio",
        "better": None,
        "help": "Correlation of returns with the benchmark.",
    },
    "excess_cagr": {
        "label": "CAGR vs benchmark",
        "fmt": "pct",
        "better": "higher",
        "help": "Strategy CAGR minus benchmark CAGR.",
    },
}


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def performance_metrics(
    equity: pd.Series,
    periods_per_year: int,
    trades: list[Trade] | None = None,
    fills: list[Fill] | None = None,
    gross_exposure: pd.Series | None = None,
    benchmark: pd.Series | None = None,
) -> dict[str, float | None]:
    eq = equity.dropna()
    out: dict[str, float | None] = {}
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return {k: None for k in METRIC_INFO}
    rets = eq.pct_change(fill_method=None).dropna()
    n = len(rets)
    years = n / periods_per_year
    total = eq.iloc[-1] / eq.iloc[0] - 1.0
    out["total_return"] = total
    out["cagr"] = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1.0 if years > 0 and eq.iloc[-1] > 0 else None
    out["volatility"] = float(rets.std(ddof=1) * math.sqrt(periods_per_year)) if n > 1 else None
    out["sharpe"] = stats.sharpe_ratio(rets, periods_per_year)
    out["sortino"] = stats.sortino_ratio(rets, periods_per_year)
    dd = stats.max_drawdown(eq)
    out["max_drawdown"] = dd.max_drawdown
    out["max_dd_duration"] = dd.longest_duration
    out["calmar"] = (
        out["cagr"] / abs(dd.max_drawdown) if out["cagr"] is not None and dd.max_drawdown < 0 else None
    )
    sk, ku = stats.skewness(rets), stats.kurtosis(rets)
    out["skew"], out["kurtosis"] = sk, ku
    sr_pp = out["sharpe"] / math.sqrt(periods_per_year) if _f(out["sharpe"]) is not None else float("nan")
    out["psr"] = stats.probabilistic_sharpe_ratio(sr_pp, n, sk, ku)
    out["kelly"] = stats.kelly_leverage(rets)

    if gross_exposure is not None:
        ge = gross_exposure.reindex(eq.index).fillna(0.0)
        out["exposure"] = float((ge > 1e-9).mean())
        out["avg_gross_exposure"] = float(ge.mean())
    if fills is not None:
        traded = sum(abs(f.qty * f.price) for f in fills)
        out["turnover"] = traded / float(eq.mean()) / years if years > 0 else None
        out["total_costs"] = sum(f.commission + f.slippage for f in fills)

    if trades is not None:
        closed = [t for t in trades if not t.is_open]
        out["n_trades"] = len(closed)
        pnls = np.array([t.pnl for t in closed], dtype=float)
        wins, losses = pnls[pnls > 0], pnls[pnls < 0]  # break-even trades are neither
        out["win_rate"] = float(len(wins) / len(pnls)) if len(pnls) else None
        gross_loss = -losses.sum()
        out["profit_factor"] = float(wins.sum() / gross_loss) if gross_loss > 0 else None
        out["payoff_ratio"] = (
            float(wins.mean() / -losses.mean()) if len(wins) and len(losses) and losses.mean() < 0 else None
        )
        out["expectancy"] = float(pnls.mean()) if len(pnls) else None
        held = [t.bars_held for t in closed if t.bars_held is not None]
        out["avg_bars_held"] = float(np.mean(held)) if held else None

    if benchmark is not None:
        b = benchmark.reindex(eq.index).dropna()
        br = b.pct_change(fill_method=None).dropna()
        joined = pd.concat([rets, br], axis=1, join="inner").dropna()
        if len(joined) > 2 and joined.iloc[:, 1].var() > 0:
            x, y = joined.iloc[:, 1].to_numpy(), joined.iloc[:, 0].to_numpy()
            beta = float(np.cov(y, x, ddof=1)[0, 1] / np.var(x, ddof=1))
            out["beta"] = beta
            out["alpha"] = float((y.mean() - beta * x.mean()) * periods_per_year)
            out["correlation"] = float(np.corrcoef(x, y)[0, 1]) if np.std(y) > 0 else None
        b_years = (len(b) - 1) / periods_per_year
        if len(b) > 1 and b_years > 0 and out["cagr"] is not None and b.iloc[-1] > 0:
            out["excess_cagr"] = out["cagr"] - ((b.iloc[-1] / b.iloc[0]) ** (1 / b_years) - 1.0)
    return {k: _f(v) for k, v in out.items()}


def monthly_returns(equity: pd.Series) -> list[dict[str, Any]]:
    """[{year, months: [12 x return|None], total}] from an equity curve."""
    eq = equity.dropna()
    if len(eq) < 2:
        return []
    idx = eq.index.tz_localize(None) if getattr(eq.index, "tz", None) is not None else eq.index
    s = pd.Series(eq.to_numpy(), index=idx)
    month_end = s.groupby([s.index.year, s.index.month]).last()
    first_val = s.iloc[0]
    rows: dict[int, list[float | None]] = {}
    prev = first_val
    for (y, m), v in month_end.items():
        rows.setdefault(int(y), [None] * 12)[int(m) - 1] = float(v / prev - 1.0)
        prev = v
    out = []
    for y in sorted(rows):
        vals = [v for v in rows[y] if v is not None]
        total = float(np.prod([1 + v for v in vals]) - 1.0) if vals else None
        out.append({"year": y, "months": rows[y], "total": total})
    return out


def drawdown(equity: pd.Series) -> pd.Series:
    return stats.drawdown_series(equity)
