# V14.3 Realistic Target Combined Rework

Status: **research-only event-based replay; ICT uses no same-trade result lookahead; V12 remains aggregate because no V12 accepted-trade ledger was found**

## Objective

Rework V14.3 as a two-engine research estimate that can target at least **$13,000** while allowing higher drawdown than the previous 9.5% cap.

## Two-engine structure

- **V12 Final** remains the master protected engine.
- **ICT V14.3** remains the satellite intraday engine.
- V12 is represented by its known aggregate net result of **$3,201.58** because the chronological V12 ledger is still missing.

## Realistic no-lookahead replay method

The ICT satellite was replayed with an event-based engine:

1. Trades are processed by entry time.
2. Risk is assigned at entry using only pre-entry information.
3. The trade result is not applied until exit time.
4. Open trades reserve risk capacity until exit.
5. The current trade result is never used to decide entry or sizing.

## Fixed V14.3 signal filters used

| Filter | Action |
|---|---|
| GBPJPY breakout-fade family | Excluded |
| GBPUSD sweep_reclaim_15 | Excluded |
| Tuesday entries | Excluded |
| 07:00 hour | Excluded |
| 13:00 hour | Excluded |
| Remaining stronger sweep/reclaim/liquidity-fade signals | Accepted if risk allows |

## Selected target profile

| Metric | Result |
|---|---:|
| Profile label | risk0.35_micro999_hard12_cap1.25 |
| ICT source signals after filters | 4,303 |
| ICT accepted trades | 3,016 |
| ICT skipped trades | 1,287 |
| Active ICT risk | 0.35% |
| Micro trigger | disabled |
| Hard DD trigger | 12% |
| Max ICT open-risk cap | 1.25% |
| ICT ending balance | $10,298.51 |
| ICT net result | $5,298.51 |
| ICT return | 105.97% |
| ICT profit factor | 1.134 |
| ICT max realized DD | 6.77% |
| Conservative combined proxy DD | 12.02% |
| Max ICT open risk used | 0.70% |

## Combined two-engine reference

| Metric | Result |
|---|---:|
| Starting balance | $5,000.00 |
| V12 aggregate net | $3,201.58 |
| ICT event-based net | $5,298.51 |
| Combined reference net | $8,500.09 |
| Combined reference ending balance | $13,500.09 |
| Combined reference return | 170.00% |

## Decision

The selected event-based V14.3 target profile reaches the **$13,000** combined target in a no-same-trade-result-lookahead ICT replay. The tradeoff is that conservative drawdown rises to about **12.02%**, so this version is not prop-safe under a 10% total-loss style limit.

## Limitations

1. This is research-only.
2. V12 is still an aggregate component because the exact V12 accepted-trade ledger is missing.
3. V14.3 filters were previously derived from historical behavior, so forward testing is still required.
4. The combined reference is not a true merged V12+ICT chronological replay until the V12 ledger is exported.
