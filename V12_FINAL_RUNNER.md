# Final V12 Automatic DEMO/LIVE Runner

The runner evaluates the frozen five-symbol strategy using completed H1, H4,
and D1 candles. Qualified signals pass through the unchanged V12 risk gates and
then through the selected-account MT5 executor.

## Files

- `v12_final_runner.py` — continuous scanner and execution router
- `v12_final_preflight.py` — selected-account/symbol/reconciliation validation
- `mt5_ai_bridge/v12_final_execution.py` — order, recovery, close, and modify layer
- `research/v12_final_runner_parity_backtest.py` — historical parity check
- `v12_final_executions.jsonl` — generated execution-attempt log
- `v12_final_runner_state.json` — scanner signal deduplication state
- `v12_final_research_state.json` — tickets, risk, cooldown, and recovery state

## Start safely

Ensure MT5 is open and logged into the intended account, then use the preferred
combined bot/dashboard launcher:

```powershell
python v12_final_bot.py
```

Choose `DEMO` or `LIVE` at startup. During runtime, use the dashboard account
mode buttons or type `MODE DEMO` / `MODE LIVE` in the terminal. The selected
mode must match the actual connected account or order execution is blocked.

If preflight passes, continuous scanning can be started with:

```powershell
python v12_final_runner.py --interval 60
```

The scanner recognizes only newly completed frozen-strategy signals. A signal
is not an order unless every portfolio, spread, drawdown, exposure, duplicate,
and account check passes.

## Historical parity

```powershell
python research/v12_final_runner_parity_backtest.py
```

This checks signal/portfolio parity; it cannot reproduce future broker fills,
slippage, outages, or guarantee future profit.
