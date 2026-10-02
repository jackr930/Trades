"""Forward journal: recording after the close, idempotency, stale data, and hand-checked scoring."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from trades.core.calendar import trading_days
from trades.data.base import DataError
from trades.data.synthetic import SyntheticProvider
from trades.journal.experiment import Experiment, code_version, experiment_id
from trades.journal.recorder import COLUMNS, StaleData, record
from trades.journal.scorer import by_label, by_strategy, outcomes, render_report

SESSION = date(2026, 9, 25)  # a Friday
AFTER_CLOSE = datetime(2026, 9, 25, 22, 15, tzinfo=timezone.utc)  # 18:15 New York time


def _experiment(tmp_path, providers=("synthetic",)) -> Experiment:
    definition = {
        "watchlist": ["SIMIDX", "SIMTEC", "SIMBNK"],
        "benchmark": "SIMIDX",
        "advisors": [{"id": "tsmom"}, {"id": "faber_trend"}, {"id": "rsi2_reversion"}],
        "pairs": [],
        "weighting": "equal",
        "history_start": "2015-01-02",
        "providers": list(providers),
    }
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(definition))
    return Experiment.load(path)


def _synthetic_fetch(end: date, calls: list | None = None):
    provider = SyntheticProvider(seed=7, end=end)

    def fetch(pid, symbols, start):
        if calls is not None:
            calls.append(pid)
        if pid != "synthetic":
            raise DataError(f"{pid} is down")
        return {s: provider.history(s, start=start) for s in symbols}, {}

    return fetch


def _rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def test_record_after_the_close_is_idempotent(tmp_path):
    exp, path = _experiment(tmp_path), tmp_path / "journal.csv"
    assert record(exp, _synthetic_fetch(SESSION), path, now=AFTER_CLOSE, log=lambda m: None) == 3
    rows = _rows(path)
    assert tuple(rows[0]) == COLUMNS and len(rows) == 3
    r = rows[0]
    assert r["session_date"] == "2026-09-25" and r["experiment_id"] == exp.id and r["provider"] == "synthetic"
    assert r["run_time_utc"] == "2026-09-25T22:15:00Z" and r["code_version"] == code_version()
    votes = json.loads(r["votes"])
    assert set(votes) == {"tsmom", "faber_trend", "rsi2_reversion"} and set(votes.values()) <= {-1, 0, 1, None}
    if r["label"] == "Neutral":
        assert float(r["weight"]) == 0.0
    # A second run on the same session adds nothing.
    assert record(exp, _synthetic_fetch(SESSION), path, now=AFTER_CLOSE, log=lambda m: None) == 0
    assert len(_rows(path)) == 3


def test_record_skips_non_trading_days_and_open_sessions(tmp_path):
    exp, path = _experiment(tmp_path), tmp_path / "journal.csv"
    saturday = datetime(2026, 9, 26, 22, 15, tzinfo=timezone.utc)
    holiday = datetime(2026, 11, 26, 22, 15, tzinfo=timezone.utc)  # Thanksgiving
    midday = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)  # 12:00 New York time
    for now in (saturday, holiday, midday):
        assert record(exp, _synthetic_fetch(SESSION), path, now=now, log=lambda m: None) == 0
    assert not path.exists()


def test_stale_data_is_never_logged_as_today(tmp_path):
    exp, path = _experiment(tmp_path), tmp_path / "journal.csv"
    waits = []
    yesterday = date(2026, 9, 24)  # the provider has not published Friday's bar
    with pytest.raises(StaleData, match="nothing recorded"):
        record(exp, _synthetic_fetch(yesterday), path, now=AFTER_CLOSE, sleep=waits.append, delays=(1, 2, 4), log=lambda m: None)
    assert waits == [1, 2, 4] and not path.exists()


def test_record_falls_back_to_the_next_provider(tmp_path):
    exp, path = _experiment(tmp_path, providers=("yahoo", "synthetic")), tmp_path / "journal.csv"
    calls = []
    assert record(exp, _synthetic_fetch(SESSION, calls), path, now=AFTER_CLOSE, log=lambda m: None) == 3
    assert calls == ["yahoo", "synthetic"] and {r["provider"] for r in _rows(path)} == {"synthetic"}


def test_experiment_id_tracks_content_not_formatting():
    a = {"watchlist": ["SPY"], "weighting": "equal"}
    assert experiment_id(a) == experiment_id(json.loads(json.dumps(a, indent=4)))
    assert experiment_id(a) == experiment_id({"weighting": "equal", "watchlist": ["SPY"]})
    assert experiment_id(a) != experiment_id({**a, "weighting": "by_category"})


def test_committed_experiment_matches_the_live_desk_defaults():
    exp = Experiment.load()
    from trades.config import DEFAULT_ADVISORS, DEFAULT_WATCHLISTS
    from trades.data.base import HISTORY_START

    assert exp.watchlist == DEFAULT_WATCHLISTS["yahoo"] and exp.definition["advisors"] == DEFAULT_ADVISORS
    assert exp.history_start == HISTORY_START  # the same check days as the Live Desk


# ------------------------------------------------------------------------- scoring by hand

DAYS = trading_days(date(2025, 1, 2), date(2025, 3, 31))[:40]


def _market() -> dict[str, pd.DataFrame]:
    """Every open is 100. A closes at 100 + i on day i, B at 100 - i, SPY at 100.

    So a row on day i earns (i + h) / 100 in A and -(i + h) / 100 in B over h sessions,
    from the next open (100) to the close h sessions later, and SPY earns nothing."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in DAYS]).tz_localize("UTC")
    i = np.arange(len(DAYS), dtype=float)

    def bars(close):
        return pd.DataFrame({"open": 100.0, "high": 200.0, "low": 50.0, "close": close, "volume": 1.0}, index=idx)

    return {"A": bars(100 + i), "B": bars(100 - i), "SPY": bars(np.full(len(i), 100.0))}


