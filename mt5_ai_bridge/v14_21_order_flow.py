"""Observe-only broker order-flow diagnostics for the live MT5 runner.

Spot FX has no centralized exchange tape.  These readings therefore use only
what the connected broker exposes: recent quote-tick direction, current spread,
and market depth when the symbol supports a depth-of-market subscription.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any


def _field(row: Any, name: str, default: Any = 0) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return getattr(row, name, default)


def _pip_size(info: Any) -> float:
    point = float(getattr(info, "point", 0.0) or 0.0)
    digits = int(getattr(info, "digits", 0) or 0)
    return point * 10.0 if digits in {3, 5} else point


def _tick_time_msc(row: Any) -> int:
    return int(
        _field(row, "time_msc", 0)
        or int(_field(row, "time", 0) or 0) * 1000
    )


def _window_pressure(
    tick_rows: list[Any],
    *,
    cutoff_msc: int,
    pip_size: float,
) -> dict[str, Any]:
    """Calculate quote pressure and absorption proxy for one time window."""
    valid: list[tuple[int, float, float, float]] = []
    for row in tick_rows:
        timestamp = _tick_time_msc(row)
        bid = float(_field(row, "bid", 0.0) or 0.0)
        ask = float(_field(row, "ask", 0.0) or 0.0)
        if timestamp < cutoff_msc or bid <= 0 or ask < bid:
            continue
        weight = float(
            _field(row, "volume_real", 0.0)
            or _field(row, "volume", 0.0)
            or 1.0
        )
        valid.append((timestamp, (bid + ask) / 2.0, ask - bid, weight))

    up_weight = 0.0
    down_weight = 0.0
    gross_move = 0.0
    directional_moves = 0
    for previous, current in zip(valid, valid[1:]):
        delta = current[1] - previous[1]
        if delta > 0:
            up_weight += current[3]
            directional_moves += 1
        elif delta < 0:
            down_weight += current[3]
            directional_moves += 1
        gross_move += abs(delta)

    directional_weight = up_weight + down_weight
    imbalance = (
        (up_weight - down_weight) / directional_weight
        if directional_weight > 0
        else 0.0
    )
    net_move = valid[-1][1] - valid[0][1] if len(valid) >= 2 else 0.0
    efficiency = min(1.0, abs(net_move) / gross_move) if gross_move > 0 else 0.0
    # Strong one-sided quote pressure with little net travel is an observable
    # absorption proxy, not proof of hidden or centralized resting orders.
    absorption_score = abs(imbalance) * (1.0 - efficiency)
    if absorption_score < 0.25:
        absorption = "NONE"
    elif imbalance > 0:
        absorption = "SELL_SIDE_ABSORPTION_PROXY"
    elif imbalance < 0:
        absorption = "BUY_SIDE_ABSORPTION_PROXY"
    else:
        absorption = "NONE"
    spreads = [
        spread / pip_size
        for _timestamp, _mid, spread, _weight in valid
        if pip_size > 0
    ]
    return {
        "imbalance": round(imbalance, 4),
        "buy_pressure_percent": round((imbalance + 1.0) * 50.0, 1),
        "sell_pressure_percent": round((1.0 - imbalance) * 50.0, 1),
        "tick_count": len(valid),
        "directional_moves": directional_moves,
        "net_move_pips": (
            round(net_move / pip_size, 3) if pip_size > 0 else None
        ),
        "path_efficiency": round(efficiency, 4),
        "absorption_score": round(absorption_score, 4),
        "absorption": absorption,
        "median_spread_pips": (
            round(float(median(spreads)), 3) if spreads else None
        ),
    }


def _market_depth(client: Any, symbol: str) -> dict[str, Any]:
    add = getattr(client, "market_book_add", None)
    get = getattr(client, "market_book_get", None)
    release = getattr(client, "market_book_release", None)
    if not callable(add) or not callable(get):
        return {"available": False, "imbalance": None, "levels": 0}

    subscribed = False
    try:
        subscribed = bool(add(symbol))
        if not subscribed:
            return {"available": False, "imbalance": None, "levels": 0}
        book = get(symbol) or []
        buy_type = int(getattr(client, "BOOK_TYPE_BUY", 2))
        sell_type = int(getattr(client, "BOOK_TYPE_SELL", 1))
        buy_volume = 0.0
        sell_volume = 0.0
        for level in book:
            level_type = int(_field(level, "type", -1) or -1)
            volume = float(
                _field(level, "volume_dbl", 0.0)
                or _field(level, "volume", 0.0)
                or 0.0
            )
            if level_type == buy_type:
                buy_volume += volume
            elif level_type == sell_type:
                sell_volume += volume
        total = buy_volume + sell_volume
        return {
            "available": bool(book),
            "imbalance": round((buy_volume - sell_volume) / total, 4)
            if total > 0
            else None,
            "levels": len(book),
        }
    except Exception:  # noqa: BLE001 - broker feature is optional
        return {"available": False, "imbalance": None, "levels": 0}
    finally:
        if subscribed and callable(release):
            try:
                release(symbol)
            except Exception:  # noqa: BLE001
                pass


def measure_order_flow(
    client: Any,
    canonical_symbol: str,
    broker_symbol: str,
    *,
    now: datetime | None = None,
    lookback_seconds: int = 900,
    max_ticks: int = 4096,
) -> dict[str, Any]:
    """Measure multi-horizon broker-local pressure without gating execution."""
    measured_at = now or datetime.now(timezone.utc)
    copy_ticks = getattr(client, "copy_ticks_from", None)
    if not callable(copy_ticks):
        return {
            "symbol": canonical_symbol,
            "broker_symbol": broker_symbol,
            "state": "UNAVAILABLE",
            "reason": "Broker client does not expose recent ticks.",
            "updated_at": measured_at.isoformat(),
        }

    try:
        flags = int(getattr(client, "COPY_TICKS_ALL", -1))
        ticks = copy_ticks(
            broker_symbol,
            measured_at - timedelta(seconds=max(30, int(lookback_seconds))),
            max(10, int(max_ticks)),
            flags,
        )
        tick_rows = [] if ticks is None else list(ticks)
        info = client.symbol_info(broker_symbol)
        latest = client.symbol_info_tick(broker_symbol)
        if len(tick_rows) < 2:
            return {
                "symbol": canonical_symbol,
                "broker_symbol": broker_symbol,
                "state": "NO_TICKS",
                "reason": "Fewer than two broker ticks were returned.",
                "tick_count": len(tick_rows),
                "updated_at": measured_at.isoformat(),
            }

        tick_rows.sort(
            key=lambda row: int(
                _field(row, "time_msc", 0)
                or int(_field(row, "time", 0) or 0) * 1000
            )
        )
        pip = _pip_size(info)
        measured_msc = int(measured_at.timestamp() * 1000)
        window_specs = {
            "30s": (30, 0.50),
            "2m": (120, 0.30),
            "15m": (900, 0.20),
        }
        pressure_windows = {
            name: _window_pressure(
                tick_rows,
                cutoff_msc=measured_msc - seconds * 1000,
                pip_size=pip,
            )
            for name, (seconds, _weight) in window_specs.items()
        }
        usable = [
            (pressure_windows[name], weight)
            for name, (_seconds, weight) in window_specs.items()
            if pressure_windows[name]["directional_moves"] > 0
        ]
        available_weight = sum(weight for _window, weight in usable)
        imbalance = (
            sum(
                float(window["imbalance"]) * weight
                for window, weight in usable
            )
            / available_weight
            if available_weight > 0
            else 0.0
        )
        if imbalance >= 0.15:
            state = "BULLISH_PRESSURE"
        elif imbalance <= -0.15:
            state = "BEARISH_PRESSURE"
        else:
            state = "BALANCED"

        bid = float(getattr(latest, "bid", 0.0) or 0.0)
        ask = float(getattr(latest, "ask", 0.0) or 0.0)
        spread_pips = (ask - bid) / pip if pip > 0 and ask >= bid else None
        baseline_spread = pressure_windows["15m"]["median_spread_pips"]
        recent_spread = pressure_windows["30s"]["median_spread_pips"]
        spread_shock_ratio = (
            float(recent_spread) / float(baseline_spread)
            if recent_spread is not None
            and baseline_spread is not None
            and float(baseline_spread) > 0
            else None
        )
        if spread_shock_ratio is None:
            spread_state = "UNAVAILABLE"
        elif spread_shock_ratio >= 2.0:
            spread_state = "SEVERE_SHOCK"
        elif spread_shock_ratio >= 1.5:
            spread_state = "ELEVATED"
        else:
            spread_state = "NORMAL"
        strongest_absorption = max(
            pressure_windows.items(),
            key=lambda item: float(item[1]["absorption_score"]),
        )
        depth = _market_depth(client, broker_symbol)
        return {
            "symbol": canonical_symbol,
            "broker_symbol": broker_symbol,
            "state": state,
            "imbalance": round(imbalance, 4),
            "buy_pressure_percent": round((imbalance + 1.0) * 50.0, 1),
            "sell_pressure_percent": round((1.0 - imbalance) * 50.0, 1),
            "directional_moves": sum(
                int(window["directional_moves"])
                for window in pressure_windows.values()
            ),
            "tick_count": len(tick_rows),
            "pressure_windows": pressure_windows,
            "composite_weights": {
                name: weight
                for name, (_seconds, weight) in window_specs.items()
            },
            "absorption": {
                "state": strongest_absorption[1]["absorption"],
                "window": strongest_absorption[0],
                "score": strongest_absorption[1]["absorption_score"],
                "note": "Broker quote-path proxy; not centralized resting orders.",
            },
            "spread_pips": round(spread_pips, 2)
            if spread_pips is not None
            else None,
            "spread_shock": {
                "state": spread_state,
                "ratio": (
                    round(spread_shock_ratio, 3)
                    if spread_shock_ratio is not None
                    else None
                ),
                "recent_30s_median_pips": recent_spread,
                "baseline_15m_median_pips": baseline_spread,
            },
            "market_depth": depth,
            "mode": "OBSERVE_ONLY",
            "source": "BROKER_LOCAL_MT5_PROXY",
            "updated_at": measured_at.isoformat(),
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must not stop trading
        return {
            "symbol": canonical_symbol,
            "broker_symbol": broker_symbol,
            "state": "ERROR",
            "reason": f"{type(exc).__name__}: {exc}",
            "updated_at": measured_at.isoformat(),
        }


class OrderFlowMonitor:
    """Cache broker measurements so a one-second heartbeat stays inexpensive."""

    def __init__(self, refresh_seconds: float = 15.0) -> None:
        self.refresh_seconds = max(5.0, float(refresh_seconds))
        self._last_refresh = 0.0
        self._cache: list[dict[str, Any]] = []

    def snapshot(
        self,
        client: Any,
        broker_map: dict[str, str],
    ) -> list[dict[str, Any]]:
        current = time.monotonic()
        if self._cache and current - self._last_refresh < self.refresh_seconds:
            return list(self._cache)
        measured_at = datetime.now(timezone.utc)
        self._cache = [
            measure_order_flow(
                client,
                canonical_symbol=symbol,
                broker_symbol=broker_symbol,
                now=measured_at,
            )
            for symbol, broker_symbol in broker_map.items()
        ]
        self._last_refresh = current
        return list(self._cache)
