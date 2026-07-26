"""Shared base for LLM-backed strategy_fns (Claude API, local Ollama, ...).

Both ``ClaudeStrategy`` and ``OllamaStrategy`` are drop-in strategy_fn(market)
-> Decision callables with identical throttling/caching semantics: one
instance is shared across every symbol/book the loop trades (make_strategy()
in app.py builds it once), so caching is keyed per symbol and, when
available, per candle -- never a single global "last decision". Getting this
wrong means a throttled call for one symbol can return another symbol's
decision (caught and fixed here after shipping the Claude-only version).

Subclasses implement ``_call_model(market) -> str`` (the raw text reply) and
nothing else; this base handles caching, JSON parsing, the confidence
threshold, and falling back to a rule-based strategy on any error.
"""

import json
import time
from pathlib import Path
from typing import Callable, Optional

from .enums import Signal
from .logging_config import get_logger
from .reasoning import ReasoningConfig, ReasoningStrategy
from .strategy import Decision

log = get_logger("llm_strategy")

PROMPTS_DIR = Path(__file__).parent / "agent_prompts"


def load_prompt(name: str) -> str:
    """Load an agent's instructions from mt5_ai_bridge/agent_prompts/<name>.md.

    Editing that file changes the agent's behaviour with no code change and
    no redeploy -- it's read fresh on every ClaudeStrategy/OllamaStrategy
    construction (not cached at import time).
    """
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text()


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


class ThrottledLLMStrategy:
    """Base class: per-symbol/per-candle cache + fallback + confidence gate.

    ``config`` must expose ``min_confidence`` and ``min_interval_seconds``.
    """

    def __init__(self, config, client=None,
                fallback: Optional[Callable] = None) -> None:
        self.config = config
        self._client = client  # injected for tests; created lazily otherwise
        self.fallback = fallback or ReasoningStrategy(ReasoningConfig())
        # symbol -> {"candle_time": ..., "call_time": float, "decision": Decision}
        self._cache: dict = {}

    def __call__(self, market: Optional[dict]) -> Decision:
        if not market:
            return Decision(Signal.WAIT, "No market data.", 0.0)

        symbol = market.get("symbol", "_default")
        candle_time = market.get("time")
        now = time.monotonic()
        entry = self._cache.get(symbol)

        if entry is not None:
            if candle_time is not None:
                # Reliable candle marker: skip only while the SAME candle is
                # still open. A new candle always gets a fresh call,
                # regardless of min_interval_seconds.
                if entry.get("candle_time") == candle_time:
                    return entry["decision"]
            else:
                # No candle marker (degraded/partial snapshot): fall back to
                # a wall-clock throttle as a safety net.
                if (now - entry["call_time"]) < self.config.min_interval_seconds:
                    return entry["decision"]

        try:
            text = self._call_model(market)
            decision = self._parse_decision(text)
        except Exception as e:  # network/auth/parsing -- never crash the loop
            log.warning("%s call failed for %s, using fallback: %s",
                       type(self).__name__, symbol, e)
            decision = self.fallback(market)

        self._cache[symbol] = {
            "candle_time": candle_time, "call_time": now, "decision": decision,
        }
        return decision

    def _parse_decision(self, text: str) -> Decision:
        payload = json.loads(_strip_code_fence(text))
        signal = Signal(str(payload["signal"]).upper())
        confidence = float(payload.get("confidence", 0.0))
        reason = str(payload.get("reason", "")) or "Model signal."

        if signal.is_trade and confidence < self.config.min_confidence:
            return Decision(
                Signal.WAIT,
                f"Signal below confidence threshold ({confidence:.2f}): {reason}",
                confidence,
            )
        return Decision(signal, reason, confidence)

    def _call_model(self, market: dict) -> str:
        raise NotImplementedError
