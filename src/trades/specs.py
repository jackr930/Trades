"""Strategy specifications: the written, frozen rules a strategy is researched and traded under.

A spec is a Markdown file in ``specs/`` such as ``specs/STRAT-002-orb-qqq.md``. Its YAML front
matter is what the code runs (strategy, parameters, universe, data, date splits, costs); the
body explains the same rules in plain words, in the playbook's eleven sections.

Life cycle (the ``status`` field):

==============  ===========================================================================
draft           the rules may still change; research runs are exploratory
backtesting     frozen by ``trades spec freeze`` (Jack only): the rules are hashed, and any later
                change to them is detected; make a new version instead
paper           the acceptance checklist passed; the paper trader may run it
live            the paper-to-live gate passed (Phase 4, decided by Jack)
retired         invalidated; keep the file and add a post-mortem
==============  ===========================================================================

Freezing records ``frozen`` (the date) and ``rules_hash`` (a hash of everything in the front
matter except ``status``, ``frozen`` and ``rules_hash``), so the locked test period, the allowed
parameter ranges and the cost model cannot drift after the fact.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from trades import config
from trades.strategies import StrategyConfig, get_strategy_class

STATUSES = ("draft", "backtesting", "paper", "live", "retired")
ID_PATTERN = re.compile(r"^STRAT-\d{3}$")
FRONT_KEYS = {
    "id",
    "name",
    "version",
    "status",
    "frozen",
    "rules_hash",
    "strategy",
    "universe",
    "data",
    "periods",
    "costs",
    "expected",
    "invalidation",
    "parameters",
    "one_trade_per_day",
}
UNHASHED = ("status", "frozen", "rules_hash")


class SpecError(ValueError):
    """A spec file is malformed, inconsistent, or was changed after it was frozen."""


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    def to_list(self) -> list[str]:
        return [self.start.isoformat(), self.end.isoformat()]


@dataclass
class Spec:
    path: Path
    id: str
    name: str
    version: int
    status: str
    frozen: date | None
    rules_hash: str | None
    strategy: StrategyConfig
    universe: dict[str, Any]
    data: dict[str, Any]
    periods: dict[str, Period]  # train | validation | test
    costs: dict[str, float]
    expected: dict[str, Any]
    invalidation: dict[str, Any]
    parameters: dict[str, list[Any]]
    one_trade_per_day: bool
    body: str
    front: dict[str, Any] = field(repr=False)

    # -- identity ----------------------------------------------------------------------
    @property
    def key(self) -> str:
        """``STRAT-002-v1``: runs and locks are kept per spec version."""
        return f"{self.id}-v{self.version}"

    @property
    def is_frozen(self) -> bool:
        return self.frozen is not None

    def computed_hash(self) -> str:
        return rules_hash(self.front)

    def drift(self) -> str | None:
        """Why this frozen spec can no longer be trusted, or None if it is intact."""
        if not self.is_frozen:
            return None
        if not self.rules_hash:
            return f"{self.id} has a frozen date but no rules_hash: freeze it with `trades spec freeze`."
        if self.rules_hash != self.computed_hash():
            return (
                f"{self.id} v{self.version} was changed after it was frozen on {self.frozen}. "
                "Its research no longer describes these rules: undo the change, or copy the spec to a "
                "new version (`trades spec new --from " + self.id + "`)."
            )
        return None

    # -- what the code needs -----------------------------------------------------------
    @property
    def symbols(self) -> list[str]:
        if "symbols" in self.universe:
            return [str(s).upper() for s in self.universe["symbols"]]
        file = config.path(str(self.universe["symbols_file"]))
        if not file.exists():
            raise SpecError(
                f"{self.id}: the universe file {self.universe['symbols_file']} does not exist yet. "
                "Build it first (see the spec's Universe section)."
            )
        symbols = []
        for line in file.read_text().splitlines():
            line = line.split("#", 1)[0].strip()  # one ticker per line; '#' starts a comment
            if line:
                symbols.append(line.upper())
        return symbols

    @property
    def timeframe(self) -> str:
        return str(self.data.get("timeframe", "1d"))

    @property
    def provider(self) -> str:
        return str(self.data.get("provider", "synthetic"))

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "frozen": self.frozen.isoformat() if self.frozen else None,
            "drift": self.drift(),
            "strategy": {"id": self.strategy.id, "params": self.strategy.params, "sizing": self.strategy.sizing},
            "universe": self.universe,
            "data": self.data,
            "periods": {k: p.to_list() for k, p in self.periods.items()},
            "costs": self.costs,
            "expected": self.expected,
            "invalidation": self.invalidation,
            "parameters": self.parameters,
            "one_trade_per_day": self.one_trade_per_day,
            "path": str(self.path),
        }


# --------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------


def split_front_matter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise SpecError("a spec must start with a YAML front matter block between '---' lines")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise SpecError("the front matter block is not closed with a '---' line")
    return text[4:end], text[end + 5 :]


def _date(value: Any, what: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise SpecError(f"{what}: {value!r} is not a YYYY-MM-DD date") from exc


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def rules_hash(front: dict[str, Any]) -> str:
    rules = {k: v for k, v in front.items() if k not in UNHASHED}
    canonical = json.dumps(_jsonable(rules), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def parse(path: Path) -> Spec:
    text = path.read_text()
    raw, body = split_front_matter(text)
    try:
        front = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise SpecError(f"{path.name}: front matter is not valid YAML: {exc}") from exc
    if not isinstance(front, dict):
        raise SpecError(f"{path.name}: front matter must be a mapping")
    unknown = sorted(set(front) - FRONT_KEYS)
    if unknown:
        raise SpecError(f"{path.name}: unknown field(s) {', '.join(unknown)}")
    for key in ("id", "name", "strategy", "universe", "data", "periods"):
        if key not in front:
            raise SpecError(f"{path.name}: missing required field '{key}'")

    sid = str(front["id"])
    if not ID_PATTERN.match(sid):
        raise SpecError(f"{path.name}: id must look like STRAT-001, got {sid!r}")
    if not path.name.startswith(sid):
        raise SpecError(f"{path.name}: the file name must start with the spec id {sid}")
    status = str(front.get("status", "draft"))
    if status not in STATUSES:
        raise SpecError(f"{path.name}: status must be one of {', '.join(STATUSES)}")
    version = front.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise SpecError(f"{path.name}: version must be a whole number >= 1")

    strat = front["strategy"]
    if not isinstance(strat, dict) or "id" not in strat:
        raise SpecError(f"{path.name}: strategy must name a strategy id from the registry")
    strategy = StrategyConfig(str(strat["id"]), dict(strat.get("params") or {}), strat.get("sizing"))
    try:
        get_strategy_class(strategy.id)
    except KeyError as exc:
        raise SpecError(f"{path.name}: {exc}") from exc

    universe = front["universe"]
    if not isinstance(universe, dict) or not ({"symbols", "symbols_file"} & set(universe)):
        raise SpecError(f"{path.name}: universe needs 'symbols' (a list) or 'symbols_file' (a path)")

    raw_periods = front["periods"]
    if not isinstance(raw_periods, dict) or set(raw_periods) != {"train", "validation", "test"}:
        raise SpecError(f"{path.name}: periods needs exactly train, validation and test")
    periods: dict[str, Period] = {}
    for name in ("train", "validation", "test"):
        pair = raw_periods[name]
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise SpecError(f"{path.name}: periods.{name} must be [start, end]")
        p = Period(_date(pair[0], f"periods.{name}"), _date(pair[1], f"periods.{name}"))
        if p.end <= p.start:
            raise SpecError(f"{path.name}: periods.{name} ends before it starts")
        periods[name] = p
    if not periods["train"].end < periods["validation"].start <= periods["validation"].end < periods["test"].start:
        raise SpecError(f"{path.name}: periods must be in order with no overlap: train < validation < test")

    parameters = front.get("parameters") or {}
    if not isinstance(parameters, dict) or any(not isinstance(v, list) or not v for v in parameters.values()):
        raise SpecError(f"{path.name}: parameters maps each parameter to a non-empty list of allowed values")

    frozen = front.get("frozen")
    return Spec(
        path=path,
        id=sid,
        name=str(front["name"]),
        version=version,
        status=status,
        frozen=_date(frozen, "frozen") if frozen else None,
        rules_hash=str(front["rules_hash"]) if front.get("rules_hash") else None,
        strategy=strategy,
        universe=dict(universe),
        data=dict(front["data"] or {}),
        periods=periods,
        costs={str(k): float(v) for k, v in (front.get("costs") or {}).items()},
        expected=dict(front.get("expected") or {}),
        invalidation=dict(front.get("invalidation") or {}),
        parameters={str(k): list(v) for k, v in parameters.items()},
        one_trade_per_day=bool(front.get("one_trade_per_day", False)),
        body=body,
        front=front,
    )


def spec_dir() -> Path:
    return config.path("specs")


def all_specs() -> list[Spec]:
    """Every spec in ``specs/``; a malformed file raises ``SpecError``."""
    return [parse(p) for p in sorted(spec_dir().glob("STRAT-*.md"))]


def scan() -> tuple[list[Spec], dict[str, str]]:
    """Every spec that parses, and the problem with each file that does not (for the web UI,
    where one broken file should not hide the others)."""
    good, problems = [], {}
    for path in sorted(spec_dir().glob("STRAT-*.md")):
        try:
            good.append(parse(path))
        except (SpecError, OSError) as exc:
            problems[path.name] = str(exc)
    return good, problems


def find(ref: str) -> Spec:
    """A spec by path, by id (``STRAT-002``: its latest version) or by key (``STRAT-002-v1``)."""
    candidate = Path(ref)
    if candidate.suffix == ".md" and candidate.exists():
        return parse(candidate)
    m = re.match(r"^(STRAT-\d{3})(?:-V(\d+))?$", ref.strip().upper())
    if not m:
        raise SpecError(f"{ref!r} is not a spec id like STRAT-002, a key like STRAT-002-v1, or a .md path")
    versions = [s for s in all_specs() if s.id == m.group(1)]
    if m.group(2):
        versions = [s for s in versions if s.version == int(m.group(2))]
    if not versions:
        known = ", ".join(s.key for s in all_specs()) or "none yet"
        raise SpecError(f"no spec matches {ref!r}; specs: {known}")
    keys = [s.key for s in versions]
    if len(set(keys)) != len(keys):
        raise SpecError(f"two files claim the same spec version: {', '.join(str(s.path) for s in versions)}")
    return max(versions, key=lambda s: s.version)


# --------------------------------------------------------------------------------------
# Freezing (Jack's decision: blocked for Claude Code by .claude/hooks/block_live.sh)
# --------------------------------------------------------------------------------------


def _set_front_value(text: str, key: str, value: str) -> str:
    raw, body = split_front_matter(text)
    pattern = re.compile(rf"^{key}:[^\n]*$", re.M)
    line = f"{key}: {value}"
    if pattern.search(raw):
        raw = pattern.sub(lambda m: line + _comment(m.group(0)), raw, count=1)
    else:
        raw = raw.rstrip("\n") + "\n" + line
    return f"---\n{raw}\n---\n{body}"


def _comment(line: str) -> str:
    """Keep a trailing ``# comment`` when a value is replaced."""
    idx = line.find(" #")
    return line[idx:] if idx >= 0 else ""


def freeze(spec: Spec, today: date) -> Spec:
    """Fix the rules: record the date and their hash and move the spec to ``backtesting``."""
    if spec.is_frozen:
        raise SpecError(f"{spec.id} v{spec.version} was already frozen on {spec.frozen}.")
    if spec.status != "draft":
        raise SpecError(f"only a draft can be frozen; {spec.id} is {spec.status}")
    spec.strategy.build()  # the strategy and its parameters must be valid before they are fixed
    for name, values in spec.parameters.items():
        for v in values:
            StrategyConfig(spec.strategy.id, {**spec.strategy.params, name: v}, spec.strategy.sizing).build()
    text = spec.path.read_text()
    text = _set_front_value(text, "status", "backtesting")
    text = _set_front_value(text, "frozen", today.isoformat())
    text = _set_front_value(text, "rules_hash", spec.computed_hash())
    spec.path.write_text(text)
    frozen = parse(spec.path)
    assert frozen.drift() is None
    return frozen


def new_version(spec: Spec) -> Path:
    """Copy a spec to its next version as a draft (the old version and its runs stay as they were)."""
    stem = spec.path.stem
    base = re.sub(r"-v\d+$", "", stem)
    target = spec.path.with_name(f"{base}-v{spec.version + 1}.md")
    if target.exists():
        raise SpecError(f"{target.name} already exists")
    text = spec.path.read_text()
    text = _set_front_value(text, "version", str(spec.version + 1))
    text = _set_front_value(text, "status", "draft")
    text = _set_front_value(text, "frozen", "null")
    text = _set_front_value(text, "rules_hash", "null")
    target.write_text(text)
    return target
