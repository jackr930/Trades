"""Defaults shared by the app settings and the strategies: the watchlists and the Live Desk's
strategy line-up. Kept free of imports so any layer can use them."""

from __future__ import annotations

from typing import Any

DEFAULT_WATCHLISTS: dict[str, list[str]] = {
    "synthetic": ["SIMIDX", "SIMTEC", "SIMBNK", "SIMNRG", "SIMUTL", "SIMHLC", "SIMBND", "SIMGLD"],
    "yahoo": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "JPM", "XOM", "TLT"],
    "alpaca": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "JPM", "XOM", "TLT"],
    "massive": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "JPM", "XOM", "TLT"],
    "csv": [],
}


DEFAULT_ADVISORS: list[dict[str, Any]] = [
    {"id": "tsmom", "params": {}},
    {"id": "faber_trend", "params": {}},
    {"id": "ma_crossover", "params": {}},
    {"id": "donchian_breakout", "params": {}},
    {"id": "bollinger_reversion", "params": {}},
    {"id": "rsi2_reversion", "params": {}},
    {"id": "xs_momentum", "params": {}},
    {"id": "pairs_trading", "params": {}},
]


DEFAULT_PAIRS: list[list[str]] = [["SIMPRA", "SIMPRB"]]  # the demo market's cointegrated pair


WEIGHTINGS = ("equal", "by_category")  # how the Live Desk averages votes (see trades.strategies.consensus)

