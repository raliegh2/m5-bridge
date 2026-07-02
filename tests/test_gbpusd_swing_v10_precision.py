from mt5_ai_bridge.gbpusd_swing_v10_precision import evaluate_swing_timing


def test_primary_breakout_uses_quality_risk_tiers():
    strong = evaluate_swing_timing(
        setup="GBPUSD_V4_PRIMARY_16UTC_BREAKOUT",
        side=1,
        open_price=1.2500,
        close_price=1.2600,
        atr14=0.0050,
        volume_ratio=1.40,
        range_atr=1.80,
        atr_ratio=1.10,
        ema20_h4=1.2550,
        ema50_h4=1.2500,
    )
    ordinary = evaluate_swing_timing(
        setup="PRIMARY_16UTC_BREAKOUT",
        side=1,
        open_price=1.2500,
        close_price=1.2550,
        atr14=0.0050,
        volume_ratio=1.10,
        range_atr=1.20,
        atr_ratio=1.05,
        ema20_h4=1.2530,
        ema50_h4=1.2500,
    )
    assert strong.allowed and strong.grade == "A"
    assert strong.risk_percent == 0.50
    assert ordinary.allowed and ordinary.grade == "B"
    assert ordinary.risk_percent == 0.20


def test_secondary_rejects_overextended_candle():
    decision = evaluate_swing_timing(
        setup="SECONDARY_12UTC_BREAKOUT",
        side=1,
        open_price=1.2500,
        close_price=1.2600,
        atr14=0.0050,
        volume_ratio=1.40,
        range_atr=2.10,
        atr_ratio=1.10,
        ema20_h4=1.2550,
        ema50_h4=1.2500,
    )
    assert not decision.allowed
    assert decision.risk_percent == 0.0


def test_pullback_rejects_excessive_ema_separation():
    decision = evaluate_swing_timing(
        setup="GBPUSD_SWING_V5_PULLBACK_ADDON",
        side=-1,
        open_price=1.2500,
        close_price=1.2450,
        atr14=0.0050,
        volume_ratio=1.20,
        range_atr=1.30,
        atr_ratio=1.05,
        ema20_h4=1.2450,
        ema50_h4=1.2550,
    )
    assert not decision.allowed
    assert decision.directional_ema_gap_atr == 2.0
