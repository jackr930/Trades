"""Back up and restore what the app keeps in ``$TRADES_HOME``, never including API keys.

A hosted copy without a persistent disk forgets everything when it restarts. A backup is
one JSON file: the settings (keys left out) plus the research log, which the Deflated
Sharpe Ratio needs to count every configuration you have tried, and the simulator
history. Restoring merges the logs line by line, so nothing already recorded is lost.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trades import __version__
from trades.config import SECRET_FIELDS, SettingsStore

FORMAT = "trades-backup"
VERSION = 1


def make_backup(store: SettingsStore, files: dict[str, Path | None]) -> dict[str, Any]:
    settings = {k: v for k, v in asdict(store.get()).items() if k not in SECRET_FIELDS}
    return {
        "format": FORMAT,
        "version": VERSION,
        "app_version": __version__,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": settings,
        "files": {name: path.read_text() if path and path.exists() else "" for name, path in files.items()},
    }


def _json_lines(text: str) -> list[str]:
    out = []
    for line in str(text).splitlines():
        try:
            if isinstance(json.loads(line), dict):
                out.append(line)
        except json.JSONDecodeError:
            continue
    return out


def restore_backup(store: SettingsStore, files: dict[str, Path | None], backup: dict[str, Any]) -> dict[str, Any]:
    """Apply ``backup``'s settings (validated, keys ignored) and add its log lines not already present."""
    if backup.get("format") != FORMAT or not isinstance(backup.get("settings"), dict):
        raise ValueError("this is not a Trades backup file")
    if int(backup.get("version", 0)) > VERSION:
        raise ValueError("this backup comes from a newer version of Trades; update the app first")
    settings = {k: v for k, v in backup["settings"].items() if k not in SECRET_FIELDS}
    store.update(settings)
    added = {}
    for name, text in dict(backup.get("files") or {}).items():
        path = files.get(name)
        if path is None:
            continue
        current = path.read_text() if path.exists() else ""
        seen = set(current.splitlines())
        new = []
        for line in _json_lines(text):
            if line not in seen:
                seen.add(line)
                new.append(line)
        if new:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as fh:
                if current and not current.endswith("\n"):
                    fh.write("\n")
                fh.write("\n".join(new) + "\n")
        added[name] = len(new)
    return {"settings": sorted(settings), "lines_added": added}
