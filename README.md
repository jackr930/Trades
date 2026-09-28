# Trades

**Quant strategy research, real-time trade recommendations, and a trading simulator, in one local web app.**

Trades runs strategies from published quantitative-finance research (time-series momentum, trend filters,
Turtle breakouts, pairs trading, cross-sectional momentum, low volatility and more) on real-time or historical
market data. It explains every recommendation in plain language, backed by the strategy's track record on that
symbol. It also includes a bar-by-bar simulator where you practise trading with a paper account and get a
scorecard that grades your *process*, not just your P&L.

> **Recommendations only.** Trades never connects to a brokerage account and never places real orders.
> It is educational software, not investment advice.

![Live Desk](docs/screenshots/live-desk.jpg)

## What's inside

| Area | What it does |
| --- | --- |
| **Live Desk** | Streams quotes for your watchlist (Yahoo Finance with no key, Alpaca real-time with a free key, or an offline demo market), runs every enabled strategy on each bar, and shows a consensus signal. Each strategy's vote comes with the rule it applied, how long the signal has held, its backtested record on *this* symbol, and a risk-based position size with a protective stop. |
| **Strategy Lab** | Backtests any strategy on any symbols and dates with realistic next-bar fills, slippage, commissions and short-borrow fees. Includes a buy-and-hold benchmark, drawdowns, monthly returns and trade lists. Parameter optimisation reports the **Deflated Sharpe Ratio** (how likely the "best" result is luck), and **walk-forward** testing scores parameters only on data the optimiser never saw. |
| **Simulator** | Trade one bar at a time with market, limit, stop and bracket (stop-loss/take-profit) orders. Choose synthetic scenarios (crash, bubble, chop, and more) or famous real periods such as 2008, COVID and the dot-com bust in **blind mode**, where ticker, dates and price level are hidden until the end. You race the strategies, then get a scorecard covering outcome and process: stop usage, position sizing, cutting losses, the disposition effect, over-trading, and journaling. |
| **Library** | Rules, rationale, failure modes, evidence rating and references for every strategy, plus concise explainers on look-ahead bias, overfitting, survivorship bias, costs, the Sharpe ratio's uncertainty, position sizing and behavioural biases. |

| Strategy Lab | Simulator | Scorecard |
| --- | --- | --- |
| ![Strategy Lab backtest](docs/screenshots/strategy-lab.jpg) | ![Simulator session](docs/screenshots/simulator.jpg) | ![Simulator scorecard](docs/screenshots/scorecard.jpg) |

## Strategy library

| Strategy | Category | Evidence | Key reference |
| --- | --- | --- | --- |
| Time-series momentum | Trend following | Strong | Moskowitz, Ooi & Pedersen (2012), *JFE* |
| Trend filter (10-month SMA) | Trend following | Moderate | Faber (2007), *J. Wealth Management* |
| Moving-average crossover | Trend following | Moderate | Brock, Lakonishok & LeBaron (1992), *JF*; Sullivan, Timmermann & White (1999) |
| Donchian breakout (Turtle System 1) | Trend following | Moderate | Faith (2007), *Way of the Turtle* |
| Bollinger band mean reversion | Mean reversion | Practitioner | Bollinger (2001); Lehmann (1990) |
| RSI(2) pullback | Mean reversion | Practitioner | Connors & Alvarez (2008) |
| Pairs trading (cointegration z-score) | Statistical arbitrage | Moderate | Gatev, Goetzmann & Rouwenhorst (2006), *RFS*; Avellaneda & Lee (2010) |
| Cross-sectional momentum (12-1) | Momentum | Strong | Jegadeesh & Titman (1993), *JF*; Asness, Moskowitz & Pedersen (2013) |
| 52-week-high momentum | Momentum | Strong | George & Hwang (2004), *JF* |
| Dual momentum | Momentum | Practitioner | Antonacci (2014) |
| Low-volatility anomaly | Defensive factor | Strong | Ang et al. (2006), *JF*; Frazzini & Pedersen (2014), *JFE* |
| Short-term reversal | Mean reversion | Moderate | Lehmann (1990); Jegadeesh (1990); Avramov, Chordia & Goyal (2006) |
| Buy and hold | Benchmark | — | Sharpe (1991), *FAJ* |

