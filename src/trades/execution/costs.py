"""The cost model: what a fill really costs, and the stress test the checklist applies.

Every fill pays half the bid-ask spread plus slippage (``half_spread_bps + slippage_bps``);
stop orders also pay ``stop_slippage_bps`` because they fill in fast markets; sales pay the SEC
Section 31 fee and FINRA's trading activity fee; commissions apply when the broker charges any.
The numbers live in ``config/base.yaml`` and a spec's ``costs:`` section overrides them. Profits
in intraday strategies are often a few basis points per trade, so these numbers decide whether a
backtest's edge survives: the acceptance checklist reruns every strategy at twice these costs.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from trades import config
from trades.execution.sim_broker import COST_FIELDS, BacktestConfig

# Costs that scale with trading activity and are multiplied in a stress test.
STRESSED = (
    "half_spread_bps",
    "slippage_bps",
    "stop_slippage_bps",
    "commission_bps",
    "commission_per_share",
    "min_commission",
    "sec_fee_per_million",
    "taf_per_share",
    "taf_max_per_trade",
)


def validate(costs: dict[str, Any], where: str = "costs") -> dict[str, float]:
    """Check a cost mapping (from YAML or a spec): known keys, non-negative numbers."""
    unknown = sorted(set(costs) - set(COST_FIELDS))
    if unknown:
        raise config.ConfigError(f"{where}: unknown cost(s) {', '.join(unknown)}; expected {', '.join(COST_FIELDS)}")
    out = {}
    for key, value in costs.items():
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise config.ConfigError(f"{where}.{key} must be a number, got {value!r}") from exc
        if number < 0:
            raise config.ConfigError(f"{where}.{key} cannot be negative")
        out[key] = number
    return out


def base_costs() -> dict[str, float]:
    """The default cost model from ``config/base.yaml``."""
    return validate(config.section("backtest", "costs"), "config/base.yaml costs")


def apply(cfg: BacktestConfig, costs: dict[str, Any]) -> BacktestConfig:
    """``cfg`` with the given costs (unlisted cost fields keep their values)."""
    return replace(cfg, **validate(costs))


def with_model(cfg: BacktestConfig, overrides: dict[str, Any] | None = None) -> BacktestConfig:
    """``cfg`` with the repository's cost model, then a spec's overrides, applied."""
    return apply(apply(cfg, base_costs()), overrides or {})


def stressed(cfg: BacktestConfig, factor: float) -> BacktestConfig:
    """``cfg`` with every trading cost multiplied by ``factor`` (financing is left alone)."""
    if factor <= 0:
        raise ValueError("a cost stress factor must be positive")
    return replace(cfg, **{k: getattr(cfg, k) * factor for k in STRESSED})


def per_fill_bps(cfg: BacktestConfig, stop: bool = False) -> float:
    """Spread plus slippage paid on one fill, in basis points (stop fills pay extra)."""
    return cfg.half_spread_bps + cfg.slippage_bps + (cfg.stop_slippage_bps if stop else 0.0)


def describe(cfg: BacktestConfig) -> str:
    """One line for reports, e.g. ``half spread 2 bps + slippage 3 bps per fill; stops +10 bps; ...``."""
    parts = [f"half spread {cfg.half_spread_bps:g} bps + slippage {cfg.slippage_bps:g} bps per fill"]
    if cfg.stop_slippage_bps:
        parts.append(f"stops +{cfg.stop_slippage_bps:g} bps")
    commission = []
    if cfg.commission_per_share:
        commission.append(f"${cfg.commission_per_share:g}/share")
    if cfg.commission_bps:
        commission.append(f"{cfg.commission_bps:g} bps")
    if cfg.min_commission:
        commission.append(f"min ${cfg.min_commission:g}")
    parts.append("commission " + (", ".join(commission) if commission else "none"))
    fees = []
    if cfg.sec_fee_per_million:
        fees.append(f"SEC ${cfg.sec_fee_per_million:g}/$1M sold")
    if cfg.taf_per_share:
        cap = f" (max ${cfg.taf_max_per_trade:g})" if cfg.taf_max_per_trade else ""
        fees.append(f"FINRA TAF ${cfg.taf_per_share:g}/share sold{cap}")
    if fees:
        parts.append("; ".join(fees))
    return "; ".join(parts)
