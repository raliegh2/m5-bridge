import sys, statistics; sys.path.insert(0, "C:/Users/ralie/m5-bridge-etf")
import numpy as np
from mt5_ai_bridge.corporate_actions import adjust_for_splits
from mt5_ai_bridge.data import load_csv
from mt5_ai_bridge.tactical_allocation import locked_tactical_config, replay_timing
R="C:/Users/ralie/m5-bridge-etf/research/data"; cfg=locked_tactical_config()
def timed(sym, spread):
    f,_ = adjust_for_splits(load_csv(f"{R}/{sym}_D1.csv").reset_index(drop=True))
    return np.asarray(replay_timing(f,cfg,spread,sym).strategy, float)
ti, tg = timed("IVV",0.003), timed("IAU",0.012)
n=min(len(ti),len(tg)); ti,tg = ti[-n:],tg[-n:]
def maxdd(r):
    c=np.concatenate([[1.0],np.cumprod(1.0+r)]); p=np.maximum.accumulate(c)
    return float(np.max((p-c)/p))
def worst10(r,w=120):
    return max(maxdd(r[i:i+w]) for i in range(len(r)-w+1))
def cagr(r):
    g=float(np.prod(1+r)); return g**(12/len(r))-1
gold_cagr = cagr(tg)
print(f"timed gold CAGR in sample: {gold_cagr*100:.2f}%\n")
print(f"{'scenario':<40}{'f':>6}{'CAGR':>9}{'DD':>8}{'median $5k':>13}")
print("-"*76)
for label, target in (("as measured (gold 8.3%/yr)", None),
                      ("gold haircut to 5%/yr", 0.05),
                      ("gold haircut to 3%/yr", 0.03),
                      ("gold haircut to 0%/yr", 0.00),
                      ("gold haircut to -2%/yr", -0.02)):
    g = tg.copy()
    if target is not None:
        # shift every gold month by a constant so its CAGR hits the target,
        # leaving volatility and crash timing untouched
        shift = (1+target)**(1/12) - (1+gold_cagr)**(1/12)
        g = tg + shift
    book = 0.5*ti + 0.5*g
    f = next(float(c) for c in np.arange(1.0,0.04,-0.01) if worst10(c*book)<=0.10)
    rs = f*book
    wins = sorted(5000*float(np.prod(1+rs[i:i+120])) for i in range(len(rs)-120+1))
    print(f"{label:<40}{f:>6.2f}{cagr(rs)*100:>8.2f}%{worst10(rs)*100:>7.1f}%"
          f"   ${statistics.median(wins):>10,.0f}")
