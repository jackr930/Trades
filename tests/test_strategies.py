"""Strategy library: metadata, causality (no look-ahead), rule behaviour and sizing."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tests.conftest import UNIVERSE, make_bars
from trades.strategies import REGISTRY, Kind, UnknownStrategyError, create_strategy, get_strategy_class
from trades.strategies.sizing import SizingConfig, apply_sizing


def _inputs(cls, daily):
    if cls.kind is Kind.PAIR:
        return {s: daily[s] for s in ("SIMPRA", "SIMPRB")}
    if cls.kind is Kind.CROSS_SECTIONAL:
        return {s: daily[s] for s in UNIVERSE}
    return {"SIMTEC": daily["SIMTEC"]}


def _params(cls):
    return {"safe_symbol": "SIMBND"} if cls.id == "dual_momentum" else {}


def test_every_strategy_is_documented():
    for cls in REGISTRY.values():
        meta = cls.meta()
        assert meta["summary"] and meta["rules"] and meta["rationale"] and meta["failure_modes"]
        assert meta["references"], cls.id
        for ref in cls.references:
            assert ref.authors and 1900 < ref.year < 2030 and ref.title
        for p in cls.params_spec:
            assert p.coerce(p.default) == p.default or p.kind in ("symbol",)


@pytest.mark.parametrize("sid", list(REGISTRY))
def test_no_look_ahead(sid, daily):
    """Signals, diagnostics and sized weights at bar t must not change when later bars are
    added. This is the property that makes a backtest honest."""
    cls = REGISTRY[sid]
    strat = cls(**_params(cls))
    data = {s: df.iloc[-900:] for s, df in _inputs(cls, daily).items()}
    full = strat.run(data)
    sizing = SizingConfig.from_dict(None, cls.default_sizing)
    w_full = apply_sizing(full.signals, data, sizing, cls.kind)
    for cut in (420, 611, 837):
        part_data = {s: df.iloc[:cut] for s, df in data.items()}
        part = strat.run(part_data)
        np.testing.assert_allclose(
            part.signals.to_numpy(), full.signals.iloc[:cut].to_numpy(), equal_nan=True
        )
        for sym in data:
            a = full.diagnostics[sym].iloc[:cut].select_dtypes("number").astype(float)
            b = part.diagnostics[sym].select_dtypes("number").astype(float)
            np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), equal_nan=True, err_msg=f"{sid}/{sym}")
        w_part = apply_sizing(part.signals, part_data, sizing, cls.kind)
        np.testing.assert_allclose(w_part.to_numpy(), w_full.iloc[:cut].to_numpy(), atol=1e-12)


@pytest.mark.parametrize("sid", list(REGISTRY))
def test_explanations_available(sid, daily):
    cls = REGISTRY[sid]
    strat = cls(**_params(cls))
    data = _inputs(cls, daily)
    out = strat.run(data)
    for sym in data:
        ex = strat.explain(out, sym)
        assert ex.state in ("long", "short", "flat")
        assert ex.headline
        d = ex.to_dict()
        assert set(d) >= {"state", "headline", "rules", "since", "fresh"}
    if strat.warmup() > 1:
        early = strat.explain(out, next(iter(data)), at=0)
        assert early.state == "warming_up"


def test_param_validation():
    with pytest.raises(ValueError, match="above the maximum"):
        create_strategy("ma_crossover", {"fast": 500})
    with pytest.raises(ValueError, match="shorter"):
        create_strategy("ma_crossover", {"fast": 100, "slow": 50})
    with pytest.raises(ValueError, match="unknown parameter"):
        create_strategy("tsmom", {"nope": 1})
    with pytest.raises(ValueError, match="whole number"):
        create_strategy("tsmom", {"lookback": 10.5})
    with pytest.raises(UnknownStrategyError):
        get_strategy_class("does_not_exist")
    assert create_strategy("tsmom", {"long_only": "true"}).params["long_only"] is True


def test_tsmom_follows_the_trend():
    up = make_bars(100 * np.exp(np.linspace(0, 0.5, 400)))
    down = make_bars(100 * np.exp(np.linspace(0, -0.5, 400)))
    s = create_strategy("tsmom", {"lookback": 60, "hold": 1})
    assert s.run({"U": up}).signals["U"].iloc[-1] == 1.0
    assert s.run({"D": down}).signals["D"].iloc[-1] == -1.0
    lo = create_strategy("tsmom", {"lookback": 60, "hold": 1, "long_only": True})
    assert lo.run({"D": down}).signals["D"].iloc[-1] == 0.0


def test_faber_checks_monthly():
    closes = np.concatenate([np.linspace(100, 150, 300), np.linspace(150, 90, 60)])
    df = make_bars(closes)
    s = create_strategy("faber_trend", {"sma_length": 50, "monthly": True}).run({"X": df})
    sig = s.signals["X"]
    first_valid = s.diagnostics["X"]["sma"].first_valid_index()
    changes = sig.index[(sig.diff().fillna(0) != 0) & (sig.index > first_valid)]
    assert len(changes) >= 1
    # after the first evaluation the signal only changes on the first bar of a month
    for ts in changes:
        prev = sig.index[sig.index.get_loc(ts) - 1]
        assert (ts.year, ts.month) != (prev.year, prev.month)
    assert sig.iloc[-1] == 0.0  # below the average by the end


def test_donchian_enters_on_breakout_and_honours_stop():
    closes = np.concatenate(
        [np.full(40, 100.0), [106.0, 107.0], np.full(10, 107.0), [90.0], np.full(5, 90.0)]
    )
    df = make_bars(closes, spread=0.002)
    out = create_strategy(
        "donchian_breakout", {"entry": 20, "exit": 10, "atr_length": 20, "stop_atr": 2.0}
    ).run({"X": df})
    sig = out.signals["X"].to_numpy()
    assert sig[40] == 1.0  # breakout bar
    assert sig[52] == 0.0  # stop / exit channel hit on the crash bar
    diag = out.diagnostics["X"]
    assert diag["stop"].iloc[41] < 106.0


def test_rsi2_buys_pullbacks_only_in_uptrends():
    rng = np.random.default_rng(4)
    trend = 100 * np.exp(np.cumsum(0.002 + rng.normal(0, 0.004, 260)))
    trend[-3:] = trend[-4] * np.array([0.98, 0.96, 0.95])  # sharp pullback
    s = create_strategy("rsi2_reversion").run({"X": make_bars(trend)})
    assert s.signals["X"].iloc[-1] == 1.0
    falling = trend[::-1].copy()
    s2 = create_strategy("rsi2_reversion").run({"X": make_bars(falling)})
    assert (s2.signals["X"] >= 0).all()
    assert s2.signals["X"].iloc[-1] == 0.0


def test_pairs_trades_the_spread(daily):
    data = {s: daily[s] for s in ("SIMPRA", "SIMPRB")}
    s = create_strategy("pairs_trading")
    out = s.run(data)
    w = out.signals
    active = w.abs().sum(axis=1) > 0
    assert active.any()
    # legs always have opposite signs (long one, short the other)
    assert ((w.loc[active, "SIMPRA"] * w.loc[active, "SIMPRB"]) < 0).all()
    z = out.diagnostics["SIMPRA"]["z"]
    entries = w["SIMPRA"].diff().fillna(0).ne(0) & (w["SIMPRA"] > 0)
    assert (z[entries] <= -s.params["entry_z"]).all()


def test_cross_sectional_momentum_picks_winners():
    n = 320
    t = np.arange(n)
    data = {f"S{i}": make_bars(100 * np.exp(0.0006 * (i - 3) * t)) for i in range(7)}
    out = create_strategy("xs_momentum", {"lookback": 126, "skip": 0, "rebalance": 21, "top_frac": 0.3}).run(
        data
    )
    last = out.signals.iloc[-1]
    assert set(last[last > 0].index) == {"S5", "S6"}
    assert last.sum() == pytest.approx(1.0)


def test_dual_momentum_goes_to_safety_when_everything_falls():
    n = 300
    t = np.arange(n)
    data = {
        "A": make_bars(100 * np.exp(-0.001 * t)),
        "B": make_bars(100 * np.exp(-0.0005 * t)),
        "SAFE": make_bars(np.full(n, 100.0)),
    }
    out = create_strategy("dual_momentum", {"lookback": 126, "safe_symbol": "SAFE"}).run(data)
    assert out.signals["SAFE"].iloc[-1] == 1.0
    assert out.signals[["A", "B"]].iloc[-1].sum() == 0.0


# ------------------------------------------------------------------------------ sizing


def test_vol_target_hits_target_on_average(daily):
    df = daily["SIMTEC"]
    sig = pd.DataFrame({"SIMTEC": np.ones(len(df))}, index=df.index)
    cfg = SizingConfig(method="vol_target", target_vol=0.10, max_leverage=5, rebalance_every=1)
    w = apply_sizing(sig, {"SIMTEC": df}, cfg, Kind.SINGLE)
    rets = df["close"].pct_change() * w["SIMTEC"].shift(1)
    realized = rets.iloc[100:].std() * np.sqrt(252)
    assert 0.07 < realized < 0.14


def test_atr_risk_sizing_formula(daily):
    df = daily["SIMIDX"]
    sig = pd.DataFrame({"SIMIDX": np.ones(len(df))}, index=df.index)
    cfg = SizingConfig(method="atr_risk", risk_per_trade=0.01, stop_atr=2.0, atr_length=20, max_leverage=10)
    w = apply_sizing(sig, {"SIMIDX": df}, cfg, Kind.SINGLE)
    from trades.core.indicators import atr

    a = atr(df["high"], df["low"], df["close"], 20)
    first = int(np.argmax(np.isfinite(a.to_numpy())))
    expected = 0.01 * df["close"].iloc[first] / (2 * a.iloc[first])
    assert w["SIMIDX"].iloc[first] == pytest.approx(expected)
    assert w["SIMIDX"].iloc[-1] == pytest.approx(expected)  # locked while the signal is unchanged


def test_sizing_locks_and_caps():
    idx = pd.bdate_range("2024-01-01", periods=6, tz="UTC")
    sig = pd.DataFrame({"A": [0, 1, 1, 1, 0, -1.0], "B": [1, 1, 1, 1, 1, 1.0]}, index=idx)
    data = {s: make_bars(np.linspace(100, 110, 6)).set_axis(idx) for s in "AB"}
    w = apply_sizing(sig, data, SizingConfig(method="fixed", allocation=0.8, max_leverage=1.0), Kind.SINGLE)
    assert (w.abs().sum(axis=1) <= 1.0 + 1e-12).all()
    assert w["A"].iloc[4] == 0.0 and w["A"].iloc[5] < 0
    with pytest.raises(ValueError):
        SizingConfig(method="martingale")
