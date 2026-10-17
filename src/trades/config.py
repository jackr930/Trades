"""The committed trading configuration: ``config/*.yaml`` and ``risk/limits.yaml``.

Two kinds of settings live in two places:

* **App settings** you change in the web UI (watchlists, API keys, chart preferences) are
  stored on your machine in ``~/.trades/settings.json`` (see ``trades.settings``).
* **Trading configuration** that decides how strategies are researched, judged and traded
  lives here, in the repository, so every change is reviewed in git:

  ======================  ==================================================  ============
  file                    what it holds                                       who edits
  ======================  ==================================================  ============
  config/base.yaml        shared defaults: data provider, cost model, folders  anyone
  config/backtest.yaml    research settings and the acceptance thresholds     Jack only
  config/paper.yaml       which spec the paper trader runs, schedule, alerts  anyone
  config/live.yaml        live trading: disabled until a spec passes the gate Jack only
  risk/limits.yaml        hard limits the risk engine enforces on orders      Jack only
  ======================  ==================================================  ============

  "Jack only" files are blocked for Claude Code by ``.claude/hooks/protect_files.sh``.

The repository is found from ``$TRADES_REPO``, else by walking up from the working directory
to the folder that holds ``config/base.yaml``, else from the installed package's location.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

MODES = ("backtest", "paper", "live")


class ConfigError(ValueError):
    """A configuration file is missing, malformed or has an unknown key."""


def repo_root(start: Path | None = None) -> Path:
    env = os.environ.get("TRADES_REPO")
    if env:
        return Path(env).expanduser().resolve()
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / "config" / "base.yaml").exists():
            return folder
    package = Path(__file__).resolve().parent  # src/trades -> src -> repository
    for folder in package.parents:
        if (folder / "config" / "base.yaml").exists():
            return folder
    return here


def path(*parts: str) -> Path:
    """A path inside the repository, e.g. ``path("risk", "limits.yaml")``."""
    return repo_root().joinpath(*parts)


def read_yaml(file: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(file.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"{file} is missing") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{file} is not valid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{file} must hold a mapping of keys to values")
    return data


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge: nested mappings are merged key by key, everything else is replaced."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def load(mode: str) -> dict[str, Any]:
    """``config/base.yaml`` merged with ``config/<mode>.yaml``."""
    if mode not in MODES:
        raise ConfigError(f"mode must be one of {', '.join(MODES)}")
    root = repo_root()
    return merge(read_yaml(root / "config" / "base.yaml"), read_yaml(root / "config" / f"{mode}.yaml"))


@lru_cache(maxsize=8)
def _cached(mode: str, root: str) -> dict[str, Any]:
    return load(mode)


def section(mode: str, name: str) -> dict[str, Any]:
    """One top-level section of a mode's merged configuration (empty if absent)."""
    value = _cached(mode, str(repo_root())).get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"'{name}' in the {mode} configuration must be a mapping")
    return dict(value)


def value(mode: str, name: str, default: Any = None) -> Any:
    """One top-level setting of a mode's merged configuration, e.g. ``value("paper", "paper_dir")``."""
    return _cached(mode, str(repo_root())).get(name, default)


def strict(cls_name: str, data: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """Reject keys a dataclass does not know: a misspelt risk limit must fail loudly, not be ignored."""
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"{cls_name}: unknown setting(s) {', '.join(unknown)}; expected {', '.join(sorted(known))}")
    return data


def clear_cache() -> None:
    _cached.cache_clear()
