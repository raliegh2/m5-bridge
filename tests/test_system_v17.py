"""The V17 VR-gated reversion system."""

import json
from dataclasses import asdict, replace

import numpy as np
import pandas as pd
import pytest
from pytest import approx

from mt5_ai_bridge.costs import ZERO_COST
from mt5_ai_bridge.instruments import Converter, cost_for
from mt5_ai_bridge.portfolio_v15 import PortfolioConfig
from mt5_ai_bridge.system_v17 import (LOCK_PATH, LOCKED_V17, SystemConfig,
                                      admit_by_persistence, locked_system,
                                      replay_system)

H4 = 14_400
START = 1_100_000_000


def _bars(closes, pad=0.0010):
    return pd.DataFrame({
        "time": [START + i * H4 for i in range(len(closes))],
        "open": closes,
        "high": [c + pad for c in closes],
        "low": [c - pad for c in closes],
        "close": closes,
    })


def _reverting(n=1500, base=1.2000, phi=0.88, sigma=0.004, seed=5):
    """Stationary: VR below 1, should pass the gate."""
    rng = np.random.default_rng(seed)
    x, out = base, []
    for _ in range(n):
        x = base + phi * (x - base) + rng.normal(0.0, sigma)
        out.append(x)
    return _bars(out)


def _trending(n=1500, base=1.2000, phi=0.35, sigma=0.002, seed=6):
    """Positively autocorrelated: VR above 1, should be refused."""
    rng = np.random.default_rng(seed)
    r, prev = [], 0.0
    for _ in range(n):
        prev = phi * prev + rng.normal(0.0, sigma)
        r.append(prev)
    return _bars(list(base + np.cumsum(r)))


# --- the lock ---------------------------------------------------------------


def test_lock_file_matches_the_code():
    assert locked_system() == LOCKED_V17


def test_lock_file_shows_its_derivation_and_expectation():
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8-sig"))
    assert payload["parameters"] == asdict(LOCKED_V17)
    for key in ("premise", "evidence", "derivation_of_entry_z",
                "derivation_of_vr_gate", "inherited_unchanged_from_v16",
                "acceptance_gates", "predeclared_expectation"):
        assert payload[key], f"lock file is missing {key}"
    # The entry threshold must be justified by arithmetic, not by a sweep.
    d = payload["derivation_of_entry_z"]
    assert "not by sweeping" in d["method"]
    assert d["result"] == "entry_z = 3.0"
    assert d["known_cost_of_this_choice"]


def test_tampered_lock_file_is_rejected(tmp_path):
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8-sig"))
    payload["parameters"]["entry_z"] = 2.5
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="disagrees with the code"):
        locked_system(path)


def test_v17_inherits_v16_parameters_unchanged():
    """Only entry_z, stop_z and the gate differ -- otherwise it is a family."""
    from mt5_ai_bridge.candidate_v16 import LOCKED_V16
    for field in ("lookback", "exit_z", "atr_period", "max_holding_bars",
                  "min_atr_pips", "risk_percent", "timeframe_minutes"):
        assert getattr(LOCKED_V17, field) == getattr(LOCKED_V16, field), field
    assert LOCKED_V17.entry_z == 3.0
    assert LOCKED_V17.stop_z == LOCKED_V17.entry_z + 2.0


# --- config validation ------------------------------------------------------


def test_gate_bounds_are_validated():
    with pytest.raises(ValueError, match="vr_horizon"):
        replace(LOCKED_V17, vr_horizon=1).validate()
    with pytest.raises(ValueError, match="vr_max"):
        replace(LOCKED_V17, vr_max=0.0).validate()
    with pytest.raises(ValueError, match="vr_max"):
        replace(LOCKED_V17, vr_max=2.0).validate()


def test_inherited_validation_still_applies():
    with pytest.raises(ValueError, match="time stop"):
        replace(LOCKED_V17, max_holding_bars=0).validate()
    with pytest.raises(ValueError, match="stop_z"):
        replace(LOCKED_V17, entry_z=3.0, stop_z=2.0).validate()


# --- the persistence gate ---------------------------------------------------


def test_gate_admits_a_mean_reverting_market():
    admitted, detail = admit_by_persistence({"EURUSD": _reverting()},
                                            LOCKED_V17)
    assert admitted == ["EURUSD"]
    assert detail["EURUSD"]["vr"] < 1.0


def test_gate_refuses_a_trending_market():
    admitted, detail = admit_by_persistence({"XAUUSD": _trending()},
                                            LOCKED_V17)
    assert admitted == []
    assert detail["XAUUSD"]["vr"] >= 1.0
    assert "VR" in detail["XAUUSD"]["reason"]


def test_gate_separates_a_mixed_universe():
    universe = {"EURUSD": _reverting(), "XAUUSD": _trending()}
    admitted, _ = admit_by_persistence(universe, LOCKED_V17)
    assert admitted == ["EURUSD"]


