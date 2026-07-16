#!/usr/bin/env python3
"""
Conversational QoR Debugging Assistant for LibreLane/OpenROAD ASIC flows.

This CLI assistant loads parsed QoR metrics from results/qor_metrics.csv
and answers natural-language questions using rule-based diagnosis logic.
It never fabricates metrics and always cites evidence sources.

Usage:
  Interactive:  python3 scripts/assistant.py
  One-shot:     python3 scripts/assistant.py "best run"
  One-shot:     python3 scripts/assistant.py summary
"""

import csv
import sys
from pathlib import Path

from rule_engine import (
    explain_qor_row,
    recommend_action,
    recommend_action_structured,
    safe_float,
    best_run_selection,
    select_fastest_setup_clean_run,
    compare_runs,
    diagnose_run,
    classify_qor,
    qor_status,
    _raw,
    _explain_setup,
    _explain_hold,
    _explain_power,
    _explain_area,
)
from fmt import fmt_ns, fmt_power, fmt_area, fmt_util, fmt_int, fmt_slack

ROOT = Path(__file__).resolve().parents[1]
METRICS_FILE = ROOT / "results" / "qor_metrics.csv"


# ---------------------------------------------------------------------------
# Multi-turn conversation state
# ---------------------------------------------------------------------------

class ConversationContext:
    """Minimal multi-turn state for the interactive assistant.

    Tracks:
      - selected_run: the last run referenced by the user
      - compared_runs: list of run IDs from the last comparison
      - last_intent: the previous intent category
      - last_metric: the last metric discussed
    """

    def __init__(self):
        self.selected_run = None
        self.compared_runs = []
        self.last_intent = None
        self.last_metric = None

    def update(self, intent, run_ids=None, metric=None):
        """Update context after processing a question."""
        self.last_intent = intent
        if run_ids:
            if len(run_ids) == 1:
                self.selected_run = run_ids[0]
            elif len(run_ids) >= 2:
                self.compared_runs = run_ids
                self.selected_run = run_ids[0]
        if metric:
            self.last_metric = metric

    def resolve_pronouns(self, question, rows):
        """Resolve pronouns and references using conversation history.

        Handles: 'the first one', 'the second one', 'it', 'that run', 'this run'.
        Returns the resolved question string.
        """
        import re
        q = question.lower().strip()

        # Resolve "the first one" / "the second one" from last comparison
        if self.compared_runs:
            if "the first one" in q or "the first run" in q:
                q = q.replace("the first one", self.compared_runs[0])
                q = q.replace("the first run", self.compared_runs[0])
            if len(self.compared_runs) > 1:
                if "the second one" in q or "the second run" in q:
                    q = q.replace("the second one", self.compared_runs[1])
                    q = q.replace("the second run", self.compared_runs[1])

        # Resolve "it" / "that run" / "this run" to selected_run
        if self.selected_run:
            # Only resolve if no explicit run_id is present
            if not re.search(r'clock_\d+|auto_tune_\d+', q):
                for pronoun in ["about it", "for it", "explain it",
                                "that run", "this run", "the same run"]:
                    if pronoun in q:
                        q = q.replace(pronoun, f"run {self.selected_run}")
                        break

        return q


# Global context (used in interactive mode)
_conversation_ctx = ConversationContext()


def load_metrics():
    """Load parsed QoR metrics from CSV."""
    if not METRICS_FILE.exists():
        print(f"[ERROR] Metrics file not found: {METRICS_FILE}")
        print("Run: python3 scripts/parse_existing_runs.py")
        return []

    with METRICS_FILE.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _run_label(row):
    """Format a run label with clock period."""
    run_id = row.get("run_id", "(unknown)")
    clock = row.get("clock_period")
    if clock:
        return f"{run_id} (CLOCK_PERIOD={clock} ns)"
    return run_id


def _rows_with_metric(rows, key):
    """Filter rows that have a valid float for the given key."""
    return [r for r in rows if safe_float(r.get(key)) is not None]


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_summary(rows):
    """Overall QoR summary of all parsed runs."""
    if not rows:
        return "No QoR data available. Run scripts/parse_existing_runs.py first."

    clocks = [r.get("clock_period", "").strip() for r in rows if r.get("clock_period", "").strip()]
    setup_rows = _rows_with_metric(rows, "setup_slack")
    met = [r for r in setup_rows if safe_float(r.get("setup_slack")) >= 0]
    fail = [r for r in setup_rows if safe_float(r.get("setup_slack")) < 0]
    large_margin = [r for r in setup_rows if safe_float(r.get("setup_slack")) > 5]
    no_metrics = [r for r in rows if r.get("confidence") == "none"]

    best_row, best_explanation, _ = best_run_selection(rows)

    lines = [
        "=== QoR Summary ===",
        f"Parsed dataset: {len(rows)} runs.",
    ]

    if clocks:
        lines.append(f"Tested CLOCK_PERIOD values: {', '.join(clocks)} ns.")

    if setup_rows:
        lines.append(f"Setup timing MET: {len(met)} run(s). Setup timing FAIL: {len(fail)} run(s).")
        if large_margin:
            names = ", ".join(r.get("run_id", "?") for r in large_margin)
            lines.append(f"Large timing margin in: {names} (CLOCK_PERIOD can be reduced).")
    else:
        lines.append("Setup timing data not available for any run.")

    if no_metrics:
        names = ", ".join(r.get("run_id", "?") for r in no_metrics)
        lines.append(f"Runs without parsed metrics: {names}.")

    lines.append("")
    if best_row:
        lines.append(best_explanation)
    else:
        lines.append(best_explanation)

    return "\n".join(lines)


