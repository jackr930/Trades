---
id: STRAT-000
name: Live Desk consensus (daily)
version: 1
status: draft # draft | backtesting | paper | live | retired
frozen: null # set by `trades spec freeze` (Jack only)
rules_hash: null # set by `trades spec freeze`

strategy:
  id: consensus
  params:
    members:
      - {id: tsmom, params: {}}
      - {id: faber_trend, params: {}}
      - {id: ma_crossover, params: {}}
      - {id: donchian_breakout, params: {}}
      - {id: bollinger_reversion, params: {}}
      - {id: rsi2_reversion, params: {}}
      - {id: xs_momentum, params: {}}
      - {id: pairs_trading, params: {}}
    pairs: []
    weighting: equal
    allow_short: false
    risk_per_trade: 0.01
    stop_atr: 2.0
    max_position_pct: 0.2
    max_gross: 1.0

universe:
  # The default Yahoo watchlist, picked in 2026: today's winners (NVDA, AAPL, MSFT) chosen to test
  # the past. Not survivorship-free, so it cannot pass check 2.
  symbols: [SPY, QQQ, AAPL, MSFT, NVDA, JPM, XOM, TLT]
  survivorship_free: false

data:
  provider: yahoo
  timeframe: 1d
  adjustment: split and dividend

periods:
  train: [2009-01-02, 2018-12-31]
  validation: [2019-01-02, 2021-12-31]
  test: [2022-01-03, 2026-09-30]

costs: {half_spread_bps: 2.0, slippage_bps: 3.0}

expected:
  trades_per_month: [1, 6]
  win_rate: [0.35, 0.6]
  avg_r: [0.0, 0.5]
  holding: weeks to months

invalidation:
  rolling_trades: 60
  max_drawdown_vs_backtest: 1.5
  max_slippage_vs_model: 2.0
  slippage_trades: 20

parameters: {weighting: [equal, by_category]}
one_trade_per_day: false
---

# STRAT-000: Live Desk consensus (daily)

**Status:** draft. **Frozen:** not yet. This is the existing Live Desk: eight published daily rules
vote, and the average vote sets a risk-sized position. It is also what the forward journal records
every evening (journal/experiment.json; a test keeps the two identical) and what the paper trader
dry-runs each night.

## 1. Hypothesis

The average vote of eight published trend, mean-reversion, momentum and pairs rules, sized by risk,
beats holding the same eight ETFs and stocks, after costs and taxes.

## 2. Economic rationale

Each member rule has published evidence (see the Library), and combining weakly correlated rules
should smooth their results. But four of the eight are trend rules that tend to agree, so the
"consensus" is mostly a trend signal; the `by_category` weighting counts each category once.

## 3. Universe

SPY, QQQ, AAPL, MSFT, NVDA, JPM, XOM, TLT: the default watchlist. Choosing NVDA, AAPL and MSFT in 2026
and testing them on 2009-2026 is survivorship bias in its plainest form, so this spec fails check 2.

## 4. Data

Daily bars from Yahoo Finance (split- and dividend-adjusted, so returns include dividends), history
from 2008 as on the Live Desk. Train 2009-2018, validation 2019-2021, locked test 2022-01-03 to
2026-09-30. **Honest caveat:** this locked test is not unseen. The Strategy Lab and the Live Desk
have already shown this strategy's results over those years. Its only clean out-of-sample evidence is
the forward journal, which started on 5 October 2026 and needs years to conclude anything.

## 5. Entry rules

Each evening after the close, every member strategy votes +1, 0 or -1 per symbol (warming-up
strategies abstain). The average vote at or above +0.2 is a buy (above +0.5 a strong buy); at or
below -0.2 a sell, which means no position because shorting is off. Orders fill at the next open.

## 6. Exit rules

A position is closed when the average vote falls back between -0.2 and +0.2, at the next open. The
2-ATR stop is used for sizing, not as an order.

## 7. Sizing

Conviction (the absolute average vote) times min(1% risk x price / (2 x ATR(20)), 20% of equity),
with all positions together scaled to at most 100% of equity.

## 8. Cost model

$0 commission; a 2 bp half spread and 3 bp slippage per fill; SEC and FINRA fees on sales. Turnover
is low, so costs matter less here than for the intraday specs; taxes matter more.

## 9. Expected behaviour

A few position changes a month across the eight symbols, holding for weeks to months.

## 10. Invalidation criteria

Retire it if expectancy over the last 60 trades is below zero, drawdown exceeds 1.5x the worst
backtest drawdown, or fills cost more than 2x the model over 20 trades.

## 11. Parameters

Equal or by-category weighting: two variants.
