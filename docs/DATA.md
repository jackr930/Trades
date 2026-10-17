# Market data

Every bar in Trades comes from a provider. A spec names its provider in its front matter
(`data.provider`); the web app uses the one chosen in **Settings**; `--provider` overrides both
on the command line.

## Providers

| Provider | Key | History | Freshness | Use it for |
| --- | --- | --- | --- | --- |
| **Massive** (formerly Polygon.io) | yes, `MASSIVE_API_KEY` | years of 1-minute to daily bars, by plan | end of day (free) to real time | **research**: the deep, intraday history a backtest needs |
| **Alpaca Market Data** | free account, `APCA_API_KEY_ID` + `APCA_API_SECRET_KEY` | years of intraday and daily bars | real time, streamed (free: the IEX exchange only; paid: all exchanges, "SIP") | the paper trader and the Live Desk |
| **Yahoo Finance** (`yfinance`) | no | decades daily; intraday only 7 to 60 days | near real time, polled | quick looks, the forward journal; unofficial, for personal use |
| **CSV files** | no | whatever you put in `~/.trades/data/SYMBOL.csv` | static | your own data |
| **Synthetic** | no | a generated market | simulated | rehearsing the pipeline offline; never evidence |

Check a key with one cheap request: `trades data check massive` (or `alpaca`, `yahoo`). In the
web app, **Settings → Test** does the same.

### Massive

Polygon.io renamed itself Massive on 30 October 2025. The old address still works for an
extended period, so the base URL is a setting: `https://api.massive.com` by default,
`https://api.polygon.io` if you need it (Settings, or `MASSIVE_BASE_URL`).

Plans set how far back history goes and how fresh it is (individual plans, October 2026):

| Plan | Price | History | Freshness | Requests |
| --- | --- | --- | --- | --- |
| Basic | free | 2 years | end of day | 5 a minute |
| Starter | $29/month | 5 years | 15 minutes delayed | unlimited |
| Developer | $79/month | 10 years | 15 minutes delayed, trades | unlimited |
| Advanced | $199/month | the longest | real time, quotes | unlimited |

Prices and limits change: check massive.com/pricing before you rely on them.

Good to know:

* **A backtest cannot reach further back than your plan.** If a spec asks for 2016 and the plan
  starts in 2024, the run starts in 2024: read the first bar in the run's data manifest.
* **Bars are split-adjusted only.** Massive does not add dividends back, so a daily return across
  an ex-dividend date is a price return. Intraday strategies that are flat overnight are not
  affected; for daily strategies, results on dividend payers are slightly pessimistic.
* **On the free plan** the provider waits out the 5-requests-a-minute limit by itself, quotes
  come from end-of-day bars (one request covers every symbol), and the cache below makes reruns
  free.
* The key is sent in a request header, only to the configured host, never in a URL.

## The bar cache

Research asks for the same years of bars on every run. The cache keeps what Massive and Alpaca
returned as parquet files under `data/cache/` (set by `data.cache_dir` in `config/base.yaml`;
gitignored), one file per provider, symbol and timeframe.

* **Only completed bars are stored**: nothing from today (New York). Today's bars, and any bar a
  vendor may still correct, always come fresh from the provider.
* **A request inside what is stored is answered from disk.** A request beyond it downloads only
  the missing edge, plus an overlap of bars already stored.
* **The overlap must match.** After a split, every adjusted price before it changes. If the
  overlap disagrees with what is stored, the file is replaced by one fresh download of the whole
  range, so two price scales are never stitched together.
* **Only what came back counts.** A file vouches for the range its bars cover, not the range that
  was asked for: a bar the vendor has not published yet is asked for again next time, and a plan's
  history limit is re-checked once a day (an upgrade shows up by itself the next day).
* **Runs stay reproducible.** Each run records a hash of its data, and a rerun from the cache
  produces the same hash: stored bars win over a vendor's later revisions (of volume, say). When
  the adjusted history itself changes (a split; for Alpaca, also a dividend), the cache downloads
  it again and `trades runs reproduce` reports which data changed.

```bash
trades data cache                  # what is stored, per provider, symbol and timeframe
trades data clear                  # empty it (all, --provider massive, or --symbol SPY)
```

After a plan upgrade, `trades data clear` brings in the deeper history at once (otherwise it
arrives the next day). The cache needs `pyarrow` (`pip install -e ".[cache]"`, included in
`.[dev]`); without it everything still works, it just downloads every time. Set
`TRADES_DATA_CACHE=off` to turn it off, or to a folder to move it. Yahoo is not cached: it
re-adjusts past prices at every dividend and keeps only weeks of intraday history.

## Rules every provider follows

* **Timestamps.** Daily bars are stamped 00:00 UTC of their trading date; intraday bars at the
  start of the bar, in UTC. Sessions follow the NYSE calendar in `data/calendar.py` (holidays and
  13:00 early closes included).
* **Completed bars only.** A bar still forming is never fed to cached statistics, a forward test
  or a paper decision (`data.base.last_bar_forming`).
* **The regular session.** Intraday features use the 09:30 to 16:00 session; pre-market and
  after-hours prints never change a daily bar.
* **One first bar.** Daily history starts on 2 January 2008 everywhere (`HISTORY_START`), so rules
  re-checked on a schedule (every 21 bars, a monthly rebalance) check on the same days in the Live
  Desk, the journal and backtests.
* **Survivorship.** Testing today's symbols on the past ignores the companies that failed. A spec
  that needs a universe (STRAT-001) builds it point in time, delisted names included; check 2 of
  the checklist asks for it.

## The synthetic market

The demo market is a regime-switching model with volatility clustering, fat tails, jumps, a
one-factor structure and a cointegrated pair. It runs offline and is seeded, so every run is
repeatable. Its intraday paths are Brownian bridges to each day's close, which leaks the day's
direction into the morning and flatters intraday momentum rules; see the playbook's note on the
[bridge artifact](PLAYBOOK.md#2-research-it-the-acceptance-checklist). It is for learning and
rehearsal: check 2 of the checklist fails on it by design.
