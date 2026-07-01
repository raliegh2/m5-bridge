"""Correct the two-position M30 swing engine.

Apply after:
    python tools/apply_closed_candle_patch.py
    python tools/apply_two_position_m30_swing_patch.py

Changes:
- Risk per position = 0.15%.
- Maximum combined swing risk = 0.30% through a two-position cap.
- M1/M5 become veto filters only; they no longer need to fully agree.
- Position 1 opens on a fresh completed M30 signal with H4/D1 alignment.
- Position 2 opens only on a later fresh completed M30 continuation signal,
  and only after Position 1 is protected at break-even or better.
- Swing only; no intraday book.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"
CONFIG = ROOT / "mt5_ai_bridge" / "config.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"Already patched: {label}")
        return text
    if old not in text:
        raise RuntimeError(f"Could not find patch target: {label}")
    return text.replace(old, new, 1)


def patch_config() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    text = text.replace('INTRADAY_RISK_PERCENT", "0.15"',
                        'INTRADAY_RISK_PERCENT", "0.10"')
    text = text.replace('SWING_RISK_PERCENT", "0.35"',
                        'SWING_RISK_PERCENT", "0.15"')
    text = text.replace('SWING_RISK_PERCENT", "0.30"',
                        'SWING_RISK_PERCENT", "0.15"')
    text = text.replace('MAX_OPEN_POSITIONS", "4"',
                        'MAX_OPEN_POSITIONS", "2"')
    text = text.replace('MAX_OPEN_POSITIONS", "7"',
                        'MAX_OPEN_POSITIONS", "2"')
    text = text.replace('MAX_SAME_DIRECTION", "4"',
                        'MAX_SAME_DIRECTION", "2"')
    text = text.replace('MAX_SAME_DIRECTION", "7"',
                        'MAX_SAME_DIRECTION", "2"')
    CONFIG.write_text(text, encoding="utf-8")


def patch_app() -> None:
    text = APP.read_text(encoding="utf-8")

    if '_LAST_SWING_M30_SETUP' not in text:
        text = text.replace(
            'log = get_logger("app")\n',
            'log = get_logger("app")\n\n'
            '_LAST_SWING_M30_SETUP = {}\n\n\n'
            'def _fresh_m30_setup(client, setup_time) -> bool:\n'
            '    key = id(client)\n'
            '    marker = str(setup_time)\n'
            '    if _LAST_SWING_M30_SETUP.get(key) == marker:\n'
            '        return False\n'
            '    _LAST_SWING_M30_SETUP[key] = marker\n'
            '    return True\n\n\n'
            'def _position_at_break_even_or_better(client, position) -> bool:\n'
            '    entry = float(getattr(position, "price_open", 0.0) or 0.0)\n'
            '    sl = float(getattr(position, "sl", 0.0) or 0.0)\n'
            '    if entry <= 0 or sl <= 0:\n'
            '        return False\n'
            '    if position.type == client.POSITION_TYPE_BUY:\n'
            '        return sl >= entry\n'
            '    return sl <= entry\n',
            1,
        )

    old = '''    observers_agree = bool(
        swing_bias is not None
        and m1_decision.signal is swing_bias
        and m5_decision.signal is swing_bias
    )
    m30_trigger = bool(swing_bias is not None and m30_decision.signal is swing_bias)
    swing_valid = bool(swing_bias is not None and observers_agree and m30_trigger)
    swing_conf = (min(h4_decision.confidence, d1_decision.confidence,
                      m1_decision.confidence, m5_decision.confidence,
                      m30_decision.confidence)
                  if swing_valid else 0.0)
'''
    new = '''    # M1/M5 are veto filters, not mandatory full confirmations.
    # A BUY is blocked only when both are strongly bearish; a SELL is blocked
    # only when both are strongly bullish.
    short_term_veto = bool(
        swing_bias is not None
        and m1_decision.signal.is_trade
        and m5_decision.signal.is_trade
        and m1_decision.signal is not swing_bias
        and m5_decision.signal is not swing_bias
        and m1_decision.confidence >= settings.strong_trend_confidence
        and m5_decision.confidence >= settings.strong_trend_confidence
    )
    m30_trigger = bool(swing_bias is not None and m30_decision.signal is swing_bias)
    swing_valid = bool(swing_bias is not None and m30_trigger and not short_term_veto)
    swing_conf = (min(h4_decision.confidence, d1_decision.confidence,
                      m30_decision.confidence)
                  if swing_valid else 0.0)
'''
    text = replace_once(text, old, new, "M1/M5 veto logic")

    old = '''            "reason": ("D1/H4 direction aligned; M1/M5 trend observers and "
                       "completed M30 entry trigger agree."
                       if swing_valid else
                       "Waiting for D1/H4, M1/M5 and completed M30 alignment."),
'''
    new = '''            "reason": ("D1/H4 aligned; completed M30 continuation confirmed; "
                       "M1/M5 did not produce a strong joint veto."
                       if swing_valid else
                       ("Blocked by a strong opposing M1/M5 veto."
                        if short_term_veto else
                        "Waiting for D1/H4 and completed M30 alignment.")),
'''
    text = replace_once(text, old, new, "swing state explanation")

    old = '''        # Never open more than two swing positions, and never open opposite
        # exposure. A second position is only allowed while the entire stack stays
        # aligned with the current completed-candle signal.
        desired = min(2, settings.max_open_positions)
'''
    new = '''        # Stage entries: Position 1 may open on the first fresh M30 setup.
        # Position 2 requires a later fresh M30 continuation signal and the first
        # position must already be protected at break-even or better.
        desired = min(2, settings.max_open_positions)
        m30_snap = decide("M30")[0] or {}
        if not _fresh_m30_setup(client, m30_snap.get("time")):
            log.info("swing engine waiting: current completed M30 setup already consumed")
            return
'''
    text = replace_once(text, old, new, "fresh staged M30 setup")

    old = '''        have = _count_side(client, positions, settings.symbol, side, book.magic)
        opened = 0

        while have + opened < desired:
'''
    new = '''        same_side_positions = [
            p for p in positions
            if p.symbol == settings.symbol
            and getattr(p, "magic", None) == book.magic
            and p.type == (client.POSITION_TYPE_BUY if side is Signal.BUY
                           else client.POSITION_TYPE_SELL)
        ]
        have = len(same_side_positions)
        if have >= 2:
            return
        if have == 1 and not _position_at_break_even_or_better(
                client, same_side_positions[0]):
            log.info("swing engine waiting: first position is not protected at break-even")
            return
        opened = 0

        # Exactly one position may be opened per fresh M30 setup.
        while have + opened < desired and opened < 1:
'''
    text = replace_once(text, old, new, "second-position break-even gate")

    APP.write_text(text, encoding="utf-8")


def main() -> None:
    patch_config()
    patch_app()
    print("Two-stage swing correction complete. Run: python -m pytest -q")


if __name__ == "__main__":
    main()
