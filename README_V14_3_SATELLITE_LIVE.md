# V14.3 Satellite Live Runner

## Verified research results

The two reported profit figures refer to different historical data windows:

- **Full repository history:** $48,377.58 net profit.
- **Exact synchronized ten-year window:** $34,690.84 net profit.

These are historical research replays, not expected or guaranteed live returns.

## Live integration coverage

Built in:

- V12 engines for GBPUSD, EURUSD, GBPJPY, AUDUSD and USDJPY.
- EURUSD ICT liquidity mode.
- AUDUSD ICT Asia/London mode.
- USDJPY ICT session-sweep mode.
- Enhanced satellite filters and risk tiers.
- Broker-native sizing, symbol suffix resolution, `order_check`, persistent state,
  duplicate prevention, spread limits, daily loss controls and drawdown protection.

The exact historical GBPUSD/GBPJPY V14.3 ICT generator was not present in the
GitHub research branch; its trades were supplied as a historical ledger. The live
runner therefore loads it only through an optional local provider named
`v14_3_signals.py`. Missing providers fail closed rather than substituting an
unverified GBP ICT strategy. V12 remains active for GBPUSD and GBPJPY.

## Normal Windows startup

The normal bot entrypoint preserves the earlier clean terminal presentation:

- one compact updating account/status line;
- concise signal and order announcements;
- no full diagnostics JSON on every scan;
- automatic dashboard launch;
- one-second target interval configured directly in the Python entrypoint.

Double-click:

```text
Start-V14-3-Satellite.bat
```

Or run:

```powershell
python v14_3_satellite_preflight.py
python v14_3_satellite_bot.py
```

The dashboard opens automatically at:

```text
http://127.0.0.1:8800/
```

The interval is set in `v14_3_satellite_bot.py`:

```python
SCAN_INTERVAL_SECONDS = 1.0
```

The strategies still evaluate completed H1/H4/D1 candles. The one-second loop
refreshes account status, positions, dashboard state, risk controls and detection
of newly completed signals. A full five-symbol calculation may take longer than
one second; the scheduler does not build an increasing timing backlog.

## Detailed diagnostics runner

Use the lower-level runner only when full JSON diagnostics are required:

```powershell
python v14_3_satellite_live_runner.py --interval 1
```

## Safe first run

Keep:

```dotenv
V14_3_EXECUTION_MODE=READ_ONLY
V14_3_FORWARD_GATE_PASSED=false
V14_3_ALLOW_DEMO_AUTO=false
```

Run:

```powershell
python v14_3_satellite_preflight.py
python v14_3_satellite_bot.py
```

Review:

- `http://127.0.0.1:8800/`
- `v14_3_satellite_live_diagnostics.json`
- `v14_3_satellite_live_executions.jsonl`
- `state/v14_3_satellite_live_state.json`

## Execution boundary

The executor remains fail-closed:

- READ_ONLY produces proposals only.
- APPROVAL requires explicit approval for each order.
- AUTO requires a confirmed MT5 demo account and both explicit validation gates.
- The current implementation refuses broker transmission on a non-demo account.
