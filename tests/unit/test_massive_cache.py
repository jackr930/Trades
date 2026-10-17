"""The Massive provider (mocked HTTP) and the parquet bar cache."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
import pandas as pd
import pytest

from trades.data.base import DataError, ProviderNotConfigured, SymbolNotFound, normalize_bars
from trades.data.calendar import NY, session_bounds, trading_days
from trades.data.massive import MassiveProvider, NotEntitled
from trades.data.service import DataService
from trades.data.timeframes import Timeframe
from trades.settings import SettingsStore

# ---------------------------------------------------------------------------- massive


def _ms(ts: str) -> int:
    return int(pd.Timestamp(ts).timestamp() * 1000)


def _massive(handler, **kw):
    sleeps: list[float] = []
    prov = MassiveProvider(
        "secret-key", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append, **kw
    )
    return prov, sleeps


def test_massive_requires_a_key_and_https():
    with pytest.raises(ProviderNotConfigured):
        MassiveProvider("")
    with pytest.raises(ProviderNotConfigured):
        MassiveProvider("k", base_url="http://api.massive.com")
    assert MassiveProvider("k", base_url="https://api.polygon.io/").base_url == "https://api.polygon.io"


def test_massive_daily_bars_paginate_on_the_configured_host_only():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer secret-key"
        assert "secret-key" not in str(request.url)  # the key never goes in a URL
        assert request.url.host == "api.massive.com"
        if "cursor" not in request.url.params:
            assert request.url.path == "/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-05"
            assert request.url.params["adjusted"] == "true" and request.url.params["sort"] == "asc"
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    # daily bars are stamped midnight New York (05:00 UTC in winter)
                    "results": [{"t": _ms("2024-01-02T05:00:00Z"), "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}],
                    # a next_url naming another host is followed on the configured one
                    "next_url": "https://elsewhere.example/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-05?cursor=abc",
                },
            )
        assert request.url.params["cursor"] == "abc"
        return httpx.Response(
            200, json={"results": [{"t": _ms("2024-01-03T05:00:00Z"), "o": 1.5, "h": 2.5, "l": 1, "c": 2, "v": 200}]}
        )

    prov, _ = _massive(handler)
    bars = prov.history("aapl", Timeframe.D1, start=date(2024, 1, 1), end=date(2024, 1, 5))
    assert len(seen) == 2
    assert list(bars.index) == [pd.Timestamp("2024-01-02", tz="UTC"), pd.Timestamp("2024-01-03", tz="UTC")]
    assert bars["close"].tolist() == [1.5, 2.0]


def test_massive_intraday_bars_use_millisecond_bounds():
    def handler(request: httpx.Request):
        *_, lo, hi = request.url.path.split("/")
        assert request.url.path.startswith("/v2/aggs/ticker/QQQ/range/5/minute/")
        assert int(lo) == _ms("2024-01-02T14:30:00Z") and int(hi) == _ms("2024-01-02T21:00:00Z")
        return httpx.Response(
            200, json={"results": [{"t": _ms("2024-01-02T14:30:00Z"), "o": 400, "h": 401, "l": 399, "c": 400.5, "v": 9}]}
        )

    prov, _ = _massive(handler)
    bars = prov.history(
        "QQQ", "5m", start=datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc), end=datetime(2024, 1, 2, 21, tzinfo=timezone.utc)
    )
    assert bars.index[0] == pd.Timestamp("2024-01-02T14:30:00Z")  # intraday bars keep their start time


def test_massive_errors_say_what_is_wrong():
    prov, _ = _massive(lambda r: httpx.Response(401, json={"status": "ERROR", "error": "Unknown API Key"}))
    with pytest.raises(ProviderNotConfigured, match="Unknown API Key"):
        prov.history("SPY", Timeframe.D1, start=date(2024, 1, 1), end=date(2024, 1, 5))
    prov, _ = _massive(lambda r: httpx.Response(403, json={"status": "NOT_AUTHORIZED", "message": "upgrade"}))
    with pytest.raises(NotEntitled, match="plan does not include"):
        prov.history("SPY", Timeframe.M1, start=date(2010, 1, 1), end=date(2010, 1, 5))
    prov, _ = _massive(lambda r: httpx.Response(200, json={"status": "OK", "resultsCount": 0}))
    with pytest.raises(SymbolNotFound, match="how far back your plan reaches"):
        prov.history("NOPE", Timeframe.D1, start=date(2024, 1, 1), end=date(2024, 1, 5))


def test_massive_waits_out_the_rate_limit():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"status": "ERROR", "error": "exceeded the maximum requests per minute"})
        return httpx.Response(200, json={"results": [{"t": _ms("2024-01-02T05:00:00Z"), "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]})

    prov, sleeps = _massive(handler)
    assert len(prov.history("SPY", Timeframe.D1, start=date(2024, 1, 1), end=date(2024, 1, 5))) == 1
    assert sleeps == [12.0]  # the free plan's window: 5 requests a minute

    prov, sleeps = _massive(lambda r: httpx.Response(429, text="slow down"))
    with pytest.raises(DataError, match="rate limit"):
        prov.history("SPY", Timeframe.D1, start=date(2024, 1, 1), end=date(2024, 1, 5))
    assert len(sleeps) == 3


def test_massive_snapshot_quotes_keep_extended_hours_out_of_the_session():
    in_session = datetime(2024, 1, 3, 15, 0, tzinfo=timezone.utc)  # 10:00 New York
    after_hours = datetime(2024, 1, 3, 22, 0, tzinfo=timezone.utc)  # 17:00 New York

    def ns(dt: datetime) -> int:
        return int(dt.timestamp() * 1e9)

    def handler(request):
        assert request.url.path == "/v2/snapshot/locale/us/markets/stocks/tickers"
        assert request.url.params["tickers"] == "AAPL,MSFT"
        return httpx.Response(
            200,
            json={
                "tickers": [
                    {
                        "ticker": "AAPL",
                        "lastTrade": {"p": 190.5, "t": ns(in_session)},
                        "lastQuote": {"p": 190.4, "P": 190.6},
                        "day": {"o": 189, "h": 191, "l": 188, "c": 190.4, "v": 1000},
                        "prevDay": {"c": 188.0},
                    },
                    {
                        "ticker": "MSFT",
                        "lastTrade": {"p": 371.0, "t": ns(after_hours)},  # an after-hours print
                        "day": {"o": 370, "h": 372, "l": 369, "c": 370.5, "v": 900},
                        "prevDay": {"c": 368.0},
                        "updated": ns(after_hours),
                    },
                ]
            },
        )

    prov, _ = _massive(handler)
    q = prov.quotes(["AAPL", "msft"])
    assert q["AAPL"].price == 190.5 and q["AAPL"].timestamp == in_session
    assert (q["AAPL"].bid, q["AAPL"].ask) == (190.4, 190.6)
    assert q["AAPL"].change_pct == pytest.approx(190.5 / 188 - 1)
    # Outside the session: the day's close, stamped with the snapshot's own (after-hours) time,
    # so it can never be folded into a daily bar.
    assert q["MSFT"].price == 370.5 and q["MSFT"].timestamp == after_hours


def test_massive_free_plan_quotes_come_from_end_of_day_bars():
    grouped = []

    def handler(request):
        if "snapshot" in request.url.path:
            return httpx.Response(403, json={"status": "NOT_AUTHORIZED", "message": "upgrade for snapshots"})
        grouped.append(request.url.path.rsplit("/", 1)[-1])
        close = 100.0 + len(grouped)
        return httpx.Response(200, json={"results": [{"T": "SPY", "o": 99, "h": 102, "l": 98, "c": close, "v": 5}]})

    prov, _ = _massive(handler)
    q = prov.quotes(["SPY", "ZZZZ"])
    assert set(q) == {"SPY"} and q["SPY"].source == "massive-eod"
    assert q["SPY"].price == 101.0 and q["SPY"].prev_close == 102.0  # latest session, then the one before
    session = date.fromisoformat(grouped[0])
    assert q["SPY"].timestamp == session_bounds(session)[1].astimezone(timezone.utc)
    prov.quotes(["SPY"])
    assert len(grouped) == 2  # a completed session's bars are fetched once


def test_massive_settings_are_masked_and_env_wins(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / "s.json")
    store.update({"massive_api_key": "mk_live_abcdef1234", "massive_base_url": "https://api.polygon.io"})
    pub = store.get().public_dict()
    assert pub["massive_api_key"] == "****1234" and pub["has_massive_credentials"]
    store.update({"massive_api_key": "****1234"})  # the masked value echoed back changes nothing
    assert store.get().massive_api_key == "mk_live_abcdef1234"
    for bad in ("http://api.massive.com", "https://x.example/?key=1", "https://user@x.example"):
        with pytest.raises(ValueError):
            store.update({"massive_base_url": bad})
    monkeypatch.setenv("MASSIVE_API_KEY", "envkey")
    assert store.get().massive_credentials() == ("envkey", "https://api.polygon.io")

    svc = DataService(store, disk_cache=None)
    assert isinstance(svc.provider("massive"), MassiveProvider)
    assert svc.provider("massive").base_url == "https://api.polygon.io"
    row = next(p for p in svc.catalog() if p["id"] == "massive")
    assert row["configured"] and row["requires_key"]


# ------------------------------------------------------------------------------ cache

pytest.importorskip("pyarrow")

from trades.data.cache import BarCache  # noqa: E402 - needs pyarrow

NOW = datetime(2026, 10, 16, 15, 0, tzinfo=NY)  # a Friday, mid-session


class DeepHistory:
    """A provider with years of bars that never ends, as a deep-history vendor looks from here:
    one daily bar per trading day, or 5-minute bars through each session, up to ``NOW`` (today's
    bars still forming). After a 2-for-1 split an adjusted feed shows all of history at half the
    old price: ``split_factor = 2`` does that. ``halt`` drops the sessions in a date range,
    ``published_through`` hides sessions the vendor has not published yet, ``history_from`` is a
    plan's history limit, and ``volume_factor`` revises every volume."""

    def __init__(self, timeframe: Timeframe = Timeframe.D1, now: datetime = NOW):
        self.timeframe = timeframe
        self.now = now
        self.calls: list[tuple] = []
        self.split_factor = 1.0
        self.halt: tuple[date, date] | None = None
        self.published_through: date | None = None
        self.history_from: date | None = None
        self.volume_factor = 1.0

    def _bars(self, lo: date, hi: date) -> pd.DataFrame:
        days = trading_days(max(lo, self.history_from or lo), min(hi, self.now.date(), self.published_through or hi))
        if self.halt:
            days = [d for d in days if not self.halt[0] <= d <= self.halt[1]]
        if self.timeframe is Timeframe.D1:
            index = [pd.Timestamp(d) for d in days]
        else:
            index = [
                t
                for d in days
                for t in pd.date_range(session_bounds(d)[0], session_bounds(d)[1], freq="5min", inclusive="left")
                if t <= self.now
            ]
        index = pd.DatetimeIndex(index, tz=None if self.timeframe is Timeframe.D1 else NY)
        # A price is a function of its time alone, as it is at a real vendor.
        minutes = np.array([(t - pd.Timestamp("2015-01-01", tz=t.tz)).total_seconds() / 60 for t in index])
        close = 100 + minutes / 1440 * 0.05 + (minutes % 7) * 0.01
        close = close / self.split_factor
        volume = 1e6 * self.volume_factor
        df = pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": volume}, index=index)
        return normalize_bars(df, self.timeframe)

    def history(self, symbol, timeframe, start, end):
        self.calls.append((start, end))
        lo = start if not isinstance(start, datetime) else start.astimezone(NY).date()
        hi = end if not isinstance(end, datetime) else end.astimezone(NY).date()
        bars = self._bars(lo, hi)
        if isinstance(start, datetime):
            bars = bars[bars.index >= pd.Timestamp(start)]
        if isinstance(end, datetime):
            bars = bars[bars.index <= pd.Timestamp(end)]
        if bars.empty:
            raise SymbolNotFound("no bars")
        return bars


