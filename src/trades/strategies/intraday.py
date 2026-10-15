"""Intraday strategies from the playbook: opening range breakouts and market intraday momentum.

All three are flat at every session's close (``intraday = True``): the simulated broker closes
what is left in the closing auction, and nothing is held overnight. Their published results are
gross of most trading costs, so the specs in ``specs/`` model spreads, slippage and fees, and the
acceptance checklist reruns them at twice those costs.

Each opening range breakout trades at most once a day with a bracket order: an entry, a
protective stop and (optionally) a take-profit, priced when the order is placed. The strategy
simulates its own trade with the broker's fill rules (``bracket_path``), so its signal goes flat
at the close of the bar in which the broker would have exited.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from trades.features.intraday import (
    infer_bar_minutes,
    opening_range,
    prior_day,
    relative_opening_volume,
    session_info,
)
from trades.strategies.base import (
    Evidence,
    Kind,
    Overlay,
    Param,
    Reference,
    Rule,
    SingleAssetStrategy,
    Strategy,
    StrategyOutput,
    fmt_num,
    fmt_pct,
)

ZA_2023 = Reference(
    "Zarattini, C. & Aziz, A.",
    2023,
    "Can Day Trading Really Be Profitable? Evidence of Sustainable Long-term Profits from Opening "
    "Range Breakout (ORB) Day Trading Strategy vs. Benchmark in the US Stock Market",
    "SSRN Working Paper 4416622",
    "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622",
)
ZBA_2024 = Reference(
    "Zarattini, C., Barbon, A. & Aziz, A.",
    2024,
    "A Profitable Day Trading Strategy for the U.S. Equity Market",
    "SSRN Working Paper 4729284",
    "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284",
)
KRUEGER_2026 = Reference(
    "Krueger, T.",
    2026,
    "The opening-range breakout paper, replicated on five indices: gross reproduced, net zero",
    "MQL5 Traders' Blogs (practitioner replication, not peer reviewed)",
    "https://www.mql5.com/en/blogs/post/776235",
)
GHLZ_2018 = Reference(
    "Gao, L., Han, Y., Li, S. Z. & Zhou, G.",
    2018,
    "Market Intraday Momentum",
    "Journal of Financial Economics 129(2), 394-414",
)
VOL_RANK_DAYS = 80  # 20 daily returns, then 60 of their values to rank against (see features.intraday.prior_day)
VOLATILITY_FILTER = Param(
    "min_vol_rank",
    "Volatility regime filter",
    0.0,
    "float",
    0.0,
    1.0,
    0.05,
    help="Trade only on days when the last 20 days' realised volatility ranks at least this high "
    "(0 to 1) among the past year's. 0 turns the filter off.",
)


def _bars_per_day(index: pd.DatetimeIndex | None, default: int = 78) -> int:
    bm = infer_bar_minutes(index) if index is not None and len(index) > 1 else None
    return -(-390 // bm) if bm else default


def require_intraday(name: str, index: pd.DatetimeIndex) -> int:
    bm = infer_bar_minutes(index)
    if bm is None:
        raise ValueError(f"{name} trades within the day: give it intraday bars (e.g. 5 minutes), not daily bars.")
    return bm


# --------------------------------------------------------------------------------------
# One bracket trade a day, simulated with the broker's rules
# --------------------------------------------------------------------------------------


def _exit_hit(o: float, h: float, lo: float, direction: float, stop: float, take: float, whole_bar: bool) -> bool:
    """Would the protective stop or take-profit have filled during this bar? ``whole_bar``: the
    position was held from the open (False when a stop entry filled inside the bar, in which case
    only the stop counts, as in ``trades.execution.sim_broker``)."""
    long = direction > 0
    if math.isfinite(stop) and (lo <= stop if long else h >= stop):
        return True
    if not whole_bar or not math.isfinite(take):
        return False
    return (o >= take or h > take) if long else (o <= take or lo < take)


def bracket_path(
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    decision: np.ndarray,
    direction: np.ndarray,
    entry_level: np.ndarray,
    stop: np.ndarray,
    take: np.ndarray,
    first: np.ndarray,
    last: np.ndarray,
) -> dict[str, np.ndarray]:
    """Signal and order levels for at most one trade per session.

    At a ``decision`` bar with a non-zero ``direction`` the trade starts: a market entry at the
    next open when ``entry_level`` is NaN, else a stop entry at that level, waiting until the price
    reaches it. It ends at its stop, its take-profit or the session's close (``last``); a new
    session (``first``) always starts flat, even if the previous one is missing its last bars.
    ``state`` per bar: 0 no trade, 1 waiting for the stop entry, 2 in the trade (or entering at the
    next open), 3 finished for the day."""
    T = len(o)
    signal = np.zeros(T)
    stop_out = np.full(T, np.nan)
    take_out = np.full(T, np.nan)
    entry_out = np.full(T, np.nan)
    state_out = np.zeros(T, dtype=int)
    state, d, lvl, s_lvl, t_lvl = 0, 0.0, math.nan, math.nan, math.nan
    for t in range(T):
        if first[t]:
            state = 0
        if state == 1:  # a stop entry is working during this bar
            reached_at_open = o[t] >= lvl if d > 0 else o[t] <= lvl
            reached = reached_at_open or (h[t] >= lvl if d > 0 else lo[t] <= lvl)
            if reached:
                state = 3 if _exit_hit(o[t], h[t], lo[t], d, s_lvl, t_lvl, whole_bar=reached_at_open) else 2
        elif state == 2 and _exit_hit(o[t], h[t], lo[t], d, s_lvl, t_lvl, whole_bar=True):
            state = 3
        if state == 0 and decision[t] and direction[t] != 0 and not last[t]:
            d = float(direction[t])
            lvl, s_lvl, t_lvl = float(entry_level[t]), float(stop[t]), float(take[t])
            state = 1 if math.isfinite(lvl) else 2
        if state in (1, 2) and not last[t]:
            signal[t] = d
            stop_out[t], take_out[t] = s_lvl, t_lvl
            if state == 1:
                entry_out[t] = lvl
        state_out[t] = state
        if last[t]:
            state = 0
    return {"signal": signal, "stop_loss": stop_out, "take_profit": take_out, "entry_stop": entry_out, "state": state_out}


def _orb_levels(
    direction: np.ndarray,
    or_high: np.ndarray,
    or_low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
    *,
    breakout: bool,
    stop_mode: str,
    stop_atr_frac: float,
    target_r: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Entry level (NaN = market entry at the next open), stop and take-profit for each bar."""
    long = direction > 0
    entry = np.where(long, or_high, or_low) if breakout else np.full(len(close), np.nan)
    ref = np.where(np.isfinite(entry), entry, close)  # the price the bracket is priced from
    if stop_mode == "atr":
        stop = np.where(long, ref - stop_atr_frac * atr, ref + stop_atr_frac * atr)
    else:
        stop = np.where(long, or_low, or_high)
    risk = np.abs(ref - stop)
    take = np.where(long, ref + target_r * risk, ref - target_r * risk) if target_r > 0 else np.full(len(close), np.nan)
    bad = ~np.isfinite(stop) | (risk <= 0)
    stop = np.where(bad, np.nan, stop)
    return entry, stop, np.where(bad, np.nan, take)


