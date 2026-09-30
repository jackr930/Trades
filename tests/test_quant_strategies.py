"""The modern quant strategies, checked against data where the right answer is known."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tests.conftest import make_bars
from trades.advisor.recommender import Recommender
from trades.backtest.engine import BacktestConfig
from trades.backtest.runner import StrategySpec, backtest_strategy, overlays_payload
from trades.strategies import create_strategy
from trades.strategies.quant import hmm_filter, hmm_fit, kalman_hedge, ou_s_scores, trend_response


def universe(n=6, T=1400, reverting=True, seed=0, half_life=5.0):
    """One market factor plus stock-specific moves that mean-revert (or random-walk)."""
    rng = np.random.default_rng(seed)
    market = np.cumsum(rng.normal(0.0003, 0.01, T))
    k = 1 - 0.5 ** (1 / half_life)
    out = {}
    for i in range(n):
        beta = 0.7 + 0.1 * i
        shocks = rng.normal(0, 0.012, T)
        idio = np.zeros(T)
        for t in range(1, T):
            idio[t] = idio[t - 1] * (1 - k if reverting else 1) + shocks[t]
        out[f"S{i}"] = make_bars(50 * np.exp(beta * market + idio), spread=0.002)
    return out, market


# ----------------------------------------------------------------------------- CTA trend


def test_trend_response_shape():
    y = np.linspace(-6, 6, 1201)
    r = trend_response(y)
    assert r.max() == pytest.approx(0.964, abs=0.002) and y[r.argmax()] == pytest.approx(
        math.sqrt(2), abs=0.01
    )
    assert np.allclose(r, -trend_response(-y))  # symmetric for up and down trends
    assert abs(trend_response(np.array([6.0]))[0]) < 0.05  # extreme moves are faded


def test_cta_trend_follows_trends():
    rng = np.random.default_rng(1)
    # A year of noise, then a year-long uptrend and a year-long downtrend (Sharpe ~1.5 each).
    drift = np.r_[np.zeros(320), np.full(260, 0.0015), np.full(260, -0.0015)]
    df = make_bars(100 * np.exp(np.cumsum(drift + rng.normal(0, 0.01, len(drift)))))
    sig = create_strategy("cta_trend", {"rebalance": 1}).run({"X": df}).signals["X"].to_numpy()
    assert sig[400:580].mean() > 0.2  # long through most of the uptrend
    assert sig[660:840].mean() < -0.2  # and short through the downtrend
    lo = create_strategy("cta_trend", {"long_only": True, "rebalance": 1}).run({"X": df}).signals["X"]
    assert lo.min() >= 0


# ----------------------------------------------------------------------------- statistical arbitrage


def test_s_scores_match_a_direct_calculation():
    rng = np.random.default_rng(3)
    f = rng.normal(0, 0.01, 300)
    r = 1.2 * f + rng.normal(0, 0.01, 300)
    s, beta, tau = ou_s_scores(r, f, 60)
    for t in (59, 150, 299):
        rw, fw = r[t - 59 : t + 1], f[t - 59 : t + 1]
        b_ref, a_ref = np.polyfit(fw, rw, 1)
        X = np.cumsum(rw - a_ref - b_ref * fw)
        b, a = np.polyfit(X[:-1], X[1:], 1)
        zeta = X[1:] - a - b * X[:-1]
        m = a / (1 - b)
        s_ref = (X[-1] - m) / math.sqrt(zeta.var() / (1 - b * b))
        assert beta[t] == pytest.approx(b_ref, rel=1e-9)
        assert s[t] == pytest.approx(s_ref, rel=1e-6)
        assert tau[t] == pytest.approx(-1 / math.log(b), rel=1e-6)
    assert np.isnan(s[:59]).all()


def test_stat_arb_earns_when_residuals_revert_and_is_market_neutral():
    cfg = BacktestConfig(slippage_bps=1, borrow_bps_annual=0)
    strat, sizing = StrategySpec("stat_arb").build()
    data, market = universe(reverting=True)
    bt = backtest_strategy(strat, data, sizing, cfg)
    assert bt.metrics["sharpe"] > 1.0
    # Hedged against the other stocks: daily returns barely move with the market.
    rets = bt.result.equity.pct_change().iloc[bt.start + 1 :].to_numpy()
    mret = np.diff(market)[bt.start :]
    assert abs(np.polyfit(mret, rets, 1)[0]) < 0.15
    walk, _ = universe(reverting=False)
    bt_walk = backtest_strategy(strat, walk, sizing, cfg)
    assert bt_walk.metrics["sharpe"] < bt.metrics["sharpe"] - 1.0  # no reversion, no edge


def test_stat_arb_labels_hedge_legs_as_hedges():
    data, _ = universe(reverting=True)
    strat = create_strategy("stat_arb")
    out = strat.run(data)
    hedges = 0
    for s in data:
        states = strat.position_states(out, s)
        traded = out.diagnostics[s]["state"].to_numpy() != 0
        held = np.abs(out.signals[s].to_numpy()) > 1e-12
        assert set(states[traded]) <= {"long", "short"}
        assert (states[held & ~traded] == "hedge").all()
        hedges += int((held & ~traded).sum())
        if (held & ~traded).any():
            t = int(np.flatnonzero(held & ~traded)[-1])
            ex = strat.explain(out, s, t)
            assert ex.state == "hedge" and "hedge" in ex.headline and "offsets" in ex.exit_rule
            assert Recommender._vote(strat, ex, None)["vote"] == 0  # a hedge is no view on the stock
    assert hedges > 0


# ----------------------------------------------------------------------------- residual momentum


def test_residual_momentum_prefers_stock_specific_winners():
    rng = np.random.default_rng(5)
    T = 400
    market = np.cumsum(np.r_[rng.normal(0.003, 0.008, 200), rng.normal(-0.004, 0.008, 200)])
    data = {}
    for i, (beta, drift) in enumerate([(1.8, 0.0), (0.5, 0.0012), (1.0, 0.0), (1.0, -0.0008), (0.8, 0.0)]):
        idio = np.cumsum(rng.normal(drift, 0.006, T))
        data[f"S{i}"] = make_bars(50 * np.exp(beta * market + idio))
    out = create_strategy("residual_momentum", {"lookback": 252, "skip": 21}).run(data)
    last = {s: out.diagnostics[s]["metric"].iloc[-1] for s in data}
    assert max(last, key=last.get) == "S1"  # steady stock-specific winner, not the high-beta S0
    assert min(last, key=last.get) == "S3"


# ----------------------------------------------------------------------------- Kalman pairs


def test_kalman_filter_learns_the_hedge_ratio_and_absorbs_a_break():
    rng = np.random.default_rng(7)
    T = 1500
    x = np.log(40) + np.cumsum(rng.normal(0.0002, 0.012, T))
    spread = np.zeros(T)
    for t in range(1, T):
        spread[t] = 0.93 * spread[t - 1] + rng.normal(0, 0.006)
    y = 0.3 + 1.5 * x + spread + 0.10 * (np.arange(T) >= 900)  # the relationship breaks at bar 900
    z, beta, alpha, err = kalman_hedge(y, x, 120, 1e-5)
    # The hedge ratio is only identified from movement in x, so it wanders a little (alpha
    # compensates), but it sits near the truth.
    assert beta[500:900].mean() == pytest.approx(1.5, abs=0.15)
    assert abs(err[700:900].mean()) < 0.005
    assert err[900] > 0.07  # the break shows up as a large forecast error...
    assert abs(err[-200:].mean()) < 0.005  # ...until the filter has absorbed it
    assert np.nanstd(z) == pytest.approx(1.0, abs=0.35)  # z is in forecast-sd units
    assert np.isnan(z[:120]).all()


def test_kalman_pairs_trades_the_spread_hedged():
    rng = np.random.default_rng(11)
    T = 1200
    xb = np.log(60) + np.cumsum(rng.normal(0.0003, 0.012, T))
    s = np.zeros(T)
    for t in range(1, T):
        s[t] = 0.9 * s[t - 1] + rng.normal(0, 0.008)
    data = {"A": make_bars(np.exp(0.2 + 0.9 * xb + s)), "B": make_bars(np.exp(xb))}
    strat, sizing = StrategySpec("kalman_pairs").build()
    bt = backtest_strategy(strat, data, sizing, BacktestConfig(slippage_bps=1, borrow_bps_annual=0))
    assert bt.metrics["sharpe"] > 1.0 and bt.metrics["n_trades"] >= 10
    diag = bt.output.diagnostics["A"]
    held = diag[diag["state"] != 0]
    assert held["locked_beta"].between(0.75, 1.05).mean() > 0.9  # trades are hedged near the true ratio


# ----------------------------------------------------------------------------- HMM regimes


def _regime_returns(seed=2):
    rng = np.random.default_rng(seed)
    calm = lambda n: rng.normal(0.05, 0.6, n)  # noqa: E731 - percent per bar
    turb = lambda n: rng.normal(-0.25, 2.8, n)  # noqa: E731
    r = np.r_[calm(500), turb(150), calm(300), turb(120), calm(200)]
    labels = np.r_[np.zeros(500), np.ones(150), np.zeros(300), np.ones(120), np.zeros(200)]
    return r, labels


def test_hmm_fit_recovers_two_regimes():
    r, _ = _regime_returns()
    sd = float(np.std(r))
    fit = hmm_fit(
        r,
        {"pi": (0.5, 0.5), "A": ((0.97, 0.03), (0.06, 0.94)), "mu": (0.0, 0.0), "sd": (0.7 * sd, 1.5 * sd)},
        60,
    )
    lo, hi = sorted(fit["sd"])
    assert lo == pytest.approx(0.6, rel=0.15) and hi == pytest.approx(2.8, rel=0.2)
    (a00, _), (_, a11) = fit["A"]
    assert a00 > 0.95 and a11 > 0.9  # regimes persist


def test_hmm_filter_tracks_the_hidden_regime_causally():
    r, labels = _regime_returns()
    r = np.r_[np.nan, r]
    labels = np.r_[0, labels]
    est, refits = hmm_filter(r, 250, 1000, 21)
    p = est["p"]
    turb = labels == 1
    later = np.arange(len(r)) >= 300
    # After a few bars of evidence the filter sees the turbulence (it can't see it in advance).
    assert np.nanmean(p[turb & later][10:]) < 0.25
    assert np.nanmean(p[~turb & later]) > 0.75
    assert refits.sum() == pytest.approx((len(r) - 251) / 21, abs=1)


def test_hmm_strategy_sits_out_turbulence():
    r, labels = _regime_returns(4)
    closes = 100 * np.exp(np.cumsum(np.r_[0, r[1:]] / 100))
    out = create_strategy("hmm_regime").run({"X": make_bars(closes)})
    sig = out.signals["X"].to_numpy()
    turb = labels == 1
    calm_seen = (labels == 0) & (np.arange(len(sig)) >= 250)
    assert sig[turb][20:].mean() < 0.3  # out for most of each turbulent spell
    assert sig[calm_seen].mean() > 0.7  # invested in calm markets, even before seeing turbulence


# ----------------------------------------------------------------------------- machine-learning ranker


def test_ml_ranker_learns_a_planted_effect_without_peeking():
    data, _ = universe(n=8, T=1300, reverting=True, seed=21, half_life=8)
    strat = create_strategy("ml_ranker", {"horizon": 10, "rebalance": 10})
    out = strat.run(data)
    d = out.diagnostics["S0"]
    coefs = {c[2:]: d[c].iloc[-1] for c in d.columns if c.startswith("b_")}
    # Stock-specific moves mean-revert, so recent relative strength predicts lagging (the effect is
    # shared among correlated features, so check their combined loading).
    assert coefs["ret_21"] + coefs["ret_63"] + coefs["rsi_14"] + coefs["to_sma200"] < 0
    ic = d["ic"].to_numpy()
    assert np.nanmean(ic) > 0.15 and ic[-1] > 0.1  # live, out-of-sample rank correlation
    first = int(np.flatnonzero(np.isfinite(d["metric"].to_numpy()))[0])
    assert first >= 252 + 252 + 10 - 1  # a year of features, a year of training, the purge gap
    ex = strat.explain(out, "S0")
    assert "Model expects" in ex.headline and any("information coefficient" in r.label for r in ex.rules)


def test_new_strategies_have_family_needs_and_a_classic_counterpart():
    for sid in ("cta_trend", "stat_arb", "residual_momentum", "kalman_pairs", "hmm_regime", "ml_ranker"):
        meta = create_strategy(sid).meta()
        assert meta["family"] == "modern" and meta["needs"]
        assert create_strategy(meta["counterpart"]).meta()["family"] == "classic"
    assert create_strategy("tsmom").meta()["family"] == "classic"
    assert create_strategy("ml_ranker").meta()["evidence"] == "experimental"


def test_chart_guide_lines_follow_the_parameters():
    cols = ("z", "s_score", "p_calm", "rsi", "trend_sma", "exit_sma")
    diag = pd.DataFrame({c: [0.0, 1.0] for c in cols})

    def levels(sid, params):
        return {o["column"]: o["levels"] for o in overlays_payload(create_strategy(sid, params), diag)}

    assert levels("kalman_pairs", {"entry_z": 2.0, "exit_z": 0.5})["z"] == [-2.0, -0.5, 0.5, 2.0]
    assert levels("pairs_trading", {"exit_z": 0.0})["z"] == [-2.0, 0.0, 2.0]
    assert levels("stat_arb", {"entry": 1.5})["s_score"] == [-1.5, -0.5, 0.75, 1.5]
    assert levels("hmm_regime", {"enter": 0.7, "exit": 0.3})["p_calm"] == [0.3, 0.7]
    rsi = levels("rsi2_reversion", {"entry": 5})
    assert rsi["rsi"] == [5.0, 90.0] and rsi["trend_sma"] == []
