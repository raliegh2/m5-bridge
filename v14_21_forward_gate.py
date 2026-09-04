"""Build a fail-closed V14.21 promotion artifact from a forward ledger."""
from __future__ import annotations

import argparse
import json

from mt5_ai_bridge.v14_21_forward_gate import evidence_from_csv, write_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", help="Closed forward-trade CSV")
    parser.add_argument("--output", default="state/v14_21_forward_evidence.json")
    parser.add_argument("--phase", default="SHADOW", choices=("SHADOW", "DEMO"))
    args = parser.parse_args()
    payload = evidence_from_csv(args.ledger, phase=args.phase)
    write_evidence(payload, args.output)
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
