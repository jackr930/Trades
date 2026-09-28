"""Synthetic market generator.

Used for the offline demo market, simulator training scenarios and tests. The model
combines well-known stylised facts of equity returns:

* Markov regime switching between bull / bear / range-bound states
  (Hamilton, 1989, Econometrica 57(2));
* volatility clustering via a GARCH(1,1) multiplier (Bollerslev, 1986, J. Econometrics 31(3));
* fat-tailed Student-t shocks and Poisson jumps (Merton, 1976, J. Financial Economics 3);
* a one-factor structure (market beta + idiosyncratic risk) across the demo universe;
* a cointegrated pair (log-price spread follows an Ornstein-Uhlenbeck process).

Everything is deterministic for a given seed and *prefix-stable*: extending the
calendar never changes previously generated bars, because every random component
draws from its own stream.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import numpy as np
import pandas as pd

from trades.core.calendar import NY, last_completed_session, next_trading_day, session_bounds, trading_days
from trades.core.timeframes import TRADING_DAYS_PER_YEAR, Timeframe
from trades.data.base import DataProvider, ProviderInfo, Quote, SymbolNotFound, slice_bars

BASE_START = date(2010, 1, 4)
SCENARIO_BASE_DATE = date(2040, 1, 2)  # clearly fictional dates for training scenarios


@dataclass(frozen=True)
class Regime:
    name: str
    drift: float  # annualised expected simple return
    vol: float  # annualised volatility
    mean_reversion: float = 0.0  # annual OU speed toward the level at regime entry


@dataclass(frozen=True)
class MarketModel:
    regimes: tuple[Regime, ...]
    transition: tuple[tuple[float, ...], ...] | None = None  # per-bar Markov matrix
    script: tuple[tuple[int, int], ...] | None = None  # fixed (regime index, bars) timeline
    garch_alpha: float = 0.07
    garch_beta: float = 0.91
    t_dof: float = 5.0
    jump_intensity: float = 1.0  # expected jumps per year
    jump_mean: float = -0.02
    jump_std: float = 0.04
    start_price: float = 100.0
    initial_regime: int = 0


BULL = Regime("bull", 0.20, 0.13)
BEAR = Regime("bear", -0.30, 0.30)
RANGE = Regime("range", 0.02, 0.16, mean_reversion=4.0)

DEFAULT_MARKET = MarketModel(
    regimes=(BULL, BEAR, RANGE),
    transition=(
        (0.996, 0.002, 0.002),
        (0.010, 0.985, 0.005),
        (0.006, 0.004, 0.990),
    ),
)


def stable_seed(*parts: object) -> int:
    return zlib.crc32("|".join(str(p) for p in parts).encode()) & 0x7FFFFFFF


def _streams(seed: int, n: int) -> list[np.random.Generator]:
    return [np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(n)]


def simulate_log_returns(
    model: MarketModel, n: int, seed: int, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Simulate ``n`` per-bar log returns. Returns (log_returns, per-bar sigma, regime index)."""
    rng_regime, rng_z, rng_jn, rng_js = _streams(seed, 4)
    dt = 1.0 / periods_per_year

    # Regime path
    if model.script:
        regimes = np.concatenate([np.full(bars, idx, dtype=int) for idx, bars in model.script])
        if len(regimes) < n:
            regimes = np.concatenate([regimes, np.full(n - len(regimes), regimes[-1], dtype=int)])
        regimes = regimes[:n]
    elif model.transition is not None and len(model.regimes) > 1:
        cum = np.cumsum(np.asarray(model.transition, dtype=float), axis=1)
        u = rng_regime.random(n)
        regimes = np.empty(n, dtype=int)
        state = model.initial_regime
        for t in range(n):
            regimes[t] = state
            state = int(min(np.searchsorted(cum[state], u[t], side="right"), len(model.regimes) - 1))
    else:
        regimes = np.full(n, model.initial_regime, dtype=int)

    # Shocks: unit-variance Student-t
    dof = model.t_dof
    z = rng_z.standard_t(dof, n) * math.sqrt((dof - 2.0) / dof) if dof > 2 else rng_z.standard_normal(n)
    counts = rng_jn.poisson(model.jump_intensity * dt, n)
    jump_noise = rng_js.standard_normal(n)
    jumps = counts * model.jump_mean + np.sqrt(counts) * model.jump_std * jump_noise

    drift = np.array([r.drift for r in model.regimes])[regimes]
    vol = np.array([r.vol for r in model.regimes])[regimes]
    kappa = np.array([r.mean_reversion for r in model.regimes])[regimes]
    base_var = vol**2 * dt

    a, b = model.garch_alpha, model.garch_beta
    out = np.empty(n)
    sig = np.empty(n)
    g = 1.0  # GARCH multiplier with unconditional mean 1
    x = 0.0  # log price relative to start
    anchor = 0.0
    prev_regime = -1
    for t in range(n):
        if regimes[t] != prev_regime:
            anchor = x
            prev_regime = regimes[t]
        if t > 0:
            g = (1.0 - a - b) + (a * z[t - 1] ** 2 + b) * g
        h = base_var[t] * g
        s = math.sqrt(h)
        r = (drift[t] - 0.5 * vol[t] ** 2) * dt + s * z[t] + jumps[t]
        if kappa[t] > 0:
            r += kappa[t] * dt * (anchor - x)
        out[t] = r
        sig[t] = s
        x += r
    return out, sig, regimes


