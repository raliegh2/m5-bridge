# V13 V12 Final + V11 Intraday Available-Data Backtest Report

Status: **AVAILABLE-DATA RESEARCH ESTIMATE — NOT A RAW EXECUTION BACKTEST**

Branch: `v13-v12-final-plus-v11-intraday`

## What was built

This branch combines both systems at the research/profile level:

- **V12 Final** remains the master portfolio and risk-control layer.
- **V11 Intraday** is added as a separate day-trading signal source.
- V11 swing exposure remains disabled.
- The broker order API remains disabled.
- Human review remains required.

The integration profile is implemented in:

```text
mt5_ai_bridge/v13_v12_plus_v11_intraday_profile.py
```

The available-data estimate runner is implemented in:

```text
research/v13_v12_final_plus_v11_intraday_available_backtest.py
```

The numeric result is stored in:

```text
research/v13_v12_plus_v11_intraday_backtest_results.json
```

## Important methodology note

The repository does **not** currently contain a merged chronological ledger of
V12 Final candidates and V11 intraday candidates. Therefore this report uses the
available reported results:

1. V12 Final optimized maximum-history result: **$3,201.58**.
2. V12 Final optimized one-year window result: **$289.65**.
3. V11 intraday-only available-data estimate: **$1,046.89**.

This means the combined numbers below are **capacity-unadjusted additive research
estimates**. They show the gross possible increase if V11 intraday profit is
additive, but they do not prove the same result would survive a real shared
portfolio replay.

## Scenario table

| Scenario | Window / basis | Net profit | Ending balance | Return | Trades | PF | Max DD | Stress DD |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| V12 Final optimized | Maximum history | $3,201.58 | $8,201.58 | 64.03% | 918 | 1.606 | 4.93% | 5.25% |
| V11 intraday-only | Approx. one-year synchronized replay estimate | $1,046.89 | $6,046.89 | 20.94% | 132 | N/A | N/A | N/A |
| V13 combined estimate | V12 max-history + V11 available intraday | **$4,248.47** | **$9,248.47** | **84.97%** | 1050* | N/A | N/A | N/A |

`*` Trade count is shown as 918 + 132 for visibility only. A true replay may
accept fewer trades because of max-position, symbol-cap and open-risk conflicts.

## Profit increase versus V12 Final maximum-history

| Base | Added component | Base profit | Added profit | Combined estimate | Profit increase | Increase vs base |
|---|---|---:|---:|---:|---:|---:|
| V12 Final maximum-history | V11 intraday-only estimate | $3,201.58 | $1,046.89 | **$4,248.47** | **+$1,046.89** | **+32.70%** |

## Rough one-year comparison

The V12 Final report also includes a one-year optimized window. Comparing the
V11 available-data estimate to that one-year V12 window gives a much larger
increase, but it is still not a true same-ledger replay.

| Base | Added component | Base profit | Added profit | Combined estimate | Profit increase | Increase vs base |
|---|---|---:|---:|---:|---:|---:|
| V12 Final 1-year window | V11 intraday-only estimate | $289.65 | $1,046.89 | **$1,336.54** | **+$1,046.89** | **+361.43%** |

## Interpretation

The available data suggests that adding V11 intraday to V12 Final could increase
gross research profit. The cleanest headline comparison is:

```text
V12 Final maximum-history profit:     $3,201.58
+ V11 intraday available-data profit: $1,046.89
= V13 combined additive estimate:     $4,248.47
```

That is a **$1,046.89** increase, or **+32.70%** versus the V12 Final
maximum-history result.

However, this is not yet an approved production result because the estimate does
not account for:

- V12 and V11 signals arriving at the same time;
- V11 consuming a position slot that V12 would have used;
- V11 consuming GBP risk capacity;
- symbol-cap conflicts;
- max-open-risk conflicts;
- exact spread/slippage at the V11 intraday entry time;
- whether V11 entries remain profitable when replayed through the V12/V13 risk governor.

## Required true combined backtest

The correct final validation is:

1. Export or regenerate V12 Final accepted and rejected candidate ledgers.
2. Export or regenerate V11 intraday accepted and rejected candidate ledgers for the same timestamp range.
3. Merge all candidates by `entry_time`.
4. Replay them chronologically through the V13 profile.
5. Track accepted trades, rejected trades, rejected reasons, net profit, PF, drawdown, stress drawdown and symbol exposure.

## Decision

The branch is built and the available-data estimate is positive:

- V12 Final max-history: **$3,201.58**
- V13 additive estimate: **$4,248.47**
- Increase: **+$1,046.89 / +32.70%**

Keep the branch in research mode until the true merged chronological replay is
available.
