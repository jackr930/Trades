"""A research log: every backtest and parameter search you run, counted.

Every test you run and then discard is a hidden trial. Try enough configurations and one
will look good by luck alone, so a result should be judged against how many were tried.
This log records each Strategy Lab backtest and grid search (and each `trades backtest`)
in ``$TRADES_HOME/trials.jsonl``, and reports the Deflated Sharpe Ratio of the latest
result given every distinct configuration tried on the same symbols (Bailey & Lopez de
Prado 2014). Running the same configuration again is not a new trial.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from trades.config import trades_home
from trades.core import stats

MAX_ENTRIES = 20_000
_lock = threading.Lock()


def log_path() -> Path:
    return trades_home() / "trials.jsonl"


def config_key(strategy_id: str, params: dict[str, Any], sizing: dict[str, Any] | None = None) -> str:
    raw = json.dumps({"id": strategy_id, "params": params, "sizing": sizing}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def record(
    kind: str, strategy_id: str, config: str, symbols: list[str], sharpe: float | None, path: Path | None = None
) -> None:
    """Append one trial (``sharpe`` is annualised; None when it could not be computed)."""
    path = path or log_path()
    entry = {
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": kind,
        "strategy": strategy_id,
        "config": config,
        "symbols": sorted(symbols),
        "sharpe": sharpe if sharpe is not None and math.isfinite(sharpe) else None,
    }
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        lines = path.read_text().splitlines()
        if len(lines) > MAX_ENTRIES:  # keep the log bounded
            path.write_text("\n".join(lines[-MAX_ENTRIES:]) + "\n")


def load(symbols: list[str] | None = None, path: Path | None = None) -> list[dict[str, Any]]:
    path = path or log_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if symbols is None or entry.get("symbols") == sorted(symbols):
            out.append(entry)
    return out


def summarise(
    symbols: list[str], metrics: dict[str, float | None], n_obs: int, ppy: int, path: Path | None = None
) -> dict[str, Any]:
    """How the latest result looks given every configuration tried on these symbols."""
    entries = load(symbols, path)
    latest: dict[str, float] = {}  # one Sharpe ratio per distinct configuration (its latest run)
    for e in entries:
        if e.get("sharpe") is not None:
            latest[f"{e['strategy']}:{e['config']}"] = e["sharpe"]
    n_configs = len(latest)
    sharpe = metrics.get("sharpe")
    out: dict[str, Any] = {"runs": len(entries), "configurations": n_configs, "deflated_sharpe": None}
    if sharpe is None or not math.isfinite(sharpe) or n_obs < 3:
        out["interpretation"] = "Not enough data to judge this result against your earlier tests."
        return out
    srs = np.array(list(latest.values()), dtype=float) / math.sqrt(ppy)
    var = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
    sr_pp = sharpe / math.sqrt(ppy)
    skew = metrics.get("skew") if metrics.get("skew") is not None else 0.0
    kurt = metrics.get("kurtosis") if metrics.get("kurtosis") is not None else 3.0
    dsr = stats.deflated_sharpe_ratio(sr_pp, n_obs, skew, kurt, max(n_configs, 1), var)
    out["deflated_sharpe"] = dsr if math.isfinite(dsr) else None
    out["expected_max_sharpe"] = stats.expected_max_sharpe(max(n_configs, 1), var) * math.sqrt(ppy)
    if n_configs <= 1:
        out["interpretation"] = (
            "The first configuration you have tested on these symbols: no correction for multiple tries yet."
        )
    else:
        out["interpretation"] = (
            f"You have tested {n_configs} configurations on these symbols ({len(entries)} runs). Picking the best "
            f"of that many skill-less strategies would give a Sharpe ratio of about {out['expected_max_sharpe']:.2f} "
            f"by luck; allowing for that, the chance this result reflects real skill is "
            f"{'unknown' if out['deflated_sharpe'] is None else f'{dsr:.0%}'} (Deflated Sharpe)."
        )
    return out


def record_and_summarise(
    kind: str, spec, symbols: list[str], metrics: dict[str, float | None], n_obs: int, ppy: int,
    path: Path | None = None,
) -> dict[str, Any]:
    """Log a backtest of ``spec`` (a StrategySpec) and judge it against the earlier ones."""
    record(kind, spec.id, config_key(spec.id, spec.params, spec.sizing), symbols, metrics.get("sharpe"), path)
    return summarise(symbols, metrics, n_obs, ppy, path)


def record_grid(
    strategy_id: str, symbols: list[str], result: dict[str, Any], base_params=None, path: Path | None = None
) -> None:
    """Log every combination of a grid search: each one is a trial."""
    for row in result.get("rows", []):
        params = {**(base_params or {}), **row["params"]}
        record("grid", strategy_id, config_key(strategy_id, params), symbols, row["metrics"].get("sharpe"), path)
