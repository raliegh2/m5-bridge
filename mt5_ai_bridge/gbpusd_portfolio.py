"""Portfolio orchestrator for frozen V4 swing + H1/M30 satellite intraday.

The portfolio keeps the frozen V4 signal parameters unchanged. The satellite is
an independently identified position book. Both books share account-level risk
checks from app.py and may coexist only when they point in the same direction.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

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
from .gbpusd_satellite import (
    COMMENT as SATELLITE_COMMENT,
    MAGIC as SATELLITE_MAGIC,
    SatelliteParams,
    can_enter_today,
    current_spread_pips,
    duplicate_signal,
    evaluate_setup as evaluate_satellite_setup,
    load_state as load_satellite_state,
    manage_positions as manage_satellite_positions,
    mark_entry as mark_satellite_entry,
    news_blocked as satellite_news_blocked,
    normalized_risk_percent,
    save_state as save_satellite_state,
    stop_and_target_pips,
)

ENGINE_MAGICS = {V4_MAGIC, SATELLITE_MAGIC}


def _side_from_position(client, position) -> Signal:
    return Signal.BUY if position.type == client.POSITION_TYPE_BUY else Signal.SELL


def _positions_for_magic(positions, magic: int) -> list:
    return [position for position in positions if getattr(position, "magic", None) == magic]


def _portfolio_max_risk() -> float:
    try:
        requested = float(os.getenv("PORTFOLIO_MAX_RISK_PERCENT", "0.50"))
    except ValueError:
        requested = 0.50
    return min(max(requested, 0.10), 0.50)


def _configured_risk(positions, satellite_risk: float) -> float:
    risk = 0.0
    if _positions_for_magic(positions, V4_MAGIC):
        risk += 0.35
    if _positions_for_magic(positions, SATELLITE_MAGIC):
        risk += satellite_risk
    return risk


def _has_unknown_symbol_position(positions) -> bool:
    return any(getattr(position, "magic", None) not in ENGINE_MAGICS for position in positions)


def _directions(positions, client) -> set[Signal]:
    return {_side_from_position(client, position) for position in positions}


def _direction_allowed(client, positions, side: Signal) -> tuple[bool, str]:
    if _has_unknown_symbol_position(positions):
        return False, "A non-portfolio GBPUSD position is open."
    directions = _directions(positions, client)
    if directions and directions != {side}:
        return False, "An opposing GBPUSD portfolio position is open."
    return True, "Direction is compatible with current GBPUSD exposure."


def _approval(settings, label: str, side: Signal, volume: float) -> bool:
    if settings.mode is not Mode.APPROVAL:
        return True
    answer = input(f"Place {label} {side.value} {volume:.2f} lots GBPUSD? Type YES: ")
    return answer == "YES"


def _log_management(journal, symbol: str, message: str) -> None:
    journal.log_order(symbol, "MANAGE", 0.0, None, None, None, None, "UPDATED", message)


def _open_v4(client, journal, settings, account, setup, state: dict,
             effective_risk: float, params: V4Params) -> tuple[bool, str]:
    pip = pip_size(client, settings.symbol) or 0.0001
    stop_pips = min(
        max(params.stop_atr * setup.atr_price / pip, params.min_stop_pips),
        params.max_stop_pips,
    )
    target_pips = params.target_r * stop_pips
    risk_cfg = RiskConfig(
        enabled=True,
        risk_percent=min(effective_risk, 0.35),
        pip_value_per_lot=float(settings.pip_value_per_lot),
        max_lot=float(settings.max_lot),
    )
    volume = risk_lot(float(account.balance), stop_pips, risk_cfg)
    if not _approval(settings, "V4 Swing", setup.side, volume):
        return False, "V4 Swing skipped by user."
    ok, message = place_market_order(
        client, settings.symbol, setup.side, volume, stop_pips, target_pips,
        magic=V4_MAGIC, comment=f"{V4_COMMENT} {setup.variant}",
    )
    journal.log_order(
        settings.symbol, setup.side.value, volume, None, stop_pips, target_pips,
        None, "FILLED" if ok else "REJECTED", f"[V4_SWING] {message}",
    )
    if ok:
        state["last_signal_end"] = setup.signal_end.isoformat()
    return ok, message


def _open_satellite(client, journal, settings, account, setup, state: dict,
                    params: SatelliteParams, risk_percent: float) -> tuple[bool, str]:
    pip = pip_size(client, settings.symbol) or 0.0001
    stop_pips, target_pips = stop_and_target_pips(setup.atr_price, pip, params)
    risk_cfg = RiskConfig(
        enabled=True,
        risk_percent=risk_percent,
        pip_value_per_lot=float(settings.pip_value_per_lot),
        max_lot=float(settings.max_lot),
    )
    volume = risk_lot(float(account.balance), stop_pips, risk_cfg)
    if not _approval(settings, "Satellite Intraday", setup.side, volume):
        return False, "Satellite Intraday skipped by user."
    ok, message = place_market_order(
        client, settings.symbol, setup.side, volume, stop_pips, target_pips,
        magic=SATELLITE_MAGIC, comment=SATELLITE_COMMENT,
    )
    journal.log_order(
        settings.symbol, setup.side.value, volume, None, stop_pips, target_pips,
        None, "FILLED" if ok else "REJECTED", f"[SATELLITE_INTRADAY] {message}",
    )
    if ok:
        mark_satellite_entry(state, setup.signal_end)
    return ok, message


def _thinking_payload(v4_setup, satellite_setup, satellite_diagnostics, note: str) -> dict:
    ready = [setup for setup in (v4_setup, satellite_setup) if setup is not None]
    biases = {setup.side for setup in ready}
    bias = next(iter(biases)).value if len(biases) == 1 else (
        "MULTIPLE" if len(biases) > 1 else "NONE"
    )
    return {
        "timeframes": [
            {
                "label": "Swing anchor", "tf": "D1",
                "signal": v4_setup.side.value if v4_setup else Signal.WAIT.value,
                "confidence": 1.0 if v4_setup else 0.0,
                "reason": (
                    "Daily EMA trend is aligned with the current V4 H4 setup."
                    if v4_setup else "Waiting for the frozen V4 daily regime."
                ),
            },
            {
                "label": "Swing trigger", "tf": "H4",
                "signal": v4_setup.side.value if v4_setup else Signal.WAIT.value,
                "confidence": 1.0 if v4_setup else 0.0,
                "reason": v4_setup.reason if v4_setup else "No completed H4 V4 trigger.",
            },
            {
                "label": "Satellite trend", "tf": "H1",
                "signal": satellite_diagnostics.get("h1_signal", Signal.WAIT.value),
                "confidence": 1.0 if satellite_setup else 0.0,
                "reason": "H1 EMA20/EMA50 trend with ADX strength filter.",
            },
            {
                "label": "Satellite trigger", "tf": "M30",
                "signal": satellite_diagnostics.get("m30_signal", Signal.WAIT.value),
                "confidence": 1.0 if satellite_setup else 0.0,
                "reason": satellite_diagnostics.get("reason", "No satellite trigger."),
            },
        ],
        "bias": bias,
        "aligned": bool(ready) and len(biases) == 1,
        "setup_valid": bool(ready) and len(biases) == 1,
        "note": note,
        "engines": [
            {
                "name": "V4 Swing", "ready": bool(v4_setup),
                "bias": v4_setup.side.value if v4_setup else "NONE",
                "confidence": 1.0 if v4_setup else 0.0,
                "reason": v4_setup.reason if v4_setup else (
                    "Waiting for D1/H4 trend-expansion conditions."
                ),
            },
            {
                "name": "Satellite Intraday", "ready": bool(satellite_setup),
                "bias": satellite_setup.side.value if satellite_setup else "NONE",
                "confidence": 1.0 if satellite_setup else 0.0,
                "reason": satellite_diagnostics.get("reason", "Waiting for H1/M30 setup."),
            },
        ],
    }


def run_portfolio_cycle(client, journal, settings, account, risk_ok: bool,
                        active: bool, v4_params: V4Params = V4Params(),
                        satellite_params: SatelliteParams = SatelliteParams()) -> dict:
    """Manage and evaluate both engines during one application loop."""
    now = datetime.now(timezone.utc)
    v4_state = load_v4_state()
    satellite_state = load_satellite_state()

    for message in manage_v4_positions(client, settings.symbol, v4_state, v4_params):
        _log_management(journal, settings.symbol, f"[V4_SWING] {message}")
    for message in manage_satellite_positions(
        client, settings.symbol, satellite_state, satellite_params, now_utc=now
    ):
        _log_management(journal, settings.symbol, message)

    effective_v4_risk, drawdown, v4_paused = v4_effective_risk(account, v4_state, v4_params)
    satellite_risk = normalized_risk_percent(satellite_params)
    max_risk = _portfolio_max_risk()
    v4_setup, _ = evaluate_v4_setup(client, settings.symbol)
    satellite_setup, satellite_diagnostics = evaluate_satellite_setup(
        client, settings.symbol, satellite_params
    )
    note = (
        f"Portfolio DD {drawdown:.2f}%; V4 risk {effective_v4_risk:.2f}%; "
        f"satellite risk {satellite_risk:.2f}%; max combined risk {max_risk:.2f}%."
    )
    thinking = _thinking_payload(v4_setup, satellite_setup, satellite_diagnostics, note)

    if v4_setup:
        journal.log_signal(
            settings.symbol, v4_setup.side.value, f"[V4_SWING] {v4_setup.reason}",
            {"time": v4_setup.signal_end.isoformat(), "atr": v4_setup.atr_price,
             "engine": "V4_SWING"}, setup=1, filtered=0,
        )
    if satellite_setup:
        journal.log_signal(
            settings.symbol, satellite_setup.side.value,
            f"[SATELLITE_INTRADAY] {satellite_setup.reason}",
            {"time": satellite_setup.signal_end.isoformat(), "atr": satellite_setup.atr_price,
             "engine": "SATELLITE_INTRADAY"}, setup=1, filtered=0,
        )

    if settings.mode is Mode.READ_ONLY or not risk_ok or not active:
        save_v4_state(v4_state)
        save_satellite_state(satellite_state)
        return thinking

    positions = list(client.positions_get(symbol=settings.symbol) or [])
    if _has_unknown_symbol_position(positions):
        thinking["note"] += " New entries blocked by a non-portfolio GBPUSD position."
        save_v4_state(v4_state)
        save_satellite_state(satellite_state)
        return thinking

    if v4_setup and satellite_setup and v4_setup.side is not satellite_setup.side:
        satellite_setup = None
        thinking["note"] += " Conflicting satellite signal suppressed; V4 has priority."

    if v4_setup and not v4_paused and not _positions_for_magic(positions, V4_MAGIC):
        direction_ok, reason = _direction_allowed(client, positions, v4_setup.side)
        proposed = _configured_risk(positions, satellite_risk) + min(effective_v4_risk, 0.35)
        duplicate = v4_state.get("last_signal_end") == v4_setup.signal_end.isoformat()
        news = v4_news_blocked(now, v4_params)
        if direction_ok and proposed <= max_risk and not duplicate and not news:
            ok, _ = _open_v4(
                client, journal, settings, account, v4_setup, v4_state,
                effective_v4_risk, v4_params,
            )
            if ok:
                positions = list(client.positions_get(symbol=settings.symbol) or positions)
        else:
            thinking["note"] += (
                f" V4 entry blocked: {reason}; risk={proposed:.2f}%; "
                f"duplicate={duplicate}; news={news}."
            )

    if satellite_setup and not _positions_for_magic(positions, SATELLITE_MAGIC):
        direction_ok, reason = _direction_allowed(client, positions, satellite_setup.side)
        proposed = _configured_risk(positions, satellite_risk) + satellite_risk
        duplicate = duplicate_signal(satellite_state, satellite_setup.signal_end)
        daily_allowed = can_enter_today(satellite_state, satellite_setup.signal_end)
        spread = current_spread_pips(client, settings.symbol)
        spread_ok = spread is not None and spread <= satellite_params.max_spread_pips
        news = satellite_news_blocked(now, satellite_params)
        if (
            direction_ok and proposed <= max_risk and not duplicate
            and daily_allowed and spread_ok and not news
        ):
            _open_satellite(
                client, journal, settings, account, satellite_setup,
                satellite_state, satellite_params, satellite_risk,
            )
        else:
            thinking["note"] += (
                f" Satellite entry blocked: {reason}; risk={proposed:.2f}%; "
                f"duplicate={duplicate}; daily_allowed={daily_allowed}; "
                f"spread={spread}; news={news}."
            )

    save_v4_state(v4_state)
    save_satellite_state(satellite_state)
    return thinking
