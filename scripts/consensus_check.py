"""Does the Live Desk consensus beat buy-and-hold after costs and taxes? Prints a Markdown table.

For each symbol set it runs, from ``--start`` on the chosen data provider:

* a plain backtest of the consensus with each weighting (equal, by category);
* a walk-forward test that re-chooses the weighting on each 3-year training window and trades it
  only on the following, unseen year (the only thing fitted here is the weighting: every other
  setting is the app's default);
* SPY buy-and-hold and equal-weight buy-and-hold of the same symbols over the same bars.

Idle cash earns T-bill returns (the BIL ETF) unless ``--no-cash-yield``; Sharpe ratios are in
excess of T-bills. "P(beats SPY)" is the share of 1,000 block-bootstrap resamples in which the
row compounds faster than SPY buy-and-hold over the same days. At the end the run is judged
against the pre-registered rule in ``journal/decision_rule.json``.

Everything uses the app's default settings (not your saved ones), so anyone can rerun it:

    python scripts/consensus_check.py --provider yahoo --start 2010-01-01
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd

from trades.backtest.engine import BacktestConfig
from trades.backtest.metrics import performance_metrics
from trades.backtest.optimize import walk_forward
from trades.backtest.runner import StrategySpec, backtest_strategy, buy_and_hold, tax_metrics
from trades.backtest.tax import TaxProfile
from trades.config import DEFAULT_WATCHLISTS, Settings, SettingsStore
from trades.core import stats
from trades.data.base import align_bars, history_start
from trades.data.service import DataService
from trades.journal.rule import BACKTEST_CHECK, backtest_verdict, load_rule
from trades.strategies.consensus import params_from_settings
from trades.universes import UNIVERSES

SETS = {"watchlist": ("Watchlist", DEFAULT_WATCHLISTS["yahoo"])} | {
    key: (str(u["label"]).split(" (")[0], list(u["symbols"])) for key, u in UNIVERSES.items()
}


def row(label: str, period: str, m: dict, p_spy: float | None) -> str:
    def pct(k):
        return "–" if m.get(k) is None else f"{m[k]:+.1%}"

    def num(k, fmt):
        return "–" if m.get(k) is None else format(m[k], fmt)

    psr = "–" if m.get("psr") is None else f"{m['psr']:.0%}"
    beats = "–" if p_spy is None else f"{p_spy:.0%}"
    drag = "–" if m.get("cost_drag") is None else format(m["cost_drag"], ".2%")
    return (
        f"| {label} | {period} | {pct('cagr')} | {pct('after_tax_cagr_if_sold')} | {num('sharpe', '.2f')} | {psr} "
        f"| {beats} | {pct('max_drawdown')} | {num('turnover', '.1f')}x | {drag} |"
    )


def check(
    name: str,
    symbols: list[str],
    data: DataService,
    provider: str,
    start,
    cfg: BacktestConfig,
    cash_yield: bool,
    variants: list[dict],
) -> tuple[list[str], dict]:
    base = params_from_settings(Settings())
    warm = StrategySpec("consensus", base).build()[0].warmup()
    # The same first bar as the Live Desk's history, so periodic rules check on the same days.
    frames, errors = data.bars_many([*symbols, "SPY"], "1d", history_start(start, warm), None, provider)
    if errors:
        raise SystemExit(f"data errors: {errors}")
    aligned = align_bars({s: frames[s] for s in symbols})
    index = next(iter(aligned.values())).index
    if cash_yield:
        cash, note = data.cash_returns(index, provider)
        cfg = replace(cfg, cash_returns=cash)
        if note:
            print(f"> {name}: {note}\n")
    eval_start = int(index.searchsorted(pd.Timestamp(start).tz_localize("UTC")))
    ppy = cfg.periods_per_year
    spy = {"SPY": frames["SPY"].reindex(index)}
    tax = TaxProfile()  # taxable, 22% short-term / 15% long-term: the app's default estimates

    def held(sub, start_at=0):  # buy-and-hold: metrics after tax if sold at the end, and its equity
        res, m = buy_and_hold(sub, cfg, start_at)
        return m | tax_metrics(res, sub, start_at, tax, ppy)[0], res.equity.iloc[start_at:]

    def p_beats(equity: pd.Series, spy_equity: pd.Series) -> float:
        both = pd.concat([equity, spy_equity], axis=1, join="inner").pct_change().iloc[1:]
        rf = cfg.cash_returns.reindex(both.index) if cfg.cash_returns is not None else None
        return stats.outperformance_probability(both.iloc[:, 0], both.iloc[:, 1], risk_free=rf)["p_growth"]

    lines, tests = [], {}
    for weighting in ("equal", "by_category"):
        strat, sizing = StrategySpec("consensus", {**base, "weighting": weighting}).build()
        tests[weighting] = backtest_strategy(strat, aligned, sizing, cfg, eval_start, tax=tax)
    first = tests["equal"]
    period = f"{first.equity.index[0].date()} – {first.equity.index[-1].date()}"
    spy_m, spy_eq = held(spy, first.start)
    ew_m, ew_eq = held(aligned, first.start)
    p_equal = None
    for weighting, bt in tests.items():
        p = p_beats(bt.equity, spy_eq)
        p_equal = p if weighting == "equal" else p_equal
        lines.append(row(f"{name}: consensus, {weighting.replace('_', ' ')}", period, bt.metrics, p))
    results = {"equal": first.metrics, "spy": spy_m, "p_equal": p_equal, "variants": {}}
    for v in variants:  # pre-registered variants, e.g. a wider no-trade band to cut turnover
        strat, sizing = StrategySpec("consensus", {**base, "weighting": "equal"}).build()
        vcfg = replace(cfg, min_trade_weight=float(v["min_trade_weight"]))
        bt = backtest_strategy(strat, aligned, sizing, vcfg, eval_start, tax=tax)
        p = p_beats(bt.equity, spy_eq)
        results["variants"][v["name"]] = (bt.metrics, p)
        lines.append(row(f"{name}: consensus, equal, {v['name']}", period, bt.metrics, p))
    lines.append(row(f"{name}: SPY buy & hold", period, spy_m, None))
    lines.append(row(f"{name}: equal-weight buy & hold", period, ew_m, p_beats(ew_eq, spy_eq)))

    # Walk-forward: the first training window starts at --start (after the members' warm-up).
    wf_data = {s: df.iloc[max(eval_start - warm + 1, 0) :] for s, df in aligned.items()}
    grid = {"weighting": ["equal", "by_category"]}
    wf = walk_forward("consensus", base, grid, wf_data, None, cfg, "sharpe", 756, 252)
    oos = pd.to_datetime(wf["oos_equity"]["t"], unit="s", utc=True)
    a, b = oos[0], oos[-1]
    period = f"{a.date()} – {b.date()}"
    oos_index = index[index.searchsorted(a) : index.searchsorted(b) + 1]
    spy_oos_m, spy_oos_eq = held({"SPY": spy["SPY"].loc[oos_index]})
    wf_eq = pd.Series(wf["oos_equity"]["v"], index=oos)
    chosen = ", ".join(w["best_params"]["weighting"].replace("_", " ") for w in wf["windows"])
    label = f"{name}: walk-forward (weighting chosen yearly: {chosen})"
    lines.append(row(label, period, wf["oos_metrics"], p_beats(wf_eq, spy_oos_eq)))
    for weighting, bt in tests.items():
        eq = bt.result.equity.loc[a:b]
        fills = [f for f in bt.result.fills if a <= pd.Timestamp(f.time) <= b]
        m = performance_metrics(eq, ppy, None, fills, bt.result.gross_exposure.loc[a:b], risk_free=cfg.cash_returns)
        lines.append(row(f"{name}: consensus, {weighting.replace('_', ' ')}", period, m, p_beats(eq, spy_oos_eq)))
    lines.append(row(f"{name}: SPY buy & hold", period, spy_oos_m, None))
    ew_oos_m, ew_oos_eq = held({s: df.loc[oos_index] for s, df in aligned.items()})
    lines.append(row(f"{name}: equal-weight buy & hold", period, ew_oos_m, p_beats(ew_oos_eq, spy_oos_eq)))
    return lines, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", default="yahoo")
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--slippage", type=float, default=5.0, help="basis points per fill (the app default)")
    parser.add_argument("--no-cash-yield", action="store_true", help="idle cash earns nothing")
    parser.add_argument("--rule", default="journal/decision_rule.json", help="the pre-registered decision rule")
    parser.add_argument(
        "--sets", default="watchlist,sectors", help=f"comma-separated symbol sets: {', '.join(SETS)}"
    )
    args = parser.parse_args()
    unknown = [k for k in args.sets.split(",") if k not in SETS]
    if unknown:
        parser.error(f"unknown set(s) {unknown}; choose from {', '.join(SETS)}")
    rule = load_rule(args.rule)
    variants = (rule or {}).get("backtest", {}).get("variants", [])
    start = pd.Timestamp(args.start).date()
    data = DataService(SettingsStore())
    cfg = BacktestConfig(slippage_bps=args.slippage, allow_short=False)
    cash = "idle cash earns nothing" if args.no_cash_yield else "idle cash earns T-bill returns (BIL)"
    print(f"Provider: {args.provider}; start {start}; slippage {args.slippage:g} bps per fill; {cash}.\n")
    print("After-tax: 22% short-term / 15% long-term federal estimates, everything sold on the last day. Not")
    print("computed ('–') for the walk-forward or for out-of-sample slices of the longer backtests.\n")
    print(
        "| Run | Period | CAGR | After-tax CAGR | Sharpe | P(Sharpe > 0) | P(beats SPY) | Max drawdown "
        "| Turnover / yr | Costs / yr |"
    )
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    results = {}
    for key in args.sets.split(","):
        name, symbols = SETS[key]
        lines, results[key] = check(name, symbols, data, args.provider, start, cfg, not args.no_cash_yield, variants)
        for line in lines:
            print(line)
    if rule and "backtest" in rule and rule["backtest"].get("symbol_set") in results:
        r = rule["backtest"]
        if r.get("start") and r["start"] != start.isoformat() or args.no_cash_yield:
            print(f"\nThe pre-registered rule is for a run from {r.get('start')} with T-bill cash; this run is not it.")
        else:
            res = results[r["symbol_set"]]
            n = 1 + len(res["variants"])
            print(f"\n**Pre-registered decision rule.** {r['description']}")
            v = backtest_verdict(res["equal"], res["spy"], res["p_equal"], r, n)
            print(f"- consensus, equal: **{v['status']}**. {v['detail']}")
            verdicts = [{"run": "consensus, equal", **v}]
            for name, (metrics, p) in res["variants"].items():
                v = backtest_verdict(metrics, res["spy"], p, r, n)
                print(f"- consensus, equal, {name}: **{v['status']}**. {v['detail']}")
                verdicts.append({"run": f"consensus, equal, {name}", **v})
            # The decision gate reads this; commit it so the Track Record page and the digest see it.
            best = next((x for x in verdicts if x["status"] == "PASS"), verdicts[0])
            out = Path(args.rule).parent / BACKTEST_CHECK
            out.write_text(json.dumps({
                "status": best["status"],
                "detail": f"{best['run']}: {best['detail']} ({args.provider}, {start} to {date.today()})",
                "runs": verdicts,
                "provider": args.provider,
                "run_on": date.today().isoformat(),
            }, indent=1) + "\n")
            print(f"\nWrote {out}: commit it so the decision gate sees this result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
