"""Start the final V12 bot and automatically load its dashboard.

Run:
    python v12_final_bot.py

This is the preferred local entry point. It starts the five-symbol strategy
scanner, launches the localhost dashboard server, and opens the dashboard in
the default browser.
"""
from v12_final_dashboard import main


if __name__ == "__main__":
    main()
