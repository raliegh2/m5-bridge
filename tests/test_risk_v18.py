"""Risk engine: Kelly sizing, drawdown taper, kill switches, budgets."""

import pytest
from pytest import approx

from mt5_ai_bridge.risk_v18 import (DrawdownGovernor, KillSwitch, RiskBudget,
                                    RiskEngine, edge_from_trades,
                                    exposure_groups, fractional_kelly,
                                    kelly_fraction, volatility_target_lots)


# --- Kelly ------------------------------------------------------------------


def test_kelly_matches_the_textbook_formula():
    # p=0.6, b=1 -> f* = (0.6*1 - 0.4)/1 = 0.2
    assert kelly_fraction(0.6, 1.0) == approx(0.2)
    # p=0.5, b=2 -> f* = (0.5*2 - 0.5)/2 = 0.25
    assert kelly_fraction(0.5, 2.0) == approx(0.25)


def test_kelly_is_zero_for_a_break_even_bet():
    assert kelly_fraction(0.5, 1.0) == 0.0


def test_negative_edge_clamps_to_zero_not_a_reversed_bet():
    """A negative Kelly means take the other side, not size smaller."""
    assert kelly_fraction(0.3, 1.0) == 0.0
    assert kelly_fraction(0.4, 0.5) == 0.0


def test_kelly_rises_with_edge():
    assert kelly_fraction(0.7, 1.0) > kelly_fraction(0.6, 1.0)
    assert kelly_fraction(0.6, 3.0) > kelly_fraction(0.6, 1.0)


def test_kelly_rejects_impossible_inputs():
    with pytest.raises(ValueError):
        kelly_fraction(1.5, 1.0)
    with pytest.raises(ValueError):
        kelly_fraction(-0.1, 1.0)
    with pytest.raises(ValueError):
        kelly_fraction(0.6, 0.0)


def test_fractional_kelly_scales_and_caps():
    assert fractional_kelly(0.20, 0.25, cap=1.0) == approx(0.05)
    assert fractional_kelly(0.80, 0.25, cap=0.02) == approx(0.02)
    assert fractional_kelly(0.0) == 0.0


def test_fractional_kelly_rejects_bad_arguments():
    with pytest.raises(ValueError):
        fractional_kelly(0.2, fraction=0.0)
    with pytest.raises(ValueError):
        fractional_kelly(0.2, cap=0.0)


# --- edge measurement -------------------------------------------------------


def test_edge_from_a_winning_series():
    profits = [2.0, 2.0, 2.0, -1.0, -1.0]      # 60% wins, 2:1 payoff
    e = edge_from_trades(profits)
    assert e["trades"] == 5
    assert e["win_rate"] == approx(0.6)
    assert e["win_loss_ratio"] == approx(2.0)
    assert e["kelly"] == approx(0.4)
    assert e["expectancy"] == approx(0.8)


def test_edge_from_a_losing_series_is_zero_kelly():
    e = edge_from_trades([1.0, -2.0, -2.0, 1.0, -2.0])
    assert e["kelly"] == 0.0
    assert e["expectancy"] < 0


def test_unmeasurable_edge_reports_none_not_infinite():
    assert edge_from_trades([])["kelly"] == 0.0
    assert edge_from_trades([1.0, 2.0])["kelly"] == 0.0     # no losses
    assert edge_from_trades([-1.0, -2.0])["kelly"] == 0.0   # no wins


# --- volatility targeting ---------------------------------------------------


def test_lots_deliver_the_requested_risk():
    # 1% of 10,000 = $100 risk over a 50-pip stop at $10/pip = 0.2 lots
    lots = volatility_target_lots(10_000, 0.01, 0.0050, 0.0001, 10.0)
    assert lots == approx(0.20)


def test_a_wider_stop_gives_a_smaller_position():
    tight = volatility_target_lots(10_000, 0.01, 0.0025, 0.0001, 10.0)
    wide = volatility_target_lots(10_000, 0.01, 0.0100, 0.0001, 10.0)
    assert wide < tight


def test_sub_minimum_size_returns_zero_not_the_minimum():
    """Rounding up a tiny position silently blows the risk budget."""
    lots = volatility_target_lots(100, 0.001, 0.0500, 0.0001, 10.0)
    assert lots == 0.0


