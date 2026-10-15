"""Modern quant-desk strategies.

These are the kinds of models systematic funds and prop desks run, simplified to what can
be done honestly with daily or intraday price and volume data:

* a multi-horizon, volatility-normalised trend signal of the kind CTAs use;
* factor-residual statistical arbitrage (Avellaneda & Lee 2010);
* residual (idiosyncratic) momentum (Blitz, Huij & Martens 2011);
* pairs trading with a Kalman-filter hedge ratio;
* a hidden Markov model that infers the market regime and sizes exposure to it.

Every estimate at bar t uses only bars up to t; the parametrised no-look-ahead test checks
this for each of them, like every other strategy in the library.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from trades.strategies.base import (
    Evidence,
    Explanation,
    Kind,
    Overlay,
    Param,
    Reference,
    Rule,
    SingleAssetStrategy,
    Strategy,
    StrategyOutput,
    check_bars,
    fmt_num,
    fmt_pct,
    held_position_text,
    hold_every,
    signal_since,
    symmetric_levels,
)
from trades.strategies.cross_sectional import DM_2016, JT_1993, CrossSectionalStrategy
from trades.strategies.pairs import AL_2010
from trades.strategies.trend import HOP_2017, MOP_2012

BAZ_2015 = Reference(
    "Baz, J., Granger, N., Harvey, C. R., Le Roux, N. & Rattray, S.",
    2015,
    "Dissecting Investment Strategies in the Cross Section and Time Series",
    "SSRN Working Paper 2695101 (Man Group)",
    "https://doi.org/10.2139/ssrn.2695101",
)
LZR_2019 = Reference(
    "Lim, B., Zohren, S. & Roberts, S.",
    2019,
    "Enhancing Time-Series Momentum Strategies Using Deep Neural Networks",
    "Journal of Financial Data Science 1(4), 19-38",
    "https://doi.org/10.3905/jfds.2019.1.015",
)
KHANDANI_LO_2011 = Reference(
    "Khandani, A. E. & Lo, A. W.",
    2011,
    "What Happened to the Quants in August 2007? Evidence from Factors and Transactions Data",
    "Journal of Financial Markets 14(1), 1-46",
    "https://doi.org/10.1016/j.finmar.2010.07.005",
)
BHM_2011 = Reference(
    "Blitz, D., Huij, J. & Martens, M.",
    2011,
    "Residual Momentum",
    "Journal of Empirical Finance 18(3), 506-521",
    "https://doi.org/10.1016/j.jempfin.2011.01.003",
)
CHAN_2013 = Reference(
    "Chan, E. P.",
    2013,
    "Algorithmic Trading: Winning Strategies and Their Rationale (ch. 3, Kalman filter)",
    "Wiley",
)
EHM_2005 = Reference(
    "Elliott, R. J., van der Hoek, J. & Malcolm, W. P.",
    2005,
    "Pairs Trading",
    "Quantitative Finance 5(3), 271-276",
    "https://doi.org/10.1080/14697680500149370",
)
HAMILTON_1989 = Reference(
    "Hamilton, J. D.",
    1989,
    "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle",
    "Econometrica 57(2), 357-384",
    "https://doi.org/10.2307/1912559",
)
ANG_BEKAERT_2002 = Reference(
    "Ang, A. & Bekaert, G.",
    2002,
    "International Asset Allocation With Regime Shifts",
    "Review of Financial Studies 15(4), 1137-1187",
    "https://doi.org/10.1093/rfs/15.4.1137",
)
NML_2018 = Reference(
    "Nystrup, P., Madsen, H. & Lindstrom, E.",
    2018,
    "Dynamic Portfolio Optimization Across Hidden Market Regimes",
    "Quantitative Finance 18(1), 83-95",
    "https://doi.org/10.1080/14697688.2017.1342857",
)
KPT_2012 = Reference(
    "Kritzman, M., Page, S. & Turkington, D.",
    2012,
    "Regime Shifts: Implications for Dynamic Strategies",
    "Financial Analysts Journal 68(3), 22-39",
    "https://doi.org/10.2469/faj.v68.n3.3",
)


# --------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------


def _log_returns(data: dict[str, pd.DataFrame], symbols: list[str]) -> np.ndarray:
    closes = np.column_stack([data[s]["close"].to_numpy(float) for s in symbols])
    out = np.full(closes.shape, np.nan)
    out[1:] = np.diff(np.log(closes), axis=0)
    return out


def _market_ex_self(rets: np.ndarray) -> np.ndarray:
    """Average return of the *other* symbols at each bar: each stock's market factor."""
    n = rets.shape[1]
    return (rets.sum(axis=1, keepdims=True) - rets) / (n - 1)


