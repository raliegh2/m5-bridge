# V15: Why there is no trading edge, and what was built about it

Date: 2026-08-16

## The question

"The model has no trading edge." This report answers why, with reproducible
numbers rather than another profile.

## Finding 1 — the edge was never measured net of costs

`mt5_ai_bridge/backtest.py` filled entries at the exact mid close and exits at
the exact stop/target level. No spread, commission, slippage or swap. Every
number it ever produced was a **gross** number.

This is not a rounding error at the frequencies this repo trades.
`research/v14_4_cost_stress_report.py` already said so:

> for a 1.25R-target scalp, the break-even win rate rises quickly with spread,
> and the whole researched edge disappears within about one pip

The arithmetic: a 5-pip stop at 1.25R pays 6.25 pips on a win. A 1.3-pip round
trip is ~21% of the target. The V14.3 ICT stream's implied edge was about
+0.03R per trade. It cannot survive that.

**Fixed.** `mt5_ai_bridge/costs.py` provides a tested `CostModel` (spread,
per-side slippage, commission per lot, swap per night) with presets. It is wired
into `backtest.py`, which now reports `total_costs`, `gross_profit` and
`gross_profit_factor` alongside the net figures. The CLI defaults to
`--cost typical`; `--cost zero` reproduces the old gross replay on request.

Measured on the committed GBPUSD M5 data (reasoning strategy, 15/30 pips):

| | gross (old default) | net (new default) |
|---|---:|---:|
| profit factor | 0.91 | 0.80 |
| total profit | -108.46 | -257.82 |
| costs paid | 0.00 | 149.36 |

Note the gross profit factor is already **below 1.0**. Costs made a losing
strategy lose faster; they did not destroy a winning one.

## Finding 2 — the reported edges were selection artifacts

Twenty-five profiles (v4 … v14.25) were tuned and judged on the same history.
The best of twenty-five looks good *because* it is the best of twenty-five.
`research/V14_4_TRAIN_CONFIRM_EDGE_REBUILD_REPORT.md` reached this conclusion
independently — all five selection protocols failed their locked test.

**Built.** `mt5_ai_bridge/validation.py`:

- `walk_forward_splits` — anchored or rolling folds with an embargo, so
  parameters chosen on a train slice are only ever scored on the slice after it;
- `probabilistic_sharpe_ratio` / `expected_max_sharpe` /
  `deflated_sharpe_ratio` — Bailey & López de Prado's correction for how many
  specifications were tried;
- `benjamini_hochberg` — FDR control when scoring many candidates at once;
- `Gate` / `evaluate` — explicit PASS/FAIL gates that name what failed.

`research/honest_walk_forward.py` runs the whole thing. On the committed data,
searching 48 parameter sets:

| | in-sample best of 48 | walk-forward (out of sample) |
|---|---:|---:|
| zero cost | **+26.41** (PF 1.04) | negative in 4 of 5 folds |
| typical cost | **-27.35** (PF 0.96) | **-126.15**, 0 of 5 folds positive |

Deflated Sharpe: **0.0009** against a 0.95 gate. Selection-bias gap: **98.80**.

The in-sample winner at zero cost (+26.41, PF 1.04) is exactly the kind of
number the older reports were built on. Out of sample it is worth nothing.

## Finding 3 — the "why no trades" question had no durable answer

`RiskGuardedClient.can_open_new_trade` returns the *first* gate that refuses,
only at the instant an order is attempted. A quiet day left no record of the
other gates that were also shut.

**Fixed.** `mt5_ai_bridge/entry_diagnostics.py` holds the gate logic as one pure
function; the guard now calls it, so the live decision and the diagnostic can
never disagree. `diagnose_entry()` returns every gate's standing, and
`guard.rejections` (a `RejectionLedger`) aggregates reasons across a session.

```
python -m mt5_ai_bridge.entry_diagnostics --symbol GBPUSD --volume 0.1

GBPUSD 0.1: BLOCKED by 4 of 8 gates
  [BLOCK] daily_lock -- daily loss limit reached
  [  ok ] loss_cooldown
  [  ok ] volume_positive
  [  ok ] minimum_lot
  [  ok ] maximum_lot
  [BLOCK] daily_trade_limit -- daily trade limit reached (8/8)
  [BLOCK] symbol_trade_limit -- GBPUSD daily trade limit reached (4/4)
  [BLOCK] entry_interval -- minimum entry interval active
```

The live guard would have reported only the first line.

## Finding 4 — a new candidate, locked and tested once

`mt5_ai_bridge/candidate_v15.py` is a time-series-momentum candidate: H4
Donchian 20/10 breakout, 2.0x ATR(14) stop, EMA50 trend filter.

Parameters were fixed **before** any result was seen, and not from this dataset:
the timeframe from the cost arithmetic above (a ~60 pip stop puts cost at ~2% of
risk instead of ~26%), the 20/10 lookbacks from the published Turtle
parameterisation, the ATR settings from the existing `sizing.py` defaults. They
are frozen in `research/v15_locked_candidate.json`, and `locked_config()`
aborts if the file and the code disagree.

Result on the committed data (`research/v15_forward_test.py`):

| | value |
|---|---:|
| full-sample net | -243.19 |
| profit factor | 0.75 |
| out-of-sample trades | 31 |
| folds positive | 2 of 5 |
| verdict | **FAIL** (all five gates) |

**This failure is not conclusive, and the script says so.** 31 out-of-sample
trades against a 200-trade gate is noise. The candidate is neither validated nor
refuted.

## The binding constraint is data, not strategy

The only price data in this repository is `GBPUSD_M5.csv`: 50,000 M5 bars,
2025-10-28 → 2026-06-30, roughly eight months of one symbol. Every 10-year claim
in `research/` references data that is not committed, so none of those reports
can be reproduced or checked here.

Eight months of one pair cannot settle whether any strategy has an edge. Before
the next profile is written:

1. Export several years of history for every traded symbol
   (`tools/export_v9_history.py`, `Export History.bat`) and commit it or pin it
   somewhere reproducible.
2. Re-run `research/honest_walk_forward.py` on it.
3. Re-run `research/v15_forward_test.py` on it.
4. Count every specification tried and pass that count as `n_trials`. The
   deflated Sharpe is only honest if the trial count is.

## What did not change

No strategy logic, no risk limits, no live execution path. The session guard's
behaviour is byte-identical — the refactor is covered by the existing suite.
593 tests pass (484 before this work, 109 added).

## Honest summary

There is no evidence of a trading edge in this repository, and there never was
measurable evidence — the instrument that would have detected one did not charge
costs and did not correct for selection. Those instruments now exist and are
tested. The tooling to find an edge is in place; the edge is not, and finding one
requires data this repo does not yet contain.
