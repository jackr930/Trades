---
id: STRAT-003
name: Market intraday momentum on SPY
version: 1
status: draft # draft | backtesting | paper | live | retired
frozen: null # set by `trades spec freeze` (Jack only)
rules_hash: null # set by `trades spec freeze`

strategy:
  id: intraday_momentum
  params: {use_r12: false, min_abs_r1: 0.0, allow_short: true, min_vol_rank: 0.0}
  sizing: {method: fixed, allocation: 1.0, max_leverage: 1.0}

universe:
  symbols: [SPY]
  survivorship_free: true # one index ETF that traded throughout

data:
  provider: alpaca # check how far back your Alpaca data plan reaches; Massive works too
  timeframe: 30m
  adjustment: split

periods:
  train: [2016-01-04, 2021-12-31]
  validation: [2022-01-03, 2023-12-29]
  test: [2024-01-02, 2026-09-30]

costs: {half_spread_bps: 2.0, slippage_bps: 1.0}

expected:
  trades_per_month: [19, 22]
  win_rate: [0.50, 0.58]
  avg_r: [0.0, 0.1]
  holding: the last half hour of each session

invalidation:
  rolling_trades: 60
  max_drawdown_vs_backtest: 1.5
  max_slippage_vs_model: 2.0
  slippage_trades: 20

parameters: {use_r12: [false, true], min_vol_rank: [0.0, 0.5]}
one_trade_per_day: true
---

# STRAT-003: Market intraday momentum on SPY

**Status:** draft. **Frozen:** not yet. The playbook's *good simple candidate*: one trade a day, cheap
data, a peer-reviewed effect, and a thin edge.

## 1. Hypothesis

SPY's return from the previous close to 10:00 predicts the sign of its return over the last half
hour of the session (15:30-16:00) well enough to pay for one round trip a day.

## 2. Economic rationale

Gao, Han, Li and Zhou (Journal of Financial Economics 129(2), 2018, pp. 394-414) find that the first
half-hour return predicts the last half-hour return (predictive R-squared 1.6%; out of sample 1.4%,
2.0% adding the 12th half-hour), stronger on volatile and news days. Their explanations: traders who
rebalance infrequently and trade late in the day (Bogousslavsky 2016), and late-informed traders.
On the other side: liquidity providers into the close. The effect was found in 12 of 16 developed
markets but not in Hong Kong or Singapore, and a related cross-sectional US effect has declined by
about 75%: assume it has weakened.

## 3. Universe

SPY only; no survivorship issue.

## 4. Data

30-minute bars (the paper's frequency), split-adjusted, regular session in New York time (13:00 on
early closes), stored in UTC. The first half-hour return runs from the previous session's close to
the close of the 09:30-10:00 bar, so it includes the overnight move. Train 2016-01-04 to
2021-12-31, validation 2022-01-03 to 2023-12-29, locked test 2024-01-02 to 2026-09-30.

## 5. Entry rules

At 15:30 (the close of the 15:00-15:30 bar; 12:30 on early-close days): if the first half-hour
return was positive, buy SPY at the open of the last half-hour bar; if negative, sell short. With
`use_r12`, trade only when the 15:00-15:30 return has the same sign.

## 6. Exit rules

Exit in the closing auction (a market-on-close order). No stop: the holding period is thirty
minutes. Flat every night.

## 7. Sizing

100% of equity, no leverage (risk/limits.yaml caps gross exposure at 1x). Risk per trade is
whatever SPY moves in its last half hour, typically 0.1-0.3%.

## 8. Cost model

$0 commission. A 2 bp half spread and 1 bp of slippage per fill: about 6 bps a round trip, every
day. The closing auction prints a single price, so its real cost may be lower: the paper trader will
tell. The checklist reruns everything at 2x.

## 9. Expected behaviour

About 20 trades a month; a hit rate just above 50%; an average return per trade of a few basis
points before costs, so a few basis points of cost decide the result; holding thirty minutes.

## 10. Invalidation criteria

Retire it if expectancy over the last 60 trades is below zero, drawdown exceeds 1.5x the worst
backtest drawdown, or fills cost more than 2x the model over 20 trades.

## 11. Parameters

With or without the 12th half-hour agreeing; volatility filter off or above the median (the paper
finds the effect stronger on volatile days): four variants, each counted.
