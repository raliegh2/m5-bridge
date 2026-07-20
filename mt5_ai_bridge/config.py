"""Typed configuration loaded from environment / .env."""

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

from .enums import Mode


# Built-in per-symbol risk defaults (%% of balance). These apply ONLY when the
# user has not set a SWING_RISK_PERCENT_<SYM> / INTRADAY_RISK_PERCENT_<SYM>
# override in their .env. Gold (XAUUSD) moves far more than FX per lot and
# whipsaws hard in ranges, so it ships throttled well below the global risk so
# it can\'t dominate account drawdown out of the box.
_BUILTIN_SWING_RISK = {"XAUUSD": 0.2}
_BUILTIN_INTRADAY_RISK = {"XAUUSD": 0.1}


@dataclass(frozen=True)
class Settings:
    login: Optional[int]
    password: Optional[str]
    server: Optional[str]

    symbol: str
    symbols: tuple          # all pairs to trade concurrently (>=1)
    combined_risk_ceiling: float  # max aggregate OPEN risk %% across all symbols/engines
    mode: Mode
    timeframe: str

    strategy: str
    reasoning_threshold: float
    rsi_overbought: float
    rsi_oversold: float

    lot_size: float
    ny_size_multiplier: float
    ny_start_hour: int
    ny_end_hour: int

    swing_confidence: float
    intraday_sl_pips: float
    intraday_tp_pips: float
    swing_sl_pips: float
    swing_tp_pips: float

    stop_loss_pips: float
    take_profit_pips: float

    daily_max_loss: float
    total_max_loss: float
    max_open_positions: int
    max_trades_per_day: int

    strong_trend_confidence: float
    max_same_direction: int
    min_same_direction: int

    tp_stagger_step: float
    sl_stagger_step: float
    sl_floor_pips: float

    trail_enabled: bool
    trail_start_pips: float
    trail_distance_pips: float

    # ATR-based (volatility-adaptive) stops
    atr_enabled: bool
    atr_period: int
    atr_sl_mult: float
    atr_tp_mult: float
    atr_min_sl_pips: float
    atr_max_sl_pips: float

    # Fixed-fractional (risk %) position sizing
    risk_based_sizing: bool
    risk_percent: float
    intraday_risk_percent: float
    swing_risk_percent: float
    # Optional per-symbol overrides, tuples of (SYMBOL, percent).
    swing_risk_overrides: tuple
    intraday_risk_overrides: tuple
    pip_value_per_lot: float
    max_lot: float

    multi_book: bool
    require_trend_alignment: bool
    # Confirmation timeframes: every one must agree for a trade (M30 + H4 + D1).
    # `timeframe` above is the fast ENTRY read (M15).
    trend_tf_mid: str
    swing_tf_high: str
    swing_tf_higher: str
    swing_strong_max: int
    day_timeframe: str
    day_sl_pips: float
    day_tp_pips: float
    day_strong_max: int
    scalp_timeframe: str
    scalp_sl_pips: float
    scalp_tp_pips: float
    scalp_strong_max: int

    write_dashboard: bool
    dashboard_path: str
    dashboard_refresh_seconds: int
    serve_dashboard: bool
    dashboard_port: int
    dashboard_host: str

    console_status: bool

    loop_interval_seconds: float
    log_level: str
    db_path: str

    reconnect_attempts: int
    reconnect_delay_seconds: float

    # Safety: when True, refuse AUTOMATIC trading unless the account is a demo.
    require_demo: bool = True

    # Prop-firm challenge guard (FTMO-style drawdown protection).
    prop_firm: bool = False
    prop_start_balance: float = 0.0
    prop_max_daily_loss_pct: float = 5.0
    prop_max_total_loss_pct: float = 10.0
    prop_profit_target_pct: float = 8.0
    prop_trailing: bool = False
    prop_derisk_start_pct: float = 60.0

    # Regime router (Efficiency Ratio). OFF by default -- an opt-in filter that
    # only lets the trend engines trade in a DIRECTIONAL regime (ER >= min).
    regime_filter: bool = False
    regime_er_min: float = 0.30
    regime_er_overrides: tuple = ()

    @property
    def has_credentials(self) -> bool:
        return bool(self.login and self.password and self.server)

    @property
    def confirm_timeframes(self) -> tuple:
        """The higher timeframes that must all agree to confirm a trend."""
        return (self.trend_tf_mid, self.swing_tf_high, self.swing_tf_higher)

    def swing_risk_for(self, symbol: str) -> float:
        """Swing risk %% for a symbol.

        Precedence: .env override -> built-in per-symbol default (e.g. gold is
        throttled) -> the global SWING_RISK_PERCENT.
        """
        sym = (symbol or "").upper()
        overrides = dict(self.swing_risk_overrides)
        if sym in overrides:
            return overrides[sym]
        return _BUILTIN_SWING_RISK.get(sym, self.swing_risk_percent)

    def intraday_risk_for(self, symbol: str) -> float:
        """Intraday risk %% for a symbol.

        Precedence: .env override -> built-in per-symbol default (e.g. gold is
        throttled) -> the global INTRADAY_RISK_PERCENT.
        """
        sym = (symbol or "").upper()
        overrides = dict(self.intraday_risk_overrides)
        if sym in overrides:
            return overrides[sym]
        return _BUILTIN_INTRADAY_RISK.get(sym, self.intraday_risk_percent)

    def regime_er_min_for(self, symbol: str) -> float:
        """ER directional threshold for a symbol (.env override else global)."""
        return dict(self.regime_er_overrides).get(
            (symbol or "").upper(), self.regime_er_min)

    def prop_config(self):
        """Build a PropConfig from these settings."""
        from .prop import PropConfig
        state = (self.db_path[:-3] if self.db_path.endswith(".db")
                 else self.db_path) + "_prop.json" if self.db_path not in (
                 "", ":memory:") else "prop_state.json"
        return PropConfig(
            enabled=self.prop_firm, start_balance=self.prop_start_balance,
            max_daily_loss_pct=self.prop_max_daily_loss_pct,
            max_total_loss_pct=self.prop_max_total_loss_pct,
            profit_target_pct=self.prop_profit_target_pct,
            trailing=self.prop_trailing,
            derisk_start_pct=self.prop_derisk_start_pct, state_path=state)


