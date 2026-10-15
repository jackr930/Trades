"""The pre-registered decision rule: what counts as "beats buy-and-hold", fixed in advance.

``journal/decision_rule.json`` says, before any result is seen, which numbers would count
as success for the backtest, the forward journal and the paper account. Its git history
is its registration date, and the report flags any change made after the journal's first
row: moving the goalposts after looking is the easiest way to fool yourself.

Each verdict is PASS, FAIL or NOT YET (not enough evidence to decide either way).
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from trades.journal.experiment import JOURNAL_DIR

RULE_FILE = JOURNAL_DIR / "decision_rule.json"


def load_rule(path: Path | str = RULE_FILE) -> dict[str, Any] | None:
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else None


def rule_history(path: Path | str = RULE_FILE, repo: Path | str = ".") -> dict[str, str | None]:
    """First and latest commit dates (ISO) of the rule file; None outside git or if uncommitted."""

    def dates(*extra: str) -> list[str]:
        try:
            out = subprocess.run(
                ["git", "log", "--format=%cI", *extra, "--", str(path)],
                cwd=repo, capture_output=True, text=True, timeout=10, check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        return out.stdout.split()

    try:  # a shallow clone only knows its latest commit: its dates would be wrong, not just incomplete
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"], cwd=repo, capture_output=True, text=True, timeout=10
        ).stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        shallow = False
    history = [] if shallow else dates()
    return {"registered": history[-1] if history else None, "last_changed": history[0] if history else None}


def _verdict(status: str, detail: str) -> dict[str, str]:
    return {"status": status, "detail": detail}


def forward_verdict(scored: pd.DataFrame, rule: dict[str, Any]) -> dict[str, str]:
    """``scored``: one experiment's journal rows with ``excess_{h}`` and ``session_day`` columns."""
    from trades.journal.scorer import Stat  # the scorer imports this module

    h = int(rule["horizon"])
    col = f"excess_{h}"
    rows = scored[scored["label"].isin(rule["labels"])].dropna(subset=[col]) if col in scored else scored.iloc[:0]
    st = Stat.of(rows.groupby("session_day")[col].mean() if len(rows) else pd.Series(dtype=float), h)
    need = int(rule["min_independent_days"])
    t = f"{st.t:.2f}" if math.isfinite(st.t) else "n/a"
    summary = f"{st.n} independent days, mean excess {st.mean:+.2%} per {h} sessions, t = {t}" if st.n else "no finished windows yet"
    if st.n < need:
        return _verdict("NOT YET", f"{summary}; the rule needs {need} independent days.")
    ok = st.mean > rule["min_mean_excess"] and math.isfinite(st.t) and st.t >= rule["min_t"]
    return _verdict("PASS" if ok else "FAIL", summary + ".")


def paper_verdict(orders: pd.DataFrame | None, rule: dict[str, Any]) -> dict[str, str]:
    bps = pd.to_numeric(orders["slippage_bps"], errors="coerce").dropna() if orders is not None and len(orders) else []
    need = int(rule["min_fills"])
    if len(bps) < need:
        return _verdict("NOT YET", f"{len(bps)} fills with a known slippage; the rule needs {need}.")
    mean = float(bps.mean())
    ok = mean <= rule["max_mean_slippage_bps"]
    return _verdict("PASS" if ok else "FAIL", f"mean slippage {mean:+.1f} bps over {len(bps)} fills.")


def backtest_verdict(
    strategy: dict[str, float | None],
    benchmark: dict[str, float | None],
    p_growth: float | None,
    rule: dict[str, Any],
    n_tests: int = 1,
) -> dict[str, str]:
    """``n_tests``: how many versions (the main one plus its variants) are judged; the required
    probability is raised accordingly (Bonferroni), since testing more versions finds more luck."""
    key = rule["metric"]
    s, b = strategy.get(key), benchmark.get(key)
    if s is None or b is None or p_growth is None or not math.isfinite(p_growth):
        return _verdict("NOT YET", "missing numbers for the comparison.")
    need = 1 - (1 - rule["min_probability"]) / max(n_tests, 1)
    ok = s > b and p_growth >= need
    return _verdict(
        "PASS" if ok else "FAIL",
        f"{key} {s:.2%} vs {b:.2%} for {rule['must_beat']}; beats it on growth in {p_growth:.1%} of resamples "
        f"(the rule needs {need:.1%}).",
    )


def changed_after(history: dict[str, str | None], first_row: str | None) -> bool:
    """Was the rule last changed after the journal's first recorded session (ISO dates)?"""
    return bool(history.get("last_changed") and first_row and history["last_changed"][:10] > first_row)


BACKTEST_CHECK = "backtest_check.json"  # written by scripts/consensus_check.py for the registered run


def decision(
    backtest: dict[str, Any] | None,
    forward: dict[str, str] | None,
    paper: dict[str, str] | None,
    changed_after_first_row: bool = False,
) -> dict[str, Any]:
    """The answer the whole app exists to give: would the evidence justify real money?

    YES only when every pre-registered check passed: the hindsight-free backtest, the forward
    journal and the paper fills. NO as soon as one failed. Otherwise NOT YET. A rule edited
    after the journal started cannot vouch for anything, so that alone makes it INVALID.
    """
    checks = [
        {"name": "Backtest on hindsight-free symbols, after costs and taxes", **(backtest or _verdict("NOT YET", "not run yet: run scripts/consensus_check.py and commit journal/backtest_check.json"))},
        {"name": "Forward journal: calls recorded before their outcome", **(forward or _verdict("NOT YET", "no journal yet"))},
        {"name": "Paper fills: are the assumed costs real?", **(paper or _verdict("NOT YET", "no paper fills yet"))},
    ]
    statuses = {c["status"] for c in checks}
    if changed_after_first_row:
        status, text = "INVALID", (
            "The decision rule was edited after the journal started, so passing it proves nothing. Restore the "
            "registered rule, or start a new experiment and wait again."
        )
    elif "FAIL" in statuses:
        status, text = "NO", (
            "At least one pre-registered check failed. The honest choice is a low-cost index fund held for the long run."
        )
    elif statuses == {"PASS"}:
        status, text = "YES, WITH CARE", (
            "Every pre-registered check passed. That is evidence, not a guarantee: if you go ahead, do it yourself at "
            "your broker, with an amount you could lose, and keep the journal running. This app never places real orders."
        )
    else:
        status, text = "NOT YET", (
            "Not enough evidence either way. Until there is, buying and holding an index fund is the default to beat."
        )
    return {"status": status, "summary": text, "checks": checks}
