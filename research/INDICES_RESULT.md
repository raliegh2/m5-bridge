# Indices: the closest thing to an edge found, and why it is probably not one

Date: 2026-08-16

The recommendation was CME futures order flow via databento. That is not
reachable — `databento` is not installed and there is no API key. But probing
the terminal for what it *does* offer turned up something better: **12,525
symbols**, including 26 indices and 126 FX pairs, not the six being tested.

That made the "different universe" recommendation immediately actionable.

## Indices do not trend — they mean-revert, harder than FX

The hypothesis going in was that equity indices trend, which would suit V15
momentum. **That is wrong on this data.** Variance ratios on D1:

| symbol | q=6 | q=30 | q=120 |
|---|---:|---:|---:|
| US500 | 0.874 | **0.775** | 0.492 |
| US30 | 0.908 | **0.805** | 0.475 |
| SWI20 | 0.938 | **0.712** | 0.433 |
| JPN225 | 0.897 | **0.745** | 0.651 |
| HK50 | 0.880 | **0.760** | 0.588 |
| USTEC | 0.854 | **0.822** | 0.449 |
| GBPUSD (FX, for scale) | 0.983 | 0.877 | 0.939 |

**All twelve indices sit below 1.0 at every horizon**, and consistently lower
than the FX majors. So the evidence points at V16 reversion, not V15 momentum —
and the earlier finding that V16's gross P&L tracks the variance ratio makes
that a genuine prediction rather than a guess.

Note the q=120 figures are extreme (0.43–0.65) but **not significant**: with
~1,900 daily bars there are only ~15 independent 120-day windows. The
significance test correctly refuses to call them.

## The result

V16, locked parameters, walk-forward, per-instrument costs:

| tf | symbol | trades | OOS net (tight) | OOS PF | folds+ |
|---|---|---:|---:|---:|---:|
| H4 | **USTEC** | 350 | **+1,531.61** | **1.272** | **80%** |
| H4 | US500 | 805 | +1,119.65 | 1.077 | 60% |
| D1 | US30 | 199 | +442.14 | 1.131 | 60% |
| H4 | US30 | 1,006 | +84.54 | 1.005 | 40% |
| H4 | US2000 | 713 | −801.88 | 0.942 | 40% |
| D1 | USTEC | 55 | −307.34 | 0.709 | 40% |

USTEC clears **4 of the 5 gates**:

| gate | value | required | verdict |
|---|---:|---:|---|
| net profitable | +1,511.44 | > 0 | **pass** |
| profit factor | 1.268 | ≥ 1.10 | **pass** |
| out-of-sample trades | 297 | ≥ 200 | **pass** |
| folds positive | 80% | > 50% | **pass** |
| deflated Sharpe | 0.108 | ≥ 0.95 | **fail** |

It is also barely cost-sensitive — PF 1.294 frictionless, 1.239 at the wide
tier — because a ~1-point Nasdaq spread is trivial against ~100-point stops.
That is the first time in this investigation that costs have not been the
binding constraint.

## Why it is probably not an edge

Two reasons, and the second is the serious one.

**1. Deflation.** 1,389 specifications are on record. In fairness, almost all
were searched against FX, not USTEC, so deflating this result by all of them is
conservative. That alone would not be decisive.

**2. It does not replicate, and the pattern is backwards.** Ordering the
indices by how much history they have:

| index | history | OOS PF |
|---|---|---:|
| US30 | 14.0y | 0.996 |
| US500 | 14.0y | 1.052 |
| US2000 | 14.0y | 0.929 |
| **USTEC** | **4.1y** | **1.265** |

**The index with the least data shows by far the strongest effect; the three
with fourteen years each show almost nothing.** That is what noise looks like.
A real mean-reversion mechanism in equity indices should appear most clearly
where there is most data to see it, not least.

USTEC's window is 2022-07 to 2026-08 — a single regime covering the post-2022
drawdown and the AI-driven recovery. A strategy that fades extremes would do
well in exactly that shape of market, and 4.1 years is not enough to know
whether it survives a different one.

## What would settle it

1. **More USTEC history.** This broker serves 4.1 years. A source with 15+
   years of Nasdaq would answer the question directly, and it is an ordinary
   data problem rather than a research one.
2. **Forward test.** The system is built and the risk engine is ready. Running
   USTEC H4 on the demo account records genuinely unseen data at roughly 85
   trades a year — enough for a meaningful read in about 18 months, and it
   costs nothing but patience.
3. **Do not trade it on this evidence.** 4 of 5 gates on 4 years of one regime,
   with the effect failing to replicate on three longer-history siblings, is
   interesting. It is not a validated edge, and the edge gate in
   `trading_system.py` will correctly refuse it until the deflated Sharpe
   clears.

## A bug this exposed

The first index run produced an impossible ordering: US500 H4 at the *tight*
cost tier lost $5,424 while *typical* made $1,163. Cost can only subtract, so
tighter can never be worse.

Cause: `RETAIL_TIGHT` carries a $7/lot ECN commission — an FX convention. On FX
at 0.01 lots that is $0.07. An index lot is ~1.0, so it became $7 per trade
against ~$3 of gross. Index CFDs are priced spread-only and charge no per-lot
commission.

`Instrument.commission_per_lot` now lets an instrument override the preset, and
the indices set it to zero. This is the same family as the gold contract-size
bug and the JPY conversion gap: **a convention borrowed from FX and applied
where it does not hold.** Worth watching for on any new asset class.

## Files

| file | purpose |
|---|---|
| `mt5_ai_bridge/instruments.py` | index contract specs, measured spreads, per-instrument commission |
| `research/persistence_d1_all.json` | variance ratios across FX and indices |
| `research/indices_levers.json` | cost-tier sweep on indices |
| `research/v16_indices_gate.json` | the deflated gate verdict |

856 tests pass.
