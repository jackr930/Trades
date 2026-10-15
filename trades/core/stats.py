"""Statistics used for strategy evaluation.

References
----------
* Lo, A. W. (2002). The Statistics of Sharpe Ratios. Financial Analysts Journal 58(4).
* Bailey, D. H. & Lopez de Prado, M. (2012). The Sharpe Ratio Efficient Frontier.
  Journal of Risk 15(2) -- Probabilistic Sharpe Ratio (PSR).
* Bailey, D. H. & Lopez de Prado, M. (2014). The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting and Non-Normality. Journal of Portfolio Management 40(5).
* Dickey, D. A. & Fuller, W. A. (1979). Distribution of the Estimators for Autoregressive
  Time Series with a Unit Root. JASA 74(366).
* MacKinnon, J. G. (2010). Critical Values for Cointegration Tests. Queen's Economics
  Department Working Paper No. 1227.
* Engle, R. F. & Granger, C. W. J. (1987). Co-integration and Error Correction.
  Econometrica 55(2).
* Politis, D. N. & Romano, J. P. (1992). A Circular Block-Resampling Procedure for
  Stationary Data. In Exploring the Limits of Bootstrap, Wiley.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


def _clean(returns: pd.Series | np.ndarray) -> np.ndarray:
    arr = np.asarray(returns, dtype=float)
    return arr[np.isfinite(arr)]


def sharpe_ratio(
    returns: pd.Series | np.ndarray, periods_per_year: float = 1.0, risk_free: float = 0.0
) -> float:
    """Annualised Sharpe ratio of periodic returns (``risk_free`` is annual)."""
    r = _clean(returns) - risk_free / periods_per_year
    if len(r) < 2:
        return float("nan")
    sd = r.std(ddof=1)
    if sd == 0 or not np.isfinite(sd):
        return float("nan")
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def sortino_ratio(returns: pd.Series | np.ndarray, periods_per_year: float = 1.0) -> float:
    r = _clean(returns)
    if len(r) < 2:
        return float("nan")
    downside = np.minimum(r, 0.0)
    dd = math.sqrt(float(np.mean(downside**2)))
    if dd == 0:
        return float("nan")
    return float(r.mean() / dd * math.sqrt(periods_per_year))


def skewness(returns: pd.Series | np.ndarray) -> float:
    r = _clean(returns)
    if len(r) < 3:
        return float("nan")
    d = r - r.mean()
    m2 = np.mean(d**2)
    if m2 == 0:
        return 0.0
    return float(np.mean(d**3) / m2**1.5)


def kurtosis(returns: pd.Series | np.ndarray) -> float:
    """Non-excess kurtosis (a normal distribution has kurtosis 3)."""
    r = _clean(returns)
    if len(r) < 4:
        return float("nan")
    d = r - r.mean()
    m2 = np.mean(d**2)
    if m2 == 0:
        return 3.0
    return float(np.mean(d**4) / m2**2)


@dataclass(frozen=True)
class Drawdown:
    max_drawdown: float  # negative fraction, e.g. -0.25
    peak: int  # index of the peak before the max drawdown
    trough: int
    recovery: int | None  # index where the prior peak was regained (None = not yet)
    longest_duration: int  # longest peak-to-recovery span in bars


def drawdown_series(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    return equity / peak - 1.0


def max_drawdown(equity: pd.Series | np.ndarray) -> Drawdown:
    eq = np.asarray(equity, dtype=float)
    if len(eq) == 0:
        return Drawdown(0.0, 0, 0, None, 0)
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1.0
    trough = int(np.argmin(dd))
    peak = int(np.argmax(eq[: trough + 1])) if trough > 0 else 0
    recovery = None
    after = np.flatnonzero(eq[trough:] >= peaks[trough])
    if len(after):
        recovery = trough + int(after[0])
    # longest time spent below a prior high
    longest = cur = 0
    for v in dd:
        cur = cur + 1 if v < 0 else 0
        longest = max(longest, cur)
    return Drawdown(float(dd[trough]), peak, trough, recovery, longest)


def probabilistic_sharpe_ratio(
    sr: float, n_obs: int, skew: float = 0.0, kurt: float = 3.0, sr_benchmark: float = 0.0
) -> float:
    """P(true SR > sr_benchmark) given an observed *per-period* (non-annualised) Sharpe
    ratio estimated from ``n_obs`` returns with the given skewness and (non-excess)
    kurtosis. Bailey & Lopez de Prado (2012)."""
    if not (np.isfinite(sr) and n_obs > 1):
        return float("nan")
    skew = 0.0 if not np.isfinite(skew) else skew
    kurt = 3.0 if not np.isfinite(kurt) else kurt
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2
    if denom <= 0:
        return float("nan")
    z = (sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom)
    return float(_N.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum of ``n_trials`` per-period Sharpe ratios drawn under the null of
    zero skill, given the variance of the trial Sharpe ratios (False Strategy Theorem,
    Bailey & Lopez de Prado 2014)."""
    if n_trials <= 1 or not np.isfinite(sr_variance) or sr_variance <= 0:
        return 0.0
    a = _N.inv_cdf(1.0 - 1.0 / n_trials)
    b = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(sr_variance) * ((1.0 - EULER_GAMMA) * a + EULER_GAMMA * b))


