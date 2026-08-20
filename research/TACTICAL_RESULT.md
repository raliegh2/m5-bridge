# Timing the assets worth holding

Date: 2026-08-18
Status: **drawdown benefit real and large; risk-adjusted benefit not proven**

## Why the target moved

Everything measured in this repo points the same way. The ETF reversion rule
earned a profit factor and was beaten by holding on six of six. Cross-sectional
ranking added +1.44% a year at **t = 0.815**, which is zero. Six FX engines and
gold all lost after costs. The one thing that reliably made money across 22.9
years was *being long*.

So aiming at absolute profit is aiming at something holding already provides.
What holding does not provide is a survivable drawdown — buy-and-hold equity
gives up **51%** in 2008, and this account's stated ceiling is 10%. That is the
gap worth attacking, so the benchmark is buy-and-hold and the metric is
risk-adjusted, not total return.

The rule is Faber (2007), unchanged: hold the asset when its price is above its
**10-month moving average**, otherwise hold cash. Long or flat, never short.
Published parameters, frozen in `research/tactical_locked.json`. Cash earns
zero, which is pessimistic on purpose.

## Result, 22.9 years, costs charged on every switch

| symbol | in mkt | switches | strategy CAGR | hold CAGR | **strategy DD** | **hold DD** | strategy SR | hold SR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **IVV** | 78% | 25 | 8.38% | 9.19% | **28.2%** | 51.0% | **0.780** | 0.654 |
| **VTI** | 77% | 27 | 7.95% | 9.33% | **30.8%** | 50.8% | **0.712** | 0.639 |
| **ONEQ** | 76% | 29 | 10.84% | 12.78% | **17.9%** | 53.4% | **0.855** | 0.714 |
| IWM | 68% | 39 | 3.14% | 7.97% | 44.5% | 53.5% | 0.279 | 0.483 |
| TQQQ | 77% | 23 | 22.73% | 34.16% | 65.2% | 77.5% | 0.738 | 0.847 |
| EEM | 63% | 49 | 4.43% | 6.23% | 46.7% | 54.8% | 0.360 | 0.402 |
| XAUUSD | 71% | 38 | 8.24% | 11.23% | 30.6% | 41.0% | 0.590 | 0.697 |

* Drawdown falls on **7 of 7**, by **16.9 points** on average.
* Sharpe beats holding on **3 of 7** — the broad large-cap indices, which are
  the same names the ETF portfolio's walk-forward admission independently kept.
* **0 of 7** get inside the 10% drawdown ceiling. The best is ONEQ at 17.9%.
* Average cost: **3.6 points of annual return** given up.

## The improvement is insurance, and it is not statistically proven

A paired stationary bootstrap (blocks of 6 months, 4,000 draws) on the Sharpe
difference, plus out-of-sample folds:

| symbol | Sharpe diff | bootstrap p | folds better | fold-by-fold Sharpe difference |
|---|---:|---:|---:|---|
| ONEQ | +0.148 | 0.214 | 2/5 | +0.718, −0.152, −0.053, −0.002, +0.041 |
| IVV | +0.125 | 0.294 | 2/5 | **+0.662**, −0.148, −0.040, −0.270, **+0.454** |
| VTI | +0.073 | 0.363 | 2/5 | +0.622, −0.163, −0.074, −0.276, +0.322 |
| EEM | −0.039 | 0.597 | 2/5 | — |
| TQQQ | −0.106 | 0.759 | 1/5 | — |
| XAUUSD | −0.107 | 0.821 | 1/5 | — |
| IWM | −0.204 | 0.925 | 2/5 | — |

**Nothing is significant.** The best p-value is 0.21 against a 0.05 bar, and
none survives Benjamini-Hochberg across the seven assets.

The fold pattern says exactly what the rule is. IVV's improvement lives in fold
0 (2004–2009) and fold 4 (2022–2026) and is negative in the three folds in
between. Those two folds contain the two bear markets in the sample. The rule
pays in crashes and charges premium the rest of the time — which is what a
trend filter is *supposed* to do, and it means the evidence base is **two
events**. Two events cannot produce a significant Sharpe difference, and no
amount of further testing on this history will change that.

There is also a caveat the "published parameters" defence does not cover: the
10-month moving average is among the most examined rules in retail finance.
Its parameters were not searched *here*, but they were searched by the
literature, and the 2008 that made it famous is inside this sample.

## What this supports doing, stated honestly

Trading **IVV and VTI** — chosen by walk-forward admission, not hindsight —
with the moving-average overlay is defensible, and it is the best-performing
thing measured in this entire investigation:

* about **8.4% a year** over 22.9 years, genuinely profitable in absolute terms;
* maximum drawdown roughly **halved**, 51% to 28%;
* at a cost of about **1 point of annual return** versus holding.

What it is not: alpha. The return is equity beta, the drawdown reduction is
mechanical rather than predictive, and the risk-adjusted improvement is not
statistically distinguishable from luck. Anyone quoting the 0.780 Sharpe
against 0.654 as an edge is quoting a p = 0.29 result.

And it does not meet the 10% drawdown ceiling. Nothing tested does. Reaching
10% on an equity book requires either position sizing well below fully
invested, or an asset mix that is not all equities.

## Feasibility on the real account

$4,802.43, whole shares only, at current prices:

| holding | shares | invested | granularity |
|---|---:|---:|---:|
| IVV alone | 6 | $4,680.24 (97.5%) | 16.2% per share |
| VTI alone | 12 | $4,606.20 (95.9%) | 8.0% per share |
| ONEQ alone | 45 | $4,739.40 (98.7%) | 2.2% per share |
| 50/50 IVV+VTI | 3 + 6 | $4,643.22 (96.7%) | achieved 50.4% / 49.6% |

A 50/50 IVV+VTI book lands within half a point of its target weights, so whole
shares are not an obstacle here. ONEQ is the most granular of the three and
also showed the largest drawdown reduction — worth noting, though its Sharpe
edge is no more significant than the others'.

## Files

| file | purpose |
|---|---|
| `mt5_ai_bridge/tactical_allocation.py` | the locked 10-month timing rule |
| `research/tactical_locked.json` | frozen parameters |
| `research/tactical_test.py` | the table above |
| `research/tactical.json`, `research/tactical_significance.json` | full results |

928 tests pass.
