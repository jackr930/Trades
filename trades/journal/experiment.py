"""The forward experiment: what is being tracked, fixed in a committed file.

``journal/experiment.json`` defines the watchlist, the Live Desk's strategies and pairs, how
the votes are combined and the account's risk settings. Every journal row carries

* the **experiment id**: a short hash of that definition, so a changed rule is a new
  experiment and the scorer never mixes two of them;
* the **code version**: ``git rev-parse HEAD:trades``, the hash of the ``trades`` package's
  tree. It changes only when the code changes, unlike HEAD itself, which the journal's own
  daily commits move.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from trades.advisor import AdvisorSettings
from trades.backtest.runner import StrategySpec

JOURNAL_DIR = Path("journal")
EXPERIMENT_FILE = JOURNAL_DIR / "experiment.json"

ACCOUNT_DEFAULTS = {
    "equity": 100_000.0,
    "risk_per_trade": 0.01,
    "stop_atr": 2.0,
    "max_position_pct": 0.20,
    "max_gross_exposure": 1.0,
    "allow_short": False,
    "fractional": False,  # paper orders in fractional shares
    "slippage_bps": 5.0,
    "commission_bps": 0.0,
}


@dataclass
class Experiment:
    definition: dict[str, Any]
    id: str

    @classmethod
    def load(cls, path: Path | str = EXPERIMENT_FILE) -> Experiment:
        definition = json.loads(Path(path).read_text())
        return cls(definition, experiment_id(definition))

    # -- fields --------------------------------------------------------------------------
    @property
    def watchlist(self) -> list[str]:
        return [s.upper() for s in self.definition["watchlist"]]

    @property
    def benchmark(self) -> str:
        return str(self.definition.get("benchmark", "SPY")).upper()

    @property
    def providers(self) -> list[str]:
        """Data providers to try in order (e.g. Yahoo, then Alpaca if Yahoo fails)."""
        return list(self.definition.get("providers", ["yahoo"]))

    @property
    def history_start(self) -> date:
        return date.fromisoformat(self.definition["history_start"])

    @property
    def account(self) -> dict[str, Any]:
        return {**ACCOUNT_DEFAULTS, **self.definition.get("account", {})}

    @property
    def pairs(self) -> list[tuple[str, str]]:
        have = set(self.watchlist)
        pairs = [(a.upper(), b.upper()) for a, b in self.definition.get("pairs", [])]
        return [(a, b) for a, b in pairs if a in have and b in have]

    def advisor_settings(self) -> AdvisorSettings:
        """The Live Desk exactly as this experiment defines it."""
        a = self.account
        return AdvisorSettings(
            strategies=[StrategySpec.from_dict(d) for d in self.definition["advisors"]],
            account_equity=a["equity"],
            risk_per_trade=a["risk_per_trade"],
            stop_atr=a["stop_atr"],
            max_position_pct=a["max_position_pct"],
            max_gross=a["max_gross_exposure"],
            allow_short=a["allow_short"],
            slippage_bps=a["slippage_bps"],
            commission_bps=a["commission_bps"],
            weighting=self.definition.get("weighting", "equal"),
            pairs=self.pairs,
        )

    def consensus_params(self) -> dict[str, Any]:
        """Parameters of the ``consensus`` strategy that reproduce this experiment's Live Desk."""
        a = self.account
        return {
            "members": self.definition["advisors"],
            "pairs": [list(p) for p in self.pairs],
            "weighting": self.definition.get("weighting", "equal"),
            "allow_short": a["allow_short"],
            "risk_per_trade": a["risk_per_trade"],
            "stop_atr": a["stop_atr"],
            "max_position_pct": a["max_position_pct"],
            "max_gross": a["max_gross_exposure"],
        }


def experiment_id(definition: dict[str, Any]) -> str:
    """Short hash of the definition's content (key order and whitespace don't matter)."""
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def code_version(repo: Path | str = ".") -> str:
    """Tree hash of the ``trades`` package at HEAD; "unknown" outside a git checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD:trades"], cwd=repo, capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip()[:12] or "unknown"
