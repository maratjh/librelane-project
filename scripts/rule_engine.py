#!/usr/bin/env python3
"""
Rule-based QoR diagnosis engine for the LibreLane Conversational Assistant.

Uses normalized setup_slack and hold_slack for all pass/fail decisions.
Never conflates positive WS with positive WNS.
Reports raw WNS and WS transparently alongside normalized slack.

Provides:
- Per-run analysis with structured diagnosis objects.
- Multi-run comparison and best-run selection.
- Recommendations with evidence and severity levels.
- Handles missing data honestly without fabrication.
- Configurable thresholds for diagnosis categories.
"""

import json

# ---------------------------------------------------------------------------
# Configurable thresholds
# ---------------------------------------------------------------------------

LARGE_SETUP_MARGIN_NS = 5.0
CRITICAL_SETUP_MARGIN_NS = 0.5
HIGH_UTILIZATION_THRESHOLD = 0.80
VERY_HIGH_UTILIZATION_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_float(value):
    """Convert a value to float, returning None if not possible."""
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _raw(row, key):
    """Get raw string value or None."""
    v = row.get(key)
    if v is None or str(v).strip() == "":
        return None
    return str(v).strip()


def _get_setup_slack(row):
    """Get the normalized setup slack from the row."""
    return safe_float(row.get("setup_slack"))


def _get_hold_slack(row):
    """Get the normalized hold slack from the row."""
    return safe_float(row.get("hold_slack"))


# ---------------------------------------------------------------------------
# Individual metric explanations
# ---------------------------------------------------------------------------

def _explain_setup(row):
    """Explain setup timing using correct WNS/WS terminology."""
    setup_wns = safe_float(row.get("setup_wns"))
    setup_ws = safe_float(row.get("setup_ws"))
    setup_slack = _get_setup_slack(row)
    tns = safe_float(row.get("setup_tns"))

    if setup_slack is None:
        return "Setup timing data is not available in the parsed reports."

    if setup_slack < 0:
        # Timing failure: WNS is negative
        msg = (
            f"Setup timing FAILS. "
            f"Setup WNS = {_raw(row, 'setup_wns')} ns. "
            f"The critical path is slower than the target clock period."
        )
        if tns is not None and tns < 0:
            msg += f" Setup TNS = {_raw(row, 'setup_tns')} ns."
        return msg

    # Timing met
    parts = ["Setup timing is MET."]
    if setup_wns is not None:
        parts.append(f"OpenROAD setup WNS = {_raw(row, 'setup_wns')} ns.")
    if setup_ws is not None:
        parts.append(f"Worst setup slack (margin) = {_raw(row, 'setup_ws')} ns.")

    if setup_slack > LARGE_SETUP_MARGIN_NS:
        parts.append("This is a large margin; CLOCK_PERIOD can likely be reduced.")
    elif setup_slack < CRITICAL_SETUP_MARGIN_NS:
        parts.append("Margin is very small; timing is at risk under variation.")

    return " ".join(parts)


def _explain_hold(row):
    """Explain hold timing using correct WNS/WS terminology."""
    hold_wns = safe_float(row.get("hold_wns"))
    hold_ws = safe_float(row.get("hold_ws"))
    hold_slack = _get_hold_slack(row)
    tns = safe_float(row.get("hold_tns"))

    if hold_slack is None:
        return "Hold timing data is not available in the parsed reports."

    if hold_slack < 0:
        msg = (
            f"Hold timing FAILS. "
            f"Hold WNS = {_raw(row, 'hold_wns')} ns. "
            f"Data may arrive too early at the capturing register."
        )
        if tns is not None and tns < 0:
            msg += f" Hold TNS = {_raw(row, 'hold_tns')} ns."
        return msg

    parts = ["Hold timing is MET."]
    if hold_wns is not None:
        parts.append(f"OpenROAD hold WNS = {_raw(row, 'hold_wns')} ns.")
    if hold_ws is not None:
        parts.append(f"Worst hold slack (margin) = {_raw(row, 'hold_ws')} ns.")

    return " ".join(parts)


