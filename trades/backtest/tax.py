"""After-tax results, computed after the backtest from its fills (the engine is unchanged).

Each fill records the gain or loss it realized and how long the shares it closed had been
held. Per calendar year:

1. gains and losses are split into short-term (held one year or less) and long-term;
2. each kind is netted, then a net loss of one kind offsets a net gain of the other;
3. a remaining net loss is carried forward into the next year, keeping its kind;
4. tax at the short- and long-term rates is charged on the year's last bar.

The tax comes out of the account, so the after-tax account compounds the strategy's returns
on a smaller base: each year's tax is taken as a share of the pre-tax equity on that bar.
Buy-and-hold only pays tax on what it realizes (usually nothing); ``liquidation`` adds the
tax due if every open position were sold on the last bar. A tax-advantaged account (an IRA,
say) pays no tax as it goes, so its after-tax result equals the pre-tax one.

Simplifications:

* average cost instead of tax lots (the holding period runs from the share-weighted
  average purchase date);
* no wash-sale rule;
* net capital losses are only carried forward, never offset against up to $3,000 of
  ordinary income a year;
* federal tax only (no state tax), at flat rates you choose rather than brackets;
* dividends are not taxed separately (adjusted prices fold them into returns);
* selling shares to pay the tax realizes no further gains.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from trades.core.ledger import Fill, Ledger

LONG_TERM_DAYS = 365  # held one year or less -> short-term


@dataclass(frozen=True)
class TaxProfile:
    account_type: str = "taxable"  # taxable | tax_advantaged
    short_term_rate: float = 0.22
    long_term_rate: float = 0.15

    def __post_init__(self):
        if self.account_type not in ("taxable", "tax_advantaged"):
            raise ValueError("account_type must be 'taxable' or 'tax_advantaged'")
        for rate in (self.short_term_rate, self.long_term_rate):
            if not 0 <= rate < 1:
                raise ValueError("tax rates are fractions between 0 and 1 (0.22 = 22%)")

    @property
    def taxable(self) -> bool:
        return self.account_type == "taxable"


@dataclass
class AfterTax:
    equity: pd.Series  # after-tax equity curve
    taxes: dict[int, float] = field(default_factory=dict)  # tax charged per year, in pre-tax dollars
    # The last year's realized (short, long) gains and the losses carried into it: selling
    # everything on the last bar would be taxed together with them.
    last_year: tuple[float, float] = (0.0, 0.0)
    carry_in: tuple[float, float] = (0.0, 0.0)


def is_long_term(holding_days: float | None) -> bool:
    return holding_days is not None and holding_days > LONG_TERM_DAYS


def year_tax(short: float, long: float, carry: tuple[float, float], profile: TaxProfile) -> float:
    s, lg, _, _ = net_year(short, long, *carry)
    return s * profile.short_term_rate + lg * profile.long_term_rate


def net_year(short: float, long: float, carry_short: float, carry_long: float) -> tuple[float, float, float, float]:
    """One year's taxable (short, long) gains and the losses carried into the next year."""
    short, long = short + carry_short, long + carry_long
    # A net loss of one kind offsets a net gain of the other; what is left keeps its kind.
    if short < 0 < long:
        short, long = min(short + long, 0.0), max(short + long, 0.0)
    elif long < 0 < short:
        short, long = max(short + long, 0.0), min(short + long, 0.0)
    return max(short, 0.0), max(long, 0.0), min(short, 0.0), min(long, 0.0)


def after_tax(equity: pd.Series, fills: list[Fill], profile: TaxProfile) -> AfterTax:
    """After-tax equity for a pre-tax ``equity`` curve and the fills behind it."""
    if not profile.taxable or len(equity) < 2:
        return AfterTax(equity.copy())
    gains: dict[int, list[float]] = {}  # year -> [short-term, long-term]
    for f in fills:
        # Commissions reduce taxable gains. Each is deducted in the year it is paid, with the
        # character of the fill it belongs to (an opening fill's counts as short-term).
        amount = f.realized_pnl - f.commission
        if amount:
            year = pd.Timestamp(f.time).year
            # Only a sale closing a long can be long-term: gains on short sales are short-term
            # however long the short was open (IRC section 1233).
            kind = 1 if f.qty < 0 and is_long_term(f.holding_days) else 0
            gains.setdefault(year, [0.0, 0.0])[kind] += amount
    years = equity.index.year
    last_bar_of_year = pd.Series(years, index=equity.index) != pd.Series(years, index=equity.index).shift(-1)
    carry_s = carry_l = 0.0
    taxes: dict[int, float] = {}
    rate: dict[pd.Timestamp, float] = {}  # bar -> share of pre-tax equity paid in tax
    realized, carry_in = (0.0, 0.0), (0.0, 0.0)
    for ts in equity.index[last_bar_of_year.to_numpy()]:
        realized, carry_in = tuple(gains.get(ts.year, (0.0, 0.0))), (carry_s, carry_l)
        short, long, carry_s, carry_l = net_year(*realized, carry_s, carry_l)
        tax = short * profile.short_term_rate + long * profile.long_term_rate
        taxes[ts.year] = tax
        if tax > 0 and equity.loc[ts] > 0:
            rate[ts] = min(tax / float(equity.loc[ts]), 1.0)
    growth = (equity / equity.shift(1)).fillna(1.0).to_numpy()
    out, value = [], float(equity.iloc[0])
    for ts, g in zip(equity.index, growth, strict=True):
        value *= g if math.isfinite(g) else 1.0
        value *= 1.0 - rate.get(ts, 0.0)
        out.append(value)
    return AfterTax(pd.Series(out, index=equity.index, name="after_tax"), taxes, realized, carry_in)


def liquidation(
    result: AfterTax, ledger: Ledger, prices: dict[str, float], end_time, profile: TaxProfile, pre_tax_end: float
) -> float:
    """After-tax equity if every open position were sold on the last bar (no extra costs)."""
    if not profile.taxable:
        return float(result.equity.iloc[-1])
    end_ns = pd.Timestamp(end_time).value
    short = long = 0.0
    for sym, pos in ledger.positions.items():
        if abs(pos.qty) <= 1e-9 or sym not in prices:
            continue
        gain = pos.qty * (prices[sym] - pos.avg_price)
        days = (end_ns - pos.opened_ns) / 86_400e9 if pos.opened_ns is not None else None
        if pos.qty > 0 and is_long_term(days):  # short positions are always short-term
            long += gain
        else:
            short += gain
    # Sold on the last bar, these gains join the last year's: the extra tax is the difference.
    rs, rl = result.last_year
    tax = year_tax(rs + short, rl + long, result.carry_in, profile) - year_tax(rs, rl, result.carry_in, profile)
    end_value = float(result.equity.iloc[-1])
    # Scale to the after-tax account, which is smaller than the pre-tax one the ledger tracks.
    return end_value * (1.0 - tax / pre_tax_end) if pre_tax_end > 0 else end_value


def cagr(start_value: float, end_value: float, years: float) -> float | None:
    if years <= 0 or start_value <= 0 or end_value <= 0:
        return None
    return (end_value / start_value) ** (1 / years) - 1.0