def _cached(cache: BarCache, prov: DeepHistory, start, end):
    return cache.history("massive", "SPY", prov.timeframe, start, end, lambda a, b: prov.history("SPY", prov.timeframe, a, b))


def test_cache_serves_a_repeat_request_from_disk(tmp_path):
    prov, cache = DeepHistory(), BarCache(tmp_path, now=lambda: NOW)
    first = _cached(cache, prov, date(2020, 1, 1), date(2024, 12, 31))
    again = _cached(cache, prov, date(2021, 3, 1), date(2024, 6, 30))
    assert len(prov.calls) == 1  # the second request is inside what is stored
    pd.testing.assert_frame_equal(again, first.loc["2021-03-01":"2024-06-30"])
    pd.testing.assert_frame_equal(first, prov._bars(date(2020, 1, 1), date(2024, 12, 31)))
    [entry] = cache.entries()
    assert (entry.provider, entry.symbol, entry.timeframe, entry.bars) == ("massive", "SPY", "1d", len(first))


def test_cache_downloads_only_the_new_edge_and_never_stores_today(tmp_path):
    prov, cache = DeepHistory(), BarCache(tmp_path, now=lambda: NOW)
    _cached(cache, prov, date(2020, 1, 1), date(2024, 12, 31))
    full = _cached(cache, prov, date(2020, 1, 1), None)  # through today: today's bar is still forming
    assert len(prov.calls) == 2
    lo, _ = prov.calls[1]
    assert date(2024, 12, 5) <= lo <= date(2024, 12, 15)  # the edge, plus an overlap to re-check
    pd.testing.assert_frame_equal(full, prov._bars(date(2020, 1, 1), NOW.date()))
    assert full.index[-1] == pd.Timestamp(NOW.date(), tz="UTC")  # returned, but not stored:
    [entry] = cache.entries()
    assert entry.last == pd.Timestamp("2026-10-15", tz="UTC").isoformat()
    _cached(cache, prov, date(2020, 1, 1), None)
    assert len(prov.calls) == 3  # so the forming bar is fetched fresh every time

    _cached(cache, prov, date(2018, 6, 1), date(2019, 6, 1))  # earlier than anything stored
    lo, hi = prov.calls[3]
    assert lo == date(2018, 6, 1) and date(2020, 1, 15) <= hi <= date(2020, 1, 31)


