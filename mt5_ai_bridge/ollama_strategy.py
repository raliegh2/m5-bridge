"""Ollama-backed reasoning layer (local, no API calls, no per-call cost).

Drop-in for ``strategy.evaluate_strategy`` / ``reasoning.ReasoningStrategy`` /
``ClaudeStrategy``: ``OllamaStrategy`` is callable as
``strategy_fn(market) -> Decision`` (``make_strategy`` in ``app.py`` selects
it when ``STRATEGY=ollama``).

Talks to a local Ollama server (the same one Open WebUI sits in front of) via
its HTTP API -- no Anthropic account, no API key, no network egress beyond
your own machine. Shares its throttling/fallback behaviour with
``ClaudeStrategy`` via ``llm_strategy_base``, and its instructions come from
the same ``agent_prompts/analyst.md`` file, so switching between the two
backends doesn't change the reasoning criteria, only where it runs.

Set ``OLLAMA_MODEL`` to whatever you've already pulled (``ollama pull ...``)
-- there's no universally-right default, so pick one you've verified
locally: ``ollama list`` shows what's available.
"""

import json
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from .llm_strategy_base import ThrottledLLMStrategy, load_prompt

SYSTEM_PROMPT = load_prompt("analyst")


@dataclass(frozen=True)
class OllamaStrategyConfig:
    model: str = "llama3.1"          # set OLLAMA_MODEL to whatever you've pulled
    host: str = "http://localhost:11434"
    min_confidence: float = 0.65
    min_interval_seconds: float = 60.0
    timeout_seconds: float = 30.0
    # Per-symbol overrides ({SYMBOL: value}) for independent per-symbol
    # reasoning; a symbol not listed uses the global values above.
    per_symbol_confidence: Dict[str, float] = field(default_factory=dict)
    per_symbol_interval: Dict[str, float] = field(default_factory=dict)


class OllamaStrategy(ThrottledLLMStrategy):
    """Callable strategy_fn built on a local Ollama call, with a rule-based
    fallback for errors, timeouts, and the gap between calls.

    ``client``, if provided, replaces the HTTP transport entirely -- pass a
    callable ``payload -> response_dict`` for tests instead of hitting a
    real server.
    """

    def __init__(self, config: Optional[OllamaStrategyConfig] = None,
                client: Optional[Callable[[dict], dict]] = None,
                fallback: Optional[Callable] = None) -> None:
        super().__init__(config or OllamaStrategyConfig(), client, fallback)

    def _call_model(self, market: dict) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(market, default=str)},
            ],
            "stream": False,
            "format": "json",  # ask Ollama to constrain output to valid JSON
        }
        body = self._client(payload) if self._client is not None \
            else self._post(payload)
        return body["message"]["content"]

    def _post(self, payload: dict) -> dict:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"{self.config.host.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                    req, timeout=self.config.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Could not reach Ollama at {self.config.host} "
                f"(is it running? try `ollama serve`): {e}"
            ) from e
