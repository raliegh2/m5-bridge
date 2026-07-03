# Final V12 Runner

The runner continuously reads closed H1, H4, and D1 candles from the connected
MetaTrader 5 terminal and evaluates the final five-symbol V12 strategy.

## Files

- `v12_final_runner.py` — continuous scanner
- `v12_final_preflight.py` — connection and symbol validation
- `research/v12_final_runner_parity_backtest.py` — historical parity check
- `v12_final_proposals.jsonl` — generated proposal log
- `v12_final_runner_state.json` — signal deduplication state
- `v12_final_research_state.json` — portfolio and adaptive-guard state

## Verification

GitHub Actions run `28635397898` passed compilation, focused tests, candidate
parity, and the final portfolio replay tolerance check.

## One scan

```powershell
python v12_final_runner.py --once
```

## Continuous scanning

```powershell
python v12_final_runner.py --interval 60
```

The runner checks for newly completed candles every 60 seconds. It evaluates:

- GBPUSD V10 primary and secondary precision breakouts
- GBPUSD V5 pullback add-on
- GBPUSD H4 retest
- EURUSD H4 core and H1 retest
- GBPJPY H4 core
- AUDUSD D1/H4 pullback
- USDJPY D1/H4 40-bar breakout

The disabled `GBPUSD_SWING_CORE` and `GBPJPY_SWING_RETEST` engines are not built.

## Local historical parity check

```powershell
python research/v12_final_runner_parity_backtest.py
```

Output is written to:

```text
research\v12_final_runner_parity_output\
```

## Important behavior

The runner uses MT5 market and account data to calculate exact proposals and
writes them to `v12_final_proposals.jsonl`. The current supervised research
adapter does not submit orders to the broker.
