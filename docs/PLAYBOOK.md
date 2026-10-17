# The playbook, as this repository runs it

A strategy reaches real money only after four stages, and each stage ends with a written test.
Most strategies fail one of them. That is the system working: a failure costs a few hours of
compute instead of a few thousand dollars.

| Stage | What happens | Command | Exit test |
| --- | --- | --- | --- |
| 1. Spec | Write the rules down before any code or data | `trades spec new` → **Jack:** `trades spec freeze` | The spec is frozen and committed |
| 2. Research | Backtest on train and validation data, net of costs, then stress it | `trades research STRAT-xxx` | All 14 acceptance checks pass, the locked test included |
| 3. Paper | Trade it on an Alpaca paper account for 8 to 12 weeks | **Jack:** `trades paper --submit`; `trades track` | Paper tracks a replay of the same bars |
| 4. Gate | Ten checks before any real money | `trades gate` | All ten pass. Passing turns nothing on |

The web app's **Pipeline** screen shows the same four stages for every spec, the next action for
each, the kill switch and the alert feed.

## Who does what

Claude Code may write code, specs (as drafts), tests and docs, run research and tracking, and
review. A few acts are Jack's alone, because each one is a commitment that must not be made by
the thing being tested. The hooks in `.claude/` block Claude from all of them.

| Only Jack | Why |
| --- | --- |
| `trades spec freeze` | Freezing fixes the rules before the test. Whoever freezes vouches that nothing was tuned on the test data. |
| `trades research STRAT-xxx --open-locked-test` | The locked test is opened once. A second look is a second chance to fool yourself. |
| `trades paper --submit` | Sending orders, even paper ones, is a decision. |
| `trades paper drill ...` | A drill proves *you* can stop the system. |
| `trades gate STRAT-xxx --attest live_config\|broker_account` | Only you can confirm you reviewed the live files and own the right account. |
| `trades resume` | Turning the kill switch off is a judgment that the cause is fixed. |
| Editing `risk/limits.yaml`, `config/backtest.yaml`, `config/live.yaml` | Lowering a bar so a strategy passes is exactly what must never happen quietly. |

Records are never edited or deleted: `runs/` (research) and `paper/` (paper trading) are the
evidence the gate reads.

## 1. Write it down: the spec

```bash
trades spec new STRAT-004 vwap-reversion    # specs/STRAT-004-vwap-reversion.md, a draft
trades spec show STRAT-004
trades spec freeze STRAT-004                 # Jack only
trades spec version STRAT-004                # a frozen spec changes only as a new version
```

A spec is a Markdown file: the front matter is what the code runs (strategy, parameters,
symbols, timeframe, data provider, costs, dates), and the body says why the edge should exist,
how it fails and what would retire it. Write the economic rationale in one sentence; if you
cannot, there is no strategy yet.

**The split** is fixed in the spec: train on the earliest 60% of the history, validate on the
next 20%, and lock the most recent 20%. The locked dates never change, and a frozen spec's
rules never change: `trades spec version` makes a new draft instead.

## 2. Research it: the acceptance checklist

```bash
trades research STRAT-004                   # train and validation only; writes runs/<id>/
trades research STRAT-004 --provider synthetic   # rehearse the pipeline on the demo market
trades runs list STRAT-004 && trades runs show <id>
trades runs reproduce <id>                  # rerun and compare: same data hash, same results?
```

Every run writes `runs/<id>/`: the report, the checklist, the configuration, the git commit, the
seed, a hash of the data, and `variants.json`, which lists every parameter variant ever tested
for the spec. The variant count matters: the more variants you try, the more likely the best one
is luck, and the deflated Sharpe ratio (check 7) charges for every one of them.

A spec passes only when all fourteen pass. The thresholds are in `config/backtest.yaml`; they are
the playbook's deliberately conservative choices for a small account, not industry standards.