def test_zero_or_invalid_inputs_produce_no_position():
    assert volatility_target_lots(0, 0.01, 0.005, 0.0001, 10.0) == 0.0
    assert volatility_target_lots(10_000, 0.0, 0.005, 0.0001, 10.0) == 0.0
    assert volatility_target_lots(10_000, 0.01, 0.0, 0.0001, 10.0) == 0.0
    assert volatility_target_lots(10_000, 0.01, 0.005, 0.0, 10.0) == 0.0


def test_max_lot_is_respected():
    lots = volatility_target_lots(10_000_000, 0.02, 0.0005, 0.0001, 10.0,
                                  max_lot=5.0)
    assert lots == approx(5.0)


# --- drawdown governor ------------------------------------------------------


def test_no_taper_above_the_soft_limit():
    g = DrawdownGovernor(soft_limit=0.05, hard_limit=0.20)
    g.observe(10_000)
    assert g.multiplier(10_000) == 1.0
    assert g.multiplier(9_600) == 1.0        # 4% drawdown


def test_taper_is_monotonic_between_the_limits():
    g = DrawdownGovernor(soft_limit=0.05, hard_limit=0.20, floor=0.25)
    g.observe(10_000)
    m8 = g.multiplier(9_200)     # 8% dd
    m15 = g.multiplier(8_500)    # 15% dd
    assert 0.25 <= m15 < m8 < 1.0


def test_hard_limit_stops_trading():
    g = DrawdownGovernor(soft_limit=0.05, hard_limit=0.20)
    g.observe(10_000)
    assert g.multiplier(8_000) == 0.0
    assert g.multiplier(7_000) == 0.0


def test_peak_is_a_high_water_mark():
    g = DrawdownGovernor()
    g.observe(10_000)
    g.observe(12_000)
    g.observe(11_000)
    assert g.peak_equity == 12_000
    assert g.drawdown(11_000) == approx(1 / 12)


def test_governor_rejects_incoherent_limits():
    with pytest.raises(ValueError):
        DrawdownGovernor(soft_limit=0.3, hard_limit=0.2)
    with pytest.raises(ValueError):
        DrawdownGovernor(floor=0.0)


# --- kill switches ----------------------------------------------------------


def test_daily_loss_limit_trips():
    ks = KillSwitch(max_daily_loss_fraction=0.02)
    ks.start_day("2026-08-16", 10_000)
    ok, _ = ks.check(9_900, 10_000)
    assert ok
    ok, why = ks.check(9_790, 10_000)     # -2.1%
    assert not ok and "daily loss" in why


def test_total_drawdown_limit_trips_and_latches():
    ks = KillSwitch(max_total_drawdown_fraction=0.20)
    ks.start_day("2026-08-16", 10_000)
    ok, why = ks.check(7_900, 10_000)
    assert not ok and "total drawdown" in why
    # Latched: recovering equity does not re-enable trading.
    assert ks.check(9_999, 10_000)[0] is False


def test_a_new_day_clears_a_daily_trip_but_not_a_drawdown_trip():
    ks = KillSwitch(max_daily_loss_fraction=0.02,
                    max_total_drawdown_fraction=0.20)
    ks.start_day("2026-08-16", 10_000)
    ks.check(9_700, 10_000)
    assert ks.state.tripped
    ks.start_day("2026-08-17", 9_700)
    assert ks.check(9_700, 10_000)[0] is True

    ks2 = KillSwitch(max_total_drawdown_fraction=0.20)
    ks2.start_day("2026-08-16", 10_000)
    ks2.check(7_000, 10_000)
    ks2.start_day("2026-08-17", 7_000)
    assert ks2.check(7_000, 10_000)[0] is False


def test_consecutive_losses_stop_trading_and_reset_on_a_win():
    ks = KillSwitch(max_consecutive_losses=3)
    ks.start_day("2026-08-16", 10_000)
    for _ in range(3):
        ks.record_trade(-10.0)
    ok, why = ks.check(9_970, 10_000)
    assert not ok and "consecutive losses" in why
    ks.record_trade(+5.0)
    assert ks.check(9_975, 10_000)[0] is True