def _window_regression(r: np.ndarray, f: np.ndarray, w: int):
    """OLS of r on f over every trailing window of ``w`` bars.

    Returns (beta, dR, dF) where row k is the window ending at bar k + w - 1, and dR, dF
    are the demeaned returns inside each window (residuals are dR - beta * dF).
    """
    Rw, Fw = sliding_window_view(r, w), sliding_window_view(f, w)
    dR = Rw - Rw.mean(axis=1, keepdims=True)
    dF = Fw - Fw.mean(axis=1, keepdims=True)
    var_f = (dF**2).mean(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        beta = np.where(var_f > 0, (dR * dF).mean(axis=1) / var_f, np.nan)
    return beta, dR, dF


def _full(T: int, w: int, rows: np.ndarray) -> np.ndarray:
    """Place per-window results (row k = window ending at k + w - 1) on the bar axis."""
    out = np.full(T, np.nan)
    if len(rows):
        out[w - 1 :] = rows
    return out


# --------------------------------------------------------------------------------------
# 1. Multi-horizon trend (the CTA signal)
# --------------------------------------------------------------------------------------

TREND_HORIZONS = {
    "fast": ((4, 12), (8, 24), (16, 48)),
    "standard": ((8, 24), (16, 48), (32, 96)),
    "slow": ((16, 48), (32, 96), (64, 192)),
}


def trend_response(y):
    """Baz et al.'s response curve: grows with trend strength, then bends back for extreme,
    stretched trends (max about 0.96 at |y| = sqrt(2))."""
    return y * np.exp(-(y**2) / 4.0) / 0.89


class MultiHorizonTrend(SingleAssetStrategy):
    id = "cta_trend"
    name = "Multi-Horizon Trend (CTA Signal)"
    category = "Trend following"
    family = "modern"
    counterpart = "tsmom"
    needs = (
        "Markets that trend; spread over several unrelated markets (stocks, bonds, gold) it diversifies best."
    )
    summary = (
        "The trend signal systematic CTAs use: three fast/slow moving-average gaps, each scaled by "
        "volatility and passed through a response curve, averaged into one position between -1 and +1."
    )
    rules_text = (
        "For three horizon pairs (e.g. 8/24, 16/48 and 32/96 bars), take the gap between a fast and a slow "
        "exponential moving average of the price.",
        "Divide each gap by the price's 63-bar standard deviation, then by that ratio's own 252-bar "
        "standard deviation, so each horizon reads in comparable units.",
        "Pass each through the response curve y*exp(-y^2/4)/0.89: strength grows with the trend but falls "
        "back for extreme, overstretched moves. Average the three into a position between -1 and +1.",
        "Round to steps of 0.25 and re-evaluate every 5 bars to keep turnover down; size to a volatility "
        "target.",
    )
    rationale = (
        "Time-series momentum is one of the best-documented effects in markets. Blending several horizons "
        "diversifies across trend speeds, a continuous signal scales in and out gradually instead of flipping, "
        "and the bending response cuts exposure when a move is so stretched that reversals become likely."
    )
    failure_modes = (
        "Trendless, choppy markets bleed small losses; sharp V-shaped reversals hurt because every horizon "
        "lags. Like all trend following, returns come in bursts separated by long flat or losing stretches."
    )
    evidence = Evidence.MODERATE
    evidence_text = (
        "The effect it harvests has strong evidence (Moskowitz, Ooi & Pedersen 2012; Hurst, Ooi & Pedersen "
        "2017). This exact formulation comes from Man Group research (Baz et al. 2015, a working paper) and "
        "is the standard benchmark in academic work on trend following (Lim, Zohren & Roberts 2019). Evidence "
        "that it beats simpler trend rules out of sample is mixed."
    )
    references = (BAZ_2015, LZR_2019, MOP_2012, HOP_2017)
    params_spec = (
        Param(
            "speed",
            "Trend horizons",
            "standard",
            "choice",
            choices=tuple(TREND_HORIZONS),
            help="Fast: 4/12, 8/24, 16/48 bars. Standard: 8/24, 16/48, 32/96. Slow: 16/48, 32/96, 64/192.",
        ),
        Param("price_window", "Price volatility window (bars)", 63, "int", 20, 252, 1),
        Param("signal_window", "Signal normalisation window (bars)", 252, "int", 63, 756, 1),
        Param(
            "step",
            "Signal step (0 = continuous)",
            0.25,
            "float",
            0.0,
            1.0,
            0.05,
            help="Round the position to this step so small wiggles don't trigger trades.",
        ),
        Param("rebalance", "Re-evaluate every (bars)", 5, "int", 1, 63, 1),
        Param("long_only", "Long only", False, "bool"),
    )
    default_sizing = {"method": "vol_target", "target_vol": 0.15}
    overlays = (Overlay("strength", "Trend strength", "lower", levels=(0.0,)),)
    uses_short = True

    def warmup(self) -> int:
        return self.params["price_window"] + self.params["signal_window"]

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        close = df["close"]
        price_sd = close.rolling(p["price_window"]).std()
        cols: dict[str, Any] = {}
        responses = []
        for k, (fast, slow) in enumerate(TREND_HORIZONS[p["speed"]], 1):
            gap = (
                close.ewm(alpha=1.0 / fast, adjust=False).mean()
                - close.ewm(alpha=1.0 / slow, adjust=False).mean()
            )
            q = gap / price_sd
            y = q / q.rolling(p["signal_window"]).std()
            phi = trend_response(y)
            cols[f"y{k}"], cols[f"phi{k}"] = y, phi
            responses.append(phi)
        strength = sum(responses) / len(responses)
        raw = strength.clip(lower=0.0) if p["long_only"] else strength
        if p["step"] > 0:
            raw = (raw / p["step"]).round() * p["step"]
        raw = raw.clip(-1.0, 1.0)
        return pd.DataFrame(
            {
                "signal": hold_every(raw, p["rebalance"]),
                "strength": strength,
                **cols,
                "check": check_bars(raw, p["rebalance"]),
                "valid": raw.notna(),
            },
            index=df.index,
        )

    def describe_row(self, diag, t):
        p = self.params
        strength = diag["strength"].iloc[t]
        held = diag["signal"].iloc[t]
        rules = []
        ups = downs = 0
        for k, (fast, slow) in enumerate(TREND_HORIZONS[p["speed"]], 1):
            y, phi = diag[f"y{k}"].iloc[t], diag[f"phi{k}"].iloc[t]
            ups += bool(phi > 0.05)
            downs += bool(phi < -0.05)
            rules.append(
                Rule(
                    f"{fast}/{slow}-bar trend (normalised)",
                    f"{fmt_num(y)} -> response {fmt_num(phi)}",
                    None if abs(phi) <= 0.05 else bool(phi > 0),
                )
            )
        rules.append(Rule("Combined strength", fmt_num(strength), None))
        checks = np.flatnonzero(diag["check"].to_numpy(bool)[: t + 1])
        ago = t - int(checks[-1]) if len(checks) else 0
        rules.append(
            Rule("Re-evaluation", f"every {p['rebalance']} bars; next in {p['rebalance'] - ago}", None)
        )
        stretched = any(abs(diag[f"y{k}"].iloc[t]) > 2.5 for k in (1, 2, 3))
        pos = held_position_text(held)
        size = f"{abs(held):.0%} of full size" if pos != "flat" else "no position"
        if pos == "flat":
            head = (
                f"No clear trend (strength {fmt_num(strength)}; {ups} of 3 horizons up, {downs} down): flat."
            )
        else:
            direction = "Uptrend" if held > 0 else "Downtrend"
            head = (
                f"{direction} across {ups if held > 0 else downs} of 3 horizons (strength {fmt_num(strength)}): "
                f"{pos} at {size}."
            )
        if stretched:
            head += " The move is stretched, so the response curve is already scaling back."
        return head, rules

    def exit_rule(self, state):
        return (
            "Scales down as the trend fades and reverses when the horizons turn the other way "
            f"(re-evaluated every {self.params['rebalance']} bars)."
        )


# --------------------------------------------------------------------------------------
# 2. Factor-residual statistical arbitrage (Avellaneda & Lee 2010)
# --------------------------------------------------------------------------------------


def ou_s_scores(r: np.ndarray, f: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Avellaneda-Lee s-score of each bar's trailing window.

    Regress the stock's returns on its market factor, cumulate the residuals into X, fit
    X(k+1) = a + b X(k) (an Ornstein-Uhlenbeck process sampled daily) and return
    (s-score, beta, mean-reversion time in bars). s = (X_last - m) / sigma_eq, with the
    equilibrium m = a / (1 - b) and sigma_eq = sqrt(var(zeta) / (1 - b^2)).
    """
    T = len(r)
    if T < w:
        empty = np.full(T, np.nan)
        return empty, empty.copy(), empty.copy()
    beta, dR, dF = _window_regression(r, f, w)
    X = np.cumsum(dR - beta[:, None] * dF, axis=1)
    x0, x1 = X[:, :-1], X[:, 1:]
    d0 = x0 - x0.mean(axis=1, keepdims=True)
    d1 = x1 - x1.mean(axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        b = (d0 * d1).mean(axis=1) / (d0**2).mean(axis=1)
        a = x1.mean(axis=1) - b * x0.mean(axis=1)
        zeta = x1 - a[:, None] - b[:, None] * x0
        ok = np.isfinite(b) & (b > 0) & (b < 1)
        m = np.where(ok, a / (1 - b), np.nan)
        sigma_eq = np.where(ok, np.sqrt((zeta**2).mean(axis=1) / (1 - b**2)), np.nan)
        s = np.where(ok & (sigma_eq > 0), (X[:, -1] - m) / sigma_eq, np.nan)
        tau = np.where(ok, -1.0 / np.log(b), np.nan)
    return _full(T, w, s), _full(T, w, beta), _full(T, w, tau)


class ResidualReversion(Strategy):
    id = "stat_arb"
    name = "Statistical Arbitrage (Factor Residuals)"
    category = "Statistical arbitrage"
    kind = Kind.CROSS_SECTIONAL
    family = "modern"
    counterpart = "st_reversal"
    min_symbols = 4
    uses_short = True
    needs = "4+ related stocks that move together (one sector works best) and short selling."
    summary = (
        "The classic quant-fund stat-arb model: strip each stock's market-driven moves out, treat what is left "
        "as a mean-reverting process, and trade the stocks whose residual has drifted unusually far, hedged "
        "against the others."
    )
    rules_text = (
        "Each stock's market factor is the average return of the other stocks. Over a 60-bar window, regress "
        "the stock's returns on it; the residuals are the stock-specific part.",
        "Cumulate the residuals and fit an Ornstein-Uhlenbeck (mean-reverting) process; skip stocks whose "
        "residual takes longer than 30 bars to revert.",
        "s-score = distance of the residual from its equilibrium in standard deviations. Buy below -1.25, "
        "short above +1.25; close longs above -0.5 and shorts below +0.75.",
        "Hedge each position by trading the other stocks in the opposite direction (beta-weighted), so the "
        "book is roughly market-neutral. The hedge ratio is locked when a trade opens.",
    )
    rationale = (
        "Market and sector moves explain most of a stock's daily return; the stock-specific remainder is "
        "partly noise from temporary order imbalances, which liquidity providers are paid to absorb. Betting "
        "that it reverts, while hedging out the market, earns that liquidity premium."
    )
    failure_modes = (
        "A residual can move for a real reason (earnings, a takeover) and never come back. Crowded unwinds "
        "hit every stat-arb book at once, as in August 2007 (Khandani & Lo 2011). Turnover is high, so costs "
        "matter a lot, and returns have decayed as more capital chases the effect."
    )
    evidence = Evidence.MODERATE
    evidence_text = (
        "Avellaneda & Lee (2010) report Sharpe ratios around 1.4 for this model on US stocks in 1997-2007 "
        "(using industry ETFs or principal components as factors), with performance weakening after 2002. "
        "Here the factor is simply the average of the other symbols, which is cruder than their setup."
    )
    references = (AL_2010, KHANDANI_LO_2011)
    params_spec = (
        Param("window", "Estimation window (bars)", 60, "int", 30, 252, 1),
        Param("entry", "Open at |s-score| above", 1.25, "float", 0.5, 3.0, 0.05),
        Param("exit_long", "Close longs when s rises above minus", 0.5, "float", 0.0, 2.0, 0.05),
        Param("exit_short", "Close shorts when s falls below", 0.75, "float", 0.0, 2.0, 0.05),
        Param("max_reversion", "Max mean-reversion time (bars)", 30, "int", 5, 126, 1),
        Param("hedge", "Hedge with the other stocks", True, "bool"),
    )
    default_sizing = {"method": "fixed", "allocation": 1.0, "max_leverage": 2.0}
    overlays = (Overlay("s_score", "Residual s-score", "lower", levels=(-1.25, -0.5, 0.75, 1.25)),)

    def validate(self):
        p = self.params
        if p["exit_long"] >= p["entry"] or p["exit_short"] >= p["entry"]:
            raise ValueError("Exit levels must be inside the entry level")

    def overlay_levels(self, overlay: Overlay) -> tuple[float, ...]:
        if overlay.column != "s_score":
            return overlay.levels
        p = self.params
        return (-p["entry"], -p["exit_long"], p["exit_short"], p["entry"])

    def warmup(self) -> int:
        return self.params["window"] + 1

    def run(self, data: dict[str, pd.DataFrame]) -> StrategyOutput:
        self.check_symbols(list(data))
        p = self.params
        symbols = list(data)
        index = data[symbols[0]].index
        rets = _log_returns(data, symbols)
        mkt = _market_ex_self(rets)
        T, N = rets.shape
        S, B, TAU = (np.full((T, N), np.nan) for _ in range(3))
        for j in range(N):
            S[:, j], B[:, j], TAU[:, j] = ou_s_scores(rets[:, j], mkt[:, j], p["window"])

        state = np.zeros((T, N))
        locked = np.full((T, N), np.nan)
        for j in range(N):
            pos, lb = 0.0, np.nan
            for t in range(T):
                s = S[t, j]
                if pos != 0:
                    if (
                        not np.isfinite(s)
                        or (pos > 0 and s > -p["exit_long"])
                        or (pos < 0 and s < p["exit_short"])
                    ):
                        pos, lb = 0.0, np.nan
                if pos == 0 and np.isfinite(s) and TAU[t, j] <= p["max_reversion"]:
                    if s < -p["entry"]:
                        pos, lb = 1.0, B[t, j]
                    elif s > p["entry"]:
                        pos, lb = -1.0, B[t, j]
                state[t, j], locked[t, j] = pos, lb

        unit = 1.0 / N
        own = state * unit
        weights = own.copy()
        if p["hedge"] and N > 1:
            hedge = own * np.nan_to_num(locked)  # beta-dollars of factor exposure per position
            weights -= (hedge.sum(axis=1, keepdims=True) - hedge) / (N - 1)
        valid = pd.Series(np.arange(T) >= p["window"], index=index)
        diags = {
            sym: pd.DataFrame(
                {
                    "s_score": S[:, j],
                    "beta": B[:, j],
                    "tau": TAU[:, j],
                    "state": state[:, j],
                    "locked_beta": locked[:, j],
                    "weight": weights[:, j],
                    "valid": valid,
                },
                index=index,
            )
            for j, sym in enumerate(symbols)
        }
        return StrategyOutput(pd.DataFrame(weights, index=index, columns=symbols), diags)

    def describe(self, output, symbol, t):
        p = self.params
        d = output.diagnostics[symbol]
        s, beta, tau = d["s_score"].iloc[t], d["beta"].iloc[t], d["tau"].iloc[t]
        state, w, lb = d["state"].iloc[t], d["weight"].iloc[t], d["locked_beta"].iloc[t]
        rules = [
            Rule("Residual s-score", fmt_num(s), None if not np.isfinite(s) else bool(abs(s) >= p["entry"])),
            Rule(
                "Open / close levels",
                f"open at |s| >= {p['entry']:g}; close longs above -{p['exit_long']:g}, shorts below {p['exit_short']:g}",
                None,
            ),
            Rule("Beta to the other stocks", fmt_num(beta), None),
            Rule(
                "Mean-reversion time",
                f"{fmt_num(tau, 1)} bars (max {p['max_reversion']})",
                None if not np.isfinite(tau) else bool(tau <= p["max_reversion"]),
            ),
            Rule("Net weight incl. hedges", fmt_pct(w), None),
        ]
        if state > 0:
            head = (
                f"{symbol} is cheap versus the others (s = {fmt_num(s)}): long it, hedged by shorting the rest "
                f"(beta {fmt_num(lb)})."
            )
        elif state < 0:
            head = (
                f"{symbol} is rich versus the others (s = {fmt_num(s)}): short it, hedged by buying the rest "
                f"(beta {fmt_num(lb)})."
            )
        elif np.isfinite(tau) and tau > p["max_reversion"]:
            head = f"No trade: {symbol}'s residual reverts too slowly (about {fmt_num(tau, 0)} bars) to arbitrage."
        elif not np.isfinite(s):
            head = f"No trade: {symbol}'s residual shows no mean reversion in this window."
        else:
            head = f"{symbol} is fairly priced versus the others (s = {fmt_num(s)})."
        if state == 0 and abs(w) > 1e-9:
            head += f" It carries a {fmt_pct(w)} hedge for the other positions."
        return head, rules

    def position_states(self, output, symbol):
        states = super().position_states(output, symbol)
        traded = output.diagnostics[symbol]["state"].to_numpy(float) != 0
        states[~traded & (states != "flat")] = "hedge"  # held only to offset the other positions
        return states

    def exit_rule(self, state):
        p = self.params
        if state == "hedge":
            return "The hedge changes as the positions it offsets open and close."
        return f"Closes when the s-score comes back (longs above -{p['exit_long']:g}, shorts below {p['exit_short']:g})."


# --------------------------------------------------------------------------------------
# 3. Residual momentum (Blitz, Huij & Martens 2011)
# --------------------------------------------------------------------------------------


class ResidualMomentum(CrossSectionalStrategy):
    id = "residual_momentum"
    name = "Residual Momentum"
    category = "Momentum"
    family = "modern"
    counterpart = "xs_momentum"
    min_symbols = 4
    needs = "4+ stocks; the more, the better the ranking."
    metric_label = "Residual momentum (t-stat)"
    metric_is_pct = False
    summary = (
        "Momentum on the stock-specific part of returns: rank stocks by how consistently they beat what their "
        "market exposure explains, not by raw return, and hold the strongest."
    )
    rules_text = (
        "Estimate each stock's beta to the average of the other stocks over the past year.",
        "Residual return = return - beta x market return. Score = the t-statistic of the mean residual return "
        "from 12 months ago to 1 month ago (so a steady outperformer beats a lucky one).",
        "Every 21 bars, buy the top 30% (optionally short the bottom 30%).",
    )
    rationale = (
        "Raw momentum is partly a hidden bet on the market: after a rally the winners are high-beta stocks, "
        "so the strategy crashes when the market rebounds sharply. Ranking on residual returns removes that "
        "time-varying beta and keeps the stock-specific trend."
    )
    failure_modes = (
        "Still a momentum strategy: sharp reversals in stock-specific trends hurt. With few symbols the "
        "ranking is noisy; academic results use thousands of stocks."
    )
    evidence = Evidence.MODERATE
    evidence_text = (
        "Blitz, Huij & Martens (2011) found residual momentum earned similar returns to total-return momentum "
        "on US stocks (1930-2009) with about half the volatility, and far smaller losses in momentum crashes "
        "(Daniel & Moskowitz 2016). Their residuals come from the Fama-French three-factor model; this version "
        "uses a single market factor."
    )
    references = (BHM_2011, DM_2016, JT_1993)
    params_spec = (
        Param("lookback", "Formation period (bars)", 252, "int", 63, 504, 1),
        Param("skip", "Skip recent (bars)", 21, "int", 0, 63, 1),
        Param("rebalance", "Rebalance every (bars)", 21, "int", 1, 126, 1),
        Param("top_frac", "Basket size (fraction)", 0.3, "float", 0.1, 0.5, 0.05),
        Param("allow_short", "Short the laggards", False, "bool"),
    )

    def validate(self):
        if self.params["skip"] >= self.params["lookback"] - 20:
            raise ValueError("The skip period must leave at least 20 bars of formation period")

    def warmup(self) -> int:
        return self.params["lookback"] + 1

    def metric(self, data):
        p = self.params
        symbols = list(data)
        index = data[symbols[0]].index
        rets = _log_returns(data, symbols)
        mkt = _market_ex_self(rets)
        T, N = rets.shape
        L, skip = p["lookback"], p["skip"]
        out = np.full((T, N), np.nan)
        if T >= L:
            for j in range(N):
                beta, _, _ = _window_regression(rets[:, j], mkt[:, j], L)
                Rw = sliding_window_view(rets[:, j], L)
                Fw = sliding_window_view(mkt[:, j], L)
                resid = (Rw - beta[:, None] * Fw)[:, : L - skip]  # formation part, alpha included
                n = resid.shape[1]
                with np.errstate(divide="ignore", invalid="ignore"):
                    tstat = resid.mean(axis=1) / (resid.std(axis=1, ddof=1) / math.sqrt(n))
                out[L - 1 :, j] = tstat
        return pd.DataFrame(out, index=index, columns=symbols)


# --------------------------------------------------------------------------------------
# 4. Kalman-filter pairs trading
# --------------------------------------------------------------------------------------

KALMAN_ADAPT = {"slow": 1e-7, "medium": 1e-6, "fast": 1e-5}


def kalman_hedge(y: np.ndarray, x: np.ndarray, warmup: int, delta: float, noise_halflife: float = 60.0):
    """Track y(t) = beta(t) x(t) + alpha(t) + e(t) with a random-walk state (beta, alpha).

    Returns per bar: z (one-step forecast error / its predicted sd), beta, alpha, error.
    Starts from OLS on the first ``warmup`` bars *with that regression's own uncertainty*
    (so the filter keeps learning the hedge ratio instead of trusting a short sample);
    the observation-noise variance follows an EWMA of past forecast errors, so z-scores stay
    in scale. Only data up to t is used.
    """
    T = len(y)
    z, beta, alpha, err = (np.full(T, np.nan) for _ in range(4))
    if T <= warmup:
        return z, beta, alpha, err
    H0 = np.column_stack([x[:warmup], np.ones(warmup)])
    theta, *_ = np.linalg.lstsq(H0, y[:warmup], rcond=None)
    ve = float(np.var(y[:warmup] - H0 @ theta)) or 1e-6
    P = ve * np.linalg.pinv(H0.T @ H0)  # OLS coefficient covariance, including the beta/alpha trade-off
    xm = float(x[:warmup].mean())
    # Scale the hedge-ratio noise so both states can move the prediction by similar amounts.
    wv = np.diag(delta * np.array([1.0 / max(xm * xm, 1e-6), 1.0]))
    lam = 0.5 ** (1.0 / noise_halflife)
    beta[warmup - 1], alpha[warmup - 1] = theta
    for t in range(warmup, T):
        R = P + wv
        H = np.array([x[t], 1.0])
        Q = float(H @ R @ H) + ve
        e = float(y[t] - H @ theta)
        z[t], err[t] = e / math.sqrt(Q), e
        K = (R @ H) / Q
        theta = theta + K * e
        P = R - np.outer(K, H) @ R
        ve = lam * ve + (1 - lam) * e * e
        beta[t], alpha[t] = theta
    return z, beta, alpha, err


class KalmanPairs(Strategy):
    id = "kalman_pairs"
    name = "Pairs Trading (Kalman Filter)"
    category = "Statistical arbitrage"
    kind = Kind.PAIR
    family = "modern"
    counterpart = "pairs_trading"
    min_symbols = 2
    max_symbols = 2
    uses_short = True
    needs = "Two closely linked stocks or ETFs (e.g. two share classes, or rivals in one industry)."
    summary = (
        "Pairs trading with a hedge ratio that updates every bar: a Kalman filter predicts one price from the "
        "other and the strategy trades when the prediction error is unusually large."
    )
    rules_text = (
        "Model log(A) = beta x log(B) + alpha, where beta and alpha drift slowly as random walks. A Kalman "
        "filter updates both after every bar.",
        "z = today's forecast error divided by its predicted standard deviation (before seeing today).",
        "z < -1.5: A is cheap relative to B, so buy A and sell beta x B; z > +1.5: the reverse.",
        "Exit when z crosses back through 0; stop out at |z| >= 4. The hedge ratio is locked while a trade "
        "is open.",
    )
    rationale = (
        "A fixed rolling-window hedge ratio reacts late and jumps when old data leaves the window. The Kalman "
        "filter weighs each new bar by how informative it is, so the hedge ratio adapts smoothly to slow "
        "changes in the relationship while short-lived deviations still show up as trading signals."
    )
    failure_modes = (
        "If the filter adapts too fast it absorbs the very deviations you want to trade; too slow and it "
        "trades a relationship that has already changed. A genuine break (a merger, diverging businesses) can "
        "still produce large losses before the filter catches up."
    )
    evidence = Evidence.PRACTITIONER
    evidence_text = (
        "Elliott, van der Hoek & Malcolm (2005) model the spread as a mean-reverting state observed with noise; "
        "Chan (2013) popularised the dynamic-hedge-ratio version among practitioners. It is widely used, but "
        "there is little independent evidence that it beats simpler pairs rules after costs."
    )
    references = (CHAN_2013, EHM_2005)
    params_spec = (
        Param(
            "adapt",
            "Adaptation speed",
            "medium",
            "choice",
            choices=tuple(KALMAN_ADAPT),
            help="How fast the hedge ratio may drift: slow trusts history, fast follows recent prices.",
        ),
        Param("warmup_bars", "Initial estimation (bars)", 120, "int", 40, 504, 1),
        Param("entry_z", "Entry |z|", 1.5, "float", 0.5, 4.0, 0.1),
        Param("exit_z", "Exit |z|", 0.0, "float", 0.0, 2.0, 0.1),
        Param("stop_z", "Stop |z| (0 = off)", 4.0, "float", 0.0, 8.0, 0.1),
    )
    overlays = (Overlay("z", "Forecast-error z-score", "lower", levels=(-1.5, 0.0, 1.5)),)

    def validate(self):
        p = self.params
        if p["exit_z"] >= p["entry_z"]:
            raise ValueError("Exit |z| must be below entry |z|")
        if p["stop_z"] and p["stop_z"] <= p["entry_z"]:
            raise ValueError("Stop |z| must exceed entry |z| (or be 0 to disable)")

    def overlay_levels(self, overlay: Overlay) -> tuple[float, ...]:
        if overlay.column != "z":
            return overlay.levels
        return symmetric_levels(self.params["entry_z"], self.params["exit_z"])

    def warmup(self) -> int:
        return self.params["warmup_bars"] + 1

    def run(self, data: dict[str, pd.DataFrame]) -> StrategyOutput:
        self.check_symbols(list(data))
        p = self.params
        (sym_a, df_a), (sym_b, df_b) = list(data.items())
        y = np.log(df_a["close"].to_numpy(float))
        x = np.log(df_b["close"].to_numpy(float))
        z, beta, alpha, err = kalman_hedge(y, x, p["warmup_bars"], KALMAN_ADAPT[p["adapt"]])
        T = len(z)
        state = np.zeros(T)
        locked = np.full(T, np.nan)
        pos, lb = 0.0, np.nan
        for t in range(T):
            if not np.isfinite(z[t]):
                continue
            if pos != 0:
                done = (pos > 0 and z[t] >= -p["exit_z"]) or (pos < 0 and z[t] <= p["exit_z"])
                stopped = p["stop_z"] and abs(z[t]) >= p["stop_z"]
                if done or stopped:
                    pos, lb = 0.0, np.nan
            if pos == 0 and abs(z[t]) >= p["entry_z"] and (not p["stop_z"] or abs(z[t]) < p["stop_z"]):
                pos, lb = (-1.0 if z[t] > 0 else 1.0), beta[t]  # latest estimate, known at the close
            state[t], locked[t] = pos, lb
        gross = 1.0 + np.abs(np.nan_to_num(locked))
        w_a = np.where(state != 0, state / gross, 0.0)
        w_b = np.where(state != 0, -state * np.nan_to_num(locked) / gross, 0.0)
        index = df_a.index
        diag = pd.DataFrame(
            {
                "z": z,
                "beta": beta,
                "alpha": alpha,
                "error": err,
                "state": state,
                "locked_beta": locked,
                "valid": np.isfinite(z),
            },
            index=index,
        )
        return StrategyOutput(
            pd.DataFrame({sym_a: w_a, sym_b: w_b}, index=index),
            {sym_a: diag, sym_b: diag.copy(), "__pair__": diag},
        )

    def explain(self, output: StrategyOutput, symbol: str, at: int = -1) -> Explanation:
        p = self.params
        syms = list(output.signals.columns)
        diag = output.diagnostics[symbol]
        t = at if at >= 0 else len(diag) + at
        if not bool(diag["valid"].iloc[t]):
            return Explanation(
                symbol, "warming_up", 0.0, f"Needs {self.warmup()} bars of history.", [], None, False
            )
        sig = output.signals[symbol]
        value = float(sig.iloc[t])
        since, fresh = signal_since(sig, t)
        z, beta, state = diag["z"].iloc[t], diag["beta"].iloc[t], diag["state"].iloc[t]
        before = diag["beta"].iloc[max(t - 20, 0)]
        a_sym, b_sym = syms
        rules = [
            Rule("Forecast-error z-score", fmt_num(z), bool(abs(z) >= p["entry_z"])),
            Rule("Entry / exit", f"|z| >= {p['entry_z']:g} / z crosses {p['exit_z']:g}", None),
            Rule(f"Hedge ratio now (log {a_sym} on log {b_sym})", fmt_num(beta, 3), None),
            Rule("Hedge ratio 20 bars ago", fmt_num(before, 3), None),
            Rule("Adaptation speed", p["adapt"], None),
        ]
        if state > 0:
            head = (
                f"{a_sym} trades below what {b_sym} predicts (z = {fmt_num(z)}): long {a_sym}, short {b_sym}."
            )
        elif state < 0:
            head = (
                f"{a_sym} trades above what {b_sym} predicts (z = {fmt_num(z)}): short {a_sym}, long {b_sym}."
            )
        else:
            head = f"Forecast error z = {fmt_num(z)} is inside the entry band: no trade."
        st = "long" if value > 1e-12 else "short" if value < -1e-12 else "flat"
        exit_rule = f"Exit when z crosses {p['exit_z']:g}" + (
            f" or stop at |z| >= {p['stop_z']:g}." if p["stop_z"] else "."
        )
        return Explanation(symbol, st, value, head, rules, since, fresh, exit_rule if state else "")


# --------------------------------------------------------------------------------------
# 5. Hidden Markov regime model
# --------------------------------------------------------------------------------------

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _normal_pdf(r: np.ndarray, mu: float, sd: float) -> list[float]:
    return (np.exp(-0.5 * ((r - mu) / sd) ** 2) / (sd * _SQRT_2PI) + 1e-300).tolist()


def hmm_fit(r: np.ndarray, init: dict[str, Any], n_iter: int) -> dict[str, Any]:
    """Baum-Welch (EM) for a two-state Gaussian hidden Markov model on returns ``r``.

    Written for two states with plain floats (fast enough to refit every few weeks inside a
    backtest). Returns the parameters and the filtered probabilities at the last bar.
    """
    pi0, pi1 = init["pi"]
    (a00, a01), (a10, a11) = init["A"]
    mu0, mu1 = init["mu"]
    s0, s1 = init["sd"]
    T = len(r)
    floor = max(float(np.std(r)) * 0.05, 1e-8)
    f0 = f1 = 0.5
    for _ in range(n_iter):
        b0, b1 = _normal_pdf(r, mu0, s0), _normal_pdf(r, mu1, s1)
        al0, al1, cs = [0.0] * T, [0.0] * T, [0.0] * T
        p0, p1 = pi0 * b0[0], pi1 * b1[0]
        c = p0 + p1
        al0[0], al1[0], cs[0] = p0 / c, p1 / c, c
        for t in range(1, T):
            q0, q1 = al0[t - 1], al1[t - 1]
            p0 = (q0 * a00 + q1 * a10) * b0[t]
            p1 = (q0 * a01 + q1 * a11) * b1[t]
            c = p0 + p1
            al0[t], al1[t], cs[t] = p0 / c, p1 / c, c
        f0, f1 = al0[-1], al1[-1]
        # Backward pass, accumulating the expected sufficient statistics on the way.
        be0 = be1 = 1.0
        g0s = g1s = g0r = g1r = g0rr = g1rr = 0.0
        x00 = x01 = x10 = x11 = 0.0
        g0_first = g1_first = 0.0
        for t in range(T - 1, -1, -1):
            g0, g1 = al0[t] * be0, al1[t] * be1
            gn = g0 + g1
            g0, g1 = g0 / gn, g1 / gn
            rt = float(r[t])
            g0s += g0
            g1s += g1
            g0r += g0 * rt
            g1r += g1 * rt
            g0rr += g0 * rt * rt
            g1rr += g1 * rt * rt
            if t == 0:
                g0_first, g1_first = g0, g1
                break
            n0, n1 = b0[t] * be0, b1[t] * be1
            c = cs[t]
            q0, q1 = al0[t - 1], al1[t - 1]
            x00 += q0 * a00 * n0 / c
            x01 += q0 * a01 * n1 / c
            x10 += q1 * a10 * n0 / c
            x11 += q1 * a11 * n1 / c
            be0, be1 = (a00 * n0 + a01 * n1) / c, (a10 * n0 + a11 * n1) / c
        pi0, pi1 = g0_first, g1_first
        row0, row1 = x00 + x01, x10 + x11
        if row0 > 0:
            a00, a01 = x00 / row0, x01 / row0
        if row1 > 0:
            a10, a11 = x10 / row1, x11 / row1
        if g0s > 1e-9:
            mu0 = g0r / g0s
            s0 = max(math.sqrt(max(g0rr / g0s - mu0 * mu0, 0.0)), floor)
        if g1s > 1e-9:
            mu1 = g1r / g1s
            s1 = max(math.sqrt(max(g1rr / g1s - mu1 * mu1, 0.0)), floor)
    return {
        "pi": (pi0, pi1),
        "A": ((a00, a01), (a10, a11)),
        "mu": (mu0, mu1),
        "sd": (s0, s1),
        "filtered": (f0, f1),
    }


_FIT_CACHE: OrderedDict[bytes, dict[str, Any]] = OrderedDict()
_FIT_CACHE_LOCK = threading.Lock()
_FIT_CACHE_SIZE = 10_000


def _hmm_fit_cached(r: np.ndarray, init: dict[str, Any], n_iter: int) -> dict[str, Any]:
    """``hmm_fit`` remembered by its exact inputs.

    Simulations recompute signals as bars arrive. Every refit before the newest bars sees the
    same returns and starting point as last time, so only the new refits cost anything.
    """
    key = hashlib.blake2b(
        np.ascontiguousarray(r, dtype=float).tobytes() + repr((init, n_iter)).encode(), digest_size=16
    ).digest()
    with _FIT_CACHE_LOCK:
        fit = _FIT_CACHE.get(key)
        if fit is not None:
            _FIT_CACHE.move_to_end(key)
            return fit
    fit = hmm_fit(r, init, n_iter)
    with _FIT_CACHE_LOCK:
        _FIT_CACHE[key] = fit
        while len(_FIT_CACHE) > _FIT_CACHE_SIZE:
            _FIT_CACHE.popitem(last=False)
    return fit


def hmm_filter(
    returns: np.ndarray,
    train_min: int,
    train_max: int,
    refit: int,
    min_vol_ratio: float = 1.0,
    first_iter: int = 30,
    warm_iter: int = 5,
):
    """Filtered probability of the calm (lower-volatility) regime at every bar.

    The model is re-estimated every ``refit`` bars on the trailing ``train_max`` returns and
    run forward in between, so the probability at bar t only ever uses returns up to t. If the
    two fitted regimes' volatilities differ by less than ``min_vol_ratio``, the window holds no
    real turbulence to contrast with, so the market counts as one calm regime (p = 1).
    Returns per-bar estimates (in return units) and the refit mask.
    """
    T = len(returns)
    keys = ("p", "mu_c", "sd_c", "mu_t", "sd_t", "stay_c", "stay_t", "separated")
    out = {k: np.full(T, np.nan) for k in keys}
    refit_mask = np.zeros(T, dtype=bool)
    finite = np.flatnonzero(np.isfinite(returns))
    if len(finite) < train_min:
        return out, refit_mask
    start = int(finite[0])
    first = start + train_min - 1
    params: dict[str, Any] | None = None
    f0 = f1 = 0.5
    for t in range(first, T):
        if (t - first) % refit == 0:
            window = returns[max(start, t - train_max + 1) : t + 1]
            if params is None:
                sd = float(np.std(window)) or 1e-4
                mu = float(np.mean(window))
                init = {
                    "pi": (0.5, 0.5),
                    "A": ((0.97, 0.03), (0.06, 0.94)),
                    "mu": (mu, mu),
                    "sd": (0.7 * sd, 1.5 * sd),
                }
                params = _hmm_fit_cached(window, init, first_iter)
            else:
                params = _hmm_fit_cached(window, params, warm_iter)
            f0, f1 = params["filtered"]
            refit_mask[t] = True
        else:
            assert params is not None
            (a00, a01), (a10, a11) = params["A"]
            (mu0, mu1), (s0, s1) = params["mu"], params["sd"]
            rt = returns[t]
            if np.isfinite(rt):
                b0 = math.exp(-0.5 * ((rt - mu0) / s0) ** 2) / s0 + 1e-300
                b1 = math.exp(-0.5 * ((rt - mu1) / s1) ** 2) / s1 + 1e-300
                p0, p1 = (f0 * a00 + f1 * a10) * b0, (f0 * a01 + f1 * a11) * b1
                f0, f1 = p0 / (p0 + p1), p1 / (p0 + p1)
        (a00, _), (_, a11) = params["A"]
        calm = 0 if params["sd"][0] <= params["sd"][1] else 1
        turb = 1 - calm
        separated = params["sd"][turb] >= min_vol_ratio * params["sd"][calm]
        out["separated"][t] = float(separated)
        out["p"][t] = (f0 if calm == 0 else f1) if separated else 1.0
        out["mu_c"][t], out["sd_c"][t] = params["mu"][calm], params["sd"][calm]
        out["mu_t"][t], out["sd_t"][t] = params["mu"][turb], params["sd"][turb]
        stay = (a00, a11)
        out["stay_c"][t], out["stay_t"][t] = stay[calm], stay[turb]
    return out, refit_mask


class RegimeSwitching(SingleAssetStrategy):
    id = "hmm_regime"
    name = "Regime Switching (Hidden Markov Model)"
    category = "Regime detection"
    family = "modern"
    counterpart = "faber_trend"
    needs = "A broad index or ETF with a few years of history to learn calm and turbulent periods from."
    summary = (
        "A hidden Markov model infers whether the market is in a calm or a turbulent regime from its returns, "
        "and the strategy is invested only while the calm regime is likely."
    )
    rules_text = (
        "Fit a two-regime hidden Markov model (each regime has its own mean and volatility, and a probability "
        "of switching) to the past returns with the EM algorithm; refit every 21 bars.",
        "Between refits, update the probability of each regime after every bar (the forward filter).",
        "Invest when the calm regime's probability rises above 60%; go to cash when it falls below 40%.",
        "Optionally short when the turbulent regime is likely and its estimated mean return is negative.",
    )
    rationale = (
        "Volatility clusters: calm markets tend to stay calm and turbulent ones turbulent, and most large "
        "drawdowns happen in the turbulent state. Stepping aside when that state becomes likely aims to keep "
        "most of the return with less of the risk."
    )
    failure_modes = (
        "The model only recognises a new regime after some turbulent bars have already happened, and it can "
        "exit right before a sharp rebound (turbulent regimes contain the best days as well as the worst). "
        "Re-estimated parameters shift over time, so the same probability can mean different things. With no "
        "turbulence in the training window there is nothing to contrast with; the strategy then treats the "
        "market as one calm regime and stays invested."
    )
    evidence = Evidence.MODERATE
    evidence_text = (
        "Regime-switching models are standard in finance since Hamilton (1989). Ang & Bekaert (2002), Kritzman, "
        "Page & Turkington (2012) and Nystrup, Madsen & Lindstrom (2018) find regime-based allocation can cut "
        "drawdowns and improve risk-adjusted returns, but results depend on the sample and the benefit after "
        "costs is modest."
    )
    references = (HAMILTON_1989, ANG_BEKAERT_2002, KPT_2012, NML_2018)
    params_spec = (
        Param("train_min", "Minimum training (bars)", 250, "int", 120, 750, 1),
        Param("train_max", "Training window (bars)", 1000, "int", 250, 2500, 1),
        Param("refit", "Refit every (bars)", 21, "int", 5, 126, 1),
        Param("enter", "Invest above calm probability", 0.6, "float", 0.5, 0.95, 0.05),
        Param("exit", "Exit below calm probability", 0.4, "float", 0.05, 0.5, 0.05),
        Param(
            "min_vol_ratio",
            "Minimum volatility ratio",
            1.5,
            "float",
            1.0,
            4.0,
            0.1,
            help="The turbulent regime must be at least this many times as volatile as the calm one to count as "
            "a separate regime; otherwise the market is treated as calm (1.0 = always trust the split).",
        ),
        Param("allow_short", "Short in a falling turbulent regime", False, "bool"),
    )
    overlays = (Overlay("p_calm", "Calm-regime probability", "lower", levels=(0.4, 0.6)),)

    def validate(self):
        p = self.params
        if p["exit"] >= p["enter"]:
            raise ValueError("The exit probability must be below the entry probability")
        if p["train_max"] < p["train_min"]:
            raise ValueError("The training window must be at least the minimum training length")

    def overlay_levels(self, overlay: Overlay) -> tuple[float, ...]:
        if overlay.column != "p_calm":
            return overlay.levels
        return (self.params["exit"], self.params["enter"])

    def warmup(self) -> int:
        return self.params["train_min"] + 1

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        close = df["close"].to_numpy(float)
        rets = np.full(len(close), np.nan)
        rets[1:] = np.diff(np.log(close)) * 100.0  # percent: better-conditioned densities
        est, refits = hmm_filter(rets, p["train_min"], p["train_max"], p["refit"], p["min_vol_ratio"])
        pc = est["p"]
        signal = np.zeros(len(close))
        pos = 0.0
        for t in range(len(close)):
            if not np.isfinite(pc[t]):
                continue
            turb_p = 1.0 - pc[t]
            if pos > 0 and pc[t] <= p["exit"]:
                pos = 0.0
            elif pos < 0 and turb_p <= 1.0 - p["enter"]:
                pos = 0.0
            if pos == 0:
                if pc[t] >= p["enter"]:
                    pos = 1.0
                elif p["allow_short"] and turb_p >= p["enter"] and est["mu_t"][t] < 0:
                    pos = -1.0
            signal[t] = pos
        ann = math.sqrt(252) / 100.0
        return pd.DataFrame(
            {
                "signal": signal,
                "p_calm": pc,
                "vol_calm": est["sd_c"] * ann,
                "vol_turb": est["sd_t"] * ann,
                "ret_calm": est["mu_c"] * 252 / 100.0,
                "ret_turb": est["mu_t"] * 252 / 100.0,
                "stay_calm": est["stay_c"],
                "stay_turb": est["stay_t"],
                "separated": est["separated"],
                "refit": refits,
                "valid": np.isfinite(pc),
            },
            index=df.index,
        )

    def describe_row(self, diag, t):
        p = self.params
        pc = diag["p_calm"].iloc[t]
        held = diag["signal"].iloc[t]

        def duration(stay):
            return f"{1.0 / max(1.0 - stay, 1e-6):.0f} bars" if np.isfinite(stay) else "n/a"

        rules = [
            Rule("Calm-regime probability", f"{pc:.0%}", bool(pc >= p["enter"])),
            Rule(
                "Calm regime (vol / drift, per year)",
                f"{fmt_pct(diag['vol_calm'].iloc[t], 0)} / {fmt_pct(diag['ret_calm'].iloc[t], 0)}",
                None,
            ),
            Rule(
                "Turbulent regime (vol / drift, per year)",
                f"{fmt_pct(diag['vol_turb'].iloc[t], 0)} / {fmt_pct(diag['ret_turb'].iloc[t], 0)}",
                None,
            ),
            Rule(
                "Typical length (calm / turbulent)",
                f"{duration(diag['stay_calm'].iloc[t])} / {duration(diag['stay_turb'].iloc[t])}",
                None,
            ),
            Rule("Invest / exit thresholds", f"{p['enter']:.0%} / {p['exit']:.0%}", None),
        ]
        refits = np.flatnonzero(diag["refit"].to_numpy(bool)[: t + 1])
        if len(refits):
            rules.append(Rule("Model re-estimated", f"{t - int(refits[-1])} bars ago", None))
        if diag["separated"].iloc[t] == 0:
            ratio = diag["vol_turb"].iloc[t] / diag["vol_calm"].iloc[t]
            head = (
                f"No distinct turbulent regime in the training window (volatilities {fmt_pct(diag['vol_calm'].iloc[t], 0)} "
                f"vs {fmt_pct(diag['vol_turb'].iloc[t], 0)}, ratio {ratio:.1f} < {p['min_vol_ratio']:g}): treated as calm, "
                + ("invested." if held > 0 else "in cash.")
            )
            return head, rules
        if held > 0:
            head = f"Calm regime likely ({pc:.0%}): invested."
        elif held < 0:
            head = f"Turbulent, falling regime likely ({1 - pc:.0%}): short."
        elif pc > p["exit"]:
            head = f"Calm-regime probability {pc:.0%} is between the thresholds: staying in cash until it clears {p['enter']:.0%}."
        else:
            head = f"Turbulent regime likely ({1 - pc:.0%}): in cash."
        return head, rules

    def exit_rule(self, state):
        p = self.params
        if state == "long":
            return f"Exit when the calm-regime probability drops below {p['exit']:.0%}."
        if state == "short":
            return f"Cover when the turbulent-regime probability drops below {1 - p['enter']:.0%}."
        return f"Invest when the calm-regime probability rises above {p['enter']:.0%}."
