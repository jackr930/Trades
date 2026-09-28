"""NYSE trading calendar and market-status helpers (no third-party dependency).

Holiday rules follow NYSE Rule 7.2: a holiday falling on a Saturday is observed the
preceding Friday (except New Year's Day, which is then not observed at all because the
Friday closes a reporting period); a holiday falling on a Sunday is observed the
following Monday. Early (13:00 ET) closes: July 3 (when it is Mon-Thu), the day after
Thanksgiving, and Christmas Eve (when it is a weekday).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
PRE_MARKET_OPEN = time(4, 0)
POST_MARKET_CLOSE = time(20, 0)

# Unscheduled closures (national days of mourning, weather, 9/11).
SPECIAL_CLOSURES = frozenset(
    {
        date(2001, 9, 11),
        date(2001, 9, 12),
        date(2001, 9, 13),
        date(2001, 9, 14),
        date(2004, 6, 11),  # President Reagan
        date(2007, 1, 2),  # President Ford
        date(2012, 10, 29),
        date(2012, 10, 30),  # Hurricane Sandy
        date(2018, 12, 5),  # President G.H.W. Bush
        date(2025, 1, 9),  # President Carter
    }
)


def easter_sunday(year: int) -> date:
    """Gregorian Easter (anonymous Gregorian / Meeus-Jones-Butcher algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=256)
def nyse_holidays(year: int) -> frozenset[date]:
    days: set[date] = set()
    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:
        days.add(new_year + timedelta(days=1))
    elif new_year.weekday() < 5:
        days.add(new_year)
    if year >= 1998:
        days.add(_nth_weekday(year, 1, 0, 3))  # Martin Luther King Jr. Day
    days.add(_nth_weekday(year, 2, 0, 3))  # Washington's Birthday
    days.add(easter_sunday(year) - timedelta(days=2))  # Good Friday
    days.add(_last_weekday(year, 5, 0))  # Memorial Day
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    days.add(_observed(date(year, 7, 4)))  # Independence Day
    days.add(_nth_weekday(year, 9, 0, 1))  # Labor Day
    days.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    days.add(_observed(date(year, 12, 25)))  # Christmas
    days.update(d for d in SPECIAL_CLOSURES if d.year == year)
    return frozenset(days)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def is_early_close(d: date) -> bool:
    if not is_trading_day(d):
        return False
    july3 = date(d.year, 7, 3)
    if d == july3 and d.weekday() <= 3:
        return True
    if d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1):
        return True
    return d == date(d.year, 12, 24)


def trading_days(start: date, end: date) -> list[date]:
    """All NYSE trading days in [start, end] inclusive."""
    out: list[date] = []
    d = start
    one = timedelta(days=1)
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += one
    return out


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def session_bounds(d: date) -> tuple[datetime, datetime]:
    """Regular-session open/close for a trading day, as timezone-aware NY datetimes."""
    close = EARLY_CLOSE if is_early_close(d) else REGULAR_CLOSE
    return datetime.combine(d, REGULAR_OPEN, NY), datetime.combine(d, close, NY)


@dataclass(frozen=True)
class MarketStatus:
    is_open: bool
    phase: str  # "regular" | "pre" | "post" | "closed"
    now: datetime
    session_date: date  # the trading day that is live, or the most recent completed one
    next_open: datetime
    next_close: datetime

    def to_dict(self) -> dict:
        return {
            "is_open": self.is_open,
            "phase": self.phase,
            "now": self.now.astimezone(timezone.utc).isoformat(),
            "session_date": self.session_date.isoformat(),
            "next_open": self.next_open.astimezone(timezone.utc).isoformat(),
            "next_close": self.next_close.astimezone(timezone.utc).isoformat(),
        }


def market_status(now: datetime | None = None) -> MarketStatus:
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    today = now.date()
    if is_trading_day(today):
        open_dt, close_dt = session_bounds(today)
        if open_dt <= now < close_dt:
            return MarketStatus(True, "regular", now, today, open_dt, close_dt)
        if now < open_dt:
            phase = "pre" if now.time() >= PRE_MARKET_OPEN else "closed"
            return MarketStatus(False, phase, now, previous_trading_day(today), open_dt, close_dt)
        phase = "post" if now.time() < POST_MARKET_CLOSE else "closed"
        nxt = next_trading_day(today)
        n_open, n_close = session_bounds(nxt)
        return MarketStatus(False, phase, now, today, n_open, n_close)
    nxt = next_trading_day(today)
    n_open, n_close = session_bounds(nxt)
    return MarketStatus(False, "closed", now, previous_trading_day(today), n_open, n_close)


def last_completed_session(now: datetime | None = None) -> date:
    """Most recent trading day whose regular session has fully closed."""
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    today = now.date()
    if is_trading_day(today) and now >= session_bounds(today)[1]:
        return today
    return previous_trading_day(today)
