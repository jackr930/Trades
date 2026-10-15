"""Time-series statistics used as strategy features: OLS, unit-root and cointegration tests,
and the half-life of mean reversion.

References
----------
* Dickey, D. A. & Fuller, W. A. (1979). Distribution of the Estimators for Autoregressive
  Time Series with a Unit Root. JASA 74(366).
* MacKinnon, J. G. (2010). Critical Values for Cointegration Tests. Queen's Economics
  Department Working Paper No. 1227.
* Engle, R. F. & Granger, C. W. J. (1987). Co-integration and Error Correction.
  Econometrica 55(2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


def _clean(values: pd.Series | np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    return arr[np.isfinite(arr)]


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

