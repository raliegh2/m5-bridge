"""Is this price history fit to draw conclusions from?

Two of the three wrong answers in this investigation came from data, not from
strategy code: a synthetic pre-1999 EURUSD series that manufactured a
statistically significant trend, and instrument conventions applied to symbols
they did not fit. Both would have been caught by looking at the data first.

This module checks a bar series for the defects that silently corrupt a
backtest, and returns a verdict with the earliest timestamp from which the
series can be trusted.

What it looks for
-----------------
* **Structural corruption** -- non-positive prices, ``high < low``, an open or
  close outside the bar's range, duplicate or non-monotonic timestamps. Any of
  these makes fills meaningless.
* **Synthetic padding** -- zero-range bars, zero tick volume, long runs of an
  unchanged close. Fabricated history is usually smooth in a way real markets
  never are, and smoothness inflates measured trend persistence.
* **Coverage** -- how much of the expected trading calendar is actually present,
  and bars stamped on a weekend when the FX market is shut.
* **Outliers** -- returns beyond a robust sigma threshold, which are usually bad
  ticks rather than real moves, and which dominate an ATR-sized stop.
* **Regime breaks** -- a step change in realised volatility, which is the
  signature of two different sources spliced together.

The functions are pure; the CLI in ``tools/audit_history.py`` renders them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
import pandas as pd

__all__ = [
    "Issue",
    "AuditResult",
    "audit_bars",
    "SEVERITY_ORDER",
]

# Fatal  -> the series cannot be used at all.
# Major  -> usable only from a later date, or with the affected rows removed.
# Minor  -> worth knowing, does not by itself invalidate a backtest.
SEVERITY_ORDER = ("fatal", "major", "minor", "info")

# A weekday of FX at H4 is 6 bars; brokers differ slightly at the week's edges,
# so coverage below this fraction of the calendar is flagged, not assumed fatal.
_COVERAGE_WARN = 0.80
_STALE_RUN_WARN = 20          # consecutive identical closes
_OUTLIER_SIGMA = 12.0         # robust sigmas; real FX rarely exceeds this
_REGIME_RATIO = 3.0           # volatility step change between halves


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    message: str
    count: int = 0
    first_time: Optional[int] = None

    def __str__(self) -> str:
        when = ""
        if self.first_time:
            when = (" from "
                    + datetime.fromtimestamp(self.first_time, tz=timezone.utc)
                    .strftime("%Y-%m-%d"))
        n = f" ({self.count})" if self.count else ""
        return f"[{self.severity.upper():<5}] {self.code}: {self.message}{n}{when}"


@dataclass
class AuditResult:
    symbol: str
    timeframe: str
    bars: int
    start: Optional[int] = None
    end: Optional[int] = None
    issues: List[Issue] = field(default_factory=list)
    trusted_from: Optional[int] = None

    def of(self, severity: str) -> List[Issue]:
        return [i for i in self.issues if i.severity == severity]

    @property
    def fatal(self) -> List[Issue]:
        return self.of("fatal")

    @property
    def usable(self) -> bool:
        return not self.fatal and self.bars > 0

    @property
    def verdict(self) -> str:
        if not self.usable:
            return "UNUSABLE"
        if self.trusted_from and self.trusted_from > (self.start or 0):
            return "USABLE FROM"
        if self.of("major"):
            return "USABLE WITH CARE"
        return "USABLE"

    @property
    def trusted_from_date(self) -> str:
        if not self.trusted_from:
            return "-"
        return datetime.fromtimestamp(self.trusted_from, tz=timezone.utc) \
            .strftime("%Y-%m-%d")

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bars": self.bars,
            "verdict": self.verdict,
            "trusted_from": self.trusted_from,
            "trusted_from_date": self.trusted_from_date,
            "issues": [{"severity": i.severity, "code": i.code,
                        "message": i.message, "count": i.count,
                        "first_time": i.first_time} for i in self.issues],
        }


_PADDING_WINDOW = 500         # bars, for locating a synthetic prefix
_PADDING_DENSITY = 0.20       # fraction padded that marks a stretch as fake

# Dates before which a symbol did not exist, or did not float, and therefore
# cannot have had a market price. No statistic can detect this: a broker
# serving DEM prices relabelled as EURUSD looks like perfectly real data,
# because it IS real data -- of a different instrument.
#
# Getting this wrong is not academic. Pre-1999 "EURUSD" bars made the
# Lo-MacKinlay variance ratio report significant trend persistence
# (VR 1.256, p < 0.05) that vanished entirely once they were dropped.
INSTRUMENT_INCEPTION: dict[str, tuple[int, str]] = {
    # symbol: (epoch seconds, why)
    "EURUSD": (915_148_800, "the euro launched 1999-01-04; earlier bars are a "
                            "DEM/ECU proxy relabelled as EURUSD"),
    "EURJPY": (915_148_800, "the euro launched 1999-01-04"),
    "EURGBP": (915_148_800, "the euro launched 1999-01-04"),
    "EURCHF": (915_148_800, "the euro launched 1999-01-04"),
    "USDJPY": (94_694_400, "the yen floated after Bretton Woods collapsed in "
                           "1973; earlier quotes are the pegged 360 rate"),
    "AUDUSD": (440_078_400, "the Australian dollar floated 1983-12-12"),
}


def inception_of(symbol: str) -> Optional[tuple[int, str]]:
    """Earliest date a symbol could have had a real market price."""
    return INSTRUMENT_INCEPTION.get(str(symbol).upper())


def _robust_sigma(x: np.ndarray) -> float:
    """Median-absolute-deviation sigma, immune to the outliers being hunted."""
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    return float(mad * 1.4826) if mad > 0 else float(x.std(ddof=1) or 0.0)


def _padding_prefix_end(flags: np.ndarray, times: np.ndarray,
                        window: int = _PADDING_WINDOW,
                        density: float = _PADDING_DENSITY) -> Optional[int]:
    """Timestamp at which a densely-padded prefix stops.

    Real markets produce the occasional zero-range or zero-volume bar in thin
    hours, so a single occurrence proves nothing. What identifies fabricated
    history is a *sustained* stretch of them. This finds the last window in
    which padding was dense and returns its end -- everything after it is real.
    """
    n = flags.size
    if n < window or not flags.any():
        return None
    frac = pd.Series(flags.astype(float)).rolling(window).mean().to_numpy()
    dense = np.where(frac > density)[0]
    if dense.size == 0:
        return None
    end = int(min(dense[-1], n - 1))
    return int(times[end])


def audit_bars(df: pd.DataFrame, symbol: str = "?", timeframe: str = "?",
               bar_seconds: Optional[int] = None) -> AuditResult:
    """Audit one OHLC series and report where it becomes trustworthy."""
    result = AuditResult(symbol=symbol, timeframe=timeframe, bars=len(df))
    if df.empty:
        result.issues.append(Issue("fatal", "empty", "no bars"))
        return result

    required = {"time", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        result.issues.append(
            Issue("fatal", "columns", f"missing columns: {sorted(missing)}"))
        return result

    d = df.reset_index(drop=True)
    t = d["time"].to_numpy(dtype="int64")
    o = d["open"].to_numpy(dtype=float)
    h = d["high"].to_numpy(dtype=float)
    lo = d["low"].to_numpy(dtype=float)
    c = d["close"].to_numpy(dtype=float)
    result.start, result.end = int(t[0]), int(t[-1])
    trusted = int(t[0])

    # --- structural integrity -----------------------------------------
    bad_price = ~(np.isfinite(o) & np.isfinite(h) & np.isfinite(lo)
                  & np.isfinite(c)) | (o <= 0) | (h <= 0) | (lo <= 0) | (c <= 0)
    if bad_price.any():
        result.issues.append(Issue(
            "fatal", "bad_price", "non-positive or non-finite prices",
            int(bad_price.sum()), int(t[np.argmax(bad_price)])))

    inverted = h < lo
    if inverted.any():
        result.issues.append(Issue(
            "fatal", "high_below_low", "high below low",
            int(inverted.sum()), int(t[np.argmax(inverted)])))

    outside = (o > h) | (o < lo) | (c > h) | (c < lo)
    if outside.any():
        result.issues.append(Issue(
            "fatal", "oc_outside_range", "open/close outside the bar range",
            int(outside.sum()), int(t[np.argmax(outside)])))

    if len(np.unique(t)) != len(t):
        dupes = len(t) - len(np.unique(t))
        result.issues.append(Issue(
            "major", "duplicate_time", "duplicate timestamps", dupes))

    if np.any(np.diff(t) < 0):
        result.issues.append(Issue(
            "fatal", "unsorted", "timestamps are not monotonically increasing",
            int((np.diff(t) < 0).sum())))

    # --- synthetic-padding signatures -----------------------------------
    zero_range = h == lo
    if zero_range.any():
        frac = zero_range.mean()
        sev = "major" if frac > 0.01 else "minor"
        result.issues.append(Issue(
            sev, "zero_range", f"bars with high == low ({frac:.2%})",
            int(zero_range.sum()), int(t[np.argmax(zero_range)])))

    if "tick_volume" in d.columns:
        tv = d["tick_volume"].to_numpy(dtype=float)
        zero_vol = tv <= 0
        if zero_vol.any():
            frac = zero_vol.mean()
            result.issues.append(Issue(
                "major" if frac > 0.01 else "minor", "zero_volume",
                f"bars with zero tick volume ({frac:.2%})",
                int(zero_vol.sum()), int(t[np.argmax(zero_vol)])))

    # Runs of an unchanged close: a real market almost never does this.
    same = np.concatenate(([False], c[1:] == c[:-1]))
    if same.any():
        runs, run = [], 0
        for flag in same:
            run = run + 1 if flag else 0
            runs.append(run)
        longest = int(max(runs))
        if longest >= _STALE_RUN_WARN:
            idx = int(np.argmax(runs))
            result.issues.append(Issue(
                "major", "stale_run",
                f"longest run of an unchanged close is {longest} bars",
                longest, int(t[max(0, idx - longest)])))

    # --- calendar coverage -----------------------------------------------
    if bar_seconds is None and len(t) > 10:
        bar_seconds = int(np.median(np.diff(t)))
    if bar_seconds and bar_seconds > 0:
        dts = pd.to_datetime(t, unit="s", utc=True)
        weekend = (dts.dayofweek == 5) | (dts.dayofweek == 6)
        # Sunday evening bars are normal at the week's open; Saturday is not.
        saturday = dts.dayofweek == 5
        if saturday.any():
            result.issues.append(Issue(
                "minor", "weekend_bars",
                f"bars stamped on a Saturday ({saturday.mean():.2%})",
                int(saturday.sum())))

        span = int(t[-1] - t[0])
        weekday_fraction = 5.0 / 7.0
        expected = (span / bar_seconds) * weekday_fraction
        if expected > 0:
            coverage = len(t) / expected
            if coverage < _COVERAGE_WARN:
                result.issues.append(Issue(
                    "minor", "sparse_coverage",
                    f"only {coverage:.0%} of the expected weekday bars present"))

    # --- return outliers ---------------------------------------------------
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.diff(np.log(np.where(c > 0, c, np.nan)))
    rets = rets[np.isfinite(rets)]
    if rets.size > 100:
        sigma = _robust_sigma(rets)
        if sigma > 0:
            extreme = np.abs(rets) > _OUTLIER_SIGMA * sigma
            if extreme.any():
                result.issues.append(Issue(
                    "minor", "return_outliers",
                    f"returns beyond {_OUTLIER_SIGMA:.0f} robust sigma "
                    f"(max {np.abs(rets).max() / sigma:.0f} sigma)",
                    int(extreme.sum())))

        # --- volatility regime break -> spliced sources -------------------
        half = rets.size // 2
        early, late = rets[:half], rets[half:]
        if early.size > 50 and late.size > 50:
            v_early = _robust_sigma(early)
            v_late = _robust_sigma(late)
            if v_early > 0 and v_late > 0:
                ratio = max(v_early, v_late) / min(v_early, v_late)
                if ratio > _REGIME_RATIO:
                    quieter_first = v_early < v_late
                    mid = int(t[half])
                    result.issues.append(Issue(
                        "major", "volatility_regime_break",
                        f"realised volatility differs {ratio:.1f}x between "
                        f"halves ({'early half is quieter' if quieter_first else 'late half is quieter'})"
                        " -- likely two sources spliced together",
                        0, mid))
                    if quieter_first:
                        trusted = max(trusted, mid)

    # A synthetic prefix shows up as a sustained stretch of zero-range or
    # zero-volume bars. Trust the series only from where that stretch ends.
    padding = zero_range.copy()
    if "tick_volume" in d.columns:
        padding |= (d["tick_volume"].to_numpy(dtype=float) <= 0)
    prefix_end = _padding_prefix_end(padding, t)
    if prefix_end is not None and prefix_end > trusted:
        trusted = prefix_end
        result.issues.append(Issue(
            "major", "synthetic_prefix",
            "sustained padded bars before this point -- fabricated history",
            0, prefix_end))

    # Domain facts override statistics: a proxy series is real price action of
    # the wrong instrument, so nothing in the data itself gives it away.
    inception = inception_of(symbol)
    if inception:
        start_ts, why = inception
        if int(t[0]) < start_ts:
            before = int((t < start_ts).sum())
            result.issues.append(Issue(
                "major", "pre_inception",
                f"bars before this symbol existed -- {why}",
                before, start_ts))
            trusted = max(trusted, start_ts)

    result.trusted_from = trusted
    return result
