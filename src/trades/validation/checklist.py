"""The Backtest Acceptance Checklist: fourteen checks a spec must pass before paper trading.

The thresholds live in ``config/backtest.yaml`` (human-only). They are the playbook's deliberately
conservative judgment calls for a small account, not industry standards. ``evaluate`` turns the
results of a research run (``trades.validation.research``) into one ``Check`` per item, each with
the measured value, the bar it had to clear and a sentence explaining it.

Verdicts:

* ``pass``: all fourteen pass (the PBO may be ``n/a`` when no parameter grid was searched). The
  spec may go to paper trading.
* ``ready_for_locked_test``: everything passes except the locked test, which is still sealed.
  Opening it is Jack's decision (``trades research STRAT-xxx --open-locked-test``), once.
* ``fail``: anything else. A failed locked test means rejection, not retuning.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

PASS, FAIL, PENDING, NA = "pass", "fail", "pending", "n/a"


@dataclass(frozen=True)
class Check:
    n: int
    name: str
    status: str
    value: str
    threshold: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ok(x: float | None) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def _num(x: float | None, fmt: str = "{:.2f}") -> str:
    return fmt.format(x) if _ok(x) else "n/a"


def _pct(x: float | None, digits: int = 1) -> str:
    return f"{x:.{digits}%}" if _ok(x) else "n/a"


def evaluate(r: dict[str, Any], a: dict[str, Any]) -> list[Check]:
    """``r``: a research run's results; ``a``: the ``acceptance`` thresholds."""
    checks: list[Check] = []

    def add(n, name, passed, value, threshold, detail="", status=None):
        checks.append(Check(n, name, status or (PASS if passed else FAIL), value, threshold, detail))

    spec = r["spec"]
    frozen_ok = spec["frozen"] and spec["drift"] is None and spec["committed"]
    if not spec["frozen"]:
        why = "The spec is a draft: freeze it (`trades spec freeze`) before results can count."
    elif spec["drift"]:
        why = spec["drift"]
    elif not spec["committed"]:
        why = "The spec file has uncommitted changes: commit it so the rules on record are the rules tested."
    else:
        why = f"Frozen on {spec['frozen_on']} and committed."
    add(1, "Spec frozen and committed before testing", frozen_ok, "yes" if frozen_ok else "no", "yes, dated", why)

    uni = r["universe"]
    look = r["lookahead"]
    reasons = []
    if not uni["survivorship_free"]:
        reasons.append("the universe is not survivorship-free")
    if not uni["real_data"]:
        reasons.append(f"the data is not real market data ({uni['provider']})")
    if not look["passed"]:
        reasons.append("look-ahead: " + look["detail"])
    add(
        2,
        "Survivorship-free universe on real data; no look-ahead",
        not reasons,
        "yes" if not reasons else "no",
        "yes",
        ("; ".join(reasons) + ".").capitalize() if reasons else f"Real {uni['provider']} data; {look['detail']}.",
    )

    val = r["validation"]
    need = a["min_oos_trades_one_per_day"] if r["one_trade_per_day"] else a["min_oos_trades"]
    n_oos = val["expectancy"]["n"]
    add(3, "Out-of-sample trades", n_oos >= need, str(n_oos), f">= {need}",
        "Trades in the validation period" + (" (a one-trade-per-day system)." if r["one_trade_per_day"] else "."))

    exp = val["expectancy"]
    t = exp.get("t_stat")
    add(4, "Net expectancy at base costs", _ok(exp.get("mean")) and exp["mean"] > 0 and _ok(t) and t >= a["min_expectancy_tstat"],
        f"{_pct(exp.get('mean'), 3)} per trade, t = {_num(t)}", f"> 0 with t >= {a['min_expectancy_tstat']:g}",
        "Average net result per validation trade as a share of equity, and how many standard errors from zero.")

    stressed = val["stressed_expectancy"]
    add(5, "Net expectancy at 2x costs", _ok(stressed.get("mean")) and stressed["mean"] >= a["min_stressed_expectancy"],
        f"{_pct(stressed.get('mean'), 3)} per trade", f">= {a['min_stressed_expectancy']:g}",
        "The validation period again with every trading cost doubled.")

    wf = r["walk_forward"]
    if wf is None:
        add(6, "Walk-forward Sharpe (net, stitched)", False, "n/a", f">= {a['min_walk_forward_sharpe']:g}",
            "Not enough history for one training window plus one test window.")
    else:
        add(6, "Walk-forward Sharpe (net, stitched)", _ok(wf["sharpe"]) and wf["sharpe"] >= a["min_walk_forward_sharpe"],
            _num(wf["sharpe"]), f">= {a['min_walk_forward_sharpe']:g}",
            f"{len(wf['windows'])} windows; only the unseen test days are stitched together ({wf['days']} days).")

    d = r["deflated"]
    add(7, "Deflated Sharpe probability", _ok(d.get("dsr")) and d["dsr"] >= a["min_deflated_sharpe"], _pct(d.get("dsr"), 0),
        f">= {a['min_deflated_sharpe']:.0%}",
        f"Sharpe {_num(d.get('sharpe'))} over train and validation, against {_num(d.get('expected_max_sharpe'))}: the "
        f"Sharpe the best of {d['n_trials']} skill-less variants would show by luck, {d['n_trials']} being how many "
        "variants of this spec have been tested so far.")

    pbo = r["pbo"]
    if pbo is None:
        add(8, "Probability of backtest overfitting", True, "n/a", f"< {a['max_pbo']:g}",
            "Not computed: no parameter grid was searched.", status=NA)
    else:
        add(8, "Probability of backtest overfitting", pbo["pbo"] < a["max_pbo"], _pct(pbo["pbo"], 0), f"< {a['max_pbo']:.0%}",
            f"How often the in-sample winner of the grid fell to the bottom half out of sample ({pbo['combinations']} splits).")

    lt = r["locked_test"]
    if not lt["opened"]:
        add(9, "Locked test", False, "sealed", f"same sign, >= {a['locked_test_min_fraction']:.0%} of validation Sharpe",
            "Opened once, by Jack, after everything else passes.", status=PENDING)
    else:
        ts, vs = lt.get("sharpe"), lt.get("validation_sharpe")
        ok = _ok(ts) and _ok(vs) and vs > 0 and ts > 0 and ts >= a["locked_test_min_fraction"] * vs
        add(9, "Locked test", ok, f"Sharpe {_num(ts)} vs {_num(vs)}", f"same sign, >= {a['locked_test_min_fraction']:.0%} of validation Sharpe",
            f"Opened on {lt.get('opened_on')}. " + ("A failed locked test means rejection, not retuning." if not ok else ""))

    nb = r["neighborhood"]
    retained = nb.get("retained")
    add(10, "Parameter neighbourhood (+/-20%)", _ok(retained) and retained >= a["neighborhood_min_retained"], _pct(retained, 0),
        f">= {a['neighborhood_min_retained']:.0%}", nb.get("detail", ""))

    mc = r["monte_carlo"]
    add(11, "Monte Carlo 95th-percentile drawdown", _ok(mc.get("p95")) and mc["p95"] <= a["max_drawdown_budget"], _pct(mc.get("p95")),
        f"<= {a['max_drawdown_budget']:.0%}", "Trades resampled 10,000 times: the drawdown you should be ready to sit through.")

    rg = r["regimes"]
    ok = _ok(rg.get("profitable_share")) and rg["profitable_share"] >= a["min_profitable_year_share"] and _ok(rg.get("max_year_share")) and rg["max_year_share"] <= a["max_single_year_profit_share"]
    top = _pct(rg.get("max_year_share"), 0) + " of profit" if _ok(rg.get("max_year_share")) else "no net profit"
    add(12, "Regimes: profitable years, no single-year dependence", ok,
        f"{_pct(rg.get('profitable_share'), 0)} of years profitable; top year {top}",
        f">= {a['min_profitable_year_share']:.0%}; <= {a['max_single_year_profit_share']:.0%}",
        "Calendar years over the train and validation periods.")

    cap = r["capacity"]
    add(13, "Capacity (share of 1-minute volume at entry)", _ok(cap.get("p95")) and cap["p95"] < a["max_participation"], _pct(cap.get("p95"), 2),
        f"< {a['max_participation']:.0%}", "The 95th percentile of order size over volume per minute in the entry bar.")

    rep = r["reproducible"]
    if rep["clean"]:
        why = "Git commit, data fingerprint, configuration and seed recorded."
    elif rep["dirty"]:
        why = "Uncommitted changes in " + ", ".join(rep["dirty"][:5]) + ": commit them, then rerun."
    else:
        why = "Not run from a git checkout, so the code that produced it cannot be identified."
    add(14, "Reproducible from runs/", rep["clean"], "yes" if rep["clean"] else "no", "yes", why)
    return checks


def verdict(checks: list[Check]) -> str:
    blocking = [c for c in checks if c.status not in (PASS, NA)]
    if not blocking:
        return "pass"
    if all(c.n == 9 and c.status == PENDING for c in blocking):
        return "ready_for_locked_test"
    return "fail"


VERDICT_TEXT = {
    "pass": "PASSED: all fourteen checks. The spec may move to paper trading.",
    "ready_for_locked_test": "Everything passes except the locked test, which is still sealed. Opening it is your decision, and it can only happen once.",
    "fail": "FAILED: the spec does not go to paper trading. That is the system working: most strategies fail here.",
}
