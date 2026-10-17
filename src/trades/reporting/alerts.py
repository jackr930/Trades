"""Alerts from the paper trader: a Telegram message when something needs a person.

Which events alert is set in config/paper.yaml (``alerts.on``): errors, order rejections, risk-limit
hits, the kill switch, data gaps and the daily report. The bot token and chat id come from the
environment (``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_CHAT_ID``, in the env file on the trading machine,
never in the repository). Without them, or with ``alerts.telegram: false``, alerts are only logged.

A missed heartbeat cannot be reported by the process that died, so the runner also pings
``TRADES_HEARTBEAT_URL`` (a dead-man's-switch service such as healthchecks.io) if it is set: that
service alerts you when the pings stop.

Sending never raises: an alert that fails is logged, and trading carries on.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx

from trades import config

KINDS = ("error", "rejection", "risk_limit", "halt", "data_gap", "missed_heartbeat", "daily_report")
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


class Alerts:
    """``alerts(kind, message)``: log it, and send it to Telegram if that kind is switched on.

    The same kind is sent at most once per ``quiet_minutes`` (a flapping data feed must not flood
    your phone); the daily report and the kill switch always go out.
    """

    def __init__(
        self,
        kinds: tuple[str, ...] | list[str] = KINDS,
        telegram: bool = False,
        token: str | None = None,
        chat_id: str | None = None,
        *,
        prefix: str = "",
        quiet_minutes: float = 10.0,
        post: Callable[..., httpx.Response] | None = None,
        log: Callable[[str], None] = print,
    ):
        self.kinds = set(kinds)
        self.telegram = bool(telegram and token and chat_id)
        self.token, self.chat_id = token, chat_id
        self.prefix = prefix
        self.quiet = timedelta(minutes=quiet_minutes)
        self._post = post or (lambda url, **kw: httpx.post(url, timeout=10.0, **kw))
        self._log = log
        self._last: dict[str, datetime] = {}
        self.sent: list[tuple[str, str]] = []  # what went out (for tests and the daily report)

    @classmethod
    def from_config(cls, prefix: str = "", log: Callable[[str], None] = print) -> Alerts:
        section = config.section("paper", "alerts")
        return cls(
            kinds=tuple(section.get("on", KINDS)),
            telegram=bool(section.get("telegram", False)),
            token=os.environ.get("TELEGRAM_BOT_TOKEN"),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
            prefix=prefix,
            log=log,
        )

    def __call__(self, kind: str, message: str, now: datetime | None = None) -> bool:
        """Returns True if the alert was sent to Telegram."""
        now = now or datetime.now(timezone.utc)
        self._log(f"ALERT [{kind}] {message}")
        if kind not in self.kinds or not self.telegram:
            return False
        last = self._last.get(kind)
        if kind not in ("daily_report", "halt") and last is not None and now - last < self.quiet:
            return False
        self._last[kind] = now
        text = f"{self.prefix}{kind.replace('_', ' ').upper()}: {message}"
        try:
            resp = self._post(TELEGRAM_URL.format(token=self.token), json={"chat_id": self.chat_id, "text": text[:4000]})
            ok = resp.status_code < 300
        except httpx.HTTPError as exc:
            self._log(f"  (the Telegram alert failed: {exc})")
            return False
        if not ok:
            self._log(f"  (Telegram refused the alert: {resp.status_code})")
            return False
        self.sent.append((kind, text))
        return True


def ping(url: str | None, log: Callable[[str], None] = print) -> bool:
    """One heartbeat to a dead-man's-switch URL; True if it answered."""
    if not url:
        return False
    try:
        return httpx.get(url, timeout=5.0).status_code < 300
    except httpx.HTTPError as exc:
        log(f"  (heartbeat ping failed: {exc})")
        return False
