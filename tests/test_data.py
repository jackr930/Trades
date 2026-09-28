"""Data providers: synthetic generator, Yahoo/Alpaca adapters (mocked), CSV, service, settings."""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import numpy as np
import pandas as pd
import pytest

from trades.backtest.runner import bars_payload
from trades.config import SettingsStore
from trades.core.stats import engle_granger
from trades.core.timeframes import Timeframe
from trades.data.alpaca import AlpacaProvider
from trades.data.base import DataError, ProviderNotConfigured, Quote, normalize_bars
from trades.data.csv_provider import CSVProvider
from trades.data.service import DataService
from trades.data.synthetic import SCENARIOS, SyntheticProvider, scenario_bars, scenario_regimes
from trades.data.yahoo import YahooProvider

# ------------------------------------------------------------------------- synthetic


def test_synthetic_is_deterministic_and_prefix_stable():
    a = SyntheticProvider(seed=7, end=date(2026, 9, 25)).history("SIMTEC")
    b = SyntheticProvider(seed=7, end=date(2026, 9, 25)).history("SIMTEC")
    pd.testing.assert_frame_equal(a, b)
    shorter = SyntheticProvider(seed=7, end=date(2025, 1, 10)).history("SIMTEC")
    np.testing.assert_allclose(shorter.to_numpy(), a.loc[shorter.index].to_numpy())
    other_seed = SyntheticProvider(seed=8, end=date(2026, 9, 25)).history("SIMTEC")
    assert not np.allclose(other_seed["close"].to_numpy(), a["close"].to_numpy())


def test_synthetic_bars_are_valid(daily):
    for sym, df in daily.items():
        assert (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9).all(), sym
        assert (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9).all(), sym
        assert (df["volume"] > 0).all(), sym
        assert df.index.tz is not None and df.index.is_monotonic_increasing


def test_synthetic_volatility_is_realistic(daily):
    vol = np.log(daily["SIMIDX"]["close"]).diff().std() * np.sqrt(252)
    assert 0.08 < vol < 0.35
    bond_vol = np.log(daily["SIMBND"]["close"]).diff().std() * np.sqrt(252)
    assert bond_vol < vol


def test_synthetic_pair_is_cointegrated(daily):
    res = engle_granger(np.log(daily["SIMPRA"]["close"]), np.log(daily["SIMPRB"]["close"]))
    assert res.cointegrated
    assert 3 < res.half_life < 60


def test_synthetic_intraday(provider):
    bars = provider.history("SIMIDX", Timeframe.M15)
    assert len(bars) == 30 * 26
    day = bars.index[-1].tz_convert("America/New_York")
    assert (day.hour, day.minute) == (15, 45)


def test_scenarios_have_expected_shapes():
    for sid in SCENARIOS:
        df = scenario_bars(sid, seed=3)
        assert len(df) >= 500
        assert df.index[0].year == 2040
    crash = scenario_bars("crash", seed=1)
    assert (crash["close"] / crash["close"].cummax() - 1).min() < -0.3
    regimes = scenario_regimes("crash", seed=1)
    assert [r["regime"] for r in regimes][:2] == ["calm", "crash"]


def test_bars_payload_uses_unix_seconds(provider):
    """Regression: pandas 3 may store second-resolution indexes (asi8 is not always ns)."""
    df = provider.history("SIMIDX").iloc[-5:]
    assert bars_payload(df)["t"] == [int(ts.timestamp()) for ts in df.index]
    micro = df.copy()
    micro.index = micro.index.as_unit("us")
    assert bars_payload(micro)["t"] == [int(ts.timestamp()) for ts in df.index]


# ------------------------------------------------------------------------ normalise


