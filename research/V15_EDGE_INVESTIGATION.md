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

## Finding 5 — the data gap is now closed

The lock file's `honest_limitation` field recorded that the repo held only eight
months of GBPUSD M5, too little to test anything. **That has been resolved.**

`tools/export_validation_history.py` pulls the deepest history the terminal will
serve and writes a manifest of what was and was not available. Against the
MetaQuotes-Demo account it returned:

| symbol | H4 bars | span |
|---|---:|---|
| GBPUSD | 44,471 | 1993 → 2026 (33.3y) |
| EURUSD | 50,169 | (55.6y nominal, see caveat) |
| GBPJPY | 44,466 | 1993 → 2026 (33.3y) |
| AUDUSD | 44,477 | 1993 → 2026 (33.3y) |
| USDJPY | 50,162 | (55.6y nominal, see caveat) |
| XAUUSD | 33,972 | 2004 → 2026 (22.2y) |

D1 and H4 are committed under `research/data/` so these results are
reproducible. H1 (~49 MB) is git-ignored and regenerates in seconds.

**Caveat:** MetaQuotes reports EURUSD and USDJPY from 1971. The euro did not
exist until 1999, so those early bars are synthetic or proxied. Treat pre-1999
EURUSD as unusable.

## Finding 6 — a silent 300x pricing bug, and what it hid

The first multi-symbol run reported XAUUSD at **+2,244,625** and USDJPY at
**+1,678,893** on a $10,000 account. Those were not results. The locked config
carried `pip=0.0001` and `contract_size=100_000` — correct for EURUSD, wrong for
everything else. Gold trades 100 ounces, not 100,000 units, and a JPY pip is
0.01.

The distortion did not come from the price maths directly: risk-based sizing
mostly cancels it. It came from `max(0.01, ...)` — with FX conventions the
computed gold lot rounds below the minimum, gets clamped up, and is then
multiplied by a 1000x-too-large contract size.

**Fixed.** `mt5_ai_bridge/instruments.py` holds real per-symbol conventions and
`instrument_for()` **raises** on anything it cannot price rather than guessing.
JPY-quoted pairs are refused outright: their P&L is earned in yen and needs a
USDJPY conversion per trade, which requires a second price series.

This is the same disease as Finding 1 — a number that looks like a result but
was never measured correctly.

## Result: the locked V15 candidate, tested properly

`research/v15_forward_test.py` on 33 years of GBPUSD H4, and
`research/v15_multi_symbol_test.py` across every priceable symbol. Identical
parameters everywhere; nothing tuned per symbol.

| symbol | trades | gross | net | net PF | out-of-sample | folds + |
|---|---:|---:|---:|---:|---:|---:|
| AUDUSD | 1,619 | -5,002.94 | -6,422.52 | 0.771 | -7,259.67 | 20% |
| EURUSD | 1,784 | +54,679.97 | +30,399.20 | 1.129 | -3,969.27 | 40% |
| GBPUSD | 1,609 | -2,085.55 | -3,688.38 | 0.890 | -2,766.19 | 20% |
| **XAUUSD** | **1,179** | **+7,313.66** | **+7,140.98** | **1.192** | **+5,455.76** | **100%** |

GBPUSD alone, with 1,394 out-of-sample trades — comfortably past the 200-trade
gate — **fails conclusively**: PF 0.945, 2 of 5 folds positive, deflated Sharpe
0.232. The candidate is now genuinely refuted on GBPUSD, not merely untested.

EURUSD is the cautionary case: strongly profitable in-sample (+30,399, PF 1.129)
and **negative out of sample**. That is selection bias visible in a single row.

**XAUUSD is the one real signal.** 1,006 out-of-sample trades, PF 1.207, and
every one of five folds positive. Four of the five locked gates pass.

It still **FAILS** the fifth: deflated Sharpe **0.4526** against a 0.95 bar,
after deflating by the four symbols tried. The honest reading is *suggestive,
not proven* — consistent enough to be worth pursuing, not strong enough to fund.

Two things are worth noting about it. The return is modest: +$5,455 on $10,000
across 22 years is roughly 2%/yr, before any slippage beyond the modelled 1.3
pips. And the repo already throttles gold hardest — `config.py` ships
`_BUILTIN_SWING_RISK = {"XAUUSD": 0.2}`, treating it as the dangerous instrument,
when it is the only symbol here showing cross-fold consistency.

## What to do next

1. **Do not fund XAUUSD on this evidence.** Deflated Sharpe 0.45 is below the
   bar you set. Forward-test it on the demo account and compare live fills
   against `research/v15_multi_symbol_h4.json`.
2. **Every further variant raises the bar.** Trying more symbols, timeframes or
   parameters increases `n_trials`; the deflated Sharpe must be recomputed
   against the new count or it means nothing.
3. **Extend `instruments.py` before testing JPY pairs.** GBPJPY and USDJPY are
   currently refused, so two of the six exported symbols are untested.
4. **Re-check the pre-1999 EURUSD bars** before trusting any EURUSD figure.

## What did not change

No strategy logic, no risk limits, no live execution path. The session guard's
behaviour is byte-identical — the refactor is covered by the existing suite.
610 tests pass (484 before this work, 126 added).

## Honest summary

The reported edges were never measured correctly: costs were not charged,
selection was not corrected for, and instrument conventions were assumed. Those
three instruments now exist, are tested, and refuse to guess.

Applied to 22–33 years of real history, the pre-registered V15 candidate is
**refuted on GBPUSD, AUDUSD and EURUSD** — conclusively, on thousands of
out-of-sample trades. On **XAUUSD** it is profitable out of sample with every
fold positive, but at a deflated Sharpe of 0.45 it does not clear the 0.95 bar
and should not be funded on this evidence.

That is a real answer rather than a twenty-sixth report: one live hypothesis
worth forward-testing, three closed off, and measurement you can trust the next
time.
