"""A parquet cache of downloaded bars, so a research rerun does not download its history again.

Research asks for the same years of bars on every run, and data plans limit requests (Massive's
free plan allows 5 a minute). The cache keeps the *completed* bars a deep-history provider
returned: one parquet file per provider, symbol and timeframe under ``data.cache_dir``
(config/base.yaml; default ``data/cache``, which git ignores).

The rules that keep it honest:

* Only bars from before today (New York) are stored. A bar still forming, or one a vendor may still
  correct, is always fetched fresh.
* Each file records the range its bars vouch for, not the range that was asked for: up to the
  last bar's session (a bar the vendor has not published yet is fetched next time, not assumed
  missing), and from the first bar, unless the provider answered from the first session asked
  for. When a provider has nothing earlier (a plan's history limit, a listing date), the file
  notes that it looked today, so reruns today do not ask again.
* A request inside that range is answered from disk, reading only the rows asked for. A request
  beyond it downloads only the missing edge, plus at least 15 bars already on disk.
* Those bars must match what is on disk. Adjusted prices change after a split (and, where a
  provider adjusts for dividends, after every dividend): when they disagree, the file is replaced
  by one fresh download of the whole range, so two price scales never meet.
* Where they agree, the stored bars win (a vendor's revised volumes do not rewrite history), so a
  rerun reproduces the run's data hash (``runs.data_manifest``) until the provider's adjusted
  history itself changes; `trades runs reproduce` then reports the difference.
* A cache that cannot be written (a full disk) never fails the request: the bars are returned.

The cache needs ``pyarrow`` (``pip install -e ".[cache]"``); without it every request goes to the
provider, as before. ``trades data cache`` lists what is stored and ``trades data clear`` empties
it. After a data plan upgrade the deeper history arrives by itself the next day, or at once after
a clear.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from trades import config
from trades.data.base import BAR_COLUMNS, SymbolNotFound, slice_bars, to_utc_timestamp
from trades.data.calendar import NY, is_trading_day, next_trading_day, session_bounds, trading_days
from trades.data.timeframes import Timeframe

log = logging.getLogger(__name__)

CACHED_PROVIDERS = ("massive", "alpaca")
"""Deep-history providers. Yahoo is left out: it serves weeks, not years, of intraday history and
re-adjusts past prices at every dividend, so a cache of it would mostly hold stale numbers.
Synthetic and CSV data are already local."""

META_KEY = b"trades.cache"
VERSION = 2  # 2: covers what the bars vouch for, not what was asked
ANCHOR_BARS = 15  # each extension re-checks at least this many stored bars
OVERLAP_DAILY = pd.Timedelta(days=21)  # about 15 sessions re-checked when a file is extended
OVERLAP_INTRADAY = pd.Timedelta(days=10)  # at least a week of sessions, whatever the holidays
ONE_NS = pd.Timedelta(1, "ns")

# fetch(start, end) -> bars from the provider, both ends inclusive (dates for daily bars)
Fetch = Callable[[datetime | date, datetime | date], pd.DataFrame]


def available() -> bool:
    try:
        import pyarrow  # noqa: F401, PLC0415 - optional dependency
        import pyarrow.parquet  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def default_root() -> Path | None:
    """Where the cache lives: ``$TRADES_DATA_CACHE``, else ``data.cache_dir`` in config/base.yaml,
    relative to the repository (outside a repository: ``~/.trades/cache``). ``off`` disables it."""
    raw = os.environ.get("TRADES_DATA_CACHE")
    if raw is None:
        try:
            raw = str(config.read_yaml(config.path("config", "base.yaml")).get("data", {}).get("cache_dir") or "")
        except config.ConfigError:
            from trades.settings import trades_home  # noqa: PLC0415 - avoids an import cycle

            return trades_home() / "cache"
    if raw.strip().lower() in ("", "off", "none", "false", "0"):
        return None
    root = Path(raw).expanduser()
    return root if root.is_absolute() else config.repo_root() / root


def _safe_name(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9.\-^=]", "_", symbol.upper())


@dataclass(frozen=True)
class Entry:
    """One cached file, for ``trades data cache``."""

    provider: str
    symbol: str
    timeframe: str
    bars: int
    first: str
    last: str
    covers: tuple[str, str]
    updated: str
    size: int


class BarCache:
    def __init__(self, root: Path | str, now: Callable[[], datetime] | None = None):
        self.root = Path(root)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._locks: dict[Path, threading.Lock] = {}
        self._guard = threading.Lock()

    def path(self, provider: str, symbol: str, timeframe: Timeframe) -> Path:
        return self.root / provider / timeframe.value / f"{_safe_name(symbol)}.parquet"

    def last_complete(self, timeframe: Timeframe) -> pd.Timestamp:
        """The last bar stamp that is surely complete: anything stamped before today (New York).

        Daily bars are stamped 00:00 UTC of their trading date, intraday bars at their start in UTC.
        """
        today = self._now().astimezone(NY).date()
        if timeframe is Timeframe.D1:
            return pd.Timestamp(today).tz_localize("UTC") - ONE_NS
        return pd.Timestamp(datetime.combine(today, time(0), NY)).tz_convert("UTC") - ONE_NS

    def history(
        self,
        provider: str,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | date | None,
        end: datetime | date | None,
        fetch: Fetch,
    ) -> pd.DataFrame:
        """Bars in [start, end], from disk where they are stored and from ``fetch`` where not."""
        if start is None:  # the provider picks its own default span: nothing to look up
            return fetch(start, end)
        lo_req = to_utc_timestamp(start)
        if end is None:
            hi_req = pd.Timestamp(self._now()).tz_convert("UTC")
        elif isinstance(end, datetime):
            hi_req = to_utc_timestamp(end)
            if timeframe is Timeframe.D1:  # a daily bar belongs to its New York date, as at the providers
                end = hi_req = min(hi_req, _end_of_stamp_day(hi_req.tz_convert(NY).date()))
        else:
            hi_req = _end_of_stamp_day(end)
        if hi_req < lo_req:
            return fetch(start, end)
        path = self.path(provider, symbol, timeframe)
        with self._lock(path):
            bars = self._fill(path, provider, symbol, timeframe, lo_req, hi_req, fetch)
        return slice_bars(bars, start, end)

    def _fill(self, path, provider, symbol, timeframe, lo_req, hi_req, fetch) -> pd.DataFrame:
        today = self._now().astimezone(NY).date()
        last_complete = self.last_complete(timeframe)
        meta = self._meta(path)
        if meta is not None and _covered(meta, lo_req, hi_req, today, timeframe):
            bars = self._read(path, meta, lo_req, hi_req)  # only the rows asked for
            if bars is not None:
                return bars
        stored = self._read(path, meta) if meta is not None else None
        overlap = OVERLAP_DAILY if timeframe is Timeframe.D1 else OVERLAP_INTRADAY

        def get(lo: pd.Timestamp, hi: pd.Timestamp, *, edge: bool) -> pd.DataFrame:
            try:
                return fetch(*_bounds(lo, hi, timeframe))
            except SymbolNotFound:
                if not edge:
                    raise
                return stored.iloc[:0]  # no bars at all in this edge: a fact, not an error

        if stored is None or stored.empty:
            bars, asked, meta = get(lo_req, hi_req, edge=False), (lo_req, hi_req), None
        else:
            lo, hi = _covers(meta)
            # Each edge re-reads at least ANCHOR_BARS stored bars, so the check never compares two
            # empty windows (a long halt, then a split, must not slip through).
            k = min(ANCHOR_BARS, len(stored))
            pieces, agree, asked = [stored], True, (lo, hi)
            if lo_req < lo and not _before_first(meta, lo_req, today, timeframe):
                edge_hi = min(max(lo + overlap, stored.index[k - 1]), hi)  # compare only what is stored
                left = get(lo_req, edge_hi, edge=True)
                # A plan's rolling window may no longer serve the oldest stored bars; they stay
                # valid (stored bars win), so compare from the fresh edge's first bar. An empty edge
                # merges nothing, so nothing can disagree.
                agree = left.empty or _agrees(stored, left, max(lo, left.index[0]), edge_hi)
                pieces.append(left)
                asked = (lo_req, asked[1])
            if agree and hi_req > hi:
                edge_lo = max(min(hi - overlap, stored.index[-k]), lo)
                right = get(edge_lo, hi_req, edge=True)
                agree = _agrees(stored, right, edge_lo, hi)
                pieces.append(right)
                asked = (asked[0], hi_req)
            if agree:
                bars = pd.concat(pieces)  # stored first: stored bars win the overlap, so hashes hold
                bars = bars[~bars.index.duplicated(keep="first")].sort_index()
            else:
                log.info("%s %s %s: stored bars no longer match the provider (a split or a new "
                         "adjustment); downloading the whole range again", provider, symbol, timeframe.value)
                asked = (min(lo, lo_req), max(hi, hi_req))
                bars, meta = get(*asked, edge=False), None
        try:
            self._store(path, bars, provider, symbol, timeframe, asked, meta, today, last_complete)
        except Exception as exc:  # noqa: BLE001 - a full disk must not fail a request that succeeded
            log.warning("could not write the bar cache %s (%s)", path, exc)
        return bars

    # ------------------------------------------------------------------------------- files

    def _lock(self, path: Path) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(path, threading.Lock())

    @staticmethod
    def _meta(path: Path) -> dict | None:
        """A file's own record of what it holds (its footer only), or None if there is no usable file."""
        if not path.exists():
            return None
        import pyarrow.parquet as pq  # noqa: PLC0415 - optional dependency

        try:
            meta = json.loads((pq.read_schema(path).metadata or {})[META_KEY])
        except Exception as exc:  # noqa: BLE001 - a damaged or foreign file: start that symbol again
            log.warning("ignoring unreadable cache file %s (%s)", path, exc)
            return None
        return meta if meta.get("version") == VERSION else None

    @staticmethod
    def _read(path: Path, meta: dict, lo: pd.Timestamp | None = None, hi: pd.Timestamp | None = None):
        import pyarrow.parquet as pq  # noqa: PLC0415 - optional dependency

        filters = None
        if lo is not None and hi is not None:
            filters = [("time", ">=", lo.to_pydatetime(warn=False)), ("time", "<=", hi.to_pydatetime(warn=False))]
        try:
            bars = pq.read_table(path, filters=filters).to_pandas()
        except Exception as exc:  # noqa: BLE001
            log.warning("ignoring unreadable cache file %s (%s)", path, exc)
            return None
        index = pd.DatetimeIndex(bars.index).tz_convert("UTC")
        bars.index = index.as_unit(meta.get("unit") or index.unit)  # the provider's own resolution
        bars.index.name = "time"
        return bars[list(BAR_COLUMNS)].astype(float)

    def _store(self, path, bars, provider, symbol, timeframe, asked, old_meta, today, last_complete) -> None:
        """Keep the completed bars, and record exactly what they vouch for."""
        complete = bars[bars.index <= last_complete]
        if complete.empty:
            return  # nothing complete yet (the whole request is today)
        first, last = complete.index[0], complete.index[-1]
        asked_lo, asked_hi = asked
        # From where we asked, if the provider answered from the first session there; otherwise
        # from its first bar (a plan's history limit, a listing date), noting that we looked.
        lo = asked_lo if _starts_at(first, asked_lo, timeframe) else first
        # Up to the end we asked for, but never past today, nor past the last bar's session: a bar
        # the vendor has not published yet is fetched next time instead of assumed missing.
        hi = min(asked_hi, last_complete, _through(last, timeframe))
        if hi < lo:
            return
        probe = None
        if asked_lo < lo:
            probe = {"day": today.isoformat(), "lo": asked_lo.isoformat()}
        elif old_meta and (old_meta.get("probe") or {}).get("day") == today.isoformat():
            probe = old_meta["probe"]
        self._write(path, complete, {
            "provider": provider,
            "symbol": symbol.upper(),
            "timeframe": timeframe.value,
            "covers": [lo.isoformat(), hi.isoformat()],
            "probe": probe,
        })

    def _write(self, path: Path, bars: pd.DataFrame, meta: dict) -> None:
        import pyarrow as pa  # noqa: PLC0415 - optional dependency
        import pyarrow.parquet as pq  # noqa: PLC0415

        meta = {
            "version": VERSION,
            **meta,
            "unit": pd.DatetimeIndex(bars.index).unit,  # parquet has no seconds: give back what came in
            "updated": self._now().astimezone(timezone.utc).isoformat(timespec="seconds"),
        }
        table = pa.Table.from_pandas(bars[list(BAR_COLUMNS)])
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), META_KEY: json.dumps(meta)})
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            pq.write_table(table, tmp, compression="zstd")
            tmp.replace(path)  # atomic: a reader sees the old file or the new one, never half of one
        finally:
            tmp.unlink(missing_ok=True)

    # ---------------------------------------------------------------------------- listing

    def entries(self, provider: str | None = None) -> list[Entry]:
        """What is stored, read from each file's footer and time column (not its prices)."""
        import pyarrow.parquet as pq  # noqa: PLC0415 - optional dependency

        out: list[Entry] = []
        for file in sorted(self.root.glob("*/*/*.parquet")):
            if provider and file.parts[-3] != provider:
                continue
            try:
                meta = json.loads((pq.read_schema(file).metadata or {})[META_KEY])
                times = pq.read_table(file, columns=["time"]).column("time").to_pylist()
            except Exception:  # noqa: BLE001 - listing must not fail on one bad file
                continue
            out.append(
                Entry(
                    provider=meta["provider"],
                    symbol=meta["symbol"],
                    timeframe=meta["timeframe"],
                    bars=len(times),
                    first=pd.Timestamp(times[0]).isoformat() if times else "",
                    last=pd.Timestamp(times[-1]).isoformat() if times else "",
                    covers=(meta["covers"][0], meta["covers"][1]),
                    updated=meta["updated"],
                    size=file.stat().st_size,
                )
            )
        return out

    def clear(self, provider: str | None = None, symbol: str | None = None) -> int:
        """Delete cached files (all, one provider's, or one symbol's); returns how many."""
        removed = 0
        for file in self.root.glob("*/*/*.parquet"):
            if provider and file.parts[-3] != provider:
                continue
            if symbol and file.stem != _safe_name(symbol):
                continue
            file.unlink(missing_ok=True)
            removed += 1
        return removed


