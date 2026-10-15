---
id: STRAT-002
name: Opening range breakout on QQQ (5-minute)
version: 1
status: draft # draft | backtesting | paper | live | retired
frozen: null # set by `trades spec freeze` (Jack only)
rules_hash: null # set by `trades spec freeze`

strategy:
  id: orb
  params: {or_minutes: 5, entry: next_open, stop_mode: range, target_r: 10.0, allow_short: true}
  sizing: {method: stop_risk, risk_per_trade: 0.005, max_leverage: 1.0}

universe:
  symbols: [QQQ]
  survivorship_free: true # one ETF that traded throughout: nothing was dropped from the list

data:
  provider: alpaca # check how far back your Alpaca data plan reaches; Massive works too
  timeframe: 5m
  adjustment: split

periods:
  train: [2016-01-04, 2021-12-31]
  validation: [2022-01-03, 2023-12-29]
  test: [2024-01-02, 2026-09-30] # after the paper's 2016-2023 sample: genuinely out of sample

costs: {half_spread_bps: 2.0, slippage_bps: 1.0, stop_slippage_bps: 5.0}

expected:
  trades_per_month: [18, 22]
  win_rate: [0.18, 0.30]
  avg_r: [-0.05, 0.20]
  holding: intraday, minutes to hours

invalidation:
  rolling_trades: 60
  max_drawdown_vs_backtest: 1.5
  max_slippage_vs_model: 2.0
  slippage_trades: 20

parameters: {target_r: [5.0, 10.0, 20.0], or_minutes: [5, 15]}
one_trade_per_day: true
---

# STRAT-002: Opening range breakout on QQQ (5-minute)

**Status:** draft. **Frozen:** not yet. The playbook calls this the *learning project*: a weak money
candidate whose value is in teaching the harness, the data and the costs.

## 1. Hypothesis

The direction of QQQ's first five minutes predicts the direction of the rest of the session well
enough that a 10R bracket trade earns more than its costs.

## 2. Economic rationale

Overnight news and institutional order imbalances are absorbed over hours, not minutes, so early
order flow can persist. On the other side are traders fading the open and market makers who are
paid the spread. That last point is the problem: Zarattini and Aziz (SSRN 4416622) charged
commission but no spread and no slippage, and Krueger's replication on NQ, SPX, Dow, DAX and FTSE
(September 2026) reproduced the gross edge (+0.131 R on NQ, 23.2% hit rate) but found it net zero
after costs (+0.002 R on NQ; four of five markets negative). Expect to learn that, not to beat it.

## 3. Universe

QQQ only. A single ETF that existed throughout the sample has no survivorship bias.

## 4. Data

5-minute bars from Alpaca (check that your data plan reaches back to 2016; Massive is the
alternative), split-adjusted, regular session
09:30-16:00 New York time (13:00 on early closes), stored in UTC. Train 2016-01-04 to 2021-12-31,
validation 2022-01-03 to 2023-12-29, locked test 2024-01-02 to 2026-09-30. The test period starts
after the paper's sample ends, so it is out of sample for the paper as well as for us.

## 5. Entry rules

At the close of the first 5-minute bar (09:35): if it closed above its open, buy at the open of
the next bar; below its open, sell short; unchanged (a doji), no trade. One trade per day.

## 6. Exit rules

A protective stop at the first bar's low (long) or high (short). A take-profit at 10 times the
entry-to-stop distance, priced from the 09:35 close when the bracket is placed. Otherwise exit in
the closing auction. Flat every night.

## 7. Sizing

Risk 0.5% of equity if the stop is hit (the paper risked 1%), at most 1x leverage (the paper
allowed 4x), within risk/limits.yaml. QQQ's opening range is usually 0.1-0.4% of its price, so 0.5%
risk would want 1.25-5x leverage: **the 1x cap binds on most days**, and the realised risk per trade
is then well under 0.5%. Ask yourself what that does to the R statistics before comparing them with
the paper's.

## 8. Cost model

$0 commission (Alpaca). QQQ's quoted spread is one cent, well under 1 bp of its price, but the
open is when spreads are widest and a market order also moves the price, so this spec uses the
playbook's floor for large-cap ETFs: a 2 bp half spread per fill, 1 bp slippage, and stop fills 5
bps worse; SEC and FINRA fees on sales from config/base.yaml. The checklist reruns everything at 2x.
Krueger's result says this assumption decides the outcome; calibrate it against paper fills.

## 9. Expected behaviour

About 20 trades a month (one a day, minus dojis), a 18-30% hit rate, average R between slightly
negative and +0.2 before the leverage cap, holding minutes to hours.

## 10. Invalidation criteria

Retire it if expectancy over the last 60 trades is below zero, drawdown exceeds 1.5x the worst
backtest drawdown, or fills cost more than 2x the model over 20 trades.

## 11. Parameters

Take-profit 5R, 10R (the paper) or 20R; opening range 5 or 15 minutes: six variants. Each one
tried counts toward the deflated Sharpe ratio.
