"""Resilience: daily-loss tracker, reconnect, and surviving connect failures."""

from datetime import date

from mt5_ai_bridge.app import run
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.risk_engine import DailyLossTracker, RiskLimits, check_risk
from tests.fakes import (FakeMT5Client, make_account, make_order_result,
                         make_settings, make_symbol_info, make_tick)


# --- DailyLossTracker -------------------------------------------------------

def test_tracker_baselines_on_first_observation():
    t = DailyLossTracker()
    assert t.update(10_000, today=date(2026, 6, 29)) == 0
    assert t.start_equity == 10_000


def test_tracker_reports_drawdown_within_day():
    t = DailyLossTracker()
    t.update(10_000, today=date(2026, 6, 29))
    assert t.update(9_800, today=date(2026, 6, 29)) == 200


def test_tracker_resets_on_new_day():
    t = DailyLossTracker()
    t.update(10_000, today=date(2026, 6, 29))
    t.update(9_700, today=date(2026, 6, 29))
    assert t.update(9_700, today=date(2026, 6, 30)) == 0
    assert t.start_equity == 9_700


# --- check_risk with realised daily loss ------------------------------------

def test_daily_loss_blocks_even_without_floating():
    acct = make_account(balance=9_700, equity=9_700)
    r = check_risk(acct, [], RiskLimits(daily_max_loss=250), daily_loss=300)
    assert not r.ok and "daily" in r.message.lower()


def test_daily_loss_within_limit_passes():
    acct = make_account(balance=9_900, equity=9_900)
    assert check_risk(acct, [], RiskLimits(daily_max_loss=250), daily_loss=100).ok


# --- loop resilience --------------------------------------------------------

def _rates(n=60):
    return [{"time": 1_700_000_000 + i * 1800, "open": 1.20, "high": 1.21,
             "low": 1.19, "close": 1.20 + i * 0.0001, "tick_volume": 100}
            for i in range(n)]


def test_loop_recovers_after_transient_disconnect(tmp_path):
    db = str(tmp_path / "j.db")
    client = FakeMT5Client(
        account=make_account(), positions=[], tick=make_tick(),
        symbol_info=make_symbol_info(), rates=_rates(),
        order_result=make_order_result(), account_info_errors=1,
    )
    run(settings=make_settings(db_path=db), client=client,
        journal=Journal(db), max_iterations=2)

    assert client.init_calls >= 2
    j = Journal(db)
    signals = j.recent_signals()
    j.close()
    assert len(signals) == 1


def test_loop_gives_up_after_too_many_failures(tmp_path):
    db = str(tmp_path / "j.db")
    client = FakeMT5Client(account=make_account(), rates=_rates(),
                           account_info_errors=10)
    raised = False
    try:
        run(settings=make_settings(db_path=db, reconnect_attempts=3), client=client,
            journal=Journal(db), max_iterations=10)
    except RuntimeError:
        raised = True
    assert raised


def test_loop_survives_connection_failure(tmp_path):
    db = str(tmp_path / "j.db")
    dash = str(tmp_path / "d.html")
    client = FakeMT5Client(account=make_account(), rates=_rates(), login_ok=False)
    # Login fails every time: the loop must NOT raise; it writes a status page
    # and keeps retrying.
    run(settings=make_settings(db_path=db, write_dashboard=True, dashboard_path=dash),
        client=client, journal=Journal(db), max_iterations=2)

    assert client.init_calls >= 2        # retried the connection
    with open(dash, encoding="utf-8") as fh:
        assert "not connected" in fh.read().lower()
