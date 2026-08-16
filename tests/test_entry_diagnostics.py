"""Every blocked-entry reason, not just the first one."""

import pytest

from mt5_ai_bridge.entry_diagnostics import (GATE_NAMES, EntryDiagnosis,
                                             GateStatus, RejectionLedger,
                                             evaluate_entry_gates)
from mt5_ai_bridge.session_guard import RiskGuardedClient, SessionGuardConfig

from .test_session_guard import DAY, FakeGuardClient, _settings

NOW = DAY


def _cfg(**kw):
    base = dict(enable_trade_limit=True, max_trades_per_day=8,
                max_trades_per_symbol_per_day=4,
                minimum_minutes_between_entries=15,
                minimum_lot=0.01, maximum_lot=0.40)
    base.update(kw)
    return SessionGuardConfig(**base)


def _clear_state(**kw):
    state = {"daily_lock": False, "cooldown_until": 0.0, "trades_today": 0,
             "trades_by_symbol": {}, "last_entry_time": 0.0}
    state.update(kw)
    return state


def _diagnose(state=None, cfg=None, symbol="GBPUSD", volume=0.10, now=NOW):
    return evaluate_entry_gates(state or _clear_state(), cfg or _cfg(),
                                symbol, volume, now)


# --- happy path ------------------------------------------------------------


def test_all_gates_open_allows_entry():
    d = _diagnose()
    assert d.allowed
    assert d.blocking == ()
    assert d.first_reason == "session risk checks passed"
    assert "ALLOWED" in d.summary()


def test_every_named_gate_is_reported():
    d = _diagnose()
    assert tuple(g.name for g in d.gates) == GATE_NAMES


# --- each gate in isolation ------------------------------------------------


def test_daily_lock_blocks_and_keeps_its_reason():
    d = _diagnose(_clear_state(daily_lock=True, lock_reason="daily loss hit"))
    assert not d.allowed
    assert d.first_reason == "daily loss hit"
    assert [g.name for g in d.blocking] == ["daily_lock"]


def test_daily_lock_without_reason_falls_back():
    d = _diagnose(_clear_state(daily_lock=True))
    assert d.first_reason == "daily trading lock active"


def test_loss_cooldown_blocks_until_it_expires():
    active = _diagnose(_clear_state(cooldown_until=NOW + 600))
    assert not active.allowed
    assert "loss cooldown active" in active.first_reason
    expired = _diagnose(_clear_state(cooldown_until=NOW - 1))
    assert expired.allowed


def test_non_positive_volume_blocks():
    d = _diagnose(volume=0.0)
    assert not d.allowed
    assert "requested volume is not positive" in d.first_reason


def test_volume_below_minimum_lot_blocks():
    d = _diagnose(volume=0.005)
    assert not d.allowed
    assert "below SESSION_MINIMUM_LOT" in d.first_reason


def test_volume_above_maximum_lot_blocks():
    d = _diagnose(volume=1.5)
    assert not d.allowed
    assert "exceeds SESSION_MAXIMUM_LOT" in d.first_reason
    assert [g.name for g in d.blocking] == ["maximum_lot"]


def test_daily_trade_limit_blocks():
    d = _diagnose(_clear_state(trades_today=8))
    assert not d.allowed
    assert d.first_reason == "daily trade limit reached (8/8)"


def test_symbol_trade_limit_blocks():
    d = _diagnose(_clear_state(trades_by_symbol={"GBPUSD": 4}))
    assert not d.allowed
    assert d.first_reason == "GBPUSD daily trade limit reached (4/4)"


def test_symbol_limit_is_case_insensitive():
    d = _diagnose(_clear_state(trades_by_symbol={"GBPUSD": 4}), symbol="gbpusd")
    assert not d.allowed
    assert "daily trade limit reached (4/4)" in d.first_reason


def test_entry_interval_blocks_then_opens():
    blocked = _diagnose(_clear_state(last_entry_time=NOW - 60))
    assert not blocked.allowed
    assert "minimum entry interval active" in blocked.first_reason
    open_again = _diagnose(_clear_state(last_entry_time=NOW - 16 * 60))
    assert open_again.allowed


def test_trade_limit_gates_are_open_when_the_limiter_is_disabled():
    cfg = _cfg(enable_trade_limit=False)
    d = _diagnose(_clear_state(trades_today=999, last_entry_time=NOW),
                  cfg=cfg)
    assert d.allowed
    for name in ("daily_trade_limit", "symbol_trade_limit", "entry_interval"):
        gate = next(g for g in d.gates if g.name == name)
        assert gate.detail["disabled"] is True


# --- the point of the module ----------------------------------------------


def test_several_simultaneous_blockers_are_all_reported():
    """The live guard shows one reason; the diagnosis shows every one."""
    state = _clear_state(trades_today=8, trades_by_symbol={"GBPUSD": 4},
                         last_entry_time=NOW - 60)
    d = _diagnose(state, volume=99.0)
    names = [g.name for g in d.blocking]
    assert names == ["maximum_lot", "daily_trade_limit",
                     "symbol_trade_limit", "entry_interval"]
    # ...while still reproducing exactly what can_open_new_trade would say.
    assert "exceeds SESSION_MAXIMUM_LOT" in d.first_reason
    assert "4 of 8 gates" in d.summary()