Position sizing is a separate, swappable layer: fixed allocation, **volatility targeting** (Moreira & Muir 2017),
or **ATR risk units** (the Turtles' fixed-fractional sizing).

## Quick start

Requirements: Python 3.10+ and Node.js 18+ (only needed to build the web UI).

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .
cd web && npm install && npm run build && cd ..
trades serve                                          # open http://127.0.0.1:8000
```

The app starts on an offline **synthetic demo market** (clearly labelled, with an accelerated clock) so everything
works without an internet connection or API key. Switch to real data under **Settings**.

Or with Docker:

```bash
docker compose up --build                             # http://127.0.0.1:8000
```

For development with hot reload: run `trades serve` and, in another terminal, `cd web && npm run dev`
(then open http://localhost:5173). Common tasks are also in the `Makefile` (`make serve`, `make dev`, `make test`).

## Connecting real-time market data

| Provider | Key needed | Latency | Notes |
| --- | --- | --- | --- |
| **Yahoo Finance** (via `yfinance`) | No | Near real-time, polled | Decades of daily history; intraday limited to 7-60 days. Unofficial API, for personal use. |
| **Alpaca Market Data** | Free account | Real-time, streamed | Free plan uses the IEX exchange feed; a paid plan unlocks the consolidated SIP feed. Trades uses only Alpaca's *data* API, never its trading API. |
| **CSV files** | No | Static | Put `SYMBOL.csv` files (Date, Open, High, Low, Close, Volume) in `~/.trades/data`. |
| **Synthetic** | No | Simulated | Regime-switching GARCH market with jumps; not real data. |

To use Alpaca, create a free account at [alpaca.markets](https://alpaca.markets), generate a *paper trading* API
key (data access works with it), and paste the key ID and secret into **Settings**. Alternatively, set the
`APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` environment variables.

Settings, including API keys, are stored locally in `~/.trades/settings.json` (override with `TRADES_HOME`) with
file mode 0600. Keys are masked whenever the UI reads them back. The server listens on `127.0.0.1` by default; don't
expose it to a network, because it has no authentication.

## How results are kept honest

Backtests go wrong in predictable ways, and the engine is built to avoid the common traps:

- **No look-ahead bias.** Signals are computed from data up to a bar's close and filled at the *next* bar's open
  (or close). The test suite truncates the data at several points and checks that every strategy's past signals,
  indicators and position sizes never change when future bars are added.
- **Costs are always on.** Slippage (default 5 bps per fill), optional commissions and short-borrow fees. The Lab
  warns you when costs eat a large share of gross profits.
- **Statistics that tell you how much to trust a number.** Each backtest reports the Probabilistic Sharpe Ratio
  (Bailey & López de Prado 2012) alongside the Sharpe ratio. Parameter searches report the Deflated Sharpe Ratio
  (2014), and walk-forward analysis compares in-sample with out-of-sample results.
- **Plain-language warnings** for too few trades, short samples, probably-overfit results, synthetic data, and
  survivorship bias when you pick today's symbols to test the past.
- **Benchmarks everywhere.** Every backtest and simulator session is compared with buy-and-hold over the same period.

## Command line

```bash
trades strategies -v                                  # list strategies with references
trades backtest tsmom SPY --provider yahoo --start 2010-01-01
trades backtest pairs_trading KO PEP --provider yahoo --short
trades recommend AAPL MSFT NVDA --provider yahoo -v   # print recommendations
trades serve --port 8000
```

## Architecture

```
trades/
  core/        NYSE calendar, timeframes, indicators, statistics (PSR/DSR, ADF, cointegration), ledger
  data/        providers (synthetic, Yahoo, Alpaca, CSV), bar normalisation, cached data service
  strategies/  strategy framework + library, position sizing
  backtest/    event-driven engine, metrics, optimisation (grid + Deflated Sharpe, walk-forward)
  advisor/     ensemble recommendation engine with evidence and risk-based sizing
  sim/         paper broker, replay sessions, behavioural scorecard
  live/        live service: quotes -> forming bars -> recommendations -> WebSocket
  api/         FastAPI REST + WebSocket, serves the built UI
web/           React + TypeScript + Vite UI (charts: TradingView lightweight-charts)
tests/         pytest suite
```

Data flows one way. A provider supplies bars; a strategy turns them into signals; the sizing layer turns signals
into target weights. The same target weights drive the backtester, the live recommender and the simulator's
strategy "ghosts", so what you research is exactly what gets recommended.

## Adding your own strategy

A single-symbol strategy is a small class: declare its parameters and metadata, then compute a `signal` column
(+1 long, -1 short, 0 flat) using only data up to each bar. Register it in `trades/strategies/__init__.py`, and it
appears in the Lab, the Live Desk and the Simulator.

```python
import numpy as np
import pandas as pd

from trades.core import indicators as ind
from trades.strategies.base import Evidence, Param, Reference, SingleAssetStrategy


class RSITrend(SingleAssetStrategy):
    id = "rsi_trend"
    name = "RSI trend (example)"
    category = "Trend following"
    summary = "Long while 14-bar RSI is above 55, flat below 45."
    rules_text = ("RSI(14) > 55 -> long", "RSI(14) < 45 -> flat")
    rationale = "Momentum persists over short horizons."
    failure_modes = "Choppy markets."
    evidence = Evidence.PRACTITIONER
    evidence_text = "Illustrative example, not a researched strategy."
    references = (Reference("Wilder, J. W.", 1978, "New Concepts in Technical Trading Systems", "Trend Research"),)
    params_spec = (Param("length", "RSI length", 14, "int", 2, 50),)

    def warmup(self) -> int:
        return self.params["length"] + 1

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        r = ind.rsi(df["close"], self.params["length"])
        raw = pd.Series(np.where(r > 55, 1.0, np.where(r < 45, 0.0, np.nan)), index=df.index)
        return pd.DataFrame({"signal": raw.ffill(), "rsi": r}, index=df.index)
```

The parametrised no-look-ahead test in `tests/test_strategies.py` runs against every registered strategy
automatically, so a rule that peeks at future bars fails the test suite.

## Development

```bash
pip install -e ".[dev]"
pytest                      # Python tests
ruff check trades tests     # lint
cd web && npm run build     # type-check and build the UI
```

## Limitations

- US equities and ETFs only (NYSE calendar); no options, futures or crypto.
- The fill model is bar-based: no partial fills, volume limits or queue position. Intrabar order of stop-loss and
  take-profit hits is unknown, so the stop is assumed to fill first.
- Free data has gaps: Yahoo is an unofficial API, Alpaca's free IEX feed covers one exchange, and neither offers a
  survivorship-free historical universe.
- Simulator sessions live in memory (completed-session summaries are saved), and the app is single-user.
- Published strategies weaken after publication (McLean & Pontiff 2016). Treat them as disciplined baselines, not
  guarantees.

## Disclaimer

For education and research only. Nothing here is investment advice or a recommendation to buy or sell any
security. Past performance, and especially backtested performance, does not predict future results. Trading
involves risk of loss.