# --------------------------------------------------------------------------------------
# Opening range breakout on one or more symbols (Zarattini & Aziz 2023)
# --------------------------------------------------------------------------------------


class OpeningRangeBreakout(SingleAssetStrategy):
    id = "orb"
    name = "Opening range breakout"
    category = "Intraday breakout"
    family = "intraday"
    intraday = True
    summary = (
        "Trade in the direction of the first five minutes: enter at the next bar, stop at the other "
        "side of the opening range, take profit at 10 times the risk, and be flat by the close."
    )
    rules_text = (
        "First 5-minute bar up -> buy at the open of the next bar; down -> sell short; unchanged -> no trade",
        "Protective stop at the opening range's low (long) or high (short)",
        "Take profit at 10x the entry-to-stop distance; otherwise exit at the close",
        "Size so a stop-out loses 1% of equity, with at most 4x leverage",
    )
    rationale = (
        "Order imbalances at the open (overnight news, institutional flows) can persist through the day, "
        "so the first minutes' direction carries information about the rest of the session."
    )
    failure_modes = (
        "The edge per trade is a fraction of a percent, so spreads and slippage decide it: an independent "
        "replication found the gross result but net results indistinguishable from zero. Low hit rate "
        "(~24%) means long losing streaks."
    )
    evidence = Evidence.EXPERIMENTAL
    evidence_text = (
        "Working paper by a day-trading educator, with no out-of-sample period; a practitioner replication on "
        "five index markets (2015-2026) reproduced the gross edge but found it net zero after costs."
    )
    references = (ZA_2023, KRUEGER_2026)
    needs = "Intraday bars (1 or 5 minutes) and realistic costs; a liquid ETF such as QQQ."
    params_spec = (
        Param("or_minutes", "Opening range (minutes)", 5, "int", 1, 60, 1, help="Length of the opening range."),
        Param("target_r", "Take profit (x risk)", 10.0, "float", 0.0, 50.0, 0.5, help="0 = no target, exit at the close."),
        Param(
            "entry",
            "Entry",
            "next_open",
            "choice",
            choices=("next_open", "breakout"),
            help="next_open: enter at the next bar in the first bar's direction (2023 paper). breakout: "
            "enter with a stop order when the price returns to the range's extreme (2024 paper).",
        ),
        Param("stop_mode", "Stop", "range", "choice", choices=("range", "atr"), help="range: the other side of the opening range; atr: a fraction of the daily ATR from the entry."),
        Param("stop_atr_frac", "Stop distance (x ATR)", 0.10, "float", 0.01, 2.0, 0.01),
        Param("atr_days", "ATR length (days)", 14, "int", 2, 100, 1),
        Param("min_rel_volume", "Minimum relative volume", 0.0, "float", 0.0, 20.0, 0.1, help="Opening-range volume vs its 14-day average; 0 = no filter."),
        Param("rel_volume_days", "Relative volume lookback (days)", 14, "int", 2, 100, 1),
        Param("allow_short", "Trade the short side", True, "bool"),
        VOLATILITY_FILTER,
    )
    default_sizing = {"method": "stop_risk", "risk_per_trade": 0.01, "max_leverage": 4.0}
    overlays = (
        Overlay("or_high", "Opening range high", style="dotted"),
        Overlay("or_low", "Opening range low", style="dotted"),
        Overlay("stop_loss", "Stop", style="dashed"),
        Overlay("take_profit", "Take profit", style="dashed"),
    )

    def __init__(self, **params: Any):
        super().__init__(**params)
        self._bars_per_day = 78

    def history_days(self) -> int:
        """Completed sessions needed before the first trade (the opening range itself needs none)."""
        p = self.params
        days = 0
        if p["stop_mode"] == "atr":
            days = max(days, p["atr_days"])
        if p["min_rel_volume"] > 0:
            days = max(days, p["rel_volume_days"])
        if p["min_vol_rank"] > 0:
            days = max(days, VOL_RANK_DAYS)
        return days

    def warmup(self) -> int:
        return self.history_days() * self._bars_per_day + 1

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        bm = require_intraday(self.name, df.index)
        self._bars_per_day = _bars_per_day(df.index)
        info = session_info(df.index, bm)
        rng = opening_range(df, p["or_minutes"], bm)
        prior = prior_day(df, atr_days=p["atr_days"])
        rel = (
            relative_opening_volume(df, p["or_minutes"], p["rel_volume_days"])
            if p["min_rel_volume"] > 0
            else pd.Series(np.nan, index=df.index)
        )
        move = (rng["or_close"] - rng["or_open"]).to_numpy(float)
        direction = np.sign(np.nan_to_num(move))
        if not p["allow_short"]:
            direction = np.where(direction < 0, 0.0, direction)
        ok = np.ones(len(df), dtype=bool)
        if p["min_rel_volume"] > 0:
            ok &= rel.to_numpy(float) >= p["min_rel_volume"]
        if p["min_vol_rank"] > 0:
            ok &= prior["vol_rank"].to_numpy(float) >= p["min_vol_rank"]
        direction = np.where(ok, direction, 0.0)
        entry, stop, take = _orb_levels(
            direction,
            rng["or_high"].to_numpy(float),
            rng["or_low"].to_numpy(float),
            df["close"].to_numpy(float),
            prior["atr"].to_numpy(float),
            breakout=p["entry"] == "breakout",
            stop_mode=p["stop_mode"],
            stop_atr_frac=p["stop_atr_frac"],
            target_r=p["target_r"],
        )
        decision = rng["decision"].to_numpy() & np.isfinite(stop)
        path = bracket_path(
            df["open"].to_numpy(float),
            df["high"].to_numpy(float),
            df["low"].to_numpy(float),
            decision,
            direction,
            entry,
            stop,
            take,
            info["first"].to_numpy(),
            info["last"].to_numpy(),
        )
        out = pd.DataFrame(path, index=df.index)
        out["or_high"] = rng["or_high"]
        out["or_low"] = rng["or_low"]
        out["or_move"] = move
        out["rel_volume"] = rel
        out["atr"] = prior["atr"]
        out["vol_rank"] = prior["vol_rank"]
        out["close"] = df["close"]
        return out

    def position_states(self, output: StrategyOutput, symbol: str) -> np.ndarray:
        states = super().position_states(output, symbol)
        diag = output.diagnostics.get(symbol)
        if diag is not None and "state" in diag:
            states[diag["state"].to_numpy() == 1] = "flat"  # waiting for the entry is not a position yet
        return states

    def describe_row(self, diag: pd.DataFrame, t: int) -> tuple[str, list[Rule]]:
        row = diag.iloc[t]
        state = int(row["state"])
        rules = [
            Rule("Opening range", f"{fmt_num(row['or_low'])} - {fmt_num(row['or_high'])}", None),
            Rule("First bars' move", fmt_num(row["or_move"]), None if not math.isfinite(row["or_move"]) else bool(row["or_move"] != 0)),
        ]
        if math.isfinite(row["stop_loss"]):
            rules.append(Rule("Stop", fmt_num(row["stop_loss"]), None))
        if math.isfinite(row["take_profit"]):
            rules.append(Rule("Take profit", fmt_num(row["take_profit"]), None))
        side = "long" if row["signal"] > 0 else "short"
        if state == 1:
            return f"Waiting to go {side}: a stop order at {fmt_num(row['entry_stop'])}.", rules
        if state == 2:
            return f"In a {side} breakout trade; out at the stop, the target or the close.", rules
        if state == 3:
            return "Today's trade is over (stop or target reached); flat until tomorrow's opening range.", rules
        if not math.isfinite(row["or_high"]):
            return "Waiting for the opening range to complete.", rules
        return "No trade today (the first bars were unchanged, or a filter said no).", rules

    def exit_rule(self, state: str) -> str:
        return "Exit at the stop, the take-profit or the close, whichever comes first." if state in ("long", "short") else ""


