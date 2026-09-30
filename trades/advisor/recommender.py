"""Ensemble trade recommendations.

For every symbol the recommender runs each enabled strategy, records its vote
(+1 bullish / -1 bearish / 0 neutral) with a plain-language explanation, attaches the
strategy's historical evidence *on that symbol* (a backtest on completed bars), and
combines the votes into a consensus. Position sizing follows fixed-fractional risk
management: size so that hitting a protective stop at ``stop_atr`` x ATR costs
``risk_per_trade`` of equity, capped at ``max_position_pct`` and scaled by conviction.

This produces information for a human to evaluate. It never sends orders anywhere.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from trades.backtest.engine import BacktestConfig
from trades.backtest.runner import StrategySpec, backtest_strategy
from trades.core import indicators as ind
from trades.data.base import align_bars
from trades.strategies import Kind, get_strategy_class

EVIDENCE_KEYS = ("cagr", "sharpe", "max_drawdown", "win_rate", "n_trades", "psr", "exposure", "total_return")


@dataclass
class AdvisorSettings:
    strategies: list[StrategySpec]
    account_equity: float = 100_000.0
    risk_per_trade: float = 0.01
    stop_atr: float = 2.0
    max_position_pct: float = 0.20
    allow_short: bool = False
    periods_per_year: int = 252
    evidence_bars: int = 1260  # ~5 years of daily bars
    pairs: list[tuple[str, str]] = field(default_factory=list)
    commission_bps: float = 0.0
    slippage_bps: float = 5.0

    @classmethod
    def from_settings(
        cls, s, strategies: list[dict] | None = None, periods_per_year: int = 252
    ) -> AdvisorSettings:
        specs = [StrategySpec.from_dict(d) for d in (strategies if strategies is not None else s.advisors)]
        return cls(
            strategies=specs,
            account_equity=s.account_equity,
            risk_per_trade=s.risk_per_trade,
            stop_atr=s.stop_atr,
            max_position_pct=s.max_position_pct,
            allow_short=s.allow_short,
            periods_per_year=periods_per_year,
            commission_bps=s.commission_bps,
            slippage_bps=s.slippage_bps,
        )


def fingerprint(df: pd.DataFrame) -> tuple:
    """Cheap identity of a bar history for cache keys: a different source, seed or price
    adjustment (dividends rescale all past prices) changes it."""
    if len(df) == 0:
        return (0,)
    c = df["close"]
    return (len(df), str(df.index[0]), str(df.index[-1]), float(c.iloc[0]), float(c.iloc[-1]))


def consensus_label(score: float, allow_short: bool) -> tuple[str, str]:
    """Map a consensus score in [-1, 1] to (action, label)."""
    if score >= 0.5:
        return "BUY", "Strong buy"
    if score >= 0.2:
        return "BUY", "Buy"
    if score <= -0.5:
        return ("SHORT", "Strong sell / short") if allow_short else ("SELL", "Strong sell / avoid")
    if score <= -0.2:
        return ("SHORT", "Sell / short") if allow_short else ("SELL", "Sell / avoid")
    return "HOLD", "Neutral"


class Recommender:
    def __init__(self, cache_size: int = 512):
        self._evidence: OrderedDict[tuple, dict] = OrderedDict()
        self._lock = threading.Lock()
        self._cache_size = cache_size

    def clear(self) -> None:
        """Forget cached evidence (e.g. after the data source or costs change)."""
        with self._lock:
            self._evidence.clear()

    # -- evidence ------------------------------------------------------------------
    def _evidence_for(
        self, key: tuple, spec: StrategySpec, data: dict[str, pd.DataFrame], settings: AdvisorSettings
    ) -> dict[str, Any] | None:
        with self._lock:
            if key in self._evidence:
                self._evidence.move_to_end(key)
                return self._evidence[key]
        try:
            strat, sizing = spec.build()
            n = settings.evidence_bars + strat.warmup()
            sub = {s: df.iloc[-n:] for s, df in data.items()}
            cfg = BacktestConfig(
                initial_cash=100_000.0,
                slippage_bps=settings.slippage_bps,
                commission_bps=settings.commission_bps,
                allow_short=settings.allow_short or strat.kind is Kind.PAIR,
                periods_per_year=settings.periods_per_year,
            )
            bt = backtest_strategy(strat, sub, sizing, cfg)
            ev = {k: bt.metrics.get(k) for k in EVIDENCE_KEYS}
            ev["years"] = (len(bt.equity) - 1) / settings.periods_per_year
            ev["benchmark_cagr"] = (bt.benchmark_metrics or {}).get("cagr")
            ev["benchmark_sharpe"] = (bt.benchmark_metrics or {}).get("sharpe")
            ev["benchmark_max_drawdown"] = (bt.benchmark_metrics or {}).get("max_drawdown")
        except (ValueError, KeyError) as exc:
            ev = {"error": str(exc)}
        with self._lock:
            self._evidence[key] = ev
            while len(self._evidence) > self._cache_size:
                self._evidence.popitem(last=False)
        return ev

    # -- main entry point --------------------------------------------------------------
    def recommend(
        self,
        data: dict[str, pd.DataFrame],
        settings: AdvisorSettings,
        *,
        provisional: bool | dict[str, bool] = False,
        namespace: str = "",
        names: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """``data``: full bar history per symbol. ``provisional`` says whose last bar is
        still forming (one flag for all, or per symbol); evidence uses completed bars only."""
        symbols = [s for s, df in data.items() if len(df) >= 3]
        forming = (
            {s: bool(provisional.get(s, False)) for s in symbols}
            if isinstance(provisional, dict)
            else dict.fromkeys(symbols, bool(provisional))
        )
        completed = {s: (data[s].iloc[:-1] if forming[s] else data[s]) for s in symbols}
        votes: dict[str, list[dict]] = {s: [] for s in symbols}
        notes: list[str] = []

        for spec in settings.strategies:
            try:
                cls = get_strategy_class(spec.id)
            except KeyError as exc:
                notes.append(str(exc))
                continue
            if cls.kind is Kind.SINGLE:
                for sym in symbols:
                    self._single_vote(spec, sym, data[sym], completed[sym], settings, namespace, votes)
            elif cls.kind is Kind.CROSS_SECTIONAL:
                if len(symbols) < cls.min_symbols:
                    notes.append(f"{cls.name} needs at least {cls.min_symbols} symbols in the watchlist.")
                    continue
                self._group_vote(spec, symbols, data, completed, settings, namespace, votes)
            elif cls.kind is Kind.PAIR:
                for a, b in settings.pairs:
                    if a in data and b in data:
                        self._group_vote(spec, [a, b], data, completed, settings, namespace, votes)

        recs = [
            self._summarise(sym, data[sym], votes[sym], settings, forming[sym], (names or {}).get(sym))
            for sym in symbols
        ]
        recs.sort(key=lambda r: -abs(r["consensus"]["score"]))
        return {"recommendations": recs, "notes": notes, "provisional": any(forming.values())}

    def _single_vote(self, spec, sym, df, done_df, settings, namespace, votes):
        try:
            strat, sizing = spec.build()
            out = strat.run({sym: df})
            ex = strat.explain(out, sym)
        except ValueError as exc:
            votes[sym].append(self._error_vote(spec, str(exc)))
            return
        key = (
            namespace,
            sym,
            spec.key(),
            fingerprint(done_df),
            settings.evidence_bars,
            settings.allow_short,
            settings.slippage_bps,
            settings.commission_bps,
        )
        evidence = self._evidence_for(key, spec, {sym: done_df}, settings)
        votes[sym].append(self._vote(strat, ex, evidence))

    def _group_vote(self, spec, group, data, completed, settings, namespace, votes):
        try:
            strat, sizing = spec.build()
            aligned = align_bars({s: data[s] for s in group})
            out = strat.run(aligned)
        except ValueError as exc:
            for s in group:
                votes[s].append(self._error_vote(spec, str(exc)))
            return
        done = align_bars({s: completed[s] for s in group})
        key = (
            namespace,
            tuple(group),
            spec.key(),
            tuple(fingerprint(df) for df in done.values()),
            settings.evidence_bars,
            settings.allow_short,
            settings.slippage_bps,
            settings.commission_bps,
        )
        evidence = self._evidence_for(key, spec, done, settings)
        for s in group:
            ex = strat.explain(out, s)
            votes[s].append(self._vote(strat, ex, evidence, group=group))

    @staticmethod
    def _vote(strat, ex, evidence, group=None) -> dict[str, Any]:
        # A hedge leg (e.g. in statistical arbitrage) offsets other positions: it is no view on the symbol.
        direction = 0 if ex.state in ("flat", "warming_up", "hedge") else (1 if ex.signal > 0 else -1)
        return {
            "strategy_id": strat.id,
            "strategy_name": strat.name,
            "category": strat.category,
            "evidence_level": strat.evidence.value,
            "vote": direction,
            "state": ex.state,
            "headline": ex.headline,
            "rules": [r.to_dict() for r in ex.rules],
            "since": int(ex.since.timestamp()) if ex.since is not None else None,
            "fresh": ex.fresh,
            "exit_rule": ex.exit_rule,
            "evidence": evidence,
            "group": list(group) if group else None,
            "params": strat.params,
        }

    @staticmethod
    def _error_vote(spec, message) -> dict[str, Any]:
        cls = get_strategy_class(spec.id)
        return {
            "strategy_id": spec.id,
            "strategy_name": cls.name,
            "category": cls.category,
            "evidence_level": cls.evidence.value,
            "vote": 0,
            "state": "error",
            "headline": message,
            "rules": [],
            "since": None,
            "fresh": False,
            "exit_rule": "",
            "evidence": None,
            "group": None,
            "params": spec.params,
        }

    def _summarise(self, sym, df, votes, settings, provisional, name) -> dict[str, Any]:
        active = [v for v in votes if v["state"] not in ("warming_up", "error")]
        n = len(active)
        score = float(np.mean([v["vote"] for v in active])) if n else 0.0
        action, label = consensus_label(score, settings.allow_short)
        bull = sum(v["vote"] > 0 for v in active)
        bear = sum(v["vote"] < 0 for v in active)
        close = df["close"]
        price = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else float("nan")
        risk = risk_profile(df, settings.periods_per_year)
        sizing = suggest_position(price, risk.get("atr"), score, settings)
        flags = list(risk.pop("flags"))
        if provisional:
            flags.insert(0, "Signals use today's still-forming bar: they can change before the close.")
        fresh = [v["strategy_name"] for v in active if v["fresh"]]
        return {
            "symbol": sym,
            "name": name,
            "price": price,
            "change_pct": price / prev - 1.0 if prev and math.isfinite(prev) else None,
            "as_of": int(df.index[-1].timestamp()),
            "provisional": provisional,
            "consensus": {
                "score": score,
                "action": action,
                "label": label,
                "bullish": bull,
                "bearish": bear,
                "neutral": n - bull - bear,
                "n_votes": n,
                "agreement": (max(bull, bear, n - bull - bear) / n) if n else None,
            },
            "fresh_signals": fresh,
            "votes": votes,
            "sizing": sizing,
            "risk": {**risk, "flags": flags},
        }


def risk_profile(df: pd.DataFrame, ppy: int = 252) -> dict[str, Any]:
    close = df["close"]
    rets = close.pct_change(fill_method=None)
    atr = ind.atr(df["high"], df["low"], close, 20)
    atr_v = float(atr.iloc[-1]) if len(atr) and math.isfinite(atr.iloc[-1]) else None
    vol20 = float(rets.iloc[-20:].std() * math.sqrt(ppy)) if len(rets) > 21 else None
    vol1y = float(rets.iloc[-ppy:].std() * math.sqrt(ppy)) if len(rets) > 60 else None
    dollar_vol = float((close * df["volume"]).iloc[-20:].mean()) if len(df) >= 5 else None
    flags = []
    if vol20 is not None and vol20 > 0.5:
        flags.append(f"High volatility: {vol20:.0%} annualised over the last 20 bars.")
    if dollar_vol is not None and 0 < dollar_vol < 2_000_000:
        flags.append(f"Thin liquidity: about ${dollar_vol:,.0f} traded per bar; expect wider spreads.")
    if len(rets) > 21:
        sd = rets.iloc[-21:-1].std()
        last = rets.iloc[-1]
        if sd and math.isfinite(sd) and abs(last) > 3 * sd:
            flags.append(
                f"Unusually large move on the last bar ({last:+.1%}, {abs(last) / sd:.1f} standard deviations)."
            )
    hi52 = float(df["high"].iloc[-ppy:].max()) if len(df) else None
    lo52 = float(df["low"].iloc[-ppy:].min()) if len(df) else None
    return {
        "atr": atr_v,
        "atr_pct": atr_v / float(close.iloc[-1]) if atr_v else None,
        "vol_20": vol20,
        "vol_1y": vol1y,
        "avg_dollar_volume": dollar_vol,
        "high_52w": hi52,
        "low_52w": lo52,
        "flags": flags,
    }


def suggest_position(
    price: float, atr: float | None, score: float, settings: AdvisorSettings
) -> dict[str, Any]:
    equity = settings.account_equity
    side = "long" if score > 0 else "short" if score < 0 else "flat"
    if side == "short" and not settings.allow_short:
        side = "flat"
    if side == "flat" or not atr or atr <= 0 or price <= 0:
        return {
            "side": "flat",
            "shares": 0,
            "weight": 0.0,
            "notional": 0.0,
            "stop": None,
            "risk_amount": 0.0,
            "conviction": abs(score),
            "explanation": "No position suggested: the strategies do not agree on a direction."
            if side == "flat" and score == 0
            else "No position suggested (shorting disabled or not enough data).",
        }
    stop_dist = settings.stop_atr * atr
    risk_budget = equity * settings.risk_per_trade
    risk_shares = risk_budget / stop_dist
    cap_shares = equity * settings.max_position_pct / price
    base = min(risk_shares, cap_shares)
    conviction = min(abs(score), 1.0)
    shares = math.floor(base * conviction)
    limited_by = "risk budget" if risk_shares <= cap_shares else "max position size"
    stop = price - stop_dist if side == "long" else price + stop_dist
    expl = (
        f"Risking {settings.risk_per_trade:.1%} of ${equity:,.0f} (${risk_budget:,.0f}) with a stop "
        f"{settings.stop_atr:g} x ATR (${stop_dist:,.2f}) away allows {risk_shares:,.0f} shares; the "
        f"{settings.max_position_pct:.0%} position cap allows {cap_shares:,.0f}. Limited by the {limited_by}, "
        f"then scaled by {conviction:.0%} conviction -> {shares:,} shares."
    )
    return {
        "side": side,
        "shares": shares,
        "weight": shares * price / equity,
        "notional": shares * price,
        "stop": stop,
        "stop_distance": stop_dist,
        "risk_amount": shares * stop_dist,
        "conviction": conviction,
        "limited_by": limited_by,
        "explanation": expl,
    }
