"""The null test: a strategy run on a random walk, where no edge can exist.

If a strategy makes money *before costs* on prices that are a pure random walk, the money came
from a bug (usually look-ahead in the strategy or the data) rather than from the market. The bars
are generated here, at the strategy's own bar size and session calendar, with constant volatility,
no drift, no regimes and no intraday pattern. A healthy strategy's gross per-trade results should
look like noise: a t-statistic between -2 and 2.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from trades.data.calendar import session_bounds, trading_days
from trades.engine.backtest import backtest_strategy
from trades.execution.sim_broker import BacktestConfig
from trades.strategies import StrategyConfig
from trades.validation.robustness import expectancy


def random_walk_bars(symbols: list[str], bar_minutes: int | None, sessions: int = 250, seed: int = 7,
                     daily_vol: float = 0.015, end: date = date(2025, 12, 31)) -> dict[str, pd.DataFrame]:
    """Martingale OHLCV bars for ``symbols``: daily bars when ``bar_minutes`` is None."""
    days = trading_days(end - timedelta(days=int(sessions * 1.5) + 10), end)[-sessions:]
    if bar_minutes is None:
        index = pd.DatetimeIndex([pd.Timestamp(d) for d in days]).tz_localize("UTC")
        steps_per_bar = 16
        bar_vol = daily_vol
    else:
        stamps = []
        for d in days:
            open_dt, close_dt = session_bounds(d)
            n = int((close_dt - open_dt).total_seconds() // 60) // bar_minutes
            stamps += [open_dt + timedelta(minutes=bar_minutes * i) for i in range(n)]
        index = pd.DatetimeIndex(stamps).tz_convert("UTC")
        steps_per_bar = max(bar_minutes, 4)
        bar_vol = daily_vol * math.sqrt(bar_minutes / 390)
    out = {}
    for k, sym in enumerate(symbols):
        rng = np.random.default_rng(seed * 1000 + k)
        steps = rng.standard_normal((len(index), steps_per_bar)) * bar_vol / math.sqrt(steps_per_bar)
        steps -= 0.5 * (bar_vol**2) / steps_per_bar  # no drift in price
        start = math.log(50.0 + 10 * k)
        paths = start + np.cumsum(steps.ravel()).reshape(steps.shape)
        prev_close = np.concatenate([[start], paths[:-1, -1]])
        log_open = prev_close
        log_high = np.maximum(log_open, paths.max(axis=1))
        log_low = np.minimum(log_open, paths.min(axis=1))
        out[sym] = pd.DataFrame(
            {
                "open": np.exp(log_open),
                "high": np.exp(log_high),
                "low": np.exp(log_low),
                "close": np.exp(paths[:, -1]),
                "volume": 1e5 * (1 + rng.random(len(index))),
            },
            index=index,
        )
    return out


def null_test(config: StrategyConfig, symbols: list[str], bar_minutes: int | None, periods_per_year: int,
              seed: int = 7, sessions: int = 250) -> dict:
    """Gross per-trade results of ``config`` on a random walk. ``passed`` when |t| < 2."""
    strat, sizing = config.build()
    data = random_walk_bars(symbols, bar_minutes, sessions=sessions, seed=seed)
    cfg = BacktestConfig(initial_cash=100_000, slippage_bps=0.0, periods_per_year=periods_per_year,
                         borrow_bps_annual=0.0, margin_rate_annual=0.0)
    bt = backtest_strategy(strat, data, sizing, cfg, with_benchmark=False)
    eq = bt.result.equity
    rets = [t.pnl / float(eq.iloc[max(t.entry_index - 1, 0)]) for t in bt.result.trades if not t.is_open]
    exp = expectancy(rets)
    passed = exp.n < 10 or not math.isfinite(exp.t_stat) or abs(exp.t_stat) < 2
    return {"passed": bool(passed), "trades": exp.n, "t_stat": exp.t_stat, "mean": exp.mean}
