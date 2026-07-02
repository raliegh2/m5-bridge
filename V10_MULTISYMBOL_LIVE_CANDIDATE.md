# V10 Multi-Symbol Live Candidate

This branch coordinates GBPUSD, EURUSD and GBPJPY through one runtime controller.

Implemented:

- completed-candle feature generation for EURUSD/GBPJPY V7
- broker suffix/prefix symbol resolution
- unique magic numbers
- normalized lot sizing from stop risk
- spread and broker stop-distance checks
- `order_check` before `order_send`
- shared 0.75% account open-risk cap
- aligned/mixed GBP currency caps
- duplicate-signal suppression
- atomic state persistence
- break-even and time/force-flat management
- READ_ONLY, APPROVAL and AUTO modes
- synchronized portfolio replay and cost stress

The controller defaults to READ_ONLY. It remains a live candidate until shadow
signals and demo fills are reconciled against the frozen research rules.
