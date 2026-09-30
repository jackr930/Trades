"""Trend-following strategies."""

from __future__ import annotations

import numpy as np
import pandas as pd

from trades.core import indicators as ind
from trades.strategies.base import (
    Evidence,
    Overlay,
    Param,
    Reference,
    Rule,
    SingleAssetStrategy,
    check_bars,
    fmt_num,
    fmt_pct,
    held_position_text,
    hold_every,
)

MOP_2012 = Reference(
    "Moskowitz, T. J., Ooi, Y. H. & Pedersen, L. H.",
    2012,
    "Time Series Momentum",
    "Journal of Financial Economics 104(2), 228-250",
    "https://doi.org/10.1016/j.jfineco.2011.11.003",
)
HOP_2017 = Reference(
    "Hurst, B., Ooi, Y. H. & Pedersen, L. H.",
    2017,
    "A Century of Evidence on Trend-Following Investing",
    "Journal of Portfolio Management 44(1), 15-29",
    "https://doi.org/10.3905/jpm.2017.44.1.015",
)
FABER_2007 = Reference(
    "Faber, M. T.",
    2007,
    "A Quantitative Approach to Tactical Asset Allocation",
    "Journal of Wealth Management 9(4), 69-79",
    "https://doi.org/10.3905/jwm.2007.674809",
)
BLL_1992 = Reference(
    "Brock, W., Lakonishok, J. & LeBaron, B.",
    1992,
    "Simple Technical Trading Rules and the Stochastic Properties of Stock Returns",
    "Journal of Finance 47(5), 1731-1764",
    "https://doi.org/10.1111/j.1540-6261.1992.tb04681.x",
)
STW_1999 = Reference(
    "Sullivan, R., Timmermann, A. & White, H.",
    1999,
    "Data-Snooping, Technical Trading Rule Performance, and the Bootstrap",
    "Journal of Finance 54(5), 1647-1691",
    "https://doi.org/10.1111/0022-1082.00163",
)
FAITH_2007 = Reference("Faith, C. M.", 2007, "Way of the Turtle", "McGraw-Hill")
WILDER_1978 = Reference("Wilder, J. W.", 1978, "New Concepts in Technical Trading Systems", "Trend Research")
MOREIRA_MUIR_2017 = Reference(
    "Moreira, A. & Muir, T.",
    2017,
    "Volatility-Managed Portfolios",
    "Journal of Finance 72(4), 1611-1644",
    "https://doi.org/10.1111/jofi.12513",
)