| # | Check | Passes when |
| --- | --- | --- |
| 1 | Spec frozen and committed before testing | yes, dated |
| 2 | Real, survivorship-free data; look-ahead tests pass | yes |
| 3 | Out-of-sample trades | at least 200 (100 for one-trade-per-day systems) |
| 4 | Net expectancy at base costs | positive, t-statistic at least 2 |
| 5 | Net expectancy at **2x** costs | at least 0 |
| 6 | Walk-forward Sharpe, net, stitched out-of-sample | at least 0.75 |
| 7 | Deflated Sharpe probability, counting every variant | at least 0.95 |
| 8 | Probability of backtest overfitting (when a grid was searched) | below 0.2 |
| 9 | Locked test | same sign, at least 50% of the validation Sharpe |
| 10 | Parameters moved ±20% | keep at least 70% of the performance |
| 11 | Monte Carlo 95th-percentile drawdown | within your loss budget |
| 12 | Regimes | profitable in at least 60% of years, no year over 50% of the profit |
| 13 | Capacity | trade size below 1% of the average 1-minute volume at entry |
| 14 | Reproducible from `runs/` | yes |

Some terms:

* **Expectancy** is the average net result per trade, after costs. The t-statistic says how far
  it is from zero in units of its own noise; 2 is roughly "unlikely to be luck".
* **Walk-forward**: choose parameters on two years, trade the next six months, roll forward,
  and judge only the stitched out-of-sample months.
* **Deflated Sharpe** (Bailey and López de Prado, 2014): the probability that the Sharpe ratio is
  above zero after allowing for how many variants were tried, fat tails and sample length.
* **The locked test** stays sealed (no run sees its bars) until checks 1 to 8 and
  10 to 14 pass. Then Jack opens it once. A failed locked test means rejection, not retuning.

Two built-in controls keep the harness honest:

* **The null test** (part of check 2). Every run also trades the strategy on a random walk,
  where no edge can exist. A strategy that makes money there before costs (|t| of 2 or more) is
  measuring a bug, usually a peek at the future.
* **The synthetic market's bridge artifact.** The demo market draws each day's intraday path as
  a Brownian bridge between that day's open and close, so the bars agree with the daily bars. A
  path pinned to a known close leaks the day's direction into its first minutes, so intraday
  momentum rules look better there than they should: the opening range breakout shows t = 2.3
  before costs on the bridge and about zero on a random walk (and costs turn its +47% gross into
  −45% net). Synthetic data is for rehearsing the pipeline, never for evidence; check 2 fails on
  it by design. `SyntheticProvider(intraday_paths="random_walk")` gives the edgeless version.

After every run, ask the `backtest-auditor` agent to audit it (look-ahead, survivorship, costs,
variants, reproducibility) before anyone freezes or promotes anything.

## 3. Paper trade it

```bash
trades paper status          # the pre-market check: kill switch, account, positions, heartbeat
trades paper                 # dry run: prints the plan, sends and records nothing
trades paper --submit        # Jack only; refused for a spec whose latest run did not pass
trades track                 # paper against a replay of the same bars
trades halt --reason "..."   # kill switch on: nothing is traded
trades paper flatten         # cancel everything, close everything, kill switch on
```

The paper trader uses the backtest's own decision path: the same strategy, sizing, risk engine
and target-to-orders rule (`engine/live.py`, `execution/orders.py`). It talks only to Alpaca's
paper endpoint, which is hard-coded; there is no setting for a live one. Daily specs run once an
evening and send opening-auction orders; intraday specs run through the session with brackets
at the broker and close out before the bell. Running it unattended (a small VM, docker compose,
systemd timers, a heartbeat, Telegram alerts and a backstop that flattens if the runner dies) is
in [deploy/README.md](../deploy/README.md).

`trades track` replays the paper period through the backtest and compares:

| Measure | Target | What a miss usually means |
| --- | --- | --- |
| Signal match | at least 95% of decisions | the paper path is not the backtest's path (data, timing, a bug) |
| Daily P&L correlation | at least 0.8 | fills or timing differ from the model |
| Paper expectancy | positive, at least 50% of the backtest's | the edge, or the cost model, was optimistic |
| Fill cost | within 1.5x the cost model | spreads and slippage are worse than assumed: recalibrate `costs:` |

## 4. The gate

