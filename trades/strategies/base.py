"""Strategy framework.

A strategy turns aligned OHLCV frames into *signals*: the desired exposure per symbol
before position sizing (e.g. +1 long, -1 short, 0 flat; cross-sectional strategies emit
basket weights). Signals are piecewise constant -- they only change when a rule fires --
and strictly causal: the signal at bar ``t`` uses data up to and including bar ``t``'s
close and is traded at the next bar's open by the backtester and simulator.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

import numpy as np
import pandas as pd


class Kind(str, Enum):
    SINGLE = "single"  # evaluated on each symbol independently
    PAIR = "pair"  # exactly two symbols (A, B)
    CROSS_SECTIONAL = "cross_sectional"  # ranks a universe of symbols


class Evidence(str, Enum):
    STRONG = "strong"  # peer-reviewed, replicated across markets and decades
    MODERATE = "moderate"  # peer-reviewed, but mixed or weakened after publication
    PRACTITIONER = "practitioner"  # well-known practitioner rule with limited academic testing
    BENCHMARK = "benchmark"


@dataclass(frozen=True)
class Reference:
    authors: str
    year: int
    title: str
    venue: str
    url: str | None = None

    def to_dict(self) -> dict:
        return {
            "authors": self.authors,
            "year": self.year,
            "title": self.title,
            "venue": self.venue,
            "url": self.url,
        }

    def __str__(self) -> str:
        return f"{self.authors} ({self.year}). {self.title}. {self.venue}."


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    default: Any
    kind: str = "int"  # int | float | bool | choice | symbol
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()
    help: str = ""

    def coerce(self, value: Any) -> Any:
        if value is None:
            return self.default
        if self.kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if self.kind == "choice":
            if value not in self.choices:
                raise ValueError(f"{self.label}: {value!r} is not one of {', '.join(self.choices)}")
            return value
        if self.kind == "symbol":
            return str(value).strip().upper()
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{self.label}: {value!r} is not a number") from exc
        if not math.isfinite(num):
            raise ValueError(f"{self.label}: must be finite")
        if self.kind == "int":
            if abs(num - round(num)) > 1e-9:
                raise ValueError(f"{self.label}: must be a whole number")
            num = int(round(num))
        if self.min is not None and num < self.min:
            raise ValueError(f"{self.label}: {num} is below the minimum {self.min:g}")
        if self.max is not None and num > self.max:
            raise ValueError(f"{self.label}: {num} is above the maximum {self.max:g}")
        return num

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "default": self.default,
            "kind": self.kind,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "choices": list(self.choices),
            "help": self.help,
        }


@dataclass(frozen=True)
class Overlay:
    """A diagnostic column the UI draws: on the price pane or a lower indicator pane."""

    column: str
    label: str
    pane: str = "price"  # price | lower
    style: str = "solid"  # solid | dashed | dotted
    levels: tuple[float, ...] = ()

    def to_dict(self) -> dict:
        return {
            "column": self.column,
            "label": self.label,
            "pane": self.pane,
            "style": self.style,
            "levels": list(self.levels),
        }


@dataclass(frozen=True)
class Rule:
    label: str
    value: str
    passed: bool | None = None  # None = informational

    def to_dict(self) -> dict:
        return {"label": self.label, "value": self.value, "passed": self.passed}


@dataclass
class Explanation:
    symbol: str
    state: str  # long | short | flat | warming_up
    signal: float
    headline: str
    rules: list[Rule]
    since: pd.Timestamp | None
    fresh: bool  # signal changed on the latest bar -> actionable at the next open
    exit_rule: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "state": self.state,
            "signal": float(self.signal),
            "headline": self.headline,
            "rules": [r.to_dict() for r in self.rules],
            "since": int(self.since.timestamp()) if self.since is not None else None,
            "fresh": self.fresh,
            "exit_rule": self.exit_rule,
        }


@dataclass
class StrategyOutput:
    signals: pd.DataFrame  # index = time, columns = symbols
    diagnostics: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.signals.index  # type: ignore[return-value]


def fmt_pct(x: float, digits: int = 1) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 100:+.{digits}f}%"


def fmt_num(x: float, digits: int = 2) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x:,.{digits}f}"


class Strategy(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    category: ClassVar[str]
    kind: ClassVar[Kind] = Kind.SINGLE
    summary: ClassVar[str]
    rules_text: ClassVar[tuple[str, ...]]
    rationale: ClassVar[str]
    failure_modes: ClassVar[str]
    evidence: ClassVar[Evidence]
    evidence_text: ClassVar[str]
    references: ClassVar[tuple[Reference, ...]]
    params_spec: ClassVar[tuple[Param, ...]] = ()
    default_sizing: ClassVar[dict[str, Any]] = {"method": "fixed"}
    overlays: ClassVar[tuple[Overlay, ...]] = ()
    min_symbols: ClassVar[int] = 1
    max_symbols: ClassVar[int | None] = None
    uses_short: ClassVar[bool] = False  # needs short selling to work as designed

    def __init__(self, **params: Any):
        self.params = self.resolve_params(params)
        self.validate()

    # -- parameters ----------------------------------------------------------------
    @classmethod
    def resolve_params(cls, params: dict[str, Any] | None) -> dict[str, Any]:
        params = dict(params or {})
        spec = {p.name: p for p in cls.params_spec}
        unknown = set(params) - set(spec)
        if unknown:
            raise ValueError(f"{cls.name}: unknown parameter(s) {', '.join(sorted(unknown))}")
        return {name: p.coerce(params.get(name, p.default)) for name, p in spec.items()}

    def validate(self) -> None:
        """Cross-parameter checks; raise ValueError with a helpful message."""

    @abstractmethod
    def warmup(self) -> int:
        """Bars of history needed before the first valid signal."""

    # -- evaluation ----------------------------------------------------------------
    @abstractmethod
    def run(self, data: dict[str, pd.DataFrame]) -> StrategyOutput:
        """Compute signals for aligned bars (same index for every symbol)."""

    def check_symbols(self, symbols: list[str]) -> None:
        if len(symbols) < self.min_symbols:
            raise ValueError(f"{self.name} needs at least {self.min_symbols} symbols")
        if self.max_symbols is not None and len(symbols) > self.max_symbols:
            raise ValueError(f"{self.name} takes at most {self.max_symbols} symbols")

    def explain(self, output: StrategyOutput, symbol: str, at: int = -1) -> Explanation:
        sig = output.signals[symbol]
        t = at if at >= 0 else len(sig) + at
        value = float(sig.iloc[t])
        diag = output.diagnostics.get(symbol)
        valid = bool(diag["valid"].iloc[t]) if diag is not None and "valid" in diag else True
        since, fresh = signal_since(sig, t)
        if not valid:
            return Explanation(
                symbol,
                "warming_up",
                0.0,
                f"Needs {self.warmup()} bars of history before it can signal.",
                [],
                None,
                False,
            )
        headline, rules = self.describe(output, symbol, t)
        state = "long" if value > 1e-12 else "short" if value < -1e-12 else "flat"
        return Explanation(symbol, state, value, headline, rules, since, fresh, self.exit_rule(state))

    def describe(self, output: StrategyOutput, symbol: str, t: int) -> tuple[str, list[Rule]]:
        return "", []

    def exit_rule(self, state: str) -> str:
        return ""

    # -- metadata ------------------------------------------------------------------
    @classmethod
    def meta(cls) -> dict:
        return {
            "id": cls.id,
            "name": cls.name,
            "category": cls.category,
            "kind": cls.kind.value,
            "summary": cls.summary,
            "rules": list(cls.rules_text),
            "rationale": cls.rationale,
            "failure_modes": cls.failure_modes,
            "evidence": cls.evidence.value,
            "evidence_text": cls.evidence_text,
            "references": [r.to_dict() for r in cls.references],
            "params": [p.to_dict() for p in cls.params_spec],
            "default_sizing": dict(cls.default_sizing),
            "overlays": [o.to_dict() for o in cls.overlays],
            "min_symbols": cls.min_symbols,
            "max_symbols": cls.max_symbols,
            "uses_short": cls.uses_short,
        }


class SingleAssetStrategy(Strategy):
    """Applies ``compute`` to each symbol; capital is split equally across symbols."""

    kind = Kind.SINGLE

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a frame (same index as ``df``) with a ``signal`` column in [-1, 1]
        plus diagnostic columns. Must be causal."""

    def run(self, data: dict[str, pd.DataFrame]) -> StrategyOutput:
        self.check_symbols(list(data))
        n = len(data)
        diags: dict[str, pd.DataFrame] = {}
        sigs: dict[str, pd.Series] = {}
        for sym, df in data.items():
            d = self.compute(df)
            d["signal"] = d["signal"].fillna(0.0).astype(float)
            if "valid" not in d:
                d["valid"] = np.arange(len(d)) >= self.warmup() - 1
            diags[sym] = d
            sigs[sym] = d["signal"] / n
        return StrategyOutput(pd.DataFrame(sigs), diags)

    def describe(self, output: StrategyOutput, symbol: str, t: int) -> tuple[str, list[Rule]]:
        return self.describe_row(output.diagnostics[symbol], t)

    def describe_row(self, diag: pd.DataFrame, t: int) -> tuple[str, list[Rule]]:
        return "", []