# --------------------------------------------------------------------------------------
# Opening range breakout on Stocks in Play (Zarattini, Barbon & Aziz 2024)
# --------------------------------------------------------------------------------------


class ORBStocksInPlay(Strategy):
    id = "orb_sip"
    name = "Opening range breakout on Stocks in Play"
    category = "Intraday breakout"
    family = "intraday"
    kind = Kind.CROSS_SECTIONAL
    intraday = True
    min_symbols = 2
    uses_short = True
    summary = (
        "Each morning, rank liquid stocks by how unusually busy their first five minutes were, keep the top "
        "20, and trade breakouts of their opening ranges with a tight ATR stop, flat by the close."
    )
    rules_text = (
        "Universe: price above $5, 14-day average volume of 1M+ shares, 14-day ATR above $0.50",
        "Stocks in Play: opening 5-minute volume at least its 14-day average, top 20 by that ratio",
        "First 5 minutes up -> buy stop at the range high; down -> sell stop at the range low",
        "Stop 10% of the 14-day ATR from the entry; exit at the close",
        "Size so a stop-out loses 1% of equity; gross exposure at most 4x",
    )
    rationale = (
        "Stocks with unusual opening volume are the ones news and institutions are moving; their order flow "
        "is more likely to persist through the day than in a quiet stock."
    )
    failure_modes = (
        "The tight stop and volatile names make costs at the open (spreads of 10-30 bps) decisive. The "
        "relative-volume filter does most of the work, and survivorship-free intraday data for thousands of "
        "stocks is expensive; a hand-picked list of today's active stocks is not survivorship-free."
    )
    evidence = Evidence.EXPERIMENTAL
    evidence_text = (
        "Working paper (2016-2023) reporting very large returns before spreads and slippage; a QuantConnect "
        "recreation found similar gross behaviour. No independent net-of-cost replication on the full market."
    )
    references = (ZBA_2024, KRUEGER_2026)
    needs = "1- or 5-minute bars for a liquid stock universe, survivorship-free for a real test."
    params_spec = (
        Param("or_minutes", "Opening range (minutes)", 5, "int", 1, 60, 1),
        Param("top_n", "Stocks traded per day", 20, "int", 1, 200, 1),
        Param("min_rel_volume", "Minimum relative volume", 1.0, "float", 0.0, 20.0, 0.1),
        Param("rel_volume_days", "Relative volume lookback (days)", 14, "int", 2, 100, 1),
        Param("min_price", "Minimum price ($)", 5.0, "float", 0.0, 10_000.0, 1.0),
        Param("min_avg_volume", "Minimum average daily volume (shares)", 1_000_000.0, "float", 0.0, 1e10, 100_000.0),
        Param("min_atr", "Minimum ATR ($)", 0.5, "float", 0.0, 1000.0, 0.05),
        Param("atr_days", "ATR length (days)", 14, "int", 2, 100, 1),
        Param("stop_atr_frac", "Stop distance (x ATR)", 0.10, "float", 0.01, 2.0, 0.01),
        Param("allow_short", "Trade the short side", True, "bool"),
        VOLATILITY_FILTER,
    )
    default_sizing = {"method": "stop_risk", "risk_per_trade": 0.01, "max_leverage": 4.0}

    def __init__(self, **params: Any):
        super().__init__(**params)
        self._bars_per_day = 78

    def warmup(self) -> int:
        p = self.params
        days = max(p["atr_days"], p["rel_volume_days"], 14)  # 14: the average-volume filter
        if p["min_vol_rank"] > 0:
            days = max(days, VOL_RANK_DAYS)
        return days * self._bars_per_day + 1

    def run(self, data: dict[str, pd.DataFrame]) -> StrategyOutput:
        p = self.params
        symbols = list(data)
        self.check_symbols(symbols)
        first = data[symbols[0]]
        bm = require_intraday(self.name, first.index)
        self._bars_per_day = _bars_per_day(first.index)
        info = session_info(first.index, bm)
        session_first, last = info["first"].to_numpy(), info["last"].to_numpy()
        per: dict[str, dict[str, Any]] = {}
        rel_at_decision = {}
        for sym, df in data.items():
            rng = opening_range(df, p["or_minutes"], bm)
            prior = prior_day(df, atr_days=p["atr_days"], volume_days=14)
            rel = relative_opening_volume(df, p["or_minutes"], p["rel_volume_days"])
            move = (rng["or_close"] - rng["or_open"]).to_numpy(float)
            direction = np.sign(np.nan_to_num(move))
            if not p["allow_short"]:
                direction = np.where(direction < 0, 0.0, direction)
            eligible = (
                rng["decision"].to_numpy()
                & (direction != 0)
                & (rel.to_numpy(float) >= p["min_rel_volume"])
                & (rng["or_open"].to_numpy(float) > p["min_price"])
                & (prior["avg_volume"].to_numpy(float) >= p["min_avg_volume"])
                & (prior["atr"].to_numpy(float) > p["min_atr"])
            )
            if p["min_vol_rank"] > 0:
                eligible &= prior["vol_rank"].to_numpy(float) >= p["min_vol_rank"]
            per[sym] = {"rng": rng, "prior": prior, "rel": rel, "direction": direction, "eligible": eligible}
            rel_at_decision[sym] = np.where(eligible, rel.to_numpy(float), np.nan)
        # Stocks in Play: each morning, the top_n eligible stocks by relative volume.
        rel_df = pd.DataFrame(rel_at_decision, index=first.index)
        rank = rel_df.rank(axis=1, ascending=False, method="first")
        chosen = (rank <= p["top_n"]).to_numpy()
        signals, stops, entries, diags = {}, {}, {}, {}
        for j, sym in enumerate(symbols):
            df, d = data[sym], per[sym]
            entry, stop, _ = _orb_levels(
                d["direction"],
                d["rng"]["or_high"].to_numpy(float),
                d["rng"]["or_low"].to_numpy(float),
                df["close"].to_numpy(float),
                d["prior"]["atr"].to_numpy(float),
                breakout=True,
                stop_mode="atr",
                stop_atr_frac=p["stop_atr_frac"],
                target_r=0.0,
            )
            nan = np.full(len(df), np.nan)
            path = bracket_path(
                df["open"].to_numpy(float),
                df["high"].to_numpy(float),
                df["low"].to_numpy(float),
                chosen[:, j] & np.isfinite(stop),
                d["direction"],
                entry,
                stop,
                nan,
                session_first,
                last,
            )
            signals[sym] = path["signal"]
            stops[sym] = path["stop_loss"]
            entries[sym] = path["entry_stop"]
            diags[sym] = pd.DataFrame(
                {
                    "signal": path["signal"],
                    "state": path["state"],
                    "stop_loss": path["stop_loss"],
                    "entry_stop": path["entry_stop"],
                    "or_high": d["rng"]["or_high"],
                    "or_low": d["rng"]["or_low"],
                    "rel_volume": d["rel"],
                    "atr": d["prior"]["atr"],
                    "selected": chosen[:, j],
                    "valid": np.arange(len(df)) >= self.warmup() - 1,
                },
                index=df.index,
            )
        idx = first.index
        return StrategyOutput(
            pd.DataFrame(signals, index=idx),
            diags,
            stop_losses=pd.DataFrame(stops, index=idx),
            entry_stops=pd.DataFrame(entries, index=idx),
        )

    def position_states(self, output: StrategyOutput, symbol: str) -> np.ndarray:
        states = super().position_states(output, symbol)
        diag = output.diagnostics[symbol]
        states[diag["state"].to_numpy() == 1] = "flat"
        return states

    def describe(self, output: StrategyOutput, symbol: str, t: int) -> tuple[str, list[Rule]]:
        row = output.diagnostics[symbol].iloc[t]
        rules = [
            Rule("Relative volume", fmt_num(row["rel_volume"]), None),
            Rule("Opening range", f"{fmt_num(row['or_low'])} - {fmt_num(row['or_high'])}", None),
        ]
        state = int(row["state"])
        if state == 1:
            return f"In play today: stop order at {fmt_num(row['entry_stop'])}, stop {fmt_num(row['stop_loss'])}.", rules
        if state == 2:
            return f"In a breakout trade; stop at {fmt_num(row['stop_loss'])}, out at the close.", rules
        if state == 3:
            return "Today's trade was stopped out.", rules
        return "Not among today's Stocks in Play.", rules