```bash
trades gate STRAT-004
trades paper drill halt          # Jack: each drill on paper, confirmed flat
trades paper drill flatten
trades paper drill daily-loss
trades paper drill revoke-key
trades paper drill alert         # a test alert must reach you
trades gate STRAT-004 --attest live_config      # Jack
trades gate STRAT-004 --attest broker_account   # Jack
```

| # | Gate | Passes when |
| --- | --- | --- |
| 1 | Backtest checklist | all 14 passed, and the run is committed |
| 2 | Paper duration | 8+ weeks and 50+ trades (12 weeks for daily or one-trade-per-day specs) |
| 3 | Signal match | at least 95%, daily P&L correlation at least 0.8 |
| 4 | Paper expectancy | positive, at least 50% of the backtest's |
| 5 | Fill cost | within 1.5x the cost model |
| 6 | Incidents | no errors in the last 4 weeks, and a test alert reached you |
| 7 | Kill switch drills | all four done on paper |
| 8 | Live config | `config/live.yaml` and `risk/limits.yaml` reviewed line by line by Jack (void if either changes) |
| 9 | Broker account | a margin account that meets your broker's intraday margin rules |
| 10 | Loss budget | the total you can afford to lose, written down |

Passing the gate turns nothing on. **This repository has no real-money order path**, and Claude
never adds one. The gate is the evidence that one is worth building, by a person, with live keys
kept on the trading machine only and never on a machine that runs Claude Code.

## 5. If you go live (outside this repository)

The playbook's starting rules, for reference:

* Tuition money in a separate account ($2,000 to $5,000). Risk 0.25% to 0.5% per trade. Daily loss
  stop 1.5%, weekly 4%, enforced in code. No leverage at first.
* Scale up at most 25% per 50 live trades that keep expectancy at least 50% of the backtest's and
  drawdown inside the Monte Carlo 95th percentile. Halve size after a drawdown beyond the worst
  backtest.
* Retire or pause on: rolling 60-trade expectancy below zero; drawdown over 1.5x the worst
  backtest; slippage over 2x the model for 20 trades; or a broken rationale. Write a post-mortem.
* Routine: 15 minutes before the open (health, data, reconciliation, halts, the economic
  calendar), hands off during the session, 15 minutes after the close, one hour a week (tracking,
  cost calibration, one written decision).
* Records for taxes: monthly broker fill exports, your own trade database with the strategy
  version on every trade, deposits and withdrawals. Wash-sale rules bite repeated day trading of
  the same tickers. Confirm all of it with a CPA.

## Self-checks

The playbook ends each phase with questions. Answer them in writing before moving on; if an
answer is "I'm not sure", that is the next thing to build or read.

**Foundation**
* Without code, how does an order flow from strategy through the risk engine to the broker, and
  which component can stop it?
* What three things guarantee an identical rerun of your first backtest next month?
* What is your first strategy's economic rationale in one sentence?

**Research and paper**
* How many variants did you test, and how did that change the deflated Sharpe?
* Which cost assumption, if wrong by 2x, kills the strategy, and how did paper fills compare?
* If the trader crashed at 10:15 ET with open positions, what happens automatically, and how
  would you know?

**Small live**
* What dashboard number would make you turn the strategy off tomorrow, and is it enforced in code?
* How much of the live-versus-paper difference comes from fills, and how much from signals?
* Could you reconstruct every trade for your CPA if the broker's site were down?

**Product** (see [SELLING.md](SELLING.md))
* Does any sentence on your landing page imply a user will make money? Read it as the FTC would.
* If a customer asks "should I buy NVDA tomorrow", what do your product and support process say?
* Does your product category match your payment processor's current policy text?

**Claude's tools**
* If a malicious prompt in a dependency's README hijacked Claude Code, what is the worst it could
  do to your money? (Here: nothing real. There is no live order path and no live key on the
  machine; the hooks are a second layer, not the boundary.)
* Which tool produces your daily paper report, and where does it read its data?
* If you add "AI insights" to a product, which Usage Policy requirements might apply, and how does
  your design handle them?
