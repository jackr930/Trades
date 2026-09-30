"""An incremental simulated market you can steer while strategies trade it.

Bars are generated one at a time with the same stylised facts as the synthetic history
(regime switching, GARCH volatility clustering, fat-tailed shocks, jumps, a one-factor
structure across stocks and a cointegrated pair). While a simulation runs you can
inject events -- a crash, a rally, a volatility spike, a forced regime, an earnings gap
in one stock, or a structural break in the pair -- and watch how each strategy reacts.

Randomness is *counter-based*: bar ``t`` of a symbol always uses the same random
numbers for a given seed, whatever happened before. Injecting an event therefore
changes the path only through the event itself, so "what if it had crashed here?" is a
controlled experiment, and bars already generated ahead of the visible cursor can be
regenerated after an injection.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from trades.core.calendar import trading_days
from trades.core.timeframes import TRADING_DAYS_PER_YEAR
from trades.data.synthetic import (
    _ASSETS,
    PAIR_A,
    PAIR_B,
    SCENARIO_BASE_DATE,
    SCENARIOS,
    MarketModel,
    Regime,
    SyntheticAsset,
    _generic_asset,
    stable_seed,
)

DT = 1.0 / TRADING_DAYS_PER_YEAR
GAP_FRACTION = 0.25
BRIDGE_STEPS = 16
# Guards so that any pile-up of injected events stays a (wild) market rather than a numeric
# overflow: overlapping volatility spikes multiply, and crashes compound the GARCH state.
MAX_VOL_MULT = 6.0
MAX_GARCH = 30.0
MAX_BAR_MOVE = 0.7  # |log return| per bar (about -50% / +100%)
MIN_EVENT_RETURN = -0.5  # the largest one-bar drop an event can ask for without being clipped
LOG_PRICE_BOUNDS = (math.log(0.01), math.log(1e7))

# Regimes an event can force, whatever the scenario's own regimes are.
ARCHETYPES = {
    "bull": Regime("bull", 0.25, 0.14),
    "bear": Regime("bear", -0.40, 0.32),
    "range": Regime("range", 0.0, 0.18, mean_reversion=8.0),
    # Drift strong enough that an injected crash reliably keeps falling instead of being
    # swamped by its own (boosted) volatility: over 30 bars the median move including a 10%
    # gap is about -27%, and fewer than 1 in 20 paths end down less than 10%.
    "crash": Regime("crash", -1.50, 0.28),
}

EVENT_KINDS: dict[str, dict[str, Any]] = {
    "crash": {
        "label": "Crash",
        "size": -0.10,
        "bars": 30,
        "help": "The market gaps down by `size` at the next open, then keeps falling with high volatility "
        "for `bars` bars (typically another 15-20% over 30 bars). Stocks move with their beta.",
    },
    "rally": {
        "label": "Rally",
        "size": 0.06,
        "bars": 40,
        "help": "The market gaps up by `size` at the next open and a bull regime follows for `bars` bars.",
    },
    "vol_spike": {
        "label": "Volatility spike",
        "size": 2.5,
        "bars": 20,
        "help": "All shocks are `size` times larger for `bars` bars; direction stays random.",
    },
    "regime": {
        "label": "Force a regime",
        "regime": "range",
        "bars": 60,
        "help": "Replace the hidden regime with bull, bear, range-bound or crash for `bars` bars.",
    },
    "gap": {
        "label": "Earnings gap",
        "size": -0.15,
        "bars": 1,
        "help": "One stock gaps by `size` at the next open (an earnings surprise); the market is unaffected.",
    },
    "break_pair": {
        "label": "Break the pair",
        "size": 0.20,
        "bars": 1,
        "help": f"The long-run relationship between {PAIR_A} and {PAIR_B} shifts by `size` for good: the "
        "spread drifts to a new level instead of reverting. The classic way pairs trades lose.",
    },
}


@dataclass
class MarketEvent:
    id: int
    kind: str
    start: int  # first bar the event affects
    bars: int
    size: float | None = None
    symbol: str | None = None
    regime: str | None = None
    description: str = ""

    def active(self, t: int) -> bool:
        return self.start <= t < self.start + max(self.bars, 1)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["label"] = EVENT_KINDS[self.kind]["label"]
        return d


def describe_event(kind: str, size: float | None, bars: int, symbol: str | None, regime: str | None) -> str:
    if kind == "crash":
        return f"Crash: market gaps {size:+.0%}, then a high-volatility crash regime for {bars} bars."
    if kind == "rally":
        return f"Rally: market gaps {size:+.0%}, then a bull regime for {bars} bars."
    if kind == "vol_spike":
        return f"Volatility spike: shocks {size:g}x larger for {bars} bars."
    if kind == "regime":
        return f"Forced {regime} regime for {bars} bars."
    if kind == "gap":
        return f"Earnings gap: {symbol} opens {size:+.0%}."
    return f"Pair break: the {PAIR_A}/{PAIR_B} equilibrium shifts {size:+.0%} for good."


@dataclass
class _SymbolState:
    g: float = 1.0  # GARCH multiplier of the idiosyncratic shock
    z_prev: float = 0.0
    log_close: float = 0.0


@dataclass
class _State:
    """Everything the next bar depends on apart from its (counter-based) random numbers."""

    chain: int  # underlying Markov regime
    effective: str  # regime actually used on the last bar (name)
    g: float = 1.0  # market GARCH multiplier
    z_prev: float = 0.0
    x: float = 0.0  # market log level
    anchor: float = 0.0  # level at the start of the current regime (mean reversion)
    spread: float = 0.0  # pair spread (log)
    spread_mean: float = 0.0
    symbols: dict[str, _SymbolState] | None = None

    def copy(self) -> _State:
        return replace(self, symbols={k: replace(v) for k, v in (self.symbols or {}).items()})


def _live_script(model: MarketModel, warmup: int) -> tuple[tuple[int, int], ...] | None:
    """A scenario's regime script for a live run: the warm-up happens in the first regime,
    whose live part is shortened so the story starts soon after the run does."""
    if not model.script:
        return None
    (r0, b0), *rest = model.script
    return ((r0, warmup + min(b0, 60)), *rest)


class SimulatedMarket:
    """Multi-asset bar generator with event injection (see module docstring)."""

    def __init__(
        self,
        symbols: list[str],
        scenario: str = "random",
        seed: int = 1,
        warmup: int = 300,
    ):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}")
        if not symbols:
            raise ValueError("pick at least one symbol")
        self.scenario = SCENARIOS[scenario]
        self.seed = int(seed)
        self.symbols = [s.upper() for s in dict.fromkeys(symbols)]
        self.warmup = int(warmup)
        model = self.scenario.model
        rng = np.random.default_rng(stable_seed("arena-setup", scenario, self.seed))
        if model.transition is not None and not model.script:
            model = replace(model, initial_regime=int(rng.integers(len(model.regimes))))
        self.model = model
        self.script = _live_script(model, self.warmup)
        self._script_regimes = (
            np.concatenate([np.full(n, i, dtype=int) for i, n in self.script]) if self.script else None
        )
        # Pair leg A is built from leg B, so B is simulated whenever A is.
        self._simulated = list(self.symbols)
        if PAIR_A in self._simulated and PAIR_B not in self._simulated:
            self._simulated.append(PAIR_B)
        self._assets = {s: self._asset(s) for s in self._simulated}
        self._cum = np.cumsum(np.asarray(model.transition, float), axis=1) if model.transition else None

        n_sym = len(self._simulated)
        self._o = np.empty((0, n_sym))
        self._h = np.empty((0, n_sym))
        self._l = np.empty((0, n_sym))
        self._c = np.empty((0, n_sym))
        self._v = np.empty((0, n_sym))
        self._regimes: list[str] = []
        self._states: list[_State] = []  # state *after* each generated bar
        self._paths: dict[int, np.ndarray] = {}  # intrabar log-price paths of recent bars
        self._dates: list = []
        self.events: list[MarketEvent] = []
        self.regime_drift: dict[str, float] = {}  # regime label -> annual drift (for display)
        self._next_event_id = 1
        start = _State(
            chain=model.initial_regime,
            effective="",
            symbols={
                s: _SymbolState(log_close=math.log(self._assets[s].start_price)) for s in self._simulated
            },
        )
        self._initial = start
        self.generate_until(self.warmup)

    # -- set-up --------------------------------------------------------------------
    def _asset(self, symbol: str) -> SyntheticAsset:
        if symbol in _ASSETS:
            return _ASSETS[symbol]
        if symbol == PAIR_B:
            return SyntheticAsset(PAIR_B, "Pair leg B", 1.0, 0.2, 0.02, 58.0)
        if symbol == PAIR_A:
            return SyntheticAsset(PAIR_A, "Pair leg A", 0.9, 0.0, 0.0, 62.0, 0.0, 0.0, 4_000_000)
        return _generic_asset(symbol)

    # -- queries -------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._regimes)

    def index(self, n: int | None = None) -> pd.DatetimeIndex:
        n = len(self) if n is None else n
        self._ensure_dates(n)
        return pd.DatetimeIndex(pd.to_datetime(self._dates[:n])).tz_localize("UTC")

    def time(self, t: int) -> pd.Timestamp:
        self._ensure_dates(t + 1)
        return pd.Timestamp(self._dates[t]).tz_localize("UTC")

    def frames(self, n: int | None = None) -> dict[str, pd.DataFrame]:
        """Bars 0..n-1 per requested symbol."""
        n = len(self) if n is None else n
        idx = self.index(n)
        out = {}
        for s in self.symbols:
            j = self._simulated.index(s)
            df = pd.DataFrame(
                {
                    "open": self._o[:n, j],
                    "high": self._h[:n, j],
                    "low": self._l[:n, j],
                    "close": self._c[:n, j],
                    "volume": self._v[:n, j],
                },
                index=idx,
            )
            df.index.name = "time"
            out[s] = df
        return out

    def bars_payload(self, first: int, last: int) -> dict[str, dict[str, list]]:
        """Bars ``first..last`` (inclusive) per symbol as compact rounded arrays."""
        last = min(last, len(self) - 1)
        n = max(last - first + 1, 0)
        self._ensure_dates(last + 1)
        times = pd.DatetimeIndex(pd.to_datetime(self._dates[first : first + n])).as_unit("s").asi8.tolist()
        out = {}
        for s in self.symbols:
            j = self._simulated.index(s)
            rows = slice(first, first + n)
            out[s] = {
                "t": times,
                "o": np.round(self._o[rows, j], 4).tolist(),
                "h": np.round(self._h[rows, j], 4).tolist(),
                "l": np.round(self._l[rows, j], 4).tolist(),
                "c": np.round(self._c[rows, j], 4).tolist(),
                "v": np.round(self._v[rows, j], 0).tolist(),
            }
        return out

    def drifts(self, upto: int) -> dict[str, float]:
        """Annual drift of each regime that occurs in bars ``0..upto``."""
        seen = set(self._regimes[: upto + 1])
        return {k: v for k, v in self.regime_drift.items() if k in seen}

    def bar(self, t: int) -> dict[str, tuple[float, float, float, float, float]]:
        cols = [self._simulated.index(s) for s in self.symbols]
        return {
            s: (self._o[t, j], self._h[t, j], self._l[t, j], self._c[t, j], self._v[t, j])
            for s, j in zip(self.symbols, cols, strict=True)
        }

    def arrays(self, t: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Open/high/low/close of bar ``t`` in ``self.symbols`` order."""
        cols = [self._simulated.index(s) for s in self.symbols]
        return self._o[t, cols], self._h[t, cols], self._l[t, cols], self._c[t, cols]

    def regime(self, t: int) -> str:
        return self._regimes[t]

    def intrabar_path(self, t: int, symbol: str) -> np.ndarray | None:
        """Log-price path (open ... close) of a recent bar, for animating a forming candle."""
        paths = self._paths.get(t)
        if paths is None:
            return None
        return paths[self._simulated.index(symbol)]

    # -- events --------------------------------------------------------------------
    def inject(
        self,
        kind: str,
        at: int,
        *,
        size: float | None = None,
        bars: int | None = None,
        symbol: str | None = None,
        regime: str | None = None,
    ) -> MarketEvent:
        """Schedule an event from bar ``at`` on. Bars already generated from ``at`` on are
        discarded (they will be regenerated with the event)."""
        spec = EVENT_KINDS.get(kind)
        if spec is None:
            raise ValueError(f"unknown event {kind!r}; choose from {', '.join(EVENT_KINDS)}")
        if at < self.warmup:
            raise ValueError("events can only be injected after the warm-up")
        size = float(spec["size"]) if size is None and "size" in spec else size
        bars = int(bars if bars is not None else spec["bars"])
        if not 1 <= bars <= 500:
            raise ValueError("bars must be between 1 and 500")
        if kind in ("crash", "rally", "gap") and size is not None and not MIN_EVENT_RETURN <= size <= 1.0:
            raise ValueError("size must be a return between -50% and +100% (the most one bar can move)")
        if kind == "crash" and size is not None and size >= 0:
            raise ValueError("a crash needs a negative size")
        if kind == "rally" and size is not None and size <= 0:
            raise ValueError("a rally needs a positive size")
        if kind == "vol_spike" and size is not None and not 1.0 < size <= 6.0:
            raise ValueError("the volatility multiplier must be between 1 and 6")
        if kind == "break_pair":
            if not {PAIR_A, PAIR_B} <= set(self.symbols):
                raise ValueError(f"break_pair needs both {PAIR_A} and {PAIR_B} in the simulation")
            if size is not None and not -0.5 <= size <= 0.5:
                raise ValueError("the pair shift must be between -50% and +50%")
        if kind == "gap":
            symbol = (symbol or "").upper()
            if symbol not in self.symbols:
                raise ValueError(f"pick one of the simulated symbols for the gap: {', '.join(self.symbols)}")
        if kind == "regime":
            regime = regime or spec["regime"]
            if regime not in ARCHETYPES:
                raise ValueError(f"regime must be one of {', '.join(ARCHETYPES)}")
        ev = MarketEvent(
            self._next_event_id,
            kind,
            int(at),
            bars,
            size,
            symbol if kind == "gap" else None,
            regime if kind == "regime" else None,
            describe_event(kind, size, bars, symbol, regime),
        )
        self._next_event_id += 1
        self.events.append(ev)
        self.truncate(at)
        return ev

    # -- generation ----------------------------------------------------------------
    def truncate(self, n: int) -> None:
        """Forget bars ``n`` and later (the state rewinds to the end of bar ``n-1``)."""
        n = max(int(n), 0)
        if n >= len(self):
            return
        for arr in ("_o", "_h", "_l", "_c", "_v"):
            setattr(self, arr, getattr(self, arr)[:n])
        del self._regimes[n:]
        del self._states[n:]
        for k in [k for k in self._paths if k >= n]:
            del self._paths[k]

    def generate_until(self, n: int) -> None:
        missing = int(n) - len(self)
        if missing <= 0:
            return
        first = len(self)
        prev = self._states[-1] if self._states else self._initial
        rows, states, names, paths = [], [], [], {}
        for i in range(missing):  # build everything first: a failure leaves the market unchanged
            row, prev, name, path = self._step(first + i, prev)
            rows.append(row)
            states.append(prev)
            names.append(name)
            paths[first + i] = path
        o, h, l, c, v = (np.array([r[k] for r in rows]) for k in range(5))
        self._o = np.vstack([self._o, o])
        self._h = np.vstack([self._h, h])
        self._l = np.vstack([self._l, l])
        self._c = np.vstack([self._c, c])
        self._v = np.vstack([self._v, v])
        self._states.extend(states)
        self._regimes.extend(names)
        self._paths.update(paths)
        for k in [k for k in self._paths if k < len(self) - 64]:
            del self._paths[k]  # keep intrabar paths of recent bars only

    def _ensure_dates(self, n: int) -> None:
        if len(self._dates) >= n:
            return
        span = int(n * 1.5) + 60
        self._dates = trading_days(SCENARIO_BASE_DATE, SCENARIO_BASE_DATE + timedelta(days=span))

    def _regime_for(self, t: int, st: _State, u: float) -> tuple[Regime, str, int]:
        """(regime used, its display name, next chain state) for bar ``t``."""
        m = self.model
        if self._script_regimes is not None:
            k = int(self._script_regimes[min(t, len(self._script_regimes) - 1)])
            chain = k
        elif self._cum is not None:
            chain = (
                st.chain
                if t == 0
                else int(min(np.searchsorted(self._cum[st.chain], u, side="right"), len(m.regimes) - 1))
            )
            k = chain
        else:
            chain = k = m.initial_regime
        regime, name = m.regimes[k], m.regimes[k].name
        for ev in self.events:
            if ev.active(t) and ev.kind in ("crash", "rally", "regime"):
                arche = {"crash": "crash", "rally": "bull"}.get(ev.kind, ev.regime or "range")
                regime = ARCHETYPES[arche]
                name = f"{arche} ({'forced' if ev.kind == 'regime' else 'injected'})"
        return regime, name, chain

    def _step(self, t: int, prev: _State) -> tuple[tuple[list[float], ...], _State, str, np.ndarray]:
        """Bar ``t`` from the state after bar ``t-1``: (OHLCV row, new state, regime, paths)."""
        m = self.model
        st = prev.copy()
        rng = np.random.default_rng([self.seed, stable_seed("arena-market"), t])
        u = rng.random()
        dof = m.t_dof
        scale = math.sqrt((dof - 2.0) / dof) if dof > 2 else 1.0
        z = float(rng.standard_t(dof)) * scale if dof > 2 else float(rng.standard_normal())
        n_jumps = int(rng.poisson(m.jump_intensity * DT))
        jump = n_jumps * m.jump_mean + math.sqrt(n_jumps) * m.jump_std * float(rng.standard_normal())

        regime, name, st.chain = self._regime_for(t, st, u)
        self.regime_drift[name] = regime.drift
        if name != st.effective:
            st.anchor = st.x
            st.effective = name
        vol_mult = 1.0
        open_jump = 0.0
        for ev in self.events:
            if not ev.active(t):
                continue
            if ev.kind == "vol_spike":
                vol_mult *= float(ev.size or 1.0)
            if t == ev.start and ev.kind in ("crash", "rally"):
                open_jump += math.log1p(float(ev.size or 0.0))
            if t == ev.start and ev.kind == "crash":
                st.g *= 2.5  # a crash raises volatility, which then decays at the GARCH pace
            if t == ev.start and ev.kind == "break_pair":
                st.spread_mean += float(ev.size or 0.0)
        vol_mult = min(vol_mult, MAX_VOL_MULT)
        open_jump = _clip(open_jump, MAX_BAR_MOVE)

        a, b = m.garch_alpha, m.garch_beta
        if t > 0:
            st.g = (1.0 - a - b) + (a * st.z_prev**2 + b) * st.g
        st.g = min(st.g, MAX_GARCH)
        st.z_prev = z
        s_m = math.sqrt(regime.vol**2 * DT * st.g) * vol_mult
        r_m = (regime.drift - 0.5 * regime.vol**2) * DT + s_m * z + jump
        if regime.mean_reversion > 0:
            r_m += regime.mean_reversion * DT * (st.anchor - st.x)
        r_m = _clip(r_m, MAX_BAR_MOVE)
        st.x += r_m + open_jump

        # Pair spread (Ornstein-Uhlenbeck, 15-day half-life) -- drawn every bar for stable streams.
        kappa = math.log(2) / (15 / TRADING_DAYS_PER_YEAR)
        spread_sd = 0.045 * math.sqrt(2 * kappa)
        spread_shock = float(rng.standard_normal()) * spread_sd * math.sqrt(DT) * vol_mult
        prev_spread = st.spread
        st.spread = st.spread - kappa * DT * (st.spread - st.spread_mean) + spread_shock

        opens, highs, lows, closes, vols, paths = [], [], [], [], [], []
        diffusive: dict[str, tuple[float, float, float]] = {}
        for sym in sorted(self._simulated, key=lambda s: s == PAIR_A):  # leg A after leg B
            asset = self._assets[sym]
            ss = st.symbols[sym]
            srng = np.random.default_rng([self.seed, stable_seed("arena-symbol", sym), t])
            zi = float(srng.standard_t(5)) * math.sqrt(3 / 5)
            ni = int(srng.poisson(asset.jump_intensity * DT))
            ji = math.sqrt(ni) * asset.jump_std * float(srng.standard_normal())  # zero-mean jumps
            ss.g = min(
                (1.0 - 0.05 - 0.93) + (0.05 * ss.z_prev**2 + 0.93) * ss.g if t > 0 else ss.g, MAX_GARCH
            )
            ss.z_prev = zi
            s_i = math.sqrt(asset.idio_vol**2 * DT * ss.g) * vol_mult
            gap = sum(
                math.log1p(float(ev.size or 0.0))
                for ev in self.events
                if t == ev.start and ev.kind == "gap" and ev.symbol == sym
            )
            if sym == PAIR_A and PAIR_B in diffusive:
                rb, sb, jb = diffusive[PAIR_B]
                hedge = 0.9
                r = hedge * rb + (st.spread - prev_spread) + 0.01 * DT
                sigma = math.sqrt((hedge * sb) ** 2 + (spread_sd * math.sqrt(DT)) ** 2)
                j_open = hedge * jb + gap
            else:
                r = asset.beta * r_m + (asset.alpha - 0.5 * asset.idio_vol**2) * DT + s_i * zi + ji
                sigma = math.sqrt((asset.beta * s_m) ** 2 + s_i**2)
                j_open = asset.beta * open_jump + gap
            r, j_open = _clip(r, MAX_BAR_MOVE), _clip(j_open, MAX_BAR_MOVE)
            diffusive[sym] = (r, sigma, j_open)
            bar, path, ss.log_close = _ohlcv(ss.log_close, r, j_open, sigma, srng, asset)
            k = self._simulated.index(sym)
            opens.append((k, bar[0]))
            highs.append((k, bar[1]))
            lows.append((k, bar[2]))
            closes.append((k, bar[3]))
            vols.append((k, bar[4]))
            paths.append((k, path))

        def ordered(items):
            return [v for _, v in sorted(items, key=lambda kv: kv[0])]

        row = (ordered(opens), ordered(highs), ordered(lows), ordered(closes), ordered(vols))
        return row, st, name, np.array(ordered(paths))


