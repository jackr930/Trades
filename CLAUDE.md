# Trades: project memory for Claude Code

## What this repo is

A research, backtest and paper-trading system for US equities through Alpaca, built on the
Trades Playbook (spec, research, paper, gate), plus an educational web app (Pipeline, Library, Lab,
simulator, Live Desk).
Owner: Jack. Safety and reproducibility matter more than speed. There is no live (real-money)
order path; Claude never adds one and never holds order authority over real money.

## Hard rules

1. Never place, modify or cancel live orders, and never add a live endpoint, key setting or order
   path. The Alpaca client is paper-only by construction (`src/trades/execution/alpaca.py`).
2. Never read, print or edit `.env` files, `/etc/trades/` or `config/live.yaml`. Never edit
   `risk/limits.yaml` or `config/backtest.yaml` (read them freely). Propose changes in chat.
3. Never implement a strategy without a spec in `specs/` (`trades spec new`; the `new-strategy`
   skill). If it is missing, stop and ask. Jack alone freezes specs (`trades spec freeze`), opens
   locked tests (`--open-locked-test`), records gate attestations (`trades gate --attest`), runs
   kill switch drills, turns the kill switch off (`trades resume`) and sends paper orders
   (`trades paper --submit`).
4. Features use only data available before the decision timestamp. Every registered strategy
   must pass `pytest tests/lookahead`; add a test for each new feature.
5. Every backtest runs through `src/trades/engine/` with the cost model in
   `src/trades/execution/sim_broker.py` and `costs.py`. Paper trading shares the same decision
   path (`engine/live.py`, `execution/orders.py`): never write a second copy of it.
6. Every parameter variant tested is logged in `runs/<id>/variants.json`: test variants with
   `trades research`, never ad hoc. Never delete or edit `runs/` or `paper/` (they are records).
7. Never change a spec's locked test dates or a frozen spec's rules: `trades spec version` makes
   a new draft.

## Architecture

```
data -> features -> strategies (signals, order levels) -> sizing -> risk engine (deterministic)
     -> orders (execution/orders.plan_symbol) -> broker (sim_broker in backtests | Alpaca paper)
```

| Folder (`src/trades/`) | What lives there |
| --- | --- |
| `data/` | providers (synthetic, yahoo, alpaca, csv), the NYSE calendar, timeframes |
| `features/` | pure indicator functions, causal intraday session features |
| `strategies/` | the registry; strategies emit target weights and stop/take/entry levels only |
| `risk/` | `RiskEngine` (eight checks on every bar), `RiskLimits` from risk/limits.yaml, the HALT kill switch |
| `execution/` | `sim_broker.py` (fills, costs), `orders.py` (target to orders), `alpaca.py` (paper only), `broker.py` |
| `engine/` | `backtest.py`, `live.py` (one decision), `paper.py` (nightly runner), `intraday.py` (session runner) |
| `validation/` | the 14-point checklist, research runs, robustness, look-ahead and null tests, the gate |
| `reporting/` | tear sheets, tracking (paper vs replay), alerts, payloads for the app |
| `recommend/`, `journal/`, `simulator/`, `api/`, `cli/` | the Live Desk, the forward journal, practice mode, the web API, the `trades` command |

Specs live in `specs/` (front matter = what the code runs), research runs in `runs/<id>/`, paper
records in `paper/<spec key>/`, configuration in `config/` (base, backtest, paper, live) and
`risk/limits.yaml`.

## Commands

- Install: `pip install -e ".[dev]"`; the web app: `cd web && npm install`
- Tests: `pytest -q` (tiers: `tests/unit`, `tests/lookahead`, `tests/integration`)
- Lint: `ruff check src tests scripts`; web: `cd web && npm run build`
- The pipeline: `trades spec new|show|list|version`, `trades research STRAT-xxx [--provider synthetic]`,
  `trades runs list|show|reproduce`, `trades paper [status|flatten]`, `trades track`, `trades gate`
- The app: `trades serve`

## Style

Python 3.12, type hints, pure functions in `features/`, UTC internally, America/New_York for
sessions. Match the surrounding code. User-facing text is plain English: short sentences, no
jargon without a definition. Prefer small changes with tests. When unsure, ask.

## Guardrails

`.claude/settings.json` wires hooks that block the human-only files and commands above, and runs
the unit tests after Python edits. Agents: `backtest-auditor` (after every research run, before a
freeze) and `risk-reviewer` (any diff touching execution, risk, engine, deploy or the hooks).
Hooks are guardrails, not a security boundary: live keys never live on a machine that runs
Claude Code.
