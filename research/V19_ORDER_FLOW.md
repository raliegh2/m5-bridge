# V19: the order-flow thread, tested

Date: 2026-08-16

The last unexplored avenue. `research/VIABILITY_VERDICT.md` established that the
gross signal has to get **stronger** — cost reduction cannot help, because
V16's frictionless profit factor of 1.057 is already below the 1.10 gate and
cost only subtracts. This tests whether activity data supplies the missing
strength.

## What was available, and what was not

Your V14.22–14.25 order-flow modules are honest about the constraint: spot FX
has no centralised tape, so they use live broker quote ticks and run
shadow-only. That cannot be replayed historically.

What every MT5 export *does* carry is **`tick_volume`** — quote changes per bar.
A proxy for activity rather than traded size, but a documented one, and the only
flow-like information in this dataset.

## The information test, run first

The hypothesis is not invented here. **Campbell, Grossman & Wang (1993)**:
moves on high volume are information-driven and persist; moves on low volume are
liquidity-driven and revert. That is a falsifiable statement about the data,
testable before any strategy exists — the same discipline by which the variance
ratio predicted V15's failure in advance.

First-order return autocorrelation, split by trailing-relative volume, on
audited H4 bars:

| symbol | ρ all | ρ low vol | ρ high vol | spread | p(low) | supports CGW |
|---|---:|---:|---:|---:|---:|---|
| AUDUSD | −0.0105 | **−0.0358** | −0.0071 | −0.0287 | 0.0000 | yes |
| EURUSD | −0.0070 | **−0.0275** | +0.0038 | −0.0313 | 0.0018 | yes |
| USDJPY | +0.0042 | **−0.0224** | +0.0145 | −0.0369 | 0.0088 | yes |
| XAUUSD | +0.0085 | −0.0193 | +0.0280 | −0.0473 | 0.0518 | no |
| GBPJPY | −0.0106 | +0.0011 | −0.0169 | +0.0180 | 0.9001 | no |
| GBPUSD | −0.0238 | +0.0061 | −0.0130 | +0.0191 | 0.4798 | no |

Three of six support it significantly. EURUSD is cleanly graded across
quintiles (−0.045, −0.024, −0.027, −0.000, +0.011), which is what a real
mechanism looks like. **GBPUSD contradicts it outright** — its reversion sits in
the middle quintiles — and GBPUSD is V16's best performer.

## The prediction, registered before testing

Because a filter that simply trades less will often look better on some subset,
"V19 is profitable" would prove nothing. So the lock file registered a
**directional, symbol-specific prediction**:

> V19 should beat V16 on AUDUSD, EURUSD and USDJPY, and should NOT beat it on
> GBPUSD or GBPJPY. Falsified if V19 improves GBPUSD, or fails to improve
> EURUSD.

V19 itself changes nothing but the entry condition: every V16 signal parameter
is inherited unchanged, and the threshold is relative volume < 1.0 — the
textbook definition of below-average, deliberately *less* favourable than the
30th percentile where the measured effect is strongest.

## Result

Frictionless, so the filter is isolated from the cost of trading:

| symbol | V16 gross PF | V19 gross PF | V16 trades | V19 trades | improved? | predicted |
|---|---:|---:|---:|---:|---|---|
| AUDUSD | 1.041 | 0.993 | 2,213 | 1,359 | no | improve — **MISS** |
| **EURUSD** | 1.037 | **1.078** | 2,127 | 1,444 | **yes** | improve — **OK** |
| USDJPY | 0.955 | 0.888 | 2,356 | 1,406 | no | improve — **MISS** |
| GBPUSD | 1.057 | 1.038 | 2,202 | 1,425 | no | no change — OK |
| GBPJPY | 0.887 | 0.851 | 2,274 | 1,368 | no | no change — OK |
| XAUUSD | 0.923 | 0.892 | 1,742 | 1,129 | no | — |

## Reading it honestly

"3 of 5 correct" flatters the result and should not be quoted.

V19 trades roughly a third less than V16 and improved **1 of 6 symbols overall**
— a 17% base rate. Predicting "no improvement" is therefore right about 83% of
the time by default, so the GBPUSD and GBPJPY hits carry almost no information.

The informative count is the symbols predicted to **improve**:

> **1 of 3 (EURUSD only) — at or below what chance produces.**

**The mechanism is not established.** EURUSD improving could easily be the one
symbol in six that moved by luck, and it is exactly the symbol whose measured
effect was cleanest, which is suggestive but not conclusive on n=1.

## What did move

The best frictionless profit factor found anywhere in this investigation:

| | best frictionless PF | gap to the 1.10 gate |
|---|---:|---:|
| V16 (GBPUSD) | 1.057 | 0.043 |
| **V19 (EURUSD)** | **1.078** | **0.022** |

The gap **halved**. That is the only genuine progress toward viability in the
whole exercise, and it came from adding information rather than from tuning
parameters — which is the direction the evidence has pointed throughout.

It is still not enough. EURUSD's *net* profit factor is 1.014, one fold in five
positive, deflated Sharpe 0.000 against 1,385 recorded trials. The gates fail,
as the lock file predeclared they would.

## Where this leaves things

The direction is right and the magnitude is short. Concretely:

1. **`tick_volume` is a weak proxy.** It counts quote changes, not traded size.
   Real order flow — signed volume, book imbalance, aggressor side — is a
   materially stronger signal, and it is what your V14.22–14.25 modules were
   built to capture live. The infrastructure to validate it now exists; the data
   does not, historically.
2. **CME FX futures carry a real tape.** `databento` is already in
   `requirements.txt`, and `v14_25_futures_order_flow.py` was headed there.
   6E/6B/6A futures have centralised volume and are the honest way to test
   order flow on history. That is the single highest-value next step.
3. **Do not tune the volume threshold.** Moving it from 1.0 toward the 30th
   percentile would improve the backtest. The measurement was taken at that
   percentile, so fitting the threshold to it is circular, and every variant
   raises a deflation bar already at 1,385 trials.

## Files

| file | purpose |
|---|---|
| `mt5_ai_bridge/order_flow.py` | relative volume, conditional autocorrelation with significance |
| `mt5_ai_bridge/candidate_v19.py` | V16 restricted to below-average-volume bars |
| `research/volume_information_test.py` | the CGW test, run before building |
| `research/v19_locked_candidate.json` | frozen spec and registered prediction |
| `research/v19_forward_test.py` | prediction test with base-rate correction |

835 tests pass (813 before this work, 22 added).
