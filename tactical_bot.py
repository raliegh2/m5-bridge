"""Run the tactical book against the live terminal.

    python tactical_bot.py            # act if the month has turned
    python tactical_bot.py --dry-run  # decide and print, never send
    python tactical_bot.py --force    # rebalance now, ignoring the calendar

The book is a monthly long-or-flat overlay: hold each leg while it is above its
ten-month average, otherwise hold that sleeve in cash. Configure it in `.env`:

    TACTICAL_ENABLED=true
    TACTICAL_WEIGHT_SCHX=0.5
    TACTICAL_WEIGHT_IAU=0.5
    TACTICAL_FRACTION_INVESTED=0.90
    MODE=APPROVAL

`MODE` governs execution exactly as it does for the main bridge: READ_ONLY
prints, APPROVAL asks before each order, AUTO places them. The default is
APPROVAL and the account should be a demo one.

State lives in `state/tactical_state.json` so a restart does not re-trade a
month that has already been rebalanced.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.enums import Mode
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.logging_config import get_logger, setup_logging
from mt5_ai_bridge.mt5_client import RealMT5Client
from mt5_ai_bridge.risk_v18 import DrawdownGovernor, KillSwitch
from mt5_ai_bridge.tactical_allocation import locked_tactical_config
from mt5_ai_bridge.tactical_runner import (TacticalLeg, apply_plans,
                                           is_rebalance_due, plan_rebalance)

log = get_logger("tactical_bot")
STATE = Path(__file__).resolve().parent / "state" / "tactical_state.json"


def _load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        log.warning("could not read %s; treating as a first run", STATE)
        return {}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="decide and print; never send an order")
    parser.add_argument("--force", action="store_true",
                        help="rebalance even if this month is already done")
    args = parser.parse_args(argv)

    setup_logging()
    settings = load_settings()
    if args.dry_run:
        settings = replace(settings, mode=Mode.READ_ONLY)

    if not settings.tactical_enabled:
        print("TACTICAL_ENABLED is not set. Nothing to do.")
        return 0
    if not settings.tactical_legs:
        print("No legs configured. Set TACTICAL_WEIGHT_<SYMBOL> entries.")
        return 2

    legs = [TacticalLeg(symbol, weight)
            for symbol, weight in settings.tactical_legs]
    cfg = locked_tactical_config()

    state = _load_state()
    last_raw = state.get("last_rebalance")
    last = datetime.fromisoformat(last_raw) if last_raw else None
    now = datetime.now(timezone.utc)
    if not args.force and not is_rebalance_due(now, last):
        print(f"Already rebalanced for {now:%Y-%m}. Nothing to do "
              f"(--force to override).")
        return 0

    client = RealMT5Client()
    if not client.initialize():
        log.error("MT5 initialize failed: %s", client.last_error())
        return 1

    try:
        if settings.has_credentials:
            client.login(settings.login, settings.password, settings.server)
        account = client.account_info()
        if account is None:
            log.error("no account info")
            return 1
        print(f"account {getattr(account, 'login', '?')} "
              f"balance {getattr(account, 'balance', 0.0):,.2f}, "
              f"mode {settings.mode.value}")

        governor = DrawdownGovernor()
        peak = float(state.get("peak_equity") or 0.0)
        if peak:
            governor.observe(peak)

        plans = plan_rebalance(
            client, legs, cfg, settings.tactical_fraction_invested,
            governor=governor, kill_switch=KillSwitch())
        if not plans:
            print("No plan produced; check history availability.")
            return 1

        print(f"\n{'symbol':<8}{'signal':<9}{'price':>10}{'hold':>7}"
              f"{'have':>7}{'action':>8}")
        print("-" * 49)
        for plan in plans:
            print(f"{plan.symbol:<8}"
                  f"{'above' if plan.above_average else 'below':<9}"
                  f"{plan.price:>10.2f}{plan.target_shares:>7}"
                  f"{plan.current_shares:>7}"
                  f"{plan.action + ' ' + str(abs(plan.delta)):>8}")
            log.info("%s", plan.describe())

        with Journal() as journal:
            results = apply_plans(client, journal, settings, plans)

        if settings.mode is not Mode.READ_ONLY and not args.dry_run:
            state["last_rebalance"] = now.isoformat()
            state["peak_equity"] = max(
                governor.peak_equity,
                float(getattr(account, "equity", 0.0) or 0.0))
            _save_state(state)

        # A READ_ONLY decision is a report, not a failure -- only count things
        # that were meant to reach the broker and did not.
        if settings.mode is Mode.READ_ONLY:
            pending = [p for p in plans if p.delta != 0]
            print(f"\nDry run: {len(pending)} order(s) would be placed. "
                  "Set MODE=APPROVAL and drop --dry-run to trade them.")
            return 0

        failures = [message for ok, message in results if not ok]
        if failures:
            print(f"\n{len(failures)} action(s) not completed:")
            for message in failures:
                print(f"  {message}")
        return 0
    finally:
        client.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