def returns_to_ohlcv(
    log_returns: np.ndarray,
    sigma: np.ndarray,
    start_price: float,
    seed: int,
    *,
    gap_fraction: float = 0.25,
    bridge_steps: int = 16,
    base_volume: float = 2_000_000.0,
) -> pd.DataFrame:
    """Turn close-to-close log returns into realistic OHLCV bars.

    The overnight gap and the intraday move are the two Gaussian pieces of each bar's
    return (gap | r ~ N(f r, f(1-f) sigma^2)); highs and lows come from a Brownian
    bridge between open and close. Volume rises with volatility and absolute returns.
    """
    n = len(log_returns)
    rng_gap, rng_path, rng_vol = _streams(seed, 3)
    log_close = math.log(start_price) + np.cumsum(log_returns)
    prev_close = np.concatenate([[math.log(start_price)], log_close[:-1]])
    f = gap_fraction
    gap = f * log_returns + math.sqrt(f * (1 - f)) * sigma * rng_gap.standard_normal(n)
    log_open = prev_close + gap

    k = bridge_steps
    step_sd = sigma * math.sqrt((1 - f) / k)
    incr = rng_path.standard_normal((n, k)) * step_sd[:, None]
    walk = np.concatenate([np.zeros((n, 1)), np.cumsum(incr, axis=1)], axis=1)
    frac = np.linspace(0.0, 1.0, k + 1)[None, :]
    bridge = walk - frac * walk[:, -1:]
    path = log_open[:, None] + bridge + frac * (log_close - log_open)[:, None]
    high = np.exp(path.max(axis=1))
    low = np.exp(path.min(axis=1))

    # Reference volatility from the first year only, so extending the series never
    # changes earlier bars (prefix stability).
    ref = float(np.median(sigma[:252])) if n else 1.0
    ref = ref if ref > 0 else 1.0
    with np.errstate(divide="ignore", invalid="ignore"):
        surprise = np.where(sigma > 0, np.abs(log_returns) / sigma, 0.0)
    log_v = (
        math.log(base_volume)
        + 0.6 * np.log(np.maximum(sigma, 1e-12) / ref)
        + 0.3 * np.minimum(surprise, 6.0)
        + 0.25 * rng_vol.standard_normal(n)
    )
    return pd.DataFrame(
        {
            "open": np.exp(log_open),
            "high": high,
            "low": low,
            "close": np.exp(log_close),
            "volume": np.round(np.exp(log_v)),
        }
    )


# --------------------------------------------------------------------------------------
# Demo universe
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SyntheticAsset:
    symbol: str
    name: str
    beta: float
    idio_vol: float
    alpha: float
    start_price: float
    jump_intensity: float = 3.0
    jump_std: float = 0.05
    base_volume: float = 3_000_000.0


