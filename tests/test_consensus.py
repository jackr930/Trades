"""The Live Desk consensus: shared decision rules, and parity between the Live Desk and the strategy."""

from __future__ import annotations

import pytest

from tests.conftest import UNIVERSE
from trades.advisor import AdvisorSettings, Recommender
from trades.advisor.recommender import suggest_position
from trades.backtest.runner import StrategySpec, backtest_strategy
from trades.config import DEFAULT_ADVISORS
from trades.data.base import align_bars
from trades.strategies import REGISTRY, create_strategy
from trades.strategies.consensus import ConsensusRules, consensus_score, decide

RULES = ConsensusRules(risk_per_trade=0.01, stop_atr=2.0, max_position_pct=0.2, max_gross=1.0)


def test_hold_means_no_position():
    """A neutral consensus (|score| < 0.2) suggests nothing, however strong the risk budget."""
    votes = {"X": [("Trend", 1.0), ("Trend", 0.0), ("Trend", 0.0), ("Trend", -1.0), ("Trend", 1.0)]}  # +0.2
    assert decide(votes, {"X": 50.0}, {"X": 1.0}, RULES)["X"].action == "BUY"
    for score_votes in ([1.0, 0.0, 0.0, 0.0, 0.0, 0.0], [1.0, -1.0], [0.0, 0.0], []):  # +0.17, 0, 0, no votes
        d = decide({"X": [("Trend", v) for v in score_votes]}, {"X": 50.0}, {"X": 1.0}, RULES)["X"]
        assert d.action == "HOLD" and d.weight == 0.0 and d.side == "flat"
        pos = suggest_position(d, 50.0, AdvisorSettings(strategies=[]))
        assert pos["shares"] == 0 and pos["weight"] == 0.0 and "No position" in pos["explanation"]


def test_sell_without_shorting_means_no_position_and_short_with_it():
    votes = {"X": [("Trend", -1.0)]}
    assert decide(votes, {"X": 50.0}, {"X": 1.0}, RULES)["X"].weight == 0.0
    short = decide(votes, {"X": 50.0}, {"X": 1.0}, ConsensusRules(allow_short=True))["X"]
    assert short.action == "SHORT" and short.weight == pytest.approx(-0.2)


def test_portfolio_cap_scales_every_suggestion_proportionally():
    # Ten unanimous buys, each capped at 20% of equity: 200% in total before the portfolio cap.
    symbols = [f"S{i}" for i in range(10)]
    votes = {s: [("Trend", 1.0)] for s in symbols}
    prices, atrs = dict.fromkeys(symbols, 50.0), dict.fromkeys(symbols, 1.0)
    out = decide(votes, prices, atrs, RULES)
    assert sum(abs(d.weight) for d in out.values()) == pytest.approx(1.0)
    assert all(d.weight == pytest.approx(0.1) and d.scale == pytest.approx(0.5) for d in out.values())
    # Mixed sizes keep their proportions; a cap that is not reached changes nothing.
    votes["S0"] = [("Trend", 1.0), ("Trend", 0.0)]  # half conviction -> 10% before the cap
    out = decide(votes, prices, atrs, RULES)
    assert out["S0"].weight == pytest.approx(out["S1"].weight / 2)
    assert sum(abs(d.weight) for d in out.values()) == pytest.approx(1.0)
    loose = decide(votes, prices, atrs, ConsensusRules(max_gross=2.0))
    assert loose["S1"].weight == pytest.approx(0.2) and loose["S1"].scale == 1.0


def test_by_category_counts_each_category_once():
    votes = [("Trend following", 1.0)] * 4 + [("Mean reversion", -1.0), ("Momentum", 0.0)]
    assert consensus_score(votes, "equal") == pytest.approx(0.5)
    assert consensus_score(votes, "by_category") == pytest.approx(0.0)  # (1 - 1 + 0) / 3


def test_states_match_explanations(daily):
    """The consensus reads every bar's vote from ``states``; the Live Desk reads it from ``explain``."""
    from tests.test_strategies import _inputs, _params

    for sid, cls in REGISTRY.items():
        strat = cls(**_params(cls))
        data = {s: df.iloc[-600:] for s, df in _inputs(cls, daily).items()}
        out = strat.run(data)
        sym = next(iter(data))
        states = strat.states(out, sym)
        for t in (0, 150, 300, 599):
            assert states[t] == strat.explain(out, sym, t).state, (sid, t)


