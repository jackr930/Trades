"""Pairs trading / statistical arbitrage."""

from __future__ import annotations

import numpy as np
import pandas as pd

from trades.core.stats import engle_granger
from trades.strategies.base import (
    Evidence,
    Explanation,
    Kind,
    Overlay,
    Param,
    Reference,
    Rule,
    Strategy,
    StrategyOutput,
    fmt_num,
    signal_since,
)

GGR_2006 = Reference(
    "Gatev, E., Goetzmann, W. N. & Rouwenhorst, K. G.",
    2006,
    "Pairs Trading: Performance of a Relative-Value Arbitrage Rule",
    "Review of Financial Studies 19(3), 797-827",
    "https://doi.org/10.1093/rfs/hhj020",
)
AL_2010 = Reference(
    "Avellaneda, M. & Lee, J.-H.",
    2010,
    "Statistical Arbitrage in the US Equities Market",
    "Quantitative Finance 10(7), 761-782",
    "https://doi.org/10.1080/14697680903124632",
)
EG_1987 = Reference(
    "Engle, R. F. & Granger, C. W. J.",
    1987,
    "Co-integration and Error Correction: Representation, Estimation, and Testing",
    "Econometrica 55(2), 251-276",
    "https://doi.org/10.2307/1913236",
)
DO_FAFF_2010 = Reference(
    "Do, B. & Faff, R.",
    2010,
    "Does Simple Pairs Trading Still Work?",
    "Financial Analysts Journal 66(4), 83-95",
    "https://doi.org/10.2469/faj.v66.n4.1",
)


