from mt5_ai_bridge.v12_final_mode import AccountModeStore


def test_account_mode_persists_and_switches_at_runtime(tmp_path) -> None:
    path = tmp_path / "mode.json"
    store = AccountModeStore(str(path))
    assert store.get() == "DEMO"
    assert store.set("live") == "LIVE"
    assert AccountModeStore(str(path)).get() == "LIVE"
    assert store.set("demo") == "DEMO"
