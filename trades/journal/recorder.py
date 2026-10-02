"""``trades journal record``: log today's Live Desk recommendations after the close.

Each row is written once the session has closed, from completed bars only, and never for a
session the data does not reach yet: if the provider's latest bar is not today's session,
the recorder retries with backoff and then fails without writing anything. Rows are
appended to ``journal/recommendations.csv`` and keyed by (session date, symbol, experiment
id), so running twice on the same day adds nothing.
"""

from __future__ import annotations

import csv
import json
import time
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trades.advisor import Recommender
from trades.core.calendar import NY, is_trading_day, last_completed_session
from trades.data.base import DataError
from trades.journal.experiment import JOURNAL_DIR, Experiment, code_version

JOURNAL_FILE = JOURNAL_DIR / "recommendations.csv"
COLUMNS = (
    "run_time_utc",
    "session_date",
    "symbol",
    "close",
    "score",
    "label",
    "bullish",
    "bearish",
    "neutral",
    "votes",
    "side",
    "weight",
    "stop",
    "experiment_id",
    "code_version",
    "provider",
)
RETRY_DELAYS = (60.0, 180.0, 600.0)  # seconds to wait for the provider to publish today's bar

# fetch(provider, symbols, start) -> (frames by symbol, errors by symbol)
Fetch = Callable[[str, list[str], date], tuple[dict[str, pd.DataFrame], dict[str, str]]]


class StaleData(DataError):
    """The provider's latest bar is not the session being recorded."""


def _last_dates(frames: dict[str, pd.DataFrame]) -> dict[str, date | None]:
    return {s: (df.index[-1].date() if len(df) else None) for s, df in frames.items()}


def fetch_session(
    exp: Experiment,
    fetch: Fetch,
    session: date,
    sleep: Callable[[float], None] = time.sleep,
    delays: Sequence[float] = RETRY_DELAYS,
    log: Callable[[str], None] = print,
) -> tuple[dict[str, pd.DataFrame], str]:
    """Bars through ``session`` for the watchlist and benchmark, from the first provider that
    has that session for every symbol. Raises if none does after retrying."""
    symbols = list(dict.fromkeys([*exp.watchlist, exp.benchmark]))
    problems = []
    for provider in exp.providers:
        for attempt in range(len(delays) + 1):
            try:
                frames, errors = fetch(provider, symbols, exp.history_start)
            except DataError as exc:  # e.g. missing credentials: try the next provider
                problems.append(f"{provider}: {exc}")
                break
            if errors:
                problems.append(f"{provider}: {errors}")
                break
            # Completed bars only: nothing after the session being recorded.
            frames = {s: df[df.index.date <= session] for s, df in frames.items()}
            stale = {s: d for s, d in _last_dates(frames).items() if d != session}
            if not stale:
                return frames, provider
            if attempt == len(delays):
                problems.append(f"{provider}: latest bars {stale} are not the {session} session")
                break
            log(f"{provider}: no {session} bar yet for {sorted(stale)}; retrying in {delays[attempt]:.0f}s")
            sleep(delays[attempt])
    raise StaleData(f"No provider has complete {session} bars; nothing recorded. " + "; ".join(problems))


def _vote_key(v: dict[str, Any]) -> str:
    return v["strategy_id"] + (f":{'/'.join(v['group'])}" if v.get("group") and len(v["group"]) == 2 else "")


def build_rows(
    exp: Experiment, frames: dict[str, pd.DataFrame], session: date, provider: str, run_time: datetime, version: str
) -> list[dict[str, Any]]:
    """One row per watchlist symbol: the Live Desk's call on the close of ``session``."""
    data = {s: frames[s] for s in exp.watchlist}
    res = Recommender().recommend(data, exp.advisor_settings(), namespace=f"journal:{provider}")
    rows = []
    for rec in sorted(res["recommendations"], key=lambda r: exp.watchlist.index(r["symbol"])):
        c, sz = rec["consensus"], rec["sizing"]
        votes = {
            _vote_key(v): (None if v["state"] in ("warming_up", "error") else v["vote"]) for v in rec["votes"]
        }
        rows.append(
            {
                "run_time_utc": run_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "session_date": session.isoformat(),
                "symbol": rec["symbol"],
                "close": round(rec["price"], 4),
                "score": round(c["score"], 6),
                "label": c["label"],
                "bullish": c["bullish"],
                "bearish": c["bearish"],
                "neutral": c["neutral"],
                "votes": json.dumps(votes, separators=(",", ":")),
                "side": sz["side"],
                "weight": round(sz["weight"], 6),
                "stop": "" if sz["stop"] is None else round(sz["stop"], 4),
                "experiment_id": exp.id,
                "code_version": version,
                "provider": provider,
            }
        )
    return rows


def existing_keys(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    with path.open(newline="") as f:
        return {(r["session_date"], r["symbol"], r["experiment_id"]) for r in csv.DictReader(f)}


def append_rows(path: Path, rows: list[dict[str, Any]]) -> int:
    """Append rows not already in the journal; returns how many were written."""
    have = existing_keys(path)
    new = [r for r in rows if (r["session_date"], r["symbol"], r["experiment_id"]) not in have]
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerows(new)
    return len(new)


def record(
    exp: Experiment,
    fetch: Fetch,
    path: Path = JOURNAL_FILE,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
    delays: Sequence[float] = RETRY_DELAYS,
    log: Callable[[str], None] = print,
) -> int:
    """Record today's session if it is a trading day that has closed. Returns rows written."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(NY).date()
    if not is_trading_day(today):
        log(f"{today} is not a trading day: nothing to record.")
        return 0
    session = last_completed_session(now)
    if session != today:
        log(f"The {today} session has not closed yet: nothing to record.")
        return 0
    frames, provider = fetch_session(exp, fetch, session, sleep, delays, log)
    rows = build_rows(exp, frames, session, provider, now, code_version())
    written = append_rows(path, rows)
    log(f"Recorded {written} new row(s) for {session} (experiment {exp.id}, data from {provider}).")
    return written