def cmd_best_run(rows):
    """Select and explain the fastest setup-clean run."""
    best_row, explanation, structured = best_run_selection(rows)

    if best_row is None:
        return explanation

    # Conversational format with evidence/why/recommendation sections
    run_id = best_row.get("run_id", "?")
    clock = best_row.get("clock_period", "?")
    setup_wns = _raw(best_row, "setup_wns")
    setup_ws = _raw(best_row, "setup_ws")
    setup_slack = _raw(best_row, "setup_slack")
    hold_ws = _raw(best_row, "hold_ws")
    drc = _raw(best_row, "route_drc_errors")
    src = best_row.get("source_files", "")

    lines = [
        f"The fastest setup-clean run among available completed reports is {run_id}.",
        "  (This is NOT necessarily the best overall QoR run — it only considers setup timing,",
        "   not electrical violations, routing quality, or utilization.)",
        "",
        "Evidence:",
        f"  - CLOCK_PERIOD = {clock} ns",
    ]

    if setup_wns is not None:
        lines.append(f"  - OpenROAD setup WNS = {fmt_ns(setup_wns)}")
    if setup_ws is not None:
        lines.append(f"  - Worst setup slack (margin) = {fmt_ns(setup_ws)}")
    if hold_ws is not None:
        lines.append(f"  - Worst hold slack (margin) = {fmt_ns(hold_ws)}")

    if drc is not None:
        lines.append(f"  - Routing DRC errors = {fmt_int(drc)}")

    # Show electrical violations prominently
    slew = _raw(best_row, "slew_violations")
    cap = _raw(best_row, "cap_violations")
    fanout = _raw(best_row, "fanout_violations")
    if slew is not None:
        lines.append(f"  - Slew violations = {fmt_int(slew)}")
    if cap is not None:
        lines.append(f"  - Cap violations = {fmt_int(cap)}")
    if fanout is not None:
        lines.append(f"  - Fanout violations = {fmt_int(fanout)}")

    area = _raw(best_row, "area")
    if area:
        lines.append(f"  - Area = {fmt_area(area)}")

    util = _raw(best_row, "utilization")
    if util:
        pct = safe_float(util)
        if pct is not None:
            pct_display = pct * 100 if pct <= 1.0 else pct
            lines.append(f"  - Utilization = {pct_display:.1f}%")

    ptotal = _raw(best_row, "power_total")
    if ptotal:
        lines.append(f"  - Total power = {fmt_power(ptotal)}")

    # Why section
    timing_clean = [r for r in rows
                    if safe_float(r.get("setup_slack")) is not None
                    and safe_float(r.get("setup_slack")) >= 0]
    lines.append("")
    lines.append("Why:")
    lines.append(f"  {run_id} is the smallest clock period among {len(timing_clean)} timing-clean run(s) with available data.")

    others = [r for r in timing_clean if r.get("run_id") != run_id]
    if others:
        other_names = ", ".join(r.get("run_id", "?") for r in others)
        lines.append(f"  Other timing-clean runs ({other_names}) have larger CLOCK_PERIOD.")

    # Note about missing runs
    if not structured.get("global_conclusion_possible", True):
        missing_names = ", ".join(
            r.get("run_id", "?") for r in rows if r.get("confidence") == "none"
        )
        lines.append("")
        lines.append("Note:")
        lines.append(f"  Metrics are unavailable for {missing_names}.")
        lines.append("  A global best configuration cannot yet be determined.")

    # Recommendation: use recommend_action_structured() for blocker-aware priority
    rec = recommend_action_structured(best_row)
    lines.append("")
    lines.append("Recommendation:")
    if rec["blocking_issues"]:
        issues_str = ", ".join(b.replace("_", " ") for b in rec["blocking_issues"])
        lines.append(f"  Blocking issues: {issues_str}.")
        lines.append(f"  {rec['text']}")
    else:
        lines.append(f"  {rec['text']}")

    if rec["secondary_opportunities"]:
        opps = ", ".join(o.replace("_", " ") for o in rec["secondary_opportunities"])
        lines.append(f"  Secondary: {opps}.")

    # Source
    if src:
        lines.append("")
        lines.append("Source:")
        for s in src.split(";"):
            lines.append(f"  {s.strip()}")

    return "\n".join(lines)


def cmd_explain_timing(rows):
    """Diagnose timing across all runs."""
    if not rows:
        return "No timing data available. Run the parser first."

    setup_rows = _rows_with_metric(rows, "setup_slack")
    hold_rows = _rows_with_metric(rows, "hold_slack")

    if not setup_rows and not hold_rows:
        return (
            "Timing metrics are not available in the parsed data. "
            "Cannot diagnose timing closure."
        )

    lines = ["=== Timing Diagnosis ==="]

    if setup_rows:
        fail = [r for r in setup_rows if safe_float(r.get("setup_slack")) < 0]
        met = [r for r in setup_rows if safe_float(r.get("setup_slack")) >= 0]
        large = [r for r in setup_rows if safe_float(r.get("setup_slack")) > 5]

        if fail:
            lines.append("")
            lines.append("SETUP TIMING FAILURES:")
            for r in fail:
                lines.append(
                    f"  - {_run_label(r)}: setup WNS = {r.get('setup_wns', 'N/A')} ns. "
                    "Critical path too slow for target clock."
                )
        if met:
            lines.append("")
            lines.append(f"SETUP TIMING MET: {len(met)} run(s).")
            for r in met:
                ws = _raw(r, "setup_ws")
                wns = _raw(r, "setup_wns")
                detail = f"WNS = {fmt_ns(wns)}" if wns else ""
                if ws:
                    detail += f", margin = {fmt_ns(ws)}" if detail else f"margin = {fmt_ns(ws)}"
                lines.append(f"  - {_run_label(r)}: {detail}")

        if large:
            lines.append("")
            names = ", ".join(_run_label(r) for r in large)
            lines.append(f"Large margin (slack > 5 ns): {names}. Consider reducing CLOCK_PERIOD.")
    else:
        lines.append("Setup timing data not available for any run.")

    if hold_rows:
        hold_fail = [r for r in hold_rows if safe_float(r.get("hold_slack")) < 0]
        if hold_fail:
            lines.append("")
            lines.append("HOLD TIMING FAILURES:")
            for r in hold_fail:
                lines.append(f"  - {_run_label(r)}: hold WNS = {r.get('hold_wns', 'N/A')} ns.")
        else:
            lines.append("")
            lines.append("Hold timing is MET for all runs with available data.")

    return "\n".join(lines)