UNIVERSE: tuple[SyntheticAsset, ...] = (
    SyntheticAsset("SIMIDX", "Broad-market index fund", 1.0, 0.015, 0.0, 120.0, 0.0, 0.0, 60_000_000),
    SyntheticAsset("SIMTEC", "Large-cap technology", 1.3, 0.24, 0.03, 150.0),
    SyntheticAsset("SIMBNK", "Money-center bank", 1.15, 0.17, -0.01, 45.0),
    SyntheticAsset("SIMNRG", "Integrated energy", 0.8, 0.26, 0.0, 70.0),
    SyntheticAsset("SIMUTL", "Regulated utility", 0.45, 0.12, 0.01, 55.0, 1.0, 0.03),
    SyntheticAsset("SIMHLC", "Healthcare", 0.75, 0.19, 0.02, 95.0),
    SyntheticAsset("SIMBND", "Treasury bond fund", -0.15, 0.06, 0.015, 100.0, 0.0, 0.0, 20_000_000),
    SyntheticAsset("SIMGLD", "Gold trust", 0.05, 0.15, 0.02, 160.0, 1.0, 0.03),
)
PAIR_A, PAIR_B = "SIMPRA", "SIMPRB"  # cointegrated pair (e.g. two share classes / close competitors)
PAIR_INFO = {
    PAIR_A: "Cointegrated with SIMPRB (pairs-trading demo)",
    PAIR_B: "Cointegrated with SIMPRA (pairs-trading demo)",
}
_ASSETS = {a.symbol: a for a in UNIVERSE}


def _generic_asset(symbol: str) -> SyntheticAsset:
    rng = np.random.default_rng(stable_seed("asset", symbol))
    return SyntheticAsset(
        symbol=symbol,
        name="Synthetic stock",
        beta=float(rng.uniform(0.6, 1.4)),
        idio_vol=float(rng.uniform(0.15, 0.4)),
        alpha=float(rng.normal(0.0, 0.03)),
        start_price=float(np.exp(rng.uniform(np.log(15), np.log(400)))),
    )


def universe_info() -> list[dict]:
    rows = [{"symbol": a.symbol, "name": a.name, "beta": a.beta, "idio_vol": a.idio_vol} for a in UNIVERSE]
    rows += [{"symbol": s, "name": d, "beta": None, "idio_vol": None} for s, d in PAIR_INFO.items()]
    return rows


