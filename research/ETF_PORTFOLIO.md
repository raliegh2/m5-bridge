# The tradable-ETF portfolio, on corrected prices

Date: 2026-08-18

The index CFDs this strategy was built against are all `trade_mode = DISABLED`
on this account, and so are QQQ, SPY and DIA. Six ETFs are fully tradable and
carry 22.9 years of history, so this is the version of the model that could
actually be ordered here.

Building it out surfaced a data defect that changes the previous conclusion.

## 1. The price series contained unadjusted splits

A split is not a price move. When a fund splits ten-for-one the quote drops 90%
overnight while every holder's position value is unchanged.

| ETF | date | quote | implied split |
|---|---|---|---|
| ONEQ | 2021-04-08 | 531.98 → 53.58 | 10-for-1 |
| EEM | 2005-06-09, 2008-07-24 | ×0.333 each | 3-for-1 |
| IWM | 2005-06-09 | 123.33 → 61.63 | 2-for-1 |
| VTI | 2008-06-18 | 136.18 → 67.76 | 2-for-1 |
| TQQQ | seven dates | ×0.5, ×0.333 | repeated |
| IVV | — | — | clean |

Two things break. A mean-reversion rule sees a −90% bar as the largest dip in
the instrument's history and **buys it**, against a reversion that cannot
happen because the move never happened. And buy-and-hold is understated by the
split factor, so the benchmark the strategy is judged against is far too low.

`mt5_ai_bridge/corporate_actions.py` detects and back-adjusts these. A split is
distinguished from a crash by the bar's own range: a split day is quiet and
only the scale changed (ONEQ ranged 0.7%), while a collapse is violent intraday
(TQQQ on 2020-03-12 gapped to 0.823 and then ranged 35.8%). The detector finds
seven TQQQ splits and correctly leaves the four real crashes alone.

`data_audit` now rates an unadjusted split **fatal** rather than a minor return
outlier, so the series is refused until it is corrected.

## 2. What the correction does to the previous result

| ETF | strategy, raw | strategy, adjusted | buy & hold, raw | buy & hold, adjusted |
|---|---:|---:|---:|---:|
| TQQQ | +13.7% (PF 1.512) | **−5.1%** | −7.5% | **+17,610.5%** |
| ONEQ | +12.3% (PF 1.301) | **−17.4%** | +44.0% | **+1,329.5%** |
| IVV | +16.4% | +16.4% | +666.9% | +666.9% |
| VTI | +10.8% | +9.5% | +295.2% | +694.2% |
| IWM | +8.8% | +8.6% | +204.2% | +508.8% |
| EEM | +2.3% | +0.2% | −51.8% | +336.5% |

**The two best results were manufactured by the defect.** TQQQ's 1.512 profit
factor and ONEQ's 1.301 — the strongest numbers in the investigation — both
turn negative once the splits are removed.

The earlier reading was that the strategy "beats holding only on the two assets
where holding lost money, TQQQ and EEM." Holding did not lose money on either;
those were split artifacts. Corrected, it is **beaten by holding on six of
six**, and the short side loses money on three of six.

## 3. The portfolio, on one shared account

Six independent per-symbol backtests cannot show what happens when they share
capital. `mt5_ai_bridge/etf_portfolio.py` runs them on one account at the real
**$4,802.43** balance.

**Factor exposure binds hard.** `portfolio_v15` caps correlated risk per
*currency*, and `currency_exposure("ONEQ")` raises — an ETF ticker is not a
currency pair, and all six are USD-quoted, so that cap would do nothing while
the positions rise and fall together:

| pair | correlation |
|---|---:|
| IVV / VTI | **0.989** |
| VTI / IWM | 0.909 |
| IVV / IWM | 0.883 |

Equally weighted the six carry **1.49 effective bets, not six**. Exposure is
therefore capped per equity factor, with TQQQ counted three times because it is
a 3x fund.

**Whole shares.** One ETF lot is one share and shares are indivisible, so
sizing floors to whole shares and refuses any entry whose single-share risk
already exceeds the budget rather than rounding up past it.

Results, 22.9 years, tight costs:

| profile | trades | net | return | PF | max DD |
|---|---:|---:|---:|---:|---:|
| flat risk | 1,110 | +$104.50 | +2.18% | 1.011 | 19.37% |
| beta-scaled risk | 1,146 | −$23.61 | −0.49% | 0.997 | 19.37% |
| **walk-forward admission** | 692 | **+$550.93** | **+11.47%** | — | — |

Beta-scaled sizing exists because without it TQQQ takes **zero trades in 22.9
years** — a 0.5% position asks for 1.5 factor units against a 1.0 unit cap and
can never be admitted. Silent permanent exclusion reads as a decision and was
not one.

Walk-forward admission trades only symbols that were profitable in *earlier*
folds, so the choice is never made with hindsight. It converges immediately and
stays there:

| fold | window | admitted |
|---|---|---|
| 0 | 2003-09 .. 2008-04 | all six |
| 1–4 | 2008-04 .. 2026-08 | **IVV, VTI** |

That is the deployable answer to "which of these do I trade": the evidence
picks IVV and VTI and drops the other four, out of sample, and doing so turns
−0.49% into +11.47%.

## 4. What this is and is not

It is a working, deployable portfolio: it runs on symbols the account can
actually order, sizes on the real balance in whole shares, caps correlated
exposure honestly, and picks its symbols without hindsight.

It is not an edge. **+11.47% over 22.9 years is 0.47% a year**, against
+666.9% for holding IVV over the same period, and the full-sample drawdown is
**19.4%** — roughly double the 10% ceiling set for this account. Shared
capital, factor caps and whole-share sizing do not create an edge; they bound
the damage of not having one.

The untested frontier is unchanged: **11,439 individual equities** are fully
tradable here and nothing has been run against them. Cross-sectional strategies
on single stocks are a genuinely different problem from anything tried so far.

## Files

| file | purpose |
|---|---|
| `mt5_ai_bridge/corporate_actions.py` | split detection and back-adjustment |
| `mt5_ai_bridge/etf_portfolio.py` | shared-account replay, factor caps, whole shares |
| `research/etf_portfolio_test.py` | the runner behind the tables above |
| `research/etf_portfolio.json` | full results |
| `research/etf_beta_adjusted.json` | corrected beta check |

888 tests pass.
