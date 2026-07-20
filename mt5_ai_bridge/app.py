"""Application entrypoint: wires the pipeline together and runs the loop.

Pipeline per iteration:
    MT5 -> Indicators -> Strategy -> Risk -> Plan -> Execute -> Trail -> Journal

Console: with CONSOLE_STATUS on, the terminal shows a single updating status
line plus a concise line each time a trade opens. Full detail -> logs/bridge.log.

Trend model (MULTI_BOOK): the fast ENTRY read is TIMEFRAME (M15); a trade only
fires when the CONFIRMATION timeframes (M30 + H4 + D1) all agree on the same
direction (REQUIRE_TREND_ALIGNMENT). This makes trading smoother and takes
FEWER — not more — trades. Stops are ATR-based; each position is sized by the
fixed-fractional 1-2% rule. Winners trail.

Remote control: the dashboard's Start/Pause buttons POST to the local control
server, toggling a shared ControlState. The loop opens NEW trades only while
ACTIVE; existing trades keep trailing regardless.

Near-realtime dashboard: each loop the bot rewrites both the HTML shell and a
JSON snapshot; the page polls /data and patches itself in place.

Bot thinking + signal breakdown: the dashboard shows each timeframe's read with
a plain-English 'why', and separates raw analyses from valid setups, executed
trades and filtered-out setups — so raw signal counts are never mistaken for
trade entries.
"""

import time
import webbrowser
from datetime import datetime, timezone
from typing import Callable, Optional

from .books import build_books, desired_positions, trend_bias
from .config import Settings, load_settings
from .control import ControlState, start_control_server
from .dashboard import (est_now, write_dashboard_data, write_dashboard_live,
                        write_status)
from .enums import Mode, Signal
from .execution import pip_size, pip_value_per_lot, place_market_order
from .explain import UNAVAILABLE, explain_market
from .indicators import market_snapshot
from .journal import Journal
from .logging_config import get_logger, setup_logging
from .mt5_client import create_client
from .prop import PropGuard
from .planner import (SessionConfig, SizingConfig, StaggerConfig, StyleConfig,
                      build_plan, is_ny_session, position_size, stagger)
from .reasoning import ReasoningConfig, ReasoningStrategy
from .risk_engine import DailyLossTracker, RiskLimits, check_risk
from .sizing import AtrConfig, RiskConfig, atr_stops, risk_lot
from .strategy import evaluate_strategy
from .trade_manager import close_position, modify_position_sl, trailing_sl

log = get_logger("app")


def make_strategy(settings: Settings) -> Callable:
    if settings.strategy == "reasoning":
        return ReasoningStrategy(ReasoningConfig(
            threshold=settings.reasoning_threshold,
            rsi_overbought=settings.rsi_overbought,
            rsi_oversold=settings.rsi_oversold,
        ))
    return evaluate_strategy


def make_planner_configs(settings: Settings):
    session = SessionConfig(settings.ny_start_hour, settings.ny_end_hour)
    sizing = SizingConfig(base_lot=settings.lot_size,
                          ny_multiplier=settings.ny_size_multiplier)
    style = StyleConfig(
        swing_confidence=settings.swing_confidence,
        intraday_sl_pips=settings.intraday_sl_pips,
        intraday_tp_pips=settings.intraday_tp_pips,
        swing_sl_pips=settings.swing_sl_pips,
        swing_tp_pips=settings.swing_tp_pips,
    )
    stagger_cfg = StaggerConfig(tp_step=settings.tp_stagger_step,
                                sl_step=settings.sl_stagger_step,
                                sl_floor=settings.sl_floor_pips)
    return session, sizing, style, stagger_cfg


def _atr_cfg(settings) -> AtrConfig:
    return AtrConfig(enabled=settings.atr_enabled, period=settings.atr_period,
                     sl_mult=settings.atr_sl_mult, tp_mult=settings.atr_tp_mult,
                     min_sl_pips=settings.atr_min_sl_pips,
                     max_sl_pips=settings.atr_max_sl_pips)


def _risk_cfg(settings, risk_percent: Optional[float] = None,
              pip_value: Optional[float] = None) -> RiskConfig:
    return RiskConfig(enabled=settings.risk_based_sizing,
                      risk_percent=(settings.risk_percent if risk_percent is None
                                    else risk_percent),
                      pip_value_per_lot=(settings.pip_value_per_lot
                                         if pip_value is None else pip_value),
                      max_lot=settings.max_lot)


