"""Efficiency-Ratio regime router: pure classifier + config + live gate."""

from mt5_ai_bridge import app, regime
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.strategy import Decision
from tests.fakes import (FakeMT5Client, make_account, make_order_result,
                         make_settings, make_symbol_info, make_tick)


def test_efficiency_ratio_trend_vs_chop():
    trend = list(range(0, 42, 2))                 # perfectly directional
    assert regime.efficiency_ratio(trend, 20) == 1.0
    chop = [0, 1] * 21                            # pure oscillation
    assert regime.efficiency_ratio(chop, 20) < 0.1
    assert regime.efficiency_ratio([1, 2, 3], 20) is None   # too few points


def test_classify_and_trend_allowed():
    assert regime.classify(0.5) == "directional"
    assert regime.classify(0.10) == "range"
    assert regime.classify(0.27) == "unclear"
    assert regime.classify(None) == "unknown"
    assert regime.trend_allowed(0.4, 0.3) is True
    assert regime.trend_allowed(0.1, 0.3) is False
    assert regime.trend_allowed(None, 0.3) is True   # unknown never blocks


def test_regime_er_min_override(monkeypatch):
    monkeypatch.setenv("REGIME_ER_MIN", "0.30")
    monkeypatch.setenv("REGIME_ER_MIN_XAUUSD", "0.40")
    s = load_settings(dotenv=False)
    assert s.regime_er_min_for("XAUUSD") == 0.40
    assert s.regime_er_min_for("GBPUSD") == 0.30


def test_breakdown_holds_engines_in_range_regime(monkeypatch):
    settings = make_settings(symbol="XAUUSD", symbols=("XAUUSD",),
                             multi_book=True, regime_filter=True,
                             regime_er_min=0.30)
    monkeypatch.setattr(app, "market_snapshot",
                        lambda *a, **k: {"close": 1.0, "er": 0.12})   # ranging
    monkeypatch.setattr(app, "explain_market", lambda snap: "why")
    r = app._engine_breakdown(object(), settings,
                              lambda m: Decision(Signal.BUY, "b", 0.9))[0]
    assert r["regime"]["state"] == "range" and r["regime"]["allowed"] is False
    assert all(e["ready"] is False for e in r["engines"])   # held aside


def _client(rates):
    return FakeMT5Client(account=make_account(balance=10000, equity=10000),
                         positions=[], tick=make_tick(),
                         symbol_info=make_symbol_info(), rates=rates,
                         order_result=make_order_result())


def test_range_regime_blocks_new_trades(tmp_path):
    from mt5_ai_bridge.app import run
    from mt5_ai_bridge.enums import Mode
    from mt5_ai_bridge.journal import Journal
    # Flat/oscillating closes -> ER ~ 0 -> range regime -> no new trades.
    rates = [{"time": 1_700_000_000 + i * 900, "open": 1.20,
              "high": 1.2005, "low": 1.1995,
              "close": 1.20 + (0.0002 if i % 2 else -0.0002),
              "tick_volume": 100} for i in range(120)]
    db = str(tmp_path / "j.db")
    client = _client(rates)
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, multi_book=True,
                               regime_filter=True, regime_er_min=0.30,
                               swing_risk_percent=1.0, intraday_risk_percent=0.5,
                               risk_based_sizing=True),
        client=client, journal=Journal(db),
        strategy_fn=lambda m: Decision(Signal.BUY, "b", 0.9), max_iterations=1)
    assert client.sent_requests == []   # range regime stood the bot aside