def _clip(x: float, bound: float) -> float:
    return max(-bound, min(bound, x))


def _ohlcv(
    prev_log_close: float,
    r: float,
    j_open: float,
    sigma: float,
    rng: np.random.Generator,
    asset: SyntheticAsset,
) -> tuple[tuple[float, float, float, float, float], np.ndarray, float]:
    """One OHLCV bar from its log return: an overnight gap (a share of the return, plus any
    event jump at the open), then a Brownian bridge from the open to the close. Returns the
    bar, its intrabar log-price path and the new log close."""
    f = GAP_FRACTION
    lo_bound, hi_bound = LOG_PRICE_BOUNDS
    gap = f * r + math.sqrt(f * (1 - f)) * sigma * float(rng.standard_normal()) + j_open
    lo = min(max(prev_log_close + gap, lo_bound), hi_bound)
    lc = min(max(prev_log_close + r + j_open, lo_bound), hi_bound)
    k = BRIDGE_STEPS
    incr = rng.standard_normal(k) * sigma * math.sqrt((1 - f) / k)
    walk = np.concatenate([[0.0], np.cumsum(incr)])
    frac = np.linspace(0.0, 1.0, k + 1)
    path = np.clip(lo + walk - frac * walk[-1] + frac * (lc - lo), lo_bound, hi_bound)
    typical = math.sqrt(asset.beta**2 * 0.16**2 + asset.idio_vol**2) * math.sqrt(DT)
    surprise = min(abs(r + j_open) / sigma, 6.0) if sigma > 0 else 0.0
    log_v = (
        math.log(asset.base_volume)
        + 0.6 * math.log(max(sigma, 1e-12) / max(typical, 1e-12))
        + 0.3 * surprise
        + 0.25 * float(rng.standard_normal())
    )
    bar = (
        math.exp(lo),
        math.exp(path.max()),
        math.exp(path.min()),
        math.exp(lc),
        float(round(math.exp(min(log_v, 40.0)))),
    )
    return bar, path, lc
