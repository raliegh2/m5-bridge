# V12 + V14.3 production improvements

This branch keeps the V12 master engine and the locked V14.3 ICT candidate stream, then adds a separate production-policy replay with:

- per-symbol diagnostics for GBPUSD, EURUSD, GBPJPY, AUDUSD and USDJPY;
- explicit engine coverage checks;
- GBPJPY one-position protection;
- GBPJPY 0.20% normal and 0.10% post-loss ICT risk;
- two-loss daily GBPJPY stop, 0.50% daily loss cap and four-hour rolling-loss cooldown;
- GBPJPY 07:00-20:00 UTC entry session and one entry per rolling hour;
- GBPUSD-specific post-loss sizing and loss-cluster controls;
- duplicate candidate removal, shared open-risk limits and rejection-code reporting;
- baseline versus improved full-history and exact ten-year comparisons.

The backtest does not force trades on quiet symbols. Every symbol is reported even when no valid candidate is available. Historical R-multiple data has no broker bid/ask series, so spread is reported as unavailable rather than fabricated.
