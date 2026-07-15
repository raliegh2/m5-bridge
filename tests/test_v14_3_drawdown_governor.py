from mt5_ai_bridge.v14_3_drawdown_governor import DrawdownGovernor, validate_governor


def test_multiplier_tiers() -> None:
    governor = DrawdownGovernor()
    assert governor.multiplier(0.0) == 1.0
    assert governor.multiplier(9.0) == 0.995
    assert governor.multiplier(9.5) == 0.95
    assert governor.multiplier(9.75) == 0.80
    assert governor.multiplier(9.99) == 0.0


def test_apply_never_increases_risk() -> None:
    governor = DrawdownGovernor()
    for drawdown in (0.0, 9.0, 9.5, 9.75, 9.99):
        assert governor.apply(0.20, drawdown) <= 0.20


def test_hard_stop_returns_zero() -> None:
    governor = DrawdownGovernor()
    assert governor.apply(0.20, governor.hard_stop_percent) == 0.0


def test_default_governor_is_valid() -> None:
    validate_governor(DrawdownGovernor())
