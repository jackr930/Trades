# Trades

**Quant strategy research, live strategy simulations, and real-time trade recommendations, in one web app you run
on your own computer or put online behind a password.**

Trades runs statistical strategies from published quantitative-finance research: classic rules (time-series
momentum, trend filters, Turtle breakouts, pairs trading, cross-sectional momentum, low volatility and more) and
the modern methods of quant funds and prop desks (a multi-horizon CTA trend signal, factor-neutral statistical
arbitrage, residual momentum, Kalman-filter pairs, a hidden Markov regime model and a walk-forward
machine-learning ranker). In the **strategy simulator**, every strategy trades its own paper account and reacts
bar by bar as the market moves, either a simulated market you can shock (crash, rally, volatility spike, broken
pair) or the connected real-time feed.
The Live Desk turns the same strategies into plain-language recommendations, the Strategy Lab backtests and
optimises them honestly, and a practice mode lets you trade yourself and get a scorecard on your *process*.

> **No real money.** Trades never places real-money orders and has no path to a live brokerage account. The
> optional paper trader sends orders only to an Alpaca *paper* (practice) account.
> It is educational software, not investment advice.

![Live Desk](docs/screenshots/live-desk.jpg)

## What's inside

| Area | What it does |
| --- | --- |
| **Strategy simulator** | Pick strategies and a market, press play. Each strategy decides at every bar's close using only the past and fills at the next open, in its own paper account, so you watch them react in real time. Markets: a **simulated market** (regime-switching, fat tails, volatility clustering, a cointegrated pair) where you can inject a crash, rally, volatility spike, forced regime, earnings gap or pair break mid-run; a **historical replay** at any speed; or a **real-time forward test** on Yahoo or Alpaca data, trading as each bar completes. A live leaderboard, equity race and a feed of every trade *with its reason*; at the end, risk-adjusted results and each strategy's return in every hidden regime. |
| **Live Desk** | Streams quotes for your watchlist (Yahoo Finance with no key, Alpaca real-time with a free key, or an offline demo market), runs every enabled strategy on each bar, and shows a consensus signal. Each strategy's vote comes with the rule it applied, how long the signal has held, its backtested record on *this* symbol, and a risk-based position size with a protective stop. A neutral consensus suggests no position, and a portfolio cap keeps all suggestions together within 100% of your account (the total is shown). The consensus itself is a strategy you can backtest: see [The Live Desk consensus](#the-live-desk-consensus). |
| **Strategy Lab** | Backtests any strategy on any symbols and dates with realistic next-bar fills, slippage, commissions and short-borrow fees. Includes a buy-and-hold benchmark, drawdowns, monthly returns, trade lists, **after-tax results** for your account, and a **cost-sensitivity** check that reruns the test at 2x and 4x costs. Parameter optimisation reports the **Deflated Sharpe Ratio** (how likely the "best" result is luck), and **walk-forward** testing scores parameters only on data the optimiser never saw. |
| **Practice trading** | Trade yourself, one bar at a time, with market, limit, stop and bracket (stop-loss/take-profit) orders. Choose synthetic scenarios (crash, bubble, chop, and more) or famous real periods such as 2008, COVID and the dot-com bust in **blind mode**, where ticker, dates, price level and volume are hidden until the end. You race the strategies, then get a scorecard covering outcome and process: stop usage, position sizing, cutting losses, the disposition effect, over-trading, and journaling. |
| **Today and Portfolio** | The home screen in plain English: today's suggestions and how much of the account they add up to, whether the calls are proven yet, a one-click "Is this better than just buying SPY?" and your real holdings, imported read-only from a Schwab, Fidelity, Vanguard or Robinhood CSV export. A first-run guide sets things up, an essentials mode hides the research tools, and the Library has a plain-English glossary. |
| **Track Record** | The forward journal at a glance: the calls logged each evening before their outcome was known, how they did against SPY at 5 and 21 sessions, how many independent results the pre-registered rule still needs and roughly when a verdict becomes possible, the latest calls, the paper account's fills, and any problem the journal's health check found. |
| **Library** | Rules, rationale, failure modes, evidence rating and references for every strategy, grouped into classic published rules and modern quant methods (each modern method links to the classic rule it refines, with one click to race the two), a plain-English glossary, plus concise explainers on look-ahead bias, overfitting, survivorship bias, costs, the Sharpe ratio's uncertainty, position sizing, behavioural biases, and why an AI model's stock picks can't be backtested (it was trained on text from after the backtest's dates, so only predictions logged in advance can test it). |

