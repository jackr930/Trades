"""Does the Live Desk consensus beat buy-and-hold after costs? Prints a Markdown table.

For each symbol set it runs, from ``--start`` on the chosen data provider:

* a plain backtest of the consensus with each weighting (equal, by category);
* a walk-forward test that re-chooses the weighting on each 3-year training window and trades it
  only on the following, unseen year (the only thing fitted here is the weighting: every other
  setting is the app's default);
* SPY buy-and-hold and equal-weight buy-and-hold of the same symbols over the same bars.

Everything uses the app's default settings (not your saved ones), so anyone can rerun it:

    python scripts/consensus_check.py --provider yahoo --start 2010-01-01
"""

from __future__ import annotations

import argparse

import pandas as pd

from trades.backtest.engine import BacktestConfig
from trades.backtest.metrics import performance_metrics
from trades.backtest.optimize import walk_forward
from trades.backtest.runner import StrategySpec, backtest_strategy, buy_and_hold
from trades.config import DEFAULT_WATCHLISTS, Settings, SettingsStore
from trades.data.base import align_bars, history_start
from trades.data.service import DataService
from trades.strategies.consensus import params_from_settings

SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]  # the nine original sector SPDRs


def row(label: str, period: str, m: dict) -> str:
    def pct(k):
        return "–" if m.get(k) is None else f"{m[k]:+.1%}"

    def num(k, fmt):
        return "–" if m.get(k) is None else format(m[k], fmt)

    psr = "–" if m.get("psr") is None else f"{m['psr']:.0%}"
    return (
        f"| {label} | {period} | {pct('cagr')} | {num('sharpe', '.2f')} | {psr} | {pct('max_drawdown')} "
        f"| {num('turnover', '.1f')}x | {'–' if m.get('cost_drag') is None else format(m['cost_drag'], '.2%')} |"
    )


def window_metrics(result, a: pd.Timestamp, b: pd.Timestamp, ppy: int) -> dict:
    """A fixed-rule backtest's record between dates ``a`` and ``b`` (fills in that window only)."""
    fills = [f for f in result.fills if a <= pd.Timestamp(f.time) <= b]
    return performance_metrics(result.equity.loc[a:b], ppy, None, fills, result.gross_exposure.loc[a:b])


def check(name: str, symbols: list[str], data: DataService, provider: str, start, cfg: BacktestConfig) -> list[str]:
    base = params_from_settings(Settings())
    warm = StrategySpec("consensus", base).build()[0].warmup()
    # The same first bar as the Live Desk's history, so periodic rules check on the same days.
    frames, errors = data.bars_many([*symbols, "SPY"], "1d", history_start(start, warm), None, provider)
    if errors:
        raise SystemExit(f"data errors: {errors}")
    aligned = align_bars({s: frames[s] for s in symbols})
    index = next(iter(aligned.values())).index
    eval_start = int(index.searchsorted(pd.Timestamp(start).tz_localize("UTC")))
    ppy = cfg.periods_per_year
    spy = {"SPY": frames["SPY"].reindex(index)}
    lines = []
    tests = {}
    for weighting in ("equal", "by_category"):
        strat, sizing = StrategySpec("consensus", {**base, "weighting": weighting}).build()
        tests[weighting] = bt = backtest_strategy(strat, aligned, sizing, cfg, eval_start)
        period = f"{bt.equity.index[0].date()} – {bt.equity.index[-1].date()}"
        lines.append(row(f"{name}: consensus, {weighting.replace('_', ' ')}", period, bt.metrics))
    bt = tests["equal"]
    lines.append(row(f"{name}: SPY buy & hold", period, buy_and_hold(spy, cfg, bt.start)[1]))
    lines.append(row(f"{name}: equal-weight buy & hold", period, bt.benchmark_metrics or {}))

    # Walk-forward: the first training window starts at --start (after the members' warm-up).
    wf_data = {s: df.iloc[eval_start - warm + 1 :] for s, df in aligned.items()}
    grid = {"weighting": ["equal", "by_category"]}
    wf = walk_forward("consensus", base, grid, wf_data, None, cfg, "sharpe", 756, 252)
    oos = pd.to_datetime(wf["oos_equity"]["t"], unit="s", utc=True)
    a, b = oos[0], oos[-1]
    period = f"{a.date()} – {b.date()}"
    chosen = ", ".join(w["best_params"]["weighting"].replace("_", " ") for w in wf["windows"])
    lines.append(row(f"{name}: walk-forward (weighting chosen yearly: {chosen})", period, wf["oos_metrics"]))
    for weighting, bt in tests.items():
        lines.append(row(f"{name}: consensus, {weighting.replace('_', ' ')}", period, window_metrics(bt.result, a, b, ppy)))
    oos_index = index[index.searchsorted(a) : index.searchsorted(b) + 1]
    lines.append(row(f"{name}: SPY buy & hold", period, buy_and_hold({"SPY": spy["SPY"].loc[oos_index]}, cfg)[1]))
    ew = {s: df.loc[oos_index] for s, df in aligned.items()}
    lines.append(row(f"{name}: equal-weight buy & hold", period, buy_and_hold(ew, cfg)[1]))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", default="yahoo")
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--slippage", type=float, default=5.0, help="basis points per fill (the app default)")
    args = parser.parse_args()
    start = pd.Timestamp(args.start).date()
    data = DataService(SettingsStore())
    cfg = BacktestConfig(slippage_bps=args.slippage, allow_short=False)
    sets = {"Watchlist": DEFAULT_WATCHLISTS["yahoo"], "Sectors": SECTORS}
    print(f"Provider: {args.provider}; start {start}; slippage {args.slippage:g} bps per fill.\n")
    print("| Run | Period | CAGR | Sharpe | P(Sharpe > 0) | Max drawdown | Turnover / yr | Costs / yr |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, symbols in sets.items():
        for line in check(name, symbols, data, args.provider, start, cfg):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
