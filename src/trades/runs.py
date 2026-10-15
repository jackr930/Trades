"""Research runs: one folder per run in ``runs/``, enough to reproduce it.

::

    runs/<run id>/
      run.json        the inputs (spec version and rules hash, git commit, data fingerprint,
                      configuration, seed) and the results, including the acceptance checklist
      report.md       the same as a readable report
      variants.json   every parameter set evaluated; they all count toward the deflated Sharpe ratio
      artifacts/      bulky outputs (equity curves, trades), not committed to git

The playbook's rule: if you cannot reproduce a result from its folder, it does not count. Runs are
also the record of two things that must never be forgotten: how many variants of a spec have been
tried (``variants_tested``) and whether its locked test has been opened (``locked_test_run``).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trades import config

RUN_FILE = "run.json"
WATCHED = ("src", "specs", "config", "risk")  # uncommitted changes here make a run irreproducible


def runs_dir() -> Path:
    return config.path("runs")


def new_run_id(spec_key: str, now: datetime | None = None) -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"{now:%Y%m%dT%H%M%S}-{spec_key}"


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=config.repo_root(), capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def git_state() -> dict[str, Any]:
    """The commit, the code's tree hash, and any uncommitted changes that would make a run irreproducible."""
    status = _git("status", "--porcelain", "--", *WATCHED)
    return {
        "commit": _git("rev-parse", "HEAD"),
        "code_tree": _git("rev-parse", "HEAD:src/trades"),
        "dirty": [line[3:] for line in (status or "").splitlines() if line.strip()],
        "available": status is not None,
    }


def committed(path: Path) -> bool:
    """Is ``path`` tracked by git with no uncommitted changes?"""
    rel = str(Path(path).resolve().relative_to(config.repo_root()))
    tracked = _git("ls-files", "--error-unmatch", rel)
    changed = _git("status", "--porcelain", "--", rel)
    return tracked is not None and not changed


def fingerprint(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update(pd.DatetimeIndex(df.index).as_unit("s").asi8.tobytes())
    h.update(np.ascontiguousarray(df[["open", "high", "low", "close", "volume"]].to_numpy(float)).tobytes())
    return h.hexdigest()[:16]


def data_manifest(data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Rows, first and last bar and a content hash per symbol, and one hash for the whole set."""
    symbols = {
        s: {
            "rows": len(df),
            "first": str(df.index[0]) if len(df) else None,
            "last": str(df.index[-1]) if len(df) else None,
            "sha256": fingerprint(df),
        }
        for s, df in sorted(data.items())
    }
    whole = hashlib.sha256(json.dumps(symbols, sort_keys=True).encode()).hexdigest()[:16]
    return {"symbols": symbols, "hash": whole}


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return v if np.isfinite(v) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    return obj


def write_run(run: dict[str, Any], report: str, variants: list[dict[str, Any]],
              artifacts: dict[str, pd.DataFrame | pd.Series] | None = None) -> Path:
    folder = runs_dir() / run["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / RUN_FILE).write_text(json.dumps(_clean(run), indent=1, sort_keys=False) + "\n")
    (folder / "report.md").write_text(report)
    (folder / "variants.json").write_text(json.dumps(_clean(variants), indent=1) + "\n")
    if artifacts:
        art = folder / "artifacts"
        art.mkdir(exist_ok=True)
        for name, frame in artifacts.items():
            frame.to_csv(art / f"{name}.csv")
    return folder


def load_run(run_id: str) -> dict[str, Any]:
    path = runs_dir() / run_id / RUN_FILE
    if not path.exists():
        raise FileNotFoundError(f"no run {run_id!r} in {runs_dir()}")
    return json.loads(path.read_text())


def list_runs(spec_key: str | None = None) -> list[dict[str, Any]]:
    """Every run (or every run of one spec version), oldest first."""
    out = []
    folder = runs_dir()
    if not folder.exists():
        return out
    for path in sorted(folder.glob(f"*/{RUN_FILE}")):
        try:
            run = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if spec_key is None or run.get("spec", {}).get("key") == spec_key:
            out.append(run)
    return sorted(out, key=lambda r: r.get("created", ""))


def latest(spec_key: str) -> dict[str, Any] | None:
    runs = list_runs(spec_key)
    return runs[-1] if runs else None


def variants_tested(spec_key: str) -> list[dict[str, Any]]:
    """Distinct parameter sets evaluated on this spec version across all its runs."""
    seen: dict[str, dict[str, Any]] = {}
    folder = runs_dir()
    for run in list_runs(spec_key):
        path = folder / run["id"] / "variants.json"
        if not path.exists():
            continue
        for v in json.loads(path.read_text()):
            seen.setdefault(json.dumps(v.get("params", {}), sort_keys=True), v)
    return list(seen.values())


def locked_test_run(spec_key: str) -> dict[str, Any] | None:
    """The run that opened this spec version's locked test, if any (it can only happen once)."""
    for run in list_runs(spec_key):
        if run.get("locked_test", {}).get("opened"):
            return run
    return None
