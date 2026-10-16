"""``trades track``: does the paper account trade like its backtest?

The playbook's test: replay the paper period through the backtest engine (the same strategy,
costs, risk limits and starting equity, on the provider's historical bars) and compare, decision
by decision and fill by fill:

* **signal match** (gate: at least 95%): of the bars where the paper runner or the replay wanted a
  position, the share where both wanted the same direction. A miss means the data differed (live
  bars against historical ones), the risk engine saw a different account, or the code changed;
* **fill cost against the model** (gate: within 1.5x): each paper fill against the replay's fill
  of the same trade, in basis points. The replay's price already includes the modelled cost, so
  the paper's actual cost is the model's plus the difference;
* **expectancy** (gate: positive, and at least half the backtest's): the mean net result per paper
  trade, as a fraction of equity, against the research run's validation expectancy;
* **daily P&L correlation** (gate: at least 0.8): the paper account's daily returns against the
  replay's.

Paper trades are built from the broker's fills (``paper/<key>/fills.csv``), so trades that a
protective stop closed are counted like any other.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from trades import config, runs
from trades.data.calendar import NY, trading_days
from trades.data.timeframes import Timeframe
from trades.engine.backtest import backtest_strategy
from trades.engine.paper import PaperRecords
from trades.execution import costs
from trades.execution.ledger import Ledger
from trades.execution.sim_broker import BacktestConfig
from trades.risk import RiskLimits, load_limits
from trades.validation.robustness import expectancy

Fetch = Callable[[str, list[str], str, date, date], tuple[dict[str, pd.DataFrame], dict[str, str]]]


class TrackingError(RuntimeError):
    pass


@dataclass
class Tracking:
    spec: str
    first: str | None = None  # the first and last paper sessions
    last: str | None = None
    sessions: int = 0
    weeks: float = 0.0
    decisions: int = 0
    missed_sessions: int = 0  # trading days inside the period without a single decision
    missed_bars: int = 0  # bars the replay decided on, inside decided sessions, that the runner skipped
    compared: int = 0  # bar-symbol pairs where either side wanted a position
    signal_match: float | None = None  # after the risk engine
    strategy_match: float | None = None  # before it: data and code alone
    trades: int = 0
    expectancy: dict[str, Any] | None = None  # paper trades
    replay_expectancy: dict[str, Any] | None = None  # the replay's trades in the same sessions
    backtest_expectancy: float | None = None  # the research run's validation mean
    fills: dict[str, Any] | None = None
    daily_correlation: float | None = None
    daily_days: int = 0
    errors_recent: int = 0
    resize_skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sign(x: Any) -> int:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0
    return 0 if not math.isfinite(v) or abs(v) <= 1e-12 else (1 if v > 0 else -1)


def _session(t: Any) -> date:
    ts = pd.Timestamp(t)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.date() if (ts.hour, ts.minute, ts.second) == (0, 0, 0) else ts.tz_convert(NY).date()


def _latest_per_bar(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One decision per bar: the last one recorded (a rerun of the same bar replaces the first)."""
    by_time = {pd.Timestamp(d["time"]): d for d in decisions}
    return [by_time[t] for t in sorted(by_time)]


def model_bps(tag: str, cfg: BacktestConfig) -> float:
    """What the cost model charges a fill of this kind, in basis points."""
    if tag == "take_profit":
        return 0.0  # a limit order: no spread or slippage in the model
    return costs.per_fill_bps(cfg, stop=tag in ("stop", "stop_entry"))


def match_fills(paper: list[dict[str, Any]], replay: list, cfg: BacktestConfig) -> dict[str, Any] | None:
    """Pair each paper fill with the replay's nearest fill of the same symbol, side and session."""
    used: set[int] = set()
    diffs, models = [], []
    for pf in paper:
        t, buy = pd.Timestamp(pf["time_utc"]), pf["side"] == "buy"
        options = [
            (i, rf) for i, rf in enumerate(replay)
            if i not in used and rf.symbol == pf["symbol"] and (rf.qty > 0) == buy and _session(rf.time) == _session(t)
        ]
        if not options:
            continue
        i, rf = min(options, key=lambda o: abs(pd.Timestamp(o[1].time) - t))
        used.add(i)
        diffs.append((1 if buy else -1) * (float(pf["price"]) / rf.price - 1) * 1e4)  # positive: paper paid more
        models.append(model_bps(rf.tag, cfg))
    if not paper and not replay:
        return None
    out: dict[str, Any] = {"matched": len(diffs), "paper_only": len(paper) - len(diffs), "replay_only": len(replay) - len(used)}
    if diffs:
        model = float(np.mean(models))
        actual = model + float(np.mean(diffs))
        out.update(mean_diff_bps=round(float(np.mean(diffs)), 2), model_bps=round(model, 2), actual_bps=round(actual, 2),
                   ratio=round(actual / model, 3) if model > 0 else None)
    return out


