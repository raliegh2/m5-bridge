# V11 Intraday-Only Available-Data Backtest Report

Status: **RESEARCH ESTIMATE — NOT A RAW EXECUTION BACKTEST**

Branch: `v11-intraday-walkforward-profitability`

## What was tested

This report tests the V11 **intraday-only base-risk policy** against the available
V10 aggregate backtest result. The swing component has been removed.

Because the repository does not include the raw V8/V9/V10 accepted and rejected
candidate ledgers, the new V11 walk-forward harness cannot be run honestly yet.
The available test is therefore a **risk-reweighted replay estimate**:

- start from the V10 intraday engine-level net-profit contributions;
- remove `GBPUSD_SWING_V6` completely;
- apply the V11 intraday base-risk allocation to each remaining engine;
- preserve the intraday trade count as an estimate;
- do not assume any V11 quality-score promotion;
- do not assume any new trades admitted by the higher 0.90% open-risk cap.

This is useful for checking whether the V11 intraday base-risk plan is profitable,
but it is not enough to approve live execution.

## V10 intraday reference result

| Metric | V10 intraday-only reference |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $6,130.63 |
| Net profit | $1,130.63 |
| Return | 22.61% |
| Intraday trades | 132 |
| Weekly equivalent | $21.74/week |
| Excluded swing profit | $84.02 |
| Excluded swing trades | 7 |

## V11 intraday-only base-risk replay estimate

| Metric | V11 intraday-only estimate |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $6,046.89 |
| Net profit | $1,046.89 |
| Return | 20.94% |
| Intraday trades | 132 |
| Average trade | $7.93 |
| Weekly equivalent | $20.13/week |
| Difference versus V10 intraday | -$83.74 |
| V10 intraday profit retained | 92.59% |

## Intraday engine contribution estimate

| Engine | V10 risk | V11 base risk | V10 profit | Estimated V11 profit |
|---|---:|---:|---:|---:|
| EURUSD_SATELLITE_V7 | 0.35% | 0.30% | $195.83 | $167.85 |
| GBPJPY_SATELLITE_V7 | 0.35% | 0.25% | $195.19 | $139.42 |
| GBPUSD_SATELLITE_V3 | 0.30% | 0.30% | $739.62 | $739.62 |
| **Intraday total** | — | — | **$1,130.63** | **$1,046.89** |

## Removed swing component

| Removed engine | Type | V10 profit | Reason |
|---|---|---:|---|
| GBPUSD_SWING_V6 | Swing | $84.02 | Removed because V11 is strictly intraday/day-trading only. |

## Cost-stress estimate

The V10 report included 0.03R, 0.05R and 0.10R stress results. Scaling those
proportionally by the V11 intraday-only base-risk estimate gives:

| Additional cost | Estimated net profit | Weekly equivalent |
|---|---:|---:|
| 0.03R | $976.11 | $18.77/week |
| 0.05R | $929.44 | $17.87/week |
| 0.10R | $814.54 | $15.66/week |

## $50/week target gap

| Item | Value |
|---|---:|
| Target weekly profit | $50.00 |
| Target annual profit | $2,600.00 |
| Current V11 intraday weekly estimate | $20.13 |
| Required multiplier from V11 intraday estimate | 2.48x |
| Current V11 estimated average trade | $7.93 |
| Required average trade if still 132 trades | $19.70 |

## Interpretation

The V11 intraday-only base-risk replay remains profitable, but it does **not**
reach the $50/week target. Removing the swing component drops the available-data
estimate from the earlier mixed system to approximately **$20.13/week**.

This is the correct direction for a pure day-trading bot. The extra profit must
come from intraday-only improvements:

1. quality-score promotion of only the strongest intraday setups;
2. additional accepted intraday trades from the 0.90% open-risk cap;
3. setup-level intraday trade-management improvements;
4. new independent intraday setups that pass out-of-sample testing.

## Decision

**V11 is now an intraday-only system. The base-risk policy remains profitable but
is not yet a $50/week system.**

Estimated intraday-only result: **$1,046.89 net profit**, approximately
**$20.13/week**.

Do not merge to a live-execution branch from this estimate alone. The next
required step is to supply or regenerate the raw intraday candidate ledger and
run:

```powershell
python research\v11_intraday_walkforward.py `
  --trades research\v10_or_v11_intraday_trade_ledger.csv `
  --windows 6 `
  --min-pf 1.40 `
  --min-trades 30 `
  --min-pass-rate 0.70 `
  --out research\V11_WALKFORWARD_REPORT.md `
  --json-out research\v11_walkforward_report.json
```

## Limitation

This report uses aggregate engine-level V10 performance. It cannot measure:

- V11 quality-score filtering;
- exact trade timing;
- open-risk conflicts;
- newly freed or newly admitted intraday candidates;
- spread expansion;
- slippage;
- broker execution differences;
- true walk-forward pass rate.

A full profitability test requires the raw accepted/rejected intraday candidate
ledgers or broker-native M15/M1 data.