| Strategy simulator: results by hidden regime | Strategy Lab |
| --- | --- |
| ![Strategy simulator results](docs/screenshots/strategy-results.jpg) | ![Strategy Lab backtest](docs/screenshots/strategy-lab.jpg) |
| **Practice trading** | **Practice scorecard** |
| ![Practice session](docs/screenshots/simulator.jpg) | ![Practice scorecard](docs/screenshots/scorecard.jpg) |

## Strategy simulator

![Strategy simulator: eight strategies racing through an injected crash](docs/screenshots/strategy-simulator.jpg)

Open **Simulator -> Test strategies**, pick a market and a set of strategies, and press play:

- **Every strategy is an independent trader.** Each gets its own paper account and position sizing, decides at
  the close of each bar with only the bars so far, and fills at the next open with slippage, commissions, short
  borrow fees and margin interest. It is the same execution engine the backtester uses, and the test suite checks
  that a strategy traded live, bar by bar, produces exactly the equity curve of a backtest over the same bars,
  including after you inject events.
- **Steer a simulated market.** Bars come from a regime-switching model (bull, bear, range-bound, and scripted
  stories like "calm, then crash") with GARCH volatility clustering, fat-tailed shocks, jumps, a one-factor
  structure across stocks, and a cointegrated pair. Mid-run you can inject a **crash**, a **rally**, a
  **volatility spike**, a **forced regime**, an **earnings gap** in one stock, or a **pair break** (the spread
  moves to a new level for good, the way pairs trades blow up). Randomness is seeded per bar, so the same seed with
  and without your crash differs *only* by the crash: a controlled experiment.
- **Replay real history** from Yahoo, Alpaca, CSV files or the synthetic history at anything from half a bar to
  200 bars per second, or use **Replay bar by bar** on any Strategy Lab backtest.
- **Forward-test in real time.** Connect Yahoo Finance or Alpaca and choose a bar size (1 minute to daily): the
  strategies trade each bar as it completes on the live market, while the chart shows the bar still forming.
- **Pit modern methods against classic rules.** Quick picks load the classic rules, the modern quant methods, or
  each modern method next to the classic rule it refines ("Modern vs classic"). The warm-up lengthens
  automatically when a strategy needs more history (the machine-learning ranker needs about two years) so every
  strategy can trade from the first live bar.
- **See why.** The event feed lists every signal change and trade with the strategy's own reasoning ("Holding long
  from the last check 5 bars ago...", "Spread is rich (z = 2.36): short SIMPRA, long SIMPRB"), and the strategy
  panel shows each rule's current value.
- **Learn what the result means.** At the end: return, CAGR, Sharpe, probability that the Sharpe ratio is really
  above zero, drawdown, trades, costs, and each strategy's return inside every hidden regime, with cautions about
  reading too much into one path. Run the same market again with changed parameters, or a new market to see
  whether a ranking survives.

## Strategy library

### Classic published rules

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

### The Live Desk consensus

What you would actually trade on is the Live Desk's consensus, not any single strategy, so it is a strategy too
(`consensus`), runnable from the Strategy Lab, the simulator and the command line. One function,
`trades.strategies.consensus.decide`, goes from votes to a suggested position, and both the Live Desk and the
`consensus` strategy call it:

1. **Score.** The average vote of the strategies that are not warming up (+1 bullish, 0 neutral, -1 bearish).
   Four of the eight default strategies are trend rules and a fifth is momentum, so their votes are correlated.
   **Settings -> Combine the votes -> By category** averages within each category (trend following, mean
   reversion, momentum, statistical arbitrage) first and then across categories, so agreeing trend rules count once.
2. **Action.** Score >= +0.2 buy (>= +0.5 strong buy); <= -0.2 sell, or short if shorting is allowed; anything
   in between is **Neutral, with no position**.
3. **Weight.** Conviction (the absolute score) x min(risk per trade x price / (stop ATRs x ATR(20)), max
   position). Account equity cancels out, so this is the same weight on a $1,000 or a $1,000,000 account.
4. **Portfolio cap.** If the suggestions add up to more than the cap (100% of equity by default, up to 200% in
   Settings), every one of them shrinks in proportion.

The strategy's members default to the Live Desk's strategy list; the Lab and the command line fill in your
saved Live Desk settings (strategies, pairs, weighting, risk per trade, stop, position cap, portfolio cap,
shorting), so a backtest tests what the Live Desk would have told you. A test runs the Live Desk on data cut off
at several bars and checks that it gives the same action and weight as the strategy at those bars; the
no-look-ahead test covers the strategy like every other.

