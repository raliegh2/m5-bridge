"""Append-only machine and human journals for demo learning evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _human_reason(event: dict[str, Any]) -> str:
    signal = event.get("signal") or event.get("position") or {}
    metadata = signal.get("metadata") or {}
    explicit = metadata.get("decision_reason") or metadata.get("reason")
    if explicit:
        return str(explicit)
    return (
        f"{signal.get('engine', 'UNKNOWN')} generated a "
        f"{signal.get('side', 'UNKNOWN')} {signal.get('setup', 'UNKNOWN')} "
        f"candidate from {metadata.get('source', 'the locked strategy rules')}."
    )


def append_learning_event(
    jsonl_path: str | Path,
    document_path: str | Path,
    event: dict[str, Any],
) -> None:
    """Write one credential-free event to training JSONL and readable Markdown."""
    machine = Path(jsonl_path)
    document = Path(document_path)
    machine.parent.mkdir(parents=True, exist_ok=True)
    document.parent.mkdir(parents=True, exist_ok=True)
    with machine.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, default=str, sort_keys=True) + "\n")
        handle.flush()

    signal = event.get("signal") or event.get("position") or {}
    result = event.get("result") or {}
    stamp = event.get("created_at") or event.get("closed_at") or "UNKNOWN"
    lines = [
        f"## {event.get('event', 'EVENT')} — {stamp}",
        "",
        f"- Event ID: `{event.get('event_id', 'UNKNOWN')}`",
        f"- Symbol: {signal.get('symbol', 'UNKNOWN')}",
        f"- Engine/setup: {signal.get('engine', 'UNKNOWN')} / {signal.get('setup', 'UNKNOWN')}",
        f"- Direction: {signal.get('side', 'UNKNOWN')}",
        f"- Decision: {result.get('code', event.get('event', 'UNKNOWN'))}",
        f"- Why: {_human_reason(event)}",
    ]
    if result:
        lines.append(f"- Execution explanation: {result.get('message', '')}")
        lines.append(f"- Ticket: {result.get('ticket') or 'not placed'}")
        lines.append(f"- Executed risk: {result.get('risk_percent', 0.0)}%")
        request = (result.get("proposal") or {}).get("request") or {}
        if request:
            lines.append(f"- Entry / stop / target: {request.get('price')} / {request.get('sl')} / {request.get('tp')}")
            lines.append(f"- Volume: {request.get('volume')}")
    if event.get("event") == "TRADE_CLOSED":
        lines.append(f"- Broker-net P/L: {event.get('pnl', 0.0)}")
        lines.append(f"- Result in R: {event.get('r_multiple')}")
    lines.extend(["", "---", ""])
    with document.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.flush()


__all__ = ["append_learning_event"]