def test_normalize_bars_handles_vendor_quirks():
    idx = pd.DatetimeIndex(["2024-01-03", "2024-01-02", "2024-01-03"]).tz_localize("America/New_York")
    raw = pd.DataFrame(
        {
            "Open": [2, 1, 2.5],
            "High": [1.9, 1.5, 3],
            "Low": [1.8, 0.5, 2],
            "Close": [2.2, 1.2, 2.8],
            "Adj Close": [1.1, 0.6, 1.4],
            "Volume": [10, 20, 30],
        },
        index=idx,
    )
    out = normalize_bars(raw, Timeframe.D1)
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert len(out) == 2 and out.index.is_monotonic_increasing
    assert str(out.index.tz) == "UTC" and out.index[0].hour == 0
    assert out["close"].iloc[-1] == pytest.approx(1.4)  # adjusted
    assert (out["high"] >= out[["open", "close"]].max(axis=1)).all()


# ---------------------------------------------------------------------------- yahoo


class FakeYahoo(YahooProvider):
    def __init__(self, frame, recent=None):
        super().__init__()
        self.frame = frame
        self.recent = recent
        self.calls = []

    def _download_history(self, symbol, interval, start, end):
        self.calls.append((symbol, interval, start, end))
        return self.frame

    def _download_recent(self, symbols):
        return self.recent


def test_yahoo_history_and_quotes():
    idx = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])).tz_localize(
        "America/New_York"
    )
    frame = pd.DataFrame(
        {
            "Open": [1.0, 2, 3],
            "High": [2.0, 3, 4],
            "Low": [0.5, 1.5, 2.5],
            "Close": [1.5, 2.5, 3.5],
            "Volume": [10, 20, 30],
        },
        index=idx,
    )
    cols = pd.MultiIndex.from_product([["AAPL"], ["Open", "High", "Low", "Close", "Adj Close", "Volume"]])
    recent = pd.DataFrame([[1, 2, 0.5, 1.5, 1.5, 10], [2, 3, 1.5, 2.5, 2.5, 20]], index=idx[:2], columns=cols)
    y = FakeYahoo(frame, recent)
    bars = y.history("AAPL", Timeframe.D1, start=date(2024, 1, 1), end=date(2024, 1, 5))
    assert len(bars) == 3 and bars["close"].iloc[-1] == 3.5
    q = y.quotes(["AAPL", "MSFT"])
    assert set(q) == {"AAPL"}
    assert q["AAPL"].price == 2.5 and q["AAPL"].prev_close == 1.5
    assert y.calls[0][1] == "1d"


def test_yahoo_empty_raises():
    with pytest.raises(DataError):
        FakeYahoo(pd.DataFrame()).history("NOPE")


def test_yahoo_clamps_intraday_lookback():
    frame = pd.DataFrame(
        {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
        index=pd.DatetimeIndex([pd.Timestamp.now(tz="UTC").floor("min")]),
    )
    y = FakeYahoo(frame)
    y.history("AAPL", Timeframe.M1, start=datetime(2000, 1, 1, tzinfo=timezone.utc))
    start = y.calls[0][2]
    assert (datetime.now(timezone.utc) - start).days <= 7


# --------------------------------------------------------------------------- alpaca


def _alpaca(handler):
    return AlpacaProvider("key", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_alpaca_requires_credentials():
    with pytest.raises(ProviderNotConfigured):
        AlpacaProvider(None, None)


def test_alpaca_bars_pagination_and_headers():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        assert request.headers["APCA-API-KEY-ID"] == "key"
        assert request.url.params["adjustment"] == "all" and request.url.params["feed"] == "iex"
        if "page_token" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "bars": [{"t": "2024-01-02T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}],
                    "next_page_token": "p2",
                },
            )
        return httpx.Response(
            200,
            json={
                "bars": [{"t": "2024-01-03T05:00:00Z", "o": 1.5, "h": 2.5, "l": 1, "c": 2, "v": 200}],
                "next_page_token": None,
            },
        )

    bars = _alpaca(handler).history("AAPL", Timeframe.D1, start=date(2024, 1, 1), end=date(2024, 1, 5))
    assert len(seen) == 2 and len(bars) == 2
    assert bars.index[0] == pd.Timestamp("2024-01-02", tz="UTC")  # 05:00Z -> trading date


def test_alpaca_snapshot_quotes():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "AAPL": {
                    "latestTrade": {"t": "2024-01-03T15:00:00.123456789Z", "p": 190.5},
                    "latestQuote": {"ap": 190.6, "bp": 190.4},
                    "dailyBar": {"o": 189, "h": 191, "l": 188, "c": 190.4, "v": 1000},
                    "prevDailyBar": {"c": 188.0},
                }
            },
        )

    q = _alpaca(handler).quotes(["AAPL", "MSFT"])
    assert set(q) == {"AAPL"}
    assert q["AAPL"].price == 190.5 and q["AAPL"].change_pct == pytest.approx(190.5 / 188 - 1)


