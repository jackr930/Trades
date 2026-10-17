"""The kill switch: while a file named ``HALT`` exists at the repository root, nothing trades.

A file is the simplest switch there is. You can create it from a phone (commit it on GitHub, and the
scheduled paper job stops), from a terminal on the trading machine (``touch HALT`` or ``trades halt``)
or from the web app, and every component checks it before every order. Deleting it (``trades
resume``) turns trading back on. The file name comes from ``halt_file`` in risk/limits.yaml.

The switch stops new orders; it does not close positions, and leaves protective stops already at the
broker in place. ``trades paper flatten`` closes everything (and turns the switch on).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from trades.risk.limits import RiskLimits, load_limits


def halt_path(limits: RiskLimits | None = None, root: Path | None = None) -> Path:
    return (limits or load_limits()).halt_path(root)


def is_halted(limits: RiskLimits | None = None, root: Path | None = None) -> bool:
    return halt_path(limits, root).exists()


def reason(limits: RiskLimits | None = None, root: Path | None = None) -> str | None:
    path = halt_path(limits, root)
    if not path.exists():
        return None
    return path.read_text().strip() or "no reason given"


def halt(why: str = "", limits: RiskLimits | None = None, root: Path | None = None) -> Path:
    path = halt_path(limits, root)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    path.write_text(f"Trading halted {stamp}: {why or 'no reason given'}\n")
    return path


def resume(limits: RiskLimits | None = None, root: Path | None = None) -> bool:
    """Remove the HALT file; True if it existed."""
    path = halt_path(limits, root)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed
