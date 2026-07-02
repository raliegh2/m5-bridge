# Historical economic-event coverage

## Included and tested

`official_fomc_events_2016_2026.csv` contains 86 scheduled or exceptional
Federal Reserve policy-statement events sourced from official Federal Reserve
calendars and normalized to UTC.

The frozen V4 ±10-minute event window did not remove any historical V4 entries,
so the FOMC-filtered metrics were identical to the baseline.

## Not yet complete

This package does not claim complete historical coverage for:

- Bank of England monetary-policy decisions
- U.S. Consumer Price Index releases
- U.S. Employment Situation / nonfarm payroll releases
- UK CPI
- UK employment
- UK GDP

Only source-verified timestamps should be appended. Do not generate dates from
recurring-calendar assumptions because official releases can move around
holidays and exceptional events.

## Version-control rule

Do not widen the event blackout inside frozen V4. A wider pre-event window is a
new V4.1 candidate and must repeat the purged walk-forward and Monte Carlo
process.
