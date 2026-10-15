"""Risk: the deterministic checks every order passes through, and the kill switch.

``RiskLimits`` (from ``risk/limits.yaml``) states the account's hard limits; ``RiskEngine``
applies them bar by bar in backtests and on every run of the paper trader, so a strategy is
researched under the same limits it will trade under. No model, and no AI, can override them.
"""

from trades.risk.limits import RiskLimits, load_limits

__all__ = ["RiskLimits", "load_limits"]
