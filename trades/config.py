"""User settings, persisted as JSON in ``$TRADES_HOME/settings.json`` (default ``~/.trades``).

API keys never leave this machine: they are stored locally (file mode 0600), sent only
to the data provider they belong to, and masked whenever settings are returned by the API.
Environment variables ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY`` override stored keys.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

SECRET_FIELDS = ("alpaca_key_id", "alpaca_secret_key")
MASK_PREFIX = "****"

DEFAULT_WATCHLISTS: dict[str, list[str]] = {
    "synthetic": ["SIMIDX", "SIMTEC", "SIMBNK", "SIMNRG", "SIMUTL", "SIMHLC", "SIMBND", "SIMGLD"],
    "yahoo": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "JPM", "XOM", "TLT"],
    "alpaca": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "JPM", "XOM", "TLT"],
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


def trades_home() -> Path:
    return Path(os.environ.get("TRADES_HOME", Path.home() / ".trades")).expanduser()


@dataclass
class Settings:
    provider: str = "synthetic"
    timeframe: str = "1d"
    watchlists: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_WATCHLISTS.items()}
    )
    advisors: list[dict[str, Any]] = field(default_factory=lambda: [dict(a) for a in DEFAULT_ADVISORS])
    pairs: list[list[str]] = field(default_factory=lambda: [list(p) for p in DEFAULT_PAIRS])  # for pairs trading
    consensus_weighting: str = "equal"  # "equal": every vote counts once; "by_category": categories count once
    alpaca_key_id: str = ""
    alpaca_secret_key: str = ""
    alpaca_feed: str = "iex"
    csv_dir: str = ""
    synthetic_seed: int = 7
    # Account profile: the default for the Lab, the simulators and the Live Desk.
    account_equity: float = 100_000.0  # starting equity
    fractional_shares: bool = False
    account_type: str = "taxable"  # taxable | tax_advantaged (IRA, 401(k): no tax as you go)
    short_term_tax_rate: float = 0.22  # estimates: replace with your own federal bracket
    long_term_tax_rate: float = 0.15
    risk_per_trade: float = 0.01  # fraction of equity lost if the protective stop is hit
    stop_atr: float = 2.0  # protective stop distance in ATRs
    max_position_pct: float = 0.20  # cap on any single position, fraction of equity
    max_gross_exposure: float = 1.0  # cap on all suggested positions together (1.0 = 100% of equity)
    allow_short: bool = False
    commission_bps: float = 0.0
    slippage_bps: float = 5.0
    # Paper trading (Alpaca's paper API only; see trades.paper)
    paper_halted: bool = False  # kill switch: `trades paper halt` / `trades paper resume`
    paper_max_daily_loss: float = 0.03  # no orders after the paper account lost more since the prior close
    paper_max_orders: int = 20  # per run
    poll_seconds: float = 15.0
    demo_speed: float = 60.0  # synthetic live clock: simulated seconds per real second

    def tax_profile(self):
        from trades.backtest.tax import TaxProfile

        return TaxProfile(self.account_type, self.short_term_tax_rate, self.long_term_tax_rate)

    def watchlist(self, provider: str | None = None) -> list[str]:
        pid = provider or self.provider
        return list(self.watchlists.get(pid, DEFAULT_WATCHLISTS.get(pid, [])))

    def active_pairs(self, symbols: list[str]) -> list[tuple[str, str]]:
        """Configured pairs whose two legs are both in ``symbols``."""
        have = {s.upper() for s in symbols}
        return [
            (a.upper(), b.upper())
            for a, b in (p for p in self.pairs if len(p) == 2)
            if a.upper() in have and b.upper() in have
        ]

    def resolved_csv_dir(self) -> Path:
        return Path(self.csv_dir).expanduser() if self.csv_dir else trades_home() / "data"

    def alpaca_credentials(self) -> tuple[str, str]:
        key = os.environ.get("APCA_API_KEY_ID") or self.alpaca_key_id
        secret = os.environ.get("APCA_API_SECRET_KEY") or self.alpaca_secret_key
        return key, secret

    def public_dict(self) -> dict[str, Any]:
        """Settings safe to send to the browser (secrets masked)."""
        data = asdict(self)
        key, secret = self.alpaca_credentials()
        for name, value in (("alpaca_key_id", key), ("alpaca_secret_key", secret)):
            data[name] = mask(value)
        data["has_alpaca_credentials"] = bool(key and secret)
        data["csv_dir_resolved"] = str(self.resolved_csv_dir())
        return data


def mask(value: str) -> str:
    if not value:
        return ""
    return MASK_PREFIX + value[-4:] if len(value) > 4 else MASK_PREFIX


_VALIDATORS = {
    "provider": lambda v: v in ("synthetic", "yahoo", "alpaca", "csv"),
    "timeframe": lambda v: v in ("1m", "5m", "15m", "1h", "1d"),
    "alpaca_feed": lambda v: v in ("iex", "sip", "delayed_sip"),
    "account_equity": lambda v: 100 <= float(v) <= 1e10,
    "account_type": lambda v: v in ("taxable", "tax_advantaged"),
    "short_term_tax_rate": lambda v: 0 <= float(v) <= 0.6,
    "long_term_tax_rate": lambda v: 0 <= float(v) <= 0.6,
    "risk_per_trade": lambda v: 0 < float(v) <= 0.1,
    "stop_atr": lambda v: 0.25 <= float(v) <= 10,
    "max_position_pct": lambda v: 0 < float(v) <= 1.0,
    "max_gross_exposure": lambda v: 0.1 <= float(v) <= 2.0,
    "consensus_weighting": lambda v: v in WEIGHTINGS,
    "commission_bps": lambda v: 0 <= float(v) <= 100,
    "slippage_bps": lambda v: 0 <= float(v) <= 200,
    "paper_max_daily_loss": lambda v: 0.001 <= float(v) <= 0.5,
    "paper_max_orders": lambda v: 1 <= int(v) <= 200,
    "poll_seconds": lambda v: 2 <= float(v) <= 3600,
    "demo_speed": lambda v: 1 <= float(v) <= 3600,
}


def _typed(key: str, value: Any) -> Any:
    """``value`` as the setting's own type: "100" becomes 100.0 for a number, but a string is
    never a yes/no setting (bool("false") is True), and a list is never a number."""
    kind = type(getattr(Settings(), key))
    if kind is bool:
        if not isinstance(value, bool):
            raise ValueError(f"invalid value for {key}: {value!r} (expected true or false)")
        return value
    if kind in (int, float):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"invalid value for {key}: {value!r}")
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(f"invalid value for {key}: {value!r}") from exc
        if kind is int:
            if number != int(number):
                raise ValueError(f"invalid value for {key}: {value!r} (expected a whole number)")
            return int(number)
        return number
    if kind is str and not isinstance(value, str):
        raise ValueError(f"invalid value for {key}: {value!r}")
    if kind in (list, dict) and not isinstance(value, kind):
        raise ValueError(f"invalid value for {key}: {value!r}")
    return value


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or trades_home() / "settings.json"
        self._lock = threading.RLock()  # update() calls get() while holding the lock
        self._settings = self._load()

    def _load(self) -> Settings:
        s = Settings()
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError):
                raw = {}
            known = {f.name for f in fields(Settings)}
            for k, v in raw.items():
                if k in known:
                    setattr(s, k, v)
            for pid, wl in DEFAULT_WATCHLISTS.items():
                s.watchlists.setdefault(pid, list(wl))
        env_provider = os.environ.get("TRADES_PROVIDER")
        if env_provider and _VALIDATORS["provider"](env_provider):
            s.provider = env_provider
        return s

    def get(self) -> Settings:
        with self._lock:
            return Settings(**json.loads(json.dumps(asdict(self._settings))))

    def update(self, patch: dict[str, Any]) -> Settings:
        with self._lock:
            current = asdict(self._settings)
            known = {f.name for f in fields(Settings)}
            for key, value in patch.items():
                if key not in known:
                    continue
                if key in SECRET_FIELDS and (value is None or str(value).startswith(MASK_PREFIX)):
                    continue  # unchanged masked value echoed back by the UI
                value = _typed(key, value)
                if key in _VALIDATORS and not _VALIDATORS[key](value):
                    raise ValueError(f"invalid value for {key}: {value!r}")
                if key == "pairs":
                    value = [[str(a).strip().upper(), str(b).strip().upper()] for a, b in value]
                if key == "watchlists":
                    value = {
                        str(pid): _clean_symbols(syms)
                        for pid, syms in dict(value).items()
                        if isinstance(syms, list)
                    }
                    merged = dict(current["watchlists"])
                    merged.update(value)
                    value = merged
                current[key] = value
            self._settings = Settings(**current)
            self._save()
            return self.get()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self._settings), indent=2))
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - e.g. some Windows filesystems
            pass
        tmp.replace(self.path)


def _clean_symbols(symbols: list[Any]) -> list[str]:
    out: list[str] = []
    for s in symbols:
        sym = str(s).strip().upper()
        if sym and len(sym) <= 15 and all(ch.isalnum() or ch in ".-^=" for ch in sym) and sym not in out:
            out.append(sym)
    return out[:50]