def paper_trade_returns(fills: list[dict[str, Any]], equity: pd.DataFrame, start_equity: float) -> list[float]:
    """Net result of each closed paper trade, as a fraction of the equity before it opened."""
    ledger = Ledger(start_equity)
    for i, f in enumerate(sorted(fills, key=lambda r: r["time_utc"])):
        qty = float(f["qty"]) * (1 if f["side"] == "buy" else -1)
        ledger.apply_fill(time=pd.Timestamp(f["time_utc"]), index=i, symbol=f["symbol"], qty=qty, price=float(f["price"]))
    out = []
    for trade in ledger.all_trades():
        if trade.is_open:
            continue
        before = equity[equity.index < pd.Timestamp(_session(trade.entry_time))]
        base = float(before["equity"].iloc[-1]) if len(before) else start_equity
        out.append(trade.pnl / base)
    return out


def _start_equity(records: PaperRecords, decisions: list[dict[str, Any]]) -> float:
    eq = records.equity()
    if len(eq):
        return float(eq["last_equity"].iloc[0])
    for d in decisions:
        if d.get("equity"):
            return float(d["equity"])
    return 100_000.0


def track(
    spec,
    records: PaperRecords | None = None,
    fetch: Fetch | None = None,
    *,
    provider: str | None = None,
    limits: RiskLimits | None = None,
    now: datetime | None = None,
    incident_weeks: int = 4,
) -> Tracking:
    """Compare ``spec``'s paper record with a replay of the same period through the backtest engine."""
    records = records or PaperRecords(spec.key)
    out = Tracking(spec.key)
    decisions = _latest_per_bar(records.decisions())
    now = now or datetime.now(timezone.utc)
    events = records.events()
    since = now - timedelta(weeks=incident_weeks)
    out.errors_recent = sum(1 for e in events if e["kind"] == "error" and pd.Timestamp(e["time"]) >= pd.Timestamp(since))
    out.resize_skipped = sum(1 for e in events if e["kind"] == "resize_skipped")
    if not decisions:
        out.notes.append("No paper decisions yet: `trades paper --submit` records them.")
        return out
    if fetch is None:
        from trades.validation.research import default_fetch as fetch
    tf = Timeframe.parse(spec.timeframe)
    times = [pd.Timestamp(d["time"]) for d in decisions]
    sessions = sorted({_session(t) for t in times})
    out.first, out.last = sessions[0].isoformat(), sessions[-1].isoformat()
    out.sessions, out.decisions = len(sessions), len(decisions)
    out.weeks = round((sessions[-1] - sessions[0]).days / 7, 1)
    out.missed_sessions = len(set(trading_days(sessions[0], sessions[-1])) - set(sessions))

    # The replay: the same period through the backtest engine, from the paper account's starting equity.
    strat, sizing = spec.strategy.build()
    warm_days = int(math.ceil(strat.warmup() / tf.bars_per_day) * 1.6) + 10
    source = provider or spec.provider
    frames, errors = fetch(source, spec.symbols, tf.value, sessions[0] - timedelta(days=warm_days), sessions[-1])
    if errors or not frames:
        raise TrackingError(f"no {tf.value} bars from {source} for the replay: {errors}")
    start_equity = _start_equity(records, decisions)
    cfg = costs.with_model(BacktestConfig(initial_cash=start_equity, periods_per_year=tf.periods_per_year), spec.costs)
    index = pd.DatetimeIndex(sorted(set.intersection(*(set(df.index) for df in frames.values()))))
    day_start = pd.Timestamp(sessions[0]).tz_localize("UTC" if tf is Timeframe.D1 else NY)  # daily bars: 00:00 UTC
    first_bar = int(index.searchsorted(day_start.tz_convert("UTC")))
    replay = backtest_strategy(strat, frames, sizing, cfg, eval_start=first_bar, risk=limits or load_limits(),
                               with_benchmark=False).result

    # Signals, after and before the risk engine (the replay drops short signals when shorting is off).
    both = same = raw_both = raw_same = 0
    decided = set(times)
    shorts = replay.config.allow_short
    for d, t in zip(decisions, times, strict=True):
        if t not in replay.approved.index:
            out.notes.append(f"The replay has no bar at {t}: the live and historical bars differ.")
            continue
        for s in spec.symbols:
            p, r = _sign(d["targets"].get(s)), _sign(replay.approved.at[t, s])
            if p or r:
                both += 1
                same += p == r
            raw = float(d["raw"].get(s) or 0.0)
            p, r = _sign(raw if shorts else max(raw, 0.0)), _sign(replay.targets.at[t, s])
            if p or r:
                raw_both += 1
                raw_same += p == r
    out.compared = both
    out.signal_match = round(same / both, 4) if both else None
    out.strategy_match = round(raw_same / raw_both, 4) if raw_both else None
    by_session: dict[date, list[pd.Timestamp]] = {}
    for t in times:
        by_session.setdefault(_session(t), []).append(t)
    for t in replay.approved.index[first_bar:]:
        mine = by_session.get(_session(t))
        if mine and mine[0] <= t <= mine[-1] and t not in decided:
            out.missed_bars += 1

    # Trades and expectancy.
    in_period = set(sessions)
    paper_fills = [f for f in records.read_fills() if _session(f["time_utc"]) in in_period]
    rets = paper_trade_returns(paper_fills, records.equity(), start_equity)
    out.trades = len(rets)
    if rets:
        out.expectancy = expectancy(rets).to_dict()
    eq = replay.equity
    replay_rets = [
        t.pnl / float(eq.iloc[max(t.entry_index - 1, 0)])
        for t in replay.trades if not t.is_open and _session(t.entry_time) in in_period
    ]
    if replay_rets:
        out.replay_expectancy = expectancy(replay_rets).to_dict()
    last_run = runs.latest(spec.key)
    if last_run is not None:
        mean = ((last_run.get("results") or {}).get("validation") or {}).get("expectancy", {}).get("mean")
        out.backtest_expectancy = float(mean) if mean is not None and math.isfinite(float(mean)) else None

    # Fills against the model.
    replay_fills = [f for f in replay.fills if _session(f.time) in in_period]
    out.fills = match_fills(paper_fills, replay_fills, cfg)

    # Daily P&L.
    paper_eq = records.equity()
    if len(paper_eq):
        paper_daily = (paper_eq["equity"] / paper_eq["last_equity"] - 1).rename("paper")
        paper_daily.index = [d.date() for d in paper_daily.index]
        closes = eq.groupby([_session(t) for t in eq.index]).last()
        replay_daily = closes.pct_change().rename("replay")
        joined = pd.concat([paper_daily, replay_daily], axis=1, join="inner").dropna()
        joined = joined[[d in in_period for d in joined.index]]
        out.daily_days = len(joined)
        if len(joined) >= 5 and joined["paper"].std() > 0 and joined["replay"].std() > 0:
            out.daily_correlation = round(float(joined["paper"].corr(joined["replay"])), 3)
    if out.resize_skipped:
        out.notes.append(f"{out.resize_skipped} re-size(s) the backtest made were skipped on paper (by design).")
    return out