class TimeSeriesMomentum(SingleAssetStrategy):
    id = "tsmom"
    name = "Time-Series Momentum"
    category = "Trend following"
    summary = "Go long if the asset's own trailing 12-month return is positive, short (or flat) if negative, sized to a volatility target."
    rules_text = (
        "Every month (21 bars), compute the trailing 12-month (252-bar) return.",
        "Positive return -> long; negative -> short (or flat when shorting is disabled).",
        "Size each position so its expected volatility matches a target (e.g. 15%/yr) using an EWMA volatility estimate.",
    )
    rationale = (
        "Investors under-react to news and then over-extrapolate trends, so past returns of an asset "
        "predict its own future returns for up to about a year. Volatility scaling keeps risk roughly "
        "constant across calm and turbulent markets."
    )
    failure_modes = (
        "Choppy, trendless markets cause whipsaws; sharp trend reversals (e.g. V-shaped recoveries such "
        "as spring 2009 or 2020) hurt because the signal lags by construction."
    )
    evidence = Evidence.STRONG
    evidence_text = (
        "Moskowitz, Ooi & Pedersen (2012) found significant time-series momentum in 58 equity-index, "
        "currency, commodity and bond futures (1985-2009); Hurst, Ooi & Pedersen (2017) extend the evidence "
        "back to 1880. Evidence is strongest for diversified futures portfolios; single stocks are noisier."
    )
    references = (MOP_2012, HOP_2017, MOREIRA_MUIR_2017)
    params_spec = (
        Param(
            "lookback",
            "Lookback (bars)",
            252,
            "int",
            20,
            756,
            1,
            help="Return window; 252 daily bars = 12 months.",
        ),
        Param(
            "skip",
            "Skip recent (bars)",
            0,
            "int",
            0,
            63,
            1,
            help="Ignore the most recent bars (0 in the original paper).",
        ),
        Param(
            "hold",
            "Re-evaluate every (bars)",
            21,
            "int",
            1,
            126,
            1,
            help="The paper rebalances monthly (21 bars).",
        ),
        Param("long_only", "Long only", False, "bool", help="Go flat instead of short on negative momentum."),
    )
    default_sizing = {"method": "vol_target", "target_vol": 0.15, "rebalance_every": 21}
    overlays = (Overlay("mom_return", "Trailing return", "lower", levels=(0.0,)),)
    uses_short = True

    def warmup(self) -> int:
        return self.params["lookback"] + self.params["skip"] + 1

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        close = df["close"]
        past = close.shift(p["skip"]) / close.shift(p["skip"] + p["lookback"]) - 1.0
        raw = np.sign(past)
        if p["long_only"]:
            raw = raw.clip(lower=0.0)
        signal = hold_every(raw, p["hold"])
        check = check_bars(raw, p["hold"])
        return pd.DataFrame(
            {"signal": signal, "mom_return": past, "check": check, "valid": past.notna()}, index=df.index
        )

    def describe_row(self, diag, t):
        p = self.params
        r = diag["mom_return"].iloc[t]
        label = f"Trailing {p['lookback']}-bar return" + (
            f" (skipping last {p['skip']})" if p["skip"] else ""
        )
        held = held_position_text(diag["signal"].iloc[t])
        checks = np.flatnonzero(diag["check"].to_numpy(bool)[: t + 1])
        last = int(checks[-1]) if len(checks) else t
        ago, next_in = t - last, p["hold"] - (t - last)
        rules = [
            Rule(label, fmt_pct(r), bool(r > 0)),
            Rule(
                "Re-evaluation",
                f"every {p['hold']} bars; last {ago} bar{'s' if ago != 1 else ''} ago, next in {next_in}",
                None,
            ),
        ]
        if ago == 0:  # re-evaluated on this bar: the position follows today's reading
            direction = (
                "uptrend -> long"
                if r > 0
                else ("downtrend -> flat" if p["long_only"] else "downtrend -> short")
            )
            return f"{label} is {fmt_pct(r)}: {direction}.", rules
        then = diag["mom_return"].iloc[last]
        headline = (
            f"Holding {held} from the last check {ago} bar{'s' if ago != 1 else ''} ago (trailing return "
            f"{fmt_pct(then)} then). Today's reading is {fmt_pct(r)}; next check in {next_in} bar"
            f"{'s' if next_in != 1 else ''}."
        )
        return headline, rules

    def exit_rule(self, state):
        return f"Re-checked every {self.params['hold']} bars; reverses when the trailing return changes sign."


