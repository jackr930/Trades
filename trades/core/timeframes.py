"""Bar timeframes and the annualisation constants that go with them."""

from __future__ import annotations

from enum import Enum

TRADING_DAYS_PER_YEAR = 252
# Regular US equity session: 09:30-16:00 ET = 390 minutes.
SESSION_MINUTES = 390


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"

    @property
    def minutes(self) -> int:
        return {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": SESSION_MINUTES}[self.value]

    @property
    def is_intraday(self) -> bool:
        return self is not Timeframe.D1

    @property
    def bars_per_day(self) -> int:
        """Bars in one regular session (hourly bars: 09:30, 10:30 ... 15:30 = 7 bars)."""
        if self is Timeframe.D1:
            return 1
        return -(-SESSION_MINUTES // self.minutes)  # ceil division

    @property
    def periods_per_year(self) -> int:
        return TRADING_DAYS_PER_YEAR * self.bars_per_day

    @property
    def pandas_freq(self) -> str:
        return {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "1d": "1D"}[self.value]

    @property
    def label(self) -> str:
        return {"1m": "1 minute", "5m": "5 minutes", "15m": "15 minutes", "1h": "1 hour", "1d": "Daily"}[
            self.value
        ]

    @classmethod
    def parse(cls, value: str | Timeframe) -> Timeframe:
        if isinstance(value, Timeframe):
            return value
        key = str(value).strip().lower()
        aliases = {
            "1min": "1m",
            "1minute": "1m",
            "minute": "1m",
            "5min": "5m",
            "15min": "15m",
            "60m": "1h",
            "1hour": "1h",
            "hour": "1h",
            "60min": "1h",
            "d": "1d",
            "1day": "1d",
            "day": "1d",
            "daily": "1d",
        }
        key = aliases.get(key, key)
        try:
            return cls(key)
        except ValueError as exc:
            valid = ", ".join(t.value for t in cls)
            raise ValueError(f"Unknown timeframe {value!r}; expected one of {valid}") from exc
