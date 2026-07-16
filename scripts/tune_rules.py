#!/usr/bin/env python3
"""
Clock period tuning rules for the auto-tuning loop.

Uses setup_slack (not raw WNS) for decisions.
Represents unavailable slack as None, never as sentinel values like 999.
"""

# Configurable parameters
TARGET_MARGIN_LOW = 1.0    # ns - stop if slack is between these
TARGET_MARGIN_HIGH = 3.0   # ns
STEP_DECREASE = 2.0        # ns - how much to reduce clock when margin is large
STEP_INCREASE = 2.0        # ns - how much to increase clock on failure
MIN_CLOCK_PERIOD = 5.0     # ns - never go below this
MAX_CLOCK_PERIOD = 50.0    # ns - never go above this


def choose_next_clock_period(setup_slack, current_clock: float) -> float:
    """
    Rule-based clock period adjustment.

    Args:
        setup_slack: Normalized setup slack in ns, or None if unavailable.
        current_clock: Current clock period in ns.

    Returns:
        Next clock period to try, or current_clock if no change needed.

    Rules:
    - If slack is None: cannot decide, return current (caller should stop).
    - If slack < 0: timing fails, increase clock period.
    - If slack > TARGET_MARGIN_HIGH: margin too large, decrease clock period.
    - If TARGET_MARGIN_LOW <= slack <= TARGET_MARGIN_HIGH: in target range, stop.
    - If 0 <= slack < TARGET_MARGIN_LOW: margin very small, keep or slightly increase.

    All adjustments are clamped to [MIN_CLOCK_PERIOD, MAX_CLOCK_PERIOD].
    """
    if setup_slack is None:
        # Cannot make a decision without data
        return current_clock

    if setup_slack < 0:
        # Timing failure: increase clock period
        next_clock = current_clock + STEP_INCREASE
        return min(next_clock, MAX_CLOCK_PERIOD)

    if setup_slack > TARGET_MARGIN_HIGH:
        # Large margin: decrease clock period
        next_clock = current_clock - STEP_DECREASE
        return max(next_clock, MIN_CLOCK_PERIOD)

    if setup_slack < TARGET_MARGIN_LOW:
        # Very small margin: might want to keep or slightly increase
        # Don't change - this is close enough to optimal
        return current_clock

    # In target range [TARGET_MARGIN_LOW, TARGET_MARGIN_HIGH]
    return current_clock
