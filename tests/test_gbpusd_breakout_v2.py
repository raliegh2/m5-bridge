from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from mt5_ai_bridge.enums import Mode, Signal
from mt5_ai_bridge.gbpusd_breakout_v2 import (
    BreakoutParams,
    BreakoutSetup,
    _approval_granted,
    _initial_risk_price,
    _setup_risk_multiplier,
    evaluate_setup,
)


def _rates(count: int, timeframe_seconds: int, rising: bool = True):
    base = 1.20
    direction = 1 if rising else -1
    rows = []
    for i in range(count):
        close = base + direction * i * 0.0005
        rows.append((
            1_700_000_000 + i * timeframe_seconds,
            close - direction * 0.0001,
            close + 0.0003,
            close - 0.0003,
            close,
            1_000 + i,
        ))
    dtype = [
        ("time", "i8"),
        ("open", "f8"),
        ("high", "f8"),
        ("low", "f8"),
        ("close", "f8"),
        ("tick_volume", "i8"),
    ]
    return np.array(rows, dtype=dtype)


class FakeClient:
    def __init__(self, h4, d1):
        self.h4 = h4
        self.d1 = d1
        self.calls = []

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        self.calls.append((symbol, timeframe, start, count))
        source = self.h4 if timeframe == "H4" else self.d1
        return source[-count:]


def test_initial_risk_is_preserved_from_two_r_target():
    position = SimpleNamespace(price_open=1.2500, tp=1.2700, sl=1.2400)
    assert _initial_risk_price(position) == 0.0100


def test_expanded_liquid_setup_receives_quality_risk():
    setup = BreakoutSetup(Signal.BUY, pd.Timestamp("2026-01-01", tz="UTC").to_pydatetime(),
                          0.001, 1.10, 1.20, "quality")
    assert _setup_risk_multiplier(setup) == 1.25


def test_valid_but_weaker_setup_is_throttled():
    setup = BreakoutSetup(Signal.BUY, pd.Timestamp("2026-01-01", tz="UTC").to_pydatetime(),
                          0.001, 0.90, 1.20, "standard")
    assert _setup_risk_multiplier(setup) == 0.75


def test_approval_mode_requires_exact_yes():
    setup = BreakoutSetup(Signal.BUY, pd.Timestamp("2026-01-01", tz="UTC").to_pydatetime(),
                          0.001, 1.10, 1.20, "quality")
    with patch("builtins.input", return_value="no"):
        assert _approval_granted(Mode.APPROVAL, setup, 0.625) is False
    with patch("builtins.input", return_value="YES"):
        assert _approval_granted(Mode.APPROVAL, setup, 0.625) is True


def test_auto_mode_does_not_prompt():
    setup = BreakoutSetup(Signal.BUY, pd.Timestamp("2026-01-01", tz="UTC").to_pydatetime(),
                          0.001, 1.10, 1.20, "quality")
    with patch("builtins.input") as prompt:
        assert _approval_granted(Mode.AUTO, setup, 0.625) is True
    prompt.assert_not_called()


def test_wrong_symbol_is_rejected_without_data_request():
    client = FakeClient(_rates(180, 14_400), _rates(120, 86_400))
    setup, frame = evaluate_setup(client, "EURUSD")
    assert setup is None
    assert frame is None
    assert client.calls == []


def test_completed_candles_are_requested_with_start_position_one():
    client = FakeClient(_rates(180, 14_400), _rates(120, 86_400))
    evaluate_setup(client, "GBPUSD", BreakoutParams(entry_end_hours_utc=tuple(range(24))))
    assert client.calls
    assert all(call[2] == 1 for call in client.calls)


def test_trending_breakout_can_generate_buy_setup():
    h4 = _rates(180, 14_400, rising=True)
    d1 = _rates(120, 86_400, rising=True)

    # Force the last completed H4 close above the prior 55-bar channel while
    # retaining adequate volume and trend history.
    h4["close"][-1] = h4["high"][-2] + 0.0020
    h4["high"][-1] = h4["close"][-1] + 0.0003
    h4["low"][-1] = h4["close"][-1] - 0.0003
    h4["open"][-1] = h4["close"][-1] - 0.0002
    h4["tick_volume"][-1] = 5_000

    client = FakeClient(h4, d1)
    setup, _ = evaluate_setup(
        client,
        "GBPUSD",
        BreakoutParams(adx_min=0, entry_end_hours_utc=tuple(range(24))),
    )
    assert setup is not None
    assert setup.side is Signal.BUY