def cmd_tuning(rows):
    """Clock period tuning recommendation."""
    if not rows:
        return "No data available for tuning recommendations."

    setup_rows = _rows_with_metric(rows, "setup_slack")
    if not setup_rows:
        return (
            "Cannot make tuning recommendations: setup slack is unavailable. "
            "Parse timing reports first."
        )

    best_row, best_msg, _ = best_run_selection(rows)

    if best_row:
        rec = recommend_action(best_row)
        slack = _raw(best_row, "setup_slack") or "N/A"
        return (
            f"=== Tuning Recommendation ===\n"
            f"Starting point: {_run_label(best_row)}.\n"
            f"Setup slack = {fmt_slack(slack)} (timing met).\n"
            f"Recommendation: {rec}\n"
            f"Evidence: {best_row.get('source_files', 'N/A')}"
        )

    # All fail - recommend based on least-negative
    least_bad = max(setup_rows, key=lambda r: safe_float(r.get("setup_slack")))
    slack = _raw(least_bad, "setup_slack") or "N/A"
    return (
        f"=== Tuning Recommendation ===\n"
        f"No timing-clean run available.\n"
        f"Least-severe violation: {_run_label(least_bad)} with setup slack = {fmt_slack(slack)}.\n"
        f"Recommendation: Increase CLOCK_PERIOD or optimize the critical path.\n"
        f"Evidence: {least_bad.get('source_files', 'N/A')}"
    )


def cmd_show_metrics(rows):
    """Display all parsed metrics in a readable format."""
    if not rows:
        return "No metrics found. Run scripts/parse_existing_runs.py first."

    lines = ["=== Parsed QoR Metrics ==="]

    for row in rows:
        lines.append("")
        lines.append(f"--- {_run_label(row)} ---")

        confidence = row.get("confidence", "none")
        if confidence == "none":
            lines.append("  [No metrics available for this run]")
            missing = row.get("missing_fields", "")
            if missing:
                lines.append(f"  Missing: {missing}")
            continue

        lines.append(f"  Confidence: {confidence}")

        # Timing
        swns = _raw(row, "setup_wns")
        hwns = _raw(row, "hold_wns")
        if swns:
            lines.append(f"  Setup WNS: {swns} ns")
        if hwns:
            lines.append(f"  Hold WNS: {hwns} ns")

        stns = _raw(row, "setup_tns")
        htns = _raw(row, "hold_tns")
        if stns:
            lines.append(f"  Setup TNS: {stns} ns")
        if htns:
            lines.append(f"  Hold TNS: {htns} ns")

        # Area
        area = _raw(row, "area")
        util = _raw(row, "utilization")
        if area:
            lines.append(f"  Area: {area} um^2")
        if util:
            lines.append(f"  Utilization: {util}")

        # Power
        ptotal = _raw(row, "power_total")
        if ptotal:
            lines.append(f"  Power (total): {ptotal} W")
            pint = _raw(row, "power_internal")
            psw = _raw(row, "power_switching")
            plk = _raw(row, "power_leakage")
            if pint:
                lines.append(f"    Internal: {pint} W")
            if psw:
                lines.append(f"    Switching: {psw} W")
            if plk:
                lines.append(f"    Leakage: {plk} W")

        # Violations
        slew = _raw(row, "slew_violations")
        cap = _raw(row, "cap_violations")
        fanout = _raw(row, "fanout_violations")
        if slew:
            lines.append(f"  Slew violations: {slew}")
        if cap:
            lines.append(f"  Cap violations: {cap}")
        if fanout:
            lines.append(f"  Fanout violations: {fanout}")

        # Congestion and decision
        cong = _raw(row, "congestion_status")
        if cong:
            lines.append(f"  Congestion: {cong}")

        dec = _raw(row, "decision")
        if dec:
            lines.append(f"  Decision: {dec}")

        src = _raw(row, "source_files")
        if src:
            lines.append(f"  Source: {src}")

        missing = row.get("missing_fields", "")
        if missing:
            lines.append(f"  Missing fields: {missing}")

    return "\n".join(lines)


def cmd_violations(rows):
    """Electrical violation analysis."""
    if not rows:
        return "No violation data available."

    any_data = False
    lines = ["=== Electrical Violations ==="]

    for row in rows:
        slew = safe_float(row.get("slew_violations"))
        cap = safe_float(row.get("cap_violations"))
        fanout = safe_float(row.get("fanout_violations"))

        if slew is None and cap is None and fanout is None:
            continue

        any_data = True
        lines.append("")
        lines.append(f"  {_run_label(row)}:")

        if slew is not None:
            if slew > 0:
                lines.append(f"    Slew violations: {int(slew)} - transitions too slow")
            else:
                lines.append("    Slew violations: 0 (clean)")

        if cap is not None:
            if cap > 0:
                lines.append(f"    Cap violations: {int(cap)} - excessive net load")
            else:
                lines.append("    Cap violations: 0 (clean)")

        if fanout is not None:
            if fanout > 0:
                lines.append(f"    Fanout violations: {int(fanout)} - too many sinks")
            else:
                lines.append("    Fanout violations: 0 (clean)")

        src = _raw(row, "source_files")
        if src:
            lines.append(f"    Source: {src}")

    if not any_data:
        return (
            "Violation metrics (slew, cap, fanout) are not available in the parsed data. "
            "Cannot claim zero violations without report evidence."
        )

    lines.append("")
    lines.append("Recommendation: For slew violations, check buffering and placement density. "
                 "For cap violations, check routing load and fanout.")
    return "\n".join(lines)


