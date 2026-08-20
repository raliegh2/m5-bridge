# Is this viable for live forex trading? The arithmetic answer

Date: 2026-08-16

The brief was to keep improving until the model is viable for the open market.
This is the point at which that question stops being a matter of iteration and
becomes an inequality. The answer is **no**, and it is now proven rather than
asserted.

## The measurement that settles it

`research/breakeven_analysis.py` asks the question backwards from every previous
run. Instead of "is it profitable at cost X?", it asks **what is the maximum
cost at which it clears the gate**, and compares that to what brokers charge.

The signal, run **frictionless** — zero spread, zero commission, zero slippage,
zero swap — across every symbol and timeframe:

| timeframe | symbol | OOS trades | gross OOS | **gross PF** | gate | max affordable cost |
|---|---|---:|---:|---:|---:|---:|
| H4 | GBPUSD | 2,202 | +2,684.01 | **1.057** | 1.10 | 1.513 pips |
| H4 | EURUSD | 2,127 | +1,656.78 | **1.037** | 1.10 | 0.886 pips |
| H4 | AUDUSD | 2,213 | +1,937.09 | **1.041** | 1.10 | 0.938 pips |
| H1 | GBPUSD | 10,466 | +2,752.21 | **1.012** | 1.10 | 0.134 pips |
| H1 | EURUSD | 9,858 | +5,691.01 | **1.026** | 1.10 | 0.256 pips |
| H1 | AUDUSD | 8,896 | +1,425.27 | **1.008** | 1.10 | 0.121 pips |
| D1 | GBPUSD | 441 | −667.25 | 0.927 | 1.10 | — |
| D1 | EURUSD | 332 | +145.99 | **1.021** | 1.10 | 1.311 pips |
| D1 | AUDUSD | 424 | +143.05 | **1.017** | 1.10 | 0.734 pips |

**Best frictionless profit factor anywhere: 1.057. The gate requires 1.10.
Zero of nine configurations clear it at zero cost.**

Cost can only subtract. A configuration that cannot reach 1.10 with *no* costs
cannot reach it with any. **This is therefore not a broker problem and cannot be
solved by finding a cheaper account.** The raw signal is too weak, and no
amount of further iteration changes that arithmetic.

## The broker question, answered properly

I checked what your account actually charges rather than continuing to assume.

**Your MetaQuotes-Demo feed reports near-zero spreads** — GBPUSD and EURUSD
median 0.0 pips on recent M1 bars, live spread 0 points. That is a synthetic
demo feed and not tradeable reality; no live account fills at zero spread.

The historical `spread` column (2020 onward) is more plausible and useful:

| symbol | median | p90 |
|---|---:|---:|
| GBPUSD | 0.4 pips | 1.8 |
| EURUSD | 0.2 pips | 0.8 |
| AUDUSD | 0.3 pips | 1.8 |
| USDJPY | 0.3 pips | 1.3 |
| GBPJPY | 1.0 pips | 3.0 |
| XAUUSD | 0.5 pips | 1.4 |

This also **validated the gold correction**: live gold spread is 32 points
against the 30 I assumed after finding that bug. That number was right.

And the pricing structures converge more than people expect:

| arrangement | all-in round trip |
|---|---:|
| institutional (indicative) | 0.15 pips |
| tight spread account | 0.60 |
| ECN raw + $5/lot | 0.70 |
| ECN raw + $7/lot | **0.90** |
| typical spread account | **0.90** |
| wide market maker | 1.80 |

An ECN at 0.2 pips raw plus $7/lot commission is ~0.9 pips all in — exactly
where a 0.9-pip spread account lands. Switching broker model moves this far
less than the marketing suggests.

## The honest nuance

There is a distinction worth being precise about, because "not viable" and "not
profitable" are not the same claim:

* **Marginally net-profitable is achievable.** GBPUSD H4 can afford up to 1.513
  pips per trade and stay positive. At a tight account (0.6 pips) it measured
  **+$25.80 out of sample over 33 years**.
* **Viable is not.** That result is profit factor 1.001, one fold in five
  positive, and a deflated Sharpe of 0.000 against 1,379 recorded trials.

A strategy with a profit factor of 1.001 is not an edge you can trade. It is
indistinguishable from zero, it would be swamped by any execution detail the
backtest does not model, and after 1,379 attempts it carries no evidential
weight whatsoever. Trading it live would be gambling on a rounding error.

## Why I stopped iterating

Continuing would mean searching, and `research/SEARCH_RESULT.md` already
measured what searching produces on this data: of the specifications profitable
in-sample, **36.4% (GBPUSD) and 4.8% (EURUSD)** stayed profitable out of
sample. Both below a coin flip. On this dataset a profitable backtest is
mildly *anti*-predictive.

I could hand you a profit factor of 1.11 within the hour. It would lose money
live, and I would be manufacturing exactly the artefact this whole investigation
exists to eliminate.

## What genuinely remains

Not more iteration on this signal. Three things, and only the first two are
cheap:

1. **Verify against your real broker.** The demo feed is synthetic. Get a
   statement from the live account you intend to trade, take the actual all-in
   cost per round turn, and run `research/breakeven_analysis.py`. If your true
   cost sits below the max-affordable column, the strategy is net-positive —
   though still at a profit factor near 1.0, which is my point above.
2. **A different universe.** These six symbols give 2.6 effective bets and all
   show the same 1–6% effect. Instruments with different drivers — indices,
   rates, futures — are where a materially larger effect could exist. Every tool
   built here transfers unchanged: audit, cost model, validation, risk engine.
3. **Different information.** Mean reversion and momentum are both measured and
   both accounted for. Anything materially better must come from data these bars
   do not contain: order flow, positioning, rates, the calendar. Your own
   V14.22–14.25 order-flow work was headed there and was never validated — that
   is the most promising unexplored thread in this repository.

## What you have that is viable

The infrastructure, which is now genuinely sound and is the part that transfers:

- **Honest measurement** — costs, per-instrument conventions, audited data,
  walk-forward validation, deflated Sharpe, a persistent trial registry.
- **A risk system** that sizes from measured edge, tapers through drawdown,
  and stops on kill switches — correct whether or not an edge exists.
- **An edge gate** that refuses capital to unvalidated signals structurally,
  with no override.

Point that at an instrument or an information source with a real effect and it
will find it, size it, and protect it. Point it at this universe and it
correctly declines — which is the system working, not failing.

813 tests pass.
