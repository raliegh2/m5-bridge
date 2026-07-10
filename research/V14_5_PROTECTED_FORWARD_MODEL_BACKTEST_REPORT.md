# V14.5 Protected Forward-Test Overlay Backtest

## Purpose

This run adds the profit-protection and symbol-quarantine controls requested after the live demo showed GBPJPY loss clustering. It does **not** change the entry model. It only changes whether a candidate is allowed and what risk is assigned.

## Data Used

- Candidate source: `selected_under10_target_trades.csv`
- Window: 2023-01-02 09:45:00 to 2026-07-03 16:01:00
- Symbols tested: GBPUSD, GBPJPY
- Candidate count: 4,303

Note: this local run validates the V14.3 ICT satellite candidate stream. To run the full true combined replay, run the committed replay locally after `research/v12_final_ledger_output/v12_final_trade_ledger.csv` is present.

## New Protection Rules

- Default risk reduced to 0.15%.
- Maximum risk capped at 0.25% during forward-test mode.
- GBPJPY starts at 0.05% risk unless it is positive for the day.
- After a loss, risk throttles to 0.10%.
- A symbol is blocked after 2 consecutive losses.
- A symbol is blocked for the day after 2 daily losses or a 0.50% day-start-equity symbol loss cap.
- The bot pauses globally for 2 hours after 2 consecutive global losses.
- The bot stops new entries for the day after 3 global losses.
- Profit lock activates once daily realized P/L reaches +0.35%.
- New entries stop if the bot gives back more than 35% of peak daily realized profit.
- New entries stop if equity falls 0.50% from the day high-watermark.
- Max 1 new trade per symbol per hour.
- Max 2 new trades total per hour.
- Max 2 simultaneous open trades.

## Backtest Result

| Metric | Result |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $6,592.51 |
| Net profit | $1,592.51 |
| Return | 31.85% |
| Closed trades | 3,079 |
| Skipped candidates | 1,224 |
| Profit factor | 1.151 |
| Max drawdown | 4.45% |

## By Symbol

| Symbol | Trades | Net P/L | Wins | Losses | Profit Factor |
|---|---:|---:|---:|---:|---:|
| GBPJPY | 850 | $313.31 | 427 | 421 | 1.210 |
| GBPUSD | 2,229 | $1,279.20 | 1,101 | 1,124 | 1.141 |

## Skip Reasons

| Reason | Count |
|---|---:|
| SYMBOL_BLOCK_REST_DAY | 431 |
| GLOBAL_DAILY_LOSS_STOP | 344 |
| GLOBAL_CONSECUTIVE_LOSS_PAUSE | 271 |
| TRADE_CLUSTER_SYMBOL_HOUR | 92 |
| PROFIT_GIVEBACK_STOP | 86 |

## Interpretation

This overlay does what was needed for the live problem: GBPJPY is still allowed to prove itself, but it cannot keep firing at full size after losses. The trade-off is lower net profit compared with the unrestricted V14.3 ICT satellite. That is expected because this version is designed for demo forward testing and profit retention, not maximum historical return.

This is not ready for prop/live use until it runs forward on demo and the logs confirm the blocks trigger correctly in real time.
