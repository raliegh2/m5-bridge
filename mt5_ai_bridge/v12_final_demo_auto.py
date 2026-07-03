"""Reserved module name for a future supervised execution adapter.

The final V12 project currently supports proposal and autonomous paper modes.
This module intentionally exposes no broker-order implementation.
"""


class FinalV12DemoAutoAdapter:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError(
            "Broker-order execution is not available. Use FinalV12PaperAdapter "
            "for autonomous live-quote paper testing."
        )