@pytest.mark.parametrize(("weighting", "allow_short"), [("equal", False), ("by_category", True)])
def test_strategy_reproduces_the_live_desk(daily, weighting, allow_short):
    """At every cut point, the Live Desk on data truncated at bar t says what the strategy says at bar t."""
    symbols = [*UNIVERSE[:5], "SIMPRA", "SIMPRB"]
    data = {s: daily[s].iloc[-700:] for s in symbols}
    # Big positions, so the portfolio cap binds on some bars.
    risk = {"risk_per_trade": 0.03, "stop_atr": 2.0, "max_position_pct": 0.5}
    adv = AdvisorSettings(
        strategies=[StrategySpec.from_dict(d) for d in DEFAULT_ADVISORS],
        pairs=[("SIMPRA", "SIMPRB")],
        weighting=weighting,
        allow_short=allow_short,
        max_gross=1.0,
        evidence_bars=60,  # the track records are not under test; keep them cheap
        **risk,
    )
    strat = create_strategy(
        "consensus",
        {"weighting": weighting, "allow_short": allow_short, "max_gross": 1.0, "pairs": [["SIMPRA", "SIMPRB"]]}
        | risk,
    )
    out = strat.run(data)
    capped = 0
    for cut in (150, 330, 518, 700):  # 150: the slower members are still warming up
        res = Recommender().recommend({s: df.iloc[:cut] for s, df in data.items()}, adv, namespace=f"p{cut}")
        for rec in res["recommendations"]:
            mine = strat.decision_at(out, rec["symbol"], cut - 1)
            assert rec["consensus"]["action"] == mine["action"], (cut, rec["symbol"])
            assert rec["consensus"]["score"] == pytest.approx(mine["score"], abs=1e-12)
            assert rec["sizing"]["weight"] == pytest.approx(mine["weight"], abs=1e-12), (cut, rec["symbol"])
        capped += res["portfolio"]["scale"] < 1
        assert res["portfolio"]["gross"] <= 1.0 + 1e-9
    assert capped, "the portfolio cap should bind at some cut point"


def test_live_desk_matches_the_strategy_when_histories_differ(daily):
    """A symbol listed later and a missing bar: the Live Desk uses the dates all symbols share,
    exactly as the strategy (and so the backtests and the paper trader) does."""
    symbols = UNIVERSE[:5]
    data = {s: daily[s].iloc[-600:] for s in symbols}
    data[symbols[1]] = data[symbols[1]].iloc[40:]  # listed 40 bars later
    data[symbols[2]] = data[symbols[2]].drop(data[symbols[2]].index[300])  # a provider gap
    adv = AdvisorSettings(strategies=[StrategySpec.from_dict(d) for d in DEFAULT_ADVISORS], evidence_bars=60)
    strat = create_strategy("consensus", {})
    aligned = align_bars(data)
    out = strat.run(aligned)
    for cut in range(200, len(aligned[symbols[0]]) + 1, 37):
        end = aligned[symbols[0]].index[cut - 1]
        res = Recommender().recommend({s: df.loc[:end] for s, df in data.items()}, adv, namespace=f"h{cut}")
        for rec in res["recommendations"]:
            mine = strat.decision_at(out, rec["symbol"], cut - 1)
            assert rec["consensus"]["score"] == pytest.approx(mine["score"], abs=1e-12), (cut, rec["symbol"])
            assert rec["sizing"]["weight"] == pytest.approx(mine["weight"], abs=1e-12), (cut, rec["symbol"])
    assert any("starts on" in n for n in res["notes"])


def test_a_pair_leg_without_history_does_not_break_the_live_desk(daily):
    data = {s: daily[s].iloc[-300:] for s in ["SIMIDX", "SIMPRA"]} | {"SIMPRB": daily["SIMPRB"].iloc[-2:]}
    adv = AdvisorSettings(strategies=[StrategySpec.from_dict({"id": "pairs_trading"})], pairs=[("SIMPRA", "SIMPRB")])
    res = Recommender().recommend(data, adv)
    assert {r["symbol"] for r in res["recommendations"]} == {"SIMIDX", "SIMPRA"}


def test_consensus_backtests_with_its_own_sizing(daily):
    data = {s: daily[s].iloc[-700:] for s in UNIVERSE[:4]}
    strat, sizing = StrategySpec("consensus", sizing={"method": "vol_target"}).build()
    assert sizing.method == "fixed" and sizing.allocation == 1.0  # sizing settings never apply
    bt = backtest_strategy(strat, data, sizing)
    assert bt.start == strat.warmup() - 1
    # The engine is handed exactly the strategy's target weights, within the portfolio cap.
    assert (bt.weights.to_numpy() == strat.run(data).signals.to_numpy()).all()
    assert (bt.weights.abs().sum(axis=1) <= 1.0 + 1e-9).all()


def test_consensus_rejects_itself_and_unknown_members():
    with pytest.raises(ValueError, match="own members"):
        create_strategy("consensus", {"members": [{"id": "consensus"}]})
    with pytest.raises(ValueError, match="unknown strategy"):
        create_strategy("consensus", {"members": ["nope"]})
    with pytest.raises(ValueError, match="at least one"):
        create_strategy("consensus", {"members": []})
    s = create_strategy("consensus", {"members": '["tsmom"]', "pairs": [["ko", "pep"]]})
    assert s.params["members"] == [{"id": "tsmom", "params": {}}] and s.params["pairs"] == [["KO", "PEP"]]