def test_cache_starts_over_when_a_split_rescales_history(tmp_path):
    prov, cache = DeepHistory(), BarCache(tmp_path, now=lambda: NOW)
    _cached(cache, prov, date(2020, 1, 1), date(2024, 12, 31))
    prov.split_factor = 2.0  # a split in 2025: every adjusted price so far is now half what is stored
    bars = _cached(cache, prov, date(2020, 1, 1), date(2025, 12, 31))
    assert len(prov.calls) == 3  # the edge disagreed on the overlap, so the whole range came again
    pd.testing.assert_frame_equal(bars, prov._bars(date(2020, 1, 1), date(2025, 12, 31)))
    returns = bars["close"].pct_change().abs()
    assert returns.max() < 0.01  # no fake -50% day where two price scales would have met


def test_cache_of_intraday_bars_keeps_sessions_whole(tmp_path):
    prov, cache = DeepHistory(Timeframe.M5), BarCache(tmp_path, now=lambda: NOW)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    bars = _cached(cache, prov, start, None)
    assert bars.index[-1] <= pd.Timestamp(NOW) and bars.index[-1].date() == NOW.date()
    [entry] = cache.entries()
    assert pd.Timestamp(entry.last).tz_convert(NY).date() == date(2026, 10, 15)  # only whole sessions stored
    again = _cached(cache, prov, start, datetime(2026, 10, 9, 20, tzinfo=timezone.utc))
    assert len(prov.calls) == 1
    pd.testing.assert_frame_equal(again, bars[bars.index <= pd.Timestamp("2026-10-09T20:00Z")])


