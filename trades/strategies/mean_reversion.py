"""Single-asset mean-reversion strategies."""

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
    fmt_num,
    fmt_pct,
)

BOLLINGER_2001 = Reference("Bollinger, J.", 2001, "Bollinger on Bollinger Bands", "McGraw-Hill")
CONNORS_2008 = Reference(
    "Connors, L. & Alvarez, C.", 2008, "Short Term Trading Strategies That Work", "TradingMarkets Publishing"
)
LEHMANN_1990 = Reference(
    "Lehmann, B. N.",
    1990,
    "Fads, Martingales, and Market Efficiency",
    "Quarterly Journal of Economics 105(1), 1-28",
    "https://doi.org/10.2307/2937816",
)
JEGADEESH_1990 = Reference(
    "Jegadeesh, N.",
    1990,
    "Evidence of Predictable Behavior of Security Returns",
    "Journal of Finance 45(3), 881-898",
    "https://doi.org/10.1111/j.1540-6261.1990.tb05110.x",
)
WILDER_1978 = Reference("Wilder, J. W.", 1978, "New Concepts in Technical Trading Systems", "Trend Research")


class BollingerReversion(SingleAssetStrategy):
    id = "bollinger_reversion"
    name = "Bollinger Band Mean Reversion"
    category = "Mean reversion"
    summary = "Buy when price closes below the lower Bollinger Band (2 standard deviations below its 20-bar average); exit back at the average."
    rules_text = (
        "Bands: 20-bar SMA +/- 2 standard deviations.",
        "Entry: close below the lower band (optionally only while above a long-term trend SMA).",
        "Exit: close back at or above the middle band, or after a maximum holding period.",
        "Short side (optional): close above the upper band, cover at the middle band.",
    )
    rationale = (
        "Short-term over-reactions to liquidity shocks and noise tend to partially reverse as liquidity "
        "providers step in (Lehmann 1990; Jegadeesh 1990). The trend filter avoids 'catching falling knives'."
    )
    failure_modes = (
        "Strong trends 'walk the band': price can stay below the lower band for weeks, so losses on the "
        "few failed trades can erase many small wins (negatively skewed returns)."
    )
    evidence = Evidence.PRACTITIONER
    evidence_text = (
        "Short-horizon reversal is documented academically (Lehmann 1990; Jegadeesh 1990), but band rules "
        "themselves come from practitioners (Bollinger 2001) and have limited peer-reviewed testing. They tend "
        "to work better on broad indices than on single stocks, and are sensitive to costs."
    )
    references = (BOLLINGER_2001, LEHMANN_1990, JEGADEESH_1990)
    params_spec = (
        Param("length", "Band length (bars)", 20, "int", 5, 100, 1),
        Param("k", "Width (std devs)", 2.0, "float", 0.5, 4.0, 0.1),
        Param(
            "trend_filter",
            "Trend SMA (0 = off)",
            0,
            "int",
            0,
            400,
            1,
            help="Only take longs above (shorts below) this SMA.",
        ),
        Param("max_hold", "Max holding (bars, 0 = off)", 0, "int", 0, 120, 1),
        Param("allow_short", "Fade the upper band", False, "bool"),
    )
    overlays = (
        Overlay("bb_upper", "Upper band", style="dotted"),
        Overlay("bb_mid", "Middle band"),
        Overlay("bb_lower", "Lower band", style="dotted"),
        Overlay("bb_pct_b", "%B", "lower", levels=(0.0, 0.5, 1.0)),
    )

    def warmup(self) -> int:
        return max(self.params["length"], self.params["trend_filter"])

    def compute(self, df):
        p = self.params
        close = df["close"]
        bands = ind.bollinger(close, p["length"], p["k"])
        trend = ind.sma(close, p["trend_filter"]) if p["trend_filter"] else pd.Series(np.nan, index=df.index)
        c = close.to_numpy(float)
        lo, mid, up = (bands[k].to_numpy(float) for k in ("bb_lower", "bb_mid", "bb_upper"))
        tr = trend.to_numpy(float)
        use_trend = p["trend_filter"] > 0
        T = len(c)
        signal = np.zeros(T)
        pos, held = 0.0, 0
        for t in range(T):
            if np.isnan(mid[t]) or (use_trend and np.isnan(tr[t])):
                continue
            if pos != 0:
                held += 1
                if (pos > 0 and c[t] >= mid[t]) or (pos < 0 and c[t] <= mid[t]):
                    pos = 0.0
                elif p["max_hold"] and held >= p["max_hold"]:
                    pos = 0.0
            if pos == 0:
                if c[t] < lo[t] and (not use_trend or c[t] > tr[t]):
                    pos, held = 1.0, 0
                elif p["allow_short"] and c[t] > up[t] and (not use_trend or c[t] < tr[t]):
                    pos, held = -1.0, 0
            signal[t] = pos
        out = bands.copy()
        out["signal"] = signal
        out["trend_sma"] = trend
        out["close"] = close
        out["valid"] = bands["bb_mid"].notna() & (trend.notna() if use_trend else True)
        return out

    def describe_row(self, diag, t):
        p = self.params
        c, lo, mid, up = (diag[k].iloc[t] for k in ("close", "bb_lower", "bb_mid", "bb_upper"))
        pb = diag["bb_pct_b"].iloc[t]
        rules = [
            Rule("Close vs lower band", f"{fmt_num(c)} vs {fmt_num(lo)}", bool(c < lo)),
            Rule("Close vs middle band (exit)", f"{fmt_num(c)} vs {fmt_num(mid)}", None),
            Rule("%B (0 = lower band, 1 = upper band)", fmt_num(pb), None),
        ]
        if p["trend_filter"]:
            tr = diag["trend_sma"].iloc[t]
            rules.append(
                Rule(
                    f"Above {p['trend_filter']}-bar trend SMA", f"{fmt_num(c)} vs {fmt_num(tr)}", bool(c > tr)
                )
            )
        sig = diag["signal"].iloc[t]
        if sig > 0:
            head = f"Long a stretched dip; target is the middle band at {fmt_num(mid)}."
        elif sig < 0:
            head = f"Short a stretched rally; target is the middle band at {fmt_num(mid)}."
        else:
            head = f"No stretch: price sits at %B {fmt_num(pb)} inside the bands (entry below {fmt_num(lo)})."
        return head, rules

    def exit_rule(self, state):
        if state == "long":
            return "Exit on a close at or above the middle band" + (
                f", or after {self.params['max_hold']} bars." if self.params["max_hold"] else "."
            )
        if state == "short":
            return "Cover on a close at or below the middle band."
        return "Enter on a close below the lower band."


