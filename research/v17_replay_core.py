from __future__ import annotations

import pandas as pd

import v13_expanded_assets_backtest as base
from v17_guard import GuardConfig, risk_multiplier

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


def replay(candidates, start, end, guard=GuardConfig()):
    data = candidates[(candidates["entry_time"] >= start) & (candidates["entry_time"] <= end)].copy()
    data = data.sort_values(["entry_time", "engine"]).reset_index(drop=True)
    balance = peak = STARTING_BALANCE
    max_dd = stress_dd = 0.0
    active, accepted = [], []
    histories, disabled_until, basket_last = {}, {}, {}
    config = base.PortfolioConfig()

    def close_due(now):
        nonlocal balance, peak, max_dd
        for item in sorted([x for x in active if x["exit_time"] <= now], key=lambda x: x["exit_time"]):
            balance += item["risk_dollars"] * item["r_multiple"]
            histories.setdefault(item["engine"], []).append(float(item["r_multiple"]))
            peak = max(peak, balance)
            max_dd = max(max_dd, (peak - balance) / peak * 100 if peak else 0.0)
            active.remove(item)

    for row in data.itertuples(index=False):
        entry_time = pd.Timestamp(row.entry_time)
        close_due(entry_time)
        multiplier = risk_multiplier(str(row.engine), histories, entry_time, disabled_until, guard)
        if multiplier <= 0:
            continue
        adjusted = row._asdict()
        adjusted["risk_percent"] = float(row.risk_percent) * multiplier
        proxy = type("Trade", (), adjusted)
        if not _position_allowed(active, proxy, entry_time, basket_last, config):
            continue
        risk_dollars = balance * proxy.risk_percent / 100.0
        item = {**adjusted, "risk_dollars": risk_dollars}
        active.append(item)
        accepted.append(item)
        for basket in base.BASKETS.get(proxy.symbol, set()):
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
