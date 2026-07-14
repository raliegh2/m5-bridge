"""Run the final approved five-symbol portfolio over repository history.

The executable final registry is intentionally the same seven-engine family used
by the frozen V12 validated-assets replay. This wrapper validates that registry,
runs the repository's historical replay, and writes a provenance manifest so the
result cannot be confused with broker-native tick testing.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mt5_ai_bridge.final_engine_registry import FINAL_ENGINES, FINAL_SYMBOLS, registry_summary
from mt5_ai_bridge.v12_final_risk import ENGINE_RULES

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research"
SOURCE_RUNNER = RESEARCH / "v12_plus_validated_assets_backtest.py"
SOURCE_OUTPUT = RESEARCH / "v12_plus_validated_assets_output"
OUT = RESEARCH / "final_five_symbol_10y_output"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_registry() -> None:
    if set(FINAL_ENGINES) != set(ENGINE_RULES):
        raise RuntimeError("Final executable registry no longer matches V12 engine rules")
    symbols = {engine.symbol for engine in FINAL_ENGINES.values()}
    if symbols != set(FINAL_SYMBOLS):
        raise RuntimeError(f"Final registry does not cover all symbols: {sorted(symbols)}")
    if not SOURCE_RUNNER.exists():
        raise FileNotFoundError(SOURCE_RUNNER)


def main() -> None:
    validate_registry()
    OUT.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [sys.executable, str(SOURCE_RUNNER)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    (OUT / "backtest_stdout.log").write_text(completed.stdout, encoding="utf-8")
    (OUT / "backtest_stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise SystemExit(
            f"Historical replay failed with exit code {completed.returncode}; "
            f"see {OUT / 'backtest_stderr.log'}"
        )

    artifacts = []
    if SOURCE_OUTPUT.exists():
        for path in sorted(SOURCE_OUTPUT.rglob("*")):
            if path.is_file():
                artifacts.append({
                    "path": str(path.relative_to(ROOT)),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                })

    manifest = {
        "model": "FINAL_FIVE_SYMBOL_V12_GBPJPY_GUARDED_EXECUTION_CANDIDATE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested_window_years": 10,
        "actual_history_note": (
            "The repository's common five-symbol public history is approximately "
            "2012-11-26 through 2022-03-04 (about 9.27 years), not a complete ten years."
        ),
        "starting_balance_usd": 5000.0,
        "symbols": list(FINAL_SYMBOLS),
        "engine_registry": registry_summary(),
        "source_backtest": str(SOURCE_RUNNER.relative_to(ROOT)),
        "source_outputs": artifacts,
        "limitations": [
            "Completed-bar research replay; not tick-level broker execution.",
            "Historical spread, commission, swap and slippage are not equivalent to current broker fills.",
            "The new live GBPJPY session/spread/stop filters are execution controls; the source V12 historical replay remains the frozen approved engine evidence.",
            "V11 intraday engines are excluded because no merged chronological five-symbol approval replay exists.",
        ],
    }
    (OUT / "final_five_symbol_10y_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
