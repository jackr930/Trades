"""Buy-and-hold: the benchmark every active strategy has to beat after costs."""

from __future__ import annotations

import numpy as np
import pandas as pd

from trades.strategies.base import Evidence, Param, Reference, Rule, SingleAssetStrategy, fmt_pct

SHARPE_1991 = Reference(
    "Sharpe, W. F.",
    1991,
    "The Arithmetic of Active Management",
    "Financial Analysts Journal 47(1), 7-9",
    "https://doi.org/10.2469/faj.v47.n1.7",
)
BARBER_ODEAN_2000 = Reference(
    "Barber, B. M. & Odean, T.",
    2000,
    "Trading Is Hazardous to Your Wealth: The Common Stock Investment Performance of Individual Investors",
    "Journal of Finance 55(2), 773-806",
    "https://doi.org/10.1111/0022-1082.00226",
)


class BuyAndHold(SingleAssetStrategy):
    id = "buy_hold"
    name = "Buy and Hold"
    category = "Benchmark"
    summary = "Buy on the first bar and never sell. The yardstick for every other strategy."
    rules_text = ("Buy at the first opportunity and hold to the end.",)
    rationale = (
        "Before costs, the average actively managed dollar must earn the market return; after costs it must "
        "earn less (Sharpe 1991). Any strategy should be judged against this baseline, net of costs and risk."
    )
    failure_modes = "Full exposure to bear markets and deep drawdowns (e.g. -55% for US stocks in 2007-2009)."
    evidence = Evidence.BENCHMARK
    evidence_text = (
        "Barber & Odean (2000) found the most active individual traders underperformed the market by about "
        "6.5 percentage points a year, mostly due to trading costs."
    )
    references = (SHARPE_1991, BARBER_ODEAN_2000)
    params_spec = (
        Param(
            "warmup_bars",
            "Start after (bars)",
            0,
            "int",
            0,
            1000,
            1,
            help="Delay the purchase to line up with another strategy's warm-up.",
        ),
    )

    def warmup(self) -> int:
        return self.params["warmup_bars"] + 1

    def compute(self, df):
        n = self.params["warmup_bars"]
        signal = np.where(np.arange(len(df)) >= n, 1.0, 0.0)
        close = df["close"]
        return pd.DataFrame(
            {
                "signal": signal,
                "total_return": close / close.iloc[min(n, len(close) - 1)] - 1.0,
                "valid": np.arange(len(df)) >= n,
            },
            index=df.index,
        )

    def describe_row(self, diag, t):
        r = diag["total_return"].iloc[t]
        return f"Invested and holding; {fmt_pct(r)} since purchase.", [
            Rule("Return since purchase", fmt_pct(r), None)
        ]

    def exit_rule(self, state):
        return "Never sells."
