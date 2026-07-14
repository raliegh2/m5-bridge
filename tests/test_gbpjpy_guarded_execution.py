from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from mt5_ai_bridge.gbpjpy_guard import GBPJPYGuardStore
from mt5_ai_bridge.gbpjpy_strict_execution import GBPJPYStrictExecutor
from mt5_ai_bridge.v12_final_execution import ENGINE_MAGIC, FinalExecutionRequest
from mt5_ai_bridge.v12_final_state import StateStore


UTC = timezone.utc


class Client:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self, positions=None, spread=0.020):
        self._positions = list(positions or [])
        self._account = SimpleNamespace(
            balance=5000.0,
            equity=5000.0,
            login=12345,
            server="Broker-Demo",
            trade_mode=0,
        )
        self._tick = SimpleNamespace(bid=217.000, ask=217.000 + spread)
        self._info = SimpleNamespace(
            digits=3,
            point=0.001,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=100.0,
            filling_mode=1,
        )
        self.sent = []
        self.next_ticket = 9000
        self.deals = {}

    def account_info(self):
        return self._account

    def positions_get(self, **kwargs):
        rows = list(self._positions)
        if "ticket" in kwargs:
            rows = [p for p in rows if p.ticket == kwargs["ticket"]]
        if "symbol" in kwargs:
            rows = [p for p in rows if p.symbol == kwargs["symbol"]]
        return rows

    def history_deals_get(self, **kwargs):
        return self.deals.get(int(kwargs["position"]), [])

    def symbol_info(self, _symbol):
        return self._info

    def symbol_info_tick(self, _symbol):
        return self._tick

    def order_calc_profit(self, *_args):
        return 6.50

    def order_send(self, request):
        self.sent.append(request)
        self.next_ticket += 1
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=self.next_ticket,
            deal=self.next_ticket + 1000,
            comment="done",
        )

    @staticmethod
    def last_error():
        return (0, "ok")


def request(at: datetime, **overrides) -> FinalExecutionRequest:
    data = dict(
        symbol="GBPJPY",
        engine="GBPJPY_SWING_CORE",
        setup="H4_DONCHIAN_BREAKOUT",
        side="SELL",
        base_risk_percent=0.15,
        stop_pips=50.0,
        target_pips=100.0,
        signal_time=at,
    )
    data.update(overrides)
    return FinalExecutionRequest(**data)


def open_position():
    return SimpleNamespace(
        ticket=88,
        symbol="GBPJPY",
        type=Client.POSITION_TYPE_SELL,
        volume=0.01,
        price_open=217.000,
        sl=217.500,
        tp=216.000,
        magic=ENGINE_MAGIC["GBPJPY_SWING_CORE"],
    )


def executor(tmp_path, client=None):
    client = client or Client()
    return GBPJPYStrictExecutor(
        client,
        state=StateStore(str(tmp_path / "state.json")),
        gbpjpy_guard=GBPJPYGuardStore(str(tmp_path / "guard.json")),
    ), client


def test_initial_gbpjpy_order_uses_guarded_risk_and_post_loss_reduces_volume(tmp_path):
    bot, client = executor(tmp_path)
    start = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    first = bot.place(request(start), now=start)
    assert first.ok
    assert client.sent[-1]["volume"] == 0.02
    assert first.risk_percent <= 0.15

    bot.record_closed_trade(
        "GBPJPY_SWING_CORE", -1.0,
        now=start + timedelta(hours=1),
    )
    second_time = start + timedelta(hours=2)
    second = bot.place(request(second_time), now=second_time)
    assert second.ok
    assert client.sent[-1]["volume"] == 0.01
    assert second.risk_percent <= 0.10


def test_existing_gbpjpy_position_blocks_stacking(tmp_path):
    bot, client = executor(tmp_path, Client([open_position()]))
    at = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    result = bot.place(request(at), now=at)
    assert not result.ok
    assert result.code == "GBPJPY_ONE_POSITION_LIMIT"
    assert not client.sent


def test_two_gbpjpy_losses_stop_new_orders_for_day(tmp_path):
    bot, client = executor(tmp_path)
    start = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    bot.record_closed_trade("GBPJPY_SWING_CORE", -1.0, now=start)
    bot.record_closed_trade(
        "GBPJPY_SWING_CORE", -0.5,
        now=start + timedelta(hours=1),
    )
    result = bot.place(
        request(start + timedelta(hours=2)),
        now=start + timedelta(hours=2),
    )

    assert not result.ok
    assert result.code == "GBPJPY_DAILY_STOP"
    assert not client.sent


def test_broker_closed_gbpjpy_is_reconciled_from_deal_history(tmp_path):
    bot, client = executor(tmp_path)
    start = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    first = bot.place(request(start), now=start)
    assert first.ok and first.ticket is not None
    client.deals[first.ticket] = [
        SimpleNamespace(profit=-6.50, commission=0.0, swap=0.0, fee=0.0)
    ]

    next_time = start + timedelta(hours=2)
    second = bot.place(request(next_time), now=next_time)

    assert second.ok
    assert bot.gbpjpy_guard.state.daily_losses == 1
    assert client.sent[-1]["volume"] == 0.01


def test_missing_close_history_fails_closed(tmp_path):
    bot, _client = executor(tmp_path)
    start = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    first = bot.place(request(start), now=start)
    assert first.ok
    result = bot.place(
        request(start + timedelta(hours=2)),
        now=start + timedelta(hours=2),
    )

    assert not result.ok
    assert result.code == "GBPJPY_CLOSE_UNRECONCILED"


def test_gbpjpy_outside_london_new_york_window_is_blocked(tmp_path):
    bot, client = executor(tmp_path)
    at = datetime(2026, 7, 14, 3, 0, tzinfo=UTC)

    result = bot.place(request(at), now=at)

    assert not result.ok
    assert result.code == "GBPJPY_SESSION_BLOCK"
    assert not client.sent


def test_gbpjpy_wide_spread_is_blocked(tmp_path):
    bot, client = executor(tmp_path, Client(spread=0.040))
    at = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    result = bot.place(request(at), now=at)

    assert not result.ok
    assert result.code == "GBPJPY_SPREAD_BLOCK"
    assert not client.sent


def test_gbpjpy_low_reward_risk_is_blocked(tmp_path):
    bot, client = executor(tmp_path)
    at = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    result = bot.place(request(at, target_pips=60.0), now=at)

    assert not result.ok
    assert result.code == "GBPJPY_REWARD_RISK_BLOCK"
    assert not client.sent


def test_gbpjpy_stop_outside_guarded_range_is_blocked(tmp_path):
    bot, client = executor(tmp_path)
    at = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    result = bot.place(request(at, stop_pips=10.0), now=at)

    assert not result.ok
    assert result.code == "GBPJPY_STOP_RANGE_BLOCK"
    assert not client.sent
