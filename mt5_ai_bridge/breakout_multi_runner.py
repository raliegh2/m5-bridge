"""One-command, fail-closed demo runner for the multi-symbol V2 breakout system.

Runs the validated composition only:
    swing  H4/D1    -> GBPUSD (160 trades / 10.5 yrs, PF 1.35)
    intraday M30/H4 -> XAUUSD gold (132 trades, out-of-sample +4.6%, PF ~1.5)

AUDUSD / EURUSD swing were rejected on ~13 years of H4 (PF 0.84, negative in and
out of sample); USDJPY / GBPJPY and FX-intraday lost too. AUTO mode +
REQUIRE_DEMO=true: orders fail closed unless MT5 reports a demo account.
"""

from dataclasses import replace

from .app import run
from .config import Settings, load_settings
from .enums import Mode

# The validated set. engines_for() will only run engines whose symbol is here.
VALIDATED_SYMBOLS = ("GBPUSD", "XAUUSD")


def automated_demo_settings(settings: Settings) -> Settings:
    return replace(
        settings,
        symbols=VALIDATED_SYMBOLS,
        strategy="breakout_multi",
        mode=Mode.AUTO,
        require_demo=True,
        risk_based_sizing=True,
    )


def main() -> None:
    settings = automated_demo_settings(load_settings())
    if not settings.has_credentials:
        raise SystemExit(
            "Missing MT5 credentials. Configure MT5_LOGIN, MT5_PASSWORD and "
            "MT5_SERVER in .env before starting the demo runner."
        )
    from . import breakout_multi
    engines = breakout_multi.engines_for(settings)
    print("Starting multi-symbol V2 breakout (swing FX + intraday gold) in AUTO "
          "demo-only mode.")
    print(f"Universal cap: combined risk ceiling {settings.combined_risk_ceiling:g}%"
          f"  ·  per-symbol drawdown halt {settings.per_symbol_dd_pct:g}%")
    print("Engines cleared to operate:")
    for e in engines:
        print(f"  · {e.symbol:7s} {e.kind:8s} {e.entry_tf}/{e.trend_tf}"
              f"  risk {e.risk_percent:g}%")
    run(settings=settings)


if __name__ == "__main__":
    main()
