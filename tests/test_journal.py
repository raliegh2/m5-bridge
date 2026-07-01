from mt5_ai_bridge.journal import Journal


def test_log_and_read_signal(tmp_path):
    db = tmp_path / "j.db"
    with Journal(str(db)) as j:
        j.log_signal("GBPUSD", "BUY", "Bullish", {"close": 1.23})
        rows = j.recent_signals()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "GBPUSD"
    assert rows[0]["signal"] == "BUY"
    assert "1.23" in rows[0]["snapshot"]


def test_log_and_read_order(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    j.log_order("GBPUSD", "BUY", 0.01, 1.2345, 1.2315, 1.2405, 555, "FILLED", "ok")
    rows = j.recent_orders()
    j.close()
    assert rows[0]["ticket"] == 555
    assert rows[0]["status"] == "FILLED"


def test_log_risk_event(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    rid = j.log_risk_event(False, "Daily loss limit reached.", 10000, 9700, 0)
    j.close()
    assert rid == 1


def test_persistence_across_connections(tmp_path):
    db = str(tmp_path / "j.db")
    j1 = Journal(db)
    j1.log_signal("EURUSD", "SELL", "Bearish")
    j1.close()

    j2 = Journal(db)
    rows = j2.recent_signals()
    j2.close()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "EURUSD"
