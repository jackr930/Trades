"""Strategy library and registry."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from trades.strategies.base import Evidence, Kind, Strategy, StrategyOutput
from trades.strategies.benchmark import BuyAndHold
from trades.strategies.consensus import Consensus
from trades.strategies.cross_sectional import (
    CrossSectionalMomentum,
    DualMomentum,
    FiftyTwoWeekHigh,
    LowVolatility,
    ShortTermReversal,
)
from trades.strategies.intraday import IntradayMomentum, OpeningRangeBreakout, ORBStocksInPlay
from trades.strategies.mean_reversion import BollingerReversion, RSI2Reversion
from trades.strategies.ml import MLRanker
from trades.strategies.pairs import PairsTrading
from trades.strategies.quant import (
    KalmanPairs,
    MultiHorizonTrend,
    RegimeSwitching,
    ResidualMomentum,
    ResidualReversion,
)
from trades.strategies.sizing import SizingConfig
from trades.strategies.trend import DonchianBreakout, FaberTrend, MovingAverageCrossover, TimeSeriesMomentum

REGISTRY: dict[str, type[Strategy]] = {
    cls.id: cls
    for cls in (
        TimeSeriesMomentum,
        FaberTrend,
        MovingAverageCrossover,
        DonchianBreakout,
        BollingerReversion,
        RSI2Reversion,
        PairsTrading,
        CrossSectionalMomentum,
        DualMomentum,
        FiftyTwoWeekHigh,
        LowVolatility,
        ShortTermReversal,
        MultiHorizonTrend,
        ResidualReversion,
        ResidualMomentum,
        KalmanPairs,
        RegimeSwitching,
        MLRanker,
        OpeningRangeBreakout,
        ORBStocksInPlay,
        IntradayMomentum,
        Consensus,
        BuyAndHold,
    )
}


class UnknownStrategyError(KeyError, ValueError):
    """Raised for an unknown strategy id (a ValueError so APIs report it as a bad request)."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "unknown strategy"


def get_strategy_class(strategy_id: str) -> type[Strategy]:
    try:
        return REGISTRY[strategy_id]
    except KeyError as exc:
        raise UnknownStrategyError(
            f"unknown strategy {strategy_id!r}; available: {', '.join(REGISTRY)}"
        ) from exc


def create_strategy(strategy_id: str, params: dict[str, Any] | None = None) -> Strategy:
    return get_strategy_class(strategy_id)(**(params or {}))


@dataclass
class StrategyConfig:
    """Which strategy to run, with which parameters and position sizing (JSON-friendly)."""

    id: str
    params: dict[str, Any] = field(default_factory=dict)
    sizing: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StrategyConfig:
        return cls(id=d["id"], params=dict(d.get("params") or {}), sizing=d.get("sizing"))

    def build(self) -> tuple[Strategy, SizingConfig]:
        cls = get_strategy_class(self.id)
        strat = create_strategy(self.id, self.params)
        # A strategy whose signals are already target weights (the consensus) keeps its own sizing.
        sizing = None if cls.sizes_itself else self.sizing
        return strat, SizingConfig.from_dict(sizing, cls.default_sizing)

    def key(self) -> str:
        return json.dumps(
            {"id": self.id, "params": self.params, "sizing": self.sizing}, sort_keys=True, default=str
        )


def catalog() -> list[dict]:
    return [cls.meta() for cls in REGISTRY.values()]


__all__ = [
    "REGISTRY",
    "Evidence",
    "Kind",
    "Strategy",
    "StrategyConfig",
    "StrategyOutput",
    "UnknownStrategyError",
    "catalog",
    "create_strategy",
    "get_strategy_class",
]
