---
id: STRAT-001
name: Opening range breakout on Stocks in Play
version: 1
status: draft # draft | backtesting | paper | live | retired
frozen: null # set by `trades spec freeze` (Jack only)
rules_hash: null # set by `trades spec freeze`

strategy:
  id: orb_sip
  params:
    or_minutes: 5
    top_n: 20
    min_rel_volume: 1.0
    rel_volume_days: 14
    min_price: 5.0
    min_avg_volume: 1000000
    min_atr: 0.5
    atr_days: 14
    stop_atr_frac: 0.10
    allow_short: true
  sizing: {method: stop_risk, risk_per_trade: 0.005, max_leverage: 1.0}

universe:
  # A starter list of liquid US stocks chosen in October 2026: chosen with hindsight, so it is
  # NOT survivorship-free and cannot pass check 2. Replace it with a point-in-time universe that
  # includes delisted names (see section 3) before believing any result.
  symbols_file: specs/universes/STRAT-001-liquid-starter.txt
  survivorship_free: false

data:
  provider: massive # needs intraday history for many stocks, including delisted tickers
  timeframe: 5m
  adjustment: split

periods:
  train: [2016-01-04, 2021-12-31]
  validation: [2022-01-03, 2023-12-29]
  test: [2024-01-02, 2026-09-30] # after the paper's 2016-2023 sample

costs: {half_spread_bps: 15.0, slippage_bps: 5.0, stop_slippage_bps: 15.0, commission_per_share: 0.0}

expected:
  trades_per_month: [150, 400]
  win_rate: [0.15, 0.35]
  avg_r: [0.0, 0.3]
  holding: intraday, minutes to hours

invalidation:
  rolling_trades: 60
  max_drawdown_vs_backtest: 1.5
  max_slippage_vs_model: 2.0
  slippage_trades: 20

parameters: {stop_atr_frac: [0.05, 0.10, 0.20], top_n: [10, 20]}
one_trade_per_day: false
---

# STRAT-001: Opening range breakout on Stocks in Play

**Status:** draft. **Frozen:** not yet. The playbook's *best research candidate*, "only with realistic
spread and capacity modeling".

## 1. Hypothesis

Among liquid US stocks, those whose first five minutes trade unusually heavy volume ("Stocks in
Play") continue in the direction of that range often enough that a breakout entry with a tight
ATR stop earns more than its spreads and slippage.

## 2. Economic rationale

Unusual opening volume marks a stock that news, earnings or institutions are moving; the order flow
behind that move tends to persist through the day, while in a quiet stock the open is mostly noise.
On the other side: liquidity providers and traders fading the move, who are paid the wide spreads
these stocks have at the open. Zarattini, Barbon and Aziz (SSRN 4729284, 2016-2023) report a net
return over 1,600% for the top 20 Stocks in Play (Sharpe 2.81) against about 29% for the unfiltered
5-minute ORB: the relative-volume filter does the work. QuantConnect's recreation reported a 2.4
Sharpe in 2016 with 17 of 25 parameter combinations beating the benchmark. Neither models the open's
spreads the way this spec does.

## 3. Universe

The paper: every US stock priced above $5 with 14-day average volume of at least 1 million shares
and a 14-day ATR above $0.50, **including stocks later delisted**, filtered each morning to the top
20 by opening relative volume. That needs point-in-time data for thousands of stocks.

This draft runs on `specs/universes/STRAT-001-liquid-starter.txt`, a list of liquid stocks picked in
October 2026. Picking today's survivors to test the past is survivorship bias: the list excludes
every stock that crashed out of the market between 2016 and now. It is fine for checking the
mechanics and costs, and **it fails check 2 by design**. The Phase 2 data task is a point-in-time
universe built from Massive's grouped daily bars and delisted tickers.

## 4. Data

5-minute bars (1-minute would make the stop fills less ambiguous; see the run's ambiguous-fill
count), split-adjusted, regular session in New York time, stored in UTC; NYSE calendar with early
closes. **Known data issue:** the $5 price and 1-million-share volume filters must see what traders
saw at the time (prices and volumes *not* adjusted for later splits), while signals and P&L use
split-adjusted bars. This draft has one adjusted series, which misstates both filters for stocks
that split later: NVDA traded near $30 in early 2016 but shows about $0.80 after adjusting for its
2021 (4:1) and 2024 (10:1) splits, so the draft wrongly filters it out. Fixing this is part of the
Phase 2 data task. Train 2016-01-04 to 2021-12-31, validation 2022-01-03 to 2023-12-29, locked test 2024-01-02
to 2026-09-30.

## 5. Entry rules

Each morning, a stock is eligible when its first 5-minute bar moved up or down (not unchanged), its
opening price is above $5, its average daily volume over the previous 14 sessions is at least 1
million shares, its 14-day ATR (from previous sessions) is above $0.50, and its opening 5-minute
volume is at least its average over the previous 14 sessions. Keep the 20 eligible stocks with the
highest relative volume. For each: if the first bar moved up, a buy-stop at the range high; if
down, a sell-stop at the range low. The order works until it fills or the session ends.

## 6. Exit rules

A protective stop 10% of the 14-day ATR from the entry level, priced when the stop order is placed
(the paper measures from the executed price; a gap through the entry makes our stop a little
tighter). No profit target. Exit in the closing auction. At most one trade per stock per day.

## 7. Sizing

Risk 0.5% of equity per trade (the paper: 1%), gross exposure at most 1x (the paper: 4x), within
risk/limits.yaml, which also caps open positions at 5. The paper's sizing sentence ("1% of capital
deployed", 4x cap) is ambiguous about how simultaneous positions share the cap: here each position is
sized on its own and the broker trims entries that would breach the gross cap, so on busy mornings
late entries are smaller or skipped. Read the paper's section on sizing and decide whether this
matches before freezing.

## 8. Cost model

$0 commission (Alpaca; the paper charged $0.0035 per share). A half spread of 15 bps per fill and 5
bps of slippage: the playbook's range for volatile Stocks in Play at the open is 10-30 bps per side.
Stop fills 15 bps worse. SEC and FINRA fees on sales. With a stop at 10% of ATR (often 0.2-0.5% of
the price), a 40 bp round trip can be most of the risk on a trade: this spec lives or dies on costs.

## 9. Expected behaviour

Up to 20 stop orders a day, of which a fraction trigger: roughly 150-400 trades a month across the
universe; a 15-35% hit rate; average R positive but small; holding minutes to hours.

## 10. Invalidation criteria

Retire it if expectancy over the last 60 trades is below zero, drawdown exceeds 1.5x the worst
backtest drawdown, or fills cost more than 2x the model over 20 trades.

## 11. Parameters

Stop at 5%, 10% (the paper) or 20% of ATR; 10 or 20 stocks a day: six variants, each counted.
