# Strategy Engine V9 Research Backtest Report

## Strategy tested

The candidate changes only `GBPUSD_SATELLITE_V2` entry admission:

- Allowed UTC entry hours: **07, 10, 11, 12, 14, 15, 16**
- Blocked UTC entry hours: **08, 09, 13, 17**
- EURUSD Satellite V7, GBPJPY Satellite V7, and GBPUSD Swing V6 remain unchanged.
- Shared V8 risk controls remain unchanged.

## Full synchronized replay

| Metric | V8 control | V9 candidate | Change |
|---|---:|---:|---:|
| Ending balance | $5,805.46 | $5,983.63 | $178.16 |
| Net profit | $805.46 | **$983.63** | **+$178.16** |
| Return | 16.11% | **19.67%** | +3.56 pp |
| Trades | 217 | 139 | -78 |
| Win rate | 43.32% | **51.80%** | +8.48 pp |
| Profit factor | 1.5946 | **2.4417** | +0.8471 |
| Average trade | $3.71 | **$7.08** | +$3.36 |
| Realized drawdown | 3.002% | **1.575%** | -1.427 pp |
| Open-risk stress drawdown | 3.487% | **2.067%** | -1.420 pp |

V9 increased historical net profit by **22.12%** while reducing both realized and stress drawdown. It finished at **$5,983.63**, which is **$16.37 below** the $1,000 one-year research target.

## Engine contribution under V9

| Engine | Trades | Net profit | Profit factor | Win rate |
|---|---:|---:|---:|---:|
| EURUSD_SATELLITE_V7 | 22 | $137.18 | 2.0585 | 50.00% |
| GBPJPY_SATELLITE_V7 | 19 | $137.59 | 2.8684 | 52.63% |
| GBPUSD_SATELLITE_V2 | 91 | $604.89 | 2.3471 | 49.45% |
| GBPUSD_SWING_V6 | 7 | $103.98 | 4.4619 | 85.71% |

## Period robustness check

| Period | Strategy | Net profit | Profit factor | Win rate | Realized DD |
|---|---|---:|---:|---:|---:|
| Before 2026 | V8 | $622.30 | 2.113 | 49.52% | 1.208% |
| Before 2026 | V9 | **$649.15** | **3.427** | **56.94%** | **0.797%** |
| 2026 validation segment | V8 | $162.90 | 1.230 | 37.50% | 3.002% |
| 2026 validation segment | V9 | **$296.04** | **1.806** | **46.27%** | **1.575%** |

The filter remained beneficial in the 2026 segment: profit increased from $162.90 to $296.04, and profit factor increased from 1.230 to 1.806.

## Candidate handling

- 80 GBPUSD satellite candidates were removed by the hour gate.
- 5 candidates remained rejected by the shared maximum-open-risk rule.
- One previously rejected GBPJPY candidate became admissible after the filter freed portfolio capacity; its result is included.

## Verification

```text
pytest -q
2 passed
```

The V8 control replay exactly reproduced the supplied V8 ending balance, accepted-trade count, rejected-candidate count, profit factor, and drawdown before the V9 filter was tested.

## Limitations and decision

This result is a synchronized candidate-ledger replay, not a new tick-level OHLC simulation. The hour gate was selected after inspecting the same one-year ledger, so the full result is in-sample. The 2026 split is supportive but not completely untouched. V9 should therefore remain a research candidate and should not replace V8 in live execution until it passes a raw M15 OHLC rerun, transaction-cost stress, and demo forward testing.
