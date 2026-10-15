"""Planning tools: will my savings reach a goal, what do costs and taxes take, and what could
I do about the accounts I hold? Suggestions and arithmetic only; nothing here trades.

* **Goal planner**: a stock, bond and cash mix, simulated forward by drawing 12-month blocks
  of its own history (a block bootstrap), with monthly contributions, in today's dollars.
  Alongside it, the mix's worst fall in that history, so you can ask whether you would
  have stayed the course.
* **Cost and tax drag**: what fund fees, advisory fees, trading costs and taxes take from
  the same return over the years, for buy-and-hold next to a scenario of your choosing.
* **Allocation**: your holdings by asset class against a target, how much to move to get
  back within a band, and where (asset location: bonds in tax-advantaged accounts first).
* **Tax-loss harvesting**: taxable positions below their cost, the tax a sale could defer,
  and the wash-sale rule to respect.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from trades.holdings import account_type

CLASSES = ("stocks", "bonds", "cash", "real_estate")
PROXIES = {"stocks": "SPY", "bonds": "AGG", "cash": "BIL"}  # what each class earned in the past
DEMO_PROXIES = {"stocks": "SIMIDX", "bonds": "SIMBND"}  # the demo market has no T-bill fund

# Common funds by asset class. Anything else is classed from its description, or as stocks.
KNOWN = {
    "stocks": {
        "SPY", "VOO", "IVV", "VTI", "ITOT", "SCHB", "SCHX", "QQQ", "QQQM", "DIA", "IWM", "VB", "VO", "VUG", "VTV", "SCHD",
        "VXUS", "IXUS", "VEA", "IEFA", "VWO", "IEMG", "EFA", "EEM", "ACWI", "VT", "FXAIX", "FSKAX", "FZROX", "FTIHX",
        "VTSAX", "VFIAX", "VTIAX", "SWPPX", "SWTSX", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY",
    },
    "bonds": {
        "AGG", "BND", "BNDX", "TLT", "IEF", "SHY", "LQD", "HYG", "JNK", "TIP", "VTIP", "SCHZ", "VBTLX", "FXNAX",
        "VGIT", "VGSH", "VGLT", "MUB", "BSV", "BIV", "BLV", "GOVT", "SCHR", "SCHO", "IUSB", "EMB", "VCIT", "VCSH",
    },
    "real_estate": {"VNQ", "VNQI", "SCHH", "IYR", "XLRE", "VGSLX", "USRT", "RWR"},
    "cash": {"BIL", "SGOV", "SHV", "USFR", "TFLO", "VMFXX", "SPAXX", "SWVXX"},
}
KEYWORDS = {
    "bonds": ("BOND", "TREASURY", "AGGREGATE", "MUNICIPAL", "FIXED INCOME", "TIPS", "INCOME FUND"),
    "real_estate": ("REIT", "REAL ESTATE"),
    "cash": ("MONEY MARKET", "T-BILL", "TREASURY BILL"),
}

# Similar exposure, different index: common tax-loss-harvesting swaps. Whether two funds are
# "substantially identical" has never been defined by the IRS; this is not tax advice.
REPLACEMENTS = {
    "SPY": "VTI", "VOO": "VTI", "IVV": "VTI", "FXAIX": "FSKAX", "VFIAX": "VTSAX", "SWPPX": "SWTSX",
    "VTI": "ITOT", "ITOT": "SCHB", "SCHB": "VTI", "VTSAX": "FSKAX", "FSKAX": "VTSAX",
    "QQQ": "ONEQ", "VXUS": "IXUS", "IXUS": "VXUS", "VEA": "IEFA", "IEFA": "VEA", "VWO": "IEMG", "IEMG": "VWO",
    "BND": "AGG", "AGG": "BND", "VNQ": "SCHH", "SCHH": "VNQ",
}


# ------------------------------------------------------------------------------ goal planner


def monthly_returns(closes: dict[str, pd.Series]) -> pd.DataFrame:
    """Month-end to month-end returns of each series, over the months they all cover."""
    frame = pd.DataFrame({k: s.resample("ME").last() for k, s in closes.items()}).dropna()
    return frame.pct_change().dropna()


def mix_returns(returns: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """A mix rebalanced every month; a class with no series (cash in the demo) earns nothing."""
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("the mix must hold something")
    out = pd.Series(0.0, index=returns.index)
    for k, w in weights.items():
        if w and k in returns:
            out += (w / total) * returns[k]
    return out


def history_stats(mix: pd.Series, start_value: float) -> dict[str, Any]:
    """The mix's worst stretch in its own history: what staying the course would have meant."""
    path = (1 + mix).cumprod()
    peak = path.cummax()
    dd = path / peak - 1
    trough = dd.idxmin()
    peak_date = path.loc[:trough].idxmax()
    after = path.loc[trough:]
    recovered = after[after >= path.loc[peak_date]]
    back = recovered.index[0] if len(recovered) else mix.index[-1]
    months_down = (back.year - peak_date.year) * 12 + back.month - peak_date.month
    worst_12 = float(((1 + mix).rolling(12).apply(np.prod, raw=True) - 1).min()) if len(mix) >= 12 else math.nan
    return {
        "from": mix.index[0].strftime("%Y-%m"),
        "to": mix.index[-1].strftime("%Y-%m"),
        "max_drawdown": float(dd.min()),
        "peak": peak_date.strftime("%Y-%m"),
        "trough": trough.strftime("%Y-%m"),
        "recovered": recovered.index[0].strftime("%Y-%m") if len(recovered) else None,
        "months_below_peak": months_down,
        "worst_12_months": worst_12,
        "start_value_at_trough": start_value * (1 + float(dd.min())),
        "annual_return": float((1 + mix).prod() ** (12 / len(mix)) - 1),
    }


