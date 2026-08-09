"""Offline smoke test for the external signal pipeline; never connects to MT5."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import external_signal_manager as manager


def main() -> None:
    required=("validate_signal","process_file","manage_positions","close_all_positions","run")
    for name in required:
        assert callable(getattr(manager,name,None)),f"missing function: {name}"
    manager.INCOMING.mkdir(parents=True,exist_ok=True); manager.PROCESSED.mkdir(parents=True,exist_ok=True)
    signal_id=f"pipeline_test_{uuid.uuid4().hex[:8]}"
    signal={"signal_id":signal_id,"symbol":"EURUSD","direction":"BUY",
        "volume_lots":None,"stop_loss_price":1.095,"take_profit_price":None,
        "timestamp":datetime.now(timezone.utc).isoformat(),"source":"manual"}
    path=manager.INCOMING/f"{signal_id}.json"; path.write_text(json.dumps(signal,indent=2),encoding="utf-8")
    broker=manager.MockBroker(); state=manager.RiskState()
    accepted,reason=manager.process_file(path,broker,state,mock=True)
    assert accepted,f"dummy signal rejected: {reason}"
    assert not path.exists(),"incoming file was not archived"
    archives=list(manager.PROCESSED.glob(f"{signal_id}.accepted*.json"))
    assert archives,"archive missing"
    # The same-symbol guard must reject a second signal while one is open.
    broker._positions.append(SimpleNamespace(symbol="EURUSD", sl=1.095,
        price_open=1.10012, volume=.10, ticket=1, type=0, tp=0.0))
    second={**signal,"signal_id":f"{signal_id}_duplicate",
            "timestamp":datetime.now(timezone.utc).isoformat()}
    duplicate_ok,duplicate_reason,_=manager.validate_signal(second,broker,state)
    assert not duplicate_ok and "already open" in duplicate_reason,duplicate_reason
    for archive in archives:
        archive.unlink()
    print("SUCCESS: external signal manager imported and mock signal passed all risk rules.")


if __name__=="__main__": main()
