# Cross-sectional momentum on the tradable equity universe

Date: 2026-08-18
Status: **FAILED** — and the failure is concentrated in the one leg this data
source cannot measure honestly.

## What was tested

The account can order 11,438 individual equities and nothing had ever been run
against them. This is one pre-registered trial: Jegadeesh & Titman (1993) 12-1
momentum, unchanged. Rank on the return from 252 to 21 days ago, rebalance
every 21 days, long the top 20 and short the bottom 20 equally weighted.
Parameters are the published ones, frozen in `research/cross_sectional_locked.json`.

Market-neutral by construction, which was the point. The ETF result failed
because a long-biased rule in a rising asset earns a profit factor while adding
nothing, and holding beat it six times out of six. A book that is long 20 and
short 20 cancels the market's direction, so its return cannot be drift.

**Universe**: 166 stocks, 15.4–22.9 years of daily history each, median spread
**0.017%**, priced $5–400, selected by rule from the first 718 symbols
alphabetically. Prices back-adjusted for **59 splits** before use.

## Result

| | gross | net of spread |
|---|---:|---:|
| return, 22.9 years | −88.23% | **−88.51%** |
| annualised | −9.30% | −9.40% |
| Sharpe | −0.317 | −0.323 |
| profit factor | 0.693 | 0.688 |
| max drawdown | 91.83% | 91.99% |
| hit rate | 52.8% | 52.5% |

Walk-forward, net: **1 of 5 folds positive** (+16.6%, −47.3%, −12.9%, −75.4%,
−53.0%).

Every gate fails except trade count:

| gate | verdict |
|---|---|
| net profitable | FAIL |
| profit factor ≥ 1.10 | FAIL (0.688) |
| ≥ 200 out-of-sample trades | pass (10,520) |
| majority of folds positive | FAIL (1 of 5) |
| deflated Sharpe ≥ 0.95 | FAIL (0.024 against one trial) |

**Costs are not the reason.** Median spread is 0.017% and turnover-charged
drag is 0.009% per period — gross and net differ by 0.28 percentage points
over 22.9 years. This is the first strategy in this investigation that costs
did not decide, and it still lost by 88%.

## Where the loss actually comes from

| leg | mean per period |
|---|---:|
| long the winners | **+1.444%** |
| short the losers | **−2.543%** |

The long leg works. The short leg loses two and a half percent a month —
shorting the worst 12-month performers was catastrophic over this period.

That is the known momentum-crash pattern: beaten-down names rebound violently
off market bottoms, and 2009, 2020 and 2021 are all inside this window.

## Why this test cannot settle the question

**Every name in the universe is a survivor.** The screen requires 15+ years of
history, and the terminal only lists what is listed *today*. So the "losers"
being shorted are, by construction, companies that fell hard **and then
survived to still be listed in 2026** — precisely the population that
rebounded. The ones that kept falling and delisted are absent from the sample
entirely.

Survivorship bias does not hit this strategy evenly. It inflates the long leg
mildly and **destroys the short leg specifically**, because the short leg's
losses are exactly the recoveries that survivorship selects for. A −2.543% per
period short leg on a survivors-only universe is not a measurement of shorting
losers; it is a measurement of the bias.

The honest conclusion is therefore narrower than "momentum does not work here":

* the **long leg is not evidence** either — it is long equity in a bull market,
  which is the ETF finding again;
* the **short leg is not measurable** on this data source at all;
* so the market-neutral combination cannot be evaluated, in either direction.

**Flipping the sign would be worse, not better.** Long the losers and short the
winners would show a large profit on this data, and it would be almost entirely
the same bias read backwards. A reversion result here would be the most
flattering and least trustworthy number in the whole investigation.

## What would settle it

A **point-in-time universe including delisted companies** — every name that was
listed and tradable at each historical rebalance date, survivors and failures
alike. That is a property of the data source, not of the code: MT5 cannot
provide it, because a broker lists what it can trade now.

Everything else is ready. The strategy, the walk-forward, the gates, the
cost model and the live risk integration all run; they are waiting on a
universe that includes the companies that died.

## Feasibility, separately

Position sizing is *not* the blocker, contrary to expectation: 40 positions at
one share each costs about $507 of notional against a $4,802 account. What one
share does prevent is equal weighting — a $5 name and a $390 name cannot carry
equal risk when the smallest increment is one share — so an implementation
would need volatility-scaled share counts rather than equal weights.

## Files

| file | purpose |
|---|---|
| `mt5_ai_bridge/cross_sectional.py` | the locked 12-1 ranking book |
| `mt5_ai_bridge/cross_sectional_live.py` | sizing through the live RiskEngine |
| `tools/export_equity_universe.py` | rule-based, resumable universe export |
| `research/cross_sectional_test.py` | this runner |
| `research/cross_sectional.json` | full results |

914 tests pass.