def goal_paths(
    mix: pd.Series,
    start_value: float,
    monthly: float,
    years: int,
    goal: float,
    inflation: float = 0.025,
    n: int = 2000,
    block: int = 12,
    seed: int = 3,
) -> dict[str, Any]:
    """Percentiles of the balance, year by year, in today's dollars, and the chance of reaching ``goal``.

    Each path strings together random 12-month stretches of the mix's history (wrapping
    around at the end), so good and bad years keep their real lengths and order within a
    year. Returns are deflated by ``inflation``; contributions stay constant in today's dollars.
    """
    if years < 1 or years > 60:
        raise ValueError("years must be between 1 and 60")
    r = ((1 + mix.to_numpy(float)) / (1 + inflation) ** (1 / 12)) - 1  # real monthly returns
    T, months = len(r), years * 12
    if T < 2 * block:
        raise ValueError("not enough history for a simulation: at least two years of monthly returns are needed")
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, T, size=(n, -(-months // block)))
    idx = ((starts[:, :, None] + np.arange(block)) % T).reshape(n, -1)[:, :months]
    draws = r[idx]
    balance = np.full(n, float(start_value))
    yearly = [np.percentile(balance, (10, 50, 90))]
    for m in range(months):
        balance = balance * (1 + draws[:, m]) + monthly
        if (m + 1) % 12 == 0:
            yearly.append(np.percentile(balance, (10, 50, 90)))
    bands = np.array(yearly)
    return {
        "years": list(range(years + 1)),
        "p10": bands[:, 0].tolist(),
        "p50": bands[:, 1].tolist(),
        "p90": bands[:, 2].tolist(),
        "p_goal": float(np.mean(balance >= goal)) if goal > 0 else None,
        "contributed": float(start_value + monthly * months),
        "paths": n,
        "inflation": inflation,
    }


# ------------------------------------------------------------------------- cost and tax drag


def drag(
    amount: float,
    years: int,
    gross_return: float,
    expense_ratio: float = 0.0,
    advisory_fee: float = 0.0,
    turnover: float = 0.0,
    trade_cost_bps: float = 0.0,
    short_rate: float = 0.0,
    long_rate: float = 0.0,
    taxable: bool = True,
) -> dict[str, Any]:
    """Final value with each drag added in turn: fees, then trading costs, then taxes.

    ``turnover`` is the share of the portfolio sold and replaced each year (1.0 = 100%);
    each turn pays ``trade_cost_bps`` on the sale and again on the purchase. Selling a share
    ``f = min(turnover, 1)`` realises that share of the unrealised gain, taxed at the
    short-term rate when the average holding is under a year (turnover above 100%) and the
    long-term rate otherwise. Losses are not harvested. The last row also sells everything
    at the end and pays long-term tax on the gain still unrealised.
    """
    if years < 1 or years > 60:
        raise ValueError("years must be between 1 and 60")
    fees = expense_ratio + advisory_fee
    trading = 2 * turnover * trade_cost_bps / 1e4
    rate = (short_rate if turnover > 1 else long_rate) if taxable else 0.0
    f = min(turnover, 1.0)

    def grow(r: float, tax: bool) -> tuple[float, float]:
        value, basis = amount, amount
        for _ in range(years):
            value *= 1 + r
            if tax:
                realised = f * max(value - basis, 0.0)
                basis += realised
                value -= realised * rate
                basis -= realised * rate
        return value, basis

    rows = [("No costs or taxes", grow(gross_return, False)[0])]
    rows.append(("After fund and advisory fees", grow(gross_return - fees, False)[0]))
    rows.append(("After trading costs", grow(gross_return - fees - trading, False)[0]))
    taxed, basis = grow(gross_return - fees - trading, taxable)
    if taxable:
        rows.append(("After taxes as you go", taxed))
        rows.append(("After tax if sold at the end", taxed - max(taxed - basis, 0.0) * long_rate))
    out = [{"label": label, "value": v, "cost": rows[0][1] - v} for label, v in rows]
    return {"rows": out, "final": out[-1]["value"], "lost": out[-1]["cost"], "lost_share": out[-1]["cost"] / rows[0][1]}


# ------------------------------------------------------------------- allocation and location


def asset_class(symbol: str, description: str = "") -> tuple[str, bool]:
    """(class, guessed): from the known funds, then the description, else stocks (guessed)."""
    for cls, symbols in KNOWN.items():
        if symbol in symbols:
            return cls, False
    words = description.upper()
    for cls, keys in KEYWORDS.items():
        if any(k in words for k in keys):
            return cls, False
    return "stocks", True


def allocation(
    holdings: list[dict[str, Any]],
    account_types: dict[str, str],
    targets: dict[str, float],
    band: float = 0.05,
    short_rate: float = 0.22,
    long_rate: float = 0.15,
) -> dict[str, Any]:
    """Holdings by asset class against ``targets``, the moves to get back within ``band``,
    and asset-location suggestions."""
    total = sum(h.get("value") or 0.0 for h in holdings)
    if total <= 0:
        raise ValueError("import holdings with market values first")
    by = {c: {"taxable": 0.0, "tax_advantaged": 0.0} for c in CLASSES}
    guessed = []
    for h in holdings:
        cls, guess = ("cash", False) if h.get("cash") else asset_class(h["symbol"], h.get("description", ""))
        kind = account_types.get(h["account"], account_type(h["account"]))
        by[cls][kind] += h.get("value") or 0.0
        if guess:
            guessed.append(h["symbol"])
    want = sum(targets.get(c, 0.0) for c in CLASSES)
    if want <= 0:
        raise ValueError("the targets must add up to more than zero")
    rows, moves = [], []
    for c in CLASSES:
        have = by[c]["taxable"] + by[c]["tax_advantaged"]
        target = targets.get(c, 0.0) / want
        diff = target * total - have
        rows.append({"class": c, "value": have, "share": have / total, "target": target, "difference": diff})
        if abs(have / total - target) > band:
            moves.append({"class": c, "amount": diff})
    return {
        "total": total,
        "rows": rows,
        "moves": _where(moves, by),
        "location": _location(by, short_rate, long_rate),
        "guessed": sorted(set(guessed)),
        "band": band,
    }


def _where(moves: list[dict[str, Any]], by: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    """Sell first inside tax-advantaged accounts, where selling costs no tax."""
    out = []
    for m in moves:
        if m["amount"] < 0 and m["class"] == "cash":
            out.append({**m, "action": "reduce", "where": "by buying what is below its target (spending cash costs no tax)"})
        elif m["amount"] < 0:
            inside = by[m["class"]]["tax_advantaged"]
            where = (
                "inside your tax-advantaged accounts, where selling costs no tax"
                if inside >= -m["amount"]
                else "first inside tax-advantaged accounts; any sale in a taxable account may realise gains"
            )
            out.append({**m, "action": "reduce", "where": where})
        else:
            out.append({**m, "action": "add", "where": "with new contributions or the proceeds of the reductions"})
    return out


BOND_YIELD = 0.04  # rough assumptions for the location estimate
STOCK_DIVIDEND = 0.015


def _location(by: dict[str, dict[str, float]], short_rate: float, long_rate: float) -> list[str]:
    """Bonds and REITs pay income taxed every year at full rates; broad stock funds mostly defer."""
    notes = []
    income_taxable = by["bonds"]["taxable"] + by["real_estate"]["taxable"]
    stocks_sheltered = by["stocks"]["tax_advantaged"]
    swap = min(income_taxable, stocks_sheltered)
    if swap > 1000:
        saving = swap * (BOND_YIELD * short_rate - STOCK_DIVIDEND * long_rate)
        notes.append(
            f"About ${income_taxable:,.0f} of bonds or real estate sits in taxable accounts while tax-advantaged accounts "
            f"hold ${stocks_sheltered:,.0f} of stocks. Holding the income-paying funds in the tax-advantaged accounts and "
            f"stocks in the taxable ones could save roughly ${saving:,.0f} a year in tax (assuming a {BOND_YIELD:.0%} "
            f"yield taxed as income against a {STOCK_DIVIDEND:.1%} qualified dividend). Selling in a taxable account "
            "to do it can realise gains, so new contributions are usually the cheaper way."
        )
    if not notes:
        notes.append("Nothing obvious to move: income-paying funds are not crowding taxable accounts.")
    return notes


# ------------------------------------------------------------------------ tax-loss harvesting


def harvest(
    holdings: list[dict[str, Any]],
    account_types: dict[str, str],
    short_rate: float,
    long_rate: float,
    min_loss: float = 200.0,
) -> list[dict[str, Any]]:
    """Taxable positions at least ``min_loss`` below cost: the tax a sale could defer, a
    similar fund to stay invested with, and wash-sale warnings."""

    def kind(h):
        return account_types.get(h["account"], account_type(h["account"]))

    sheltered = {h["symbol"]: h["account"] for h in holdings if kind(h) == "tax_advantaged" and not h.get("cash")}
    out = []
    for h in holdings:
        if h.get("cash") or kind(h) != "taxable" or h.get("value") is None or h.get("cost_basis") is None:
            continue
        loss = h["cost_basis"] - h["value"]
        if loss < min_loss:
            continue
        warnings = [
            f"Do not buy {h['symbol']} (or a substantially identical fund) in any account, IRAs included, from 30 days "
            "before to 30 days after the sale, and turn off dividend reinvestment for it: the loss would be disallowed."
        ]
        if h["symbol"] in sheltered:
            warnings.append(
                f"You also hold {h['symbol']} in {sheltered[h['symbol']]}: a purchase there, a reinvested dividend included, "
                "within the window disallows the loss permanently."
            )
        out.append(
            {
                "account": h["account"],
                "symbol": h["symbol"],
                "value": h["value"],
                "loss": loss,
                "loss_share": loss / h["cost_basis"] if h["cost_basis"] else None,
                "tax_deferred": [loss * long_rate, loss * short_rate],
                "replacement": REPLACEMENTS.get(h["symbol"]),
                "warnings": warnings,
            }
        )
    return sorted(out, key=lambda x: -x["loss"])