class RSI2Reversion(SingleAssetStrategy):
    id = "rsi2_reversion"
    name = "RSI(2) Pullback (Connors)"
    category = "Mean reversion"
    summary = "In an uptrend (price above its 200-bar SMA), buy when 2-bar RSI drops below 10; sell when price closes above its 5-bar SMA."
    rules_text = (
        "Trend filter: close above the 200-bar SMA.",
        "Entry: RSI(2) below 10 (a sharp 1-3 day pullback).",
        "Exit: close above the 5-bar SMA.",
        "Mirror image for shorts (optional): below the 200-SMA, RSI(2) above 90, cover below the 5-SMA.",
    )
    rationale = (
        "Buying short, sharp pullbacks within an established uptrend exploits short-term reversal while "
        "the trend filter keeps you on the side of the dominant drift."
    )
    failure_modes = (
        "No stop by design: rare deep pullbacks (e.g. crash onsets) produce large losses; high win rates "
        "mask negatively skewed returns. Costs matter because holding periods are only a few days."
    )
    evidence = Evidence.PRACTITIONER
    evidence_text = (
        "Popularised by Connors & Alvarez (2008) with backtests on US index ETFs showing high win rates. "
        "Grounded in documented short-term reversal (Jegadeesh 1990), but largely practitioner-tested; "
        "the edge on indices has reportedly weakened since publication."
    )
    references = (CONNORS_2008, WILDER_1978, JEGADEESH_1990)
    params_spec = (
        Param("rsi_length", "RSI length", 2, "int", 2, 14, 1),
        Param("entry", "Buy below RSI", 10.0, "float", 1.0, 40.0, 1.0),
        Param("exit_ma", "Exit SMA (bars)", 5, "int", 2, 20, 1),
        Param("trend_ma", "Trend SMA (bars)", 200, "int", 50, 300, 1),
        Param("allow_short", "Short overbought downtrends", False, "bool"),
        Param("short_entry", "Short above RSI", 90.0, "float", 60.0, 99.0, 1.0),
    )
    overlays = (
        Overlay("trend_sma", "Trend SMA"),
        Overlay("exit_sma", "Exit SMA", style="dotted"),
        Overlay("rsi", "RSI", "lower", levels=(10.0, 90.0)),
    )

    def warmup(self) -> int:
        return max(self.params["trend_ma"], self.params["rsi_length"] + 1)

    def compute(self, df):
        p = self.params
        close = df["close"]
        r = ind.rsi(close, p["rsi_length"])
        trend = ind.sma(close, p["trend_ma"])
        ex = ind.sma(close, p["exit_ma"])
        c, rv, tr, xv = (s.to_numpy(float) for s in (close, r, trend, ex))
        T = len(c)
        signal = np.zeros(T)
        pos = 0.0
        for t in range(T):
            if np.isnan(tr[t]) or np.isnan(rv[t]) or np.isnan(xv[t]):
                continue
            if pos > 0 and c[t] > xv[t]:
                pos = 0.0
            elif pos < 0 and c[t] < xv[t]:
                pos = 0.0
            if pos == 0:
                if c[t] > tr[t] and rv[t] < p["entry"]:
                    pos = 1.0
                elif p["allow_short"] and c[t] < tr[t] and rv[t] > p["short_entry"]:
                    pos = -1.0
            signal[t] = pos
        valid = trend.notna() & r.notna() & ex.notna()
        return pd.DataFrame(
            {"signal": signal, "rsi": r, "trend_sma": trend, "exit_sma": ex, "close": close, "valid": valid},
            index=df.index,
        )

    def describe_row(self, diag, t):
        p = self.params
        c, rv, tr, xv = (diag[k].iloc[t] for k in ("close", "rsi", "trend_sma", "exit_sma"))
        rules = [
            Rule(
                f"Uptrend: close above {p['trend_ma']}-SMA",
                f"{fmt_num(c)} vs {fmt_num(tr)} ({fmt_pct(c / tr - 1)})",
                bool(c > tr),
            ),
            Rule(f"RSI({p['rsi_length']}) below {p['entry']:g}", fmt_num(rv, 1), bool(rv < p["entry"])),
            Rule(f"Exit when close > {p['exit_ma']}-SMA", fmt_num(xv), None),
        ]
        sig = diag["signal"].iloc[t]
        if sig > 0:
            head = f"Holding a pullback buy; exit on a close above {fmt_num(xv)}."
        elif sig < 0:
            head = f"Holding an overbought short; cover on a close below {fmt_num(xv)}."
        elif c > tr:
            head = f"Uptrend, but RSI({p['rsi_length']}) = {fmt_num(rv, 1)} is not oversold (needs < {p['entry']:g})."
        else:
            head = f"Below the {p['trend_ma']}-bar SMA: no long pullback trades in a downtrend."
        return head, rules

    def exit_rule(self, state):
        if state == "long":
            return f"Exit on a close above the {self.params['exit_ma']}-bar SMA."
        if state == "short":
            return f"Cover on a close below the {self.params['exit_ma']}-bar SMA."
        return (
            f"Buy when RSI({self.params['rsi_length']}) < {self.params['entry']:g} while above the trend SMA."
        )