def test_daily_trade_cap():
    ks = KillSwitch(max_trades_per_day=2)
    ks.start_day("2026-08-16", 10_000)
    ks.record_trade(1.0)
    ks.record_trade(1.0)
    ok, why = ks.check(10_002, 10_002)
    assert not ok and "trades today" in why


def test_kill_switch_rejects_bad_configuration():
    with pytest.raises(ValueError):
        KillSwitch(max_daily_loss_fraction=0.0)
    with pytest.raises(ValueError):
        KillSwitch(max_total_drawdown_fraction=1.0)
    with pytest.raises(ValueError):
        KillSwitch(max_consecutive_losses=0)


# --- budgets ----------------------------------------------------------------


def test_per_symbol_cap_applies():
    b = RiskBudget(max_symbol_risk_fraction=0.01)
    allowed, why = b.room_for("EURUSD", 0.02, {})
    assert allowed == approx(0.01)
    assert "per symbol" in why


def test_aggregate_ceiling_limits_the_next_trade():
    b = RiskBudget(max_total_risk_fraction=0.03,
                   max_symbol_risk_fraction=0.02,
                   max_currency_risk_fraction=0.99)
    allowed, why = b.room_for("EURUSD", 0.02, {"GBPJPY": 0.02})
    assert allowed == approx(0.01)
    assert "aggregate headroom" in why


def test_currency_cap_catches_correlated_usd_exposure():
    """Three long-USD positions are one bet; the cap must see that."""
    b = RiskBudget(max_currency_risk_fraction=0.02,
                   max_total_risk_fraction=0.99,
                   max_symbol_risk_fraction=0.02)
    allowed, why = b.room_for("EURUSD", 0.02,
                              {"GBPUSD": 0.01, "AUDUSD": 0.01})
    assert allowed == 0.0
    assert "USD" in why


def test_concurrency_cap():
    b = RiskBudget(max_concurrent_positions=2)
    allowed, why = b.room_for("EURUSD", 0.01,
                              {"GBPUSD": 0.01, "AUDUSD": 0.01})
    assert allowed == 0.0
    assert "positions open" in why


def test_budget_rejects_incoherent_configuration():
    with pytest.raises(ValueError):
        RiskBudget(max_symbol_risk_fraction=0.10,
                   max_total_risk_fraction=0.05)
    with pytest.raises(ValueError):
        RiskBudget(max_concurrent_positions=0)


# --- the engine -------------------------------------------------------------


def _winning_edge():
    return edge_from_trades([2.0, 2.0, 2.0, -1.0, -1.0])


def _kw(**over):
    base = dict(symbol="EURUSD", balance=10_000.0, equity=10_000.0,
                stop_distance=0.0050, pip=0.0001, pip_value_per_lot=10.0,
                edge=_winning_edge())
    base.update(over)
    return base


def test_engine_sizes_a_validated_edge():
    engine = RiskEngine()
    engine.governor.observe(10_000)
    d = engine.size(**_kw())
    assert d.approved
    assert d.lots > 0
    assert 0 < d.risk_fraction <= engine.max_risk_per_trade


def test_engine_refuses_a_signal_with_no_measured_edge():
    """The central rule: no edge means no position, not a small one."""
    engine = RiskEngine()
    engine.governor.observe(10_000)
    d = engine.size(**_kw(edge=edge_from_trades([1.0, -2.0, -2.0])))
    assert not d.approved
    assert d.lots == 0.0
    assert "no measured edge" in d.reason


def test_engine_refuses_an_empty_track_record():
    engine = RiskEngine()
    engine.governor.observe(10_000)
    d = engine.size(**_kw(edge=edge_from_trades([])))
    assert not d.approved
    assert d.lots == 0.0


def test_engine_shrinks_size_in_a_drawdown():
    engine = RiskEngine()
    engine.governor.observe(10_000)
    full = engine.size(**_kw())
    drawn = engine.size(**_kw(equity=8_800, balance=8_800))   # 12% dd
    assert drawn.approved
    assert drawn.risk_fraction < full.risk_fraction


def test_engine_stops_at_the_drawdown_hard_limit():
    engine = RiskEngine()
    engine.governor.observe(10_000)
    d = engine.size(**_kw(equity=7_500, balance=7_500))
    assert not d.approved
    assert "drawdown" in d.reason


