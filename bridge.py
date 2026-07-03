"""Default bridge entrypoint. Run with: python bridge.py

The final V12 profile uses named engines and a dedicated execution gate.  The
legacy generic strategy is deliberately blocked when V12_FINAL_PROFILE is set,
so copying the final demo environment cannot accidentally run the wrong model.
"""

import os

from mt5_ai_bridge.app import main


if __name__ == "__main__":
    if os.getenv("V12_FINAL_PROFILE"):
        raise SystemExit(
            "V12 final profile selected: legacy bridge.py is disabled. "
            "Use the named-engine V12 demo adapter and FinalDemoExecutor."
        )
    main()
