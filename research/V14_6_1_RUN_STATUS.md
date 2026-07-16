# V14.6.1 workflow status

- Backtest outcome: success
- Validation outcome: success
- Commit: 293282b482b8ae41dc076fda988a969f911a39da

```text
{
  "window": {
    "start": "2012-03-05T00:00:00+00:00",
    "end": "2022-03-05T00:00:00+00:00"
  },
  "target_symbol_results": {
    "GBPUSD": {
      "validated": false,
      "base_risk_percent": null,
      "components": [],
      "component_count": 0,
      "evidence": null,
      "activity": {
        "trading_days": 48,
        "average_entries_per_active_day": 1.4583,
        "p95_entries_per_active_day": 2.0,
        "maximum_entries_per_day": 2,
        "days_with_multiple_entries": 22
      },
      "candidate_count": 1239
    },
    "GBPJPY": {
      "validated": false,
      "base_risk_percent": null,
      "components": [],
      "component_count": 0,
      "evidence": null,
      "activity": {
        "trading_days": 36,
        "average_entries_per_active_day": 2.9167,
        "p95_entries_per_active_day": 3.0,
        "maximum_entries_per_day": 3,
        "days_with_multiple_entries": 36
      },
      "candidate_count": 1029
    },
    "AUDUSD": {
      "validated": false,
      "base_risk_percent": null,
      "components": [],
      "component_count": 0,
      "evidence": null,
      "activity": {
        "trading_days": 104,
        "average_entries_per_active_day": 1.375,
        "p95_entries_per_active_day": 2.0,
        "maximum_entries_per_day": 2,
        "days_with_multiple_entries": 39
      },
      "candidate_count": 831
    }
  },
  "coverage": {
    "active_swing_symbols": [
      "AUDUSD",
      "EURUSD",
      "GBPJPY",
      "GBPUSD"
    ],
    "active_ict_symbols": [
      "EURUSD",
      "USDJPY"
    ],
    "all_three_failed_ict_symbols_fixed": false,
    "all_ten_sleeves_active": false
  },
  "baseline_v14_6": {
    "swing_scale": 0.9,
    "ict_scale": 1.5,
    "net_profit": 6282.968223828464,
    "ending_balance": 11282.968223828464,
    "return_percent": 125.6593644765693,
    "profit_factor": 1.9769245609508357,
    "max_closed_drawdown_percent": 8.505177779530758,
    "stress_drawdown_percent": 9.417577531124506,
    "closed_trades": 405,
    "skipped_trades": 42,
    "target_reached": false,
    "safe": true,
    "governor_interventions": 10
  },
  "best_safe_portfolio": {
    "swing_scale": 0.9,
    "ict_scale": 2.0,
    "net_profit": 6383.035643406654,
    "ending_balance": 11383.035643406654,
    "return_percent": 127.66071286813307,
    "profit_factor": 1.9356097038938038,
    "max_closed_drawdown_percent": 8.659772788672973,
    "stress_drawdown_percent": 9.469459106535764,
    "closed_trades": 405,
    "skipped_trades": 42,
    "safe": true,
    "target_reached": false,
    "governor_interventions": 13
  },
  "profit_improvement_vs_v14_6": 100.07,
  "target_reached": false,
  "target_gap": 27616.96,
  "output": "/home/runner/work/m5-bridge/m5-bridge/research/v14_6_1_intraday_ict_output"
}
```