Rules re-checked on a fixed schedule (time-series momentum every 21 bars, cross-sectional momentum's monthly
rebalance) count that schedule from the first bar of history, so the same rule fed a different start date checks
on different days. Daily history therefore always starts on 2 January 2008: on the Live Desk, in
`trades recommend`, the forward journal and every backtest that starts after early 2009, so all of them check on
the same days. A backtest that starts earlier (or with blank dates in the Lab) loads more history and may check
on other days. If a provider's history begins later than 2008, its first bar is used, in every view alike.

### Modern quant methods

The tools of today's systematic funds and prop desks. They adapt where the classic rules are fixed, and that
flexibility makes them easier to overfit, so each one names the classic rule it refines: race the two in the
simulator and see whether the extra machinery pays for itself.

| Strategy | What it adds | Refines | Evidence | Key reference |
| --- | --- | --- | --- | --- |
| Multi-horizon trend (CTA signal) | Three fast/slow EMA gaps, volatility-normalised and passed through a response curve that fades overstretched moves; a continuous position | Time-series momentum | Moderate | Baz et al. (2015), Man Group; Lim, Zohren & Roberts (2019), *JFDS* |
| Statistical arbitrage (factor residuals) | Strips out each stock's market-driven moves, models the residual as an Ornstein-Uhlenbeck process, trades s-scores market-neutral with beta hedges | Short-term reversal | Moderate | Avellaneda & Lee (2010), *Quant. Finance*; Khandani & Lo (2011) |
| Residual momentum | Ranks stocks by the t-statistic of their stock-specific return, so a high-beta rally doesn't count as momentum | Cross-sectional momentum | Moderate | Blitz, Huij & Martens (2011), *J. Empirical Finance* |
| Pairs trading (Kalman filter) | The hedge ratio is a hidden state updated every bar; trades when the one-step forecast error is unusually large | Pairs trading (cointegration) | Practitioner | Chan (2013); Elliott, van der Hoek & Malcolm (2005), *Quant. Finance* |
| Regime switching (hidden Markov model) | Two-state Gaussian HMM fitted by EM (Baum-Welch), refitted monthly, filtered forward; invested while the calm regime is likely | Trend filter (10-month SMA) | Moderate | Hamilton (1989), *Econometrica*; Ang & Bekaert (2002), *RFS*; Nystrup et al. (2018) |
| Machine-learning ranker (walk-forward ridge) | Pooled cross-sectional ridge regression on ten price features, purged walk-forward retraining, live information coefficient | Cross-sectional momentum | Experimental | Gu, Kelly & Xiu (2020), *RFS*; Krauss, Do & Huck (2017); López de Prado (2018) |

The strategy panels explain each decision in the method's own terms: the s-score and mean-reversion time of a
residual, the filter's current hedge ratio and forecast error, the probability of the calm regime, or the model's
predicted relative return with the features driving it.

| Library: modern quant methods | Simulator: modern vs classic through a crash |
| --- | --- |
| ![Library filtered to modern quant methods](docs/screenshots/modern-methods.jpg) | ![Results of a modern-vs-classic race](docs/screenshots/modern-vs-classic.jpg) |

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
works without an internet connection or API key. A short first-run guide asks which prices to use (Yahoo needs no
key), your account size, account type and tax bracket, and whether to show the essentials (Today, Portfolio, Track
Record, Library, Settings) or everything; all of it can be changed later under **Settings**.

**Today** is the home screen: what the Live Desk suggests for your watchlist and how much of the account that adds
up to, whether those calls are proven yet (from the forward test), and a one-click **"Is this better than just
buying SPY?"**: the Live Desk's consensus under your settings, on your watchlist or the nine sector ETFs, backtested
from 2010 against SPY after costs and your taxes, with the probability that it really grows faster. It is the
Strategy Lab's backtest with nothing left to choose, so it is logged in the research log like any other.

**Portfolio** imports your real holdings from a broker's CSV export, read-only: Schwab (Accounts -> Positions ->
Export), Fidelity (Positions -> Download), Vanguard (Balances and holdings -> Download) or Robinhood (account
activity report; positions are rebuilt from buys and sells at average cost, without splits or transfers). Other
files work if they have a symbol column and a quantity or shares column. Re-importing an account replaces it.
Each account is marked taxable or tax-advantaged (guessed from its name, e.g. "Roth IRA"; correct it on the page).
Holdings are stored with your settings, included in backups, and never sent anywhere.

Or with Docker:

```bash
docker compose up --build                             # http://127.0.0.1:8000
```