def _journal() -> pd.DataFrame:
    def row(day, sym, label, votes, exp="e1"):
        return {
            "session_date": DAYS[day].isoformat(),
            "symbol": sym,
            "label": label,
            "votes": json.dumps(votes),
            "experiment_id": exp,
            "code_version": "abc",
        }

    rows = [
        row(0, "A", "Buy", {"tsmom": 1, "rsi2": 0}),
        row(0, "B", "Neutral", {"tsmom": -1, "rsi2": 1}),
        row(1, "A", "Buy", {"tsmom": 1, "rsi2": 0}),
        row(1, "B", "Buy", {"tsmom": 1, "rsi2": None}),
        row(5, "A", "Buy", {"tsmom": 1, "rsi2": 0}),
        row(10, "A", "Strong buy", {"tsmom": 0, "rsi2": -1}),
    ]
    df = pd.DataFrame(rows)
    return df.assign(session_day=pd.to_datetime(df["session_date"]).dt.date)


def test_outcomes_by_hand():
    scored = outcomes(_journal(), _market(), "SPY", last_session=DAYS[25])
    # Day 0, A: open 100 on day 1, close 105 on day 5 -> +5%; B mirrors it.
    assert scored["excess_5"].tolist() == pytest.approx([0.05, -0.05, 0.06, -0.06, 0.10, 0.15])
    # 21 sessions: day 0 ends on day 21 and day 1 on day 22 (both <= 25); days 5 and 10 are not done yet.
    assert scored["excess_21"].iloc[:4].tolist() == pytest.approx([0.21, -0.21, 0.22, -0.22])
    assert scored["excess_21"].iloc[4:].isna().all()


def test_label_statistics_by_hand():
    scored = outcomes(_journal(), _market(), "SPY", last_session=DAYS[39])
    buy = by_label(scored, 5)["Buy"]
    # Daily "Buy" averages: day 0 +5%, day 1 (A +6%, B -6%) 0%, day 5 +10%. Day 1 overlaps day 0's
    # 5-session window, so only days 0 and 5 count: mean 7.5%, both beat SPY,
    # t = 0.075 / (std(0.05, 0.10) / sqrt(2)) = 0.075 / 0.025 = 3.
    assert (buy.n, buy.mean, buy.hit_rate, buy.t) == (2, pytest.approx(0.075), 1.0, pytest.approx(3.0))
    neutral = by_label(scored, 5)["Neutral"]
    assert (neutral.n, neutral.mean, neutral.hit_rate) == (1, pytest.approx(-0.05), 0.0)
    # 21 sessions: days 1 and 5 overlap day 0's window, so "Buy" is day 0 alone: +21%.
    buy21 = by_label(scored, 21)["Buy"]
    assert (buy21.n, buy21.mean) == (1, pytest.approx(0.21))


def test_strategy_statistics_by_hand():
    scored = outcomes(_journal(), _market(), "SPY", last_session=DAYS[39])
    bull, other = by_strategy(scored, 5)["tsmom"]
    # Bullish: day 0 +5%, day 1 0% (overlaps), day 5 +10% -> 7.5% over 2 days.
    assert (bull.n, bull.mean) == (2, pytest.approx(0.075))
    # Neutral or bearish: day 0 B -5%, day 10 A +15% -> 5% over 2 days.
    assert (other.n, other.mean) == (2, pytest.approx(0.05))
    bull, other = by_strategy(scored, 5)["rsi2"]
    assert (bull.n, bull.mean) == (1, pytest.approx(-0.05))
    # Not bullish: A on days 0, 1, 5, 10 (B on day 1 was warming up) -> days 0, 5, 10 -> 10%.
    assert (other.n, other.mean) == (3, pytest.approx(0.10))


def test_report_keeps_experiments_apart_and_warns_early():
    journal = pd.concat([_journal(), _journal().assign(experiment_id="e2")]).drop(columns="session_day")
    text = render_report(journal, _market(), "SPY", DAYS[39])
    assert text.count("## Experiment `") == 2 and "Too early to conclude anything" in text
    assert "| Buy | 2 | +7.50% | 100% | 3.00 |" in text
    assert render_report(pd.DataFrame(), {}, "SPY", DAYS[39]).endswith("The journal is empty so far.\n")
