"""Load your own OHLCV data from CSV files (``<SYMBOL>.csv`` in a data folder)."""

from __future__ import annotations

from datetime import timezone
from pathlib import Path

import pandas as pd

from trades.core.timeframes import Timeframe
from trades.data.base import (
    DataError,
    DataProvider,
    ProviderInfo,
    Quote,
    SymbolNotFound,
    normalize_bars,
    slice_bars,
)

DATE_COLUMNS = ("date", "datetime", "time", "timestamp")


def read_ohlcv_csv(path: Path | str, timeframe: Timeframe = Timeframe.D1) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    date_col = next((cols[c] for c in DATE_COLUMNS if c in cols), None)
    if date_col is None:
        raise DataError(f"{path}: needs a Date/Datetime column")
    idx = pd.to_datetime(df[date_col], utc=timeframe.is_intraday)
    df = df.drop(columns=[date_col])
    df.index = pd.DatetimeIndex(idx)
    return normalize_bars(df, timeframe)


class CSVProvider(DataProvider):
    info = ProviderInfo(
        id="csv",
        label="CSV files",
        description=(
            "Your own data: put one file per symbol named SYMBOL.csv with columns "
            "Date, Open, High, Low, Close, Volume (optional Adj Close) in the data folder."
        ),
        requires_key=False,
        realtime="static files",
        timeframes=(Timeframe.D1,),
    )

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    def _path(self, symbol: str) -> Path:
        if not self.directory.exists():
            raise SymbolNotFound(f"CSV folder {self.directory} does not exist")
        for p in self.directory.glob("*.csv"):
            if p.stem.upper() == symbol.upper():
                return p
        raise SymbolNotFound(f"No {symbol}.csv in {self.directory}")

    def symbols(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(p.stem.upper() for p in self.directory.glob("*.csv"))

    def history(self, symbol, timeframe=Timeframe.D1, start=None, end=None) -> pd.DataFrame:
        timeframe = Timeframe.parse(timeframe)
        return slice_bars(read_ohlcv_csv(self._path(symbol), timeframe), start, end)

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out = {}
        for sym in symbols:
            try:
                df = self.history(sym)
            except DataError:
                continue
            if df.empty:
                continue
            last = df.iloc[-1]
            prev_close = float(df["close"].iloc[-2]) if len(df) > 1 else None
            out[sym] = Quote(
                symbol=sym,
                price=float(last["close"]),
                timestamp=df.index[-1].to_pydatetime().astimezone(timezone.utc),
                prev_close=prev_close,
                open=float(last["open"]),
                high=float(last["high"]),
                low=float(last["low"]),
                volume=float(last["volume"]),
                source="csv",
            )
        return out

    def check(self) -> tuple[bool, str]:
        syms = self.symbols()
        if not syms:
            return False, f"No CSV files found in {self.directory}"
        return True, f"{len(syms)} symbols: {', '.join(syms[:10])}{'...' if len(syms) > 10 else ''}"
