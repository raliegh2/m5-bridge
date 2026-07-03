"""Start the final V12 bot and automatically load its dashboard.

Run:
    python v12_final_bot.py

This is the preferred local entry point. It starts the five-symbol strategy
scanner, launches the localhost dashboard server on port 8801, and opens the
dashboard in the default browser.
"""
from __future__ import annotations

import os

# Use 8801 by default so the final V12 dashboard does not conflict with older
# dashboard processes that may still be holding port 8800.
os.environ.setdefault("DASHBOARD_PORT", "8801")

from v12_final_dashboard import main


if __name__ == "__main__":
    main()
