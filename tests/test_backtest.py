import pandas as pd
from pytest import approx

from mt5_ai_bridge.backtest import Backtester
from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.strategy import Decision

# The drifting series below produces its first valid RSI (and therefore the
# first strategy call / entry) on bar 13.
ENTRY_CLOSE = 1.2000 + 13 * 1e-5


def _buy_once():
    """Strategy stub: fire one BUY on the first eligible bar, then WAIT."""
    state = {"fired": False}

    def fn(market):
        if not state["fired"]:
            state["fired"] = True
            return Decision(Signal.BUY, "test", 1.0)
        return Decision(Signal.WAIT, "test", 0.0)

    return fn


def _drift_df(n=40, base=1.2000, step=1e-5, overrides=None):
    """n bars drifting up by `step`/bar (keeps RSI defined, not 0/0=NaN).

    overrides[i] can set {'high':..,'low':..} to force an SL/TP touch.
    """
    overrides = overrides or {}
    rows = []
    for i in range(n):
        close = base + i * step
        hi, lo = close + 0.0005, close - 0.0005
        if i in overrides:
            hi = overrides[i].get("high", hi)
            lo = overrides[i].get("low", lo)
        rows.append({"time": i, "open": close, "high": hi, "low": lo, "close": close})
    return pd.DataFrame(rows)


def test_money_and_pips_math():
    bt = Backtester(pip_size=0.0001, lot_size=0.01, contract_size=100_000)
    assert bt._money(Signal.BUY, 1.2000, 1.2060) == approx(6.0)
    assert bt._pips(Signal.BUY, 1.2000, 1.2060) == 60.0
    assert bt._money(Signal.SELL, 1.2000, 1.1940) == approx(6.0)
    assert bt._pips(Signal.SELL, 1.2000, 1.1940) == 60.0


def test_long_hits_take_profit():
    df = _drift_df(40, overrides={15: {"high": 1.2070}})
    bt = Backtester(stop_loss_pips=30, take_profit_pips=60, strategy_fn=_buy_once())
    r = bt.run(df)

    assert r.n_trades == 1
    t = r.trades[0]
    assert t.side is Signal.BUY
    assert t.exit_reason == "TP"
    assert t.exit_price == approx(ENTRY_CLOSE + 0.0060)
    assert t.profit == approx(6.0)
    assert r.final_balance == approx(10_006.0)
    assert r.win_rate == 1.0


def test_long_hits_stop_loss():
    df = _drift_df(40, overrides={15: {"low": 1.1960}})
    bt = Backtester(stop_loss_pips=30, take_profit_pips=60, strategy_fn=_buy_once())
    r = bt.run(df)

    assert r.n_trades == 1
    assert r.trades[0].exit_reason == "SL"
    assert r.trades[0].profit == approx(-3.0)
    assert r.losses == 1


def test_stop_checked_before_target_when_bar_touches_both():
    df = _drift_df(40, overrides={15: {"high": 1.2070, "low": 1.1960}})
    r = Backtester(strategy_fn=_buy_once()).run(df)
    assert r.trades[0].exit_reason == "SL"


def test_open_position_closed_at_end_of_data():
    df = _drift_df(20)  # no SL/TP ever hit -> closed at last bar
    r = Backtester(strategy_fn=_buy_once()).run(df)
    assert r.n_trades == 1
    assert r.trades[0].exit_reason == "EOD"


def test_equity_curve_length_matches_bars():
    df = _drift_df(30)
    r = Backtester(strategy_fn=_buy_once()).run(df)
    assert len(r.equity_curve) == 30


def test_default_strategy_runs_on_trend():
    closes = [1.2000 + i * 0.0005 for i in range(120)]
    df = pd.DataFrame({
        "time": range(120),
        "open": closes,
        "high": [c + 0.0006 for c in closes],
        "low": [c - 0.0006 for c in closes],
        "close": closes,
    })
    r = Backtester().run(df)
    assert len(r.equity_curve) == 120
    assert r.n_trades >= 0
    assert isinstance(r.summary(), dict)