def signal_since(sig: pd.Series, t: int) -> tuple[pd.Timestamp | None, bool]:
    """When the current signal value started, and whether it changed on bar ``t``."""
    vals = sig.to_numpy()[: t + 1]
    if len(vals) == 0:
        return None, False
    cur = vals[-1]
    k = len(vals) - 1
    while k > 0 and abs(vals[k - 1] - cur) <= 1e-12:
        k -= 1
    fresh = k == len(vals) - 1 and k > 0
    return sig.index[k], fresh


def hold_every(raw: pd.Series, every: int) -> pd.Series:
    """Sample ``raw`` every ``every`` bars from its first valid value and hold in between
    (e.g. monthly rebalancing of a daily signal). Before the first valid value -> NaN."""
    arr = raw.to_numpy(dtype=float)
    out = np.full(len(arr), np.nan)
    valid = np.flatnonzero(~np.isnan(arr))
    if len(valid) == 0:
        return pd.Series(out, index=raw.index)
    first = valid[0]
    cur = np.nan
    for i in range(first, len(arr)):
        if (i - first) % max(every, 1) == 0 and not np.isnan(arr[i]):
            cur = arr[i]
        out[i] = cur
    return pd.Series(out, index=raw.index)


def hysteresis(
    enter_long: pd.Series,
    enter_short: pd.Series,
    exit_long: pd.Series | None = None,
    exit_short: pd.Series | None = None,
) -> pd.Series:
    """Generic position state machine from boolean condition series."""
    el = enter_long.fillna(False).to_numpy(bool)
    es = enter_short.fillna(False).to_numpy(bool)
    xl = exit_long.fillna(False).to_numpy(bool) if exit_long is not None else es
    xs = exit_short.fillna(False).to_numpy(bool) if exit_short is not None else el
    out = np.zeros(len(el))
    pos = 0.0
    for i in range(len(el)):
        if pos > 0 and xl[i]:
            pos = 0.0
        elif pos < 0 and xs[i]:
            pos = 0.0
        if pos == 0:
            if el[i]:
                pos = 1.0
            elif es[i]:
                pos = -1.0
        out[i] = pos
    return pd.Series(out, index=enter_long.index)
