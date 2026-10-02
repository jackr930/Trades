"""The Live Desk consensus: one shared decision function, and a strategy you can backtest.

``decide`` is the whole path from strategy votes to suggested positions:

1. **Score.** The mean of the active votes (+1 bullish, 0 neutral, -1 bearish); a strategy
   that is still warming up does not vote. With ``weighting="by_category"`` the votes are
   averaged within each strategy category first and then across categories, so four trend
   rules that agree count once, not four times.
2. **Action.** Score >= 0.2 is a buy (>= 0.5 a strong buy), <= -0.2 a sell (a short when
   shorting is allowed), anything in between is HOLD.
3. **Weight.** Fixed-fractional risk sizing: a protective stop ``stop_atr`` ATRs away should
   cost ``risk_per_trade`` of equity, capped at ``max_position_pct``, then scaled by
   conviction (the absolute score). HOLD, and a sell when shorting is off, mean no position.
   Equity cancels out (shares x price / equity), so the target weight is
   ``conviction x min(risk_per_trade x price / (stop_atr x ATR), max_position_pct)``.
4. **Portfolio cap.** If the suggestions add up to more than ``max_gross`` of equity, every
   one of them shrinks in proportion.

The Recommender behind the Live Desk calls ``decide`` once per update for the whole
watchlist; the ``Consensus`` strategy calls it at every bar of a backtest. Because both go
through the same function with the same votes, what is researched is what gets recommended.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from trades.config import DEFAULT_ADVISORS, DEFAULT_PAIRS, WEIGHTINGS
from trades.core import indicators as ind
from trades.strategies.base import (
    Evidence,
    Kind,
    Overlay,
    Param,
    Reference,
    Rule,
    Strategy,
    StrategyOutput,
)

BUY_AT = 0.2  # |score| at or above this leans far enough to act on
STRONG_AT = 0.5
ATR_LENGTH = 20  # the ATR behind stops and sizing, on the Live Desk and in backtests
ACTION_CODES = {"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0, "SHORT": -2.0}  # numeric, for diagnostics
VOTE_OF_STATE = {"long": 1.0, "short": -1.0}  # flat, hedge (no view on the symbol) -> 0

CLEMEN_1989 = Reference(
    "Clemen, R. T.",
    1989,
    "Combining Forecasts: A Review and Annotated Bibliography",
    "International Journal of Forecasting 5(4), 559-583",
    "https://doi.org/10.1016/0169-2070(89)90012-5",
)
RSZ_2010 = Reference(
    "Rapach, D. E., Strauss, J. K. & Zhou, G.",
    2010,
    "Out-of-Sample Equity Premium Prediction: Combination Forecasts and Links to the Real Economy",
    "Review of Financial Studies 23(2), 821-862",
    "https://doi.org/10.1093/rfs/hhp063",
)


def vote_of(state: str) -> float:
    """A strategy's vote from the state it reports: +1 long, -1 short, otherwise 0."""
    return VOTE_OF_STATE.get(state, 0.0)


@dataclass(frozen=True)
class ConsensusRules:
    """The settings that turn votes into positions (the Live Desk's risk settings)."""

    weighting: str = "equal"
    allow_short: bool = False
    risk_per_trade: float = 0.01
    stop_atr: float = 2.0
    max_position_pct: float = 0.20
    max_gross: float = 1.0

    def __post_init__(self):
        if self.weighting not in WEIGHTINGS:
            raise ValueError(f"weighting must be one of {', '.join(WEIGHTINGS)}")


@dataclass
class Decision:
    """What the consensus says about one symbol."""

    score: float
    action: str  # BUY | SELL | SHORT | HOLD
    label: str
    n_votes: int
    bullish: int
    bearish: int
    side: str = "flat"  # long | short | flat
    conviction: float = 0.0
    risk_weight: float | None = None  # weight at which the stop costs risk_per_trade of equity
    limited_by: str = ""  # risk budget | max position size
    stop_distance: float | None = None
    uncapped_weight: float = 0.0  # signed, before the portfolio cap
    scale: float = 1.0  # portfolio-cap factor applied to every suggestion
    weight: float = 0.0  # signed target weight: what the strategy trades and the Live Desk suggests
    reason: str = ""  # why there is no position

    @property
    def neutral(self) -> int:
        return self.n_votes - self.bullish - self.bearish


def consensus_score(votes: Sequence[tuple[str, float]], weighting: str = "equal") -> float:
    """Combine ``(category, vote)`` pairs of the strategies that are not warming up."""
    if not votes:
        return 0.0
    if weighting == "equal":
        return float(np.mean([float(v) for _, v in votes]))
    by_category: dict[str, list[float]] = {}
    for category, v in votes:
        by_category.setdefault(category, []).append(float(v))
    return float(np.mean([np.mean(vs) for vs in by_category.values()]))


