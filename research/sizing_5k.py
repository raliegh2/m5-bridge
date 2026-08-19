import sys, json, statistics; sys.path.insert(0, "C:/Users/ralie/m5-bridge-etf")
import numpy as np
from mt5_ai_bridge.corporate_actions import adjust_for_splits
from mt5_ai_bridge.data import load_csv
from mt5_ai_bridge.tactical_allocation import locked_tactical_config, replay_timing

R = "C:/Users/ralie/m5-bridge-etf/research/data"; cfg = locked_tactical_config(); BAL = 5000.0
def load(sym):
    f, _ = adjust_for_splits(load_csv(f"{R}/{sym}_D1.csv").reset_index(drop=True))
    return f
def timed(sym, spread):
    r = replay_timing(load(sym), cfg, spread, sym)
    return np.asarray(r.strategy, float)

# Is SCHX a sound stand-in for IVV? Compare daily returns on the overlap.
a, b = load("IVV"), load("SCHX")
m = a.merge(b, on="time", suffixes=("_ivv", "_schx"))
ri = np.diff(np.log(m.close_ivv.to_numpy(float)))
rs = np.diff(np.log(m.close_schx.to_numpy(float)))
print(f"IVV vs SCHX: {len(m)} shared days, daily-return correlation "
      f"{np.corrcoef(ri, rs)[0,1]:.4f}")

# Size on the LONGEST history (IVV+IAU, includes 2008); SCHX only starts 2009.
ivv, iau = timed("IVV", 0.003), timed("IAU", 0.012)
n = min(len(ivv), len(iau)); ivv, iau = ivv[-n:], iau[-n:]
book = 0.5*ivv + 0.5*iau
def maxdd(r):
    c = np.concatenate([[1.0], np.cumprod(1.0+r)]); p = np.maximum.accumulate(c)
    return float(np.max((p-c)/p))
def worst10(r, w=120):
    return maxdd(r) if len(r) <= w else max(maxdd(r[i:i+w]) for i in range(len(r)-w+1))
f = next(float(c) for c in np.arange(1.0, 0.04, -0.01) if worst10(c*book) <= 0.10)
rs_ = f*book
g = float(np.prod(1+rs_)); cagr = g**(12/len(rs_))-1

print(f"\nsized on {n} months (~{n/12:.1f}y, includes 2008): "
      f"{f:.0%} invested, CAGR {cagr*100:.2f}%, worst 10y DD {worst10(rs_)*100:.1f}%")

wins = sorted(BAL*float(np.prod(1+rs_[i:i+120])) for i in range(len(rs_)-120+1))
print(f"\n$5,000 over 10 years ({len(wins)} rolling windows):")
for lab, v in (("worst", wins[0]), ("25th", wins[len(wins)//4]),
               ("median", statistics.median(wins)), ("75th", wins[3*len(wins)//4]),
               ("best", wins[-1])):
    print(f"  {lab:<7} ${v:>9,.0f}   profit ${v-BAL:>8,.0f}   ({(v/BAL-1)*100:+6.1f}%)")

px = {s: float(load(s)["close"].iloc[-1]) for s in ("SCHX", "IAU", "IVV")}
print(f"\nallocation of ${BAL:,.0f} at {f:.0%} invested, 50/50:")
for sym in ("SCHX", "IAU"):
    tgt = BAL*f*0.5; sh = int(tgt // px[sym])
    print(f"  {sym:>5} ${px[sym]:>7.2f}  target ${tgt:>8,.2f} -> {sh:>3} shares "
          f"= ${sh*px[sym]:>8,.2f}  ({sh*px[sym]/(BAL*f)*100:.1f}% of book)")
held = sum(int(BAL*f*0.5//px[s])*px[s] for s in ("SCHX","IAU"))
print(f"  invested ${held:,.2f} ({held/BAL*100:.1f}% of account), "
      f"cash ${BAL-held:,.2f}")
json.dump({"fraction_invested": f, "cagr_pct": round(cagr*100,3),
           "worst_10y_dd_pct": round(worst10(rs_)*100,2),
           "median_10y_end": round(statistics.median(wins),2),
           "worst_10y_end": round(wins[0],2), "best_10y_end": round(wins[-1],2),
           "windows": len(wins), "months": int(n)},
          open("C:/Users/ralie/m5-bridge-etf/research/sizing_5k.json","w"), indent=2)
