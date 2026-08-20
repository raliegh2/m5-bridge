import sys, json
sys.path.insert(0, "C:/Users/ralie/m5-bridge-etf")
import numpy as np, pandas as pd
from mt5_ai_bridge.corporate_actions import adjust_for_splits
from mt5_ai_bridge.data import load_csv
from mt5_ai_bridge.tactical_allocation import locked_tactical_config, replay_timing
from mt5_ai_bridge.validation import benjamini_hochberg

ROOT = "C:/Users/ralie/m5-bridge-etf/research/data"
cfg = locked_tactical_config()
rng = np.random.default_rng(20260818)

def sharpe(r):
    r = np.asarray(r, float)
    v = r.std(ddof=1) * np.sqrt(12)
    return (r.mean() * 12) / v if v > 0 else 0.0

out = {}
for sym in ("IVV", "VTI", "ONEQ", "IWM", "TQQQ", "EEM", "XAUUSD"):
    frame, _ = adjust_for_splits(load_csv(f"{ROOT}/{sym}_D1.csv").reset_index(drop=True))
    res = replay_timing(frame, cfg, 0.02, sym)
    s = np.asarray(res.strategy, float); b = np.asarray(res.benchmark, float)
    observed = sharpe(s) - sharpe(b)

    # Paired stationary bootstrap: resample months in blocks of 6 to keep
    # autocorrelation, recompute the Sharpe difference each draw.
    n, block, draws = len(s), 6, 4000
    diffs = np.empty(draws)
    for d in range(draws):
        idx = []
        while len(idx) < n:
            start = rng.integers(0, n)
            idx.extend(range(start, min(start + block, n)))
        idx = np.array(idx[:n])
        diffs[d] = sharpe(s[idx]) - sharpe(b[idx])
    p = float((diffs <= 0).mean())

    # Out-of-sample folds
    edges = np.linspace(0, n, 6, dtype=int)
    folds = []
    for k in range(5):
        ss, bb = s[edges[k]:edges[k+1]], b[edges[k]:edges[k+1]]
        if len(ss) > 2:
            folds.append(round(float(sharpe(ss) - sharpe(bb)), 3))
    out[sym] = {"sharpe_diff": round(float(observed), 3), "p_value": round(p, 4),
                "folds_better": int(sum(1 for f in folds if f > 0)),
                "fold_sharpe_diffs": folds}
    print(f"{sym:>7}: SR diff {observed:+.3f}  bootstrap p={p:.4f}  "
          f"folds better {out[sym]['folds_better']}/5  {folds}")

syms = list(out)
flags = benjamini_hochberg([out[s]["p_value"] for s in syms], 0.05)
print("\nBenjamini-Hochberg across the 7 assets (FDR 5%):")
for s, f in zip(syms, flags):
    out[s]["bh_significant"] = bool(f)
    print(f"  {s:>7}: {'SIGNIFICANT' if f else 'not significant'}  "
          f"(p={out[s]['p_value']:.4f})")
json.dump(out, open("C:/Users/ralie/m5-bridge-etf/research/tactical_significance.json","w"), indent=2)