def consensus_label(score: float, allow_short: bool) -> tuple[str, str]:
    """Map a consensus score in [-1, 1] to (action, label)."""
    if score >= STRONG_AT:
        return "BUY", "Strong buy"
    if score >= BUY_AT:
        return "BUY", "Buy"
    if score <= -STRONG_AT:
        return ("SHORT", "Strong sell / short") if allow_short else ("SELL", "Strong sell / avoid")
    if score <= -BUY_AT:
        return ("SHORT", "Sell / short") if allow_short else ("SELL", "Sell / avoid")
    return "HOLD", "Neutral"


def _size(d: Decision, price: float | None, atr: float | None, rules: ConsensusRules) -> None:
    """Fill in the position (before the portfolio cap) for one symbol's decision."""
    if d.action == "HOLD":
        d.reason = (
            f"No position: the votes do not lean far enough either way (the score is between "
            f"-{BUY_AT:g} and +{BUY_AT:g})."
        )
        return
    if d.action == "SELL":
        d.reason = "No position: the votes are bearish, but short selling is off, so the suggestion is to avoid it."
        return
    ok = price is not None and math.isfinite(price) and price > 0
    if not ok or atr is None or not math.isfinite(atr) or atr <= 0:
        d.reason = f"No position: not enough history for a {ATR_LENGTH}-bar ATR to set a stop."
        return
    d.side = "long" if d.action == "BUY" else "short"
    d.conviction = min(abs(d.score), 1.0)
    d.stop_distance = rules.stop_atr * atr
    d.risk_weight = rules.risk_per_trade * price / d.stop_distance
    d.limited_by = "risk budget" if d.risk_weight <= rules.max_position_pct else "max position size"
    base = min(d.risk_weight, rules.max_position_pct)
    d.uncapped_weight = (1.0 if d.side == "long" else -1.0) * d.conviction * base


def decide(
    votes: Mapping[str, Sequence[tuple[str, float]]],
    prices: Mapping[str, float],
    atrs: Mapping[str, float | None],
    rules: ConsensusRules,
) -> dict[str, Decision]:
    """Votes -> score -> action -> target weight for every symbol, then the portfolio cap.

    ``votes`` maps each symbol to the ``(category, vote)`` pairs of its active strategies;
    ``prices`` and ``atrs`` are the latest close and ATR(20) per symbol.
    """
    out: dict[str, Decision] = {}
    for sym, vs in votes.items():
        score = consensus_score(vs, rules.weighting)
        action, label = consensus_label(score, rules.allow_short)
        d = Decision(score, action, label, len(vs), sum(v > 0 for _, v in vs), sum(v < 0 for _, v in vs))
        _size(d, prices.get(sym), atrs.get(sym), rules)
        out[sym] = d
    gross = sum(abs(d.uncapped_weight) for d in out.values())
    scale = rules.max_gross / gross if gross > rules.max_gross else 1.0
    for d in out.values():
        d.scale = scale
        d.weight = d.uncapped_weight * scale
    return out


def params_from_settings(s) -> dict:
    """The consensus parameters that reproduce the Live Desk under user settings ``s``."""
    return {
        "members": s.advisors,
        "pairs": s.pairs,
        "weighting": s.consensus_weighting,
        "allow_short": s.allow_short,
        "risk_per_trade": s.risk_per_trade,
        "stop_atr": s.stop_atr,
        "max_position_pct": s.max_position_pct,
        "max_gross": s.max_gross_exposure,
    }


def member_votes(strategy: Strategy, output: StrategyOutput, symbol: str) -> np.ndarray:
    """A member's vote on ``symbol`` at every bar; NaN while it is warming up (no vote)."""
    states = strategy.states(output, symbol)
    votes = np.array([vote_of(s) for s in states], dtype=float)
    votes[states == "warming_up"] = np.nan
    return votes


