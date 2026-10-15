"""The committed configuration (config/, risk/limits.yaml) and the strategy specs (specs/)."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from trades import config, specs
from trades.execution import costs
from trades.journal.experiment import Experiment
from trades.risk import RiskLimits, load_limits
from trades.strategies import StrategyConfig

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def scratch_repo(tmp_path, monkeypatch):
    """A copy of the repository's config, risk limits and specs to change freely."""
    for folder in ("config", "risk", "specs"):
        shutil.copytree(REPO / folder, tmp_path / folder)
    monkeypatch.setenv("TRADES_REPO", str(tmp_path))
    config.clear_cache()
    yield tmp_path
    config.clear_cache()


def test_repo_root_and_mode_merging():
    assert config.repo_root() == REPO
    merged = config.load("backtest")
    assert merged["costs"]["taf_max_per_trade"] == 9.79  # from base.yaml
    assert merged["acceptance"]["min_deflated_sharpe"] == 0.95  # from backtest.yaml
    assert config.load("live")["enabled"] is False
    with pytest.raises(config.ConfigError):
        config.load("production")
    assert config.merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}}) == {"a": {"b": 1, "c": 3}}


def test_committed_risk_limits_are_valid_and_conservative():
    limits = load_limits()
    assert limits.max_risk_per_trade <= 0.005  # the playbook: 0.25-0.5% to start
    assert limits.daily_loss_limit == 0.015 and limits.weekly_loss_limit == 0.04
    assert limits.max_gross_exposure == 1.0 and not limits.allow_short  # no leverage, no shorts at first
    assert limits.halt_path() == REPO / "HALT"


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"max_daily_loss": 0.01}, "unknown setting"),  # a typo must not fall back to a default
        ({"daily_loss_limit": 0.9}, "outside the sane range"),
        ({"weekly_loss_limit": 0.01, "daily_loss_limit": 0.02}, "tighter than daily"),
        ({"max_position_pct": 2.0}, "cannot exceed max_gross_exposure"),
        ({"allowed_symbols": ["QQQ"], "blocked_symbols": ["qqq"]}, "both allowed and blocked"),
        ({"allow_short": "yes"}, "true or false"),
    ],
)
def test_bad_risk_limits_stop_the_program(patch, message):
    with pytest.raises(config.ConfigError, match=message):
        RiskLimits.from_dict({**load_limits().to_dict(), **patch})


def test_symbol_lists():
    limits = RiskLimits(allowed_symbols=("QQQ", "SPY"), blocked_symbols=("TSLA",))
    assert limits.symbol_allowed("qqq") and not limits.symbol_allowed("AAPL")
    assert not RiskLimits(blocked_symbols=("TSLA",)).symbol_allowed("TSLA")


def test_cost_model_in_base_yaml_is_valid():
    model = costs.base_costs()
    assert model["sec_fee_per_million"] == 20.60 and model["taf_per_share"] == 0.000195
    assert set(model) <= set(costs.STRESSED)


def test_every_committed_spec_parses_and_its_strategy_builds():
    found = specs.all_specs()
    assert {s.id for s in found} >= {"STRAT-000", "STRAT-001", "STRAT-002", "STRAT-003"}
    for spec in found:
        strat, sizing = spec.strategy.build()
        assert strat.id == spec.strategy.id
        p = spec.periods
        assert p["train"].end < p["validation"].start and p["validation"].end < p["test"].start
        for name, values in spec.parameters.items():  # every allowed value is a valid parameter
            for v in values:
                StrategyConfig(spec.strategy.id, {**spec.strategy.params, name: v}).build()
        assert spec.drift() is None
        assert spec.symbols


def test_universe_files_ignore_comments():
    spec = specs.find("STRAT-001")
    assert spec.symbols[:3] == ["AAPL", "MSFT", "NVDA"] and "STARTER" not in spec.symbols
    assert spec.universe["survivorship_free"] is False


def test_strat_000_is_exactly_the_forward_journals_experiment():
    """The spec and journal/experiment.json describe one strategy; they must not drift apart."""
    spec = specs.find("STRAT-000")
    exp = Experiment.load(REPO / "journal" / "experiment.json")
    assert spec.symbols == exp.watchlist
    assert spec.strategy.params == json.loads(json.dumps(exp.consensus_params()))


def test_freezing_records_the_rules_and_detects_later_edits(scratch_repo):
    spec = specs.find("STRAT-002")
    frozen = specs.freeze(spec, date(2026, 10, 15))
    assert frozen.status == "backtesting" and frozen.frozen == date(2026, 10, 15)
    assert frozen.rules_hash == frozen.computed_hash() and frozen.drift() is None
    text = frozen.path.read_text()
    assert "# set by `trades spec freeze`" in text  # comments survive
    with pytest.raises(specs.SpecError, match="already frozen"):
        specs.freeze(frozen, date(2026, 10, 16))
    # Moving the locked test window after freezing is exactly what must be caught.
    frozen.path.write_text(text.replace("test: [2024-01-02, 2026-09-30]", "test: [2025-01-02, 2026-09-30]"))
    changed = specs.find("STRAT-002")
    assert "was changed after it was frozen" in changed.drift()
    # Prose edits are fine: only the front matter is hashed.
    frozen.path.write_text(text.replace("## 1. Hypothesis", "## 1. Hypothesis (typo fixed)"))
    assert specs.find("STRAT-002").drift() is None


def test_new_version_is_a_draft_and_find_returns_the_latest(scratch_repo):
    v1 = specs.freeze(specs.find("STRAT-003"), date(2026, 10, 15))
    path = specs.new_version(v1)
    assert path.name == "STRAT-003-intraday-momentum-v2.md"
    latest = specs.find("STRAT-003")
    assert latest.version == 2 and latest.status == "draft" and latest.frozen is None
    assert specs.find("STRAT-003-v1").version == 1 and specs.find("strat-003-v1").is_frozen


def test_malformed_specs_are_rejected(scratch_repo):
    good = (scratch_repo / "specs" / "STRAT-002-orb-qqq.md").read_text()
    raw, body = specs.split_front_matter(good)
    front = yaml.safe_load(raw)

    def write(front_matter: dict, name="STRAT-009-bad.md") -> Path:
        path = scratch_repo / "specs" / name
        path.write_text("---\n" + yaml.safe_dump(front_matter) + "---\n" + body)
        return path

    bad = dict(front, id="STRAT-009", periods={**front["periods"], "test": ["2023-01-02", "2024-12-31"]})
    with pytest.raises(specs.SpecError, match="in order with no overlap"):
        specs.parse(write(bad))
    with pytest.raises(specs.SpecError, match="unknown field"):
        specs.parse(write(dict(front, id="STRAT-009", stoploss=1)))
    with pytest.raises(specs.SpecError, match="unknown strategy"):
        specs.parse(write(dict(front, id="STRAT-009", strategy={"id": "nope"})))
    with pytest.raises(specs.SpecError, match="file name must start"):
        specs.parse(write(dict(front, id="STRAT-010")))
    # One broken file: the strict list raises; the scan reports it and keeps the others.
    with pytest.raises(specs.SpecError):
        specs.all_specs()
    good, problems = specs.scan()
    assert list(problems) == ["STRAT-009-bad.md"] and len(good) >= 4
    assert all(s.path.name != "TEMPLATE.md" for s in good)
