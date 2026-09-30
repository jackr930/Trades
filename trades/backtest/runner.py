"""Glue: strategy -> sizing -> backtest -> metrics, plus serialisation for the API."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from trades.backtest.engine import BacktestConfig, BacktestResult, buy_and_hold_weights, run_backtest
from trades.backtest.metrics import drawdown, monthly_returns, performance_metrics
from trades.data.base import align_bars
from trades.strategies import Kind, Strategy, StrategyOutput, create_strategy, get_strategy_class
from trades.strategies.sizing import SizingConfig, apply_sizing


@dataclass
class StrategySpec:
    id: str
    params: dict[str, Any] = field(default_factory=dict)
    sizing: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StrategySpec:
        return cls(id=d["id"], params=dict(d.get("params") or {}), sizing=d.get("sizing"))

    def build(self) -> tuple[Strategy, SizingConfig]:
        cls = get_strategy_class(self.id)
        strat = create_strategy(self.id, self.params)
        return strat, SizingConfig.from_dict(self.sizing, cls.default_sizing)

    def key(self) -> str:
        import json

        return json.dumps(
            {"id": self.id, "params": self.params, "sizing": self.sizing}, sort_keys=True, default=str
        )


@dataclass
class StrategyBacktest:
    strategy: Strategy
    sizing: SizingConfig
    data: dict[str, pd.DataFrame]
    output: StrategyOutput
    weights: pd.DataFrame
    result: BacktestResult
    benchmark: BacktestResult | None
    metrics: dict[str, float | None]
    benchmark_metrics: dict[str, float | None] | None
    start: int
    warnings: list[str]

    @property
    def equity(self) -> pd.Series:
        return self.result.equity.iloc[self.start :]


def benchmark_symbols(strategy: Strategy, symbols: list[str]) -> list[str]:
    if strategy.kind is Kind.PAIR:
        return symbols[:1]
    return symbols


def backtest_strategy(
    strategy: Strategy,
    data: dict[str, pd.DataFrame],
    sizing: SizingConfig | None = None,
    config: BacktestConfig | None = None,
    eval_start: int | None = None,
    with_benchmark: bool = True,
) -> StrategyBacktest:
    cfg = config or BacktestConfig()
    sizing = sizing or SizingConfig.from_dict(None, strategy.default_sizing)
    aligned = align_bars({s.upper() if isinstance(s, str) else s: df for s, df in data.items()})
    symbols = list(aligned)
    index = next(iter(aligned.values())).index
    T = len(index)
    if T < 3:
        raise ValueError("not enough bars to backtest")
    output = strategy.run(aligned)
    weights = apply_sizing(output.signals, aligned, sizing, strategy.kind, cfg.periods_per_year)
    warm = max(strategy.warmup() - 1, 0)
    start = warm if eval_start is None else max(eval_start, 0)
    warnings: list[str] = []
    if start >= T - 2:
        raise ValueError(
            f"{strategy.name} needs {strategy.warmup()} bars of history but only {T} are available; "
            "choose an earlier start date or shorter lookbacks."
        )
    if eval_start is not None and eval_start < warm:
        warnings.append(
            f"The first {warm - eval_start} bars of the test period are still warming up the indicators "
            f"({strategy.warmup()} bars needed)."
        )
    if strategy.uses_short and not cfg.allow_short:
        warnings.append(
            f"{strategy.name} is designed to short, but short selling is disabled; shorts are skipped."
        )

    # Fills may not push gross exposure past the sizing's leverage cap (e.g. after an opening gap).
    cfg = cfg.capped(sizing.max_leverage)
    result = run_backtest(aligned, weights, cfg, start)
    bench = None
    bench_metrics = None
    if with_benchmark:
        bsyms = benchmark_symbols(strategy, symbols)
        bench = run_backtest(
            {s: aligned[s] for s in bsyms},
            buy_and_hold_weights(index, bsyms, start),
            replace(cfg, max_gross_leverage=1.0),  # a buy-and-hold investor uses no margin
            start,
        )
        bench_metrics = performance_metrics(
            bench.equity.iloc[start:], cfg.periods_per_year, bench.trades, bench.fills, bench.gross_exposure
        )
    metrics = performance_metrics(
        result.equity.iloc[start:],
        cfg.periods_per_year,
        result.trades,
        result.fills,
        result.gross_exposure.iloc[start:],
        bench.equity.iloc[start:] if bench is not None else None,
    )
    warnings += interpretation_warnings(metrics, T - start, cfg.periods_per_year, cfg.initial_cash)
    return StrategyBacktest(
        strategy, sizing, aligned, output, weights, result, bench, metrics, bench_metrics, start, warnings
    )


def interpretation_warnings(
    m: dict[str, float | None], bars: int, ppy: int, initial_cash: float = 100_000.0
) -> list[str]:
    """Plain-language cautions that help interpret a backtest honestly."""
    out = []
    n_trades = m.get("n_trades") or 0
    if n_trades < 30:
        out.append(
            f"Only {int(n_trades)} closed trades: too few to judge the strategy reliably (aim for 30+)."
        )
    years = bars / ppy
    if years < 3:
        out.append(f"Only {years:.1f} years of data: results may reflect a single market regime.")
    psr = m.get("psr")
    if psr is not None and psr < 0.95:
        out.append(
            f"Probabilistic Sharpe ratio is {psr:.0%}: you cannot rule out that the true Sharpe ratio is zero "
            "(95% is the usual bar)."
        )
    sharpe = m.get("sharpe")
    if sharpe is not None and sharpe > 2.5:
        out.append(
            "A Sharpe ratio above 2.5 is rare outside of backtests: check for overfitting or data errors."
        )
    costs, total = m.get("total_costs"), m.get("total_return")
    if costs and total is not None:
        gross_profit = total * initial_cash + costs
        if gross_profit > 0 and costs / gross_profit > 0.25:
            out.append(f"Trading costs consumed {costs / gross_profit:.0%} of gross profits.")
    turnover = m.get("turnover")
    if turnover is not None and turnover > 20:
        out.append(f"Turnover is {turnover:.0f}x per year: results are very sensitive to trading costs.")
    return out


# --------------------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------------------


def _ts(index: pd.Index) -> list[int]:
    """Unix seconds, independent of the index's resolution (pandas 3 may store s/ms/us/ns)."""
    return pd.DatetimeIndex(index).as_unit("s").asi8.tolist()


