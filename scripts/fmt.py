#!/usr/bin/env python3
"""
Number formatting helpers for human-facing QoR output.

Full precision is preserved in CSV/JSON. These helpers format
only the user-facing text in the assistant.
"""


def fmt_ns(value) -> str:
    """Format a timing value in nanoseconds (3 decimal places)."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
        return f"{v:.3f} ns"
    except (TypeError, ValueError):
        return str(value)


def fmt_power(value) -> str:
    """Format power value. Convert W to mW when appropriate."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
        if abs(v) < 0.01:
            return f"{v*1000:.3f} mW"
        return f"{v:.6f} W"
    except (TypeError, ValueError):
        return str(value)


def fmt_area(value) -> str:
    """Format area in um^2 with comma separator."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
        return f"{v:,.1f} um^2"
    except (TypeError, ValueError):
        return str(value)


def fmt_util(value) -> str:
    """Format utilization as percentage."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
        pct = v * 100 if v <= 1.0 else v
        return f"{pct:.2f}%"
    except (TypeError, ValueError):
        return str(value)


def fmt_int(value) -> str:
    """Format an integer count."""
    if value is None:
        return "N/A"
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)


def fmt_slack(value) -> str:
    """Format slack value: 3 decimals, with ns suffix."""
    return fmt_ns(value)