def _get_int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw.strip())


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw.strip())


def _get_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw.strip() if raw is not None and raw.strip() != "" else default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_settings(dotenv: bool = True) -> Settings:
    """Build a Settings object from the environment."""
    if dotenv:
        load_dotenv()

    server = os.getenv("MT5_SERVER")
    symbol = _get_str("SYMBOL", "GBPUSD")
    raw_symbols = os.getenv("SYMBOLS", "")
    symbols = tuple(dict.fromkeys(
        t.strip().upper() for t in raw_symbols.replace(",", " ").split() if t.strip()
    )) or (symbol,)

    def _risk_overrides(prefix: str) -> tuple:
        out = []
        for key, val in os.environ.items():
            if key.startswith(prefix) and val.strip():
                sym = key[len(prefix):].strip().upper()
                try:
                    if sym:
                        out.append((sym, float(val.strip())))
                except ValueError:
                    pass
        return tuple(sorted(out))

    swing_overrides = _risk_overrides("SWING_RISK_PERCENT_")
    intraday_overrides = _risk_overrides("INTRADAY_RISK_PERCENT_")
    regime_overrides = _risk_overrides("REGIME_ER_MIN_")
    return Settings(
        login=_get_int("MT5_LOGIN"),
        password=os.getenv("MT5_PASSWORD"),
        server=server.strip() if server and server.strip() else None,
        symbol=symbol,
        symbols=symbols,
        combined_risk_ceiling=_get_float("COMBINED_RISK_CEILING", 2.5),
        mode=Mode.from_str(_get_str("MODE", "APPROVAL")),
        timeframe=_get_str("TIMEFRAME", "M15"),
        strategy=_get_str("STRATEGY", "trend").lower(),
        reasoning_threshold=_get_float("REASONING_THRESHOLD", 0.6),
        rsi_overbought=_get_float("RSI_OVERBOUGHT", 75),
        rsi_oversold=_get_float("RSI_OVERSOLD", 25),
        lot_size=_get_float("LOT_SIZE", 0.09),
        ny_size_multiplier=_get_float("NY_SIZE_MULTIPLIER", 2.0),
        ny_start_hour=_get_int("NY_START_HOUR", 12),
        ny_end_hour=_get_int("NY_END_HOUR", 21),
        swing_confidence=_get_float("SWING_CONFIDENCE", 0.7),
        intraday_sl_pips=_get_float("INTRADAY_SL_PIPS", 20),
        intraday_tp_pips=_get_float("INTRADAY_TP_PIPS", 40),
        swing_sl_pips=_get_float("SWING_SL_PIPS", 80),
        swing_tp_pips=_get_float("SWING_TP_PIPS", 160),
        stop_loss_pips=_get_float("STOP_LOSS_PIPS", 30),
        take_profit_pips=_get_float("TAKE_PROFIT_PIPS", 60),
        daily_max_loss=_get_float("DAILY_MAX_LOSS", 250),
        total_max_loss=_get_float("TOTAL_MAX_LOSS", 500),
        max_open_positions=_get_int("MAX_OPEN_POSITIONS", 7),
        max_trades_per_day=_get_int("MAX_TRADES_PER_DAY", 20),
        strong_trend_confidence=_get_float("STRONG_TREND_CONFIDENCE", 0.8),
        max_same_direction=_get_int("MAX_SAME_DIRECTION", 7),
        min_same_direction=_get_int("MIN_SAME_DIRECTION", 3),
        tp_stagger_step=_get_float("TP_STAGGER_STEP", 0.5),
        sl_stagger_step=_get_float("SL_STAGGER_STEP", 0.25),
        sl_floor_pips=_get_float("SL_FLOOR_PIPS", 10),
        trail_enabled=_get_bool("TRAIL_ENABLED", True),
        trail_start_pips=_get_float("TRAIL_START_PIPS", 20),
        trail_distance_pips=_get_float("TRAIL_DISTANCE_PIPS", 15),
        atr_enabled=_get_bool("ATR_ENABLED", True),
        atr_period=_get_int("ATR_PERIOD", 14),
        atr_sl_mult=_get_float("ATR_SL_MULT", 2.0),
        atr_tp_mult=_get_float("ATR_TP_MULT", 4.0),
        atr_min_sl_pips=_get_float("ATR_MIN_SL_PIPS", 8),
        atr_max_sl_pips=_get_float("ATR_MAX_SL_PIPS", 200),
        risk_based_sizing=_get_bool("RISK_BASED_SIZING", True),
        risk_percent=_get_float("RISK_PERCENT", 0.5),
        intraday_risk_percent=_get_float("INTRADAY_RISK_PERCENT", 0.11),
        swing_risk_percent=_get_float("SWING_RISK_PERCENT", 1.05),
        swing_risk_overrides=swing_overrides,
        intraday_risk_overrides=intraday_overrides,
        pip_value_per_lot=_get_float("PIP_VALUE_PER_LOT", 10.0),
        max_lot=_get_float("MAX_LOT", 2.0),
        multi_book=_get_bool("MULTI_BOOK", True),
        require_trend_alignment=_get_bool("REQUIRE_TREND_ALIGNMENT", True),
        trend_tf_mid=_get_str("TREND_TF_MID", "M30"),
        swing_tf_high=_get_str("SWING_TF_HIGH", "H4"),
        swing_tf_higher=_get_str("SWING_TF_HIGHER", "D1"),
        swing_strong_max=_get_int("SWING_STRONG_MAX", 2),
        day_timeframe=_get_str("DAY_TIMEFRAME", "M15"),
        day_sl_pips=_get_float("DAY_SL_PIPS", 15),
        day_tp_pips=_get_float("DAY_TP_PIPS", 30),
        day_strong_max=_get_int("DAY_STRONG_MAX", 2),
        scalp_timeframe=_get_str("SCALP_TIMEFRAME", "M5"),
        scalp_sl_pips=_get_float("SCALP_SL_PIPS", 8),
        scalp_tp_pips=_get_float("SCALP_TP_PIPS", 16),
        scalp_strong_max=_get_int("SCALP_STRONG_MAX", 1),
        write_dashboard=_get_bool("WRITE_DASHBOARD", True),
        dashboard_path=_get_str("DASHBOARD_PATH", "dashboard.html"),
        dashboard_refresh_seconds=_get_int("DASHBOARD_REFRESH_SECONDS", 1),
        serve_dashboard=_get_bool("SERVE_DASHBOARD", True),
        dashboard_port=_get_int("DASHBOARD_PORT", 8800),
        dashboard_host=_get_str("DASHBOARD_HOST", "127.0.0.1"),
        console_status=_get_bool("CONSOLE_STATUS", True),
        loop_interval_seconds=_get_float("LOOP_INTERVAL_SECONDS", 1),
        log_level=_get_str("LOG_LEVEL", "INFO"),
        db_path=_get_str("DB_PATH", "journal.db"),
        reconnect_attempts=_get_int("RECONNECT_ATTEMPTS", 3),
        reconnect_delay_seconds=_get_float("RECONNECT_DELAY_SECONDS", 5),
        require_demo=_get_bool("REQUIRE_DEMO", True),
        prop_firm=_get_bool("PROP_FIRM", False),
        prop_start_balance=_get_float("PROP_START_BALANCE", 0.0),
        prop_max_daily_loss_pct=_get_float("PROP_MAX_DAILY_LOSS_PCT", 5.0),
        prop_max_total_loss_pct=_get_float("PROP_MAX_TOTAL_LOSS_PCT", 10.0),
        prop_profit_target_pct=_get_float("PROP_PROFIT_TARGET_PCT", 8.0),
        prop_trailing=_get_bool("PROP_TRAILING", False),
        prop_derisk_start_pct=_get_float("PROP_DERISK_START_PCT", 60.0),
        regime_filter=_get_bool("REGIME_FILTER", False),
        regime_er_min=_get_float("REGIME_ER_MIN", 0.30),
        regime_er_overrides=regime_overrides,
    )
