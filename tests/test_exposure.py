"""Currency-factor exposure decomposition and caps."""

from mt5_ai_bridge import exposure


def test_currency_legs_fx_and_metal_and_suffix():
    assert exposure.currency_legs("EURUSD") == ("EUR", "USD")
    assert exposure.currency_legs("GBPJPY") == ("GBP", "JPY")
    assert exposure.currency_legs("XAUUSD") == ("XAU", "USD")
    # broker suffixes are tolerated
    assert exposure.currency_legs("EURUSD.pro") == ("EUR", "USD")
    assert exposure.currency_legs("XAUUSD.m") == ("XAU", "USD")
    assert exposure.currency_legs("") is None
    assert exposure.currency_legs("BADGE") is None  # <6 letters


def test_correlated_longs_are_one_short_usd_bet():
    # long EURUSD + long AUDUSD + long XAUUSD are ALL short USD.
    positions = [("EURUSD", True, 1.0), ("AUDUSD", True, 1.0),
                 ("XAUUSD", True, 0.5)]
    net = exposure.factor_exposure(positions)
    assert round(net["USD"], 3) == -2.5      # net short USD accumulates
    assert net["EUR"] == 1.0 and net["AUD"] == 1.0 and net["XAU"] == 0.5


def test_offsetting_positions_net_out():
    # long EURUSD and long USDJPY partly offset on USD.
    net = exposure.factor_exposure([("EURUSD", True, 1.0),
                                    ("USDJPY", True, 1.0)])
    assert net["USD"] == 0.0
    assert net["EUR"] == 1.0 and net["JPY"] == -1.0


def test_short_flips_sign():
    net = exposure.factor_exposure([("EURUSD", False, 1.0)])
    assert net["EUR"] == -1.0 and net["USD"] == 1.0


def test_breach_blocks_third_correlated_leg():
    existing = [("EURUSD", True, 1.05), ("AUDUSD", True, 1.05)]  # USD -2.10
    hit = exposure.breach(existing, "XAUUSD", True, 0.2, cap=2.0)
    assert hit is not None
    ccy, val = hit
    assert ccy == "USD" and round(val, 2) == -2.30


def test_breach_allows_within_cap():
    existing = [("EURUSD", True, 1.05)]
    assert exposure.breach(existing, "AUDUSD", True, 0.2, cap=2.0) is None


def test_diversifying_trade_not_blocked():
    # already short USD; a trade that BUYS usd (short EURUSD) reduces net risk.
    existing = [("EURUSD", True, 1.5), ("AUDUSD", True, 1.5)]  # USD -3.0
    # selling AUDUSD is +USD, lowers concentration -> allowed
    assert exposure.breach(existing, "AUDUSD", False, 1.0, cap=2.0) is None


def test_nonpositive_cap_disables_check():
    existing = [("EURUSD", True, 5.0)]
    assert exposure.breach(existing, "GBPUSD", True, 5.0, cap=0) is None
    assert exposure.breach(existing, "GBPUSD", True, 5.0, cap=None) is None


def test_unparseable_symbol_is_ignored():
    assert exposure.breach([], "ZZZ", True, 1.0, cap=0.1) is None


# --- integration: the cap wired into the live _run_books path -----------------
from datetime import datetime, timezone

from mt5_ai_bridge.app import _run_books, make_planner_configs
from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.strategy import Decision
from tests.fakes import (FakeMT5Client, make_account, make_order_result,
                         make_settings, make_symbol_info, make_tick)

_NY = datetime(2026, 6, 29, 15, tzinfo=timezone.utc)


def _rates(n=60):
    return [{"time": 1_700_000_000 + i * 1800, "open": 1.20, "high": 1.21,
             "low": 1.19, "close": 1.20 + i * 0.0001, "tick_volume": 100}
            for i in range(n)]


def _client():
    return FakeMT5Client(account=make_account(), positions=[], tick=make_tick(),
                         symbol_info=make_symbol_info(), rates=_rates(),
                         order_result=make_order_result())


def _run(client, settings):
    _run_books(client, Journal(":memory:"), settings, lambda _m: Decision(
        Signal.BUY, "x", 0.85), make_planner_configs(settings), [], now_utc=_NY)


def test_tight_currency_cap_blocks_all_new_usd_risk():
    # A strong aligned trend normally opens 4 GBPUSD longs (all short USD). A
    # cap below one engine's risk means the correlated USD exposure is refused.
    client = _client()
    _run(client, make_settings(multi_book=True, max_open_positions=7,
                               factor_caps=True, max_currency_risk=0.10))
    assert client.sent_requests == []


def test_cap_disabled_lets_the_same_trades_through():
    client = _client()
    _run(client, make_settings(multi_book=True, max_open_positions=7,
                               factor_caps=False, max_currency_risk=0.10))
    assert len(client.sent_requests) == 4       # nothing blocked when off


def test_default_loose_cap_does_not_over_block():
    client = _client()
    _run(client, make_settings(multi_book=True, max_open_positions=7))
    assert len(client.sent_requests) == 4       # default 2.0 cap allows ~1% USD


def test_account_exposure_weights_by_engine_and_sorts():
    from mt5_ai_bridge.app import _account_exposure, build_books
    from tests.fakes import make_position

    class _Stub:
        POSITION_TYPE_BUY = 0

    settings = make_settings(multi_book=True)
    books = build_books(settings)
    sw = next(b.magic for b in books
              if b.timeframe.upper() == settings.swing_tf_high.upper())
    dy = next(b.magic for b in books
              if b.timeframe.upper() == settings.day_timeframe.upper())
    positions = [make_position(symbol="EURUSD", ptype=0, magic=sw),
                 make_position(symbol="XAUUSD", ptype=0, magic=dy)]
    view = _account_exposure(_Stub(), settings, positions)
    assert view["on"] is True and view["cap"] == settings.max_currency_risk
    cur = {r["currency"]: r["net"] for r in view["rows"]}
    assert cur["USD"] < 0                       # both legs are short USD
    assert cur["EUR"] > 0 and cur["XAU"] > 0
    assert view["rows"][0]["currency"] == "USD"  # most concentrated leg first
    assert view["rows"][0]["over"] is False


def test_account_exposure_empty_when_flat():
    from mt5_ai_bridge.app import _account_exposure

    class _Stub:
        POSITION_TYPE_BUY = 0

    assert _account_exposure(_Stub(), make_settings(), [])["rows"] == []
