"""Strategy library and registry."""

from __future__ import annotations

from typing import Any

from trades.strategies.base import Evidence, Kind, Strategy, StrategyOutput
from trades.strategies.benchmark import BuyAndHold
from trades.strategies.cross_sectional import (
    CrossSectionalMomentum,
    DualMomentum,
    FiftyTwoWeekHigh,
    LowVolatility,
    ShortTermReversal,
)
from trades.strategies.mean_reversion import BollingerReversion, RSI2Reversion
from trades.strategies.pairs import PairsTrading
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


def catalog() -> list[dict]:
    return [cls.meta() for cls in REGISTRY.values()]


__all__ = [
    "REGISTRY",
    "Evidence",
    "Kind",
    "Strategy",
    "StrategyOutput",
    "UnknownStrategyError",
    "catalog",
    "create_strategy",
    "get_strategy_class",
]
