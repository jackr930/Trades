"""Command-line interface: ``trades serve | strategies | backtest | recommend``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta

import pandas as pd


def _fmt(value, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value:+.2%}"
    if kind in ("ratio", "x"):
        return f"{value:.2f}"
    if kind == "money":
        return f"${value:,.0f}"
    if kind in ("int", "bars"):
        return f"{value:,.0f}"
    return str(value)


def cmd_serve(args) -> int:
    import uvicorn

    from trades.api import create_app

    if args.host not in ("127.0.0.1", "localhost") and not os.environ.get("TRADES_PASSWORD"):
        print(
            "WARNING: listening on a non-local interface without TRADES_PASSWORD: anyone who can reach "
            "this machine can use the app and change its settings.",
            file=sys.stderr,
        )
    app = create_app(start_live=not args.no_live)
    print(f"Trades running at http://{args.host}:{args.port}  (Ctrl+C to stop)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_strategies(args) -> int:
    from trades.strategies import REGISTRY

    for cls in REGISTRY.values():
        print(f"{cls.id:20s} {cls.name}  [{cls.category}; evidence: {cls.evidence.value}]")
        print(f"{'':20s} {cls.summary}")
        if args.verbose:
            for ref in cls.references:
                print(f"{'':20s}   - {ref}")
        print()
    return 0


def _parse_params(items: list[str]) -> dict:
    out = {}
    for item in items or []:
        k, _, v = item.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


METRIC_ROWS = (
    "total_return",
    "cagr",
    "volatility",
    "sharpe",
    "max_drawdown",
    "psr",
    "n_trades",
    "win_rate",
    "profit_factor",
    "exposure",
    "turnover",
    "total_costs",
    "cost_drag",
)


def _parse_grid(items: list[str]) -> dict[str, list]:
    """``--grid weighting=equal,by_category`` -> {"weighting": ["equal", "by_category"]}."""
    grid = {}
    for item in items or []:
        name, _, values = item.partition("=")
        if not name or not values:
            raise SystemExit(f"--grid expects name=value1,value2 (got {item!r})")
        grid[name] = [_parse_params([f"v={v}"])["v"] for v in values.split(",")]
    return grid


def _print_table(columns: dict[str, dict]) -> None:
    from trades.backtest.metrics import METRIC_INFO

    print(f"{'Metric':24s}" + "".join(f" {name:>16s}" for name in columns))
    for key in METRIC_ROWS:
        info = METRIC_INFO[key]
        print(f"{info['label']:24s}" + "".join(f" {_fmt(m.get(key), info['fmt']):>16s}" for m in columns.values()))


def cmd_backtest(args) -> int:
    from trades.backtest.engine import BacktestConfig
    from trades.backtest.optimize import walk_forward
    from trades.backtest.runner import StrategySpec, backtest_strategy, buy_and_hold
    from trades.config import SettingsStore
    from trades.core.timeframes import Timeframe
    from trades.data.base import align_bars, history_start
    from trades.data.service import DataService
    from trades.strategies.consensus import params_from_settings

    store = SettingsStore()
    data = DataService(store)
    params = _parse_params(args.param)
    if args.strategy == "consensus":
        # Reproduce the Live Desk: its strategies, pairs, weighting and risk settings, unless overridden.
        params = {**params_from_settings(store.get()), **params}
        if args.short:
            params["allow_short"] = True
    spec = StrategySpec(args.strategy, params)
    strat, sizing = spec.build()
    tf = Timeframe.parse(args.timeframe)
    start = pd.Timestamp(args.start).date() if args.start else None
    fetch_start = None
    if start and tf is Timeframe.D1:
        fetch_start = history_start(start, strat.warmup())  # the Live Desk's first bar, unless warm-up needs earlier
    elif start:
        fetch_start = start - timedelta(days=int(strat.warmup() / tf.bars_per_day * 1.6) + 4)
    symbols = [s.upper() for s in args.symbols]
    bench_symbol = args.benchmark.upper() if args.benchmark else None
    wanted = symbols + ([bench_symbol] if bench_symbol and bench_symbol not in symbols else [])
    frames, errors = data.bars_many(wanted, tf, fetch_start, args.end, args.provider)
    if errors:
        print("Data errors:", errors, file=sys.stderr)
        return 1
    aligned = align_bars({s: frames[s] for s in symbols})
    idx = next(iter(aligned.values())).index
    eval_start = int(idx.searchsorted(pd.Timestamp(start).tz_localize("UTC"))) if start else None
    cfg = BacktestConfig(
        slippage_bps=args.slippage,
        commission_bps=args.commission,
        allow_short=args.short or bool(strat.params.get("allow_short") and strat.sizes_itself),
        periods_per_year=tf.periods_per_year,
    )
    provider = args.provider or store.get().provider
    if strat.sizes_itself:
        p = strat.params
        print(
            f"\nConsensus of {', '.join(m['id'] for m in p['members'])}; weighting {p['weighting']}; "
            f"risk {p['risk_per_trade']:.2%} per trade, stop {p['stop_atr']:g} ATR, max position "
            f"{p['max_position_pct']:.0%}, portfolio cap {p['max_gross']:.0%}, shorts "
            f"{'allowed' if p['allow_short'] else 'off'}."
        )

    def benchmark_columns(index, start_at: int) -> dict[str, dict]:
        """Buy-and-hold of the same symbols, and of ``--benchmark``, over the same bars."""
        cols = {"EW buy & hold": buy_and_hold({s: aligned[s].loc[index] for s in symbols}, cfg, start_at)[1]}
        if bench_symbol:
            bdf = frames[bench_symbol].reindex(index)
            cols[f"{bench_symbol} buy & hold"] = buy_and_hold({bench_symbol: bdf}, cfg, start_at)[1]
        return cols

    if args.walk_forward:
        grid = _parse_grid(args.grid)
        if eval_start:  # the first training window starts at the requested date
            aligned = {s: df.iloc[max(eval_start - strat.warmup() + 1, 0) :] for s, df in aligned.items()}
        res = walk_forward(
            args.strategy, strat.params, grid, aligned, None, cfg,
            args.objective, args.train_bars, args.test_bars, args.anchored,
        )
        oos_t = pd.to_datetime(res["oos_equity"]["t"], unit="s", utc=True)
        print(
            f"\nWalk-forward {strat.name} on {', '.join(symbols)}: out-of-sample {oos_t[0].date()} -> "
            f"{oos_t[-1].date()} ({len(res['windows'])} windows; provider: {provider})\n"
        )
        for w in res["windows"]:
            test_start = pd.Timestamp(w["test_start"], unit="s").date()
            print(f"  test from {test_start}: chose {w['best_params'] or 'the defaults'}")
        index = next(iter(aligned.values())).index
        oos_index = index[index.searchsorted(oos_t[0]) :][: len(oos_t)]
        cols = {"Walk-forward": res["oos_metrics"], "Defaults": res["default_metrics"]}
        _print_table(cols | benchmark_columns(oos_index, 0))
        print(f"\n{res['interpretation']}")
        return 0

    bt = backtest_strategy(strat, aligned, sizing, cfg, eval_start)
    eq = bt.equity
    print(
        f"\n{strat.name} on {', '.join(symbols)}  ({eq.index[0].date()} -> {eq.index[-1].date()}, "
        f"provider: {provider})\n"
    )
    cols = {"Strategy": bt.metrics}
    if len(symbols) == 1 or not bench_symbol:
        cols["Buy & hold"] = bt.benchmark_metrics or {}
    if bench_symbol:
        cols |= benchmark_columns(bt.result.equity.index, bt.start)
    _print_table(cols)
    for w in bt.warnings:
        print(f"\n! {w}")
    return 0


def cmd_recommend(args) -> int:
    from trades.advisor import AdvisorSettings, Recommender
    from trades.config import SettingsStore
    from trades.core.timeframes import Timeframe
    from trades.data.base import HISTORY_START, last_bar_forming
    from trades.data.service import DataService

    store = SettingsStore()
    s = store.get()
    data = DataService(store)
    provider = args.provider or s.provider
    symbols = [x.upper() for x in (args.symbols or s.watchlist(provider))]
    frames, errors = data.bars_many(symbols, "1d", HISTORY_START, None, provider)
    for sym, err in errors.items():
        print(f"{sym}: {err}", file=sys.stderr)
    adv = AdvisorSettings.from_settings(s)
    adv.pairs = s.active_pairs(list(frames))
    forming = {k: provider != "synthetic" and last_bar_forming(df, Timeframe.D1) for k, df in frames.items()}
    res = Recommender().recommend(frames, adv, provisional=forming, namespace=provider)
    print(f"\nRecommendations (provider: {provider}) -- educational, not investment advice\n")
    for r in res["recommendations"]:
        c = r["consensus"]
        print(
            f"{r['symbol']:8s} {r['price']:>10.2f}  {c['label']:<22s} score {c['score']:+.2f} "
            f"({c['bullish']} bull / {c['bearish']} bear / {c['neutral']} neutral)"
        )
        if args.verbose:
            for v in r["votes"]:
                print(f"{'':10s}- {v['strategy_name']}: {v['headline']}")
            print(f"{'':10s}  sizing: {r['sizing']['explanation']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trades", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="start the web app")
    p.add_argument("--host", default="127.0.0.1")
    # Hosting platforms (Render, Railway, Heroku, ...) say which port to use in $PORT.
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    p.add_argument("--no-live", action="store_true", help="do not start the live feed automatically")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("strategies", help="list available strategies")
    p.add_argument("-v", "--verbose", action="store_true", help="include references")
    p.set_defaults(func=cmd_strategies)

    p = sub.add_parser("backtest", help="backtest a strategy")
    p.add_argument("strategy")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--provider")
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--param", action="append", help="strategy parameter, e.g. --param lookback=126")
    p.add_argument("--slippage", type=float, default=5.0, help="slippage in basis points")
    p.add_argument("--commission", type=float, default=0.0, help="commission in basis points")
    p.add_argument("--short", action="store_true", help="allow short selling")
    p.add_argument("--benchmark", help="also compare with buy-and-hold of this symbol, e.g. SPY")
    p.add_argument(
        "--walk-forward",
        action="store_true",
        help="re-choose --grid parameters on each training window and score them on the next, unseen one",
    )
    p.add_argument("--grid", action="append", help="walk-forward choices, e.g. --grid weighting=equal,by_category")
    p.add_argument("--objective", default="sharpe", choices=("sharpe", "cagr", "calmar", "sortino", "total_return"))
    p.add_argument("--train-bars", type=int, default=756, help="walk-forward training window (bars)")
    p.add_argument("--test-bars", type=int, default=252, help="walk-forward test window (bars)")
    p.add_argument("--anchored", action="store_true", help="walk-forward training windows all start at the beginning")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("recommend", help="print recommendations for symbols (default: watchlist)")
    p.add_argument("symbols", nargs="*")
    p.add_argument("--provider")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_recommend)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
