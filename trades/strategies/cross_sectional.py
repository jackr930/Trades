"""Cross-sectional strategies: rank a universe of symbols and hold baskets."""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from trades.strategies.base import (
    Evidence,
    Kind,
    Overlay,
    Param,
    Reference,
    Rule,
    Strategy,
    StrategyOutput,
    fmt_num,
    fmt_pct,
)

JT_1993 = Reference(
    "Jegadeesh, N. & Titman, S.",
    1993,
    "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency",
    "Journal of Finance 48(1), 65-91",
    "https://doi.org/10.1111/j.1540-6261.1993.tb04702.x",
)
AMP_2013 = Reference(
    "Asness, C. S., Moskowitz, T. J. & Pedersen, L. H.",
    2013,
    "Value and Momentum Everywhere",
    "Journal of Finance 68(3), 929-985",
    "https://doi.org/10.1111/jofi.12021",
)
DM_2016 = Reference(
    "Daniel, K. & Moskowitz, T. J.",
    2016,
    "Momentum Crashes",
    "Journal of Financial Economics 122(2), 221-247",
    "https://doi.org/10.1016/j.jfineco.2015.12.002",
)
ANTONACCI_2014 = Reference(
    "Antonacci, G.",
    2014,
    "Dual Momentum Investing: An Innovative Strategy for Higher Returns with Lower Risk",
    "McGraw-Hill",
)
AHXZ_2006 = Reference(
    "Ang, A., Hodrick, R. J., Xing, Y. & Zhang, X.",
    2006,
    "The Cross-Section of Volatility and Expected Returns",
    "Journal of Finance 61(1), 259-299",
    "https://doi.org/10.1111/j.1540-6261.2006.00836.x",
)
FP_2014 = Reference(
    "Frazzini, A. & Pedersen, L. H.",
    2014,
    "Betting Against Beta",
    "Journal of Financial Economics 111(1), 1-25",
    "https://doi.org/10.1016/j.jfineco.2013.10.005",
)
BBW_2011 = Reference(
    "Baker, M., Bradley, B. & Wurgler, J.",
    2011,
    "Benchmarks as Limits to Arbitrage: Understanding the Low-Volatility Anomaly",
    "Financial Analysts Journal 67(1), 40-54",
    "https://doi.org/10.2469/faj.v67.n1.4",
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
ACG_2006 = Reference(
    "Avramov, D., Chordia, T. & Goyal, A.",
    2006,
    "Liquidity and Autocorrelations in Individual Stock Returns",
    "Journal of Finance 61(5), 2365-2394",
    "https://doi.org/10.1111/j.1540-6261.2006.01060.x",
)
GH_2004 = Reference(
    "George, T. J. & Hwang, C.-Y.",
    2004,
    "The 52-Week High and Momentum Investing",
    "Journal of Finance 59(5), 2145-2176",
    "https://doi.org/10.1111/j.1540-6261.2004.00695.x",
)


class CrossSectionalStrategy(Strategy):
    kind = Kind.CROSS_SECTIONAL
    min_symbols = 3
    metric_label: ClassVar[str] = "Score"
    metric_is_pct: ClassVar[bool] = True
    higher_is_better: ClassVar[bool] = True
    overlays = (Overlay("metric", "Ranking metric", "lower"),)

    def metric(self, data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        raise NotImplementedError

    def select(self, score: np.ndarray, metric: np.ndarray) -> np.ndarray:
        """Target weights for one rebalance, given scores (higher = more attractive)."""
        p = self.params
        w = np.zeros(len(score))
        valid = np.flatnonzero(np.isfinite(score))
        n = len(valid)
        if n == 0:
            return w
        k = max(1, int(round(n * p.get("top_frac", 0.3))))
        order = valid[np.argsort(-score[valid], kind="stable")]
        if p.get("allow_short"):
            k = max(1, min(k, n // 2))
            w[order[:k]] = 0.5 / k
            w[order[-k:]] = -0.5 / k
        else:
            w[order[:k]] = 1.0 / k
        return w

    def run(self, data: dict[str, pd.DataFrame]) -> StrategyOutput:
        self.check_symbols(list(data))
        symbols = list(data)
        metric = self.metric(data)[symbols]
        score = metric if self.higher_is_better else -metric
        m, s = metric.to_numpy(float), score.to_numpy(float)
        T, N = s.shape
        need = min(self.min_symbols, N)
        eligible = np.isfinite(s).sum(axis=1) >= need
        weights = np.zeros((T, N))
        rebalance_rows = np.zeros(T, dtype=bool)
        first = int(np.argmax(eligible)) if eligible.any() else T
        cur = np.zeros(N)
        every = max(int(self.params.get("rebalance", 21)), 1)
        for t in range(first, T):
            if (t - first) % every == 0 and eligible[t]:
                cur = self.select(s[t], m[t])
                rebalance_rows[t] = True
            weights[t] = cur
        ranks = score.rank(axis=1, ascending=False, method="first")
        n_ranked = score.notna().sum(axis=1)
        signals = pd.DataFrame(weights, index=metric.index, columns=symbols)
        valid = pd.Series(np.arange(T) >= first, index=metric.index)
        diags = {}
        for j, sym in enumerate(symbols):
            diags[sym] = pd.DataFrame(
                {
                    "metric": metric[sym],
                    "rank": ranks[sym],
                    "n": n_ranked,
                    "weight": weights[:, j],
                    "rebalance": rebalance_rows,
                    "valid": valid,
                },
                index=metric.index,
            )
        return StrategyOutput(signals, diags)

    def _fmt(self, x: float) -> str:
        return fmt_pct(x) if self.metric_is_pct else fmt_num(x, 3)

    def describe(self, output, symbol, t):
        d = output.diagnostics[symbol]
        rank, n = d["rank"].iloc[t], int(d["n"].iloc[t])
        w = d["weight"].iloc[t]
        reb = np.flatnonzero(d["rebalance"].to_numpy()[: t + 1])
        last_reb = d.index[reb[-1]].date().isoformat() if len(reb) else "n/a"
        rules = [
            Rule(self.metric_label, self._fmt(d["metric"].iloc[t]), None),
            Rule(
                "Rank now (1 = most attractive)",
                f"{int(rank)} of {n}" if np.isfinite(rank) else "n/a",
                bool(w > 0) if w != 0 else None,
            ),
            Rule("Last rebalance", last_reb, None),
        ]
        side = "in the long basket" if w > 0 else "in the short basket" if w < 0 else "not selected"
        return f"{self.metric_label} {self._fmt(d['metric'].iloc[t])}; {side} (weight {w:.0%}).", rules

    def exit_rule(self, state):
        return (
            f"Re-ranked every {self.params.get('rebalance', 21)} bars; held until it drops out of the basket."
        )


class CrossSectionalMomentum(CrossSectionalStrategy):
    id = "xs_momentum"
    name = "Cross-Sectional Momentum (12-1)"
    category = "Momentum"
    metric_label = "12-1 month return"
    summary = "Rank the watchlist by 12-month return excluding the latest month; buy the top third (optionally short the bottom third). Rebalance monthly."
    rules_text = (
        "Score = return from 12 months ago to 1 month ago (skipping the most recent month avoids short-term reversal).",
        "Every 21 bars, buy the top 30% of symbols equally weighted.",
        "Optionally short the bottom 30% for a market-neutral version.",
    )
    rationale = (
        "Winners keep winning and losers keep losing for 3-12 months, often attributed to investor under-reaction "
        "to news and gradual information diffusion."
    )
    failure_modes = (
        "'Momentum crashes': sharp market rebounds after bear markets (1932, 2009) cause severe losses, "
        "especially on the short side (Daniel & Moskowitz 2016). Needs a reasonably large universe."
    )
    evidence = Evidence.STRONG
    evidence_text = (
        "One of the most robust anomalies: Jegadeesh & Titman (1993) found ~1%/month for US stocks 1965-1989, "
        "and Asness, Moskowitz & Pedersen (2013) find momentum in eight markets and asset classes. With a small "
        "watchlist the effect is noisy; academic results use hundreds of stocks."
    )
    references = (JT_1993, AMP_2013, DM_2016)
    params_spec = (
        Param("lookback", "Formation period (bars)", 252, "int", 21, 504, 1),
        Param("skip", "Skip recent (bars)", 21, "int", 0, 63, 1),
        Param("rebalance", "Rebalance every (bars)", 21, "int", 1, 126, 1),
        Param("top_frac", "Basket size (fraction)", 0.3, "float", 0.1, 0.5, 0.05),
        Param("allow_short", "Short the losers", False, "bool"),
    )

    def warmup(self) -> int:
        return self.params["lookback"] + self.params["skip"] + 1

    def metric(self, data):
        p = self.params
        closes = pd.DataFrame({s: df["close"] for s, df in data.items()})
        return closes.shift(p["skip"]) / closes.shift(p["skip"] + p["lookback"]) - 1.0


class DualMomentum(CrossSectionalStrategy):
    id = "dual_momentum"
    name = "Dual Momentum (Antonacci)"
    category = "Momentum"
    min_symbols = 2
    metric_label = "12-month return"
    summary = "Hold the watchlist asset with the best 12-month return -- but only if that return is positive (absolute momentum); otherwise hold a safe asset or cash."
    rules_text = (
        "Relative momentum: rank risky assets by 12-month return; pick the best (top N).",
        "Absolute momentum: only hold a pick if its 12-month return beats the threshold (e.g. 0%, a T-bill proxy).",
        "Otherwise put that slice into the safe asset (e.g. a bond fund) or cash. Rebalance monthly.",
    )
    rationale = (
        "Relative momentum picks the strongest market; absolute (time-series) momentum steps aside during "
        "sustained bear markets, which historically reduced drawdowns."
    )
    failure_modes = (
        "Whipsaws around the absolute-momentum threshold; concentrated in one asset; lags sharp reversals."
    )
    evidence = Evidence.PRACTITIONER
    evidence_text = (
        "Proposed by Antonacci (2014) for equity/bond index rotation, combining two effects with strong academic "
        "support (cross-sectional and time-series momentum). The specific combination is practitioner-tested."
    )
    references = (ANTONACCI_2014, JT_1993)
    params_spec = (
        Param("lookback", "Lookback (bars)", 252, "int", 21, 504, 1),
        Param("rebalance", "Rebalance every (bars)", 21, "int", 1, 126, 1),
        Param("top_n", "Assets held", 1, "int", 1, 5, 1),
        Param("threshold", "Absolute momentum threshold", 0.0, "float", -0.2, 0.2, 0.01),
        Param(
            "safe_symbol",
            "Safe asset (blank = cash)",
            "",
            "symbol",
            help="E.g. a bond fund such as SIMBND, AGG or BND. Must be in the symbol list.",
        ),
    )

    def warmup(self) -> int:
        return self.params["lookback"] + 1

    def metric(self, data):
        closes = pd.DataFrame({s: df["close"] for s, df in data.items()})
        return closes / closes.shift(self.params["lookback"]) - 1.0

    def run(self, data):
        self._symbols = list(data)
        return super().run(data)

    def select(self, score, metric):
        p = self.params
        symbols = self._symbols
        safe = p["safe_symbol"]
        safe_idx = symbols.index(safe) if safe in symbols else None
        w = np.zeros(len(score))
        risky = [i for i in range(len(score)) if i != safe_idx and np.isfinite(score[i])]
        if not risky:
            return w
        order = sorted(risky, key=lambda i: -score[i])[: p["top_n"]]
        slot = 1.0 / p["top_n"]
        for i in order:
            if metric[i] > p["threshold"]:
                w[i] += slot
            elif safe_idx is not None:
                w[safe_idx] += slot
        return w


class LowVolatility(CrossSectionalStrategy):
    id = "low_volatility"
    name = "Low-Volatility Anomaly"
    category = "Defensive factor"
    metric_label = "Annualised volatility"
    higher_is_better = False
    summary = "Hold the least volatile third of the watchlist, rebalanced monthly. Low-risk stocks have historically delivered similar or better returns than high-risk stocks, with far less volatility."
    rules_text = (
        "Measure each symbol's trailing 1-year volatility of daily returns.",
        "Every 21 bars, buy the 30% with the lowest volatility (equal or inverse-volatility weights).",
    )
    rationale = (
        "Leverage-constrained and benchmarked investors bid up high-beta 'lottery' stocks, leaving low-risk "
        "stocks under-priced relative to CAPM (Frazzini & Pedersen 2014; Baker, Bradley & Wurgler 2011)."
    )
    failure_modes = "Lags in speculative bull markets; tends to concentrate in rate-sensitive sectors (utilities, staples)."
    evidence = Evidence.STRONG
    evidence_text = (
        "Ang et al. (2006) found high idiosyncratic-volatility stocks earn abysmally low returns; Frazzini & "
        "Pedersen (2014) document 'betting against beta' across 20 equity markets, bonds and futures. Widely "
        "implemented institutionally (minimum-volatility index funds)."
    )
    references = (AHXZ_2006, FP_2014, BBW_2011)
    params_spec = (
        Param("vol_window", "Volatility window (bars)", 252, "int", 20, 504, 1),
        Param("rebalance", "Rebalance every (bars)", 21, "int", 1, 126, 1),
        Param("top_frac", "Basket size (fraction)", 0.3, "float", 0.1, 0.5, 0.05),
        Param("weighting", "Weighting", "equal", "choice", choices=("equal", "inverse_vol")),
    )

    def warmup(self) -> int:
        return self.params["vol_window"] + 1

    def metric(self, data):
        closes = pd.DataFrame({s: df["close"] for s, df in data.items()})
        rets = closes.pct_change(fill_method=None)
        return rets.rolling(self.params["vol_window"]).std() * np.sqrt(252)

    def select(self, score, metric):
        w = super().select(score, metric)
        if self.params["weighting"] == "inverse_vol":
            held = w > 0
            inv = np.where(held, 1.0 / np.where(metric > 0, metric, np.nan), 0.0)
            inv = np.nan_to_num(inv)
            if inv.sum() > 0:
                w = inv / inv.sum()
        return w


class ShortTermReversal(CrossSectionalStrategy):
    id = "st_reversal"
    name = "Short-Term Reversal (1-Week Losers)"
    category = "Mean reversion"
    metric_label = "5-bar return"
    higher_is_better = False
    summary = "Buy last week's biggest losers in the watchlist (optionally short the biggest winners); rebalance weekly."
    rules_text = (
        "Score = return over the last 5 bars.",
        "Every 5 bars, buy the 30% of symbols with the lowest recent return.",
        "Optionally short the 30% with the highest recent return.",
    )
    rationale = (
        "Liquidity providers demand compensation for absorbing order imbalances, so short-term price pressure "
        "partially reverses (Lehmann 1990; Jegadeesh 1990)."
    )
    failure_modes = (
        "Very high turnover: realistic transaction costs can eliminate the edge (try raising slippage in the Lab). "
        "Losers can keep losing on genuine bad news."
    )
    evidence = Evidence.MODERATE
    evidence_text = (
        "Weekly and monthly reversal were strong in 1960s-1980s US data (Lehmann 1990; Jegadeesh 1990). Later "
        "work ties the effect to illiquid stocks and finds much of it disappears after trading costs "
        "(Avramov, Chordia & Goyal 2006). A good lesson in gross vs. net returns."
    )
    references = (LEHMANN_1990, JEGADEESH_1990, ACG_2006)
    params_spec = (
        Param("lookback", "Lookback (bars)", 5, "int", 1, 21, 1),
        Param("rebalance", "Rebalance every (bars)", 5, "int", 1, 21, 1),
        Param("top_frac", "Basket size (fraction)", 0.3, "float", 0.1, 0.5, 0.05),
        Param("allow_short", "Short the winners", False, "bool"),
    )

    def warmup(self) -> int:
        return self.params["lookback"] + 1

    def metric(self, data):
        closes = pd.DataFrame({s: df["close"] for s, df in data.items()})
        return closes / closes.shift(self.params["lookback"]) - 1.0


class FiftyTwoWeekHigh(CrossSectionalStrategy):
    id = "high_52w"
    name = "52-Week-High Momentum"
    category = "Momentum"
    metric_label = "Price / 52-week high"
    metric_is_pct = False
    summary = "Buy the symbols trading closest to their 52-week highs; investors anchor on the high and under-react to good news near it."
    rules_text = (
        "Nearness = close / highest high of the last 252 bars (1.0 = at the high).",
        "Every 21 bars, buy the 30% nearest their highs (optionally short the 30% furthest away).",
    )
    rationale = (
        "Traders anchor on the 52-week high and are reluctant to bid prices above it, so good news is "
        "incorporated slowly; nearness to the high predicts future returns (George & Hwang 2004)."
    )
    failure_modes = "Like other momentum signals, vulnerable to sharp reversals and crowded unwinds."
    evidence = Evidence.STRONG
    evidence_text = (
        "George & Hwang (2004) found nearness to the 52-week high explains a large part of momentum profits "
        "and that its returns do not reverse in the long run. Replicated internationally."
    )
    references = (GH_2004, JT_1993)
    params_spec = (
        Param("window", "High window (bars)", 252, "int", 20, 504, 1),
        Param("rebalance", "Rebalance every (bars)", 21, "int", 1, 126, 1),
        Param("top_frac", "Basket size (fraction)", 0.3, "float", 0.1, 0.5, 0.05),
        Param("allow_short", "Short the laggards", False, "bool"),
    )

    def warmup(self) -> int:
        return self.params["window"]

    def metric(self, data):
        w = self.params["window"]
        return pd.DataFrame(
            {s: df["close"] / df["high"].rolling(w, min_periods=w).max() for s, df in data.items()}
        )