def deflated_sharpe_ratio(
    sr: float, n_obs: int, skew: float, kurt: float, n_trials: int, sr_variance: float
) -> float:
    """PSR evaluated against the Sharpe ratio one would expect from the best of
    ``n_trials`` skill-less strategies. All Sharpe ratios are per-period."""
    return probabilistic_sharpe_ratio(sr, n_obs, skew, kurt, expected_max_sharpe(n_trials, sr_variance))


def sharpe_standard_error(sr: float, n_obs: int) -> float:
    """Lo (2002) IID standard error of a per-period Sharpe ratio."""
    if n_obs < 2 or not np.isfinite(sr):
        return float("nan")
    return math.sqrt((1.0 + 0.5 * sr**2) / n_obs)


def ols(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """OLS of y on x (x already contains any constant). Returns (beta, std errors,
    residuals, sum of squared residuals)."""
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ beta
    ssr = float(resid @ resid)
    dof = max(len(y) - x.shape[1], 1)
    sigma2 = ssr / dof
    try:
        cov = sigma2 * np.linalg.inv(x.T @ x)
        se = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    except np.linalg.LinAlgError:
        se = np.full(x.shape[1], np.nan)
    return beta, se, resid, ssr


# MacKinnon (2010) response-surface coefficients for the constant-only case, as used by
# statsmodels: crit(T) = b0 + b1/T + b2/T^2 + b3/T^3. Rows: 1%, 5%, 10%.
_MACKINNON_C = {
    1: (
        (-3.43035, -6.5393, -16.786, -79.433),
        (-2.86154, -2.8903, -4.234, -40.04),
        (-2.56677, -1.5384, -2.809, 0.0),
    ),
    2: (
        (-3.89644, -10.9519, -33.527, 0.0),
        (-3.33613, -6.1101, -6.823, 0.0),
        (-3.04445, -4.2412, -2.72, 0.0),
    ),
}


def mackinnon_critical_values(n_obs: int, n_vars: int = 1) -> dict[str, float]:
    out = {}
    for label, coefs in zip(("1%", "5%", "10%"), _MACKINNON_C[n_vars], strict=True):
        b0, b1, b2, b3 = coefs
        t = float(n_obs)
        out[label] = b0 + b1 / t + b2 / t**2 + b3 / t**3
    return out


@dataclass(frozen=True)
class ADFResult:
    stat: float
    lags: int
    n_obs: int
    critical_values: dict[str, float]

    @property
    def reject_5pct(self) -> bool:
        return bool(np.isfinite(self.stat) and self.stat < self.critical_values["5%"])


def _lagged_design(x: np.ndarray, lags: int) -> tuple[np.ndarray, np.ndarray]:
    """Design matrix [level_{t-1}, dx_{t-1}..dx_{t-lags}] and target dx_t (no constant)."""
    dx = np.diff(x)
    n = len(dx) - lags
    cols = [x[lags : lags + n]]  # y_{t-1}
    for i in range(1, lags + 1):
        cols.append(dx[lags - i : lags - i + n])
    return np.column_stack(cols), dx[lags:]


def adf_test(series: pd.Series | np.ndarray, max_lags: int | None = None, constant: bool = True) -> ADFResult:
    """Augmented Dickey-Fuller unit-root test with AIC lag selection -- the same
    procedure as ``statsmodels.tsa.stattools.adfuller``. A more negative statistic is
    stronger evidence of mean reversion (stationarity)."""
    x = _clean(series)
    nobs = len(x)
    n_trend = 1 if constant else 0
    if nobs < 20:
        return ADFResult(float("nan"), 0, nobs, mackinnon_critical_values(max(nobs, 1)))
    if max_lags is None:
        max_lags = int(math.ceil(12.0 * (nobs / 100.0) ** 0.25))
        max_lags = max(min(nobs // 2 - n_trend - 1, max_lags), 0)

    # Lag search on a common sample so the information criteria are comparable.
    design, target = _lagged_design(x, max_lags)
    full = np.column_stack([np.ones(len(target)), design]) if constant else design
    n = len(target)
    best_aic, best_lag = np.inf, 0
    for lag in range(0, max_lags + 1):
        cols = full[:, : n_trend + 1 + lag]
        _, _, _, ssr = ols(target, cols)
        if ssr <= 0:
            continue
        aic = n * (math.log(2 * math.pi) + math.log(ssr / n) + 1.0) + 2.0 * cols.shape[1]
        if aic < best_aic:
            best_aic, best_lag = aic, lag

    design, target = _lagged_design(x, best_lag)
    x_mat = np.column_stack([design, np.ones(len(target))]) if constant else design
    beta, se, _, _ = ols(target, x_mat)
    stat = float(beta[0] / se[0]) if se[0] > 0 else float("nan")
    return ADFResult(stat, best_lag, len(target), mackinnon_critical_values(len(target)))


def ou_half_life(spread: pd.Series | np.ndarray) -> float:
    """Half-life (in bars) of mean reversion from an AR(1) fit ds_t = a + b s_{t-1}.
    Returns inf when the series shows no mean reversion (b >= 0)."""
    s = _clean(spread)
    if len(s) < 10:
        return float("nan")
    ds = np.diff(s)
    x = np.column_stack([np.ones(len(ds)), s[:-1]])
    beta, *_ = ols(ds, x)
    b = beta[1]
    if b >= 0:
        return float("inf")
    return float(-math.log(2.0) / math.log1p(b)) if b > -1 else 0.0


@dataclass(frozen=True)
class CointegrationResult:
    beta: float
    alpha: float
    adf: ADFResult
    half_life: float

    @property
    def cointegrated(self) -> bool:
        return self.adf.reject_5pct


def engle_granger(y: pd.Series | np.ndarray, x: pd.Series | np.ndarray) -> CointegrationResult:
    """Engle-Granger two-step test: regress y on x (with intercept), then ADF-test the
    residual without a constant against MacKinnon's two-variable critical values
    (matches ``statsmodels.tsa.stattools.coint``)."""
    ya, xa = np.asarray(y, dtype=float), np.asarray(x, dtype=float)
    mask = np.isfinite(ya) & np.isfinite(xa)
    ya, xa = ya[mask], xa[mask]
    beta, _, resid, _ = ols(ya, np.column_stack([np.ones(len(xa)), xa]))
    raw = adf_test(resid, constant=False)
    adf = ADFResult(raw.stat, raw.lags, raw.n_obs, mackinnon_critical_values(len(ya) - 1, n_vars=2))
    return CointegrationResult(float(beta[1]), float(beta[0]), adf, ou_half_life(resid))


def outperformance_probability(
    returns: pd.Series | np.ndarray,
    benchmark: pd.Series | np.ndarray,
    n_boot: int = 1000,
    block: int = 21,
    seed: int = 7,
    risk_free: pd.Series | np.ndarray | None = None,
) -> dict[str, float]:
    """How often a strategy beats a benchmark across resampled histories.

    A paired circular block bootstrap (Politis & Romano 1992): blocks of ``block``
    consecutive days are drawn with replacement, the same days for both series, so the
    resampled paths keep short-term autocorrelation and the two series' co-movement.

    * ``p_growth``: share of resamples in which the strategy's average log return, and so
      its compound growth rate, is higher than the benchmark's;
    * ``p_sharpe``: share in which its Sharpe ratio (in excess of ``risk_free``, per-bar cash
      returns, if given) is higher.

    Not a guarantee of the future: it only asks whether the historical gap is larger than
    the luck of which days happened to occur. The seed is fixed so results are reproducible.
    """
    a = np.asarray(returns, dtype=float)
    b = np.asarray(benchmark, dtype=float)
    f = np.zeros(len(a)) if risk_free is None else np.nan_to_num(np.asarray(risk_free, dtype=float))
    ok = np.isfinite(a) & np.isfinite(b) & (a > -1) & (b > -1) & (f > -1)
    a, b, f = np.log1p(a[ok]), np.log1p(b[ok]), np.log1p(f[ok])
    T = len(a)
    if T < 2 * block or n_boot < 1:
        return {"p_growth": float("nan"), "p_sharpe": float("nan")}
    rng = np.random.default_rng(seed)
    n_blocks = -(-T // block)
    wins_growth = wins_sharpe = 0
    done = 0
    while done < n_boot:  # in chunks, to keep memory small for long histories
        n = min(200, n_boot - done)
        starts = rng.integers(0, T, size=(n, n_blocks))
        idx = ((starts[:, :, None] + np.arange(block)) % T).reshape(n, -1)[:, :T]
        ra, rb = a[idx], b[idx]
        wins_growth += int(np.sum(ra.mean(axis=1) > rb.mean(axis=1)))
        ra, rb = ra - f[idx], rb - f[idx]  # Sharpe ratios in excess of cash
        with np.errstate(divide="ignore", invalid="ignore"):
            sa = ra.mean(axis=1) / ra.std(axis=1, ddof=1)
            sb = rb.mean(axis=1) / rb.std(axis=1, ddof=1)
        wins_sharpe += int(np.sum(np.nan_to_num(sa, nan=-np.inf) > np.nan_to_num(sb, nan=-np.inf)))
        done += n
    return {"p_growth": wins_growth / n_boot, "p_sharpe": wins_sharpe / n_boot}


def kelly_leverage(returns: pd.Series | np.ndarray) -> float:
    """Continuous-time Kelly-optimal leverage mu/sigma^2 of periodic returns (Thorp 2006).
    Extremely sensitive to estimation error -- practitioners use a fraction (e.g. 1/2)."""
    r = _clean(returns)
    if len(r) < 2:
        return float("nan")
    var = r.var(ddof=1)
    if var == 0:
        return float("nan")
    return float(r.mean() / var)
