"""Request bodies."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class StrategyBody(BaseModel):
    id: str
    params: dict[str, Any] = Field(default_factory=dict)
    sizing: dict[str, Any] | None = None


class DataBody(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=50)  # a watchlist holds up to 50
    timeframe: str = "1d"
    start: str | None = None
    end: str | None = None
    provider: str | None = None

    @field_validator("symbols")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        out = []
        for s in v:
            s = s.strip().upper()
            if not s or len(s) > 15:
                raise ValueError(f"invalid symbol {s!r}")
            if s not in out:
                out.append(s)
        return out


class BacktestBody(DataBody):
    strategy: StrategyBody
    config: dict[str, Any] = Field(default_factory=dict)


class GridParam(BaseModel):
    min: float | None = None
    max: float | None = None
    step: float | None = None
    values: list[Any] | None = None


class OptimizeBody(BacktestBody):
    grid: dict[str, GridParam]
    objective: Literal["sharpe", "cagr", "calmar", "sortino", "total_return"] = "sharpe"
    mode: Literal["grid", "walk_forward"] = "grid"
    train_bars: int = Field(756, ge=60, le=5000)
    test_bars: int = Field(252, ge=20, le=2000)
    anchored: bool = False


class RecommendBody(BaseModel):
    symbols: list[str] | None = None
    provider: str | None = None
    timeframe: str | None = None
    strategies: list[StrategyBody] | None = None
    pairs: list[tuple[str, str]] = Field(default_factory=list)


class WatchlistBody(BaseModel):
    symbols: list[str] = Field(max_length=50)
    provider: str | None = None


class OrderBody(BaseModel):
    side: Literal["buy", "sell"]
    qty: float = Field(gt=0)
    type: Literal["market", "limit", "stop"] = "market"
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    tif: Literal["gtc", "day"] = "gtc"
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    note: str = Field(default="", max_length=500)


class StepBody(BaseModel):
    n: int = Field(1, ge=1, le=500)
    since: int | None = None


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
