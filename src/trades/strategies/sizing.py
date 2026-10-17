"""Position sizing: turns strategy signals into target portfolio weights.

Methods
-------
``fixed``
    weight = signal x allocation.
``vol_target``
    Scale exposure so the position's *ex-ante* volatility matches ``target_vol``
    (Moskowitz, Ooi & Pedersen 2012; Moreira & Muir 2017). Single-asset strategies scale
    each symbol by its own EWMA volatility; pair and cross-sectional strategies scale the
    whole basket using an EWMA covariance matrix (RiskMetrics-style).
``atr_risk``
    Fixed-fractional risk sizing used by the Turtles: size so that an adverse move of
    ``stop_atr`` x ATR loses ``risk_per_trade`` of equity. Single-asset strategies only.
``stop_risk``
    For strategies that set their own protective stops (e.g. the opening range breakout):
    size so that entering at the reference price (the strategy's ``entry_stop``, else the
    decision close) and being stopped out loses ``risk_per_trade`` of equity, capped per
    position at ``max_leverage``. Gross exposure across positions is capped by the broker at
    fill time instead of here, because a stop entry may never fill.

Sizes are *locked* while a signal is unchanged (so positions are held as shares rather
than constantly re-traded) unless ``rebalance_every`` > 0, which re-sizes periodically.
Gross exposure is capped at ``max_leverage``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields
from typing import Any

import numpy as np
import pandas as pd

from trades.features import indicators as ind
from trades.strategies.base import Kind

METHODS = ("fixed", "vol_target", "atr_risk", "stop_risk")


@dataclass
class SizingConfig:
    method: str = "fixed"
    allocation: float = 1.0
    target_vol: float = 0.15
    vol_com: float = 30.0
    risk_per_trade: float = 0.01
    stop_atr: float = 2.0
    atr_length: int = 20
    max_leverage: float = 1.0
    rebalance_every: int = 0

    def __post_init__(self):
        if self.method not in METHODS:
            raise ValueError(f"sizing method must be one of {METHODS}")
        checks = {
            "allocation": (0.0, 10.0),
            "target_vol": (0.01, 2.0),
            "vol_com": (2.0, 500.0),
            "risk_per_trade": (0.0005, 0.1),
            "stop_atr": (0.25, 10.0),
            "atr_length": (2, 200),
            "max_leverage": (0.1, 10.0),
            "rebalance_every": (0, 1000),
        }
        for name, (lo, hi) in checks.items():
            v = getattr(self, name)
            if not (lo <= v <= hi):
                raise ValueError(f"sizing.{name}={v} outside [{lo}, {hi}]")
        self.atr_length = int(self.atr_length)
        self.rebalance_every = int(self.rebalance_every)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, defaults: dict[str, Any] | None = None) -> SizingConfig:
        known = {f.name for f in fields(cls)}
        merged = {**(defaults or {}), **(data or {})}
        return cls(**{k: v for k, v in merged.items() if k in known and v is not None})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ewma_vol(returns: pd.DataFrame, com: float, ppy: int) -> pd.DataFrame:
    return np.sqrt((returns**2).ewm(com=com, adjust=True, min_periods=20).mean() * ppy)


def _portfolio_vol(returns: np.ndarray, weights: np.ndarray, com: float, ppy: int) -> np.ndarray:
    """Ex-ante vol of ``weights[t]`` using the EWMA covariance of returns up to t."""
    T, N = returns.shape
    lam = com / (1.0 + com)
    S = np.zeros((N, N))
    norm = 0.0
    out = np.full(T, np.nan)
    r = np.nan_to_num(returns)
    for t in range(T):
        S = lam * S + (1 - lam) * np.outer(r[t], r[t])
        norm = lam * norm + (1 - lam)
        if t >= 19:
            cov = S / norm
            w = weights[t]
            v = float(w @ cov @ w)
            out[t] = np.sqrt(max(v, 0.0) * ppy)
    return out


def apply_sizing(
    signals: pd.DataFrame,
    data: dict[str, pd.DataFrame],
    cfg: SizingConfig,
    kind: Kind,
    periods_per_year: int = 252,
    *,
    stop_losses: pd.DataFrame | None = None,
    entry_stops: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Convert signals (T x N) to target weights (T x N). ``stop_losses`` and ``entry_stops`` are
    the strategy's order levels, needed by ``stop_risk`` sizing."""
    signals = signals.fillna(0.0)
    cols = list(signals.columns)
    closes = pd.DataFrame({s: data[s]["close"] for s in cols}).reindex(signals.index)
    returns = closes.pct_change(fill_method=None)
    method = cfg.method
    if method == "atr_risk" and kind is not Kind.SINGLE:
        method = "fixed"

    if method == "fixed":
        target = signals * cfg.allocation
    elif method == "vol_target":
        if kind is Kind.SINGLE:
            vol = _ewma_vol(returns, cfg.vol_com, periods_per_year)
            scale = (cfg.target_vol / vol).replace([np.inf, -np.inf], np.nan)
            target = signals * scale * cfg.allocation
        else:
            pv = _portfolio_vol(
                returns.to_numpy(float), signals.to_numpy(float), cfg.vol_com, periods_per_year
            )
            with np.errstate(divide="ignore", invalid="ignore"):
                scale = np.where(pv > 0, cfg.target_vol / pv, np.nan)
            target = signals.mul(scale, axis=0) * cfg.allocation
    elif method == "stop_risk":
        if stop_losses is None:
            raise ValueError("stop_risk sizing needs a strategy that sets protective stops")
        parts = {}
        for s in cols:
            ref = closes[s]
            if entry_stops is not None and s in entry_stops:
                ref = entry_stops[s].where(entry_stops[s].notna(), ref)
            dist = (ref - stop_losses[s].reindex(signals.index)).abs()
            size = (cfg.risk_per_trade * ref / dist).replace([np.inf, -np.inf], np.nan)
            parts[s] = np.sign(signals[s]) * size.clip(upper=cfg.max_leverage)
        target = pd.DataFrame(parts, index=signals.index) * cfg.allocation
    else:  # atr_risk, single-asset
        parts = {}
        for s in cols:
            df = data[s].reindex(signals.index)
            atr = ind.atr(df["high"], df["low"], df["close"], cfg.atr_length)
            size = (cfg.risk_per_trade * df["close"] / (cfg.stop_atr * atr)).clip(upper=cfg.max_leverage)
            parts[s] = np.sign(signals[s]) * size
        target = pd.DataFrame(parts, index=signals.index) * cfg.allocation

    per_column = kind is Kind.SINGLE or method == "stop_risk"  # each stop-sized trade keeps its own size
    weights = _lock(signals.to_numpy(float), target.to_numpy(float), per_column, cfg.rebalance_every)
    if method != "stop_risk":
        gross = np.abs(weights).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            cap = np.where(gross > cfg.max_leverage, cfg.max_leverage / gross, 1.0)
        weights = weights * cap[:, None]
    return pd.DataFrame(weights, index=signals.index, columns=cols)