def _explain_area(row):
    """Explain area and utilization with density analysis."""
    area = _raw(row, "area")
    util_str = _raw(row, "utilization")

    if area is None and util_str is None:
        return "Area and utilization data are not available."

    parts = []
    if area:
        parts.append(f"Core area = {area} um^2.")
    if util_str:
        util = safe_float(util_str)
        parts.append(f"Utilization = {util_str}")
        if util is not None:
            pct = util * 100 if util <= 1.0 else util
            if pct > VERY_HIGH_UTILIZATION_THRESHOLD * 100:
                parts.append(
                    f"({pct:.1f}% is very high; this increases routing congestion risk "
                    f"and may make timing repair harder)."
                )
            elif pct > HIGH_UTILIZATION_THRESHOLD * 100:
                parts.append(f"({pct:.1f}% is relatively high; routing should be monitored).")
            else:
                parts.append(f"({pct:.1f}% is moderate).")
        else:
            parts.append(".")

    return " ".join(parts)


def _explain_power(row):
    """Explain power breakdown."""
    total = _raw(row, "power_total")
    if total is None:
        return "Power data is not available in the parsed reports."

    parts = [f"Total power = {total} W"]
    internal = _raw(row, "power_internal")
    switching = _raw(row, "power_switching")
    leakage = _raw(row, "power_leakage")

    if internal:
        parts.append(f"internal = {internal} W")
    if switching:
        parts.append(f"switching = {switching} W")
    if leakage:
        parts.append(f"leakage = {leakage} W")

    return "Power breakdown: " + ", ".join(parts) + "."


def _explain_slew(row):
    """Explain slew violations with corner info."""
    slew = safe_float(row.get("slew_violations"))
    if slew is None:
        return "Slew violation count is not available."

    if slew > 0:
        msg = (
            f"Slew violations: {int(slew)} globally. "
            "Some signal transitions exceed the maximum allowed transition time."
        )
        worst_corner = _raw(row, "worst_setup_corner")
        if worst_corner:
            msg += f" Worst corner for timing: {worst_corner}."
        msg += (
            " Potential investigation areas: buffering, driver sizing, "
            "placement density, high-capacitance routing."
        )
        return msg
    return "No slew violations detected."


def _explain_cap(row):
    """Explain capacitance violations."""
    cap = safe_float(row.get("cap_violations"))
    if cap is None:
        return "Capacitance violation count is not available."

    if cap > 0:
        return (
            f"Capacitance violations: {int(cap)}. "
            "Some nets exceed the driving cell's load capacity. "
            "Investigate routing, fanout, buffering, or constraints."
        )
    return "No capacitance violations detected."


def _explain_fanout(row):
    """Explain fanout violations."""
    fanout = safe_float(row.get("fanout_violations"))
    if fanout is None:
        return "Fanout violation count is not available."

    if fanout > 0:
        return (
            f"Fanout violations: {int(fanout)}. "
            "Some nets drive too many sinks. "
            "Consider buffering or restructuring high-fanout nets."
        )
    return "No fanout violations detected."


