# V13 ICT Research Guard Report

Status: **research-only paper backtest patch**

## What changed

The previous ICT setup kept taking signals through weak sequences, especially during 2024 and 2025. This patch keeps the same selected ICT setup, but adds a containment rule so the engine pauses when recent paper-trade quality deteriorates.

## Guard rules

| Guard | Value |
|---|---:|
| Risk per trade | 0.35% |
| Daily stop | -1.50R |
| Rolling quality window | 8 trades |
| Disable trigger | rolling 8-trade total <= -0.50R |
| Cooldown after disable | 3 months |
| Total drawdown stop | 8.80% |

## Baseline vs guarded paper result

| Metric | Baseline ICT | Guarded ICT |
|---|---:|---:|
| Starting balance | $5,000.00 | $5,000.00 |
| Ending balance | $4,773.00 | $5,058.56 |
| Net result | -$227.00 | $58.56 |
| Return | -4.54% | 1.17% |
| Trades | 112 | 65 |
| Wins | 42 | 29 |
| Losses | 70 | 36 |
| Win rate | 37.50% | 44.62% |
| Profit factor | 0.811 | 1.091 |
| Max drawdown | 8.47% | 2.73% |
| Total R | -13.016R | 3.485R |
| Improvement vs baseline net result | — | 125.80% |

## Guarded paper result by year

| Year | Trades | Total R | Average R | Net PnL |
|---:|---:|---:|---:|---:|
| 2023 | 29 | 4.266R | 0.147R | $73.92 |
| 2024 | 11 | 0.940R | 0.085R | $16.23 |
| 2025 | 17 | -0.901R | -0.053R | -$16.71 |
| 2026 | 8 | -0.820R | -0.103R | -$14.88 |

## Decision

This guard improves the historical paper OOS result by stopping weak sequences. Because the guard was designed after reviewing the prior OOS failure, this remains a research patch and is not production proof. The next step is fresh forward validation or a later unseen data period before enabling it.