def test_alpaca_auth_error():
    with pytest.raises(ProviderNotConfigured):
        _alpaca(lambda r: httpx.Response(403, text="forbidden")).quotes(["SPY"])


# ------------------------------------------------------------------------------ csv


def test_csv_provider(tmp_path):
    (tmp_path / "ABC.csv").write_text(
        "Date,Open,High,Low,Close,Adj Close,Volume\n"
        "2024-01-02,10,11,9,10.5,10.5,100\n2024-01-03,10.5,12,10,11.5,11.5,200\n"
    )
    p = CSVProvider(tmp_path)
    assert p.symbols() == ["ABC"]
    bars = p.history("abc")
    assert bars["close"].tolist() == [10.5, 11.5]
    assert p.quotes(["ABC"])["ABC"].prev_close == 10.5
    assert p.check()[0]
    with pytest.raises(DataError):
        p.history("MISSING")


# -------------------------------------------------------------------- service/settings


def test_settings_masking_and_persistence(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.update(
        {"alpaca_key_id": "AKTEST1234", "alpaca_secret_key": "supersecret9876", "risk_per_trade": 0.02}
    )
    pub = store.get().public_dict()
    assert pub["alpaca_key_id"] == "****1234" and pub["has_alpaca_credentials"]
    store.update({"alpaca_key_id": "****1234"})  # masked echo must not overwrite
    assert SettingsStore(tmp_path / "s.json").get().alpaca_key_id == "AKTEST1234"
    with pytest.raises(ValueError):
        store.update({"risk_per_trade": 5})
    store.update({"watchlists": {"yahoo": ["aapl", "msft", "AAPL", "bad symbol!"]}})
    assert store.get().watchlist("yahoo") == ["AAPL", "MSFT"]
    assert store.get().active_pairs(["SIMPRA", "SIMPRB"]) == [("SIMPRA", "SIMPRB")]


def test_env_credentials_override(tmp_path, monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "envkey")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "envsecret")
    s = SettingsStore(tmp_path / "s.json").get()
    assert s.alpaca_credentials() == ("envkey", "envsecret")


def test_data_service_caches_and_rebuilds(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    svc = DataService(store)
    p1 = svc.provider()
    assert svc.provider() is p1
    a = svc.bars("SIMIDX", "1d", count=100)
    assert len(a) == 100
    store.update({"synthetic_seed": 11})
    assert svc.provider() is not p1  # settings changed -> new instance
    frames, errors = svc.bars_many(["SIMIDX", "SIMTEC"], "1d", count=50)
    assert set(frames) == {"SIMIDX", "SIMTEC"} and not errors
    with pytest.raises(ProviderNotConfigured):
        svc.provider("alpaca")


def test_quote_change():
    q = Quote("X", 110.0, datetime.now(timezone.utc), prev_close=100.0)
    assert q.change == pytest.approx(10) and q.change_pct == pytest.approx(0.1)
    assert q.to_dict()["change_pct"] == pytest.approx(0.1)
