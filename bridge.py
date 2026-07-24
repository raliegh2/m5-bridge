"""Guarded live entrypoint. Run with: python bridge.py

The normal Gold/intraday and swing engines run through the persistent
account-level session guard. The separately supervised V12 final profile
remains blocked from this generic entrypoint.
"""

import os

from mt5_ai_bridge.app import run
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.logging_config import get_logger
from mt5_ai_bridge.mt5_client import create_client
from mt5_ai_bridge.session_guard import RiskGuardedClient

log = get_logger("bridge")


def main() -> None:
    if os.getenv("V12_FINAL_PROFILE"):
        raise SystemExit(
            "V12 final profile selected: generic bridge.py is disabled. "
            "Use the named-engine FinalV12Adapter proposal workflow."
        )

    settings = load_settings()
    journal = Journal(settings.db_path)
    client = RiskGuardedClient(
        create_client(),
        settings,
        journal=journal,
    )

    try:
        run(
            settings=settings,
            client=client,
            journal=journal,
        )
    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C).")


if __name__ == "__main__":
    main()
