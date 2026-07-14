# GBPJPY Forward-Test Guard

This branch implements the GBPJPY corrections identified from the demo-account loss cluster.

## Enforced controls

- One open GBPJPY position maximum.
- Normal GBPJPY risk cap: **0.20%**.
- Post-loss GBPJPY risk cap: **0.10%**.
- Two losing GBPJPY trades in one UTC day stop GBPJPY for the rest of that day.
- A rolling net result of **-2R** across the recent six GBPJPY trades starts a four-hour cooldown.
- A winning trade reduces loss pressure gradually; it does not erase the loss state immediately.
- The guard state is stored atomically in a separate JSON file so restarting the bot does not clear a daily stop or cooldown.
- GBPUSD and all non-GBPJPY engines continue through the existing V12 executor without changed sizing or entry rules.

## Execution integration

`FinalV12Adapter` now routes orders through `GBPJPYGuardedExecutor`.

The adapter creates two state files by default:

- `v12_final_research_state.json`
- `v12_final_research_state_gbpjpy_guard.json`

A closed GBPJPY trade must be recorded through:

```python
adapter.record_closed_trade(
    engine="GBPJPY_SWING_CORE",
    r_multiple=-1.0,
    symbol="GBPJPY",
)
```

The `symbol` argument is optional for engines already registered in the V12 engine map.

## Validation

Run:

```powershell
python -m pytest tests\test_gbpjpy_guard.py tests\test_gbpjpy_guarded_execution.py tests\test_v13_v12_plus_v11_intraday_profile.py -q
```

Keep the bot on a demo account and review at least 30 new GBPJPY signals before considering any less restrictive settings.