def _dual_engine_state(decisions: dict, settings) -> dict:
    """Return independent intraday and swing setup states.

    Intraday needs M15+M30 agreement and is blocked by strong H4 opposition.
    Swing needs H4+D1 agreement plus matching M15+M30 entry timing. No engine
    treats a weak/disagreeing read as a probe trade.
    """
    def get(tf):
        return decisions.get(tf)

    entry = get(settings.timeframe)
    mid = get(settings.trend_tf_mid)
    high = get(settings.swing_tf_high)
    higher = get(settings.swing_tf_higher)

    intraday_bias = trend_bias(
        *(d.signal for d in (entry, mid) if d is not None)
    ) if entry is not None and mid is not None else None
    h4_opposes = bool(
        intraday_bias is not None and high is not None
        and high.signal.is_trade and high.signal is not intraday_bias
        and high.confidence >= settings.strong_trend_confidence
    )
    intraday_valid = bool(intraday_bias is not None and not h4_opposes)

    swing_bias = trend_bias(high.signal, higher.signal) \
        if high is not None and higher is not None else None
    swing_trigger = trend_bias(entry.signal, mid.signal) \
        if entry is not None and mid is not None else None
    swing_valid = bool(swing_bias is not None and swing_trigger is swing_bias)

    intraday_conf = min(entry.confidence, mid.confidence) \
        if intraday_valid else 0.0
    swing_conf = min(entry.confidence, mid.confidence,
                     high.confidence, higher.confidence) \
        if swing_valid else 0.0

    return {
        "intraday": {
            "valid": intraday_valid,
            "bias": intraday_bias,
            "confidence": intraday_conf,
            "reason": ("M15 and M30 agree; no strong H4 opposition."
                       if intraday_valid else
                       ("Blocked by a strong opposing H4 trend."
                        if h4_opposes else "Waiting for M15 and M30 to agree.")),
        },
        "swing": {
            "valid": swing_valid,
            "bias": swing_bias,
            "confidence": swing_conf,
            "reason": ("H4 and D1 agree, with matching M30/M15 timing."
                       if swing_valid else
                       "Waiting for H4/D1 trend and M30/M15 timing to agree."),
        },
    }


def _data_path(settings) -> str:
    """The JSON snapshot path derived from the dashboard HTML path."""
    p = settings.dashboard_path
    return (p[:-5] + ".json") if p.lower().endswith(".html") else (p + ".json")