def test_cache_through_the_data_service(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    prov = DeepHistory()
    svc = DataService(store, disk_cache=BarCache(tmp_path / "cache", now=lambda: NOW))
    svc.provider = lambda pid=None: prov  # type: ignore[method-assign]
    a = svc.bars("SPY", "1d", date(2022, 1, 1), date(2023, 1, 1), "massive")
    svc.invalidate()  # empty the in-memory cache: the next answer comes from disk
    b = svc.bars("SPY", "1d", date(2022, 1, 1), date(2023, 1, 1), "massive")
    pd.testing.assert_frame_equal(a, b)
    assert len(prov.calls) == 1
    svc.bars("SPY", "1d", date(2022, 1, 1), date(2023, 1, 1), "synthetic")  # local data is not cached
    assert len(prov.calls) == 2 and len(svc.disk.entries()) == 1
    assert svc.disk.clear(provider="massive") == 1 and not svc.disk.entries()


def test_a_damaged_cache_file_is_downloaded_again(tmp_path):
    prov, cache = DeepHistory(), BarCache(tmp_path, now=lambda: NOW)
    _cached(cache, prov, date(2024, 1, 1), date(2024, 12, 31))
    cache.path("massive", "SPY", Timeframe.D1).write_bytes(b"not parquet")
    bars = _cached(cache, prov, date(2024, 1, 1), date(2024, 12, 31))
    assert len(prov.calls) == 2 and len(bars) == len(prov._bars(date(2024, 1, 1), date(2024, 12, 31)))


def test_cache_is_off_in_tests_unless_asked(tmp_path, monkeypatch):
    from trades.data import cache as disk

    assert disk.default_root() is None  # conftest sets TRADES_DATA_CACHE=off
    monkeypatch.setenv("TRADES_DATA_CACHE", str(tmp_path / "bars"))
    assert disk.default_root() == tmp_path / "bars"
    assert DataService(SettingsStore(tmp_path / "s.json")).disk is not None
    assert NOW - timedelta(days=1) < NOW  # keep NOW a datetime (used above as a fixed clock)


# --------------------------------------------------- what the backtest-auditor's review caught


def test_cache_catches_a_split_during_a_long_halt(tmp_path):
    """A halt longer than the overlap window, then a split: the edge must still compare real bars."""
    prov, cache = DeepHistory(), BarCache(tmp_path, now=lambda: NOW)
    prov.halt = (date(2024, 6, 3), date(2024, 8, 30))  # three months without a bar
    _cached(cache, prov, date(2022, 1, 1), date(2024, 7, 31))  # ends inside the halt
    prov.split_factor = 10.0  # it comes back after a reverse split, history re-adjusted
    bars = _cached(cache, prov, date(2022, 1, 1), date(2024, 12, 31))
    pd.testing.assert_frame_equal(bars, prov._bars(date(2022, 1, 1), date(2024, 12, 31)))
    assert bars["close"].pct_change().abs().max() < 0.05  # the drift across the halt, not a +900% day


def test_cache_fetches_a_bar_the_vendor_published_late(tmp_path):
    prov, cache = DeepHistory(), BarCache(tmp_path, now=lambda: NOW)
    prov.published_through = date(2026, 10, 14)  # Thursday's bar is not out yet
    first = _cached(cache, prov, date(2026, 1, 1), date(2026, 10, 15))
    assert first.index[-1] == pd.Timestamp("2026-10-14", tz="UTC")
    prov.published_through = None
    again = _cached(cache, prov, date(2026, 1, 1), date(2026, 10, 15))
    assert again.index[-1] == pd.Timestamp("2026-10-15", tz="UTC")  # asked again, not assumed missing


def test_cache_remembers_a_short_history_for_the_day_and_finds_more_after_an_upgrade(tmp_path):
    prov = DeepHistory()
    prov.history_from = date(2024, 10, 17)  # a free plan's two years
    today = BarCache(tmp_path, now=lambda: NOW)
    bars = _cached(today, prov, date(2016, 1, 1), date(2025, 12, 31))
    assert bars.index[0] == pd.Timestamp("2024-10-17", tz="UTC")
    _cached(today, prov, date(2016, 1, 1), date(2025, 12, 31))
    _cached(today, prov, date(2018, 1, 1), date(2025, 6, 30))
    assert len(prov.calls) == 1  # it looked today: reruns today do not ask again

    prov.history_from = None  # the plan is upgraded
    tomorrow = BarCache(tmp_path, now=lambda: NOW + timedelta(days=1))
    deeper = _cached(tomorrow, prov, date(2016, 1, 1), date(2025, 12, 31))
    assert len(prov.calls) == 2 and deeper.index[0] == pd.Timestamp("2016-01-04", tz="UTC")
    pd.testing.assert_frame_equal(deeper, prov._bars(date(2016, 1, 1), date(2025, 12, 31)))


def test_a_holiday_start_counts_as_covered(tmp_path):
    prov, cache = DeepHistory(), BarCache(tmp_path, now=lambda: NOW)
    _cached(cache, prov, date(2016, 1, 4), date(2016, 12, 30))
    _cached(cache, prov, date(2016, 1, 1), date(2016, 12, 30))  # New Year's Day, then a weekend
    assert len(prov.calls) == 1


def test_cache_daily_datetime_end_follows_new_york_dates(tmp_path):
    """An evening end in New York is already the next day in UTC; the providers stop at the New
    York date, and so must the cache."""
    prov, cache = DeepHistory(), BarCache(tmp_path, now=lambda: NOW)
    _cached(cache, prov, date(2024, 1, 1), date(2024, 1, 31))
    end = datetime(2024, 1, 2, 20, 30, tzinfo=NY)  # 01:30 UTC on 3 January
    from_disk = _cached(cache, prov, date(2024, 1, 1), end)
    direct = prov.history("SPY", Timeframe.D1, date(2024, 1, 1), end)
    pd.testing.assert_frame_equal(from_disk, direct)
    assert from_disk.index[-1] == pd.Timestamp("2024-01-02", tz="UTC")


def test_a_cache_that_cannot_be_written_still_answers(tmp_path, monkeypatch):
    prov, cache = DeepHistory(), BarCache(tmp_path, now=lambda: NOW)

    def full_disk(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cache, "_write", full_disk)
    bars = _cached(cache, prov, date(2024, 1, 1), date(2024, 6, 30))
    pd.testing.assert_frame_equal(bars, prov._bars(date(2024, 1, 1), date(2024, 6, 30)))


def test_stored_bars_win_the_overlap_so_old_runs_reproduce(tmp_path):
    from trades.runs import fingerprint

    prov, cache = DeepHistory(), BarCache(tmp_path, now=lambda: NOW)
    before = _cached(cache, prov, date(2023, 1, 1), date(2024, 12, 31))
    prov.volume_factor = 1.01  # the vendor revises volumes; prices are unchanged
    _cached(cache, prov, date(2023, 1, 1), date(2025, 6, 30))
    after = _cached(cache, prov, date(2023, 1, 1), date(2024, 12, 31))
    assert fingerprint(after) == fingerprint(before)


def test_massive_premarket_minute_bar_is_not_the_session_price():
    """At 09:30:10 the latest minute bar is 09:29 (pre-market). It ends at 09:30:00, but it is not a
    regular-session price, so the quote must not be stamped inside the session."""
    from trades.data.calendar import regular_session_date
    from trades.data.massive import _snapshot_quote

    now = datetime(2024, 1, 3, 9, 30, 10, tzinfo=NY)
    pre = datetime(2024, 1, 3, 9, 29, tzinfo=NY)
    snap = {
        "ticker": "SPY",
        "lastTrade": {"p": 471.9, "t": int(datetime(2024, 1, 3, 9, 29, 58, tzinfo=NY).timestamp() * 1e9)},
        "min": {"c": 471.9, "t": int(pre.timestamp() * 1000)},
        "day": {"c": 471.9},
        "prevDay": {"c": 470.0},
        "updated": int(datetime(2024, 1, 3, 9, 29, 59, tzinfo=NY).timestamp() * 1e9),
    }
    q = _snapshot_quote(snap, now.astimezone(timezone.utc))
    assert regular_session_date(q.timestamp) is None
    # A regular-session minute bar (09:30) is used, stamped at its end.
    snap["lastTrade"] = {}
    snap["min"] = {"c": 472.5, "t": int(datetime(2024, 1, 3, 9, 30, tzinfo=NY).timestamp() * 1000)}
    q = _snapshot_quote(snap, datetime(2024, 1, 3, 9, 31, 30, tzinfo=NY).astimezone(timezone.utc))
    assert q.price == 472.5 and q.timestamp == datetime(2024, 1, 3, 9, 31, tzinfo=NY)


def test_massive_end_of_day_quotes_refresh_a_session_published_today():
    now = [datetime(2024, 1, 3, 16, 30, tzinfo=NY)]  # after Wednesday's close
    closes = {"2024-01-03": [101.0], "2024-01-02": [100.0]}
    calls: list[str] = []

    def handler(request):
        if "snapshot" in request.url.path:
            return httpx.Response(403, json={"status": "NOT_AUTHORIZED"})
        day = request.url.path.rsplit("/", 1)[-1]
        calls.append(day)
        return httpx.Response(200, json={"results": [{"T": "SPY", "c": closes[day][-1]}]})

    prov = MassiveProvider(
        "k", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None, clock=lambda: now[0]
    )
    assert prov.quotes(["SPY"])["SPY"].price == 101.0
    closes["2024-01-03"].append(101.5)  # the vendor corrects today's preliminary close
    now[0] = datetime(2024, 1, 3, 16, 50, tzinfo=NY)
    assert prov.quotes(["SPY"])["SPY"].price == 101.5  # today's session is re-read after 15 minutes
    calls.clear()
    now[0] = datetime(2024, 1, 3, 16, 55, tzinfo=NY)
    prov.quotes(["SPY"])
    assert calls == []  # but not on every poll, and past sessions never


# ------------------------------------------------------------- and what its second review caught


def test_an_intraday_file_that_starts_mid_session_is_not_taken_for_the_whole_day(tmp_path):
    prov, cache = DeepHistory(Timeframe.M5), BarCache(tmp_path, now=lambda: NOW)
    end = datetime(2026, 10, 14, 20, 0, tzinfo=NY)
    _cached(cache, prov, datetime(2026, 10, 13, 11, 0, tzinfo=NY), end)  # a chart asked from 11:00
    bars = _cached(cache, prov, datetime(2026, 10, 13, 0, 0, tzinfo=NY), end)  # then from midnight
    assert bars.index[0] == pd.Timestamp(datetime(2026, 10, 13, 9, 30, tzinfo=NY))  # the morning is there
    assert len(prov.calls) == 2  # the left edge was fetched, not assumed


def test_a_rolling_plan_window_keeps_the_stored_history(tmp_path):
    from trades.runs import fingerprint

    prov = DeepHistory()
    prov.history_from = date(2024, 10, 17)
    first = _cached(BarCache(tmp_path, now=lambda: NOW), prov, date(2016, 1, 1), date(2025, 12, 31))
    prov.history_from = date(2024, 10, 18)  # a day later the plan's two years start a day later
    nxt = BarCache(tmp_path, now=lambda: NOW + timedelta(days=1))
    again = _cached(nxt, prov, date(2016, 1, 1), date(2025, 12, 31))
    assert len(prov.calls) == 2  # one look at the left edge, not a whole new download
    assert again.index[0] == first.index[0] and fingerprint(again) == fingerprint(first)
    prov.history_from = date(2024, 12, 2)  # weeks later the window has moved past every bar compared
    later = BarCache(tmp_path, now=lambda: NOW + timedelta(days=40))
    still = _cached(later, prov, date(2016, 1, 1), date(2025, 12, 31))
    assert len(prov.calls) == 3 and fingerprint(still) == fingerprint(first)


def test_massive_fallback_price_is_never_stamped_inside_the_session():
    from trades.data.calendar import regular_session_date
    from trades.data.massive import _snapshot_quote

    snap = {  # 09:30:10, no regular-session trade yet: the day's price so far is pre-market
        "ticker": "SPY",
        "day": {"c": 471.9},
        "prevDay": {"c": 470.0},
        "updated": int(datetime(2024, 1, 3, 9, 30, 5, tzinfo=NY).timestamp() * 1e9),
    }
    q = _snapshot_quote(snap, datetime(2024, 1, 3, 9, 30, 10, tzinfo=NY).astimezone(timezone.utc))
    assert q.price == 471.9 and regular_session_date(q.timestamp) is None


def test_massive_end_of_day_bars_fetched_on_their_own_day_are_read_again():
    now = [datetime(2024, 1, 3, 16, 5, tzinfo=NY)]  # minutes after Wednesday's close
    closes = {"2024-01-03": 101.0, "2024-01-02": 100.0}
    calls: list[str] = []

    def handler(request):
        if "snapshot" in request.url.path:
            return httpx.Response(403, json={"status": "NOT_AUTHORIZED"})
        day = request.url.path.rsplit("/", 1)[-1]
        calls.append(day)
        return httpx.Response(200, json={"results": [{"T": "SPY", "c": closes[day]}]})

    prov = MassiveProvider(
        "k", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None, clock=lambda: now[0]
    )
    assert prov.quotes(["SPY"])["SPY"].price == 101.0  # preliminary
    closes["2024-01-03"] = 101.25  # corrected overnight
    now[0] = datetime(2024, 1, 4, 8, 0, tzinfo=NY)  # the next morning: Wednesday is now the past
    assert prov.quotes(["SPY"])["SPY"].price == 101.25
    calls.clear()
    now[0] = datetime(2024, 1, 4, 9, 0, tzinfo=NY)
    prov.quotes(["SPY"])
    assert calls == []  # fetched on a later date, so final
