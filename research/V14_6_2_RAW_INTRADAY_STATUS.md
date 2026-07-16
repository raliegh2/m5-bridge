# V14.6.2 raw-candle workflow status

- Backtest outcome: success
- Validation outcome: failure
- Commit: 1a6202a2524ef0b5f91c7b4f69430e033380af7a

```text
    "GBPJPY": {
      "validated": true,
      "selected_engine": "GBPJPY_ICT_INTRADAY_GJ_LONDON_PULLBACK",
      "base_risk_percent": 0.2,
      "evidence": {
        "segments": {
          "development": {
            "trades": 14,
            "net_r": 2.16889,
            "expectancy_r": 0.154921,
            "profit_factor": 1.569444
          },
          "confirmation": {
            "trades": 15,
            "net_r": 0.941948,
            "expectancy_r": 0.062797,
            "profit_factor": 1.165387
          },
          "holdout": {
            "trades": 6,
            "net_r": 0.458519,
            "expectancy_r": 0.07642,
            "profit_factor": 1.334351
          }
        },
        "total": {
          "trades": 35,
          "net_r": 3.569357,
          "expectancy_r": 0.101982,
          "profit_factor": 1.328199
        },
        "score": 15.675992
      },
      "activity": {
        "trading_days": 35,
        "average_entries_per_active_day": 1.0,
        "median_entries_per_active_day": 1.0,
        "p95_entries_per_active_day": 1.0,
        "maximum_entries_per_day": 1,
        "days_with_multiple_entries": 0
      },
      "candidate_count": 6376,
      "intraday_candidate_count": 5347
    },
    "AUDUSD": {
      "validated": true,
      "selected_engine": "AUDUSD_ICT_INTRADAY_AU_ASIA_LONDON_PULLBACK",
      "base_risk_percent": 0.2,
      "evidence": {
        "segments": {
          "development": {
            "trades": 26,
            "net_r": 1.822093,
            "expectancy_r": 0.07008,
            "profit_factor": 1.224128
          },
          "confirmation": {
            "trades": 7,
            "net_r": 1.384867,
            "expectancy_r": 0.197838,
            "profit_factor": 1.57434
          },
          "holdout": {
            "trades": 16,
            "net_r": 8.283522,
            "expectancy_r": 0.51772,
            "profit_factor": 7.084022
          }
        },
        "total": {
          "trades": 49,
          "net_r": 11.490482,
          "expectancy_r": 0.2345,
          "profit_factor": 1.965387
        },
        "score": 24.619122
      },
      "activity": {
        "trading_days": 49,
        "average_entries_per_active_day": 1.0,
        "median_entries_per_active_day": 1.0,
        "p95_entries_per_active_day": 1.0,
        "maximum_entries_per_day": 1,
        "days_with_multiple_entries": 0
      },
      "candidate_count": 10575,
      "intraday_candidate_count": 9744
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
      "AUDUSD",
      "EURUSD",
      "GBPJPY",
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
    "swing_scale": 1.0,
    "ict_scale": 2.0,
    "net_profit": 8069.701021206678,
    "ending_balance": 13069.701021206678,
    "return_percent": 161.39402042413357,
    "profit_factor": 1.9429036150886525,
    "max_closed_drawdown_percent": 8.796869546776602,
    "stress_drawdown_percent": 9.979199079604385,
    "closed_trades": 489,
    "skipped_trades": 42,
    "safe": true,
    "target_reached": false,
    "governor_interventions": 25
  },
  "profit_improvement_vs_v14_6": 1786.73,
  "target_reached": false,
  "target_gap": 25930.3,
  "output": "/home/runner/work/m5-bridge/m5-bridge/research/v14_6_2_raw_intraday_output"
}
```
