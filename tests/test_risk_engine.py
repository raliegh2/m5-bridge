from mt5_ai_bridge.risk_engine import RiskLimits, check_risk
from tests.fakes import make_account, make_position


def test_passes_when_within_limits():
    r = check_risk(make_account(balance=10000, equity=9900), [])
    assert r.ok
    assert r.message == "Risk check passed."


def test_blocks_on_daily_loss():
    r = check_risk(make_account(balance=10000, equity=9700), [])  # -300
    assert not r.ok
    assert "Daily" in r.message


def test_blocks_on_total_loss_first():
    r = check_risk(make_account(balance=10000, equity=9400), [])  # -600
    assert not r.ok
    assert "Total" in r.message


def test_blocks_on_max_positions():
    positions = [make_position(ticket=i) for i in range(3)]
    r = check_risk(make_account(balance=10000, equity=10000), positions)
    assert not r.ok
    assert "positions" in r.message.lower()


def test_custom_limits_are_respected():
    limits = RiskLimits(daily_max_loss=50, total_max_loss=100, max_open_positions=1)
    r = check_risk(make_account(balance=10000, equity=9940), [], limits)  # -60
    assert not r.ok


def test_result_unpacks_to_ok_message():
    ok, message = check_risk(make_account(), [])
    assert ok is True
    assert isinstance(message, str)
