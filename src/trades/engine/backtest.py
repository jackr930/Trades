"""Backtests: strategy -> sizing -> simulated broker -> metrics.

``run_backtest`` feeds historical bars, one at a time, to the same ``ExecutionEngine`` the
strategy simulator and the forward tests use (see ``trades.execution.sim_broker``), so a
strategy is filled the same way whether it is backtested or traded bar by bar.
``backtest_strategy`` adds the strategy, position sizing, a buy-and-hold benchmark, metrics and
plain-language warnings. JSON for the web UI is built in ``trades.reporting.payloads``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from trades.data.base import align_bars
from trades.execution.ledger import Fill, Ledger, Trade
from trades.execution.sim_broker import BacktestConfig, ExecutionEngine
from trades.features.intraday import infer_bar_minutes, session_info
from trades.reporting.metrics import performance_metrics
from trades.reporting.tax import TaxProfile, after_tax, cagr, liquidation
from trades.risk import RiskEngine, RiskEvent, RiskLimits
from trades.strategies import Kind, Strategy, StrategyOutput
from trades.strategies.sizing import SizingConfig, apply_sizing


@dataclass
class BacktestResult:
    equity: pd.Series
    cash: pd.Series
    positions: pd.DataFrame  # shares held at each close
    weights: pd.DataFrame  # actual weights at each close
    targets: pd.DataFrame  # target weights supplied
    fills: list[Fill]
    trades: list[Trade]
    ledger: Ledger
    config: BacktestConfig
    start: int
    pending: dict[str, float]  # share orders that would execute at the next bar
    ambiguous_fills: int = 0  # fills that relied on the "assume the worse outcome" rule
    risk_events: list[RiskEvent] = field(default_factory=list)  # what the risk engine changed

    @property
    def returns(self) -> pd.Series:
        return self.equity.pct_change(fill_method=None).fillna(0.0)

    @property
    def gross_exposure(self) -> pd.Series:
        return self.weights.abs().sum(axis=1)

    @property
    def net_exposure(self) -> pd.Series:
        return self.weights.sum(axis=1)


def run_backtest(
    data: dict[str, pd.DataFrame],
    target_weights: pd.DataFrame,
    config: BacktestConfig | None = None,
    start: int = 0,
    *,
    stop_losses: pd.DataFrame | None = None,
    take_profits: pd.DataFrame | None = None,
    entry_stops: pd.DataFrame | None = None,
    risk: RiskEngine | None = None,
) -> BacktestResult:
    """Simulate trading ``target_weights`` (index aligned with every frame in ``data``).

    ``start`` is the first bar of the evaluation period (earlier bars only warm up
    indicators); equity is flat at ``initial_cash`` until then. ``stop_losses``, ``take_profits`` and
    ``entry_stops`` are optional order levels (same shape as the weights, NaN for none). With a
    ``risk`` engine, every bar's targets pass through it before any order is placed, exactly as
    in the paper trader.
    """
    cfg = config or BacktestConfig()
    symbols = list(target_weights.columns)
    index = target_weights.index
    T, N = len(index), len(symbols)
    if T == 0:
        raise ValueError("no bars to backtest")
    for s in symbols:
        if s not in data:
            raise KeyError(f"no price data for {s}")
    frames = {s: data[s].reindex(index) for s in symbols}
    O = np.column_stack([frames[s]["open"].to_numpy(float) for s in symbols])
    H = np.column_stack([frames[s]["high"].to_numpy(float) for s in symbols])
    L = np.column_stack([frames[s]["low"].to_numpy(float) for s in symbols])
    C = np.column_stack([frames[s]["close"].to_numpy(float) for s in symbols])
    W = np.nan_to_num(target_weights.to_numpy(float))
    if not cfg.allow_short:
        W = np.clip(W, 0.0, None)

    def levels(frame: pd.DataFrame | None) -> np.ndarray | None:
        return None if frame is None else frame.reindex(index=index, columns=symbols).to_numpy(float)

    S, TP, E = levels(stop_losses), levels(take_profits), levels(entry_stops)
    # The whole history is known here, so a session missing its final bars still closes on its last one.
    closing = session_info(index)["last_available"].to_numpy() if cfg.flat_by_close else np.zeros(T, dtype=bool)

    engine = ExecutionEngine(symbols, cfg)
    ledger = engine.ledger
    equity = np.full(T, float(cfg.initial_cash))  # float: an int fill value would truncate P&L
    cash = np.full(T, float(cfg.initial_cash))
    pos = np.zeros((T, N))
    act_w = np.zeros((T, N))
    start = max(0, min(start, T - 1))

    for t in range(start, T):
        fills = engine.execute(t, index[t], O[t], H[t], L[t], C[t], bool(closing[t]))
        if risk is not None:
            risk.note_fills(len(fills))
        eq = engine.equity
        equity[t], cash[t] = eq, ledger.cash
        for j, s in enumerate(symbols):
            q = ledger.qty(s)
            pos[t, j] = q
            act_w[t, j] = q * C[t, j] / eq if eq > 0 and np.isfinite(C[t, j]) else 0.0
        if engine.stopped:
            equity[t + 1 :] = eq
            cash[t + 1 :] = ledger.cash
            break
        target = W[t]
        if risk is not None:
            target = risk.review(
                index[t],
                eq,
                W[t],
                act_w[t],
                C[t],
                S[t] if S is not None else None,
                E[t] if E is not None else None,
            )
        # On the last bar these become the "pending" orders a live user would place for tomorrow.
        engine.decide(
            t,
            target,
            S[t] if S is not None else None,
            TP[t] if TP is not None else None,
            E[t] if E is not None else None,
            closing=bool(closing[t]),
        )

    return BacktestResult(
        equity=pd.Series(equity, index=index, name="equity"),
        cash=pd.Series(cash, index=index, name="cash"),
        positions=pd.DataFrame(pos, index=index, columns=symbols),
        weights=pd.DataFrame(act_w, index=index, columns=symbols),
        targets=pd.DataFrame(W, index=index, columns=symbols),
        fills=list(ledger.fills),
        trades=ledger.all_trades(),
        ledger=ledger,
        config=cfg,
        start=start,
        pending={symbols[j]: q for j, q in engine.pending.items()},
        ambiguous_fills=engine.ambiguous_fills,
        risk_events=list(risk.events) if risk is not None else [],
    )


def buy_and_hold_weights(index: pd.DatetimeIndex, symbols: list[str], start: int = 0) -> pd.DataFrame:
    """Equal-weight buy-and-hold (bought once at ``start``, never rebalanced)."""
    w = np.zeros((len(index), len(symbols)))
    w[start:, :] = 1.0 / len(symbols)
    return pd.DataFrame(w, index=index, columns=symbols)


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
    after_tax: pd.Series | None = None  # after-tax equity (taxable accounts), from bar ``start``

    @property
    def equity(self) -> pd.Series:
        return self.result.equity.iloc[self.start :]


def benchmark_symbols(strategy: Strategy, symbols: list[str]) -> list[str]:
    if strategy.kind is Kind.PAIR:
        return symbols[:1]
    return symbols


def tax_metrics(
    result: BacktestResult, data: dict[str, pd.DataFrame], start: int, profile: TaxProfile, ppy: int
) -> tuple[dict[str, float | None], pd.Series]:
    """After-tax CAGR (on realized gains, and if everything were sold on the last bar) and taxes paid."""
    eq = result.equity.iloc[start:]
    at = after_tax(eq, result.fills, profile)
    prices = {s: float(df["close"].iloc[-1]) for s, df in data.items() if s in result.positions}
    sold = liquidation(at, result.ledger, prices, eq.index[-1], profile, float(eq.iloc[-1]))
    years = (len(eq) - 1) / ppy
    start_value = float(eq.iloc[0])
    metrics = {
        "after_tax_cagr": cagr(start_value, float(at.equity.iloc[-1]), years),
        "after_tax_cagr_if_sold": cagr(start_value, sold, years),
        "taxes_paid": sum(at.taxes.values()) if profile.taxable else 0.0,
    }
    return metrics, at.equity


def backtest_strategy(
    strategy: Strategy,
    data: dict[str, pd.DataFrame],
    sizing: SizingConfig | None = None,
    config: BacktestConfig | None = None,
    eval_start: int | None = None,
    with_benchmark: bool = True,
    tax: TaxProfile | None = None,
    risk: RiskLimits | None = None,
) -> StrategyBacktest:
    """Backtest ``strategy``; with a ``tax`` profile, also report after-tax results (post-processing).
    With ``risk`` limits, the risk engine reviews every bar's targets (see ``trades.risk.engine``)."""
    cfg = config or BacktestConfig()
    sizing = sizing or SizingConfig.from_dict(None, strategy.default_sizing)
    aligned = align_bars({s.upper() if isinstance(s, str) else s: df for s, df in data.items()})
    symbols = list(aligned)
    index = next(iter(aligned.values())).index
    T = len(index)
    if T < 3:
        raise ValueError("not enough bars to backtest")
    if strategy.intraday:
        if infer_bar_minutes(index) is None:
            raise ValueError(f"{strategy.name} trades within the day: test it on intraday bars (e.g. 5 minutes).")
        cfg = replace(cfg, flat_by_close=True)
    output = strategy.run(aligned)
    weights = apply_sizing(
        output.signals,
        aligned,
        sizing,
        strategy.kind,
        cfg.periods_per_year,
        stop_losses=output.stop_losses,
        entry_stops=output.entry_stops,
    )
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
    risk_engine = None
    if risk is not None:
        cap = min(cfg.max_gross_leverage or risk.max_gross_exposure, risk.max_gross_exposure)
        cfg = replace(cfg, max_gross_leverage=cap, allow_short=cfg.allow_short and risk.allow_short)
        risk_engine = RiskEngine(risk, symbols)
    result = run_backtest(
        aligned,
        weights,
        cfg,
        start,
        stop_losses=output.stop_losses,
        take_profits=output.take_profits,
        entry_stops=output.entry_stops,
        risk=risk_engine,
    )
    bench = None
    bench_metrics = None
    if with_benchmark:
        bsyms = benchmark_symbols(strategy, symbols)
        bench, bench_metrics = buy_and_hold({s: aligned[s] for s in bsyms}, cfg, start)
    metrics = performance_metrics(
        result.equity.iloc[start:],
        cfg.periods_per_year,
        result.trades,
        result.fills,
        result.gross_exposure.iloc[start:],
        bench.equity.iloc[start:] if bench is not None else None,
    )
    warnings += interpretation_warnings(metrics, T - start, cfg.periods_per_year, cfg.initial_cash)
    if risk_engine is not None:
        warnings += risk_warnings(risk_engine.summary())
        if not risk.allow_short:  # type: ignore[union-attr]
            w = weights.to_numpy(float)
            prev = np.vstack([np.zeros((1, w.shape[1])), w[:-1]])
            wanted = int(((w < -1e-12) & (prev >= -1e-12)).sum())
            if wanted:
                warnings.append(
                    f"The strategy wanted to go short {wanted} times; shorting is off in risk/limits.yaml, "
                    "so those trades were skipped."
                )
    if result.ambiguous_fills:
        warnings.append(
            f"{result.ambiguous_fills} exits assumed the worse of two outcomes because one bar could have hit "
            "both orders; test on finer bars (e.g. 1 minute) to see how much this matters."
        )
    after_tax_equity = None
    if tax is not None:
        extra, after_tax_equity = tax_metrics(result, aligned, start, tax, cfg.periods_per_year)
        metrics.update(extra)
        if bench is not None and bench_metrics is not None:
            bench_metrics.update(tax_metrics(bench, aligned, start, tax, cfg.periods_per_year)[0])
    return StrategyBacktest(
        strategy, sizing, aligned, output, weights, result, bench, metrics, bench_metrics, start, warnings, after_tax_equity
    )