def test_gate_measures_only_the_training_window():
    """A fold must not admit a symbol using data it is about to be scored on."""
    reverting = _reverting(n=800, seed=1)
    trending = _trending(n=800, seed=2)
    spliced = pd.concat([trending, reverting], ignore_index=True)
    spliced["time"] = [START + i * H4 for i in range(len(spliced))]

    early, _ = admit_by_persistence({"X": spliced}, LOCKED_V17, upto=800)
    whole, _ = admit_by_persistence({"X": spliced}, LOCKED_V17)
    # The trending first half must be judged on its own merits.
    assert early == []
    assert early != whole or whole == []


def test_gate_reports_a_reason_for_short_series():
    _, detail = admit_by_persistence({"X": _bars([1.2] * 10)}, LOCKED_V17)
    assert detail["X"]["admitted"] is False
    assert detail["X"]["reason"]


# --- replay -----------------------------------------------------------------


def test_flat_market_trades_nothing():
    r = replay_system({"EURUSD": _bars([1.2000] * 400)}, LOCKED_V17)
    assert r.trades == []


def test_a_reverting_market_produces_trades():
    r = replay_system({"EURUSD": _reverting()}, LOCKED_V17)
    assert r.trades
    assert all(t.symbol == "EURUSD" for t in r.trades)


def test_admitted_subset_restricts_trading():
    universe = {"EURUSD": _reverting(seed=1), "GBPUSD": _reverting(seed=2)}
    r = replay_system(universe, LOCKED_V17, admitted=["EURUSD"])
    assert {t.symbol for t in r.trades} <= {"EURUSD"}


def test_empty_admission_trades_nothing():
    r = replay_system({"EURUSD": _reverting()}, LOCKED_V17, admitted=[])
    assert r.trades == []
    assert r.net_profit == 0.0


def test_a_wider_entry_takes_fewer_trades():
    """The frequency lever the system depends on."""
    bars = {"EURUSD": _reverting(n=3000)}
    narrow = replay_system(bars, replace(LOCKED_V17, entry_z=2.0, stop_z=4.0))
    wide = replay_system(bars, replace(LOCKED_V17, entry_z=3.0, stop_z=5.0))
    assert len(wide.trades) < len(narrow.trades)


def test_currency_cap_limits_correlated_usd_exposure():
    """Four USD-quoted symbols stretching together are one USD bet, not four.

    They share a series so they signal on the same bar; independent seeds at a
    3-sigma threshold almost never coincide, which is itself why the real
    universe gives only 1.68 effective bets.
    """
    shared = _reverting(n=3000)
    universe = {s: shared.copy()
                for s in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD")}
    tight = PortfolioConfig(risk_percent_per_trade=0.5,
                            max_currency_risk_percent=1.0,
                            max_total_risk_percent=99.0,
                            max_concurrent_positions=99)
    loose = PortfolioConfig(risk_percent_per_trade=0.5,
                            max_currency_risk_percent=99.0,
                            max_total_risk_percent=99.0,
                            max_concurrent_positions=99)
    capped = replay_system(universe, LOCKED_V17, tight)
    uncapped = replay_system(universe, LOCKED_V17, loose)
    assert capped.rejected_for_risk > 0
    assert len(capped.trades) < len(uncapped.trades)


def test_per_symbol_costs_are_applied():
    bars = {"EURUSD": _reverting()}
    free = replay_system(bars, LOCKED_V17, costs={"EURUSD": ZERO_COST})
    paid = replay_system(bars, LOCKED_V17,
                         costs={"EURUSD": cost_for("EURUSD", "typical")})
    assert paid.net_profit < free.net_profit
    assert sum(t.cost for t in paid.trades) > 0


def test_net_profit_matches_the_trade_ledger():
    r = replay_system({"EURUSD": _reverting()}, LOCKED_V17,
                      costs={"EURUSD": cost_for("EURUSD", "typical")})
    assert r.net_profit == approx(sum(t.profit for t in r.trades), abs=0.05)


def test_time_stop_bounds_holding_period():
    cfg = replace(LOCKED_V17, max_holding_bars=5)
    r = replay_system({"EURUSD": _reverting()}, cfg)
    assert r.trades
    max_bars = max((t.exit_time - t.entry_time) // H4 for t in r.trades)
    assert max_bars <= 6      # 5 bars held, plus the entry bar


def test_jpy_pair_needs_a_converter():
    with pytest.raises(ValueError, match="quoted in JPY"):
        replay_system({"USDJPY": _reverting(base=150.0, sigma=0.4)},
                      LOCKED_V17)


def test_jpy_pair_trades_with_a_converter():
    bars = {"USDJPY": _reverting(base=150.0, sigma=0.4)}
    conv = Converter([START, START + 2000 * H4], [150.0, 150.0], "USDJPY")
    r = replay_system(bars, LOCKED_V17, converters={"JPY": conv})
    assert r.trades
