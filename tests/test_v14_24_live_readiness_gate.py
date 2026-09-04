from research.v14_24_live_readiness_gate import build_readiness_snapshot


def test_current_v14_24_candidate_cannot_authorize_live_execution():
    snapshot = build_readiness_snapshot()
    assert snapshot["status"] == "BLOCKED"
    assert snapshot["checks"]["execution_price_and_cost_fields_present"] is False
    assert snapshot["checks"]["gold_strategy_generator_present"] is False
    assert snapshot["checks"]["fresh_untouched_forward_trades_present"] is False
    assert snapshot["checks"]["intratrade_drawdown_available"] is False
    assert snapshot["checks"]["recent_extra_005r_profitable"] is False
