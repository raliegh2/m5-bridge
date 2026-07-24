"""Account-level circuit-breaker tests; no MetaTrader installation required."""

from datetime import datetime, timezone
from types import SimpleNamespace

from mt5_ai_bridge.session_guard import RiskGuardedClient, SessionGuardConfig

DAY = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc).timestamp()


class FakeGuardClient:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 2
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_REMOVE = 8
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    DEAL_ENTRY_INOUT = 2
    DEAL_ENTRY_OUT_BY = 3

    def __init__(self):
        self.account = SimpleNamespace(balance=5000.0, equity=5000.0)
        self.tick = SimpleNamespace(bid=1.0, ask=1.1, time=DAY)
        self.positions = []
        self.deals = []
        self.requests = []

    def account_info(self):
        return self.account

    def positions_get(self, **kwargs):
        return list(self.positions)

    def symbol_info_tick(self, symbol):
        return self.tick

    def order_send(self, request):
        self.requests.append(request)
        number = len(self.requests)
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            comment="done",
            order=number,
            deal=number,
        )

    def history_deals_get(self, date_from, date_to):
        return list(self.deals)



def _settings(tmp_path):
    return SimpleNamespace(
        symbol="XAUUSD",
        symbols=("XAUUSD", "GBPUSD"),
        max_trades_per_day=20,
        db_path=str(tmp_path / "journal.db"),
        day_timeframe="M15",
        swing_tf_high="H4",
        swing_tf_higher="D1",
        scalp_timeframe="M5",
        swing_sl_pips=80,
        swing_tp_pips=160,
        day_sl_pips=15,
        day_tp_pips=30,
        scalp_sl_pips=8,
        scalp_tp_pips=16,
        swing_strong_max=2,
        day_strong_max=2,
        scalp_strong_max=1,
    )


def _config(tmp_path, **overrides):
    values = dict(
        state_path=str(tmp_path / "state.json"),
        history_sync_seconds=0,
        minimum_minutes_between_entries=0,
        maximum_lot=1.0,
        max_trades_per_day=8,
        max_trades_per_symbol_per_day=4,
    )
    values.update(overrides)
    return SessionGuardConfig(**values)


def _entry(symbol="XAUUSD", volume=0.2):
    return {
        "action": FakeGuardClient.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": FakeGuardClient.ORDER_TYPE_BUY,
    }


def _closed_trade(position_id, seconds, profit, symbol="XAUUSD"):
    return [
        SimpleNamespace(
            position_id=position_id,
            magic=20260801,
            time=DAY + seconds,
            entry=FakeGuardClient.DEAL_ENTRY_IN,
            symbol=symbol,
            order=position_id,
            ticket=position_id * 10,
            profit=0,
            commission=-1,
            swap=0,
            fee=0,
        ),
        SimpleNamespace(
            position_id=position_id,
            magic=20260801,
            time=DAY + seconds + 1,
            entry=FakeGuardClient.DEAL_ENTRY_OUT,
            symbol=symbol,
            order=position_id + 100,
            ticket=position_id * 10 + 1,
            profit=profit,
            commission=-1,
            swap=0,
            fee=0,
        ),
    ]


def test_daily_equity_loss_locks_new_entries(tmp_path):
    client = FakeGuardClient()
    guard = RiskGuardedClient(
        client, _settings(tmp_path), config=_config(tmp_path))
    guard.account_info()

    client.account.equity = 4949.0
    guard.account_info()
    result = guard.order_send(_entry())

    assert result.retcode != client.TRADE_RETCODE_DONE
    assert "daily equity loss limit" in result.comment
    assert guard.status()["daily_lock"] is True


def test_profit_giveback_locks_at_configured_floor(tmp_path):
    client = FakeGuardClient()
    guard = RiskGuardedClient(
        client, _settings(tmp_path), config=_config(tmp_path))
    guard.account_info()

    client.account.equity = 5084.0
    guard.account_info()
    client.account.equity = 5050.0
    guard.account_info()

    status = guard.status()
    assert status["daily_lock"] is True
    assert status["lock_kind"] == "profit_giveback"


def test_three_completed_losses_lock_and_partial_close_waits(tmp_path):
    client = FakeGuardClient()
    guard = RiskGuardedClient(
        client, _settings(tmp_path), config=_config(tmp_path))
    guard.account_info()
    client.deals = (
        _closed_trade(1, 1, -10)
        + _closed_trade(2, 10, -11)
        + _closed_trade(3, 20, -12)
    )

    guard.account_info()
    assert guard.status()["daily_lock"] is True
    assert guard.status()["consecutive_losses"] == 3

    partial_dir = tmp_path / "partial"
    partial_client = FakeGuardClient()
    partial_client.positions = [SimpleNamespace(
        ticket=7,
        identifier=7,
        magic=20260801,
        symbol="XAUUSD",
        type=FakeGuardClient.POSITION_TYPE_BUY,
        volume=0.1,
    )]
    partial_client.deals = _closed_trade(7, 1, -10)
    partial_guard = RiskGuardedClient(
        partial_client,
        _settings(partial_dir),
        config=_config(partial_dir),
    )
    partial_guard.account_info()
    assert partial_guard.status()["consecutive_losses"] == 0


def test_minimum_entry_interval_blocks_cross_engine_reentry(tmp_path):
    client = FakeGuardClient()
    guard = RiskGuardedClient(
        client,
        _settings(tmp_path),
        config=_config(tmp_path, minimum_minutes_between_entries=15),
    )
    guard.account_info()

    assert guard.order_send(_entry()).retcode == client.TRADE_RETCODE_DONE
    result = guard.order_send(_entry("GBPUSD"))
    assert result.retcode != client.TRADE_RETCODE_DONE
    assert "minimum entry interval" in result.comment


def test_position_management_bypasses_daily_entry_lock(tmp_path):
    client = FakeGuardClient()
    guard = RiskGuardedClient(
        client, _settings(tmp_path), config=_config(tmp_path))
    guard.account_info()
    client.account.equity = 4900.0
    guard.account_info()

    close_request = {
        "action": client.TRADE_ACTION_DEAL,
        "position": 123,
        "symbol": "XAUUSD",
        "volume": 0.1,
        "type": client.ORDER_TYPE_SELL,
    }
    assert guard.order_send(close_request).retcode == client.TRADE_RETCODE_DONE


def test_maximum_lot_is_enforced_centrally(tmp_path):
    client = FakeGuardClient()
    guard = RiskGuardedClient(
        client,
        _settings(tmp_path),
        config=_config(tmp_path, maximum_lot=0.4),
    )
    guard.account_info()

    result = guard.order_send(_entry(volume=0.41))
    assert result.retcode != client.TRADE_RETCODE_DONE
    assert "SESSION_MAXIMUM_LOT" in result.comment


def test_daily_lock_survives_process_restart(tmp_path):
    client = FakeGuardClient()
    config = _config(tmp_path)
    guard = RiskGuardedClient(client, _settings(tmp_path), config=config)
    guard.account_info()
    client.account.equity = 4940.0
    guard.account_info()
    assert guard.status()["daily_lock"] is True

    restarted = RiskGuardedClient(client, _settings(tmp_path), config=config)
    assert restarted.status()["daily_lock"] is True
