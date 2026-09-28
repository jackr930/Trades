"""Event-driven, share-based portfolio execution and backtesting.

Timeline for each bar ``t``:

1. orders decided at the close of bar ``t-1`` are filled at bar ``t``'s open
   (``execution="next_open"``, default) or close (``"next_close"``), with adverse
   slippage and commissions. Orders that reduce exposure go first, and orders that add
   exposure are trimmed so gross exposure stays within ``max_gross_leverage``;
2. financing accrues (short borrow fees, interest on borrowed cash, optional interest
   on idle cash) and positions are marked to the close;
3. the target weights *known at the close of bar t* are converted into share orders
   for the next bar.

Because decisions only use information up to the close of ``t`` and execute at
``t+1``, a causal strategy cannot peek at the future. ``ExecutionEngine`` performs one
bar at a time; ``run_backtest`` loops it over history, and the live strategy agents
feed it bars as they arrive, so a forward test trades exactly like a backtest.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields, replace
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
    borrow_bps_annual: float = 25.0  # stock-loan fee on short positions (easy-to-borrow ~0.25%/yr)
    # Interest on borrowed cash (negative balance). Returns are reported in excess of cash
    # (idle cash earns ``cash_rate_annual``, 0 by default, and Sharpe assumes a 0% risk-free
    # rate), so the default is the typical *spread* of a margin loan over cash rates.
    margin_rate_annual: float = 0.02
    cash_rate_annual: float = 0.0
    max_gross_leverage: float | None = None  # fill-time cap on gross exposure / equity
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
            "margin_rate_annual",
            "cash_rate_annual",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.margin_rate_annual > 1 or self.cash_rate_annual > 1:
            raise ValueError("interest rates are annual fractions (0.05 = 5%)")
        if self.max_gross_leverage is not None and self.max_gross_leverage <= 0:
            raise ValueError("max_gross_leverage must be positive")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BacktestConfig:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known and v is not None})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def commission(self, qty: float, price: float) -> float:
        fee = abs(qty) * self.commission_per_share + abs(qty) * price * self.commission_bps / 1e4
        return max(fee, self.min_commission) if abs(qty) > EPS else 0.0

    def capped(self, max_gross_leverage: float) -> BacktestConfig:
        """This config with a fill-time leverage cap, unless one was set explicitly."""
        if self.max_gross_leverage is not None:
            return self
        return replace(self, max_gross_leverage=float(max_gross_leverage))


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


class ExecutionEngine:
    """Trades a portfolio towards target weights one bar at a time.

    Call ``execute`` when bar ``t`` completes (fills yesterday's orders, accrues
    financing, marks to the close), then ``decide`` with the target weights known at
    that close (queues share orders for bar ``t+1``).
    """

    def __init__(self, symbols: list[str], config: BacktestConfig | None = None):
        self.cfg = cfg = config or BacktestConfig()
        self.symbols = list(symbols)
        self.ledger = Ledger(cfg.initial_cash)
        self.pending: dict[int, float] = {}  # symbol index -> target share quantity
        self.last_target = np.zeros(len(self.symbols))
        self.close = np.full(len(self.symbols), np.nan)  # closes of the latest bar
        self.equity = cfg.initial_cash
        self.stopped = False  # the account was wiped out
        self._slip = cfg.slippage_bps / 1e4
        ppy = cfg.periods_per_year
        self._borrow = cfg.borrow_bps_annual / 1e4 / ppy
        self._margin = cfg.margin_rate_annual / ppy
        self._cash_rate = cfg.cash_rate_annual / ppy

    # -- queries ---------------------------------------------------------------------
    def prices(self) -> dict[str, float]:
        return {s: float(p) for s, p in zip(self.symbols, self.close, strict=True) if np.isfinite(p)}

    def weights(self) -> dict[str, float]:
        eq = self.equity
        return {
            s: self.ledger.qty(s) * float(p) / eq if eq > 0 and np.isfinite(p) else 0.0
            for s, p in zip(self.symbols, self.close, strict=True)
        }

    def orders(self) -> dict[str, float]:
        """Share quantities that will be traded at the next bar (target - current)."""
        return {self.symbols[j]: q - self.ledger.qty(self.symbols[j]) for j, q in self.pending.items()}

    # -- bar processing ----------------------------------------------------------------
    def execute(self, t: int, time: Any, open_, high, low, close) -> list[Fill]:
        """Bar ``t`` has completed: fill pending orders, accrue financing, mark to close."""
        n_fills = len(self.ledger.fills)
        if self.stopped:
            self.close = np.asarray(close, dtype=float)
            return []
        next_open = self.cfg.execution == "next_open"
        if self.pending:
            if not next_open:
                self._excursions(high, low)  # the bar's range happened before a closing fill
            self._fill(t, time, open_ if next_open else close, close)
            if next_open:
                self._excursions(high, low)
        else:
            self._excursions(high, low)
        self.close = np.asarray(close, dtype=float)
        prices = self.prices()
        self._finance(prices)
        self.equity = self.ledger.equity(prices)
        if self.equity <= 0:  # account wiped out: stop trading
            self.stopped = True
            self.pending.clear()
        return self.ledger.fills[n_fills:]

    def decide(self, t: int, targets) -> dict[str, float]:
        """Turn the target weights known at the close of bar ``t`` into orders for ``t+1``."""
        if self.stopped:
            return {}
        cfg = self.cfg
        W = np.nan_to_num(np.asarray(targets, dtype=float))
        if not cfg.allow_short:
            W = np.clip(W, 0.0, None)
        eq = self.equity
        for j, s in enumerate(self.symbols):
            tw = W[j]
            px = self.close[j]
            cur = self.ledger.qty(s)
            if not np.isfinite(px) or px <= 0:
                continue
            if abs(tw) <= 1e-12:
                if abs(cur) > EPS:
                    self.pending[j] = 0.0
                self.last_target[j] = 0.0
                continue
            changed = abs(tw - self.last_target[j]) > 1e-9
            flipped = cur * tw < 0
            if changed or flipped or abs(cur) <= EPS:
                desired = _round_qty(tw * eq / px, cfg.fractional)
                trade_value = abs(desired - cur) * px
                if abs(cur) <= EPS or flipped or trade_value >= cfg.min_trade_weight * eq:
                    if abs(desired - cur) > EPS:
                        self.pending[j] = desired
            self.last_target[j] = tw
        return self.orders()

    # -- internals -------------------------------------------------------------------
    def _excursions(self, high, low) -> None:
        for j, s in enumerate(self.symbols):
            if abs(self.ledger.qty(s)) > EPS:
                self.ledger.mark_excursions(s, float(high[j]), float(low[j]))

    def _fill(self, t: int, time: Any, ref_prices, close) -> None:
        cfg, led = self.cfg, self.ledger

        def adds_exposure(item: tuple[int, float]) -> bool:
            return abs(item[1]) > abs(led.qty(self.symbols[item[0]])) + EPS

        # Reducing orders first: they free up capital for the ones that add exposure.
        for j, target_qty in sorted(self.pending.items(), key=adds_exposure):
            s = self.symbols[j]
            ref = float(ref_prices[j])
            if not np.isfinite(ref) or ref <= 0:
                continue  # no price: keep the order for the next bar
            self.pending.pop(j)
            cur = led.qty(s)
            if cfg.max_gross_leverage is not None and abs(target_qty) > abs(cur) + EPS:
                target_qty = self._within_leverage(j, target_qty, ref_prices, close)
            delta = target_qty - cur
            if abs(delta) <= EPS:
                continue
            price = ref * (1 + self._slip) if delta > 0 else ref * (1 - self._slip)
            led.apply_fill(
                time=time,
                index=t,
                symbol=s,
                qty=delta,
                price=price,
                commission=cfg.commission(delta, price),
                slippage=abs(delta) * ref * self._slip,
            )

    def _within_leverage(self, j: int, target_qty: float, ref_prices, close) -> float:
        """Shrink an exposure-increasing target so gross exposure stays within the cap."""
        marks: dict[str, float] = {}
        for k, s in enumerate(self.symbols):
            for p in (ref_prices[k], self.close[k], close[k]):
                if np.isfinite(p) and p > 0:
                    marks[s] = float(p)
                    break
        equity = self.ledger.equity(marks)
        s = self.symbols[j]
        other = sum(abs(self.ledger.qty(x) * p) for x, p in marks.items() if x != s)
        room = max(self.cfg.max_gross_leverage * equity - other, 0.0)
        max_qty = _round_qty(room / (float(ref_prices[j]) * (1 + self._slip)), self.cfg.fractional)
        return float(np.sign(target_qty)) * min(abs(target_qty), max_qty)

    def _finance(self, prices: dict[str, float]) -> None:
        led = self.ledger
        if self._borrow > 0:
            short_value = sum(-led.qty(s) * prices.get(s, 0.0) for s in self.symbols if led.qty(s) < 0)
            if short_value > 0:
                led.charge(short_value * self._borrow)
        if led.cash < 0 and self._margin > 0:
            led.charge(-led.cash * self._margin)
        elif led.cash > 0 and self._cash_rate > 0:
            led.charge(-led.cash * self._cash_rate)


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

    engine = ExecutionEngine(symbols, cfg)
    ledger = engine.ledger
    equity = np.full(T, float(cfg.initial_cash))  # float: an int fill value would truncate P&L
    cash = np.full(T, float(cfg.initial_cash))
    pos = np.zeros((T, N))
    act_w = np.zeros((T, N))
    start = max(0, min(start, T - 1))

    for t in range(start, T):
        engine.execute(t, index[t], O[t], H[t], L[t], C[t])
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
        # On the last bar these become the "pending" orders a live user would place for tomorrow.
        engine.decide(t, W[t])

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
    )


def buy_and_hold_weights(index: pd.DatetimeIndex, symbols: list[str], start: int = 0) -> pd.DataFrame:
    """Equal-weight buy-and-hold (bought once at ``start``, never rebalanced)."""
    w = np.zeros((len(index), len(symbols)))
    w[start:, :] = 1.0 / len(symbols)
    return pd.DataFrame(w, index=index, columns=symbols)