def _end_of_stamp_day(d: date) -> pd.Timestamp:
    """The last instant of a day in bar-stamp terms (daily bars are stamped 00:00 UTC of their date)."""
    return pd.Timestamp(d).tz_localize("UTC") + pd.Timedelta(days=1) - ONE_NS


def _covers(meta: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(meta["covers"][0]), pd.Timestamp(meta["covers"][1])


def _probed(meta: dict, lo_req: pd.Timestamp, today: date) -> bool:
    """Did we already ask today from ``lo_req`` or earlier and get nothing before the first bar?"""
    probe = meta.get("probe") or {}
    return probe.get("day") == today.isoformat() and pd.Timestamp(probe["lo"]) <= lo_req


def _before_first(meta: dict, lo_req: pd.Timestamp, today: date, timeframe: Timeframe) -> bool:
    """Is there nothing to fetch before the file's start? For daily bars, a start on the holiday or
    weekend just before it (no bar can exist there); for any bars, a probe made today."""
    lo, _ = _covers(meta)
    # An intraday file may start mid-session (at the hour a chart asked from), so a start earlier
    # that day is not covered; a daily file starts on a whole trading date.
    return (timeframe is Timeframe.D1 and _starts_at(lo, lo_req, timeframe)) or _probed(meta, lo_req, today)


def _covered(meta: dict, lo_req: pd.Timestamp, hi_req: pd.Timestamp, today: date, timeframe: Timeframe) -> bool:
    """Is [lo_req, hi_req] inside what the file vouches for?"""
    lo, hi = _covers(meta)
    return (lo <= lo_req or _before_first(meta, lo_req, today, timeframe)) and hi_req <= hi


def _starts_at(first: pd.Timestamp, asked_lo: pd.Timestamp, timeframe: Timeframe) -> bool:
    """Is the first bar in the first session that could hold one, at or after ``asked_lo``?"""
    if first <= asked_lo:
        return True
    if timeframe is Timeframe.D1:  # stamps are dates
        days = trading_days(asked_lo.date(), first.date() - timedelta(days=1))
        return not days
    start_day = asked_lo.tz_convert(NY).date()
    days = trading_days(start_day, first.tz_convert(NY).date() - timedelta(days=1))
    if days and days[0] == start_day and asked_lo >= session_bounds(start_day)[1]:
        days = days[1:]  # asked from after that day's close
    return not days


def _through(last: pd.Timestamp, timeframe: Timeframe) -> pd.Timestamp:
    """The last instant the bars vouch for: through the last bar's trading day (and the closed days
    after it), if that day is complete in the data; otherwise only up to the last bar."""
    if timeframe is Timeframe.D1:
        return pd.Timestamp(next_trading_day(last.date())).tz_localize("UTC") - ONE_NS
    day = last.tz_convert(NY).date()
    if is_trading_day(day) and last + pd.Timedelta(minutes=timeframe.minutes) >= session_bounds(day)[1]:
        nxt = next_trading_day(day)
        return pd.Timestamp(datetime.combine(nxt, time(0), NY)).tz_convert("UTC") - ONE_NS
    return last


def _bounds(lo: pd.Timestamp, hi: pd.Timestamp, timeframe: Timeframe) -> tuple[datetime | date, datetime | date]:
    """What to hand the provider: trading dates for daily bars, UTC datetimes for intraday."""
    if timeframe is Timeframe.D1:
        return lo.date(), hi.date()
    return lo.to_pydatetime(), hi.to_pydatetime()


def _agrees(stored: pd.DataFrame, fresh: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> bool:
    """Do the stored and fresh bars in [lo, hi] match: the same bars at the same prices?"""
    a = stored[(stored.index >= lo) & (stored.index <= hi)]
    b = fresh[(fresh.index >= lo) & (fresh.index <= hi)]
    if a.empty or not a.index.equals(b.index):
        return False
    cols = ["open", "high", "low", "close"]
    return bool(np.allclose(a[cols].to_numpy(), b[cols].to_numpy(), rtol=1e-6, atol=1e-9))
