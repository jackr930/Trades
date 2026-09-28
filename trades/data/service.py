"""Provider registry plus a small TTL cache in front of provider requests."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from trades.config import Settings, SettingsStore
from trades.core.timeframes import Timeframe
from trades.data.alpaca import AlpacaProvider
from trades.data.base import DataError, DataProvider, ProviderNotConfigured, Quote, lookback_start
from trades.data.csv_provider import CSVProvider
from trades.data.synthetic import SyntheticProvider
from trades.data.yahoo import YahooProvider

PROVIDER_CLASSES: dict[str, type[DataProvider]] = {
    "synthetic": SyntheticProvider,
    "yahoo": YahooProvider,
    "alpaca": AlpacaProvider,
    "csv": CSVProvider,
}


def build_provider(provider_id: str, settings: Settings) -> DataProvider:
    if provider_id == "synthetic":
        return SyntheticProvider(seed=settings.synthetic_seed)
    if provider_id == "yahoo":
        return YahooProvider()
    if provider_id == "alpaca":
        key, secret = settings.alpaca_credentials()
        return AlpacaProvider(key, secret, feed=settings.alpaca_feed)
    if provider_id == "csv":
        return CSVProvider(settings.resolved_csv_dir())
    raise DataError(f"unknown data provider {provider_id!r}")


def _provider_signature(provider_id: str, s: Settings) -> tuple:
    if provider_id == "alpaca":
        return (*s.alpaca_credentials(), s.alpaca_feed)
    if provider_id == "csv":
        return (str(s.resolved_csv_dir()),)
    if provider_id == "synthetic":
        return (s.synthetic_seed,)
    return ()


class TTLCache:
    def __init__(self, maxsize: int = 512):
        self.maxsize = maxsize
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < time.monotonic():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key, value, ttl: float) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class DataService:
    """Hands out provider instances (rebuilt when their settings change) and caches bars."""

    def __init__(self, settings: SettingsStore):
        self.settings = settings
        self._providers: dict[str, tuple[tuple, DataProvider]] = {}
        self._lock = threading.Lock()
        self.cache = TTLCache()
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="data")

    def provider(self, provider_id: str | None = None) -> DataProvider:
        s = self.settings.get()
        pid = provider_id or s.provider
        if pid not in PROVIDER_CLASSES:
            raise DataError(f"unknown data provider {pid!r}")
        sig = _provider_signature(pid, s)
        with self._lock:
            cached = self._providers.get(pid)
            if cached and cached[0] == sig:
                return cached[1]
            prov = build_provider(pid, s)
            self._providers[pid] = (sig, prov)
            return prov

    def catalog(self) -> list[dict]:
        s = self.settings.get()
        out = []
        for pid, cls in PROVIDER_CLASSES.items():
            info = cls.info.to_dict()
            configured = True
            if pid == "alpaca":
                configured = all(s.alpaca_credentials())
            info["configured"] = configured
            info["active"] = pid == s.provider
            out.append(info)
        return out

    def bars(
        self,
        symbol: str,
        timeframe: str | Timeframe = Timeframe.D1,
        start: datetime | date | str | None = None,
        end: datetime | date | str | None = None,
        provider_id: str | None = None,
        *,
        count: int | None = None,
    ) -> pd.DataFrame:
        """Bars for ``symbol``. With ``count`` and no ``start``, the most recent ``count`` bars."""
        tf = Timeframe.parse(timeframe)
        pid = provider_id or self.settings.get().provider
        start = _coerce(start)
        end = _coerce(end)
        if count and start is None:
            ls = lookback_start(tf, count)
            start = ls.date() if tf is Timeframe.D1 else ls.replace(minute=0, second=0, microsecond=0)
        key = ("bars", pid, symbol.upper(), tf.value, str(start), str(end))
        df = self.cache.get(key)
        if df is None:
            df = self.provider(pid).history(symbol.upper(), tf, start, end)
            self.cache.set(key, df, self._ttl(pid, tf, end))
        if count:
            df = df.iloc[-count:]
        return df.copy()

    def bars_many(
        self,
        symbols: list[str],
        timeframe=Timeframe.D1,
        start=None,
        end=None,
        provider_id=None,
        *,
        count: int | None = None,
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        """Fetch several symbols concurrently. Returns (frames, errors by symbol)."""
        futures = {
            sym: self._pool.submit(self.bars, sym, timeframe, start, end, provider_id, count=count)
            for sym in dict.fromkeys(s.upper() for s in symbols)
        }
        frames, errors = {}, {}
        for sym, fut in futures.items():
            try:
                frames[sym] = fut.result()
            except ProviderNotConfigured:
                raise
            except DataError as exc:
                errors[sym] = str(exc)
            except Exception as exc:  # pragma: no cover - unexpected provider failure
                errors[sym] = f"{type(exc).__name__}: {exc}"
        return frames, errors

    def quotes(self, symbols: list[str], provider_id: str | None = None) -> dict[str, Quote]:
        return self.provider(provider_id).quotes([s.upper() for s in symbols])

    def invalidate(self) -> None:
        self.cache.clear()

    @staticmethod
    def _ttl(pid: str, tf: Timeframe, end) -> float:
        if pid in ("synthetic", "csv"):
            return 600.0
        if end is not None:
            end_d = end.date() if isinstance(end, datetime) else end
            if isinstance(end_d, date) and end_d < datetime.now(timezone.utc).date() - timedelta(days=1):
                return 6 * 3600.0
        return 300.0 if tf is Timeframe.D1 else 30.0


def _coerce(value):
    if value is None or value == "":
        return None
    if isinstance(value, (date, datetime)):
        return value
    ts = pd.Timestamp(value)
    if len(str(value)) <= 10:
        return ts.date()
    return ts.to_pydatetime()
