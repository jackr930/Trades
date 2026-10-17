"""One decision at the latest completed bar, made exactly the way a backtest makes it at that bar.

``decide`` runs the strategy on every bar up to now, sizes it and passes it through the risk
engine: the same three steps ``trades.engine.backtest.run_backtest`` takes at each bar. Because
strategies are causal (the ``tests/lookahead`` tier), a decision at bar t is the same whether t is
the newest bar of a live feed or one bar inside a backtest, which is what makes paper results
comparable with a replay of the same bars (``trades.reporting.tracking``).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from trades.data.base import align_bars
from trades.features.intraday import infer_bar_minutes, session_info
from trades.risk import RiskEngine
from trades.strategies import StrategyConfig
from trades.strategies.sizing import apply_sizing


@dataclass
class Decision:
    time: pd.Timestamp  # the bar whose close the decision was made at
    raw: dict[str, float]  # target weights from the strategy and its sizing
    targets: dict[str, float]  # after the risk engine: what the account may hold
    prices: dict[str, float]  # closes of that bar
    stop_losses: dict[str, float | None] = field(default_factory=dict)
    take_profits: dict[str, float | None] = field(default_factory=dict)
    entry_stops: dict[str, float | None] = field(default_factory=dict)
    closing: bool = False  # the session's last bar: an intraday strategy ends its day here
    headlines: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["time"] = pd.Timestamp(self.time).isoformat()
        return d


def _level(frame: pd.DataFrame | None, sym: str) -> float | None:
    if frame is None or sym not in frame:
        return None
    v = float(frame[sym].iloc[-1])
    return v if math.isfinite(v) else None


def decide(
    config: StrategyConfig,
    data: dict[str, pd.DataFrame],
    *,
    periods_per_year: int = 252,
    risk: RiskEngine | None = None,
    equity: float | None = None,
    current: dict[str, float] | None = None,
) -> Decision:
    """The decision at the last bar of ``data`` (completed bars only). ``current``: the account's
    weights per symbol now, which the risk engine needs together with ``equity``."""
    strat, sizing = config.build()
    aligned = align_bars(data)
    symbols = list(aligned)
    out = strat.run(aligned)
    weights = apply_sizing(
        out.signals, aligned, sizing, strat.kind, periods_per_year,
        stop_losses=out.stop_losses, entry_stops=out.entry_stops,
    )
    index = weights.index
    time = index[-1]
    raw = {s: float(weights[s].iloc[-1]) for s in symbols}
    prices = {s: float(aligned[s]["close"].iloc[-1]) for s in symbols}
    stops = {s: _level(out.stop_losses, s) for s in symbols}
    takes = {s: _level(out.take_profits, s) for s in symbols}
    entries = {s: _level(out.entry_stops, s) for s in symbols}
    targets = dict(raw)
    if risk is not None:
        cur = np.array([(current or {}).get(s, 0.0) for s in symbols])
        reviewed = risk.review(
            time,
            float(equity if equity is not None else 0.0),
            np.array([raw[s] for s in symbols]),
            cur,
            np.array([prices[s] for s in symbols]),
            np.array([stops[s] if stops[s] is not None else np.nan for s in symbols]),
            np.array([entries[s] if entries[s] is not None else np.nan for s in symbols]),
        )
        targets = {s: float(w) for s, w in zip(symbols, reviewed, strict=True)}
    closing = False
    bar_minutes = infer_bar_minutes(index)
    if strat.intraday and bar_minutes is not None:
        closing = bool(session_info(index[-1:], bar_minutes)["last"].iloc[-1])
    headlines = {}
    for s in symbols:
        try:
            headlines[s] = strat.explain(out, s).headline
        except Exception:  # an explanation is a courtesy; a decision must never fail on it
            headlines[s] = ""
    return Decision(time, raw, targets, prices, stops, takes, entries, closing, headlines)
