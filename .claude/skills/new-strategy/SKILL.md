---
name: new-strategy
description: Turn a trading idea into a draft spec, a strategy that emits target weights only, its tests and a rehearsal research run on synthetic data, ready for Jack to review and freeze. Use when asked to add, implement or prototype a strategy, or to write a spec from a paper or an idea.
---

# New strategy: from idea to a spec Jack can freeze

The order matters: the spec comes first, and nothing here ever freezes a spec, opens a locked
test or sends an order. Those are Jack's (`trades spec freeze`, `--open-locked-test`,
`trades paper --submit`), and the hooks block them.

## 1. Write the spec

1. Pick the next free id: `trades spec list`. Create the draft: `trades spec new STRAT-NNN short-name`.
2. Fill in the front matter (what the code runs) and all eleven sections of the body (what it
   means). Insist on the hard parts:
   * a **hypothesis** that could be false, and an **economic rationale** that names who is on
     the other side of the trade;
   * a **universe** that is survivorship-free, or `survivorship_free: false` said honestly;
   * **periods**: train, validation and a locked test that has never been looked at;
   * **costs** at least as harsh as the playbook's (ETFs 2-5 bps half spread, Stocks in Play
     10-30 bps, stops 5-20 bps extra);
   * **parameters**: only the values you will really try. Every variant counts against the
     deflated Sharpe ratio, so a short list is a stronger test.
3. `trades spec show STRAT-NNN` must parse it without errors.

If the idea needs a strategy that already exists (`trades strategies`), the spec just points at
it with new parameters: skip to step 4.

## 2. Implement the strategy (only if it is new)

* Put it in the right family module of `src/trades/strategies/` (classic, cross_sectional,
  mean_reversion, intraday, ...), subclassing `SingleAssetStrategy` or the family's base class.
* It emits **signals and order levels only** (`signal`, and for stop-based rules `stop_loss`,
  `take_profit`, `entry_stop`). Sizing is `sizing.py`'s job, limits are the risk engine's, fills
  are the broker's. Never place orders, read the account, or apply risk limits in a strategy.
* Fill in the metadata honestly: `summary`, `rules_text`, `rationale`, `failure_modes`,
  `evidence` and `references` (the Library shows them to users). `params_spec` bounds every
  parameter; `warmup()` returns the bars needed before the first valid signal.
* Features go in `src/trades/features/` as pure functions that only use data up to each bar.
* Register the class in `REGISTRY` in `src/trades/strategies/__init__.py`.

## 3. Test it

* **Look-ahead:** `pytest tests/lookahead` runs every registered strategy; it must pass with
  yours in it. Add a unit test for each new feature function, including one that changes later
  bars and checks earlier values do not move.
* **Known answers:** a unit test in `tests/unit/` on a tiny hand-made series (`tests.helpers.make_bars`)
  where you can work out the right signal by hand.
* **Golden outputs:** a new strategy adds entries to `tests/integration/golden/outputs.json`.
  Regenerate with `TRADES_REGEN_GOLDEN=1 pytest tests/integration/test_golden.py`, then
  `git diff --stat tests/integration/golden/` and confirm the only changes are your strategy's new
  entries. A change to anything else means you changed existing behaviour: stop and explain.
* The whole suite: `pytest -q` and `ruff check src tests scripts`.

## 4. Rehearse the research run

`trades research STRAT-NNN --provider synthetic` exercises the whole pipeline without real data.
Its verdict means nothing about the edge (synthetic data fails check 2 by design, and its
intraday paths carry a known artifact), but every check should run, and the report should read
sensibly. Then ask the `backtest-auditor` agent to audit the run.

## 5. Hand over

Tell Jack, in this order: the hypothesis in one sentence; what the rehearsal showed and what it
cannot show; the variants the spec allows; anything the auditor flagged; and the commands he runs
next (review the spec, `trades spec freeze STRAT-NNN`, then `trades research STRAT-NNN` on real
data). End with one question that tests his understanding of why the edge should exist.