class FaberTrend(SingleAssetStrategy):
    id = "faber_trend"
    name = "Trend Filter (10-Month SMA)"
    category = "Trend following"
    summary = "Hold the asset while its price is above its ~10-month (200-day) moving average; move to cash when below."
    rules_text = (
        "Compute the 200-bar simple moving average (about 10 months of daily data).",
        "Once a month (on the first bar of each month): close above the SMA -> long; below -> cash.",
        "Optionally short when below the average (not part of the original rule).",
    )
    rationale = (
        "A slow trend filter keeps you invested in persistent bull markets while side-stepping much of "
        "prolonged bear markets, which historically carry most of the large drawdowns."
    )
    failure_modes = (
        "Sideways markets that repeatedly cross the average produce small losses and trading costs; "
        "fast crashes can happen before the monthly check fires."
    )
    evidence = Evidence.MODERATE
    evidence_text = (
        "Faber (2007) showed that the 10-month SMA rule applied to US stocks, bonds, REITs and commodities "
        "(1973-2005) produced equity-like returns with much smaller drawdowns. Out-of-sample results since "
        "publication show lower drawdowns but lagging returns in strong bull markets; it is a risk-control "
        "rule more than a return enhancer."
    )
    references = (FABER_2007, HOP_2017)
    params_spec = (
        Param("sma_length", "SMA length (bars)", 200, "int", 20, 400, 1, help="200 daily bars ~ 10 months."),
        Param(
            "monthly",
            "Check monthly",
            True,
            "bool",
            help="Only act on the first bar of each month, like the paper.",
        ),
        Param("allow_short", "Short below SMA", False, "bool"),
    )
    overlays = (Overlay("sma", "SMA"),)

    def warmup(self) -> int:
        return self.params["sma_length"]

    def compute(self, df):
        p = self.params
        close = df["close"]
        avg = ind.sma(close, p["sma_length"])
        below = -1.0 if p["allow_short"] else 0.0
        raw = pd.Series(np.where(close > avg, 1.0, below), index=df.index).where(avg.notna())
        if p["monthly"]:
            naive = df.index if df.index.tz is None else df.index.tz_localize(None)
            month = naive.year * 12 + naive.month
            new_month = pd.Series(np.r_[True, month[1:] != month[:-1]], index=df.index)
            first_valid = raw.first_valid_index()
            if first_valid is not None:
                new_month.loc[first_valid] = True
            signal = raw.where(new_month).ffill()
            check = new_month.to_numpy(bool) & raw.notna().to_numpy()
        else:
            signal = raw
            check = raw.notna().to_numpy()
        return pd.DataFrame(
            {
                "signal": signal,
                "sma": avg,
                "distance": close / avg - 1.0,
                "check": check,
                "valid": avg.notna(),
            },
            index=df.index,
        )

    def describe_row(self, diag, t):
        p = self.params
        d = diag["distance"].iloc[t]
        sma_v = diag["sma"].iloc[t]
        rules = [
            Rule(f"Close vs {p['sma_length']}-bar SMA ({fmt_num(sma_v)})", fmt_pct(d), bool(d > 0)),
            Rule("Evaluation", "first bar of each month" if p["monthly"] else "every bar", None),
        ]
        checks = np.flatnonzero(diag["check"].to_numpy(bool)[: t + 1])
        last = int(checks[-1]) if len(checks) else t
        if last == t:  # evaluated on this bar
            verdict = (
                "above the trend line -> invested"
                if d > 0
                else "below the trend line -> " + ("short" if p["allow_short"] else "in cash")
            )
            return f"Price is {fmt_pct(d)} vs its {p['sma_length']}-bar average: {verdict}.", rules
        held = held_position_text(diag["signal"].iloc[t])
        position = {"long": "Invested", "short": "Short", "flat": "In cash"}[held]
        then = diag["distance"].iloc[last]
        when = diag.index[last].strftime("%b %d")
        headline = (
            f"{position} since the monthly check on {when} (price was {fmt_pct(then)} vs the average then). "
            f"Today it is {fmt_pct(d)}; the next check is on the first bar of next month."
        )
        return headline, rules

    def exit_rule(self, state):
        when = "at the next monthly check" if self.params["monthly"] else "on the next bar"
        if state == "long":
            return f"Exit {when} if the close is below the SMA."
        return f"Re-enter {when} if the close is above the SMA."


