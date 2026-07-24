"""AUTO execution: minimum-stack burst on strong trends, caps, and guards."""

from mt5_ai_bridge.app import run
from mt5_ai_bridge.enums import Mode, Signal
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.strategy import Decision
from tests.fakes import (FakeMT5Client, make_account, make_order_result,
                         make_position, make_settings, make_symbol_info,
                         make_tick)


def _rates(n=60):
    return [{"time": 1_700_000_000 + i * 1800, "open": 1.20, "high": 1.21,
             "low": 1.19, "close": 1.20 + i * 0.0001, "tick_volume": 100}
            for i in range(n)]


def _client(positions=None):
    return FakeMT5Client(
        account=make_account(balance=10000, equity=10000),
        positions=positions or [], tick=make_tick(bid=1.2343, ask=1.2345),
        symbol_info=make_symbol_info(), rates=_rates(),
        order_result=make_order_result(order=777),
    )


def _buy(conf):
    def fn(_m):
        return Decision(Signal.BUY, "forced buy", conf)
    return fn


_STRONG = _buy(0.85)   # >= strong_trend_confidence (0.8)
_WEAK = _buy(0.6)      # < strong


def _buys(n):
    return [make_position(ticket=i, ptype=FakeMT5Client.POSITION_TYPE_BUY)
            for i in range(n)]


def test_strong_trend_opens_minimum_three(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client()
    run(settings=make_settings(mode=Mode.AUTO, db_path=db,
                               min_same_direction=3, max_same_direction=3),
        client=client, journal=Journal(db), strategy_fn=_STRONG, max_iterations=1)

    assert len(client.sent_requests) == 3                 # burst to the minimum
    assert all(r["type"] == client.ORDER_TYPE_BUY for r in client.sent_requests)
    # staggered exits -> the three stops are at distinct prices
    assert len({r["sl"] for r in client.sent_requests}) == 3


def test_weak_trend_opens_single(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client()
    run(settings=make_settings(mode=Mode.AUTO, db_path=db),
        client=client, journal=Journal(db), strategy_fn=_WEAK, max_iterations=1)
    assert len(client.sent_requests) == 1


def test_strong_trend_tops_up_to_minimum(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client(positions=_buys(2))   # already 2 longs
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, min_same_direction=3,
                               max_same_direction=3),
        client=client, journal=Journal(db), strategy_fn=_STRONG, max_iterations=1)
    assert len(client.sent_requests) == 1   # opens just the 3rd


def test_does_not_exceed_max_same_direction(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client(positions=_buys(3))
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, max_same_direction=3),
        client=client, journal=Journal(db), strategy_fn=_STRONG, max_iterations=1)
    assert client.sent_requests == []


def test_burst_respects_max_open_positions(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client()
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, min_same_direction=3,
                               max_same_direction=3, max_open_positions=2),
        client=client, journal=Journal(db), strategy_fn=_STRONG, max_iterations=1)
    assert len(client.sent_requests) == 2   # capped by max open positions


def test_daily_cap_blocks_burst(tmp_path):
    db = str(tmp_path / "j.db")
    journal = Journal(db)
    for _ in range(10):
        journal.log_order("GBPUSD", "BUY", 0.09, None, None, None, 1, "FILLED", "x")
    client = _client()
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, max_trades_per_day=10),
        client=client, journal=journal, strategy_fn=_STRONG, max_iterations=1)
    assert client.sent_requests == []


def test_opposite_direction_allowed(tmp_path):
    db = str(tmp_path / "j.db")
    open_sell = make_position(ticket=1, ptype=FakeMT5Client.POSITION_TYPE_SELL)
    client = _client(positions=[open_sell])
    run(settings=make_settings(mode=Mode.AUTO, db_path=db),
        client=client, journal=Journal(db), strategy_fn=_WEAK, max_iterations=1)
    assert len(client.sent_requests) == 1
    assert client.sent_requests[0]["type"] == client.ORDER_TYPE_BUY


