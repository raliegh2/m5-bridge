"""Prop-firm guard: limits, de-risking, target, persistence."""

from mt5_ai_bridge.prop import PropConfig, PropGuard


def _cfg(tmp_path, **kw):
    base = dict(enabled=True, start_balance=5000.0, max_daily_loss_pct=5.0,
                max_total_loss_pct=10.0, profit_target_pct=8.0,
                state_path=str(tmp_path / "prop.json"))
    base.update(kw)
    return PropConfig(**base)


def test_full_risk_when_flat(tmp_path):
    g = PropGuard(_cfg(tmp_path))
    s = g.update(5000.0, 5000.0, today="2026-01-01")
    assert s["allow_trading"] and s["risk_scale"] == 1.0 and s["status"] == "TRADING"


def test_derisk_scales_down_near_daily_limit(tmp_path):
    g = PropGuard(_cfg(tmp_path))  # 5% daily = $250; derisk at 60% -> $150
    g.update(5000.0, 5000.0, today="2026-01-01")              # set the day baseline
    s = g.update(5000.0, 5000.0 - 200.0, today="2026-01-01")  # 4% loss = 80% used
    assert 0.0 < s["risk_scale"] < 1.0
    assert s["status"] == "DE-RISKED"


def test_daily_limit_blocks(tmp_path):
    g = PropGuard(_cfg(tmp_path))
    g.update(5000.0, 5000.0, today="2026-01-01")              # set the day baseline
    s = g.update(5000.0, 5000.0 - 260.0, today="2026-01-01")  # >5%
    assert not s["allow_trading"] and s["risk_scale"] == 0.0
    assert s["status"] == "DAILY LIMIT"


def test_total_drawdown_blocks(tmp_path):
    g = PropGuard(_cfg(tmp_path))
    s = g.update(5000.0, 5000.0 - 520.0, today="2026-01-01")  # >10%
    assert not s["allow_trading"] and s["status"] == "MAX DRAWDOWN"


def test_profit_target_locks_in(tmp_path):
    g = PropGuard(_cfg(tmp_path))
    s = g.update(5000.0, 5000.0 * 1.09, today="2026-01-01")  # +9% > 8% target
    assert not s["allow_trading"] and s["status"] == "TARGET HIT"


def test_trailing_drawdown_from_peak(tmp_path):
    g = PropGuard(_cfg(tmp_path, trailing=True))
    g.update(5000.0, 5400.0, today="2026-01-01")            # peak 5400
    s = g.update(5000.0, 5400.0 - 520.0, today="2026-01-01")  # -520 from peak = >10% of 5000
    assert not s["allow_trading"] and s["status"] == "MAX DRAWDOWN"


def test_state_persists_across_restart(tmp_path):
    cfg = _cfg(tmp_path, start_balance=0.0)   # auto-capture
    g = PropGuard(cfg)
    g.update(5000.0, 5200.0, today="2026-01-01")   # captures start 5000, peak 5200
    g2 = PropGuard(cfg)                             # reload
    assert g2.start_balance == 5000.0 and g2.peak_equity == 5200.0
