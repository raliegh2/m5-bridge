# V14.7.2 workflow status

- Backtest outcome: failure
- Validation outcome: failure
- Commit: 1296f86ea89b0be0ead1d0422c52cbf88f9251de

```text
Traceback (most recent call last):
  File "/home/runner/work/m5-bridge/m5-bridge/research/v14_7_2_frozen_all_ten.py", line 374, in <module>
    main()
  File "/home/runner/work/m5-bridge/m5-bridge/research/v14_7_2_frozen_all_ten.py", line 240, in main
    swing_frames, ict_frames, selections, previous = current_selections(source, start, latest)
                                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/runner/work/m5-bridge/m5-bridge/research/v14_7_2_frozen_all_ten.py", line 155, in current_selections
    frame = materialize(
            ^^^^^^^^^^^^
  File "/home/runner/work/m5-bridge/m5-bridge/research/v14_7_2_frozen_all_ten.py", line 105, in materialize
    raise RuntimeError(f"No trades for {symbol} {mode} {spec}")
RuntimeError: No trades for GBPJPY ICT FilterSpec(engine='GBPJPY_ICT_INTRADAY_GJ_LONDON_PULLBACK', side='SELL', hour=14, session=None, excluded_weekday=None)
```
