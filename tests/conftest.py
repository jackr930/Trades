from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trades.data.synthetic import SyntheticProvider

END = date(2026, 9, 25)
UNIVERSE = ["SIMIDX", "SIMTEC", "SIMBNK", "SIMNRG", "SIMUTL", "SIMHLC", "SIMBND", "SIMGLD"]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the user's real ~/.trades during tests."""
    monkeypatch.setenv("TRADES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.delenv("TRADES_PROVIDER", raising=False)


@pytest.fixture(scope="session")
def provider() -> SyntheticProvider:
    return SyntheticProvider(seed=7, end=END)


@pytest.fixture(scope="session")
def daily(provider) -> dict[str, pd.DataFrame]:
    """~6 years of synthetic daily bars for the demo universe and the cointegrated pair."""
    return {s: provider.history(s).iloc[-1500:] for s in [*UNIVERSE, "SIMPRA", "SIMPRB"]}


def make_bars(closes, *, start="2024-01-02", spread=0.01, volume=1_000_000.0) -> pd.DataFrame:
    """Deterministic OHLCV frame from a close series (open = previous close)."""
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + spread)
    lows = np.minimum(opens, closes) * (1 - spread)
    idx = pd.bdate_range(start, periods=len(closes), tz="UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume}, index=idx
    )
