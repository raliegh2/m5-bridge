"""Detect and back-adjust unadjusted stock splits in equity/ETF history.

A split is not a price move. When a fund splits ten-for-one the quote drops
90% overnight while every holder's position value is unchanged, so a series
that records the raw quote contains a -90% "return" that nobody experienced.

This is the same family of defect as applying an FX contract size to gold: it
does not raise, it just silently rewrites the result. Two things break.

* **Mean reversion buys it.** A ten-for-one split is a z-score of -30. A rule
  that fades stretched moves sees the largest dip in the history of the
  instrument and buys, against a reversion that cannot happen because the move
  never happened. The rolling mean and standard deviation stay wrong for a
  further ``lookback`` bars.
* **Buy-and-hold is understated by the split factor.** ONEQ's raw series runs
  73.15 -> 105.32 over 22.9 years, +44%. Adjusted for its 2021 ten-for-one
  split the same history is roughly +1,340%. Benchmarking a strategy against
  the raw figure flatters it by an order of magnitude.

Telling a split from a crash
----------------------------
Both show a large overnight gap, so the gap alone cannot decide. The
discriminator is the bar's own range: on a split day the instrument is quiet
and only the scale changed (ONEQ's split day ranged 0.7%), whereas a genuine
collapse is violent intraday (TQQQ on 2020-03-12 gapped to 0.823 and then
ranged 35.8%). Requiring the gap to also land on a conventional split ratio
makes a false positive unlikely.

The one case this cannot decide is an instrument that doubles overnight and
then trades quietly: that is arithmetically identical to a one-for-two reverse
split, and it is read as one. For an index ETF a quiet overnight doubling does
not happen, so the ambiguity is theoretical here; on a single stock after a
takeover bid it would not be.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd

__all__ = ["SplitEvent", "COMMON_SPLIT_RATIOS", "detect_splits",
           "adjust_for_splits"]

# Conventional split ratios as new/old price factors. 0.5 is a two-for-one,
# 2.0 a one-for-two reverse split.
COMMON_SPLIT_RATIOS: Tuple[float, ...] = (
    0.1, 0.125, 1 / 7, 1 / 6, 0.2, 0.25, 1 / 3, 0.4, 0.5, 2 / 3, 0.75,
    1.5, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0,
)


@dataclass(frozen=True)
class SplitEvent:
    """One detected split. ``ratio`` is the new price divided by the old."""

    index: int
    time: int
    ratio: float
    nearest: float
    prev_close: float
    open: float
    intraday_range: float

    def __str__(self) -> str:
        stamp = pd.to_datetime(self.time, unit="s", utc=True).date()
        return (f"{stamp}: {self.prev_close:.2f} -> {self.open:.2f} "
                f"(x{self.ratio:.3f}, nearest {self.nearest:.3f})")


def _nearest_ratio(ratio: float, tolerance: float) -> float | None:
    for candidate in COMMON_SPLIT_RATIOS:
        if abs(ratio - candidate) <= tolerance * candidate:
            return candidate
    return None


def detect_splits(df: pd.DataFrame, *, min_gap: float = 0.20,
                  range_fraction: float = 0.5,
                  tolerance: float = 0.05) -> List[SplitEvent]:
    """Find bars whose opening gap is a split rather than a price move.

    A bar qualifies when all three hold:

    * the overnight gap moves the price by at least ``min_gap``;
    * the bar's own high-low range is under ``range_fraction`` of that gap,
      so the level changed while the instrument stayed quiet;
    * the gap lands within ``tolerance`` of a conventional split ratio.
    """
    for column in ("open", "high", "low", "close"):
        if column not in df.columns:
            raise ValueError(f"bars need an {column!r} column")

    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    times = (df["time"].to_numpy(dtype="int64") if "time" in df.columns
             else np.arange(len(df), dtype="int64"))

    events: List[SplitEvent] = []
    for i in range(1, len(df)):
        prev_close, open_ = closes[i - 1], opens[i]
        if not (np.isfinite(prev_close) and np.isfinite(open_)):
            continue
        if prev_close <= 0 or open_ <= 0:
            continue
        ratio = open_ / prev_close
        if abs(ratio - 1.0) < min_gap:
            continue
        low, high = lows[i], highs[i]
        if not (np.isfinite(low) and np.isfinite(high)) or low <= 0:
            continue
        bar_range = high / low - 1.0
        if bar_range >= range_fraction * abs(ratio - 1.0):
            continue                      # violent bar -> a real move
        nearest = _nearest_ratio(ratio, tolerance)
        if nearest is None:
            continue                      # not a conventional ratio
        events.append(SplitEvent(index=i, time=int(times[i]), ratio=ratio,
                                 nearest=nearest, prev_close=prev_close,
                                 open=open_, intraday_range=bar_range))
    return events


def adjust_for_splits(df: pd.DataFrame,
                      events: Sequence[SplitEvent] | None = None,
                      **kwargs) -> Tuple[pd.DataFrame, List[SplitEvent]]:
    """Back-adjust prices so the whole series is on the latest scale.

    Every bar before a split is multiplied by that split's ratio, which is the
    standard convention: recent prices are left untouched and history is
    restated, so today's quote still matches the broker's.

    Returns the adjusted frame and the events applied. Volume is left alone --
    a split multiplies share counts, and no strategy here trades on volume
    levels across a split boundary.
    """
    events = list(events) if events is not None else detect_splits(df, **kwargs)
    out = df.copy()
    if not events:
        return out, []

    factors = np.ones(len(out), dtype=float)
    for event in events:
        factors[:event.index] *= event.ratio
    for column in ("open", "high", "low", "close"):
        if column in out.columns:
            out[column] = out[column].to_numpy(dtype=float) * factors
    return out, events
