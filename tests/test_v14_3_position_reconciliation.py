from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from mt5_ai_bridge.v14_3_live_execution import LiveSignal
from mt5_ai_bridge.v14_3_position_reconciliation import (
    ReconciledResearchParityLiveExecutor,
)
from mt5_ai_bridge.v14_3_research_parity_execution import (
    ResearchParityLiveRunnerConfig,
)


class FakeBrokerClient:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ACCOUNT_TRADE_MODE_DEMO = 0
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    DEAL_ENTRY_INOUT = 2
    DEAL_ENTRY_OUT_BY = 3

    def __init__(self) -> None:
        self.account = SimpleNamespace(
            balance=100000.0,
            equity=100000.0,
            login=12345,
            server="UnitTest-Demo",
            trade_mode=0,
        )
        self.info = SimpleNamespace(
            visible=True,
            point=0.00001,
            digits=5,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_stops_level=10,
            filling_mode=1,
        )
        self.tick = SimpleNamespace(bid=1.35350, ask=1.35360)
        self.positions: list[SimpleNamespace] = []
        self.deals: list[SimpleNamespace] = []
        self.sent_request = None

    def account_info(self):
        return self.account

    def positions_get(self, **kwargs):
        values = list(self.positions)
        symbol = kwargs.get("symbol")
        if symbol:
            values = [position for position in values if position.symbol == symbol]
        return values

    def symbol_info(self, _symbol):
        return self.info

    def symbol_info_tick(self, _symbol):
        return self.tick

    def order_calc_profit(self, _order_type, _symbol, volume, price_open, price_close):
        return -abs(price_open - price_close) * 100000.0 * float(volume)

    def order_check(self, _request):
        return SimpleNamespace(retcode=0, comment="Done")

    def order_send(self, request):
        self.sent_request = dict(request)
        self.positions = [
            SimpleNamespace(
                ticket=900,
                identifier=77,
                symbol=request["symbol"],
                type=self.POSITION_TYPE_BUY,
                volume=request["volume"],
                price_open=request["price"],
                price_current=request["price"],
                sl=request["sl"],
                tp=request["tp"],
                profit=0.0,
                magic=request["magic"],
                time=1_752_600_060,
                time_msc=1_752_600_060_000,
            )
        ]
        # The broker's order ticket is deliberately different from the position
        # ticket and stable position identifier.
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=500,
            deal=501,
            comment="Done",
        )

    def history_deals_get(self, *_args, **_kwargs):
        return list(self.deals)

    def last_error(self):
        return (0, "OK")


def _config(tmp_path) -> ResearchParityLiveRunnerConfig:
    result = ResearchParityLiveRunnerConfig(
        execution_mode="AUTO",
        state_path=str(tmp_path / "state.json"),
        forward_gate_passed=True,
        allow_demo_auto=True,
        maximum_signal_age_minutes=90,
    )
    result.validate()
    return result


def _signal() -> LiveSignal:
    return LiveSignal(
        symbol="GBPUSD",
        broker_symbol="GBPUSD",
        engine="ICT_V14_3_GBPUSD",
        setup="breakout_60_fade",
        mode="ICT",
        side="BUY",
        signal_time=datetime(2025, 7, 15, 12, 0, tzinfo=timezone.utc),
        requested_risk_percent=0.731,
        stop_pips=6.0,
        target_pips=7.5,
        metadata={},
    )


def test_order_ticket_is_remapped_to_actual_position_and_loss_is_reconciled(
    tmp_path,
) -> None:
    client = FakeBrokerClient()
    executor = ReconciledResearchParityLiveExecutor(client, _config(tmp_path))
    now = datetime(2025, 7, 15, 12, 1, tzinfo=timezone.utc)

    result = executor.place(_signal(), now=now)
    assert result.code == "ORDER_FILLED"
    assert result.ticket == 900
    assert "900" in executor.state.data["positions"]
    assert "500" not in executor.state.data["positions"]
    stored = executor.state.data["positions"]["900"]
    assert stored["position_identifier"] == 77
    assert stored["order_ticket"] == 500

    client.positions = []
    client.deals = [
        SimpleNamespace(
            ticket=501,
            order=500,
            position_id=77,
            symbol="GBPUSD",
            magic=20264331,
            entry=client.DEAL_ENTRY_IN,
            profit=0.0,
            commission=0.0,
            swap=0.0,
            fee=0.0,
            time=1_752_600_060,
        ),
        SimpleNamespace(
            ticket=601,
            order=600,
            position_id=77,
            symbol="GBPUSD",
            magic=20264331,
            entry=client.DEAL_ENTRY_OUT,
            profit=-720.0,
            commission=0.0,
            swap=0.0,
            fee=0.0,
            time=1_752_603_600,
        ),
    ]

    executor.reconcile(datetime(2025, 7, 15, 13, 1, tzinfo=timezone.utc))
    day = executor.state.data["day"]
    assert executor.state.data["positions"] == {}
    assert day["global_consecutive_losses"] == 1
    assert day["global_daily_losses"] == 1
    assert day["symbol_losses"]["GBPUSD"] == 1
    assert day["symbol_consecutive_losses"]["GBPUSD"] == 1
    assert day["symbol_loss_pressure"]["GBPUSD"] == 1.0
    assert day["symbol_pnl"]["GBPUSD"] == -720.0

    # Repeated reconciliation must not count the same closed position twice.
    executor.reconcile(datetime(2025, 7, 15, 13, 2, tzinfo=timezone.utc))
    assert executor.state.data["day"]["global_daily_losses"] == 1
