import pandas as pd

from mt5_ai_bridge.backtest import Backtester
from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.reasoning import (ReasoningConfig, ReasoningStrategy, reason,
                                     score)


def _bull(rsi=60):
    return {"close": 1.310, "ema_9": 1.305, "ema_20": 1.300, "ema_50": 1.290,
            "ema_200": 1.280, "rsi_14": rsi, "macd": 0.5, "macd_signal": 0.2,
            "macd_hist": 0.3}


def _bear(rsi=40):
    return {"close": 1.270, "ema_9": 1.275, "ema_20": 1.280, "ema_50": 1.290,
            "ema_200": 1.300, "rsi_14": rsi, "macd": -0.5, "macd_signal": -0.2,
            "macd_hist": -0.3}


def _mixed():
    # bull: trend_fast + price + rsi = 3.0 ; bear: trend_slow + macd + hist = 2.5
    # bull_conf = 3.0 / 5.5 = 0.545 -> below default 0.6 threshold
    return {"close": 1.305, "ema_9": 1.30, "ema_20": 1.300, "ema_50": 1.290,
            "ema_200": 1.310, "rsi_14": 60, "macd": 0.1, "macd_signal": 0.2,
            "macd_hist": -0.1}


def test_full_bull_scores_one():
    s = score(_bull())
    assert s.bull_conf == 1.0
    assert s.bear_conf == 0.0


def test_strong_bull_returns_buy():
    d = reason(_bull())
    assert d.signal is Signal.BUY
    assert d.confidence == 1.0


def test_strong_bear_returns_sell():
    d = reason(_bear())
    assert d.signal is Signal.SELL
    assert d.confidence == 1.0


def test_overbought_vetoes_buy():
    d = reason(_bull(rsi=80))
    assert d.signal is Signal.WAIT
    assert "veto buy" in d.reason.lower()


def test_oversold_vetoes_sell():
    d = reason(_bear(rsi=20))
    assert d.signal is Signal.WAIT
    assert "veto sell" in d.reason.lower()


def test_weak_confluence_waits():
    d = reason(_mixed())
    assert d.signal is Signal.WAIT
    assert 0.5 < d.confidence < 0.6


def test_lower_threshold_lets_weak_setup_trade():
    d = reason(_mixed(), ReasoningConfig(threshold=0.5))
    assert d.signal is Signal.BUY


def test_none_market_waits():
    d = reason(None)
    assert d.signal is Signal.WAIT
    assert d.confidence == 0.0


def test_strategy_is_callable_and_unpacks():
    strat = ReasoningStrategy()
    d = strat(_bull())
    signal, reason_text = d
    assert signal is Signal.BUY
    assert isinstance(reason_text, str)


def test_reasoning_is_drop_in_for_backtester():
    closes = [1.2000 + i * 0.0005 for i in range(120)]
    df = pd.DataFrame({
        "time": range(120),
        "open": closes,
        "high": [c + 0.0006 for c in closes],
        "low": [c - 0.0006 for c in closes],
        "close": closes,
    })
    result = Backtester(strategy_fn=ReasoningStrategy()).run(df)
    assert len(result.equity_curve) == 120
    assert isinstance(result.summary(), dict)
