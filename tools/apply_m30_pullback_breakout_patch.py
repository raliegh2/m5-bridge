"""Add a completed-candle M30 pullback-breakout entry to the staged swing engine.

Apply after:
    python tools/apply_closed_candle_patch.py
    python tools/apply_two_position_m30_swing_patch.py
    python tools/apply_two_stage_swing_correction.py

Rules:
- H4 and D1 define swing direction.
- M30 must pull back to EMA20 while remaining on the correct side of EMA50.
- A later completed M30 candle must break the pullback candle high/low.
- M1/M5 remain joint veto filters only.
- Position 2 still requires Position 1 at break-even and a new M30 setup.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"Already patched: {label}")
        return text
    if old not in text:
        raise RuntimeError(f"Could not find patch target: {label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = APP.read_text(encoding="utf-8")

    if '_ARMED_M30_PULLBACK' not in text:
        text = text.replace(
            '_LAST_SWING_M30_SETUP = {}\n',
            '_LAST_SWING_M30_SETUP = {}\n_ARMED_M30_PULLBACK = {}\n',
            1,
        )

    old = '''    m30_trigger = bool(swing_bias is not None and m30_decision.signal is swing_bias)
    swing_valid = bool(swing_bias is not None and m30_trigger and not short_term_veto)
    swing_conf = (min(h4_decision.confidence, d1_decision.confidence,
                      m30_decision.confidence)
                  if swing_valid else 0.0)
'''

    new = '''    m30_snap = decide("M30")[0] or {}
    pullback_key = id(client)
    armed = _ARMED_M30_PULLBACK.get(pullback_key)

    # Reset the armed pullback whenever the higher-timeframe direction changes.
    if swing_bias is None or (armed and armed.get("side") is not swing_bias):
        armed = None
        _ARMED_M30_PULLBACK[pullback_key] = None

    m30_pullback = False
    if swing_bias is Signal.BUY:
        m30_pullback = bool(
            m30_snap.get("low", float("inf")) <= m30_snap.get("ema_20", 0)
            and m30_snap.get("close", 0) > m30_snap.get("ema_20", 0)
            and m30_snap.get("close", 0) > m30_snap.get("ema_50", 0)
        )
    elif swing_bias is Signal.SELL:
        m30_pullback = bool(
            m30_snap.get("high", float("-inf")) >= m30_snap.get("ema_20", 0)
            and m30_snap.get("close", 0) < m30_snap.get("ema_20", 0)
            and m30_snap.get("close", 0) < m30_snap.get("ema_50", 0)
        )

    if m30_pullback:
        armed = {
            "side": swing_bias,
            "time": m30_snap.get("time"),
            "high": m30_snap.get("high"),
            "low": m30_snap.get("low"),
        }
        _ARMED_M30_PULLBACK[pullback_key] = armed

    breakout = False
    if armed and armed.get("time") != m30_snap.get("time"):
        if swing_bias is Signal.BUY:
            breakout = bool(
                m30_snap.get("close", 0) > armed.get("high", float("inf"))
                and m30_decision.signal is Signal.BUY
            )
        elif swing_bias is Signal.SELL:
            breakout = bool(
                m30_snap.get("close", 0) < armed.get("low", float("-inf"))
                and m30_decision.signal is Signal.SELL
            )

    swing_valid = bool(swing_bias is not None and breakout and not short_term_veto)
    if swing_valid:
        _ARMED_M30_PULLBACK[pullback_key] = None
    swing_conf = (min(h4_decision.confidence, d1_decision.confidence,
                      m30_decision.confidence)
                  if swing_valid else 0.0)
'''
    text = replace_once(text, old, new, "M30 pullback and breakout trigger")

    old2 = '''            "reason": ("D1/H4 aligned; completed M30 continuation confirmed; "
                       "M1/M5 did not produce a strong joint veto."
                       if swing_valid else
                       ("Blocked by a strong opposing M1/M5 veto."
                        if short_term_veto else
                        "Waiting for D1/H4 and completed M30 alignment.")),
'''
    new2 = '''            "reason": ("D1/H4 aligned; M30 pullback completed and a later "
                       "M30 breakout confirmed; M1/M5 did not jointly veto."
                       if swing_valid else
                       ("Blocked by a strong opposing M1/M5 veto."
                        if short_term_veto else
                        "Waiting for an M30 EMA20 pullback and later breakout.")),
'''
    text = replace_once(text, old2, new2, "pullback strategy explanation")

    APP.write_text(text, encoding="utf-8")
    print("M30 pullback-breakout patch complete. Run: python -m pytest -q")


if __name__ == "__main__":
    main()