class PairsTrading(Strategy):
    id = "pairs_trading"
    name = "Pairs Trading (Cointegration Z-Score)"
    category = "Statistical arbitrage"
    kind = Kind.PAIR
    min_symbols = 2
    max_symbols = 2
    uses_short = True
    summary = "Trade two historically linked stocks against each other when the spread between them stretches unusually far, betting it snaps back."
    rules_text = (
        "Regress log(A) on log(B) over a rolling window to estimate the hedge ratio beta.",
        "Spread = log(A) - alpha - beta*log(B); z-score = spread / its standard deviation.",
        "z < -2: buy A, sell beta-weighted B (spread is cheap). z > +2: the reverse.",
        "Exit when |z| < 0.5; stop out if |z| exceeds the stop level. The hedge ratio is locked at entry.",
    )
    rationale = (
        "If two prices share a common stochastic trend (they are cointegrated), deviations of their spread "
        "are temporary and tend to mean-revert. The position is roughly market-neutral."
    )
    failure_modes = (
        "Relationships break (mergers, divergent fundamentals), turning a 'temporary' spread into a permanent "
        "loss; crowded unwinds (e.g. the August 2007 quant crisis) cause sharp losses. Requires short selling."
    )
    evidence = Evidence.MODERATE
    evidence_text = (
        "Gatev, Goetzmann & Rouwenhorst (2006) report ~11% annualised excess returns for a simple pairs rule "
        "on US stocks (1962-2002). Do & Faff (2010) document that profitability declined markedly after the "
        "late 1980s, and costs consume much of what remains; Avellaneda & Lee (2010) describe the institutional "
        "factor-residual version."
    )
    references = (GGR_2006, AL_2010, EG_1987, DO_FAFF_2010)
    params_spec = (
        Param("lookback", "Estimation window (bars)", 120, "int", 30, 504, 1),
        Param("entry_z", "Entry |z|", 2.0, "float", 0.5, 4.0, 0.1),
        Param("exit_z", "Exit |z|", 0.5, "float", 0.0, 2.0, 0.1),
        Param("stop_z", "Stop |z| (0 = off)", 4.0, "float", 0.0, 8.0, 0.1),
        Param("max_hold", "Max holding (bars, 0 = off)", 0, "int", 0, 252, 1),
        Param(
            "require_coint",
            "Require cointegration (ADF 5%)",
            False,
            "bool",
            help="Only enter when an Engle-Granger test on the window rejects 'no cointegration'.",
        ),
    )
    overlays = (Overlay("z", "Spread z-score", "lower", levels=(-2.0, -0.5, 0.5, 2.0)),)

    def validate(self):
        p = self.params
        if p["exit_z"] >= p["entry_z"]:
            raise ValueError("Exit |z| must be below entry |z|")
        if p["stop_z"] and p["stop_z"] <= p["entry_z"]:
            raise ValueError("Stop |z| must exceed entry |z| (or be 0 to disable)")

    def warmup(self) -> int:
        return self.params["lookback"]

    def run(self, data: dict[str, pd.DataFrame]) -> StrategyOutput:
        self.check_symbols(list(data))
        (sym_a, df_a), (sym_b, df_b) = list(data.items())
        p = self.params
        L = p["lookback"]
        a = np.log(df_a["close"])
        b = np.log(df_b["close"])
        mean_a, mean_b = a.rolling(L).mean(), b.rolling(L).mean()
        var_a, var_b = a.rolling(L).var(ddof=0), b.rolling(L).var(ddof=0)
        cov = a.rolling(L).cov(b, ddof=0)
        beta = cov / var_b
        alpha = mean_a - beta * mean_b
        resid = a - alpha - beta * b
        resid_var = (var_a - cov**2 / var_b).clip(lower=1e-12)
        z = resid / np.sqrt(resid_var)

        zv, bv = z.to_numpy(float), beta.to_numpy(float)
        a_np, b_np = a.to_numpy(float), b.to_numpy(float)
        T = len(zv)
        state = np.zeros(T)
        locked_beta = np.full(T, np.nan)
        pos, lb, held = 0.0, np.nan, 0
        coint_cache: dict[int, bool] = {}

        def cointegrated(t: int) -> bool:
            if t not in coint_cache:
                res = engle_granger(a_np[t - L + 1 : t + 1], b_np[t - L + 1 : t + 1])
                coint_cache[t] = res.cointegrated
            return coint_cache[t]

        for t in range(T):
            if not np.isfinite(zv[t]) or not np.isfinite(bv[t]):
                continue
            if pos != 0:
                held += 1
                done = (pos > 0 and zv[t] >= -p["exit_z"]) or (pos < 0 and zv[t] <= p["exit_z"])
                stopped = p["stop_z"] and abs(zv[t]) >= p["stop_z"]
                timed_out = p["max_hold"] and held >= p["max_hold"]
                if done or stopped or timed_out:
                    pos, lb = 0.0, np.nan
            if pos == 0 and abs(zv[t]) >= p["entry_z"] and (not p["stop_z"] or abs(zv[t]) < p["stop_z"]):
                if not p["require_coint"] or cointegrated(t):
                    pos, lb, held = (-1.0 if zv[t] > 0 else 1.0), bv[t], 0
            state[t] = pos
            locked_beta[t] = lb

        gross = 1.0 + np.abs(np.nan_to_num(locked_beta))
        w_a = np.where(state != 0, state / gross, 0.0)
        w_b = np.where(state != 0, -state * np.nan_to_num(locked_beta) / gross, 0.0)
        signals = pd.DataFrame({sym_a: w_a, sym_b: w_b}, index=df_a.index)
        diag = pd.DataFrame(
            {
                "z": z,
                "beta": beta,
                "spread": resid,
                "state": state,
                "locked_beta": locked_beta,
                "log_a": a,
                "log_b": b,
                "valid": z.notna(),
            },
            index=df_a.index,
        )
        return StrategyOutput(signals, {sym_a: diag, sym_b: diag.copy(), "__pair__": diag})

    def explain(self, output: StrategyOutput, symbol: str, at: int = -1) -> Explanation:
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
        z = diag["z"].iloc[t]
        beta = diag["beta"].iloc[t]
        state = diag["state"].iloc[t]
        other = syms[1] if symbol == syms[0] else syms[0]
        a_sym, b_sym = syms
        rules = [
            Rule("Spread z-score", fmt_num(z), bool(abs(z) >= self.params["entry_z"])),
            Rule(
                "Entry / exit thresholds",
                f"|z| >= {self.params['entry_z']:g} / |z| <= {self.params['exit_z']:g}",
                None,
            ),
            Rule(f"Hedge ratio (log {a_sym} on log {b_sym})", fmt_num(beta, 3), None),
        ]
        L = self.params["lookback"]
        stats = None
        if t + 1 >= L:
            # Cointegration test on the window ending at bar t (never uses later data).
            stats = engle_granger(
                diag["log_a"].iloc[t - L + 1 : t + 1], diag["log_b"].iloc[t - L + 1 : t + 1]
            )
        if stats is not None:
            rules.append(
                Rule(
                    "Engle-Granger ADF (5% crit)",
                    f"{fmt_num(stats.adf.stat)} ({fmt_num(stats.adf.critical_values['5%'])})",
                    stats.cointegrated,
                )
            )
            rules.append(Rule("Mean-reversion half-life", f"{fmt_num(stats.half_life, 1)} bars", None))
        if state > 0:
            head = f"Spread is cheap (z = {fmt_num(z)}): long {a_sym}, short {b_sym}."
        elif state < 0:
            head = f"Spread is rich (z = {fmt_num(z)}): short {a_sym}, long {b_sym}."
        else:
            head = f"Spread z = {fmt_num(z)} is inside the entry band; {symbol} vs {other} is fairly priced."
        st = "long" if value > 1e-12 else "short" if value < -1e-12 else "flat"
        exit_rule = f"Exit when |z| <= {self.params['exit_z']:g}" + (
            f" or stop at |z| >= {self.params['stop_z']:g}." if self.params["stop_z"] else "."
        )
        return Explanation(symbol, st, value, head, rules, since, fresh, exit_rule if state else "")
