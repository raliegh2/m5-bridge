from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.strategy import evaluate_strategy


def _market(ema20, ema50, close, rsi, macd, macd_signal):
    return {
        "ema_20": ema20, "ema_50": ema50, "close": close,
        "rsi_14": rsi, "macd": macd, "macd_signal": macd_signal,
    }


def test_bullish_setup_returns_buy():
    d = evaluate_strategy(_market(1.30, 1.29, 1.31, 60, 0.5, 0.2))
    assert d.signal is Signal.BUY
    assert d.confidence == 1.0


def test_bearish_setup_returns_sell():
    d = evaluate_strategy(_market(1.28, 1.29, 1.27, 40, -0.5, -0.2))
    assert d.signal is Signal.SELL


def test_no_setup_returns_wait_with_partial_confidence():
    d = evaluate_strategy(_market(1.30, 1.29, 1.31, 50, 0.5, 0.2))
    assert d.signal is Signal.WAIT
    assert 0.0 < d.confidence < 1.0


def test_none_market_returns_wait():
    d = evaluate_strategy(None)
    assert d.signal is Signal.WAIT
    assert d.confidence == 0.0


def test_decision_unpacks_to_signal_reason():
    signal, reason = evaluate_strategy(_market(1.30, 1.29, 1.31, 60, 0.5, 0.2))
    assert signal is Signal.BUY
    assert isinstance(reason, str)