class SyntheticMarket:
    """Generates the whole demo universe on a NYSE calendar from ``BASE_START``."""

    def __init__(self, seed: int = 7):
        self.seed = seed

    @lru_cache(maxsize=8)  # noqa: B019 - small, bounded cache on a long-lived object
    def _market(self, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return simulate_log_returns(DEFAULT_MARKET, n, stable_seed("market", self.seed))

    @lru_cache(maxsize=128)  # noqa: B019
    def _symbol_path(self, symbol: str, n: int) -> tuple[np.ndarray, np.ndarray, float, float]:
        """Per-bar log returns, per-bar sigma, start price and base volume for a symbol."""
        market_r, market_sig, _ = self._market(n)
        if symbol == PAIR_A:
            b_r, b_sig, _, b_vol = self._symbol_path(PAIR_B, n)
            (rng_s,) = _streams(stable_seed("spread", self.seed), 1)
            kappa = math.log(2) / (15 / TRADING_DAYS_PER_YEAR)  # 15-day half-life
            sd = 0.045 * math.sqrt(2 * kappa)  # stationary sd of spread ~4.5%
            dt = 1 / TRADING_DAYS_PER_YEAR
            shocks = rng_s.standard_normal(n) * sd * math.sqrt(dt)
            spread = np.empty(n)
            s = 0.0
            for t in range(n):
                s = s - kappa * dt * s + shocks[t]
                spread[t] = s
            d_spread = np.diff(np.concatenate([[0.0], spread]))
            hedge = 0.9
            r = hedge * b_r + d_spread + 0.01 / TRADING_DAYS_PER_YEAR
            sig = np.sqrt((hedge * b_sig) ** 2 + (sd * math.sqrt(dt)) ** 2)
            return r, sig, 62.0, 4_000_000.0
        asset = _ASSETS.get(symbol)
        if asset is None:
            asset = (
                SyntheticAsset(PAIR_B, "Pair leg B", 1.0, 0.2, 0.02, 58.0)
                if symbol == PAIR_B
                else _generic_asset(symbol)
            )
        idio_model = MarketModel(
            regimes=(Regime("idio", asset.alpha, asset.idio_vol),),
            jump_intensity=asset.jump_intensity,
            jump_mean=0.0,
            jump_std=asset.jump_std,
            garch_alpha=0.05,
            garch_beta=0.93,
        )
        idio_r, idio_sig, _ = simulate_log_returns(idio_model, n, stable_seed("idio", symbol, self.seed))
        r = asset.beta * market_r + idio_r
        sig = np.sqrt((asset.beta * market_sig) ** 2 + idio_sig**2)
        return r, sig, asset.start_price, asset.base_volume

    def daily_bars(self, symbol: str, end: date) -> pd.DataFrame:
        symbol = symbol.upper()
        days = trading_days(BASE_START, end)
        if not days:
            raise SymbolNotFound(f"no synthetic data before {BASE_START}")
        r, sig, p0, vol0 = self._symbol_path(symbol, len(days))
        df = returns_to_ohlcv(r, sig, p0, stable_seed("ohlc", symbol, self.seed), base_volume=vol0)
        df.index = pd.DatetimeIndex(pd.to_datetime(days)).tz_localize("UTC")
        df.index.name = "time"
        return df

    def intraday_path(self, symbol: str, day_bar: pd.Series, day: date, substeps: int = 6) -> np.ndarray:
        """Log-price path at ``substeps`` points per minute from the day's open to close."""
        open_dt, close_dt = session_bounds(day)
        minutes = int((close_dt - open_dt).total_seconds() // 60)
        k = minutes * substeps
        rng = np.random.default_rng(stable_seed("intraday", symbol, day.toordinal(), self.seed))
        lo, lc = math.log(day_bar["open"]), math.log(day_bar["close"])
        # daily sigma implied by the bar's range (Parkinson estimator), floored
        rng_sigma = max(math.log(day_bar["high"] / day_bar["low"]) / (2 * math.sqrt(math.log(2))), 1e-4)
        incr = rng.standard_normal(k) * rng_sigma / math.sqrt(k)
        walk = np.concatenate([[0.0], np.cumsum(incr)])
        frac = np.linspace(0.0, 1.0, k + 1)
        return lo + walk - frac * walk[-1] + frac * (lc - lo)

    def intraday_bars(self, symbol: str, day_bar: pd.Series, day: date, timeframe: Timeframe) -> pd.DataFrame:
        substeps = 6
        path = self.intraday_path(symbol, day_bar, day, substeps)
        open_dt, close_dt = session_bounds(day)
        minutes = (len(path) - 1) // substeps
        step = timeframe.minutes
        rows = []
        n_bars = -(-minutes // step)
        profile = 1.0 + 1.5 * (2 * (np.arange(n_bars) + 0.5) / n_bars - 1.0) ** 2  # U-shaped volume
        vol_rng = np.random.default_rng(stable_seed("ivol", symbol, day.toordinal(), step))
        weights = profile * np.exp(0.3 * vol_rng.standard_normal(n_bars))
        weights /= weights.sum()
        for i in range(n_bars):
            a = i * step * substeps
            b = min((i + 1) * step * substeps, len(path) - 1)
            seg = np.exp(path[a : b + 1])
            rows.append((seg[0], seg.max(), seg.min(), seg[-1], round(day_bar["volume"] * weights[i])))
        idx = pd.DatetimeIndex([open_dt + timedelta(minutes=i * step) for i in range(n_bars)]).tz_convert(
            "UTC"
        )
        df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)
        df.index.name = "time"
        return df


class SyntheticProvider(DataProvider):
    info = ProviderInfo(
        id="synthetic",
        label="Synthetic demo market",
        description=(
            "Offline, deterministic simulated market (regime-switching GARCH with jumps). "
            "Works without an internet connection or API key. Symbols SIMIDX, SIMTEC, SIMBNK, "
            "SIMNRG, SIMUTL, SIMHLC, SIMBND, SIMGLD plus the cointegrated pair SIMPRA/SIMPRB; "
            "any other ticker gets its own synthetic series. NOT real market data."
        ),
        requires_key=False,
        realtime="simulated",
        timeframes=(Timeframe.D1, Timeframe.H1, Timeframe.M15, Timeframe.M5, Timeframe.M1),
    )

    def __init__(self, seed: int = 7, end: date | None = None, extra_days: int = 0):
        self.market = SyntheticMarket(seed)
        self._fixed_end = end
        self.extra_days = extra_days
        self._cache: dict[tuple[str, date], pd.DataFrame] = {}

    def end_date(self) -> date:
        end = self._fixed_end or last_completed_session()
        for _ in range(self.extra_days):
            end = next_trading_day(end)
        return end

    def daily(self, symbol: str) -> pd.DataFrame:
        key = (symbol.upper(), self.end_date())
        df = self._cache.get(key)
        if df is None:
            df = self.market.daily_bars(symbol, key[1])
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[key] = df
        return df.copy()

    def history(self, symbol, timeframe=Timeframe.D1, start=None, end=None) -> pd.DataFrame:
        timeframe = Timeframe.parse(timeframe)
        daily = self.daily(symbol)
        if timeframe is Timeframe.D1:
            return slice_bars(daily, start, end)
        # Intraday: build from each day's bar; default to the last 30 sessions.
        window = slice_bars(daily, start, end) if (start or end) else daily
        if start is None:
            window = window.iloc[-30:]
        frames = [
            self.market.intraday_bars(symbol.upper(), row, ts.date(), timeframe)
            for ts, row in window.iterrows()
        ]
        if not frames:
            return window.iloc[0:0]
        out = pd.concat(frames)
        return (
            slice_bars(out, start, end) if isinstance(start, datetime) or isinstance(end, datetime) else out
        )

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out = {}
        for sym in symbols:
            df = self.daily(sym)
            last, prev = df.iloc[-1], df.iloc[-2]
            ts = datetime.combine(df.index[-1].date(), datetime.min.time(), NY) + timedelta(hours=16)
            out[sym] = Quote(
                symbol=sym,
                price=float(last["close"]),
                timestamp=ts.astimezone(timezone.utc),
                prev_close=float(prev["close"]),
                open=float(last["open"]),
                high=float(last["high"]),
                low=float(last["low"]),
                volume=float(last["volume"]),
                source="synthetic",
            )
        return out

    def check(self) -> tuple[bool, str]:
        return True, "Synthetic market is always available (offline)."


# --------------------------------------------------------------------------------------
# Training scenarios (single synthetic series on fictional dates)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    id: str
    label: str
    description: str
    model: MarketModel
    bars: int = 520  # total bars including warm-up history
    difficulty: str = "medium"


SCENARIOS: dict[str, Scenario] = {
    s.id: s
    for s in (
        Scenario(
            "random",
            "Mystery market",
            "Regime-switching market: bull, bear and range-bound phases arrive at random. "
            "You won't know which regime you're in -- just like real life.",
            DEFAULT_MARKET,
            difficulty="medium",
        ),
        Scenario(
            "steady_bull",
            "Steady bull market",
            "A persistent uptrend with modest volatility. Tests whether you can stay invested "
            "instead of taking profits too early.",
            MarketModel(regimes=(Regime("bull", 0.24, 0.15),), jump_intensity=1.0),
            difficulty="easy",
        ),
        Scenario(
            "crash",
            "Calm, then crash",
            "A calm advance turns into a violent bear market and an uneven recovery "
            "(loosely shaped like 2007-2009). Tests risk management and stop discipline.",
            MarketModel(
                regimes=(
                    Regime("calm", 0.14, 0.12),
                    Regime("crash", -0.70, 0.48),
                    Regime("bear rally", 0.30, 0.36),
                    Regime("recovery", 0.28, 0.22),
                ),
                script=((0, 280), (1, 90), (2, 40), (1, 30), (3, 120)),
                jump_intensity=2.0,
                jump_mean=-0.03,
            ),
            bars=560,
            difficulty="hard",
        ),
        Scenario(
            "range",
            "Range-bound chop",
            "Prices oscillate around a level with no lasting trend. Trend-followers get "
            "whipsawed; mean-reversion shines.",
            MarketModel(regimes=(Regime("range", 0.0, 0.24, mean_reversion=9.0),), jump_intensity=0.5),
            difficulty="medium",
        ),
        Scenario(
            "bubble",
            "Melt-up and bust",
            "A steady market accelerates into a speculative melt-up, then collapses "
            "(loosely shaped like 1999-2002). Tests greed and exit discipline.",
            MarketModel(
                regimes=(
                    Regime("steady", 0.10, 0.16),
                    Regime("melt-up", 0.85, 0.32),
                    Regime("bust", -0.65, 0.50),
                ),
                script=((0, 270), (1, 130), (2, 120)),
                jump_intensity=1.5,
            ),
            bars=520,
            difficulty="hard",
        ),
        Scenario(
            "whipsaw",
            "High-volatility whipsaw",
            "Frequent, sharp regime changes in both directions with high volatility. "
            "Tests position sizing and patience.",
            MarketModel(
                regimes=(Regime("up", 0.45, 0.38), Regime("down", -0.45, 0.42)),
                transition=((0.96, 0.04), (0.04, 0.96)),
                jump_intensity=4.0,
                jump_std=0.06,
            ),
            difficulty="hard",
        ),
        Scenario(
            "low_vol_grind",
            "Low-volatility grind",
            "A slow, quiet uptrend with rare shocks. Tests patience and over-trading.",
            MarketModel(regimes=(Regime("grind", 0.11, 0.09),), jump_intensity=0.3, jump_mean=-0.04),
            difficulty="easy",
        ),
    )
}


def _scenario(
    scenario_id: str, seed: int, bars: int | None = None
) -> tuple[pd.DataFrame, np.ndarray, Scenario]:
    sc = SCENARIOS.get(scenario_id)
    if sc is None:
        raise KeyError(f"unknown scenario {scenario_id!r}")
    n = bars or sc.bars
    rng = np.random.default_rng(stable_seed("scenario-setup", scenario_id, seed))
    model = replace(sc.model, start_price=float(np.exp(rng.uniform(np.log(20), np.log(250)))))
    if model.transition is not None and not model.script:
        model = replace(model, initial_regime=int(rng.integers(len(model.regimes))))
    r, sig, regimes = simulate_log_returns(model, n, stable_seed("scenario", scenario_id, seed))
    df = returns_to_ohlcv(r, sig, model.start_price, stable_seed("scenario-ohlc", scenario_id, seed))
    days = trading_days(SCENARIO_BASE_DATE, SCENARIO_BASE_DATE + timedelta(days=int(n * 1.5) + 30))[:n]
    df.index = pd.DatetimeIndex(pd.to_datetime(days)).tz_localize("UTC")
    df.index.name = "time"
    return df, regimes, sc


def scenario_bars(scenario_id: str, seed: int, bars: int | None = None) -> pd.DataFrame:
    """Daily bars for a training scenario on fictional dates starting in 2040."""
    return _scenario(scenario_id, seed, bars)[0]


def scenario_regimes(scenario_id: str, seed: int, bars: int | None = None) -> list[dict]:
    """The hidden regime timeline of a scenario: [{regime, start, end}] in bar indices."""
    _, regimes, sc = _scenario(scenario_id, seed, bars)
    out: list[dict] = []
    for i, k in enumerate(regimes):
        name = sc.model.regimes[int(k)].name
        if out and out[-1]["regime"] == name and out[-1]["end"] == i - 1:
            out[-1]["end"] = i
        else:
            out.append({"regime": name, "start": i, "end": i})
    return out
