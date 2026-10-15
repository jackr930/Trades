"""Characterization ("golden") tests: today's outputs, pinned.

These tests do not say the numbers are *right*; they say they have not *changed*. They pin the
results of every strategy's backtest (under several configurations), the Live Desk's
recommendations, the optimiser and walk-forward analysis on the deterministic synthetic market,
so a refactor that is meant to move code without changing behaviour can prove it did.

When a change *should* alter results, regenerate the snapshot on purpose and review the diff:

    TRADES_REGEN_GOLDEN=1 pytest tests/test_golden.py
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from tests.helpers import UNIVERSE
from trades.engine.backtest import backtest_strategy, cost_sensitivity
from trades.execution.sim_broker import BacktestConfig
from trades.recommend import AdvisorSettings, Recommender
from trades.reporting.payloads import backtest_payload, sanitize
from trades.reporting.tax import TaxProfile
from trades.strategies import REGISTRY, Kind, StrategyConfig, catalog
from trades.validation.optimize import grid_search, walk_forward

GOLDEN = Path(__file__).parent / "golden" / "outputs.json"
REGEN = os.environ.get("TRADES_REGEN_GOLDEN") == "1"
REL, ABS = 1e-7, 1e-9

# Configurations that exercise the engine's switches: costs, shorting, fractional shares, timing.
CONFIGS = {
    "default": {},
    "costly_short_fractional": {
        "allow_short": True,
        "fractional": True,
        "slippage_bps": 12.0,
        "commission_bps": 1.5,
        "commission_per_share": 0.005,
        "min_commission": 1.0,
        "initial_cash": 25_000.0,
    },
    "next_close": {"execution": "next_close", "slippage_bps": 2.0},
}
SIZINGS = {
    "default": None,
    "vol_target": {"method": "vol_target", "target_vol": 0.12, "max_leverage": 1.5},
    "atr_risk": {"method": "atr_risk", "risk_per_trade": 0.008, "stop_atr": 2.5},
}


def _inputs(cls, daily):
    if cls.id == "consensus":
        return {s: daily[s] for s in (*UNIVERSE, "SIMPRA", "SIMPRB")}
    if cls.kind is Kind.PAIR:
        return {s: daily[s] for s in ("SIMPRA", "SIMPRB")}
    if cls.kind is Kind.CROSS_SECTIONAL:
        return {s: daily[s] for s in UNIVERSE}
    return {"SIMTEC": daily["SIMTEC"], "SIMIDX": daily["SIMIDX"]}


def _params(cls):
    if cls.id == "dual_momentum":
        return {"safe_symbol": "SIMBND"}
    if cls.id == "consensus":
        return {"pairs": [["SIMPRA", "SIMPRB"]]}
    return {}


def _round(x, digits=10):
    if x is None:
        return None
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if not math.isfinite(float(x)) else round(float(x), digits)
    if isinstance(x, dict):
        return {str(k): _round(v, digits) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_round(v, digits) for v in x]
    return x


def _summarise_backtest(bt) -> dict:
    eq = bt.result.equity
    fills = bt.result.fills
    sample = [0, 1, 2, len(fills) // 2, len(fills) - 2, len(fills) - 1] if fills else []
    return _round(
        {
            "start": bt.start,
            "metrics": bt.metrics,
            "benchmark_metrics": bt.benchmark_metrics,
            "final_equity": float(eq.iloc[-1]),
            "equity_sum": float(eq.sum()),
            "equity_points": [float(eq.iloc[i]) for i in np.linspace(0, len(eq) - 1, 9).astype(int)],
            "weights_abs_sum": float(np.abs(bt.weights.to_numpy()).sum()),
            "n_fills": len(fills),
            "fills": [
                [int(fills[i].index), fills[i].symbol, float(fills[i].qty), float(fills[i].price)]
                for i in sorted({i for i in sample if 0 <= i < len(fills)})
            ],
            "n_trades": len(bt.result.trades),
            "pending": bt.result.pending,
            "warnings": bt.warnings,
        }
    )


INTRADAY_CASES = (
    ("orb", {}),
    ("orb", {"entry": "breakout", "stop_mode": "atr", "target_r": 0.0}),
    ("orb_sip", {}),
    ("intraday_momentum", {}),
    ("intraday_momentum", {"use_r12": True, "allow_short": False}),
)
INTRADAY_CONFIG = {  # the repository's cost model (config/base.yaml) on a $25,000 account
    "initial_cash": 25_000.0,
    "periods_per_year": 252 * 78,
    "half_spread_bps": 2.0,
    "slippage_bps": 3.0,
    "stop_slippage_bps": 10.0,
    "sec_fee_per_million": 20.6,
    "taf_per_share": 0.000195,
    "taf_max_per_trade": 9.79,
}


def compute_outputs(daily, intraday) -> dict:
    out: dict = {"backtests": {}, "sizings": {}, "intraday": {}}
    for sid, cls in REGISTRY.items():
        if cls.intraday:
            continue  # pinned below, on intraday bars
        data = _inputs(cls, daily)
        for cname, cfg in CONFIGS.items():
            spec = StrategyConfig(sid, _params(cls))
            strat, sizing = spec.build()
            bt = backtest_strategy(strat, data, sizing, BacktestConfig(**cfg))
            out["backtests"][f"{sid}/{cname}"] = _summarise_backtest(bt)
        if cls.kind is Kind.SINGLE and not cls.sizes_itself:
            for zname, z in SIZINGS.items():
                if z is None:
                    continue
                spec = StrategyConfig(sid, _params(cls), z)
                strat, sizing = spec.build()
                bt = backtest_strategy(strat, data, sizing, BacktestConfig())
                out["sizings"][f"{sid}/{zname}"] = _summarise_backtest(bt)

    for sid, params in INTRADAY_CASES:
        cls = REGISTRY[sid]
        data = dict(intraday) if cls.kind is Kind.CROSS_SECTIONAL else {"SIMTEC": intraday["SIMTEC"]}
        strat, sizing = StrategyConfig(sid, params).build()
        bt = backtest_strategy(strat, data, sizing, BacktestConfig(**INTRADAY_CONFIG))
        key = sid + "/" + (",".join(f"{k}={v}" for k, v in sorted(params.items())) or "default")
        out["intraday"][key] = _summarise_backtest(bt) | {"ambiguous_fills": bt.result.ambiguous_fills}

    # After-tax results and the cost-sensitivity rerun of one backtest.
    strat, sizing = StrategyConfig("tsmom", {}).build()
    tax = TaxProfile("taxable", 0.24, 0.15)
    bt = backtest_strategy(strat, {"SIMTEC": daily["SIMTEC"]}, sizing, BacktestConfig(), tax=tax)
    out["tax"] = _summarise_backtest(bt)
    out["cost_sensitivity"] = _round(cost_sensitivity(bt, tax))

    # Optimiser: grid search with the Deflated Sharpe Ratio, and walk-forward analysis.
    grid = grid_search(
        "ma_crossover", {}, {"fast": [20, 50], "slow": [100, 200]}, {"SIMIDX": daily["SIMIDX"]}
    )
    out["grid_search"] = _round({k: v for k, v in grid.items() if k != "start_time"} | {"start": grid["start_time"]})
    wf = walk_forward(
        "donchian_breakout",
        {},
        {"entry": [20, 55]},
        {"SIMTEC": daily["SIMTEC"]},
        train_bars=504,
        test_bars=252,
    )
    out["walk_forward"] = _round(
        {
            "windows": [{k: v for k, v in w.items()} for w in wf["windows"]],
            "oos_metrics": wf["oos_metrics"],
            "default_metrics": wf["default_metrics"],
            "efficiency": wf["efficiency"],
            "oos_final": wf["oos_equity"]["v"][-1],
            "interpretation": wf["interpretation"],
        }
    )

    # The Live Desk: recommendations for the demo watchlist on completed bars.
    frames = {s: daily[s] for s in (*UNIVERSE, "SIMPRA", "SIMPRB")}
    from trades.defaults import DEFAULT_ADVISORS

    settings = AdvisorSettings(
        strategies=[StrategyConfig.from_dict(a) for a in DEFAULT_ADVISORS],
        pairs=[("SIMPRA", "SIMPRB")],
    )
    res = Recommender().recommend(frames, settings, namespace="golden")
    recs = []
    for r in res["recommendations"]:
        recs.append(
            {
                "symbol": r["symbol"],
                "price": r["price"],
                "consensus": r["consensus"],
                "votes": [
                    {k: v.get(k) for k in ("strategy_id", "vote", "state", "headline", "evidence")} for v in r["votes"]
                ],
                "sizing": {k: r["sizing"].get(k) for k in ("side", "weight", "shares", "stop", "explanation")},
            }
        )
    out["recommendations"] = _round(recs)
    out["portfolio"] = _round(res.get("portfolio"))

    # The contracts the web UI reads: the strategy catalog and the shape of a backtest payload.
    out["catalog"] = _round(catalog())
    payload = sanitize(backtest_payload(bt))
    out["payload_shape"] = {
        k: sorted(v) if isinstance(v, dict) else type(v).__name__ for k, v in sorted(payload.items())
    }
    return out


def _compare(actual, expected, path="$"):
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        assert sorted(actual) == sorted(expected), f"{path}: keys differ"
        for k in expected:
            _compare(actual[k], expected[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{path}: length differs"
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            _compare(a, e, f"{path}[{i}]")
    elif isinstance(expected, float) and not isinstance(expected, bool):
        assert actual == pytest.approx(expected, rel=REL, abs=ABS), path
    else:
        assert actual == expected, path


@pytest.fixture(scope="module")
def outputs(daily, intraday):
    return json.loads(json.dumps(compute_outputs(daily, intraday)))


def test_outputs_match_the_snapshot(outputs):
    if REGEN or not GOLDEN.exists():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(outputs, indent=1, sort_keys=True) + "\n")
        pytest.skip(f"wrote {GOLDEN}; review the diff and commit it")
    expected = json.loads(GOLDEN.read_text())
    _compare(outputs, expected)
