# GBPJPY Forward-Test Guard

This branch implements the GBPJPY corrections identified from the demo-account loss cluster.

## Enforced controls

- One open GBPJPY position maximum.
- Normal GBPJPY risk cap: **0.20%**.
- Post-loss GBPJPY risk cap: **0.10%**.
- Two losing GBPJPY trades in one UTC day stop GBPJPY for the rest of that day.
- A rolling net result of **-2R** across the recent six GBPJPY trades starts a four-hour cooldown.
- A winning trade reduces loss pressure gradually; it does not erase the loss state immediately.
- New GBPJPY entries are limited to **07:00–20:00 UTC**.
- Maximum permitted GBPJPY spread: **3.0 pips**.
- Minimum reward-to-risk ratio: **1.50:1**.
- Permitted stop range: **15–150 pips**.
- The guard state is stored atomically in a separate JSON file so restarting the bot does not clear a daily stop or cooldown.
- GBPUSD and all non-GBPJPY engines continue through the existing V12 executor without changed sizing or entry rules.

## Execution integration

`FinalV12Adapter` now routes orders through `GBPJPYStrictExecutor`, which applies the market-quality filters before the persistent loss guard and broker-native position sizing.

The adapter creates two state files by default:

- `v12_final_research_state.json`
- `v12_final_research_state_gbpjpy_guard.json`

When an SL or TP closes GBPJPY in MT5, the executor detects the missing stored ticket, reads its deal history, calculates the realized R multiple, and updates the guard before permitting another GBPJPY entry. If the close cannot be reconciled, GBPJPY fails closed instead of opening a new trade.

A manually managed close can also be recorded through:

```python
adapter.record_closed_trade(
    engine="GBPJPY_SWING_CORE",
    r_multiple=-1.0,
    symbol="GBPJPY",
)
```

The `symbol` argument is optional for engines already registered in the V12 engine map.

## Research replay

`research/v14_7_gbpjpy_guarded_combined_replay.py` applies the corrected GBPJPY position, risk, loss and UTC-session limits to the V14.6 combined replay while leaving other symbols unchanged.

## Validation

The `GBPJPY loss guard` GitHub Actions workflow compiles the modified modules and runs the focused guard, execution, adapter and replay tests. The focused workflow currently passes **31 tests**.

To run the same checks locally:

```powershell
python -m pytest tests\test_gbpjpy_guard.py tests\test_gbpjpy_guarded_execution.py tests\test_v13_v12_plus_v11_intraday_profile.py tests\test_v14_7_gbpjpy_guarded_replay.py -q
```

Keep the bot on a demo account. Confirm that the guard state file updates after each closed GBPJPY trade, and review at least 30 new GBPJPY signals before considering any less restrictive settings.
