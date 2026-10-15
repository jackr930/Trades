"""The account's hard risk limits, read from ``risk/limits.yaml``.

The file is human-only: Claude Code is blocked from editing it, and every change is reviewed in
git. Unknown keys and out-of-range values are errors, never silently ignored, because a
misspelt limit that quietly falls back to a default is exactly the failure a limit exists to
prevent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from trades import config

LIMITS_FILE = ("risk", "limits.yaml")


@dataclass(frozen=True)
class RiskLimits:
    max_risk_per_trade: float = 0.005  # equity lost if a trade's protective stop is hit
    daily_loss_limit: float = 0.015  # from the session's opening equity: flatten, no new trades today
    weekly_loss_limit: float = 0.04  # from the week's opening equity: flatten, no new trades this week
    max_gross_exposure: float = 1.0  # long + short positions together, as a multiple of equity
    max_position_pct: float = 0.25  # largest single position, as a fraction of equity
    max_open_positions: int = 5
    allow_short: bool = False
    max_orders_per_day: int = 40
    allowed_symbols: tuple[str, ...] = ()  # empty: any symbol in the spec's universe
    blocked_symbols: tuple[str, ...] = ()
    halt_file: str = "HALT"  # relative to the repository root; while it exists nothing trades

    def __post_init__(self) -> None:
        checks: dict[str, tuple[float, float]] = {
            "max_risk_per_trade": (0.0001, 0.05),
            "daily_loss_limit": (0.001, 0.2),
            "weekly_loss_limit": (0.001, 0.5),
            "max_gross_exposure": (0.05, 4.0),
            "max_position_pct": (0.01, 4.0),
            "max_open_positions": (1, 100),
            "max_orders_per_day": (1, 10_000),
        }
        for name, (lo, hi) in checks.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise config.ConfigError(f"risk limit {name} must be a number, got {value!r}")
            if not lo <= value <= hi:
                raise config.ConfigError(f"risk limit {name}={value} is outside the sane range [{lo:g}, {hi:g}]")
        if self.weekly_loss_limit < self.daily_loss_limit:
            raise config.ConfigError("weekly_loss_limit cannot be tighter than daily_loss_limit")
        if self.max_position_pct > self.max_gross_exposure:
            raise config.ConfigError("max_position_pct cannot exceed max_gross_exposure")
        if not isinstance(self.allow_short, bool):
            raise config.ConfigError("allow_short must be true or false")
        overlap = set(self.allowed_symbols) & set(self.blocked_symbols)
        if overlap:
            raise config.ConfigError(f"symbols both allowed and blocked: {', '.join(sorted(overlap))}")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RiskLimits:
        data = config.strict("risk/limits.yaml", dict(data or {}), {f.name for f in fields(cls)})
        for key in ("allowed_symbols", "blocked_symbols"):
            if key in data:
                data[key] = tuple(str(s).strip().upper() for s in (data[key] or ()))
        for key in ("max_open_positions", "max_orders_per_day"):
            if key in data and isinstance(data[key], float) and data[key].is_integer():
                data[key] = int(data[key])
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["allowed_symbols"] = list(self.allowed_symbols)
        out["blocked_symbols"] = list(self.blocked_symbols)
        return out

    def symbol_allowed(self, symbol: str) -> bool:
        s = symbol.upper()
        if s in self.blocked_symbols:
            return False
        return not self.allowed_symbols or s in self.allowed_symbols

    def halt_path(self, root: Path | None = None) -> Path:
        return (root or config.repo_root()) / self.halt_file


def load_limits(file: Path | None = None) -> RiskLimits:
    """The limits in ``risk/limits.yaml`` (or ``file``)."""
    return RiskLimits.from_dict(config.read_yaml(file or config.path(*LIMITS_FILE)))
