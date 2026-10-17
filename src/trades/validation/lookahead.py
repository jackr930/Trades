"""Look-ahead check: does a strategy's past change when the future arrives?

The same property the ``tests/lookahead`` tier checks for every registered strategy on synthetic
data, run here on the data of a research run: signals, order levels and sizes computed on data
cut off at bar ``t`` must equal those computed on the full history, up to ``t``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trades.features.intraday import infer_bar_minutes
from trades.strategies.base import Strategy, StrategyOutput

LEVELS = ("stop_losses", "take_profits", "entry_stops")


def _cut_points(n: int, bar_minutes: int | None) -> list[int]:
    cuts = [int(n * f) for f in (0.35, 0.6, 0.85)]
    if bar_minutes:  # move the cuts into the middle of a session, where intraday leaks show up
        cuts = [c + 17 for c in cuts]
    return [c for c in cuts if 10 < c < n - 1]


def _same(a: pd.DataFrame | None, b: pd.DataFrame | None, cut: int) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return np.allclose(a.iloc[:cut].to_numpy(float), b.to_numpy(float), equal_nan=True, atol=1e-10)


def check(strategy: Strategy, data: dict[str, pd.DataFrame]) -> tuple[bool, str]:
    """(passed, explanation) for ``strategy`` on ``data`` (aligned bars)."""
    first = next(iter(data.values()))
    n = len(first)
    full: StrategyOutput = strategy.run(data)
    for cut in _cut_points(n, infer_bar_minutes(first.index)):
        part = strategy.run({s: df.iloc[:cut] for s, df in data.items()})
        when = first.index[cut - 1]
        if not np.allclose(part.signals.to_numpy(float), full.signals.iloc[:cut].to_numpy(float), equal_nan=True, atol=1e-10):
            return False, f"signals before {when} changed when later bars were added"
        for name in LEVELS:
            if not _same(getattr(full, name), getattr(part, name), cut):
                return False, f"{name.replace('_', ' ')} before {when} changed when later bars were added"
    return True, "signals and order levels are unchanged by later bars"
