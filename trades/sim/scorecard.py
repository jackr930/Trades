"""End-of-session scorecard: outcome *and* process.

Good decisions can lose money and bad ones can win, so the scorecard separates the
outcome (returns vs. benchmark and the strategy "ghosts") from the process (risk
control, sizing, discipline), and flags well-documented behavioural biases.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from trades.backtest.metrics import performance_metrics
from trades.core import indicators as ind
from trades.strategies.base import Reference

if TYPE_CHECKING:
    from trades.sim.session import SimSession

SHEFRIN_STATMAN = Reference(
    "Shefrin, H. & Statman, M.",
    1985,
    "The Disposition to Sell Winners Too Early and Ride Losers Too Long: Theory and Evidence",
    "Journal of Finance 40(3), 777-790",
    "https://doi.org/10.1111/j.1540-6261.1985.tb05002.x",
)
ODEAN_1998 = Reference(
    "Odean, T.",
    1998,
    "Are Investors Reluctant to Realize Their Losses?",
    "Journal of Finance 53(5), 1775-1798",
    "https://doi.org/10.1111/0022-1082.00072",
)
BARBER_ODEAN = Reference(
    "Barber, B. M. & Odean, T.",
    2000,
    "Trading Is Hazardous to Your Wealth",
    "Journal of Finance 55(2), 773-806",
    "https://doi.org/10.1111/0022-1082.00226",
)
KAHNEMAN_TVERSKY = Reference(
    "Kahneman, D. & Tversky, A.",
    1979,
    "Prospect Theory: An Analysis of Decision under Risk",
    "Econometrica 47(2), 263-291",
    "https://doi.org/10.2307/1914185",
)
KELLY_1956 = Reference(
    "Kelly, J. L.",
    1956,
    "A New Interpretation of Information Rate",
    "Bell System Technical Journal 35(4), 917-926",
    "https://doi.org/10.1002/j.1538-7305.1956.tb03809.x",
)
DE_BONDT_THALER = Reference(
    "De Bondt, W. F. M. & Thaler, R.",
    1985,
    "Does the Stock Market Overreact?",
    "Journal of Finance 40(3), 793-805",
    "https://doi.org/10.1111/j.1540-6261.1985.tb05004.x",
)
FAITH_2007 = Reference("Faith, C. M.", 2007, "Way of the Turtle", "McGraw-Hill")
DUKE_2018 = Reference(
    "Duke, A.",
    2018,
    "Thinking in Bets: Making Smarter Decisions When You Don't Have All the Facts",
    "Portfolio/Penguin",
)

WEIGHTS = {"stops": 25, "sizing": 25, "loss_control": 20, "disposition": 15, "overtrading": 10, "journal": 5}
POINTS = {"good": 1.0, "warn": 0.5, "bad": 0.0}


def _diag(
    id_: str, title: str, status: str, value: str, detail: str, lesson: str, ref: Reference | None = None
) -> dict[str, Any]:
    return {
        "id": id_,
        "title": title,
        "status": status,
        "value": value,
        "detail": detail,
        "lesson": lesson,
        "reference": ref.to_dict() if ref else None,
    }


def build_scorecard(sess: SimSession) -> dict[str, Any]:
    s, e = sess.start_index, sess.cursor
    idx = sess.bars.index[s : e + 1]
    ppy = 252
    led = sess.broker.ledger
    trades = led.all_trades()
    closed = [t for t in trades if not t.is_open]
    you_eq = pd.Series(sess.equity, index=idx)
    you = performance_metrics(you_eq, ppy, trades, led.fills, pd.Series(sess.exposure, index=idx))
    bench_eq = sess.benchmark.equity.iloc[s : e + 1]
    bench = performance_metrics(bench_eq, ppy)
    board = [
        {
            "name": "You",
            "kind": "you",
            "total_return": you.get("total_return"),
            "max_drawdown": you.get("max_drawdown"),
            "sharpe": you.get("sharpe"),
            "trades": you.get("n_trades"),
        },
        {
            "name": "Buy & hold",
            "kind": "benchmark",
            "total_return": bench.get("total_return"),
            "max_drawdown": bench.get("max_drawdown"),
            "sharpe": bench.get("sharpe"),
            "trades": 1,
        },
    ]
    advisor_trades = []
    for a in sess.advisors:
        eq = a["result"].equity.iloc[s : e + 1]
        in_range = [t for t in a["result"].trades if t.entry_index <= e]
        m = performance_metrics(eq, ppy, [t for t in in_range if not t.is_open or t.entry_index <= e])
        n_tr = sum(1 for t in in_range if t.exit_index is not None and t.exit_index <= e)
        advisor_trades.append(n_tr)
        board.append(
            {
                "name": a["strategy"].name,
                "kind": "strategy",
                "total_return": m.get("total_return"),
                "max_drawdown": m.get("max_drawdown"),
                "sharpe": m.get("sharpe"),
                "trades": n_tr,
            }
        )
    board.sort(key=lambda r: -(r["total_return"] if r["total_return"] is not None else -math.inf))
    rank = next(i for i, r in enumerate(board) if r["kind"] == "you") + 1

    diags = behaviour_diagnostics(sess, closed, trades, you, bench, advisor_trades)
    scored = [
        (WEIGHTS[d["id"]], POINTS[d["status"]]) for d in diags if d["id"] in WEIGHTS and d["status"] in POINTS
    ]
    process = round(100 * sum(w * p for w, p in scored) / sum(w for w, _ in scored)) if scored else None
    excess = (you.get("total_return") or 0.0) - (bench.get("total_return") or 0.0)
    return {
        "you": you,
        "benchmark": bench,
        "leaderboard": board,
        "rank": rank,
        "of": len(board),
        "excess_return": excess,
        "process_score": process,
        "verdict": verdict(process, excess, len(closed)),
        "diagnostics": diags,
        "bars": e - s,
    }


def verdict(process: int | None, excess: float, n_closed: int) -> str:
    if n_closed == 0 and process is None:
        return (
            "You did not complete any trades. Staying out is a valid choice -- compare your result "
            "with buy-and-hold to see what patience would have earned."
        )
    good_process = process is not None and process >= 70
    good_outcome = excess >= 0
    if good_process and good_outcome:
        return "Good process and a good outcome. Repeat it on different markets to check it wasn't luck."
    if good_process:
        return (
            "Good process, disappointing outcome. Over one session luck dominates; a disciplined process "
            "is what pays off over many repetitions (Duke 2018: don't 'result')."
        )
    if good_outcome:
        return (
            "You beat the benchmark, but with risky habits. A win with poor risk control is dangerous "
            "because it reinforces the habits that eventually cause large losses."
        )
    return "Both process and outcome need work: focus on the red items below, one at a time."


def stop_protected(trade, orders: dict[str, Any], cursor: int, grace: int = 2, need: float = 0.8) -> bool:
    """Did a working stop order on the exit side protect ``trade`` for (almost) its whole life?

    A stop "works" during bar b if it was placed before b (orders placed at a close act from
    the next bar) and was not yet filled, canceled, expired or rejected. Bracket stops only
    work once their entry has filled. The first ``grace`` bars after entry are excused, so a
    stop placed up to one bar after the fill still counts. A stop that actually fired
    protected the trade by definition.
    """
    exit_side = "sell" if trade.direction == "long" else "buy"

    def armed(o) -> bool:  # bracket children only work once their entry has filled
        parent = orders.get(o.parent_id) if o.parent_id else None
        return o.parent_id is None or (parent is not None and parent.status.value == "filled")

    stops = [
        o
        for o in orders.values()
        if o.symbol == trade.symbol
        and o.type.value == "stop"
        and o.side.value == exit_side
        and o.status.value != "pending"
        and armed(o)
    ]
    if trade.exit_index is not None and any(
        o.status.value == "filled" and o.filled_index == trade.exit_index for o in stops
    ):
        return True
    e = trade.entry_index
    x = trade.exit_index if trade.exit_index is not None else cursor + 1
    held = list(range(e, max(x, e + 1)))
    needed = [b for b in held if b >= e + grace] or held[1:] or held

    def end(o) -> float:
        if o.status.value == "filled":
            return o.filled_index
        if o.status.value == "open":
            return math.inf
        return o.closed_index if o.closed_index is not None else -math.inf

    covered = sum(1 for b in needed if any(o.created_index < b <= end(o) for o in stops))
    return covered / len(needed) >= need


def behaviour_diagnostics(sess, closed, trades, you, bench, advisor_trades) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    bars = sess.bars
    atr = ind.atr(bars["high"], bars["low"], bars["close"], 20).to_numpy(float)
    closes = bars["close"].to_numpy(float)
    rets = pd.Series(closes).pct_change().to_numpy()
    sd20 = pd.Series(rets).rolling(20).std().to_numpy()
    n_bars = max(sess.cursor - sess.start_index, 1)

    # 1. Stop-loss usage -------------------------------------------------------------
    orders = sess.broker.orders
    protected = sum(1 for t in trades if stop_protected(t, orders, sess.cursor))
    if trades:
        frac = protected / len(trades)
        status = "good" if frac >= 0.8 else "warn" if frac >= 0.4 else "bad"
        out.append(
            _diag(
                "stops",
                "Protective stops",
                status,
                f"{frac:.0%} of trades",
                f"{protected} of {len(trades)} trades had a working stop-loss order for (almost) their "
                "whole life (placing it up to one bar after entry is fine).",
                "Decide where you are wrong *before* entering and place the stop. It turns an open-ended "
                "risk into a known, small one (the Turtles never traded without a 2-ATR stop).",
                FAITH_2007,
            )
        )
    else:
        out.append(
            _diag(
                "stops",
                "Protective stops",
                "info",
                "no trades",
                "You did not trade.",
                "When you do trade, place a stop-loss with every entry.",
                FAITH_2007,
            )
        )

    # 2. Position sizing: implied risk per trade at a 2-ATR stop --------------------------
    risks = []
    for f in sess.broker.ledger.fills:
        if f.tag not in ("entry",):
            continue
        i = f.index
        eq = sess.equity[max(0, min(i - sess.start_index, len(sess.equity) - 1))]
        a = atr[i - 1] if i - 1 >= 0 and np.isfinite(atr[i - 1]) else np.nan
        if eq > 0 and np.isfinite(a):
            risks.append(abs(f.qty) * 2 * a / eq)
    if risks:
        worst = max(risks)
        status = "good" if worst <= 0.02 else "warn" if worst <= 0.05 else "bad"
        out.append(
            _diag(
                "sizing",
                "Position sizing",
                status,
                f"max {worst:.1%} risk",
                f"At a 2-ATR stop, your largest entry risked {worst:.1%} of equity (average {np.mean(risks):.1%}).",
                "Professionals typically risk 0.5-2% of equity per trade. Betting more than the optimal (Kelly) "
                "fraction lowers long-run growth and raises the odds of ruin, even with a real edge.",
                KELLY_1956,
            )
        )
    else:
        out.append(
            _diag("sizing", "Position sizing", "info", "n/a", "No entries to evaluate.", "", KELLY_1956)
        )

    # 3. Loss control in units of initial risk (R = 2 ATR at entry) -----------------------------
    r_multiples = []
    for t in closed:
        i = t.entry_index
        a = atr[i - 1] if i - 1 >= 0 and np.isfinite(atr[i - 1]) else np.nan
        if np.isfinite(a) and a > 0 and t.pnl < 0:
            r_multiples.append(
                (t.exit_price - t.entry_price) * (1 if t.direction == "long" else -1) / (2 * a)
            )
    if r_multiples:
        worst_r = min(r_multiples)
        big = sum(r < -1.5 for r in r_multiples)
        status = "good" if worst_r >= -1.25 else "warn" if worst_r >= -2 else "bad"
        out.append(
            _diag(
                "loss_control",
                "Cutting losses",
                status,
                f"worst {worst_r:.1f}R",
                f"Your worst loss was {abs(worst_r):.1f}x a 2-ATR risk unit; {big} loss(es) exceeded 1.5R.",
                "Losses hurt about twice as much as equal gains feel good (loss aversion), which tempts us to "
                "hold losers hoping to break even. Pre-committed stops keep every loss near 1R.",
                KAHNEMAN_TVERSKY,
            )
        )
    else:
        out.append(
            _diag(
                "loss_control",
                "Cutting losses",
                "info",
                "no losing trades",
                "No closed losing trades to evaluate.",
                "",
                KAHNEMAN_TVERSKY,
            )
        )

    # 4. Disposition effect ----------------------------------------------------------------
    # Holding periods in bars, counting the entry bar (a same-bar round trip held for 1 bar).
    winners = [t.bars_held + 1 for t in closed if t.pnl > 0]
    losers = [t.bars_held + 1 for t in closed if t.pnl < 0]
    if len(winners) >= 2 and len(losers) >= 2:
        ratio = float(np.mean(losers) / np.mean(winners))
        status = "good" if ratio <= 1.1 else "warn" if ratio <= 1.5 else "bad"
        out.append(
            _diag(
                "disposition",
                "Disposition effect",
                status,
                f"losers held {ratio:.1f}x longer",
                f"Average holding: winners {np.mean(winners):.1f} bars, losers {np.mean(losers):.1f} bars.",
                "Investors tend to sell winners too early and ride losers too long. The fix is rules-based exits "
                "that treat gains and losses symmetrically.",
                SHEFRIN_STATMAN,
            )
        )
    else:
        out.append(
            _diag(
                "disposition",
                "Disposition effect",
                "info",
                "too few trades",
                "Needs at least two winning and two losing closed trades.",
                "",
                ODEAN_1998,
            )
        )

    # 5. Over-trading and costs --------------------------------------------------------------
    n_tr = len(closed)
    per100 = n_tr / n_bars * 100
    costs = sess.broker.ledger.total_commission + sess.broker.ledger.total_slippage
    cost_pct = costs / sess.config.initial_cash
    avg_adv = float(np.mean(advisor_trades)) if advisor_trades else None
    status = "good"
    if cost_pct > 0.02 or (avg_adv is not None and n_tr > max(3 * avg_adv, 10)):
        status = "bad" if cost_pct > 0.03 else "warn"
    out.append(
        _diag(
            "overtrading",
            "Trading frequency",
            status,
            f"{per100:.1f} trades / 100 bars",
            f"{n_tr} round trip{'' if n_tr == 1 else 's'}; costs ${costs:,.0f} ({cost_pct:.2%} of starting equity)"
            + (f"; the strategies averaged {avg_adv:.1f} trades." if avg_adv is not None else "."),
            "The most active individual traders underperformed by ~6.5 points a year, mostly from costs. "
            "Trade when your plan says so, not because the market moved.",
            BARBER_ODEAN,
        )
    )

    # 6. Chasing: buying right after a big up bar ----------------------------------------------------
    # Judge each order on the bar it was placed, against the volatility of the 20 bars before it.
    def placed_at(f) -> int:
        o = orders.get(f.order_id) if f.order_id else None
        return o.created_index if o is not None else f.index - 1

    def big_move(k: int, threshold: float) -> bool:
        if k < 1 or k >= len(rets) or not np.isfinite(sd20[k - 1]) or sd20[k - 1] <= 0:
            return False
        return bool(np.sign(threshold) * rets[k] > abs(threshold) * sd20[k - 1])

    entries = [f for f in sess.broker.ledger.fills if f.tag == "entry" and f.qty > 0]
    chased = sum(1 for f in entries if big_move(placed_at(f), 1.5))
    if entries:
        frac = chased / len(entries)
        out.append(
            _diag(
                "chasing",
                "Chasing big moves",
                "warn" if frac > 0.6 else "info",
                f"{frac:.0%} of buys",
                f"{chased} of {len(entries)} buys came right after an unusually strong up bar (> 1.5 standard deviations).",
                "Breakout strategies buy strength on purpose, but impulsive buying after sharp rallies (FOMO) "
                "tends to buy short-term over-reaction. Was each one part of your plan?",
                DE_BONDT_THALER,
            )
        )

    # 7. Panic selling: selling right after a sharp drop that later recovered ------------------------
    # Only discretionary sells count: stop-loss and take-profit legs were planned in advance.
    exits = [f for f in sess.broker.ledger.fills if f.qty < 0 and f.tag == "exit"]
    panic = recovered = 0
    for f in exits:
        if big_move(placed_at(f), -2.0):
            panic += 1
            later = min(f.index + 10, sess.cursor)
            if closes[later] > f.price:
                recovered += 1
    if panic:
        out.append(
            _diag(
                "panic",
                "Selling into panic",
                "warn" if recovered >= 2 else "info",
                f"{panic} panic sale(s)",
                f"You sold {panic} time(s) right after a sharp drop; {recovered} time(s) the price was higher 10 bars later.",
                "Sharp drops trigger loss aversion. Exits planned in advance (a stop level) beat exits decided in "
                "the heat of the moment.",
                KAHNEMAN_TVERSKY,
            )
        )

    # 8. Time in the market -------------------------------------------------------------------------
    exposure = you.get("exposure") or 0.0
    b_ret = bench.get("total_return") or 0.0
    status = "warn" if exposure < 0.3 and b_ret > 0.1 else "info"
    out.append(
        _diag(
            "exposure",
            "Time in the market",
            status,
            f"{exposure:.0%} of bars",
            f"You held a position {exposure:.0%} of the time while buy-and-hold returned {b_ret:+.1%}.",
            "Missing a handful of the best days drastically cuts long-run returns; sitting out has an "
            "opportunity cost in rising markets.",
            None,
        )
    )

    # 9. Journal ---------------------------------------------------------------------------------------
    entry_decisions = [d for d in sess.decisions if d["tag"] == "entry"]
    if entry_decisions:
        noted = sum(1 for d in entry_decisions if d["note"]) + min(len(sess.journal), len(entry_decisions))
        frac = min(noted / len(entry_decisions), 1.0)
        status = "good" if frac >= 0.8 else "warn" if frac >= 0.3 else "bad"
        out.append(
            _diag(
                "journal",
                "Trade journal",
                status,
                f"{frac:.0%} documented",
                "Share of entries where you wrote down your reasoning.",
                "Writing the reason for a trade before you know the outcome is the only way to later tell skill "
                "from luck -- judge decisions by their quality, not their results.",
                DUKE_2018,
            )
        )

    # 10. Agreement with the strategy consensus ---------------------------------------------------------
    with_c = [d for d in entry_decisions if d["consensus"] is not None]
    if with_c:
        agree = sum(
            1
            for d in with_c
            if (d["consensus"] > 0 and d["side"] == "buy") or (d["consensus"] < 0 and d["side"] == "sell")
        )
        out.append(
            _diag(
                "agreement",
                "Agreement with the strategies",
                "info",
                f"{agree}/{len(with_c)} entries",
                f"{agree} of your {len(with_c)} entries went in the same direction as the majority of the strategy "
                "advisors at that moment.",
                "Disagreeing is fine -- but know why. Compare how your independent trades fared against the "
                "ones that followed the systems.",
                None,
            )
        )
    return out
