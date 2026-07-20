from datetime import datetime, timezone

from mt5_ai_bridge.dashboard import (build_dashboard, est_now, save_dashboard,
                                     session_label, write_dashboard_live)
from mt5_ai_bridge.journal import Journal


def _populate(db_path):
    j = Journal(db_path)
    j.log_risk_event(True, "Risk check passed.", 10000, 10000, 0)
    j.log_risk_event(True, "Risk check passed.", 10000, 9950, 1)
    j.log_risk_event(False, "Daily loss limit reached.", 10000, 9700, 1)
    j.log_signal("GBPUSD", "BUY", "Bullish trend confirmed.", {"close": 1.23})
    j.log_signal("GBPUSD", "WAIT", "No trade setup.")
    j.log_order("GBPUSD", "BUY", 0.09, None, 80, 160, 555, "FILLED", "[swing/NY] ok")
    return j


def _live():
    return {
        "symbol": "GBPUSD", "balance": 10000.0, "equity": 9985.0,
        "pip_size": 0.0001,
        "positions": [{
            "ticket": 555, "type": "BUY", "volume": 0.18, "profit": -15.0,
            "price_open": 1.27000, "price_current": 1.26850,
            "sl": 1.26200, "tp": 1.28600,
        }],
    }


# --- helpers ----------------------------------------------------------------

def test_session_label_windows():
    assert session_label(datetime(2026, 6, 29, 14, tzinfo=timezone.utc)) \
        == "London/New York overlap"
    assert session_label(datetime(2026, 6, 29, 20, tzinfo=timezone.utc)) == "New York"
    assert session_label(datetime(2026, 6, 29, 8, tzinfo=timezone.utc)) == "London"
    assert session_label(datetime(2026, 6, 29, 23, tzinfo=timezone.utc)) == "Sydney"


def test_est_now_has_meridiem():
    s = est_now(datetime(2026, 6, 29, 17, 30, tzinfo=timezone.utc))
    assert ("AM" in s) or ("PM" in s)


# --- static (journal-only) --------------------------------------------------

def test_static_dashboard_contains_data(tmp_path):
    j = _populate(str(tmp_path / "j.db"))
    html = build_dashboard(j)
    j.close()
    assert "MT5 AI Bridge" in html
    assert "Snapshot" in html
    assert "<svg" in html
    assert "<th>R:R</th>" not in html         # no live -> no positions table


def test_static_dashboard_handles_empty_db(tmp_path):
    j = Journal(str(tmp_path / "empty.db"))
    html = build_dashboard(j)
    j.close()
    assert "No rows yet." in html
    assert "Not enough data" in html


def test_dashboard_separates_analyses_setups_filters_and_trades(tmp_path):
    j = Journal(str(tmp_path / "stats.db"))
    j.log_signal("GBPUSD", "SELL", "raw", setup=0, filtered=1)
    j.log_signal("GBPUSD", "WAIT", "neutral", setup=0, filtered=0)
    j.log_signal("GBPUSD", "BUY", "valid", setup=1, filtered=0)
    j.log_order("GBPUSD", "BUY", 0.01, None, 10, 20, 7, "FILLED", "ok")
    html = build_dashboard(j)
    stats = j.signal_stats_today()
    j.close()

    assert stats == {"analyses": 3, "raw_buy": 1, "raw_sell": 1,
                     "raw_wait": 1, "setups": 1, "filtered": 1,
                     "executed": 1}
    for label in ("Total analyses", "Raw timeframe signals",
                  "Valid trade setups", "Executed trades",
                  "Filtered-out setups"):
        assert label in html
    assert "These are market <b>checks</b>, not trades" in html


def test_dashboard_shows_waiting_and_timeframe_why_text(tmp_path):
    j = Journal(str(tmp_path / "thinking.db"))
    thinking = {
        "aligned": False, "bias": "NONE",
        "note": "Waiting — M30/H4/D1 do not all agree.",
        "timeframes": [
            {"label": "Entry", "tf": "M15", "signal": "SELL",
             "confidence": 0.8, "reason": "price below EMA 200."},
            {"label": "Confirm", "tf": "H4", "signal": "BUY",
             "confidence": 0.7, "reason": "MACD above zero."},
        ],
    }
    html = build_dashboard(j, live=_live(), thinking=thinking)
    j.close()
    assert "WAITING" in html
    assert "<th>Why</th>" in html
    assert "price below EMA 200" in html
    assert "MACD above zero" in html


# --- live -------------------------------------------------------------------

def test_live_dashboard_shows_pl_rr_session_pips(tmp_path):
    j = _populate(str(tmp_path / "j.db"))
    html = build_dashboard(j, live=_live(), refresh_seconds=5,
                           now_utc=datetime(2026, 6, 29, 18, tzinfo=timezone.utc))
    j.close()

    assert "Live" in html and '<span class="live"' in html
    assert "Open P/L" in html and "Day P/L" in html
    assert "Risk : Reward" in html
    assert "<th>R:R</th>" in html                   # positions table present
    assert "1 : 2.0" in html or "1:2.0" in html     # reward/risk = 1600/800 pips
    assert "-15.00" in html                          # floating P/L
    assert "-15.0" in html                           # floating pips
    assert 'http-equiv="refresh"' in html
    assert "New York" in html                        # session at 18:00 UTC