For development with hot reload: run `trades serve` and, in another terminal, `cd web && npm run dev`
(then open http://localhost:5173). Common tasks are also in the `Makefile` (`make serve`, `make dev`, `make test`).

## Put it online (Render)

Trades is a server that has to keep running: it streams prices and simulator bars to your browser over WebSockets
and keeps simulations going between your clicks. Static and serverless hosts such as Vercel or Netlify can't run it
(a Vercel deploy shows a 404 page), but any host that runs a container can. This repository includes a blueprint
for [Render](https://render.com)'s free plan:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/jackr930/Trades)

1. Click the button and sign in to Render with your GitHub account.
2. When Render asks for `TRADES_PASSWORD`, choose a long password. Anyone who has it can use the app and change its
   settings.
3. Wait for the first build (about five minutes), open the `onrender.com` address Render shows, and log in.

Good to know about the free plan:

- The service sleeps after 15 minutes without visitors; the next visit takes about a minute to wake it up.
- It gets a fraction of a CPU core, so backtests and simulations run several times slower than on a laptop.
- There is no persistent disk: settings, the research log and practice history reset whenever the service restarts
  or redeploys. To make choices stick, set them as environment variables in the Render dashboard, for example
  `TRADES_PROVIDER=yahoo` to start on real market data, or `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` for Alpaca.
  **Settings → Backup and restore** downloads everything except API keys as one JSON file and loads it back after a
  restart (restoring adds missing log entries and never deletes any). For a disk instead, switch to a paid instance
  type and uncomment the `disk:` lines in `render.yaml`; the app keeps its files in `/data`.
- The Track Record page reads the journal the workflow commits. A deployed copy never sees those later commits, so set
  **Settings → Track record source** to `https://raw.githubusercontent.com/<you>/Trades/<branch>/journal` (only
  `raw.githubusercontent.com` addresses are accepted). For a private repository, set `TRADES_JOURNAL_TOKEN` to a
  read-only GitHub token (fine-grained, Contents: read). The page caches what it reads for ten minutes.

Other container hosts work the same way with the Dockerfile. The server listens on `$PORT` when it is set. To reach
it under another host name, add the name to `TRADES_ALLOWED_HOSTS` and set `TRADES_PASSWORD`: once outside host
names are allowed, Trades refuses to serve anything without a password. Set `TRADES_SECRET_KEY` to a long random
string so logins survive restarts. While nobody has the Live Desk open, the live feed pauses to save CPU and data
quota, and resumes as soon as someone does.

## Connecting real-time market data

| Provider | Key needed | Latency | Notes |
| --- | --- | --- | --- |
| **Yahoo Finance** (via `yfinance`) | No | Near real-time, polled | Decades of daily history; intraday limited to 7-60 days. Unofficial API, for personal use. |
| **Alpaca Market Data** | Free account | Real-time, streamed | Free plan uses the IEX exchange feed; a paid plan unlocks the consolidated SIP feed. The data provider uses only Alpaca's *data* API; the optional [paper trader](#paper-trading-alpaca-paper-account-only) uses only the *paper* trading API. |
| **CSV files** | No | Static | Put `SYMBOL.csv` files (Date, Open, High, Low, Close, Volume) in `~/.trades/data`. |
| **Synthetic** | No | Simulated | Regime-switching GARCH market with jumps; not real data. |

To use Alpaca, create a free account at [alpaca.markets](https://alpaca.markets), generate a *paper trading* API
key (data access works with it), and paste the key ID and secret into **Settings**. Alternatively, set the
`APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` environment variables.

Settings, including API keys, are stored locally in `~/.trades/settings.json` (override with `TRADES_HOME`) with
file mode 0600. Keys are masked whenever the UI reads them back. The server listens on `127.0.0.1` by default and
needs no login there; to make it reachable from other machines, use the password-protected setup in
[Put it online](#put-it-online-render).

## Your account: costs and taxes

**Settings -> Your account** holds one account profile, the default for the Strategy Lab, the strategy simulator,
practice mode and the Live Desk:

- **Starting equity** (from $100 everywhere, including the simulators);
- **Fractional shares** on or off: the Live Desk suggests fractional share counts when on, whole shares (rounded
  down) when off, and backtests and simulations trade the same way;
- **Account type**: taxable, or tax-advantaged (an IRA or 401(k), which pays no tax as it goes);
- **Short- and long-term tax rates**, 22% and 15% by default. These are estimates: replace them with your own
  federal bracket. An optional flat **state rate** (0 by default) is added to both;
- **What idle cash earns**: T-bill returns (BIL) by default, or nothing.

After-tax results are computed from a backtest's fills afterwards; the engine is unchanged. Every fill records the
gain it realized and how long the shares it closed had been held. Each calendar year, gains are split into
short-term (held one year or less) and long-term, netted, with any net loss carried forward, and tax is charged on
the year's last bar. Gains on short sales are always short-term, however long the short was open, and commissions
reduce taxable gains in the year they are paid. Buy-and-hold is taxed only on what it realizes (normally nothing), so the Lab also shows an
**"after tax, if sold at end"** line for both: that is the fair comparison. Simplifications: average cost instead
of tax lots, no wash-sale rule, no $3,000 offset against ordinary income, flat federal and state rates (no
brackets or local taxes), and dividends are not taxed separately: adjusted prices fold them into returns, which
slightly flatters whichever side holds more stock for longer, usually buy-and-hold. A tax-advantaged account shows
after-tax equal to pre-tax.

After every Lab backtest, the **cost sensitivity** card reruns it at 2x and 4x the slippage and commissions and says whether the
strategy still beats buy-and-hold at double costs (after tax, if sold at the end, in a taxable account).
`trades backtest` prints the after-tax lines too.

## How results are kept honest

Backtests go wrong in predictable ways, and the engine is built to avoid the common traps:

- **No look-ahead bias.** Signals are computed from data up to a bar's close and filled at the *next* bar's open
  (or close). The test suite truncates the data at several points and checks that every strategy's past signals,
  indicators and position sizes never change when future bars are added.
- **Costs are always on.** Slippage (default 5 bps per fill), optional commissions and short-borrow fees. The Lab
  warns you when costs eat a large share of gross profits and reports the annual cost drag (costs per year as a
  share of average equity).
- **What is researched is what is recommended.** The Live Desk's consensus and the `consensus` strategy share one
  decision function and see the same bars: every strategy runs on the dates all watchlist symbols share, as in a
  backtest (so a recently listed symbol moves everyone's first bar, and the Live Desk says so). Tests check they
  agree at several cut points, including when one symbol starts later and another is missing a day. Daily data
  always starts on 2 January 2008, with or without a start date, so periodic rules re-check on the same days.
- **Statistics that tell you how much to trust a number.** Each backtest reports the Probabilistic Sharpe Ratio
  (Bailey & López de Prado 2012) alongside the Sharpe ratio. Parameter searches report the Deflated Sharpe Ratio
  (2014), and walk-forward analysis compares in-sample with out-of-sample results.
- **Models only learn from the past.** The hidden Markov model is refitted on trailing returns and run forward
  between refits. The machine-learning ranker trains only on rows whose target return was fully known before the
  prediction date (the purge gap), retrains on a rolling schedule, and reports its live out-of-sample information
  coefficient next to every prediction. The no-look-ahead test covers these models like every other strategy.
- **Plain-language warnings** for too few trades, short samples, probably-overfit results, synthetic data, and
  survivorship bias when you pick today's symbols to test the past.
- **Benchmarks everywhere.** Every backtest and simulator session is compared with buy-and-hold over the same period.
- **Idle cash earns T-bills, and Sharpe ratios are in excess of them.** On real daily data, cash a strategy is not
  using earns what a 1-3 month T-bill ETF (BIL) returned, borrowed cash pays that plus the margin spread, and Sharpe
  ratios subtract it. Without this, a strategy that is sometimes out of the market is penalised (cash earning 0%
  against a fully invested benchmark) and one sitting in cash looks safe for free. Switch it off in Settings or with
  `--no-cash-yield`.
- **"Did it really beat buy-and-hold?" is measured directly.** "Prob. beats buy & hold" is the share of 1,000
  block-bootstrap resamples of the same days (21-day blocks) in which the strategy compounded faster; "P(Sharpe > 0)"
  only compares a strategy with doing nothing.
- **A research log counts your tries.** Every Lab backtest, grid search and `trades backtest` is logged in
  `$TRADES_HOME/trials.jsonl`, and each result shows its Deflated Sharpe Ratio given every distinct configuration
  you have tested on the same symbols, so the tenth idea you tried is judged as the tenth.
- **Success is defined in advance.** `journal/decision_rule.json` fixes what would count as beating buy-and-hold for
  the backtest, the forward journal and the paper account. `scripts/consensus_check.py` and `journal/REPORT.md` say
  PASS, FAIL or NOT YET against it, and the report warns if the rule changed after the journal's first row.
- **One backtest is one path.** After each Lab backtest, "How robust is it?" shows the share of rolling 3-year
  windows in which the strategy beat its benchmark, how often you would be ahead today had you started in any
  quarter, returns in bull, correction and bear markets and in calm versus volatile stretches, and the 5th to 95th
  percentile of CAGR and drawdown across 1,000 block-bootstrap resamples.
- **Choose a fair benchmark and a fair universe.** Compare with equal-weight buy-and-hold of the same symbols, SPY,
  a monthly-rebalanced 60/40 (SPY/AGG) or an all-weather style mix, and test on sets nobody picked with hindsight:
  the sector SPDRs, 17 country ETFs, a multi-asset set, or the largest US companies as of January 2010 (a frozen
  list; all of them still trade, so some survivorship bias remains).
- **Trade less, if it helps.** "Skip trades under" sets the no-trade band (0.5% of equity by default): re-sizing
  trades smaller than that are skipped. A wider band cuts turnover and costs. The pre-registered decision rule
  includes a 5% band as a variant, judged with a Bonferroni-raised bar because testing two versions finds more luck
  than testing one.
- **Leverage and shorting are not free.** Short positions pay a borrow fee (0.25%/yr by default), borrowed cash pays
  margin interest (2%/yr above cash, since returns are reported in excess of cash), and fills can't push gross
  exposure past the strategy's leverage cap after an overnight gap.
- **Live data stays clean.** Pre-market and after-hours prints never alter daily bars, a bar that is still forming
  is never fed into cached statistics, and forward tests trade only completed bars.

## Command line

```bash
trades strategies -v                                  # list strategies with references
trades backtest tsmom SPY --provider yahoo --start 2010-01-01
trades backtest pairs_trading KO PEP --provider yahoo --short
trades backtest consensus SPY QQQ AAPL MSFT --provider yahoo --start 2010-01-01 --benchmark SPY
trades backtest consensus XLB XLE XLF XLI XLK XLP XLU XLV XLY --provider yahoo --start 2010-01-01 \
    --walk-forward --grid weighting=equal,by_category    # re-choose the weighting each year on past data
trades backtest consensus SPY EFA EEM AGG TLT GLD DBC --provider yahoo --start 2010-01-01 \
    --benchmark 60_40 --min-trade 0.05                   # a 60/40 benchmark and a 5% no-trade band
trades recommend AAPL MSFT NVDA --provider yahoo -v   # print recommendations
trades journal record && trades journal score         # forward journal (see below)
trades journal health && trades journal digest        # check it for problems; a weekly summary
trades paper                                          # paper-trading dry run (see below)
trades serve --port 8000
```

`trades backtest consensus` uses your saved Live Desk settings unless you override them with `--param`, for
example `--param weighting=by_category`. `--benchmark SPY` adds SPY buy-and-hold over the same bars next to the
equal-weight buy-and-hold of your symbols; `--benchmark 60_40` or `--benchmark all_weather` adds a monthly
rebalanced mix instead. `python scripts/consensus_check.py --sets sectors,countries,multi_asset,large_caps_2010`
runs the comparison on the hindsight-free sets.

Does the consensus beat buy-and-hold after costs and taxes? `python scripts/consensus_check.py --provider yahoo`
prints a Markdown table for the default Yahoo watchlist and for the nine original sector SPDR ETFs (a set nobody
picked for having won): plain backtests from 2010 with both weightings, a walk-forward test that re-chooses the
weighting each year using only earlier data, and SPY and equal-weight buy-and-hold over the same bars, with
after-tax CAGR and the probability of beating SPY. It ends with the verdict of the pre-registered decision rule.
**Edit `journal/decision_rule.json` before you run it** if you want different thresholds: its git history is the
record of what you committed to in advance.

## Forward journal

A backtest, however honest, tests rules you chose after seeing the history. The forward journal records the Live
Desk's recommendations *before* their outcome exists, every trading day, and scores them once it does.

- **The experiment is a committed file.** `journal/experiment.json` fixes the watchlist, the strategies and pairs,
  how votes are combined, the account's risk settings, the benchmark, the data providers and the first bar of
  history (2 January 2008, as on the Live Desk). Every row carries the **experiment id** (a short hash of that
  definition) and the **code version** (`git rev-parse HEAD:trades`, the hash of the `trades` package, which only
  changes when the code does). Changing a rule starts a new experiment; the scorer never mixes two.
- **`trades journal record`** runs after the close. On weekends and holidays, or before the session has closed,
  it exits without writing. It uses completed bars only, and if the provider's latest bar is not today's session it
  retries with backoff (1, 3 and 10 minutes), tries the next provider, and finally fails without writing anything:
  stale data is never logged as today's. Rows are appended to `journal/recommendations.csv` once per (session,
  symbol, experiment), so rerunning is harmless. Columns: run time (UTC), session date, symbol, close, score,
  label, bullish/bearish/neutral counts, each strategy's vote as compact JSON (`null` while warming up), the
  suggested side, weight and stop, experiment id, code version and data provider.
- **`trades journal score`** writes `journal/REPORT.md`. A row's outcome is the symbol's return from the next
  session's open (when the backtest engine would have filled) to the close 5 and 21 sessions later, minus SPY's
  return over the same window; rows whose window has not passed are left out. Outcomes are averaged within each
  day first, because symbols on the same day move together, and only days at least one horizon apart are used, so
  overlapping windows are not counted twice. Per label it reports the number of independent days, the mean excess
  return, the hit rate against SPY and the t-statistic; per strategy, the mean excess return on days it voted
  bullish versus days it voted neutral or bearish. Below about 60 independent days it says plainly that it is
  too early to conclude anything. At 21 sessions that takes about five years: forward evidence is slow.
- **A pre-registered decision rule** (`journal/decision_rule.json`, separate from the experiment so editing it does
  not start a new experiment) says what would count as success: by default, Buy and Strong buy calls must beat SPY
  over 21 sessions on at least 60 independent days with a t-statistic of at least 2. The report shows PASS, FAIL or
  NOT YET, the rule's registration date from git, and a warning if it was changed after the first journal row.
- **`trades journal score`** also writes `journal/track_record.json`, the same numbers for the app's **Track
  Record** page and the weekly digest, with the current experiment id and the health check's findings.
- **`trades journal health`** looks for problems and exits with status 1 if it finds any: a session among the last
  five with no rows (a missed session cannot be recorded afterwards, since that would be look-ahead, so the note
  drops off after five sessions), a latest session missing a watchlist symbol, paper orders still not final after
  their trade date, and paper fills more than 50 bps from the open.
- **`trades journal digest`** prints a short plain-English summary of `journal/track_record.json`.
- **GitHub Actions** (`.github/workflows/journal.yml`) runs record, score and the health check at 22:15 UTC on
  weekdays (after the close in both EDT and EST) and commits the results as `github-actions[bot]`. Scheduled
  workflows only run on the repository's default branch. Yahoo needs no key; if it fails, the journal falls back to
  Alpaca's data API with the `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` repository secrets (Settings -> Secrets
  and variables -> Actions). You can also start it by hand from the Actions tab.
- **Alerts and the weekly digest arrive by email, through GitHub issues.** When the journal or paper job fails, or
  the health check finds a problem, the workflow opens an issue labelled `journal-health` (or comments on the one
  already open) with a link to the run. Close it once fixed. To get a digest every Friday, set the repository
  variable `DIGEST_ISSUE` to `true` (Settings -> Secrets and variables -> Actions -> Variables): it is posted as a
  comment on an issue labelled `journal-digest`. GitHub emails you about both as long as you watch the repository
  (Watch -> All activity, or Custom -> Issues).

```bash
trades journal record     # after the close; does nothing on weekends and holidays
trades journal score      # rewrites journal/REPORT.md and journal/track_record.json
trades journal health     # exit status 1 if anything looks wrong
trades journal digest     # the weekly summary, as Markdown
```

## Paper trading (Alpaca paper account only)

`trades paper` trades the journal's experiment (by default the Live Desk consensus) on an
[Alpaca](https://alpaca.markets) **paper** account, so the forward test includes real order handling: queued
orders, fills, partial fills and slippage. It talks only to `https://paper-api.alpaca.markets`. That address is
hard-coded, every request is checked against it, redirects are not followed, and there is no setting, flag or code
path for Alpaca's live endpoint. **Moving to real money is out of scope for this app.**

**Before you start, reset your Alpaca paper account's balance to the amount you would really trade** (in Alpaca's
dashboard), so that position sizes, whole-share rounding and the costs per trade look like yours. It uses the same
Alpaca key as the data provider (Settings, or `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`); generate it from the
*paper* account.

Each run, in the evening after the close:

1. **Reconciles** earlier orders: their status and Alpaca's fill price, next to the price the backtest engine would
   have assumed (that session's open moved against you by the slippage setting, 5 bps by default).
2. **Computes target weights** from completed bars, exactly as a backtest does.
3. **Reads the paper account's actual positions and equity** from Alpaca (it never assumes earlier orders filled)
   and orders the difference, rounded to whole shares unless the experiment's account allows fractional shares,
   skipping re-sizing trades under 0.5% of equity as the backtest engine does.
4. **Matches the engine's next-open fill.** Whole-share orders go to the opening auction (`time_in_force` "opg").
   Alpaca does not accept fractional quantities in the auction, so those go as day market orders queued for the
   open. Alpaca accepts auction orders before 9:28am ET or after 7:00pm ET, so orders are only sent in that window
   (the GitHub job runs at 00:30 UTC, which is 8:30pm EDT / 7:30pm EST). Alpaca allows no fractional short sales,
   so shorts are whole shares, and a position is closed before it flips. Note that Alpaca's *paper* account fills
   auction orders like ordinary market orders, so its "slippage" is only a rough check.
5. **Checks the guardrails before sending anything**: a kill switch (Settings, or `trades paper halt`; re-read from
   disk before each order, so switching it on in the app stops a run already going), a maximum daily loss (no orders if the paper equity fell more than 3% since the prior
   close; configurable), long-only unless the experiment allows shorting, gross exposure at most 100%, only the
   experiment's watchlist, a maximum number of orders per run (20), and no earlier orders still open. If any check
   fails, nothing is sent.

```bash
trades paper              # dry run (the default): prints the orders
trades paper --submit     # sends them to the paper account
trades paper halt --reason "taking a break"     # kill switch on; also writes journal/PAPER_HALTED
trades paper resume       # kill switch off
```

Every order's `client_order_id` is built from the session date, symbol and experiment id, so a rerun finds the
order it already sent and never orders twice. Sent orders are logged to `journal/paper_orders.csv` with the
engine's assumed price and, once known, Alpaca's fill; `trades journal score` adds the real slippage in basis
points (against the assumed 5) to `journal/REPORT.md`.

The journal workflow has a second job, `paper`, that runs at 00:30 UTC on weekday evenings. It sends orders only
when the repository *variable* `PAPER_SUBMIT` is `true` (Settings -> Secrets and variables -> Actions ->
Variables); otherwise it is a dry run. The Alpaca keys go in the `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`
*secrets*; without them the job skips itself. To stop it, run `trades paper halt` and commit
`journal/PAPER_HALTED` (or create that file on GitHub). The Settings page's kill switch and limits live on the
machine running the app, so they do not reach this job: on GitHub only `journal/PAPER_HALTED`, `PAPER_SUBMIT` and the
experiment's own account settings apply, with the default limits (3% daily loss, 20 orders). A run that had any
order refused, or was stopped by the kill switch, exits with an error so the failure is visible.

## Architecture

```
trades/
  core/        NYSE calendar, timeframes, indicators, statistics (PSR/DSR, ADF, cointegration), ledger
  data/        providers (synthetic, Yahoo, Alpaca, CSV), bar normalisation, cached data service
  strategies/  strategy framework + library, position sizing
  backtest/    event-driven engine, metrics, optimisation (grid + Deflated Sharpe, walk-forward)
  advisor/     ensemble recommendation engine with evidence and risk-based sizing
  journal/     forward journal: record recommendations after the close, score them, track record, health check
  paper/       paper trading on Alpaca's paper API only (guardrails, idempotent orders, reconciliation)
  arena/       strategy simulations: steerable simulated market, replay/real-time feeds, strategy agents
  sim/         paper broker, practice sessions, behavioural scorecard
  live/        live service: quotes -> forming bars -> recommendations -> WebSocket
  api/         FastAPI REST + WebSocket, serves the built UI
web/           React + TypeScript + Vite UI (charts: TradingView lightweight-charts)
tests/         pytest suite
```

Data flows one way. A provider supplies bars; a strategy turns them into signals; the sizing layer turns signals
into target weights; an `ExecutionEngine` turns target weights into fills, one bar at a time. The backtester, the
strategy simulator's agents and the practice mode's strategy "ghosts" all use that path, and the live recommender
uses the same signals and sizing, so what you research is exactly what gets simulated and recommended.

The REST and WebSocket API is documented at `http://127.0.0.1:8000/docs` while the server runs.

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
- Simulations and practice sessions live in memory (completed practice summaries are saved), and the app is
  single-user. Real-time forward tests run while the server runs; they poll the provider rather than stream.
- Published strategies weaken after publication (McLean & Pontiff 2016). Treat them as disciplined baselines, not
  guarantees.

## Disclaimer

For education and research only. Nothing here is investment advice or a recommendation to buy or sell any
security. Past performance, and especially backtested performance, does not predict future results. Trading
involves risk of loss.
