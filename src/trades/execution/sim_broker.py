"""The simulated broker: turns target weights into share fills, one bar at a time.

Timeline for each bar ``t``:

1. orders decided at the close of bar ``t-1`` are filled at bar ``t``'s open
   (``execution="next_open"``, default) or close (``"next_close"``), with adverse
   costs. Orders that reduce exposure go first, and orders that add exposure are trimmed so
   gross exposure stays within ``max_gross_leverage``;
2. working orders placed at the close of ``t-1`` are checked against bar ``t``'s range
   (stop entries, protective stops, take-profits; see the table below), and intraday
   strategies are flattened in the closing auction on the session's last bar;
3. financing accrues (short borrow fees, interest on borrowed cash, optional interest
   on idle cash) and positions are marked to the close;
4. the target weights *known at the close of bar t* are converted into share orders
   for the next bar.

Because decisions only use information up to the close of ``t`` and execute at
``t+1``, a causal strategy cannot peek at the future. ``ExecutionEngine`` performs one
bar at a time; ``trades.engine.backtest.run_backtest`` loops it over history, and the
simulator's strategy agents feed it bars as they arrive, so a forward test trades exactly
like a backtest.

Orders and fills (``next_open`` execution). Prices are fixed when the order is placed, at a
bar's close, exactly as a broker's bracket order fixes them at submission:

=====================  ==============================  =====================================
order                  fills during the next bar when   at
=====================  ==============================  =====================================
market (new target)    always, at the open              open, plus spread and slippage
stop entry             the price reaches the level      level (or the open, if it gapped
                                                        through), plus costs and stop slippage
protective stop        the price reaches the level      level (or the open), minus costs and
                                                        stop slippage
take-profit (limit)    the price trades *through* it    level (or the open, if better); no
                                                        spread or slippage
closing auction        the session's last bar           close, plus spread and slippage
=====================  ==============================  =====================================

When one bar could have triggered two orders and the bar cannot say which came first, the
engine assumes the worse outcome: a stop beats a take-profit, and a stop entry that fills
*inside* a bar can be stopped out in that same bar (but never reaches its take-profit there,
since the part of the bar before the entry is unknown). A stop entry that fills at the open,
because the price gapped through the level, sees the whole bar and is treated like any other
position. Each worse-outcome assumption is counted in ``ambiguous_fills``; finer bars make it
rarer. After a protective exit the engine does not re-enter in the same direction until the
target goes flat or reverses, so a strategy whose signal still says "long" cannot buy straight
back into a stopped-out trade.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

import numpy as np

from trades.execution.ledger import EPS, Fill, Ledger
from trades.execution.orders import plan_symbol, round_qty

COST_FIELDS = (
    "half_spread_bps",
    "slippage_bps",
    "stop_slippage_bps",
    "commission_bps",
    "commission_per_share",
    "min_commission",
    "sec_fee_per_million",
    "taf_per_share",
    "taf_max_per_trade",
)


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
    # The detailed cost model (all zero by default, so a plain backtest charges ``slippage_bps``
    # and commissions only). ``trades.execution.costs`` fills these from config/base.yaml.
    half_spread_bps: float = 0.0  # half the quoted spread, paid on every market or stop fill
    stop_slippage_bps: float = 0.0  # extra slippage on stop fills (entries and protective stops)
    sec_fee_per_million: float = 0.0  # SEC Section 31 fee on sales, $ per $1M sold
    taf_per_share: float = 0.0  # FINRA trading activity fee per share sold ...
    taf_max_per_trade: float = 0.0  # ... capped per trade (0 = no cap)
    flat_by_close: bool = False  # intraday strategies: close every position in the closing auction

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
            "half_spread_bps",
            "stop_slippage_bps",
            "sec_fee_per_million",
            "taf_per_share",
            "taf_max_per_trade",
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
        """Commission plus, on sales (``qty`` < 0), the SEC and FINRA regulatory fees."""
        if abs(qty) <= EPS:
            return 0.0
        fee = abs(qty) * self.commission_per_share + abs(qty) * price * self.commission_bps / 1e4
        fee = max(fee, self.min_commission)
        if qty < 0:
            fee += abs(qty) * price * self.sec_fee_per_million / 1e6
            taf = abs(qty) * self.taf_per_share
            fee += min(taf, self.taf_max_per_trade) if self.taf_max_per_trade > 0 else taf
        return fee

    def capped(self, max_gross_leverage: float) -> BacktestConfig:
        """This config with a fill-time leverage cap, unless one was set explicitly."""
        if self.max_gross_leverage is not None:
            return self
        return replace(self, max_gross_leverage=float(max_gross_leverage))


def _level(levels, j: int) -> float | None:
    if levels is None:
        return None
    v = float(levels[j])
    return v if math.isfinite(v) and v > 0 else None


class ExecutionEngine:
    """Trades a portfolio towards target weights one bar at a time.

    Call ``execute`` when bar ``t`` completes (fills the previous close's orders, checks
    working orders against the bar, accrues financing, marks to the close), then ``decide``
    with the target weights, and any stop, take-profit or stop-entry levels, known at that
    close (queues orders for bar ``t+1``).
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
        # Working orders for the next bar, placed at the last close.
        self.entries: dict[int, tuple[float, float]] = {}  # symbol -> (stop-entry level, signed qty)
        self.protect: dict[int, tuple[float | None, float | None]] = {}  # symbol -> (stop, take-profit)
        self.latched: dict[int, float] = {}  # symbol -> direction of a trade a protective order closed
        self.ambiguous_fills = 0  # fills that relied on the "assume the worse outcome" rule
        self._slip = (cfg.slippage_bps + cfg.half_spread_bps) / 1e4
        self._stop_slip = cfg.stop_slippage_bps / 1e4
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

    def working_orders(self) -> list[dict[str, Any]]:
        """Stop entries, protective stops and take-profits waiting for the next bar."""
        out = []
        for j, (level, qty) in self.entries.items():
            out.append({"symbol": self.symbols[j], "type": "stop_entry", "price": level, "qty": qty})
        for j, (stop, take) in self.protect.items():
            if stop is not None:
                out.append({"symbol": self.symbols[j], "type": "stop", "price": stop})
            if take is not None:
                out.append({"symbol": self.symbols[j], "type": "take_profit", "price": take})
        return out

    # -- bar processing ----------------------------------------------------------------
    def execute(self, t: int, time: Any, open_, high, low, close, closing: bool = False) -> list[Fill]:
        """Bar ``t`` has completed: fill pending orders, check working orders, mark to close.

        ``closing``: bar ``t`` is the last bar of its trading session (intraday strategies
        are flattened in the closing auction).
        """
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
        entered = self._stop_entries(t, time, open_, high, low, close) if self.entries else {}
        if self.protect:
            self._protective(t, time, open_, high, low, close, entered)
        self.entries.clear()
        self.protect.clear()
        if closing and self.cfg.flat_by_close:
            self._closing_auction(t, time, close)
        self.close = np.asarray(close, dtype=float)
        prices = self.prices()
        self._finance(prices)
        self.equity = self.ledger.equity(prices)
        if self.equity <= 0:  # account wiped out: stop trading
            self.stopped = True
            self.pending.clear()
        return self.ledger.fills[n_fills:]

    def decide(
        self,
        t: int,
        targets,
        stop_losses=None,
        take_profits=None,
        entry_stops=None,
        closing: bool = False,
    ) -> dict[str, float]:
        """Turn the target weights known at the close of bar ``t`` into orders for ``t+1``.

        ``stop_losses``, ``take_profits`` and ``entry_stops`` are optional per-symbol price levels
        (NaN for none). A target with an entry stop, while the symbol is flat, becomes a stop
        order at that level instead of a market order.
        """
        if self.stopped:
            return {}
        cfg = self.cfg
        if (stop_losses is not None or take_profits is not None or entry_stops is not None) and cfg.execution != "next_open":
            raise ValueError("stop, take-profit and stop-entry orders need execution='next_open'")
        if closing and cfg.flat_by_close:
            # An intraday strategy is flat after the closing auction; nothing carries overnight.
            self.pending.clear()
            self.latched.clear()
            self.last_target[:] = 0.0
            return {}
        W = np.nan_to_num(np.asarray(targets, dtype=float))
        if not cfg.allow_short:
            W = np.clip(W, 0.0, None)
        for j, s in enumerate(self.symbols):
            plan = plan_symbol(
                float(W[j]),
                self.ledger.qty(s),
                float(self.close[j]),
                self.equity,
                last_target=float(self.last_target[j]),
                latched=self.latched.get(j),
                entry=_level(entry_stops, j),
                stop=_level(stop_losses, j),
                take=_level(take_profits, j),
                fractional=cfg.fractional,
                min_trade_weight=cfg.min_trade_weight,
            )
            if plan.skip:
                continue
            self.latched.pop(j, None)
            self.last_target[j] = plan.last_target
            if plan.market is not None:
                self.pending[j] = plan.market
            if plan.cancel_market:
                self.pending.pop(j, None)
            if plan.entry is not None:
                self.entries[j] = plan.entry
            if plan.protect is not None:
                self.protect[j] = plan.protect
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
                capped = self._within_leverage(j, target_qty, ref_prices, close)
                if cur * target_qty > 0:  # an add the cap blocks is skipped, never turned into a sale
                    capped = float(np.sign(target_qty)) * max(abs(capped), abs(cur))
                target_qty = capped
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
                tag="market",
            )

    def _trade(self, t: int, time: Any, j: int, qty: float, ref: float, cost: float, tag: str) -> None:
        """Fill ``qty`` shares of symbol ``j`` at ``ref`` moved against us by ``cost`` (a fraction)."""
        price = ref * (1 + cost) if qty > 0 else ref * (1 - cost)
        self.ledger.apply_fill(
            time=time,
            index=t,
            symbol=self.symbols[j],
            qty=qty,
            price=price,
            commission=self.cfg.commission(qty, price),
            slippage=abs(qty) * ref * cost,
            tag=tag,
        )

    def _stop_entries(self, t, time, open_, high, low, close) -> dict[int, bool]:
        """Fill stop entries the bar reached. Returns symbol -> whether it filled *inside* the bar
        (at the level) rather than at the open (a gap through the level)."""
        entered: dict[int, bool] = {}
        for j, (level, qty) in self.entries.items():
            s = self.symbols[j]
            if abs(self.ledger.qty(s)) > EPS or j in self.pending:
                continue  # already in a position (or about to change it): no second entry
            o, h, lo = float(open_[j]), float(high[j]), float(low[j])
            if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(lo)):
                continue
            if qty > 0:
                ref = o if o >= level else level if h >= level else None
            else:
                ref = o if o <= level else level if lo <= level else None
            if ref is None:
                continue
            if self.cfg.max_gross_leverage is not None:  # other positions are marked at this bar's open
                qty = self._within_leverage(j, qty, open_, open_, ref_price=ref)
            if abs(qty) <= EPS:
                continue
            self._trade(t, time, j, qty, ref, self._slip + self._stop_slip, "stop_entry")
            self.ledger.mark_excursions(s, h, lo)
            entered[j] = ref != o
        return entered

    def _protective(self, t, time, open_, high, low, close, entered: dict[int, bool]) -> None:
        for j, (stop, take) in self.protect.items():
            s = self.symbols[j]
            q = self.ledger.qty(s)
            if abs(q) <= EPS:
                continue
            o, h, lo = float(open_[j]), float(high[j]), float(low[j])
            if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(lo)):
                continue
            long = q > 0
            # Entered inside this bar: the part of the bar before the entry is unknown.
            just_entered = entered.get(j, False)
            gap_stop = stop is not None and not just_entered and (o <= stop if long else o >= stop)
            gap_take = take is not None and not just_entered and (o >= take if long else o <= take)
            hit_stop = stop is not None and (lo <= stop if long else h >= stop)
            hit_take = take is not None and not just_entered and (h > take if long else lo < take)
            if gap_stop:
                exit_ref, tag, cost = o, "stop", self._slip + self._stop_slip
            elif gap_take:
                exit_ref, tag, cost = o, "take_profit", 0.0
            elif hit_stop:
                if hit_take or just_entered:
                    self.ambiguous_fills += 1  # assumed the stop came first (or came after the entry)
                exit_ref, tag, cost = stop, "stop", self._slip + self._stop_slip
            elif hit_take:
                exit_ref, tag, cost = take, "take_profit", 0.0
            else:
                continue
            self._trade(t, time, j, -q, float(exit_ref), cost, tag)
            self.pending.pop(j, None)
            self.latched[j] = 1.0 if long else -1.0

    def _closing_auction(self, t: int, time: Any, close) -> None:
        for j, s in enumerate(self.symbols):
            q = self.ledger.qty(s)
            c = float(close[j])
            if abs(q) > EPS and math.isfinite(c) and c > 0:
                self._trade(t, time, j, -q, c, self._slip, "close")
        self.pending.clear()
        self.latched.clear()

    def _within_leverage(self, j: int, target_qty: float, ref_prices, close, ref_price: float | None = None) -> float:
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
        fill_ref = float(ref_prices[j]) if ref_price is None else ref_price
        max_qty = round_qty(room / (fill_ref * (1 + self._slip)), self.cfg.fractional)
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