def test_html_escaped(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    j.log_signal("GBPUSD", "WAIT", "<script>alert(1)</script>")
    html = build_dashboard(j)
    j.close()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_write_dashboard_live_writes_file(tmp_path):
    db = str(tmp_path / "j.db")
    _populate(db).close()
    out = str(tmp_path / "live.html")
    j = Journal(db)
    path = write_dashboard_live(j, _live(), out, refresh_seconds=5)
    j.close()
    assert path == out
    with open(out, encoding="utf-8") as fh:
        assert "Live" in fh.read()


def test_save_dashboard_cli_writes_static(tmp_path):
    db = str(tmp_path / "j.db")
    _populate(db).close()
    out = str(tmp_path / "dash.html")
    assert save_dashboard(db, out) == out
    with open(out, encoding="utf-8") as fh:
        assert "MT5 AI Bridge" in fh.read()


def test_day_start_equity(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    j.log_risk_event(True, "ok", 10000, 10000, 0)
    j.log_risk_event(True, "ok", 10000, 9900, 1)
    val = j.day_start_equity()
    j.close()
    assert val == 10000


def test_position_pips_use_per_position_pip_size():
    """Gold and FX in the same book must each use their OWN pip size.

    The account-level pip_size is FX-sized (0.0001). A gold position that
    carries its own pip_size (0.01) must not be scaled by the FX pip.
    """
    from mt5_ai_bridge.dashboard import _position_view
    gold = {"symbol": "XAUUSD", "type": "SELL", "price_open": 4006.58,
            "price_current": 4007.11, "sl": 4008.4, "tp": 3982.97,
            "pip_size": 0.01}
    view = _position_view(gold, 0.0001)   # account-level FX pip passed in
    # 0.53 price move / 0.01 pip = 53 pips (SELL losing -> negative), NOT 5300.
    assert view["pips"] == -53.0

    fx = {"symbol": "GBPUSD", "type": "BUY", "price_open": 1.2680,
          "price_current": 1.2712, "sl": 1.26, "tp": 1.284, "pip_size": 0.0001}
    assert _position_view(fx, 0.0001)["pips"] == 32.0


def test_engine_breakdown_panel_shows_all_symbols_and_engines(tmp_path):
    """The all-engines panel lists every symbol with both engines + reasons."""
    j = Journal(str(tmp_path / "eb.db"))
    rows = [
        {"symbol": "USDJPY", "aligned": True, "bias": "BUY",
         "engines": [
             {"name": "Intraday", "ready": True, "bias": "BUY", "confidence": 0.71,
              "reason": "M15 and M30 agree; no strong H4 opposition."},
             {"name": "Swing", "ready": False, "bias": "NONE", "confidence": 0.0,
              "reason": "Waiting for H4/D1 trend and M30/M15 timing to agree."}],
         "timeframes": [{"tf": "M15", "label": "Entry", "signal": "BUY",
                         "confidence": 0.71, "reason": "EMA20>EMA50."}]},
        {"symbol": "XAUUSD", "aligned": False, "bias": "NONE",
         "engines": [
             {"name": "Intraday", "ready": False, "bias": "NONE", "confidence": 0.0,
              "reason": "Waiting for M15 and M30 to agree."},
             {"name": "Swing", "ready": False, "bias": "NONE", "confidence": 0.0,
              "reason": "Waiting for H4/D1 trend and M30/M15 timing to agree."}],
         "timeframes": []},
    ]
    html = build_dashboard(j, live=_live(), engines=rows)
    j.close()
    assert "All engines" in html
    assert "USDJPY" in html and "XAUUSD" in html
    assert "Intraday" in html and "Swing" in html
    assert "Decision process" in html                # per-symbol read table
    assert "M15 and M30 agree" in html               # engine reason surfaced
    assert 'id="engines_panel"' in html              # live-updatable container


def test_engine_breakdown_panel_empty_when_no_rows(tmp_path):
    j = Journal(str(tmp_path / "eb2.db"))
    html = build_dashboard(j, live=_live(), engines=[])
    j.close()
    assert "All engines" not in html                 # panel omitted when empty


def test_engine_panel_shows_disabled_and_trades_summary(tmp_path):
    """Disabled engines render as DISABLED; the symbol shows which engines trade it."""
    j = Journal(str(tmp_path / "ebt.db"))
    rows = [
        {"symbol": "AUDUSD", "aligned": False, "bias": "NONE",
         "trades": ["Intraday"],
         "engines": [
             {"name": "Intraday", "ready": False, "bias": "NONE", "confidence": 0.0,
              "reason": "Waiting for M15 and M30 to agree.",
              "enabled": True, "risk": 0.30},
             {"name": "Swing", "ready": False, "bias": "NONE", "confidence": 0.0,
              "reason": "Not traded on this pair — engine risk set to 0.",
              "enabled": False, "risk": 0.0}],
         "timeframes": []},
    ]
    html = build_dashboard(j, live=_live(), engines=rows)
    j.close()
    assert "Trades: Intraday" in html          # per-pair engine summary
    assert "DISABLED" in html                  # swing engine marked disabled
    assert "risk 0.3%" in html                 # per-engine risk shown
