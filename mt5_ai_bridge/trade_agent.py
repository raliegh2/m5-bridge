"""Per-symbol Trade Manager agent (position lifecycle: breakeven/partial/exit).

The Analyst (``claude_strategy`` / ``ollama_strategy``) decides ENTRIES. This
agent manages an already-open position: it reads the position + live momentum
and returns one action -- HOLD, BREAKEVEN, PARTIAL, or EXIT -- which the loop
then applies THROUGH the deterministic primitives in ``trade_manager.py`` (so
the agent decides *when*, but sizing/order-sends and the guardrails stay pure).

Design mirrors the entry strategies: one instance is shared across every open
position, cached and throttled PER TICKET, and on any error/timeout/malformed
reply it falls back to the deterministic rule floor -- the loop never stalls or
acts on garbage. Instructions come from ``agent_prompts/trade_manager.md``.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from .llm_strategy_base import _strip_code_fence, load_prompt
from .logging_config import get_logger
from .trade_manager import breakeven_sl

log = get_logger("trade_agent")

SYSTEM_PROMPT = load_prompt("trade_manager")

VALID_ACTIONS = ("HOLD", "BREAKEVEN", "PARTIAL", "EXIT")


@dataclass(frozen=True)
class TradeAction:
    """A managed action on one open position."""
    action: str = "HOLD"          # HOLD / BREAKEVEN / PARTIAL / EXIT
    fraction: float = 0.0         # portion to bank on PARTIAL (0..max)
    confidence: float = 0.0
    reason: str = ""

    @property
    def is_noop(self) -> bool:
        return self.action == "HOLD"


@dataclass(frozen=True)
class TradeManagerConfig:
    backend: str = "ollama"                 # "ollama" (local) or "claude" (API)
    model: str = "llama3.1"
    host: str = "http://localhost:11434"    # ollama only
    max_tokens: int = 256                   # claude only
    min_confidence: float = 0.6
    min_interval_seconds: float = 30.0
    breakeven_trigger_pips: float = 15.0
    max_partial_fraction: float = 0.5
    timeout_seconds: float = 8.0
    # Circuit breaker: after this many consecutive backend failures, stop
    # calling the model for cooldown_seconds and use the deterministic rule
    # floor instead -- so an unreachable model can never stall the live loop.
    fail_threshold: int = 2
    cooldown_seconds: float = 300.0
    # Per-symbol overrides ({SYMBOL: value}); a symbol not listed uses globals.
    per_symbol_confidence: Dict[str, float] = field(default_factory=dict)
    per_symbol_interval: Dict[str, float] = field(default_factory=dict)


def parse_trade_action(text: str, min_confidence: float,
                       max_fraction: float) -> TradeAction:
    """Parse + guardrail the model's JSON reply into a TradeAction.

    Unknown actions collapse to HOLD; a trade-affecting action below the
    confidence bar becomes HOLD; PARTIAL fraction is clamped to (0, max]."""
    payload = json.loads(_strip_code_fence(text))
    action = str(payload.get("action", "HOLD")).upper()
    if action not in VALID_ACTIONS:
        action = "HOLD"
    confidence = float(payload.get("confidence", 0.0) or 0.0)
    reason = str(payload.get("reason", "")) or "Trade manager."

    if action != "HOLD" and confidence < min_confidence:
        return TradeAction("HOLD", 0.0, confidence,
                           f"Below confidence ({confidence:.2f}): {reason}")

    if action == "PARTIAL":
        raw = float(payload.get("fraction", 0.0) or 0.0)
        frac = raw if raw > 0 else max_fraction
        frac = max(0.0, min(frac, max_fraction))
        if frac <= 0:
            return TradeAction("HOLD", 0.0, confidence,
                               "No valid partial fraction.")
        return TradeAction("PARTIAL", frac, confidence, reason)

    return TradeAction(action, 0.0, confidence, reason)


def rule_trade_action(ctx: dict, config: TradeManagerConfig) -> TradeAction:
    """Deterministic safety net used as the fallback (and, in the loop, as an
    always-on floor): move to breakeven once the trade has earned it, else hold.
    No momentum judgement -- that is the agent's job; this only guarantees risk
    comes off a winner even when the model is unavailable."""
    pip = ctx.get("pip") or 0.0
    entry, current = ctx.get("entry"), ctx.get("current")
    if entry is None or current is None or pip <= 0:
        return TradeAction("HOLD", 0.0, 0.0, "Rule floor: insufficient data.")
    is_buy = str(ctx.get("side", "BUY")).upper() == "BUY"
    be = breakeven_sl(is_buy, entry, current, ctx.get("sl") or 0.0, pip,
                      config.breakeven_trigger_pips)
    if be is not None:
        return TradeAction("BREAKEVEN", 0.0, 1.0,
                           "Rule floor: breakeven earned.")
    return TradeAction("HOLD", 0.0, 0.0, "Rule floor: hold.")


class TradeManagerAgent:
    """Callable ``ctx -> TradeAction`` with per-ticket throttling + rule floor
    fallback. ``client``, if provided, replaces the transport entirely: pass a
    callable ``(system_prompt, user_json) -> str`` for tests."""

    def __init__(self, config: Optional[TradeManagerConfig] = None,
                 client: Optional[Callable[[str, str], str]] = None) -> None:
        self.config = config or TradeManagerConfig()
        self._client = client
        # ticket -> {"call_time": float, "action": TradeAction}
        self._cache: dict = {}
        # Circuit breaker state (shared across tickets).
        self._fails = 0
        self._cooldown_until = 0.0

    def _min_confidence_for(self, symbol) -> float:
        return self.config.per_symbol_confidence.get(
            str(symbol).upper(), self.config.min_confidence)

    def _interval_for(self, symbol) -> float:
        return self.config.per_symbol_interval.get(
            str(symbol).upper(), self.config.min_interval_seconds)

    def __call__(self, ctx: Optional[dict]) -> TradeAction:
        if not ctx:
            return TradeAction("HOLD", 0.0, 0.0, "No position context.")
        ticket = ctx.get("ticket", "_")
        symbol = ctx.get("symbol", "_")
        now = time.monotonic()
        entry = self._cache.get(ticket)
        if entry is not None and (now - entry["call_time"]) < self._interval_for(symbol):
            return entry["action"]

        # Circuit breaker: while cooling down after repeated failures, skip the
        # model entirely and use the deterministic floor -- no blocking calls.
        if now < self._cooldown_until:
            action = rule_trade_action(ctx, self.config)
            self._cache[ticket] = {"call_time": now, "action": action}
            return action

        try:
            text = self._call_model(ctx)
            action = parse_trade_action(
                text, self._min_confidence_for(symbol),
                self.config.max_partial_fraction)
            self._fails = 0                       # healthy -> reset breaker
        except Exception as e:  # network/auth/parsing -- never crash the loop
            self._fails += 1
            if self._fails >= self.config.fail_threshold:
                self._cooldown_until = now + self.config.cooldown_seconds
                self._fails = 0
                log.warning("TradeManagerAgent backend unreachable; using rule "
                            "floor for %.0fs. Last error: %s",
                            self.config.cooldown_seconds, e)
            else:
                log.warning("TradeManagerAgent failed for ticket %s: %s", ticket, e)
            action = rule_trade_action(ctx, self.config)

        self._cache[ticket] = {"call_time": now, "action": action}
        return action

    def _call_model(self, ctx: dict) -> str:
        user = json.dumps(ctx, default=str)
        if self._client is not None:
            return self._client(SYSTEM_PROMPT, user)
        if self.config.backend == "claude":
            return self._claude(user)
        return self._ollama(user)

    def _ollama(self, user: str) -> str:
        import urllib.error
        import urllib.request
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
        }
        req = urllib.request.Request(
            f"{self.config.host.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(
                    req, timeout=self.config.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Could not reach Ollama at {self.config.host} "
                f"(is it running? try `ollama serve`): {e}") from e
        return body["message"]["content"]

    def _claude(self, user: str) -> str:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=self.config.model, max_tokens=self.config.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in response.content
                       if getattr(b, "type", None) == "text")
