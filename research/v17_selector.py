from __future__ import annotations

from v17_select_core import merge_frames, qualified, rank_score
from v17_signal_families import generate, parameter_grid, params_dict, stats


def select_symbol(symbol, h4, anchor=None):
    start, end = h4["time"].min(), h4["time"].max()
    split1 = start + (end - start) * 0.60
    split2 = start + (end - start) * 0.80
    scored = []
    for params in parameter_grid(symbol):
        frame = generate(symbol, h4, params)
        train = frame[frame["entry_time"] < split1]
        valid = frame[(frame["entry_time"] >= split1) & (frame["entry_time"] < split2)]
        tr = stats(train, start, split1)
        va = stats(valid, split1, split2)
        if tr["trades_per_year"] < 7 or va["trades_per_year"] < 7:
            continue
        if tr["net_r"] <= 0 or tr["profit_factor"] < 1.03:
            continue
        if va["net_r"] <= 0 or va["profit_factor"] < 1.05:
            continue
        score = va["net_r"] + 12 * (va["profit_factor"] - 1) + 0.15 * tr["net_r"] + 0.15 * min(va["trades_per_year"], 25)
        scored.append((score, params, frame))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        selected = merge_frames([anchor] if anchor is not None else [])
        return selected, {"status": "NO_VALIDATED_CONFIGURATION", "validation_split": split1.isoformat(), "holdout_split": split2.isoformat(), "holdout": stats(selected[selected["entry_time"] >= split2], split2, end)}
    combos = []
    for primary in scored[:12]:
        frames = [primary[2]]
        selected_params = [primary[1]]
        if anchor is not None:
            frames.insert(0, anchor)
        merged = merge_frames(frames)
        valid = merged[(merged["entry_time"] >= split1) & (merged["entry_time"] < split2)]
        combos.append((stats(valid, split1, split2), merged, selected_params))
        for secondary in scored:
            if secondary[1].family != primary[1].family:
                merged2 = merge_frames(frames + [secondary[2]])
                valid2 = merged2[(merged2["entry_time"] >= split1) & (merged2["entry_time"] < split2)]
                combos.append((stats(valid2, split1, split2), merged2, selected_params + [secondary[1]]))
                break
    good = [item for item in combos if qualified(item)]
    pool = good or combos
    pool.sort(key=rank_score, reverse=True)
    validation, selected, selected_params = pool[0]
    return selected, {
        "status": "QUALIFIED" if good and qualified(pool[0]) else "BEST_AVAILABLE_BELOW_TARGET",
        "validation_split": split1.isoformat(),
        "holdout_split": split2.isoformat(),
        "selected_parameters": params_dict(selected_params),
        "development": stats(selected[selected["entry_time"] < split1], start, split1),
        "validation": validation,
        "holdout": stats(selected[selected["entry_time"] >= split2], split2, end),
    }
