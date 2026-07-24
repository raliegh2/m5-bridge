"""In-memory fakes for tests (no broker needed)."""

from types import SimpleNamespace

from mt5_ai_bridge.config import Settings
from mt5_ai_bridge.enums import Mode


class FakeResult(SimpleNamespace):
    pass


class FakeMT5Client:
    """Mirrors the surface of mt5_client.RealMT5Client."""

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 2
    ORDER_TIME_GTC = 0
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self, *, account=None, positions=None, tick=None,
                 symbol_info=None, rates=None, order_result=None,
                 account_info_errors=0, login_ok=True):
        self._account = account
        self._positions = positions or []
        self._tick = tick
        self._symbol_info = symbol_info
        self._rates = rates
        self._order_result = order_result
        self._account_info_errors = account_info_errors
        self._login_ok = login_ok
        self.sent_requests = []
        self.init_calls = 0
        self.login_calls = 0
        self.shutdown_called = False

    def initialize(self):
        self.init_calls += 1
        return True

    def login(self, login, password, server):
        self.login_calls += 1
        return self._login_ok

    def shutdown(self):
        self.shutdown_called = True

    def last_error(self):
        return (10004, "login failed") if not self._login_ok else (0, "no error")

    def account_info(self):
        if self._account_info_errors > 0:
            self._account_info_errors -= 1
            raise RuntimeError("simulated disconnect")
        return self._account

    def positions_get(self, **kwargs):
        ticket = kwargs.get("ticket")
        if ticket is not None:
            return [p for p in self._positions if p.ticket == ticket]
        return list(self._positions)

    def symbol_select(self, symbol, enable=True):
        return True

    def symbol_info(self, symbol):
        return self._symbol_info

    def symbol_info_tick(self, symbol):
        return self._tick

    def timeframe(self, name):
        return name

    def copy_rates_from_pos(self, symbol, timeframe_name, start, count):
        return self._rates

    def order_send(self, request):
        self.sent_requests.append(request)
        return self._order_result


def make_account(balance=10000.0, equity=10000.0, margin=0.0, margin_free=10000.0,
                 profit=0.0, login=123):
    return SimpleNamespace(balance=balance, equity=equity, margin=margin,
                           margin_free=margin_free, profit=profit, login=login)


def make_tick(bid=1.2343, ask=1.2345):
    return SimpleNamespace(bid=bid, ask=ask)


def make_symbol_info(digits=5, point=0.00001):
    return SimpleNamespace(digits=digits, point=point)


def make_position(ticket=1, symbol="GBPUSD", ptype=0, volume=0.09, profit=0.0,
                  sl=0.0, tp=0.0, price_open=1.2700, price_current=1.2710,
                  magic=0):
    return SimpleNamespace(ticket=ticket, symbol=symbol, type=ptype,
                           volume=volume, profit=profit, sl=sl, tp=tp,
                           price_open=price_open, price_current=price_current,
                           magic=magic)


def make_order_result(retcode=10009, order=999, comment="Done"):
    return FakeResult(retcode=retcode, order=order, comment=comment)


def make_settings(**kw) -> Settings:
    """Construct a Settings with test defaults; override any field via kwargs.

    ATR stops and risk sizing default OFF so book tests are deterministic.
    """
    base = dict(
        login=1, password="x", server="s", symbol="GBPUSD",
        symbols=("GBPUSD",), combined_risk_ceiling=3.5, mode=Mode.READ_ONLY,
        timeframe="M15", strategy="trend", reasoning_threshold=0.6,
        rsi_overbought=75, rsi_oversold=25,
        lot_size=0.09, ny_size_multiplier=2.0, ny_start_hour=12, ny_end_hour=21,
        swing_confidence=0.7, intraday_sl_pips=20, intraday_tp_pips=40,
        swing_sl_pips=80, swing_tp_pips=160,
        stop_loss_pips=30, take_profit_pips=60,
        daily_max_loss=250, total_max_loss=500, max_open_positions=7,
        max_trades_per_day=20,
        strong_trend_confidence=0.8, max_same_direction=3, min_same_direction=3,
        tp_stagger_step=0.5, sl_stagger_step=0.25, sl_floor_pips=10,
        trail_enabled=False, trail_start_pips=20, trail_distance_pips=15,
        atr_enabled=False, atr_period=14, atr_sl_mult=2.0, atr_tp_mult=4.0,
        atr_min_sl_pips=8, atr_max_sl_pips=200,
        risk_based_sizing=False, risk_percent=0.5,
        intraday_risk_percent=0.15, swing_risk_percent=0.35,
        swing_risk_overrides=(), intraday_risk_overrides=(),
        pip_value_per_lot=10.0,
        max_lot=2.0,
        multi_book=False, require_trend_alignment=True, trend_tf_mid="M30",
        swing_tf_high="H4", swing_tf_higher="D1",
        swing_strong_max=2, day_timeframe="M15", day_sl_pips=15, day_tp_pips=30,
        day_strong_max=2, scalp_timeframe="M5", scalp_sl_pips=8, scalp_tp_pips=16,
        scalp_strong_max=1,
        write_dashboard=False, dashboard_path="dashboard.html",
        dashboard_refresh_seconds=5, serve_dashboard=False, dashboard_port=8800,
        dashboard_host="127.0.0.1", console_status=False,
        loop_interval_seconds=0, log_level="INFO", db_path=":memory:",
        reconnect_attempts=3, reconnect_delay_seconds=0,
    )
    base.update(kw)
    # Keep symbols consistent with an overridden single symbol unless the test
    # set symbols explicitly.
    if "symbols" not in kw:
        base["symbols"] = (base["symbol"],)
    return Settings(**base)
