# Data audit, and the recommendations implemented

Date: 2026-08-16

## Part 1 — which data is fit to backtest on

`tools/audit_history.py` checks every exported series for structural
corruption, synthetic padding, coverage, outliers and volatility regime breaks,
then applies known instrument inception dates. Verdict on all 18 series:

| symbol | timeframes | bars (H4) | verdict | use from |
|---|---|---:|---|---|
| **AUDUSD** | D1, H4, H1 | 44,477 | **USABLE** | 1993-04-27 (all) |
| **GBPUSD** | D1, H4, H1 | 44,471 | **USABLE** | 1993-05-12 (all) |
| **GBPJPY** | D1, H4, H1 | 44,466 | **USABLE** | 1993-04-19 (all) |
| **XAUUSD** | D1, H4, H1 | 33,972 | **USABLE** | 2004-06-11 (all) |
| **EURUSD** | D1, H4, H1 | 50,169 | **USABLE FROM** | **1999-01-04** |
| **USDJPY** | D1, H4, H1 | 50,162 | **USABLE FROM** | **1988-07-11** |

**12 series clean, 6 usable from a date, 0 unusable.**

### What has to be discarded, and why

**EURUSD before 1999-01-04 — 7,161 H4 bars.** The euro did not exist. Those
bars are a DEM/ECU proxy relabelled as EURUSD. This is the defect that matters
most, because **no statistic can detect it**: the prices are real market data,
just of a different instrument. It is caught only by knowing when the euro
launched, which is now encoded in `data_audit.INSTRUMENT_INCEPTION`.

The cost of not catching it was concrete. With those bars included, the
Lo–MacKinlay variance ratio reported EURUSD as **significantly trending**
(VR 1.256 at q=120, p < 0.05). Dropping them removes the signal entirely
(VR 0.962, not significant). A whole strategy direction rested on fabricated
history.

**USDJPY before 1988-07-11 — 4,433 H4 bars.** 28% of the pre-1988 D1 bars have
`high == low`. The yen was pegged at 360/USD until 1973 and thinly quoted after;
these are padded, not traded.

### The rule going forward

Every runner now trims each symbol to its audited window automatically. No
result in this repo rests on synthetic history any more, and
`research/v16_locked_candidate.json` records the requirement explicitly.

### Effective sample, honestly

Trustworthy H4 bars: AUDUSD 44,477 · GBPUSD 44,471 · GBPJPY 44,466 ·
EURUSD 43,008 · USDJPY 45,729 · XAUUSD 33,972. That is 22–33 genuine years per
symbol — ample for the 200-trade gate, and the reason V15's refutation on
GBPUSD (1,394 out-of-sample trades) is now conclusive rather than suggestive.

## Part 2 — recommendations implemented

### 1. JPY quote-currency conversion ✅

`instruments.Converter` converts quote-currency P&L to USD **at the rate in
force when the trade closed**, looked up as-of so a past trade is never valued
at a future rate. Over 33 years USDJPY ranges roughly 75–160, so a fixed rate
would misstate P&L by up to 2x, with the error correlated to the period tested.

Spread, slippage and swap convert with the P&L; **commission does not**, being
quoted in USD per lot. Mixing them is a silent error worth the exchange rate
itself.

Result, as predicted: **effective bets rose from 1.68 to 2.60** and the
achievable Sharpe multiplier from ×1.30 to ×1.61.

### 2. Audit-driven data trimming ✅

`v15_portfolio_test.py` and `v16_forward_test.py` call the auditor and start
each symbol at its trusted date, reporting what was dropped.

### 3. V16, a pre-registered mean-reversion candidate ✅

The variance ratios pointed weakly *opposite* to V15 — six of six symbols at or
below 1.0 at the longest horizon. V16 tests that direction: fade a 2σ stretch
from a 20-bar mean, exit at 0.5σ, stop at 4σ, mandatory 60-bar time stop.
Parameters are textbook values and the lookback is reused unchanged from V15,
so this is one new trial rather than a family. The lock file **predeclared FAIL
as the expected outcome**.

| symbol | trades | gross | net | net PF | out-of-sample | folds + |
|---|---:|---:|---:|---:|---:|---:|
| AUDUSD | 2,587 | **+81.78** | -3,767.81 | 0.900 | -2,070.67 | 40% |
| EURUSD | 2,482 | **+922.58** | -2,672.96 | 0.939 | -1,521.15 | 60% |
| GBPUSD | 2,581 | **+2,594.98** | -908.49 | 0.983 | -145.48 | 20% |
| XAUUSD | 2,022 | -3,300.28 | -3,486.01 | 0.907 | -3,037.73 | 40% |

**Verdict: FAIL.** 0 of 4 symbols profitable out of sample, deflated Sharpe
0.147 against 10 trials on record.

## The one result worth acting on

V16 fails, but it fails *differently* from V15, and the difference is the most
useful finding in this whole investigation:

**Three of four symbols are profitable gross and unprofitable net.** GBPUSD
turns +2,594.98 gross into -908.49 net — roughly $3,500 of costs across 2,581
trades. Its net profit factor is 0.983 and its out-of-sample profit factor is
**0.997**: almost exactly break-even.

That is not noise. It is the signature of a real but very small edge being
taxed away, and it matches both the variance-ratio evidence (VR < 1.0, weakly
mean-reverting) and the V14 scalp finding from the original report. V15 had no
gross edge to lose; V16 has one that costs consume.

The implication is specific: **the direction is right and the trade frequency is
wrong.** 2,581 trades over 33 years at 1.3 pips round trip is ~$3,500 of friction
against ~$2,600 of signal. Options, in order of how much they change:

1. **Trade the same idea less often.** A higher entry threshold takes fewer,
   larger stretches. This is one new locked specification, not a sweep.
2. **Reduce cost per trade.** At `--cost tight` (0.4 pip spread, $7/lot
   commission) rather than `typical` (0.9 pip), GBPUSD's friction roughly
   halves. Worth checking what your broker actually charges before assuming.
3. **Test on D1 rather than H4.** Same rules, a fraction of the trades, the same
   cost per trade. The audited D1 data is already exported.

Each of those is a new trial and must be recorded in
`research/v15_trials.json` — currently at 10 — and deflated against. With the
deflation bar rising, only a materially larger effect will clear 0.95, which is
the correct standard.

## What is still not established

No candidate has passed. V15 is refuted, V16 is refuted, and the portfolio
declines to trade. The tooling is now sound enough that a real edge would be
visible if it were there — which is worth more than a twenty-seventh profile,
but it is not a profitable system.

719 tests pass (694 before this work, 25 added).
