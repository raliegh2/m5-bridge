"""Windows-safe launcher for the split H1/M1 V14.3 satellite bot.

Windows can briefly lock the dashboard JSON while the browser thread reads it.
The base dashboard uses a single atomic ``replace`` call, which can raise
``PermissionError: [WinError 5]`` during that short race. This launcher swaps in
an implementation that retries the atomic replacement and skips one dashboard
refresh rather than terminating the trading runner.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import v14_3_satellite_bot_m1 as bot
from mt5_ai_bridge.v14_3_live_dashboard import LiveDashboard


class WindowsSafeLiveDashboard(LiveDashboard):
    """Dashboard writer tolerant of transient Windows file locks."""

    replace_attempts = 12
    replace_delay_seconds = 0.025

    def write(self, payload: dict[str, Any]) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.snapshot_path.with_name(
            f"{self.snapshot_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )

        replaced = False
        try:
            for attempt in range(self.replace_attempts):
                try:
                    os.replace(temporary, self.snapshot_path)
                    replaced = True
                    break
                except PermissionError:
                    if attempt + 1 >= self.replace_attempts:
                        break
                    time.sleep(self.replace_delay_seconds * (attempt + 1))
                except OSError as exc:
                    # Windows sharing violations may surface as WinError 5 or 32.
                    if getattr(exc, "winerror", None) not in {5, 32}:
                        raise
                    if attempt + 1 >= self.replace_attempts:
                        break
                    time.sleep(self.replace_delay_seconds * (attempt + 1))
        finally:
            if not replaced:
                # Keep the previous valid dashboard snapshot. A missed refresh is
                # preferable to stopping the strategy and execution loop.
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


# ``main`` resolves this module global when it constructs the dashboard.
bot.LiveDashboard = WindowsSafeLiveDashboard


if __name__ == "__main__":
    bot.main()