def buy_and_hold(
    data: dict[str, pd.DataFrame], config: BacktestConfig, start: int = 0
) -> tuple[BacktestResult, dict[str, float | None]]:
    """Equal-weight buy-and-hold of ``data`` (aligned bars) from bar ``start``, with its metrics."""
    symbols = list(data)
    index = next(iter(data.values())).index
    # A buy-and-hold investor uses no margin, and holds overnight.
    plain = replace(config, max_gross_leverage=1.0, flat_by_close=False)
    res = run_backtest(data, buy_and_hold_weights(index, symbols, start), plain, start)
    metrics = performance_metrics(
        res.equity.iloc[start:], config.periods_per_year, res.trades, res.fills, res.gross_exposure
    )
    return res, metrics


SENSITIVITY_KEYS = ("cagr", "after_tax_cagr_if_sold", "sharpe", "max_drawdown", "cost_drag", "turnover")


def cost_sensitivity(bt: StrategyBacktest, tax: TaxProfile | None = None, multipliers=(2, 4)) -> dict[str, Any]:
    """Rerun ``bt`` with its slippage multiplied, and say whether it still beats buy-and-hold at
    double costs. Compared after tax with both sold at the end (CAGR in a tax-advantaged account)."""
    cfg = bt.result.config
    rows = [{"multiplier": 1, "slippage_bps": cfg.slippage_bps, **{k: bt.metrics.get(k) for k in SENSITIVITY_KEYS}}]
    for m in multipliers:
        more = replace(cfg, slippage_bps=cfg.slippage_bps * m)
        rerun = backtest_strategy(bt.strategy, bt.data, bt.sizing, more, bt.start, with_benchmark=False, tax=tax)
        rows.append({"multiplier": m, "slippage_bps": more.slippage_bps, **{k: rerun.metrics.get(k) for k in SENSITIVITY_KEYS}})
    key = "after_tax_cagr_if_sold" if tax is not None else "cagr"
    basis = "after costs and taxes, if sold at the end" if tax is not None and tax.taxable else "after costs"
    bench = (bt.benchmark_metrics or {}).get(key)
    double = next((r for r in rows if r["multiplier"] == 2), None)
    if cfg.slippage_bps == 0:
        verdict = "Slippage is set to 0 bps, so doubling it changes nothing: set a realistic slippage first."
    elif double is None or double.get(key) is None or bench is None:
        verdict = "Not enough data to compare with buy-and-hold at double costs."
    else:
        beats = double[key] > bench
        verdict = (
            f"At double slippage ({double['slippage_bps']:g} bps per fill) the strategy compounds at "
            f"{double[key]:.1%} a year {basis}, versus {bench:.1%} for buy-and-hold: it "
            f"{'still beats' if beats else 'does not beat'} buy-and-hold at double costs."
        )
    return {"rows": rows, "benchmark": {k: (bt.benchmark_metrics or {}).get(k) for k in SENSITIVITY_KEYS}, "verdict": verdict}


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


def risk_warnings(counts: dict[str, int]) -> list[str]:
    """Plain words for what the risk engine did during a backtest."""
    out = []
    if counts.get("daily_loss"):
        out.append(f"The daily loss limit fired {counts['daily_loss']} times; each time the account was flattened until the next session.")
    if counts.get("weekly_loss"):
        out.append(f"The weekly loss limit fired {counts['weekly_loss']} times; each time the account was flattened until the next week.")
    if counts.get("trade_risk"):
        out.append(f"On {counts['trade_risk']} sessions a trade was shrunk to the maximum risk per trade.")
    if counts.get("positions"):
        out.append(f"On {counts['positions']} sessions the strategy wanted more positions than the limit allows.")
    if counts.get("position_size"):
        out.append(f"On {counts['position_size']} sessions a position was cut to the maximum position size.")
    if counts.get("short"):
        out.append(f"Short trades were refused on {counts['short']} sessions (shorting is off in risk/limits.yaml).")
    return out
