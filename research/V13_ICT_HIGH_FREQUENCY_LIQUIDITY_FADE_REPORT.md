# V13 ICT High-Frequency Liquidity-Fade Expansion Report

Status: **research-only paper backtest; expanded trade frequency after prior OOS review**

## Objective

The previous consistent-throttle version traded all 112 selected ICT signals, but it was still too inactive for an intraday bot. This expansion tests a higher-frequency ICT-style signal pool while removing the weakest overtrading families.

## Signal construction

Included:

- liquidity sweep/reclaim signals;
- breakout-fade signals;
- M5 directional regime;
- M1 entry timing;
- London/New York activity window;
- risk throttling instead of hard stops.

Excluded:

- EMA-only activity overtrading;
- broad activity-blend signals that produced poor expectancy;
- full hard-stop cooldown behavior.

## Important methodology warning

This is not a clean untouched out-of-sample result. The higher-frequency family was selected after reviewing the earlier OOS behavior. Treat it as a research candidate that must be forward-tested before any live use.

## Trade activity

| Metric | Value |
|---|---:|
| Raw selected liquidity-fade signals | 161,494 |
| Deduped tradable signals, 60-min gap | 11,649 |
| Approximate average trades per week | 64 |
| Symbols | GBPUSD, GBPJPY |

## Recommended profile: balanced_gap60

This is the best balance between frequent trading and drawdown control.

| Metric | Value |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $9,432.93 |
| Net result | $4,432.93 |
| Return | 88.66% |
| Trades | 11,649 |
| Win rate | 47.75% |
| Profit factor | 1.081 |
| Max drawdown | 13.70% |
| Active risk | 0.35% |
| Micro risk | 0.05% |
| Average risk | 0.138% |
| Active-risk trades | 3,413 |
| Micro-risk trades | 8,236 |

## More aggressive research options

| Profile | Ending balance | Net result | Return | Trades | Profit factor | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| active_gap60 | $17,961.89 | $12,961.89 | 259.24% | 11,649 | 1.088 | 20.69% |
| growth_gap60 | $9,803.16 | $4,803.16 | 96.06% | 17,279 | 1.051 | 20.60% |
| balanced_gap60 | $9,432.93 | $4,432.93 | 88.66% | 11,649 | 1.081 | 13.70% |
| balanced_gap15 | $13,735.10 | $8,735.10 | 174.70% | 24,825 | 1.078 | 14.15% |

## Decision

The high-frequency liquidity-fade expansion makes the bot trade far more often and produces a much stronger historical paper result. However, it is not production proof because this expansion was designed after reviewing the earlier OOS failures. The safer candidate is **balanced_gap60**, not the highest-return profile, because it trades frequently while keeping drawdown materially lower than the aggressive variants.
