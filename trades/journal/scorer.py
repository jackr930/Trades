"""``trades journal score``: how did the journal's recommendations do against SPY?

A row's outcome over a horizon of ``h`` sessions is the symbol's return from the open of
the next session (when the backtest engine would have filled) to the close ``h`` sessions
after the recommendation, minus SPY's return over the same window. Only rows whose window
has fully passed are scored.

Symbols recommended on the same day move together, so they are not independent evidence:
the outcomes are first averaged within each day (one number per label per day). Windows of
consecutive days overlap too (two 21-session windows a day apart share 20 sessions), so the
statistics use only days at least ``h`` sessions apart. Each observation is then
independent, and a plain t-statistic is valid. Experiments are scored separately.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from trades.core.calendar import next_trading_day, trading_days

HORIZONS = (5, 21)
MIN_OBSERVATIONS = 60


def read_journal(path) -> pd.DataFrame:
    """The journal CSV with ids, versions and symbols kept as text: read naively, an id such as
    "123456789012" would become a number and a symbol such as "NA" would become missing."""
    from pathlib import Path

    if not Path(path).exists():
        return pd.DataFrame()
    return pd.read_csv(
        path,
        dtype={"session_date": str, "symbol": str, "experiment_id": str, "code_version": str, "label": str, "votes": str},
        keep_default_na=False,
        na_values={c: [""] for c in ("close", "score", "weight", "stop", "bullish", "bearish", "neutral")},
    )


def sessions_after(d: date, n: int) -> date:
    for _ in range(n):
        d = next_trading_day(d)
    return d


def _bar(df: pd.DataFrame | None, d: date, column: str) -> float:
    if df is None:
        return math.nan
    ts = pd.Timestamp(d).tz_localize("UTC")
    return float(df[column].get(ts, math.nan))


def window_return(df: pd.DataFrame | None, session: date, h: int) -> float:
    """Return from the next session's open to the close ``h`` sessions after ``session``."""
    entry = _bar(df, next_trading_day(session), "open")
    exit_ = _bar(df, sessions_after(session, h), "close")
    return exit_ / entry - 1.0 if entry > 0 and math.isfinite(exit_) else math.nan


def outcomes(
    rows: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    benchmark: str,
    last_session: date,
    horizons: Iterable[int] = HORIZONS,
) -> pd.DataFrame:
    """``rows`` plus an ``excess_{h}`` column per horizon (NaN until the window has passed).

    The benchmark's own rows stay NaN: against itself its excess is always exactly zero, which
    would only dilute every average it joined."""
    out = rows.copy()
    sessions = pd.to_datetime(out["session_date"]).dt.date
    for h in horizons:
        values = []
        for sym, d in zip(out["symbol"], sessions, strict=True):
            if sym == benchmark or sessions_after(d, h) > last_session:
                values.append(math.nan)
                continue
            values.append(window_return(bars.get(sym), d, h) - window_return(bars.get(benchmark), d, h))
        out[f"excess_{h}"] = values
    return out


def independent_days(days: Iterable[date], h: int) -> list[date]:
    """The earliest day, then each next day at least ``h`` sessions after the last one kept."""
    days = sorted(set(days))
    if not days:
        return []
    position = {d: i for i, d in enumerate(trading_days(days[0], days[-1]))}  # journal days are sessions
    kept = [days[0]]
    for d in days[1:]:
        if position[d] - position[kept[-1]] >= h:
            kept.append(d)
    return kept


@dataclass
class Stat:
    n: int
    mean: float
    hit_rate: float
    t: float

    @classmethod
    def of(cls, daily: pd.Series, h: int) -> Stat:
        """Statistics of a per-day series over its non-overlapping days."""
        daily = daily.dropna()
        keep = independent_days(daily.index, h)
        x = daily.loc[keep].to_numpy(float)
        n = len(x)
        if n == 0:
            return cls(0, math.nan, math.nan, math.nan)
        sd = float(np.std(x, ddof=1)) if n > 1 else math.nan
        t = float(np.mean(x) / (sd / math.sqrt(n))) if n > 1 and sd > 0 else math.nan
        return cls(n, float(np.mean(x)), float(np.mean(x > 0)), t)


def by_label(scored: pd.DataFrame, h: int) -> dict[str, Stat]:
    col = f"excess_{h}"
    s = scored.dropna(subset=[col])
    daily = s.groupby(["label", "session_day"])[col].mean()
    return {label: Stat.of(daily.loc[label], h) for label in daily.index.get_level_values(0).unique()}


def by_strategy(scored: pd.DataFrame, h: int) -> dict[str, tuple[Stat, Stat]]:
    """Per strategy: (days it voted bullish, days it voted neutral or bearish), averaged per day."""
    col = f"excess_{h}"
    s = scored.dropna(subset=[col])
    votes = [json.loads(v) for v in s["votes"]]
    keys = sorted({k for v in votes for k in v})
    out = {}
    for key in keys:
        vote = pd.Series([v.get(key) for v in votes], index=s.index, dtype=object)
        bull = s[vote == 1].groupby("session_day")[col].mean()
        other = s[vote.isin([0, -1])].groupby("session_day")[col].mean()  # active but not bullish
        out[key] = (Stat.of(bull, h), Stat.of(other, h))
    return out


