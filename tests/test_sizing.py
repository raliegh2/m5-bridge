"""ATR indicator + ATR stops + fixed-fractional position sizing."""

import pandas as pd

from mt5_ai_bridge.indicators import atr
from mt5_ai_bridge.sizing import AtrConfig, RiskConfig, atr_stops, risk_lot


def test_atr_indicator_positive():
    df = pd.DataFrame({
        "high": [1.21, 1.22, 1.23, 1.24, 1.25],
        "low": [1.19, 1.20, 1.21, 1.22, 1.23],
        "close": [1.20, 1.21, 1.22, 1.23, 1.24],
    })
    a = atr(df, period=3)
    assert a.iloc[-1] > 0


def test_atr_stops_scale_with_multiplier():
    cfg = AtrConfig(sl_mult=2.0, tp_mult=4.0, min_sl_pips=8, max_sl_pips=200)
    # ATR 0.0030 price, pip 0.0001 -> 30 pips; SL 60, TP 120
    assert atr_stops(0.0030, 0.0001, cfg) == (60.0, 120.0)


def test_atr_stops_respects_floor_and_cap():
    cfg = AtrConfig(sl_mult=2.0, min_sl_pips=8, max_sl_pips=50)
    assert atr_stops(0.0002, 0.0001, cfg)[0] == 8.0     # 4 -> floored to 8
    assert atr_stops(0.0100, 0.0001, cfg)[0] == 50.0    # 200 -> capped at 50


def test_atr_stops_none_when_missing():
    assert atr_stops(None, 0.0001, AtrConfig()) is None
    assert atr_stops(float("nan"), 0.0001, AtrConfig()) is None


def test_risk_lot_scales_to_stop():
    cfg = RiskConfig(risk_percent=1.0, pip_value_per_lot=10.0, min_lot=0.01,
                     max_lot=5.0, lot_step=0.01)
    # balance 10000, 1% = $100 risk; 50-pip stop -> 100/(50*10) = 0.2 lots
    assert risk_lot(10000, 50, cfg) == 0.2
    # tighter stop -> bigger lot, same $ risk
    assert risk_lot(10000, 20, cfg) == 0.5


def test_risk_lot_caps_and_floors():
    cfg = RiskConfig(risk_percent=5.0, pip_value_per_lot=10.0, min_lot=0.01,
                     max_lot=1.0)
    assert risk_lot(10000, 10, cfg) == 1.0            # would be 5.0 -> capped
    assert risk_lot(500, 50, RiskConfig(risk_percent=0.1, min_lot=0.01)) == 0.01
