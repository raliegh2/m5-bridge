# V17: the system the data supports, and what it proved

Date: 2026-08-16

Built to the brief "using the current data set and market conditions, create a
trading system". It is a complete, tested, runnable system. It also fails, and
the way it fails settles a question the previous twenty-six versions left open.

## The system

**Signal** — fade a 3.0σ stretch from a 20-bar mean on H4. Exit on reversion to
0.5σ, stop at 5.0σ, mandatory 60-bar time stop.

**Gate** — trade a symbol only when its Lo–MacKinlay variance ratio at q=30 is
below 1.0, recomputed inside each walk-forward fold on the **training window
only**.

**Portfolio** — 0.5% risk per trade, volatility-targeted; 2.0% aggregate open
risk; 1.5% per-currency cap; 4 concurrent positions maximum.

**Data** — audited, post-inception bars only; per-instrument spreads.

## Why these choices, and not others

### The gate came from two measurements agreeing

V16's *gross* P&L is positive on exactly the symbols whose variance ratio is
below 1.0, and negative on those above it:

| symbol | VR q=30 | V16 gross | V17 gate |
|---|---:|---:|---|
| AUDUSD | 0.926 | +81.78 | admit |
| GBPUSD | 0.934 | +2,594.98 | admit |
| EURUSD | 0.962 | +922.58 | admit |
| GBPJPY | 0.994 | −4,636.71 | admit (marginal) |
| USDJPY | 1.011 | −2,539.77 | refuse |
| XAUUSD | 1.031 | −3,300.28 | refuse |

A price statistic and a P&L statistic, computed independently, ordering the
symbols the same way. That is worth building on.

The threshold is **1.0**, the textbook boundary — not 0.97, where the gross
figures actually flip. Using 0.97 would fit the gate to data already seen. The
cost of the honest choice is admitting GBPJPY at 0.994, which was
gross-negative.

### The entry threshold came from arithmetic, not a sweep

V16 on GBPUSD earned $1.005 gross per trade against $1.15 of cost — under water
by 13%. Assuming gross per trade scales with the captured stretch
(entry_z − exit_z) while cost per trade stays fixed:

| entry | captures | gross/trade | cost/trade | ratio |
|---|---|---:|---:|---:|
| 2.0σ | 1.5σ | $1.005 | $1.15 | 0.87 |
| 2.5σ | 2.0σ | ~$1.34 | $1.15 | 1.17 |
| 3.0σ | 2.5σ | ~$1.68 | $1.15 | **1.46** |

Rule: take the smallest threshold clearing a ratio of 1.40. That gives 3.0σ.
Everything else — lookback, exit, stop buffer, time stop, risk — is inherited
from V16 unchanged, so V17 is one new specification rather than a family.

## Result

Walk-forward, 5 folds, audited data, per-instrument costs:

```
fold 0: net=  +457.08  trades=132  dd= 7.00%  [AUDUSD 0.833, EURUSD 0.903, GBPUSD 0.691]
fold 1: net=  -774.36  trades=111  dd= 9.28%  [AUDUSD 0.877, EURUSD 0.947, GBPUSD 0.775]
fold 2: net=  -998.34  trades= 97  dd=10.67%  [AUDUSD 0.901, EURUSD 0.984, GBPUSD 0.834]
fold 3: net= -1003.50  trades=206  dd=12.99%  [+ GBPJPY 0.991]
fold 4: net=  -516.01  trades=124  dd= 6.33%  [AUDUSD 0.927, EURUSD 0.970, GBPUSD 0.910]
```

| metric | value |
|---|---:|
| out-of-sample trades | 670 |
| net profit | **−2,835.13** |
| profit factor | 0.826 |
| folds positive | 1 of 5 |
| deflated Sharpe | 0.000 (11 trials) |
| verdict | **FAIL** (4 of 5 gates) |

The gate itself worked exactly as designed: USDJPY and XAUUSD were refused in
every fold, GBPJPY admitted only in fold 3 when its training-window VR dipped
below 1.0.

## What this proved — the derivation was wrong

Run cost-free, V17 is **still negative**: −1,783.26, profit factor 0.888, 1 of 5
folds positive.

That is the finding. V16 had a small but genuine gross edge; V17 has none. The
only thing changed was the entry threshold, so the linear-scaling assumption
behind the derivation is false:

> **A 3σ FX move is not a more-stretched 2σ move. It is a different event.**
> Moderate stretches revert. Extreme ones are news and breakouts, and they
> continue.

Widening the entry did not amplify the edge in proportion to the cost saved. It
walked out of the regime where the edge exists.

This closes the loop opened in the original report. The chain is now complete
and each link is measured:

1. The reversion effect is real, at **moderate** stretches, on the symbols the
   variance ratio identifies. (V16 gross, +$1.005/trade on GBPUSD.)
2. It is **smaller than retail transaction costs** at that frequency.
   ($1.15/trade.)
3. It **cannot be rescued by trading less often**, because the effect does not
   survive the wider threshold. (V17 gross, negative.)

Those three together mean the edge is not merely unproven — it is *uneconomic*
at retail costs on H4. That is a stronger and more useful conclusion than
another failed backtest.

## What I did not do

I did not sweep entry_z between 2.0 and 3.0 to find where the result turns
positive. There is almost certainly such a value. Finding it by search would be
fitting, it would add trials to a deflation bar already at 11, and it would
produce exactly the kind of number that filled `research/` with twenty-five
profitable-looking reports and left the account with no edge.

## Where an edge could still be, honestly

Ranked by whether the evidence actually points there:

1. **Lower transaction costs, same V16 signal.** The gap is 13% of the cost per
   trade. A broker at `--cost tight` (0.4 pip spread) rather than `typical`
   (0.9) roughly halves the friction. This changes nothing about the strategy
   and is worth checking against what you are actually charged — it is the only
   route that does not require a new effect to exist.
2. **A lower timeframe where the same effect is larger relative to cost.** The
   audited H1 data is exported. This is one new specification, and it should be
   locked before running.
3. **Not gold, and not the yen crosses.** Both variance ratios and both
   candidates' gross P&L agree they do not mean-revert. Gold's earlier apparent
   edge was 80% underpriced spread.

## Files

| file | purpose |
|---|---|
| `mt5_ai_bridge/system_v17.py` | the system: VR gate, reversion signal, portfolio risk |
| `research/v17_locked_system.json` | frozen spec with the full derivation |
| `research/v17_system_test.py` | walk-forward runner with per-fold admission |
| `research/v17_system_h4.json` | the result above |

748 tests pass (726 before this work, 22 added).
