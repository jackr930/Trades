---
# Copy to specs/STRAT-NNN-short-name.md (`trades spec new STRAT-NNN short-name` does it for you).
# Everything in this block is what the code runs; the body below says the same in plain words.
id: STRAT-NNN
name: Short descriptive name
version: 1
status: draft # draft | backtesting | paper | live | retired
frozen: null # set by `trades spec freeze` (Jack only); no rule changes after this date
rules_hash: null # set by `trades spec freeze`; any later change to the rules is detected

strategy: # a strategy from the registry (`trades strategies`), its parameters and sizing
  id: orb
  params: {}
  sizing: {method: stop_risk, risk_per_trade: 0.005, max_leverage: 1.0}

universe:
  symbols: [QQQ] # or symbols_file: specs/universes/STRAT-NNN.txt
  survivorship_free: true # does the list include names that later disappeared? (check 2)

data:
  provider: alpaca # synthetic | yahoo | alpaca | csv | massive
  timeframe: 5m # 1m | 5m | 15m | 30m | 1h | 1d
  adjustment: split # prices for signals; fills use the same bars

periods: # the locked test is opened once, by Jack, with `trades research ... --open-locked-test`
  train: [2016-01-04, 2021-12-31]
  validation: [2022-01-03, 2023-12-29]
  test: [2024-01-02, 2026-09-30]

costs: {} # overrides of config/base.yaml's cost model for this strategy, e.g. {half_spread_bps: 1.0}

expected: # what a working strategy should look like; a big miss in paper is a warning sign
  trades_per_month: [15, 22]
  win_rate: [0.2, 0.35]
  avg_r: [0.0, 0.3]
  holding: intraday

invalidation: # when to stop trading it (checked in paper and live)
  rolling_trades: 60 # expectancy over the last 60 trades below zero
  max_drawdown_vs_backtest: 1.5 # drawdown more than 1.5x the worst backtest drawdown
  max_slippage_vs_model: 2.0 # fills cost more than 2x the cost model ...
  slippage_trades: 20 # ... over 20 trades

parameters: {} # allowed values for research, e.g. {target_r: [5, 10, 15]}; every variant tried counts
one_trade_per_day: true # needs 100 out-of-sample trades instead of 200 (check 3)
---

# STRAT-NNN: Short descriptive name

**Status:** draft. **Frozen:** not yet.

## 1. Hypothesis

One sentence that could be false.

## 2. Economic rationale

Why should this edge exist, and who is on the other side of the trade? If you cannot name them,
the backtest is probably fitting noise.

## 3. Universe

Instruments, price and volume floors, the survivorship-free source, exclusions.

## 4. Data

Source, bar size, adjustments, timezone (America/New_York sessions, UTC storage) and calendar
(NYSE, early closes). Train, validation and locked-test dates, as in the front matter.

## 5. Entry rules

Exact and testable: someone else could code them from this paragraph.

## 6. Exit rules

Stop, target, time exit, flat by the close.

## 7. Sizing

Risk per trade, maximum positions, maximum exposure, leverage cap; how they meet risk/limits.yaml.

## 8. Cost model

Commission, half spread per side, slippage, stop slippage, borrow, regulatory fees. The checklist
reruns everything at 2x.

## 9. Expected behaviour

Trades per month, win-rate range, average R, holding time.

## 10. Invalidation criteria

When the strategy is retired, decided now, before you want it to work.

## 11. Parameters

Allowed ranges and why; every variant tested counts toward the deflated Sharpe ratio, so keep the
grid small.
