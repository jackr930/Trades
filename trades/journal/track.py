"""The forward record at a glance: what the Track Record page, the health check and the weekly
digest show.

Everything here is computed from the committed journal (``recommendations.csv``), the paper
order log and the decision rule, scored with prices that arrived after each call.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from trades.backtest.runner import sanitize
from trades.core.calendar import next_trading_day, previous_trading_day, trading_days
from trades.journal import rule as decision
from trades.journal.scorer import (
    HORIZONS,
    MIN_OBSERVATIONS,
    Stat,
    _bar,
    by_label,
    by_strategy,
    outcomes,
    sessions_after,
)
from trades.paper.trader import FINAL

BULLISH = ("Strong buy", "Buy")
SLIPPAGE_ALERT_BPS = 50.0  # a paper fill this far from the open is worth a look


def _stat(st: Stat) -> dict[str, float | int]:
    return asdict(st)


def _open_to_open(df: pd.DataFrame | None, entry: date, exit_: date) -> float:
    a, b = _bar(df, entry, "open"), _bar(df, exit_, "open")
    return b / a - 1 if a > 0 and math.isfinite(b) else math.nan


def bullish_curve(rows: pd.DataFrame, bars: dict[str, pd.DataFrame], benchmark: str, last_session: date) -> dict:
    """Each day's Buy and Strong buy symbols, held equally from the next open to the open after,
    minus the benchmark over the same session, chained. Before costs; a picture, not a test."""
    t, v, total = [], [], 1.0
    for d, day in rows[rows["label"].isin(BULLISH)].groupby("session_day"):
        entry, exit_ = next_trading_day(d), sessions_after(d, 2)
        if exit_ > last_session:
            continue

        others = [s for s in day["symbol"] if s != benchmark]  # the benchmark against itself is always zero
        picks = [r for r in (_open_to_open(bars.get(s), entry, exit_) for s in others) if math.isfinite(r)]
        bench = _open_to_open(bars.get(benchmark), entry, exit_)
        if not picks or not math.isfinite(bench):
            continue
        total *= 1 + (float(np.mean(picks)) - bench)
        t.append(int(pd.Timestamp(exit_).tz_localize("UTC").timestamp()))
        v.append(total - 1)
    return {"t": t, "v": v}


def conclusive_date(independent_days: int, last_session: date, horizon: int = 21, need: int = MIN_OBSERVATIONS) -> date:
    """When the 21-session horizon reaches ``need`` independent days, if the journal keeps running."""
    d = last_session
    for _ in range(max(need - independent_days, 0) * horizon):
        d = next_trading_day(d)
    return d


def track_record(
    journal: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    benchmark: str,
    last_session: date,
    rule: dict | None = None,
    rule_dates: dict | None = None,
    paper: pd.DataFrame | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"benchmark": benchmark, "prices_through": last_session.isoformat(), "experiments": []}
    if rule is not None:
        first = str(journal["session_date"].min()) if not journal.empty else None
        out["rule"] = {
            "registered": (rule_dates or {}).get("registered"),
            "last_changed": (rule_dates or {}).get("last_changed"),
            "changed_after_first_row": decision.changed_after(rule_dates or {}, first),
            "forward": rule.get("forward", {}).get("description"),
            "paper": decision.paper_verdict(paper, rule["paper"]) if "paper" in rule else None,
        }
    out["paper"] = paper_summary(paper)
    if journal.empty:
        return sanitize(out)
    for exp_id, rows in journal.groupby("experiment_id", sort=False):
        rows = rows.assign(session_day=pd.to_datetime(rows["session_date"]).dt.date)
        scored = outcomes(rows, bars, benchmark, last_session)
        last = rows[rows["session_day"] == rows["session_day"].max()]
        exp: dict[str, Any] = {
            "id": exp_id,
            "first_session": str(rows["session_day"].min()),
            "last_session": str(rows["session_day"].max()),
            "sessions": int(rows["session_day"].nunique()),
            "rows": len(rows),
            "code_versions": list(dict.fromkeys(rows["code_version"].astype(str))),
            "labels": {h: {k: _stat(s) for k, s in by_label(scored, h).items()} for h in HORIZONS},
            "strategies": {
                h: {k: {"bullish": _stat(a), "other": _stat(b)} for k, (a, b) in by_strategy(scored, h).items()}
                for h in HORIZONS
            },
            "curve": bullish_curve(rows, bars, benchmark, last_session),
            "latest": [
                {
                    "symbol": r["symbol"],
                    "label": r["label"],
                    "score": float(r.get("score", math.nan)),
                    "weight": float(r.get("weight", math.nan)),
                    "votes": json.loads(r["votes"]),
                }
                for r in last.to_dict("records")
            ],
        }
        if rule is not None and "forward" in rule:
            exp["verdict"] = decision.forward_verdict(scored, rule["forward"])
        group = scored[scored["label"].isin(BULLISH)].dropna(subset=["excess_21"])
        n = Stat.of(group.groupby("session_day")["excess_21"].mean(), 21).n if len(group) else 0
        exp["independent_days_21"] = n
        exp["conclusive_from"] = conclusive_date(n, last_session).isoformat()
        out["experiments"].append(exp)
    return sanitize(out)


def paper_summary(paper: pd.DataFrame | None) -> dict[str, Any] | None:
    if paper is None or paper.empty:
        return None
    bps = pd.to_numeric(paper.get("slippage_bps"), errors="coerce").dropna()
    return {
        "orders": len(paper),
        "filled": int((paper["status"] == "filled").sum()) if "status" in paper else 0,
        "with_slippage": len(bps),
        "mean_slippage_bps": float(bps.mean()) if len(bps) else None,
        "worst_slippage_bps": float(bps.max()) if len(bps) else None,
    }


def health(
    journal: pd.DataFrame,
    watchlist: list[str],
    last_session: date,
    paper: pd.DataFrame | None = None,
    lookback: int = 5,
) -> list[str]:
    """Problems worth an alert: missed sessions, incomplete days, stuck or badly filled paper orders.

    A missed session cannot be recorded afterwards (that would be look-ahead), so a gap is
    reported only while it is among the last ``lookback`` sessions."""
    problems = []
    if journal.empty:
        return ["The journal has no rows yet: has the workflow run after a close?"]
    days = set(pd.to_datetime(journal["session_date"]).dt.date)
    first = min(days)
    recent = trading_days(max(first, last_session - timedelta(days=lookback * 2 + 7)), last_session)
    missing = [d for d in recent[-lookback:] if d not in days]
    if missing:
        problems.append(
            f"No journal rows for {', '.join(map(str, missing))}: did the workflow fail or not run? A missed "
            f"session cannot be recorded afterwards; this note drops off after {lookback} sessions."
        )
    latest = journal[pd.to_datetime(journal["session_date"]).dt.date == max(days)]
    absent = sorted(set(watchlist) - set(latest["symbol"]))
    if absent:
        problems.append(f"The latest session ({max(days)}) has no row for {', '.join(absent)}.")
    if paper is not None and not paper.empty:
        open_status = ~paper["status"].isin(FINAL)
        trade_days = pd.to_datetime(paper["trade_date"]).dt.date
        stuck = paper[open_status & (trade_days < previous_trading_day(last_session))]
        if len(stuck):
            problems.append(f"{len(stuck)} paper order(s) are still not final days after their trade date.")
        bps = pd.to_numeric(paper.get("slippage_bps"), errors="coerce")
        bad = paper[bps.abs() > SLIPPAGE_ALERT_BPS]
        if len(bad):
            problems.append(
                f"{len(bad)} paper fill(s) landed more than {SLIPPAGE_ALERT_BPS:g} bps from the open "
                f"({', '.join(bad['symbol'].astype(str).head(5))})."
            )
    return problems


def digest(record: dict[str, Any], week_end: date) -> str:
    """A plain-English weekly summary of a ``track_record`` result."""
    lines = [f"# Weekly digest: week ending {week_end}", ""]
    if not record["experiments"]:
        return "\n".join([*lines, "The journal has no rows yet."]) + "\n"
    for exp in record["experiments"]:
        latest = exp["latest"]
        buys = [r["symbol"] for r in latest if r["label"] in BULLISH]
        sells = [r["symbol"] for r in latest if r["label"].startswith(("Sell", "Strong sell"))]
        lines += [
            f"## Experiment `{exp['id']}`",
            "",
            f"- {exp['sessions']} sessions recorded since {exp['first_session']}; the latest is {exp['last_session']}.",
            f"- Latest calls: buy {', '.join(buys) or 'nothing'}; avoid {', '.join(sells) or 'nothing'}; "
            f"the rest neutral.",
        ]
        if exp.get("verdict"):
            lines.append(f"- Decision rule (forward): **{exp['verdict']['status']}**: {exp['verdict']['detail']}")
        lines.append(
            f"- {exp['independent_days_21']} independent 21-session observations so far; the rule's "
            f"{MIN_OBSERVATIONS} arrive around {exp['conclusive_from']} if the journal keeps running."
        )
        curve = exp["curve"]["v"]
        if curve:
            lines.append(
                f"- Buy calls held for a day each, minus {record['benchmark']} and before costs: "
                f"{curve[-1]:+.2%} so far (a picture, not a test)."
            )
        lines.append("")
    if record.get("paper"):
        p = record["paper"]
        slip = "n/a" if p["mean_slippage_bps"] is None else f"{p['mean_slippage_bps']:+.1f} bps"
        lines += [f"Paper account: {p['orders']} orders, {p['filled']} filled; mean slippage {slip}.", ""]
    lines.append("Nothing here is investment advice. Small samples are mostly noise: judge only by the rule.")
    return "\n".join(lines) + "\n"
