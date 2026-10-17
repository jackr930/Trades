"""``trades research`` end to end, in a scratch copy of the repository, on synthetic 30-minute bars."""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from trades import config, runs, specs
from trades.cli import main as cli_main
from trades.data.synthetic import SyntheticProvider
from trades.validation import checklist
from trades.validation.research import ResearchError, research

REPO = Path(__file__).resolve().parents[2]
SPEC = "STRAT-091"
TEST_END = date(2026, 6, 30)


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    for folder in ("config", "risk", "specs"):
        shutil.copytree(REPO / folder, tmp_path / folder)
    text = (tmp_path / "specs" / "STRAT-003-intraday-momentum.md").read_text()
    text = (
        text.replace("id: STRAT-003", f"id: {SPEC}")
        .replace("symbols: [SPY]", "symbols: [SIMIDX]")
        .replace("train: [2016-01-04, 2021-12-31]", "train: [2025-01-02, 2025-08-29]")
        .replace("validation: [2022-01-03, 2023-12-29]", "validation: [2025-09-02, 2025-12-31]")
        .replace("test: [2024-01-02, 2026-09-30]", f"test: [2026-01-02, {TEST_END}]")
    )
    (tmp_path / "specs" / f"{SPEC}-test.md").write_text(text)
    monkeypatch.setenv("TRADES_REPO", str(tmp_path))
    config.clear_cache()
    yield tmp_path
    config.clear_cache()


class SpyFetch:
    """Synthetic bars, remembering the last date of every request."""

    def __init__(self):
        self.provider = SyntheticProvider(seed=7, end=date(2026, 9, 25))
        self.ends: list[date] = []

    def __call__(self, provider, symbols, timeframe, start, end):
        self.ends.append(end)
        return {s: self.provider.history(s, timeframe, start, end) for s in symbols}, {}


QUIET = {"log": lambda m: None}


def test_a_run_records_everything_needed_to_reproduce_it(scratch):
    fetch = SpyFetch()
    rec = research(SPEC, provider="synthetic", fetch=fetch, **QUIET)
    folder = scratch / "runs" / rec["id"]
    assert {p.name for p in folder.iterdir()} >= {"run.json", "report.md", "variants.json", "artifacts"}
    saved = json.loads((folder / "run.json").read_text())
    assert saved["spec"]["key"] == f"{SPEC}-v1" and saved["seed"] == 7 and saved["data"]["hash"]
    assert [c["n"] for c in saved["checklist"]] == list(range(1, 15))
    by_n = {c["n"]: c for c in saved["checklist"]}
    assert by_n[1]["status"] == "fail"  # a draft
    assert by_n[2]["status"] == "fail" and "synthetic" in by_n[2]["detail"]  # not real data
    assert by_n[9]["status"] == "pending"  # the locked test is sealed
    assert saved["verdict"] == "fail" and "Hypothetical backtest" in (folder / "report.md").read_text()
    # Every variant is counted, and counting is cumulative across runs of the same version.
    first = rec["variants_tested"]
    assert first == len(runs.variants_tested(f"{SPEC}-v1")) >= 4  # the grid's four, plus neighbours
    again = research(SPEC, provider="synthetic", fetch=fetch, now=datetime(2026, 10, 16, tzinfo=timezone.utc), **QUIET)
    assert again["variants_tested"] == first  # the same variants again are not new trials
    assert len(runs.list_runs(f"{SPEC}-v1")) == 2


def test_the_locked_period_is_never_loaded_unless_opened(scratch):
    fetch = SpyFetch()
    research(SPEC, provider="synthetic", fetch=fetch, write=False, **QUIET)
    validation_end = specs.find(SPEC).periods["validation"].end
    assert fetch.ends and max(fetch.ends) <= validation_end


def test_the_locked_test_opens_once_for_a_frozen_spec_that_passed_everything_else(scratch, monkeypatch):
    fetch = SpyFetch()
    with pytest.raises(ResearchError, match="draft"):
        research(SPEC, open_locked_test=True, provider="synthetic", fetch=fetch, **QUIET)
    specs.freeze(specs.find(SPEC), date(2026, 10, 15))
    # It stays sealed while other checks fail ...
    sealed = research(SPEC, open_locked_test=True, provider="synthetic", fetch=fetch, **QUIET)
    assert not sealed["locked_test"]["opened"] and "stays sealed" in sealed["locked_test"]["refused"]
    assert runs.locked_test_run(f"{SPEC}-v1") is None
    # ... and opens once they pass (here: pretend they did).
    monkeypatch.setattr(checklist, "verdict", lambda checks: "ready_for_locked_test")
    fetch.ends.clear()
    opened = research(SPEC, open_locked_test=True, provider="synthetic", fetch=fetch, **QUIET)
    assert opened["locked_test"]["opened"] and max(fetch.ends) == TEST_END
    assert opened["results"]["locked_test"]["sharpe"] is not None
    assert runs.locked_test_run(f"{SPEC}-v1")["id"] == opened["id"]
    with pytest.raises(ResearchError, match="already opened"):
        research(SPEC, open_locked_test=True, provider="synthetic", fetch=fetch, **QUIET)
    # A new version starts sealed again.
    v2 = specs.new_version(specs.find(SPEC))
    assert specs.find(SPEC).version == 2 and runs.locked_test_run(f"{SPEC}-v2") is None
    assert v2.exists()


def test_a_frozen_spec_changed_afterwards_is_refused(scratch):
    frozen = specs.freeze(specs.find(SPEC), date(2026, 10, 15))
    frozen.path.write_text(frozen.path.read_text().replace("use_r12: false", "use_r12: true"))
    with pytest.raises(specs.SpecError, match="changed after it was frozen"):
        research(SPEC, provider="synthetic", fetch=SpyFetch(), **QUIET)


def test_cli_lists_shows_and_reproduces_runs(scratch, monkeypatch, capsys):
    import trades.validation.research as research_module

    fetch = SpyFetch()
    monkeypatch.setattr(research_module, "default_fetch", fetch)
    rec = research(SPEC, provider="synthetic", **QUIET)
    capsys.readouterr()
    assert cli_main(["spec", "list"]) == 0
    assert f"{SPEC}-v1" in capsys.readouterr().out
    assert cli_main(["runs", "list", SPEC]) == 0 and rec["id"] in capsys.readouterr().out
    assert cli_main(["runs", "reproduce", rec["id"]]) == 0
    assert "Reproduced" in capsys.readouterr().out
    # Freezing needs a person (or --yes): without a terminal it refuses.
    assert cli_main(["spec", "freeze", SPEC]) == 1
    assert not specs.find(SPEC).is_frozen
