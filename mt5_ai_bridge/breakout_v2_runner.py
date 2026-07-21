"""One-command, fail-closed automated demo runner for Breakout V2."""

from dataclasses import replace

from .app import run
from .config import Settings, load_settings
from .enums import Mode


def automated_demo_settings(settings: Settings) -> Settings:
    """Force the validated engine topology while preserving credentials/UI."""
    return replace(
        settings,
        symbol="GBPUSD",
        symbols=("GBPUSD",),
        strategy="gbpusd_breakout_v2",
        mode=Mode.AUTO,
        require_demo=True,
        multi_book=False,
        risk_based_sizing=True,
        risk_percent=0.50,
        max_open_positions=1,
        max_same_direction=1,
        min_same_direction=1,
        trail_enabled=False,
    )


def main() -> None:
    settings = automated_demo_settings(load_settings())
    if not settings.has_credentials:
        raise SystemExit(
            "Missing MT5 credentials. Configure MT5_LOGIN, MT5_PASSWORD and "
            "MT5_SERVER in .env before starting the demo runner."
        )
    print(
        "Starting GBPUSD Breakout V2 in AUTO demo-only mode "
        "(base risk 0.50%, one position maximum)."
    )
    run(settings=settings)


if __name__ == "__main__":
    main()
