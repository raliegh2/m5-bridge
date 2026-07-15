# V14.3 Satellite Live Runner

## Verified research results

The two reported profit figures refer to different data windows:

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
`v14_3_signals.py`. That provider must expose:

```python
def build_live_signals(client):
    return [
        {
            "symbol": "GBPUSD",
            "engine": "ICT_V14_3_GBPUSD",
            "setup": "breakout_60_fade",
            "side": "BUY",
            "signal_time": "2026-07-15T12:00:00+00:00",
            "risk_percent": 0.20,
            "stop_pips": 25.0,
            "target_pips": 50.0,
            "metadata": {},
        }
    ]
```

Missing providers fail closed; the runner does not invent a replacement GBP ICT
strategy. V12 remains active for GBPUSD and GBPJPY.

## Pull the branch

```powershell
cd C:\Users\ralie

git clone --branch v14-3-satellite-live-runner --single-branch `
  https://github.com/raliegh2/m5-bridge.git `
  mt5-ai-bridge-satellite-live

cd C:\Users\ralie\mt5-ai-bridge-satellite-live
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
$env:PYTHONPATH = "."
```

Copy `.env.v14-3-satellite-live.example` values into `.env`, then enter only demo
account credentials.

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
python v14_3_satellite_live_runner.py --once
```

Continuous READ_ONLY scanning:

```powershell
python v14_3_satellite_live_runner.py --interval 60
```

Review:

- `v14_3_satellite_live_diagnostics.json`
- `v14_3_satellite_live_executions.jsonl`
- `state/v14_3_satellite_live_state.json`

## Supervised demo orders

After READ_ONLY proposals are correct, change only:

```dotenv
V14_3_EXECUTION_MODE=APPROVAL
```

Every order requires typing exactly `YES`. The executor refuses transmission on a
non-demo account. Keep AUTO disabled until broker-specific forward validation is
complete.
