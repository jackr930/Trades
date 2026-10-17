"""A research run as a Markdown report (``runs/<id>/report.md``): the checklist first, then the
numbers behind it, then everything needed to reproduce the run."""

from __future__ import annotations

import math
from typing import Any

from trades.validation.checklist import VERDICT_TEXT

MARK = {"pass": "PASS", "fail": "FAIL", "pending": "sealed", "n/a": "n/a"}


def _pct(x: Any, digits: int = 2) -> str:
    return f"{x:.{digits}%}" if isinstance(x, (int, float)) and math.isfinite(x) else "n/a"


def _num(x: Any, digits: int = 2) -> str:
    return f"{x:.{digits}f}" if isinstance(x, (int, float)) and math.isfinite(x) else "n/a"


def render(run: dict[str, Any]) -> str:
    spec, res = run["spec"], run["results"]
    lines = [
        f"# {spec['key']}: {spec['name']}",
        "",
        f"Run `{run['id']}` on {run['created'][:16].replace('T', ' ')} UTC. "
        f"Spec status: {spec['status']}; frozen: {spec['frozen'] or 'no (a draft: results are exploratory)'}.",
        "",
        f"**Verdict:** {VERDICT_TEXT.get(run['verdict'], run['verdict'])}",
        "",
        "## Backtest acceptance checklist",
        "",
        "| # | Check | Result | Value | Needs | Why |",
        "|---|---|---|---|---|---|",
    ]
    for c in run["checklist"]:
        lines.append(f"| {c['n']} | {c['name']} | {MARK.get(c['status'], c['status'])} | {c['value']} | {c['threshold']} | {c['detail']} |")
    val, train = res["validation"], res.get("train", {})
    ve, se = val["expectancy"], val["stressed_expectancy"]
    lines += [
        "",
        "## The numbers",
        "",
        "| | Train | Validation |",
        "|---|---|---|",
        f"| Trades | {train.get('expectancy', {}).get('n', 'n/a')} | {ve['n']} |",
        f"| Net result per trade (share of equity) | {_pct(train.get('expectancy', {}).get('mean'), 3)} | {_pct(ve['mean'], 3)} |",
        f"| t-statistic | {_num(train.get('expectancy', {}).get('t_stat'))} | {_num(ve['t_stat'])} |",
        f"| Hit rate | {_pct(train.get('expectancy', {}).get('hit_rate'), 0)} | {_pct(ve['hit_rate'], 0)} |",
        f"| Sharpe ratio (daily, annualised) | {_num(train.get('sharpe'))} | {_num(val['sharpe'])} |",
        "",
        f"At {run['config']['research'].get('cost_stress', 2):g}x costs the validation trades average "
        f"{_pct(se['mean'], 3)} each (t = {_num(se['t_stat'])}).",
    ]
    if ve.get("mean_r") is not None:
        lines.append(f"Average result in R (multiples of the risk at the stop): {_num(ve['mean_r'])}.")
    mc = res["monte_carlo"]
    lines += [
        "",
        f"Monte Carlo drawdowns (trades resampled): median {_pct(mc.get('p50'), 1)}, 95th percentile {_pct(mc.get('p95'), 1)}, "
        f"99th percentile {_pct(mc.get('p99'), 1)}.",
        f"Variants of this spec version tested so far: {run['variants_tested']} (all count toward the deflated Sharpe ratio).",
    ]
    years = res["regimes"].get("years") or {}
    if years:
        lines += ["", "Net result by year (sum of per-trade results): " + ", ".join(f"{y}: {_pct(v, 1)}" for y, v in years.items()) + "."]
    nt = res["lookahead"].get("null_test") or {}
    if nt:
        lines.append(
            f"Null test (a random walk, no costs): {nt.get('trades', 0)} trades, t = {_num(nt.get('t_stat'))} "
            f"({'no edge, as it should be' if nt.get('passed') else 'AN EDGE ON NOISE: look for look-ahead'})."
        )
    if res.get("ambiguous_fills"):
        n = res["ambiguous_fills"]
        lines.append(f"{n} {'exit' if n == 1 else 'exits'} assumed the worse outcome inside one bar; finer bars would tell.")
    if res.get("warnings"):
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in res["warnings"]]
    lt = res.get("locked_test") or {}
    if lt.get("opened"):
        lines += [
            "",
            "## Locked test",
            "",
            f"Opened on {lt.get('opened_on')}: Sharpe {_num(lt.get('sharpe'))} against {_num(lt.get('validation_sharpe'))} in "
            f"validation; {lt.get('expectancy', {}).get('n', 0)} trades averaging {_pct(lt.get('expectancy', {}).get('mean'), 3)}.",
        ]
    elif run.get("locked_test", {}).get("refused"):
        lines += ["", f"The locked test was not opened: {run['locked_test']['refused']}."]
    git, data = run["git"], run["data"]
    lines += [
        "",
        "## Reproduce",
        "",
        f"- Code: commit `{git.get('commit')}` (src/trades tree `{git.get('code_tree')}`)"
        + (f"; **uncommitted changes:** {', '.join(git['dirty'])}" if git.get("dirty") else ""),
        f"- Data: {data['provider']} {data['timeframe']} bars, fingerprint `{data['hash']}`",
        f"- Costs: {run['config']['costs']}",
        f"- Seed: {run['seed']}; periods: " + "; ".join(f"{k} {v[0]} to {v[1]}" for k, v in run["periods"].items()),
        f"- Rerun: `trades runs reproduce {run['id']}`",
        "",
        "*Hypothetical backtest. Not actual trading results. Past results do not predict future results.*",
        "",
    ]
    return "\n".join(lines)