def cmd_power(rows):
    """Power analysis."""
    power_rows = _rows_with_metric(rows, "power_total")
    if not power_rows:
        return "Power data is not available in the parsed reports. Cannot analyze power."

    lines = ["=== Power Analysis ==="]
    for row in power_rows:
        lines.append("")
        lines.append(f"  {_run_label(row)}:")
        lines.append(f"    Total power: {row.get('power_total')} W")
        pint = _raw(row, "power_internal")
        psw = _raw(row, "power_switching")
        plk = _raw(row, "power_leakage")
        if pint:
            lines.append(f"    Internal: {pint} W")
        if psw:
            lines.append(f"    Switching: {psw} W")
        if plk:
            lines.append(f"    Leakage: {plk} W")
        lines.append(f"    Source: {row.get('source_files', 'N/A')}")

    return "\n".join(lines)


def cmd_area(rows):
    """Area analysis."""
    area_rows = _rows_with_metric(rows, "area")
    if not area_rows:
        return "Area data is not available in the parsed reports. Cannot analyze area."

    lines = ["=== Area Analysis ==="]
    for row in area_rows:
        lines.append("")
        lines.append(f"  {_run_label(row)}:")
        lines.append(f"    Core area: {row.get('area')} um^2")
        util = _raw(row, "utilization")
        if util:
            lines.append(f"    Utilization: {util}")
        lines.append(f"    Source: {row.get('source_files', 'N/A')}")

    return "\n".join(lines)


def cmd_congestion(rows):
    """Congestion and routing quality report."""
    if not rows:
        return "No data available. Cannot report congestion or routing status."

    # Check for routing data
    routing_rows = [
        r for r in rows
        if any(r.get(k, "").strip() for k in ["route_drc_errors", "route_wirelength", "grt_wirelength"])
    ]

    if routing_rows:
        lines = ["=== Routing and Congestion Analysis ===", ""]
        lines.append("NOTE: No explicit congestion overflow metrics (e.g., GRT overflow percentage)")
        lines.append("are available. However, the following routing quality data was extracted:")
        lines.append("")

        for row in routing_rows:
            lines.append(f"  {_run_label(row)}:")
            drc = _raw(row, "route_drc_errors")
            wl = _raw(row, "route_wirelength")
            vias = _raw(row, "route_vias")
            grt_wl = _raw(row, "grt_wirelength")
            grt_vias = _raw(row, "grt_vias")
            antenna = _raw(row, "antenna_violations")

            if grt_wl:
                lines.append(f"    Global route wirelength: {grt_wl}")
            if grt_vias:
                lines.append(f"    Global route vias: {grt_vias}")
            if wl:
                lines.append(f"    Detailed route wirelength: {wl}")
            if vias:
                lines.append(f"    Detailed route vias: {vias}")
            if drc is not None:
                if drc == "0":
                    lines.append("    Final routing DRC errors: 0 (routing clean)")
                else:
                    lines.append(f"    Final routing DRC errors: {drc} (routing violations present)")
            if antenna is not None:
                if antenna == "0":
                    lines.append("    Antenna violations: 0 (clean)")
                else:
                    lines.append(f"    Antenna violations: {antenna}")

            src = _raw(row, "source_files")
            if src:
                lines.append(f"    Source: {src}")
            lines.append("")

        lines.append("Congestion interpretation:")
        lines.append("  - Final DRC errors = 0 does NOT prove zero congestion during global routing.")
        lines.append("  - It only means detailed routing completed without remaining DRC violations.")
        lines.append("  - No explicit congestion overflow percentage is reported by this flow configuration.")
        lines.append("  - To get per-layer overflow metrics, enable GRT congestion reporting in LibreLane.")
        return "\n".join(lines)

    # No routing data at all
    return (
        "Congestion data is NOT available in the parsed reports.\n"
        "The assistant does not invent congestion results.\n"
        "To get congestion metrics, run LibreLane with congestion reporting enabled "
        "and ensure the reports are stored in results/important_reports/."
    )


def cmd_explain_all(rows):
    """Full explanation of every run."""
    if not rows:
        return "No QoR data available."

    lines = ["=== Detailed QoR Explanation (All Runs) ==="]
    for row in rows:
        lines.append("")
        lines.append(explain_qor_row(row))

    return "\n".join(lines)


def cmd_explain_run(rows, run_id):
    """Explain a specific run by ID."""
    matching = [r for r in rows if r.get("run_id", "").lower() == run_id.lower()]
    if not matching:
        available = ", ".join(r.get("run_id", "?") for r in rows)
        return f"Run '{run_id}' not found. Available runs: {available}"

    row = matching[0]
    lines = [f"=== Detailed Analysis: {_run_label(row)} ===", ""]

    # If no metrics at all, state this clearly with status-aware explanation
    confidence = row.get("confidence", "none")
    if confidence == "none":
        # Load experiment status from qor_runs.csv for context
        import csv as _csv
        run_status = "unknown"
        qor_runs_path = ROOT / "results" / "qor_runs.csv"
        if qor_runs_path.exists():
            with qor_runs_path.open(newline="", encoding="utf-8") as f:
                for r in _csv.DictReader(f):
                    if r.get("run_id", "") == run_id:
                        run_status = r.get("status", "unknown")
                        break

        if run_status == "execution_failed":
            lines.append(
                f"Metrics are not available (missing) for this run. "
                f"LibreLane execution was attempted but the flow terminated "
                f"before producing final metrics (CLOCK_PERIOD={row.get('clock_period')} ns "
                f"was too aggressive for the tool to converge). "
                f"Re-running with the same clock period is unlikely to succeed "
                f"without RTL or constraint changes."
            )
        elif run_status == "config_only":
            lines.append(
                f"Metrics are not available for this run (missing). "
                f"A configuration file exists for CLOCK_PERIOD={row.get('clock_period')} ns "
                f"but no LibreLane execution was attempted. "
                f"To obtain metrics, execute: docker run --rm -v \"$(pwd):/work\" -w /work "
                f"ghcr.io/librelane/librelane:3.0.3 librelane "
                f"designs/pm32/config_clock{row.get('clock_period')}.json"
            )
        else:
            lines.append(
                f"Metrics are not available (missing) for this run. "
                f"All metric fields are missing. "
                f"This means no report artifact was matched to {run_id}."
            )
        return "\n".join(lines)

    lines.append(explain_qor_row(row))
    lines.append("")

    # Add structured diagnoses
    diags = diagnose_run(row)
    if diags:
        lines.append("Structured diagnoses:")
        for d in diags:
            severity = d["severity"].upper()
            lines.append(f"  [{severity}] {d['category']}: {d['finding']}")
            lines.append(f"         Recommendation: {d['recommendation']}")

    return "\n".join(lines)


