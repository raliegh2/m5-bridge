# V13 Local Validation Commands

```powershell
cd C:\Users\ralie\mt5-ai-bridge
git fetch origin
git switch v13-v12-final-plus-v11-intraday
python -m pytest tests\test_v13_v12_plus_v11_intraday_profile.py -q
python -m py_compile mt5_ai_bridge\v13_v12_plus_v11_intraday_profile.py research\v13_v12_final_plus_v11_intraday_available_backtest.py
python research\v13_v12_final_plus_v11_intraday_available_backtest.py
```

The final command rewrites:

```text
research/v13_v12_plus_v11_intraday_backtest_results.json
```
