"""Cost-stress calculator for the V14.3 GBP ICT scalp economics.

The validated 10-year replay simulated exits as pure R multiples with no
spread, commission, swap or slippage. This report shows why that matters:
for a 1.25R-target scalp, the break-even win rate rises quickly with
spread, and the whole researched edge disappears within about one pip.

Run:  python research/v14_4_cost_stress_report.py
"""
from __future__ import annotations

TARGET_R = 1.25
# Implied from the combined GBP ICT stream: PF 1.158 at 1.25R target
# => win rate w solves 1.25 * w / (1 - w) = 1.158  =>  w ~ 0.4809.
BACKTEST_WIN_RATE = 1.158 / (1.158 + TARGET_R)

STOP_PIPS = [5.0, 6.0, 7.5]
SPREADS = [0.0, 0.25, 0.50, 0.75, 1.00, 1.50]


def cost_adjusted_expectancy(
    stop_pips: float,
    spread_pips: float,
    win_rate: float,
    target_r: float = TARGET_R,
) -> tuple[float, float]:
    """Return (expectancy in R, break-even win rate) after spread costs.

    A win pays ``target_r * stop - spread`` pips and a loss costs
    ``stop + spread`` pips, both expressed against the researched
    zero-cost stop distance.
    """
    win_pips = target_r * stop_pips - spread_pips
    loss_pips = stop_pips + spread_pips
    expectancy_pips = win_rate * win_pips - (1.0 - win_rate) * loss_pips
    breakeven = loss_pips / (loss_pips + win_pips)
    return expectancy_pips / stop_pips, breakeven


def main() -> None:
    print(
        f"Backtest-implied win rate at {TARGET_R}R (GBP ICT PF 1.158): "
        f"{BACKTEST_WIN_RATE * 100.0:.2f}%"
    )
    print()
    header = "stop (pips) | spread (pips) | expectancy (R/trade) | break-even WR"
    print(header)
    print("-" * len(header))
    for stop in STOP_PIPS:
        for spread in SPREADS:
            expectancy, breakeven = cost_adjusted_expectancy(
                stop, spread, BACKTEST_WIN_RATE
            )
            flag = "  <-- EDGE GONE" if expectancy <= 0 else ""
            print(
                f"{stop:11.1f} | {spread:13.2f} | {expectancy:+20.4f} |"
                f" {breakeven * 100.0:13.2f}%{flag}"
            )
        print("-" * len(header))
    print(
        "\nReading: the researched zero-cost expectancy is only about "
        f"{cost_adjusted_expectancy(5.0, 0.0, BACKTEST_WIN_RATE)[0]:+.3f}R per"
        " trade. On a 5-pip stop, roughly 0.4 pips of round-trip cost"
        " erases it entirely. This is why the V14.4 spread gate defaults to"
        " 10% of the stop distance and why live per-setup expectancy is"
        " tracked instead of trusting the zero-cost replay."
    )


if __name__ == "__main__":
    main()