# --------------------------------------------------------------------------------------
# Market intraday momentum (Gao, Han, Li & Zhou 2018)
# --------------------------------------------------------------------------------------


class IntradayMomentum(SingleAssetStrategy):
    id = "intraday_momentum"
    name = "Market intraday momentum"
    category = "Intraday momentum"
    family = "intraday"
    intraday = True
    summary = (
        "The return from yesterday's close to 10:00 predicts the last half hour: hold the market in its "
        "direction from 15:30 to the close."
    )
    rules_text = (
        "First half-hour return = price at 10:00 / previous close - 1",
        "At 15:30 (the last half hour; 12:30 on early-close days): long if positive, short if negative",
        "Optionally require the 12th half-hour (15:00-15:30) to agree",
        "Exit in the closing auction; one trade a day",
    )
    rationale = (
        "Traders who rebalance infrequently and late-informed traders push the market in the direction of the "
        "morning's news near the close (Bogousslavsky 2016)."
    )
    failure_modes = (
        "Predictive R-squared is about 1-2%: right a little more often than wrong, so most days are noise. "
        "Related cross-sectional effects have weakened sharply since publication."
    )
    evidence = Evidence.MODERATE
    evidence_text = (
        "Peer-reviewed (JFE 2018), out-of-sample R-squared about 1.4%, found in most developed markets; absent "
        "in some Asian markets, and related effects have decayed since publication."
    )
    references = (GHLZ_2018,)
    needs = "Intraday bars that divide 30 minutes (1, 5, 15 or 30 minutes) for SPY or another index ETF."
    params_spec = (
        Param("use_r12", "Require the 12th half-hour to agree", False, "bool"),
        Param("min_abs_r1", "Minimum first half-hour move", 0.0, "float", 0.0, 0.05, 0.0005, help="As a fraction, e.g. 0.001 = 0.1%."),
        Param("allow_short", "Trade the short side", True, "bool"),
        VOLATILITY_FILTER,
    )
    default_sizing = {"method": "fixed", "allocation": 1.0, "max_leverage": 1.0}
    overlays = ()

    def __init__(self, **params: Any):
        super().__init__(**params)
        self._bars_per_day = 13

    def warmup(self) -> int:
        days = VOL_RANK_DAYS if self.params["min_vol_rank"] > 0 else 1  # yesterday's close
        return days * self._bars_per_day + 1

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        bm = require_intraday(self.name, df.index)
        if 30 % bm:
            raise ValueError(f"{self.name} needs bars that divide 30 minutes; these are {bm} minutes.")
        self._bars_per_day = _bars_per_day(df.index, 13)
        info = session_info(df.index, bm)
        minute = info["minute"].to_numpy()
        regular = info["regular"].to_numpy()
        dates = info["date"].to_numpy()
        day_end = info["close_minute"].to_numpy()  # minutes from 09:30 to the close, from the calendar
        end_of = minute + bm  # each bar's end, in minutes after the open
        close = df["close"].to_numpy(float)
        prior = prior_day(df)

        def close_when(hit: np.ndarray) -> np.ndarray:
            """The close of the bar marked ``hit`` in each session, on that bar and the later bars of
            its session (NaN before it: the value is not known yet)."""
            value = np.where(hit, close, np.nan)
            return pd.Series(value).groupby(dates).ffill().to_numpy()

        p10 = close_when(regular & (end_of == 30))  # 10:00
        p_decide = close_when(regular & (end_of == day_end - 30))  # 15:30 (12:30 on an early close)
        p_before = close_when(regular & (end_of == day_end - 60))  # 15:00
        prior_close = prior["prior_close"].to_numpy(float)
        r1 = p10 / prior_close - 1.0
        r12 = p_decide / p_before - 1.0
        direction = np.sign(np.nan_to_num(r1))
        direction = np.where(np.abs(np.nan_to_num(r1)) >= p["min_abs_r1"], direction, 0.0)
        if p["use_r12"]:
            direction = np.where(np.sign(np.nan_to_num(r12)) == direction, direction, 0.0)
        if not p["allow_short"]:
            direction = np.where(direction < 0, 0.0, direction)
        if p["min_vol_rank"] > 0:
            direction = np.where(prior["vol_rank"].to_numpy(float) >= p["min_vol_rank"], direction, 0.0)
        decision = regular & (end_of == day_end - 30)
        first, last = info["first"].to_numpy(), info["last"].to_numpy()
        signal = np.zeros(len(df))
        holding = 0.0
        for t in range(len(df)):
            if first[t]:
                holding = 0.0
            if decision[t]:
                holding = direction[t]
            signal[t] = 0.0 if last[t] else holding
        known_r1 = regular & (end_of >= 30)
        return pd.DataFrame(
            {
                "signal": signal,
                "r1": np.where(known_r1, r1, np.nan),
                "r12": np.where(regular & (end_of >= day_end - 30), r12, np.nan),
                "vol_rank": prior["vol_rank"],
                "valid": np.isfinite(prior_close),
            },
            index=df.index,
        )

    def describe_row(self, diag: pd.DataFrame, t: int) -> tuple[str, list[Rule]]:
        row = diag.iloc[t]
        rules = [Rule("Close to 10:00 return", fmt_pct(row["r1"], 2), None)]
        if math.isfinite(row["r12"]):
            rules.append(Rule("15:00-15:30 return", fmt_pct(row["r12"], 2), None))
        if row["signal"] > 0:
            return "Long into the close: the morning was up.", rules
        if row["signal"] < 0:
            return "Short into the close: the morning was down.", rules
        if not math.isfinite(row["r1"]):
            return "Waiting for 10:00 to measure the morning's move.", rules
        return "Flat: waiting for 15:30, or today's move was too small.", rules

    def exit_rule(self, state: str) -> str:
        return "Exit in the closing auction." if state in ("long", "short") else ""
