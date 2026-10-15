"""Helpers shared by the test tiers (fixtures live in conftest.py)."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from trades.data.base import DataError
from trades.data.synthetic import SyntheticProvider
from trades.journal.experiment import Experiment
from trades.strategies import Kind

END = date(2026, 9, 25)
UNIVERSE = ["SIMIDX", "SIMTEC", "SIMBNK", "SIMNRG", "SIMUTL", "SIMHLC", "SIMBND", "SIMGLD"]


def make_bars(closes, *, start="2024-01-02", spread=0.01, volume=1_000_000.0) -> pd.DataFrame:
    """Deterministic OHLCV frame from a close series (open = previous close)."""
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + spread)
    lows = np.minimum(opens, closes) * (1 - spread)
    idx = pd.bdate_range(start, periods=len(closes), tz="UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume}, index=idx
    )


def strategy_inputs(cls, daily):
    """The demo symbols a strategy of ``cls``'s kind is tested on."""
    if cls.id == "consensus":  # the whole demo watchlist, so its pair member votes too
        return {s: daily[s] for s in (*UNIVERSE, "SIMPRA", "SIMPRB")}
    if cls.kind is Kind.PAIR:
        return {s: daily[s] for s in ("SIMPRA", "SIMPRB")}
    if cls.kind is Kind.CROSS_SECTIONAL:
        return {s: daily[s] for s in UNIVERSE}
    return {"SIMTEC": daily["SIMTEC"]}


def strategy_params(cls):
    return {"safe_symbol": "SIMBND"} if cls.id == "dual_momentum" else {}


def synthetic_experiment(tmp_path, providers=("synthetic",)) -> Experiment:
    definition = {
        "watchlist": ["SIMIDX", "SIMTEC", "SIMBNK"],
        "benchmark": "SIMIDX",
        "advisors": [{"id": "tsmom"}, {"id": "faber_trend"}, {"id": "rsi2_reversion"}],
        "pairs": [],
        "weighting": "equal",
        "history_start": "2015-01-02",
        "providers": list(providers),
    }
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(definition))
    return Experiment.load(path)


def synthetic_fetch(end: date, calls: list | None = None):
    """A journal ``fetch`` backed by the synthetic market; every other provider is "down"."""
    provider = SyntheticProvider(seed=7, end=end)

    def fetch(pid, symbols, start):
        if calls is not None:
            calls.append(pid)
        if pid != "synthetic":
            raise DataError(f"{pid} is down")
        return {s: provider.history(s, start=start) for s in symbols}, {}

    return fetch
