# $5,000 over ten years, with drawdown held under 10%

Date: 2026-08-18

The question: what does this strategy make on a $5,000 account over ten years,
with a drawdown ceiling below 10%?

## The book

**50/50 US large-cap equity and gold, each with the 10-month moving-average
overlay, run at 70% invested and 30% cash.**

> Configured at **0.70**, not the 0.90 that sizes exactly to the 10%
> ceiling. Sizing to the worst drawdown ever observed leaves no room for a
> crash worse than 2008; 0.70 holds the worst rolling ten-year drawdown to
> **7.7%** for about 1.8 points of annual return. The 0.90 figures are kept
> below for comparison.

Two changes from the previous version, both forced by the constraint:

**Gold replaces the second equity ETF.** IVV and VTI correlate **0.988** — they
are one bet with two tickers, so the "diversified" equity book had the
volatility of a single asset. Gold's correlation with equities on the same
timed series is **−0.07**. That is the difference between one bet and two, and
it is the entire reason this fits inside 10%.

**Exchange-traded gold, not spot.** XAUUSD's minimum order is 1 oz ≈ $4,376 of
notional against a target gold exposure of about $2,250 — untradeable at the
right size on this account. **IAU** at $81.70 a share is fully tradable with
21.6 years of history and a 0.012% spread.

Sizing is set by the worst rolling ten-year drawdown across 20.7 years of
history *including 2008*, not by the full-sample figure, so the ceiling has to
hold in the worst decade the data contains.

## Why volatility, not return, decides the size

Every book below is scaled to the same 10% drawdown budget. What differs is how
much exposure each can carry inside it:

| book | invested | CAGR | worst 10y DD | median $5k → 10y |
|---|---:|---:|---:|---:|
| **timed 50/50 equity+gold** | **90%** | **8.15%** | 9.9% | **$8,404** |
| held 50/50 equity+gold | 37% | 4.05% | 9.8% | $6,658 |
| timed equity only | 35% | 3.15% | 9.9% | $6,644 |
| held equity only | 16% | 1.61% | 10.0% | $5,994 |

A plain IVV holding can only be run at 16% of the account before it breaches
10%. The timed, diversified book runs at 90% inside the same ceiling. Both the
timing and the diversification contribute, and they compound: neither alone
gets past 4%.

This is the first result in the investigation that beats buy-and-hold on the
terms this repo has insisted on all along — equal risk, not equal capital.

## The answer, as a range

$5,000, 129 overlapping ten-year windows drawn from 2005–2026:

At the configured **0.70** (6.35% a year):

| | ending balance | profit | return |
|---|---:|---:|---:|
| worst ten years | $6,519 | $1,519 | +30.4% |
| **median** | **$7,528** | **$2,528** | **+50.6%** |

Worst rolling ten-year drawdown: **7.7%**.

The full ladder, so the trade-off is visible rather than asserted:

| fraction invested | CAGR | worst 10y DD | median $5k → | worst $5k → |
|---:|---:|---:|---:|---:|
| 0.90 | 8.15% | 9.9% | $8,404 | $6,992 |
| 0.80 | 7.25% | 8.8% | $7,957 | $6,754 |
| 0.75 | 6.80% | 8.3% | $7,740 | $6,636 |
| **0.70** | **6.35%** | **7.7%** | **$7,528** | **$6,519** |
| 0.65 | 5.90% | 7.2% | $7,320 | $6,403 |

Roughly one point of annual return buys one point of drawdown, all the way
down the ladder.

## The number this rests on, and what happens if it is wrong

Gold returned **10.74% a year** over this window — roughly $400 to $4,376. That
is an exceptional era, and half the book is in it. Holding gold's volatility
and crash timing fixed while shifting its return:

| if gold returns | invested | CAGR | median $5k → 10y | profit |
|---|---:|---:|---:|---:|
| 8.3%/yr (as measured) | 90% | 8.15% | $8,404 | $3,404 |
| 5%/yr | 80% | 5.95% | $7,040 | $2,040 |
| 3%/yr | 69% | 4.45% | $6,302 | $1,302 |
| 0%/yr | 48% | 2.39% | $5,492 | $492 |
| −2%/yr | 40% | 1.59% | $5,199 | $199 |

**A realistic planning range is $6,300–$8,400**, and the outcome depends more
on gold's next decade than on anything in the trading rules.

## What to actually hold

$5,000 at 70% invested, 50/50, whole shares:

| symbol | price | target | shares | value | weight |
|---|---:|---:|---:|---:|---:|
| **SCHX** (US large cap) | $30.25 | $1,750 | **57** | $1,724.25 | 50.1% |
| **IAU** (gold) | $81.70 | $1,750 | **21** | $1,715.70 | 49.9% |
| cash | | | | $1,560.05 | 31.2% |

SCHX stands in for IVV purely for granularity — IVV at $780 a share buys only 2
units of a $2,250 target and wrecks the weights. Their daily returns correlate
**0.9882** over 4,211 shared days, so it is the same exposure at a tradable
increment.

Each month: hold the asset if its price is above its 10-month moving average,
otherwise hold that half in cash. Roughly 25 switches per asset per 20 years —
about one trade each every ten months.

## Limits worth knowing before funding it

* **7.7% is the worst drawdown observed in 20.7 years, not a guarantee.** A
  crash worse than 2008 breaches it. Running at 0.70 rather than the 0.90 that
  sizes exactly to 10% is what buys the margin for that.
* **129 windows are not 129 independent trials.** They overlap heavily; the
  sample really contains about two independent decades.
* **The timing rule's risk-adjusted edge is not statistically significant**
  (bootstrap p = 0.29 on IVV). Its drawdown reduction is mechanical and
  reliable; its Sharpe improvement is two bear markets' worth of evidence.
* **Cash earns 0% here.** A real account earns something on the 10–50% sitting
  idle, so these figures understate by roughly that amount.
* **This is beta, harvested at a controlled risk level.** It is not alpha, and
  it does not need to be to answer the question asked.

## Files

`research/sizing_5k.json`, `mt5_ai_bridge/tactical_allocation.py`,
`research/tactical_test.py`, `research/TACTICAL_RESULT.md`.
