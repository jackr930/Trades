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


def cmd_backtest(args) -> int:
    from trades.backtest.engine import BacktestConfig
    from trades.backtest.metrics import METRIC_INFO
    from trades.backtest.runner import StrategySpec, backtest_strategy
    from trades.config import SettingsStore
    from trades.core.timeframes import Timeframe
    from trades.data.base import align_bars
    from trades.data.service import DataService

    store = SettingsStore()
    data = DataService(store)
    spec = StrategySpec(args.strategy, _parse_params(args.param))
    strat, sizing = spec.build()
    tf = Timeframe.parse(args.timeframe)
    start = pd.Timestamp(args.start).date() if args.start else None
    fetch_start = start - timedelta(days=int(strat.warmup() * 1.5) + 10) if start else None
    symbols = [s.upper() for s in args.symbols]
    frames, errors = data.bars_many(symbols, tf, fetch_start, args.end, args.provider)
    if errors:
        print("Data errors:", errors, file=sys.stderr)
        return 1
    aligned = align_bars(frames)
    eval_start = None
    if start:
        idx = next(iter(aligned.values())).index
        eval_start = int(idx.searchsorted(pd.Timestamp(start).tz_localize("UTC")))
    cfg = BacktestConfig(
        slippage_bps=args.slippage,
        commission_bps=args.commission,
        allow_short=args.short,
        periods_per_year=tf.periods_per_year,
    )
    bt = backtest_strategy(strat, aligned, sizing, cfg, eval_start)
    eq = bt.equity
    print(
        f"\n{strat.name} on {', '.join(symbols)}  ({eq.index[0].date()} -> {eq.index[-1].date()}, "
        f"provider: {args.provider or store.get().provider})\n"
    )
    print(f"{'Metric':24s} {'Strategy':>12s} {'Buy & hold':>12s}")
    for key in (
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
        "total_costs",
    ):
        info = METRIC_INFO[key]
        b = (bt.benchmark_metrics or {}).get(key)
        print(
            f"{info['label']:24s} {_fmt(bt.metrics.get(key), info['fmt']):>12s} {_fmt(b, info['fmt']):>12s}"
        )
    for w in bt.warnings:
        print(f"\n! {w}")
    return 0


def cmd_recommend(args) -> int:
    from trades.advisor import AdvisorSettings, Recommender
    from trades.config import SettingsStore
    from trades.core.timeframes import Timeframe
    from trades.data.base import last_bar_forming
    from trades.data.service import DataService

    store = SettingsStore()
    s = store.get()
    data = DataService(store)
    provider = args.provider or s.provider
    symbols = [x.upper() for x in (args.symbols or s.watchlist(provider))]
    frames, errors = data.bars_many(symbols, "1d", None, None, provider, count=1600)
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
