"""``trades gate``: the paper-to-live gate, the ten checks before real money (the playbook's Part C).

Passing the gate turns nothing on. There is no live order path in this repository: a person adds
one after the gate passes, with live keys kept off any machine that runs Claude Code
(config/live.yaml). The gate is the evidence that it is worth building.

1. **Backtest checklist:** the latest research run passed all 14 checks, and its record is committed.
2. **Paper duration:** at least 8 weeks and 50 trades (12 weeks for daily or one-trade-per-day specs).
3. **Signal match:** at least 95% against a replay of the same bars, and daily P&L correlation of
   at least 0.8 (``trades track``).
4. **Paper expectancy:** positive, and at least half the backtest's.
5. **Fill cost:** within 1.5x the cost model.
6. **Incidents:** no errors in the last 4 weeks, and a test alert that reached you.
7. **Kill switch drills:** all four done on paper (``trades paper drill``): the HALT file, flatten
   (Alpaca's "close all positions"), the daily loss stop, and a revoked API key.
8. **Live config:** config/live.yaml and risk/limits.yaml reviewed line by line by you (an
   attestation, void if either file changes afterwards).
9. **Broker account:** a margin account meeting your broker's intraday margin rules (an attestation).
10. **Loss budget:** the total you can afford to lose, written in config/live.yaml.

Thresholds come from config/live.yaml (``gate:``). Each check passes, fails, or is pending (not
enough evidence yet: keep paper trading).
"""

from __future__ import annotations

import getpass
import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any

from trades import config, runs
from trades.engine.paper import PaperRecords, _stamp, flatten
from trades.execution.alpaca import PaperAPIError
from trades.execution.broker import Broker
from trades.reporting.tracking import Tracking
from trades.risk import RiskEngine, RiskLimits, killswitch

DRILLS = ("halt", "flatten", "daily-loss", "revoke-key")  # the four kill switches of item 7
ATTESTATIONS = {
    "live_config": "I reviewed config/live.yaml and risk/limits.yaml line by line, and they say what I intend.",
    "broker_account": "My live account is a margin account that meets my broker's intraday margin rules.",
}
REVIEWED_FILES = ("config/live.yaml", "risk/limits.yaml")


@dataclass(frozen=True)
class GateCheck:
    n: int
    name: str
    status: str  # pass | fail | pending
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------------------
# Attestations: what only a person can confirm, stored in paper/<key>/gate.json
# --------------------------------------------------------------------------------------


def file_hash(rel: str) -> str:
    path = config.path(rel)
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else "missing"


def attestations(records: PaperRecords) -> dict[str, dict[str, Any]]:
    path = records.folder / "gate.json"
    try:
        return json.loads(path.read_text()).get("attestations", {})
    except (OSError, json.JSONDecodeError):
        return {}


def attest(records: PaperRecords, item: str, note: str = "", now: datetime | None = None) -> dict[str, Any]:
    """Record that a person confirmed ``item`` (one of ``ATTESTATIONS``)."""
    if item not in ATTESTATIONS:
        raise ValueError(f"unknown attestation {item!r}; expected one of {', '.join(ATTESTATIONS)}")
    entry = {
        "statement": ATTESTATIONS[item],
        "by": getpass.getuser(),
        "on": _stamp(now or datetime.now(timezone.utc)),
        "note": note,
    }
    if item == "live_config":
        entry["files"] = {rel: file_hash(rel) for rel in REVIEWED_FILES}
    current = attestations(records)
    current[item] = entry
    records.folder.mkdir(parents=True, exist_ok=True)
    (records.folder / "gate.json").write_text(json.dumps({"attestations": current}, indent=1) + "\n")
    return entry


# --------------------------------------------------------------------------------------
# Drills: each kill switch, exercised on the paper account and checked
# --------------------------------------------------------------------------------------


def _adds_exposure(order: dict[str, Any], positions: dict[str, dict[str, float]]) -> bool:
    held = float((positions.get(order.get("symbol", "")) or {}).get("qty", 0.0))
    reduces = held and (order.get("side") == "sell") == (held > 0)
    return not reduces


