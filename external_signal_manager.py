"""Demo-only MT5 external-signal executor and autonomous risk manager.

Signals are atomically claimed from ``signals/incoming`` and archived under
``signals/processed``.  This module refuses to trade a non-demo account.
News blackouts are supplied locally in ``signals/news_blackouts.json`` as a
JSON list of ``{"start": ISO8601, "end": ISO8601, "symbols": [...]}`` objects.
No external API is used.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as clock_time, timezone
import json
import logging
import math
import os
from pathlib import Path
import time
from typing import Any, Optional, Protocol
from zoneinfo import ZoneInfo

try:
    import MetaTrader5 as mt5
except ImportError:  # permits import and mock tests on machines without MT5
    mt5 = None


ROOT = Path(__file__).resolve().parent
INCOMING = ROOT / "signals" / "incoming"
PROCESSED = ROOT / "signals" / "processed"
STATE_FILE = ROOT / "signals" / "manager_state.json"
NEWS_FILE = ROOT / "signals" / "news_blackouts.json"
LOG_FILE = ROOT / "signal_manager.log"

ALLOWED_SYMBOLS = ("EURUSD", "AUDUSD", "GBPUSD", "GBPJPY", "XAUUSD",
                   "US30", "GER40", "DE40")
SYMBOL_CEILINGS = {"EURUSD": .010, "AUDUSD": .010, "GBPUSD": .010,
                   "GBPJPY": .0065, "XAUUSD": .005,
                   "US30": .010, "GER40": .010, "DE40": .010}
GLOBAL_RISK_CAP = .025
DEFAULT_TRADE_RISK = .0035
DAILY_LOSS_LOCK = .020
WEEKLY_LOSS_LOCK = .035
MAX_DRAWDOWN_HALT = .070
SIGNAL_MAX_AGE_SECONDS = 24 * 60 * 60
POLL_SECONDS = 1.0
MAGIC_NUMBER = 9300
COMMENT_PREFIX = "ExternalSignal"

LOGGER = logging.getLogger("external_signal_manager")


def configure_logging() -> None:
    """Configure rotating-process-style console and file logging once."""
    if LOGGER.handlers:
        return
    LOGGER.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s")
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    stream_handler = logging.StreamHandler()
    file_handler.setFormatter(formatter); stream_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler); LOGGER.addHandler(stream_handler)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def symbol_base(symbol: str) -> Optional[str]:
    upper = symbol.upper()
    return next((base for base in ALLOWED_SYMBOLS if upper.startswith(base)), None)


@dataclass
class PositionState:
    initial_volume: float
    initial_risk_price: float
    entry_price: float
    partial_done: bool = False
    last_trail_bar: Optional[str] = None


@dataclass
class RiskState:
    day_key: str = ""
    week_key: str = ""
    day_start_equity: float = 0.0
    week_start_equity: float = 0.0
    peak_equity: float = 0.0
    daily_locked: bool = False
    weekly_locked: bool = False
    drawdown_halted: bool = False
    positions: dict[str, PositionState] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "RiskState":
        if not path.exists():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["positions"] = {key: PositionState(**value)
                                    for key, value in payload.get("positions", {}).items()}
            return cls(**payload)
        except Exception as exc:
            LOGGER.error("Cannot load risk state; failing closed: %s", exc)
            state = cls(); state.drawdown_halted = True
            return state

    def save(self, path: Path = STATE_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        payload = asdict(self)
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def update(self, equity: float, now: datetime) -> None:
        day = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        iso = now.astimezone(ZoneInfo("America/New_York")).isocalendar()
        week = f"{iso.year}-W{iso.week:02d}"
        if self.day_key != day:
            self.day_key, self.day_start_equity, self.daily_locked = day, equity, False
        if self.week_key != week:
            self.week_key, self.week_start_equity, self.weekly_locked = week, equity, False
        if self.peak_equity <= 0:
            self.peak_equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        self.daily_locked |= equity <= self.day_start_equity * (1 - DAILY_LOSS_LOCK)
        self.weekly_locked |= equity <= self.week_start_equity * (1 - WEEKLY_LOSS_LOCK)
        self.drawdown_halted |= equity <= self.peak_equity * (1 - MAX_DRAWDOWN_HALT)


class Broker(Protocol):
    def equity(self) -> float: ...
    def resolve_symbol(self, requested: str) -> Optional[str]: ...
    def positions(self, symbol: Optional[str] = None) -> list[Any]: ...
    def symbol_info(self, symbol: str) -> Any: ...
    def tick(self, symbol: str) -> Any: ...
    def rates(self, symbol: str, timeframe: int, count: int) -> Any: ...
    def send(self, request: dict[str, Any]) -> Any: ...


class MT5Broker:
    """Small demo-only wrapper around the MetaTrader5 package."""
    def connect(self) -> None:
        if mt5 is None:
            raise RuntimeError("MetaTrader5 package is not installed")
        if not mt5.initialize():
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        account = mt5.account_info()
        if account is None:
            raise RuntimeError("MT5 account information unavailable")
        if account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            mt5.shutdown()
            raise RuntimeError("REFUSING TO RUN: the connected MT5 account is not DEMO")
        LOGGER.info("Connected to demo account %s", account.login)

    def shutdown(self) -> None:
        if mt5 is not None: mt5.shutdown()

    def equity(self) -> float:
        account = mt5.account_info()
        if account is None: raise RuntimeError("Account unavailable")
        return float(account.equity)

    def resolve_symbol(self, requested: str) -> Optional[str]:
        if symbol_base(requested) is None: return None
        if mt5.symbol_info(requested) is not None:
            name = requested
        else:
            names = [s.name for s in (mt5.symbols_get() or ())
                     if s.name.upper().startswith(requested.upper())]
            if not names: return None
            name = min(names, key=lambda value: (len(value), value))
        return name if mt5.symbol_select(name, True) else None

    def positions(self, symbol: Optional[str] = None) -> list[Any]:
        return list((mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()) or ())

    def symbol_info(self, symbol: str) -> Any: return mt5.symbol_info(symbol)
    def tick(self, symbol: str) -> Any: return mt5.symbol_info_tick(symbol)
    def rates(self, symbol: str, timeframe: int, count: int) -> Any:
        return mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    def send(self, request: dict[str, Any]) -> Any:
        result = mt5.order_send(request)
        if result is None or result.retcode not in {mt5.TRADE_RETCODE_DONE,
                                                    mt5.TRADE_RETCODE_DONE_PARTIAL}:
            raise RuntimeError(f"order_send failed: {result}; {mt5.last_error()}")
        return result


@dataclass
class MockInfo:
    point: float = .00001
    digits: int = 5
    trade_tick_value: float = 1.0
    trade_tick_size: float = .00001
    volume_min: float = .01
    volume_max: float = 100.0
    volume_step: float = .01
    filling_mode: int = 1


@dataclass
class MockTick:
    bid: float = 1.10000
    ask: float = 1.10012


class MockBroker:
    """Deterministic no-MT5 broker used only by test_pipeline.py."""
    def __init__(self, starting_equity: float = 10_000) -> None:
        self._equity = starting_equity; self._positions: list[Any] = []
    def equity(self) -> float: return self._equity
    def resolve_symbol(self, requested: str) -> Optional[str]:
        return requested if symbol_base(requested) else None
    def positions(self, symbol: Optional[str] = None) -> list[Any]:
        return [p for p in self._positions if symbol is None or p.symbol == symbol]
    def symbol_info(self, symbol: str) -> MockInfo: return MockInfo()
    def tick(self, symbol: str) -> MockTick: return MockTick()
    def rates(self, symbol: str, timeframe: int, count: int) -> list[dict]:
        return [{"high":1.102,"low":1.098,"close":1.1} for _ in range(count)]
    def send(self, request: dict[str, Any]) -> dict: return {"retcode":"MOCK_DONE", **request}


def load_news_blackouts(path: Path = NEWS_FILE) -> list[dict[str, Any]]:
    if not path.exists(): return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        LOGGER.error("Invalid news calendar; new entries fail closed: %s", exc)
        return [{"start":"1970-01-01T00:00:00+00:00",
                 "end":"2999-01-01T00:00:00+00:00","symbols":[]}]


def in_news_blackout(symbol: str, now: datetime,
                     events: Optional[list[dict[str, Any]]] = None) -> bool:
    for event in events if events is not None else load_news_blackouts():
        try:
            affected = event.get("symbols", [])
            if (not affected or symbol_base(symbol) in affected or symbol in affected) \
                    and parse_time(event["start"]) <= now <= parse_time(event["end"]):
                return True
        except Exception:
            LOGGER.error("Malformed news event; treating as blackout: %s", event)
            return True
    return False


def current_position_risk(position: Any, info: Any, equity: float) -> float:
    sl = float(getattr(position, "sl", 0) or 0)
    entry = float(getattr(position, "price_open", 0) or 0)
    if sl <= 0 or equity <= 0: return math.inf
    dollars = abs(entry-sl) * info.trade_tick_value / info.trade_tick_size * float(position.volume)
    return dollars / equity


def portfolio_risk(broker: Broker, equity: float) -> tuple[float, dict[str, float]]:
    total = 0.0; symbols: dict[str, float] = {}
    for position in broker.positions():
        info = broker.symbol_info(position.symbol)
        risk = current_position_risk(position, info, equity)
        total += risk; symbols[position.symbol] = symbols.get(position.symbol, 0.0) + risk
    return total, symbols


def atr_stop(broker: Broker, symbol: str, entry: float, direction: str) -> float:
    timeframe = mt5.TIMEFRAME_H1 if mt5 is not None else 16385
    rates = broker.rates(symbol, timeframe, 30)
    if rates is None or len(rates) < 15: raise ValueError("insufficient H1 bars for ATR stop")
    rows = list(rates); trs=[]; previous=None
    for row in rows:
        high, low, close = float(row["high"]), float(row["low"]), float(row["close"])
        trs.append(high-low if previous is None else max(high-low,abs(high-previous),abs(low-previous)))
        previous=close
    atr=sum(trs[-14:])/14; distance=1.5*atr
    return entry-distance if direction=="BUY" else entry+distance


def calculate_volume(info: Any, equity: float, entry: float, stop: float,
                     requested: Optional[float]) -> tuple[float, float]:
    loss_per_lot=abs(entry-stop)*info.trade_tick_value/info.trade_tick_size
    if loss_per_lot<=0: raise ValueError("invalid stop distance/contract")
    max_volume=equity*DEFAULT_TRADE_RISK/loss_per_lot
    volume=max_volume if requested is None else min(float(requested),max_volume)
    volume=math.floor((volume+1e-12)/info.volume_step)*info.volume_step
    if volume<info.volume_min: raise ValueError("risk-sized volume below broker minimum")
    volume=min(volume,info.volume_max); risk=volume*loss_per_lot/equity
    return round(volume,8),risk


def validate_signal(signal: dict[str, Any], broker: Broker, state: RiskState,
                    now: Optional[datetime]=None,
                    news_events: Optional[list[dict[str,Any]]]=None) -> tuple[bool,str,Optional[dict]]:
    """Validate schema, locks, existing exposure and produce an execution plan."""
    try:
        now=now or utc_now(); required={"signal_id","symbol","direction","timestamp","source"}
        if not required.issubset(signal): return False,"missing required fields",None
        direction=str(signal["direction"]).upper()
        if direction not in {"BUY","SELL"}: return False,"direction must be BUY or SELL",None
        requested=str(signal["symbol"]).upper(); base=symbol_base(requested)
        if base is None: return False,"symbol not allowlisted",None
        if abs((now-parse_time(str(signal["timestamp"]))).total_seconds())>SIGNAL_MAX_AGE_SECONDS:
            return False,"signal timestamp is stale or future-dated",None
        symbol=broker.resolve_symbol(requested)
        if not symbol: return False,"broker symbol unavailable",None
        if in_news_blackout(symbol,now,news_events): return False,"news blackout active",None
        equity=broker.equity(); state.update(equity,now)
        if state.daily_locked: return False,"daily loss lock active",None
        if state.weekly_locked: return False,"weekly loss lock active",None
        if state.drawdown_halted: return False,"maximum drawdown halt active",None
        if broker.positions(symbol): return False,"position already open on symbol",None
        info,tick=broker.symbol_info(symbol),broker.tick(symbol)
        if info is None or tick is None: return False,"symbol/tick information unavailable",None
        entry=float(tick.ask if direction=="BUY" else tick.bid)
        raw_sl=signal.get("stop_loss_price")
        stop=float(raw_sl) if raw_sl is not None else atr_stop(broker,symbol,entry,direction)
        if (direction=="BUY" and stop>=entry) or (direction=="SELL" and stop<=entry):
            return False,"stop is on wrong side of entry",None
        volume,risk=calculate_volume(info,equity,entry,stop,signal.get("volume_lots"))
        total,by_symbol=portfolio_risk(broker,equity)
        if not math.isfinite(total): return False,"an open position lacks a valid stop",None
        symbol_existing=by_symbol.get(symbol,0.0)
        if total+risk>GLOBAL_RISK_CAP+1e-12: return False,"global 2.5% risk cap exceeded",None
        if symbol_existing+risk>SYMBOL_CEILINGS[base]+1e-12:
            return False,"symbol risk ceiling exceeded",None
        tp=signal.get("take_profit_price")
        if tp is not None:
            tp=float(tp)
            if (direction=="BUY" and tp<=entry) or (direction=="SELL" and tp>=entry):
                return False,"take profit is on wrong side of entry",None
        return True,"accepted",{"signal_id":signal["signal_id"],"symbol":symbol,
            "base":base,"direction":direction,"entry":entry,"stop":stop,"tp":tp,
            "volume":volume,"risk_fraction":risk}
    except Exception as exc:
        LOGGER.exception("Signal validation failed")
        return False,f"validation error: {exc}",None


def execute_plan(plan: dict[str,Any], broker: Broker, state: RiskState) -> Any:
    if mt5 is None: raise RuntimeError("MetaTrader5 unavailable")
    buy=plan["direction"]=="BUY"; tick=broker.tick(plan["symbol"]); info=broker.symbol_info(plan["symbol"])
    price=float(tick.ask if buy else tick.bid)
    request={"action":mt5.TRADE_ACTION_DEAL,"symbol":plan["symbol"],"volume":plan["volume"],
        "type":mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,"price":price,
        "sl":round(plan["stop"],info.digits),"tp":round(plan["tp"],info.digits) if plan["tp"] else 0.0,
        "deviation":20,"magic":MAGIC_NUMBER,"comment":f"{COMMENT_PREFIX}:{plan['signal_id'][:12]}",
        "type_time":mt5.ORDER_TIME_GTC,"type_filling":int(info.filling_mode)}
    result=broker.send(request)
    ticket=int(getattr(result,"order",0) or getattr(result,"deal",0))
    if ticket: state.positions[str(ticket)]=PositionState(plan["volume"],abs(price-plan["stop"]),price)
    state.save(); return result


def _close_position(broker:Broker,position:Any,volume:Optional[float]=None) -> Any:
    if mt5 is None: return None
    tick=broker.tick(position.symbol); buy=position.type==mt5.POSITION_TYPE_BUY
    return broker.send({"action":mt5.TRADE_ACTION_DEAL,"symbol":position.symbol,
        "position":int(position.ticket),"volume":float(position.volume if volume is None else volume),
        "type":mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
        "price":float(tick.bid if buy else tick.ask),"deviation":20,"magic":MAGIC_NUMBER,
        "comment":f"{COMMENT_PREFIX}:close","type_time":mt5.ORDER_TIME_GTC,
        "type_filling":int(broker.symbol_info(position.symbol).filling_mode)})


def close_all_positions(broker:Broker) -> int:
    count=0
    for position in broker.positions():
        if _close_position(broker,position) is not None: count+=1
    return count


def manage_positions(broker:Broker,state:RiskState) -> None:
    """Manage partials/trails and enforce hard locks/weekend flattening."""
    equity=broker.equity(); now=utc_now(); state.update(equity,now)
    ny=now.astimezone(ZoneInfo("America/New_York"))
    if state.weekly_locked or state.drawdown_halted or (ny.weekday()==4 and ny.time()>=clock_time(16,30)):
        reason="weekly lock" if state.weekly_locked else "drawdown halt" if state.drawdown_halted else "Friday close"
        closed=close_all_positions(broker); LOGGER.warning("%s: closed %d positions",reason,closed); state.save(); return
    live_tickets=set()
    for p in broker.positions():
        ticket=str(int(p.ticket)); live_tickets.add(ticket); info=broker.symbol_info(p.symbol); tick=broker.tick(p.symbol)
        ps=state.positions.get(ticket)
        if ps is None:
            risk=abs(float(p.price_open)-float(p.sl))
            if risk<=0: LOGGER.error("Ticket %s has no recoverable initial risk",ticket); continue
            ps=state.positions[ticket]=PositionState(float(p.volume),risk,float(p.price_open),
                bool(p.sl and abs(float(p.sl)-float(p.price_open))<=info.point))
        buy=p.type==mt5.POSITION_TYPE_BUY; market=float(tick.bid if buy else tick.ask)
        target=ps.entry_price+(ps.initial_risk_price if buy else -ps.initial_risk_price)
        if not ps.partial_done and (market>=target if buy else market<=target):
            amount=math.floor(ps.initial_volume*.5/info.volume_step)*info.volume_step
            if amount>=info.volume_min and float(p.volume)-amount>=info.volume_min:
                if _close_position(broker,p,amount) is not None:
                    broker.send({"action":mt5.TRADE_ACTION_SLTP,"symbol":p.symbol,"position":int(p.ticket),
                        "sl":round(ps.entry_price,info.digits),"tp":float(p.tp or 0),"magic":MAGIC_NUMBER})
                    ps.partial_done=True; LOGGER.info("Ticket %s partial 50%% and breakeven",ticket)
        if ps.partial_done:
            base=symbol_base(p.symbol); tf=mt5.TIMEFRAME_D1 if base in {"XAUUSD","US30","GER40","DE40"} else mt5.TIMEFRAME_H1
            count=4 if tf==mt5.TIMEFRAME_D1 else 21; rates=broker.rates(p.symbol,tf,count)
            if rates is not None and len(rates)>=count-1:
                closed=list(rates)[:-1]; stamp=str(rates[-1]["time"])
                trail=min(float(x["low"]) for x in closed[-3:]) if buy and tf==mt5.TIMEFRAME_D1 else \
                      max(float(x["high"]) for x in closed[-3:]) if not buy and tf==mt5.TIMEFRAME_D1 else \
                      min(float(x["low"]) for x in closed[-20:]) if buy else max(float(x["high"]) for x in closed[-20:])
                valid=trail<market if buy else trail>market; improves=trail>p.sl if buy else (p.sl==0 or trail<p.sl)
                if stamp!=ps.last_trail_bar and valid and improves:
                    broker.send({"action":mt5.TRADE_ACTION_SLTP,"symbol":p.symbol,"position":int(p.ticket),
                        "sl":round(trail,info.digits),"tp":float(p.tp or 0),"magic":MAGIC_NUMBER})
                    ps.last_trail_bar=stamp; LOGGER.info("Ticket %s trail -> %s",ticket,trail)
    for ticket in set(state.positions)-live_tickets: state.positions.pop(ticket,None)
    state.save()


def archive(path:Path,status:str,reason:str) -> Path:
    PROCESSED.mkdir(parents=True,exist_ok=True); destination=PROCESSED/f"{path.stem}.{status}.json"
    counter=1
    while destination.exists(): destination=PROCESSED/f"{path.stem}.{status}.{counter}.json"; counter+=1
    try:
        payload=json.loads(path.read_text(encoding="utf-8")); payload["manager_status"]=status; payload["manager_reason"]=reason
        path.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    except Exception: pass
    os.replace(path,destination); return destination


def process_file(path:Path,broker:Broker,state:RiskState,mock:bool=False) -> tuple[bool,str]:
    claimed=path.with_suffix(".processing")
    try: os.replace(path,claimed)
    except FileNotFoundError: return False,"already claimed"
    try:
        signal=json.loads(claimed.read_text(encoding="utf-8")); ok,reason,plan=validate_signal(signal,broker,state)
        if ok and not mock: execute_plan(plan,broker,state)
        archive(claimed,"accepted" if ok else "rejected",reason); LOGGER.info("%s: %s",signal.get("signal_id"),reason)
        return ok,reason
    except Exception as exc:
        LOGGER.exception("Processing failed for %s",claimed); archive(claimed,"error",str(exc)); return False,str(exc)


def run(mock:bool=False,once:bool=False) -> None:
    configure_logging(); INCOMING.mkdir(parents=True,exist_ok=True); PROCESSED.mkdir(parents=True,exist_ok=True)
    broker:Broker=MockBroker() if mock else MT5Broker()
    if not mock: broker.connect()  # type: ignore[attr-defined]
    state=RiskState.load(); LOGGER.info("Signal manager started (mock=%s)",mock)
    try:
        while True:
            for path in sorted(INCOMING.glob("*.json")): process_file(path,broker,state,mock)
            if not mock: manage_positions(broker,state)
            if once: break
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt: LOGGER.info("Stopped by user")
    finally:
        if not mock: broker.shutdown()  # type: ignore[attr-defined]


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--mock",action="store_true"); parser.add_argument("--once",action="store_true")
    args=parser.parse_args(); run(mock=args.mock,once=args.once)


if __name__=="__main__": main()
