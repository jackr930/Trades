"""Machine-learning strategy: a walk-forward, purged cross-sectional ranker.

A deliberately transparent model (ridge regression) trained the way quant researchers
train return models: only on labels that were already known at each retraining date
("purging"), re-estimated on a rolling window, and judged by its live out-of-sample
information coefficient rather than by its in-sample fit.
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd

from trades.core import indicators as ind
from trades.strategies.base import Evidence, Param, Reference, Rule, fmt_num, fmt_pct
from trades.strategies.cross_sectional import CrossSectionalStrategy

GKX_2020 = Reference(
    "Gu, S., Kelly, B. & Xiu, D.",
    2020,
    "Empirical Asset Pricing via Machine Learning",
    "Review of Financial Studies 33(5), 2223-2273",
    "https://doi.org/10.1093/rfs/hhaa009",
)
KDH_2017 = Reference(
    "Krauss, C., Do, X. A. & Huck, N.",
    2017,
    "Deep Neural Networks, Gradient-Boosted Trees, Random Forests: Statistical Arbitrage on the S&P 500",
    "European Journal of Operational Research 259(2), 689-702",
    "https://doi.org/10.1016/j.ejor.2016.10.031",
)
LDP_2018 = Reference(
    "Lopez de Prado, M.",
    2018,
    "Advances in Financial Machine Learning (ch. 7, purged cross-validation)",
    "Wiley",
)

FEATURES: tuple[tuple[str, str], ...] = (
    ("ret_1", "1-bar return"),
    ("ret_5", "1-week return"),
    ("ret_21", "1-month return"),
    ("ret_63", "3-month return"),
    ("mom_12_1", "12-1 month momentum"),
    ("vol_21", "1-month volatility"),
    ("vol_63", "3-month volatility"),
    ("to_high", "distance to 52-week high"),
    ("to_sma200", "distance to 200-bar average"),
    ("rsi_14", "RSI(14)"),
)
SHRINKAGE = {"light": 0.01, "medium": 0.1, "strong": 1.0}
IC_MEMORY = 12  # realised predictions averaged in the live IC


def _raw_features(data: dict[str, pd.DataFrame], symbols: list[str]) -> dict[str, pd.DataFrame]:
    close = pd.DataFrame({s: data[s]["close"] for s in symbols})
    high = pd.DataFrame({s: data[s]["high"] for s in symbols})
    lc = np.log(close)
    r1 = lc.diff()
    return {
        "ret_1": r1,
        "ret_5": lc - lc.shift(5),
        "ret_21": lc - lc.shift(21),
        "ret_63": lc - lc.shift(63),
        "mom_12_1": lc.shift(21) - lc.shift(252),
        "vol_21": r1.rolling(21).std(),
        "vol_63": r1.rolling(63).std(),
        "to_high": close / high.rolling(252, min_periods=252).max() - 1.0,
        "to_sma200": close / close.rolling(200).mean() - 1.0,
        "rsi_14": pd.DataFrame({s: (ind.rsi(close[s], 14) - 50.0) / 50.0 for s in symbols}),
    }


def _cross_sectional_z(x: np.ndarray, min_count: int) -> np.ndarray:
    """Standardise each bar across symbols (the model learns *relative* effects)."""
    finite = np.isfinite(x)
    count = finite.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN rows during the warm-up
        mean = np.nanmean(np.where(finite, x, np.nan), axis=1, keepdims=True)
        sd = np.nanstd(np.where(finite, x, np.nan), axis=1, keepdims=True)
        z = (x - mean) / sd
    ok = (count >= min_count) & (sd > 0)
    return np.where(ok & finite, z, np.nan)


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 3:
        return float("nan")
    ra = pd.Series(a[ok]).rank().to_numpy()
    rb = pd.Series(b[ok]).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


class MLRanker(CrossSectionalStrategy):
    id = "ml_ranker"
    name = "Machine-Learning Ranker (Walk-Forward Ridge)"
    category = "Machine learning"
    family = "modern"
    counterpart = "xs_momentum"
    min_symbols = 4
    needs = "5+ stocks (the more the better) and several years of history."
    metric_label = "Predicted relative return"
    metric_is_pct = True
    summary = (
        "A regression model learns, from ten price-based features, which stocks tend to beat the group over "
        "the next month; it is retrained every month on data it could have known, and holds the stocks it "
        "ranks highest."
    )
    rules_text = (
        "Features for every stock, standardised across the group each bar: returns over 1, 5, 21 and 63 bars, "
        "12-1 month momentum, 1- and 3-month volatility, distance to the 52-week high and to the 200-bar "
        "average, and RSI(14).",
        "Target: the stock's return over the next 21 bars minus the group average. Train a ridge regression "
        "on all stocks together, using only past rows whose 21-bar outcome was already known ('purging').",
        "Every 21 bars: retrain on the latest three years, predict each stock's relative return, buy the top "
        "30% (optionally short the bottom 30%).",
        "Track the live information coefficient: the rank correlation between past predictions and what then "
        "happened. Near zero means the model is not finding anything.",
    )
    rationale = (
        "Machine learning can combine many weak signals (momentum, reversal, volatility, trend) and learn how "
        "much each is worth instead of hand-picking one. Keeping the model linear and heavily regularised, and "
        "judging it only on predictions made before the outcome was known, keeps the test honest."
    )
    failure_modes = (
        "With a handful of stocks and ten features there is very little to learn from, and the easiest thing "
        "for any model to learn is noise. Relationships that held in the training window can reverse. Treat a "
        "high backtest return with suspicion unless the live information coefficient stays positive."
    )
    evidence = Evidence.EXPERIMENTAL
    evidence_text = (
        "Gu, Kelly & Xiu (2020) found machine-learning forecasts roughly doubled the performance of linear "
        "benchmarks when predicting US stock returns from 94 firm characteristics across thousands of stocks; "
        "Krauss, Do & Huck (2017) report large pre-cost returns from ML on S&P 500 stocks that faded after "
        "2010. Neither result carries over to a small watchlist with price features only, so this is a tool "
        "for learning how to test models honestly, not a proven strategy."
    )
    references = (GKX_2020, KDH_2017, LDP_2018)
    params_spec = (
        Param("horizon", "Prediction horizon (bars)", 21, "int", 5, 63, 1),
        Param("train_bars", "Training window (bars)", 756, "int", 252, 1512, 1),
        Param("min_train", "Minimum training history (bars)", 252, "int", 126, 756, 1),
        Param(
            "shrinkage",
            "Regularisation",
            "medium",
            "choice",
            choices=tuple(SHRINKAGE),
            help="How strongly the model's weights are pulled towards zero. Stronger = simpler, more robust.",
        ),
        Param("rebalance", "Retrain and rebalance every (bars)", 21, "int", 5, 126, 1),
        Param("top_frac", "Basket size (fraction)", 0.3, "float", 0.1, 0.5, 0.05),
        Param("allow_short", "Short the lowest-ranked", False, "bool"),
    )

    def warmup(self) -> int:
        p = self.params
        return 253 + p["min_train"] + p["horizon"]

    # The base class ranks on metric(); the model's extra diagnostics ride along.
    def metric(self, data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        p = self.params
        symbols = list(data)
        index = data[symbols[0]].index
        raw = _raw_features(data, symbols)
        keys = [k for k, _ in FEATURES]
        X = np.stack([_cross_sectional_z(raw[k].to_numpy(float), 3) for k in keys], axis=2)  # T x N x K
        T, N, K = X.shape
        h = p["horizon"]
        lc = np.log(np.column_stack([data[s]["close"].to_numpy(float) for s in symbols]))
        fwd = np.full((T, N), np.nan)
        if T > h:
            fwd[: T - h] = lc[h:] - lc[: T - h]
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # rows with no future yet
            Y = fwd - np.nanmean(fwd, axis=1, keepdims=True)  # relative to the group

        rows_ok = np.isfinite(X).all(axis=2)
        feature_rows = np.flatnonzero(rows_ok.sum(axis=1) >= self.min_symbols)
        pred = np.full((T, N), np.nan)
        contrib = np.full((T, N, K), np.nan)
        coef = np.full((T, K), np.nan)
        ic = np.full(T, np.nan)
        n_ic = np.zeros(T)
        n_train = np.zeros(T)
        refit = np.zeros(T, dtype=bool)
        if len(feature_rows):
            start = int(feature_rows[0])
            first_fit = start + p["min_train"] + h - 1
            beta = None
            realised: list[float] = []
            fit_dates: list[int] = []
            last_ic, last_n, last_rows = np.nan, 0, 0
            for t in range(first_fit, T):
                if (t - first_fit) % p["rebalance"] == 0:
                    lo = max(start, t - h - p["train_bars"] + 1)
                    hi = t - h  # purge: every label in the sample is known by bar t
                    Xs = X[lo : hi + 1].reshape(-1, K)
                    ys = Y[lo : hi + 1].reshape(-1)
                    ok = np.isfinite(Xs).all(axis=1) & np.isfinite(ys)
                    Xs, ys = Xs[ok], ys[ok]
                    if len(ys) >= K * 5:
                        lam = SHRINKAGE[p["shrinkage"]] * len(ys)
                        beta = np.linalg.solve(Xs.T @ Xs + lam * np.eye(K), Xs.T @ ys)
                        last_rows = len(ys)
                    refit[t] = True
                    # Score earlier predictions whose outcome is now known.
                    while fit_dates and fit_dates[0] + h <= t:
                        s = fit_dates.pop(0)
                        v = _spearman(pred[s], Y[s])
                        if np.isfinite(v):
                            realised.append(v)
                    if beta is not None:
                        fit_dates.append(t)
                    recent = realised[-IC_MEMORY:]
                    last_ic = float(np.mean(recent)) if recent else np.nan
                    last_n = len(recent)
                if beta is not None:
                    xt = X[t]
                    good = np.isfinite(xt).all(axis=1)
                    pred[t, good] = xt[good] @ beta
                    contrib[t, good] = xt[good] * beta
                    coef[t] = beta
                ic[t], n_ic[t], n_train[t] = last_ic, last_n, last_rows

        self._extra: dict[str, Any] = {
            "contrib": contrib,
            "coef": coef,
            "ic": ic,
            "n_ic": n_ic,
            "n_train": n_train,
            "refit": refit,
        }
        return pd.DataFrame(pred, index=index, columns=symbols)

    def run(self, data):
        out = super().run(data)
        extra = self._extra
        keys = [k for k, _ in FEATURES]
        for j, sym in enumerate(list(data)):
            d = out.diagnostics[sym]
            cols = {f"c_{k}": extra["contrib"][:, j, i] for i, k in enumerate(keys)}
            cols.update({f"b_{k}": extra["coef"][:, i] for i, k in enumerate(keys)})
            out.diagnostics[sym] = d.assign(
                ic=extra["ic"], n_ic=extra["n_ic"], n_train=extra["n_train"], retrain=extra["refit"], **cols
            )
        return out

    def describe(self, output, symbol, t):
        p = self.params
        d = output.diagnostics[symbol]
        pred, rank, n, w = d["metric"].iloc[t], d["rank"].iloc[t], d["n"].iloc[t], d["weight"].iloc[t]
        labels = dict(FEATURES)
        contribs = sorted(
            ((labels[k], d[f"c_{k}"].iloc[t]) for k, _ in FEATURES if np.isfinite(d[f"c_{k}"].iloc[t])),
            key=lambda kv: -abs(kv[1]),
        )
        ic, n_ic = d["ic"].iloc[t], int(d["n_ic"].iloc[t])
        rules = [
            Rule(
                f"Predicted return vs group, next {p['horizon']} bars",
                fmt_pct(pred),
                None if not np.isfinite(pred) else bool(pred > 0),
            ),
            Rule("Rank (1 = best)", f"{int(rank)} of {int(n)}" if np.isfinite(rank) else "n/a", None),
        ]
        rules += [Rule(f"Driver: {label}", fmt_pct(c, 2), bool(c > 0)) for label, c in contribs[:3]]
        rules.append(
            Rule(
                f"Live information coefficient (last {n_ic} predictions)",
                fmt_num(ic) if np.isfinite(ic) else "not yet known",
                None if not np.isfinite(ic) else bool(ic > 0.02),
            )
        )
        rules.append(Rule("Training rows", f"{int(d['n_train'].iloc[t]):,}", None))
        side = "in the long basket" if w > 0 else "in the short basket" if w < 0 else "not selected"
        if not np.isfinite(pred):
            return "The model has no prediction for this bar yet.", rules
        drivers = ", ".join(f"{label} ({fmt_pct(c, 2)})" for label, c in contribs[:2])
        head = (
            f"Model expects {symbol} to {'beat' if pred > 0 else 'lag'} the group by {fmt_pct(abs(pred), 2)} over "
            f"{p['horizon']} bars, mainly from {drivers}; {side}."
        )
        if np.isfinite(ic) and n_ic >= 3 and ic <= 0:
            head += f" Caution: its past predictions have not beaten chance (IC {fmt_num(ic)})."
        return head, rules

    def exit_rule(self, state):
        return f"Re-ranked when the model retrains every {self.params['rebalance']} bars."