LABEL_ORDER = ("Strong buy", "Buy", "Neutral", "Sell / avoid", "Strong sell / avoid", "Sell / short", "Strong sell / short")


def _pct(x: float) -> str:
    return "–" if not math.isfinite(x) else f"{x:+.2%}"


def _num(x: float) -> str:
    return "–" if not math.isfinite(x) else f"{x:.2f}"


def paper_slippage(orders: pd.DataFrame, assumed_bps: float) -> list[str]:
    """Report lines: the paper account's fills against the open the backtest engine assumes."""
    lines = ["", "## Paper trading fills", ""]
    filled = orders[pd.to_numeric(orders.get("slippage_bps"), errors="coerce").notna()] if len(orders) else orders
    if filled.empty:
        return [*lines, f"{len(orders)} paper orders logged; none with a known fill and open price yet."]
    bps = pd.to_numeric(filled["slippage_bps"])
    lines += [
        f"Slippage of each fill against that session's open, in basis points (positive = cost); the backtests "
        f"assume {assumed_bps:g} bps.",
        "",
        "| Order type | Fills | Mean slippage | Median slippage |",
        "| --- | --- | --- | --- |",
    ]
    for tif, group in [("all", bps), *((t, bps[filled["time_in_force"] == t]) for t in ("opg", "day"))]:
        if len(group):
            lines.append(f"| {tif} | {len(group)} | {group.mean():+.1f} bps | {group.median():+.1f} bps |")
    lines += [
        "",
        "Alpaca's paper account simulates fills from quotes; it does not run a real opening auction, so these "
        "numbers are only a rough check of the cost assumption.",
    ]
    return lines


def render_report(
    journal: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    benchmark: str,
    last_session: date,
    paper_orders: pd.DataFrame | None = None,
    assumed_bps: float = 5.0,
) -> str:
    """The Markdown report for every experiment in ``journal`` (and the paper fills, if any)."""
    lines = [
        "# Forward journal report",
        "",
        f"Scored from `journal/recommendations.csv` with prices through {last_session}.",
        "",
        f"Outcome of a recommendation: the symbol's return from the next session's open to the close 5 or 21 "
        f"sessions later, minus {benchmark}'s return over the same window. Outcomes are averaged within each day "
        "first (symbols move together), and only days at least one horizon apart are used, so every observation "
        "is independent. Each experiment is scored on its own. Outcomes are before trading costs and taxes, "
        f"and {benchmark}'s own rows are left out (against itself its excess is always zero).",
    ]
    paper = paper_slippage(paper_orders, assumed_bps) if paper_orders is not None else []
    if journal.empty:
        return "\n".join([*lines, "", "The journal is empty so far.", *paper]) + "\n"
    for exp_id, rows in journal.groupby("experiment_id", sort=False):
        rows = rows.assign(session_day=pd.to_datetime(rows["session_date"]).dt.date)
        scored = outcomes(rows, bars, benchmark, last_session)
        versions = ", ".join(f"`{v}`" for v in dict.fromkeys(rows["code_version"]))
        lines += [
            "",
            f"## Experiment `{exp_id}`",
            "",
            f"{rows['session_day'].nunique()} sessions recorded ({rows['session_day'].min()} to "
            f"{rows['session_day'].max()}), {len(rows)} rows; code version(s) {versions}.",
        ]
        for h in HORIZONS:
            col = f"excess_{h}"
            n_scored = int(scored[col].notna().sum())
            labels = by_label(scored, h)
            lines += ["", f"### {h}-session horizon", "", f"{n_scored} of {len(rows)} rows have a finished window."]
            if not labels:
                continue
            lines += [
                "",
                f"| Label | Independent days | Mean excess vs {benchmark} | Hit rate vs {benchmark} | t-stat |",
                "| --- | --- | --- | --- | --- |",
            ]
            order = sorted(labels, key=lambda k: LABEL_ORDER.index(k) if k in LABEL_ORDER else 99)
            for label in order:
                st = labels[label]
                hit = "–" if not math.isfinite(st.hit_rate) else f"{st.hit_rate:.0%}"
                lines.append(f"| {label} | {st.n} | {_pct(st.mean)} | {hit} | {_num(st.t)} |")
            lines += [
                "",
                "| Strategy | Bullish: days | Bullish: mean excess | Not bullish: days | Not bullish: mean excess |",
                "| --- | --- | --- | --- | --- |",
            ]
            for key, (bull, other) in by_strategy(scored, h).items():
                lines.append(f"| {key} | {bull.n} | {_pct(bull.mean)} | {other.n} | {_pct(other.mean)} |")
            fewest = min(st.n for st in labels.values())
            if fewest < MIN_OBSERVATIONS:
                lines += [
                    "",
                    f"> **Too early to conclude anything.** Some rows above rest on fewer than {MIN_OBSERVATIONS} "
                    "independent observations. With so few, an average can easily be large and still be luck: a "
                    "t-statistic below about 2 (or 3, given how many numbers this report shows) is no evidence of "
                    "skill, and even a large one can flip as more days arrive.",
                ]
    return "\n".join([*lines, *paper]) + "\n"
