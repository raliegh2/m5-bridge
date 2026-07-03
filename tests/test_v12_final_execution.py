from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from mt5_ai_bridge.v12_final_execution import (
    ENGINE_MAGIC,
    FinalExecutionRequest,
    FinalMT5Executor,
)
from mt5_ai_bridge.v12_final_state import StateStore


class Client:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self, positions=None, spread=0.00010, trade_mode=0,
                 retcode=10009):
        self._positions = list(positions or [])
        self._account = SimpleNamespace(
            balance=5000.0, equity=5000.0, login=12345,
            server="Broker-Demo", trade_mode=trade_mode,
        )
        self._tick = SimpleNamespace(bid=1.10000, ask=1.10000 + spread)
        self._info = SimpleNamespace(
            digits=5, point=0.00001, volume_step=0.01,
            volume_min=0.01, volume_max=100.0, filling_mode=1,
        )
        self.retcode = retcode
        self.sent = []

    def account_info(self):
        return self._account

    def positions_get(self, **kwargs):
        rows = list(self._positions)
        if "ticket" in kwargs:
            rows = [p for p in rows if p.ticket == kwargs["ticket"]]
        if "symbol" in kwargs:
            rows = [p for p in rows if p.symbol == kwargs["symbol"]]
        return rows

    def symbol_info(self, _symbol):
        return self._info

    def symbol_info_tick(self, _symbol):
        return self._tick

    def order_calc_profit(self, *_args):
        return 10.0

    def order_send(self, request):
        self.sent.append(request)
        return SimpleNamespace(retcode=self.retcode, order=9001, deal=8001,
                               comment="done")

    @staticmethod
    def last_error():
        return (0, "ok")


def request(**overrides):
    data = dict(
        symbol="AUDUSD", engine="AUDUSD_TREND_PULLBACK",
        setup="D1_H4_EMA_PULLBACK_04_08UTC", side="BUY",
        base_risk_percent=0.25, stop_pips=50.0, target_pips=100.0,
        signal_time=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    data.update(overrides)
    return FinalExecutionRequest(**data)


def position(ticket=42, magic=None, volume=0.02, sl=1.09510):
    return SimpleNamespace(
        ticket=ticket, symbol="AUDUSD", type=Client.POSITION_TYPE_BUY,
        volume=volume, price_open=1.10010, sl=sl, tp=1.11010,
        magic=magic if magic is not None else ENGINE_MAGIC["AUDUSD_TREND_PULLBACK"],
    )


def test_executor_rejects_account_mode_mismatch(tmp_path) -> None:
    client = Client(trade_mode=2)
    result = FinalMT5Executor(client, state=StateStore(str(tmp_path / "s.json"))).place(request())
    assert not result.ok and result.code == "ACCOUNT_MODE_MISMATCH"
    assert not client.sent


def test_executor_automatically_trades_matching_live_account(tmp_path) -> None:
    client = Client(trade_mode=2)
    result = FinalMT5Executor(
        client, state=StateStore(str(tmp_path / "s.json")),
        account_mode_provider=lambda: "LIVE",
    ).place(request())
    assert result.ok and result.code == "ORDER_FILLED"
    assert len(client.sent) == 1


def test_executor_sends_complete_native_order_and_persists_ticket(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    client = Client()
    executor = FinalMT5Executor(client, state=StateStore(str(state_path)))
    result = executor.place(request())
    assert result.ok and result.code == "ORDER_FILLED" and result.ticket == 9001
    assert result.volume == 0.02
    sent = client.sent[0]
    assert sent == {
        "action": client.TRADE_ACTION_DEAL, "symbol": "AUDUSD",
        "volume": 0.02, "type": client.ORDER_TYPE_BUY, "price": 1.1001,
        "sl": 1.0951, "tp": 1.1101, "deviation": 10,
        "magic": ENGINE_MAGIC["AUDUSD_TREND_PULLBACK"],
        "comment": "V12 AUDUSD_TREND_PULLBACK", "type_time": 0,
        "type_filling": 1,
    }
    assert "9001" in StateStore(str(state_path)).state.positions


def test_duplicate_signal_is_not_sent_twice(tmp_path) -> None:
    client = Client()
    executor = FinalMT5Executor(client, state=StateStore(str(tmp_path / "s.json")))
    assert executor.place(request()).ok
    result = executor.place(request())
    assert not result.ok and result.code == "DUPLICATE_ORDER"
    assert len(client.sent) == 1


def test_rejected_retcode_is_reported(tmp_path) -> None:
    client = Client(retcode=10016)
    result = FinalMT5Executor(client, state=StateStore(str(tmp_path / "s.json"))).place(request())
    assert not result.ok and result.code == "ORDER_REJECTED"


def test_restart_recovers_known_magic_position(tmp_path) -> None:
    state = StateStore(str(tmp_path / "s.json"))
    result = FinalMT5Executor(Client([position()]), state=state).reconcile_open_positions()
    assert result.ok
    assert state.state.positions["42"].engine == "AUDUSD_TREND_PULLBACK"


def test_unknown_open_position_fails_closed(tmp_path) -> None:
    unknown = position(magic=999)
    result = FinalMT5Executor(
        Client([unknown]), state=StateStore(str(tmp_path / "s.json"))
    ).reconcile_open_positions()
    assert not result.ok and result.code == "UNREGISTERED_POSITION"


def test_modify_and_close_use_mt5_management_requests(tmp_path) -> None:
    item = position()
    client = Client([item])
    executor = FinalMT5Executor(client, state=StateStore(str(tmp_path / "s.json")))
    assert executor.reconcile_open_positions().ok
    modified = executor.modify(42, stop_loss=1.09700, take_profit=1.11200)
    assert modified.ok and client.sent[-1]["action"] == client.TRADE_ACTION_SLTP
    closed = executor.close(42)
    assert closed.ok
    assert client.sent[-1]["action"] == client.TRADE_ACTION_DEAL
    assert client.sent[-1]["position"] == 42
    assert client.sent[-1]["type"] == client.ORDER_TYPE_SELL
    assert "42" not in executor.state.state.positions


def test_wide_spread_and_disabled_engine_remain_blocked(tmp_path) -> None:
    wide = FinalMT5Executor(Client(spread=0.00040), state=StateStore(str(tmp_path / "a.json")))
    assert wide.place(request()).code == "SPREAD_TOO_WIDE"
    disabled = FinalMT5Executor(Client(), state=StateStore(str(tmp_path / "b.json")))
    result = disabled.place(request(
        symbol="GBPUSD", engine="GBPUSD_SWING_CORE",
        setup="H4_DONCHIAN_BREAKOUT", base_risk_percent=0.20,
    ))
    assert result.code == "ENGINE_NOT_ALLOWED"