def _lock(sig: np.ndarray, target: np.ndarray, per_column: bool, every: int) -> np.ndarray:
    """Hold the size chosen when a signal starts until the signal changes (or until the
    periodic re-size). NaN targets (e.g. no volatility estimate yet) mean 'flat for now'."""
    T, N = sig.shape
    out = np.zeros((T, N))
    if T == 0 or N == 0:
        return out
    if per_column:
        for j in range(N):
            out[:, j] = _lock_group(sig[:, [j]], target[:, [j]], every)[:, 0]
        return out
    return _lock_group(sig, target, every)


def _lock_group(sig: np.ndarray, target: np.ndarray, every: int) -> np.ndarray:
    # Plain Python floats: this loop runs on every bar of every live strategy agent, and
    # per-element numpy calls would dominate its cost.
    S, W = sig.tolist(), target.tolist()
    zero = [0.0] * sig.shape[1]
    rows: list[list[float]] = []
    last_sig: list[float] | None = None
    last_t = -(10**9)
    prev = zero
    for t, s in enumerate(S):
        if not any(s):
            row = zero
            last_sig, last_t = s, t
        else:
            changed = last_sig is None or any(abs(a - b) > 1e-12 for a, b in zip(s, last_sig, strict=True))
            due = every > 0 and t - last_t >= every
            if changed or due:
                w = W[t]
                if all(math.isfinite(x) for x in w):
                    row = w
                    last_sig, last_t = s, t
                else:
                    row = zero
                    last_sig = None  # no size available yet: try again next bar
            else:
                row = prev
        rows.append(row)
        prev = row
    return np.array(rows, dtype=float)
