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
INTRADAY_START = date(2026, 3, 2)  # about seven months of 5-minute bars (incl. no early close)
INTRADAY_SYMBOLS = ["SIMTEC", "SIMIDX", "SIMBNK", "SIMNRG", "SIMHLC"]


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


def strategy_inputs(cls, daily, intraday=None):
    """The demo symbols a strategy of ``cls``'s kind is tested on (5-minute bars for intraday ones)."""
    if cls.intraday:
        if intraday is None:
            raise ValueError(f"{cls.id} is an intraday strategy: pass the intraday fixture")
        return dict(intraday) if cls.kind is Kind.CROSS_SECTIONAL else {"SIMTEC": intraday["SIMTEC"]}
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


def synthetic_target(key: str = "TEST-v1", providers=("synthetic",)):
    """A paper target on three synthetic symbols: a small consensus, costed at 5 bps per fill."""
    from trades.engine.paper import PaperTarget
    from trades.execution.sim_broker import BacktestConfig
    from trades.strategies import StrategyConfig

    members = [{"id": "tsmom"}, {"id": "faber_trend"}, {"id": "rsi2_reversion"}]
    return PaperTarget(
        key=key,
        symbols=["SIMIDX", "SIMTEC", "SIMBNK"],
        strategy=StrategyConfig("consensus", {"members": members, "pairs": [], "allow_short": False}),
        providers=list(providers),
        engine=BacktestConfig(slippage_bps=5.0),
        history_start=date(2015, 1, 2),
        benchmark="SIMIDX",
    )


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


def orb_target(entry: str = "next_open"):
    """A paper target trading the opening range breakout on SIMTEC's 5-minute bars (STRAT-002's rules)."""
    from trades.engine.paper import PaperTarget
    from trades.execution.sim_broker import BacktestConfig
    from trades.strategies import StrategyConfig

    params = {"or_minutes": 5, "entry": entry, "stop_mode": "range", "target_r": 10.0, "allow_short": True}
    return PaperTarget(
        key=f"ORB-{entry}-v1",
        symbols=["SIMTEC"],
        strategy=StrategyConfig("orb", params, {"method": "stop_risk", "risk_per_trade": 0.005, "max_leverage": 1.0}),
        providers=["synthetic"],
        timeframe="5m",
        intraday=True,
        engine=BacktestConfig(half_spread_bps=2.0, slippage_bps=1.0, stop_slippage_bps=5.0, periods_per_year=252 * 78),
    )


def ny_time(day: date, hhmm: str, seconds: int = 20):
    """``day`` at ``hhmm`` New York time (20 seconds past the minute: just after a bar completes)."""
    from datetime import datetime

    from trades.data.calendar import NY

    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, seconds, tzinfo=NY)
