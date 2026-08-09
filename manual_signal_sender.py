"""Interactive terminal utility for submitting manual trading signals."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid


INCOMING_DIRECTORY = Path("signals") / "incoming"


def prompt_symbol() -> str:
    """Ask until a non-empty symbol is supplied."""
    while True:
        symbol = input("Symbol (e.g., EURUSD, GBPUSD, XAUUSD): ").strip().upper()
        if symbol:
            return symbol
        print("Symbol cannot be empty. Please try again.")


def prompt_direction() -> str:
    """Ask until BUY or SELL is supplied."""
    while True:
        direction = input("Direction (BUY or SELL): ").strip().upper()
        if direction in {"BUY", "SELL"}:
            return direction
        print("Direction must be BUY or SELL. Please try again.")


def prompt_optional_float(label: str, *, positive: bool = True) -> float | None:
    """Read an optional number; an empty response returns None."""
    while True:
        raw = input(label).strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            print("Enter a valid number, or press Enter to skip.")
            continue
        if positive and value <= 0:
            print("Value must be greater than zero, or press Enter to skip.")
            continue
        return value


def create_signal() -> dict[str, object]:
    """Prompt for and construct one manual signal."""
    symbol = prompt_symbol()
    direction = prompt_direction()
    volume = prompt_optional_float("Volume in lots (Enter for agent auto-size): ")
    stop_loss = prompt_optional_float("Stop loss price (Enter for ATR-based stop): ")
    take_profit = prompt_optional_float(
        "Take profit price (Enter for partial + trailing management): "
    )

    now = datetime.now(timezone.utc)
    signal_id = f"manual_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "direction": direction,
        "volume_lots": volume,
        "stop_loss_price": stop_loss,
        "take_profit_price": take_profit,
        "timestamp": now.isoformat(),
        "source": "manual",
    }


def write_signal(signal: dict[str, object]) -> Path:
    """Atomically publish a signal JSON file to the incoming directory."""
    INCOMING_DIRECTORY.mkdir(parents=True, exist_ok=True)
    destination = INCOMING_DIRECTORY / f"{signal['signal_id']}.json"
    temporary = destination.with_suffix(".json.tmp")

    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(signal, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    return destination


def prompt_another() -> bool:
    """Return True when the user wants to submit another signal."""
    while True:
        answer = input("Submit another? (y/n): ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please enter y or n.")


def main() -> None:
    """Run the interactive manual-signal loop."""
    print("Manual Trading Signal Sender")
    print("Press Ctrl+C at any time to exit.\n")

    try:
        while True:
            signal = create_signal()
            path = write_signal(signal)
            print("\nSignal submitted successfully:")
            print(json.dumps(signal, indent=2))
            print(f"Written to: {path.resolve()}\n")

            if not prompt_another():
                print("Done.")
                break
            print()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. No further signals were submitted.")


if __name__ == "__main__":
    main()
