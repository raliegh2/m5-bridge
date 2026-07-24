<<<<<<< HEAD
"""Default bridge entrypoint. Run with: python bridge.py

The final V12 profile uses named engines and a dedicated supervised proposal
adapter. The legacy generic strategy is blocked when V12_FINAL_PROFILE is set,
so the wrong strategy cannot run under the final research configuration.
"""

import os

from mt5_ai_bridge.app import main
=======
"""Live entrypoint. Run with: python bridge.py

The current intraday/Gold and swing engines remain in ``mt5_ai_bridge.app``.
This entrypoint wraps the MT5 client with the persistent account-level session
risk guard before starting the live loop.
"""

from mt5_ai_bridge.app import run
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.logging_config import get_logger
from mt5_ai_bridge.mt5_client import create_client
from mt5_ai_bridge.session_guard import RiskGuardedClient

log = get_logger("bridge")


def main() -> None:
    settings = load_settings()
    journal = Journal(settings.db_path)
    client = RiskGuardedClient(create_client(), settings, journal=journal)
    try:
        run(settings=settings, client=client, journal=journal)
    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C).")

>>>>>>> origin/main


if __name__ == "__main__":
    if os.getenv("V12_FINAL_PROFILE"):
        raise SystemExit(
            "V12 final profile selected: legacy bridge.py is disabled. "
            "Use the named-engine FinalV12Adapter proposal workflow."
        )
    main()
