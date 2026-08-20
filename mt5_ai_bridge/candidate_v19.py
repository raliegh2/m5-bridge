"""V19: V16's reversion, restricted to below-average-volume bars.

Every signal parameter is inherited from V16 unchanged. The only addition is a
filter: take a reversion trade only when the bar's tick volume is below its own
trailing average.

The justification is measured, not assumed. `research/volume_information_test.py`
found first-order return autocorrelation materially more negative after
low-volume bars on AUDUSD (-0.036 vs -0.007), EURUSD (-0.028 vs +0.004) and
USDJPY (-0.022 vs +0.015) -- the pattern Campbell, Grossman & Wang (1993)
predict. It also found GBPUSD contradicting it, which is why
``research/v19_locked_candidate.json`` registers a symbol-by-symbol prediction
rather than a simple profit claim.

The threshold is 1.0 -- below the bar's own trailing mean -- which is the
textbook definition and deliberately *less* favourable than the 30th percentile
where the measured effect is strongest. Tuning it toward that percentile would
improve the backtest and would be fitting.

Why this exists at all: V16's frictionless profit factor is 1.057 against a
1.10 gate. Cost can only subtract, so the gross signal has to get stronger. This
is the one principled way found to attempt that.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .candidate_v16 import ReversionConfig, replay_v16
from .costs import ZERO_COST, CostModel
from .order_flow import relative_volume

__all__ = ["VolumeReversionConfig", "LOCKED_V19", "locked_config_v19",
           "add_volume_filter", "replay_v19"]

LOCK_PATH = (Path(__file__).resolve().parents[1] / "research"
             / "v19_locked_candidate.json")


@dataclass(frozen=True)
class VolumeReversionConfig(ReversionConfig):
    """V16's parameters plus the volume filter."""

    volume_lookback: int = 20
    max_relative_volume: float = 1.0

    def validate(self) -> None:
        super().validate()
        if self.volume_lookback < 2:
            raise ValueError("volume_lookback must be at least 2")
        if self.max_relative_volume <= 0:
            raise ValueError("max_relative_volume must be positive")


LOCKED_V19 = VolumeReversionConfig()


def locked_config_v19(path: Optional[Path] = None) -> VolumeReversionConfig:
    """Load the frozen parameters, refusing a file edited after the fact."""
    path = Path(path or LOCK_PATH)
    if not path.exists():
        raise FileNotFoundError(f"lock file missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    cfg = VolumeReversionConfig(**payload.get("parameters", {}))
    cfg.validate()
    if cfg != LOCKED_V19:
        raise ValueError(
            f"lock file {path} disagrees with the code's LOCKED_V19 config.\n"
            f"  file: {asdict(cfg)}\n  code: {asdict(LOCKED_V19)}")
    return cfg


def add_volume_filter(df: pd.DataFrame, cfg: VolumeReversionConfig
                      ) -> pd.DataFrame:
    """Attach relative volume and the tradeable mask.

    ``relative_volume`` already uses only bars strictly before each one, so no
    shift is applied here. A bar with no volume data is not tradeable rather
    than assumed quiet -- padded history reports zero volume, and treating that
    as "low volume" would trade exactly the fabricated bars the audit excludes.
    """
    out = df.copy()
    if "tick_volume" not in out.columns:
        raise ValueError("V19 needs a tick_volume column")
    rv = relative_volume(out["tick_volume"].tolist(), cfg.volume_lookback)
    out["relative_volume"] = rv
    out["volume_ok"] = np.isfinite(rv) & (rv > 0) & (rv < cfg.max_relative_volume)
    return out


def replay_v19(bars: pd.DataFrame, cfg: VolumeReversionConfig = LOCKED_V19,
               cost: CostModel = ZERO_COST,
               starting_balance: float = 10_000.0,
               instrument=None):
    """Replay V16's rules, entering only on below-average-volume bars.

    The filter is passed to V16's engine as an entry mask, so exits, stops and
    the time stop behave identically. V19 is therefore a pure entry filter on
    V16, and any difference in results is attributable to the filter alone.
    """
    cfg.validate()
    prepared = add_volume_filter(bars, cfg)
    return replay_v16(prepared, cfg, cost, starting_balance, instrument,
                      entry_filter=prepared["volume_ok"].to_numpy())