def test_diagnosis_serialises_for_the_dashboard():
    d = _diagnose(_clear_state(daily_lock=True, lock_reason="stop"))
    payload = d.as_dict()
    assert payload["allowed"] is False
    assert payload["first_reason"] == "stop"
    assert len(payload["gates"]) == len(GATE_NAMES)
    assert payload["gates"][0]["blocking"] is True


def test_state_is_not_mutated_by_diagnosis():
    state = _clear_state(cooldown_until=NOW - 5, trades_today=2)
    before = dict(state)
    _diagnose(state)
    assert state == before


def test_missing_state_keys_are_tolerated():
    d = evaluate_entry_gates({}, _cfg(), "GBPUSD", 0.10, NOW)
    assert d.allowed


# --- rejection ledger ------------------------------------------------------


def test_ledger_counts_attempts_and_dominant_gate():
    ledger = RejectionLedger()
    ledger.record(_diagnose())                                   # allowed
    for _ in range(3):
        ledger.record(_diagnose(_clear_state(last_entry_time=NOW - 60)))
    ledger.record(_diagnose(_clear_state(daily_lock=True)))

    assert ledger.attempts == 5
    assert ledger.allowed == 1
    assert ledger.rejected == 4
    assert ledger.dominant_gate() == "entry_interval"
    assert ledger.by_gate()["entry_interval"] == 3
    assert ledger.by_symbol()["GBPUSD"] == 4


def test_ledger_counts_every_gate_of_a_multi_block_attempt():
    ledger = RejectionLedger()
    ledger.record(_diagnose(_clear_state(trades_today=8), volume=99.0))
    assert ledger.rejected == 1
    assert ledger.by_gate() == {"maximum_lot": 1, "daily_trade_limit": 1}


def test_ledger_report_explains_a_quiet_session():
    ledger = RejectionLedger()
    for _ in range(4):
        ledger.record(_diagnose(_clear_state(last_entry_time=NOW - 60)))
    report = ledger.report()
    assert "Entry attempts: 4" in report
    assert "entry_interval" in report
    assert "100.0% of rejections" in report


def test_empty_ledger_reports_nothing_recorded():
    assert RejectionLedger().report() == "No entry attempts recorded."
    assert RejectionLedger().dominant_gate() is None


def test_ledger_keeps_recent_entries_bounded():
    ledger = RejectionLedger(capacity=5)
    for _ in range(20):
        ledger.record(_diagnose())
    assert len(ledger.recent) == 5
    assert ledger.attempts == 20


def test_ledger_rejects_bad_capacity():
    with pytest.raises(ValueError):
        RejectionLedger(capacity=0)


# --- integration with the live guard --------------------------------------


def test_guard_decision_matches_its_own_diagnosis(tmp_path):
    client = FakeGuardClient()
    guard = RiskGuardedClient(client, _settings(tmp_path), config=_cfg())

    allowed, reason = guard.can_open_new_trade("GBPUSD", client.ORDER_TYPE_BUY,
                                               0.10)
    diagnosis = guard.diagnose_entry("GBPUSD", client.ORDER_TYPE_BUY, 0.10)
    assert allowed is True
    assert diagnosis.allowed is True
    assert diagnosis.first_reason == reason


def test_guard_diagnosis_reports_an_oversized_volume(tmp_path):
    client = FakeGuardClient()
    guard = RiskGuardedClient(client, _settings(tmp_path), config=_cfg())

    allowed, reason = guard.can_open_new_trade("GBPUSD", client.ORDER_TYPE_BUY,
                                               5.0)
    assert allowed is False
    assert "exceeds SESSION_MAXIMUM_LOT" in reason

    diagnosis = guard.diagnose_entry("GBPUSD", client.ORDER_TYPE_BUY, 5.0)
    assert [g.name for g in diagnosis.blocking] == ["maximum_lot"]


def test_guard_ledger_accumulates_across_attempts(tmp_path):
    client = FakeGuardClient()
    guard = RiskGuardedClient(client, _settings(tmp_path), config=_cfg())

    guard.can_open_new_trade("GBPUSD", client.ORDER_TYPE_BUY, 0.10)
    guard.can_open_new_trade("GBPUSD", client.ORDER_TYPE_BUY, 9.0)
    guard.can_open_new_trade("GBPUSD", client.ORDER_TYPE_BUY, 0.0)

    assert guard.rejections.attempts == 3
    assert guard.rejections.rejected == 2
    # A zero volume trips both the positivity gate and the minimum-lot gate;
    # the ledger records each blocker rather than only the first.
    assert set(guard.rejections.by_gate()) == {"maximum_lot", "volume_positive",
                                               "minimum_lot"}


def test_guard_returns_none_diagnosis_without_account(tmp_path):
    client = FakeGuardClient()
    client.account_info = lambda: None
    guard = RiskGuardedClient(client, _settings(tmp_path), config=_cfg())

    assert guard.diagnose_entry("GBPUSD", client.ORDER_TYPE_BUY, 0.10) is None
    allowed, reason = guard.can_open_new_trade("GBPUSD", client.ORDER_TYPE_BUY,
                                               0.10)
    assert allowed is False
    assert reason == "account information unavailable"


def test_gate_status_is_immutable():
    gate = GateStatus(name="x", blocking=True, reason="no")
    with pytest.raises(Exception):
        gate.blocking = False
