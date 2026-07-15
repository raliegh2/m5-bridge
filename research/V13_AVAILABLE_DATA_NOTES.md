# V13 Available-Data Notes

The branch currently includes an available-data estimate, not a raw merged replay.

## Why the estimate is useful

It answers the first practical research question:

> If V11 intraday profit were additive to the V12 Final model, what would the
> profit increase look like?

Answer:

- V12 Final maximum-history: **$3,201.58**
- V11 intraday available-data estimate: **$1,046.89**
- V13 additive estimate: **$4,248.47**
- Increase: **+$1,046.89 / +32.70%**

## Why this is not enough

A true combined replay may produce less than the additive estimate because V11
intraday candidates may consume:

- open-risk capacity;
- max-position capacity;
- GBP correlation capacity;
- symbol-specific capacity;
- time windows where V12 already has stronger candidates.

The true test must therefore merge V12 and V11 candidates in timestamp order and
replay them through the same V13 risk governor.
