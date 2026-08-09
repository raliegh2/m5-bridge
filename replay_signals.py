"""Chronologically replay historical CSV signals through the external manager.

MT5 is used only to load history and contract specifications.  The terminal is
disconnected before the event simulation begins; no order_send call is made.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

import external_signal_manager as manager


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "signals_history.csv"
DEFAULT_LEDGER = ROOT / "replay_trade_ledger.csv"
BROKER_UTC_OFFSET_HOURS = 0
HISTORY_AFTER_LAST_SIGNAL_DAYS = 7
HISTORY_WARMUP_DAYS = 4
PARTIAL_R_MULTIPLE = getattr(manager, "PARTIAL_R_MULTIPLE", 1.0)


@dataclass
class ReplayPosition:
    ticket: int
    symbol: str
    base: str
    type: int
    volume: float
    price_open: float
    sl: float
    tp: float
    entry_time: pd.Timestamp
    initial_volume: float
    initial_risk: float
    direction: int
    signal_id: str
    partial_done: bool = False
    partial_time: Optional[pd.Timestamp] = None
    partial_price: Optional[float] = None
    banked_profit: float = 0.0
    banked_pips: float = 0.0
    last_trail_bar: Optional[pd.Timestamp] = None


class HistoricalBroker:
    """Read-only broker surface consumed by manager.validate_signal."""
    def __init__(self, frames: dict[str, pd.DataFrame], infos: dict[str, Any],
                 aliases: dict[str, str], initial_equity: float) -> None:
        self.frames, self.infos, self.aliases = frames, infos, aliases
        self.balance = float(initial_equity)
        self.marked_equity = float(initial_equity)
        self.open_positions: list[ReplayPosition] = []
        self.now: Optional[pd.Timestamp] = None
        self.current_bars: dict[str, pd.Series] = {}

    def equity(self) -> float: return self.marked_equity
    def resolve_symbol(self, requested: str) -> Optional[str]:
        return self.aliases.get(requested.upper())
    def positions(self, symbol: Optional[str] = None) -> list[ReplayPosition]:
        return [p for p in self.open_positions if symbol is None or p.symbol == symbol]
    def symbol_info(self, symbol: str) -> Any: return self.infos[symbol]
    def tick(self, symbol: str) -> Any:
        bar = self.current_bars.get(symbol)
        if bar is None: return None
        # Entry requirement specifies the first bar's open. Keep bid/ask equal
        # here; historical spread remains available in the source ledger/bar.
        return SimpleNamespace(bid=float(bar.open), ask=float(bar.open))
    def rates(self, symbol: str, timeframe: int, count: int) -> list[dict]:
        if self.now is None: return []
        source = self.frames[symbol].loc[:self.now - pd.Timedelta(microseconds=1)]
        if timeframe == mt5.TIMEFRAME_H1:
            source = source.resample("1h", label="right", closed="left").agg(
                {"open":"first","high":"max","low":"min","close":"last"}).dropna()
        elif timeframe == mt5.TIMEFRAME_D1:
            source = source.resample("1D", label="right", closed="left").agg(
                {"open":"first","high":"max","low":"min","close":"last"}).dropna()
        return source.tail(count).reset_index().to_dict("records")
    def send(self, request: dict[str, Any]) -> Any:
        raise RuntimeError("HistoricalBroker is offline and cannot send orders")


def parse_entry_time(value: str) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize(timezone(timedelta(hours=BROKER_UTC_OFFSET_HOURS)))
    return parsed.tz_convert("UTC")


def read_signals(path: Path) -> pd.DataFrame:
    if not path.exists(): raise FileNotFoundError(f"Signal file not found: {path}")
    frame = pd.read_csv(path)
    required = {"entry_time","symbol","direction","stop_loss_price",
                "take_profit_price","volume_lots"}
    missing = required - set(frame.columns)
    if missing: raise ValueError(f"Missing CSV columns: {sorted(missing)}")
    frame["entry_time"] = frame.entry_time.map(parse_entry_time)
    frame["symbol"] = frame.symbol.astype(str).str.strip().str.upper()
    frame["direction"] = frame.direction.astype(str).str.strip().str.upper()
    for column in ("stop_loss_price","take_profit_price","volume_lots"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.sort_values("entry_time", kind="stable").reset_index(drop=True)
    frame["signal_id"] = [f"replay_{i:07d}" for i in range(len(frame))]
    return frame


def resolve_mt5_symbol(requested: str) -> Optional[str]:
    if manager.symbol_base(requested) is None: return None
    if mt5.symbol_info(requested) is not None: return requested
    names = [item.name for item in (mt5.symbols_get() or ())
             if item.name.upper().startswith(requested.upper())]
    return min(names, key=lambda value:(len(value),value)) if names else None


def load_history(signals: pd.DataFrame) -> tuple[dict,dict,dict]:
    if not mt5.initialize(): raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    frames: dict[str,pd.DataFrame]={}; infos={}; aliases={}
    start=signals.entry_time.min()-pd.Timedelta(days=HISTORY_WARMUP_DAYS)
    end=signals.entry_time.max()+pd.Timedelta(days=HISTORY_AFTER_LAST_SIGNAL_DAYS)
    try:
        for requested in signals.symbol.unique():
            resolved=resolve_mt5_symbol(requested)
            if not resolved:
                print(f"WARNING: no broker symbol for {requested}; its signals will reject")
                continue
            mt5.symbol_select(resolved,True); info=mt5.symbol_info(resolved)
            raw=mt5.copy_rates_range(resolved,mt5.TIMEFRAME_M1,start.to_pydatetime(),end.to_pydatetime())
            if raw is None or not len(raw):
                print(f"WARNING: no M1 history for {resolved}"); continue
            data=pd.DataFrame(raw); data["time"]=pd.to_datetime(data.time,unit="s",utc=True)
            frames[resolved]=data.set_index("time").sort_index(); infos[resolved]=info
            aliases[requested]=resolved
    finally:
        mt5.shutdown()
    return frames,infos,aliases


def pip_size(info: Any) -> float:
    return info.point*10 if info.digits in (3,5) else info.point


def money(position: ReplayPosition, exit_price: float, volume: float,
          info: Any) -> float:
    return position.direction*(exit_price-position.price_open) \
           * info.trade_tick_value/info.trade_tick_size*volume


def pips(position: ReplayPosition, exit_price: float) -> float:
    return position.direction*(exit_price-position.price_open) \
           / pip_size(position_info[position.symbol])


position_info: dict[str,Any] = {}


def close_trade(position:ReplayPosition, price:float, when:pd.Timestamp,
                reason:str, broker:HistoricalBroker, ledger:list[dict]) -> None:
    info=broker.infos[position.symbol]
    closing=money(position,price,position.volume,info); broker.balance+=closing
    total=position.banked_profit+closing
    closing_pips=position.direction*(price-position.price_open)/pip_size(info)
    total_pips=position.banked_pips+closing_pips*(position.volume/position.initial_volume)
    ledger.append({"status":"ACCEPTED","signal_id":position.signal_id,
        "entry_time":position.entry_time.isoformat(),"exit_time":when.isoformat(),
        "symbol":position.symbol,"direction":"BUY" if position.direction==1 else "SELL",
        "entry_price":position.price_open,"exit_price":price,"initial_stop":position.price_open-position.direction*position.initial_risk,
        "initial_volume":position.initial_volume,"profit":total,"profit_pips":total_pips,
        "exit_reason":reason,"partial_time":position.partial_time.isoformat() if position.partial_time else None,
        "partial_price":position.partial_price,"rejection_reason":None})
    broker.open_positions.remove(position)


def trail_level(position:ReplayPosition, broker:HistoricalBroker,
                when:pd.Timestamp) -> Optional[float]:
    frame=broker.frames[position.symbol].loc[:when-pd.Timedelta(microseconds=1)]
    index_like=position.base in {"XAUUSD","US30","GER40","DE40"}
    if index_like:
        daily=frame.resample("1D",label="right",closed="left").agg({"high":"max","low":"min"}).dropna()
        if len(daily)>=3:
            return float(daily.low.tail(3).min() if position.direction==1 else daily.high.tail(3).max())
    hourly=frame.resample("1h",label="right",closed="left").agg({"high":"max","low":"min"}).dropna()
    if len(hourly)<20: return None
    return float(hourly.low.tail(20).min() if position.direction==1 else hourly.high.tail(20).max())


def process_bar(position:ReplayPosition, bar:pd.Series, when:pd.Timestamp,
                broker:HistoricalBroker, ledger:list[dict]) -> None:
    direction=position.direction
    stop_hit=bar.low<=position.sl if direction==1 else bar.high>=position.sl
    tp_hit=bool(position.tp) and (bar.high>=position.tp if direction==1 else bar.low<=position.tp)
    # Conservative ordering when both boundaries occur inside one M1 bar.
    if stop_hit:
        gap=float(bar.open)
        fill=min(gap,position.sl) if direction==1 else max(gap,position.sl)
        close_trade(position,fill,when,"STOP",broker,ledger); return
    if tp_hit:
        gap=float(bar.open)
        fill=max(gap,position.tp) if direction==1 else min(gap,position.tp)
        close_trade(position,fill,when,"FIXED_TP",broker,ledger); return
    if not position.partial_done:
        target=position.price_open+direction*PARTIAL_R_MULTIPLE*position.initial_risk
        reached=bar.high>=target if direction==1 else bar.low<=target
        if reached:
            fill=max(float(bar.open),target) if direction==1 else min(float(bar.open),target)
            half=position.initial_volume*.5
            gain=money(position,fill,half,broker.infos[position.symbol]); broker.balance+=gain
            position.banked_profit+=gain
            position.banked_pips+=direction*(fill-position.price_open)/pip_size(broker.infos[position.symbol])*.5
            position.volume-=half; position.partial_done=True; position.partial_time=when
            position.partial_price=fill; position.sl=position.price_open
    if position.partial_done:
        cadence=when.floor("D") if position.base in {"XAUUSD","US30","GER40","DE40"} else when.floor("h")
        if position.last_trail_bar!=cadence:
            level=trail_level(position,broker,when)
            if level is not None:
                market=float(bar.close); valid=level<market if direction==1 else level>market
                improves=level>position.sl if direction==1 else level<position.sl
                if valid and improves: position.sl=level
            position.last_trail_bar=cadence


def friday_close_due(when:pd.Timestamp) -> bool:
    ny=when.tz_convert(ZoneInfo("America/New_York"))
    return ny.weekday()==4 and ny.hour==16 and ny.minute==30


def summary(ledger:pd.DataFrame,initial:float,final:float,max_dd:float,
            daily_returns:list[float],processed:int) -> dict:
    accepted=ledger[ledger.status=="ACCEPTED"] if not ledger.empty else ledger
    profits=pd.to_numeric(accepted.get("profit",pd.Series(dtype=float)),errors="coerce").dropna()
    gp=profits[profits>0].sum(); gl=-profits[profits<0].sum()
    returns=pd.Series(daily_returns,dtype=float)
    sharpe=np.sqrt(252)*returns.mean()/returns.std(ddof=1) if len(returns)>1 and returns.std(ddof=1)>0 else None
    return {"signals_processed":processed,"trades_accepted":int(len(accepted)),
        "signals_rejected":int((ledger.status=="REJECTED").sum()) if not ledger.empty else processed,
        "net_profit":final-initial,"final_equity":final,
        "profit_factor":float(gp/gl) if gl else None,"max_drawdown":max_dd,
        "max_drawdown_pct":100*max_dd/initial,"sharpe":sharpe,
        "win_rate":100*float((profits>0).mean()) if len(profits) else None,
        "total_pips":float(pd.to_numeric(accepted.get("profit_pips",0),errors="coerce").sum()) if len(accepted) else 0.0}


def run_replay(signals:pd.DataFrame,initial_balance:float) -> tuple[pd.DataFrame,dict]:
    frames,infos,aliases=load_history(signals)
    global position_info; position_info=infos
    broker=HistoricalBroker(frames,infos,aliases,initial_balance); state=manager.RiskState()
    news_events=manager.load_news_blackouts()
    ledger:list[dict]=[]; ticket=1
    scheduled:dict[pd.Timestamp,list[pd.Series]]={}
    for _,signal in signals.iterrows():
        symbol=aliases.get(signal.symbol)
        if not symbol:
            ledger.append({"status":"REJECTED","signal_id":signal.signal_id,"symbol":signal.symbol,
                           "rejection_reason":"broker symbol/history unavailable"}); continue
        index=frames[symbol].index; location=index.searchsorted(signal.entry_time,side="right")
        if location>=len(index):
            ledger.append({"status":"REJECTED","signal_id":signal.signal_id,"symbol":symbol,
                           "rejection_reason":"no M1 bar after entry time"}); continue
        scheduled.setdefault(index[location],[]).append(signal)
    timeline=sorted(set().union(*(set(frame.index) for frame in frames.values())))
    peak=initial_balance; max_dd=0.; last_day=None; prior_day_equity=initial_balance; daily_returns=[]
    for when in timeline:
        broker.now=when; broker.current_bars={symbol:frame.loc[when] for symbol,frame in frames.items() if when in frame.index}
        # Signals are validated at the first available M1 open, before that bar's path unfolds.
        for row in scheduled.get(when,[]):
            payload={"signal_id":row.signal_id,"symbol":row.symbol,"direction":row.direction,
                "volume_lots":None if pd.isna(row.volume_lots) else float(row.volume_lots),
                "stop_loss_price":None if pd.isna(row.stop_loss_price) else float(row.stop_loss_price),
                "take_profit_price":None if pd.isna(row.take_profit_price) else float(row.take_profit_price),
                "timestamp":when.isoformat(),"source":"replay"}
            ok,reason,plan=manager.validate_signal(payload,broker,state,now=when.to_pydatetime(),news_events=news_events)
            if not ok:
                ledger.append({"status":"REJECTED","signal_id":row.signal_id,"symbol":row.symbol,
                               "entry_time":when.isoformat(),"rejection_reason":reason}); continue
            side=1 if plan["direction"]=="BUY" else -1
            position=ReplayPosition(ticket,plan["symbol"],plan["base"],0 if side==1 else 1,
                plan["volume"],plan["entry"],plan["stop"],float(plan["tp"] or 0),when,
                plan["volume"],abs(plan["entry"]-plan["stop"]),side,plan["signal_id"])
            broker.open_positions.append(position); ticket+=1
        for position in list(broker.open_positions):
            bar=broker.current_bars.get(position.symbol)
            if bar is not None: process_bar(position,bar,when,broker,ledger)
        if friday_close_due(when):
            for position in list(broker.open_positions):
                bar=broker.current_bars.get(position.symbol)
                if bar is not None: close_trade(position,float(bar.close),when,"FRIDAY_CLOSE",broker,ledger)
        floating=0.
        for position in broker.open_positions:
            bar=broker.current_bars.get(position.symbol)
            if bar is not None: floating+=money(position,float(bar.close),position.volume,infos[position.symbol])
        broker.marked_equity=broker.balance+floating; state.update(broker.marked_equity,when.to_pydatetime())
        peak=max(peak,broker.marked_equity); max_dd=max(max_dd,peak-broker.marked_equity)
        day=when.date()
        if last_day is not None and day!=last_day:
            daily_returns.append((prior_day_equity-initial_balance if not daily_returns else prior_day_equity-prev_equity)/max(prev_equity,1e-9))
            prev_equity=prior_day_equity
        elif last_day is None: prev_equity=initial_balance
        last_day=day; prior_day_equity=broker.marked_equity
        if state.weekly_locked or state.drawdown_halted:
            reason="WEEKLY_LOCK" if state.weekly_locked else "DRAWDOWN_HALT"
            for position in list(broker.open_positions):
                bar=broker.current_bars.get(position.symbol)
                if bar is not None: close_trade(position,float(bar.close),when,reason,broker,ledger)
    if last_day is not None: daily_returns.append((prior_day_equity-prev_equity)/max(prev_equity,1e-9))
    if broker.open_positions:
        last=timeline[-1]
        for position in list(broker.open_positions):
            frame=frames[position.symbol]; available=frame.loc[:last]
            close_trade(position,float(available.iloc[-1].close),available.index[-1],"END_OF_DATA",broker,ledger)
    broker.marked_equity=broker.balance
    ledger_frame=pd.DataFrame(ledger)
    return ledger_frame,summary(ledger_frame,initial_balance,broker.balance,max_dd,daily_returns,len(signals))


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,default=DEFAULT_INPUT)
    parser.add_argument("--ledger",type=Path,default=DEFAULT_LEDGER)
    parser.add_argument("--initial-balance",type=float,default=5000.0)
    args=parser.parse_args()
    signals=read_signals(args.input.resolve())
    if signals.empty: raise SystemExit("signals_history.csv contains no signals")
    ledger,report=run_replay(signals,args.initial_balance)
    args.ledger.parent.mkdir(parents=True,exist_ok=True); ledger.to_csv(args.ledger,index=False)
    print("\nHistorical Signal Replay")
    for key,value in report.items(): print(f"{key}: {value}")
    print(f"ledger: {args.ledger.resolve()}")


if __name__=="__main__": main()