def _explain_congestion(row):
    """Explain congestion with correct distinction between DRC and overflow."""
    status = _raw(row, "congestion_status")
    overflow = _raw(row, "congestion_overflow")
    drc = safe_float(row.get("route_drc_errors"))

    parts = []

    if overflow:
        parts.append(f"Global-routing congestion overflow: {overflow}.")
    elif status == "overflow_data_available":
        parts.append("Congestion overflow data is available in reports.")

    if drc is not None:
        if drc == 0:
            parts.append(
                "Detailed routing completed with 0 final DRC errors. "
                "This means no remaining design-rule violations after routing, "
                "but does NOT prove zero congestion occurred during global routing."
            )
        else:
            parts.append(
                f"Routing has {int(drc)} final DRC errors, which may indicate "
                "congestion-related routing failures."
            )

    if not overflow and (status is None or status in ("not_available", "routing_data_found")):
        parts.append(
            "Explicit global-routing overflow metrics are unavailable, "
            "so congestion cannot be quantified."
        )

    if not parts:
        return (
            "Congestion data is not available. "
            "No congestion conclusion can be drawn."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Recommendation logic (priority-ordered)
# ---------------------------------------------------------------------------

def recommend_action_structured(row) -> dict:
    """Generate a structured recommendation with priority-ordered blocking checks.

    Priority hierarchy:
      1. Missing/invalid report data
      2. Final routing DRC errors
      3. Hold timing failure
      4. Setup timing failure
      5. Slew violations
      6. Capacitance violations
      7. Fanout violations
      8. Explicit congestion overflow
      9. Very high utilization
     10. Very small positive setup margin
     11. Reasonable setup margin
     12. Large setup margin, otherwise clean
     13. Clock-period optimization (only when sufficiently clean)

    Returns dict with: primary_category, priority, blocking_issues,
    secondary_opportunities, text.
    """
    setup_slack = _get_setup_slack(row)
    hold_slack = _get_hold_slack(row)
    drc = safe_float(row.get("route_drc_errors"))
    slew = safe_float(row.get("slew_violations"))
    cap = safe_float(row.get("cap_violations"))
    fanout = safe_float(row.get("fanout_violations"))
    overflow = _raw(row, "congestion_overflow")
    util = safe_float(row.get("utilization"))

    blocking = []
    secondary = []

    # Priority 1: Missing data
    if setup_slack is None:
        return {
            "primary_category": "missing_data",
            "priority": 1,
            "blocking_issues": ["setup_slack_unavailable"],
            "secondary_opportunities": [],
            "text": "Parse or inspect timing reports before making clock tuning decisions.",
        }

    # Priority 2: Routing DRC errors
    if drc is not None and drc > 0:
        blocking.append("route_drc_errors")

    # Priority 3: Hold failure
    if hold_slack is not None and hold_slack < 0:
        blocking.append("hold_timing_failure")

    # Priority 4: Setup failure
    if setup_slack < 0:
        blocking.append("setup_timing_failure")

    # Priority 5-7: Electrical violations
    if slew is not None and slew > 0:
        blocking.append("slew_violations")
    if cap is not None and cap > 0:
        blocking.append("cap_violations")
    if fanout is not None and fanout > 0:
        blocking.append("fanout_violations")

    # Priority 8: Congestion overflow
    if overflow and safe_float(overflow) is not None and safe_float(overflow) > 0:
        blocking.append("congestion_overflow")

    # Priority 9: Very high utilization
    if util is not None:
        pct = util * 100 if util <= 1.0 else util
        if pct > VERY_HIGH_UTILIZATION_THRESHOLD * 100:
            blocking.append("very_high_utilization")

    # Determine secondary opportunities
    if setup_slack > LARGE_SETUP_MARGIN_NS and "setup_timing_failure" not in blocking:
        secondary.append("reduce_clock_period_after_cleanup")

    # Generate text based on highest-priority blocking issue (DRC > hold > setup > electrical)
    if "route_drc_errors" in blocking:
        text = "Resolve routing DRC errors before considering clock optimization."
        cat = "route_drc_errors"
        pri = 2
    elif "hold_timing_failure" in blocking:
        text = "Fix hold violations: check min-delay paths, add buffers, review constraints."
        cat = "hold_timing_failure"
        pri = 3
    elif "setup_timing_failure" in blocking:
        text = "Increase CLOCK_PERIOD or optimize the critical path, then rerun the flow."
        cat = "setup_timing_failure"
        pri = 4
    elif blocking:
        # Electrical violations or congestion or utilization are blocking
        issues_text = ", ".join(b.replace("_", " ") for b in blocking)
        text = f"Fix blocking issues first: {issues_text}."
        if secondary:
            text += " After cleanup, a smaller CLOCK_PERIOD may be feasible."
        cat = "electrical_violations"
        pri = 5
    elif setup_slack < CRITICAL_SETUP_MARGIN_NS:
        text = (
            "Margin is very thin. Current CLOCK_PERIOD may fail under variation. "
            "Consider keeping or slightly increasing the clock period."
        )
        cat = "small_margin"
        pri = 10
    elif setup_slack <= LARGE_SETUP_MARGIN_NS:
        text = "Current CLOCK_PERIOD is reasonable. Consider testing a slightly smaller value."
        cat = "reasonable_margin"
        pri = 11
    else:
        # Large margin and no blocking issues
        text = (
            "Try a smaller CLOCK_PERIOD in the next experiment. "
            "Current target appears conservative and no blocking issues exist."
        )
        cat = "clock_optimization"
        pri = 13

    return {
        "primary_category": cat,
        "priority": pri,
        "blocking_issues": blocking,
        "secondary_opportunities": secondary,
        "text": text,
    }


def recommend_action(row) -> str:
    """Generate a primary recommendation string (backward-compatible API).

    Uses the structured priority logic internally.
    """
    result = recommend_action_structured(row)
    return result["text"]


# ---------------------------------------------------------------------------
# Structured diagnosis objects
# ---------------------------------------------------------------------------

def diagnose_run(row) -> list:
    """Produce a list of structured diagnosis objects for one run."""
    diagnoses = []
    run_id = row.get("run_id", "unknown")
    sources = row.get("source_files", "")

    setup_slack = _get_setup_slack(row)
    hold_slack = _get_hold_slack(row)
    slew = safe_float(row.get("slew_violations"))
    cap = safe_float(row.get("cap_violations"))
    fanout = safe_float(row.get("fanout_violations"))
    util = safe_float(row.get("utilization"))

    # Setup timing diagnosis
    if setup_slack is not None:
        if setup_slack < 0:
            diagnoses.append({
                "run_id": run_id,
                "severity": "high",
                "category": "setup_timing",
                "finding": "Setup timing fails",
                "evidence": {"setup_slack": setup_slack, "source": sources},
                "recommendation": "Increase CLOCK_PERIOD or optimize the critical path.",
            })
        elif setup_slack < CRITICAL_SETUP_MARGIN_NS:
            diagnoses.append({
                "run_id": run_id,
                "severity": "warning",
                "category": "setup_timing",
                "finding": "Setup timing met with very small margin",
                "evidence": {"setup_slack": setup_slack, "source": sources},
                "recommendation": "Margin may be insufficient under variation. Monitor closely.",
            })
        elif setup_slack <= LARGE_SETUP_MARGIN_NS:
            diagnoses.append({
                "run_id": run_id,
                "severity": "ok",
                "category": "setup_timing",
                "finding": "Setup timing met with reasonable margin",
                "evidence": {"setup_slack": setup_slack, "source": sources},
                "recommendation": "Current timing target is reasonable.",
            })
        else:
            diagnoses.append({
                "run_id": run_id,
                "severity": "info",
                "category": "setup_timing",
                "finding": "Setup timing met with large margin",
                "evidence": {"setup_slack": setup_slack, "source": sources},
                "recommendation": "Try a smaller CLOCK_PERIOD in the next experiment.",
            })

    # Hold timing diagnosis
    if hold_slack is not None:
        if hold_slack < 0:
            diagnoses.append({
                "run_id": run_id,
                "severity": "high",
                "category": "hold_timing",
                "finding": "Hold timing fails",
                "evidence": {"hold_slack": hold_slack, "source": sources},
                "recommendation": "Check min-delay paths, add buffers, review hold constraints.",
            })
        else:
            diagnoses.append({
                "run_id": run_id,
                "severity": "ok",
                "category": "hold_timing",
                "finding": "Hold timing met",
                "evidence": {"hold_slack": hold_slack, "source": sources},
                "recommendation": "No hold action needed.",
            })

    # Utilization diagnosis
    if util is not None:
        pct = util * 100 if util <= 1.0 else util
        if pct > VERY_HIGH_UTILIZATION_THRESHOLD * 100:
            diagnoses.append({
                "run_id": run_id,
                "severity": "warning",
                "category": "utilization",
                "finding": f"Very high utilization ({pct:.1f}%)",
                "evidence": {"utilization": util, "source": sources},
                "recommendation": (
                    "High density increases routing congestion risk and may "
                    "contribute to slew or timing-repair pressure."
                ),
            })
        elif pct > HIGH_UTILIZATION_THRESHOLD * 100:
            diagnoses.append({
                "run_id": run_id,
                "severity": "info",
                "category": "utilization",
                "finding": f"Relatively high utilization ({pct:.1f}%)",
                "evidence": {"utilization": util, "source": sources},
                "recommendation": "Monitor routing quality.",
            })

    # Slew violations
    if slew is not None and slew > 0:
        diagnoses.append({
            "run_id": run_id,
            "severity": "medium",
            "category": "slew_violations",
            "finding": f"Slew violations detected: {int(slew)}",
            "evidence": {"slew_violations": int(slew), "source": sources},
            "recommendation": "Check buffering, gate sizing, placement, and routing.",
        })

    # Capacitance violations
    if cap is not None and cap > 0:
        diagnoses.append({
            "run_id": run_id,
            "severity": "medium",
            "category": "cap_violations",
            "finding": f"Capacitance violations detected: {int(cap)}",
            "evidence": {"cap_violations": int(cap), "source": sources},
            "recommendation": "Check routing load, fanout, and buffering.",
        })

    # Fanout violations
    if fanout is not None and fanout > 0:
        diagnoses.append({
            "run_id": run_id,
            "severity": "medium",
            "category": "fanout_violations",
            "finding": f"Fanout violations detected: {int(fanout)}",
            "evidence": {"fanout_violations": int(fanout), "source": sources},
            "recommendation": "Add buffers or restructure high-fanout nets.",
        })

    return diagnoses


# ---------------------------------------------------------------------------
# High-level analysis functions (used by assistant.py)
# ---------------------------------------------------------------------------

def explain_qor_row(row) -> str:
    """Produce a full natural-language explanation for one run row."""
    run_id = _raw(row, "run_id") or "this run"
    clock = _raw(row, "clock_period")

    intro = f"For {run_id}"
    if clock:
        intro += f" (CLOCK_PERIOD = {clock} ns)"
    intro += ":"

    parts = [
        intro,
        _explain_setup(row),
        _explain_hold(row),
        _explain_area(row),
        _explain_power(row),
        _explain_slew(row),
        _explain_cap(row),
        _explain_fanout(row),
        _explain_congestion(row),
        f"Recommendation: {recommend_action(row)}",
    ]

    sources = _raw(row, "source_files")
    if sources:
        parts.append(f"Evidence source: {sources}")

    return " ".join(parts)


def analyze_row(row) -> str:
    """Short-form analysis of a row."""
    return " ".join([
        _explain_setup(row),
        _explain_hold(row),
        _explain_slew(row),
        _explain_cap(row),
        _explain_congestion(row),
        f"Recommendation: {recommend_action(row)}",
    ])


def best_run_selection(rows) -> tuple:
    """Select the best timing-clean run among available data.

    Returns (best_row, explanation_string, structured_result).
    Timing-clean: setup_slack >= 0. Best: smallest clock period among clean runs.

    IMPORTANT: This selects the best among runs with available parsed metrics.
    It does NOT claim a global best when some runs lack data.
    """
    timing_clean = []
    missing_runs = []

    for row in rows:
        setup_slack = _get_setup_slack(row)
        clock = safe_float(row.get("clock_period"))
        confidence = row.get("confidence", "none")

        if confidence == "none":
            missing_runs.append(row)
            continue

        if setup_slack is not None and setup_slack >= 0 and clock is not None:
            timing_clean.append((clock, setup_slack, row))

    structured = {
        "best_available_run": None,
        "evaluated_run_count": len(rows) - len(missing_runs),
        "missing_run_count": len(missing_runs),
        "timing_clean_count": len(timing_clean),
        "global_conclusion_possible": len(missing_runs) == 0,
    }

    if not timing_clean:
        has_setup = any(
            _get_setup_slack(r) is not None for r in rows
            if r.get("confidence", "none") != "none"
        )
        if has_setup:
            explanation = (
                "No timing-clean run found among configurations with available metrics. "
                "All runs with parsed setup data have negative slack. "
                "Increase CLOCK_PERIOD or optimize the critical path."
            )
        else:
            explanation = (
                "Cannot select a best run: setup timing data is not available "
                "for any run in the parsed dataset."
            )
        return None, explanation, structured

    timing_clean.sort(key=lambda x: x[0])
    best_clock, best_slack, best_row = timing_clean[0]

    structured["best_available_run"] = best_row.get("run_id")

    explanation = (
        f"The fastest setup-clean run among available completed reports "
        f"is {best_row.get('run_id')} at CLOCK_PERIOD = {best_clock} ns. "
        f"Setup slack = {best_slack} ns (timing met). "
        f"This is the smallest clock period among {len(timing_clean)} timing-clean run(s)."
    )

    if best_slack > LARGE_SETUP_MARGIN_NS:
        explanation += " Large margin suggests CLOCK_PERIOD can be further reduced."

    if missing_runs:
        missing_names = ", ".join(r.get("run_id", "?") for r in missing_runs)
        explanation += (
            f"\n\nNote: Metrics are unavailable for {missing_names}. "
            f"A global best configuration cannot yet be determined."
        )

    return best_row, explanation, structured


# ---------------------------------------------------------------------------
# QoR classification and Pareto analysis
# ---------------------------------------------------------------------------

def select_fastest_setup_clean_run(rows) -> tuple:
    """Select the fastest setup-clean run among available data.

    This is NOT the best overall QoR run — it only considers setup timing.
    Wrapper around best_run_selection for backward compatibility.
    """
    return best_run_selection(rows)


def classify_qor(row) -> dict:
    """Classify a run's overall QoR status.

    Returns dict with:
      - setup_clean: bool
      - hold_clean: bool or None
      - electrically_clean: bool
      - routing_clean: bool or None
      - qor_clean: bool (all above are True)
      - blocking_issues: list of issue names
    """
    setup_slack = _get_setup_slack(row)
    hold_slack = _get_hold_slack(row)
    drc = safe_float(row.get("route_drc_errors"))
    slew = safe_float(row.get("slew_violations"))
    cap = safe_float(row.get("cap_violations"))
    fanout = safe_float(row.get("fanout_violations"))
    confidence = row.get("confidence", "none")

    if confidence == "none":
        return {
            "setup_clean": None,
            "hold_clean": None,
            "electrically_clean": None,
            "routing_clean": None,
            "qor_clean": None,
            "blocking_issues": ["no_data"],
        }

    blocking = []

    setup_clean = setup_slack is not None and setup_slack >= 0
    if not setup_clean and setup_slack is not None:
        blocking.append("setup_timing_failure")

    hold_clean = None
    if hold_slack is not None:
        hold_clean = hold_slack >= 0
        if not hold_clean:
            blocking.append("hold_timing_failure")

    electrically_clean = True
    electrically_complete = True  # track whether all violation metrics are available
    if slew is not None and slew > 0:
        electrically_clean = False
        blocking.append("slew_violations")
    if cap is not None and cap > 0:
        electrically_clean = False
        blocking.append("cap_violations")
    if fanout is not None and fanout > 0:
        electrically_clean = False
        blocking.append("fanout_violations")
    # If any violation metric is missing, we cannot confirm electrical cleanliness
    if slew is None or cap is None or fanout is None:
        electrically_complete = False

    routing_clean = None
    if drc is not None:
        routing_clean = drc == 0
        if not routing_clean:
            blocking.append("route_drc_errors")

    # qor_clean requires all mandatory metrics to be present and passing.
    # If any dimension is unknown (None) or incomplete, qor_clean is None (unknown).
    metrics_complete = (
        setup_slack is not None and
        hold_slack is not None and
        electrically_complete and
        drc is not None
    )

    if not metrics_complete:
        # Cannot confirm fully QoR-clean without all metrics
        qor_clean = None if (setup_clean and electrically_clean) else False
    else:
        qor_clean = (
            setup_clean and
            hold_clean and
            electrically_clean and
            routing_clean
        )

    return {
        "setup_clean": setup_clean,
        "hold_clean": hold_clean,
        "electrically_clean": electrically_clean,
        "routing_clean": routing_clean,
        "qor_clean": qor_clean,
        "blocking_issues": blocking,
    }


def qor_status(rows) -> dict:
    """Compute overall QoR status across all runs.

    Returns structured data for:
      - fastest_setup_clean_run
      - qor_clean_runs
      - pareto_candidates
      - global_conclusion_possible
    """
    missing_runs = [r for r in rows if r.get("confidence", "none") == "none"]
    available_runs = [r for r in rows if r.get("confidence", "none") != "none"]

    # Fastest setup-clean
    _, _, best_structured = best_run_selection(rows)
    fastest_setup_clean = best_structured.get("best_available_run")

    # QoR-clean runs
    qor_clean_runs = []
    for row in available_runs:
        cls = classify_qor(row)
        if cls["qor_clean"]:
            qor_clean_runs.append(row.get("run_id"))

    # Pareto candidates (non-dominated across key metrics)
    pareto = _compute_pareto(available_runs)

    return {
        "fastest_setup_clean_run": fastest_setup_clean,
        "qor_clean_runs": qor_clean_runs,
        "pareto_candidates": pareto,
        "global_conclusion_possible": len(missing_runs) == 0,
        "missing_run_count": len(missing_runs),
        "reason": (
            f"{len(missing_runs)} planned runs lack reports."
            if missing_runs else "All planned runs have reports."
        ),
    }


def _compute_pareto(runs) -> list:
    """Find Pareto-optimal runs (non-dominated across available metrics).

    Objectives (lower is better unless noted):
      - clock_period: lower is better (faster)
      - power_total: lower is better
      - area: lower is better
      - slew_violations + cap_violations + fanout_violations: lower is better
      - setup_slack: must be >= 0 (filter first), then not an objective

    A run dominates another if it is at least as good on all metrics
    and strictly better on at least one.
    """
    # Only consider setup-clean runs
    candidates = []
    for row in runs:
        ss = _get_setup_slack(row)
        if ss is None or ss < 0:
            continue
        clock = safe_float(row.get("clock_period"))
        power = safe_float(row.get("power_total"))
        area = safe_float(row.get("area"))
        slew = safe_float(row.get("slew_violations")) or 0
        cap = safe_float(row.get("cap_violations")) or 0
        fanout = safe_float(row.get("fanout_violations")) or 0
        violations = slew + cap + fanout

        candidates.append({
            "run_id": row.get("run_id"),
            "clock": clock,
            "power": power,
            "area": area,
            "violations": violations,
            "objectives_complete": (clock is not None and power is not None and area is not None),
        })

    if not candidates:
        return []

    # Only runs with complete objectives participate in dominance comparisons.
    # Runs with incomplete objectives are excluded from the Pareto set.
    complete = [c for c in candidates if c["objectives_complete"]]
    if not complete:
        # If no run has complete objectives, return all setup-clean as candidates
        return [c["run_id"] for c in candidates]

    # Find non-dominated set among complete runs
    pareto = []
    for i, c in enumerate(complete):
        dominated = False
        for j, other in enumerate(complete):
            if i == j:
                continue
            if _dominates(other, c):
                dominated = True
                break
        if not dominated:
            pareto.append(c["run_id"])

    return pareto


def _dominates(a, b) -> bool:
    """Return True if run 'a' dominates run 'b' (all metrics at least as good, one strictly better).

    If any metric is missing in either run, dominance cannot be established.
    """
    metrics = ["clock", "power", "area", "violations"]
    at_least_as_good = True
    strictly_better = False

    for m in metrics:
        va = a.get(m)
        vb = b.get(m)
        if va is None or vb is None:
            # Cannot establish dominance with incomplete data
            return False
        if va > vb:
            at_least_as_good = False
            break
        if va < vb:
            strictly_better = True

    return at_least_as_good and strictly_better


def compare_runs(rows, run_ids=None) -> str:
    """Compare two or more runs side-by-side."""
    if run_ids:
        selected = [r for r in rows if r.get("run_id") in run_ids]
    else:
        selected = rows

    if len(selected) < 2:
        return "Need at least 2 runs to compare. Available runs: " + ", ".join(
            r.get("run_id", "?") for r in rows
        )

    lines = ["Run comparison:"]
    lines.append("")

    header = f"{'Run ID':<12} {'Clock':<8} {'Setup Slack':<13} {'Hold Slack':<12} {'Area':<12} {'Slew Vio':<10} {'Decision'}"
    lines.append(header)
    lines.append("-" * len(header))

    for row in selected:
        rid = row.get("run_id", "?")[:11]
        clk = _raw(row, "clock_period") or "N/A"
        s_slack = _raw(row, "setup_slack") or "N/A"
        h_slack = _raw(row, "hold_slack") or "N/A"
        area = _raw(row, "area") or "N/A"
        slew = _raw(row, "slew_violations") or "N/A"
        dec = (row.get("decision") or "")[:35]
        lines.append(f"{rid:<12} {clk:<8} {s_slack:<13} {h_slack:<12} {area:<12} {slew:<10} {dec}")

    return "\n".join(lines)