def cmd_compare(rows, run_ids=None):
    """Compare runs."""
    return compare_runs(rows, run_ids)


def cmd_help():
    """Show help text."""
    return (
        "=== QoR Debugging Assistant - Available Commands ===\n"
        "\n"
        "  summary              Overall QoR summary\n"
        "  best run             Select best timing-clean run\n"
        "  why timing bad       Diagnose timing failures\n"
        "  why is timing failing  Same as above\n"
        "  explain run <id>     Detailed analysis of one run\n"
        "  compare runs         Compare all runs side-by-side\n"
        "  compare <id1> <id2>  Compare specific runs\n"
        "  what should I tune   Clock period recommendation\n"
        "  show metrics         Display all parsed metrics\n"
        "  violations           Electrical violation analysis\n"
        "  power                Power breakdown\n"
        "  area                 Area and utilization\n"
        "  congestion           Congestion status\n"
        "  explain all          Full explanation of every run\n"
        "  help                 Show this help\n"
        "  exit                 Exit the assistant\n"
    )


# ---------------------------------------------------------------------------
# Intent matching for natural-language questions
# ---------------------------------------------------------------------------

# Common spelling/terminology aliases for normalization
_ALIASES = {
    "timming": "timing",
    "congeston": "congestion",
    "electical": "electrical",
    "violaitons": "violations",
    "violatons": "violations",
    "congesion": "congestion",
    "pareto": "pareto",
    "qor": "qor",
    "firstly": "first",
    "more fast": "faster",
}

# Each intent is a list of patterns. If ANY pattern matches (substring),
# the question is routed to the corresponding handler.

_INTENT_PATTERNS = {
    "exit": [
        "exit", "quit", "bye", "goodbye",
    ],
    "help": [
        "help", "commands", "what can you",
    ],
    "summary": [
        "summary", "overview", "summarize", "give me an overview",
        "how many runs", "all runs", "overall",
    ],
    "best_run": [
        "best run", "best available", "best", "which run is best", "fastest valid",
        "which clock period should", "which implementation meets",
        "optimal", "highest performance", "which should i choose",
        "fastest passing", "best timing", "which run should i present",
        "which run is timing clean", "what is the best",
        "what is the fastest", "which is the best",
        "which available run is fastest",
        "fastest setup-clean", "fastest setup clean",
        "fastest timing run",
    ],
    "qor_status": [
        "qor status", "qor clean", "pareto", "best qor",
        "best overall", "which run is best overall",
        "show pareto", "pareto candidates", "pareto runs",
        "is any run fully clean", "globally best",
    ],
    "what_to_fix": [
        "what should be fixed", "what to fix", "fix first",
        "what should i fix", "why should i not reduce",
        "why not reduce", "what is blocking",
        "what should be fixed first",
    ],
    "timing": [
        "timing", "slack", "wns", "tns", "setup", "hold",
        "why is timing", "does timing pass", "is timing met",
        "timing failing", "timing bad", "timing fail",
        "what is the wns", "what is the tns",
        "which run has the best timing", "is setup timing met",
        "does setup pass", "does hold pass", "is hold timing met",
        "critical path", "timing closure",
        "are there timing violations", "timing violations",
        "what is the difference between wns and ws",
        "what is the positive setup margin",
        "which timing corner is worst",
        "what is setup wns", "what is setup margin",
        "clean timing", "clean timming",
    ],
    "tuning": [
        "tune", "adjust", "should i reduce", "should i increase",
        "can i make the clock faster", "is the clock conservative",
        "clock period", "what should i tune", "reduce clock",
        "increase clock", "faster clock", "slower clock",
        "what clock", "next clock", "next experiment",
        "can clock_period be reduced", "should i try a smaller",
        "try smaller clock", "try a smaller clock period",
        "what should i run next", "run next",
        "can clock be faster", "smaller period", "higher frequency",
    ],
    "violations": [
        "violation", "slew", "capacitance", "fanout",
        "electrical", "drv", "design rule",
        "are there slew", "are there cap", "are there fanout",
        "what electrical violations", "what violations should i fix",
        "signals too slow", "are signals too slow",
        "electrical violations", "electical violations",
        "errors", "problems",
    ],
    "congestion": [
        "congestion", "routing", "overflow", "drc error",
        "is there congestion", "did routing fail",
        "are there routing", "do we have overflow",
        "is routing clean", "route", "wirelength",
        "antenna", "grt overflow", "routing data",
        "what routing data", "are there antenna",
        "does zero drc", "zero drc", "drc prove",
        "does zero drc mean", "does zero drc prove",
        "zero drc means",
    ],
    "power": [
        "power", "energy", "watt", "consumption",
        "how much power", "power breakdown",
        "internal power", "switching power", "leakage",
        "compare power", "show power",
    ],
    "area": [
        "area", "utilization", "die size", "core size",
        "what is the area", "what is utilization",
        "silicon area", "floorplan", "show area",
        "compare area",
    ],
    "missing": [
        "missing", "empty", "no metrics", "why are some runs",
        "which runs have no", "why is clock_", "unavailable",
        "no data", "not found", "which reports are unavailable",
        "what data is missing", "why does clock_",
        "why can't you determine the global",
        "which configurations have data",
    ],
    "compare": [
        "compare",
    ],
    "show_metrics": [
        "show metrics", "show all metrics", "display metrics",
        "print all", "list all", "raw data", "all data",
        "table",
    ],
    "explain_all": [
        "explain all", "analyze all", "full explanation",
        "explain every", "all explanations",
    ],
}