def _vals(series: pd.Series, digits: int = 6) -> list[float | None]:
    arr = series.to_numpy(dtype=float)
    return [None if not math.isfinite(v) else round(float(v), digits) for v in arr]


def bars_payload(df: pd.DataFrame) -> dict[str, list]:
    return {
        "t": _ts(df.index),
        "o": _vals(df["open"], 4),
        "h": _vals(df["high"], 4),
        "l": _vals(df["low"], 4),
        "c": _vals(df["close"], 4),
        "v": _vals(df["volume"], 0),
    }


def overlays_payload(strategy: Strategy, diag: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for ov in strategy.overlays:
        if ov.column in diag:
            levels = [float(x) for x in strategy.overlay_levels(ov)]
            out.append({**ov.to_dict(), "levels": levels, "values": _vals(diag[ov.column].astype(float), 6)})
    return out


def markers_payload(bt: StrategyBacktest, symbol: str) -> list[dict[str, Any]]:
    out = []
    for f in bt.result.fills:
        if f.symbol != symbol:
            continue
        out.append(
            {
                "t": int(pd.Timestamp(f.time).timestamp()),
                "side": "buy" if f.qty > 0 else "sell",
                "qty": float(f.qty),
                "price": round(float(f.price), 4),
            }
        )
    return out


def trade_payload(t, unrealized: float | None = None) -> dict[str, Any]:
    """Serialise a trade; an open trade's P&L includes its mark-to-market ``unrealized`` part."""
    d = t.to_dict()
    if t.is_open and unrealized is not None and math.isfinite(unrealized):
        d["pnl"] = t.pnl + unrealized
        d["return_pct"] = d["pnl"] / t.entry_notional if t.entry_notional > 0 else 0.0
    for k in ("entry_time", "exit_time"):
        d[k] = int(pd.Timestamp(d[k]).timestamp()) if d[k] is not None else None
    for k, v in list(d.items()):
        if isinstance(v, float) and not math.isfinite(v):
            d[k] = None
    return d


def backtest_payload(bt: StrategyBacktest, chart_symbols: list[str] | None = None) -> dict[str, Any]:
    start = bt.start
    eq = bt.result.equity.iloc[start:]
    symbols = list(bt.data)
    chart_symbols = chart_symbols or symbols[: (2 if bt.strategy.kind is Kind.PAIR else 1)]
    charts = []
    for sym in chart_symbols:
        diag = bt.output.diagnostics.get(sym, pd.DataFrame(index=bt.result.equity.index))
        charts.append(
            {
                "symbol": sym,
                "bars": bars_payload(bt.data[sym]),
                "overlays": overlays_payload(bt.strategy, diag),
                "markers": markers_payload(bt, sym),
                "signal": _vals(bt.output.signals[sym], 6) if sym in bt.output.signals else None,
            }
        )
    payload: dict[str, Any] = {
        "strategy": bt.strategy.meta()
        | {"params": bt.strategy.params, "param_spec": [p.to_dict() for p in bt.strategy.params_spec]},
        "sizing": bt.sizing.to_dict(),
        "config": bt.result.config.to_dict(),
        "symbols": symbols,
        "start_time": int(eq.index[0].timestamp()),
        "end_time": int(eq.index[-1].timestamp()),
        "equity": {"t": _ts(eq.index), "v": _vals(eq, 2)},
        "drawdown": {"t": _ts(eq.index), "v": _vals(drawdown(eq), 6)},
        "exposure": {"t": _ts(eq.index), "v": _vals(bt.result.gross_exposure.iloc[start:], 4)},
        "metrics": bt.metrics,
        "monthly_returns": monthly_returns(eq),
        "trades": [
            trade_payload(
                t,
                bt.result.ledger.unrealized_pnl(t.symbol, float(bt.data[t.symbol]["close"].iloc[-1]))
                if t.is_open and t.symbol in bt.data
                else None,
            )
            for t in bt.result.trades
        ],
        "charts": charts,
        "warnings": bt.warnings,
        "pending_orders": bt.result.pending,
    }
    if bt.benchmark is not None:
        beq = bt.benchmark.equity.iloc[start:]
        payload["benchmark"] = {
            "label": "Buy & hold "
            + (
                "equal-weight"
                if len(benchmark_symbols(bt.strategy, symbols)) > 1
                else benchmark_symbols(bt.strategy, symbols)[0]
            ),
            "equity": {"t": _ts(beq.index), "v": _vals(beq, 2)},
            "drawdown": {"t": _ts(beq.index), "v": _vals(drawdown(beq), 6)},
            "metrics": bt.benchmark_metrics,
        }
    return payload


def sanitize(obj: Any) -> Any:
    """Recursively convert numpy/pandas scalars and non-finite floats for JSON."""
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return int(obj.timestamp())
    return obj
