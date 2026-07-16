# V14.6.1 workflow status

- Backtest outcome: failure
- Validation outcome: failure
- Commit: 00f28dfddfe66a20fd3ed22b3ba9fca72ca30030

```text
Traceback (most recent call last):
  File "/home/runner/work/m5-bridge/m5-bridge/research/v14_6_1_intraday_ict_trend_backtest.py", line 428, in <module>
    main()
  File "/home/runner/work/m5-bridge/m5-bridge/research/v14_6_1_intraday_ict_trend_backtest.py", line 297, in main
    swing_all = base.build_continuous_swing_candidates()
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/runner/work/m5-bridge/m5-bridge/research/v14_6_five_symbol_dual_engine_target.py", line 280, in build_continuous_swing_candidates
    symbol: live.prepare_v12_frames(
            ^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/runner/work/m5-bridge/m5-bridge/mt5_ai_bridge/v14_3_live_signals.py", line 70, in prepare_v12_frames
    h1 = _frame(client.copy_rates_from_pos(broker_symbol, "H1", 1, h1_count))
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/runner/work/m5-bridge/m5-bridge/research/v14_6_swing_regeneration.py", line 81, in copy_rates_from_pos
    frame = self._load(symbol, timeframe)
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/runner/work/m5-bridge/m5-bridge/research/v14_6_swing_regeneration.py", line 69, in _load
    frame = pd.read_csv(self.data_dir / f"{symbol}_{timeframe}.csv")
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/io/parsers/readers.py", line 873, in read_csv
    return _read(filepath_or_buffer, kwds)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/io/parsers/readers.py", line 300, in _read
    parser = TextFileReader(filepath_or_buffer, **kwds)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/io/parsers/readers.py", line 1645, in __init__
    self._engine = self._make_engine(f, self.engine)
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/io/parsers/readers.py", line 1904, in _make_engine
    self.handles = get_handle(
                   ^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/io/common.py", line 930, in get_handle
    handle = open(
             ^^^^^
FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/m5-bridge/m5-bridge/research/data_v14_6/GBPUSD_H1.csv'
```
