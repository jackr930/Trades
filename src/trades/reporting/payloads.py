"""Backtest results as JSON for the web UI and the API."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from trades.engine.backtest import StrategyBacktest, benchmark_symbols
from trades.reporting.metrics import drawdown, monthly_returns
from trades.strategies import Kind, Strategy


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
        "after_tax_equity": (
            {"t": _ts(bt.after_tax.index), "v": _vals(bt.after_tax, 2)} if bt.after_tax is not None else None
        ),
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

