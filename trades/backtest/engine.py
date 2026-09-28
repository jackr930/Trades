"""Event-driven, share-based portfolio backtester.

Timeline for each bar ``t``:

1. orders decided at the close of bar ``t-1`` are filled at bar ``t``'s open
   (``execution="next_open"``, default) or close (``"next_close"``), with adverse
   slippage and commissions;
2. short borrow fees accrue and positions are marked to the close;
3. the target weights *known at the close of bar t* are converted into share orders
   for the next bar.

Because decisions only use information up to the close of ``t`` and execute at
``t+1``, a causal strategy cannot peek at the future.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields
from typing import Any

import numpy as np
import pandas as pd

from trades.core.ledger import EPS, Fill, Ledger, Trade


@dataclass
class BacktestConfig:
    initial_cash: float = 100_000.0
    commission_bps: float = 0.0  # percentage-of-notional commission, in basis points
    commission_per_share: float = 0.0
    min_commission: float = 0.0
    slippage_bps: float = 5.0  # adverse price impact per fill, in basis points
    allow_short: bool = True
    fractional: bool = False
    execution: str = "next_open"  # next_open | next_close
    min_trade_weight: float = 0.005  # skip re-sizing trades smaller than 0.5% of equity
    borrow_bps_annual: float = 0.0  # stock-loan fee on short positions
    periods_per_year: int = 252

    def __post_init__(self):
        self.initial_cash = float(self.initial_cash)
        if self.execution not in ("next_open", "next_close"):
            raise ValueError("execution must be 'next_open' or 'next_close'")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        for name in (
            "commission_bps",
            "commission_per_share",
            "min_commission",
            "slippage_bps",
            "borrow_bps_annual",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BacktestConfig:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known and v is not None})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def commission(self, qty: float, price: float) -> float:
        fee = abs(qty) * self.commission_per_share + abs(qty) * price * self.commission_bps / 1e4
        return max(fee, self.min_commission) if abs(qty) > EPS else 0.0


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

    @property
    def returns(self) -> pd.Series:
        return self.equity.pct_change(fill_method=None).fillna(0.0)

    @property
    def gross_exposure(self) -> pd.Series:
        return self.weights.abs().sum(axis=1)

    @property
    def net_exposure(self) -> pd.Series:
        return self.weights.sum(axis=1)


def _round_qty(qty: float, fractional: bool) -> float:
    if fractional:
        return round(qty, 6)
    return float(math.trunc(qty))  # toward zero: never oversize


def run_backtest(
    data: dict[str, pd.DataFrame],
    target_weights: pd.DataFrame,
    config: BacktestConfig | None = None,
    start: int = 0,
) -> BacktestResult:
    """Simulate trading ``target_weights`` (index aligned with every frame in ``data``).

    ``start`` is the first bar of the evaluation period (earlier bars only warm up
    indicators); equity is flat at ``initial_cash`` until then.
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

    ledger = Ledger(cfg.initial_cash)
    slip = cfg.slippage_bps / 1e4
    borrow = cfg.borrow_bps_annual / 1e4 / cfg.periods_per_year
    equity = np.full(T, float(cfg.initial_cash))  # float: an int fill value would truncate P&L
    cash = np.full(T, float(cfg.initial_cash))
    pos = np.zeros((T, N))
    act_w = np.zeros((T, N))
    last_target = np.zeros(N)
    pending: dict[int, float] = {}
    start = max(0, min(start, T - 1))

    def mark_excursions(t: int) -> None:
        for j, s in enumerate(symbols):
            if abs(ledger.qty(s)) > EPS:
                ledger.mark_excursions(s, H[t, j], L[t, j])

    for t in range(start, T):
        # 1) execute yesterday's decisions
        if pending:
            if cfg.execution == "next_close":
                mark_excursions(t)  # the bar's range happened before a closing fill
            for j, target_qty in list(pending.items()):
                s = symbols[j]
                ref = O[t, j] if cfg.execution == "next_open" else C[t, j]
                if not np.isfinite(ref) or ref <= 0:
                    continue  # no price: keep the order for the next bar
                delta = target_qty - ledger.qty(s)
                if abs(delta) <= EPS:
                    pending.pop(j)
                    continue
                price = ref * (1 + slip) if delta > 0 else ref * (1 - slip)
                ledger.apply_fill(
                    time=index[t],
                    index=t,
                    symbol=s,
                    qty=delta,
                    price=price,
                    commission=cfg.commission(delta, price),
                    slippage=abs(delta) * ref * slip,
                )
                pending.pop(j)
            if cfg.execution == "next_open":
                mark_excursions(t)
        else:
            mark_excursions(t)

        # 2) financing and mark-to-market
        prices = {s: C[t, j] for j, s in enumerate(symbols) if np.isfinite(C[t, j])}
        if borrow > 0:
            short_value = sum(-ledger.qty(s) * prices.get(s, 0.0) for s in symbols if ledger.qty(s) < 0)
            if short_value > 0:
                ledger.charge(short_value * borrow)
        eq = ledger.equity(prices)
        equity[t], cash[t] = eq, ledger.cash
        for j, s in enumerate(symbols):
            q = ledger.qty(s)
            pos[t, j] = q
            act_w[t, j] = q * C[t, j] / eq if eq > 0 and np.isfinite(C[t, j]) else 0.0

        # 3) turn today's targets into orders for the next bar (on the last bar these
        #    become the "pending" orders a live user would place for tomorrow)
        if eq <= 0:  # account wiped out: stop trading
            equity[t + 1 :] = eq
            cash[t + 1 :] = ledger.cash
            pending.clear()
            break
        for j, s in enumerate(symbols):
            tw = W[t, j]
            px = C[t, j]
            cur = ledger.qty(s)
            if not np.isfinite(px) or px <= 0:
                continue
            if abs(tw) <= 1e-12:
                if abs(cur) > EPS:
                    pending[j] = 0.0
                last_target[j] = 0.0
                continue
            changed = abs(tw - last_target[j]) > 1e-9
            flipped = cur * tw < 0
            if changed or flipped or abs(cur) <= EPS:
                desired = _round_qty(tw * eq / px, cfg.fractional)
                trade_value = abs(desired - cur) * px
                if abs(cur) <= EPS or flipped or trade_value >= cfg.min_trade_weight * eq:
                    if abs(desired - cur) > EPS:
                        pending[j] = desired
            last_target[j] = tw

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
        pending={symbols[j]: q for j, q in pending.items()},
    )


def buy_and_hold_weights(index: pd.DatetimeIndex, symbols: list[str], start: int = 0) -> pd.DataFrame:
    """Equal-weight buy-and-hold (bought once at ``start``, never rebalanced)."""
    w = np.zeros((len(index), len(symbols)))
    w[start:, :] = 1.0 / len(symbols)
    return pd.DataFrame(w, index=index, columns=symbols)