class Consensus(Strategy):
    id = "consensus"
    name = "Live Desk consensus"
    category = "Ensemble"
    kind = Kind.CROSS_SECTIONAL  # needs the whole watchlist: ranking and pair members vote across symbols
    min_symbols = 1
    sizes_itself = True
    summary = (
        "Trade exactly what the Live Desk suggests: the average vote of its strategies, sized by risk "
        "and capped across the whole watchlist."
    )
    rules_text = (
        "Run every member strategy as the Live Desk does; a member still warming up does not vote.",
        "Score = average vote (+1 bullish, 0 neutral, -1 bearish). 'By category' averages within each "
        "strategy category first, then across categories.",
        "Score >= +0.2 -> long; <= -0.2 -> short if shorting is allowed, otherwise no position; in between "
        "-> hold, no position.",
        "Weight = conviction x min(risk per trade x price / (stop ATRs x ATR(20)), max position). If the "
        "weights add up to more than the portfolio cap, all of them shrink in proportion.",
    )
    rationale = (
        "Combining forecasts usually beats betting on any single one, because their errors partly cancel "
        "(Clemen 1989; Rapach, Strauss & Zhou 2010). The Live Desk's suggestion is such a combination, so this "
        "is the strategy you would actually be following if you traded on it."
    )
    failure_modes = (
        "Correlated members are not independent opinions: with equal weighting, four trend rules that agree "
        "count four times ('by category' counts them once). Averaging dilutes each member's edge along with "
        "its noise, and positions are re-sized at every close, which costs turnover."
    )
    evidence = Evidence.EXPERIMENTAL
    evidence_text = (
        "Forecast combination is well documented in economics, but this particular combination of rules, "
        "sized this way on a hand-picked watchlist, has no published track record: a backtest here is its "
        "first test, and only a forward record is truly unseen."
    )
    references = (CLEMEN_1989, RSZ_2010)
    params_spec = (
        Param(
            "members",
            "Member strategies",
            DEFAULT_ADVISORS,
            "list",
            help='The Live Desk\'s strategy list, as [{"id": "tsmom", "params": {}}, ...].',
        ),
        Param(
            "pairs",
            "Pairs for pair strategies",
            DEFAULT_PAIRS,
            "list",
            help='As [["KO", "PEP"]]. A pair votes only when both legs are among the symbols.',
        ),
        Param(
            "weighting",
            "Weighting",
            "equal",
            "choice",
            choices=WEIGHTINGS,
            help="equal: every vote counts once. by_category: average within each category first.",
        ),
        Param("allow_short", "Allow short positions", False, "bool"),
        Param("risk_per_trade", "Risk per trade (fraction)", 0.01, "float", 0.0001, 0.1, 0.001),
        Param("stop_atr", "Stop distance (ATRs)", 2.0, "float", 0.25, 10.0, 0.25),
        Param("max_position_pct", "Max position (fraction)", 0.20, "float", 0.001, 1.0, 0.01),
        Param(
            "max_gross",
            "Portfolio cap (gross exposure)",
            1.0,
            "float",
            0.1,
            2.0,
            0.05,
            help="1.0 = all suggestions together at most 100% of equity.",
        ),
    )
    # Signals are already target weights; the sizing cap sits at the highest portfolio cap allowed.
    default_sizing = {"method": "fixed", "allocation": 1.0, "max_leverage": 2.0}
    overlays = (Overlay("score", "Consensus score", "lower", levels=(-STRONG_AT, -BUY_AT, BUY_AT, STRONG_AT)),)

    def validate(self) -> None:
        from trades.strategies import create_strategy  # the registry imports this module

        members = []
        for m in self.params["members"]:
            spec = {"id": m} if isinstance(m, str) else m
            if not isinstance(spec, dict) or not isinstance(spec.get("id"), str):
                raise ValueError('Each member needs a strategy id, e.g. {"id": "tsmom", "params": {}}')
            if spec["id"] == self.id:
                raise ValueError("The consensus cannot be one of its own members")
            members.append({"id": spec["id"], "params": dict(spec.get("params") or {})})
        if not members:
            raise ValueError("Choose at least one member strategy")
        pairs = []
        for p in self.params["pairs"]:
            if not isinstance(p, (list, tuple)) or len(p) != 2 or not all(isinstance(x, str) for x in p):
                raise ValueError('Each pair needs two symbols, e.g. ["KO", "PEP"]')
            pairs.append([p[0].strip().upper(), p[1].strip().upper()])
        self.params["members"], self.params["pairs"] = members, pairs
        self.members = [create_strategy(m["id"], m["params"]) for m in members]
        self.decision_rules = ConsensusRules(
            weighting=self.params["weighting"],
            allow_short=self.params["allow_short"],
            risk_per_trade=self.params["risk_per_trade"],
            stop_atr=self.params["stop_atr"],
            max_position_pct=self.params["max_position_pct"],
            max_gross=self.params["max_gross"],
        )

    def warmup(self) -> int:
        return max(ATR_LENGTH, *(m.warmup() for m in self.members))

    # -- evaluation --------------------------------------------------------------------
    def _ballots(self, data: dict[str, pd.DataFrame]) -> dict[str, list[tuple[str, str, np.ndarray]]]:
        """Every member's votes per symbol, grouped exactly as the Live Desk groups them:
        single-symbol rules one symbol at a time, ranking rules over all symbols, pair rules
        over each configured pair. Returns symbol -> [(label, category, votes per bar)]."""
        symbols = list(data)
        ballots: dict[str, list[tuple[str, str, np.ndarray]]] = {s: [] for s in symbols}
        for m in self.members:
            if m.kind is Kind.SINGLE:
                groups = [[s] for s in symbols]
            elif m.kind is Kind.CROSS_SECTIONAL:
                groups = [symbols] if len(symbols) >= m.min_symbols else []
            else:
                groups = [[a, b] for a, b in self.params["pairs"] if a in data and b in data]
            for group in groups:
                try:
                    out = m.run({s: data[s] for s in group})
                except ValueError:
                    continue  # the Live Desk shows an error vote, which does not count either
                label = m.name + (f" ({group[0]}/{group[1]})" if m.kind is Kind.PAIR else "")
                for s in group:
                    ballots[s].append((label, m.category, member_votes(m, out, s)))
        return ballots

    def run(self, data: dict[str, pd.DataFrame]) -> StrategyOutput:
        self.check_symbols(list(data))
        symbols = list(data)
        index = next(iter(data.values())).index
        T, N = len(index), len(symbols)
        ballots = self._ballots(data)
        close = {s: data[s]["close"].to_numpy(float) for s in symbols}
        atr = {
            s: ind.atr(data[s]["high"], data[s]["low"], data[s]["close"], ATR_LENGTH).to_numpy(float)
            for s in symbols
        }
        fields = ("score", "action", "n_votes", "bullish", "bearish", "uncapped_weight", "scale", "weight")
        cols = {f: np.zeros((T, N)) for f in fields}
        for t in range(T):
            votes = {
                s: [(category, float(v[t])) for _, category, v in ballots[s] if not np.isnan(v[t])]
                for s in symbols
            }
            prices = {s: close[s][t] for s in symbols}
            decisions = decide(votes, prices, {s: atr[s][t] for s in symbols}, self.decision_rules)
            for j, s in enumerate(symbols):
                d = decisions[s]
                row = (d.score, ACTION_CODES[d.action], d.n_votes, d.bullish, d.bearish)
                for f, value in zip(fields, (*row, d.uncapped_weight, d.scale, d.weight), strict=True):
                    cols[f][t, j] = value
        valid = np.arange(T) >= self.warmup() - 1
        diags = {}
        for j, s in enumerate(symbols):
            frame = {f: cols[f][:, j] for f in fields}
            frame |= {"atr": atr[s], "valid": valid}
            frame |= {f"vote: {label}": votes for label, _, votes in ballots[s]}
            diags[s] = pd.DataFrame(frame, index=index)
        return StrategyOutput(pd.DataFrame(cols["weight"], index=index, columns=symbols), diags)

    def decision_at(self, output: StrategyOutput, symbol: str, t: int) -> dict:
        """The Live Desk's call on ``symbol`` at bar ``t``: action, label, score and target weight."""
        d = output.diagnostics[symbol]
        score = float(d["score"].iloc[t])
        action, label = consensus_label(score, self.decision_rules.allow_short)
        return {"action": action, "label": label, "score": score, "weight": float(d["weight"].iloc[t])}

    def describe(self, output: StrategyOutput, symbol: str, t: int) -> tuple[str, list[Rule]]:
        d = output.diagnostics[symbol]
        call = self.decision_at(output, symbol, t)
        n, bull, bear = (int(d[c].iloc[t]) for c in ("n_votes", "bullish", "bearish"))
        head = (
            f"{call['label']} (score {call['score']:+.2f}: {bull} bullish, {bear} bearish, "
            f"{n - bull - bear} neutral)."
        )
        w = call["weight"]
        if abs(w) > 1e-12:
            head += f" Target {abs(w):.1%} of equity {'long' if w > 0 else 'short'}"
            head += " after scaling to the portfolio cap." if d["scale"].iloc[t] < 1 - 1e-12 else "."
        else:
            head += " No position."
        rules = [Rule("Weighting", self.decision_rules.weighting.replace("_", " "), None)]
        for col in (c for c in d.columns if c.startswith("vote: ")):
            v = d[col].iloc[t]
            text = "warming up" if np.isnan(v) else "bullish" if v > 0 else "bearish" if v < 0 else "neutral"
            rules.append(Rule(col[len("vote: ") :], text, None if np.isnan(v) or v == 0 else bool(v > 0)))
        return head, rules

    def exit_rule(self, state: str) -> str:
        return (
            "Re-decided at every close: the position grows, shrinks or closes as the votes, the ATR and the "
            "portfolio cap change (re-sizing trades under 0.5% of equity are skipped)."
        )
