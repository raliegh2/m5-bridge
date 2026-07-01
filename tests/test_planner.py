from datetime import datetime, timezone

from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.planner import (SessionConfig, SizingConfig, StaggerConfig,
                                   StyleConfig, build_plan, choose_style,
                                   is_ny_session, position_size, stagger)
from mt5_ai_bridge.strategy import Decision


def _at(hour):
    return datetime(2026, 6, 29, hour, 0, tzinfo=timezone.utc)


def test_ny_session_window():
    cfg = SessionConfig(ny_start_hour=12, ny_end_hour=21)
    assert not is_ny_session(_at(11), cfg)
    assert is_ny_session(_at(12), cfg)
    assert is_ny_session(_at(20), cfg)
    assert not is_ny_session(_at(21), cfg)


def test_position_size_doubles_in_ny():
    cfg = SizingConfig(base_lot=0.09, ny_multiplier=2.0)
    assert position_size(False, cfg) == 0.09
    assert position_size(True, cfg) == 0.18


def test_position_size_respects_min_lot():
    cfg = SizingConfig(base_lot=0.0, ny_multiplier=2.0, min_lot=0.01)
    assert position_size(False, cfg) == 0.01


def test_choose_style_by_confidence():
    cfg = StyleConfig(swing_confidence=0.7)
    assert choose_style(0.5, cfg)[0] == "intraday"
    assert choose_style(0.7, cfg)[0] == "swing"
    assert choose_style(0.9, cfg) == ("swing", 80, 160)


def test_stagger_levels_ladder_sl_and_tp():
    cfg = StaggerConfig(tp_step=0.5, sl_step=0.25, sl_floor=10)
    assert stagger(80, 160, 1, cfg) == (80, 160)     # base unchanged
    assert stagger(80, 160, 2, cfg) == (60, 240)     # tighter SL, wider TP
    assert stagger(80, 160, 3, cfg) == (40, 320)


def test_stagger_respects_sl_floor():
    cfg = StaggerConfig(tp_step=0.5, sl_step=0.5, sl_floor=15)
    # level 3 would be 80*(1-1.0)=0 -> floored at 15
    assert stagger(80, 160, 3, cfg)[0] == 15


def test_build_plan_strong_trend_ny_is_heavy_swing():
    plan = build_plan(
        Decision(Signal.BUY, "strong", 0.85), _at(14),
        SessionConfig(12, 21), SizingConfig(base_lot=0.09, ny_multiplier=2.0),
        StyleConfig(swing_confidence=0.7),
    )
    assert plan.side is Signal.BUY
    assert plan.volume == 0.18
    assert plan.style == "swing"
    assert plan.session == "NY"
    assert plan.level == 1
    assert plan.sl_pips == 80 and plan.tp_pips == 160


def test_build_plan_applies_stagger_at_higher_levels():
    plan = build_plan(
        Decision(Signal.BUY, "strong", 0.85), _at(14),
        SessionConfig(12, 21), SizingConfig(base_lot=0.09),
        StyleConfig(swing_confidence=0.7), level=3,
        stagger_cfg=StaggerConfig(tp_step=0.5, sl_step=0.25, sl_floor=10),
    )
    assert plan.level == 3
    assert plan.sl_pips == 40 and plan.tp_pips == 320


def test_build_plan_none_on_wait():
    assert build_plan(Decision(Signal.WAIT, "no setup", 0.3), _at(14)) is None
