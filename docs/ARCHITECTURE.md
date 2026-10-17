# Architecture

Trades is one Python package (`src/trades/`) with a web app on top (`web/`). Its central rule:
**a backtest and the paper trader make every decision with the same code**, so what was
researched is exactly what trades. Everything else is arranged around that.

## One path from data to orders

```
data ─▶ features ─▶ strategy ─▶ sizing ─▶ risk engine ─▶ orders ─▶ broker
                    (signals,             (deterministic,  (target to   (sim_broker in backtests,
                     stop/take/entry)      every bar)       orders)      Alpaca paper in paper)
```

| Step | Code | What it does |
| --- | --- | --- |
| Data | `data/` | Bars from a provider (synthetic, Yahoo, Alpaca, CSV, Massive), the NYSE calendar, timeframes, a parquet cache. Daily bars are stamped 00:00 UTC of their trading date, intraday bars at their start in UTC. See [DATA.md](DATA.md). |
| Features | `features/` | Pure functions of past bars: indicators, time-series statistics, causal intraday session features (the opening range, relative volume, prior-day ATR). |
| Strategy | `strategies/` | Turns features into target weights, plus protective stop, take-profit and entry levels. A strategy never sizes in dollars and never sees an order. |
| Sizing | `strategies/sizing.py` | Fixed allocation, volatility targeting, ATR risk units, or stop risk (size so a stopped-out trade loses a set share of equity). |
| Risk engine | `risk/engine.py` | Eight deterministic checks on every bar's targets, from `risk/limits.yaml`. |
| Orders | `execution/orders.py` | `plan_symbol`: the one rule that turns a target and a holding into orders (market, stop entry, protective legs). |
| Broker | `execution/sim_broker.py`, `execution/alpaca.py` | Fills with the cost model in backtests; the Alpaca paper API in paper trading. |

`engine/live.py` (`decide`) runs strategy, sizing and risk engine for one decision. The backtest
(`engine/backtest.py`) calls the same pieces bar by bar; the daily paper runner
(`engine/paper.py`) and the intraday runner (`engine/intraday.py`) call `decide` and
`plan_symbol` on live bars. Tests hold the line:

* `tests/integration/test_paper.py::test_a_paper_day_decides_exactly_like_the_backtest`: a paper
  day produces the backtest's decisions, bar for bar.
* `tests/integration/test_golden.py`: pinned outputs for every strategy; refactors must not move
  a number.
* `tests/lookahead/`: every registered strategy is run on data cut at several points; past
  signals must not change when future bars are added.

## The risk engine and the kill switch

At each bar's close, before any order, `RiskEngine.review` applies `risk/limits.yaml` in order:

1. **Kill switch.** While the `HALT` file exists, nothing is bought or sold: the targets become
   what the account already holds.
2. **Loss stops.** A daily or weekly loss beyond its limit flattens and blocks new trades until
   the next session (or week).
3. **Symbols.** Blocked symbols, or symbols outside a non-empty allow-list, are not traded.
4. **Shorting.** Without `allow_short`, short targets become flat.
5. **Position size.** No position above `max_position_pct` of equity.
6. **Risk per trade.** A trade with a stop is shrunk so being stopped out loses at most
   `max_risk_per_trade`, and it may not grow while it lasts.
7. **Open positions.** At most `max_open_positions`; positions already held keep their place.
8. **Orders per day.** After `max_orders_per_day` fills, only exits.

Every intervention is a `RiskEvent`, recorded in the backtest result and in the paper events.
Closing everything is a separate, deliberate act: `trades paper flatten` cancels all orders,
closes all positions and turns the kill switch on.

## Paper only, by construction

* `execution/alpaca.py` holds one base URL, Alpaca's paper endpoint, as a constant. No setting,
  argument or environment variable changes it, every request is checked against it, and
  redirects are not followed. `test_no_live_trading_url_in_the_code` fails if Alpaca's live
  trading address appears anywhere in the package.
* `trades paper --submit` refuses a spec whose latest research run did not pass the checklist.
* No model holds order authority. Orders come only from this deterministic, tested code.
* Live keys never live on a machine that runs Claude Code. The hooks in `.claude/hooks/` and the
  deny rules in `.claude/settings.json` are a second layer, not the boundary.

## Records

| Folder | Written by | Holds |
| --- | --- | --- |
| `specs/` | `trades spec ...` | One Markdown file per strategy version; the front matter is what the code runs. |
| `runs/<id>/` | `trades research` | The report and checklist, configuration, git commit, seed, data hash and `variants.json` (every variant ever tested for the spec). Bulky artifacts are gitignored. |
| `paper/<spec key>/` | the paper runners | `orders.csv`, `fills.csv` (the broker's own fills, protective legs included), `decisions.jsonl`, `equity.csv`, `events.jsonl`, `state.json`, daily `reports/`, `TRACKING.md`, `GATE.md`, `gate.json` (drills and attestations). `heartbeat.json` is local only. |
| `journal/` | `trades journal` | The forward journal: the Live Desk's calls logged before their outcome exists, and their scores. |

`runs/` and `paper/` are evidence: nothing edits or deletes them.

## Configuration

| File | Holds | Who edits |
| --- | --- | --- |
| `config/base.yaml` | the default data provider, the cache folder, the cost model, folders | anyone |
| `config/backtest.yaml` | research settings and the 14 acceptance thresholds | Jack only |
| `config/paper.yaml` | which spec the paper account trades, the schedule, alerts | anyone |
| `config/live.yaml` | the gate's thresholds and the loss budget; no live trading exists | Jack only |
| `risk/limits.yaml` | the hard limits the risk engine enforces | Jack only |
| `~/.trades/settings.json` | the web app's settings: watchlists, API keys (mode 0600, masked in the UI), account profile | the user, in Settings |

A spec's own `costs:` and `data:` override `config/base.yaml` for that spec.

## Folders

```
src/trades/
  data/        providers, the NYSE calendar, timeframes, the data service and the parquet cache
  features/    pure indicator functions and causal intraday session features
  strategies/  the registry; strategies emit target weights and stop/take/entry levels only
  risk/        RiskEngine, RiskLimits (risk/limits.yaml), the HALT kill switch
  execution/   sim_broker (fills, costs), orders (target to orders), alpaca (paper only), broker
  engine/      backtest, live (one decision), paper (daily runner), intraday (session runner)
  validation/  the 14-point checklist, research runs, robustness, look-ahead and null tests, the gate
  reporting/   tear sheets, tracking (paper vs replay), alerts, payloads for the app
  recommend/   the Live Desk: recommendations with evidence and sizing, live quotes
  journal/     the forward journal
  simulator/   strategy races on a steerable market, practice trading and its scorecard
  api/         FastAPI REST and WebSocket; serves the built web app
  cli/         the `trades` command
web/           React 19 + TypeScript + Vite; charts by TradingView lightweight-charts
tests/         unit/ (fast), lookahead/ (no strategy may peek ahead), integration/ (golden outputs,
               paper runners against a fake broker, tracking, the gate, the API)
deploy/        Dockerfile, docker-compose.yml, systemd units for running unattended
.claude/       hooks, agents (backtest-auditor, risk-reviewer, ui-designer), the new-strategy skill
```

## The web app

`trades serve` starts FastAPI (`api/`), which serves the API (documented at `/docs` while it
runs) and the built React app. The screens follow the pipeline: **Pipeline** (where every spec
stands, the kill switch, the alert feed), **Live Desk**, **Lab**, **Simulator**, **Library** and
**Settings**; [APP.md](APP.md) describes each. The design language is in [DESIGN.md](DESIGN.md).

## Tests and checks

```bash
pytest -q                          # all tiers (tests/unit, tests/lookahead, tests/integration)
ruff check src tests scripts       # lint
cd web && npm run build            # type-check and build the web app
```

CI runs the same. After every Python edit, a Claude Code hook runs the unit tests. The
`risk-reviewer` agent reviews any diff that touches execution, risk, the engine, deploy or the
hooks; the `backtest-auditor` audits every research run.
