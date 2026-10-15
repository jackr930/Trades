"""Where the app reads the forward track record from.

``trades journal score`` writes ``journal/track_record.json`` next to the report, and the
journal workflow commits it every evening. The app shows that file, read from

* the local ``journal/`` folder (the default: right when you run the app from a clone that
  you keep up to date with ``git pull``), or
* the repository on GitHub, when the ``journal_source`` setting is a
  ``https://raw.githubusercontent.com/<owner>/<repo>/<branch>/journal`` address. Use this
  for a hosted copy: its image is built once and never sees the workflow's later commits.
  For a private repository, set ``TRADES_JOURNAL_TOKEN`` to a read-only GitHub token.

Reads from GitHub are cached for ten minutes.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from trades.config import GITHUB_RAW, valid_journal_source
from trades.journal.experiment import JOURNAL_DIR

TRACK_FILE = "track_record.json"
CACHE_SECONDS = 600

_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def load_track_record(source: str = "", folder: Path | None = None) -> dict[str, Any] | None:
    """The committed ``track_record.json``, or None if there is none yet."""
    if not source:
        path = Path(folder or JOURNAL_DIR) / TRACK_FILE
        return json.loads(path.read_text()) if path.exists() else None
    if not valid_journal_source(source):
        raise ValueError(f"journal source must start with {GITHUB_RAW}")
    url = source.rstrip("/") + "/" + TRACK_FILE
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    token = os.environ.get("TRADES_JOURNAL_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.get(url, headers=headers, timeout=15, follow_redirects=False)
    if r.status_code == 404:
        record = None
    else:
        r.raise_for_status()
        record = r.json()
    _cache[url] = (time.monotonic(), record)
    return record
