#!/usr/bin/env python3
"""
General QoR decision-logic evaluation using synthetic datasets.

Validates that the rule engine and assistant produce correct conclusions
from varied metric configurations, without hardcoding PM32-specific names.

Usage:
  python evaluation/evaluate_generalization.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rule_engine import (
    recommend_action_structured,
    best_run_selection,
    classify_qor,
    qor_status,
    safe_float,
)


def _make_row(run_id="run_a", clock="25", setup_slack="5.0", hold_slack="0.5",
              setup_wns="0", setup_ws="5.0", slew="0", cap="0", fanout="0",
              drc="0", overflow="", util="0.70", confidence="high", **kw):
    """Build a synthetic row with defaults."""
    row = {
        "run_id": run_id,
        "clock_period": clock,
        "setup_wns": setup_wns,
        "setup_ws": setup_ws,
        "setup_tns": "0",
        "hold_wns": "0" if float(hold_slack) >= 0 else hold_slack,
        "hold_ws": hold_slack if float(hold_slack) >= 0 else "",
        "hold_tns": "0",
        "setup_slack": setup_slack,
        "hold_slack": hold_slack,
        "area": "10000",
        "utilization": util,
        "power_total": "0.001",
        "power_internal": "0.0006",
        "power_switching": "0.0003",
        "power_leakage": "1e-08",
        "slew_violations": slew,
        "cap_violations": cap,
        "fanout_violations": fanout,
        "route_drc_errors": drc,
        "route_wirelength": "30000",
        "route_vias": "5000",
        "grt_wirelength": "40000",
        "grt_vias": "10",
        "antenna_violations": "0",
        "congestion_overflow": overflow,
        "congestion_status": "overflow_data_available" if overflow else "routing_data_found",
        "worst_setup_corner": "",
        "worst_hold_corner": "",
        "timing_corners_count": "",
        "decision": "",
        "source_files": "fixtures/metrics.json",
        "field_sources": "",
        "missing_fields": "",
        "confidence": confidence,
        "parser_warnings": "",
    }
    row.update(kw)
    return row


class TestCase:
    def __init__(self, name, rows, checks):
        self.name = name
        self.rows = rows
        self.checks = checks  # list of (description, assertion_func)


def build_cases():
    """Build synthetic evaluation cases."""
    cases = []

    # Case 1: Negative setup slack
    cases.append(TestCase(
        "Negative setup slack -> setup failure recommendation",
        [_make_row(setup_slack="-2.0", setup_wns="-2.0", setup_ws="-2.0")],
        [
            ("recommend category is setup_timing_failure",
             lambda rows: recommend_action_structured(rows[0])["primary_category"] == "setup_timing_failure"),
            ("setup_slack blocking",
             lambda rows: "setup_timing_failure" in recommend_action_structured(rows[0])["blocking_issues"]),
        ]
    ))

    # Case 2: Setup passing, hold failure
    cases.append(TestCase(
        "Hold failure blocks clock optimization",
        [_make_row(setup_slack="10.0", hold_slack="-0.5")],
        [
            ("hold failure is blocking",
             lambda rows: "hold_timing_failure" in recommend_action_structured(rows[0])["blocking_issues"]),
            ("primary is not clock_optimization",
             lambda rows: recommend_action_structured(rows[0])["primary_category"] != "clock_optimization"),
        ]
    ))

    # Case 3: Setup+hold passing, nonzero DRC
    cases.append(TestCase(
        "DRC errors block clock optimization",
        [_make_row(setup_slack="8.0", drc="5")],
        [
            ("route_drc_errors is blocking",
             lambda rows: "route_drc_errors" in recommend_action_structured(rows[0])["blocking_issues"]),
        ]
    ))

    # Case 4: Slew violations with large margin
    cases.append(TestCase(
        "Slew violations block clock optimization despite large margin",
        [_make_row(setup_slack="13.0", slew="247")],
        [
            ("slew_violations is blocking",
             lambda rows: "slew_violations" in recommend_action_structured(rows[0])["blocking_issues"]),
            ("secondary includes clock reduction",
             lambda rows: "reduce_clock_period_after_cleanup" in recommend_action_structured(rows[0])["secondary_opportunities"]),
            ("primary is electrical_violations not clock_optimization",
             lambda rows: recommend_action_structured(rows[0])["primary_category"] == "electrical_violations"),
        ]
    ))

    # Case 5: Fanout violations with large margin
    cases.append(TestCase(
        "Fanout violations block clock optimization",
        [_make_row(setup_slack="8.0", fanout="3")],
        [
            ("fanout_violations is blocking",
             lambda rows: "fanout_violations" in recommend_action_structured(rows[0])["blocking_issues"]),
        ]
    ))

    # Case 6: Nonzero cap violations
    cases.append(TestCase(
        "Cap violations are blocking",
        [_make_row(setup_slack="6.0", cap="10")],
        [
            ("cap_violations is blocking",
             lambda rows: "cap_violations" in recommend_action_structured(rows[0])["blocking_issues"]),
        ]
    ))

    # Case 7: Congestion overflow present
    cases.append(TestCase(
        "Congestion overflow is blocking",
        [_make_row(setup_slack="6.0", overflow="15.3")],
        [
            ("congestion_overflow is blocking",
             lambda rows: "congestion_overflow" in recommend_action_structured(rows[0])["blocking_issues"]),
        ]
    ))

    # Case 8: Very high utilization
    cases.append(TestCase(
        "Very high utilization is blocking",
        [_make_row(setup_slack="6.0", util="0.92")],
        [
            ("very_high_utilization is blocking",
             lambda rows: "very_high_utilization" in recommend_action_structured(rows[0])["blocking_issues"]),
        ]
    ))

    # Case 9: Borderline positive margin
    cases.append(TestCase(
        "Very small margin produces small_margin category",
        [_make_row(setup_slack="0.3")],
        [
            ("primary is small_margin",
             lambda rows: recommend_action_structured(rows[0])["primary_category"] == "small_margin"),
        ]
    ))

    # Case 10: Multiple clean runs, different clocks
    cases.append(TestCase(
        "Fastest setup-clean run selected among multiple",
        [
            _make_row(run_id="fast_20", clock="20", setup_slack="3.0"),
            _make_row(run_id="slow_30", clock="30", setup_slack="10.0"),
        ],
        [
            ("fastest is fast_20",
             lambda rows: best_run_selection(rows)[2]["best_available_run"] == "fast_20"),
        ]
    ))

    # Case 11: Faster run with worse electrical quality
    cases.append(TestCase(
        "Faster run with violations is still fastest setup-clean but not qor-clean",
        [
            _make_row(run_id="fast_dirty", clock="15", setup_slack="2.0", slew="100"),
            _make_row(run_id="slow_clean", clock="30", setup_slack="8.0", slew="0"),
        ],
        [
            ("fastest setup-clean is fast_dirty",
             lambda rows: best_run_selection(rows)[2]["best_available_run"] == "fast_dirty"),
            ("fast_dirty is NOT qor-clean",
             lambda rows: not classify_qor(rows[0])["qor_clean"]),
            ("slow_clean IS qor-clean",
             lambda rows: classify_qor(rows[1])["qor_clean"]),
        ]
    ))

    # Case 12: Missing fields
    cases.append(TestCase(
        "Missing setup_slack -> missing_data recommendation",
        [_make_row(setup_slack="", setup_wns="", setup_ws="", confidence="high")],
        [
            ("primary is missing_data",
             lambda rows: recommend_action_structured(rows[0])["primary_category"] == "missing_data"),
        ]
    ))

    # Case 13: No timing-clean runs
    cases.append(TestCase(
        "No timing-clean runs -> appropriate message",
        [
            _make_row(run_id="fail_a", setup_slack="-1.0", setup_wns="-1.0"),
            _make_row(run_id="fail_b", setup_slack="-3.0", setup_wns="-3.0"),
        ],
        [
            ("best_run is None",
             lambda rows: best_run_selection(rows)[0] is None),
        ]
    ))

    # Case 14: Run not named clock_25 is fastest
    cases.append(TestCase(
        "Non-standard run ID selected as fastest",
        [
            _make_row(run_id="experiment_7", clock="12", setup_slack="1.5"),
            _make_row(run_id="experiment_9", clock="20", setup_slack="5.0"),
        ],
        [
            ("fastest is experiment_7",
             lambda rows: best_run_selection(rows)[2]["best_available_run"] == "experiment_7"),
        ]
    ))

    # Case 15: All planned runs have reports
    cases.append(TestCase(
        "All runs have data -> global conclusion possible",
        [
            _make_row(run_id="r1", clock="15", setup_slack="1.0"),
            _make_row(run_id="r2", clock="20", setup_slack="4.0"),
        ],
        [
            ("global conclusion possible",
             lambda rows: best_run_selection(rows)[2]["global_conclusion_possible"] is True),
        ]
    ))

    # Case 16: Dynamic run ID
    cases.append(TestCase(
        "Dynamic auto_tune ID works normally",
        [_make_row(run_id="auto_tune_3", clock="18", setup_slack="2.5")],
        [
            ("fastest is auto_tune_3",
             lambda rows: best_run_selection(rows)[2]["best_available_run"] == "auto_tune_3"),
        ]
    ))

    # Case 17: Clean large margin allows clock reduction
    cases.append(TestCase(
        "Clean run with large margin -> clock_optimization recommended",
        [_make_row(setup_slack="8.0", slew="0", cap="0", fanout="0", drc="0", util="0.60")],
        [
            ("primary is clock_optimization",
             lambda rows: recommend_action_structured(rows[0])["primary_category"] == "clock_optimization"),
            ("no blocking issues",
             lambda rows: len(recommend_action_structured(rows[0])["blocking_issues"]) == 0),
        ]
    ))

    # Case 18: QoR status with missing runs
    cases.append(TestCase(
        "QoR status reports missing runs",
        [
            _make_row(run_id="avail", clock="25", setup_slack="5.0"),
            _make_row(run_id="missing_1", confidence="none", setup_slack=""),
        ],
        [
            ("global not possible",
             lambda rows: qor_status(rows)["global_conclusion_possible"] is False),
            ("missing count is 1",
             lambda rows: qor_status(rows)["missing_run_count"] == 1),
        ]
    ))

    return cases


def run_generalization():
    """Run all general logic cases."""
    cases = build_cases()
    passed = 0
    failed = 0

    print("=" * 60)
    print("General QoR Decision-Logic Evaluation")
    print("=" * 60)
    print()

    for case in cases:
        case_passed = True
        for desc, check_fn in case.checks:
            try:
                result = check_fn(case.rows)
                if not result:
                    print(f"  [FAIL] {case.name}: {desc}")
                    case_passed = False
            except Exception as e:
                print(f"  [FAIL] {case.name}: {desc} (exception: {e})")
                case_passed = False

        if case_passed:
            print(f"  [PASS] {case.name}")
            passed += 1
        else:
            failed += 1

    print()
    total = passed + failed
    print(f"General logic cases: {passed}/{total}")
    print()

    if failed > 0:
        print(f"  {failed} case(s) FAILED.")
    else:
        print("  All general logic cases passed.")
        print("  These validate decision logic across varied synthetic metric configurations.")

    return failed == 0


if __name__ == "__main__":
    success = run_generalization()
    sys.exit(0 if success else 1)