def _pct(x: float | None, digits: int = 1) -> str:
    return "–" if x is None else f"{x:.{digits}%}"


def render(t: Tracking) -> str:
    """The tracking report, in Markdown, with the gate's thresholds (config/live.yaml) beside each measure."""
    gate = config.section("live", "gate")
    lines = [f"# Tracking: {t.spec}, paper against its replay", ""]
    if t.first is None:
        return "\n".join([*lines, *t.notes, ""])
    lines += [
        f"Paper period {t.first} to {t.last}: {t.sessions} sessions ({t.weeks:g} weeks), {t.decisions} decisions, "
        f"{t.trades} closed trades. Missed: {t.missed_sessions} sessions, {t.missed_bars} bars.",
        "",
        "| Measure | Paper | For comparison | Gate |",
        "| --- | --- | --- | --- |",
        f"| Signal match | {_pct(t.signal_match)} of {t.compared} bar-symbols with a position wanted "
        f"| before the risk engine: {_pct(t.strategy_match)} | at least {gate.get('min_signal_match', 0.95):.0%} |",
    ]
    e, r = t.expectancy or {}, t.replay_expectancy or {}
    paper_e = f"{_pct(e.get('mean'), 3)} (t {e.get('t_stat', float('nan')):.2f}, n {e.get('n', 0)})" if e else "no closed trades"
    lines.append(
        f"| Expectancy per trade | {paper_e} | replay {_pct(r.get('mean'), 3)}; backtest (validation) "
        f"{_pct(t.backtest_expectancy, 3)} | positive, at least {gate.get('min_expectancy_fraction', 0.5):.0%} of the backtest |"
    )
    f = t.fills or {}
    if f.get("matched"):
        fill = f"{f['actual_bps']:.1f} bps a fill ({f['matched']} matched)"
        model = f"model {f['model_bps']:.1f} bps: {f['ratio']:.2f}x" if f.get("ratio") is not None else "model 0 bps"
    else:
        fill, model = "no matched fills", "–"
    lines.append(f"| Fill cost | {fill} | {model} | at most {gate.get('max_fill_cost_vs_model', 1.5):g}x the model |")
    corr = "–" if t.daily_correlation is None else f"{t.daily_correlation:.2f} over {t.daily_days} days"
    lines.append(f"| Daily P&L correlation | {corr} | | at least {gate.get('min_daily_pnl_correlation', 0.8):g} |")
    lines.append(f"| Errors in the last {gate.get('incident_free_weeks', 4)} weeks | {t.errors_recent} | | none |")
    if f:
        lines += ["", f"Fills only on paper: {f.get('paper_only', 0)}; only in the replay: {f.get('replay_only', 0)}."]
    if t.notes:
        lines += ["", *[f"- {n}" for n in dict.fromkeys(t.notes)]]
    lines += ["", "Hypothetical paper trading and backtest results. Not investment advice.", ""]
    return "\n".join(lines)