class MovingAverageCrossover(SingleAssetStrategy):
    id = "ma_crossover"
    name = "Moving-Average Crossover"
    category = "Trend following"
    summary = "Long while the fast moving average is above the slow one (the '50/200 golden cross'); flat or short otherwise."
    rules_text = (
        "Compute a fast (50-bar) and slow (200-bar) moving average.",
        "Fast above slow by more than the band -> long; below by more than the band -> flat (or short).",
        "Inside the band, keep the previous position (reduces whipsaw).",
    )
    rationale = "Captures medium-term trends; the band filters out noise around the crossover point."
    failure_modes = "Lags turning points and whipsaws in range-bound markets."
    evidence = Evidence.MODERATE
    evidence_text = (
        "Brock, Lakonishok & LeBaron (1992) found that moving-average rules on the Dow (1897-1986) produced "
        "returns after buy signals well above those after sell signals. Sullivan, Timmermann & White (1999) "
        "showed that after correcting for data-snooping the best rules' out-of-sample performance after 1986 "
        "was not significant -- a lesson in why testing many rules inflates results."
    )
    references = (BLL_1992, STW_1999)
    params_spec = (
        Param("fast", "Fast MA (bars)", 50, "int", 2, 200, 1),
        Param("slow", "Slow MA (bars)", 200, "int", 10, 400, 1),
        Param("ma_type", "Average type", "sma", "choice", choices=("sma", "ema")),
        Param("band_pct", "Band (%)", 0.0, "float", 0.0, 5.0, 0.1, help="BLL tested 0% and 1% bands."),
        Param("allow_short", "Short on bearish cross", False, "bool"),
    )
    overlays = (Overlay("ma_fast", "Fast MA"), Overlay("ma_slow", "Slow MA"))

    def validate(self):
        if self.params["fast"] >= self.params["slow"]:
            raise ValueError("Fast MA must be shorter than the slow MA")

    def warmup(self) -> int:
        return self.params["slow"]

    def compute(self, df):
        p = self.params
        fn = ind.sma if p["ma_type"] == "sma" else ind.ema
        fast, slow = fn(df["close"], p["fast"]), fn(df["close"], p["slow"])
        spread = fast / slow - 1.0
        band = p["band_pct"] / 100.0
        down = -1.0 if p["allow_short"] else 0.0
        raw = pd.Series(np.where(spread > band, 1.0, np.where(spread < -band, down, np.nan)), index=df.index)
        raw = raw.where(slow.notna())
        signal = raw.ffill().where(slow.notna())
        return pd.DataFrame(
            {"signal": signal, "ma_fast": fast, "ma_slow": slow, "spread": spread, "valid": slow.notna()},
            index=df.index,
        )

    def describe_row(self, diag, t):
        p = self.params
        s = diag["spread"].iloc[t]
        name = p["ma_type"].upper()
        rules = [
            Rule(
                f"{p['fast']}-{name} vs {p['slow']}-{name}",
                f"{fmt_num(diag['ma_fast'].iloc[t])} vs {fmt_num(diag['ma_slow'].iloc[t])} ({fmt_pct(s)})",
                bool(s > 0),
            ),
        ]
        if p["band_pct"]:
            rules.append(Rule("Band", f"+/-{p['band_pct']:.1f}%", None))
        regime = "bullish (fast above slow)" if s > 0 else "bearish (fast below slow)"
        return f"Moving averages are {regime}, spread {fmt_pct(s)}.", rules

    def exit_rule(self, state):
        if state == "long":
            return "Exit when the fast MA falls below the slow MA (minus the band)."
        if state == "short":
            return "Cover when the fast MA rises above the slow MA (plus the band)."
        return "Enter when the fast MA crosses above the slow MA (plus the band)."