def _account_kind(account) -> str:
    """MT5 account trade_mode -> DEMO / CONTEST / REAL / UNKNOWN."""
    tm = getattr(account, "trade_mode", None) if account is not None else None
    return {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(tm, "UNKNOWN")


def _is_real_account(account) -> bool:
    """True only when the broker explicitly reports a REAL (live) account."""
    return getattr(account, "trade_mode", None) == 2


def _subscribe_symbols(client, symbols) -> None:
    """Enable every configured symbol in Market Watch.

    MT5 only returns bars/ticks for symbols selected in Market Watch, so a
    symbol that is not subscribed shows up as "no market data" on the dashboard
    even though the connection is fine. We select them up front. Any that fail
    are almost always a NAME MISMATCH (e.g. the broker uses GBPUSD.r) -- we log
    those loudly so they can be corrected in SYMBOLS.
    """
    missing = []
    for sym in symbols:
        ok = False
        try:
            if hasattr(client, "symbol_select"):
                ok = bool(client.symbol_select(sym, True))
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            missing.append(sym)
    if missing:
        log.warning("Could not enable these symbols in Market Watch -- check the "
                    "EXACT broker names (some add a suffix like .r/.m/.pro) and "
                    "fix SYMBOLS in your .env: %s", ", ".join(missing))


def connect(client, settings: Settings) -> None:
    if not settings.has_credentials:
        raise RuntimeError(
            "Missing MT5 credentials. Set MT5_LOGIN / MT5_PASSWORD / MT5_SERVER "
            "in your .env (see .env.example)."
        )
    if not client.initialize():
        raise RuntimeError(f"MT5 initialize failed: {client.last_error()}")
    if not client.login(settings.login, settings.password, settings.server):
        raise RuntimeError(f"MT5 login failed: {client.last_error()}")
    _subscribe_symbols(client, settings.symbols)
    log.info("Connected to MT5 | mode=%s | strategy=%s | symbols=%s",
             settings.mode.value, settings.strategy, ",".join(settings.symbols))


def account_snapshot(client, symbol: str, symbols=None) -> dict:
    account = client.account_info()
    positions = client.positions_get() or []
    tick = client.symbol_info_tick(symbol)

    return {
        "symbol": symbol,
        "symbols": list(symbols) if symbols else [symbol],
        "login": account.login,
        "balance": account.balance,
        "equity": account.equity,
        "margin": account.margin,
        "free_margin": account.margin_free,
        "profit": account.profit,
        "bid": tick.bid if tick else None,
        "ask": tick.ask if tick else None,
        "pip_size": pip_size(client, symbol),
        "open_positions": len(positions),
        "positions": [
            {
                "ticket": p.ticket, "symbol": p.symbol,
                "type": "BUY" if p.type == client.POSITION_TYPE_BUY else "SELL",
                "volume": p.volume, "profit": p.profit,
                "price_open": getattr(p, "price_open", None),
                "price_current": getattr(p, "price_current", None),
                "sl": p.sl, "tp": p.tp,
                # Per-position pip size so the dashboard computes pips with the
                # RIGHT scale for each instrument (gold/JPY differ from FX).
                "pip_size": pip_size(client, p.symbol),
            }
            for p in positions
        ],
    }


def _bot_thinking(client, settings, strategy_fn, symbol=None) -> Optional[dict]:
    """What the bot sees now, for the dashboard.

    Reads the fast ENTRY timeframe (M15) and the CONFIRMATION timeframes
    (M30 + H4 + D1). Each row carries a plain-English 'why'. Returns the
    confirmed trend, whether it's aligned, and whether the current entry is a
    valid setup. Returns None when the multi-book engine is off."""
    if not settings.multi_book:
        return None

    symbol = symbol or settings.symbol
    entry_tf = settings.timeframe
    confirm_tfs = list(settings.confirm_timeframes)

    def evaluate(tf, label):
        try:
            snap = market_snapshot(client, symbol, tf, settings.atr_period)
            decision = strategy_fn(snap)
        except Exception as exc:  # noqa: BLE001
            log.warning("Dashboard analysis unavailable for %s: %s", tf, exc)
            return None, {
                "tf": tf, "label": label, "signal": Signal.WAIT.value,
                "confidence": 0.0,
                "reason": f"{UNAVAILABLE} — market data could not be read.",
            }
        view = {
            "tf": tf, "label": label,
            "signal": decision.signal.value,
            "confidence": round(float(decision.confidence), 2),
            "reason": explain_market(snap),
        }
        return decision, view

    views, decisions = [], {}
    labels = ((entry_tf, "Entry"),
              (settings.trend_tf_mid, "Intraday confirm"),
              (settings.swing_tf_high, "Swing trend"),
              (settings.swing_tf_higher, "Swing anchor"))
    for tf, label in labels:
        decision, view = evaluate(tf, label)
        if view is not None:
            views.append(view)
        if decision is not None:
            decisions[tf] = decision

    state = _dual_engine_state(decisions, settings)
    active = [name for name, item in state.items() if item["valid"]]
    biases = {state[name]["bias"] for name in active}
    bias = next(iter(biases)) if len(biases) == 1 else None
    aligned = bool(active)

    setup_valid = aligned

    note = " ".join(
        f"{name.title()}: "
        f"{'READY ' + item['bias'].value if item['valid'] else 'WAITING'} — "
        f"{item['reason']}"
        for name, item in state.items()
    )

    return {
        "timeframes": views,
        "bias": bias.value if bias else ("MULTIPLE" if len(biases) > 1 else "NONE"),
        "aligned": aligned,
        "setup_valid": setup_valid,
        "note": note,
        "engines": [
            {"name": name.title(), "ready": item["valid"],
             "bias": item["bias"].value if item["bias"] else "NONE",
             "confidence": round(float(item["confidence"]), 2),
             "reason": item["reason"]}
            for name, item in state.items()
        ],
    }


def _count_side(client, positions, symbol: str, side: Signal,
                magic: Optional[int] = None) -> int:
    target = client.POSITION_TYPE_BUY if side is Signal.BUY else client.POSITION_TYPE_SELL
    return sum(1 for p in positions
               if p.symbol == symbol and p.type == target
               and (magic is None or getattr(p, "magic", None) == magic))


def _announce_open(settings, side, volume, book, sl_pips, tp_pips,
                   symbol=None) -> None:
    if not settings.console_status:
        return
    print(f"\n[{est_now()}] OPEN {side} {volume} {symbol or settings.symbol} "
          f"({book})  SL {sl_pips:g}p / TP {tp_pips:g}p")


def _print_status(client, settings, active: bool = True) -> None:
    if not settings.console_status:
        return
    try:
        account = client.account_info()
        positions = client.positions_get() or []
    except Exception:  # noqa: BLE001
        return
    lots = sum(getattr(p, "volume", 0.0) for p in positions)
    pl = (account.equity - account.balance) if account else 0.0
    state = "ACTIVE" if active else "PAUSED"
    print(f"\r{est_now()} | {settings.symbol} | {state} | active {len(positions)} | "
          f"lots {lots:.2f} | P/L {pl:+.2f}      ", end="", flush=True)


def _status(settings, message: str) -> None:
    if not settings.write_dashboard:
        return
    try:
        write_status(settings.dashboard_path, message,
                     settings.dashboard_refresh_seconds)
    except Exception:  # noqa: BLE001
        pass


def _refresh_dashboard(client, journal, settings, control=None,
                       thinking=None, prop=None) -> None:
    if not settings.write_dashboard:
        return
    try:
        snap = account_snapshot(client, settings.symbols[0], settings.symbols)
        write_dashboard_live(journal, snap, settings.dashboard_path,
                             settings.dashboard_refresh_seconds,
                             control=control, thinking=thinking,
                             port=settings.dashboard_port, prop=prop)
        write_dashboard_data(journal, snap, _data_path(settings),
                             settings.dashboard_refresh_seconds,
                             control=control, thinking=thinking, prop=prop)
    except Exception as exc:  # noqa: BLE001
        log.warning("Dashboard refresh failed: %s", exc)


def _update_trailing_stops(client, settings, symbol=None) -> None:
    symbol = symbol or settings.symbol
    pip = pip_size(client, symbol)
    if not pip:
        return
    for p in client.positions_get(symbol=symbol) or []:
        is_buy = p.type == client.POSITION_TYPE_BUY
        new_sl = trailing_sl(is_buy, getattr(p, "price_open", 0.0),
                             getattr(p, "price_current", 0.0), p.sl, pip,
                             settings.trail_start_pips, settings.trail_distance_pips)
        if new_sl is None:
            continue
        ok, message = modify_position_sl(client, p, new_sl)
        (log.info if ok else log.warning)("Trail: %s", message)


def _signal_flags(decision, thinking) -> tuple:
    """Per-analysis (setup, filtered) flags for the journal.

    setup=1 when this loop is a valid trade setup; filtered=1 when the entry
    wanted to trade but a filter (trend not aligned / veto) blocked it."""
    if thinking is not None:
        setup_ok = bool(thinking.get("setup_valid"))
        setup = 1 if setup_ok else 0
        filtered = 1 if (decision.signal.is_trade and not setup_ok) else 0
        return setup, filtered
    # Single-book fallback: a trade signal is itself the setup.
    return (1 if decision.signal.is_trade else 0), 0


def _prop_off_payload(settings, account) -> dict:
    """A prop status dict for when challenge mode is OFF, so the dashboard
    panel can still render (greyed) and offer the enable toggle."""
    return {
        "enabled": False, "status": "OFF", "allow_trading": True,
        "risk_scale": 1.0,
        "start_balance": round(float(getattr(account, "balance", 0.0) or 0.0), 2),
        "equity": round(float(getattr(account, "equity", 0.0) or 0.0), 2),
        "profit_pct": 0.0, "profit_target_pct": settings.prop_profit_target_pct,
        "daily_loss_pct": 0.0, "max_daily_loss_pct": settings.prop_max_daily_loss_pct,
        "total_dd_pct": 0.0, "max_total_loss_pct": settings.prop_max_total_loss_pct,
        "trailing": settings.prop_trailing,
    }


def _pick_primary(client, settings) -> str:
    """The symbol the dashboard's \"What the bot sees now\" panel reads.

    Normally the first configured symbol, but if that one returns no bars (e.g.
    it is not offered under that exact name by the broker) we fall through to
    the first symbol that DOES have data, so the panel is never stuck showing
    \"no market data\" while other symbols trade fine.
    """
    for sym in settings.symbols:
        try:
            if market_snapshot(client, sym, settings.timeframe,
                               settings.atr_period) is not None:
                return sym
        except Exception:  # noqa: BLE001
            continue
    return settings.symbols[0]


def _run_once(client, journal, settings, strategy_fn, limits, tracker,
              planner_cfgs, state: Optional[ControlState] = None,
              prop_guard=None) -> None:
    active = state.is_active() if state is not None else True

    account = client.account_info()
    if account is None:
        raise RuntimeError("account_info() returned None (disconnected?)")
    positions = client.positions_get() or []

    day_loss = tracker.update(account.equity)
    risk = check_risk(account, positions, limits, daily_loss=day_loss)
    journal.log_risk_event(risk.ok, risk.message, account.balance,
                           account.equity, len(positions))
    log.info("Risk: %s | account=%s | day_loss=%.2f | active=%s",
             risk.message, _account_kind(account), day_loss, active)

    # Fast ENTRY read on the PRIMARY symbol (drives the dashboard signal view).
    # Auto-pick the first symbol that actually has data so the panel never
    # sticks on a symbol the broker does not offer under that name.
    primary = _pick_primary(client, settings)
    market = market_snapshot(client, primary, settings.timeframe,
                             settings.atr_period)
    decision = strategy_fn(market)

    # Higher-timeframe confirmation view (for the dashboard + setup/filter flags).
    thinking = _bot_thinking(client, settings, strategy_fn, symbol=primary)
    setup_flag, filtered_flag = _signal_flags(decision, thinking)

    journal.log_signal(primary, decision.signal.value,
                       decision.reason, market, setup=setup_flag,
                       filtered=filtered_flag)
    log.info("Signal[%s]: %s (%s) confidence=%s setup=%s filtered=%s",
             primary, decision.signal.value, decision.reason, decision.confidence,
             setup_flag, filtered_flag)

    # Safety guard: never place AUTOMATIC trades on a REAL account while
    # REQUIRE_DEMO is on. Existing positions still trail; the dashboard still runs.
    demo_ok = not (settings.require_demo and _is_real_account(account))
    if not demo_ok:
        log.warning("Trading blocked: REAL account with REQUIRE_DEMO on. "
                    "Use a demo account (or set REQUIRE_DEMO=false to override).")

    # Prop-firm challenge guard: gate trading + scale risk near the limits.
    # Prop mode can be toggled live from the dashboard (state.is_prop());
    # when OFF we still emit a payload so the panel always shows its status.
    prop_on = state.is_prop() if state is not None else settings.prop_firm
    if prop_on and prop_guard is not None:
        prop = prop_guard.update(account.balance, account.equity)
    else:
        prop = _prop_off_payload(settings, account)
    prop_ok = prop["allow_trading"] if prop["enabled"] else True
    risk_scale = prop["risk_scale"] if prop["enabled"] else 1.0
    if prop["enabled"] and not prop_ok:
        log.warning("Prop guard [%s]: new trades paused | equity %.2f | "
                    "daily %.2f%%/%.1f%% | total DD %.2f%%/%.1f%%.",
                    prop["status"], prop["equity"], prop["daily_loss_pct"],
                    prop["max_daily_loss_pct"], prop["total_dd_pct"],
                    prop["max_total_loss_pct"])

    # Open NEW trades only while trading is ACTIVE (remote pause honoured).
    # Every symbol shares ONE account: the combined open-risk ceiling and the
    # account-level dollar/drawdown limits bound total risk across all pairs.
    if settings.mode is not Mode.READ_ONLY and risk.ok and active and demo_ok \
            and prop_ok:
        if settings.multi_book:
            for sym in settings.symbols:
                # Fetch positions fresh per symbol so the combined-risk ceiling
                # sees trades opened for earlier symbols this same iteration.
                _run_books(client, journal, settings, strategy_fn, planner_cfgs,
                           client.positions_get() or [], account=account,
                           symbol=sym, risk_scale=risk_scale)
        elif decision.signal.is_trade:
            _consider_trade(client, journal, settings, decision, positions,
                            planner_cfgs)

    if settings.mode is Mode.APPROVAL and positions:
        _maybe_close(client, journal, settings, positions)

    # Existing positions keep trailing even while paused (protects open risk).
    if settings.mode is not Mode.READ_ONLY and settings.trail_enabled:
        for sym in settings.symbols:
            _update_trailing_stops(client, settings, symbol=sym)

    control = {"active": active, "prop": prop_on} if state is not None else None
    _refresh_dashboard(client, journal, settings, control=control,
                       thinking=thinking, prop=prop)
    _print_status(client, settings, active=active)


def _run_books(client, journal, settings, strategy_fn, planner_cfgs, positions,
               now_utc: Optional[datetime] = None, account=None,
               symbol: Optional[str] = None, risk_scale: float = 1.0) -> None:
    """Run the intraday + swing engines for ONE symbol under shared limits.

    In multi-symbol mode this is called once per symbol each loop. ``positions``
    is the full account-wide list, so the combined open-risk ceiling is
    enforced across EVERY symbol's open positions, not just this one.
    """
    symbol = symbol or settings.symbol
    now_utc = now_utc or datetime.now(timezone.utc)
    session_cfg, sizing_cfg, _style, stagger_cfg = planner_cfgs
    atr_cfg = _atr_cfg(settings)
    pip = pip_size(client, symbol) or 0.0001
    pip_val = pip_value_per_lot(client, symbol, pip, settings.pip_value_per_lot)
    balance = account.balance if account is not None else (
        client.account_info().balance if client.account_info() else 0.0)
    in_ny = is_ny_session(now_utc, session_cfg)
    total_open = len(positions)

    books = build_books(settings)
    swing_book = next(b for b in books
                      if b.timeframe.upper() == settings.swing_tf_high.upper())
    intraday_book = next(b for b in books
                         if b.timeframe.upper() == settings.day_timeframe.upper())
    engine_magics = {swing_book.magic, intraday_book.magic}

    # Fixed-fractional sizing means each open position risks a known % of the
    # balance (its engine's risk_percent for ITS symbol). Aggregate open risk
    # across ALL symbols is the sum; trailing only lowers it, so this is a
    # conservative upper bound used for the combined-risk ceiling.
    def _position_risk(p) -> float:
        mg = getattr(p, "magic", None)
        if mg == swing_book.magic:
            return settings.swing_risk_for(getattr(p, "symbol", symbol))
        if mg == intraday_book.magic:
            return settings.intraday_risk_for(getattr(p, "symbol", symbol))
        return 0.0

    open_risk = sum(_position_risk(p) for p in positions)

    cache = {}

    def decide(tf):
        if tf not in cache:
            snap = market_snapshot(client, symbol, tf, settings.atr_period)
            cache[tf] = (snap, strategy_fn(snap))
        return cache[tf]

    timeframes = (settings.timeframe, settings.trend_tf_mid,
                  settings.swing_tf_high, settings.swing_tf_higher)
    decisions = {tf: decide(tf)[1] for tf in timeframes}
    state = _dual_engine_state(decisions, settings)

    def has_opposing_engine_position(side: Signal) -> bool:
        opposing = (client.POSITION_TYPE_SELL if side is Signal.BUY
                    else client.POSITION_TYPE_BUY)
        return any(p.symbol == symbol and p.type == opposing
                   and getattr(p, "magic", None) in engine_magics
                   for p in positions)

    def open_engine(name, setup, book, snap_tf, risk_percent):
        nonlocal total_open, open_risk
        if risk_percent <= 0:
            # Engine disabled for this symbol (risk set to 0) -- e.g. a pair
            # whose swing side loses is run day-only.
            return
        if not setup["valid"]:
            log.info("%s [%s] waiting: %s", name, symbol, setup["reason"])
            return
        if book.ny_only and not in_ny:
            log.info("%s [%s] waiting: outside configured intraday session.",
                     name, symbol)
            return
        side = setup["bias"]
        if has_opposing_engine_position(side):
            log.info("%s [%s] blocked: opposite dual-engine position is open.",
                     name, symbol)
            return

        strong = setup["confidence"] >= settings.strong_trend_confidence
        desired = desired_positions(book, strong)
        if desired <= 0:
            return
        snap = decide(snap_tf)[0]
        stops = atr_stops((snap or {}).get("atr"), pip, atr_cfg) \
            if atr_cfg.enabled else None
        base_sl, base_tp = stops if stops else (book.sl_pips, book.tp_pips)
        risk_cfg = _risk_cfg(settings, risk_percent, pip_value=pip_val)
        have = _count_side(client, positions, symbol, side, book.magic)
        opened = 0

        while have + opened < desired:
            if total_open >= settings.max_open_positions:
                return
            # Combined open-risk ceiling across ALL symbols and engines.
            if open_risk + risk_percent > settings.combined_risk_ceiling + 1e-9:
                log.info("%s [%s] blocked: combined open risk "
                         "%.2f%% + %.2f%% > ceiling %.2f%%.",
                         name, symbol, open_risk, risk_percent,
                         settings.combined_risk_ceiling)
                return
            if journal.count_trades_today() >= settings.max_trades_per_day:
                return
            level = have + opened + 1
            sl_pips, tp_pips = stagger(base_sl, base_tp, level, stagger_cfg)
            volume = (risk_lot(balance, sl_pips, risk_cfg)
                      if risk_cfg.enabled else position_size(in_ny, sizing_cfg))
            ok, message = place_market_order(
                client, symbol, side, volume, sl_pips, tp_pips,
                magic=book.magic, comment=book.name)
            journal.log_order(symbol, side.value, volume, None,
                              sl_pips, tp_pips, None,
                              "FILLED" if ok else "REJECTED",
                              f"[{book.name} lvl{level}] {message}")
            log.info("%s [%s]: %s %s lots SL%.1f/TP%.1f conf=%.2f -> %s",
                     book.name, symbol, side.value, volume, sl_pips, tp_pips,
                     setup["confidence"], message)
            opened += 1
            if ok:
                total_open += 1
                open_risk += risk_percent
                _announce_open(settings, side.value, volume, book.name,
                               sl_pips, tp_pips, symbol=symbol)
            else:
                break

    # Swing is evaluated first; intraday may still open alongside it when both
    # engines point the same way and shared account limits permit.
    open_engine("swing", state["swing"], swing_book,
                settings.swing_tf_high, settings.swing_risk_for(symbol) * risk_scale)
    open_engine("intraday", state["intraday"], intraday_book,
                settings.timeframe, settings.intraday_risk_for(symbol) * risk_scale)


def _consider_trade(client, journal, settings, decision, positions,
                    planner_cfgs) -> None:
    session_cfg, sizing_cfg, style_cfg, stagger_cfg = planner_cfgs

    strong = decision.confidence >= settings.strong_trend_confidence
    cap = settings.max_same_direction if strong else 1
    same_count = _count_side(client, positions, settings.symbol, decision.signal)
    if same_count >= cap:
        log.info("Same-direction limit reached (%s/%s %s, conf=%.2f); skipping.",
                 same_count, cap, decision.signal.value, decision.confidence)
        return

    target = min(max(settings.min_same_direction, 1), cap) if strong else 1
    to_open = min(max(target - same_count, 1), cap - same_count)

    open_count = len(positions)
    opened = 0
    for _ in range(to_open):
        if journal.count_trades_today() >= settings.max_trades_per_day:
            break
        if open_count + opened >= settings.max_open_positions:
            break
        level = same_count + opened + 1
        plan = build_plan(decision, datetime.now(timezone.utc), session_cfg,
                          sizing_cfg, style_cfg, level=level,
                          stagger_cfg=stagger_cfg)
        if plan is None:
            break
        _execute_plan(client, journal, settings, plan, cap)
        opened += 1


def _execute_plan(client, journal, settings, plan, of=1) -> None:
    if settings.mode is Mode.APPROVAL:
        answer = input(f"Place demo {plan.describe()} ? Type YES to confirm: ")
        if answer != "YES":
            log.info("Trade skipped by user.")
            return

    ok, message = place_market_order(
        client, settings.symbol, plan.side, plan.volume,
        plan.sl_pips, plan.tp_pips,
    )
    journal.log_order(settings.symbol, plan.side.value, plan.volume,
                      None, plan.sl_pips, plan.tp_pips, None,
                      "FILLED" if ok else "REJECTED",
                      f"[{plan.style}/{plan.session} lvl{plan.level}/{of}] {message}")
    log.info("%s (%s/%s) -> %s", plan.describe(), plan.level, of, message)
    if ok:
        _announce_open(settings, plan.side.value, plan.volume,
                       f"{plan.style}/{plan.session}", plan.sl_pips, plan.tp_pips)


def _maybe_close(client, journal, settings, positions) -> None:
    answer = input("Close first open position? Type CLOSE to confirm: ")
    if answer != "CLOSE":
        log.info("Position left open.")
        return
    ticket = positions[0].ticket
    ok, message = close_position(client, ticket)
    journal.log_order(settings.symbol, "CLOSE", positions[0].volume, None, None,
                      None, ticket, "CLOSED" if ok else "REJECTED", message)
    log.info(message)


def run(settings: Optional[Settings] = None, client=None,
        journal: Optional[Journal] = None, strategy_fn: Optional[Callable] = None,
        max_iterations: Optional[int] = None, serve_dashboard: bool = True) -> None:
    settings = settings or load_settings()
    setup_logging(settings.log_level,
                  console_level="WARNING" if settings.console_status else settings.log_level)
    journal = journal or Journal(settings.db_path)
    strategy_fn = strategy_fn or make_strategy(settings)
    planner_cfgs = make_planner_configs(settings)
    limits = RiskLimits(settings.daily_max_loss, settings.total_max_loss,
                        settings.max_open_positions)
    tracker = DailyLossTracker()
    # Always build the guard so prop mode can be toggled ON live from the
    # dashboard; whether it actually gates trading is driven by state.is_prop().
    prop_guard = PropGuard(settings.prop_config())

    # Shared trading switch: the control server toggles it (Start/Pause on the
    # dashboard); the loop reads it. Trading starts ACTIVE.
    state = ControlState(active=True, prop=settings.prop_firm)

    if settings.serve_dashboard and serve_dashboard:
        try:
            start_control_server(state, settings.dashboard_port,
                                 settings.dashboard_path,
                                 host=settings.dashboard_host,
                                 data_path=_data_path(settings))
            url = f"http://{settings.dashboard_host}:{settings.dashboard_port}"
            local_url = f"http://127.0.0.1:{settings.dashboard_port}"
            log.info("Live dashboard: %s", url)
            if settings.console_status:
                print(f"Live dashboard (this PC): {local_url}")
                if settings.dashboard_host not in ("127.0.0.1", "localhost"):
                    print(f"Live dashboard (phone via Tailscale): "
                          f"http://<your-tailscale-ip>:{settings.dashboard_port}")
                print("Open the URL in a browser — do NOT open dashboard.html as "
                      "a file, or live updates won't work.")
            _status(settings, "Starting up - connecting to MT5...")
            try:
                webbrowser.open(local_url)
            except Exception:  # noqa: BLE001
                pass
        except OSError as exc:
            msg = (f"Dashboard server could NOT start on "
                   f"{settings.dashboard_host}:{settings.dashboard_port} ({exc}). "
                   f"Another bot is probably already running and using this port. "
                   f"Close EVERY other bot window (or run  taskkill /F /IM "
                   f"python.exe ), then start the bot again.")
            log.warning(msg)
            if settings.console_status:
                print("\n" + "=" * 70 + f"\n[!] {msg}\n" + "=" * 70 + "\n",
                      flush=True)

    iterations = 0
    consecutive_failures = 0
    connected = False
    try:
        while True:
            if not connected:
                try:
                    if client is None:
                        client = create_client()
                    connect(client, settings)
                    connected = True
                except Exception as exc:  # noqa: BLE001
                    log.error("Not connected: %s", exc)
                    _status(settings, f"MT5 not connected: {exc}")
                    iterations += 1
                    if max_iterations is not None and iterations >= max_iterations:
                        break
                    time.sleep(max(settings.reconnect_delay_seconds,
                                   settings.loop_interval_seconds) or 0)
                    continue

            try:
                _run_once(client, journal, settings, strategy_fn, limits,
                          tracker, planner_cfgs, state, prop_guard)
                consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                connected = False
                log.error("Iteration %s failed (%s/%s): %s", iterations,
                          consecutive_failures, settings.reconnect_attempts, exc)
                _status(settings, f"Error: {exc} (retrying)")
                if consecutive_failures >= settings.reconnect_attempts:
                    log.error("Giving up after %s consecutive failures.",
                              consecutive_failures)
                    raise

            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            time.sleep(settings.loop_interval_seconds)
    finally:
        journal.close()


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C).")


if __name__ == "__main__":
    main()
