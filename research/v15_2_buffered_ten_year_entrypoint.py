"""Run V15.2 with the V14.9 projected-stress safety buffer."""
from __future__ import annotations

from research import v15_2_safe_ten_year_entrypoint as model

# V14.9 demonstrated that a 9.45% projected-stress ceiling is required to
# preserve room for a closing loss below the 9.60% closed-drawdown boundary.
model.v15.v149.v148.PROJECTED_STRESS_LIMIT = 9.45

if __name__ == "__main__":
    model.main()