class DonchianBreakout(SingleAssetStrategy):
    id = "donchian_breakout"
    name = "Donchian Breakout (Turtle System 1)"
    category = "Trend following"
    summary = "Buy a close above the highest high of the last 20 bars; exit on a close below the 10-bar low or a 2-ATR stop."
    rules_text = (
        "Entry: close breaks above the prior 20-bar high (short: below the prior 20-bar low, if enabled).",
        "Exit: close breaks the opposite 10-bar channel, or the protective stop 2 x ATR(20) from entry is hit.",
        "Size so that a 2-ATR adverse move costs about 1% of equity (the Turtles' 'unit').",
    )
    rationale = (
        "Breakouts to new highs often mark the start of trends; cutting losers quickly with a volatility-based "
        "stop and letting winners run creates a positively skewed payoff: many small losses, a few large wins."
    )
    failure_modes = "False breakouts in range-bound markets; win rate is typically only 30-40%, which is psychologically hard."
    evidence = Evidence.MODERATE
    evidence_text = (
        "Richard Donchian's channel rule was the core of the Turtle Trading system (Faith 2007). Channel "
        "breakouts are one form of trend following, whose long-run evidence is strong in diversified futures "
        "(Hurst, Ooi & Pedersen 2017); results on individual stocks are weaker and parameter-sensitive."
    )
    references = (FAITH_2007, HOP_2017, WILDER_1978)
    params_spec = (
        Param(
            "entry", "Entry channel (bars)", 20, "int", 5, 120, 1, help="Turtle System 1: 20; System 2: 55."
        ),
        Param("exit", "Exit channel (bars)", 10, "int", 2, 60, 1, help="Turtle System 1: 10; System 2: 20."),
        Param("atr_length", "ATR length", 20, "int", 5, 60, 1),
        Param(
            "stop_atr", "Stop (ATRs)", 2.0, "float", 0.0, 6.0, 0.25, help="0 disables the protective stop."
        ),
        Param("allow_short", "Trade downside breakouts", False, "bool"),
    )
    default_sizing = {"method": "atr_risk", "risk_per_trade": 0.01, "stop_atr": 2.0, "atr_length": 20}
    overlays = (
        Overlay("dc_upper", "Entry high"),
        Overlay("dc_lower", "Entry low"),
        Overlay("exit_level", "Exit channel", style="dotted"),
        Overlay("stop", "Protective stop", style="dashed"),
    )

    def validate(self):
        if self.params["exit"] > self.params["entry"]:
            raise ValueError("The exit channel should not be longer than the entry channel")

    def warmup(self) -> int:
        return max(self.params["entry"], self.params["atr_length"]) + 1

    def compute(self, df):
        p = self.params
        close = df["close"].to_numpy(float)
        ent = ind.donchian(df["high"], df["low"], p["entry"])
        ext = ind.donchian(df["high"], df["low"], p["exit"])
        n = ind.atr(df["high"], df["low"], df["close"], p["atr_length"]).to_numpy(float)
        up_e, lo_e = ent["upper"].to_numpy(float), ent["lower"].to_numpy(float)
        up_x, lo_x = ext["upper"].to_numpy(float), ext["lower"].to_numpy(float)
        T = len(close)
        signal = np.zeros(T)
        stop = np.full(T, np.nan)
        exit_level = np.full(T, np.nan)
        entry_px = np.full(T, np.nan)
        pos, stop_px, e_px = 0.0, np.nan, np.nan
        k = p["stop_atr"]
        for t in range(T):
            c = close[t]
            if np.isnan(up_e[t]) or np.isnan(n[t]):
                signal[t] = 0.0
                continue
            if pos > 0 and (c < lo_x[t] or (k > 0 and c < stop_px)):
                pos = 0.0
            elif pos < 0 and (c > up_x[t] or (k > 0 and c > stop_px)):
                pos = 0.0
            if pos == 0:
                if c > up_e[t]:
                    pos, e_px = 1.0, c
                    stop_px = c - k * n[t] if k > 0 else np.nan
                elif p["allow_short"] and c < lo_e[t]:
                    pos, e_px = -1.0, c
                    stop_px = c + k * n[t] if k > 0 else np.nan
            signal[t] = pos
            if pos != 0:
                stop[t] = stop_px
                entry_px[t] = e_px
                exit_level[t] = lo_x[t] if pos > 0 else up_x[t]
        valid = ~np.isnan(up_e) & ~np.isnan(n)
        return pd.DataFrame(
            {
                "signal": signal,
                "dc_upper": up_e,
                "dc_lower": lo_e,
                "exit_level": exit_level,
                "stop": stop,
                "entry_price": entry_px,
                "atr": n,
                "close": close,
                "valid": valid,
            },
            index=df.index,
        )

    def describe_row(self, diag, t):
        p = self.params
        c, hi, lo = diag["close"].iloc[t], diag["dc_upper"].iloc[t], diag["dc_lower"].iloc[t]
        sig = diag["signal"].iloc[t]
        rules = [
            Rule(f"Close vs prior {p['entry']}-bar high", f"{fmt_num(c)} vs {fmt_num(hi)}", bool(c > hi)),
            Rule(f"Close vs prior {p['entry']}-bar low", f"{fmt_num(c)} vs {fmt_num(lo)}", None),
            Rule(f"ATR({p['atr_length']})", fmt_num(diag["atr"].iloc[t]), None),
        ]
        if sig > 0:
            rules.append(Rule("Protective stop", fmt_num(diag["stop"].iloc[t]), None))
            rules.append(
                Rule(f"Exit if close < {p['exit']}-bar low", fmt_num(diag["exit_level"].iloc[t]), None)
            )
            head = f"Long after a breakout at {fmt_num(diag['entry_price'].iloc[t])}; riding the trend."
        elif sig < 0:
            rules.append(Rule("Protective stop", fmt_num(diag["stop"].iloc[t]), None))
            rules.append(
                Rule(f"Cover if close > {p['exit']}-bar high", fmt_num(diag["exit_level"].iloc[t]), None)
            )
            head = f"Short after a downside breakout at {fmt_num(diag['entry_price'].iloc[t])}."
        else:
            gap = hi / c - 1.0 if c else np.nan
            head = f"No breakout: price needs {fmt_pct(gap)} to clear the {p['entry']}-bar high."
        return head, rules

    def exit_rule(self, state):
        p = self.params
        if state == "long":
            return f"Exit on a close below the {p['exit']}-bar low or below the {p['stop_atr']:g}-ATR stop."
        if state == "short":
            return f"Cover on a close above the {p['exit']}-bar high or above the {p['stop_atr']:g}-ATR stop."
        return f"Enter on a close above the prior {p['entry']}-bar high."