def run_drill(
    kind: str,
    client_factory: Callable[[], Broker],
    records: PaperRecords,
    limits: RiskLimits,
    *,
    alerts: Callable[[str, str], bool] | None = None,
    now: datetime | None = None,
    sleep: Callable[[float], None] | None = None,
    log: Callable[[str], None] = print,
) -> tuple[bool, str]:
    """Exercise one kill switch on the paper account and record the result as a ``drill`` event."""
    now = now or datetime.now(timezone.utc)
    if kind == "halt":
        client = client_factory()
        if not killswitch.is_halted(limits):
            ok, detail = False, "The HALT file is not there: run `trades halt` first, then repeat the drill."
        else:
            positions = client.positions()
            adding = [o for o in client.open_orders() if _adds_exposure(o, positions)]
            ok = not adding
            detail = ("HALT is on and nothing that adds exposure is working." if ok else
                      f"HALT is on, but {len(adding)} order(s) that add exposure are still working: the runner "
                      "should have cancelled them (is it running?).")
    elif kind == "flatten":
        ok, detail = flatten(client_factory(), limits, reason="flatten drill", sleep=sleep, log=log)
    elif kind == "daily-loss":
        client = client_factory()
        account, positions = client.account(), client.positions()
        equity = float(account["equity"])
        symbols = sorted(positions) or ["SPY"]
        engine = RiskEngine(limits, symbols)
        engine.restore({}, opening_equity=equity / (1 - 2 * limits.daily_loss_limit))  # as if down twice the limit
        held = [float(positions.get(s, {}).get("market_value", 0.0)) / equity if equity else 0.0 for s in symbols]
        targets = engine.review(now, equity, [max(abs(w), 0.1) for w in held], held, [1.0] * len(symbols))
        if any(abs(w) > 1e-12 for w in targets) or engine.blocked_until != "session":
            ok, detail = False, "The risk engine did not flatten and block on a loss past the daily limit."
        else:
            ok, detail = flatten(client, limits, reason="daily loss drill", sleep=sleep, log=log)
            detail = f"The risk engine flattened and blocked the session; then: {detail}"
    elif kind == "revoke-key":
        try:
            client_factory().account()
            ok, detail = False, "The API key still works: revoke it in Alpaca's dashboard, then repeat the drill."
        except PaperAPIError as exc:
            ok = exc.status in (401, 403)
            detail = ("Alpaca refused the revoked key, as it should. Create a new paper key for the trading machine."
                      if ok else f"Unexpected answer: {exc}")
    elif kind == "alert":
        sent = bool(alerts and alerts("error", "Test alert: `trades paper drill alert`. Nothing is wrong."))
        ok, detail = sent, ("The test alert was sent." if sent else
                            "No alert went out: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID and `alerts.telegram: true`.")
    else:
        raise ValueError(f"unknown drill {kind!r}; expected one of {', '.join((*DRILLS, 'alert'))}")
    records.log_event(now, "drill", f"{kind}: {'passed' if ok else 'failed'}. {detail}", drill=kind, passed=ok)
    return ok, detail


def drills_passed(records: PaperRecords) -> dict[str, str]:
    """Drill kind -> when it last passed."""
    out: dict[str, str] = {}
    for e in records.events():
        if e["kind"] == "drill" and e.get("passed"):
            out[e["drill"]] = e["time"]
    return out


# --------------------------------------------------------------------------------------
# The ten checks
# --------------------------------------------------------------------------------------


