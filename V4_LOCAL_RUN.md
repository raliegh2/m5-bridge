# Run GBPUSD V4 locally

```powershell
cd C:\Users\ralie\mt5-ai-bridge

git fetch origin
git checkout gbpusd-v4-exceptional
git pull origin gbpusd-v4-exceptional

python -m pip install -r requirements.txt
python tools\apply_gbpusd_v4.py
python -m pytest -q
```

Copy the strategy values from `.env.v4.example` into your existing `.env` while
preserving the private MT5 login, password, and server values.

Start in read-only mode:

```powershell
python bridge.py
```

After verifying completed-H4 signals, dashboard output, state persistence,
position sizing, and broker-visible stops, change `MODE=READ_ONLY` to
`MODE=APPROVAL` in `.env` and restart the bot.

Do not use unattended live execution until the frozen 20/30-trade forward-test
gates are satisfied.
