"""How a target becomes orders: the one rule that backtests and the paper runners share.

At a bar's close, the strategy (after sizing and the risk engine) says what weight it wants in a
symbol, and maybe a stop-entry level, a protective stop and a take-profit. ``plan_symbol`` says
what to do about it before the next bar, given what is held:

* **a market order** to a new number of shares, when the target weight changed, the position is
  flat or would flip, and the trade is worth making (a re-size smaller than ``min_trade_weight`` of
  equity is skipped);
* **a stop entry** instead, when the symbol is flat and the strategy gave an entry level: buy (or
  sell short) only if the price reaches the level;
* **protective levels** (stop, take-profit) for the position;
* **the latch**: after a protective exit, no re-entry in the same direction until the target goes
  flat or reverses, so a signal that still says "long" cannot buy straight back into a stopped-out
  trade.

``trades.execution.sim_broker.ExecutionEngine`` applies the plan to its simulated book, and the
paper runners turn it into broker orders, so neither re-implements the rule.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trades.execution.ledger import EPS


@dataclass(frozen=True)
class SymbolPlan:
    skip: bool = False  # no price, or a latched trade: change nothing this bar (keep any older order)
    market: float | None = None  # target shares to reach with a market order (None: no market order)
    entry: tuple[float, float] | None = None  # (stop level, signed shares) of a stop entry
    protect: tuple[float | None, float | None] | None = None  # (stop, take-profit) for the position
    last_target: float | None = 0.0  # the target weight to remember for the next bar
    cancel_market: bool = False  # drop a market order still waiting from an earlier bar


def round_qty(qty: float, fractional: bool) -> float:
    """Shares to order for ``qty``: whole shares toward zero unless fractional shares are allowed."""
    if fractional:
        return round(qty, 6)
    return float(math.trunc(qty))  # toward zero: never oversize


def plan_symbol(
    target: float,
    held: float,
    price: float,
    equity: float,
    *,
    last_target: float | None,
    latched: float | None = None,
    entry: float | None = None,
    stop: float | None = None,
    take: float | None = None,
    fractional: bool = False,
    min_trade_weight: float = 0.005,
) -> SymbolPlan:
    """What to do about one symbol at a bar's close.

    ``target``: the weight wanted; ``held``: shares held now; ``price``: the bar's close;
    ``last_target``: the weight wanted at the previous bar (None when unknown or not reached: plan
    afresh); ``latched``: the direction (+1/-1) of a trade a protective order closed, while the
    latch is on; ``entry``/``stop``/``take``: the strategy's levels, if any. A plan that is not
    ``skip`` releases the latch.
    """
    if not math.isfinite(price) or price <= 0:
        return SymbolPlan(skip=True, last_target=last_target)
    direction = float(math.copysign(1.0, target)) if target != 0 else 0.0
    if latched is not None and direction == latched:
        return SymbolPlan(skip=True, last_target=last_target)  # stopped out: wait for the target to change
    if abs(target) <= 1e-12:
        return SymbolPlan(market=0.0 if abs(held) > EPS else None, last_target=0.0)
    market = new_entry = None
    cancel = False
    if entry is not None and abs(held) <= EPS:
        qty = round_qty(target * equity / entry, fractional)
        new_entry = (entry, qty) if abs(qty) > EPS else None
        cancel = True
    else:
        changed = last_target is None or abs(target - last_target) > 1e-9
        flipped = held * target < 0
        if changed or flipped or abs(held) <= EPS:
            desired = round_qty(target * equity / price, fractional)
            trade_value = abs(desired - held) * price
            if (abs(held) <= EPS or flipped or trade_value >= min_trade_weight * equity) and abs(desired - held) > EPS:
                market = desired
    protect = (stop, take) if stop is not None or take is not None else None
    return SymbolPlan(market=market, entry=new_entry, protect=protect, last_target=target, cancel_market=cancel)