def test_engine_respects_the_kill_switch():
    engine = RiskEngine()
    engine.governor.observe(10_000)
    engine.kill_switch.start_day("2026-08-16", 10_000)
    for _ in range(engine.kill_switch.max_consecutive_losses):
        engine.kill_switch.record_trade(-5.0)
    d = engine.size(**_kw())
    assert not d.approved
    assert "kill switch" in d.reason


def test_engine_respects_open_exposure():
    engine = RiskEngine(budget=RiskBudget(max_total_risk_fraction=0.02,
                                          max_symbol_risk_fraction=0.02,
                                          max_currency_risk_fraction=0.99))
    engine.governor.observe(10_000)
    d = engine.size(**_kw(open_risk={"GBPJPY": 0.02}))
    assert not d.approved
    assert "risk budget" in d.reason


def test_engine_refuses_when_the_broker_minimum_exceeds_the_budget():
    """A small account cannot trade an instrument whose minimum is too big.

    Sizing up to the minimum instead would breach the risk budget on every
    trade, which is how a computed 10% drawdown ceiling becomes a real 17% one.
    """
    engine = RiskEngine()
    engine.governor.observe(500)
    d = engine.size(**_kw(balance=500, equity=500, stop_distance=0.0500))
    assert not d.approved
    assert d.lots == 0.0
    assert "broker minimum" in d.reason
    assert "no tradeable size" in d.reason
    # The detail should quantify by how much, so the refusal is actionable.
    assert d.detail["min_lot_risk"] > d.detail["risk_budget_money"]


def test_stronger_edge_earns_a_larger_position():
    engine = RiskEngine()
    engine.governor.observe(10_000)
    # 52% wins at 1:1 -> Kelly 0.04 -> quarter Kelly 0.01, under the 2% cap.
    weak_edge = edge_from_trades([1.0] * 13 + [-1.0] * 12)
    # 60% wins at 2:1 -> Kelly 0.40 -> quarter Kelly 0.10, above the cap.
    strong_edge = edge_from_trades([2.0] * 6 + [-1.0] * 4)

    weak = engine.size(**_kw(edge=weak_edge))
    strong = engine.size(**_kw(symbol="GBPUSD", edge=strong_edge))
    assert weak.risk_fraction == approx(0.01)
    assert strong.risk_fraction > weak.risk_fraction


def test_the_per_trade_cap_binds_however_large_the_edge():
    """A small sample can imply an absurd Kelly; the cap is the backstop."""
    engine = RiskEngine(max_risk_per_trade=0.02)
    engine.governor.observe(10_000)
    absurd = edge_from_trades([50.0] * 19 + [-1.0])
    d = engine.size(**_kw(edge=absurd))
    assert d.approved
    assert d.risk_fraction == approx(0.02)


# --- exposure grouping ------------------------------------------------------


def test_currency_pairs_still_split_into_base_and_quote():
    assert exposure_groups("EURUSD") == ("EUR", "USD")
    assert exposure_groups("XAUUSD") == ("XAU", "USD")
    assert exposure_groups("gbpjpy") == ("GBP", "JPY")


def test_equity_tickers_are_not_sliced_like_currencies():
    # AAPL would otherwise become base "AAP" and quote "L", and META would
    # share a budget with MetLife because both slice to "MET".
    assert exposure_groups("AAPL") == ("US_EQUITY", "AAPL")
    assert exposure_groups("META") == ("US_EQUITY", "META")
    assert exposure_groups("MET") == ("US_EQUITY", "MET")


def test_two_currency_pairs_sharing_a_leg_still_compete_for_that_budget():
    budget = RiskBudget()
    allowed, why = budget.room_for("GBPUSD", 0.02, {"EURUSD": 0.04})

    assert allowed == 0.0
    assert "USD" in why


def test_equities_share_the_market_factor_rather_than_a_letter_prefix():
    budget = RiskBudget()
    # Correlated by being US equities, not by spelling.
    allowed, why = budget.room_for("AAPL", 0.02, {"XOM": 0.04})

    assert allowed == 0.0
    assert "US_EQUITY" in why


def test_an_equity_does_not_consume_a_currency_budget():
    budget = RiskBudget()
    allowed, _ = budget.room_for("EURUSD", 0.02, {"AAPL": 0.04})

    assert allowed > 0.0
