"""One-command, fail-closed automated demo runner for Breakout V2."""

from dataclasses import replace

from .app import run
from .config import Settings, load_settings
from .enums import Mode


def automated_demo_settings(settings: Settings) -> Settings:
    """Add Breakout V2 to the configured multi-symbol demo portfolio."""
    symbols = tuple(dict.fromkeys(("GBPUSD", *settings.symbols)))
    return replace(
        settings,
        symbols=symbols,
        strategy="hybrid_breakout_v2",
        mode=Mode.AUTO,
        require_demo=True,
        multi_book=True,
        risk_based_sizing=True,
        risk_percent=0.50,
    )


def main() -> None:
    settings = automated_demo_settings(load_settings())
    if not settings.has_credentials:
        raise SystemExit(
            "Missing MT5 credentials. Configure MT5_LOGIN, MT5_PASSWORD and "
            "MT5_SERVER in .env before starting the demo runner."
        )
    print(
        "Starting multi-symbol portfolio + GBPUSD Breakout V2 in AUTO "
        "demo-only mode (shared portfolio risk controls)."
    )
    run(settings=settings)


if __name__ == "__main__":
    main()
