import pandas as pd

from mt5_ai_bridge.data import load_csv


def test_load_csv_normalises_columns(tmp_path):
    csv = tmp_path / "rates.csv"
    pd.DataFrame({
        "Date": [1, 2, 3],
        "Open": [1.20, 1.21, 1.22],
        "High": [1.21, 1.22, 1.23],
        "Low": [1.19, 1.20, 1.21],
        "Close": [1.205, 1.215, 1.225],
    }).to_csv(csv, index=False)

    df = load_csv(str(csv))
    assert list(df.columns) == ["time", "open", "high", "low", "close"]
    assert len(df) == 3
    assert df["close"].iloc[-1] == 1.225


def test_load_csv_missing_column_raises(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9]}).to_csv(csv, index=False)
    try:
        load_csv(str(csv))
        assert False, "expected ValueError"
    except ValueError as e:
        assert "close" in str(e).lower()
