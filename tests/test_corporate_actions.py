"""Split detection: a split must be corrected, a crash must be left alone."""

import numpy as np
import pandas as pd

from mt5_ai_bridge.corporate_actions import (adjust_for_splits, detect_splits)
from mt5_ai_bridge.data_audit import audit_bars


def _series(closes, opens=None, ranges=None, start=1_000_000_000):
    """Daily bars from a list of closes, with controllable open and range."""
    opens = list(opens if opens is not None else closes)
    ranges = list(ranges if ranges is not None else [0.01] * len(closes))
    rows = []
    for i, (close, open_, rng) in enumerate(zip(closes, opens, ranges)):
        top = max(close, open_) * (1.0 + rng / 2)
        bottom = min(close, open_) * (1.0 - rng / 2)
        rows.append({"time": start + i * 86_400, "open": open_, "high": top,
                     "low": bottom, "close": close})
    return pd.DataFrame(rows)


def test_two_for_one_split_is_detected():
    # 20 quiet bars near 100, then the quote halves overnight and carries on
    # quietly at 50. Nobody lost half their money.
    closes = [100.0] * 20 + [50.0] * 20
    opens = [100.0] * 20 + [50.0] + [50.0] * 19
    events = detect_splits(_series(closes, opens))

    assert len(events) == 1
    assert events[0].index == 20
    assert events[0].nearest == 0.5


def test_a_crash_is_not_a_split():
    # Gapping 30% down and then ranging 35% intraday is a real collapse: the
    # instrument is violent, not merely rescaled.
    closes = [100.0] * 20 + [70.0] * 5
    opens = [100.0] * 20 + [70.0] * 5
    ranges = [0.01] * 20 + [0.35] * 5

    assert detect_splits(_series(closes, opens, ranges)) == []


def test_an_unconventional_ratio_is_not_a_split():
    # A 37% overnight gap on a quiet bar is odd, but it is not a split ratio
    # any registrar issues, so it is reported by the outlier check instead.
    closes = [100.0] * 20 + [63.0] * 5
    events = detect_splits(_series(closes, closes))

    assert events == []


def test_adjustment_restates_history_and_leaves_recent_prices_alone():
    closes = [100.0] * 20 + [50.0] * 20
    frame = _series(closes, closes)

    adjusted, events = adjust_for_splits(frame)

    assert len(events) == 1
    # History is restated onto today's scale; today is untouched.
    assert adjusted["close"].iloc[0] == 50.0
    assert adjusted["close"].iloc[-1] == 50.0
    # And the split is no longer a return.
    returns = np.diff(np.log(adjusted["close"].to_numpy(dtype=float)))
    assert np.abs(returns).max() < 1e-9


def test_adjustment_is_a_no_op_on_a_clean_series():
    frame = _series([100.0, 101.0, 102.0, 101.5, 103.0])

    adjusted, events = adjust_for_splits(frame)

    assert events == []
    pd.testing.assert_frame_equal(adjusted, frame)


def test_hold_return_is_understated_until_the_split_is_adjusted():
    # The instrument doubled over ten sessions and then split two-for-one: a
    # holder is up 100%, while the raw series claims they are flat. The rise
    # is gradual on purpose -- an overnight doubling on a quiet bar is a
    # one-for-two reverse split as far as any detector can tell.
    ramp = [100.0 + 10.0 * i for i in range(11)]        # 100 -> 200
    closes = ramp + [100.0] * 10
    opens = ramp + [100.0] * 10
    frame = _series(closes, opens)
    raw_hold = frame["close"].iloc[-1] / frame["close"].iloc[0] - 1.0

    adjusted, _ = adjust_for_splits(frame)
    real_hold = adjusted["close"].iloc[-1] / adjusted["close"].iloc[0] - 1.0

    assert raw_hold == 0.0
    assert round(real_hold, 6) == 1.0


def test_audit_refuses_a_series_with_an_unadjusted_split():
    closes = [100.0] * 200 + [50.0] * 200
    frame = _series(closes, closes)

    result = audit_bars(frame, "TEST", "D1")

    assert not result.usable
    assert any(issue.code == "unadjusted_split" for issue in result.fatal)


def test_audit_accepts_the_same_series_once_adjusted():
    closes = [100.0] * 200 + [50.0] * 200
    adjusted, _ = adjust_for_splits(_series(closes, closes))

    result = audit_bars(adjusted, "TEST", "D1")

    assert not any(issue.code == "unadjusted_split" for issue in result.issues)
