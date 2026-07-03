# V11 Available-Data Backtest Report

Status: **RESEARCH ESTIMATE — NOT A RAW EXECUTION BACKTEST**

Branch: `v11-intraday-walkforward-profitability`

## What was tested

This report tests the V11 **base-risk policy** against the available V10 aggregate
backtest result. Because the repository does not include the raw V8/V9/V10
accepted and rejected candidate ledgers, the new V11 walk-forward harness cannot
be run honestly yet.

The available test is therefore a **risk-reweighted replay estimate**:

- start from the V10 engine-level net-profit contributions;
- apply the V11 base-risk allocation to each engine;
- preserve the V10 trade count as an estimate;
- do not assume any V11 quality-score promotion;
- do not assume any new trades admitted by the higher 0.90% open-risk cap.

This is useful for checking whether the V11 base risk plan is profitable, but it
is not enough to approve live execution.

## V10 reference result

| Metric | V10 result |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $6,214.66 |
| Net profit | $1,214.66 |
| Return | 24.29% |
| Trades | 139 |
| Average trade | $8.74 |
| Profit factor | 2.4007 |
| Realized drawdown | 2.11% |
| Open-risk stress drawdown | 2.79% |
| Weekly equivalent | $23.36/week |

## V11 base-risk replay estimate

| Metric | V11 base-risk estimate |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $6,099.40 |
| Net profit | $1,099.40 |
| Return | 21.99% |
| Trades | 139 |
| Average trade | $7.91 |
| Weekly equivalent | $21.14/week |
| Difference versus V10 | -$115.25 |
| V10 profit retained | 90.51% |

## Engine contribution estimate

| Engine | V10 risk | V11 base risk | V10 profit | Estimated V11 profit |
|---|---:|---:|---:|---:|
| EURUSD_SATELLITE_V7 | 0.35% | 0.30% | $195.83 | $167.85 |
| GBPJPY_SATELLITE_V7 | 0.35% | 0.25% | $195.19 | $139.42 |
| GBPUSD_SATELLITE_V3 | 0.30% | 0.30% | $739.62 | $739.62 |
| GBPUSD_SWING_V6 | 0.40% | 0.25% | $84.02 | $52.51 |
| **Total** | — | — | **$1,214.66** | **$1,099.40** |

## Cost-stress estimate

The V10 report included 0.03R, 0.05R and 0.10R stress results. Scaling those
proportionally by the V11 base-risk estimate gives:

| Additional cost | Estimated net profit | Weekly equivalent |
|---|---:|---:|
| 0.03R | $1,025.07 | $19.71/week |
| 0.05R | $976.06 | $18.77/week |
| 0.10R | $855.40 | $16.45/week |

## $50/week target gap

| Item | Value |
|---|---:|
| Target weekly profit | $50.00 |
| Target annual profit | $2,600.00 |
| Current V11 base weekly estimate | $21.14 |
| Required multiplier from V11 base estimate | 2.36x |
| Current V11 estimated average trade | $7.91 |
| Required average trade if still 139 trades | $18.71 |

## Interpretation

The V11 base-risk replay remains profitable, but it does **not** reach the
$50/week target. The base policy is intentionally more conservative than V10 in
EURUSD, GBPJPY and GBPUSD swing exposure. That explains why the estimate falls
from $1,214.66 to $1,099.40.

This is not a failure of V11. V11 was designed to create a safer validation layer
first. The extra profit must come from one or more of the following after proper
walk-forward validation:

1. quality-score promotion of only the strongest setups;
2. additional accepted trades from the 0.90% open-risk cap;
3. setup-level trade-management improvements;
4. new independent setups that pass out-of-sample testing.

## Decision

**V11 base-risk policy remains profitable but is not yet a $50/week system.**

Estimated result: **$1,099.40 net profit**, approximately **$21.14/week**.

Do not merge to a live-execution branch from this estimate alone. The next
required step is to supply or regenerate the raw candidate ledger and run:

```powershell
python research\v11_intraday_walkforward.py `
  --trades research\v10_or_v11_trade_ledger.csv `
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
- newly freed or newly admitted candidates;
- spread expansion;
- slippage;
- broker execution differences;
- true walk-forward pass rate.

A full profitability test requires the raw accepted/rejected candidate ledgers or
broker-native M15/M1 data.