def evaluate(spec, tracking: Tracking, records: PaperRecords) -> list[GateCheck]:
    gate = config.section("live", "gate")
    live = config.load("live")
    checks: list[GateCheck] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append(GateCheck(len(checks) + 1, name, status, detail))

    # 1. The backtest checklist.
    run = runs.latest(spec.key)
    if run is None:
        add("Backtest checklist", "pending", "No research run yet.")
    elif run["verdict"] != "pass":
        add("Backtest checklist", "fail", f"The latest research run ({run['id']}) did not pass all 14 checks.")
    elif not runs.committed(runs.runs_dir() / run["id"] / "run.json"):
        add("Backtest checklist", "pending", f"Run {run['id']} passed; commit runs/{run['id']}/ to archive it.")
    else:
        add("Backtest checklist", "pass", f"Run {run['id']} passed all 14 checks and is committed.")

    # 2. Paper duration.
    slow = spec.timeframe == "1d" or bool(getattr(spec, "one_trade_per_day", False))
    weeks_needed = float(gate.get("min_paper_weeks_one_per_day" if slow else "min_paper_weeks", 12 if slow else 8))
    trades_needed = int(gate.get("min_paper_trades", 50))
    enough = tracking.weeks >= weeks_needed and tracking.trades >= trades_needed
    add("Paper duration", "pass" if enough else "pending",
        f"{tracking.weeks:g} of {weeks_needed:g} weeks, {tracking.trades} of {trades_needed} trades.")

    # 3. Tracking: signal match and daily P&L correlation.
    need_match, need_corr = float(gate.get("min_signal_match", 0.95)), float(gate.get("min_daily_pnl_correlation", 0.8))
    m, c = tracking.signal_match, tracking.daily_correlation
    if m is None or c is None:
        add("Signal match", "pending", "Not enough paper decisions and days to compare yet (`trades track`).")
    else:
        ok = m >= need_match and c >= need_corr
        add("Signal match", "pass" if ok else "fail",
            f"{m:.1%} of decisions match the replay (need {need_match:.0%}); daily P&L correlation {c:.2f} (need {need_corr:g}).")

    # 4. Expectancy.
    frac = float(gate.get("min_expectancy_fraction", 0.5))
    paper = (tracking.expectancy or {}).get("mean")
    backtest = tracking.backtest_expectancy
    if paper is None or not math.isfinite(paper) or backtest is None:
        add("Paper expectancy", "pending", "Needs closed paper trades and a research run to compare with.")
    else:
        ok = paper > 0 and backtest > 0 and paper >= frac * backtest
        add("Paper expectancy", "pass" if ok else "fail",
            f"{paper:+.3%} a trade on paper against {backtest:+.3%} in the backtest (need positive and {frac:.0%} of it).")

    # 5. Fill cost.
    limit = float(gate.get("max_fill_cost_vs_model", 1.5))
    ratio = (tracking.fills or {}).get("ratio")
    if ratio is None:
        add("Fill cost", "pending", "No paper fills matched to the replay yet.")
    else:
        f = tracking.fills or {}
        add("Fill cost", "pass" if ratio <= limit else "fail",
            f"{f['actual_bps']:.1f} bps a fill against the model's {f['model_bps']:.1f} bps: {ratio:.2f}x (limit {limit:g}x).")

    # 6. Incidents.
    weeks = int(gate.get("incident_free_weeks", 4))
    alert_ok = "alert" in drills_passed(records)
    if tracking.errors_recent:
        add("Incidents", "fail", f"{tracking.errors_recent} error(s) in the last {weeks} weeks (paper/{spec.key}/events.jsonl).")
    elif not alert_ok:
        add("Incidents", "pending", f"No errors in {weeks} weeks; test the alerts: `trades paper drill alert`.")
    else:
        add("Incidents", "pass", f"No errors in the last {weeks} weeks, and a test alert reached you.")

    # 7. Kill switch drills.
    done = drills_passed(records)
    missing = [k for k in DRILLS if k not in done]
    add("Kill switch drills", "pass" if not missing else "pending",
        "All four done on paper." if not missing else f"Still to do: {', '.join(missing)} (`trades paper drill <name>`).")

    # 8. Live config reviewed by you.
    att = attestations(records)
    review = att.get("live_config")
    if review is None:
        add("Live config", "pending", "Review config/live.yaml and risk/limits.yaml line by line, then "
            f"`trades gate {spec.id} --attest live_config`.")
    else:
        changed = [rel for rel, h in review.get("files", {}).items() if file_hash(rel) != h]
        add("Live config", "fail" if changed else "pass",
            f"Changed since your review on {review['on'][:10]}: {', '.join(changed)}." if changed
            else f"Reviewed by {review['by']} on {review['on'][:10]}.")

    # 9. Broker account.
    acct = att.get("broker_account")
    add("Broker account", "pass" if acct else "pending",
        f"Confirmed by {acct['by']} on {acct['on'][:10]}." if acct else f"`trades gate {spec.id} --attest broker_account`.")

    # 10. Loss budget.
    budget = live.get("loss_budget")
    try:
        ok = budget is not None and float(budget) > 0
    except (TypeError, ValueError):
        ok = False
    add("Loss budget", "pass" if ok else "pending",
        f"${float(budget):,.0f}, written in config/live.yaml." if ok else "Write `loss_budget:` in config/live.yaml yourself.")
    return checks


def verdict(checks: list[GateCheck]) -> str:
    if any(c.status == "fail" for c in checks):
        return "fail"
    return "pass" if all(c.status == "pass" for c in checks) else "not_yet"


VERDICT_TEXT = {
    "pass": "PASSED: the evidence supports building a live path (Phase 4: you build it, not Claude).",
    "not_yet": "NOT YET: keep paper trading and finish the pending items.",
    "fail": "FAILED: a check failed. Most strategies fail here; that is the system working.",
}


def render(spec, checks: list[GateCheck], today: date | None = None) -> str:
    mark = {"pass": "PASS", "fail": "FAIL", "pending": "...."}
    lines = [f"# Paper-to-live gate: {spec.key} ({(today or date.today()).isoformat()})", ""]
    lines += [f"{c.n:>2}. [{mark[c.status]}] {c.name}: {c.detail}" for c in checks]
    lines += ["", VERDICT_TEXT[verdict(checks)], ""]
    return "\n".join(lines)
