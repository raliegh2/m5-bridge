# V13 ICT Lower-Drawdown High-Activity Report

Status: **research-only paper backtest; drawdown-control patch applied after prior review**

## Objective

The previous high-frequency ICT liquidity-fade profile was profitable but had too much drawdown. This run keeps the same frequent 60-minute-gap signal stream, but lowers drawdown by moving earlier into micro-risk mode instead of halting trades.

## Selected profile: low_dd_active_gap60

| Setting | Value |
|---|---:|
| Normal risk | 0.30% |
| Micro risk | 0.03% |
| Signal spacing | 60-minute minimum gap per symbol |
| Rolling quality window | 50 trades |
| Rolling throttle trigger | -12.00R |
| Cooldown | 21 days at micro risk |
| Daily soft brake | -10.00R |
| Drawdown micro-risk trigger | 5.00% |

## Comparison

| Profile | Ending balance | Net result | Return | Max DD | PF | Trades | Active-risk trades | Micro-risk trades | Avg risk |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| previous_balanced_gap60 | $9,432.93 | $4,432.93 | 88.66% | 13.70% | 1.081 | 11,649 | 3,413 | 8,236 | 0.138% |
| low_dd_active_gap60 | $10,819.14 | $5,819.14 | 116.38% | 7.94% | 1.114 | 11,649 | 3,743 | 7,906 | 0.117% |
| extra_safe_gap60 | $5,744.27 | $744.27 | 14.89% | 3.89% | 1.072 | 11,649 | 341 | 11,308 | 0.036% |

## Improvement vs previous balanced profile

| Metric | Previous balanced | Low-DD active | Change |
|---|---:|---:|---:|
| Net result | $4,432.93 | $5,819.14 | $1,386.21 |
| Return | 88.66% | 116.38% | 27.72 percentage points |
| Max drawdown | 13.70% | 7.94% | -5.76 percentage points |
| Profit factor | 1.081 | 1.114 | 0.032 |

## Decision

The **low_dd_active_gap60** profile is the better research candidate. It keeps the same 11,649-trade activity level, improves net result from the previous balanced profile, and lowers max drawdown from 13.70% to 7.94%.

## Important limitation

This remains research-only because the risk-throttle adjustment was made after reviewing historical OOS behavior. It should be forward-tested before live deployment.