def test_pick_primary_skips_symbols_without_data(monkeypatch):
    """The dashboard primary falls through to the first symbol WITH bars."""
    from mt5_ai_bridge import app
    from tests.fakes import make_settings

    settings = make_settings(symbol="GBPUSD",
                             symbols=("GBPUSD", "USDJPY", "XAUUSD"))

    # GBPUSD has no data (broker name mismatch); USDJPY is the first with bars.
    def fake_snapshot(client, symbol, timeframe, atr_period):
        return None if symbol == "GBPUSD" else {"symbol": symbol}

    monkeypatch.setattr(app, "market_snapshot", fake_snapshot)
    assert app._pick_primary(object(), settings) == "USDJPY"


def test_pick_primary_defaults_to_first_when_none_have_data(monkeypatch):
    from mt5_ai_bridge import app
    from tests.fakes import make_settings
    settings = make_settings(symbol="GBPUSD", symbols=("GBPUSD", "USDJPY"))
    monkeypatch.setattr(app, "market_snapshot",
                        lambda *a, **k: None)
    assert app._pick_primary(object(), settings) == "GBPUSD"


def test_engine_breakdown_covers_every_symbol(monkeypatch):
    """_engine_breakdown returns one read per configured symbol."""
    from mt5_ai_bridge import app
    from mt5_ai_bridge.enums import Signal
    from mt5_ai_bridge.strategy import Decision
    from tests.fakes import make_settings

    settings = make_settings(symbol="USDJPY",
                             symbols=("USDJPY", "XAUUSD"), multi_book=True)
    monkeypatch.setattr(app, "market_snapshot",
                        lambda *a, **k: {"close": 1.0})
    monkeypatch.setattr(app, "explain_market", lambda snap: "because reasons")
    rows = app._engine_breakdown(object(), settings,
                                 lambda _m: Decision(Signal.WAIT, "wait", 0.5))
    assert [r["symbol"] for r in rows] == ["USDJPY", "XAUUSD"]
    # Each row exposes both engines with a reason (the decision process).
    for r in rows:
        names = {e["name"] for e in r["engines"]}
        assert names == {"Intraday", "Swing"}
        assert all(e["reason"] for e in r["engines"])


def test_engine_breakdown_marks_enabled_disabled_and_risk(monkeypatch):
    """Each engine carries enabled + its per-symbol risk; disabled = risk 0."""
    from mt5_ai_bridge import app
    from mt5_ai_bridge.enums import Signal
    from mt5_ai_bridge.strategy import Decision
    from tests.fakes import make_settings

    settings = make_settings(
        symbol="GBPUSD", symbols=("GBPUSD", "AUDUSD"), multi_book=True,
        swing_risk_percent=1.05, intraday_risk_percent=0.11,
        swing_risk_overrides=(("AUDUSD", 0.0),),          # swing disabled
        intraday_risk_overrides=(("AUDUSD", 0.30),))
    monkeypatch.setattr(app, "market_snapshot", lambda *a, **k: {"close": 1.0})
    monkeypatch.setattr(app, "explain_market", lambda snap: "why")
    rows = {r["symbol"]: r for r in app._engine_breakdown(
        object(), settings, lambda _m: Decision(Signal.WAIT, "wait", 0.5))}

    gbp = {e["name"]: e for e in rows["GBPUSD"]["engines"]}
    assert gbp["Swing"]["enabled"] and gbp["Swing"]["risk"] == 1.05
    assert gbp["Intraday"]["enabled"] and gbp["Intraday"]["risk"] == 0.11
    assert rows["GBPUSD"]["trades"] == ["Intraday", "Swing"]

    aud = {e["name"]: e for e in rows["AUDUSD"]["engines"]}
    assert aud["Swing"]["enabled"] is False and aud["Swing"]["risk"] == 0.0
    assert aud["Intraday"]["enabled"] and aud["Intraday"]["risk"] == 0.30
    # A disabled engine never reports ready, and is excluded from 'trades'.
    assert aud["Swing"]["ready"] is False
    assert rows["AUDUSD"]["trades"] == ["Intraday"]
