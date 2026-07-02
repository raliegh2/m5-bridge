"""Portfolio controller for frozen V4 swing + quality-first Satellite V2."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .enums import Mode, Signal
from .execution import pip_size, place_market_order
from .sizing import RiskConfig, risk_lot
from .gbpusd_v4 import (
    COMMENT as V4_COMMENT,
    MAGIC as V4_MAGIC,
    LiveParams as V4Params,
    _effective_risk as v4_effective_risk,
    _load_state as load_v4_state,
    _news_blocked as v4_news_blocked,
    _save_state as save_v4_state,
    evaluate_setup as evaluate_v4_setup,
    manage_positions as manage_v4_positions,
)
from .gbpusd_satellite_v2 import (
    COMMENT as SATELLITE_COMMENT,
    MAGIC as SATELLITE_MAGIC,
    SatelliteV2Params,
    SatelliteV2Setup,
    current_spread_pips,
    evaluate_setup as evaluate_satellite_setup,
    manage_positions as manage_satellite_positions,
    news_blocked as satellite_news_blocked,
    risk_capped_lot,
    setup_stop_target_pips,
)

ENGINE_MAGICS = {V4_MAGIC, SATELLITE_MAGIC}


def _state_path() -> Path:
    return Path(os.getenv("PORTFOLIO_V2_STATE_PATH", "portfolio_v2_state.json"))


def _load_portfolio_state(now: datetime) -> dict:
    path = _state_path()
    payload = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = {}
    today = now.date().isoformat()
    if payload.get("date") != today:
        payload = {
            "date": today,
            "committed_risk_percent": 0.0,
            "satellite_setups_used": [],
            "satellite_entries": 0,
        }
    payload.setdefault("committed_risk_percent", 0.0)
    payload.setdefault("satellite_setups_used", [])
    payload.setdefault("satellite_entries", 0)
    return payload


def _save_portfolio_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def _position_side(client, position) -> Signal:
    return Signal.BUY if position.type == client.POSITION_TYPE_BUY else Signal.SELL


def _engine_positions(positions, magic: int) -> list:
    return [position for position in positions if getattr(position, "magic", None) == magic]


def _unknown_position_present(positions) -> bool:
    return any(getattr(position, "magic", None) not in ENGINE_MAGICS for position in positions)


def _direction_allowed(client, positions, side: Signal) -> tuple[bool, str]:
    if _unknown_position_present(positions):
        return False, "A non-portfolio GBPUSD position is open."
    directions = {_position_side(client, position) for position in positions}
    if directions and directions != {side}:
        return False, "An opposing GBPUSD portfolio position is open."
    return True, "Direction is compatible with current GBPUSD exposure."


def _open_risk_percent(client, positions, symbol: str, balance: float,
                       pip_value_per_lot: float) -> tuple[float, bool]:
    pip = pip_size(client, symbol) or 0.0001
    total_risk = 0.0
    for position in positions:
        if getattr(position, "magic", None) not in ENGINE_MAGICS:
            continue
        stop = float(getattr(position, "sl", 0.0) or 0.0)
        entry = float(getattr(position, "price_open", 0.0) or 0.0)
        if stop <= 0 or entry <= 0:
            return 100.0, False
        total_risk += abs(entry - stop) / pip * pip_value_per_lot * float(position.volume)
    return (total_risk / balance * 100 if balance else 100.0), True


def _approval(settings, engine: str, side: Signal, volume: float,
              stop_pips: float, target_pips: float) -> bool:
    if settings.mode is not Mode.APPROVAL:
        return True
    answer = input(
        f"Place {engine} {side.value} {volume:.2f} lots GBPUSD "
        f"SL {stop_pips:.1f}p TP {target_pips:.1f}p? Type YES: "
    )
    return answer == "YES"


def _log_management(journal, symbol: str, message: str) -> None:
    journal.log_order(
        symbol, "MANAGE", 0.0, None, None, None, None, "UPDATED", message
    )


def _planned_v4_order(client, settings, account, setup, effective_risk: float,
                      params: V4Params) -> dict:
    pip = pip_size(client, settings.symbol) or 0.0001
    stop_pips = min(
        max(params.stop_atr * setup.atr_price / pip, params.min_stop_pips),
        params.max_stop_pips,
    )
    target_pips = params.target_r * stop_pips
    risk_percent = min(effective_risk, 0.35)
    volume = risk_lot(
        float(account.balance),
        stop_pips,
        RiskConfig(
            enabled=True,
            risk_percent=risk_percent,
            pip_value_per_lot=float(settings.pip_value_per_lot),
            max_lot=float(settings.max_lot),
        ),
    )
    actual_risk = stop_pips * float(settings.pip_value_per_lot) * volume
    return {
        "volume": volume,
        "stop_pips": stop_pips,
        "target_pips": target_pips,
        "risk_percent": actual_risk / float(account.balance) * 100,
    }


def _planned_satellite_order(client, settings, account,
                             setup: SatelliteV2Setup,
                             params: SatelliteV2Params) -> dict:
    pip = pip_size(client, settings.symbol) or 0.0001
    stop_pips, target_pips = setup_stop_target_pips(setup, pip)
    volume, actual_risk = risk_capped_lot(
        client,
        settings.symbol,
        float(account.balance),
        stop_pips,
        float(settings.pip_value_per_lot),
        params,
    )
    return {
        "volume": volume,
        "stop_pips": stop_pips,
        "target_pips": target_pips,
        "risk_percent": actual_risk / float(account.balance) * 100,
    }


def _place_v4(client, journal, settings, setup, order: dict,
              v4_state: dict) -> bool:
    if not _approval(
        settings, "V4 Swing", setup.side, order["volume"],
        order["stop_pips"], order["target_pips"],
    ):
        return False
    ok, message = place_market_order(
        client,
        settings.symbol,
        setup.side,
        order["volume"],
        order["stop_pips"],
        order["target_pips"],
        magic=V4_MAGIC,
        comment=f"{V4_COMMENT} {setup.variant}",
    )
    journal.log_order(
        settings.symbol,
        setup.side.value,
        order["volume"],
        None,
        order["stop_pips"],
        order["target_pips"],
        None,
        "FILLED" if ok else "REJECTED",
        f"[V4_SWING] {message}",
    )
    if ok:
        v4_state["last_signal_end"] = setup.signal_end.isoformat()
    return ok


def _place_satellite(client, journal, settings, setup: SatelliteV2Setup,
                     order: dict) -> bool:
    if not _approval(
        settings, "Satellite V2", setup.side, order["volume"],
        order["stop_pips"], order["target_pips"],
    ):
        return False
    ok, message = place_market_order(
        client,
        settings.symbol,
        setup.side,
        order["volume"],
        order["stop_pips"],
        order["target_pips"],
        magic=SATELLITE_MAGIC,
        comment=f"{SATELLITE_COMMENT} {setup.name}",
    )
    journal.log_order(
        settings.symbol,
        setup.side.value,
        order["volume"],
        None,
        order["stop_pips"],
        order["target_pips"],
        None,
        "FILLED" if ok else "REJECTED",
        f"[SATELLITE_V2:{setup.name}] {message}",
    )
    return ok


def _thinking(v4_setup, satellite_setup, diagnostics, note: str) -> dict:
    ready = [setup for setup in (v4_setup, satellite_setup) if setup is not None]
    directions = {setup.side for setup in ready}
    bias = next(iter(directions)).value if len(directions) == 1 else (
        "MULTIPLE" if len(directions) > 1 else "NONE"
    )
    return {
        "timeframes": [
            {
                "label": "Swing anchor", "tf": "D1",
                "signal": v4_setup.side.value if v4_setup else Signal.WAIT.value,
                "confidence": 1.0 if v4_setup else 0.0,
                "reason": "Frozen V4 daily regime filter.",
            },
            {
                "label": "Swing trigger", "tf": "H4",
                "signal": v4_setup.side.value if v4_setup else Signal.WAIT.value,
                "confidence": 1.0 if v4_setup else 0.0,
                "reason": v4_setup.reason if v4_setup else "No completed H4 V4 trigger.",
            },
            {
                "label": "Satellite trend", "tf": "H1/M30",
                "signal": diagnostics.get("h1_signal", Signal.WAIT.value),
                "confidence": 1.0 if satellite_setup else 0.0,
                "reason": "H1 trend strength; London also requires M30 alignment.",
            },
            {
                "label": "Satellite trigger", "tf": "M15",
                "signal": diagnostics.get("m15_signal", Signal.WAIT.value),
                "confidence": 1.0 if satellite_setup else 0.0,
                "reason": diagnostics.get("reason", "No completed M15 trigger."),
            },
        ],
        "bias": bias,
        "aligned": bool(ready) and len(directions) == 1,
        "setup_valid": bool(ready) and len(directions) == 1,
        "note": note,
        "engines": [
            {
                "name": "V4 Swing",
                "ready": bool(v4_setup),
                "bias": v4_setup.side.value if v4_setup else "NONE",
                "confidence": 1.0 if v4_setup else 0.0,
                "reason": v4_setup.reason if v4_setup else (
                    "Waiting for D1/H4 trend-expansion conditions."
                ),
            },
            {
                "name": "Satellite V2",
                "ready": bool(satellite_setup),
                "bias": satellite_setup.side.value if satellite_setup else "NONE",
                "confidence": 1.0 if satellite_setup else 0.0,
                "reason": diagnostics.get("reason", "Waiting for M15/M30 setup."),
            },
        ],
    }


def run_portfolio_v2_cycle(
    client,
    journal,
    settings,
    account,
    risk_ok: bool,
    active: bool,
    v4_params: V4Params = V4Params(),
    satellite_params: SatelliteV2Params = SatelliteV2Params(),
) -> dict:
    now = datetime.now(timezone.utc)
    portfolio_state = _load_portfolio_state(now)
    v4_state = load_v4_state()

    for message in manage_v4_positions(client, settings.symbol, v4_state, v4_params):
        _log_management(journal, settings.symbol, f"[V4_SWING] {message}")
    for message in manage_satellite_positions(
        client, settings.symbol, satellite_params, now_utc=now
    ):
        _log_management(journal, settings.symbol, message)

    effective_v4_risk, drawdown, v4_paused = v4_effective_risk(
        account, v4_state, v4_params
    )
    v4_setup, _ = evaluate_v4_setup(client, settings.symbol)
    satellite_setup, diagnostics = evaluate_satellite_setup(
        client, settings.symbol, satellite_params
    )
    positions = list(client.positions_get(symbol=settings.symbol) or [])
    open_risk, stops_valid = _open_risk_percent(
        client,
        positions,
        settings.symbol,
        float(account.balance),
        float(settings.pip_value_per_lot),
    )
    note = (
        f"Portfolio DD {drawdown:.2f}%; open risk {open_risk:.2f}%; "
        f"today committed risk {portfolio_state['committed_risk_percent']:.2f}%; "
        f"satellite entries {portfolio_state['satellite_entries']}/"
        f"{satellite_params.max_entries_per_day}."
    )
    thinking = _thinking(v4_setup, satellite_setup, diagnostics, note)

    if v4_setup:
        journal.log_signal(
            settings.symbol,
            v4_setup.side.value,
            f"[V4_SWING] {v4_setup.reason}",
            {"time": v4_setup.signal_end.isoformat(), "engine": "V4_SWING"},
            setup=1,
            filtered=0,
        )
    if satellite_setup:
        journal.log_signal(
            settings.symbol,
            satellite_setup.side.value,
            f"[SATELLITE_V2:{satellite_setup.name}] {satellite_setup.reason}",
            {
                "time": satellite_setup.signal_end.isoformat(),
                "engine": "SATELLITE_V2",
                "setup": satellite_setup.name,
            },
            setup=1,
            filtered=0,
        )

    if settings.mode is Mode.READ_ONLY or not risk_ok or not active:
        save_v4_state(v4_state)
        _save_portfolio_state(portfolio_state)
        return thinking
    if not stops_valid or _unknown_position_present(positions):
        thinking["note"] += " New entries blocked by missing stops or an external GBPUSD position."
        save_v4_state(v4_state)
        _save_portfolio_state(portfolio_state)
        return thinking

    if v4_setup and satellite_setup and v4_setup.side is not satellite_setup.side:
        satellite_setup = None
        thinking["note"] += " Conflicting satellite setup suppressed; V4 has priority."

    if v4_setup and not v4_paused and not _engine_positions(positions, V4_MAGIC):
        allowed, reason = _direction_allowed(client, positions, v4_setup.side)
        duplicate = v4_state.get("last_signal_end") == v4_setup.signal_end.isoformat()
        news = v4_news_blocked(now, v4_params)
        order = _planned_v4_order(
            client, settings, account, v4_setup, effective_v4_risk, v4_params
        )
        daily_after = portfolio_state["committed_risk_percent"] + order["risk_percent"]
        open_after = open_risk + order["risk_percent"]
        if (
            allowed and not duplicate and not news
            and daily_after <= satellite_params.daily_new_risk_percent
            and open_after <= satellite_params.open_risk_cap_percent
        ):
            if _place_v4(client, journal, settings, v4_setup, order, v4_state):
                portfolio_state["committed_risk_percent"] = daily_after
                positions = list(client.positions_get(symbol=settings.symbol) or positions)
                open_risk = open_after
        else:
            thinking["note"] += (
                f" V4 blocked: {reason}; duplicate={duplicate}; news={news}; "
                f"daily_after={daily_after:.2f}%; open_after={open_after:.2f}%."
            )

    if satellite_setup and not _engine_positions(positions, SATELLITE_MAGIC):
        allowed, reason = _direction_allowed(client, positions, satellite_setup.side)
        order = _planned_satellite_order(
            client, settings, account, satellite_setup, satellite_params
        )
        daily_after = portfolio_state["committed_risk_percent"] + order["risk_percent"]
        open_after = open_risk + order["risk_percent"]
        spread = current_spread_pips(client, settings.symbol)
        setup_unused = satellite_setup.name not in portfolio_state["satellite_setups_used"]
        entries_available = (
            portfolio_state["satellite_entries"] < satellite_params.max_entries_per_day
        )
        news = satellite_news_blocked(now)
        if (
            allowed and setup_unused and entries_available and not news
            and spread is not None and spread <= satellite_params.max_spread_pips
            and daily_after <= satellite_params.daily_new_risk_percent
            and open_after <= satellite_params.open_risk_cap_percent
        ):
            if _place_satellite(
                client, journal, settings, satellite_setup, order
            ):
                portfolio_state["committed_risk_percent"] = daily_after
                portfolio_state["satellite_entries"] += 1
                portfolio_state["satellite_setups_used"].append(satellite_setup.name)
        else:
            thinking["note"] += (
                f" Satellite V2 blocked: {reason}; setup_unused={setup_unused}; "
                f"entries_available={entries_available}; spread={spread}; news={news}; "
                f"daily_after={daily_after:.2f}%; open_after={open_after:.2f}%."
            )

    save_v4_state(v4_state)
    _save_portfolio_state(portfolio_state)
    return thinking
