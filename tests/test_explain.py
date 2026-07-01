from mt5_ai_bridge.explain import explain_market


def test_explanation_uses_only_available_indicator_values():
    reason = explain_market({
        "close": 1.10, "ema_50": 1.12, "ema_200": 1.15,
        "macd": -0.01, "macd_signal": -0.005, "macd_hist": -0.005,
    })
    assert "price below EMA 200" in reason
    assert "EMA 50 below EMA 200" in reason
    assert "MACD below zero" in reason
    assert "bearish momentum" in reason
    assert "RSI" not in reason


def test_explanation_has_clear_fallback_when_data_is_missing():
    assert explain_market(None).startswith("Reason unavailable")
    assert explain_market({}).startswith("Reason unavailable")
