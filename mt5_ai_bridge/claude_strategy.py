"""Claude-backed reasoning layer (Anthropic API).

Drop-in for ``strategy.evaluate_strategy`` / ``reasoning.ReasoningStrategy``:
``ClaudeStrategy`` is callable as ``strategy_fn(market) -> Decision`` and can
be used directly by the live loop and the backtester (``make_strategy`` in
``app.py`` selects it when ``STRATEGY=claude``).

This calls the Anthropic API, so it costs money per call -- see
``ollama_strategy.py`` for a local, no-API-cost alternative using Ollama.
Both share their throttling/fallback behaviour via ``llm_strategy_base``.

The system prompt lives in ``agent_prompts/analyst.md``, not inline here --
edit that file to change the agent's signal-selection behaviour without
touching code.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from .llm_strategy_base import ThrottledLLMStrategy, load_prompt

SYSTEM_PROMPT = load_prompt("analyst")


@dataclass(frozen=True)
class ClaudeStrategyConfig:
    model: str = "claude-sonnet-5"
    min_confidence: float = 0.65
    min_interval_seconds: float = 60.0
    max_tokens: int = 256


class ClaudeStrategy(ThrottledLLMStrategy):
    """Callable strategy_fn built on a live Claude call, with a rule-based
    fallback for errors, timeouts, and the gap between calls."""

    def __init__(self, config: Optional[ClaudeStrategyConfig] = None,
                client=None, fallback: Optional[Callable] = None) -> None:
        super().__init__(config or ClaudeStrategyConfig(), client, fallback)

    @property
    def client(self):
        if self._client is None:
            import anthropic  # imported lazily so the package is optional
            self._client = anthropic.Anthropic()
        return self._client

    def _call_model(self, market: dict) -> str:
        import json
        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user",
                      "content": json.dumps(market, default=str)}],
        )
        return "".join(
            block.text for block in response.content
            if getattr(block, "type", None) == "text"
        )
