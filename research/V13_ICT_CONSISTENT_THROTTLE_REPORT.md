# V13 ICT Consistent Intraday Throttle Report

Status: **research-only paper backtest; hard trade halts replaced with risk-throttling**

## Objective

The previous guard made the bot too inactive. This version keeps the ICT M5/M1 setup trading consistently by replacing most hard pauses with a **probation-risk mode**. The bot still takes signals, but weak sequences are traded at micro risk instead of fully halted.

## Rule change

| Component | Previous strict guard | New consistent guard |
|---|---:|---:|
| Normal risk | 0.35% | 0.50% balanced / 0.75% aggressive |
| Weak-sequence response | Stop taking trades for months | Continue trading at 0.05% probation risk |
| Rolling quality window | 8 trades | 20 trades |
| Rolling trigger | -0.50R | -3.00R |
| Cooldown | 90 days hard stop | 120 days micro-risk mode |
| Daily loss response | Stop/skip after -1.50R | Micro-risk after -2.00R |
| Total DD response | Stop/skip near 8.80% | Micro-risk near 8.80% |

## Profile comparison

| Profile | Ending balance | Net profit | Return | Signals traded | Active-risk trades | Micro-risk trades | Win rate | PF | Max DD | Avg risk |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| balanced_consistent | $5,170.67 | $170.67 | 3.41% | 112 | 66 | 46 | 37.50% | 1.180 | 3.82% | 0.315% |
| aggressive_consistent | $5,282.84 | $282.84 | 5.66% | 112 | 66 | 46 | 37.50% | 1.205 | 5.70% | 0.462% |
| previous_strict_guard | $5,037.50 | $37.50 | 0.75% | 112 | 45 | 67 | 17.86% | 1.085 | 2.08% | 0.141% |

## Recommended profile

The balanced consistent profile is the safer improvement because it keeps all 112 ICT signals tradable, increases net result versus the failed baseline, and keeps drawdown below the prior ICT drawdown.

## Important limitation

This is still a research patch because it was designed after reviewing the prior OOS failure. It should be validated on fresh forward data before live deployment.