def _normalize_query(q: str) -> str:
    """Normalize query text: lowercase, remove punctuation, apply aliases."""
    import re
    q = q.lower().strip()
    # Remove punctuation except underscores and hyphens in identifiers
    q = re.sub(r"[?.!,;:'\"()\[\]{}]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    # Apply spelling aliases
    for alias, replacement in _ALIASES.items():
        q = q.replace(alias, replacement)
    return q


def _match_intent(q):
    """Match a question to an intent using pattern matching with normalization.

    Returns the intent name or None.
    """
    q = _normalize_query(q)

    # Exact command matches first (preserve backward compatibility)
    if q in ("exit", "quit", "q"):
        return "exit"
    if q in ("help", "commands"):
        return "help"
    if q == "summary":
        return "summary"
    if q in ("best run", "best available run"):
        return "best_run"
    if q == "violations":
        return "violations"
    if q == "congestion":
        return "congestion"
    if q == "power":
        return "power"
    if q == "area":
        return "area"
    if q == "explain all":
        return "explain_all"
    if q == "show metrics":
        return "show_metrics"
    if q in ("qor status", "pareto runs", "show pareto candidates"):
        return "qor_status"
    if q in ("what should be fixed first", "what to fix first"):
        return "what_to_fix"

    # Check for "explain run <id>" pattern
    import re
    if q.startswith("explain run ") or (q.startswith("explain ") and "all" not in q):
        return "explain_run"

    # Check for run-specific patterns
    run_match = re.search(r'(clock_\d+|auto_tune_\d+)', q)
    if run_match and any(kw in q for kw in ["explain", "analyze", "detail", "happened", "about"]):
        return "explain_run"

    # Check for "compare" patterns
    if "compare" in q or "which is better" in q:
        return "compare"

    # Score-based intent matching: count how many patterns match
    scores = {}
    for intent, patterns in _INTENT_PATTERNS.items():
        score = 0
        for pattern in patterns:
            if pattern in q:
                # Longer patterns get higher weight (more specific = better)
                score += len(pattern) * len(pattern)
        if score > 0:
            scores[intent] = score

    if not scores:
        return None

    # Return highest-scoring intent
    return max(scores, key=scores.get)


def cmd_qor_status(rows):
    """Overall QoR classification and Pareto analysis."""
    if not rows:
        return "No QoR data available."

    status = qor_status(rows)
    lines = ["=== QoR Status ===", ""]

    fastest = status["fastest_setup_clean_run"]
    if fastest:
        lines.append(f"Fastest setup-clean run: {fastest}")
        lines.append("  (This is NOT necessarily the best overall QoR run.)")
        lines.append("  (It only considers setup timing, not electrical/routing quality.)")
    else:
        lines.append("No setup-clean run available among parsed data.")

    lines.append("")
    qor_clean = status["qor_clean_runs"]
    if qor_clean:
        lines.append(f"Fully QoR-clean runs: {', '.join(qor_clean)}")
    else:
        lines.append("No fully QoR-clean run exists (electrical violations remain).")

    lines.append("")
    pareto = status["pareto_candidates"]
    if pareto:
        lines.append(f"Pareto candidates (non-dominated): {', '.join(pareto)}")
    else:
        lines.append("No Pareto candidates (no setup-clean runs).")

    lines.append("")
    if not status["global_conclusion_possible"]:
        lines.append(f"Note: {status['reason']}")
        lines.append("A global best configuration cannot yet be determined.")

    return "\n".join(lines)


def cmd_what_to_fix(rows):
    """Explain what should be fixed first based on priority hierarchy."""
    if not rows:
        return "No data available."

    # Find the fastest setup-clean run for recommendation
    best_row, _, _ = best_run_selection(rows)
    if best_row is None:
        # Use the first available run
        available = [r for r in rows if r.get("confidence") != "none"]
        if not available:
            return "No parsed metrics available to analyze."
        best_row = available[0]

    rec = recommend_action_structured(best_row)
    lines = ["=== What Should Be Fixed First ===", ""]
    lines.append(f"Analyzing: {best_row.get('run_id')} (CLOCK_PERIOD={best_row.get('clock_period')} ns)")
    lines.append("")

    if rec["blocking_issues"]:
        lines.append("Blocking issues (must fix before clock optimization):")
        for issue in rec["blocking_issues"]:
            lines.append(f"  - {issue.replace('_', ' ')}")
        lines.append("")
        lines.append(f"Primary recommendation: {rec['text']}")
    else:
        lines.append("No blocking issues found.")
        lines.append(f"Recommendation: {rec['text']}")

    if rec["secondary_opportunities"]:
        lines.append("")
        lines.append("Secondary opportunities (after fixing blockers):")
        for opp in rec["secondary_opportunities"]:
            lines.append(f"  - {opp.replace('_', ' ')}")

    return "\n".join(lines)


def _cmd_missing(rows):
    """Explain which runs have missing data, distinguishing execution_failed from config_only."""
    if not rows:
        return "No data loaded. Run scripts/parse_existing_runs.py first."

    no_data = [r for r in rows if r.get("confidence") == "none"]
    has_data = [r for r in rows if r.get("confidence") != "none"]

    if not no_data:
        return "All runs have parsed metrics. No data is missing."

    # Load experiment status from qor_runs.csv
    import csv as _csv
    run_status = {}
    qor_runs_path = ROOT / "results" / "qor_runs.csv"
    if qor_runs_path.exists():
        with qor_runs_path.open(newline="", encoding="utf-8") as f:
            for r in _csv.DictReader(f):
                run_status[r.get("run_id", "")] = r.get("status", "unknown")

    # Separate into execution_failed vs config_only
    exec_failed = []
    config_only = []
    unknown_status = []
    for r in no_data:
        rid = r.get("run_id", "?")
        status = run_status.get(rid, "unknown")
        if status == "execution_failed":
            exec_failed.append(rid)
        elif status == "config_only":
            config_only.append(rid)
        else:
            unknown_status.append(rid)

    lines = ["=== Missing Data Explanation ===", ""]
    lines.append(f"Runs WITH metrics ({len(has_data)}): " +
                 ", ".join(r.get("run_id", "?") for r in has_data))
    lines.append(f"Runs WITHOUT metrics ({len(no_data)}): " +
                 ", ".join(r.get("run_id", "?") for r in no_data))
    lines.append("")

    if exec_failed:
        lines.append(f"Execution FAILED ({len(exec_failed)}): {', '.join(exec_failed)}")
        lines.append("  - LibreLane was executed but the flow terminated before producing final metrics.")
        lines.append("  - The clock target was too aggressive for the tool to converge.")
        lines.append("  - Re-running with the same clock period is unlikely to succeed without RTL changes.")
        lines.append("")

    if config_only:
        lines.append(f"Config ONLY ({len(config_only)}): {', '.join(config_only)}")
        lines.append("  - Configuration files exist but no execution was attempted.")
        lines.append("  - These can be executed when LibreLane is available.")
        lines.append("")

    if unknown_status:
        lines.append(f"Status unknown ({len(unknown_status)}): {', '.join(unknown_status)}")
        lines.append("  - No status information in qor_runs.csv.")
        lines.append("")

    lines.append("How to obtain metrics for config_only runs:")
    lines.append("  1. Run LibreLane for the desired clock period.")
    lines.append("  2. Store the output metrics.json in results/important_reports/.")
    lines.append("  3. Update run_mapping.json to map the new files to run IDs.")
    lines.append("  4. Re-run: python scripts/parse_existing_runs.py")
    lines.append("")
    lines.append("The assistant never invents metrics for runs without report data.")

    return "\n".join(lines)


def answer(question, rows, ctx=None):
    """Route a natural-language question to the appropriate handler.

    If ctx (ConversationContext) is provided, resolve pronouns and update state.
    """
    import re as _re

    # Resolve pronouns if context is available
    if ctx:
        question = ctx.resolve_pronouns(question, rows)

    q = question.lower().strip()

    intent = _match_intent(q)

    if intent == "exit":
        return "EXIT"

    if intent == "help":
        return cmd_help()

    if intent == "explain_run":
        # Extract run_id from various patterns
        run_match = _re.search(r'(clock_\d+|auto_tune_\d+)', q)
        if run_match:
            rid = run_match.group(1)
            if ctx:
                ctx.update("explain_run", run_ids=[rid])
            return cmd_explain_run(rows, rid)
        parts = q.replace("explain run ", "").replace("explain ", "").strip().split()
        if parts:
            run_id = parts[0]
            if not run_id.startswith("clock_") and not run_id.startswith("auto_tune_"):
                run_id = f"clock_{run_id}"
            if ctx:
                ctx.update("explain_run", run_ids=[run_id])
            return cmd_explain_run(rows, run_id)
        return cmd_explain_all(rows)

    if intent == "compare":
        # Find all clock_XX references
        run_matches = _re.findall(r'clock_\d+|auto_tune_\d+', q)
        if run_matches:
            if ctx:
                ctx.update("compare", run_ids=run_matches)
            return cmd_compare(rows, run_matches)
        # Try bare numbers after "compare"
        parts = q.replace("compare", "").replace("runs", "").replace("and", " ").replace(",", " ").strip().split()
        if parts:
            run_ids = []
            for p in parts:
                cleaned = p.strip(",. ")
                if not cleaned:
                    continue
                if cleaned.isdigit():
                    cleaned = f"clock_{cleaned}"
                elif not cleaned.startswith("clock_"):
                    cleaned = f"clock_{cleaned}"
                run_ids.append(cleaned)
            if run_ids:
                if ctx:
                    ctx.update("compare", run_ids=run_ids)
                return cmd_compare(rows, run_ids)
        return cmd_compare(rows)

    if intent == "best_run":
        best_row, _, _ = best_run_selection(rows)
        if ctx and best_row:
            ctx.update("best_run", run_ids=[best_row.get("run_id")])
        return cmd_best_run(rows)

    if intent == "summary":
        return cmd_summary(rows)

    if intent == "timing":
        return cmd_explain_timing(rows)

    if intent == "tuning":
        return cmd_tuning(rows)

    if intent == "violations":
        return cmd_violations(rows)

    if intent == "congestion":
        return cmd_congestion(rows)

    if intent == "power":
        return cmd_power(rows)

    if intent == "area":
        return cmd_area(rows)

    if intent == "qor_status":
        return cmd_qor_status(rows)

    if intent == "what_to_fix":
        return cmd_what_to_fix(rows)

    if intent == "missing":
        return _cmd_missing(rows)

    if intent == "show_metrics":
        return cmd_show_metrics(rows)

    if intent == "explain_all":
        return cmd_explain_all(rows)

    # Fallback: no intent matched
    return (
        "I didn't understand that question. Here are some things I can help with:\n"
        "\n"
        "  Timing:     'Why is timing failing?', 'Is setup timing met?', 'What is the WNS?'\n"
        "  Best run:   'Which run is best?', 'Which clock period should I choose?'\n"
        "  Tuning:     'Should I reduce the clock period?', 'Can I make the clock faster?'\n"
        "  Routing:    'Is there congestion?', 'Did routing fail?', 'Is routing clean?'\n"
        "  Power:      'How much power does it consume?', 'What is the power breakdown?'\n"
        "  Area:       'What is the area?', 'What is utilization?'\n"
        "  Violations: 'Are there slew violations?', 'What electrical violations exist?'\n"
        "  Missing:    'Why are some runs missing?', 'Which runs have no metrics?'\n"
        "  General:    'summary', 'show metrics', 'compare runs', 'explain all'\n"
        "\n"
        "Type 'help' for the full command list."
    )


# ---------------------------------------------------------------------------
# Structured data interface (for tests and evaluation)
# ---------------------------------------------------------------------------

def answer_structured(question, rows):
    """Route a question and return structured data + formatted text.

    Returns: {"intent": str, "data": dict, "text": str}

    The CLI prints only 'text'. Tests and evaluation inspect 'data'.
    """
    q = question.lower().strip()
    intent = _match_intent(q)

    if intent == "best_run":
        best_row, explanation, structured = best_run_selection(rows)
        text = cmd_best_run(rows)
        data = structured
        if best_row:
            data["setup_wns"] = safe_float(best_row.get("setup_wns"))
            data["setup_ws"] = safe_float(best_row.get("setup_ws"))
            data["setup_slack"] = safe_float(best_row.get("setup_slack"))
        return {"intent": "best_run", "data": data, "text": text}

    if intent == "timing":
        text = cmd_explain_timing(rows)
        setup_rows = _rows_with_metric(rows, "setup_slack")
        data = {
            "runs": []
        }
        for r in setup_rows:
            data["runs"].append({
                "run_id": r.get("run_id"),
                "setup_wns": safe_float(r.get("setup_wns")),
                "setup_ws": safe_float(r.get("setup_ws")),
                "setup_slack": safe_float(r.get("setup_slack")),
                "setup_status": "met" if safe_float(r.get("setup_slack", 0)) >= 0 else "fail",
            })
        return {"intent": "timing", "data": data, "text": text}

    if intent == "congestion":
        text = cmd_congestion(rows)
        routing_rows = [r for r in rows if r.get("route_drc_errors", "").strip()]
        drc_val = None
        if routing_rows:
            drc_val = safe_float(routing_rows[0].get("route_drc_errors"))
        overflow = None
        for r in rows:
            ov = r.get("congestion_overflow", "").strip()
            if ov:
                overflow = safe_float(ov)
                break
        data = {
            "final_drc_errors": int(drc_val) if drc_val is not None else None,
            "congestion_overflow": overflow,
            "congestion_quantifiable": overflow is not None,
        }
        return {"intent": "congestion", "data": data, "text": text}

    if intent == "missing":
        text = _cmd_missing(rows)
        no_data = [r.get("run_id") for r in rows if r.get("confidence") == "none"]
        has_data = [r.get("run_id") for r in rows if r.get("confidence") != "none"]
        data = {
            "missing_runs": no_data,
            "available_runs": has_data,
        }
        return {"intent": "missing_data", "data": data, "text": text}

    if intent == "tuning":
        text = cmd_tuning(rows)
        best_row, _, structured = best_run_selection(rows)
        data = {
            "recommendation_basis": "setup_slack",
            "best_available_run": structured.get("best_available_run"),
            "setup_slack": safe_float(best_row.get("setup_slack")) if best_row else None,
        }
        return {"intent": "tuning", "data": data, "text": text}

    if intent == "compare":
        import re as _re
        run_ids = _re.findall(r'clock_\d+|auto_tune_\d+', question.lower())
        if not run_ids:
            # Try bare numbers after "compare"
            parts = question.lower().replace("compare", "").replace("runs", "").replace("and", " ").replace(",", " ").strip().split()
            for p in parts:
                p = p.strip()
                if p.isdigit():
                    run_ids.append(f"clock_{p}")
        text = cmd_compare(rows, run_ids if run_ids else None)
        data = {"run_count": len(run_ids) if run_ids else len(rows), "requested_run_ids": run_ids}
        return {"intent": "compare", "data": data, "text": text}

    # For all other intents, return text only
    text = answer(question, rows)
    return {"intent": intent or "unknown", "data": {}, "text": text}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    # Determine LLM mode using single helper from llm_client
    use_llm = False
    try:
        from llm_client import should_use_llm, is_llm_available
        use_llm = should_use_llm()
        if use_llm:
            print("[INFO] LLM explanation mode enabled.")
            print()
    except ImportError:
        if "--llm" in sys.argv:
            print("[INFO] LLM client not available. Continuing in rule-based mode.")
            use_llm = False

    args = [a for a in sys.argv[1:] if a != "--llm"]

    rows = load_metrics()

    # One-shot mode
    if args:
        question = " ".join(args)
        response = answer(question, rows)
        if response != "EXIT":
            print(response)
            if use_llm:
                _try_llm_enhance(question, rows, response)
        return

    # Interactive mode
    print("QoR Debugging Assistant")
    print("Ask a QoR question, or type 'help' / 'exit'.")
    if use_llm:
        print("[LLM mode active]")
    print()

    while True:
        try:
            question = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting assistant.")
            break

        if not question.strip():
            continue

        response = answer(question, rows, ctx=_conversation_ctx)
        if response == "EXIT":
            print("Exiting assistant.")
            break

        print()
        print(response)
        if use_llm:
            _try_llm_enhance(question, rows, response)
        print()


def _try_llm_enhance(question, rows, rule_response):
    """Attempt LLM enhancement of a rule-based response."""
    try:
        from llm_client import llm_explain, is_llm_available
        from rule_engine import diagnose_run
        if not is_llm_available():
            return
        # Collect diagnoses for context
        all_diags = []
        for row in rows:
            all_diags.extend(diagnose_run(row))
        enhanced = llm_explain(question, rows, all_diags)
        if enhanced:
            print()
            print(enhanced)
    except (ImportError, Exception):
        pass


if __name__ == "__main__":
    main()
