# "Improve until an edge is found" — what that produced

Date: 2026-08-16

The brief was to keep improving until an edge is found. I ran that search, and
I ran it under a protocol that makes the result interpretable rather than
flattering. This documents both.

## Part 1 — the levers that don't involve fitting

V16's parameters held exactly as locked; only the broker cost assumption and
the bar size varied. A parameter you did not choose cannot be overfitted, so
these are measurements, not a search.

Three mean-reverting symbols (VR < 1.0) × three timeframes × four cost tiers:

| timeframe | symbol | zero cost | tight | typical | wide |
|---|---|---:|---:|---:|---:|
| D1 | GBPUSD | −667 | −994 | −1,135 | −1,579 |
| D1 | EURUSD | **+146** | −158 | −305 | −742 |
| D1 | AUDUSD | **+143** | −391 | −569 | −1,225 |
| H4 | GBPUSD | **+2,684** | **+26** | −145 | −2,627 |
| H4 | EURUSD | **+1,657** | −1,286 | −1,521 | −4,548 |
| H4 | AUDUSD | **+1,937** | −1,781 | −2,071 | −5,601 |
| H1 | GBPUSD | **+2,752** | −16,223 | −16,354 | −28,422 |
| H1 | EURUSD | **+5,691** | −17,441 | −17,660 | −30,834 |
| H1 | AUDUSD | **+1,425** | −19,641 | −19,835 | −32,189 |

(out-of-sample net, walk-forward, 5 folds)

**At zero cost, 8 of 9 are profitable out of sample.** The gross edge is real
and it is present exactly where the variance ratio said it would be.

**Its size is the problem.** Those zero-cost profit factors run 1.01–1.06. A
1–6% gross edge cannot pay a cost that runs 3–15% of gross. The best realistic
result in the whole table is GBPUSD H4 at tight costs: **+$25.80 out of sample,
profit factor 1.001** — breakeven to three decimals, on 33 years.

No timeframe or cost tier clears the gates. H1 is worst: more trades means more
friction, and the same tiny edge paid twelve thousand times.

## Part 2 — the exhaustive search, run honestly

**Protocol.** Split the series once: first 60% is the search set, last 40% is a
holdout that is not touched during the search. Score all 684 specifications
(entry_z × exit_z × lookback × stop offset × time stop) on the search set. Take
the single best. Evaluate it on the holdout **once**. Deflate by the trial
count.

### GBPUSD H4, tight costs

| | result |
|---|---|
| best spec found | entry 2.5σ, exit 0.25σ, lookback 10, stop 5.5σ, hold 60 |
| **in-sample** | **+933.51, PF 1.114**, 489 trades |
| **holdout** | **−142.49, PF 0.969**, 305 trades |

33 of 684 specs were profitable in-sample. Picking the best of those is exactly
what "improve until an edge is found" means, and it produced a profit factor of
1.114 — a result that would look entirely publishable in `research/`.

It lost money on data it had not seen.

### EURUSD H4, tight costs

| | result |
|---|---|
| best spec found | entry 1.5σ, exit 0.25σ, lookback 10, stop 4.5σ, hold 20 |
| **in-sample** | **+3,065.70, PF 1.050**, 3,148 trades |
| **holdout** | **−2,862.41, PF 0.902**, 2,076 trades |

Three thousand trades and a clean profit in-sample. It lost nearly three
thousand dollars out of sample.

## The number that settles it

Of the specifications that were **profitable in-sample**, how many stayed
profitable **out of sample**?

| symbol | profitable in-sample | of those, profitable out | survival rate |
|---|---:|---:|---:|
| GBPUSD | 33 | 12 | **36.4%** |
| EURUSD | 42 | 2 | **4.8%** |

**A coin flip is 50%. Both are below it.**

On this data, a profitable backtest is not merely uninformative about the
future — it is mildly *anti*-informative. The specs that fit the past best are
the ones most tuned to its noise, and noise does not repeat. Searching harder
does not improve the expected out-of-sample result; it degrades it, while
making the in-sample number look better.

That is the whole mechanism behind twenty-five profitable reports and an
account with no edge, measured directly on your own data.

## Deflated verdict

Trials on record: **1,379**. Deflated Sharpe of the winner: **0.0040** against a
0.95 gate. **FAIL.**

The deflation bar rises with the trial count, which is precisely why searching
cannot manufacture a passing result. Every one of those 1,379 specifications is
recorded in `research/v15_trials.json` and will count against any future claim
made on this dataset.

## So is there an edge?

**Yes, and it is too small to trade.** That is the honest, complete answer, and
every link is now measured rather than assumed:

1. FX majors mean-revert mildly at H4 — variance ratios 0.877–0.982, and the
   effect appears on exactly the symbols the statistic identifies.
2. Harvested with zero friction it is worth a profit factor of about
   **1.01–1.06**.
3. Retail costs consume **3–15%** of gross, which is more than the edge.
4. It cannot be rescued by trading less often (V17, negative even gross), by
   changing timeframe (H1 far worse, D1 too thin), or by choosing a better
   broker tier (tight costs still land at PF 1.001).
5. It cannot be found by searching, because on this data searching is
   anti-predictive.

## What would actually change this answer

Not more searching. Three things, in order of how much they would move it:

1. **Materially lower transaction costs.** At zero cost 8 of 9 configurations
   are profitable out of sample. The entire question is the 1–6% gross edge
   against the 3–15% cost. An ECN account at raw spread plus commission, or
   trading size where commission dominates spread, is the only lever that acts
   directly on the binding constraint. Get your actual fill costs and re-run
   `research/v16_lever_test.py` with them.
2. **A different universe.** These six symbols give 2.6 effective bets and all
   show the same small effect. Instruments with different drivers — indices,
   rates, futures with genuinely tighter relative costs — are where a larger
   effect could exist. This repo's tooling now transfers to them unchanged.
3. **A different effect entirely.** Mean reversion and momentum are both
   measured and both accounted for here. Anything materially better will come
   from information these bars do not contain: order flow, positioning, rates,
   or the calendar. The V14.22–14.25 order-flow work in `research/` was headed
   there and never validated.

## Files

| file | purpose |
|---|---|
| `research/v16_lever_test.py` | cost tier × timeframe grid, no fitting |
| `research/exhaustive_search.py` | search-set/holdout protocol with survival diagnostic |
| `research/v16_levers.json` | the lever table above |
| `research/exhaustive_search_gbpusd.json` | GBPUSD search, 684 specs |
| `research/exhaustive_search_eurusd.json` | EURUSD search, 684 specs |

748 tests pass.
