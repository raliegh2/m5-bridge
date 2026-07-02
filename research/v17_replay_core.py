from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

import v13_expanded_assets_backtest as base
from v17_guard import GuardConfig, recovery_decision, risk_multiplier

STARTING_BALANCE = 5000.0


def _position_allowed(active, row, entry_time, basket_last, config):
    if len(active) >= config.max_positions:
        return False
    baskets = base.BASKETS.get(row.symbol, set())
    for basket in baskets:
        if any(basket in base.BASKETS.get(item["symbol"], set()) for item in active):
            return False
        last = basket_last.get(basket)
        if last is not None and (entry_time - last).total_seconds() < config.basket_cooldown_hours * 3600:
            return False
    risk = float(row.risk_percent)
    if sum(item["risk_percent"] for item in active) + risk > config.max_open_risk_percent + 1e-9:
        return False
    symbol_cap = config.gbpusd_precision_symbol_cap_percent if row.symbol == "GBPUSD" else config.generic_symbol_cap_percent
    if sum(item["risk_percent"] for item in active if item["symbol"] == row.symbol) + risk > symbol_cap + 1e-9:
        return False
    if row.symbol.startswith("GBP"):
        gbp = [item for item in active if item["symbol"].startswith("GBP")]
        directions = {item["side"] for item in gbp}
        directions.add(int(row.side))
        cap = config.mixed_gbp_cap_percent if len(directions) > 1 else config.aligned_gbp_cap_percent
        if sum(item["risk_percent"] for item in gbp) + risk > cap + 1e-9:
            return False
    return True


def _proxy(values):
    return type("Trade", (), values)


def replay(
    candidates,
    start,
    end,
    guard=GuardConfig(),
    *,
    recovery_probes=True,
    selective_multipliers: Mapping[str, float] | None = None,
):
    """Replay candidates through the unchanged portfolio gates.

    ``recovery_probes=False`` reproduces the legacy V17 guard exactly.
    ``selective_multipliers`` may increase validated swing engines only after
    their rolling guard reaches full-performance status. If an uplift would
    violate a portfolio cap, the trade falls back to its valid base risk rather
    than being rejected. The GBPUSD precision/satellite engine is never scaled.
    """
    data = candidates[(candidates["entry_time"] >= start) & (candidates["entry_time"] <= end)].copy()
    data = data.sort_values(["entry_time", "engine"]).reset_index(drop=True)
    balance = peak = STARTING_BALANCE
    max_dd = stress_dd = 0.0
    active, accepted = [], []
    histories, disabled_until, basket_last = {}, {}, {}
    probe_active_until = {}
    config = base.PortfolioConfig()
    policy = dict(selective_multipliers or {})

    def close_due(now):
        nonlocal balance, peak, max_dd
        for item in sorted([x for x in active if x["exit_time"] <= now], key=lambda x: x["exit_time"]):
            balance += item["risk_dollars"] * item["r_multiple"]
            histories.setdefault(item["engine"], []).append(float(item["r_multiple"]))
            if item.get("is_recovery_probe"):
                probe_active_until.pop(item["engine"], None)
            peak = max(peak, balance)
            max_dd = max(max_dd, (peak - balance) / peak * 100 if peak else 0.0)
            active.remove(item)

    for row in data.itertuples(index=False):
        entry_time = pd.Timestamp(row.entry_time)
        close_due(entry_time)
        engine = str(row.engine)

        if recovery_probes:
            decision = recovery_decision(
                engine,
                histories,
                entry_time,
                disabled_until,
                probe_active_until,
                guard,
            )
            multiplier = decision.multiplier
            guard_reason = decision.reason
            is_probe = decision.is_probe
        else:
            multiplier = risk_multiplier(engine, histories, entry_time, disabled_until, guard)
            guard_reason = "legacy"
            is_probe = False

        if multiplier <= 0:
            continue

        adjusted = row._asdict()
        adjusted["risk_percent"] = float(row.risk_percent) * multiplier
        base_proxy = _proxy(adjusted)
        if not _position_allowed(active, base_proxy, entry_time, basket_last, config):
            continue

        selective_multiplier = 1.0
        desired_multiplier = float(policy.get(engine, 1.0))
        if (
            recovery_probes
            and engine != "GBPUSD_V10_PRECISION"
            and guard_reason == "full_performance"
            and desired_multiplier != 1.0
        ):
            desired = dict(adjusted)
            desired["risk_percent"] = float(adjusted["risk_percent"]) * desired_multiplier
            desired_proxy = _proxy(desired)
            if desired_multiplier < 1.0 or _position_allowed(active, desired_proxy, entry_time, basket_last, config):
                adjusted = desired
                selective_multiplier = desired_multiplier

        risk_dollars = balance * float(adjusted["risk_percent"]) / 100.0
        item = {
            **adjusted,
            "risk_dollars": risk_dollars,
            "guard_reason": guard_reason,
            "guard_multiplier": multiplier,
            "selective_multiplier": selective_multiplier,
            "is_recovery_probe": is_probe,
        }
        active.append(item)
        accepted.append(item)

        if is_probe:
            disabled_until.pop(engine, None)
            probe_active_until[engine] = pd.Timestamp(row.exit_time)

        for basket in base.BASKETS.get(row.symbol, set()):
            basket_last[basket] = entry_time
        stressed = balance - sum(x["risk_dollars"] for x in active)
        stress_dd = max(stress_dd, (peak - stressed) / peak * 100 if peak else 0.0)

    close_due(pd.Timestamp.max.tz_localize("UTC"))
    return _summarize(accepted, start, end, balance, max_dd, stress_dd)


def _summarize(accepted, start, end, balance, max_dd, stress_dd):
    frame = pd.DataFrame(accepted)
    if frame.empty:
        gross_income = gross_loss = 0.0
    else:
        pnl = frame["risk_dollars"] * frame["r_multiple"]
        gross_income = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())
    summary = {
        "start": start.isoformat(), "end": end.isoformat(),
        "gross_income": gross_income, "gross_loss": gross_loss,
        "net_profit": balance - STARTING_BALANCE,
        "return_percent": (balance / STARTING_BALANCE - 1) * 100,
        "average_monthly_net": (balance - STARTING_BALANCE) / max(1.0, (end - start).days / 30.4375),
        "trades": int(len(frame)),
        "profit_factor": gross_income / gross_loss if gross_loss else float("inf"),
        "max_drawdown_percent": max_dd,
        "stress_drawdown_percent": stress_dd,
    }
    return summary, frame
