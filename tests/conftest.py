from __future__ import annotations

import pandas as pd
import pytest

from tests.helpers import END, INTRADAY_START, INTRADAY_SYMBOLS, UNIVERSE
from trades.data.synthetic import SyntheticProvider


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the user's real ~/.trades during tests."""
    monkeypatch.setenv("TRADES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.delenv("TRADES_PROVIDER", raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_BASE_URL", raising=False)
    monkeypatch.setenv("TRADES_DATA_CACHE", "off")  # tests that use the bar cache give it a tmp folder
    for var in (
        "TRADES_PASSWORD",
        "TRADES_SECRET_KEY",
        "TRADES_ALLOWED_HOSTS",
        "RENDER_EXTERNAL_HOSTNAME",
        "PORT",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(scope="session")
def provider() -> SyntheticProvider:
    return SyntheticProvider(seed=7, end=END)


@pytest.fixture(scope="session")
def daily(provider) -> dict[str, pd.DataFrame]:
    """~6 years of synthetic daily bars for the demo universe and the cointegrated pair."""
    return {s: provider.history(s).iloc[-1500:] for s in [*UNIVERSE, "SIMPRA", "SIMPRB"]}


@pytest.fixture(scope="session")
def intraday(provider) -> dict[str, pd.DataFrame]:
    """About seven months of synthetic 5-minute bars (145 sessions) for five demo symbols."""
    return {s: provider.history(s, "5m", start=INTRADAY_START) for s in INTRADAY_SYMBOLS}
